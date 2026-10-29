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
    _image_is_blank,
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


# ---------------------------------------------------------------------------
# _image_is_blank — blank-page detector (Fix C)
# ---------------------------------------------------------------------------

def _make_png(width: int, height: int, color: tuple) -> bytes:
    """Create a solid-color PNG in memory without writing to disk."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


class TestImageIsBlank:

    def test_all_white_is_blank(self):
        png = _make_png(100, 100, (255, 255, 255))
        assert _image_is_blank(png), "A fully white image should be blank"

    def test_light_gray_is_blank(self):
        # 251/255 is above the 250 threshold → still "white"
        png = _make_png(100, 100, (251, 251, 251))
        assert _image_is_blank(png), "Near-white gray should count as blank"

    def test_dark_text_page_not_blank(self):
        # A page with black text on white: ~50 % dark pixels
        import io
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (200, 200), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 190, 190], fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
        assert not _image_is_blank(png), "A page with a filled black rectangle should not be blank"

    def test_empty_bytes_returns_false(self):
        assert not _image_is_blank(b""), "Unreadable bytes should return False (not crash)"

    def test_invalid_png_returns_false(self):
        assert not _image_is_blank(b"\x89PNG garbage"), "Corrupt PNG should return False"


# ---------------------------------------------------------------------------
# Fix A: truncated + looped response → recovery
# Fix B: truncated without loop → OCR-specific error message
# ---------------------------------------------------------------------------

def _make_ocr_backend():
    """Return a minimal OcrApiBackend without hitting any real API."""
    from folith.ocr_api_backend import OcrApiBackend
    from folith.ocr_online import OnlineOCRConfig
    cfg = OnlineOCRConfig(
        provider="test", model="PaddlePaddle/PaddleOCR-VL-1.5",
        api_key="test-key")
    return OcrApiBackend(cfg)


class TestTruncatedLoopRecovery:
    """Fix A + Fix B: repetition guard applied to finish_reason=length responses."""

    def test_truncated_looped_response_is_recovered(self):
        """When the truncated text contains a repetition loop the page must be
        returned as recognised (no error) with the loop warning set."""
        backend = _make_ocr_backend()
        # Simulate a truncated response whose text is kept in this call's context.
        def fake_read_image(*args, **kwargs):
            kwargs["context"]["response_text"] = _SINGLE_ITEM_LOOP
            return [], "truncated: 视觉理解返回的是纯文本"

        png = _make_png(10, 10, (255, 255, 255))
        from unittest.mock import patch
        with patch.object(backend, "read_image", side_effect=fake_read_image):
            result = backend.recognize_page(
                png, page_index=0, page_number=1, language="zh", dpi=150)
        assert not result.error
        assert result.blocks
        assert any("repetition_detected" in warning for warning in result.warnings)

    def test_truncated_non_looped_response_gives_ocr_message(self):
        """When the truncated text has no repetition, the error must mention
        output limit rather than the VLM-specific 'structured format' message."""
        backend = _make_ocr_backend()
        real_text = "This is a genuine single-occurrence page without a loop."

        def fake_read_image(*args, **kwargs):
            kwargs["context"]["response_text"] = real_text
            return [], "truncated: 视觉理解返回的是纯文本"

        png = _make_png(10, 10, (255, 255, 255))
        from unittest.mock import patch
        with patch.object(backend, "read_image", side_effect=fake_read_image):
            blocks, error = backend.ocr_page(
                png, page_index=0, page_number=1, language="zh", dpi=150)

        assert blocks == []
        assert error is not None
        assert "输出上限" in error or "finish_reason=length" in error, (
            f"OCR truncation error should mention output limit; got: {error!r}"
        )
        assert "视觉理解" not in error, (
            f"OCR truncation error must not reference VLM format; got: {error!r}"
        )

    def test_truncated_no_text_gives_ocr_message(self):
        """Edge case: _last_response_text is empty after truncation."""
        backend = _make_ocr_backend()

        def fake_read_image(*args, **kwargs):
            # Do not set _last_response_text
            return [], "truncated: 线上 OCR 输出被服务截断"

        png = _make_png(10, 10, (255, 255, 255))
        from unittest.mock import patch
        with patch.object(backend, "read_image", side_effect=fake_read_image):
            blocks, error = backend.ocr_page(
                png, page_index=0, page_number=1, language="zh", dpi=150)

        assert blocks == []
        assert error is not None
        assert "输出上限" in error or "finish_reason=length" in error


# ---------------------------------------------------------------------------
# Fix C: empty response on blank page → blank page (no error)
# ---------------------------------------------------------------------------

class TestBlankPageEmptyResponse:

    def test_empty_response_on_blank_image_is_not_an_error(self):
        """A blank-image page that returns an empty OCR response must be recorded
        as a blank page (no error) rather than a failure."""
        backend = _make_ocr_backend()

        def fake_read_image(*args, **kwargs):
            return [], "empty_response: OCR 返回空响应；该页未保存。"

        blank_png = _make_png(200, 200, (255, 255, 255))
        from unittest.mock import patch
        with patch.object(backend, "read_image", side_effect=fake_read_image):
            blocks, error = backend.ocr_page(
                blank_png, page_index=0, page_number=1, language="zh", dpi=150)

        assert error is None, (
            f"A blank page with an empty response must not be an error; got {error!r}"
        )
        assert blocks == [], "A blank page should have no text blocks"

    def test_empty_response_on_real_page_stays_an_error(self):
        """An empty response on a non-blank page must remain a failure."""
        backend = _make_ocr_backend()

        def fake_read_image(*args, **kwargs):
            return [], "empty_response: OCR 返回空响应；该页未保存。"

        text_png = _make_png(200, 200, (0, 0, 0))  # fully black = not blank
        from unittest.mock import patch
        with patch.object(backend, "read_image", side_effect=fake_read_image):
            blocks, error = backend.ocr_page(
                text_png, page_index=0, page_number=1, language="zh", dpi=150)

        assert error is not None, "Empty response on a real page must be an error"
        assert "empty_response" in error
        assert "视觉理解" not in error, (
            f"OCR empty-response error must not reference VLM; got: {error!r}"
        )


def test_repetition_preserves_prefix_and_first_unit():
    prefix = [f'本文 {i}' for i in range(20)]
    text = '\n'.join(prefix + ['以下余白'] * 10)
    assert detect_repetition(text) == ('\n'.join(prefix + ['以下余白']), True)


def test_block_repetition_after_prefix():
    prefix = ['本文その一', '本文その二']
    unit = ['甲', '乙', '丙']
    assert detect_repetition('\n'.join(prefix + unit * 8)) == ('\n'.join(prefix + unit), True)


def test_numbered_repetition_preserves_original_first_line():
    text = '\n'.join(['正文'] + [f'{i}. 读取本页内容。' for i in range(1, 9)])
    assert detect_repetition(text) == ('正文\n1. 读取本页内容。', True)


@pytest.mark.parametrize('compact', [True, False])
def test_chapter_contents_are_not_a_loop(compact):
    lines = []
    for i in range(10):
        lines.extend([f'第{i}章', 'はじめに'] + ([] if compact else [f'一 {i}', f'二 {i}']) + ['おわりに'])
    text = '\n'.join(lines)
    assert detect_repetition(text) == (text, False)


def test_sparse_title_and_blank_scanned_pages():
    import fitz
    from pathlib import Path
    from folith.ocr_api_backend import _image_is_blank
    with fitz.open(Path(__file__).parent / 'fixtures' / 'ocr_sparse_pages.pdf') as pdf:
        title, blank = [page.get_pixmap(dpi=220, alpha=False).tobytes('png') for page in pdf]
    assert not _image_is_blank(title)
    assert _image_is_blank(blank)


def test_concurrent_page_results_keep_their_own_text_and_warnings(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    backend = _make_ocr_backend()
    barrier = Barrier(4)

    def read_image(png, **kwargs):
        page = int(png.decode())
        raw = f'正文 page {page}\n' + ('繰り返し\n' * 8 if page == 1 else '本文です')
        context = kwargs.get('context')
        if context is None:
            backend._last_response_text = raw
        else:
            context['response_text'] = raw
            context['request'] = {'page': page}
        barrier.wait(timeout=5)
        if page == 1:
            return [], 'truncated: length'
        return kwargs['decoder'](raw), None

    monkeypatch.setattr(backend, 'read_image', read_image)
    def recognize(page):
        return backend.recognize_page(str(page).encode(), page_index=page-1,
                                      page_number=page, language='jpn', dpi=220)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(recognize, range(1, 5)))
    for page, result in enumerate(results, 1):
        assert not result.error
        assert f'正文 page {page}' in ' '.join(block.text for block in result.blocks)
        assert bool(result.warnings) == (page == 1)
        assert result.request['page'] == page
