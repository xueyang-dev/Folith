"""运行记录：任务最近发生了什么（只读），从顶栏的进度条下面展开。

只有一条滚动的状态时间线，底下一个「在 Finder 中显示完整日志」；没有标题、关闭按钮
或步骤详情——某一步的完整说明在悬停提示里，完整的技术日志留在磁盘上。

跟随的约定：停在底部时新记录进来就跟到底；用户往上翻了（或刚动过滚轮 / 键盘），
不抢视口，底部浮出"回到最新"，带上翻走之后新来了几条。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout, QWidget,
)

from . import i18n, platform_ui
from . import icons
from . import style as style_module

_MARK = {"completed": "✓", "running": "…", "failed": "✕"}
_TONE = {"completed": "text-secondary", "running": "accent", "failed": "danger"}
#: 离底部这么近（px）仍算"在底部"，新记录进来继续跟随。
PIN_TOLERANCE = 40
#: 离底部超过这么远（px）才浮出"回到最新"。
JUMP_DISTANCE = 120
#: 用户刚滚动 / 按键后的这段时间（ms）里，新记录不抢视口。
INPUT_GRACE_MS = 150


class ActivityPanel(QWidget):
    revealLogRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._signature = ""
        self._pinned = True
        self._rebuilding = False
        self._input_at = float("-inf")
        self._unseen = 0
        self._live = False
        self.list = QListWidget()
        self.list.setObjectName("activityList")
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 浮层窄：长步骤换行，不横向滚动。
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setWordWrap(True)
        self.list.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.list.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.list.verticalScrollBar().sliderPressed.connect(self._user_input)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)
        # 不加滚动渐隐：浮层是从进度条长出来的，顶边是实的（2026-10-16 试用反馈）。
        # 浮在列表上（父对象是列表而不是 viewport：viewport 的子控件会跟着内容滚走）。
        self.jump_button = QPushButton(i18n.tr('回到最新'), self.list)
        self.jump_button.setObjectName("jumpLatest")
        self.jump_button.setIcon(icons.icon("arrow-down", 14, "text-primary"))
        self.jump_button.setIconSize(QSize(14, 14))
        self.jump_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.jump_button.clicked.connect(self.scroll_to_latest)
        self.jump_button.setVisible(False)
        self.empty = QLabel(i18n.tr('还没有活动记录。任务开始运行后，这里会显示每一步。'))
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.log_button = QPushButton(i18n.tr('在{v0}中显示完整日志', v0=platform_ui.file_manager_name()))
        self.log_button.setIcon(icons.icon("folder", 16, "text-secondary"))
        self.log_button.setIconSize(QSize(16, 16))
        self.log_button.clicked.connect(self.revealLogRequested.emit)

        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.addWidget(self.log_button)
        foot.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty, 1)
        layout.addLayout(foot)
        self.set_rows([])

    def set_rows(self, rows: list[dict], *, signature: str = "") -> None:
        self._signature = signature
        scrollbar = self.list.verticalScrollBar()
        position = scrollbar.value()
        before = self.list.count()
        live = bool(rows) and str(rows[-1].get("status") or "") == "running"
        self._rebuilding = True
        self.list.clear()
        for row in rows:
            status = str(row.get("status") or "completed")
            label = i18n.tr_dynamic(str(row.get("label") or ""))
            item = QListWidgetItem(
                f"{_MARK.get(status, '·')}  {row.get('timestamp') or ''}   {label}")
            item.setForeground(style_module.qcolor(
                style_module.text_color(_TONE.get(status, "text-secondary"))))
            # 步骤的完整说明（例如失败原因）放在悬停提示里。
            item.setToolTip(label + ("\n\n" + str(row["detail"]) if row.get("detail") else ""))
            self.list.addItem(item)
        self._live = live
        self.empty.setVisible(not rows)
        self.list.setVisible(bool(rows))
        if rows and self._pinned and not self._recent_input():
            self.list.scrollToBottom()
            self._unseen = 0
        else:
            scrollbar.setValue(position)
            if rows and before:
                self._unseen += max(0, len(rows) - before)
        self._rebuilding = False
        self._scrolled()

    # ---- 跟随到底 / 回到最新 ----
    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        kind = event.type()
        if kind in (QEvent.Type.Wheel, QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
            self._user_input()
        elif kind == QEvent.Type.Resize and watched is self.list:
            self._place_jump()
        return super().eventFilter(watched, event)

    def _user_input(self) -> None:
        self._input_at = time.monotonic()

    def _recent_input(self) -> bool:
        return (time.monotonic() - self._input_at) * 1000 < INPUT_GRACE_MS

    def distance_from_bottom(self) -> int:
        bar = self.list.verticalScrollBar()
        return bar.maximum() - bar.value()

    def pinned(self) -> bool:
        return self._pinned

    def _scrolled(self, *_args) -> None:
        if self._rebuilding:
            return  # clear() 把滚动条归零，不是用户离开了底部
        distance = self.distance_from_bottom()
        self._pinned = distance <= PIN_TOLERANCE
        if self._pinned:
            self._unseen = 0
        show = distance > JUMP_DISTANCE and self.list.count() > 0
        if show:
            self.jump_button.setText(
                i18n.tr('{v0} 条新记录', v0=self._unseen) if self._unseen else i18n.tr('回到最新'))
            self._place_jump()
        self.jump_button.setVisible(show)
        if show:
            self.jump_button.raise_()

    def _range_changed(self, *_args) -> None:
        """列表高度变了（详情展开、窗口缩放）：原本贴底的，继续贴底。"""
        if self._rebuilding:
            return
        if self._pinned and not self._recent_input():
            self.list.scrollToBottom()
        self._scrolled()

    def _place_jump(self) -> None:
        self.jump_button.adjustSize()
        viewport = self.list.viewport().geometry()
        size = self.jump_button.sizeHint()
        self.jump_button.setGeometry(viewport.center().x() - size.width() // 2,
                                     viewport.bottom() - size.height() - 10,
                                     size.width(), size.height())

    def scroll_to_latest(self) -> None:
        self._input_at = float("-inf")
        self.list.scrollToBottom()
        self._unseen = 0
        self._scrolled()

    def live(self) -> bool:
        return self._live

    def signature(self) -> str:
        return self._signature

    def texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
