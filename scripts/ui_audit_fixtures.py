#!/usr/bin/env python3
"""Create deterministic, local-only Folith UI audit fixtures."""

from pathlib import Path
import json
import sys

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core
from folith import academic_writer, case_provenance, translation_core
from folith import translation_evidence


OUTPUT = ROOT / "outputs"
FIXED_AT = "2026-09-01T08:15:00+03:00"
PDF_PATH = Path("/tmp/folith-ui-audit-source.pdf")


def _pairs(job_id, count=3, reviewed=False):
    pairs = []
    for index in range(count):
        pairs.append({
            "segment_id": f"seg-{job_id}-{index:04d}",
            "source": [
                "The translation workspace preserves source context and review history.",
                "Terminology decisions should remain visible to the human reviewer.",
                "The final document is frozen only after the delivery gate passes.",
                "A stale downstream artifact must explain what needs to be rebuilt.",
            ][index % 4],
            "target": [
                "翻译工作区保留原文上下文和审校历史。",
                "术语决策应当对人工审校者保持可见。",
                "只有通过交付门禁后，最终文档才会被冻结。",
                "下游产物过期时，系统必须说明需要重建的内容。",
            ][index % 4],
            "initial_target": "初译：" + [
                "翻译工作区保留原文上下文和审校历史。",
                "术语决策应当对人工审校者保持可见。",
                "只有通过交付门禁后，最终文档才会被冻结。",
                "下游产物过期时，系统必须说明需要重建的内容。",
            ][index % 4],
            "reviewed": reviewed,
            "review_status": "reviewed_clean" if reviewed else "not_reviewed",
            "target_provenance": "reviewed" if reviewed else "generated",
        })
    return pairs


def _base(job_id, filename, count=3, *, review_required=False, report=False,
          reviewed=False):
    pairs = _pairs(job_id, count, reviewed=reviewed)
    state = core.new_job_state(filename)
    state.update({
        "p1_done": True,
        "p2_done": True,
        "p3_done": False,
        "report_enabled": report,
        "target_lang": "简体中文",
        "paras": [pair["source"] for pair in pairs],
        "pairs": pairs,
        "document_profile": {
            "domain": "翻译研究",
            "subdomain": "长文档工作流",
            "genre": "学术实践报告",
            "audience": "研究生与审校者",
            "register": "正式书面语",
            "style_constraints": "保留引用和专有名词",
        },
        "profile_done": True,
        "translation_core_review_required": review_required,
        "review_evidence": [],
        "findings": [],
        "human_actions": [],
        "review_stats": {
            "reviewed_segments": count if reviewed else 0,
            "batches_reviewed": 1 if reviewed else 0,
            "blocking": 0,
            "actionable": 0,
            "informational": 0,
            "review_failed": 0,
        },
        "translation_truth": {
            "authority": "CURRENT_TRANSLATION",
            "version": 1,
            "last_changed_at": FIXED_AT,
            "last_change": None,
        },
        "pipeline_config": {
            "provider": "DeepSeek",
            "model": "audit-model",
            "target_lang": "简体中文",
            "auto_term": False,
            "enable_report": report,
            "enable_review": review_required,
            "enable_annotate": False,
            "use_tm": True,
            "translation_theory": "",
            "style_rules": "正式书面语",
        },
        # 真实任务经 run_job_pipeline 一定会带上这些顶层字段。fixture 以前缺它们，
        # 于是完成判定（依赖 enable_annotate 等）与交付就绪判断会得出与实际不同的
        # 结论，导出截图因此比真实情况杂乱。
        "provider": "DeepSeek",
        "model": "audit-model",
        "enable_report": report,
        "enable_review": review_required,
        "enable_annotate": False,
        "use_tm": True,
        "annotations_done": False,
        "quality_mode": False,
        "stage": "TRANSLATED" if reviewed else "TRANSLATING",
        "style_rules": "正式书面语",
        "glossary_versions": [],
        "project_id": core.DEFAULT_PROJECT_ID,
        "translator_config": {"provider": "DeepSeek", "model": "audit-model"},
        "delivery_config": core.default_delivery_config(),
    })
    return state


def _review_event(state, segment_index, event_id, *, decision="clean",
                  target=None, stale=False):
    pair = state["pairs"][segment_index]
    target = target if target is not None else pair["target"]
    fingerprint = translation_core.fingerprint({
        "segment": segment_index, "target": target, "fixture": event_id,
    })
    event = {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_index],
        "dependency_segment_ids": [segment_index],
        "decision": decision,
        "freshness_status": "stale" if stale else "current",
        "stale_segment_ids": [segment_index] if stale else [],
        "stale_reason": "人工修改 CURRENT_TRANSLATION" if stale else "",
        "completion_receipt": {
            "status": "failed" if decision == "failed" else "completed",
            "reviewed_segment_ids": [segment_index],
        },
        "translation_core": {
            "final_consumed_input_fingerprint": fingerprint,
            "review_truth": [{
                "segment_id": segment_index,
                "source": pair["source"],
                "target": target,
                "target_checked": True,
            }],
        },
        "created_at": FIXED_AT,
    }
    state["review_evidence"].append(event)
    return fingerprint


def _finding(state, segment_index, event_id, fingerprint, *, category,
             summary, source_span, target_span, suggested_target="",
             entry_id="", severity="blocking", location="source-offset:0"):
    raw = {
        "type": "review",
        "category": category,
        "severity": severity,
        "status": "open",
        "segment_id": segment_index,
        "segment_index": segment_index,
        "location_key": location,
        "entry_id": entry_id,
        "requires_human_confirmation": severity == "blocking",
        "summary": summary,
        "source_span": source_span,
        "target_span": target_span,
        "explanation": "当前译文需要结合原文、项目约束与上下文进行人工判断。",
        "recommendation": "核对后选择安全动作，并在需要时重新审校当前段落。",
        "detector": "Synthetic UI Audit Review",
        "confidence": 0.91,
        "review_event_id": event_id,
        "reason": summary,
    }
    if suggested_target:
        raw["suggested_target"] = suggested_target
    normalized = translation_core.normalize_finding(raw, input_fingerprint=fingerprint)
    normalized.update({
        "type": "review",
        "segment_index": segment_index,
        "review_event_id": event_id,
        "reason": summary,
        "created_at": FIXED_AT,
    })
    state["findings"].append(normalized)
    return normalized


def _review_stats(state):
    counts = {"blocking": 0, "actionable": 0, "informational": 0}
    for finding in state.get("findings") or []:
        if finding.get("status") == "open":
            severity = finding.get("severity")
            if severity in counts:
                counts[severity] += 1
    state["review_stats"].update(counts)
    state["has_blocking"] = bool(counts["blocking"])


def _reviewed_state(job_id, filename, count=3):
    state = _base(job_id, filename, count, review_required=True, reviewed=True)
    for index in range(count):
        event_id = f"audit-review-{job_id}-{index}"
        _review_event(state, index, event_id)
    return state


def _artifact(state, job_id, name, payload, status="valid"):
    filename = academic_writer.ARTIFACT_FILES[name]
    path = OUTPUT / job_id / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if filename.endswith(".jsonl"):
        items = payload.get("items") if isinstance(payload, dict) else []
        metadata = {"record_type": "artifact_metadata", "status": status}
        lines = [json.dumps(metadata, ensure_ascii=False)]
        lines.extend(json.dumps(item, ensure_ascii=False) for item in items or [])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {
        "artifact_id": name,
        "artifact_type": "audit_fixture",
        "file": filename,
        "status": status,
        "content_hash": "audit-fixture",
        "dependency_hash": "audit-fixture",
        "version": "audit-v1",
        "updated_at": FIXED_AT,
        "input_segment_ids": [],
        "input_artifact_ids": [],
    }
    state.setdefault("academic_state", {}).setdefault("artifacts", {})[name] = record
    state.setdefault("academic_state", {}).setdefault("artifact_status", {})[name] = {
        key: record[key] for key in ("status", "updated_at")
    }


def _report_state(job_id, filename, *, stale=False, qa="unfinished", cases_pending=False):
    state = _reviewed_state(job_id, filename, count=3)
    state.update({
        "p3_done": True,
        "report_enabled": True,
        "report_status": "generated",
        "academic_state": {
            **state["academic_state"],
            "status": "pass",
            "current_stage": "report_generated",
            "quality_status": "pass",
            "updated_at": FIXED_AT,
        },
        "p3_md": (
            "# 翻译实践报告\n\n"
            "## 1 引言\n\n本报告记录一次长文档翻译项目的工作方法。\n\n"
            "## 2 项目概述\n\n项目围绕术语、上下文和审校展开。\n\n"
            "## 3 案例分析\n\n案例显示当前译文与审校决策之间的关系。\n\n"
            "## 4 实践总结\n\n人工确认保留了可追溯的交付依据。\n\n"
            "## 5 参考文献\n\n[1] Synthetic audit reference.\n\n"
            "## 6 双语附录\n\n原文与译文对照。"
        ),
    })
    cases = [
        {
            "case_id": "case-real-audit-01",
            "case_type": "authentic_revision",
            "segment_index": 0,
            "source_text": state["pairs"][0]["source"],
            "initial_text": "历史初译：翻译工作区保留上下文。",
            "analysis_fields": {"decision": "保留上下文范围"},
            "target_subsection": "3.1",
            "review_status": "approved" if not cases_pending else "unreviewed",
        },
        {
            "case_id": "case-synthetic-audit-02",
            "case_type": "synthetic_contrast",
            "segment_index": 1,
            "source_text": state["pairs"][1]["source"],
            "synthetic_baseline": {"text": "模拟初译：术语决策需要可见。"},
            "baseline_status": "unreviewed",
            "analysis_fields": {"decision": "统一术语"},
            "synthetic_evidence": {
                "baseline_plausibility": "pass",
                "material_difference": "pass",
                "repair_correctness": "pass",
                "academic_analysis_value": "high",
            },
            "target_subsection": "3.2",
            "review_status": "unreviewed",
        },
    ]
    state["case_reviews"] = {
        cases[0]["case_id"]: {
            "review_status": "approved" if not cases_pending else "unreviewed",
            "reviewed_at": FIXED_AT if not cases_pending else None,
            "actor": "audit-fixture",
        },
        cases[1]["case_id"]: {"review_status": "unreviewed"},
    }
    selected_cases = {
        "cases": cases,
        "authentic_selection_status": "sufficient_revision_cases",
        "report_case_policy": {"synthetic_counts_toward_minimum": False},
    }
    _artifact(state, job_id, "selected_cases", selected_cases)
    _artifact(state, job_id, "outline", {
        "sections": [{"section_id": "1", "title": "引言"},
                     {"section_id": "3", "title": "案例分析"},
                     {"section_id": "4", "title": "实践总结"}],
    })
    _artifact(state, job_id, "report", {"report_status": "generated", "content_hash": "audit-fixture"})
    _artifact(state, job_id, "validation", {"status": "pass", "issues": []})
    _artifact(state, job_id, "review", {"status": "pass", "issues": []})
    _artifact(state, job_id, "literature_sources", {
        "sources": [{"source_id": "audit-source-1", "title": "Synthetic audit reference"}],
    })
    _artifact(state, job_id, "literature_evidence", {
        "items": [{"evidence_id": "audit-evidence-1", "source_id": "audit-source-1", "quote": "audit"}],
    })
    _artifact(state, job_id, "literature_claims", {
        "items": [{"claim_id": "audit-claim-1", "source_id": "audit-source-1", "evidence_grounded_status": "grounded"}],
    })
    _artifact(state, job_id, "literature_support_review", {"status": "pass", "issues": []})
    _artifact(state, job_id, "academic_quality", {"status": "pass", "findings": []})
    final_qa = state["final_qa"]
    if qa == "pass":
        final_qa.update({
            "structural_qa": "PASS",
            "libreoffice_render": "PASS",
            "author_visual_review": "CONFIRMED",
            "word_final_review": "CONFIRMED",
            "translation_truth_version": 1,
            "page_count": 3,
            "updated_at": FIXED_AT,
        })
        _artifact(state, job_id, "final_docx_validation", {"status": "pass", "issues": []})
        _artifact(state, job_id, "libreoffice_render", {
            "qa_status": "PASS", "status": "pass", "page_count": 3,
            "analysis": {"pages": [{"page_number": 1, "text_block_count": 8}]},
        })
    elif qa == "failed":
        final_qa["structural_qa"] = "FAIL"
        _artifact(state, job_id, "final_docx_validation", {"status": "failed", "issues": [{"reason": "fixture failure"}]}, status="failed")
    elif qa == "stale":
        final_qa["structural_qa"] = "STALE"
        _artifact(state, job_id, "final_docx_validation", {"status": "pass", "issues": []}, status="stale")
    if stale:
        state["dependency_impact"] = {
            "schema_version": "finalization-state-v1",
            "status": "stale",
            "reason": "第 2 段当前译文已修改，报告下游需要更新。",
            "changed_segment_ids": [state["pairs"][1]["segment_id"]],
            "changed_segment_indexes": [1],
            "affected_case_ids": ["case-synthetic-audit-02"],
            "affected_section_ids": ["3"],
            "affected_subsection_ids": ["3.2"],
            "chain": [{"id": "CURRENT_TRANSLATION"}, {"id": "case:case-synthetic-audit-02"},
                      {"id": "subsection:3.2"}, {"id": "report"}],
            "affected": [
                {"id": "case:case-synthetic-audit-02", "status": "stale"},
                {"id": "subsection:3.2", "status": "stale"},
                {"id": "report", "status": "stale"},
            ],
            "reusable": [
                {"id": "case:case-real-audit-01", "status": "reusable"},
                {"id": "subsection:3.1", "status": "reusable"},
            ],
            "recorded_at": FIXED_AT,
        }
        state["translation_truth"]["last_change"] = {
            "segment_indexes": [1],
            "reason": "人工修改 CURRENT_TRANSLATION；相关下游需要重建",
        }
    return state


def _runtime_for(state, *, completed=None, completed_units=None, total_units=None):
    """与业务状态一致的运行状态。

    真实任务都会留下 runtime_state.json。fixture 以前完全不写，于是运行时状态
    只能靠推断，审计截图里的运行面板会显示与业务状态无关的内容
    （例如已完成任务却出现「继续处理」）。
    """
    if completed is None:
        completed = core._runtime_business_complete(state)
    changes = {
        "status": "completed" if completed else "idle_incomplete",
        "phase": "completed" if completed else "idle_incomplete",
        "phase_label": "已完成" if completed else "尚未完成",
        "started_at": FIXED_AT,
        "last_heartbeat_at": FIXED_AT,
        "last_progress_at": FIXED_AT,
        "last_event": "fixture",
    }
    if total_units:
        changes.update(completed_units=completed_units or 0,
                       total_units=total_units,
                       overall_progress=(completed_units or 0) / total_units)
    return changes


def _save(job_id, state, *, runtime=None):
    if runtime is None:
        runtime = _runtime_for(state)
    core.save_job_state(job_id, state)
    core.save_source(job_id, b"Folith synthetic UI audit source")
    core.update_runtime_state(job_id, **runtime)


def warn_about_target():
    """明确告知本脚本会覆盖什么。

    它会就地改写 `ui-audit-*` 任务目录，并**替换全局翻译记忆
    （outputs/translation_memory.json）**。审计包的做法是事后手工恢复
    outputs/；这里把这个事实打印出来，避免静默覆盖真实数据。
    """
    OUTPUT.mkdir(parents=True, exist_ok=True)
    others = sorted(p.name for p in OUTPUT.iterdir()
                    if p.is_dir() and not p.name.startswith("ui-audit-"))
    tm = OUTPUT / "translation_memory.json"
    if others:
        print(f"[提示] {OUTPUT} 下还有 {len(others)} 个非 fixture 任务目录"
              f"（{', '.join(others[:3])}{'…' if len(others) > 3 else ''}）；"
              "本脚本不会改动它们。")
    if tm.is_file():
        print(f"[提示] 本脚本会替换全局翻译记忆 {tm}；"
              "如需保留，请先备份或改到临时目录运行。")


def main():
    warn_about_target()
    (OUTPUT / ".onboarded").touch()
    core.OUTPUT_DIR = OUTPUT

    new_state = core.new_job_state("audit-new-untranslated.docx")
    new_state.update({"p1_done": False, "p2_done": False, "paras": [], "pairs": []})
    _save("ui-audit-new-untranslated", new_state)

    in_progress = _base("ui-audit-in-progress", "audit-translation-in-progress.docx", 4)
    in_progress.update({"p2_done": False, "pairs": _pairs("ui-audit-in-progress", 2),
                        "stage": "TRANSLATING", "glossary_frozen": {
                            "version": 1, "entries": [], "frozen_at": FIXED_AT,
                        }})
    # 进行中的任务：运行面板应显示真实进度，而不是 "checkpoint 0 / —"
    _save("ui-audit-in-progress", in_progress,
          runtime=_runtime_for(in_progress, completed=False,
                               completed_units=2, total_units=4))

    clean = _reviewed_state("ui-audit-clean", "audit-clean-review.docx", count=3)
    clean["glossary"] = [{
        "id": "term-audit-01", "source": "translation memory",
        "proposed_target": "翻译记忆", "target": "翻译记忆",
        "preferred": "翻译记忆", "status": "locked", "domain": "翻译技术",
        "scope": "项目", "note": "保持术语一致", "occurrences": [0, 2],
    }]
    clean["glossary_frozen"] = {"version": 1, "entries": clean["glossary"], "frozen_at": FIXED_AT}
    _save("ui-audit-clean", clean)

    suggested = _reviewed_state("ui-audit-blocking-suggested", "audit-blocking-suggested.docx")
    fp = _review_event(suggested, 0, "audit-review-suggested-0")
    _finding(suggested, 0, "audit-review-suggested-0", fp, category="semantic_accuracy",
             summary="译文可能扩大原文概念的语义范围", source_span="source context",
             target_span="原文上下文", suggested_target="译文应保留原文的概念边界。")
    _review_stats(suggested)
    _save("ui-audit-blocking-suggested", suggested)

    no_suggestion = _reviewed_state("ui-audit-blocking-no-suggestion", "audit-blocking-no-suggestion.docx")
    fp = _review_event(no_suggestion, 0, "audit-review-no-suggestion-0")
    _finding(no_suggestion, 0, "audit-review-no-suggestion-0", fp, category="completeness",
             summary="原文中的限定条件未在译文中清楚体现", source_span="source context",
             target_span="原文上下文", location="source-offset:1")
    _review_stats(no_suggestion)
    _save("ui-audit-blocking-no-suggestion", no_suggestion)

    stale = _reviewed_state("ui-audit-stale", "audit-stale-review.docx")
    for index in range(3):
        _review_event(stale, index, f"audit-review-stale-{index}")
    stale["pairs"][1]["target"] = "人工修改后的当前译文。"
    stale["translation_truth"]["last_change"] = {
        "segment_indexes": [1], "reason": "人工修改 CURRENT_TRANSLATION；相关下游需要重建",
    }
    _save("ui-audit-stale", stale)

    failed = _base("ui-audit-failed", "audit-failed-review.docx", 2, review_required=True)
    _review_event(failed, 0, "audit-review-failed-0", decision="failed")
    _review_event(failed, 1, "audit-review-failed-1")
    failed["review_stats"]["review_failed"] = 1
    _save("ui-audit-failed", failed)

    missing = _base("ui-audit-missing", "audit-missing-review.docx", 2, review_required=True)
    _save("ui-audit-missing", missing)

    multiple = _reviewed_state("ui-audit-multiple", "audit-multiple-findings.docx", count=2)
    fp = _review_event(multiple, 0, "audit-review-multiple-0")
    _finding(multiple, 0, "audit-review-multiple-0", fp, category="completeness",
             summary="第 1 段缺少必要信息", source_span="source", target_span="译文",
             location="source-offset:0")
    _finding(multiple, 0, "audit-review-multiple-0", fp, category="style",
             summary="第 1 段的语气与项目风格不一致", source_span="context", target_span="上下文",
             location="source-offset:1", severity="actionable")
    _review_stats(multiple)
    _save("ui-audit-multiple", multiple)

    style = _reviewed_state("ui-audit-style", "audit-style-finding.docx", count=2)
    fp = _review_event(style, 0, "audit-review-style-0")
    _finding(style, 0, "audit-review-style-0", fp, category="style",
             summary="句式风格需要保持正式、克制", source_span="workspace",
             target_span="翻译工作区", location="source-offset:0", severity="actionable")
    _review_stats(style)
    _save("ui-audit-style", style)

    term = _reviewed_state("ui-audit-term", "audit-term-finding.docx", count=2)
    term["glossary"] = [{
        "id": "term-audit-01", "source": "translation memory", "target": "翻译记忆",
        "preferred": "翻译记忆", "status": "locked", "domain": "翻译技术",
        "note": "项目锁定术语", "occurrences": [0],
    }]
    term["glossary_frozen"] = {"version": 1, "entries": term["glossary"], "frozen_at": FIXED_AT}
    fp = _review_event(term, 0, "audit-review-term-0")
    _finding(term, 0, "audit-review-term-0", fp, category="terminology",
             summary="项目术语需要按规范使用", source_span="translation memory",
             target_span="翻译记忆", entry_id="term-audit-01", location="term:translation-memory",
             severity="actionable")
    _review_stats(term)
    _save("ui-audit-term", term)

    legacy = _base("ui-audit-legacy", "audit-legacy-no-review.docx", 2, review_required=False, reviewed=True)
    legacy["translation_core_review_required"] = False
    _save("ui-audit-legacy", legacy)

    _save("ui-audit-report-available", _report_state("ui-audit-report-available", "audit-report-available.docx", cases_pending=False, qa="pass"))
    _save("ui-audit-report-stale", _report_state("ui-audit-report-stale", "audit-report-stale.docx", stale=True, cases_pending=True))
    _save("ui-audit-qa-unfinished", _report_state("ui-audit-qa-unfinished", "audit-qa-unfinished.docx", qa="unfinished"))
    _save("ui-audit-qa-passing", _report_state("ui-audit-qa-passing", "audit-qa-passing.docx", qa="pass"))
    _save("ui-audit-qa-failed", _report_state("ui-audit-qa-failed", "audit-qa-failed.docx", qa="failed"))
    _save("ui-audit-qa-stale", _report_state("ui-audit-qa-stale", "audit-qa-stale.docx", qa="stale"))

    core.save_tm({
        "translation memory": {"target": "翻译记忆", "updated_at": FIXED_AT},
        "current translation": {"target": "当前译文", "updated_at": FIXED_AT},
    })

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 90), "Folith UI audit fixture", fontsize=16)
    page.insert_text((72, 130), "The translation workspace preserves source context.", fontsize=11)
    page.insert_text((72, 160), "Terminology decisions remain visible to the reviewer.", fontsize=11)
    document.save(str(PDF_PATH))
    document.close()

    print(json.dumps({
        "output": str(OUTPUT),
        "pdf": str(PDF_PATH),
        "fixture_count": len([p for p in OUTPUT.iterdir() if p.is_dir() and (p / "state.json").is_file()]),
        "fixtures": [
            "ui-audit-new-untranslated", "ui-audit-in-progress", "ui-audit-clean",
            "ui-audit-blocking-suggested", "ui-audit-blocking-no-suggestion", "ui-audit-stale",
            "ui-audit-failed", "ui-audit-missing", "ui-audit-multiple", "ui-audit-style",
            "ui-audit-term", "ui-audit-legacy", "ui-audit-report-available", "ui-audit-report-stale",
            "ui-audit-qa-unfinished", "ui-audit-qa-passing", "ui-audit-qa-failed", "ui-audit-qa-stale",
        ],
    }, ensure_ascii=False, indent=2))


def self_check():
    """fixture 必须与真实任务一致，否则审计截图会误导。

    重点：**已完成**的任务不允许被判定为未完成——否则概览会渲染运行面板
    （worker / lease / checkpoint）并出现「继续处理」，让截图看起来比真实情况
    杂乱。这里把它变成脚本自身的失败，而不是靠人工事后发现。
    """
    problems = []
    # 只检查本脚本生成的 fixture：`outputs/` 里可能还有用户真实任务，它们的
    # "worker 跑完了"与"业务全部完成"本来就可以不同，不属于 fixture 的问题。
    for job_dir in sorted(p for p in OUTPUT.iterdir()
                          if p.is_dir() and p.name.startswith("ui-audit-")):
        state = core.load_job_state(job_dir.name)
        if state is None:
            continue
        business = core._runtime_business_complete(state)
        status = core.build_job_runtime_view(job_dir.name, state).get("runtime_status")
        expected = "completed" if business else "idle_incomplete"
        if status != expected:
            problems.append(f"{job_dir.name}: runtime={status} 但业务完成={business}")
        if business and status not in {None, "idle", "completed"}:
            problems.append(f"{job_dir.name}: 已完成任务会渲染运行面板")
    if problems:
        raise SystemExit("fixture 自检失败：\n  " + "\n  ".join(problems))
    count = len([p for p in OUTPUT.iterdir()
                 if p.is_dir() and p.name.startswith("ui-audit-")])
    print(f"fixture 自检通过：{count} 个 fixture 的运行状态与业务状态一致")


if __name__ == "__main__":
    main()
    self_check()
