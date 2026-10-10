"""Case-review projection for the workspace (UI-framework neutral).

The academic report picks a handful of real revision cases (and, when no honest
initial translation exists, a labelled *simulated* baseline).  Each case needs a
person's explicit sign-off before it may be cited; an approval is void once the
translation it was based on changes.  This module only reshapes artifacts the core
already owns into per-case views and labels — every decision still goes through
``core.review_academic_case`` / ``update_synthetic_baseline`` / ``replace_rejected_case``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from folith import case_provenance as _case_provenance
from folith import finalization as _finalization

ORIGIN_FILTERS = ("全部", "真实修订", "合成对照", "翻译决策")
STATUS_FILTERS = ("全部", "待人工确认", "已批准纳入", "已排除")
SEVERITY_LABELS = {"blocking": "必须处理", "actionable": "建议", "informational": "参考"}

_STATE_LABELS = {
    "pass": "已通过", "fail": "未通过", "manual_review": "待人工复核",
    "not_checked": "未检查", "not_applicable": "不适用",
    "not_available": "尚未生成", "unreviewed": "未处理",
    "approved": "已确认", "rejected": "已拒绝", "modified": "已修改", "other": "其他",
}


def origin_label(case: Mapping[str, Any]) -> str:
    return _case_provenance.display_contract(case).get("origin_label") or "未分类案例"


def review_status_label(status: Any) -> str:
    value = str(status or "")
    return {"unreviewed": "待人工确认", "approved": "已批准纳入", "rejected": "已排除"}.get(
        value, "需人工确认" if value else "待人工确认")


def identity_label(case: Mapping[str, Any]) -> str:
    raw = str(case.get("case_id") or "")
    index = case.get("segment_index")
    if raw.startswith(("seg-", "LSC-", "SC-")):
        if isinstance(index, int):
            return f"第 {index + 1} 段"
        return "真实修订案例" if raw.startswith("seg-") else "案例"
    return "案例" if raw else "未命名案例"


def state_label(value: Any) -> str:
    return _STATE_LABELS.get(str(value or ""), "需确认" if value else "—")


def is_stale(case: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    case_id = str(case.get("case_id") or "")
    if bool(case.get("content_stale")):
        return True
    artifact = ((state.get("academic_state") or {}).get("artifacts") or {}).get(f"case:{case_id}") or {}
    if artifact.get("status") == "stale":
        return True
    impact = state.get("dependency_impact") or {}
    # An author's own case decision also marks every case "affected" (the report
    # prose downstream must be reassembled), but that is not a reason to doubt the
    # decision itself.  Only an impact caused by changed *translation* segments
    # voids an earlier approval.
    translation_changed = bool(impact.get("changed_segment_ids")
                               or impact.get("changed_segment_indexes"))
    return translation_changed and case_id in {
        str(v) for v in impact.get("affected_case_ids") or []}


def validity(case: Mapping[str, Any], state: Mapping[str, Any]) -> tuple[str, str]:
    if is_stale(case, state):
        return "需要重新检查", "stale"
    if case.get("review_status") == "rejected" or case.get("baseline_status") == "rejected":
        return "已排除", "stale"
    if case.get("review_status") == "unreviewed":
        return "待人工确认", "pending"
    return "可复用", "valid"


def case_views(job_id: str, state: Mapping[str, Any],
               artifacts: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """One view per selected case, with its evidence gathered from the artifacts."""
    selected = artifacts.get("selected_cases") or {}
    project_evidence = artifacts.get("evidence") or {}
    argument_plan = artifacts.get("argument_plan") or {}
    plans = artifacts.get("case_analysis_plans") or {}
    outline = artifacts.get("outline") or {}
    literature = artifacts.get("literature_sources") or {}
    evidence_segments = ((project_evidence.get("project_evidence") or {}).get("segments") or [])
    sources = {str(i.get("source_id")): i for i in literature.get("sources") or []
               if isinstance(i, dict) and i.get("source_id")}
    glossary = [i for i in state.get("glossary") or [] if isinstance(i, dict)]
    findings = [i for i in state.get("findings") or [] if isinstance(i, dict)]
    plan_index = {str(i.get("case_id")): i for i in plans.get("plans") or [] if i.get("case_id")}
    human_by_case: Dict[str, list] = {}
    for entry in state.get("human_evidence") or []:
        human_by_case.setdefault(str(entry.get("case_id")), []).append(entry)
    pairs = state.get("pairs") or []

    views: List[Dict[str, Any]] = []
    for case in selected.get("cases") or []:
        if not isinstance(case, dict):
            continue
        view = _finalization.case_review_view(case, state, job_id)
        case_id = str(view.get("case_id") or "")
        segment_index = view.get("segment_index")
        segment_id = str(view.get("segment_id") or "")
        evidence_segment = next((i for i in evidence_segments
                                 if str(i.get("segment_id") or "") == segment_id
                                 or i.get("segment_index") == segment_index), {})
        process = evidence_segment.get("process_evidence") or {}
        related_terms = list(process.get("terminology_decisions") or [])
        if not related_terms:
            haystack = f'{view.get("source_text") or ""}\n{view.get("current_text") or ""}'.casefold()
            related_terms = [i for i in glossary if str(i.get("source") or "").casefold() in haystack]
        case_findings = [i for i in findings if i.get("segment_index") == segment_index]
        for item in process.get("findings") or []:
            if item not in case_findings:
                case_findings.append(item)
        claims = []
        for claim in argument_plan.get("claims") or []:
            if (case_id in {str(i) for i in claim.get("core_case_ids") or []}
                    or case_id in {str(i) for i in claim.get("supports_cases") or []}
                    or segment_id in {str(i) for i in claim.get("project_evidence") or []}):
                claims.append(claim)
        literature_ids: set = set()
        for claim in claims:
            for key in ("literature_evidence", "literature_claims", "literature_sources"):
                literature_ids.update(str(i) for i in claim.get(key) or [])
        commentary = []
        for label, value in (
                ("针对问题", view.get("targeted_issue")),
                ("选择理由", view.get("selection_rationale")),
                ("差异说明", view.get("contrast_rationale")),
                ("分析理由", (view.get("synthetic_evidence") or {}).get("academic_analysis_reason")),
                ("分析种子", view.get("legacy_analysis_seed")),
                ("限制", "；".join(str(i) for i in view.get("limitations") or [])),
        ):
            if value:
                commentary.append({"label": label, "value": value})
        target_subsection = str(view.get("target_subsection") or "").strip()
        section_title = ""
        for section in outline.get("sections") or []:
            section_id = str(section.get("section_id") or "")
            if section_id == str(view.get("section_id") or "") or (
                    target_subsection and target_subsection.startswith(section_id + ".")):
                section_title = str(section.get("title") or "")
                break
        context = view.get("focus") or {}
        before = str(context.get("context_before") or "").strip()
        after = str(context.get("context_after") or "").strip()
        if not before and isinstance(segment_index, int) and 0 < segment_index <= len(pairs):
            before = str(pairs[segment_index - 1].get("target") or "")
        if not after and isinstance(segment_index, int) and 0 <= segment_index + 1 < len(pairs):
            after = str(pairs[segment_index + 1].get("target") or "")
        view.update({
            "case_plan": plan_index.get(case_id) or {},
            "human_evidence": human_by_case.get(case_id) or [],
            "related_terms": related_terms[:12],
            "case_findings": case_findings[:12],
            "related_claims": claims[:8],
            "literature_evidence": [sources[i] for i in sorted(literature_ids) if i in sources],
            "analytical_commentary": commentary[:8],
            "target_subsection": target_subsection,
            "section_title": section_title,
            "context_before": before,
            "context_after": after,
        })
        view["origin_label"] = origin_label(view)
        view["status_label"] = review_status_label(view.get("review_status"))
        view["identity_label"] = identity_label(view)
        validity_text, tone = validity(view, state)
        view["validity_label"], view["validity_tone"] = validity_text, tone
        view["stale"] = tone == "stale" and is_stale(view, state)
        views.append(view)
    return views


def filter_views(views: List[Dict[str, Any]], origin: str = "全部", status: str = "全部",
                 query: str = "") -> List[Dict[str, Any]]:
    needle = str(query or "").strip().casefold()
    out = []
    for item in views:
        if origin != "全部" and item["origin_label"] != origin:
            continue
        if status != "全部" and item["status_label"] != status:
            continue
        if needle and needle not in " ".join(str(v or "") for v in (
                item["identity_label"], item["origin_label"], item["status_label"],
                item.get("source_text"), item.get("current_text"),
                item.get("target_subsection"), item.get("section_title"))).casefold():
            continue
        out.append(item)
    return out


def evidence_rows(item: Mapping[str, Any]) -> List[tuple[str, str]]:
    """The analysis/evidence grid shown next to a case."""
    evidence = item.get("synthetic_evidence") or {}
    analysis = item.get("analysis_fields") or {}
    synthetic = item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE
    rows = [
        ("基线合理性", state_label(evidence.get("baseline_plausibility"))),
        ("实质差异", state_label(evidence.get("material_difference"))),
        ("修复正确性", state_label(evidence.get("repair_correctness"))),
        ("分析价值", state_label(evidence.get("academic_analysis_value"))),
        ("基线状态", {"modified": "已修改", "rejected": "已拒绝", "approved": "已确认",
                  "unreviewed": "未处理"}.get(item.get("baseline_status"), "不适用")
         if synthetic else "不适用"),
        ("分析状态", "已保存" if analysis else "未提供"),
    ]
    plan = item.get("case_plan") or {}
    if plan:
        rows.extend([
            ("问题类型", state_label((plan.get("problem") or {}).get("type"))),
            ("决策理由", "已保存" if plan.get("decision_rationale") else "未提供"),
            ("理论映射", "已保存" if plan.get("theory_mapping") else "未提供"),
        ])
    return rows


def counts(views: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(views),
        "approved": sum(v.get("review_status") == "approved" for v in views),
        "rejected": sum(v.get("review_status") == "rejected" for v in views),
        "pending": sum(v.get("review_status") not in ("approved", "rejected") for v in views),
    }
