"""文档的实物感：纸 / 一叠纸 / 书、折角、抬起，以及打开任务时文档飞入工作台。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.qt import components, motion  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _image(colour: str) -> QImage:
    image = QImage(60, 80, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    return image


def test_format_and_first_page_decide_paper_or_book(app):
    thumb = components.DocThumb(72, 94)
    thumb.set_document("Field Guide.epub", 900)
    assert thumb.is_book(), "EPUB is always a book"
    thumb.set_document("Paper.pdf", 120)
    thumb.set_image(_image("#ffffff"))
    assert not thumb.is_book(), "a white text page is paper"
    thumb.set_image(_image("#2a5a3a"))
    assert thumb.is_book(), "a coloured first page is a cover"
    thumb.set_document("Notes.docx", 50)
    assert not thumb.is_book(), "Word documents are never books"


def test_longer_documents_sit_on_a_thicker_stack(app):
    thumb = components.DocThumb()
    sheets = []
    for segments in (10, 120, 900, 5000):
        thumb.set_document("a.pdf", segments)
        sheets.append(thumb._sheets)
    assert sheets == [0, 1, 2, 3]


def test_lift_raises_the_page_and_every_kind_paints(app):
    thumb = components.DocThumb(72, 94)
    resting = thumb.page_rect().top()
    thumb.set_lift(1.0)
    assert thumb.page_rect().top() < resting
    thumb.set_lift(-0.3)
    assert thumb.page_rect().top() > resting, "pressing sinks it a little"
    for name in ("a.pdf", "b.epub", "c.txt", "d.md", "e.docx"):
        thumb.set_document(name, 800)
        assert not thumb.grab().isNull()


def test_flight_only_runs_with_motion_and_cleans_up(app, monkeypatch):
    host = QWidget()
    host.resize(800, 600)
    host.show()
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    pixmap = QPixmap(40, 50)
    pixmap.fill(QColor("#c0502a"))
    flight = components.DocumentFlight(host, pixmap, QRectF(20, 400, 40, 50),
                                       QRectF(300, 100, 200, 250), opening=True)
    assert flight.isVisible()
    QTest.qWait(motion.DURATION["view"] + 150)
    assert not [child for child in host.findChildren(components.DocumentFlight)
                if child.isVisible()]
    host.close()
