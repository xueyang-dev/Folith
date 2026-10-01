"""Lightweight isolated runtime status polling fragment.

Guarantees:
1. Polling (run_every) is isolated strictly to the runtime status bar fragment.
2. The CAT table, Inspector, and PDF viewer DO NOT re-evaluate every 2 seconds.
3. Polling only activates when the job runtime status is active (running, queued, etc.).
4. Status changes to terminal state trigger a single full-page rerun.
"""
from __future__ import annotations

from html import escape
import re
from typing import Any, Dict, Optional
import streamlit as st

import core
from folith import profiler
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter

_RUNTIME_DRAWER_KEY = "workspace_runtime_drawer_job"


def refresh_workspace_on_runtime_stop(job_id: str, status: str, initial_view=None) -> None:
    """Reload saved workspace data once when polling observes a stopped run."""
    key = f"workspace_runtime_status_{job_id}"
    initial_view = initial_view or {}
    previous = st.session_state.get(key, initial_view.get("status")
                                    or initial_view.get("runtime_status"))
    st.session_state[key] = status
    if previous in core.RUNTIME_ACTIVE_STATUSES and status not in core.RUNTIME_ACTIVE_STATUSES:
        st.rerun(scope="app")


@st.fragment(run_every="2s")
def render_runtime_strip_fragment(
    job_id: str,
    state: Optional[Dict[str, Any]] = None,
    view: Optional[Dict[str, Any]] = None,
) -> bool:
    """Compact running status rendered in an isolated 2-second polling fragment."""
    with profiler.span("runtime_fragment", job_id=job_id):
        initial_view = view
        view = core.read_runtime_view(job_id)
        status = view.get("status") or view.get("runtime_status") or "idle"
        refresh_workspace_on_runtime_stop(job_id, status, initial_view)
        if status in {"idle", "completed"}:
            return False

        from folith.ui.workspace.runtime_status import runtime_presentation, hud_html
        st.markdown(hud_html(runtime_presentation(view, state)), unsafe_allow_html=True)

        with st.container(key=f"workspace_runtime_actions_{job_id}"):
            action_col, pause_col, detail_col = st.columns([1, 1, 1], gap="small")
            with action_col:
                segment_operation = (view.get("runtime") or {}).get("segment_operation")
                if segment_operation and status not in core.RUNTIME_ACTIVE_STATUSES:
                    st.caption(_t("段落操作已停止；可返回原段落重试。"))
                elif status in {"resume_requested", "queued", "starting"}:
                    st.button(_t("正在恢复…"), key=f"runtime_resuming_{job_id}",
                              disabled=True, width="content")
                elif status == "paused":
                    if st.button(_t("取消"), key=f"runtime_cancel_{job_id}", width="content"):
                        core.request_job_cancel(job_id)
                        st.rerun()
                elif status == "cancelling":
                    st.button(_t("正在取消…"), key=f"runtime_cancelling_{job_id}",
                              disabled=True, width="content")
                elif status in {"running", "waiting_external"}:
                    if st.button(_t("取消"), key=f"runtime_cancel_{job_id}",
                                 width="content"):
                        core.request_job_cancel(job_id)
                        st.rerun()
                elif status in {"interrupted", "idle_incomplete", "cancelled"}:
                    if st.button(_t("继续处理"), type="primary",
                                 key=f"runtime_resume_{job_id}", width="content"):
                        core.resume_job(job_id)
                        st.rerun()
                elif status == "stalled" and core.is_job_worker_alive(job_id):
                    if st.button(_t("放弃当前运行"), type="primary",
                                 key=f"runtime_abandon_{job_id}", width="content"):
                        core.request_job_cancel(job_id)
                        st.rerun()
                elif status in {"failed", "stalled"}:
                    if st.button(_t("重试当前步骤"), type="primary",
                                 key=f"runtime_retry_{job_id}", width="content"):
                        core.retry_job_step(job_id)
                        core.resume_job(job_id)
                        st.rerun()
            with pause_col:
                if status in {"running", "waiting_external"}:
                    if st.button(_t("暂停任务"), key=f"runtime_pause_{job_id}", width="content"):
                        core.request_job_pause(job_id)
                        st.rerun()
                elif status == "pausing":
                    st.button(_t("正在暂停…"), key=f"runtime_pausing_{job_id}", disabled=True, width="content")
                elif status == "paused":
                    if st.button(_t("继续任务"), key=f"runtime_continue_{job_id}", width="content"):
                        if not core.continue_job(job_id):
                            core.resume_job(job_id)
                        st.rerun()

            with detail_col:
                drawer_open = st.session_state.get(_RUNTIME_DRAWER_KEY) == job_id
                if st.button(_t("运行详情 →" if not drawer_open else "运行详情 · 已打开"),
                             key=f"runtime_details_{job_id}", width="content"):
                    st.session_state[_RUNTIME_DRAWER_KEY] = job_id
                    drawer_open = True
                if drawer_open:
                    _runtime_drawer_dialog(job_id)

        return True


def _runtime_pipeline_label(view: Dict[str, Any]) -> tuple[str, int, int]:
    from folith.ui.workspace.performance_view import _map_stage_name
    raw_stage = view.get("stage_label") or view.get("pipeline_stage") or ""
    stage = _map_stage_name(raw_stage)
    completed = int(view.get("progress_done") or 0)
    total = int(view.get("progress_total") or 0)
    if total > 0:
        return f"{stage} ({completed}/{total})", completed, total
    return str(stage or "准备中"), completed, total


def _runtime_stage_label(view: Dict[str, Any]) -> str:
    from folith.ui.workspace.performance_view import _map_stage_name
    raw_stage = view.get("stage_label") or view.get("operation_label") or "正在执行"
    return _map_stage_name(raw_stage)


def _runtime_action_label(view: Dict[str, Any], status: str) -> str:
    if status == "running":
        return str(view.get("operation_detail") or "模型正在生成翻译")
    if status == "waiting_external":
        return "等待 API 响应"
    if status == "failed":
        return "步骤遇到错误，可重试"
    return str(view.get("detail") or "")


def _runtime_age(timestamp: Any) -> float:
    if not timestamp:
        return 0.0
    import datetime
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        if isinstance(timestamp, str):
            dt = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return max(0.0, (now - dt).total_seconds())
    except Exception:
        pass
    return 0.0


def _runtime_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}秒"
    m = s // 60
    return f"{m}分{s % 60}秒"


def _runtime_clock(timestamp: Any) -> str:
    if not timestamp:
        return "刚刚"
    return str(timestamp).replace("T", " ")[:19]


@st.dialog(_t("运行详情"), width="medium", on_dismiss=lambda: st.session_state.pop(_RUNTIME_DRAWER_KEY, None))
def _runtime_drawer_dialog(job_id: str) -> None:
    from folith.ui.workspace.performance_view import render_performance_drawer

    @st.fragment(run_every="2s")
    def content():
        render_performance_drawer(job_id, runtime_view=core.read_runtime_view(job_id))

    content()
