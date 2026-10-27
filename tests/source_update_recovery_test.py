"""Tests for interrupt recovery, pre-update snapshots, and failure rollback (#24/#74 pattern)."""
import copy
import pytest
import core
from tests.source_update_helpers import source_args
from folith.source_update.plan import create_source_update_plan
from folith.source_update.executor import (
    apply_source_update_plan,
    create_source_update_snapshot,
    recover_source_update,
    rollback_source_update,
    snapshot_dir,
)


def _init_job(tmp_path, job_id="job-recovery-test"):
    core.OUTPUT_DIR = tmp_path
    state = {
        "job_id": job_id,
        "paras": [
            "Paragraph 1: Original text.",
            "Paragraph 2: Original text.",
            "Paragraph 3: Original text.",
        ],
        "pairs": [
            {"source": "Paragraph 1: Original text.", "target": "段落 1：译文", "reviewed": True, "segment_uid": "u001"},
            {"source": "Paragraph 2: Original text.", "target": "段落 2：译文", "reviewed": True, "segment_uid": "u002"},
            {"source": "Paragraph 3: Original text.", "target": "段落 3：译文", "reviewed": True, "segment_uid": "u003"},
        ],
        "review_stats": {"reviewed_segments": 3, "total_segments": 3},
        "delivery_status": "delivered",
        "p1_done": True,
        "p2_done": True,
    }
    core.save_source(job_id, b"original source bytes")
    core.save_job_state(job_id, state)
    return state


def test_pre_update_snapshot_creation(tmp_path, monkeypatch):
    """AC: 保留更新前任务快照。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "snap-test"
    state = _init_job(tmp_path, job_id)

    manifest = create_source_update_snapshot(job_id, state)
    assert manifest["snapshot_id"] == "snap-0001"
    assert manifest["para_count"] == 3
    assert manifest["pair_count"] == 3

    s_root = snapshot_dir(core.job_dir(job_id))
    snap_folder = s_root / "snap-0001"
    assert snap_folder.exists()
    assert (snap_folder / "state.json").exists()
    assert (snap_folder / "manifest.json").exists()
    assert (snap_folder / "source.bin").exists()
    assert (snap_folder / "source.bin").read_bytes() == b"original source bytes"


def test_rollback_restores_pre_update_state(tmp_path, monkeypatch):
    """AC: 失败回滚行为：回退到更新前状态。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "rollback-test"
    original_state = _init_job(tmp_path, job_id)

    v2 = [
        "Paragraph 1: Original text.",
        "Paragraph 2: Modified text in v2.",
        "Paragraph 4: Brand new paragraph.",
    ]

    plan = create_source_update_plan(original_state, v2, job_id=job_id)
    for e in plan.entries:
        if e.classification == "suspected_changed":
            plan.resolve(e.entry_id, "split_add_delete")

    updated_state = apply_source_update_plan(job_id, plan, **source_args(job_id, plan, b"v2 source bytes"))

    # Confirmed updated
    assert len(updated_state["paras"]) == 3
    assert updated_state["paras"][1] == "Paragraph 2: Modified text in v2."
    assert core.load_source(job_id) == b"v2 source bytes"

    # Roll back
    restored = rollback_source_update(job_id)
    assert len(restored["paras"]) == 3
    assert restored["paras"][0] == "Paragraph 1: Original text."
    assert restored["paras"][1] == "Paragraph 2: Original text."
    assert restored["paras"][2] == "Paragraph 3: Original text."
    assert restored["pairs"][1]["target"] == "段落 2：译文"
    assert restored["pairs"][1]["reviewed"] is True
    assert core.load_source(job_id) == b"original source bytes"


def test_crash_recovery_no_duplicate_no_segment_loss(tmp_path, monkeypatch):
    """AC: 执行中断 → 重启恢复：无双重应用、无段落丢失（复用恢复测试模式）。"""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "crash-recovery-test"
    original_state = _init_job(tmp_path, job_id)

    v2 = [
        "Paragraph 1: Original text.",
        "Paragraph 2: Modified text in v2.",
        "Paragraph 4: Brand new paragraph.",
    ]

    plan = create_source_update_plan(original_state, v2, job_id=job_id)
    for e in plan.entries:
        if e.classification == "suspected_changed":
            plan.resolve(e.entry_id, "split_add_delete")

    # 1. Simulate crash mid-update
    with pytest.raises(RuntimeError, match="Simulated crash during update execution"):
        apply_source_update_plan(job_id, plan, simulate_crash_at="mid_update", **source_args(job_id, plan))

    # State on disk has pending transaction marker
    crashed_state = core.load_job_state(job_id)
    assert crashed_state.get("source_update_transaction") is not None
    assert crashed_state["source_update_transaction"]["status"] == "applying"

    # 2. Recovery on system restart
    recovered_state = recover_source_update(job_id)
    assert recovered_state.get("source_update_transaction") is None
    # No duplicate segments, no lost segments: matches original state exactly
    assert len(recovered_state["paras"]) == 3
    assert len(recovered_state["pairs"]) == 3
    assert [p["source"] for p in recovered_state["pairs"]] == [
        "Paragraph 1: Original text.",
        "Paragraph 2: Original text.",
        "Paragraph 3: Original text.",
    ]
    assert [p["target"] for p in recovered_state["pairs"]] == [
        "段落 1：译文",
        "段落 2：译文",
        "段落 3：译文",
    ]

    # 3. Idempotent: repeated recovery does nothing
    repeated_recovered = recover_source_update(job_id)
    assert len(repeated_recovered["paras"]) == 3
    assert len(repeated_recovered["pairs"]) == 3
