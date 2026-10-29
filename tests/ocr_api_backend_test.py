"""Unit tests for folith/ocr_api_backend.py (#261).

Covers:
- detect_repetition on a representative looped sample (single-item loop)
- detect_repetition on a representative block-loop sample
- No false positive on legitimate short repeated lines (TOC, page numbers, ruby)
- decode_ocr_api_response: paragraphs, single-line fallback, markdown stripping, empty error
- Prompt selection: PaddleOCR-VL gets "OCR:", other models get the generic prompt
- resolve_backend("ocr_api") without a config raises OCRUnavailable("unconfigured")
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.ocr_api_backend import (
    detect_repetition,
    decode_ocr_api_response,
    OCR_API_USER_PROMPT_DEFAULT,
    OCR_API_USER_PROMPT_PADDLE,
    _is_paddle_family,
)


# ---------------------------------------------------------------------------
# Fixtures — inline text, not read from the user's data directory
# ---------------------------------------------------------------------------

# Representative single-item instruction loop that PaddleOCR-VL produces when
# the model repeats its own task prefix.  Copied from an observed failure
# pattern: the numbered prompt "Read all content on this page." repeated >20 ×.
_SINGLE_ITEM_LOOP = "\n".join(
    [f"{i}. Read all content on this page." for i in range(1, 25)]
)

# Representative block loop: a 4-line block repeated 8 times (32 lines total).
# Each line appears 8/32 = 25 % of the time, which is below the 30 % single-item
# threshold, so only the block-repetition path fires.
_BLOCK_LOOP = "\n".join(
    [
        line
        for _ in range(8)
        for line in (
            "Alpha line — first in block.",
            "Beta line — second in block.",
            "Gamma line — third in block.",
            "Delta line — fourth in block.",
        )
    ]
)

# A TOC with short lines that legitimately repeat patterns (page numbers, section
# markers) but must NOT trigger the repetition guard.
_TOC_NORMAL = """\
はじめに
1
第一章　序論
3
第二章　先行研究
7
第三章　本研究の方法
12
第四章　分析
18
第五章　考察
25
おわりに
30
参考文献
32
はじめに補足
33
付録　おわりに
34
"""

# A page that has a few repeated short markers (ruby / footnote indicators)
# followed by substantive text — should NOT trigger.
_RUBY_PAGE = """\
第一節

※注１　本稿は…

本文段落一。

※注２　参照…

本文段落二。

第二節

※注１　本稿は…
※注２　参照…
本文段落三。
第三節
"""

# A page where the most-repeated line appears exactly 5 times — below the threshold of 6.
# Use very different lines for the rest so none cross the threshold.
_BELOW_THRESHOLD = "\n".join(
    ["はじめに"] * 5
    + [f"章節{i}の内容がここに入ります。このテキストは一意です。" for i in range(1, 20)]
)


# ---------------------------------------------------------------------------
# detect_repetition — looped samples
# ---------------------------------------------------------------------------

class TestDetectRepetition:

    def test_single_item_loop_detected(self):
        result, looped = detect_repetition(_SINGLE_ITEM_LOOP)
        assert looped, "Numbered instruction loop should be detected as a repetition"
        # Only the first occurrence should be returned (sans the list number prefix).
        assert "Read all content on this page." in result
        assert len(result) < len(_SINGLE_ITEM_LOOP) / 3

    def test_single_item_loop_keeps_first_occurrence(self):
        # The de-numbered content is: "Read all content on this page."
        result, looped = detect_repetition(_SINGLE_ITEM_LOOP)
        assert looped
        # Must not contain the full sequence
        assert result.count("Read all content on this page.") == 1

    def test_block_loop_detected(self):
        result, looped = detect_repetition(_BLOCK_LOOP)
        assert looped, "Block loop should be detected"
        # Only the first block (4 lines) should be returned
        assert "Alpha line" in result
        assert "Beta line" in result
        assert "Gamma line" in result
        assert "Delta line" in result
        assert result.count("Alpha line") == 1

    def test_toc_not_a_loop(self):
        _, looped = detect_repetition(_TOC_NORMAL)
        assert not looped, (
            "A table of contents with short repeated markers should not trigger the guard"
        )

    def test_ruby_page_not_a_loop(self):
        _, looped = detect_repetition(_RUBY_PAGE)
        assert not looped, (
            "A page with footnote markers and substantive text should not trigger the guard"
        )

    def test_below_threshold_not_a_loop(self):
        """5 repetitions is below the minimum threshold of 6; must pass through."""
        _, looped = detect_repetition(_BELOW_THRESHOLD)
        assert not looped

    def test_short_text_not_a_loop(self):
        """Fewer than _REPEAT_MIN_COUNT lines → always pass through unchanged."""
        short = "Line one.\nLine two.\nLine three."
        result, looped = detect_repetition(short)
        assert not looped
        assert result == short

    def test_normal_page_untouched(self):
        normal = (
            "第一段落　本研究の目的は…\n\n"
            "第二段落　先行研究によれば…\n\n"
            "第三段落　本稿の構成は…\n\n"
            "第四段落　次章では…\n"
        )
        result, looped = detect_repetition(normal)
        assert not looped
        assert result == normal


# ---------------------------------------------------------------------------
# decode_ocr_api_response
# ---------------------------------------------------------------------------

class TestDecodeOcrApiResponse:

    def test_blank_line_split_into_paragraphs(self):
        raw = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        segs = decode_ocr_api_response(raw)
        assert len(segs) == 3
        assert segs[0]["text"] == "Paragraph one."
        assert segs[1]["text"] == "Paragraph two."
        assert segs[2]["text"] == "Paragraph three."

    def test_single_line_per_segment_when_no_blank_lines(self):
        raw = "Line one.\nLine two.\nLine three."
        segs = decode_ocr_api_response(raw)
        assert len(segs) == 3
        texts = [s["text"] for s in segs]
        assert "Line one." in texts
        assert "Line two." in texts

    def test_segments_have_required_keys(self):
        segs = decode_ocr_api_response("Hello.\n\nWorld.")
        for seg in segs:
            assert "index" in seg
            assert "text" in seg
            assert "break_after" in seg
            assert seg["break_after"] is True

    def test_segments_indexed_sequentially(self):
        raw = "A.\n\nB.\n\nC."
        segs = decode_ocr_api_response(raw)
        assert [s["index"] for s in segs] == [0, 1, 2]

    def test_markdown_code_fence_stripped(self):
        raw = "```\nParagraph one.\n\nParagraph two.\n```"
        segs = decode_ocr_api_response(raw)
        assert len(segs) == 2
        assert segs[0]["text"] == "Paragraph one."

    def test_markdown_code_fence_with_language_stripped(self):
        raw = "```text\nLine one.\nLine two.\n```"
        segs = decode_ocr_api_response(raw)
        assert len(segs) >= 1

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="empty_response"):
            decode_ocr_api_response("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="empty_response"):
            decode_ocr_api_response("   \n\n   ")

    def test_only_code_fence_raises_value_error(self):
        with pytest.raises(ValueError, match="empty_response|empty_transcription"):
            decode_ocr_api_response("```\n```")

    def test_leading_trailing_whitespace_trimmed(self):
        raw = "\n\n  Hello world.  \n\n"
        segs = decode_ocr_api_response(raw)
        assert len(segs) == 1
        assert segs[0]["text"] == "Hello world."


# ---------------------------------------------------------------------------
# Prompt selection: PaddleOCR-VL vs. other models
# ---------------------------------------------------------------------------

class TestPromptSelection:

    @pytest.mark.parametrize("model_name", [
        "PaddlePaddle/PaddleOCR-VL-1.5",
        "paddleocr-vl",
        "my/paddle-ocr-model",
        "custom_paddle_ocr_v2",
    ])
    def test_paddle_family_uses_native_prompt(self, model_name):
        assert _is_paddle_family(model_name), f"Expected {model_name!r} to be Paddle family"

    @pytest.mark.parametrize("model_name", [
        "qwen2.5-vl",
        "gpt-4o",
        "stepfun-ai/GOT-OCR2_0",
        "olmOCR-7B",
        "glm-4v",
        "",
    ])
    def test_non_paddle_uses_default_prompt(self, model_name):
        assert not _is_paddle_family(model_name), (
            f"Expected {model_name!r} to NOT be Paddle family"
        )

    def test_paddle_prompt_is_ocr_colon(self):
        assert OCR_API_USER_PROMPT_PADDLE == "OCR:"

    def test_default_prompt_is_transcription_instruction(self):
        assert "transcribe" in OCR_API_USER_PROMPT_DEFAULT.lower()
        assert "OCR:" not in OCR_API_USER_PROMPT_DEFAULT


# ---------------------------------------------------------------------------
# resolve_backend("ocr_api") without a config → OCRUnavailable("unconfigured")
# ---------------------------------------------------------------------------

class TestResolveBackendOcrApi:

    def test_no_config_raises_ocr_unavailable(self):
        from folith.ocr_backends import resolve_backend, OCRUnavailable
        with pytest.raises(OCRUnavailable) as exc_info:
            resolve_backend("ocr_api")
        # OCRUnavailable(code, message) stores code in .code, message in args[0]
        assert exc_info.value.code == "unconfigured", (
            f"Expected code='unconfigured', got {exc_info.value.code!r}"
        )

    def test_ocr_api_string_variations_resolve_correctly(self):
        """Alternate spellings must normalise to 'ocr_api', not raise 'unsupported'."""
        from folith.ocr_backends import normalize_backend
        for alias in ("ocr_api", "ocr-api", "ocrapi"):
            assert normalize_backend(alias) == "ocr_api", (
                f"Expected 'ocr_api' for alias {alias!r}"
            )
