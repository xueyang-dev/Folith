import json
import threading

from folith.trace import RunContext, TraceLevel


def test_request_stream_reconstructs_overlap_and_failed_call(tmp_path):
    run = RunContext("requests", output_dir=tmp_path, level=TraceLevel.STANDARD)
    barrier = threading.Barrier(2)

    def request(batch):
        run.begin_model_request("provider", "model", role="annotation", batch_id=batch)
        barrier.wait(timeout=2)
        run.record_model_call("provider", "model", duration_s=0.01,
                              status="error" if batch == 2 else "success", prompt_tokens=12)
        run.end_model_request()

    threads = [threading.Thread(target=request, args=(batch,)) for batch in [1, 2]]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    summary = run.finish(status="failed")
    events = [json.loads(line) for line in run.requests_path.read_text().splitlines()]
    starts = [e for e in events if e["event"] == "request_started"]
    finishes = [e for e in events if e["event"] == "request_finished"]
    calls = [e for e in events if e["event"] == "model_call"]
    assert len(starts) == len(finishes) == len(calls) == summary["model_metrics"]["total_calls"] == 2
    assert max(e["job_in_flight"] for e in starts) == 2
    assert {e["logical_request_id"] for e in starts} == {e["logical_request_id"] for e in finishes}
    assert sum(e["prompt_tokens"] for e in calls) == summary["model_metrics"]["total_prompt_tokens"]
    assert {e["role"] for e in calls} == {"annotation"}
    assert all(e["started_monotonic"] <= e["finished_monotonic"] for e in finishes)


def test_call_failure_closes_request_and_restores_role(tmp_path, monkeypatch):
    import pytest
    import core
    from folith import usage

    run = RunContext("failure", output_dir=tmp_path, level=TraceLevel.STANDARD)
    monkeypatch.setattr(core, "_runtime_job_id", lambda: None)
    monkeypatch.setattr(core.trace, "get_active_run", lambda _job=None: run)

    def fail(*_args, **_kwargs):
        raise RuntimeError("controlled transport failure")

    monkeypatch.setattr(core, "_call_llm_transport", fail)
    invoke = usage.tracked_call(core.call_llm, usage.empty_usage(), role="semantic_review")
    with pytest.raises(RuntimeError, match="controlled"):
        invoke("DeepSeek", "secret", "model", "system", "source")
    assert usage.CURRENT_ROLE.get() == "unknown"
    run.record_retry("translation", "translate_batch", 2, 1.5, "RuntimeError")
    run.finish(status="failed")
    events = [json.loads(line) for line in run.requests_path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["request_started", "model_call", "request_finished", "retry", "run_finished"]
    assert events[1]["status"] == "error"
    assert events[1]["role"] == "semantic_review"
    assert events[2]["job_in_flight"] == 0
    assert "secret" not in run.requests_path.read_text()
