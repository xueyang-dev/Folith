"""PDF 原页对照：把译文段落对回原页并高亮。

* 只有 PDF 任务才有「原页」页签与「查看原位置」按钮；
* 定位用核心的 ``pdf_preview``（持久化锚点优先，旧任务文本搜索）；
* 页面真的被渲染出来（PNG 能解码、有内容），高亮的页与没有坐标的页都如实说明；
* 找不到位置、没有原文、页越界都不崩溃。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
fitz = pytest.importorskip("fitz")

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402

PAGE_ONE = "The quick brown fox jumps over the lazy dog near the river bank."
PAGE_TWO = "Translation memory keeps reviewed segments for later reuse in projects."


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    for text in (PAGE_ONE, PAGE_TWO):
        page = doc.new_page()
        page.insert_text((72, 100), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def pdf_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "pdfpanel00000001"
    state = core.new_job_state("paper.pdf")
    state.update(p1_done=True, p2_done=True, paras=[PAGE_ONE, PAGE_TWO],
                 pairs=[{"source": PAGE_ONE, "target": "敏捷的棕色狐狸。"},
                        {"source": PAGE_TWO, "target": "翻译记忆保存已审校的段落。"}])
    core.save_job_state(job_id, state)
    core.save_source(job_id, _pdf_bytes())
    return job_id


@pytest.fixture()
def docx_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "pdfpanel00000002"
    state = core.new_job_state("note.docx")
    state.update(p1_done=True, paras=["a"], pairs=[{"source": "a", "target": "甲"}])
    core.save_job_state(job_id, state)
    core.save_source(job_id, b"PK\x03\x04not a pdf")
    return job_id


def test_only_pdf_tasks_have_an_original_page(pdf_job, docx_job):
    assert adapter.pdf_available(pdf_job) is True
    assert adapter.pdf_available(docx_job) is False
    assert adapter.pdf_available("") is False


def test_page_count_and_rendering(pdf_job):
    assert adapter.pdf_page_count(pdf_job) == 2
    png = adapter.pdf_page_png(pdf_job, 0)
    assert png and png.startswith(b"\x89PNG")
    assert adapter.pdf_page_png(pdf_job, 99) is None, "页越界不能崩溃"
    highlighted = adapter.pdf_page_png(pdf_job, 0, [[60, 85, 400, 110]])
    assert highlighted != png, "高亮必须真的画在页上"


def test_segment_location_finds_the_right_page(pdf_job):
    first = adapter.segment_pdf_location(pdf_job, 0)
    second = adapter.segment_pdf_location(pdf_job, 1)
    assert first["page"] == 0 and second["page"] == 1
    assert first["rects"], "文本层 PDF 有行级坐标，应当高亮"
    assert "高亮" in first["hint"]


def test_segment_without_source_or_match_says_so(pdf_job):
    state = adapter.load_state(pdf_job)
    state["pairs"].append({"source": "zzzz qqqq nothing like this text xxxx", "target": "?"})
    state["paras"].append("zzzz qqqq nothing like this text xxxx")
    core.save_job_state(pdf_job, state)
    missing = adapter.segment_pdf_location(pdf_job, 2)
    assert missing["page"] is None and "没能" in missing["hint"]
    assert adapter.segment_pdf_location(pdf_job, 50)["page"] is None


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_shows_pages_and_navigates(qapp, pdf_job):
    from folith.qt.pdf_panel import PdfPanel

    panel = PdfPanel()
    panel.load(pdf_job)
    assert panel.total_label.text() == "/ 2"
    assert panel.image.pixmap() is not None and not panel.image.pixmap().isNull()
    assert not panel.prev_button.isEnabled() and panel.next_button.isEnabled()
    panel.next_button.click()
    assert panel.current_page() == 1 and panel.page_spin.value() == 2
    assert not panel.next_button.isEnabled()
    panel.goto(50)
    assert panel.current_page() == 1, "越界跳转停在最后一页"


def test_panel_locates_and_highlights_a_segment(qapp, pdf_job):
    from folith.qt.pdf_panel import PdfPanel

    panel = PdfPanel()
    panel.load(pdf_job)
    assert not panel.locate_button.isEnabled(), "没有选中段落时不能定位"
    panel.set_segment(1)
    panel.locate_button.click()
    assert panel.current_page() == 1
    assert "第 2 段" in panel.hint.text() and "高亮" in panel.hint.text()
    panel.zoom_box.setCurrentIndex(2)           # 缩放重画不丢高亮页
    assert panel.current_page() == 1


def test_panel_for_a_non_pdf_task_is_empty(qapp, docx_job):
    from folith.qt.pdf_panel import PdfPanel

    panel = PdfPanel()
    panel.load("")
    assert panel.total_label.text() == "/ —" and not panel.page_spin.isEnabled()


def test_workbench_shows_the_original_page_only_for_pdf_tasks(qapp, pdf_job, docx_job):
    from folith.qt import views

    bench = views.WorkbenchView()
    for job_id, expected in ((pdf_job, True), (docx_job, False)):
        state = adapter.load_state(job_id)
        bench.load(job_id, adapter.EngineConfig(target_lang="简体中文"), state,
                   adapter.segment_rows(job_id, state), [])
        # 原页是工具条上的「原页」按钮（左侧展开面板），只有 PDF 任务才有。
        assert (not bench.source_button.isHidden()) is expected
        assert (not bench.view_source_button.isHidden()) is expected
        assert bench.tabs.indexOf(bench.pdf_panel) == -1, "原页不再是整页"


def test_view_source_button_jumps_to_the_segment(qapp, pdf_job):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(pdf_job)
    bench.load(pdf_job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(pdf_job, state), [])
    bench.select_segment(1)
    bench.view_source_button.click()
    # 在工作台左侧展开，网格仍在旁边（acceptance-feedback D2）。
    assert bench.source_open() and bench.source_button.isChecked()
    assert bench.tabs.currentWidget() is bench._pages["translate"]
    assert bench.pdf_panel.current_page() == 1
    bench.pdf_panel.close_button.click()
    assert not bench.source_open() and not bench.source_button.isChecked()
