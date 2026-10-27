"""术语在正文里的位置：本段标注、悬停高亮、全文定位；前后文只在筛选时出现。"""
from __future__ import annotations

import io
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import term_highlight  # noqa: E402


def test_spans_follow_the_glossary_matching_rules():
    assert term_highlight.spans("art", "Start the art.") == [(10, 13)], "whole words for Latin"
    assert term_highlight.spans("Pixie", "the pixie and PIXIE") == [(4, 9), (14, 19)]
    assert term_highlight.spans("小精灵", "真相小精灵与小精灵") == [(2, 5), (6, 9)]
    assert term_highlight.spans("CAT", "CAT2 CAT_ CAT") == [(10, 13)]
    assert term_highlight.spans("草ABC", "前草ABC后") == [(1, 5)]
    assert term_highlight.spans("", "x") == [] and term_highlight.spans("x", "") == []


def _docx(texts):
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture()
def bench(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data, raising=False)
    from folith.qt import adapter, views

    sources = ["The Truth Pixie arrives.", "Nothing here.", "The Truth Pixie leaves.",
               "Again the Truth Pixie."]
    targets = ["真相小精灵来了。", "这里没有。", "真相精灵走了。", "又是真相小精灵。"]
    path = tmp_path / "book.docx"
    path.write_bytes(_docx(sources))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = sources
    state["pairs"] = [{"source": s, "target": t, "segment_uid": f"s{i}"}
                      for i, (s, t) in enumerate(zip(sources, targets))]
    state["glossary"] = [{"id": "g1", "source": "Truth Pixie", "preferred": "真相小精灵",
                          "status": "approved", "forbidden": ["真相精灵"]}]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)
    view = views.WorkbenchView()
    view.resize(1300, 800)
    view.show()
    view.load(job_id, config, adapter.load_state(job_id), adapter.segment_rows(job_id), [])
    app.processEvents()
    yield view, app
    view.hide()
    view.deleteLater()
    app.processEvents()


def _pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


def test_chip_shows_document_count_and_insert_is_a_separate_button(bench):
    view, app = bench
    view.select_segment(0)
    _pump(app)
    assert view.term_heading.text() == "术语 · 1"
    chip = view.term_list.buttons[0]
    assert chip.count_label.text() == "本文 3 处"
    inserted = []
    view.term_list.insertRequested.connect(inserted.append)
    chip.insert_button.click()
    assert inserted == ["真相小精灵"]


def test_current_row_marks_terms_and_hover_highlights_the_pair(bench):
    view, app = bench
    view.select_segment(2)
    _pump(app)
    source_marks = view.inline_editor.source_view.extraSelections()
    assert len(source_marks) == 1
    target_marks = view.target_edit.extraSelections()
    # 第 3 段用了禁用译名「真相精灵」：红色波浪线；首选译名没出现，所以没有虚线。
    from PySide6.QtGui import QTextCharFormat

    assert [mark.format.underlineStyle() for mark in target_marks] == [
        QTextCharFormat.UnderlineStyle.WaveUnderline]
    assert view.term_list.terms[0]["missing_preferred"] is True
    view._hover_term(view.term_list.terms[0])
    hovered = view.inline_editor.source_view.extraSelections()[0]
    assert hovered.format.background().color().alpha() > 0
    view._hover_term(None)
    assert view.inline_editor.source_view.extraSelections()[0].format.background().style() == \
        view.inline_editor.source_view.extraSelections()[0].format.background().style().NoBrush


def test_click_locates_every_occurrence_and_marks_inconsistent_ones(bench):
    view, app = bench
    view.select_segment(1)
    _pump(app)
    view.select_segment(0)
    _pump(app)
    view.focus_term(view.term_list.terms[0])
    found = view._term_focus
    assert found.positions == [0, 2, 3]
    assert found.inconsistent == {2}, "segment 3 does not use the preferred translation"
    assert view.term_focus_bar.isVisible()
    assert "第 1 / 3 处" in view.term_focus_label.text()
    assert "1 处没用首选译名" in view.term_focus_label.text()
    assert [tone for _f, tone in view.term_markers.marks] == ["accent", "warning", "accent"]
    view.step_term_focus(1)
    assert view.current_index() == 2
    view.step_term_focus(1)
    view.step_term_focus(1)
    assert view.current_index() == 0, "wraps around"
    view.step_term_focus(-1)
    assert view.current_index() == 3
    assert view.drawer_escape.isEnabled()
    view._escape_pressed()
    assert view._term_focus is None and not view.term_focus_bar.isVisible()
    assert view.term_markers.marks == []


def test_context_only_appears_when_the_grid_is_filtered(bench):
    view, app = bench
    view.select_segment(1)
    _pump(app)
    assert view.context_view.isHidden() and view.context_heading.isHidden()
    view.set_search("Pixie")
    _pump(app)
    view.select_segment(2)
    _pump(app)
    assert not view.context_view.isHidden(), "neighbours are no longer adjacent in the grid"
    view.set_search("")
    _pump(app)
    assert view.context_view.isHidden()


def test_typing_over_a_source_selection_starts_a_question(bench):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view, app = bench
    view.select_segment(0)
    _pump(app)
    source = view.inline_editor.source_view
    cursor = source.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(15, QTextCursor.MoveMode.KeepAnchor)
    source.setTextCursor(cursor)
    source.setFocus()
    QTest.keyClicks(source, "w")   # QTest 无法合成 CJK 按键；真实输入法同样走 KeyPress.text()
    _pump(app)
    assert view.assist_input.text() == "「Truth Pixie」w"
    assert view.window().focusWidget() is view.assist_input
