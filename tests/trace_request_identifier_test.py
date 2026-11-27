"""CX-20 §4：``requests.jsonl`` 里的远端请求 ID 只收标识符形状。

成功响应的 id 直接从 SDK 对象写到性能追踪文件，没有经过错误诊断那套标识符校验。
供应商返回的自由文本（URL、带 ``user:pass@`` 或 ``?key=`` 的串）会原样落盘。
测试钉住：合法标识符保留，非标识符形状一律不写入，且原值不出现在任何一行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith.trace import RunContext, TraceLevel, safe_request_identifier  # noqa: E402


def _records(run: RunContext) -> list:
    path = run.requests_path
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_identifier_shapes_are_kept():
    for value in ("req_abc123", "chatcmpl-9xY", "2024.req:17", "1234"):
        assert safe_request_identifier(value) == value, value


def test_free_text_and_credential_shapes_are_dropped():
    for value in ("https://user:pass@relay.example/v1?key=synthetic",
                  "req abc", "Bearer sk-synthetic-token", "", "   ",
                  None, True, False):
        assert safe_request_identifier(value) is None, value


def test_an_over_long_value_is_not_an_identifier():
    assert safe_request_identifier("a" * 65) is None
    assert safe_request_identifier("a" * 64) == "a" * 64


def test_the_timing_record_keeps_a_real_identifier(tmp_path):
    run = RunContext("trace-id-kept", output_dir=tmp_path, level=TraceLevel.STANDARD)
    run.record_model_call(provider="openai", model="stub-model", duration_s=0.5,
                          request_id="req_abc-123")
    run.finish()
    records = [rec for rec in _records(run) if rec.get("event") == "model_call"]
    assert records and records[-1]["request_id"] == "req_abc-123"


def test_a_credential_bearing_id_never_reaches_the_file(tmp_path):
    marker = "synthetic-url-key"
    run = RunContext("trace-id-dropped", output_dir=tmp_path, level=TraceLevel.STANDARD)
    run.record_model_call(
        provider="自定义中转站", model="stub-model", duration_s=0.5,
        request_id=f"https://user:pass@relay.example/v1?key={marker}")
    run.finish()
    text = run.requests_path.read_text(encoding="utf-8")
    records = [rec for rec in _records(run) if rec.get("event") == "model_call"]
    assert records and records[-1]["request_id"] is None
    for leaked in (marker, "user:pass", "relay.example"):
        assert leaked not in text, leaked


def test_error_diagnostics_apply_the_same_rule():
    # The same helper names the provider code in error diagnostics, so a relay
    # cannot put a URL into the status dict either.
    from folith.engine.llm_provider import provider_error_status

    class _ProviderError(Exception):
        pass

    error = _ProviderError("rate limited")
    error.status_code = 429
    error.body = {"error": {"code": "https://user:pass@relay.example/v1?key=synthetic",
                            "type": "rate_limit_error"}}

    status = provider_error_status(error)
    assert status["provider_code"] == ""
    dump = json.dumps(status, ensure_ascii=False)
    assert "user:pass" not in dump and "synthetic" not in dump


def test_a_plain_provider_code_still_comes_through():
    from folith.engine.llm_provider import provider_error_status

    class _ProviderError(Exception):
        pass

    error = _ProviderError("rate limited")
    error.status_code = 429
    error.body = {"error": {"code": "rate_limit_exceeded"}}

    assert provider_error_status(error)["provider_code"] == "rate_limit_exceeded"
