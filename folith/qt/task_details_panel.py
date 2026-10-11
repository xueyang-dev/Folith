"""「任务详情」页：开始翻译以后，这个任务用的是什么设置。

开始翻译后「准备」页就收起了，之前的选择（翻译模式、术语、审校、识别方式、交付
文件……）在界面上再也看不到。这一页把它们如实列出来（来自
:func:`folith.qt.adapter.task_details`），并放着文档画像——画像判断错了可以在这里改。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import i18n
from . import style as style_module
from . import theme as theme_module
from .style import Card


class TaskDetailsPanel(QWidget):
    profileEditRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[dict] = []
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        self.profile_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile_button = QPushButton(i18n.tr('修改画像…'))
        self.profile_button.clicked.connect(self.profileEditRequested.emit)
        profile_card = Card()
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                          theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        head = QHBoxLayout()
        title = QLabel(i18n.tr('文档画像'))
        title.setObjectName("cardTitle")
        head.addWidget(title, 1)
        head.addWidget(self.profile_button)
        profile_layout.addLayout(head)
        profile_layout.addWidget(self.profile_label)

        self.groups_card = Card()
        self._groups_layout = QVBoxLayout(self.groups_card)
        self._groups_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                               theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        self._groups_layout.setSpacing(theme_module.SPACE["md"])

        column = QWidget()
        column.setMaximumWidth(820)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, theme_module.SPACE["lg"], 0, 0)
        column_layout.setSpacing(theme_module.SPACE["md"])
        column_layout.addWidget(profile_card)
        column_layout.addWidget(self.groups_card)
        column_layout.addStretch(1)
        content = QWidget()
        centre = QHBoxLayout(content)
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addStretch(1)
        centre.addWidget(column, 100)
        centre.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def apply(self, groups: list[dict], profile: dict, profile_text: str) -> None:
        self.profile_label.setText(
            profile_text or i18n.tr('还没有文档画像。可以自己填写：领域、文本类型、读者、语域。'))
        self.profile_label.setObjectName("" if profile_text else "hint")
        style_module.repolish(self.profile_label)
        if groups == self._groups:
            return
        self._groups = [dict(item) for item in groups]
        while self._groups_layout.count():
            item = self._groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for group in groups:
            self._groups_layout.addWidget(style_module.SectionLabel(i18n.tr(group["title"])))
            grid_holder = QWidget()
            grid = QGridLayout(grid_holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme_module.SPACE["lg"])
            grid.setVerticalSpacing(theme_module.SPACE["xs"])
            for row, (label, value) in enumerate(group["rows"]):
                grid.addWidget(style_module.field_label(i18n.tr_dynamic(label)), row, 0,
                               Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                text = QLabel(i18n.tr_dynamic(value))
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(text, row, 1)
            grid.setColumnStretch(1, 1)
            self._groups_layout.addWidget(grid_holder)

    def texts(self) -> list[str]:
        return [f"{label}：{value}" for group in self._groups for label, value in group["rows"]]
