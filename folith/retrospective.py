"""Retrospective consistency suggestions from terminology evidence (issue #99).

What this layer is
------------------
While a document is translated, deterministic terminology checks already notice
that one locked term is rendered in more than one way.  Today that becomes an
actionable finding on the *current* segments and is then forgotten.  This module
keeps those observations as **evidence** and, from evidence plus the already
committed translations, prepares **suggestions** for earlier segments.

It is deliberately a suggestion layer, not a second truth:

* it never writes ``state["pairs"]``, review state, findings or the delivery
  gate; producing or dismissing a suggestion changes only this module's own
  records;
* a suggestion is materialised through the existing candidate transaction
  (``folith.engine.candidates.commit_candidates``) and adopted only through the
  explicit candidate adoption path, so worker locks, baseline checks, review
  invalidation and the translation-truth version are the existing ones;
* it calls no model.  The only proposed text it can build is a deterministic
  term substitution (a *forbidden* rendering replaced by the locked preferred
  rendering); anything that needs judgement stays evidence-only.

Guards (from the issue's acceptance list):

* human-confirmed / human-edited targets are protected -- no suggestion is made
  for them, and a batch materialisation can never overwrite them;
* titles, very short segments, dialogue/literary quotations and explicitly
  registered exceptions are skipped, because under-specified context is exactly
  where one "unified" rendering can destroy a deliberate choice;
* the whole layer can be switched off (``FOLIOTHREAD_RETROSPECTIVE_SUGGESTIONS=0``
  or ``state["retrospective_suggestions_enabled"] = False``).  Switching it off
  keeps the collected evidence and any already adopted text untouched.

State keys are additive.  Old tasks load unchanged and simply have no evidence.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from folith import translation_candidates as _cand
from folith.terminology import _scope_applies, term_matches

EVIDENCE_KEY = "retrospective_evidence"
SUGGESTIONS_KEY = "retrospective_suggestions"
ENV_SWITCH = "FOLIOTHREAD_RETROSPECTIVE_SUGGESTIONS"
MAX_EVIDENCE = 400
MAX_SUGGESTIONS = 200

STATUS_PENDING = "pending"
STATUS_CANDIDATE = "candidate"
STATUS_DISMISSED = "dismissed"
STATUS_STALE = "stale"

#: Why a segment was left alone.  Stable tokens, asserted by tests and logged.
SKIP_HUMAN_TARGET = "human_target"
SKIP_SHORT_SEGMENT = "short_segment"
SKIP_LITERARY = "literary_dialogue"
SKIP_EXCEPTION = "registered_exception"
SKIP_NO_REWRITE = "no_deterministic_rewrite"

SKIP_LABELS = {
    SKIP_HUMAN_TARGET: "人工确认或改写的译文，保留人工结果",
    SKIP_SHORT_SEGMENT: "标题或极短段落，语境可能与后文不同",
    SKIP_LITERARY: "含对话或文学引文，可能是有意保留的译法",
    SKIP_EXCEPTION: "已登记为回溯例外",
    SKIP_NO_REWRITE: "无法在不猜测的前提下安全改写",
}

#: A target containment replacement is only considered when the observed
#: rendering is at least this long; one-character hits inside other words are
#: exactly the kind of "fix" that corrupts a translation.
_MIN_OBSERVED_LEN = 2

_SHORT_CHARS = 24
_SHORT_WORDS = 3
_DIALOGUE_RE = re.compile(r"[「」『』“”\"'‘’]|(?:^|\s)[-—–]\s")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(*parts: Any, size: int = 12) -> str:
    payload = "\u241f".join(str(part or "") for part in parts)
    return hashlib.blake2s(payload.encode("utf-8"), digest_size=size).hexdigest()


def enabled(state: Optional[Mapping[str, Any]] = None) -> bool:
    """The single rollback switch; defaults to on for translation tasks."""
    raw = os.environ.get(ENV_SWITCH)
    if raw is not None and str(raw).strip().lower() in {"0", "false", "off", "no"}:
        return False
    state = state or {}
    if state.get("retrospective_suggestions_enabled") is False:
        return False
    return True


def normalize_exceptions(raw: Any) -> List[Dict[str, Any]]:
    """Accept ``[3, "7"]`` or ``[{"segment_index": 3, "kind": "title"}]``."""
    result: List[Dict[str, Any]] = []
    for item in raw or []:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            result.append({"segment_index": item, "kind": "exception", "reason": ""})
            continue
        if isinstance(item, Mapping):
            index = item.get("segment_index")
            if isinstance(index, int) and not isinstance(index, bool):
                result.append({
                    "segment_index": index,
                    "kind": str(item.get("kind") or "exception"),
                    "reason": str(item.get("reason") or ""),
                })
    return result


def _exceptions(state: Mapping[str, Any]) -> Dict[int, Dict[str, Any]]:
    return {item["segment_index"]: item
            for item in normalize_exceptions(state.get("retrospective_exceptions"))}


def ambiguity_reason(pair: Mapping[str, Any], *,
                     exceptions: Optional[Mapping[int, Mapping[str, Any]]] = None,
                     segment_index: Optional[int] = None) -> str:
    """Why this segment must not be "unified" automatically; ``""`` when safe."""
    pair = pair if isinstance(pair, Mapping) else {}
    if pair.get("human_accepted") or bool(pair.get("human_edited")):
        return SKIP_HUMAN_TARGET
    provenance = str(pair.get("target_provenance") or "")
    if provenance in {"human_accepted", "human_edit"} \
            or str(pair.get("review_status") or "") == "reviewed_human":
        return SKIP_HUMAN_TARGET
    if isinstance(segment_index, int) and exceptions \
            and segment_index in exceptions:
        return SKIP_EXCEPTION
    source = str(pair.get("source") or "").strip()
    words = [word for word in re.split(r"\s+", source) if word]
    if len(source) <= _SHORT_CHARS or len(words) <= _SHORT_WORDS:
        return SKIP_SHORT_SEGMENT
    if _DIALOGUE_RE.search(source):
        return SKIP_LITERARY
    return ""


def _glossary_index(glossary: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for entry in glossary or []:
        if not isinstance(entry, Mapping):
            continue
        entry_id = str(entry.get("id") or entry.get("entry_id") or "")
        if not entry_id:
            continue
        if str(entry.get("behavior") or "translate") != "translate":
            continue
        if str(entry.get("status") or "") != "locked":
            continue
        preferred = str(entry.get("preferred") or entry.get("target") or "")
        if not preferred:
            continue
        index[entry_id] = {
            "entry_id": entry_id,
            "term": str(entry.get("source") or ""),
            "preferred": preferred,
            "scope": str(entry.get("scope") or ""),
            "forbidden": [str(item) for item in entry.get("forbidden") or []
                          if str(item or "").strip()],
        }
    return index


def _evidence_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records = state.get(EVIDENCE_KEY)
    return records if isinstance(records, list) else []


def _suggestion_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records = state.get(SUGGESTIONS_KEY)
    return records if isinstance(records, list) else []


def _prune(records: List[Dict[str, Any]], limit: int,
           keep_statuses: Iterable[str] = ()) -> None:
    keep = set(keep_statuses)
    removable = [record for record in records
                 if isinstance(record, dict)
                 and str(record.get("status") or "") not in keep]
    overflow = len(records) - limit
    if overflow <= 0 or not removable:
        return
    drop = {id(record) for record in removable[:overflow]}
    records[:] = [record for record in records if id(record) not in drop]


def observe_batch(
    state: Dict[str, Any],
    *,
    findings: Sequence[Mapping[str, Any]] = (),
    pairs: Sequence[Mapping[str, Any]] = (),
    glossary: Sequence[Mapping[str, Any]] = (),
    batch: int = 0,
    sections: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Turn this batch's terminology conflicts into evidence and suggestions.

    Pure local work over data the pipeline already computed.  Idempotent: the
    same batch replayed (retry, journal replay, resume) adds no duplicate.
    """
    started = time.perf_counter()
    counts = {"evidence_added": 0, "suggestions_added": 0, "skipped": 0,
              "enabled": enabled(state)}
    if not counts["enabled"] or not findings:
        counts["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return counts
    entries = _glossary_index(glossary)
    evidence = _evidence_records(state)
    if not isinstance(state.get(EVIDENCE_KEY), list):
        state[EVIDENCE_KEY] = evidence
    existing_ids = {str(item.get("evidence_id") or "") for item in evidence
                    if isinstance(item, dict)}
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        if finding.get("type") != "glossary" or not finding.get("conflict"):
            continue
        entry = entries.get(str(finding.get("entry_id") or ""))
        segment_index = finding.get("segment_id", finding.get("segment_index"))
        if entry is None or not isinstance(segment_index, int) \
                or isinstance(segment_index, bool):
            continue
        observed = str(finding.get("target_span") or "")
        if not observed or observed == entry["preferred"]:
            continue
        evidence_id = "re-" + _digest(entry["entry_id"], segment_index, observed,
                                      entry["preferred"], batch)
        if evidence_id in existing_ids:
            continue
        evidence.append({
            "evidence_id": evidence_id,
            "entry_id": entry["entry_id"],
            "term": entry["term"],
            "preferred_target": entry["preferred"],
            "observed_target": observed,
            # Only a *forbidden* rendering can be replaced deterministically.
            # "some other rendering" is evidence-only: the correct text there
            # depends on the sentence, not on a string substitution.
            "rewritable": observed in entry["forbidden"],
            "segment_index": segment_index,
            "batch": int(batch),
            "recorded_at": _now_iso(),
            "reason": str(finding.get("reason") or finding.get("summary") or ""),
        })
        existing_ids.add(evidence_id)
        counts["evidence_added"] += 1
    _prune(evidence, MAX_EVIDENCE)
    if sections is None:
        sections = (state.get("document_profile") or {}).get("sections") or []
    added, skipped = _derive_suggestions(state, pairs, evidence, entries, sections)
    counts["suggestions_added"] = added
    counts["skipped"] = skipped
    counts["duration_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return counts


def _derive_suggestions(
    state: Dict[str, Any],
    pairs: Sequence[Mapping[str, Any]],
    evidence: List[Dict[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> tuple:
    """Evidence → suggestions, *including segments earlier than the evidence*.

    Evidence names the segment where a conflict surfaced, but the forbidden
    rendering it pins down may occur in segments committed by earlier batches
    that never raised a conflict of their own.  #99's acceptance is exactly
    that later evidence points back at those earlier segments, so rewritable
    evidence is grouped by ``(entry, observed, preferred)`` — the deterministic
    rewrite is identical for every occurrence — and each group scans the whole
    committed list.  The evidence's own segment is found by the same scan; its
    suggestion id (digest of entry, index, preferred, observed) is unchanged,
    so records persisted by the previous single-segment derivation stay valid.
    """
    suggestions = _suggestion_records(state)
    if not isinstance(state.get(SUGGESTIONS_KEY), list):
        state[SUGGESTIONS_KEY] = suggestions
    existing = {str(item.get("suggestion_id") or "") for item in suggestions
                if isinstance(item, dict)}
    exceptions = _exceptions(state)
    added = 0
    skipped = 0
    groups: Dict[tuple, Dict[str, Any]] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        # Not a known forbidden rendering: evidence-only for every segment,
        # because inventing the right text would need judgement (a model).
        observed = str(item.get("observed_target") or "")
        preferred = str(item.get("preferred_target") or "")
        entry = entries.get(str(item.get("entry_id") or ""))
        if not item.get("rewritable") or len(observed) < _MIN_OBSERVED_LEN \
                or not preferred or entry is None \
                or entry["preferred"] != preferred or observed not in entry["forbidden"] \
                or entry["term"] != str(item.get("term") or ""):
            skipped += 1
            continue
        key = (str(item.get("entry_id") or ""), observed, preferred)
        group = groups.get(key)
        evidence_id = str(item.get("evidence_id") or "")
        if group is None:
            groups[key] = {
                "entry_id": str(item.get("entry_id") or ""),
                "term": entry["term"],
                "scope": entry["scope"],
                "preferred_target": preferred,
                "observed_target": observed,
                "evidence_ids": [evidence_id] if evidence_id else [],
            }
        elif evidence_id and evidence_id not in group["evidence_ids"]:
            group["evidence_ids"].append(evidence_id)
    for group in groups.values():
        observed = group["observed_target"]
        preferred = group["preferred_target"]
        term = group["term"]
        # Match the source as well as the rendering: a flower called violets
        # must not become a candidate for the character Violet. Reuse the
        # terminology detector's boundaries and the glossary's current scope.
        for segment_index, pair in enumerate(pairs):
            if not isinstance(pair, Mapping):
                continue
            target = str(pair.get("target") or "")
            if not target or preferred in target or observed not in target:
                continue
            if not term_matches(term, str(pair.get("source") or "")):
                continue
            section = None
            if group["scope"].startswith("section:"):
                section = next((section for section in sections
                                if section.get("start_segment") is not None
                                and section.get("end_segment") is not None
                                and section["start_segment"] <= segment_index
                                <= section["end_segment"]), None)
            if not _scope_applies(group, segment_index, section):
                continue
            suggestion_id = "rs-" + _digest(group["entry_id"], segment_index,
                                            preferred, observed)
            if suggestion_id in existing:
                continue
            reason = ambiguity_reason(pair, exceptions=exceptions,
                                      segment_index=segment_index)
            if reason:
                skipped += 1
                continue
            proposed = target.replace(observed, preferred, 1)
            if not proposed.strip() or proposed == target:
                skipped += 1
                continue
            suggestions.append({
                "suggestion_id": suggestion_id,
                "segment_index": segment_index,
                "entry_id": group["entry_id"],
                "term": group["term"],
                "preferred_target": preferred,
                "observed_target": observed,
                "baseline_target": target,
                "proposed_target": proposed,
                "evidence_ids": list(group["evidence_ids"]),
                "status": STATUS_PENDING,
                "origin": _cand.ORIGIN_RETROSPECTIVE,
                "reason": "later_terminology_evidence",
                "reason_label": (f"后出现的术语证据：术语「{group['term']}」"
                                 f"按锁定译法「{preferred}」统一"),
                "created_at": _now_iso(),
                "resolved_at": None,
                "resolved_by": "",
                "candidate_id": "",
            })
            existing.add(suggestion_id)
            added += 1
    _prune(suggestions, MAX_SUGGESTIONS,
           keep_statuses=(STATUS_PENDING, STATUS_CANDIDATE))
    return added, skipped


def pending_suggestions(state: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [record for record in _suggestion_records(state or {})
            if isinstance(record, dict)
            and str(record.get("status") or "") == STATUS_PENDING]


def suggestion_view(state: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Read-only projection for the task card / CLI."""
    pairs = (state or {}).get("pairs") or []
    view = []
    for record in pending_suggestions(state):
        index = record.get("segment_index")
        pair = pairs[index] if isinstance(index, int) and 0 <= index < len(pairs) else {}
        view.append({
            "suggestion_id": record.get("suggestion_id"),
            "segment_index": index,
            "source": str(pair.get("source") or ""),
            "current_target": str(pair.get("target") or ""),
            "proposed_target": record.get("proposed_target"),
            "term": record.get("term"),
            "preferred_target": record.get("preferred_target"),
            "observed_target": record.get("observed_target"),
            "reason": record.get("reason"),
            "reason_label": record.get("reason_label"),
            "status": record.get("status"),
        })
    return view


def find_suggestion(state: Mapping[str, Any],
                    suggestion_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(suggestion_id or "")
    for record in _suggestion_records(state):
        if isinstance(record, dict) and str(record.get("suggestion_id") or "") == wanted:
            return record
    return None


def build_candidate_draft(state: Mapping[str, Any],
                          record: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the candidate draft for a suggestion; ``None`` when it went stale.

    The baseline is the pair signature *now*, so the candidate transaction
    re-validates it again under the task lock: a segment edited after this call
    is reported as stale instead of being overwritten.
    """
    pairs = state.get("pairs") or []
    index = record.get("segment_index")
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return None
    pair = pairs[index]
    if not isinstance(pair, Mapping):
        return None
    if str(pair.get("target") or "") != str(record.get("baseline_target") or ""):
        return None
    if ambiguity_reason(pair, exceptions=_exceptions(state),
                        segment_index=index):
        return None
    proposed = str(record.get("proposed_target") or "")
    if not proposed or proposed == str(pair.get("target") or ""):
        return None
    return {
        "segment_index": index,
        # The candidate transaction resolves a draft by uid, then by
        # index + source.  Both are supplied so an old task whose uids are
        # assigned on first candidate write still resolves correctly.
        "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
        "source": str(pair.get("source") or ""),
        "candidate_target": proposed,
        "origin": _cand.ORIGIN_RETROSPECTIVE,
        "instruction": str(record.get("reason_label") or ""),
        "baseline": _cand.pair_signature(pair),
        "note": "回溯一致性建议；采用前请确认语境",
        "finding_ids": [],
    }
