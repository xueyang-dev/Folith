"""Fit a dialog/window into the screen's available area (C-35 / U-1, U-6).

Qt's ``QScreen.availableGeometry()`` already excludes the macOS Dock/menu bar
and the Windows taskbar, so one cross-platform call is enough.  The helper
caps the *frame* height (including decorations), leaves a small margin, keeps
the window centred over its parent when that fits, and never lets any part of
the window leave the available area.  Content that does not fit must scroll
inside its own ``QScrollArea`` — the caller owns that part.
"""
from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QWidget

MARGIN = 12


def available_area(widget: QWidget) -> QRect:
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return QRect(0, 0, 1600, 900)
    return screen.availableGeometry()


def max_client_height(widget: QWidget) -> int:
    """Tallest client height whose whole frame still fits the available area."""
    area = available_area(widget)
    frame = widget.frameGeometry()
    client = widget.geometry()
    extra_h = max(0, frame.height() - client.height())
    return max(1, area.height() - 2 * MARGIN - extra_h)


def capped_client_size(widget: QWidget) -> tuple[int, int]:
    """Client (inner) size that keeps the whole frame inside the available area."""
    area = available_area(widget)
    frame = widget.frameGeometry()
    client = widget.geometry()
    extra_w = max(0, frame.width() - client.width())
    extra_h = max(0, frame.height() - client.height())
    width = min(widget.width(), max(1, area.width() - 2 * MARGIN - extra_w))
    height = min(widget.height(), max(1, area.height() - 2 * MARGIN - extra_h))
    return max(1, width), max(1, height)


def fit_to_screen(widget: QWidget, *, recenter: bool = False) -> None:
    """Cap size and move the frame inside the available area; no OS branches."""
    width, height = capped_client_size(widget)
    if (width, height) != (widget.width(), widget.height()):
        widget.resize(width, height)
    area = available_area(widget)
    frame = widget.frameGeometry()
    if recenter:
        parent = widget.parentWidget()
        anchor = parent.frameGeometry() if parent is not None and parent.isVisible() else QRect()
        if not anchor.isValid() or not area.contains(anchor):
            anchor = area
        center = anchor.center()
        x, y = center.x() - frame.width() // 2, center.y() - frame.height() // 2
    else:
        x, y = frame.left(), frame.top()
    # C-46: when the available area barely fits the minimum size, discard the
    # cosmetic margin instead of pushing the frame off-screen.
    slack_x = max(0, area.width() - 2 * MARGIN - frame.width())
    slack_y = max(0, area.height() - 2 * MARGIN - frame.height())
    margin_x = MARGIN if slack_x > 0 else 0
    margin_y = MARGIN if slack_y > 0 else 0
    x = max(area.left() + margin_x,
            min(x, area.right() - frame.width() - margin_x + 1))
    y = max(area.top() + margin_y,
            min(y, area.bottom() - frame.height() - margin_y + 1))
    widget.move(x, y)
    actual = widget.frameGeometry().topLeft()
    if actual.x() != x or actual.y() != y:
        widget.move(widget.x() + x - actual.x(), widget.y() + y - actual.y())


class FittedDialog(QDialog):
    """A QDialog that re-fits itself into the available area whenever shown."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._fitted_once = False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        fit_to_screen(self, recenter=not self._fitted_once)
        self._fitted_once = True

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self.isVisible():
            fit_to_screen(self)
