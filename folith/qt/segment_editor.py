"""工作台的就地编辑：译文就在中间的双语对照里改，没有第二个编辑位置。

设计约定见 ``docs/agent-inspector-workspace.md``（"一个内容只能有一个可写位置"）：

* 中间网格是唯一编辑区。当前选中的那一行展开：原文整段换行显示，译文格变成
  编辑框；其余行仍是一行一段的轻量表格（6000 多段的书也不会建几千个编辑框）。
* 编辑框只有**一个**，随当前行移动（它浮在表格视口上，按单元格位置摆放）。
  后台刷新不会重建它，所以光标、撤销历史和未提交文字都不会丢。
* 保存按需浮现：内容没变时编辑框下面什么都没有；改了才出现"● 未保存　保存"。
  ⌘S 保存，⌘↩ 保存并进入下一段。
* 对段落的其他操作（审校、重译、排除、段落结构、查看原位置）在右侧操作栏，
  作用于表格里选中的行。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPlainTextEdit, QStyleOptionViewItem,
    QTableWidget, QVBoxLayout, QWidget,
)

from . import i18n
from . import style as style_module
from . import theme as theme_module

#: 普通行的高度（与表格默认行高一致）。
ROW_HEIGHT = 26
#: 展开行里，编辑框下面那一条（未保存提示与保存按钮）的高度。
FOOTER_HEIGHT = 32
#: 编辑框最少显示的行数：短句也要一眼看出"这里可以写"。
MIN_EDITOR_LINES = 2
#: 单元格内边距。
CELL_PAD = 6


class _TargetEdit(QPlainTextEdit):
    """译文编辑框：⌘↩ 交给工作台（保存并进入下一段），Esc 把焦点还给表格。"""

    commitAndNext = Signal()
    escaped = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                     | Qt.KeyboardModifier.MetaModifier)):
            self.commitAndNext.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class InlineSegmentEditor(QWidget):
    """浮在表格视口上的编辑框 + 按需出现的保存条。"""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table.viewport())
        self.setObjectName("inlineEditor")
        self._table = table
        self.edit = _TargetEdit()
        self.edit.setObjectName("targetEdit")
        self.edit.setPlaceholderText(i18n.tr('在这里输入译文'))
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

        self.state_label = QLabel("")
        self.state_label.setObjectName("inlineState")
        self.hint_label = QLabel(i18n.tr('⌘S 保存 · ⌘↩ 保存并进入下一段'))
        self.hint_label.setObjectName("hint")

        self.footer = QWidget()
        self.footer.setFixedHeight(FOOTER_HEIGHT)
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(theme_module.SPACE["sm"])
        footer.addWidget(self.state_label)
        footer.addWidget(self.hint_label)
        footer.addStretch(1)
        self._footer_layout = footer

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.footer)
        self.footer.setVisible(False)
        self.hide()

    def add_footer_button(self, button) -> None:
        self._footer_layout.addWidget(button)

    def set_footer_visible(self, visible: bool) -> None:
        if self.footer.isVisible() != visible:
            self.footer.setVisible(visible)

    def editor_height(self, width: int) -> int:
        """编辑框按内容需要的高度（不含保存条）：用与编辑框相同的排版算一遍。"""
        document = QTextDocument()
        document.setDefaultFont(self.edit.font())
        document.setDocumentMargin(self.edit.document().documentMargin())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(self.edit.toPlainText() or " ")
        # 外壳的内边距与边框（见 style.py 的 QPlainTextEdit#targetEdit）。
        chrome = 2 * (theme_module.SPACE["sm"] + 1)
        document.setTextWidth(max(40, width - chrome))
        lines = max(MIN_EDITOR_LINES, document.blockCount())
        minimum = lines * QFontMetrics(self.edit.font()).lineSpacing()
        return int(max(document.size().height(), minimum)) + chrome + 2


class WorkbenchRowDelegate(style_module.RowDelegate):
    """在 :class:`RowDelegate` 之上：当前展开的那一行，原文整段换行、顶部对齐。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.active_row = -1

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt 命名
        super().initStyleOption(option, index)
        if index.row() == self.active_row:
            option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
            option.displayAlignment = (Qt.AlignmentFlag.AlignTop
                                       | Qt.AlignmentFlag.AlignLeft)
            option.textElideMode = Qt.TextElideMode.ElideNone


def source_height(table: QAbstractItemView, text: str, width: int) -> int:
    """原文在展开行里整段换行所需的高度。"""
    metrics = QFontMetrics(table.font())
    rect = metrics.boundingRect(QRect(0, 0, max(40, width - 2 * CELL_PAD), 100000),
                                int(Qt.TextFlag.TextWordWrap), text or " ")
    return rect.height() + 2 * CELL_PAD + 4


def install_repositioning(table: QTableWidget, callback) -> None:
    """表格滚动、列宽变化、视口改变大小时重新摆放编辑框。"""
    table.verticalScrollBar().valueChanged.connect(lambda _value: callback())
    table.horizontalScrollBar().valueChanged.connect(lambda _value: callback())
    table.horizontalHeader().sectionResized.connect(lambda *_args: callback())

    class _ResizeWatcher(QWidget):
        def eventFilter(self, watched, event):  # noqa: N802 - Qt 命名
            if event.type() == QEvent.Type.Resize:
                callback()
            return False

    watcher = _ResizeWatcher(table)
    watcher.hide()
    table.viewport().installEventFilter(watcher)
    table._inline_resize_watcher = watcher  # 保活


__all__ = [
    "FOOTER_HEIGHT", "InlineSegmentEditor", "ROW_HEIGHT", "WorkbenchRowDelegate",
    "install_repositioning", "source_height",
]
