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
    # A line consisting solely of a number is content, not a list prefix.
    return _NUM_PREFIX.sub("", line).strip() or line.strip()


def _tail_loop(lines: List[str]) -> Optional[Tuple[int, int, int]]:
    """Find a loop that runs to the end of ``lines``: ``(start, period, repeats)``.

    A runaway model never resumes ordinary text after it starts repeating: it
    either repeats until the output limit or stops inside the repetition.  A run
    of identical lines followed by more text is therefore page content (a table
    column of 同上, a refrain) and is not a loop.  The response may end inside a
    unit, and its last line may be cut mid-line; that partial tail is allowed
    but is not counted as a repeat.
    """
    total = len(lines)
    best = None
    for period in range(1, 6):
        for partial in range(0, period + 1):
            end = total - partial
            if end < period * _REPEAT_MIN_COUNT:
                continue
            unit = lines[end - period:end]
            repeats = 1
            while (end - (repeats + 1) * period >= 0
                   and lines[end - (repeats + 1) * period:end - repeats * period] == unit):
                repeats += 1
            if repeats < _REPEAT_MIN_COUNT:
                continue
            tail = lines[end:]
            if tail and (tail[:-1] != unit[:len(tail) - 1]
                         or not unit[len(tail) - 1].startswith(tail[-1])):
                continue
            start = end - repeats * period
            # Widest coverage wins; on a tie the shorter period is the real unit.
            if best is None or total - start > total - best[0]:
                best = (start, period, repeats)
    return best


def collapse_tail_loop(text: str) -> Tuple[str, int]:
    """Return ``(kept_text, repeats)``; ``repeats`` is 0 when there is no loop.

    The kept text is everything before the loop plus one copy of its unit, in
    the original lines (list numbers included).
    """
    original = text.splitlines()
    indexed = [(i, _strip_num(line)) for i, line in enumerate(original) if line.strip()]
    found = _tail_loop([line for _, line in indexed])
    if found is None:
        return text, 0
    start, period, repeats = found
    last = indexed[start + period - 1][0]
    return "\n".join(original[:last + 1]), repeats


def detect_repetition(text: str) -> Tuple[str, bool]:
    """Keep the text before a loop that runs to the end, plus one copy of its unit."""
    kept, repeats = collapse_tail_loop(text)
    return kept, bool(repeats)


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
# At 220 DPI the source title page has 0.00331 dark-pixel coverage and the
# adjacent blank scan has 0.0000325 (grayscale <160).
_BLANK_INK_THRESHOLD = 0.0002


def _image_is_blank(png_bytes: bytes, threshold: float = _BLANK_INK_THRESHOLD) -> bool:
    """Return True if the rendered PNG has very low ink coverage.

    Counts pixels darker than 160 to exclude pale scan noise.
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
        dark = sum(hist[:160])
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
            clean, repeats = collapse_tail_loop(raw)
            if repeats:
                warnings.append(f"ocr_api_repetition_detected: 响应末尾有 {repeats} 次重复，"
                                "已保留第一次，请核查该页。")
            return decode_ocr_api_response(clean)

        model = self.config.model or ""
        user_prompt = (OCR_API_USER_PROMPT_PADDLE if _is_paddle_family(model)
                       else OCR_API_USER_PROMPT_DEFAULT)
        segments, error = self.read_image(
            png_bytes, system_prompt=OCR_API_SYSTEM_PROMPT, user_prompt=user_prompt,
            decoder=decode, context=context)
        if error and error.startswith("truncated:"):
            clean, repeats = collapse_tail_loop(context.get("response_text", ""))
            if repeats:
                try:
                    segments = decode_ocr_api_response(clean)
                    warnings.append(f"ocr_api_repetition_detected_truncated: 响应被截断，末尾有 {repeats} 次重复，"
                                    "已保留第一次，请核查该页。")
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
