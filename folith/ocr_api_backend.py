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
    """Return ``(first_occurrence_text, was_looped)``.

    Checks for two patterns:

    * **Single-item repetition**: the stripped content of > 30 % of lines is
      identical and appears at least ``_REPEAT_MIN_COUNT`` times.
      Common in PaddleOCR-VL instruction loops::

          1. Read all content on this page.
          2. Read all content on this page.
          …

    * **Block repetition**: a block of 2–5 consecutive lines repeats
      ``_REPEAT_MIN_COUNT`` or more times.

    When a loop is detected, only the *first* unit (one line or one block) is
    returned so the caller has something to work with rather than empty output.
    The caller must mark the page for human review.
    """
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < _REPEAT_MIN_COUNT:
        return text, False

    stripped = [_strip_num(l) for l in lines]

    # --- single-item repetition -------------------------------------------
    counter: Dict[str, int] = {}
    for s in stripped:
        if s:
            counter[s] = counter.get(s, 0) + 1
    if counter:
        best, count = max(counter.items(), key=lambda kv: kv[1])
        if count >= _REPEAT_MIN_COUNT and count >= len(lines) * 0.30:
            return best, True

    # --- block repetition (period 2–5) ------------------------------------
    for period in range(2, 6):
        if len(lines) < period * _REPEAT_MIN_COUNT:
            continue
        unit = tuple(stripped[i] for i in range(period))
        repeat_count = 0
        for i in range(0, len(lines), period):
            chunk = tuple(stripped[i:i + period])
            if chunk == unit[: len(chunk)]:
                repeat_count += 1
            else:
                break
        if repeat_count >= _REPEAT_MIN_COUNT:
            return "\n".join(lines[:period]), True

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

    def __init__(self, config: OnlineOCRConfig, **kwargs: Any) -> None:
        super().__init__(config, **kwargs)
        self._repetition_detected: bool = False
        self._repetition_warning: str = ""

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
        """Run OCR and return result; repetition warning is added when detected."""
        self._repetition_detected = False
        self._repetition_warning = ""

        started = time.perf_counter()
        blocks, error = self.ocr_page(
            png_bytes, page_index=page_index, page_number=page_number,
            language=language, dpi=dpi)
        warnings: List[str] = []
        if self._repetition_detected:
            warnings.append(self._repetition_warning or "repetition_detected")

        return OCRPageResult(
            page_index=page_index,
            page_number=page_number,
            language=language,
            direction=direction_for(language),
            dpi=dpi,
            blocks=blocks or [],
            engine=self.runtime_info(language).to_dict(),
            timings={"recognition_s": time.perf_counter() - started,
                     **dict(self._last_timings)},
            error=error or "",
            warnings=warnings,
            request=dict(self._last_request or {}),
        )

    def ocr_page(self, png_bytes: bytes, *, page_index: int, page_number: int,
                 language: str, dpi: int) -> Tuple[List[OCRBlock], Optional[str]]:
        """Transcribe one page image; returns ``(blocks, error_or_None)``."""
        backend_ref = self  # captured by closure below

        def _decoder(raw: str) -> List[Dict[str, Any]]:
            # Repetition guard: runs before splitting into segments.
            clean, looped = detect_repetition(raw)
            if looped:
                backend_ref._repetition_detected = True
                backend_ref._repetition_warning = (
                    "ocr_api_repetition_detected: "
                    "响应内容重复，已保留第一次出现的内容，请核查该页。"
                )
                raw = clean  # decode only the first occurrence
            return decode_ocr_api_response(raw)

        model = self.config.model or ""
        system_prompt = OCR_API_SYSTEM_PROMPT
        user_prompt = (OCR_API_USER_PROMPT_PADDLE if _is_paddle_family(model)
                       else OCR_API_USER_PROMPT_DEFAULT)

        segments, error = self.read_image(
            png_bytes,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            decoder=_decoder,
        )
        if error:
            # Fix A: finish_reason=length + repetition loop → recover the first cycle.
            # ocr_online.read_image now stores the raw truncated text in
            # self._last_response_text before calling _fail("truncated", …), so we
            # can inspect it here.
            if error.startswith("truncated:"):
                raw_truncated = getattr(self, "_last_response_text", "")
                if raw_truncated:
                    clean, looped = detect_repetition(raw_truncated)
                    if looped:
                        self._repetition_detected = True
                        self._repetition_warning = (
                            "ocr_api_repetition_detected_truncated: "
                            "响应被截断且内容重复，已保留第一次出现的内容，请核查该页。"
                        )
                        try:
                            segs = decode_ocr_api_response(clean)
                            return (self._blocks_from_segments(
                                segs, page_index, page_number, language), None)
                        except ValueError:
                            pass
                # Fix B: truncated without a detectable loop → OCR-specific message.
                return [], ("truncated: OCR 输出过长被截断（finish_reason=length）；"
                            "该页未保存，可提高输出上限后重试。")

            # Fix B: empty response — try blank-page check first (Fix C).
            if error.startswith("empty_response:"):
                if _image_is_blank(png_bytes):
                    # Truly blank page: not a failure, just no text.
                    return [], None
                return [], "empty_response: OCR 返回空响应；该页未保存。"

            return [], error

        return self._blocks_from_segments(segments, page_index, page_number, language), None

    def capabilities(self) -> Dict[str, Any]:
        caps = super().capabilities()
        caps["prompt_style"] = "plain_text"
        caps["accepts_json"] = False
        return caps
