"""Incremental retranslation for source update."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def retranslate_incremental(
    job_id: str,
    *,
    call_llm_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Retranslate only segments marked as changed or added, skipping unchanged segments."""
    import core

    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务: {job_id}")

    if call_llm_fn is None:
        raise ValueError("未提供翻译执行器，不能生成增量译文。")

    pairs = list(state.get("pairs") or [])
    indices_to_translate = [
        i for i, p in enumerate(pairs)
        if (p.get("retranslation_required") or not p.get("target"))
        and not (p.get("locked") or p.get("reviewed") or p.get("human_accepted")
                 or p.get("human_edited"))
    ]

    total_chars = sum(len(p.get("source") or "") for p in pairs)
    translated_chars = 0
    skipped_chars = 0

    for i in range(len(pairs)):
        pair = pairs[i]
        src = str(pair.get("source") or "")
        if i in indices_to_translate:
            new_tgt = call_llm_fn(src)
            if not isinstance(new_tgt, str) or not new_tgt.strip():
                raise ValueError("翻译执行器未返回有效译文。")

            pair["target"] = new_tgt
            if not pair.get("initial_target"):
                pair["initial_target"] = new_tgt
            pair["retranslation_required"] = False
            translated_chars += len(src)
        else:
            skipped_chars += len(src)

    # All translated
    state["pairs"] = pairs
    state["p2_done"] = all(bool(p.get("target")) and not p.get("retranslation_required")
                           for p in pairs)
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
