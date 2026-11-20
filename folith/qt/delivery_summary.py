"""Shared compact summary for delivery contents (C8 / #521).

Both the setup stage (a collapsed 「交付内容」 row) and the delivery stage
(its header strip) describe *the same outputs*.  This module owns the one
widget that renders "标准交付 · 3 个文件 · DOCX ×2 · XLSX ×1" so the two
places look and behave identically, on both platforms and in both themes.
No business logic lives here: callers pass a plain summary string.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from . import i18n, icons, theme


class DeliverySummaryRow(QFrame):
    """One line: package icon + preset label + file breakdown + optional chevron."""

    def __init__(self, parent=None, *, chevron: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("deliverySummaryRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE["md"], theme.SPACE["xs"],
                               theme.SPACE["md"], theme.SPACE["xs"])
        row.setSpacing(theme.SPACE["sm"])
        self.icon = icons.IconLabel("package", 16, "text-muted", parent=self)
        self.preset = QLabel("")
        self.preset.setObjectName("rowTitle")
        self.detail = QLabel("")
        self.detail.setObjectName("rowValue")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.chevron = icons.IconLabel("chevron-down", 13, "text-muted", parent=self)
        self.chevron.setVisible(chevron)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.preset, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.detail, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_summary(self, preset: str, detail: str) -> None:
        """Only the facts: which preset and what it actually produces."""
        self.preset.setText(str(preset or ""))
        self.detail.setText(str(detail or ""))
        self.setToolTip(i18n.tr('交付内容：{v0} · {v1}', v0=preset, v1=detail))
        self.setAccessibleName(self.toolTip())

    def expand(self, opened: bool) -> None:
        self.chevron.set_icon("chevron-up" if opened else "chevron-down", "text-muted")
