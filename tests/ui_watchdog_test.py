"""The gate must outlive a held GIL and must not call sleep a UI failure."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from folith.desktop.ui_watchdog import HeartbeatGate, latest_incident, _runtime


def gate_at_zero(timeout=30):
    gate = HeartbeatGate(timeout)
    gate.last_tick = gate.last_wall = gate.last_pulse = 0
    return gate


def test_timeout_is_one_incident_until_real_recovery():
    gate = gate_at_zero()
    events = [gate.tick(t, t, pulse=False) for t in range(1, 61)]
    assert events.count("unresponsive") == 1
    assert gate.tick(61, 61, pulse=True) == "recovered"
    assert gate.tick(62, 62, pulse=True) is None


@pytest.mark.parametrize("monotonic_advances", [False, True])
def test_sleep_requires_a_full_active_timeout_after_wake(monotonic_advances):
    gate = gate_at_zero()
    for t in range(1, 29):
        assert gate.tick(t, t, pulse=False) is None
    offset = 3600 if monotonic_advances else 0
    assert gate.tick(29 + offset, 3629, pulse=False) is None
    for t in range(30, 59):
        assert gate.tick(t + offset, t + 3600, pulse=False) is None
    assert gate.tick(59 + offset, 3659, pulse=False) == "unresponsive"


def test_runtime_and_export_do_not_copy_document_or_credentials(tmp_path):
    job = tmp_path / "abcd"
    job.mkdir()
    (job / "runtime_state.json").write_text(json.dumps({
        "status": "waiting_external", "last_event": "private document sentence",
        "events": [{"api_key": "secret"}], "phase": "waiting_llm",
    }))
    assert _runtime(tmp_path, "abcd") == {"status": "waiting_external", "phase": "waiting_llm"}
    session = tmp_path / "ui-health/session-test"
    session.mkdir(parents=True)
    (session / "incident.json").write_text(json.dumps({
        "status": "unresponsive", "evidence": "ui_heartbeat_timeout",
        "snapshot": {"job_id": "abcd"}, "raw": "private document sentence",
    }))
    assert latest_incident(tmp_path) == {"status": "unresponsive", "evidence": "ui_heartbeat_timeout"}
    from folith.desktop import diagnostics
    from folith.desktop.paths import DesktopPaths
    destination = tmp_path / "support.json"
    diagnostics.export_bundle(destination, paths=DesktopPaths(tmp_path, tmp_path))
    exported = destination.read_text()
    assert json.loads(exported)["diagnostics"]["ui_health"]["status"] == "unresponsive"
    assert "private document sentence" not in exported
    assert "job_id" not in exported


@pytest.mark.parametrize("mode", ["healthy", "gil_stall", "observer_failed"])
def test_independent_gate_with_real_qt_and_gil_stall(tmp_path, mode):
    pytest.importorskip("PySide6")
    script = r'''
import ctypes
import json
from pathlib import Path
import sys
import time
import threading
from types import SimpleNamespace
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from folith.desktop.paths import DesktopPaths
from folith.qt.ui_health import UiHealthMonitor

root, mode = Path(sys.argv[1]), sys.argv[2]
paths = DesktopPaths(root / "data", root / "logs").ensure()
app = QGuiApplication([])
window = SimpleNamespace(_cached_state={}, _current_job_id="", runner=SimpleNamespace(_running=[]), winId=lambda: 0)
monitor = UiHealthMonitor(app, window, paths, timeout=.5)
monitor.start()
monitor.timer.setInterval(100)
ticks = []
heartbeat = QTimer()
heartbeat.timeout.connect(lambda: ticks.append(1))
heartbeat.start(50)

def block_with_gil():
    if sys.platform == "win32":
        library = ctypes.PyDLL("kernel32")
        library.Sleep(4000)
    else:
        library = ctypes.PyDLL(None)
        library.sleep(4)  # PyDLL keeps the GIL, unlike CDLL/time.sleep.

if mode == "healthy":
    threading.Thread(target=lambda: time.sleep(3), daemon=True).start()
if mode == "gil_stall":
    QTimer.singleShot(200, block_with_gil)
if mode == "observer_failed":
    QTimer.singleShot(100, monitor.process.terminate)

def finish():
    try:
        incident = monitor.directory / "incident.json"
        if mode == "healthy":
            assert not incident.exists(), incident.read_text() if incident.exists() else ""
        elif mode == "observer_failed":
            assert json.loads(incident.read_text())["status"] == "observer_failed"
        else:
            report = json.loads(incident.read_text())
            assert report["status"] == "recovered", report
            assert report["capture_status"] == "complete", report
            if sys.platform == "darwin":
                assert report["native_sample_returncode"] == 0, report
                assert (monitor.directory / "native-sample.txt").stat().st_size > 0
            stacks = (monitor.directory / "python-stacks.log").read_text()
            assert "block_with_gil" in stacks, stacks
        assert len(ticks) > 5, ticks
        monitor.stop()
        assert monitor.process is None
        print("gate passed", mode, flush=True)
        app.exit(0)
    except BaseException:
        import traceback
        traceback.print_exc()
        monitor.stop()
        app.exit(1)

QTimer.singleShot(8000 if mode == "gil_stall" else 2000, finish)
raise SystemExit(app.exec())
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), mode],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"gate passed {mode}" in result.stdout


def test_unexpected_exit_keeps_crash_evidence(tmp_path):
    directory = tmp_path / "session-exit"
    directory.mkdir()
    (directory / "crash-stacks.log").write_text("Fatal Python error: Bus error\n")
    result = subprocess.run(
        [sys.executable, "-m", "folith.qt", "--ui-watchdog", str(os.getpid()),
         str(directory), str(tmp_path), "30"],
        input='{"job_id":"abcd"}\n', text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((directory / "incident.json").read_text())
    assert report["status"] == "unexpected_exit"
    assert report["evidence"] == "crash_log_available"


def test_normal_entrypoint_starts_and_stops_observer(tmp_path):
    pytest.importorskip("PySide6")
    script = r'''
from pathlib import Path
import sys
from PySide6.QtCore import QTimer
from folith.qt.ui_health import UiHealthMonitor
from folith.qt.app import main
started = []
original = UiHealthMonitor.start

def start(self):
    original(self)
    started.append(self)
    QTimer.singleShot(1500, self.app.quit)

UiHealthMonitor.start = start
root = Path(sys.argv[1])
assert main(["--data-dir", str(root / "data"), "--log-dir", str(root / "logs"),
             "--allow-second-instance"]) == 0
assert len(started) == 1
assert started[0].process is None
assert not (started[0].directory / "incident.json").exists()
print("entrypoint gate passed")
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1],
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "entrypoint gate passed" in result.stdout
