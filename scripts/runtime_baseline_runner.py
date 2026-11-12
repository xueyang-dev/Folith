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


def _normalize_window(window: dict) -> dict:
    """Make a sampler window answer "measured?" the same way a run record does."""
    block = dict(window)
    if block.get("enabled") is False:
        block["measured"] = False
        block["reason"] = "stall sampler disabled (FOLITH_JANK_MONITOR=0)"
    else:
        block.setdefault("measured", True)
    return block


def _stall_window_from_records(records: list[dict]) -> dict:
    """The last stall window a run record actually measured, else an explicit miss.

    "No window" and "zero stalls" must not look the same to a reader: a record
    without a measured window says so instead of collapsing to ``{}``.
    """
    for row in reversed(records):
        block = row.get("gui_jank")
        if isinstance(block, dict) and block.get("measured", True):
            return block
    return {
        "measured": False,
        "reason": "no run record carried a GUI stall window (sources list is empty "
                  "or the sampler was disabled)",
    }


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


_TICK_MS = 25
_PREDICATE_INTERVAL_S = 0.2


def _job_settled(adapter, job_id: str) -> bool:
    """The task reached a terminal state (failed/cancelled counts as settled)."""
    runtime = adapter.core.load_runtime_state(job_id) or {}
    status = runtime.get("status")
    if status in ("failed", "stalled", "interrupted", "cancelled"):
        return True
    if status in adapter.core.RUNTIME_ACTIVE_STATUSES:
        return False
    return bool((adapter.load_state(job_id) or {}).get("p2_done"))


class _ScenarioDriver:
    """Drive S1 from Qt event-loop callbacks instead of pumping the GUI (#327).

    The previous version called ``QApplication.processEvents()`` in a loop from
    script code.  That makes the measuring tool itself the biggest stall bucket:
    the GUI thread sits inside the harness frame, so every native/layout stall
    is attributed to the driver and the measured p99 contains the harness.
    Here every step runs from a ``QTimer`` callback (the caller wires ``begin``
    to a zero-delay single-shot and ``tick`` to a repeating timer), and the
    driver never pumps events.  Only predicate polling (job listing, runtime
    state reads) is marked as instrumentation, because that is harness work the
    product does not do, so it cannot be counted against the product.

    The state machine itself is Qt-free and deterministic, so a test can call
    ``begin()``/``tick()`` directly with stub objects.
    """

    def __init__(self, *, app, window, adapter, jank, fixture, seconds,
                 settle_timeout, result, clock=time.monotonic):
        self.app = app
        self.window = window
        self.adapter = adapter
        self.jank = jank
        self.fixture = str(fixture)
        self.seconds = max(1.0, float(seconds))
        self.settle_timeout = max(1.0, float(settle_timeout))
        self.result = result
        self.clock = clock
        self.state = "created"
        self.finished = False
        self.settled = False
        self.job_id = ""
        self.edited = self.pages = self.scrolls = 0
        self._started = 0.0
        self._deadline = 0.0
        self._open_started = 0.0
        self._interact_until = 0.0
        self._settle_started = 0.0
        self._last_predicate = -1.0
        self._last_scroll = 0.0
        self._last_edit = 0.0
        self._last_page = 0.0
        self._page_ids = ("translate", "review", "deliver")

    # -- event-loop callbacks ---------------------------------------------
    def begin(self) -> None:
        """First callback: dispatch the import, then let the event loop run."""
        self.state = "import"
        self._started = self.clock()
        self._deadline = self._started + self.settle_timeout
        self.result["steps"].append({"step": "import_dispatched", "ok": True})
        self.window.import_paths([self.fixture], autostart=False)

    def tick(self) -> bool:
        """One event-loop tick; returns True once the scenario is over."""
        if self.finished:
            return True
        try:
            getattr(self, "_tick_" + self.state)(self.clock())
        except Exception as exc:  # noqa: BLE001 - recorded in the report
            self.result["failures"].append(f"{self.state}: {type(exc).__name__}: {exc}")
            self._finish(settled=False)
        return self.finished

    # -- states ------------------------------------------------------------
    def _tick_import(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        imported = self._instrumented(
            lambda: not self.window.runner.busy and len(self.adapter.list_jobs()) >= 1)
        if not imported:
            if now > self._deadline:
                self._fail("import did not produce a task")
            return
        jobs = self._instrumented(lambda: len(self.adapter.list_jobs()))
        self.result["steps"].append({"step": "imported", "ok": True, "jobs": jobs})
        self.job_id = self._instrumented(lambda: self.adapter.list_jobs()[0].job_id)
        self.result["job_id"] = self.job_id
        self.window.open_job(self.job_id)
        self._open_started = now
        self.state = "open"

    def _tick_open(self, now: float) -> None:
        if getattr(self.window, "_opening_job_id", ""):
            if now - self._open_started > min(120.0, self.settle_timeout):
                self._fail("task did not open")
            return
        if getattr(self.window, "_current_job_id", "") != self.job_id:
            self._fail("task did not open")
            return
        self.result["steps"].append({"step": "opened", "ok": True})
        self._phase("translate")
        self.window.start_or_resume()
        self.result["steps"].append({"step": "translate_started", "ok": True})
        self._interact_until = now + self.seconds
        self._last_scroll = self._last_edit = self._last_page = now
        self.state = "interact"

    def _tick_interact(self, now: float) -> None:
        if now >= self._interact_until:
            self._phase("settle")
            self._settle_started = now
            self.state = "settle"
            return
        workbench = self.window.workbench
        table = workbench.segment_list
        bar = table.verticalScrollBar()
        if now - self._last_scroll >= 0.1 and bar.maximum() > 0:
            self._last_scroll = now
            self._phase("scroll")
            step = max(bar.maximum() // 12, 1)
            bar.setValue(0 if bar.value() + step > bar.maximum() else bar.value() + step)
            self.scrolls += 1
        if now - self._last_edit >= 2.0 and workbench.visible_segment_count():
            self._last_edit = now
            self._phase("edit")
            index = self.edited % max(1, workbench.visible_segment_count())
            try:
                if workbench.select_segment(index):
                    workbench.set_editor_text_for_test(f"基线场景人工修改译文 {index}")
                    workbench.flush_draft()
                    self.window.submit_edit(index)
                    self.edited += 1
            except Exception as exc:  # noqa: BLE001 - recorded, run continues
                self.result["failures"].append(f"edit({index}): {type(exc).__name__}: {exc}")
        if now - self._last_page >= 4.0:
            self._last_page = now
            self._phase("page")
            workbench.show_page(self._page_ids[self.pages % len(self._page_ids)])
            self.pages += 1

    def _tick_settle(self, now: float) -> None:
        if not self._predicate_due(now):
            return
        if self._instrumented(lambda: _job_settled(self.adapter, self.job_id)):
            self._finish(settled=True)
            return
        if now - self._settle_started > self.settle_timeout:
            self._finish(settled=False)

    # -- helpers -----------------------------------------------------------
    def _predicate_due(self, now: float) -> bool:
        if self._last_predicate >= 0.0 and now - self._last_predicate < _PREDICATE_INTERVAL_S:
            return False
        self._last_predicate = now
        return True

    def _instrumented(self, call):
        """Run harness-only work (predicate polling) under the sampler's mark."""
        if self.jank is None:
            return call()
        with self.jank.instrumentation():
            return call()

    def _phase(self, name: str) -> None:
        if self.jank is not None:
            self.jank.set_phase(name)

    def _fail(self, message: str) -> None:
        self.result["failures"].append(message)
        self._finish(settled=False)

    def _finish(self, *, settled: bool) -> None:
        self.settled = bool(settled) and "settle" in (self.state, "done")
        self.state = "done"
        self.finished = True
        self.app.quit()


def load_engine_config(path: Path):
    """Build the real engine configuration from a provider config file (#327).

    The file is read in memory only: the API key is never copied into the
    report, the isolated data directory or any log.  The shape matches what
    ``EngineConfig.load()`` reads from the app's own data dir, so the client
    talks to the real endpoint with the user's real settings.
    """
    from folith.qt import adapter
    from folith.model_connections import effective_config

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not (raw.get("provider") or raw.get("connections")):
        raise RuntimeError(f"not a provider config: {path}")
    config = adapter.EngineConfig(
        provider=str(raw.get("provider") or ""),
        model=str(raw.get("model") or ""),
        api_key=str(raw.get("api_key") or ""),
        base_url=str(raw.get("base_url") or ""),
        reasoning_effort=str(raw.get("reasoning_effort") or ""),
    )
    reviewer = raw.get("reviewer") or {}
    if isinstance(reviewer, dict) and reviewer.get("provider"):
        config.reviewer_mode = "separate"
        config.reviewer_provider = str(reviewer.get("provider") or "")
        config.reviewer_model = str(reviewer.get("model") or "")
        config.reviewer_api_key = str(reviewer.get("api_key") or "")
        config.reviewer_base_url = str(reviewer.get("base_url") or "")
    config.connections = list(raw.get("connections") or [])
    config.connection_roles = dict(raw.get("connection_roles") or {})
    config.connection_models = dict(raw.get("connection_models") or {})
    config.ensure_connections()
    return effective_config(config, config.connections, config.connection_roles)


def _run_scenario_event_loop(app) -> None:
    """Run the application's own event loop for the whole scenario (#327).

    A stall captured while Qt dispatches natively (paint, layout, plugin work,
    with no Python frame below) shows this frame as its innermost Python frame.
    That is the *application's* event loop, not the harness pumping events: the
    shipping client calls the same ``app.exec()`` from ``folith/qt/app.py``.
    The frame name is kept explicit so a baseline report does not mistake it for
    the old ``processEvents`` pump.
    """
    app.exec()


def run_source(args: argparse.Namespace, fixture: Path, data_dir: Path,
               log_dir: Path, base_url: str) -> dict:
    """S1 on the shared Qt window, driven from the event loop (#327).

    The scenario runs inside ``app.exec()``; ``_ScenarioDriver`` steps are Qt
    timer callbacks.  This function never pumps events itself, so a stall is
    never attributed to the harness's own ``processEvents()`` frame.
    """
    from folith.qt import bootstrap

    paths = bootstrap.prepare_environment(data_dir=data_dir, log_dir=log_dir)
    from folith.qt import adapter, selftest
    from folith.qt import window as window_module
    from folith.qt.ui_health import UiHealthMonitor
    from PySide6.QtCore import QTimer

    result: dict = {"mode": "source_offscreen" if args.offscreen else "source",
                    "steps": [], "failures": []}
    app = window_module.build_application([sys.argv[0]])
    if args.engine_config:
        config = load_engine_config(Path(args.engine_config).expanduser())
        problem = config.connection_problem()
        if problem:
            result["failures"].append(f"real engine not usable: {problem}")
        from urllib.parse import urlparse

        result["real_engine"] = {
            "provider": config.provider,
            "model": config.model,
            "endpoint_host": urlparse(config.base_url or "").netloc,
            "reviewer_mode": config.reviewer_mode,
            "connection_problem": problem,
            "api_key_loaded": bool(config.api_key),
        }
    else:
        config = selftest.engine_from_env(adapter, adapter.EngineConfig.load())
    window = window_module.MainWindow(config, paths, None)
    window.show()
    # S1 is a foreground session: the user is looking at and interacting with
    # this window.  Raising it keeps the measured session comparable across
    # runs (and out of background timer-throttling), and uses only Qt's
    # cross-platform API.
    window.raise_()
    window.activateWindow()

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
    driver = _ScenarioDriver(app=app, window=window, adapter=adapter, jank=jank,
                             fixture=fixture, seconds=args.seconds,
                             settle_timeout=args.settle_timeout, result=result)
    timer = QTimer(app)
    timer.setInterval(_TICK_MS)

    def on_tick() -> None:
        if driver.tick():
            timer.stop()

    timer.timeout.connect(on_tick)
    safety = QTimer(app)
    safety.setSingleShot(True)
    safety.timeout.connect(app.quit)
    safety.start(int((driver.seconds + driver.settle_timeout + 120.0) * 1000))

    try:
        QTimer.singleShot(0, driver.begin)
        timer.start()
        # The scenario runs in the application's event loop, as callbacks — no
        # processEvents pump from script code.
        _run_scenario_event_loop(app)
        timer.stop()
        safety.stop()
        if not driver.finished:
            result["failures"].append("scenario did not finish (event loop ended early)")
        job_id = driver.job_id
        state = (adapter.load_state(job_id) or {}) if job_id else {}
        result["translation"] = {
            "settled": driver.settled,
            "runtime_status": ((adapter.core.load_runtime_state(job_id) or {}).get("status")
                               if job_id else ""),
            "p2_done": bool(state.get("p2_done")),
            "segments": len(state.get("pairs") or []),
            "edited_segments": driver.edited,
            "page_switches": driver.pages,
            "scroll_events": driver.scrolls,
        }
        if job_id:
            result["run_records"] = read_run_records(paths.data_dir, job_id)
    except Exception as exc:  # noqa: BLE001 - the JSON is the evidence
        import traceback

        result["failures"].append(f"{type(exc).__name__}: {exc}")
        result["traceback"] = traceback.format_exc()
    finally:
        result["wall_time_s"] = round(time.monotonic() - started, 2)
        if jank is not None and checkpoint is not None:
            result["gui_jank"] = _normalize_window(jank.summary_since(checkpoint))
            result["gui_jank_process"] = _normalize_window(jank.summary())
        else:
            result["gui_jank"] = {
                "measured": False,
                "reason": "the UI health monitor did not start, so no stall "
                          "window exists for this run",
            }
        monitor.stop()
        _teardown(app, window)
    return result


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
    # The selftest entry point now starts the UI health monitor before the flow,
    # so its run records carry a measured window; if a build predates that, the
    # report says "not measured" instead of implying zero stalls.
    result["gui_jank"] = _stall_window_from_records(result["run_records"])
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
    parser.add_argument("--engine-config", default=None,
                        help="real provider config file, read in memory: runs the "
                             "scenario against the real endpoint instead of the mock "
                             "gateway (the key is never written into any output)")
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
        if args.engine_config:
            # Real endpoint: no mock gateway, and no selftest endpoint override.
            base_url = ""
        elif not base_url:
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
        # #327: which measurement definition produced these numbers.  A report
        # without this marker predates measurement v2 (harness pumped the GUI).
        "measurement": {
            "version": 2,
            "driver": "qt_event_loop_callbacks",
            "instrumentation_split": True,
        },
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
            "real_engine": result.get("real_engine") or None,
            "data_dir": str(data_dir),
        },
        "translation": result.get("translation") or {},
        "steps": result.get("steps") or [],
        "failures": result.get("failures") or [],
        "metrics": {
            # Run-scoped stall window straight from the sampler; the run record
            # below is the product's own copy of the same measurement.
            "gui_jank": result.get("gui_jank") or {
                "measured": False,
                "reason": "this mode did not collect a stall window",
            },
            "gui_jank_process": result.get("gui_jank_process") or {
                "measured": False,
                "reason": "this mode did not collect a session-level stall view",
            },
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
                      "gui_jank_measured": payload["metrics"]["gui_jank"].get("measured"),
                      "slow_count": payload["metrics"]["gui_jank"].get("slow_count"),
                      "severe_count": payload["metrics"]["gui_jank"].get("severe_count")},
                     ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
