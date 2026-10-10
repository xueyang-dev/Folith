"""Use the real SDK with an offline HTTP transport to test hidden retries."""
import json
import httpx
import pytest
from openai import DefaultHttpxClient

import core
from folith import llm_transport, usage
from folith.trace import trace, TraceLevel


@pytest.mark.parametrize("failure", ["429", "connection"])
def test_sdk_attempts_join_one_logical_call(tmp_path, monkeypatch, failure):
    llm_transport.close_all_clients()
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "_runtime_job_id", lambda: "sdk-attempts")
    monkeypatch.setattr(core, "_runtime_pause_checkpoint", lambda *_: None)
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda *_: False)
    for name in ["_runtime_telemetry_request_started", "_runtime_telemetry_request_finished"]:
        monkeypatch.setattr(core, name, lambda *_: None)
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            if failure == "connection":
                raise httpx.ConnectError("controlled connection failure", request=request)
            return httpx.Response(429, headers={"retry-after-ms": "1"}, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "test-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}})

    monkeypatch.setattr(core, "DefaultHttpxClient",
        lambda **kwargs: DefaultHttpxClient(transport=httpx.MockTransport(handler), **kwargs))
    run = trace.start_run("sdk-attempts", output_dir=tmp_path / "trace", level=TraceLevel.STANDARD)
    try:
        invoke = usage.tracked_call(core.call_llm, usage.empty_usage(), role="translation")
        assert invoke("DeepSeek", "mock-secret", "test-model", "private system", "private source") == "OK"
    finally:
        trace.finish_run("sdk-attempts")
        llm_transport.close_all_clients()
    events = [json.loads(line) for line in run.requests_path.read_text().splitlines()]
    starts = [e for e in events if e["event"] == "http_attempt_started"]
    responses = [e for e in events if e["event"] == "http_response_headers"]
    calls = [e for e in events if e["event"] == "model_call"]
    assert [e["attempt"] for e in starts] == [1, 2]
    assert len(calls) == 1
    assert {e["logical_request_id"] for e in starts} == {calls[0]["logical_request_id"]}
    assert [e["status_code"] for e in responses] == ([429, 200] if failure == "429" else [200])
    assert calls[0]["prompt_tokens"] == 12
    assert calls[0]["metadata"]["usage_available"]
    assert calls[0]["http_attempts"] == 2
    assert "private source" not in run.requests_path.read_text()
    assert "mock-secret" not in run.requests_path.read_text()
