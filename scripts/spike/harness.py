"""A2 spike harness: GUI-thread smoothness, in-process worker vs child process.

Runs the same CPU-shaped workload (``workload.BatchEngine``) in two modes while a
Qt event loop runs in the main thread with A0's pulse metric:

* ``inprocess``  -- worker runs as a ``threading.Thread`` in the GUI process
  (the current product architecture: ``core.start_job_worker``);
* ``subprocess`` -- worker runs in a fresh child interpreter, controlled and
  observed through the same durable files (command / progress / status);
* ``idle``       -- GUI alone, the measurement noise floor.

Lifecycle for both worker modes: start -> translate -> pause -> continue ->
exit (``interrupted``) -> resume -> completed.  ``--crash`` kills the child
mid-run to exercise failure isolation; in-process mode has no crash analogue
(a crash would take the GUI with it, which is the point).

The prototype lives under ``scripts/spike`` and touches no product code.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from stall_probe import PulseProbe  # noqa: E402
from workload import BatchEngine, read_commands, write_json_atomic  # noqa: E402


def _now() -> float:
    return time.monotonic()


def _rss_mb(pid: int) -> float | None:
    try:
        result = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5)
        return round(int(result.stdout.strip()) / 1024.0, 1) if result.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _progress_lines(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    records = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.directory = Path(args.data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        for name in ("control.json", "progress.jsonl", "status.json", "terminal.json"):
            (self.directory / name).unlink(missing_ok=True)
        self.control_path = self.directory / "control.json"
        self.progress_path = self.directory / "progress.jsonl"
        self.status_path = self.directory / "status.json"
        self.terminal_path = self.directory / "terminal.json"
        self.log_path = self.directory / "worker.log"
        self.events: list[dict] = []
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        # Only the in-process host needs the workload objects in the GUI process;
        # in child mode they must live in the child, otherwise the RSS comparison
        # would measure the harness rather than the architecture.
        self.engine = (BatchEngine(segments=args.segments, batches=args.batches,
                                   latency_s=args.latency, parse_mb=args.parse_mb)
                       if args.mode == "inprocess" else None)
        self.engine_error = ""
        self.done = threading.Event()
        self.gui_rss_mb = _rss_mb(os.getpid())
        self.child_rss_mb = None
        self.spawn_to_first_delta_ms: float | None = None

    # -- small helpers ----------------------------------------------------
    def event(self, name: str, **data: Any) -> None:
        self.events.append({"at_s": round(_now() - self.t0, 3), "event": name, **data})

    def command(self, value: str) -> None:
        write_json_atomic(self.control_path, {"command": value})
        self.event(f"command:{value}")

    def wait_lines(self, count: int, timeout: float) -> bool:
        deadline = _now() + timeout
        while _now() < deadline:
            if len(_progress_lines(self.progress_path)) >= count:
                return True
            time.sleep(0.02)
        return False

    def wait_progress_stable(self, timeout: float = 3.0, quiet: float = 0.4) -> bool:
        """True when no new batch committed for ``quiet`` seconds (paused)."""
        deadline = _now() + timeout
        last_count = len(_progress_lines(self.progress_path))
        stable_since = _now()
        while _now() < deadline:
            time.sleep(0.05)
            count = len(_progress_lines(self.progress_path))
            if count != last_count:
                last_count, stable_since = count, _now()
            elif _now() - stable_since >= quiet:
                return True
        return False

    def wait_terminal(self, timeout: float) -> dict | None:
        deadline = _now() + timeout
        while _now() < deadline:
            try:
                return json.loads(self.terminal_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.02)
        return None

    # -- worker hosts -----------------------------------------------------
    def _start_inprocess(self, resume_from: int, crash_after: int | None) -> None:
        def emit(record: dict) -> None:
            with self.progress_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()

        def status(state: str, payload: dict) -> None:
            record = {"status": state, "pid": os.getpid(), **payload}
            write_json_atomic(self.status_path, record)
            if state in {"interrupted", "completed", "failed"}:
                write_json_atomic(self.terminal_path, record)

        def run() -> None:
            assert self.engine is not None
            try:
                self.engine.run(control=lambda: read_commands(self.control_path),
                                emit=emit, status=status,
                                resume_from=resume_from, crash_after=crash_after)
            except Exception as exc:  # noqa: BLE001 - the harness must report, not hang
                self.engine_error = f"{type(exc).__name__}: {exc}"
                status("failed", {"error": self.engine_error})

        self.thread = threading.Thread(target=run, name="spike-worker", daemon=True)
        self.thread.start()

    def _start_subprocess(self, resume_from: int, crash_after: int | None) -> None:
        command = [sys.executable, str(HERE / "worker.py"),
                   "--data-dir", str(self.directory),
                   "--segments", str(self.args.segments),
                   "--batches", str(self.args.batches),
                   "--latency", str(self.args.latency),
                   "--parse-mb", str(self.args.parse_mb),
                   "--resume-from", str(resume_from)]
        if crash_after is not None:
            command += ["--crash-after", str(crash_after)]
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self.process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=self.log_path.open("ab"), **kwargs)

    def start_worker(self, resume_from: int = 0, crash_after: int | None = None) -> None:
        started = _now()
        self.event("worker:start", mode=self.args.mode, resume_from=resume_from)
        if self.args.mode == "subprocess":
            self._start_subprocess(resume_from, crash_after)
        else:
            self._start_inprocess(resume_from, crash_after)
        if self.wait_lines(resume_from + 1, timeout=30):
            self.spawn_to_first_delta_ms = round((_now() - started) * 1000, 1)
            self.event("worker:first_delta", ms=self.spawn_to_first_delta_ms)

    def stop_worker(self) -> str:
        """Request exit and wait with a bound (lifecycle contract §2.3)."""
        self.command("exit")
        terminal = self.wait_terminal(timeout=self.args.exit_timeout)
        if terminal is None:
            if self.process is not None:
                self.process.kill()
            self.event("worker:forced_exit")
            return "forced"
        self.event("worker:terminal", status=terminal.get("status"),
                   seq=terminal.get("seq"))
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread is not None:
            self.thread.join(timeout=5)
        return str(terminal.get("status"))

    # -- the scenario -----------------------------------------------------
    def scenario(self) -> dict[str, Any]:
        args = self.args
        self.t0 = _now()
        pause_at = min(args.batches, max(2, args.batches // 4))
        continue_at = min(args.batches, pause_at + 2)

        self.start_worker()
        self.wait_lines(pause_at, timeout=args.batch_timeout * args.batches)
        self.child_rss_mb = _rss_mb(self.process.pid) if self.process is not None else _rss_mb(os.getpid())
        self.command("pause")
        held = self.wait_progress_stable(timeout=args.pause_seconds + 2.0)
        paused_at = len(_progress_lines(self.progress_path))
        self.event("pause:observed", lines=paused_at, still_running=not held)
        self.command("run")
        self.wait_lines(continue_at, timeout=args.batch_timeout * args.batches)
        self.event("continue:observed", lines=len(_progress_lines(self.progress_path)))

        if args.crash:
            self.wait_lines(min(args.batches, continue_at + 1), timeout=args.batch_timeout * 10)
            if self.process is None:
                raise SystemExit("--crash is defined for subprocess mode only")
            self.process.kill()
            self.process.wait(timeout=10)
            committed = _progress_lines(self.progress_path)
            seq = int(committed[-1]["seq"]) if committed else 0
            # Lease recovery analogue: the observer publishes `interrupted` from
            # the last committed cursor instead of leaving a dangling `running`.
            write_json_atomic(self.terminal_path,
                              {"status": "interrupted", "pid": None, "seq": seq,
                               "committed_batches": seq, "recovered_from": "lease"})
            self.event("crash:recovered", exit_code=self.process.returncode, seq=seq)
        else:
            # Exit before completion: must land on `interrupted`, bounded wait.
            exit_started = _now()
            self.command("exit")
            terminal = self.wait_terminal(timeout=args.exit_timeout)
            self.event("exit:observed", terminal=terminal, ms=round((_now() - exit_started) * 1000, 1))
            if terminal is None:
                raise SystemExit("exit did not reach a terminal state within the bound")
        terminal = json.loads(self.terminal_path.read_text(encoding="utf-8"))
        resume_from = int(terminal.get("seq") or 0)

        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread is not None:
            self.thread.join(timeout=5)

        resume_started = _now()
        self.terminal_path.unlink(missing_ok=True)   # never read the old terminal
        self.command("run")
        self.start_worker(resume_from=resume_from)
        final = self.wait_terminal(timeout=args.batch_timeout * args.batches)
        self.event("resume:observed", status=(final or {}).get("status"),
                   ms=round((_now() - resume_started) * 1000, 1), resume_from=resume_from)
        if final is None or final.get("status") != "completed":
            raise SystemExit(f"resume did not complete: {final}")
        if self.process is not None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread is not None:
            self.thread.join(timeout=5)
        return final

    def report(self, probe: PulseProbe, scenario: dict) -> dict[str, Any]:
        records = _progress_lines(self.progress_path)
        seqs = [int(record.get("seq") or 0) for record in records]
        duplicates = sorted({seq for seq in seqs if seqs.count(seq) > 1})
        return {
            "spike": "a2-process-isolation",
            "mode": self.args.mode,
            "params": {
                "segments": self.args.segments, "batches": self.args.batches,
                "batch_size": 20, "latency_s": self.args.latency,
                "parse_mb": self.args.parse_mb,
                "crash": bool(self.args.crash),
            },
            "gui_jank": probe.summary(),
            "lifecycle": {
                "events": self.events,
                "pause_held": any(event["event"] == "pause:observed"
                                  and not event.get("still_running") for event in self.events),
                "terminal": scenario,
                "duplicate_seq": duplicates,
                "committed_batches": len({seq for seq in seqs if seq}),
            },
            "cost": {
                "spawn_to_first_delta_ms": self.spawn_to_first_delta_ms,
                "gui_rss_mb": self.gui_rss_mb,
                "child_rss_mb": self.child_rss_mb,
                "worker_error": self.engine_error,
            },
            "environment": {
                "platform": sys.platform, "python": sys.version.split()[0],
                "pid": os.getpid(), "frozen": bool(getattr(sys, "frozen", False)),
            },
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("idle", "inprocess", "subprocess"), default="subprocess")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", default=None, help="report JSON path (default: <data-dir>/report.json)")
    parser.add_argument("--segments", type=int, default=2000)
    parser.add_argument("--batches", type=int, default=40)
    parser.add_argument("--latency", type=float, default=0.02)
    parser.add_argument("--parse-mb", type=float, default=0.0,
                        help="extra large JSON parse per batch (GIL-holding C call)")
    parser.add_argument("--pause-seconds", type=float, default=1.2)
    parser.add_argument("--exit-timeout", type=float, default=8.0)
    parser.add_argument("--batch-timeout", type=float, default=10.0)
    parser.add_argument("--crash", action="store_true")
    parser.add_argument("--idle-seconds", type=float, default=8.0)
    args = parser.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    sys.setswitchinterval(0.001)     # same mitigation as folith/qt/app.py:94

    app = QApplication.instance() or QApplication([])
    probe = PulseProbe()
    harness = Harness(args)
    probe.start()

    failure: list[str] = []
    scenario_box: dict[str, Any] = {}

    def orchestrate() -> None:
        try:
            if args.mode == "idle":
                time.sleep(args.idle_seconds)
                scenario_box.update({"status": "idle"})
            else:
                scenario_box.update(harness.scenario())
        except SystemExit as exc:
            failure.append(str(exc))
            scenario_box.update(harness.events[-1] if harness.events else {})
        except Exception as exc:  # noqa: BLE001 - always produce a report
            import traceback
            traceback.print_exc()
            failure.append(f"{type(exc).__name__}: {exc}")
            scenario_box.update(harness.events[-1] if harness.events else {})
        finally:
            harness.done.set()

    threading.Thread(target=orchestrate, name="spike-orchestrator", daemon=True).start()

    def poll_done() -> None:
        if harness.done.is_set():
            app.quit()

    timer = QTimer()
    timer.timeout.connect(poll_done)
    timer.start(50)
    app.exec()

    report = harness.report(probe, scenario_box)
    if failure:
        report["failure"] = failure
    output = Path(args.out) if args.out else harness.directory / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"mode": args.mode, **report["gui_jank"], "lifecycle": {
        "pause_held": report["lifecycle"]["pause_held"],
        "duplicate_seq": report["lifecycle"]["duplicate_seq"],
        "terminal": report["lifecycle"]["terminal"],
        "failure": failure,
    }, "cost": report["cost"]}, ensure_ascii=False))
    print(f"report: {output}")
    return 0 if not failure else 1


if __name__ == "__main__":
    raise SystemExit(main())
