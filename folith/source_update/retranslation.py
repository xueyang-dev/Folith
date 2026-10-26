"""Incremental retranslation for source update."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def retranslate_incremental(
    job_id: str,
    *,
    call_llm_fn: Optional[Callable[[str], str]] = None,
    mock_target_suffix: str = " (重新翻译)",
) -> Dict[str, Any]:
    """Retranslate only segments marked as changed or added, skipping unchanged segments."""
    import core

    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务: {job_id}")

    pairs = list(state.get("pairs") or [])
    indices_to_translate = [
        i for i, p in enumerate(pairs)
        if p.get("retranslation_required") or not p.get("target")
    ]

    total_chars = sum(len(p.get("source") or "") for p in pairs)
    translated_chars = 0
    skipped_chars = 0

    for i in range(len(pairs)):
        pair = pairs[i]
        src = str(pair.get("source") or "")
        if i in indices_to_translate:
            if call_llm_fn is not None:
                new_tgt = call_llm_fn(src)
            else:
                new_tgt = f"译文: {src}{mock_target_suffix}"

            pair["target"] = new_tgt
            if not pair.get("initial_target"):
                pair["initial_target"] = new_tgt
            pair["retranslation_required"] = False
            translated_chars += len(src)
        else:
            skipped_chars += len(src)

    # All translated
    state["pairs"] = pairs
    state["p2_done"] = all(bool(p.get("target")) for p in pairs)
    state["review_stats"] = {
        "reviewed_segments": sum(1 for p in pairs if p.get("reviewed")),
        "total_segments": len(pairs),
    }

    metrics = {
        "total_segments": len(pairs),
        "retranslated_segments": len(indices_to_translate),
        "skipped_segments": len(pairs) - len(indices_to_translate),
        "total_chars": total_chars,
        "retranslated_chars": translated_chars,
        "skipped_chars": skipped_chars,
        "cost_savings_ratio": round(skipped_chars / total_chars, 4) if total_chars > 0 else 0.0,
    }
    state["incremental_retranslation_metrics"] = metrics

    core.save_job_state(job_id, state)
    return state
