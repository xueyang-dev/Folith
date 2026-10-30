"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import httpx
import json
import os
import re
from folith import llm_transport as _llm_transport
from folith import request_control as _request_control
from folith import translation_protocol as _translation_protocol
from folith.trace import trace
from urllib.parse import urlparse
from folith.engine._link import core as _c


def normalize_openai_base_url(base_url):
    """接受基址或误填的 OpenAI 兼容具体端点，并统一回基址。"""
    base = (base_url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/responses", "/models"):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    if not base:
        return ""
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return base


def resolve_openai_base_url(provider, base_url=None):
    """Resolve an OpenAI-compatible base URL without silently falling back.

    A custom relay has no registry URL.  Falling through to the OpenAI SDK's
    default in that case sends a relay credential to api.openai.com and turns
    a configuration mistake into a misleading ``invalid_api_key`` response.
    """
    cfg = _c.PROVIDERS.get(provider) or {}
    explicit = base_url is not None
    if explicit and str(base_url).strip() and not normalize_openai_base_url(base_url):
        raise ValueError("API 地址无效（需要 http(s)://…/v1）")
    resolved = normalize_openai_base_url(
        base_url if explicit and str(base_url).strip() else cfg.get("base_url"))
    if cfg.get("custom_base_url") and explicit and not resolved:
        raise ValueError("自定义中转站的 API 地址无效（需要 http(s)://…/v1）")
    if cfg.get("custom_base_url") and not explicit and not resolved:
        resolved = normalize_openai_base_url(getattr(_c._LLM_CTX, "base_url", None))
    if cfg.get("custom_base_url") and not resolved:
        raise ValueError("自定义中转站未配置有效的 API 地址（需要 http(s)://…/v1）")
    if provider == "OpenAI" and not resolved:
        # Resolve SDK environment/default routing here, before cache identity
        # and client construction. Always pass the result explicitly to SDK.
        candidate = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        resolved = normalize_openai_base_url(candidate)
        if not resolved:
            raise ValueError("API 地址无效（需要 http(s)://…/v1）")
    if provider == "Local Gateway" and resolved:
        parsed = urlparse(resolved)
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("API 地址不能包含凭据、查询参数或片段")
    return resolved


def set_llm_base_url(base_url):
    """为当前线程设置 OpenAI 兼容中转站地址（空值清除）。"""
    _c._LLM_CTX.base_url = normalize_openai_base_url(base_url) or None
    return _c._LLM_CTX.base_url


def reasoning_effort_options(provider, model):
    """Return safe reasoning-effort values for a known reasoning model.

    OpenAI-compatible relays do not expose a portable capability schema.  We
    therefore only offer the control for recognizable reasoning model names;
    unknown custom models remain in automatic mode so an unsupported request
    parameter cannot break translation.
    """
    if provider == "Local Gateway":
        # 网关不声明支持任何推理档位；发过去只会换来 400。
        return ()
    cfg = _c.PROVIDERS.get(provider) or {}
    if cfg.get("kind") not in {"openai", "openai_compat"}:
        return ()
    name = str(model or "").strip().lower()
    if not name:
        return ()
    # OpenAI reasoning families and common compatible relay names.  The UI
    # deliberately uses the portable low/medium/high subset.
    prefixes = ("o1", "o3", "o4-mini", "gpt-5", "gpt-6", "deepseek-r1", "qwq")
    if "-chat" in name or not name.startswith(prefixes):
        return ()
    return ("low", "medium", "high")


def set_llm_reasoning_effort(value):
    """Set or clear the per-thread Chat Completions reasoning effort."""
    value = str(value or "").strip().lower()
    _c._LLM_CTX.reasoning_effort = value if value in _c._REASONING_EFFORT_VALUES else None
    return _c._LLM_CTX.reasoning_effort


REASONING_MODES = ("provider_default", "thinking_disabled", "thinking_disabled_vllm")
# usage-ledger role -> configurable role.  Reviewer is intentionally absent.
_MODE_ROLE_OF = {"translation": "translator", "annotation": "translator",
                 "translator": "translator", "auxiliary": "auxiliary"}
# Model families whose endpoint was verified to honour an explicit "stop
# thinking" control, and the wire shape that control takes.  A family absent
# here is offered no switch at all: an unverified request parameter must never
# reach a relay that could reject it or, worse, ignore it silently.
_THINKING_CONTROL_MODELS = {
    # vLLM/SGLang chat-template switch, verified against dots3-note-prev.
    "thinking_disabled_vllm": (("dots3", "dots-"), None),
    # DeepSeek's own switch, verified on the official endpoint (page reads with
    # it came back with 0 reasoning tokens) and on OpenCode Go's deepseek model.
    # With thinking on, these models spend most of the output on reasoning and,
    # on vision pages, the whole output budget, so the family reads "off" unless
    # the user chose otherwise.  Only these model names were measured: older names
    # such as deepseek-chat are not thinking models and get nothing.  A custom relay serving a deepseek-named model
    # is not covered: nobody measured what it forwards.
    "thinking_disabled": (("deepseek-flash", "deepseek-v4"), frozenset({"DeepSeek", "OpenCode Go"})),
}


def thinking_control_options(provider, model):
    """Return the thinking switches a model's endpoint is known to honour.

    Mirrors :func:`reasoning_effort_options`: the control is offered only for
    recognizable names, so an unknown custom model stays on its provider
    default rather than receiving a parameter it may not understand.
    """
    if provider == "Local Gateway":
        return ()
    cfg = _c.PROVIDERS.get(provider) or {}
    if cfg.get("kind") not in {"openai", "openai_compat"}:
        return ()
    name = str(model or "").strip().lower()
    if not name:
        return ()
    return tuple(mode for mode, (prefixes, providers) in _THINKING_CONTROL_MODELS.items()
                 if name.startswith(prefixes) and (providers is None or provider in providers))


def connection_thinking_mode():
    """Thinking mode stored on the connection currently used for translation.

    ``None`` means that connection records no choice, so the caller falls back
    to the task snapshot and the environment.  An explicit default is *not* the
    same as no choice: it is how the user turns the control back off after a run
    already recorded the opted-in mode in the snapshot.  The connection editor
    writes the empty string for 服务默认, so that counts as a choice too.
    """
    saved = _c.load_provider_config() or {}
    wanted = str((saved.get("connection_roles") or {}).get("translation") or "")
    profile = next((p for p in saved.get("connections") or []
                    if isinstance(p, dict) and p.get("id") == wanted), None)
    if not profile or profile.get("reasoning_mode") is None:
        return None
    mode = str(profile.get("reasoning_mode") or "").strip().lower()
    if mode in ("", "provider_default"):
        return "provider_default"
    if mode not in thinking_control_options(profile.get("provider", ""),
                                            profile.get("model", "")):
        return None                 # the model behind this connection changed
    return mode


def normalize_reasoning_mode(value):
    value = str(value or "").strip().lower()
    return value if value in REASONING_MODES else "provider_default"


def set_llm_reasoning_modes(translator=None, auxiliary=None):
    """Set the per-thread text-path reasoning mode for each role.

    Default (``provider_default``) sends nothing extra.  ``thinking_disabled``
    sends ``extra_body={"thinking": {"type": "disabled"}}``, the same opt-in
    control the online-OCR path uses; ``thinking_disabled_vllm`` sends the
    chat-template switch instead.  Empty values clear the setting.
    """
    modes = {"translator": normalize_reasoning_mode(translator),
             "auxiliary": normalize_reasoning_mode(auxiliary)}
    _c._LLM_CTX.reasoning_modes = modes
    return dict(modes)


def resolve_reasoning_mode(explicit=None, role=None):
    """Mode for a request: explicit argument > this thread's role setting."""
    if explicit is not None:
        return normalize_reasoning_mode(explicit)
    if role is None:
        from folith import usage as _usage
        role = _usage.CURRENT_ROLE.get()
    configurable = _MODE_ROLE_OF.get(role)
    modes = getattr(_c._LLM_CTX, "reasoning_modes", None) or {}
    return normalize_reasoning_mode(modes.get(configurable)) if configurable else "provider_default"


def default_thinking_mode(target):
    """Mode a model gets when nobody chose one: off for families that need it."""
    if target is not None and "thinking_disabled" in thinking_control_options(*target):
        return "thinking_disabled"
    return "provider_default"


def job_reasoning_modes(translator=None, auxiliary=None, connection_mode=None,
                        saved=None, env=None, auxiliary_target=None,
                        translator_target=None):
    """Resolve the (translator, auxiliary) reasoning modes a run will use.

    Per role: explicit argument > the connection's own switch > the saved task
    snapshot > the environment.  The connection beats the snapshot because it is
    live user intent, and it feeds both roles: a run that only sped the
    translator up leaves the pipeline waiting on auxiliary calls instead.

    ``translator_target`` / ``auxiliary_target`` are the ``(provider, model)``
    each role actually calls.  A switch was verified on particular models, so a
    mode only applies to a model whose endpoint honours it: one carried over from
    another connection (a task started on a vLLM relay, then moved to DeepSeek)
    is replaced instead of being sent to an endpoint that ignores or rejects it.
    When nothing valid is left, the model's own default applies (off for the
    DeepSeek family).  A recorded ``provider_default`` is not a choice; only the
    connection's switch or an explicit argument can ask for it.
    """
    saved = saved or {}
    if env is None:
        env = os.environ

    def connection_mode_for(target):
        if connection_mode is None:
            return None
        if target is None or connection_mode == "provider_default":
            return connection_mode
        return (connection_mode
                if connection_mode in thinking_control_options(*target) else None)

    def one(explicit, snapshot_key, env_key, mode_for, target):
        if explicit is not None:
            return explicit
        inherited = mode_for()
        if inherited is not None:
            return inherited
        recorded = normalize_reasoning_mode(saved.get(snapshot_key) or env.get(env_key))
        verified = thinking_control_options(*target) if target is not None else ()
        if not verified:
            return recorded           # nothing verified for this model: keep what was recorded
        if recorded != "provider_default" and recorded in verified:
            return recorded
        return default_thinking_mode(target)

    return (normalize_reasoning_mode(
                one(translator, "reasoning_mode", "FOLITH_REASONING_MODE",
                    lambda: connection_mode_for(translator_target), translator_target)),
            normalize_reasoning_mode(
                one(auxiliary, "auxiliary_reasoning_mode",
                    "FOLITH_AUXILIARY_REASONING_MODE",
                    lambda: connection_mode_for(auxiliary_target), auxiliary_target)))


def effective_request_route(provider, model, role=None):
    """What a request made on this thread reaches, beyond provider and model.

    The same provider and model name can sit behind different endpoints (two
    relays both serving ``gpt-4o``), and the thread's reasoning effort changes
    the request body.  Caches keyed on "the same request" must include both,
    resolved exactly as :func:`core._call_llm_transport` resolves them.
    """
    cfg = _c.PROVIDERS.get(provider) or {}
    if cfg.get("kind") == "gemini":
        return {}
    try:
        pinned = getattr(_c._LLM_CTX, "annotation_base_url", None) if role == "annotation" else None
        base_url = resolve_openai_base_url(provider, pinned) or ""
    except ValueError:
        base_url = ""             # the request itself will fail; nothing to reuse
    reasoning = getattr(_c._LLM_CTX, "reasoning_effort", None)
    if reasoning not in reasoning_effort_options(provider, model):
        reasoning = None
    route = {"base_url": base_url, "reasoning_effort": reasoning or ""}
    mode = resolve_reasoning_mode(role=role)
    if mode != "provider_default":
        # Only present when opted in, so default-route cache keys are unchanged
        # while thinking-on and thinking-off results can never mix.
        route["reasoning_mode"] = mode
    return route


def clean_xml_chars(text):
    if not isinstance(text, str):
        return str(text)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)


def parse_json_array(text):
    """从 LLM 输出中稳健地解析 JSON 数组；解析失败返回 None。

    依次尝试：整体解析 -> 去掉 Markdown 代码块后解析 -> 从每个 '[' 位置做
    raw_decode（可容忍输出前后夹带解释文字）。
    """
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    candidate = re.sub(r'^```(?:json)?\s*', '', candidate, flags=re.DOTALL)
    candidate = re.sub(r'\s*```$', '', candidate, flags=re.DOTALL).strip()

    try:
        obj = json.loads(candidate)
        if isinstance(obj, list):
            return obj
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for m in re.finditer(r'\[', candidate):
        try:
            obj, _ = decoder.raw_decode(candidate[m.start():])
        except Exception:
            continue
        if isinstance(obj, list):
            return obj
    return None


def parse_translation_array(res, expected):
    """Compatibility API backed by the canonical translation parser."""
    return _translation_protocol.parse_translation_array(res, expected)


def is_rate_limited(err):
    s = str(err)
    return '429' in s or 'RESOURCE_EXHAUSTED' in s or 'rate limit' in s.lower()


def _llm_request_controller(provider, model, api_key="", *, base_url=None):
    """Shared, provider/model scoped request controller (see request_control)."""
    if provider == "Local Gateway":
        # 一个网关地址就是一条独立的限流/退避线；同一模型名换个端点不能再共用。
        route = effective_request_route(provider, model)
        endpoint = (resolve_openai_base_url(provider, base_url)
                    if base_url is not None else route.get("base_url", ""))
        provider = "|".join((provider, endpoint or "",
                             _llm_transport.api_key_fingerprint(api_key)))
    return _request_control.get_request_controller(provider, model)


def _record_llm_thread_stats(seconds, *, failed):
    _c._LLM_LATENCY_CTX.seconds = getattr(_c._LLM_LATENCY_CTX, "seconds", 0.0) + max(0.0, seconds)
    _c._LLM_LATENCY_CTX.requests = getattr(_c._LLM_LATENCY_CTX, "requests", 0) + 1
    if failed:
        _c._LLM_LATENCY_CTX.failures = getattr(_c._LLM_LATENCY_CTX, "failures", 0) + 1


def llm_thread_stats():
    """Cumulative provider stats for the calling thread."""
    return {
        "api_latency_s": float(getattr(_c._LLM_LATENCY_CTX, "seconds", 0.0) or 0.0),
        "requests": int(getattr(_c._LLM_LATENCY_CTX, "requests", 0) or 0),
        "failures": int(getattr(_c._LLM_LATENCY_CTX, "failures", 0) or 0),
    }


def llm_thread_stats_delta(before):
    after = llm_thread_stats()
    return {
        "api_latency_s": max(0.0, after["api_latency_s"] - float(before.get("api_latency_s") or 0.0)),
        "requests": max(0, after["requests"] - int(before.get("requests") or 0)),
        "failures": max(0, after["failures"] - int(before.get("failures") or 0)),
    }


def reset_llm_clients():
    """Drop every pooled provider client (closing it) so the next call rebuilds.

    Ops/test hook: production never needs this because the pool identity is
    derived from provider routing plus a credential fingerprint, both stable
    for a process.  Tests that swap the SDK class (``core.OpenAI``) must call
    this so a client built by a previous stub is not reused.
    """
    return _llm_transport.close_all_clients()


def _trace_http_request(request):
    # Runs for each actual HTTP attempt, including SDK retries. A peer's
    # staging failure must prevent transport dispatch after provider-gate exit.
    _c._annotation_fail_stop_check()
    run = trace.get_active_run(_c._runtime_job_id())
    if run:
        run.begin_http_attempt(request)


def _trace_http_response(response):
    run = trace.get_active_run(_c._runtime_job_id())
    if run:
        run.record_http_response(response)


def _provider_error_payload(error):
    """Extract safe, non-secret fields from SDK/http responses.

    OpenAI-compatible relays do not all use the same exception shape: the
    OpenAI SDK exposes ``status_code``/``body`` while httpx exposes a response
    object.  Keep this extraction deliberately small and never return the raw
    exception text to the UI (it may contain request details or credentials).
    """
    response = error if isinstance(error, httpx.Response) else getattr(
        error, "response", None)
    status_code = getattr(error, "status_code", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None

    payloads = []
    body = getattr(error, "body", None)
    if body is not None:
        payloads.append(body)
    if response is not None:
        try:
            payloads.append(response.json())
        except (TypeError, ValueError, AttributeError):
            pass

    code = ""
    message = ""
    period = ""

    def visit(payload):
        nonlocal code, message, period
        if isinstance(payload, str):
            stripped = payload.strip()
            if stripped.startswith(("{", "[")):
                try:
                    visit(json.loads(stripped))
                except (TypeError, ValueError):
                    pass
            return
        if not isinstance(payload, dict):
            return
        if not code:
            for key in ("code", "error_code", "type"):
                value = payload.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    code = str(value).strip()
                    break
        if not message:
            value = payload.get("message") or payload.get("detail")
            if isinstance(value, str) and value.strip():
                message = value.strip()
        if not period:
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                for key in ("limitName", "limit_name", "period", "resetPeriod"):
                    value = metadata.get(key)
                    if isinstance(value, str) and value.strip():
                        period = value.strip().lower()
                        break
        for key in ("error", "detail"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                visit(nested)

    for payload in payloads:
        visit(payload)
    error_text = str(error or "")
    if status_code is None:
        match = re.search(r"\b(401|403|408|409|429|5\d{2})\b", error_text)
        if match:
            status_code = int(match.group(1))
    return {
        "http_status": status_code,
        "code": code,
        "message": message,
        "period": period,
        "text": error_text,
    }


def provider_error_status(error):
    """Return a stable, user-facing status for a provider failure.

    The UI can use the ``status`` value for state styling and ``message`` for
    the copy.  Matching provider codes/messages takes precedence over HTTP
    status because relays commonly use HTTP 403 for both insufficient balance
    and other permission failures.
    """
    info = _provider_error_payload(error)
    http_status = info["http_status"]
    code = info["code"].replace("-", "_").upper()
    searchable = " ".join((info["text"], info["code"], info["message"])).lower()
    status_suffix = f"（HTTP {http_status}）" if http_status else ""

    if (code in {"INSUFFICIENT_BALANCE", "BALANCE_INSUFFICIENT"}
            or "insufficient_balance" in searchable
            or "insufficient balance" in searchable
            or "余额不足" in searchable
            or "balance is too low" in searchable):
        return {
            "status": "insufficient_balance",
            "label": "余额不足",
            "message": "余额不足，请充值或更换 API Key。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    if (http_status == 401 or code in {"INVALID_API_KEY", "UNAUTHORIZED"}
            or "invalid_api_key" in searchable
            or "incorrect api key" in searchable
            or "unauthorized" in searchable):
        return {
            "status": "invalid_api_key",
            "label": "API Key 无效",
            "message": f"API Key 无效或已过期{status_suffix}，请检查密钥和服务商。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    # 额度用尽和"限流"是两件事，必须排在 429 前面判断：限流等一会儿会好，按月计量
    # 的额度不会——重试只是把同一份额度再撞一遍，还把"请稍后重试"这句假话讲给用户
    # （OpenCode Go 用 ``GoUsageLimitError`` + ``limitName: monthly`` 表达这件事）。
    if ("usage limit exceeded" in searchable
            or "gousagelimiterror" in searchable
            or "insufficient_quota" in searchable
            or "quota exceeded" in searchable
            or "额度已用尽" in searchable
            or ("quota" in searchable
                and ("exceed" in searchable or "exhaust" in searchable))):
        period = "本月" if (info.get("period") in {"monthly", "month"}
                            or "monthly" in searchable or "按月" in searchable) else ""
        return {
            "status": "quota_exhausted",
            "label": "额度已用尽",
            # 这里不拼 HTTP 状态：调用方常把它再包一层括号，嵌套括号读起来很别扭，
            # 而"额度用尽"本身就是可操作的信息，429 并不额外说明什么。
            "message": (f"该连接的{period}用量额度已用尽，等一会儿不会恢复；"
                        "请更换连接或提升额度。"),
            "http_status": http_status,
            "provider_code": info["code"],
        }

    if (http_status == 429 or code in {"RATE_LIMITED", "TOO_MANY_REQUESTS"}
            or "rate limit" in searchable or "too many requests" in searchable):
        return {
            "status": "rate_limited",
            "label": "请求受限",
            "message": f"请求过于频繁或已达到服务商限额{status_suffix}，请稍后重试。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    if http_status == 403:
        return {
            "status": "forbidden",
            "label": "访问被拒绝",
            "message": "服务商拒绝了请求（HTTP 403），请检查 API 权限、余额或接口地址。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    if isinstance(error, httpx.RequestError):
        return {
            "status": "network_error",
            "label": "无法连接服务商",
            "message": "无法连接服务商，请检查 API 地址和网络。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    if http_status is not None and http_status >= 500:
        return {
            "status": "provider_unavailable",
            "label": "服务商暂时不可用",
            "message": f"服务商暂时不可用{status_suffix}，请稍后重试。",
            "http_status": http_status,
            "provider_code": info["code"],
        }

    return {
        "status": "unknown",
        "label": "API 请求失败",
        "message": f"API 请求失败{status_suffix}，请检查服务商配置后重试。",
        "http_status": http_status,
        "provider_code": info["code"],
    }


def provider_error_message(error, action="请求失败"):
    """Format a provider failure without leaking raw SDK/relay details."""
    status = provider_error_status(error)
    message = status["message"]
    return f"{action}：{message}" if action else message


def _fetch_gemini_models(api_key):
    """Use Gemini's native, paginated directory instead of an OpenAI route."""
    if not (api_key or "").strip():
        return False, [], "请先填写 API Key", {}
    models = []
    params = {"pageSize": 1000}
    try:
        while True:
            response = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                headers={"x-goog-api-key": api_key.strip(), "Accept": "application/json"},
                params=params, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
            for row in payload.get("models", []):
                name = row.get("name", "").removeprefix("models/")
                if ("generateContent" in row.get("supportedGenerationMethods", [])
                        and name.startswith(("gemini-", "gemma-"))
                        and not any(part in name for part in ("tts", "native-audio"))):
                    models.append(name)
            if not payload.get("nextPageToken"):
                break
            params["pageToken"] = payload["nextPageToken"]
    except httpx.HTTPError as exc:
        return False, [], provider_error_message(exc, "获取模型失败"), {}
    except (ValueError, AttributeError, TypeError):
        return False, [], "获取模型失败：服务商返回了无法解析的响应。", {}
    models = sorted(set(models), key=str.casefold)
    if not models:
        return False, [], "模型目录为空或返回格式不受支持", {}
    return True, models, f"已获取 {len(models)} 个可用模型", {}
