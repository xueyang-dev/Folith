"""Compact left column for the setup stage (C8 / #521).

At 1280×800 and 1366×768 the cover must not eat the whole left column: the
setup page should fit without scrolling for the common case.  This subclass
keeps every `DocumentOverview` method (so `set_document` / `_ready` / notes all
keep working) and only uses a smaller paper plus a tighter spacing.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

from . import theme as theme_module
from .doc_overview import DocumentOverview


class CompactOverview(DocumentOverview):
    """The same overview with a small cover, tuned for a short viewport."""

    COVER_W, COVER_H = 120, 156

    def __init__(self, parent=None) -> None:
        # The base constructor creates the 200×260 cover; replace it afterwards
        # with a smaller one at the same position in the column.
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        column = self.layout()
        old = self.paper
        from . import components

        small = components.DocThumb(self.COVER_W, self.COVER_H, parent=self)
        index = column.indexOf(old)
        column.removeWidget(old)
        old.deleteLater()
        column.insertWidget(index, small, 0, Qt.AlignmentFlag.AlignHCenter)
        column.setSpacing(theme_module.SPACE["xs"])
        self.paper = small
