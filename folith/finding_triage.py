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

Scope for #286: this is the storage and switch layer only.  #287 adds the
evidence allowlist and the read/write constraints: ``verified_safe`` can only be
recorded from an allowlisted deterministic evidence item, ``blocking`` can never
be downgraded, and a model actor can reach at most ``needs_investigation``.

Only three kinds can *produce* ``verified_safe`` here — ``concept_decision``,
``orthographic_equivalence`` and ``retention_policy``.  ``candidate_verification``
and ``structural_repair`` are registered in the allowlist so a later issue can
attach them, but they never justify ``verified_safe`` in this round.
"""
from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from . import concept_cards, terminology_variants
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

#: Closed allowlist of deterministic evidence kinds (#287).  A record whose
#: evidence kind is outside this set is invalid and reads as ``human_required``.
CONCEPT_DECISION = "concept_decision"
ORTHOGRAPHIC_EQUIVALENCE = "orthographic_equivalence"
RETENTION_POLICY = "retention_policy"
CANDIDATE_VERIFICATION = "candidate_verification"
STRUCTURAL_REPAIR = "structural_repair"
EVIDENCE_KINDS = frozenset({
    CONCEPT_DECISION,
    ORTHOGRAPHIC_EQUIVALENCE,
    RETENTION_POLICY,
    CANDIDATE_VERIFICATION,
    STRUCTURAL_REPAIR,
})

#: Kinds that can justify ``verified_safe`` in this round.  The other two are
#: registered for a later issue and never produce ``verified_safe`` here.
PRODUCING_EVIDENCE_KINDS = frozenset({
    CONCEPT_DECISION,
    ORTHOGRAPHIC_EQUIVALENCE,
    RETENTION_POLICY,
})

#: Dispositions a model actor may never reach (it caps at ``needs_investigation``).
_MODEL_FORBIDDEN = frozenset({VERIFIED_SAFE, AUTO_RESOLVABLE, BLOCKING})

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


# ---------------------------------------------------------------------------
# Orthographic folding (triage side only; the detectors are not touched)
#
# A closed character table, not a fuzzy matcher.  It folds traditional ->
# simplified and a small set of orthographic variants (异体) so that
# 増淵/增渊 and 〈奏谳书〉/奏谳书 become the same key, plus bracket/punctuation
# folding that ``consistency.spelling`` does not do (it strips 《》 and quotes
# only).  It deliberately does NOT implement Japanese shinjitai/kyūjitai as a
# rule: 増->增 is registered here as an 异体 variant, nothing more.
# ---------------------------------------------------------------------------

_TRADITIONAL_TO_SIMPLIFIED = {
    "書": "书", "漢": "汉", "淵": "渊", "讞": "谳", "獻": "献",
    "這": "这", "說": "说", "國": "国", "學": "学", "會": "会",
    "對": "对", "時": "时", "個": "个", "們": "们", "來": "来",
    "為": "为", "開": "开", "關": "关", "長": "长", "門": "门",
    "問": "问", "間": "间", "東": "东", "車": "车", "馬": "马",
    "鳥": "鸟", "魚": "鱼", "龍": "龙", "見": "见", "頁": "页",
    "風": "风", "飛": "飞", "頭": "头", "題": "题", "讓": "让",
    "認": "认", "識": "识", "語": "语", "讀": "读", "寫": "写",
    "聽": "听", "買": "买", "賣": "卖", "錢": "钱", "銀": "银",
    "鐵": "铁", "張": "张", "強": "强", "歸": "归", "當": "当",
    "戰": "战", "爭": "争", "愛": "爱", "應": "应", "舊": "旧",
    "華": "华", "萬": "万", "與": "与", "將": "将", "歲": "岁",
    "歷": "历", "廣": "广", "從": "从", "眾": "众", "傳": "传",
    "價": "价", "內": "内", "兩": "两", "別": "别", "創": "创",
    "動": "动", "務": "务", "勝": "胜", "勞": "劳", "區": "区",
    "醫": "医", "錯": "错", "閱": "阅", "陰": "阴", "陽": "阳",
    "隨": "随", "險": "险", "隱": "隐", "雖": "虽", "雙": "双",
    "離": "离", "難": "难", "電": "电", "圖": "图", "團": "团",
    "導": "导", "層": "层", "幣": "币", "師": "师", "帶": "带",
    "幫": "帮", "庫": "库", "廠": "厂", "實": "实", "寧": "宁",
    "體": "体",
}

#: Orthographic variants (异体), NOT a Japanese old/new-font rule.
_ORTHOGRAPHIC_VARIANTS = {
    "増": "增",
}

#: Brackets/quotes the triage fold removes anywhere (consistency.spelling only
#: strips 《》 and quotes, and only at the edges).
_FOLD_BRACKETS = "〈〉《》「」『』〔〕〖〗【】“”‘’\"'"

_ORTHOGRAPHIC_FOLD = {**_TRADITIONAL_TO_SIMPLIFIED, **_ORTHOGRAPHIC_VARIANTS}


def orthographic_key(form: Any) -> str:
    """Fold one surface form to a comparable orthographic key.

    NFKC + casefold + punctuation folding via ``terminology_variants.punct_key``,
    then the closed character table.  Two forms are orthographically equivalent
    iff their keys are equal and the raw forms differ.
    """
    text = "".join(ch for ch in str(form or "") if ch not in _FOLD_BRACKETS)
    key = terminology_variants.punct_key(text)
    return "".join(_ORTHOGRAPHIC_FOLD.get(ch, ch) for ch in key)


def _variant_forms(finding: Mapping[str, Any]) -> List[str]:
    """Target-side surface forms of an entity conflict: preferred + observed."""
    forms: List[str] = []
    preferred = str(finding.get("preferred_target") or "").strip()
    if preferred:
        forms.append(preferred)
    for value in finding.get("observed_targets") or []:
        text = str(value or "").strip()
        if text:
            forms.append(text)
    return list(dict.fromkeys(forms))


def _finding_span_text(finding: Mapping[str, Any]) -> str:
    for key in ("detected_text", "source_span", "target_span"):
        text = str(finding.get(key) or "").strip()
        if text:
            return text
    return ""


def _span_key(text: Any) -> str:
    return terminology_variants.punct_key(str(text or ""))


def _concept_decision_evidence(state: Optional[Mapping[str, Any]],
                               finding: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """A stored concept decision that already settles this finding's concept."""
    decisions = (state or {}).get("concept_decisions")
    if not decisions:
        return None
    probes = []
    source = str(finding.get("source") or "").strip()
    if source:
        probes.append(terminology_variants.casefold_key(source))
    for alias in finding.get("aliases") or []:
        probes.append(terminology_variants.casefold_key(alias))
    probes = [probe for probe in probes if probe]
    if not probes:
        return None
    card = concept_cards.decision_for_keys(decisions, probes)
    if not card or not str(card.get("card_id") or "").strip():
        return None
    preferred = str(card.get("preferred_target") or "").strip()
    if not preferred and not card.get("keep_variants"):
        return None
    detail = "keep_variants" if card.get("keep_variants") else f"preferred {preferred}"
    return {"kind": CONCEPT_DECISION, "ref": str(card["card_id"]), "detail": detail}


def _orthographic_equivalence_evidence(
        state: Optional[Mapping[str, Any]],
        finding: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """The target variants are the same word written differently."""
    if str(finding.get("type") or "") != "entity_conflict":
        return None
    forms = _variant_forms(finding)
    distinct = sorted(set(forms))
    if len(distinct) < 2:
        return None
    keys = {orthographic_key(form) for form in distinct}
    if len(keys) != 1:
        return None
    canonical = next(iter(keys))
    ref = " == ".join(distinct)
    return {"kind": ORTHOGRAPHIC_EQUIVALENCE, "ref": ref,
            "detail": f"orthographic variants of {canonical}"}


def _retention_policy_evidence(
        state: Optional[Mapping[str, Any]],
        finding: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    """A frozen ``preserve`` glossary entry covers the text this finding flags."""
    frozen = (state or {}).get("glossary_frozen")
    if not isinstance(frozen, Mapping):
        return None
    entries = frozen.get("entries")
    if not isinstance(entries, (list, tuple)):
        return None
    text = _finding_span_text(finding)
    if not text:
        return None
    target_key = _span_key(text)
    version = frozen.get("version")
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("behavior") or "").strip() != "preserve":
            continue
        forms = (entry.get("target"), entry.get("source"), entry.get("preferred"))
        if not any(form and _span_key(form) == target_key for form in forms):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id:
            continue
        return {"kind": RETENTION_POLICY, "ref": f"{entry_id}@v{version}",
                "detail": f"frozen preserve entry {entry_id}"}
    return None


#: Fixed resolution order: a concept decision outranks a pure character fold.
_RESOLVERS = (
    (CONCEPT_DECISION, _concept_decision_evidence),
    (ORTHOGRAPHIC_EQUIVALENCE, _orthographic_equivalence_evidence),
    (RETENTION_POLICY, _retention_policy_evidence),
)
_RESOLVER_BY_KIND = dict(_RESOLVERS)


def _evidence_holds(state: Optional[Mapping[str, Any]],
                    finding: Mapping[str, Any],
                    item: Mapping[str, Any]) -> bool:
    """Whether one evidence item still reproduces from the current state."""
    resolver = _RESOLVER_BY_KIND.get(str(item.get("kind") or ""))
    if resolver is None:
        return False
    resolved = resolver(state, finding)
    return bool(resolved) and resolved.get("ref") == str(item.get("ref") or "")


def _verified_safe_evidence_valid(state: Optional[Mapping[str, Any]],
                                  finding: Mapping[str, Any],
                                  evidence: Any) -> bool:
    """``verified_safe`` needs one allowlisted, currently-holding producing item."""
    if not isinstance(evidence, (list, tuple)) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, Mapping):
            return False
        kind = str(item.get("kind") or "")
        if kind not in EVIDENCE_KINDS:
            return False
        if kind in PRODUCING_EVIDENCE_KINDS and _evidence_holds(state, finding, item):
            return True
    return False


def _cap_for_actor(disposition: str, actor_type: Any) -> str:
    """A model actor may reach at most ``needs_investigation``."""
    if str(actor_type or "").strip() == "model" and disposition in _MODEL_FORBIDDEN:
        return NEEDS_INVESTIGATION
    return disposition


def classify(state: Optional[Mapping[str, Any]],
             finding: Mapping[str, Any]) -> Dict[str, Any]:
    """Derive a disposition candidate from deterministic evidence.

    ``blocking`` severity short-circuits before any evidence lookup; otherwise the
    allowlist is tried in a fixed order and the first match yields
    ``verified_safe``; no match yields ``human_required``.  Pure read.
    """
    if str(finding.get("severity") or "").strip() == BLOCKING:
        return {"disposition": BLOCKING, "evidence": [], "actor_type": "rule"}
    for _kind, resolver in _RESOLVERS:
        item = resolver(state, finding)
        if item:
            return {"disposition": VERIFIED_SAFE, "evidence": [item],
                    "actor_type": "rule"}
    return {"disposition": HUMAN_REQUIRED, "evidence": [], "actor_type": "rule"}


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

    Refuses an unknown disposition or actor type, a model actor claiming more
    than ``needs_investigation``, a ``verified_safe``/``auto_resolvable`` write on
    a blocking finding, and ``verified_safe`` without at least one allowlisted
    producing evidence item that currently holds.  Every evidence kind must be on
    ``EVIDENCE_KINDS``.
    """
    if not enabled(state):
        return None
    disposition = str(disposition or "").strip()
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {disposition!r}")
    actor_type = str(actor_type or "").strip()
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"unknown actor_type: {actor_type!r}")
    if actor_type == "model" and disposition in _MODEL_FORBIDDEN:
        raise ValueError(
            f"a model actor may not record {disposition!r}; "
            "it caps at needs_investigation")
    finding_id = _finding_id(finding)
    if not finding_id:
        raise ValueError("finding has no finding_id")
    if str(finding.get("severity") or "").strip() == BLOCKING \
            and disposition in (VERIFIED_SAFE, AUTO_RESOLVABLE):
        raise ValueError("blocking findings cannot be downgraded")

    items = normalize_evidence(evidence)
    for item in items:
        if item["kind"] not in EVIDENCE_KINDS:
            raise ValueError(f"evidence kind not allowed: {item['kind']!r}")
    if disposition == VERIFIED_SAFE:
        if not items:
            raise ValueError("verified_safe requires at least one evidence item")
        if not _verified_safe_evidence_valid(state, finding, items):
            raise ValueError(
                "verified_safe requires allowlisted evidence that currently holds")

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

    A ``blocking`` severity always reads as ``blocking``, whatever is stored and
    even when triage is off — it is never downgraded.  Otherwise this returns
    ``human_required`` when triage is off, when no record exists, when the record
    is malformed, unknown, revoked or stale, when its snapshot is incomplete, or
    when a ``verified_safe`` record has no allowlisted producing evidence that
    still holds.  A model actor caps at ``needs_investigation``.
    """
    try:
        if str(finding.get("severity") or "").strip() == BLOCKING:
            return BLOCKING
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
        evidence = entry.get("evidence")
        if evidence is not None and not isinstance(evidence, (list, tuple)):
            return DEFAULT_DISPOSITION
        for item in evidence or []:
            if not isinstance(item, Mapping) \
                    or str(item.get("kind") or "") not in EVIDENCE_KINDS:
                return DEFAULT_DISPOSITION
        if disposition == VERIFIED_SAFE \
                and not _verified_safe_evidence_valid(state, finding, evidence):
            return DEFAULT_DISPOSITION
        return _cap_for_actor(disposition, entry.get("actor_type"))
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
