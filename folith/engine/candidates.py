"""One transactional entry point for every translation candidate.

Split out of ``core.py`` following the existing ``folith.engine`` layout: the
public names are re-exported by ``core`` and whatever still lives in ``core`` is
read late-bound through ``_c`` so tests patching ``core.X`` keep working.

What this module owns
---------------------
* committing generated text as a **candidate** (never as the working text),
* adopting a candidate -- the only path that turns a suggestion into
  CURRENT_TRANSLATION, idempotent and re-validated under the task lock,
* discarding a candidate,
* the read projection the workbench and the CLI print.

What it deliberately does not own
---------------------------------
It does not call a model.  Generation happens outside the state lock (a network
call must never hold a task transaction open); this module only re-reads the
authoritative state afterwards and decides whether the late result is still
valid for the segment it was generated for.

Staleness is decided by the pair itself
---------------------------------------
``folith.translation_candidates.baseline_conflict`` compares the baseline the
candidate was generated from with the pair as it is now.  A candidate generated
for a human-edited, human-accepted or reviewed segment is *not* invalidated by
being generated; it is invalidated only when that segment moved underneath it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from folith import translation_candidates as _cand
from folith.engine._link import core as _c
from folith.engine.locks import _serialized_job_mutation


def _loaded(job_id: str) -> Dict[str, Any]:
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    return state


def _view_with_pair(state: Mapping[str, Any], record: Mapping[str, Any]) -> Dict[str, Any]:
    pairs = state.get("pairs") or []
    index = _cand.resolve_segment_index(state, record)
    pair = pairs[index] if isinstance(index, int) and 0 <= index < len(pairs) else None
    return _cand.public_view(record, pair=pair)


# ================= 读取 =================


def _load_state_safe(job_id: str) -> Optional[Dict[str, Any]]:
    """Read-only state for projections; a missing task returns ``None``."""
    try:
        return _c.load_job_state(job_id)
    except Exception:  # noqa: BLE001 - a projection must not raise on I/O
        return None


def candidate_view(job_id: str, segment_uid: str = "",
                   state: Mapping[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Pending candidates, all of them or just one segment's (newest first)."""
    state = state if state is not None else (_load_state_safe(job_id) or {})
    records = _cand.pending_records(state)
    wanted = str(segment_uid or "")
    if wanted:
        records = [record for record in records
                   if str(record.get("segment_uid") or "") == wanted]
    records = list(reversed(records))
    return [_view_with_pair(state, record) for record in records]


def pending_candidate_count(job_id: str, state: Mapping[str, Any] | None = None) -> int:
    state = state if state is not None else (_load_state_safe(job_id) or {})
    return len(_cand.pending_records(state))


# ================= 生成结果落库 =================


@_serialized_job_mutation
def commit_candidates(job_id: str, drafts: Sequence[Mapping[str, Any]], *,
                      actor: str = "user") -> Dict[str, Any]:
    """Persist generated texts as candidates; the working translation is untouched.

    ``drafts`` carries what the model call produced, together with the baseline
    it was produced from.  Each draft is resolved against the *authoritative*
    state read inside the lock, so a draft whose segment moved, was excluded or
    no longer exists is recorded as stale (or rejected) instead of being written
    over the newer text.  Exactly one snapshot is written for the whole batch.
    """
    state = _loaded(job_id)
    created: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    drafts = [draft for draft in drafts or [] if isinstance(draft, Mapping)]
    if not drafts:
        return {"created": [], "rejected": [], "state": state}

    # Old tasks gain their stable identities on first candidate write, the same
    # way the first structural edit gives them one.
    _c.assign_missing_segment_uids(state, job_id=job_id)
    pairs = state.get("pairs") or []
    truth_version = int((state.get("translation_truth") or {}).get("version") or 0)

    for draft in drafts:
        text = str(draft.get("candidate_target") or "")
        if not text.strip():
            rejected.append({"reason": "empty_candidate",
                             "segment_index": draft.get("segment_index")})
            continue
        index = _resolve_draft_index(state, draft)
        if index is None:
            rejected.append({"reason": _cand.CONFLICT_SEGMENT_MISSING,
                             "segment_index": draft.get("segment_index")})
            continue
        pair = pairs[index]
        uid = str(pair.get(_cand.SEGMENT_UID_FIELD) or "")
        if not uid:
            rejected.append({"reason": _cand.CONFLICT_UID_MISMATCH,
                             "segment_index": index})
            continue
        baseline = draft.get("baseline")
        if not isinstance(baseline, Mapping):
            baseline = _cand.pair_signature(pair)
        record = _cand.make_record(
            candidate_id=_cand.next_candidate_id(state, job_id),
            segment_uid=uid,
            segment_index=index,
            pair=pair,
            baseline=baseline,
            candidate_target=text,
            origin=str(draft.get("origin") or _cand.ORIGIN_TARGETED),
            provider=str(draft.get("provider") or ""),
            model=str(draft.get("model") or ""),
            instruction=str(draft.get("instruction") or ""),
            usage=draft.get("usage"),
            finding_ids=draft.get("finding_ids") or (),
            truth_version=draft.get("truth_version", truth_version),
            checks=draft.get("checks"),
        )
        conflict = _cand.baseline_conflict(record, pair)
        if conflict is not None:
            # A late result never becomes the working text: it stays visible as
            # "已过期" so the user can see that the segment moved, and the
            # reason is recorded verbatim.
            record["status"] = _cand.STATUS_STALE
            record["resolved_at"] = _cand.now_iso()
            record["note"] = _cand.CONFLICT_REASONS.get(conflict, "候选已过期")
            record["conflict"] = conflict
        _cand.add_record(state, record)
        created.append(_cand.public_view(record, pair=pair))

    _c.save_job_state(job_id, state)
    return {"created": created, "rejected": rejected, "state": state}


def _resolve_draft_index(state: Mapping[str, Any], draft: Mapping[str, Any]) -> Optional[int]:
    """Find the segment a draft belongs to: uid first, index+source as fallback."""
    pairs = state.get("pairs") or []
    uid = str(draft.get("segment_uid") or "")
    if uid:
        for index, pair in enumerate(pairs):
            if isinstance(pair, dict) and str(pair.get(_cand.SEGMENT_UID_FIELD) or "") == uid:
                return index
        return None
    index = draft.get("segment_index")
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return None
    pair = pairs[index]
    if not isinstance(pair, dict):
        return None
    if str(pair.get("source") or "") != str(draft.get("source") or ""):
        return None
    return index


# ================= 采用 / 丢弃 =================


def _conflict_reason(code: str, record: Mapping[str, Any]) -> str:
    reason = _cand.CONFLICT_REASONS.get(code, "")
    if code == _cand.CONFLICT_NOT_PENDING:
        reason = (f"这个候选已经是「{_cand.STATUS_LABELS.get(str(record.get('status')), '')}」，"
                  "不能再次采用。")
    return reason


def _adopt_one(state: Dict[str, Any], job_id: str, candidate_id: str, *,
               actor: str, note: str, allow_protected: bool) -> tuple[Dict[str, Any], bool]:
    """Adopt one candidate against ``state``; return ``(result, state_changed)``.

    Callers own the lock and the save.  Splitting this out is what lets a batch
    adopt several candidates in **one** snapshot write instead of one write per
    segment: a crash between two writes would otherwise leave half a batch
    adopted with no record of the intent.
    """
    record = _cand.by_id(state, candidate_id)
    if record is None:
        raise ValueError(f"找不到候选 {candidate_id}")
    view = _view_with_pair(state, record)
    status = str(record.get("status") or "")
    if status == _cand.STATUS_ADOPTED:
        return {"committed": True, "already_adopted": True, "candidate": view,
                "conflict": None, "reason": ""}, False
    if status != _cand.STATUS_PENDING:
        code = _cand.CONFLICT_NOT_PENDING
        return {"committed": False, "already_adopted": False, "candidate": view,
                "conflict": code,
                "reason": _conflict_reason(code, record)}, False

    index = _cand.resolve_segment_index(state, record)
    pairs = state.get("pairs") or []
    pair = pairs[index] if isinstance(index, int) and 0 <= index < len(pairs) else None
    conflict = _cand.baseline_conflict(record, pair)
    if conflict is None:
        if pairs[index].get("excluded"):
            conflict = _cand.CONFLICT_EXCLUDED
        else:
            gate = _c._audit_source_quality_for_state(state)
            blocked = {item.get("segment_index") for item in gate.get("flags") or []
                       if isinstance(item, dict) and not item.get("confirmed")
                       and not item.get("resolved")}
            if index in blocked:
                conflict = _cand.CONFLICT_SOURCE_UNCONFIRMED
    if conflict is not None:
        record["status"] = _cand.STATUS_STALE
        record["resolved_at"] = _cand.now_iso()
        record["conflict"] = conflict
        record["note"] = _cand.CONFLICT_REASONS.get(conflict, "候选已过期")
        return {"committed": False, "already_adopted": False,
                "candidate": _cand.public_view(record, pair=pair),
                "conflict": conflict,
                "reason": _cand.CONFLICT_REASONS.get(conflict, "候选已过期")}, True

    # Protection is checked against the state *now*, not against the baseline:
    # a candidate generated before the human confirmed the text must not
    # overwrite that confirmation through a batch.
    locked = _cand.locked_protection(pair)
    if locked and not allow_protected:
        return {"committed": False, "already_adopted": False,
                "candidate": _cand.public_view(record, pair=pair),
                "conflict": _cand.CONFLICT_PROTECTED_HUMAN,
                "reason": _cand.CONFLICT_REASONS[_cand.CONFLICT_PROTECTED_HUMAN],
                "protection": list(locked)}, False

    candidate_target = str(record.get("candidate_target") or "")
    current_target = str(pair.get("target") or "")
    truth = dict(state.get("translation_truth") or {})
    revision = int(truth.get("version") or 0)
    resolved_at = _cand.now_iso()

    if candidate_target == current_target:
        # Same text: the human made a choice, the working translation did not
        # change.  No text version is invented and no review evidence is
        # revoked; the decision is recorded once, on the candidate.
        record["status"] = _cand.STATUS_ADOPTED
        record["resolved_at"] = resolved_at
        record["resolved_by"] = actor
        record["adopted_revision"] = revision
        record["adopted_truth_version"] = revision
        record["previous_target"] = current_target
        record["note"] = note or "同文采用：记录人工选择，正文没有变化"
        from folith import delivery as _delivery

        _delivery.add_human_action(
            state, f"candidate:{record.get('candidate_id')}", "candidate_adopted_unchanged",
            record["note"], actor)
        return {"committed": True, "already_adopted": False, "changed_text": False,
                "candidate": _cand.public_view(record, pair=pair),
                "conflict": None, "reason": ""}, True

    if locked:
        # Recorded on the candidate, not implied: adopting a suggestion over an
        # accepted translation is a deliberate override, and the decision log
        # has to say so.
        record["overrode_protection"] = list(locked)
        record["overrode_note"] = "采用候选覆盖了" + "、".join(
            _cand.PROTECTION_LABELS.get(mark, mark) for mark in locked)

    pair.pop("_translation_edit_restore", None)
    pair["target"] = candidate_target
    # Provenance says what this text is: model output a human chose.  Calling it
    # a human edit would relabel model text as hand-written work.
    pair["target_provenance"] = "candidate_adopted"
    pair["adopted_candidate_id"] = str(record.get("candidate_id") or "")
    pair["human_edited"] = False
    pair["from_tm"] = False
    # The acceptance and review markers described the text that was just
    # replaced.  Clearing them here is explicit on purpose: the staleness helper
    # below only touches segments that have a *runtime review event*, and an
    # old human acceptance without one would otherwise survive onto new text.
    for key in ("accepted_target", "human_accepted", "accepted_by_human"):
        pair.pop(key, None)
    pair["reviewed"] = False
    pair["review_status"] = "not_reviewed"
    _c._recount_reviewed_segments(state)
    # Provisional observations mined from the text that was just replaced no
    # longer describe the current translation.
    try:
        from folith import knowledge

        state["knowledge_candidates"] = knowledge.discard_candidates_for_segments(
            state.get("knowledge_candidates") or [], [index])
    except Exception:  # noqa: BLE001 - knowledge mining must not fail an adoption
        pass

    # One canonical CURRENT_TRANSLATION mutation: the existing entry point
    # revokes stale review/TM trust, propagates dependency staleness and resets
    # the mutable delivery approval.  Adoption does not invent a second path.
    _c._mark_translation_truth_changed(
        job_id, state, [index],
        "采用翻译候选改变 CURRENT_TRANSLATION；相关案例与学术下游需要重建",
        # The truth-change log names the document-level change; the candidate's
        # own decision entry below names the suggestion that was chosen.  Two
        # subjects, two actions -- never one record pretending to be both.
        actor=actor, action="translation_candidate_adopted")
    _c._recheck_delivery_invariants_for_segments(state, [index], actor=actor)

    new_revision = int((state.get("translation_truth") or {}).get("version") or revision + 1)
    record["status"] = _cand.STATUS_ADOPTED
    record["resolved_at"] = resolved_at
    record["resolved_by"] = actor
    record["adopted_revision"] = new_revision
    record["adopted_truth_version"] = new_revision
    record["previous_target"] = current_target
    record["adopted_segment_index"] = index
    record["note"] = note
    from folith import delivery as _delivery

    _delivery.add_human_action(
        state, f"candidate:{record.get('candidate_id')}", "candidate_adopted",
        note or "采用翻译候选", actor)
    return {"committed": True, "already_adopted": False, "changed_text": True,
            "candidate": _cand.public_view(record, pair=pair),
            "conflict": None, "reason": ""}, True


@_serialized_job_mutation
def adopt_translation_candidate(job_id: str, candidate_id: str, *,
                                actor: str = "user", note: str = "",
                                allow_protected: bool = True) -> Dict[str, Any]:
    """Adopt one candidate as the working translation, or explain why not.

    The whole decision runs under the task's state lock, against freshly loaded
    state: the segment is resolved by uid, the baseline is re-checked, the
    existing source-quality gate is honoured, and the text change, the candidate
    status and the audit record are saved in the same snapshot.

    Adoption is idempotent.  Re-adopting an adopted candidate returns its
    original result and does not write the text again, does not bump the
    translation-truth version and does not append a second change record.

    ``allow_protected`` defaults to true because adopting *this one segment* is
    the user pointing at it; a batch is the case where protection has to be
    honoured first, and it defaults the other way.
    """
    state = _loaded(job_id)
    result, dirty = _adopt_one(state, job_id, candidate_id, actor=actor,
                               note=note, allow_protected=allow_protected)
    if dirty:
        _c.save_job_state(job_id, state)
    result["state"] = state
    return result


@_serialized_job_mutation
def adopt_translation_candidates(job_id: str, candidate_ids: Sequence[str], *,
                                 actor: str = "user", note: str = "",
                                 allow_protected: bool = False) -> Dict[str, Any]:
    """Adopt several candidates in one transaction, reporting every item.

    Policy (documented here because #92 asks for one stated batch semantic):
    results are **per item** -- a stale or protected item is skipped with its
    reason and does not block the others -- while the persisted state is a
    single atomic snapshot.  A caller therefore never sees "the first three
    were adopted" on disk without the rest of the batch: either the whole
    snapshot lands or none of it does, and the returned per-item results always
    describe what actually landed.

    Duplicate ids in one call are adopted once: a double click is not a second
    change.
    """
    state = _loaded(job_id)
    results: List[Dict[str, Any]] = []
    committed: List[str] = []
    skipped: List[Dict[str, Any]] = []
    already: List[str] = []
    seen: set = set()
    changed = 0
    dirty = False

    for raw in candidate_ids or []:
        candidate_id = str(raw or "").strip()
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        if _cand.by_id(state, candidate_id) is None:
            skipped.append({"candidate_id": candidate_id,
                            "conflict": _cand.CONFLICT_SEGMENT_MISSING,
                            "reason": "找不到候选"})
            continue
        result, item_dirty = _adopt_one(state, job_id, candidate_id,
                                        actor=actor, note=note,
                                        allow_protected=allow_protected)
        dirty = dirty or item_dirty
        results.append({"candidate_id": candidate_id,
                        "committed": bool(result.get("committed")),
                        "changed_text": bool(result.get("changed_text")),
                        "conflict": result.get("conflict") or "",
                        "reason": result.get("reason") or "",
                        "candidate": result.get("candidate")})
        if result.get("committed"):
            committed.append(candidate_id)
            if result.get("changed_text"):
                changed += 1
            if result.get("already_adopted"):
                already.append(candidate_id)
        else:
            skipped.append({"candidate_id": candidate_id,
                            "conflict": result.get("conflict") or "",
                            "reason": result.get("reason") or ""})

    if dirty:
        _c.save_job_state(job_id, state)
    return {"committed": committed, "skipped": skipped, "changed": changed,
            "results": results, "state": state, "already_adopted": already}



@_serialized_job_mutation
def discard_translation_candidate(job_id: str, candidate_id: str, *,
                                  actor: str = "user", note: str = "") -> Dict[str, Any]:
    """Close a candidate as discarded.  The working translation is not touched.

    Discarding is not a review decision: the finding the candidate was generated
    for stays exactly as it was, and no text change is recorded.
    """
    state = _loaded(job_id)
    record = _cand.by_id(state, candidate_id)
    if record is None:
        raise ValueError(f"找不到候选 {candidate_id}")
    view = _view_with_pair(state, record)
    status = str(record.get("status") or "")
    if status == _cand.STATUS_DISCARDED:
        return {"discarded": True, "already_discarded": True, "candidate": view,
                "state": state}
    if status != _cand.STATUS_PENDING:
        code = _cand.CONFLICT_NOT_PENDING
        return {"discarded": False, "already_discarded": False, "candidate": view,
                "state": state, "conflict": code,
                "reason": _conflict_reason(code, record)}
    record["status"] = _cand.STATUS_DISCARDED
    record["resolved_at"] = _cand.now_iso()
    record["resolved_by"] = actor
    record["note"] = note or "丢弃候选；当前译文保持不变"
    from folith import delivery as _delivery

    _delivery.add_human_action(
        state, f"candidate:{record.get('candidate_id')}", "candidate_discarded",
        record["note"], actor)
    _c.save_job_state(job_id, state)
    return {"discarded": True, "already_discarded": False,
            "candidate": _cand.public_view(record), "state": state,
            "conflict": None, "reason": ""}


def candidate_status(job_id: str, candidate_id: str,
                     state: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    state = state if state is not None else (_c.load_job_state(job_id) or {})
    record = _cand.by_id(state, candidate_id)
    if record is None:
        raise ValueError(f"找不到候选 {candidate_id}")
    return _view_with_pair(state, record)


__all__ = [
    "adopt_translation_candidate",
    "adopt_translation_candidates",
    "candidate_status",
    "candidate_view",
    "commit_candidates",
    "discard_translation_candidate",
    "pending_candidate_count",
]
