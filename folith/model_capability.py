"""Name-based model capability classes, shared by the engine and the desktop client (#258).

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

import re

# ---- OCR-only patterns -------------------------------------------------------
#: Compiled patterns for the OCR-only model family.
_OCR_PATTERNS = re.compile(
    r'paddleocr|paddle-ocr|got-ocr|olmocr|'
    r'-ocr-|-ocr$|_ocr_|_ocr$|/ocr-|/ocr$',
    re.IGNORECASE,
)

# ---- Translation-only patterns -----------------------------------------------
#: Compiled patterns for the machine-translation-only model family.
_MT_PATTERNS = re.compile(
    r'hunyuan-mt|-mt-7b|-mt-\d|-mt$|'
    r'\bnllb\b|opus-mt|m2m100|seamlessm4t|seamless-m4t',
    re.IGNORECASE,
)


def classify_model(name: str) -> str:
    """Return the capability class of *name*: ``"ocr_only"``, ``"translation_only"``, or ``"general"``.

    >>> classify_model("PaddlePaddle/PaddleOCR-VL-1.5")
    'ocr_only'
    >>> classify_model("tencent/Hunyuan-MT-7B")
    'translation_only'
    >>> classify_model("qwen2.5-vl")
    'general'
    >>> classify_model("gpt-5")
    'general'
    """
    name = str(name or "").strip()
    if _OCR_PATTERNS.search(name):
        return "ocr_only"
    if _MT_PATTERNS.search(name):
        return "translation_only"
    return "general"



#: Shown when a task would start with a model that cannot do the role (Settings
#: only tags such models; starting a task is where they are refused).
_TASK_PROBLEMS: dict[tuple[str, str], str] = {
    ("translation", "translation_only"): (
        "翻译模型「{v0}」是专用翻译模型，只会逐句直译，读不懂 Folith 的翻译要求"
        "（术语、文风、按格式返回），译文会变成无关的文字。请在 设置 › 模型 › 用途 换一个通用模型。"),
    ("translation", "ocr_only"): (
        "翻译模型「{v0}」是 OCR 专用模型，只能识别图片里的文字，不能翻译。"
        "请在 设置 › 模型 › 用途 换一个通用模型。"),
    ("reviewer", "translation_only"): (
        "审校模型「{v0}」是专用翻译模型，读不懂审校要求，审校结果会是无关的文字。"
        "请在 设置 › 模型 › 用途 换一个通用模型。"),
    ("reviewer", "ocr_only"): (
        "审校模型「{v0}」是 OCR 专用模型，不能审校译文。"
        "请在 设置 › 模型 › 用途 换一个通用模型。"),
}


def task_model_problem(role: str, model: str) -> str:
    """Why a task cannot start with *model* in *role* (``translation`` / ``reviewer``); ``""`` if it can.

    Measured on a real book: Hunyuan-MT-7B answered 248 of 302 segments with
    paraphrases of Folith's own instructions, and every source-cleanup batch
    failed.  Such a run cannot produce a usable translation, so it is refused
    before it spends hours of requests.
    """
    template = _TASK_PROBLEMS.get((role, classify_model(model)), "")
    return template.replace("{v0}", str(model)) if template else ""
