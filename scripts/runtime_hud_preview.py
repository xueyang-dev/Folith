"""Isolated browser QA of production Runtime HUD and Drawer, no model or job writes.

Run: python3 -m streamlit run scripts/runtime_hud_preview.py --server.port 8507
The HUD function is loaded from app.py without executing the application shell.
"""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import streamlit as st
from folith.ui.styles import inject_design_system
from folith.ui.workspace.performance_view import render_performance_drawer
from folith.ui.workspace.runtime_status import runtime_presentation

st.set_page_config(page_title="Folith · Runtime HUD 验证", layout="wide")
inject_design_system()
st.markdown("### Folith · Runtime HUD 验证")
st.caption("隔离状态样本 · 使用生产 HUD 与 Run Details 渲染代码 · 不发送模型请求")
labels = ["术语准备", "构建上下文", "翻译中", "等待模型", "保存结果", "重试", "取消中", "正在暂停", "已暂停", "标记中", "标记已暂停", "暂无信号", "已完成"]
selected = st.radio("验证状态", labels, horizontal=True, key="qa_state")
if st.session_state.get("qa_previous") != selected:
    st.session_state.qa_reference = datetime.now(timezone.utc)
    st.session_state.qa_previous = selected


def fixture():
    selected = st.session_state.get("qa_state", labels[0])
    now = st.session_state.get("qa_reference", datetime.now(timezone.utc))
    status, operation_id, operation = {
        "术语准备": ("running", "terminology", "正在处理术语候选"),
        "构建上下文": ("running", "context_build", "正在构建第 81 批翻译上下文"),
        "翻译中": ("running", "translation_generation", "正在翻译第 486–489 段"),
        "等待模型": ("waiting_external", "translation_generation", "正在翻译第 486–489 段"),
        "保存结果": ("running", "state_commit", "正在写入第 81 批译文"),
        "重试": ("running", "translation_retry", "正在重试翻译请求（第 2/3 次尝试）"),
        "取消中": ("cancelling", "translation_generation", "正在翻译第 486–489 段"),
        "正在暂停": ("pausing", "translation_generation", "正在翻译第 486–489 段"),
        "已暂停": ("paused", "translation_generation", "正在翻译第 486–489 段"),
        "标记中": ("waiting_external", "annotation", "正在标注"),
        "标记已暂停": ("paused", "annotation", "任务已暂停"),
        "暂无信号": ("stalled", "annotation", "正在标注"),
        "已完成": ("completed", "state_commit_done", "译文已保存"),
    }[selected]
    stage = "terminology" if selected == "术语准备" else "annotation" if ("标记" in selected or selected == "暂无信号") else "translation"
    activity = now - timedelta(seconds=3)
    runtime = {
        "status": status, "stage": stage, "stage_id": stage,
        "operation_id": operation_id, "operation_label": operation,
        "phase_label": "等待模型响应（1 个请求进行中）" if selected in {"等待模型", "标记中"} else operation,
        "started_at": (now - timedelta(seconds=2176)).isoformat(),
        "last_activity_at": activity.isoformat(), "last_progress_at": activity.isoformat(),
        "model_wait_started_at": (now - timedelta(seconds=18)).isoformat(),
        "stage_progress": {} if selected in {"术语准备", "标记中", "标记已暂停", "暂无信号"} else {
            "completed": 1221 if selected == "已完成" else 80, "total": 1221, "unit": "批次", "in_flight": [120, 122, 123]},
        "events": [{"event": operation_id, "stage": stage, "timestamp": activity.isoformat(),
                    "message": operation + ("（并发批次 #120 #122 #123，共 4845 段）" if selected == "翻译中" else "")}],
    }
    if selected in {"等待模型", "标记中"}:
        runtime["events"][-1].update(event="llm_request_started", message="已向模型发送请求")
    elif selected == "取消中":
        runtime["events"][-1].update(event="cancel_requested", message="已请求取消任务")
    elif selected == "已完成":
        runtime["events"][-1].update(event="job_completed", message="任务已完成")
    if st.query_params.get("zero") == "1":
        runtime["stage_progress"] = {"completed": 0, "total": 11, "unit": "批次"}
    if status == "completed":
        runtime["finished_at"] = now.isoformat()
    return {"status": status, "runtime": runtime, "events": runtime["events"],
            "stage_progress": runtime["stage_progress"], "status_label": "正在运行"}


@st.dialog("运行详情", width="medium", on_dismiss=lambda: st.session_state.pop("qa_drawer", None))
def drawer(job_id):
    @st.fragment(run_every="2s")
    def content():
        render_performance_drawer(job_id, runtime_view=fixture(), on_cancel=lambda: None)
    content()


def preview_cancel(job_id):
    st.session_state.qa_state = "取消中"


namespace = {
    "st": st, "profiler": SimpleNamespace(span=lambda *args, **kwargs: __import__("contextlib").nullcontext()),
    "core": SimpleNamespace(read_runtime_view=lambda job: fixture(), request_job_cancel=preview_cancel,
                            is_job_worker_alive=lambda job: True,
                            request_job_pause=lambda job: st.session_state.update(qa_state="已暂停"),
                            continue_job=lambda job: (st.session_state.update(qa_state="翻译中") or True)),
    "_RUNTIME_DRAWER_KEY": "qa_drawer", "_runtime_drawer_dialog": drawer,
}
module = ast.parse((ROOT / "app.py").read_text())
function = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == "_render_runtime_strip")
exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / "app.py"), "exec"), namespace)
st.markdown('<div class="tp-workspace-shell"></div>', unsafe_allow_html=True)
with st.container(key="workspace_topbar"):
    st.markdown('<div class="tp-workspace-topbar-copy"><h1>翻译工作台</h1><div class="tp-workspace-meta">示例文档 · 4,845 段</div></div>', unsafe_allow_html=True)
    namespace["_render_runtime_strip"]("runtime_hud_qa", {}, fixture())
st.caption("以下为工作区内容；状态切换时此区域不应被 HUD 推动。")
st.markdown("| 原文 | 译文 |\n|---|---|\n| Sample paragraph | 示例译文 |")
with st.expander("修正前状态栏参考"):
    st.markdown('''<div class="tp-banner-runtime is-active"><div class="tp-banner-runtime-main"><div class="tp-banner-runtime-state is-active"><i></i>正在运行</div><strong class="tp-banner-runtime-headline">术语准备</strong><span class="tp-banner-runtime-detail">双语翻译与术语严格注入</span></div><div class="tp-banner-runtime-pipeline"><span>流程进度</span><span class="tp-banner-runtime-pipeline-label">当前阶段 80 / 1221 批次 · 处理中 #120 #122 #123</span><span class="tp-banner-runtime-bar"><i style="width:7%"></i></span></div><div class="tp-banner-runtime-meta"><span>开始于 2026-09-30 11:23</span><span>最近更新 2026-09-30 12:00</span></div></div>''', unsafe_allow_html=True)
    left, right = st.columns([1.15, 2.85])
    left.button("取消任务", disabled=True, width="stretch")
    right.button("运行详情 ›", disabled=True, width="stretch")
