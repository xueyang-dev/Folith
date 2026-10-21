"""Widgets built without a parent must not flash up as top-level windows."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import components, segment_assist, style as style_module  # noqa: E402


class _ShowSpy(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.windows: list[str] = []

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Show and getattr(watched, "isWindow", lambda: False)():
            self.windows.append(type(watched).__name__)
        return False


def _shown_windows(factory) -> list[str]:
    app = QApplication.instance() or QApplication([])
    spy = _ShowSpy()
    app.installEventFilter(spy)
    try:
        widget = factory()
        app.processEvents()
        widget.deleteLater()
    finally:
        app.removeEventFilter(spy)
    return spy.windows


def test_option_card_builds_without_a_top_level_show():
    assert _shown_windows(lambda: components.OptionCard("k", "标题", "细节", icon="check-circle")) == []


def test_option_card_set_detail_stays_inside_the_card():
    def build():
        card = components.OptionCard("k", "标题")
        card.set_detail("<b>x</b>")
        return card

    assert _shown_windows(build) == []


def test_term_chip_with_count_builds_without_a_top_level_show():
    assert _shown_windows(lambda: segment_assist.TermChip(
        {"source": "Nora", "target": "诺拉"}, 3)) == []


def test_notice_strip_with_text_builds_without_a_top_level_show():
    assert _shown_windows(lambda: style_module.NoticeStrip("文字")) == []
