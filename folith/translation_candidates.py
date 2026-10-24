"""Translation candidates: a suggestion that has not been committed yet.

Why this module exists
----------------------
Targeted retranslation used to write ``pairs[i]["target"]`` directly: one model
call replaced a translation a human had already edited, accepted or reviewed,
cleared the acceptance markers and closed the old findings.  A candidate is the
same generated text kept *outside* the working translation until a human
explicitly adopts it, so "generate" and "commit" become two separate acts.

Authority model
---------------
``pairs`` / CURRENT_TRANSLATION stays the only authoritative translation.  A
candidate record is a suggestion plus the baseline it was generated from:

* generating, previewing or discarding a candidate never changes the working
  translation, the review state, or the delivery gate;
* adopting one goes through ``folith.engine.candidates`` -- a single core
  transaction that re-reads the authoritative state under the task lock;
* a candidate is *not* a review decision and *not* a completed review.  Adoption
  invalidates segment review evidence like any other text change.

The baseline is stored as values, not as a hash
-----------------------------------------------
Comparing only the target string cannot tell "unchanged" from "edited and edited
back", and it cannot notice that the source was corrected in the meantime.  The
record therefore keeps the few mutable pair fields it was generated from
(``BASELINE_FIELDS``) and compares them field by field at adoption time.  That is
a comparison of values that already exist in the state -- deliberately *not* a
second fingerprint store next to the existing freshness machinery.
"""
from __future__ import annotations

import copy
import difflib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Candidate records live in the task state so they survive restarts, are
#: covered by the same atomic snapshot write as everything else, and are
#: visible to the journal-compaction contract (a full snapshot includes them).
CANDIDATES_KEY = "translation_candidates"
CANDIDATE_SEQ_KEY = "translation_candidate_seq"

#: Same field name as ``core.SEGMENT_UID_FIELD``; repeated here so this module
#: stays importable without ``core``.
SEGMENT_UID_FIELD = "segment_uid"

STATUS_PENDING = "pending"
STATUS_ADOPTED = "adopted"
STATUS_DISCARDED = "discarded"
STATUS_STALE = "stale"
STATUS_FAILED = "failed"

STATUS_LABELS = {
    STATUS_PENDING: "待采用",
    STATUS_ADOPTED: "已采用",
    STATUS_DISCARDED: "已丢弃",
    STATUS_STALE: "已过期",
    STATUS_FAILED: "生成失败",
}

ORIGIN_TARGETED = "targeted_retranslate"
ORIGIN_INSTRUCTED = "instructed_retranslate"
ORIGIN_REVIEW = "review_suggestion"
ORIGIN_BATCH = "batch_revision"
ORIGIN_CONCEPT = "concept_revision"
ORIGIN_TERM = "term_revision"

ORIGIN_LABELS = {
    ORIGIN_TARGETED: "定点重译",
    ORIGIN_INSTRUCTED: "按要求重译",
    ORIGIN_REVIEW: "审校建议",
    ORIGIN_BATCH: "批量修订",
    ORIGIN_CONCEPT: "概念统一",
    ORIGIN_TERM: "术语修订",
}

#: Why a candidate cannot be adopted.  The values reach the UI, which turns
#: them into a sentence; they are stable enough to assert in tests.
CONFLICT_SEGMENT_MISSING = "segment_missing"
CONFLICT_UID_MISMATCH = "segment_uid_mismatch"
CONFLICT_SOURCE_CHANGED = "source_changed"
CONFLICT_TARGET_CHANGED = "target_changed"
CONFLICT_REVIEW_CHANGED = "review_state_changed"
CONFLICT_EXCLUDED = "segment_excluded"
CONFLICT_SOURCE_UNCONFIRMED = "source_unconfirmed"
CONFLICT_NOT_PENDING = "not_pending"
CONFLICT_PROTECTED_HUMAN = "protected_human_target"

CONFLICT_REASONS = {
    CONFLICT_SEGMENT_MISSING: "这一段已经不在任务里（可能被删除或排除）。",
    CONFLICT_UID_MISMATCH: "候选对应的段落已经不是同一段，已过期。",
    CONFLICT_SOURCE_CHANGED: "原文在生成候选之后被修改，候选已过期。",
    CONFLICT_TARGET_CHANGED: "译文在生成候选之后被修改，候选已过期。",
    CONFLICT_REVIEW_CHANGED: "这一段的审校/接受状态在生成候选之后变了，候选已过期。",
    CONFLICT_EXCLUDED: "这一段已被排除，不会进入译文与交付。",
    CONFLICT_SOURCE_UNCONFIRMED: "这一段的原文尚未核对，不能写入译文。",
    CONFLICT_NOT_PENDING: "候选已经不是待采用状态。",
    CONFLICT_PROTECTED_HUMAN: "这一段是人工改写或人工确认过的译文：批量采用不会覆盖它，"
                               "需要在段落里单独采用。",
}

#: What the current text *is*, as far as the human work on it goes.  A candidate
#: does not become unadoptable because the segment is protected -- the user may
#: still choose it for that one segment -- but a batch must not sweep those
#: segments away, and the workbench has to say what adopting one would undo.
PROTECTION_HUMAN_ACCEPTED = "human_accepted"
PROTECTION_HUMAN_EDIT = "human_edit"
PROTECTION_REVIEWED = "reviewed"

PROTECTION_LABELS = {
    PROTECTION_HUMAN_ACCEPTED: "人工确认过的译文",
    PROTECTION_HUMAN_EDIT: "人工改写的译文",
    PROTECTION_REVIEWED: "已审校的译文",
}

#: Protection levels a batch refuses to overwrite unless the user asks for it.
#: "Reviewed" is not in here: review is reproducible, the two human ones are not.
PROTECTION_LOCKED = (PROTECTION_HUMAN_ACCEPTED, PROTECTION_HUMAN_EDIT)

#: The pair fields a candidate is generated from.  Any change to one of them
#: between generation and adoption makes the candidate stale.  These are the
#: fields the existing write paths already maintain -- no new bookkeeping.
BASELINE_FIELDS = (
    "source",
    "target",
    "reviewed",
    "review_status",
    "target_provenance",
    "from_tm",
    "excluded",
    "human_accepted",
    "accepted_target",
)

#: A long document can collect candidates for months; only the newest few
#: hundred matter for the workbench and the history stays in human_actions.
MAX_RECORDS = 300

_DIFF_LIMIT = 400


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_records(state: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    """Every candidate record, newest last.  Damaged entries are skipped."""
    records = (state or {}).get(CANDIDATES_KEY)
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def records_for_uid(
    state: Mapping[str, Any] | None,
    segment_uid: str,
    *,
    pending_only: bool = False,
) -> List[Dict[str, Any]]:
    uid = str(segment_uid or "")
    if not uid:
        return []
    found = [record for record in candidate_records(state)
             if str(record.get("segment_uid") or "") == uid]
    if pending_only:
        found = [record for record in found
                 if str(record.get("status") or "") == STATUS_PENDING]
    return found


def pending_records(state: Mapping[str, Any] | None) -> List[Dict[str, Any]]:
    return [record for record in candidate_records(state)
            if str(record.get("status") or "") == STATUS_PENDING]


def by_id(state: Mapping[str, Any] | None, candidate_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(candidate_id or "")
    if not wanted:
        return None
    for record in candidate_records(state):
        if str(record.get("candidate_id") or "") == wanted:
            return record
    return None


def pair_signature(pair: Mapping[str, Any] | None) -> Dict[str, Any]:
    """The baseline fields of one pair, normalised for comparison."""
    pair = pair or {}
    signature: Dict[str, Any] = {}
    for field in BASELINE_FIELDS:
        value = pair.get(field)
        if field in ("reviewed", "from_tm", "excluded", "human_accepted"):
            signature[field] = bool(value)
        elif value is None:
            signature[field] = ""
        elif field == "accepted_target":
            signature[field] = str(value)
        else:
            signature[field] = str(value)
    return signature


def _normalised_baseline(record: Mapping[str, Any]) -> Dict[str, Any]:
    baseline = record.get("baseline")
    if not isinstance(baseline, dict):
        return {}
    return {field: baseline.get(field) for field in BASELINE_FIELDS}


def baseline_conflict(record: Mapping[str, Any],
                      pair: Mapping[str, Any] | None) -> Optional[str]:
    """Why ``record`` no longer matches ``pair``; ``None`` when it still does.

    Source, target and the review/acceptance markers are compared separately so
    the reason can name what actually moved.  A source change is the most
    serious: the candidate was produced from text that no longer exists.
    """
    if pair is None:
        return CONFLICT_SEGMENT_MISSING
    stored = _normalised_baseline(record)
    current = pair_signature(pair)
    if str(stored.get("source") or "") != current["source"]:
        return CONFLICT_SOURCE_CHANGED
    if str(stored.get("target") or "") != current["target"]:
        return CONFLICT_TARGET_CHANGED
    for field in BASELINE_FIELDS:
        if field in ("source", "target"):
            continue
        if stored.get(field) != current[field]:
            return CONFLICT_REVIEW_CHANGED
    return None


def make_record(
    *,
    candidate_id: str,
    segment_uid: str,
    segment_index: int,
    pair: Mapping[str, Any] | None = None,
    baseline: Mapping[str, Any] | None = None,
    candidate_target: str,
    origin: str,
    provider: str = "",
    model: str = "",
    instruction: str = "",
    usage: Optional[Mapping[str, Any]] = None,
    finding_ids: Optional[Sequence[str]] = None,
    truth_version: int = 0,
    checks: Optional[Mapping[str, Any]] = None,
    note: str = "",
) -> Dict[str, Any]:
    """Build one candidate record.

    ``baseline`` is the pair signature captured *when the model was called*;
    ``pair`` is only a fallback for callers that generate and commit in one step.
    Passing the captured baseline is what makes a late result detectable.
    """
    recorded = dict(baseline) if isinstance(baseline, Mapping) else pair_signature(pair)
    return {
        "candidate_id": str(candidate_id),
        "segment_uid": str(segment_uid or ""),
        # Display only: reordering, splitting or excluding segments moves the
        # index, and adoption resolves the segment by uid instead.
        "segment_index": int(segment_index),
        "baseline": recorded,
        "candidate_target": str(candidate_target or ""),
        "origin": str(origin or ORIGIN_TARGETED),
        "instruction": str(instruction or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "generated_at": now_iso(),
        # Token counts the provider did not report are recorded as unknown --
        # never as 0, which would read like "this call was free".
        "usage": dict(usage) if isinstance(usage, Mapping) else {"unknown": True},
        "finding_ids": [str(item) for item in finding_ids or []],
        # Integrity check results computed on the candidate.  They describe the
        # suggestion only: nothing here is written into the task's findings.
        "checks": dict(checks) if isinstance(checks, Mapping) else {},
        "baseline_truth_version": int(truth_version or 0),
        "status": STATUS_PENDING,
        "resolved_at": None,
        "resolved_by": "",
        "note": str(note or ""),
        "adopted_revision": None,
        "adopted_truth_version": None,
    }


def add_record(state: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Append one record, superseding older pending candidates of the same segment.

    Two pending candidates for one segment would make "adopt" ambiguous: the
    later generation is the one the user just asked for, so the earlier ones are
    closed as stale instead of silently remaining adoptable.
    """
    records = state.setdefault(CANDIDATES_KEY, [])
    if not isinstance(records, list):
        records = []
        state[CANDIDATES_KEY] = records
    for existing in records:
        if not isinstance(existing, dict):
            continue
        if str(existing.get("status") or "") != STATUS_PENDING:
            continue
        if str(existing.get("segment_uid") or "") != str(record.get("segment_uid") or ""):
            continue
        existing["status"] = STATUS_STALE
        existing["resolved_at"] = now_iso()
        existing["note"] = "同一段新生成了候选，旧候选不再可采用"
    records.append(record)
    _prune(records)
    return record


def _prune(records: List[Dict[str, Any]]) -> None:
    """Drop the oldest resolved records once the list grows past MAX_RECORDS.

    Pending records are never pruned: an unhandled suggestion has to survive a
    restart, which is exactly what "仍然待处理" means.
    """
    if len(records) <= MAX_RECORDS:
        return
    excess = len(records) - MAX_RECORDS
    kept: List[Dict[str, Any]] = []
    for record in records:
        if excess and isinstance(record, dict) \
                and str(record.get("status") or "") != STATUS_PENDING:
            excess -= 1
            continue
        kept.append(record)
    records[:] = kept


def next_candidate_id(state: Dict[str, Any], job_id: str = "") -> str:
    seq = int(state.get(CANDIDATE_SEQ_KEY) or 0) + 1
    state[CANDIDATE_SEQ_KEY] = seq
    prefix = f"cand-{job_id}" if job_id else "cand"
    return f"{prefix}-{seq:05d}"


def resolve_segment_index(state: Mapping[str, Any], record: Mapping[str, Any]) -> Optional[int]:
    """Find the segment a record belongs to, by uid first, index only as fallback.

    A record generated before stable identities existed has no uid; it may use
    its recorded index **only** while that index still holds the same source
    text.  Anything else is a mismatch, never a guess.
    """
    pairs = (state or {}).get("pairs") or []
    uid = str(record.get("segment_uid") or "")
    if uid:
        for index, pair in enumerate(pairs):
            if isinstance(pair, dict) and str(pair.get(SEGMENT_UID_FIELD) or "") == uid:
                return index
        return None
    index = record.get("segment_index")
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return None
    pair = pairs[index]
    if not isinstance(pair, dict):
        return None
    baseline = _normalised_baseline(record)
    if str(pair.get("source") or "") != str(baseline.get("source") or ""):
        return None
    return index


def protection_of(pair: Mapping[str, Any] | None) -> List[str]:
    """What human work the current text carries, in a stable order.

    Read from the fields the existing write paths already maintain -- a
    candidate shows this so "adopt" cannot quietly undo an acceptance the user
    made earlier, and a batch can refuse to sweep those segments away.
    """
    if not isinstance(pair, Mapping):
        return []
    marks: List[str] = []
    if pair.get("human_accepted") or pair.get("accepted_target") \
            or pair.get("accepted_by_human"):
        marks.append(PROTECTION_HUMAN_ACCEPTED)
    if pair.get("human_edited") or str(pair.get("target_provenance") or "") == "human_edit":
        marks.append(PROTECTION_HUMAN_EDIT)
    if pair.get("reviewed") or str(pair.get("review_status") or "") == "reviewed":
        marks.append(PROTECTION_REVIEWED)
    return marks


def locked_protection(pair: Mapping[str, Any] | None) -> List[str]:
    """The protection levels a batch must not overwrite without an explicit ask."""
    return [mark for mark in protection_of(pair) if mark in PROTECTION_LOCKED]


def preview_diff(before: str, after: str, *, limit: int = _DIFF_LIMIT) -> Dict[str, Any]:
    """Character-level comparison of the current text and the candidate.

    Returned as ordered ``{"tag", "text"}`` chunks so the workbench can paint
    added/removed ranges without re-running a diff.  ``changed`` is false when
    the texts differ only in leading/trailing whitespace, because adoption then
    produces the same working translation.
    """
    before = str(before or "")
    after = str(after or "")
    if before == after:
        return {"changed": False, "identical": True, "chunks": [], "ratio": 1.0}
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    chunks: List[Dict[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            chunks.append({"tag": "equal", "text": before[i1:i2]})
            continue
        # A "replace" opcode is painted as its two halves so the workbench has
        # one vocabulary for every change: removed text, then added text.
        if i1 != i2:
            chunks.append({"tag": "delete", "text": before[i1:i2]})
        if j1 != j2:
            chunks.append({"tag": "insert", "text": after[j1:j2]})
        if len(chunks) >= limit:
            chunks.append({"tag": "truncated", "text": ""})
            break
    return {
        "changed": before.strip() != after.strip(),
        "identical": False,
        "chunks": chunks,
        "ratio": round(matcher.ratio(), 4),
    }


def public_view(record: Mapping[str, Any], *, pair: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Adapter-facing projection: what the workbench needs, without internals."""
    record = record or {}
    baseline = _normalised_baseline(record)
    current_target = str((pair or {}).get("target") or baseline.get("target") or "")
    status = str(record.get("status") or STATUS_PENDING)
    conflict = baseline_conflict(record, pair) if status == STATUS_PENDING else None
    marks = protection_of(pair)
    return {
        "candidate_id": str(record.get("candidate_id") or ""),
        "segment_uid": str(record.get("segment_uid") or ""),
        "segment_index": record.get("segment_index"),
        "source": str(baseline.get("source") or ""),
        "baseline_target": str(baseline.get("target") or ""),
        "candidate_target": str(record.get("candidate_target") or ""),
        "current_target": current_target,
        "origin": str(record.get("origin") or ""),
        "origin_label": ORIGIN_LABELS.get(str(record.get("origin") or ""), ""),
        "instruction": str(record.get("instruction") or ""),
        "provider": str(record.get("provider") or ""),
        "model": str(record.get("model") or ""),
        "generated_at": str(record.get("generated_at") or ""),
        "usage": dict(record.get("usage") or {}),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "adoptable": status == STATUS_PENDING and conflict is None,
        "conflict": conflict,
        "conflict_reason": CONFLICT_REASONS.get(conflict or "", ""),
        # Protection is not a conflict: the candidate can still be adopted for
        # this one segment.  It tells the UI what that adoption would undo.
        "protection": marks,
        "protection_labels": [PROTECTION_LABELS.get(mark, mark) for mark in marks],
        "protected": any(mark in PROTECTION_LOCKED for mark in marks),
        "checks": dict(record.get("checks") or {}),
        # Which finding the suggestion answers, so "adopt" and "resolve" can be
        # connected later without guessing from the segment index.
        "finding_ids": [str(value) for value in record.get("finding_ids") or []],
        "diff": preview_diff(current_target, str(record.get("candidate_target") or "")),
        "resolved_at": record.get("resolved_at"),
        "note": str(record.get("note") or ""),
        "adopted_revision": record.get("adopted_revision"),
    }


def view_for_segment(state: Mapping[str, Any], segment_uid: str,
                     *, pending_only: bool = True) -> List[Dict[str, Any]]:
    pairs = (state or {}).get("pairs") or []
    index = next((position for position, pair in enumerate(pairs)
                  if isinstance(pair, dict)
                  and str(pair.get(SEGMENT_UID_FIELD) or "") == str(segment_uid)),
                 None)
    pair = pairs[index] if index is not None else None
    records = records_for_uid(state, segment_uid, pending_only=pending_only)
    return [public_view(record, pair=pair) for record in records]


def usage_from_response(response: Any) -> Dict[str, Any]:
    """Normalise a provider usage block; an unreported count stays ``unknown``."""
    usage: Dict[str, Any] = {}
    if isinstance(response, Mapping):
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = response.get(key)
            if isinstance(value, (int, float)):
                usage[key] = int(value)
    if not usage:
        return {"unknown": True}
    return usage


def merge_usage(parts: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sum the usage of one candidate's model calls (generate + repair)."""
    total: Dict[str, int] = {}
    unknown = False
    for part in parts:
        if not isinstance(part, Mapping) or part.get("unknown") or not part:
            unknown = True
            continue
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = part.get(key)
            if isinstance(value, (int, float)):
                total[key] = total.get(key, 0) + int(value)
    if not total:
        return {"unknown": True}
    if unknown:
        total["partial"] = True
    return total


def copy_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(dict(record))


__all__ = [
    "BASELINE_FIELDS",
    "CANDIDATES_KEY",
    "CANDIDATE_SEQ_KEY",
    "CONFLICT_EXCLUDED",
    "CONFLICT_NOT_PENDING",
    "CONFLICT_PROTECTED_HUMAN",
    "CONFLICT_REASONS",
    "CONFLICT_REVIEW_CHANGED",
    "CONFLICT_SEGMENT_MISSING",
    "CONFLICT_SOURCE_CHANGED",
    "CONFLICT_SOURCE_UNCONFIRMED",
    "CONFLICT_TARGET_CHANGED",
    "CONFLICT_UID_MISMATCH",
    "ORIGIN_BATCH",
    "ORIGIN_INSTRUCTED",
    "ORIGIN_LABELS",
    "ORIGIN_REVIEW",
    "ORIGIN_TARGETED",
    "PROTECTION_HUMAN_ACCEPTED",
    "PROTECTION_HUMAN_EDIT",
    "PROTECTION_LABELS",
    "PROTECTION_LOCKED",
    "PROTECTION_REVIEWED",
    "STATUS_ADOPTED",
    "STATUS_DISCARDED",
    "STATUS_FAILED",
    "STATUS_LABELS",
    "STATUS_PENDING",
    "STATUS_STALE",
    "add_record",
    "baseline_conflict",
    "by_id",
    "candidate_records",
    "copy_record",
    "locked_protection",
    "make_record",
    "merge_usage",
    "next_candidate_id",
    "now_iso",
    "pair_signature",
    "pending_records",
    "preview_diff",
    "protection_of",
    "public_view",
    "records_for_uid",
    "resolve_segment_index",
    "usage_from_response",
    "view_for_segment",
]
