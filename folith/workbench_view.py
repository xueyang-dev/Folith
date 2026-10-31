"""Read-only human-facing projection for the translation review workbench."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import delivery, translation_evidence
from . import finding_groups as _finding_groups


#: 左栏三组（xueyang-dev/Folith#102）：必须处理 / 建议 / 已处理 · 仅供参考。
_LIST_GROUP_BY_SEVERITY = {
    "blocking": "must",
    "actionable": "suggested",
    "informational": "reference",
}

#: 术语类发现按概念归并：一条术语只出现一次，而不是每段一张卡。
_TERM_FINDING_TYPES = frozenset({"glossary_stale"})
_TERM_SEVERITY_ORDER = {"blocking": 0, "actionable": 1, "informational": 2}
#: How many affected paragraphs a term card carries for its side-by-side detail.
TERM_SEGMENT_PREVIEW = 100


_SKIP_REASON_COPY = {
    "already_reviewed_tm": "复用已审校翻译记忆",
    "clean_deterministic": "无确定性风险信号",
    "unknown": "未记录跳过原因",
}


def skip_reason_label(reason: Any) -> str:
    """Explain why a segment was skipped without turning the skip into a pass."""
    text = str(reason or "").strip()
    return _SKIP_REASON_COPY.get(text, text or "按策略跳过")


def _skip_reason_breakdown(reason_counts: Mapping[str, Any]) -> str:
    parts = [
        f"{skip_reason_label(reason)} {count} 段"
        for reason, count in sorted((reason_counts or {}).items())
        if isinstance(count, int) and count > 0
    ]
    return "；".join(parts)


READINESS_COPY = {
    "not_run": ("尚未审校", "当前译文尚未开始独立审校", "warning"),
    "missing": ("还有内容待处理", "完成待审段落或处理必须解决的问题后即可继续", "warning"),
    "failed": ("审校未完成", "部分段落的独立审校未成功，请重试", "danger"),
    "stale": ("译文已变化，需要重新审校", "上次审校仍保留在历史中，但不再代表当前译文", "warning"),
    "current": ("当前译文已完成审校", "所有需要独立审校的段落都与当前译文一致", "success"),
    "not_required": ("当前任务未启用独立审校", "此任务沿用原有交付流程", "neutral"),
}


def _coverage_copy(readiness: Mapping[str, Any]) -> tuple[str, str, str]:
    status = str(readiness.get("status") or "not_run")
    label, detail, tone = READINESS_COPY.get(status, READINESS_COPY["not_run"])
    if status != "current":
        return label, detail, tone
    coverage = str(readiness.get("coverage_status") or "")
    skipped = int(readiness.get("skipped_count") or 0)
    reviewed = int(readiness.get("reviewed_count") or 0)
    total = len(readiness.get("expected_segment_ids") or [])
    breakdown = _skip_reason_breakdown(readiness.get("skipped_reason_counts") or {})
    if coverage == "skipped":
        return (
            f"{skipped} 段未执行本轮独立语义审校（按策略跳过）",
            (f"按策略跳过：{breakdown}。这些段落没有独立审校结论，"
             "不等于审校通过；现有交付门禁保持不变。"),
            "warning",
        )
    if coverage == "partial" and skipped:
        return (
            "部分译文未执行独立语义审校",
            (f"{reviewed} / {total} 段已完成独立审校，"
             f"{skipped} 段未执行本轮独立语义审校（按策略跳过：{breakdown}）。"),
            "warning",
        )
    return label, detail, tone


FINDING_STATUS_COPY = {
    "open": "待处理",
    "resolved": "已确认解决",
    "dismissed": "已确认保留",
    "stale": "已过期",
}

#: 人工确认"问题是真的、但译文还没改"的决定。问题仍留在队列里，但它的状态
#: 必须和"没人看过"区分开（#93 把决定与文本变更分开之后，#102 补上显示）。
_OPEN_CONFIRMATION_DECISIONS = frozenset({"request_revision", "confirmed_open"})
_CONFIRMATION_LABEL = "已确认存在，待修订"


def _open_confirmations(state: Mapping[str, Any]) -> Dict[str, str]:
    """Read-only index: finding id → ``confirmed_open`` for still-open findings.

    Scans the authoritative human-action journal once per queue render; it does
    not persist anything or create a second decision store.
    """
    latest: Dict[str, str] = {}
    for action in state.get("human_actions") or []:
        if not isinstance(action, Mapping):
            continue
        finding_id_ = str(action.get("finding_id") or "")
        if not finding_id_:
            continue
        if str(action.get("record_type") or "") not in {
                "human_decision", "finding_confirmation"}:
            continue
        if action.get("status") == "superseded":
            continue
        name = str(action.get("action") or "")
        if name in _OPEN_CONFIRMATION_DECISIONS:
            latest[finding_id_] = "confirmed_open"
        elif name in {"accept_resolution", "dismiss", "human_fixed", "preserved"}:
            latest.pop(finding_id_, None)
    return latest

_STALE_REASON_COPY = (
    (("canonical glossary", "glossary"), "项目术语发生变化"),
    (("confirmed style", "style knowledge"), "项目风格规则发生变化"),
    (("document profile",), "文档画像发生变化"),
    (("target", "translation truth", "current_translation", "translation"),
     "当前译文已修改"),
    (("superseded",), "已有更新的审校结果"),
)


def stale_reason_label(reason: Any) -> str:
    """Translate a persisted technical stale reason without hiding its source."""
    text = str(reason or "").strip()
    folded = text.casefold()
    for needles, label in _STALE_REASON_COPY:
        if any(needle in folded for needle in needles):
            return label
    return "审校依据发生变化" if text else "当前内容需要重新审校"


def _segment_id(value: Mapping[str, Any]) -> Optional[int]:
    segment_id = value.get("segment_id")
    if isinstance(segment_id, bool) or not isinstance(segment_id, int):
        segment_id = value.get("segment_index")
    return segment_id if isinstance(segment_id, int) and not isinstance(
        segment_id, bool) else None


def _pair(state: Mapping[str, Any], segment_id: Optional[int]) -> Mapping[str, Any]:
    pairs = state.get("pairs") or []
    if segment_id is None or not 0 <= segment_id < len(pairs):
        return {}
    pair = pairs[segment_id]
    return pair if isinstance(pair, Mapping) else {}


def _current_finding(state: Mapping[str, Any], finding: Mapping[str, Any], *,
                     index: "translation_evidence.ReviewIndex | None" = None) -> bool:
    if finding.get("status") == "stale" or finding.get(
            "superseded_by_review_event_id"):
        return False
    if finding.get("type") != "review" or not finding.get("input_fingerprint"):
        return True
    segment_id = _segment_id(finding)
    if segment_id is None:
        return False
    event = translation_evidence.current_review_event(state, segment_id, index=index)
    return bool(event and event.get("review_event_id") == finding.get(
        "review_event_id"))


def _identity_detail(context: Mapping[str, Any]) -> str:
    span = str(context.get("detected_text") or context.get("target_span")
               or context.get("source_span") or "").strip()
    if span:
        preview = " ".join(span.split())
        return f"“{preview[:24]}{'…' if len(preview) > 24 else ''}”"
    return str(context.get("category_label") or "审校问题")


def _is_invariant(finding: Mapping[str, Any]) -> bool:
    return (finding.get("type") == "delivery_invariant"
            or finding.get("detector") == "Translation Target Invariant")


def _finding_item(state: Mapping[str, Any], finding: Mapping[str, Any], *,
                  confirmations: Mapping[str, str] | None = None) -> Dict[str, Any]:
    context = delivery.finding_context(dict(state), dict(finding))
    segment_id = context.get("segment_index")
    severity = str(context.get("severity") or "informational")
    filter_group = {
        "blocking": "pending",
        "actionable": "suggested",
        "informational": "reference",
    }.get(severity, "reference")
    rendered_id = str(context.get("finding_id") or "")
    core_id = str(finding.get("finding_id") or "")
    summary = str(context.get("summary") or context.get("reason")
                  or "旧版本审校记录").strip()
    core_finding = bool(finding.get("finding_id") and finding.get(
        "input_fingerprint"))
    # 两条写入路径的键不同：delivery.record_finding_confirmation 记渲染 ID，
    # runtime 的 HumanDecision 记核心 ID。两个都要认，否则同一条确认在
    # 「审校工作台」里会看起来像没人处理过。
    confirmed = (not finding.get("resolved")) and bool(
        (confirmations or {}).get(rendered_id)
        or (core_id and (confirmations or {}).get(core_id)))
    return {
        "id": f"finding:{rendered_id}",
        "kind": "finding",
        "filter_group": filter_group,
        "list_group": _LIST_GROUP_BY_SEVERITY.get(severity, "reference"),
        "affected_segment_ids": ([segment_id]
                                 if isinstance(segment_id, int)
                                 and not isinstance(segment_id, bool) else []),
        "priority": {"blocking": 2, "actionable": 4,
                     "informational": 5}.get(severity, 5),
        "segment_id": segment_id,
        "segment_index": segment_id,
        "segment_number": context.get("segment_number"),
        "finding_id": rendered_id,
        "core_finding_id": str(finding.get("finding_id") or ""),
        "core_finding": core_finding,
        "requires_human_confirmation": bool(
            finding.get("requires_human_confirmation")),
        # 交付完整性（Translation Target Invariant）是硬门禁：记一条"已解决 / 保留"
        # 不会让导出放行，只有改译文或重译才行——所以不给这两个按钮（试用反馈：
        # 点了确认已解决，导出照样被拦）。
        "decidable": not (core_finding and finding.get(
            "identity_stability") == "provisional") and not _is_invariant(finding),
        "invariant": _is_invariant(finding),
        "severity": severity,
        "severity_label": context.get("severity_label"),
        "finding_type": context.get("finding_type"),
        "entity_source": (str(finding.get("source") or "")
                          if finding.get("type") == "entity_conflict" else ""),
        "status": str(finding.get("status") or (
            "resolved" if finding.get("resolved") else "open")),
        "status_label": FINDING_STATUS_COPY.get(str(finding.get("status") or "open"),
                                                "待处理"),
        "decision": "confirmed_open" if confirmed else "",
        "decision_label": _CONFIRMATION_LABEL if confirmed else "",
        "category": context.get("category"),
        "category_label": context.get("category_label"),
        "title": f"第 {context.get('segment_number')} 段 · {_identity_detail(context)}",
        # 段号的框架文案由界面翻译；这里给出不带段号的定位说明，
        # 免得为了翻译"第 N 段"去改核心拼好的整串。
        "identity_detail": _identity_detail(context),
        "summary": summary,
        "reason": context.get("reason"),
        "source": context.get("source"),
        "target": context.get("target"),
        "source_span": context.get("source_span"),
        "target_span": context.get("target_span"),
        "preferred_target": str(finding.get("preferred_target") or "").strip(),
        "explanation": context.get("explanation"),
        "recommendation": context.get("recommendation"),
        "suggested_target": str(finding.get("suggested_target") or "").strip(),
        "legacy_diagnostic": context.get("legacy_diagnostic"),
        "proper_noun_candidate": context.get("proper_noun_candidate"),
        "detected_text": context.get("detected_text"),
        "detector": context.get("detector"),
        "confidence": context.get("confidence"),
        "evidence_refs": list(context.get("evidence_refs") or []),
        "evidence_ids": list(context.get("evidence_ids") or []),
        "review_evidence": list(context.get("review_evidence") or []),
        "review_event_id": context.get("review_event_id"),
        "entry_id": str(finding.get("entry_id") or ""),
        "entry_ids": list(finding.get('entry_ids') or []),
        "technical_reason": str(finding.get("stale_reason") or ""),
        "identity_stability": finding.get("identity_stability") or "legacy",
        # #101: only number/unit/tag/placeholder findings are grouped; every
        # other finding keeps exactly its previous shape.
        **(_finding_groups.annotation(
            finding,
            exceptions=_finding_groups.normalize_exceptions(
                state.get(_finding_groups.EXCEPTIONS_KEY)))
           if _finding_groups.enabled(state) else {}),
    }


def _historical_event(state: Mapping[str, Any], segment_id: int, *,
                      index: "translation_evidence.ReviewIndex | None" = None,
                      ) -> Mapping[str, Any]:
    if index is not None:
        return index.latest_any.get(segment_id) or {}
    for event in reversed(state.get("review_evidence") or []):
        if not isinstance(event, Mapping):
            continue
        ids = event.get("segment_ids") or (event.get("completion_receipt") or {}).get(
            "reviewed_segment_ids") or []
        if segment_id in ids:
            return event
    return {}


def _review_task_item(
    state: Mapping[str, Any], kind: str, segment_id: int, *,
    index: "translation_evidence.ReviewIndex | None" = None,
    skip_reason: str = "",
) -> Dict[str, Any]:
    pair = _pair(state, segment_id)
    event = _historical_event(state, segment_id, index=index)
    if kind == "skipped":
        status_label = "未执行独立审校"
        summary = (f"按策略跳过：{skip_reason_label(skip_reason)}。"
                   "没有独立语义审校结果，不等于审校通过。")
        action, priority = "review", 2
    else:
        copy = {
            "failed": ("审校未完成", "上次独立审校调用失败", "retry", 0),
            "stale": ("需要重新审校", stale_reason_label(event.get("stale_reason")),
                      "rereview", 1),
            "missing": ("尚未审校", "当前译文尚未完成独立审校", "review", 3),
        }
        status_label, summary, action, priority = copy[kind]
    return {
        "id": f"{kind}:segment:{segment_id}",
        "kind": kind,
        "filter_group": ("skipped" if kind == "skipped" else
                         "rereview" if kind in {"failed", "stale"} else "pending"),
        # 审校覆盖类的一律是「必须处理」：没审过、审失败、译文变了都要先处理。
        "list_group": "must",
        "affected_segment_ids": [segment_id],
        "priority": priority,
        "segment_id": segment_id,
        "segment_index": segment_id,
        "segment_number": segment_id + 1,
        "identity_detail": status_label,
        "finding_id": "",
        "core_finding_id": "",
        "core_finding": False,
        "requires_human_confirmation": False,
        "decidable": False,
        "severity": ("informational" if kind == "skipped" else
                     "blocking" if kind in {"failed", "stale"} else "actionable"),
        "severity_label": status_label,
        "status": kind,
        "status_label": status_label,
        "category": "review_coverage" if kind == "skipped" else "review_freshness",
        "category_label": "审校覆盖" if kind == "skipped" else "独立审校",
        "title": f"第 {segment_id + 1} 段 · {status_label}",
        "summary": summary,
        "reason": summary,
        "source": str(pair.get("source") or ""),
        "target": str(pair.get("target") or ""),
        "source_span": "",
        "target_span": "",
        "explanation": summary,
        "recommendation": ("如需覆盖本段，请重新运行独立审校。"
                           if kind == "skipped"
                           else "重新运行此段审校，生成与当前译文一致的新结果。"),
        "suggested_target": "",
        "legacy_diagnostic": False,
        "proper_noun_candidate": False,
        "detected_text": "",
        "detector": "Independent Review",
        "confidence": None,
        "evidence_refs": [],
        "evidence_ids": [],
        "review_evidence": [],
        "review_event_id": event.get("review_event_id"),
        "entry_id": "",
        "technical_reason": (skip_reason if kind == "skipped"
                             else str(event.get("stale_reason")
                                      or event.get("error") or "")),
        "identity_stability": "synthetic",
        "action": action,
    }


def _glossary_entries_by_id(state: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    from . import models
    frozen = state.get("glossary_frozen") or {}
    # The card describes the frozen decision, so its entry wins over an unfrozen draft.
    entries = models.normalize_glossary(
        frozen.get("entries") or state.get("glossary") or [])
    return {entry["id"]: entry for entry in entries if entry.get("id")}


def _merge_term_cards(
    state: Mapping[str, Any], items: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """One card per term: the queue follows concepts, not segments."""
    groups: Dict[str, List[Mapping[str, Any]]] = {}
    kept: List[Dict[str, Any]] = []
    for item in items:
        entry_id = str(item.get("entry_id") or "")
        if item.get("finding_type") in _TERM_FINDING_TYPES and entry_id:
            groups.setdefault(entry_id, []).append(item)
        else:
            kept.append(dict(item))
    if not groups:
        return kept
    entries = _glossary_entries_by_id(state)
    for entry_id, rows in groups.items():
        representative = next(
            (row for row in rows if row.get("finding_type") == "glossary_stale"),
            rows[0])
        entry = entries.get(entry_id) or {}
        severe = min((str(row.get("severity") or "informational") for row in rows),
                     key=lambda value: _TERM_SEVERITY_ORDER.get(value, 9))
        segments = sorted({
            index
            for row in rows
            for index in (row.get("affected_segment_ids")
                          or [row.get("segment_index")])
            if isinstance(index, int) and not isinstance(index, bool)
        })
        source = str(entry.get("source") or representative.get("source_span")
                     or "").strip()
        target = str(entry.get("preferred") or entry.get("target")
                     or representative.get("target_span") or "").strip()
        stale = any(row.get("finding_type") == "glossary_stale" for row in rows)
        previous = str(representative.get("previous_target") or "").strip()
        count = len(segments) or 1
        if source and target:
            title = f"{source} → {target} · 影响 {count} 段"
            if stale and previous and previous != target:
                # The conflict the user has to settle: the translation still says
                # the old term, the frozen glossary now says the new one.
                summary = (f"「{source}」的译法从「{previous}」改为「{target}」，"
                           f"{count} 段译文需要确认或重译")
            elif stale:
                summary = f"冻结术语表已更新「{source}」的译法，{count} 段译文需要确认或重译"
            else:
                summary = f"译文与术语表不一致：「{source}」应译为「{target}」，涉及 {count} 段"
        elif source:
            title = f"术语「{source}」· 影响 {count} 段"
            summary = str(representative.get("summary") or "")
            if summary and f"{count} 段" not in summary:
                summary = f"{summary}（涉及 {count} 段）"
        else:
            title = str(representative.get("title") or "")
            summary = str(representative.get("summary") or "")
        handled = all(bool(row.get("handled")) for row in rows)
        pairs = state.get("pairs") or []
        segment_rows: List[Dict[str, Any]] = []
        for index in segments[:TERM_SEGMENT_PREVIEW]:
            pair = pairs[index] if 0 <= index < len(pairs) else {}
            pair = pair if isinstance(pair, Mapping) else {}
            current = str(pair.get("target") or "")
            segment_rows.append({
                "index": index,
                "number": index + 1,
                "source": str(pair.get("source") or ""),
                "target": current,
                # False is the conflict: the paragraph still uses another spelling.
                "uses_frozen": not target or not current or target in current,
            })
        card = dict(representative)
        card.update({
            "term_card": True,
            "handled": handled,
            "list_group": ("reference" if handled
                           else _LIST_GROUP_BY_SEVERITY.get(severe, "reference")),
            "term_entry_id": entry_id,
            "term_source": source,
            "term_target": target,
            "term_previous_target": previous,
            "term_segments": segment_rows,
            "term_segment_total": len(segments),
            "affected_segment_ids": segments,
            "finding_ids": [str(row.get("finding_id") or "") for row in rows
                            if row.get("finding_id")],
            "severity": severe,
            "priority": {"blocking": 2, "actionable": 4,
                         "informational": 5}.get(severe, 5),
            "title": title,
            "summary": summary,
            "segment_id": segments[0] if segments else representative.get("segment_id"),
            "segment_index": segments[0] if segments else representative.get("segment_index"),
        })
        kept.append(card)
    return kept


def _handled_items(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Decisions already made, one card per concept, collapsed by default."""
    items: List[Dict[str, Any]] = []
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping):
            continue
        if not (finding.get("resolved") or str(finding.get("status") or "")
                in {"resolved", "dismissed"}):
            continue
        if finding.get("superseded_by_review_event_id"):
            continue
        items.append(_finding_item(state, finding))
    if not items:
        return []
    for item in items:
        item.update({"handled": True, "list_group": "reference", "priority": 6})
    return _merge_term_cards(state, items)


def _queue_items(state: Mapping[str, Any], readiness: Mapping[str, Any]) -> List[Dict[str, Any]]:
    # One index for the whole queue: per-item reverse scans of every review event
    # were O(items × events) (thousands × ~1 000 on a book-length task).
    index = translation_evidence.review_index(state)
    items = [
        _review_task_item(state, kind, segment_id, index=index)
        for kind, key in (
            ("failed", "failed_segment_ids"),
            ("stale", "stale_segment_ids"),
            ("missing", "missing_segment_ids"),
        )
        for segment_id in readiness.get(key) or []
    ]
    skipped_reasons = readiness.get("skipped_segment_reasons") or {}
    items.extend(
        _review_task_item(
            state, "skipped", segment_id, index=index,
            skip_reason=str(skipped_reasons.get(str(segment_id)) or ""))
        for segment_id in readiness.get("skipped_segment_ids") or []
    )
    current_findings = [
        finding for finding in delivery.review_queue_findings(dict(state))
        if _current_finding(state, finding, index=index)
    ]
    confirmations = _open_confirmations(state)
    items.extend(_finding_item(state, finding, confirmations=confirmations)
                 for finding in current_findings)
    items = _merge_term_cards(state, items)
    return sorted(items, key=lambda item: (
        item["priority"], item.get("segment_id") if isinstance(
            item.get("segment_id"), int) else 10**9, item["id"]))


def _primary_action(
    readiness: Mapping[str, Any], counts: Mapping[str, int], items: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    failed = len(readiness.get("failed_segment_ids") or [])
    stale = len(readiness.get("stale_segment_ids") or [])
    missing = len(readiness.get("missing_segment_ids") or [])
    blocking = counts.get("blocking", 0)
    actionable = counts.get("actionable", 0)
    if failed:
        return {"kind": "review_segments", "label": f"重试失败的 {failed} 段",
                "segment_ids": list(readiness["failed_segment_ids"])}
    if stale:
        return {"kind": "review_segments", "label": f"重新审校 {stale} 段",
                "segment_ids": list(readiness["stale_segment_ids"])}
    if blocking:
        first = next(item for item in items if item.get("severity") == "blocking")
        return {"kind": "handle_finding", "label": f"继续处理 {blocking} 个必须处理的问题",
                "item_id": first["id"]}
    if missing:
        return {"kind": "review_segments", "label": f"审校尚未完成的 {missing} 段",
                "segment_ids": list(readiness["missing_segment_ids"])}
    if actionable:
        first = next(item for item in items if item.get("severity") == "actionable")
        return {"kind": "handle_finding", "label": f"检查 {actionable} 个建议",
                "item_id": first["id"]}
    return {"kind": "delivery", "label": "前往交付"}


def _delivery_copy(readiness: Mapping[str, Any], counts: Mapping[str, int]) -> Dict[str, Any]:
    skipped = int(readiness.get("skipped_count") or 0)
    if readiness.get("ready") and skipped:
        return {
            "title": "可以准备交付（审校覆盖不完整）",
            "detail": (f"{skipped} 段未执行本轮独立语义审校（按策略跳过）。"
                       "现有交付门禁允许继续准备交付。"),
            "tone": "warning",
        }
    if readiness.get("ready"):
        return {"title": "翻译审校已完成", "detail": "当前译文的审校状态允许继续准备交付",
                "tone": "success"}
    failed = len(readiness.get("failed_segment_ids") or [])
    stale = len(readiness.get("stale_segment_ids") or [])
    missing = len(readiness.get("missing_segment_ids") or [])
    if failed:
        detail = f"{failed} 段审校未完成，请重试"
    elif stale:
        detail = f"{stale} 段译文在上次审校后发生变化，需要重新审校"
    elif counts.get("blocking"):
        detail = f"还有 {counts['blocking']} 个必须处理的问题"
    elif missing:
        detail = f"还有 {missing} 段尚未完成审校"
    else:
        detail = "当前译文的审校尚未完成"
    return {"title": "暂时无法交付", "detail": detail, "tone": "warning"}


def _nav_copy(readiness: Mapping[str, Any], counts: Mapping[str, int]) -> Dict[str, str]:
    failed = len(readiness.get("failed_segment_ids") or [])
    stale = len(readiness.get("stale_segment_ids") or [])
    missing = len(readiness.get("missing_segment_ids") or [])
    if failed:
        return {"label": f"{failed} 未完成", "tone": "attention", "title": "有审校调用未完成"}
    if stale:
        return {"label": f"{stale} 需复审", "tone": "stale", "title": "当前译文变化后需要重新审校"}
    if counts.get("blocking"):
        return {"label": f"{counts['blocking']} 必须处理", "tone": "attention",
                "title": "还有必须处理的审校问题"}
    if missing:
        return {"label": f"{missing} 待审", "tone": "pending", "title": "还有段落尚未审校"}
    if readiness.get("status") == "not_required":
        return {"label": "不适用", "tone": "neutral", "title": "当前任务未启用独立审校"}
    skipped = int(readiness.get("skipped_count") or 0)
    if skipped:
        return {"label": f"{skipped} 段跳过", "tone": "pending",
                "title": "部分段落未执行本轮独立语义审校"}
    if counts.get("actionable"):
        return {"label": f"{counts['actionable']} 建议", "tone": "pending",
                "title": "有建议检查项，不影响交付"}
    if counts.get("informational"):
        return {"label": f"{counts['informational']} 参考", "tone": "neutral",
                "title": "有可供参考的审校信息"}
    return {"label": "✓", "tone": "done", "title": "当前译文审校已完成"}


def _risk_acceptance(
    state: Mapping[str, Any], readiness: Mapping[str, Any],
) -> Dict[str, Any]:
    records = [item for item in state.get("human_actions") or []
               if isinstance(item, Mapping)
               and item.get("record_type") == "delivery_risk_acceptance"]
    current = [dict(item) for item in records if item.get("status") == "current"]
    decision_only_block = (
        readiness.get("status") == "missing"
        and bool(readiness.get("blocking_finding_ids"))
        and not readiness.get("missing_segment_ids")
        and not readiness.get("stale_segment_ids")
        and not readiness.get("failed_segment_ids")
        and all(item.get("status") == "missing"
                for item in readiness.get("decision_errors") or [])
    )
    return {
        "current": bool(current),
        "current_records": current,
        "stale_count": sum(item.get("status") == "stale" for item in records),
        "available": bool(readiness.get("ready") or decision_only_block),
    }


def review_workbench_view(state: Mapping[str, Any]) -> Dict[str, Any]:
    """Project authoritative review truth into one read-only workbench model."""
    readiness = translation_evidence.translation_review_readiness(state)
    items = _queue_items(state, readiness)
    counts = {
        severity: sum(item.get("kind") == "finding" and item.get("severity") == severity
                      for item in items)
        for severity in delivery.SEVERITY_LABELS
    }
    filter_counts = {
        name: sum(item.get("filter_group") == name for item in items)
        for name in ("pending", "rereview", "skipped", "suggested", "reference")
    }
    filter_counts["all"] = len(items)
    handled = _handled_items(state)
    group_counts = {
        name: sum(item.get("list_group") == name for item in items + handled)
        for name in ("must", "suggested", "reference")
    }
    label, detail, tone = _coverage_copy(readiness)
    progress = {
        "total": len(readiness.get("expected_segment_ids") or []),
        "current": len(readiness.get("current_segment_ids") or []),
        "skipped": len(readiness.get("skipped_segment_ids") or []),
        "stale": len(readiness.get("stale_segment_ids") or []),
        "missing": len(readiness.get("missing_segment_ids") or []),
        "failed": len(readiness.get("failed_segment_ids") or []),
        "blocking": counts["blocking"],
        "actionable": counts["actionable"],
        "informational": counts["informational"],
    }
    groups = (_finding_groups.group_findings(
        state.get("findings") or [],
        exceptions=_finding_groups.normalize_exceptions(
            state.get(_finding_groups.EXCEPTIONS_KEY)))
        if _finding_groups.enabled(state) else [])
    return {
        "finding_groups": _finding_groups.group_summaries(groups),
        "readiness": {**readiness, "label": label, "detail": detail, "tone": tone},
        "progress": progress,
        "queue_items": items,
        "handled_items": handled,
        "queue_counts": counts,
        "filter_counts": filter_counts,
        "group_counts": group_counts,
        "primary_action": _primary_action(readiness, counts, items),
        "delivery": _delivery_copy(readiness, counts),
        "nav": _nav_copy(readiness, counts),
        "risk_acceptance": _risk_acceptance(state, readiness),
    }


def filter_queue_items(
    items: Sequence[Mapping[str, Any]], filter_name: str,
) -> List[Dict[str, Any]]:
    """Return one queue filter without changing its priority order."""
    if filter_name == "all":
        return [dict(item) for item in items]
    return [dict(item) for item in items if item.get("filter_group") == filter_name]


def select_queue_item(
    items: Sequence[Mapping[str, Any]], selected_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """Keep a valid selection or deterministically select the first item."""
    selector = str(selected_id or "")
    selected = next((item for item in items if item.get("id") == selector), None)
    return dict(selected or items[0]) if selected or items else None


def next_queue_item_id(
    items: Sequence[Mapping[str, Any]], completed_id: Any,
    segment_id: Optional[int] = None,
) -> Optional[str]:
    """Prefer another current issue on the same segment, then queue priority."""
    remaining = [item for item in items if item.get("id") != str(completed_id or "")]
    if segment_id is not None:
        same_segment = next((item for item in remaining
                             if item.get("segment_id") == segment_id), None)
        if same_segment:
            return str(same_segment["id"])
    return str(remaining[0]["id"]) if remaining else None


def _timestamp(value: Any) -> str:
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text


def review_history(
    state: Mapping[str, Any], segment_id: Optional[int], limit: int = 5,
) -> List[Dict[str, Any]]:
    """Return compact current-vs-history copy for one segment."""
    if segment_id is None:
        return []
    rows: List[Dict[str, Any]] = []
    finding_ids = set()
    for finding in state.get("findings") or []:
        if not isinstance(finding, Mapping) or _segment_id(finding) != segment_id:
            continue
        finding_ids.update({str(finding.get("finding_id") or ""),
                            delivery.finding_id(dict(finding))})
        status = str(finding.get("status") or (
            "resolved" if finding.get("resolved") else "open"))
        if status == "stale":
            label = "上次审校发现已过期"
        elif status == "resolved":
            label = "问题已确认解决"
        elif status == "dismissed":
            label = "已确认保留当前译文"
        else:
            label = "发现审校问题"
        rows.append({
            "timestamp": _timestamp(finding.get("stale_at") or finding.get("created_at")),
            "label": label,
            "detail": str(finding.get("summary") or finding.get("reason") or ""),
        })
    for action in state.get("human_actions") or []:
        if not isinstance(action, Mapping) or not ({
                str(action.get("finding_id") or ""),
                str(action.get("translation_core_finding_id") or ""),
        } & finding_ids):
            continue
        decision = str(action.get("decision") or action.get("action") or "")
        label = {
            "accept_resolution": "确认问题已解决",
            "dismiss": "确认保留当前译文",
            "request_revision": "请求修改译文",
            "accepted_risk": "记录风险接受",
        }.get(decision, "记录人工处理")
        if action.get("status") == "stale":
            label += "（已过期）"
        actor = str(action.get("actor") or "用户")
        rows.append({
            "timestamp": _timestamp(action.get("decided_at") or action.get("timestamp")),
            "label": label,
            "detail": f"{actor} · {str(action.get('note') or '').strip()}".rstrip(" ·"),
        })
    for event in state.get("review_evidence") or []:
        if not isinstance(event, Mapping):
            continue
        ids = event.get("segment_ids") or (event.get("completion_receipt") or {}).get(
            "reviewed_segment_ids") or []
        if segment_id not in ids:
            continue
        if event.get("freshness_status") == "stale":
            label = "上次审校已过期"
        elif event.get("decision") == "failed" or (
                event.get("completion_receipt") or {}).get("status") == "failed":
            label = "审校未完成"
        elif event.get("decision") == "clean":
            label = "重新审校通过"
        else:
            label = "完成独立审校"
        rows.append({
            "timestamp": _timestamp(event.get("completed_at") or event.get("created_at")
                                    or event.get("stale_at")),
            "label": label,
            "detail": stale_reason_label(event.get("stale_reason"))
            if event.get("freshness_status") == "stale" else "",
        })
    truth = state.get("translation_truth") or {}
    change = truth.get("last_change") or {}
    if segment_id in (change.get("segment_indexes") or []):
        rows.append({
            "timestamp": _timestamp(truth.get("last_changed_at")),
            "label": "译文已修改",
            "detail": "上次审校结果因此需要重新确认",
        })
    rows.sort(key=lambda row: row.get("timestamp") or "", reverse=True)
    return rows[:max(0, int(limit))]
