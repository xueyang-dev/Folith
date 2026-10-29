"""Online OCR backend: OpenAI-compatible image transcription.

Why this module exists
----------------------
Two local engines are already wired through :class:`folith.ocr_evidence.OCRBackend`.
A third source is genuinely different: the page image leaves the machine and a
remote multimodal model returns the text.  This module implements **one** such
protocol — an OpenAI-compatible ``/chat/completions`` request that accepts an
image part and is asked to transcribe faithfully — because that is the only
remote protocol this project has actually exercised.  Vendor-specific OCR APIs
belong in a later adapter, not in a speculative multi-vendor framework.

Honesty rules enforced here
---------------------------
* The prompt asks for a faithful transcription only.  The request never carries
  a reference answer, the OCR candidate, or any translatable instruction.
* A response is only accepted when it parses, covers the page completely and was
  not truncated.  An empty response, a truncated response (``finish_reason ==
  "length"``) or a parse failure marks the page **failed** — never a successful
  empty page.
* Remote transcription returns no line geometry.  Coordinates are recorded as
  *unknown* (``geometry_known=False``) instead of a fabricated box.
* The engine's own confidence is unavailable and is recorded as *unknown*, not
  converted into a fake 0–100 score.
* Credentials never enter the request record, the cache identity or any report;
  the connection target only ever appears as a short SHA-256 fingerprint.

Request policy
--------------
Concurrency, pacing and rate-limit backoff reuse the process-wide
:mod:`folith.request_control` controller, so an online OCR pass and a
translation run against the same provider share one cooldown instead of
stampeding it.  Authentication and parameter errors are **not** retried; rate
limits, timeouts and 5xx responses are retried a bounded number of times.  A
timeout is recorded as "usage unknown": the service may have executed the
request even though no response was received.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from folith import page_events, request_control
from folith.ocr_evidence import OCRBackend, OCRBlock, OCRRuntimeInfo

ONLINE_OCR_BACKEND = "online"

#: Version of the transcription prompt.  Part of the cache identity: a changed
#: instruction is a different recognition configuration.
ONLINE_PROMPT_VERSION = "v1"

#: Default and hard bounds for the completion budget.  A page whose transcription
#: does not fit is reported as truncated rather than silently shortened.
DEFAULT_MAX_TOKENS = 8192
MIN_MAX_TOKENS = 256
MAX_MAX_TOKENS = 65536
DEFAULT_TIMEOUT_S = 180.0

#: Retry policy.  ``max_retries`` counts *additional* attempts.
DEFAULT_MAX_RETRIES = 2
MAX_MAX_RETRIES = 5
DEFAULT_BACKOFF_S = 1.0
MAX_BACKOFF_S = 30.0

#: Providers that require an explicit session header.  This is a transport
#: requirement of the one relay this project verified, not a generic parameter.
_SESSION_HEADER_PROVIDERS = {"OpenCode Go"}

#: Reasoning control is a DeepSeek-family API extension.  It is only sent when
#: the user explicitly selects it, so an unrelated relay never receives an
#: unsupported request field.
REASONING_MODES = ("provider_default", "thinking_disabled", "thinking_disabled_vllm")

SYSTEM_PROMPT_V1 = (
    "你是扫描页图像的文字转写员。逐字转写图中正文，按原始印刷行从上到下，"
    "每行一个条目。\n"
    "- 只转写图中看得见的文字：不翻译、不摘要、不补写、不现代化拼写、"
    "不修正历史拼写。\n"
    "- 不添加图中没有的标题、页码、注释、说明或标点。\n"
    "- 看不清的字符用 [?] 占位；整行都看不清时该行文本用 [?]。\n"
    "- 不要输出 Markdown、代码块或任何解释。\n"
    '只返回 JSON：{"segments":[{"index":0,"text":"该行原文","break_after":true}]}\n'
    "index 必须从 0 开始连续递增；break_after 表示该行之后是否开始新段落。"
)
USER_PROMPT_V1 = "转写这张扫描页的全部正文行，只返回 JSON。"
SYSTEM_PROMPTS = {ONLINE_PROMPT_VERSION: SYSTEM_PROMPT_V1}
USER_PROMPTS = {ONLINE_PROMPT_VERSION: USER_PROMPT_V1}

#: Markers that mean the model refused or answered with prose instead of a
#: transcription.  Checked only after JSON parsing has already failed, so a
#: legitimate transcription containing these words is never rejected.
_REFUSAL_MARKERS = (
    "i cannot", "i can't", "i'm unable", "i am unable", "as an ai",
    "无法转写", "不能转写", "抱歉", "对不起", "作为一个ai", "作为人工智能",
)


class OnlineOCRUnavailable(RuntimeError):
    """The online OCR source cannot run with the current configuration."""

    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(message)


class OnlineOCRRequestError(RuntimeError):
    """One transport attempt failed, with a retry decision attached."""

    def __init__(self, code: str, message: str, *, retryable: bool,
                 http_status: Optional[int] = None,
                 retry_after: Optional[float] = None) -> None:
        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(retryable)
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(self.message)


@dataclass(frozen=True)
class OnlineOCRConfig:
    """Connection + model + recognition parameters for the online source.

    ``api_key`` is deliberately excluded from ``repr`` so an exception traceback
    or a debug log can never print the credential.
    """

    provider: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.0
    reasoning_mode: str = "provider_default"
    prompt_version: str = ONLINE_PROMPT_VERSION
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES
    proxy_bypass: bool = False
    #: Where the credential came from, for the settings UI only.
    credential_source: str = ""
    #: ``provider|base_url`` of the saved connection that supplied ``api_key``.  A key must
    #: only ever travel to the endpoint of the connection that owns it; empty = not tracked.
    credential_owner: str = field(default="", repr=False)

    def endpoint_id(self) -> str:
        return f"{self.provider}|{str(self.base_url or '').rstrip('/')}"

    def normalized(self) -> "OnlineOCRConfig":
        reasoning = str(self.reasoning_mode or "").strip().lower()
        if reasoning not in REASONING_MODES:
            reasoning = "provider_default"
        prompt = str(self.prompt_version or "").strip() or ONLINE_PROMPT_VERSION
        if prompt not in SYSTEM_PROMPTS:
            prompt = ONLINE_PROMPT_VERSION
        return replace(
            self,
            provider=str(self.provider or "").strip(),
            base_url=str(self.base_url or "").strip().rstrip("/"),
            model=str(self.model or "").strip(),
            max_tokens=max(MIN_MAX_TOKENS, min(int(self.max_tokens or DEFAULT_MAX_TOKENS),
                                              MAX_MAX_TOKENS)),
            temperature=max(0.0, min(float(self.temperature or 0.0), 2.0)),
            reasoning_mode=reasoning,
            prompt_version=prompt,
            timeout_s=max(10.0, min(float(self.timeout_s or DEFAULT_TIMEOUT_S), 900.0)),
            max_retries=max(0, min(int(self.max_retries), MAX_MAX_RETRIES)),
        )

    def unavailable_reason(self) -> str:
        """Why this connection cannot run, or ``""`` when it is usable."""
        if not self.model:
            return "未选择线上 OCR 模型。"
        if not self.api_key:
            return "线上 OCR 缺少访问凭据；请在 OCR 设置中填写或引用已有凭据。"
        if not self.base_url:
            return "线上 OCR 缺少服务地址（需要 http(s)://…/v1）。"
        if self.credential_owner and self.credential_owner != self.endpoint_id():
            # 最后一道闸：密钥属于另一个连接时，宁可让任务失败也不把它发给这个地址。
            return ("线上 OCR 的访问凭据属于另一个连接，与当前服务地址不一致；"
                    "已拒绝发送请求。请在设置里重新选择视觉连接。")
        return ""

    # -- non-secret descriptors ------------------------------------------
    def target_fingerprint(self) -> str:
        """Short hash of the connection target; the only form cache keys keep."""
        import hashlib
        material = f"{self.provider}|{self.base_url}|{self.model}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]

    def cache_options(self) -> str:
        """Canonical, secret-free identity of everything that changes output."""
        return "|".join((
            "online",
            f"provider={self.provider}",
            f"target={self.target_fingerprint()}",
            f"model={self.model}",
            f"prompt={self.prompt_version}",
            f"max_tokens={int(self.max_tokens)}",
            f"temperature={self.temperature:g}",
            f"reasoning={self.reasoning_mode}",
        ))

    def describe(self) -> Dict[str, Any]:
        """Secret-free description safe for reports and task snapshots."""
        return {
            "source": ONLINE_OCR_BACKEND,
            "provider": self.provider,
            "base_url": _redact_url(self.base_url),
            "target_fingerprint": self.target_fingerprint(),
            "model": self.model,
            "prompt_version": self.prompt_version,
            "max_tokens": int(self.max_tokens),
            "temperature": float(self.temperature),
            "reasoning_mode": self.reasoning_mode,
            "timeout_s": float(self.timeout_s),
            "max_retries": int(self.max_retries),
            "credential_source": self.credential_source,
            "api_key_configured": bool(self.api_key),
            # Canonical cache-identity token; contains no credential material.
            "cache_options": self.cache_options(),
        }


def _redact_url(value: str) -> str:
    """Strip userinfo and query from a URL before it is stored or shown."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from urllib.parse import urlsplit, urlunsplit
        parts = urlsplit(text)
    except (TypeError, ValueError):
        return text.split("?", 1)[0][:200]
    if not parts.scheme or not parts.netloc:
        return text.split("?", 1)[0][:200]
    host = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _bounded_backoff(attempt: int, retry_after: Optional[float]) -> float:
    delay = min(MAX_BACKOFF_S, DEFAULT_BACKOFF_S * (2 ** max(0, attempt)))
    if retry_after is not None:
        try:
            delay = max(delay, min(float(retry_after), MAX_BACKOFF_S))
        except (TypeError, ValueError):
            pass
    return delay


def _retry_after_seconds(response: Any) -> Optional[float]:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after") or headers.get("Retry-After")
    except (TypeError, ValueError):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: 网关 / 读取超时：服务商那一页多半一直处理不完，再试几次只是白等几分钟，最多再试一次。
TIMEOUT_STATUSES = frozenset({408, 504, 524})
TIMEOUT_MAX_RETRIES = 1
#: A reply that arrives but breaks the agreed format (not JSON, wrong shape) is a
#: sampling accident, not a verdict on the page: ask again, but only this often.
CONTENT_MAX_RETRIES = 2
#: How much of a rejected reply is kept in the attempt record for diagnosis.
RESPONSE_EXCERPT_CHARS = 4000


def is_timeout_error(error: "OnlineOCRRequestError") -> bool:
    return error.code == "timeout" or error.http_status in TIMEOUT_STATUSES


def default_vision_reasoning(provider, model):
    """Thinking mode vision reads use when nobody chose one.

    DeepSeek-family models spend the whole output budget on reasoning when
    thinking is on (a page came back with 8,196 reasoning tokens of 8,196 and no
    answer), so reading a page needs it off.  Everyone else keeps the service
    default.
    """
    name = str(model or "").strip().lower()
    if provider == "OpenCode Go" and name == "deepseek-v4.1-flash":
        return "thinking_disabled"
    if provider == "DeepSeek" and name.startswith(("deepseek-flash", "deepseek-v4")):
        return "thinking_disabled"
    return "provider_default"


def classify_request_error(error: BaseException) -> OnlineOCRRequestError:
    """Turn an SDK/transport exception into an explicit retry decision.

    Authentication (401/403) and request-shape errors (400/404/405/422) never
    succeed on a retry and are surfaced immediately.  Rate limits, timeouts and
    5xx/connection failures are retryable.
    """
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    text = str(error or "")
    if status is None:
        match = re.search(r"\b(400|401|403|404|405|408|409|413|422|429|5\d{2})\b", text)
        if match:
            status = int(match.group(1))
    retry_after = _retry_after_seconds(response)
    name = type(error).__name__
    lowered = f"{name} {text}".lower()
    timeout_like = any(marker in lowered for marker in
                       ("timeout", "timed out", "connecterror", "connection error",
                        "apiconnectionerror", "apierror"))
    if status in {401, 403}:
        return OnlineOCRRequestError("auth_error", "线上 OCR 认证失败；请检查服务地址与凭据。",
                                     retryable=False, http_status=status)
    if status in {400, 404, 405, 413, 422}:
        return OnlineOCRRequestError(
            "invalid_request",
            f"线上 OCR 请求被服务拒绝（HTTP {status}）；请检查模型名与参数设置。",
            retryable=False, http_status=status)
    if status == 402 or (status == 429 and any(
            marker in lowered for marker in ("quota", "billing", "insufficient", "balance",
                                             "余额", "额度", "欠费"))):
        return OnlineOCRRequestError(
            "quota_error", f"线上 OCR 额度或余额不足（HTTP {status}）；请检查服务商账户。",
            retryable=False, http_status=status)
    if status == 429:
        return OnlineOCRRequestError("rate_limited", "线上 OCR 服务限流（HTTP 429）。",
                                     retryable=True, http_status=status,
                                     retry_after=retry_after)
    if status is not None and status >= 500:
        return OnlineOCRRequestError(
            "service_error", f"线上 OCR 服务错误（HTTP {status}）。",
            retryable=True, http_status=status, retry_after=retry_after)
    if "timeout" in name.lower() or timeout_like:
        return OnlineOCRRequestError("timeout", "线上 OCR 请求超时。", retryable=True,
                                     http_status=status, retry_after=retry_after)
    if status in {408, 409}:
        return OnlineOCRRequestError("transient_error",
                                     f"线上 OCR 请求暂时失败（HTTP {status}）。",
                                     retryable=True, http_status=status,
                                     retry_after=retry_after)
    return OnlineOCRRequestError("request_failed", f"线上 OCR 请求失败：{text[:200]}",
                                 retryable=True, http_status=status,
                                 retry_after=retry_after)


def decode_segments(raw: str) -> List[Dict[str, Any]]:
    """Parse and *completely* validate one transcription response.

    There is no known line count to compare against, so completeness means:
    a JSON object with a ``segments`` list, every entry carrying a string
    ``text`` and a boolean ``break_after``, indices contiguous from 0 in
    ascending order, and at least one non-empty line.  Anything else raises —
    the caller marks the page failed instead of guessing.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty_response")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    value: Any = None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        start = text.find("{")
        if start >= 0:
            try:
                value = json.JSONDecoder().raw_decode(text[start:])[0]
            except (TypeError, ValueError):
                value = None
        if value is None:
            lowered = text.casefold()
            if any(marker in lowered for marker in _REFUSAL_MARKERS):
                raise ValueError("refused")
            raise ValueError("not_json")
    rows = value.get("segments") if isinstance(value, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("invalid_schema")
    parsed: List[Dict[str, Any]] = []
    indexes: List[int] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_schema")
        index = row.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("invalid_index")
        body = row.get("text")
        if not isinstance(body, str):
            raise ValueError("invalid_text")
        break_after = row.get("break_after")
        if not isinstance(break_after, bool):
            raise ValueError("invalid_break_after")
        indexes.append(index)
        parsed.append({"index": index, "text": body, "break_after": break_after})
    if indexes != list(range(len(parsed))):
        raise ValueError("index_not_contiguous")
    if not any(row["text"].strip() for row in parsed):
        raise ValueError("empty_transcription")
    return parsed


class OnlineOCRBackend(OCRBackend):
    """Remote faithful transcription through an OpenAI-compatible endpoint."""

    name = ONLINE_OCR_BACKEND
    display_name = "线上 OCR API"
    max_workers = 4

    def __init__(self, config: OnlineOCRConfig, *,
                 client_factory: Optional[Callable[[OnlineOCRConfig], Any]] = None,
                 cancel_check: Optional[Callable[[], bool]] = None,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.config = (config or OnlineOCRConfig()).normalized()
        self._client_factory = client_factory
        self._client = None
        self._cancel_check = cancel_check
        self._sleep = sleeper
        self._state = "available"
        self._import_error = ""
        self._last_timings: Dict[str, float] = {}
        self._last_request: Dict[str, Any] = {}
        self.load_seconds = 0.0

    # -- availability -----------------------------------------------------
    def unavailable_reason(self) -> str:
        return self.config.unavailable_reason()

    def status(self) -> Dict[str, Any]:
        """Configuration-only status: never issues a billable request."""
        reason = self.unavailable_reason()
        if reason:
            return {"state": "unconfigured", "message": reason, **self.config.describe()}
        return {"state": "configured",
                "message": "已配置；识别时会实际请求该服务。",
                **self.config.describe()}

    def available(self) -> bool:
        return not self.unavailable_reason()

    def capabilities(self) -> Dict[str, Any]:
        return {
            "languages": [], "scripts": ["any"],
            "cpu": True, "gpu": False, "rtl": True,
            "line_boxes": False, "word_boxes": False,
            "geometry": False,
            "confidence_kind": "unknown",
            "max_workers": self.max_workers,
            "platforms": ["network"],
            "remote": True,
        }

    def quality_note(self) -> str:
        return ("线上 OCR 只返回文字，不返回行框；原页对照只能定位到页面，"
                "无法按坐标高亮。请勿把它当作逐词可核对的证据。")

    def runtime_info(self, language: str, *, include_model_version: bool = True) -> OCRRuntimeInfo:
        descriptor = self.config.describe()
        return OCRRuntimeInfo(
            backend=self.name,
            model=self.config.model or "unconfigured",
            device="remote",
            provider=self.config.provider or "openai_compatible",
            execution_provider="remote_api",
            gpu_name=None,
            backend_version="openai-chat-completions/" + self.config.prompt_version,
            extra={
                "language": language,
                "online": descriptor,
                "confidence_kind": "unknown",
                "geometry": False,
                # The online source owns no local model assets, so there is
                # nothing to hash; the value stays explicit rather than absent.
                "model_version": ("not_inspected" if not include_model_version
                                  else "remote_no_local_assets"),
                "state": self._state,
            },
        )

    # -- lifecycle --------------------------------------------------------
    def load(self, on_status=None, cancel_check=None):
        if cancel_check is not None:
            self._cancel_check = cancel_check
        if on_status:
            on_status(f"线上 OCR：{self.config.model}（{self.config.provider or 'OpenAI 兼容'}）")
        return None

    def close(self):
        # Clients are owned by the shared transport registry; nothing to close
        # here, and closing another thread's pooled client would be wrong.
        return None

    # -- request plumbing -------------------------------------------------
    def _get_client(self):
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(self.config)
            return self._client
        self._client = _default_client_factory(self.config)
        return self._client

    def _ensure_cancelled(self):
        if self._cancel_check is not None and self._cancel_check():
            raise RuntimeError("任务已请求取消")

    def _request_body(self, system_prompt: str, user_content: List[Dict[str, Any]]) -> Dict[str, Any]:
        config = self.config
        body: Dict[str, Any] = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if config.reasoning_mode == "thinking_disabled":
            # Documented DeepSeek-family control; sent only on explicit opt-in.
            body["extra_body"] = {"thinking": {"type": "disabled"}}
        elif config.reasoning_mode == "thinking_disabled_vllm":
            # vLLM/SGLang chat-template switch, same wire shape as the text path.
            body["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if config.provider in _SESSION_HEADER_PROVIDERS:
            session_id = getattr(self, "_session_id", None)
            if not session_id:
                import uuid
                session_id = str(uuid.uuid4())
                self._session_id = session_id
            body["extra_headers"] = {
                "User-Agent": "Folith/0.8.0",
                "x-opencode-session": session_id,
            }
        return body

    def _attempt(self, png_bytes: bytes, system_prompt: str, user_prompt=None) -> Dict[str, Any]:
        user_content = [
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64,"
                                  + base64.b64encode(png_bytes).decode("ascii")}},
            {"type": "text", "text": user_prompt or USER_PROMPTS[self.config.prompt_version]},
        ]
        body = self._request_body(system_prompt, user_content)
        client = self._get_client()
        started = time.perf_counter()
        response = client.chat.completions.create(**body)
        elapsed = time.perf_counter() - started
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise OnlineOCRRequestError("empty_choices", "线上 OCR 服务未返回任何候选结果。",
                                        retryable=True)
        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) or ""
        usage = getattr(response, "usage", None)
        usage_dict = None
        if usage is not None:
            try:
                usage_dict = usage.model_dump()
            except AttributeError:
                usage_dict = dict(usage) if isinstance(usage, dict) else None
        return {
            "text": content,
            "finish_reason": getattr(choice, "finish_reason", None),
            "request_id": getattr(response, "id", None),
            "model": getattr(response, "model", None) or self.config.model,
            "usage": usage_dict,
            "seconds": elapsed,
        }

    def _usage_record(self, usage: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Actual reported usage; ``amount`` stays unknown unless reported."""
        if not isinstance(usage, dict):
            return {"reported": False, "prompt_tokens": None, "completion_tokens": None,
                    "reasoning_tokens": None, "total_tokens": None, "amount": None}
        details = usage.get("completion_tokens_details") or {}
        return {
            "reported": True,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens") if isinstance(details, dict) else None,
            "total_tokens": usage.get("total_tokens"),
            # No billing amount is returned by this protocol; it stays unknown.
            "amount": usage.get("amount") if isinstance(usage.get("amount"), (int, float)) else None,
        }

    # -- page recognition -------------------------------------------------
    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        segments, error = self.read_image(png_bytes)
        if error:
            return [], error
        return self._blocks_from_segments(segments, page_index, page_number, language), None

    def read_image(self, png_bytes: bytes, *, system_prompt=None,
                   user_prompt=None, decoder=decode_segments):
        """Shared bounded image transport; response normalization belongs to adapters."""
        self._last_timings = {}
        reason = self.unavailable_reason()
        if reason:
            self._last_request = {"status": "unavailable", "error_code": "unconfigured",
                                  **self.config.describe()}
            return [], f"unconfigured: {reason}"
        if self._cancel_check is not None and self._cancel_check():
            raise RuntimeError("任务已请求取消")
        system_prompt = system_prompt or SYSTEM_PROMPTS[self.config.prompt_version]
        controller = request_control.get_request_controller(
            self.config.provider or ONLINE_OCR_BACKEND, self.config.model)
        attempts: List[Dict[str, Any]] = []
        usage_total: Dict[str, Any] = self._usage_record(None)
        request_ids: List[str] = []
        last_error: Optional[OnlineOCRRequestError] = None
        started = time.perf_counter()
        for attempt in range(self.config.max_retries + 1):
            self._ensure_cancelled()
            gate_wait = controller.acquire(self._cancel_check)
            attempt_started = time.perf_counter()
            try:
                result = (self._attempt(png_bytes, system_prompt, user_prompt=user_prompt)
                          if user_prompt is not None else self._attempt(png_bytes, system_prompt))
            except BaseException as exc:  # noqa: BLE001 - classified below
                classified = classify_request_error(exc)
                attempts.append({
                    "attempt": attempt,
                    "status": "failed",
                    "error_code": classified.code,
                    "http_status": classified.http_status,
                    "message": classified.message[:200],
                    "seconds": time.perf_counter() - attempt_started,
                    "usage_unknown": True,
                })
                if classified.http_status == 429:
                    controller.note_rate_limited(classified.retry_after)
                last_error = classified
                retries = self.config.max_retries
                if is_timeout_error(classified):
                    retries = min(retries, TIMEOUT_MAX_RETRIES)
                if not classified.retryable or attempt >= retries:
                    break
                page_events.emit("retry", attempt=attempt + 1,
                                 attempts=retries + 1,
                                 http_status=classified.http_status,
                                 code=classified.code, message=classified.message[:120])
                delay = _bounded_backoff(attempt, classified.retry_after)
                self._last_timings[f"retry_backoff_{attempt}"] = delay
                self._sleep_slices(delay)
                continue
            controller.note_success()
            attempts.append({
                "attempt": attempt,
                "status": "returned",
                "request_id": result.get("request_id"),
                "finish_reason": result.get("finish_reason"),
                "model": result.get("model"),
                "seconds": result.get("seconds"),
                "gate_wait_s": gate_wait,
                "usage_unknown": result.get("usage") is None,
            })
            usage = self._usage_record(result.get("usage"))
            usage_total = {
                "reported": usage_total["reported"] or usage["reported"],
                "prompt_tokens": _sum_optional(usage_total["prompt_tokens"], usage["prompt_tokens"]),
                "completion_tokens": _sum_optional(usage_total["completion_tokens"], usage["completion_tokens"]),
                "reasoning_tokens": _sum_optional(usage_total["reasoning_tokens"], usage["reasoning_tokens"]),
                "total_tokens": _sum_optional(usage_total["total_tokens"], usage["total_tokens"]),
                "amount": _sum_optional(usage_total["amount"], usage["amount"]),
            }
            if result.get("request_id"):
                request_ids.append(str(result["request_id"]))
            finish = result.get("finish_reason")
            if finish == "length":
                # The reply never finished, so it never reaches the decoder: keep its
                # head and tail, and how repetitive it is, so a runaway can be studied.
                text = str(result.get("text") or "")
                # Store text so subclasses (e.g. OcrApiBackend) can inspect it for
                # repetition loops and attempt recovery before accepting the failure.
                self._last_response_text = text
                half = RESPONSE_EXCERPT_CHARS // 2
                pieces = text.split(",")
                attempts[-1].update(
                    response_chars=len(text), response_head=text[:half],
                    response_tail=text[-half:] if len(text) > half else "",
                    distinct_piece_ratio=round(len(set(pieces)) / max(len(pieces), 1), 3))
                return self._fail(
                    "truncated", _truncation_message(text),
                    attempts, usage_total, request_ids, started)
            if finish not in {None, "stop"}:
                return self._fail(
                    "incomplete_response",
                    f"线上 OCR 未正常结束（finish_reason={finish}）；该页未保存。",
                    attempts, usage_total, request_ids, started)
            raw = result.get("text") or ""
            self._last_response_text = raw
            try:
                segments = decoder(raw)
            except ValueError as exc:
                code = str(exc)
                attempts[-1].update(error_code=code, response_chars=len(raw),
                                    response_excerpt=raw[:RESPONSE_EXCERPT_CHARS])
                if attempt >= min(self.config.max_retries, CONTENT_MAX_RETRIES):
                    return self._fail(
                        code, _parse_failure_message(code),
                        attempts, usage_total, request_ids, started)
                page_events.emit("retry", attempt=attempt + 1,
                                 attempts=min(self.config.max_retries, CONTENT_MAX_RETRIES) + 1,
                                 http_status=None, code=code,
                                 message=f"返回格式不合要求（{code}）")
                continue
            if not segments:
                return self._fail("empty_transcription", "线上 OCR 未返回可用的正文行；该页未保存。",
                                  attempts, usage_total, request_ids, started)
            self._last_request = {
                "status": "completed",
                "backend": self.name,
                "provider": self.config.provider,
                "model": self.config.model,
                "model_reported": result.get("model"),
                "target_fingerprint": self.config.target_fingerprint(),
                "prompt_version": self.config.prompt_version,
                "request_ids": request_ids,
                "attempts": attempts,
                "attempt_count": len(attempts),
                "usage": usage_total,
                "finish_reason": finish,
                "max_tokens": self.config.max_tokens,
                "reasoning_mode": self.config.reasoning_mode,
                "geometry": False,
                "confidence": "unknown",
            }
            self._last_timings["request_s"] = time.perf_counter() - started
            return segments, None
        # every attempt failed
        page_events.emit("failed", attempts=len(attempts),
                         http_status=getattr(last_error, "http_status", None),
                         code=getattr(last_error, "code", ""),
                         message=str(getattr(last_error, "message", ""))[:120])
        error = last_error or OnlineOCRRequestError("request_failed", "线上 OCR 请求失败。",
                                                    retryable=False)
        self._last_request = {
            "status": "failed",
            "backend": self.name,
            "provider": self.config.provider,
            "model": self.config.model,
            "target_fingerprint": self.config.target_fingerprint(),
            "prompt_version": self.config.prompt_version,
            "request_ids": request_ids,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "usage": usage_total,
            "error_code": error.code,
            "http_status": error.http_status,
            "geometry": False,
            "confidence": "unknown",
        }
        self._last_timings["request_s"] = time.perf_counter() - started
        return [], f"{error.code}: {error.message}"

    def _fail(self, code: str, message: str, attempts, usage_total, request_ids,
              started) -> Tuple[List[OCRBlock], Optional[str]]:
        page_events.emit("failed", attempts=len(attempts), http_status=None,
                         code=code, message=str(message)[:120])
        self._last_request = {
            "status": "failed",
            "backend": self.name,
            "provider": self.config.provider,
            "model": self.config.model,
            "target_fingerprint": self.config.target_fingerprint(),
            "prompt_version": self.config.prompt_version,
            "request_ids": request_ids,
            "attempts": attempts,
            "attempt_count": len(attempts),
            "usage": usage_total,
            "error_code": code,
            "geometry": False,
            "confidence": "unknown",
        }
        self._last_timings["request_s"] = time.perf_counter() - started
        return [], f"{code}: {message}"

    def _blocks_from_segments(self, segments, page_index, page_number, language):
        info = self.runtime_info(language)
        blocks: List[OCRBlock] = []
        for segment in segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            line = {
                "text": text,
                "left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0,
                # Explicitly unknown: a placeholder must never be read as a box.
                "geometry_known": False,
                "break_after": bool(segment.get("break_after")),
            }
            blocks.append(OCRBlock(
                text=text, left=0.0, top=0.0, right=0.0, bottom=0.0,
                # No engine confidence exists for a generative transcriber.
                confidence=None, engine_confidence=None,
                confidence_kind="unknown",
                page_index=page_index, page_number=page_number,
                backend=self.name, device=info.device, provider=info.provider,
                model=info.model, backend_version=info.backend_version,
                raw_text=text, lines=[line],
                geometry_known=False, geometry_source="none",
            ))
        return blocks

    def _sleep_slices(self, delay: float) -> None:
        remaining = max(0.0, float(delay))
        while remaining > 0:
            self._ensure_cancelled()
            step = min(0.2, remaining)
            self._sleep(step)
            remaining -= step


def _sum_optional(left, right):
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def _truncation_message(response_text: str) -> str:
    """#258：finish_reason=length 时，若响应全是纯文本（无 JSON 标记），说明很可能是
    OCR 专用模型在输出原始文本，而非视觉理解模型按格式回答；给出针对性提示。

    判断依据：响应里完全没有 JSON 的结构特征（{ [ "），同时长度不为零。
    """
    text = str(response_text or "").strip()
    looks_like_json = text.startswith(("{", "[")) or '{"' in text or "'b'" in text
    if text and not looks_like_json:
        return (
            "视觉理解返回的是纯文本，不是约定的结构化格式（finish_reason=length）；该页未保存。"
            "如果你选择的是 OCR 专用模型（如 PaddleOCR-VL、GOT-OCR），"
            "请改用支持图像输入并能按格式回答的视觉语言模型。"
        )
    return (
        "线上 OCR 输出被服务截断（finish_reason=length）；"
        "该页未保存，请提高输出上限或改用其他来源。"
    )


def _parse_failure_message(code: str) -> str:
    # #258：invalid_semantic_json 是「返回内容无法解析为约定的 JSON」；
    # 最常见的原因是 OCR 专用模型只返回纯文本——给出针对性提示。
    if code in ("invalid_semantic_json", "invalid_semantic_blocks", "invalid_semantic_block",
                "invalid_semantic_language"):
        return (
            "视觉理解返回的内容不是约定的结构化格式；该页未保存。"
            "如果你选择的是 OCR 专用模型（如 PaddleOCR-VL、GOT-OCR），"
            "请改用支持图像输入并能按格式回答的视觉语言模型。"
        )
    return {
        "empty_response": "线上 OCR 返回空响应；该页未保存。",
        "not_json": "线上 OCR 响应不是约定的 JSON；该页未保存。",
        "refused": "线上 OCR 模型拒绝了转写，返回的是说明文字；该页未保存。",
        "invalid_schema": "线上 OCR 响应缺少 segments 列表；该页未保存。",
        "invalid_index": "线上 OCR 响应的 index 不是整数；该页未保存。",
        "invalid_text": "线上 OCR 响应缺少文本字段；该页未保存。",
        "invalid_break_after": "线上 OCR 响应缺少 break_after 字段；该页未保存。",
        "index_not_contiguous": "线上 OCR 响应的行号不连续；该页未保存。",
        "empty_transcription": "线上 OCR 未返回任何非空文本；该页未保存。",
    }.get(code, f"线上 OCR 响应校验失败（{code}）；该页未保存。")


def _default_client_factory(config: OnlineOCRConfig):
    """Build the pooled OpenAI-compatible client. Imported lazily: no network at import."""
    from openai import OpenAI
    from folith import llm_transport
    import httpx

    if not config.base_url:
        raise OnlineOCRUnavailable("unconfigured", "线上 OCR 缺少服务地址。")
    timeout = (15.0, float(config.timeout_s))

    bypass_proxy = llm_transport.proxy_bypass_for(config.proxy_bypass, config.base_url)

    def build():
        kwargs: Dict[str, Any] = {"api_key": config.api_key, "base_url": config.base_url,
                                  "timeout": timeout, "max_retries": 0}
        bypass = None
        if bypass_proxy:
            bypass = httpx.Client(trust_env=False, timeout=timeout)
            kwargs["http_client"] = bypass
        try:
            return OpenAI(**kwargs)
        except BaseException:
            if bypass is not None:
                bypass.close()
            raise

    return llm_transport.get_registry().acquire(
        llm_transport.build_openai_identity(
            provider=f"ocr-online:{config.provider or 'custom'}",
            base_url=config.base_url, proxy_bypass=bypass_proxy,
            api_key=config.api_key, timeout=timeout, max_retries=0),
        build)
