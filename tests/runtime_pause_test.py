import threading
import time
import core


def test_pause_waits_between_operations_and_continues(monkeypatch):
    runtime = {"status": "running", "phase": "translation", "pause_requested": False}
    events = []
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: True)
    def update(job, **changes):
        runtime.update(changes)
        events.append(changes)
    monkeypatch.setattr(core, "update_runtime_state", update)
    assert core.request_job_pause("job")
    returned = threading.Event()
    worker = threading.Thread(target=lambda: (core._runtime_pause_checkpoint("job"), returned.set()))
    worker.start()
    deadline = time.monotonic() + 2
    while runtime["status"] != "paused" and time.monotonic() < deadline:
        time.sleep(.01)
    assert runtime["status"] == "paused"
    assert not returned.is_set()
    assert core.continue_job("job")
    worker.join(2)
    assert returned.is_set()
    assert runtime["status"] == "running"
    assert any(e.get("event_name") == "job_continued" for e in events)


def test_cancel_wakes_paused_operation(monkeypatch):
    runtime = {"pause_requested": True, "cancel_requested": True}
    monkeypatch.setattr(core, "load_runtime_state", lambda job: dict(runtime))
    monkeypatch.setattr(core, "update_runtime_state", lambda job, **changes: runtime.update(changes))
    import pytest
    with pytest.raises(RuntimeError, match="请求取消"):
        core._runtime_pause_checkpoint("job")


def test_pause_does_not_claim_success_without_worker(monkeypatch):
    monkeypatch.setattr(core, "is_job_worker_alive", lambda job: False)
    assert not core.request_job_pause("job")
    assert not core.continue_job("job")


def test_annotate_stage_cooperative_pause(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("annot_pause.docx")
    state.update(pairs=[{"source": f"Sentence {i}", "target": f"句子 {i}"} for i in range(25)],
                 p1_done=True, p2_done=True)
    paused_seen = []
    original_checkpoint = core._runtime_pause_checkpoint

    def checkpoint(job_id):
        if job_id == "annot_pause_job":
            paused_seen.append(True)
        return original_checkpoint(job_id)

    monkeypatch.setattr(core, "_runtime_pause_checkpoint", checkpoint)
    monkeypatch.setattr(core, "annotate_batch", lambda *args, **kwargs: [])
    core.annotate_stage(state, "annot_pause_job", [], "DeepSeek", "k", "model", "简体中文")
    assert len(paused_seen) > 0, "annotate_stage should check pause checkpoint on batches"
