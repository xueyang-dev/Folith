"""Persisted facts behind the seven Check & Repair issue categories.

No Qt dependency. Summary writes and the workbench use the same predicates;
listing reads the saved counts, never scans document content for a badge.
"""
from __future__ import annotations


def state_facts(state: dict) -> dict:
    import core

    outcomes = core.workflow_outcomes(state)
    # 画像警告写在 state 上（阶段 1.2 在提取报告落盘之后才跑），只读提取报告就会
    # 看不见"画像失败、文档级上下文没了"（#49）。
    warnings = list((state.get("extraction_report") or {}).get("warnings") or []) \
        + list(state.get("profile_warnings") or [])
    profile_missing = any(str(w).startswith("文档画像") and "失败" in str(w) for w in warnings) \
        and not str((state.get("document_profile") or {}).get("domain") or "").strip()
    failures = [item for item in state.get("translation_failures") or []
                if isinstance(item, dict) and not item.get("recovered")
                and isinstance(item.get("offset"), int)
                and isinstance(item.get("segment_count"), int) and item["segment_count"] > 0]
    pairs = state.get("pairs") or []
    fragments = sorted({int(item["segment_index"])
                        for item in (state.get("source_quality_gate") or {}).get("flags") or []
                        if isinstance(item, dict) and item.get("segment_index") is not None
                        and not item.get("confirmed") and not item.get("resolved")
                        and 0 <= int(item["segment_index"]) < len(pairs)
                        and not pairs[int(item["segment_index"])].get("excluded")})
    ingest = state.get("streaming_ingest") if isinstance(state.get("streaming_ingest"), dict) else {}
    from folith import delivery as _delivery

    unrecovered = set(_delivery.unrecovered_scan_pages(state))
    scan_pages = [dict(row) for row in ingest.get("failed_pages") or []
                  if isinstance(row, dict) and row.get("page") in unrecovered]
    try:
        entities = [dict(item) for item in core.entity_consistency_queue(state)]
    except Exception:  # preserve the existing malformed-registry fallback
        entities = []
    count = sum((bool(failures), bool(scan_pages), bool(profile_missing), bool(fragments), bool(entities),
                 outcomes["understanding"].get("status") == "degraded",
                 outcomes["annotation"].get("status") == "partial"))
    return dict(outcomes, profile_missing=bool(profile_missing), fragments=fragments,
                entities=entities, scan_pages=scan_pages, attention_count=count,
                attention_tone="danger" if failures or scan_pages else "warning" if count else "")


def summary(job_id: str, state: dict) -> dict:
    import core

    facts = state_facts(state)
    recovery = core.recovery_summary(job_id, state)
    return {key: facts[key] for key in ("attention_count", "attention_tone")} | {
        "recovery_attention": bool(recovery.get("current_batch") or recovery.get("can_resume"))}
