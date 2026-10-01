"""Read-only human-facing state projections for the Folith workspace.

The runtime and Translation Core remain authoritative.  This module only turns
their persisted values into copy, counts, and next actions shared by pages.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .workbench_view import review_workbench_view


VOCABULARY = {
    "review": "审校",
    "reviewed": "已审校",
    "stale": "需要重新审校",
    "failed": "运行失败",
    "not_required": "不适用",
    "blocking": "必须处理",
    "actionable": "建议检查",
    "informational": "参考",
    "locked": "已锁定",
    "frozen": "已冻结",
}

RECOVERY_ACTIONS = {
    "ai_credentials": {"label": "前往 AI 设置", "target": "settings"},
    "ai_connection": {"label": "修复 AI 配置", "target": "settings"},
    "review": {"label": "前往审校", "target": "review"},
    "rereview": {"label": "重新审校", "target": "review"},
    "report": {"label": "更新报告", "target": "report"},
    "delivery": {"label": "前往交付", "target": "delivery"},
}


def recovery_action(kind: str) -> Dict[str, str]:
    """Return a stable human-facing recovery action."""
    return dict(RECOVERY_ACTIONS.get(kind, RECOVERY_ACTIONS["delivery"]))


def _connection_state(*, provider: Any, model: Any, api_key: Any,
                      connection_status: Any) -> Dict[str, Any]:
    selected = bool(str(provider or "").strip() and str(model or "").strip())
    credentials = bool(str(api_key or "").strip())
    status = str(connection_status or "unverified")
    if not selected:
        state, label, tone = "not_selected", "尚未选择模型", "neutral"
    elif not credentials:
        state, label, tone = "credentials_missing", "API 凭据未配置", "warning"
    elif status == "connected":
        state, label, tone = "connected", "连接正常", "success"
    elif status == "error":
        state, label, tone = "error", "连接失败", "danger"
    else:
        state, label, tone = "unverified", "尚未验证连接", "warning"
    return {
        "provider_selected": selected,
        "model_selected": bool(str(model or "").strip()),
        "credentials_configured": credentials,
        "connection_status": status,
        "state": state,
        "label": label,
        "tone": tone,
    }


def ai_configuration_view(
    provider: Any, model: Any, api_key: Any,
    connection_status: Any = "unverified", *,
    reviewer_mode: str = "same", reviewer_provider: Any = None,
    reviewer_model: Any = None, reviewer_api_key: Any = None,
    reviewer_connection_status: Any = "unverified",
    review_required: bool = True,
) -> Dict[str, Any]:
    """Project translator and reviewer configuration without persisting state.

    ``review_required`` scopes readiness to the current task; settings and the
    sidebar keep the default global view so an optional reviewer can still be
    configured without blocking a translation-only task.
    """
    translator = _connection_state(
        provider=provider, model=model, api_key=api_key,
        connection_status=connection_status)
    if reviewer_mode == "same":
        reviewer = {**translator, "mode": "same", "label": "使用翻译模型"}
    else:
        reviewer = _connection_state(
            provider=reviewer_provider, model=reviewer_model,
            api_key=reviewer_api_key,
            connection_status=reviewer_connection_status)
        reviewer["mode"] = "separate"
    considered = [translator, reviewer] if review_required else [translator]
    if any(item["state"] == "error" for item in considered):
        overall_state, overall_label, tone = "error", "连接失败", "danger"
    elif any(item["state"] == "credentials_missing" for item in considered):
        overall_state, overall_label, tone = "credentials_missing", "API 凭据未配置", "warning"
    elif any(item["state"] == "unverified" for item in considered):
        overall_state, overall_label, tone = "unverified", "尚未验证连接", "warning"
    elif any(item["state"] == "not_selected" for item in considered):
        overall_state, overall_label, tone = "not_selected", "尚未选择模型", "neutral"
    else:
        overall_state, overall_label, tone = "connected", "连接正常", "success"
    action = (recovery_action("ai_connection") if overall_state == "error"
              else recovery_action("ai_credentials")
              if overall_state in {"credentials_missing", "not_selected"}
              else recovery_action("ai_connection")
              if overall_state == "unverified" else None)
    return {
        "translator": translator,
        "reviewer": reviewer,
        "reviewer_mode": reviewer_mode,
        "review_required": review_required,
        "state": overall_state,
        "label": overall_label,
        "tone": tone,
        "ready": overall_state == "connected",
        "recovery_action": action,
    }


def task_ai_ready(view: Mapping[str, Any]) -> bool:
    """Return whether the current task has the AI roles it actually uses."""
    translator = view.get("translator") or {}
    reviewer = view.get("reviewer") or {}
    return bool(
        translator.get("credentials_configured") and
        translator.get("model_selected") and
        (not view.get("review_required") or
         reviewer.get("credentials_configured") and reviewer.get("model_selected"))
    )


def delivery_review_gate_copy(
    review_required: bool, translation_gate_pass: bool, pending_detail: str = "",
) -> Dict[str, str]:
    """Keep delivery review copy truthful for both review policies."""
    if not review_required:
        return {"status": "不适用", "detail": "当前任务未启用独立审校（翻译审校不适用）"}
    if translation_gate_pass:
        return {"status": "已完成", "detail": "当前译文的独立审校已完成，可以继续准备交付"}
    return {"status": "需要处理", "detail": f"翻译审校：{pending_detail or '当前译文的审校尚未完成'}"}


def _translated_count(state: Mapping[str, Any]) -> int:
    return sum(bool(isinstance(pair, Mapping) and str(pair.get("target") or "").strip())
               for pair in state.get("pairs") or [])


def project_workspace_state(
    state: Mapping[str, Any], *, delivery_state: Optional[str] = None,
    delivery_tone: str = "neutral", delivery_ready: Optional[bool] = None,
    report_enabled: Optional[bool] = None, report_stale: bool = False,
) -> Dict[str, Any]:
    """Return the shared status/count/action model used by workspace pages."""
    state = state or {}
    review = review_workbench_view(state)
    readiness = review["readiness"]
    report_stale = bool(report_stale or
                        (state.get("dependency_impact") or {}).get("status") == "stale")
    pairs = state.get("pairs") or []
    total = len(state.get("paras") or pairs)
    translated = _translated_count(state)
    review_required = bool(readiness.get("required"))
    segment_status = {}
    current_ids = set(readiness.get("current_segment_ids") or [])
    stale_ids = set(readiness.get("stale_segment_ids") or [])
    failed_ids = set(readiness.get("failed_segment_ids") or [])
    missing_ids = set(readiness.get("missing_segment_ids") or [])
    findings_by_segment = {}
    for item in review["queue_items"]:
        if item.get("kind") == "finding":
            findings_by_segment.setdefault(item.get("segment_id"), []).append(item)
    for index, pair in enumerate(pairs):
        if not str(pair.get("target") or "").strip():
            label = "待翻译"
        elif not review_required:
            label = "已翻译"
        elif index in failed_ids:
            label = "审校未完成"
        elif index in stale_ids:
            label = "需要重新审校"
        elif index in missing_ids:
            label = "待审校"
        else:
            findings = findings_by_segment.get(index, ())
            if any(item.get("severity") == "blocking" for item in findings):
                label = "必须处理"
            elif any(item.get("severity") == "actionable" for item in findings):
                label = "建议检查"
            elif index in current_ids:
                label = "已审校"
            else:
                label = "待审校"
        segment_status[index] = label

    if not state.get("p2_done") or translated < total:
        stage, stage_label, next_action = "translation", "正在翻译", {"label": "继续翻译", "target": "translation"}
    elif review_required and not readiness.get("ready"):
        stage, stage_label = "review", readiness.get("label") or "需要审校"
        next_action = recovery_action("rereview" if readiness.get("status") in {"stale", "failed"} else "review")
    elif report_stale:
        stage, stage_label, next_action = "report", "报告需要更新", recovery_action("report")
    elif not review_required:
        stage, stage_label = "translated", "已翻译"
        next_action = recovery_action("delivery")
    elif delivery_ready:
        stage, stage_label, next_action = "delivery", delivery_state or "可以正式交付", recovery_action("delivery")
    else:
        stage, stage_label, next_action = "delivery", delivery_state or "可以准备交付", recovery_action("delivery")

    return {
        "stage": stage,
        "stage_label": stage_label,
        "next_action": next_action,
        "total_segments": total,
        "translated_segments": translated,
        "review_required": review_required,
        "review": review,
        "review_readiness": readiness,
        "review_coverage": len(current_ids),
        "stale_segments": len(stale_ids),
        "failed_segments": len(failed_ids),
        "missing_review_segments": len(missing_ids),
        "blocking_count": review["queue_counts"].get("blocking", 0),
        "actionable_count": review["queue_counts"].get("actionable", 0),
        "informational_count": review["queue_counts"].get("informational", 0),
        "delivery_state": delivery_state or "—",
        "delivery_tone": delivery_tone,
        "report_enabled": bool(state.get("report_enabled") if report_enabled is None else report_enabled),
        "report_stale": report_stale,
        "segment_status": segment_status,
    }


def history_copy(state: Mapping[str, Any], *, delivery_state: Optional[str] = None,
                 snapshot_current: bool = False) -> Dict[str, str]:
    """Human history card copy, deliberately excluding checkpoint telemetry."""
    view = project_workspace_state(state, delivery_state=delivery_state,
                                   delivery_ready=bool(delivery_state and
                                                       delivery_state.startswith("可以正式交付")))
    if snapshot_current:
        return {"status": delivery_state or "已冻结交付", "detail": "已生成不可变交付版本", "action": "查看交付"}
    if not state.get("p2_done") or view["translated_segments"] < view["total_segments"]:
        return {"status": "正在处理",
                "detail": (f"{view['translated_segments']} / {view['total_segments']} 段已完成"),
                "action": "打开"}
    if view["blocking_count"]:
        return {"status": "待审校", "detail": f"{view['blocking_count']} 个问题需要处理", "action": "继续审校"}
    if view["stale_segments"]:
        return {"status": "需要重新审校", "detail": f"{view['stale_segments']} 段译文需要复审", "action": "继续审校"}
    if view["review_required"] and not view["review_readiness"].get("ready"):
        return {"status": view["review_readiness"].get("label") or "待审校",
                "detail": view["review_readiness"].get("detail") or "还有内容需要处理", "action": "继续审校"}
    if view["report_stale"]:
        return {"status": "报告需要更新", "detail": "当前译文变化后，受影响的报告内容需要重建",
                "action": "更新报告"}
    if not view["review_required"]:
        return {"status": "已翻译", "detail": "当前译文有效，可以准备交付", "action": "查看交付"}
    if delivery_state in {"可以准备交付", "可以正式交付"}:
        return {"status": "可以交付", "detail": "译文已完成审校", "action": "查看交付"}
    return {"status": "已翻译", "detail": "当前译文已完成审校", "action": "查看交付"}
