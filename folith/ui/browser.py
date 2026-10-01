"""Shared Object System: Project & Task Browser Components.

Provides unified domain objects, presentation models, and HTML markup
for the Project / Task Browser system across Folith:
  - ProjectRow (List View)
  - ProjectCard (Grid View)
  - TaskRow (Inbox & Project Task Lists)
  - InboxRow (Compact Inbox Strip)
"""
from __future__ import annotations

from html import escape
from typing import Any, Dict, List, Optional
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter


def extract_language_pair(jobs: List[Dict[str, Any]], default: str = "EN → ZH") -> str:
    """Extract human-readable language pair from a list of jobs."""
    if not jobs:
        return default
    pairs = []
    for job in jobs:
        state = job.get("state") or {}
        src = str(state.get("source_lang") or "").strip()
        tgt = str(state.get("target_lang") or "").strip()
        if not src and not tgt:
            continue
        src_label = "EN" if ("en" in src.lower() or "英" in src or not src) else src[:2].upper()
        tgt_label = "ZH" if ("zh" in tgt.lower() or "中" in tgt or not tgt) else tgt[:2].upper()
        pair = f"{src_label} → {tgt_label}"
        if pair not in pairs:
            pairs.append(pair)
    return " · ".join(pairs) if pairs else default


def task_language_pair(state: Dict[str, Any], default: str = "EN → ZH") -> str:
    """Extract language pair for a single task state."""
    state = state or {}
    src = str(state.get("source_lang") or "").strip()
    tgt = str(state.get("target_lang") or "").strip()
    src_label = "EN" if ("en" in src.lower() or "英" in src or not src) else src[:2].upper()
    tgt_label = "ZH" if ("zh" in tgt.lower() or "中" in tgt or not tgt) else tgt[:2].upper()
    return f"{src_label} → {tgt_label}"


def build_project_list_header_markup() -> str:
    """Render table column header for Project List view."""
    return (
        _h('<div class="tp-prow-header">'
        '<span class="tp-prow-h-name">项目</span>'
        '<span class="tp-prow-h-status">状态</span>'
        '<span class="tp-prow-h-tasks">任务</span>'
        '<span class="tp-prow-h-updated">最近更新</span>'
        '</div>')
    )


def build_project_row_markup(card: Dict[str, Any]) -> str:
    """List View row markup adhering to Linear/Finder desktop utility design."""
    name = escape(str(card.get("name") or "—"))
    status_chip = (_h('<span class="tp-chip is-archived">已归档</span>')
                   if card.get("archived") else "")
    lang_pair = escape(card.get("lang_pair") or "EN → ZH")

    activity = card.get("activity") or None
    if activity is not None:
        recent_title = escape(activity["title"])
        recent_meta = " · ".join(p for p in (activity.get("status"), activity.get("progress")) if p)
        recent_html = (
            (_h('<div class="tp-prow-recent"><span class="tp-prow-recent-label">最近</span><em title="') + str(recent_title) + '">' + str(recent_title) + '</em><span class="tp-prow-recent-meta">' + str(escape(" · ".join(_t(p) for p in (activity.get("status"), activity.get("progress")) if p))) + '</span></div>')
        )
        status_label = activity.get("status") or "进行中"
        status_tone = "success" if "可以" in status_label or "已完成" in status_label else (
            "warning" if "需要" in status_label or "待处理" in status_label else "info"
        )
    else:
        recent_html = _h('<div class="tp-prow-recent"><span class="tp-prow-recent-label">尚无任务</span></div>')
        status_label = "已归档" if card.get("archived") else "尚无任务"
        status_tone = "neutral"

    job_count = int(card.get("job_count") or 0)
    tasks_html = f'<strong>{job_count}</strong>' if job_count else "—"
    knowledge = escape(str(card.get("knowledge") or ""))
    updated_str = escape(str(card.get("updated") or "—"))
    updated_exact = escape(str(card.get("updated_exact") or ""))

    return (
        '<div class="tp-prow">'
        '<div class="tp-prow-main">'
        '<span class="tp-project-icon" aria-hidden="true">'
        '<span class="material-symbols-rounded">folder</span></span>'
        '<div class="tp-prow-copy">'
        '<div class="tp-prow-title">'
        f'<strong title="{name}">{name}</strong>'
        f'{status_chip}'
        f'<span class="tp-prow-lang">{lang_pair}</span>'
        '</div>'
        f'{recent_html}'
        '</div></div>'
        '<div class="tp-prow-status-col">'
        f'<span class="tp-status-chip is-{status_tone}">{escape(_t(status_label))}</span>'
        '</div>'
        '<div class="tp-prow-facts">'
        f'<span class="tp-prow-tasks">{tasks_html}</span>'
        + (f'<span class="tp-prow-knowledge">{knowledge}</span>' if knowledge else '') +
        '</div>'
        f'<span class="tp-prow-updated" title="{updated_exact}">{updated_str}</span>'
        '</div>'
    )


def build_project_card_markup(card: Dict[str, Any]) -> str:
    """Grid View compact card markup adhering to quiet object card hierarchy."""
    name = escape(str(card.get("name") or "—"))
    status_chip = (_h('<span class="tp-chip is-archived">已归档</span>')
                   if card.get("archived") else "")
    lang_pair = escape(card.get("lang_pair") or "英语 → 中文")
    description = str(card.get("description") or "").strip()
    desc_html = (f'<div class="tp-pcard-desc" title="{escape(description)}">'
                 f'{escape(description)}</div>' if description else "")

    job_count = int(card.get("job_count") or 0)
    activity = card.get("activity") or None
    is_empty = not job_count and not card.get("archived")

    if job_count:
        work_html = (
            ('<div class="tp-pcard-work"><span class="tp-pcard-tasks"><strong>' + str(job_count) + _h('</strong> 个任务</span>'))
            + (f'<span class="tp-pcard-breakdown">{escape(card["breakdown"])}</span>'
               if card.get("breakdown") else "")
            + '</div>'
        )
    else:
        work_html = (_h('<div class="tp-pcard-work is-quiet">'
                     '<span class="tp-pcard-tasks">尚无任务</span></div>'))

    recent_html = ""
    if activity is not None:
        meta = " · ".join(part for part in (activity.get("status"),
                                            activity.get("progress")) if part)
        recent_html = (
            (_h('<div class="tp-pcard-recent"><span class="tp-pcard-recent-label">最近</span><em title="') + str(escape(activity["title"])) + '">' + str(escape(activity["title"])) + '</em><span class="tp-pcard-recent-meta">' + str(escape(meta)) + '</span></div>')
        )
    elif is_empty:
        recent_html = _h('<div class="tp-pcard-hint">可开始积累术语与翻译记忆</div>')

    knowledge = escape(str(card.get("knowledge") or ""))
    cta_slot = '<span class="tp-pcard-cta-slot" aria-hidden="true"></span>'
    updated_str = escape(str(card.get("updated") or "—"))
    updated_exact = escape(str(card.get("updated_exact") or ""))

    return (
        f'<div class="tp-pcard{" is-empty" if is_empty else ""}">'
        '<div class="tp-pcard-head">'
        '<div class="tp-pcard-identity">'
        '<span class="tp-project-icon" aria-hidden="true">'
        '<span class="material-symbols-rounded">folder</span></span>'
        f'<strong title="{name}">{name}</strong>{status_chip}'
        '</div>'
        f'<div class="tp-pcard-lang">{lang_pair}</div>'
        '</div>'
        '<div class="tp-pcard-body">'
        f'{work_html}'
        f'{desc_html}'
        f'{recent_html}'
        '</div>'
        '<div class="tp-pcard-foot">'
        f'{cta_slot}'
        '<span class="tp-pcard-meta">'
        f'<span class="tp-pcard-knowledge">{knowledge}</span>'
        f'<span class="tp-pcard-updated" title="{updated_exact}">{updated_str}</span>'
        '</span>'
        '</div>'
        '</div>'
    )


def build_task_row_markup(row: Dict[str, Any]) -> str:
    """Task Row markup for Uncategorized Tasks and Project Tasks list."""
    title = escape(str(row.get("title") or "未命名任务"))
    filename = escape(str(row.get("filename") or ""))
    kind = escape(str(row.get("kind") or "DOCX"))
    lang_pair = escape(str(row.get("lang_pair") or "EN → ZH"))
    status = escape(str(row.get("status") or ""))
    tone = escape(str(row.get("tone") or "neutral"))
    total = int(row.get("total") or 0)
    percent = row.get("percent")
    updated = escape(str(row.get("updated") or "—"))

    # Status icon
    if tone == "success" or "交付" in status or "完成" in status:
        status_icon = "✓ "
    elif tone in ("danger", "warning") or "处理" in status or "失败" in status:
        status_icon = "! "
    else:
        status_icon = ""

    # Stats
    stats_parts = []
    if total:
        stats_parts.append(f"{total} 段")
    if percent:
        stats_parts.append(f"{percent}%")
    stats_text = escape(" · ".join(stats_parts)) if stats_parts else ""

    # Meta tags line: Kind · Lang Pair
    tags_text = f"{kind} · {lang_pair}" if kind else lang_pair

    # Secondary contextual hint if issue exists
    has_issue = tone in ("danger", "warning") or "处理" in status
    issue_action = (_h('<span class="tp-taskrow-issue-action">查看建议 →</span>')
                    if status == "可以准备交付" and has_issue else
                    _h('<span class="tp-taskrow-issue-action">查看问题 →</span>') if has_issue else "")

    meta_parts = [status]
    if row.get("stage"):
        meta_parts.append(f'阶段：{escape(str(row["stage"]))}')
    if percent:
        meta_parts.append(f'{percent}%')
    if total:
        meta_parts.append(f'{total} 段')
    if kind:
        meta_parts.append(kind)

    return (
        f'<div class="tp-taskrow" title="{filename}">'
        '<div class="tp-taskrow-head">'
        f'<div class="tp-taskrow-title">{title}</div>'
        f'<div class="tp-taskrow-tags">{tags_text}</div>'
        '</div>'
        '<div class="tp-taskrow-foot">'
        f'<span class="tp-taskrow-meta is-{tone}">'
        f'<span class="tp-taskrow-badge is-{tone}">{status_icon}{status}</span>'
        f'{issue_action}'
        f'<span class="tp-taskrow-segments">{stats_text}</span>'
        '</span>'
        f'<span class="tp-taskrow-time">{updated}</span>'
        '<span class="tp-taskrow-arrow" aria-hidden="true">→</span>'
        '</div>'
        '</div>'
    )


def build_uncategorized_strip_markup(job_count: int, meta: str) -> str:
    """Compact Inbox row markup for Project Center."""
    count_html = (('<span class="tp-uncat-count">' + str(job_count) + _h(' 个</span>'))
                  if job_count else _h('<span class="tp-uncat-count">0 个</span>'))
    return (
        (_h('<div class="tp-uncat"><span class="tp-uncat-icon" aria-hidden="true"><span class="material-symbols-rounded">inbox</span></span><div class="tp-uncat-copy"><div class="tp-uncat-head"><span class="tp-uncat-title">未分类任务<span class="tp-uncat-tag">Inbox</span></span><span class="tp-uncat-right">') + str(count_html) + _h('<span class="tp-uncat-action">查看 →</span></span></div><div class="tp-uncat-meta">') + str(escape(meta)) + '</div></div></div>')
    )
