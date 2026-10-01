"""UI Component for Folith Execution Tracking / Run Details / Performance Diagnostics.

Information Architecture:
1. Primary / Default View: Minimalist Run Details (运行详情)
   - Status badge (● 正在运行 / 准备中 / 已完成 / 失败)
   - Current Stage in user-friendly Chinese (e.g. 原文纠错与断行整理, 正在翻译, 解析原文)
   - Real progress (e.g. 41 / 109 批次  38%) or indeterminate state (never 0/0 or 0.0%)
   - Elapsed time (e.g. 已运行 03:42)
   - Current Step detail (e.g. 正在处理第 42–44 段)
   - Recent Activity: 3–5 human-readable events with presentation mapping
   - Primary action (取消任务 / 查看结果 / 重试当前步骤)
   - Secondary low-weight entry: "高级诊断 ›"

2. Secondary View: Advanced Diagnostics (高级诊断)
   - Demoted task/run metadata (Job ID, Run ID)
   - Performance Overview (CPU, RAM RSS, throughput, model latency, TM hit rate)
   - Pipeline Timings (Stage wall & CPU time, bounded percentages, throughput)
   - Diagnostics (P50/P90/P95/P99 latency, slowest batches, model tokens, retries)
   - Raw Trace & History (historical runs table, performance.md, summary.json)
"""
from __future__ import annotations

import json
import re
import time
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

from folith.trace import trace
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter

# Stage display name mapping (internal identifiers -> friendly Chinese)
_STAGE_DISPLAY_NAMES = {
    # 状态机主阶段 (State Machine Stages)
    "ingested": "原文提取与排版整理",
    "profiled": "文档画像分析完成",
    "terms_prepared": "术语候选提取完成",
    "glossary_frozen": "核心术语库已锁定",
    "translating": "正在翻译正文",
    "translated": "全书初译已完成",
    "annotated": "深度译注生成完成",
    "academic_writing": "学术实践报告撰写中",
    "academic_review_required": "实践报告待人工审校",
    "academic_failed": "实践报告生成异常",
    "report_generated": "学术实践报告已生成",
    "review_required": "译文待人工审校",
    "final": "终稿交付已归档",

    # 原文提取与 PDF 排版 (Ingestion & Layout)
    "pdf_budget": "解析原文结构",
    "pdf_classify": "识别文档类型",
    "pdf_parse": "解析原文",
    "layout_recovery": "版面还原与排版",
    "rasterize": "页面光栅化渲染",
    "ocr": "文字识别 (OCR)",
    "ocr_processing": "文字识别 (OCR)",
    "ocr_page": "文字识别 (OCR)",
    "document_processing": "解析原文",

    # 排版清洗、纠错与分句 (Cleanup & Segmentation)
    "deterministic_cleanup": "原文纠错与断行整理",
    "source_cleanup": "原文排版与断行整理",
    "llm_cleanup": "原文纠错与整理",
    "cleanup": "整理原文",
    "segment_build": "构建翻译单元",
    "segmentation": "智能分句分段",
    "split_and_clean": "智能分句分段",

    # 画像、摘要与术语 (Profiling & Terminology)
    "document_profile": "分析文档画像",
    "style_profile": "制定翻译风格",
    "semantic_units": "语义单元切分",
    "section_digests": "生成章节摘要",
    "terminology": "术语提取与对齐",
    "term_lookup": "术语库对齐与检查",
    "glossary_freeze": "锁定核心术语表",
    "tm_lookup": "记忆库检索",

    # 翻译流水线 (Translation Pipeline)
    "translation": "正在翻译",
    "translation_pipeline": "正在翻译",
    "batch_pipeline": "批量翻译",
    "document_pipeline": "文档处理流水线",
    "batch": "批量翻译",

    # 审校、质检与自愈 (Review & QA)
    "targeted_final_review": "整书一致性审校",
    "qa": "译文质量检查",
    "qa_check": "译文质量检查",
    "repair_pass": "译文自愈与修复",
    "review": "人工审校就绪",

    # 交付物生成 (Delivery)
    "final_docx": "生成 Word 交付终稿",
    "delivery": "生成交付文件",
    "build_delivery": "生成交付文件",
    "finalization": "生成交付文件",

    # 学术报告 (Academic Report)
    "academic_profile": "分析学术画像",
    "academic_outline": "构建报告大纲",
    "academic_synthesis": "生成实践报告",

    # 任务生命周期与通用状态 (Lifecycle Statuses)
    "idle": "准备就绪",
    "idle_incomplete": "任务未完成（可继续）",
    "starting": "准备任务中",
    "queued": "排队就绪",
    "resume_requested": "正在恢复任务",
    "running": "正在运行",
    "waiting_external": "等待大模型响应",
    "waiting_manual": "等待人工确认",
    "cancelling": "正在取消任务",
    "cancelled": "任务已取消",
    "completed": "任务已完成",
    "failed": "运行遇到异常",
    "interrupted": "上次运行已中断",
    "stalled": "暂无运行信号",
    "draft": "尚未开始",
    "needs_attention": "需要处理",
    "ready_for_delivery_prep": "可以准备交付",
    "preparing_delivery": "正在准备交付",
    "delivery_ready": "可以正式交付",
    "delivered": "已交付",
}

# Detailed, user-friendly explanations for each stage and status
_STAGE_EXPLANATIONS = {
    # 状态机主阶段
    "ingested": "已提取文档文本，正在进行排版清洗、断行消除与段落边界重组。",
    "profiled": "已完成文档体裁与专业领域画像分析，确立了翻译风格引导规范。",
    "terms_prepared": "已提取核心专业术语与高频词汇候选表，等待锁定或人工确认。",
    "glossary_frozen": "核心术语库与对齐规则已锁定，准备启动正文翻译流水线。",
    "translating": "正在调用大语言模型进行批量句段翻译，并实时注入锁定术语约束。",
    "translated": "全书所有翻译单元初译完成，正在进入质检自愈与一致性审校流程。",
    "annotated": "已生成跨文化背景、专业术语考证与深度翻译注释。",
    "academic_writing": "正在基于翻译对齐与决策证据自动撰写 MTI 学术翻译实践报告。",
    "academic_review_required": "学术实践报告初稿已完成，等待人工审阅与质量确认。",
    "academic_failed": "学术实践报告生成流程遇到异常，可重试当前撰写步骤。",
    "report_generated": "学术实践报告全文、案例库与附录已全部生成完毕。",
    "review_required": "译文包含需要人工核对的低置信度翻译或质检项，等待人工确认。",
    "final": "项目已完成最终人工审阅与交付物格式封装，进入归档状态。",

    # 原文提取与 PDF 排版
    "pdf_budget": "正在评估文档规模与复杂度，规划最优处理切分策略。",
    "pdf_classify": "正在识别文档是扫描图像版还是包含文本层的原生数字版。",
    "pdf_parse": "正在从文档中解析原始排版流并提取结构化段落。",
    "layout_recovery": "正在识别并分离正文、标题、页眉、页脚与插图注释。",
    "rasterize": "正在将 PDF 页面光栅化渲染为高清图像以便进行文字定位。",
    "ocr": "正在调用文字识别引擎提取页面中的文字层。",
    "ocr_processing": "正在调用文字识别引擎提取页面中的文字层。",
    "document_processing": "正在解析源文档排版并提取文本段落。",

    # 排版清洗、纠错与分句
    "deterministic_cleanup": "正在通过排版规则规范空白字符、合并换行并剔除孤立乱码符号。",
    "source_cleanup": "正在整理提取出的原文段落，消除排版断行并重建自然语意段落。",
    "llm_cleanup": "正在通过大语言模型检查原文断句、修复连字符断词并确认自然段落边界。",
    "cleanup": "正在整理提取出的原文段落，消除排版断行并重建自然段落。",
    "segment_build": "正在基于清洗后的段落，按句子进行边界识别与对齐，构建标准翻译单元。",
    "segmentation": "正在根据标点与语义切分翻译单元，确保每句独立可对齐。",
    "split_and_clean": "正在切分翻译单元并清除排版残留杂质。",

    # 画像、摘要与术语
    "document_profile": "正在分析文档主题、语言难度、文体特征与目标读者定位。",
    "style_profile": "正在根据原文语调生成翻译风格指引，统一全书译文基调。",
    "semantic_units": "正在识别长难句中的语义从句结构，辅助模型精准理解长句。",
    "section_digests": "正在提取各章节核心情节与逻辑主线，构建全书宏观上下文缓存。",
    "terminology": "正在扫描全书高频专业词汇，提取双语术语候选并进行概念对齐。",
    "term_lookup": "正在比对已有术语库，标记并在翻译中强制统一核心术语。",
    "glossary_freeze": "正在对确认的术语表生成快照版本，锁定翻译时的专有名词约束。",
    "tm_lookup": "正在检索历史翻译记忆库 (TM)，复用高匹配度的优质已有译文。",

    # 翻译流水线
    "translation": "正在并发调用大语言模型进行批量句段翻译，保障行文通顺并严格遵循术语。",
    "translation_pipeline": "正在并发调用大语言模型进行批量句段翻译，保障行文通顺并严格遵循术语。",
    "batch_pipeline": "正在执行批量翻译调度，兼顾翻译上下文连贯性与吞吐效率。",
    "document_pipeline": "全自动翻译与交付流水线正在有序处理中。",
    "batch": "正在执行当前批次的双语翻译与术语对齐。",

    # 审校、质检与自愈
    "targeted_final_review": "正在对全书专有名词、核心人物称谓及长篇语境进行跨章节一致性审查。",
    "qa": "正在自动检查译文是否存在漏译、格式缺失、错别字或标点异常。",
    "qa_check": "正在自动检查译文是否存在漏译、格式缺失、错别字或标点异常。",
    "repair_pass": "正在针对初译中检测到的标点、格式遗漏进行局部精准重译与自愈修复。",
    "review": "翻译与自动修复已完成，已就绪供人工审查确认。",

    # 交付物生成
    "final_docx": "正在将双语及目标语言排版格式导出为标准化 DOCX 交付文档。",
    "delivery": "正在打包并生成可直接交付的双语对照与纯译文最终文档。",
    "build_delivery": "正在打包并生成可直接交付的双语对照与纯译文最终文档。",
    "finalization": "正在生成最终标准化交付文档与项目归档包。",

    # 学术报告
    "academic_profile": "正在提取译文实践特色与理论指导维度，构建实践报告画像。",
    "academic_outline": "正在根据 MTI 翻译实践报告规范自动编排章节大纲。",
    "academic_synthesis": "正在结合翻译实例与理论框架自动撰写学术实践报告正文。",

    # 通用状态
    "idle": "系统当前处于空闲状态，随时可加载或启动新任务。",
    "idle_incomplete": "当前任务进度已安全保存在本地，可随时点击继续处理。",
    "starting": "正在从最近检查点读取历史状态并初始化流水线环境。",
    "queued": "任务已加入执行队列，等待调度器分配执行通道。",
    "resume_requested": "已收到继续执行指令，正在校验检查点完整性并恢复任务。",
    "running": "任务正在后台平稳运行中，所有中间步骤与进度均实时持久化。",
    "waiting_external": "已向大语言模型服务发出推理请求，正在等待模型返回内容。",
    "waiting_manual": "当前阶段需要人工介入（如确认术语表或审阅疑难项），确认后即可继续。",
    "cancelling": "已发起取消操作，正在安全收拢工作线程并固化当前检查点。",
    "cancelled": "任务已由用户主动取消，所有已翻译内容均已完整保存。",
    "completed": "全流程所有步骤已顺利完成，可直接查看结果或下载交付文档。",
    "failed": "当前步骤执行失败，详细错误已记录，建议检查网络或密钥后重试。",
    "interrupted": "检测到运行意外中断，已有进度完好保存在本地，可随时点击继续处理。",
    "stalled": "较长时间未收到运行心跳信号，任务可能处于等待状态，可尝试重试唤醒。",
}


def _map_stage_name(stage: Optional[str]) -> str:
    """Map raw internal stage name to friendly Chinese label."""
    if not stage:
        return "正在运行"
    raw = str(stage).strip().lower()
    if raw in _STAGE_DISPLAY_NAMES:
        return _STAGE_DISPLAY_NAMES[raw]
    # Check partial substring matches
    for k, v in _STAGE_DISPLAY_NAMES.items():
        if k in raw:
            return v
    # Format fallback: remove brackets and underscores
    clean = re.sub(r"[\[\]]", "", str(stage)).replace("_", " ").strip()
    clean_lower = clean.lower()
    if clean_lower in _STAGE_DISPLAY_NAMES:
        return _STAGE_DISPLAY_NAMES[clean_lower]
    return clean or "正在运行"


def _map_stage_explanation(stage: Optional[str]) -> str:
    """Return a friendly, clear, informative Chinese explanation for the given stage/status."""
    if not stage:
        return "任务正在后台平稳运行中，所有进度实时持久化。"
    raw = str(stage).strip().lower()
    if raw in _STAGE_EXPLANATIONS:
        return _STAGE_EXPLANATIONS[raw]
    for k, v in _STAGE_EXPLANATIONS.items():
        if k in raw:
            return v
    clean = _map_stage_name(stage)
    return f"当前正在执行「{clean}」步骤，系统正在平稳处理中。"


def _map_status(status: Optional[str]) -> Tuple[str, str]:
    """Return friendly status text and color code."""
    s = str(status or "").strip().lower()
    if s in {"paused", "pausing"}:
        return ("已暂停" if s == "paused" else "正在暂停"), "#64748b"
    if s == "waiting_external":
        return "等待模型响应", "#d97706"
    if s == "running":
        return "正在运行", "#2563eb"
    elif s in {"completed", "success"}:
        return "已完成", "#16a34a"
    elif s in {"failed", "error"}:
        return "运行失败", "#dc2626"
    elif s == "cancelling":
        return "正在取消", "#ea580c"
    elif s == "cancelled":
        return "已取消", "#64748b"
    elif s in {"interrupted", "stalled"}:
        return "已中断", "#d97706"
    elif s in {"waiting_manual"}:
        return "待确认", "#0284c7"
    return "准备中", "#4f46e5"


def _format_elapsed(seconds: float) -> str:
    """Format seconds into 03:42 or 01:23:45."""
    s = max(0, int(round(seconds)))
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _format_duration_friendly(seconds: float) -> str:
    """Format duration into friendly readable string."""
    s = int(round(seconds))
    if s < 60:
        return f"{s}秒"
    m = s // 60
    rem_s = s % 60
    if m < 60:
        return f"{m}分{rem_s}秒"
    h = m // 60
    rem_m = m % 60
    return f"{h}小时{rem_m}分{rem_s}秒"


def _format_clock(timestamp: Any) -> str:
    """Extract HH:MM:SS or return —."""
    if not timestamp:
        return "—"
    ts_str = str(timestamp)
    if "T" in ts_str:
        time_part = ts_str.split("T")[1][:8]
        return time_part
    elif " " in ts_str:
        return ts_str.split(" ")[1][:8]
    return ts_str[:8]


def _map_activity_event(ev: Dict[str, Any], total_batches_hint: Optional[int] = None) -> str:
    """Map raw event dictionary to clear, friendly user message."""
    stage = str(ev.get("stage") or "").lower()
    op = str(ev.get("operation") or ev.get("event") or "").lower()
    meta = ev.get("metadata") or {}
    raw_msg = str(meta.get("message") or "")

    # Check if raw message is already clean user-facing sentence
    if raw_msg and not raw_msg.startswith("[") and not any(k in raw_msg for k in ("_cleanup", "_pipeline", "_classify", "deterministic")):
        from folith.ui.workspace.runtime_status import clean_operation
        return clean_operation(raw_msg)

    if op == "context_build":
        if ev.get("status") == "error":
            return "翻译上下文准备失败"
        count = ev.get("items_in")
        suffix = f"（{count} 段）" if count else ""
        return f"已准备好翻译上下文{suffix}"
    request_messages = {
        "llm_request_started": "已发送请求，等待模型返回",
        "llm_response_received": "已收到模型响应，正在处理结果",
        "llm_request_failed": "模型请求失败",
        "translation_retry": "翻译请求失败，正在重试",
    }
    if op in request_messages:
        return request_messages[op]

    # Batch completions
    if "batch" in op or "batch" in stage or "batch" in raw_msg:
        batch_no = meta.get("batch_id") or meta.get("batch") or meta.get("batch_index")
        tot = meta.get("total_batches") or meta.get("batch_count") or total_batches_hint
        if batch_no is not None:
            try:
                b_num = int(batch_no) + 1
            except (ValueError, TypeError):
                b_num = str(batch_no)
            if tot:
                return f"完成批次 {b_num} / {tot}"
            return f"完成批次 {b_num}"
        # Parse from op string (e.g. batch_3)
        m = re.search(r"batch[_\s]*(\d+)", op or raw_msg)
        if m:
            b_num = int(m.group(1)) + 1
            if tot:
                return f"完成批次 {b_num} / {tot}"
            return f"完成批次 {b_num}"
        return "完成一个翻译批次"

    # Specific stages
    if any(k in op or k in stage for k in ("pdf_parse", "extract_pages", "parse_start", "document_processing")):
        return "开始解析文档与版面结构"
    if any(k in op or k in stage for k in ("ocr_page", "ocr_processing")):
        page_no = meta.get("page_no")
        if page_no:
            return f"完成第 {page_no} 页文字识别 (OCR)"
        return "完成文字识别 (OCR)"
    if any(k in op or k in stage for k in ("split_and_clean", "segmentation")):
        return "完成原文分句切分"
    if any(k in op or k in stage for k in ("deterministic_cleanup", "llm_cleanup", "source_cleanup")):
        return "完成原文整理与断行规整"
    if any(k in op or k in stage for k in ("qa_check", "qa")):
        return "完成译文质量自检"
    if any(k in op or k in stage for k in ("repair_pass", "repair")):
        return "完成格式校验与自愈"
    if any(k in op or k in stage for k in ("build_delivery", "finalization", "docx_build")):
        return "完成交付文件生成"
    if any(k in op or k in stage for k in ("tm_lookup", "tm")):
        return "完成记忆库匹配检索"
    if any(k in op or k in stage for k in ("term_lookup", "terminology")):
        return "完成术语库对齐与检查"
    if any(k in op or k in stage for k in ("job_start", "pipeline_start", "queued")):
        return "已排入处理队列"
    if any(k in op or k in stage for k in ("job_finish", "completed")):
        return "任务全部处理完成"
    if any(k in op or k in stage for k in ("cancel_requested", "cancelling")):
        return "已请求取消任务"
    if any(k in op or k in stage for k in ("job_cancelled", "cancelled")):
        return "任务已取消"
    if any(k in op or k in stage for k in ("error", "failed")):
        err_msg = meta.get("error") or raw_msg or "处理出现异常"
        return f"处理异常: {err_msg[:30]}"

    clean = raw_msg or f"{stage} {op}"
    clean = re.sub(r"\[.*?\]", "", clean).strip()
    clean_lower = clean.lower()
    if clean_lower in {"start", "started", "pipeline_start", "job_start"}:
        return "开始执行任务流水线"
    if clean_lower in {"done", "finished", "completed", "finish"}:
        return "任务步骤执行完成"
    if clean_lower in {"resume", "resumed"}:
        return "继续执行任务"
    return clean or "执行系统处理"


def render_performance_drawer(
    job_id: str,
    state: Optional[Dict[str, Any]] = None,
    runtime_view: Optional[Dict[str, Any]] = None,
    on_retry: Optional[Any] = None,
    on_resume: Optional[Any] = None,
    on_cancel: Optional[Any] = None,
) -> None:
    """Unified entry for the performance / execution details drawer."""
    view_key = f"perf_view_mode_{job_id}"
    current_mode = st.session_state.get(view_key, "simple")

    if current_mode == "advanced":
        _render_advanced_view(job_id, state=state, runtime_view=runtime_view)
    else:
        _render_simple_view(
            job_id,
            state=state,
            runtime_view=runtime_view,
            on_retry=on_retry,
            on_resume=on_resume,
            on_cancel=on_cancel,
        )


def _render_simple_view(
    job_id: str,
    state: Optional[Dict[str, Any]] = None,
    runtime_view: Optional[Dict[str, Any]] = None,
    on_retry: Optional[Any] = None,
    on_resume: Optional[Any] = None,
    on_cancel: Optional[Any] = None,
) -> None:
    """Render the simplified primary runtime details view."""
    view = runtime_view or {}
    from folith.ui.workspace.runtime_status import runtime_presentation, progress_html
    model = runtime_presentation(view, state)
    raw_status = model["status"]
    _, status_color = _map_status(raw_status)
    status_label = model["status_label"]
    stage_title = model["operation"]
    pipeline_label = model["stage_label"]
    stage_explanation = model["detail"]
    completed, total, unit = model["completed"], model["total"], model["unit"]

    # 5. Header: Status & Stage
    st.markdown(
        (_h('<div style="margin-bottom: 14px;"><div class="tp-runtime-drawer-kicker" style="font-size: 0.78em; font-weight: 600; text-transform: uppercase; color: gray; letter-spacing: 0.5px; margin-bottom: 6px;">运行概要</div><div style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px;"><span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: ') + str(status_color) + ';"></span><span style="font-size: 0.9em; font-weight: 600; color: ' + str(status_color) + ';">' + str(escape(status_label)) + '</span></div><div style="font-size: 1.35em; font-weight: 700; color: var(--text-color, #1e293b); line-height: 1.25;">' + str(escape(stage_title)) + _h('</div><div style="display: flex; gap: 14px; font-size: 0.82em; color: gray; margin-top: 6px;"><span>任务阶段：<strong>') + str(escape(pipeline_label)) + _h('</strong></span></div><div style="font-size: 0.83em; color: #475569; background: rgba(37, 99, 235, 0.05); border-left: 3px solid #2563eb; padding: 7px 11px; border-radius: 4px; margin-top: 10px; line-height: 1.45;">💡 <strong>阶段说明</strong>：') + str(escape(stage_explanation)) + '</div></div>'),
        unsafe_allow_html=True,
    )

    # Task timing and activity freshness share the HUD's semantics.
    st.markdown(progress_html(model, drawer=True), unsafe_allow_html=True)
    progress_text = (f"已完成 {completed:,} / {total:,} {unit}" if total else "当前动作进度未知")
    if model["progress_known"]:
        progress_text += f" · {model['percent']:.1f}%"
    if model.get("is_paused"):
        progress_text += " · 已暂停"
    elif model.get("is_stopped"):
        progress_text += f" · {model['status_label']}"
    st.markdown(
        f'<div class="tp-run-progress-label">{escape(progress_text)}</div>'
        f'<div class="tp-run-time">{escape(model["task_time"])} · {escape(model["freshness"])}</div>',
        unsafe_allow_html=True,
    )
    detail_msg = stage_explanation

    st.markdown(
        (_h('<div style="background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.12); border-radius: 8px; padding: 12px 14px; margin-bottom: 18px;"><div style="font-size: 0.78em; font-weight: 600; text-transform: uppercase; color: gray; letter-spacing: 0.5px; margin-bottom: 4px;">当前步骤</div><div style="font-size: 0.95em; font-weight: 600; margin-bottom: 4px;">') + str(escape(stage_title)) + '</div><div style="font-size: 0.85em; color: gray; line-height: 1.45;">' + str(escape(str(detail_msg))) + '</div></div>'),
        unsafe_allow_html=True,
    )

    # 8. Recent Activity (3–5 items, mapped to user language)
    raw_events = view.get("user_events") or view.get("events") or (view.get("runtime") or {}).get("events")
    events = [{"timestamp": e.get("timestamp") or e.get("at"),
               "stage": e.get("stage"), "operation": e.get("event"),
               "metadata": {"message": e.get("message")}} for e in raw_events] if raw_events else _load_recent_events(job_id, limit=5)
    events = [e for e in events if not e.get("stage") or str(e["stage"]).lower() in {model["stage"], model["operation_id"]}]

    st.markdown(
        _h('<div style="font-size: 0.78em; font-weight: 600; text-transform: uppercase; color: gray; letter-spacing: 0.5px; margin-bottom: 8px;">最近活动</div>'),
        unsafe_allow_html=True,
    )
    if events:
        total_batches_hint = total if unit == "批次" else None
        for ev in reversed(events[-4:]):
            clk = _format_clock(ev.get("timestamp") or ev.get("at"))
            msg = _map_activity_event(ev, total_batches_hint=total_batches_hint)
            st.markdown(
                f'<div style="display: flex; gap: 12px; font-size: 0.85em; padding: 5px 0; border-bottom: 1px solid rgba(128,128,128,0.08); align-items: baseline;">'
                f'<span style="font-family: monospace; color: gray; flex-shrink: 0;">{escape(clk)}</span>'
                f'<span style="color: var(--text-color, #334155);">{escape(msg)}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
    else:
        st.markdown(
            _h('<div style="font-size: 0.85em; color: gray; padding: 4px 0 10px 0;">暂无最近活动</div>'),
            unsafe_allow_html=True,
        )

    # 9. Canonical Bottom Action Button
    import core
    if (view.get("runtime") or {}).get("segment_operation") and raw_status not in core.RUNTIME_ACTIVE_STATUSES:
        st.caption(_t("段落操作已停止；请返回原段落查看结果或重试。"))
    elif raw_status in {"running", "waiting_external", "starting", "queued", "resume_requested"}:
        if st.button(_t("取消任务"), key=f"btn_cancel_{job_id}", type="secondary", width="stretch"):
            if callable(on_cancel):
                on_cancel()
            else:
                core.request_job_cancel(job_id)
                st.rerun()
    elif raw_status == "cancelling":
        st.button(_t("正在取消…"), key=f"btn_cancelling_{job_id}", disabled=True, width="stretch")
    elif raw_status in {"failed", "stalled"}:
        if st.button(_t("重试当前步骤"), key=f"btn_retry_{job_id}", type="primary", width="stretch"):
            if callable(on_retry):
                on_retry()
            else:
                core.retry_job_step(job_id)
                st.session_state.pop("runtime_drawer_job_id", None)
                st.rerun()
    elif raw_status in {"interrupted", "cancelled", "idle_incomplete"}:
        if st.button(_t("继续处理"), key=f"btn_resume_{job_id}", type="primary", width="stretch"):
            if callable(on_resume):
                on_resume()
            else:
                st.session_state.pop("runtime_drawer_job_id", None)
                st.rerun()
    elif raw_status in {"completed", "success"}:
        if st.button(_t("查看结果"), key=f"btn_view_{job_id}", type="primary", width="stretch"):
            st.session_state.pop("runtime_drawer_job_id", None)
            st.session_state["workspace_mode"] = True
            st.session_state["active_job_id"] = job_id
            st.session_state["workspace_section"] = "report" if state and state.get("p2_done") else "translation"
            st.rerun()

    # 10. Secondary Low-weight Entry: 高级诊断 >
    st.markdown("<div style='text-align: center; margin-top: 10px;'>", unsafe_allow_html=True)
    if st.button(_t("高级诊断  ›"), key=f"btn_to_adv_{job_id}", help=_t("查看 CPU、内存、流水线耗时、Token 与详细诊断数据")):
        st.session_state[f"perf_view_mode_{job_id}"] = "advanced"
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(_t("技术详情 ▸"), expanded=False):
        st.json(view.get("runtime") or {})

    if st.button(_t("关闭运行详情"), key=f"runtime_drawer_close_{job_id}", width="stretch"):
        st.session_state.pop("runtime_drawer_job_id", None)
        st.rerun()


def _render_advanced_view(
    job_id: str,
    state: Optional[Dict[str, Any]] = None,
    runtime_view: Optional[Dict[str, Any]] = None,
) -> None:
    """Render the detailed secondary diagnostics view."""
    # Top bar: Return to simple view + metadata
    c_back, c_meta = st.columns([1, 2])
    with c_back:
        if st.button(_t("‹  返回运行状态"), key=f"btn_back_simple_{job_id}"):
            st.session_state[f"perf_view_mode_{job_id}"] = "simple"
            st.rerun()
    with c_meta:
        st.caption(_t('任务 ID: `{v0}`', v0=job_id))

    # Load active or latest run summary
    active_run = trace.get_active_run(job_id)
    summary: Dict[str, Any] = {}
    history: List[Dict[str, Any]] = []

    job_perf_dir = Path("outputs") / job_id / ".performance"
    runs_dir = job_perf_dir / "runs"
    history_file = job_perf_dir / "history.json"

    if history_file.is_file():
        try:
            history = json.loads(history_file.read_text(encoding="utf-8"))
        except Exception:
            history = []

    if active_run:
        summary = active_run.generate_summary()
    elif runs_dir.is_dir():
        run_folders = sorted([f for f in runs_dir.iterdir() if f.is_dir()], key=lambda p: p.name, reverse=True)
        if run_folders:
            latest_summary_file = run_folders[0] / "summary.json"
            if latest_summary_file.is_file():
                try:
                    summary = json.loads(latest_summary_file.read_text(encoding="utf-8"))
                except Exception:
                    summary = {}

    if not summary and isinstance(state, dict):
        summary = state.get("performance") or {}

    # 4 Clean Categories instead of cramped 5-tab bar
    tabs = st.tabs([_t(_label) for _label in ["性能概览", "流水线耗时", "诊断分析", "原始数据与历史"]])

    with tabs[0]:
        _render_adv_performance_tab(summary, active_run)

    with tabs[1]:
        _render_adv_pipeline_tab(summary)

    with tabs[2]:
        _render_adv_diagnostics_tab(summary)

    with tabs[3]:
        _render_adv_raw_tab(job_id, summary, history)


def _render_adv_performance_tab(summary: Dict[str, Any], active_run: Any) -> None:
    """Render CPU, RAM, throughput, latency, TM hit rate."""
    r = summary.get("resources", {})
    m = summary.get("model_metrics", {})
    tm = summary.get("tm_and_cache", {})
    stages = summary.get("stages", {})
    wall_s = float(summary.get("wall_time_s") or 0.0)

    trans_stage = stages.get("translation", {}) or stages.get("translation_pipeline", {})
    tp = trans_stage.get("throughput", {})
    tp_str = f"{tp.get('items_per_s', 0.0)} 句/秒" if tp.get("items_per_s") else "—"

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("处理吞吐量", tp_str)
        st.metric("模型平均延迟", f"{m.get('avg_call_latency_s', 0.0)} 秒")
    with c2:
        st.metric("CPU 利用率", f"{r.get('process_cpu_avg_pct', 0.0)}%")
        st.metric("TM 记忆命中率", f"{tm.get('tm_hit_ratio', 0.0) * 100:.1f}%")
    with c3:
        st.metric("进程内存 RSS", f"{r.get('process_rss_peak_mb', 0.0)} MB")
        st.metric("大模型调用数", f"{m.get('total_calls', 0)} 次")

    st.markdown("---")
    st.markdown("##### 运行画像")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(f"- **总耗时**: `{_format_duration_friendly(wall_s)}` ({wall_s:.2f} 秒)")
        st.markdown(f"- **CPU/Wall 比率**: `{summary.get('cpu_wall_ratio', 0.0)}`")
        st.markdown(f"- **线程峰值**: `{r.get('thread_peak', 1)}`")
    with g2:
        st.markdown(f"- **GPU 检测**: `{'已启用' if r.get('gpu_detected') else '未启用 / CPU安全回退'}`")
        st.markdown(f"- **Trace 模式**: `{summary.get('trace_level', 'standard')}`")
        unacc_s = summary.get("unaccounted_time_s", 0.0)
        unacc_pct = summary.get("unaccounted_pct", 0.0)
        st.markdown(f"- **未计入调度耗时**: `{unacc_s} 秒 ({unacc_pct}%)`")


def _render_adv_pipeline_tab(summary: Dict[str, Any]) -> None:
    """Render pipeline stage timings with bounded percentages."""
    stages = summary.get("stages", {})
    if not stages:
        st.info(_t("尚无流水线阶段记录。"))
        return

    st.markdown("##### 各阶段耗时与吞吐量拆解")
    rows = []
    for s_name, s_info in stages.items():
        w_s = s_info.get("wall_time_s", 0.0)
        pct_val = s_info.get("pct_of_total", 0.0)
        # Guarantee valid bounded percentage
        try:
            pct_num = min(100.0, max(0.0, float(pct_val)))
            pct_str = f"{pct_num:.1f}%" if pct_num > 0 else "—"
        except (ValueError, TypeError):
            pct_str = "—"

        cpu_s = s_info.get("cpu_time_s", 0.0)
        ratio = s_info.get("cpu_wall_ratio", 0.0)
        tp = s_info.get("throughput", {})
        tp_str = ", ".join(f"{v} {k}" for k, v in tp.items()) if tp else "—"
        stat = s_info.get("status", "success")
        icon = "✓" if stat in {"success", "completed"} else "●" if stat == "running" else "!"

        rows.append({
            "状态": icon,
            "阶段": f"{_map_stage_name(s_name)} ({s_name})",
            "耗时 (秒)": w_s,
            "占比": pct_str,
            "CPU 时间 (秒)": cpu_s,
            "CPU/Wall 比": ratio,
            "吞吐量": tp_str,
        })

    st.table(rows)


def _render_adv_diagnostics_tab(summary: Dict[str, Any]) -> None:
    """Render latency percentiles, slowest batches, and tokens."""
    if not summary:
        st.info(_t("尚无诊断数据。"))
        return

    lp = summary.get("latency_percentiles", {})
    m = summary.get("model_metrics", {})
    err = summary.get("errors_and_retries", {})
    unacc = summary.get("unaccounted_time_s", 0.0)
    unacc_pct = summary.get("unaccounted_pct", 0.0)

    st.markdown("##### 延迟分布 (Percentiles)")
    b_pct = lp.get("batch_seconds", {})
    s_pct = lp.get("segment_seconds", {})
    m_pct = lp.get("model_call_seconds", {})
    pct_rows = [
        {"指标": "批次耗时 (秒)", "P50": b_pct.get("p50", 0.0), "P90": b_pct.get("p90", 0.0), "P95": b_pct.get("p95", 0.0), "Max": b_pct.get("max", 0.0)},
        {"指标": "单段预估 (秒)", "P50": s_pct.get("p50", 0.0), "P90": s_pct.get("p90", 0.0), "P95": s_pct.get("p95", 0.0), "Max": s_pct.get("max", 0.0)},
        {"指标": "模型响应 (秒)", "P50": m_pct.get("p50", 0.0), "P90": m_pct.get("p90", 0.0), "P95": m_pct.get("p95", 0.0), "Max": m_pct.get("max", 0.0)},
    ]
    st.table(pct_rows)

    slowest = summary.get("slowest_batches", [])
    if slowest:
        st.markdown("##### Top 最慢批次")
        s_rows = []
        for b in slowest[:5]:
            s_rows.append({
                "批次": f"#{b.get('batch_id')}",
                "段数": b.get("segment_count"),
                "字符数": b.get("source_chars"),
                "耗时 (秒)": round(b.get("duration_ms", 0.0) / 1000.0, 2),
                "TM 命中": b.get("tm_hits", 0),
                "调用模型": "是" if b.get("model_called") else "否",
            })
        st.table(s_rows)

    st.markdown("##### Token 消耗统计")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Prompt Tokens", f"{m.get('total_prompt_tokens', 0):,}")
    with c2:
        st.metric("Output Tokens", f"{m.get('total_completion_tokens', 0):,}")
    with c3:
        st.metric("Cached Tokens", f"{m.get('total_cached_tokens', 0):,}")
    with c4:
        st.metric("重试次数", f"{err.get('retries', 0)}")

    if unacc_pct > 10.0 and unacc > 3.0:
        st.warning(_t('⚠️ 发现未记录空闲时间: **{v0} 秒** ({v1}%)。可能是由于 IO 阻塞、模型网络排队或锁等待引起。', v0=unacc, v1=unacc_pct))


def _render_adv_raw_tab(job_id: str, summary: Dict[str, Any], history: List[Dict[str, Any]]) -> None:
    """Render historical runs, performance.md, and raw summary JSON."""
    job_perf_dir = Path("outputs") / job_id / ".performance" / "runs"
    latest_run_dir = None
    if job_perf_dir.is_dir():
        run_folders = sorted([f for f in job_perf_dir.iterdir() if f.is_dir()], key=lambda p: p.name, reverse=True)
        if run_folders:
            latest_run_dir = run_folders[0]

    if history:
        st.markdown("##### 历史运行记录对比 (Historical Runs)")
        h_rows = []
        for h in reversed(history[-10:]):
            h_rows.append({
                "Run ID": h.get("run_id", "—")[-15:],
                "总耗时": f"{h.get('wall_time_s', 0.0):.1f}s",
                "段数": h.get("segment_count", 0),
                "TM 命中率": f"{h.get('tm_hit_ratio', 0.0) * 100:.1f}%",
                "峰值内存": f"{h.get('peak_ram_mb', 0.0):.1f}MB",
                "Tokens": f"{h.get('total_tokens', 0):,}",
                "状态": h.get("status", "—"),
            })
        st.table(h_rows)

    if latest_run_dir and (latest_run_dir / "performance.md").is_file():
        with st.expander(_t("📄 查看 Coding Agent 诊断简报 (performance.md)"), expanded=False):
            st.markdown((latest_run_dir / "performance.md").read_text(encoding="utf-8"))

    with st.expander(_t("🌲 查看结构化 Summary JSON"), expanded=False):
        st.json(summary)


def _load_recent_events(job_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Helper to load recent structured events safely."""
    import core
    raw_events = core.read_runtime_events(job_id, limit=limit, visibility="user")
    if raw_events:
        return [{
            "timestamp": e.get("timestamp") or e.get("at"),
            "stage": e.get("stage"),
            "operation": e.get("event"),
            "metadata": {"message": e.get("message")},
        } for e in raw_events]
    events = []
    job_perf_dir = Path("outputs") / job_id / ".performance" / "runs"
    if job_perf_dir.is_dir():
        run_folders = sorted([f for f in job_perf_dir.iterdir() if f.is_dir()], key=lambda p: p.name, reverse=True)
        if run_folders:
            ev_path = run_folders[0] / "events.jsonl"
            if ev_path.is_file():
                try:
                    for line in ev_path.read_text(encoding="utf-8").splitlines()[-30:]:
                        if line.strip():
                            events.append(json.loads(line))
                except Exception:
                    pass

    return events[-limit:]
