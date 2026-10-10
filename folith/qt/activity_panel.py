"""活动页：任务最近发生了什么（只读）。

旧界面有"运行抽屉"，任务卡住或失败时能看到卡在哪一步。这里把用户可见的运行时
事件折叠成人话的时间线；完整的技术日志留在磁盘上，可一键在 Finder 中显示。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
    QWidget,
)

from . import i18n
from . import icons
from . import style as style_module

_MARK = {"completed": "✓", "running": "…", "failed": "✕"}
_TONE = {"completed": "text-secondary", "running": "accent", "failed": "danger"}


class ActivityPanel(QWidget):
    revealLogRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._signature = ""
        self.heading = QLabel(i18n.tr('任务活动'))
        self.heading.setObjectName("sectionTitle")
        self.hint = QLabel(i18n.tr('最近发生的步骤，最新的在最下面。完整的技术日志不在这里显示，可在 Finder 中查看。'))
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("activityList")
        self.list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.empty = QLabel(i18n.tr('还没有活动记录。任务开始运行后，这里会显示每一步。'))
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.log_button = QPushButton(i18n.tr('在 Finder 中显示技术日志'))
        self.log_button.setIcon(icons.icon("folder", 16, style_module.text_color("text-secondary")))
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
        layout.addWidget(self.empty)
        self.set_rows([])

    def set_rows(self, rows: list[dict], *, signature: str = "") -> None:
        self._signature = signature
        self.list.clear()
        for row in rows:
            status = str(row.get("status") or "completed")
            item = QListWidgetItem(
                f"{_MARK.get(status, '·')}  {row.get('timestamp') or ''}   "
                f"{i18n.tr_dynamic(str(row.get('label') or ''))}")
            item.setForeground(style_module.qcolor(
                style_module.text_color(_TONE.get(status, "text-secondary"))))
            self.list.addItem(item)
        if rows:
            self.list.scrollToBottom()
        self.empty.setVisible(not rows)
        self.list.setVisible(bool(rows))

    def signature(self) -> str:
        return self._signature

    def texts(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]
