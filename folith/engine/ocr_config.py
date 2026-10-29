"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import hashlib
import json
import os
import re
from folith import ocr_backends as _ocr_backends
from folith import ocr_language as _ocr_language
from folith import ocr_models as _ocr_models
from folith import ocr_online as _ocr_online
from folith.ocr_online import default_vision_reasoning
from folith.textual import has_textual_content
from pathlib import Path
from folith.engine._link import core as _c


OCR_WORKERS_DEFAULT = 2


OCR_QUEUE_SIZE_DEFAULT = 4


OCR_CONFIDENCE_THRESHOLD_DEFAULT = 88.0


OCR_CHECKPOINT_VERSION = 1


# Fixed 220 DPI stays the production baseline for scanned-page OCR.
OCR_DPI_BASELINE = 220


_OCR_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$",
    re.IGNORECASE,
)


def _legacy_select_tesseract_language(installed):
    """Legacy auto-selection, kept only for tasks with no explicit language.

    New tasks always carry a user-selected ``ocr_language``; this path exists so
    an old checkpoint can still resume.  It is never used to override an
    explicit selection, and the caller records a visible warning that no OCR
    language was configured.
    """
    installed = set(installed or [])
    for preferred in (
        ("chi_sim", "eng"),
        ("eng",),
        ("chi_sim",),
        ("jpn", "eng"),
        ("kor", "eng"),
        ("ara", "eng"),
        ("rus", "eng"),
        ("deu", "eng"),
        ("fra", "eng"),
        ("spa", "eng"),
    ):
        if set(preferred).issubset(installed):
            return "+".join(preferred)
    fallback = sorted(installed - {"osd", "snum"})
    return fallback[0] if fallback else ""


def _select_tesseract_language(installed):
    """Deprecated: use an explicit OCR language from the task configuration."""
    return _legacy_select_tesseract_language(installed)


def _effective_ocr_config(ocr_language, ocr_dpi, ocr_psm, saved_config=None,
                          ocr_quality=None):
    """Resolve the effective OCR configuration from UI, state and environment.

    Returns ``(language, dpi, psm, quality, resolved)``.  ``language`` is
    ``None`` only for a legacy task that never had an explicit selection; the
    OCR stage then uses its documented compatibility fallback with a warning.
    """
    saved = saved_config if isinstance(saved_config, dict) else {}
    # An explicit empty string means "not provided" (the UI always sends a key),
    # so it must not override a language already persisted with the task.
    language = (ocr_language if str(ocr_language or "").strip()
                else saved.get("ocr_language"))
    if language is None:
        language = os.environ.get("FOLIOTHREAD_OCR_LANGUAGE", "")
    language = str(language or "").strip() or None

    quality = ocr_quality if ocr_quality is not None else saved.get("ocr_quality")
    quality = _ocr_language.normalize_quality(quality) if quality else ""
    dpi_value = ocr_dpi if ocr_dpi is not None else saved.get("ocr_dpi")
    if dpi_value is None:
        dpi_value = (_ocr_language.dpi_for_quality(quality)
                     if quality else OCR_DPI_BASELINE)
    try:
        dpi_value = int(dpi_value)
    except (TypeError, ValueError):
        dpi_value = OCR_DPI_BASELINE
    dpi_value = max(72, min(dpi_value, 600))
    if not quality:
        quality = _ocr_language.quality_for_dpi(dpi_value)

    psm_value = ocr_psm if ocr_psm is not None else saved.get("ocr_psm")
    try:
        psm_value = int(psm_value) if psm_value is not None else _ocr_language.DEFAULT_PSM
    except (TypeError, ValueError):
        psm_value = _ocr_language.DEFAULT_PSM
    resolved = (_ocr_language.try_resolve_ocr_language(language)
                if language else None)
    return language, dpi_value, psm_value, quality, resolved


def _effective_ocr_source(ocr_backend, saved_config=None, *, ocr_paddle_model=None,
                          ocr_online=None, prefer_saved=False):
    """Resolve the effective OCR *source* configuration for one task run.

    Returns ``(backend_name, paddle_model_id, online_descriptor, online_config)``.
    ``online_descriptor`` is the secret-free snapshot to persist with the task;
    ``online_config`` carries the credential for this run and is never stored.

    Precedence: an explicit caller value, then the value saved with the task,
    then the historical Tesseract default.  ``prefer_saved`` reverses the first
    two for a *resume*, because the OCR source saved with an in-flight task must
    not change underneath it — a rescan is the explicit route to change it.  A
    missing model resource or an incomplete online connection is reported here
    rather than silently degraded.
    """
    saved = saved_config if isinstance(saved_config, dict) else {}

    def _pick(explicit, saved_key, default=None):
        if prefer_saved and saved:
            # A task that is already running keeps its own OCR source; a task
            # stored before this setting existed keeps the historical default.
            value = saved.get(saved_key)
            return value if value is not None else default
        if explicit is not None:
            return explicit
        if saved.get(saved_key) is not None:
            return saved.get(saved_key)
        return default

    backend_name = _ocr_backends.normalize_backend(
        _pick(ocr_backend, "ocr_backend", "tesseract" if saved else "online"))
    model_id = str(_pick(ocr_paddle_model, "ocr_paddle_model", "") or "").strip() \
        or _ocr_models.OFFICIAL_MODEL_ID
    option = _ocr_models.try_resolve(model_id)
    if option is None:
        raise ValueError(f"找不到已注册的 Paddle 模型「{model_id}」；请重新选择模型。")
    model_id = option.model_id
    descriptor_source = _pick(ocr_online, "ocr_online")
    if backend_name not in ("online", _ocr_backends.OCR_API_BACKEND):
        return backend_name, model_id, (descriptor_source
                                        if isinstance(descriptor_source, dict) else None), None
    # Both "online" (VLM) and "ocr_api" use the same online-connection descriptor.
    # The backend class differs: OnlineOCRBackend vs. OcrApiBackend.
    config, error = resolve_ocr_online_config(descriptor_source)
    if error is not None or (config is not None and config.unavailable_reason()):
        # Native sources need no remote credentials. Validate only when a page
        # actually requires visual understanding, rather than blocking DOCX or
        # native PDF translation because the visual profile is not configured.
        descriptor = descriptor_source if isinstance(descriptor_source, dict) else {}
        return backend_name, model_id, descriptor, descriptor
    return backend_name, model_id, config.describe(), config


def _resolve_ocr_language_config(ocr_language, installed):
    """Resolve the requested OCR language against the installed packs.

    Returns ``(resolved, warnings, error)``.  ``error`` is a blocking report when
    the requested pack is missing — the caller must not fall back to another
    language, because that produces plausible-looking but poisoned OCR text.
    """
    installed_set = set(installed or [])
    if str(ocr_language or "").strip():
        resolved = _ocr_language.resolve_ocr_language(ocr_language)
        report = _ocr_language.validate_language_packs(
            resolved.engine_code, installed_set)
        if not report["ok"]:
            return resolved, [], {
                "error": "language_pack_missing",
                "message": _ocr_language.missing_pack_message(
                    resolved.engine_code, installed_set),
                "missing": list(report["missing"]),
                "installed_languages": sorted(installed_set),
                **resolved.to_dict(),
            }
        return resolved, [], None
    # Legacy task with no explicit OCR language.  Choose the old default but say
    # so out loud; a newly created task never reaches this branch.
    legacy_code = _legacy_select_tesseract_language(installed_set)
    if not legacy_code:
        return None, [], {
            "error": "no_language_pack",
            "message": ("Tesseract 未找到可用语言包（至少需要 eng 或 chi_sim），"
                        "无法执行 OCR。"),
            "installed_languages": sorted(installed_set),
        }
    resolved = _ocr_language.resolve_ocr_language(legacy_code)
    warning = (
        f"该任务未显式设置 OCR 语言，已按旧任务兼容规则使用 {resolved.label}"
        f"（{resolved.engine_code}）。如需其他语言，请重新扫描并选择 OCR 语言。")
    return resolved, [warning], None


def _ocr_workers_default():
    try:
        configured = int(os.environ.get("FOLIOTHREAD_OCR_WORKERS", ""))
    except ValueError:
        configured = OCR_WORKERS_DEFAULT
    return max(1, min(configured or OCR_WORKERS_DEFAULT, 8))


def _ocr_queue_size_default(workers):
    try:
        configured = int(os.environ.get("FOLIOTHREAD_OCR_QUEUE_SIZE", ""))
    except ValueError:
        configured = OCR_QUEUE_SIZE_DEFAULT
    return max(1, min(configured or workers * 2, 32))


def _ocr_runtime_metadata(language, workers, backend=None, command="",
                          paddle_model=None, online_config=None):
    """Describe the provider actually executing OCR, not only a config value."""
    resolved, resolve_warnings = _ocr_backends.resolve_backend(
        backend, tesseract_command=command, language=language,
        paddle_model=paddle_model, online_config=online_config)
    info = resolved.runtime_info(language).to_dict()
    metadata = {**info, "language": language, "workers": workers}
    warnings = list(resolve_warnings)
    configured_device = str(os.environ.get("FOLIOTHREAD_OCR_DEVICE", "auto") or "auto").lower()
    if configured_device not in {"", "auto", "cpu"} and metadata.get("device") == "cpu":
        warnings.append(
            f"OCR 配置请求 device={configured_device}，但当前 "
            f"{metadata.get('backend')} runtime 实际使用 CPU；未发生 GPU 加速。"
        )
    return metadata, warnings


def ocr_engine_status(engine, language="eng", *, paddle_model=None, online_config=None):
    """Availability report for one OCR source.

    Configuration only: the online source is never probed with a billable
    recognition request.
    """
    name = _ocr_backends.normalize_backend(engine)
    if name == "paddle":
        backend = _ocr_backends.PaddleOCRBackend(language, model_id=paddle_model)
        status = backend.status()
        return {**status, "ok": backend.available(), "capabilities": backend.capabilities(),
                "source": "paddle",
                "quality_note": backend.quality_note() if status["state"] != "unsupported" else ""}
    if name in ("online", _ocr_backends.OCR_API_BACKEND):
        status = ocr_online_status(online_config)
        return {**status, "capabilities": {"line_boxes": False, "word_boxes": False,
                                           "geometry": False},
                "quality_note": ("VLM 提取原文结构；本地文字框提供尽力而为的高亮，无法匹配时只定位到原页。"
                                 if name == "online" else "OCR API 提取文本；随后整理原文结构。")}
    if name == "auto" and not _c._find_tesseract():
        status = ocr_engine_status("paddle", language, paddle_model=paddle_model)
        return {**status, "message": "Auto：Tesseract 未安装；将使用 PaddleOCR。 " + status.get("message", "")}
    return ocr_language_status(language)


def ocr_backend_report():
    """Diagnostics: which sources exist and what will really execute them."""
    command = _c._find_tesseract()
    version = _c._ocr_backend_version("tesseract", command) if command else ""
    return _ocr_backends.backend_report(
        tesseract_command=command, tesseract_version=version,
        online_config=resolve_ocr_online_config()[0],
        paddle_models=_ocr_backends.paddle_model_report())


def ocr_language_options():
    """Selectable OCR languages for the task UI (single + explicit pairs)."""
    return _ocr_language.ocr_language_options()


def ocr_quality_options():
    """Selectable scan-quality profiles (Standard / High / Historical)."""
    return _ocr_language.quality_options()


def ocr_quality_dpi(quality):
    """Map a named scan-quality profile to its rasterization DPI."""
    return _ocr_language.dpi_for_quality(quality)


def ocr_language_status(ocr_language):
    """Availability report for one OCR language selection (for the UI)."""
    installed = _c.installed_ocr_languages()
    resolved = _ocr_language.try_resolve_ocr_language(ocr_language)
    if resolved is None:
        return {"ok": False, "message": "请选择扫描文字语言。",
                "installed_languages": installed}
    report = _ocr_language.validate_language_packs(
        resolved.engine_code, installed)
    return {
        **report,
        "label": resolved.label,
        "direction": resolved.direction,
        "message": ("" if report["ok"]
                    else _ocr_language.missing_pack_message(
                        resolved.engine_code, installed)),
    }


def _ocr_checkpoint_path(checkpoint_dir):
    """Legacy v1 checkpoint location, kept only for one-time adoption."""
    return Path(checkpoint_dir) / "ocr_checkpoint.json" if checkpoint_dir else None


def _ocr_cache_dir(checkpoint_dir):
    """Page-granular cache root (manifest + one file per page)."""
    return Path(checkpoint_dir) / "ocr_cache" if checkpoint_dir else None


#: Shared cache root for the quick-profile representative pages.  Profiling runs
#: before a job exists, so it cannot write into the job directory; the formal
#: pipeline reads this directory in addition to its own job-scoped cache.
OCR_PROFILE_CACHE_DIRNAME = "ocr_profile_cache"


def ocr_profile_cache_dir(source_bytes=None, *, source_sha256=None):
    """Content-addressed cache for the quick-profile representative pages.

    Returns ``None`` when no source is given, so callers degrade to "no shared
    cache" instead of silently writing to a global bucket.  Page files are keyed
    by the full OCR identity (which includes the document SHA-256), so two
    documents can never mix even though they share this root.
    """
    digest = str(source_sha256 or "").strip()
    if not digest and source_bytes:
        digest = hashlib.sha256(source_bytes).hexdigest()
    if not digest:
        return None
    return _c.OUTPUT_DIR / OCR_PROFILE_CACHE_DIRNAME / digest[:16]


def _ocr_env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_OCR_BACKEND_VERSION_CACHE = {}


# The online OCR connection is deliberately separate from the translation model
# configuration: switching the translator must never change which service reads
# the page images.  A task may *reference* the saved translation credentials, but
# the OCR profile has its own file and its own model.
OCR_ONLINE_CONFIG_FILE = "ocr_online_config.json"


OCR_ONLINE_CREDENTIAL_SOURCES = ("ocr_profile", "translation_profile")


OCR_ONLINE_DEFAULT_MAX_TOKENS = _ocr_online.DEFAULT_MAX_TOKENS


OCR_ONLINE_MIN_MAX_TOKENS = _ocr_online.MIN_MAX_TOKENS


OCR_ONLINE_MAX_MAX_TOKENS = _ocr_online.MAX_MAX_TOKENS


OCR_ONLINE_REASONING_MODES = tuple(_ocr_online.REASONING_MODES)


OCR_ONLINE_CREDENTIAL_LABELS = {
    "ocr_profile": "使用 OCR 专用凭据",
    "translation_profile": "引用已保存的翻译模型凭据",
}


def ocr_online_config_path():
    return _c.OUTPUT_DIR / OCR_ONLINE_CONFIG_FILE


def save_ocr_online_config(*, provider="", model="", api_key="", base_url=None,
                           max_tokens=_ocr_online.DEFAULT_MAX_TOKENS,
                           temperature=0.0, reasoning_mode="provider_default",
                           timeout_s=_ocr_online.DEFAULT_TIMEOUT_S,
                           max_retries=_ocr_online.DEFAULT_MAX_RETRIES):
    """Persist the OCR-only connection (0600), mirroring provider_config.json."""
    _c._ensure_output_dir()
    normalized = _ocr_online.OnlineOCRConfig(
        provider=provider, model=model, api_key=api_key, base_url=base_url or "",
        max_tokens=max_tokens, temperature=temperature,
        reasoning_mode=reasoning_mode, timeout_s=timeout_s,
        max_retries=max_retries).normalized()
    payload = {
        "provider": normalized.provider,
        "model": normalized.model,
        "api_key": normalized.api_key,
        "base_url": _c.normalize_openai_base_url(normalized.base_url) or "",
        "max_tokens": normalized.max_tokens,
        "temperature": normalized.temperature,
        "reasoning_mode": normalized.reasoning_mode,
        "timeout_s": normalized.timeout_s,
        "max_retries": normalized.max_retries,
    }
    path = ocr_online_config_path()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)
    return path


def _resolve_online_ocr_base_url(provider, base_url):
    base = str(base_url or "").strip()
    if provider and provider in _c.PROVIDERS:
        return _c.resolve_openai_base_url(provider, base or None)
    normalized = _c.normalize_openai_base_url(base)
    if not normalized:
        raise ValueError("线上 OCR 缺少有效的服务地址（需要 http(s)://…/v1）")
    return normalized


def _endpoint_id(provider, base_url):
    try:
        resolved = _resolve_online_ocr_base_url(provider, base_url)
    except ValueError:
        return None
    return f"{provider}|{resolved.rstrip('/')}"


def saved_credentials():
    """Every saved credential with the endpoint it belongs to.

    ``[(origin, provider, model, endpoint_id, api_key)]`` from the translation
    profile, each saved connection and the OCR-only profile.  A key is only ever
    paired with the endpoint of the entry it came from.
    """
    from folith.model_connections import environment_key
    rows = []
    config = _c.load_provider_config() or {}

    def add(origin, entry):
        provider = str(entry.get("provider") or "")
        if not provider:
            return
        endpoint = _endpoint_id(provider, entry.get("base_url"))
        key = str(entry.get("api_key") or "") or environment_key(provider)
        if endpoint:
            rows.append((origin, provider, str(entry.get("model") or ""), endpoint, key))

    add("translation_profile", config)
    for connection in config.get("connections") or []:
        if isinstance(connection, dict):
            add("connection", connection)
    add("ocr_profile", _c.load_ocr_online_config() or {})
    return rows


def credential_for_endpoint(provider, resolved_base, prefer=""):
    """``(api_key, owner_endpoint_id)`` of the saved connection owning this endpoint, else ``None``."""
    target = f"{provider}|{str(resolved_base or '').rstrip('/')}"
    rows = [row for row in saved_credentials() if row[3] == target and row[4]]
    rows.sort(key=lambda row: 0 if row[0] == prefer else 1)
    return (rows[0][4], rows[0][3]) if rows else None


def _vision_reasoning_mode(source, provider, model, recorded):
    """Recorded mode, else the model's default.  When vision simply follows the
    translation connection there is no vision-side setting, so a recorded
    ``provider_default`` is only an earlier default, not a choice."""
    fallback = default_vision_reasoning(provider, model)
    recorded = str(recorded or "").strip()
    if not recorded or (source == "translation_profile" and recorded == "provider_default"):
        return fallback
    return recorded


def follow_connection_thinking(source, provider, model, recorded):
    """Vision reasoning mode when it rides on the translation connection.

    The switch is live user intent on that connection, so it beats the mode a
    task snapshot recorded earlier; it only applies to a model whose endpoint
    was verified to honour it, and "服务默认" can undo a recorded opt-in.
    """
    if source != "translation_profile":
        return recorded
    live = _c._connection_thinking_mode()
    if live in _c.thinking_control_options(provider, model):
        return live
    if live == "provider_default" and recorded in _c.thinking_control_options(provider, model):
        return live
    return recorded


def resolve_ocr_online_config(saved=None, *, descriptor=None):
    """Build the runtime online OCR config from a task descriptor + credentials.

    ``descriptor`` is the secret-free snapshot persisted with the task.  The
    credential itself always comes from disk, so it is never written into job
    state, the cache identity, logs or reports.

    Key and endpoint always belong to the same saved connection: when the task
    pins a provider/endpoint, the key is looked up from the saved connection
    that owns *that* endpoint — never from whichever connection is current.  If
    no saved connection owns it, the result is a ``connection_missing`` error.
    """
    snapshot = descriptor if isinstance(descriptor, dict) else (
        saved if isinstance(saved, dict) else {})
    default_source = "ocr_profile" if _c.load_ocr_online_config() else "translation_profile"
    source = str(snapshot.get("credential_source") or default_source)
    if source not in OCR_ONLINE_CREDENTIAL_SOURCES:
        source = "ocr_profile"
    profile = (_c.load_provider_config() or {} if source == "translation_profile"
               else _c.load_ocr_online_config() or {})
    pinned = bool(snapshot.get("provider") or snapshot.get("base_url"))
    provider = str(snapshot.get("provider") or profile.get("provider") or "")
    model = str(snapshot.get("model") or profile.get("model") or "")
    base_url = str(snapshot.get("base_url") or profile.get("base_url") or "")
    try:
        resolved_base = _resolve_online_ocr_base_url(provider, base_url)
    except ValueError as exc:
        return None, {"error": "invalid_base_url", "message": str(exc)}
    owner = f"{provider}|{resolved_base.rstrip('/')}"
    api_key = str(profile.get("api_key") or "")
    if pinned:
        found = credential_for_endpoint(provider, resolved_base, prefer=source)
        if found is None and not (profile.get("provider") == provider and not snapshot.get("base_url")):
            return None, {"error": "connection_missing",
                          "message": (f"这个任务使用的连接「{provider} · {model}」（{resolved_base}）"
                                      "已不在已保存的连接里，无法取得它的访问凭据。")}
        api_key = found[0] if found else api_key
        owner = found[1] if found else owner
    config = _ocr_online.OnlineOCRConfig(
        provider=provider,
        base_url=resolved_base,
        model=model,
        api_key=api_key,
        max_tokens=int(snapshot.get("max_tokens") or profile.get("max_tokens")
                       or _ocr_online.DEFAULT_MAX_TOKENS),
        temperature=float(snapshot.get("temperature")
                          if snapshot.get("temperature") is not None
                          else profile.get("temperature") or 0.0),
        reasoning_mode=follow_connection_thinking(
            source, provider, model,
            _vision_reasoning_mode(
                source, provider, model,
                str(snapshot.get("reasoning_mode") or profile.get("reasoning_mode") or ""))),
        prompt_version=str(snapshot.get("prompt_version")
                           or _ocr_online.ONLINE_PROMPT_VERSION),
        timeout_s=float(snapshot.get("timeout_s") or profile.get("timeout_s")
                        or _ocr_online.DEFAULT_TIMEOUT_S),
        max_retries=int(snapshot.get("max_retries") if snapshot.get("max_retries") is not None
                        else profile.get("max_retries") or _ocr_online.DEFAULT_MAX_RETRIES),
        proxy_bypass=bool((_c.PROVIDERS.get(provider) or {}).get("proxy_bypass")),
        credential_source=source,
        credential_owner=owner,
    ).normalized()
    return config, None


def vision_connection_label(config_or_descriptor):
    value = config_or_descriptor
    get = value.get if isinstance(value, dict) else (lambda k, d="": getattr(value, k, d))
    return " · ".join(str(get(key) or "") for key in ("provider", "model") if get(key))


def vision_connection_conflict(saved_pipeline_config):
    """Compare the connection pinned in a task with what the vision role resolves to now.

    Returns ``None`` when there is nothing to decide (no pinned online connection, no
    current one, or both are the same endpoint).  Otherwise a dict with labels, the two
    endpoint ids, whether the pinned connection still exists among the saved ones
    (``pinned_exists``) and the recorded choice if it still applies.
    """
    saved = saved_pipeline_config if isinstance(saved_pipeline_config, dict) else {}
    pinned = saved.get("ocr_online")
    if not isinstance(pinned, dict) or not (pinned.get("provider") and pinned.get("base_url")):
        return None
    current, error = resolve_ocr_online_config(None)
    if error is not None or current is None or current.unavailable_reason():
        return None
    pinned_endpoint = _endpoint_id(str(pinned.get("provider")), pinned.get("base_url"))
    if pinned_endpoint is None or pinned_endpoint == current.endpoint_id():
        return None
    pinned_config, pinned_error = resolve_ocr_online_config(pinned)
    choice = saved.get("ocr_online_choice") if isinstance(saved.get("ocr_online_choice"), dict) else {}
    applies = (choice.get("pinned") == pinned_endpoint
               and choice.get("current") == current.endpoint_id())
    return {
        "pinned_label": vision_connection_label(pinned),
        "current_label": vision_connection_label(current),
        "pinned_id": pinned_endpoint,
        "current_id": current.endpoint_id(),
        "pinned_exists": pinned_error is None and bool(pinned_config and pinned_config.api_key),
        "choice": str(choice.get("choice") or "") if applies else "",
        "current_descriptor": current.describe(),
    }


def ocr_online_status(saved=None):
    """Configuration-only status for the settings UI.

    Never issues a recognition request: checking a connection must not cost
    money or send a page image anywhere.
    """
    config, error = resolve_ocr_online_config(saved)
    if error is not None:
        return {"ok": False, "state": "unconfigured", "message": error["message"],
                "source": _ocr_online.ONLINE_OCR_BACKEND}
    if config is None:
        return {"ok": False, "state": "unconfigured", "message": "线上 OCR 未配置。",
                "source": _ocr_online.ONLINE_OCR_BACKEND}
    reason = config.unavailable_reason()
    return {
        "ok": not reason,
        "state": "configured" if not reason else "unconfigured",
        "message": reason or "已配置；识别时会实际请求该服务。",
        "source": _ocr_online.ONLINE_OCR_BACKEND,
        **config.describe(),
    }


def ocr_source_options():
    """The three OCR sources the task UI offers, in the documented order."""
    return [
        {"value": "tesseract", "label": "Tesseract",
         "description": "本机 Tesseract；默认来源，无需额外运行时。"},
        {"value": "paddle", "label": "PaddleOCR",
         "description": "本机 PP-OCRv5，可选任务级模型（含阿拉伯语微调模型）。"},
        {"value": "online", "label": "线上 OCR API",
         "description": "OpenAI 兼容图像转写接口；页面图像会发送到所配置的服务。"},
        {"value": "auto", "label": "自动（仅本地）",
         "description": "优先 Tesseract；未安装时回退 PaddleOCR。不会自动使用线上服务。"},
    ]


def ocr_source_label(value):
    name = _ocr_backends.normalize_backend(value)
    return {"tesseract": "Tesseract", "paddle": "PaddleOCR",
            "online": "线上 OCR API", "auto": "自动（仅本地）"}.get(name, name)


def paddle_model_options():
    """Selectable PaddleOCR model sets (official + locally exported models)."""
    return _ocr_models.model_options()


def ocr_paddle_model_status(model_id, language="eng"):
    """Availability of one Paddle model set for one OCR language."""
    option = _ocr_models.try_resolve(model_id)
    if option is None:
        return {"ok": False, "state": "model_unavailable",
                "message": f"找不到已注册的 Paddle 模型「{model_id}」。",
                "model_id": str(model_id or "")}
    language_ok = _ocr_models.language_supported(option, language)
    backend = _ocr_backends.PaddleOCRBackend(language, model_id=option.model_id)
    status = backend.status()
    if not language_ok:
        return {**status, "ok": False, "model_id": option.model_id,
                "language_supported": False,
                "message": (f"模型「{option.label}」只在"
                            f"{_ocr_models.language_scope_label(option)}上验证过，"
                            "不能用于当前 OCR 语言。")}
    return {**status, "ok": backend.available(), "model_id": option.model_id,
            "language_supported": True,
            "language_scope": _ocr_models.language_scope_label(option),
            "resource_dir": str(_ocr_models.resource_base(option))}


def _ocr_pdf_text(file_bytes, max_pages=3):
    """Compatibility wrapper used by the quick-profile path."""
    text, _warnings = _c._ocr_pdf_text_with_warnings(file_bytes, max_pages=max_pages)
    return text


def _ocr_text_to_paragraphs(text, direction="ltr"):
    """Turn Tesseract page/block output into translation-ready paragraphs."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", str(text or "")):
        lines = [_c.clean_xml_chars(line.strip())
                 for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        merged = lines[0]
        for line in lines[1:]:
            # Hyphenation repair is a cased-script (English) rule; uncased RTL
            # scripts never match ``islower()`` and fall through to a space join.
            if (str(direction or "ltr") != "rtl" and merged.endswith("-")
                    and line[:1].islower()):
                merged = merged[:-1] + line
            elif re.search(r"[\u3400-\u9fff]$", merged) or \
                    re.match(r"[\u3400-\u9fff]", line):
                merged += line
            else:
                merged += " " + line
        if len(merged) > 1 and has_textual_content(merged):
            paragraphs.append(merged)
    return paragraphs


#: OCR ingestion modes.  ``auto`` trusts the text layer only when it passes the
#: quality gate; ``force`` ignores the text layer body and always OCRs; ``off``
#: never OCRs.  The mode is a user decision and is never overridden by the gate.
OCR_MODES = ("auto", "force", "off")


DEFAULT_OCR_MODE = "auto"


_OCR_MODE_ALIASES = {
    "auto": "auto", "automatic": "auto", "自动": "auto", "自动检测": "auto",
    "force": "force", "forced": "force", "force_ocr": "force",
    "强制": "force", "强制ocr": "force", "强制识别": "force",
    "off": "off", "none": "off", "text": "off", "text_layer": "off",
    "text_only": "off", "text_layer_only": "off", "关闭": "off",
    "仅文本层": "off", "不ocr": "off",
}


def normalize_ocr_mode(value, default=DEFAULT_OCR_MODE):
    """Resolve a user/configured OCR mode without allowing an unknown value."""
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in _OCR_MODE_ALIASES:
        return _OCR_MODE_ALIASES[text]
    for alias, mode in _OCR_MODE_ALIASES.items():
        if alias and alias in text:
            return mode
    return default


def ocr_mode_options():
    """Selectable OCR modes for the task UI."""
    return [
        {"value": "auto", "label": "Auto", "label_zh": "自动（推荐）",
         "help": "文本层通过质量检测才直接使用，否则自动 OCR。"},
        {"value": "force", "label": "Force OCR", "label_zh": "强制 OCR",
         "help": "忽略 PDF 文本层正文，始终重新 OCR（历史扫描/隐藏坏文本层）。"},
        {"value": "off", "label": "Text layer only", "label_zh": "仅文本层",
         "help": "显式不 OCR；适用于数字原生 PDF 或调试。"},
    ]
