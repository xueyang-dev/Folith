"""Tests for lazy page-dict cache and the page_text_dict image-byte stripping.

Two things verified here:

1. ``page_text_dict`` — spliced dict equivalence
   A PDF with interleaved text and image blocks must yield the same block
   list (type, bbox, enumeration position) from ``page_text_dict`` as from
   the full ``page.get_text("dict")``, with no image bytes in the result.

2. Lazy cache — scanned/rejected PDFs pay zero extra parsing
   For a PDF whose text layer is absent (auto-mode), ``build_page_dict_cache``
   must never be called: the lazy holder is created but no consumer iterates
   it so the underlying build never runs.
"""
from __future__ import annotations

import io
from unittest.mock import patch

import fitz
import pytest

import core
from folith import pdf_ingestion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _interleaved_pdf() -> bytes:
    """Return a PDF with interleaved text and image blocks on one page.

    Block order:  text → image → text → image → text
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "First text block.", fontsize=12)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
    pix.set_rect(pix.irect, (255, 0, 0))
    page.insert_image(fitz.Rect(100, 150, 200, 250), pixmap=pix)
    page.insert_text((72, 300), "Second text block.", fontsize=12)
    pix2 = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
    pix2.set_rect(pix2.irect, (0, 255, 0))
    page.insert_image(fitz.Rect(100, 350, 200, 450), pixmap=pix2)
    page.insert_text((72, 500), "Third text block.", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _text_only_pdf(text: str = "Hello world. This is a test.") -> bytes:
    """Return a minimal PDF with a single text page (no images)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), text, fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _image_only_pdf(n_pages: int = 1) -> bytes:
    """Return a minimal image-only (scanned) PDF with *n_pages* pages."""
    doc = fitz.open()
    for _ in range(n_pages):
        page = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
        pix.set_rect(pix.irect, (200, 200, 200))
        page.insert_image(page.rect, pixmap=pix)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# page_text_dict — spliced dict equivalence
# ---------------------------------------------------------------------------

def test_page_text_dict_matches_full_dict_block_types_and_bboxes():
    """Spliced dict has the same block list as the full dict (type and bbox)."""
    pdf_bytes = _interleaved_pdf()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        full = page.get_text("dict")
        spliced = pdf_ingestion.page_text_dict(page)

    full_blocks = full.get("blocks", [])
    spl_blocks = spliced.get("blocks", [])

    assert len(full_blocks) == len(spl_blocks), (
        f"block count mismatch: full={len(full_blocks)}, spliced={len(spl_blocks)}"
    )
    for i, (fb, sb) in enumerate(zip(full_blocks, spl_blocks)):
        assert fb["type"] == sb["type"], (
            f"block {i} type mismatch: full={fb['type']}, spliced={sb['type']}"
        )
        fb_bbox = tuple(round(x, 3) for x in fb["bbox"])
        sb_bbox = tuple(round(x, 3) for x in sb["bbox"])
        assert fb_bbox == sb_bbox, (
            f"block {i} bbox mismatch: full={fb_bbox}, spliced={sb_bbox}"
        )


def test_page_text_dict_no_image_bytes_in_image_blocks():
    """Image blocks in the spliced dict must not carry PNG bytes."""
    pdf_bytes = _interleaved_pdf()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        spliced = pdf_ingestion.page_text_dict(page)

    image_blocks = [b for b in spliced.get("blocks", []) if b.get("type") == 1]
    assert image_blocks, "expected at least one image block"
    for i, b in enumerate(image_blocks):
        assert not b.get("image"), (
            f"image block {i} still has embedded PNG bytes"
        )


def test_page_text_dict_enumeration_positions_match_full_dict():
    """Enumeration index of each block must match the full dict's ordering.

    ``extract_layout_blocks`` uses ``for raw_number, raw in enumerate(blocks)``
    to build block IDs like ``p1-b0``, ``p1-b1``, etc.  The spliced dict
    must preserve the same position for each block.
    """
    pdf_bytes = _interleaved_pdf()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        full = page.get_text("dict")
        spliced = pdf_ingestion.page_text_dict(page)

    for i, (fb, sb) in enumerate(
        zip(full.get("blocks", []), spliced.get("blocks", []))
    ):
        assert fb["type"] == sb["type"], (
            f"position {i}: type mismatch (full={fb['type']}, spliced={sb['type']})"
        )


def test_page_text_dict_text_only_page_unchanged():
    """For a page with no images the result should match the full dict."""
    pdf_bytes = _text_only_pdf()
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        full = page.get_text("dict")
        spliced = pdf_ingestion.page_text_dict(page)

    # No images — the spliced dict is the text-only dict returned unchanged
    assert len(spliced.get("blocks", [])) == len(full.get("blocks", []))
    for fb, sb in zip(full.get("blocks", []), spliced.get("blocks", [])):
        assert fb["type"] == sb["type"]
        assert fb["bbox"] == sb["bbox"]


def test_extract_layout_blocks_produces_same_block_ids_with_interleaved_pdf():
    """extract_layout_blocks on a PDF with images must produce the same
    ``block_id`` values as before — the page_text_dict splice must not
    shift enumeration indices.
    """
    pdf_bytes = _interleaved_pdf()
    blocks = pdf_ingestion.classify_blocks(
        pdf_ingestion.extract_layout_blocks(pdf_bytes)
    )
    # Image blocks must have block IDs that reflect their original position
    # (not re-indexed as if image blocks were absent).
    block_ids = [b.get("block_id") for b in blocks]
    # The interleaved PDF has 5 blocks: text(b0), image(b1), text(b2),
    # image(b3), text(b4) — images are at b1 and b3.
    assert "p1-b1" in block_ids, "image block at position 1 must be p1-b1"
    assert "p1-b3" in block_ids, "image block at position 3 must be p1-b3"


# ---------------------------------------------------------------------------
# Lazy cache — scanned/rejected PDFs pay zero extra parsing
# ---------------------------------------------------------------------------

def test_lazy_cache_does_not_build_until_iterated(monkeypatch):
    """Creating _LazyPDFPageCache must not trigger build_page_dict_cache."""
    built = []
    original = pdf_ingestion.build_page_dict_cache

    def tracking(fb):
        built.append(1)
        return original(fb)

    monkeypatch.setattr(pdf_ingestion, "build_page_dict_cache", tracking)

    cache = core._LazyPDFPageCache(_text_only_pdf())
    assert built == [], "build_page_dict_cache should not be called on construction"

    _ = list(cache)
    assert len(built) == 1, "should be called exactly once on first iteration"

    _ = list(cache)
    assert len(built) == 1, "should NOT be called again on second iteration"


def test_lazy_cache_release_clears_data():
    """After release() the lazy cache data and file_bytes are cleared."""
    cache = core._LazyPDFPageCache(_text_only_pdf())
    _ = list(cache)                      # materialise
    assert cache._data is not None
    cache.release()
    assert cache._data is None
    assert cache._file_bytes is None
    assert not bool(cache)


def test_image_only_pdf_does_not_call_build_page_dict_cache(monkeypatch):
    """build_page_dict_cache must never be called for an image-only PDF
    on the ocr_mode=auto path (text layer absent → use_text_layer=False).

    The lazy holder is created but no consumer iterates it, so the
    underlying build never runs — identical cost to origin/main.
    """
    pdf_bytes = _image_only_pdf(n_pages=2)

    built = []
    original = pdf_ingestion.build_page_dict_cache

    def tracking(fb):
        built.append(1)
        return original(fb)

    monkeypatch.setattr(pdf_ingestion, "build_page_dict_cache", tracking)

    # ocr_mode="auto": text layer is absent → use_text_layer=False.
    # After the text-layer check the VLM path is triggered; _vlm_runtime
    # raises ValueError when no online config is configured (expected in CI).
    try:
        core.extract_document_paragraphs_with_report(
            "scan.pdf", pdf_bytes, ocr_mode="auto", ocr_max_pages=0)
    except (ValueError, RuntimeError):
        pass  # VLM provider creation fails without credentials — expected

    assert built == [], (
        f"build_page_dict_cache was called {len(built)} time(s) for an "
        "image-only PDF; expected 0 on the rejected-layer path"
    )
