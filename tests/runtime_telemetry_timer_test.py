"""A drained telemetry timer must not resurrect a completed worker."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def timer_env(tmp_path, monkeypatch):
    # Pin paths before importing core; no test touches the desktop's data dir.
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(tmp_path / "data"))
    from folith.qt import bootstrap

    bootstrap.prepare_environment(bundled_tools=False)
    import core
    from folith.engine import runtime_telemetry

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path / "data")
    timers = []

    class ManualTimer:
        def __init__(self, interval, callback):
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

        def fire(self):
            assert not self.cancelled
            self.callback()

    monkeypatch.setattr(runtime_telemetry, "threading", SimpleNamespace(
        RLock=threading.RLock, Timer=ManualTimer))
    monkeypatch.setattr(runtime_telemetry, "time", SimpleNamespace(
        monotonic=lambda: 100.0))
    return core, runtime_telemetry, timers


def _finish_two_requests(coalescer, timers):
    coalescer.request_started()
    coalescer.request_finished()
    # A new request inside the coalescing window schedules a trailing timer.
    coalescer.request_started()
    assert len(timers) == 1
    coalescer.request_finished()


def test_drained_timer_does_not_publish_another_response(timer_env):
    _core, telemetry, timers = timer_env
    writes = []
    coalescer = telemetry._RuntimeTelemetryCoalescer(
        "drained-timer", writer=lambda job, **changes: writes.append(changes))
    _finish_two_requests(coalescer, timers)
    assert writes[-1]["event_name"] == "llm_response_received"
    finished_writes = list(writes)

    timers[0].fire()

    assert writes == finished_writes


def test_completed_worker_stays_completed_when_drained_timer_fires(timer_env):
    core, telemetry, timers = timer_env
    job_id = "completed-timer"
    coalescer = telemetry._RuntimeTelemetryCoalescer(job_id)
    _finish_two_requests(coalescer, timers)
    core.update_runtime_state(
        job_id, status="completed", phase="completed",
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})

    # Replay the timer firing before worker cleanup closes the coalescer.
    timers[0].fire()

    assert core.load_runtime_state(job_id)["status"] == "completed"
    assert core.get_job_runtime_status(job_id, {})["status"] == "completed"


def test_timer_still_publishes_pending_request_telemetry(timer_env):
    _core, telemetry, timers = timer_env
    writes = []
    coalescer = telemetry._RuntimeTelemetryCoalescer(
        "pending-timer", writer=lambda job, **changes: writes.append(changes))
    coalescer.request_started()
    coalescer.request_finished()
    coalescer.request_started()
    before_timer = len(writes)

    timers[0].fire()

    assert len(writes) == before_timer + 1
    assert writes[-1]["status"] == "waiting_external"
    assert writes[-1]["event_name"] == "llm_request_started"
