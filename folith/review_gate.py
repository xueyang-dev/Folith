"""Deterministic pre-review gating for the translation runtime.

Independent review is the largest measured cost centre of the translation
runtime.  It is valuable exactly when it can catch something the deterministic
layer did not, so the runtime spends review tokens on a batch only when the
batch carries a signal that makes independent judgement worth its price.

This module is deliberately one small rule.  It does **not** score, weight,
learn, call a model, or introduce any new subsystem: it reads signals the
pipeline has already computed before review and answers yes/no.

Two policies exist:

``always``
    Every reviewable batch is independently reviewed.  This is the production
    default and the behaviour every existing caller keeps.

``risk_selective``
    A batch with no deterministic risk signal is recorded as
    ``skipped_low_risk`` instead of being sent to the reviewer.  Skipping never
    changes deterministic QA, terminology checks, structural validation,
    persistence, or the findings already produced locally, and it never marks a
    segment reviewed, approved or TM-eligible.

The caller is responsible for recording the skip as explicit provenance; see
``translation_review_readiness`` and ``review_status``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REVIEW_MODE_ALWAYS = "always"
REVIEW_MODE_RISK_SELECTIVE = "risk_selective"
REVIEW_MODES = (REVIEW_MODE_ALWAYS, REVIEW_MODE_RISK_SELECTIVE)
DEFAULT_REVIEW_MODE = REVIEW_MODE_ALWAYS

#: ``review_status`` / event decision for a batch that was not independently
#: reviewed because deterministic gating proved it low risk.
SKIPPED_LOW_RISK = "skipped_low_risk"

#: Ordered (signal, reason) pairs.  The first signal that fires decides, so the
#: reason recorded in the audit trail is the strongest one present.
RISK_RULES: Tuple[Tuple[str, str], ...] = (
    ("blocking_findings", "blocking deterministic finding in batch"),
    ("actionable_findings", "actionable deterministic finding in batch"),
    ("repair_attempted", "deterministic repair changed this batch"),
    ("protocol_failure", "provider protocol failure during generation"),
    ("malformed_output", "empty, truncated or structurally invalid target"),
    ("entity_conflict", "entity conflict or drift on a batch segment"),
    ("source_quality_flag", "source-quality gate flagged a batch segment"),
)


def resolve_review_mode(
    explicit: Optional[str] = None,
    pipeline_config: Optional[Mapping[str, Any]] = None,
    state: Optional[Mapping[str, Any]] = None,
) -> str:
    """Resolve the review policy, most specific source first.

    ``explicit`` (an API argument) beats ``pipeline_config["review_mode"]``
    which beats ``state["review_mode"]``.  Anything unrecognised falls back to
    the production default ``always``, so a typo can never silently weaken
    review.
    """
    candidates = [explicit]
    if isinstance(pipeline_config, Mapping):
        candidates.append(pipeline_config.get("review_mode"))
    if isinstance(state, Mapping):
        candidates.append(state.get("review_mode"))
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in REVIEW_MODES:
            return text
    return DEFAULT_REVIEW_MODE


def batch_risk_signals(
    *,
    findings: Sequence[Mapping[str, Any]] = (),
    fixable: Sequence[Mapping[str, Any]] = (),
    repair_attempted: bool = False,
    protocol_failure: bool = False,
    malformed_output: bool = False,
    entity_conflicts: int = 0,
    source_quality_flags: int = 0,
    all_from_tm: bool = False,
) -> Dict[str, Any]:
    """Collect the deterministic signals a batch carries before review.

    Every input is already computed by the translation pipeline; nothing here
    inspects text or calls out to another component.
    """
    blocking = sum(1 for f in findings
                   if isinstance(f, Mapping) and f.get("severity") == "blocking")
    actionable = sum(1 for f in findings
                     if isinstance(f, Mapping) and f.get("severity") == "actionable")
    return {
        "blocking_findings": blocking,
        "actionable_findings": actionable,
        "unresolved_before_repair": len([f for f in fixable if isinstance(f, Mapping)]),
        "repair_attempted": bool(repair_attempted),
        "protocol_failure": bool(protocol_failure),
        "malformed_output": bool(malformed_output),
        "entity_conflict": int(entity_conflicts or 0),
        "source_quality_flag": int(source_quality_flags or 0),
        "all_from_tm": bool(all_from_tm),
    }


def should_run_independent_review(signals: Mapping[str, Any]) -> Tuple[bool, str]:
    """Return ``(run_review, reason)`` for one batch.

    ``reason`` is a stable machine-readable token, so the audit trail explains
    exactly which signal justified the decision.
    """
    if signals.get("all_from_tm"):
        # Every segment in the batch came from reviewed translation memory: the
        # exact content already passed an independent review when it was
        # promoted, so re-reviewing it buys nothing.
        return False, "already_reviewed_tm"
    for key, reason in RISK_RULES:
        value = signals.get(key)
        if isinstance(value, bool):
            if value:
                return True, reason
            continue
        try:
            if int(value or 0) > 0:
                return True, reason
        except (TypeError, ValueError):
            if value:
                return True, reason
    return False, "clean_deterministic"


def batch_signals_from_context(
    *,
    findings: Sequence[Mapping[str, Any]] = (),
    fixable: Sequence[Mapping[str, Any]] = (),
    repair_attempted: bool = False,
    batch_pairs: Sequence[Mapping[str, Any]] = (),
    blocked_local_indexes: Sequence[int] = (),
    protocol_failure: bool = False,
    findings_all: Sequence[Mapping[str, Any]] = (),
    segment_ids: Sequence[int] = (),
) -> Dict[str, Any]:
    """Derive the signal dict from the pipeline's own batch context."""
    ids = {int(value) for value in segment_ids}
    conflicts = 0
    for finding in findings_all:
        if not isinstance(finding, Mapping):
            continue
        if finding.get("type") != "entity_conflict":
            continue
        segment_id = finding.get("segment_id", finding.get("segment_index"))
        if isinstance(segment_id, int) and not isinstance(segment_id, bool) \
                and segment_id in ids:
            conflicts += 1
    pairs = [p for p in batch_pairs if isinstance(p, Mapping)]
    malformed = False
    for pair in pairs:
        target = str(pair.get("target") or "")
        if not target.strip():
            malformed = True
            break
    all_from_tm = bool(pairs) and all(bool(p.get("from_tm")) for p in pairs)
    return batch_risk_signals(
        findings=findings,
        fixable=fixable,
        repair_attempted=repair_attempted,
        protocol_failure=protocol_failure,
        malformed_output=malformed,
        entity_conflicts=conflicts,
        source_quality_flags=len(list(blocked_local_indexes or ())),
        all_from_tm=all_from_tm,
    )


def describe_signals(signals: Mapping[str, Any]) -> List[str]:
    """Human-readable list of the signals that fired (for logs and reports)."""
    fired = []
    for key, reason in RISK_RULES:
        value = signals.get(key)
        if isinstance(value, bool):
            if value:
                fired.append(reason)
        else:
            try:
                if int(value or 0) > 0:
                    fired.append(reason)
            except (TypeError, ValueError):
                if value:
                    fired.append(reason)
    if signals.get("all_from_tm"):
        fired.append("already_reviewed_tm")
    return fired
