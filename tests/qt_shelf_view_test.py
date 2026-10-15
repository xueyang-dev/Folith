"""任务库的书架视图：书脊尺寸随长度、颜色稳定、状态色带、选择与键盘。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, shelf_view  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _row(job_id, filename, segments, **extra):
    base = dict(job_id=job_id, filename=filename, project_id=None, target_lang="简体中文",
                status_label="待审校", progress_label="", runtime_status="idle",
                segment_count=segments, updated_at="", active=False)
    base.update(extra)
    return adapter.JobRow(**base)


def test_spine_grows_with_the_document_but_stays_bounded():
    small, medium, huge = (shelf_view.spine_size(n) for n in (5, 400, 50_000))
    assert small.width() == shelf_view.MIN_W and small.height() == shelf_view.MIN_H
    assert small.width() < medium.width() < huge.width() <= shelf_view.MAX_W
    assert huge.height() <= shelf_view.MAX_H


def test_colours_are_stable_and_follow_the_cover():
    assert shelf_view.fallback_colour("a.pdf", False) == shelf_view.fallback_colour("a.pdf", False)
    cover = QImage(40, 60, QImage.Format.Format_RGB32)
    cover.fill(QColor("#2a7a3a"))                       # a green cover
    colour = shelf_view.cover_colour(cover, False)
    assert colour is not None and 90 < colour.hslHue() < 150
    paper = QImage(40, 60, QImage.Format.Format_RGB32)
    paper.fill(QColor("#ffffff"))                       # a plain page has no colour of its own
    assert shelf_view.cover_colour(paper, False) is None


def test_status_band_uses_the_shared_meaning():
    assert shelf_view.status_tone(_row("e", "e.pdf", 1, error="boom")) == "danger"
    assert shelf_view.status_tone(_row("w", "w.pdf", 1, attention_count=2)) == "warning"
    assert shelf_view.status_tone(_row("r", "r.pdf", 1, active=True)) == "accent"
    assert shelf_view.status_tone(_row("d", "d.pdf", 1, snapshot_version=1)) == "success"
    assert shelf_view.status_tone(_row("n", "n.pdf", 1)) == ""


def test_click_select_double_click_open_and_keyboard(app):
    view = shelf_view.ShelfView()
    view.resize(900, 600)
    rows = [_row(f"j{i}", f"Book {i}.docx", 100 * (i + 1)) for i in range(4)]
    view.fill([("项目甲", rows[:2]), ("项目乙", rows[2:])])
    view.show()
    app.processEvents()
    clicked, opened = [], []
    view.clicked.connect(clicked.append)
    view.activated.connect(opened.append)
    first = view.strips[0]
    point = first.spine_rect("j1").center()
    QTest.mouseClick(first, Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(first, Qt.MouseButton.LeftButton, pos=point)
    assert clicked[0] == "j1" and opened == ["j1"]
    view.set_selected({"j1"})
    assert first.selected == {"j1"}
    first.focus_job("j1")
    QTest.keyClick(first, Qt.Key.Key_Right)       # crosses into the next shelf
    assert clicked[-1] == "j2"
    second = view.strips[1]
    QTest.keyClick(second, Qt.Key.Key_Return)
    assert opened[-1] == "j2"
    assert "Book 2.docx" in second.accessibleDescription()


def test_bookplate_appears_on_hover_with_the_task_facts(app):
    view = shelf_view.ShelfView()
    view.resize(900, 600)
    row = _row("j0", "Annual Report.pdf", 1200, progress_percent=64, attention_count=3)
    view.fill([("项目", [row])])
    view.show()
    app.processEvents()
    strip = view.strips[0]
    strip._set_hover("j0")
    app.processEvents()
    assert view.plate.isVisible()
    assert "Annual Report.pdf" in view.plate.title.text()
    assert "1,200 段" in view.plate.meta.text() and "PDF" in view.plate.meta.text()
    assert "已译 64%" in view.plate.status.text() and "3 项待处理" in view.plate.status.text()
