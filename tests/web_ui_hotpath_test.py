"""UI cost and background segment-operation regressions, without provider calls."""
import json
import threading

import pytest

import core
from folith.storage import job_repository


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_repository.invalidate_cache()
    yield tmp_path
    for worker in list(core._RUNTIME_WORKERS.values()):
        worker.join(timeout=3)
    job_repository.invalidate_cache()


def _state():
    state = core.new_job_state("hotpath.docx")
    state.update(p1_done=True, p2_done=True, report_enabled=False)
    state["pairs"] = [{"source": f"Source {i}.", "target": f"译文 {i}。"}
                      for i in range(100)]
    state["paras"] = [pair["source"] for pair in state["pairs"]]
    return state


def test_snapshot_preserves_explicit_empty_translation_and_new_structure(job_env):
    job_id = "authoritative-snapshot"
    state = _state()
    core.save_job_state(job_id, state)
    state["pairs"][0]["target"] = ""
    core.save_job_state(job_id, state)
    assert core.load_job_state(job_id)["pairs"][0]["target"] == ""
    result = core.mutate_translation_segments(
        job_id, 1, "split", source_a="First half", source_b="Second half",
        target_a="", target_b="")
    assert result
    pairs = core.load_job_state(job_id)["pairs"]
    assert [pair["source"] for pair in pairs[1:3]] == ["First half", "Second half"]
    assert all(pair["target"] == "" for pair in pairs[1:3])
    identities = [pair["segment_uid"] for pair in pairs]
    assert len(identities) == len(set(identities))


def test_runtime_tail_filters_before_limiting_and_preserves_order(job_env, monkeypatch):
    path = core.runtime_events_path("event-tail")
    path.parent.mkdir()
    records = [{"event": "llm_response_received", "message": f"译文 {i}"}
               for i in range(1000)]
    path.write_text("\r\n".join(json.dumps(row, ensure_ascii=False) for row in records)
                    + '\ninvalid json\n' + json.dumps({"event": "state_flushed"}),
                    encoding="utf-8")
    expected = core.read_runtime_events("event-tail", visibility="user")[-5:]
    original = core.json.loads
    parsed = 0

    def parse(*args, **kwargs):
        nonlocal parsed
        parsed += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(core.json, "loads", parse)
    assert core.read_runtime_events("event-tail", 5, visibility="user") == expected
    assert parsed == 7  # five matches, one technical record, one malformed line


def test_runtime_tail_handles_lines_larger_than_read_buffer(job_env):
    path = core.runtime_events_path("long-tail")
    path.parent.mkdir()
    records = [{"event": "llm_response_received", "message": "译" * 12000},
               {"event": "job_completed", "message": "完成"}]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records),
                    encoding="utf-8")
    assert core.read_runtime_events("long-tail", 2) == core.read_runtime_events("long-tail")


@pytest.mark.parametrize("kind", ["review", "retranslate"])
def test_segment_worker_is_nonblocking_and_owns_snapshot_writes(job_env, monkeypatch, kind):
    job_id = f"segment-{kind}"
    core.save_job_state(job_id, _state())
    entered, release = threading.Event(), threading.Event()
    captured = {}

    def operation(job_id, indexes, **kwargs):
        captured.update(kwargs)
        captured["thread_url"] = core._LLM_CTX.base_url
        captured["thread_job_id"] = core._runtime_job_id()
        entered.set()
        assert release.wait(3)
        state = core.load_job_state(job_id)
        state["pairs"][indexes[0]]["target"] = "后台结果"
        core.save_job_state(job_id, state)
        return (state, {"failed_segment_ids": [], "reviewed_segment_ids": indexes}) \
            if kind == "review" else (state, indexes)

    monkeypatch.setattr(core, "review_translation_segments" if kind == "review"
                        else "retranslate_segments", operation)
    try:
        assert core.start_segment_worker(
            job_id, kind, [0], provider="DeepSeek", api_key="private-test-key",
            model="deepseek-chat", target_lang="简体中文", base_url="https://relay.example/v1")
        assert entered.wait(1)
        assert not core.start_segment_worker(job_id, kind, [0])
        assert core.read_runtime_view(job_id)["status"] == "running"
        assert captured["thread_job_id"] == job_id
        assert captured["thread_url"] == "https://relay.example/v1"
        if kind == "review":
            assert captured["base_url"] == "https://relay.example/v1"
        with pytest.raises(RuntimeError, match="后台处理"):
            core.save_translation_edit(job_id, 1, "并发修改")
        assert "private-test-key" not in core.runtime_state_path(job_id).read_text(
            encoding="utf-8")
    finally:
        worker = core._RUNTIME_WORKERS.get(job_id)
        release.set()
        if worker:
            worker.join(3)
    assert not core.is_job_worker_alive(job_id)
    assert core.load_runtime_state(job_id)["segment_operation_result"] == "completed"
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "后台结果"
    assert core.load_job_state(job_id)["pairs"][1]["target"] != "并发修改"


@pytest.mark.parametrize("failed_review", [False, True])
def test_segment_worker_reports_provider_and_review_failures(job_env, monkeypatch, failed_review):
    job_id = "segment-failure"
    core.save_job_state(job_id, _state())

    def review(*args, **kwargs):
        if failed_review:
            return core.load_job_state(job_id), {"failed_segment_ids": [0]}
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(core, "review_translation_segments", review)
    core.start_segment_worker(job_id, "review", [0])
    worker = core._RUNTIME_WORKERS.get(job_id)
    if worker:
        worker.join(3)
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "failed"
    assert runtime["segment_operation_result"] != "completed"
    assert runtime["error"]["message"]


def test_segment_worker_cancel_does_not_claim_success(job_env, monkeypatch):
    job_id = "segment-cancel"
    core.save_job_state(job_id, _state())
    entered, release = threading.Event(), threading.Event()

    def review(*args, **kwargs):
        entered.set()
        release.wait(3)
        return core.load_job_state(job_id), {"failed_segment_ids": []}

    monkeypatch.setattr(core, "review_translation_segments", review)
    core.start_segment_worker(job_id, "review", [0])
    worker = core._RUNTIME_WORKERS.get(job_id)
    try:
        assert entered.wait(1)
        assert core.request_job_cancel(job_id)
    finally:
        release.set()
        if worker:
            worker.join(3)
    assert core.load_runtime_state(job_id)["status"] == "cancelled"
    assert core.load_runtime_state(job_id)["segment_operation_result"] != "completed"


