"""交付状态机、人工处理记录与定点重译。

交付状态：draft -> review_required -> approved/final
- 翻译完成但有 blocking -> review_required；
- draft 资产可下载，但不得显示为最终交付完成；
- 人工处理记录（finding ID / action / note / timestamp）全部落盘；
- 只有 blocking 被解决或明确接受风险后才能进入 final；
- retranslate_segments 抽取自 scripts/fix_segments.py 的能力（不破坏原脚本），
  但现在只生成**候选**：写入正文必须经过 core.adopt_translation_candidate。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import translation_target, workflow_errors

DELIVERY_STATUSES = ("draft", "review_required", "approved", "final")
_HUMAN_ACTIONS = ("human_fixed", "accepted_risk", "retranslated",
                  "approve_final", "bypass_freeze", "preserved")
SEVERITY_LABELS = {
    "blocking": "必须处理",
    "actionable": "建议检查",
    "informational": "仅供参考",
}
SEVERITY_ORDER = {"blocking": 0, "actionable": 1, "informational": 2}
_DETECTED_TEXT_RE = re.compile(r"[「『“\"]([^」』”\"]+)[」』”\"]")
_CATEGORY_LABELS = {
    "semantic_accuracy": "语义准确性",
    "terminology_consistency": "术语一致性",
    "completeness": "完整性",
    "translation_completion": "翻译完成度",
    "format_integrity": "格式完整性",
    "source_language_residue": "源语残留",
    "source_quality": "原文质量",
    "deterministic_qa": "确定性检查",
    "review_finding": "审校发现",
    "fluency": "语言表达",
    "style": "风格一致性",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finding_fingerprint(f: Dict[str, Any]) -> str:
    """Return semantic identity, independent of evidence or render identity."""
    segment_index = f.get("segment_index")
    if segment_index is None:
        segment_index = f.get("segment_id")
    payload = json.dumps({
        "type": f.get("type"),
        "entry_id": f.get("entry_id"),
        "segment_index": segment_index,
        "reason": f.get("reason"),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def finding_id(f: Dict[str, Any], index: Optional[int] = None) -> str:
    """Stable action/render ID: semantic identity plus event/evidence instance."""
    existing = str(f.get("finding_id") or f.get("id") or "")
    event_id = f.get("review_event_id") or f.get("finding_instance_id")
    evidence = sorted(set(_values(f.get("evidence_refs"))
                          + _values(f.get("evidence_ids"))))
    anonymous_instance = {
        "evidence": evidence,
        "detected_text": str(f.get("detected_text") or ""),
        "suggested_target": str(f.get("suggested_target") or ""),
        "review_detail": str(f.get("review_detail") or ""),
    }
    has_anonymous_instance = any(anonymous_instance.values())
    if existing and not event_id and not has_anonymous_instance:
        return existing
    identity = event_id or (json.dumps(anonymous_instance, ensure_ascii=False,
                                       sort_keys=True) if has_anonymous_instance else "")
    payload = json.dumps({
        "fingerprint": finding_fingerprint(f),
        "identity": identity,
        "legacy_id": existing,
    }, ensure_ascii=False, sort_keys=True)
    return "f-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def severity_label(severity: str) -> str:
    return SEVERITY_LABELS.get(str(severity or ""), str(severity or "未知"))


def _segment_index(finding: Dict[str, Any]) -> Optional[int]:
    value = finding.get("segment_index")
    if isinstance(value, bool) or not isinstance(value, int):
        value = finding.get("segment_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _values(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if value is not None and str(value).strip() else []


def _merge_finding(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """Merge same-instance duplicates without dropping evidence metadata."""
    target["duplicate_count"] = (
        int(target.get("duplicate_count") or 1)
        + int(incoming.get("duplicate_count") or 1))
    for key in ("evidence_refs", "evidence_ids"):
        merged = list(dict.fromkeys(_values(target.get(key)) + _values(incoming.get(key))))
        if merged:
            target[key] = merged
    suggested = _values(incoming.get("suggested_target"))
    if suggested:
        all_targets = list(dict.fromkeys(
            _values(target.get("suggested_target"))
            + _values(target.get("suggested_targets")) + suggested))
        target["suggested_target"] = all_targets[0]
        if len(all_targets) > 1:
            target["suggested_targets"] = all_targets
    for key in ("detected_text", "review_detail"):
        if incoming.get(key) and not target.get(key):
            target[key] = incoming[key]
    for key in ("category", "summary", "source_span", "target_span",
                "explanation", "recommendation", "confidence", "detector",
                "diagnostic_version"):
        if incoming.get(key) is not None and not target.get(key):
            target[key] = incoming[key]


def _merge_unresolved_findings(
    findings: Iterable[Dict[str, Any]],
    severities: Iterable[str],
) -> List[Dict[str, Any]]:
    allowed = set(severities)
    out: List[Dict[str, Any]] = []
    positions: Dict[str, int] = {}
    for source in findings:
        if not isinstance(source, dict):
            continue
        f = dict(source)
        if f.get("status") == "stale" and f.get("superseded_by_review_event_id"):
            continue
        if f.get("resolved"):
            continue
        if f.get("severity") not in allowed:
            continue
        fid = finding_id(f)
        if fid not in positions:
            positions[fid] = len(out)
            f["duplicate_count"] = int(f.get("duplicate_count") or 1)
            out.append(f)
            continue
        existing = out[positions[fid]]
        _merge_finding(existing, f)
        if SEVERITY_ORDER.get(f.get("severity"), 99) < \
                SEVERITY_ORDER.get(existing.get("severity"), 99):
            existing["severity"] = f["severity"]
    return out


def is_missing_translation_finding(state: Dict[str, Any], finding: Dict[str, Any]) -> bool:
    """Missing text is a delivery notice, including legacy source-gate rows."""
    index = _segment_index(finding)
    pairs = state.get("pairs") or []
    if index is None or not 0 <= index < len(pairs):
        return False
    pair = pairs[index]
    if not isinstance(pair, dict) or str(pair.get("target") or "").strip():
        return False
    return (finding.get("invariant_code") == "empty_target"
            or finding.get("category") == "completeness"
            or finding.get("type") == "source_quality_gate")


def _delivery_findings(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {**finding, "severity": "actionable"}
        if isinstance(finding, dict) and is_missing_translation_finding(state, finding)
        else finding
        for finding in state.get("findings") or []
    ]


def review_queue_findings(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """所有未解决发现的审查视图（含 informational），同实例合并。"""
    return _merge_unresolved_findings(
        _delivery_findings(state), SEVERITY_LABELS.keys())


def unresolved_findings(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """未解决且需要动作的 findings（blocking / actionable）。"""
    return _merge_unresolved_findings(
        _delivery_findings(state), ("blocking", "actionable"))


def normalize_state_findings(state: Dict[str, Any]) -> Dict[str, Any]:
    """归并同一审校实例的重复记录；保留已解决记录和不同事件实例。"""
    raw = state.get("findings") or []
    if not isinstance(raw, list):
        return state
    merged: List[Dict[str, Any]] = []
    positions: Dict[str, int] = {}
    for source in raw:
        if not isinstance(source, dict) or source.get("resolved") \
                or source.get("severity") not in SEVERITY_LABELS:
            merged.append(source)
            continue
        finding = dict(source)
        fid = finding_id(finding)
        if fid not in positions:
            positions[fid] = len(merged)
            finding["duplicate_count"] = int(finding.get("duplicate_count") or 1)
            merged.append(finding)
            continue
        existing = merged[positions[fid]]
        _merge_finding(existing, finding)
        if SEVERITY_ORDER.get(finding.get("severity"), 99) < \
                SEVERITY_ORDER.get(existing.get("severity"), 99):
            existing["severity"] = finding["severity"]
    # Update the list in place; never rebind it.  `translate_stage` holds
    # `findings_all = state.setdefault("findings", [])` as a document-lifetime
    # alias (core.py:1566), and this function runs from `save_job_state`, which
    # `_commit_translation_batch` calls once per batch.  Rebinding here orphans
    # that alias, so every finding recorded after the first batch is silently
    # dropped from state (while `review_stats`/`has_blocking`, computed from the
    # orphan, keep counting them).
    if isinstance(state.get("findings"), list):
        state["findings"][:] = merged
    else:
        state["findings"] = merged
    return state


def finding_detected_text(finding: Dict[str, Any]) -> str:
    explicit = str(finding.get("detected_text") or "").strip()
    if explicit:
        return explicit
    match = _DETECTED_TEXT_RE.search(str(finding.get("reason") or ""))
    return match.group(1).strip() if match else ""


def is_source_residue_finding(finding: Dict[str, Any]) -> bool:
    reason = str(finding.get("reason") or "")
    return finding.get("kind") == "source_residue" \
        or "残留源语" in reason or "源语片段" in reason


def likely_proper_noun(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", str(text or ""))
    if len(words) < 2:
        return bool(words and words[0][0].isupper() and len(words[0]) >= 3)
    return sum(word[0].isupper() for word in words) >= 2


def finding_context(state: Dict[str, Any], finding: Dict[str, Any]) -> Dict[str, Any]:
    """Build the review card context from persisted translation/evidence state."""
    index = _segment_index(finding)
    event_id = finding.get("review_event_id") or finding.get("finding_instance_id")
    finding_evidence_refs = set(_values(finding.get("evidence_refs")))
    pairs = state.get("pairs") or []
    pair = pairs[index] if index is not None and 0 <= index < len(pairs) else {}
    review_evidence = []
    for entry in state.get("review_evidence") or []:
        # The event test is cheap and selective, so it goes first; the segment
        # test (which stringifies every id) then runs for one entry instead of
        # every review event of the task, for every finding on the review page.
        if not ((event_id and entry.get("review_event_id") == event_id)
                or (not event_id and finding.get("type") == "review"
                    and not entry.get("review_event_id"))):
            continue
        segment_ids = entry.get("segment_ids") or []
        if index in segment_ids or str(index) in {str(x) for x in segment_ids}:
            trace_evidence_ids = [str(item) for item in entry.get("evidence_ids") or []
                                  if str(item) in finding_evidence_refs]
            trace_requests = [dict(request) for request in entry.get("requests") or []
                              if str(request.get("evidence_id")) in finding_evidence_refs]
            review_evidence.append({
                "phase": entry.get("phase"),
                "decision": entry.get("decision"),
                "evidence_ids": trace_evidence_ids,
                "requests": trace_requests,
                "completion_receipt": entry.get("completion_receipt") or {},
                "review_event_id": entry.get("review_event_id"),
            })
    detected = finding_detected_text(finding)
    finding_type = str(finding.get("type") or "")
    category = str(finding.get("category") or "").strip()
    if not category:
        if finding_type in {"review", "semantic"}:
            category = "semantic_accuracy"
        elif finding_type in {"glossary", "glossary_stale", "knowledge_conflict"}:
            category = "terminology_consistency"
        elif finding.get("kind") == "source_residue":
            category = "source_language_residue"
        elif finding_type == "check":
            category = "deterministic_qa"
        else:
            category = finding_type or "review_finding"
    detector = str(finding.get("detector") or "").strip()
    if not detector:
        detector = {
            "review": "Semantic QA", "glossary": "Terminology QA",
            "glossary_stale": "Terminology QA", "knowledge_conflict": "Knowledge QA",
            "check": "Deterministic QA",
        }.get(finding_type, "审校记录")
    evidence_ids = list(dict.fromkeys(_values(finding.get("evidence_refs"))))
    for trace in review_evidence:
        evidence_ids.extend(str(item) for item in trace.get("evidence_ids") or [])
    evidence_ids = list(dict.fromkeys(evidence_ids))
    required_diagnostics = ("summary", "explanation", "recommendation")
    diagnostic_complete = all(str(finding.get(field) or "").strip()
                               for field in required_diagnostics)
    return {
        "finding_id": finding_id(finding),
        "finding_type": finding_type,
        "segment_index": index,
        "segment_number": index + 1 if index is not None else "?",
        "severity": finding.get("severity"),
        "severity_label": severity_label(finding.get("severity")),
        "reason": str(finding.get("reason") or "审校发现问题"),
        "category": category,
        "category_label": _CATEGORY_LABELS.get(category, category),
        "summary": str(finding.get("summary") or "").strip(),
        "source_span": str(finding.get("source_span") or "").strip(),
        "target_span": str(finding.get("target_span") or "").strip(),
        "explanation": str(finding.get("explanation") or "").strip(),
        "recommendation": str(finding.get("recommendation") or "").strip(),
        "confidence": finding.get("confidence"),
        "detector": detector,
        "diagnostic_version": finding.get("diagnostic_version"),
        "diagnostic_complete": diagnostic_complete,
        "legacy_diagnostic": not diagnostic_complete,
        "detected_text": detected,
        "source": str(pair.get("source") or ""),
        "target": str(pair.get("target") or ""),
        "initial_target": str(pair.get("initial_target") or ""),
        "source_residue": is_source_residue_finding(finding),
        "proper_noun_candidate": is_source_residue_finding(finding)
        and likely_proper_noun(detected),
        "evidence_refs": _values(finding.get("evidence_refs")),
        "evidence_ids": evidence_ids,
        "review_evidence": review_evidence,
        "duplicate_count": int(finding.get("duplicate_count") or 1),
        "review_event_id": event_id,
        "translation_core_finding_id": finding.get("finding_id"),
        "finding_status": finding.get("status") or (
            "resolved" if finding.get("resolved") else "open"),
        "stale_reason": finding.get("stale_reason"),
        "requires_human_confirmation": bool(
            finding.get("requires_human_confirmation")),
    }


def unresolved_blocking(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for f in unresolved_findings(state) if f.get("severity") == "blocking"]


def add_human_action(state: Dict[str, Any], finding_id_: str, action: str,
                     note: str = "", actor: str = "user") -> Dict[str, Any]:
    state.setdefault("human_actions", []).append({
        "finding_id": finding_id_,
        "action": action,
        "note": note,
        "timestamp": now_iso(),
        "actor": actor,
    })
    return state


def record_finding_confirmation(state: Dict[str, Any], finding_ids: List[str],
                                note: str = "", actor: str = "user"
                                ) -> Tuple[Dict[str, Any], List[str]]:
    """记录"问题是真的、但还没改"的人工决定，**不**把 finding 标成已解决。

    这是"确认有问题"与"确认已解决"分开的地方：确认存在只是把人的判断写进历史，
    问题仍然待处理，交付门禁也照旧拦着。``mark_findings`` 做不到这一点——它总是
    顺手把 finding 标成 resolved，那会把"我同意这里有错"记成"这里已经修好了"。
    """
    ids = set(finding_ids or [])
    recorded: List[str] = []
    for f in state.get("findings") or []:
        fid = finding_id(f)
        if fid in ids and not f.get("resolved"):
            state.setdefault("human_actions", []).append({
                "finding_id": fid,
                "action": "confirmed_open",
                "record_type": "finding_confirmation",
                "note": note or "确认问题存在，仍待修订",
                "timestamp": now_iso(),
                "actor": actor,
            })
            recorded.append(fid)
    return state, recorded


def mark_findings(state: Dict[str, Any], finding_ids: List[str], action: str,
                  note: str = "", actor: str = "user") -> Tuple[Dict[str, Any], List[str]]:
    """标记指定 findings 为已处理，并写入人工处理记录。返回 (state, marked_ids)。"""
    ids = set(finding_ids or [])
    marked: List[str] = []
    for f in state.get("findings") or []:
        fid = finding_id(f)
        if fid in ids and not f.get("resolved"):
            timestamp = now_iso()
            f["resolved"] = True
            f["resolution"] = {"action": action, "note": note,
                               "timestamp": timestamp, "actor": actor}
            audit = {
                "finding_id": fid,
                "action": action,
                "note": note,
                "timestamp": timestamp,
                "actor": actor,
            }
            if action == "accepted_risk" and f.get("type") == "review" \
                    and f.get("input_fingerprint"):
                audit.update({
                    "record_type": "delivery_risk_acceptance",
                    "actor_type": "human",
                    "translation_core_finding_id": f.get("finding_id"),
                    "review_event_id": f.get("review_event_id"),
                    "input_fingerprint": f.get("input_fingerprint"),
                    "status": "current",
                })
            state.setdefault("human_actions", []).append(audit)
            marked.append(fid)
    return state, marked


def compute_delivery_status(state: Dict[str, Any]) -> str:
    """由当前状态推导交付状态；final/approved 只能由人工确认产生。"""
    if not state.get("p2_done"):
        return "draft"
    from .translation_evidence import translation_review_readiness
    if not translation_review_readiness(state)["ready"]:
        return "review_required"
    if unresolved_blocking(state):
        return "review_required"
    current = state.get("delivery_status")
    if current in ("approved", "final"):
        return current
    return "draft"


def unrecovered_scan_pages(state: Dict[str, Any]) -> List[int]:
    """扫描书里读失败、至今没有补读成功的页（页码从 1 起）。

    这些页的原文不在 ``pairs`` 里：译稿缺页，不能冻结交付，也不能「接受风险」跳过。
    """
    ingest = state.get("streaming_ingest")
    rows = ingest.get("failed_pages") if isinstance(ingest, dict) else None
    pages = {int(row["page"]) for row in rows or []
             if isinstance(row, dict) and str(row.get("page") or "").isdigit()}
    return sorted(pages)


def scan_pages_message(pages: List[int]) -> str:
    shown = "、".join(str(page) for page in pages[:8]) + ("…" if len(pages) > 8 else "")
    return (f"第 {shown} 页识别失败，原文缺失（共 {len(pages)} 页）；"
            "请在「检查与修复」补读失败页后再交付")


def report_ready(state: Dict[str, Any]) -> bool:
    """A requested academic report must pass its final report gate."""
    if not state.get("report_enabled", False):
        return True
    academic = state.get("academic_state") or {}
    return (state.get("report_status") or academic.get("report_status")) == "generated"


def approve_delivery(state: Dict[str, Any], note: str = "", actor: str = "user",
                     accept_blocking: bool = False) -> Tuple[Dict[str, Any], bool, List[str]]:
    """人工交付确认 -> final。

    - 存在未解决 blocking 且未接受风险 -> 拒绝进入 final，返回错误说明；
    - accept_blocking=True：把所有未解决 blocking 记录为 accepted_risk 后进入 final；
    - 全部人工处理记录保存到 state["human_actions"]。
    """
    if not state.get("p2_done"):
        return state, False, ["翻译尚未完成，不能创建最终交付版本"]
    missing_pages = unrecovered_scan_pages(state)
    if missing_pages:
        return state, False, [scan_pages_message(missing_pages)]
    if not report_ready(state):
        return state, False, ["实践报告尚未完成或未通过校验，不能创建最终交付版本"]
    from .translation_evidence import translation_review_readiness
    review = translation_review_readiness(state)
    can_accept_current_review_risk = (
        accept_blocking
        and review["status"] == "missing"
        and review["blocking_finding_ids"]
        and not review["missing_segment_ids"]
        and not review["stale_segment_ids"]
        and not review["failed_segment_ids"]
        and all(item.get("status") == "missing"
                for item in review["decision_errors"])
    )
    if not review["ready"] and not can_accept_current_review_risk:
        return state, False, [
            f"Translation Core review gate={review['status']}，不能进入 final"]
    target_report = translation_target.validate_translation_pairs(
        state.get("pairs") or [], allow_empty=True)
    pair_count_mismatch = bool(
        state.get("paras") and len(state.get("pairs") or []) != len(state.get("paras") or [])
    )
    if target_report["blocking"] or pair_count_mismatch:
        existing = {
            (item.get("type"), item.get("segment_index"), item.get("invariant_code"))
            for item in state.setdefault("findings", [])
        }
        for finding in translation_target.target_invariant_findings(target_report):
            key = (finding.get("type"), finding.get("segment_index"),
                   finding.get("invariant_code"))
            if key not in existing:
                state["findings"].append(finding)
        if pair_count_mismatch and not any(
                item.get("type") == "delivery_invariant"
                and item.get("invariant_code") == "pair_count_mismatch"
                for item in state["findings"] if isinstance(item, dict)):
            state["findings"].append({
                "type": "delivery_invariant", "severity": "blocking",
                "category": "format_integrity", "segment_index": None,
                "segment_id": None, "invariant_code": "pair_count_mismatch",
                "summary": "双语 pairs 数量与源文段落数量不一致",
                "reason": "双语 pairs 数量与源文段落数量不一致，不能进入最终交付",
                "detector": "Translation Target Invariant",
            })
        errors = [
            item.get("message", "译文未通过 Translation Target Invariant")
            for item in target_report.get("issues") or []
        ]
        if pair_count_mismatch:
            errors.append("双语 pairs 数量与源文段落数量不一致，不能进入最终交付")
        return state, False, errors
    blockers = unresolved_blocking(state)
    if blockers:
        if not accept_blocking:
            ids = "、".join(finding_id(f) for f in blockers)
            return state, False, [f"存在未解决的 blocking 问题，不能进入 final：{ids}"]
        for f in blockers:
            state, _ = mark_findings(state, [finding_id(f)], "accepted_risk",
                                     note or "接受风险（人工确认）", actor)
    state["delivery_status"] = "final"
    state["stage"] = "FINAL"
    # Delivery approval is document-scoped authority.  It does not prove that
    # the user inspected and accepted every segment, so segment-level
    # human_accepted fields remain unchanged.
    state["delivery_approved_by_human"] = True
    state["delivery_approval"] = {
        "timestamp": now_iso(), "actor": actor, "note": note,
    }
    add_human_action(state, "*delivery*", "approve_final", note, actor)
    return state, True, []


def _retranslation_request(state: Dict[str, Any], index: int,
                           target_lang: str) -> Dict[str, Any]:
    """Capture what one model call needs, before the call leaves the lock.

    The baseline is captured *now*, not after the response: that is what lets a
    late result be recognised as stale instead of being written over text the
    user edited while the model was thinking.
    """
    from . import translation_candidates as _candidates

    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    pair = pairs[index]
    return {
        "segment_index": index,
        "segment_uid": str(pair.get("segment_uid") or ""),
        "source": str(pair.get("source") or ""),
        "baseline": _candidates.pair_signature(pair),
        "truth_version": int((state.get("translation_truth") or {}).get("version") or 0),
        "context_prev": paras[max(0, index - 2):index],
        "context_next": paras[index + 1:index + 3],
    }


def _generate_candidate_text(request: Dict[str, Any], provider: str, api_key: str,
                             model: str, target_lang: str, glossary_text: str,
                             style_rules: str, glossary: List[Dict[str, Any]],
                             section_profile: Any) -> Dict[str, Any]:
    """One model call plus the existing integrity pass; returns text and counts.

    Nothing here touches the task state.  The completeness checks and the single
    repair round still run -- on the *candidate*, so the preview can say "this
    candidate still has a blocking problem" without rewriting anything.
    """
    import core

    source = request["source"]
    target = core.translate_batch(
        [source], request["context_prev"], request["context_next"], glossary_text,
        style_rules, target_lang, provider, api_key, model)[0]
    target = core.clean_xml_chars(target).replace("\n", " ")
    findings = core.check_translation_batch(
        [source], [target], glossary, target_lang, section_profile=section_profile)
    fixable = [item for item in findings
               if item.get("severity") in ("blocking", "actionable")]
    if fixable:
        repaired = core.repair_batch([source], [target], fixable, glossary_text,
                                     style_rules, target_lang, provider,
                                     api_key, model)
        if repaired and repaired[0].strip() \
                and not core.is_incomplete_translation(source, repaired[0]):
            target = core.clean_xml_chars(repaired[0]).replace("\n", " ")
    remaining = core.check_translation_batch(
        [source], [target], glossary, target_lang, section_profile=section_profile)
    return {
        "candidate_target": target,
        "checks": {
            "blocking": sum(1 for item in remaining
                            if item.get("severity") == "blocking"),
            "actionable": sum(1 for item in remaining
                              if item.get("severity") == "actionable"),
            "informational": sum(1 for item in remaining
                                 if item.get("severity") == "informational"),
        },
    }


def retranslate_segments(
    job_id: str,
    indexes: List[int],
    provider: str,
    api_key: str,
    model: str,
    target_lang: str,
    style_rules: str = "",
    glossary: Optional[List[Dict[str, Any]]] = None,
    on_status=None,
    on_caption=None,
    actor: str = "user",
    base_url: str = "",
    instruction: str = "",
    origin: str = "",
    finding_ids: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[int]]:
    """Generate retranslation **candidates** for the given segments.

    This used to overwrite ``pairs[i]["target"]``, rewrite ``initial_target``,
    clear the human-acceptance markers and close the old findings -- one model
    call destroyed work a human had done.  Generation is now separated from
    commit:

    1. read the authoritative state and capture a baseline per segment (no lock);
    2. call the model and run the integrity checks (no lock -- a network call
       must never hold the task transaction open);
    3. persist the results as candidates in one short transaction, together
       with any rejection (the segment moved, was excluded, disappeared).

    The working translation, the review state, the findings, TM eligibility and
    the delivery gate are all unchanged by this call.  Only
    ``core.adopt_translation_candidate`` turns a candidate into text.

    ``base_url`` is accepted for compatibility with the caller that sets the
    thread-local endpoint; the value itself is already applied there.
    """
    import core
    from . import translation_candidates as _candidates

    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    pairs = state.get("pairs") or []
    indexes = sorted({int(i) for i in indexes if 0 <= int(i) < len(pairs)})
    if not indexes:
        return state, []
    source_gate = core._audit_source_quality_for_state(state)
    blocked_source_indexes = {
        item.get("segment_index") for item in source_gate.get("flags") or []
    }
    still_blocked = [index for index in indexes if index in blocked_source_indexes]
    if still_blocked:
        numbers = "、".join(str(index + 1) for index in still_blocked)
        raise ValueError(f"第 {numbers} 段原文尚未核对，不能重译")

    glossary = core.normalize_glossary(
        glossary if glossary is not None else state.get("glossary") or [])
    glossary_text = core.glossary_block(glossary)
    requests = [_retranslation_request(state, index, target_lang) for index in indexes]

    drafts: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    stopped = None
    for request in requests:
        index = request["segment_index"]
        try:
            section_profile = core._batch_section_profile(
                state.get("document_profile"), index, 1)
            result = _generate_candidate_text(
                request, provider, api_key, model, target_lang, glossary_text,
                style_rules, glossary, section_profile)
            if not str(result["candidate_target"] or "").strip():
                raise ValueError("模型没有返回译文")
            drafts.append({
                **request,
                "candidate_target": result["candidate_target"],
                "checks": result["checks"],
                "origin": origin or _candidates.ORIGIN_TARGETED,
                "instruction": instruction,
                "provider": provider,
                "model": model,
                "usage": {"unknown": True},
                "finding_ids": list(finding_ids or []),
            })
            if on_caption:
                on_caption(
                    f"段 {index + 1} 已生成候选（{len(request['source'])} -> "
                    f"{len(result['candidate_target'])} 字符，尚未写入译文）")
        except Exception as e:  # noqa: BLE001 - one bad segment must not sink the batch
            if workflow_errors.fatal_reason(e):
                stopped = e
                break
            failures.append({"segment_index": index, "error": str(e)})
            if on_caption:
                on_caption(f"段 {index + 1} 生成候选失败：{str(e)[:120]}")

    created: List[int] = []
    if drafts:
        committed = core.commit_candidates(job_id, drafts, actor=actor)
        for view in committed.get("created") or []:
            # A candidate that was already stale when it landed is reported, but
            # it is not "created for this segment" from the caller's point of view.
            if view.get("status") != _candidates.STATUS_PENDING:
                if on_caption:
                    on_caption(
                        f"段 {int(view.get('segment_index') or 0) + 1} 的候选已过期："
                        f"{view.get('note') or ''}")
                continue
            created.append(int(view.get("segment_index") or 0))
        for rejected in committed.get("rejected") or []:
            failures.append({"segment_index": rejected.get("segment_index"),
                             "error": _candidates.CONFLICT_REASONS.get(
                                 str(rejected.get("reason") or ""),
                                 str(rejected.get("reason") or "候选未能保存"))})
        state = committed.get("state") or core.load_job_state(job_id) or state

    if stopped is not None:
        workflow_errors.raise_if_fatal(stopped, job_id)
    if failures and not created:
        # A run where nothing was produced must say so: otherwise the caller
        # sees created == [] and cannot tell it from "nothing to retranslate".
        raise ValueError(
            "重译没有生成候选：" + "；".join(
                f"第 {(item['segment_index'] or 0) + 1} 段 {item['error'][:160]}"
                for item in failures))
    if failures and on_caption:
        on_caption(f"{len(failures)} 段没有生成候选，其余已保存为待采用")
    return core.load_job_state(job_id) or state, created
