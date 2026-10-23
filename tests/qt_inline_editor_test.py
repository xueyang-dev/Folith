"""工作台就地编辑：译文在中间的双语对照里改，右侧是作用于选中行的操作栏。

设计约定见 docs/agent-inspector-workspace.md："一个内容只能有一个可写位置"。
"""
from __future__ import annotations

import io
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402


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
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    from folith.qt import adapter, views

    sources = ["First sentence of the book.",
               "A much longer second sentence that has to wrap across several lines "
               "when the row is expanded for editing in the middle of the grid.",
               "Third one."]
    path = tmp_path / "book.docx"
    path.write_bytes(_docx(sources))
    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = adapter.import_documents([path], config)[0].job_id
    state = core.load_job_state(job_id)
    state["paras"] = sources
    state["pairs"] = [{"source": text, "target": f"译文{i}", "segment_uid": f"s{i}"}
                      for i, text in enumerate(sources)]
    state["p1_done"] = state["p2_done"] = True
    core.save_job_state(job_id, state)
    view = views.WorkbenchView()
    view.resize(1300, 800)
    view.show()
    view.load(job_id, config, adapter.load_state(job_id),
              adapter.segment_rows(job_id), [])
    app.processEvents()
    yield view, app
    view.flush_draft()
    view.hide()
    view.deleteLater()
    app.processEvents()


def _pump(app, seconds=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()


def test_the_only_editor_sits_in_the_selected_row_of_the_grid(bench):
    view, app = bench
    view.select_segment(1)
    _pump(app)
    editor = view.inline_editor
    # 编辑框是表格视口的子控件，摆在当前行的译文格里。
    assert editor.parentWidget() is view.segment_list.viewport()
    assert view.target_edit.parentWidget() is editor
    assert editor.isVisible()
    cell = view.segment_list.visualRect(view.segment_list.model().index(1, 2))
    assert cell.contains(editor.geometry().center())
    assert view.target_edit.toPlainText() == "译文1"
    # 展开行显示整段原文（不省略）并加高；其他行保持一行。
    assert view.segment_list.item(1, 1).text().endswith("middle of the grid.")
    assert view.segment_list.rowHeight(1) > view.segment_list.rowHeight(0)
    # 右侧没有原文 / 译文编辑框：一个内容只能有一个可写位置（批注框写的是批注，不是译文）。
    panel_editors = [widget for widget in view.inspector_scroll.findChildren(QPlainTextEdit)
                     if widget is not view.note_box.edit]
    assert panel_editors == []


def test_editor_follows_the_selection_and_collapses_the_previous_row(bench):
    view, app = bench
    view.select_segment(1)
    _pump(app)
    tall = view.segment_list.rowHeight(1)
    view.select_segment(2)
    _pump(app)
    assert view.segment_list.rowHeight(1) < tall
    assert view.segment_list.item(1, 1).text() != ""
    cell = view.segment_list.visualRect(view.segment_list.model().index(2, 2))
    assert cell.contains(view.inline_editor.geometry().center())
    assert view.target_edit.toPlainText() == "译文2"


def test_rows_are_as_tall_as_their_longer_side_and_never_truncated(bench):
    """acceptance-feedback #20：每行整段显示，行高按原文 / 译文中较长的一方。"""
    from folith.qt import segment_editor

    view, app = bench
    view.select_segment(2)
    table = view.segment_list
    view.resize(700, 800)  # 两列随窗口伸缩：窗口变窄 → 列宽变化 → 去抖 → 分批重算
    _pump(app, 0.4)
    short, long_ = table.rowHeight(0), table.rowHeight(1)
    assert short >= segment_editor.ROW_HEIGHT
    assert long_ > short, "the wrapped source must make its row taller"
    assert table.item(1, 1).text() == view._rows[1].source  # full text, no preview
    view.resize(2400, 800)
    _pump(app, 0.4)
    assert table.rowHeight(1) < long_, "a wider column needs fewer lines"


def test_save_bar_appears_only_after_a_change(bench):
    view, app = bench
    view.select_segment(0)
    _pump(app)
    footer = view.inline_editor.footer
    assert not footer.isVisibleTo(view.inline_editor)
    view.target_edit.setFocus()
    view.target_edit.insertPlainText("改")
    _pump(app)
    assert footer.isVisibleTo(view.inline_editor)
    assert view.inline_editor.state_label.text() == "● 未保存"
    assert view.submit_button.isVisibleTo(view.inline_editor)


def test_ctrl_return_in_the_editor_saves_and_moves_on(bench):
    from PySide6.QtTest import QTest

    view, app = bench
    submitted: list[int] = []
    view.editSubmitted.connect(submitted.append)
    view.select_segment(0)
    _pump(app)
    view.target_edit.setFocus()
    view.target_edit.insertPlainText("改")
    QTest.keyClick(view.target_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    _pump(app)
    assert submitted and submitted[0] == 0
    assert view.current_index() == 1
    # 没改过时 ⌘↩ 只是下一段，不提交。
    submitted.clear()
    QTest.keyClick(view.target_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    _pump(app)
    assert submitted == []
    assert view.current_index() == 2


def test_operation_panel_acts_on_the_selection(bench):
    view, app = bench
    view.select_segment(1)
    _pump(app)
    assert view.selection_title.text() == "第 2 段"
    assert view.retranslate_button.isVisibleTo(view)
    assert not view.review_selected_button.isVisibleTo(view)
    view.select_segments([0, 1, 2])
    _pump(app)
    assert view.selection_title.text() == "已选 3 段"
    assert view.review_selected_button.isVisibleTo(view)
    assert "3" in view.review_selected_button.text()
    assert not view.retranslate_button.isVisibleTo(view)
    assert not view.single_segment_section.isVisibleTo(view)


def test_filter_menu_combines_conditions(bench):
    view, app = bench
    from folith.qt import adapter

    state = dict(view._state)
    pairs = [dict(pair) for pair in state.get("pairs") or []]
    pairs[0]["from_tm"] = True
    pairs[2]["from_tm"] = True
    state["pairs"] = pairs
    state["findings"] = [{"segment_index": 2, "category": "terminology_consistency",
                          "severity": "actionable", "reason": "术语", "resolved": False}]
    state["source_quality_gate"] = {"flags": [{"segment_index": 0}]}
    view._state = state
    assert adapter.term_issue_indexes(state) == {2}
    assert adapter.source_quality_indexes(state) == {0}

    view.filter_actions["tm"].setChecked(True)
    assert view.filtered_indexes() == [0, 2]
    assert view.filter_button.text() == "筛选 · 1"
    assert view.filter_button.property("active") == "true"  # highlighted while filtered
    view.filter_actions["term_issues"].setChecked(True)
    assert view.filtered_indexes() == [2]
    view.clear_filters()
    assert view.filtered_indexes() == [0, 1, 2]
    assert view.filter_button.text() == "筛选"
    assert view.filter_button.property("active") == "false"


def test_save_and_next_skips_confirmed_segments(bench):
    view, app = bench
    rows = list(view._all_rows)
    import dataclasses

    view._all_rows = [dataclasses.replace(rows[0], status="待审校"),
                      dataclasses.replace(rows[1], status="已审校", reviewed=True),
                      dataclasses.replace(rows[2], status="待审校")]
    view._render_segments()
    view.select_segment(0)
    _pump(app)
    target = view._next_unconfirmed_position()
    assert view._rows[target].index == 2


def test_bulk_draft_helpers(bench):
    view, app = bench
    from folith.qt import adapter

    job = view.job_id
    state = adapter.load_state(job)
    uid0 = adapter.core.segment_uid(state["pairs"][0])
    uid1 = adapter.core.segment_uid(state["pairs"][1])
    adapter.save_draft(job, uid0, "新译文0", "译文0")
    adapter.save_draft(job, uid1, "新译文1", "更早的译文")   # 基于旧译文
    view._all_rows = adapter.segment_rows(job)
    view._render_segments()
    assert not view.drafts_banner.isHidden()
    assert "2 段" in view.drafts_label.text()
    result = adapter.submit_all_drafts(job)
    assert result["saved"] == 1 and result["stale"] == [1]
    assert adapter.load_state(job)["pairs"][0]["target"] == "新译文0"
    assert adapter.discard_all_drafts(job) == 1
    assert adapter.pending_draft_count(job) == 0


def test_word_count_bar_and_dialog(bench):
    view, app = bench
    view.select_segment(0)
    _pump(app)
    assert not view.word_bar.isHidden()
    total = view.word_total_label.text()
    assert total.startswith("原文 ") and "已译 100%" in total
    assert view.word_selection_label.text().startswith("第 1 段：原文 5 词")
    view.select_segments([0, 1])
    _pump(app)
    assert view.word_selection_label.text().startswith("所选 2 段")
    from folith.qt.dialogs import WordCountDialog

    dialog = WordCountDialog(view._word_count, view._all_rows[:2], view)
    assert dialog.table.item(0, 0).text().startswith("全部")
    assert dialog.selection_label is not None


def test_appending_segments_reuses_existing_table_items(bench):
    view, app = bench
    import dataclasses

    existing = view.segment_list.item(0, 1)
    rows = list(view._all_rows)
    rows.append(dataclasses.replace(rows[-1], index=len(rows), segment_uid="s-extra",
                                    source="Appended sentence.", target="追加译文"))
    view._state.setdefault("pairs", []).append(
        {"source": "Appended sentence.", "target": "追加译文", "segment_uid": "s-extra"})
    view._all_rows = rows
    view._render_segments()
    app.processEvents()
    assert view.segment_list.rowCount() == 4
    assert view.segment_list.item(0, 1) is existing
    assert view.segment_list.item(3, 1).text() == "Appended sentence."
    assert view.segment_list.item(3, 2).text() == "追加译文"


def test_changed_prefix_rebuilds_rows_instead_of_reusing_stale_cells(bench):
    view, app = bench
    import dataclasses

    rows = list(view._all_rows)
    rows[0] = dataclasses.replace(rows[0], segment_uid="s-changed",
                                  source="Changed source.", target="Changed target")
    rows.append(dataclasses.replace(rows[-1], index=len(rows), segment_uid="s-extra",
                                    source="Appended sentence.", target="追加译文"))
    view._state.setdefault("pairs", []).append(
        {"source": "Appended sentence.", "target": "追加译文", "segment_uid": "s-extra"})
    view._all_rows = rows
    view._render_segments()
    app.processEvents()
    assert view.segment_list.rowCount() == 4
    assert view.segment_list.item(0, 1).text() == "Changed source."
    assert view.segment_list.item(0, 2).text() == "Changed target"
