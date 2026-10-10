"""Compliance & final-QA projection for the workspace (UI-framework neutral).

The "合规与最终 QA" page answers one question: *what still stands between this
report and a confirmed final version?*  Each check decides exactly one thing; a
passing structure check or page render does not stand in for the author's or
Word's final review.  This module only reshapes facts the core already owns
(``compliance_profile_view``, ``final_qa``, the DOCX validation artifact) into
rows a frontend can draw — it decides nothing itself.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from folith import finalization as _finalization

RULE_STATUS_LABELS = {"pass": "通过", "fail": "失败", "manual_review": "手动复核",
                      "not_applicable": "不适用", "not_checked": "未检查"}
CHECK_LEVEL_LABELS = {"deterministic": "自动检查", "manual": "人工检查",
                      "project_constraint": "项目约束"}
AUTHORITY_LABELS = {"project": "项目约束", "reference_template": "默认参考模板",
                    "custom_profile": "用户自定义院校规则"}
#: Open items that need attention (anything not pass / not applicable).
ATTENTION_RULE_STATUSES = frozenset({"fail", "manual_review", "not_checked"})

#: The four independent final-QA facts, in display order: (field, label, what it proves).
QA_FACTS = (
    ("structural_qa", "DOCX 结构检查", "文档结构、段落与确定性规则"),
    ("libreoffice_render", "LibreOffice 页面渲染", "DOCX 转 PDF 页面预检"),
    ("author_visual_review", "作者视觉复核", "作者检查关键页面与版式"),
    ("word_final_review", "Word 最终复核", "Word 更新字段并确认最终页面"),
)
MANUAL_FACTS = frozenset({"author_visual_review", "word_final_review"})


def structural_qa(state: Mapping[str, Any], final_docx_validation: Optional[Mapping[str, Any]]) -> str:
    """PASS / FAIL / NOT_RUN, or STALE when the validated DOCX is out of date."""
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    validation = final_docx_validation or {}
    structural = ("PASS" if validation.get("status") in {"pass", "pass_with_warnings"}
                  else "FAIL" if validation.get("status") == "fail"
                  else qa.get("structural_qa", "NOT_RUN"))
    artifact_status = _finalization._artifact_status_value(
        state.get("academic_state") or {}, "final_docx_validation")
    if artifact_status in {"stale", "missing"}:
        return "STALE"
    if artifact_status == "failed":
        return "FAIL"
    return structural


def _rule_row(rule: Mapping[str, Any]) -> Dict[str, Any]:
    status = str(rule.get("status") or "not_checked")
    source = rule.get("source") or {}
    check_level = str(rule.get("check_type") or rule.get("check_level") or "—")
    authority = str(rule.get("authority_level") or "")
    source_available = bool(rule.get("source_available") or rule.get("reliable_source_mapping")
                            or source.get("available"))
    has_rule_source = bool(
        rule.get("source_kind") in {"reference_template", "custom_profile"}
        and authority != "project" and source_available)
    authority_label = AUTHORITY_LABELS.get(authority) or (
        "未映射的自定义规则" if not source_available else str(source.get("authority") or "—"))
    document = rule.get("source_document") or source.get("file")
    return {
        "id": str(rule.get("rule_id") or rule.get("id") or ""),
        "label": str(rule.get("description") or rule.get("label") or rule.get("rule_id")
                     or rule.get("id") or "合规规则"),
        "message": str(rule.get("message") or ""),
        "status": status,
        "status_label": RULE_STATUS_LABELS.get(status, status),
        "needs_attention": status in ATTENTION_RULE_STATUSES,
        "source": str(document if has_rule_source and document else "院校特殊要求需根据实际模板确认"),
        "page_or_clause": str(rule.get("page_or_clause") or source.get("page") or "—"),
        "source_url": str(rule.get("source_url") or source.get("url") or "") if has_rule_source else "",
        "scope": str(rule.get("scope") or rule.get("applicability") or "—"),
        "check_level": CHECK_LEVEL_LABELS.get(check_level, check_level),
        "authority": authority_label,
        "implementation": " · ".join(str(x) for x in (
            rule.get("implementation_source"),
            rule.get("implementation_clause") or rule.get("source_clause")) if x),
    }


def qa_view(state: Mapping[str, Any], compliance: Mapping[str, Any],
            profile: Mapping[str, Any], structural: str,
            render_record: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Everything the QA page draws, derived from facts the core already holds."""
    counts = compliance.get("counts") or {}
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    render = qa.get("libreoffice_render")
    failed = int(counts.get("fail", 0) or 0) + int(structural == "FAIL") + int(render == "FAIL")
    manual = int(counts.get("manual_review", 0) or 0)
    manual += int(qa.get("author_visual_review") != "CONFIRMED")
    manual += int(qa.get("word_final_review") != "CONFIRMED")
    not_run = int(counts.get("not_checked", 0) or 0)
    not_run += int(structural in {"NOT_RUN", "STALE"})
    not_run += int(render in {None, "NOT_RUN", "STALE"})

    facts: List[Dict[str, Any]] = []
    for field, label, proof in QA_FACTS:
        status = structural if field == "structural_qa" else qa.get(field)
        facts.append({
            "field": field, "label": label, "proof": proof, "status": status,
            "status_label": ("需要重建" if status == "STALE"
                             else _finalization.final_qa_label(field, str(status))),
            "manual": field in MANUAL_FACTS,
            "confirmed": status == "CONFIRMED",
        })
    key_pages: List[Dict[str, Any]] = []
    if render == "PASS" and render_record:
        from folith import rendered_qa as _rendered_qa
        key_pages = _rendered_qa.key_page_references(render_record)
    mapping = {"reference_template_mapped": "已登记匿名参考模板"}.get(
        profile.get("authority_mapping_status"), "院校特殊要求需人工确认")
    return {
        "profile": {"id": str(profile.get("profile_id") or ""),
                    "name": str(profile.get("display_name") or ""),
                    "mapping": mapping},
        "rules": [_rule_row(rule) for rule in compliance.get("rules") or []],
        "facts": facts,
        "failed": failed, "manual": manual, "not_run": not_run,
        "attention": failed + manual + not_run,
        "structural": structural,
        "page_count": qa.get("page_count"),
        "key_pages": key_pages,
    }
