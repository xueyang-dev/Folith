"""The verified reasoning-control catalogue (C1b / #540, PR-1).

Before this module the UI learned which thinking controls a model supports from
two prefix whitelists in :mod:`folith.engine.llm_provider`.  The whitelists
encoded three different wire shapes in Python branches, could not say which
level was the default, and carried no evidence of *why* a model was listed.
This module keeps the same set of verified models as data: one ordered list of
levels per model family, each level naming how it is sent, plus the default and
the measurement behind it.

The first revision deliberately contains **only the entries already verified in
the old code** — no new API probing, no new models.  Nothing here decides what
to show in the UI yet (that is PR-2); callers keep asking the old public helpers,
which now read this catalogue.

Dispatch is data, not branches at the call site: :func:`apply_to_request` is the
single place that turns a chosen level into request fields.
"""
from __future__ import annotations

from dataclasses import dataclass

# Runtime status field used by both the model-call trace and the reasoning
# watcher.  One field, not two: it names the level actually sent and how it was
# sent.  Kept here so the trace, the watcher and the report agree on the key.
LEVEL_FIELD = "reasoning_level"


@dataclass(frozen=True)
class Level:
    """One controllable level of a model family."""

    key: str                 # stable id: off / low / medium / high / on
    label: str               # the model's own name for it (Off / Low / …)
    dispatch: str            # reasoning_effort | thinking_disabled | thinking_disabled_vllm | service_default
    value: str = ""          # value for reasoning_effort; empty for switch/default dispatch


@dataclass(frozen=True)
class Entry:
    """One verified model family and the controls its endpoint accepts."""

    id: str
    providers: frozenset[str] | None   # None = any OpenAI-compatible provider
    prefixes: tuple[str, ...]          # empty with `any_model` matches every name
    levels: tuple[Level, ...]
    default: str                       # key of a level, or "service_default"
    verified: str                      # date + evidence for this entry
    any_model: bool = False
    require_openai_compat: bool = True
    exclude_chat: bool = False


OFF = Level("off", "Off", "thinking_disabled")
OFF_VLLM = Level("off", "Off", "thinking_disabled_vllm")
ON = Level("on", "On", "service_default")
LOW = Level("low", "Low", "reasoning_effort", "low")
MEDIUM = Level("medium", "Medium", "reasoning_effort", "medium")
HIGH = Level("high", "High", "reasoning_effort", "high")
EFFORT_LEVELS = (LOW, MEDIUM, HIGH)

#: Ordered; first matching entry owns the model.  Order mirrors the old
#: whitelist precedence and is asserted by the catalogue tests.
ENTRIES: tuple[Entry, ...] = (
    Entry(
        id="local-gateway-effort",
        providers=frozenset({"Local Gateway"}),
        prefixes=(),
        levels=EFFORT_LEVELS,
        default="service_default",
        verified=("2026-11 local gateway probe: same task 917 reasoning tokens at "
                  "service default vs 226 at low; HTTP accepts reasoning_effort. "
                  "No off level: the gateway accepts the DeepSeek thinking field "
                  "(HTTP 200) but does not stop thinking, and sends the whole "
                  "budget to reasoning with an empty body."),
        any_model=True,
        require_openai_compat=False,
    ),
    Entry(
        id="deepseek-thinking-switch",
        providers=frozenset({"DeepSeek", "OpenCode Go"}),
        prefixes=("deepseek-flash", "deepseek-v4"),
        levels=(OFF, ON),
        default="on",
        verified=("Official DeepSeek endpoint measured 0 reasoning tokens with "
                  "the thinking switch; OpenCode Go's deepseek model measured "
                  "the same. Older names such as deepseek-chat are not thinking "
                  "models, and a custom relay serving a deepseek-named model "
                  "was not measured."),
    ),
    Entry(
        id="dots-vllm-switch",
        providers=None,
        prefixes=("dots3", "dots-"),
        levels=(OFF_VLLM, ON),
        default="on",
        verified=("vLLM/SGLang chat-template switch verified against "
                  "dots3-note-prev."),
    ),
    Entry(
        id="openai-reasoning-effort",
        providers=None,
        prefixes=("o1", "o3", "o4-mini", "gpt-5", "gpt-6", "deepseek-r1", "qwq"),
        levels=EFFORT_LEVELS,
        default="service_default",
        verified=("OpenAI reasoning families and common compatible relay names; "
                  "portable low/medium/high subset retained from 0.8.2."),
        exclude_chat=True,
    ),
)

#: OpenAI's own naming quirk: these prefixes drop `temperature` even when no
#: reasoning level was sent.  Distinct from the catalogue prefixes (`o4` vs
#: `o4-mini`) and kept as data so the call site has no model-name branch.
OPENAI_TEMPERATURE_FAMILIES = ("o1", "o3", "o4", "gpt-5", "gpt-6")

#: Stable ordering for thinking switches (the old dict order).
_THINKING_ORDER = ("thinking_disabled_vllm", "thinking_disabled")


def _is_openai_compatible(provider: str) -> bool:
    from folith.engine._link import core as _c

    cfg = _c.PROVIDERS.get(provider) or {}
    return cfg.get("kind") in {"openai", "openai_compat"}


def match(provider: str, model: str) -> Entry | None:
    """The catalogue entry for a model, or ``None`` when nothing was verified."""
    provider = str(provider or "").strip()
    name = str(model or "").strip().lower()
    if not name:
        return None
    for entry in ENTRIES:
        if entry.providers is not None and provider not in entry.providers:
            continue
        if entry.require_openai_compat and not _is_openai_compatible(provider):
            continue
        if not entry.any_model:
            if entry.exclude_chat and "-chat" in name:
                continue
            if not name.startswith(entry.prefixes):
                continue
        return entry
    return None


def levels_for(provider: str, model: str) -> tuple[Level, ...]:
    entry = match(provider, model)
    return entry.levels if entry is not None else ()


def default_level(provider: str, model: str) -> str:
    entry = match(provider, model)
    return entry.default if entry is not None else "service_default"


def effort_options(provider: str, model: str) -> tuple[str, ...]:
    """Wire values accepted by ``reasoning_effort`` (old helper's contract)."""
    return tuple(level.value for level in levels_for(provider, model)
                 if level.dispatch == "reasoning_effort")


def thinking_options(provider: str, model: str) -> tuple[str, ...]:
    """Thinking switches accepted by this model (old helper's contract)."""
    modes = [level.dispatch for level in levels_for(provider, model)
             if level.dispatch in _THINKING_ORDER]
    return tuple(sorted(modes, key=_THINKING_ORDER.index))


def resolve_effort(provider: str, model: str, requested, thread_default=None):
    """The reasoning level actually in force for a call, or ``None``.

    A thread level (set for the translator role) is never allowed to leak into
    a reviewer or an unknown relay model whose catalogue has no such level.
    """
    value = requested if requested is not None else thread_default
    if value and str(value).strip().lower() not in effort_options(provider, model):
        return None
    return value


def apply_to_request(request: dict, provider: str, model: str, *,
                     effort=None, mode: str = "", temperature: float = 0.1) -> None:
    """Add the chosen reasoning level's wire fields to an OpenAI-style request.

    The former call site had these branches inline in ``core.call_llm``; they
    now live here so the catalogue is the only source of dispatch shapes.
    """
    name = str(model or "").lower()
    if effort:
        request["reasoning_effort"] = effort
        if not name.startswith(EFFORT_LEVELS_PREFIXES):
            request["temperature"] = temperature
    else:
        request["temperature"] = temperature
    if (provider == "OpenAI"
            and name.startswith(OPENAI_TEMPERATURE_FAMILIES)
            and "-chat" not in name
            and effort != "none"):
        request.pop("temperature", None)
    if mode == "thinking_disabled":
        request["extra_body"] = {"thinking": {"type": "disabled"}}
    elif mode == "thinking_disabled_vllm":
        request["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}


#: The prefix set that suppresses `temperature` when a reasoning level is sent
#: (same families as the OpenAI entry above, kept explicit so it does not depend
#: on entry order).
EFFORT_LEVELS_PREFIXES = ("o1", "o3", "o4-mini", "gpt-5", "gpt-6", "deepseek-r1", "qwq")


def level_record(provider: str, model: str, *, mode: str = "", effort=None) -> dict:
    """The single per-call trace field: level actually sent + dispatch method.

    This replaces the old ``reasoning_mode`` metadata key.  The reasoning
    watcher and the performance report read this one field; there is no second
    "current mode" field to keep in sync.
    """
    if effort and str(effort).strip().lower() in effort_options(provider, model):
        return {"level": str(effort).strip().lower(), "dispatch": "reasoning_effort"}
    if mode in _THINKING_ORDER:
        return {"level": "off", "dispatch": mode}
    return {"level": "service_default", "dispatch": "service_default"}


def is_reasoning_level_record(record: dict) -> bool:
    """Whether a model-call record carries the level field (the watch uses this)."""
    return LEVEL_FIELD in (record.get("metadata") or {})
