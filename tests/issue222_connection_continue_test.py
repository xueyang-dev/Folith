"""Issue #222: changing the connection while a task is paused must take effect.

When a task is paused its worker thread is still alive and keeps the connection
it read when it started.  "Continue" used to just clear the pause flag, so a new
API key / provider / model was silently ignored.  These tests pin the new
contract:

* a plain Continue (same connection) keeps the old behaviour;
* a Continue whose resolved connection differs asks the live worker to restart
  transparently with the new connection, keeping committed work;
* an API-key-only change is detected without persisting the key;
* the worker loop re-enters the pipeline with the new kwargs;
* the app command resolves the current connection against the task.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_pending_restart():
    with core._RUNTIME_PENDING_RESTART_LOCK:
        core._RUNTIME_PENDING_RESTART.clear()
    yield
    with core._RUNTIME_PENDING_RESTART_LOCK:
        core._RUNTIME_PENDING_RESTART.clear()


def _old_kwargs(**overrides):
    kwargs = {
        "provider": "自定义中转站",
        "model": "deepseek-v4.1-flash",
        "api_key": "old-relay-key",
        "translator_base_url": "https://relay.example/v1",
        "reasoning_effort": "",
    }
    kwargs.update(overrides)
    return kwargs


def _patch_continue(monkeypatch, runtime, state=None):
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: True)
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))
    monkeypatch.setattr(core, "load_job_state",
                        lambda job: dict(state or {"filename": "scan.pdf"}))

    events = []

    def update(job, **changes):
        runtime.update(changes)
        events.append(dict(changes))
        return dict(runtime)

    monkeypatch.setattr(core, "update_runtime_state", update)
    return events


def test_continue_with_unchanged_connection_stays_a_plain_continue(monkeypatch):
    kwargs = _old_kwargs()
    runtime = {"status": "paused", "pause_requested": True,
               "worker_connection": core._connection_signature_from_kwargs(kwargs)}
    events = _patch_continue(monkeypatch, runtime)
    assert core.continue_job("job", restart_kwargs=dict(kwargs)) is True
    assert runtime["pause_requested"] is False
    assert runtime.get("restart_requested") in (False, None)
    assert any(e.get("event_name") == "continue_requested" for e in events)
    with core._RUNTIME_PENDING_RESTART_LOCK:
        assert "job" not in core._RUNTIME_PENDING_RESTART


def test_continue_with_changed_provider_requests_a_restart(monkeypatch):
    runtime = {"status": "paused", "phase": "paused", "pause_requested": True,
               "worker_connection": core._connection_signature_from_kwargs(_old_kwargs())}
    events = _patch_continue(monkeypatch, runtime)
    new_kwargs = _old_kwargs(provider="DeepSeek", api_key="sk-new",
                             translator_base_url=None)
    assert core.continue_job("job", restart_kwargs=new_kwargs,
                             base_url=None, filename="scan.pdf") is True
    assert runtime["restart_requested"] is True
    assert runtime["pause_requested"] is False
    assert any(e.get("event_name") == "connection_changed_restart" for e in events)
    with core._RUNTIME_PENDING_RESTART_LOCK:
        pending = core._RUNTIME_PENDING_RESTART["job"]
    assert pending["kwargs"]["provider"] == "DeepSeek"
    assert pending["kwargs"]["api_key"] == "sk-new"
    assert pending["filename"] == "scan.pdf"


def test_api_key_only_change_is_detected_without_storing_the_key(monkeypatch):
    old = _old_kwargs()
    runtime = {"status": "paused", "pause_requested": True,
               "worker_connection": core._connection_signature_from_kwargs(old)}
    _patch_continue(monkeypatch, runtime)
    assert core._worker_connection_changed("job", _old_kwargs()) is False
    assert core._worker_connection_changed(
        "job", _old_kwargs(api_key="rotated-key")) is True
    # The persisted signature compares fingerprints, never the raw credential.
    stored = core._connection_signature_from_kwargs(old)
    assert stored["translator"]["api_key_fp"] != "old-relay-key"
    assert "api_key_fp" in stored["translator"]


def test_continue_without_recorded_connection_does_not_restart(monkeypatch):
    runtime = {"status": "paused", "pause_requested": True}
    events = _patch_continue(monkeypatch, runtime)
    assert core.continue_job("job", restart_kwargs=_old_kwargs(provider="Other")) is True
    assert runtime.get("restart_requested") in (False, None)
    assert any(e.get("event_name") == "continue_requested" for e in events)


def test_pause_checkpoint_unwinds_when_restart_is_requested(monkeypatch):
    runtime = {"status": "paused", "pause_requested": True,
               "restart_requested": True, "cancel_requested": False}
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: True)
    monkeypatch.setattr(core, "update_runtime_state", lambda job, **c: runtime.update(c))
    with core._RUNTIME_PENDING_RESTART_LOCK:
        core._RUNTIME_PENDING_RESTART["job"] = {
            "filename": "scan.pdf", "kwargs": _old_kwargs(), "base_url": None}
    with pytest.raises(core._WorkerRestartRequested):
        core._runtime_pause_checkpoint("job")


def test_stale_restart_flag_without_pending_request_does_not_fail_the_run(monkeypatch):
    runtime = {"status": "paused", "pause_requested": True,
               "restart_requested": True, "cancel_requested": False}
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: True)
    monkeypatch.setattr(core, "update_runtime_state", lambda job, **c: runtime.update(c))
    # A stale flag (no in-memory pending request) must be ignored, not crash.
    with core._RUNTIME_PENDING_RESTART_LOCK:
        core._RUNTIME_PENDING_RESTART.pop("job", None)
    # It would block on a real pause loop; a cancel wakes it, so the guard is
    # proven by the fact that it does not raise the restart signal first.
    runtime["cancel_requested"] = True
    with pytest.raises(RuntimeError, match="请求取消"):
        core._runtime_pause_checkpoint("job")


def test_worker_reenters_pipeline_with_new_connection(monkeypatch, tmp_path):
    """The live worker retries the pipeline with the new kwargs, not the old ones."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    old = _old_kwargs()
    new = _old_kwargs(provider="DeepSeek", api_key="sk-new", translator_base_url=None)
    state = {"p1_done": True, "p2_done": True, "report_enabled": False,
             "enable_annotate": False, "filename": "scan.pdf"}
    runtime = {"status": "running", "restart_requested": True, "attempt": 1,
               "worker_connection": core._connection_signature_from_kwargs(old)}
    monkeypatch.setattr(core, "load_job_state", lambda job: dict(state))
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))

    events = []

    def update(job, **changes):
        runtime.update(changes)
        events.append(dict(changes))
        return dict(runtime)

    monkeypatch.setattr(core, "update_runtime_state", update)
    for name in ("flush_runtime_status", "reset_runtime_status",
                 "flush_runtime_telemetry", "reset_runtime_telemetry"):
        monkeypatch.setattr(core, name, lambda job: None)
    monkeypatch.setattr(core, "_runtime_heartbeat_loop",
                        lambda *a, **k: None)
    monkeypatch.setattr(core, "_append_runtime_technical_log", lambda *a, **k: None)

    calls = []

    def fake_pipeline(job_id, filename, file_bytes, **kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            # The live worker is mid-run when Continue sets the pending request.
            with core._RUNTIME_PENDING_RESTART_LOCK:
                core._RUNTIME_PENDING_RESTART["job"] = {
                    "filename": "scan.pdf", "kwargs": dict(new), "base_url": None}
            raise core._WorkerRestartRequested("restart")
        return state

    monkeypatch.setattr(core, "run_job_pipeline", fake_pipeline)

    core._run_job_worker("job", "scan.pdf", b"%PDF", dict(old))

    assert len(calls) == 2, "worker should re-enter the pipeline once"
    assert calls[0]["provider"] == "自定义中转站"
    assert calls[1]["provider"] == "DeepSeek"
    assert calls[1]["api_key"] == "sk-new"
    assert any(e.get("event_name") == "connection_restart_applied" for e in events)
    assert core._RUNTIME_PENDING_RESTART == {}


def test_active_connection_view_reports_running_connection():
    state = {"provider": "DeepSeek", "model": "deepseek-v4.1-flash",
             "pipeline_config": {
                 "translator": {"provider": "DeepSeek",
                                "model": "deepseek-v4.1-flash",
                                "base_url": ""},
                 "reviewer": {"provider": "OpenAI", "model": "gpt-4o-mini",
                              "base_url": ""},
                 "reasoning_effort": "medium"}}
    view = core._active_connection_view(state)
    assert view["label"] == "DeepSeek · deepseek-v4.1-flash"
    assert view["reviewer_label"] == "OpenAI · gpt-4o-mini"
    assert view["roles"] == ["翻译", "审校"]
    assert core._active_connection_view({}) is None


def test_command_continue_resolves_current_connection(monkeypatch):
    from folith.application import commands

    state = core.new_job_state("scan.pdf")
    monkeypatch.setattr(core, "load_job_state", lambda job: dict(state))
    captured = {}

    def fake_continue(job_id, *, restart_kwargs=None, base_url=None, filename=None):
        captured.update(job_id=job_id, kwargs=restart_kwargs,
                        base_url=base_url, filename=filename)
        return True

    monkeypatch.setattr(core, "continue_job", fake_continue)
    ok = commands.continue_job("job", {
        "provider": "DeepSeek", "model": "deepseek-v4.1-flash",
        "api_key": "sk-new", "translator_base_url": None})
    assert ok is True
    assert captured["kwargs"]["provider"] == "DeepSeek"
    assert captured["kwargs"]["model"] == "deepseek-v4.1-flash"
    assert captured["filename"] == "scan.pdf"


def test_resume_job_passes_resolved_kwargs_to_continue(monkeypatch):
    runtime = {"status": "paused"}
    monkeypatch.setattr(core, "get_job_runtime_status", lambda job: dict(runtime))
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: True)
    captured = {}

    def fake_continue(job_id, *, restart_kwargs=None, base_url=None, filename=None):
        captured.update(job_id=job_id, kwargs=restart_kwargs,
                        base_url=base_url, filename=filename)
        return True

    monkeypatch.setattr(core, "continue_job", fake_continue)
    assert core.resume_job("job", "scan.pdf", _old_kwargs(provider="DeepSeek"),
                           base_url=None) is True
    assert captured["kwargs"]["provider"] == "DeepSeek"
    assert captured["filename"] == "scan.pdf"
