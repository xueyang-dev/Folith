"""Readiness metrics and read models across the translation pipeline (Phase 3).

This module defines three explicit, independent readiness dimensions:
1. **Draft Ready**: All required source segments in scope have a non-empty,
   structural initial translation; source quarantine follows the recorded gate,
   and unrecovered source pages prevent readiness.
2. **Translation Ready**: Draft Ready is met, plus the document satisfies the
   task configuration's QA/review and consistency gates (including review
   readiness and zero unresolved blocking findings).
3. **Enhanced Delivery Ready**: Translation Ready is met, plus all user-selected
   enhanced outputs (automatic annotations, MTI report) are completely ready for
   the current translation version.

None of these dimensions alters existing delivery approval conditions,
overrides human authority, or exposes sensitive document content.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from folith import delivery, translation_target


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def draft_readiness(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Assess whether initial draft translation is complete and structural."""
    state = state if isinstance(state, Mapping) else {}
    pairs = state.get("pairs") or []
    total_segments = len(pairs)

    quality_gate = state.get("source_quality_gate") or {}
    blocked_source_indexes = {
        item.get("segment_index")
        for item in (quality_gate.get("flags") or [])
        if isinstance(item, Mapping)
        and isinstance(item.get("segment_index"), int)
        and not isinstance(item.get("segment_index"), bool)
    }

    required_indexes = [i for i in range(total_segments) if i not in blocked_source_indexes]
    required_count = len(required_indexes)

    translated_count = 0
    missing_indexes = []
    for i in required_indexes:
        target = str(pairs[i].get("target") or "").strip()
        if target:
            translated_count += 1
        else:
            missing_indexes.append(i)

    p2_done = bool(state.get("p2_done"))
    missing_pages = delivery.unrecovered_scan_pages(state)
    target_report = translation_target.validate_translation_pairs(pairs)
    invalid_targets = [issue for issue in target_report["issues"]
                       if issue["severity"] == "blocking"
                       and issue.get("segment_index") not in blocked_source_indexes]
    missing_pairs = max(0, len(state.get("paras") or []) - total_segments)
    is_ready = bool(p2_done and not missing_pages and not missing_pairs
                    and not invalid_targets and translated_count == required_count)

    if total_segments == 0 and not p2_done:
        status = "empty"
    elif is_ready:
        status = "ready"
    elif translated_count > 0:
        status = "in_progress"
    else:
        status = "not_started"

    truth = state.get("translation_truth") or {}
    truth_version = int(truth.get("version") or 0) if isinstance(truth, Mapping) else 0

    return {
        "ready": is_ready,
        "status": status,
        "total_segments": total_segments,
        "required_segments": required_count,
        "translated_segments": translated_count,
        "missing_segments": len(missing_indexes),
        "blocked_segments": len(blocked_source_indexes),
        "missing_source_pages": len(missing_pages),
        "missing_pairs": missing_pairs,
        "invalid_targets": len(invalid_targets),
        "translation_version": truth_version,
    }


def translation_readiness(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Assess whether translation meets all required QA, review, and consistency gates."""
    state = state if isinstance(state, Mapping) else {}
    draft = draft_readiness(state)

    from folith.translation_evidence import translation_review_readiness
    review = translation_review_readiness(state)

    review_required = bool(review.get("required"))
    review_ready = bool(review.get("ready"))

    blocking_findings = delivery.unresolved_blocking(state)
    has_blocking = bool(blocking_findings)

    is_ready = bool(draft["ready"] and (not review_required or review_ready) and not has_blocking)

    if not draft["ready"]:
        status = "draft_incomplete"
    elif has_blocking:
        status = "blocking_findings"
    elif review_required and not review_ready:
        status = "review_pending"
    else:
        status = "ready"

    return {
        "ready": is_ready,
        "status": status,
        "draft_ready": draft["ready"],
        "review_required": review_required,
        "review_ready": review_ready,
        "review_coverage_status": review.get("coverage_status"),
        "reviewed_count": int(review.get("reviewed_count") or 0),
        "skipped_count": int(review.get("skipped_count") or 0),
        "has_blocking": has_blocking,
        "blocking_finding_count": len(blocking_findings),
    }


def enhanced_delivery_readiness(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Assess whether optional user-requested deliverables (annotations, report) are ready."""
    state = state if isinstance(state, Mapping) else {}
    trans = translation_readiness(state)

    pipeline_cfg = state.get("pipeline_config")
    pipeline_cfg = pipeline_cfg if isinstance(pipeline_cfg, Mapping) else {}

    annotate_requested = bool(
        pipeline_cfg.get("enable_annotate")
        or state.get("enable_annotate")
        or state.get("annotations_done")
        or state.get("annotations_done_offset")
    )
    report_requested = bool(
        pipeline_cfg.get("enable_report")
        or state.get("report_enabled")
        or state.get("enable_report")
        or state.get("p3_done")
    )

    batch_results = state.get("annotation_batches")
    batch_results = batch_results if isinstance(batch_results, Mapping) else {}
    failed_batches = sum(
        1 for r in batch_results.values()
        if isinstance(r, Mapping) and r.get("status") in {"request_failed", "parse_failed", "stale"}
    )
    annotations_ready = bool(state.get("annotations_done") and failed_batches == 0)

    p3_done = bool(state.get("p3_done"))
    report_ready = bool(p3_done and delivery.report_ready(state))

    is_ready = bool(
        trans["ready"]
        and (not annotate_requested or annotations_ready)
        and (not report_requested or report_ready)
    )

    if not trans["ready"]:
        status = "translation_incomplete"
    elif annotate_requested and failed_batches > 0:
        status = "annotation_failed"
    elif annotate_requested and not annotations_ready:
        status = "annotation_pending"
    elif report_requested and not report_ready:
        status = "report_pending"
    elif not annotate_requested and not report_requested:
        status = "ready_basic_only"
    else:
        status = "ready"

    return {
        "ready": is_ready,
        "status": status,
        "translation_ready": trans["ready"],
        "annotations_requested": annotate_requested,
        "annotations_ready": annotations_ready,
        "annotation_failed_batches": failed_batches,
        "report_requested": report_requested,
        "report_ready": report_ready,
    }


def workflow_readiness(state: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Canonical aggregated readiness snapshot across all three dimensions."""
    state = state if isinstance(state, Mapping) else {}
    draft = draft_readiness(state)
    trans = translation_readiness(state)
    enhanced = enhanced_delivery_readiness(state)

    return {
        "draft": draft,
        "translation": trans,
        "enhanced_delivery": enhanced,
        "overall_ready": enhanced["ready"],
        "milestones": dict(state.get("readiness_milestones") or {}),
    }


def record_readiness_milestone(
    job_id: Optional[str],
    state: Dict[str, Any],
    milestone_name: str,
    *,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Record a stable timestamped readiness transition without leaking sensitive data."""
    if not isinstance(state, dict):
        return False
    project = {"draft_ready": draft_readiness,
               "translation_ready": translation_readiness,
               "enhanced_delivery_ready": enhanced_delivery_readiness}[milestone_name]
    if not project(state)["ready"]:
        return False
    cfg = state.get("pipeline_config") or {}
    identity = {
        "translation_version": int((state.get("translation_truth") or {}).get("version") or 0),
        "policy": {
            "review_required": bool(state.get("translation_core_review_required")),
            "review_mode": state.get("review_mode") or "always",
            "annotations_requested": bool(cfg.get("enable_annotate") or state.get("enable_annotate")),
            "report_requested": bool(cfg.get("enable_report") or state.get("report_enabled")),
        },
    }
    milestones = state.setdefault("readiness_milestones", {})
    previous = milestones.get(milestone_name) or {}
    if all(previous.get(key) == value for key, value in identity.items()):
        return False
    now = _now_iso()
    record = {**(extra_metadata or {}), "reached_at": now, **identity}
    milestones[milestone_name] = record

    if job_id:
        try:
            from folith import trace
            run = trace.get_active_run(job_id)
            if run:
                run.record_point("milestone", phase=milestone_name, **identity,
                                 **(extra_metadata or {}))
        except Exception:  # noqa: BLE001 - non-essential telemetry must not interrupt execution
            pass
    return True
