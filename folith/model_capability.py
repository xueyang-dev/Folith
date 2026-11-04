"""Name-based model capability classes, shared by the engine and the desktop client (#258).

Some provider catalogues let a user point a role at a model that cannot do the
job.  A vision role needs a general vision-language model that follows the
prompt and returns structured JSON; an OCR-only model (PaddleOCR-VL, GOT-OCR,
olmOCR …) ignores the instruction and returns plain transcribed text, so every
page fails with ``invalid_semantic_json`` or ``finish_reason=length``.  Likewise
a translation role is also used for term extraction, the document profile and
consistency checks, which need instruction-following and structured output — a
machine-translation-only model (Hunyuan-MT, NLLB, opus-mt …) cannot do that.

This module classifies a model name into one of three families:

  ``"ocr_only"``          – returns plain text, not structured JSON (OCR output models)
  ``"translation_only"``  – machine-translation only, no vision or structured output
  ``"general"``           – general-purpose model; works for all roles

The matching is deliberately conservative and name-based.  A ``general`` label
never means "definitely works as vision"; it means "no evidence it cannot", so an
unknown model is never flagged (no false warnings).

Rules (case-insensitive; a family token must sit on a name boundary):

OCR-only family — any of ``paddleocr``, ``paddle-ocr``, ``got-ocr``, ``olmocr``,
or a standalone ``ocr`` token (``-ocr-``, ``-ocr`` at the end, ``_ocr_``,
``/ocr-`` …).  Matches ``PaddlePaddle/PaddleOCR-VL-1.5``, ``GOT-OCR``,
``olmOCR-7B``, ``stepfun-ai/GOT-OCR2_0``, ``my-OCR-model``.  Does not match
``qwen2.5-vl``, ``deepseek-v4.1-flash``, ``glm-4.5v``, ``gpt-5``, ``Qwen3-VL``.

Translation-only family — ``hunyuan-mt``, a standalone ``mt`` token
(``-mt-``, ``-mt`` at the end, ``/mt-`` …), ``nllb``, ``opus-mt``, ``m2m100``,
``seamlessm4t``.  Matches ``tencent/Hunyuan-MT-7B``,
``Helsinki-NLP/opus-mt-en-zh``, ``foo/bar-MT-7B``.  Does not match the general
models above or ``dots3-note-prev``.

A name matching both families is classified as ``"ocr_only"`` (OCR wins): an
OCR model name is the stronger, more specific signal.
"""
from __future__ import annotations

import re

#: Models that read images but only emit plain text (no prompt-following JSON).
_OCR_PATTERNS = re.compile(
    r'paddleocr|paddle-ocr|got-ocr|olmocr|'
    r'(?:^|[-_/])ocr(?:[-_/]|$)',
    re.IGNORECASE,
)

#: Machine-translation-only models (no vision, no instruction-following output).
_MT_PATTERNS = re.compile(
    r'hunyuan-mt|(?:^|[-_/])mt-|(?:^|[-_/])mt$|'
    r'\bnllb\b|opus-mt|m2m100|seamlessm4t|seamless-m4t',
    re.IGNORECASE,
)

#: The three capability classes, for callers that want to validate a value.
CAPABILITY_CLASSES = ("ocr_only", "translation_only", "general")


def classify_model(name: str) -> str:
    """Return ``"ocr_only"``, ``"translation_only"`` or ``"general"`` for *name*.

    >>> classify_model("PaddlePaddle/PaddleOCR-VL-1.5")
    'ocr_only'
    >>> classify_model("tencent/Hunyuan-MT-7B")
    'translation_only'
    >>> classify_model("qwen2.5-vl")
    'general'
    """
    name = str(name or "").strip()
    if _OCR_PATTERNS.search(name):
        return "ocr_only"
    if _MT_PATTERNS.search(name):
        return "translation_only"
    return "general"
