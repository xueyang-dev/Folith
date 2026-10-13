"""Bounded evidence access and evidence-guided translation review."""
from __future__ import annotations

import json
import re
from collections.abc import Sequence as _AbcSequence
from datetime import datetime, timezone
from typing import (
    Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple,
)

from . import critical_path as _critical_path
from . import models, terminology, translation_core
from .review_gate import SKIPPED_LOW_RISK as _SKIPPED_LOW_RISK

MAX_REQUESTS_PER_ROUND = 5
MAX_EVIDENCE_PAYLOAD_BYTES = 24000
DIAGNOSTIC_FIELDS = (
    "category", "summary", "source_span", "target_span", "explanation",
    "recommendation", "confidence", "detector",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _segment_id(value: Mapping[str, Any]) -> Optional[int]:
    segment_id = value.get("segment_id")
    if isinstance(segment_id, bool) or not isinstance(segment_id, int):
        segment_id = value.get("segment_index")
    return segment_id if isinstance(segment_id, int) and not isinstance(
        segment_id, bool) else None


def _review_segment_ids(event: Mapping[str, Any]) -> List[int]:
    values = event.get("segment_ids") or (event.get("completion_receipt") or {}).get(
        "reviewed_segment_ids") or []
    return [value for value in values
            if isinstance(value, int) and not isinstance(value, bool)]


def _review_dependency_segment_ids(event: Mapping[str, Any]) -> List[int]:
    """Return the persisted segment dependencies for one review event."""
    translation_core = event.get("translation_core") or {}
    values = event.get("dependency_segment_ids") or \
        translation_core.get("dependency_segment_ids") or _review_segment_ids(event)
    return [value for value in values
            if isinstance(value, int) and not isinstance(value, bool)]


def _current_translation_review(event: Mapping[str, Any]) -> bool:
    return event.get("review_scope") == "current_translation" or \
        event.get("phase") == "formal_review"


class ReviewIndex(NamedTuple):
    """One pass over ``review_evidence``, answering per-segment questions in O(1).

    ``current``    segment → latest non-stale current-translation review (what
                   :func:`current_review_event` returns);
    ``reviewed``   segments that any current-translation review ever covered
                   (stale or not) — "stale" versus "missing" in the readiness;
    ``latest_any`` raw segment id → latest event of any kind listing it (the
                   workbench's historical lookup; keys keep ``in``-list equality).
    """

    current: Dict[Any, Dict[str, Any]]
    reviewed: frozenset
    latest_any: Dict[Any, Mapping[str, Any]]


def review_index(state: Mapping[str, Any]) -> ReviewIndex:
    """Build the per-segment review lookups with one reverse pass.

    Scanning every event for every segment made opening a 3 700-segment task with
    ~1 000 review events take 36 million calls (about 10 s on the GUI thread).
    The conditions are exactly those of the per-segment scans they replace.
    """
    current: Dict[Any, Dict[str, Any]] = {}
    reviewed = set()
    latest_any: Dict[Any, Mapping[str, Any]] = {}
    for event in reversed(state.get("review_evidence") or []):
        if not isinstance(event, Mapping):
            continue
        raw = event.get("segment_ids") or (event.get("completion_receipt") or {}).get(
            "reviewed_segment_ids") or []
        for value in raw:
            try:
                latest_any.setdefault(value, event)
            except TypeError:  # unhashable ids can never equal an int segment id
                continue
        if not _current_translation_review(event):
            continue
        ids = _review_segment_ids(event)
        reviewed.update(ids)
        if not isinstance(event, dict) or event.get("freshness_status") == "stale":
            continue
        stale = set(event.get("stale_segment_ids") or [])
        for segment_id in ids:
            if segment_id not in stale:
                current.setdefault(segment_id, event)
    return ReviewIndex(current, frozenset(reviewed), latest_any)


def current_review_event(
    state: Mapping[str, Any], segment_id: int, *, index: Optional[ReviewIndex] = None,
) -> Optional[Dict[str, Any]]:
    """Return the latest non-stale current-translation review for one segment.

    Callers that ask about many segments should build :func:`review_index` once
    and pass it; a single lookup keeps the early-exit scan.
    """
    if index is not None:
        try:
            return index.current.get(segment_id)
        except TypeError:
            return None
    for event in reversed(state.get("review_evidence") or []):
        if not isinstance(event, dict) or not _current_translation_review(event):
            continue
        if segment_id not in _review_segment_ids(event):
            continue
        if segment_id in set(event.get("stale_segment_ids") or []):
            continue
        if event.get("freshness_status") == "stale":
            continue
        return event
    return None


#: Fields this function may write on an event / finding / decision.  Listed so
#: the optional ``patches`` collector can report exactly what it changed without
#: comparing two states (a generic diff is explicitly out of bounds).
_STALE_FIELDS = (
    "stale_segment_ids", "freshness_status", "status_before_stale",
    "stale_reason", "stale_at", "superseded_by_review_event_id",
)
_DECISION_STALE_FIELDS = (
    "status", "status_before_stale", "stale_reason", "stale_at",
    "superseded_by_review_event_id",
)


def _record_patch(patches: Optional[Dict[str, Any]], bucket: str,
                  key: str, fields: Sequence[str],
                  item: Mapping[str, Any]) -> None:
    """Report one in-place update to an item an earlier journal record carried.

    The journal replays these by identity, so an item that has no stable
    identity cannot be described.  That is not silently ignored: the collector
    is flagged incomplete, and the caller falls back to a full snapshot.
    """
    if patches is None:
        return
    if not key:
        patches["incomplete"] = True
        return
    entry = {"fields": {name: item.get(name) for name in fields
                        if name in item}}
    entry["review_event_id" if bucket == "review_events" else "finding_id"] = key
    patches.setdefault(bucket, []).append(entry)


def mark_runtime_review_stale(
    state: Dict[str, Any], segment_ids: Sequence[int], reason: str, *,
    stale_at: Optional[str] = None, superseded_by: Optional[str] = None,
    exclude_review_event_id: Optional[str] = None,
    dependency_change: bool = False,
    review_event_ids: Optional[Sequence[str]] = None,
    patches: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    """Preserve and stale review artifacts by segment or dependency event.

    ``patches`` is an optional collector.  When supplied, every in-place update
    this function applies is reported as a typed, identity-keyed patch so the
    state journal can replay it.  Passing nothing keeps the behaviour identical.
    """
    ids = {value for value in segment_ids
           if isinstance(value, int) and not isinstance(value, bool)}
    selected_event_ids = {str(value) for value in review_event_ids or [] if str(value)}
    timestamp = stale_at or _now_iso()
    affected_events = set()
    for event in state.get("review_evidence") or []:
        if not isinstance(event, dict) or not _current_translation_review(event):
            continue
        event_id = str(event.get("review_event_id") or "")
        event_segments = set(_review_segment_ids(event))
        overlap = ids.intersection(event_segments)
        dependency_overlap = ids.intersection(_review_dependency_segment_ids(event))
        selected = event_id in selected_event_ids
        if not (selected or overlap or
                (dependency_change and dependency_overlap)):
            continue
        affected_events.add(event_id)
        stale_segments = event_segments if (
            selected or dependency_change) else overlap
        event["stale_segment_ids"] = sorted(
            set(event.get("stale_segment_ids") or []).union(stale_segments))
        if set(_review_segment_ids(event)).issubset(event["stale_segment_ids"]):
            if event.get("freshness_status") != "stale":
                event["status_before_stale"] = event.get("decision") or "current"
            event["freshness_status"] = "stale"
        else:
            event["freshness_status"] = "partial_stale"
        event.setdefault("stale_reason", str(reason or "review inputs changed"))
        event.setdefault("stale_at", timestamp)
        if superseded_by:
            event["superseded_by_review_event_id"] = superseded_by
        _record_patch(patches, "review_events", event_id, _STALE_FIELDS, event)

    affected_findings = set()
    for finding in state.get("findings") or []:
        if not isinstance(finding, dict) or finding.get("type") != "review" \
                or not finding.get("input_fingerprint") \
                or finding.get("review_event_id") == exclude_review_event_id:
            continue
        finding_event_id = str(finding.get("review_event_id") or "")
        event_scoped = finding_event_id in affected_events
        if dependency_change or selected_event_ids:
            if not event_scoped:
                continue
        elif _segment_id(finding) not in ids:
            continue
        finding_key = str(finding.get("finding_id") or "")
        affected_findings.add(finding_key)
        if finding.get("status") != "stale":
            finding["status_before_stale"] = finding.get("status") or "open"
            finding["status"] = "stale"
        finding.setdefault("stale_reason", str(reason or "review inputs changed"))
        finding.setdefault("stale_at", timestamp)
        if superseded_by:
            finding["superseded_by_review_event_id"] = superseded_by
        _record_patch(patches, "findings", finding_key, _DECISION_STALE_FIELDS,
                      finding)

    decision_count = 0
    for decision in state.get("human_actions") or []:
        if not isinstance(decision, dict) or decision.get("record_type") not in {
                "human_decision", "delivery_risk_acceptance"}:
            continue
        decision_finding_ids = {
            str(decision.get("finding_id") or ""),
            str(decision.get("translation_core_finding_id") or ""),
        }
        if not decision_finding_ids.intersection(affected_findings):
            continue
        if decision.get("status") != "stale":
            decision["status_before_stale"] = decision.get("status") or "current"
            decision["status"] = "stale"
            decision_count += 1
        decision.setdefault("stale_reason", str(reason or "review inputs changed"))
        decision.setdefault("stale_at", timestamp)
        if superseded_by:
            decision["superseded_by_review_event_id"] = superseded_by
    return {
        "events": len(affected_events),
        "findings": len(affected_findings),
        "decisions": decision_count,
    }


def reconcile_runtime_review_truth(state: Dict[str, Any]) -> Dict[str, int]:
    """Detect dependency changes even when state was edited outside core helpers."""
    stale_ids = set()
    stale_event_ids = set()
    pairs = state.get("pairs") or []
    paragraphs = state.get("paras") or []
    glossary = models.normalize_glossary(
        (state.get("glossary_frozen") or {}).get("entries")
        or state.get("glossary") or [])
    current_glossary_hash = models.glossary_hash(glossary)
    for event in state.get("review_evidence") or []:
        if not isinstance(event, dict) or not _current_translation_review(event) \
                or event.get("freshness_status") == "stale":
            continue
        translation_core = event.get("translation_core") or {}
        event_glossary_hash = str(
            translation_core.get("glossary_hash") or event.get("glossary_hash") or "")
        if event_glossary_hash and event_glossary_hash != current_glossary_hash \
                and event.get("review_event_id"):
            stale_event_ids.add(str(event["review_event_id"]))
        truths = list(translation_core.get("dependency_truth") or [])
        truths.extend(translation_core.get("review_truth") or [])
        for truth in truths:
            if not isinstance(truth, Mapping):
                continue
            segment_id = truth.get("segment_id")
            if isinstance(segment_id, bool) or not isinstance(segment_id, int) \
                    or segment_id < 0 or segment_id >= max(len(pairs), len(paragraphs)):
                stale_ids.add(segment_id)
                continue
            pair = pairs[segment_id] if segment_id < len(pairs) else {}
            if truth.get("source_from_paragraphs"):
                current_source = str(
                    paragraphs[segment_id] if segment_id < len(paragraphs) else "")
            else:
                current_source = str(pair.get("source") or (
                    paragraphs[segment_id] if segment_id < len(paragraphs) else ""))
            current_target = str(
                (pair.get("accepted_target") or pair.get("target") or "")
                if truth.get("target_from_accepted") else (pair.get("target") or ""))
            source_changed = current_source != str(truth.get("source") or "")
            target_changed = truth.get("target_checked", True) \
                and current_target != str(truth.get("target") or "")
            if source_changed or target_changed:
                stale_ids.add(segment_id)
                if event.get("review_event_id"):
                    stale_event_ids.add(str(event["review_event_id"]))
    valid_ids = [value for value in stale_ids
                 if isinstance(value, int) and not isinstance(value, bool)]
    if not valid_ids and not stale_event_ids:
        return {"events": 0, "findings": 0, "decisions": 0}
    return mark_runtime_review_stale(
        state, valid_ids, "persisted translation truth changed after review",
        dependency_change=True, review_event_ids=sorted(stale_event_ids))


def register_runtime_review_event(
    state: Dict[str, Any], event: Dict[str, Any],
    findings: Sequence[Dict[str, Any]], review_event_id: str,
    segment_ids: Sequence[int], *, phase: str = "formal_review",
    patches: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Make one review current and supersede only its reviewed segments."""
    ids = [value for value in segment_ids
           if isinstance(value, int) and not isinstance(value, bool)]
    mark_runtime_review_stale(
        state, ids, "superseded by a current independent review",
        superseded_by=review_event_id,
        exclude_review_event_id=review_event_id, patches=patches)
    event.update({
        "phase": phase,
        "review_scope": "current_translation",
        "review_event_id": review_event_id,
        "segment_ids": ids,
        "freshness_status": "current",
        "stale_segment_ids": [],
        "finding_ids": [str(item.get("finding_id") or "")
                        for item in findings if item.get("finding_id")],
    })
    translation_core = event.get("translation_core") or {}
    dependency_ids = event.get("dependency_segment_ids") or \
        translation_core.get("dependency_segment_ids") or ids
    event["dependency_segment_ids"] = [value for value in dependency_ids
                                       if isinstance(value, int)
                                       and not isinstance(value, bool)]
    for finding in findings:
        finding["review_event_id"] = review_event_id
    state.setdefault("review_evidence", []).append(event)
    return event


def translation_review_readiness(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate only current required review state; history remains audit-only."""
    required = bool(state.get("translation_core_review_required"))
    blocked_source_indexes = {
        item.get("segment_index")
        for item in (state.get("source_quality_gate") or {}).get("flags") or []
        if isinstance(item, Mapping)
        and isinstance(item.get("segment_index"), int)
        and not isinstance(item.get("segment_index"), bool)
    }
    expected = [index for index in range(len(state.get("pairs") or []))
                if index not in blocked_source_indexes
                and str(state["pairs"][index].get("target") or "").strip()]
    base = {
        "required": required,
        "expected_segment_ids": expected,
        "current_segment_ids": [],
        "stale_segment_ids": [],
        "missing_segment_ids": [],
        "failed_segment_ids": [],
        # Segments that the deterministic pre-review gate recorded as
        # ``skipped_low_risk``.  They are explicitly *not* independently
        # reviewed, and are reported separately from reviewed segments so no
        # consumer can present a skip as a pass.
        "skipped_segment_ids": [],
        "review_mode": str(state.get("review_mode") or "always"),
        "blocking_finding_ids": [],
        "decision_errors": [],
    }
    if not required:
        return {**base, "ready": True, "status": "not_required"}

    events = [event for event in state.get("review_evidence") or []
              if isinstance(event, Mapping) and _current_translation_review(event)]
    index = review_index(state)
    stale_ids, missing_ids, failed_ids, current_ids = [], [], [], []
    skipped_ids: List[int] = []
    current_events: Dict[int, Dict[str, Any]] = {}
    for segment_id in expected:
        event = index.current.get(segment_id)
        if event is None:
            (stale_ids if segment_id in index.reviewed else missing_ids).append(segment_id)
            continue
        current_events[segment_id] = event
        receipt = event.get("completion_receipt") or {}
        if event.get("decision") == "failed" or receipt.get("status") == "failed":
            failed_ids.append(segment_id)
        elif _SKIPPED_LOW_RISK in (
                event.get("decision"), receipt.get("status")):
            skipped_ids.append(segment_id)
        else:
            current_ids.append(segment_id)

    blocking_ids, decision_errors = [], []
    actions = state.get("human_actions") or []
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping) or finding.get("type") != "review" \
                or finding.get("severity") != "blocking" \
                or not finding.get("requires_human_confirmation"):
            continue
        segment_id = _segment_id(finding)
        event = current_events.get(segment_id)
        if event is None or event.get("review_event_id") != finding.get(
                "review_event_id"):
            continue
        finding_id = str(finding.get("finding_id") or "")
        status = str(finding.get("status") or "open")
        risk_acceptance = next((item for item in reversed(actions)
                                if isinstance(item, Mapping)
                                and item.get("record_type") ==
                                "delivery_risk_acceptance"
                                and item.get("action") == "accepted_risk"
                                and item.get("status") == "current"
                                and item.get("actor_type") == "human"
                                and str(item.get("translation_core_finding_id") or "") ==
                                finding_id
                                and item.get("review_event_id") ==
                                event.get("review_event_id")
                                and item.get("input_fingerprint") ==
                                finding.get("input_fingerprint")
                                and str(item.get("actor") or "").strip()), None)
        if risk_acceptance is not None and finding.get("resolved"):
            continue
        if status == "open":
            blocking_ids.append(finding_id)
            decision_errors.append({"finding_id": finding_id, "status": "missing"})
            continue
        if status not in {"resolved", "dismissed"}:
            blocking_ids.append(finding_id)
            decision_errors.append({"finding_id": finding_id, "status": "stale"})
            continue
        decision_id = str(finding.get("latest_decision_id") or "")
        decision = next((item for item in reversed(actions)
                         if isinstance(item, Mapping)
                         and item.get("decision_id") == decision_id), None)
        if decision is None:
            blocking_ids.append(finding_id)
            decision_errors.append({"finding_id": finding_id, "status": "missing"})
        elif decision.get("status") != "current" \
                or decision.get("actor_type") != "human" \
                or decision.get("input_fingerprint") != finding.get("input_fingerprint"):
            blocking_ids.append(finding_id)
            decision_errors.append({"finding_id": finding_id, "status": "stale"})

    base.update({
        "current_segment_ids": current_ids,
        "stale_segment_ids": stale_ids,
        "missing_segment_ids": missing_ids,
        "failed_segment_ids": failed_ids,
        "skipped_segment_ids": skipped_ids,
        "blocking_finding_ids": blocking_ids,
        "decision_errors": decision_errors,
    })
    if failed_ids:
        status = "failed"
    elif stale_ids or any(item["status"] == "stale" for item in decision_errors):
        status = "stale"
    elif blocking_ids or (missing_ids and current_ids):
        status = "missing"
    elif missing_ids:
        status = "not_run" if not events else "missing"
    else:
        status = "current"
    return {**base, "ready": status == "current", "status": status}


def record_runtime_human_decision(
    state: Dict[str, Any], finding_id: str, decision: str, actor: str, *,
    actor_type: str, note: str = "", decided_at: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Apply Translation Core HumanDecision to the current persisted finding."""
    candidates = [item for item in state.get("findings") or []
                  if isinstance(item, dict)
                  and str(item.get("finding_id") or "") == str(finding_id or "")]
    finding = next((item for item in reversed(candidates)
                    if _segment_id(item) is not None
                    and (current_review_event(state, _segment_id(item)) or {}).get(
                        "review_event_id") == item.get("review_event_id")), None)
    if finding is None and candidates:
        finding = candidates[-1]
    if finding is None:
        raise ValueError("finding has no current persisted record")
    segment_id = _segment_id(finding)
    if segment_id is None:
        raise ValueError("finding has no global segment_id")
    event = current_review_event(state, segment_id)
    if event is None or event.get("review_event_id") != finding.get("review_event_id"):
        raise ValueError("cannot decide a stale finding")
    current_fingerprint = str(
        (event.get("translation_core") or {}).get(
            "final_consumed_input_fingerprint") or "")
    if not current_fingerprint:
        raise ValueError("current review has no input fingerprint")
    updated, record = translation_core.record_human_decision(
        finding, decision, actor, actor_type=actor_type,
        current_fingerprint=current_fingerprint, note=note, decided_at=decided_at)
    for old in state.get("human_actions") or []:
        if isinstance(old, dict) and old.get("record_type") == "human_decision" \
                and old.get("finding_id") == updated["finding_id"] \
                and old.get("status") == "current":
            old["status"] = "superseded"
            old["superseded_by_decision_id"] = record["decision_id"]
    updated["resolved"] = updated["status"] in {"resolved", "dismissed"}
    if updated["resolved"]:
        updated["resolution"] = {
            "action": record["decision"], "note": record["note"],
            "timestamp": record["decided_at"], "actor": record["actor"],
            "decision_id": record["decision_id"],
        }
    else:
        updated.pop("resolution", None)
    finding.clear()
    finding.update(updated)
    audit = {
        **record,
        "record_type": "human_decision",
        "action": record["decision"],
        "timestamp": record["decided_at"],
        "review_event_id": event.get("review_event_id"),
    }
    state.setdefault("human_actions", []).append(audit)
    return state, finding, audit


class _ProjectedPairs(_AbcSequence):
    """Read-only ``pairs`` image that materialises only reachable indices.

    A review packet can only ever read the current batch plus its bounded
    dependency set, yet the historical projection rebuilt a fresh ``dict`` for
    *every* committed pair on *every* call -- O(n) objects per batch, i.e.
    O(n^2) per document, none of which any consumer dereferenced.

    This keeps the exact ``len()`` of the old list (so
    ``_translation_truth``'s ``0 <= index < len(pairs)`` bound, including its
    padding-to-``max(segment_ids)+1`` behaviour, is unchanged) while exposing
    stored values only for indices that were actually touched.  Misses return a
    fresh ``{}``, matching what the old padding loop produced.
    """

    __slots__ = ("_length", "_values")

    def __init__(self, length: int, values: Dict[int, Dict[str, Any]]) -> None:
        self._length = int(length)
        self._values = values

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: Any) -> Any:
        if isinstance(index, slice):
            return [self[position]
                    for position in range(*index.indices(self._length))]
        if index < 0:
            index += self._length
        return self._values.get(index, {})


class _ReviewGlossaryScope(NamedTuple):
    """Everything one review batch derives from one immutable glossary."""

    memory: Dict[str, Any]
    entries: List[Dict[str, Any]]
    glossary_hash: str
    relevant_glossary: List[Dict[str, Any]]


def _review_glossary_scope(
    state: Mapping[str, Any], glossary: Sequence[Dict[str, Any]],
    sources: Sequence[str], segment_ids: Sequence[int],
) -> _ReviewGlossaryScope:
    """Derive the whole review-batch glossary view exactly once.

    A review batch needs three things out of the same immutable glossary: the
    normalised entries, the glossary revision hash, and the review-relevant
    subset.  Historically each was derived twice -- once here and again inside
    :func:`translation_core.build_review_packet` -- and the
    ``project_memory_from_state`` wrapper added a *third*
    ``normalize_glossary`` / ``glossary_hash`` pass on top of it.  Every one of
    those derivations is pure and took byte-identical inputs, so the repeats
    were duplicated work rather than semantic difference: the Phase 7
    ``ctx-heavy`` decomposition charged ~59% of ``review_input_packet_build``
    to them.

    The single ``occurrences``/term-match scan below serves **both** consumers
    at once.  ``build_review_packet`` wants every match; Project Memory only
    the ``locked`` ones, which are an order-preserving sub-sequence of it.
    """
    with _critical_path.phase("review_input_memory_normalize") as _norm_sink:
        _norm_sink.traversed(len(glossary))
        _norm_sink.materialized(len(glossary))
        entries = models.normalize_glossary(glossary)
    with _critical_path.phase("review_input_memory_hash") as _hash_sink:
        _hash_sink.traversed(len(entries))
        glossary_hash = models.glossary_hash(entries)
    ids = set(segment_ids)
    with _critical_path.phase("review_input_memory_compile") as _compile_sink:
        # Phase 8: the whole-glossary scan below used to run through the term-level
        # 4096-entry LRU, which a glossary larger than that thrashes on every batch,
        # re-compiling every pattern once per batch.  The revision-keyed compiled
        # view compiles each unique pattern **once per glossary revision**; this
        # phase records that one-time build so the amortisation is visible instead
        # of being hidden inside ``review_input_memory_scan``.
        _compile_sink.traversed(len(entries))
        matcher = terminology.compiled_glossary(entries)
        _compile_sink.materialized(matcher.pattern_count)
    with _critical_path.phase("review_input_memory_scan") as _scan_sink:
        # O(G x S): every glossary entry tested against every source in the
        # batch.  Recorded as items so the G x S shape is visible, not assumed.
        _scan_sink.traversed(len(entries) * len(sources))
        relevant_glossary = [
            entry for entry in entries
            if ids.intersection(entry.get("occurrences") or [])
            or any(matcher.matches(entry["source"], source)
                   for source in sources)
        ]
    relevant_locked = [
        entry for entry in relevant_glossary if entry["status"] == "locked"]
    with _critical_path.phase("review_input_memory_project") as _proj_sink:
        _proj_sink.traversed(len(entries))
        memory = translation_core.project_memory_from_entries(
            entries, glossary_hash,
            translation_memory=[],
            confirmed_style_rules=state.get("confirmed_style_rules") or [],
            include_state_translation_memory=False,
            terminology_refs=[{
                "glossary_entry_id": entry["id"],
                "glossary_hash": glossary_hash,
                "status": entry["status"],
                "behavior": entry["behavior"],
            } for entry in relevant_locked],
        )
    memory.pop("audit_history", None)
    return _ReviewGlossaryScope(memory, entries, glossary_hash, relevant_glossary)


def _scoped_project_memory(
    state: Mapping[str, Any], glossary: Sequence[Dict[str, Any]], sources: Sequence[str],
    segment_ids: Sequence[int],
) -> Dict[str, Any]:
    """Review-relevant confirmed knowledge; intentionally no TM or audit history."""
    return _review_glossary_scope(state, glossary, sources, segment_ids).memory


@_critical_path.timed("review_input_packet_build")
def build_runtime_review_packet(
    state: Dict[str, Any],
    current_batch_pairs: Sequence[Dict[str, Any]],
    segment_ids: Sequence[int],
    glossary: Sequence[Dict[str, Any]],
    *,
    deterministic_checks: Any = None,
    review_context: Any = None,
    candidate_targets: Optional[Dict[int, str]] = None,
    blind: bool = False,
) -> Dict[str, Any]:
    """Build a packet from an ephemeral current-batch state projection."""
    ids = list(segment_ids)
    batch = [dict(pair) for pair in current_batch_pairs]
    if len(batch) != len(ids) or len(set(ids)) != len(ids) or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in ids):
        raise ValueError("current batch pairs require matching global segment IDs")
    committed = state.get("pairs") or []
    targets = {int(key): str(value or "")
               for key, value in (candidate_targets or {}).items()}
    overrides: Dict[int, Dict[str, Any]] = {}
    for pair, segment_id in zip(batch, ids):
        target = targets.get(segment_id, str(pair.get("target") or ""))
        if blind:
            pair = {
                "segment_id": segment_id,
                "source": str(pair.get("source") or ""),
                "target": target,
                "reviewed": False,
            }
        else:
            pair["segment_id"] = segment_id
            pair["source"] = str(pair.get("source") or "")
            pair["target"] = target
        overrides[segment_id] = pair
    sources = [str(pair.get("source") or "") for pair in batch]
    context_value = dict(review_context) if isinstance(review_context, Mapping) else {}
    dependency_ids = context_value.pop("_dependency_segment_ids", None)
    target_dependency_ids = context_value.pop("_dependency_target_segment_ids", None)
    dependency_ids = [value for value in (dependency_ids or ids)
                      if isinstance(value, int) and not isinstance(value, bool)]
    target_dependency_ids = {
        value for value in (target_dependency_ids or ids)
        if isinstance(value, int) and not isinstance(value, bool)
    }
    # Which committed indices can this packet possibly read?  ``_translation_truth``
    # validates ``0 <= index < len(pairs)`` and dereferences only ``segment_ids``;
    # the loop below dereferences only the bounded dependency set.  Every other
    # committed pair is unreachable, so copying it was work no consumer could
    # observe -- the Phase 6 anti-pattern ``[dict(pair) for pair in committed]``.
    # The projected image below still reports the same ``len()`` (so the bounds
    # check is unchanged) while materialising only reachable indices.
    committed_length = len(committed)
    if ids:
        committed_length = max(committed_length, max(ids) + 1)
    # The current batch is *not* in ``committed`` yet -- it is being reviewed
    # before commit -- so every ``id`` is reachable regardless of prefix length;
    # only the dependency indices have to fall inside it.
    touched = sorted({
        index for index in dependency_ids
        if isinstance(index, int) and not isinstance(index, bool)
        and 0 <= index < len(committed)
    })
    with _critical_path.phase("review_input_pair_prefix") as _prefix_sink:
        _prefix_sink.traversed(len(touched))
        _prefix_sink.materialized(len(touched) + len(overrides))
        values: Dict[int, Dict[str, Any]] = {}
        for index in touched:
            pair = committed[index]
            values[index] = dict(pair) if isinstance(pair, dict) else {}
        values.update(overrides)
        projected_pairs = _ProjectedPairs(committed_length, values)
    projection = dict(state)
    projection["pairs"] = projected_pairs
    projection["glossary"] = list(glossary)
    projection["glossary_frozen"] = {"entries": list(glossary)}
    with _critical_path.phase("review_input_project_memory") as _memory_sink:
        _memory_sink.traversed(len(glossary))
        scope = _review_glossary_scope(state, glossary, sources, ids)
    dependency_truth = []
    paragraphs = state.get("paras") or []
    for dependency_id in sorted(set(dependency_ids)):
        pair = projected_pairs[dependency_id] if dependency_id < len(projected_pairs) \
            and isinstance(projected_pairs[dependency_id], Mapping) else {}
        uses_pair_source = dependency_id in ids or dependency_id in target_dependency_ids
        source = str(pair.get("source") or (
            paragraphs[dependency_id] if dependency_id < len(paragraphs) else "")) \
            if uses_pair_source else str(
                paragraphs[dependency_id] if dependency_id < len(paragraphs)
                else pair.get("source") or "")
        target_from_accepted = dependency_id not in ids \
            and dependency_id in target_dependency_ids \
            and bool(pair.get("accepted_target"))
        target = (pair.get("accepted_target") or pair.get("target") or "") \
            if target_from_accepted else str(pair.get("target") or "")
        dependency_truth.append({
            "segment_id": dependency_id,
            "source": source,
            "target": target if dependency_id in target_dependency_ids else None,
            "target_checked": dependency_id in target_dependency_ids,
            "target_from_accepted": target_from_accepted,
            "source_from_paragraphs": not uses_pair_source,
        })
    with _critical_path.phase("review_input_review_packet_core") as _core_sink:
        _core_sink.traversed(len(ids))
        packet = translation_core.build_review_packet(
            projection,
            segment_ids=ids,
            deterministic_checks=deterministic_checks or [],
            context=context_value,
            evidence=[],
            project_memory=scope.memory,
            glossary_entries=scope.entries,
            glossary_hash=scope.glossary_hash,
            relevant_glossary=scope.relevant_glossary,
        )
    packet["dependency_segment_ids"] = sorted(set(dependency_ids))
    packet["dependency_target_segment_ids"] = sorted(target_dependency_ids)
    packet["dependency_truth"] = dependency_truth
    return packet


def _bound_evidence(result: Any) -> Any:
    try:
        payload_bytes = len(json.dumps(
            result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return {"error": "证据结果不可序列化"}
    if payload_bytes <= MAX_EVIDENCE_PAYLOAD_BYTES:
        return result
    return {
        "error": "证据负载超过上限",
        "payload_bytes": payload_bytes,
        "max_bytes": MAX_EVIDENCE_PAYLOAD_BYTES,
    }


class TranslationEvidenceIndex:
    """Read-only, bounded evidence surface for a translation reviewer."""

    ALLOWED_TOOLS = {
        "get_segment", "get_neighbors", "find_occurrences", "get_term",
        "get_document_profile", "get_document_synopsis", "get_section_digest",
        "get_translation_history", "get_findings",
    }

    def __init__(
        self,
        paragraphs: Sequence[str],
        pairs: Sequence[Dict[str, Any]],
        glossary: Sequence[Dict[str, Any]],
        document_profile: Optional[Dict[str, Any]] = None,
        document_synopsis: Optional[Dict[str, Any]] = None,
        section_digests: Optional[Sequence[Dict[str, Any]]] = None,
        findings: Optional[Sequence[Dict[str, Any]]] = None,
        blind: bool = False,
        candidate_targets: Optional[Dict[int, str]] = None,
    ) -> None:
        self.paragraphs = list(paragraphs or [])
        self.pairs = list(pairs or [])
        self.glossary = list(glossary or [])
        self.document_profile = document_profile or {}
        self.document_synopsis = document_synopsis or {}
        self.section_digests = list(section_digests or [])
        self.findings = list(findings or [])
        self.blind = bool(blind)
        self.candidate_targets = {
            int(key): str(value or "")
            for key, value in (candidate_targets or {}).items()
        }
        self.requests: List[Dict[str, Any]] = []

    def get_segment(self, segment_id: int) -> Dict[str, Any]:
        try:
            index = int(segment_id)
        except (TypeError, ValueError):
            return {}
        if not 0 <= index < len(self.paragraphs):
            return {}
        pair = self.pairs[index] if index < len(self.pairs) else {}
        if self.blind:
            # Blind review may see the candidate and accepted neighbor context,
            # but never formal/initial targets or their provenance.
            target = self.candidate_targets.get(index, "")
            if not target:
                target = str(pair.get("accepted_target") or "")
            return {
                "segment_id": index,
                "source": self.paragraphs[index],
                "target": target,
            }
        return {
            "segment_id": index,
            "source": self.paragraphs[index],
            "target": pair.get("target", ""),
            "initial_target": pair.get("initial_target", ""),
            "accepted_target": pair.get("accepted_target", ""),
            "target_provenance": pair.get("target_provenance", ""),
            "reviewed": bool(pair.get("reviewed")),
            "from_tm": bool(pair.get("from_tm")),
        }

    def get_neighbors(self, segment_id: int, before: int = 3,
                      after: int = 3) -> List[Dict[str, Any]]:
        try:
            index = int(segment_id)
            before = max(0, min(5, int(before)))
            after = max(0, min(5, int(after)))
        except (TypeError, ValueError):
            return []
        if not 0 <= index < len(self.paragraphs):
            return []
        start = max(0, index - before)
        end = min(len(self.paragraphs), index + after + 1)
        return [self.get_segment(i) for i in range(start, end) if i != index]

    def find_occurrences(self, source_expression: str,
                         selectors: Sequence[str] = ("first", "middle", "last")) -> List[Dict[str, Any]]:
        indices = terminology.find_occurrences(source_expression, self.paragraphs)
        if not indices:
            return []
        chosen = []
        if "first" in selectors:
            chosen.append(indices[0])
        if "middle" in selectors:
            chosen.append(indices[len(indices) // 2])
        if "last" in selectors:
            chosen.append(indices[-1])
        return [self.get_segment(i) for i in dict.fromkeys(chosen)]

    def get_term(self, term_id: Optional[str] = None,
                 source: Optional[str] = None) -> Dict[str, Any]:
        for entry in self.glossary:
            if term_id and entry.get("id") == term_id:
                return dict(entry)
            if source and str(entry.get("source") or "").casefold() == str(source).casefold():
                return dict(entry)
        return {}

    def get_document_profile(self) -> Dict[str, Any]:
        return dict(self.document_profile)

    def get_document_synopsis(self) -> Dict[str, Any]:
        return dict(self.document_synopsis)

    def get_section_digest(self, section_id: Optional[str] = None,
                           segment_id: Optional[int] = None) -> Dict[str, Any]:
        for digest in self.section_digests:
            if not isinstance(digest, dict):
                continue
            if section_id and digest.get("unit_id") == section_id:
                return dict(digest)
            if segment_id is not None:
                try:
                    in_range = (digest.get("start_segment", 0) <= int(segment_id)
                                <= digest.get("end_segment", -1))
                except (TypeError, ValueError):
                    in_range = False
                if in_range:
                    return dict(digest)
        return {}

    def get_translation_history(self, segment_id: int) -> Dict[str, Any]:
        segment = self.get_segment(segment_id)
        if not segment:
            return {}
        if self.blind:
            return {key: segment.get(key) for key in (
                "segment_id", "source", "target")}
        return {key: segment.get(key) for key in (
            "segment_id", "source", "initial_target", "target", "accepted_target",
            "target_provenance", "reviewed", "from_tm")}

    def get_findings(self, segment_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if self.blind:
            return []
        if segment_id is None:
            return [dict(item) for item in self.findings]
        try:
            index = int(segment_id)
        except (TypeError, ValueError):
            return []
        return [dict(item) for item in self.findings
                if item.get("segment_index") == index or item.get("segment_id") == index]

    def request(self, tool: str, **arguments: Any) -> Any:
        """Execute one allow-listed evidence request and record its trace."""
        if tool not in self.ALLOWED_TOOLS:
            raise ValueError(f"不支持的证据工具：{tool}")
        if tool == "get_segment":
            result = self.get_segment(arguments.get("segment_id"))
        elif tool == "get_neighbors":
            result = self.get_neighbors(arguments.get("segment_id"),
                                         arguments.get("before", 3),
                                         arguments.get("after", 3))
        elif tool == "find_occurrences":
            result = self.find_occurrences(
                arguments.get("source_expression") or arguments.get("source") or "",
                arguments.get("selectors") or ("first", "middle", "last"))
        elif tool == "get_term":
            result = self.get_term(arguments.get("term_id"), arguments.get("source"))
        elif tool == "get_document_profile":
            result = self.get_document_profile()
        elif tool == "get_document_synopsis":
            result = self.get_document_synopsis()
        elif tool == "get_section_digest":
            result = self.get_section_digest(arguments.get("section_id"),
                                             arguments.get("segment_id"))
        elif tool == "get_translation_history":
            result = self.get_translation_history(arguments.get("segment_id"))
        else:
            result = self.get_findings(arguments.get("segment_id"))
        result = _bound_evidence(result)
        self.requests.append({"tool": tool, "arguments": dict(arguments), "result": result})
        return result


def _parse_payload(text: Any) -> Optional[Any]:
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.DOTALL)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.DOTALL).strip()
    try:
        return json.loads(candidate)
    except (TypeError, ValueError):
        decoder = json.JSONDecoder()
        for marker in ("[", "{"):
            for match in re.finditer(re.escape(marker), candidate):
                try:
                    value, _ = decoder.raw_decode(candidate[match.start():])
                except (TypeError, ValueError):
                    continue
                return value
    return None


@_critical_path.timed("reviewer_findings_normalize")
def _normalize_findings(
    items: Any,
    segment_ids: Optional[Sequence[int]] = None,
    invalid_segments: Optional[List[Any]] = None,
    invalid_refs: Optional[List[str]] = None,
    valid_evidence_ids: Optional[Sequence[str]] = None,
    invalid_diagnostics: Optional[List[Any]] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        if invalid_segments is not None:
            invalid_segments.append("findings_not_array")
        return []
    valid_ids = set(int(item) for item in segment_ids or [])
    valid_refs = set(str(item) for item in valid_evidence_ids or [])
    out = []
    for item in items:
        if not isinstance(item, dict):
            if invalid_segments is not None:
                invalid_segments.append("malformed_finding")
            continue
        severity = item.get("severity")
        if severity not in ("blocking", "actionable", "informational"):
            if invalid_segments is not None:
                invalid_segments.append("invalid_severity")
            continue
        segment_id = item.get("segment_id")
        # Read old responses only as a one-way compatibility shim: the prompt
        # and persisted records use global IDs, while an old local ordinal is
        # translated through this batch's explicit segment list.
        if segment_id is None and isinstance(item.get("segment_index"), int):
            local_ordinal = item["segment_index"]
            if segment_ids is not None and 0 <= local_ordinal < len(segment_ids):
                segment_id = segment_ids[local_ordinal]
        if isinstance(segment_id, bool) or not isinstance(segment_id, int):
            if invalid_segments is not None:
                invalid_segments.append("missing_segment_id")
            continue
        if segment_ids is not None and segment_id not in valid_ids:
            if invalid_segments is not None:
                invalid_segments.append(segment_id)
            continue
        raw_refs = item.get("evidence_refs") or item.get("evidence_ids") or []
        if not isinstance(raw_refs, list):
            if invalid_refs is not None:
                invalid_refs.append("evidence_refs_not_array")
            raw_refs = []
        evidence_refs = [str(ref) for ref in raw_refs if str(ref).strip()]
        if valid_evidence_ids is not None:
            for ref in evidence_refs:
                if ref not in valid_refs and invalid_refs is not None:
                    invalid_refs.append(ref)
        reason = str(item.get("reason") or item.get("summary") or "审校发现问题")
        record = {
            "segment_id": segment_id,
            "severity": severity,
            "reason": reason,
            "evidence_refs": evidence_refs,
        }
        if item.get("suggested_target"):
            record["suggested_target"] = str(item["suggested_target"])
        for field in ("code", "entry_id", "location_key", "occurrence_key"):
            if item.get(field) is not None:
                record[field] = str(item[field]).strip()
        for field in DIAGNOSTIC_FIELDS:
            value = item.get(field)
            if field == "confidence":
                if isinstance(value, bool):
                    continue
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
                if not 0 <= value <= 1:
                    continue
            elif value is not None:
                value = str(value).strip()
                if not value:
                    continue
            if value is not None:
                record[field] = value
        has_diagnostics = any(field in item for field in DIAGNOSTIC_FIELDS)
        if has_diagnostics:
            record["diagnostic_version"] = 1
            if invalid_diagnostics is not None and any(
                    not str(item.get(field) or "").strip()
                    for field in ("summary", "explanation", "recommendation")):
                invalid_diagnostics.append(segment_id)
        out.append(record)
    return out


def _packet_truth_matches(
    packet: Dict[str, Any], sources: Sequence[str], targets: Sequence[str],
    segment_ids: Sequence[int],
) -> bool:
    truth = packet.get("translation_truth")
    if not isinstance(truth, list) or len(truth) != len(segment_ids):
        return False
    expected = list(zip(segment_ids, sources, targets))
    return all(
        isinstance(item, dict)
        and item.get("segment_id") == segment_id
        and item.get("source") == source
        and item.get("target") == target
        for item, (segment_id, source, target) in zip(truth, expected)
    )


#: Review-view reductions.  Each is an *information* reduction (a piece of the
#: reviewer request that duplicates content carried elsewhere in the same
#: request), never a wording change, and each can be ablated independently:
#:
#: ``document_arc``
#:     The enumerated whole-document narrative.  Measured at 63% 8-gram
#:     containment inside ``summary`` (the same figure that removed it from the
#:     generation prompt in the Translation Efficiency pass) and 100% inside the
#:     synopsis it is sent in.
#: ``synopsis_themes``
#:     The theme index, 51% contained in ``summary``.
#: ``truth_source_target``
#:     ``translation_truth[].source/target``, which the numbered source/target
#:     list in the user prompt already carries verbatim.  Only the identity and
#:     provenance fields survive.
#: ``neighbour_source_dup``
#:     ``previous_source_context`` / ``next_source_context`` entries whose text
#:     is already the ``source`` of a ``previous_target_context`` or
#:     ``adjacent_target_context`` entry.
#: ``glossary_boilerplate``
#:     Per-entry glossary metadata that carries no review constraint: a constant
#:     129-character provenance record, the document-scale occurrence index,
#:     advisory ``domain``/``confidence``, and a third copy of the rendering.
#: ``knowledge_hash_dup``
#:     ``confirmed_knowledge.glossary_hash``, already present as
#:     ``glossary.glossary_hash`` in the same request.
#: ``bounded_document_context``
#:     A total character budget for document-level context, applied in priority
#:     order.  Derived from measurement: this workload's document context is
#:     3,424 characters, so the budget only binds for much longer documents.
REVIEW_VIEW_REDUCTIONS = (
    "document_arc",
    "synopsis_themes",
    "truth_source_target",
    "neighbour_source_dup",
    "glossary_boilerplate",
    "knowledge_hash_dup",
    "bounded_document_context",
    "context_minimal",
)
DEFAULT_REVIEW_VIEW_REDUCTIONS = frozenset(REVIEW_VIEW_REDUCTIONS) - {"context_minimal"}

#: Reduction ids that remove *unique* document-level context rather than
#: duplicated content.  They are never part of the default view: they exist so
#: the packet ablation can measure what they cost in review recall.
REVIEW_VIEW_AGGRESSIVE_REDUCTIONS = frozenset({"context_minimal"})

#: Fields dropped by ``context_minimal``: everything the reviewer does not need
#: to judge the batch in front of it.  The batch source/target, glossary,
#: deterministic checks and instructions all survive.
REVIEW_VIEW_MINIMAL_CONTEXT_FIELDS = (
    "document_profile", "document_synopsis", "section_profile", "section_digest",
    "previous_source_context", "previous_target_context", "next_source_context",
    "adjacent_target_context",
)

#: Glossary fields the reviewer actually needs: the term, the required
#: rendering, the forbidden alternatives, and the scope/behaviour/status
#: constraints.  Everything else is metadata that cannot change the review.
REVIEW_VIEW_GLOSSARY_FIELDS = (
    "id", "source", "forbidden", "behavior", "status", "scope", "note",
)

#: Priority order for the document-context budget: earlier entries are kept
#: whole, later ones are dropped once the budget is exhausted.
REVIEW_VIEW_CONTEXT_BUDGET_CHARS = 4_000
REVIEW_VIEW_SYNOPSIS_PRIORITY = (
    "summary", "translation_notes", "themes", "entities", "terms", "status",
)


def _slim_synopsis(
    synopsis: Any, reductions: frozenset,
) -> Any:
    if not isinstance(synopsis, Mapping):
        return synopsis
    kept = dict(synopsis)
    if "document_arc" in reductions:
        kept.pop("document_arc", None)
    if "synopsis_themes" in reductions:
        kept.pop("themes", None)
    return kept


def _slim_glossary(
    glossary: Any, reductions: frozenset,
) -> Any:
    if "glossary_boilerplate" not in reductions or not isinstance(glossary, Mapping):
        return glossary
    entries = glossary.get("entries")
    if not isinstance(entries, list):
        return glossary
    slim = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            slim.append(entry)
            continue
        item: Dict[str, Any] = {}
        for key in REVIEW_VIEW_GLOSSARY_FIELDS:
            value = entry.get(key)
            if value in (None, "", [], {}):
                continue
            item[key] = list(value) if key == "forbidden" and isinstance(value, list) \
                else value
        # ``target`` and ``preferred`` are normally the same confirmed rendering;
        # send the required one, and the other only when they disagree.
        preferred = entry.get("preferred")
        target = entry.get("target")
        if preferred and target and preferred != target:
            item["target"] = target
            item["preferred"] = preferred
        elif preferred or target:
            item["target"] = preferred or target
        slim.append(item)
    return {**glossary, "entries": slim}


def _slim_confirmed_knowledge(
    knowledge: Any, glossary: Any, reductions: frozenset,
) -> Any:
    if "knowledge_hash_dup" not in reductions or not isinstance(knowledge, Mapping):
        return knowledge
    if not isinstance(glossary, Mapping) or not glossary.get("glossary_hash"):
        return knowledge
    if "glossary_hash" not in knowledge:
        return knowledge
    trimmed = dict(knowledge)
    trimmed.pop("glossary_hash", None)
    return trimmed


def _slim_translation_truth(
    truth: Any, reductions: frozenset,
) -> Any:
    if "truth_source_target" not in reductions or not isinstance(truth, list):
        return truth
    # ``source``/``target`` are rendered verbatim by the numbered list in the
    # user prompt; keeping them here would pay for the same text twice.
    return [
        {key: item.get(key) for key in
         ("segment_id", "reviewed", "target_provenance")}
        if isinstance(item, Mapping) else item
        for item in truth
    ]


def _slim_neighbour_context(
    context: Dict[str, Any], reductions: frozenset,
) -> Dict[str, Any]:
    if "neighbour_source_dup" not in reductions:
        return context
    covered = {
        _normalised_text(item.get("source"))
        for key in ("previous_target_context", "adjacent_target_context")
        if isinstance(context.get(key), list)
        for item in context[key]
        if isinstance(item, Mapping) and str(item.get("source") or "").strip()
    }
    if not covered:
        return context
    for key in ("previous_source_context", "next_source_context"):
        sources = context.get(key)
        if isinstance(sources, list):
            context[key] = [item for item in sources
                            if _normalised_text(item) not in covered]
    return context


def _normalised_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _apply_document_context_budget(context: Dict[str, Any]) -> Dict[str, Any]:
    """Bound the document-level context in the review view.

    The whole point of review is the batch, not the book.  Document-level
    fields are kept in priority order until the budget is spent; anything that
    does not fit is dropped from the *view* (the packet keeps it for audit and
    for the review-input fingerprint).
    """
    budget = REVIEW_VIEW_CONTEXT_BUDGET_CHARS
    used = len(json.dumps({k: v for k, v in context.items()
                           if k in REVIEW_VIEW_SYNOPSIS_PRIORITY},
                          ensure_ascii=False, default=str))
    if used <= budget:
        return context
    synopsis = context.get("document_synopsis")
    if not isinstance(synopsis, Mapping):
        return context
    trimmed: Dict[str, Any] = {}
    remaining = budget
    for key in REVIEW_VIEW_SYNOPSIS_PRIORITY:
        if key not in synopsis:
            continue
        value = synopsis[key]
        size = len(json.dumps(value, ensure_ascii=False, default=str)) + len(key) + 4
        if size > remaining:
            break
        trimmed[key] = value
        remaining -= size
    context = dict(context)
    context["document_synopsis"] = trimmed
    return context


#: Reviewer-view context fields that are identical for every batch of a
#: document (or of a section), in the order they are rendered.  Everything not
#: listed here varies per batch and is rendered after them, so consecutive
#: review requests share one long prefix the provider can serve from its
#: prompt cache.  ``sort_keys=True`` used to interleave the two groups
#: (``context_dedupe_removed_count`` sorted before ``document_synopsis``).
REVIEW_VIEW_STABLE_CONTEXT_ORDER = (
    "target_language", "style_rules", "style_constraints",
    "context_budget_chars", "document_profile", "document_synopsis",
    "section_profile", "section_digest",
)

#: Top-level reviewer-view order: context first (stable fields lead it), then
#: the batch-specific parts.
REVIEW_VIEW_TOP_LEVEL_ORDER = (
    "context", "glossary", "confirmed_knowledge", "deterministic_checks",
    "translation_truth",
)


def _sorted_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _sorted_json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_sorted_json_value(item) for item in value]
    return value


def _ordered_by(mapping: Mapping[str, Any], leading: Sequence[str]) -> Dict[str, Any]:
    ordered = {key: _sorted_json_value(mapping[key]) for key in leading if key in mapping}
    for key in sorted(mapping):
        if key not in ordered:
            ordered[key] = _sorted_json_value(mapping[key])
    return ordered


def _render_reviewer_view(view: Mapping[str, Any]) -> str:
    """Deterministic JSON of the reviewer view, stable content first.

    Only the rendering order changes; the packet, the view and every
    fingerprint are untouched.
    """
    ordered = _ordered_by(view, REVIEW_VIEW_TOP_LEVEL_ORDER)
    context = view.get("context")
    if isinstance(context, Mapping):
        ordered["context"] = _ordered_by(context, REVIEW_VIEW_STABLE_CONTEXT_ORDER)
    return json.dumps(ordered, ensure_ascii=False)


def _reviewer_packet_view(
    packet: Dict[str, Any], *,
    reductions: Optional[frozenset] = None,
) -> Dict[str, Any]:
    """Return only independent-review inputs; audit and rationale stay hidden.

    ``reductions`` selects the measured information de-duplications applied to
    the rendered view.  The packet itself is never modified, so the
    review-input fingerprint and every persisted audit surface are unchanged.
    """
    active = DEFAULT_REVIEW_VIEW_REDUCTIONS if reductions is None else frozenset(reductions)
    memory = packet.get("project_memory") or {}
    context = packet.get("context") or {}
    if isinstance(context, Mapping):
        context = _slim_neighbour_context(dict(context), active)
        context["document_synopsis"] = _slim_synopsis(
            context.get("document_synopsis"), active)
        if "context_minimal" in active:
            context = {key: value for key, value in context.items()
                       if key not in REVIEW_VIEW_MINIMAL_CONTEXT_FIELDS}
        elif "bounded_document_context" in active:
            context = _apply_document_context_budget(context)
    glossary = _slim_glossary(packet.get("glossary") or {}, active)
    return {
        "translation_truth": _slim_translation_truth(
            packet.get("translation_truth") or [], active),
        "glossary": glossary,
        "confirmed_knowledge": _slim_confirmed_knowledge(
            memory.get("knowledge") or {}, glossary, active),
        "deterministic_checks": packet.get("deterministic_checks") or [],
        "context": context,
    }


def _consumed_fingerprint(
    packet: Dict[str, Any], evidence: Sequence[Dict[str, Any]],
) -> str:
    truth = packet.get("translation_truth") or []
    memory = packet.get("project_memory") or {}
    return translation_core.review_input_fingerprint(
        source=[item.get("source") for item in truth],
        target=[item.get("target") for item in truth],
        glossary_hash=str((packet.get("glossary") or {}).get("glossary_hash") or ""),
        confirmed_knowledge=memory.get("knowledge") or {},
        context=packet.get("context") or {},
        deterministic_checks=packet.get("deterministic_checks") or [],
        evidence=[*(packet.get("evidence") or []), *evidence],
    )


def _translation_core_findings(
    findings: Sequence[Dict[str, Any]], input_fingerprint: str,
    sources_by_segment: Optional[Mapping[int, str]] = None,
) -> List[Dict[str, Any]]:
    """Add authoritative identity/freshness while retaining the legacy surface."""
    prepared = []
    group_counts: Dict[Tuple[str, str, str], int] = {}
    for finding in findings:
        item = dict(finding)
        code = str(item.get("code") or item.get("category") or "semantic_review")
        subject = str(item.get("segment_id")
                      if item.get("segment_id") is not None else "")
        key = (code, subject, str(item.get("entry_id") or ""))
        group_counts[key] = group_counts.get(key, 0) + 1
        if not item.get("location_key") and not item.get("occurrence_key"):
            source = str((sources_by_segment or {}).get(item.get("segment_id"), ""))
            span = str(item.get("source_span") or "")
            if source and span and source.count(span) == 1:
                item["location_key"] = f"source-offset:{source.index(span)}"
        prepared.append(item)
    occurrences: Dict[Tuple[str, str, str], int] = {}
    used_locations: Dict[Tuple[str, str, str], set[str]] = {}
    for finding in prepared:
        code = str(finding.get("code") or finding.get("category") or "semantic_review")
        subject = str(finding.get("segment_id")
                      if finding.get("segment_id") is not None else "")
        key = (code, subject, str(finding.get("entry_id") or ""))
        location = str(finding.get("location_key")
                       or finding.get("occurrence_key") or "")
        if location:
            used_locations.setdefault(key, set()).add(location)
    result = []
    for finding in prepared:
        item = dict(finding)
        code = str(item.get("code") or item.get("category") or "semantic_review")
        subject = str(item.get("segment_id") if item.get("segment_id") is not None else "")
        entry_id = str(item.get("entry_id") or "")
        if not item.get("location_key") and not item.get("occurrence_key"):
            key = (code, subject, entry_id)
            if group_counts.get(key, 0) > 1:
                while True:
                    occurrences[key] = occurrences.get(key, 0) + 1
                    occurrence_key = f"occurrence-{occurrences[key]}"
                    if occurrence_key not in used_locations.get(key, set()):
                        break
                item["occurrence_key"] = occurrence_key
                item["identity_stability"] = "provisional"
                used_locations.setdefault(key, set()).add(occurrence_key)
        item.setdefault("identity_stability", "stable")
        item.update({
            "code": code,
            "status": "open",
            "subject_id": subject,
            "requires_human_confirmation": item.get("severity") == "blocking",
        })
        normalized = translation_core.normalize_finding(
            item, input_fingerprint=input_fingerprint)
        result.append({**item, **normalized})
    return result


#: Source text is often split mid-sentence (verse lines, PDF line breaks), and
#: translation reorders content across those boundaries into target-language
#: word order.  Judged one segment at a time, the same move reads as an
#: omission in one segment and an addition in the next.
CROSS_SEGMENT_REVIEW_RULE = (
    "原文可能在句子中间被切成多段，译者会按目标语语序把内容挪到相邻段。"
    "判定漏译或增译前，先把相邻段的原文与译文连起来读（本批内的相邻段，"
    "以及审校包 context.adjacent_target_context 给出的批外相邻段）；"
    "若内容只是移到了相邻段译文中、连读后语义完整，不要报漏译或增译。"
    "adjacent_target_context 里的段落只用于对齐，不在本次审校范围，不要为它们生成 finding。"
    "若句子延续到批外而那一段的译文不可见，句末成分的疑似漏译最多报 informational。"
)


def _call(call_llm: Callable, provider: str, api_key: str, model: str,
          system_prompt: str, user_prompt: str) -> Any:
    try:
        return call_llm(provider, api_key, model, system_prompt, user_prompt,
                        temperature=0.2)
    except TypeError:
        return call_llm(provider, api_key, model, system_prompt, user_prompt)


def review_translation_batch_with_evidence(
    sources: Sequence[str],
    targets: Sequence[str],
    glossary_text: str,
    style_rules: str,
    target_lang: str,
    provider: str,
    api_key: str,
    model: str,
    evidence_index: TranslationEvidenceIndex,
    call_llm: Optional[Callable] = None,
    max_rounds: int = 2,
    blind: bool = False,
    segment_ids: Optional[Sequence[int]] = None,
    review_identity: Optional[Dict[str, str]] = None,
    translation_core_packet: Optional[Dict[str, Any]] = None,
    review_view_reductions: Optional[frozenset] = None,
) -> Tuple[List[Dict[str, Any]], bool, Dict[str, Any]]:
    """Review a batch with a bounded, two-state evidence protocol."""
    if call_llm is None:
        import core
        call_llm = core.call_llm
    segment_ids = list(segment_ids) if segment_ids is not None \
        else list(range(len(sources)))
    if len(targets) != len(sources) or len(segment_ids) != len(sources):
        return [], True, {
            "blind": blind, "rounds": [], "requests": [],
            "review_identity": dict(review_identity or {}),
            "decision": "failed", "error": "sources、targets、segment_ids 长度不一致",
            "completion_receipt": {
                "status": "failed", "reviewed_unit_count": 0,
                "review_identity": dict(review_identity or {}),
            },
        }
    if translation_core_packet is not None and not _packet_truth_matches(
            translation_core_packet, sources, targets, segment_ids):
        return [], True, {
            "blind": blind, "segment_ids": segment_ids, "rounds": [],
            "requests": [], "evidence_ids": [], "decision": "failed",
            "review_identity": dict(review_identity or {}),
            "error": "Translation Core review packet truth mismatch",
            "translation_core": {
                "packet_schema_version": translation_core_packet.get("schema_version"),
                "initial_input_fingerprint": translation_core_packet.get("input_fingerprint"),
                "final_consumed_input_fingerprint": None,
                "translation_truth_fingerprint": translation_core_packet.get(
                    "translation_truth_fingerprint"),
            },
            "completion_receipt": {
                "status": "failed", "reviewed_unit_count": 0,
                "review_identity": dict(review_identity or {}),
            },
        }
    packet_target_lang = None
    if translation_core_packet is not None:
        packet_context = translation_core_packet.get("context") or {}
        packet_target_lang = str(packet_context.get("target_language") or "")
    with _critical_path.phase("reviewer_prompt_assemble") as _asm_sink:
        numbered = "\n".join(
            f"local_ordinal: {i}\nsegment_id: {segment_id}\n原文：{source}\n译文：{target}"
            for i, (segment_id, source, target) in
            enumerate(zip(segment_ids, sources, targets))
        )
        _asm_sink.traversed(len(segment_ids))
        packet_prompt = ""
        identity_prompt = ""
        if translation_core_packet is not None:
            identity_prompt = (
                "若有稳定规则编号、术语或逻辑位置，可带 code、entry_id、"
                "location_key 或 occurrence_key。"
            )
            packet_prompt = "\n【Translation Core 独立审校包】\n" + _render_reviewer_view(
                _reviewer_packet_view(
                    translation_core_packet, reductions=review_view_reductions))
        legacy_prompt_context = "" if translation_core_packet is not None else \
            glossary_text + "\n" + style_rules
        # One statement of each rule.  The previous wording described the
        # finding schema twice (a prose field list and a worked example); every
        # field, constraint and the example survive, the repetition does not.
        system_prompt = (
            "你是一位独立的翻译审校专家，负责审查机器译文。"
            + ("这是盲审：不要提及修复候选或内部流程。" if blind else "")
            + "只报告真实存在的问题，不要为低风险或主观偏好制造 finding；"
            "没有充分证据不要生成 blocking finding。"
            "severity 只允许 blocking、actionable、informational。"
            + CROSS_SEGMENT_REVIEW_RULE +
            "若需全文证据，先在 evidence_requests 请求工具"
            "（{\"tool\": \"get_segment\", \"arguments\": {}}），拿到证据后再作最终判断。"
            "严格输出 JSON 对象 {\"findings\": [...], \"evidence_requests\": [...]}；"
            "finding 使用给出的全局 segment_id（不要用 segment_index），字段为 "
            "category、severity、summary、explanation、recommendation、source_span、"
            "target_span、confidence、detector，可带 evidence_refs 与 suggested_target；"
            "source_span/target_span 必须是原文/译文中的精确连续片段，无法可靠定位时为 null；"
            "confidence 为 0 到 1 的数字或 null。" + identity_prompt +
            "示例：{\"segment_id\": 0, \"category\": \"semantic_accuracy\", "
            "\"severity\": \"actionable\", \"summary\": \"具体问题摘要\", "
            "\"source_span\": null, \"target_span\": null, "
            "\"explanation\": \"为什么判定为问题\", "
            "\"recommendation\": \"建议人工如何处理\", "
            "\"confidence\": 0.87, \"detector\": \"Semantic QA\", "
            "\"evidence_refs\": [\"E1\"]}。"
            "若无问题 findings 必须为空数组。\n" + legacy_prompt_context
            + packet_prompt
        )
        prompt_target_lang = packet_target_lang if packet_target_lang is not None else target_lang
        base_prompt = f"待审校段落（目标语言：{prompt_target_lang}）：\n{numbered}"
        prompt = base_prompt
    trace: Dict[str, Any] = {
        "blind": blind, "segment_ids": segment_ids, "rounds": [],
        "requests": [], "evidence_ids": [], "decision": "",
        "review_identity": dict(review_identity or {}),
        "completion_receipt": None,
    }
    if translation_core_packet is not None:
        trace["translation_core"] = {
            "packet_schema_version": translation_core_packet.get("schema_version"),
            "initial_input_fingerprint": translation_core_packet.get("input_fingerprint"),
            "final_consumed_input_fingerprint": None,
            "translation_truth_fingerprint": translation_core_packet.get(
                "translation_truth_fingerprint"),
            "review_truth": [{
                "segment_id": item.get("segment_id"),
                "source": item.get("source"),
                "target": item.get("target"),
            } for item in translation_core_packet.get("translation_truth") or []],
            "glossary_hash": str(
                (translation_core_packet.get("glossary") or {}).get(
                    "glossary_hash") or ""),
            "dependency_segment_ids": list(
                translation_core_packet.get("dependency_segment_ids") or []),
            "dependency_target_segment_ids": list(
                translation_core_packet.get("dependency_target_segment_ids") or []),
            "dependency_truth": [dict(item) for item in
                                 translation_core_packet.get("dependency_truth") or []],
        }
    latest_findings: List[Dict[str, Any]] = []
    evidence_by_key: Dict[str, Dict[str, Any]] = {}

    def finish(findings: List[Dict[str, Any]], decision: str):
        final_fingerprint = None
        if translation_core_packet is not None:
            final_fingerprint = _consumed_fingerprint(
                translation_core_packet, list(evidence_by_key.values()))
            findings = _translation_core_findings(
                findings, final_fingerprint, dict(zip(segment_ids, sources)))
            trace["translation_core"]["final_consumed_input_fingerprint"] = \
                final_fingerprint
            if trace["rounds"]:
                trace["rounds"][-1]["findings"] = findings
        trace["decision"] = decision
        trace["completion_receipt"] = {
            "status": "completed",
            "reviewed_segment_ids": list(segment_ids),
            "reviewed_unit_count": len(segment_ids),
            "finding_count": len(findings),
            "evidence_ids": list(trace["evidence_ids"]),
            "review_identity": dict(trace["review_identity"]),
        }
        if final_fingerprint is not None:
            trace["completion_receipt"]["final_consumed_input_fingerprint"] = \
                final_fingerprint
        return findings, False, trace

    def fail(message: Optional[str] = None):
        if translation_core_packet is not None:
            final_fingerprint = _consumed_fingerprint(
                translation_core_packet, list(evidence_by_key.values()))
            trace["translation_core"]["final_consumed_input_fingerprint"] = \
                final_fingerprint
        trace["decision"] = "failed"
        if message:
            trace["error"] = message[:240]
        trace["completion_receipt"] = {
            "status": "failed",
            "reviewed_segment_ids": [],
            "reviewed_unit_count": 0,
            "finding_count": 0,
            "evidence_ids": list(trace["evidence_ids"]),
            "review_identity": dict(trace["review_identity"]),
        }
        if translation_core_packet is not None:
            trace["completion_receipt"]["final_consumed_input_fingerprint"] = \
                trace["translation_core"]["final_consumed_input_fingerprint"]
        return [], True, trace

    if packet_target_lang is not None and packet_target_lang != str(target_lang or ""):
        return fail("Translation Core target language mismatch")

    for round_index in range(max(1, int(max_rounds or 1))):
        try:
            # Transport and parse are timed apart on purpose: the whole point of
            # this pass is to answer "provider wait or local CPU", and a single
            # bucket around both cannot.
            with _critical_path.phase("reviewer_transport") as _t_sink:
                raw_response = _call(
                    call_llm, provider, api_key, model, system_prompt, prompt)
                if isinstance(raw_response, str):
                    _t_sink.received(len(raw_response))
            with _critical_path.phase("reviewer_parse"):
                payload = _parse_payload(raw_response)
        except Exception as exc:
            return fail(str(exc))
        if isinstance(payload, list):
            invalid_segments: List[Any] = []
            invalid_refs: List[str] = []
            invalid_diagnostics: List[Any] = []
            latest_findings = _normalize_findings(
                payload, segment_ids, invalid_segments, invalid_refs,
                list(trace["evidence_ids"]), invalid_diagnostics)
            trace["rounds"].append({"round": round_index, "findings": latest_findings,
                                    "requests": []})
            if invalid_segments or invalid_refs or invalid_diagnostics:
                return fail("finding 引用了无效的定位、证据或诊断字段")
            return finish(latest_findings,
                          "clean" if not latest_findings else "findings")
        if not isinstance(payload, dict):
            return fail("审校返回不是 JSON 对象")
        invalid_segments = []
        latest_findings = _normalize_findings(
            payload.get("findings"), segment_ids, invalid_segments)
        if invalid_segments:
            return fail("findings 必须是数组且只能引用当前批次的全局 segment_id")
        if "evidence_requests" in payload:
            raw_requests = payload["evidence_requests"]
        else:
            raw_requests = payload.get("requests", [])
        if not isinstance(raw_requests, list) \
                or any(not isinstance(item, dict) for item in raw_requests):
            return fail("evidence_requests 必须是对象数组")
        requests = raw_requests[:MAX_REQUESTS_PER_ROUND]
        round_trace = {"round": round_index, "findings": latest_findings, "requests": []}
        if round_index > 0 and requests:
            trace["rounds"].append(round_trace)
            return fail("最终审校轮次禁止继续请求证据")
        evidence = []
        for request in requests:
            tool = str(request.get("tool") or request.get("type") or "")
            arguments = request.get("arguments") or request.get("args") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            request_key = json.dumps({"tool": tool, "arguments": arguments},
                                     ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"))
            envelope = evidence_by_key.get(request_key)
            deduped = envelope is not None
            if envelope is None:
                with _critical_path.phase("reviewer_evidence_request"):
                    try:
                        result = evidence_index.request(tool, **arguments)
                    except (TypeError, ValueError) as exc:
                        result = {"error": str(exc)}
                result = _bound_evidence(result)
                evidence_id = f"E{len(evidence_by_key) + 1}"
                envelope = {
                    "evidence_id": evidence_id,
                    "tool": tool,
                    "arguments": arguments,
                    "result": result,
                }
                evidence_by_key[request_key] = envelope
                trace["evidence_ids"].append(evidence_id)
            round_request = {
                "evidence_id": envelope["evidence_id"],
                "tool": tool,
                "arguments": arguments,
                "result": envelope["result"],
            }
            if deduped:
                round_request["deduped"] = True
            round_trace["requests"].append(round_request)
            trace["requests"].append(round_request)
            evidence.append(envelope)
        trace["rounds"].append(round_trace)
        if not evidence:
            invalid_segments = []
            invalid_refs: List[str] = []
            invalid_diagnostics: List[Any] = []
            latest_findings = _normalize_findings(
                payload.get("findings"), segment_ids, invalid_segments, invalid_refs,
                list(trace["evidence_ids"]), invalid_diagnostics)
            if invalid_segments or invalid_refs or invalid_diagnostics:
                return fail("finding 或诊断字段无效")
            return finish(latest_findings,
                          "findings" if latest_findings else "clean")
        prompt = base_prompt + (
            "\n\n【按审校请求返回的证据】\n" +
            json.dumps(evidence, ensure_ascii=False, indent=2) +
            "\n请基于证据作最终判断；本轮不要再请求证据。")
    return fail("证据审校未收到最终判定")
