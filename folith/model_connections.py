"""Resolve saved model connections without importing the desktop toolkit."""
from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from folith.qt.adapter import EngineConfig


def input_capability(metadata: dict) -> str:
    """Interpret advertised input modalities without guessing from model names."""
    if not isinstance(metadata, dict):
        return "unknown"
    architecture = metadata.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    inputs = metadata.get("input_modalities", architecture.get("input_modalities"))
    if not isinstance(inputs, list):
        return "unknown"
    values = {str(value).lower() for value in inputs}
    if "image" in values or "vision" in values:
        return "vision"
    if "text" in values:
        return "text"
    return "unknown"


def environment_key(provider: str) -> str:
    if provider == "Local Gateway":
        return ""
    names = {"OpenAI": "OPENAI_API_KEY", "Gemini": "GEMINI_API_KEY",
             "OpenCode Go": "OPENCODE_API_KEY",
             "DeepSeek": "DEEPSEEK_API_KEY", "OpenRouter": "OPENROUTER_API_KEY",
             "SiliconFlow": "SILICONFLOW_API_KEY", "Moonshot (Kimi)": "MOONSHOT_API_KEY",
             "Zhipu (GLM)": "ZHIPU_API_KEY", "Qwen (DashScope)": "DASHSCOPE_API_KEY"}
    return os.environ.get(names.get(provider, "OPENAI_API_KEY"), "")


def connection_base_url(provider: str, base_url: str = "") -> str:
    import core
    return core.resolve_openai_base_url(provider, base_url) or ""


def effective_config(config: EngineConfig, profiles: list[dict], roles: dict) -> EngineConfig:
    result = dataclasses.replace(config, connections=list(profiles), connection_roles=dict(roles))
    for role in ("translation", "reviewer"):
        profile = next((p for p in profiles if p["id"] == roles.get(role)), None)
        if role == "reviewer":
            result.reviewer_mode = "separate" if profile else "same"
            if not profile:
                for key in ("provider", "model", "api_key", "base_url"):
                    setattr(result, "reviewer_" + key, "")
        elif "translation" in roles and not profile:
            for key in ("provider", "model", "api_key", "base_url"):
                setattr(result, key, "")
        if profile:
            if role == "translation":
                result.reasoning_effort = str(profile.get("reasoning_effort", config.reasoning_effort))
            prefix = "reviewer_" if role == "reviewer" else ""
            for key in ("provider", "model", "api_key", "base_url"):
                value = profile.get(key, "")
                if key == "model":
                    value = config.connection_models.get(role) or value
                if key == "api_key":
                    value = value or environment_key(profile["provider"])
                if key == "base_url" and role == "reviewer" and not value:
                    try:
                        value = connection_base_url(profile["provider"])
                    except ValueError:
                        value = ""
                setattr(result, prefix + key, value)
    return result

