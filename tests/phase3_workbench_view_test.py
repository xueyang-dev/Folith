"""Phase 3 human-facing review workbench projection regressions."""
import json

from folith import delivery
from folith.translation_core import fingerprint, normalize_finding
from folith.workbench_view import (
    filter_queue_items,
    next_queue_item_id,
    review_workbench_view,
    select_queue_item,
)


def _event(segment_id, event_id, value, *, decision="clean", stale=False):
    receipt_status = "failed" if decision == "failed" else "completed"
    event = {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_id],
        "decision": decision,
        "freshness_status": "stale" if stale else "current",
        "stale_segment_ids": [segment_id] if stale else [],
        "completion_receipt": {
            "status": receipt_status,
            "reviewed_segment_ids": [] if decision == "failed" else [segment_id],
        },
        "translation_core": {"final_consumed_input_fingerprint": value},
    }
    if stale:
        event.update(stale_reason="persisted translation truth changed after review",
                     stale_at="2026-08-30T00:28:00+00:00")
    return event


def _finding(segment_id, event_id, value, *, severity="blocking",
             status="open", summary="疑似遗漏限定语", span="限定语",
             location_key=""):
    finding = normalize_finding({
        "category": "completeness",
        "severity": severity,
        "status": status,
        "segment_id": segment_id,
        "requires_human_confirmation": severity == "blocking",
        "summary": summary,
        "source_span": span,
        "location_key": location_key,
        "explanation": "当前译文没有体现原文限定关系。",
        "recommendation": "补充限定关系后重新审校。",
    }, input_fingerprint=value)
    finding.update({
        "type": "review",
        "segment_index": segment_id,
        "review_event_id": event_id,
        "reason": summary,
    })
    return finding


def _state(count=1):
    return {
        "translation_core_review_required": True,
        "p2_done": True,
        "pairs": [{"source": f"Source {index}", "target": f"译文 {index}"}
                  for index in range(count)],
        "findings": [],
        "review_evidence": [],
        "human_actions": [],
    }


def test_clean_current_is_human_readable_and_read_only():
    state = _state()
    value = fingerprint({"segment": 0, "version": 1})
    state["review_evidence"] = [_event(0, "review-current", value)]
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)

    view = review_workbench_view(state)

    assert view["readiness"]["label"] == "当前译文已完成审校"
    assert view["progress"] == {
        "total": 1, "current": 1, "skipped": 0, "stale": 0, "missing": 0,
        "failed": 0, "blocking": 0, "actionable": 0, "informational": 0,
    }
    assert view["primary_action"]["kind"] == "delivery"
    assert view["delivery"]["title"] == "翻译审校已完成"
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before


def test_stale_review_becomes_a_rereview_task_before_current_findings():
    state = _state(2)
    stale_value = fingerprint({"segment": 0, "version": 1})
    current_value = fingerprint({"segment": 1, "version": 1})
    state["review_evidence"] = [
        _event(0, "review-stale", stale_value, stale=True),
        _event(1, "review-current", current_value, decision="findings"),
    ]
    state["findings"] = [_finding(1, "review-current", current_value)]

    view = review_workbench_view(state)

    assert view["readiness"]["label"] == "译文已变化，需要重新审校"
    assert [item["kind"] for item in view["queue_items"]] == ["stale", "finding"]
    assert view["primary_action"]["label"] == "重新审校 1 段"
    assert view["nav"]["label"] == "1 需复审"


def test_failed_review_is_first_and_explains_retry():
    state = _state()
    value = fingerprint({"failed": True})
    state["review_evidence"] = [_event(0, "review-failed", value, decision="failed")]

    view = review_workbench_view(state)

    assert view["readiness"]["label"] == "审校未完成"
    assert view["queue_items"][0]["kind"] == "failed"
    assert view["primary_action"]["label"] == "重试失败的 1 段"
    assert view["delivery"]["detail"] == "1 段审校未完成，请重试"


def test_missing_review_is_a_synthetic_task():
    view = review_workbench_view(_state())

    assert view["readiness"]["label"] == "尚未审校"
    assert view["queue_items"][0]["kind"] == "missing"
    assert view["queue_items"][0]["title"] == "第 1 段 · 尚未审校"
    assert view["filter_counts"]["pending"] == 1
    assert view["risk_acceptance"]["available"] is False


def test_current_blocking_finding_drives_the_primary_action():
    state = _state()
    value = fingerprint({"finding": "blocking"})
    state["review_evidence"] = [_event(0, "review-blocking", value,
                                            decision="findings")]
    state["findings"] = [_finding(0, "review-blocking", value)]

    view = review_workbench_view(state)

    assert view["readiness"]["status"] == "missing"
    assert view["queue_counts"]["blocking"] == 1
    assert view["primary_action"]["kind"] == "handle_finding"
    assert view["risk_acceptance"]["available"] is True
    assert "必须处理" in view["primary_action"]["label"]


def test_historical_stale_and_superseded_findings_are_not_current_work():
    state = _state()
    old_value = fingerprint({"version": 1})
    current_value = fingerprint({"version": 2})
    old = _finding(0, "review-old", old_value)
    old.update(status="stale", stale_reason="target changed",
               superseded_by_review_event_id="review-current")
    superseded = _finding(0, "review-superseded", old_value,
                          summary="旧的开放问题")
    superseded["superseded_by_review_event_id"] = "review-current"
    current = _finding(0, "review-current", current_value,
                       summary="当前问题")
    state["review_evidence"] = [
        _event(0, "review-old", old_value, stale=True),
        _event(0, "review-current", current_value, decision="findings"),
    ]
    state["findings"] = [old, superseded, current]

    view = review_workbench_view(state)

    finding_items = [item for item in view["queue_items"]
                     if item["kind"] == "finding"]
    assert len(finding_items) == 1
    assert finding_items[0]["summary"] == "当前问题"


def test_same_logical_id_historical_version_cannot_steal_selection():
    state = _state()
    old_value = fingerprint({"version": "old"})
    current_value = fingerprint({"version": "current"})
    old = _finding(0, "review-old", old_value, summary="旧问题")
    current = _finding(0, "review-current", current_value, summary="当前问题")
    current["finding_id"] = old["finding_id"]
    old.update(status="stale", superseded_by_review_event_id="review-current")
    state["review_evidence"] = [
        _event(0, "review-old", old_value, stale=True),
        _event(0, "review-current", current_value, decision="findings"),
    ]
    state["findings"] = [old, current]

    items = review_workbench_view(state)["queue_items"]

    assert len(items) == 1
    assert items[0]["review_event_id"] == "review-current"


def test_informational_finding_remains_optional_for_delivery():
    state = _state()
    value = fingerprint({"info": True})
    state["review_evidence"] = [_event(0, "review-info", value,
                                            decision="findings")]
    state["findings"] = [_finding(
        0, "review-info", value, severity="informational",
        summary="可参考的表达建议")]

    view = review_workbench_view(state)

    assert view["readiness"]["ready"] is True
    assert view["queue_counts"]["informational"] == 1
    assert view["delivery"]["title"] == "翻译审校已完成"
    assert view["primary_action"]["kind"] == "delivery"


def test_current_accepted_risk_is_explained_without_erasing_the_finding():
    state = _state()
    value = fingerprint({"risk": True})
    state["review_evidence"] = [_event(0, "review-risk", value,
                                            decision="findings")]
    state["findings"] = [_finding(0, "review-risk", value)]

    approved, ok, errors = delivery.approve_delivery(
        state, note="决定继续交付", actor="alice", accept_blocking=True)
    view = review_workbench_view(approved)

    assert ok and not errors
    assert view["readiness"]["ready"] is True
    assert view["risk_acceptance"]["current"] is True
    assert view["queue_counts"]["blocking"] == 0
    assert approved["findings"][0]["status"] == "open"


def test_v04_no_review_job_uses_legacy_delivery_path():
    state = {"pairs": [{"source": "A", "target": "甲"}],
             "findings": [], "human_actions": []}

    view = review_workbench_view(state)

    assert view["readiness"]["status"] == "not_required"
    assert view["readiness"]["label"] == "当前任务未启用独立审校"
    assert view["queue_items"] == []
    assert view["primary_action"]["kind"] == "delivery"


def test_multiple_findings_same_segment_have_meaningful_distinct_titles():
    state = _state()
    value = fingerprint({"duplicates": True})
    state["review_evidence"] = [_event(0, "review-multiple", value,
                                            decision="findings")]
    first = _finding(0, "review-multiple", value, span="planetarity",
                     location_key="source-offset:7")
    second = _finding(0, "review-multiple", value, span="scope",
                      location_key="source-offset:30")
    state["findings"] = [first, second]

    titles = [item["title"] for item in review_workbench_view(state)["queue_items"]]

    assert len(titles) == 2
    assert titles[0] != titles[1]
    assert all("另一个发现" not in title for title in titles)


def test_provisional_finding_identity_is_visible_but_not_decidable():
    state = _state()
    value = fingerprint({"provisional": True})
    state["review_evidence"] = [_event(0, "review-provisional", value,
                                            decision="findings")]
    finding = _finding(0, "review-provisional", value)
    finding["identity_stability"] = "provisional"
    state["findings"] = [finding]

    item = review_workbench_view(state)["queue_items"][0]

    assert item["kind"] == "finding"
    assert item["decidable"] is False


def test_filter_and_selection_helpers_choose_the_next_useful_item():
    items = [
        {"id": "failed:0", "filter_group": "rereview", "segment_id": 0},
        {"id": "finding:0", "filter_group": "pending", "segment_id": 0},
        {"id": "finding:1", "filter_group": "pending", "segment_id": 1},
    ]

    pending = filter_queue_items(items, "pending")
    assert [item["id"] for item in pending] == ["finding:0", "finding:1"]
    assert select_queue_item(pending, "filtered-out")["id"] == "finding:0"
    assert next_queue_item_id(items, "failed:0", segment_id=0) == "finding:0"
    assert next_queue_item_id(items, "finding:0", segment_id=0) == "failed:0"
    assert select_queue_item([], "anything") is None
    assert next_queue_item_id([], "anything") is None
