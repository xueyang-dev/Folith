"""Window-local notifications: no modal grab and no editor focus change."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from . import i18n, icons, motion, style


class Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details = QPushButton(i18n.tr('详情'), self)
        self.details.clicked.connect(self._show_details)
        self.close_button = QPushButton(self)
        self.close_button.setObjectName("roundButton")
        self.close_button.setIcon(icons.icon("x", 14, "text-secondary"))
        self.close_button.setAccessibleName(i18n.tr('关闭通知'))
        self.close_button.setAccessibleDescription(i18n.tr('关闭当前通知，继续工作。'))
        self.close_button.clicked.connect(self.hide)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 10, 10)
        row.addWidget(self.label, 1)
        row.addWidget(self.details)
        row.addWidget(self.close_button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade)
        self._motion = motion.Motion(self)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._detail = ""
        self.hide()
        parent.installEventFilter(self)
        for control in (self.close_button, self.details, self.label):
            control.installEventFilter(self)

    def present(self, message: str, *, error=False, detail=""):
        self._motion.stop("fade")
        self._effect.setOpacity(1)
        compact = " ".join(message.split())
        long = len(compact) > 240
        self._message = message
        self._detail = detail
        self.label.setText(compact[:239] + "…" if long else message)
        self.label.setProperty("error", "true" if error else "false")
        style.repolish(self.label)
        self.setAccessibleName(message)
        self.setAccessibleDescription(detail or message)
        self.details.setVisible(bool(detail) or long)
        self.details.setAccessibleDescription(message)
        self._place()
        self.show()
        self.raise_()
        self.timer.start(3000)

    def _place(self):
        parent = self.parentWidget()
        self.setFixedWidth(max(120, min(580, parent.width() - 32)))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, parent.menuBar().height() + 12)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        if event.type() == QEvent.Type.FocusOut and self.isVisible():
            self.timer.start(3000)
        return False

    def enterEvent(self, event):
        self.timer.stop()
        self._motion.stop("fade")
        self._effect.setOpacity(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.timer.start(3000)
        super().leaveEvent(event)

    def _fade(self):
        if self.underMouse() or (self.window().focusWidget() is not None and
                                 self.isAncestorOf(self.window().focusWidget())):
            return
        if motion.reduced_transparency():
            self.hide()
            return
        self._motion.animate("fade", 1, 0, self._effect.setOpacity,
                             kind="local", finished=self.hide)

    def hideEvent(self, event):
        self.timer.stop()
        self._motion.stop("fade")
        super().hideEvent(event)

    def _show_details(self):
        # Non-modal, selectable complete text; closing it returns to the editor.
        from PySide6.QtWidgets import QDialog, QPlainTextEdit
        dialog = QDialog(self.window())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(i18n.tr('详情'))
        dialog.resize(620, 360)
        text = QPlainTextEdit(dialog)
        text.setReadOnly(True)
        text.setPlainText(self._message + ("\n\n" + self._detail if self._detail else ""))
        text.setAccessibleName(i18n.tr('通知详情'))
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        close = QPushButton(i18n.tr('关闭'), dialog)
        close.clicked.connect(dialog.close)
        layout.addWidget(close)
        dialog.show()
        self.hide()
