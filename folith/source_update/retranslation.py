"""Incremental retranslation for source update."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from folith.engine.locks import _exclusive_job_execution


def mark_segment_resolved(state: Dict[str, Any], pair: Dict[str, Any]) -> None:
    """An explicit edit or adoption resolves a pending source-update segment."""
    if not state.get("source_update_pending"):
        return
    if str(pair.get("target") or "").strip():
        pair["retranslation_required"] = False
    pairs = state.get("pairs") or []
    done = bool(pairs) and all(str(p.get("target") or "").strip()
                               and not p.get("retranslation_required") for p in pairs)
    state["p2_done"] = done
    state["source_update_pending"] = not done


@_exclusive_job_execution(0)
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
    drafts = []
    for i in indices_to_translate:
        pair = pairs[i]
        src = str(pair.get("source") or "")
        new_tgt = call_llm_fn(src)
        if not isinstance(new_tgt, str) or not new_tgt.strip():
            raise ValueError("翻译执行器未返回有效译文。")
        drafts.append({"segment_index": i, "segment_uid": pair.get("segment_uid"),
                       "source": src, "candidate_target": new_tgt,
                       "origin": "source_update"})

    committed_indexes = set()
    if drafts:
        staged = core.commit_candidates(job_id, drafts, actor="source_update")
        ids = [item["candidate_id"] for item in staged.get("created") or []]
        adopted = core.adopt_translation_candidates(job_id, ids, allow_protected=False)
        committed = set(adopted.get("committed") or [])
        committed_indexes = {item["segment_index"] for item in staged.get("created") or []
                             if item.get("candidate_id") in committed}
        state = adopted["state"]
        pairs = list(state.get("pairs") or [])
    translated_chars = sum(len(p.get("source") or "") for i, p in enumerate(pairs)
                           if i in committed_indexes)
    skipped_chars = total_chars - translated_chars
    state["p2_done"] = all(bool(p.get("target")) and not p.get("retranslation_required")
                           for p in pairs)
    state["source_update_pending"] = not state["p2_done"]
    state["review_stats"] = {
        "reviewed_segments": sum(1 for p in pairs if p.get("reviewed")),
        "total_segments": len(pairs),
    }

    metrics = {
        "total_segments": len(pairs),
        "retranslated_segments": len(committed_indexes),
        "skipped_segments": len(pairs) - len(committed_indexes),
        "total_chars": total_chars,
        "retranslated_chars": translated_chars,
        "skipped_chars": skipped_chars,
        "cost_savings_ratio": round(skipped_chars / total_chars, 4) if total_chars > 0 else 0.0,
    }
    state["incremental_retranslation_metrics"] = metrics

    core.save_job_state(job_id, state)
    return state
