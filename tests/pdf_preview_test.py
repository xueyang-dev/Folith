"""Tests for PDF visual preview and segment source provenance tracking."""
from pathlib import Path
import pytest
import fitz

import core
from folith import pdf_preview


@pytest.fixture
def sample_pdf_job(tmp_path: Path, monkeypatch):
    """Create a temporary job directory with a multi-page sample PDF."""
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path

    job_id = "test-pdf-preview-001"
    doc = fitz.open()

    # Page 1 (0-indexed)
    p1 = doc.new_page(width=400, height=600)
    p1.insert_text(fitz.Point(50, 100), "The Midnight Library", fontsize=16)
    p1.insert_text(fitz.Point(50, 150), "By Matt Haig", fontsize=12)

    # Page 2 (1-indexed)
    p2 = doc.new_page(width=400, height=600)
    p2.insert_text(fitz.Point(50, 100), "Between life and death there is a library.", fontsize=12)
    p2.insert_text(fitz.Point(50, 140), "And within that library, the shelves go on for ever.", fontsize=12)

    # Page 3 (2-indexed)
    p3 = doc.new_page(width=400, height=600)
    p3.insert_text(fitz.Point(50, 100), "A Conversation About Rain", fontsize=14)
    p3.insert_text(fitz.Point(50, 140), "Nora Seed sat in the warmth of the small library.", fontsize=12)

    pdf_bytes = doc.tobytes()
    doc.close()

    core.save_source(job_id, pdf_bytes)
    state = core.new_job_state("sample.pdf")
    state["paras"] = [
        "The Midnight Library",
        "Between life and death there is a library.",
        "Nora Seed sat in the warmth of the small library.",
    ]
    core.save_job_state(job_id, state)

    try:
        yield job_id, tmp_path
    finally:
        core.OUTPUT_DIR = old_output


def test_has_pdf_source(sample_pdf_job):
    job_id, _ = sample_pdf_job
    assert pdf_preview.has_pdf_source(job_id) is True
    assert pdf_preview.has_pdf_source("non-existent-job") is False


def test_get_pdf_page_count(sample_pdf_job):
    job_id, _ = sample_pdf_job
    assert pdf_preview.get_pdf_page_count(job_id) == 3
    assert pdf_preview.get_pdf_page_count("non-existent-job") == 0


def test_find_segment_page_and_rects(sample_pdf_job):
    job_id, _ = sample_pdf_job

    # Search for text on Page 1 (0-indexed)
    page_idx, rects = pdf_preview.find_segment_page_and_rects(job_id, "The Midnight Library")
    assert page_idx == 0
    assert len(rects) >= 1
    assert len(rects[0]) == 4  # [x0, y0, x1, y1]

    # Search for text on Page 2 (0-indexed: 1)
    page_idx, rects = pdf_preview.find_segment_page_and_rects(
        job_id, "Between life and death there is a library."
    )
    assert page_idx == 1
    assert len(rects) >= 1

    # Search for text on Page 3 (0-indexed: 2)
    page_idx, rects = pdf_preview.find_segment_page_and_rects(
        job_id, "Nora Seed sat in the warmth"
    )
    assert page_idx == 2
    assert len(rects) >= 1

    # Search for non-existent text
    page_idx, rects = pdf_preview.find_segment_page_and_rects(job_id, "Completely unknown text xyz")
    assert page_idx is None
    assert rects == []


def test_render_pdf_page_image_and_cache(sample_pdf_job):
    job_id, _ = sample_pdf_job

    # Render without highlight
    png_bytes = pdf_preview.render_pdf_page_image(job_id, 0, dpi=100)
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG")

    # Render with highlight rects
    _, rects = pdf_preview.find_segment_page_and_rects(job_id, "Between life and death")
    highlighted_png = pdf_preview.render_pdf_page_image(
        job_id, 1, highlight_rects=rects, dpi=100
    )
    assert highlighted_png is not None
    assert highlighted_png.startswith(b"\x89PNG")

    # Second render should hit the in-memory cache
    cached_png = pdf_preview.render_pdf_page_image(
        job_id, 1, highlight_rects=rects, dpi=100
    )
    assert cached_png == highlighted_png

    # Out-of-bounds page returns None
    assert pdf_preview.render_pdf_page_image(job_id, 999) is None
    assert pdf_preview.render_pdf_page_image(job_id, -1) is None


def test_get_cached_segment_location(sample_pdf_job):
    job_id, _ = sample_pdf_job
    loc1 = pdf_preview.get_cached_segment_location(
        job_id, 0, "The Midnight Library", start_page_hint=0
    )
    assert loc1[0] == 0
    # Second call should return cached value
    loc2 = pdf_preview.get_cached_segment_location(
        job_id, 0, "The Midnight Library", start_page_hint=0
    )
    assert loc1 == loc2


def test_render_pdf_page_image_prerenders_adjacent_pages(sample_pdf_job):
    job_id, _ = sample_pdf_job
    pdf_preview._IMAGE_CACHE.clear()

    # Render Page 1 (middle page of 3-page document)
    _ = pdf_preview.render_pdf_page_image(job_id, 1, dpi=100)

    # Adjacent pages 0 and 2 should have been pre-rendered into cache
    assert (job_id, 0, (), 100) in pdf_preview._IMAGE_CACHE
    assert (job_id, 2, (), 100) in pdf_preview._IMAGE_CACHE
