"""Use the real SDK against a local HTTP endpoint to test hidden retries."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler
import pytest

import core
from scripts.local_http_server import LocalThreadingHTTPServer as ThreadingHTTPServer
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

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            attempts.append(self.path)
            if len(attempts) == 1 and failure == "connection":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status = 429 if len(attempts) == 1 else 200
            body = ({"error": {"message": "rate limited"}} if status == 429 else {
                "id": "test", "object": "chat.completion", "created": 0,
                "model": "test-model", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": "OK"},
                "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14}})
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if status == 429:
                self.send_header("retry-after-ms", "1")
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f"http://127.0.0.1:{server.server_port}/v1"
    run = trace.start_run("sdk-attempts", output_dir=tmp_path / "trace", level=TraceLevel.STANDARD)
    try:
        invoke = usage.tracked_call(core.call_llm, usage.empty_usage(), role="translation")
        assert invoke("DeepSeek", "mock-secret", "test-model", "private system", "private source",
                      base_url=base_url) == "OK"
    finally:
        trace.finish_run("sdk-attempts")
        llm_transport.close_all_clients()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    events = [json.loads(line) for line in run.requests_path.read_text(encoding="utf-8").splitlines()]
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
    assert "private source" not in run.requests_path.read_text(encoding="utf-8")
    assert "mock-secret" not in run.requests_path.read_text(encoding="utf-8")
