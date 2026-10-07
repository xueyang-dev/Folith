"""Negative controls for marker restoration.

An acceptance check that passes for every rendering proves nothing.  Each test
here deliberately breaks one property of the restored marker and asserts that a
detector notices, so the acceptance numbers in the renderer report have real
detection power behind them.
"""
from __future__ import annotations

import pathlib
import sys

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith import pdfrender  # noqa: E402
from folith.pdfrender import typeset  # noqa: E402

from pdfrender_marker_restoration_test import _mark, _pdf, _state  # noqa: E402


def _spans(pdf_bytes, page_index=0):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        return [span for block in page.get_text("dict")["blocks"]
                if block.get("type") == 0
                for line in block["lines"] for span in line["spans"]
                if span["text"].strip()]
    finally:
        doc.close()


def _chars(pdf_bytes, page_index=0):
    """``(char, span_size, origin, bbox)`` for every glyph in the output."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        out = []
        for block in page.get_text("rawdict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    for char in span["chars"]:
                        out.append((char["c"], span["size"],
                                    tuple(char["origin"]), list(char["bbox"])))
        return out
    finally:
        doc.close()


def _digit_char(pdf_bytes, digit="1", page_index=0):
    """The smallest glyph for ``digit`` — a superscript is the small one."""
    hits = [item for item in _chars(pdf_bytes, page_index) if item[0] == digit]
    return min(hits, key=lambda item: item[1]) if hits else None


def _source_line_box(source_bytes, page_index=0):
    doc = pymupdf.open(stream=source_bytes, filetype="pdf")
    try:
        return list(doc[page_index].get_text("dict")["blocks"][0]["lines"][0]["bbox"])
    finally:
        doc.close()


def test_nc1_disabling_restoration_is_detected():
    """Without the provenance the mark is simply gone, and the report says so."""
    source = _pdf(["The sentence ends here."])
    rect = _source_line_box(source)
    restored = pdfrender.render_original_layout(
        "nc1", _state(source, [("The sentence ends here.", "句子到此结束。",
                                [_mark("1", rect)])]),
        source, use_cache=False)
    assert _digit_char(restored.data) is not None

    disabled = pdfrender.render_original_layout(
        "nc1", _state(source, [("The sentence ends here.", "句子到此结束。")]),
        source, use_cache=False)
    assert _digit_char(disabled.data) is None
    assert disabled.report["markers"]["restored_marks"] == 0
    # The check that matters: the two renders are distinguishable.
    assert (restored.report["markers"]["restored_marks"]
            != disabled.report["markers"]["restored_marks"])


def test_nc2_semantic_anchor_beats_the_source_bbox():
    """A reflowed translation must not drag the mark to its old coordinates."""
    source = _pdf(["The quick brown fox jumps over the lazy dog near the river."])
    rect = _source_line_box(source)
    long_target = ("敏捷的棕色狐狸跳过了河边那只懒狗。"
                   "这句话明显比原文更长，需要在槽位内换行。")
    state = _state(source, [(
        "The quick brown fox jumps over the lazy dog near the river.",
        long_target, [_mark("1", rect)])])
    result = pdfrender.render_original_layout("nc2", state, source, use_cache=False)

    digit = _digit_char(result.data)
    assert digit is not None
    assert result.report["pages"][0]["overflow"] == 0
    # The translation reflowed past the source line's end; absolute-bbox
    # placement would leave the mark at the source box's left edge, while the
    # semantic anchor carries it to wherever the translated sentence ended.
    assert abs(digit[2][0] - rect[0]) > 20.0


def test_nc3_full_size_marker_is_clamped_not_trusted():
    """A source claiming a body-sized mark must not produce a body-sized 'super'."""
    source = _pdf(["The sentence ends here."])
    rect = _source_line_box(source)
    wrong = dict(_mark("1", rect), size=9.0, body_size=9.0)
    result = pdfrender.render_original_layout(
        "nc3", _state(source, [("The sentence ends here.", "句子到此结束。",
                                [wrong])]),
        source, use_cache=False)
    digit = _digit_char(result.data)
    body = max(_spans(result.data), key=lambda span: span["size"])
    assert digit is not None
    # 1.0 would be a full-size digit pretending to be a superscript; the clamp
    # holds it down to the marker scale ceiling.
    assert digit[1] <= body["size"] * typeset.MARKER_SCALE_MAX + 0.01
    assert digit[1] < body["size"] * 0.95


def test_nc4_wrong_baseline_raise_is_detected():
    source = _pdf(["The sentence ends here."])
    rect = _source_line_box(source)
    state = _state(source, [("The sentence ends here.", "句子到此结束。",
                             [_mark("1", rect)])])
    result = pdfrender.render_original_layout("nc4", state, source, use_cache=False)
    digit = _digit_char(result.data)
    body = max(_spans(result.data), key=lambda span: span["size"])
    assert digit is not None
    # Typst's y axis points down, so a raised mark has the smaller origin.
    rise = body["origin"][1] - digit[2][1]
    assert 0 < rise < body["size"] * 0.9

    # Source-level guard.  An earlier revision emitted a positive baseline with
    # Typst's OpenType superscript feature left on, and the mark rendered as a
    # subscript; this assertion is what catches the sign flipping back.
    block = typeset.Block(x=0, y=0, width=100, height=20, text="Text.", size=10.0,
                          families=["Liberation Serif"],
                          marks=[{"text": "1", "size": 5.4, "body_size": 9.0}])
    source_markup = typeset._styled_body(block)
    assert "typographic: false" in source_markup
    assert "baseline: -" in source_markup
