"""Readable activity timeline from the runtime event log (UI-framework neutral).

The Streamlit workspace and the Qt client both show "what happened" for a task.
The raw events are machine-shaped (``llm_request_started``, ``【排版解析】…``);
this turns them into short human sentences and folds consecutive duplicates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping

_EXACT = {
    "pipeline_resumed": "已从最近检查点继续",
    "llm_request_started": "已发送模型请求",
    "llm_response_received": "已收到模型响应",
    "job_completed": "任务已完成",
    "job_failed": "任务运行失败",
    "cancel_requested": "已请求取消任务",
    "job_cancelled": "任务已取消",
    "interrupted": "运行已中断，进度已保存",
    "stalled": "暂时没有新的运行信号",
    "retry_requested": "已准备重试当前步骤",
}

_KEYWORDS = (
    ("排版解析", "完成排版解析与段落重建"),
    ("文档画像", "完成文档画像"),
    ("全文语义理解", "完成全文语义理解"),
    ("智能抽取术语", "完成全文术语抽取"),
    ("批次翻译返回格式异常", "批量翻译格式异常，已切换为逐段处理"),
    ("双语翻译", "完成双语翻译"),
    ("自动标注", "完成自动标注"),
    ("研究模型", "完成研究模型"),
    ("文献证据", "完成文献证据整理"),
    ("报告", "完成报告生成"),
)

_FAILURE_EVENTS = frozenset({"job_failed", "interrupted", "stalled"})
_RUNNING_EVENTS = frozenset({"llm_request_started", "cancel_requested"})


def clip(value: Any, limit: int = 180) -> str:
    """Keep a line readable without leaking a transport payload into the UI."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return "—"
    # Provider responses can contain a whole paragraph after this marker; the raw
    # payload belongs in the technical log, not in a status line.
    text = re.split(r"\s*响应预览\s*[:：]", text, maxsplit=1)[0].strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clock(value: Any) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(value)[:19]


def event_label(event: Mapping[str, Any]) -> str:
    name = str(event.get("event") or "").strip()
    message = str(event.get("message") or "").strip()
    if name in _EXACT:
        return _EXACT[name]
    for needle, label in _KEYWORDS:
        if needle in message:
            return label
    return clip(re.sub(r"^【[^】]+】\s*", "", message), 86)


def activity_rows(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """Oldest-first rows ``{status, label, timestamp}``; repeats collapse to the latest."""
    rows: List[Dict[str, str]] = []
    for event in events:
        label = event_label(event)
        if not label or label == "—":
            continue
        name = str(event.get("event") or "")
        status = ("failed" if name in _FAILURE_EVENTS or "失败" in str(event.get("message") or "")
                  else "running" if name in _RUNNING_EVENTS else "completed")
        row = {"status": status, "label": label,
               "timestamp": clock(event.get("timestamp") or event.get("at"))}
        if rows and rows[-1]["label"] == label and rows[-1]["status"] == status:
            rows[-1] = row
        else:
            rows.append(row)
    return rows
