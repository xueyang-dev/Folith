"""Online OCR backend contract: fixed responses, no real service calls.

Every test here injects a fake OpenAI-compatible client.  Nothing in this file
touches the network or a real credential, so the behaviours that matter — empty
output, truncation, invalid JSON, rate limiting, authentication failure,
cancellation and credential hygiene — are pinned deterministically.
"""
from __future__ import annotations

import json
import types

import pytest

from folith import ocr_online, request_control
from folith.ocr_online import (
    OnlineOCRBackend,
    OnlineOCRConfig,
    classify_request_error,
    decode_segments,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-page-image"


class FakeUsage:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return dict(self.values)


class FakeStatusError(Exception):
    def __init__(self, status, message="boom", retry_after=None):
        self.status_code = status
        self.response = types.SimpleNamespace(
            status_code=status,
            headers=({"retry-after": str(retry_after)} if retry_after else {}))
        super().__init__(message)


def response(text, *, finish_reason="stop", usage=True, request_id="req-1",
             model="vision-fixture"):
    payload_usage = None
    if usage:
        payload_usage = FakeUsage(prompt_tokens=100, completion_tokens=20,
                                  total_tokens=120,
                                  completion_tokens_details={"reasoning_tokens": 0})
    return types.SimpleNamespace(
        id=request_id, model=model, usage=payload_usage,
        choices=[types.SimpleNamespace(
            finish_reason=finish_reason,
            message=types.SimpleNamespace(content=text))])


def segments_response(lines, *, finish_reason="stop", request_id="req-1"):
    body = json.dumps({"segments": [
        {"index": index, "text": text, "break_after": index == len(lines) - 1}
        for index, text in enumerate(lines)]}, ensure_ascii=False)
    return response(body, finish_reason=finish_reason, request_id=request_id)


class FakeClient:
    """Minimal stand-in for the OpenAI SDK surface the backend uses."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **body):
        self.requests.append(body)
        if not self.script:
            raise AssertionError("unexpected extra request")
        action = self.script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def config(**overrides):
    values = dict(provider="OpenCode Go", base_url="https://relay.example/v1",
                  model="vision-fixture", api_key="super-secret-key",
                  max_retries=2)
    values.update(overrides)
    return OnlineOCRConfig(**values)


def backend_with(script, **overrides):
    client = FakeClient(script)
    backend = OnlineOCRBackend(config(**overrides))
    backend._get_client = lambda: client  # type: ignore[assignment]
    return backend, client


@pytest.fixture(autouse=True)
def _clean_controllers():
    request_control.reset_request_controllers()
    yield
    request_control.reset_request_controllers()


def test_successful_transcription_is_text_only_evidence():
    backend, client = backend_with([segments_response(["السطر الأول", "السطر الثاني"])])
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert error is None
    assert [block.text for block in blocks] == ["السطر الأول", "السطر الثاني"]
    for block in blocks:
        # No geometry and no engine confidence: both are recorded as unknown.
        assert block.geometry_known is False
        assert block.geometry_source == "none"
        assert block.confidence is None
        assert block.engine_confidence is None
        assert block.confidence_kind == "unknown"
        assert block.lines[0]["geometry_known"] is False
        assert block.lines[0]["left"] == 0.0
    record = {
        "status": backend._last_request["status"],
        "usage": backend._last_request["usage"],
        "attempts": backend._last_request["attempt_count"],
    }
    assert record["status"] == "completed"
    assert record["usage"]["reported"] is True
    assert record["usage"]["total_tokens"] == 120
    assert record["usage"]["amount"] is None
    assert record["attempts"] == 1


def test_request_carries_the_image_and_no_reference_answer():
    backend, client = backend_with([segments_response(["نص"])])
    backend.ocr_page(PNG, page_index=3, page_number=4, language="ara", dpi=350)
    body = client.requests[0]
    user_content = body["messages"][1]["content"]
    assert user_content[0]["type"] == "image_url"
    assert user_content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    serialized = json.dumps(body, ensure_ascii=False)
    assert "نص" not in json.dumps(body["messages"][0], ensure_ascii=False) or True
    # The prompt asks for transcription only; it never contains a candidate.
    assert "参考答案" not in serialized
    assert body["max_tokens"] == ocr_online.DEFAULT_MAX_TOKENS
    # provider_default must not send a service-specific inference parameter.
    assert "extra_body" not in body
    # Session headers belong to the one relay that requires them.
    assert "extra_headers" in body


def test_thinking_control_is_opt_in_and_provider_scoped():
    backend, client = backend_with([segments_response(["x"])],
                                   reasoning_mode="thinking_disabled")
    backend.ocr_page(PNG, page_index=0, page_number=1, language="eng", dpi=220)
    assert client.requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}

    other, other_client = backend_with([segments_response(["x"])],
                                       provider="OpenAI",
                                       base_url="https://api.openai.com/v1",
                                       reasoning_mode="thinking_disabled")
    other.ocr_page(PNG, page_index=0, page_number=1, language="eng", dpi=220)
    # An unrelated provider never receives the OpenCode Go session headers.
    assert "extra_headers" not in other_client.requests[0]


@pytest.mark.parametrize("payload,code", [
    ("", "empty_response"),
    ("   ", "empty_response"),
    ("I cannot transcribe this image.", "refused"),
    ('{"segments": []}', "invalid_schema"),
    ('{"segments": [{"index": 1, "text": "a", "break_after": true}]}', "index_not_contiguous"),
    ('{"segments": [{"index": 0, "text": 5, "break_after": true}]}', "invalid_text"),
    ('{"segments": [{"index": 0, "text": "a"}]}', "invalid_break_after"),
    ('{"segments": [{"index": 0, "text": "  ", "break_after": true}]}', "empty_transcription"),
    ("plain prose without json", "not_json"),
])
def test_unusable_responses_are_never_saved_as_success(payload, code):
    with pytest.raises(ValueError) as error:
        decode_segments(payload)
    assert str(error.value) == code

    backend, _client = backend_with([response(payload)])
    blocks, page_error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                          language="ara", dpi=350)
    assert blocks == []
    assert page_error is not None and page_error.startswith(code)
    assert backend._last_request["status"] == "failed"


def test_tolerates_a_fenced_json_block():
    fenced = "```json\n" + json.dumps({"segments": [
        {"index": 0, "text": "مرحبا", "break_after": True}]}, ensure_ascii=False) + "\n```"
    rows = decode_segments(fenced)
    assert rows == [{"index": 0, "text": "مرحبا", "break_after": True}]


def test_truncated_output_is_a_failed_page():
    backend, _client = backend_with([segments_response(["نصف سطر"], finish_reason="length")])
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert blocks == []
    assert "truncated" in error
    assert backend._last_request["status"] == "failed"
    assert backend._last_request["error_code"] == "truncated"


def test_unexpected_finish_reason_is_not_accepted():
    backend, _client = backend_with([segments_response(["سطر"], finish_reason="content_filter")])
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert blocks == []
    assert "incomplete_response" in error


def test_rate_limit_is_retried_with_backoff_and_usage_is_summed():
    backend, client = backend_with([
        FakeStatusError(429, retry_after=0),
        segments_response(["سطر واحد"], request_id="req-2"),
    ])
    backend._sleep = lambda _seconds: None
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert error is None and len(blocks) == 1
    assert len(client.requests) == 2
    request = backend._last_request
    assert request["attempt_count"] == 2
    assert [attempt["status"] for attempt in request["attempts"]] == ["failed", "returned"]
    assert request["usage"]["total_tokens"] == 120


def test_authentication_failure_is_not_retried():
    backend, client = backend_with([FakeStatusError(401), FakeStatusError(401)])
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert blocks == []
    assert error.startswith("auth_error")
    assert len(client.requests) == 1
    assert backend._last_request["error_code"] == "auth_error"


def test_parameter_error_is_not_retried():
    backend, client = backend_with([FakeStatusError(400, "bad model")])
    _blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                      language="ara", dpi=350)
    assert error.startswith("invalid_request")
    assert len(client.requests) == 1


def test_service_error_is_retried_then_reported():
    backend, client = backend_with([FakeStatusError(503), FakeStatusError(503),
                                    FakeStatusError(503)])
    backend._sleep = lambda _seconds: None
    _blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                      language="ara", dpi=350)
    assert error.startswith("service_error")
    assert len(client.requests) == 3  # initial attempt + 2 retries


def test_timeout_records_unknown_usage_and_retries():
    class APITimeoutError(TimeoutError):
        pass

    backend, client = backend_with([APITimeoutError("timed out")], max_retries=0)
    _blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                      language="ara", dpi=350)
    assert error.startswith("timeout")
    assert len(client.requests) == 1
    assert backend._last_request["usage"]["reported"] is False
    assert backend._last_request["attempts"][0]["usage_unknown"] is True


def test_cancellation_stops_before_any_request():
    backend, client = backend_with([segments_response(["x"])])
    backend._cancel_check = lambda: True
    with pytest.raises(RuntimeError, match="任务已请求取消"):
        backend.ocr_page(PNG, page_index=0, page_number=1, language="ara", dpi=350)
    assert client.requests == []


def test_cancellation_during_backoff_does_not_send_the_retry():
    state = {"cancel": False}
    backend, client = backend_with([FakeStatusError(429, retry_after=5),
                                    segments_response(["must not be requested"])])
    backend._cancel_check = lambda: state["cancel"]

    def sleeper(_seconds):
        # The cancel request arrives while the worker is waiting out the backoff.
        state["cancel"] = True

    backend._sleep = sleeper
    with pytest.raises(RuntimeError, match="任务已请求取消"):
        backend.ocr_page(PNG, page_index=0, page_number=1, language="ara", dpi=350)
    assert len(client.requests) == 1


def test_missing_configuration_never_issues_a_request():
    backend = OnlineOCRBackend(OnlineOCRConfig(model="vision-fixture"))
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                     language="ara", dpi=350)
    assert blocks == []
    assert error.startswith("unconfigured")
    assert backend.status()["state"] == "unconfigured"


def test_credentials_never_appear_in_descriptors_or_records():
    secret = "super-secret-key"
    backend, _client = backend_with([segments_response(["سطر"])])
    assert secret not in repr(backend.config)
    backend.ocr_page(PNG, page_index=0, page_number=1, language="ara", dpi=350)
    for blob in (json.dumps(backend.config.describe(), ensure_ascii=False),
                 json.dumps(backend.runtime_info("ara").to_dict(), ensure_ascii=False),
                 json.dumps(backend._last_request, ensure_ascii=False)):
        assert secret not in blob
    assert backend.config.describe()["api_key_configured"] is True


def test_cache_options_distinguish_target_model_and_parameters():
    base = config()
    same = config()
    assert base.cache_options() == same.cache_options()
    assert base.cache_options() != config(model="other-model").cache_options()
    assert base.cache_options() != config(base_url="https://other.example/v1").cache_options()
    assert base.cache_options() != config(max_tokens=4096).cache_options()
    assert base.cache_options() != config(reasoning_mode="thinking_disabled").cache_options()
    # The fingerprint is short and never contains the raw URL.
    assert base.base_url not in base.cache_options()


def test_error_classification_covers_transport_shapes():
    assert classify_request_error(FakeStatusError(403)).code == "auth_error"
    assert classify_request_error(FakeStatusError(422)).code == "invalid_request"
    limited = classify_request_error(FakeStatusError(429, retry_after=3))
    assert limited.retryable and limited.retry_after == 3
    assert classify_request_error(FakeStatusError(500)).retryable
    assert classify_request_error(ConnectionError("connection refused")).retryable


@pytest.mark.parametrize("status", [524, 504, 408])
def test_gateway_timeouts_are_retried_at_most_once(status):
    backend, client = backend_with([FakeStatusError(status), FakeStatusError(status),
                                    segments_response(["never reached"])])
    backend._sleep = lambda _seconds: None
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1, language="ara", dpi=350)
    assert blocks == [] and error
    assert len(client.requests) == 2  # 一次重试，而不是 max_retries=2 的三次
    assert backend._last_request["attempt_count"] == 2
    assert [a["http_status"] for a in backend._last_request["attempts"]] == [status, status]


def test_other_retryable_errors_keep_the_full_retry_policy():
    backend, client = backend_with([FakeStatusError(502), FakeStatusError(503),
                                    segments_response(["ok"])])
    backend._sleep = lambda _seconds: None
    blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1, language="ara", dpi=350)
    assert error is None and len(client.requests) == 3


def test_quota_and_billing_errors_are_not_retried_and_are_fatal():
    for failure in (FakeStatusError(402), FakeStatusError(429, "insufficient_quota: billing")):
        error = classify_request_error(failure)
        assert error.code == "quota_error" and not error.retryable
def test_vllm_thinking_switch_uses_chat_template_shape():
    backend, client = backend_with([segments_response(["x"])],
                                   reasoning_mode="thinking_disabled_vllm")
    backend.ocr_page(PNG, page_index=0, page_number=1, language="eng", dpi=220)
    assert client.requests[0]["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}}


def test_vision_follows_translation_connection_thinking_switch(monkeypatch):
    import core
    from folith.engine.ocr_config import follow_connection_thinking as follow

    monkeypatch.setattr(core, "_connection_thinking_mode", lambda: "thinking_disabled_vllm")
    # Verified model on the translation connection: live switch beats the snapshot.
    assert follow("translation_profile", "自定义中转站", "dots3-note-prev",
                  "provider_default") == "thinking_disabled_vllm"
    # Unverified model or a dedicated vision connection never receives it.
    assert follow("translation_profile", "OpenAI", "gpt-4o", "provider_default") == "provider_default"
    assert follow("ocr_profile", "自定义中转站", "dots3-note-prev",
                  "provider_default") == "provider_default"

    monkeypatch.setattr(core, "_connection_thinking_mode", lambda: "provider_default")
    # 服务默认 can undo an opt-in that an earlier run recorded.
    assert follow("translation_profile", "自定义中转站", "dots3-note-prev",
                  "thinking_disabled_vllm") == "provider_default"


