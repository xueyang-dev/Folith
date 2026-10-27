"""Cost and telemetry metrics for source update and incremental retranslation."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from folith.source_update.alignment import Classification
from folith.source_update.plan import Resolution, SourceUpdatePlan


def calculate_source_update_savings(
    plan: SourceUpdatePlan,
    *,
    cost_per_1k_chars: float = 0.002,  # Example standard CAT reference cost
) -> Dict[str, Any]:
    """Calculate character, call, and cost savings vs a full retranslation."""
    v2_total_chars = 0
    reused_chars = 0
    retranslate_chars = 0
    api_calls_avoided = 0
    api_calls_required = 0

    for entry in plan.entries:
        cls = entry.classification
        if cls == Classification.SUSPECTED_CHANGED:
            if entry.resolution == Resolution.CONFIRM_REUSE:
                cls = Classification.UNCHANGED
            elif entry.resolution == Resolution.CONFIRM_RETRANSLATE:
                cls = Classification.CHANGED
            elif entry.resolution == Resolution.SPLIT_ADD_DELETE:
                cls = Classification.ADDED

        v2_text = entry.v2_source or ""
        text_len = len(v2_text)

        if cls == Classification.UNCHANGED:
            reused_chars += text_len
            v2_total_chars += text_len
            api_calls_avoided += 1
        elif cls in (Classification.CHANGED, Classification.ADDED):
            retranslate_chars += text_len
            v2_total_chars += text_len
            api_calls_required += 1

    savings_ratio = (reused_chars / v2_total_chars) if v2_total_chars > 0 else 0.0
    full_cost_estimate = (v2_total_chars / 1000.0) * cost_per_1k_chars
    incremental_cost_estimate = (retranslate_chars / 1000.0) * cost_per_1k_chars
    saved_cost_estimate = max(0.0, full_cost_estimate - incremental_cost_estimate)

    return {
        "v2_total_chars": v2_total_chars,
        "reused_chars": reused_chars,
        "retranslate_chars": retranslate_chars,
        "savings_ratio": round(savings_ratio, 4),
        "api_calls_avoided": api_calls_avoided,
        "api_calls_required": api_calls_required,
        "full_cost_estimate": round(full_cost_estimate, 5),
        "incremental_cost_estimate": round(incremental_cost_estimate, 5),
        "saved_cost_estimate": round(saved_cost_estimate, 5),
    }
