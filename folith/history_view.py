"""Translation Task 列表卡片的只读投影。

这一页列出的是 **Translation Tasks**（一次具体文档翻译执行），不是 Projects。
Task 属于某个 Project 只是它的一个属性，不改变列表的对象语义——因此页面叫
「历史任务」，卡片动作叫「打开任务」。Project 有自己的入口（侧栏「项目」）。

每张卡片是一个可进入的工作对象，主标题来自 display name / 文档元数据（不是完整
源文件名），并且**只保留一个**按当前状态决定的 contextual CTA，放在卡片内部。

这个模块只做投影：不改状态、不渲染 UI。时间相关的判断要求调用方传入 `now`，
以便测试确定性。

CTA 的选择顺序是有意的，先解决"卡住流程"的问题：

    运行中       → 查看进度
    运行中断     → 继续处理（会真正恢复任务）
    未翻译完     → 继续翻译
    有 blocker   → 继续审校
    报告待更新   → 更新报告
    有交付快照   → 查看交付
    未冻结交付   → 准备交付
    其余         → 打开任务

其中"有交付快照"优先于"未冻结交付"：已冻结过的任务，用户最可能想看的是那份交付，
而不是再走一次准备流程。
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any, Dict, Mapping, Optional

from . import translation_planner as planner

# 卡片状态 chip → 语义色。颜色只在这里映射，UI 层不各自判断。
CHIP_TONES = {
    "blocking": "danger",
    "actionable": "warn",
    "report_stale": "warn",
    "interrupted": "warn",
    "running": "active",
    "translating": "active",
    "review_pending": "active",
    "review_required": "warn",
    "delivered": "done",
    "ready": "done",
    "neutral": "neutral",
}

# CTA → 工作台落点（任务工作台已取消「概览」路由：打开任务 = 落到翻译工作台，
# 全局状态摘要由任务 Banner 承担，续跑/重试动作也在那一条运行区里可见）。
#
# 注意兜底 CTA 必须是「打开任务」而不是「打开项目」：Project 与翻译任务是两个
# 实体，任务卡片用「打开项目」会让文案层面把两者混同（既有回归测试守住这条）。
CTA_DESTINATIONS = {
    "查看进度": "translation",
    "继续处理": "translation",
    "继续翻译": "translation",
    "继续审校": "review",
    "更新报告": "report",
    "查看交付": "delivery",
    "准备交付": "delivery",
    "打开任务": "translation",
}

_EXTENSION_LABELS = {
    "pdf": "PDF", "docx": "DOCX", "doc": "DOC", "md": "Markdown",
    "markdown": "Markdown", "txt": "TXT", "rtf": "RTF", "epub": "EPUB",
    "html": "HTML", "htm": "HTML",
}

# 文件名里常见的工序噪声，做标题时要剥掉
_NOISE_PATTERNS = (
    r"^\s*\d+\s*",                       # 前导序号
    r"\bPart\s*[0-9IVX]+\s*",
    r"提取自?",
    r"译文",
    r"translation",
    r"\bfinal\b",
    r"\bdraft\b",
    r"\bcopy\b",
)

_RUNNING = {"running", "waiting_external", "starting", "queued", "resume_requested"}
_BROKEN = {"failed", "interrupted", "stalled", "cancelled", "idle_incomplete"}

# 作者/年份这类括号尾注："(Author Name)" / "（Author Name, 2024）"
_PAREN_TAIL = re.compile(r"[（(]\s*([^（()）]{2,60}?)\s*[)）]\s*$")


def _text(value: Any) -> str:
    return unescape(str(value or "")).strip()


def document_title(state: Mapping[str, Any]) -> str:
    """卡片主标题：来自文档元数据，而不是完整源文件名。

    完整文件名（`Part 2提取Neural Machine Interface
    (Elena Rostova).docx`）不适合当标题：带工序前缀、带作者、带扩展名。
    这里剥掉这些噪声，只留书名。任何一步失败都回落到文件名主体，
    保证卡片永远有标题可显示。
    """
    raw = _text(state.get("filename"))
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", raw).strip()
    if not stem:
        return "未命名文档"
    title = stem
    # 作者括号尾注先留一份，后面作为次级信息
    match = _PAREN_TAIL.search(title)
    if match:
        title = title[:match.start()].strip()
    for pattern in _NOISE_PATTERNS:
        title = re.sub(pattern, "", title, flags=re.IGNORECASE)
    # 剥掉噪声词后可能留下重复分隔符（`audit-translation-in-progress` →
    # `audit--in-progress`），收敛成一个，否则标题看起来是坏掉的。
    title = re.sub(r"[-_]{2,}", "-", title)
    title = re.sub(r"\s+", " ", title).strip(" -–—_:·,，")
    return title or stem


def document_author(state: Mapping[str, Any]) -> str:
    """作者：优先文件名括号尾注；没有就留空（不编造）。

    必须先剥扩展名再匹配：`(Elena Rostova).docx` 的括号不在字符串末尾。
    """
    raw = _text(state.get("filename"))
    stem = re.sub(r"\.[A-Za-z0-9]{1,6}$", "", raw).strip()
    match = _PAREN_TAIL.search(stem)
    if not match:
        return ""
    candidate = match.group(1).strip()
    # "(2019)" 这种是年份不是作者
    if re.fullmatch(r"\d{4}(\s*[-–]\s*\d{4})?", candidate):
        return ""
    return candidate


def document_kind(state: Mapping[str, Any]) -> str:
    """文件类型：扩展名优先，其次文档画像的 genre。"""
    raw = _text(state.get("filename"))
    match = re.search(r"\.([A-Za-z0-9]{1,6})$", raw)
    if match:
        return _EXTENSION_LABELS.get(match.group(1).lower(),
                                     match.group(1).upper())
    profile = state.get("document_profile") or {}
    return _text(profile.get("genre")) if isinstance(profile, Mapping) else ""


def document_language(state: Mapping[str, Any]) -> str:
    return _text(state.get("target_lang")) or "简体中文"


# 内部标识形态：`audit-blocking-no-suggestion` / `ui_audit_stale_case`。
# 这类串是 fixture、job 或脚本的标识，不是给人看的标题。
_INTERNAL_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+){1,}$")
# 出现这些词的分隔串基本可以确定是开发/测试 fixture，而不是真实文档名。
_INTERNAL_MARKERS = frozenset({
    "audit", "fixture", "sample", "demo", "debug", "tmp", "temp",
    "test", "smoke", "dummy", "slug", "placeholder",
})


def is_internal_identifier(value: Any) -> bool:
    """这个字符串看起来是内部 identifier，而不是可读标题。

    只有"分隔串 + 内部标记词"才算：`audit-blocking-no-suggestion` 是 fixture，
    而 `mti-practice-report` 这类可能来自真实文件名的串不算——它会被
    `_humanize_slug` 变成可读标题，而不是被丢掉。
    """
    text = _text(value)
    if not text or " " in text or not _INTERNAL_ID_RE.match(text):
        return False
    return any(token in _INTERNAL_MARKERS
               for token in re.split(r"[-_]+", text.lower()) if token)


def _humanize_slug(text: str) -> str:
    """把 `field-notes-2026` 这类分隔串变成可读标题 `Field Notes 2026`。

    只处理分隔串；已经可读的标题原样返回，绝不改写真实文档名。
    """
    if " " in text or not _INTERNAL_ID_RE.match(text):
        return text
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", text) if part)


def display_name(state: Mapping[str, Any]) -> str:
    """卡片主标题：`display_name` 优先，其次文档元数据标题。

    优先级：

    1. `state["display_name"]`（显式的人类可读任务名）；
    2. `state["document_profile"]["display_name"]`；
    3. 由文件名派生的可读标题（`document_title`）。

    第 3 步若得到的是内部 identifier（`audit-blocking-no-suggestion`），返回空串
    表示"没有 display title"。调用方负责给出兜底标题——内部标识可以留在 hover
    提示与搜索里，但**不作为标题展示**。
    """
    explicit = _text(state.get("display_name"))
    if explicit:
        return explicit
    profile = state.get("document_profile") or {}
    if isinstance(profile, Mapping):
        explicit = _text(profile.get("display_name"))
        if explicit:
            return explicit
    title = document_title(state)
    if is_internal_identifier(title):
        return ""
    return _humanize_slug(title)


def segment_count(state: Mapping[str, Any]) -> int:
    pairs = state.get("pairs") or []
    if pairs:
        return len(pairs)
    return len(state.get("paras") or [])


def _translated_count(state: Mapping[str, Any]) -> int:
    return sum(
        1 for pair in state.get("pairs") or []
        if isinstance(pair, Mapping) and _text(pair.get("target")))


def format_age(value: Any, *, now: Any = None) -> str:
    """相对时间。无法解析时返回空串，由调用方回落到原始时间戳。"""
    from datetime import datetime, timezone

    text = _text(value)
    if not text:
        return ""
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if isinstance(current, str):
        try:
            current = datetime.fromisoformat(current.replace("Z", "+00:00"))
        except ValueError:
            current = datetime.now(timezone.utc)
    if getattr(current, "tzinfo", None) is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = (current - stamp).total_seconds()
    if seconds < 0:
        return "刚刚"
    if seconds < 90:
        return "刚刚"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    if seconds < 86400 * 30:
        return f"{int(seconds // 86400)} 天前"
    if seconds < 86400 * 365:
        return f"{int(seconds // (86400 * 30))} 个月前"
    return f"{int(seconds // (86400 * 365))} 年前"


def _has_report(state: Mapping[str, Any]) -> bool:
    """项目是否真的有报告下游产物。

    `dependency_impact` 只要译文变了就把所有已知下游标成 stale，**包括这个任务
    根本不适用的报告产物**。所以不能只看 dep 状态，否则纯翻译任务的历史卡上会
    挂出一个"更新报告"的 CTA，点进去却没有报告可更新——这是个真实的错误引导。
    """
    if state.get("report_enabled") or state.get("p3_done"):
        return True
    artifacts = (state.get("academic_state") or {}).get("artifacts") or {}
    return bool(artifacts)


def report_stale(state: Mapping[str, Any]) -> bool:
    if not _has_report(state):
        return False
    return (state.get("dependency_impact") or {}).get("status") == "stale"


def _counts(state: Mapping[str, Any], job_id: str) -> Dict[str, int]:
    findings = planner.plan_findings(state, job_id=job_id, limit=500)
    progress = planner.workspace_progress(state, job_id=job_id)
    return {
        "blocking": sum(1 for item in findings if item.get("severity") == "blocking"),
        "actionable": sum(1 for item in findings if item.get("severity") == "actionable"),
        "informational": sum(1 for item in findings
                             if item.get("severity") == "informational"),
        "translated": progress["translation"]["done"],
        "total": progress["translation"]["total"],
        "review_applicable": int(bool(progress["review"]["applicable"])),
        "review_done": progress["review"]["done"] if progress["review"]["applicable"] else 0,
    }


def _chip(*, kind: str, label: str, detail: str = "") -> Dict[str, str]:
    return {"kind": kind, "tone": CHIP_TONES.get(kind, "neutral"),
            "label": label, "detail": detail}


def _status_chip(state: Mapping[str, Any], job_id: str, runtime_status: str,
                 counts: Mapping[str, int], *, delivery_current: bool) -> Dict[str, str]:
    total, translated = counts["total"], counts["translated"]
    if runtime_status in _RUNNING:
        return _chip(kind="running", label="运行中",
                     detail=f"{translated} / {total} 段")
    if runtime_status in _BROKEN:
        return _chip(kind="interrupted", label="处理中断",
                     detail="已保留进度，可以继续")
    if not state.get("p2_done") or translated < total:
        return _chip(kind="translating", label="待翻译",
                     detail=f"{translated} / {total} 段")
    if counts["blocking"]:
        return _chip(kind="blocking", label="需要处理",
                     detail=f"{counts['blocking']} 个必须处理")
    if counts["actionable"]:
        return _chip(kind="actionable", label="建议检查",
                     detail=f"{counts['actionable']} 项建议")
    if report_stale(state):
        return _chip(kind="report_stale", label="报告需要更新",
                     detail="当前译文已变化")
    if (state.get("translation_core_review_required")
            and counts["review_applicable"]
            and counts["review_done"] < total):
        return _chip(kind="review_pending", label="待审校",
                     detail=f"{counts['review_done']} / {total} 段已审校")
    if delivery_current:
        return _chip(kind="delivered", label="已交付", detail="冻结交付与当前版本一致")
    return _chip(kind="ready", label="已完成", detail=f"{total} 段")


def _cta(state: Mapping[str, Any], chip: Mapping[str, str],
         counts: Mapping[str, int], *, delivery_current: bool) -> Dict[str, str]:
    kind = chip["kind"]
    if kind == "running":
        label = "查看进度"
    elif kind == "interrupted":
        label = "继续处理"
    elif kind == "translating":
        label = "继续翻译"
    elif kind == "blocking":
        label = "继续审校"
    elif kind == "report_stale":
        label = "更新报告"
    elif delivery_current:
        label = "查看交付"
    elif kind in {"delivered", "ready"} and state.get("p2_done"):
        label = "准备交付"
    else:
        label = "打开任务"
    return {"label": label, "destination": CTA_DESTINATIONS.get(label, "overview"),
            "resume": label == "继续处理"}


def history_card_view(
    state: Mapping[str, Any], *, job_id: str = "", runtime_status: str = "",
    delivery_label: str = "", delivery_current: bool = False,
    saved_at: str = "", now: Any = None, project_name: str = "",
    project_orphan: bool = False,
) -> Dict[str, Any]:
    """把持久状态投影成一张历史卡片。只读，不改 state。

    `project_orphan=True` 表示任务的 `project_id` 指向一个**已经不存在的项目
    记录**（容器被删了），而不是"用户主动选择没有长期归属"（那是未分类）。
    调用方负责给出相应的 `project_name` 文案；这里只把它透传出来，让卡片能把
    两者画得不一样。
    """
    state = state or {}
    counts = _counts(state, job_id)
    chip = _status_chip(state, job_id, _text(runtime_status), counts,
                        delivery_current=delivery_current)
    cta = _cta(state, chip, counts, delivery_current=delivery_current)
    profile = state.get("document_profile") or {}
    domain = _text(profile.get("domain")) if isinstance(profile, Mapping) else ""

    name = display_name(state)
    # 第二行固定四项：author / source type / target language / domain。
    # 项目归属**不**混进身份行——它属于"这个任务在哪个容器里"，不是任务的身份。
    identity = [part for part in (
        document_author(state),
        document_kind(state),
        document_language(state),
        domain,
    ) if part]

    parts = re.findall(r"\bPart\s*[0-9IVX]+\b", _text(state.get("filename")),
                       flags=re.IGNORECASE)
    return {
        "job_id": job_id,
        # title 永远是"给人看的标题"；没有可读标题时用兜底文案，绝不展示内部标识。
        "title": name or "未命名任务",
        "display_name": name,
        "part_label": parts[0].replace("part", "Part") if parts else "",
        "filename": _text(state.get("filename")),
        "identity": " · ".join(identity),
        "domain": domain,
        "project_name": _text(project_name),
        "project_orphan": bool(project_orphan),
        "chip": chip,
        "cta": cta,
        "updated_at": _text(saved_at),
        "updated_label": format_age(saved_at, now=now) or _text(saved_at) or "—",
        "progress": f"{counts['translated']} / {counts['total']} 段",
        "issue_count": counts["blocking"] + counts["actionable"],
        "blocking_count": counts["blocking"],
        "segment_count": counts["total"],
        "counts": counts,
        "delivery_label": _text(delivery_label),
    }


def card_matches(view: Mapping[str, Any], *, query: str = "", status: str = "全部") -> bool:
    """历史页的搜索/筛选谓词（放在这里以便测试，不依赖 Streamlit）。"""
    if status and status != "全部":
        kind = (view.get("chip") or {}).get("kind", "")
        if status == "需要处理" and kind not in {"blocking", "interrupted"}:
            return False
        if status == "建议检查" and kind != "actionable":
            return False
        if status == "进行中" and kind not in {"running", "translating", "review_pending"}:
            return False
        if status == "已完成" and kind not in {"delivered", "ready"}:
            return False
    needle = _text(query).casefold()
    if not needle:
        return True
    haystack = " ".join(_text(view.get(field)).casefold() for field in
                        ("title", "display_name", "filename", "identity", "domain",
                         "project_name", "part_label", "delivery_label"))
    return needle in haystack


def sort_key(view: Mapping[str, Any], order: str = "最近更新") -> Any:
    """排序键。'最近更新' 用可比较的降序字符串（ISO 时间戳）。"""
    if order == "任务名称":
        return _text(view.get("title")).casefold()
    if order == "状态":
        return ((view.get("chip") or {}).get("label") or "",)
    return _text(view.get("updated_at"))


__all__ = [
    "CHIP_TONES", "CTA_DESTINATIONS", "card_matches", "display_name",
    "document_author", "document_kind", "document_language", "document_title",
    "format_age", "history_card_view", "is_internal_identifier", "report_stale",
    "segment_count", "sort_key",
]
