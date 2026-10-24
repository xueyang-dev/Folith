"""Group deterministic number / unit / tag / placeholder findings (#101).

Why a grouping layer
--------------------
The deterministic QA pass reports one finding per lost token per segment.  On a
book that is the same *problem* repeated many times ("the number 12.5 vanished
in four places"), and handling it segment by segment loses the trace of which
occurrences belong together.

This module groups those findings by ``(type, token)`` and keeps, for every
occurrence, the evidence the finding already carried: segment index, spans,
reason, severity, the finding identity and any human decision.  It is read-model
plus preview only:

* grouping never edits findings, translations, review state or the delivery
  gate; a group is a view over ``state["findings"]``;
* when grouping is disabled (``FOLIOTHREAD_FINDING_GROUPS=0`` or
  ``state["finding_groups_enabled"] = False``), every finding stays individually
  inspectable exactly as before -- no record is rewritten;
* handling reuses the existing per-item human-decision path.  A group is a
  *batch preview list*: the caller still resolves each member through the P0
  review path, and excluded occurrences stay unchanged;
* a deterministic number fix can be materialised through the existing candidate
  transaction (``materialize_group_fix`` in core).  Adoption remains explicit,
  with the candidate baseline checking that the segment has not moved.

Only ``number`` / ``unit`` / ``tag`` / ``placeholder`` findings are groupable;
everything else stays a normal finding.  No model is called anywhere here.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from folith import translation_candidates as _cand

ENV_SWITCH = "FOLIOTHREAD_FINDING_GROUPS"
GROUP_TYPES = ("number", "unit", "tag", "placeholder")
TYPE_LABELS = {
    "number": "数字",
    "unit": "单位",
    "tag": "标记",
    "placeholder": "占位符",
}
EXCEPTIONS_KEY = "finding_group_exceptions"
_NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?![\w.])")

#: Decisions that end a finding; kept in the member evidence so a grouped view
#: never loses who decided what.
_RESOLVED = {"accept_resolution", "dismiss", "resolved", "preserved"}
_OPEN = {"", "open", "pending", "request_revision", "confirmed_open"}


def enabled(state: Optional[Mapping[str, Any]] = None) -> bool:
    raw = os.environ.get(ENV_SWITCH)
    if raw is not None and str(raw).strip().lower() in {"0", "false", "off", "no"}:
        return False
    return (state or {}).get("finding_groups_enabled") is not False


def _digest(*parts: Any) -> str:
    payload = "\u241f".join(str(part or "") for part in parts)
    return hashlib.blake2s(payload.encode("utf-8"), digest_size=6).hexdigest()


def finding_group_type(finding: Mapping[str, Any]) -> str:
    """Return one of ``GROUP_TYPES`` or ``""`` for a non-groupable finding."""
    if not isinstance(finding, Mapping):
        return ""
    group_type = str(finding.get("group_type") or "")
    if group_type in GROUP_TYPES:
        return group_type
    kind = str(finding.get("kind") or "")
    if kind in GROUP_TYPES:
        return kind
    # Older/imported findings may not carry ``kind``; infer only from the
    # preserved-token fields that the deterministic QA pass sets.
    if str(finding.get("type") or "") in {"check", "glossary"} \
            and str(finding.get("detector") or "") == "Deterministic QA":
        text = str(finding.get("source_span") or "")
        if re.fullmatch(r"<[^<>\s]{1,160}>", text):
            return "tag"
        if re.fullmatch(r"%[sd]|%1\$[sd]|\{\{?\w+\}?\}", text):
            return "placeholder"
    return ""


def finding_token(finding: Mapping[str, Any]) -> str:
    for key in ("group_token", "detected_text", "source_span"):
        value = str(finding.get(key) or "").strip()
        if value:
            return value
    return ""


def group_id_for(finding: Mapping[str, Any]) -> str:
    group_type = finding_group_type(finding)
    token = finding_token(finding)
    if not group_type or not token:
        return ""
    return "fg-" + _digest(group_type, token)


def normalize_exceptions(raw: Any) -> List[Dict[str, Any]]:
    """Accept group-level or occurrence-level exceptions.

    ``{"group_type": "number", "token": "12.5"}`` excludes the whole group;
    ``{"group_type": "number", "token": "12.5", "segment_index": 7}`` excludes
    one occurrence only.  A bare ``group_id`` also works.
    """
    result = []
    for item in raw or []:
        if isinstance(item, Mapping):
            entry = {
                "group_id": str(item.get("group_id") or ""),
                "group_type": str(item.get("group_type") or ""),
                "token": str(item.get("token") or ""),
                "segment_index": item.get("segment_index"),
                "reason": str(item.get("reason") or ""),
            }
            if entry["group_id"] or (entry["group_type"] and entry["token"]):
                result.append(entry)
    return result


def _exceptions(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return normalize_exceptions(state.get(EXCEPTIONS_KEY))


def _excluded(finding: Mapping[str, Any], exceptions: Sequence[Mapping[str, Any]]) -> str:
    group_id = group_id_for(finding)
    segment_index = finding.get("segment_index", finding.get("segment_id"))
    for entry in exceptions:
        if entry.get("group_id") and entry["group_id"] == group_id:
            return str(entry.get("reason") or "已登记为例外")
        if entry.get("group_type") and entry.get("token"):
            if entry["group_type"] != finding_group_type(finding):
                continue
            if entry["token"] != finding_token(finding):
                continue
            allowed_segment = entry.get("segment_index")
            if allowed_segment is None or allowed_segment == segment_index:
                return str(entry.get("reason") or "已登记为例外")
    return ""


def _decision(finding: Mapping[str, Any]) -> str:
    for key in ("decision", "human_decision", "status", "resolution"):
        value = str(finding.get(key) or "")
        if value:
            return value
    if finding.get("resolved") is True:
        return "resolved"
    return "open"


def _member(finding: Mapping[str, Any], finding_index: int) -> Dict[str, Any]:
    return {
        "finding_index": finding_index,
        "finding_id": str(finding.get("finding_id") or ""),
        "segment_index": finding.get("segment_index", finding.get("segment_id")),
        "severity": str(finding.get("severity") or "actionable"),
        "summary": str(finding.get("summary") or finding.get("reason") or ""),
        "source_span": finding.get("source_span"),
        "target_span": finding.get("target_span"),
        "reason": str(finding.get("reason") or ""),
        "detected_text": finding.get("detected_text"),
        "expected_number": finding.get("expected_number"),
        "expected_unit": finding.get("expected_unit"),
        "occurrence": finding.get("occurrence"),
        "decision": _decision(finding),
        "status": str(finding.get("status") or "open"),
        "category": str(finding.get("category") or ""),
        "evidence_refs": list(finding.get("evidence_refs") or []),
        "evidence_ids": list(finding.get("evidence_ids") or []),
    }


def group_findings(
    findings: Sequence[Mapping[str, Any]],
    *,
    exceptions: Sequence[Mapping[str, Any]] = (),
    max_groups: int = 500,
) -> List[Dict[str, Any]]:
    """Group groupable findings, preserving order of first occurrence."""
    order: List[str] = []
    groups: Dict[str, Dict[str, Any]] = {}
    for index, finding in enumerate(findings or []):
        if not isinstance(finding, Mapping):
            continue
        if not finding_group_type(finding):
            continue
        group_id = group_id_for(finding)
        if not group_id:
            continue
        if group_id not in groups:
            if len(order) >= max_groups:
                continue
            order.append(group_id)
            groups[group_id] = {
                "group_id": group_id,
                "group_type": finding_group_type(finding),
                "label": TYPE_LABELS.get(finding_group_type(finding), "结构项"),
                "token": finding_token(finding),
                "members": [],
                "excluded": [],
                "segments": set(),
                "open_count": 0,
                "resolved_count": 0,
            }
        group = groups[group_id]
        reason = _excluded(finding, exceptions)
        member = _member(finding, index)
        if reason:
            member["excluded_reason"] = reason
            group["excluded"].append(member)
            continue
        group["members"].append(member)
        if isinstance(member["segment_index"], int):
            group["segments"].add(member["segment_index"])
        decision = member["decision"]
        if decision in _RESOLVED:
            group["resolved_count"] += 1
        else:
            group["open_count"] += 1
    result = []
    for group_id in order:
        group = groups[group_id]
        segments = sorted(group["segments"])
        group["segments"] = segments
        group["segment_count"] = len(segments)
        group["occurrence_count"] = len(group["members"])
        group["excluded_count"] = len(group["excluded"])
        group["severity"] = ("blocking"
                             if any(item["severity"] == "blocking"
                                    for item in group["members"])
                             else "actionable")
        group["status"] = ("open" if group["open_count"] else
                           ("resolved" if group["members"] else "excluded"))
        result.append(group)
    return result


def annotation(
    finding: Mapping[str, Any],
    *,
    exceptions: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """Group metadata for one finding; empty for non-groupable/excluded items."""
    group_id = group_id_for(finding)
    if not group_id or _excluded(finding, exceptions):
        return {}
    group_type = finding_group_type(finding)
    return {
        "finding_group_id": group_id,
        "finding_group_type": group_type,
        "finding_group_label": TYPE_LABELS.get(group_type, "结构项"),
        "finding_group_token": finding_token(finding),
    }


def annotate_findings(
    findings: Sequence[Mapping[str, Any]],
    *,
    exceptions: Sequence[Mapping[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """Copy findings with ``finding_group_id`` / label for groupable items."""
    annotated = []
    for finding in findings or []:
        if not isinstance(finding, Mapping):
            continue
        item = dict(finding)
        item.update(annotation(finding, exceptions=exceptions))
        annotated.append(item)
    return annotated


def group_summaries(groups: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Compact, UI-friendly projection of groups (no member paragraphs)."""
    return [{
        "group_id": group.get("group_id"),
        "group_type": group.get("group_type"),
        "label": group.get("label"),
        "token": group.get("token"),
        "severity": group.get("severity"),
        "status": group.get("status"),
        "occurrence_count": group.get("occurrence_count"),
        "segment_count": group.get("segment_count"),
        "segments": group.get("segments"),
        "open_count": group.get("open_count"),
        "excluded_count": group.get("excluded_count"),
    } for group in groups]


def preview_group(
    group: Mapping[str, Any],
    pairs: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Per-occurrence preview: what is open, what is excluded, what text is now.

    Read-only.  The caller resolves members through the existing human-decision
    path; this preview is what makes the group's evidence inspectable first.
    """
    rows = []
    for member in list(group.get("members") or []) + list(group.get("excluded") or []):
        index = member.get("segment_index")
        pair = pairs[index] if isinstance(index, int) and 0 <= index < len(pairs) else {}
        rows.append({
            "segment_index": index,
            "finding_id": member.get("finding_id"),
            "summary": member.get("summary"),
            "severity": member.get("severity"),
            "decision": member.get("decision"),
            "target": str(pair.get("target") or ""),
            "source_span": member.get("source_span"),
            "target_span": member.get("target_span"),
            "excluded": bool(member.get("excluded_reason")),
            "excluded_reason": member.get("excluded_reason", ""),
        })
    rows.sort(key=lambda row: (row["segment_index"] if isinstance(
        row["segment_index"], int) else 10 ** 9))
    return {
        "group_id": group.get("group_id"),
        "group_type": group.get("group_type"),
        "label": group.get("label"),
        "token": group.get("token"),
        "rows": rows,
        "would_change_text": False,
    }


def build_number_fix_draft(
    state: Mapping[str, Any],
    group: Mapping[str, Any],
    *,
    segment_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Deterministic candidate for one number occurrence, or ``None`` when unsafe.

    A fix is only built when that segment has exactly one source number missing
    from the target and exactly one extra number present in the target.  Then
    the proposal is a single literal substitution.  Anything more ambiguous
    stays a human decision: a wrong automatic "fix" to a number is worse than
    an open finding.
    """
    if not isinstance(group, Mapping) or group.get("group_type") != "number":
        return None
    members = [item for item in group.get("members") or []
               if not item.get("excluded_reason")]
    if segment_index is not None:
        members = [item for item in members
                   if item.get("segment_index") == segment_index]
    if not members:
        return None
    member = members[0]
    index = member.get("segment_index")
    pairs = state.get("pairs") or []
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return None
    pair = pairs[index]
    if not isinstance(pair, Mapping):
        return None
    source = str(pair.get("source") or "")
    target = str(pair.get("target") or "")
    source_numbers = _NUMBER_RE.findall(source)
    target_numbers = _NUMBER_RE.findall(target)
    missing = _NUMBER_RE.findall(str(member.get("source_span")
                                      or group.get("token") or ""))
    if len(missing) != 1:
        return None
    missing_token = missing[0]
    target_extra = [token for token in target_numbers
                    if token != missing_token
                    and token not in source_numbers]
    if len(target_extra) != 1:
        return None
    wrong = target_extra[0]
    if target.count(wrong) != 1 or wrong not in target:
        return None
    proposed = target.replace(wrong, missing_token, 1)
    if proposed == target:
        return None
    return {
        "segment_index": index,
        "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
        "source": source,
        "candidate_target": proposed,
        "origin": _cand.ORIGIN_DETERMINISTIC_FIX,
        "instruction": f"数字「{wrong}」改回原文的「{missing_token}」",
        "baseline": _cand.pair_signature(pair),
        "note": "确定性结构修复；采用前请确认数字语境",
        "finding_ids": [item.get("finding_id") or ""
                        for item in members if item.get("finding_id")],
    }
