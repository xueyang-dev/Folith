"""A-65: manual review must use the same reviewer role as automatic review."""
from __future__ import annotations

from typing import Any


def manual_review_runtime(config: Any, core_module: Any) -> tuple:
    """(provider, api_key, model, base_url) for the review role, not translation."""
    if getattr(config, 'reviewer_mode', '') == 'separate' and getattr(config, 'reviewer_provider', ''):
        base = config.reviewer_base_url or core_module.resolve_openai_base_url(
            config.reviewer_provider, None)
        return (config.reviewer_provider, config.reviewer_api_key,
                config.reviewer_model, base)
    return (config.provider, config.api_key, config.model,
            config.translator_base_url())
