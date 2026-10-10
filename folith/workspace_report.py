"""Report-page projection for the workspace (UI-framework neutral).

Ported from the Streamlit report page so the Qt client shows the same decision
hierarchy: one overall status, the issues grouped by what a person has to do about
them (cases, literature, citations, statistics, template, sections, human input,
and any open translation-review findings), and the cleaned report draft.  Nothing
here decides quality — it only reshapes artifacts and runtime facts the core owns.
The page-level facts that live outside the artifacts (runtime view, the canonical
delivery label, the review queue) are passed in rather than imported, so this module
stays free of the core and of any UI framework.
"""
from __future__ import annotations

import re

from folith import academic_validator, finalization, report_template


_REPORT_CASE_ISSUES = {
    "case_count_status_mismatch", "insufficient_core_revision_cases",
    "invalid_selected_case", "non_revision_case_used_as_revision_analysis",
    "synthetic_pipeline_unavailable", "synthetic_only_without_eligible_cases",
    "ineligible_synthetic_case_selected", "synthetic_case_provenance_mismatch",
    "duplicate_selected_case_presentation",
    "case_presentation_count_mismatch",
    "case_minimum_not_met", "case_coverage_below_recommended",
    "duplicate_canonical_case", "missing_focus_span",
    "focus_span_outside_canonical", "focus_excerpt_excessively_long",
    "full_segment_rendered_as_case", "case_node_focus_mismatch",
    "case_numbering_not_continuous", "case_hierarchy_missing",
    "case_distribution_severely_unbalanced",
    "research_question_case_coverage_insufficient",
    "case_presentation_contract_violation",
}


_REPORT_SECTION_ISSUES = {
    "missing_required_section", "section_too_short", "missing_planned_claim",
    "missing_research_question_link", "missing_selected_case",
    "missing_planned_literature_claim", "missing_planned_literature_evidence",
    "section_literature_outside_plan", "section_literature_claim_outside_plan",
    "section_literature_evidence_outside_plan", "section_literature_source_outside_plan",
}


_REPORT_STATISTIC_ISSUES = {
    "unknown_project_statistic", "wrong_project_statistic",
    "unresolved_statistic_token", "unmarked_project_statistic",
}


_REPORT_TEMPLATE_ISSUES = {
    "template_chapter_count_mismatch", "template_missing_chapter",
    "template_chapter_order_mismatch", "template_chapter_title_mismatch",
    "template_missing_subsection", "template_subsection_level_mismatch",
    "template_subsection_order_mismatch", "template_extra_subsection",
    "template_extra_chapter", "template_hash_mismatch", "template_matter_mismatch",
    "template_case_role_missing", "template_case_role_mismatch",
    "template_case_mapping_mismatch", "template_case_minimum_not_met",
    "template_front_matter_content_missing", "template_internal_id_visible",
    "template_unresolved_marker", "template_duplicate_rendering",
    "template_duplicate_heading",
}


def quality_label(status):
    return {
        "pass": "已验证", "pass_with_warnings": "已验证 · 有警告",
        "review_required": "需要复核", "fail": "需要复核",
        "literature_required": "需要补充文献",
        "failed": "生成失败", "not_started": "未生成",
        "stale": "需要更新（按影响范围）", "in_progress": "生成中",
    }.get(status, "—")


def validation_label(status):
    return {
        "pass": "通过", "pass_with_warnings": "通过 · 有警告",
        "fail": "需要复核", "review_required": "需要复核",
        "not_configured": "未配置模板",
    }.get(status, "未生成")


def literature_status(artifacts):
    sources = (artifacts.get("literature_sources") or {}).get("sources") or []
    evidence = (artifacts.get("literature_evidence") or {}).get("items") or []
    claims = (artifacts.get("literature_claims") or {}).get("items") or []
    if not sources:
        return "尚未建立"
    if not evidence or not claims:
        return "待补证据"
    if any(item.get("evidence_grounded_status") in {"needs_review", "review_required"}
           for item in claims):
        return "需要复核"
    return "已登记"


def _report_heading_title(value):
    value = re.sub(r"\s+#+\s*$", "", str(value or "")).strip()
    return value


def _report_heading_key(value):
    value = _report_heading_title(value).casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[.、．]?\s*", "", value)
    return re.sub(r"\s+", "", value)


def report_headings(report):
    headings = []
    for line_index, line in enumerate(str(report or "").splitlines()):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = _report_heading_title(match.group(2))
        if not title:
            continue
        headings.append({
            "line_index": line_index,
            "level": len(match.group(1)),
            "title": title,
            "anchor": f"report-heading-{len(headings) + 1}",
        })
    return headings


def clean_report_for_display(report_md):
    """Hide provenance and collapse adjacent duplicate headings in the reader."""
    text = str(report_md or "")
    text = re.sub(r"\\?<!--.*?-->\\?", "", text, flags=re.DOTALL)
    text = re.sub(r"\\?\{\{TERM:[^}]+\}\}\\?", "", text)
    text = re.sub(r"\b(?:(?:seg|claim|rq|lit-claim|lit-evidence|human-ev)-"
                  r"[A-Za-z0-9_.:-]+|(?:AQ|AV|AR|LR)-\d+)\b",
                  "对应证据", text)
    # These two shapes are reachable in ordinary legacy/generated reports:
    # the analysis label can leak a quote marker, and a bold numbered
    # subsection can be glued to the preceding paragraph.  Repair only those
    # exact forms; ordinary blockquotes and bold prose remain untouched.
    text = re.sub(
        r"((?:\*{0,2}分析\*{0,2})\s*[：:]\s*)>\s*[。．]\s*",
        r"\1", text)
    text = re.sub(
        r"(?P<lead>[。！？])\s*(?P<title>\d+(?:\.\d+)+\s+[^。\n*]{2,100})"
        r"\*{2}\s*[。！？]",
        r"\g<lead>\n\n### \g<title>\n\n", text)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            title = _report_heading_title(match.group(2))
            # The toolbar is the single report-title source.
            if not any(value.strip() for value in cleaned) and \
                    title.casefold().startswith("翻译实践报告"):
                continue
            previous = next((value for value in reversed(cleaned) if value.strip()), "")
            previous_match = re.match(r"^#{1,6}\s+(.+?)\s*$", previous)
            if previous_match and _report_heading_key(previous_match.group(1)) == \
                    _report_heading_key(title):
                continue
        cleaned.append(line.rstrip())
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return report_template.anonymize_sensitive_institutions(text.strip())


def issue_category(issue):
    issue_type = str(issue.get("type") or "")
    if issue_type in _REPORT_CASE_ISSUES:
        return "案例不足"
    if issue_type in _REPORT_STATISTIC_ISSUES:
        return "统计验证失败"
    if issue_type in _REPORT_TEMPLATE_ISSUES:
        return "模板合规"
    if "citation" in issue_type or issue_type in {
            "unregistered_formal_citation", "uncitable_literature_source"}:
        return "引用需要确认"
    if "literature" in issue_type or "grounding" in issue_type:
        return "文献证据缺失"
    if issue_type in _REPORT_SECTION_ISSUES or issue.get("section_id"):
        return "章节需要重新生成"
    if "human" in issue_type:
        return "需要人工补充"
    return "需要人工补充"


def issue_detail(category, issue):
    if category == "案例不足":
        return "项目中可追溯、符合资格的案例数量或案例选择状态需要确认。"
    if category == "文献证据缺失":
        return "文献来源、逐字证据与论点之间的支持链路尚未完整。"
    if category == "引用需要确认":
        return "存在需要核对的引用来源或作者—年份信息。"
    if category == "统计验证失败":
        return "报告中的项目统计与证据库不一致，或统计来源未能解析。"
    if category == "模板合规":
        return "报告与上传模板的章节、标题、层级或角色不一致。"
    if category == "章节需要重新生成":
        section_id = issue.get("section_id")
        return f"第 {section_id} 节存在结构或论证问题，建议定点重新生成。" \
            if section_id else "报告章节存在结构或论证问题，建议重新生成。"
    if "human" in str(issue.get("type") or ""):
        return "有需要作者确认或补充的项目过程信息。"
    return "报告质量结果仍需人工核对后再作为最终学术结论使用。"


def issue_groups(artifacts, academic):
    groups = {}

    def add(category, detail, section_id=None):
        group = groups.setdefault(category, {"category": category, "details": [],
                                             "sections": set()})
        if detail and detail not in group["details"]:
            group["details"].append(detail)
        if section_id:
            group["sections"].add(str(section_id))

    selected = artifacts.get("selected_cases") or {}
    policy = selected.get("authentic_selection_status") or \
        (selected.get("case_count_policy") or {}).get("status")
    if policy == "insufficient_revision_cases":
        add("案例不足", "符合资格的真实修订案例少于当前研究要求。")

    validation = artifacts.get("validation") or {}
    review = artifacts.get("review") or {}
    literature_review = artifacts.get("literature_support_review") or {}
    quality = artifacts.get("academic_quality") or {}
    final_docx_validation = artifacts.get("final_docx_validation") or {}
    for issue in list(validation.get("issues") or []) \
            + list(review.get("issues") or []) \
            + list(literature_review.get("issues") or []) \
            + list(quality.get("findings") or []) \
            + list(final_docx_validation.get("issues") or []):
        category = issue_category(issue)
        add(category, issue_detail(category, issue), issue.get("section_id"))

    # A legacy/fixture report without a structured report artifact has no
    # literature contract to enforce.  Once the academic pipeline has emitted
    # the report artifact, its explicit literature status is authoritative.
    report_artifact = artifacts.get("report") or {}
    if report_artifact and not (artifacts.get("literature_sources") or {}).get("sources"):
        add("文献证据缺失", "正文结构与案例已完成，但学术文献支持尚未建立。")

    if academic_validator.citation_validation_status(
            validation, artifacts.get("literature_sources"),
            artifacts.get("literature_evidence"), artifacts.get("literature_claims")) \
            == "evidence_missing" and \
            (artifacts.get("literature_sources") or {}).get("sources"):
        add("文献证据缺失", "已登记文献来源，但逐字证据或文献主张尚未完整。")

    dimensions = academic.get("quality_dimensions") or {}
    if dimensions.get("literature_grounding") in {"review_required", "fail"}:
        add("文献证据缺失", "文献证据的可核验性仍需处理。")
    if dimensions.get("citation_validation") in {"review_required", "fail"}:
        add("引用需要确认", "引用完整性检查未通过。")
    if dimensions.get("statistics_validation") in {"review_required", "fail"}:
        add("统计验证失败", "统计一致性检查未通过。")
    human_status = academic.get("human_evidence_status") or {}
    if human_status.get("unanswered") or human_status.get("critical_questions"):
        add("需要人工补充", "仍有未回答的人类证据问题。")
    if (academic.get("quality_status") or academic.get("status")) in {
            "review_required", "fail", "failed"} and not groups:
        add("需要人工补充", "报告质量状态仍要求人工复核。")

    ordered = []
    for category in ("模板合规", "案例不足", "文献证据缺失", "引用需要确认",
                     "统计验证失败", "章节需要重新生成", "需要人工补充"):
        if category not in groups:
            continue
        group = groups[category]
        sections = sorted(group["sections"], key=str)
        if category == "模板合规":
            action = "处理模板问题"
        elif category == "案例不足":
            action = "处理案例"
        elif category == "文献证据缺失":
            action = "处理文献证据"
        elif category == "引用需要确认":
            action = "检查引用"
        elif category == "统计验证失败":
            action = "检查统计"
        elif category == "章节需要重新生成":
            action = "重新生成章节"
        else:
            action = "回答人工问题"
        group["action"] = action
        group["target"] = "detail"
        ordered.append(group)
    return ordered


def _report_issue_level(category):
    if category == "需要人工补充":
        return "human_review"
    if category in {"章节需要重新生成", "引用需要确认"}:
        return "warning"
    return "blocker"


def _report_issue_impact(group):
    category = group["category"]
    sections = sorted(group.get("sections") or [], key=str)
    if group.get("target") == "review":
        return (f"涉及 {len(sections)} 个段落，完成处理后才能继续最终交付。"
                if sections else "翻译审校队列仍有未关闭发现。")
    if category == "案例不足":
        return "案例分析未满足数量或可追溯性要求，报告不能最终交付。"
    if category == "统计验证失败":
        return "项目统计与证据不一致，最终稿不能通过验证。"
    if category == "模板合规":
        return "章节或版式未满足模板约束，DOCX 交付可能被阻止。"
    if category == "文献证据缺失":
        return "论点缺少可核验支持，报告不能作为最终学术成果交付。"
    if category == "引用需要确认":
        return "引用信息仍需核对，可能影响稿件可信度。"
    if category == "章节需要重新生成":
        location = "、".join(f"第 {value} 节" for value in sections)
        return f"{location or '部分章节'}的结构或证据需要修正。"
    return "仍有内容依赖作者判断或项目经历补充。"


def _report_issue_recommendation(category):
    return {
        "模板合规": "查看验证结果，按模板约束修正报告。",
        "案例不足": "查看案例选择结果，补足合格案例。",
        "文献证据缺失": "检查来源与证据链，补齐文献支持。",
        "引用需要确认": "核对引用来源和作者—年份信息。",
        "统计验证失败": "查看不一致项，重新验证项目统计。",
        "章节需要重新生成": "定位受影响章节，仅重新生成问题部分。",
        "需要人工补充": "回答待补充问题，再继续生成受影响内容。",
    }[category]


def review_issue_groups(contexts):
    """Bring translation delivery findings into the report Issues view."""
    groups = {}
    for context in contexts:
        if context.get("severity") not in {"blocking", "actionable"}:
            continue
        category = context.get("category_label") or "翻译审校"
        severity = "blocker" if context.get("severity") == "blocking" else "warning"
        group = groups.setdefault(category, {
            "category": category,
            "details": [],
            "sections": set(),
            "severity": severity,
            "level": severity,
            "action": "处理审校",
            "target": "review",
            "finding_ids": [],
        })
        if severity == "blocker":
            group["severity"] = group["level"] = "blocker"
        reason = context.get("summary") or context.get("reason") or "存在待处理的翻译审校发现。"
        if reason not in group["details"]:
            group["details"].append(reason)
        if context.get("segment_number") not in {None, "?"}:
            group["sections"].add(f"段落 {context['segment_number']}")
        finding_id = context.get("finding_id")
        if finding_id and finding_id not in group["finding_ids"]:
            group["finding_ids"].append(finding_id)
    for group in groups.values():
        sections = sorted(group["sections"], key=str)
        group["impact"] = (f"涉及 {len(sections)} 个段落，完成处理后才能继续最终交付。"
                            if sections else "翻译审校队列仍有未关闭发现。")
        group["recommendation"] = "打开审校工作区，逐项处理并保留处理记录。"
    return list(groups.values())


def page_view(job_id, state, artifacts, *, runtime_view, canonical_label,
                      review_contexts, active_statuses):
    """Compose the report page's single status and decision hierarchy."""
    # 报告页的交付措辞直接取 canonical 状态，一次推导、两处引用。
    academic = state.get("academic_state") or {}
    runtime_status = runtime_view.get("runtime_status") or runtime_view.get("status")
    quality = academic.get("quality_status") or academic.get("status") or "not_started"
    final_qa = finalization.normalize_final_qa(state.get("final_qa"))
    academic_records = academic.get("artifacts") or {}
    strict_finalization = bool(state.get("report_enabled")) and any(
        name in academic_records for name in (
            "report", "compliance", "final_docx_validation",
            "libreoffice_render", "report_qa"))
    final_review_pending = strict_finalization and (
        final_qa.get("author_visual_review") != "CONFIRMED" or
        final_qa.get("word_final_review") != "CONFIRMED")
    validation = artifacts.get("validation") or {}
    groups = issue_groups(artifacts, academic) + review_issue_groups(review_contexts)
    if runtime_status == "waiting_manual" and not any(
            group.get("severity") == "human_review" for group in groups):
        groups.append({
            "category": "运行需要人工确认",
            "details": ["当前阶段已暂停，等待人工输入后才能继续。"],
            "sections": set(),
            "severity": "human_review",
            "level": "human_review",
            "action": "查看运行详情",
            "target": "runtime",
            "impact": "报告生成流程尚未完成。",
            "recommendation": "查看运行详情，确认需要补充的输入。",
        })
    for group in groups:
        group.setdefault("severity", _report_issue_level(group["category"]))
        group["level"] = group["severity"]
        group["impact"] = _report_issue_impact(group)
        group.setdefault("recommendation", _report_issue_recommendation(group["category"])
                         if group["category"] in {
                             "模板合规", "案例不足", "文献证据缺失", "引用需要确认",
                             "统计验证失败", "章节需要重新生成", "需要人工补充",
                         } else "打开审校工作区，逐项处理并保留处理记录。")
    # 报告页的"整体状态"是**导航/呈现**模型；它的"正在运行"成员来自唯一的活动
    # 状态集合（排除正在暂停/已暂停：报告页不提供运行控制）。
    blocking = [group for group in groups if group["severity"] == "blocker"]
    if runtime_status in active_statuses:
        overall = "running"
    elif runtime_status == "waiting_manual":
        overall = "review_required"
    elif runtime_status in {"failed", "interrupted", "stalled"} or quality == "failed":
        overall = "failed"
    elif runtime_status in {"cancelled", "idle_incomplete"} and not state.get("p3_done"):
        overall = "blocked"
    elif blocking or quality == "fail" or validation.get("status") == "fail":
        overall = "blocked"
    elif groups or quality in {"review_required", "pass_with_warnings"}:
        overall = "review_required"
    elif state.get("p3_done") and quality == "pass" and validation.get("status") == "pass":
        overall = "ready_for_delivery"
    elif state.get("p3_md"):
        overall = "draft_preview"
    else:
        overall = "blocked"
    report_preview_available = bool(state.get("p3_md") or artifacts.get("report"))
    status_meta = {
        "running": ("正在运行", "查看运行详情", "运行详情", "neutral"),
        "failed": ("运行失败", "查看运行详情", "运行详情", "danger"),
        "blocked": ("可预览 · 尚不可交付" if report_preview_available else "尚不可交付",
                    "处理阻塞问题", "问题与修复", "danger"),
        "review_required": ("可预览 · 等待复核" if report_preview_available else "等待复核",
                            "查看待复核项", "问题与修复", "warning"),
        # 报告页不另造交付词汇：这里显示的就是 canonical 状态。
        "ready_for_delivery": (canonical_label,
                                "完成最终确认" if final_review_pending else "进入交付",
                                None, "warning" if final_review_pending else "success"),
        "draft_preview": ("可预览 · 尚不可交付", "查看当前稿件", "当前稿件", "neutral"),
    }
    label, cta, target_tab, tone = status_meta[overall]
    severity_order = {"blocker": 0, "warning": 1, "human_review": 2}
    groups.sort(key=lambda group: (severity_order.get(group.get("severity"), 9),
                                   group.get("category", "")))
    recommended_issue = next((group for group in groups
                              if group.get("severity") == "blocker"), None)
    if recommended_issue is None:
        recommended_issue = next((group for group in groups
                                  if group.get("severity") in {"warning", "human_review"}), None)
    if recommended_issue and overall in {"blocked", "review_required"}:
        cta = recommended_issue["action"]
        target_tab = "问题与修复"
    if overall in {"failed", "running"}:
        target_tab = "运行详情"
    if overall == "blocked" and not groups:
        cta = "查看运行详情"
        target_tab = "运行详情"
    if overall == "blocked":
        if blocking:
            gate_note = f"仍有 {len(blocking)} 个报告阻塞项；下一步：{cta}。"
        elif validation.get("status") == "fail":
            gate_note = "当前问题列表没有报告阻塞项，但验证结果仍未通过；下一步：查看报告验证结果。"
        elif quality in {"fail", "failed"}:
            gate_note = "当前问题列表没有报告阻塞项，但报告质量检查未通过；下一步：查看质量检查结果。"
        else:
            gate_note = "报告门禁尚未完成；下一步：查看运行详情并完成报告流程。"
    elif overall == "review_required" and not groups:
        gate_note = "报告仍需人工复核；下一步：查看运行详情并记录复核结果。"
    elif overall == "ready_for_delivery" and final_review_pending:
        gate_note = "报告技术检查已完成，但作者视觉复核和 Word 最终复核仍未确认；下一步：进入交付检查。"
    else:
        gate_note = ""
    return {
        "overall": overall, "overall_label": label, "tone": tone,
        "primary_cta": cta, "target_tab": target_tab,
        "runtime": runtime_view, "quality": quality,
        "validation": validation, "groups": groups, "blocking": blocking,
        "recommended_issue": recommended_issue,
        "preview_only": overall != "ready_for_delivery" or final_review_pending,
        "delivery_label": canonical_label
        if overall == "ready_for_delivery" else
        "可预览 · 尚不可交付" if report_preview_available else "尚不可交付",
        "final_review_pending": final_review_pending,
        "gate_note": gate_note,
    }


def issues_for_category(category, artifacts):
    issues = []
    for artifact_name, field in (("validation", "issues"), ("review", "issues"),
                                 ("literature_support_review", "issues"),
                                 ("academic_quality", "findings")):
        for issue in (artifacts.get(artifact_name) or {}).get(field) or []:
            if issue_category(issue) == category:
                issues.append(issue)
    return issues
