"""Window-local notifications: no modal grab and no editor focus change.

#520：小通知共用一套视觉语言。语气（success / info / warning / error / progress）
决定图标与颜色，颜色只走 :data:`folith.qt.theme.STATUS_TONES` 的语义 token；
错误与警告常驻，其余约 5 秒后淡出；位置固定在窗口底部中央，长名字中间省略。
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QPushButton, QToolButton, QVBoxLayout)

from . import i18n, icons, motion, style, theme


#: 语气 → 状态语气键（QLabel[textRole="status"][tone=…] 的 QSS 已有这些颜色）
TONE_STATUS = {"success": "done", "info": "active", "warning": "attention",
               "error": "error", "progress": "active"}
#: 语气 → 主题图标引擎里的图标名
TONE_ICONS = {"success": "check-circle", "info": "info", "warning": "alert",
              "error": "alert", "progress": "pulse"}
#: 自动消失时间（毫秒）：错误与警告常驻，由用户关闭。
DISMISS_MS = 5000


def tone_of(error: bool = False, tone: str = "") -> str:
    return tone if tone in TONE_STATUS else ("error" if error else "info")


class Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("noticeToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.icon_label = QLabel(self)
        self.icon_label.setAccessibleName("")
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(False)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        style.set_text_role(self.label, "status")
        self.details = QPushButton(i18n.tr('详情'), self)
        self.details.clicked.connect(self._show_details)
        self.close_button = QPushButton(self)
        self.close_button.setObjectName("roundButton")
        self.close_button.setIcon(icons.icon("x", 14, "text-secondary"))
        self.close_button.setAccessibleName(i18n.tr('关闭通知'))
        self.close_button.setAccessibleDescription(i18n.tr('关闭当前通知，继续工作。'))
        self.close_button.clicked.connect(self.hide)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(theme.SPACE["sm"])
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.details)
        row.addWidget(self.close_button)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fade)
        self._motion = motion.Motion(self)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._detail = ""
        self._message = ""
        self._tone = "info"
        self._slot = 0
        self.hide()
        parent.installEventFilter(self)
        for control in (self.close_button, self.details, self.label):
            control.installEventFilter(self)

    def present(self, message: str, *, error=False, detail="", tone: str = "",
                slot: int = 0):
        self._motion.stop("fade")
        self._effect.setOpacity(1)
        self._tone = tone_of(error, tone)
        self._message = " ".join(message.split())
        self._detail = detail
        self._slot = max(0, int(slot))
        status = TONE_STATUS[self._tone]
        self.icon_label.setPixmap(icons.pixmap(TONE_ICONS[self._tone], 16,
                                               theme.STATUS_TONES[status]))
        self.label.setProperty("tone", status)
        style.repolish(self.label)
        self.label.setText(self._message)
        # 完整文字始终在提示与辅助技术里，省略只发生在可见的单行上。
        self.setAccessibleName(message)
        self.setAccessibleDescription(detail or message)
        self.details.setVisible(bool(detail) or len(self._message) > 240)
        self.details.setAccessibleDescription(message)
        self._place()
        self.show()
        self.raise_()
        if self._tone in ("error", "warning"):
            self.timer.stop()          # 错误 / 警告常驻，等用户关闭
        else:
            self.timer.start(DISMISS_MS)

    def _elided(self) -> str:
        """长文件名中间省略：保留开头和结尾，完整文字留在 tooltip / 辅助名里。"""
        width = max(80, self.width() - 140)  # 图标 + 两个按钮 + 内边距
        return QFontMetrics(self.label.font()).elidedText(
            self._message, Qt.TextElideMode.ElideMiddle, width)

    def _place(self):
        parent = self.parentWidget()
        self.setFixedWidth(max(320, min(580, parent.width() - 40)))
        self.label.setText(self._elided())
        self.label.setToolTip(self._message)
        self.adjustSize()
        # U-5/A-61: centre on the *actual* content widget, never on
        # `sidebar.width()` guessed as a left offset -- in RTL the sidebar is
        # on the right.  Map the content widget's rect into toast coordinates;
        # this works in both directions and when the sidebar is collapsed.
        sheet = getattr(parent, "sheet", None)
        if sheet is not None and sheet.isVisibleTo(parent):
            origin = sheet.mapTo(parent, sheet.rect().topLeft())
            left, content_width = origin.x(), sheet.width()
        else:
            left, content_width = 0, parent.width()
        content_width = max(1, content_width)
        stack = getattr(parent, "toast", None)
        count = max(1, len(getattr(stack, '_toasts', ()))) if stack is not None else 1
        h = self.height()
        y_last = parent.height() - 24 - h
        y = y_last - (count - 1 - self._slot) * (h + 8)
        self.move(left + (content_width - self.width()) // 2, max(0, y))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and watched is self.parentWidget() and self.isVisible():
            self._place()
        if event.type() == QEvent.Type.FocusOut and self.isVisible():
            self._resume_timer()
        return False

    def enterEvent(self, event):
        self.timer.stop()
        self._motion.stop("fade")
        self._effect.setOpacity(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._resume_timer()
        super().leaveEvent(event)

    def _resume_timer(self):
        if self._tone not in ("error", "warning"):
            self.timer.start(DISMISS_MS)

    def _fade(self):
        if self._tone in ("error", "warning"):
            return
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
        # A-50: reflow the remaining stack as soon as one toast disappears
        # (auto-fade or close button), so the bottom stays glued to 24 px.
        stack = getattr(self, "_stack", None)
        if stack is not None:
            QTimer.singleShot(0, self, stack._prune_and_reflow)

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
        # A bound method of the dialog on a child button's signal forms a reference
        # cycle (dialog -> button -> connection -> dialog.close).  That keeps the
        # Python wrappers alive after WA_DeleteOnClose deleted the C++ objects, which
        # is the stale-wrapper SIGSEGV class.  Close through a weakref instead.
        dialog_ref = weakref.ref(dialog)

        def close_dialog() -> None:
            target = dialog_ref()
            if target is not None:
                target.close()

        close.clicked.connect(close_dialog)
        layout.addWidget(close)
        dialog.show()
        self.hide()


class NoticeBanner(QFrame):
    """#520 行内任务横幅：图标 + 一句话 + 可选动作 + 关闭，始终只占一行。

    长文件名中间省略，完整文字留在 tooltip / 辅助名里；语气只改图标与文字颜色，
    卡片表面仍用 `bg-surface`，亮暗两套主题都由 token 决定。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("noticeBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.icon_label = QLabel(self)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(False)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        style.set_text_role(self.label, "status")
        self.action_button = QPushButton(self)
        style.set_button(self.action_button, "plain", small=True)
        self.action_button.setVisible(False)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("iconButton")
        self.close_button.setIcon(icons.icon("x", 14, "text-secondary"))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setAccessibleName(i18n.tr('关闭提示'))
        self.close_button.clicked.connect(self.hide)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(theme.SPACE["sm"])
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._message = ""
        self._tone = "info"
        self.hide()

    def set_notice(self, message: str, *, tone: str = "", error: bool = False,
                   action: str = "", on_action=None) -> None:
        self._tone = tone_of(error, tone)
        self._message = " ".join(message.split())
        status = TONE_STATUS[self._tone]
        self.icon_label.setPixmap(icons.pixmap(TONE_ICONS[self._tone], 16,
                                               theme.STATUS_TONES[status]))
        self.label.setProperty("tone", status)
        style.repolish(self.label)
        self.setAccessibleName(message)
        self.setAccessibleDescription(message)
        self.action_button.setVisible(bool(action))
        if action:
            self.action_button.setText(i18n.tr(action))
            if getattr(self, "_action_connected", False):
                self.action_button.clicked.disconnect()
                self._action_connected = False
            if on_action is not None:
                self.action_button.clicked.connect(on_action)
                self._action_connected = True
        self.show()
        self.raise_()
        self._elide()

    def _elide(self) -> None:
        width = max(80, self.width() - 120)
        self.label.setText(QFontMetrics(self.label.font()).elidedText(
            self._message, Qt.TextElideMode.ElideMiddle, width))
        self.label.setToolTip(self._message)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        if self.isVisible():
            self._elide()


class ToastStack:
    """#520: at most three toasts, newest on top, older ones stacked below.

    A plain object (not a QWidget): every geometry/visibility attribute the
    main window and tests read is forwarded explicitly to the visible toasts,
    so nothing ever measures an invisible container.  Errors/warnings stay until
    the user closes them; success/info fade on their own.
    """

    MAX_VISIBLE = 3

    def __init__(self, parent) -> None:
        self.parent = parent
        self._toasts: list[Toast] = []

    def present(self, message: str, *, error=False, detail="", tone: str = "") -> Toast:
        self._toasts = [t for t in self._toasts if not t.isHidden()]
        while len(self._toasts) >= self.MAX_VISIBLE:
            victim = next((t for t in self._toasts if t._tone not in ("error", "warning")),
                          self._toasts[0])
            self._toasts.remove(victim)
            victim.hide()
            victim.deleteLater()
        toast = Toast(self.parent)
        toast._stack = self
        self._toasts.append(toast)
        # Newest gets slot 0 (the topmost position); older ones shift down.
        for slot, existing in enumerate(reversed(self._toasts[:-1]), start=1):
            existing._slot = slot
            existing._place()
        toast.present(message, error=error, detail=detail, tone=tone, slot=0)
        return toast

    def _prune_and_reflow(self) -> None:
        """Drop hidden toasts and re-place the rest so the stack stays bottom-anchored."""
        self._toasts = [t for t in self._toasts if not t.isHidden()]
        for slot, toast in enumerate(reversed(self._toasts)):
            toast._slot = slot
            toast._place()

    def close_all(self) -> None:
        for toast in self._toasts:
            toast.hide()
        self._toasts.clear()

    def _fade(self) -> None:
        for toast in self._toasts:
            if toast._tone not in ("error", "warning"):
                toast._fade()

    def visible(self) -> list[Toast]:
        return [t for t in self._toasts if not t.isHidden()]

    # ---- explicit delegation -----------------------------------------------
    def __getattr__(self, name):
        toasts = object.__getattribute__(self, "_toasts")
        if not toasts:
            raise AttributeError(name)
        return getattr(toasts[-1], name)

    def newest(self) -> Toast | None:
        return self._toasts[-1] if self._toasts else None

    def isVisible(self):  # noqa: N802 - Qt 命名
        return bool(self.visible())

    def isHidden(self):  # noqa: N802 - Qt 命名
        return not self.isVisible()

    def y(self):  # noqa: N802 - Qt 命名
        return self._toasts[-1].y() if self._toasts else 0

    def x(self):  # noqa: N802 - Qt 命名
        return self._toasts[-1].x() if self._toasts else 0

    def width(self):  # noqa: N802 - Qt 命名
        return self._toasts[-1].width() if self._toasts else 0

    def height(self):  # noqa: N802 - Qt 命名
        return self._toasts[-1].height() if self._toasts else 0

    def hide(self) -> None:
        self.close_all()
