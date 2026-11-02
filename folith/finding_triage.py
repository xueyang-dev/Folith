"""Persisted per-finding triage records (issue #286).

Storage and switch layer only.  A :class:`FindingTriage` record hangs off a
finding by its ``finding_id``; it never replaces the finding and never changes
detection, the ``ReviewFinding`` contract, the human-decision writers, the
review-queue projection or the delivery gate.

Record shape (``state["finding_triage"][finding_id]``)::

    {
      "finding_id": str,
      "input_fingerprint": str,
      "disposition": str,           # one of DISPOSITIONS
      "evidence": [{"kind": str, "ref": str, "detail": str}],
      "actor_type": str,            # "rule" | "model" | "human"
      "candidate_id": str,
      "committed": bool,
      "created_at": str,
      "revoked_at": str,
      "revoke_reason": str,
      "snapshot": {"source", "target", "summary", "severity", "detector"},
    }

Two properties make the record safe:

* **Additive.**  Deleting ``state["finding_triage"]`` restores today's behaviour
  exactly.  This module never deletes a finding, never writes ``status =
  "dismissed"``, never writes ``resolved`` and never writes a ``HumanDecision``.
* **Fail-closed read.**  ``disposition_of`` returns ``human_required`` for every
  missing, unknown, revoked, stale or broken record.

Scope for #286: this is the storage and switch layer only.  The evidence
allowlist is empty, so ``verified_safe`` cannot be produced yet; the
``verified_safe`` evidence kinds arrive with #287, and the "blocking is never
downgraded" invariant is deliberately left to that issue.
"""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from .translation_core.evidence import fingerprint, freshness

#: Environment kill switch.  Uses the current brand prefix; unlike the retained
#: ``finding_groups.ENV_SWITCH`` compatibility variable, this is a new switch and
#: must not add a new occurrence of the legacy prefix (see
#: ``tests/brand_regression_test.py``).
ENV_SWITCH = "FOLITH_QA_TRIAGE"

STATE_KEY = "finding_triage"
ENABLED_KEY = "qa_triage_enabled"

#: The five dispositions.  There is no sixth state and no free-text fallback.
VERIFIED_SAFE = "verified_safe"
AUTO_RESOLVABLE = "auto_resolvable"
NEEDS_INVESTIGATION = "needs_investigation"
HUMAN_REQUIRED = "human_required"
BLOCKING = "blocking"
DISPOSITIONS = (
    VERIFIED_SAFE,
    AUTO_RESOLVABLE,
    NEEDS_INVESTIGATION,
    HUMAN_REQUIRED,
    BLOCKING,
)

#: Default and failure disposition.  Everything that cannot be proven resolves here.
DEFAULT_DISPOSITION = HUMAN_REQUIRED

#: Who produced the disposition.  A model may reach at most ``needs_investigation``
#: (enforced from #287 onward; the vocabulary exists here so records are typed).
ACTOR_TYPES = ("rule", "model", "human")

#: Closed allowlist of deterministic evidence kinds.  Empty for #286: the
#: ``verified_safe`` kinds (concept decision, orthographic equivalence, frozen
#: retention policy) land with #287.  An empty allowlist means ``verified_safe``
#: cannot be recorded at all yet, which is the intended fail-closed behaviour.
EVIDENCE_KINDS: frozenset = frozenset()

#: Bounded growth, mirroring ``translation_candidates.MAX_RECORDS``.
MAX_RECORDS = 300

#: Every snapshot must carry these keys; a record missing one reads as
#: ``human_required`` so the audit view can always show what was decided.
SNAPSHOT_FIELDS = ("source", "target", "summary", "severity", "detector")

_FALSY = {"0", "false", "off", "no"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def enabled(state: Optional[Mapping[str, Any]] = None) -> bool:
    """Return whether triage is on for this task.  Default is **off**.

    The environment variable wins over state: any of ``0`` / ``false`` / ``off``
    / ``no`` disables triage even when ``state["qa_triage_enabled"]`` is true.
    Unlike ``finding_groups.enabled`` the state value must be explicitly true;
    an absent key means off.
    """
    raw = os.environ.get(ENV_SWITCH)
    if raw is not None and str(raw).strip().lower() in _FALSY:
        return False
    return (state or {}).get(ENABLED_KEY) is True


def _segment_index(finding: Mapping[str, Any]) -> Optional[int]:
    for key in ("segment_index", "segment_id"):
        value = finding.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _pair_for(state: Optional[Mapping[str, Any]],
              finding: Mapping[str, Any]) -> Mapping[str, Any]:
    pairs = (state or {}).get("pairs") or []
    index = _segment_index(finding)
    if isinstance(index, int) and 0 <= index < len(pairs) \
            and isinstance(pairs[index], Mapping):
        return pairs[index]
    return {}


def _finding_id(finding: Mapping[str, Any]) -> str:
    return str((finding or {}).get("finding_id")
               or (finding or {}).get("id") or "").strip()


def input_fingerprint(state: Optional[Mapping[str, Any]],
                      finding: Mapping[str, Any]) -> str:
    """Fingerprint the disposition depends on.

    The finding's own ``input_fingerprint`` wins when present (independent-review
    findings carry one).  Otherwise the fingerprint covers exactly the inputs the
    disposition read: the segment source and target, the finding code, the
    detector and the sorted evidence refs.  Hashing is delegated to
    ``translation_core.evidence.fingerprint`` so there is one convention.
    """
    own = str((finding or {}).get("input_fingerprint") or "").strip()
    if own:
        return own
    pair = _pair_for(state, finding)
    refs = sorted({
        str(ref).strip()
        for ref in (finding.get("evidence_refs") or finding.get("evidence_ids") or [])
        if str(ref).strip()
    })
    return fingerprint({
        "source": str(pair.get("source") or ""),
        "target": str(pair.get("target") or ""),
        "code": str(finding.get("code") or finding.get("category")
                    or finding.get("type") or "review"),
        "detector": str(finding.get("detector") or ""),
        "evidence_refs": refs,
    })


def _snapshot(state: Optional[Mapping[str, Any]],
              finding: Mapping[str, Any]) -> Dict[str, str]:
    pair = _pair_for(state, finding)

    def pick(*keys: str) -> str:
        for key in keys:
            value = finding.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    return {
        "source": str(pair.get("source") or pick("source_span", "source")),
        "target": str(pair.get("target") or pick("target_span", "target")),
        "summary": pick("summary", "reason"),
        "severity": str(finding.get("severity") or ""),
        "detector": str(finding.get("detector") or ""),
    }


def normalize_evidence(evidence: Any) -> List[Dict[str, str]]:
    """Validate and normalize an evidence list into ``{kind, ref, detail}``."""
    result: List[Dict[str, str]] = []
    for item in evidence or []:
        if not isinstance(item, Mapping):
            raise ValueError("evidence item must be a mapping")
        kind = str(item.get("kind") or "").strip()
        if not kind:
            raise ValueError("evidence item requires a kind")
        result.append({
            "kind": kind,
            "ref": str(item.get("ref") or "").strip(),
            "detail": str(item.get("detail") or "").strip(),
        })
    return result


def store(state: Dict[str, Any]) -> Dict[str, Any]:
    """Return the mutable triage map, creating it when absent."""
    current = state.get(STATE_KEY)
    if not isinstance(current, dict):
        current = {}
        state[STATE_KEY] = current
    return current


def record(
    state: Dict[str, Any],
    finding: Mapping[str, Any],
    *,
    disposition: str,
    actor_type: str,
    evidence: Any = None,
    candidate_id: str = "",
    committed: bool = False,
    now: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Write one triage record, or return ``None`` when triage is off.

    Refuses an unknown disposition, an unknown actor type, and ``verified_safe``
    without evidence.  Every provided evidence kind must be on ``EVIDENCE_KINDS``
    (empty for #286, so no evidence can be attached yet — fail-closed).
    """
    if not enabled(state):
        return None
    disposition = str(disposition or "").strip()
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {disposition!r}")
    actor_type = str(actor_type or "").strip()
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown actor_type: {actor_type!r}")
    finding_id = _finding_id(finding)
    if not finding_id:
        raise ValueError("finding has no finding_id")

    items = normalize_evidence(evidence)
    if disposition == VERIFIED_SAFE and not items:
        raise ValueError("verified_safe requires at least one evidence item")
    for item in items:
        if item["kind"] not in EVIDENCE_KINDS:
            raise ValueError(f"evidence kind not allowed: {item['kind']!r}")

    entry = {
        "finding_id": finding_id,
        "input_fingerprint": input_fingerprint(state, finding),
        "disposition": disposition,
        "evidence": items,
        "actor_type": actor_type,
        "candidate_id": str(candidate_id or "").strip(),
        "committed": bool(committed),
        "created_at": now or _now_iso(),
        "revoked_at": "",
        "revoke_reason": "",
        "snapshot": _snapshot(state, finding),
    }
    store(state)[finding_id] = entry
    prune(state)
    return dict(entry)


def revoke(state: Dict[str, Any], finding_id: str, *, reason: str = "",
           now: Optional[str] = None) -> bool:
    """Mark one record revoked.  Storage only: it never touches the finding.

    A revoked record reads as ``human_required`` (see ``disposition_of``).  Text
    is never reverted here; reverting a committed repair is a separate candidate
    transaction.
    """
    if not enabled(state):
        return False
    current = state.get(STATE_KEY)
    if not isinstance(current, dict):
        return False
    entry = current.get(str(finding_id or ""))
    if not isinstance(entry, dict):
        return False
    entry["revoked_at"] = now or _now_iso()
    entry["revoke_reason"] = str(reason or "").strip()
    return True


def disposition_of(state: Optional[Mapping[str, Any]],
                   finding: Mapping[str, Any]) -> str:
    """Return the effective disposition for one finding.  Fail-closed.

    Returns ``human_required`` when triage is off, when no record exists, when
    the record is malformed or unknown, when its snapshot is incomplete, when it
    is revoked, and when the fingerprint no longer matches current inputs.
    """
    try:
        if not enabled(state):
            return DEFAULT_DISPOSITION
        finding_id = _finding_id(finding)
        if not finding_id:
            return DEFAULT_DISPOSITION
        current = state.get(STATE_KEY)
        if not isinstance(current, Mapping):
            return DEFAULT_DISPOSITION
        entry = current.get(finding_id)
        if not isinstance(entry, Mapping):
            return DEFAULT_DISPOSITION
        disposition = str(entry.get("disposition") or "").strip()
        if disposition not in DISPOSITIONS:
            return DEFAULT_DISPOSITION
        if str(entry.get("revoked_at") or "").strip():
            return DEFAULT_DISPOSITION
        snapshot = entry.get("snapshot")
        if not isinstance(snapshot, Mapping) \
                or not all(field in snapshot for field in SNAPSHOT_FIELDS):
            return DEFAULT_DISPOSITION
        stored = str(entry.get("input_fingerprint") or "").strip()
        if not stored:
            return DEFAULT_DISPOSITION
        current_fingerprint = input_fingerprint(state, finding)
        if freshness(dict(entry), current_fingerprint) != "current":
            return DEFAULT_DISPOSITION
        return disposition
    except Exception:  # noqa: BLE001 - a broken record must never read as safe
        return DEFAULT_DISPOSITION


def _ended(entry: Any) -> bool:
    """A record is finished once its candidate committed or it was revoked."""
    if not isinstance(entry, Mapping):
        return False
    return bool(entry.get("committed")) or bool(str(entry.get("revoked_at") or "").strip())


def prune(state: Dict[str, Any]) -> int:
    """Bound the map to ``MAX_RECORDS`` by dropping the oldest finished records.

    An unfinished record is never dropped, exactly like the pending-candidate
    policy in ``translation_candidates._prune``.  Returns the number dropped.
    """
    current = state.get(STATE_KEY)
    if not isinstance(current, dict) or len(current) <= MAX_RECORDS:
        return 0
    excess = len(current) - MAX_RECORDS
    ordered = sorted(
        current.items(),
        key=lambda item: str((item[1] or {}).get("created_at") or "")
        if isinstance(item[1], Mapping) else "",
    )
    dropped = 0
    for finding_id, entry in ordered:
        if dropped >= excess:
            break
        if _ended(entry):
            del current[finding_id]
            dropped += 1
    return dropped


def records(state: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Read-only projection: ``{finding_id: record copy}`` for well-formed records."""
    current = (state or {}).get(STATE_KEY)
    if not isinstance(current, Mapping):
        return {}
    return {
        str(finding_id): deepcopy(dict(entry))
        for finding_id, entry in current.items()
        if isinstance(entry, Mapping)
    }


def annotate(findings: Any,
             state: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Copy findings with ``triage_disposition`` and ``triage_evidence_count``.

    A read model can then consume triage without importing this module twice.
    The findings themselves are not modified.
    """
    projection = records(state)
    result: List[Dict[str, Any]] = []
    for finding in findings or []:
        if not isinstance(finding, Mapping):
            continue
        item = dict(finding)
        entry = projection.get(_finding_id(finding))
        item["triage_disposition"] = disposition_of(state, finding)
        item["triage_evidence_count"] = len(entry.get("evidence") or []) \
            if isinstance(entry, Mapping) else 0
        result.append(item)
    return result
