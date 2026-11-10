#!/usr/bin/env python3
"""Fixed-scenario runtime baseline runner (issue #323, A0).

The point is to make "卡顿 / 无响应 / 占用" repeatable *numbers*, not to be
another probe.  A scenario run produces one JSON file that carries:

* the GUI-thread stall window measured by ``folith.qt.jank_monitor`` (p50/p99,
  longest, per-minute counts, Top-N stacks by accumulated stall time),
* the task's own performance record — ``.performance/runs/run_*/summary.json``
  with its ``resources`` block, ``gui_jank`` block and ``gui_jank.json``,
* the UI-health incident (out-of-process watchdog) status.

Scenarios (only S1 is implemented in this round; the rest are declared so the
report format is stable):

* S1 — a large EPUB is being translated while the driver scrolls the paragraph
  table, edits a translation and switches stage pages.
* S2 — long scanned PDF, paging and thumbnails while OCR runs.   (skeleton)
* S3 — open a finished large task and switch tasks.               (skeleton)
* S4 — eight-hour run against a local mock gateway.               (skeleton)

Usage::

    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/runtime_baseline_runner.py \
        --out /tmp/runtime-baseline --paras 200 --seconds 120

    # real machine, frozen app (the run the baseline report needs):
    venv/bin/python scripts/runtime_baseline_runner.py --out /tmp/baseline-mac \
        --paras 10000 --seconds 1800 --mode frozen \
        --frozen-app dist/qt-desktop/Folith.app/Contents/MacOS/Folith

Offscreen numbers are labelled ``mode: source_offscreen`` and can never stand in
for the macOS/Windows frozen-app baseline: an offscreen QPA has no compositor,
no GPU path and no native input, and this machine is not the tester's machine.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NOT_IMPLEMENTED = {
    "S2": "long scanned PDF: paging/thumbnails while OCR runs",
    "S3": "open a finished large task and switch tasks",
    "S4": "eight-hour run against a local mock gateway",
}

LIMITS = (
    "offscreen QPA: no compositor, no GPU paint path, no native input events",
    "this harness drives the window from Python; a real user's input timing differs",
    "one machine per run: a baseline needs the same scenarios on macOS and Windows",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_epub(path: Path, paragraphs: int) -> Path:
    """Deterministic EPUB with *paragraphs* segments (no random content)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"<p>第 {index + 1} 段：基线场景用于在同一份文档上重复测量主线程响应。"
        f"The runtime baseline keeps the workload identical across runs "
        f"(segment {index + 1}).</p>"
        for index in range(max(1, paragraphs)))
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles><rootfile full-path=\"book.opf\"/></rootfiles></container>")
        book.writestr(
            "book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest><spine><itemref idref=\"chapter\"/></spine></package>")
        book.writestr(
            "chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            + body + "</body></html>")
    return path


class MockGateway:
    """Local mock model endpoint, so a run needs no API key and no network."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.out_dir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        self._handle = (self.out_dir / "stub-server.log").open("ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--port", "0", "--log", str(self.out_dir / "stub-llm.jsonl"),
             "--ready-file", str(ready)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("mock gateway exited before becoming ready")
            time.sleep(0.1)
        raise RuntimeError("mock gateway did not become ready in 30 s")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - hung child
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def read_run_records(data_dir: Path, job_id: str) -> list[dict]:
    """The task's own performance records, newest last; nothing is re-derived."""
    rows: list[dict] = []
    base = data_dir / job_id / ".performance" / "runs"
    for summary_path in sorted(base.glob("run_*/summary.json")):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        jank_path = summary_path.parent / "gui_jank.json"
        rows.append({
            "run_id": payload.get("run_id") or summary_path.parent.name,
            "directory": str(summary_path.parent),
            "wall_time_s": payload.get("wall_time_s"),
            "resources": payload.get("resources"),
            "gui_jank": payload.get("gui_jank"),
            "gui_jank_file": jank_path.is_file(),
            "resources_jsonl_lines": _count_lines(summary_path.parent / "resources.jsonl"),
        })
    return rows


def _count_lines(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def scenario_environment(base_url: str, data_dir: Path, log_dir: Path) -> dict:
    """Environment for the window under test (keeps the caller's PATH/HOME)."""
    env = dict(os.environ)
    env["FOLITH_QT_SELFTEST_BASE_URL"] = base_url
    env["FOLITH_QT_NONINTERACTIVE"] = "1"
    env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    env["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    env["FOLITH_DESKTOP_LOG_DIR"] = str(log_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _pump(app, seconds: float, predicate=None, interval: float = 0.02) -> bool:
    """Drive the real Qt event loop for *seconds* (same shape as selftest.pump)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate is not None and predicate():
            return True
        time.sleep(min(interval, 0.01))
    app.processEvents()
    return bool(predicate()) if predicate is not None else True


def run_source(args: argparse.Namespace, fixture: Path, data_dir: Path,
               log_dir: Path, base_url: str) -> dict:
    """S1 on the shared Qt window, in-process (offscreen-capable)."""
    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=log_dir)
    from folith.qt import adapter, selftest
    from folith.qt import window as window_module
    from folith.qt.ui_health import UiHealthMonitor

    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": []}
    app = window_module.build_application([sys.argv[0]])
    config = selftest.engine_from_env(adapter, adapter.EngineConfig.load())
    window = window_module.MainWindow(config, paths, None)
    window.show()

    monitor = UiHealthMonitor(app, window, paths, identity=adapter.app_identity())
    try:
        monitor.start()
        result["ui_health"] = {"available": True, "timeout_seconds": monitor.timeout,
                               "directory": str(monitor.directory)}
    except OSError as exc:
        result["ui_health"] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    jank = monitor.jank
    if jank is None:
        result["failures"].append("stall sampler was not created")
    checkpoint = jank.checkpoint() if jank is not None else None

    started = time.monotonic()
    try:
        job_id = ""
        window.import_paths([str(fixture)], autostart=False)
        imported = _pump(
            app, min(120.0, args.settle_timeout),
            lambda: not window.runner.busy and len(adapter.list_jobs()) >= 1)
        result["steps"].append({"step": "imported", "ok": imported,
                                "jobs": len(adapter.list_jobs())})
        if not imported:
            raise RuntimeError("import did not produce a task")
        job_id = adapter.list_jobs()[0].job_id
        result["job_id"] = job_id
        selftest.open_job_and_wait(window, job_id)

        if jank is not None:
            jank.set_phase("translate")
        window.start_or_resume()
        result["steps"].append({"step": "translate_started", "ok": True})

        _interact(window, app, jank, args.seconds, result)
        settled = _wait_settled(app, adapter, job_id, args.settle_timeout)
        state = adapter.load_state(job_id) or {}
        result["translation"] = {
            "settled": settled,
            "runtime_status": (adapter.core.load_runtime_state(job_id) or {}).get("status"),
            "p2_done": bool(state.get("p2_done")),
            "segments": len(state.get("pairs") or []),
            "edited_segments": result.pop("_edited", 0),
            "page_switches": result.pop("_pages", 0),
            "scroll_events": result.pop("_scrolls", 0),
        }
        result["run_records"] = read_run_records(paths.data_dir, job_id)
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        if jank is not None:
            result["gui_jank"] = jank.summary_since(checkpoint) if checkpoint else {}
            result["gui_jank_process"] = jank.summary()
        monitor.stop()
        _teardown(app, window)
    return result


def _interact(window, app, jank, seconds: float, result: dict) -> None:
    """S1 interaction: scroll the paragraph table, edit, switch stage pages."""
    workbench = window.workbench
    table = workbench.segment_list
    bar = table.verticalScrollBar()
    page_ids = ("translate", "review", "deliver")
    edited = pages = scrolls = 0
    tick = 0
    deadline = time.monotonic() + max(1.0, seconds)
    try:
        while time.monotonic() < deadline:
            app.processEvents()
            tick += 1
            if tick % 10 == 0 and bar.maximum() > 0:
                if jank is not None:
                    jank.set_phase("scroll")
                step = max(bar.maximum() // 12, 1)
                bar.setValue(0 if bar.value() + step > bar.maximum() else bar.value() + step)
                scrolls += 1
            if tick % 120 == 0 and workbench.visible_segment_count():
                if jank is not None:
                    jank.set_phase("edit")
                index = (tick // 120) % max(1, workbench.visible_segment_count())
                try:
                    if workbench.select_segment(index):
                        workbench.set_editor_text_for_test(f"基线场景人工修改译文 {index}")
                        workbench.flush_draft()
                        window.submit_edit(index)
                        edited += 1
                except Exception as exc:  # noqa: BLE001 - recorded, run continues
                    result["failures"].append(f"edit({index}): {type(exc).__name__}: {exc}")
            if tick % 200 == 0:
                if jank is not None:
                    jank.set_phase("page")
                workbench.show_page(page_ids[(tick // 200) % len(page_ids)])
                pages += 1
            time.sleep(0.01)
    finally:
        if jank is not None:
            jank.set_phase("settle")
        result["_edited"] = edited
        result["_pages"] = pages
        result["_scrolls"] = scrolls


def _wait_settled(app, adapter, job_id: str, timeout: float) -> bool:
    def settled() -> bool:
        runtime = adapter.core.load_runtime_state(job_id) or {}
        status = runtime.get("status")
        if status in ("failed", "stalled", "interrupted", "cancelled"):
            return True
        if status in adapter.core.RUNTIME_ACTIVE_STATUSES:
            return False
        return bool((adapter.load_state(job_id) or {}).get("p2_done"))

    return _pump(app, timeout, settled, interval=0.2)


def _teardown(app, window) -> None:
    try:
        window.timer.stop()
    except Exception:  # noqa: BLE001 - best effort shutdown
        pass
    app.processEvents()
    try:
        window.close()
    except Exception:  # noqa: BLE001 - a failing close must not hide the report
        pass
    app.processEvents()


def run_frozen(args: argparse.Namespace, fixture: Path, data_dir: Path,
               log_dir: Path, base_url: str) -> dict:
    """The real baseline path: the frozen app runs its own scripted flow."""
    result: dict = {"mode": "frozen_selftest", "steps": [], "failures": []}
    report_path = args.out / "selftest.json"
    env = scenario_environment(base_url, data_dir, log_dir)
    env["QT_QPA_PLATFORM"] = "offscreen"
    command = [str(Path(args.frozen_app).expanduser()),
               "--data-dir", str(data_dir), "--log-dir", str(log_dir),
               "--selftest", "--selftest-source", str(fixture),
               "--result", str(report_path)]
    started = time.monotonic()
    run = subprocess.run(command, env=env, cwd=str(args.out), capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         timeout=args.timeout)
    result["wall_time_s"] = round(time.monotonic() - started, 2)
    result["exit_code"] = run.returncode
    report: dict = {}
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            result["failures"].append("selftest report was not valid JSON")
    result["steps"].append({"step": "selftest", "ok": bool(report.get("ok"))})
    if run.returncode != 0:
        result["failures"].append("frozen app exited with a non-zero status")
        result["stdout_tail"] = run.stdout[-800:]
        result["stderr_tail"] = run.stderr[-800:]
    job_ids = [str(item) for item in (report.get("job_ids") or []) if item]
    result["job_ids"] = job_ids
    result["run_records"] = []
    for job_id in job_ids:
        result["run_records"].extend(read_run_records(data_dir, job_id))
    result["translation"] = {
        "settled": bool((report.get("run") or {}).get("p2_done")),
        "segments": (report.get("run") or {}).get("segments"),
        "edited_segments": None,
    }
    result["gui_jank"] = {}
    result["failures"].append(
        "frozen --selftest entrypoint does not start the UI health monitor, so no "
        "stall window is available from this path yet")
    return result


def write_report(args: argparse.Namespace, payload: dict) -> Path:
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "runtime-baseline.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Folith runtime baseline runner (S1)")
    parser.add_argument("--out", required=True, help="output directory for this run")
    parser.add_argument("--scenario", default="S1", choices=("S1", "S2", "S3", "S4"))
    parser.add_argument("--paras", type=int, default=200, help="paragraph count (S1)")
    parser.add_argument("--seconds", type=float, default=90.0,
                        help="interaction window while translation runs")
    parser.add_argument("--settle-timeout", type=float, default=300.0,
                        help="how long to wait for the task to settle after interacting")
    parser.add_argument("--timeout", type=float, default=1800.0,
                        help="subprocess budget for the frozen path")
    parser.add_argument("--mode", default="source", choices=("source", "frozen"))
    parser.add_argument("--frozen-app", default=None,
                        help="frozen Folith executable (implies --mode frozen)")
    parser.add_argument("--offscreen", action="store_true",
                        help="force QT_QPA_PLATFORM=offscreen for the window")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--base-url", default=None,
                        help="reuse an already running mock gateway")
    args = parser.parse_args(argv)

    args.out = Path(args.out).expanduser().resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.frozen_app:
        args.mode = "frozen"
    started_at = now_iso()
    if args.scenario != "S1":
        payload = {
            "scenario": args.scenario,
            "implemented": False,
            "reason": f"{args.scenario} is declared but not implemented in this round: "
                      f"{NOT_IMPLEMENTED[args.scenario]}",
            "limits": list(LIMITS),
            "started_at": started_at,
            "finished_at": now_iso(),
        }
        path = write_report(args, payload)
        print(json.dumps({"ok": False, "report": str(path), **payload},
                         ensure_ascii=False, indent=2))
        return 2

    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else args.out / "data"
    log_dir = Path(args.log_dir).expanduser().resolve() if args.log_dir else args.out / "logs"
    for directory in (data_dir, log_dir):
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True, exist_ok=True)

    fixture = build_epub(args.out / "fixtures" / f"s1-{args.paras}p.epub", args.paras)
    gateway = None
    base_url = (args.base_url or "").strip()
    try:
        if not base_url:
            gateway = MockGateway(args.out / "gateway")
            gateway.out_dir.mkdir(parents=True, exist_ok=True)
            base_url = gateway.start()
        env_before = dict(os.environ)
        os.environ.update({key: value for key, value in
                           scenario_environment(base_url, data_dir, log_dir).items()})
        try:
            if args.mode == "frozen":
                result = run_frozen(args, fixture, data_dir, log_dir, base_url)
            else:
                result = run_source(args, fixture, data_dir, log_dir, base_url)
        finally:
            os.environ.clear()
            os.environ.update(env_before)
    finally:
        if gateway is not None:
            gateway.stop()

    job_id = str(result.get("job_id") or "")
    if not job_id:
        job_ids = [str(item) for item in (result.get("job_ids") or []) if item]
        job_id = job_ids[0] if job_ids else ""
    payload = {
        "scenario": args.scenario,
        "implemented": True,
        "report_version": 1,
        "started_at": started_at,
        "finished_at": now_iso(),
        "fixture": {"kind": "epub", "paragraphs": args.paras, "path": str(fixture)},
        "environment": {
            "mode": result.get("mode"),
            "offscreen": os.environ.get("QT_QPA_PLATFORM") == "offscreen",
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
            "frozen_app": args.frozen_app,
            "base_url_is_mock": gateway is not None,
            "data_dir": str(data_dir),
        },
        "translation": result.get("translation") or {},
        "steps": result.get("steps") or [],
        "failures": result.get("failures") or [],
        "metrics": {
            # Run-scoped stall window straight from the sampler; the run record
            # below is the product's own copy of the same measurement.
            "gui_jank": result.get("gui_jank") or {},
            "gui_jank_process": result.get("gui_jank_process") or {},
            "ui_health": result.get("ui_health") or {},
            "run_records": result.get("run_records") or [],
        },
        "limits": list(LIMITS),
        "real_machine_baseline": False,
        "note": "offscreen/mock numbers are labelled and cannot replace the "
                "macOS/Windows frozen-app baseline the issue asks for",
    }
    payload["ok"] = bool(
        (result.get("translation") or {}).get("settled")
        and not result.get("failures"))
    path = write_report(args, payload)
    print(json.dumps({"ok": payload["ok"], "report": str(path),
                      "scenario": payload["scenario"], "mode": payload["environment"]["mode"],
                      "slow_count": (payload["metrics"]["gui_jank"] or {}).get("slow_count"),
                      "severe_count": (payload["metrics"]["gui_jank"] or {}).get("severe_count")},
                     ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
