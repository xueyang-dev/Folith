"""Stage and commit deterministic auto-resolutions for QA triage (issue #288).

Two strictly separated transactions, mirroring the candidate transaction:

* :func:`stage_auto_resolution` stages a candidate from
  ``finding_groups.build_number_fix_draft`` through
  ``core.materialize_deterministic_group_fix`` and writes a triage record with
  ``disposition="auto_resolvable"`` and ``committed=False``.  **No text is
  written.**
* :func:`commit_auto_resolution` dry-runs the candidate text first, adopts that
  single candidate under five short-circuiting conditions, re-runs the detector
  on the committed segment and only then writes ``committed=True`` with
  ``candidate_verification`` evidence.  A failed precheck never writes the
  candidate text; a failed post-check restores the pre-adoption text and pair
  fields in the same transaction.

A failure never removes the finding, never writes ``resolved``/``dismissed`` and
never writes a ``HumanDecision``; it leaves the candidate for a human and records
where the attempt stopped.

Both entry points run under ``_serialized_job_mutation``.  The delivery gate is
not touched: disposition is explanation, never permission.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional

from folith import delivery as _delivery
from folith import finding_groups as _finding_groups
from folith import finding_triage as _triage
from folith import translation_candidates as _candidates_store
from folith.engine import candidates as _candidates
from folith.engine._link import core as _c
from folith.engine.locks import _serialized_job_mutation

#: Non-human audit marker for an automatic adoption.  Never "human", never
#: "human_decision"; a reader can tell it apart from a real human decision.
AUTO_RESOLUTION_RECORD_TYPE = "qa_triage_auto_resolution"

_NUMBER_KIND = "number"
_STRUCTURAL_REPAIR = "structural_repair"
_CANDIDATE_VERIFICATION = "candidate_verification"


# ---------------------------------------------------------------------------
# identity and small readers
# ---------------------------------------------------------------------------

def triage_key(finding: Mapping[str, Any]) -> str:
    """Stable triage key for a finding, with or without a core ``finding_id``.

    Independent-review findings carry ``finding_id``; deterministic findings do
    not, so they fall back to ``delivery.finding_id`` (the same stable render id
    the queue already derives).  A reader that wants ``disposition_of`` for a
    deterministic finding uses this key via :func:`keyed_finding`.
    """
    finding = finding or {}
    explicit = str(finding.get("finding_id") or "").strip()
    if explicit:
        return explicit
    return str(_delivery.finding_id(dict(finding)))


def keyed_finding(finding: Mapping[str, Any]) -> Dict[str, Any]:
    """Copy of ``finding`` with ``finding_id`` set to its triage key."""
    item = dict(finding or {})
    item["finding_id"] = triage_key(item)
    return item


def _find_finding(state: Mapping[str, Any], key: str) -> Optional[Dict[str, Any]]:
    for finding in state.get("findings") or []:
        if isinstance(finding, dict) and triage_key(finding) == key:
            return finding
    return None


def _segment_index(finding: Mapping[str, Any]) -> Optional[int]:
    value = finding.get("segment_index", finding.get("segment_id"))
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _pair_for(state: Mapping[str, Any], finding: Mapping[str, Any]) -> Mapping[str, Any]:
    index = _segment_index(finding)
    pairs = state.get("pairs") or []
    if isinstance(index, int) and 0 <= index < len(pairs) \
            and isinstance(pairs[index], Mapping):
        return pairs[index]
    return {}


def _record(state: Mapping[str, Any], key: str) -> Dict[str, Any]:
    entry = (state.get(_triage.STATE_KEY) or {}).get(key)
    return entry if isinstance(entry, dict) else {}


def _has_structural_evidence(entry: Mapping[str, Any]) -> bool:
    for item in entry.get("evidence") or []:
        if isinstance(item, Mapping) and str(item.get("kind") or "") == _STRUCTURAL_REPAIR:
            return True
    return False


def _same_kind(candidate: Mapping[str, Any], original: Mapping[str, Any]) -> bool:
    """Whether a re-run finding is the same detector class as the original.

    Verification is conservative: the same ``kind`` firing again means the
    detector is still unhappy, even if the concrete token or span differs.  A
    narrower comparison would let a repair that merely moved the defect read as
    a success.
    """
    kind = str(candidate.get("kind") or "")
    return bool(kind) and kind == str(original.get("kind") or "")


def _detector_still_fires(state: Mapping[str, Any], finding: Mapping[str, Any],
                          pair: Mapping[str, Any],
                          target: Optional[str] = None) -> bool:
    """Re-run the deterministic batch check on a target text.

    ``target`` defaults to the pair's current target (the committed segment).
    The precheck passes the staged candidate's proposed text instead, so the
    same helper covers both the dry-run before adoption and the post-check after
    it without writing anything into the pair.
    """
    source = str(pair.get("source") or "")
    target = str(pair.get("target") or "") if target is None else str(target)
    glossary = state.get("glossary") \
        or (state.get("glossary_frozen") or {}).get("entries") or []
    target_lang = str(state.get("target_lang") or "简体中文")
    try:
        rerun = _c.check_translation_batch([source], [target], glossary, target_lang)
    except Exception:  # noqa: BLE001 - if we cannot verify, treat as still firing
        return True
    for item in rerun or []:
        if isinstance(item, Mapping) and _same_kind(item, finding):
            return True
    return False


def _write(state: Dict[str, Any], finding: Mapping[str, Any], *,
           disposition: str, evidence: list, committed: bool,
           candidate_id: Optional[str] = None, step: str, ok: bool, reason: str,
           ) -> Optional[Dict[str, Any]]:
    """Write/refresh the triage record and stamp where the attempt stopped."""
    keyed = keyed_finding(finding)
    key = keyed["finding_id"]
    existing = _record(state, key)
    created_at = str(existing.get("created_at") or "")
    cid = str(existing.get("candidate_id") or "") if candidate_id is None \
        else str(candidate_id or "")
    record = _triage.record(
        state, keyed, disposition=disposition, actor_type="rule",
        evidence=list(evidence or []), candidate_id=cid,
        committed=bool(committed), now=created_at or None,
    )
    if record is None:
        return None
    entry = state[_triage.STATE_KEY][key]
    # The snapshot is the finding as it was when the record was first written;
    # a commit changes the target underneath it, so keep the original snapshot
    # (revert reads the pre-repair text from here).
    if existing.get("snapshot"):
        entry["snapshot"] = existing["snapshot"]
    entry["auto_resolution"] = {"step": step, "ok": bool(ok), "reason": str(reason or "")}
    return entry


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------

@_serialized_job_mutation
def stage_auto_resolution(job_id: str, finding_id: str, *,
                          actor: str = "qa_triage") -> Dict[str, Any]:
    """Stage a deterministic number fix as a candidate.  Never writes text."""
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    if not _triage.enabled(state):
        return {"staged": False, "reason": "triage_disabled", "candidate_id": ""}

    key = str(finding_id)
    finding = _find_finding(state, key)
    if finding is None:
        raise ValueError("finding has no current record")
    if str(finding.get("severity") or "").strip() == _triage.BLOCKING:
        return {"staged": False, "reason": "blocking", "candidate_id": ""}
    group_id = _finding_groups.group_id_for(finding)
    if not group_id or _finding_groups.finding_group_type(finding) != _NUMBER_KIND:
        return {"staged": False, "reason": "not_structural", "candidate_id": ""}

    try:
        staged = _c.materialize_deterministic_group_fix(
            job_id, group_id, actor=actor, segment_index=_segment_index(finding))
    except ValueError:
        # The deterministic group is not present (for example finding groups are
        # disabled): there is nothing this issue is allowed to stage.
        return {"staged": False, "reason": "no_group", "candidate_id": ""}

    # Reload after the nested staged save so we never save a stale snapshot.
    state = _c.load_job_state(job_id)
    finding = _find_finding(state, key)
    if finding is None:
        raise ValueError("finding disappeared after staging")

    candidate = staged.get("candidate") or None
    if candidate is None:
        _write(state, finding, disposition=_triage.NEEDS_INVESTIGATION, evidence=[],
               committed=False, candidate_id="", step="staged", ok=False,
               reason="needs_human_judgement")
        _c.save_job_state(job_id, state)
        return {"staged": False, "reason": "needs_human_judgement", "candidate_id": ""}

    candidate_id = str(candidate.get("candidate_id") or "")
    evidence = [{
        "kind": _STRUCTURAL_REPAIR,
        "ref": candidate_id or group_id,
        "detail": str(candidate.get("instruction") or "deterministic number fix"),
    }]
    _write(state, finding, disposition=_triage.AUTO_RESOLVABLE, evidence=evidence,
           committed=False, candidate_id=candidate_id, step="staged", ok=True, reason="")
    _c.save_job_state(job_id, state)
    return {"staged": True, "reason": "", "candidate_id": candidate_id,
            "candidate_status": str(candidate.get("status") or "")}


# ---------------------------------------------------------------------------
# committing
# ---------------------------------------------------------------------------

@_serialized_job_mutation
def commit_auto_resolution(job_id: str, finding_id: str, *,
                           actor: str = "qa_triage") -> Dict[str, Any]:
    """Adopt the staged candidate under five short-circuiting conditions."""
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    if not _triage.enabled(state):
        return {"committed": False, "step": "disabled", "reason": "triage_disabled"}

    key = str(finding_id)
    finding = _find_finding(state, key)
    if finding is None:
        raise ValueError("finding has no current record")
    if str(finding.get("severity") or "").strip() == _triage.BLOCKING:
        return {"committed": False, "step": "blocking", "reason": "blocking"}

    entry = _record(state, key)
    if str(entry.get("disposition") or "") != _triage.AUTO_RESOLVABLE \
            or not str(entry.get("candidate_id") or "") or entry.get("committed"):
        return {"committed": False, "step": "evidence", "reason": "nothing_staged"}

    # Condition 1 — the repair is deterministic structure, not semantics.
    if not _has_structural_evidence(entry):
        _write(state, finding, disposition=_triage.NEEDS_INVESTIGATION,
               evidence=list(entry.get("evidence") or []), committed=False,
               step="evidence", ok=False, reason="evidence is not structural_repair")
        _c.save_job_state(job_id, state)
        return {"committed": False, "step": "evidence", "reason": "not_structural"}

    # Condition 2 — the segment carries no human work.
    pair = _pair_for(state, finding)
    locked = list(_candidates_store.locked_protection(pair))
    if locked or pair.get("human_edited") or pair.get("excluded"):
        detail = ("protected:" + ",".join(locked)) if locked else "human_edited_or_excluded"
        _write(state, finding, disposition=_triage.NEEDS_INVESTIGATION,
               evidence=list(entry.get("evidence") or []), committed=False,
               step="protection", ok=False, reason=detail)
        _c.save_job_state(job_id, state)
        return {"committed": False, "step": "protection", "reason": detail}

    # Condition 2.5 — dry-run the staged candidate's text before adopting it.
    # A deterministic repair is only adopted if it would actually silence the
    # same detector; a candidate that still fires is never written into the pair.
    candidate_id = str(entry["candidate_id"])
    candidate_record = _candidates_store.by_id(state, candidate_id) or {}
    candidate_target = str(candidate_record.get("candidate_target") or "")
    if _detector_still_fires(state, finding, pair, target=candidate_target):
        reason = "precheck: detector still fires on the candidate text"
        _write(state, finding, disposition=_triage.NEEDS_INVESTIGATION,
               evidence=list(entry.get("evidence") or []), committed=False,
               step="precheck", ok=False, reason=reason)
        _c.save_job_state(job_id, state)
        return {"committed": False, "step": "precheck", "reason": reason}

    # Condition 3 — adoption succeeds (baseline, exclusion, source-quality, protection).
    # Snapshot the WHOLE task state before adoption.  Adoption mutates the pair,
    # the candidate record, translation_truth.version, human_actions and knowledge
    # candidates; a failed post-check must restore this exact snapshot rather than
    # patch a few fields back onto the adopted state.
    pre_adoption_state = deepcopy(state)
    try:
        adopted = _candidates.adopt_translation_candidate(
            job_id, candidate_id, actor=actor, allow_protected=False,
            record_type=AUTO_RESOLUTION_RECORD_TYPE)
    except Exception as exc:  # noqa: BLE001 - an error reads as human_required
        state = _c.load_job_state(job_id)
        finding = _find_finding(state, key)
        if finding is not None:
            _write(state, finding, disposition=_triage.HUMAN_REQUIRED,
                   evidence=list(_record(state, key).get("evidence") or []),
                   committed=False, step="adopt", ok=False, reason=f"error: {exc}")
            _c.save_job_state(job_id, state)
        return {"committed": False, "step": "adopt", "reason": f"error: {exc}"}

    if not adopted.get("committed"):
        state = _c.load_job_state(job_id)
        finding = _find_finding(state, key)
        reason = str(adopted.get("reason") or adopted.get("conflict") or "adoption refused")
        if finding is not None:
            _write(state, finding, disposition=_triage.NEEDS_INVESTIGATION,
                   evidence=list(_record(state, key).get("evidence") or []),
                   committed=False, step="adopt", ok=False, reason=reason)
            _c.save_job_state(job_id, state)
        return {"committed": False, "step": "adopt", "reason": reason,
                "conflict": str(adopted.get("conflict") or "")}

    # Condition 4 — the same detector is silent on the committed segment.
    state = _c.load_job_state(job_id)
    finding = _find_finding(state, key)
    if finding is None:
        raise ValueError("finding disappeared after adoption")
    pair = _pair_for(state, finding)
    evidence = list(_record(state, key).get("evidence") or [])
    if _detector_still_fires(state, finding, pair):
        # The committed text did not silence the detector.  Patching a few pair
        # fields back onto the adopted state would leave the candidate marked
        # adopted and leak every other adoption side effect (truth version, audit
        # rows, dropped knowledge candidates).  Restore the whole pre-adoption
        # snapshot and add only this failure record.
        restored = deepcopy(pre_adoption_state)
        restored_finding = _find_finding(restored, key)
        if restored_finding is None:
            return {"committed": False, "step": "restore",
                    "reason": "finding missing from pre-adoption snapshot"}
        _write(restored, restored_finding,
               disposition=_triage.NEEDS_INVESTIGATION,
               evidence=list(_record(restored, key).get("evidence") or []),
               committed=False, step="verification", ok=False,
               reason="detector still fires on the committed segment")
        # Adoption persisted one snapshot under this same lock.  Advance the
        # restored snapshot past that revision so save_job_state accepts it;
        # otherwise the platform rejects the pre-adoption snapshot as stale.
        current_revision = int(
            (_c.load_job_state(job_id) or {}).get("snapshot_revision") or 0)
        restored["snapshot_revision"] = current_revision
        try:
            _c.save_job_state(job_id, restored)
        except Exception as exc:  # noqa: BLE001 - never fall back to partial rollback
            return {"committed": False, "step": "restore_refused",
                    "reason": f"could not restore pre-adoption state: {exc}"}
        return {"committed": False, "step": "verification",
                "reason": "detector still fires"}

    # Condition 5 — commit, with verification evidence.
    evidence = evidence + [{
        "kind": _CANDIDATE_VERIFICATION,
        "ref": candidate_id,
        "detail": "the same detector is silent on the committed segment",
    }]
    _write(state, finding, disposition=_triage.AUTO_RESOLVABLE, evidence=evidence,
           committed=True, step="committed", ok=True, reason="")
    _c.save_job_state(job_id, state)
    return {"committed": True, "step": "committed", "candidate_id": candidate_id}


# ---------------------------------------------------------------------------
# reverting a committed auto-resolution
# ---------------------------------------------------------------------------

@_serialized_job_mutation
def revert_auto_resolution(job_id: str, finding_id: str, *,
                           actor: str = "qa_triage",
                           reason: str = "reverted") -> Dict[str, Any]:
    """Stage a revert candidate from the snapshot target.  Never writes text."""
    state = _c.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    if not _triage.enabled(state):
        return {"reverted": False, "reason": "triage_disabled"}

    key = str(finding_id)
    finding = _find_finding(state, key)
    if finding is None:
        raise ValueError("finding has no current record")
    entry = _record(state, key)
    if not entry.get("committed"):
        return {"reverted": False, "reason": "not_committed"}

    old_target = str((entry.get("snapshot") or {}).get("target") or "")
    if not old_target:
        return {"reverted": False, "reason": "no_snapshot_target"}
    pair = _pair_for(state, finding)
    if not pair:
        return {"reverted": False, "reason": "segment_missing"}

    draft = {
        "segment_index": _segment_index(finding),
        "segment_uid": str(pair.get(_candidates_store.SEGMENT_UID_FIELD) or ""),
        "source": str(pair.get("source") or ""),
        "candidate_target": old_target,
        "origin": "auto_revert",
        "instruction": "撤回自动修复：恢复旧译文",
        "baseline": _candidates_store.pair_signature(pair),
        "note": "撤回自动修复的候选；仍需人工采用，绝不直接覆盖正文",
        "finding_ids": [key],
    }
    result = _candidates.commit_candidates(job_id, [draft], actor=actor)
    created = list(result.get("created") or [])

    # The disposition returns to human_required; the revert text waits for a human.
    state = _c.load_job_state(job_id)
    _triage.revoke(state, key, reason=reason)
    _c.save_job_state(job_id, state)
    return {
        "reverted": True,
        "candidate_id": str((created[0] or {}).get("candidate_id") or "") if created else "",
    }
