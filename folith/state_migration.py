"""任务状态机与旧 state.json 迁移。

阶段（stage）：
    INGESTED -> PROFILED -> TERMS_PREPARED -> GLOSSARY_FROZEN -> TRANSLATING -> TRANSLATED
    -> ANNOTATED -> ACADEMIC_WRITING -> REPORT_GENERATED -> REVIEW_REQUIRED / FINAL

兼容性：
- 旧任务只有 p1_done / p2_done / p3_done / annotations_done；
- migrate_state 只补默认值，不把旧任务虚假标记为 glossary 已冻结；
- 交付状态（delivery_status）默认 draft；旧任务即使全部完成也保持 draft，
  需要人工确认后才进入 final。
- 明确保存为 paragraph 模式的旧 CAT 任务会经过一次保守的自然句迁移；
  目标句数无法与源句安全对齐的段落保留原样并进入迁移门禁。
"""
from __future__ import annotations

from typing import Any, Dict

STAGES = (
    "INGESTED", "PROFILED", "TERMS_PREPARED", "GLOSSARY_FROZEN", "TRANSLATING", "TRANSLATED",
    "ANNOTATED", "ACADEMIC_WRITING", "ACADEMIC_REVIEW_REQUIRED",
    "ACADEMIC_FAILED", "REPORT_GENERATED", "REVIEW_REQUIRED", "FINAL",
)

DELIVERY_STATUSES = ("draft", "review_required", "approved", "final")


def _default_new_fields() -> Dict[str, Any]:
    """新增字段的默认值（旧任务加载时补齐，避免 KeyError）。"""
    from .academic_writer import default_academic_state
    from .finalization import default_dependency_impact, default_final_qa
    from .project import SYSTEM_PROJECT_ID
    from .usage import empty_usage
    return {
        "annotation_batches": {},
        "understanding_retry_requested": False,
        "stage": "INGESTED",
        "delivery_status": "draft",
        # 任务所属项目（蓝图 §3.2 的 Project / Job 边界）。旧任务统一归入系统
        # 工作区「未分类」——它有真实持久化 UUID 与 is_system=true，不再是字符串
        # "default" 这样的虚拟 project id。项目记录存放在 outputs/projects/。
        # 显式 `None` 是**有效值**（用户选择「未分类」），由 migrate_state 保留。
        "project_id": SYSTEM_PROJECT_ID,
        # 项目记忆注入审计：哪些跨任务锁定术语/风格规则被注入过这个任务。
        "project_memory": {},
        "document_profile": None,
        # PDF text-layer/OCR cleanup is a separate source checkpoint.  The raw
        # extraction is retained so a correction can be audited or retried
        # without re-reading the uploaded file.
        "source_paras": [],
        # Cleaned paragraph structure is retained separately from the CAT
        # sentence units in ``paras``.  This keeps source audit/export stable
        # while allowing translation to use sentence-sized segments.
        "source_paragraphs": [],
        "segmentation": {},
        "source_cleanup_done": False,
        "source_cleanup": {},
        "profile_done": False,
        "profile_warnings": [],
        "semantic_units": [],
        "section_digests": [],
        "document_synopsis": None,
        "understanding_done": False,
        "understanding_warnings": [],
        "context_packet_log": [],
        "llm_usage": empty_usage(),
        # Auxiliary model is optional; when absent the translator configuration
        # remains the fallback for all analysis/continuity calls.
        "auxiliary_config": {},
        "knowledge_feedback_policy": {},
        "targeted_final_review": {
            "status": "not_run", "segment_ids": [],
            "reviewed_segment_ids": [], "failed_segment_ids": [],
        },
        "knowledge_candidates": [],
        "translation_continuity": [],
        "knowledge_events": [],
        "confirmed_style_rules": [],
        "knowledge_feedback_failures": 0,
        "entity_registry": [],
        "translation_failures": [],
        "review_evidence": [],
        "translation_core_review_required": False,
        "repair_overlays": [],
        "tm_recovered_count": 0,
        "auto_term_entries": [],
        "glossary": [],
        "glossary_draft": [],
        "glossary_frozen": None,
        "glossary_versions": [],
        "glossary_injection_log": [],
        "human_actions": [],
        "delivery_manifest": {},
        # CAT 工作台的结构编辑：稳定段落身份、排除清单、撤销留档。
        # 这些字段让"同一段在拆分/合并之后仍然是同一段"，以及"被排除的原文还能
        # 原样恢复"成为持久事实，而不是只活在浏览器会话里。
        "segment_uid_seq": 0,
        "segment_structure_history": [],
        "segment_structure_undo_log": [],
        "excluded_segments": [],
        "exclusion_seq": 0,
        # 导入范围报告：本次提取到了什么、哪些内容没有进入翻译。
        "extraction_report": {},
        # Stage-level timing is an audit artifact, never a workflow gate.
        "performance": {},
        "delivery_approved_by_human": False,
        "delivery_approval": None,
        "delivery_snapshots": [],
        "latest_delivery_snapshot_version": None,
        "exported_assets": [],
        "pipeline_config": {},
        # Explicit OCR language contract.  Legacy tasks have no value here; the
        # OCR stage then uses its documented compatibility fallback with a
        # visible warning instead of silently guessing.  New tasks always write
        # the user's selection.
        "ocr_language": "",
        # Ingestion contract: auto trusts a text layer only when the quality
        # gate accepts it; force always OCRs; off never does.  Legacy tasks
        # default to auto, which is the pre-existing behaviour for a clean
        # layer and the fixed behaviour for a broken one.
        "ocr_mode": "auto",
        "ocr_config": {},
        "translator_config": {},
        "reviewer_config": {},
        "delivery_config": {},
        "quality_mode": False,
        "quality_bypass": False,
        "delivery_notes": "",
        "delivery_validation": {},
        "research_settings": {},
        "literature_sources": [],
        "report_template": None,
        "report_template_contract": None,
        # v0.4 finalization state.  These are intentionally plain records in
        # state.json so older jobs can be opened without a migration step.
        "translation_truth": {
            "authority": "CURRENT_TRANSLATION",
            "version": 0,
            "last_changed_at": None,
            "last_change": None,
        },
        "dependency_impact": default_dependency_impact(),
        "case_reviews": {},
        "case_review_overrides": {},
        "final_qa": default_final_qa(),
        "compliance_profile_id": "MTI_PRACTICE_REPORT_DEFAULT",
        "compliance_record": {},
        "language_constraint_record": {},
        "academic_state": default_academic_state(),
    }


def derive_stage(state: Dict[str, Any]) -> str:
    """由现有里程碑标志推导当前阶段（不修改状态）。"""
    if not state.get("p1_done"):
        return "INGESTED"

    # 1) 翻译已开始但尚未完成 → TRANSLATING
    if state.get("p1_done") and not state.get("p2_done"):
        pairs = state.get("pairs") or []
        if pairs and (state.get("glossary_frozen") or state.get("quality_bypass")):
            return "TRANSLATING"
    # 2) 术语表已冻结 → GLOSSARY_FROZEN
    if not state.get("p2_done") and state.get("glossary_frozen"):
        return "GLOSSARY_FROZEN"
    # 3) 术语已提取但未冻结 → TERMS_PREPARED（优先于 profile_done 检查）
    if not state.get("p2_done") and (state.get("auto_term_entries") or state.get("auto_terms") \
            or state.get("glossary") or state.get("glossary_draft")):
        return "TERMS_PREPARED"
    # 4) 画像完成 → PROFILED
    if not state.get("p2_done") and state.get("profile_done"):
        return "PROFILED"
    # 5) p1 完成但画像未完成 → PROFILED（仍可进入术语准备）
    if not state.get("p2_done"):
        return "PROFILED"
    if state.get("has_blocking"):
        return "REVIEW_REQUIRED"
    academic = state.get("academic_state") or {}
    if academic.get("status") == "failed" or academic.get("quality_status") == "fail":
        return "ACADEMIC_FAILED"
    if academic.get("quality_status") == "review_required":
        return "ACADEMIC_REVIEW_REQUIRED"
    if academic.get("status") == "in_progress":
        return "ACADEMIC_WRITING"
    if state.get("p3_done") or not state.get("report_enabled", True):
        return "REPORT_GENERATED"
    if state.get("annotations_done"):
        return "ANNOTATED"
    return "TRANSLATED"


def derive_delivery_status(state: Dict[str, Any]) -> str:
    """由现有状态推导交付状态。

    规则：翻译完成且有 blocking -> review_required；
    其余保持 draft（final 只能由人工确认产生，旧任务不得自动变 final）。
    """
    if state.get("p2_done") and state.get("has_blocking"):
        return "review_required"
    return "draft"


def migrate_state(state: Any) -> Dict[str, Any]:
    """迁移旧任务状态：补默认字段、升级 CAT 粒度并推导生命周期。

    CAT 句级迁移只在通过 segmentation gate 的段落上发生；译文文本保留，
    旧 evidence / 交付批准在结构改变时失效并等待重新确认。
    """
    if not isinstance(state, dict):
        return dict(_default_new_fields())
    out = dict(state)
    # `project_id` 允许显式的 `None`：它表示用户选择了系统工作区「未分类」，是一个
    # 有效选择，不能按"缺省字段"补默认值（默认值本身就是系统工作区 UUID，语义
    # 相同但会掩盖"用户显式选过"这一事实）。其余新字段仍然在缺失/为 None 时补默认。
    preserve_unassigned_project = "project_id" in out and out.get("project_id") is None
    for key, default in _default_new_fields().items():
        if key not in out or out[key] is None:
            out[key] = default
    if preserve_unassigned_project:
        out["project_id"] = None
    # v0.4 exposes one anonymous default profile. Unknown or private profile
    # identifiers are not preserved in public-facing state until custom
    # profile import exists.
    out["compliance_profile_id"] = "MTI_PRACTICE_REPORT_DEFAULT"

    # 旧任务可能把新字段存成空串/空 dict，统一归一化
    if out.get("glossary") == {}:
        out["glossary"] = []
    if out.get("auto_term_entries") == {}:
        out["auto_term_entries"] = []
    if not out.get("translation_continuity") and out.get("knowledge_candidates"):
        out["translation_continuity"] = list(out.get("knowledge_candidates") or [])
    for candidate in out.get("knowledge_candidates") or []:
        if isinstance(candidate, dict):
            candidate.setdefault("provenance", "generated_continuity")
            candidate.setdefault("scope", "document")
            candidate.setdefault("confidence", 0.35)
    from . import entity_registry
    out["entity_registry"] = entity_registry.normalize_registry(
        out.get("entity_registry") or [])

    # CAT 默认契约已统一为自然句。旧任务的段落级状态在内存中做一次
    # 可审计、无模型的迁移；只有通过源文覆盖与目标句数门禁的段落才会拆开。
    # 该调用不写磁盘，保持 job_repository 的读取路径无副作用；下一次正常
    # 保存会把迁移后的状态持久化。
    from . import segmentation as _segmentation
    out, segmentation_migration = _segmentation.migrate_legacy_state(out)
    if ((out.get("segmentation") or {}).get("mode") == "sentence"
            and isinstance(out.get("pipeline_config"), dict)
            and out["pipeline_config"].get("segmentation_mode") == "paragraph"):
        # Keep resume/start configuration aligned with the migrated CAT
        # contract.  A paragraph choice made for a new task is stored only
        # after the task starts and therefore does not enter this legacy path.
        out["pipeline_config"]["segmentation_mode"] = "sentence"
    if (segmentation_migration.get("status") in {"completed", "partial"}
            and int(segmentation_migration.get("split_paragraph_count") or 0) > 0):
        # 旧 evidence/finding 的索引是段落级的，不能继续冒充句级证据。
        # 译文文本本身已在迁移函数中保留，但审校与交付批准必须重新确认。
        out["findings"] = []
        out["review_evidence"] = []
        out["repair_overlays"] = []
        out["has_blocking"] = False
        out["review_stats"] = {
            "reviewed_segments": 0, "batches_reviewed": 0,
            "blocking": 0, "actionable": 0,
            "informational": 0, "review_failed": 0,
        }
        out["targeted_final_review"] = {
            "status": "not_run", "segment_ids": [],
            "reviewed_segment_ids": [], "failed_segment_ids": [],
        }
        out["annotations"] = {}
        out["annotations_done"] = False
        out["annotations_done_offset"] = 0
        out["annotation_batches"] = {}
        out["annotations_failed_batches"] = 0
        out["p3_done"] = False
        out["p3_md"] = ""
        out["p3_sections"] = []
        out["batch_plan"] = {}
        out["delivery_validation"] = {}
        out["delivery_status"] = "draft"
        out["delivery_approved_by_human"] = False
        out["delivery_approval"] = None
        out["delivery_snapshots"] = []
        out["latest_delivery_snapshot_version"] = None

    # An explicitly approved delivery is a durable milestone, not a value to
    # recompute from the mutable processing flags on reload.
    out["stage"] = "FINAL" if out.get("delivery_status") == "final" \
        else derive_stage(out)
    # 只在不显式设置过交付状态时推导；显式 final/approved 不覆盖
    if out.get("delivery_status") not in ("approved", "final"):
        out["delivery_status"] = derive_delivery_status(out)
    return out
