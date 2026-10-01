"""Integration tests for PDF visual source provenance preview and workbench layout."""
from pathlib import Path
import pytest
import fitz
import streamlit as st
from streamlit.testing.v1 import AppTest

import app
import core
from folith import pdf_preview


@pytest.fixture
def mock_pdf_workspace(tmp_path: Path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path

    job_id = "test-pdf-ui-workspace"
    doc = fitz.open()
    p1 = doc.new_page(width=400, height=600)
    p1.insert_text(fitz.Point(50, 100), "Title on Page One", fontsize=16)

    p2 = doc.new_page(width=400, height=600)
    p2.insert_text(fitz.Point(50, 100), "First segment text on Page Two", fontsize=12)
    p2.insert_text(fitz.Point(50, 150), "Second segment text on Page Two", fontsize=12)

    core.save_source(job_id, doc.tobytes())
    doc.close()

    state = core.new_job_state("book.pdf")
    state["paras"] = [
        "Title on Page One",
        "First segment text on Page Two",
        "Second segment text on Page Two",
    ]
    state["pairs"] = [
        {"source": "Title on Page One", "target": "第一页标题"},
        {"source": "First segment text on Page Two", "target": "第二页第一段"},
        {"source": "Second segment text on Page Two", "target": "第二页第二段"},
    ]
    core.save_job_state(job_id, state)

    try:
        yield job_id, state
    finally:
        core.OUTPUT_DIR = old_output


def test_pdf_preview_has_source(mock_pdf_workspace):
    job_id, _ = mock_pdf_workspace
    assert pdf_preview.has_pdf_source(job_id) is True
    assert pdf_preview.get_pdf_page_count(job_id) == 2


def test_pdf_preview_finds_page_and_renders(mock_pdf_workspace):
    job_id, state = mock_pdf_workspace

    # Query text on Page 2 (0-indexed: 1)
    page_idx, rects = pdf_preview.find_segment_page_and_rects(
        job_id, "First segment text on Page Two", start_page_hint=0
    )
    assert page_idx == 1
    assert len(rects) >= 1

    png_bytes = pdf_preview.render_pdf_page_image(job_id, page_idx, highlight_rects=rects)
    assert png_bytes is not None
    assert png_bytes.startswith(b"\x89PNG")


def test_pdf_preview_panel_state_and_close_control(mock_pdf_workspace):
    job_id, _ = mock_pdf_workspace
    preview_key = f"pdf_preview_open_{job_id}"
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=40)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception

    assert at.session_state[preview_key] is False, "PDF 原页默认收起，把宽度留给编辑器"
    assert not any(button.key == f"pdf_prev_btn_{job_id}" for button in at.button)
    # The old toolbar dropdown toggle was removed. Opening is now owned by
    # contextual source-quality/PDF navigation; set the persisted state here to
    # exercise the same panel contract without recreating a duplicate CTA.
    at.session_state[preview_key] = True
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[preview_key] is True
    assert any(button.key == f"pdf_prev_btn_{job_id}" for button in at.button), \
        "打开 PDF 原页后必须保留独立分页控件"

    next(button for button in at.button
         if button.key == f"btn_close_pdf_preview_{job_id}").click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[preview_key] is False
    assert not any(button.key == f"pdf_prev_btn_{job_id}" for button in at.button)


def test_source_quality_alert_filters_and_opens_the_flagged_pdf_page(
        mock_pdf_workspace, monkeypatch):
    """OCR 入口先按问题过滤，再定位真实 PDF 段落与页码。"""
    job_id, _state = mock_pdf_workspace
    monkeypatch.setattr(core, "_audit_source_quality_for_state", lambda state: {
        "version": 1, "status": "blocked", "checked_count": 3,
        "flagged_count": 1,
        "flags": [{"segment_index": 1,
                   "text": "First segment text on Page Two",
                   "reasons": ["fixture OCR fragment"]}],
        "clusters": [],
    })
    app_path = Path(__file__).resolve().parent.parent / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=50)
    at.run()
    at.session_state["active_job_id"] = job_id
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "translation"
    at.run()
    assert not at.exception, at.exception
    page = "\n".join(str(item.value) for item in at.markdown)
    assert 'class="tp-source-quality-alert"' in page

    quality_chip = f"translation_issue_chip_translation_quality_filter_{job_id}"
    next(button for button in at.button if button.key == quality_chip).click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[f"translation_quality_filter_{job_id}"] is True
    assert not any('class="tp-source-quality-alert"' in str(item.value)
                   for item in at.markdown), \
        "进入原文质量筛选后应隐藏重复的 OCR 提醒"
    assert [button.key for button in at.button
            if str(button.key).startswith(f"cat_sel_{job_id}_")] == [
                f"cat_sel_{job_id}_1"]

    next(button for button in at.button if button.key == quality_chip).click()
    at.run()
    next(button for button in at.button
         if button.key == f"cat_source_pdf_{job_id}_1").click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state[f"pdf_preview_open_{job_id}"] is True
    assert at.session_state["selected_segment_id"] in {
        "1", "seg-test-pdf-ui-workspace-0001"}
    assert at.session_state[f"pdf_preview_page_{job_id}"] == 1, \
        "OCR 原文入口必须同步到对应的 PDF 第 2 页"
    assert any(button.key == f"pdf_prev_btn_{job_id}" for button in at.button)


def test_navigate_to_segment_syncs_pdf_page(mock_pdf_workspace):
    job_id, state = mock_pdf_workspace
    st.session_state[f"pdf_preview_open_{job_id}"] = True
    st.session_state[f"pdf_preview_page_{job_id}"] = 0

    # Navigate to segment index 1 (which is on Page 2, 0-indexed: 1)
    success = app._navigate_to_segment(job_id, state, 1, reveal=False)
    assert success is True

    # PDF preview highlight should be set to index 1
    assert st.session_state[f"pdf_preview_hl_seg_{job_id}"] == 1
    # PDF preview page should be synced to page index 1
    assert st.session_state[f"pdf_preview_page_{job_id}"] == 1
