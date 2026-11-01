"""Model capability classification for role-assignment warnings (#258).

Classifies a model name as one of:
  "ocr_only"          – returns plain text, not structured JSON (OCR output models)
  "translation_only"  – machine-translation only, no vision or structured output
  "general"           – general-purpose LLM; works for all roles

The matching is conservative and name-based.  A ``general`` label never means
"definitely works as vision"; it means "no evidence it cannot".

Rules (case-insensitive substring or pattern match on the model name):

OCR-only family — model names containing any of:
  "paddleocr", "paddle-ocr", "got-ocr", "olmocr", "-ocr-", "-ocr$", "_ocr_", "_ocr$"
  Matches:  PaddleOCR-VL-1.5, PaddlePaddle/PaddleOCR-VL-1.5, GOT-OCR, olmOCR-7B,
            stepfun-ai/GOT-OCR2_0
  Non-matches: qwen2.5-vl, deepseek-v4.1-flash, glm-4.5v, gpt-5, Qwen3-VL

Translation-only family — model names containing any of:
  "hunyuan-mt", "-mt-", "-mt$", "nllb", "opus-mt", "m2m100", "seamlessm4t"
  Matches:  tencent/Hunyuan-MT-7B, Helsinki-NLP/opus-mt-en-zh,
            facebook/m2m100_1.2B, facebook/seamless-m4t
  Non-matches: qwen2.5-vl, deepseek-v4.1-flash, glm-4.5v, gpt-5, Qwen3-VL,
               dots3-note-prev

A name matching BOTH families is classified as "ocr_only" (OCR takes precedence).
"""
from __future__ import annotations

from folith.model_capability import (  # noqa: F401 - re-exported
    _MT_PATTERNS, _OCR_PATTERNS, classify_model, task_model_problem,
)


#: Short human-readable labels for each capability class (Chinese UI).
CAPABILITY_LABELS: dict[str, str] = {
    "ocr_only": "仅 OCR",
    "translation_only": "仅翻译",
    "general": "",        # no badge needed for the normal case
}

#: Warning messages when a model is mismatched to a role.
#: key = (role_category, capability_class)
_WARNINGS: dict[tuple[str, str], str] = {
    ("vision", "ocr_only"): (
        "此模型仅输出纯文本，不能按视觉理解所需的结构化格式返回——"
        "扫描页将无法读取。请选择支持图像输入并能按格式回答的视觉语言模型。"
    ),
    ("vision", "translation_only"): (
        "此模型是机器翻译专用模型，不支持图像输入——"
        "扫描页将无法读取。请选择支持图像输入并能按格式回答的视觉语言模型。"
    ),
    ("translation", "ocr_only"): (
        "此模型是 OCR 专用模型，不能生成翻译。请选择通用语言模型。"
    ),
    ("translation", "translation_only"): (
        "这是专用翻译模型，术语抽取、文档画像等需要按格式回答的步骤可能失败；"
        "建议选通用模型。"
    ),
    ("reviewer", "ocr_only"): (
        "此模型是 OCR 专用模型，无法审校译文。请选择通用语言模型。"
    ),
    ("reviewer", "translation_only"): (
        "这是专用翻译模型，审校时需要按格式回答的步骤可能失败；"
        "建议选通用模型。"
    ),
}


def capability_warning(role_category: str, model_name: str) -> str:
    """Return a warning string if *model_name* is ill-suited for *role_category*, else ``""``.

    *role_category* is one of ``"vision"``, ``"translation"``, ``"reviewer"``.
    """
    cap = classify_model(model_name)
    if cap == "general":
        return ""
    return _WARNINGS.get((role_category, cap), "")


#: Short tag labels shown inline (muted, next to the model selector) for non-general models.
#: No tag for "general" or for the vision role (the channel A list is pre-filtered).
_TAGS: dict[str, str] = {
    "ocr_only": "仅 OCR",
    "translation_only": "专用翻译",
}

_TAG_TOOLTIPS: dict[str, str] = {
    "ocr_only": "此模型是 OCR 专用模型，不能生成翻译。请选择通用语言模型。",
    "translation_only": (
        "这是专用翻译模型（如 Hunyuan-MT），读不懂 Folith 的翻译要求，译文会变成无关的文字；"
        "用它不能开始任务。请选通用模型。"
    ),
}


def capability_tag(role_category: str, model_name: str) -> tuple[str, str]:
    """Return ``(tag_text, tooltip_text)`` for a small inline muted tag.

    Returns ``("", "")`` when no tag is needed (general model or vision role).
    Used by 用途 rows (translation / reviewer) instead of red warning paragraphs.
    """
    if role_category == "vision":
        return "", ""
    cap = classify_model(model_name)
    if cap == "general":
        return "", ""
    return _TAGS.get(cap, ""), _TAG_TOOLTIPS.get(cap, "")
