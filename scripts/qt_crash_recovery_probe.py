#!/usr/bin/env python3
"""Qt crash recovery: SIGKILL the client mid-task, reopen, resume, finish.

Roadmap §6.C: "强制结束后既有检查点可恢复" and §6.A.4: the task must show a
credible recovery entry point.  This is done at the *process* level — the client
is killed with SIGKILL while a real task is in flight (no chance to clean up),
then a fresh window is opened on the same data directory.

Two phases in one script:

    --phase run     child: build the window, import, start the task, keep pumping
    --phase verify  parent: kill the child mid-flight, reopen, assert and resume

Usage:
    QT_QPA_PLATFORM=offscreen venv/bin/python scripts/qt_crash_recovery_probe.py \
        --data-dir /tmp/qt-crash/data --out /tmp/qt-crash/report.json
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

ACTIVE = {"resume_requested", "queued", "starting", "running",
          "waiting_external", "cancelling", "pausing", "paused"}


class Stub:
    def __init__(self, out_dir: Path, delay: float = 0.6) -> None:
        self.out_dir = out_dir
        self.delay = delay
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
             "--ready-file", str(ready), "--delay", str(self.delay)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if ready.exists():
                self.url = json.loads(ready.read_text(encoding="utf-8"))["url"]
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError("stub server exited")
            time.sleep(0.1)
        raise RuntimeError("stub server did not become ready")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.proc.kill()
        if self._handle is not None:
            self._handle.close()


def _prepare_env(data_dir: Path, log_dir: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(log_dir)
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)


def run_child(args) -> int:
    """Import a document, start the task, and keep the window alive."""
    data_dir = Path(args.data_dir).expanduser().resolve()
    _prepare_env(data_dir, Path(args.log_dir).expanduser().resolve())
    from folith.qt import adapter, selftest, window as w

    app = w.build_application([])
    config = adapter.EngineConfig(
        provider="自定义中转站", model="stub-model", api_key="stub-key",
        base_url=os.environ["FOLITH_QT_CRASH_STUB_URL"], target_lang="简体中文",
        enable_review=True, enable_report=False, enable_understanding=False,
        enable_source_cleanup=False)
    window = w.MainWindow(config, adapter.desktop_paths.resolve_paths(), None)
    window.show()
    fixture = Path(args.fixture).expanduser()
    window.import_paths([str(fixture)], autostart=False)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        app.processEvents()
        jobs = adapter.list_jobs()
        if jobs and adapter.core.load_source(jobs[0].job_id):
            break
        time.sleep(0.05)
    else:
        raise SystemExit("child: import did not complete")
    job_id = adapter.list_jobs()[0].job_id
    window.open_job(job_id)
    window.start_or_resume()
    (Path(args.log_dir) / "child-job.txt").write_text(job_id, encoding="utf-8")
    # Keep the process alive while the parent watches for mid-flight progress.
    while True:
        app.processEvents()
        time.sleep(0.02)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt crash recovery probe")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--phase", default="verify", choices=("run", "verify"))
    parser.add_argument("--paragraphs", type=int, default=60)
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    log_dir = Path(args.log_dir or (data_dir.parent / "logs")).expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    if args.phase == "run":
        return run_child(args)

    out_dir = Path(args.out).parent if args.out else data_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"started_at": time.time(), "steps": []}

    def step(name: str, **extra) -> None:
        report["steps"].append(
            {"at": round(time.time() - report["started_at"], 2),
             "step": name, **extra})

    stub = Stub(out_dir)
    url = stub.start()
    report["stub_url"] = url
    fixture = Path(args.fixture).expanduser() if args.fixture else out_dir / "crash.docx"
    if not fixture.exists():
        from folith.qt import selftest

        selftest.make_docx(fixture, [f"第 {n} 段，术语 MT。" for n in range(args.paragraphs)])

    env = dict(os.environ)
    env["FOLITH_QT_CRASH_STUB_URL"] = url
    child = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()),
         "--phase", "run", "--data-dir", str(data_dir), "--log-dir", str(log_dir),
         "--fixture", str(fixture), "--paragraphs", str(args.paragraphs)],
        env=env, stdout=(log_dir / "child.log").open("ab"),
        stderr=subprocess.STDOUT)
    report["child_pid"] = child.pid
    try:
        job_file = log_dir / "child-job.txt"
        job_file.unlink(missing_ok=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and not job_file.exists():
            if child.poll() is not None:
                raise AssertionError(
                    "child exited before starting a task: "
                    + (log_dir / "child.log").read_text(errors="replace")[-500:])
            time.sleep(0.2)
        job_id = job_file.read_text(encoding="utf-8").strip()
        report["job_id"] = job_id

        # Wait until the task is running, then let it make progress before the
        # kill, so the crash lands *inside* the pipeline rather than at startup.
        # Progress during translation is reported in `stage_progress`, not in
        # `completed_units`, so "active" is the honest signal here.
        runtime_path = data_dir / job_id / "runtime_state.json"

        def read_runtime() -> dict:
            try:
                return json.loads(runtime_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}

        runtime: dict = {}
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            runtime = read_runtime()
            if runtime.get("status") in ACTIVE:
                break
            if child.poll() is not None:
                raise AssertionError("child exited while the task was starting")
            time.sleep(0.2)
        else:
            raise AssertionError(
                f"the task never became active: {runtime.get('status')!r}")
        # Give it a moment of real work (batches are small, so keep this short).
        time.sleep(1.5)
        runtime = read_runtime()
        report["before_kill"] = {
            "status": runtime.get("status"),
            "completed_units": runtime.get("completed_units"),
            "total_units": runtime.get("total_units"),
            "stage_progress": runtime.get("stage_progress"),
            "phase": runtime.get("phase"),
            "still_active": runtime.get("status") in ACTIVE,
        }
        step("mid-flight", **report["before_kill"])
        if runtime.get("status") not in ACTIVE:
            raise AssertionError(
                "the task finished before it could be killed; widen the window "
                f"(status={runtime.get('status')!r})")

        # Hard kill: no cleanup, no chance to flush anything.
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=30)
        report["child_exit"] = child.returncode
        step("killed", exit_code=child.returncode)

        # Reopen on the same data directory with a fresh window.
        _prepare_env(data_dir, log_dir)
        from folith.qt import adapter, window as w

        app = w.build_application([])
        config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url=url, target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        window = w.MainWindow(config, adapter.desktop_paths.resolve_paths(), None)
        window.show()
        app.processEvents()

        def pump(seconds: float, predicate=None, interval: float = 0.05) -> bool:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.processEvents()
                if predicate is not None and predicate():
                    return True
                time.sleep(interval)
            app.processEvents()
            return bool(predicate()) if predicate is not None else True

        window.refresh_home()
        state = adapter.load_state(job_id)
        runtime = adapter.runtime_view(job_id, state)
        report["after_reopen"] = {
            "status": runtime.get("status"),
            "status_label": runtime.get("status_label"),
            "p1_done": bool(state.get("p1_done")),
            "p2_done": bool(state.get("p2_done")),
            "available_actions": runtime.get("available_actions"),
        }
        step("reopened", **report["after_reopen"])
        # The job must be reported as interrupted (or resumable), never complete.
        assert not (state.get("p1_done") and state.get("p2_done")), \
            "a killed task was reported as complete"
        assert runtime.get("status") in ("interrupted", "stalled",
                                         "idle_incomplete", "failed"), \
            f"unexpected status after a crash: {runtime.get('status')}"

        window.open_job(job_id)
        app.processEvents()
        report["recovery_entry"] = {
            "retry_visible": window.workbench.retry_button.isVisible(),
            "start_text": window.workbench.start_button.text(),
            "status_text": window.workbench.status_label.text(),
        }
        step("recovery-entry", **report["recovery_entry"])

        # Resume through the same entry point a user would use.
        window.start_or_resume()

        def finished() -> bool:
            current = adapter.load_state(job_id)
            rt = adapter.core.get_job_runtime_status(job_id, current)
            return (rt.get("status") not in adapter.core.RUNTIME_ACTIVE_STATUSES
                    and bool(current.get("p2_done")))

        report["resumed"] = pump(args.timeout, finished)
        state = adapter.load_state(job_id)
        report["after_resume"] = {
            "p1_done": bool(state.get("p1_done")),
            "p2_done": bool(state.get("p2_done")),
            "pairs": len(state.get("pairs") or []),
            "reviewed": sum(1 for pair in state.get("pairs") or []
                            if pair.get("reviewed")),
        }
        step("resumed", **report["after_resume"])

        # And the recovered task can be delivered.
        window.deliver()
        pump(180, lambda: bool(adapter.delivery_snapshots(job_id)))
        report["delivery"] = {
            "snapshots": [item.get("snapshot_version")
                          for item in adapter.delivery_snapshots(job_id)],
        }
        step("delivered", **report["delivery"])
        report["ok"] = bool(report["resumed"]
                            and report["after_resume"]["p2_done"]
                            and report["delivery"]["snapshots"])
    except BaseException as exc:  # noqa: BLE001 - the report is the evidence
        import traceback

        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        stub.stop()
        if child.poll() is None:
            child.kill()
        report.setdefault("ok", False)
        report["finished_at"] = time.time()
        report["duration_s"] = round(report["finished_at"] - report["started_at"], 1)

    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
