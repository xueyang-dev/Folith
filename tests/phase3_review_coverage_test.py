"""Regression tests for truthful review coverage across human-facing surfaces."""
import json
from copy import deepcopy

from folith import assets, task_overview
from folith.engine.document_export import findings_report_md
from folith.translation_core import fingerprint
from folith.workbench_view import review_workbench_view
from folith.workspace_view import history_copy, project_workspace_state


def _state(count=10, *, required=True):
    return {
        "translation_core_review_required": required,
        "p2_done": True,
        "pairs": [{"source": f"Source {index}", "target": f"译文 {index}"}
                  for index in range(count)],
        "findings": [],
        "review_evidence": [],
        "human_actions": [],
    }


def _value(segment_id, version="v1"):
    return fingerprint({"segment": segment_id, "version": version})


def _current_event(segment_id, event_id, *, decision="clean"):
    return {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_id],
        "decision": decision,
        "freshness_status": "current",
        "stale_segment_ids": [],
        "completion_receipt": {
            "status": "completed",
            "reviewed_segment_ids": [segment_id],
        },
        "translation_core": {"final_consumed_input_fingerprint": _value(segment_id)},
    }


def _skipped_event(segment_id, event_id, reason):
    return {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_id],
        "decision": "skipped_low_risk",
        "skip_reason": reason,
        "freshness_status": "current",
        "stale_segment_ids": [],
        "completion_receipt": {
            "status": "skipped_low_risk",
            "reviewed_segment_ids": [],
        },
        "translation_core": {"final_consumed_input_fingerprint": _value(segment_id)},
    }


def _stale_event(segment_id, event_id):
    event = _current_event(segment_id, event_id)
    event.update(freshness_status="stale", stale_segment_ids=[segment_id],
                 stale_reason="当前译文已修改")
    return event


def _failed_event(segment_id, event_id):
    event = _current_event(segment_id, event_id, decision="failed")
    event["completion_receipt"] = {
        "status": "failed", "reviewed_segment_ids": []}
    return event


def _blocking_finding(segment_id, event_id):
    return {
        "type": "review",
        "finding_id": "finding-blocking",
        "input_fingerprint": _value(segment_id),
        "review_event_id": event_id,
        "segment_id": segment_id,
        "segment_index": segment_id,
        "severity": "blocking",
        "requires_human_confirmation": True,
        "status": "open",
        "summary": "必须确认的审校问题",
    }


def test_all_skipped_is_visible_coverage_not_completed_review():
    state = _state()
    state["review_evidence"] = [
        _skipped_event(index, f"skip-{index}", "clean_deterministic")
        for index in range(10)
    ]

    view = review_workbench_view(state)
    readiness = view["readiness"]

    assert readiness["status"] == "current"
    assert readiness["ready"] is True
    assert readiness["reviewed_count"] == 0
    assert readiness["skipped_count"] == 10
    assert readiness["coverage_complete"] is False
    assert readiness["coverage_status"] == "skipped"
    assert readiness["label"] == "10 段未执行本轮独立语义审校（按策略跳过）"
    assert "已完成审校" not in readiness["label"]
    assert view["progress"]["current"] == 0
    assert view["progress"]["skipped"] == 10
    assert [item["kind"] for item in view["queue_items"]] == ["skipped"] * 10
    assert view["filter_counts"]["skipped"] == 10
    assert view["delivery"]["tone"] == "warning"
    assert "已完成" not in view["delivery"]["title"]
    assert view["nav"]["label"] == "10 段跳过"
    assert view["nav"]["tone"] == "pending"

    workspace = project_workspace_state(state, delivery_state="可以准备交付",
                                        delivery_ready=True)
    assert workspace["segment_status"] == {
        index: "按策略跳过" for index in range(10)}
    assert workspace["review_skipped"] == 10
    assert workspace["review_coverage_complete"] is False
    assert "已完成审校" not in history_copy(
        state, delivery_state="可以准备交付")["detail"]

    overview = task_overview.derive_task_overview_state(state)
    review_stage = next(stage for stage in overview["stages"]
                        if stage["id"] == task_overview.STAGE_REVIEW)
    assert review_stage["state"] == task_overview.CURRENT
    assert "10 段未执行本轮独立语义审校（按策略跳过）" in review_stage["detail"]
    assert overview["review"]["skipped"] == 10
    assert overview["review"]["coverage_complete"] is False

    manifest = assets.build_delivery_manifest(state, "coverage-job")
    assert manifest["review_coverage"] == {
        "status": "skipped",
        "complete": False,
        "ready": True,
        "expected": 10,
        "reviewed": 0,
        "skipped": 10,
        "skipped_reasons": {"clean_deterministic": 10},
    }
    assert assets.validate_manifest(manifest, state) == []

    report = findings_report_md(state)
    assert "10 段未执行本轮独立语义审校（按策略跳过）" in report
    assert "审校覆盖状态：skipped" in report
    assert "已审校段落：0" in report


def test_all_current_reviews_remain_complete_and_ready():
    state = _state()
    state["review_evidence"] = [
        _current_event(index, f"review-{index}") for index in range(10)
    ]

    view = review_workbench_view(state)
    readiness = view["readiness"]

    assert readiness["status"] == "current"
    assert readiness["ready"] is True
    assert readiness["reviewed_count"] == 10
    assert readiness["skipped_count"] == 0
    assert readiness["coverage_complete"] is True
    assert readiness["coverage_status"] == "complete"
    assert readiness["label"] == "当前译文已完成审校"
    assert view["progress"]["current"] == 10
    assert view["progress"]["skipped"] == 0
    assert view["delivery"]["title"] == "翻译审校已完成"
    assert view["nav"]["tone"] == "done"
    assert not [item for item in view["queue_items"] if item["kind"] == "skipped"]


def test_mixed_current_and_skipped_is_partial_and_names_both_reasons():
    state = _state()
    events = [_current_event(index, f"review-{index}") for index in range(6)]
    events.extend([
        _skipped_event(6, "skip-6", "already_reviewed_tm"),
        _skipped_event(7, "skip-7", "already_reviewed_tm"),
        _skipped_event(8, "skip-8", "clean_deterministic"),
        _skipped_event(9, "skip-9", "clean_deterministic"),
    ])
    state["review_evidence"] = events

    view = review_workbench_view(state)
    readiness = view["readiness"]

    assert readiness["status"] == "current"
    assert readiness["ready"] is True
    assert readiness["reviewed_count"] == 6
    assert readiness["skipped_count"] == 4
    assert readiness["coverage_complete"] is False
    assert readiness["coverage_status"] == "partial"
    assert readiness["skipped_reason_counts"] == {
        "already_reviewed_tm": 2,
        "clean_deterministic": 2,
    }
    assert readiness["label"] == "部分译文未执行独立语义审校"
    assert "4 段未执行本轮独立语义审校（按策略跳过" in readiness["detail"]
    assert "复用已审校翻译记忆 2 段" in readiness["detail"]
    assert "无确定性风险信号 2 段" in readiness["detail"]
    assert view["progress"]["current"] == 6
    assert view["progress"]["skipped"] == 4
    assert view["delivery"]["title"] == "可以准备交付（审校覆盖不完整）"
    assert view["nav"]["label"] == "4 段跳过"

    history = history_copy(state, delivery_state="可以准备交付")
    assert history["status"] == "可以交付"
    assert "4 段未执行本轮独立语义审校（按策略跳过）" in history["detail"]
    assert "已完成审校" not in history["detail"]

    overview = task_overview.derive_task_overview_state(state)
    review_stage = next(stage for stage in overview["stages"]
                        if stage["id"] == task_overview.STAGE_REVIEW)
    assert review_stage["state"] == task_overview.CURRENT
    assert "4 段未执行本轮独立语义审校（按策略跳过）" in review_stage["detail"]


def test_review_not_required_has_no_coverage_claim():
    state = _state(3, required=False)

    view = review_workbench_view(state)

    assert view["readiness"]["status"] == "not_required"
    assert view["readiness"]["coverage_status"] == "not_required"
    assert view["readiness"]["reviewed_count"] == 0
    assert view["readiness"]["skipped_count"] == 0
    assert view["readiness"]["label"] == "当前任务未启用独立审校"
    assert view["queue_items"] == []
    assert view["progress"]["skipped"] == 0


def test_failed_stale_missing_and_blocking_never_look_completed():
    state = _state(4)
    state["review_evidence"] = [
        _failed_event(0, "review-failed"),
        _stale_event(1, "review-stale"),
        _current_event(3, "review-blocking", decision="findings"),
    ]
    state["findings"] = [_blocking_finding(3, "review-blocking")]

    view = review_workbench_view(state)
    readiness = view["readiness"]

    assert readiness["ready"] is False
    assert readiness["status"] == "failed"
    assert readiness["coverage_complete"] is False
    assert readiness["coverage_status"] == "incomplete"
    assert "已完成" not in readiness["label"]
    assert view["progress"] == {
        "total": 4, "current": 1, "skipped": 0, "stale": 1, "missing": 1,
        "failed": 1, "blocking": 1, "actionable": 0, "informational": 0,
    }
    kinds = [item["kind"] for item in view["queue_items"]]
    assert kinds.count("failed") == 1
    assert kinds.count("stale") == 1
    assert kinds.count("missing") == 1
    assert kinds.count("finding") == 1
    assert "已完成" not in view["delivery"]["title"]
    assert view["delivery"]["title"] == "暂时无法交付"

    overview = task_overview.derive_task_overview_state(state)
    review_stage = next(stage for stage in overview["stages"]
                        if stage["id"] == task_overview.STAGE_REVIEW)
    assert review_stage["state"] == task_overview.BLOCKED
    assert "未成功" in review_stage["detail"]


def test_old_skipped_events_load_without_new_persisted_truth():
    state = _state()
    state["review_evidence"] = [
        _skipped_event(index, f"skip-{index}", "clean_deterministic")
        for index in range(10)
    ]
    for event in state["review_evidence"]:
        event.pop("skip_reason", None)
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)

    loaded = json.loads(json.dumps(state))
    readiness = review_workbench_view(loaded)["readiness"]

    assert readiness["status"] == "current"
    assert readiness["ready"] is True
    assert readiness["skipped_count"] == 10
    assert readiness["coverage_status"] == "skipped"
    assert readiness["skipped_reason_counts"] == {"unknown": 10}
    assert json.dumps(loaded, ensure_ascii=False, sort_keys=True) == before
    assert deepcopy(loaded) == loaded
