"""Unified review finding contract."""
from __future__ import annotations

from typing import Any, List, Mapping, TypedDict

from .. import models


SEVERITIES = {"blocking", "actionable", "informational"}
STATUSES = {"open", "resolved", "dismissed", "stale"}


class ReviewFinding(TypedDict, total=False):
    finding_id: str
    category: str
    code: str
    severity: str
    status: str
    subject_id: str
    segment_id: int | str
    entry_id: str
    location_key: str
    occurrence_key: str
    summary: str
    explanation: str
    recommendation: str
    source_span: str | None
    target_span: str | None
    evidence_refs: List[str]
    requires_human_confirmation: bool
    input_fingerprint: str
    detector: str
    identity_stability: str
    review_event_id: str
    status_before_stale: str
    stale_reason: str
    stale_at: str
    superseded_by_review_event_id: str
    resolution_decision_id: str
    latest_decision_id: str


def _identity(raw: Mapping[str, Any]) -> str:
    existing = str(raw.get("finding_id") or raw.get("id") or "").strip()
    if existing:
        return existing
    subject = raw.get("subject_id")
    if subject is None:
        subject = raw.get("segment_id", "")
    code = raw.get("code") or raw.get("category") or raw.get("type") or "review"
    location = raw.get("location_key")
    if location in (None, ""):
        location = raw.get("occurrence_key", "")
    return models.stable_id(
        str(code), str(subject), str(raw.get("entry_id") or ""),
        str(location),
        prefix="f",
    )


def normalize_finding(
    raw: Mapping[str, Any], *, input_fingerprint: str | None = None,
) -> ReviewFinding:
    """Normalize one finding or reject an ambiguous lifecycle contract."""
    if not isinstance(raw, Mapping):
        raise ValueError("review finding must be a mapping")
    severity = str(raw.get("severity") or "").strip().lower()
    status = str(raw.get("status") or "open").strip().lower()
    category = str(raw.get("category") or raw.get("type") or "review").strip()
    fingerprint_value = str(input_fingerprint or raw.get("input_fingerprint") or "").strip()
    if severity not in SEVERITIES:
        raise ValueError(f"invalid review finding severity: {severity!r}")
    if status not in STATUSES:
        raise ValueError(f"invalid review finding status: {status!r}")
    if not category or not fingerprint_value:
        raise ValueError("review finding requires category and input_fingerprint")
    refs = raw.get("evidence_refs") or raw.get("evidence_ids") or []
    if not isinstance(refs, (list, tuple)):
        raise ValueError("review finding evidence_refs must be a list")
    subject = raw.get("subject_id")
    if subject is None:
        subject = raw.get("segment_id", "")
    out: ReviewFinding = {
        "finding_id": _identity(raw),
        "category": category,
        "code": str(raw.get("code") or category),
        "severity": severity,
        "status": status,
        "subject_id": str(subject),
        "entry_id": str(raw.get("entry_id") or ""),
        "location_key": str(raw.get("location_key") or ""),
        "occurrence_key": str(raw.get("occurrence_key") or ""),
        "summary": str(raw.get("summary") or raw.get("reason") or "").strip(),
        "explanation": str(raw.get("explanation") or "").strip(),
        "recommendation": str(raw.get("recommendation") or "").strip(),
        "source_span": raw.get("source_span"),
        "target_span": raw.get("target_span"),
        "evidence_refs": list(dict.fromkeys(
            str(ref).strip() for ref in refs if str(ref).strip()
        )),
        "requires_human_confirmation": bool(raw.get("requires_human_confirmation")),
        "input_fingerprint": fingerprint_value,
        "detector": str(raw.get("detector") or "").strip(),
    }
    if raw.get("segment_id") is not None:
        out["segment_id"] = raw["segment_id"]
    return out
