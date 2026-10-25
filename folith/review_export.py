"""Review handoff export: decisions, target changes and unresolved findings (#103).

The export is a **read-only projection of existing state**: review events,
human actions, candidate records, findings, pairs and the delivery gate.  It
adds no reporting truth; deleting the export changes nothing in the task.

What it distinguishes (the issue's acceptance list):

``adopted``
    An adopted candidate whose text actually replaced the previous target.
``adopted_unchanged``
    An adoption where the candidate text equals the current target.  A human
    decision was recorded, but the target was **kept** -- this is never reported
    as a modification.
``manual_edit``
    The current target carries human-edit provenance (or was marked human
    edited) and differs from the recorded initial target.
``reviewed``
    The current text passed review and no human decision edited it.
``skipped``
    The deterministic pre-review gate recorded the segment as skipped
    (``skipped_low_risk``).  A skip is reported as skipped, never as a pass.
``unreviewed``
    Required review has not happened yet for that segment.
``unresolved``
    Open blocking/actionable findings; listed per segment and in a dedicated
    section with their evidence identifiers.

Unchanged targets are never reported as modified: ``modified`` is derived from
an actual before/after pair, not from the existence of a decision.  Every
segment row carries a stable segment UID, the review event id (when known) and
links to findings/candidates/decisions.

The module also renders Markdown for handoff.  Long text is exported verbatim by
default (no truncation); callers may pass ``max_text_chars`` for a compact
preview, in which case truncation is explicit in the payload.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import delivery as _delivery
from . import translation_evidence as _evidence

SCHEMA_VERSION = 1

STATUS_ADOPTED = "adopted"
STATUS_ADOPTED_UNCHANGED = "adopted_unchanged"
STATUS_MANUAL_EDIT = "manual_edit"
STATUS_REVIEWED = "reviewed"
STATUS_SKIPPED = "skipped"
STATUS_UNREVIEWED = "unreviewed"
STATUS_UNRESOLVED = "unresolved"

STATUS_LABELS = {
    STATUS_ADOPTED: "采用候选（正文已改）",
    STATUS_ADOPTED_UNCHANGED: "采用候选但保留原译文（正文未改）",
    STATUS_MANUAL_EDIT: "人工改写",
    STATUS_REVIEWED: "已审校",
    STATUS_SKIPPED: "按策略跳过（未独立审校）",
    STATUS_UNREVIEWED: "尚未审校",
    STATUS_UNRESOLVED: "有未解决发现项",
}

_SEVERITIES = ("blocking", "actionable", "informational")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _segment_uid(pair: Mapping[str, Any], job_id: str, index: int) -> str:
    uid = str(pair.get("segment_uid") or "")
    if uid:
        return uid
    from . import assets as _assets

    return str(_assets.segment_id(job_id, index))


def _candidate_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(item) for item in state.get("translation_candidates") or []
            if isinstance(item, Mapping)]


def _candidate_segment(candidate: Mapping[str, Any]) -> Optional[int]:
    index = candidate.get("segment_index")
    return index if isinstance(index, int) and not isinstance(index, bool) else None


def _adoption_for_segment(
    candidates: Sequence[Mapping[str, Any]], segment_uid: str, index: int,
) -> Dict[str, Any]:
    """Latest adopted candidate for a segment (adoption is the decision)."""
    matches = []
    for candidate in candidates:
        if str(candidate.get("status") or "") != "adopted":
            continue
        if _candidate_segment(candidate) != index:
            continue
        candidate_uid = str(candidate.get("segment_uid") or "")
        if candidate_uid and segment_uid and candidate_uid != segment_uid:
            continue
        matches.append(candidate)
    if not matches:
        return {}
    matches.sort(key=lambda item: str(item.get("resolved_at") or ""))
    return dict(matches[-1])


def _decisions(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    decisions = []
    for action in state.get("human_actions") or []:
        if not isinstance(action, Mapping):
            continue
        if str(action.get("record_type") or "") not in {"human_decision", "candidate"} \
                and not str(action.get("action") or "").startswith("candidate_"):
            continue
        decisions.append(dict(action))
    return decisions


def _current_decisions(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [item for item in _decisions(state)
            if str(item.get("status") or "current") == "current"]


def _findings_for(state: Mapping[str, Any], index: int) -> List[Dict[str, Any]]:
    return [dict(item) for item in state.get("findings") or []
            if isinstance(item, Mapping)
            and item.get("segment_index", item.get("segment_id")) == index]


def _finding_export(finding: Mapping[str, Any]) -> Dict[str, Any]:
    resolution = finding.get("resolution") if isinstance(
        finding.get("resolution"), Mapping) else {}
    return {
        "finding_id": str(finding.get("finding_id")
                          or _delivery.finding_id(dict(finding)) or ""),
        "segment_index": finding.get("segment_index", finding.get("segment_id")),
        "segment_id": finding.get("segment_id", finding.get("segment_index")),
        "type": finding.get("type"),
        "category": finding.get("category"),
        "kind": finding.get("kind"),
        "severity": finding.get("severity"),
        "detected_text": finding.get("detected_text"),
        "source_span": finding.get("source_span"),
        "target_span": finding.get("target_span"),
        "reason": finding.get("reason") or finding.get("summary"),
        "suggested_target": finding.get("suggested_target"),
        "evidence_refs": list(finding.get("evidence_refs") or []),
        "evidence_ids": list(finding.get("evidence_ids") or []),
        "review_event_id": finding.get("review_event_id"),
        "status": finding.get("status") or ("resolved" if finding.get("resolved") else "open"),
        "resolved": bool(finding.get("resolved")),
        "resolution": dict(resolution),
    }


def _edited_after_adoption(adopted: Mapping[str, Any], pair: Mapping[str, Any]) -> bool:
    """True when a person rewrote the target after the candidate was adopted.

    The adopted candidate's own text is the reference: a target that still
    equals it is the adoption, anything else was changed by someone afterwards.
    """
    adopted_text = str(adopted.get("candidate_target") or "")
    if not adopted_text:
        return False
    provenance = str(pair.get("target_provenance") or "")
    human = bool(pair.get("human_edited")) or provenance in {"human_edit", "human_accepted"}
    return human and str(pair.get("target") or "") != adopted_text


def _change_records(
    state: Mapping[str, Any],
    pairs: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Actual before/after text changes, from persisted records only."""
    changes = []
    for index, pair in enumerate(pairs):
        current = str(pair.get("target") or "")
        adopted = _adoption_for_segment(
            candidates, str(pair.get("segment_uid") or ""), index)
        if adopted:
            before = str(adopted.get("previous_target") or "")
            edited_after = _edited_after_adoption(adopted, pair)
            adopted_text = str(adopted.get("candidate_target") or "") \
                if edited_after else current
            if before and before != adopted_text:
                changes.append({
                    "segment_index": index,
                    "kind": "candidate_adoption",
                    "before": before,
                    "after": adopted_text,
                    "candidate_id": str(adopted.get("candidate_id") or ""),
                    "actor": str(adopted.get("resolved_by") or ""),
                    "timestamp": str(adopted.get("resolved_at") or ""),
                    "note": str(adopted.get("note") or ""),
                    "truth_revision": adopted.get("adopted_truth_version"),
                })
            if edited_after:
                changes.append({
                    "segment_index": index,
                    "kind": "manual_edit",
                    "before": adopted_text or before,
                    "after": current,
                    "candidate_id": "",
                    "actor": str(pair.get("accepted_by_human") or ""),
                    "timestamp": str(pair.get("accepted_at") or ""),
                    "note": str(pair.get("human_edit_note") or ""),
                    "truth_revision": (state.get("translation_truth") or {}).get("version"),
                })
            continue
        provenance = str(pair.get("target_provenance") or "")
        initial = pair.get("initial_target")
        if not (isinstance(initial, str) and initial.strip()):
            restore = pair.get("_translation_edit_restore")
            initial = str((restore or {}).get("target") or "") \
                if isinstance(restore, Mapping) else ""
        if (pair.get("human_edited") or provenance in {"human_edit", "human_accepted"}) \
                and isinstance(initial, str) and initial and initial != current:
            changes.append({
                "segment_index": index,
                "kind": "manual_edit",
                "before": initial,
                "after": current,
                "candidate_id": "",
                "actor": str(pair.get("accepted_by_human") or ""),
                "timestamp": str(pair.get("accepted_at") or ""),
                "note": str(pair.get("human_edit_note") or ""),
                "truth_revision": (state.get("translation_truth") or {}).get("version"),
            })
    return changes


def build_review_export(
    state: Mapping[str, Any],
    job_id: str,
    *,
    max_text_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the handoff export payload from authoritative state (read-only)."""
    pairs = [dict(pair) if isinstance(pair, Mapping) else {}
             for pair in state.get("pairs") or []]
    candidates = _candidate_records(state)
    readiness = _evidence.translation_review_readiness(state)
    skipped_ids = set(readiness.get("skipped_segment_ids") or [])
    skipped_reasons = readiness.get("skipped_segment_reasons") or {}
    unresolved = [_finding_export(item) for item in _delivery.unresolved_findings(dict(state))]
    unresolved_by_segment: Dict[int, List[Dict[str, Any]]] = {}
    for item in unresolved:
        index = item.get("segment_index")
        if isinstance(index, int):
            unresolved_by_segment.setdefault(index, []).append(item)
    changes = _change_records(state, pairs, candidates)
    changes_by_segment: Dict[int, List[Dict[str, Any]]] = {}
    for change in changes:
        changes_by_segment.setdefault(change["segment_index"], []).append(change)
    decisions = _decisions(state)
    decisions_by_segment: Dict[int, List[Dict[str, Any]]] = {}
    for decision in decisions:
        index = decision.get("segment_index")
        if not isinstance(index, int):
            finding = _finding_by_id(state, str(decision.get("finding_id") or ""))
            index = finding.get("segment_index") if finding else None
        if isinstance(index, int):
            decisions_by_segment.setdefault(index, []).append(decision)
    review_events = _review_events_by_segment(state)

    def text(value: str) -> tuple:
        if max_text_chars is not None and len(value) > max_text_chars:
            return value[:max_text_chars], True
        return value, False

    rows = []
    for index, pair in enumerate(pairs):
        uid = _segment_uid(pair, job_id, index)
        current = str(pair.get("target") or "")
        initial = pair.get("initial_target")
        initial_text = initial if isinstance(initial, str) else None
        adopted = _adoption_for_segment(candidates, uid, index)
        segment_unresolved = unresolved_by_segment.get(index, [])
        segment_changes = changes_by_segment.get(index, [])
        segment_decisions = decisions_by_segment.get(index, [])
        modified = bool(segment_changes)
        review_status = str(pair.get("review_status") or "")
        reviewed = bool(pair.get("reviewed")) or review_status.startswith("reviewed")
        provenance = str(pair.get("target_provenance") or "")
        if segment_unresolved:
            status = STATUS_UNRESOLVED
        elif adopted and _edited_after_adoption(adopted, pair):
            status = STATUS_MANUAL_EDIT
        elif adopted:
            status = (STATUS_ADOPTED
                      if str(adopted.get("previous_target") or "") != current
                      else STATUS_ADOPTED_UNCHANGED)
        elif pair.get("human_edited") or provenance in {"human_edit", "human_accepted"}:
            status = STATUS_MANUAL_EDIT
        elif reviewed:
            status = STATUS_REVIEWED
        elif index in skipped_ids:
            status = STATUS_SKIPPED
        else:
            status = STATUS_UNREVIEWED
        source_text, source_truncated = text(str(pair.get("source") or ""))
        target_text, target_truncated = text(current)
        row = {
            "segment_index": index,
            "segment_uid": uid,
            "segment_number": index + 1,
            "source": source_text,
            "source_truncated": source_truncated,
            "current_target": target_text,
            "target_truncated": target_truncated,
            "initial_target": initial_text,
            "modified": modified,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "reviewed": reviewed,
            "review_status": review_status,
            "skipped": index in skipped_ids,
            "skip_reason": str(skipped_reasons.get(str(index)) or ""),
            "unreviewed": (not reviewed) and index not in skipped_ids,
            "provenance": provenance,
            "from_tm": bool(pair.get("from_tm")),
            "review_event_id": review_events.get(index, ""),
            "findings": [_finding_export(f) for f in _findings_for(state, index)],
            "finding_ids": [item["finding_id"]
                            for item in [_finding_export(f) for f in _findings_for(state, index)]],
            "unresolved_finding_ids": [item["finding_id"] for item in segment_unresolved],
            "changes": segment_changes,
            "decisions": segment_decisions,
            "adopted_candidate_id": str(adopted.get("candidate_id") or ""),
        }
        rows.append(row)

    summary = {
        "segments": len(rows),
        # Flags, not the display status: a segment can be skipped *and* carry an
        # unresolved finding, and both facts must survive in the summary.
        "reviewed": sum(1 for row in rows if row["reviewed"]),
        "adopted": sum(1 for row in rows if row["status"] == STATUS_ADOPTED),
        "adopted_unchanged": sum(
            1 for row in rows if row["status"] == STATUS_ADOPTED_UNCHANGED),
        "manual_edits": sum(1 for change in changes
                            if change["kind"] == "manual_edit"),
        "skipped": sum(1 for row in rows if row["skipped"]),
        "unreviewed": sum(1 for row in rows if row["unreviewed"]),
        "unresolved_segments": sum(1 for row in rows if row["unresolved_finding_ids"]),
        "modified_targets": sum(1 for row in rows if row["modified"]),
        "unchanged_targets": sum(1 for row in rows if not row["modified"]),
        "unresolved_findings": len(unresolved),
        "unresolved_by_severity": {
            severity: sum(1 for item in unresolved if item.get("severity") == severity)
            for severity in _SEVERITIES
        },
        "decisions": len(decisions),
        "changes": len(changes),
        "review_required": bool(readiness.get("required")),
        "coverage_complete": bool(readiness.get("coverage_complete")),
        "delivery_status": str(state.get("delivery_status") or ""),
        "has_blocking": bool(state.get("has_blocking")),
    }
    resolved = [_finding_export(item) for item in state.get("findings") or []
                if isinstance(item, Mapping) and item.get("resolved")]
    dismissed = [item for item in resolved
                 if str(item.get("status") or "") in {"dismissed", "waived", "ignored"}
                 or str((item.get("resolution") or {}).get("action") or "")
                 in {"dismiss", "dismissed", "ignore", "waive"}]
    summary["resolved_findings"] = len(resolved)
    summary["dismissed_findings"] = len(dismissed)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "review_handoff",
        "job_id": str(job_id),
        "exported_at": _now_iso(),
        "target_lang": str(state.get("target_lang") or ""),
        "source_lang": str(state.get("source_lang") or ""),
        "summary": summary,
        "segments": rows,
        "decisions": decisions,
        "changes": changes,
        "unresolved_findings": unresolved,
        # Registered exceptions: findings a human explicitly dismissed/waived.
        # They are evidence of a decision, not silently dropped review work.
        "resolved_findings": resolved,
        "dismissed_findings": dismissed,
        "skipped_segments": [
            {"segment_index": row["segment_index"], "segment_uid": row["segment_uid"],
             "reason": row["skip_reason"]}
            for row in rows if row["skipped"]
        ],
        "unreviewed_segments": [
            {"segment_index": row["segment_index"], "segment_uid": row["segment_uid"]}
            for row in rows if row["unreviewed"]
        ],
        "provenance": {
            "state_keys": ["pairs", "findings", "human_actions",
                           "translation_candidates", "review_evidence",
                           "delivery_status"],
            "note": "read-only projection; no separate reporting truth",
        },
    }


def _finding_by_id(state: Mapping[str, Any], finding_id: str) -> Optional[Dict[str, Any]]:
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        if _delivery.finding_id(dict(finding)) == finding_id \
                or str(finding.get("finding_id") or "") == finding_id:
            return dict(finding)
    return None


def _review_events_by_segment(state: Mapping[str, Any]) -> Dict[int, str]:
    result: Dict[int, str] = {}
    for event in state.get("review_evidence") or []:
        if not isinstance(event, Mapping):
            continue
        segment = event.get("segment_index", event.get("segment_id"))
        event_id = str(event.get("review_event_id") or "")
        if isinstance(segment, int) and event_id:
            result[segment] = event_id
    return result


def export_json(state: Mapping[str, Any], job_id: str, *,
                max_text_chars: Optional[int] = None) -> str:
    return json.dumps(build_review_export(state, job_id,
                                          max_text_chars=max_text_chars),
                      ensure_ascii=False, indent=2)


def export_jsonl(state: Mapping[str, Any], job_id: str) -> str:
    """One JSON object per segment; the summary is the first line."""
    payload = build_review_export(state, job_id)
    lines = [json.dumps({"kind": "summary", **payload["summary"]},
                        ensure_ascii=False)]
    lines.extend(json.dumps({"kind": "segment", **row}, ensure_ascii=False)
                 for row in payload["segments"])
    lines.extend(json.dumps({"kind": "unresolved", **item}, ensure_ascii=False)
                 for item in payload["unresolved_findings"])
    return "\n".join(lines) + "\n"


def _clip(text: str, limit: int = 120) -> str:
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def export_markdown(state: Mapping[str, Any], job_id: str) -> str:
    payload = build_review_export(state, job_id)
    summary = payload["summary"]
    lines = [
        f"# 审校交接导出 · {job_id}",
        "",
        f"导出时间：{payload['exported_at']}　目标语言：{payload['target_lang'] or '未记录'}",
        "",
        "## 概览",
        "",
        "| 指标 | 数量 |",
        "| --- | --- |",
        f"| 段落 | {summary['segments']} |",
        f"| 已审校 | {summary['reviewed']} |",
        f"| 采用候选（正文已改） | {summary['adopted']} |",
        f"| 采用候选但保留译文 | {summary['adopted_unchanged']} |",
        f"| 人工改写 | {summary['manual_edits']} |",
        f"| 按策略跳过 | {summary['skipped']} |",
        f"| 尚未审校 | {summary['unreviewed']} |",
        f"| 有未解决发现项的段落 | {summary['unresolved_segments']} |",
        f"| 正文实际改动 | {summary['modified_targets']} |",
        f"| 未解决发现项 | {summary['unresolved_findings']}（blocking "
        f"{summary['unresolved_by_severity']['blocking']} / actionable "
        f"{summary['unresolved_by_severity']['actionable']}） |",
        "",
        "## 段落明细",
        "",
    ]
    for row in payload["segments"]:
        lines.append(f"### 第 {row['segment_number']} 段 · {row['status_label']}")
        lines.append("")
        lines.append(f"- segment_uid：`{row['segment_uid']}`")
        if row["review_event_id"]:
            lines.append(f"- review_event_id：`{row['review_event_id']}`")
        if row["skip_reason"]:
            lines.append(f"- 跳过原因：{row['skip_reason']}")
        lines.append(f"- 原文：{_clip(row['source'])}")
        lines.append(f"- 当前译文：{_clip(row['current_target'])}")
        if row["modified"]:
            for change in row["changes"]:
                lines.append(f"- 改动（{change['kind']}）：{_clip(change['before'])}"
                             f" → {_clip(change['after'])}"
                             + (f"（{change['actor']}）" if change["actor"] else ""))
        if row["unresolved_finding_ids"]:
            lines.append("- 未解决：" + "、".join(
                f"`{value}`" for value in row["unresolved_finding_ids"]))
        lines.append("")
    if payload["unresolved_findings"]:
        lines.extend(["## 未解决发现项", ""])
        for item in payload["unresolved_findings"]:
            index = item.get("segment_index")
            where = (f"第 {index + 1} 段" if isinstance(index, int)
                     else "未定位段落")
            lines.append(f"- `{item['finding_id']}` · {item['severity']} · "
                         f"{where}：{item['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
