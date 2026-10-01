"""Auditable human decisions for review findings."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Tuple, TypedDict

from .. import models
from .findings import ReviewFinding


DECISION_STATUSES = {
    "accept_resolution": "resolved",
    "dismiss": "dismissed",
    "request_revision": "open",
}


class HumanDecision(TypedDict, total=False):
    decision_id: str
    finding_id: str
    decision: str
    actor: str
    actor_type: str
    note: str
    decided_at: str
    input_fingerprint: str
    status: str
    status_before_stale: str
    stale_reason: str
    stale_at: str
    superseded_by_decision_id: str
    superseded_by_review_event_id: str


def record_human_decision(
    finding: Mapping[str, Any],
    decision: str,
    actor: str,
    *,
    actor_type: str,
    current_fingerprint: str,
    note: str = "",
    decided_at: str | None = None,
) -> Tuple[ReviewFinding, HumanDecision]:
    """Record a fail-closed decision on one current, human-required finding."""
    decision = str(decision or "").strip().lower()
    actor = str(actor or "").strip()
    if actor_type != "human" or not actor:
        raise ValueError("only an identified human may record a human decision")
    if decision not in DECISION_STATUSES:
        raise ValueError(f"invalid human decision: {decision!r}")
    finding_id = str(finding.get("finding_id") or "").strip()
    if not finding_id:
        raise ValueError("finding has no finding_id")
    if finding.get("identity_stability") == "provisional":
        raise ValueError("human decisions require a stable finding identity")
    if finding.get("status") != "open" or not finding.get("requires_human_confirmation") \
            or finding.get("severity") == "informational":
        raise ValueError("human decisions require an open finding that requests confirmation")
    input_fingerprint = str(finding.get("input_fingerprint") or "")
    if not input_fingerprint:
        raise ValueError("finding has no review input fingerprint")
    if input_fingerprint != current_fingerprint:
        raise ValueError("cannot decide a stale finding")
    timestamp = decided_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    decision_id = models.stable_id(
        finding_id, input_fingerprint, actor, decision, timestamp, prefix="d",
    )
    record: HumanDecision = {
        "decision_id": decision_id,
        "finding_id": finding_id,
        "decision": decision,
        "actor": actor,
        "actor_type": "human",
        "note": str(note or "").strip(),
        "decided_at": timestamp,
        "input_fingerprint": input_fingerprint,
        "status": "current",
    }
    updated: ReviewFinding = dict(finding)  # type: ignore[assignment]
    updated["status"] = DECISION_STATUSES[decision]
    updated["latest_decision_id"] = decision_id
    if updated["status"] in {"resolved", "dismissed"}:
        updated["resolution_decision_id"] = decision_id
    return updated, record
