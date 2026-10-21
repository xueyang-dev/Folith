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

# Pictographs and joiners/variation selectors. Old task logs already hold
# messages such as "⚠️ 文档画像失败"; the panel draws its own status marks.
_EMOJI_RANGES = ((0x2139, 0x2139), (0x23E9, 0x23FF), (0x2600, 0x27BF), (0x2B50, 0x2B50),
                 (0x2B55, 0x2B55), (0x200D, 0x200D), (0xFE0E, 0xFE0F), (0x1F000, 0x1FAFF))
_EMOJI = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _EMOJI_RANGES) + "]")
# A step that was skipped or degraded does not block the task: show it as a warning.
_WARNING_WORDS = ("跳过", "降级", "部分完成", "格式异常", "未能")
_FAILURE_WORDS = ("失败", "出错", "错误")
_PROGRESS_SUFFIXES = ("...", "…", "……")


def strip_emoji(text: Any) -> str:
    """Drop pictographs and the internal "概要状态 model" suffix old logs still carry."""
    text = re.sub(r"\s*[·•]\s*概要状态\s*\S+", "", _EMOJI.sub("", str(text or "")))
    return re.sub(r"\s{2,}", " ", text).strip()


def message_status(name: str, message: str) -> str:
    """``failed`` / ``warning`` / ``running`` / ``completed`` for one event."""
    if name in _FAILURE_EVENTS or name == "client_error":
        return "failed"
    if name in _RUNNING_EVENTS:
        return "running"
    body = re.sub(r"^【[^】]+】\s*", "", message)
    if any(word in body for word in _WARNING_WORDS):
        return "warning"
    if any(word in body for word in _FAILURE_WORDS):
        return "failed"
    if body.startswith("正在") or body.rstrip().endswith(_PROGRESS_SUFFIXES):
        return "running"
    return "completed"


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
    message = strip_emoji(event.get("message"))
    if name in _EXACT:
        return _EXACT[name]
    # "完成 …" is only true for a message that reports success; a failure, skip or
    # still-running message keeps its own wording.
    finished = message_status(name, message) == "completed"
    for needle, label in _KEYWORDS:
        if needle in message and (finished or not label.startswith("完成")):
            return label
    return clip(re.sub(r"^【[^】]+】\s*", "", message), 86)


def activity_rows(events: Iterable[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """Oldest-first rows ``{status, label, timestamp}``; repeats collapse to the latest."""
    rows: List[Dict[str, str]] = []
    for event in events:
        client = event.get("event") in ("client_notice", "client_error")
        label = strip_emoji(event.get("message")) if client else event_label(event)
        if not label or label == "—":
            continue
        name = str(event.get("event") or "")
        status = message_status(name, strip_emoji(event.get("message")))
        row = {"status": status, "label": label,
               "detail": str((event.get("metadata") or {}).get("detail") or ""),
               "timestamp": clock(event.get("timestamp") or event.get("at"))}
        if rows and rows[-1]["label"] == label and rows[-1]["status"] == status:
            rows[-1] = row
        else:
            rows.append(row)
    return rows
