"""End-to-end tests for source update workflow and cost comparison (Issue #152)."""
import json
from pathlib import Path
import pytest
import core
from folith.docx_roundtrip import extract_roundtrip_units
from folith.source_update import (
    Classification,
    Resolution,
    align_docx_units,
    apply_source_update_plan,
    create_source_update_plan,
    retranslate_incremental,
)


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "source_update"


def test_end_to_end_text_source_update_and_cost_savings(tmp_path, monkeypatch):
    """AC: 端到端：v1 完成任务 → 导入 v2 → 重译仅变更段 → 交付，记录调用成本对比全量重翻。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "job-e2e-text"

    # Step 1: Create and complete v1 task
    with open(FIXTURES_DIR / "text_v1.txt", encoding="utf-8") as f:
        paras_v1 = [line.strip() for line in f if line.strip()]

    pairs_v1 = [
        {
            "source": src,
            "target": f"译文: {src}",
            "initial_target": f"译文: {src}",
            "reviewed": True,
            "review_status": "reviewed",
            "segment_uid": f"seg-v1-{i:04d}",
        }
        for i, src in enumerate(paras_v1)
    ]

    state_v1 = {
        "job_id": job_id,
        "paras": paras_v1,
        "pairs": pairs_v1,
        "review_stats": {"reviewed_segments": len(paras_v1), "total_segments": len(paras_v1)},
        "delivery_status": "delivered",
        "p1_done": True,
        "p2_done": True,
        "excluded_segments": [],
    }
    core.save_source(job_id, (FIXTURES_DIR / "text_v1.txt").read_bytes())
    core.save_job_state(job_id, state_v1)

    # Step 2: Customer sends v2 revision
    with open(FIXTURES_DIR / "text_v2.txt", encoding="utf-8") as f:
        paras_v2 = [line.strip() for line in f if line.strip()]
    v2_bytes = (FIXTURES_DIR / "text_v2.txt").read_bytes()

    # Step 3: Generate Source Update Plan
    plan = create_source_update_plan(state_v1, paras_v2, job_id=job_id)
    assert plan.v1_count == 6
    assert plan.v2_count == 6
    assert plan.summary["suspected_count"] == 1
    assert plan.has_unresolved_suspected is True

    # Human confirms the moved segment
    moved_entry = next(e for e in plan.entries if e.classification == Classification.SUSPECTED_CHANGED)
    assert moved_entry.suspected_reason == "moved_segment"
    plan.resolve(moved_entry.entry_id, Resolution.CONFIRM_REUSE, note="确认移动后依然适用原译文")
    assert not plan.has_unresolved_suspected

    # Step 4: Apply Plan
    updated_state = apply_source_update_plan(
        job_id,
        plan,
        actor="lead_translator",
        new_source_bytes=v2_bytes,
    )

    # Verify state after plan application:
    # 4 unchanged segments retain exact translation and reviewed=True
    # 2 segments (1 changed, 1 added) require retranslation
    # 1 deleted segment entered exclusion ledger
    assert len(updated_state["paras"]) == 6
    assert len(updated_state["pairs"]) == 6

    # Verify unchanged preservation
    assert updated_state["pairs"][0]["source"] == "Title: Project Overview."
    assert updated_state["pairs"][0]["target"] == "译文: Title: Project Overview."
    assert updated_state["pairs"][0]["reviewed"] is True

    # Moved segment confirmed for reuse
    assert updated_state["pairs"][1]["source"] == "Important safety and privacy notice."
    assert updated_state["pairs"][1]["target"] == "译文: Important safety and privacy notice."
    assert updated_state["pairs"][1]["reviewed"] is True

    # Changed segment requires retranslation
    assert updated_state["pairs"][2]["source"] == "Introduction to Folith professional workbench."
    assert updated_state["pairs"][2]["target"] == ""
    assert updated_state["pairs"][2]["retranslation_required"] is True

    # Deleted segment in ledger
    assert len(updated_state["excluded_segments"]) == 1
    assert updated_state["excluded_segments"][0]["source"] == "Legacy section to be removed."

    # Step 5: Incremental Retranslation
    model_calls = []

    def mock_llm_call(prompt: str) -> str:
        model_calls.append(prompt)
        return f"全新译文: {prompt}"

    final_state = retranslate_incremental(job_id, call_llm_fn=mock_llm_call)

    # Assert: Exactly 2 LLM calls made (changed + added), 4 calls saved!
    assert len(model_calls) == 2
    assert "Introduction to Folith professional workbench." in model_calls[0]
    assert "New section on professional CAT migration." in model_calls[1]

    # Verify telemetry metrics
    telemetry = final_state["incremental_retranslation_metrics"]
    assert telemetry["total_segments"] == 6
    assert telemetry["retranslated_segments"] == 2
    assert telemetry["skipped_segments"] == 4
    assert telemetry["cost_savings_ratio"] > 0.50

    savings = final_state["source_update_savings"]
    assert savings["api_calls_avoided"] == 4
    assert savings["api_calls_required"] == 2
    assert savings["saved_cost_estimate"] > 0

    assert final_state["p2_done"] is True


def test_end_to_end_docx_source_update(tmp_path, monkeypatch):
    """AC: DOCX 源更新端到端对齐与增量重译。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "job-e2e-docx"

    docx_v1 = FIXTURES_DIR / "sample_v1.docx"
    docx_v2 = FIXTURES_DIR / "sample_v2.docx"

    units_v1, _ = extract_roundtrip_units(docx_v1.read_bytes())
    paras_v1 = [getattr(u, "source_plain", str(u)) for u in units_v1]
    anchors_v1 = [getattr(u, "source_anchor", None) for u in units_v1]

    pairs_v1 = [
        {
            "source": src,
            "target": f"DOCX译文: {src}",
            "initial_target": f"DOCX译文: {src}",
            "reviewed": True,
            "review_status": "reviewed",
            "segment_uid": f"seg-docx-{i:04d}",
        }
        for i, src in enumerate(paras_v1)
    ]

    state_v1 = {
        "job_id": job_id,
        "paras": paras_v1,
        "pairs": pairs_v1,
        "segment_source_anchors": anchors_v1,
        "review_stats": {"reviewed_segments": len(paras_v1), "total_segments": len(paras_v1)},
        "delivery_status": "delivered",
        "p1_done": True,
        "p2_done": True,
        "excluded_segments": [],
    }
    core.save_source(job_id, docx_v1.read_bytes())
    core.save_job_state(job_id, state_v1)

    units_v2, _ = extract_roundtrip_units(docx_v2.read_bytes())
    paras_v2 = [getattr(u, "source_plain", str(u)) for u in units_v2]
    anchors_v2 = [getattr(u, "source_anchor", None) for u in units_v2]

    # Generate plan with DOCX anchors
    plan = create_source_update_plan(state_v1, paras_v2, anchors_v2=anchors_v2, job_id=job_id)
    assert plan.summary["suspected_count"] == 1

    moved = next(e for e in plan.entries if e.classification == Classification.SUSPECTED_CHANGED)
    plan.resolve(moved.entry_id, Resolution.CONFIRM_REUSE)

    updated_state = apply_source_update_plan(
        job_id,
        plan,
        new_source_bytes=docx_v2.read_bytes(),
    )

    retranslated = retranslate_incremental(job_id)
    assert retranslated["p2_done"] is True
    assert retranslated["incremental_retranslation_metrics"]["skipped_segments"] == 4
    assert retranslated["incremental_retranslation_metrics"]["retranslated_segments"] == 2
