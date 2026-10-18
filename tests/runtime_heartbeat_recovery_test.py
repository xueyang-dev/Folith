"""Issue #13: live execution, heartbeat faults and retry policy must agree."""
import os
import threading
from types import SimpleNamespace

import pytest

import core
from folith.application import read_models


OLD = "2000-01-01T00:00:00+00:00"


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "heartbeat-recovery"
    state = core.new_job_state("fixture.pdf")
    core.save_job_state(job_id, state)
    core.update_runtime_state(
        job_id, status="running", operation_label="正在解析 PDF",
        last_heartbeat_at=OLD, last_progress_at=OLD,
        worker={"owner_pid": os.getpid(), "worker_id": "worker-1", "lease_expires_at": OLD})
    return job_id


def local_worker(monkeypatch, job):
    monkeypatch.setitem(core._RUNTIME_WORKERS, job,
                        SimpleNamespace(is_alive=lambda: True, worker_id="worker-1"))


class Ticks:
    """Drive heartbeat iterations without timeouts or sleeping in unit tests."""
    def __init__(self, count, before_tick=lambda tick: None):
        self.count = count
        self.tick = 0
        self.before_tick = before_tick

    def wait(self, _interval):
        self.tick += 1
        if self.tick > self.count:
            return True
        self.before_tick(self.tick)
        return False


@pytest.mark.parametrize("status", ["running", "waiting_external", "pausing", "paused", "cancelling"])
def test_live_local_worker_is_not_stalled_by_old_timestamps(job, monkeypatch, status):
    local_worker(monkeypatch, job)
    core.update_runtime_state(job, status=status)
    runtime = core.get_job_runtime_status(job)
    assert runtime["status"] == status
    assert not any(e["event"] == "stalled" for e in core.read_runtime_events(job))


def test_old_false_stall_is_reconciled_and_cannot_restart(job, monkeypatch):
    local_worker(monkeypatch, job)
    core.update_runtime_state(job, status="stalled", phase_label="没有运行信号")
    view = core.build_job_runtime_view(job)
    assert view["status"] == "running"
    assert view["detail"] == "正在解析 PDF"
    assert "retry" not in view["available_actions"]
    assert not core.retry_job_step(job)
    assert not core.start_job_worker(job, "fixture.pdf", None, {})


def foreign_owner(job, monkeypatch):
    core.update_runtime_state(job, worker={
        "owner_pid": 12345, "worker_id": "worker-1", "lease_expires_at": OLD})
    monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: pid == 12345)


def test_recent_progress_is_a_signal_even_when_heartbeat_is_old(job, monkeypatch):
    foreign_owner(job, monkeypatch)
    core.update_runtime_state(job, progress=True, event="已处理 698 页")
    assert core.get_job_runtime_status(job)["status"] == "running"


def test_recent_model_response_is_a_signal(job, monkeypatch):
    from folith.engine.runtime_telemetry import _RuntimeTelemetryCoalescer

    foreign_owner(job, monkeypatch)
    telemetry = _RuntimeTelemetryCoalescer(job, flush_interval=0)
    try:
        telemetry.request_started()
        assert core.get_job_runtime_status(job)["status"] == "waiting_external"
        telemetry.request_finished()
        assert core.get_job_runtime_status(job)["status"] == "running"
        assert core.load_runtime_state(job)["last_progress_at"] != OLD
    finally:
        telemetry.close()


def test_foreign_stalled_owner_keeps_cancel_but_cannot_retry(job, monkeypatch):
    foreign_owner(job, monkeypatch)
    view = core.build_job_runtime_view(job)
    assert view["status"] == "stalled"
    assert view["available_actions"] == ["cancel", "details"]
    assert view["primary_action"] is None
    assert "后台任务仍在运行" in view["detail"]
    assert read_models.job_has_live_worker(job)
    summary = read_models._summary_row(job, {"filename": "fixture.pdf", "attention_count": 0})
    assert "retry" not in summary.available_actions
    assert summary.primary_action is None
    assert not core.retry_job_step(job)


def test_dead_owner_still_offers_recovery(job, monkeypatch):
    monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: False)
    view = core.build_job_runtime_view(job)
    assert view["status"] == "interrupted"
    assert "retry" in view["available_actions"]


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_heartbeat_retries_and_logs_transient_write_failure(job, monkeypatch, error_type):
    original = core.update_runtime_state
    calls = []

    def flaky(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise error_type("api_key=sk-private")
        return original(*args, **kwargs)

    monkeypatch.setattr(core, "update_runtime_state", flaky)
    core._runtime_heartbeat_loop(job, Ticks(2), "worker-1")
    assert len(calls) == 2
    assert core.load_runtime_state(job)["last_heartbeat_at"] != OLD
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert error_type.__name__ in log and "recovered" in log
    assert "sk-private" not in log


def test_repeated_heartbeat_errors_are_logged_once_per_episode(job, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise PermissionError("locked")
    monkeypatch.setattr(core, "update_runtime_state", fail)
    core._runtime_heartbeat_loop(job, Ticks(3), "worker-1")
    assert len(calls) == 3
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert log.count("heartbeat update failed") == 1


def test_unreadable_state_does_not_end_heartbeat(job):
    saved = core.runtime_state_path(job).read_text(encoding="utf-8")
    core.runtime_state_path(job).write_text("{", encoding="utf-8")
    def repair(tick):
        if tick == 2:
            core.runtime_state_path(job).write_text(saved, encoding="utf-8")
    core._runtime_heartbeat_loop(job, Ticks(2, repair), "worker-1")
    assert core.load_runtime_state(job)["last_heartbeat_at"] != OLD
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "state unavailable" in log and "recovered" in log


@pytest.mark.parametrize("intent,expected", [(None, "running"), ("cancel_requested", "cancelling"),
                                             ("pause_requested", "pausing")])
def test_heartbeat_recovers_false_stall_and_preserves_intent(job, intent, expected):
    changes = {intent: True} if intent else {}
    core.update_runtime_state(job, status="stalled", **changes)
    core._runtime_heartbeat_loop(job, Ticks(1), "worker-1")
    runtime = core.load_runtime_state(job)
    assert runtime["status"] == expected
    assert runtime["last_heartbeat_at"] != OLD
    assert runtime["worker"]["lease_expires_at"] != OLD


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "idle_incomplete"])
def test_heartbeat_never_revives_terminal_state(job, status):
    core.update_runtime_state(job, status=status)
    before = core.runtime_state_path(job).read_bytes()
    core._runtime_heartbeat_loop(job, Ticks(2), "worker-1")
    assert core.runtime_state_path(job).read_bytes() == before


def test_old_heartbeat_does_not_renew_replacement_worker(job):
    core.update_runtime_state(job, worker={"owner_pid": os.getpid(), "worker_id": "replacement"})
    before = core.runtime_state_path(job).read_bytes()
    core._runtime_heartbeat_loop(job, Ticks(2), "worker-1")
    assert core.runtime_state_path(job).read_bytes() == before


def test_fatal_workflow_error_stops_heartbeat_without_renewing(job, monkeypatch):
    calls = []
    def stopped(job_id):
        calls.append(job_id)
        raise core._workflow_errors.WorkflowStopped("disk full", reason="disk_full")
    monkeypatch.setattr(core._workflow_errors, "check", stopped)
    before = core.runtime_state_path(job).read_bytes()
    core._runtime_heartbeat_loop(job, Ticks(2), "worker-1")
    assert calls == [job]
    assert core.runtime_state_path(job).read_bytes() == before


def test_real_waiting_pipeline_remains_active_without_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(core, "_runtime_heartbeat_loop", lambda job, stop, worker_id=None: stop.wait(5))
    def pipeline(job, *args, **kwargs):
        entered.set()
        release.wait(5)
        return core.load_job_state(job)
    monkeypatch.setattr(core, "run_job_pipeline", pipeline)
    job = "slow-extraction"
    assert core.start_job_worker(job, "fixture.pdf", None, {"enable_report": False})
    worker = core._RUNTIME_WORKERS[job]
    try:
        assert entered.wait(2)
        core.update_runtime_state(job, last_heartbeat_at=OLD, last_progress_at=OLD,
                                  worker={"owner_pid": os.getpid(), "worker_id": worker.worker_id,
                                          "lease_expires_at": OLD})
        for _ in range(3):
            view = core.build_job_runtime_view(job)
            assert view["status"] == "running"
            assert "retry" not in view["available_actions"]
        assert not core.retry_job_step(job)
        assert not core.start_job_worker(job, "fixture.pdf", None, {})
        assert core.load_runtime_state(job)["attempt"] == 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
