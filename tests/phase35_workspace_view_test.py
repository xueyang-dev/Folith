"""Phase 3.5 cross-page human-facing state invariants."""
import json

from folith.translation_core import fingerprint
from folith.workspace_view import (
    ai_configuration_view,
    delivery_review_gate_copy,
    history_copy,
    project_workspace_state,
    task_ai_ready,
)


def _state(count=2, *, review_required=True):
    return {
        "p2_done": True,
        "translation_core_review_required": review_required,
        "paras": [f"Source {index}" for index in range(count)],
        "pairs": [{"source": f"Source {index}", "target": f"译文 {index}"}
                  for index in range(count)],
        "findings": [],
        "review_evidence": [],
        "human_actions": [],
    }


def _event(segment_id, event_id, *, stale=False):
    target = f"译文 {segment_id}"
    return {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": event_id,
        "segment_ids": [segment_id],
        "freshness_status": "stale" if stale else "current",
        "stale_segment_ids": [segment_id] if stale else [],
        "stale_reason": "当前译文已修改" if stale else "",
        "completion_receipt": {
            "status": "completed",
            "reviewed_segment_ids": [segment_id],
        },
        "translation_core": {
            "final_consumed_input_fingerprint": fingerprint(
                {"segment": segment_id, "target": target}),
                "review_truth": [{
                    "segment_id": segment_id,
                    "source": f"Source {segment_id}",
                    "target": target,
                    "target_checked": True,
                }],
        },
    }


def test_clean_review_projection_is_shared_and_read_only():
    state = _state()
    state["review_evidence"] = [_event(0, "review-0"), _event(1, "review-1")]
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)

    view = project_workspace_state(state, delivery_state="可以准备交付",
                                   delivery_ready=True)
    history = history_copy(state, delivery_state="可以准备交付")

    assert view["stage_label"] == "可以准备交付"
    assert view["segment_status"] == {0: "已审校", 1: "已审校"}
    assert view["review_coverage"] == 2
    assert history == {"status": "可以交付", "detail": "译文已完成审校",
                       "action": "查看交付"}
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before


def test_stale_review_has_one_rereview_vocabulary_across_surfaces():
    state = _state()
    state["review_evidence"] = [_event(0, "review-0", stale=True),
                                _event(1, "review-1")]

    view = project_workspace_state(state)
    history = history_copy(state)

    assert view["review_readiness"]["status"] == "stale"
    assert view["segment_status"][0] == "需要重新审校"
    assert view["segment_status"][1] == "已审校"
    assert history["status"] == "需要重新审校"
    assert history["action"] == "继续审校"


def test_no_review_job_never_claims_independent_review():
    state = _state(review_required=False)

    view = project_workspace_state(state)
    history = history_copy(state)

    assert view["stage_label"] == "已翻译"
    assert view["review_readiness"]["status"] == "not_required"
    assert view["segment_status"] == {0: "已翻译", 1: "已翻译"}
    assert history["status"] == "已翻译"
    assert "审校" not in history["detail"]


def test_ai_projection_separates_missing_credentials_from_connection_failure():
    missing = ai_configuration_view(
        "DeepSeek", "audit-model", "", "unverified")
    failed = ai_configuration_view(
        "DeepSeek", "audit-model", "secret", "error")
    connected = ai_configuration_view(
        "DeepSeek", "audit-model", "secret", "connected")

    assert missing["state"] == "credentials_missing"
    assert missing["label"] == "API 凭据未配置"
    assert missing["recovery_action"]["target"] == "settings"
    assert failed["state"] == "error"
    assert failed["label"] == "连接失败"
    assert connected["ready"] is True
    assert connected["label"] == "连接正常"


def test_task_ai_readiness_ignores_unused_reviewer_configuration():
    kwargs = {
        "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI",
        "reviewer_model": "reviewer-model",
        "reviewer_api_key": "",
        "reviewer_connection_status": "unverified",
    }
    no_review = ai_configuration_view(
        "DeepSeek", "translator-model", "translator-key", "connected",
        review_required=False, **kwargs)
    needs_review = ai_configuration_view(
        "DeepSeek", "translator-model", "translator-key", "connected",
        review_required=True, **kwargs)

    assert no_review["state"] == "connected"
    assert task_ai_ready(no_review) is True
    assert needs_review["state"] == "credentials_missing"
    assert task_ai_ready(needs_review) is False


def test_task_ai_readiness_requires_reviewer_only_when_review_is_enabled():
    ready_translation = ai_configuration_view(
        "DeepSeek", "translator-model", "translator-key", "connected",
        reviewer_mode="separate", reviewer_provider="OpenAI",
        reviewer_model="reviewer-model", reviewer_api_key="",
        review_required=False)
    blocked_review = ai_configuration_view(
        "DeepSeek", "translator-model", "translator-key", "connected",
        reviewer_mode="separate", reviewer_provider="OpenAI",
        reviewer_model="reviewer-model", reviewer_api_key="",
        review_required=True)

    assert task_ai_ready(ready_translation) is True
    assert task_ai_ready(blocked_review) is False


def test_no_review_delivery_gate_never_claims_review_completion():
    no_review = delivery_review_gate_copy(False, True, "unused detail")
    reviewed = delivery_review_gate_copy(True, True)

    assert no_review == {
        "status": "不适用",
        "detail": "当前任务未启用独立审校（翻译审校不适用）",
    }
    assert "翻译审校已完成" not in no_review["detail"]
    assert reviewed["status"] == "已完成"
    assert "独立审校已完成" in reviewed["detail"]


def test_actionable_and_informational_findings_are_visible_without_blocking_delivery():
    state = _state(1)
    state["review_evidence"] = [_event(0, "review-0")]
    value = fingerprint({"segment": 0, "target": "译文 0"})
    state["findings"] = [{
        "type": "review",
        "finding_id": "finding-actionable",
        "input_fingerprint": value,
        "review_event_id": "review-0",
        "segment_index": 0,
        "segment_id": 0,
        "severity": "actionable",
        "status": "open",
        "summary": "建议检查表达",
    }]

    view = project_workspace_state(state)

    assert view["review_readiness"]["ready"] is True
    assert view["actionable_count"] == 1
    assert view["segment_status"][0] == "建议检查"
