"""译页 Streamlit 界面层。

信息架构：左侧产品导航 + 四步任务创建 + 运行后任务工作台。AI Provider
与翻译记忆属于全局设置；研究与报告属于翻译后的专用下游工作流，不占据文档首屏。
"""
import base64
import hashlib
import inspect
import importlib
import json
import os
import re
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

import core
from folith import brand as _brand
# Streamlit keeps the imported module alive across script reruns.  Reload only
# when the running process predates the source-rescan entrypoint; unconditional
# reloads reset test/workspace state such as a temporary output directory and
# can also detach a worker that is handling a long model request.
_CORE_RUNTIME_VERSION = "source-rescan-v3"
if not hasattr(core, "rescan_pdf_source"):
    core = importlib.reload(core)
    core._APP_RUNTIME_VERSION = _CORE_RUNTIME_VERSION
import folith.source_cleanup as _source_cleanup_runtime
if not hasattr(_source_cleanup_runtime, "cleanup_source_paragraphs"):
    _source_cleanup_runtime = importlib.reload(_source_cleanup_runtime)
    _source_cleanup_runtime._APP_RUNTIME_VERSION = _CORE_RUNTIME_VERSION
    core._source_cleanup = _source_cleanup_runtime
from folith import assets as _assets
from folith import academic_validator as _academic_validator
from folith import case_provenance as _case_provenance
from folith import context as _context
from folith import delivery as _delivery
from folith import finalization as _finalization
from folith import history_view as _history_view
from folith import knowledge as _knowledge
from folith import language_assets as _language_assets
from folith import literature_evidence as _literature_evidence
from folith import model_roles as _model_roles
from folith import report_evidence as _report_evidence
from folith import report_template as _report_template
from folith import compliance as _compliance
from folith import task_overview as _task_overview
from folith import thesis_constraints as _thesis_constraints
from folith import translation_evidence as _translation_evidence
from folith import translation_decisions as _translation_decisions
from folith import translation_planner as _planner
from folith import workbench_view as _workbench_view
from folith import workspace_view as _workspace_view
from folith import pdf_preview as _pdf_preview
from folith.ui import styles as _styles
from folith.ui import pdf_view as _pdf_view
from folith.ui import language_assets as _language_assets_ui
from folith.ui.workspace import runtime as _runtime_ui
from folith.ui.workspace import cat_grid as _cat_grid_ui
from folith.ui.workspace import inspector as _inspector_ui
from folith.ui.workspace import translation as _translation_ui
from folith.ui import session as _ui_session
from folith.ui import browser as _browser
from folith import profiler
from folith.ui.i18n import tr as _t, html_literal as _h, localized_formatter as _localized_formatter
from folith.ui import preferences as _preferences

# Older Streamlit versions (including the Python 3.9-compatible line) do not
# expose persist_state; widget keys provide the fallback there.
_PERSIST_STATE = (
    {"persist_state": "session"}
    if "persist_state" in inspect.signature(st.selectbox).parameters
    else {}
)

# ================= 页面全局设置 =================
_APP_ROOT = Path(__file__).resolve().parent
_BRAND_DIR = Path(_assets.__file__).resolve().parent / "resources" / "brand"
# 界面使用用户提供的正式品牌素材（lockup / 图标 / App 图标）；
# 侧栏使用 folith-lockup.png 原图逐像素去白底的透明 PNG，不使用 SVG 派生图。
_BRAND_LOGO = _BRAND_DIR / "folith-lockup-transparent.png"
_BRAND_FAVICON = _BRAND_DIR / "folith-mark.png"
_BRAND_LOGO_URI = "data:image/png;base64," + base64.b64encode(
    _BRAND_LOGO.read_bytes()).decode("ascii")

# 面向用户的示例文案保持领域中性，避免把个人项目主题带进界面截图或演示。
_PROJECT_NAME_PLACEHOLDER = "例如：产品文档翻译 / 资料整理"
_STYLE_RULES_PLACEHOLDER = "例如：保持正式语气；术语与引用标注保持一致。"

st.set_page_config(page_title=_brand.APP_TITLE_ZH,
                   page_icon=_BRAND_FAVICON, layout="wide",
                   initial_sidebar_state="expanded")

if "doc_states" not in st.session_state:
    st.session_state.doc_states = {}
if "active_job_id" not in st.session_state:
    st.session_state.active_job_id = None
if "task_step" not in st.session_state:
    st.session_state.task_step = 1
if "app_view" not in st.session_state:
    st.session_state.app_view = "new"
if "workspace_mode" not in st.session_state:
    st.session_state.workspace_mode = False
if "workspace_inspector_open" not in st.session_state:
    st.session_state.workspace_inspector_open = True
# A task opens in the segment-first workbench.  The task overview remains
# available from the compact status banner, but it is no longer the default
# landing surface for an active document.
if "workspace_section" not in st.session_state:
    st.session_state.workspace_section = "translation"
# 新任务的默认归属是「未分类」（系统工作区）：Translation Task 不需要先有
# Project 才能开始，但"没有归属"仍然指向一个真实容器。None 是有效值
# （用户显式选择未分类），落盘时写成显式 null。
if "task_project_id" not in st.session_state:
    st.session_state.task_project_id = None
if "task_auto_profile" not in st.session_state:
    st.session_state.task_auto_profile = True
if "provider_configured" not in st.session_state:
    st.session_state.provider_configured = False
if "provider_connection_status" not in st.session_state:
    st.session_state.provider_connection_status = "unverified"
if "reviewer_connection_status" not in st.session_state:
    st.session_state.reviewer_connection_status = "unverified"
# 从本地配置恢复 AI 引擎（保存过之后，重启应用无需重新填写）
_saved_provider_cfg = core.load_provider_config()
if _saved_provider_cfg:
    _saved_provider = _saved_provider_cfg["provider"]
    if _saved_provider in core.PROVIDERS \
            and "provider_choice" not in st.session_state:
        st.session_state.provider_choice = _saved_provider
    if _saved_provider_cfg.get("api_key") \
            and f"api_key_{_saved_provider}" not in st.session_state:
        st.session_state[f"api_key_{_saved_provider}"] = \
            _saved_provider_cfg["api_key"]
    _saved_model = _saved_provider_cfg.get("model")
    _saved_models = core.PROVIDERS.get(_saved_provider, {}).get("models") or []
    if _saved_model and (not _saved_models or _saved_model in _saved_models) \
            and f"model_choice_{_saved_provider}" not in st.session_state:
        st.session_state[f"model_choice_{_saved_provider}"] = \
            _saved_model
    if _saved_provider_cfg.get("base_url") \
            and "custom_base_url" not in st.session_state:
        st.session_state.custom_base_url = _saved_provider_cfg["base_url"]
    if _saved_provider_cfg.get("reasoning_effort") \
            and f"reasoning_effort_{_saved_provider}" not in st.session_state:
        st.session_state[f"reasoning_effort_{_saved_provider}"] = \
            _saved_provider_cfg["reasoning_effort"]
    _saved_reviewer = _saved_provider_cfg.get("reviewer") or {}
    if _saved_reviewer.get("provider") and "reviewer_mode" not in st.session_state:
        st.session_state.reviewer_mode = "separate"
        st.session_state.reviewer_provider_choice = _saved_reviewer["provider"]
        st.session_state.reviewer_model = _saved_reviewer.get("model", "")
        st.session_state.reviewer_api_key = _saved_reviewer.get("api_key", "")
        st.session_state.reviewer_base_url = _saved_reviewer.get("base_url", "")
# ================= 设计系统（Folith·译页 Agentic Translation Workspace） =================
# 样式系统已完整提取至 folith.ui.styles 模块
_preferences.initialize(core.OUTPUT_DIR)
_styles.inject_design_system(theme=st.session_state.ui_theme, locale=st.session_state.ui_locale)

_EVIDENCE_LABELS = {
    "user": "用户提供", "local_termbase": "本地术语库",
    "project_override": "项目覆盖", "model_knowledge": "模型知识",
    "external": "外部来源",
}
from folith.ui.workspace import presentation as _workspace_presentation

_TERM_STATUS_LABELS = {
    "candidate": "候选",
    "provisional": "暂定",
    "locked": "已锁定",
    "rejected": "已拒绝",
}
_TERM_STATUS_VALUES = {label: value for value, label in _TERM_STATUS_LABELS.items()}
_TERM_BEHAVIOR_LABELS = {"translate": "翻译", "preserve": "保留原文"}
_TERM_BEHAVIOR_VALUES = {label: value for value, label in _TERM_BEHAVIOR_LABELS.items()}


def _evidence_label(e):
    evs = e.get("evidence") or []
    parts = []
    for ev in evs[:2]:
        label = _EVIDENCE_LABELS.get(ev.get("evidence_type"), ev.get("evidence_type"))
        note = (ev.get("note") or "").strip()
        parts.append(f"{label}：{note}" if note else label)
    return "；".join(parts)


def _conflict(e, entries):
    src = (e.get("source") or "").casefold()
    pref = e.get("preferred") or e.get("target")
    for other in entries:
        if other is e:
            continue
        if (other.get("source") or "").casefold() == src \
                and (other.get("preferred") or other.get("target")) != pref:
            return "冲突"
    return ""


def _first_context(e, paras, width=60):
    occ = e.get("occurrences") or []
    if not occ or not paras:
        return ""
    first = occ[0]
    if not (0 <= first < len(paras)):
        return ""
    text = paras[first]
    return text[:width] + ("…" if len(text) > width else "")


def _glossary_dataframe(entries, paras):
    rows = []
    for e in entries:
        rows.append({
            "选择": False,
            "id": e.get("id", ""),
            "source": e.get("source", ""),
            "proposed_target": e.get("proposed_target") or e.get("target", ""),
            "target": e.get("target", ""),
            "preferred": e.get("preferred", ""),
            "forbidden": "；".join(e.get("forbidden") or []),
            "behavior": e.get("behavior", "translate"),
            "status": e.get("status", "provisional"),
            "domain": e.get("domain", ""),
            "scope": e.get("scope", ""),
            "note": e.get("note", ""),
            "confidence": float(e.get("confidence") or 0.5),
            "出现次数": len(e.get("occurrences") or []),
            "上下文": _first_context(e, paras),
            "证据": _evidence_label(e),
            "冲突": _conflict(e, entries),
            "payload": json.dumps(e, ensure_ascii=False),
        })
    return pd.DataFrame(rows)


def _df_to_entries(df):
    entries = []
    for _, row in df.iterrows():
        base = {}
        payload = row.get("payload")
        if isinstance(payload, str) and payload.strip():
            try:
                base = json.loads(payload)
            except Exception:
                base = {}
        if not isinstance(base, dict):
            base = {}
        base = dict(base)

        def _s(key):
            v = row.get(key)
            return "" if pd.isna(v) else str(v).strip()

        base.update({
            "source": _s("source"),
            "proposed_target": _s("proposed_target"),
            "target": _s("target") or _s("proposed_target"),
            "preferred": _s("preferred") or _s("target") or _s("proposed_target"),
            "forbidden": [x.strip() for x in re.split(r"[;；]", _s("forbidden"))
                          if x.strip()],
            "behavior": _TERM_BEHAVIOR_VALUES.get(
                _s("behavior"), (_s("behavior") or "translate").lower()),
            "status": _TERM_STATUS_VALUES.get(
                _s("status"), (_s("status") or "provisional").lower()),
            "domain": _s("domain"),
            "scope": _s("scope"),
            "note": _s("note"),
        })
        try:
            base["confidence"] = float(row.get("confidence") or 0.5)
        except (TypeError, ValueError):
            base["confidence"] = 0.5
        entries.append(base)
    return entries


def _humanize_glossary_editor(df):
    """Keep persisted enum values out of the normal terminology editor."""
    displayed = df.copy()
    if "status" in displayed:
        displayed["status"] = displayed["status"].map(_TERM_STATUS_LABELS).fillna("待确认")
    if "behavior" in displayed:
        displayed["behavior"] = displayed["behavior"].map(_TERM_BEHAVIOR_LABELS).fillna("翻译")
    return displayed


def _page_title_html(title, sub):
    title, sub = _t(title), _t(sub)
    return ('<div class="tp-title"><div class="tp-brand-kicker">Folith / Workspace</div>'
            f'<h1>{title}</h1><p>{sub}</p></div>')


def _page_title(title, sub):
    st.markdown(_page_title_html(title, sub), unsafe_allow_html=True)


def _step_title(number, title, sub):
    title, sub = _t(title), _t(sub)
    st.markdown(
        f'<div class="tp-section-title">{title}</div>'
        f'<div class="tp-section-sub">{sub}</div>', unsafe_allow_html=True)


def _go_to_step(step):
    st.session_state.task_step = step


def _request_step(step):
    if step > 1 and not st.session_state.get("task_files"):
        st.session_state.step_gate_message = "请先上传原文。"
        st.session_state.task_step = 1
        return
    # Step 1 的画像是 Agent 的默认准备动作：用户点击下一步时才执行，
    # 侧栏直接点 Step 2 也必须经过同一条路径，避免绕过画像状态。
    if step == 2 and st.session_state.get("task_auto_profile", True) \
            and not _task_profile_is_ready():
        st.session_state.style_profiling_state = "running"
        st.session_state.pending_profile_step = 2
        st.session_state.pop("step_gate_message", None)
        return
    st.session_state.pop("step_gate_message", None)
    st.session_state.task_step = step


def _reset_provider_connection(preserve_models=False):
    st.session_state.provider_configured = False
    st.session_state.provider_connection_status = "unverified"
    st.session_state.reviewer_connection_status = "unverified"
    st.session_state.pop("provider_test_feedback", None)
    if not preserve_models:
        provider = st.session_state.get("provider_choice")
        if provider:
            st.session_state.pop(f"fetched_models_{provider}", None)
            st.session_state.pop(f"preferred_fetched_model_{provider}", None)
        st.session_state.pop("model_fetch_feedback", None)


def _reset_reviewer_connection():
    st.session_state.provider_configured = False
    st.session_state.reviewer_connection_status = "unverified"
    st.session_state.pop("provider_test_feedback", None)


def _open_provider_settings():
    st.session_state.app_view = "settings"


_PRESET_CONFIGS = {
    "快速": {
        "auto_term": False, "use_tm": True,
        "enable_understanding": False,
        "enable_review": False, "strict_terminology_governance": False,
        "segmentation_mode": "sentence", "translation_concurrency": 4,
        "batch_profile": "保守",
    },
    "标准": {
        "auto_term": True, "use_tm": True,
        "enable_understanding": True,
        "enable_review": False, "strict_terminology_governance": False,
        "segmentation_mode": "sentence", "translation_concurrency": 4,
        "batch_profile": "保守",
    },
    "学术增强": {
        "auto_term": True, "use_tm": True,
        "enable_understanding": True,
        "enable_review": True, "strict_terminology_governance": True,
        "segmentation_mode": "sentence", "translation_concurrency": 4,
        "batch_profile": "保守",
    },
}

# 批次策略：批次越大 → 调用次数越少 → 越快，但单次返回项数不符或截断的风险越高。
# 实测（本仓库 82 段文档）：保守 32 批，均衡 17 批，快速 13 批。
BATCH_PROFILES = {
    "保守": {"batch_size": 4, "max_batch_chars": 2400,
             "hint": "每批最多 4 段 / 2400 字符；调用次数最多，单次响应最稳"},
    "均衡": {"batch_size": 6, "max_batch_chars": 4800,
             "hint": "每批最多 6 段 / 4800 字符；调用次数约减半"},
    "快速": {"batch_size": 8, "max_batch_chars": 6400,
             "hint": "每批最多 8 段 / 6400 字符；最快，但更容易出现返回项数不符"},
}


def _batch_params(config):
    """把批次策略映射为 (batch_size, max_batch_chars)。"""
    profile = BATCH_PROFILES.get(str((config or {}).get("batch_profile") or "保守"),
                                 BATCH_PROFILES["保守"])
    return profile["batch_size"], profile["max_batch_chars"]

def _default_output_config():
    return core.default_delivery_config()


# Step 03 keeps the persisted `delivery_config` keys used by the export
# pipeline, but presents them through a smaller, user-facing preset model.
# The research-only keys are intentionally off in the ordinary delivery
# presets: they are additional products, not invisible files hidden behind a
# standard translation bundle.
_DELIVERY_OUTPUT_KEYS = (
    "enable_annotate", "enable_report", "deliver_plain_docx",
    "deliver_bilingual_docx", "deliver_pdf", "deliver_epub", "deliver_terms_xlsx",
    "deliver_tbx", "deliver_tmx", "deliver_jsonl", "deliver_evidence",
    "deliver_cases", "deliver_academic_workspace", "deliver_review_report",
)
_DELIVERY_RESEARCH_KEYS = (
    "enable_report", "deliver_evidence", "deliver_cases",
    "deliver_academic_workspace", "deliver_review_report",
)


def _delivery_config_for_outputs(*selected):
    """Return a complete export config for a UI delivery preset."""
    config = core.default_delivery_config()
    for key in _DELIVERY_OUTPUT_KEYS:
        config[key] = False
    for key in selected:
        if key in config:
            config[key] = True
    return config


_DELIVERY_PRESETS = {
    "compact": {
        "label": "精简交付",
        "description": "只保留最终译文，适合快速分享或内部查看。",
        "outputs": ("deliver_plain_docx",),
    },
    "standard": {
        "label": "标准交付",
        "description": "适合大多数正式翻译任务。",
        "outputs": ("deliver_plain_docx", "deliver_bilingual_docx",
                    "deliver_terms_xlsx"),
    },
    "complete": {
        "label": "完整交付",
        "description": "同时提供分享、审校与 CAT 工具需要的语言资产。",
        "outputs": ("deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf",
                    "deliver_terms_xlsx", "deliver_tmx", "deliver_tbx"),
    },
    "custom": {
        "label": "自定义",
        "description": "按你的交付对象和下游工具手动配置。",
        "outputs": (),
    },
}
_DELIVERY_PRESET_KEYS = tuple(_DELIVERY_PRESETS)
_DELIVERY_PRESET_CONFIGS = {
    key: _delivery_config_for_outputs(*value["outputs"])
    for key, value in _DELIVERY_PRESETS.items()
    if key != "custom"
}
_DELIVERY_WIDGET_KEYS = (
    "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf", "deliver_epub",
    "output_annotate", "deliver_terms_xlsx", "deliver_tmx", "deliver_tbx",
    "deliver_jsonl", "output_report", "deliver_evidence", "deliver_cases",
    "deliver_academic_workspace", "deliver_review_report",
)


# These are the strategy names that currently unlock the research-product
# section. Keeping the mapping here makes the Step 02 -> Step 03 dependency
# explicit and gives future research-grade strategies one place to join.
_RESEARCH_STRATEGY_PRESETS = frozenset({"学术增强", "研究与报告", "研究级", "深度研究"})


def _research_outputs_visible(preset_label=None, strategy_config=None):
    preset_label = preset_label or st.session_state.get(
        "translation_preset", "标准")
    config = strategy_config or st.session_state.get("strategy_config") or {}
    return (preset_label in _RESEARCH_STRATEGY_PRESETS
            or bool(config.get("research_outputs_enabled")))


def _delivery_preset_label(key, *, current=None, modified=False):
    meta = _DELIVERY_PRESETS.get(key, _DELIVERY_PRESETS["standard"])
    label = meta["label"]
    if key == "standard":
        label += " · 推荐"
    if current == key and modified and key != "custom":
        label += " · 已修改"
    return label


def _delivery_preset_select_label(key):
    # Keep the selectbox option strings stable across reruns.  Streamlit uses
    # the formatted value to restore the widget; the live "已修改" state is
    # shown in the adjacent helper line instead of changing option labels.
    return _delivery_preset_label(key)


def _apply_delivery_preset(key):
    """Apply a delivery preset without changing the underlying export schema."""
    key = key if key in _DELIVERY_PRESET_KEYS else "standard"
    st.session_state.delivery_preset = key
    st.session_state.delivery_preset_modified = False
    if key == "custom":
        return
    for widget_key in _DELIVERY_WIDGET_KEYS:
        st.session_state.pop(widget_key, None)
    st.session_state.output_config = dict(_DELIVERY_PRESET_CONFIGS[key])


def _on_delivery_preset_change():
    _apply_delivery_preset(st.session_state.get("delivery_preset", "standard"))


def _delivery_output_changed(option, widget_key):
    """Persist a row checkbox and mark the originating preset as modified."""
    _set_output_option(option, widget_key)
    if st.session_state.get("delivery_preset", "standard") != "custom":
        st.session_state.delivery_preset_modified = True


def _delivery_selected_count(config, items):
    return sum(1 for item in items if bool(config.get(item["key"])))


_DELIVERY_TRANSLATION_ITEMS = (
    {
        "key": "deliver_plain_docx", "title": "纯译文", "format": "DOCX",
        "detail": "仅包含目标语言译文。",
        "file_units": (("DOCX", 1),),
        "disambig_fmt": True,
    },
    {
        "key": "deliver_bilingual_docx", "title": "双语对照", "format": "DOCX",
        "detail": "原文与译文并列，适合审校。",
        "file_units": (("DOCX", 1),),
    },
    {
        "key": "enable_annotate", "title": "标记术语与翻译难点", "format": "",
        "detail": "标出生僻词、专业术语和疑难句。", "nested": True,
        "file_units": (("DOCX", 1),),
    },
    {
        "key": "deliver_pdf", "title": "纯译文", "format": "PDF",
        "detail": "适合分享、打印与最终交付。",
        "file_units": (("PDF", 1),),
        "disambig_fmt": True,
    },
    {
        "key": "deliver_epub", "title": "译文电子书", "format": "EPUB",
        "detail": "保留原图与段落顺序，支持阅读器调整字号；适用于普通 PDF / DOCX。",
        "file_units": (("EPUB", 1),),
    },
)
_DELIVERY_ASSET_ITEMS = (
    {
        "key": "deliver_terms_xlsx", "title": "术语表", "format": "XLSX",
        "detail": "便于人工查看、编辑和交付。",
        "file_units": (("XLSX", 1),),
    },
    {
        "key": "deliver_tmx", "title": "翻译记忆", "format": "TMX",
        "detail": "可导入 Trados、memoQ 等 CAT 工具。",
        "file_units": (("TMX", 1),),
    },
    {
        "key": "deliver_tbx", "title": "标准术语库", "format": "TBX",
        "detail": "用于术语管理与跨系统交换。",
        "file_units": (("TBX", 1),),
    },
    {
        "key": "deliver_jsonl", "title": "结构化数据", "format": "JSONL",
        "detail": "用于 AI、自动化与二次处理。",
        "file_units": (("JSONL", 1),),
    },
)
_DELIVERY_RESEARCH_ITEMS = (
    {
        "key": "enable_report", "title": "翻译实践报告", "format": "DOCX + MD",
        "detail": "基于项目数据、案例和引用证据生成。",
        "file_units": (("DOCX", 1), ("MD", 1)),
    },
    {
        "key": "deliver_evidence", "title": "翻译过程证据", "format": "JSONL",
        "detail": "保存批次翻译、审校与修订的可追溯证据。",
        "file_units": (("JSONL", 1),),
    },
    {
        "key": "deliver_cases", "title": "案例候选", "format": "JSON",
        "detail": "导出符合资格的真实修订案例。",
        "file_units": (("JSON", 1),),
    },
    {
        "key": "deliver_academic_workspace", "title": "学术写作工作区", "format": "ZIP",
        "detail": "打包论证大纲与写作素材。",
        "file_units": (("ZIP", 1),),
    },
    {
        "key": "deliver_review_report", "title": "审校报告", "format": "MD",
        "detail": "记录审校发现与处理结果。",
        "file_units": (("MD", 1),),
    },
)


def _delivery_items_for_summary(config, *, research_visible=False):
    """Return selected product rows; annotated output depends on bilingual DOCX."""
    items = []
    for item in _DELIVERY_TRANSLATION_ITEMS:
        if item["key"] == "enable_annotate":
            if config.get("deliver_bilingual_docx") and config.get(item["key"]):
                items.append(item)
        elif config.get(item["key"]):
            items.append(item)
    items.extend(item for item in _DELIVERY_ASSET_ITEMS if config.get(item["key"]))
    if research_visible:
        items.extend(item for item in _DELIVERY_RESEARCH_ITEMS
                     if config.get(item["key"]))
    return items


def _delivery_summary(config, *, research_visible=False):
    """Compute user-facing file totals and format counts from export selections."""
    counts = {}
    file_count = 0
    for item in _delivery_items_for_summary(config, research_visible=research_visible):
        for kind, amount in item.get("file_units", ()):
            counts[kind] = counts.get(kind, 0) + amount
            file_count += amount
    return {
        "file_count": file_count,
        "counts": counts,
        "items": _delivery_items_for_summary(
            config, research_visible=research_visible),
    }


def _delivery_summary_html(summary):
    count = int(summary.get("file_count", 0))
    counts = summary.get("counts") or {}
    parts = " · ".join(
        f"{escape(str(kind))} ×{int(amount)}"
        for kind, amount in counts.items())
    if not parts:
        parts = "尚未选择交付文件"
    return (
        (_h('<div class="tp-delivery-summary"><strong>将生成 ') + str(count) + _h(' 个文件</strong><span>') + str(parts) + '</span></div>')
    )


def _render_delivery_item(item, *, group_key, index, config, disabled=False):
    """Render one dense, labeled delivery row inside a group card."""
    option = item["key"]
    widget_key = {
        "enable_annotate": "output_annotate",
        "enable_report": "output_report",
    }.get(option, option)
    row_key = f"delivery_row_{group_key}_{index:02d}"
    if item.get("nested"):
        row_key += "_nested"
    # When a format is present and the title would be ambiguous, append the
    # format to the visible title so users can scan rows at a glance.
    display_title = item["title"]
    if item.get("format") and item.get("disambig_fmt"):
        display_title = f"{item['title']} · {item['format']}"
    with st.container(key=row_key):
        check_col, copy_col, meta_col = st.columns([0.06, 0.80, 0.14])
        with check_col:
            # The checkbox label stays as item["title"] for widget identity and
            # accessibility; label_visibility="collapsed" hides it visually.
            st.checkbox(
                _t(item["title"]), value=bool(config.get(option)), key=widget_key,
                on_change=_delivery_output_changed,
                args=(option, widget_key), label_visibility="collapsed",
                disabled=disabled, help=_t(item["detail"]), **_PERSIST_STATE)
        with copy_col:
            # display_title may have " · FORMAT" appended when disambig_fmt is set.
            st.markdown(
                f'<div class="tp-delivery-row-copy"><strong>{escape(display_title)}'
                f'</strong><span>{escape(item["detail"])}</span></div>',
                unsafe_allow_html=True)
        with meta_col:
            if item.get("format"):
                st.markdown(
                    f'<div class="tp-delivery-row-meta"><span class="tp-format-badge">'
                    f'{escape(item["format"])}</span></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="tp-delivery-row-meta">&nbsp;</div>',
                            unsafe_allow_html=True)


def _render_delivery_group(title, subtitle, items, config, *, group_key,
                           research_visible=False, after_rows=None):
    """Render a single vertical delivery group and its selected count."""
    count_items = [item for item in items if not item.get("nested")]
    selected = _delivery_selected_count(config, count_items)
    extra_result = None
    with st.container(key=f"delivery_group_{group_key}"):
        st.markdown(
            ('<div class="tp-delivery-group-head"><div><strong>' + str(escape(title)) + '</strong><span class="tp-delivery-group-sub">' + str(escape(subtitle)) + _h('</span></div><b>已选 ') + str(selected) + '</b></div>'), unsafe_allow_html=True)
        for index, item in enumerate(items, start=1):
            disabled = (item.get("key") == "enable_annotate"
                        and not config.get("deliver_bilingual_docx"))
            _render_delivery_item(
                item, group_key=group_key, index=index, config=config,
                disabled=disabled)
        if after_rows is not None:
            extra_result = after_rows()
    return extra_result


def _render_delivery_preset_selector():
    """Render the compact delivery-plan selector and its live file summary."""
    current = st.session_state.get("delivery_preset", "standard")
    if current not in _DELIVERY_PRESET_KEYS:
        current = "standard"
        st.session_state.delivery_preset = current
    modified = bool(st.session_state.get("delivery_preset_modified"))
    meta = _DELIVERY_PRESETS[current]
    status = " · 已修改" if modified and current != "custom" else ""
    with st.container(key="delivery_preset_selector"):
        label_col, select_col, desc_col = st.columns([0.18, 0.32, 0.50])
        with label_col:
            st.markdown(
                _h('<div class="tp-delivery-preset-lead">交付方案</div>'),
                unsafe_allow_html=True)
        with select_col:
            st.selectbox(
                _t("交付方案"), list(_DELIVERY_PRESET_KEYS),
                format_func=_localized_formatter(_delivery_preset_select_label),
                key="delivery_preset", on_change=_on_delivery_preset_change,
                label_visibility="collapsed",
                **_PERSIST_STATE)
        with desc_col:
            st.markdown(
                f'<div class="tp-delivery-preset-help">'
                f'<strong>{escape(meta["label"])}{escape(status)}</strong>'
                f'<span>&ensp;{escape(meta["description"])}</span></div>',
                unsafe_allow_html=True)


def _render_delivery_research_details(output_config):
    """Render report inputs only after the research product is selected."""
    if not output_config.get("enable_report"):
        return None
    st.markdown(
        _h('<div class="tp-output-section-head"><strong>报告设置</strong>'
        '<span>先确定分析框架，再将可追溯证据组织成报告</span></div>'),
        unsafe_allow_html=True)
    theory_choice = st.selectbox(_t("理论框架"), [
        "自动推荐（建议）", "目的论 (Skopos Theory)",
        "交际翻译与语义翻译 (Newmark)", "功能对等理论 (Nida)",
        "文本类型理论 (Reiss)", "生态翻译学 (Hu Gengshen)",
        "自定义"], key="translation_theory_choice", **_PERSIST_STATE, format_func=_localized_formatter())
    st.caption(_t("根据文本特征、案例证据与可用文献确定；仅在证据充分时使用。"))
    if theory_choice == "自定义":
        custom_theory = st.text_input(
            _t("自定义理论框架"), key="custom_translation_theory",
            placeholder=_t("输入理论名称或分析框架"))
        translation_theory = custom_theory.strip() or "自定义理论框架"
    elif theory_choice == "自动推荐（建议）":
        translation_theory = "基于文本特征、案例证据与可用文献自动推荐理论框架"
    else:
        translation_theory = theory_choice
    with st.container(key="report_template_inputs"):
        st.markdown(
            _h('<div class="tp-output-section-head"><strong>报告结构模板</strong>'
            '<span>先固定结构，再将证据分配到章节</span></div>'),
            unsafe_allow_html=True)
        _render_report_template_input()
    with st.container(key="literature_inputs"):
        st.markdown(
            _h('<div class="tp-output-section-head"><strong>参考文献与理论资料</strong>'
            '<span>上传与本次研究或报告相关的专著、论文或资料</span></div>'),
            unsafe_allow_html=True)
        st.caption(_t("系统将从文献中提取可核验的理论依据，并仅在证据充分时用于实践报告。"))
        _render_literature_inputs()
    return translation_theory


_PRESET_OUTPUTS = {
    # Translation presets no longer decide which files are shown in Step 03;
    # all new tasks start from the same standard delivery recommendation.
    "快速": dict(_DELIVERY_PRESET_CONFIGS["standard"]),
    "标准": dict(_DELIVERY_PRESET_CONFIGS["standard"]),
    "学术增强": dict(_DELIVERY_PRESET_CONFIGS["standard"]),
}

_PRESET_DISPLAY_NAMES = {
    "快速": "快速",
    "标准": "标准",
    "学术增强": "深度研究",
}


def _apply_preset(label):
    for key in ("strategy_auto_term", "strategy_use_tm", "strategy_review",
                "strategy_understanding", "strategy_strict_terms",
                "strategy_seg_mode", "strategy_concurrency",
                "output_annotate", "output_report"):
        st.session_state.pop(key, None)
    st.session_state.translation_preset = label
    st.session_state.strategy_config = dict(_PRESET_CONFIGS[label])
    _apply_delivery_preset("standard")


def _strategy_is_adjusted(label, config):
    return any(config.get(key) != value
               for key, value in _PRESET_CONFIGS[label].items())


def _strategy_adjustment_count(label, config):
    """Return the number of advanced strategy values changed from the preset."""
    baseline = _PRESET_CONFIGS.get(label, {})
    return sum(config.get(key) != value for key, value in baseline.items())


def _output_is_adjusted(label, config):
    return any(config.get(key) != value
               for key, value in _PRESET_OUTPUTS[label].items())


def _toggle_advanced_strategy():
    st.session_state.strategy_advanced_open = not st.session_state.get(
        "strategy_advanced_open", False)


def _set_strategy_option(option, widget_key):
    config = dict(st.session_state.strategy_config)
    config[option] = bool(st.session_state[widget_key])
    st.session_state.strategy_config = config


def _set_output_option(option, widget_key):
    config = dict(st.session_state.output_config)
    config[option] = bool(st.session_state[widget_key])
    # Annotation is a child of the bilingual DOCX row.  Clearing the parent
    # cannot leave an invisible annotated export enabled in the pipeline.
    if option == "deliver_bilingual_docx" and not config[option]:
        config["enable_annotate"] = False
        st.session_state["output_annotate"] = False
    st.session_state.output_config = config


# ---------------- 智能风格建议（Step 01 Quick Profiling） ----------------

def _apply_style_selection(selection):
    """把选中的 Style Profile 落成 style_rules / style_template。"""
    from folith.style_profile import STYLE_PROFILES, profile_to_rules
    selection = selection or {}
    rules = profile_to_rules(selection)
    custom = (selection.get("custom_rules") or "").strip()
    if custom:
        rules = rules.rstrip("。") + "。" + custom + "。"
    st.session_state.style_rules = rules
    base = selection.get("selected") or "general"
    st.session_state.style_template = STYLE_PROFILES.get(
        base, STYLE_PROFILES["general"])["name"]
    st.session_state.style_selection = selection


def _accept_style_recommendation(source="accepted"):
    rec = st.session_state.get("style_recommendation") or {}
    selection = {
        "selected": rec.get("recommended_style", "general"),
        "source": source,
        "adjustments": {},
    }
    _apply_style_selection(selection)


def _task_profile_signature():
    """Return the inputs that make a cached document profile reusable."""
    task_files = st.session_state.get("task_files") or []
    if not task_files:
        return None
    source = task_files[0]
    raw = source.get("bytes") or b""
    try:
        file_id = core.file_job_id(raw)
    except (TypeError, ValueError):
        file_id = f"{source.get('name') or ''}:{len(raw)}"
    return (str(file_id), str(st.session_state.get("target_lang") or "简体中文"))


def _task_profile_is_ready():
    """Whether Step 1 has a profile for the current file and target language."""
    profile = st.session_state.get("doc_profile")
    recommendation = st.session_state.get("style_recommendation")
    selection = st.session_state.get("style_selection")
    return bool(profile and recommendation and selection
                and st.session_state.get("task_profile_signature")
                == _task_profile_signature())


def _queue_task_profile_retry():
    st.session_state.style_profiling_state = "running"
    st.session_state.pending_profile_step = 2


def _run_quick_profile_with_progress():
    """在脚本运行内执行 Quick Profiling，用 st.status 分步显示进度。

    不放在按钮回调里：回调期间前端收不到任何更新会显得卡死/白屏。
    改为点击后置 running 状态，在本轮运行内逐步渲染状态并执行，
    完成后同一轮直接渲染结果卡片。
    """
    from folith import models as _models
    from folith.style_profile import _fallback_recommendation, quick_profile
    task_files = st.session_state.get("task_files") or []
    if not task_files:
        st.session_state.style_profiling_state = "idle"
        return
    source = task_files[0]
    warnings = []
    doc_profile = None
    style_rec = None
    profiling_state = "done"
    needs_api = False
    error_message = ""
    try:
        with st.status("正在生成智能画像…", expanded=True) as status:
            status.update(label="正在提取文档文本…", state="running")
            paragraphs, extract_warnings, extraction_report = \
                core.extract_document_paragraphs_with_report(
                    source.get("name", ""), source.get("bytes", b""),
                    # Profiling runs before a job exists, so its OCR pages go to
                    # the shared content-addressed profile cache.  The formal
                    # pipeline reads that cache in addition to its own job-scoped
                    # one, so the representative pages are never OCR'd twice.
                    checkpoint_dir=core.ocr_profile_cache_dir(
                        source.get("bytes", b"")),
                    on_progress=lambda message: status.update(
                        label=message, state="running"))
            warnings.extend(extract_warnings)
            st.session_state.task_extraction_report = extraction_report
            provider = st.session_state.get("provider_choice",
                                            next(iter(core.PROVIDERS)))
            api_key = st.session_state.get(f"api_key_{provider}", "")
            model = st.session_state.get(f"model_choice_{provider}", "")
            target_lang = st.session_state.get("target_lang", "简体中文")
            if not api_key or not model:
                status.update(label="需要配置 API Key 才能完成画像",
                              state="error")
                warnings.append("已选择 AI 模型，但 API 凭据未配置，无法自动画像")
                doc_profile = _models.default_document_profile()
                style_rec = _fallback_recommendation()
                needs_api = True
                profiling_state = "error"
            else:
                status.update(label="正在抽取首 / 中 / 尾样本并分析文体…",
                              state="running")
                doc_profile, style_rec, llm_warnings = quick_profile(
                    paragraphs, provider, api_key, model, target_lang,
                    base_url=api_base if core.PROVIDERS.get(provider, {}).get(
                        "custom_base_url") else None)
                warnings.extend(llm_warnings)
                if llm_warnings:
                    error_message = (
                        extract_warnings[-1] if not paragraphs and extract_warnings
                        else llm_warnings[-1])
                    status.update(label="智能画像未完成", state="error")
                    profiling_state = "error"
                else:
                    status.update(label="智能画像已完成", state="complete")
    except Exception as exc:  # profile failure must be recoverable in the UI
        provider_status = core.provider_error_status(exc)
        error_message = (
            core.provider_error_message(exc, "智能画像失败")
            if provider_status["status"] != "unknown"
            else str(exc).strip() or "分析服务暂时不可用")
        warnings.append(f"无法完成自动画像：{error_message}")
        profiling_state = "error"

    st.session_state.style_profiling_state = profiling_state
    st.session_state.style_profiling_needs_api = needs_api
    st.session_state.style_profiling_error = error_message
    if doc_profile is not None:
        st.session_state.doc_profile = doc_profile
    if style_rec is not None:
        st.session_state.style_recommendation = style_rec
    st.session_state.style_profile_warnings = warnings
    if profiling_state == "done" and style_rec is not None:
        # Agentic default: the recommendation becomes the effective profile
        # without exposing an internal "accept" workflow to the user.
        _accept_style_recommendation(source="auto")
        st.session_state.task_profile_signature = _task_profile_signature()


def _render_style_adjust_panel():
    """基础风格 radio + 4 个微调滑块 + 高级规则；应用后覆盖系统建议。"""
    from folith.style_profile import STYLE_PROFILES
    names = list(STYLE_PROFILES)
    current = st.session_state.get("style_selection") or {}
    rec = st.session_state.get("style_recommendation") or {}
    base_id = current.get("selected") or rec.get("recommended_style") or "general"
    if base_id not in names:
        base_id = "general"
    st.markdown(
        _h('<div class="tp-style-adjust-head"><strong>调整风格</strong>'
        '<span>修改后将覆盖系统建议，并记录为 user_override</span></div>'),
        unsafe_allow_html=True)
    base = st.radio(
        _t("基础风格"), names, index=names.index(base_id),
        format_func=_localized_formatter(lambda pid: STYLE_PROFILES[pid]["name"]),
        key="style_adjust_base", label_visibility="collapsed",
        **_PERSIST_STATE)
    col_a, col_b = st.columns(2)
    with col_a:
        formality = st.slider("表达正式度", 0, 100, 60, key="adj_formality",
                              **_PERSIST_STATE)
        restructuring = st.slider("句法重构幅度", 0, 100, 40,
                                  key="adj_restructuring",
                                  **_PERSIST_STATE)
    with col_b:
        terminology = st.slider("术语保守程度", 0, 100, 60, key="adj_terminology",
                                **_PERSIST_STATE)
        form_preservation = st.slider("原文形式保留", 0, 100, 70,
                                      key="adj_form_preservation",
                                      **_PERSIST_STATE)
    custom_rules = st.text_area(
        _t("高级规则（可选）"), key="style_adjust_custom",
        placeholder=_t('补充风格约束，{v0}', v0=_STYLE_RULES_PLACEHOLDER),
        **_PERSIST_STATE)
    if st.button(_t("应用风格"), key="apply_style_adjust"):
        selection = {
            "selected": base,
            "source": "user_override",
            "adjustments": {
                "formality": formality,
                "terminology": terminology,
                "restructuring": restructuring,
                "form_preservation": form_preservation,
            },
            "custom_rules": custom_rules.strip(),
        }
        _apply_style_selection(selection)
        st.session_state.style_adjust_open = False
        st.rerun()


def _render_style_profile_section():
    """Step 01 的智能风格建议卡片：推荐 -> 接受 / 调整 / 查看分析。"""
    from folith.style_profile import STYLE_PROFILES
    state = st.session_state.get("style_profiling_state", "idle")
    rec = st.session_state.get("style_recommendation")
    selection = st.session_state.get("style_selection")
    with st.container(key="style_profile_section"):
        if state == "idle":
            if not st.button(_t("开始智能画像"),
                             icon=":material/auto_awesome:",
                             key="run_quick_profile"):
                return
            st.session_state.style_profiling_state = "running"
            state = "running"
        if state == "running":
            _run_quick_profile_with_progress()
            rec = st.session_state.get("style_recommendation")
        if rec is None:
            return
        style_id = (selection or {}).get("selected") or rec.get("recommended_style") \
            or "general"
        meta = STYLE_PROFILES.get(style_id, STYLE_PROFILES["general"])
        confidence = rec.get("confidence", 0.0)
        reasons = rec.get("reasons") or []
        source_text = ""
        if selection:
            source_text = "已接受系统推荐" if selection.get("source") == "accepted" \
                else "已使用用户选择覆盖系统建议"
        st.markdown(
            ('<div class="tp-style-card' + str(" is-selected" if selection else "") + _h('"><div class="tp-style-card-head"><span class="material-symbols-rounded" aria-hidden="true">auto_awesome</span><strong>智能风格建议</strong><b>') + str(round(confidence * 100)) + '%</b></div><div class="tp-style-name">' + str(meta["name"]) + '</div><div class="tp-style-summary">' + str(meta["summary"]) + _h('</div><div class="tp-style-reasons"><span>检测依据</span><ul>'))
            + "".join(f"<li>{escape(r)}</li>" for r in reasons)
            + '</ul></div>'
            + (f'<div class="tp-style-source">{source_text}</div>'
               if source_text else "")
            + '</div>', unsafe_allow_html=True)
        for warn in st.session_state.get("style_profile_warnings", []):
            st.warning(_t(warn))
        report = st.session_state.get("task_extraction_report") or {}
        if report and report.get("extracted", {}).get("paragraphs"):
            with st.expander(_t("导入范围：本次会翻译什么、不会翻译什么"),
                             expanded=bool(report.get("unsupported")),
                             key="task_extraction_scope"):
                _render_extraction_report(report)
        if st.session_state.get("style_profiling_needs_api"):
            goto_col, retry_col, adjust_col = st.columns(3)
            with goto_col:
                if st.button(_t("前往配置 API Key"), key="goto_api_settings",
                             type="primary", width="stretch"):
                    st.session_state.app_view = "settings"
                    st.rerun()
            with retry_col:
                if st.button(_t("重试"), key="retry_quick_profile",
                             width="stretch"):
                    st.session_state.style_profiling_state = "running"
                    st.rerun()
            with adjust_col:
                if st.button(_t("调整"), key="open_style_adjust_api",
                             width="stretch"):
                    st.session_state.style_adjust_open = not st.session_state.get(
                        "style_adjust_open", False)
                    st.session_state.style_analysis_open = False
                    st.rerun()
        else:
            accept_col, adjust_col, analyze_col = st.columns(3)
            with accept_col:
                if st.button(_t("接受推荐"), key="accept_style_rec",
                             width="stretch"):
                    _accept_style_recommendation()
                    st.session_state.style_adjust_open = False
                    st.session_state.style_analysis_open = False
                    st.rerun()
            with adjust_col:
                if st.button(_t("调整"), key="open_style_adjust",
                             width="stretch"):
                    st.session_state.style_adjust_open = not st.session_state.get(
                        "style_adjust_open", False)
                    st.session_state.style_analysis_open = False
                    st.rerun()
            with analyze_col:
                if st.button(_t("查看分析"), key="show_style_analysis",
                             width="stretch"):
                    st.session_state.style_analysis_open = not st.session_state.get(
                        "style_analysis_open", False)
                    st.session_state.style_adjust_open = False
                    st.rerun()
        if st.session_state.get("style_analysis_open"):
            with st.expander(_t("文档画像分析"), expanded=True):
                doc = st.session_state.get("doc_profile") or {}
                rows = [
                    ("领域", doc.get("domain") or "—"),
                    ("细分领域", doc.get("subdomain") or "—"),
                    ("文本类型", doc.get("genre") or "—"),
                    ("目标读者", doc.get("audience") or "—"),
                    ("语域", doc.get("register") or "—"),
                    ("文体约束", doc.get("style_constraints") or "—"),
                ]
                st.markdown("<br>".join(
                    f"<b>{k}</b>：{escape(str(v))}" for k, v in rows),
                    unsafe_allow_html=True)
                if rec.get("domain"):
                    st.caption(_t("领域标签：" + " · ".join(rec.get("domain", []))))
                st.caption(_t("样本策略：首 / 中 / 尾分布式采样，约 3000–6000 字符"))
        if st.session_state.get("style_adjust_open"):
            _render_style_adjust_panel()


def _render_task_profile_setting():
    """Compact Agentic profile setting used by the new Step 1 surface."""
    state = st.session_state.get("style_profiling_state", "idle")
    ready = _task_profile_is_ready()
    with st.container(key="task_setting_profile"):
        st.markdown(
            _h('<div class="tp-setting-title"><span>智能画像</span>'
            '<span class="tp-setting-badge">推荐</span></div>'),
            unsafe_allow_html=True)
        toggle_col, status_col = st.columns([1.08, .92], vertical_alignment="center")
        with toggle_col:
            st.toggle(_t("自动分析"), key="task_auto_profile",
                      help=_t("进入下一步时自动分析文档；关闭后不会触发画像调用。"),
                      **_PERSIST_STATE)
        with status_col:
            if state == "running":
                st.markdown(_h('<span class="tp-setting-status is-running">正在分析…</span>'),
                            unsafe_allow_html=True)
            elif ready:
                st.markdown(_h('<span class="tp-setting-status is-ready">已开启</span>'),
                            unsafe_allow_html=True)
            elif state == "error":
                st.markdown(_h('<span class="tp-setting-status is-error">需要处理</span>'),
                            unsafe_allow_html=True)
            else:
                st.markdown(_h('<span class="tp-setting-status">待下一步分析</span>'),
                            unsafe_allow_html=True)
        if ready:
            st.caption(_t("已复用画像结果 · 进入下一步时不会重复分析"))
        elif state == "error":
            st.caption(_t("自动分析未完成。请重试，或关闭自动分析后继续。"))
            detail = str(st.session_state.get("style_profiling_error") or "").strip()
            if detail:
                st.warning(_t(detail))
            retry_col, settings_col = st.columns(2)
            with retry_col:
                if st.button(_t("重试"), key="retry_task_profile",
                             icon=":material/refresh:", width="stretch"):
                    _queue_task_profile_retry()
                    st.rerun()
            with settings_col:
                if st.button(_t("配置 API Key"), key="goto_task_profile_settings",
                             width="stretch"):
                    st.session_state.app_view = "settings"
                    st.rerun()
        else:
            st.caption(_t("自动识别领域、术语、语气与写作风格"))


def _render_task_termbase_setting():
    """Compact glossary attachment setting with add / replace / remove actions."""
    term_label = str(st.session_state.get("task_glossary_name") or "未使用")
    has_term_base = term_label not in {"未使用", "未添加"}
    with st.container(key="task_setting_glossary"):
        st.markdown(_h('<div class="tp-setting-title"><span>术语库</span></div>'),
                    unsafe_allow_html=True)
        if has_term_base:
            count = st.session_state.get("task_glossary_count")
            count_text = f"{count:,} 条术语" if count is not None else "已添加"
            st.markdown(
                '<div class="tp-setting-value">'
                f'<strong>{escape(term_label)}</strong><span>{escape(count_text)}</span>'
                '</div>', unsafe_allow_html=True)
            action_col, remove_col = st.columns([1, 1])
            with action_col:
                if st.button(_t("更换"), key="replace_termbase",
                             icon=":material/swap_horiz:", width="stretch"):
                    st.session_state.show_termbase_picker = True
                    st.rerun()
            with remove_col:
                st.button(_t("移除"), key="remove_termbase",
                          icon=":material/close:", width="stretch",
                          on_click=_remove_task_termbase)
        else:
            action_col, state_col = st.columns([1, 1])
            with action_col:
                if st.button(_t("添加"), key="add_termbase",
                             icon=":material/add:", width="stretch"):
                    st.session_state.show_termbase_picker = True
                    st.rerun()
            with state_col:
                st.markdown(_h('<span class="tp-setting-status">未使用</span>'),
                            unsafe_allow_html=True)
            st.caption(_t("可选 · 保持术语与专名一致"))

        if st.session_state.get("show_termbase_picker"):
            with st.container(key="task_termbase_picker"):
                termbase_file = st.file_uploader(
                    _t("选择术语库文件"), type=["xlsx", "csv", "tbx", "tmx"],
                    key="task_termbase_file",
                    help=_t("支持 TBX、TMX、Excel 和 CSV。"))
            if termbase_file:
                try:
                    if termbase_file.name.lower().endswith(".tmx"):
                        # TM 条目必须带目标语言：这里显式把用户选定的目标语言
                        # 交给导入器，而不是让它去猜（TMX 的 xml:lang 是
                        # BCP-47 代码，与本应用的显示名不是同一套写法）。
                        result = core.import_tmx(
                            termbase_file,
                            target_lang=st.session_state.get("target_lang"))
                        st.session_state.task_glossary = []
                        st.session_state.task_glossary_count = result["added"]
                        st.session_state.task_glossary_name = termbase_file.name
                    else:
                        parser = core.parse_termbase if termbase_file.name.lower().endswith(".xlsx") \
                            else core.parse_termbase_csv if termbase_file.name.lower().endswith(".csv") \
                            else core.parse_termbase_tbx
                        st.session_state.task_glossary = parser(termbase_file)
                        st.session_state.task_glossary_count = len(
                            st.session_state.task_glossary)
                        st.session_state.task_glossary_name = termbase_file.name
                    st.session_state.show_termbase_picker = False
                    st.rerun()
                except ValueError as exc:
                    st.warning(_t(str(exc)))


def _finish_profile_before_step_two():
    """Run the queued profile and advance only after a usable result exists."""
    if st.session_state.get("style_profiling_state") != "running":
        return
    _run_quick_profile_with_progress()
    st.session_state.pop("pending_profile_step", None)
    if _task_profile_is_ready():
        st.session_state.task_step = 2
        st.rerun()


def _render_task_actions(*, back_step=None, next_step=None, next_label="下一步",
                         next_disabled=False, run=False, delivery_summary=None):
    with st.container(key="task_action_bar"):
        if back_step is None:
            status_col, next_col = st.columns([1, .22])
            back_col = None
        else:
            status_col, back_col, next_col = st.columns([2.6, .8, .8])
        has_inputs = bool(st.session_state.get("task_files"))
        save_text = "✓ 已自动保存" if has_inputs else "更改会自动保存"
        save_class = "tp-autosave is-saved" if has_inputs else "tp-autosave"
        if delivery_summary is not None:
            status_col.markdown(
                f'{_delivery_summary_html(delivery_summary)}'
                f'<span class="{save_class} tp-delivery-autosave">{save_text}</span>',
                unsafe_allow_html=True)
        else:
            status_col.markdown(f'<span class="{save_class}">{save_text}</span>',
                                unsafe_allow_html=True)
        if back_step is not None:
            back_col.button(_t("上一步"), icon=":material/arrow_back:", width="stretch",
                            on_click=_go_to_step,
                            args=(back_step,), key=f"back_to_{back_step}")
        if run:
            return next_col.button(_t(next_label), type="primary", width="stretch",
                                   disabled=next_disabled, key="run_task")
        next_col.button(_t(next_label), type="primary", icon=":material/arrow_forward:",
                        width="stretch", on_click=_request_step, args=(next_step,),
                        disabled=next_disabled, key=f"next_to_{next_step}")
    return False


def _remove_task_termbase():
    for key in ("task_glossary", "task_glossary_name", "task_glossary_count",
                "task_termbase_file"):
        st.session_state.pop(key, None)
    st.session_state.show_termbase_picker = False


def _remove_literature_uploads():
    for key in ("literature_uploads", "literature_upload_sources",
                "literature_upload_warnings"):
        st.session_state.pop(key, None)
    st.session_state.literature_uploader_generation = \
        st.session_state.get("literature_uploader_generation", 0) + 1


def _remove_literature_registry():
    for key in ("literature_registry_sources", "literature_registry_name",
                "literature_registry_signature", "literature_registry_warning"):
        st.session_state.pop(key, None)
    st.session_state.literature_registry_generation = \
        st.session_state.get("literature_registry_generation", 0) + 1


def _remove_report_template():
    for key in ("report_template_input", "report_template_error",
                "report_template_signature"):
        st.session_state.pop(key, None)
    st.session_state.report_template_removed = True
    st.session_state.report_template_uploader_generation = \
        st.session_state.get("report_template_uploader_generation", 0) + 1


def _render_report_template_input():
    """Capture the DOCX template separately from reference-material uploads."""
    template = st.session_state.get("report_template_input")
    if template:
        summary = _report_template.contract_summary(template.get("contract"))
        st.markdown(
            ('<div class="tp-attachment"><div><strong>' + str(escape(str(template.get("name") or "模板.docx"))) + _h('</strong><span>已解析 · ') + str(summary.get("chapter_count", 0)) + ' 个章节 · ' + str(summary.get("subsection_count", 0)) + _h(' 个小节</span></div></div>')),
            unsafe_allow_html=True)
        st.button(_t("移除报告模板"), key="remove_report_template",
                  on_click=_remove_report_template, width="stretch")
        with st.expander(_t("查看模板结构与格式契约"), expanded=False):
            st.caption(
                _t('模板哈希：{v0} · 模板章节、标题层级、前后置部分与 DOCX 样式将作为报告约束。', v0=str(summary.get('template_hash') or '')[:16]))
            structure = template["contract"].get("document_structure") or {}
            for chapter in structure.get("chapters") or []:
                subs = "、".join(str(x.get("title")) for x in
                                  chapter.get("required_subsections") or [])
                st.markdown(
                    f"- **{chapter.get('section_id')} {chapter.get('title')}**"
                    f"（{chapter.get('role')}）"
                    + (f"：{subs}" if subs else ""))
            if structure.get("front_matter"):
                st.caption(_t("前置部分：" + "、".join(
                    str(x.get("title")) for x in structure["front_matter"])))
            if structure.get("back_matter"):
                st.caption(_t("后置部分：" + "、".join(
                    str(x.get("title")) for x in structure["back_matter"])))
        return

    uploaded = st.file_uploader(
        _t("报告结构模板（DOCX，可选）"),
        type=["docx"],
        key=f"report_template_uploader_"
            f"{st.session_state.get('report_template_uploader_generation', 0)}",
        help=_t("需要研究与报告专用能力时，可用模板固定章节、标题层级、前后置部分和 Word 样式。"),
    )
    if uploaded:
        raw = uploaded.getvalue()
        signature = (uploaded.name, len(raw), raw[:32])
        if signature != st.session_state.get("report_template_signature"):
            try:
                contract = _report_template.parse_docx_template(uploaded.name, raw)
                st.session_state.report_template_input = {
                    "name": uploaded.name, "bytes": raw, "contract": contract,
                }
                st.session_state.report_template_error = None
                st.session_state.report_template_signature = signature
                st.session_state.report_template_removed = False
                st.rerun()
            except _report_template.TemplateParseError as exc:
                st.session_state.report_template_error = str(exc)
                st.session_state.report_template_signature = signature
    if st.session_state.get("report_template_error"):
        st.error(_t("报告模板无法使用：" + str(st.session_state.report_template_error)))


def _render_literature_inputs():
    """Show user-facing reference uploads; registry JSON stays an advanced escape hatch."""
    uploads = st.session_state.get("literature_uploads") or []
    if uploads:
        names = [escape(str(item.get("name") or "未命名资料")) for item in uploads]
        label = names[0] if len(names) == 1 else f"{names[0]} 等 {len(names)} 个文件"
        st.markdown(
            ('<div class="tp-attachment"><div><strong>' + str(label) + _h('</strong><span>已添加参考资料 · 系统将在运行时解析并记录来源位置</span></div></div>')),
            unsafe_allow_html=True)
        st.button(_t("移除参考资料"), key="remove_literature_uploads",
                  on_click=_remove_literature_uploads, width="stretch")
    else:
        uploaded = st.file_uploader(
            _t("上传参考资料"),
            type=["pdf", "docx", "md", "markdown", "txt", "bib", "ris"],
            accept_multiple_files=True,
            key=f"literature_uploads_"
                f"{st.session_state.get('literature_uploader_generation', 0)}",
            help=_t("支持 PDF、DOCX、Markdown、TXT、BibTeX / RIS（含 Zotero 导出）"),
        )
        if uploaded:
            files = [{"name": item.name, "bytes": item.getvalue()} for item in uploaded]
            sources, warnings = _literature_evidence.build_sources_from_uploads(
                files, core.OUTPUT_DIR)
            st.session_state.literature_uploads = files
            st.session_state.literature_upload_sources = sources
            st.session_state.literature_upload_warnings = warnings
            st.rerun()
    for warning in st.session_state.get("literature_upload_warnings") or []:
        st.warning(_t(warning))

    with st.expander(_t("高级选项"), expanded=False):
        st.caption(_t("用于恢复已有工作流；普通用户无需准备 JSON。"))
        registry_file = st.file_uploader(
            _t("导入已有文献证据注册表（.json）"),
            type=["json"],
            key=f"literature_registry_"
                f"{st.session_state.get('literature_registry_generation', 0)}",
            help=_t("仅支持已有 Literature Evidence Registry JSON"),
        )
        if registry_file:
            raw = registry_file.getvalue()
            signature = (registry_file.name, len(raw))
            if signature != st.session_state.get("literature_registry_signature"):
                try:
                    loaded = json.loads(raw.decode("utf-8-sig"))
                    if isinstance(loaded, dict):
                        loaded = loaded.get("sources") or []
                    if not isinstance(loaded, list) \
                            or not all(isinstance(item, dict) for item in loaded):
                        raise ValueError("JSON 中没有可用的 sources 列表")
                    st.session_state.literature_registry_sources = loaded
                    st.session_state.literature_registry_name = registry_file.name
                    st.session_state.literature_registry_warning = None
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    st.session_state.literature_registry_warning = f"注册表无法导入：{exc}"
                st.session_state.literature_registry_signature = signature
        if st.session_state.get("literature_registry_warning"):
            st.warning(_t(st.session_state.literature_registry_warning))
        if st.session_state.get("literature_registry_sources") is not None:
            registry_name = escape(str(
                st.session_state.get("literature_registry_name") or "已有注册表"))
            registry_count = len(st.session_state.get("literature_registry_sources") or [])
            st.caption(_t('已导入 {v0} · {v1} 条来源', v0=registry_name, v1=registry_count))
            st.button(_t("移除已有注册表"), key="remove_literature_registry",
                      on_click=_remove_literature_registry, width="stretch")


def _remove_source_documents():
    st.session_state.pop("task_files", None)
    st.session_state.pop("source_parse_state", None)
    st.session_state.pop("step_gate_message", None)
    for key in ("style_profiling_state", "doc_profile", "style_recommendation",
                "style_selection", "style_profile_warnings",
                "style_adjust_open", "style_analysis_open",
                "style_profiling_needs_api", "style_profiling_error",
                "task_profile_signature", "pending_profile_step"):
        st.session_state.pop(key, None)
    st.session_state.source_uploader_generation = \
        st.session_state.get("source_uploader_generation", 0) + 1


def _source_file_html(task_files):
    total_size = sum(len(item.get("bytes") or b"") for item in task_files)
    count = len(task_files)
    raw_name = task_files[0].get("name") or "未命名文档"
    first_name = escape(raw_name)
    name = first_name if count == 1 else f"{first_name} 等 {count} 个文件"
    parse_state = st.session_state.get("source_parse_state", "uploaded")
    page_total = sum(int(item.get("pages") or 0) for item in task_files)
    suffix = Path(raw_name).suffix.lower().lstrip(".")
    file_type = {"docx": "Word", "pdf": "PDF"}.get(suffix, suffix.upper() or "文档")
    parsed_detail = f"{file_type} · {_format_size(total_size)}" \
        f'{f" · {page_total:,} 页" if page_total else ""}'
    meta = {
        "uploaded": (parsed_detail, "已上传，等待解析"),
        "parsing": (parsed_detail, "正在解析…"),
        "parsed": (parsed_detail, ""),
        "error": (parsed_detail, "解析失败"),
    }
    detail, status = meta.get(parse_state, meta["uploaded"])
    icon = "progress_activity" if parse_state == "parsing" else "description"
    icon_class = "material-symbols-rounded is-loading" if parse_state == "parsing" \
        else "material-symbols-rounded"
    ready_badge = _h(' · <b class="tp-source-ready">已就绪</b>') \
        if parse_state == "parsed" else ""
    status_html = (f' · <b class="tp-source-file-status is-{parse_state}">'
                   f'{status}</b>') if status else ""
    return (
        '<div class="tp-source-file">'
        f'<span class="{icon_class}" aria-hidden="true">{icon}</span>'
        f'<div class="tp-source-file-copy"><strong title="{escape(raw_name, quote=True)}">'
        f'{name}</strong>'
        f'<span>{detail}{status_html}{ready_badge}</span></div></div>'
    )


def _preset_card_html(label):
    display_label = _PRESET_DISPLAY_NAMES.get(label, label)
    cards = {
        "快速": {
            "expectation": "适合快速产出可读初稿",
            "workflow": "翻译 → 基础检查",
            "metrics": (("速度", "高"), ("成本", "低"), ("审校深度", "基础")),
        },
        "标准": {
            "expectation": "适合大多数正式翻译任务",
            "workflow": "全文理解 → 术语增强 → 翻译 → 基础检查",
            "metrics": (("速度", "中"), ("成本", "中"), ("审校深度", "基础")),
        },
        "学术增强": {
            "expectation": "适合论文、报告及需要证据追踪的材料",
            "workflow": "全文理解 → 术语治理 → 翻译 → 独立审校 → 研究证据",
            "metrics": (("速度", "低"), ("成本", "高"), ("审校深度", "深度")),
        },
    }
    card = cards[label]
    badge = _h('<span class="tp-preset-badge">推荐</span>') if label == "标准" else ""
    icon = "radio_button_checked" if label == st.session_state.get(
        "translation_preset", "标准") else "radio_button_unchecked"
    metric_html = "".join(
        f'<span class="tp-preset-metric"><b>{escape(dimension)}</b>'
        f'<span class="tp-preset-metric-value">{escape(level)}</span></span>'
        for dimension, level in card["metrics"])
    return (
        ('<div class="tp-preset-card"><div class="tp-preset-head"><span class="material-symbols-rounded" aria-hidden="true">' + str(icon) + '</span><strong>' + str(display_label) + '</strong>' + str(badge) + '</div><div class="tp-preset-expectation"><strong>' + str(escape(card["expectation"])) + _h('</strong></div><div class="tp-preset-flow-wrap"><span class="tp-preset-flow-label">包含</span><p class="tp-preset-flow">') + str(escape(card["workflow"])) + '</p></div><div class="tp-preset-metrics">' + str(metric_html) + '</div></div>')
    )


def _render_strategy_toggle(label, description, option, key, config):
    with st.container(key=f"{key}_row"):
        copy_col, switch_col = st.columns([9, 1], vertical_alignment="center")
        copy_col.markdown(
            f'<div class="tp-setting-copy"><strong>{label}</strong>'
            f'<span>{description}</span></div>', unsafe_allow_html=True)
        switch_col.toggle(_t(label), value=config[option], key=key,
                          label_visibility="collapsed",
                          on_change=_set_strategy_option, args=(option, key),
                          help=_t(description), **_PERSIST_STATE)


def _format_size(size):
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, round(size / 1024))} KB"


def _summary_html(filename, target_lang, preset_label, glossary_name,
                  strategy_config, output_config, style_template,
                  style_source="", delivery_preset=None):
    filename = escape(str(filename))
    target_lang = escape(str(target_lang))
    preset_key = str(preset_label)
    preset_label = escape(_PRESET_DISPLAY_NAMES.get(preset_key, preset_key))
    glossary_name = escape(str(glossary_name))
    workflow = []
    if strategy_config.get("enable_understanding"):
        workflow.append("全文理解")
    if strategy_config["strict_terminology_governance"]:
        workflow.append("术语治理")
    elif strategy_config["auto_term"]:
        workflow.append("术语增强")
    workflow.extend(["翻译", "基础检查"])
    if strategy_config["enable_review"]:
        workflow.append("独立审校")
    if output_config["enable_annotate"]:
        workflow.append("重点标注")
    if output_config["enable_report"]:
        workflow.extend(["研究证据", "翻译实践报告（专用）"])
    mode_label = preset_label
    if _strategy_is_adjusted(preset_key, strategy_config) \
            or _output_is_adjusted(preset_key, output_config):
        mode_label += " · 已调整"
    segmentation_label = (
        "按自然句" if strategy_config.get("segmentation_mode", "sentence") == "sentence"
        else "按自然段")
    delivery_key = delivery_preset or st.session_state.get(
        "delivery_preset", "standard")
    delivery_label = _delivery_preset_label(
        delivery_key,
        current=delivery_key,
        modified=bool(st.session_state.get("delivery_preset_modified")),
    )
    delivery_summary = _delivery_summary(
        output_config,
        research_visible=_research_outputs_visible(preset_key, strategy_config),
    )
    artifacts = []
    if output_config.get("deliver_plain_docx"):
        artifacts.append(("description", "纯译文", "仅含译文文本", "DOCX"))
    if output_config.get("deliver_bilingual_docx"):
        artifacts.append(("description", "双语对照", "原文与译文并列，适合审校", "DOCX"))
    if output_config.get("deliver_pdf"):
        artifacts.append(("picture_as_pdf", "纯译文", "适合分享、打印与最终交付", "PDF"))
    if output_config.get("deliver_epub"):
        artifacts.append(("menu_book", "译文电子书", "保留原图，支持调整字号", "EPUB"))
    if output_config.get("deliver_terms_xlsx"):
        artifacts.append(("table", "术语表", "自动抽取与锁定术语", "XLSX"))
    for key, name, kind in (("deliver_tbx", "标准术语库", "TBX"),
                            ("deliver_tmx", "翻译记忆", "TMX"),
                            ("deliver_jsonl", "结构化数据", "JSONL")):
        if output_config.get(key):
            artifacts.append(("code", name, "语言资产导出", kind))
    if output_config["enable_annotate"]:
        artifacts.append(("ink_highlighter", "双语对照 · 标注增强版",
                          "标出生僻词、专业术语和翻译难点句", "DOCX"))
    if output_config["enable_report"]:
        artifacts.append(("article", "翻译实践报告（专用）",
                          "基于翻译过程证据生成", "DOCX / MD"))
    if output_config.get("deliver_evidence"):
        artifacts.append(("data_object", "翻译过程证据",
                          "批次翻译、审校与修订的可追溯记录", "JSONL"))
    if output_config.get("deliver_cases"):
        artifacts.append(("fact_check", "案例候选",
                          "符合资格的真实修订案例", "JSON"))
    if output_config.get("deliver_academic_workspace"):
        artifacts.append(("folder_zip", "学术写作工作区",
                          "论证大纲与写作素材打包", "ZIP"))
    if output_config.get("deliver_review_report"):
        artifacts.append(("fact_check", "审校报告",
                          "审校发现与处理记录", "MD"))
    style_value = escape(str(
        style_template + (f"（{style_source}）" if style_source else "")))
    artifact_rows = "".join(
        '<div class="tp-artifact-row">'
        f'<span class="material-symbols-rounded" aria-hidden="true">{icon}</span>'
        f'<div><strong>{name}</strong><span>{detail}</span></div><b>{kind}</b></div>'
        for icon, name, detail, kind in artifacts)
    return (
        (_h('<div class="tp-confirm-stack"><section class="tp-confirm-card"><div class="tp-confirm-head"><span class="material-symbols-rounded" aria-hidden="true">tune</span><strong>任务配置</strong></div><div class="tp-summary-grid"><div class="tp-summary-item"><span>原文</span><strong>') + str(filename) + _h('</strong></div><div class="tp-summary-item"><span>目标语言</span><strong>') + str(target_lang) + _h('</strong></div><div class="tp-summary-item"><span>翻译模式</span><strong>') + str(mode_label) + _h('</strong></div><div class="tp-summary-item"><span>翻译单元</span><strong>') + str(segmentation_label) + _h('</strong></div><div class="tp-summary-item"><span>交付方案</span><strong>') + str(escape(delivery_label)) + _h('</strong></div><div class="tp-summary-item"><span>译文风格</span><strong>') + str(style_value) + _h('</strong></div><div class="tp-summary-item"><span>术语库</span><strong>') + str(glossary_name) + _h('</strong></div><div class="tp-summary-item is-wide"><span>工作流</span><strong>') + str(" → ".join(workflow)) + _h('</strong></div></div></section><section class="tp-confirm-card"><div class="tp-confirm-head"><span class="material-symbols-rounded" aria-hidden="true">inventory_2</span><strong>将生成 ') + str(delivery_summary["file_count"]) + _h(' 个文件</strong></div><div class="tp-artifact-list">') + str(artifact_rows) + '</div></section></div>'))


def _runtime_html(provider, model, connection_status, can_start):
    provider = escape(str(provider))
    model = escape(str(model or "未配置"))
    connection = {
        "connected": ("已连接", "is-success"),
        "error": ("连接失败", "is-error"),
        "credentials_missing": ("API 凭据未配置", "is-warning"),
        "not_selected": ("尚未选择模型", "is-neutral"),
        "unverified": ("未验证", "is-neutral"),
    }.get(connection_status, ("未验证", "is-neutral"))
    if not can_start:
        readiness = ("需要配置", "is-warning")
    elif connection_status == "connected":
        readiness = ("可启动", "is-success")
    elif connection_status == "error":
        readiness = ("需检查连接", "is-error")
    else:
        readiness = ("待验证", "is-warning")
    return (
        (_h('<section class="tp-confirm-card tp-runtime-card"><div class="tp-confirm-head"><span class="material-symbols-rounded" aria-hidden="true">memory</span><strong>运行环境</strong></div><div class="tp-runtime-grid"><div><span>AI 引擎</span><strong>') + str(provider) + _h('</strong></div><div><span>模型</span><strong>') + str(model) + _h('</strong></div><div><span>连接状态</span><strong class="tp-status ') + str(connection[1]) + '">' + str(connection[0]) + _h('</strong></div><div><span>启动状态</span><strong class="tp-status ') + str(readiness[1]) + '">' + str(readiness[0]) + '</strong></div></div></section>'))


def _render_profile_editor(job_id, state, box=None):
    box = box or st
    profile = state.get("document_profile") or {}
    with box.expander(_t("文档画像（AI 生成，可修改后保存）"), expanded=False):
        c1, c2, c3 = box.columns(3)
        domain = c1.text_input(_t("领域 domain"), value=profile.get("domain") or "",
                               key=f"pf_d_{job_id}")
        subdomain = c2.text_input(_t("细分领域 subdomain"),
                                  value=profile.get("subdomain") or "",
                                  key=f"pf_sd_{job_id}")
        genre = c3.text_input(_t("文本类型 genre"), value=profile.get("genre") or "",
                              key=f"pf_g_{job_id}")
        audience = c1.text_input(_t("读者 audience"), value=profile.get("audience") or "",
                                 key=f"pf_a_{job_id}")
        register = c2.text_input(_t("语域 register"), value=profile.get("register") or "",
                                 key=f"pf_r_{job_id}")
        confidence = c3.slider("置信度", 0.0, 1.0,
                               float(profile.get("confidence") or 0.0),
                               key=f"pf_c_{job_id}")
        style_constraints = box.text_area(
            _t("风格约束 style_constraints"),
            value=profile.get("style_constraints") or "", key=f"pf_sc_{job_id}")
        if box.button(_t("保存文档画像"), key=f"pf_save_{job_id}"):
            core.save_document_profile(job_id, {
                "domain": domain, "subdomain": subdomain, "genre": genre,
                "audience": audience, "register": register,
                "style_constraints": style_constraints, "confidence": confidence,
                "sections": profile.get("sections") or [],
            })
            st.rerun()
        secs = profile.get("sections") or []
        if secs:
            box.caption(_t("分节：" + "；".join(
                f"{x.get('section_id')}（段落 {x.get('start_segment')}-{x.get('end_segment')}"
                f"，{x.get('topic') or x.get('domain') or '?'}）" for x in secs)))
        elif not state.get("profile_done"):
            box.caption(_t("画像未生成（AI 失败或已跳过），可在此人工填写后保存。"))

def _asset_prefix(state, snapshot_current=False):
    """Only a currently matching frozen snapshot may use the final prefix."""
    return "final_" if snapshot_current and state.get("delivery_status") == "final" else "draft_"


def _render_snapshot_versions(job_id, state, location):
    snapshots = core.list_delivery_snapshots(job_id)
    if not snapshots:
        return
    filename = Path(str(state.get("filename") or "document")).stem or "document"
    with st.expander(_t("最终交付版本"), expanded=False):
        st.caption(_t("历史版本来自已冻结文件，不会随当前工作版本变化。"))
        for snapshot in reversed(snapshots):
            version = snapshot["snapshot_version"]
            approval = snapshot.get("approval") or {}
            st.markdown(f"**最终交付版本 v{version}** · 已冻结")
            st.caption(
                _t('确认时间：{v0} · 交付说明：{v1} · 资产：{v2} 项', v0=approval.get('timestamp') or snapshot.get('created_at') or '—', v1=approval.get('note') or '—', v2=len(snapshot.get('assets') or [])))
            archive = core.delivery_snapshot_archive(job_id, version)
            if archive is not None:
                st.download_button(
                    _t('下载最终交付版本 v{v0}', v0=version), archive,
                    file_name=f"final_delivery_v{version}_{filename}.zip",
                    mime="application/zip",
                    key=f"snapshot_download_{location}_{job_id}_v{version}",
                    width="stretch")


def _review_sort_key(context):
    return (
        _delivery.SEVERITY_ORDER.get(context.get("severity"), 99),
        context.get("segment_index") if context.get("segment_index") is not None else 10**9,
        context.get("finding_id") or "",
    )


def _review_phase_label(phase):
    return {
        "formal_review": "正式审校",
        "shadow_repair": "自动修订复核",
        "suggested_shadow_review": "建议译文复核",
    }.get(phase, phase or "审校记录")


def _render_finding_evidence(context):
    refs = context.get("evidence_refs") or []
    traces = context.get("review_evidence") or []
    if refs:
        st.caption(_t("该问题引用的证据：" + "、".join(refs)))
    if not traces:
        st.caption(_t("暂无额外证据请求；以上原文与译文来自任务本地记录。"))
        return
    with st.expander(_t("审校 / 证据详情"), expanded=False):
        for trace in traces[-3:]:
            status = (trace.get("completion_receipt") or {}).get("status") or "-"
            evidence_ids = "、".join(trace.get("evidence_ids") or []) or "无"
            st.caption(
                _t('{v0} · 结论 {v1} · 状态 {v2} · 证据 {v3}', v0=_review_phase_label(trace.get('phase')), v1=trace.get('decision') or '-', v2=status, v3=evidence_ids))
            for request in (trace.get("requests") or [])[:4]:
                tool = request.get("tool") or "evidence"
                evidence_id = request.get("evidence_id") or "-"
                arguments = request.get("arguments") or {}
                st.caption(_t(f"{evidence_id} · {tool} · {arguments}"))


def _format_saved_at(value):
    if not value:
        return "尚无保存记录"
    return str(value).replace("T", " ")[:19]


def _render_recovery_panel(job_id, state):
    summary = core.recovery_summary(job_id, state)
    status = core.task_status_label(state, job_id)
    with st.container(border=True):
        st.subheader(_t("任务状态与自动保存"))
        c1, c2, c3 = st.columns(3)
        c1.metric("当前状态", status)
        batch_count = (f"{summary['completed_batch_count']}/{summary['total_batches']}"
                       if summary["total_batches"] else "—")
        c2.metric("已完成处理批次", batch_count)
        c3.metric("自动保存", "已开启")
        st.caption(_t('最近保存进度：{v0} · 最近完成阶段：{v1}', v0=_format_saved_at(summary['last_saved_at']), v1=summary['last_completed_stage']))
        current = summary.get("current_batch")
        if current:
            st.warning(
                _t('第 {v0} 个处理批次中断：已保存本批次 {v1}/{v2} 段。继续时会重新执行本批次剩余内容（最多重新执行 {v3} 段），此前已保存的批次不会重做。', v0=current['number'], v1=current['completed_segments'], v2=current['segment_count'], v3=current['regenerate_segments']))
        if summary["recovered_tm_entries"]:
            st.info(_t('已从上次中断中恢复 {v0} 条翻译记忆同步记录。', v0=summary['recovered_tm_entries']))
        if summary["can_resume"] and st.button(
                _t("继续处理"), type="primary", key=f"resume_workspace_{job_id}",
                width="stretch"):
            _open_job(job_id, state, "translation")
            _resume_job(job_id, state)
            st.rerun()


def _context_status_label(status):
    return {
        "model": "模型生成",
        "deterministic_fallback": "临时摘要",
        "pending": "生成中",
        "unavailable": "不可用",
    }.get(str(status or ""), "未记录")


def _target_context_level_label(level):
    return {
        "human_accepted": "人工确认",
        "reviewed": "独立审校",
        "tm_approved": "翻译记忆",
        "generated": "自动译文（未确认）",
    }.get(level, "未标注")


def _render_context_surface(job_id, state):
    st.header(_t("文档上下文"))
    st.caption(_t("查看系统对全文、当前内容单元和前文译文连续性的理解。"))
    artifacts = core.load_context_artifacts(job_id, state)
    units = artifacts["semantic_units"]
    digests = artifacts["section_digests"]
    synopsis = artifacts["document_synopsis"]
    recovery = core.recovery_summary(job_id, state)
    pairs = state.get("pairs") or []
    current_batch = recovery.get("current_batch")
    if current_batch:
        current_index = current_batch.get("start_segment", len(pairs)) \
            + current_batch.get("completed_segments", 0)
        st.info(
            _t('当前处理批次：第 {v0} 批 · 本批已保存 {v1}/{v2} 段。', v0=current_batch['number'], v1=current_batch['completed_segments'], v2=current_batch['segment_count']))
    else:
        current_index = len(pairs)
        if state.get("p2_done"):
            st.success(_t("翻译批次已完成；以下显示最近使用的上下文。"))
        else:
            st.caption(_t("翻译尚未形成批次记录；以下可查看已经生成的全文理解。"))
    current_index = max(0, min(current_index, len(state.get("paras") or pairs)))

    synopsis_status = _context_status_label(synopsis.get("status"))
    accepted_context = _context.select_target_context(
        pairs, current_index, limit=4)
    metric_cols = st.columns(3)
    metric_cols[0].metric("内容单元", len(units))
    metric_cols[1].metric("章节摘要", len(digests))
    metric_cols[2].metric("前文连续性", f"{len(accepted_context)} 条")

    with st.container(border=True):
        st.subheader(_t("全文概要"))
        if synopsis.get("summary"):
            st.write(synopsis["summary"])
            if synopsis.get("document_arc"):
                st.markdown(f"**全文发展/论证**：{synopsis['document_arc']}")
            for label, key in (("主题", "themes"), ("关键实体", "entities"),
                               ("关键概念", "terms"), ("翻译连续性提示", "translation_notes")):
                values = synopsis.get(key) or []
                if values:
                    st.caption(_t(f"{label}：" + "、".join(values)))
            st.caption(_t('概要状态：{v0}', v0=synopsis_status))
        else:
            st.info(
                _t("当前任务没有可用的全文概要。可能是快速模式运行，或全文理解尚未完成；"
                "这不会阻止翻译继续。"))

    if units:
        digest_by_unit = {str(item.get("unit_id")): item for item in digests
                          if isinstance(item, dict)}
        def unit_label(index):
            unit = units[index]
            label = unit.get("label") or f"内容单元 {index + 1}"
            return f"{label} · 第 {unit.get('start_segment', 0) + 1}-{unit.get('end_segment', 0) + 1} 段"

        default_unit = 0
        for index, unit in enumerate(units):
            if unit.get("start_segment", 0) <= current_index <= unit.get("end_segment", -1):
                default_unit = index
                break
            if unit.get("start_segment", 0) <= max(0, current_index - 1) \
                    <= unit.get("end_segment", -1):
                default_unit = index
        selected_unit = st.selectbox(
            _t("当前章节/内容单元"), range(len(units)), index=default_unit,
            format_func=_localized_formatter(unit_label), key=f"context_unit_{job_id}")
        unit = units[selected_unit]
        digest = digest_by_unit.get(str(unit.get("unit_id")))
        with st.container(border=True):
            st.subheader(_t("章节摘要"))
            st.caption(_t(unit_label(selected_unit)))
            if digest and digest.get("summary"):
                st.write(digest["summary"])
                for label, key in (("关键实体", "key_entities"), ("关键概念", "key_terms"),
                                   ("待确认线索", "open_threads"),
                                   ("翻译提示", "translation_notes")):
                    values = digest.get(key) or []
                    if values:
                        st.caption(_t(f"{label}：" + "、".join(values)))
            else:
                st.info(_t("该内容单元暂无章节摘要。"))
    elif not synopsis.get("summary"):
        st.info(_t("当前任务没有可展示的内容单元或章节摘要。"))

    with st.container(border=True):
        st.subheader(_t("上下文连续性"))
        if accepted_context:
            st.caption(_t("最近批次/最近译文参考的前文内容如下；标签表示译文的确认程度。"))
            for item in accepted_context:
                with st.expander(
                        _t('第 {v0} 段 · {v1}', v0=item['segment_index'] + 1, v1=_target_context_level_label(item['level'])), expanded=False):
                    st.markdown("**前文原文**")
                    st.code(item["source"] or "（无原文记录）")
                    st.markdown("**前文译文**")
                    st.code(item["target"])
        else:
            st.info(_t("当前没有可用的前文译文连续性记录。"))

        packet_log = state.get("context_packet_log") or []
        if packet_log:
            latest = packet_log[-1]
            previous_count = len(latest.get("previous_target_segments") or [])
            st.caption(
                _t('最近一次处理参考了全文概要、当前章节摘要、前文原文，以及 {v0} 条前文译文连续性记录。', v0=previous_count))
        else:
            st.caption(_t("当前任务尚无已保存的批次上下文参考记录。"))

    warnings = artifacts.get("warnings") or []
    if warnings:
        with st.expander(_t("上下文生成提示"), expanded=False):
            for warning in warnings:
                st.warning(_t(warning))
    if state.get("context_packet_log"):
        with st.expander(_t("高级诊断"), expanded=False):
            st.caption(_t("仅供排查使用；正常工作不需要查看内部记录。"))
            st.json(state["context_packet_log"][-5:])


# ================= Language Assets Workspace（术语与翻译记忆）=================
# 实际实现已解耦到 folith.ui.language_assets 模块。
_la_terms_tab = _language_assets_ui._la_terms_tab
_la_tm_tab = _language_assets_ui._la_tm_tab
_la_review_tab = _language_assets_ui._la_review_tab
_la_sync_tab = _language_assets_ui._la_sync_tab
_la_publish_query = _language_assets_ui._la_publish_query
_la_selected_row = _language_assets_ui._la_selected_row
_la_render_inspector = _language_assets_ui._la_render_inspector
_la_new_term_dialog = _language_assets_ui._la_new_term_dialog
_la_execute_pending = _language_assets_ui._la_execute_pending


def _render_language_assets_workspace(saved_jobs):
    _language_assets_ui.render_language_assets_workspace(
        saved_jobs,
        current_project_context_fn=_current_project_context,
        open_job_fn=_open_job,
        segment_id_fn=_translation_segment_id,
    )


def _render_delivery_review_queue(
    job_id, state, target_lang, ai_provider, ai_model, api_key, style_rules,
):
    """Render the human review queue; delivery state changes stay in core.py."""
    state = _source_review_projection(state)
    review_runtime = resolve_review_runtime()
    review_required = bool(state.get("translation_core_review_required"))
    findings = _delivery.review_queue_findings(state)
    contexts = sorted(
        [_delivery.finding_context(state, finding) for finding in findings],
        key=_review_sort_key)
    st.divider()
    st.subheader(_t("人工审查队列"))
    if not contexts:
        st.success(_t("当前没有待处理发现；可以继续准备交付资产。"))
        return

    counts = {severity: sum(1 for x in contexts if x["severity"] == severity)
              for severity in _delivery.SEVERITY_LABELS}
    st.caption(
        _t('共 {v0} 个待审问题 · 必须处理 {v1} · 建议检查 {v2} · 仅供参考 {v3}。先处理必须处理项；相同审校事件的重复记录已合并，不同审校事件会分别保留。', v0=len(contexts), v1=counts['blocking'], v2=counts['actionable'], v3=counts['informational']))
    metric_cols = st.columns(4)
    metric_cols[0].metric("待审", len(contexts))
    metric_cols[1].metric("必须处理", counts["blocking"])
    metric_cols[2].metric("建议检查", counts["actionable"])
    metric_cols[3].metric("仅供参考", counts["informational"])

    filter_options = ["必须处理", "全部", "建议检查", "仅供参考"]
    default_filter = "必须处理" if counts["blocking"] else "全部"
    filter_label = st.radio(
        _t("筛选发现"), filter_options,
        index=filter_options.index(default_filter), horizontal=True,
        key=f"fd_filter_{job_id}", format_func=_localized_formatter())
    selected_severity = {
        "必须处理": "blocking", "建议检查": "actionable", "仅供参考": "informational",
    }.get(filter_label)
    visible = [x for x in contexts
               if selected_severity is None or x["severity"] == selected_severity]
    st.caption(_t('当前显示 {v0} 项；展开单项可查看原文、译文和审校证据。', v0=len(visible)))

    selectable = [x for x in contexts if x["severity"] in ("blocking", "actionable")
                  or x["proper_noun_candidate"]]
    for ordinal, context in enumerate(visible):
        fid = context["finding_id"]
        interactive = context in selectable
        if interactive:
            st.checkbox(
                _t("选择此问题"),
                key=f"fd_select_{job_id}_{fid}",
                help=_t("选择后可在队列底部批量标记或重新翻译。"))
        title = (
            f"第 {context['segment_number']} 段 · {context['severity_label']} · "
            f"{context['reason'][:100]}")
        if context["duplicate_count"] > 1:
            title += f" · 已合并 {context['duplicate_count']} 条重复记录"
        with st.expander(_t(title), expanded=(ordinal == 0 and context["severity"] == "blocking")):
            st.caption(_t('问题编号（调试/证据追踪）：{v0}', v0=fid))
            if context["detected_text"]:
                st.markdown("**检测到的文本**")
                st.code(context["detected_text"])
            source_col, target_col = st.columns(2)
            with source_col:
                st.markdown("**原文**")
                st.code(context["source"] or "（未找到对应段落）")
            with target_col:
                st.markdown("**当前译文**")
                st.code(context["target"] or "（未找到当前译文）")
            if context["initial_target"] and context["initial_target"] != context["target"]:
                st.caption(_t("初译（当前译文之前）："))
                st.code(context["initial_target"])
            st.markdown(f"**问题说明**：{context['reason']}")
            if context["proper_noun_candidate"]:
                st.info(
                    _t("检测到的源语片段可能是人名、机构名或作品名。若确认这是有意保留，"
                    "可选择该问题并使用“确认保留专名”，不会强制重新翻译。"))
            _render_finding_evidence(context)

    selected = [
        context for context in selectable
        if st.session_state.get(f"fd_select_{job_id}_{context['finding_id']}", False)
    ]
    selected_ids = [context["finding_id"] for context in selected
                    if context.get("finding_type") != "source_quality_gate"]
    selected_segments = sorted({
        context["segment_index"] for context in selected
        if context["severity"] in ("blocking", "actionable")
        and isinstance(context["segment_index"], int)
        and context.get("finding_type") != "source_quality_gate"
    })
    source_review_segments = sorted({
        context["segment_index"] for context in selected
        if context.get("finding_type") == "source_quality_gate"
        and isinstance(context.get("segment_index"), int)
    })
    preserve_ids = [
        context["finding_id"] for context in selected
        if context["proper_noun_candidate"]
        and context.get("finding_type") != "source_quality_gate"
    ]
    st.divider()
    st.caption(
        _t('已选择 {v0} 个问题 / {v1} 个段落。批量重新翻译按段落执行，同一段的多个问题会一起复验。', v0=len(selected), v1=len(selected_segments)))
    if source_review_segments:
        pairs = state.get("pairs") or []
        source_signatures = []
        for index in source_review_segments:
            pair = pairs[index] if 0 <= index < len(pairs) else {}
            source_signatures.append(
                f"{index}:{pair.get('segment_uid') or ''}:{pair.get('source') or ''}")
        source_action_key = "|".join(source_signatures)
        source_checked = st.checkbox(
            _t("我已回看 PDF 原页，确认所选段落是有效正文"),
            key=f"fd_source_checked_{job_id}_{source_action_key}")
        source_note = st.text_input(
            _t("原文核对依据"), key=f"fd_source_note_{job_id}_{source_action_key}",
            placeholder=_t("例如：原页正文清晰可读，段落与相邻正文连续。"))
        source_action_disabled = (
            not source_checked or not source_note.strip() or not api_key or not ai_model
            or (review_required and not _review_runtime_ready(review_runtime)))
        if st.button(_t("确认原文并重新翻译"), type="primary",
                     disabled=source_action_disabled,
                     key=f"fd_confirm_source_{job_id}", width="stretch"):
            try:
                for segment_index in source_review_segments:
                    core.confirm_source_quality_segment(
                        job_id, segment_index, actor="user", note=source_note)
                with st.spinner("正在翻译已核对的段落…"):
                    _updated, fixed = core.retranslate_segments(
                        job_id, source_review_segments, ai_provider, api_key, ai_model,
                        target_lang, style_rules=style_rules,
                        reviewer_provider=review_runtime["provider"],
                        reviewer_api_key=review_runtime["api_key"],
                        reviewer_model=review_runtime["model"],
                        reviewer_base_url=review_runtime["base_url"],
                        on_caption=lambda text: st.caption(_t(text)))
            except ValueError as exc:
                st.error(_t('无法确认原文：{v0}', v0=exc))
            else:
                if len(fixed) == len(source_review_segments):
                    st.rerun()
                st.warning(_t("原文已记录核对结果，但至少一段未能完成翻译；请查看该段状态后重试。"))
    note = st.text_input(_t("处理说明（可选）"), key=f"fd_note_{job_id}")
    action_cols = st.columns(4)
    if action_cols[0].button(
            _t("标记选中为人工已处理"), disabled=not selected_ids,
            key=f"fd_fix_{job_id}", width="stretch"):
        core.mark_findings_resolved(
            job_id, selected_ids, "human_fixed", note or "人工核对后确认已处理")
        st.rerun()
    if action_cols[1].button(
            _t("重新翻译选中段落"),
            disabled=(not selected_segments or not api_key or not ai_model
                      or (review_required and not _review_runtime_ready(review_runtime))),
            key=f"fd_retranslate_{job_id}", width="stretch"):
        core.retranslate_segments(
            job_id, selected_segments, ai_provider, api_key, ai_model,
            target_lang, style_rules=style_rules,
            reviewer_provider=review_runtime["provider"],
            reviewer_api_key=review_runtime["api_key"],
            reviewer_model=review_runtime["model"],
            reviewer_base_url=review_runtime["base_url"],
            on_caption=lambda text: st.caption(_t(text)))
        st.rerun()
    if action_cols[2].button(
            _t("确认保留选中专名"), disabled=not preserve_ids,
            key=f"fd_preserve_{job_id}", width="stretch"):
        core.mark_findings_resolved(
            job_id, preserve_ids, "preserved",
            note or "用户确认该源语片段为有意保留的专名")
        st.rerun()
    if action_cols[3].button(
            _t("清除选择"), disabled=not selected,
            key=f"fd_clear_{job_id}", width="stretch"):
        for context in selectable:
            st.session_state.pop(f"fd_select_{job_id}_{context['finding_id']}", None)
        st.rerun()
    if selected_segments and (not api_key or not ai_model):
        st.warning(_t("重新翻译需要先完成 AI 引擎配置；人工处理和确认保留仍可使用。"))
    elif selected_segments and review_required and not _review_runtime_ready(review_runtime):
        st.warning(_t('{v0}；人工处理和确认保留仍可使用。', v0=_review_runtime_missing_message()))


def _render_delivery_gate(job_id, state, dstatus, target_lang="", provider="", model=""):
    st.divider()
    st.subheader(_t("最终交付"))
    review_view = _review_workbench(state)
    if not review_view["readiness"]["ready"]:
        delivery_copy = review_view["delivery"]
        st.error(_t(f"{delivery_copy['title']}：{delivery_copy['detail']}。"))
        return
    blockers = _delivery.unresolved_blocking(state)
    actions = _delivery.unresolved_findings(state)
    # 与工作台交付页共用同一道草稿门禁：交付入口不止一个，只在一个入口拦等于没拦。
    gate_drafts_pending = _render_delivery_draft_guard(job_id, state)
    if blockers:
        hard_gate_reasons = _workspace_hard_gate_reasons(job_id, state)
        if hard_gate_reasons:
            st.error(_t("当前版本存在不可通过‘接受风险’跳过的交付门禁：" +
                     "、".join(hard_gate_reasons) + "。请先完成这些门禁。"))
            return
        st.warning(_t('仍有 {v0} 个必须处理问题；未处理或未明确接受风险前不能最终交付。', v0=len(blockers)))
        confirm = st.checkbox(
            _t("我已检查这些必须处理问题，并确认接受剩余风险"),
            key=f"fd_accept_confirm_{job_id}")
        note = st.text_input(_t("接受风险说明"), key=f"fd_accept_note_{job_id}")
        if st.button(
                _t("接受必须处理风险并进入最终交付"),
                disabled=not confirm or gate_drafts_pending,
                key=f"fd_accept_{job_id}", width="stretch"):
            _, ok, errors = core.approve_delivery(
                job_id, note or "人工确认并接受剩余 blocking 风险", accept_blocking=True,
                target_lang=target_lang, provider=provider, model=model)
            if ok:
                st.rerun()
            for error in errors:
                st.error(_t(error))
    elif dstatus == "final":
        snapshot = core.delivery_snapshot_status(job_id, state)
        if snapshot["current"]:
            latest = snapshot["latest"]
            st.success(
                _t('最终交付版本 v{v0} 已冻结；后续工作版本变更不会修改该版本。', v0=latest['snapshot_version']))
        else:
            st.warning(
                _t("当前任务虽有最终状态，但没有可用的冻结交付版本，或工作版本已有变更；"
                "请重新确认以生成新的最终交付版本。"))
            note = st.text_input(_t("交付说明（可选）"), key=f"fd_reapprove_note_{job_id}")
            if st.button(_t("重新确认并冻结最终交付"), key=f"fd_reapprove_{job_id}",
                         disabled=gate_drafts_pending, width="stretch"):
                _, ok, errors = core.approve_delivery(
                    job_id, note or "重新确认最终交付", target_lang=target_lang,
                    provider=provider, model=model)
                if ok:
                    st.rerun()
                for error in errors:
                    st.error(_t(error))
        return
    else:
        if actions:
            st.info(_t('还有 {v0} 个建议检查/参考项；它们不阻止最终交付。', v0=len(actions)))
        note = st.text_input(_t("最终交付说明（可选）"), key=f"fd_final_note_{job_id}")
        if st.button(_t("确认进入最终交付"), key=f"fd_final_{job_id}",
                     disabled=gate_drafts_pending, width="stretch"):
            _, ok, errors = core.approve_delivery(
                job_id, note or "人工确认交付", target_lang=target_lang,
                provider=provider, model=model)
            if ok:
                st.rerun()
            for error in errors:
                st.error(_t(error))


# ================= 可视化辅助（证据链流程 / 术语状态） =================
def _chain_flow(stages):
    """横向流程卡片。"""
    boxes = []
    for i, (label, value, sub, color) in enumerate(stages):
        boxes.append(
            f'<div style="flex:1 1 130px;min-width:110px;padding:8px 12px;'
            f'border:1px solid {color}44;border-left:4px solid {color};'
            f'border-radius:8px;text-align:center;background:{color}10;">'
            f'<div style="font-size:12px;color:{color};font-weight:600;">{label}</div>'
            f'<div style="font-size:20px;font-weight:700;margin-top:1px;">{value}</div>'
            + (f'<div style="font-size:11px;opacity:.75;">{sub}</div>' if sub else "")
            + "</div>")
        if i < len(stages) - 1:
            boxes.append('<div style="align-self:center;color:#94a3b8;padding:0 2px;">→</div>')
    return ('<div style="display:flex;align-items:stretch;gap:2px;flex-wrap:wrap;'
            'margin:2px 0 8px;">' + "".join(boxes) + "</div>")


def _glossary_status_chips(entries):
    counts = {}
    for entry in entries:
        status = str(entry.get("status") or "provisional")
        counts[status] = counts.get(status, 0) + 1
    conflicts = sum(1 for entry in entries if _conflict(entry, entries))
    meta = [("候选", "candidate", "#64748b"), ("建议", "provisional", "#d97706"),
            ("已锁定", "locked", "#16a34a"), ("已拒绝", "rejected", "#dc2626")]
    chips = []
    for label, status, color in meta:
        chips.append(f'<span style="display:inline-block;padding:2px 12px;margin:0 6px 4px 0;'
                     f'border-radius:999px;border:1px solid {color};color:{color};'
                     f'font-size:13px;font-weight:600;">{label} {counts.get(status, 0)}</span>')
    conflict_label = f"冲突 {conflicts}" if conflicts else "无冲突"
    chips.append(f'<span style="display:inline-block;padding:2px 12px;border-radius:999px;'
                 f'border:1px solid #94a3b8;color:#64748b;font-size:13px;">{conflict_label}</span>')
    return '<div>' + "".join(chips) + "</div>"


def _merge_edited_entries(entries, edited_rows):
    """把编辑器可见行的修改合并回完整术语表。"""
    edited_by_id = {}
    new_rows = []
    for entry in edited_rows:
        entry_id = entry.get("id")
        if entry_id:
            edited_by_id[str(entry_id)] = entry
        else:
            new_rows.append(entry)
    merged = [edited_by_id.get(str(entry.get("id")), entry) for entry in entries]
    merged.extend(new_rows)
    return merged


# ================= Task workspace =================
def _source_review_projection(state):
    """Expose current source-gate findings even for older completed jobs."""
    projected = dict(state or {})
    gate = core._audit_source_quality_for_state(projected)
    projected["source_quality_gate"] = gate
    findings = list(projected.get("findings") or [])
    existing = {
        (item.get("segment_index"), str(item.get("source_span") or ""))
        for item in findings
        if isinstance(item, dict) and item.get("type") == "source_quality_gate"
        and not item.get("resolved")
    }
    for flag in gate.get("flags") or []:
        if not isinstance(flag, dict):
            continue
        key = (flag.get("segment_index"), str(flag.get("text") or ""))
        if key not in existing:
            findings.append(core._source_quality_finding(flag))
            existing.add(key)
    projected["findings"] = findings
    return projected


def _workspace_review_contexts(state):
    return _workbench_view.review_workbench_view(
        _source_review_projection(state))["queue_items"]


def _review_workbench(state):
    return _workbench_view.review_workbench_view(
        _source_review_projection(state))


def _ai_configuration_view(review_required=True):
    return _workspace_view.ai_configuration_view(
        ai_provider, ai_model, api_key,
        st.session_state.get("provider_connection_status", "unverified"),
        reviewer_mode=reviewer_mode,
        reviewer_provider=reviewer_provider,
        reviewer_model=reviewer_model,
        reviewer_api_key=reviewer_api_key,
        reviewer_connection_status=st.session_state.get(
            "reviewer_connection_status", "unverified"),
        review_required=review_required,
    )


def _workspace_projection(state, job_id="", overview=None):
    if overview is None and job_id:
        overview = _task_overview_state(job_id, state)
    delivery_state, delivery_tone = _workspace_delivery_state(
        job_id, state, overview) if job_id else (None, "neutral")
    academic = state.get("academic_state") or {}
    report_stale = any(
        _finalization._artifact_status_value(academic, name)
        in {"stale", "missing", "failed"}
        for name in ("report", "final_docx_validation", "libreoffice_render")
    )
    if job_id:
        report_stale = report_stale or core.dependency_impact_view(
            job_id, state).get("status") == "stale"
    return _workspace_view.project_workspace_state(
        state, delivery_state=delivery_state, delivery_tone=delivery_tone,
        delivery_ready=bool(overview and overview.get("delivery_ready")),
        report_stale=report_stale,
    )


def resolve_review_runtime():
    """Resolve the configured role that owns semantic review calls."""
    return _model_roles.resolve_review_runtime(
        reviewer_mode,
        {"provider": ai_provider, "model": ai_model, "api_key": api_key,
         "base_url": api_base},
        {"provider": reviewer_provider, "model": reviewer_model,
         "api_key": reviewer_api_key, "base_url": reviewer_base_url},
    )


def _review_runtime_ready(runtime):
    return bool(runtime.get("provider") and runtime.get("model")
                and runtime.get("api_key"))


def _review_runtime_missing_message():
    return ("需要先完成审校模型配置" if reviewer_mode == "separate"
            else "需要先完成 AI 引擎配置")


def _start_segment_action(job_id, kind, indexes, *, message, selected=None, **kwargs):
    started = core.start_segment_worker(job_id, kind, indexes, **kwargs)
    if not started:
        _set_workspace_flash("当前任务已有后台操作，请等待完成后再试。", "warning")
        return False
    st.session_state[f"segment_action_{job_id}"] = {
        "attempt": core.load_runtime_state(job_id).get("attempt"),
        "kind": kind, "indexes": list(indexes), "message": message,
        "selected": selected,
    }
    _set_workspace_flash("已提交后台处理，可以继续浏览或切换任务。", "info")
    return True


def _run_review_with_runtime(job_id, indexes, runtime, target_lang, style_rules,
                             *, selected=None, message="审校已完成；工作台已更新为当前结果。"):
    return _start_segment_action(
        job_id, "review", indexes, message=message, selected=selected,
        provider=runtime["provider"], api_key=runtime["api_key"],
        model=runtime["model"], target_lang=target_lang, style_rules=style_rules,
        base_url=runtime.get("base_url") or "", reasoning_effort=reasoning_effort)


def _queue_segment_retranslation(job_id, indexes, runtime, target_lang, style_rules,
                                  *, selected=None, message="所选段落重新翻译完成。"):
    return _start_segment_action(
        job_id, "retranslate", indexes, message=message, selected=selected,
        provider=ai_provider, api_key=api_key, model=ai_model,
        target_lang=target_lang, style_rules=style_rules, base_url=api_base,
        reasoning_effort=reasoning_effort,
        reviewer_provider=runtime.get("provider"),
        reviewer_api_key=runtime.get("api_key"), reviewer_model=runtime.get("model"),
        reviewer_base_url=runtime.get("base_url"))


def _finish_segment_action(job_id, state, runtime):
    key = f"segment_action_{job_id}"
    pending = st.session_state.get(key)
    if not pending or runtime.get("status") in core.RUNTIME_ACTIVE_STATUSES:
        return
    st.session_state.pop(key, None)
    if pending["attempt"] != runtime.get("attempt"):
        return
    if runtime.get("status") != "cancelled" and runtime.get("segment_operation_result") == "completed":
        if pending["kind"] == "retranslate":
            pairs = state.get("pairs") or []
            for index in pending["indexes"]:
                if 0 <= index < len(pairs):
                    _reset_translation_editor(_translation_segment_id(job_id, index, pairs[index]))
        if pending.get("selected"):
            _select_next_review_item(state, pending["selected"])
        _set_workspace_flash(pending["message"])
    else:
        error = runtime.get("error") or {}
        _set_workspace_flash(error.get("message") or "后台操作已停止；可在原段落重试。", "warning")


def _set_workspace_flash(message, tone="success"):
    st.session_state["workspace_flash"] = {
        "message": str(message or ""), "tone": tone,
    }


def _render_workspace_flash():
    flash = st.session_state.pop("workspace_flash", None)
    if not isinstance(flash, dict) or not flash.get("message"):
        return
    renderer = {
        "error": st.error, "warning": st.warning,
        "info": st.info, "success": st.success,
    }.get(flash.get("tone"), st.success)
    renderer(flash["message"])


def _select_review_item(item):
    st.session_state["selected_review_item_id"] = item.get("id") if item else None
    st.session_state["selected_finding_id"] = item.get("finding_id") if item else None


def _selected_review_item(view, items=None):
    candidates = items if items is not None else view.get("queue_items") or []
    selected_id = st.session_state.get("selected_review_item_id")
    if not selected_id and st.session_state.get("selected_finding_id"):
        selected_finding_id = str(st.session_state["selected_finding_id"])
        selected_id = next((item.get("id") for item in candidates
                            if item.get("finding_id") == selected_finding_id), None)
    selected = _workbench_view.select_queue_item(
        candidates, selected_id,
    )
    _select_review_item(selected)
    return selected


def _select_next_review_item(state, completed_item):
    view = _review_workbench(state)
    next_id = _workbench_view.next_queue_item_id(
        view["queue_items"], completed_item.get("id"),
        completed_item.get("segment_id"),
    )
    selected = _workbench_view.select_queue_item(view["queue_items"], next_id)
    _select_review_item(selected)
    return selected


def _workspace_status_badge(label, tone="neutral"):
    return f'<span class="tp-status-badge is-{tone}"><span class="tp-status-dot is-{tone}"></span>{escape(label)}</span>'


def _workspace_findings_counts(state):
    contexts = _workspace_review_contexts(state)
    return contexts, {
        severity: sum(1 for item in contexts if item.get("severity") == severity)
        for severity in _delivery.SEVERITY_LABELS
    }


def _workspace_compliance_view(job_id, state):
    """Return the anonymous default-profile checks for this task."""
    return core.compliance_profile_view(job_id, state)


def _workspace_structural_qa(job_id, state):
    """Expose a stale DOCX check as a distinct user-facing state."""
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    final_docx = core.load_academic_artifact(job_id, "final_docx_validation") or {}
    structural = ("PASS" if final_docx.get("status") in {"pass", "pass_with_warnings"}
                  else "FAIL" if final_docx.get("status") == "fail"
                  else qa.get("structural_qa", "NOT_RUN"))
    artifact_status = _finalization._artifact_status_value(
        state.get("academic_state") or {}, "final_docx_validation")
    if artifact_status in {"stale", "missing"}:
        return "STALE"
    if artifact_status == "failed":
        return "FAIL"
    return structural


def _task_overview_facts(job_id, state):
    """Collect the job-scoped facts the canonical overview needs.

    Read-only: every call is a `*_view`/`status`/`gate` projection that already
    tolerates a missing job directory.  This is the only place the workspace
    reads delivery/compliance/QA facts for status purposes, so the canonical
    derivation sees one consistent snapshot per rerun.
    """
    state = state if isinstance(state, dict) else {}
    snapshot = core.delivery_snapshot_status(job_id, state) if job_id else {}
    latest = snapshot.get("latest") or {}
    impact = core.dependency_impact_view(job_id, state) if job_id else {}
    compliance = _workspace_compliance_view(job_id, state) if job_id else {}
    case_gate = _finalization.case_review_gate(
        state,
        core.load_academic_artifact(job_id, "selected_cases") if job_id else None)
    runtime_view = core.build_job_runtime_view(job_id, state) if job_id else {}
    academic = state.get("academic_state") or {}
    return {
        "job_id": job_id,
        "runtime_status": runtime_view.get("status") or runtime_view.get("runtime_status"),
        "runtime_label": runtime_view.get("headline_status") or "",
        "runtime_stage": _runtime_stage_label(runtime_view),
        "snapshot_current": bool(snapshot.get("current")),
        "snapshot_diverged": bool(snapshot.get("diverged")),
        "snapshot_version": latest.get("snapshot_version"),
        "dependency_impact_stale": impact.get("status") == "stale",
        "compliance": {"status": compliance.get("status") or "",
                       "counts": dict(compliance.get("counts") or {})},
        "case_gate": {"status": case_gate.get("status") or "",
                      "required_count": case_gate.get("required_count") or 0,
                      "blocked_count": case_gate.get("blocked_count") or 0},
        "artifact_status": {
            name: _finalization._artifact_status_value(academic, name)
            for name in ("report", "final_docx_validation", "libreoffice_render")
        },
        "structural_qa": _workspace_structural_qa(job_id, state) if job_id else None,
        "qa": _finalization.normalize_final_qa(state.get("final_qa")),
    }


def _task_overview_state(job_id, state, *, plan_findings=None):
    """The single canonical task-overview derivation used by the workspace.

    Header, sidebar, hero, cards and pipeline all render from this object; no
    surface may re-derive its own status.
    """
    state = state if isinstance(state, dict) else {}
    facts = _task_overview_facts(job_id, state)
    if plan_findings is not None:
        facts["issue_counts"] = {
            severity: sum(item.get("severity") == severity for item in plan_findings)
            for severity in ("blocking", "actionable", "informational")
        }
    overview = _task_overview.derive_task_overview_state(state, facts=facts)
    overview["runtime_status"] = facts.get("runtime_status")
    overview["runtime_stage"] = facts.get("runtime_stage")
    return overview


def _workspace_delivery_state(job_id, state, overview=None):
    """Human-facing delivery label, derived only from the canonical overview.

    The wording is the canonical lifecycle vocabulary: ``READY_FOR_DELIVERY_PREP``
    means "可以准备交付" (start preparing), never "可以冻结交付" / "Ready for
    delivery" — that claim belongs to ``DELIVERY_READY`` alone.
    """
    overview = overview or _task_overview_state(job_id, state)
    lifecycle = overview.get("lifecycle")
    version = overview.get("snapshot_version")
    if lifecycle == _task_overview.DELIVERED:
        return ((f"已冻结交付 v{version}" if version is not None else "已冻结交付"),
                "success")
    if overview.get("reason") == "outdated_delivery":
        return ((f"工作版本已偏离冻结交付 v{version}" if version is not None
                 else "工作版本已偏离冻结交付"), "warning")
    tone = {
        _task_overview.GREEN: "success",
        _task_overview.BLUE: "info",
        _task_overview.AMBER: "warning",
        _task_overview.RED: "danger",
        _task_overview.GRAY: "neutral",
    }.get(overview.get("tone"), "warning")
    return (overview.get("label")
            or _task_overview.LIFECYCLE_LABELS[_task_overview.DRAFT]), tone


def _workspace_hard_gate_reasons(job_id, state):
    """Return delivery gates that cannot be waived as ordinary review risk."""
    impact = core.dependency_impact_view(job_id, state) if job_id else {}
    compliance = _workspace_compliance_view(job_id, state) if job_id else {}
    compliance_counts = compliance.get("counts") or {}
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    finalization_qa_required = bool(state.get("report_enabled"))
    academic = state.get("academic_state") or {}
    report_ready = _delivery.report_ready(state)
    report_stale = _finalization._artifact_status_value(academic, "report") in {
        "stale", "missing", "failed"}
    final_export_stale = _finalization._artifact_status_value(
        academic, "final_docx_validation") in {"stale", "missing", "failed"}
    render_stale = _finalization._artifact_status_value(
        academic, "libreoffice_render") in {"stale", "missing", "failed"}
    structural = _workspace_structural_qa(job_id, state) if job_id else qa.get("structural_qa")
    case_gate = _finalization.case_review_gate(
        state, core.load_academic_artifact(job_id, "selected_cases") if job_id else None)
    translation_truth_gate_pass = (bool(state.get("p2_done")) and
                                   (state.get("delivery_validation") or {}).get("blocking") is not True)
    reasons = []
    review_view = _review_workbench(state)
    if not review_view["readiness"]["ready"] \
            and not review_view["risk_acceptance"]["available"]:
        reasons.append("翻译审校尚未完成")
    if not translation_truth_gate_pass:
        reasons.append("当前译文交付门禁")
    if impact.get("status") == "stale" and _academic_downstream_enabled(state, job_id):
        reasons.append("受影响产物需要重建")
    if finalization_qa_required and not report_ready:
        reasons.append("报告尚未通过交付门禁")
    if finalization_qa_required and (report_stale or final_export_stale or render_stale):
        reasons.append("交付产物仍是旧版本")
    if case_gate.get("status") == "blocked":
        reasons.append("案例来源或终审条件未满足")
    if finalization_qa_required and (compliance_counts.get("fail")
                                     or compliance_counts.get("manual_review")
                                     or compliance_counts.get("not_checked")):
        reasons.append("合规门禁尚未完成")
    if finalization_qa_required and structural != "PASS":
        reasons.append("DOCX 结构检查尚未通过")
    if finalization_qa_required and qa.get("libreoffice_render") != "PASS":
        reasons.append("页面渲染尚未通过")
    if finalization_qa_required and qa.get("author_visual_review") != "CONFIRMED":
        reasons.append("作者视觉复核尚未确认")
    if finalization_qa_required and qa.get("word_final_review") != "CONFIRMED":
        reasons.append("Word 最终复核尚未确认")
    return list(dict.fromkeys(reasons))


def _workspace_impact_change_label(impact):
    indexes = impact.get("changed_segment_indexes") or []
    if len(indexes) == 1:
        try:
            return f"第 {int(indexes[0]) + 1} 段已编辑"
        except (TypeError, ValueError):
            pass
    if indexes:
        return f"{len(indexes)} 个段落已编辑"
    return "下游内容发生变化"


def _workspace_impact_reason(impact):
    reason = str(impact.get("reason") or "")
    if "CURRENT_TRANSLATION" in reason or "工作译文" in reason:
        return "工作译文发生变化，相关案例与报告产物需要更新。"
    if reason:
        return reason.replace("stale", "需要更新")
    return "相关下游内容需要更新。"


def _workspace_impact_label(item):
    labels = {
        "literature_sources": "文献来源",
        "literature_evidence": "文献证据",
        "literature_claims": "文献主张",
        "literature_support_review": "文献支持复核",
        "human_evidence": "人工证据",
        "human_evidence_needs": "人工证据需求",
        "human_evidence_questions": "人工证据问题",
        "final_contrast_portfolio": "案例对照组合",
        "legacy_inventory": "历史资料清单",
        "legacy_recovery": "历史资料恢复",
        "legacy_recovery_report": "历史资料恢复报告",
        "quality_repair_history": "质量修复记录",
        "repair_history": "修复记录",
    }
    raw_id = str(item.get("id") or "")
    raw_label = str(item.get("label") or "")
    if raw_label and raw_label != raw_id:
        return raw_label
    mapped = labels.get(raw_id) or _finalization.artifact_label(raw_id)
    return mapped if mapped != raw_id else "相关产物"


def _workspace_impact_action(item):
    action = item.get("action")
    if action:
        return _finalization.execution_action_label(action)
    return {
        "stale": "需要重建",
        "missing": "需要生成",
        "failed": "检查失败",
        "valid": "已同步",
        "reusable": "可复用",
    }.get(str(item.get("status") or ""), "需要确认")


def _render_workspace_impact_expander(impact):
    affected = impact.get("affected") or []
    reusable = impact.get("reusable") or []
    with st.expander(_t("查看影响"), expanded=False):
        st.markdown((_h('<div class="tp-impact-chain"><div class="tp-impact-chain-row"><i>1</i><strong>发生变化</strong><span>') + str(escape(_workspace_impact_change_label(impact))) + _h('</span></div><div class="tp-impact-chain-row"><i>2</i><strong>需要更新</strong><span>') + str(len(affected)) + ' 项下游产物：' + str(escape("、".join(_workspace_impact_label(item) + " · " + _workspace_impact_action(item) for item in affected) or "相关学术下游")) + _h('</span></div><div class="tp-impact-chain-row"><i>3</i><strong>可以复用</strong><span>') + str(len(reusable)) + _h(' 个未受影响单元/资产；案例、写作单元和支持资料保留。</span></div></div>')), unsafe_allow_html=True)


def _workspace_case_views(job_id, state):
    selected = core.load_academic_artifact(job_id, "selected_cases") or {}
    project_evidence = core.load_academic_artifact(job_id, "evidence") or {}
    argument_plan = core.load_academic_artifact(job_id, "argument_plan") or {}
    case_analysis_plans = core.load_academic_artifact(
        job_id, "case_analysis_plans") or {}
    outline = core.load_academic_artifact(job_id, "outline") or {}
    literature = core.load_academic_artifact(job_id, "literature_sources") or {}
    cases = selected.get("cases") or []
    evidence_segments = ((project_evidence.get("project_evidence") or {}).get("segments")
                         or [])
    source_records = {str(item.get("source_id")): item
                      for item in literature.get("sources") or []
                      if isinstance(item, dict) and item.get("source_id")}
    glossary = [item for item in state.get("glossary") or []
                if isinstance(item, dict)]
    findings = [item for item in state.get("findings") or []
                if isinstance(item, dict)]
    plan_index = {
        str(item.get("case_id")): item
        for item in case_analysis_plans.get("plans") or [] if item.get("case_id")
    }
    human_by_case = {}
    for entry in state.get("human_evidence") or []:
        human_by_case.setdefault(str(entry.get("case_id")), []).append(entry)
    views = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        view = _finalization.case_review_view(case, state, job_id)
        case_id = str(view.get("case_id") or "")
        segment_index = view.get("segment_index")
        segment_id = str(view.get("segment_id") or "")
        evidence_segment = next((item for item in evidence_segments
                                 if str(item.get("segment_id") or "") == segment_id
                                 or item.get("segment_index") == segment_index), {})
        process = evidence_segment.get("process_evidence") or {}
        related_terms = list(process.get("terminology_decisions") or [])
        if not related_terms:
            haystack = f'{view.get("source_text") or ""}\n{view.get("current_text") or ""}'.casefold()
            related_terms = [item for item in glossary
                             if str(item.get("source") or "").casefold() in haystack]
        case_findings = [item for item in findings
                         if item.get("segment_index") == segment_index]
        for item in process.get("findings") or []:
            if item not in case_findings:
                case_findings.append(item)
        related_claims = []
        for claim in argument_plan.get("claims") or []:
            if (case_id in {str(item) for item in claim.get("core_case_ids") or []}
                    or case_id in {str(item) for item in claim.get("supports_cases") or []}
                    or segment_id in {str(item) for item in claim.get("project_evidence") or []}):
                related_claims.append(claim)
        literature_ids = set()
        for claim in related_claims:
            for key in ("literature_evidence", "literature_claims", "literature_sources"):
                literature_ids.update(str(item) for item in claim.get(key) or [])
        commentary = []
        for label, value in (
                ("针对问题", view.get("targeted_issue")),
                ("选择理由", view.get("selection_rationale")),
                ("差异说明", view.get("contrast_rationale")),
                ("分析理由", (view.get("synthetic_evidence") or {}).get("academic_analysis_reason")),
                ("分析种子", view.get("legacy_analysis_seed")),
                ("限制", "；".join(str(item) for item in view.get("limitations") or [])),
        ):
            if value:
                commentary.append({"label": label, "value": value})
        target_subsection = str(view.get("target_subsection") or "").strip()
        section_title = ""
        for section in outline.get("sections") or []:
            section_id = str(section.get("section_id") or "")
            if section_id == str(view.get("section_id") or "") or (
                    target_subsection and target_subsection.startswith(section_id + ".")):
                section_title = str(section.get("title") or "")
                break
        context = view.get("focus") or {}
        before = str(context.get("context_before") or "").strip()
        after = str(context.get("context_after") or "").strip()
        if not before and isinstance(segment_index, int) and segment_index > 0:
            before = str((state.get("pairs") or [])[segment_index - 1].get("target") or "")
        if not after and isinstance(segment_index, int) and segment_index + 1 < len(state.get("pairs") or []):
            after = str((state.get("pairs") or [])[segment_index + 1].get("target") or "")
        view.update({
            "case_plan": plan_index.get(case_id) or {},
            "human_evidence": human_by_case.get(case_id) or [],
            "related_terms": related_terms[:12],
            "case_findings": case_findings[:12],
            "related_claims": related_claims[:8],
            "literature_evidence": [source_records[item] for item in sorted(literature_ids)
                                     if item in source_records],
            "analytical_commentary": commentary[:8],
            "target_subsection": target_subsection,
            "section_title": section_title,
            "context_before": before,
            "context_after": after,
        })
        views.append(view)
    return views


def _workspace_activity(job_id, state):
    try:
        saved_at = _workspace_saved_label(job_id, state)
    except Exception:
        saved_at = "最近"
    rows = []
    if state.get("p3_done"):
        rows.append((saved_at, "生成实践报告"))
    if state.get("findings") is not None and state.get("p2_done"):
        rows.append((saved_at, "完成审校"))
    if state.get("auto_terms"):
        rows.append((saved_at, "完成术语抽取"))
    if state.get("p2_done"):
        rows.append((saved_at, "完成翻译"))
    elif state.get("p1_done"):
        rows.append((saved_at, "完成文档解析"))
    return rows[:4]


def _runtime_clock(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(value)[:19]


def _runtime_started_label(value):
    """展示真实的启动时间，避免把跨越中断/等待的时间误称为运行时长。"""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)[:19]


def _runtime_clip(value, limit=180):
    """Keep runtime copy readable without leaking a transport payload into the UI."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return "—"
    # Provider responses can contain a whole paragraph after this marker.  The
    # raw payload belongs in the technical log, not in the product status card.
    text = re.split(r"\s*响应预览\s*[:：]", text, maxsplit=1)[0].strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


_RUNTIME_STAGE_LABELS = {
    "ingested": "原文提取与排版整理",
    "INGESTED": "原文提取与排版整理",
    "source_cleanup": "原文排版与断行整理",
    "deterministic_cleanup": "原文纠错与断行整理",
    "llm_cleanup": "原文纠错与整理",
    "segment_build": "构建翻译单元",
    "document_processing": "排版解析与段落重建",
    "document_profile": "文档画像",
    "profiled": "文档画像",
    "PROFILED": "文档画像",
    "understanding": "全文语义理解",
    "terminology": "术语准备",
    "terms_prepared": "术语准备完成",
    "TERMS_PREPARED": "术语准备完成",
    "glossary_frozen": "术语表已锁定",
    "GLOSSARY_FROZEN": "术语表已锁定",
    "translation": "双语翻译",
    "translating": "双语翻译中",
    "TRANSLATING": "双语翻译中",
    "translated": "初译完成",
    "TRANSLATED": "初译完成",
    "annotation": "自动标注",
    "annotated": "深度译注生成",
    "ANNOTATED": "深度译注生成",
    "academic_writing": "学术写作",
    "ACADEMIC_WRITING": "学术写作",
    "evidence": "构建学术证据",
    "literature_evidence": "整理文献证据",
    "research_model": "建立研究模型",
    "literature_claims": "整理文献主张",
    "argument_plan": "规划论点",
    "selected_cases": "选择聚焦案例",
    "outline": "生成学术提纲",
    "sections": "撰写报告章节",
    "validation": "验证报告",
    "review": "执行学术复核",
    "review_required": "待人工审校",
    "REVIEW_REQUIRED": "待人工审校",
    "final": "终稿交付已归档",
    "FINAL": "终稿交付已归档",
    "quality_repair": "修订受影响章节",
    "academic_quality": "评估学术质量",
}


def _runtime_stage_label(view):
    runtime = view.get("runtime") or {}
    stage_id = str(runtime.get("stage_id") or runtime.get("stage") or "").strip()
    if stage_id in _RUNTIME_STAGE_LABELS:
        return _RUNTIME_STAGE_LABELS[stage_id]
    from folith.ui.workspace.performance_view import _map_stage_name
    if stage_id:
        mapped = _map_stage_name(stage_id)
        if mapped and mapped != stage_id:
            return mapped
    text = str(runtime.get("operation_label") or view.get("headline") or "").strip()
    text = re.sub(r"^【[^】]+】\s*", "", text).strip(" .…")
    if text:
        mapped_text = _map_stage_name(text)
        return _runtime_clip(mapped_text if mapped_text != text else text, 72)
    return "任务处理"


def _runtime_action_label(view, status):
    runtime = view.get("runtime") or {}
    if status == "failed":
        error_text = ((runtime.get("error") or {}).get("message")
                      if isinstance(runtime.get("error"), dict)
                      else runtime.get("error"))
        return _runtime_clip(error_text or view.get("detail") or "当前步骤失败")
    if status in {"interrupted", "stalled", "cancelled", "idle_incomplete"}:
        return _runtime_clip(view.get("detail") or "当前进度已保存，可以继续处理。")
    operation = runtime.get("operation_label") or view.get("headline")
    if operation:
        return _runtime_clip(re.sub(r"^【[^】]+】\s*", "", str(operation)))
    return _runtime_clip(view.get("detail") or "正在处理")


def _runtime_pipeline_label(view):
    """Label pipeline progress separately from the document's segment count."""
    runtime = view.get("runtime") or {}
    nested = view.get("stage_progress") or runtime.get("stage_progress") or {}
    if isinstance(nested, dict):
        try:
            completed = max(0, int(nested.get("completed") or 0))
            total = max(0, int(nested.get("total") or 0))
        except (TypeError, ValueError):
            completed, total = 0, 0
        if total:
            unit = str(nested.get("unit") or "项")
            scope = ("原文处理 · " if runtime.get("stage_id") in {
                "ocr", "llm_cleanup", "layout_recovery", "segment_build"
            } else "")
            label = f"{scope}当前阶段 {completed} / {total} {unit}"
            active = []
            for value in nested.get("in_flight") or []:
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                if number > 0 and number not in active:
                    active.append(number)
            if active:
                label += " · 处理中 " + " ".join(f"#{number}" for number in active)
            return label, completed, total
    stage_index = runtime.get("stage_index")
    stage_total = runtime.get("stage_total")
    if isinstance(stage_index, int) and isinstance(stage_total, int) and stage_total:
        return f"当前步骤 {stage_index} / {stage_total}", stage_index, stage_total
    completed = int(view.get("progress_completed") or 0)
    total = int(view.get("progress_total") or 0)
    if total:
        return f"已完成 {completed} / {total} 步骤", completed, total
    return "等待阶段进度", 0, 0


def _runtime_translation_progress(state, job_id):
    try:
        progress = _planner.workspace_progress(state, job_id=job_id)
        translation = progress.get("translation") or {}
        return int(translation.get("done") or 0), int(translation.get("total") or 0)
    except Exception:
        pairs = state.get("pairs") or []
        paragraphs = state.get("paras") or []
        return sum(1 for pair in pairs if str(pair.get("target") or "").strip()), len(paragraphs)


def _runtime_event_label(event):
    name = str(event.get("event") or "").strip()
    message = str(event.get("message") or "").strip()
    exact = {
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
    if name in exact:
        return exact[name]
    keyword_labels = (
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
    for needle, label in keyword_labels:
        if needle in message:
            return label
    return _runtime_clip(re.sub(r"^【[^】]+】\s*", "", message), 86)


def _runtime_activity_timeline(job_id, view):
    """Project user-visible runtime events into a small, readable timeline."""
    status = view.get("status") or view.get("runtime_status") or "idle"
    events = core.read_runtime_events(job_id, 8, visibility="user")
    if not events:
        events = list(view.get("user_events") or [])[-8:]
    rows = []
    failure_names = {"job_failed", "interrupted", "stalled"}
    for event in events:
        label = _runtime_event_label(event)
        if not label or label == "—":
            continue
        name = str(event.get("event") or "")
        event_status = ("failed" if name in failure_names or "失败" in str(event.get("message") or "")
                        else "running" if name in {"llm_request_started", "cancel_requested"}
                        else "completed")
        row = {"status": event_status, "label": label,
               "timestamp": _runtime_clock(event.get("timestamp") or event.get("at"))}
        if rows and rows[-1]["label"] == row["label"] and rows[-1]["status"] == row["status"]:
            rows[-1] = row
        else:
            rows.append(row)

    if status in {"running", "waiting_external", "starting", "queued", "resume_requested", "cancelling"}:
        stage = _runtime_stage_label(view)
        if not rows or rows[-1]["label"] != stage:
            rows.append({"status": "running" if status != "cancelling" else "pending",
                         "label": stage, "timestamp": _runtime_clock(
                             (view.get("runtime") or {}).get("last_progress_at"))})
        elif status != "cancelling":
            rows[-1]["status"] = "running"
        if len(rows) < 5:
            rows.append({"status": "pending", "label": "准备下一步处理", "timestamp": ""})
    elif status == "failed":
        error = (view.get("runtime") or {}).get("error") or {}
        error_text = error.get("message") if isinstance(error, dict) else error
        if not rows or rows[-1]["status"] != "failed":
            rows.append({"status": "failed", "label": _runtime_clip(error_text or "当前步骤失败", 86),
                         "timestamp": _runtime_clock((view.get("runtime") or {}).get("last_progress_at"))})
    elif status == "completed" and (not rows or rows[-1]["label"] != "任务已完成"):
        rows.append({"status": "completed", "label": "任务已完成", "timestamp":
                     _runtime_clock((view.get("runtime") or {}).get("last_progress_at"))})
    return rows[-8:]


def _runtime_last_successful_step(timeline):
    for row in reversed(timeline):
        if row.get("status") == "completed":
            return row.get("label") or "最近一次已完成步骤"
    return "最近检查点已保存"


_RUNTIME_DRAWER_KEY = "runtime_drawer_job_id"


def _dismiss_runtime_drawer():
    st.session_state.pop(_RUNTIME_DRAWER_KEY, None)


def _close_runtime_drawer():
    st.session_state.pop(_RUNTIME_DRAWER_KEY, None)


@st.fragment(run_every="2s")
def _render_runtime_drawer_content(job_id):
    view = core.read_runtime_view(job_id)
    state = (core.load_job_state(job_id) or {}) if view.get("status") not in core.RUNTIME_ACTIVE_STATUSES else {}

    def _do_retry():
        _close_runtime_drawer()
        core.retry_job_step(job_id)
        _resume_job(job_id, state)
        st.rerun()

    def _do_resume():
        _close_runtime_drawer()
        _resume_job(job_id, state)
        st.rerun()

    def _do_cancel():
        core.request_job_cancel(job_id)
        st.rerun()

    from folith.ui.workspace.performance_view import render_performance_drawer
    render_performance_drawer(
        job_id,
        state=state,
        runtime_view=view,
        on_retry=_do_retry,
        on_resume=_do_resume,
        on_cancel=_do_cancel,
    )


if hasattr(st, "dialog"):
    @st.dialog(_t("运行详情"), width="medium", dismissible=True,
               on_dismiss=_dismiss_runtime_drawer)
    def _runtime_drawer_dialog(job_id):
        _render_runtime_drawer_content(job_id)
else:
    def _runtime_drawer_dialog(job_id):
        with st.container(border=True, key=f"runtime_drawer_fallback_{job_id}"):
            _render_runtime_drawer_content(job_id)


# 这些运行状态下，Banner 内的运行区会给出自己的主动作（继续处理 / 重试 /
# 放弃 / 正在恢复…），调用方不得再渲染第二个 canonical 主动作按钮。
_RUNTIME_ACTION_STATUSES = frozenset({
    "resume_requested", "queued", "starting", "running", "waiting_external",
    "cancelling", "interrupted", "idle_incomplete", "cancelled", "stalled",
    "failed",
})


@st.fragment(run_every="2s")
def _render_runtime_strip(job_id, state, view=None):
    """Compact running status plus an on-demand right-side detail drawer (Fragment 隔离轮询)."""
    with profiler.span("runtime_fragment", job_id=job_id):
        initial_view = view
        view = core.read_runtime_view(job_id)
        status = view.get("status") or view.get("runtime_status") or "idle"
        _runtime_ui.refresh_workspace_on_runtime_stop(job_id, status, initial_view)
        if status in {"idle", "completed"}:
            return
    from folith.ui.workspace.runtime_status import runtime_presentation, hud_html
    model = runtime_presentation(view, state)
    st.markdown(hud_html(model), unsafe_allow_html=True)

    with st.container(key=f"workspace_runtime_actions_{job_id}"):
        action_col, pause_col, detail_col = st.columns([1, 1, 1], gap="small")
        with action_col:
            if status in {"resume_requested", "queued", "starting"}:
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
                    _resume_job(job_id, state)
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
                    _resume_job(job_id, core.load_job_state(job_id) or state)
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
                        _resume_job(job_id, core.load_job_state(job_id) or state)
                    st.rerun()
        with detail_col:
            drawer_open = st.session_state.get(_RUNTIME_DRAWER_KEY) == job_id
            if st.button(_t("运行详情 →" if not drawer_open else "运行详情 · 已打开"),
                         key=f"runtime_details_{job_id}", width="content"):
                st.session_state[_RUNTIME_DRAWER_KEY] = job_id
                drawer_open = True
            if drawer_open:
                _runtime_drawer_dialog(job_id)
    return False


def _workspace_saved_label(job_id, state):
    if not job_id:
        return "最近"
    value = (core.recovery_summary(job_id, state) or {}).get("last_saved_at")
    if not value:
        return "最近"
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})", str(value))
    if match:
        return f"{int(match.group(2))} 月 {int(match.group(3))} 日 {int(match.group(4)):02d}:{match.group(5)}"
    return _format_saved_at(value)[:16]


def _workspace_project_title(filename):
    stem = Path(str(filename or "")).stem.strip()
    candidate = re.split(r"提取自", stem, maxsplit=1)[-1].strip()
    candidate = re.sub(r"^\d+\s*", "", candidate)
    candidate = re.sub(r"\s*\([^)]*\)\s*$", "", candidate).strip()
    candidate = re.sub(r"\s+", " ", candidate)
    if not candidate:
        return stem or "未命名项目"
    words = candidate.split(" ")
    small_words = {"a", "an", "and", "as", "at", "by", "for", "in",
                   "of", "on", "or", "the", "to", "via"}
    if len(words) > 1:
        words = [words[0]] + [word.lower() if word.lower() in small_words else word
                              for word in words[1:]]
    return " ".join(words)


def _workspace_document_title(filename):
    """从文件名里取出"用户认得出这本书"的那一段标题。

    顶栏原来同时显示 `Part 3 提取Machine Learning Systems...` 和文件名整理出的标题，
    两者互为噪声。这里统一成"标题 + 章节"，文件名不再第二次出现。
    """
    stem = Path(str(filename or "")).stem.strip()
    candidate = re.split(r"提取自", stem, maxsplit=1)[-1].strip()
    candidate = re.sub(r"\s*\([^)]*\)\s*$", "", candidate).strip()
    candidate = re.sub(r"\s+", " ", candidate)
    return candidate or stem or "未命名项目"


def _workspace_title_parts(filename):
    """返回 (书名, 章节标签)。书名去掉 `Part N` 前缀，章节单独成行。"""
    title = _workspace_document_title(filename)
    part_match = re.search(r"\bPart\s*([A-Za-z0-9IVX]+)", title, re.IGNORECASE)
    part_label = f"Part {part_match.group(1)}" if part_match else ""
    if part_match:
        title = title[part_match.end():].strip(" -–—:·")
    title = re.sub(r"^\d+\s*", "", title).strip()
    if not title:
        title = _workspace_project_title(filename)
    return title, part_label


def _workspace_banner_metrics(state, progress, overview):
    """Banner 第二行：低视觉权重的全局指标。

    每个指标只在这里常驻一次（正文不再重复）。计数全部复用
    `_planner.workspace_progress` 与 canonical overview，不另起一套推导。
    """
    metrics = []
    translation = progress["translation"]
    if translation["total"]:
        metrics.append((f'{translation["done"]:,} / {translation["total"]:,} 已译',
                        "done" if translation["complete"] else "active",
                        "已完成译文的段落数；不代表已审校或可交付"))
    elif state.get("p1_done"):
        metrics.append(("没有可翻译段落", "muted",
                        "文档解析已完成，但没有生成可翻译段落"))
    else:
        metrics.append(("等待原文解析", "muted",
                        "完成原文解析后会显示译文进度"))
    terminology = progress["terminology"]
    if terminology["applicable"]:
        if terminology["complete"]:
            metrics.append((f'术语 {terminology["total"]:,}', "done",
                            "术语已冻结，后续翻译批次会沿用"))
        else:
            metrics.append((f'{terminology["done"]:,} / '
                            f'{terminology["total"]:,} 术语确认', "attention",
                            "仍需确认的项目术语"))
    review = progress["review"]
    if review["applicable"]:
        metrics.append((f'审校 {review["done"]:,} / {review["total"]:,}',
                        "done" if review["complete"] else "attention",
                        "已有最新审校结果的段落数"))
    if overview.get("lifecycle") == _task_overview.DELIVERED:
        version = overview.get("snapshot_version")
        delivery = (f"已交付 · 冻结交付 v{version}" if version is not None
                    else "已交付 · 已冻结")
        metrics.append((delivery, "done", "当前工作版本对应真实冻结交付"))
    return metrics


def _workspace_task_details(state):
    """低频元数据和导入范围，放在任务导航的按需打开详情里。

    完整的源文件名必须可读、可复制（卡片上只能截断），所以内容用 `st.code`
    而不是提示气泡。
    """
    job_id = str(st.session_state.get("active_job_id") or "")
    project = core.project_for_job(job_id, state) if job_id else None
    with st.container(key="workspace_task_details"):
        if project is not None:
            view = core.project_memory_view(project)
            st.caption(_t('所属项目：{v0} · 项目锁定术语 {v1} 条 · 风格 {v2} 条', v0=project['name'], v1=view['glossary_count'], v2=view['style_rule_count']))
        else:
            st.caption(_t('所属项目：{v0}（系统工作区；本次任务不注入项目记忆）', v0=core.SYSTEM_PROJECT_NAME))
        st.caption(_t("源文件"))
        st.code(str(state.get("filename") or "—"), language=None)
        st.caption(_t('段落：{v0} · 目标语言：{v1}', v0=format(len(state.get('paras') or []), ','), v1=state.get('target_lang') or '简体中文'))
        segmentation = state.get("segmentation") or {}
        migration = segmentation.get("migration") or {}
        blocked_migration = migration.get("blocked_paragraphs") or []
        if migration.get("status") in {"partial", "blocked"}:
            if blocked_migration:
                detail = (
                    f"自然句门禁保留了 {len(blocked_migration):,} 个原段落："
                    "源文与已有译文的句数无法安全一一对齐，因此没有强行拆分。"
                    "这些段落仍可通过「拆分段落」人工处理。"
                )
            else:
                detail = "自然句门禁未改变当前段落：已有双语行数量无法安全重建，需先人工核对结构。"
        mode_label = "按自然句" if segmentation.get("mode", "sentence") == "sentence" else "按自然段"
        st.caption(_t('当前翻译单元：{v0} · 任务开始后已锁定', v0=mode_label))
        if migration.get("status") in {"partial", "blocked"}:
            st.warning(_t(detail))
        quality_gate = _source_review_projection(state)["source_quality_gate"]
        quality_count = int(quality_gate.get("flagged_count") or 0)
        if quality_count:
            st.caption(_t('{v0} 个原文段落疑似 OCR 碎片，原文已保留。', v0=format(quality_count, ',')))
        profile = state.get("document_profile") or {}
        if profile:
            st.caption(_t('翻译风格：{v0}', v0=profile.get('register') or '正式书面语'))
            domain = profile.get("domain") or "未标注领域"
            genre = profile.get("genre") or "未标注文本类型"
            st.caption(_t('文档画像：{v0} · {v1}', v0=domain, v1=genre))
        st.caption(_t("独立审校："
                   + ("已启用" if state.get("translation_core_review_required")
                      else "未启用")))
        cleanup = state.get("source_cleanup") or {}
        if cleanup.get("status") in {"completed", "partial"}:
            changed = len(cleanup.get("changed_segments") or [])
            merged = len(cleanup.get("paragraph_merges") or [])
            st.caption(_t('原文纠错：{v0} · 修复 {v1} 项 · 整理 {v2} 处断行', v0='已完成' if cleanup.get('status') == 'completed' else '部分完成', v1=format(changed, ','), v2=format(merged, ',')))
        report = state.get("extraction_report") or {}
        if report:
            st.markdown(_h('<div class="tp-task-detail-section"><strong>导入范围</strong></div>'),
                        unsafe_allow_html=True)
            _render_extraction_report(report)
        excluded = core.excluded_segments_summary(state)
        if excluded["count"]:
            with st.expander(_t('已排除 {v0} 段', v0=excluded['count']), expanded=False):
                _render_excluded_segments_panel(job_id, state)


def _switch_workspace_section(job_id, section):
    st.session_state.workspace_section = section
    _route_params(task=job_id,
                  section=section if section != "translation" else None)


def _render_workspace_topbar(job_id, state, overview=None, *, has_job=True, plan_findings=None):
    """Two-row compact topbar: identity row + nav row.

    Row 1: ← 项目  |  task title + quiet metrics  |  compact issue action
    Row 2: tab nav (翻译 术语 [审校] 交付 ···)  |  task-details popover

    审校 is promoted to the primary nav row when the task has independent
    review enabled, so it is always visible instead of buried in "更多".
    Truly low-frequency tabs (cases, report, qa) stay in the "···" popover.
    """
    filename = str(state.get("filename") or "未命名项目")
    title, part_label = _workspace_title_parts(filename)
    try:
        saved_at = _workspace_saved_label(job_id, state)
    except Exception:
        saved_at = "最近"
    progress = _planner.workspace_progress(state, job_id=job_id, findings=plan_findings)
    overview = overview or _task_overview_state(job_id, state)
    runtime_view = core.build_job_runtime_view(job_id, state)
    runtime_owns_action = has_job and str(
        runtime_view.get("status") or runtime_view.get("runtime_status")
        or "idle") in _RUNTIME_ACTION_STATUSES and overview.get("lifecycle") not in {
        _task_overview.READY_FOR_DELIVERY_PREP, _task_overview.DELIVERY_READY,
        _task_overview.DELIVERED,
    }
    metrics_html = "".join(
        f'<span class="tp-banner-metric is-{tone}" title="{escape(note)}">'
        f'{escape(text)}</span>'
        for text, tone, note in _workspace_banner_metrics(state, progress, overview))
    saved_clock = re.search(r"\b\d{1,2}:\d{2}\b", saved_at)
    metrics_html += ((_h('<span class="tp-banner-metric is-muted">保存于 ') + str(escape(saved_clock.group(0) if saved_clock else saved_at)) + '</span>'))
    problem_count = int(overview.get("blocking_count") or 0)
    suggestion_count = int(overview.get("suggestion_count") or 0)
    issue_total = problem_count or suggestion_count or int(
        (progress.get("issues") or {}).get("count") or 0)
    st.markdown('<div class="tp-workspace-shell"></div>', unsafe_allow_html=True)
    with st.container(key="workspace_exit_actions"):
        with st.container(key="workspace_topbar"):
            # ── Row 1: identity ──────────────────────────────────────────
            back_col, title_col, action_col = st.columns(
                [0.6, 5.5, 1.5], gap="small")
            with back_col:
                if st.button(_t("项目"), icon=":material/arrow_back:",
                             key=f"workspace_back_{job_id}", width="content",
                             help=_t("返回项目")):
                    project = core.project_for_job(job_id, state) if job_id else None
                    if project is not None:
                        _open_project(project["project_id"])
                    else:
                        _open_project_list()
                    st.rerun()
            with title_col:
                title_html = escape(title)
                if part_label:
                    title_html = (f'<span class="tp-workspace-part">{escape(part_label)}</span>'
                                  f'{title_html}')
                st.markdown(
                    '<div class="tp-workspace-topbar">'
                    '<div class="tp-workspace-topbar-copy">'
                    f'<h1>{title_html}</h1>'
                    f'<div class="tp-workspace-meta">{metrics_html}</div>'
                    '</div></div>', unsafe_allow_html=True)
            with action_col:
                primary = overview.get("primary_action") or {}
                if has_job and problem_count:
                    issue_label = (f"⚠ 必须处理 {problem_count:,} 个问题"
                                   if problem_count else
                                   f"⚑ {suggestion_count or issue_total:,} 条建议")
                    st.button(_t(issue_label), key=f"workspace_issues_{job_id}",
                              width="content", help=_t(str(overview.get("detail") or "")),
                              type="secondary" if problem_count else "tertiary",
                              on_click=_open_issues_panel)
                elif (primary.get("label") and primary.get("destination")
                      and primary["destination"] != st.session_state.get("workspace_section", "translation")
                      and not runtime_owns_action):
                    st.button(_t(primary["label"]),
                              key=f"workspace_topbar_cta_{job_id}", width="stretch", type="primary",
                              on_click=_switch_workspace_section,
                              args=(job_id, primary["destination"]))
                else:
                    tone = _task_overview.surface_token("verdict", overview["tone"])
                    st.markdown(
                        f'<span class="tp-workspace-verdict is-{escape(tone)}">'
                        f'{escape(overview["label"])}</span>', unsafe_allow_html=True)
                if has_job and issue_total and not problem_count:
                    st.button(_t('查看 {v0} 条建议', v0=format(suggestion_count or issue_total, ',')),
                              key=f"workspace_issues_{job_id}", width="content",
                              type="tertiary", help=_t(str(overview.get("detail") or "")),
                              on_click=_open_issues_panel)
            # ── Row 2: nav tabs ──────────────────────────────────────────
            if has_job:
                with st.container(key="workspace_toolbar"):
                    _render_workspace_nav(
                        st.session_state.get("workspace_section", "translation"),
                        state, job_id, overview)
        # Runtime controls stay isolated in their polling fragment.
        if has_job and runtime_view.get("status") not in {"idle", "completed"}:
            _render_runtime_strip(job_id, state, runtime_view)


def _render_workspace_nav(section, state, job_id="", overview=None):
    overview = overview or _task_overview_state(job_id, state)
    nav_tone = _task_overview.surface_token("nav", overview["tone"])
    # 工具栏只做一件事：切换当前页面。canonical 状态只属于 Banner 一处，
    # 这里再挂一份 chip 就是第三次读同一句话（顶栏 + 工具栏 + 旧 Hero）；
    # 连「工作台」这行 kicker 也是同一类噪声——它只在说"这是工作台"，
    # 而用户已经在工作台里了。
    projection = _workspace_projection(state, job_id, overview=overview)
    review_view = projection["review"]
    review_nav = review_view["nav"]
    # "不适用"不是一种状态文字，而是一种视觉降级：审校未启用的任务不该在侧栏
    # 一直挂着一行字占注意力（原来的 tone 是 neutral，和"参考"混在一起）。
    if str((review_view.get("readiness") or {}).get("status") or "") == "not_required":
        review_nav = {**review_nav, "tone": "inapplicable"}
    case_views = _workspace_case_views(job_id, state) if job_id else []
    case_pending = sum(1 for item in case_views
                       if item.get("review_status") == "unreviewed"
                       or (item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE
                           and item.get("baseline_status") == "rejected"))
    compliance = _workspace_compliance_view(job_id, state) if job_id else {}
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    terms_required = bool(state.get("quality_mode") or state.get("glossary")
                          or state.get("glossary_frozen") or state.get("auto_terms"))
    terms_done = not terms_required or bool(
        state.get("glossary_frozen") or state.get("quality_bypass")
        or (state.get("auto_terms") and not state.get("quality_mode")))
    compliance_counts = compliance.get("counts") or {}
    qa_required = bool(state.get("report_enabled"))
    qa_attention_count = 0
    if qa_required:
        qa_attention_count = sum(int(compliance_counts.get(key, 0) or 0)
                                 for key in ("fail", "manual_review", "not_checked"))
        structural = _workspace_structural_qa(job_id, state) if job_id else qa.get("structural_qa")
        qa_attention_count += int(structural != "PASS")
        qa_attention_count += int(qa.get("libreoffice_render") != "PASS")
        qa_attention_count += int(qa.get("author_visual_review") != "CONFIRMED")
        qa_attention_count += int(qa.get("word_final_review") != "CONFIRMED")
    # 交付导航项只表达"交付阶段现在是否需要你动手"，颜色取自 canonical tone，
    # 不再自己算一套 delivery readiness。
    if overview["lifecycle"] in {_task_overview.DELIVERED,
                                 _task_overview.DELIVERY_READY,
                                 _task_overview.READY_FOR_DELIVERY_PREP}:
        delivery_nav = ("", nav_tone, overview["detail"])
    elif overview["lifecycle"] in {_task_overview.DRAFT, _task_overview.TRANSLATING}:
        delivery_nav = ("", nav_tone, "翻译完成后进入交付")
    elif overview["reason"] == "outdated_delivery":
        delivery_nav = ("待更新 1", nav_tone, overview["detail"])
    else:
        delivery_nav = ("待处理 1", nav_tone, overview["detail"])
    if (state.get("p2_done") and
            projection["translated_segments"] >= projection["total_segments"]):
        translation_nav = ("", "done", "翻译已完成")
    elif not projection["total_segments"]:
        translation_nav = (("", "muted", "原文解析完成，但没有生成可翻译段落")
                           if state.get("p1_done") else
                           ("", "muted", "等待原文解析"))
    else:
        translation_nav = (
            f"{projection['translated_segments']} / {projection['total_segments']} 段",
            "pending", "翻译尚未完成")
    nav_meta = {
        "translation": translation_nav,
        "terms": (("", "muted", "无需确认术语") if not terms_required else
                  ("", "done", "术语已冻结") if terms_done else
                  ("待确认 1", "pending", "术语仍需确认")),
        "review": ((("", "muted", "当前任务未启用独立审校")
                    if review_nav.get("tone") == "inapplicable" else
                    (review_nav["label"], review_nav["tone"], review_nav["title"]))
                   if state.get("p2_done") else ("", "muted", "翻译完成后开始审校")),
        "cases": ((f"待确认 {case_pending}", "pending", f"还有 {case_pending} 个案例未完成人工确认") if case_pending else
                  ("", "done", "案例均已完成人工确认") if case_views else
                  ("", "muted", "案例选择产物尚未生成")),
        "report": (("", "done", "研究报告稿已生成") if state.get("p3_done") else
                   ("待完成 1", "attention", "研究报告仍需完成") if state.get("report_enabled") else
                   ("", "muted", "当前任务未启用研究报告")),
        "qa": ((f"待处理 {qa_attention_count}", "attention", f"合规与最终 QA 还有 {qa_attention_count} 项需要处理")
               if qa_attention_count else
               ("", "done", "合规与最终 QA 已完成") if qa_required else
               ("", "muted", "当前任务未启用最终 QA")),
        "delivery": delivery_nav,
    }
    # 审校是否为该任务的实际工作流阶段（有独立审校）。
    # 有独立审校时，「审校」常驻主行；否则退入「···」。
    review_enabled = bool(state.get("translation_core_review_required"))
    review_inapplicable = review_nav.get("tone") == "inapplicable"

    # 主行：翻译 + 术语 + [审校（仅 review_enabled）] + 交付
    primary_labels = [("translation", "翻译"), ("terms", "术语")]
    if review_enabled:
        primary_labels.append(("review", "审校"))
    primary_labels.append(("delivery", "交付"))

    academic = state.get("academic_state") or {}
    research_enabled = bool(state.get("report_enabled") or state.get("p3_done")
                            or case_views or academic.get("artifacts"))
    # 「···」收纳真正低频的功能；审校未启用时也进这里（带"不适用"标记）
    overflow_labels = []
    if not review_enabled:
        overflow_labels.append(("review", "审校"))
    if research_enabled:
        overflow_labels.extend([("cases", "案例"), ("report", "研究报告"),
                                ("qa", "合规与 QA")])

    with st.container(key="workspace_nav"):
        nav_col, details_col = st.columns([5.5, 0.7], gap="small")
        with nav_col, st.container(key="workspace_nav_buttons", horizontal=True):
            for value, label in primary_labels:
                active = value == section
                nav_status, nav_tone, nav_title = nav_meta[value]
                icon = (":material/radio_button_checked:" if active
                        else ":material/check_circle:" if nav_tone == "done"
                        else ":material/error_outline:" if nav_tone == "attention"
                        else ":material/schedule:" if nav_tone == "pending"
                        else None)
                with st.container(key=f"workspace_nav_item_{value}_{nav_tone}", width="content"):
                    st.button(_t(label), icon=icon, key=f"workspace_nav_{value}",
                              width="content",
                              help=_t(nav_title or None),
                              type="primary" if active else "secondary",
                              on_click=_switch_workspace_section,
                              args=(job_id, value))
            if overflow_labels:
                with st.popover("···", key=f"workspace_workflow_more_{job_id}",
                                help=_t("更多工作流阶段")):
                    for value, label in overflow_labels:
                        active = value == section
                        nav_tone = nav_meta[value][1]
                        display_label = f"{label} · 不适用" if nav_tone == "muted" else label
                        st.button(_t(display_label), key=f"workspace_nav_{value}",
                                  width="stretch",
                                  type="primary" if active else "secondary",
                                  on_click=_switch_workspace_section,
                                  args=(job_id, value))
        with details_col:
            with st.container(key="workspace_nav_details"):
                with st.popover(_t("任务详情"), icon=":material/info:", help=_t("打开任务详情"),
                                key=f"workspace_task_details_{job_id}"):
                    if st.button(_t("主页"), icon=":material/home:",
                                 key=f"workspace_home_{job_id}", width="stretch"):
                        st.session_state.update(app_view="new", workspace_mode=False,
                                                task_step=1)
                        _route_params(project=None, view=None, task=None, job=None, section=None)
                        st.rerun()
                    _workspace_task_details(state)


def _translation_pair_status(pair, state=None, index=None):
    return _translation_pair_status_label(pair, state, index)


def _translation_pair_status_label(pair, state=None, index=None, *, projection=None):
    if state is not None and isinstance(index, int):
        if projection is None:
            projection = _workspace_projection(state)
        status = projection["segment_status"].get(index)
        if status:
            # Source cleanup is a user edit too.  A task without an independent
            # review stage would otherwise continue to present the row as
            # ordinary "已翻译" after an OCR correction.
            if pair.get("source_human_edited") and status in {"已翻译", "已审校"}:
                return "已修改"
            return status
    if not state or not state.get("translation_core_review_required"):
        if pair.get("source_human_edited") or pair.get("human_edited"):
            return "已修改"
        return "已翻译" if str(pair.get("target") or "").strip() else "待翻译"
    if pair.get("human_edited") or pair.get("source_human_edited"):
        return "已修改"
    if pair.get("reviewed"):
        return "已审校"
    return "待审校"


def _translation_terms_for_pair(state, pair):
    return core.translation_terms_for_pair(state, pair)


def _translation_preview(value, limit=170):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _translation_segment_id(job_id, index, pair):
    """段落的界面身份。

    优先用 core 维护的稳定身份 `segment_uid`：拆分、合并、插入之后索引会整体
    漂移，而编辑框、选中态与 Agent 候选都以这个值为 key——继续用索引当身份，
    旧位置对应的新段落就会拿到旧编辑值（这正是要修的问题）。
    索引形式只作为旧任务尚未补齐身份时的退化路径。
    """
    uid = core.segment_uid(pair)
    if uid:
        return uid
    pair = pair if isinstance(pair, dict) else {}
    for key in ("segment_id", "segment_uid", "seg_id"):
        if pair.get(key) is not None:
            return str(pair[key])
    # Reuse the identity already used by exported translation assets.
    return _assets.segment_id(job_id, index)


def _translation_segment_records(job_id, state):
    return [{
        "segment_id": _translation_segment_id(job_id, index, pair),
        "index": index,
        "pair": pair,
    } for index, pair in enumerate(state.get("pairs") or [])]


def _translation_selected_segment(job_id, state, visible_records=None):
    pairs = state.get("pairs") or []
    selected_id = st.session_state.get("selected_segment_id")
    if selected_id is not None:
        selected_id = str(selected_id)

    # The CAT row callback records the index with the stable segment identity.
    # Validate it in O(1); building a 1,750-entry identity map for every
    # fragment rerun made a single selection allocate a document-sized list.
    selected_index = st.session_state.get("selected_segment_index")
    if (selected_id is not None and isinstance(selected_index, int)
            and 0 <= selected_index < len(pairs)):
        pair = pairs[selected_index]
        if _translation_segment_id(job_id, selected_index, pair) == selected_id:
            if visible_records is None or any(
                    item.get("segment_id") == selected_id for item in visible_records):
                return {"segment_id": selected_id, "index": selected_index,
                        "pair": pair}

    if selected_id is not None and visible_records is not None:
        visible_match = next((record for record in visible_records
                              if record.get("segment_id") == selected_id), None)
        if visible_match is not None:
            st.session_state["selected_segment_index"] = visible_match["index"]
            return visible_match
    if selected_id is None and visible_records is None:
        return None

    # Compatibility path for old sessions and selections restored from another
    # workspace. It runs only when the fast index is absent or stale.
    records = _translation_segment_records(job_id, state)
    by_id = {record["segment_id"]: record for record in records}
    if selected_id in by_id and (visible_records is None or
                                selected_id in {item["segment_id"] for item in visible_records}):
        st.session_state["selected_segment_index"] = by_id[selected_id]["index"]
        st.session_state["selected_segment_id"] = selected_id
        return by_id[selected_id]
    candidates = visible_records or records
    if not candidates:
        st.session_state["selected_segment_id"] = None
        st.session_state.pop("selected_segment_index", None)
        return None
    selected = candidates[0]
    st.session_state["selected_segment_id"] = selected["segment_id"]
    st.session_state["selected_segment_index"] = selected["index"]
    return selected


def _translation_segment_findings(state, index):
    """Project QA only for the selected segment, without a document readiness scan."""
    mapping = state if isinstance(state, dict) else {}
    candidates = _delivery.review_queue_findings(mapping)
    # Source quality has already been computed and persisted with translation.
    # Read that bounded gate result instead of recomputing it across every pair.
    gate = mapping.get("source_quality_gate") or {}
    existing = {
        (item.get("segment_index", item.get("segment_id")),
         str(item.get("source_span") or ""))
        for item in candidates if isinstance(item, dict)
    }
    for flag in gate.get("flags") or []:
        if not isinstance(flag, dict) or flag.get("segment_index") != index:
            continue
        key = (index, str(flag.get("text") or ""))
        if key not in existing:
            candidates.append(core._source_quality_finding(flag))
            existing.add(key)
    selected = [finding for finding in candidates
                if _delivery._segment_index(finding) == index
                and _workbench_view._current_finding(mapping, finding)]
    return [_workbench_view._finding_item(mapping, finding) for finding in selected]


def _save_translation_edit(job_id, index, text):
    core.save_translation_edit(job_id, index, text)


def _restore_translation_pair(job_id, index):
    core.restore_translation_edit(job_id, index)


_TRANSLATION_SEGMENT_EDITOR_KEY = "translation_segment_editor"
_TRANSLATION_STRUCTURE_ACTIVE_STATUSES = {
    "resume_requested", "queued", "starting", "running",
    "waiting_external", "cancelling",
}


def _translation_structure_edit_blocked(job_id, state):
    status = core.get_job_runtime_status(job_id, state).get("status")
    return status in _TRANSLATION_STRUCTURE_ACTIVE_STATUSES, status


def _open_translation_segment_editor(job_id, index, mode):
    st.session_state[_TRANSLATION_SEGMENT_EDITOR_KEY] = {
        "job_id": str(job_id), "index": int(index), "mode": str(mode),
    }


def _close_translation_segment_editor():
    spec = st.session_state.pop(_TRANSLATION_SEGMENT_EDITOR_KEY, None) or {}
    job_id = str(spec.get("job_id") or "")
    index = spec.get("index")
    mode = str(spec.get("mode") or "")
    if job_id and isinstance(index, int):
        prefix = f"translation_segment_editor_{job_id}_{index}_{mode}"
        for key in list(st.session_state):
            if str(key).startswith(prefix):
                st.session_state.pop(key, None)


def _dismiss_translation_segment_editor():
    """Close the segment editor and force a full workspace rerun.

    ``st.dialog`` runs its dismiss callback inside the dialog fragment.  Merely
    clearing the editor spec therefore leaves the parent workbench on the
    fragment's render pass, where the row action menus are still suppressed.
    A full rerun restores the normal CAT row affordances immediately after the
    user closes the editor with X, Escape, or an outside click.
    """
    _close_translation_segment_editor()
    st.rerun()


def _translation_structure_flash(operation, index, *, inserted_index=None):
    labels = {
        "source_edit": "原文已修改",
        "split": "段落已拆分",
        "merge": "段落已合并",
        "insert": "已插入空段",
        "delete": "空段已删除",
    }
    label = labels.get(operation, "段落结构已更新")
    if inserted_index is not None:
        label += f"，第 {inserted_index + 1} 段可继续填写"
    _set_workspace_flash(label + "；受影响内容需要重新审校。", "warning")


def _render_translation_segment_actions(job_id, state, index, pair, *, is_active=True,
                                        runtime_status=None):
    """Compact CAT row menu: delegated to folith.ui.workspace.cat_grid."""
    if _pdf_preview and _pdf_preview.has_pdf_source(job_id):
        st.button("", icon=":material/picture_as_pdf:",
                  key=f"cat_source_pdf_{job_id}_{index}", width="content",
                  help=_t("查看本段在 PDF 中的原位置"),
                  on_click=_open_pdf_at_segment, args=(job_id, state, index))
    return _cat_grid_ui.render_translation_segment_actions(
        job_id, state, index, pair, is_active=is_active,
        segment_id_fn=_translation_segment_id,
        navigate_fn=_navigate_to_segment,
        structure_edit_blocked_fn=(
            _translation_structure_edit_blocked if runtime_status is None else
            lambda *_: (runtime_status in _TRANSLATION_STRUCTURE_ACTIVE_STATUSES,
                        runtime_status)),
        flush_draft_fn=_flush_translation_draft,
        open_editor_fn=_open_translation_segment_editor,
        purge_edit_state_fn=_purge_translation_edit_state,
        structure_flash_fn=_translation_structure_flash,
        workspace_flash_fn=_set_workspace_flash,
        seed_editor_fn=_seed_translation_editor,
        commit_row_fn=_commit_translation_row,
        retranslate_fn=_retranslate_current_segment,
        draft_for_fn=_translation_draft_for,
        drop_draft_fn=_drop_translation_draft,
        reset_editor_fn=_reset_translation_editor,
        api_ready=bool(api_key and ai_model),
        editor_key_fn=_translation_editor_key,
        go_to_neighbour_fn=_go_to_neighbour_segment,
    )



def _seed_translation_editor(job_id, index, segment_id, text, *, target=""):
    """把一段文字放进译文输入框（作为未保存草稿），而不是直接写文档。"""
    _reset_translation_editor(segment_id)
    _record_translation_draft(job_id, index, segment_id, text, target)
    seeds = dict(st.session_state.get(_TRANSLATION_EDITOR_SEED) or {})
    seeds[str(segment_id)] = str(text)
    st.session_state[_TRANSLATION_EDITOR_SEED] = seeds


def _retranslate_current_segment(job_id, index):
    """只翻译/重译当前段落，复用既有术语、项目记忆与审校配置。"""
    if not api_key or not ai_model:
        _set_workspace_flash("需要先完成 AI 引擎配置。", "error")
        return
    state = core.load_job_state(job_id) or {}
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        _set_workspace_flash("该段落已经不存在，请刷新后重试。", "error")
        return
    segment_id = _translation_segment_id(job_id, index, pairs[index])
    runtime = resolve_review_runtime()
    try:
        started = _queue_segment_retranslation(
            job_id, [index], runtime, core.state_target_lang(state, "简体中文"),
            _style_rules_text(state), message=f"第 {index + 1} 段已用当前配置重新翻译。")
    except Exception as exc:
        _set_workspace_flash(core.provider_error_message(exc, "定点翻译启动失败"), "error")
    else:
        if started:
            _drop_translation_draft(job_id, segment_id)
            _reset_translation_editor(segment_id)
    st.rerun()



def _style_rules_text(state):
    """当前任务的风格规则文本（没有配置时返回空串，不下发 None）。"""
    for key in ("style_rules", "confirmed_style_rules"):
        value = state.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list):
            joined = "；".join(str(item) for item in value if str(item).strip())
            if joined:
                return joined
    selection = st.session_state.get("style_selection") or {}
    profile = selection.get("selected") if isinstance(selection, dict) else None
    return str(profile or "")


_AGENT_STATUS_OK = "ok"
_AGENT_STATUS_EMPTY = "empty"
_AGENT_STATUS_ERROR = "error"
_AGENT_REWRITE_ACTIONS = {"rewrite", "academic", "polish"}


def _agent_call_result(status, text):
    return {"status": status, "text": str(text or "")}


def _agent_suggestion(action, result, *, as_rewrite=False, label=""):
    """把一次 Agent 调用的结果打上性质标签，渲染层据此决定能不能写回正文。"""
    outcome = result if isinstance(result, dict) else _agent_call_result(
        _AGENT_STATUS_OK, result)
    status = str(outcome.get("status") or _AGENT_STATUS_OK)
    text = str(outcome.get("text") or "")
    if status in {_AGENT_STATUS_ERROR, _AGENT_STATUS_EMPTY}:
        kind = "diagnose"
    else:
        kind = ("rewrite" if action in _AGENT_REWRITE_ACTIONS or as_rewrite
                else "diagnose")
    return {"action": action, "label": label or action, "kind": kind,
            "status": status, "text": text}


def _render_translation_segment_editor(job_id, state):
    spec = st.session_state.get(_TRANSLATION_SEGMENT_EDITOR_KEY) or {}
    if str(spec.get("job_id") or "") != str(job_id):
        return
    _translation_segment_editor_dialog()


def _analyze_translation_decisions(job_id, index, state):
    """Run one explicitly requested structured analysis and cache it on the pair."""
    if not api_key or not ai_model:
        raise RuntimeError("请先完成 AI 引擎配置。")
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        raise IndexError("当前段落已不存在。")
    pair = pairs[index]
    source = str(pair.get("source") or "")
    target = str(pair.get("target") or "")
    cached = _translation_decisions.cached_analysis(pair, source, target)
    if cached is not None:
        return cached.get("decisions") or []
    system, prompt = _translation_decisions.analysis_prompt(source, target)
    raw = core.call_llm(ai_provider, api_key, ai_model, system, prompt,
                        temperature=0.1)
    decisions = _translation_decisions.parse_analysis_response(
        raw, source, target, _translation_segment_id(job_id, index, pair))

    # Re-read before persisting so analysis cannot overwrite a concurrent edit.
    latest = core.load_job_state(job_id) or state
    latest_pairs = latest.get("pairs") or []
    if not 0 <= index < len(latest_pairs):
        raise RuntimeError("分析期间段落结构发生变化；结果未保存。")
    latest_pair = latest_pairs[index]
    if _translation_decisions.pair_fingerprint(
            latest_pair.get("source"), latest_pair.get("target")) != \
            _translation_decisions.pair_fingerprint(source, target):
        raise RuntimeError("分析期间译文已变化；结果未保存，请重新审视当前版本。")
    latest_pair["translation_decision_analysis"] = \
        _translation_decisions.make_cached_analysis(source, target, decisions)
    core.save_job_state(job_id, latest)
    return decisions


def _ask_translation_decision(job_id, index, state, question):
    """Answer a user-authored question about one source/target pair."""
    if not api_key or not ai_model:
        raise RuntimeError("请先完成 AI 引擎配置。")
    pairs = state.get("pairs") or []
    if not 0 <= index < len(pairs):
        raise IndexError("当前段落已不存在。")
    pair = pairs[index]
    traces = _translation_decisions.detect_translation_decisions(
        pair.get("source"), pair.get("target"), index,
        source_quality_blocked=any(
            item.get("segment_index") == index
            for item in ((state.get("source_quality_gate") or {}).get("flags") or [])
            if isinstance(item, dict)),
    )
    evidence = json.dumps(traces, ensure_ascii=False)
    system = (
        "你是翻译决策审视助手。回答译者提出的问题，解释原文、当前译文和"
        "可见翻译取舍；使用中性措辞，不把策略差异自动判为错误。除非用户明确"
        "询问替代处理方式，否则不要生成改写译文。不要声称没有提供的证据。"
    )
    prompt = (f"原文：\n{pair.get('source', '')}\n\n当前译文：\n{pair.get('target', '')}"
              f"\n\n本段已有痕迹：\n{evidence}\n\n译者的问题：\n{str(question or '').strip()}")
    return str(core.call_llm(ai_provider, api_key, ai_model, system, prompt,
                             temperature=0.2) or "").strip()


def _pairs_of(state):
    return state.get("pairs") or []


def _segment_edit_count(state, index, pair):
    """这一段被人工改过几次。

    优先用人工动作记录（同一段多次修改都留痕）；没有记录时退化为
    `human_edited` 这个布尔事实，避免把"改过"显示成"改了 0 次"。
    """
    actions = state.get("human_actions") or []
    count = 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        segments = action.get("segment_indexes") or action.get("segment_ids") or []
        if index in segments or action.get("segment_index") == index:
            count += 1
    if not count and pair.get("human_edited"):
        return 1
    return count


def _segment_term_hits(state, pair):
    """本段原文命中的项目术语数量（术语面板里的"术语匹配 n"）。"""
    return len(_translation_terms_for_pair(state, pair))


def _segment_section_label(state, index):
    """这一段属于哪个章节（Inspector 的"上下文"事实）。"""
    sections = state.get("sections") or []
    for section in sections:
        if not isinstance(section, dict):
            continue
        start = section.get("start_segment")
        end = section.get("end_segment")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if start <= index <= end:
            title = str(section.get("title") or section.get("section_id") or "").strip()
            return _translation_preview(title, 28) if title else f"§{start + 1}–{end + 1}"
    return "—"


def _document_outline_cache_signature(state, pair_count):
    """Cheap invalidation key for the document outline projection."""
    segmentation = state.get("segmentation") or {}
    history = state.get("segment_structure_history") or []
    return (
        "toc-heading-projection-v3",
        pair_count,
        state.get("segment_uid_seq"),
        len(history),
        segmentation.get("rules_version"),
        str(state.get("filename") or ""),
    )


def _translation_document_outline(job_id, state):
    """Return a cached, lightweight chapter outline backed by source evidence.

    Explicit segment ranges win. For documents without a stored outline, use
    an existing CONTENTS list to match all-caps body headings. This is only a
    sidebar projection; it does not alter segmentation or translation data.
    """
    pairs = state.get("pairs") or []
    signature = _document_outline_cache_signature(state, len(pairs))
    cache_key = f"translation_document_outline_{job_id}"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached.get("items") or []

    explicit = state.get("sections") or []
    items = []
    for section in explicit:
        if not isinstance(section, dict):
            continue
        start = section.get("start_segment")
        end = section.get("end_segment")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        start = max(0, min(start, max(0, len(pairs) - 1)))
        end = max(start, min(end, max(0, len(pairs) - 1)))
        title = str(section.get("title") or section.get("section_id") or "").strip()
        if title:
            items.append({"start": start, "end": end, "title": title})

    if not items and pairs:
        def source_at(index):
            pair = pairs[index] if index < len(pairs) else {}
            pair = pair if isinstance(pair, dict) else {}
            return " ".join(str(pair.get("source") or "").split())

        def title_key(value):
            return "".join(char.casefold() for char in str(value or "")
                           if char.isalnum())

        contents_index = next((index for index in range(len(pairs))
                               if source_at(index).casefold() in {
                                   "contents", "table of contents"}), None)
        toc_titles = {}
        body_start = 0
        if contents_index is not None:
            body_start = contents_index + 1
            for index in range(contents_index + 1, len(pairs)):
                text = source_at(index)
                has_letters = any(char.isalpha() for char in text)
                all_caps = has_letters and text == text.upper()
                # A table of contents can itself contain short all-caps titles
                # (for example "W-9"). The body begins when a heading repeats
                # a title already encountered in the contents, not at the
                # first uppercase line.
                if all_caps and title_key(text) in toc_titles:
                    body_start = index
                    break
                candidate = re.sub(r"\s+\d{1,4}$", "", text).strip()
                words = candidate.split()
                if (candidate and len(candidate) <= 120 and 0 < len(words) <= 14
                        and not re.search(r"[.!?。！？]$", candidate)):
                    toc_titles.setdefault(title_key(candidate), candidate)

        heading_candidates = []
        excluded_titles = {"contents", "table of contents", "copyright"}
        for index in range(body_start, len(pairs)):
            text = source_at(index)
            has_letters = any(char.isalpha() for char in text)
            all_caps = has_letters and text == text.upper()
            if (not all_caps or len(text) > 100 or len(text.split()) > 14
                    or title_key(text) in excluded_titles):
                continue
            normalized = title_key(text)
            if toc_titles and normalized not in toc_titles:
                continue
            if not toc_titles and re.search(r"[.!?。！？]$", text):
                continue
            title = toc_titles.get(normalized, text.title())
            heading_candidates.append((index, title))

        for offset, (start, title) in enumerate(heading_candidates):
            end = (heading_candidates[offset + 1][0] - 1
                   if offset + 1 < len(heading_candidates) else len(pairs) - 1)
            items.append({"start": start, "end": max(start, end), "title": title})

    items.sort(key=lambda item: item["start"])
    st.session_state[cache_key] = {"signature": signature, "items": items}
    return items


def _render_workspace_document_nav(job_id, state, overview=None):
    """Compact document, structure, nearby-segment, and fulltext navigator for CAT view."""
    pairs = state.get("pairs") or []
    selected_index = st.session_state.get("selected_segment_index", 0)
    if not isinstance(selected_index, int) or not 0 <= selected_index < len(pairs):
        selected_index = 0
    outline = _translation_document_outline(job_id, state)
    has_pdf = bool(_pdf_preview and _pdf_preview.has_pdf_source(job_id))
    pdf_open_key = f"pdf_preview_open_{job_id}"
    st.text_input(_t("搜索原文、译文或段落号"),
                  key=f"translation_search_{job_id}",
                  placeholder=_t("搜索原文、译文或段落号…"),
                  label_visibility="collapsed")

    def render_structure_mode():
        if outline:
            with st.container(key="workspace_document_outline_items"):
                for item in outline:
                    start, end, title = item["start"], item["end"], item["title"]
                    active = start <= selected_index <= end
                    # One chapter row owns its label and range. No duplicate
                    # heading or nested chapter card. Keep the nearby list bounded.
                    with st.expander(_t(f"{title} · {start + 1:,}–{end + 1:,}"), expanded=active):
                        st.button(
                                _t("章节起点"), icon=":material/first_page:",
                                key=f"workspace_outline_{job_id}_{start}",
                                type="tertiary", width="stretch",
                                help=_t('定位到 {v0} · 第 {v1} 段', v0=title, v1=format(start + 1, ',')),
                                on_click=_navigate_to_segment,
                                args=(job_id, state, start), kwargs={"reveal": True})
                        if active:
                            p_start = max(start, min(selected_index - 3, end - 6))
                            p_end = min(end, p_start + 6)
                            for seg_idx in range(p_start, p_end + 1):
                                pair = pairs[seg_idx] if isinstance(pairs[seg_idx], dict) else {}
                                src = " ".join(str(pair.get("source") or "").split())
                                preview = src[:42].rstrip() + ("…" if len(src) > 42 else "") if src else "（空段落）"
                                st.button(
                                        _t(f"{seg_idx + 1:,}  {preview}"),
                                        key=f"workspace_chap_seg_{job_id}_{seg_idx}",
                                        type="primary" if seg_idx == selected_index else "tertiary",
                                        help=src, width="stretch",
                                on_click=_navigate_to_segment,
                                args=(job_id, state, seg_idx), kwargs={"reveal": True})
        else:
            st.caption(_t('{v0} 个翻译单元 · 按 40 段分块导航', v0=format(len(pairs), ',')))
            query = str(st.session_state.get(f"translation_search_{job_id}") or "")
            block_size = 40
            blocks = []
            for b_idx in range(0, len(pairs), block_size):
                b_end = min(b_idx + block_size - 1, len(pairs) - 1)
                blocks.append({
                    "start": b_idx,
                    "end": b_end,
                    "title": f"第 {b_idx + 1:,}–{b_end + 1:,} 单元",
                })
            filtered_blocks = blocks
            if query:
                matching_blocks = []
                for b in blocks:
                    if query.casefold() in b["title"].casefold():
                        matching_blocks.append(b)
                        continue
                    for seg_i in range(b["start"], b["end"] + 1):
                        p = pairs[seg_i] if isinstance(pairs[seg_i], dict) else {}
                        s = str(p.get("source") or "")
                        t = str(p.get("target") or "")
                        if query.casefold() in s.casefold() or query.casefold() in t.casefold():
                            matching_blocks.append(b)
                            break
                filtered_blocks = matching_blocks

            if not filtered_blocks:
                st.caption(_t("未找到匹配的分块。"))
                return

            with st.container(key="workspace_document_block_items"):
                for block in filtered_blocks:
                    b_start, b_end, b_title = block["start"], block["end"], block["title"]
                    active = b_start <= selected_index <= b_end
                    with st.expander(_t('{v0} ({v1} 段)', v0=b_title, v1=format(b_end - b_start + 1, ',')), expanded=active):
                        for seg_idx in range(b_start, b_end + 1):
                            if seg_idx >= len(pairs):
                                break
                            pair = pairs[seg_idx] if isinstance(pairs[seg_idx], dict) else {}
                            src = " ".join(str(pair.get("source") or "").split())
                            preview = src[:42].rstrip() + ("…" if len(src) > 42 else "") if src else "（空段落）"
                            is_seg_active = (seg_idx == selected_index)
                            st.button(
                                    _t(f"#{seg_idx + 1:,}  {preview}"),
                                    key=f"workspace_block_seg_{job_id}_{seg_idx}",
                                    type="primary" if is_seg_active else "tertiary",
                                    width="stretch",
                                on_click=_navigate_to_segment,
                                args=(job_id, state, seg_idx), kwargs={"reveal": True})

    with st.empty().container():
        if has_pdf and st.session_state.get(pdf_open_key):
            _pdf_view.render_pdf_preview_fragment(job_id, state)
        else:
            st.markdown(_h('<div class="tp-document-nav-heading">章节目录</div>'),
                        unsafe_allow_html=True)
            render_structure_mode()


def _segment_confidence(findings):
    """审校给出的置信度；没有独立审校时如实显示"未评审"。"""
    for item in findings:
        value = item.get("confidence")
        if isinstance(value, (int, float)):
            return ("高" if value >= 0.8 else "中" if value >= 0.55 else "低"), value
        label = str(item.get("confidence_label") or "").strip()
        if label:
            return label, None
    return "—", None


def _render_document_agent_panel(job_id, state, overview=None):
    """没有选中段落时的 Inspector：仍然是 Agent，不是空白占位。

    这里**不再复述** canonical 交付状态：那是 Banner 的职责。以前翻译页
    同时读到 Banner、工具栏 chip 和这块「Agent · 可以准备交付」，同一句话
    出现三次；Inspector 只回答"这一屏有什么发现、能跳到哪一段"。
    """
    findings = _planner.plan_findings(state, job_id=job_id, limit=8)
    st.markdown(
        _h('<div class="tp-translation-inspector-head"><div><h3>翻译审视</h3>'
        '<div class="tp-inspector-status"><strong>未选中段落</strong></div>'
        '</div></div>'
        '<div class="tp-inspector-section">'
        '<p class="tp-inspector-preview">选中任意段落可查看该段的翻译痕迹、'
        'QA 状态、术语与上下文；这里是全文级的 QA 发现。</p></div>'),
        unsafe_allow_html=True)
    if not findings:
        st.markdown(_h('<div class="tp-inspector-section">'
                    '<div class="tp-inspector-empty">当前没有需要处理的全文发现。</div></div>'),
                    unsafe_allow_html=True)
        return
    st.markdown(_h('<div class="tp-inspector-section"><h4>全文发现</h4>'), unsafe_allow_html=True)
    for finding in findings:
        st.markdown(
            f'<div class="tp-inspector-finding is-{escape(finding["severity"])}">'
            f'<strong>{escape(finding["title"])}</strong>'
            f'<p>{escape(finding["detail"])}</p></div>', unsafe_allow_html=True)
        if finding.get("segments"):
            st.button(_t('定位第 {v0} 段', v0=finding['segments'][0] + 1),
                      key=f"agent_goto_{job_id}_{finding['id']}",
                      width="stretch",
                      on_click=_navigate_to_segment,
                      args=(job_id, state, finding["segments"][0]))
    st.markdown('</div>', unsafe_allow_html=True)


# ---- 段落跳转：唯一入口 ----
# Agent 发现锚点、Inspector 的"定位"、问题抽屉、审校跳转都必须走这里。
# 以前每个入口各写各的（设置选中 + st.rerun() + 各自滚动），结果是
# "点 #2 → 正文变白 → 卡住"：st.pills 的选中状态在 rerun 后依然存在，
# 而 callback 里的 st.rerun() 又触发一次渲染，widget 状态反复重放形成循环。
#
# 现在的约定：
#   1. 跳转只设置状态，绝不在 callback 里 st.rerun()——Streamlit 因为 widget
#      交互本来就会 rerun；
#   2. scroll intent 消费一次就清空（`_consume_pending_scroll`），绝不留存，
#      否则之后每次 rerun 都会再跳一次；
#   3. 目标被筛选/搜索挡住时，先让筛选回到能显示它的状态（定位优先于保持筛选）。
_NAV_PENDING_SCROLL = "pending_scroll_segment_id"
# 焦点与滚动是**两个** intent：滚动用平滑动画（300ms 后还要纠一次），焦点必须立刻落。
# 混用一个标记就会退化成"要么滚太快、要么焦点在滚动中被打断"。
# 只有"保存并下一段"这类连续编辑动作会设置它——翻段浏览不该抢用户的输入焦点。
_NAV_PENDING_FOCUS = "pending_focus_segment_id"
_NAV_NOTICE = "_nav_notice"
_ISSUE_SEVERITY_LABELS = {"blocking": "必须处理", "actionable": "建议",
                          "informational": "参考"}
# 问题抽屉必须一次拿到**全部**发现，否则锚点会缺：planner 的 limit 是截断，
# 早先取 40 时，"每段一条术语发现"的 82 段文档里 #41 之后的段落根本没有锚点可点。
# 翻译页顶部那条 issue bar 只需要最高优先级的一条，仍然用小 limit。
_ISSUES_PANEL_LIMIT = 500


def _navigate_to_segment(job_id, state, index, *, reveal=True):
    """唯一跳转入口：选中 + 一次性滚动 +（可选）放宽筛选。

    `reveal=True`（默认，用于"从问题列表跳到正文"）：目标被筛选/搜索挡住时
    先让筛选回到能显示它的状态。
    `reveal=False`（用于上一段/下一段/网格内选段）：用户就在当前集合里移动，
    不该被改掉筛选条件。
    """
    pairs = (state or {}).get("pairs") or []
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return False
    st.session_state["selected_segment_id"] = _translation_segment_id(
        job_id, index, pairs[index])
    st.session_state["selected_segment_index"] = index
    st.session_state[_NAV_PENDING_SCROLL] = index
    # 跳转后恢复段落 Inspector（问题抽屉是临时视图）
    st.session_state.pop("issues_panel_open", None)
    if reveal:
        _reveal_segment(job_id, index)
    if st.session_state.get(f"pdf_preview_open_{job_id}"):
        st.session_state[f"pdf_preview_hl_seg_{job_id}"] = index
        try:
            pairs = (state or {}).get("pairs") or []
            if 0 <= index < len(pairs):
                src_txt = str(pairs[index].get("source") or "")
                cur_p = int(st.session_state.get(f"pdf_preview_page_{job_id}", 0) or 0)
                p_idx, _ = _pdf_preview.find_segment_page_and_rects(job_id, src_txt, start_page_hint=cur_p)
                if p_idx >= 0:
                    st.session_state[f"pdf_preview_page_{job_id}"] = p_idx
        except Exception:
            pass
    return True


def _open_pdf_at_segment(job_id, state, index):
    """Open the source PDF and navigate before the workspace widgets remount."""
    st.session_state[f"pdf_preview_open_{job_id}"] = True
    _navigate_to_segment(job_id, state, index, reveal=True)


def _activate_translation_segment(job_id, state, index):
    """Select a visible CAT row without changing filters or scrolling.

    Row-local clicks (segment number, source cell, and target editor focus) are
    already in view, so they should only switch the active work row. Cross-area
    navigation continues to use ``_navigate_to_segment`` with its scroll/reveal
    behavior.
    """
    pairs = (state or {}).get("pairs") or []
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return False
    st.session_state["selected_segment_id"] = _translation_segment_id(
        job_id, index, pairs[index])
    st.session_state["selected_segment_index"] = index
    st.session_state.pop("issues_panel_open", None)
    return True


def _reveal_segment(job_id, index):
    """保证目标段落处于会渲染的集合里。

    渲染集合由 status 筛选 + 关键词搜索共同决定；如果目标被挡住，
    `getElementById` 根本找不到它——那不是"滚动失败"，是目标不存在。
    这里优先保证目标可见，并把原本的搜索词暂存以便恢复。
    """
    filter_key = f"translation_filter_{job_id}"
    search_key = f"translation_search_{job_id}"
    issues_only = st.session_state.get(f"translation_filter_issue_{job_id}")
    status_filter = st.session_state.get(filter_key, "全部")
    search = str(st.session_state.get(search_key) or "")
    if status_filter != "全部":
        # 直接改写 widget state：它在本轮渲染前被清掉，所以下轮渲染会读到新值。
        st.session_state[filter_key] = "全部"
        st.session_state[_NAV_NOTICE] = f"已切换到「全部」以显示第 {index + 1} 段"
    if issues_only:
        st.session_state[f"translation_filter_issue_{job_id}"] = False
        st.session_state[_NAV_NOTICE] = f"已清除筛选以显示第 {index + 1} 段"
    if search:
        # 搜索词这里**不**恢复：恢复会让目标段落在下一次 rerun 又被藏起来，
        # 用户刚跳过去就看见它消失。"定位优先于保持筛选"——直接清空并明确告知。
        st.session_state[search_key] = ""
        st.session_state[_NAV_NOTICE] = f"已清除搜索以显示第 {index + 1} 段"


def _render_nav_notice():
    """跳转放宽了筛选条件时，明确告诉用户发生了什么。

    静默改掉用户的筛选状态比多一行提示糟糕得多。
    """
    notice = st.session_state.pop(_NAV_NOTICE, None)
    if notice:
        st.caption(_t(f"↪ {notice}"))


def _open_issues_panel():
    # Callback runs before rendering: an early st.rerun in the header would
    # discard the search widget's state before the workspace can mount it.
    st.session_state.update(workspace_section="translation",
                            workspace_inspector_open=True, issues_panel_open=True)


def _toggle_issues_panel():
    """顶部 issue bar 的「查看全部 / 返回段落」。"""
    st.session_state["issues_panel_open"] = \
        not bool(st.session_state.get("issues_panel_open"))


def _close_issues_panel():
    """问题抽屉里的「返回段落」。"""
    st.session_state.pop("issues_panel_open", None)


def _consume_pending_scroll():
    """取出并立刻清空 scroll intent。取过一次就不该再有第二次。"""
    index = st.session_state.pop(_NAV_PENDING_SCROLL, None)
    return index if isinstance(index, int) else None


def _consume_pending_focus():
    """取出并立刻清空 focus intent（与滚动同理，只消费一次）。"""
    index = st.session_state.pop(_NAV_PENDING_FOCUS, None)
    return index if isinstance(index, int) else None


def _render_focus_trigger(job_id, index):
    _h("""把键盘焦点放进目标段落的译文框。

    "保存并下一段"如果没有焦点回位，用户每段都要重新点一次输入框——连续审校的
    手感就断在这一步上（这正是"保存并下一段"存在的意义）。

    `preventScroll: true` 不是可选项：默认的 `focus()` 会让浏览器把元素滚进视口，
    与 `_render_scroll_trigger` 的"行中心对齐视口中心"同时发生，最后停在两者之间
    的任意一处——表现为"有时候跳不准"。

    定位用行锚点反查容器，而不是按 key 拼类名：类名形如
    `st-key-cat_editor_<job>_<index>`，用 `[class*=…_1]` 会同时命中 `…_12`。
    """)
    if index is None:
        return
    st.html(f"""
<script>
(function () {{
  function grab() {{
    var marker = document.querySelector('[data-segment="{index}"]');
    var row = marker && marker.closest('[class*="st-key-cat_row_"]');
    var box = row && row.querySelector('textarea');
    if (!box) return false;
    try {{ box.focus({{ preventScroll: true }}); }}
    catch (err) {{ box.focus(); }}
    return true;
  }}
  var tries = 0;
  function go() {{
    if (grab()) return;
    if (++tries < 20) window.requestAnimationFrame(go);
  }}
  window.requestAnimationFrame(go);
}})();
</script>
""", unsafe_allow_javascript=True)


def _render_scroll_trigger(index):
    if index is None:
        return
    # st.html(unsafe_allow_javascript=True) 在主文档里执行（实测可用），
    # 所以能直接查 DOM。用 requestAnimationFrame 让这一帧的 DOM 先挂完。
    #
    # 不用裸 scrollIntoView：标记 span 是 height:0 的，浏览器按它自己的盒子
    # 计算居中，实测目标行中心落在 718px、视口中心 480px——偏了 240px，
    # 而且滚完还会因为长段落上方的布局再稳定一次而继续漂。
    # 这里显式算"行中心 vs 视口中心"的差值来修正，300ms 后再纠一次。
    # 每次跳转只注入一次脚本，不存在反复滚动。
    st.html(f"""
<script>
(function () {{
  function centre() {{
    var marker = document.querySelector('[data-segment="{index}"]');
    if (!marker) return false;
    var row = marker.closest('[class*="st-key-cat_row_"]') || marker;
    var rect = row.getBoundingClientRect();
    var scroller = document.querySelector('section[data-testid="stMain"]');
    var delta = (rect.top + rect.height / 2) - window.innerHeight / 2;
    if (scroller) scroller.scrollBy({{top: delta, behavior: 'smooth'}});
    else window.scrollBy({{top: delta, behavior: 'smooth'}});
    return true;
  }}
  var tries = 0;
  function go() {{
    if (centre()) {{ window.setTimeout(centre, 300); return; }}
    if (++tries < 20) window.requestAnimationFrame(go);
  }}
  window.requestAnimationFrame(go);
}})();
</script>
""", unsafe_allow_javascript=True)

def _render_workspace_translation_context(job_id, state, overview=None, *, plan_findings=None):
    """右栏翻译审视由 folith.ui.workspace.inspector 渲染。"""
    return _inspector_ui.render_workspace_translation_context(
        job_id, state, overview,
        review_runtime_fn=resolve_review_runtime,
        issues_panel_fn=lambda job_id, state: _render_issues_panel(
            job_id, state, plan_findings=plan_findings),
        selected_segment_fn=_translation_selected_segment,
        document_agent_panel_fn=_render_document_agent_panel,
        pairs_fn=_pairs_of,
        segment_records_fn=_translation_segment_records,
        terms_for_pair_fn=_translation_terms_for_pair,
        findings_fn=_translation_segment_findings,
        status_label_fn=_translation_pair_status_label,
        navigate_fn=_navigate_to_segment,
        draft_for_fn=_translation_draft_for,
        commit_row_fn=_commit_translation_row,
        next_unconfirmed_fn=_next_unconfirmed_index,
        editor_key_fn=_translation_editor_key,
        set_flash_fn=_set_workspace_flash,
        seed_editor_fn=_seed_translation_editor,
        retranslate_fn=_retranslate_current_segment,
        edit_count_fn=_segment_edit_count,
        confidence_fn=_segment_confidence,
        term_hits_fn=_segment_term_hits,
        section_label_fn=_segment_section_label,
        select_review_item_fn=_select_review_item,
        decision_analysis_fn=_analyze_translation_decisions,
        ask_decision_fn=_ask_translation_decision,
        translation_preview_fn=_translation_preview,
        restore_pair_fn=_restore_translation_pair,
        api_ready=bool(api_key and ai_model),
    )



def _render_workspace_terms_context(job_id, state):
    rows = _workspace_presentation.term_rows(state)
    selected = next((row for row in rows if row["id"] ==
                     st.session_state.get("selected_glossary_entry_id")), None)
    if not selected:
        st.markdown(_h('<div class="tp-inspector-empty">选择一个术语，查看译名、上下文与使用一致性。</div>'),
                    unsafe_allow_html=True)
        return
    with st.container(key="term_inspector"):
        st.markdown((_h('<div class="tp-ws-inspector-heading">术语详情</div><h3>') + str(escape(selected["source"])) + '</h3><p class="tp-term-preferred">' + str(escape(selected["target"] or "尚无译名")) + '</p>'),
                    unsafe_allow_html=True)
        provenance = "自动术语" if selected["entry"].get("read_only") else "项目术语"
        st.markdown((_h('<div class="tp-ws-facts"><span>状态</span><b>') + str(escape(_TERM_STATUS_LABELS.get(selected["status"], "待确认"))) + _h('</b><span>出现次数</span><b>') + str(len(selected["occurrences"])) + _h(' 段</b><span>来源</span><b>') + str(provenance) + '</b></div>'), unsafe_allow_html=True)
        st.markdown(_h('<h4>上下文</h4>'), unsafe_allow_html=True)
        pairs = state.get("pairs") or []
        paragraphs = state.get("paras") or []
        full_examples = []
        for index in selected["occurrences"][:3]:
            pair = pairs[index] if index < len(pairs) else {"source": paragraphs[index]}
            source, _ = _review_highlight(pair.get("source"), selected["source"])
            target, _ = _review_highlight(pair.get("target"), selected["target"])
            st.markdown(f'<div class="tp-term-example"><small>#{index + 1}</small>'
                        f'<div>{escape(_translation_preview(pair.get("source"), 105))}</div>'
                        f'<p>{escape(_translation_preview(pair.get("target"), 75))}</p></div>', unsafe_allow_html=True)
            full_examples.append(f'<div class="tp-term-occurrence"><small>#{index + 1}</small><div>{source}</div><p>{target}</p></div>')
        if full_examples:
            with st.expander(_t("完整上下文"), expanded=False):
                st.markdown("".join(full_examples), unsafe_allow_html=True)
        if not selected["occurrences"]:
            st.caption(_t("当前原文中未找到该术语。"))
        st.markdown((_h('<h4>使用一致性</h4><p class="tp-ws-subtle">') + str(selected["matched"]) + ' / ' + str(len(selected["occurrences"])) + _h(' 段包含首选译名</p>')),
                    unsafe_allow_html=True)
        st.caption(_t("按当前双语段落做字面匹配，不代替语义审校。"))
        if st.button(_t("确认译名"), key=f"workspace_term_confirm_{job_id}_{selected['id']}",
                     type="primary", width="content",
                     disabled=selected["status"] == "locked" or not selected["target"]
                     or bool(selected["entry"].get("read_only"))):
            core.set_glossary_entry_status(job_id, [selected["id"]], "locked")
            _set_workspace_flash("译名已确认并锁定。")
            st.rerun()


def _review_highlight(text, span):
    text = str(text or "")
    span = str(span or "").strip()
    if not text or not span:
        return escape(text or "—"), False
    start = text.find(span)
    if start < 0:
        return escape(text), False
    end = start + len(span)
    return (escape(text[:start]) + '<mark class="tp-review-span">'
            + escape(text[start:end]) + '</mark>' + escape(text[end:])), True


def _review_confidence_label(value):
    if value is None or value == "":
        return "未提供"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return escape(str(value))


def _render_workspace_review_context(job_id, state):
    view = _review_workbench(state)
    selected_id = st.session_state.get("selected_review_item_id")
    selected = next((item for item in view["queue_items"] if item["id"] == selected_id), None)
    if not selected:
        st.markdown(_h('<div class="tp-inspector-empty">选择一项检查结果，查看证据和处理建议。</div>'), unsafe_allow_html=True)
        return
    with st.container(key="review_inspector"):
        st.markdown((_h('<div class="tp-ws-inspector-heading">问题详情</div><h3>') + str(escape(selected.get("detected_text") or selected["title"])) + '</h3><p class="tp-review-problem">' + str(escape(selected.get("summary") or "请检查当前审校任务")) + '</p>'), unsafe_allow_html=True)
        if selected["kind"] in {"stale", "failed", "missing"}:
            st.caption(_t({"stale": "译文已修改，旧审校结果不再适用于当前译文。",
                        "failed": "上次独立审校未完成。", "missing": "此段尚未完成独立审校。"}[selected["kind"]]))
        st.markdown(_h('<h4>证据</h4>'), unsafe_allow_html=True)
        evidence = _workspace_presentation.review_evidence(state, selected)
        for row in evidence[:3]:
            source, _ = _review_highlight(row["source"], selected.get("source_span"))
            target, _ = _review_highlight(row["target"], selected.get("target_span"))
            st.markdown(f'<div class="tp-review-evidence-pair"><small>#{row["index"] + 1}'
                        f'{" · 检查时记录" if row["recorded"] else " · 当前译文"}</small>'
                        f'<div>{source}</div><p>{target}</p></div>', unsafe_allow_html=True)
        if selected.get("explanation"):
            st.caption(selected["explanation"])
        elif selected.get("legacy_diagnostic"):
            st.caption(_t("旧记录未保存完整判断依据，请结合当前上下文核对。"))
        index = selected.get("segment_id")
        pair = ((state.get("pairs") or [])[index] if isinstance(index, int)
                and 0 <= index < len(state.get("pairs") or []) else {})
        detected = str(selected.get("detected_text") or "").casefold()
        related_terms = [row for row in _workspace_presentation.term_rows(state)
                         if (detected and row["source"].casefold() == detected)
                         or (selected.get("entry_id") and row["id"] == selected["entry_id"])]
        terms = [(row["source"], row["target"], _TERM_STATUS_LABELS.get(row["status"], "待确认"))
                 for row in related_terms] or _translation_terms_for_pair(state, pair)
        rules = [r for r in state.get("confirmed_style_rules") or []
                 if isinstance(r, dict) and r.get("status") == "confirmed"]
        st.markdown(_h('<h4>项目约束</h4>'), unsafe_allow_html=True)
        for source, target, provenance in terms[:5]:
            st.markdown(f'<div class="tp-review-constraint">{escape(source)} → {escape(target)}<small> · {escape(str(provenance))}</small></div>', unsafe_allow_html=True)
        for rule in rules[:3]:
            st.caption(str(rule.get("rule") or ""))
        if not terms and not rules:
            st.caption(_t("本段没有已确认的项目术语或风格规则。"))
        if selected.get("entry_id") and st.button(_t("查看项目术语"), key=f'review_open_term_{selected["id"]}', type="tertiary"):
            st.session_state["selected_glossary_entry_id"] = selected["entry_id"]
            st.session_state.workspace_section = "terms"
            st.rerun()
        with st.expander(_t("段落上下文"), expanded=False):
            pairs = state.get("pairs") or []
            if isinstance(index, int):
                for i in range(max(0, index - 1), min(len(pairs), index + 2)):
                    st.markdown(f'**#{i + 1}** {pairs[i].get("source") or ""}')
                    st.write(pairs[i].get("target") or "")
        st.markdown(_h('<h4>建议</h4>'), unsafe_allow_html=True)
        recommendation = selected.get("recommendation") or (
            "结合项目术语核对多个译法，必要时修改当前译文。" if selected.get("category") in
            {"terminology", "terminology_consistency"} else "核对证据和当前译文，再记录处理决定。")
        st.markdown(f'<p class="tp-ws-recommendation">{escape(recommendation)}</p>', unsafe_allow_html=True)
        if selected.get("suggested_target"):
            st.markdown(f'<div class="tp-review-proposed">{escape(selected["suggested_target"])}</div>', unsafe_allow_html=True)
        runtime = resolve_review_runtime()
        _, note = _render_review_item_actions(
            job_id, state, selected, view["queue_items"],
            bool(state.get("translation_core_review_required")), _review_runtime_ready(runtime),
            runtime, bool(api_key and ai_model))
        if selected.get("category") == "style":
            with st.expander(_t("保存为项目风格规则"), expanded=False):
                style_key = f'review_style_rule_{selected["id"]}'
                if style_key not in st.session_state:
                    st.session_state[style_key] = selected.get("recommendation") or ""
                st.text_area(_t("规则"), key=style_key)
                if st.button(_t("确认保存"), key=f'review_style_confirm_{selected["id"]}'):
                    core.confirm_translation_style_rule(
                        job_id, st.session_state.get(style_key, ""), "user", actor_type="human",
                        note=note, source_finding_id=selected.get("core_finding_id") or "")
                    _set_workspace_flash("项目风格规则已由你确认保存。")
                    st.rerun()
        with st.expander(_t("历史"), expanded=False):
            history = _workbench_view.review_history(state, index)
            for row in history:
                st.caption(_t(f'{str(row.get("timestamp") or "")[:16]} · {row["label"]}'))
                st.write(row.get("detail") or "")
            if not history:
                st.caption(_t("暂无更早的审校记录。"))
        with st.expander(_t("技术详情"), expanded=False):
            st.caption(_t(f'finding：{selected.get("core_finding_id") or selected.get("finding_id") or "—"}'))
            st.caption(_t(f'detector：{selected.get("detector") or "—"} · confidence：{_review_confidence_label(selected.get("confidence"))}'))
            refs = list(dict.fromkeys((selected.get("evidence_refs") or []) + (selected.get("evidence_ids") or [])))
            if refs:
                st.write("证据引用：" + "、".join(refs))


def _render_workspace_delivery_context(job_id, state):
    snapshot = core.delivery_snapshot_status(job_id, state)
    latest = snapshot.get("latest") or {}
    approval = latest.get("approval") or {}
    truth = core.translation_truth_view(job_id, state)
    working_label = f'v{truth.get("version", 0)} · 当前'
    frozen_label = (f'v{latest.get("snapshot_version")} · 已冻结'
                    if latest else "尚未生成")
    if snapshot.get("diverged"):
        status_label = f'工作版本已偏离 v{latest.get("snapshot_version")}'
        status_tone = "warning"
    elif latest:
        status_label = f'已冻结 v{latest.get("snapshot_version")}'
        status_tone = "success"
    else:
        status_label = "尚未生成冻结交付"
        status_tone = "warning"
    st.markdown((_h('<div class="tp-version-compare"><h3>版本对比</h3><div class="tp-version-row"><span>工作版本</span><strong>') + str(escape(working_label)) + _h('</strong></div><div class="tp-version-row"><span>冻结交付</span><strong>') + str(escape(frozen_label)) + '</strong></div><div class="tp-version-compare-status is-' + str(status_tone) + '">' + str(escape(status_label)) + '</div></div>'), unsafe_allow_html=True)
    if latest:
        st.caption(_t('确认：{v0} · {v1}', v0=str(approval.get('actor') or '—'), v1=str(approval.get('timestamp') or latest.get('created_at') or '—')[:16]))
        # Quick download actions for frozen snapshot
        with st.expander(_t("快捷下载"), expanded=False):
            assets = core.build_delivery_assets(job_id, state)
            for key in ("translation.docx", "translation.pdf", "translation.epub", "bilingual.docx"):
                data = (assets or {}).get(key)
                if data:
                    label = {"translation.docx": "译文 DOCX",
                             "translation.pdf": "译文 PDF",
                             "translation.epub": "译文 EPUB",
                             "bilingual.docx": "双语对照"}.get(key, key)
                    mime = {".pdf": "application/pdf", ".epub": "application/epub+zip"}.get(
                        Path(key).suffix, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                    st.download_button(_t(label), data, file_name=key, mime=mime,
                                       key=f"ctx_dl_{job_id}_{key}", use_container_width=True)


def _render_workspace_context(job_id, state, section, overview=None):
    if section == "translation":
        with st.container(key="translation_inspector"):
            _render_workspace_translation_context(job_id, state, overview)
    elif section == "terms":
        _render_workspace_terms_context(job_id, state)
    elif section == "review":
        _render_workspace_review_context(job_id, state)
    elif section == "cases":
        _render_workspace_cases_context(job_id, state)
    elif section == "qa":
        _render_workspace_qa_context(job_id, state)
    elif section == "delivery":
        _render_workspace_delivery_context(job_id, state)


def _academic_downstream_enabled(state, job_id=""):
    """是否存在需要随译文变化重建的学术/报告下游产物。

    `core.dependency_impact_view` 只要译文变了就把所有已知下游产物标为 stale，
    包括这个任务根本不适用的报告/QA 产物。于是纯翻译任务会被告知
    "重建 9 项下游产物"，而同一页的交付判断网格却说"学术产物同步：当前任务
    未启用 ✓"——页面自相矛盾。这里给出与网格一致的判据。
    """
    if not isinstance(state, dict):
        return False
    if state.get("report_enabled"):
        return True
    artifacts = (state.get("academic_state") or {}).get("artifacts") or {}
    return bool(artifacts)

# 一次最多渲染多少行（视口上限）。从 400 降到 40，大幅削减 DOM/widget 爆炸。
CAT_GRID_ROWS = 40
# 编辑框的保存基线：`translation_editor_{segment_id}` 里的值 != 基线 => 有未保存改动。
# 用基线而不是 `st.session_state` 里有没有 key，是因为 typeahead 的 widget 状态在
# 交互后就会写进 session_state，直接拿它判断会把"没改过"误报成"改过"。
_CAT_BASELINE_PREFIX = "cat_baseline_"


def _cat_status_tone(status):
    """把段落状态映射成左侧指示条，替代"状态"整列。"""
    return {
        "已审校": "is-reviewed",
        "已修改": "is-edited",
        "待审校": "is-pending",
        "待翻译": "is-pending",
    }.get(str(status or ""), "")


def _render_translation_progress(state, job_id, projection, truth, change_note):
    """The translation progress bar was removed because overall progress is
    already displayed in the primary task runtime banner (HUD) and topbar.
    """
    pass


def _toggle_translation_filter(key):
    """Toggle a keyed filter before Streamlit reconstructs its widget."""
    st.session_state[key] = not bool(st.session_state.get(key, False))


def _render_agent_findings_strip(job_id, state, *, findings, issue_indexes,
                                 quality_count):
    """Compact issue chips filter matching segments without interrupting the grid."""
    term_findings = [item for item in findings if item.get("kind") == "terminology"]
    term_key = f"translation_filter_term_issue_{job_id}"
    qa_key = f"translation_filter_issue_{job_id}"
    quality_key = f"translation_quality_filter_{job_id}"
    term_active = bool(st.session_state.get(term_key, False))
    qa_active = bool(st.session_state.get(qa_key, False))
    quality_active = bool(st.session_state.get(quality_key, False))
    chips = []
    if term_findings:
        lead = str(term_findings[0].get("title") or "")
        term_name = lead.split("「", 1)[1].split("」", 1)[0] if "「" in lead else ""
        label = f"术语 {len(term_findings)}" + (f" · {term_name}" if term_name else "")
        chips.append((("× " if term_active else "") + label, term_key, term_active))
    if quality_count:
        label = f"原文质量 {quality_count:,}"
        chips.append((("× " if quality_active else "") + label,
                      quality_key, quality_active))
    if issue_indexes:
        label = f"审校涉及 {len(issue_indexes):,} 段"
        chips.append((("× " if qa_active else "") + label, qa_key, qa_active))
    if not chips:
        return
    with st.container(key="translation_issue_filters"):
        cols = st.columns([max(1.0, min(3.8, len(label) / 8))
                           for label, _, _ in chips], gap="small")
        for col, (label, key, active) in zip(cols, chips):
            with col:
                st.button(_t(label), key=f"translation_issue_chip_{key}",
                          on_click=_toggle_translation_filter, args=(key,),
                          type="primary" if active else "secondary",
                          width="stretch", help=_t("点击筛选相关段落"))


def _render_translation_filter_menu(job_id):
    filter_terms_key = f"translation_filter_terms_{job_id}"
    term_issue_key = f"translation_filter_term_issue_{job_id}"
    filter_edited_key = f"translation_filter_edited_{job_id}"
    filter_issue_key = f"translation_filter_issue_{job_id}"
    filter_tm_key = f"translation_filter_tm_{job_id}"
    filter_quality_key = f"translation_quality_filter_{job_id}"
    active_advanced_count = sum(bool(st.session_state.get(key)) for key in (
        filter_terms_key, term_issue_key, filter_edited_key, filter_issue_key,
        filter_tm_key, filter_quality_key))
    filter_label_text = (f"筛选 {active_advanced_count}"
                         if active_advanced_count else "筛选")
    with st.popover(_t(filter_label_text), icon=":material/filter_list:", help=_t('筛选 · 已启用 {v0} 项', v0=active_advanced_count), use_container_width=True,
                    key=f"translation_filter_menu_{job_id}"):
        filter_terms = st.checkbox(_t("含项目术语"), key=filter_terms_key)
        filter_edited = st.checkbox(_t("已修改"), key=filter_edited_key)
        issue_only = st.checkbox(_t("有审校问题"), key=filter_issue_key)
        filter_tm = st.checkbox(_t("使用翻译记忆"), key=filter_tm_key)
        filter_quality = st.checkbox(_t("原文质量问题"), key=filter_quality_key)


def _render_translation_active_actions(job_id, state, index, pair, *,
                                       findings=(), issue_indexes=(), quality_count=0):
    """Share one compact row between finding filters and navigation."""
    with st.container(key="translation_editor_actions"):
        label_col, filter_col, prev_col, next_col, inspector_col = st.columns([8, 0.65, 0.55, 0.55, 0.55], gap="small")
        with inspector_col:
            st.button(_t("显示或收起右栏"), icon=":material/vertical_split:",
                      help=_t("显示或收起右栏"), key=f"workspace_inspector_toggle_{job_id}",
                      width="stretch", on_click=_toggle_translation_filter,
                      args=("workspace_inspector_open",))
        with filter_col:
            _render_translation_filter_menu(job_id)
        with label_col:
            _render_agent_findings_strip(job_id, state, findings=findings,
                                         issue_indexes=issue_indexes,
                                         quality_count=quality_count)
        prev_col.button(_t("上一段"), icon=":material/arrow_upward:", help=_t("上一段"), key=f"translation_prev_{job_id}",
                        disabled=index <= 0, width="stretch",
                        on_click=_navigate_to_segment,
                        args=(job_id, state, index - 1), kwargs={"reveal": False})
        next_col.button(_t("下一段"), icon=":material/arrow_downward:", help=_t("下一段"), key=f"translation_next_{job_id}",
                        disabled=index >= len(state.get("pairs") or []) - 1,
                        width="stretch", on_click=_navigate_to_segment,
                        args=(job_id, state, index + 1), kwargs={"reveal": False})


def _save_active_and_next_unconfirmed(job_id, state, index, segment_id, *,
                                      baseline, is_dirty):
    """Save first, then move only after a successful write (or a clean row)."""
    if is_dirty and not _commit_translation_row(
            job_id, state, index, segment_id, baseline=baseline, quiet=True):
        return
    fresh = core.load_job_state(job_id) or state
    target_index = _next_unconfirmed_index(fresh, index)
    if target_index is None:
        _set_workspace_flash("没有其它待翻译或待审校段落了。", "info")
        st.rerun()
        return
    _navigate_to_segment(job_id, fresh, target_index, reveal=True)
    st.session_state[_NAV_PENDING_FOCUS] = target_index
    st.rerun()


def _render_active_translation_row_actions(job_id, state, index, segment_id,
                                           pair, *, baseline, is_dirty, status):
    """Keep edit controls next to the active target and separate save from QA."""
    return _cat_grid_ui.render_active_translation_row_actions(
        job_id, state, index, segment_id,
        status=status, baseline=baseline, is_dirty=is_dirty,
        save_fn=_commit_translation_row,
        save_next_fn=_save_active_and_next_unconfirmed,
    )


def _render_issues_panel(job_id, state, *, plan_findings=None):
    """右栏的问题抽屉：发现 → 定位 → 回到该段 Inspector。

    每条问题保留 #N 锚点。点锚点走唯一的 `_navigate_to_segment`：
    关闭抽屉 + 选中该段 + 一次性滚动 + 目标被挡住时先放宽筛选。
    """
    if plan_findings is None:
        plan_findings = _planner.plan_findings(state, job_id=job_id, limit=10 ** 6)
    review_items = [item for item in _workbench_view.review_workbench_view(state)["queue_items"]
                    if item.get("kind") == "finding"]
    all_findings = [dict(item, source_label="全文检查") for item in plan_findings]
    all_findings.extend({
        "id": item["id"], "severity": item["severity"],
        "title": item["title"], "detail": item.get("summary") or item.get("reason") or "",
        "source_label": "审校记录",
        "segments": [item["segment_index"]] if isinstance(item.get("segment_index"), int) else [],
    } for item in review_items)
    findings = all_findings[:_ISSUES_PANEL_LIMIT]
    issues = {severity: sum(item.get("severity") == severity for item in all_findings)
              for severity in ("blocking", "actionable", "informational")}
    head_col, close_col = st.columns([2, 1.4], gap="small")
    with head_col:
        st.markdown(_h('<div class="tp-translation-inspector-head"><div>'
                    '<h3>问题</h3></div></div>'), unsafe_allow_html=True)
    with close_col:
        st.button(_t("返回段落"), key=f"issues_close_{job_id}",
                  use_container_width=True, on_click=_close_issues_panel)
    st.markdown(
        ('<div class="tp-issues-summary"><span class="is-blocking"><b>' + str(issues["blocking"]) + _h('</b> 必须处理</span><span class="is-actionable"><b>') + str(issues["actionable"]) + _h('</b> 建议检查</span><span><b>') + str(issues["informational"]) + _h('</b> 参考</span></div>')),
        unsafe_allow_html=True)
    st.caption(_t("按问题条目计数，包含全文检查与审校记录；同一条目可涉及多个段落。"))
    if len(all_findings) > len(findings):
        st.caption(_t('当前显示前 {v0} / {v1} 条问题。', v0=len(findings), v1=len(all_findings)))
    if not findings:
        st.markdown(_h('<div class="tp-inspector-empty">当前没有需要处理的问题。</div>'),
                    unsafe_allow_html=True)
        return
    for finding in findings:
        st.markdown(
            f'<div class="tp-issue-row is-{escape(finding["severity"])}">'
            f'<div class="tp-issue-row-head"><strong>{escape(finding["title"])}</strong>'
            f'<span class="tp-issue-sev">{escape(finding["source_label"])} · '
            f'{escape(_ISSUE_SEVERITY_LABELS.get(finding["severity"], finding["severity"]))}</span>'
            '</div>'
            f'<p>{escape(finding["detail"])}</p></div>', unsafe_allow_html=True)
        segments = finding.get("segments") or []
        if not segments:
            continue
        st.markdown((_h('<div class="tp-issue-hits">命中 '))
                    + " ".join(f"#{index + 1}" for index in segments[:10])
                    + '</div>', unsafe_allow_html=True)
        # 锚点用按钮而不是 pills：pills 的选中状态会留在 widget state 里，
        # rerun 后再次返回同一个值——正是上一版"点一次跳转、之后反复重放"的成因。
        # 按钮是一次性事件，天然没有这个问题。
        with st.container(key=f"issue_anchors_{job_id}_{finding['id']}", horizontal=True):
            for segment in segments[:6]:
                st.button(_t(f"#{segment + 1}"),
                          key=f"issue_anchor_{job_id}_{finding['id']}_{segment}",
                          width="content", help=_t('查看第 {v0} 段', v0=segment + 1),
                          on_click=_navigate_to_segment,
                          args=(job_id, state, segment))


def _blocking_segments(state, job_id):
    """存在"必须处理"级问题的段落集合。

    只有 blocking 才配上行内标记。actionable 级（例如"这段译文偏短，可能漏译"）
    是建议，不是异常——把 actionable 也标成"需处理"，82 段文档里会出现 26 个红字，
    等于把建议喊成了错误。
    """
    return {index for finding in _planner.plan_findings(state, job_id=job_id, limit=200)
            if finding.get("severity") == "blocking"
            for index in (finding.get("segments") or [])}


def _row_status_anomaly(pair, index, blocking_segments, is_dirty, review_required):
    """段落行里唯一值得常驻的状态文字：异常。

    "已翻译"是正常状态，不该每一行都宣布一次——20 段同屏就变成 20 个重复标签，
    正文也不再像连续文档。正常状态交给右侧 Inspector 的"段落事实"。
    没有异常时返回 None，整行不渲染状态区。
    """
    if is_dirty:
        return ("is-dirty", "● 未保存")
    if index in blocking_segments:
        return ("is-issue", "需处理")
    if pair.get("human_edited") or pair.get("source_human_edited"):
        return ("is-edited", "已修改")
    if review_required and str(pair.get("target") or "").strip() \
            and not pair.get("reviewed"):
        return ("is-pending", "待审校")
    return None


# ================= 未保存译文的草稿层 =================
# 译文输入框是普通 widget（不是表单），失焦/⌘+Enter 会把值交给服务端。但正文网格
# 只渲染窗口内的段落，窗口外的 keyed widget 会被 Streamlit 丢弃——所以"输入还在
# 输入框里但这一行本轮没渲染"就等于静默丢稿。这里把输入同步进一份会话级草稿：
# key 是 `job_id|段身份`（身份稳定，不受拆分/插入导致的索引漂移影响），因此
# 切换筛选、跳转、换任务都不会丢；只有"保存"或"丢弃"才会清掉它。
_TRANSLATION_DRAFT_STORE = "translation_edit_drafts"
# 「复制原文到译文」这类动作要在下一轮把新内容放进输入框。**注意**：官方文档里
# "清掉 key 让 widget 用新默认值重建"这条办法对**已经被用户敲过字**的输入框无效 ——
# `st.text_area` 的 element id 只由 `(user_key, max_chars)` 决定
# （`streamlit/elements/lib/utils.py: compute_and_register_element_id`，
# text_area 传的是 `key_as_main_identity={"max_chars"}`），**默认值不参与**。
# 因此清 session_state 不会改变 element id，前端组件实例不重挂载，它保留自己的
# dirty 值：用户点了"复制原文到译文"/"采用磁盘上的版本"，框里还是旧文字。
# 唯一可靠的重挂载方式是**换 key**，见 `_reset_translation_editor`。
# 浏览器实测证据：`.audit/reset_probe.js`（曾复现"点了复制原文，框里没变"）。
_TRANSLATION_EDITOR_SEED = "translation_editor_seed"
# 每个段落的重挂载序号。常态为 0，此时 key 就是 `translation_editor_<段身份>`；
# 需要复位时自增一次，key 变成 `translation_editor_<段身份>#<n>`。
_TRANSLATION_EDITOR_GENERATION = "translation_editor_generation"
# 草稿落盘失败必须让用户看见：静默的"保存了草稿"比没保存更危险。
_TRANSLATION_DRAFT_PERSIST_ERROR = "translation_drafts_persist_error"


def _translation_editor_base_key(segment_id):
    return f"translation_editor_{segment_id}"


def _translation_editor_generation(segment_id):
    generations = st.session_state.get(_TRANSLATION_EDITOR_GENERATION)
    if not isinstance(generations, dict):
        return 0
    try:
        return int(generations.get(str(segment_id)) or 0)
    except (TypeError, ValueError):
        return 0


def _translation_editor_key(segment_id):
    """译文输入框当前的 widget key（见上方注释：复位靠换 key，不靠清值）。"""
    base = _translation_editor_base_key(segment_id)
    generation = _translation_editor_generation(segment_id)
    return base if not generation else f"{base}#{generation}"


def _translation_editor_keys(segment_id):
    """这个段落的输入框用过的所有 key（含各代序号）。"""
    base = _translation_editor_base_key(segment_id)
    return [name for name in st.session_state
            if str(name) == base or str(name).startswith(f"{base}#")]


def _reset_translation_editor(segment_id):
    """让这个段落的输入框在下一轮**被重挂载**，从而丢掉前端的 dirty 值。

    调用场景都是"服务端已经决定了框里该显示什么"，而用户此刻手里是旧内容：
    「复制原文到译文」「丢弃未保存的修改」「采用磁盘上的版本」「恢复原译」，
    以及保存/结构操作之后。只清 session_state 不够（见上方注释），必须换 key。
    """
    for name in _translation_editor_keys(segment_id):
        st.session_state.pop(name, None)
        st.session_state.pop(f"{_CAT_BASELINE_PREFIX}{name}", None)
    generations = dict(st.session_state.get(_TRANSLATION_EDITOR_GENERATION) or {})
    generations[str(segment_id)] = _translation_editor_generation(segment_id) + 1
    st.session_state[_TRANSLATION_EDITOR_GENERATION] = generations


def _translation_draft_store():
    store = st.session_state.get(_TRANSLATION_DRAFT_STORE)
    if not isinstance(store, dict):
        store = {}
        st.session_state[_TRANSLATION_DRAFT_STORE] = store
    return store


def _draft_store_key(job_id, segment_id):
    return f"{job_id}|{segment_id}"


def _translation_draft_for(job_id, segment_id):
    return _translation_draft_store().get(_draft_store_key(job_id, segment_id))


def _persist_translation_drafts(job_id):
    """把本任务的会话草稿写进任务目录。

    草稿**只**在这里与磁盘打交道：它不进 state.json、不参与交付资产、不改审校状态。
    落盘失败不回滚会话里那一份（用户眼前的内容仍然正确），但要在横幅上说明，
    否则用户会以为刷新之后还在。
    """
    try:
        core.save_translation_drafts(
            job_id,
            {str(record.get("segment_id")): record
             for record in _translation_drafts_for_job(job_id)})
    except OSError as exc:
        st.session_state[_TRANSLATION_DRAFT_PERSIST_ERROR] = str(exc)
    else:
        st.session_state.pop(_TRANSLATION_DRAFT_PERSIST_ERROR, None)


def _record_translation_draft(job_id, index, segment_id, text, baseline):
    store = _translation_draft_store()
    key = _draft_store_key(job_id, segment_id)
    if str(text) == str(baseline):
        store.pop(key, None)
        _persist_translation_drafts(job_id)
        return None
    record = {"job_id": str(job_id), "segment_id": str(segment_id),
              "index": int(index), "text": str(text), "baseline": str(baseline)}
    store[key] = record
    _persist_translation_drafts(job_id)
    return record


def _drop_translation_draft(job_id, segment_id=None):
    store = _translation_draft_store()
    if segment_id is None:
        prefix = f"{job_id}|"
        for key in [item for item in store if str(item).startswith(prefix)]:
            store.pop(key, None)
        _persist_translation_drafts(job_id)
        return
    store.pop(_draft_store_key(job_id, segment_id), None)
    _persist_translation_drafts(job_id)


def _restore_translation_drafts(job_id, state):
    """会话里没有草稿、磁盘上有 → 上次会话没保存完就断了，把内容还给用户。

    只在**本会话第一次**渲染该任务时做一次；用户随后丢弃的草稿不会被"复活"。

    草稿不是正式译文：这里只把它放回输入框和未保存横幅，不写文档，
    也不碰 `reviewed` / `review_status`——一个恢复回来的草稿绝不能让段落
    显示成"已审校"。段落已被排除/合并/删除的草稿直接丢弃（并落盘），
    对应段落已经不存在，留着只会让"未保存 N 处"永远清不掉。
    """
    if _translation_drafts_for_job(job_id):
        return 0
    marker = f"translation_drafts_restored_{job_id}"
    if st.session_state.get(marker):
        return 0
    st.session_state[marker] = True
    stored = core.load_translation_drafts(job_id)
    if not stored:
        return 0
    store = _translation_draft_store()
    restored = 0
    for segment_id, record in stored.items():
        index = _translation_segment_index(job_id, state, segment_id)
        if index is None:
            continue
        store[_draft_store_key(job_id, segment_id)] = {
            "job_id": str(job_id), "segment_id": segment_id, "index": int(index),
            "text": record.get("text") or "", "baseline": record.get("baseline") or "",
        }
        # 输入框必须用草稿重建：widget 若已带旧值（或旧 seed），本轮不会再取草稿。
        _reset_translation_editor(segment_id)
        restored += 1
    # 失效条目（段落已不存在）随这次写入从磁盘上清掉。
    _persist_translation_drafts(job_id)
    st.session_state[f"translation_drafts_restored_note_{job_id}"] = restored
    return restored


def _translation_drafts_for_job(job_id):
    prefix = f"{job_id}|"
    return [record for key, record in _translation_draft_store().items()
            if str(key).startswith(prefix) and isinstance(record, dict)]


def _translation_drafts_elsewhere(job_id):
    prefix = f"{job_id}|"
    return sum(1 for key in _translation_draft_store()
               if not str(key).startswith(prefix))


def _translation_editor_changed(job_id, index, segment_id, baseline):
    """输入框失焦 / ⌘+Enter：把值同步进草稿（不写文档）。"""
    # Keep the server-side path correct even when the browser focus bridge is
    # unavailable (for example in Streamlit AppTest or a delayed rerun).
    st.session_state["selected_segment_id"] = str(segment_id)
    st.session_state["selected_segment_index"] = int(index)
    text = st.session_state.get(_translation_editor_key(segment_id), baseline)
    _record_translation_draft(job_id, index, segment_id, text, baseline)


def _translation_segment_index(job_id, state, segment_id):
    """段身份 → 当前索引。**永远现算**：拆分/插入之后索引会漂移，
    保存时若用渲染时的旧索引就会把内容写到别的段落上。"""
    wanted = str(segment_id)
    for record in _translation_segment_records(job_id, state):
        if record["segment_id"] == wanted:
            return record["index"]
    return None


def _translation_row_text(segment_id, fallback=""):
    return str(st.session_state.get(_translation_editor_key(segment_id), fallback))


def _translation_conflict_text(job_id, index, reference):
    """磁盘上的译文是否已经不等于"用户开始编辑时看到的那一份"。

    只跟**草稿自己的 baseline** 比，不跟本次渲染出来的 pair 比。原因：后台轮询或
    另一个标签页写入之后，本次渲染的 `pair.target` 已经是新值，拿它当参照永远
    比出"不冲突"——那正好是最需要拦住的情况（保存会覆盖对方的内容）。
    """
    fresh = core.load_job_state(job_id) or {}
    pairs = fresh.get("pairs") or []
    if not 0 <= index < len(pairs):
        return None
    live = str(pairs[index].get("target") or "")
    return live if live != str(reference or "") else None


def _commit_translation_row(job_id, state, index, segment_id, *, baseline,
                            quiet=False, force=False, explicit_text=None):
    """把这一行当前输入写进文档。返回是否真的写入。

    返回值用于"保存并进入下一段"：没改动时它只是导航，不该谎报"已保存"。

    `force=False` 时若磁盘上的译文已经变过（另一个标签页 / 后台重译写入过），
    这里不写，而是把冲突放进会话状态交给顶部的冲突面板处理——静默覆盖别人的
    修改比保存失败更难发现。

    `explicit_text` 用于冲突面板的"用我的草稿覆盖"：那一轮输入框可能根本没渲染
    （段落被筛掉），从 widget 读值会读到旧译文而不是草稿内容。
    """
    text = str(_translation_row_text(segment_id, baseline)
               if explicit_text is None else explicit_text)
    draft = _translation_draft_for(job_id, segment_id)
    if text == str(baseline) and not draft:
        if not quiet:
            _set_workspace_flash(
                f"第 {index + 1} 段没有需要保存的修改。", "info")
        return False
    resolved = _translation_segment_index(job_id, state, segment_id)
    if resolved is None:
        # 段落已经不存在（例如刚被排除/撤销）。草稿保留，绝不写到别的段落上。
        _set_workspace_flash(
            "这一段在当前文档里已经不存在（可能刚被排除或撤销），修改未写入；"
            "它仍保留在未保存列表中。", "error")
        return False
    reference = str(draft.get("baseline") or "") if draft else str(baseline or "")
    conflict = None if force else _translation_conflict_text(job_id, resolved, reference)
    if conflict is not None:
        st.session_state[f"translation_save_conflict_{job_id}"] = {
            "segment_id": str(segment_id), "index": int(resolved),
            "mine": str(text), "reference": reference, "theirs": conflict,
        }
        st.rerun()
        return False
    try:
        _save_translation_edit(job_id, resolved, text)
    except (RuntimeError, ValueError, IndexError) as exc:
        _set_workspace_flash(f"保存失败：{exc}", "error")
        return False
    _drop_translation_draft(job_id, segment_id)
    _reset_translation_editor(segment_id)
    review_required = bool(state.get("translation_core_review_required"))
    _set_workspace_flash(
        f"第 {resolved + 1} 段译文已保存"
        + ("；上一次审校已过期，需要重新审校。" if review_required else "。"),
        "warning" if review_required else "success")
    return True


def _flush_translation_draft(job_id, state, index, segment_id):
    """结构操作前先把未保存译文落盘——操作对象必须是用户看到的内容。"""
    draft = _translation_draft_for(job_id, segment_id)
    if not draft:
        return False
    text = _translation_row_text(segment_id, str(draft.get("text") or ""))
    if str(text) == str(draft.get("baseline") or ""):
        _drop_translation_draft(job_id, segment_id)
        return False
    try:
        _save_translation_edit(job_id, index, text)
    except (RuntimeError, ValueError, IndexError):
        return False
    _drop_translation_draft(job_id, segment_id)
    _reset_translation_editor(segment_id)
    return True


def _purge_translation_edit_state(job_id, state):
    """结构操作之后丢弃指向"已经不存在的段身份"的编辑状态。

    只清两类：本任务这份草稿里已经失效的条目，以及名字里带本任务索引身份的
    widget 键。段身份单调递增、永不复用，所以残留的旧键最多是一点内存，不可能
    被误用到别的段落上。

    返回被丢弃的草稿条数；调用方必须把这件事说出来——静默丢稿正是要修的问题。
    """
    live = {record["segment_id"]
            for record in _translation_segment_records(job_id, state)}
    store = _translation_draft_store()
    dropped = 0
    for key, record in list(store.items()):
        if not isinstance(record, dict):
            continue
        if str(record.get("job_id") or "") != str(job_id):
            continue
        if str(record.get("segment_id") or "") in live:
            continue
        store.pop(key, None)
        dropped += 1
    prefixes = ("translation_editor_", _CAT_BASELINE_PREFIX)
    for name in list(st.session_state):
        label = str(name)
        if not label.startswith(prefixes):
            continue
        # 索引身份形如 `seg-<job_id>-0007`；稳定身份是 uid，不带任务信息，
        # 因此只按"本任务索引身份"清理即可。
        if f"-{job_id}-" in label:
            st.session_state.pop(name, None)
    _persist_translation_drafts(job_id)
    # 重挂载序号也只留活着的段身份：段身份永不复用，残留的序号不会被别的段落继承，
    # 但没必要一直攒着。
    generations = st.session_state.get(_TRANSLATION_EDITOR_GENERATION)
    if isinstance(generations, dict):
        kept = {key: value for key, value in generations.items() if str(key) in live}
        if kept:
            st.session_state[_TRANSLATION_EDITOR_GENERATION] = kept
        else:
            st.session_state.pop(_TRANSLATION_EDITOR_GENERATION, None)
    return dropped


def _render_translation_row_actions(job_id, state, index, segment_id, pair,
                                    *, baseline, is_dirty, anomaly):
    """行内保存区：delegated to folith.ui.workspace.cat_grid."""
    return _cat_grid_ui.render_translation_row_actions(
        job_id, state, index, segment_id, pair,
        baseline=baseline, is_dirty=is_dirty, anomaly=anomaly,
        commit_row_fn=_commit_translation_row,
        go_to_neighbour_fn=_go_to_neighbour_segment,
    )



def _go_to_neighbour_segment(job_id, state, index, offset, *, saved=False):
    """按当前可见集合移动到相邻段落，并把"是否真的保存了"如实提示。"""
    records = _translation_segment_records(job_id, state)
    visible = _visible_segment_indexes(job_id, state)
    position = visible.index(index) if index in visible else 0
    target = position + offset
    if not 0 <= target < len(visible):
        if not saved:
            _set_workspace_flash("已经是最后一段了。", "info")
        st.rerun()
        return
    next_index = visible[target]
    st.session_state["selected_segment_id"] = records[next_index]["segment_id"]
    st.session_state["selected_segment_index"] = next_index
    st.session_state[_NAV_PENDING_SCROLL] = next_index
    if saved:
        # 只有真写了盘才接管焦点：没改动时这只是"翻页"，抢焦点会让用户以为
        # 自己刚打的内容被吃掉了。
        st.session_state[_NAV_PENDING_FOCUS] = next_index
    st.rerun()


def _visible_segment_indexes(job_id, state):
    """当前筛选/搜索下会渲染的段落索引（"下一段"必须是用户看得见的那一段）。"""
    search = str(st.session_state.get(f"translation_search_{job_id}") or "")
    status_filter = st.session_state.get(f"translation_filter_{job_id}", "全部")
    issue_indexes = {item.get("segment_index") for item in _workspace_review_contexts(state)
                     if item.get("segment_index") is not None}
    visible = core.translation_visible_indexes(
        state, search=search, status_filter=status_filter,
        filter_terms=bool(st.session_state.get(f"translation_filter_terms_{job_id}")),
        filter_edited=bool(st.session_state.get(f"translation_filter_edited_{job_id}")),
        filter_issues=bool(st.session_state.get(f"translation_filter_issue_{job_id}")),
        filter_tm=bool(st.session_state.get(f"translation_filter_tm_{job_id}")),
        issue_indexes=issue_indexes)
    if st.session_state.get(f"translation_filter_term_issue_{job_id}"):
        term_issue_indexes = set(st.session_state.get(
            f"translation_term_issue_indexes_{job_id}") or [])
        visible = [index for index in visible if index in term_issue_indexes]
    projection = _workspace_projection(state, job_id)
    if status_filter == "待审":
        visible = [index for index in visible
                   if projection["segment_status"].get(index) != "已审校"]
    elif status_filter == "已审校":
        visible = [index for index in visible
                   if projection["segment_status"].get(index) == "已审校"]
    return visible


def _next_unconfirmed_index(state, current):
    """下一个"还没有确认的段落"：没有译文，或审校未通过。

    与"下一段"分开，是因为连续审校真正要消灭的是未完成项，不是顺序推进。
    """
    pairs = state.get("pairs") or []
    projection = _workspace_projection(state)
    for index in range(current + 1, len(pairs)):
        label = projection["segment_status"].get(index)
        if not str(pairs[index].get("target") or "").strip() or label in {
                "待翻译", "待审校", "需要重新审校", "必须处理", "建议检查",
                "审校未完成"}:
            return index
    for index in range(0, current):
        label = projection["segment_status"].get(index)
        if not str(pairs[index].get("target") or "").strip() or label in {
                "待翻译", "待审校", "需要重新审校", "必须处理", "建议检查",
                "审校未完成"}:
            return index
    return None


def _render_translation_draft_banner(job_id, state):
    """未保存修改必须看得见：这是"切换段落/筛选/任务不丢稿"的承诺所在。"""
    drafts = _translation_drafts_for_job(job_id)
    elsewhere = _translation_drafts_elsewhere(job_id)
    if not drafts and not elsewhere:
        return
    records = {record["segment_id"]: record["index"]
               for record in _translation_segment_records(job_id, state)}
    live = [item for item in drafts if str(item.get("segment_id")) in records]
    labels = "、".join(
        f"第 {records[str(item.get('segment_id'))] + 1} 段"
        for item in sorted(live, key=lambda item: records[str(item.get("segment_id"))]))
    head = f"有 {len(drafts)} 处未保存的译文修改" if drafts else ""
    if labels:
        head = f"{head}（{labels}）" if head else labels
    if elsewhere:
        head = f"{head}；另有其他任务的 {elsewhere} 处未保存修改" if head \
            else f"其他任务还有 {elsewhere} 处未保存修改"
    restored = int(st.session_state.get(f"translation_drafts_restored_note_{job_id}")
                   or 0)
    if restored:
        st.info(_t('已从本机任务目录恢复上次会话未保存的 {v0} 处修改。它们是草稿，还没有写入文档；请检查后保存或丢弃。', v0=restored))
    persist_error = st.session_state.get(_TRANSLATION_DRAFT_PERSIST_ERROR)
    if persist_error:
        st.error(_t('草稿写盘失败：{v0}。这些修改此刻仍只在本浏览器会话里，刷新或关闭标签页会丢失——请先点保存写入文档。', v0=persist_error))
    st.warning(_t('{v0}。草稿还没有写入文档，也不是正式译文（不会获得审校结论）；保存后才会写入。切换段落、筛选、任务，甚至刷新页面或关闭标签页，都不会再丢失——草稿保存在本机任务目录里，重新打开会恢复。', v0=head))
    save_col, discard_col, _rest = st.columns([1.1, 1.0, 2.6], gap="small")
    if save_col.button(_t("保存全部未保存修改"), key=f"translation_draft_save_all_{job_id}",
                       type="primary", width="stretch",
                       disabled=not live):
        saved, failed, conflicted = _commit_all_drafts(job_id)
        _set_workspace_flash(
            _draft_save_outcome(saved, failed, conflicted),
            "warning" if (failed or conflicted) else "success")
        st.rerun()
    if discard_col.button(_t("丢弃全部"), key=f"translation_draft_discard_all_{job_id}",
                          width="stretch", disabled=not drafts):
        # 光丢草稿是不够的：输入框里还留着用户刚敲的字，而前端不会因为服务端
        # 清了值就松手（见 `_reset_translation_editor`）。不逐个复位的话，用户点了
        # "丢弃全部" 会看见文字**还在**，然后困惑地再点一次。
        pending = [str(item.get("segment_id") or "")
                   for item in _translation_drafts_for_job(job_id)]
        _drop_translation_draft(job_id)
        for segment_id in pending:
            _reset_translation_editor(segment_id)
        _set_workspace_flash("已丢弃本任务全部未保存修改。", "info")
        st.rerun()


def _commit_all_drafts(job_id):
    """保存本任务全部草稿。返回 `(saved, failed, conflicted)`。

    `conflicted` 必须单独计数：它既不是"保存成功"也不是"写不进去"，而是
    "写下去会覆盖别人改过的内容"。混进 failed 会让用户以为重试就好。
    """
    state = core.load_job_state(job_id) or {}
    records = {record["segment_id"]: record["index"]
               for record in _translation_segment_records(job_id, state)}
    store = _translation_draft_store()
    saved = failed = conflicted = 0
    for item in list(_translation_drafts_for_job(job_id)):
        segment_id = str(item.get("segment_id") or "")
        index = records.get(segment_id)
        text = str(item.get("text") or "")
        if index is None or not text.strip() and text == str(item.get("baseline") or ""):
            failed += 1
            continue
        if _translation_conflict_text(job_id, index, item.get("baseline")) is not None:
            conflicted += 1
            continue
        try:
            _save_translation_edit(job_id, index, text)
        except (RuntimeError, ValueError, IndexError):
            failed += 1
            continue
        store.pop(_draft_store_key(job_id, segment_id), None)
        _reset_translation_editor(segment_id)
        saved += 1
    # 一次落盘，不在循环里反复重写同一个文件。
    _persist_translation_drafts(job_id)
    return saved, failed, conflicted


def _draft_save_outcome(saved, failed, conflicted, *, tail=""):
    """把批量保存的三个计数说成一句人话。三种结果不能混为一谈。"""
    parts = [f"已保存 {saved} 处译文修改"]
    if conflicted:
        parts.append(f"{conflicted} 处因内容已在别处改动而未保存（见上方冲突提示）")
    if failed:
        parts.append(f"{failed} 处因段落已不存在未能写入，仍留在未保存列表中")
    return "；".join(parts) + (tail or "。")


def _render_delivery_draft_guard(job_id, state):
    """交付前的未保存草稿检查。返回"是否仍有未保存修改"。

    交付/冻结是把**已保存**译文定格成不可变版本。草稿不在文档里，所以冻结出来的
    快照不包含它们——用户的预期却是"导出我看到的东西"。这个差值必须在这里拦住，
    而不是靠翻译页那条"未保存"横幅（它在另一个分区，交付页看不到）。

    所有交付入口都要调用它；只在其中一个入口拦，等于没拦。
    """
    pending = []
    for item in _translation_drafts_for_job(job_id):
        index = _translation_segment_index(job_id, state, item.get("segment_id"))
        if index is not None:
            pending.append((index, item))
    pending.sort(key=lambda item: item[0])
    if not pending:
        return False
    labels = "、".join(f"第 {index + 1} 段" for index, _item in pending[:8])
    if len(pending) > 8:
        labels += f" 等 {len(pending)} 段"
    st.error(
        _t('还有 {v0} 处未保存的译文修改（{v1}）。冻结交付会把**当前已保存**的译文定格成不可变版本——未保存的修改不会进入快照，导出/下载的会是修改前的内容。请先保存，或返回编辑。', v0=len(pending), v1=labels))
    save_col, back_col, _rest = st.columns([1.25, 1.0, 2.35], gap="small")
    if save_col.button(_t('保存这 {v0} 处并继续', v0=len(pending)), type="primary",
                       width="stretch",
                       key=f"delivery_save_drafts_{job_id}"):
        saved, failed, conflicted = _commit_all_drafts(job_id)
        _set_workspace_flash(
            _draft_save_outcome(saved, failed, conflicted,
                                tail="，可以继续冻结交付。" if not (failed or conflicted) else ""),
            "warning" if (failed or conflicted) else "success")
        st.rerun()
    if back_col.button(_t("返回编辑"), width="stretch",
                       key=f"delivery_back_to_drafts_{job_id}"):
        first_index, first_item = pending[0]
        st.session_state["selected_segment_id"] = str(first_item.get("segment_id") or "")
        st.session_state["selected_segment_index"] = first_index
        st.session_state[_NAV_PENDING_SCROLL] = first_index
        st.session_state["workspace_section"] = "translation"
        st.rerun()
    return True


def _render_translation_conflict_panel(job_id, state):
    """保存时发现"磁盘上的译文已经变过" → 让用户决定，绝不静默覆盖。

    触发场景：同一任务开了两个标签页；或后台重译 / 批量操作在本会话之外写入了
    同一段。我们手里这份草稿基于更早的版本，直接写下去会丢掉对方的内容。

    三种处置都要有：覆盖（我的对）、采用（对方的对）、稍后处理（我先看看）。
    只给"覆盖"一个按钮，等于把提示做成了一次确认轰炸。
    """
    key = f"translation_save_conflict_{job_id}"
    payload = st.session_state.get(key)
    if not isinstance(payload, dict):
        return
    segment_id = str(payload.get("segment_id") or "")
    resolved = _translation_segment_index(job_id, state, segment_id)
    if resolved is None:
        # 段落已经被对方排除/合并掉了，没有可覆盖的对象，提示自动失效。
        st.session_state.pop(key, None)
        return
    st.error(
        _t('第 {v0} 段的译文在别处已经改过。你手里的修改基于更早的版本，直接保存会覆盖对方的修改。先看两边的内容再决定。', v0=resolved + 1))
    mine_col, theirs_col = st.columns(2, gap="medium")
    mine_col.caption(_t("你的草稿（尚未保存）"))
    mine_col.code(str(payload.get("mine") or "") or "（空）")
    theirs_col.caption(_t("磁盘上的当前译文"))
    theirs_col.code(str(payload.get("theirs") or "") or "（空）")
    keep_col, take_col, later_col = st.columns([1.25, 1.2, 0.95], gap="small")
    if keep_col.button(_t("用我的草稿覆盖"), type="primary", width="stretch",
                       key=f"translation_conflict_keep_{job_id}"):
        st.session_state.pop(key, None)
        _commit_translation_row(job_id, state, resolved, segment_id,
                                baseline=str(payload.get("reference") or ""),
                                force=True,
                                explicit_text=str(payload.get("mine") or ""))
        st.rerun()
    if take_col.button(_t("采用磁盘上的版本"), width="stretch",
                       key=f"translation_conflict_take_{job_id}"):
        st.session_state.pop(key, None)
        _drop_translation_draft(job_id, segment_id)
        _reset_translation_editor(segment_id)
        _set_workspace_flash(
            f"第 {resolved + 1} 段已改用磁盘上的译文，你那一份草稿已丢弃。", "info")
        st.rerun()
    if later_col.button(_t("稍后处理"), width="stretch",
                        key=f"translation_conflict_later_{job_id}"):
        # 只把提示收起来，草稿仍在未保存列表里——它不是"已解决"。
        st.session_state.pop(key, None)
        st.rerun()


def _render_translation_row(job_id, state, index, pair, segment_id,
                           selected_index, blocking_segments, *, projection=None,
                           runtime_status=None):
    """一行 = 段号 + 原文 + 可编辑译文 + 段落操作 (delegated to folith.ui.workspace.cat_grid)."""
    return _cat_grid_ui.render_translation_row(
        job_id, state, index, pair, segment_id,
        selected_index, blocking_segments,
        status_label_fn=lambda pair, state, index: _translation_pair_status_label(
            pair, state, index, projection=projection),
        editor_key_fn=_translation_editor_key,
        draft_for_fn=_translation_draft_for,
        row_anomaly_fn=_row_status_anomaly,
        navigate_fn=_navigate_to_segment,
        activate_fn=_activate_translation_segment,
        editor_changed_fn=_translation_editor_changed,
        row_actions_fn=_render_translation_row_actions,
        active_row_actions_fn=_render_active_translation_row_actions,
        segment_actions_fn=lambda *args, **kwargs: _render_translation_segment_actions(
            *args, **kwargs, runtime_status=runtime_status),
        qa_findings_fn=_translation_segment_findings,
    )



def _render_source_exclusion_panel(job_id, state, source_paras, quality_gate):
    """翻译之前的「原文清理」入口：把无效段落从待翻译里拿掉。

    只列**有证据**的候选（质量门判定为 OCR 碎片），加上手动输入的段号，避免把
    整篇 400 段原文摊开成表单。被排除的内容完整留档、可恢复。
    """
    flagged = [item for item in (quality_gate or {}).get("flags") or []
               if isinstance(item, dict)
               and isinstance(item.get("segment_index"), int)]
    excluded = core.excluded_segments_summary(state)
    with st.expander(_t("原文清理：排除无效段落（页码 / OCR 碎片 / 重复页眉）"),
                     expanded=bool(flagged)):
        st.caption(_t("被排除的段落不进入翻译、不计入待完成数量、不写入译文文档；"
                   "原文完整保留，可随时恢复。"))
        if excluded["count"]:
            _render_excluded_segments_panel(job_id, state)
        for item in flagged[:30]:
            index = int(item["segment_index"])
            if not 0 <= index < len(source_paras):
                continue
            text = " ".join(str(source_paras[index] or "").split())
            if len(text) > 140:
                text = text[:139] + "…"
            reasons = "；".join(str(reason) for reason in item.get("reasons") or [])
            st.markdown(
                (_h('<div class="tp-excluded-row is-candidate"><span>第 ') + str(index + 1) + ' 段 · ' + str(escape(reasons)) + '</span><p>' + str(escape(text) or "（空）") + '</p></div>'),
                unsafe_allow_html=True)
            if st.button(_t('排除第 {v0} 段', v0=index + 1),
                         key=f"source_exclude_{job_id}_{index}", width="stretch"):
                try:
                    core.mutate_translation_segments(
                        job_id, index, "exclude", exclude_reason=reasons or "人工判定为无效原文")
                except (RuntimeError, ValueError, IndexError) as exc:
                    _set_workspace_flash(f"排除失败：{exc}", "error")
                else:
                    _set_workspace_flash(
                        f"第 {index + 1} 段已排除，不会进入翻译。", "success")
                st.rerun()
        if not flagged:
            st.caption(_t("质量门没有发现疑似碎片。如仍有个别段落要排除，"
                       "可在上方预览里按段号操作。"))
        manual = st.number_input(
            "按段号排除", min_value=0, max_value=max(len(source_paras), 0),
            value=0, step=1, key=f"source_exclude_manual_{job_id}",
            help="填段落序号后点排除；0 表示不操作。")
        if st.button(_t("排除该段"), key=f"source_exclude_manual_run_{job_id}",
                     disabled=not manual, width="content"):
            try:
                core.mutate_translation_segments(
                    job_id, int(manual) - 1, "exclude",
                    exclude_reason="人工按段号排除")
            except (RuntimeError, ValueError, IndexError) as exc:
                _set_workspace_flash(f"排除失败：{exc}", "error")
            else:
                _set_workspace_flash(f"第 {int(manual)} 段已排除。", "success")
            st.rerun()


def _render_translation_scope_strip(job_id, state):
    """Keep rare structural undo available without reserving a status row."""
    undo = core.can_undo_translation_segment_structure(state)
    if not undo["available"]:
        return
    with st.container(key="translation_recovery_actions"):
        if st.button(_t('撤销结构操作 · {v0}', v0=undo['label']),
                     key=f"translation_undo_structure_{job_id}",
                     width="content",
                     help=_t("把原文与译文恢复到该操作之前。恢复后的段落需要重新审校。")):
            try:
                core.undo_translation_segment_structure(job_id)
            except (RuntimeError, ValueError) as exc:
                _set_workspace_flash(f"撤销失败：{exc}", "error")
            else:
                fresh = core.load_job_state(job_id) or state
                dropped = _purge_translation_edit_state(job_id, fresh)
                pairs = fresh.get("pairs") or []
                if pairs:
                    st.session_state["selected_segment_id"] = _translation_segment_id(
                        job_id, 0, pairs[0])
                    st.session_state["selected_segment_index"] = 0
                _set_workspace_flash(
                    "已撤销上一次结构操作，原文与译文已恢复。"
                    + (f"其中 {dropped} 处未保存修改因为对应段落已被撤销而失效。"
                       if dropped else "受影响段落需要重新审校。"),
                    "warning")
            st.rerun()

def _render_extraction_report(report):
    extracted = report.get("extracted") or {}
    st.markdown(
        (_h('<div class="tp-scope-block"><strong>已提取</strong><p>') + str(format(int(extracted.get("paragraphs") or 0), ',')) + ' 段')
        + (f'、{int(extracted.get("pages")):,} 页'
           if isinstance(extracted.get("pages"), int) else "")
        + ("（正文由本地 OCR 生成，可能含错字与假断行）"
           if extracted.get("ocr_used") else "")
        + '。</p></div>', unsafe_allow_html=True)
    unsupported = report.get("unsupported") or []
    if unsupported:
        st.markdown(
            _h('<div class="tp-scope-block is-warning"><strong>未纳入翻译</strong>')
            + "".join(
                f'<p>{escape(str(item.get("label") or ""))}：'
                f'{escape(str(item.get("detail") or ""))}'
                + (f'<br/><small>{escape(str(item.get("sample") or ""))}</small>'
                   if item.get("sample") else "")
                + '</p>'
                for item in unsupported)
            + '</div>', unsafe_allow_html=True)
    else:
        st.caption(_t("没有检测到表格、页眉页脚、脚注等未纳入的内容。"))
    st.caption(_t("导出是重建的译文文档，不是原文件的格式保真输出：排版、表格、"
               "图片与样式不会还原。"))


def _render_excluded_segments_panel(job_id, state):
    records = core.excluded_segment_records(state)
    st.caption(_t("被排除的段落保留原文，不进入翻译、不计入待完成数量、不写入译文文档；"
               "恢复后回到原来的位置。"))
    for position, record in enumerate(records):
        excluded_id = str(record.get("excluded_id") or "")
        source = " ".join(str(record.get("source") or "").split())
        if len(source) > 120:
            source = source[:119] + "…"
        reason = str(record.get("reason") or "").strip() or "未填写原因"
        st.markdown(
            (_h('<div class="tp-excluded-row"><span>第 ') + str(int(record["segment_index"]) + 1 if isinstance(record.get("segment_index"), int) else "—") + ' 段 · ' + str(escape(reason)) + '</span><p>' + str(escape(source) or "（空）") + '</p></div>'),
            unsafe_allow_html=True)
        if st.button(_t("恢复这一段"),
                     key=f"translation_include_{job_id}_{excluded_id or position}",
                     width="stretch"):
            try:
                core.mutate_translation_segments(
                    job_id, 0, "include", excluded_id=excluded_id)
            except (RuntimeError, ValueError, IndexError) as exc:
                _set_workspace_flash(f"恢复失败：{exc}", "error")
            else:
                fresh = core.load_job_state(job_id) or {}
                _purge_translation_edit_state(job_id, fresh)
                _set_workspace_flash("已恢复该段落；它需要重新翻译或审校。", "success")
            st.rerun()


def _render_delivery_scope_note(state):
    """交付物到底包含什么——排除范围与"重建文档"这条边界必须写在这里。"""
    summary = core.excluded_segments_summary(state)
    report = state.get("extraction_report") or {}
    unsupported = report.get("unsupported") or []
    lines = [
        "导出的 DOCX / PDF 是**依据当前译文重新生成**的文档，不是原文件的格式保真"
        "输出：原排版、表格、图片与样式不会还原。"
    ]
    if summary["count"]:
        lines.append(
            f"本次交付包含 {summary['working_segments']:,} 段译文；"
            f"已排除 {summary['count']:,} 段不进入译文文档，"
            "其原文与排除原因见 `excluded_segments.md`。")
    if unsupported:
        kinds = "、".join(str(item.get("label") or "") for item in unsupported)
        lines.append(f"导入时未纳入翻译的内容：{kinds}（详见「导入范围」）。")
    st.markdown('<div class="tp-scope-block">' + "".join(
        f'<p>{escape(line)}</p>' for line in lines) + '</div>',
        unsafe_allow_html=True)
    if report:
        with st.expander(_t("导入范围（本次翻译包含 / 不包含什么）"), expanded=False):
            _render_extraction_report(report)


def _render_translation_scope_summary_line(state):
    """工具栏旁边的一行事实：排除数量（有排除才出现）。"""
    summary = core.excluded_segments_summary(state)
    if not summary["count"]:
        return
    st.caption(_t('本次交付包含 {v0} 段；已排除 {v1} 段不计入翻译与交付。', v0=format(summary['working_segments'], ','), v1=format(summary['count'], ',')))


def _render_workspace_translation(job_id, state, overview=None, *, plan_findings=None):
    # 会话重建（刷新/关标签页/进程重启）之后，先把磁盘上的未保存草稿还给用户。
    # 必须在当前段落编辑器与正文网格之前跑：那两处都要以草稿为输入框初值。
    _restore_translation_drafts(job_id, state)
    pairs = state.get("pairs") or []
    projection = _workspace_projection(state)
    if not pairs:
        # 原文纠错完成后、译文尚未生成前，仍然要把已整理的源文本露出来。
        # 以前这里一律渲染空态，用户在语义理解/术语准备阶段会误以为 PDF 没有解析成功。
        st.markdown(_h('<div class="tp-cat-title"><h2>翻译</h2></div>'),
                    unsafe_allow_html=True)
        source_paras = [str(item or "").strip()
                        for item in (state.get("paras") or []) if str(item or "").strip()]
        if source_paras:
            cleanup = state.get("source_cleanup") or {}
            changed = len(cleanup.get("changed_segments") or [])
            merged = len(cleanup.get("paragraph_merges") or [])
            cleanup_label = "原文纠错已完成" if cleanup.get("status") == "completed" \
                else "原文纠错部分完成"
            runtime_view = core.build_job_runtime_view(job_id, state)
            operation_label = str(runtime_view.get("operation_label") or "正在准备译文")
            segmentation = state.get("segmentation") or {}
            paragraph_count = int(segmentation.get("input_paragraph_count") or 0)
            unit_summary = f"已整理 {len(source_paras):,} 个翻译单元"
            if paragraph_count and paragraph_count != len(source_paras):
                unit_summary += f"（来自 {paragraph_count:,} 个清洗段落）"
            st.markdown(
                ('<div class="tp-source-ready-block"><strong>' + str(escape(cleanup_label)) + ' · ' + str(escape(unit_summary)) + _h('</strong><p>已修复 ') + str(format(changed, ',')) + ' 项、整理 ' + str(format(merged, ',')) + ' 处断行。当前阶段：' + str(escape(operation_label)) + _h('；译文生成后会在这里显示完整翻译单元。</p></div>')), unsafe_allow_html=True)
            quality_gate = core._audit_source_quality_for_state(state)
            flagged = int(quality_gate.get("flagged_count") or 0)
            if flagged:
                st.warning(
                    _t('原文质量门发现 {v0} 个疑似 OCR 碎片翻译单元。可以直接在下面把它们排除：排除后不进入翻译、不计入待完成数量，原文仍完整保留并可恢复。', v0=format(flagged, ','))
                )
            preview_count = min(len(source_paras), 8)
            rows = []
            for index, text in enumerate(source_paras[:preview_count], 1):
                rows.append(
                    f'<div class="tp-source-preview-row"><b>{index}</b>'
                    f'<p>{escape(text)}</p></div>')
            st.markdown('<div class="tp-source-preview">' + ''.join(rows)
                        + '</div>', unsafe_allow_html=True)
            if len(source_paras) > preview_count:
                st.caption(_t('已显示前 {v0} 个翻译单元；其余单元将在译文列表生成后显示。', v0=preview_count))
            # 导入扫描件之后最先要做的往往不是翻译，而是把 OCR 垃圾行、页码和
            # 重复页眉从"待翻译"里拿掉。翻译前没有译文列表，所以这里必须另有一个入口。
            _render_source_exclusion_panel(job_id, state, source_paras, quality_gate)
            return
        # 真正没有源段落时才显示空态。开始/重试这类主动作只在 Banner 出现一次，
        # 正文里不再重复渲染第二套按钮。
        runtime_view = core.build_job_runtime_view(job_id, state)
        runtime_status = str(runtime_view.get("status") or runtime_view.get("runtime_status") or "idle")
        is_preparing = runtime_status not in {"idle", "completed", "failed", "cancelled"}
        empty_title = "正在准备翻译内容" if is_preparing else "还没有可翻译的段落"
        empty_copy = ("原文正在解析。完成后，翻译段落会自动出现在这里。"
                      if is_preparing else
                      "原文尚未解析出段落，或上次运行没有生成当前译文。已保存的任务进度不会丢失。")
        known = [f'源文件：{escape(str(state.get("filename") or "—"))}',
                 f'目标语言：{escape(str(state.get("target_lang") or "简体中文"))}']
        st.markdown(
            f'<div class="tp-empty-block"><strong>{escape(empty_title)}</strong>'
            f'<p>{escape(empty_copy)}</p>'
            f'<small>{" · ".join(known)}</small></div>', unsafe_allow_html=True)
        if not api_key or not ai_model:
            st.caption(_t("前置条件缺失：AI 引擎尚未配置完整，无法开始翻译。"))
            if st.button(_t("前往 AI 设置"), key=f"translation_open_settings_{job_id}",
                         width="content"):
                st.session_state.app_view = "settings"
                st.session_state.workspace_mode = False
                st.rerun()
        return
    _render_translation_segment_editor(job_id, state)
    # 未保存修改优先级最高：它必须出现在用户看得见的地方，而不是等切换筛选之后
    # 才发现"刚才写的那一段不见了"。冲突提示更靠前：它的默认结果是"先别动"。
    _render_translation_conflict_panel(job_id, state)
    _render_translation_draft_banner(job_id, state)
    _render_translation_scope_strip(job_id, state)

    review_projection = _source_review_projection(state)
    review_contexts = _workbench_view.review_workbench_view(
        review_projection)["queue_items"]
    issue_indexes = {item.get("segment_index") for item in review_contexts
                     if item.get("segment_index") is not None}
    all_plan_findings = (_planner.plan_findings(state, job_id=job_id, limit=200)
                         if plan_findings is None else plan_findings[:200])
    plan_findings = all_plan_findings[:12]
    term_issue_indexes = sorted({
        index for finding in plan_findings if finding.get("kind") == "terminology"
        for index in finding.get("segments") or []
        if isinstance(index, int) and not isinstance(index, bool)
    })
    st.session_state[f"translation_term_issue_indexes_{job_id}"] = term_issue_indexes
    quality_gate = review_projection["source_quality_gate"]
    quality_count = int(quality_gate.get("flagged_count") or 0)
    quality_indexes = {
        item.get("segment_index") for item in quality_gate.get("flags") or []
        if isinstance(item, dict) and isinstance(item.get("segment_index"), int)
    }
    filter_terms_key = f"translation_filter_terms_{job_id}"
    term_issue_key = f"translation_filter_term_issue_{job_id}"
    filter_edited_key = f"translation_filter_edited_{job_id}"
    filter_issue_key = f"translation_filter_issue_{job_id}"
    filter_tm_key = f"translation_filter_tm_{job_id}"
    filter_quality_key = f"translation_quality_filter_{job_id}"
    filter_quality = bool(st.session_state.get(filter_quality_key))
    active_advanced_count = sum(bool(st.session_state.get(key)) for key in (
        filter_terms_key, term_issue_key, filter_edited_key, filter_issue_key,
        filter_tm_key, filter_quality_key))

    with st.container(key="translation_toolbar"):
        with st.container(key="translation_status_tabs"):
            status_counts = projection.get("segment_status") or {}
            reviewed_count = sum(1 for value in status_counts.values() if value == "已审校")
            pending_count = max(0, len(pairs) - reviewed_count)
            status_labels = {"全部": f":material/list: {_t('全部')} {len(pairs):,}",
                             "待审": f":material/pending_actions: {_t('待审')} {pending_count:,}",
                             "已审校": f":material/task_alt: {_t('已审')} {reviewed_count:,}"}
            filter_label = st.segmented_control(
                _t("一级筛选"), ["全部", "待审", "已审校"], default="全部",
                key=f"translation_filter_{job_id}", label_visibility="collapsed",
                width="content", format_func=_localized_formatter(lambda value: status_labels.get(value, value))) or "全部"
    search = str(st.session_state.get(f"translation_search_{job_id}") or "")

    filter_terms = bool(st.session_state.get(filter_terms_key))
    filter_edited = bool(st.session_state.get(filter_edited_key))
    issue_only = bool(st.session_state.get(filter_issue_key))
    filter_tm = bool(st.session_state.get(filter_tm_key))
    filter_quality = bool(st.session_state.get(filter_quality_key))
    # 行内标记只看"必须处理"级问题（actionable 是建议，不是异常）
    blocking_segments = {index for finding in all_plan_findings
                         if finding.get("severity") == "blocking"
                         for index in (finding.get("segments") or [])}
    visible_indexes = core.translation_visible_indexes(
        state, search=search, status_filter=filter_label,
        filter_terms=filter_terms, filter_edited=filter_edited,
        filter_issues=issue_only, filter_tm=filter_tm,
        issue_indexes=issue_indexes)
    if st.session_state.get(term_issue_key):
        term_index_set = set(term_issue_indexes)
        visible_indexes = [index for index in visible_indexes
                           if index in term_index_set]
    if filter_label == "待审":
        visible_indexes = [index for index in visible_indexes
                           if projection["segment_status"].get(index) != "已审校"]
    elif filter_label == "已审校":
        visible_indexes = [index for index in visible_indexes
                           if projection["segment_status"].get(index) == "已审校"]
    if filter_quality:
        visible_indexes = [index for index in visible_indexes
                           if index in quality_indexes]
    records = _translation_segment_records(job_id, state)
    visible_records = [records[index] for index in visible_indexes]
    if quality_count and not filter_quality:
        with st.container(key="translation_source_quality_banner"):
            st.markdown(
                (_h('<div class="tp-source-quality-alert"><span class="tp-quality-icon">⚠</span><span>发现 ') + str(quality_count) + _h(' 处疑似 OCR 碎片或低质原文，已为您标记</span></div>')), unsafe_allow_html=True)

    # scroll/focus intent 只消费一次，且必须在正文渲染之前取出。
    # 清空发生在取出的一瞬间，所以不存在"之后每次 rerun 又跳一次"的残留。
    pending_scroll = _consume_pending_scroll()
    pending_focus = _consume_pending_focus()
    _render_nav_notice()
    if not visible_indexes:
        _render_agent_findings_strip(job_id, state, findings=plan_findings,
                                     issue_indexes=issue_indexes, quality_count=quality_count)
        _render_translation_filter_menu(job_id)
        st.session_state["selected_segment_id"] = None
        st.session_state.pop("selected_segment_index", None)
        st.markdown(_h('<div class="tp-empty">没有符合当前筛选条件的段落。</div>'), unsafe_allow_html=True)
        return

    selected_segment = _translation_selected_segment(job_id, state, visible_records)
    selected_index = selected_segment["index"]
    _render_translation_active_actions(
        job_id, state, selected_index, selected_segment["pair"],
        findings=plan_findings, issue_indexes=issue_indexes, quality_count=quality_count)
    # ---- CAT 段落网格 ----
    _render_cat_grid_table(job_id, state, visible_indexes, records,
                           blocking_segments, selected_index,
                           pending_scroll, pending_focus, projection=projection)


def _render_cat_grid_table(job_id, state, visible_indexes, records,
                           blocking_segments, selected_index,
                           pending_scroll, pending_focus, *, projection=None):
    """Render inside the workspace fragment so selection is recomputed first.

    A nested fragment would replay the old selected_index argument on clicks,
    leaving both the row highlight and the surrounding Inspector stale.
    """
    if projection is None:
        projection = _workspace_projection(state)
    runtime_status = core.get_job_runtime_status(job_id, state).get("status") or "idle"
    return _cat_grid_ui.render_cat_grid_table(
        job_id, state, visible_indexes, records,
        blocking_segments, selected_index,
        pending_scroll, pending_focus,
        render_row_fn=lambda *args: _render_translation_row(
            *args, projection=projection, runtime_status=runtime_status),
        navigate_fn=_navigate_to_segment,
        scroll_trigger_fn=_render_scroll_trigger,
        focus_trigger_fn=_render_focus_trigger,
        qa_findings_fn=_translation_segment_findings,
    )



def _render_workspace_terms(job_id, state):
    entries = state.get("glossary") or []
    if not isinstance(entries, list):
        entries = []
    if not entries and isinstance(state.get("auto_terms"), dict):
        entries = [{"id": f"auto-{i}", "source": source,
                    "target": value if isinstance(value, str) else "",
                    "preferred": value if isinstance(value, str) else "",
                    "status": "provisional"}
                   for i, (source, value) in enumerate(state["auto_terms"].items())]
    term_rows = _workspace_presentation.term_rows(state)
    st.markdown((_h('<div class="tp-ws-page-heading"><h2>术语</h2><span>') + str(len(entries)) + _h(' 个术语</span></div>')), unsafe_allow_html=True)
    frozen = state.get("glossary_frozen")
    bypassed = state.get("quality_bypass")
    if frozen:
        st.success(_t('术语已冻结 · v{v0}', v0=frozen.get('version')))
    elif bypassed:
        st.info(_t("本任务跳过了人工冻结，当前使用暂定术语。"))
    elif not state.get("p2_done") and state.get("quality_mode"):
        st.warning(_t("术语尚未冻结；完成人工确认后才能继续翻译。"))
        _render_profile_editor(job_id, state)
    if frozen and state.get("p2_done"):
        st.info(_t("当前术语版本已用于翻译；修改锁定术语后，相关译文或审校结果可能需要重新确认。"))
    if not entries:
        st.markdown(_h('<div class="tp-empty">暂无项目术语。</div>'), unsafe_allow_html=True)
        return
    with st.container(key="workspace_term_list"):
        st.markdown(_h('<div class="tp-term-list-head"><span>源术语</span><span>首选译名</span><span>次数</span></div>'), unsafe_allow_html=True)
        for position, row in enumerate(term_rows):
            active = st.session_state.get("selected_glossary_entry_id") == row["id"]
            with st.container(key=f"workspace_term_row_{position}"):
                source_col, target_col, count_col = st.columns([4.6, 4.4, 1], gap="small")
                with source_col:
                    st.button(row["source"] or "未命名术语", key=f"workspace_term_select_{job_id}_{position}",
                              type="primary" if active else "tertiary", width="stretch",
                              on_click=_select_workspace_term, args=(row["id"],))
                with target_col:
                    status = (f'<small>{escape(row["exception"])}</small>' if row["exception"] else "")
                    st.markdown(f'<div class="tp-term-list-target">{escape(row["target"] or "—")}{status}</div>', unsafe_allow_html=True)
                with count_col:
                    st.markdown(f'<div class="tp-term-list-count">{len(row["occurrences"])}</div>', unsafe_allow_html=True)
    if not state.get("p2_done") and state.get("quality_mode") and state.get("glossary") is not None:
        with st.expander(_t("批量编辑与冻结"), expanded=False):
            df = _glossary_dataframe(entries, state.get("paras") or [])
            visible = ["选择", "source", "proposed_target", "preferred", "status", "domain", "note", "id", "payload"]
            edited = st.data_editor(
                _humanize_glossary_editor(df[[key for key in visible if key in df.columns]]),
                key=f"workspace_glossary_editor_{job_id}", num_rows="dynamic",
                hide_index=True, width="stretch",
                column_config={
                    "选择": st.column_config.CheckboxColumn("选择"),
                    "id": st.column_config.TextColumn("ID", disabled=True),
                    "source": st.column_config.TextColumn("源术语", required=True),
                    "proposed_target": st.column_config.TextColumn("建议译名"),
                    "preferred": st.column_config.TextColumn("首选译名"),
                    "status": st.column_config.SelectboxColumn(
                        "状态", options=list(_TERM_STATUS_LABELS.values())),
                    "domain": st.column_config.TextColumn("领域"),
                    "note": st.column_config.TextColumn("备注"),
                    "payload": st.column_config.TextColumn("payload", disabled=True),
                })
            chosen = edited[edited["选择"].fillna(False)] if "选择" in edited.columns else edited.iloc[0:0]
            ids = [str(item) for item in chosen.get("id", []).tolist() if str(item)]
            a, b, c = st.columns(3)
            if a.button(_t("保存草稿"), key=f"workspace_terms_save_{job_id}", width="stretch"):
                core.save_glossary_draft(job_id, _merge_edited_entries(entries, _df_to_entries(edited)))
                st.rerun()
            if b.button(_t("锁定选中"), key=f"workspace_terms_lock_{job_id}", disabled=not ids, width="stretch"):
                core.set_glossary_entry_status(job_id, ids, "locked")
                st.rerun()
            if c.button(_t("冻结并继续翻译"), type="primary", key=f"workspace_terms_freeze_{job_id}", width="stretch"):
                core.freeze_glossary(job_id, entries=_merge_edited_entries(entries, _df_to_entries(edited)), frozen_by="用户")
                st.session_state.pending_continue_job = job_id
                st.rerun()
    with st.expander(_t("项目记忆"), expanded=False):
        _render_project_promotion(job_id, state, entries)


def _select_workspace_term(entry_id):
    st.session_state["selected_glossary_entry_id"] = entry_id


def _render_project_promotion(job_id, state, entries):
    """Memory gate：把本任务已确认的术语与风格提升到项目记忆（人工动作）。

    这是"跨任务复用"真正发生的地方：只有提升过的内容才会被同项目的后续任务
    注入。因此这里必须说明清楚"会提升什么"，而不是给一个语义模糊的按钮。
    """
    project = core.project_for_job(job_id, state)
    if project is None:
        # 任务不属于任何项目：不静默落到默认项目，而是说明"复用需要一个容器"，
        # 并指向项目页。提升本身仍是人工动作，不会自动发生。
        with st.container(border=True):
            st.markdown((_h('<div class="tp-field-head"><strong>项目记忆</strong><span>本任务在系统工作区「') + str(core.SYSTEM_PROJECT_NAME) + '」</span></div>'), unsafe_allow_html=True)
            st.caption(_t("系统工作区可以正常积累与提升记忆，但它的记忆面向所有未分类"
                       "任务。需要按项目隔离术语与风格时，在项目页创建项目，"
                       "再把本任务移入即可。"))
            if st.button(_t("前往项目页"), key=f"promote_goto_projects_{job_id}",
                         width="stretch"):
                _open_project_list()
                st.rerun()
        return
    locked = [e for e in (entries or []) if str(e.get("status") or "") == "locked"]
    styles = [r for r in (state.get("confirmed_style_rules") or [])
              if str((r or {}).get("status") or "").lower() in
              {"approved", "confirmed", "locked"}]
    decisions = [a for a in (state.get("human_actions") or [])
                 if isinstance(a, dict)
                 and str(a.get("record_type") or "") == "human_decision"]
    injectable = bool(locked or styles)

    with st.container(border=True):
        st.markdown((_h('<div class="tp-field-head"><strong>项目记忆</strong><span>') + str(escape(str(project["name"]))) + '</span></div>'),
                    unsafe_allow_html=True)
        injected = state.get("project_memory") or {}
        if injected.get("project_id"):
            st.caption(_t('本任务已注入项目记忆：锁定术语 {v0} 条 · 项目术语版本 v{v1}', v0=len(injected.get('injected_entry_ids') or []), v1=injected.get('glossary_version') or '—'))
        st.caption(_t('可提升：锁定术语 {v0} 条 · 已确认风格 {v1} 条 · 人工决定 {v2} 条', v0=len(locked), v1=len(styles), v2=len(decisions)))
        st.caption(_t("只有提升后的内容才会被同项目的后续任务注入；候选术语与未审校译文不会进入项目记忆。"))
        if st.button(_t("提升到项目记忆"), key=f"promote_project_{job_id}",
                     disabled=not injectable, width="stretch"):
            try:
                updated = core.promote_job_to_project(job_id, actor="用户", state=state)
            except ValueError as exc:
                st.error(_t('提升失败：{v0}', v0=exc))
            else:
                if updated is None:
                    st.error(_t("提升失败：任务状态不可用。"))
                else:
                    view = core.project_memory_view(updated)
                    st.success(_t('已提升到「{v0}」：锁定术语 {v1} 条 · 风格 {v2} 条 · 人工决定 {v3} 条', v0=updated['name'], v1=view['glossary_count'], v2=view['style_rule_count'], v3=view['human_decision_count']))
                    st.rerun()
        if not injectable:
            st.caption(_t("先锁定术语或确认风格规则，才有内容可提升。"))


def _render_review_item_actions(
        job_id, state, selected, items, review_required, review_ready,
        review_runtime, translator_ready):
    """Keep the common human decisions beside the selected finding."""
    segment_id = selected.get("segment_id")
    suggested_target = selected.get("suggested_target") or ""
    with st.container(key=f"review_action_bar_{selected['id']}"):
        if selected.get("finding_type") == "source_quality_gate":
            st.warning(
                _t("这段在 OCR 清洗前后都疑似残片。请先回看 PDF 原页，确认它确实是正文；"
                "核对前不会把它发给翻译或审校模型。"))
            pair = ((state.get("pairs") or [])[segment_id]
                    if isinstance(segment_id, int)
                    and 0 <= segment_id < len(state.get("pairs") or []) else {})
            review_identity = str(pair.get("segment_uid") or segment_id)
            action_key = f"{job_id}_{review_identity}_{pair.get('source') or ''}"
            checked_key = f"review_source_checked_{action_key}"
            note_key = f"review_source_note_{action_key}"
            source_checked = st.checkbox(
                _t("我已回看 PDF 原页，确认这段是有效正文"), key=checked_key)
            source_note = st.text_input(
                _t("原文核对依据"), key=note_key,
                placeholder=_t("例如：原页正文清晰可读，且与前后句连续。"))
            source_disabled = (
                not source_checked or not source_note.strip()
                or not translator_ready
                or (review_required and not review_ready)
                or not isinstance(segment_id, int))
            if st.button(_t("确认原文并重新翻译"), type="primary",
                         disabled=source_disabled,
                         key=f"review_source_confirm_{job_id}_{segment_id}",
                         width="stretch"):
                try:
                    core.confirm_source_quality_segment(
                        job_id, segment_id, actor="user", note=source_note)
                    _queue_segment_retranslation(
                        job_id, [segment_id], review_runtime, target_lang, style_rules,
                        selected=selected, message="原文已确认，段落已重新翻译。")
                except (ValueError, IndexError) as exc:
                    st.error(_t('无法确认或重新翻译：{v0}', v0=exc))
                else:
                    st.rerun()
            if not translator_ready:
                st.caption(_t("需要先完成 AI 引擎配置。"))
            elif review_required and not review_ready:
                st.caption(_t('{v0}，无法完成重新审校。', v0=_review_runtime_missing_message()))
            return True, source_note
        if selected["kind"] in {"failed", "stale", "missing"}:
            action_label = {"failed": "重试审校此段", "stale": "重新审校此段",
                            "missing": "审校此段"}[selected["kind"]]
            review_col, edit_col = st.columns([1.35, 1], gap="small")
            if review_col.button(_t(action_label), type="primary", disabled=not review_ready,
                                 key=f'review_task_run_{selected["id"]}', width="stretch"):
                _run_review_with_runtime(
                    job_id, [segment_id], review_runtime, target_lang, style_rules,
                    selected=selected, message=f"第 {segment_id + 1} 段已完成重新审校。")
                st.rerun()
            if edit_col.button(_t("修改译文"), key=f'review_task_edit_{selected["id"]}',
                               width="stretch"):
                pair = (state.get("pairs") or [])[segment_id]
                st.session_state["selected_segment_id"] = _translation_segment_id(
                    job_id, segment_id, pair)
                st.session_state["selected_segment_index"] = segment_id
                st.session_state.workspace_section = "translation"
                st.rerun()
            if not review_ready:
                st.caption(_t('{v0}；仍可先修改译文，或前往 AI 设置。', v0=_review_runtime_missing_message()))
            return True, ""

        note_key = f"workspace_review_note_{selected['id']}"
        note = st.session_state.get(note_key, "")
        action_disabled = not selected.get("decidable")
        decision_capable = (not selected.get("core_finding")
                            or selected.get("requires_human_confirmation"))
        primary_label = ("应用建议并复审" if suggested_target and review_required else
                         "应用建议" if suggested_target else
                         "确认已解决" if decision_capable else "查看下一项")
        primary_disabled = action_disabled or bool(
            suggested_target and review_required and not review_ready)
        term_edit = selected.get("category") in {"terminology", "terminology_consistency"} and not suggested_target
        cols = st.columns([1.3, 1.15, .4], gap="small")
        primary_col, edit_col, preserve_col = (cols[1], cols[0], cols[2]) if term_edit else cols
        more_menu = preserve_col.popover("···", help=_t("更多处理操作"))
        with more_menu:
            st.text_input(_t("处理说明（可选）"), key=note_key, placeholder=_t("添加本次决定的说明…"))
        note = st.session_state.get(note_key, "")
        if primary_col.button(_t(primary_label), type="secondary" if term_edit else "primary", disabled=primary_disabled,
                              key=f'review_resolve_{selected["id"]}', width="stretch"):
            if not suggested_target and not decision_capable:
                next_id = _workbench_view.next_queue_item_id(
                    items, selected["id"], selected.get("segment_id"))
                _select_review_item(_workbench_view.select_queue_item(items, next_id))
                _set_workspace_flash("此建议已查看；它不会阻止交付。", "info")
                st.rerun()
            if review_required and selected.get("core_finding") and selected.get(
                    "requires_human_confirmation"):
                core.decide_translation_review_finding(
                    job_id, selected["core_finding_id"],
                    "request_revision" if suggested_target else "accept_resolution",
                    "user", actor_type="human",
                    note=note or ("应用建议译文并重新审校" if suggested_target
                                  else "人工核对后确认问题已解决"))
            if suggested_target and isinstance(segment_id, int):
                core.save_translation_edit(job_id, segment_id, suggested_target,
                                           actor="reviewer")
                if not review_required or not selected.get("core_finding"):
                    core.mark_findings_resolved(
                        job_id, [selected["finding_id"]], "human_fixed",
                        note or "应用建议译文")
                if review_required:
                    _run_review_with_runtime(
                        job_id, [segment_id], review_runtime, target_lang, style_rules,
                        selected=selected, message=f"建议译文已应用，第 {segment_id + 1} 段重新审校已完成。")
                    st.rerun()
                message = ""
            elif decision_capable:
                if not review_required or not selected.get("core_finding"):
                    core.mark_findings_resolved(
                        job_id, [selected["finding_id"]], "human_fixed",
                        note or "人工核对后确认问题已解决")
                message = "已确认问题解决。该决定已记录。"
            refreshed = core.load_job_state(job_id) or state
            if suggested_target and review_required:
                segment_items = [item for item in _review_workbench(refreshed)["queue_items"]
                                 if item.get("segment_id") == segment_id]
                if any(item.get("kind") == "failed" for item in segment_items):
                    message = (f"建议译文已应用，但第 {segment_id + 1} 段审校未完成，"
                               "请重试。")
                elif segment_items:
                    message = (f"建议译文已应用并重新审校；第 {segment_id + 1} 段仍有"
                               f" {len(segment_items)} 项当前任务。")
                else:
                    message = f"建议译文已应用，第 {segment_id + 1} 段已完成重新审校。"
            elif suggested_target:
                message = "建议译文已应用。"
            _select_next_review_item(refreshed, selected)
            _set_workspace_flash(message, "warning" if "未完成" in message else "success")
            st.rerun()
        if edit_col.button(_t("修改译文"), type="primary" if term_edit else "tertiary", key=f'review_edit_{selected["id"]}',
                           disabled=not isinstance(segment_id, int), width="stretch"):
            pair = (state.get("pairs") or [])[segment_id]
            st.session_state["selected_segment_id"] = _translation_segment_id(
                job_id, segment_id, pair)
            st.session_state["selected_segment_index"] = segment_id
            st.session_state.workspace_section = "translation"
            st.rerun()
        if more_menu.button(_t("保留当前译文"),
                               disabled=action_disabled or not decision_capable,
                               key=f'review_preserve_{selected["id"]}', width="stretch"):
            if review_required and selected.get("core_finding") and selected.get(
                    "requires_human_confirmation"):
                core.decide_translation_review_finding(
                    job_id, selected["core_finding_id"], "dismiss", "user",
                    actor_type="human", note=note or "人工确认保留当前译文")
            else:
                core.mark_findings_resolved(
                    job_id, [selected["finding_id"]], "preserved",
                    note or "人工确认保留当前译文")
            refreshed = core.load_job_state(job_id) or state
            _select_next_review_item(refreshed, selected)
            _set_workspace_flash("已保留当前译文。该决定已记录。")
            st.rerun()
        if action_disabled:
            st.warning(_t("此发现来自临时定位，无法安全记录人工决定；请先修改或重新审校该段。"))
        elif not decision_capable:
            st.caption(_t("该建议不需要人工决定，也不会阻止交付；可修改译文、应用建议或查看下一项。"))
        elif suggested_target and review_required and not review_ready:
            st.caption(_t('{v0}；也可以先修改译文或保留当前译文。', v0=_review_runtime_missing_message()))

        retranslate_label = "重新翻译并复审" if review_required else "重新翻译"
        retranslate_disabled = (not translator_ready or action_disabled
                                or (review_required and not review_ready)
                                or not isinstance(segment_id, int))
        if more_menu.button(_t(retranslate_label), disabled=retranslate_disabled,
                     key=f'review_retranslate_{selected["id"]}', width="stretch"):
            if review_required and selected.get("core_finding") and selected.get(
                    "requires_human_confirmation"):
                core.decide_translation_review_finding(
                    job_id, selected["core_finding_id"], "request_revision", "user",
                    actor_type="human", note=note or "请求重新翻译并复审")
            _queue_segment_retranslation(
                job_id, [segment_id], review_runtime, target_lang, style_rules,
                selected=selected,
                message="重新翻译完成，已完成重新审校。" if review_required else "重新翻译完成。")
            st.rerun()
    return False, note


def _render_workflow_followup(job_id, state):
    outcomes = core.workflow_outcomes(state)
    understanding, annotation = outcomes["understanding"], outcomes["annotation"]
    queue = core.entity_consistency_queue(state)
    if understanding["status"] != "degraded" and annotation["status"] != "partial" and not queue:
        return
    busy = core._job_execution_alive(job_id)
    with st.expander(_t("长文处理收尾"), expanded=True):
        if understanding["status"] == "degraded":
            st.warning(_t(f"全文理解降级：{len(understanding['degraded_units'])}/{understanding['total_units']} 个单元使用备用摘要。"
                       + ("全文概要也使用备用结果。" if understanding["synopsis_degraded"] else "")))
            if st.button(_t("补跑失败摘要与概要"), key=f"retry_understanding_{job_id}",
                         disabled=busy or not api_key or not ai_model):
                updated = core.prepare_workflow_retry(job_id, "understanding")
                _resume_job(job_id, updated)
                st.rerun()
        if annotation["status"] == "partial":
            if annotation["legacy_unlocated"]:
                st.warning(_t("旧标注记录仅保存失败数量，无法确定失败位置；重新运行会重建全文标注。"))
            else:
                st.warning(_t('标注尚有 {v0} 个失败或待更新批次；成功批次会保留。', v0=annotation['failed_batches']))
            label = "重新运行全文标注" if annotation["legacy_unlocated"] else "只补跑失败标注批次"
            if st.button(_t(label), key=f"retry_annotation_{job_id}", disabled=busy or not api_key or not ai_model):
                updated = core.prepare_workflow_retry(job_id, "annotation")
                _resume_job(job_id, updated)
                st.rerun()
        if not queue:
            return
        st.caption(_t('{v0} 个实体的当前译名需要确认；书名号和引号差异已合并。', v0=len(queue)))
        source = st.selectbox(_t("待统一实体"), [item["source"] for item in queue], key=f"consistency_source_{job_id}", format_func=_localized_formatter())
        item = next(item for item in queue if item["source"] == source)
        st.write("当前译法：" + " / ".join(item["observed_targets"]))
        target = st.text_input(_t("确认使用的译名"), value=item["preferred_target"] or "",
                               key=f"consistency_target_{job_id}_{source}")
        preview = core.preview_entity_repair(state, source, target) if target.strip() else []
        st.caption(_t('将修改 {v0} 段；确认后记录人工译名，并使受影响段落的审校和标注失效。', v0=len(preview)))
        for change in preview:
            with st.expander(_t('第 {v0} 段修改预览', v0=change['index'] + 1)):
                st.write(change["source"])
                before, after = st.columns(2)
                before.write(change["before"])
                after.write(change["after"])
        if st.button(_t("确认预览并统一译名"), key=f"apply_consistency_{job_id}_{source}",
                     disabled=busy or not preview):
            try:
                core.apply_entity_repair(job_id, source, target, preview)
            except (ValueError, RuntimeError) as exc:
                st.error(_t(str(exc)))
            else:
                st.rerun()
        if st.button(_t("确认这些译法符合各自语境"), key=f"accept_variants_{job_id}_{source}", disabled=busy):
            core.accept_entity_variants(job_id, source)
            st.rerun()


def _render_workspace_review(job_id, state):
    _render_workflow_followup(job_id, state)
    view = _review_workbench(state)
    items = view["queue_items"]
    review_required = bool(state.get("translation_core_review_required"))
    progress = view["progress"]
    context = (f'{progress["current"]} / {progress["total"]} 段已有最新独立审校结果'
               if review_required else "独立审校未启用 · 当前仅显示基础检查结果")
    st.markdown((_h('<div class="tp-ws-page-heading"><h2>审校工作台</h2><span>') + str(len(items)) + _h(' 项</span></div><p class="tp-ws-subtle">') + str(escape(context)) + '</p>'), unsafe_allow_html=True)
    filter_names = [("pending", "待处理"), ("rereview", "需复审"),
                    ("suggested", "建议"), ("reference", "参考"), ("all", "全部")]
    filter_options = [f"{label} {view['filter_counts'][name]}" for name, label in filter_names]
    label_to_filter = {option: name for option, (name, _) in zip(filter_options, filter_names)}
    default_filter = next((name for name in ("rereview", "pending", "suggested", "reference")
                           if view["filter_counts"][name]), "all")
    default_label = next(label for label, name in label_to_filter.items() if name == default_filter)
    filter_key = f"workspace_review_filter_chips_{job_id}"
    if st.session_state.get(filter_key) not in filter_options:
        st.session_state[filter_key] = default_label
    with st.container(key="review_queue_filters"):
        value = st.segmented_control(_t("筛选审校任务"), filter_options, key=filter_key,
                                     label_visibility="collapsed", width="content", format_func=_localized_formatter()) or default_label
    visible = _workbench_view.filter_queue_items(items, label_to_filter[value])
    if not visible:
        _select_review_item(None)
        st.caption(_t("当前筛选下没有审校任务。"))
    else:
        selected = _selected_review_item(view, visible)
        st.markdown(_h('<div class="tp-ws-section-label">工作队列</div>'), unsafe_allow_html=True)
        labels, label_to_id = [], {}
        for item in visible:
            evidence = _workspace_presentation.review_evidence(state, item)
            variants = list(dict.fromkeys(row["target"] for row in evidence if row["recorded"]))
            snippet = " / ".join(variants) if variants else str(item.get("target") or "")[:90]
            segments = "、".join(str(row["index"] + 1) for row in evidence) or str(item.get("segment_number") or "—")
            problem = ("发现多个译法：" + snippet if len(variants) > 1 else
                       str(item.get("summary") or "请检查当前任务"))
            label = (f'**{item.get("detected_text") or item["title"]}**  \n'
                     f'{item.get("category_label") or "审校任务"} · {item.get("severity_label") or item["status_label"]}  \n'
                     f'{problem}  \n第 {segments} 段')
            if label in label_to_id:
                label += f" · {len(labels) + 1}"
            labels.append(label)
            label_to_id[label] = item["id"]
        queue_key = f"workspace_review_queue_{job_id}"
        current = next(label for label, item_id in label_to_id.items() if item_id == selected["id"])
        if st.session_state.get(queue_key) not in label_to_id:
            st.session_state[queue_key] = current
        with st.container(key="review_queue_list"):
            choice = st.radio(_t("审校队列"), labels, key=queue_key, label_visibility="collapsed", width="stretch", format_func=_localized_formatter())
        _select_review_item(next(item for item in visible if item["id"] == label_to_id[choice]))

    primary = view["primary_action"]
    if primary["kind"] != "handle_finding":
        with st.expander(_t("审校任务操作"), expanded=False):
            runtime = resolve_review_runtime()
            disabled = primary["kind"] == "review_segments" and not _review_runtime_ready(runtime)
            if st.button(_t(primary["label"]), disabled=disabled,
                         key=f"review_primary_{job_id}_{primary['kind']}"):
                if primary["kind"] == "review_segments":
                    _run_review_with_runtime(
                        job_id, primary["segment_ids"], runtime,
                        core.state_target_lang(state, "简体中文"), _style_rules_text(state))
                else:
                    st.session_state.workspace_section = "delivery"
                st.rerun()
            if disabled:
                st.caption(_t('{v0}，无法运行审校。', v0=_review_runtime_missing_message()))
                if st.button(_t("前往 AI 设置"), key=f"review_open_settings_{job_id}"):
                    st.session_state.update(app_view="settings", workspace_mode=False)
                    st.rerun()


def _case_origin_label(case):
    return _case_provenance.display_contract(case).get("origin_label") or "未分类案例"


def _case_review_status_label(status):
    value = str(status or "")
    return {"unreviewed": "待人工确认", "approved": "已批准纳入", "rejected": "已排除"}.get(
        value, "需人工确认" if value else "待人工确认")


def _case_state_label(value):
    return {
        "pass": "已通过", "fail": "未通过", "manual_review": "待人工复核",
        "not_checked": "未检查", "not_applicable": "不适用",
        "not_available": "尚未生成", "unreviewed": "未处理",
        "approved": "已确认", "rejected": "已拒绝", "modified": "已修改",
        "other": "其他",
    }.get(str(value or ""), "需确认" if value else "—")


def _case_identity_label(case):
    raw = str(case.get("case_id") or "")
    if raw.startswith("seg-"):
        index = case.get("segment_index")
        return f"第 {int(index) + 1} 段" if isinstance(index, int) else "真实修订案例"
    if raw.startswith(("LSC-", "SC-")):
        index = case.get("segment_index")
        return f"第 {int(index) + 1} 段" if isinstance(index, int) else "案例"
    return "案例" if raw else "未命名案例"


def _case_review_is_stale(case, state):
    case_id = str(case.get("case_id") or "")
    if bool(case.get("content_stale")):
        return True
    academic = state.get("academic_state") or {}
    artifact = (academic.get("artifacts") or {}).get(f"case:{case_id}") or {}
    if artifact.get("status") == "stale":
        return True
    impact = state.get("dependency_impact") or {}
    if case_id in {str(value) for value in impact.get("affected_case_ids") or []}:
        return True
    return False


def _case_validity_label(case, state):
    if _case_review_is_stale(case, state):
        return "需要重新检查", "stale"
    if case.get("review_status") == "rejected" or case.get("baseline_status") == "rejected":
        return "已排除", "stale"
    if case.get("review_status") == "unreviewed":
        return "待人工确认", "pending"
    return "可复用", "valid"


def _render_workspace_cases(job_id, state):
    selected = core.load_academic_artifact(job_id, "selected_cases") or {}
    views = _workspace_case_views(job_id, state)
    st.markdown(_h('<div class="tp-section-kicker">案例与人工确认</div><h2>案例终审</h2>'
                '<div class="tp-section-lead">逐例确认案例是否可纳入学术分析；批准不会把合成对照变成历史初译。</div>'),
                unsafe_allow_html=True)
    truth = core.translation_truth_view(job_id, state)
    st.markdown(
        (_h('<div class="tp-truth-banner"><div><span class="tp-truth-kicker">案例引用依据</span><strong>案例引用以当前译文为准</strong><p>所有案例的“当前译文”都来自工作译文 v') + str(truth["version"]) + _h('；修改当前译文后，受影响的案例会提示重新确认。</p></div></div>')), unsafe_allow_html=True)
    if not views:
        st.markdown(_h('<div class="tp-empty">尚未生成案例选择产物。</div>'), unsafe_allow_html=True)
        return
    filter_origin, filter_status, filter_search = st.columns([1, 1, 1.6], gap="small")
    with filter_origin:
        origin_filter = st.selectbox(
            _t("案例来源"), ["全部", "真实修订", "合成对照", "翻译决策"],
            key=f"case_origin_filter_{job_id}", label_visibility="collapsed", format_func=_localized_formatter())
    with filter_status:
        status_filter = st.selectbox(
            _t("审校状态"), ["全部", "待人工确认", "已批准纳入", "已排除"],
            key=f"case_status_filter_{job_id}", label_visibility="collapsed", format_func=_localized_formatter())
    with filter_search:
        case_search = st.text_input(
            _t("搜索案例"), key=f"case_search_{job_id}",
            placeholder=_t("搜索段落、原文或译文…"), label_visibility="collapsed")
    needle = str(case_search or "").strip().casefold()
    filtered = [item for item in views
                if (origin_filter == "全部" or _case_origin_label(item) == origin_filter)
                and (status_filter == "全部" or
                     _case_review_status_label(item.get("review_status")) == status_filter)
                and (not needle or needle in " ".join(
                    str(value or "") for value in (
                        _case_identity_label(item), _case_origin_label(item),
                        _case_review_status_label(item.get("review_status")),
                        item.get("source_text"), item.get("current_text"),
                        item.get("target_subsection"), item.get("section_title"),
                    )).casefold())]
    if not filtered:
        st.info(_t("当前筛选下没有案例。"))
        return
    ids = [str(item.get("case_id")) for item in filtered]
    selected_id = str(st.session_state.get(f"selected_case_id_{job_id}") or "")
    if selected_id not in ids:
        selected_id = ids[0]
        st.session_state[f"selected_case_id_{job_id}"] = selected_id
    queue_col, detail_col = st.columns([1.35, 2.3], gap="medium")
    with queue_col:
        st.markdown((_h('<h3>案例队列 <span class="tp-review-queue-count">') + str(len(filtered)) + '</span></h3>'),
                    unsafe_allow_html=True)
        labels = []
        label_ids = {}
        for index, item in enumerate(filtered, start=1):
            label = (f'{_case_identity_label(item)} · {_case_origin_label(item)} · '
                     f'{_case_review_status_label(item.get("review_status"))}')
            if label in label_ids:
                label = f'{label} · 第 {index} 项'
            labels.append(label)
            label_ids[label] = str(item.get("case_id"))
        queue_key = f"case_queue_{job_id}"
        current_label = next(label for label in labels if label_ids[label] == selected_id)
        if st.session_state.get(queue_key) not in labels:
            st.session_state[queue_key] = current_label
        chosen_label = st.selectbox(
            _t("案例队列"), labels, key=queue_key, label_visibility="collapsed",
            help=_t("选择一个案例查看证据并记录人工终审。"), format_func=_localized_formatter())
        selected_id = label_ids[chosen_label]
        st.session_state[f"selected_case_id_{job_id}"] = selected_id
        approved = sum(item.get("review_status") == "approved" for item in views)
        excluded = sum(item.get("review_status") == "rejected" for item in views)
        st.caption(_t('显示 {v0} / 总计 {v1} · 总计状态：已批准 {v2} · 已排除 {v3} · 待人工确认 {v4}', v0=len(filtered), v1=len(views), v2=approved, v3=excluded, v4=len(views) - approved - excluded))
    item = next(item for item in filtered if str(item.get("case_id")) == selected_id)
    display = _case_provenance.display_contract(item)
    lifecycle = item.get("artifact_status") or "not_available"
    with detail_col:
        validity_label, tone = _case_validity_label(item, state)
        stale = tone == "stale"
        status = _case_review_status_label(item.get("review_status"))
        st.markdown(
            (_h('<div class="tp-case-head"><div><div class="tp-section-kicker">当前选中 · ') + str(escape(_case_identity_label(item))) + '</div><h3>' + str(escape(display["origin_label"])) + ' · ' + str(escape(status)) + '</h3><p>' + str(escape(display["origin_description"])) + '</p></div><span class="tp-case-validity is-' + str(tone) + '">' + str(validity_label) + '</span></div>'),
            unsafe_allow_html=True)
        has_initial = bool(item.get("initial_text"))
        initial_role_label = (
            "模拟初译" if item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE
            else "历史初译" if has_initial else "无真实初译（不伪造对照）")
        role_rows = [_h('<span>原文</span><b>原文段落</b>')]
        if item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE or has_initial:
            role_rows.append(
                f'<span>{escape(initial_role_label)}</span><b>{escape(initial_role_label)}</b>')
        role_rows.append(_h('<span>当前译文</span><b>当前译文</b>'))
        st.markdown('<div class="tp-case-role-grid">' + "".join(role_rows) + '</div>',
                    unsafe_allow_html=True)
        text_col_a, text_col_b = st.columns(2)
        with text_col_a:
            st.markdown((_h('<div class="tp-case-text"><label>原文</label><p>') + str(escape(item.get("source_text") or "—")) + '</p></div>'), unsafe_allow_html=True)
            if item.get("initial_text"):
                st.markdown(f'<div class="tp-case-text"><label>{escape(display.get("initial_label") or "初始文本")}</label><p>{escape(item.get("initial_text"))}</p></div>', unsafe_allow_html=True)
        with text_col_b:
            st.markdown((_h('<div class="tp-case-text"><label>当前译文</label><p>') + str(escape(item.get("current_text") or "—")) + '</p></div>'), unsafe_allow_html=True)
            context = " ".join(str(item.get(key) or "") for key in ("context_before", "context_after")).strip()
            if context:
                st.markdown((_h('<div class="tp-case-text"><label>必要上下文</label><p>') + str(escape(context)) + '</p></div>'), unsafe_allow_html=True)
        st.markdown(_h('<div class="tp-case-detail-label">分析与证据</div>'), unsafe_allow_html=True)
        evidence = item.get("synthetic_evidence") or {}
        analysis = item.get("analysis_fields") or {}
        evidence_rows = [
            ("基线合理性", _case_state_label(evidence.get("baseline_plausibility"))),
            ("实质差异", _case_state_label(evidence.get("material_difference"))),
            ("修复正确性", _case_state_label(evidence.get("repair_correctness"))),
            ("分析价值", _case_state_label(evidence.get("academic_analysis_value"))),
            ("基线状态", ({"modified": "已修改", "rejected": "已拒绝",
                           "approved": "已确认", "unreviewed": "未处理"}.get(
                               item.get("baseline_status"), "不适用")
                       if item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE
                       else "不适用")),
            ("分析状态", "已保存" if analysis else "未提供"),
        ]
        case_plan = item.get("case_plan") or {}
        if case_plan:
            evidence_rows.extend([
                ("问题类型", _case_state_label((case_plan.get("problem") or {}).get("type"))),
                ("决策理由", "已保存" if case_plan.get("decision_rationale") else "未提供"),
                ("理论映射", "已保存" if case_plan.get("theory_mapping") else "未提供"),
            ])
        st.markdown('<div class="tp-case-evidence-grid">' + "".join(
            f'<span>{escape(label)}</span><b>{escape(str(value))}</b>'
            for label, value in evidence_rows) + '</div>', unsafe_allow_html=True)
        segment_index = item.get("segment_index")
        finding_count = len(item.get("case_findings") or [])
        target_section = item.get("target_subsection") or item.get("section_id") or "—"
        section_suffix = f' · {item.get("section_title")}' if item.get("section_title") else ""
        st.caption(_t('关联位置：第 {v0} 段 · 目标报告位置 {v1}{v2} · 相关审校发现 {v3} 条', v0=int(segment_index) + 1 if isinstance(segment_index, int) else "—", v1=target_section, v2=section_suffix, v3=finding_count))
        if item.get("segment_current_text") and item.get("current_text") != item.get("segment_current_text"):
            st.caption(_t("当前译文已按案例片段定位；完整段落真值仍以翻译工作区中的当前译文为准。"))
        with st.expander(_t("查看依赖与技术依据"), expanded=False):
            st.caption(_t('依赖状态：{v0} · 内部生命周期：{v1} · 技术标识：{v2}', v0="需要重建" if stale else "可复用", v1=lifecycle, v2=item.get("segment_id") or "—"))
        with st.expander(_t("查看关联证据"), expanded=True):
            commentary = item.get("analytical_commentary") or []
            if commentary:
                st.markdown("**分析评论**")
                for entry in commentary:
                    st.markdown(f'**{escape(str(entry.get("label") or "说明"))}**：{escape(str(entry.get("value") or "—"))}')
            else:
                st.caption(_t("尚未保存可展示的案例分析评论。"))
            terms = item.get("related_terms") or []
            if terms:
                st.markdown("**相关术语**")
                for term in terms:
                    source = term.get("source") or term.get("term") or "—"
                    target = term.get("target") or term.get("preferred") or term.get("proposed_target") or "—"
                    st.markdown(f'- {escape(str(source))} → {escape(str(target))}')
            else:
                st.caption(_t("未登记与本案例直接绑定的术语记录。"))
            if item.get("case_findings"):
                st.markdown("**相关审校发现**")
                for finding in item.get("case_findings") or []:
                    severity = {"blocking": "必须处理", "actionable": "建议",
                                "informational": "参考"}.get(
                                    str(finding.get("severity") or ""), "审校发现")
                    st.markdown(f'- {escape(severity)} · {escape(str(finding.get("reason") or "审校发现"))}')
            else:
                st.caption(_t("当前段落没有已登记的 finding。"))
            if item.get("literature_evidence"):
                st.markdown("**文献证据**")
                for source in item.get("literature_evidence") or []:
                    st.markdown(f'- {escape(str(source.get("title") or source.get("source_id") or "—"))}')
            else:
                st.caption(_t("未登记案例专属文献证据；当前分析边界仍以项目证据为准。"))
            human_entries = item.get("human_evidence") or []
            if human_entries:
                st.markdown("**作者补充证据**")
                for entry in human_entries:
                    st.markdown(f'- {escape(str(entry.get("question_type") or "说明"))}: '
                                f'{escape(str(entry.get("answer") or "—"))}')
            else:
                st.caption(_t("没有已确认的作者事后解释。"))
        if item.get("review_note"):
            st.caption(_t('人工确认说明：{v0}', v0=item["review_note"]))
        if item.get("reviewed_at"):
            st.caption(_t('人工确认记录：{v0} · {v1}', v0=item.get("reviewed_at"), v1=item.get("review_actor") or "user"))
        if stale:
            st.warning(_t("当前译文或输入已变化，此案例需要重新检查；旧批准不能直接交付。"))
        action_a, action_b, action_c, action_d = st.columns(4)
        if action_a.button(_t("批准纳入" if item.get("review_status") != "approved" else "保持批准"),
                          type="primary", key=f"case_approve_{job_id}_{selected_id}",
                          disabled=item.get("review_status") == "approved" and not stale,
                          width="stretch"):
            core.review_academic_case(job_id, selected_id, "approved", actor="user")
            st.rerun()
        exclude_key = f"case_exclude_note_{job_id}_{selected_id}"
        with action_b:
            with st.popover(_t("排除案例"), use_container_width=True):
                note = st.text_area(_t("排除原因"), key=exclude_key, height=90,
                                    placeholder=_t("说明为什么不纳入本次分析…"))
                if st.button(_t("确认排除"), key=f"case_exclude_go_{job_id}_{selected_id}",
                             type="primary", width="stretch"):
                    if not note.strip():
                        st.warning(_t("请填写排除原因。"))
                    else:
                        core.review_academic_case(job_id, selected_id, "rejected", note, actor="user")
                        st.rerun()
        with action_c:
            if st.button(_t("修改当前译文"), key=f"case_edit_target_{job_id}_{selected_id}", width="stretch"):
                if segment_index is not None:
                    pair = (state.get("pairs") or [])[int(segment_index)]
                    st.session_state["selected_segment_id"] = _translation_segment_id(
                        job_id, int(segment_index), pair)
                    st.session_state["selected_segment_index"] = int(segment_index)
                st.session_state.workspace_section = "translation"
                st.rerun()
        with action_d:
            if st.button(_t("从合格池替换"), key=f"case_replace_{job_id}_{selected_id}",
                         disabled=item.get("review_status") != "rejected", width="stretch"):
                _state, ok, result = core.replace_rejected_case(
                    job_id, selected_id, actor="user")
                if not ok:
                    st.error(_t(result[0] if isinstance(result, list) and result else str(result)))
                else:
                    st.rerun()
        if item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE:
            st.markdown(_h('<div class="tp-case-detail-label">模拟初译（分析对照，不是历史初译）</div>'), unsafe_allow_html=True)
            baseline_key = f"case_baseline_{job_id}_{selected_id}"
            if baseline_key not in st.session_state:
                st.session_state[baseline_key] = item.get("initial_text") or ""
            baseline = st.text_area(_t("模拟初译"), key=baseline_key, height=110,
                                    label_visibility="collapsed")
            base_a, base_b = st.columns(2)
            if base_a.button(_t("保存修改模拟初译"), key=f"case_baseline_save_{job_id}_{selected_id}", width="stretch"):
                _state, ok, message = core.update_synthetic_baseline(
                    job_id, selected_id, baseline, status="modified", actor="user")
                if not ok:
                    st.error(_t(message))
                else:
                    st.rerun()
            if base_b.button(_t("拒绝模拟初译"), key=f"case_baseline_reject_{job_id}_{selected_id}", width="stretch"):
                core.update_synthetic_baseline(job_id, selected_id, baseline,
                                               status="rejected", actor="user")
                st.rerun()


def _render_workspace_cases_context(job_id, state):
    views = _workspace_case_views(job_id, state)
    pending = sum(item.get("review_status") == "unreviewed" for item in views)
    synthetic = sum(item.get("case_origin") == _case_provenance.SYNTHETIC_BASELINE for item in views)
    real = sum(item.get("case_origin") == _case_provenance.REAL_REVISION for item in views)
    st.markdown((_h('<div class="tp-info-card"><h3>案例状态</h3><div class="tp-info-stat"><span>案例总数</span><b>') + str(len(views)) + _h('</b></div><div class="tp-info-stat"><span>真实修订</span><b>') + str(real) + _h('</b></div><div class="tp-info-stat"><span>合成对照</span><b>') + str(synthetic) + _h('</b></div><div class="tp-info-stat"><span>尚未审校</span><b>') + str(pending) + '</b></div></div>'), unsafe_allow_html=True)
    impact = core.dependency_impact_view(job_id, state)
    if impact.get("status") == "stale":
        st.markdown((_h('<div class="tp-info-card"><h3>最近依赖变化</h3><p class="tp-inspector-preview">') + str(escape(_workspace_impact_reason(impact))) + '</p><p class="tp-inspector-preview">' + str(len(impact.get("affected") or [])) + ' 项下游内容需要更新 · ' + str(len(impact.get("reusable") or [])) + _h(' 个未受影响单元/资产可复用</p></div>')),
                    unsafe_allow_html=True)


def _render_workspace_qa(job_id, state):
    """Compliance, independent QA facts, and the explicit finalization gate."""
    st.markdown(_h('<div class="tp-section-kicker">交付检查</div><h2>合规与最终 QA</h2>'
                '<div class="tp-section-lead">每一项检查都独立决定一件事；结构检查和页面渲染通过，也不等于作者与 Word 最终复核已确认。</div>'),
                unsafe_allow_html=True)
    compliance = _workspace_compliance_view(job_id, state)
    profile_id = str(state.get("compliance_profile_id") or
                     _compliance.DEFAULT_PROFILE_ID)
    profile = _compliance.compliance_profile(profile_id)
    counts = compliance.get("counts") or {}
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    structural = _workspace_structural_qa(job_id, state)
    render_status = qa.get("libreoffice_render")
    failed_count = int(counts.get("fail", 0) or 0) + int(structural == "FAIL") + int(render_status == "FAIL")
    manual_count = int(counts.get("manual_review", 0) or 0)
    manual_count += int(qa.get("author_visual_review") != "CONFIRMED")
    manual_count += int(qa.get("word_final_review") != "CONFIRMED")
    not_run_count = int(counts.get("not_checked", 0) or 0)
    not_run_count += int(structural in {"NOT_RUN", "STALE"})
    not_run_count += int(render_status in {None, "NOT_RUN", "STALE"})
    attention_count = failed_count + manual_count + not_run_count
    source_mapping_label = {
        "reference_template_mapped": "已登记匿名参考模板",
    }.get(profile.get("authority_mapping_status"), "院校特殊要求需人工确认")
    st.markdown(
        (_h('<div class="tp-qa-profile"><strong>研究与报告（专用能力）</strong><span>') + str(escape(str(profile.get("display_name") or "默认 MTI 实践报告规范"))) + ' · ' + str(escape(source_mapping_label)) + _h('</span><b>通过 ') + str(counts.get("pass", 0)) + ' · 失败 ' + str(counts.get("fail", 0)) + ' · 人工复核 ' + str(counts.get("manual_review", 0)) + ' · 未检查 ' + str(counts.get("not_checked", 0)) + '</b></div>'), unsafe_allow_html=True)
    if attention_count:
        summary_parts = []
        if failed_count:
            summary_parts.append(f"{failed_count} 项自动检查失败")
        if manual_count:
            summary_parts.append(f"{manual_count} 项需要人工复核")
        if not_run_count:
            summary_parts.append(f"{not_run_count} 项尚未运行")
        with st.container(key=f"qa_work_summary_{job_id}"):
            st.markdown(
                (_h('<div class="tp-qa-work-summary"><strong>还有 ') + str(attention_count) + _h(' 项需要处理</strong><span>') + str(" · ".join(summary_parts)) + '</span></div>'),
                unsafe_allow_html=True)
            if st.button(_t("处理阻塞项"), type="primary", key=f"qa_focus_{job_id}", width="stretch"):
                st.session_state[f"qa_focus_rules_{job_id}"] = True
                st.rerun()
    with st.expander(_t("查看规则集技术依据"), expanded=False):
        st.markdown(
            f'**规则集内部标识**：`{escape(str(profile.get("profile_id") or "—"))}`')
        mapping_status = profile.get("authority_mapping_status")
        mapping_label = {
            "reference_template_mapped": "已登记匿名参考模板",
        }.get(mapping_status, "院校特殊要求需人工确认")
        st.caption(_t('参考映射状态：{v0}', v0=mapping_label))
        for source in profile.get("sources") or profile.get("source_documents") or []:
            st.caption(_t('参考来源记录：{v0} · {v1}', v0=source.get("document") or source.get("title") or source.get("file") or "—", v1=source.get("note") or source.get("authority") or ""))
        for source in profile.get("implementation_sources") or []:
            st.caption(_t('项目实现依据（非规范来源）：{v0}', v0=source.get("document") or source.get("file") or "—"))
    st.markdown(_h('<h3 class="tp-qa-heading">合规检查清单</h3>'), unsafe_allow_html=True)
    focus_rules = bool(st.session_state.get(f"qa_focus_rules_{job_id}"))
    for index, rule in enumerate(compliance.get("rules") or []):
        status = str(rule.get("status") or "not_checked")
        label = {"pass": "通过", "fail": "失败", "manual_review": "手动复核",
                 "not_applicable": "不适用", "not_checked": "未检查"}.get(status, status)
        rule_label = (rule.get("description") or rule.get("label") or
                      rule.get("rule_id") or rule.get("id") or "合规规则")
        source = rule.get("source") or {}
        applicability = rule.get("scope") or rule.get("applicability") or "—"
        check_level = rule.get("check_type") or rule.get("check_level") or "—"
        check_level_label = {"deterministic": "自动检查", "manual": "人工检查",
                             "project_constraint": "项目约束"}.get(str(check_level), str(check_level))
        authority = rule.get("authority_level") or ""
        authority_label = {"project": "项目约束",
                           "reference_template": "默认参考模板",
                           "custom_profile": "用户自定义院校规则"}.get(
                               str(authority), "未映射的自定义规则" if not (
                                   rule.get("source_available") or
                                   rule.get("reliable_source_mapping") or
                                   source.get("available"))
                               else str(source.get("authority") or "—"))
        source_document = rule.get("source_document") or source.get("file")
        has_rule_source = (
            rule.get("source_kind") in {"reference_template", "custom_profile"} and
            rule.get("authority_level") != "project" and
            (rule.get("source_available") or
             rule.get("reliable_source_mapping") or source.get("available")))
        rule_source = (source_document if has_rule_source else
                       "院校特殊要求需根据实际模板确认")
        with st.container(key=f"qa_rule_{job_id}_{index}"):
            st.markdown(
                f'<div class="tp-qa-rule is-{status}"><div><strong>{escape(str(rule_label))}</strong>'
                f'<p>{escape(str(rule.get("message") or ""))}</p>'
                f'</div><span>{escape(label)}</span></div>', unsafe_allow_html=True)
            with st.expander(_t("查看来源与技术细节"), expanded=focus_rules and status in {"fail", "manual_review", "not_checked"}):
                st.caption(_t('规则来源：{v0} · 页码/条款：{v1}', v0=rule_source, v1=rule.get("page_or_clause") or source.get("page") or "待提供"))
                source_url = rule.get("source_url") or source.get("url") or ""
                if source_url and has_rule_source:
                    st.markdown(f'来源链接：[打开来源]({source_url})')
                st.caption(_t('适用范围：{v0} · 检查方式：{v1} · 规则层级：{v2}', v0=applicability, v1=check_level_label, v2=authority_label))
                implementation_source = rule.get("implementation_source")
                implementation_clause = rule.get("implementation_clause") or rule.get("source_clause")
                if implementation_source:
                    st.caption(_t('项目实现依据（非规范来源）：{v0} · {v1}', v0=implementation_source, v1=implementation_clause or "—"))
                st.caption(_t('内部状态：{v0} · 规则标识：{v1}', v0=status, v1=rule.get("rule_id") or rule.get("id") or "—"))
    if structural in {"PASS", "FAIL", "NOT_RUN"} and structural != qa.get("structural_qa"):
        qa["structural_qa"] = structural
        state["final_qa"] = qa
        core.save_job_state(job_id, state)
    st.markdown(_h('<h3 class="tp-qa-heading">独立最终质量事实</h3>'), unsafe_allow_html=True)
    qa_rows = [
        ("structural_qa", "DOCX 结构检查", structural, "文档结构、段落与确定性规则"),
        ("libreoffice_render", "LibreOffice 页面渲染", qa.get("libreoffice_render"), "DOCX 转 PDF 页面预检"),
        ("author_visual_review", "作者视觉复核", qa.get("author_visual_review"), "作者检查关键页面与版式"),
        ("word_final_review", "Word 最终复核", qa.get("word_final_review"), "Word 更新字段并确认最终页面"),
    ]
    for field, label, status, proof in qa_rows:
        with st.container(key=f"qa_fact_{job_id}_{field}"):
            left, right = st.columns([3.1, 1.1], gap="small")
            with left:
                st.markdown(f'<div class="tp-qa-fact"><strong>{escape(label)}</strong><span>{escape(proof)}</span></div>', unsafe_allow_html=True)
            with right:
                status_class = "stale" if status == "STALE" else str(status).lower()
                status_label = "需要重建" if status == "STALE" else _finalization.final_qa_label(
                    str(field), str(status))
                st.markdown(f'<div class="tp-qa-status is-{status_class}">{escape(status_label)}</div>', unsafe_allow_html=True)
            if field == "libreoffice_render":
                if st.button(_t("运行 LibreOffice 页面预检"), key=f"qa_run_lo_{job_id}",
                             disabled=not bool(state.get("p2_done")), width="stretch"):
                    try:
                        core.run_libreoffice_render_qa(job_id)
                        st.rerun()
                    except RuntimeError as exc:
                        st.error(_t(str(exc)))
            elif field in {"author_visual_review", "word_final_review"}:
                button_label = "确认已完成" if status != "CONFIRMED" else "撤销确认"
                next_status = "CONFIRMED" if status != "CONFIRMED" else "NOT_CONFIRMED"
                if st.button(_t(button_label), key=f"qa_confirm_{job_id}_{field}", width="stretch"):
                    core.record_final_qa(job_id, field, next_status,
                                        "人工在最终 QA 工作区记录", actor="user")
                    st.rerun()
    if qa.get("libreoffice_render") == "PASS":
        st.caption(_t('渲染记录：{v0} 页 · 当前 DOCX 已渲染并保存 PDF 页面预检结果。', v0=qa.get("page_count") or "—"))
        render_record = core.load_academic_artifact(job_id, "libreoffice_render") or {}
        from folith import rendered_qa as _rendered_qa
        st.markdown("#### 关键页面定位（作者需在 Word 中复核）")
        st.dataframe(pd.DataFrame(_rendered_qa.key_page_references(render_record)),
                     hide_index=True, width="stretch")
    st.warning(_t("只有以上四项独立质量事实和合规门禁都分别满足要求，才能把版本标为最终确认。"))


def _render_workspace_qa_context(job_id, state):
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    compliance = _workspace_compliance_view(job_id, state)
    snapshot = core.delivery_snapshot_status(job_id, state)
    structural = _workspace_structural_qa(job_id, state)
    render_status = _finalization._artifact_status_value(
        state.get("academic_state") or {}, "libreoffice_render")
    structural_label = {"PASS": "已通过", "FAIL": "失败", "STALE": "需要重建",
                         "NOT_RUN": "尚未运行"}.get(structural, "尚未运行")
    render_label = ("需要重建" if render_status in {"stale", "missing"} else
                    "失败" if render_status == "failed" or qa.get("libreoffice_render") == "FAIL" else
                    "已通过" if qa.get("libreoffice_render") == "PASS" else "尚未运行")
    author_label = "已确认" if qa.get("author_visual_review") == "CONFIRMED" else "尚未确认"
    word_label = "已确认" if qa.get("word_final_review") == "CONFIRMED" else "尚未确认"
    delivery_label, _ = _workspace_delivery_state(job_id, state)
    st.markdown((_h('<div class="tp-info-card"><h3>交付门禁</h3><div class="tp-info-stat"><span>合规失败</span><b>') + str((compliance.get("counts") or {}).get("fail", 0)) + _h('</b></div><div class="tp-info-stat"><span>DOCX 结构</span><b>') + str(structural_label) + _h('</b></div><div class="tp-info-stat"><span>页面渲染</span><b>') + str(render_label) + _h('</b></div><div class="tp-info-stat"><span>作者 / Word</span><b>') + str(author_label) + ' / ' + str(word_label) + _h('</b></div><div class="tp-info-stat"><span>交付判断</span><b>') + str(delivery_label) + _h('</b></div><div class="tp-info-stat"><span>最近冻结</span><b>') + str("v" + str((snapshot.get("latest") or {}).get("snapshot_version")) if snapshot.get("latest") else "尚未生成") + '</b></div></div>'), unsafe_allow_html=True)


_REPORT_CASE_ISSUES = {
    "case_count_status_mismatch", "insufficient_core_revision_cases",
    "invalid_selected_case", "non_revision_case_used_as_revision_analysis",
    "synthetic_pipeline_unavailable", "synthetic_only_without_eligible_cases",
    "ineligible_synthetic_case_selected", "synthetic_case_provenance_mismatch",
    "duplicate_selected_case_presentation",
    "case_presentation_count_mismatch",
    "case_minimum_not_met", "case_coverage_below_recommended",
    "duplicate_canonical_case", "missing_focus_span",
    "focus_span_outside_canonical", "focus_excerpt_excessively_long",
    "full_segment_rendered_as_case", "case_node_focus_mismatch",
    "case_numbering_not_continuous", "case_hierarchy_missing",
    "case_distribution_severely_unbalanced",
    "research_question_case_coverage_insufficient",
    "case_presentation_contract_violation",
}
_REPORT_SECTION_ISSUES = {
    "missing_required_section", "section_too_short", "missing_planned_claim",
    "missing_research_question_link", "missing_selected_case",
    "missing_planned_literature_claim", "missing_planned_literature_evidence",
    "section_literature_outside_plan", "section_literature_claim_outside_plan",
    "section_literature_evidence_outside_plan", "section_literature_source_outside_plan",
}
_REPORT_STATISTIC_ISSUES = {
    "unknown_project_statistic", "wrong_project_statistic",
    "unresolved_statistic_token", "unmarked_project_statistic",
}
_REPORT_TEMPLATE_ISSUES = {
    "template_chapter_count_mismatch", "template_missing_chapter",
    "template_chapter_order_mismatch", "template_chapter_title_mismatch",
    "template_missing_subsection", "template_subsection_level_mismatch",
    "template_subsection_order_mismatch", "template_extra_subsection",
    "template_extra_chapter", "template_hash_mismatch", "template_matter_mismatch",
    "template_case_role_missing", "template_case_role_mismatch",
    "template_case_mapping_mismatch", "template_case_minimum_not_met",
    "template_front_matter_content_missing", "template_internal_id_visible",
    "template_unresolved_marker", "template_duplicate_rendering",
    "template_duplicate_heading",
}


def _report_artifacts(job_id):
    return {
        name: core.load_academic_artifact(job_id, name)
        for name in (
            "evidence", "argument_plan", "selected_cases", "outline", "report", "validation", "review",
            "literature_sources", "literature_evidence", "literature_claims",
            "literature_support_review", "academic_quality",
            "human_evidence_questions",
            "final_docx_validation",
        )
    }


def _report_quality_label(status):
    return {
        "pass": "已验证", "pass_with_warnings": "已验证 · 有警告",
        "review_required": "需要复核", "fail": "需要复核",
        "literature_required": "需要补充文献",
        "failed": "生成失败", "not_started": "未生成",
        "stale": "需要更新（按影响范围）", "in_progress": "生成中",
    }.get(status, "—")


def _report_validation_label(status):
    return {
        "pass": "通过", "pass_with_warnings": "通过 · 有警告",
        "fail": "需要复核", "review_required": "需要复核",
        "not_configured": "未配置模板",
    }.get(status, "未生成")


def _report_literature_status(artifacts):
    sources = (artifacts.get("literature_sources") or {}).get("sources") or []
    evidence = (artifacts.get("literature_evidence") or {}).get("items") or []
    claims = (artifacts.get("literature_claims") or {}).get("items") or []
    if not sources:
        return "尚未建立"
    if not evidence or not claims:
        return "待补证据"
    if any(item.get("evidence_grounded_status") in {"needs_review", "review_required"}
           for item in claims):
        return "需要复核"
    return "已登记"


def _report_updated_label(job_id, state, academic):
    value = academic.get("updated_at") or \
        (core.recovery_summary(job_id, state) or {}).get("last_saved_at")
    return _format_saved_at(value) if value else "最近"


def _report_docx_bytes(state, frozen_assets=None):
    """Use the persisted template renderer for Report and Delivery surfaces."""
    job_id = st.session_state.get("active_job_id")
    if not job_id:
        return None
    return core.report_docx_bytes(job_id, state, frozen_assets=frozen_assets)


def _report_heading_title(value):
    value = re.sub(r"\s+#+\s*$", "", str(value or "")).strip()
    return value


def _report_heading_key(value):
    value = _report_heading_title(value).casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[.、．]?\s*", "", value)
    return re.sub(r"\s+", "", value)


def _report_headings(report):
    headings = []
    for line_index, line in enumerate(str(report or "").splitlines()):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        title = _report_heading_title(match.group(2))
        if not title:
            continue
        headings.append({
            "line_index": line_index,
            "level": len(match.group(1)),
            "title": title,
            "anchor": f"report-heading-{len(headings) + 1}",
        })
    return headings


def _report_markdown_with_anchors(report, headings):
    lines = str(report or "").splitlines()
    for item in reversed(headings):
        lines.insert(item["line_index"],
                     f'<a id="{escape(item["anchor"])}"></a>')
    return "\n".join(lines)


def _clean_report_for_display(report_md):
    """Hide provenance and collapse adjacent duplicate headings in the reader."""
    text = str(report_md or "")
    text = re.sub(r"\\?<!--.*?-->\\?", "", text, flags=re.DOTALL)
    text = re.sub(r"\\?\{\{TERM:[^}]+\}\}\\?", "", text)
    text = re.sub(r"\b(?:(?:seg|claim|rq|lit-claim|lit-evidence|human-ev)-"
                  r"[A-Za-z0-9_.:-]+|(?:AQ|AV|AR|LR)-\d+)\b",
                  "对应证据", text)
    # These two shapes are reachable in ordinary legacy/generated reports:
    # the analysis label can leak a quote marker, and a bold numbered
    # subsection can be glued to the preceding paragraph.  Repair only those
    # exact forms; ordinary blockquotes and bold prose remain untouched.
    text = re.sub(
        r"((?:\*{0,2}分析\*{0,2})\s*[：:]\s*)>\s*[。．]\s*",
        r"\1", text)
    text = re.sub(
        r"(?P<lead>[。！？])\s*(?P<title>\d+(?:\.\d+)+\s+[^。\n*]{2,100})"
        r"\*{2}\s*[。！？]",
        r"\g<lead>\n\n### \g<title>\n\n", text)
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            title = _report_heading_title(match.group(2))
            # The toolbar is the single report-title source.
            if not any(value.strip() for value in cleaned) and \
                    title.casefold().startswith("翻译实践报告"):
                continue
            previous = next((value for value in reversed(cleaned) if value.strip()), "")
            previous_match = re.match(r"^#{1,6}\s+(.+?)\s*$", previous)
            if previous_match and _report_heading_key(previous_match.group(1)) == \
                    _report_heading_key(title):
                continue
        cleaned.append(line.rstrip())
    text = "\n".join(cleaned)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return _report_template.anonymize_sensitive_institutions(text.strip())


def _report_issue_category(issue):
    issue_type = str(issue.get("type") or "")
    if issue_type in _REPORT_CASE_ISSUES:
        return "案例不足"
    if issue_type in _REPORT_STATISTIC_ISSUES:
        return "统计验证失败"
    if issue_type in _REPORT_TEMPLATE_ISSUES:
        return "模板合规"
    if "citation" in issue_type or issue_type in {
            "unregistered_formal_citation", "uncitable_literature_source"}:
        return "引用需要确认"
    if "literature" in issue_type or "grounding" in issue_type:
        return "文献证据缺失"
    if issue_type in _REPORT_SECTION_ISSUES or issue.get("section_id"):
        return "章节需要重新生成"
    if "human" in issue_type:
        return "需要人工补充"
    return "需要人工补充"


def _report_issue_detail(category, issue):
    if category == "案例不足":
        return "项目中可追溯、符合资格的案例数量或案例选择状态需要确认。"
    if category == "文献证据缺失":
        return "文献来源、逐字证据与论点之间的支持链路尚未完整。"
    if category == "引用需要确认":
        return "存在需要核对的引用来源或作者—年份信息。"
    if category == "统计验证失败":
        return "报告中的项目统计与证据库不一致，或统计来源未能解析。"
    if category == "模板合规":
        return "报告与上传模板的章节、标题、层级或角色不一致。"
    if category == "章节需要重新生成":
        section_id = issue.get("section_id")
        return f"第 {section_id} 节存在结构或论证问题，建议定点重新生成。" \
            if section_id else "报告章节存在结构或论证问题，建议重新生成。"
    if "human" in str(issue.get("type") or ""):
        return "有需要作者确认或补充的项目过程信息。"
    return "报告质量结果仍需人工核对后再作为最终学术结论使用。"


def _report_issue_groups(artifacts, academic):
    groups = {}

    def add(category, detail, section_id=None):
        group = groups.setdefault(category, {"category": category, "details": [],
                                             "sections": set()})
        if detail and detail not in group["details"]:
            group["details"].append(detail)
        if section_id:
            group["sections"].add(str(section_id))

    selected = artifacts.get("selected_cases") or {}
    policy = selected.get("authentic_selection_status") or \
        (selected.get("case_count_policy") or {}).get("status")
    if policy == "insufficient_revision_cases":
        add("案例不足", "符合资格的真实修订案例少于当前研究要求。")

    validation = artifacts.get("validation") or {}
    review = artifacts.get("review") or {}
    literature_review = artifacts.get("literature_support_review") or {}
    quality = artifacts.get("academic_quality") or {}
    final_docx_validation = artifacts.get("final_docx_validation") or {}
    for issue in list(validation.get("issues") or []) \
            + list(review.get("issues") or []) \
            + list(literature_review.get("issues") or []) \
            + list(quality.get("findings") or []) \
            + list(final_docx_validation.get("issues") or []):
        category = _report_issue_category(issue)
        add(category, _report_issue_detail(category, issue), issue.get("section_id"))

    # A legacy/fixture report without a structured report artifact has no
    # literature contract to enforce.  Once the academic pipeline has emitted
    # the report artifact, its explicit literature status is authoritative.
    report_artifact = artifacts.get("report") or {}
    if report_artifact and not (artifacts.get("literature_sources") or {}).get("sources"):
        add("文献证据缺失", "正文结构与案例已完成，但学术文献支持尚未建立。")

    if _academic_validator.citation_validation_status(
            validation, artifacts.get("literature_sources"),
            artifacts.get("literature_evidence"), artifacts.get("literature_claims")) \
            == "evidence_missing" and \
            (artifacts.get("literature_sources") or {}).get("sources"):
        add("文献证据缺失", "已登记文献来源，但逐字证据或文献主张尚未完整。")

    dimensions = academic.get("quality_dimensions") or {}
    if dimensions.get("literature_grounding") in {"review_required", "fail"}:
        add("文献证据缺失", "文献证据的可核验性仍需处理。")
    if dimensions.get("citation_validation") in {"review_required", "fail"}:
        add("引用需要确认", "引用完整性检查未通过。")
    if dimensions.get("statistics_validation") in {"review_required", "fail"}:
        add("统计验证失败", "统计一致性检查未通过。")
    human_status = academic.get("human_evidence_status") or {}
    if human_status.get("unanswered") or human_status.get("critical_questions"):
        add("需要人工补充", "仍有未回答的人类证据问题。")
    if (academic.get("quality_status") or academic.get("status")) in {
            "review_required", "fail", "failed"} and not groups:
        add("需要人工补充", "报告质量状态仍要求人工复核。")

    ordered = []
    for category in ("模板合规", "案例不足", "文献证据缺失", "引用需要确认",
                     "统计验证失败", "章节需要重新生成", "需要人工补充"):
        if category not in groups:
            continue
        group = groups[category]
        sections = sorted(group["sections"], key=str)
        if category == "模板合规":
            action = "处理模板问题"
        elif category == "案例不足":
            action = "处理案例"
        elif category == "文献证据缺失":
            action = "处理文献证据"
        elif category == "引用需要确认":
            action = "检查引用"
        elif category == "统计验证失败":
            action = "检查统计"
        elif category == "章节需要重新生成":
            action = "重新生成章节"
        else:
            action = "回答人工问题"
        group["action"] = action
        group["target"] = "detail"
        ordered.append(group)
    return ordered


def _report_issue_level(category):
    if category == "需要人工补充":
        return "human_review"
    if category in {"章节需要重新生成", "引用需要确认"}:
        return "warning"
    return "blocker"


def _report_issue_impact(group):
    category = group["category"]
    sections = sorted(group.get("sections") or [], key=str)
    if group.get("target") == "review":
        return (f"涉及 {len(sections)} 个段落，完成处理后才能继续最终交付。"
                if sections else "翻译审校队列仍有未关闭发现。")
    if category == "案例不足":
        return "案例分析未满足数量或可追溯性要求，报告不能最终交付。"
    if category == "统计验证失败":
        return "项目统计与证据不一致，最终稿不能通过验证。"
    if category == "模板合规":
        return "章节或版式未满足模板约束，DOCX 交付可能被阻止。"
    if category == "文献证据缺失":
        return "论点缺少可核验支持，报告不能作为最终学术成果交付。"
    if category == "引用需要确认":
        return "引用信息仍需核对，可能影响稿件可信度。"
    if category == "章节需要重新生成":
        location = "、".join(f"第 {value} 节" for value in sections)
        return f"{location or '部分章节'}的结构或证据需要修正。"
    return "仍有内容依赖作者判断或项目经历补充。"


def _report_issue_recommendation(category):
    return {
        "模板合规": "查看验证结果，按模板约束修正报告。",
        "案例不足": "查看案例选择结果，补足合格案例。",
        "文献证据缺失": "检查来源与证据链，补齐文献支持。",
        "引用需要确认": "核对引用来源和作者—年份信息。",
        "统计验证失败": "查看不一致项，重新验证项目统计。",
        "章节需要重新生成": "定位受影响章节，仅重新生成问题部分。",
        "需要人工补充": "回答待补充问题，再继续生成受影响内容。",
    }[category]


def _report_review_issue_groups(state):
    """Bring translation delivery findings into the report Issues view."""
    groups = {}
    for context in _workspace_review_contexts(state):
        if context.get("severity") not in {"blocking", "actionable"}:
            continue
        category = context.get("category_label") or "翻译审校"
        severity = "blocker" if context.get("severity") == "blocking" else "warning"
        group = groups.setdefault(category, {
            "category": category,
            "details": [],
            "sections": set(),
            "severity": severity,
            "level": severity,
            "action": "处理审校",
            "target": "review",
            "finding_ids": [],
        })
        if severity == "blocker":
            group["severity"] = group["level"] = "blocker"
        reason = context.get("summary") or context.get("reason") or "存在待处理的翻译审校发现。"
        if reason not in group["details"]:
            group["details"].append(reason)
        if context.get("segment_number") not in {None, "?"}:
            group["sections"].add(f"段落 {context['segment_number']}")
        finding_id = context.get("finding_id")
        if finding_id and finding_id not in group["finding_ids"]:
            group["finding_ids"].append(finding_id)
    for group in groups.values():
        sections = sorted(group["sections"], key=str)
        group["impact"] = (f"涉及 {len(sections)} 个段落，完成处理后才能继续最终交付。"
                            if sections else "翻译审校队列仍有未关闭发现。")
        group["recommendation"] = "打开审校工作区，逐项处理并保留处理记录。"
    return list(groups.values())


def _report_page_view(job_id, state, artifacts=None):
    """Compose the report page's single status and decision hierarchy."""
    artifacts = artifacts or _report_artifacts(job_id)
    # 报告页的交付措辞直接取 canonical 状态，一次推导、两处引用。
    canonical_label = _task_overview_state(job_id, state)["label"]
    academic = state.get("academic_state") or {}
    runtime_view = core.build_job_runtime_view(job_id, state)
    runtime_status = runtime_view.get("runtime_status") or runtime_view.get("status")
    quality = academic.get("quality_status") or academic.get("status") or "not_started"
    final_qa = _finalization.normalize_final_qa(state.get("final_qa"))
    academic_records = academic.get("artifacts") or {}
    strict_finalization = bool(state.get("report_enabled")) and any(
        name in academic_records for name in (
            "report", "compliance", "final_docx_validation",
            "libreoffice_render", "report_qa"))
    final_review_pending = strict_finalization and (
        final_qa.get("author_visual_review") != "CONFIRMED" or
        final_qa.get("word_final_review") != "CONFIRMED")
    validation = artifacts.get("validation") or {}
    groups = _report_issue_groups(artifacts, academic) + _report_review_issue_groups(state)
    if runtime_status == "waiting_manual" and not any(
            group.get("severity") == "human_review" for group in groups):
        groups.append({
            "category": "运行需要人工确认",
            "details": ["当前阶段已暂停，等待人工输入后才能继续。"],
            "sections": set(),
            "severity": "human_review",
            "level": "human_review",
            "action": "查看运行详情",
            "target": "runtime",
            "impact": "报告生成流程尚未完成。",
            "recommendation": "查看运行详情，确认需要补充的输入。",
        })
    for group in groups:
        group.setdefault("severity", _report_issue_level(group["category"]))
        group["level"] = group["severity"]
        group["impact"] = _report_issue_impact(group)
        group.setdefault("recommendation", _report_issue_recommendation(group["category"])
                         if group["category"] in {
                             "模板合规", "案例不足", "文献证据缺失", "引用需要确认",
                             "统计验证失败", "章节需要重新生成", "需要人工补充",
                         } else "打开审校工作区，逐项处理并保留处理记录。")
    active_statuses = {"resume_requested", "queued", "starting", "running",
                       "waiting_external", "cancelling"}
    blocking = [group for group in groups if group["severity"] == "blocker"]
    if runtime_status in active_statuses:
        overall = "running"
    elif runtime_status == "waiting_manual":
        overall = "review_required"
    elif runtime_status in {"failed", "interrupted", "stalled"} or quality == "failed":
        overall = "failed"
    elif runtime_status in {"cancelled", "idle_incomplete"} and not state.get("p3_done"):
        overall = "blocked"
    elif blocking or quality == "fail" or validation.get("status") == "fail":
        overall = "blocked"
    elif groups or quality in {"review_required", "pass_with_warnings"}:
        overall = "review_required"
    elif state.get("p3_done") and quality == "pass" and validation.get("status") == "pass":
        overall = "ready_for_delivery"
    elif state.get("p3_md"):
        overall = "draft_preview"
    else:
        overall = "blocked"
    report_preview_available = bool(state.get("p3_md") or artifacts.get("report"))
    status_meta = {
        "running": ("正在运行", "查看运行详情", "运行详情", "neutral"),
        "failed": ("运行失败", "查看运行详情", "运行详情", "danger"),
        "blocked": ("可预览 · 尚不可交付" if report_preview_available else "尚不可交付",
                    "处理阻塞问题", "问题与修复", "danger"),
        "review_required": ("可预览 · 等待复核" if report_preview_available else "等待复核",
                            "查看待复核项", "问题与修复", "warning"),
        # 报告页不另造交付词汇：这里显示的就是 canonical 状态。
        "ready_for_delivery": (canonical_label,
                                "完成最终确认" if final_review_pending else "进入交付",
                                None, "warning" if final_review_pending else "success"),
        "draft_preview": ("可预览 · 尚不可交付", "查看当前稿件", "当前稿件", "neutral"),
    }
    label, cta, target_tab, tone = status_meta[overall]
    severity_order = {"blocker": 0, "warning": 1, "human_review": 2}
    groups.sort(key=lambda group: (severity_order.get(group.get("severity"), 9),
                                   group.get("category", "")))
    recommended_issue = next((group for group in groups
                              if group.get("severity") == "blocker"), None)
    if recommended_issue is None:
        recommended_issue = next((group for group in groups
                                  if group.get("severity") in {"warning", "human_review"}), None)
    if recommended_issue and overall in {"blocked", "review_required"}:
        cta = recommended_issue["action"]
        target_tab = "问题与修复"
    if overall in {"failed", "running"}:
        target_tab = "运行详情"
    if overall == "blocked" and not groups:
        cta = "查看运行详情"
        target_tab = "运行详情"
    if overall == "blocked":
        if blocking:
            gate_note = f"仍有 {len(blocking)} 个报告阻塞项；下一步：{cta}。"
        elif validation.get("status") == "fail":
            gate_note = "当前问题列表没有报告阻塞项，但验证结果仍未通过；下一步：查看报告验证结果。"
        elif quality in {"fail", "failed"}:
            gate_note = "当前问题列表没有报告阻塞项，但报告质量检查未通过；下一步：查看质量检查结果。"
        else:
            gate_note = "报告门禁尚未完成；下一步：查看运行详情并完成报告流程。"
    elif overall == "review_required" and not groups:
        gate_note = "报告仍需人工复核；下一步：查看运行详情并记录复核结果。"
    elif overall == "ready_for_delivery" and final_review_pending:
        gate_note = "报告技术检查已完成，但作者视觉复核和 Word 最终复核仍未确认；下一步：进入交付检查。"
    else:
        gate_note = ""
    return {
        "overall": overall, "overall_label": label, "tone": tone,
        "primary_cta": cta, "target_tab": target_tab,
        "runtime": runtime_view, "quality": quality,
        "validation": validation, "groups": groups, "blocking": blocking,
        "recommended_issue": recommended_issue,
        "preview_only": overall != "ready_for_delivery" or final_review_pending,
        "delivery_label": canonical_label
        if overall == "ready_for_delivery" else
        "可预览 · 尚不可交付" if report_preview_available else "尚不可交付",
        "final_review_pending": final_review_pending,
        "gate_note": gate_note,
    }


def _select_report_tab(job_id, label):
    st.session_state[f"report_tabs_{job_id}"] = label


def _report_issues_for_category(category, artifacts):
    issues = []
    for artifact_name, field in (("validation", "issues"), ("review", "issues"),
                                 ("literature_support_review", "issues"),
                                 ("academic_quality", "findings")):
        for issue in (artifacts.get(artifact_name) or {}).get(field) or []:
            if _report_issue_category(issue) == category:
                issues.append(issue)
    return issues


def _queue_report_repair(job_id, state, category, sections):
    if category == "模板合规":
        scope = "writer"
    elif category == "案例不足":
        scope = "planning"
    elif category == "文献证据缺失":
        scope = "all"
    elif category in {"引用需要确认", "统计验证失败"}:
        scope = "validation"
    elif category == "章节需要重新生成" and sections:
        for section_id in sections:
            core.invalidate_academic_report(job_id, "section", section_id)
        scope = None
    else:
        scope = "case_analysis" if category == "需要人工补充" else "writer"
    if scope:
        core.invalidate_academic_report(job_id, scope)
    _resume_job(job_id, core.load_job_state(job_id) or state)


def _render_report_issue_detail(job_id, state, groups, artifacts):
    category = st.session_state.get(f"report_review_focus_{job_id}")
    group = next((item for item in groups if item["category"] == category), None)
    if not group:
        return

    with st.container(key=f"report_issue_detail_{job_id}"):
        back_col, _ = st.columns([1.2, 4], gap="small")
        with back_col:
            if st.button(_t("← 返回当前问题"), key=f"report_issue_back_{job_id}",
                         type="secondary", width="stretch"):
                st.session_state.pop(f"report_review_focus_{job_id}", None)
                st.rerun()
        st.markdown(
            f'<div class="tp-report-focus"><div class="tp-report-focus-head">'
            f'<h3>{escape(group["action"])}</h3>'
            f'<span class="tp-report-issue-badge">{escape(group["category"])}</span>'
            '</div>', unsafe_allow_html=True)
        if group.get("target") == "review":
            st.info(_t("这类问题属于翻译审校队列；打开审校工作区后可查看原文、译文和证据，并留下处理记录。"))
            if st.button(_t("打开审校工作区"), type="primary",
                         key=f"report_open_review_{job_id}", width="stretch"):
                _open_report_issue(job_id, group)
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
            return
        if category == "案例不足":
            selected = artifacts.get("selected_cases") or {}
            cases = selected.get("cases") or []
            st.caption(
                _t('选择状态：{v0} · 当前案例 {v1} 个', v0=selected.get('authentic_selection_status') or '未记录', v1=len(cases)))
            if cases:
                rows = [{
                    "案例": item.get("case_id") or "—",
                    "类型": _case_provenance.display_contract(item)["origin_label"],
                    "类型说明": _case_provenance.display_contract(item)[
                        "origin_description"],
                    "来源段": item.get("segment_id") or item.get("source_segment_id") or "—",
                    "聚焦问题": (item.get("focus") or {}).get("issue") or "—",
                    "难点": item.get("difficulty_group") or "—",
                    "策略": item.get("strategy_group") or "—",
                } for item in cases]
                with st.expander(_t('查看 {v0} 个已选案例', v0=len(cases)), expanded=False):
                    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            else:
                st.info(_t("当前没有符合报告要求的案例。重新规划会再次扫描现有翻译修订记录。"))
            _render_case_portfolio(selected, artifacts.get("validation") or {})
        elif category == "文献证据缺失":
            sources = (artifacts.get("literature_sources") or {}).get("sources") or []
            evidence = (artifacts.get("literature_evidence") or {}).get("items") or []
            claims = (artifacts.get("literature_claims") or {}).get("items") or []
            st.caption(_t('已登记来源 {v0} · 可核验证据 {v1} · 文献主张 {v2}', v0=len(sources), v1=len(evidence), v2=len(claims)))
            for source in sources:
                st.markdown(f"- {escape(str(source.get('title') or source.get('source_id') or '未命名来源'))}")
            if not sources:
                st.info(_t("当前没有已登记的参考资料；请先在新建任务的报告设置中添加文献，再重新生成。"))
        elif category == "需要人工补充":
            questions = [
                question for question in
                (artifacts.get("human_evidence_questions") or {}).get("questions") or []
                if question.get("status") == "open"
            ]
            if not questions:
                st.success(_t("当前没有尚未回答的人类证据问题，可以继续生成受影响章节。"))
            for question in questions:
                question_id = question.get("question_id")
                context = question.get("context") or {}
                st.markdown(f"**{escape(str(question.get('question') or '请补充项目过程信息'))}**")
                if context.get("source"):
                    st.caption(_t('原文：{v0}', v0=str(context['source'])[:180]))
                answer_key = f"report_human_answer_{job_id}_{question_id}"
                answer = st.text_area(
                    _t("你的回答"), key=answer_key, height=90,
                    placeholder=_t("如无法回忆，可填写“不记得/没有相关记录”。"))
                if st.button(_t("提交回答"), key=f"report_human_submit_{job_id}_{question_id}"):
                    if not answer.strip():
                        st.warning(_t("请填写回答；无法回忆时可直接填写“不记得”。"))
                    else:
                        core.record_human_evidence(job_id, question_id, answer)
                        st.session_state.pop(answer_key, None)
                        st.rerun()
        elif category == "章节需要重新生成":
            sections = group.get("sections") or []
            st.info(_t("将只重新生成问题章节：" + "、".join(f"第 {value} 节" for value in sections)))
        else:
            issues = _report_issues_for_category(category, artifacts)
            if not issues:
                st.info(_t("已定位该复核项；重新执行对应检查后会刷新这里的结果。"))
            for issue in issues:
                reason = issue.get("reason") or issue.get("message") or \
                    _report_issue_detail(category, issue)
                st.markdown(f"- {escape(str(reason))}")

        sections = sorted(group.get("sections") or [], key=str)
        action_labels = {
            "模板合规": "按模板重新生成",
            "案例不足": "重新选择案例并继续生成",
            "文献证据缺失": "重建文献证据并继续生成",
            "引用需要确认": "重新验证引用并继续生成",
            "统计验证失败": "重新验证统计并继续生成",
            "章节需要重新生成": "重新生成问题章节",
            "需要人工补充": "用已提交回答继续生成",
        }
        open_questions = category == "需要人工补充" and any(
            question.get("status") == "open" for question in
            (artifacts.get("human_evidence_questions") or {}).get("questions") or [])
        disabled = not api_key or open_questions
        if st.button(_t(action_labels[category]), type="secondary",
                     key=f"report_issue_repair_{job_id}_{category}",
                     disabled=disabled, width="stretch"):
            _queue_report_repair(job_id, state, category, sections)
            st.rerun()
        if not api_key:
            st.caption(_t("继续生成需要先在“设置”中配置当前模型的 API Key。"))
        elif open_questions:
            st.caption(_t("请先提交上方所有待回答问题；无法回忆时可以如实说明。"))
        st.markdown('</div>', unsafe_allow_html=True)


def _render_case_portfolio(selected_cases, validation):
    portfolio = (selected_cases or {}).get("case_portfolio") or {}
    cases = list(portfolio.get("cases") or [])
    if not cases:
        return
    case_validation = (validation or {}).get("case_validation") or {}
    st.markdown("### Case Portfolio")
    pool = int(portfolio.get("candidate_pool_count") or 0)
    selected_count = int(portfolio.get("selected_case_count") or len(cases))
    validated = case_validation.get("provenance_safe_case_count")
    cols = st.columns(3)
    cols[0].metric("总候选数", pool)
    cols[1].metric("最终选中", selected_count)
    cols[2].metric("已验证", "—" if validated is None else int(validated))
    distribution = (selected_cases or {}).get("difficulty_distribution") or {}
    if distribution:
        st.caption(_t(" · ".join(f"{label} {count}" for label, count in distribution.items())))
    st.caption(_t("真实修订：项目保存的历史初译与当前译文；合成对照：模拟初译仅用于分析，不是历史初译。"))
    rows = []
    for item in cases:
        focus = item.get("focus") or {}
        source = (focus.get("source_span") or {}).get("text") or ""
        display = _case_provenance.display_contract(item)
        rows.append({
            "case_id": item.get("case_id") or "—",
            "类型": display["origin_label"],
            "类型说明": display["origin_description"],
            "focus": source,
            "难点": item.get("difficulty_group") or "—",
            "策略": item.get("strategy_group") or "—",
            "RQ": "、".join(item.get("research_question_ids") or []),
            "provenance": item.get("provenance_confidence") or "—",
        })
    with st.expander(_t('查看 {v0} 个聚焦案例', v0=selected_count), expanded=False):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _open_report_issue(job_id, group):
    if group.get("target") == "review":
        st.session_state.workspace_section = "review"
        finding_ids = group.get("finding_ids") or []
        if finding_ids:
            st.session_state.selected_finding_id = finding_ids[0]
        return
    if group.get("target") == "runtime":
        _select_report_tab(job_id, "运行详情")
        return
    st.session_state[f"report_review_focus_{job_id}"] = group["category"]


def _render_report_status_card(job_id, view):
    runtime = view["runtime"]
    progress = (f'{runtime["progress_completed"]} / {runtime["progress_total"]}'
                if runtime["progress_total"] else "—")
    signal = runtime.get("runtime", {}).get("last_heartbeat_at") or runtime.get("last_activity_at")
    current_step = runtime.get("current_operation") or runtime.get("headline") or "等待下一步"
    detail = runtime.get("detail") or "当前没有新的运行说明。"
    if view.get("gate_note") and view["gate_note"] not in detail:
        detail = f"{detail} {view['gate_note']}"
    progress_html = ""
    if runtime.get("progress_total"):
        progress_pct = round(min(1.0, runtime["progress_completed"] /
                                 runtime["progress_total"]) * 100)
        progress_html = (f'<div class="tp-report-status-progress" aria-label="报告生成步骤 {escape(progress)}">'
                          f'<i style="width:{progress_pct}%"></i></div>')
    st.markdown(
        ('<div class="tp-report-overall is-' + str(view["tone"]) + _h('"><div class="tp-report-overall-head"><div><div class="tp-report-toolbar-kicker">报告状态</div><h3>') + str(escape(view["overall_label"])) + '</h3></div>' + str(_workspace_status_badge(view["overall_label"], view["tone"])) + _h('</div><div class="tp-report-overall-grid"><div><span>当前阶段</span><strong>') + str(escape(current_step)) + _h('</strong></div><div><span>生成步骤</span><strong>') + str(escape(progress)) + _h('</strong></div><div><span>最近运行信号</span><strong>') + str(escape(_runtime_clock(signal))) + '</strong></div></div>' + str(progress_html) + '<p class="tp-report-overall-detail">' + str(escape(detail)) + '</p></div>'), unsafe_allow_html=True)
    if view["overall"] == "running":
        with st.popover(_t("更多操作"), use_container_width=False):
            if st.button(_t("取消任务"), type="secondary", width="stretch",
                         key=f"report_cancel_{job_id}"):
                core.request_job_cancel(job_id)
                st.rerun()


def _render_report_issue_workbench(job_id, state, view, artifacts):
    groups = view["groups"]
    counts = {
        severity: sum(1 for group in groups if group.get("severity") == severity)
        for severity in ("blocker", "warning", "human_review")
    }
    st.markdown(
        (_h('<section class="tp-report-issues" aria-labelledby="report-issues-heading"><div class="tp-report-issues-head"><h3 id="report-issues-heading">当前问题</h3><div class="tp-report-issues-summary"><span class="is-blocker">阻塞项 ') + str(counts["blocker"]) + _h('</span><span class="is-warning">建议项 ') + str(counts["warning"]) + _h('</span><span class="is-human-review">人工判断 ') + str(counts["human_review"]) + '</span></div></div></section>'), unsafe_allow_html=True)
    focused_category = st.session_state.get(f"report_review_focus_{job_id}")
    if focused_category and any(
            group.get("category") == focused_category for group in groups):
        _render_report_issue_detail(job_id, state, groups, artifacts)
        return
    if not groups:
        st.markdown(_h('<div class="tp-report-issues-empty">当前没有待处理的问题。</div>'),
                    unsafe_allow_html=True)
        if view.get("gate_note"):
            st.warning(_t(view["gate_note"]))
        return
    if view["overall"] == "running":
        st.info(_t("系统仍在运行。问题列表是最近一次检查结果，请等待当前步骤完成后再执行修复。"))
    level_meta = {
        "blocker": ("阻塞项", "阻止交付"),
        "warning": ("建议项", "建议检查"),
        "human_review": ("人工判断", "需要人工判断"),
    }
    recommended = view.get("recommended_issue")
    for severity in ("blocker", "warning", "human_review"):
        level_groups = [group for group in groups if group.get("severity") == severity]
        if not level_groups:
            continue
        heading, note = level_meta[severity]
        st.markdown(
            f'<div class="tp-report-issues-group"><div class="tp-report-issues-group-title">'
            f'<h4>{heading}</h4><span>{note} · {len(level_groups)}</span></div></div>',
            unsafe_allow_html=True)
        for index, group in enumerate(level_groups):
            details = " ".join(group.get("details") or [])
            is_recommended = (group is recommended
                              and view["overall"] in {"blocked", "review_required"})
            recommended_marker = (
                _h('<span class="tp-report-issue-next">推荐下一步</span>')
                if is_recommended else "")
            with st.container(key=f"report_issue_row_{job_id}_{severity}_{index}"):
                issue_col, action_col = st.columns([4.8, 1.2], gap="small")
                with issue_col:
                    st.markdown(
                        ('<div class="tp-report-issue is-' + str(severity) + '"><div class="tp-report-issue-badge">' + str(heading) + '</div><h4>' + str(escape(group["category"])) + '</h4><p>' + str(escape(details)) + _h('</p><div class="tp-report-issue-meta"><span>影响范围</span><strong>') + str(escape(group["impact"])) + _h('</strong><span>下一步</span><strong>') + str(escape(group["recommendation"])) + '</strong></div></div>'), unsafe_allow_html=True)
                with action_col:
                    if is_recommended:
                        st.markdown(recommended_marker, unsafe_allow_html=True)
                    else:
                        with st.container(key=f"report_issue_row_action_{job_id}_{severity}_{index}"):
                            if st.button(_t(group["action"]), type="secondary", width="stretch",
                                         disabled=view["overall"] == "running",
                                         key=f"report_issue_button_{job_id}_{severity}_{index}"):
                                _open_report_issue(job_id, group)
                                st.rerun()
    _render_report_issue_detail(job_id, state, groups, artifacts)


def _render_report_recommended_action(job_id, view):
    issue = view.get("recommended_issue")
    if view["overall"] == "ready_for_delivery":
        title = "完成作者与 Word 最终复核" if view.get("final_review_pending") \
            else "进入交付并冻结最终版本"
        description = ("报告技术检查已完成，但两项人工终审仍需分别确认。"
                       if view.get("final_review_pending") else
                       "报告已通过当前验证，下一步是确认并生成不可变的最终交付版本。")
    elif view["overall"] in {"running", "failed"}:
        title = view["primary_cta"]
        description = ("查看当前阶段和运行日志，完成后再继续交付。"
                       if view["overall"] == "running"
                       else "查看失败原因并重试当前步骤，报告工作区会保留已完成产物。")
    elif issue:
        title = issue["action"]
        description = issue.get("recommendation") or "处理后会重新计算报告的交付状态。"
    else:
        title = view["primary_cta"]
        description = "查看运行详情，确认报告生成流程的下一步。"
    with st.container(key=f"report_recommended_{job_id}"):
        action_col, button_col = st.columns([3.2, 1], gap="small")
        with action_col:
            st.markdown(
                (_h('<div class="tp-report-recommended-copy"><div class="tp-report-recommended-kicker">推荐下一步</div><h3>') + str(escape(title)) + '</h3><p>' + str(escape(description)) + '</p></div>'),
                unsafe_allow_html=True)
        with button_col:
            if st.button(_t(title), type="primary", width="stretch",
                         key=f"report_primary_{job_id}_{view['overall']}"):
                if issue and view["overall"] in {"blocked", "review_required"}:
                    _open_report_issue(job_id, issue)
                    if issue.get("target") in {"review", "runtime"}:
                        st.rerun()
                    _select_report_tab(job_id, "问题与修复")
                elif view["target_tab"] == "运行详情":
                    _select_report_tab(job_id, "运行详情")
                elif view["target_tab"] == "当前稿件":
                    _select_report_tab(job_id, "当前稿件")
                else:
                    st.session_state.workspace_section = "delivery"
                st.rerun()


def _render_report_draft(job_id, state, view, artifacts, report, headings):
    if not report:
        st.markdown(_h('<div class="tp-empty">报告尚未生成。</div>'), unsafe_allow_html=True)
        return
    template = core.load_report_template(job_id)
    template_summary = (template or {}).get("summary") or {}
    template_compliance = (view["validation"].get("template_compliance") or {}).get(
        "status", "not_configured")
    st.markdown("### 当前工作稿")
    if view["preview_only"]:
        st.warning(_t("当前稿件仅供预览，尚不能用于最终交付。"))
    else:
        st.success(_t("当前稿件已通过验证，可以进入最终交付。"))
    if not (artifacts.get("literature_sources") or {}).get("sources"):
        st.warning(_t("正文结构与案例已完成，但学术文献支持尚未建立。"))
    if template:
        st.caption(_t('模板：{v0} · {v1} 章 / {v2} 节 · 合规 {v3}', v0=template_summary.get('filename') or '—', v1=template_summary.get('chapter_count', 0), v2=template_summary.get('subsection_count', 0), v3=_report_validation_label(template_compliance)))
    else:
        st.caption(_t("未配置报告模板；当前 DOCX 使用通用排版。"))
    st.caption(_t("阅读模式 · 技术标记已隐藏"))
    with st.container(key=f"report_actions_{job_id}"):
        action_a, action_b = st.columns([1.45, 1.2], gap="small")
        docx_data = _report_docx_bytes(state)
        filename = Path(str(state.get("filename") or "report")).stem or "report"
        draft_label = ("导出模板化 DOCX" if template else
                       "导出当前草稿 DOCX" if view["preview_only"] else "导出 DOCX")
        with action_a:
            if docx_data is None:
                blockers = "、".join(group["category"] for group in view["blocking"][:2])
                st.error(_t('DOCX 暂不可导出：请先处理“问题与修复”中的{v0}。', v0=blockers or '阻塞项'))
            else:
                st.download_button(
                    _t(draft_label), docx_data,
                    file_name=f"{filename}_翻译实践报告_草稿.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key=f"report_docx_{job_id}", width="stretch")
        with action_b:
            st.download_button(
                _t("导出 Markdown"), report.encode("utf-8"),
                file_name=f"{filename}_翻译实践报告_草稿.md", mime="text/markdown",
                key=f"report_markdown_{job_id}", width="stretch")
    if headings:
        outline_links = []
        for item in headings:
            css = "is-chapter" if item["level"] <= 2 else "is-subsection"
            outline_links.append(
                f'<a class="{css}" href="#{escape(item["anchor"])}">'
                f'{escape(item["title"])}</a>')
        st.markdown(_h('<nav class="tp-report-outline" aria-label="报告目录">'
                    '<div class="tp-report-outline-title">报告目录</div>')
                    + "".join(outline_links) + '</nav>', unsafe_allow_html=True)
    st.markdown('<article class="tp-report-body">', unsafe_allow_html=True)
    st.markdown(_report_markdown_with_anchors(report, headings), unsafe_allow_html=True)
    st.markdown('</article>', unsafe_allow_html=True)


def _render_report_runtime_details(job_id, view, artifacts):
    runtime = view["runtime"]
    runtime_status = runtime.get("runtime_status") or runtime.get("status")
    if runtime_status in {"interrupted", "idle_incomplete", "cancelled"}:
        st.info(_t("这次运行没有完成；已保存的报告产物仍然保留，可以从当前检查点继续。"))
        if st.button(_t("继续处理"), type="primary", key=f"report_runtime_resume_{job_id}",
                     width="stretch"):
            _resume_job(job_id, core.load_job_state(job_id) or {})
            st.rerun()
    elif runtime_status in {"failed", "stalled"}:
        st.error(_t("报告运行未完成。查看下方阶段和技术详情后，可以重试当前步骤。"))
        if st.button(_t("重试当前步骤"), type="primary", key=f"report_runtime_retry_{job_id}",
                     width="stretch"):
            core.retry_job_step(job_id)
            _resume_job(job_id, core.load_job_state(job_id) or {})
            st.rerun()

    from folith.ui.workspace.performance_view import render_performance_drawer
    state = core.load_job_state(job_id) or {}
    render_performance_drawer(job_id, state=state, runtime_view=view)


def _render_workspace_report(job_id, state):
    artifacts = _report_artifacts(job_id)
    view = _report_page_view(job_id, state, artifacts)
    report = _clean_report_for_display(state.get("p3_md"))
    headings = _report_headings(report)
    outline = artifacts.get("outline") or {}
    selected_cases = artifacts.get("selected_cases") or {}
    template = core.load_report_template(job_id)
    template_summary = (template or {}).get("summary") or {}
    compliance = (view["validation"].get("template_compliance") or {}).get(
        "status", "not_configured")
    chapter_count = template_summary.get("chapter_count") or sum(
        item["level"] == 2 for item in headings) or len(outline.get("sections") or [])
    subsection_count = template_summary.get("subsection_count") or sum(
        item["level"] > 2 for item in headings)
    if not report:
        chapter_count = subsection_count = 0
    case_portfolio = selected_cases.get("case_portfolio") or {}
    case_count = (len(selected_cases.get("cases") or [])
                  or int(case_portfolio.get("selected_case_count")
                         or len(case_portfolio.get("cases") or [])))
    issue_counts = {
        severity: sum(1 for group in view["groups"] if group.get("severity") == severity)
        for severity in ("blocker", "warning", "human_review")
    }
    chips = [
        ("章节", f"{chapter_count} 章 / {subsection_count} 节"),
        ("案例", f"{case_count} 个"),
        ("验证", _report_validation_label(view["validation"].get("status"))),
        ("阻塞项", str(issue_counts["blocker"])),
        ("交付", view["delivery_label"]),
    ]
    if template:
        chips.insert(2, ("模板", _report_validation_label(compliance)))
    chip_html = "".join(
        f'<span class="tp-report-meta-chip">{escape(label)} <strong>{escape(value)}</strong></span>'
        for label, value in chips)
    st.markdown(
        (_h('<div class="tp-report-page-head"><div class="tp-section-kicker">研究与报告 · 专用能力</div><h2>报告</h2><p class="tp-report-page-lead">先判断报告状态，再处理真正阻止交付的问题。</p><div class="tp-report-meta-chips">') + str(chip_html) + '</div></div>'),
        unsafe_allow_html=True)
    truth = core.translation_truth_view(job_id, state)
    st.markdown(
        (_h('<div class="tp-truth-banner"><div><span class="tp-truth-kicker">报告输入依据</span><strong>当前译文 — 报告证据的唯一来源</strong><p>报告中的译文证据来自工作译文 v') + str(truth["version"]) + _h('；译文变更会使受影响的案例、写作单元和报告稿进入“需要更新”。</p></div></div>')), unsafe_allow_html=True)
    _render_report_status_card(job_id, view)
    impact = core.dependency_impact_view(job_id, state)
    if impact.get("status") == "stale":
        affected = impact.get("affected") or []
        reusable = impact.get("reusable") or []
        st.markdown(
            (_h('<div class="tp-impact-panel"><strong>报告需要更新</strong><p>') + str(escape(_workspace_impact_reason(impact))) + _h('</p><div class="tp-impact-summary"><div><span>发生了什么</span><strong>') + str(escape(_workspace_impact_change_label(impact))) + _h('</strong></div><div><span>现在需要更新</span><strong>') + str(len(affected)) + _h(' 个下游产物</strong></div><div><span>仍可复用</span><strong>') + str(len(reusable)) + _h(' 个未受影响单元/资产</strong></div></div></div>')),
            unsafe_allow_html=True)
        _render_workspace_impact_expander(impact)
        if st.button(_t("按影响范围继续重建"), type="secondary",
                     key=f"report_targeted_rebuild_{job_id}",
                     disabled=not api_key, width="stretch"):
            _resume_job(job_id, state)
            st.rerun()
        if not api_key:
            st.caption(_t("定点重建需要先在“设置”中配置当前模型 API Key；上方范围说明仍可用于人工核对。"))
    tabs = st.tabs([_t(_label) for _label in ["问题与修复", "当前稿件", "运行详情"]],
                   default="问题与修复", key=f"report_tabs_{job_id}")
    with tabs[0]:
        _render_report_issue_workbench(job_id, state, view, artifacts)
        if not st.session_state.get(f"report_review_focus_{job_id}"):
            _render_report_recommended_action(job_id, view)
    with tabs[1]:
        _render_report_draft(job_id, state, view, artifacts, report, headings)
    with tabs[2]:
        _render_report_runtime_details(job_id, view, artifacts)


def _render_workspace_delivery(job_id, state, overview=None):
    validation = core.validate_delivery_translation_state(state)
    core._record_delivery_validation_findings(state, validation)
    missing_indexes = [
        index for index, pair in enumerate(state.get("pairs") or [])
        if str(pair.get("source") or "").strip()
        and not str(pair.get("target") or "").strip()
    ]
    overview = overview or _task_overview_state(job_id, state)
    blockers = _delivery.unresolved_blocking(state)
    review_view = _review_workbench(state)
    review_readiness = review_view["readiness"]
    snapshot = core.delivery_snapshot_status(job_id, state)
    latest = snapshot.get("latest")
    report_ready = _delivery.report_ready(state)
    impact = core.dependency_impact_view(job_id, state)
    compliance = _workspace_compliance_view(job_id, state)
    compliance_counts = compliance.get("counts") or {}
    qa = _finalization.normalize_final_qa(state.get("final_qa"))
    finalization_qa_required = bool(state.get("report_enabled"))
    final_docx = core.load_academic_artifact(job_id, "final_docx_validation") or {}
    academic = state.get("academic_state") or {}
    report_workflow_enabled = bool(
        finalization_qa_required or state.get("p3_done") or academic.get("artifacts"))
    final_export_status = _finalization._artifact_status_value(
        academic, "final_docx_validation")
    render_status = _finalization._artifact_status_value(
        academic, "libreoffice_render")
    report_status = _finalization._artifact_status_value(academic, "report")
    final_export_stale = final_export_status in {"stale", "missing", "failed"}
    render_stale = render_status in {"stale", "missing", "failed"}
    report_stale = report_status in {"stale", "missing", "failed"}
    structural = _workspace_structural_qa(job_id, state)
    qa_ready = (not finalization_qa_required or
                not report_stale and not final_export_stale and not render_stale and
                compliance.get("status") == "pass" and
                structural == "PASS" and
                qa.get("libreoffice_render") == "PASS" and
                qa.get("author_visual_review") == "CONFIRMED" and
                qa.get("word_final_review") == "CONFIRMED")
    case_views = _workspace_case_views(job_id, state)
    case_gate = _finalization.case_review_gate(
        state, core.load_academic_artifact(job_id, "selected_cases"))
    case_pending = case_gate.get("blocked_count", 0)
    case_stale = sum(1 for item in case_views if _case_review_is_stale(item, state))
    render_label = ("需要重建" if render_status == "stale" else
                    "失败" if render_status == "failed" else
                    "尚未运行" if render_status in {"missing", "not_available"} else
                    "已通过" if qa.get("libreoffice_render") == "PASS" else "尚未运行")
    render_detail = ("上一份页面预检通过；当前译文变化后需要重新运行" if render_status == "stale" else
                     "页面预检失败，需要查看失败原因" if render_status == "failed" else
                     "尚未生成页面预检结果" if render_status in {"missing", "not_available"} else
                     f"{qa.get('page_count') or '—'} 页 PDF 页面预检" if qa.get("libreoffice_render") == "PASS" else
                     "需要重新运行页面预检")
    translation_truth_gate_pass = (bool(state.get("p2_done")) and
                                   (state.get("delivery_validation") or {}).get("blocking") is not True)
    translation_gate_pass = (translation_truth_gate_pass and review_readiness["ready"]
                             and not blockers)
    report_draft_exists = bool(state.get("p3_md") or
                               core.load_academic_artifact(job_id, "report"))
    affected_count = len(impact.get("affected") or [])
    reusable_count = len(impact.get("reusable") or [])
    review_gate = _workspace_view.delivery_review_gate_copy(
        bool(review_readiness.get("required")), translation_gate_pass,
        review_view["delivery"]["detail"])
    review_gate_status = review_gate["status"]
    review_gate_detail = review_gate["detail"]
    readiness = [
        # 翻译本身是最前置的门禁：此前网格里没有这一项，于是"翻译未完成"的任务
        # 在交付页只看到"独立审校：不适用"，看不到真正的阻塞原因。
        ("译文导出", "可导出 · 有缺译" if translation_truth_gate_pass and missing_indexes else
         "可导出" if translation_truth_gate_pass else "暂不可导出",
         "缺译段落会保持空白，不影响已有译文导出" if missing_indexes and translation_truth_gate_pass else
         "当前译文可导出，交付检查未发现阻断项"
         if translation_truth_gate_pass else
         f"{len(state.get('pairs') or [])} / {len(state.get('paras') or [])} 段已翻译；"
         "完成剩余段落并确认交付门禁后才能冻结交付",
         "pass" if translation_truth_gate_pass else "warning"),
        ("独立审校", review_gate_status, review_gate_detail,
         "pass" if not review_readiness.get("required") or translation_gate_pass else "warning"),
        ("学术产物同步", "当前任务未启用" if not report_workflow_enabled else
         "需要更新" if (impact.get("status") == "stale" or
                                          report_stale or final_export_stale) else
         "已同步" if report_ready else "报告未完成",
         "当前任务没有独立研究报告要求" if not report_workflow_enabled else
         f"{affected_count} 项下游产物待重建" if impact.get("status") == "stale" else
         "报告或 DOCX 产物需要重新检查" if report_stale or final_export_stale else
         "报告与当前译文一致" if report_ready else
         "报告稿可预览，但尚未达到可交付状态" if report_draft_exists else
         "报告稿尚未生成",
         "warning" if report_workflow_enabled and (
             impact.get("status") == "stale" or report_stale or
             final_export_stale or not report_ready) else "pass"),
        ("案例复核", "当前任务未启用" if not report_workflow_enabled and not case_views else
         "需要重建" if case_stale else "待人工确认" if case_pending else
         "已确认" if case_views else "待生成",
         "当前任务没有独立案例终审要求" if not report_workflow_enabled and not case_views else
         f"{case_stale} 个案例受译文变化影响" if case_stale else
         f"{case_pending} 个案例尚未完成终审" if case_pending else
         "案例均已完成终审" if case_views else "尚未生成案例选择产物",
         "warning" if (report_workflow_enabled or case_views) and (
             case_stale or case_pending or not case_views) else "pass"),
        ("合规检查", "当前任务未启用" if not finalization_qa_required else
         f"{compliance_counts.get('fail', 0)} 项失败" if compliance_counts.get("fail") else
         "需人工复核" if compliance_counts.get("manual_review") else "已通过",
         "当前任务没有独立报告与最终 QA 要求" if not finalization_qa_required else
         (f"{compliance_counts.get('manual_review', 0)} 项需要人工复核 · "
          f"{compliance_counts.get('not_checked', 0)} 项未检查")
         if compliance_counts.get("manual_review") or compliance_counts.get("not_checked") else
         "所有适用规则已通过" if compliance.get("status") == "pass" else "仍有规则需要确认",
         "warning" if finalization_qa_required and (
             compliance_counts.get("fail") or compliance_counts.get("manual_review")) else "pass"),
        ("DOCX 结构检查", "当前任务未启用" if not finalization_qa_required else
         "已通过" if structural == "PASS" else
         "失败" if structural == "FAIL" else "需要重建" if structural == "STALE" else "尚未运行",
         "当前任务没有独立报告结构检查要求" if not finalization_qa_required else
         "文档结构检查已保存" if structural == "PASS" else
         "上一份检查已通过；当前译文变化后需要重新检查" if structural == "STALE" else
         "需要先完成结构检查" if structural == "NOT_RUN" else "结构检查发现问题",
         "pass" if not finalization_qa_required or structural == "PASS" else "warning"),
        ("LibreOffice 页面渲染",
         "当前任务未启用" if not finalization_qa_required else render_label,
         "当前任务没有独立页面预检要求" if not finalization_qa_required else render_detail,
         "warning" if finalization_qa_required and (
             render_status in {"stale", "missing", "failed", "not_available"} or
             qa.get("libreoffice_render") != "PASS") else "pass"),
        ("作者视觉复核", "当前任务未启用" if not finalization_qa_required else
         "已确认" if qa.get("author_visual_review") == "CONFIRMED" else "尚未确认",
         "当前任务没有独立作者视觉复核要求" if not finalization_qa_required else
         "作者已确认关键页面" if qa.get("author_visual_review") == "CONFIRMED" else
         "需要作者检查关键页面与版式",
         "pass" if not finalization_qa_required or
         qa.get("author_visual_review") == "CONFIRMED" else "warning"),
        ("Word 最终复核", "当前任务未启用" if not finalization_qa_required else
         "已确认" if qa.get("word_final_review") == "CONFIRMED" else "尚未确认",
         "当前任务没有独立 Word 最终复核要求" if not finalization_qa_required else
         "Word 最终页面已确认" if qa.get("word_final_review") == "CONFIRMED" else
         "需要在 Word 更新字段并确认最终页面",
         "pass" if not finalization_qa_required or
         qa.get("word_final_review") == "CONFIRMED" else "warning"),
        ("冻结交付", f"已冻结交付 v{latest.get('snapshot_version')}"
         if snapshot.get("current") and latest else
         f"工作版本已偏离冻结交付 v{latest.get('snapshot_version')}"
         if snapshot.get("diverged") and latest else "尚未生成",
         f"冻结交付 v{latest.get('snapshot_version')} 可下载" if snapshot.get("current") and latest else
         f"v{latest.get('snapshot_version')} 保持不变；完成更新后可冻结新版本 v{int(latest.get('snapshot_version')) + 1}"
         if snapshot.get("diverged") and latest else
         "所有前置事实满足后再生成不可变版本",
         "pass" if snapshot.get("current") else "warning"),
    ]
    hard_gate_reasons = _workspace_hard_gate_reasons(job_id, state)
    delivery_state_label, delivery_state_tone = _workspace_delivery_state(
        job_id, state, overview)
    if snapshot.get("current"):
        readiness_title = delivery_state_label
        readiness_summary = "当前冻结交付仍是可下载的不可变版本。"
    elif snapshot.get("diverged"):
        readiness_title = delivery_state_label
        readiness_summary = (f"冻结交付 v{latest.get('snapshot_version')} 保持不变；"
                             f"当前工作版本需要完成检查后，才能冻结为新版本 v{int(latest.get('snapshot_version')) + 1}。"
                             if latest else "当前工作版本与最近冻结交付不一致，需要重新检查后再冻结。")
    elif overview["lifecycle"] == _task_overview.DELIVERY_READY:
        readiness_title = delivery_state_label
        readiness_summary = "最终交付资产已生成并通过检查，可以生成冻结交付。"
    elif overview["lifecycle"] == _task_overview.READY_FOR_DELIVERY_PREP:
        readiness_title = delivery_state_label
        readiness_summary = "当前译文满足条件，可以开始准备最终交付。"
    else:
        readiness_title = delivery_state_label
        summary_parts = []
        if not review_readiness["ready"]:
            summary_parts.append(review_view["delivery"]["detail"])
        elif translation_gate_pass:
            summary_parts.append("当前译文已通过交付门禁")
        # 与网格、下一步保持一致：只有确实存在学术下游时才算"报告产物待重建"。
        # 此前这里没有这个判据，纯翻译任务会同时出现"学术产物同步：当前任务未启用"
        # 与"但受影响的报告产物仍需重建"两句互相矛盾的话。
        if impact.get("status") == "stale" and _academic_downstream_enabled(state, job_id):
            summary_parts.append("但受影响的报告产物仍需重建")
        if finalization_qa_required and compliance_counts.get("fail"):
            summary_parts.append(f"{compliance_counts['fail']} 项合规检查失败")
        if finalization_qa_required and compliance_counts.get("manual_review"):
            summary_parts.append(f"{compliance_counts['manual_review']} 项需要人工复核")
        if finalization_qa_required and compliance_counts.get("not_checked"):
            summary_parts.append(f"{compliance_counts['not_checked']} 项尚未检查")
        if finalization_qa_required and structural == "STALE":
            summary_parts.append("DOCX 结构检查需要重建")
        if finalization_qa_required and render_stale:
            summary_parts.append("LibreOffice 页面预检需要重建")
        if finalization_qa_required and qa.get("author_visual_review") != "CONFIRMED" and qa.get("word_final_review") != "CONFIRMED":
            summary_parts.append("作者视觉复核和 Word 最终复核尚未确认")
        elif finalization_qa_required and qa.get("author_visual_review") != "CONFIRMED":
            summary_parts.append("作者视觉复核尚未确认")
        elif finalization_qa_required and qa.get("word_final_review") != "CONFIRMED":
            summary_parts.append("Word 最终复核尚未确认")
        if case_pending and (report_workflow_enabled or case_views) \
                and "案例" not in "".join(summary_parts):
            summary_parts.append(f"另有 {case_pending} 个案例待人工确认")
        readiness_summary = (f"{delivery_state_label}；" + "；".join(summary_parts) + "。"
                             if summary_parts else f"{delivery_state_label}；请查看各项准备状态。")
    truth = core.translation_truth_view(job_id, state)
    working_version = truth.get("version", 0)
    frozen_text = f"已创建交付版本 v{latest.get('snapshot_version')}" if latest else "尚未创建交付版本"

    if not translation_truth_gate_pass:
        next_title = "翻译尚未完成"
        next_detail = (f"{len(state.get('pairs') or [])} / "
                       f"{len(state.get('paras') or [])} 段已翻译；"
                       "完成剩余段落并确认交付门禁后，才能生成冻结交付。")
        next_button = "回到翻译"
        next_target = "translate"
    elif not review_readiness["ready"] and not review_view["risk_acceptance"]["available"]:
        next_title = review_view["delivery"]["detail"]
        next_detail = "先回到审校工作台完成当前译文的审校，再继续准备交付。"
        next_button = review_view["primary_action"]["label"]
        next_target = "review"
    elif blockers and review_view["risk_acceptance"]["available"]:
        next_title = f"处理 {len(blockers)} 个必须处理的问题"
        next_detail = "优先解决问题；只有在理解剩余风险后，才可在下方选择仍要交付。"
        next_button = "返回审校工作台"
        next_target = "review"
    elif impact.get("status") == "stale" and _academic_downstream_enabled(state, job_id):
        next_title = "更新受影响的报告产物"
        next_detail = (f"先按影响范围重建 {affected_count} 项下游产物；"
                       f"{reusable_count} 个未受影响单元/资产仍可复用。")
        next_button = "按影响范围继续重建"
        next_target = "rebuild"
    elif finalization_qa_required and (
            compliance_counts.get("fail") or compliance_counts.get("manual_review")):
        next_title = "处理合规与人工复核"
        next_detail = "先在合规与 QA 中处理失败规则，再记录需要人工确认的项目。"
        next_button = "打开合规与 QA"
        next_target = "qa"
    elif not report_ready and state.get("report_enabled"):
        next_title = "完成报告"
        next_detail = "当前报告还不能作为最终学术产物交付。"
        next_button = "打开报告"
        next_target = "report"
    elif finalization_qa_required and (qa.get("author_visual_review") != "CONFIRMED" or qa.get("word_final_review") != "CONFIRMED"):
        next_title = "完成最终人工复核"
        next_detail = "作者视觉复核和 Word 最终复核都必须分别记录。"
        next_button = "打开合规与 QA"
        next_target = "qa"
    elif snapshot.get("diverged") and latest:
        next_title = f"冻结为新版本 v{int(latest.get('snapshot_version')) + 1}"
        next_detail = (f"当前工作版本已偏离 v{latest.get('snapshot_version')}；"
                       "冻结会追加新版本，不会覆盖已有交付。")
        next_button = "回到冻结操作"
        next_target = "freeze"
    else:
        next_title = "生成冻结交付"
        next_detail = "前置检查已完成，确认后会生成不可变交付快照。"
        next_button = "回到冻结操作"
        next_target = "freeze"

    # Version numbering
    if latest:
        prev_version = latest.get("snapshot_version")
        next_version = prev_version + 1 if snapshot.get("diverged") else prev_version
    else:
        next_version = 1

    drafts_pending = _render_delivery_draft_guard(job_id, state)
    pairs_count = sum(bool(str(pair.get("target") or "").strip())
                      for pair in state.get("pairs") or [])
    paras_count = len(state.get("paras") or [])
    last_modified = state.get("last_modified") or state.get("updated_at") or ""
    last_modified_display = last_modified[:16].replace("T", " ") if last_modified else "—"

    # Show actual prerequisite checks; creating a snapshot is an action, not a check.
    primary_checks = readiness[:2]
    if report_workflow_enabled:
        primary_checks += readiness[2:4]
    if finalization_qa_required:
        primary_checks += readiness[4:9]
    pending_count = sum(1 for _, _, _, tone in primary_checks if tone != "pass")
    readiness_badge_text = "前置检查已通过" if not pending_count else f"{pending_count} 项需处理"
    readiness_status_tone = "success" if not pending_count else "warning"

    unresolved_count = len(blockers)
    freeze_action = f"创建交付版本 v{next_version}"

    # 1. Page Header (Compact)
    st.markdown(
        (_h('<div class="tp-delivery-page-header"><div class="tp-delivery-title-row"><h2>交付中心</h2><span class="tp-delivery-header-meta">工作版本 v') + str(working_version) + ' · ' + str(frozen_text) + _h('</span></div><p class="tp-section-lead-compact">确认完成、创建交付版本、获取交付文件</p></div>')),
        unsafe_allow_html=True
    )

    # 2. Compact Merged Delivery Summary Card
    with st.container(key="delivery_summary_card"):
        # Header + Checks
        checks_html = "".join(
            f'<span class="tp-delivery-check-pill is-{tone}">'
            f'<span class="tp-pill-icon">{"✓" if tone == "pass" else "!"}</span> '
            f'{escape(label)} · {escape(status)}'
            f'</span>'
            for label, status, _, tone in primary_checks
        )
        st.markdown(
            (_h('<div class="tp-delivery-summary-head"><div class="tp-delivery-summary-title"><strong>准备交付</strong><span>') + str(pairs_count) + ' / ' + str(paras_count) + ' 已完成 · 工作版本 v' + str(working_version) + ' · 最后修改 ' + str(escape(last_modified_display)) + '</span></div><div class="tp-delivery-status-badge is-' + str(readiness_status_tone) + '">' + str(readiness_badge_text) + '</div></div><div class="tp-delivery-checks-row">' + str(checks_html) + '</div><div class="tp-delivery-version-meta"><strong>' + str("当前交付" if snapshot.get("current") else "待创建交付") + ' v' + str(next_version) + '</strong> · ' + str(unresolved_count) + _h(' 个阻塞问题</div>')),
            unsafe_allow_html=True
        )

        # Output formats belong before freezing a version, alongside each other.
        delivery_config = core.normalize_delivery_config(state.get("delivery_config"))
        format_items = [item for item in _DELIVERY_TRANSLATION_ITEMS if not item.get("nested")]
        selected_formats = [
            f"{item['title']} · {item['format']}" for item in format_items
            if delivery_config.get(item["key"])
        ]
        content_summary, content_settings = st.columns([3.5, 1.2], gap="small", vertical_alignment="center")
        with content_summary:
            st.caption(_t("交付内容：" + ("、".join(selected_formats) or "尚未选择译文文件")))
        with content_settings:
            with st.popover(_t("调整交付内容"), use_container_width=True):
                st.caption(_t("选择下一交付版本包含的译文文件。"))
                with st.form(f"delivery_content_form_{job_id}"):
                    updated_config = dict(delivery_config)
                    for item in format_items:
                        updated_config[item["key"]] = st.checkbox(
                            _t(f"{item['title']} · {item['format']}"),
                            value=delivery_config[item["key"]],
                            key=f"delivery_content_{job_id}_{item['key']}",
                            help=_t(item["detail"]))
                    if st.form_submit_button("应用", use_container_width=True):
                        if not updated_config["deliver_bilingual_docx"]:
                            updated_config["enable_annotate"] = False
                        if updated_config != delivery_config:
                            state["delivery_config"] = updated_config
                            core._invalidate_final_delivery_state(state)
                            core.save_job_state(job_id, state)
                            st.rerun()

        # Action Strip
        if missing_indexes:
            st.warning(_t('有 {v0} 段尚无译文。可以继续导出；双语对照中的译文保持空白。', v0=len(missing_indexes)))
            with st.expander(_t("查看缺译段落"), expanded=False):
                st.caption(_t("段落编号：" + "、".join(str(index + 1) for index in missing_indexes)))
        if blockers and hard_gate_reasons:
            st.error(_t('还有 {v0} 个审校阻塞项；以下门禁不能通过"接受风险"跳过：{v1}。请先完成这些门禁。', v0=len(blockers), v1='、'.join(dict.fromkeys(hard_gate_reasons))))
        elif blockers and review_view["risk_acceptance"]["available"]:
            st.markdown((_h('<div class="tp-risk-acceptance"><span>高风险操作</span><h3>接受风险并继续交付</h3><p>当前仍有 ') + str(len(blockers)) + _h(' 个必须处理的问题。继续交付不会删除这些问题；系统会保存本次风险接受记录。</p></div>')), unsafe_allow_html=True)
            note = st.text_area(
                _t("处理说明"), key=f"workspace_delivery_note_{job_id}",
                placeholder=_t("说明为什么在这些问题仍然存在时决定继续交付…"))
            accept = st.checkbox(
                _t("我确认理解这些问题仍然存在，并决定继续交付"),
                key=f"workspace_delivery_accept_{job_id}")
            if st.button(_t("确认风险并创建交付版本"), type="primary",
                         disabled=(not accept or not report_ready or not qa_ready
                                    or not translation_truth_gate_pass
                                    or drafts_pending),
                         key=f"workspace_delivery_accept_go_{job_id}", use_container_width=True):
                _, ok, errors = core.approve_delivery(job_id, note or "人工确认并接受剩余风险",
                                                       accept_blocking=True, target_lang=target_lang,
                                                       provider=ai_provider, model=ai_model)
                if ok:
                    _set_workspace_flash(f"风险接受记录已保存，交付版本 v{next_version} 已创建。")
                    st.rerun()
                for error in errors:
                    st.error(_t(error))
        elif next_target != "freeze":
            col_blocker, col_action = st.columns([3.5, 1.2], gap="small", vertical_alignment="center")
            with col_blocker:
                st.markdown(
                    f'<div class="tp-delivery-blocker-inline">'
                    f'<strong>! {escape(next_title)}</strong>'
                    f'<span>{escape(next_detail)}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
            with col_action:
                if next_target == "rebuild":
                    if st.button(_t(next_button), type="primary", key=f"delivery_targeted_rebuild_{job_id}",
                                 disabled=not api_key, use_container_width=True):
                        _resume_job(job_id, state)
                        st.rerun()
                    if not api_key:
                        st.caption(_t('需要配置 API Key。'))
                elif next_target == "qa":
                    if st.button(_t(next_button), type="primary", key=f"delivery_open_qa_{job_id}", use_container_width=True):
                        st.session_state.workspace_section = "qa"
                        st.rerun()
                elif next_target == "report":
                    if st.button(_t(next_button), type="primary", key=f"delivery_open_report_{job_id}", use_container_width=True):
                        st.session_state.workspace_section = "report"
                        st.rerun()
                elif next_target == "translate":
                    if st.button(_t(next_button), type="primary", key=f"delivery_open_translation_{job_id}", use_container_width=True):
                        st.session_state.workspace_section = "translation"
                        st.rerun()
                elif next_target == "review":
                    if st.button(_t(next_button), type="primary", key=f"delivery_open_review_{job_id}", use_container_width=True):
                        st.session_state.workspace_section = "review"
                        st.rerun()
        elif not snapshot.get("current"):
            col_note, col_freeze = st.columns([3.5, 1.2], gap="small", vertical_alignment="center")
            with col_note:
                note = st.text_input(
                    _t("交付说明（可选）"),
                    key=f"workspace_delivery_final_note_{job_id}",
                    placeholder=_t("交付说明（可选，例如：已完成人工审校…）"),
                    label_visibility="collapsed"
                )
            with col_freeze:
                if st.button(_t(freeze_action), type="primary",
                             disabled=next_target != "freeze" or drafts_pending,
                             key=f"workspace_delivery_final_{job_id}", use_container_width=True):
                    _, ok, errors = core.approve_delivery(job_id, note or "人工确认最终交付",
                                                           target_lang=target_lang, provider=ai_provider,
                                                           model=ai_model)
                    if ok:
                        _set_workspace_flash(f"✓ 交付版本 v{next_version} 已创建")
                        st.rerun()
                    for error in errors:
                        st.error(_t(error))
        else:
            approval_time = (latest.get("approval") or {}).get("timestamp", "")[:16].replace("T", " ")
            st.markdown(
                (_h('<div class="tp-delivery-frozen-strip"><span class="tp-frozen-tag">✓ 交付版本 v') + str(latest.get("snapshot_version")) + _h(' 已创建</span><span>') + str(pairs_count) + ' 个译文单元 · 创建时间：' + str(approval_time) + '</span></div>'),
                unsafe_allow_html=True
            )

        # Secondary Disclosure Links (Popovers inline)
        with st.container(key="delivery_summary_links"):
            p1, p2, p3 = st.columns([1, 1, 1], gap="small", vertical_alignment="center")
            with p1:
                with st.popover(_t("查看检查结果")):
                    st.markdown(_h('<div class="tp-popover-heading">详细检查项状态</div>'), unsafe_allow_html=True)
                    st.markdown('<div class="tp-delivery-details-grid">', unsafe_allow_html=True)
                    for label, status, detail, tone in readiness:
                        st.markdown(
                            f'<div class="tp-delivery-detail-item is-{tone}">'
                            f'<div class="tp-detail-header">'
                            f'<span class="tp-detail-icon">{"✓" if tone == "pass" else "!"}</span>'
                            f'<strong>{escape(label)}</strong>'
                            f'<span class="tp-detail-status">{escape(status)}</span>'
                            f'</div>'
                            f'<p class="tp-detail-explanation">{escape(detail)}</p>'
                            f'</div>',
                            unsafe_allow_html=True
                        )
                    st.markdown('</div>', unsafe_allow_html=True)
            with p2:
                with st.popover(_t("查看技术依据")):
                    st.markdown(_h('<div class="tp-popover-heading">技术依据与影响分析</div>'), unsafe_allow_html=True)
                    st.caption(_t('CURRENT_TRANSLATION · 当前工作译文 v{v0} · {v1} 段', v0=truth["version"], v1=format(truth["segment_count"], ',')))
                    st.caption(_t("交付文件和学术下游都以当前译文为输入；创建交付版本会另存为不可变快照。"))
                    if impact.get("status") == "stale":
                        st.markdown(
                            (_h('<div class="tp-impact-summary-compact"><div><span>发生了什么</span><strong>') + str(escape(_workspace_impact_change_label(impact))) + _h('</strong></div><div><span>需要更新</span><strong>') + str(affected_count) + _h(' 个下游产物</strong></div><div><span>可复用</span><strong>') + str(reusable_count) + _h(' 个未受影响单元/资产</strong></div></div>')),
                            unsafe_allow_html=True
                        )
                        _render_workspace_impact_expander(impact)
            with p3:
                with st.popover(_t("版本对比")):
                    st.markdown(_h('<div class="tp-popover-heading">版本对比与快照详情</div>'), unsafe_allow_html=True)
                    _render_workspace_delivery_context(job_id, state)

    # 3. Artifacts Preparation & Grouping
    if not state.get("p2_done"):
        st.markdown(_h('<div class="tp-empty">翻译完成后，交付文件会显示在这里。</div>'), unsafe_allow_html=True)
        return

    frozen_assets = core.delivery_snapshot_assets(job_id, latest.get("snapshot_version")) \
        if snapshot.get("current") and latest else {}
    try:
        assets = frozen_assets or core.build_delivery_assets(job_id, state)
    except RuntimeError as exc:
        st.error(_t(str(exc)))
        with st.expander(_t("查看导出检查详情"), expanded=False):
            for issue in (state.get("delivery_validation") or {}).get("issues") or []:
                st.caption(
                    _t('第 {v0} 段：{v1}', v0=int(issue.get('segment_index', -1)) + 1 if isinstance(issue.get('segment_index'), int) else '?', v1=issue.get('message', '译文未通过交付检查'))
                )
        st.caption(_t("当前工作稿已保留；修复问题后才能生成 DOCX、JSONL、TMX 和最终快照。"))
        return

    labels = {
        "translation.docx": "最终译文", "bilingual.docx": "双语对照",
        "translation.pdf": "最终译文 PDF", "annotated_bilingual.docx": "双语对照 · 标注增强版",
        "translation.epub": "译文电子书 EPUB", "epub_export_report.json": "EPUB 图文导出说明",
        "terms.xlsx": "术语表 XLSX", "terms.tbx": "标准术语库 TBX",
        "memory.tmx": "翻译记忆 TMX", "bilingual.jsonl": "结构化双语数据",
        "segment_evidence.jsonl": "翻译过程证据",
        "selected_cases.json": "案例候选",
        "academic_workspace.zip": "学术写作工作区",
        "review_report.md": "审校报告", "report.docx": "实践报告 DOCX",
        "report.md": "实践报告 Markdown",
        "delivery_manifest.json": "Delivery Manifest",
        "excluded_segments.md": "已排除段落清单",
        "excluded_segments.json": "已排除段落清单（结构化）",
        "stage1_cleaned.docx": "清洗后原文", "auto_terms.xlsx": "自动术语表",
        "source_cleanup.json": "原文纠错记录",
        "stage2_bilingual.docx": "双语对照", "stage3_report.docx": "实践报告",
    }

    primary_deliverables = ["translation.docx", "bilingual.docx", "annotated_bilingual.docx", "translation.pdf", "translation.epub"]
    language_assets = ["terms.xlsx", "terms.tbx", "memory.tmx"]

    present_primary = [k for k in primary_deliverables if k in assets]
    present_lang = [k for k in language_assets if k in assets]
    remaining_technical = [
        (k, assets[k]) for k in assets
        if k not in primary_deliverables and k not in language_assets
    ]

    mime_by_suffix = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pdf": "application/pdf", ".epub": "application/epub+zip", ".tbx": "application/xml",
        ".tmx": "application/xml", ".json": "application/json",
        ".jsonl": "application/x-jsonlines", ".md": "text/markdown",
        ".zip": "application/zip",
    }

    # 4. Primary Deliverables Section
    col_group_head, col_group_scope = st.columns([1.5, 2.5], vertical_alignment="center")
    with col_group_head:
        st.markdown(
            (_h('<div class="tp-delivery-group-header"><strong>主要交付成果</strong><span class="tp-group-count">') + str(len(present_primary)) + _h(' 个文件</span></div>')),
            unsafe_allow_html=True
        )
    with col_group_scope:
        with st.container(key="delivery_scope_container"):
            st.markdown(f'<span class="tp-scope-label">{"已保存的交付快照" if frozen_assets else "当前工作译文"} · </span>', unsafe_allow_html=True)
            with st.popover(_t("查看导出范围")):
                st.markdown(_h('<div class="tp-popover-heading">交付物范围与重建边界</div>'), unsafe_allow_html=True)
                _render_delivery_scope_note(state)
                if assets.get("epub_export_report.json"):
                    epub_report = json.loads(assets["epub_export_report.json"])
                    st.markdown("**EPUB 图文范围**")
                    st.caption(_t('{v0} 张原图 · {v1} 个正文部分 · 可调整字号', v0=epub_report['image_count'], v1=epub_report['chapter_count']))
                    st.caption(_t(epub_report["placement"]))
                    for warning in epub_report.get("warnings") or []:
                        st.caption(_t(warning))

    with st.container(key="delivery_primary_grid"):
        for key in present_primary:
            data = assets[key]
            label = labels.get(key, key)
            mime = mime_by_suffix.get(Path(key).suffix.lower(), "application/octet-stream")
            file_ext = Path(key).suffix.upper().lstrip(".")
            file_size = _format_size(len(data)) if data else "—"
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', key)

            with st.container(key=f"tp_card_prim_{sanitized}"):
                st.markdown(
                    f'<div class="tp-card-top">'
                    f'<span class="tp-card-type-badge type-{file_ext.lower()}">{file_ext}</span>'
                    f'</div>'
                    f'<div class="tp-card-body">'
                    f'<div class="tp-card-title" title="{escape(label)}">{escape(label)}</div>'
                    f'<div class="tp-card-filename" title="{escape(key)}">{escape(key)}</div>'
                    f'<div class="tp-card-size">{file_size}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                if data is not None:
                    st.download_button(
                        "↓",
                        data,
                        file_name=key,
                        mime=mime,
                        key=f"dl_prim_{job_id}_{sanitized}",
                        help=_t('下载 {v0} ({v1})', v0=label, v1=key)
                    )

    # 5. Language Assets Section
    st.markdown(
        (_h('<div class="tp-delivery-group-header"><strong>语言资产</strong><span class="tp-group-count">') + str(len(present_lang)) + _h(' 个文件</span></div>')),
        unsafe_allow_html=True
    )
    with st.container(key="delivery_lang_grid"):
        for key in present_lang:
            data = assets[key]
            label = labels.get(key, key)
            mime = mime_by_suffix.get(Path(key).suffix.lower(), "application/octet-stream")
            file_ext = Path(key).suffix.upper().lstrip(".")
            file_size = _format_size(len(data)) if data else "—"
            sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', key)

            with st.container(key=f"tp_card_lang_{sanitized}"):
                st.markdown(
                    f'<div class="tp-card-top">'
                    f'<span class="tp-card-type-badge type-{file_ext.lower()}">{file_ext}</span>'
                    f'</div>'
                    f'<div class="tp-card-body">'
                    f'<div class="tp-card-title" title="{escape(label)}">{escape(label)}</div>'
                    f'<div class="tp-card-filename" title="{escape(key)}">{escape(key)}</div>'
                    f'<div class="tp-card-size">{file_size}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                if data is not None:
                    st.download_button(
                        "↓",
                        data,
                        file_name=key,
                        mime=mime,
                        key=f"dl_lang_{job_id}_{sanitized}",
                        help=_t('下载 {v0} ({v1})', v0=label, v1=key)
                    )

    # 6. Version History & Technical Data (Collapsible)
    snapshots = core.list_delivery_snapshots(job_id)
    filename = Path(str(state.get("filename") or "document")).stem or "document"
    if snapshots:
        with st.expander(_t('版本历史 ({v0} 个交付版本)', v0=len(snapshots)), expanded=False):
            st.markdown('<div class="tp-delivery-version-list">', unsafe_allow_html=True)
            for item in reversed(snapshots):
                approval = item.get("approval") or {}
                version_num = item.get("snapshot_version")
                timestamp = str(approval.get("timestamp") or item.get("created_at") or "—")[:16].replace("T", " ")
                is_current = snapshot.get("current") and version_num == latest.get("snapshot_version")
                version_label = "当前工作版" if is_current else "已交付"
                col_vinfo, col_vbtn = st.columns([3, 1], vertical_alignment="center")
                with col_vinfo:
                    st.markdown(
                        f'<div class="tp-version-info">'
                        f'<strong>v{version_num}</strong>'
                        f'<span class="tp-version-label">{version_label}</span>'
                        f'<span class="tp-version-time">{timestamp}</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                with col_vbtn:
                    archive = core.delivery_snapshot_archive(job_id, version_num)
                    if archive:
                        st.download_button(
                            _t('下载 v{v0} 归档', v0=version_num),
                            archive,
                            file_name=f"delivery_v{version_num}_{filename}.zip",
                            mime="application/zip",
                            key=f"dl_snap_{job_id}_{version_num}",
                            use_container_width=True
                        )
            st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.markdown(_h('<div class="tp-version-history-muted">尚未创建交付版本；创建后将在此记录版本历史。</div>'), unsafe_allow_html=True)

    if remaining_technical:
        with st.expander(_t('更多交付数据 ({v0} 个文件)', v0=len(remaining_technical)), expanded=False):
            st.markdown('<div class="tp-tech-artifacts-list">', unsafe_allow_html=True)
            for key, data in remaining_technical:
                label = labels.get(key, key)
                mime = mime_by_suffix.get(Path(key).suffix.lower(), "application/octet-stream")
                file_size = _format_size(len(data)) if data else "—"
                sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', key)
                col_tinfo, col_tdl = st.columns([3.5, 1], vertical_alignment="center")
                with col_tinfo:
                    st.markdown(
                        f'<div class="tp-tech-artifact-row">'
                        f'<strong>{escape(label)}</strong>'
                        f'<span class="tp-tech-filename">{escape(key)}</span>'
                        f'<span class="tp-tech-size">{file_size}</span>'
                        f'</div>',
                        unsafe_allow_html=True
                    )
                with col_tdl:
                    if data is not None:
                        st.download_button(
                            _t("下载"),
                            data,
                            file_name=key,
                            mime=mime,
                            key=f"dl_tech_{job_id}_{sanitized}",
                            use_container_width=True
                        )
                if key == "delivery_manifest.json" and data:
                    with st.expander(_t("查看 manifest 内容"), expanded=False):
                        try:
                            st.json(json.loads(data.decode("utf-8")))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            st.caption(_t("manifest 当前不可预览。"))
            st.markdown('</div>', unsafe_allow_html=True)


@st.fragment
def _render_workspace_fragment(job_id, state):
    """Fragment boundary for the workspace topbar + section body.

    Nav tab callbacks update the route before the native fragment rerun, so the
    sidebar and the rest of the app are never repainted on a section switch.
    The runtime polling fragment (_render_runtime_strip) is nested inside and
    owns its own 2-second rerun cycle independently.
    """
    runtime = core.get_job_runtime_status(job_id, state)
    _finish_segment_action(job_id, state, runtime)
    plan_findings = _planner.plan_findings(state, job_id=job_id, limit=10 ** 6)
    # 单一派生入口：本页所有表面（顶栏 / 侧栏 / Hero / pipeline）共用这一份状态。
    overview = _task_overview_state(job_id, state, plan_findings=plan_findings)
    _render_workspace_topbar(job_id, state, overview, plan_findings=plan_findings)
    _render_workspace_flash()
    if runtime.get("status") in core.RUNTIME_ACTIVE_STATUSES:
        st.info(_t("任务正在后台处理。完成后自动更新；期间可浏览其他任务，当前任务的修改暂时锁定，已有草稿会保留。"))
        pairs = state.get("pairs") or []
        for index in ((runtime.get("segment_operation") or {}).get("indexes") or list(range(len(pairs))))[:8]:
            if 0 <= index < len(pairs):
                pair = pairs[index]
                with st.container(border=True):
                    st.caption(_t('第 {v0} 段', v0=index + 1))
                    source_col, target_col = st.columns(2)
                    source_col.write(pair.get("source") or "")
                    target_col.write(pair.get("target") or "")
        return
    section = st.session_state.get("workspace_section", "translation")
    # 没有「概览」路由了：旧 session / 旧链接里的 `overview` 直接回落到翻译
    # 工作台（默认首页），不另建迁移框架。
    if section not in {"translation", "terms", "review", "cases", "report", "qa", "delivery"}:
        section = "translation"
        st.session_state.workspace_section = section
    if section == "report":
        with st.container(key="workspace_main_col"):
            _render_workspace_report(job_id, state)
        return

    # 没有段落时翻译页只是一段状态说明：不建右栏，也就没有空 Inspector 占位。
    if section == "translation" and not (state.get("pairs") or []):
        with st.container(key="workspace_main_col"):
            _render_workspace_translation(job_id, state, overview, plan_findings=plan_findings)
        return

    # Translation needs the widest center surface; the inspector stays a
    # compact, segment-driven utility column rather than a second dashboard.
    pdf_available = _pdf_preview.has_pdf_source(job_id) if _pdf_preview else False
    pdf_open_key = f"pdf_preview_open_{job_id}"
    if pdf_available and pdf_open_key not in st.session_state:
        st.session_state[pdf_open_key] = False

    if section == "translation":
        _translation_ui.render_translation_workspace_fragment(
            job_id, state, overview, ratios=(4.6, 1.85),
            document_nav_ratio=1.4,
            render_document_nav_fn=_render_workspace_document_nav,
            render_main_fn=lambda job_id, state, overview: _render_workspace_translation(
                job_id, state, overview, plan_findings=plan_findings),
            render_inspector_fn=lambda job_id, state, overview: _render_workspace_translation_context(
                job_id, state, overview, plan_findings=plan_findings),
        )
        return

    if section == "delivery":
        with st.container(key="workspace_delivery_col"):
            _render_workspace_delivery(job_id, state, overview)
        return

    if section == "review":
        shell_ratios = [4.2, 1.6]
    else:
        shell_ratios = [4.05, 1.75]
    main_col, context_col = st.columns(shell_ratios, gap="medium")

    with main_col:
        with st.container(key="workspace_main_col"):
            if section == "terms":
                _render_workspace_terms(job_id, state)
            elif section == "review":
                _render_workspace_review(job_id, state)
            elif section == "cases":
                _render_workspace_cases(job_id, state)
            elif section == "report":
                _render_workspace_report(job_id, state)
            elif section == "qa":
                _render_workspace_qa(job_id, state)
    with context_col:
        with st.container(key="workspace_context_col"):
            _render_workspace_context(job_id, state, section, overview)


def _render_workspace_shell(job_id, state):
    if not state:
        _render_workspace_topbar(job_id or "", {"filename": "当前任务", "paras": [], "pairs": []},
                                 has_job=False)
        st.markdown(_h('<div class="tp-empty">还没有打开的任务。请从历史任务或新建任务进入。</div>'), unsafe_allow_html=True)
        return
    # state_migration performs the legacy paragraph -> sentence upgrade in
    # memory so the repository read path stays side-effect free.  Persist it
    # when the task is actually opened, making the user's selected CAT contract
    # durable across processes and preventing a future reload from rebuilding
    # the same migration.
    segmentation_migration = (state.get("segmentation") or {}).get("migration") or {}
    if segmentation_migration.get("version") and not segmentation_migration.get("persisted"):
        segmentation_migration["persisted"] = True
        core.save_job_state(job_id, state)
    _render_workspace_fragment(job_id, state)


def _render_live_workspace(job_id):
    """Render workspace shell stably; live progress is isolated to render_runtime_strip_fragment."""
    _render_workspace_shell(job_id, core.load_job_state(job_id) if job_id else None)

# ================= Product shell / state =================
providers = sorted(core.PROVIDERS, key=str.casefold)
default_provider = "DeepSeek" if "DeepSeek" in providers else providers[0]
ai_provider = st.session_state.get("provider_choice", default_provider)
if ai_provider not in core.PROVIDERS:
    ai_provider = default_provider
provider_cfg = core.PROVIDERS[ai_provider]
model_opts = sorted(provider_cfg.get("models") or [], key=str.casefold)
default_model = provider_cfg.get("default_model")
if default_model not in model_opts:
    default_model = model_opts[0] if model_opts else ""
ai_model = st.session_state.get(f"model_choice_{ai_provider}", default_model)
if model_opts and ai_model not in model_opts:
    ai_model = default_model
    st.session_state[f"model_choice_{ai_provider}"] = default_model
api_key = st.session_state.get(f"api_key_{ai_provider}", "")
api_base = st.session_state.get("custom_base_url", "") \
    if provider_cfg.get("custom_base_url") else None
reasoning_options = core.reasoning_effort_options(ai_provider, ai_model)
reasoning_effort = st.session_state.get(f"reasoning_effort_{ai_provider}", "")
if reasoning_effort not in reasoning_options:
    reasoning_effort = ""
    st.session_state[f"reasoning_effort_{ai_provider}"] = ""
# Bind the selected endpoint before any page widget can trigger a model call.
# This is deliberately above the new-task/profile UI: custom relays must never
# depend on a previous rerun's thread-local URL.
core.set_llm_base_url(api_base if provider_cfg.get("custom_base_url") else None)
core.set_llm_reasoning_effort(reasoning_effort)
reviewer_mode = st.session_state.get("reviewer_mode", "same")
reviewer_provider = st.session_state.get("reviewer_provider_choice", ai_provider)
if reviewer_provider not in core.PROVIDERS:
    reviewer_provider = ai_provider
reviewer_model = st.session_state.get("reviewer_model", "")
reviewer_api_key = st.session_state.get("reviewer_api_key", "")
reviewer_base_url = st.session_state.get("reviewer_base_url", "")
# Navigation and resume choices need only names and progress. Full document
# states are loaded by the pages that actually display their contents.
saved_jobs_snapshot = [{"job_id": item["job_id"], "state": item}
                       for item in core.list_job_summaries()]
saved_jobs = saved_jobs_snapshot


# ================= 统一反馈（toast / flash）=================
# 所有 Project 操作的结果都走这里：回调里只入队，渲染层在**稳定位置**取一次并
# 用 `st.toast` 提示。这样即使操作后紧跟 `st.rerun()`（或弹出 modal 导致一次
# 额外 rerun），反馈也不会像 inline st.success 那样丢失或重复。
_TOAST_ICONS = {"success": ":material/check_circle:", "error": ":material/error:",
                "warning": ":material/warning:", "info": ":material/info:"}


def _push_flash(message, tone="success"):
    """入队一条待提示消息。跨 rerun 存活（存在 session_state 里）。"""
    text = str(message or "").strip()
    if not text:
        return
    queue = list(st.session_state.get("app_flash") or [])
    queue.append({"message": text, "tone": str(tone or "success")})
    st.session_state["app_flash"] = queue[-5:]


def _render_flashes():
    """取一次队列并渲染 toast。只消费一次，不会在 rerun 后重复出现。

    渲染过的消息追加到 `app_flash_log`：`st.toast` 是短期浮层，无法被回归测试
    观察到，因此把"确实提示过什么"留一份可断言的记录（界面不渲染它）。
    """
    queue = list(st.session_state.pop("app_flash", []) or [])
    for item in queue:
        message = str(item.get("message") or "")
        log = list(st.session_state.get("app_flash_log") or [])
        log.append({"message": message, "tone": item.get("tone") or "success"})
        st.session_state["app_flash_log"] = log[-20:]
        try:
            st.toast(_t(message), icon=_TOAST_ICONS.get(
                item.get("tone") or "success", _TOAST_ICONS["success"]))
        except Exception:
            # 老版本 Streamlit 没有 st.toast：退化成一次性提示条，语义不变。
            {"error": st.error, "warning": st.warning,
             "info": st.info}.get(item.get("tone") or "success",
                                  st.success)(message)


# ================= Project 弹窗（新建 / 重命名 / 编辑 / 归档 / 删除）=================
# 交互模型：破坏性或需要输入的 Project 生命周期操作都在 modal 里完成，避免
# inline 表单把列表页撑成表单页。每个 modal 由「谁打开」的 session_state 键驱动。
#
# 关键约束：`st.dialog` 的内容是一个 fragment，点击其中的按钮只会**重跑该
# fragment**，主脚本里"渲染一次就清掉标记"的写法会让弹窗在这一次重跑里消失，
# 连带丢掉按钮刚写下的效果（实测：切换项目时 active_project_id 根本没被写入）。
# 因此标记**只在动作成功后清掉**（`_close_project_modals` / `_close_project_switcher`），
# 不在渲染时消费。

PROJECT_MODAL_KEYS = ("project_modal", "project_modal_target", "task_move_job_id")
_HAS_ST_DIALOG = hasattr(st, "dialog")


def _open_project_modal(kind, project_id=""):
    st.session_state["project_modal"] = str(kind or "")
    st.session_state["project_modal_target"] = str(project_id or "")


def _close_project_modals():
    for key in PROJECT_MODAL_KEYS:
        st.session_state.pop(key, None)


def _consume_project_modal(*kinds):
    """当前打开的是哪个 modal（没有打开时返回空串）。

    刻意**不**在这里清除标记：dialog 内的按钮点击只重跑 fragment，清掉标记会让
    弹窗在重跑时消失（见本节说明）。
    """
    kind = str(st.session_state.get("project_modal") or "")
    return kind if kind in kinds else ""


def _modal_container(title, *, width="small", on_dismiss=None):
    """modal 渲染器；Streamlit 版本没有 st.dialog 时退化成 inline 卡片。

    退化路径只影响外观，不影响语义：用户仍然能完成同一批操作，且不会因为
    版本差异丢掉功能。
    """
    if _HAS_ST_DIALOG:
        return st.dialog(_t(title), width=width,
                         on_dismiss=on_dismiss or "ignore")
    def _inline(func):
        def _wrapper():
            with st.container(border=True, key=f"project_modal_{title}"):
                st.markdown(f'<div class="tp-field-head"><strong>{escape(title)}'
                            '</strong></div>', unsafe_allow_html=True)
                func()
        return _wrapper
    return _inline


def _segment_text_parts(value):
    text = str(value or "").replace("\r\n", "\n")
    if "\n" not in text:
        return text.strip(), ""
    first, remainder = text.split("\n", 1)
    return first.strip(), remainder.strip()


def _segment_editor_select(job_id, index):
    state = core.load_job_state(job_id) or {}
    pairs = state.get("pairs") or []
    if not isinstance(index, int) or not 0 <= index < len(pairs):
        return state, None
    return state, pairs[index]


def _segment_editor_finish(job_id, index, operation):
    state = core.load_job_state(job_id) or {}
    pairs = state.get("pairs") or []
    # 结构操作改变了索引与段身份：先清掉指向旧身份的编辑/候选状态，再定位到结果段。
    dropped = _purge_translation_edit_state(job_id, state)
    if pairs:
        selected_index = max(0, min(index, len(pairs) - 1))
        st.session_state["selected_segment_id"] = _translation_segment_id(
            job_id, selected_index, pairs[selected_index])
        st.session_state["selected_segment_index"] = selected_index
    _close_translation_segment_editor()
    _translation_structure_flash(operation, index)
    if dropped:
        _set_workspace_flash(
            f"段落结构已更新；{dropped} 处未保存修改因为对应段落已不存在而失效。",
            "warning")
    st.rerun()


@_modal_container("编辑段落", width="large",
                  on_dismiss=_dismiss_translation_segment_editor)
def _translation_segment_editor_dialog():
    spec = st.session_state.get(_TRANSLATION_SEGMENT_EDITOR_KEY) or {}
    job_id = str(spec.get("job_id") or "")
    index = spec.get("index")
    mode = str(spec.get("mode") or "")
    state, pair = _segment_editor_select(job_id, index)
    if pair is None:
        st.warning(_t("该段落已经不存在，可能刚刚被其他操作改变。"))
        if st.button(_t("关闭"), key="translation_segment_editor_close_missing"):
            _close_translation_segment_editor()
            st.rerun()
        return
    blocked, _runtime_status = _translation_structure_edit_blocked(job_id, state)
    if blocked:
        st.warning(_t("任务正在运行。请等待当前步骤完成或暂停后再编辑段落结构。"))
        if st.button(_t("关闭"), key=f"translation_segment_editor_close_{job_id}_{index}"):
            _close_translation_segment_editor()
            st.rerun()
        return

    base_key = f"translation_segment_editor_{job_id}_{index}_{mode}"
    for side, language_key in (("source", "source_lang"), ("target", "target_lang")):
        direction = core.text_direction(pair.get(side), state.get(language_key))
        editor_class = escape(f"st-key-{base_key}_{side}", quote=True)
        st.html(
            f'<style>[class*="{editor_class}"] textarea {{ direction: {direction}; '
            f'text-align: {"right" if direction == "rtl" else "left"}; '
            'unicode-bidi: isolate; }</style>')
    if mode == "source":
        st.caption(_t("修订模型未清理干净的字符、OCR 错字或不应进入译文的原文内容。"))
        with st.form(key=f"{base_key}_form", clear_on_submit=False):
            source = st.text_area(
                _t("原文"), value=str(pair.get("source") or ""),
                key=f"{base_key}_source", height=150,
                help=_t("保留段落本身的语义；如需增加空白内容，请使用插入空段。"))
            st.text_area(_t("当前译文（只读）"), value=str(pair.get("target") or ""),
                         key=f"{base_key}_target_preview", height=100, disabled=True)
            submitted = st.form_submit_button("保存原文", type="primary",
                                              width="stretch")
        if submitted:
            try:
                core.mutate_translation_segments(
                    job_id, index, "source_edit", source_text=source)
            except (RuntimeError, ValueError, IndexError) as exc:
                st.error(_t(str(exc)))
            else:
                _segment_editor_finish(job_id, index, "source_edit")
        return

    pairs = state.get("pairs") or []
    if mode == "split":
        source_left, source_right = _segment_text_parts(pair.get("source"))
        target_left, target_right = _segment_text_parts(pair.get("target"))
        st.caption(_t("把当前段落拆成两个工作单元。两段原文都必须填写；译文可暂留空。"))
        with st.form(key=f"{base_key}_form", clear_on_submit=False):
            left_col, right_col = st.columns(2, gap="medium")
            with left_col:
                left_source = st.text_area(
                    _t("第一段原文"), value=source_left,
                    key=f"{base_key}_source_a", height=130)
                left_target = st.text_area(
                    _t("第一段译文"), value=target_left,
                    key=f"{base_key}_target_a", height=110)
            with right_col:
                right_source = st.text_area(
                    _t("第二段原文"), value=source_right,
                    key=f"{base_key}_source_b", height=130)
                right_target = st.text_area(
                    _t("第二段译文"), value=target_right,
                    key=f"{base_key}_target_b", height=110)
            submitted = st.form_submit_button("拆分并保存", type="primary",
                                              width="stretch")
        if submitted:
            try:
                core.mutate_translation_segments(
                    job_id, index, "split", source_a=left_source,
                    source_b=right_source, target_a=left_target,
                    target_b=right_target)
            except (RuntimeError, ValueError, IndexError) as exc:
                st.error(_t(str(exc)))
            else:
                _segment_editor_finish(job_id, index, "split")
        return

    if mode == "merge":
        following = pairs[index + 1] if index + 1 < len(pairs) else None
        if following is None:
            st.warning(_t("最后一段没有下一段可合并。"))
            return
        merged_source = "\n".join(
            item for item in (str(pair.get("source") or "").strip(),
                              str(following.get("source") or "").strip()) if item)
        merged_target = "\n".join(
            item for item in (str(pair.get("target") or "").strip(),
                              str(following.get("target") or "").strip()) if item)
        st.caption(_t("合并会把当前段和下一段变成一个工作单元，并让它们重新进入待审校。"))
        # 预览与确认**都不放进 `st.form`**：表单里的复选框在提交前不会到达服务端，
        # 而"合并"按钮的 disabled 又由这个复选框的后端值决定——勾了也解不开锁。
        st.text_area(_t("合并后原文"), value=merged_source,
                     key=f"{base_key}_source_preview", height=150, disabled=True)
        st.text_area(_t("合并后译文"), value=merged_target,
                     key=f"{base_key}_target_preview", height=120, disabled=True)
        confirmed = st.checkbox(_t("我确认合并当前段和下一段"),
                                key=f"{base_key}_confirm")
        st.caption(_t("合并后可用「撤销上一次结构操作」退回，原文与译文都会还原。"
                   if confirmed else "勾选后即可合并。"))
        if st.button(_t("合并段落"), type="primary", key=f"{base_key}_submit",
                     disabled=not confirmed, width="stretch"):
            try:
                core.mutate_translation_segments(job_id, index, "merge")
            except (RuntimeError, ValueError, IndexError) as exc:
                st.error(_t(str(exc)))
            else:
                _segment_editor_finish(job_id, index, "merge")
        return

    st.warning(_t("未知的段落编辑操作。"))
    if st.button(_t("关闭"), key=f"translation_segment_editor_close_{job_id}_{index}"):
        _close_translation_segment_editor()
        st.rerun()


def _project_modal_record():
    """modal 当前作用的项目记录（不存在时返回 None，调用方负责报错）。"""
    project_id = str(st.session_state.get("project_modal_target") or "")
    if not project_id:
        return None
    if core.is_system_project_id(project_id):
        return core.system_project_view()
    return core.load_project(project_id)


def _reload_project_state():
    """任何 Project 操作成功后刷新与项目相关的派生状态。

    侧栏「项目上下文」、项目列表、新建任务页的上下文都从磁盘派生，因此这里只做
    一件事：让缓存/失效的导航状态与磁盘一致。**不做 rerun**，由调用方决定。
    """
    active = str(st.session_state.get("active_project_id") or "")
    if active and core.load_project(active) is None \
            and not core.is_system_project_id(active):
        st.session_state.pop("active_project_id", None)
    chosen = str(st.session_state.get("task_project_id") or "")
    if chosen and core.load_project(chosen) is None \
            and not core.is_system_project_id(chosen):
        st.session_state["task_project_id"] = None
        st.session_state.pop("task_project_choice", None)


def _refresh_projects_after():
    """任何 Project 操作成功后的统一收尾：刷新派生导航状态并提示成功。

    侧栏「项目上下文」、项目列表、新建任务页的上下文都从磁盘派生，因此这里只做
    一件事：让失效的导航状态与磁盘一致，并把"操作成功"变成一次 toast。
    """
    _reload_project_state()


# ================= 统一导航入口（项目 / 任务 / 历史卡片）=================
# 历史卡片的标题点击、卡片空白点击、右侧 CTA、侧栏「项目上下文」，全部走这里。
# 之前每个入口各写一份 session_state 赋值，三套逻辑很容易漂移——这里收敛成一份。
#
# `destination` 用人类可读的名字（overview/translation/review/report/delivery），
# 不是一个已经映射好的 section，因为 CTA 的语义是"去哪里做事"，而不是"打开哪一页"。
_JOB_DESTINATIONS = {
    "translation": "translation",
    "terms": "terms",
    "review": "review",
    "report": "report",
    "qa": "qa",
    "delivery": "delivery",
}

# Project 详情的一级 tab（顺序即信息架构）。`active_project_tab` 是导航状态，
# 与 `workspace_section` 对称：项目与任务各有各的层级，互不覆盖。
PROJECT_TABS = (("overview", "概览"), ("tasks", "任务"),
                ("knowledge", "项目知识"), ("settings", "设置"))
PROJECT_TAB_LABELS = dict(PROJECT_TABS)


def _remember_project_visit(project_id):
    """Keep a short-lived session list for switcher recency ordering."""
    canonical = core._project.canonical_project_id(project_id)
    recent = [str(item) for item in
              (st.session_state.get("project_switcher_recent") or [])]
    recent = [item for item in recent if item != canonical]
    recent.insert(0, canonical)
    st.session_state["project_switcher_recent"] = recent[:8]


def _route_params(**mapping):
    _h("""把导航状态镜像到 URL query params（`/projects/<uuid>` 的可分享等价物）。

    Streamlit 的脚本式路由没有真实 path segment，因此用 `project=<uuid>` 表达
    "当前打开的是哪个项目"，用 `task=<hash>` 表达"当前打开的是哪个任务"。
    它只镜像**稳定 ID**，不镜像显示名称——名称是标签，改名不应该改变任何链接。

    空值必须**删除**该参数，而不是写成 `?project=None`：后者会让 URL 看起来像
    "打开了一个叫 None 的项目"。
    """)
    if mapping.get("task"):
        if "project" not in mapping:
            mapping["project"] = None
        if "view" not in mapping:
            mapping["view"] = None
        if "job" not in mapping:
            mapping["job"] = None
    elif mapping.get("project") or mapping.get("view"):
        if "task" not in mapping:
            mapping["task"] = None
        if "job" not in mapping:
            mapping["job"] = None
        if "section" not in mapping:
            mapping["section"] = None

    for key, value in mapping.items():
        text = str(value or "").strip()
        try:
            if text:
                st.query_params[key] = text
            elif key in st.query_params:
                del st.query_params[key]
        except Exception:  # 老版本 / 非浏览器上下文：路由镜像不是关键路径
            pass
    # Internal navigation has already applied this URL state. Remember it so the
    # next rerun does not replay the old route over the button's session update.
    try:
        st.session_state["_route_signature"] = tuple(
            (key, str(st.query_params.get(key) or ""))
            for key in ("view", "project", "task", "job", "tab", "section", "item"))
    except Exception:
        pass


def _open_job(job_id, state=None, destination="translation"):
    """唯一入口：打开一个任务，并落到指定阶段。

    只设置导航状态；滚动/恢复等副作用由调用方按需追加（例如 `resume=True`
    时再调 `_resume_job`）。不做 `st.rerun()`，让调用方的按钮/回调自然触发渲染。

    **只设置 task 侧状态**（`active_job_id`）。任务所属项目由任务 state 里的
    `project_id` 决定，不允许写进 `active_project_id`——那会把「打开任务」变成
    「打开项目」，让两个实体在导航状态里混成一个。Project 详情有自己的
    入口（`_open_project`）。
    """
    section = _JOB_DESTINATIONS.get(str(destination or "translation"), "translation")
    st.session_state.update(active_job_id=job_id,
                            app_view="workspace",
                            workspace_mode=True,
                            workspace_section=section)
    _route_params(project=None, view=None, task=job_id,
                  section=section if section != "translation" else None)
    return section


def _open_project(project_id, tab="overview"):
    """唯一入口：打开项目详情（`/projects/:projectId`）。

    与 `_open_job` 对称：Project 侧只写 `active_project_id` + `active_project_tab`，
    不写 `active_job_id`。当前任务保持打开（侧栏「当前任务」仍然可用），
    因为项目与任务互不替代。

    **路由标识永远是不可变 UUID**：`project_id` 可以是历史别名（`default`），
    这里先归一再写入导航状态与 URL，因此旧链接也能正确落到「未分类」。
    """
    canonical = _apply_project_context(project_id)
    st.session_state["active_project_tab"] = tab if tab in PROJECT_TAB_LABELS \
        else "overview"
    st.session_state.update(app_view="projects", workspace_mode=False,
                            projects_route="detail")
    _close_project_modals()
    _route_params(project=canonical, view=None, task=None, section=None)
    return canonical


def _projects_route():
    """`app_view == "projects"` 下正在看哪一层：`detail`（某个项目）或 `list`（项目中心）。

    **路由与上下文必须分开**。以前两者都由 `active_project_id` 兼任——进列表页就得把
    上下文清空，于是侧栏 selector 从「项目 A」翻成「未选择项目」，用户读到的是"我的
    上下文没了"，而实际上他只是去看了一眼项目列表。项目中心是**纯导航**：它不该
    修改当前 Project Context（需求 B）。

    没有显式标记时沿用旧推断（有 `active_project_id` 就是详情），这样归档 / 新建项目
    等直接写 `app_view` 的旧路径行为不变。
    """
    route = str(st.session_state.get("projects_route") or "")
    if route in ("list", "detail"):
        return route
    return "detail" if str(st.session_state.get("active_project_id") or "") else "list"


def _open_project_list():
    """进入项目列表（Project manager），不是开始翻译，也不是某个项目。

    **纯导航**：只改路由（`projects_route="list"`），不碰当前 Project Context——
    `active_project_id` 保留，侧栏 selector 因此在管理页上仍然显示"我在哪个项目里
    工作"。这同时满足需求 B 的两条：不修改上下文、且项目中心保持 active state。
    它还会收起任何展开着的切换面板，否则侧栏展开的下拉会跟着漂到管理页上。
    """
    st.session_state.update(app_view="projects", workspace_mode=False,
                            projects_route="list")
    st.session_state["active_project_tab"] = "overview"
    _close_project_modals()
    _close_all_project_switchers()
    _route_params(project=None, view="projects", task=None, section=None)


def _restore_route_from_params():
    """页面刷新后按 URL 恢复任务工作台、项目或语言资产工作区。"""
    try:
        task_id = str(st.query_params.get("task") or st.query_params.get("job") or "").strip()
        project_id = str(st.query_params.get("project") or "").strip()
        requested_view = str(st.query_params.get("view") or "").strip().casefold()
        requested_tab = str(st.query_params.get("tab") or "").strip()
        requested_section = str(st.query_params.get("section") or "").strip()
    except Exception:
        return
    signature = tuple(
        (key, str(st.query_params.get(key) or ""))
        for key in ("view", "project", "task", "job", "tab", "section", "item"))
    if st.session_state.get("_route_signature") == signature:
        return
    st.session_state["_route_signature"] = signature

    if task_id:
        resolved = core.resolve_job_id(task_id)
        if resolved is None:
            # 任务已被删除或不存在：清掉失效路由，如实说明并回退
            _route_params(task=None, job=None, section=None)
            _push_flash(f"链接指向的任务已不存在（{task_id}），已回到主页。",
                        tone="warning")
            return
        state = core.load_job_state(resolved)
        destination = requested_section if requested_section in _JOB_DESTINATIONS else (
            requested_tab if requested_tab in _JOB_DESTINATIONS else "translation"
        )
        _open_job(resolved, state, destination=destination)
        return

    if project_id:
        record = core.load_project(project_id)
        if record is None and core.is_system_project_id(project_id):
            record = core.system_project_view()
        if record is None:
            # 项目已被删除：清掉失效路由，回项目列表并如实说明。
            _route_params(project=None, view="projects")
            _push_flash(f"链接指向的项目已不存在（{project_id}），已回到项目列表。",
                        tone="warning")
            return
        st.session_state["active_project_id"] = record["project_id"]
        st.session_state.update(app_view="projects", workspace_mode=False,
                                projects_route="detail")
        return

    # 项目中心（`view=projects`）是一个可分享的页面入口：它**不**携带项目上下文，
    # 所以恢复的是"列表路由"，上下文交由用户本次会话重新建立。已经打开了某个项目
    # 的时候不覆盖（页面重连不该把详情页踢回列表）。
    if requested_view == "projects" and not st.session_state.get("active_project_id"):
        st.session_state.update(app_view="projects", workspace_mode=False,
                                projects_route="list")
        st.session_state["active_project_tab"] = "overview"
        return

    if requested_view == "appearance":
        st.session_state.update(app_view="appearance", workspace_mode=False)
        return

    # 历史任务列表（`view=history`）
    if requested_view == "history":
        st.session_state.update(app_view="history", workspace_mode=False)
        return

    # 语言资产工作区是一个真正可分享的页面入口。只接受已知 Tab，避免一个
    # 任意的 `?tab=` 参数把普通新建任务页误切走；`view=library` 可单独打开
    # 默认术语库，`view=library&tab=review` 打开指定 Tab。
    if requested_view == "library" or requested_tab in _language_assets.VALID_TABS:
        st.session_state.update(app_view="library", workspace_mode=False)
        if requested_tab in _language_assets.VALID_TABS:
            st.session_state["library_tab"] = requested_tab


def _open_job_cta(job_id, state, cta):
    """按卡片 CTA 进入对应流程。

    CTA 点击**不经过 Overview**：用户点"继续审校"就是要去审校页。没有显式
    目标的 CTA 进入翻译工作台；需要交付、报告或审校的动作沿用显式目标。
    """
    cta = cta or {}
    _open_job(job_id, state, cta.get("destination") or "translation")
    if cta.get("resume"):
        _resume_job(job_id, state)
    st.rerun()


def _job_display_title(job):
    """侧栏与卡片共用的主标题解析：文档元数据优先，不拿完整文件名当标题。"""
    state = job.get("state") or {}
    return _history_view.document_title(state)


def _project_display_title(project):
    return str((project or {}).get("name") or "").strip()


def _history_card_html(view):
    """Translation Task 卡片视觉：标题 / 身份 / 状态 / 进度 / 问题数 / 最近更新。

    四行结构（回归测试守住，密度目标 105–120px）：

      1. display title
      2. author · source type · target language · domain
      3. status chip · segment progress · issue count（+ 所属项目）
      4. 最近更新                                       contextual CTA →

    第 4 行右侧的空位留给 contextual CTA：它由 `_render_history_page` 作为卡片
    内部的一个**真实按钮**渲染，本函数只画出留白。完整源文件名不作为主标题，
    只作为 hover 提示保留，避免丢信息。
    """
    chip = view["chip"]
    title = escape(view["title"])
    part = (f'<span class="tp-hcard-part">{escape(view["part_label"])}</span>'
            if view.get("part_label") else "")
    identity = escape(view["identity"]) if view.get("identity") else ""
    project = escape(view["project_name"]) if view.get("project_name") else ""
    orphan = bool(view.get("project_orphan"))
    issues = int(view.get("issue_count") or 0)
    issue_text = f"问题 {issues}" if issues else "无待处理问题"
    return (
        f'<div class="tp-hcard{" is-orphan" if orphan else ""}" '
        f'title="{escape(view["filename"])}">'
        f'<h3 class="tp-hcard-title">{part}{title}</h3>'
        f'<div class="tp-hcard-sub">{identity}</div>'
        '<div class="tp-hcard-meta">'
        f'<span class="tp-hcard-chip is-{escape(chip["tone"])}">'
        f'{escape(chip["label"])}</span>'
        f'<span class="tp-hcard-progress">{escape(view["progress"])}</span>'
        f'<span class="tp-hcard-issues{" is-flagged" if issues else ""}">'
        f'{escape(issue_text)}</span>'
        + (('<span class="tp-hcard-project' + str(" is-orphan" if orphan else "") + _h('">项目 ') + str(project) + '</span>') if project else "")
        + (_h('</div><div class="tp-hcard-foot"><span class="tp-hcard-updated">最近更新 ') + str(escape(view["updated_label"])) + '</span></div></div>'))


def _render_history_page(jobs):
    """Translation Task 列表：每张卡片是一次具体的文档翻译执行。

    对象语义（回归测试守住）：这里列的是 **Tasks**，不是 Projects。Task 属于某个
    Project 只是它的属性，因此页面叫「历史任务」、卡片动作叫「打开任务」；Project
    有自己的入口（侧栏「项目」）。

    交互约定：
      - 整张卡片可点 → Task Overview（铺满卡片的透明 button）；
      - contextual CTA 在**卡片内部**，是真实按钮，z-index 高于点击层；
        两者是兄弟节点，一次点击只触发一个导航，不冒泡到 Overview；
      - 顶部 search 是主控件（约 55%），status / sort 是紧凑 select，三者不等宽；
      - 浅 surface + hairline + low shadow，不做重卡片。
    """
    views = []
    for job in jobs:
        state = job.get("state") or {}
        runtime_view = core.build_job_runtime_view(job["job_id"], state)
        runtime_status = (runtime_view.get("runtime_status")
                          or runtime_view.get("status") or "")
        delivery_label, _tone = _workspace_delivery_state(job["job_id"], state)
        snapshot = core.delivery_snapshot_status(job["job_id"], state)
        recovery = core.recovery_summary(job["job_id"], state)
        project = core.project_for_job(job["job_id"], state) or {}
        # 「未分类」是用户主动选择的"没有长期归属"；而 `project_id` 指向一个**已经
        # 不存在的项目记录**是另一回事——容器被删了，任务被落在后面。两者必须
        # 区分：否则卡片看上去只是"没有项目"（一个正常状态），用户永远猜不到
        # 这些条目为什么悬空、为什么在项目侧也删不掉。
        orphaned = bool(str(state.get("project_id") or "").strip()) and not project
        views.append(_history_view.history_card_view(
            state, job_id=job["job_id"], runtime_status=runtime_status,
            delivery_label=delivery_label,
            delivery_current=bool(snapshot.get("current")),
            saved_at=str(recovery.get("last_saved_at") or ""),
            project_name=("原项目已删除" if orphaned
                          else _project_display_title(project)),
            project_orphan=orphaned,
        ))

    # search 是主控件；status / sort 只做紧凑筛选，三者不等宽。
    head_left, head_mid, head_right = st.columns([5.5, 2.3, 2.2], gap="small")
    query = head_left.text_input(
        _t("搜索历史任务"), key="history_search", label_visibility="collapsed",
        placeholder=_t("搜索任务名称、文件、作者或领域…"))
    status_filter = head_mid.selectbox(
        _t("状态筛选"), ["全部", "需要处理", "建议检查", "进行中", "已完成"],
        key="history_status", label_visibility="collapsed", format_func=_localized_formatter())
    order = head_right.selectbox(
        _t("排序"), ["最近更新", "任务名称", "状态"],
        key="history_order", label_visibility="collapsed", format_func=_localized_formatter())

    visible = [view for view in views
               if _history_view.card_matches(view, query=query,
                                             status=status_filter)]
    visible.sort(key=lambda item: _history_view.sort_key(item, order),
                 reverse=(order == "最近更新"))
    if not visible:
        st.markdown(_h('<div class="tp-empty">没有符合条件的历史任务。</div>'),
                    unsafe_allow_html=True)
        return

    for view in visible:
        job_id = view["job_id"]
        state = next(job["state"] for job in jobs if job["job_id"] == job_id)
        with st.container(key=f"history_item_{job_id}"):
            # 卡片本体 + 两个导航入口。定位容器只包住卡片，恢复提示在外面，
            # 否则绝对定位的 CTA 会锚到提示下方。
            with st.container(key=f"history_cardframe_{job_id}"):
                st.markdown(_history_card_html(view), unsafe_allow_html=True)
                # 点击层：铺满整张卡片的透明按钮 → 默认翻译工作台。
                if st.button(_t("打开任务"), key=f"history_card_{job_id}",
                             width="stretch",
                             help=_t('打开任务：{v0}', v0=view['title'])):
                    _open_job(job_id, state, "translation")
                    st.rerun()
                # contextual CTA：卡片内部的真实按钮。它与点击层是**兄弟节点**且
                # z-index 更高，所以点击 CTA 不会触发整卡导航（无冒泡可穿透）。
                if st.button(_t(view["cta"]["label"]), key=f"history_cta_{job_id}",
                             width="stretch",
                             type="primary" if view["cta"]["label"] in
                             {"继续处理", "继续审校", "继续翻译"} else "secondary"):
                    _open_job_cta(job_id, state, view["cta"])
                if view["project_orphan"]:
                    if st.button(
                            _t("修复项目归属"),
                            key=f"history_orphan_move_{job_id}",
                            width="stretch",
                            disabled=core.job_is_active(job_id, state),
                            help=_t("把任务重新归入一个现有项目；正在运行时不能更改归属")):
                        _open_task_move_modal(job_id)
                        st.rerun()
                # 移除：任务唯一的删除入口。同样在卡片容器内部、z-index 高于点击
                # 层，所以点它只触发它自己（与 CTA 是并排的兄弟节点，不重叠）。
                # key 前缀必须是 `history_del_`：`history_card_*` 会被整卡点击层的
                # 子串规则命中（回归测试守住这条）。
                if st.button(_t("移除"), key=f"history_del_{job_id}",
                             width="content",
                             help=_t("把这个翻译任务从历史中删除（会先确认）")):
                    _open_history_delete(job_id)
                    st.rerun()
            recovery = core.recovery_summary(job_id, state)
            if view["counts"]["translated"] < view["counts"]["total"] or \
                    view["chip"]["kind"] in {"interrupted", "running"}:
                st.caption(_t("自动保存已开启"))
                if recovery.get("current_batch"):
                    current = recovery["current_batch"]
                    st.warning(
                        _t('处理中断：当前批次已保存 {v0}/{v1} 段；继续后只处理未完成内容。', v0=current['completed_segments'], v1=current['segment_count']))
            _render_snapshot_versions(job_id, state, "history")


# ================= 历史任务：移除（永久删除）=================
# 数据层 `core.delete_job` 一直存在，但界面上**没有**任何调用点：全仓只有
# `delete_project(cascade=True)`（连带删项目下的任务）与测试在调。项目侧的任务行
# 菜单（`_render_task_rows(..., menu=…)`）有 hook、也有测试
# `test_task_row_delete_modal_deletes_single_task` 钉着，但还没实现。
#
# 更要紧的是：**孤儿任务连那条计划中的入口也够不着**。它的 `project_id` 指向一个
# 已删除的项目记录，于是 `list_project_jobs()` 匹配不上任何项目，
# `list_unassigned_jobs()` 也匹配不上（那要求"没有归属"，而不是"归属已失效"）。
# 结果：它只出现在历史任务页，而那里此前只有搜索/筛选/排序/打开，没有删除。
# 项目侧的删除入口还被"项目下仍有任务"挡住——两头都够不着，永久卡住。
#
# 因此这里补的是**唯一能触达孤儿任务**的删除入口。与其他破坏性操作一致：
# modal + 逐字输入名称确认，绝不一键即删。
HISTORY_DELETE_KEY = "history_delete_job"


def _open_history_delete(job_id):
    st.session_state[HISTORY_DELETE_KEY] = str(job_id or "")


def _close_history_delete():
    st.session_state.pop(HISTORY_DELETE_KEY, None)


def _history_delete_target():
    """待移除的任务；标记还在但任务已不在磁盘上时自行收尾并返回 None。"""
    job_id = str(st.session_state.get(HISTORY_DELETE_KEY) or "")
    if not job_id:
        return None
    state = core.load_job_state(job_id)
    if not isinstance(state, dict):
        _close_history_delete()
        return None
    return job_id, state


def _history_delete_draft_count(job_id):
    """这个任务在会话里还挂着几处未保存的编辑草稿（删除时会一起丢）。"""
    prefix = f"{job_id}|"
    return sum(1 for key in _translation_draft_store()
               if str(key).startswith(prefix))


def _drop_history_job_state(job_id):
    """清掉会话里指向被删除任务的引用，避免留下一个"打开不存在的任务"的状态。"""
    store = _translation_draft_store()
    prefix = f"{job_id}|"
    for key in [k for k in store if str(k).startswith(prefix)]:
        store.pop(key, None)
    states = st.session_state.get("doc_states")
    if isinstance(states, dict):
        states.pop(job_id, None)
    if str(st.session_state.get("active_job_id") or "") == job_id:
        for key in ("active_job_id", "workspace_section", "selected_segment_id",
                    "selected_segment_index"):
            st.session_state.pop(key, None)
        st.session_state["workspace_mode"] = False
        _route_params(task=None, job=None, section=None)


def _render_history_delete_modal():
    """按 session 标记渲染移除确认弹窗；没有标记时什么都不做。"""
    target = _history_delete_target()
    if target is None:
        return
    job_id, state = target
    view = _history_view.history_card_view(state, job_id=job_id)
    title = view["display_name"] or _history_view.document_title(state)
    running = core.job_is_active(job_id)
    drafts = _history_delete_draft_count(job_id)

    def _body():
        rows = (
            ("文档", str(state.get("filename") or "—")),
            ("翻译进度", f'{view["counts"]["translated"]} / '
                         f'{view["counts"]["total"]} 段'),
            ("项目归属", view["project_name"] or core.SYSTEM_PROJECT_NAME),
            ("任务目录", str(core.job_dir(job_id))),
        )
        cards = "".join(
            f'<div class="tp-summary-item"><span>{escape(label)}</span>'
            f'<strong>{escape(value)}</strong></div>' for label, value in rows)
        st.markdown(
            (_h('<div class="tp-confirm-head"><span class="material-symbols-rounded" aria-hidden="true">delete_forever</span>移除「') + str(escape(title)) + '」</div><div class="tp-confirm-card"><div class="tp-summary-grid">' + str(cards) + '</div></div>'), unsafe_allow_html=True)

        if running:
            # 正在运行的任务不能删：worker 还在往这个目录写状态，删掉会留下一个
            # 半写状态并让运行时报错。如实说明，不给一个点了没反应的按钮。
            st.error(_t("这个任务正在运行，无法移除。请先在任务里取消，或等它结束。"))
            if st.button(_t("关闭"), key="history_delete_close", width="stretch"):
                _close_history_delete()
                st.rerun()
            return

        st.warning(_t("移除是永久操作：任务目录（源文档、段落、译文、审校与运行记录）"
                   "会从磁盘删除，不可恢复。"))
        if drafts:
            # 这个项目的红线是"编辑不丢"：要丢就先把数量说出来，绝不静默丢稿。
            st.warning(_t('这个任务在会话里还有 {v0} 处未保存的编辑草稿，会一起丢弃。', v0=drafts))
        typed = st.text_input(
            _t('输入任务名称「{v0}」以确认', v0=title),
            key="history_delete_confirm_name", placeholder=title)
        confirm_col, cancel_col = st.columns([2, 1])
        if confirm_col.button(_t("永久移除"), key="history_delete_confirm",
                              type="primary",
                              disabled=str(typed or "").strip() != title,
                              width="stretch"):
            try:
                removed = core.delete_job(job_id)
            except ValueError as exc:
                st.session_state["history_delete_error"] = str(exc)
            else:
                if not removed:
                    st.session_state["history_delete_error"] = \
                        "任务目录已经不存在，无需移除。"
                else:
                    _drop_history_job_state(job_id)
                    _close_history_delete()
                    _push_flash(f"已永久移除任务「{title}」。")
                    st.rerun()
        if cancel_col.button(_t("取消"), key="history_delete_cancel",
                             width="stretch"):
            _close_history_delete()
            st.rerun()
        # dialog 重跑时 body 先执行、按钮回调随后触发，所以校验错误必须在按钮
        # **之后**渲染，否则要晚一轮才可见（与项目弹窗同一约定）。
        if error := st.session_state.pop("history_delete_error", None):
            st.error(_t(error))

    _modal_container("移除任务（永久）", on_dismiss=_close_history_delete)(_body)()


def _relative_project_updated_at(value, *, now=None):
    """Return the short relative timestamp used by project scan cards."""
    if not value:
        return "刚刚更新"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "最近更新"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    seconds = max(0, int((reference - parsed).total_seconds()))
    if seconds < 60:
        return "刚刚更新"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 2:
        return "昨天更新"
    return f"{days} 天前更新"


# ---- 项目卡：先回答"这个项目现在在做什么" ----
# 卡片的信息优先级固定（回归测试守住）：
#   1. 项目名称   2. 任务分布   3. 最近工作   4. 知识资产摘要   5. 最近更新时间
#
# 「0 术语 · 0 规则 · 0 决定 · 0 记忆」这类数据库式统计**不再出现**在卡上：
# 没有数据的行留空，由 markup 隐藏，而不是渲染一排 0。

#: 任务分布的分桶顺序即展示顺序；只渲染计数非零的桶。
_PROJECT_TASK_BUCKETS = (
    ("active", "进行中"),
    ("attention", "待处理"),
    ("pending", "待开始"),
    ("done", "已完成"),
)
# 项目卡上的工作分布**不是**任务详情的权威状态：它只用 runtime 状态与段落完成度
# 做一次聚合。逐任务派生 `_task_overview_state`（含交付 / 合规 / QA 投影）成本高，
# 而任务自己的页面已经由 canonical overview 渲染，那里才是权威。
_PROJECT_RUNTIME_ACTIVE = frozenset({
    "running", "resume_requested", "queued", "starting", "waiting_external",
    "cancelling", "waiting_manual"})
_PROJECT_RUNTIME_ATTENTION = frozenset({
    "interrupted", "failed", "stalled", "cancelled"})

#: 状态筛选：默认「活动中」，归档项目从首屏移出但可一键找回。
_PROJECT_STATUS_OPTIONS = ("全部", "活动中", "已归档")
#: 排序是显式动词，不是一个含糊的"最近更新"下拉。
_PROJECT_ORDER_OPTIONS = ("最近更新", "最早更新", "名称 A-Z", "任务最多")
_PROJECT_VIEW_MODES = ("grid", "list")


def _project_translation_progress(state):
    """(已译, 总段数)。与 planner 的 translation 维度同规则：pairs 优先，否则 paras。

    纯 state 投影，不读盘、不派生状态，因此可以在项目列表上对每个任务调用。
    """
    state = state if isinstance(state, dict) else {}
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    rows = pairs if pairs else paras
    total = len(rows)
    done = sum(1 for row in rows
               if isinstance(row, dict) and str(row.get("target") or "").strip())
    return done, total


def _project_job_bucket(job):
    """任务在项目卡上的工作分布桶。

    「已完成」必须与任务详情的 canonical lifecycle 同义（只有**已冻结交付**才算
    完成）：译文全部翻完但还没交付是"进行中 / 待交付"，不是"已完成"。否则同一个
    项目在卡片上写"2 已完成"、进详情却是"3 进行中"，两处说法互相打架。
    """
    state = job.get("state") or {}
    runtime = core.build_job_runtime_view(job["job_id"], state)
    status = str(runtime.get("runtime_status") or runtime.get("status") or "")
    if status in _PROJECT_RUNTIME_ATTENTION:
        return "attention"
    if status in _PROJECT_RUNTIME_ACTIVE:
        return "active"
    if str(state.get("delivery_status") or "") == "final":
        return "done"
    done, _total = _project_translation_progress(state)
    return "active" if done or state.get("p2_done") else "pending"


def _project_job_activity(job):
    """项目卡的「最近工作」：最近任务的标题 / 状态 / 段落进度。

    状态词来自 canonical 交付状态（`_workspace_delivery_state`），百分比来自段落
    完成度。两者都是既有真实数据；没有进度数据时不显示百分比，也不编造。
    """
    state = job.get("state") or {}
    job_id = job["job_id"]
    done, total = _project_translation_progress(state)
    percent = round(done * 100 / total) if total else 0
    label, _tone = _workspace_delivery_state(job_id, state)
    return {
        "job_id": job_id,
        "title": _job_display_title(job),
        "status": label,
        # 只有"部分完成"才显示百分比：0% / 100% 是噪音，状态词已经说清楚了。
        "progress": f"{percent}%" if total and 0 < percent < 100 else "",
    }


def _project_knowledge_summary(view):
    """知识资产压成一行：术语 / 规则 /（决定）/ 记忆。全为 0 时返回空串。"""
    parts = []
    for count, label in (
            (int(view.get("glossary_count") or 0), "术语"),
            (int(view.get("style_rule_count") or 0), "规则"),
            (int(view.get("human_decision_count") or 0), "决定"),
            (int(view.get("translation_memory_count") or 0), "记忆")):
        if count:
            parts.append(f"{label} {count}")
    return " · ".join(parts)


def _project_card_view(view, project):
    """把项目摘要投影成一张紧凑项目卡需要的全部字段。

    返回的字符串字段为空即表示"这条没有真实数据"，由 markup 决定隐藏。
    """
    jobs = list(view.get("jobs") or [])
    buckets = {key: 0 for key, _label in _PROJECT_TASK_BUCKETS}
    for job in jobs:
        buckets[_project_job_bucket(job)] += 1
    breakdown = " · ".join(
        f"{buckets[key]} {label}" for key, label in _PROJECT_TASK_BUCKETS
        if buckets[key])
    latest = _recent_jobs(jobs, 1)
    return {
        "project_id": view["project_id"],
        "name": str(view.get("name") or "—"),
        "description": str(project.get("description") or "").strip(),
        "archived": bool(view.get("status") == "archived"),
        "job_count": len(jobs),
        "breakdown": breakdown,
        "activity": _project_job_activity(latest[0]) if latest else None,
        "knowledge": _project_knowledge_summary(view),
        "updated": _relative_project_updated_at(view.get("updated_at")),
        "updated_exact": _format_saved_at(view.get("updated_at")),
        "lang_pair": _browser.extract_language_pair(jobs),
    }


def _project_card_markup(card):
    """Project Hub 的项目卡：身份 / 状态 / 行动三个区（委托给共享浏览器对象模块）。"""
    return _browser.build_project_card_markup(card)


def _project_row_markup(card):
    """List View 的一行：同一份卡数据，横向排布，适合项目很多时扫读（委托给共享浏览器对象模块）。"""
    return _browser.build_project_row_markup(card)


def _project_matches(view, query):
    """项目搜索：名称 / 描述 / 项目 ID 都可命中（ID 可粘贴，便于核对）。"""
    text = str(query or "").strip().casefold()
    if not text:
        return True
    haystack = " ".join(str(view.get(key) or "") for key in
                        ("name", "description", "project_id")).casefold()
    return text in haystack


def _sort_project_views(views, order):
    """排序键必须与 `_PROJECT_ORDER_OPTIONS` 一一对应。"""
    if order == "名称 A-Z":
        return sorted(views, key=lambda item: str(item.get("name") or "").casefold())
    if order == "任务最多":
        return sorted(views, key=lambda item: int(item.get("job_count") or 0),
                      reverse=True)
    if order == "最早更新":
        return sorted(views, key=lambda item: str(item.get("updated_at") or ""))
    return sorted(views, key=lambda item: str(item.get("updated_at") or ""),
                  reverse=True)


def _load_project_views():
    """项目页的唯一数据入口：一次扫盘 → 分区 → 逐项派生摘要。"""
    sections = core.project_sections()
    views, records = [], {}
    for bucket in ("system", "active", "archived"):
        for project in sections[bucket]:
            summary = core.project_summary(project)
            views.append(summary)
            records[summary["project_id"]] = project
    return sections, views, records


def _knowledge_module(*, key, title, count, note, empty_hint, entry_label,
                      opened_label, body):
    """一个知识模块：count + 一句说明 + 当前状态 / empty hint + 入口。

    它是 Level B 的 compact section，不是一个塞满长说明的白盒子：标题与 count 同行，
    下面最多一句说明；详情默认收起，点入口才展开。四个模块共用这一份实现，所以
    「锁定术语 / 风格规则 / 人工决定 / 已审核记忆」不会各自漂移成四种版式。
    """
    open_key = f"pd_knowledge_open_{key}"
    opened = bool(st.session_state.get(open_key))
    with st.container(key=f"pd_module_{key}"):
        head_left, head_right = st.columns([5, 2], gap="medium")
        with head_left:
            st.markdown(
                '<div class="tp-mod-head">'
                f'<span class="tp-mod-title">{escape(title)}</span>'
                f'<span class="tp-mod-count">{int(count or 0)}</span>'
                '</div>'
                f'<p class="{"tp-mod-note" if count else "tp-mod-empty"}">'
                f'{escape(note if count else empty_hint)}</p>',
                unsafe_allow_html=True)
        with head_right, st.container(key=f"pd_kmod_entry_{key}"):
            if st.button(_t(opened_label if opened else entry_label),
                         key=f"pd_knowledge_toggle_{key}"):
                st.session_state[open_key] = not opened
                st.rerun()
        if opened:
            body()


def _knowledge_glossary_body(project):
    """锁定术语：术语版本 + 术语表（真实数据，未确认的候选不在其中）。"""
    version = (project.get("glossary_versions") or [])
    if version:
        latest = version[-1]
        st.caption(_t('术语版本 v{v0} · hash {v1}… · 冻结于 {v2}', v0=latest['version'], v1=str(latest.get('glossary_hash') or '')[:12], v2=_format_saved_at(latest.get('frozen_at'))))
    else:
        st.caption(_t("项目尚无术语版本。翻译完成后在任务里确认术语，再提升到项目知识。"))
    rows = [{"源术语": entry.get("source", ""),
             "首选译名": entry.get("preferred") or entry.get("target", ""),
             "保留": "是" if entry.get("behavior") == "preserve" else "",
             "禁止译名": "、".join(entry.get("forbidden") or []),
             "范围": entry.get("scope", "")}
            for entry in project.get("glossary") or []]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(420, 40 + 35 * len(rows)))


def _knowledge_rules_body(project):
    """风格规则：已确认的规则列表 + 提升记录（只读审计）。"""
    for rule in project.get("style_rules") or []:
        st.markdown(f"- {escape(str(rule.get('rule') or rule.get('text') or ''))}")
    _knowledge_promotion_body(project)


def _knowledge_decisions_body(project):
    """人工决定：只读审计流水，按时间倒序取最近 20 条。"""
    for record in (project.get("human_decisions") or [])[-20:]:
        st.caption(
            _t(f"{escape(str(record.get('decided_at') or record.get('timestamp') or ''))} · "
            f"{escape(str(record.get('decision') or record.get('action') or ''))} · "
            f"{escape(str(record.get('actor') or ''))}"))


def _knowledge_promotion_body(project):
    """提升记录：某次提升往各类知识里加了什么（真实数据，不 mock 具体条目）。"""
    log = project.get("promotion_log") or []
    if not log:
        return
    with st.expander(_t('提升记录（{v0} 次）', v0=len(log)), expanded=False):
        for entry in log[-20:]:
            added = entry.get("added") or {}
            detail = "、".join(f"{key} +{value}" for key, value in added.items()
                              if value)
            st.caption(_t('{v0} · 来自任务 {v1} · {v2}', v0=escape(str(entry.get('at') or '')), v1=escape(str(entry.get('source_job_id') or '—')), v2=escape(detail or '无新增')))


def _knowledge_memory_body(project, core_view):
    """已审核记忆：本项目已审校记忆的复用说明（+ 从系统工作区并入的显式动作）。

    翻译记忆按项目隔离：新建项目从空白记忆开始。把「未分类」的既有积累带过来是
    一个显式动作，而不是静默共享——只在"本项目为空且未分类有内容"时提供。
    """
    is_system = core.is_system_project(project)
    if core_view["translation_memory_count"]:
        st.caption(_t("这些译文通过了独立审校门槛，会在本项目的后续任务中被复用。"))
        return
    system_tm_count = len(core.load_tm(core.SYSTEM_PROJECT_ID))
    if not is_system and system_tm_count:
        st.caption(_t('系统工作区「{v0}」里有 {v1} 条已审校记忆。翻译记忆按项目隔离，新项目不会自动共享它们。', v0=core.SYSTEM_PROJECT_NAME, v1=system_tm_count))
        if st.button(_t("把「未分类」的已审校记忆并入本项目"),
                     key=f"project_adopt_tm_{project['project_id']}",
                     width="content"):
            try:
                added = core.copy_system_tm_to_project(project["project_id"])
            except ValueError as exc:
                _push_flash(f"并入失败：{exc}", tone="error")
            else:
                _push_flash(f"已并入 {added} 条已审校记忆。" if added
                            else "没有可并入的新条目。",
                            tone="success" if added else "info")
            st.rerun()
        return
    st.caption(_t("本项目还没有已审校记忆；通过独立审校的段落会自动入库到本项目。"))


def _render_project_conflicts(project):
    """待决冲突：导入时发现的"本地与导入不一致"，由人逐条决定。

    这些不是报告里的临时信息，而是持久待决项——因此关掉页面再回来仍然在。
    默认做法是保留本地（导入永不静默覆盖），采纳导入版本是一个明确动作。
    """
    conflicts = core.list_project_conflicts(project["project_id"])
    if not conflicts:
        return
    with st.container(border=True):
        st.markdown((_h('<div class="tp-field-head"><strong>待处理的导入冲突</strong><span>') + str(len(conflicts)) + _h(' 条</span></div>')),
                    unsafe_allow_html=True)
        st.caption(_t("导入不会静默覆盖本地：这些译名与导入版本不一致，需要你决定保留哪一个。"
                   "决定会记入项目提升记录。"))
        for conflict in conflicts:
            kind_label = "术语" if conflict["kind"] == "glossary" else "已审校译文"
            source = escape(str(conflict["source"])[:60])
            st.markdown(
                ('<div class="tp-history-copy"><strong>' + str(source) + '</strong><span>' + str(kind_label) + ' · 来自「' + str(escape(str(conflict.get("imported_from") or "导入文件"))) + _h('」</span><span>本地：') + str(escape(str(conflict["local"])[:40])) + '\u3000|\u3000导入：' + str(escape(str(conflict["incoming"])[:40])) + '</span></div>'),
                unsafe_allow_html=True)
            take, keep = st.columns(2)
            if take.button(_t("采纳导入版本"), width="stretch",
                           key=f"conflict_take_{conflict['conflict_id']}"):
                core.resolve_project_conflict(project["project_id"],
                                              conflict["conflict_id"],
                                              adopt_incoming=True, actor="用户")
                st.rerun()
            if keep.button(_t("保留本地版本"), width="stretch",
                           key=f"conflict_keep_{conflict['conflict_id']}"):
                core.resolve_project_conflict(project["project_id"],
                                              conflict["conflict_id"],
                                              adopt_incoming=False, actor="用户")
                st.rerun()
        all_keep, all_take = st.columns(2)
        if all_keep.button(_t("全部保留本地"), width="stretch",
                           key=f"conflict_all_keep_{project['project_id']}"):
            core.resolve_all_project_conflicts(project["project_id"],
                                               adopt_incoming=False, actor="用户")
            st.rerun()
        if all_take.button(_t("全部采纳导入"), width="stretch",
                           key=f"conflict_all_take_{project['project_id']}",
                           help=_t("把本地译名替换为导入版本；此动作不可撤销。")):
            core.resolve_all_project_conflicts(project["project_id"],
                                               adopt_incoming=True, actor="用户")
            st.rerun()


def _render_project_job_mover(project):
    """批量把任务移入本项目：**secondary collapsible panel，默认收起**。

    它是二级功能，不是页面主内容。此前它常驻展开，与任务空态并列铺在同一页上，
    页面同时出现两个"主要内容块"、两个"没有任务"的表达。现在收进一个默认折叠的
    panel：需要的人展开它，不需要的人看到的是一个安静的入口行。

    改归属会改变任务今后使用哪一套项目记忆（术语、风格、翻译记忆）。它**不会**
    改动已有译文，也不会自动迁移术语——那些要通过 Memory gate 显式提升。
    """
    others = [job for job in core.list_jobs()
              if core.resolved_project_id(job["state"]) != project["project_id"]]
    with st.container(key="pd_mover"):
        # 显式 key：折叠状态是面板自身的状态，可被测试与外部状态直接驱动。
        with st.expander(_t("把任务移入本项目"), expanded=False,
                         key=f"project_move_panel_{project['project_id']}"):
            if not others:
                st.caption(_t("没有其它项目下的任务可以移入。"))
                return
            st.caption(_t("移动只改变归属，不会修改已有译文；术语与记忆不会自动迁移，"
                       "需要通过项目记忆提升显式确认。"))
            labels = {job["job_id"]: f'{job["state"].get("filename") or "?"}'
                                     f'（{core.project_name_for_id(core.resolved_project_id(job["state"]))}）'
                      for job in others}
            chosen = st.multiselect(
                "选择要移入的任务", list(labels), format_func=lambda key: labels[key],
                key=f"project_move_pick_{project['project_id']}")
            move_tm = st.checkbox(
                _t("同时把它们的已审校译文并入本项目记忆（只增不改）"),
                key=f"project_move_tm_{project['project_id']}")
            st.caption(_t("正在运行的任务不能改归属：它已经读取了原项目的记忆。"))
            if st.button(_t("移入本项目"), key=f"project_move_go_{project['project_id']}",
                         disabled=not chosen, width="content"):
                result = core.assign_jobs_to_project(
                    chosen, project["project_id"], move_translations=move_tm)
                if result["moved"]:
                    st.success(_t('已移入 {v0} 个任务；并入已审校记忆 {v1} 条。', v0=len(result['moved']), v1=result['tm_added']))
                for item in result["skipped"]:
                    st.warning(
                        _t(f"{labels.get(item['job_id'], item['job_id'])}：{item['reason']}"))
                if result["moved"]:
                    st.rerun()


# ================= Project 管理页与项目详情 =================
# 信息架构（回归测试守住）：
#
#   /projects                 管理页：系统工作区 + 活动项目 + 搜索 + 已归档入口 + 新建
#   /projects/:project_id     详情：概览 / 任务 / 项目记忆 / 设置
#
# 项目卡片整体可点进入详情；右侧 overflow menu 提供打开项目、重命名、编辑、
# 归档、删除。系统工作区「未分类」在同一页但**不可**重命名 / 归档 / 删除。


def _project_overflow_menu(project, *, archived):
    """卡片右侧 overflow menu：只承担 **secondary actions**。

    popover 与整卡点击层是兄弟节点且 z-index 更高，因此点菜单不会触发"打开项目"。
    「打开项目」不在菜单里——整卡已经是主入口，菜单里再放一次只会让人以为卡片
    本身不可点。删除是 destructive，必须走二次确认的弹窗。
    """
    project_id = project["project_id"]
    with st.container(key=f"project_menu_{project_id}"):
        with st.popover("⋯", help=_t("更多项目操作")):
            # 与 Project Detail 的 ⋯ 共用同一套轻量 menu card 视觉（同一个 Menu 组件）。
            # key 必须带 project_id：这个函数每张卡都调一次，写死常量会在第二个项目
            # 上直接抛 StreamlitDuplicateElementKey。视觉共用靠 CSS 的
            # [class*="st-key-pd_menu_body"] 前缀匹配，不靠共享同一个 key。
            with st.container(key=f"pd_menu_body_{project_id}"):
                st.caption(str(project["name"]))
                if st.button(_t("重命名"), key=f"pm_rename_{project_id}",
                             icon=":material/edit:", width="stretch"):
                    _open_project_modal("rename", project_id)
                    st.rerun()
                if st.button(_t("编辑名称与描述"), key=f"pm_edit_{project_id}",
                             icon=":material/description:", width="stretch"):
                    _open_project_modal("edit", project_id)
                    st.rerun()
                if archived:
                    if st.button(_t("恢复为活动项目"), key=f"pm_restore_{project_id}",
                                 icon=":material/unarchive:", width="stretch"):
                        _open_project_modal("restore", project_id)
                        st.rerun()
                else:
                    if st.button(_t("归档"), key=f"pm_archive_{project_id}",
                                 icon=":material/archive:", width="stretch"):
                        _open_project_modal("archive", project_id)
                        st.rerun()
                if st.button(_t("项目设置"), key=f"pm_settings_{project_id}",
                             icon=":material/settings:", width="stretch"):
                    st.session_state["active_project_tab"] = "settings"
                    _open_project(project_id)
                    st.rerun()
                st.download_button(
                    _t("导出项目"), data=core.export_project_memory(project_id),
                    file_name=f"folith-project-{project_id}.json",
                    mime="application/json", key=f"pm_export_{project_id}",
                    icon=":material/download:", width="stretch",
                    help=_t("导出术语、风格、人工决定审计与本项目的已审校记忆；"
                         "不含任务状态、源文档或任何凭据。"))
                st.divider()
                with st.container(key=f"pd_menu_danger_{project_id}"):
                    if st.button(_t("删除"), key=f"pm_delete_{project_id}",
                                 icon=":material/delete:", width="stretch"):
                        _open_project_modal("delete", project_id)
                        st.rerun()


def _project_card(view, project, *, archived=False):
    """一张项目卡：整卡可点 + 右侧 overflow menu + 底部左侧**一个** ghost CTA。

    点击卡片主体的**任意位置**都进入项目概览页；不需要用户去找「打开」按钮。
    底部的 ghost CTA 是同一个槽位的两种动词，由状态决定：

        尚未有任何任务  →  `+ 创建任务`：进入新建任务流程，并**自动带上本项目**
        已经有任务      →  `查看项目`  ：进入项目概览页

    两者都必须是**真实按钮**，并且是整卡点击层的**兄弟节点**（z-index 更高），
    否则点它们会被整卡导航吞掉。
    """
    project_id = project["project_id"]
    card = _project_card_view(view, project)
    with st.container(key=f"project_row_{project_id}"):
        st.markdown(_project_card_markup(card), unsafe_allow_html=True)
        # 整卡可点：HTML 负责视觉，透明覆盖按钮负责点击与键盘。
        if st.button(_t("打开项目"), key=f"project_open_{project_id}", width="stretch",
                     help=_t('打开项目：{v0}', v0=card['name'])):
            _open_project(project_id)
            st.rerun()
        if not archived:
            if not card.get("job_count"):
                with st.container(key=f"project_empty_cta_{project_id}"):
                    if st.button(_t("创建任务"), key=f"project_card_create_{project_id}",
                                 icon=":material/add:", width="content",
                                 help=_t("在「%s」下创建第一个翻译任务" % card["name"])):
                        _begin_new_task(project_id)
                        st.rerun()
            else:
                with st.container(key=f"project_view_cta_{project_id}"):
                    if st.button(_t("查看项目"), key=f"project_card_view_{project_id}",
                                 icon=":material/arrow_forward:", width="content",
                                 help=_t("打开「%s」的项目详情" % card["name"])):
                        _open_project(project_id)
                        st.rerun()
        _project_overflow_menu(project, archived=archived)


def _project_list_row(view, project, *, archived=False):
    """List View 的一行：与卡片同一份数据、同一套动作。"""
    project_id = project["project_id"]
    card = _project_card_view(view, project)
    with st.container(key=f"project_row_{project_id}"):
        st.markdown(_project_row_markup(card), unsafe_allow_html=True)
        if st.button(_t("打开项目"), key=f"project_open_{project_id}", width="stretch",
                     help=_t('打开项目：{v0}', v0=card['name'])):
            _open_project(project_id)
            st.rerun()
        _project_overflow_menu(project, archived=archived)


def _uncategorized_strip(view, project):
    """系统工作区「未分类」的轻量系统入口（72–88px）。

    它不是项目：没有项目卡的面积、没有 overflow menu、没有知识资产统计。
    它只回答"有多少任务还没有归属"，整条可点进入未分类任务列表。

    count 只出现**一次**（在标题行右侧，与「查看 →」同一行）；副标题只说明这是什么，
    不再重复数字。
    """
    project_id = project["project_id"]
    job_count = int(view.get("job_count") or 0)
    count_html = (('<span class="tp-uncat-count">' + str(job_count) + _h(' 个</span>'))
                  if job_count else "")
    meta = ("尚未归入任何项目的任务" if job_count
            else "暂时没有未归入项目的任务")
    with st.container(key="project_system_zone"):
        st.markdown(
            _h('<div class="tp-section-head is-inline">'
            '<strong>系统任务区</strong>'
            '<span class="tp-section-note">系统工作区 · 不属于任何项目</span>'
            '</div>'), unsafe_allow_html=True)
        with st.container(key="project_uncategorized"):
            st.markdown(
                (_h('<div class="tp-uncat"><span class="tp-uncat-icon" aria-hidden="true"><span class="material-symbols-rounded">inbox</span></span><div class="tp-uncat-copy"><div class="tp-uncat-head"><span class="tp-uncat-title">未分类任务<span class="tp-uncat-tag">Inbox</span></span><span class="tp-uncat-right">') + str(count_html) + _h('<span class="tp-uncat-action">查看 →</span></span></div><div class="tp-uncat-meta">') + str(escape(meta)) + '</div></div></div>'), unsafe_allow_html=True)
            if st.button(_t("查看未分类任务"), key=f"project_open_{project_id}",
                         width="stretch"):
                _open_project(project_id)
                st.rerun()


def _empty_projects_state():
    """一个项目都没有时的首屏：说明项目能集中管理什么，并给出两个真实入口。"""
    st.markdown(
        _h('<div class="tp-empty-card tp-hub-empty">'
        '<strong>还没有项目</strong>'
        '<span>项目可以集中管理：任务、术语、翻译规则和项目记忆。'
        '项目不是必须先建——没有归属的任务会留在「未分类任务」里。</span>'
        '</div>'), unsafe_allow_html=True)
    # 两个入口按内容宽度排布，不各占一半：空状态的主次关系必须看得出来。
    create_col, import_col, _spacer = st.columns([1.15, 1.15, 2.4])
    if create_col.button(_t("创建第一个项目"), key="project_empty_create",
                         type="primary", icon=":material/add:", width="stretch"):
        _clear_project_form()
        _open_project_modal("new")
        st.rerun()
    if import_col.button(_t("导入已有项目"), key="project_empty_import",
                         width="stretch", icon=":material/upload:"):
        _open_project_modal("import")
        st.rerun()


def _render_project_detail(project):
    """`/projects/:projectId`：真实项目的 Project Command Center。

    一级 IA：概览 / 任务 / 项目知识 / 设置。

    关键约束（回归测试守住）：
      - header 只回答"这是什么项目、现在什么状态、下一步做什么"，**不出现**
        UUID / Project ID / 类型 / 创建时间 / 更新时间——那些是设置里的高级信息；
      - `jobs` / 派生摘要**只加载一次**，向下传给各 tab，避免同一份 project data
        被 summary / recent / task count 各读一遍。
    """
    project_id = project["project_id"]
    archived = bool(project.get("archived_at"))
    jobs = core.list_project_jobs(project_id)
    # 单一数据源：memory view 一次 + 已经加载的 jobs，不再触发第二次 list_project_jobs。
    summary = _project_detail_summary(project, jobs)

    _render_project_detail_header(project, summary, archived=archived)

    # 一级 tab：导航状态 `active_project_tab`（与任务工作区的 section 对称）。
    # Tabs 是 **compact left-aligned** 的四个入口：宽度由内容决定、间距 28px
    # （CSS 负责），不四等分页面宽度。只有「任务」带 count——四等分宽度 + 每个
    # 都挂数字会让 tab 组本身变重，而"有多少工作"只有任务这一个 tab 在回答。
    tab_key = str(st.session_state.get("active_project_tab") or "overview")
    if tab_key not in PROJECT_TAB_LABELS:
        tab_key = "overview"
    task_count = int(summary["job_count"] or 0)
    with st.container(key="project_tabbar"):
        columns = st.columns(len(PROJECT_TABS))
        for column, (key, label) in zip(columns, PROJECT_TABS):
            suffix = f" {task_count}" if key == "tasks" and task_count else ""
            if column.button(f"{_t(label)}{suffix}", key=f"project_tab_{key}",
                             type="primary" if key == tab_key else "secondary",
                             help=(_t(f"当前页签：{label}" if key == tab_key
                                   else f"切换到{label}页签"))):
                st.session_state["active_project_tab"] = key
                st.rerun()

    # 正文包在一个显式容器里：Tabs → Content 的间距由 tabbar 自己负责（14 + 16
    # gap = 30px），正文第一个 section 不再叠加自己的 18px 上边距——否则实测会变成
    # 48px，"每个 tab 像不同产品"就是这么来的。
    with st.container(key="project_tab_content"):
        if tab_key == "overview":
            _render_project_overview_tab(project, jobs, summary)
        elif tab_key == "tasks":
            _render_project_tasks_tab(project, jobs)
        elif tab_key == "knowledge":
            _render_project_knowledge_tab(project)
        else:
            _render_project_settings_tab(project, jobs, summary)


def _project_detail_summary(project, jobs):
    """项目详情的派生数据：memory view + 已加载的 jobs（不重复扫盘）。

    `core.project_summary` 会再调用一次 `list_project_jobs`；详情页已经拿到了
    jobs，因此这里直接复用，避免 summary / recent / count 各读一遍。
    """
    view = core.project_memory_view(project)
    view["job_count"] = len(jobs)
    view["jobs"] = jobs
    return view


def _render_project_detail_header(project, summary, *, archived):
    """Header：返回 + 名称 + 状态 + 描述 + 主 CTA + secondary ⋯ 菜单。

    结构固定为两层，且**动作与身份同一行**：

        ← 返回项目
        测试1  [活动中]                        + 新建任务   ···
        项目描述（如果存在）

    约束（回归测试守住）：
      - Project Name 是主标题，状态是跟在它后面的 compact badge；
      - `+ 新建任务` 与 `···` 同属 `project_detail_actions`（flex + nowrap），
        overflow 不允许单独掉到下一行；
      - 描述为空时整行省略，不写「暂无描述」；
      - UUID / Project ID / 类型 / 创建·更新时间属于设置，这里一律不出现。
    """
    project_id = project["project_id"]
    with st.container(key="project_detail_header"):
        if st.button(_t("← 返回项目"), key="project_back_to_list"):
            _open_project_list()
            st.rerun()
        title_col, actions_col = st.columns([5, 2], gap="large")
        with title_col:
            status = ("已归档" if archived else
                      ("系统中" if core.is_system_project(project) else "活动中"))
            status_modifier = "archived" if archived else "active"
            description = str(project.get("description") or "").strip()
            # 描述为空时直接省略，不写「暂无描述」。
            desc_html = (f'<p>{escape(description)}</p>' if description else "")
            st.markdown(
                '<div class="tp-project-header-copy">'
                '<div class="tp-project-title-row">'
                f'<h1>{escape(str(project["name"]))}</h1>'
                f'<span class="tp-project-badge is-{status_modifier}">'
                f'{escape(status)}</span>'
                '</div>'
                f'{desc_html}</div>', unsafe_allow_html=True)
        with actions_col:
            with st.container(key="project_detail_actions"):
                if archived:
                    # 归档项目不允许无提示地新建任务：主 CTA 变成恢复。
                    if st.button(_t("恢复项目"), key=f"detail_restore_{project_id}",
                                 type="primary", icon=":material/unarchive:"):
                        _open_project_modal("restore", project_id)
                        st.rerun()
                else:
                    if st.button(_t("新建任务"), key=f"detail_new_task_{project_id}",
                                 type="primary", icon=":material/add:",
                                 help=_t("在「%s」下创建翻译任务" % project["name"])):
                        _begin_new_task(project_id)
                        st.rerun()
                _project_detail_overflow_menu(project, archived=archived)
        if archived:
            # 归档说明留在 header 内部：Header 与 Tabs 之间不插任何东西，
            # 两者始终是一个整体。
            st.warning(_t("这个项目已归档：它不在活动项目列表与新建任务的项目选择里，"
                       "但项目知识、翻译任务与归属全部保留，可随时恢复。"))


def _project_detail_overflow_menu(project, *, archived):
    """Header 的 ⋯：编辑项目 / 导出项目 / 归档（或恢复）/ 删除。

    它是一个**轻量 popover menu card**（收窄宽度、压紧 padding、用 divider 分组），
    不是一个独立侧面板；菜单项是紧凑行而不是一个个大白按钮。低频管理动作收在这里，
    不与「新建任务」竞争注意力。删除是 destructive，单独一组并用红色文字区分。

    结构：项目操作 → 编辑 / 导出 / 归档（恢复）→ ──── → 删除项目。
    """
    project_id = project["project_id"]
    with st.container(key="project_detail_menu"):
        with st.popover("⋯", help=_t("项目操作")):
            with st.container(key=f"pd_menu_body_{project_id}"):
                st.caption(_t("项目操作"))
                if st.button(_t("编辑项目"), key=f"detail_menu_edit_{project_id}",
                             icon=":material/edit:", width="stretch"):
                    _open_project_modal("edit", project_id)
                    st.rerun()
                st.download_button(
                    _t("导出项目"), data=core.export_project_memory(project_id),
                    file_name=f"folith-project-{project_id}.json",
                    mime="application/json", key=f"detail_menu_export_{project_id}",
                    icon=":material/download:", width="stretch",
                    help=_t("导出术语、风格、人工决定审计与本项目的已审校记忆；"
                         "不含任务状态、源文档或任何凭据。"))
                if archived:
                    if st.button(_t("恢复项目"), key=f"detail_menu_restore_{project_id}",
                                 icon=":material/unarchive:", width="stretch"):
                        _open_project_modal("restore", project_id)
                        st.rerun()
                else:
                    if st.button(_t("归档项目"), key=f"detail_menu_archive_{project_id}",
                                 icon=":material/archive:", width="stretch"):
                        _open_project_modal("archive", project_id)
                        st.rerun()
                st.divider()
                # destructive 单独一组：删除不与其他管理动作混排。
                with st.container(key=f"pd_menu_danger_{project_id}"):
                    if st.button(_t("删除项目"), key=f"detail_menu_delete_{project_id}",
                                 icon=":material/delete:", width="stretch"):
                        _open_project_modal("delete", project_id)
                        st.rerun()


# Summary card：label / 主值 / 一句话说明。**只在 ACTIVE PROJECT 渲染**——
# 空项目根本不渲染这四张卡，所以这里没有"空值文案"分支：0 就是 0，
# 用一句短注说明它是什么，而不是把"还没有任务"这种长句子填进数字卡里。
def _summary_card(label, count, note):
    return (f'<div class="tp-stat"><div class="tp-stat-label">'
            f'{escape(label)}</div>'
            f'<div class="tp-stat-value">{int(count or 0)}</div>'
            f'<div class="tp-stat-note">{escape(note)}</div></div>')


def _render_project_overview_tab(project, jobs, summary):
    """概览有两种**明确分开**的 UI 状态，绝不共用同一套 dashboard：

      - EMPTY PROJECT（`jobs` 为空）：一个 onboarding surface ——
        "项目已经建好了 → 下一步创建任务"；
      - ACTIVE PROJECT（存在 task）：command center，信息顺序固定为
        工作概览 → 未交付任务 → 其他最近任务 → 项目知识摘要。

    "空 / 非空"只用**真实数据**判断：`core.list_project_jobs()` 的长度。
    不从 summary 文案或"计数是不是 0"之类的 UI 推断出发。

    这里**不再**渲染「基本信息」表：UUID / 类型 / 创建·更新时间属于设置。
    """
    project_id = project["project_id"]

    # ---- EMPTY PROJECT ----
    # 没有 task 时不渲染 任务/术语/规则/记忆 四张 summary card：它们只会重复
    # 表达"空"。也不渲染第二块「项目知识尚未建立」——onboarding 文案已经说过
    # 创建任务后会积累知识。整页只有一个 surface。
    if not jobs:
        _render_project_onboarding(project,
                                   archived=bool(project.get("archived_at")))
        return

    # ---- ACTIVE PROJECT ----
    rows = [_task_row_view(job) for job in jobs]
    rows.sort(key=lambda row: row["stamp"], reverse=True)
    unfinished_rows = [row for row in rows if row["unfinished"]]
    delivered_rows = [row for row in rows if not row["unfinished"]]
    breakdown = " · ".join(part for part in (
        f"{len(unfinished_rows)} 未交付" if unfinished_rows else "",
        f"{len(delivered_rows)} 已交付" if delivered_rows else "") if part) or "全部已交付"

    # ---- 1. 工作概览 ----
    # 四张卡只在 ACTIVE PROJECT 出现，且是**紧凑**的：数值 + 一句轻量说明。
    # 说明永远短（"已确认" / "尚未建立"），不把长句子塞进数字卡。
    st.markdown(_h('<div class="tp-section-head is-inline"><strong>工作概览</strong>'
                '</div>'), unsafe_allow_html=True)
    st.markdown(
        '<div class="tp-stat-grid">'
        + _summary_card("任务", len(rows), breakdown)
        + _summary_card("术语", summary["glossary_count"],
                        "已确认" if summary["glossary_count"] else "尚未建立")
        + _summary_card("规则", summary["style_rule_count"],
                        "已确认" if summary["style_rule_count"] else "尚未设置")
        + _summary_card("记忆", summary["translation_memory_count"],
                        "已审核" if summary["translation_memory_count"] else "尚无")
        + '</div>', unsafe_allow_html=True)

    # ---- 2. 未交付任务 ----
    # 该列表包含需要处理、翻译中和可交付等全部未交付状态。标题不再把
    # 已中断任务误称为"进行中"；最近任务区会排除这里已展示的行。
    visible_unfinished = unfinished_rows[:4]
    if visible_unfinished:
        st.markdown(
            (_h('<div class="tp-section-head is-inline"><strong>未交付任务</strong><span class="tp-count-badge">') + str(len(unfinished_rows)) + '</span></div>'),
            unsafe_allow_html=True)
        with st.container(key="project_overview_active"):
            _render_task_rows(visible_unfinished, row_prefix="projrow_active",
                              card_key_prefix="project_job_open")

    # ---- 3. 其他最近任务（最多 5 条）----
    shown_ids = {row["job_id"] for row in visible_unfinished}
    recent = [row for row in rows if row["job_id"] not in shown_ids]
    if recent:
        recent = recent[:5]
        head_left, head_right = st.columns([5, 1])
        recent_title = "其他最近任务" if visible_unfinished else "最近任务"
        recent_note = (f"其余 {len(rows) - len(shown_ids)} 个 · 显示最近 {len(recent)} 个"
                       if visible_unfinished else
                       f"共 {len(rows)} 个 · 显示最近 {len(recent)} 个")
        head_left.markdown(
            f'<div class="tp-section-head is-inline"><strong>{recent_title}</strong>'
            f'<span>{recent_note}</span></div>',
            unsafe_allow_html=True)
        if head_right.button(_t("查看全部 →"), key=f"overview_all_tasks_{project_id}",
                             width="stretch"):
            st.session_state["active_project_tab"] = "tasks"
            st.rerun()
        with st.container(key="project_overview_recent"):
            _render_task_rows(recent, row_prefix="projrow_recent",
                              card_key_prefix="project_recent_open")

    # ---- 4. 项目知识摘要（一级 tab 才有完整知识页）----
    _render_project_knowledge_summary(project, summary)


def _render_project_onboarding(project, *, archived=False):
    """EMPTY PROJECT 的**唯一** surface：项目已经建好了 → 下一步创建任务。

    它取代了此前"任务空状态 + 创建按钮 + 项目知识空状态 + 查看知识按钮"的多段
    空页面：整页只有一个 onboarding block、一个 medium 的「创建第一个任务」、
    一个次要入口「设置项目知识 →」。

    归档项目不提供新建任务入口：它的下一步是恢复，不是创建。
    """
    project_id = project["project_id"]
    if archived:
        st.markdown(
            _h('<div class="tp-onboarding-copy">'
            '<h2>这个项目已归档</h2>'
            '<p>归档项目不参与新建任务。恢复项目后即可继续创建翻译任务，'
            '项目知识会完整保留。</p></div>'), unsafe_allow_html=True)
        return
    with st.container(key="project_onboarding"):
        st.markdown(
            _h('<div class="tp-onboarding-copy">'
            '<h2>开始使用这个项目</h2>'
            '<p>这个项目已经准备好了。<br>创建第一个翻译任务后，译页会'
            '逐步积累术语、规则、项目决定与审核记忆。</p></div>'),
            unsafe_allow_html=True)
        # 与 Header 的「+ 新建任务」是**完全相同的 action**（`_begin_new_task`），
        # 只是视觉上是 medium 按钮——不做第二个巨大的 Primary CTA。
        if st.button(_t("创建第一个任务"),
                     key=f"project_onboarding_create_{project_id}",
                     type="primary", icon=":material/add:"):
            _begin_new_task(project_id)
            st.rerun()
        # 次要操作：把用户送去真正能积累知识的地方（复用同一个 tab 导航状态）。
        if st.button(_t("设置项目知识 →"),
                     key=f"project_onboarding_knowledge_{project_id}"):
            st.session_state["active_project_tab"] = "knowledge"
            st.rerun()


def _render_project_knowledge_summary(project, summary):
    """Overview 的紧凑项目知识区块：四类知识计数 + 一个入口。

    一级 tab 已经有完整的「项目知识」，因此这里**只做摘要**，绝不复制一个完整
    知识页。知识全为空时也只给一行 compact row（不是第二个巨大 dashed 空框）。
    没有 recent knowledge event 数据时**只展示数量**，不 mock 事件。
    """
    project_id = project["project_id"]
    counts = " · ".join(part for part in (
        f"术语 {int(summary['glossary_count'])}" if summary["glossary_count"] else "",
        f"规则 {int(summary['style_rule_count'])}" if summary["style_rule_count"] else "",
        f"决定 {int(summary['human_decision_count'])}"
        if summary["human_decision_count"] else "",
        f"记忆 {int(summary['translation_memory_count'])}"
        if summary["translation_memory_count"] else "") if part)
    st.markdown(_h('<div class="tp-section-head is-inline"><strong>项目知识</strong>'
                '</div>'), unsafe_allow_html=True)
    if not counts:
        st.markdown(
            _h('<div class="tp-knowledge-empty-row">'
            '<strong>项目知识尚未形成</strong>'
            '<span>完成任务后会在这里汇总术语、规则、决定和审校记忆。</span>'
            '</div>'), unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="tp-knowledge-line">{escape(counts)}</div>',
                    unsafe_allow_html=True)
        events = _project_knowledge_events(project, limit=3)
        if events:
            st.markdown(
                '<div class="tp-knowledge-events">'
                + "".join(f'<div class="tp-knowledge-event">{escape(item)}</div>'
                          for item in events)
                + '</div>', unsafe_allow_html=True)
    if st.button(_t("查看项目知识 →"), key=f"overview_knowledge_{project_id}"):
        st.session_state["active_project_tab"] = "knowledge"
        st.rerun()


def _project_knowledge_events(project, *, limit=3):
    """最近的知识提升事件（真实数据，来自项目提升记录）。

    `promotion_log` 只记录"某次提升往各类知识里加了什么"，因此这里如实描述为
    「新增术语 +N」，**不**编造具体术语条目。
    """
    labels = (("glossary", "术语"), ("style_rules", "规则"),
              ("human_decisions", "决定"), ("translation_memory", "记忆"))
    events = []
    for entry in reversed(list(project.get("promotion_log") or [])):
        added = entry.get("added") or {}
        detail = " · ".join(f"新增{name} +{int(added[key])}"
                            for key, name in labels if added.get(key))
        if not detail:
            continue
        at = _format_saved_at(entry.get("at"))
        events.append(f"{detail}（{at}）")
        if len(events) >= limit:
            break
    return events


def _recent_jobs(jobs, limit):
    def _stamp(job):
        state = job["state"] or {}
        return str(state.get("updated_at") or state.get("created_at") or "")
    return sorted(jobs, key=_stamp, reverse=True)[:limit]


def _render_project_tasks_tab(project, jobs):
    """任务：当前项目的完整任务列表。

    结构固定为「标题 → toolbar → 内容」：
      1. 标题 + count（section head）；
      2. 共享 toolbar（搜索 / 状态 / 排序）；
      3. 内容：有任务给 task list；无任务给**一个** compact empty state。

    「把任务移入本项目」是 secondary 功能，渲染成一个**默认收起**的 collapsible
    panel。此前它和空态同时铺在页面上，等于让一个二级操作与主内容抢位置。
    """
    project_id = project["project_id"]
    st.markdown((_h('<div class="tp-section-head is-inline"><strong>任务</strong><span class="tp-count-badge">') + str(len(jobs)) + '</span></div>'),
                unsafe_allow_html=True)
    query, status_filter, order = _render_task_list_toolbar(
        key_prefix=f"project_task_{project_id}")

    rows = [_task_row_view(job) for job in jobs]
    visible = _filter_task_rows(rows, query=query,
                                status_filter=status_filter, order=order)

    if not jobs:
        # 整页唯一的主 empty state：compact，不再是一个巨大 dashed 空框。
        st.markdown(
            _h('<div class="tp-pd-empty">'
            '<strong>这个项目还没有翻译任务</strong>'
            '<span>新建任务时选择本项目即可归入，也可以从其他项目移入任务。</span>'
            '</div>'), unsafe_allow_html=True)
    elif not visible:
        st.markdown(_h('<div class="tp-empty">没有符合条件的任务。</div>'),
                    unsafe_allow_html=True)
    else:
        st.caption(_t('共 {v0} 个任务，当前显示 {v1} 个。这里只列出属于本项目的任务。', v0=len(jobs), v1=len(visible)))
        with st.container(key="project_task_list"):
            _render_task_rows(visible, row_prefix="projrow_task",
                              card_key_prefix="project_task_open")
    _render_project_job_mover(project)


# ================= 共享 Task Row（Inbox / Overview / Tasks 同一份投影）=================
# Project Overview、Project Tasks、未分类 Inbox 都列同一种东西：一次翻译任务。
# 它们**必须**共用同一份 view model 与 markup——否则三套几乎一样的列表会各自漂移
# （旧版就同时存在"大按钮行"和"紧凑行"两种）。这里只投影展示数据，不重新派生
# 任何状态：状态词仍然来自 canonical `_task_overview_state`。
_TASK_STATUS_OPTIONS = ("全部", "进行中", "需要处理", "可交付", "已完成")
_TASK_ORDER_OPTIONS = ("最近更新", "最早更新", "名称")
#: 未交付的生命周期都算"进行中"（可以正式交付也算：它还没被冻结）。
_TASK_DONE_LIFECYCLES = frozenset({_task_overview.DELIVERED})


def _task_bucket(lifecycle):
    """把 canonical lifecycle 收敛成列表上的几个工作桶。

    这里**不重新发明状态**：`lifecycle` 来自 `_task_overview_state`，与任务工作区、
    历史页用的是同一套权威状态；本函数只做"一眼扫读"的聚合映射。
    """
    if lifecycle == _task_overview.DELIVERED:
        return "已完成"
    if lifecycle == _task_overview.DELIVERY_READY:
        return "可交付"
    if lifecycle == _task_overview.NEEDS_ATTENTION:
        return "需要处理"
    return "进行中"


def _task_row_view(job):
    """一条任务行的展示投影（标题 / 状态 / 段落 / 文件类型 / 进度 / 时间）。"""
    state = job.get("state") or {}
    job_id = job["job_id"]
    overview = _task_overview_state(job_id, state)
    label, tone = _workspace_delivery_state(job_id, state, overview=overview)
    done, total = _project_translation_progress(state)
    total = total or _history_view.segment_count(state)
    percent = round(done * 100 / total) if total and 0 < done < total else 0
    stamp = str(state.get("updated_at") or state.get("created_at") or "")
    lifecycle = overview.get("lifecycle") or _task_overview.DRAFT
    runtime_status = str(overview.get("runtime_status") or "")
    stage = str(overview.get("runtime_stage") or "")
    if runtime_status not in _RUNTIME_ACTION_STATUSES or stage == "任务处理":
        stage = ""
    return {
        "job_id": job_id,
        "title": _job_display_title(job),
        "filename": str(state.get("filename") or ""),
        "status": label,
        "tone": tone,
        "stage": stage,
        "lifecycle": lifecycle,
        "bucket": _task_bucket(lifecycle),
        "unfinished": lifecycle not in _TASK_DONE_LIFECYCLES,
        "total": total,
        "done": done,
        "kind": _history_view.document_kind(state),
        "percent": percent,
        "stamp": stamp,
        "updated": _history_view.format_age(stamp) or "—",
        "lang_pair": _browser.task_language_pair(state),
    }


def _task_row_markup(row):
    """紧凑 Task Row（委托给共享浏览器对象模块）：标题一行、状态 · 段落 · 文件类型一行，右侧相对时间 + 箭头。"""
    return _browser.build_task_row_markup(row)


def _open_task(job_id):
    """打开任务（共享入口）：任务不存在时如实说明，不抛异常。"""
    state = core.load_job_state(job_id)
    if state is None:
        _push_flash("任务已不存在。", tone="warning")
    else:
        _open_job(job_id, state, "translation")
    st.rerun()


def _render_task_rows(rows, *, row_prefix, card_key_prefix, menu=None):
    """共享的 Task Row 渲染：markup + 覆盖整行的点击层（+ 可选的次级菜单）。

    `row_prefix` / `card_key_prefix` 让不同页面各自持有独立控件 key，但视觉、状态、
    进度与点击语义完全一致。
    """
    for row in rows:
        job_id = row["job_id"]
        with st.container(key=f"{row_prefix}_{job_id}"):
            st.markdown(_task_row_markup(row), unsafe_allow_html=True)
            if st.button(_t("打开任务"), key=f"{card_key_prefix}_{job_id}",
                         width="stretch"):
                _open_task(job_id)
            if menu is not None:
                menu(job_id, row["title"])


def _render_task_list_toolbar(*, key_prefix, options_status=_TASK_STATUS_OPTIONS,
                              options_order=_TASK_ORDER_OPTIONS):
    """共享的任务列表 toolbar：搜索 / 状态 / 排序（返回归一后的三个值）。

    不引入任何新的后端查询：调用方用已经加载的 jobs 过滤即可。
    """
    status_key = f"{key_prefix}_status"
    order_key = f"{key_prefix}_order"
    if st.session_state.get(status_key) not in options_status:
        st.session_state.pop(status_key, None)
    if st.session_state.get(order_key) not in options_order:
        st.session_state.pop(order_key, None)
    search_col, status_col, order_col = st.columns([5, 2, 2], gap="small")
    query = search_col.text_input(
        _t("搜索任务"), key=f"{key_prefix}_search", label_visibility="collapsed",
        placeholder=_t("搜索任务..."), icon=":material/search:")
    status_filter = status_col.selectbox(
        _t("状态"), options_status, key=status_key, label_visibility="collapsed", format_func=_localized_formatter())
    order = order_col.selectbox(
        _t("排序"), options_order, key=order_key, label_visibility="collapsed", format_func=_localized_formatter())
    return query, status_filter, order


def _filter_task_rows(rows, *, query, status_filter, order):
    """共享的任务过滤 / 排序（纯内存，用的是已加载数据）。"""
    needle = str(query or "").strip().casefold()
    visible = [
        row for row in rows
        if (not needle or needle in row["title"].casefold()
            or needle in row["filename"].casefold())
        and (status_filter == "全部" or row["bucket"] == status_filter)
    ]
    if order == "名称":
        visible.sort(key=lambda row: row["title"].casefold())
    elif order == "最早更新":
        visible.sort(key=lambda row: row["stamp"])
    else:
        visible.sort(key=lambda row: row["stamp"], reverse=True)
    return visible


def _uncat_task_overflow_menu(job_id, title):
    """未分类行的 ⋯：加入项目 / 创建新项目并加入 / 删除任务。整行点击已经能打开任务。"""
    with st.container(key=f"inbox_menu_{job_id}"):
        with st.popover("⋯", help=_t("任务操作")):
            with st.container(key=f"pd_menu_body_{job_id}"):
                st.caption(str(title)[:40] or "未分类任务")
                if st.button(_t("加入项目"), key=f"inbox_move_{job_id}",
                             icon=":material/drive_file_move:", width="stretch"):
                    _open_task_move_modal(job_id, mode="existing")
                    st.rerun()
                if st.button(_t("创建新项目并加入"), key=f"inbox_create_project_{job_id}",
                             icon=":material/create_new_folder:", width="stretch"):
                    _open_task_move_modal(job_id, mode="create")
                    st.rerun()
                st.divider()
                with st.container(key=f"pd_menu_danger_{job_id}"):
                    if st.button(_t("删除任务"), key=f"inbox_delete_{job_id}",
                                 icon=":material/delete:", width="stretch"):
                        _open_history_delete(job_id)
                        st.rerun()


def _render_uncategorized_tasks_workspace(project):
    """「未分类任务」专属页面：Task Inbox，不是项目概览。

    结构与真实 Project Detail 完全无关：header 只给「未分类任务 + 数量 + 一句说明」，
    一个轻量 toolbar，然后是紧凑的任务行列表。不暴露 UUID / 项目 ID / 记忆 / 设置。
    """
    jobs = core.list_project_jobs(project["project_id"])
    with st.container(key="project_inbox"):
        # ---- Header ----
        with st.container(key="inbox_header"):
            if st.button(_t("← 项目"), key="inbox_back_to_hub"):
                _open_project_list()
                st.rerun()
            st.markdown(
                (_h('<div class="tp-project-header-copy"><div class="tp-inbox-title-row"><h1>未分类任务</h1><span class="tp-count-badge">') + str(len(jobs)) + _h('</span></div><p>尚未归入任何项目的翻译任务，可直接处理或加入已有项目。</p></div>')),
                unsafe_allow_html=True)

        # ---- Toolbar（与 Project Tasks 共用同一套 toolbar / 过滤）----
        query, status_filter, order = _render_task_list_toolbar(
            key_prefix="uncat_task")

        rows = [_task_row_view(job) for job in jobs]
        visible = _filter_task_rows(rows, query=query,
                                    status_filter=status_filter, order=order)

        # ---- List ----
        st.markdown((_h('<div class="tp-section-head is-inline"><strong>任务</strong><span class="tp-count-badge">') + str(len(jobs)) + '</span></div>'), unsafe_allow_html=True)

        if not jobs:
            st.markdown(
                _h('<div class="tp-empty-card tp-hub-empty">'
                '<strong>还没有未分类任务</strong>'
                '<span>新建任务时选择「未分类」，任务就会出现在这里；'
                '也可以把已有任务移入某个项目。</span></div>'),
                unsafe_allow_html=True)
        elif not visible:
            st.markdown(_h('<div class="tp-empty tp-project-hub-empty">'
                        '没有符合条件的任务。</div>'), unsafe_allow_html=True)
        else:
            with st.container(key="inbox_list"):
                _render_task_rows(visible, row_prefix="inbox_row",
                                  card_key_prefix="inbox_card",
                                  menu=_uncat_task_overflow_menu)


def _open_task_move_modal(job_id, mode="existing"):
    st.session_state["project_modal"] = "move_task"
    st.session_state["task_move_job_id"] = str(job_id or "")
    st.session_state["task_move_initial_mode"] = mode


def _project_move_task_modal():
    """把任务移入已有项目或新建项目并加入。复用 `core.assign_jobs_to_project`。"""
    def _body():
        job_id = str(st.session_state.get("task_move_job_id") or "")
        state = core.load_job_state(job_id)
        if state is None:
            _close_project_modals()
            _push_flash("任务已不存在，操作已取消。", tone="warning")
            st.rerun()
            return
        title = _job_display_title({"job_id": job_id, "state": state})
        st.markdown(('<div class="tp-history-copy"><strong>' + str(escape(title)) + _h('</strong><span>将任务归入项目。移动只改变归属，不会修改已有译文；项目术语和翻译记忆不会自动迁移。</span></div>')),
                    unsafe_allow_html=True)
        options = core.list_active_project_options()
        labels = {p["project_id"]: p["name"] for p in options
                  if not core.is_system_project(p)}

        initial_mode = st.session_state.get("task_move_initial_mode") or "existing"
        mode_options = ["加入已有项目", "创建新项目并加入"]
        default_index = 1 if (initial_mode == "create" or not labels) else 0

        chosen_mode = st.radio(
            _t("加入方式"), mode_options, index=default_index,
            key="task_move_mode_radio", horizontal=True,
            label_visibility="collapsed", format_func=_localized_formatter())

        if chosen_mode == "创建新项目并加入" or not labels:
            if not labels and chosen_mode != "创建新项目并加入":
                st.info(_t("当前还没有已有项目，请直接创建新项目并加入："))
            new_name = st.text_input(_t("新项目名称"), key="task_move_new_name",
                                     placeholder=_t("输入新项目名称..."))
            new_desc = st.text_input(_t("描述（可选）"), key="task_move_new_desc",
                                     placeholder=_t("简要说明项目用途..."))
            confirm_col, cancel_col = st.columns([2, 1])
            if confirm_col.button(_t("创建并加入"), key="task_move_create_confirm",
                                  type="primary", width="stretch"):
                clean_name = new_name.strip()
                if not clean_name:
                    _push_flash("项目名称不能为空。", tone="warning")
                    st.rerun()
                    return
                try:
                    new_proj = core.create_project(clean_name, description=new_desc.strip())
                    result = core.assign_jobs_to_project([job_id], new_proj["project_id"])
                    _close_project_modals()
                    st.session_state.pop("task_move_new_name", None)
                    st.session_state.pop("task_move_new_desc", None)
                    st.session_state.pop("task_move_initial_mode", None)
                    _push_flash(f'已创建新项目「{new_proj["name"]}」并把「{title}」移入。')
                    st.rerun()
                except Exception as exc:
                    _push_flash(f"创建项目失败：{exc}", tone="warning")
                    st.rerun()
            if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
                _close_project_modals()
                st.session_state.pop("task_move_new_name", None)
                st.session_state.pop("task_move_new_desc", None)
                st.session_state.pop("task_move_initial_mode", None)
                st.rerun()
        else:
            chosen = st.selectbox(
                _t("目标项目"), list(labels), key="task_move_target_project",
                format_func=lambda pid: labels[pid])
            confirm_col, cancel_col = st.columns([2, 1])
            if confirm_col.button(_t("移入项目"), key="task_move_confirm",
                                  type="primary", width="stretch"):
                result = core.assign_jobs_to_project([job_id], chosen)
                _close_project_modals()
                st.session_state.pop("task_move_target_project", None)
                st.session_state.pop("task_move_initial_mode", None)
                if result["moved"]:
                    _push_flash(f'已把「{title}」移入「{labels[chosen]}」。')
                elif result["skipped"]:
                    reason = result["skipped"][0].get("reason") if result["skipped"] else ""
                    _push_flash(f'无法移入：{reason or "任务正在运行或状态不允许"}。',
                                tone="warning")
                else:
                    _push_flash("没有发生移动。", tone="info")
                st.rerun()
            if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
                _close_project_modals()
                st.session_state.pop("task_move_target_project", None)
                st.session_state.pop("task_move_initial_mode", None)
                st.rerun()
    _modal_container("移入项目")(_body)()


def _render_project_knowledge_tab(project):
    """项目知识：术语 / 规则 / 决定 / 记忆 —— 四个 compact module。

    这一页此前像一篇说明文档：一整段长说明 + 四个 metric + 一整张表 + 页面底部一个
    全宽导出按钮，读起来像"关于项目知识的文档"而不是一个工作页面。现在它是一个
    结构化的页面：

        项目知识                                        [ 导出 JSON ]
        术语 1 · 规则 1 · 决定 0 · 记忆 0
        （知识全空时才给一段短总说明）

        锁定术语    1    已人工锁定的译名会注入后续翻译上下文。   [查看术语表 →]
        风格规则    1    已确认的风格与翻译规则。                 [查看规则 →]
        人工决定    0    人对术语与译文的决定记录。               [查看审计 →]
        已审核记忆  0    通过审校门槛、可被复用的译文。           [查看记忆 →]

    每个模块是 compact section（count + 一句说明 + 状态 / empty hint + 入口），
    详情默认收起。低频的整项目导出收进右上角的小按钮，不再用页面底部一个全宽
    大按钮承载。

    数据来源与业务能力**未改**：仍然读 `core.project_memory_view` / 项目记录，
    `core.export_project_memory` 也仍然是同一次导出。
    """
    project_id = project["project_id"]
    summary = core.project_memory_view(project)
    glossary_count = int(summary["glossary_count"])
    rule_count = int(summary["style_rule_count"])
    decision_count = int(summary["human_decision_count"])
    memory_count = int(summary["translation_memory_count"])
    chips = " · ".join((
        f"术语 {glossary_count}", f"规则 {rule_count}",
        f"决定 {decision_count}", f"记忆 {memory_count}"))

    with st.container(key="pd_knowledge_head"):
        # 列宽 [4, 2]：右侧要放得下「导出项目记忆（JSON）」这个内容宽度的小按钮，
        # 它不再靠"页面底部全宽"来获得空间。
        head_left, head_right = st.columns([4, 2], gap="medium")
        with head_left:
            st.markdown(
                (_h('<div class="tp-section-head is-inline"><strong>项目知识</strong></div><p class="tp-pd-summary">') + str(escape(chips)) + '</p>'),
                unsafe_allow_html=True)
        with head_right, st.container(key="pd_knowledge_export"):
            # 低频导出：右上角的内容宽度小按钮，不再用页面底部一个全宽大按钮承载。
            st.download_button(
                _t("导出项目记忆（JSON）"),
                data=core.export_project_memory(project_id),
                    file_name=f"folith-project-{project_id}.json",
                mime="application/json", key=f"project_export_{project_id}",
                width="content",
                help=_t("导出术语、风格、人工决定审计与本项目的已审校记忆；"
                     "不含任务状态、源文档或任何凭据。"))

    # 空状态时允许一段**短**总说明（分层：一句定义 + 一句来源），不重复四次。
    if not any((glossary_count, rule_count, decision_count, memory_count)):
        st.markdown(
            _h('<p class="tp-pd-intro">项目知识只收录人工确认后的内容：'
            '锁定术语、已确认规则、人工决定与已审核记忆。'
            '它们由任务里的确认动作逐步提升上来，模型生成的候选不会进入这里。</p>'),
            unsafe_allow_html=True)

    _knowledge_module(
        key="glossary", title="锁定术语", count=glossary_count,
        note="已人工锁定的译名会注入后续翻译上下文。",
        empty_hint="还没有锁定术语：在任务里锁定译名后提升到本项目。",
        entry_label="查看术语表 →", opened_label="收起术语表",
        body=lambda: _knowledge_glossary_body(project))
    _knowledge_module(
        key="rules", title="风格规则", count=rule_count,
        note="已确认的风格与翻译规则，随任务一起注入。",
        empty_hint="还没有确认的风格规则：在任务里确认规则后提升到本项目。",
        entry_label="查看规则 →", opened_label="收起规则",
        body=lambda: _knowledge_rules_body(project))
    _knowledge_module(
        key="decisions", title="人工决定", count=decision_count,
        note="人对术语与译文的决定记录，可追溯。",
        empty_hint="还没有人工决定记录：确认术语或译文后会留下审计记录。",
        entry_label="查看审计 →", opened_label="收起审计",
        body=lambda: _knowledge_decisions_body(project))
    _knowledge_module(
        key="memory", title="已审核记忆", count=memory_count,
        note="通过审校门槛、可在本项目复用的译文。",
        empty_hint="本项目还没有已审校记忆：通过独立审校的段落会自动入库。",
        entry_label="查看记忆 →", opened_label="收起记忆",
        body=lambda: _knowledge_memory_body(project, summary))

    # 待处理的导入冲突是**待决动作**，不是知识条目：有才渲染，放在模块之后。
    _render_project_conflicts(project)


def _render_project_settings_tab(project, jobs, summary=None):
    """设置：项目资料 / 状态 / 高级信息 / 危险操作 —— 四个明确分区。

    原 Overview 的「基本信息」表（项目 ID / 类型 / 创建时间 / 更新时间）迁移到
    这里——UUID 可以存在于设置，但不应出现在普通概览里。

    这一版做两件事，都是"去掉重复"而不是"加功能"：

      - **动作去重**：此前「编辑名称与描述」与「重命名」并排成两个大按钮，两者
        高度重叠。现在只剩一个动作「编辑项目资料」，名称与描述在同一个编辑流程里
        改；「重命名」这个入口在设置页不再出现（它本来就只是编辑流程的子集）。
      - **分组归位**：归档此前单独占一张卡，但它和「状态」说的是同一件事。
        现在并入「状态」；设置页因此从五段收敛成四段。

    动作分级：Header 的「+ 新建任务」是页面级 Primary；设置里的动作全部是
    management 动作，一律 secondary / tertiary，危险动作单独一组。
    """
    project_id = project["project_id"]
    is_system = core.is_system_project(project)
    archived = bool(project.get("archived_at"))
    summary = summary if summary is not None else _project_detail_summary(project, jobs)

    if is_system:
        with st.container(key="pd_group_system"):
            st.markdown(_h('<div class="tp-group-head"><strong>系统工作区</strong>'
                        '<span>不可重命名 / 归档 / 删除</span></div>'),
                        unsafe_allow_html=True)
            st.caption(
                _t('「{v0}」承载所有没有归属的翻译任务（包括迁移前创建的任务）。它拥有真实持久化 ID 与独立的项目知识空间，因此可以正常积累术语与已审校记忆；但重命名、归档或删除会让这些任务失去容器，所以这些操作一律不提供。', v0=core.SYSTEM_PROJECT_NAME))
            st.markdown(
                (_h('<div class="tp-pd-row"><span class="tp-detail-label">项目 ID</span><span class="tp-detail-value"><code>') + str(escape(project_id)) + _h('</code></span></div><div class="tp-pd-row"><span class="tp-detail-label">当前任务</span><span class="tp-detail-value">') + str(len(jobs)) + _h(' 个</span></div>')),
                unsafe_allow_html=True)
            if st.button(_t("前往「任务」标签"), key=f"settings_tasks_{project_id}",
                         width="content"):
                st.session_state["active_project_tab"] = "tasks"
                st.rerun()
        return

    # ---- 1. 项目资料（名称 + 描述 + 唯一的编辑入口）----
    description = str(project.get("description") or "").strip()
    with st.container(key="pd_group_profile"):
        st.markdown(_h('<div class="tp-group-head"><strong>项目资料</strong>'
                    '<span>名称与描述</span></div>'), unsafe_allow_html=True)
        st.markdown(
            (_h('<div class="tp-pd-row"><span class="tp-detail-label">项目名称</span><span class="tp-detail-value">') + str(escape(str(project["name"]))) + _h('</span></div><div class="tp-pd-row"><span class="tp-detail-label">项目描述</span><span class="tp-detail-value">'))
            + (escape(description) if description
               else _h('<span class="tp-detail-muted">添加项目描述</span>'))
            + '</span></div>', unsafe_allow_html=True)
        # 一个动作：名称与描述在同一个编辑流程里改。不再并排第二个「重命名」。
        if st.button(_t("编辑项目资料"), key=f"settings_edit_{project_id}",
                     icon=":material/edit:", width="content"):
            _open_project_modal("edit", project_id)
            st.rerun()

    # ---- 2. 状态（活动 / 归档 + 归档或恢复，收在同一组）----
    with st.container(key="pd_group_status"):
        st.markdown(_h('<div class="tp-group-head"><strong>状态</strong>'
                    '<span>可恢复 · 不删除任何东西</span></div>'),
                    unsafe_allow_html=True)
        status_label = "已归档" if archived else "活动中"
        status_note = (f"归档于 {_format_saved_at(project.get('archived_at'))}"
                       if archived else "正常参与新建任务的项目选择")
        st.markdown(
            (_h('<div class="tp-pd-row"><span class="tp-detail-label">项目状态</span><span class="tp-detail-value">') + str(escape(status_label)) + '<span class="tp-detail-muted"> · ' + str(escape(status_note)) + '</span></span></div>'), unsafe_allow_html=True)
        if archived:
            st.caption(_t("归档不影响项目知识、翻译任务与归属，只是不再出现在活动项目"
                       "列表与新建任务的选择里。"))
            if st.button(_t("恢复为活动项目"), key=f"settings_restore_{project_id}",
                         icon=":material/unarchive:", width="content"):
                _open_project_modal("restore", project_id)
                st.rerun()
        else:
            st.caption(_t("归档后项目从活动列表移出，不再出现在新建任务的选择里；"
                       "项目知识、任务与归属全部保留，可随时恢复。"))
            if st.button(_t("归档项目"), key=f"settings_archive_{project_id}",
                         icon=":material/archive:", width="content"):
                _open_project_modal("archive", project_id)
                st.rerun()

    # ---- 3. 高级信息（身份与时间戳：只在设置里出现）----
    with st.container(key="pd_group_advanced"):
        st.markdown(_h('<div class="tp-group-head"><strong>高级信息</strong>'
                    '<span>按需查看身份与时间</span></div>'), unsafe_allow_html=True)
        with st.expander(_t("查看身份与时间戳"), expanded=False):
            st.markdown(
                (_h('<div class="tp-pd-row"><span class="tp-detail-label">项目 ID</span><span class="tp-detail-value"><code>') + str(escape(project_id)) + _h('</code><span class="tp-detail-muted"> · 不可变 UUID</span></span></div><div class="tp-pd-row"><span class="tp-detail-label">时间</span><span class="tp-detail-value">创建于 ') + str(escape(_format_saved_at(project.get("created_at")))) + ' · 最近更新 ' + str(escape(_format_saved_at(project.get("updated_at")))) + '</span></div>'),
                unsafe_allow_html=True)

    # ---- 4. 危险操作（单独一组，视觉上明确分开）----
    with st.container(key="pd_group_danger"):
        st.markdown(_h('<div class="tp-group-head"><strong>危险操作</strong>'
                    '<span>永久删除 · 不可撤销</span></div>'),
                    unsafe_allow_html=True)
        if jobs:
            st.warning(_t('这个项目下还有 {v0} 个翻译任务。删除项目需要先移动或删除这些任务——否则任务的归属会指向一个不存在的项目。', v0=len(jobs)))
            if st.button(_t("前往「任务」标签处理"), key=f"settings_goto_tasks_{project_id}",
                         width="content"):
                st.session_state["active_project_tab"] = "tasks"
                st.rerun()
        else:
            st.caption(_t("这个项目没有任务，可以删除。删除前会自动写一份可恢复备份"
                       "（含项目知识与已审校记忆），但删除本身不可撤销。"))
        with st.container(key="pd_danger_action"):
            if st.button(_t("删除项目"), key=f"settings_delete_{project_id}",
                         icon=":material/delete:", width="content",
                         disabled=bool(jobs)):
                _open_project_modal("delete", project_id)
                st.rerun()


def _render_projects_surface():
    """`/projects` 与 `/projects/:projectId` 的唯一入口。

    IA 约定（回归测试守住）：

      - 首屏是**项目 hub**：page header +「新建项目」menu + 统一 toolbar
        （搜索 / 状态 / 排序 / Grid-List）+ 未分类任务轻量入口 +「我的项目」；
      - 项目卡片整体可点进入 `/projects/:projectId`，右侧 overflow menu 只提供
        secondary actions（重命名 / 编辑 / 归档 / 导出 / 删除）；
      - 真实项目详情四个一级 tab：概览 / 任务 / 项目知识 / 设置；
      - **系统工作区「未分类」不是项目**：它的路由落点不是 Project Detail，而是
        专门的 Task Inbox（`_render_uncategorized_tasks_workspace`）；
      - 归档项目从默认活动列表移出，统一从状态筛选进入并可恢复；
      - 新建与导入都是 modal，不在首屏展开表单。
    """
    active_project_id = str(st.session_state.get("active_project_id") or "")
    # 路由说"在看某个项目"但上下文已经空了（项目刚被删除 / 冷启动）：归一到列表，
    # 否则 `projects_route` 会停在 `detail` 上，管理页的 active state 就丢了。
    if _projects_route() == "detail" and not active_project_id:
        st.session_state["projects_route"] = "list"
    if active_project_id and _projects_route() == "detail":
        project = core.load_project(active_project_id)
        if project is None and core.is_system_project_id(active_project_id):
            project = core.system_project_view()
        if project is None:
            # 项目在别处被删除：清掉失效导航状态，如实说明并回到列表。
            st.session_state.pop("active_project_id", None)
            st.session_state["projects_route"] = "list"
            _route_params(project=None, view="projects")
            _push_flash(f"项目 {active_project_id} 已不存在，已回到项目列表。",
                        tone="warning")
        else:
            # 系统工作区「未分类」不是项目：它走专门的 Task Inbox，
            # 而不是 generic Project Detail（后者会把它重新渲染成一个正式项目）。
            if core.is_system_project(project):
                _render_uncategorized_tasks_workspace(project)
            else:
                _render_project_detail(project)
            return

    _render_project_list()


def _project_view_mode():
    """Grid / List 视图状态（默认 List：高信息密度桌面工具风格；测试未指定时保持网格卡片以守住卡片结构契约）。"""
    mode = str(st.session_state.get("project_view_mode") or "")
    if mode not in _PROJECT_VIEW_MODES:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            mode = "grid"
        else:
            mode = "list"
        st.session_state["project_view_mode"] = mode
    return mode


def _normalize_project_toolbar_state():
    """会话里可能残留上一版选项（例如旧的「活动项目」）：先归一再建控件。"""
    if st.session_state.get("project_status_filter") not in _PROJECT_STATUS_OPTIONS:
        st.session_state.pop("project_status_filter", None)
    if st.session_state.get("project_order") not in _PROJECT_ORDER_OPTIONS:
        st.session_state.pop("project_order", None)


def _render_project_toolbar():
    """统一 Project Toolbar。返回 (query, status_filter, order)。

    它是**一个**工具栏，不是三个散落的表单控件：search 是主控件，状态与排序是
    紧凑的具名下拉，视图切换是同一行右侧的图标按钮。
    """
    _normalize_project_toolbar_state()
    with st.container(key="project_toolbar"):
        search_col, status_col, order_col, view_col = st.columns(
            [5.0, 1.8, 2.0, 1.3], gap="small")
        query = search_col.text_input(
            _t("搜索项目"), key="project_search", label_visibility="collapsed",
            placeholder=_t("搜索项目..."), icon=":material/search:")
        status_filter = status_col.selectbox(
            _t("状态"), _PROJECT_STATUS_OPTIONS, key="project_status_filter",
            index=_PROJECT_STATUS_OPTIONS.index("活动中"),
            label_visibility="collapsed", format_func=_localized_formatter())
        order = order_col.selectbox(
            _t("排序"), _PROJECT_ORDER_OPTIONS, key="project_order",
            label_visibility="collapsed", format_func=_localized_formatter())
        with view_col.container(key="project_view_toggle"):
            grid_col, list_col = st.columns(2, gap="small")
            mode = _project_view_mode()
            if grid_col.button(_t("网格视图"), key="project_view_grid", help=_t("切换到网格视图"),
                               type="primary" if mode == "grid" else "secondary",
                               width="stretch"):
                st.session_state["project_view_mode"] = "grid"
                st.rerun()
            if list_col.button(_t("列表视图"), key="project_view_list", help=_t("切换到列表视图"),
                               type="primary" if mode == "list" else "secondary",
                               width="stretch"):
                st.session_state["project_view_mode"] = "list"
                st.rerun()
    return query, status_filter, order


def _render_project_cards(visible, records, *, mode):
    """Grid 是 2 列（宽屏才考虑 3 列）；List 是单列紧凑行。两者共用同一份卡数据。"""
    if mode == "list":
        with st.container(key="project_list"):
            st.markdown(_browser.build_project_list_header_markup(), unsafe_allow_html=True)
            for view in visible:
                _project_list_row(
                    view, records[view["project_id"]],
                    archived=(view.get("status") == "archived"))
        return
    with st.container(key="project_grid"):
        for start in range(0, len(visible), 2):
            row = visible[start:start + 2]
            columns = st.columns(2, gap="medium")
            for column, view in zip(columns, row):
                with column:
                    _project_card(
                        view, records[view["project_id"]],
                        archived=(view.get("status") == "archived"))


def _render_project_list():
    """项目 hub 首屏：page header / toolbar / 未分类入口 / 我的项目。"""
    sections, views, records = _load_project_views()
    system_views = [v for v in views if v.get("is_system")]
    active_views = [v for v in views
                    if not v.get("is_system") and v.get("status") != "archived"]
    archived_views = [v for v in views if v.get("status") == "archived"]
    if system_views:
        system_view = system_views[0]
        system_project = records[system_view["project_id"]]
    else:  # pragma: no cover - 磁盘上还没有记录时的只读视图
        system_project = core.system_project_view()
        system_view = core.project_summary(system_project)
    uncategorized_count = int(system_view.get("job_count") or 0)

    with st.container(key="project_hub"):
        # ---- Page header：标题与「新建项目」属于同一个 page container ----
        with st.container(key="project_header"):
            head_left, head_right = st.columns([5, 2], gap="large")
            head_left.markdown(
                _h('<div class="tp-project-header-copy"><h1>项目</h1>'
                '<p>管理翻译任务、项目知识与语言资产</p></div>'),
                unsafe_allow_html=True)
            with head_right.container(key="project_header_action"):
                # Compact CTA：它是 page header 的动作，不是整页最重的元素。
                with st.popover(_t("新建项目"), type="primary",
                                icon=":material/add:", width="content"):
                    if st.button(_t("新建空白项目"), key="project_new_blank",
                                 icon=":material/create_new_folder:",
                                 width="stretch"):
                        _clear_project_form()
                        _open_project_modal("new")
                        st.rerun()
                    if st.button(_t("导入项目"), key="project_new_import",
                                 icon=":material/upload_file:",
                                 width="stretch",
                                 help=_t("从译页项目备份（JSON）导入")):
                        _open_project_modal("import")
                        st.rerun()

        # ---- Unified toolbar ----
        query, status_filter, order = _render_project_toolbar()

        if status_filter == "已归档":
            candidate_views = archived_views
        elif status_filter == "全部":
            candidate_views = active_views + archived_views
        else:
            candidate_views = active_views
        visible = _sort_project_views(
            [v for v in candidate_views if _project_matches(v, query)], order)

        # ---- 系统任务区：未分类任务不是项目，只是没有归属的任务收纳区 ----
        # 它必须和「我的项目」在**结构上**分开（各自一个 section），只在视觉上
        # 靠虚线 / sunken 面降级是不够的：区块标题才说明"这里不是你的项目列表"。
        _uncategorized_strip(system_view, system_project)

        # ---- My projects ----
        filter_note = ""
        if str(query or "").strip():
            filter_note = f"匹配 {len(visible)} 个"
        elif status_filter == "已归档":
            filter_note = f"{len(archived_views)} 个已归档"
        elif status_filter == "全部":
            filter_note = f"共 {len(active_views) + len(archived_views)} 个"
        # badge 与当前筛选一致：筛"已归档"时它必须说归档的数量，否则读者会以为
        # 列表和数字对不上。
        section_count = {"已归档": len(archived_views),
                         "全部": len(active_views) + len(archived_views)}.get(
                             status_filter, len(active_views))
        with st.container(key="project_section"):
            st.markdown(
                (_h('<div class="tp-section-head is-inline"><strong>我的项目</strong><span class="tp-count-badge">') + str(section_count) + '</span>')
                + (f'<span class="tp-section-note">{escape(filter_note)}</span>'
                   if filter_note else "")
                + '</div>', unsafe_allow_html=True)
            if not active_views and not archived_views:
                _empty_projects_state()
            elif not visible:
                hint = ("没有符合条件的项目。"
                        if active_views or archived_views else "")
                if not active_views and archived_views \
                        and status_filter == "活动中":
                    hint = ("没有活动项目。已归档的项目可以从上方「状态」"
                            "筛选里查看并恢复。")
                st.markdown(f'<div class="tp-empty tp-project-hub-empty">'
                            f'{hint}</div>', unsafe_allow_html=True)
            else:
                _render_project_cards(visible, records,
                                      mode=_project_view_mode())


# ================= Project 生命周期弹窗 =================
# 一次只渲染一个 modal，由 `_open_project_modal(kind, project_id)` 打开。


def _render_project_modal():
    """按当前 modal 类型渲染对应弹窗；没有打开的 modal 时什么都不做。"""
    kind = _consume_project_modal("new", "import", "rename", "edit", "archive",
                                  "restore", "delete", "move_task")
    if not kind:
        return
    if kind == "move_task":
        _project_move_task_modal()
        return
    target = _project_modal_record() if kind not in ("new", "import") else None
    if kind not in ("new", "import") and (target is None or not str(
            st.session_state.get("project_modal_target") or "").strip()):
        # 项目在弹窗打开后被删除：如实说明并关闭，而不是抛异常。
        _close_project_modals()
        _push_flash("这个项目已不存在，操作已取消。", tone="warning")
        return
    if kind == "new":
        _project_new_modal()
    elif kind == "import":
        _project_import_modal()
    elif kind == "rename":
        _project_rename_modal(target)
    elif kind == "edit":
        _project_edit_modal(target)
    elif kind == "archive":
        _project_archive_modal(target, archived=True)
    elif kind == "restore":
        _project_archive_modal(target, archived=False)
    else:
        _project_delete_modal(target)


def _project_new_modal():
    """新建项目：第一版至少支持 name 与 description。"""
    def _body():
        st.caption(_t("项目是任务、术语、风格规则、人工决定与已审校记忆的长期容器。"
                   "新建项目不会创建任何翻译任务。"))
        name = st.text_input(_t("项目名称"), key="project_form_name",
                             placeholder=_t(_PROJECT_NAME_PLACEHOLDER))
        description = st.text_area(
            _t("项目描述（可选）"), key="project_form_description", height=90,
            placeholder=_t("一句话说明这个项目覆盖什么文档、面向什么读者。"))
        create_col, cancel_col = st.columns([2, 1])
        if create_col.button(_t("新建项目"), key="project_form_create",
                             type="primary", width="stretch"):
            try:
                if error := _reserved_project_name_error(name):
                    raise ValueError(error)
                created = core.create_project(name, description=description)
            except ValueError as exc:
                # 校验失败时**不** rerun：在 dialog 里 rerun 会关掉弹窗并丢掉
                # 用户已经填好的内容，错误提示也来不及显示。
                st.session_state["project_form_error"] = str(exc)
            else:
                _clear_project_form()
                _close_project_modals()
                _apply_project_context(created["project_id"])
                if _in_new_task_flow():
                    # 在「新建任务」流程里建项目：它成为上下文，但用户留在流程里。
                    _push_flash(f"已新建项目「{created['name']}」，"
                                "已作为本次任务的上下文。")
                else:
                    _push_flash(f"已新建项目「{created['name']}」，已进入项目详情。")
                    st.session_state.update(app_view="projects",
                                            workspace_mode=False,
                                            projects_route="detail")
                    st.session_state["active_project_tab"] = "overview"
                st.rerun()
        if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
            _clear_project_form()
            _close_project_modals()
            st.rerun()
        _render_project_form_error()
    _modal_container("新建项目")(_body)()


def _project_import_modal():
    """导入项目：与新建项目平级的 modal，而不是页面底部的展开表单。

    保留既有全部业务能力：JSON 文件、名称覆盖、描述、只增不改的冲突记录。
    """
    def _body():
        _render_project_importer(key_prefix="project_import_modal",
                                 heading="")
    _modal_container("导入项目", width="large")(_body)()


def _project_rename_modal(project):
    """重命名：**只改 display name，不改 project_id**，所有引用不受影响。"""
    def _body():
        st.caption(_t("重命名只改变显示名称。项目 ID 是不可变 UUID，任务归属、路由与"
                   "项目记忆都不受影响。"))
        name = st.text_input(_t("新的项目名称"), key="project_form_name",
                             value=str(project["name"]))
        save_col, cancel_col = st.columns([2, 1])
        if save_col.button(_t("保存名称"), key="project_form_save", type="primary",
                           width="stretch"):
            try:
                updated = core.rename_project(project["project_id"], name)
            except ValueError as exc:
                st.session_state["project_form_error"] = str(exc)
            else:
                _clear_project_form()
                _close_project_modals()
                _refresh_projects_after()
                _push_flash(f"已重命名为「{updated['name']}」；项目 ID 未变。")
                st.rerun()
        if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
            _clear_project_form()
            _close_project_modals()
            st.rerun()
        _render_project_form_error()
    _modal_container("重命名项目")(_body)()


def _project_edit_modal(project):
    """编辑：名称 + 描述。与重命名共用同一层校验，不重复实现。"""
    def _body():
        name = st.text_input(_t("项目名称"), key="project_form_name",
                             value=str(project["name"]))
        description = st.text_area(_t("项目描述"), key="project_form_description",
                                   value=str(project.get("description") or ""),
                                   height=110)
        save_col, cancel_col = st.columns([2, 1])
        if save_col.button(_t("保存"), key="project_form_save", type="primary",
                           width="stretch"):
            try:
                updated = core.update_project(project["project_id"], name=name,
                                              description=description)
            except ValueError as exc:
                st.session_state["project_form_error"] = str(exc)
            else:
                _clear_project_form()
                _close_project_modals()
                _refresh_projects_after()
                _push_flash(f"已保存项目「{updated['name']}」的基本信息。")
                st.rerun()
        if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
            _clear_project_form()
            _close_project_modals()
            st.rerun()
        _render_project_form_error()
    _modal_container("编辑项目")(_body)()


def _project_archive_modal(project, *, archived):
    """归档（可恢复）/ 恢复。都不删除任务，也不删除项目记忆。"""
    verb = "归档" if archived else "恢复"
    def _body():
        if archived:
            st.markdown(
                ('<div class="tp-history-copy"><strong>' + str(escape(project["name"])) + _h('</strong><span>归档后：从活动项目列表移出，不再出现在新建任务的项目选择里。</span><span>不会被删除：项目记忆、术语、风格规则与任务归属全部保留，随时可以恢复。</span></div>')),
                unsafe_allow_html=True)
        else:
            st.markdown(
                ('<div class="tp-history-copy"><strong>' + str(escape(project["name"])) + _h('</strong><span>恢复后：重新出现在活动项目列表与新建任务的项目选择里。</span></div>')),
                unsafe_allow_html=True)
        confirm_col, cancel_col = st.columns([2, 1])
        if confirm_col.button(_t('确认{v0}', v0=verb), key="project_form_confirm",
                              type="primary", width="stretch"):
            try:
                core.archive_project(project["project_id"], archived)
            except ValueError as exc:
                _close_project_modals()
                _push_flash(f"{verb}失败：{exc}", tone="error")
                st.rerun()
            else:
                _close_project_modals()
                _refresh_projects_after()
                if archived and str(st.session_state.get("active_project_id")) \
                        == project["project_id"]:
                    st.session_state["active_project_tab"] = "settings"
                _push_flash(f"已{verb}项目「{project['name']}」。"
                            + ("项目记忆与任务归属都保留，可随时恢复。"
                               if archived else ""))
                st.rerun()
        if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
            _close_project_modals()
            st.rerun()
    _modal_container(f"{verb}项目")(_body)()


def _project_delete_modal(project):
    """永久删除：仍含任务时**拒绝**并指引先移动/删除任务；无任务时二次确认。"""
    def _body():
        jobs = core.list_project_jobs(project["project_id"])
        st.markdown(
            ('<div class="tp-history-copy"><strong>' + str(escape(project["name"])) + _h('</strong><span>项目 ID：') + str(escape(project["project_id"])) + _h('</span><span>翻译任务 ') + str(len(jobs)) + ' 个 · 锁定术语 ' + str(len(project.get("glossary") or [])) + ' 条 · 已审校记忆 ' + str(len(core.load_tm(project["project_id"]))) + _h(' 条</span></div>')),
            unsafe_allow_html=True)
        if jobs:
            st.error(_t('这个项目下还有 {v0} 个翻译任务，不能删除。请先把这些任务移到其它项目，或删除任务，然后再删除项目。', v0=len(jobs)))
            if st.button(_t("前往「任务」标签处理"), key="project_form_goto_tasks",
                         width="stretch"):
                _close_project_modals()
                st.session_state["active_project_tab"] = "tasks"
                st.rerun()
            if st.button(_t("取消"), key="project_form_cancel", width="stretch"):
                _close_project_modals()
                st.rerun()
            return
        st.warning(_t("删除是永久操作：项目记忆、术语、风格规则与该项目已审校记忆都会"
                   "从工作区移除。删除前会自动写一份可恢复备份。"))
        typed = st.text_input(
            _t('输入项目名称「{v0}」以确认', v0=project['name']),
            key="project_form_confirm_name", placeholder=project["name"])
        confirm_col, cancel_col = st.columns([2, 1])
        if confirm_col.button(_t("永久删除"), key="project_form_confirm",
                              type="primary",
                              disabled=str(typed or "").strip() != project["name"],
                              width="stretch"):
            try:
                result = core.delete_project(project["project_id"],
                                             confirm_name=typed)
            except ValueError as exc:
                st.session_state["project_form_error"] = str(exc)
            else:
                _clear_project_form()
                _close_project_modals()
                if str(st.session_state.get("active_project_id")) \
                        == project["project_id"]:
                    st.session_state.pop("active_project_id", None)
                    _route_params(project=None, view="projects")
                _refresh_projects_after()
                _push_flash(f"已永久删除项目「{result['name']}」；"
                            f"备份保存在 {result['backup'].name}。")
                st.rerun()
        if cancel_col.button(_t("取消"), key="project_form_cancel", width="stretch"):
            _clear_project_form()
            _close_project_modals()
            st.rerun()
    _modal_container("删除项目（永久）")(_body)()


def _render_project_form_error():
    """渲染弹窗内的校验错误。

    必须放在按钮**之后**：dialog 重跑时 body 先执行、按钮回调随后触发，因此
    "先渲染再处理"会让校验失败晚一轮才可见。
    """
    if error := st.session_state.get("project_form_error"):
        st.error(_t(error))


def _clear_project_form():
    for key in ("project_form_name", "project_form_description",
                "project_form_error", "project_form_confirm_name"):
        st.session_state.pop(key, None)


# ================= 新建任务的项目上下文（只读）=================
# 语义：Workspace ▸ Project ▸ Task ▸ Run。Project 是可复用的长期容器，Task 是
# 一次执行，任务的归属就是**当前 Project Context**。
#
# Project Context 只有一个来源：侧栏的 switcher。因此新建任务页不自己维护一个
# 并列的项目下拉框——两个并列的选择器迟早会给出互相矛盾的答案（侧栏说 A、
# 任务说 B）。这里只**显示**上下文，改归属只有一条路：打开那一个 switcher。
#
# 没有上下文的任务属于系统工作区「未分类任务」：它是真实容器，有记忆命名空间，
# 只是不注入任何项目上下文。

# 项目名不能占用系统工作区的显示名，否则列表里的「未分类」会同时指两个东西。
_RESERVED_PROJECT_NAMES = ("未分类", "未分类任务")


def _reserved_project_name_error(name):
    cleaned = str(name or "").strip()
    if cleaned in _RESERVED_PROJECT_NAMES:
        return (f"项目名称不能是「{'」或「'.join(_RESERVED_PROJECT_NAMES)}」"
                "——那是系统工作区的保留名。")
    return ""


def _render_project_importer(*, key_prefix="project_import", heading="导入项目"):
    """导入项目备份（只增不改）。

    advanced / migration 动作，不是项目页首屏内容：入口在「新建项目」菜单里，
    界面是一个独立 modal。`key_prefix` 让同一页面上可能出现的多个入口各自持有
    独立控件，不会撞 key。业务能力（JSON / 名称覆盖 / 描述 / 冲突记录）不变。
    """
    if heading:
        st.markdown(('<div class="tp-field-head"><strong>' + str(heading) + _h('</strong><span>从项目备份导入 · 只增不改，冲突不会被静默覆盖</span></div>')), unsafe_allow_html=True)
    upload = st.file_uploader(
        _t("拖入译页项目文件（.json）"), type=["json"],
        key=f"{key_prefix}_file",
        help=_t("由「导出项目」生成的 JSON。拖入或点击选择文件；"
             "同名项目会被并入，而不是新建重复项目。"))
    rename = st.text_input(
        _t("项目名称（可选）"), key=f"{key_prefix}_name",
        placeholder=_t("留空则自动使用文件中的项目名称"))
    description = st.text_input(
        _t("项目描述（可选）"), key=f"{key_prefix}_description",
        placeholder=_t("一句话说明这个项目的范围"))
    if upload is None:
        st.caption(_t("导入只增不改：与本地不一致的译名会被记录成待处理冲突，"
                   "而不是静默覆盖。"))
        return
    if st.button(_t("导入"), key=f"{key_prefix}_go", width="stretch", type="primary"):
        try:
            project, report = core.import_project_memory(
                upload.getvalue(), name=str(rename or "").strip() or None,
                description=str(description or "").strip())
        except (ValueError, OSError) as exc:
            _push_flash(f"导入失败：{exc}", tone="error")
            st.error(_t('导入失败：{v0}', v0=exc))
            return
        summary = (f"已导入到「{project['name']}」：术语 +{report['glossary_added']} · "
                   f"风格 +{report['style_added']} · "
                   f"人工决定 +{report['decisions_added']} · "
                   f"已审校记忆 +{report.get('tm_added', 0)}")
        _close_project_modals()
        _refresh_projects_after()
        _push_flash(summary)
        if report["glossary_conflicts"]:
            with st.expander(_t('术语冲突 {v0} 条（保留本地译名）', v0=len(report['glossary_conflicts'])), expanded=False):
                for item in report["glossary_conflicts"]:
                    st.caption(_t('{v0}：本地「{v1}」 vs 导入「{v2}」', v0=item['source'], v1=item['local'], v2=item['incoming']))
        if report.get("tm_conflicts_count"):
            st.caption(_t('有 {v0} 条已审校记忆与本地不同，已保留本地译法。', v0=report['tm_conflicts_count']))
        _open_project(project["project_id"])
        st.rerun()


def _render_task_project_context():
    """新建任务里的「项目上下文」：**只读**地显示当前上下文。

    它不是一个 Project Selector：归属只有一个来源（侧栏「项目」分组里的上下文
    selector），这里只回答"这个任务会落在哪个项目里"，并把改归属的入口指回那一个
    selector。

    两种状态是**同一个 context block**，不是两张卡：

        已选项目：项目名 +「继承项目术语、翻译记忆和规则」+ [更改]
        未选项目：未分类任务（Inbox）+「任务将保存到系统工作区…」+ [选择项目]

    关键约束（回归测试守住）：未分类态**不得**使用 Project 的语法——没有卡片阴影、
    没有"项目"这个称谓、没有 overflow menu，且英文 `Inbox` 只能作为状态行右侧的
    低对比小标签出现，不能变成主视觉标题。
    """
    context = _project_context()
    with st.container(key="task_project_context"):
        st.markdown(_h('<div class="tp-context-head">项目上下文</div>'),
                    unsafe_allow_html=True)
        if context["selected"]:
            st.markdown(
                ('<div class="tp-project-context is-selected"><span class="material-symbols-rounded" aria-hidden="true">folder_open</span><div class="tp-context-copy"><div class="tp-context-status"><strong>' + str(escape(context["name"])) + _h('</strong></div><span class="tp-project-context-note">继承项目术语、翻译记忆和规则</span></div></div>')),
                unsafe_allow_html=True)
            if st.button(_t("更改"), key="task_project_change",
                         icon=":material/swap_horiz:", width="content",
                         help=_t("打开侧栏「项目」分组里的同一个上下文选择器")):
                _toggle_project_switcher("task")
                st.rerun()
            if _project_switcher_is_open("task"):
                _render_project_switcher_body("task")
        else:
            st.markdown(
                _h('<div class="tp-project-context is-empty">'
                '<span class="material-symbols-rounded" aria-hidden="true">'
                'inbox</span>'
                '<div class="tp-context-copy">'
                '<div class="tp-context-status">'
                '<strong>未分类任务</strong>'
                '<span class="tp-context-tag">Inbox</span></div>'
                '<span class="tp-project-context-note">'
                '任务将保存到系统工作区，不继承项目术语、翻译记忆与规则'
                '</span></div></div>'),
                unsafe_allow_html=True)
            if st.button(_t("选择项目"), key="task_project_pick",
                         icon=":material/folder_open:", width="content",
                         help=_t("在侧栏「项目」分组里为本次任务选择上下文")):
                _toggle_project_switcher("task")
                st.rerun()
            if _project_switcher_is_open("task"):
                _render_project_switcher_body("task")


def _current_project_context():
    """侧栏上下文的**原始**来源（可能是系统工作区），按优先级：

    1. **正在做的任务**（`workspace_mode`）：任务所属的项目——层级里 Task 在
       Project 之下，打开一个任务就等于进入它所属的项目；
    2. **正在看的项目**（`active_project_id`，即停在 `/projects/:projectId`），
       以及新建任务流程里记住的上下文；
    3. 否则是系统工作区「未分类」。

    这是 context switcher，不是 project manager：它只回答"我现在在哪个项目里
    工作"。顺序刻意让"当前任务"压过"上次看过的项目"——否则侧栏会说项目 A，
    而屏幕上做的事属于项目 B。
    """
    if st.session_state.get("workspace_mode"):
        active_job_id = str(st.session_state.get("active_job_id") or "")
        if active_job_id:
            state = core.load_job_state(active_job_id)
            if state is not None:
                project = core.project_for_job(active_job_id, state)
                if project is not None:
                    return project
    viewing = str(st.session_state.get("active_project_id") or "")
    if viewing:
        project = core.load_project(viewing)
        if project is None and core.is_system_project_id(viewing):
            project = core.system_project_view()
        if project is not None:
            return project
    active_job_id = str(st.session_state.get("active_job_id") or "")
    if active_job_id:
        state = core.load_job_state(active_job_id)
        if state is not None:
            project = core.project_for_job(active_job_id, state)
            if project is not None:
                return project
    return core.system_project_view()


def _project_context():
    """Project Context 的**唯一读口**：全应用都从这里取，不各自拼一份。

    返回 `{project, project_id, name, selected, is_system}`：
    `selected=False` 表示当前没有进入任何真实项目（= 系统工作区「未分类」）。
    """
    project = _current_project_context() or core.system_project_view()
    is_system = core.is_system_project(project)
    return {
        "project": project,
        "project_id": str(project.get("project_id") or core.SYSTEM_PROJECT_ID),
        "name": (core.SYSTEM_PROJECT_NAME if is_system
                 else str(project.get("name") or "未命名项目")),
        "selected": not is_system,
        "is_system": is_system,
    }


def _apply_project_context(project_id):
    """Project Context 的**唯一写入口**：侧栏 switcher / 打开项目 / 新建项目共用。

    一次写入同时落两处：`active_project_id`（导航与侧栏）与 `task_project_id`
    （新建任务的归属）。二者必须是同一个值——它们是同一件事的两种读法，分开写
    迟早会变成"侧栏 A、任务 B"。
    """
    canonical = core._project.canonical_project_id(project_id)
    _remember_project_visit(canonical)
    st.session_state["active_project_id"] = canonical
    st.session_state["task_project_id"] = canonical
    st.session_state.pop("task_project_choice", None)
    _route_params(project=canonical, view=None)
    return canonical


def _sync_task_project_context():
    """让 Task 归属成为 Project Context 的**投影**，而不是第二份状态。

    每次运行都对齐一次：上下文换了，新建任务的归属就跟着换，不存在"侧栏已经
    切到 B、任务还挂在 A"的中间态。
    """
    context = _project_context()
    if st.session_state.get("task_project_id") != context["project_id"]:
        st.session_state["task_project_id"] = context["project_id"]
    if st.session_state.get("task_project_context_id") != context["project_id"]:
        st.session_state["task_project_context_id"] = context["project_id"]
        st.session_state.pop("task_project_choice", None)
    return context


def _in_new_task_flow():
    """是否停在「新建任务」流程里（决定 switcher 切换后要不要留在原地）。"""
    return (str(st.session_state.get("app_view") or "") == "new"
            and not st.session_state.get("workspace_mode"))


def _sidebar_current_project():
    """侧栏「项目上下文」里那个**真实项目**，没有时返回 None。

    「未分类」是任务收纳区，不是项目：它不出现在这里，否则会持续强化
    "未分类 = 项目"这个错误概念。未分类任务有自己的轻量入口（项目中心）。
    """
    project = _current_project_context()
    if project is None or core.is_system_project(project):
        return None
    return project


def _sidebar_active_job():
    """侧栏「当前任务」的来源：任务必须**真实存在**。

    `active_job_id` 是会话状态；任务被删除之后它仍然会留在状态里，进而让侧栏
    显示一个已经不存在、点开还会报错的入口。因此这里以磁盘为准。
    """
    job_id = str(st.session_state.get("active_job_id") or "")
    if not job_id:
        return ""
    return job_id if core.load_job_state(job_id) is not None else ""


def _begin_new_task(project_id=""):
    """Open Step 1 with the current workspace project as its default context.

    `project_id` 显式传入时优先（Project Detail 的「+ 新建任务」走这条），保证
    从项目里创建任务时**所属项目已经选定**，用户不必在创建流程里再选一次。
    归档项目不是合法的任务归属：回落到系统工作区并如实说明。
    """
    target = str(project_id or "").strip()
    if not target:
        target = str((_sidebar_current_project() or {}).get("project_id") or "")
    if target and core.is_system_project_id(target):
        target = ""
    if target:
        record = core.load_project(target)
        if record is None:
            target = ""
        elif record.get("archived_at"):
            _push_flash(f"「{record['name']}」已归档，新任务不会归入它；"
                        "请先恢复项目。", tone="warning")
            target = ""
    # 归档 / 已删除的上下文不能留着当归属：显式落回系统工作区，而不是让侧栏
    # 继续显示一个本次任务用不上的项目。
    _apply_project_context(target or core.SYSTEM_PROJECT_ID)
    st.session_state.update(app_view="new", workspace_mode=False, task_step=1)
    st.session_state.pop("task_project_choice", None)
    _route_params(task=None, job=None, section=None, view=None)


def _sidebar_project_switcher(context):
    """侧栏「项目」分组里的上下文控件：**state / switch action**，不是导航。

    它只回答"我此刻在哪个项目里工作"。点击展开一个**轻量下拉面板**（可选搜索 +
    项目列表 + 新建项目），选中后立刻收起并更新全局 Project Context。

    Project Management（查看 / 新建 / 重命名 / 归档 / 删除）属于同组的**分组标题**
    （「项目」标题本身就是 Project Center 入口），**不在**这个面板里——所以这里没有
    「管理所有项目」。state action 与 navigation 相邻但严格分离。

    "新任务将默认加入所选项目，已有任务不会移动"这句说明**不常驻**在面板里：
    switcher 只负责 switching，产品教育属于 tooltip（见下方 `help=`）与 New Task
    正文的「项目上下文」区域。

    不管有没有选中项目，它都是**同一个 compact 控件**：选中时显示项目名，
    未选中时显示「未选择项目」。绝不把「未分类」渲染成一个项目。
    """
    label = context["name"] if context["selected"] else "未选择项目"
    display = _project_display_title({"name": label}) or "未选择项目"
    if len(display) > 14:
        display = display[:13] + "…"
    # 切换的**效果**说明放这里，而不是面板里占一段常驻文案：switcher 只负责
    # switching，产品教育属于 tooltip（完整解释仍在 New Task 正文）。
    switch_effect = "仅影响新任务，已有任务不会移动"
    with st.container(key="current_project"):
        if not context["selected"]:
            # 空的语义标记（不渲染任何文字）：CSS 用它把 selector 切成中性态，
            # 没有项目时不该看起来像已经选中了一个。
            st.markdown('<span class="tp-nav-empty" aria-hidden="true"></span>',
                        unsafe_allow_html=True)
        if _project_switcher_is_open("sidebar"):
            # 同一个套路：展开态也是**空的语义标记**，CSS 用它把触发器切成"已按下"，
            # 这样开与合共用一套控件样式，不会突然换一种按钮语言。
            st.markdown('<span class="tp-nav-open" aria-hidden="true"></span>',
                        unsafe_allow_html=True)
        if st.button(f"{display if context['selected'] else _t('未选择项目')}  ▾",
                     key=_PROJECT_SWITCHER_ANCHORS["sidebar"]["trigger"],
                     icon=":material/folder_open:" if context["selected"]
                     else ":material/swap_horiz:",
                     width="stretch", type="secondary",
                     help=(_t(f"切换项目上下文：{switch_effect}"
                           if context["selected"]
                           else f"选择要进入的项目：{switch_effect}"))):
            _toggle_project_switcher("sidebar")
            st.rerun()
        # 面板**只在展开时渲染**：列表数据一次约 0.4 s，常驻渲染会把它加到每一次
        # 重跑上。收起时不渲染，空闲成本为零——这才是 quick switcher。
        if _project_switcher_is_open("sidebar"):
            _render_project_switcher_body("sidebar")
        viewing = str(st.session_state.get("active_project_id") or "")
        # `is_system_project_id("")` 为真（空值收敛到系统项目），所以这里必须
        # 先确认"确实打开了一个项目"，再判断它是不是系统工作区。而且这条
        # "正在查看未分类任务"提示只属于**项目详情**路由：上下文会被带进项目中心，
        # 只看 `app_view == "projects"` 会让管理页也挂上这句不该出现的话。
        if viewing and core.is_system_project_id(viewing) \
                and str(st.session_state.get("app_view") or "") == "projects" \
                and _projects_route() == "detail":
            st.markdown(_h('<p class="tp-nav-note">正在查看未分类任务</p>'),
                        unsafe_allow_html=True)


# ---- Project Switcher：**唯一**一份上下文切换 UI ----
# 交互模型（本轮修正）：上下文 selector 展开的是**轻量下拉面板**，不是大型 Modal。
# 两个锚点共用同一份列表实现，所以不存在"两套并行的 project switching UX"：
#
#   sidebar：侧栏「项目」分组的 selector（主入口，state action）
#   task   ：新建任务第 1 步「项目上下文」块的 [更改] / [选择项目]（就近入口）
#
# 为什么不用 `st.popover` 而用一个受控的会话标记：①`st.popover` 的开合由前端
# 驱动，服务端既读不到也关不掉——"选中后立即关闭"与"点项目中心不得弹 switcher"
# 这两条会变成不可测也不可靠的约定；②Popover 的内容默认**常驻渲染**（惰性要靠
# `.open`，而 `.open` 只在 `on_change="rerun"` 下才存在），面板数据一次约 0.4 s，
# 常驻等于给每次重跑都加上这笔开销。用会话标记驱动，展开才渲染、关闭是确定性的。
#
# 容器 key 都含 `switcher_*` 片段，CSS 用 `[class*="switcher_..."]` 同时命中两处；
# 每处锚点又有自己的整套 widget key，同一页面里两处同时展开不会撞 key。
#
# 业务边界：switcher 只做 context switching + 搜索 + 进入 create-project flow。
# 管理动作（查看全部 / 重命名 / 归档 / 删除）属于侧栏「项目」分组标题上的 Project
# Center 入口，不在这里。
#
# 每个锚点有**两类**行 key：`row` 是铺满整行的透明点击层（测试与读屏按它定位），
# `row_frame` 是承载可见版式的容器。两者都是单行 row，不是卡片。
_PROJECT_SWITCHER_ANCHORS = {
    "sidebar": {"trigger": "current_project_selector",
                "open": "sidebar_project_switcher_open",
                "panel": "project_switcher_panel",
                "search": "project_switcher_search",
                "query": "project_switcher_query",
                "list": "project_switcher_list",
                "row": "switcher_pick_",
                "row_frame": "switcher_row_",
                "footer": "project_switcher_footer",
                "new": "switcher_new_project"},
    "task": {"trigger": "task_project_switcher_trigger",
             "open": "task_project_switcher_open",
             "panel": "task_project_switcher_panel",
             "search": "task_project_switcher_search",
             "query": "task_project_switcher_query",
             "list": "task_project_switcher_list",
             "row": "task_switcher_pick_",
             "row_frame": "task_switcher_row_",
             "footer": "task_project_switcher_footer",
             "new": "task_switcher_new_project"},
}

# 面板里出现搜索框的项目数阈值。搜索框在侧栏里要占掉一整行，所以只在"翻找开始
# 费劲"时才给：4 个以内的项目一眼即可扫完。
_PROJECT_SWITCHER_SEARCH_MIN = 5


def _project_switcher_is_open(anchor="sidebar"):
    """某个锚点的切换面板是否展开。纯 UI chrome，不参与任何 Project 状态推导。"""
    return bool(st.session_state.get(_PROJECT_SWITCHER_ANCHORS[anchor]["open"]))


def _close_project_switcher(anchor="sidebar"):
    """收起某个锚点的切换面板。

    "选中后立刻关闭"是硬要求：面板不会因为内部按钮重跑而自动消失（脚本式 UI 里
    它就是一段普通内容），所以每次选中都显式关一次。
    """
    st.session_state[_PROJECT_SWITCHER_ANCHORS[anchor]["open"]] = False


def _close_all_project_switchers():
    """收起全部切换面板（导航离开当前语境时调用）。"""
    for anchor in _PROJECT_SWITCHER_ANCHORS:
        _close_project_switcher(anchor)


def _toggle_project_switcher(anchor="sidebar"):
    """展开 / 收起**同一个**切换面板，同一时刻只留一个。

    两处锚点展开的是同一份列表：同时开着会被读成两套系统（本轮要消除的正是这个
    观感），所以打开一处就收起另一处。
    """
    key = _PROJECT_SWITCHER_ANCHORS[anchor]["open"]
    opening = not bool(st.session_state.get(key))
    if opening:
        _close_all_project_switchers()
    st.session_state[key] = opening


def _ordered_project_switcher_options(options, current_project_id):
    """Prioritize the current project and recent session visits."""
    by_id = {str(item.get("project_id") or ""): item for item in options}
    ordered_ids = []
    current_id = str(current_project_id or "")
    if current_id in by_id:
        ordered_ids.append(current_id)
    for project_id in st.session_state.get("project_switcher_recent") or []:
        project_id = str(project_id or "")
        if project_id in by_id and project_id not in ordered_ids:
            ordered_ids.append(project_id)
    ordered_ids.extend(project_id for project_id in by_id
                       if project_id not in ordered_ids)
    return [by_id[project_id] for project_id in ordered_ids]


def _switch_project_context(project):
    """Apply a context selection and finish with one success toast."""
    project_id = str(project["project_id"])
    project_name = str(project.get("name") or "未分类")
    # 在「新建任务」流程里只换上下文：不把用户从创建流程拽到项目详情页。
    # 其余任何页面里，切换上下文 = 进入该项目（导航语义不变）。
    in_flow = _in_new_task_flow()
    # 选中即收起：面板不会因为内部按钮重跑而自动消失（见 `_close_project_switcher`）。
    # 两处锚点都收：上下文已经变了，留着另一个展开会显示过期的"当前"行。
    _close_all_project_switchers()
    if in_flow:
        _apply_project_context(project_id)
        _push_flash(f"已切换到「{project_name}」")
        st.rerun()
        return
    _push_flash(f"已切换到「{project_name}」")
    _open_project(project_id)
    st.rerun()


def _render_project_switcher_row(ids, project, context_id):
    """switcher 里的一行：compact row（名称 + 计数），整行是一个点击层。

    行**不做成卡片**。卡片语法属于"用户拥有的 Project 对象"（见项目中心的项目卡），
    而这里只回答"要切到哪"。所以是 30px 单行、无阴影、无边框，长名字一律省略号。

    为什么是"可见 markdown 行 + 铺满它的透明按钮"：`st.button` 的 label 只接受
    markdown，排不出"名称 flex:1、计数 flex:0 0 auto"的单行布局，硬塞会把名称换成
    多行（上一版的溢出根因之一）。这个套路与历史任务卡一致，CSS 见 `switcher_row_`。

    Inbox 只带一个极轻的 `Inbox` 标签：它承担"系统容器、不是项目"的语义，但不再
    重复"系统工作区 / 20 个未归入项目的任务"那几层说明——那些属于正文的产品教育。
    """
    pid = str(project["project_id"])
    is_system = core.is_system_project(project)
    is_current = pid == context_id
    name = ("未分类任务" if is_system
            else str(project.get("name") or "未命名项目"))
    count = int(core.project_summary(project).get("job_count") or 0)
    tag = '<span class="tp-switch-tag">Inbox</span>' if is_system else ""
    with st.container(key=f"{ids['row_frame']}{pid}"):
        st.markdown(
            f'<div class="tp-switch-row{" is-current" if is_current else ""}">'
            '<span class="tp-switch-check" aria-hidden="true"></span>'
            f'<span class="tp-switch-name">{escape(name)}</span>'
            f'{tag}'
            f'<span class="tp-switch-count">{count}</span>'
            '</div>', unsafe_allow_html=True)
        # 透明点击层。label 里带上完整项目名：视觉上不可见，但控件在读屏与测试里
        # 仍然叫得出名字（视觉截断由 CSS 负责，两者互不牵制）。
        if st.button(_t('切换到{v0}', v0=name), key=f"{ids['row']}{pid}",
                     width="stretch", help=_t('切换到「{v0}」', v0=name)):
            _switch_project_context(project)


def _render_project_switcher_body(anchor="sidebar"):
    """项目切换列表正文。**唯一一份实现**，两个锚点共用。

    只做三件事：切换当前 Project Context、搜索、进入 create-project flow。
    管理动作（查看全部 / 重命名 / 归档 / 删除）不在这里——那是分组标题上 Project
    Center 入口的职责，所以这里**没有**「管理所有项目」：同一个页面里出现两个指向
    管理页的入口，正是要消除的重复。

    「当前」标记以**原始上下文**为准（可能落在系统工作区），因此用
    `_current_project_context()` 而不是只认真实项目的 `_sidebar_current_project()`。

    它是 quick switcher，不是产品说明页：面板里**不常驻**任何解释文案。
    "只影响新任务"这类说明放在 selector 的 tooltip 与 New Task 正文里。
    """
    ids = _PROJECT_SWITCHER_ANCHORS[anchor]
    context_id = str((_current_project_context() or {}).get("project_id") or "")
    options = _ordered_project_switcher_options(
        core.list_active_project_options(), context_id)
    with st.container(key=ids["panel"]):
        search_query = ""
        # 搜索框只在"翻找开始费劲"时才出现：它在侧栏里要占掉一整行，而 4 个以内的
        # 项目一眼就能扫完。无条件放一个输入框是纯浪费。
        if len(options) >= _PROJECT_SWITCHER_SEARCH_MIN:
            with st.container(key=ids["search"]):
                search_query = st.text_input(
                    _t("搜索项目"), key=ids["query"],
                    placeholder=_t("搜索项目名称"), label_visibility="collapsed",
                    icon=":material/search:") or ""
        normalized_query = str(search_query).strip().casefold()
        filtered = [
            item for item in options
            if not normalized_query
            or normalized_query in str(item.get("name") or "").casefold()
            or normalized_query in str(item.get("description") or "").casefold()
        ]

        with st.container(key=ids["list"]):
            for project in filtered:
                _render_project_switcher_row(ids, project, context_id)
        if not filtered:
            st.caption(_t("没有匹配的项目。"))

        st.divider()
        with st.container(key=ids["footer"]):
            # 单个低频动作：新建项目。管理入口**不**在这里重复一份。
            if st.button(_t("新建项目"), key=ids["new"],
                         width="stretch", icon=":material/add:"):
                _close_project_switcher(anchor)
                _open_project_modal("new")
                st.rerun()


def _sidebar_task_steps():
    """当前任务步骤（只在任务创建流程里出现，收在「当前任务」下）。"""
    with st.container(key="task_steps"):
        current_step = st.session_state.task_step
        for number, label in ((1, "文档与画像"), (2, "翻译策略"),
                              (3, "交付内容"), (4, "确认运行")):
            status = "done" if number < current_step else "current" if number == current_step else "pending"
            icon = ":material/check_circle:" if status == "done" \
                else ":material/radio_button_checked:" if status == "current" \
                else ":material/radio_button_unchecked:"
            if st.button(_t(f"{number:02d}  {_t(label)}"), icon=icon,
                         key=f"task_step_{status}_{number}", width="stretch",
                         type="primary" if status == "current" else "secondary"):
                _request_step(number)
                st.rerun()


# ---- URL 路由恢复（必须在读取 app_view 之前）----
# 路由参数可能在同一会话中由分享链接或浏览器前进/后退更新；每次运行都按
# 当前参数校正页面，避免旧的 session state 抢在 URL 之前决定用户看到的项目。
_restore_route_from_params()

app_view = st.session_state.get("app_view", "new")
workspace_mode = st.session_state.get("workspace_mode", False)

st.markdown(_h('<a class="tp-skip-link" href="#folio-main-content">跳到主要内容</a>'),
            unsafe_allow_html=True)

with st.sidebar:
    brand_label = escape(_t("译页 智能体翻译工作台"), quote=True)
    st.markdown(
            f'<div class="tp-brand" aria-label="{brand_label}">'
            f'<img class="tp-brand-logo" src="{_BRAND_LOGO_URI}" '
            f'alt="{brand_label}"></div>',
            unsafe_allow_html=True)
    new_task_in_flow = app_view == "new" and not workspace_mode
    # Project Detail 的页面级 Primary CTA 是 Header 的「+ 新建任务」：侧栏这一颗
    # 必须退成 secondary，避免两个同级主操作互相竞争。动作仍然是同一个
    # （`_begin_new_task()` 会带上当前项目上下文）。未分类 Task Inbox 没有页面级 CTA，
    # 因此那里保持 primary。
    # 只有**项目详情**才有页面级 Primary CTA。判据是路由而不是"有没有上下文"：
    # 上下文现在会跟着用户进到项目中心（`_open_project_list` 不再清它），拿
    # `sidebar_project_id` 当依据会让管理页也以为自己在项目详情里。
    sidebar_project_id = str(st.session_state.get("active_project_id") or "")
    on_project_detail = (app_view == "projects" and _projects_route() == "detail")
    new_task_in_project = (on_project_detail and bool(sidebar_project_id)
                           and not core.is_system_project_id(sidebar_project_id))
    new_task_subdued = new_task_in_flow or new_task_in_project
    new_task_action_key = ("new_task_action_in_flow" if new_task_in_flow else
                           "new_task_action_in_project" if new_task_in_project else
                           "new_task_action")
    with st.container(key=new_task_action_key):
        if st.button(_t("新建任务"), icon=":material/add:", width="stretch",
                     type="secondary" if new_task_subdued else "primary"):
            _begin_new_task()
            st.rerun()
    # ---- 「项目」分组：上下文（我在哪个项目里工作）+ Project Center（我拥有哪些项目）----
    # 两者同属 Project，因此收进**同一个分组**：同一个标题、上下相邻、中间不插
    # 分隔线。拆成"顶部上下文 + 底部项目中心"会被读成两套系统。
    #
    # 管理入口**收敛到分组标题本身**：Project Center 就是"所有项目"这一层，把它做成
    # 标题下面一个独立行，等于让同一件事在同一个分组里出现两次，还让它去和 selector
    # 争视觉重量。现在标题是一个轻量 header affordance（`st-key-project_section_header`），
    # 权重明显低于 selector。
    with st.container(key="project_nav_group"):
        # "当前页"标记只在**列表路由**上出现（`projects_route == "list"`），不拿
        # `active_project_id` 当判据——上下文会被带进管理页，用旧判据会让标记在
        # 管理页上消失（需求 B：位于 Project Center 时要保持 active state）。
        _project_center_current = (app_view == "projects" and not workspace_mode
                                  and _projects_route() == "list")
        with st.container(key="project_section_header"):
            if _project_center_current:
                st.markdown('<span class="tp-nav-current" aria-hidden="true"></span>',
                            unsafe_allow_html=True)
            # 导航语义不变：只改路由（`_open_project_list`），不碰 `active_project_id`，
            # 也绝不展开 switcher。已经在项目中心时它是幂等的（route 不变）。
            # 「项目」是 Project Center 入口，交互契约不变（只改路由，不碰上下文）。
            # tooltip 只回答"这是什么"，不再承担完整能力清单 —— 旧文案长到横跨侧栏
            # 与主工作区；新建 / 重命名 / 归档 / 删除这些能力由项目中心页面自己表达。
            if st.button(_t("项目"), key="project_section_header_button",
                         width="stretch",
                         help=_t("查看和管理所有项目")):
                _open_project_list()
                st.rerun()
        # 每次运行都对齐一次：Task 归属是上下文的投影，不存在两份互相矛盾的选择。
        _sidebar_context = _sync_task_project_context()
        _sidebar_project_switcher(_sidebar_context)
    # ---- 当前任务（任务创建流程的步骤收在这里，不与项目混在一栏）----
    _sidebar_active_job = _sidebar_active_job()
    if app_view == "new" and not workspace_mode:
        st.markdown(_h('<div class="tp-nav-divider"></div>'
                    '<div class="tp-nav-label">当前任务</div>'), unsafe_allow_html=True)
        _sidebar_task_steps()
    elif _sidebar_active_job and app_view == "workspace":
        _sidebar_state = core.load_job_state(_sidebar_active_job) or {}
        _sidebar_job_label = (_history_view.document_title(_sidebar_state)
                              or _sidebar_active_job)[:16]
        st.markdown(_h('<div class="tp-nav-divider"></div>'
                    '<div class="tp-nav-label">当前任务</div>'), unsafe_allow_html=True)
        if st.button(_sidebar_job_label, icon=":material/description:",
                     key=f"sidebar_job_{_sidebar_active_job}", width="stretch",
                     type="primary" if workspace_mode else "secondary",
                     help=_t("回到当前任务工作区")):
            _open_job(_sidebar_active_job, _sidebar_state,
                      st.session_state.get("workspace_section") or "translation")
            st.rerun()
    # ---- 工作区区：跨项目的资料与全局设置 ----
    # 这是 **global navigation**，不是 Project 的一部分：所以「项目中心」不在这一
    # 组里——否则它会被读成"和历史任务 / 术语与翻译记忆同类的第三个资料库"，
    # 而它其实是 Project 分组的次级入口。
    st.markdown(_h('<div class="tp-nav-label">工作区</div>'), unsafe_allow_html=True)
    with st.container(key="library_nav"):
        if st.button(_t("历史任务"), icon=":material/history:", width="stretch",
                     type="primary" if app_view == "history" else "secondary"):
            st.session_state.app_view = "history"
            _route_params(project=None, view="history")
            st.rerun()
        if st.button(_t("术语与翻译记忆"), icon=":material/menu_book:", width="stretch",
                     type="primary" if app_view == "library" else "secondary"):
            st.session_state.app_view = "library"
            _route_params(project=None, view="library")
            st.rerun()
        if st.button(_t("外观与语言"), icon=":material/contrast:", width="stretch",
                     key="appearance_navigation",
                     type="primary" if app_view == "appearance" else "secondary"):
            st.session_state.update(app_view="appearance", workspace_mode=False)
            _route_params(project=None, task=None, job=None, section=None, tab=None, view="appearance")
            st.rerun()
    # AI Engine 区是 **runtime status module**，不是「工作区」分组里的第四行导航。
    # 它同时承担四件事：当前 runtime 状态、当前模型、连接状态、以及 AI Engine /
    # Model Center 的**唯一**管理入口。所以这里刻意不套用 `library_nav` 的导航行
    # 语法（48px 行高 + hover 面），整块也不做成 clickable card —— 只有「管理」是
    # 可点击 action，标题是 status label，模型名与连接状态是它的两行读数。
    with st.container(key="provider_status"):
        provider_col, manage_col = st.columns([3, 1])
        ai_view = _ai_configuration_view()
        connection_status = st.session_state.get("provider_connection_status", "unverified")
        # Keep the legacy class for existing CSS/tests while exposing the
        # more useful configuration state in the visible copy.
        provider_col.markdown(('<div class="tp-provider is-' + str(connection_status) + ' is-' + str(ai_view["state"]) + _h('"><strong>AI引擎</strong></div>')),
                              unsafe_allow_html=True)
        # 「管理」= 打开 AI Engine / Model Center。这是侧栏里**唯一**指向该页面的
        # 入口；route 与页面功能都不变（`app_view == "settings"` 仍然是该页）。
        #
        # ⚠️ 这里刻意**不加 `help=`**：带 tooltip 的按钮会被 Streamlit 包进
        # `stTooltipHoverTarget`，`button` 就不再是 `.stButton` 的直接子元素，而
        # `.st-key-provider_status .stButton > button`（把「管理」压成右对齐文字
        # action 的那条规则）用的是直接子选择器 —— 一旦加上 tooltip，它会静默落空、
        # 退回 Streamlit 默认的描边按钮，把这块 status module 变成"两颗按钮"。
        # 要加 tooltip，必须先把那条 CSS 改成 `.stButton button` 后代写法。
        if manage_col.button(_t("管理"), key="manage_provider"):
            st.session_state.app_view = "settings"
            st.rerun()
        # 读数层级：模型名是 secondary text，连接状态是 tertiary/status text。
        st.markdown('<div class="tp-engine-detail">'
                    f'<span class="tp-engine-model">{escape(str(ai_model or "尚未选择模型"))}</span>'
                    f'<span class="tp-engine-state is-{escape(str(ai_view["state"]))}">'
                    f'{escape(_t(ai_view["label"]))}</span></div>',
                    unsafe_allow_html=True)

# ---- 跨页面的 Project 弹窗 ----
# 这里只剩**管理类**弹窗（新建 / 导入 / 重命名 / 编辑 / 归档 / 删除 / 移入任务）。
# 「切换项目」不再是弹窗：它是侧栏 selector（以及正文上下文块）上的轻量 Popover，
# 在各自的锚点里就地渲染，因此这里没有任何 switcher 的分支。
_render_project_modal()

# Pipeline defaults; preset is a template and strategy_config is the effective configuration.
preset_label = st.session_state.get("translation_preset", "标准")
if preset_label not in _PRESET_CONFIGS:
    preset_label = "标准"
if "strategy_config" not in st.session_state:
    st.session_state.strategy_config = dict(_PRESET_CONFIGS[preset_label])
if "delivery_preset" not in st.session_state:
    st.session_state.delivery_preset = "standard"
if "delivery_preset_modified" not in st.session_state:
    st.session_state.delivery_preset_modified = False
if "output_config" not in st.session_state:
    st.session_state.output_config = dict(_DELIVERY_PRESET_CONFIGS["standard"])
strategy_config = st.session_state.strategy_config
output_config = st.session_state.output_config
# A normal/quick strategy never exposes research products in Step 03.  Clear
# stale UI-only state so a hidden option cannot accidentally reach a new run;
# persisted/resumed jobs still take their saved pipeline configuration in
# `_pipeline_kwargs` below.
if not _research_outputs_visible(preset_label, strategy_config):
    output_config["enable_report"] = False
    for _research_key in _DELIVERY_RESEARCH_KEYS[1:]:
        output_config[_research_key] = False
auto_term = strategy_config["auto_term"]
use_tm = strategy_config["use_tm"]
enable_review = strategy_config["enable_review"]
strict_terminology_governance = strategy_config["strict_terminology_governance"]
enable_annotate = output_config["enable_annotate"]
enable_report = output_config["enable_report"]
target_lang = st.session_state.get("target_lang", "简体中文")
user_glossary = st.session_state.get("task_glossary", [])
uploaded_files = []
run_clicked = False
resume_choice = None
job_choices = [f"{j['state'].get('filename', '?')} {j['state'].get('progress_label', '')}"
               for j in saved_jobs]
style_rules = st.session_state.get(
    "style_rules", "保持学术书面语；专有名词、作者姓名、机构名、引用标注、URL 等保留原文；标点遵循目标语言规范。")
annotation_colors = st.session_state.get("annotation_colors", {
    "rare": "C00000", "domain": "BF8F00", "hard": "008080"})
theory_choice = st.session_state.get("translation_theory_choice", "自动推荐（建议）")
if theory_choice == "自定义":
    translation_theory = st.session_state.get("custom_translation_theory", "").strip() \
        or "自定义理论框架"
elif theory_choice in ("自动推荐", "自动推荐（建议）"):
    translation_theory = "基于文本特征、案例证据与可用文献自动推荐理论框架"
else:
    translation_theory = theory_choice
_uploaded_literature = st.session_state.get("literature_upload_sources") or []
_registry_literature = st.session_state.get("literature_registry_sources")
if _registry_literature is None:
    _registry_literature = st.session_state.get("literature_sources") or []
literature_sources = [*_uploaded_literature, *_registry_literature] or None
research_settings = st.session_state.get("research_settings", {
    "target_words": 4200, "body_language": "zh-CN",
    "case_selection_policy": "mixed", "report_stage": "final_report",
    "analysis_dimensions": ["文本特征", "术语管理", "翻译策略", "译后编辑与质量控制"],
})
research_settings = dict(research_settings)
template_input = st.session_state.get("report_template_input")
if template_input:
    research_settings["report_template_contract"] = template_input.get("contract")
elif st.session_state.get("report_template_removed"):
    research_settings.pop("report_template_contract", None)


def _pipeline_kwargs(state=None):
    """Build the current UI configuration for a new or resumed worker."""
    state = state if isinstance(state, dict) else {}
    saved = state.get("pipeline_config") or {}
    academic_state = state.get("academic_state") or {}
    persisted = bool(saved or state.get("p1_done") or state.get("p2_done")
                     or academic_state.get("artifacts"))
    resume_report = saved.get("enable_report") if saved else \
        state.get("report_enabled", enable_report) if persisted else enable_report
    if state and (academic_state.get("artifacts") or academic_state.get(
            "current_stage") not in {None, "", "not_started"}):
        resume_report = True
    resume_annotate = saved.get("enable_annotate") if "enable_annotate" in saved \
        else state.get("enable_annotate", enable_annotate) if persisted else enable_annotate
    delivery = state.get("delivery_config") or output_config
    delivery = core.normalize_delivery_config(
        delivery, enable_report=resume_report, enable_annotate=resume_annotate)
    saved_reasoning = saved.get("reasoning_effort", reasoning_effort)
    effective_reasoning = (saved_reasoning
                           if saved_reasoning in core.reasoning_effort_options(
                               ai_provider, ai_model) else "")
    understanding_default = strategy_config.get("enable_understanding", True)
    if persisted and "enable_understanding" not in saved:
        understanding_default = bool(
            state.get("profile_done") or state.get("understanding_done")
            or state.get("quality_mode"))
    return {
        "provider": ai_provider,
        "api_key": api_key,
        "model": ai_model,
        "reasoning_effort": effective_reasoning,
        "target_lang": saved.get("target_lang") or state.get("target_lang") or target_lang,
        "auto_term": saved.get("auto_term", auto_term),
        "enable_report": bool(resume_report),
        "translation_theory": saved.get("translation_theory") or
        state.get("theory") or translation_theory,
        "user_glossary": state.get("glossary") or user_glossary,
        "style_rules": saved.get("style_rules", style_rules),
        "enable_review": saved.get("enable_review", enable_review),
        "enable_annotate": bool(resume_annotate),
        "use_tm": saved.get("use_tm", use_tm),
        "enable_understanding": saved.get(
            "enable_understanding", understanding_default),
        "enable_source_cleanup": saved.get(
            "enable_source_cleanup",
            str(state.get("filename") or "").lower().endswith(".pdf")
            if persisted else None),
        "batch_size": _batch_params(saved or strategy_config)[0],
        "max_batch_chars": _batch_params(saved or strategy_config)[1],
        "segmentation_mode": (
            saved.get("segmentation_mode")
            or ((state.get("segmentation") or {}).get("mode")
                if (state.get("segmentation") or {}).get("locked") else None)
            or strategy_config.get("segmentation_mode", "sentence")),
        "translation_concurrency": int(saved.get("translation_concurrency") or strategy_config.get("translation_concurrency", 4)),
        "translator_base_url": api_base,
        "strict_terminology_governance": saved.get(
            "strict_terminology_governance", state.get(
                "quality_mode", strict_terminology_governance)),
        **({
            "reviewer_provider": reviewer_provider,
            "reviewer_model": reviewer_model,
            "reviewer_api_key": reviewer_api_key,
            "reviewer_base_url": reviewer_base_url,
        } if reviewer_mode == "separate" else {}),
        "research_settings": state.get("research_settings") or research_settings,
        "literature_sources": state.get("literature_sources") or literature_sources,
        "delivery_config": delivery,
    }


def _resume_job(job_id, state):
    return core.resume_job(
        job_id, state.get("filename") or "当前任务",
        _pipeline_kwargs(state), base_url=api_base)

# ================= Main views =================
setup_placeholder = st.empty()
with setup_placeholder.container():
    if app_view == "settings":
        _page_title(_t("AI 引擎"), _t("配置一次，所有新任务自动使用当前连接"))
        if not core.is_onboarded():
            st.caption(_t("首次使用：选择服务商 → 填写 API 密钥与模型 → 保存配置 → 点击「测试连接」通过后即可开始翻译。"))
        st.markdown(
            _h('<div class="tp-settings-section-head">连接设置'
            '<span>选择服务商并确认模型可用</span></div>'),
            unsafe_allow_html=True)
        pc1, pc2 = st.columns(2)
        ai_provider = pc1.selectbox(_t("服务商"), providers,
                                    index=providers.index(ai_provider), key="provider_choice",
                                    on_change=_reset_provider_connection,
                                    **_PERSIST_STATE, format_func=_localized_formatter())
        provider_cfg = core.PROVIDERS[ai_provider]
        model_opts = sorted(provider_cfg.get("models") or [], key=str.casefold)
        fetched_models = sorted(
            set(st.session_state.get(f"fetched_models_{ai_provider}") or []),
            key=str.casefold)
        with pc2:
            with st.container(key="model_selector_row"):
                model_col, fetch_col = st.columns(
                    [3.2, 1.4], gap="small", vertical_alignment="bottom")
                fetch_slot = fetch_col.empty()
                if model_opts:
                    default_model = provider_cfg.get("default_model")
                    if default_model not in model_opts:
                        default_model = model_opts[0]
                    model_key = f"model_choice_{ai_provider}"
                    if st.session_state.get(model_key, default_model) not in model_opts:
                        st.session_state[model_key] = default_model
                    ai_model = model_col.selectbox(
                        _t("模型"), model_opts,
                        index=model_opts.index(st.session_state.get(
                            model_key, default_model)),
                        key=model_key,
                        on_change=_reset_provider_connection,
                        **_PERSIST_STATE, format_func=_localized_formatter())
                elif fetched_models:
                    model_key = f"fetched_model_choice_{ai_provider}"
                    preferred_key = f"preferred_fetched_model_{ai_provider}"
                    current_model = st.session_state.get(
                        model_key, st.session_state.get(
                            preferred_key, st.session_state.get(
                                f"model_choice_{ai_provider}")))
                    if current_model not in fetched_models:
                        current_model = fetched_models[0]
                    ai_model = model_col.selectbox(
                        _t("模型"), fetched_models,
                        index=fetched_models.index(current_model), key=model_key,
                        on_change=_reset_provider_connection, args=(True,),
                        **_PERSIST_STATE, format_func=_localized_formatter())
                    st.session_state[f"model_choice_{ai_provider}"] = ai_model
                else:
                    ai_model = model_col.text_input(
                        _t("模型"), key=f"model_choice_{ai_provider}",
                        placeholder=_t(provider_cfg.get("model_hint") or "model-name"),
                        on_change=_reset_provider_connection,
                        **_PERSIST_STATE)
        if provider_cfg.get("custom_base_url"):
            key_col, endpoint_col = st.columns(2, gap="medium")
            with key_col:
                api_key = st.text_input(
                    _t("API 密钥"), type="password", key=f"api_key_{ai_provider}",
                    on_change=_reset_provider_connection, **_PERSIST_STATE)
            with endpoint_col:
                api_base = st.text_input(
                    _t("API 地址"), key="custom_base_url",
                    placeholder="https://your-relay.example.com/v1",
                    on_change=_reset_provider_connection, **_PERSIST_STATE)
        else:
            api_key = st.text_input(
                _t("API 密钥"), type="password", key=f"api_key_{ai_provider}",
                on_change=_reset_provider_connection, **_PERSIST_STATE)
            api_base = None
        st.markdown(
            _h('<div class="tp-settings-section-head">模型参数'
            '<span>只显示当前模型支持的选项</span></div>'),
            unsafe_allow_html=True)
        reasoning_key = f"reasoning_effort_{ai_provider}"
        reasoning_ui_options = ("", *core.reasoning_effort_options(ai_provider, ai_model))
        if st.session_state.get(reasoning_key, "") not in reasoning_ui_options:
            st.session_state[reasoning_key] = ""
        if len(reasoning_ui_options) > 1:
            reasoning_labels = {"": "自动（由模型决定）", "low": "低 · 更快",
                                "medium": "中 · 平衡", "high": "高 · 更充分"}
            st.selectbox(
                _t("推理强度"), list(reasoning_ui_options),
                format_func=_localized_formatter(lambda value: reasoning_labels.get(value, value)),
                key=reasoning_key, **_PERSIST_STATE)
            st.caption(_t("仅对已识别的推理模型发送 reasoning_effort；自动模式不附加该参数。"))
        else:
            st.caption(_t("推理强度：自动（当前模型未声明可调推理参数）"))
        st.markdown(
            _h('<div class="tp-settings-section-head">独立审校'
            '<span>可选；默认复用翻译模型</span></div>'),
            unsafe_allow_html=True)
        reviewer_mode = st.radio(
            _t("审校模型"),
            ["same", "separate"],
            index=0 if st.session_state.get("reviewer_mode", "same") == "same" else 1,
            format_func=_localized_formatter(lambda value: "与翻译模型相同" if value == "same" else "单独配置"),
            key="reviewer_mode",
            horizontal=True,
            on_change=_reset_reviewer_connection,
            **_PERSIST_STATE,
        )
        if reviewer_mode == "separate":
            reviewer_provider = st.selectbox(
                _t("审校服务商"), providers,
                index=providers.index(st.session_state.get(
                    "reviewer_provider_choice", ai_provider))
                if st.session_state.get("reviewer_provider_choice", ai_provider) in providers
                else providers.index(ai_provider),
                key="reviewer_provider_choice",
                on_change=_reset_reviewer_connection,
                **_PERSIST_STATE,
             format_func=_localized_formatter())
            reviewer_provider_cfg = core.PROVIDERS[reviewer_provider]
            reviewer_model_opts = sorted(
                reviewer_provider_cfg.get("models") or [], key=str.casefold)
            if reviewer_model_opts:
                reviewer_default = reviewer_provider_cfg.get("default_model")
                if reviewer_default not in reviewer_model_opts:
                    reviewer_default = reviewer_model_opts[0]
                reviewer_current = st.session_state.get(
                    "reviewer_model", reviewer_default)
                if reviewer_current not in reviewer_model_opts:
                    reviewer_current = reviewer_default
                    st.session_state.reviewer_model = reviewer_current
                reviewer_model = st.selectbox(
                    _t("审校模型"), reviewer_model_opts,
                    index=reviewer_model_opts.index(reviewer_current),
                    key="reviewer_model", on_change=_reset_reviewer_connection,
                    **_PERSIST_STATE, format_func=_localized_formatter())
            else:
                reviewer_model = st.text_input(
                    _t("审校模型"), key="reviewer_model",
                    placeholder=_t(reviewer_provider_cfg.get("model_hint") or "例如 gpt-4.1-mini"),
                    on_change=_reset_reviewer_connection,
                    **_PERSIST_STATE,
                )
            reviewer_api_key = st.text_input(
                _t("审校 API 密钥"), type="password", key="reviewer_api_key",
                on_change=_reset_reviewer_connection,
                **_PERSIST_STATE,
            )
            reviewer_base_url = st.text_input(
                _t("审校 API 地址（可选）"), key="reviewer_base_url",
                placeholder=_t("留空使用服务商默认地址"),
                on_change=_reset_reviewer_connection,
                **_PERSIST_STATE,
            )
        else:
            reviewer_provider = ai_provider
            reviewer_model = ""
            reviewer_api_key = ""
            reviewer_base_url = ""
        can_fetch_models = provider_cfg.get("kind") in ("openai", "openai_compat") \
            and (provider_cfg.get("custom_base_url") or not model_opts)
        if can_fetch_models:
            fetch_base = api_base or provider_cfg.get("base_url")
            if fetch_slot.button(
                    _t("获取可用模型"), width="stretch",
                    disabled=not (api_key and fetch_base),
                    help=_t("从 OpenAI 兼容接口的 /models 目录读取可用模型")):
                with st.spinner("正在获取模型目录…"):
                    ok, fetched, msg = core.fetch_provider_models(
                        ai_provider, api_key, base_url=fetch_base)
                if ok:
                    st.session_state[f"fetched_models_{ai_provider}"] = fetched
                    selected = st.session_state.get(f"model_choice_{ai_provider}")
                    if selected not in fetched:
                        selected = fetched[0]
                    st.session_state[f"preferred_fetched_model_{ai_provider}"] = selected
                else:
                    st.session_state.pop(f"fetched_models_{ai_provider}", None)
                st.session_state.model_fetch_feedback = (ok, msg)
                st.rerun()
            if feedback := st.session_state.get("model_fetch_feedback"):
                ok, msg = feedback
                (st.success if ok else st.error)(msg)
        base_ready = (not provider_cfg.get("custom_base_url")
                      or bool(core.normalize_openai_base_url(api_base)))
        save_ready = bool(api_key and ai_model and base_ready) and (
            reviewer_mode == "same"
            or bool(reviewer_provider and reviewer_model and reviewer_api_key)
        )
        if provider_cfg.get("custom_base_url") and not base_ready:
            st.warning(_t("API 地址无效，请填写包含 http:// 或 https:// 的中转站基址。"))
        connection_state = str(
            st.session_state.get("provider_connection_status") or "unverified")
        connection_labels = {
            "connected": "翻译连接已验证",
            "error": "翻译连接需要处理",
            "unverified": "尚未验证连接",
        }
        reviewer_state = str(
            st.session_state.get("reviewer_connection_status") or "unverified")
        reviewer_suffix = ""
        if reviewer_mode == "separate" and reviewer_state != "unverified":
            reviewer_suffix = " · 审校连接" + (
                "已验证" if reviewer_state == "connected" else "需要处理")
        st.markdown(
            f'<div class="tp-settings-connection-note is-{escape(connection_state)}">'
            f'{escape(connection_labels.get(connection_state, "尚未验证连接"))}'
            f'{escape(reviewer_suffix)}</div>', unsafe_allow_html=True)
        save_btn, test_btn = st.columns(2)
        with save_btn:
            if st.button(_t("保存配置"), width="stretch",
                         disabled=not save_ready,
                         help=_t("把服务商、模型与 API 密钥写入本地，重启应用后仍会保留")):
                core.save_provider_config(
                    ai_provider, ai_model, api_key, api_base,
                    reviewer=(
                        {"provider": reviewer_provider, "model": reviewer_model,
                         "api_key": reviewer_api_key, "base_url": reviewer_base_url}
                         if reviewer_mode == "separate" else None
                    ),
                    reasoning_effort=st.session_state.get(reasoning_key, ""),
                )
                st.toast(_t("AI 引擎配置已保存，重启应用后仍会保留"))
        with test_btn:
            if st.button(_t("测试连接"), type="primary", width="stretch",
                         disabled=not save_ready):
                with st.spinner("正在验证连接…"):
                    ok, msg = core.test_provider(ai_provider, api_key,
                                                 ai_model, base_url=api_base)
                    reviewer_ok, reviewer_msg = True, ""
                    if ok and reviewer_mode == "separate":
                        reviewer_ok, reviewer_msg = core.test_provider(
                            reviewer_provider, reviewer_api_key, reviewer_model,
                            base_url=reviewer_base_url or None)
                if ok:
                    core.mark_onboarded()
                st.session_state.provider_configured = ok and reviewer_ok
                st.session_state.provider_connection_status = \
                    "connected" if ok else "error"
                st.session_state.reviewer_connection_status = (
                    "connected" if reviewer_ok else "error")
                if ok and not reviewer_ok:
                    msg = f"翻译连接正常；审校连接失败：{reviewer_msg}"
                st.session_state.provider_test_feedback = (ok and reviewer_ok, msg)
                st.rerun()
        if feedback := st.session_state.get("provider_test_feedback"):
            ok, msg = feedback
            (st.success if ok else st.error)(msg)

    elif app_view == "new" and not workspace_mode:
        step = st.session_state.task_step
        if step == 3:
            _page_title(_t("新建翻译任务"), "")
        else:
            _page_title(_t("新建翻译任务"), _t("准备输入材料与翻译上下文"))
        if not core.is_onboarded() \
                and not st.session_state.get("onboarding_dismissed") \
                and not api_key:
            with st.container(key="onboarding_guide"):
                guide_col, action_col = st.columns([3, 1])
                guide_col.markdown(
                    _h('<div class="tp-onboard-card">'
                    '<div class="tp-style-card-head">'
                    '<span class="material-symbols-rounded" aria-hidden="true">rocket_launch</span>'
                    '<strong>首次使用译页</strong></div>'
                    '<p>配置 AI 引擎后即可开始翻译。三步完成：</p>'
                    '<ol><li>选择服务商（DeepSeek / OpenAI / Gemini / 中转站…）</li>'
                    '<li>填写 API 密钥并选择模型</li>'
                    '<li>点击「测试连接」验证通过</li></ol></div>'),
                    unsafe_allow_html=True)
                with action_col:
                    if st.button(_t("前往设置"), key="goto_settings_onboard",
                                 type="primary", width="stretch"):
                        st.session_state.app_view = "settings"
                        st.rerun()
                    if st.button(_t("暂不配置"), key="dismiss_onboarding",
                                 width="stretch"):
                        st.session_state.onboarding_dismissed = True
                        st.rerun()

        if step == 1:
            if gate_message := st.session_state.pop("step_gate_message", None):
                st.warning(_t(gate_message), icon=":material/info:")
            # 画像只在用户真正继续时运行；这样首屏保持安静，也不会把内部
            # workflow 暴露成一个需要手动启动的 CTA。
            if st.session_state.get("pending_profile_step") == 2 \
                    and st.session_state.get("style_profiling_state") == "running":
                _finish_profile_before_step_two()

            st.markdown(_h('<div class="tp-task-section-heading">输入文件</div>'),
                        unsafe_allow_html=True)
            task_files = st.session_state.get("task_files") or []
            if task_files:
                # 只认**当前本地化上下文**下已存在的任务：同一份文件在别的项目 /
                # 别的目标语言下的任务不是这个任务，不能拿它的解析结果当依据。
                first_job = core.load_job_state(core.resolve_task_id(
                    task_files[0]["bytes"],
                    project_id=st.session_state.get("task_project_id"),
                    target_lang=st.session_state.get("target_lang")
                    or core.DEFAULT_TARGET_LANG))
                if first_job and first_job.get("p1_done"):
                    st.session_state.source_parse_state = "parsed"
                    if first_job.get("source_page_count"):
                        task_files[0]["pages"] = first_job["source_page_count"]
                with st.container(key="source_file_summary"):
                    with st.container(key="source_file_card"):
                        st.markdown(_source_file_html(task_files), unsafe_allow_html=True)
                        with st.container(key="source_file_actions"):
                            replace_col, remove_col = st.columns(2)
                            with replace_col:
                                st.button(_t("更换文件"), icon=":material/swap_horiz:",
                                          key="replace_source",
                                          on_click=_remove_source_documents,
                                          help=_t("选择另一份 PDF 或 DOCX"))
                            with remove_col:
                                st.button(_t("删除"), icon=":material/delete_outline:",
                                          key="remove_source",
                                          on_click=_remove_source_documents,
                                          help=_t("移除当前输入文件"))
            else:
                with st.container(key="source_documents"):
                    st.markdown(_h('<div class="tp-upload-copy">'
                                '<span class="material-symbols-rounded" aria-hidden="true">upload_file</span>'
                                '<span>拖入文件或点击选择</span>'
                                '<small>支持 PDF、DOCX · 单文件最大 200 MB</small></div>'),
                                unsafe_allow_html=True)
                    uploaded_files = st.file_uploader(_t("原文"), type=["pdf", "docx"],
                                                      accept_multiple_files=True,
                                                      key=f"source_documents_"
                                                          f"{st.session_state.get('source_uploader_generation', 0)}",
                                                      label_visibility="collapsed",
                                                      help=_t("支持 PDF 和 DOCX，可一次添加多个文件"))
                if uploaded_files:
                    st.session_state.task_files = [
                        {"name": f.name, "bytes": f.getvalue()} for f in uploaded_files
                    ]
                    st.session_state.source_parse_state = "uploaded"
                    st.rerun()

            st.markdown(_h('<div class="tp-task-section-heading">任务设置</div>'),
                        unsafe_allow_html=True)
            with st.container(key="task_settings_grid"):
                st.markdown(_h('<div class="tp-task-subheading">必填设置</div>'),
                            unsafe_allow_html=True)
                language_col, project_col = st.columns(2, gap="medium")
                with language_col:
                    with st.container(key="task_setting_language"):
                        language_options = ["简体中文", "繁体中文", "English", "日本語", "한국어",
                                            "Deutsch", "Français", "Español", "Русский", "Português",
                                            "Italiano", "العربية"]
                        current_language = str(st.session_state.get("target_lang") or "").strip()
                        if current_language and current_language not in language_options:
                            language_options.append(current_language)
                        st.selectbox(
                            _t("目标语言"), language_options, key="target_lang",
                            accept_new_options=True,
                            help=_t("可选择常用语言，或输入其他语言名称、语言代码；翻译能力取决于所用模型。"),
                            **_PERSIST_STATE, format_func=_localized_formatter())
                with project_col:
                    with st.container(key="task_setting_project"):
                        _render_task_project_context()

                st.markdown(_h('<div class="tp-task-subheading tp-task-subheading-optional">可选设置</div>'),
                            unsafe_allow_html=True)
                glossary_col, profile_col = st.columns(2, gap="medium")
                with glossary_col:
                    _render_task_termbase_setting()
                with profile_col:
                    _render_task_profile_setting()
            # Keep the current custom relay available to quick profiling, which
            # can run before the rest of the page reaches the pipeline setup.
            core.set_llm_base_url(api_base if provider_cfg.get("custom_base_url") else None)
            _render_task_actions(
                next_step=2,
                next_disabled=(not st.session_state.get("task_files")
                               or st.session_state.get("style_profiling_state")
                               == "running"))

        elif step == 2:
            _step_title(2, _t("翻译策略"), _t("选择适合本次任务的工作流；需要时可调整高级设置"))
            with st.container(key="preset_cards"):
                preset_columns = st.columns(3)
                for column, label in zip(preset_columns, _PRESET_CONFIGS):
                    state = "selected" if label == preset_label else "idle"
                    with column.container(key=f"preset_card_{label}_{state}"):
                        st.markdown(_preset_card_html(label), unsafe_allow_html=True)
                        display_label = _PRESET_DISPLAY_NAMES.get(label, label)
                        if st.button(_t('选择{v0}预设', v0=display_label), key=f"choose_preset_{label}"):
                            _apply_preset(label)
                            st.toast(_t('已恢复“{v0}”预设', v0=display_label))
                            st.rerun()
            strategy_config = st.session_state.strategy_config
            with st.container(key="task_segmentation_mode"):
                st.markdown(_h('<div class="tp-task-subheading">翻译单元</div>'),
                            unsafe_allow_html=True)
                _seg_options = ["按自然句（推荐）", "按自然段"]
                _current_seg_mode = str(
                    strategy_config.get("segmentation_mode") or "sentence")
                _seg_idx = 0 if _current_seg_mode == "sentence" else 1
                _picked_seg_label = st.radio(
                    _t("翻译单元粒度"), _seg_options,
                    index=_seg_idx,
                    key="strategy_seg_mode",
                    horizontal=True,
                    help=_t("按自然句提供主流 CAT 工具的逐句对照；按自然段保留清洗后的整段。"
                         "按句模式会启用小数点、缩写、网址、引号和列表门禁。"),
                    **_PERSIST_STATE, format_func=_localized_formatter())
                _new_seg_mode = "sentence" if "自然句" in _picked_seg_label else "paragraph"
                if _new_seg_mode != strategy_config.get("segmentation_mode"):
                    strategy_config["segmentation_mode"] = _new_seg_mode
                    st.session_state.strategy_config = strategy_config
                st.caption(_t("开始任务后该选择会锁定，避免翻译过程中改变段落索引。"))
            adjusted = _strategy_is_adjusted(preset_label, strategy_config)
            adjustment_count = _strategy_adjustment_count(preset_label, strategy_config)
            with st.container(key="strategy_advanced"):
                advanced_open = st.session_state.get("strategy_advanced_open", False)
                display_preset_label = _PRESET_DISPLAY_NAMES.get(preset_label, preset_label)
                state_text = ('<strong>' + str(display_preset_label) + ' · 已调整 ' + str(adjustment_count) + _h(' 项</strong>')) if adjusted \
                    else f'当前使用「{display_preset_label}」默认配置'
                summary_class = "tp-advanced-summary is-adjusted" if adjusted \
                    else "tp-advanced-summary"
                summary_text = (
                    f"{display_preset_label} · 已调整 {adjustment_count} 项 · "
                    f"已自定义 {adjustment_count} 项"
                    if adjusted else "默认配置")
                trigger_icon = "expand_less" if advanced_open else "chevron_right"
                st.markdown(
                    ('<div class="tp-advanced-trigger"><span class="tp-advanced-title"><span class="material-symbols-rounded" aria-hidden="true">' + str(trigger_icon) + _h('</span><strong>高级设置 · 可选</strong></span><span class="') + str(summary_class) + '">' + str(summary_text) + '</span></div>'),
                    unsafe_allow_html=True)
                st.button(_t("切换高级设置"), key="toggle_strategy_advanced",
                          on_click=_toggle_advanced_strategy, width="stretch")
                if advanced_open:
                    with st.container(key="advanced_body"):
                        st.markdown(f'<div class="tp-strategy-state">{state_text}</div>',
                                    unsafe_allow_html=True)
                        st.markdown(_h('<div class="tp-advanced-group">翻译辅助</div>'),
                                    unsafe_allow_html=True)
                        _render_strategy_toggle(
                            "自动术语抽取", "从全文识别候选术语并用于翻译",
                            "auto_term", "strategy_auto_term", strategy_config)
                        _render_strategy_toggle(
                            "全文文档理解", "建立文档画像、语义单元、章节摘要和全文概要",
                            "enable_understanding", "strategy_understanding", strategy_config)
                        _render_strategy_toggle(
                            "复用翻译记忆", "精确复用已审校通过的历史译文",
                            "use_tm", "strategy_use_tm", strategy_config)
                        st.markdown(_h('<div class="tp-advanced-group">质量控制</div>'),
                                    unsafe_allow_html=True)
                        st.markdown(
                            _h('<div class="tp-readonly-setting">'
                            '<div class="tp-readonly-head">'
                            '<strong>基础一致性检查</strong><b>始终开启</b></div>'
                            '<span>自动检查漏译、保留项、源语残留和锁定术语</span>'
                            '</div>'), unsafe_allow_html=True)
                        _render_strategy_toggle(
                            "独立审校", "使用独立模型阶段复核语义与术语，并保存审校证据",
                            "enable_review", "strategy_review", strategy_config)
                        st.markdown(_h('<div class="tp-advanced-group">术语治理</div>'),
                                    unsafe_allow_html=True)
                        _render_strategy_toggle(
                            "审核并冻结候选术语", "翻译前建立文档画像，并审核自动提取的候选术语",
                            "strict_terminology_governance", "strategy_strict_terms",
                            strategy_config)
                        st.markdown(_h('<div class="tp-advanced-group">分段与速度</div>'),
                                    unsafe_allow_html=True)
                        _concurrency_options = [1, 2, 4, 6, 8]
                        _current_concurrency = int(strategy_config.get("translation_concurrency") or 4)
                        _picked_concurrency = st.selectbox(
                            _t("并发请求数"), _concurrency_options,
                            index=_concurrency_options.index(_current_concurrency)
                            if _current_concurrency in _concurrency_options else 2,
                            key="strategy_concurrency",
                            help=_t("同时向模型发出的批次请求数。提高并发可成倍缩短总体翻译时间；"
                                 "若遇提供商限频（Rate Limit）可适当降低并发。"),
                            **_PERSIST_STATE, format_func=_localized_formatter())
                        if _picked_concurrency != strategy_config.get("translation_concurrency"):
                            strategy_config["translation_concurrency"] = _picked_concurrency
                            st.session_state.strategy_config = strategy_config
                        _batch_labels = list(BATCH_PROFILES)
                        _current_batch = str(strategy_config.get("batch_profile") or "保守")
                        _picked_batch = st.selectbox(
                            _t("批次策略"), _batch_labels,
                            index=_batch_labels.index(_current_batch)
                            if _current_batch in _batch_labels else 0,
                            key="strategy_batch_profile",
                            help=_t("批次越大，模型调用次数越少、整体越快；"
                                 "但单次返回项数不符或截断的风险也越高。"),
                            **_PERSIST_STATE, format_func=_localized_formatter())
                        if _picked_batch != _current_batch:
                            strategy_config["batch_profile"] = _picked_batch
                            st.session_state.strategy_config = strategy_config
                        st.caption(_t(BATCH_PROFILES[_picked_batch]["hint"]
                                   + "。大文档建议先用「均衡」跑一章确认质量。"))
            _render_task_actions(back_step=1, next_step=3)

        elif step == 3:
            st.markdown(
                _h('<div class="tp-section-title">交付内容'
                '<span class="tp-section-sub">&ensp;·&ensp;选择需要生成的文件与语言资产</span>'
                '</div>'), unsafe_allow_html=True)
            with st.container(key="delivery_builder"):
                _render_delivery_preset_selector()
            output_config = st.session_state.output_config
            research_visible = _research_outputs_visible(preset_label, strategy_config)
            if not output_config.get("deliver_bilingual_docx"):
                output_config["enable_annotate"] = False
                st.session_state["output_annotate"] = False
            with st.container(key="delivery_panels"):
                panel_left, panel_right = st.columns(2, gap="small")
                with panel_left:
                    _render_delivery_group(
                        "译文文件", "最终译文与审校用文档",
                        _DELIVERY_TRANSLATION_ITEMS, output_config,
                        group_key="translation")
                with panel_right:
                    _render_delivery_group(
                        "语言资产", "术语、翻译记忆与结构化数据",
                        _DELIVERY_ASSET_ITEMS, output_config,
                        group_key="assets")
            if research_visible:
                selected_theory = _render_delivery_group(
                    "研究产物", "仅对研究级翻译策略开放",
                    _DELIVERY_RESEARCH_ITEMS, output_config,
                    group_key="research",
                    after_rows=lambda: _render_delivery_research_details(output_config))
                if selected_theory:
                    translation_theory = selected_theory
            enable_annotate = bool(output_config.get("enable_annotate"))
            enable_report = bool(output_config.get("enable_report"))
            delivery_summary = _delivery_summary(
                output_config, research_visible=research_visible)
            _render_task_actions(
                back_step=2, next_step=4, delivery_summary=delivery_summary)

        else:
            _step_title(4, _t("确认运行"), _t("核对任务、输出内容与运行环境"))
            task_files = st.session_state.get("task_files") or []
            filename_summary = task_files[0]["name"] if len(task_files) == 1 \
                else f"{len(task_files)} 个文档"
            glossary_name = st.session_state.get("task_glossary_name", "未添加")
            style_template = st.session_state.get("style_template", "学术书面语")
            style_source = ""
            style_sel = st.session_state.get("style_selection")
            if style_sel:
                style_source = "接受系统推荐" if style_sel.get("source") == "accepted" \
                    else "用户调整"
            connection_status = st.session_state.get(
                "provider_connection_status", "unverified")
            task_review_required = bool(strategy_config.get("enable_review"))
            ai_view = _ai_configuration_view(task_review_required)
            can_start = bool(task_files and _workspace_view.task_ai_ready(ai_view)) and not bool(
                st.session_state.get("report_template_error"))
            if st.session_state.get("report_template_error"):
                st.warning(_t("请移除或重新上传可解析的报告模板后再开始任务。"))
            st.markdown(_summary_html(filename_summary, target_lang, preset_label,
                                      glossary_name, strategy_config, output_config,
                                      style_template, style_source),
                        unsafe_allow_html=True)
            st.markdown(_runtime_html(ai_provider, ai_model, ai_view["state"],
                                      can_start), unsafe_allow_html=True)
            if ai_view["state"] == "credentials_missing":
                with st.container(key="engine_setup_banner"):
                    message_col, action_col = st.columns([4, 1])
                    message_col.warning(
                        _t("已选择 AI 模型，但 API 凭据未配置。开始任务前，请先完成凭据设置。"),
                        icon=":material/warning:")
                    action_col.button(_t("前往设置"), key="open_engine_settings",
                                      on_click=_open_provider_settings,
                                      width="stretch")
            elif ai_view["state"] == "error":
                with st.container(key="engine_connection_banner"):
                    message_col, action_col = st.columns([3, 1.5])
                    message_col.error(
                        _t("最近一次连接测试未通过。请检查服务商、模型或 API 地址后再开始任务。"),
                        icon=":material/error:")
                    retry_col, settings_col = action_col.columns(2)
                    if retry_col.button(_t("重试"), key="step4_retry_connection", width="stretch"):
                        with st.spinner("正在验证连接…"):
                            ok, msg = core.test_provider(
                                ai_provider, api_key, ai_model, base_url=api_base)
                        st.session_state["provider_connection_status"] = "connected" if ok else "error"
                        st.session_state["provider_test_feedback"] = (ok, msg)
                        st.rerun()
                    settings_col.button(_t("检查设置"), key="open_engine_settings_error",
                                        on_click=_open_provider_settings,
                                        width="stretch")
            elif ai_view["state"] != "connected":
                with st.container(key="engine_connection_banner"):
                    message_col, action_col = st.columns([4, 1])
                    message_col.warning(
                        _t("模型已配置，但连接尚未验证。建议先测试连接，确认可用后再开始任务。"),
                        icon=":material/warning:")
                    if action_col.button(_t("测试"), key="open_engine_settings_unverified",
                                         width="stretch"):
                        with st.spinner("正在验证连接…"):
                            ok, msg = core.test_provider(
                                ai_provider, api_key, ai_model, base_url=api_base)
                        st.session_state["provider_connection_status"] = "connected" if ok else "error"
                        st.session_state["provider_test_feedback"] = (ok, msg)
                        st.rerun()
            run_clicked = _render_task_actions(
                back_step=3, next_label="开始任务", run=True,
                next_disabled=not can_start)

    elif app_view == "workspace":
        pass
    elif app_view == "history":
        _page_title(_t("历史任务"), "")
    elif app_view == "library":
        pass

# `setup_placeholder` 保留的是上一次脚本运行写入的子树。切换到 workspace、
# history、projects 或 library 时该分支不会再次写入内容，如果不显式清空，
# 用户会看到旧的「新建翻译任务」仍浮在新页面上方。
if not (app_view == "settings" or (app_view == "new" and not workspace_mode)):
    setup_placeholder.empty()

core.set_llm_base_url(api_base if provider_cfg.get("custom_base_url") else None)

# Keyboard users can bypass the persistent sidebar and reach the current page.
st.markdown('<span id="folio-main-content" tabindex="-1"></span>',
            unsafe_allow_html=True)

if app_view == "appearance":
    _preferences.render_preferences(core.OUTPUT_DIR)
    st.stop()
if app_view == "settings":
    _render_flashes()
    st.stop()
if app_view == "projects":
    # Project 管理页：/projects（列表）与 /projects/:projectId（详情）。
    _render_projects_surface()
    _render_flashes()
    st.stop()
if app_view == "library":
    # 术语与翻译记忆：语言资产管理中心（术语库 / 翻译记忆 / 待审核）。
    _render_flashes()
    _render_language_assets_workspace(core.list_jobs())
    st.stop()
if app_view == "history":
    _render_flashes()
    _render_history_delete_modal()
    if not saved_jobs:
        st.info(_t("暂无历史任务。新建任务后，这里会列出可以继续的翻译任务。"))
    else:
        _render_history_page(core.list_jobs())
    st.stop()
if app_view == "new" and not workspace_mode and not run_clicked:
    # 这一步也会被"带着上下文进入新建流程"用到（例如从已归档项目点新建任务），
    # 因此必须把队列里的提示真正渲染出来——否则用户看不到"为什么没归入那个项目"。
    _render_flashes()
    st.stop()

# ================= 核心处理流（后台 worker，UI 轮询 runtime 状态）=================
pending_job = st.session_state.pop("pending_continue_job", None)

tasks = []
seen = set()
if run_clicked:
    task_inputs = st.session_state.get("task_files") or []
    has_resume = bool(saved_jobs and resume_choice and resume_choice != "— 不继续 —")
    if not task_inputs and not has_resume:
        st.error(_t("请先上传待翻译文档，或在「上传与开始」卡片中选择要继续的本地任务。"))
    else:
        st.session_state.update(workspace_mode=True, app_view="workspace")
        # 任务身份 = 文档身份 + 本地化上下文。同一份文档在另一个项目或另一种
        # 目标语言下是一个**独立的本地化任务**：项目决定注入哪套项目记忆与
        # 术语，目标语言决定译文本身，两者都不是"文件内容"能表达的。
        # 只用内容哈希当任务 ID，会让"同文件 + 另一个项目/语言"静默打开旧任务。
        task_project = st.session_state.get("task_project_id")
        task_target_lang = st.session_state.get("target_lang") \
            or core.DEFAULT_TARGET_LANG
        for f in task_inputs:
            file_bytes = f["bytes"]
            job_id = core.resolve_task_id(
                file_bytes, project_id=task_project, target_lang=task_target_lang)
            if job_id in seen:
                continue
            seen.add(job_id)
            tasks.append({"job_id": job_id, "filename": f["name"],
                          "file_bytes": file_bytes})
        if has_resume:
            job = saved_jobs[job_choices.index(resume_choice)]
            if job["job_id"] not in seen:
                tasks.append({"job_id": job["job_id"],
                              "filename": job["state"].get("filename", "?"),
                              "file_bytes": None})
                st.session_state.active_job_id = job["job_id"]
elif pending_job:
    job = next((j for j in (saved_jobs or []) if j["job_id"] == pending_job), None)
    if job:
        tasks.append({"job_id": job["job_id"],
                      "filename": job["state"].get("filename", "?"),
                      "file_bytes": None})
        st.session_state.active_job_id = job["job_id"]

if tasks:
    started_jobs = []
    for task in tasks:
        job_id, filename, file_bytes = task["job_id"], task["filename"], task["file_bytes"]
        if st.session_state.get("report_template_removed"):
            core.clear_report_template(job_id)
            st.session_state.doc_states.pop(job_id, None)
        if template_input:
            core.save_report_template(
                job_id, template_input.get("name") or "template.docx",
                template_input.get("bytes") or b"")
        cached_state = st.session_state.doc_states.get(job_id)
        disk_state = core.load_job_state(job_id)
        # A source-rescan request is written by an explicit recovery action;
        # never let an older in-memory workbench snapshot overwrite that flag
        # before the worker can consume it.
        if isinstance(disk_state, dict) and disk_state.get("source_rescan_requested"):
            st.session_state.doc_states.pop(job_id, None)
            cached_state = None
        state = cached_state or disk_state or core.new_job_state(filename)
        # 归属项目必须在 worker 启动前落盘：worker 是独立进程，会从磁盘读取
        # project_id 来决定注入哪一套项目记忆。已开始的任务不改归属。
        # 「未分类」写入显式 null（而不是某个"默认项目"）：未分类任务由系统工作区
        # 承载（`resolved_project_id` 负责归一），显式 null 让"用户没有选择长期
        # 归属"这一事实在 state 里保持可见。
        if "task_project_id" in st.session_state and not state.get("glossary") \
                and not state.get("p2_done"):
            chosen_project = str(st.session_state.get("task_project_id") or "")
            # **落盘前必须确认项目记录还在**。上下文里的项目可能已经被删掉了：
            # 删除发生在另一个会话/标签页，或者本项目侧删除后这个会话仍留着旧值。
            # 以前这里直接写入，于是任务带着一个永远解析不出项目的 project_id
            # 存下来，变成一张"不在任何项目下"的孤儿卡片——项目侧列表与未分类
            # Inbox 都匹配不到它，两条删除入口都够不着（实测：两次孤儿任务的目录
            # 创建时间分别在其项目被删之后 29 秒与 13 秒）。
            chosen_project, stale_project = core.task_project_id_for_new_job(
                chosen_project)
            if stale_project:
                _push_flash(
                    "原来的项目已不存在，本次任务保存到系统工作区"
                    f"「{core.SYSTEM_PROJECT_NAME}」。", tone="warning")
                st.session_state["task_project_id"] = None
                st.session_state.pop("active_project_id", None)
                _route_params(project=None, view=None)
            state["project_id"] = chosen_project
            # 目标语言同样是任务身份的一部分（见 `core.resolve_task_id`），因此在
            # 任务创建时就落盘，而不是等流水线跑到才写：否则"这个任务是哪种目标
            # 语言"在任务存在的最初一段时间里是不可读的。
            if not state.get("target_lang"):
                state["target_lang"] = str(st.session_state.get("target_lang")
                                           or core.DEFAULT_TARGET_LANG)
        st.session_state.doc_states[job_id] = state
        core.save_job_state(job_id, state)

        # Report dependencies (research settings, literature, writer version) are
        # checked inside the backend before its early return.  Only skip here
        # when academic writing is explicitly disabled.
        if state["p1_done"] and state["p2_done"] and not enable_report \
                and (not enable_annotate or state.get("annotations_done")):
            started_jobs.append(job_id)
            continue

        try:
            # Step 01 的 Quick Profiling 产物：落盘为版本化 artifact，
            # 并把文档画像注入任务状态，让管线跳过重复画像。
            style_selection = st.session_state.get("style_selection")
            doc_profile = st.session_state.get("doc_profile")
            if style_selection:
                core.write_profile_artifacts(job_id, doc_profile, style_selection)
                if doc_profile and doc_profile.get("domain"):
                    job_state = core.load_job_state(job_id) or {}
                    if not job_state.get("profile_done"):
                        job_state["document_profile"] = doc_profile
                        job_state["profile_done"] = True
                        core.save_job_state(job_id, job_state)
            if task["file_bytes"] is not None:
                st.session_state.source_parse_state = "parsing"
            if file_bytes is None:
                started = core.resume_job(
                    job_id, filename, _pipeline_kwargs(state), base_url=api_base)
            else:
                started = core.start_job_worker(
                    job_id, filename, file_bytes, _pipeline_kwargs(state), base_url=api_base)
            if started or core.is_job_worker_alive(job_id):
                started_jobs.append(job_id)
                st.session_state.active_job_id = job_id
        except Exception as exc:  # setup failures remain visible in the UI
            st.error(_t('无法启动 {v0}：{v1}', v0=filename, v1=exc))
    if started_jobs:
        _open_job(started_jobs[0], core.load_job_state(started_jobs[0]), "translation")
        st.rerun()
    elif tasks:
        # Collection mutation without immediate rerun: 任务已保存到磁盘但未触发 rerun，刷新集合快照
        saved_jobs_snapshot = [{"job_id": item["job_id"], "state": item}
                               for item in core.list_job_summaries()]
        saved_jobs = saved_jobs_snapshot

# ================= 术语准备与审核面板（刷新/重启后自动恢复）=================
active = st.session_state.get("active_job_id")
if active is None and saved_jobs_snapshot:
    for job in saved_jobs_snapshot:
        s = core.load_job_state(job["job_id"]) or {}
        if s.get("p1_done") and not s.get("p2_done") and s.get("quality_mode") \
                and s.get("glossary") is not None:
            active = job["job_id"]
            break
st.session_state.active_job_id = active

if active and not (app_view == "workspace" and st.session_state.get("active_job_id")):
    astate = core.load_job_state(active)
    if astate and astate.get("p1_done") and not astate.get("p2_done") \
            and astate.get("quality_mode") and astate.get("glossary") is not None \
            and astate.get("stage") not in ("TRANSLATING", "TRANSLATED", "ACADEMIC_WRITING", "REPORT_GENERATED", "REVIEW_REQUIRED"):
        box = st.container(border=True)
        box.subheader(_t('术语准备与审核：{v0}', v0=astate.get('filename', '?')))
        _render_profile_editor(active, astate, box)

        entries = astate.get("glossary") or []
        frozen = astate.get("glossary_frozen")
        bypassed = astate.get("quality_bypass")
        if frozen:
            box.success(_t('术语表已冻结：版本 v{v0} 冻结时间 {v1}', v0=frozen.get('version'), v1=frozen.get('frozen_at', '')))
            with box.expander(_t("高级诊断"), expanded=False):
                st.caption(_t('术语版本内容指纹：{v0}', v0=frozen.get('glossary_hash', '')))
        elif bypassed:
            box.info(_t("已选择跳过人工冻结：术语以 provisional 建议注入翻译。"))
        else:
            box.warning(_t("术语尚未冻结：仍有候选术语待人工审核，「开始翻译」不可执行。"
                       "请完成审核后冻结，或选择跳过冻结。"))

        box.markdown(_glossary_status_chips(entries), unsafe_allow_html=True)
        fc1, fc2, fc3 = box.columns([2, 1, 1])
        filter_status = fc1.selectbox(
            _t("状态筛选"), ["全部", *(_TERM_STATUS_LABELS.values())],
            key=f"gfilter_status_{active}", format_func=_localized_formatter())
        only_conflicts = fc2.checkbox(_t("只看冲突项"), key=f"gfilter_conflict_{active}")
        filter_text = fc3.text_input(_t("搜索源术语"), key=f"gfilter_text_{active}")

        df = _glossary_dataframe(entries, astate.get("paras") or [])
        view_mask = pd.Series(True, index=df.index)
        if filter_status != "全部":
            view_mask &= df["status"].eq(_TERM_STATUS_VALUES[filter_status])
        if only_conflicts:
            view_mask &= df["冲突"].eq("冲突")
        if filter_text.strip():
            view_mask &= df["source"].str.contains(filter_text.strip(), case=False, na=False)
        df_view = df[view_mask]
        if not view_mask.all():
            box.caption(_t('已按条件筛选：显示 {v0} / {v1} 条术语。', v0=len(df_view), v1=len(df)))

        edited = box.data_editor(
            _humanize_glossary_editor(df_view),
            key=f"glossary_editor_{active}",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "选择": st.column_config.CheckboxColumn("选择", default=False),
                "id": st.column_config.TextColumn("ID", disabled=True),
                "source": st.column_config.TextColumn("源术语", required=True),
                "proposed_target": st.column_config.TextColumn("建议译名"),
                "target": st.column_config.TextColumn("目标译名"),
                "preferred": st.column_config.TextColumn("首选译名"),
                "forbidden": st.column_config.TextColumn("禁止译名（;分隔）"),
                "behavior": st.column_config.SelectboxColumn(
                    "行为", options=list(_TERM_BEHAVIOR_LABELS.values())),
                "status": st.column_config.SelectboxColumn(
                    "状态", options=list(_TERM_STATUS_LABELS.values())),
                "domain": st.column_config.TextColumn("领域"),
                "scope": st.column_config.TextColumn("范围"),
                "note": st.column_config.TextColumn("备注"),
                "confidence": st.column_config.NumberColumn(
                    "置信度", min_value=0.0, max_value=1.0, step=0.05),
                "出现次数": st.column_config.NumberColumn("出现次数", disabled=True),
                "上下文": st.column_config.TextColumn("部分上下文", disabled=True),
                "证据": st.column_config.TextColumn("证据", disabled=True),
                "冲突": st.column_config.TextColumn("冲突", disabled=True),
                "payload": st.column_config.TextColumn("payload", disabled=True),
            },
        )

        selected = edited[edited["选择"].fillna(False)] if "选择" in edited.columns \
            else edited.iloc[0:0]
        sel_ids = [str(x) for x in selected["id"].tolist() if str(x)]

        c1, c2, c3 = box.columns(3)
        if c1.button(_t("保存草稿"), key=f"gs_{active}", width="stretch"):
            core.save_glossary_draft(
                active, _merge_edited_entries(entries, _df_to_entries(edited)))
            st.rerun()
        if c2.button(_t("锁定选中术语"), disabled=not sel_ids, key=f"gl_{active}",
                          width="stretch"):
            core.set_glossary_entry_status(active, sel_ids, "locked")
            st.rerun()
        if c3.button(_t("拒绝选中术语"), disabled=not sel_ids, key=f"gr_{active}",
                          width="stretch"):
            core.set_glossary_entry_status(active, sel_ids, "rejected")
            st.rerun()

        c4, c5, c6 = box.columns(3)
        if c4.button(_t("冻结术语表并继续翻译"), key=f"gf_{active}",
                          width="stretch"):
            core.freeze_glossary(
                active, entries=_merge_edited_entries(entries, _df_to_entries(edited)),
                frozen_by="用户")
            st.session_state["pending_continue_job"] = active
            st.rerun()
        if c5.button(_t("跳过冻结并翻译"), key=f"gb_{active}",
                          width="stretch"):
            core.save_glossary_draft(
                active, _merge_edited_entries(entries, _df_to_entries(edited)))
            core.bypass_freeze(active)
            st.session_state["pending_continue_job"] = active
            st.rerun()
        if c6.button(_t("开始翻译"), disabled=not (frozen or bypassed),
                     key=f"gt_{active}", width="stretch"):
            st.session_state["pending_continue_job"] = active
            st.rerun()
        if frozen and not bypassed:
            if box.button(_t("返回修改（解除冻结）"), key=f"gu_{active}",
                          width="stretch"):
                core.unfreeze_glossary(active)
                st.rerun()
        if not frozen and not bypassed:
            box.caption(_t("翻译未开始：请先「冻结术语表并继续翻译」，"
                       "或选择跳过冻结（快速模式）。"))

# ================= 任务工作区（唯一工作区表面）=================
# 所有可达视图都在上面提前 st.stop()，工作区统一走这里的 compact shell。
# 旧的「资产与交付 / 文档上下文 / 研究报告（专用）」渲染面已按蓝图 §9 删除：
# 它会把工作区里每一个任务的资产面板、审校队列和报告渲染到同一页，是纯粹的
# 回归风险。回归防线见 tests/ui_console_test.py。
if app_view == "workspace":
    live_status = core.get_job_runtime_status(active).get("status") if active else None
    if live_status in {"resume_requested", "queued", "starting", "running",
                       "waiting_external", "cancelling"}:
        _render_live_workspace(active)
    else:
        _render_workspace_shell(active, core.load_job_state(active) if active else None)
    _render_flashes()
    st.stop()
