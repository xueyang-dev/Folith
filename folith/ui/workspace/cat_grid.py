"""CAT Translation Workspace and Grid component with isolated fragment re-rendering.

Optimizations:
1. Viewport pagination: `CAT_GRID_ROWS = 40` (drastic reduction from 400).
2. Action Menu: only the selected row mounts its toolbar and overflow menu.
   The viewport remains bounded to 40 editors for large tasks.
3. Shared Fragment scope: CAT Grid and Inspector share ONE fragment boundary via
   `folith.ui.workspace.translation.render_translation_workspace_fragment`.
4. Robust Draft preservation: drafts survive viewport scrolling and filter toggles.
"""
from __future__ import annotations

import math
import unicodedata
from html import escape
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import streamlit as st

import core
from folith import profiler
from folith import translation_decisions as _decisions
from folith import translation_planner as _planner
from folith.textual import text_direction
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter

CAT_GRID_ROWS = 40


def _render_row_click_binding(job_id: str, index: int) -> None:
    """Make the non-interactive surface of a row select that row.

    Streamlit containers are not clickable widgets. Delegate clicks on the row
    surface to its existing segment button while leaving textareas, buttons,
    links, and popovers fully interactive.
    """
    row_key = f"cat_row_{job_id}_{index}"
    number_key = f"cat_num_{job_id}_{index}"
    st.html(
        f"""<script>
(function() {{
  const row = document.querySelector('[class*="st-key-{row_key}"]');
  if (!row || row.dataset.tpRowClickBound === '1') return;
  row.dataset.tpRowClickBound = '1';
  row.addEventListener('click', function(event) {{
    if (event.target.closest('button, textarea, input, select, a, [role="button"], [data-testid="stPopover"]')) return;
    const select = row.querySelector('[class*="st-key-{number_key}"] button');
    if (select && !select.disabled) select.click();
  }});
}})();
</script>""",
        unsafe_allow_javascript=True,
    )


def _estimated_editor_lines(text: str, *, units_per_line: int = 36) -> int:
    """Estimate wrapped lines for one CAT column without browser measurements.

    Source and target use different font metrics and usually differ in script
    density, so callers supply separate conservative column estimates.
    Explicit newlines start a new visual line and long lines wrap independently.
    """
    value = str(text or "")
    if not value:
        return 1
    explicit = value.splitlines() or [""]
    wrapped = 0
    for line in explicit:
        width = sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"}
                    else 1 for ch in line)
        wrapped += max(1, math.ceil(width / max(1, units_per_line)))
    return max(1, wrapped)


def _estimated_source_lines(text: str) -> int:
    # At the current CAT proportions, a narrow English source column wraps at
    # roughly fifty Latin glyphs. A slight safety margin covers narrower windows
    # and long unbroken words.
    return _estimated_editor_lines(text, units_per_line=48)


def _estimated_target_lines(text: str) -> int:
    # Chinese glyphs and the target editor's line-height need a tighter budget
    # than the old shared 78-unit estimate, which clipped ordinary long targets.
    return _estimated_editor_lines(text, units_per_line=30)


def _editor_height(text: str, *, lines: Optional[int] = None) -> int:
    lines = _estimated_target_lines(text) if lines is None else max(1, int(lines))
    if lines <= 1:
        return 44
    if lines == 2:
        return 64
    # 13px text at the CAT editor line-height is about 22px per visual line,
    # plus compact vertical padding. Do not cap this: the row must expose the
    # current translation instead of hiding the tail behind an internal scroll.
    return max(84, lines * 22 + 8)
_CAT_BASELINE_PREFIX = "cat_baseline_"
_TRANSLATION_SEGMENT_EDITOR_KEY = "translation_segment_editor_target"
_TRANSLATION_EDITOR_SEED = "translation_editor_seed"
_NAV_PENDING_SCROLL = "pending_scroll_segment_id"
_NAV_PENDING_FOCUS = "pending_focus_segment_id"
_NAV_NOTICE = "workspace_nav_notice"


def cat_status_tone(status: str) -> str:
    """Map segment review status to a compact visual tone class."""
    return {
        "已审校": "is-reviewed",
        "已审": "is-reviewed",
        "已修改": "is-edited",
        "已改": "is-edited",
        "待审校": "is-pending",
        "待审": "is-pending",
        "待翻译": "is-untranslated",
        "待译": "is-untranslated",
        "已翻译": "is-translated",
        "已译": "is-translated",
        "需要重新审校": "is-pending",
        "审校未完成": "is-pending",
        "必须处理": "is-issue",
        "建议检查": "is-pending",
    }.get(str(status or ""), "is-pending")


def render_translation_segment_actions(
    job_id: str,
    state: Dict[str, Any],
    index: int,
    pair: Dict[str, Any],
    *,
    is_active: bool = True,
    segment_id_fn: Optional[Callable[..., str]] = None,
    navigate_fn: Optional[Callable[..., Any]] = None,
    structure_edit_blocked_fn: Optional[Callable[..., Tuple[bool, str]]] = None,
    flush_draft_fn: Optional[Callable[..., Any]] = None,
    open_editor_fn: Optional[Callable[..., Any]] = None,
    purge_edit_state_fn: Optional[Callable[..., Any]] = None,
    structure_flash_fn: Optional[Callable[..., Any]] = None,
    workspace_flash_fn: Optional[Callable[..., Any]] = None,
    seed_editor_fn: Optional[Callable[..., Any]] = None,
    commit_row_fn: Optional[Callable[..., Any]] = None,
    retranslate_fn: Optional[Callable[..., Any]] = None,
    draft_for_fn: Optional[Callable[..., Any]] = None,
    drop_draft_fn: Optional[Callable[..., Any]] = None,
    reset_editor_fn: Optional[Callable[..., Any]] = None,
    api_ready: bool = False,
    editor_key_fn: Optional[Callable[..., str]] = None,
    go_to_neighbour_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """Render the selected row's overflow actions without moving focus."""
    editor_spec = st.session_state.get(_TRANSLATION_SEGMENT_EDITOR_KEY) or {}
    if str(editor_spec.get("job_id") or "") == str(job_id):
        return

    pair = pair if isinstance(pair, dict) else {}
    pairs = state.get("pairs") or []
    segment_id = segment_id_fn(job_id, index, pair) if segment_id_fn else (
        core.segment_uid(pair) or str(pair.get("segment_id") or index)
    )

    if structure_edit_blocked_fn:
        blocked, runtime_status = structure_edit_blocked_fn(job_id, state)
    else:
        runtime_view = core.get_job_runtime_status(job_id, state)
        runtime_status = runtime_view.get("status") or "idle"
        blocked = runtime_status in {"running", "waiting_external", "queued", "starting"}

    with st.popover(
            "", icon=":material/more_horiz:", help=_t("更多段落操作"),
            key=f"cat_actions_{job_id}_{index}", use_container_width=True):
        # Primary row tools are icons; cleanup and destructive actions stay in overflow.
        source_text = str(pair.get("source") or "").strip()
        empty_target = not str(pair.get("target") or "").strip()
        if st.button(
                _t("重译本单元"), icon=":material/refresh:",
                key=f"cat_action_retranslate_inline_{job_id}_{index}",
                disabled=blocked or not api_ready or not source_text,
                help=(_t("定点翻译需要先完成 AI 引擎配置" if not api_ready
                      else "翻译本单元" if empty_target else "重译本单元")),
                type="tertiary", width="content"):
            if retranslate_fn:
                retranslate_fn(job_id, index)

        if st.button(_t("编辑原文"), icon=":material/edit_note:", help=_t("编辑原文"), key=f"cat_action_source_{job_id}_{index}",
                     disabled=blocked, width="stretch"):
            if flush_draft_fn:
                flush_draft_fn(job_id, state, index, segment_id)
            if open_editor_fn:
                open_editor_fn(job_id, index, "source")
            st.rerun()
        if st.button(_t("拆分单元"), icon=":material/call_split:", help=_t("拆分单元"), key=f"cat_action_split_{job_id}_{index}",
                     disabled=blocked, width="stretch"):
            if flush_draft_fn:
                flush_draft_fn(job_id, state, index, segment_id)
            if open_editor_fn:
                open_editor_fn(job_id, index, "split")
            st.rerun()
        has_next = index + 1 < len(pairs)
        if st.button(_t("与下一单元合并"), icon=":material/call_merge:", help=_t("与下一单元合并"), key=f"cat_action_merge_{job_id}_{index}",
                     disabled=blocked or not has_next, width="stretch"):
            if flush_draft_fn:
                flush_draft_fn(job_id, state, index, segment_id)
                if has_next:
                    next_seg_id = segment_id_fn(job_id, index + 1, pairs[index + 1]) if segment_id_fn else (
                        core.segment_uid(pairs[index + 1]) or str(pairs[index + 1].get("segment_id") or (index + 1))
                    )
                    flush_draft_fn(job_id, state, index + 1, next_seg_id)
            if open_editor_fn:
                open_editor_fn(job_id, index, "merge")
            st.rerun()
        if st.button(_t("在下方插入空单元"), icon=":material/add:", help=_t("在下方插入空单元"), key=f"cat_action_insert_{job_id}_{index}",
                     disabled=blocked, width="stretch"):
            if flush_draft_fn:
                flush_draft_fn(job_id, state, index, segment_id)
            try:
                core.mutate_translation_segments(job_id, index, "insert")
            except (RuntimeError, ValueError, IndexError) as exc:
                st.error(_t(str(exc)))
            else:
                new_state = core.load_job_state(job_id) or state
                if purge_edit_state_fn:
                    purge_edit_state_fn(job_id, new_state)
                inserted_index = index + 1
                new_pairs = new_state.get("pairs") or []
                if inserted_index < len(new_pairs):
                    st.session_state["selected_segment_id"] = (
                        segment_id_fn(job_id, inserted_index, new_pairs[inserted_index])
                        if segment_id_fn else (
                            core.segment_uid(new_pairs[inserted_index])
                            or str(new_pairs[inserted_index].get("segment_id") or inserted_index)
                        )
                    )
                    st.session_state["selected_segment_index"] = inserted_index
                if structure_flash_fn:
                    structure_flash_fn("insert", index, inserted_index=inserted_index)
                if open_editor_fn:
                    open_editor_fn(job_id, inserted_index, "source")
                st.rerun()

        has_content = bool(str(pair.get("source") or "").strip()
                           or str(pair.get("target") or "").strip())
        if st.button(_t("排除单元"), icon=":material/visibility_off:",
                     key=f"cat_action_exclude_{job_id}_{index}",
                     disabled=blocked or not has_content, width="stretch",
                     help=_t("排除出翻译与交付；保留原文，可在「已排除」中恢复")):
            if flush_draft_fn:
                flush_draft_fn(job_id, state, index, segment_id)
            try:
                core.mutate_translation_segments(
                    job_id, index, "exclude", exclude_reason="人工判定为无效原文")
            except (RuntimeError, ValueError, IndexError) as exc:
                st.error(_t(str(exc)))
            else:
                fresh = core.load_job_state(job_id) or {}
                if purge_edit_state_fn:
                    purge_edit_state_fn(job_id, fresh)
                if workspace_flash_fn:
                    workspace_flash_fn(
                        f"第 {index + 1} 段已排除：不进入翻译与交付；"
                        "原文保留，可在「已排除」中恢复。", "success")
                st.rerun()

        draft = draft_for_fn(job_id, segment_id) if draft_for_fn else None
        if pair.get("manual_segment"):
            empty = not str(pair.get("source") or "").strip() and not str(
                pair.get("target") or "").strip()
            if st.button(_t("删除空单元"), key=f"cat_action_delete_{job_id}_{index}",
                         disabled=blocked or not empty, width="stretch"):
                try:
                    core.mutate_translation_segments(job_id, index, "delete")
                except (RuntimeError, ValueError, IndexError) as exc:
                    st.error(_t(str(exc)))
                else:
                    fresh = core.load_job_state(job_id) or {}
                    if purge_edit_state_fn:
                        purge_edit_state_fn(job_id, fresh)
                    if structure_flash_fn:
                        structure_flash_fn("delete", index)
                    st.rerun()

        if draft:
            st.markdown(_h('<div class="tp-cat-action-menu-title">未保存的修改</div>'),
                        unsafe_allow_html=True)
            if st.button(_t("保存这一段"), key=f"cat_action_save_{job_id}_{index}",
                         type="primary", width="stretch"):
                if commit_row_fn:
                    if commit_row_fn(
                            job_id, state, index, segment_id,
                            baseline=str(draft.get("baseline") or "")):
                        st.rerun()
            if st.button(_t("丢弃这一段未保存的修改"),
                         key=f"cat_action_discard_{job_id}_{index}",
                         width="stretch"):
                if drop_draft_fn:
                    drop_draft_fn(job_id, segment_id)
                if reset_editor_fn:
                    reset_editor_fn(segment_id)
                if workspace_flash_fn:
                    workspace_flash_fn("已丢弃该段落未保存的修改。", "info")
                st.rerun()

        if runtime_status == "completed":
            st.caption(_t("段落结构修改会使当前交付回到草稿状态。"))


def render_translation_row_actions(
    job_id: str,
    state: Dict[str, Any],
    index: int,
    segment_id: str,
    pair: Dict[str, Any],
    *,
    baseline: str,
    is_dirty: bool,
    anomaly: Optional[Tuple[str, str]] = None,
    commit_row_fn: Optional[Callable[..., Any]] = None,
    go_to_neighbour_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """Render save and advance icons for an inactive dirty row."""
    if st.button(_t("保存当前段"), icon=":material/save:", help=_t("保存这一段"), key=f"cat_save_btn_{job_id}_{index}",
                 type="primary" if is_dirty else "secondary",
                 width="stretch"):
        if commit_row_fn and commit_row_fn(
                job_id, state, index, segment_id, baseline=baseline):
            st.rerun()
    if st.button(_t("保存并进入下一未确认段"), icon=":material/keyboard_double_arrow_down:", help=_t("保存并下一段"), key=f"cat_save_next_btn_{job_id}_{index}",
                 width="stretch"):
        saved = commit_row_fn(
            job_id, state, index, segment_id,
            baseline=baseline, quiet=True) if commit_row_fn else False
        if go_to_neighbour_fn:
            go_to_neighbour_fn(job_id, state, index, +1, saved=saved)


def render_active_translation_row_actions(
    job_id: str,
    state: Dict[str, Any],
    index: int,
    segment_id: str,
    *,
    status: str,
    baseline: str,
    is_dirty: bool,
    save_fn: Optional[Callable[..., Any]] = None,
    save_next_fn: Optional[Callable[..., Any]] = None,
) -> None:
    """Render save and advance icons in the selected row toolbar."""
    if st.button(
            _t("保存当前段"), icon=":material/save:", key=f"cat_save_btn_{job_id}_{index}",
            type="primary" if is_dirty else "secondary",
            disabled=not is_dirty, width="stretch",
            help=_t("保存当前段；不代表已通过审校")):
        if save_fn and save_fn(
                job_id, state, index, segment_id, baseline=baseline):
            st.rerun()
    if st.button(
            _t("保存并进入下一未确认段"), icon=":material/keyboard_double_arrow_down:", key=f"translation_save_next_{job_id}_{index}",
            type="secondary", width="stretch",
            help=_t("保存并进入下一未确认段")):
        if save_next_fn:
            save_next_fn(
                job_id, state, index, segment_id,
                baseline=baseline, is_dirty=is_dirty)


def render_translation_row(
    job_id: str,
    state: Dict[str, Any],
    index: int,
    pair: Dict[str, Any],
    segment_id: str,
    selected_index: int,
    blocking_segments: set,
    *,
    status_label_fn: Optional[Callable[..., str]] = None,
    editor_key_fn: Optional[Callable[..., str]] = None,
    draft_for_fn: Optional[Callable[..., Any]] = None,
    row_anomaly_fn: Optional[Callable[..., Any]] = None,
    navigate_fn: Optional[Callable[..., Any]] = None,
    activate_fn: Optional[Callable[..., Any]] = None,
    editor_changed_fn: Optional[Callable[..., Any]] = None,
    row_actions_fn: Optional[Callable[..., Any]] = None,
    active_row_actions_fn: Optional[Callable[..., Any]] = None,
    segment_actions_fn: Optional[Callable[..., Any]] = None,
    qa_findings_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
) -> None:
    """Render a single CAT row: index + source + editable target + actions."""
    pair = pair if isinstance(pair, dict) else {}
    status = status_label_fn(pair, state, index) if status_label_fn else "待审校"
    cell_class = f"tp-cat-rowcell {cat_status_tone(status)}".strip()
    is_active = index == selected_index
    source_text = str(pair.get("source") or "").strip()
    source_class = "tp-cat-source" + (" is-empty" if not source_text else "")
    target_text = str(pair.get("target") or "")
    source_direction = text_direction(source_text, state.get("source_lang"))
    target_direction = text_direction(target_text, state.get("target_lang"))
    editor_key = editor_key_fn(segment_id) if editor_key_fn else f"translation_editor_{segment_id}"
    baseline_key = f"{_CAT_BASELINE_PREFIX}{editor_key}"

    seeds = st.session_state.get(_TRANSLATION_EDITOR_SEED)
    if isinstance(seeds, dict) and str(segment_id) in seeds:
        st.session_state.pop(baseline_key, None)
        seeded = seeds.pop(str(segment_id))
        if not seeds:
            st.session_state.pop(_TRANSLATION_EDITOR_SEED, None)
        else:
            st.session_state[_TRANSLATION_EDITOR_SEED] = seeds
    else:
        seeded = None

    draft = draft_for_fn(job_id, segment_id) if draft_for_fn else None
    if (seeded is None and draft is not None
            and editor_key not in st.session_state):
        st.session_state.pop(baseline_key, None)
        seeded = str(draft.get("text") or "")
    if editor_key not in st.session_state:
        st.session_state[baseline_key] = target_text
    baseline = st.session_state.get(baseline_key, target_text)
    current = (seeded if seeded is not None
               else st.session_state.get(editor_key, target_text))

    is_dirty = bool(draft) or str(current) != str(baseline)
    row_class = f"{cell_class}{' is-active' if is_active else ''}".strip()
    source_lines = _estimated_source_lines(source_text or "（空段落）")
    target_lines = _estimated_target_lines(str(current))
    density_lines = max(source_lines, target_lines)
    density_class = ("is-row-one" if density_lines <= 1 else
                     "is-row-two" if density_lines == 2 else "is-row-long")
    source_density_class = f"is-source-lines-{min(source_lines, 64)}"
    target_density_class = f"is-target-lines-{min(target_lines, 64)}"
    anomaly = row_anomaly_fn(
        pair, index, blocking_segments, is_dirty,
        bool(state.get("translation_core_review_required"))) if row_anomaly_fn else None

    issue_badge = (_h('<div class="tp-cat-badges">'
                   '<span class="tp-cat-badge is-issue">必须处理</span></div>')
                   if index in blocking_segments else "")

    with st.container(key=f"cat_row_{job_id}_{index}"):
        st.markdown(f'<span class="{row_class} {density_class} '
                    f'{source_density_class} {target_density_class}" '
                    f'data-segment="{index}"></span>',
                    unsafe_allow_html=True)
        number_col, source_col, target_col, status_col, notice_col = st.columns(
            [0.45, 3.1, 3.1, 0.75, 1.1], gap="small")
        with number_col:
            with st.container(key=f"cat_num_{job_id}_{index}"):
                btn_kwargs: Dict[str, Any] = {
                    "key": f"cat_sel_{job_id}_{index}",
                    "help": f"第 {index + 1} 单元 · {status}" + ("（当前单元）" if is_active else ""),
                    "type": "primary" if is_active else "secondary",
                    "width": "stretch",
                }
                selection_fn = activate_fn or navigate_fn
                if selection_fn:
                    btn_kwargs["on_click"] = selection_fn
                    btn_kwargs["args"] = (job_id, state, index)
                if selection_fn is navigate_fn:
                    btn_kwargs["kwargs"] = {"reveal": False}
                st.button(_t(str(index + 1)), **btn_kwargs)
        with source_col:
            with st.container(key=f"cat_source_cell_{job_id}_{index}"):
                st.markdown(
                    f'<div class="{source_class}" dir="{source_direction}" '
                    f'style="text-align: {"right" if source_direction == "rtl" else "left"}">'
                    f'{escape(source_text) or "（空段落）"}</div>',
                    unsafe_allow_html=True)
                source_activate_fn = activate_fn or navigate_fn
                if source_activate_fn:
                    source_kwargs: Dict[str, Any] = {
                        "key": f"cat_source_activate_{job_id}_{index}",
                        "width": "stretch",
                        "on_click": source_activate_fn,
                        "args": (job_id, state, index),
                    }
                    if source_activate_fn is navigate_fn:
                        source_kwargs["kwargs"] = {"reveal": False}
                    st.button(_t("选择段落"), **source_kwargs)
        with target_col:
            with st.container(key=f"cat_editor_{job_id}_{index}"):
                editor_class = escape(f"st-key-cat_editor_{job_id}_{index}", quote=True)
                st.html(
                    f'<style>[class~="{editor_class}"] textarea {{ '
                    f'direction: {target_direction}; '
                    f'text-align: {"right" if target_direction == "rtl" else "left"}; '
                    'unicode-bidi: isolate; }</style>')
                ta_kwargs: Dict[str, Any] = {
                    "height": _editor_height(str(current), lines=target_lines),
                    "label_visibility": "collapsed",
                    "placeholder": "",
                }
                if editor_changed_fn:
                    ta_kwargs["on_change"] = editor_changed_fn
                    ta_kwargs["args"] = (job_id, index, segment_id, baseline)
                st.text_area(_t('第 {v0} 段译文', v0=index + 1), value=current, key=editor_key, **ta_kwargs)
        if is_active:
            with status_col:
                with st.container(key=f"cat_status_{job_id}_{index}"):
                    display_status = {
                        "已审校": "已审",
                        "已审": "已审",
                        "待审校": "待审",
                        "待审": "待审",
                        "待翻译": "待翻译",
                        "已修改": "已修改",
                        "已翻译": "已翻译",
                    }.get(status, status)
                    status_tone = "is-edited" if is_dirty else cat_status_tone(status)
                    if is_dirty:
                        display_status = "未保存"
                    st.markdown(
                        f'<div class="tp-cat-status-cell">'
                        f'<span class="tp-cat-status-chip {status_tone}" title="{escape(_t(status))}">{escape(_t(display_status))}</span>'
                        '</div>',
                        unsafe_allow_html=True)
            with notice_col:
                with st.container(key=f"cat_notice_{job_id}_{index}"):
                    if qa_findings_fn:
                        qa_items = list(qa_findings_fn(state, index))
                    else:
                        qa_items = [f for f in state.get("findings", [])
                                    if isinstance(f, dict) and f.get("segment_index", f.get("segment_id")) == index]
                    is_blocking = (index in blocking_segments) or any(
                        f.get("severity") == "blocking" for f in qa_items)
                    has_qa = bool(qa_items)

                    try:
                        heuristic_traces = _decisions.detect_translation_decisions(
                            pair.get("source"), pair.get("target"), index, qa_findings=qa_items)
                        cached = _decisions.cached_analysis(pair, pair.get("source"), pair.get("target"))
                        analyzed = (cached or {}).get("decisions") or []
                        traces = _decisions.merge_decisions(heuristic_traces, analyzed, qa_items)
                    except Exception:
                        traces = []
                    has_trace = bool(traces)

                    if not has_qa and not has_trace:
                        st.markdown('<div class="tp-notice-empty">—</div>', unsafe_allow_html=True)
                    else:
                        if has_qa:
                            qa_label = ("QA 阻断" if is_blocking else
                                        "QA 参考" if all(item.get("severity") == "informational"
                                                       for item in qa_items) else
                                        f"QA {len(qa_items)}" if len(qa_items) > 1 else "QA")
                            if st.button(
                                _t(qa_label),
                                key=f"cat_notice_qa_{job_id}_{index}",
                                type="primary" if is_blocking else "secondary",
                                help=_t('第 {v0} 单元有 {v1} 项 QA 检查记录，点击在右栏查看', v0=index + 1, v1=len(qa_items)),
                                width="stretch",
                            ):
                                st.session_state["workspace_inspector_open"] = True
                                st.session_state[f"translation_inspector_focus_{job_id}"] = "qa"
                                sel_fn = activate_fn or navigate_fn
                                if sel_fn:
                                    if sel_fn is navigate_fn:
                                        sel_fn(job_id, state, index, reveal=False)
                                    else:
                                        sel_fn(job_id, state, index)
                                else:
                                    st.session_state["selected_segment_id"] = segment_id
                                    st.session_state["selected_segment_index"] = index
                                try:
                                    st.rerun(scope="fragment")
                                except Exception:
                                    st.rerun()
                        if has_trace:
                            trace_label = f"审视 {len(traces)}"
                            if st.button(
                                _t(trace_label),
                                key=f"cat_notice_trace_{job_id}_{index}",
                                type="tertiary",
                                help=_t('第 {v0} 单元有 {v1} 项翻译决策审视，点击在右栏查看依据', v0=index + 1, v1=len(traces)),
                                width="stretch",
                            ):
                                st.session_state["workspace_inspector_open"] = True
                                st.session_state[f"translation_inspector_focus_{job_id}"] = "trace"
                                if traces and (traces[0].get("source_span") or traces[0].get("target_span")):
                                    spans = []
                                    if traces[0].get("source_span"):
                                        spans.append({"side": "source", "span": str(traces[0]["source_span"]), "offset": traces[0].get("source_start")})
                                    if traces[0].get("target_span"):
                                        spans.append({"side": "target", "span": str(traces[0]["target_span"]), "offset": traces[0].get("target_start")})
                                    st.session_state[f"translation_trace_highlight_{job_id}"] = {
                                        "segment_index": index, "spans": spans,
                                    }
                                sel_fn = activate_fn or navigate_fn
                                if sel_fn:
                                    if sel_fn is navigate_fn:
                                        sel_fn(job_id, state, index, reveal=False)
                                    else:
                                        sel_fn(job_id, state, index)
                                else:
                                    st.session_state["selected_segment_id"] = segment_id
                                    st.session_state["selected_segment_index"] = index
                                try:
                                    st.rerun(scope="fragment")
                                except Exception:
                                    st.rerun()
            with st.container(key=f"cat_tools_{job_id}_{index}",
                              horizontal=True, horizontal_alignment="right", gap="small"):
                # Save and structural controls share one right-aligned toolbar.
                if active_row_actions_fn:
                    active_row_actions_fn(
                        job_id, state, index, segment_id, pair,
                        baseline=baseline, is_dirty=is_dirty, status=status)
                if segment_actions_fn:
                    segment_actions_fn(job_id, state, index, pair, is_active=is_active)
                else:
                    render_translation_segment_actions(
                        job_id, state, index, pair, is_active=is_active)
        _render_row_click_binding(job_id, index)


def render_cat_grid_table(
    job_id: str,
    state: Dict[str, Any],
    visible_indexes: Sequence[int],
    records: Sequence[Dict[str, Any]],
    blocking_segments: set,
    selected_index: int,
    pending_scroll: Optional[int] = None,
    pending_focus: Optional[int] = None,
    *,
    render_row_fn: Optional[Callable[..., Any]] = None,
    navigate_fn: Optional[Callable[..., Any]] = None,
    scroll_trigger_fn: Optional[Callable[..., Any]] = None,
    focus_trigger_fn: Optional[Callable[..., Any]] = None,
    qa_findings_fn: Optional[Callable[..., Sequence[Dict[str, Any]]]] = None,
) -> None:
    """Render CAT grid table within the surrounding Translation Fragment.

    Viewport pagination: CAT_GRID_ROWS = 40.
    Rerun scope: fragment (reruns the enclosing translation workspace fragment,
    ensuring CAT Grid and Inspector stay atomically in sync without drifting).
    """
    pairs = state.get("pairs") or []
    position = (visible_indexes.index(selected_index)
                if selected_index in visible_indexes else 0)
    start = max(0, min(position - CAT_GRID_ROWS // 2,
                       len(visible_indexes) - CAT_GRID_ROWS))
    window_indexes = visible_indexes[start:start + CAT_GRID_ROWS]

    st.markdown((_h('<div class="tp-cat-head"><span class="tp-cat-num">编号</span><span class="tp-cat-col is-src">原文</span><span class="tp-cat-col is-tgt">译文 · 可直接编辑<small class="tp-cat-window-note">') + str(format(start + 1, ',')) + '–' + str(format(start + len(window_indexes), ',')) + ' / ' + str(format(len(visible_indexes), ',')) + _h('</small></span><span class="tp-cat-col is-status">状态</span><span class="tp-cat-col is-notices">关注项</span></div>')),
                unsafe_allow_html=True)

    with st.container(key=f"cat_grid_{job_id}"):
        for index in window_indexes:
            if render_row_fn:
                render_row_fn(job_id, state, index, pairs[index],
                              records[index]["segment_id"],
                              selected_index, blocking_segments)
            else:
                render_translation_row(
                    job_id, state, index, pairs[index],
                    records[index]["segment_id"],
                    selected_index, blocking_segments,
                    navigate_fn=navigate_fn,
                    qa_findings_fn=qa_findings_fn)

    # 40-row viewport pagination
    if len(visible_indexes) > CAT_GRID_ROWS:
        with st.container(key=f"cat_pager_{job_id}"):
            nav_cols = st.columns([5, 1, 1], gap="small")
            with nav_cols[0]:
                st.markdown(f'<div class="tp-cat-page-range">{start + 1:,}–{start + len(window_indexes):,} / {len(visible_indexes):,}</div>', unsafe_allow_html=True)
            has_prev = start > 0
            has_next = start + len(window_indexes) < len(visible_indexes)
            with nav_cols[1]:
                if st.button(_t("‹ 上一组"), key=f"cat_page_prev_{job_id}",
                             disabled=not has_prev, width="stretch"):
                    new_idx = visible_indexes[max(0, start - CAT_GRID_ROWS)]
                    if navigate_fn:
                        navigate_fn(job_id, state, new_idx, reveal=False)
                    else:
                        st.session_state["selected_segment_id"] = records[new_idx]["segment_id"]
                        st.session_state["selected_segment_index"] = new_idx
                    try:
                        st.rerun(scope="fragment")
                    except Exception:
                        st.rerun()
            with nav_cols[2]:
                if st.button(_t("下一组 ›"), key=f"cat_page_next_{job_id}",
                             disabled=not has_next, width="stretch"):
                    new_idx = visible_indexes[min(len(visible_indexes) - 1, start + len(window_indexes))]
                    if navigate_fn:
                        navigate_fn(job_id, state, new_idx, reveal=False)
                    else:
                        st.session_state["selected_segment_id"] = records[new_idx]["segment_id"]
                        st.session_state["selected_segment_index"] = new_idx
                    try:
                        st.rerun(scope="fragment")
                    except Exception:
                        st.rerun()

    if scroll_trigger_fn:
        scroll_trigger_fn(pending_scroll)
    if focus_trigger_fn:
        focus_trigger_fn(job_id, pending_focus)

    # A textarea can receive focus before Streamlit runs its on_change callback.
    # Delegate focus to the row's existing selection button so editing an
    # inactive target immediately activates that row. The guard prevents
    # duplicate listeners after fragment reruns.
    st.html("""
<script>
(function () {
  if (window.__folithCatRowFocusSelectionBound) return;
  window.__folithCatRowFocusSelectionBound = true;
  document.addEventListener("focusin", function (event) {
    var area = event.target && event.target.closest
      ? event.target.closest('[class*="st-key-cat_editor_"] textarea') : null;
    if (!area) return;
    var row = area.closest('[class*="st-key-cat_row_"]');
    if (!row) return;
    var active = row.querySelector('[class*="st-key-cat_num_"] button[kind="primary"]');
    if (active) return;
    var selector = row.querySelector('[class*="st-key-cat_num_"] button');
    if (selector) selector.click();
  }, true);
})();
</script>
""", unsafe_allow_javascript=True)


# Backward compatibility alias
render_cat_grid_fragment = render_cat_grid_table
