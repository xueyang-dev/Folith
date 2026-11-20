"""Per-purpose thinking levels for the settings UI and job hand-off (C1b PR-2).

PR-1 put the verified model families in :mod:`folith.engine.reasoning_catalog`
and made the wire dispatch read it.  This module is the small bridge between
that catalogue and the existing settings/job pipeline: it turns a stored
``connection_levels`` map into the two legacy scalar fields the job config
already understands, so no runtime path changes.
"""
from __future__ import annotations

import dataclasses

from typing import Any, Mapping

from folith.engine import reasoning_catalog as catalog

#: Roles whose level can follow the translation row when not set explicitly.
INHERITING_ROLES = ("reviewer", "vision")


def choices(provider: str, model: str) -> list[tuple[str, str]]:
    """The selectable levels for a model, always including the service default."""
    result = [("service_default", "服务默认")]
    for level in catalog.levels_for(provider, model):
        result.append((level.key, level.label))
    return result


def cannot_switch(provider: str, model: str) -> bool:
    """Whether this model has no verified Off level (e.g. Local Gateway)."""
    return "off" not in [level.key for level in catalog.levels_for(provider, model)]


def effective_level(levels: Mapping[str, str], role: str) -> str:
    """The level for a role, inheriting translation when unset on reviewer/vision."""
    value = str(levels.get(role) or "")
    if value or role not in INHERITING_ROLES:
        return value
    return str(levels.get("translation") or "")


def to_job_fields(levels: Mapping[str, str], role: str, provider: str,
                  model: str) -> tuple[str, str]:
    """Translate one role's level to ``(reasoning_effort, reasoning_mode)``.

    These are the two scalars the existing job config and runtime already
    accept.  A level the model does not declare is sent unchanged (the "old
    setting" case) so migration never alters a working request.
    """
    level = effective_level(levels, role)
    declared = {item.key for item in catalog.levels_for(provider, model)}
    if not level or level == "service_default":
        return "", "provider_default"
    if level not in declared:
        # Keep a stored legacy value byte-for-byte; the UI marks it 旧设置.
        if level in ("thinking_disabled", "thinking_disabled_vllm"):
            return "", level
        return level, "provider_default"
    item = next(item for item in catalog.levels_for(provider, model) if item.key == level)
    if item.dispatch == "reasoning_effort":
        return item.value, "provider_default"
    return "", item.dispatch if item.dispatch.startswith("thinking_") else "provider_default"


def migrate(config: Any) -> dict[str, str]:
    """Derive per-role levels from a 0.8.2 config without changing behaviour.

    Only the verified catalogue is consulted.  Old values outside it are kept
    so the next request is identical; the settings UI will mark such a value
    "旧设置" until the user actively replaces it.  Idempotent and additive:
    the old fields stay in place for 0.8.2 downgrade.
    """
    existing = dict(getattr(config, "connection_levels", None) or {})
    if existing:
        return {role: str(existing.get(role) or "") for role in
                ("translation", *INHERITING_ROLES)}
    translation = str(getattr(config, "reasoning_effort", "") or "").strip().lower()
    roles = dict(getattr(config, "connection_roles", None) or {})
    profiles = {p.get("id"): p for p in (getattr(config, "connections", None) or [])}
    result = {"translation": translation, "reviewer": "", "vision": ""}
    for role in INHERITING_ROLES:
        profile = profiles.get(roles.get(role)) or {}
        mode = str(profile.get("reasoning_mode") or "").strip().lower()
        if mode in ("thinking_disabled", "thinking_disabled_vllm"):
            result[role] = "off"
    return result


def migrate_loaded(config, saved: dict) -> "object":
    """Migration entry point used by ``EngineConfig.load`` (keeps adapter short)."""
    from folith.model_connections import effective_config

    cfg = dataclasses.replace(
        config,
        connections=list(saved.get("connections") or []),
        connection_roles=dict(saved.get("connection_roles") or {}),
        connection_models=dict(saved.get("connection_models") or {}),
        connection_levels=dict(saved.get("connection_levels") or {}),
    )
    cfg.ensure_connections()
    cfg.connection_levels = migrate(cfg)
    return effective_config(cfg, cfg.connections, cfg.connection_roles)


def translation_effort(config) -> str:
    """The legacy scalar for a config's translation role (client mapping only)."""
    effort, _mode = to_job_fields(config.connection_levels, "translation",
                                  config.provider, config.model)
    return effort or str(getattr(config, "reasoning_effort", "") or "")


def is_legacy(level: str, provider: str, model: str) -> bool:
    """A stored level the current catalogue does not declare."""
    level = str(level or "")
    if not level or level == "service_default":
        return False
    return level not in {item.key for item in catalog.levels_for(provider, model)}
