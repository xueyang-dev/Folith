"""The shared, frontend-neutral job configuration contract.

Why this module exists
----------------------
Before architecture Phase 1 the two Folith frontends assembled the worker
keyword dictionary on their own:

* Streamlit web app  → ``app.py::_pipeline_kwargs``
* Qt desktop client  → ``folith/qt/adapter.py::pipeline_kwargs``

Both produced a dictionary for :func:`core.run_job_pipeline` (via
``core.start_job_worker`` / ``core.resume_job``), and the two builders had
already diverged — different resume precedence, ten fields Streamlit sent that
Qt never sent, and a file admission list that advertised formats the core
cannot ingest.  This module is now the single authority for that contract:

.. code-block:: text

    frontend UI values (plain mapping)
              ↓
    JobConfig.from_frontend(values, state=job_state)
              ↓
    JobConfig      — validated, normalized, resume-resolved, typed
              ↓
    JobConfig.to_pipeline_kwargs()
              ↓
    core.run_job_pipeline / core.start_job_worker / core.resume_job

``core.run_job_pipeline`` is deliberately *unchanged* and remains backward
compatible: this module only ever produces its keyword arguments.

Design rules
------------
* No framework dependency — plain :mod:`dataclasses` and the standard library.
* Frontends translate their own widgets into a plain mapping; they never decide
  defaults, resume precedence, normalization or validation.
* ``None`` is meaningful for the two tri-state fields the core already treats
  that way (``enable_understanding``, ``enable_source_cleanup``): it means
  "apply the core's documented rule", which is not the same as ``False``.
* Only rules the product already has are enforced; this is not a validation
  framework.  Every field has a documented origin; see
  ``tests/application_job_config_test.py`` for the coverage oracle against the
  core signature.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields as _dataclass_fields
import json
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants mirrored from the core runtime.
#
# They are duplicated (rather than imported) so that importing this module does
# not drag in the very large ``core`` module, and so that the frontends can be
# imported in any order.  ``tests/application_job_config_test.py`` asserts each
# of them still agrees with ``core``; that test is the drift guard.
# ---------------------------------------------------------------------------

#: Default target language (``core.DEFAULT_TARGET_LANG``).
DEFAULT_TARGET_LANG = "简体中文"

#: Default OCR mode (``core.DEFAULT_OCR_MODE``) — ``auto`` / ``force`` / ``off``.
DEFAULT_OCR_MODE = "auto"

#: Default OCR quality profile (``folith.ocr_language.DEFAULT_OCR_QUALITY``).
DEFAULT_OCR_QUALITY = "standard"

#: Default OCR source for a *new* task.  The core's own fallback for a saved
#: task is ``tesseract``; both frontends offer the online source by default.
DEFAULT_OCR_BACKEND = "online"

#: Default PaddleOCR model set (``core`` resolves the same official id).
DEFAULT_PADDLE_MODEL = "official"

#: Default batch profile ("保守") used by every frontend preset.
DEFAULT_BATCH_SIZE = 4
DEFAULT_MAX_BATCH_CHARS = 2400

#: Default translation concurrency used by every frontend preset.  The core's
#: own fallback for callers that omit the value is 1; the shared contract always
#: sends the frontend default so behaviour stays identical on both surfaces.
DEFAULT_TRANSLATION_CONCURRENCY = 4

#: Default segmentation mode (``core`` default is ``sentence``).
DEFAULT_SEGMENTATION_MODE = "sentence"

#: Source formats the core can turn into paragraphs. File
#: admission has exactly one authority — this tuple — so no surface can advertise
#: a format the engine cannot ingest.
SOURCE_SUFFIXES: Tuple[str, ...] = (".pdf", ".docx", ".epub", ".txt", ".md", ".markdown")

#: A single document larger than this is refused up front instead of being read
#: into memory and failing deep inside the pipeline.
MAX_SOURCE_BYTES = 512 * 1024 * 1024

# ---------------------------------------------------------------------------
# The coverage oracle.
#
# ``tests/application_job_config_test.py`` asserts:
#
#   set(core.run_job_pipeline signature)
#       == JOB_CONFIG_KWARG_KEYS | OPTIONAL_REVIEWER_KWARG_KEYS
#          | INTERNAL_CORE_OPTIONS
#
# so a new core keyword cannot be added without deciding whether the shared
# frontend contract owns it.
# ---------------------------------------------------------------------------

#: Keywords ``to_pipeline_kwargs`` always emits.
JOB_CONFIG_KWARG_KEYS = frozenset({
    "provider", "api_key", "model", "translator_base_url", "reasoning_effort",
    "target_lang", "auto_term", "translation_theory", "user_glossary",
    "style_rules", "enable_review", "enable_annotate", "enable_report",
    "use_tm", "enable_understanding", "enable_source_cleanup",
    "strict_terminology_governance", "segmentation_mode",
    "translation_concurrency", "batch_size", "max_batch_chars",
    "batch_strategy",
    "knowledge_feedback",
    "ocr_language", "ocr_quality", "ocr_dpi", "ocr_psm", "ocr_mode",
    "ocr_backend", "ocr_paddle_model", "ocr_online",
    "research_settings", "literature_sources", "delivery_config",
})

#: Keywords emitted only when a separate review model is usable.
OPTIONAL_REVIEWER_KWARG_KEYS = frozenset({
    "reviewer_provider", "reviewer_model", "reviewer_api_key",
    "reviewer_base_url",
})

#: Core keywords that intentionally stay outside the frontend job contract.
#: They are legacy (``mode``), role-routing, per-run runtime tuning, or
#: callbacks; no product surface sets them today, and migrating them is not part
#: of Phase 1.
INTERNAL_CORE_OPTIONS = frozenset({
    "mode", "annotation_scheduler",
    "auxiliary_provider", "auxiliary_model", "auxiliary_api_key",
    "auxiliary_base_url",
    "knowledge_feedback_interval",
    "source_cleanup_concurrency", "source_cleanup_confidence_threshold",
    "source_cleanup_max_retries", "source_cleanup_request_interval",
    "ocr_workers", "ocr_queue_size",
    "reasoning_mode", "auxiliary_reasoning_mode",
    "on_status", "on_caption",
})

#: ``JobConfig`` members that are contract state rather than core keywords.
NON_KWARG_FIELDS = frozenset({"source_suffix", "reviewer_mode"})

#: Every field name of :class:`JobConfig`.
JOB_CONFIG_FIELDS = frozenset(
    JOB_CONFIG_KWARG_KEYS | OPTIONAL_REVIEWER_KWARG_KEYS | NON_KWARG_FIELDS
)


class JobConfigError(ValueError):
    """The requested job configuration is impossible and must not be started."""


class _Missing:
    """Sentinel for "no value was supplied by any layer"."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<missing>"

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()


def normalize_source_suffix(value: Any) -> str:
    """Return the lower-cased, dot-prefixed suffix of ``value`` (or ``""``)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    tail = text.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    index = tail.rfind(".")
    if index < 0:
        return ""
    return tail[index:]


def is_supported_source(filename_or_suffix: Any) -> bool:
    """True when the core can ingest this filename/suffix."""
    return normalize_source_suffix(filename_or_suffix) in SOURCE_SUFFIXES


def source_suffix_problem(filename_or_suffix: Any) -> str:
    """A user-facing reason, or ``""`` when the suffix is ingestible."""
    suffix = normalize_source_suffix(filename_or_suffix)
    supported = "、".join(SOURCE_SUFFIXES)
    if not suffix:
        return f"无法识别文件类型；当前只支持 {supported} 文件。"
    if suffix not in SOURCE_SUFFIXES:
        return f"暂不支持 {suffix} 文件；当前只能导入 {supported} 文件。"
    return ""


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"", "0", "false", "no", "off", "none", "null"}:
            return False
        if text in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _as_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _as_sequence(value: Any) -> Optional[Tuple[Any, ...]]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "" or value is _MISSING:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise JobConfigError(f"无法把 {value!r} 解析为整数。")


@dataclass(frozen=True)
class JobConfig:
    """The validated, frontend-neutral configuration of one translation job.

    Instances are built by :meth:`from_frontend` (which owns defaults, resume
    precedence and normalization) and consumed through
    :meth:`to_pipeline_kwargs` (which owns the compatibility conversion to the
    unchanged core keyword API).
    """

    # -- model connection -------------------------------------------------
    provider: str = ""
    model: str = ""
    api_key: str = ""
    translator_base_url: Optional[str] = None
    reasoning_effort: str = ""

    # -- separate review model -------------------------------------------
    reviewer_mode: str = "same"          # "same" | "separate"
    reviewer_provider: str = ""
    reviewer_model: str = ""
    reviewer_api_key: str = ""
    reviewer_base_url: str = ""

    # -- task definition --------------------------------------------------
    target_lang: str = DEFAULT_TARGET_LANG
    auto_term: bool = True
    translation_theory: str = ""
    user_glossary: Tuple[Any, ...] = ()
    style_rules: str = ""
    enable_review: bool = True
    enable_annotate: bool = True
    enable_report: bool = False
    use_tm: bool = True
    # ``None`` = "apply the core's documented rule" (see module docstring).
    enable_understanding: Optional[bool] = None
    enable_source_cleanup: Optional[bool] = None
    strict_terminology_governance: bool = False

    # -- segmentation and batching ---------------------------------------
    segmentation_mode: str = DEFAULT_SEGMENTATION_MODE
    translation_concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY
    batch_size: int = DEFAULT_BATCH_SIZE
    max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS
    #: Explicit batching strategy (Phase 3 contract).  The desktop entry always
    #: resolves this to a concrete value — ``sentence`` matches the historic
    #: behavior where ``batch_size``/``max_batch_chars`` were always supplied.
    #: This is deliberately different from ``core.DEFAULT_BATCH_STRATEGY``
    #: (``paragraph``), which stays the core-level default for new tasks that
    #: do not come through the desktop contract.  A task's own saved strategy
    #: wins at the runtime; this field never flips an already-started job's
    #: batch plan.
    batch_strategy: str = "sentence"
    #: per-batch knowledge feedback (name continuity, term candidates); 快速 is off
    knowledge_feedback: bool = True

    # -- OCR / visual source ---------------------------------------------
    ocr_language: str = ""
    ocr_quality: str = DEFAULT_OCR_QUALITY
    ocr_dpi: Optional[int] = None
    ocr_psm: Optional[int] = None
    ocr_mode: str = DEFAULT_OCR_MODE
    ocr_backend: str = DEFAULT_OCR_BACKEND
    ocr_paddle_model: str = DEFAULT_PADDLE_MODEL
    ocr_online: Optional[Dict[str, Any]] = None

    # -- research products ------------------------------------------------
    research_settings: Optional[Dict[str, Any]] = None
    literature_sources: Optional[Tuple[Any, ...]] = None

    # -- delivery ---------------------------------------------------------
    delivery_config: Dict[str, Any] = field(default_factory=dict)

    # -- admission (contract state, not a core keyword) -------------------
    source_suffix: str = ""

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    @classmethod
    def from_frontend(
        cls,
        values: Optional[Mapping[str, Any]] = None,
        *,
        state: Optional[Mapping[str, Any]] = None,
    ) -> "JobConfig":
        """Resolve one effective configuration from frontend values and job state.

        Thin compatibility entry point: the precedence policy itself lives in
        :func:`folith.application.settings.resolve_effective_settings`, which
        every frontend now feeds with *named layers* (explicit choice, stored
        user defaults, provider profile, persisted job state, environment).
        Here the live values are one layer and the job state is another, which is
        exactly what the pre-Phase-4 callers passed.
        """
        from folith.application.settings import SettingsSources, resolve_effective_settings

        return cls.from_settings(resolve_effective_settings(SettingsSources(
            explicit=dict(values or {}),
            job_state=dict(state or {}) if isinstance(state, Mapping) else {},
        )))

    @classmethod
    def from_settings(cls, effective) -> "JobConfig":
        """Build the execution contract from resolved effective settings.

        Everything here is contract-level work: normalization, explicit
        validation and the cross-field rules (reviewer endpoint inheritance,
        reasoning-effort support, the academic "report stays on" rule, delivery
        normalization).  Which value *won* was already decided by the settings
        resolver.
        """
        import core  # local import: keeps this module import-order independent

        values = dict(getattr(effective, "values", {}) or {})
        state = dict(getattr(effective, "state", {}) or {})
        saved_raw = state.get("pipeline_config")
        saved: Dict[str, Any] = dict(saved_raw) if isinstance(saved_raw, Mapping) else {}
        academic_raw = state.get("academic_state")
        academic: Dict[str, Any] = (dict(academic_raw)
                                    if isinstance(academic_raw, Mapping) else {})

        # -- connection ------------------------------------------------------
        provider = _as_text(values.get("provider")).strip()
        model = _as_text(values.get("model")).strip()
        api_key = _as_text(values.get("api_key"))
        base_url = _as_text(values.get("translator_base_url")).strip() or None

        # -- reviewer model --------------------------------------------------
        reviewer_mode = _as_text(values.get("reviewer_mode") or "same").strip().lower()
        if reviewer_mode not in {"same", "separate"}:
            raise JobConfigError(f"未知的审校模型模式：{reviewer_mode!r}")
        reviewer_provider = _as_text(values.get("reviewer_provider")).strip()
        reviewer_model = _as_text(values.get("reviewer_model")).strip()
        reviewer_api_key = _as_text(values.get("reviewer_api_key")).strip()
        reviewer_base_url = _as_text(values.get("reviewer_base_url")).strip()
        if reviewer_mode == "separate" and reviewer_provider and reviewer_model:
            reviewer_base_url = _resolve_reviewer_base_url(
                core, reviewer_base_url, reviewer_provider, provider, base_url)

        # -- reasoning effort: only values the resolved model offers ---------
        reasoning_raw = _as_text(values.get("reasoning_effort")).strip()
        reasoning_effort = (
            reasoning_raw
            if reasoning_raw and reasoning_raw in core.reasoning_effort_options(
                provider, model)
            else "")

        # -- source admission -------------------------------------------------
        suffix = normalize_source_suffix(
            values.get("source_suffix") or values.get("filename")
            or state.get("filename") or "")

        # -- task definition ---------------------------------------------------
        target_lang = _as_text(values.get("target_lang")).strip() or DEFAULT_TARGET_LANG
        auto_term = _as_bool(values.get("auto_term"), default=True)
        translation_theory = _as_text(values.get("translation_theory")).strip()
        glossary_raw = values.get("user_glossary")
        user_glossary = _as_sequence(glossary_raw)
        if user_glossary is None:
            if glossary_raw in (None, ""):
                user_glossary = ()
            else:
                raise JobConfigError("user_glossary 必须是术语条目序列。")
        style_rules = _as_text(values.get("style_rules"))
        enable_review = _as_bool(values.get("enable_review"), default=True)
        enable_annotate = _as_bool(values.get("enable_annotate"), default=True)
        enable_report = _as_bool(values.get("enable_report"), default=False)
        if state and (academic.get("artifacts")
                      or academic.get("current_stage")
                      not in {None, "", "not_started"}):
            # Research artifacts already exist: the report stage must not be
            # silently disabled on resume.
            enable_report = True
        use_tm = _as_bool(values.get("use_tm"), default=True)

        understanding_raw = values.get("enable_understanding")
        enable_understanding = (None if understanding_raw is None
                                else _as_bool(understanding_raw, default=True))
        cleanup_raw = values.get("enable_source_cleanup")
        enable_source_cleanup = (None if cleanup_raw is None
                                 else _as_bool(cleanup_raw, default=False))
        strict = _as_bool(values.get("strict_terminology_governance"), default=False)

        # -- segmentation / batching -------------------------------------------
        segmentation_mode = _as_text(values.get("segmentation_mode")).strip() \
            or DEFAULT_SEGMENTATION_MODE
        translation_concurrency = _normalize_concurrency(values.get("translation_concurrency"))
        # Batch parameters travel with the task: a resume keeps the batch split
        # the job was created with.
        batch_size = _normalize_positive_int(
            values.get("batch_size"), DEFAULT_BATCH_SIZE, "batch_size")
        max_batch_chars = _normalize_positive_int(
            values.get("max_batch_chars"), DEFAULT_MAX_BATCH_CHARS, "max_batch_chars")
        batch_strategy = _normalize_batch_strategy(values.get("batch_strategy"))
        knowledge_feedback = _as_bool(values.get("knowledge_feedback"), default=True)

        # -- OCR / visual source -------------------------------------------------
        ocr_language = _as_text(values.get("ocr_language")).strip()
        ocr_quality = _as_text(values.get("ocr_quality")).strip() or DEFAULT_OCR_QUALITY
        ocr_dpi = _int_or_none(values.get("ocr_dpi"))
        ocr_psm = _int_or_none(values.get("ocr_psm"))
        ocr_mode = core.normalize_ocr_mode(values.get("ocr_mode"))
        ocr_backend = _normalize_ocr_backend(values.get("ocr_backend"))
        ocr_paddle_model = _as_text(values.get("ocr_paddle_model")).strip() \
            or DEFAULT_PADDLE_MODEL
        ocr_online = _as_mapping(values.get("ocr_online"))

        # -- research -------------------------------------------------------------
        research_settings = _as_mapping(values.get("research_settings"))
        literature_sources = _as_sequence(values.get("literature_sources"))

        # -- delivery --------------------------------------------------------------
        delivery_source = values.get("delivery_config")
        delivery = core.normalize_delivery_config(
            delivery_source if isinstance(delivery_source, Mapping) else None,
            enable_report=enable_report, enable_annotate=enable_annotate)

        config = cls(
            provider=provider,
            model=model,
            api_key=api_key,
            translator_base_url=base_url,
            reasoning_effort=reasoning_effort,
            reviewer_mode=reviewer_mode,
            reviewer_provider=reviewer_provider,
            reviewer_model=reviewer_model,
            reviewer_api_key=reviewer_api_key,
            reviewer_base_url=reviewer_base_url,
            target_lang=target_lang,
            auto_term=auto_term,
            translation_theory=translation_theory,
            user_glossary=user_glossary,
            style_rules=style_rules,
            enable_review=enable_review,
            enable_annotate=enable_annotate,
            enable_report=enable_report,
            use_tm=use_tm,
            enable_understanding=enable_understanding,
            enable_source_cleanup=enable_source_cleanup,
            strict_terminology_governance=strict,
            segmentation_mode=segmentation_mode,
            translation_concurrency=translation_concurrency,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            batch_strategy=batch_strategy,
            knowledge_feedback=knowledge_feedback,
            ocr_language=ocr_language,
            ocr_quality=ocr_quality,
            ocr_dpi=ocr_dpi,
            ocr_psm=ocr_psm,
            ocr_mode=ocr_mode,
            ocr_backend=ocr_backend,
            ocr_paddle_model=ocr_paddle_model,
            ocr_online=ocr_online,
            research_settings=research_settings,
            literature_sources=literature_sources,
            delivery_config=delivery,
            source_suffix=suffix,
        )
        config.validate()
        return config

    # ------------------------------------------------------------------
    # validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Raise :class:`JobConfigError` for combinations the product forbids.

        Only rules that already exist in the product are enforced here; this is
        not a validation framework.  In particular an incomplete *connection* is
        not an error at this layer — both frontends already report it with their
        own actionable message before starting a task.
        """
        if not str(self.target_lang or "").strip():
            raise JobConfigError("缺少目标语言，无法开始翻译任务。")
        if self.source_suffix and self.source_suffix not in SOURCE_SUFFIXES:
            raise JobConfigError(source_suffix_problem(self.source_suffix))
        if int(self.batch_size) < 1:
            raise JobConfigError("batch_size 必须是正整数。")
        if int(self.max_batch_chars) < 1:
            raise JobConfigError("max_batch_chars 必须是正整数。")
        import core  # local import: keeps this module import-order independent

        if str(self.batch_strategy or "").strip().lower() not in core.BATCH_STRATEGIES:
            raise JobConfigError(
                f"未知的组批策略：{self.batch_strategy!r}"
                f"（可选：{'、'.join(core.BATCH_STRATEGIES)}）")
        if int(self.translation_concurrency) < 1:
            raise JobConfigError("translation_concurrency 必须大于 0。")
        if self.ocr_mode not in {"auto", "force", "off"}:
            raise JobConfigError(f"未知的 OCR 模式：{self.ocr_mode!r}")
        _normalize_ocr_backend(self.ocr_backend)  # raises for unknown sources
        if self.ocr_dpi is not None and not (72 <= int(self.ocr_dpi) <= 600):
            raise JobConfigError("ocr_dpi 必须在 72–600 之间。")
        if self.ocr_online is not None and not isinstance(self.ocr_online, Mapping):
            raise JobConfigError("ocr_online 必须是非密连接描述。")
        if self.reviewer_mode not in {"same", "separate"}:
            raise JobConfigError(f"未知的审校模型模式：{self.reviewer_mode!r}")

    # ------------------------------------------------------------------
    # compatibility conversion
    # ------------------------------------------------------------------
    @property
    def separate_reviewer_usable(self) -> bool:
        """True when a separate review model is fully specified.

        An incomplete selection falls back to the main model, which is what both
        frontends did before Phase 1 (the core's role-config normalizer treats
        empty reviewer values as "use the primary role").
        """
        return bool(self.reviewer_mode == "separate"
                    and self.reviewer_provider and self.reviewer_model)

    def to_pipeline_kwargs(self) -> Dict[str, Any]:
        """Return the keyword arguments for the unchanged core runtime API."""
        kwargs: Dict[str, Any] = {
            "provider": self.provider,
            "api_key": self.api_key,
            "model": self.model,
            "translator_base_url": self.translator_base_url,
            "reasoning_effort": self.reasoning_effort,
            "target_lang": self.target_lang,
            "auto_term": bool(self.auto_term),
            "translation_theory": self.translation_theory,
            "user_glossary": list(self.user_glossary),
            "style_rules": self.style_rules,
            "enable_review": bool(self.enable_review),
            "enable_annotate": bool(self.enable_annotate),
            "enable_report": bool(self.enable_report),
            "use_tm": bool(self.use_tm),
            "enable_understanding": self.enable_understanding,
            "enable_source_cleanup": self.enable_source_cleanup,
            "strict_terminology_governance": bool(
                self.strict_terminology_governance),
            "segmentation_mode": self.segmentation_mode,
            "translation_concurrency": int(self.translation_concurrency),
            "batch_size": int(self.batch_size),
            "max_batch_chars": int(self.max_batch_chars),
            "batch_strategy": self.batch_strategy,
            "knowledge_feedback": bool(self.knowledge_feedback),
            "ocr_language": self.ocr_language,
            "ocr_quality": self.ocr_quality,
            "ocr_dpi": self.ocr_dpi,
            "ocr_psm": self.ocr_psm,
            "ocr_mode": self.ocr_mode,
            "ocr_backend": self.ocr_backend,
            "ocr_paddle_model": self.ocr_paddle_model,
            "ocr_online": dict(self.ocr_online) if self.ocr_online else None,
            "research_settings": (dict(self.research_settings)
                                  if self.research_settings else None),
            "literature_sources": (list(self.literature_sources)
                                   if self.literature_sources else None),
            "delivery_config": dict(self.delivery_config),
        }
        if self.separate_reviewer_usable:
            kwargs.update({
                "reviewer_provider": self.reviewer_provider,
                "reviewer_model": self.reviewer_model,
                "reviewer_api_key": self.reviewer_api_key,
                "reviewer_base_url": self.reviewer_base_url,
            })
        return kwargs

    # ------------------------------------------------------------------
    # deterministic serialization (fixtures, fingerprinting, tests)
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """A JSON-serializable view of the contract (field declaration order)."""
        payload: Dict[str, Any] = {}
        for spec in _dataclass_fields(self):
            value = getattr(self, spec.name)
            if isinstance(value, tuple):
                value = list(value)
            payload[spec.name] = value
        return payload

    def to_json(self) -> str:
        """Deterministic JSON: sorted keys, stable separators."""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))

    def fingerprint(self) -> str:
        """Stable content hash of the effective contract."""
        import hashlib
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    def connection_problem(self) -> str:
        """Empty string when the connection is usable, else the reason."""
        import core
        if not str(self.provider or "").strip():
            return "尚未选择模型提供商"
        if not str(self.model or "").strip():
            return "尚未填写模型名称"
        if self.provider != "Local Gateway" and not str(self.api_key or "").strip():
            return "尚未填写 API Key"
        try:
            core.resolve_openai_base_url(self.provider, self.translator_base_url or
                                         ("" if self.provider == "Local Gateway" else None))
        except ValueError as exc:
            return str(exc)
        return ""


# ---------------------------------------------------------------------------
# resolution helpers
# ---------------------------------------------------------------------------

def _normalize_concurrency(value: Any) -> int:
    """Mirror ``core._runtime_int_option`` bounds (1..16)."""
    if value is _MISSING or value is None or value == "":
        return DEFAULT_TRANSLATION_CONCURRENCY
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise JobConfigError(f"translation_concurrency 无效：{value!r}") from exc
    return max(1, min(number, 16))


def _normalize_positive_int(value: Any, default: int, label: str) -> int:
    if value is _MISSING or value is None or value == "":
        return int(default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise JobConfigError(f"{label} 无效：{value!r}") from exc
    if number < 1:
        raise JobConfigError(f"{label} 必须是正整数。")
    return number


def _normalize_batch_strategy(value: Any) -> str:
    """Resolve the batching strategy explicitly (Phase 3 contract).

    Absent/empty means "not configured" and falls back to the documented
    desktop entry default (``sentence``, matching the behavior before the
    strategy became an explicit field).  A *non-empty* unknown value is a
    configuration error and is rejected, not silently reinterpreted.
    """
    import core  # local import: keeps this module import-order independent

    if value is _MISSING or value is None:
        return "sentence"
    text = _as_text(value).strip().lower()
    if not text:
        return "sentence"
    if text in core.BATCH_STRATEGIES:
        return text
    raise JobConfigError(
        f"未知的组批策略：{value!r}（可选：{'、'.join(core.BATCH_STRATEGIES)}）")


def _normalize_ocr_backend(value: Any) -> str:
    from folith import ocr_backends as _ocr_backends
    text = _as_text(value).strip()
    if not text:
        return DEFAULT_OCR_BACKEND
    try:
        return _ocr_backends.normalize_backend(text)
    except Exception as exc:  # noqa: BLE001 - OCRUnavailable carries the reason
        raise JobConfigError(str(exc) or f"未知的 OCR 来源：{text}") from exc


def _resolve_reviewer_base_url(core, configured: str, reviewer_provider: str,
                               provider: str,
                               primary_base_url: Optional[str]) -> str:
    """Resolve the separate review endpoint without inventing one.

    A reviewer left without an address inherits the primary address *only* when
    it points at the same provider; otherwise the core reports the real problem
    at call time instead of this builder guessing an endpoint.
    """
    if configured:
        return configured
    if reviewer_provider and reviewer_provider == provider:
        return primary_base_url or ""
    try:
        return core.resolve_openai_base_url(reviewer_provider, None) or ""
    except ValueError:
        return ""


def pipeline_kwarg_keys() -> frozenset:
    """The complete set of core keywords this contract can emit."""
    return frozenset(JOB_CONFIG_KWARG_KEYS | OPTIONAL_REVIEWER_KWARG_KEYS)


def build_pipeline_kwargs(
    values: Optional[Mapping[str, Any]] = None,
    *,
    state: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """One-call convenience for frontends: live values + job state → core kwargs."""
    return JobConfig.from_frontend(values, state=state).to_pipeline_kwargs()
