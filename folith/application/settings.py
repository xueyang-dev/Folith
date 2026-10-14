"""One frontend-neutral *effective settings* resolution path.

Phase 4 of the architecture pass.  Before this module every surface resolved
runtime configuration on its own: Qt through ``EngineConfig`` (which merged
``provider_config.json`` with dataclass defaults) and Streamlit through session
state, presets and ``ui_preferences.json``.  They answered "which value wins?"
independently, so an equivalent job could be configured differently depending on
which UI produced it.

.. code-block:: text

    explicit user choice ─┐
    stored user defaults ─┤
    provider profile ─────┼─→ resolve_effective_settings() ─→ EffectiveSettings
    persisted job state ──┤            (values + provenance)          │
    environment ──────────┘                                          ↓
                                                    JobConfig.from_settings()
                                                             │
                                                             ↓
                                                    existing core runtime

This module decides **which value wins**.  :class:`~folith.application.job_config.JobConfig`
remains the exact execution contract (normalization, validation, cross-field
rules and the conversion to core keywords).

Storage is untouched: ``QSettings``, ``ui_preferences.json``,
``provider_config.json`` and environment variables keep working; they are simply
*read as named layers* by the frontends and handed to one resolver.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

__all__ = [
    "DEFAULT_STYLE_RULES",
    "ENVIRONMENT_FALLBACKS",
    "LAYERS",
    "SECRET_FIELDS",
    "EffectiveSettings",
    "SettingsSources",
    "resolve_effective_settings",
]

#: The named configuration layers, highest precedence first.  Every resolved
#: field records which of these won.
LAYERS: Tuple[str, ...] = (
    "job_state",            # the task's own persisted decision (authoritative on resume)
    "job_state_derived",    # a value derived from the task's state (e.g. "locked" segmentation)
    "persisted_default",    # a persisted-job-only default (understanding / source cleanup)
    "explicit",             # what the user chose in this action / session
    "provider_profile",     # provider_config.json (connection, review model, reasoning effort)
    "user_defaults",        # stored preferences and presets (QSettings / ui_preferences / presets)
    "environment",          # FOLIOTHREAD_* fallbacks applied by the core itself
    "application_defaults",  # the documented neutral defaults
    "default",              # the dataclass default of the contract
)

#: Values that must never be echoed into diagnostics, fixtures or logs.
SECRET_FIELDS = frozenset({"api_key", "reviewer_api_key"})

#: The product's default style rule.  It used to be a literal duplicated in the
#: Qt adapter and in the Streamlit shell; one default belongs in one place.
DEFAULT_STYLE_RULES = (
    "保持学术书面语；专有名词、作者姓名、机构名、引用标注、URL 等保留原文；"
    "标点遵循目标语言规范。")

#: Environment variables the core reads for settings this contract can also
#: carry.  They are applied *inside* the core when the resolved value is absent,
#: so the resolver deliberately does not duplicate the lookup; the list exists so
#: the environment layer is explicit and test-guarded.
ENVIRONMENT_FALLBACKS: Tuple[str, ...] = (
    "FOLIOTHREAD_OCR_LANGUAGE",
    "FOLIOTHREAD_TRANSLATION_CONCURRENCY",
    "FOLIOTHREAD_LLM_CLEANUP_CONCURRENCY",
    "FOLIOTHREAD_OCR_WORKERS",
    "FOLIOTHREAD_OCR_QUEUE_SIZE",
    "FOLIOTHREAD_OCR_CONFIDENCE_THRESHOLD",
    "FOLIOTHREAD_LLM_CLEANUP_MAX_RETRIES",
    "FOLIOTHREAD_LLM_CLEANUP_REQUEST_INTERVAL",
)

_MISSING = object()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _present(value: Any) -> bool:
    """A layer supplies a value when it is neither missing nor empty."""
    if value is _MISSING or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


@dataclass(frozen=True)
class SettingsSources:
    """Raw configuration inputs, one field per origin.

    Nothing here is resolved or normalised: the frontends only decide *where* a
    value came from, never who wins.
    """

    explicit: Mapping[str, Any] = field(default_factory=dict)
    user_defaults: Mapping[str, Any] = field(default_factory=dict)
    provider_profile: Mapping[str, Any] = field(default_factory=dict)
    job_state: Mapping[str, Any] = field(default_factory=dict)
    environment: Mapping[str, Any] = field(default_factory=dict)
    application_defaults: Mapping[str, Any] = field(default_factory=dict)

    def normalized(self) -> "SettingsSources":
        def as_map(value: Any) -> Dict[str, Any]:
            return dict(value) if isinstance(value, Mapping) else {}

        return SettingsSources(
            explicit=as_map(self.explicit),
            user_defaults=as_map(self.user_defaults),
            provider_profile=as_map(self.provider_profile),
            job_state=as_map(self.job_state),
            environment=as_map(self.environment),
            application_defaults=as_map(self.application_defaults),
        )

    def with_state(self, state: Optional[Mapping[str, Any]]) -> "SettingsSources":
        """The same sources, resolved against a different job state.

        Task creation needs this: identity — and therefore the persisted state —
        is only known after the document has been hashed, and the *previous*
        state (or ``{}`` for a brand-new task) is what must decide the contract,
        never the placeholder the command just created.
        """
        return SettingsSources(
            explicit=self.explicit,
            user_defaults=self.user_defaults,
            provider_profile=self.provider_profile,
            job_state=dict(state) if isinstance(state, Mapping) else {},
            environment=self.environment,
            application_defaults=self.application_defaults,
        )


@dataclass(frozen=True)
class EffectiveSettings:
    """The winning value for every runtime setting, plus where it came from."""

    values: Mapping[str, Any]
    provenance: Mapping[str, str]
    state: Mapping[str, Any] = field(default_factory=dict)
    sources: Optional[SettingsSources] = field(default=None, repr=False)

    # -- inspection ------------------------------------------------------
    def winner(self, name: str) -> str:
        """Which layer supplied ``name`` (``"default"`` when nothing did)."""
        return self.provenance.get(name, "default")

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def describe(self, *, redact: bool = True) -> Dict[str, Any]:
        """A diagnostics-safe view: every value with its winning layer.

        Secret fields are replaced by ``"***"`` unless ``redact=False``; this is
        the shape diagnostics, fixtures and logs may print.
        """
        described: Dict[str, Any] = {}
        for name, value in self.values.items():
            if redact and name in SECRET_FIELDS:
                value = "***" if _present(value) else ""
            described[name] = {"value": value, "source": self.winner(name)}
        return described

    # -- contract --------------------------------------------------------
    def to_job_config(self):
        """Build the execution contract from the resolved settings."""
        from folith.application.job_config import JobConfig

        return JobConfig.from_settings(self)


# ---------------------------------------------------------------------------
# field specifications
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Field:
    """Where one setting may come from, highest precedence first."""

    name: str
    #: ``"task"`` = the persisted task decision wins; ``"connection"`` = the
    #: live/profile value wins (a stale credential must not be replayed).
    scope: str = "task"
    #: the value must be truthy to win (matches the historic `or` chains)
    truthy: bool = False
    #: keys inside the persisted job state that carry this value
    state_keys: Tuple[str, ...] = ()
    #: computed from the job state when a persisted task has no explicit value
    state_derived: Optional[str] = None
    #: default that applies only to a persisted task
    persisted_default: Optional[str] = None
    #: layers consulted, in order, when the task state did not decide
    live_layers: Tuple[str, ...] = (
        "explicit", "user_defaults", "application_defaults")


_TASK_FIELDS: Tuple[_Field, ...] = (
    _Field("reviewer_provider", truthy=True),
    _Field("reviewer_model", truthy=True),
    _Field("reviewer_api_key"),
    _Field("reviewer_base_url"),
    _Field("reasoning_effort", truthy=True),
    _Field("target_lang", truthy=True, state_keys=("target_lang",)),
    # The task-setup panel saves the strategy preset into the job state; without
    # these state keys the live EngineConfig (the global task defaults) won and a
    # 快速 task still ran term extraction and the independent review.
    _Field("auto_term", state_keys=("auto_term",)),
    _Field("translation_theory", truthy=True, state_keys=("theory",)),
    _Field("user_glossary", truthy=True, state_keys=("glossary",)),
    _Field("style_rules"),
    _Field("enable_review", state_keys=("enable_review",)),
    _Field("enable_annotate", state_keys=("enable_annotate",)),
    _Field("enable_report", state_keys=("report_enabled",)),
    _Field("use_tm", state_keys=("use_tm",)),
    _Field("enable_understanding", state_keys=("enable_understanding",),
           persisted_default="understanding"),
    _Field("enable_source_cleanup", state_keys=("enable_source_cleanup",),
           persisted_default="pdf_cleanup"),
    # The panel writes the field name; the pipeline persists it as `quality_mode`.
    _Field("strict_terminology_governance",
           state_keys=("strict_terminology_governance", "quality_mode")),
    _Field("segmentation_mode", truthy=True, state_derived="locked_segmentation"),
    _Field("translation_concurrency", truthy=True),
    _Field("batch_size", truthy=True),
    _Field("max_batch_chars", truthy=True),
    _Field("ocr_language"),
    _Field("ocr_quality", truthy=True),
    _Field("ocr_dpi"),
    _Field("ocr_psm"),
    _Field("ocr_mode"),
    _Field("ocr_backend"),
    _Field("ocr_paddle_model", truthy=True),
    _Field("ocr_online"),
    _Field("research_settings", truthy=True, state_keys=("research_settings",)),
    _Field("literature_sources", truthy=True, state_keys=("literature_sources",)),
)

_CONNECTION_FIELDS: Tuple[_Field, ...] = (
    _Field("provider", scope="connection",
           live_layers=("explicit", "provider_profile", "user_defaults",
                        "application_defaults")),
    _Field("model", scope="connection",
           live_layers=("explicit", "provider_profile", "user_defaults",
                        "application_defaults")),
    _Field("api_key", scope="connection",
           live_layers=("explicit", "provider_profile", "user_defaults",
                        "application_defaults")),
    _Field("translator_base_url", scope="connection",
           live_layers=("explicit", "provider_profile", "user_defaults",
                        "application_defaults")),
)

_ALL_FIELDS: Tuple[_Field, ...] = _CONNECTION_FIELDS + _TASK_FIELDS

#: Fields resolved without any job-state participation.
_SIMPLE_FIELDS: Tuple[_Field, ...] = (
    _Field("reviewer_mode"),
    _Field("source_suffix"),
    _Field("delivery_config"),
)


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------

def _persisted(state: Mapping[str, Any], saved: Mapping[str, Any]) -> bool:
    academic = state.get("academic_state")
    academic = academic if isinstance(academic, Mapping) else {}
    return bool(saved or state.get("p1_done") or state.get("p2_done")
                or academic.get("artifacts"))


def _locked_segmentation(state: Mapping[str, Any]) -> Any:
    segmentation = state.get("segmentation")
    if isinstance(segmentation, Mapping) and segmentation.get("locked"):
        mode = _text(segmentation.get("mode")).strip()
        if mode:
            return mode
    return _MISSING


def _persisted_default(kind: str, state: Mapping[str, Any]) -> Any:
    if kind == "understanding":
        # A legacy in-progress job had no understanding pass outside strict
        # terminology governance; resuming it must not add API cost.
        return bool(state.get("profile_done") or state.get("understanding_done")
                    or state.get("quality_mode"))
    if kind == "pdf_cleanup":
        # The core's documented rule: source cleanup is on for PDF tasks.
        return _text(state.get("filename")).lower().endswith(".pdf")
    return _MISSING


def _from_layers(name: str, layers: Iterable[str], sources: SettingsSources,
                 *, truthy: bool, explicit_is_truthy: bool = False) -> Tuple[Any, str]:
    """First supplied value across the named layers, with its provenance.

    ``explicit_is_truthy=False`` (task-scoped settings) treats the user's live
    choice as *presence-based*: an explicit ``0``/``""``/``None`` is passed
    through so the contract can validate or tri-state it instead of silently
    substituting a default.  Connection settings use the historic truthy rule,
    because an empty credential must fall through to the stored profile.
    """
    for layer in layers:
        if layer == "application_defaults":
            if name in sources.application_defaults:
                value = sources.application_defaults[name]
                if not (truthy and not value):
                    return value, layer
            continue
        mapping = getattr(sources, layer)
        if name not in mapping:
            continue
        value = mapping[name]
        if truthy and not (layer == "explicit" and not explicit_is_truthy) and not value:
            continue
        if value is None and not (layer == "explicit" and not explicit_is_truthy):
            # `None` is meaningful only where the contract says so (tri-state
            # fields, and the explicit layer); elsewhere it means "not supplied".
            continue
        return value, layer
    return _MISSING, "default"


def _resolve_task_field(spec: _Field, sources: SettingsSources,
                        saved: Mapping[str, Any], state: Mapping[str, Any],
                        persisted: bool) -> Tuple[Any, str]:
    if persisted and spec.name in saved:
        value = saved[spec.name]
        if not (spec.truthy and not value):
            return value, "job_state"
    for key in spec.state_keys:
        if key in state and (not spec.truthy or state[key]):
            return state[key], "job_state"
    if spec.state_derived:
        derived = (_locked_segmentation(state) if spec.state_derived == "locked_segmentation"
                   else _MISSING)
        if derived is not _MISSING:
            return derived, "job_state_derived"
    if spec.persisted_default and persisted:
        return _persisted_default(spec.persisted_default, state), "persisted_default"
    return _from_layers(spec.name, spec.live_layers, sources, truthy=spec.truthy,
                        explicit_is_truthy=False)


def _resolve_connection_field(spec: _Field, sources: SettingsSources,
                              saved: Mapping[str, Any]) -> Tuple[Any, str]:
    saved_translator = saved.get("translator")
    saved_translator = (saved_translator
                        if isinstance(saved_translator, Mapping) else {})
    value, layer = _from_layers(spec.name, spec.live_layers, sources, truthy=True,
                                explicit_is_truthy=True)
    if layer != "default":
        return value, layer
    # Legacy states stored the connection flat, or under the `translator` role.
    flat = saved.get(spec.name)
    if _present(_text(flat)):
        return flat, "job_state"
    role = saved_translator.get(spec.name)
    if _present(_text(role)):
        return role, "job_state"
    return _MISSING, "default"


def resolve_effective_settings(sources: SettingsSources) -> EffectiveSettings:
    """Resolve every runtime setting once, from named layers.

    The policy is the product's existing one, made explicit:

    * **task-scoped** settings (batching, review, terminology, OCR, research,
      delivery, target language): the task's own persisted decision wins, then a
      job-state fallback, then a persisted-only default, then the live value,
      the stored user default, and finally the application default.  A global
      preference change therefore cannot silently rewrite an existing task.
    * **connection** settings (provider, model, key, base URL): the live or
      provider-profile value wins, then the legacy value saved with the task,
      then the user default.  A stale credential is never replayed.
    """
    sources = sources.normalized()
    state = sources.job_state
    saved_raw = state.get("pipeline_config")
    saved: Dict[str, Any] = dict(saved_raw) if isinstance(saved_raw, Mapping) else {}
    persisted = _persisted(state, saved)

    values: Dict[str, Any] = {}
    provenance: Dict[str, str] = {}

    for spec in _CONNECTION_FIELDS:
        value, layer = _resolve_connection_field(spec, sources, saved)
        values[spec.name] = None if value is _MISSING else value
        provenance[spec.name] = layer

    for spec in _TASK_FIELDS:
        value, layer = _resolve_task_field(spec, sources, saved, state, persisted)
        values[spec.name] = None if value is _MISSING else value
        provenance[spec.name] = layer

    # -- simple fields -------------------------------------------------
    for spec in _SIMPLE_FIELDS:
        value, layer = _from_layers(spec.name, spec.live_layers, sources, truthy=False)
        values[spec.name] = None if value is _MISSING else value
        provenance[spec.name] = layer

    # Delivery is task-scoped like the rest of the task definition: a task that
    # already declares its products keeps them.
    saved_delivery = state.get("delivery_config")
    if isinstance(saved_delivery, Mapping) and saved_delivery:
        values["delivery_config"] = dict(saved_delivery)
        provenance["delivery_config"] = "job_state"
    if not values.get("delivery_config"):
        value, layer = _from_layers("delivery_config", ("explicit", "user_defaults"),
                                    sources, truthy=False)
        if value is not _MISSING:
            values["delivery_config"] = value
            provenance["delivery_config"] = layer

    # Admission: a bare suffix, an explicit filename, or the task's own name.
    suffix = (sources.explicit.get("source_suffix")
              or sources.explicit.get("filename")
              or state.get("filename") or "")
    values["source_suffix"] = suffix
    provenance["source_suffix"] = "explicit" if suffix else "default"
    if not values.get("filename"):
        values["filename"] = sources.explicit.get("filename") or state.get("filename")

    return EffectiveSettings(values=values, provenance=provenance, state=state,
                             sources=sources)
