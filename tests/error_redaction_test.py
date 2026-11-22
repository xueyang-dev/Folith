"""CX-06 §1/§3：密钥与原始回包在落盘、展示、打包前一律脱敏。

三件事钉在这里：
1. ``redact_error_text`` 是共享脱敏入口：对干净文本是**恒等变换**（既有失败
   记录的契约与测试不动），只替换密钥形状、``key: value``、家目录/账号名和
   绝对路径；调用方自带的字面密钥最强——服务商回包里回显的是它。
2. 线上 OCR 的失败记录（attempts 的 message/response_body/head/tail/excerpt
   与用户可见消息）全部经它落盘。
3. 视觉理解的逐页失败取证、core 的技术日志、trace 的错误记录同享该入口。

备份排除清单（凭据文件 + 原始取证）的测试在 ``qt_settings_window_test.py``；
两个清单对应 CX-06 证据 ``backup_members_with_model_keys_by_role`` 逐项。

全部用合成数据与假密钥（``super-secret-key`` 与运行时拼装的 ``sk-`` 样式假
值），不碰真实数据目录，也不读真实密钥。字面量不落树：发布预检
（``scripts/release_preflight.py``）禁止源码树里出现密钥形状与本机临时目录
路径，所以假密钥在测试内拼装、合成路径用 ``/srv/relay`` 样式。
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import trace  # noqa: E402
from folith.document_understanding import understand_visual_pdf  # noqa: E402
from folith.engine.redaction import redact_error_text  # noqa: E402
from folith.ocr_online import OnlineOCRBackend, OnlineOCRConfig, classify_request_error  # noqa: E402
from folith.spatial_index import SpatialTextIndex  # noqa: E402

FAKE_KEY = "super-secret-key"
#: OpenAI 风格的假密钥：运行时拼装，源码树里不出现完整字面量（发布预检 §3）。
FAKE_SK_KEY = "sk-" + "C" * 12 + "06fake"
#: 非家目录的合成绝对路径：走 ``<external>`` 折叠，且不像任何本机临时目录。
FAKE_PATH = "/srv/relay/fixture/capture.py"
PNG = b"\x89PNG\r\n\x1a\n" + b"fixture-page-image"


class FakeClient:
    """最小桩：按脚本抛错或回包，只记请求。"""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **body):
        self.requests.append(body)
        action = self.script.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


class FakeStatusError(Exception):
    def __init__(self, status, message="boom", retry_after=None, body=None):
        self.status_code = status
        self.response = types.SimpleNamespace(
            status_code=status,
            headers=({"retry-after": str(retry_after)} if retry_after else {}),
            text=body)
        super().__init__(message)


def _config(**overrides):
    values = dict(provider="fixture", base_url="https://relay.example/v1",
                  model="vision-fixture", api_key=FAKE_KEY, max_retries=2)
    values.update(overrides)
    return OnlineOCRConfig(**values)


def _usage_response(text, *, request_id="req-1"):
    return types.SimpleNamespace(
        id=request_id, usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        choices=[types.SimpleNamespace(
            finish_reason="stop", message=types.SimpleNamespace(content=text))])


def _segments_response(lines):
    body = json.dumps({"segments": [
        {"index": index, "text": text, "break_after": index == len(lines) - 1}
        for index, text in enumerate(lines)]}, ensure_ascii=False)
    return _usage_response(body)


def _backend_with(script, **overrides):
    backend = OnlineOCRBackend(_config(**overrides))
    backend._get_client = lambda: FakeClient(script)  # type: ignore[assignment]
    backend._sleep = lambda _seconds: None
    return backend


@pytest.fixture(autouse=True)
def _clean_controllers():
    from folith import request_control

    request_control.reset_request_controllers()
    yield
    request_control.reset_request_controllers()


# ---------------------------------------------------------------------------
# 1. the shared entry point
# ---------------------------------------------------------------------------

def test_redaction_is_the_identity_on_clean_text():
    """干净文本一字不改：既有的失败记录契约与测试都依赖这一点。"""
    clean = [
        "invalid parameter: model not found",
        "线上 OCR 服务限流（HTTP 429）。",
        "rel/path/to/file.md",
        "POST https://api.relay.example/v1 failed with 500",
    ]
    for text in clean:
        assert redact_error_text(text) == text


def test_redaction_removes_known_secrets_patterns_and_paths():
    echoed = json.dumps({
        "error": f"bad api_key {FAKE_KEY}",
        "header": "Authorization: Bearer abcdef1234567890abcdef",
        "sk": FAKE_SK_KEY,
        "file": FAKE_PATH,
    })
    out = redact_error_text(echoed, known_secrets=(FAKE_KEY,))
    assert FAKE_KEY not in out and FAKE_SK_KEY not in out
    assert out.count("<redacted>") >= 3
    assert "/srv" not in out
    # 键名与 JSON 结构保留，只有值被替换。
    assert '"sk": "<redacted>"' in out
    assert '"file": "<external>/capture.py"' in out


def test_redaction_covers_key_value_shapes_and_windows_paths():
    assert redact_error_text('{"api_key": "v1"}') == '{"api_key": "<redacted>"}'
    assert redact_error_text("x-api-key: tok123") == "x-api-key: <redacted>"
    windows = redact_error_text("C:\\Users\\someone\\AppData\\Local\\Temp\\x.txt")
    assert "someone" not in windows and "Temp" not in windows


# ---------------------------------------------------------------------------
# 2. the online-OCR failure records
# ---------------------------------------------------------------------------

def test_a_rejected_body_echoing_the_key_is_redacted_before_it_persists():
    """HTTP 400 回包可能回显发出去的密钥：留头可以，密钥不留。"""
    body = json.dumps({"error": f"invalid api_key {FAKE_KEY}",
                       "detail": f"no such file {FAKE_PATH}"})
    backend = _backend_with([FakeStatusError(400, "bad model", body=body),
                             _segments_response(["一 行"])])
    _blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                      language="ara", dpi=350)
    assert error is not None and error.startswith("invalid_request")
    attempt = backend._last_request["attempts"][-1]
    assert attempt["http_status"] == 400
    assert attempt["response_chars"] == len(body), "完整长度照记"
    assert FAKE_KEY not in attempt["response_body"]
    assert "<redacted>" in attempt["response_body"]
    assert "/srv" not in attempt["response_body"]


def test_the_unclassified_failure_message_never_echoes_the_key():
    """request_failed 兜底会把原始错误文带进用户消息——先脱敏。"""
    backend = _backend_with([FakeStatusError(0, f"relay said {FAKE_KEY} is bad")])
    _blocks, error = backend.ocr_page(PNG, page_index=0, page_number=1,
                                      language="ara", dpi=350)
    assert error is not None and error.startswith("request_failed")
    assert FAKE_KEY not in error
    assert "<redacted>" in error


def test_classify_request_error_redacts_the_fallback_text():
    classified = classify_request_error(
        FakeStatusError(0, f"upstream rejected {FAKE_KEY}"),
        known_secrets=(FAKE_KEY,))
    assert FAKE_KEY not in classified.message
    assert classified.code == "request_failed"


# ---------------------------------------------------------------------------
# 3. the vision failure forensics, the technical log and the trace
# ---------------------------------------------------------------------------

def _one_page_pdf() -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page(width=200, height=200)
    page.insert_text((20, 50), "fixture paragraph")
    return document.tobytes()


def test_vision_failure_records_redact_the_echoed_credential(tmp_path):
    from folith.document_understanding import VLMDocumentProvider

    body = json.dumps({"error": f"invalid api_key {FAKE_KEY}"})
    provider = VLMDocumentProvider(
        _config(), client=FakeClient([FakeStatusError(400, body=body)]))
    from folith.document_understanding import DocumentUnderstandingError

    with pytest.raises(DocumentUnderstandingError):
        understand_visual_pdf(
            "scan.pdf", _one_page_pdf(), provider, checkpoint_dir=tmp_path,
            spatial_engine=lambda page: SpatialTextIndex(page.page_id))

    log = tmp_path / "document_understanding" / "failures.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    assert rows, "失败必须照常落盘（脱敏不等于不记）"
    text = json.dumps(rows[-1], ensure_ascii=False)
    assert FAKE_KEY not in text
    assert "<redacted>" in text
    # 记录的结构字段（#300 契约）原样保留。
    assert rows[-1]["page"] == 1 and rows[-1]["page_index"] == 0


def test_the_technical_log_redacts_credential_shapes(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    try:
        raise RuntimeError(
            f"provider said: api_key={FAKE_KEY} at {FAKE_PATH}")
    except RuntimeError as exc:
        core._write_runtime_technical_log("redact1", exc)
    text = (data / "redact1" / "runtime_technical.log").read_text(encoding="utf-8")
    assert FAKE_KEY not in text
    assert "<redacted>" in text and "/srv" not in text


def test_trace_error_records_are_redacted_before_the_summary(tmp_path, monkeypatch):
    """trace 边界不知道调用方的字面密钥，但它能兜住一切模式形状的密钥。

    RunContext 会把运行目录写进 ``core.OUTPUT_DIR``：必须先指到私有临时目录，
    否则会话级守卫会把整个文件判非零退出（CI 上已实证过一次）。
    """
    data = tmp_path / "data"
    data.mkdir()
    run = trace.RunContext(job_id="redact-trace", output_dir=data)
    run.record_error(stage="model_request", operation="m",
                     error_type="RuntimeError",
                     message=f"relay echoed {FAKE_SK_KEY}")
    message = run.errors[-1]["message"]
    assert FAKE_SK_KEY not in message and "<redacted>" in message


# ---------------------------------------------------------------------------
# 4. CX-11 §1: provider failures persist a structured diagnostic, never raw text
# ---------------------------------------------------------------------------

class _RelayRefusal(RuntimeError):
    """The probe's exception shape: SDK-style status/body with an echoed reply."""

    status_code = 400
    body = {"error": {"message": "Relay refused value " + FAKE_KEY + ".",
                      "code": "invalid_request", "request_id": "cx11-req-1"}}

    def __init__(self):
        super().__init__("Error code: 400 - " + json.dumps(self.body))


def test_provider_error_detail_is_structured_and_carries_no_raw_text():
    from folith.engine.llm_provider import provider_error_detail

    detail = provider_error_detail(_RelayRefusal(), stage="translation")
    assert "class=" in detail and "http=400" in detail
    assert "code=invalid_request" in detail
    assert "request_id=cx11-req-1" in detail
    assert "error=_RelayRefusal" in detail and "stage=translation" in detail
    assert FAKE_KEY not in detail
    assert "Relay refused" not in detail, "回包自由文本一律不进诊断"


def test_translate_batch_raises_the_structured_diagnostic_not_the_raw_body(
        monkeypatch):
    """批失败的 RuntimeError 带结构化诊断；回显的密钥不再随它进入任务文件。"""
    monkeypatch.setattr(core, "_runtime_job_id", lambda: "redact_diag_test")
    monkeypatch.setattr(core, "_llm_cancel_check", lambda: None)

    def refuse(*_args, **_kwargs):
        raise _RelayRefusal()

    monkeypatch.setattr(core, "call_llm", refuse)
    with pytest.raises(RuntimeError) as caught:
        core.translate_batch(["one"], [], [], "", "", "zh",
                             "DeepSeek", FAKE_KEY, "deepseek-v4-flash")
    text = str(caught.value)
    assert "批次翻译失败（1 段）" in text
    assert "class=" in text and "http=400" in text and "code=invalid_request" in text
    assert "request_id=cx11-req-1" in text
    assert FAKE_KEY not in text and "Relay refused" not in text


# ---------------------------------------------------------------------------
# 5. CX-12 §1: diagnostic identifiers are shape-validated, never relay prose
# ---------------------------------------------------------------------------

def test_diagnostic_identifiers_reject_relay_prose_and_keep_real_ones():
    """code/request_id 只保留短机器标识；藏进字段里的回显整体丢弃。"""
    from folith.engine.llm_provider import provider_error_detail

    class HidingRelay(RuntimeError):
        status_code = 400
        body = {"error": {
            "code": FAKE_KEY + " CX12 prose /srv/relay/fixture/notes.docx",
            "message": "irrelevant",
            "request_id": FAKE_KEY + " and prose",
        }}

        def __init__(self):
            super().__init__("Error code: 400 - " + json.dumps(self.body))

    detail = provider_error_detail(HidingRelay())
    assert "class=" in detail and "http=400" in detail
    assert "code=" not in detail.split("class=")[0] and "code=" not in detail, \
        "非标识形状的 code 一律不进诊断"
    assert FAKE_KEY not in detail
    assert "request_id=" not in detail, "非标识形状的请求 ID 一律丢弃"

    class HonestRelay(RuntimeError):
        status_code = 400
        body = {"error": {"code": "invalid_request",
                          "message": "irrelevant",
                          "request_id": "req-honest-8471"}}

        def __init__(self):
            super().__init__("Error code: 400 - " + json.dumps(self.body))

    honest = provider_error_detail(HonestRelay())
    assert "code=invalid_request" in honest
    assert "request_id=req-honest-8471" in honest


def test_the_request_id_is_read_from_the_response_header_first():
    """x-request-id 响应头是可靠来源；回包里的 id 只是兜底（CX-12 应修）。"""
    import httpx

    class HeaderRelay(RuntimeError):
        status_code = 400
        response = httpx.Response(
            400, headers={"x-request-id": "req-header-8471"},
            json={"error": {"code": "invalid_request", "message": "irrelevant"}})

        def __init__(self):
            super().__init__("Error code: 400 - relay refused")

    from folith.engine.llm_provider import provider_error_detail

    detail = provider_error_detail(HeaderRelay())
    assert "request_id=req-header-8471" in detail
    assert "code=invalid_request" in detail
