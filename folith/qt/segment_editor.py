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
from PySide6.QtGui import QBrush, QFontMetrics, QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPlainTextEdit, QStyleOptionViewItem,
    QTableWidget, QVBoxLayout, QWidget,
)

from functools import lru_cache

from . import i18n
from . import style as style_module
from . import theme as theme_module


@lru_cache(maxsize=4096)
def cell_direction(text: str) -> Qt.LayoutDirection:
    """一格文字的书写方向：按强方向字符的多少判断（阿拉伯语、希伯来语……从右往左）。"""
    from folith.textual import text_direction

    return (Qt.LayoutDirection.RightToLeft if text_direction(text, None) == "rtl"
            else Qt.LayoutDirection.LeftToRight)

#: 普通行的高度（与表格默认行高一致）。
ROW_HEIGHT = 26
#: 展开行里，编辑框下面那一条（未保存提示与保存按钮）的高度。
FOOTER_HEIGHT = 32
#: 编辑框最少显示的行数：短句也要一眼看出"这里可以写"。
MIN_EDITOR_LINES = 2
#: 单元格内边距。
CELL_PAD = 6


def set_text_direction(edit, direction: Qt.LayoutDirection) -> None:
    """编辑框按书写方向排：只改 layoutDirection 不够——文档的段落方向和对齐不跟着变，
    阿拉伯语译文仍是左对齐（试用反馈图 5）。"""
    if edit.layoutDirection() != direction:
        edit.setLayoutDirection(direction)
    document = edit.document()
    option = document.defaultTextOption()
    rtl = direction == Qt.LayoutDirection.RightToLeft
    alignment = Qt.AlignmentFlag.AlignAbsolute | (
        Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)
    if option.textDirection() == direction and option.alignment() == alignment:
        return
    option.setTextDirection(direction)
    option.setAlignment(alignment)
    document.setDefaultTextOption(option)


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

        # 展开行的原文：只读、可选中（选中一个词即可「加入术语库」）。与编辑框同为
        # 视口上的浮层，由工作台按原文格摆放；表格本身在这一格不再画字。
        self.source_view = QPlainTextEdit(table.viewport())
        self.source_view.setObjectName("inlineSource")
        self.source_view.setReadOnly(True)
        self.source_view.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.source_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.source_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.source_view.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.source_view.hide()

    def set_source(self, text: str) -> None:
        if self.source_view.toPlainText() != (text or ""):
            self.source_view.setPlainText(text or "")

    def hide_all(self) -> None:
        self.hide()
        self.source_view.hide()

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
        self.saved: dict[int, float] = {}
        #: 全文定位中的术语（term_highlight.Occurrences）；None 表示没有。
        self.focus = None

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        super().paint(painter, option, index)
        found = self.focus
        if found is None or index.column() not in (1, 2) or index.row() not in found.positions:
            return
        if index.row() == self.active_row and index.column() == 1:
            return  # 原文由浮层显示，浮层自己标注（见 WorkbenchView._decorate_current_row）
        from . import term_highlight

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        needle = found.focus.source if index.column() == 1 else found.focus.target
        ranges = term_highlight.spans(needle, text)
        if not ranges:
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        widget = option.widget if option.widget is not None else self.parent()
        rects = term_highlight.text_rects(styled, text, ranges, widget)
        tone = "warning" if index.row() in found.inconsistent else "accent"
        term_highlight.paint_marks(painter, rects, tone)

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt 命名
        super().initStyleOption(option, index)
        if index.row() == self.active_row and index.column() == 1:
            # 原文由可选中的浮层显示（见 InlineSegmentEditor.source_view），这里不画字。
            option.text = ""
            return
        # 每一行都整段换行、顶部对齐（行高按较长的一方自适应，见 WorkbenchView）。
        option.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        # 译成阿拉伯语等从右往左的语言：这一格按 RTL 排（右对齐、标点与括号随之镜像），
        # 不是左对齐的 LTR 段落（试用反馈图 5）。AlignLeft 在 RTL 方向下会被翻成右对齐。
        option.direction = cell_direction(str(option.text or ""))
        option.displayAlignment = (Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        option.textElideMode = Qt.TextElideMode.ElideNone
        share = self.saved.get(index.data(Qt.ItemDataRole.UserRole), 0.0)
        if share:
            colour = theme_module.qcolor(theme_module.tokens(style_module.current_theme())["success-soft"])
            colour.setAlphaF(share)
            option.backgroundBrush = QBrush(colour)


def source_height(table: QAbstractItemView, text: str, width: int) -> int:
    """原文在展开行里整段换行所需的高度。"""
    metrics = QFontMetrics(table.font())
    rect = metrics.boundingRect(QRect(0, 0, max(40, width - 2 * CELL_PAD - 10), 100000),
                                int(Qt.TextFlag.TextWordWrap), text or " ")
    return rect.height() + 2 * CELL_PAD + 12


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
    "FOOTER_HEIGHT", "cell_direction", "set_text_direction", "InlineSegmentEditor", "ROW_HEIGHT", "WorkbenchRowDelegate",
    "install_repositioning", "source_height",
]
