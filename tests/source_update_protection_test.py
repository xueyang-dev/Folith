"""Test protection invariants: zero overwrite of reviewed/locked segments and ledger integrity."""
import copy
import pytest
import core
from folith.source_update.plan import Resolution, create_source_update_plan
from folith.source_update.executor import apply_source_update_plan, assert_protection_invariants


def _build_protected_state():
    return {
        "job_id": "job-protect-test",
        "paras": [
            "P1: Unchanged preamble.",
            "P2: Human reviewed and locked translation.",
            "P3: Segment that will be edited in v2.",
            "P4: Segment that will be deleted in v2.",
            "P5: Unchanged postamble with human edit.",
        ],
        "pairs": [
            {
                "source": "P1: Unchanged preamble.",
                "target": "第 1 段：已审译文（首段）",
                "reviewed": True,
                "review_status": "reviewed",
                "segment_uid": "seg-u0001",
            },
            {
                "source": "P2: Human reviewed and locked translation.",
                "target": "第 2 段：人工精修并锁定译文（严禁覆盖）",
                "reviewed": True,
                "review_status": "reviewed",
                "locked": True,
                "human_accepted": True,
                "segment_uid": "seg-u0002",
            },
            {
                "source": "P3: Segment that will be edited in v2.",
                "target": "第 3 段：待修改段的旧译文",
                "reviewed": True,
                "review_status": "reviewed",
                "segment_uid": "seg-u0003",
            },
            {
                "source": "P4: Segment that will be deleted in v2.",
                "target": "第 4 段：即将废弃删除的译文",
                "reviewed": True,
                "locked": True,
                "segment_uid": "seg-u0004",
            },
            {
                "source": "P5: Unchanged postamble with human edit.",
                "target": "第 5 段：末段人工改写译文",
                "reviewed": True,
                "review_status": "reviewed",
                "human_edited": True,
                "target_provenance": "human_edit",
                "segment_uid": "seg-u0005",
            },
        ],
        "review_stats": {"reviewed_segments": 5, "total_segments": 5},
        "delivery_status": "delivered",
        "excluded_segments": [],
    }


def test_zero_overwrite_on_unchanged_reviewed_and_locked_segments(tmp_path, monkeypatch):
    """AC: 已审与 locked 段在任何自动路径下零覆盖（测试断言）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "job-zero-overwrite"
    state = _build_protected_state()
    core.save_source(job_id, b"source v1")
    core.save_job_state(job_id, state)

    v2 = [
        "P1: Unchanged preamble.",  # Unchanged, reviewed
        "P2: Human reviewed and locked translation.",  # Unchanged, locked
        "P3: Segment that has been modified slightly in v2.",  # Changed
        # P4 deleted
        "P5: Unchanged postamble with human edit.",  # Unchanged, human edit
        "P6: Brand new section added in v2.",  # Added
    ]

    plan = create_source_update_plan(state, v2, job_id=job_id)
    assert not plan.has_unresolved_suspected

    updated_state = apply_source_update_plan(job_id, plan)

    # Assertions on unchanged segments
    p1_updated = updated_state["pairs"][0]
    assert p1_updated["source"] == "P1: Unchanged preamble."
    assert p1_updated["target"] == "第 1 段：已审译文（首段）"
    assert p1_updated["reviewed"] is True
    assert p1_updated["segment_uid"] == "seg-u0001"

    p2_updated = updated_state["pairs"][1]
    assert p2_updated["source"] == "P2: Human reviewed and locked translation."
    assert p2_updated["target"] == "第 2 段：人工精修并锁定译文（严禁覆盖）"
    assert p2_updated["reviewed"] is True
    assert p2_updated["locked"] is True
    assert p2_updated["human_accepted"] is True
    assert p2_updated["segment_uid"] == "seg-u0002"

    p5_updated = updated_state["pairs"][3]
    assert p5_updated["source"] == "P5: Unchanged postamble with human edit."
    assert p5_updated["target"] == "第 5 段：末段人工改写译文"
    assert p5_updated["reviewed"] is True
    assert p5_updated["human_edited"] is True
    assert p5_updated["segment_uid"] == "seg-u0005"


def test_deleted_segments_appear_in_exclusion_ledger(tmp_path, monkeypatch):
    """AC: 删除段出现在台账，不静默消失。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "job-ledger-test"
    state = _build_protected_state()
    core.save_source(job_id, b"source v1")
    core.save_job_state(job_id, state)

    v2 = [
        "P1: Unchanged preamble.",
        "P2: Human reviewed and locked translation.",
        "P3: Segment that will be edited in v2.",
        "P5: Unchanged postamble with human edit.",
    ]
    # P4 deleted

    plan = create_source_update_plan(state, v2, job_id=job_id)
    updated_state = apply_source_update_plan(job_id, plan, actor="qa_auditor")

    excluded = updated_state.get("excluded_segments") or []
    assert len(excluded) == 1

    rec = excluded[0]
    assert rec["source"] == "P4: Segment that will be deleted in v2."
    assert rec["target"] == "第 4 段：即将废弃删除的译文"
    assert rec["reason"] == "source_update_deleted"
    assert rec["excluded_by"] == "qa_auditor"
    assert rec["pair"]["locked"] is True
    assert rec["pair"]["segment_uid"] == "seg-u0004"


def test_statistics_and_delivery_status_consistency(tmp_path, monkeypatch):
    """AC: 统计（进度、段数、状态）更新一致。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "job-stats-test"
    state = _build_protected_state()
    state["delivery_status"] = "delivered"
    core.save_source(job_id, b"source v1")
    core.save_job_state(job_id, state)

    v2 = [
        "P1: Unchanged preamble.",
        "P2: Human reviewed and locked translation.",
        "P3: Segment that has been edited in v2.",
        "P6: Added segment.",
    ]

    plan = create_source_update_plan(state, v2, job_id=job_id)
    updated_state = apply_source_update_plan(job_id, plan)

    # Invariants
    assert len(updated_state["paras"]) == len(v2)
    assert len(updated_state["pairs"]) == len(v2)

    stats = updated_state["review_stats"]
    assert stats["total_segments"] == 4
    assert stats["reviewed_segments"] == 2  # P1 and P2 only

    # Delivered status transitions to draft because new document requires translation
    assert updated_state["delivery_status"] == "draft"
    assert updated_state["p2_done"] is False  # Translation needed
