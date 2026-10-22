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


def test_cover_glass_darkens_more_on_light_covers(app):
    light = components._bottom_scrim(_image("#fdfcf8"))
    dark = components._bottom_scrim(_image("#1d2a40"))
    assert 0.45 <= dark < light <= 0.72, "white pages need a stronger scrim than dark covers"


@pytest.mark.parametrize("opening", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_flight_completion_survives_destination_destruction(app, monkeypatch, opening, deleted):
    """Returning home can rebuild cards before the flight has landed."""
    from types import SimpleNamespace
    from shiboken6 import delete
    from folith.qt.window import MainWindow

    host = QWidget()
    paper = components.CoverArt(host)
    host.show()
    pixmap = QPixmap(40, 50)
    pixmap.fill(QColor("white"))
    rect = QRectF(20, 20, 40, 50)
    completed = []
    monkeypatch.setattr(motion, "reduced", lambda: False)
    monkeypatch.setattr(components, "DocumentFlight",
                        lambda *a, **kw: completed.append(kw["on_done"]))
    view = SimpleNamespace(isVisible=lambda: True,
                           _card_document=lambda job: (pixmap, rect),
                           home=SimpleNamespace(_cards={"job": SimpleNamespace(paper=paper)}))
    if opening:
        MainWindow._fly(view, (pixmap, rect), opening=True, overview=(paper, rect))
    else:
        MainWindow._fly_back(view, "job", rect)
    assert paper._ghost
    if deleted:
        delete(paper)
    completed[0]()
    if not deleted:
        assert not paper._ghost
    host.close()


def test_task_tile_is_the_cover_itself(app):
    from folith.qt import adapter, views

    row = adapter.JobRow(job_id="j", filename="Survey.pdf", project_id=None, target_lang="简体中文",
                         status_label="待审校", progress_label="", runtime_status="idle",
                         segment_count=700, updated_at="", active=False, attention_count=2,
                         attention_tone="warning")
    card = views._TaskCard(row, tone="muted")
    card.set_tile_width(220)
    cover = card.paper.geometry()
    assert abs(cover.height() / cover.width() - views._TaskCard.ASPECT) < 0.02
    assert card.overlay.geometry() == cover, "the text floats on the cover"
    assert card.title.text() == "Survey.pdf" and "700 段" in card.meta.text()
    assert cover.contains(card.attention.geometry().center()), "problems badge sits on the glass"
    resting = card.paper.y()
    card._set_lift(1.0)
    assert card.paper.y() < resting
    assert not card.grab().isNull()
