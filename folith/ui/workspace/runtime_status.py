"""One presentation model for the live HUD and Run Details.

Runtime fields are authoritative; performance spans describe completed work.
No state reads or writes occur here, so both surfaces can use the same snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import re
from typing import Any
from folith.ui.i18n import tr as _t

_STAGE_LABELS = {
    "translation": "翻译 · 术语注入", "translating": "翻译 · 术语注入",
    "translation_pipeline": "翻译 · 术语注入", "terminology": "术语准备",
    "pipeline": "任务处理", "context_build": "翻译准备", "state_commit": "翻译 · 保存",
}


def clean_operation(value: Any) -> str:
    text = re.sub(r"^【[^】]+】\s*", "", str(value or "").strip())
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text)
    # Scheduler metadata remains available in the technical runtime snapshot.
    text = re.sub(r"[（(][^）)]*(?:并发|共\s*\d+\s*段|批次\s*\d+\s*/)[^）)]*[）)]", "", text)
    return text.strip(" .…")


def age_seconds(timestamp: Any, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return max(0, (now - value).total_seconds())
    except (ValueError, TypeError):
        return None


def elapsed_label(seconds: float) -> str:
    minutes, seconds = divmod(int(max(0, seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def relative_label(seconds: float | None) -> str:
    if seconds is None:
        return "暂无活动时间"
    if seconds < 60:
        return f"最近活动 {int(seconds)} 秒前"
    if seconds < 3600:
        return f"最近活动 {int(seconds // 60)} 分钟前"
    return f"最近活动 {int(seconds // 3600)} 小时前"


def runtime_presentation(view: dict, state: dict | None = None, *, now: datetime | None = None) -> dict:
    from folith.ui.workspace.performance_view import _map_stage_name, _map_stage_explanation
    now = now or datetime.now(timezone.utc)
    runtime = view.get("runtime") or {}
    status = view.get("status") or view.get("runtime_status") or runtime.get("status") or "idle"
    stage = str(runtime.get("stage_id") or runtime.get("stage") or view.get("stage")
                or view.get("stage_label") or view.get("pipeline_stage") or (state or {}).get("stage") or "pipeline").lower()
    operation_id = str(runtime.get("operation_id") or runtime.get("operation") or view.get("operation_id") or "")
    operation = clean_operation(runtime.get("operation_label") or view.get("current_operation")
                                or view.get("operation_label") or view.get("operation") or view.get("headline"))
    stage_label = _STAGE_LABELS.get(stage) or _map_stage_name(stage)
    detail = clean_operation(runtime.get("phase_label") or view.get("detail")) or _map_stage_explanation(stage)
    if operation_id == "context_build":
        stage_label = "翻译准备"
        detail = "正在加载前后文、术语和翻译记忆"
    elif operation_id == "translation_generation" and operation.startswith("正在翻译"):
        detail = "正在生成当前批次译文，进度按已保存批次数计算"
    elif operation_id in {"translation_qa", "translation_review"}:
        stage_label = "翻译 · 检查" if operation_id == "translation_qa" else "翻译 · 审校"
        detail = "正在检查译文质量，检查结束后保存当前批次"
    elif operation_id == "state_commit":
        stage_label = "翻译 · 保存"
        detail = "正在保存译文与可恢复的任务进度"
    operation = operation or _map_stage_name(stage)
    if status == "waiting_external":
        operation = (operation + " · 等待模型响应") if operation_id == "translation_retry" else "正在等待模型响应"
        detail = clean_operation(runtime.get("phase_label")) or "请求已发出，正在等待模型返回。段落完成并保存后，进度才会增加。"
    elif status == "pausing":
        operation, detail = "正在暂停任务", "当前操作结束后暂停，已完成的进度会保留"
    elif status == "paused":
        operation, detail = "任务已暂停", "点击继续任务，从当前操作继续处理"
    elif status == "cancelling":
        operation, detail = "正在取消任务", "正在收拢运行中的请求并保存进度"
    elif status == "completed":
        operation, detail = "任务已完成", "已完成全部步骤"
    elif status == "failed":
        operation = "当前步骤失败"
        error = runtime.get("error") or view.get("error") or {}
        detail = str(error.get("message") if isinstance(error, dict) else error) or detail

    if stage == "annotation" and status in {"running", "waiting_external"}:
        operation = "正在标注"

    progress = view.get("stage_progress") or runtime.get("stage_progress") or {}
    completed, total, unit = 0, 0, "段"
    if progress.get("total"):
        completed, total = int(progress.get("completed") or 0), int(progress["total"])
        unit = str(progress.get("unit") or "项")
    elif view.get("progress"):
        completed, total = map(int, view["progress"])
        unit = str(view.get("progress_unit") or "段")
        if (state or {}).get("total_batches") == total:
            unit = "批次"
    elif runtime.get("total_units"):
        completed, total = int(runtime.get("completed_units") or 0), int(runtime["total_units"])
        unit = str(runtime.get("progress_unit") or "项")
    if status == "completed" and total:
        completed = total
    completed, total = max(0, completed), max(0, total)
    completed = min(completed, total) if total else completed
    known = status == "completed" or (total > 0 and operation_id != "context_build")
    percent = 100.0 if status == "completed" else completed / total * 100 if known else None
    compact_unit = "批" if unit == "批次" else unit
    progress_label = f"{completed:,} / {total:,} {compact_unit}" if total else "进度待确认"
    is_paused = status in {"paused", "pausing"}
    is_stopped = status in {"stalled", "interrupted", "failed", "cancelled", "cancelling", "idle_incomplete"}
    if status == "completed" and not total:
        progress_label = "任务已完成"
    elif known:
        progress_label += f" · {percent:.1f}%"
        if status == "paused":
            progress_label += " · 已暂停"
        elif status == "pausing":
            progress_label += " · 正在暂停"
        elif status == "stalled":
            progress_label += " · 暂无信号"
        elif status == "interrupted":
            progress_label += " · 处理中断"
        elif status == "failed":
            progress_label += " · 运行失败"
        elif status == "cancelled":
            progress_label += " · 已取消"
        elif status == "cancelling":
            progress_label += " · 正在取消"
    else:
        if status == "paused":
            progress_label = "已暂停 · 进度待确认"
        elif status == "pausing":
            progress_label = "正在暂停 · 进度待确认"
        elif status == "stalled":
            progress_label = "暂无信号 · 进度待确认"
        elif status == "interrupted":
            progress_label = "处理中断 · 进度待确认"
        elif status == "failed":
            progress_label = "运行失败 · 进度待确认"
        elif status == "cancelled":
            progress_label = "已取消 · 进度待确认"
        elif status == "cancelling":
            progress_label = "正在取消 · 进度待确认"

    events = view.get("user_events") or view.get("events") or runtime.get("events") or []
    event_at = (events[-1].get("timestamp") or events[-1].get("at")) if events else None
    activity_at = runtime.get("last_activity_at") or view.get("last_activity_at") or event_at or runtime.get("last_progress_at")
    activity_age = age_seconds(activity_at, now)
    wait_age = age_seconds(runtime.get("model_wait_started_at"), now)
    freshness = relative_label(activity_age)
    if status == "waiting_external" and wait_age is not None:
        freshness = f"等待模型响应 {int(wait_age)} 秒"
    # Silence is observable; it is not proof of failure.
    quiet = status in {"running", "waiting_external"} and activity_age is not None and activity_age >= 180
    if quiet:
        freshness = f"较长时间没有新活动 · {freshness}"
    started = runtime.get("started_at")
    elapsed = age_seconds(started, now)
    ended = runtime.get("finished_at")
    if status in {"completed", "cancelled", "failed"}:
        end_age = age_seconds(ended, now)
        elapsed = (max(0, elapsed - end_age)
                   if elapsed is not None and end_age is not None else None)
    task_time = (f"任务运行 {elapsed_label(elapsed)}" if elapsed is not None else
                 "结束耗时未知" if status in {"completed", "cancelled", "failed"} else
                 "任务运行时间暂无记录")
    status_label = {
        "pausing": "正在暂停", "paused": "已暂停", "running": "正在运行", "waiting_external": "等待模型", "cancelling": "正在取消",
        "completed": "已完成", "failed": "运行失败", "starting": "准备中", "queued": "排队中",
        "resume_requested": "正在恢复", "cancelled": "已取消", "stalled": "暂无运行信号",
        "interrupted": "已中断", "idle_incomplete": "处理中断",
    }.get(status, view.get("status_label") or _map_stage_name(status))
    tone = (
        "danger" if status == "failed"
        else "paused" if is_paused
        else "warning" if (quiet or status in {"waiting_external", "cancelling", "stalled", "interrupted"})
        else "success" if status == "completed"
        else "active"
    )
    return dict(status=status, status_label=status_label, tone=tone, stage=stage,
                stage_label=stage_label, operation=operation, operation_id=operation_id,
                detail=detail, completed=completed, total=total, unit=unit, percent=percent,
                progress_known=known, progress_label=progress_label, freshness=freshness,
                task_time=task_time, activity_at=activity_at, activity_age=activity_age,
                is_paused=is_paused, is_stopped=is_stopped)


def progress_html(model: dict, *, drawer: bool = False) -> str:
    status = str(model.get("status") or "")
    is_paused = bool(model.get("is_paused") or status in {"paused", "pausing"})
    is_stopped = bool(model.get("is_stopped") or status in {"stalled", "interrupted", "failed", "cancelled", "cancelling", "idle_incomplete"})

    classes = ["tp-execution-progress"]
    if drawer:
        classes.append("is-drawer")
    if is_paused:
        classes.append("is-paused")
    elif is_stopped:
        classes.append("is-stopped")
        if status == "failed":
            classes.append("is-danger")
        elif status in {"stalled", "interrupted"}:
            classes.append("is-warning")
        elif status in {"cancelled", "cancelling"}:
            classes.append("is-cancelled")

    label = escape(model["progress_label"])

    if model["progress_known"]:
        classes_str = " ".join(classes)
        return (f'<div class="{classes_str}" role="progressbar" aria-valuemin="0" aria-valuemax="100" '
                f'aria-valuenow="{model["percent"]:.3f}" aria-label="{label}">'
                f'<i style="width:{model["percent"]:.3f}%"></i></div>')

    # Indeterminate progress
    classes.append("is-indeterminate")
    classes_str = " ".join(classes)
    if is_paused or is_stopped:
        aria_label = f"任务已暂停（{label}）" if is_paused else f"任务已停止（{label}）"
        return f'<div class="{classes_str}" role="progressbar" aria-label="{escape(aria_label)}"><i></i></div>'
    return f'<div class="{classes_str}" role="progressbar" aria-label="正在处理，进度未知"><i></i></div>'


def hud_html(model: dict) -> str:
    if model.get("status") in {"cancelled", "failed", "interrupted", "idle_incomplete"}:
        return (f'<div class="tp-runtime-hud is-compact is-{model["tone"]}" role="status" aria-live="polite">'
                f'<div class="tp-runtime-hud-top"><span class="tp-runtime-hud-state">'
                f'{escape(_t(model["status_label"]))}</span> '
                f'<span class="tp-runtime-hud-operation">{escape(_t(model["operation"]))}</span></div>'
                f'<div class="tp-runtime-hud-meta">{escape(model["progress_label"])} · '
                f'{escape(_t(model["task_time"]))}</div></div>')
    status_class = ""
    if model.get("is_paused"):
        status_class = "is-paused"
    elif model.get("is_stopped"):
        status_class = f"is-stopped is-{model.get('tone', 'warning')}"
    span_attr = f' class="{status_class}"' if status_class else ""
    return (f'<div class="tp-runtime-hud is-{model["tone"]}" role="status" aria-live="polite">'
            f'<div class="tp-runtime-hud-top"><span class="tp-runtime-hud-state"><i></i>{escape(_t(model["status_label"]))}</span>'
            f'<span class="tp-runtime-hud-stage">{escape(_t(model["stage_label"]))}</span></div>'
            f'<div class="tp-runtime-hud-operation">{escape(_t(model["operation"]))}</div>'
            f'<div class="tp-runtime-hud-progress">{progress_html(model)}<span{span_attr}>{escape(model["progress_label"])}</span></div>'
            f'<div class="tp-runtime-hud-meta">{escape(_t(model["freshness"]))} · {escape(_t(model["task_time"]))}</div></div>')
