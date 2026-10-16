"""活动页：任务最近发生了什么（只读）。

旧界面有"运行抽屉"，任务卡住或失败时能看到卡在哪一步。这里把用户可见的运行时
事件折叠成人话的时间线；完整的技术日志留在磁盘上，可一键在 Finder 中显示。

跟随与详情的约定：

* 停在底部时新记录进来就跟到底；用户往上翻了（或刚动过滚轮 / 键盘），不抢视口，
  底部浮出"回到最新"，带上翻走之后新来了几条；
* 最新一步正在运行时，详情区锁定展开并跟着这一步（收起按钮不可用）；
  运行结束后交还收起的权利，详情保持原样，不会自己合上。
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QToolButton, QVBoxLayout, QWidget, QPlainTextEdit,
)

from . import components
from . import i18n
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
        self._expanded = False
        self.heading = QLabel(i18n.tr('运行记录'))
        self.heading.setObjectName("sectionTitle")
        self.hint = QLabel(i18n.tr('最近的步骤，最新的在最下面。完整日志请在 Finder 中查看。'))
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("activityList")
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.list.verticalScrollBar().rangeChanged.connect(self._range_changed)
        self.list.verticalScrollBar().sliderPressed.connect(self._user_input)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)
        self.fade = components.scroll_fade(self.list)
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
        self.log_button = QPushButton(i18n.tr('在 Finder 中显示完整日志'))
        self.log_button.setIcon(icons.icon("folder", 16, "text-secondary"))
        self.log_button.setIconSize(QSize(16, 16))
        self.log_button.clicked.connect(self.revealLogRequested.emit)

        bar = QHBoxLayout()
        bar.addWidget(self.heading)
        bar.addStretch(1)
        bar.addWidget(self.log_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(bar)
        layout.addWidget(self.hint)
        layout.addWidget(self.list, 1)
        self.detail_caption = QLabel(i18n.tr('步骤详情'), self)
        self.detail_caption.setObjectName("groupCaption")
        self.detail_toggle = QToolButton(self)
        self.detail_toggle.setObjectName("issueToggle")
        self.detail_toggle.setFixedSize(20, 20)
        self.detail_toggle.setIconSize(QSize(14, 14))
        self.detail_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_toggle.setAccessibleName(i18n.tr('详情'))
        self.detail_toggle.clicked.connect(self._toggle_detail)
        self.detail_head = QWidget(self)
        head = QHBoxLayout(self.detail_head)
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.detail_caption)
        head.addStretch(1)
        head.addWidget(self.detail_toggle)
        self.detail_head.setVisible(False)
        self.detail = QPlainTextEdit(self)
        self.detail.setReadOnly(True)
        self.detail.setAccessibleName(i18n.tr('通知详情'))
        self.detail.setVisible(False)
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.list.itemClicked.connect(self._item_clicked)
        layout.addWidget(self.detail_head)
        layout.addWidget(self.detail, 1)
        self.retry_button = QPushButton(i18n.tr('重试当前步骤'), self)
        self.retry_button.setAccessibleDescription(i18n.tr('重试当前任务失败的步骤。'))
        self.retry_button.setVisible(False)
        layout.addWidget(self.retry_button)
        layout.addWidget(self.empty)
        self.set_rows([])

    def set_rows(self, rows: list[dict], *, signature: str = "") -> None:
        self._signature = signature
        scrollbar = self.list.verticalScrollBar()
        position = scrollbar.value()
        before = self.list.count()
        live = bool(rows) and str(rows[-1].get("status") or "") == "running"
        # 运行中的这一步由面板跟随：没选中、或选中的就是（上一次的）最后一行。
        follow_live = live and self.list.currentRow() in (-1, before - 1)
        selected_item = self.list.currentItem()
        selected = selected_item.data(Qt.ItemDataRole.UserRole) if selected_item else None
        self._rebuilding = True
        self.list.blockSignals(True)
        self.list.clear()
        for row in rows:
            status = str(row.get("status") or "completed")
            item = QListWidgetItem(
                f"{_MARK.get(status, '·')}  {row.get('timestamp') or ''}   "
                f"{i18n.tr_dynamic(str(row.get('label') or ''))}")
            item.setForeground(style_module.qcolor(
                style_module.text_color(_TONE.get(status, "text-secondary"))))
            item.setData(Qt.ItemDataRole.UserRole, str(row.get("label") or "") +
                         ("\n\n" + str(row["detail"]) if row.get("detail") else ""))
            item.setToolTip(str(row.get("label") or ""))
            self.list.addItem(item)
        if follow_live:
            self.list.setCurrentRow(self.list.count() - 1)
            # 锁定展开的状态就是交还时的状态：运行结束后详情不会自己合上。
            self._expanded = True
        elif selected is not None:
            for index in range(self.list.count()):
                if self.list.item(index).data(Qt.ItemDataRole.UserRole) == selected:
                    self.list.setCurrentRow(index)
                    break
        self.list.blockSignals(False)
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
        self._sync_detail()

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

    # ---- 详情：运行中锁定展开 ----
    def _selection_changed(self, item, _previous=None):
        if item is not None:
            self._expanded = True
        self._sync_detail()

    def _item_clicked(self, _item) -> None:
        self._expanded = True
        self._sync_detail()

    def _toggle_detail(self) -> None:
        if self._live:
            return  # 运行中不收起：内容还在变，收起会让视口跳动
        self._expanded = not self._expanded
        self._sync_detail()

    def live(self) -> bool:
        return self._live

    def _sync_detail(self) -> None:
        item = self.list.currentItem()
        following = self._live and self.list.currentRow() == self.list.count() - 1
        open_ = item is not None and (self._live or self._expanded)
        self.detail.setPlainText(item.data(Qt.ItemDataRole.UserRole) if item else "")
        self.detail.setVisible(open_)
        self.detail_head.setVisible(item is not None)
        self.detail_caption.setText(i18n.tr('正在进行') if following else i18n.tr('步骤详情'))
        self.detail_toggle.setEnabled(not self._live)
        self.detail_toggle.setIcon(icons.icon("chevron-up" if open_ else "chevron-down", 14,
                                              "text-secondary"))
        if self._live:
            self.detail_toggle.setToolTip(i18n.tr('步骤运行中，完成后可收起'))
        else:
            self.detail_toggle.setToolTip(i18n.tr('收起详情') if open_ else i18n.tr('展开详情'))

    def signature(self) -> str:
        return self._signature

    def texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
