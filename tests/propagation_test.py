"""Unit tests for identical-source propagation (Issue #151)."""
import pytest

from folith.propagation import (
    preview_source_propagation,
    stage_source_propagation,
    apply_source_propagation,
    REPLACEABLE_ROW,
    ALREADY_CORRECT,
    PROTECTED,
    EXCLUDED,
)


def test_propagation_categorization():
    pairs = [
        # 0: The initiating segment (current_index=0)
        {"source": "Hello world", "target": "你好世界", "segment_uid": "s0"},
        # 1: Identical source, different target -> replaceable
        {"source": "Hello world", "target": "世界你好", "segment_uid": "s1"},
        # 2: Identical source, already has target -> already_correct
        {"source": "Hello world", "target": "你好世界", "segment_uid": "s2"},
        # 3: Identical source, human_edited -> protected (MUST NEVER BE REPLACED)
        {"source": "Hello world", "target": "定制你好", "segment_uid": "s3", "human_edited": True},
        # 4: Identical source, human_accepted -> protected
        {"source": "Hello world", "target": "已审定", "segment_uid": "s4", "human_accepted": True},
        # 5: Identical source, locked -> protected
        {"source": "Hello world", "target": "锁定文", "segment_uid": "s5", "locked": True},
        # 6: Identical source, excluded -> excluded
        {"source": "Hello world", "target": "排除文", "segment_uid": "s6", "excluded": True},
        # 7: Different source -> not matched
        {"source": "Goodbye world", "target": "再见世界", "segment_uid": "s7"},
    ]

    preview = preview_source_propagation(
        "Hello world", "你好世界", pairs, current_index=0
    )

    assert preview["source_occurrences"] == 7  # 0, 1, 2, 3, 4, 5, 6
    assert len(preview["rows"]) == 1
    assert preview["rows"][0]["segment_index"] == 1
    assert preview["rows"][0]["before"] == "世界你好"
    assert preview["rows"][0]["after"] == "你好世界"

    assert preview["already_correct"] == [2]

    protected_indexes = [item["segment_index"] for item in preview["protected"]]
    assert protected_indexes == [3, 4, 5]

    assert preview["excluded"] == [6]


def test_protected_segments_never_staged():
    pairs = [
        {"source": "Important heading", "target": "重要标题 v1", "human_edited": True, "segment_uid": "p0"},
        {"source": "Important heading", "target": "重要标题 v2", "human_accepted": True, "segment_uid": "p1"},
        {"source": "Important heading", "target": "旧标题", "segment_uid": "p2"},
    ]

    committed_drafts = []

    def mock_load_state(job_id):
        return {"pairs": pairs}

    def mock_commit_candidates(job_id, drafts, actor="user"):
        committed_drafts.extend(drafts)
        return {"created": [{"candidate_id": "c1", "segment_index": d["segment_index"]} for d in drafts], "rejected": []}

    res = stage_source_propagation(
        "job-1", "Important heading", "新标题",
        load_state_fn=mock_load_state,
        commit_candidates_fn=mock_commit_candidates,
    )

    # Only segment 2 was staged! Segments 0 and 1 are protected and MUST NOT be in drafts.
    assert len(committed_drafts) == 1
    assert committed_drafts[0]["segment_index"] == 2
    assert committed_drafts[0]["candidate_target"] == "新标题"
    assert committed_drafts[0]["origin"] == "propagation"

    # Protected segments appear in preview summary for user clarity
    assert len(res["preview"]["protected"]) == 2


def test_cancellation_and_empty_propagation():
    pairs = [
        {"source": "One", "target": "一"},
        {"source": "Two", "target": "二"},
    ]

    # No identical sources exist
    preview = preview_source_propagation("One", "壹", pairs, current_index=0)
    assert len(preview["rows"]) == 0
    assert len(preview["protected"]) == 0

    # No drafts created
    res = stage_source_propagation(
        "job-1", "One", "壹", current_index=0,
        load_state_fn=lambda j: {"pairs": pairs},
        commit_candidates_fn=lambda j, d, actor: {"created": [], "rejected": []},
    )
    assert res["selected"] == 0
    assert len(res["created"]) == 0
