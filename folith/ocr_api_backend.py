"""OCR API backend: plain-text transcription through an OpenAI-compatible endpoint.

This backend differs from :class:`folith.ocr_online.OnlineOCRBackend` (the VLM
structured-understanding path) in three key ways:

1. **No JSON schema.**  The prompt asks for a faithful plain-text transcription;
   the model returns the page text in reading order, one paragraph per line, with
   blank lines between paragraphs.  Any ``finish_reason == "stop"`` response is
   accepted.

2. **PaddleOCR-VL native task prompt.**  When the model name matches the
   PaddleOCR-VL family the user prompt is ``"OCR:"`` (its native task prefix),
   which activates a higher-quality OCR mode than a free-form instruction.

3. **Repetition guard.**  PaddleOCR-VL and related models sometimes loop — the
   response repeats the same sentence or block (often the instruction text itself)
   until ``finish_reason == "length"``.  This guard detects that pattern, keeps
   only the first occurrence, and marks the page result as needing review so the
   user can verify the output.

Transport (retries, pacing, rate-limit backoff, concurrency, cancel) is identical
to the existing online OCR path: it delegates to
:meth:`folith.ocr_online.OnlineOCRBackend.read_image`.
"""
from __future__ import annotations

import dataclasses
import io
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from folith.ocr_evidence import OCRBlock, OCRPageResult, OCRRuntimeInfo
from folith.ocr_language import direction_for
from folith.ocr_online import (
    OnlineOCRBackend, OnlineOCRConfig,
    DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_S, DEFAULT_MAX_RETRIES,
)

#: Backend name stored in task state and pipeline config.
OCR_API_BACKEND = "ocr_api"

#: Prompt-version token.  Part of the cache identity; bump when the instruction
#: changes so cached pages are not mixed with the new prompt's output.
OCR_API_PROMPT_VERSION = "plain-v1"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

OCR_API_SYSTEM_PROMPT = (
    "You are a faithful transcription agent for scanned-page images.\n"
    "Read the image and output the visible text in reading order.\n"
    "- Output paragraphs separated by blank lines.\n"
    "- Transcribe only what is visible; do not translate, summarise or add headings.\n"
    "- Mark illegible characters with [?]; mark an entirely illegible line as [?].\n"
    "- Do not output Markdown, code blocks, JSON or any explanation — plain text only."
)

#: Generic user message when no native task prompt applies.
OCR_API_USER_PROMPT_DEFAULT = "Transcribe all visible text on this scanned page."

#: PaddleOCR-VL native task prompt (activates the model's built-in OCR mode).
OCR_API_USER_PROMPT_PADDLE = "OCR:"

# ---------------------------------------------------------------------------
# Repetition guard
# ---------------------------------------------------------------------------

#: Minimum number of repeated items before a loop is declared.
_REPEAT_MIN_COUNT = 6
#: A line is stripped of its leading list-number before comparison.
_NUM_PREFIX = re.compile(r"^\s*[\(\[]?\d+[\)\].:]\s*")


def _strip_num(line: str) -> str:
    return _NUM_PREFIX.sub("", line).strip()


def detect_repetition(text: str) -> Tuple[str, bool]:
    """Keep the text preceding a consecutive loop and one copy of its unit."""
    original = text.splitlines()
    indexed = [(i, _strip_num(line)) for i, line in enumerate(original) if line.strip()]
    stripped = [line for _, line in indexed]
    for start in range(len(stripped)):
        for period in range(1, 6):
            end = start + period * _REPEAT_MIN_COUNT
            if end > len(stripped):
                continue
            unit = stripped[start:start + period]
            if all(stripped[pos:pos + period] == unit
                   for pos in range(start, end, period)):
                last = indexed[start + period - 1][0]
                return "\n".join(original[:last + 1]), True
    return text, False


# ---------------------------------------------------------------------------
# Plain-text decoder
# ---------------------------------------------------------------------------

def decode_ocr_api_response(raw: str) -> List[Dict[str, Any]]:
    """Convert a plain-text OCR response into the segment list ``read_image`` expects.

    Unlike ``decode_segments`` (which requires JSON), this accepts any plain text.
    Raises ``ValueError`` on empty responses (same contract as ``decode_segments``
    so the existing retry logic in ``read_image`` triggers correctly).

    The caller is responsible for running :func:`detect_repetition` *before*
    passing to this function — the decoder just splits into paragraphs.
    """
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty_response")

    # Remove any markdown code fencing a model might wrap around output.
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    if not text:
        raise ValueError("empty_response")

    # Split on blank lines (paragraphs), fall back to single-line split.
    if re.search(r"\n\s*\n", text):
        parts = re.split(r"\n\s*\n", text)
    else:
        parts = text.split("\n")

    segments: List[Dict[str, Any]] = []
    idx = 0
    for part in parts:
        body = part.strip()
        if body:
            segments.append({"index": idx, "text": body, "break_after": True})
            idx += 1

    if not segments:
        raise ValueError("empty_transcription")
    return segments


# ---------------------------------------------------------------------------
# Blank-page detector
# ---------------------------------------------------------------------------

#: Fraction of non-white pixels below which a page is considered blank.
_BLANK_INK_THRESHOLD = 0.02


def _image_is_blank(png_bytes: bytes, threshold: float = _BLANK_INK_THRESHOLD) -> bool:
    """Return True if the rendered PNG has very low ink coverage.

    Converts to grayscale and counts pixels darker than 250/255.  A page with
    fewer than *threshold* (default 2 %) non-white pixels is treated as blank.
    Returns False on any import or decode error so callers default to the
    normal empty-response failure path.
    """
    try:
        from PIL import Image  # soft dependency — already required by the Qt layer
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        total = img.width * img.height
        if total == 0:
            return True
        hist = img.histogram()  # list of 256 counts (brightness 0–255)
        dark = sum(hist[:250])  # pixels with brightness 0–249 (not near-white)
        return dark / total < threshold
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Backend class
# ---------------------------------------------------------------------------

def _is_paddle_family(model_name: str) -> bool:
    name = str(model_name or "").lower()
    return "paddleocr" in name or "paddle-ocr" in name or "paddle_ocr" in name


class OcrApiBackend(OnlineOCRBackend):
    """Plain-text OCR API backend: extends OnlineOCRBackend transport, different prompt.

    Call :meth:`recognize_page` (inherited from :class:`~folith.ocr_evidence.OCRBackend`)
    to process a page image.  The result's ``warnings`` list will contain
    ``"repetition_detected"`` when the guard triggered, so the caller can mark
    the page for review.
    """

    name = OCR_API_BACKEND
    display_name = "OCR API"

    # -- runtime metadata ------------------------------------------------
    def runtime_info(self, language: str, *,
                     include_model_version: bool = True) -> OCRRuntimeInfo:
        info = super().runtime_info(language, include_model_version=include_model_version)
        return dataclasses.replace(
            info,
            backend=self.name,
            backend_version="ocr-api-plain/" + OCR_API_PROMPT_VERSION,
        )

    # -- page recognition ------------------------------------------------
    def recognize_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                       language: str, dpi: int) -> OCRPageResult:
        """Recognize a page using diagnostics owned by this call."""
        started = time.perf_counter()
        blocks, error, warnings, context = self._recognize(
            png_bytes, page_index=page_index, page_number=page_number,
            language=language, dpi=dpi)
        return OCRPageResult(
            page_index=page_index, page_number=page_number, language=language,
            direction=direction_for(language), dpi=dpi, blocks=blocks,
            engine=self.runtime_info(language).to_dict(),
            timings={"recognition_s": time.perf_counter() - started,
                     **context.get("timings", {})},
            error=error or "", warnings=warnings,
            request=context.get("request", {}),
        )

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        blocks, error, _, _ = self._recognize(
            png_bytes, page_index=page_index, page_number=page_number,
            language=language, dpi=dpi)
        return blocks, error

    def _recognize(self, png_bytes, *, page_index, page_number, language, dpi):
        context = {}
        warnings = []

        def decode(raw):
            clean, looped = detect_repetition(raw)
            if looped:
                warnings.append("ocr_api_repetition_detected: 响应内容重复，已保留循环前内容，请核查该页。")
            return decode_ocr_api_response(clean)

        model = self.config.model or ""
        user_prompt = (OCR_API_USER_PROMPT_PADDLE if _is_paddle_family(model)
                       else OCR_API_USER_PROMPT_DEFAULT)
        segments, error = self.read_image(
            png_bytes, system_prompt=OCR_API_SYSTEM_PROMPT, user_prompt=user_prompt,
            decoder=decode, context=context)
        if error and error.startswith("truncated:"):
            clean, looped = detect_repetition(context.get("response_text", ""))
            if looped:
                try:
                    segments = decode_ocr_api_response(clean)
                    warnings.append("ocr_api_repetition_detected_truncated: 响应被截断且内容重复，已保留循环前内容，请核查该页。")
                    error = None
                except ValueError:
                    pass
            if error:
                error = "truncated: OCR 输出过长被截断（finish_reason=length）；该页未保存，可提高输出上限后重试。"
        elif error and error.startswith("empty_response:"):
            if _image_is_blank(png_bytes):
                warnings.append("ocr_api_blank_page: 没有识别到文字，按空白页处理。")
                error = None
            else:
                error = "empty_response: OCR 返回空响应；该页未保存。"
        blocks = self._blocks_from_segments(segments, page_index, page_number, language) if not error else []
        return blocks, error, warnings, context

    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps["prompt_style"] = "plain_text"
        caps["accepts_json"] = False
        return caps
