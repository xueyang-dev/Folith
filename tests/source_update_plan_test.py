"""Unit tests for Source Update Change Plan, preview, and human confirmation."""
import pytest
import core
from folith.source_update.alignment import Classification
from folith.source_update.plan import (
    Resolution,
    SourceUpdateEntry,
    SourceUpdatePlan,
    create_source_update_plan,
)
from folith.source_update.executor import apply_source_update_plan


def _sample_state():
    return {
        "job_id": "job-plan-test",
        "paras": [
            "Section 1: Introduction",
            "Section 2: Architecture",
            "Section 3: Legacy notice to delete",
            "Section 4: Conclusion",
        ],
        "pairs": [
            {"source": "Section 1: Introduction", "target": "第 1 节：引言", "reviewed": True, "segment_uid": "u0001"},
            {"source": "Section 2: Architecture", "target": "第 2 节：架构", "reviewed": True, "locked": True, "segment_uid": "u0002"},
            {"source": "Section 3: Legacy notice to delete", "target": "第 3 节：废弃声明", "reviewed": False, "segment_uid": "u0003"},
            {"source": "Section 4: Conclusion", "target": "第 4 节：结论", "reviewed": True, "segment_uid": "u0004"},
        ],
        "review_stats": {"reviewed_segments": 3, "total_segments": 4},
        "delivery_status": "draft",
    }


def test_create_plan_and_summary_metrics():
    state = _sample_state()
    v2 = [
        "Section 1: Introduction",  # Unchanged
        "Section 2: Architecture and Overview",  # Changed (minor edit)
        "Section 4: Conclusion",  # Unchanged
        "Section 5: New Appendix",  # Added
    ]
    # Section 3 is Deleted

    plan = create_source_update_plan(state, v2, job_id="job-plan-test")
    assert plan.v1_count == 4
    assert plan.v2_count == 4
    assert plan.summary["unchanged_count"] == 2
    assert plan.summary["changed_count"] == 0
    assert plan.summary["deleted_count"] == 1
    assert plan.summary["added_count"] == 1
    assert plan.summary["suspected_count"] == 1
    assert plan.has_unresolved_suspected
    protected = next(e for e in plan.entries if e.suspected_reason == "protected_source_changed")
    plan.resolve(protected.entry_id, Resolution.CONFIRM_RETRANSLATE)
    assert plan.summary["changed_count"] == 1
    assert plan.summary["reviewed_preserved_count"] == 2
    assert plan.cost_estimate["reused_chars"] > 0
    assert plan.cost_estimate["estimated_savings_ratio"] > 0.4


def test_plan_with_moved_segment_has_unresolved_suspected():
    state = _sample_state()
    v2 = [
        "Section 4: Conclusion",  # Moved from end to beginning
        "Section 1: Introduction",  # Unchanged
        "Section 2: Architecture",  # Unchanged
    ]

    plan = create_source_update_plan(state, v2, job_id="job-plan-test")
    assert plan.has_unresolved_suspected is True
    assert plan.summary["suspected_count"] == 1
    assert plan.summary["unresolved_suspected_count"] == 1

    suspected_entry = next(e for e in plan.entries if e.classification == Classification.SUSPECTED_CHANGED)
    assert suspected_entry.suspected_reason == "moved_segment"
    assert suspected_entry.v2_source == "Section 4: Conclusion"


def test_unresolved_suspected_blocks_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "blocked-job"
    state = _sample_state()
    core.save_source(job_id, b"dummy source")
    core.save_job_state(job_id, state)

    v2 = [
        "Section 4: Conclusion",  # Moved -> suspected
        "Section 1: Introduction",
    ]
    plan = create_source_update_plan(state, v2, job_id=job_id)
    assert plan.has_unresolved_suspected is True

    # AC: 人工确认后才执行。存在未确认疑似项时拒绝执行
    with pytest.raises(ValueError, match="存在未确认的疑似变更段落"):
        apply_source_update_plan(job_id, plan)


def test_resolving_suspected_enables_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "resolve-job"
    state = _sample_state()
    core.save_source(job_id, b"dummy source")
    core.save_job_state(job_id, state)

    v2 = [
        "Section 4: Conclusion",  # Moved -> suspected
        "Section 1: Introduction",
    ]
    plan = create_source_update_plan(state, v2, job_id=job_id)
    suspected_entry = next(e for e in plan.entries if e.classification == Classification.SUSPECTED_CHANGED)

    # Human confirms to reuse translation at new position
    plan.resolve(suspected_entry.entry_id, Resolution.CONFIRM_REUSE, note="确认移动后仍适用原译文")
    assert not plan.has_unresolved_suspected
    assert plan.summary["unresolved_suspected_count"] == 0

    # Execution now succeeds!
    updated_state = apply_source_update_plan(job_id, plan)
    assert updated_state["paras"][0] == "Section 4: Conclusion"
    assert updated_state["pairs"][0]["target"] == "第 4 节：结论"
    assert updated_state["pairs"][0]["reviewed"] is True


def test_plan_serialization_roundtrip():
    state = _sample_state()
    v2 = ["Section 1: Introduction", "Section 5: New"]
    plan = create_source_update_plan(state, v2, job_id="job-plan-test")

    data = plan.to_dict()
    restored = SourceUpdatePlan.from_dict(data)

    assert restored.job_id == plan.job_id
    assert restored.summary == plan.summary
    assert len(restored.entries) == len(plan.entries)
    assert restored.markdown_preview().startswith("# 源文版本更新预览计划")
