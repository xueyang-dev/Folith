"""Unit and regression tests for simplified Performance & Run Details UI.

Covers:
1. Stage name mapping (internal identifiers -> friendly Chinese)
2. Activity event presentation mapping (stripping internal prefixes, user language)
3. State A: Preparing (indeterminate progress, never 0/0 or 0.0%)
4. State B: Running (real progress e.g. 41 / 109 批次 38%, elapsed time, recent activity)
5. State C: Completed (已完成, 100%, 查看结果)
6. State D: Failed (运行失败, 简洁错误, 重试当前步骤)
7. State E: Advanced Diagnostics (性能概览, 流水线耗时, 诊断分析, 原始数据与历史)
8. Percentage calculation correctness (bounded between 0.0% and 100.0%, no explosion)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from folith.trace import RunContext, TraceLevel
from folith.ui.workspace.performance_view import (
    _format_elapsed,
    _map_activity_event,
    _map_stage_name,
    _map_status,
    render_performance_drawer,
)


def test_stage_name_mapping():
    """Verify internal identifiers are cleanly mapped to friendly Chinese."""
    assert _map_stage_name("pdf_classify") == "识别文档类型"
    assert _map_stage_name("layout_recovery") == "版面还原与排版"
    assert _map_stage_name("document_processing") == "解析原文"
    assert _map_stage_name("deterministic_cleanup") == "原文纠错与断行整理"
    assert _map_stage_name("llm_cleanup") == "原文纠错与整理"
    assert _map_stage_name("translation_pipeline") == "正在翻译"
    assert _map_stage_name("qa_check") == "译文质量检查"
    assert _map_stage_name("build_delivery") == "生成交付文件"
    assert _map_stage_name("idle") == "准备就绪"

    # Must never leak raw brackets or underscores
    assert "[" not in _map_stage_name("[llm_cleanup]")
    assert "]" not in _map_stage_name("[llm_cleanup]")


def test_activity_event_presentation_mapping():
    """Verify internal structured events are mapped to user-friendly sentences."""
    # Batch completion with total hint
    ev_batch = {
        "stage": "translation",
        "operation": "batch_2",
        "metadata": {"batch_id": 2, "total_batches": 109},
    }
    assert _map_activity_event(ev_batch) == "完成批次 3 / 109"

    # OCR completion
    ev_ocr = {
        "stage": "ocr_processing",
        "operation": "ocr_page_2",
        "metadata": {"page_no": 2},
    }
    assert _map_activity_event(ev_ocr) == "完成第 2 页文字识别 (OCR)"

    # Segmentation
    ev_seg = {"stage": "segmentation", "operation": "split_and_clean"}
    assert _map_activity_event(ev_seg) == "完成原文分句切分"

    # Cleanup
    ev_clean = {"stage": "source_cleanup", "operation": "deterministic_cleanup"}
    assert _map_activity_event(ev_clean) == "完成原文整理与断行规整"

    # QA check
    ev_qa = {"stage": "qa", "operation": "qa_check"}
    assert _map_activity_event(ev_qa) == "完成译文质量自检"

    # Leaked brackets in raw msg must be cleaned
    ev_raw = {"stage": "pipeline", "operation": "event", "metadata": {"message": "[pipeline] start"}}
    clean_msg = _map_activity_event(ev_raw)
    assert "[pipeline]" not in clean_msg


def test_elapsed_time_formatting():
    """Verify format into mm:ss or hh:mm:ss."""
    assert _format_elapsed(0) == "00:00"
    assert _format_elapsed(222) == "03:42"
    assert _format_elapsed(3665) == "01:01:05"


def test_percentage_correctness_and_boundedness(tmp_path):
    """Verify percentage denominator uses active elapsed time and stays in [0.0, 100.0]."""
    ctx = RunContext("test_pct_bound", output_dir=tmp_path)
    # Record a tiny stage
    with ctx.span("pdf_classify", "classify"):
        time.sleep(0.01)

    # Calling generate_summary during active run MUST NOT explode
    summary = ctx.generate_summary()
    assert summary["wall_time_s"] >= 0.001
    stages = summary.get("stages", {})
    assert "pdf_classify" in stages
    pct = stages["pdf_classify"]["pct_of_total"]
    # Must be bounded, never 35305.2%
    assert 0.0 <= pct <= 100.0
    assert summary["unaccounted_pct"] <= 100.0
    ctx.finish()


def test_state_a_preparing_apptest():
    """Verify state A (Preparing): indeterminate progress, never 0/0 or 0.0%."""
    code = """
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

view = {
    "status": "starting",
    "runtime_status": "starting",
    "stage": "pdf_parse",
    "progress": (0, 0),
    "detail": "正在解析原文与版面结构…",
}
state = {"stage": "STARTING", "paras": [], "pairs": []}
render_performance_drawer("job_prep_001", state=state, runtime_view=view)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    # Check that '0 / 0' and '0.0%' do NOT appear anywhere
    all_text = " ".join([m.value for m in at.markdown])
    assert "0 / 0" not in all_text
    assert "0.0%" not in all_text
    assert "正在解析原文" in all_text or "正在确定任务规模" in all_text or "准备中" in all_text


def test_state_b_running_apptest():
    """Verify state B (Running): 41 / 109 批次 38%, elapsed time, action button."""
    code = """
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

view = {
    "status": "running",
    "runtime_status": "running",
    "stage": "deterministic_cleanup",
    "progress": (41, 109),
    "operation": "正在处理第 42–44 段",
    "detail": "正在处理第 42–44 段",
}
state = {"stage": "CLEANED", "batch_cursor": 41, "total_batches": 109}
render_performance_drawer("job_run_001", state=state, runtime_view=view)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    all_text = " ".join([m.value for m in at.markdown])
    assert "41 / 109 批次" in all_text
    assert "37.6%" in all_text
    assert "原文纠错与断行整理" in all_text
    # Action button '取消任务' must exist
    cancel_btns = [b for b in at.button if "取消任务" in b.label]
    assert len(cancel_btns) == 1
    # Secondary entry '高级诊断' must exist
    adv_btns = [b for b in at.button if "高级诊断" in b.label]
    assert len(adv_btns) == 1


def test_state_c_completed_apptest():
    """Verify state C (Completed): 已完成, 100%, 查看结果 button."""
    code = """
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

view = {
    "status": "completed",
    "runtime_status": "completed",
    "stage": "build_delivery",
    "progress": (109, 109),
    "detail": "已完成全部步骤。",
}
state = {"stage": "TRANSLATED", "batch_cursor": 109, "total_batches": 109}
render_performance_drawer("job_done_001", state=state, runtime_view=view)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    all_text = " ".join([m.value for m in at.markdown])
    assert "已完成" in all_text
    assert "100.0%" in all_text
    # '查看结果' button must exist
    view_btns = [b for b in at.button if "查看结果" in b.label]
    assert len(view_btns) == 1


def test_state_d_failed_apptest():
    """Verify state D (Failed): 运行失败, 重试当前步骤 button."""
    code = """
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

view = {
    "status": "failed",
    "runtime_status": "failed",
    "stage": "translation",
    "progress": (12, 109),
    "error": "API 请求超时，已安全中断",
    "detail": "API 请求超时，已安全中断",
}
state = {"stage": "TRANSLATING", "batch_cursor": 12, "total_batches": 109}
render_performance_drawer("job_fail_001", state=state, runtime_view=view)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    all_text = " ".join([m.value for m in at.markdown])
    assert "运行失败" in all_text
    assert "API 请求超时" in all_text
    # '重试当前步骤' button must exist
    retry_btns = [b for b in at.button if "重试当前步骤" in b.label]
    assert len(retry_btns) == 1


def test_state_e_advanced_diagnostics_toggle(tmp_path):
    """Verify state E (Advanced Diagnostics): 4 clean categories, full technical data preserved."""
    # Prepare a job with real performance trace artifacts
    job_id = "job_adv_test"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    ctx = RunContext(job_id, output_dir=job_dir)
    with ctx.span("translation", "batch_1"):
        ctx.record_model_call(
            provider="openai", model="gpt-4o", duration_s=0.5,
            prompt_tokens=500, completion_tokens=200, cached_tokens=50,
        )
        ctx.record_tm_lookup("exact", duration_ms=2.5)
        ctx.record_batch(batch_id=0, segment_count=10, source_chars=500, duration_ms=500.0, tm_hits=1)
    ctx.finish(status="completed")

    summary_json_escaped = json.dumps(json.dumps(ctx.generate_summary()))
    code = f"""
import json
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

# Force advanced mode via session_state
st.session_state['perf_view_mode_{job_id}'] = 'advanced'
summary = json.loads({summary_json_escaped})
state = {{'performance': summary}}
render_performance_drawer('{job_id}', state=state)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    # Check that tabs for the 4 categories exist
    tab_labels = [t.label for t in at.tabs]
    assert "性能概览" in tab_labels
    assert "流水线耗时" in tab_labels
    assert "诊断分析" in tab_labels
    assert "原始数据与历史" in tab_labels

    # Check back button exists
    back_btns = [b for b in at.button if "返回运行状态" in b.label]
    assert len(back_btns) == 1


def test_ingested_and_lifecycle_stages_mapping():
    """Verify INGESTED and other state machine stages map to friendly Chinese and explanations."""
    from folith.ui.workspace.performance_view import _map_stage_explanation

    assert _map_stage_name("INGESTED") == "原文提取与排版整理"
    assert _map_stage_name("ingested") == "原文提取与排版整理"
    assert _map_stage_name("PROFILED") == "文档画像分析完成"
    assert _map_stage_name("GLOSSARY_FROZEN") == "核心术语库已锁定"
    assert _map_stage_name("TRANSLATING") == "正在翻译正文"
    assert _map_stage_name("TRANSLATED") == "全书初译已完成"
    assert _map_stage_name("FINAL") == "终稿交付已归档"

    expl = _map_stage_explanation("INGESTED")
    assert "排版清洗" in expl or "提取" in expl
    assert len(expl) > 10


def test_ingested_drawer_displays_chinese_and_explanation():
    """Verify drawer renders Chinese for INGESTED without raw code leaks."""
    code = """
import streamlit as st
from folith.ui.workspace.performance_view import render_performance_drawer

state = {
    "stage": "INGESTED",
    "source_paras": ["Chapter 1..."],
    "source_cleanup_done": False,
    "p1_done": False,
}
view = {
    "status": "running",
    "runtime_status": "running",
    "stage": "INGESTED",
    "progress": (36, 109),
}
render_performance_drawer("job_ingested_test", state=state, runtime_view=view)
"""
    at = AppTest.from_string(code).run()
    assert not at.exception
    all_text = " ".join([m.value for m in at.markdown])
    # Must NOT have raw '当前阶段：<strong>INGESTED</strong>'
    assert "<strong>INGESTED</strong>" not in all_text
    # Must display friendly Chinese title and explanation
    assert "原文排版与断行整理" in all_text or "原文提取与排版整理" in all_text
    assert "阶段说明" in all_text
    assert "任务阶段" in all_text



def test_context_build_activity_is_readable():
    assert _map_activity_event({"stage": "translation", "operation": "context_build", "status": "success", "items_in": 3}) == "已准备好翻译上下文（3 段）"
    assert _map_activity_event({"stage": "translation", "operation": "context_build", "status": "error"}) == "翻译上下文准备失败"


def test_runtime_activity_takes_priority(monkeypatch):
    import core
    from folith.ui.workspace.performance_view import _load_recent_events
    monkeypatch.setattr(core, "read_runtime_events", lambda *args, **kwargs: [
        {"event": "translation_retry", "message": "正在重试（第 2/3 次尝试）", "timestamp": "12:00:01"},
        {"event": "llm_request_started", "message": "已向模型发送请求", "timestamp": "12:00:02"},
    ])
    events = _load_recent_events("runtime_activity_test")
    assert [e["timestamp"] for e in events] == ["12:00:01", "12:00:02"]
    assert "第 2/3 次" in _map_activity_event(events[0])


def test_waiting_model_overrides_trace_status_apptest():
    code = '''
import streamlit as st
from unittest.mock import patch
from folith.ui.workspace.performance_view import render_performance_drawer
from types import SimpleNamespace
run = SimpleNamespace(
    generate_summary=lambda: {"status": "running"},
    top_level_stages=["translation"], started_monotonic=__import__("time").perf_counter())
with patch("folith.ui.workspace.performance_view.trace.get_active_run", return_value=run):
    render_performance_drawer("waiting_model_test", runtime_view={
        "status": "waiting_external", "progress": (0, 11),
        "runtime": {"phase_label": "等待模型响应（2 个请求进行中）"}})
'''
    at = AppTest.from_string(code).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "正在等待模型响应" in text
    assert "2 个请求进行中" in text
    assert "任务运行时间暂无记录" in text
    assert "已完成 0 / 11 段" in text
    assert "Pipeline 流水线" not in text
