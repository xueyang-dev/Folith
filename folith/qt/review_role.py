"""A-65/A-68: manual review reuses the product's review-role resolution."""
from __future__ import annotations

from typing import Any

from folith import model_roles


def manual_review_runtime(config: Any, core_module: Any) -> tuple:
    """(provider, api_key, model, base_url) resolved by the shared role logic."""
    translator = {
        "provider": config.provider, "model": config.model,
        "api_key": config.api_key, "base_url": config.translator_base_url(),
    }
    reviewer = None
    if getattr(config, "reviewer_mode", "") == "separate" and getattr(config, "reviewer_provider", ""):
        reviewer = {
            "provider": config.reviewer_provider, "model": config.reviewer_model,
            "api_key": config.reviewer_api_key,
            "base_url": config.reviewer_base_url,
        }
    role = model_roles.resolve_review_runtime(
        getattr(config, "reviewer_mode", ""), translator, reviewer)
    base = role["base_url"] or core_module.resolve_openai_base_url(role["provider"], None)
    return (role["provider"], role["api_key"], role["model"], base)
