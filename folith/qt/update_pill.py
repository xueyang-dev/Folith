"""Sidebar update pill widget factory (#518)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QToolButton


def create_pill(on_click):
    pill = QToolButton()
    pill.setObjectName("updatePill")
    pill.setVisible(False)
    pill.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    pill.clicked.connect(on_click)
    notes = QLabel("")
    notes.setWordWrap(True)
    notes.setProperty("role", "muted")
    notes.setVisible(False)
    return pill, notes


def install(view, footer_layout):
    """Create the pill and place it above the settings row."""
    view._toggle_update_notes = lambda: toggle(view)
    view.update_pill, view._update_notes = create_pill(view._toggle_update_notes)
    view._update_url = ""
    footer_layout.insertWidget(0, view.update_pill)
    footer_layout.insertWidget(1, view._update_notes)


def toggle(view):
    view._update_notes.setVisible(not view._update_notes.isVisible())


def show(view, version: str, url: str, notes: str = "") -> None:
    from . import i18n
    view.update_pill.setText(i18n.tr("发现新版本 {v0}", v0=version))
    view.update_pill.setVisible(True)
    view._update_url = url
    view._update_notes.setText(notes)
    view._update_notes.setVisible(False)
