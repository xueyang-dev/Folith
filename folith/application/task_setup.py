"""One frontend-neutral source of truth for the *pre-translation setup surface*.

The Streamlit shell grew the "新建翻译任务" wizard first, and its presets and
delivery catalog used to live as private constants inside ``app.py``.  The Qt
client now offers the same preparation step, so those tables have to exist once:
two copies would drift, and a user would be able to pick a product in one
surface that the other never produces.

Nothing here is Streamlit- or Qt-specific; this module imports without either
framework (``core`` is imported lazily, exactly like ``commands`` does).

Contents:

* :data:`STRATEGY_PRESETS` — the 快速 / 标准 / 学术增强 workflow presets;
* :data:`BATCH_PROFILES` — batch sizing (fewer calls vs. truncation risk);
* :data:`DELIVERY_PRESETS` — 精简 / 标准 / 完整 / 自定义 output bundles;
* :data:`DELIVERY_TRANSLATION_ITEMS` / :data:`DELIVERY_ASSET_ITEMS` /
  :data:`DELIVERY_RESEARCH_ITEMS` — the per-file products, grouped as the
  delivery step presents them.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Tuple

__all__ = [
    "BATCH_PROFILES",
    "BATCH_PROFILE_LABELS",
    "DELIVERY_ASSET_ITEMS",
    "DELIVERY_OUTPUT_KEYS",
    "DELIVERY_PRESETS",
    "DELIVERY_PRESET_CONFIGS",
    "DELIVERY_PRESET_KEYS",
    "DELIVERY_RESEARCH_ITEMS",
    "DELIVERY_RESEARCH_KEYS",
    "DELIVERY_TRANSLATION_ITEMS",
    "DELIVERY_WIDGET_KEYS",
    "RESEARCH_STRATEGY_PRESETS",
    "STRATEGY_PRESET_LABELS",
    "STRATEGY_PRESET_KEYS",
    "STRATEGY_PRESET_SUMMARIES",
    "STRATEGY_PRESETS",
    "PANEL_STATE_KEYS",
    "batch_params",
    "default_delivery_config",
    "delivery_config_for_outputs",
    "delivery_items_for_source",
    "delivery_preset_for",
    "research_outputs_visible",
    "strategy_preset_for",
]

# ---------------------------------------------------------------------------
# translation strategy presets
# ---------------------------------------------------------------------------

#: Workflow presets.  A preset is a *template*: the effective configuration is
#: the preset's values, optionally adjusted in the advanced section.
STRATEGY_PRESETS: Dict[str, Dict[str, Any]] = {
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
STRATEGY_PRESET_KEYS: Tuple[str, ...] = tuple(STRATEGY_PRESETS)

#: User-facing names.  The internal key stays stable; only the label changes.
STRATEGY_PRESET_LABELS: Dict[str, str] = {
    "快速": "快速",
    "标准": "标准",
    "学术增强": "精细",
}

#: One-line summaries for **compact** surfaces (the desktop preparation panel).
#:
#: They must describe what the preset's :data:`STRATEGY_PRESETS` values actually
#: do — the preset is the template, so a summary that promises more than the
#: configuration delivers is a lie the user only discovers after a full run:
#:
#: * 快速     — no document profile, no term extraction, no review;
#: * 标准     — document profile + term extraction, still no review pass;
#: * 学术增强 — adds the review pass and locks the confirmed terminology.
#:
#: The Streamlit wizard renders its own, longer card copy
#: (``app.py:_preset_card_html``, with expectation / workflow / trade-off
#: metrics); this table is the short form for surfaces with one line of room.
STRATEGY_PRESET_SUMMARIES: Dict[str, str] = {
    "快速": "优先处理效率",
    "标准": "质量与效率兼顾",
    "学术增强": "更充分的质量检查",
}

#: Batch strategy: a bigger batch means fewer calls (faster) but a higher risk
#: of a mismatched or truncated response.  Measured on this repo's 82-segment
#: document: 保守 32 batches, 均衡 17, 快速 13.
BATCH_PROFILES: Dict[str, Dict[str, Any]] = {
    "保守": {"batch_size": 4, "max_batch_chars": 2400,
             "hint": "每批最多 4 段 / 2400 字符；调用次数最多，单次响应最稳"},
    "均衡": {"batch_size": 6, "max_batch_chars": 4800,
             "hint": "每批最多 6 段 / 4800 字符；调用次数约减半"},
    "快速": {"batch_size": 8, "max_batch_chars": 6400,
             "hint": "每批最多 8 段 / 6400 字符；最快，但更容易出现返回项数不符"},
}

#: Display names for the batch profiles.  ``保守 / 均衡 / 快速`` reads as a
#: quality ladder (safe → fast); the underlying ``batch_size`` only changes how
#: many segments share one request, so the labels say what actually differs —
#: request scheduling.  The keys (and therefore every serialized value) stay put.
BATCH_PROFILE_LABELS: Dict[str, str] = {
    "保守": "稳妥",
    "均衡": "均衡",
    "快速": "高吞吐",
}


def batch_params(config: Mapping[str, Any] | None) -> Tuple[int, int]:
    """Map a batch profile name to ``(batch_size, max_batch_chars)``."""
    profile = BATCH_PROFILES.get(str((config or {}).get("batch_profile") or "保守"),
                                 BATCH_PROFILES["保守"])
    return profile["batch_size"], profile["max_batch_chars"]


# ---------------------------------------------------------------------------
# delivery products
# ---------------------------------------------------------------------------

def default_delivery_config() -> Dict[str, Any]:
    """The core's neutral delivery defaults (lazy import; no core at import time)."""
    import core  # noqa: PLC0415 - frontend-neutral module must stay import-light

    return core.default_delivery_config()


#: Every delivery key the export pipeline understands.
DELIVERY_OUTPUT_KEYS: Tuple[str, ...] = (
    "enable_annotate", "enable_report", "deliver_plain_docx",
    "deliver_bilingual_docx", "deliver_pdf", "deliver_original_pdf",
    "deliver_epub", "deliver_terms_xlsx",
    "deliver_tbx", "deliver_tmx", "deliver_jsonl", "deliver_evidence",
    "deliver_cases", "deliver_academic_workspace", "deliver_review_report",
)

#: The research-only products: additional products, never invisible extras
#: hidden behind an ordinary translation bundle.
DELIVERY_RESEARCH_KEYS: Tuple[str, ...] = (
    "enable_report", "deliver_evidence", "deliver_cases",
    "deliver_academic_workspace", "deliver_review_report",
)


def delivery_config_for_outputs(*selected: str) -> Dict[str, Any]:
    """A complete export config with exactly ``selected`` products enabled."""
    config = default_delivery_config()
    for key in DELIVERY_OUTPUT_KEYS:
        config[key] = False
    for key in selected:
        if key in config:
            config[key] = True
    return config


DELIVERY_PRESETS: Dict[str, Dict[str, Any]] = {
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
                    "deliver_original_pdf", "deliver_terms_xlsx", "deliver_tmx",
                    "deliver_tbx"),
    },
    "custom": {
        "label": "自定义",
        "description": "按你的交付对象和下游工具手动配置。",
        "outputs": (),
    },
}
DELIVERY_PRESET_KEYS: Tuple[str, ...] = tuple(DELIVERY_PRESETS)
DELIVERY_PRESET_CONFIGS: Dict[str, Dict[str, Any]] = {
    key: delivery_config_for_outputs(*value["outputs"])
    for key, value in DELIVERY_PRESETS.items()
    if key != "custom"
}

#: Widget keys the delivery step owns (used by the Streamlit shell to reset its
#: widgets when a preset is applied).
DELIVERY_WIDGET_KEYS: Tuple[str, ...] = (
    "deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf",
    "deliver_original_pdf", "deliver_epub",
    "output_annotate", "deliver_terms_xlsx", "deliver_tmx", "deliver_tbx",
    "deliver_jsonl", "output_report", "deliver_evidence", "deliver_cases",
    "deliver_academic_workspace", "deliver_review_report",
)

#: Strategy names that unlock the research-product section.  Keeping the mapping
#: here makes the "策略 → 交付内容" dependency explicit and gives future
#: research-grade strategies one place to join.
#:
#: Both spellings of the research-grade preset are listed: the internal key and
#: every label it has ever been displayed under (the label moved from 深度研究 to
#: 精细, and a caller that passes the label instead of the key must still unlock
#: the section).
RESEARCH_STRATEGY_PRESETS = frozenset({
    "学术增强", "研究与报告", "研究级", "深度研究", "精细"})


def research_outputs_visible(preset_label: str | None = None,
                            strategy_config: Mapping[str, Any] | None = None) -> bool:
    """Whether the research-product section applies to this strategy."""
    label = preset_label or "标准"
    config = strategy_config or {}
    return (label in RESEARCH_STRATEGY_PRESETS
            or bool(config.get("research_outputs_enabled")))


DELIVERY_TRANSLATION_ITEMS: Tuple[Dict[str, Any], ...] = (
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
        "key": "deliver_original_pdf", "title": "原排版译文（Beta）", "format": "PDF",
        "detail": "保留原文件的版式、图形与表格，译文就位；未完成的区域会在页面上标出。",
        "file_units": (("PDF", 1),),
        "disambig_fmt": True,
        # Only a PDF source has page geometry to rebuild from.
        "pdf_only": True,
    },
    {
        "key": "deliver_epub", "title": "译文电子书", "format": "EPUB",
        "detail": "保留原图与段落顺序，支持阅读器调整字号；适用于普通 PDF / DOCX。",
        "file_units": (("EPUB", 1),),
    },
)
DELIVERY_ASSET_ITEMS: Tuple[Dict[str, Any], ...] = (
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
DELIVERY_RESEARCH_ITEMS: Tuple[Dict[str, Any], ...] = (
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


def delivery_items_for_source(items: Iterable[Mapping[str, Any]],
                              source_is_pdf: bool) -> Tuple[Mapping[str, Any], ...]:
    """Drop PDF-only rows for a non-PDF source (they can never be produced)."""
    if source_is_pdf:
        return tuple(items)
    return tuple(item for item in items if not item.get("pdf_only"))


#: Keys the preparation surface writes for its own round-trip.
#:
#: The resolver only reads *some* of these back (the ones with state keys in
#: :mod:`folith.application.settings`); the rest are carried so reopening the
#: panel shows what the user chose instead of a rebuilt guess.  Unknown keys are
#: ignored by the core, so this stays additive.
PANEL_STATE_KEYS: Tuple[str, ...] = (
    "strategy_preset", "batch_profile", "translation_concurrency",
    "enable_review", "strict_terminology_governance", "translation_theory",
    "ocr_language", "ocr_mode", "ocr_backend", "ocr_quality",
)


def strategy_preset_for(strategy: Mapping[str, Any]) -> str:
    """The preset whose template matches ``strategy`` exactly, else ``"标准"``.

    Used to highlight the chosen card on reopen; an adjusted strategy simply
    shows no card highlighted rather than silently claiming to be a preset.
    """
    for label, config in STRATEGY_PRESETS.items():
        if all(strategy.get(key) == value for key, value in config.items()):
            return label
    return ""


def delivery_preset_for(delivery: Mapping[str, Any]) -> str:
    """The delivery preset whose product set matches ``delivery``, else ``"custom"``."""
    for key, meta in DELIVERY_PRESETS.items():
        if key == "custom":
            continue
        expected = DELIVERY_PRESET_CONFIGS[key]
        if all(bool(delivery.get(name)) == bool(value) for name, value in expected.items()):
            return key
    return "custom"
