"""Out-of-process UI heartbeat gate. Never imports Qt or the business core."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

TIMEOUT_SECONDS = 30.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HeartbeatGate:
    """Require a full active timeout after scheduler gaps / system suspend."""

    def __init__(self, timeout: float = TIMEOUT_SECONDS) -> None:
        self.timeout = timeout
        self.last_tick = time.monotonic()
        self.last_wall = time.time()
        self.last_pulse = self.last_tick
        self.reported = False

    def tick(self, now: float, wall: float, *, pulse: bool) -> str | None:
        elapsed = now - self.last_tick
        wall_elapsed = wall - self.last_wall
        suspended = elapsed > 5.0 or wall_elapsed - elapsed > 5.0
        self.last_tick, self.last_wall = now, wall
        if suspended:
            self.last_pulse = now
            # Keep an existing incident until a real UI pulse proves recovery.
            return None
        if pulse:
            self.last_pulse = now
            if self.reported:
                self.reported = False
                return "recovered"
        elif not self.reported and now - self.last_pulse >= self.timeout:
            self.reported = True
            return "unresponsive"
        return None


def _write_report(directory: Path, report: dict) -> None:
    temporary = directory / "incident.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(directory / "incident.json")


def _runtime(data_dir: Path, job_id: str) -> dict:
    # Only application job IDs, never a caller-provided path or document body.
    if not job_id or not all(c in "0123456789abcdef" for c in job_id):
        return {}
    try:
        path = data_dir / job_id / "runtime_state.json"
        with path.open(encoding="utf-8") as stream:
            value = json.loads(stream.read(128 * 1024))
        return {key: value.get(key) for key in (
            "status", "stage", "operation", "phase", "last_heartbeat_at",
            "last_progress_at", "pause_requested", "cancel_requested",
        ) if key in value and isinstance(value[key], (str, bool, int, float, type(None)))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _windows_window_responds(window_handle: int) -> bool:
    # Native QFileDialog on Windows deliberately does not dispatch QTimers.
    # Probe the native event loop before calling that intentional wait a hang.
    if not window_handle:
        return False
    import ctypes
    from ctypes import wintypes
    send = ctypes.WinDLL("user32", use_last_error=True).SendMessageTimeoutW
    send.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t,
                     ctypes.c_ssize_t, wintypes.UINT, wintypes.UINT,
                     ctypes.POINTER(ctypes.c_size_t)]
    send.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t()
    return bool(send(window_handle, 0, 0, 0, 0x0002, 1000, ctypes.byref(result)))


def capture(pid: int, directory: Path, data_dir: Path, snapshot: dict) -> dict:
    report = {
        "status": "unresponsive", "detected_at": _now(), "pid": pid,
        "evidence": "ui_heartbeat_timeout", "capture_status": "capturing",
        "snapshot": snapshot, "runtime": _runtime(data_dir, snapshot.get("job_id", "")),
    }
    try:
        report["disk_free_bytes"] = shutil.disk_usage(directory).free
    except OSError:
        pass
    _write_report(directory, report)
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["/usr/bin/sample", str(pid), "1", "10", "-file", str(directory / "native-sample.txt")],
                capture_output=True, timeout=8,
            )
            report["native_sample_returncode"] = result.returncode
            if result.returncode == 0:
                native = (directory / "native-sample.txt").read_text(errors="replace")
                if "QLatch::waitInternal" in native and "QRasterPaintEngine" in native:
                    report["evidence"] = "qt_raster_latch_wait_observed"
        except (OSError, subprocess.TimeoutExpired):
            report["native_sample_returncode"] = None
    if sys.platform != "win32":
        # Registered faulthandler runs without acquiring the Python GIL.
        # Capture native state first: the signal may interrupt a blocking call.
        try:
            (directory / "python-stacks.log").write_bytes(b"")
            os.kill(pid, signal.SIGUSR2)
        except ProcessLookupError:
            report["process_exited_during_capture"] = True
        except OSError:
            report["python_signal_failed"] = True
    try:
        if sys.platform == "win32":
            command = ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"]
        else:
            command = ["ps", "-p", str(pid), "-o", "%cpu=,rss=,state="]
        result = subprocess.run(
            command, capture_output=True, text=True, errors="replace", timeout=3,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
        report["process_resources"] = result.stdout.strip()[:1024]
    except (OSError, subprocess.TimeoutExpired):
        pass
    report["capture_status"] = "complete"
    _write_report(directory, report)
    return report


def run(pid: int, directory: Path, data_dir: Path, timeout: float = TIMEOUT_SECONDS) -> int:
    stream = sys.stdin
    if stream is None:
        # PyInstaller's Windows windowed bootloader sets sys.stdin to None
        # even when Popen supplied an inherited pipe. Recover that pipe only
        # for the observer, without creating a console or changing the UI.
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            import msvcrt
            get_handle = ctypes.WinDLL("kernel32", use_last_error=True).GetStdHandle
            get_handle.argtypes = [wintypes.DWORD]
            get_handle.restype = wintypes.HANDLE
            descriptor = msvcrt.open_osfhandle(get_handle(-10), os.O_RDONLY)
        else:
            descriptor = os.dup(0)
        stream = os.fdopen(descriptor, "r", encoding="utf-8")
    messages: queue.Queue = queue.Queue(maxsize=1)

    def deliver(value):
        try:
            messages.get_nowait()
        except queue.Empty:
            pass
        try:
            messages.put_nowait(value)
        except queue.Full:
            pass

    def read():
        for line in stream:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    if value.get("event") == "stop":
                        deliver("stop")
                        return
                    deliver(value)
            except ValueError:
                continue
        deliver(None)  # Parent exited or explicitly closed the channel.

    threading.Thread(target=read, daemon=True).start()
    gate = HeartbeatGate(timeout)
    snapshot: dict = {}
    report = None
    while True:
        pulse = False
        try:
            value = messages.get(timeout=min(1.0, timeout / 4))
            if value == "stop":
                return 0
            if value is None:
                try:
                    crashed = (directory / "crash-stacks.log").stat().st_size > 0
                    if report is not None and report["status"] == "unresponsive":
                        report["status"] = "exited_after_hang"
                        report["process_exited_at"] = _now()
                        _write_report(directory, report)
                    else:
                        _write_report(directory, {
                            "status": "unexpected_exit", "detected_at": _now(),
                            "evidence": "crash_log_available" if crashed else "process_channel_closed_without_shutdown",
                            "snapshot": snapshot,
                        })
                except OSError:
                    pass
                return 0
            snapshot, pulse = value, True
        except queue.Empty:
            pass
        event = gate.tick(time.monotonic(), time.time(), pulse=pulse)
        try:
            if event == "unresponsive":
                if sys.platform == "win32" and _windows_window_responds(snapshot.get("window_handle", 0)):
                    gate.reported = False
                    gate.last_pulse = time.monotonic()
                    continue
                report = capture(pid, directory, data_dir, snapshot)
            elif event == "recovered" and report is not None:
                report["status"] = "recovered"
                report["recovered_at"] = _now()
                _write_report(directory, report)
        except OSError:
            # A full disk must not kill the observer or the application.
            pass


def latest_incident(log_dir: Path) -> dict:
    """Safe summary for support export; raw stacks stay local and separate."""
    root = log_dir / "ui-health"
    try:
        candidates = sorted(root.glob("session-*/incident.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            value = json.loads(candidates[0].read_text(encoding="utf-8"))
            return {key: value[key] for key in (
                "status", "detected_at", "recovered_at", "process_exited_at", "evidence", "capture_status", "disk_free_bytes",
            ) if key in value and isinstance(value[key], (str, int, float, type(None)))}
    except (OSError, ValueError, TypeError):
        pass
    return {}
