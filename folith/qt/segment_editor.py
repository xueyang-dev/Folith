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
from PySide6.QtGui import QBrush, QFontMetrics, QKeySequence, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QPlainTextEdit, QStyleOptionViewItem,
    QTableWidget, QVBoxLayout, QWidget,
)

import math
from functools import lru_cache

from . import i18n, platform_ui
from . import style as style_module
from . import theme as theme_module
from .flow_layout import FlowRow


@lru_cache(maxsize=4096)
def cell_direction(text: str, language: str | None = None) -> Qt.LayoutDirection:
    """一格文字的书写方向：优先语言设定，再按强方向字符的多少判断（阿拉伯语、希伯来语……从右往左）。"""
    from folith.textual import text_direction

    return (Qt.LayoutDirection.RightToLeft if text_direction(text, language) == "rtl"
            else Qt.LayoutDirection.LeftToRight)

#: 普通行的高度（与表格默认行高一致）。
ROW_HEIGHT = 26
#: 展开行里，编辑框下面那一条（未保存提示与保存按钮）的**最小**高度。
#: 实际高度按列宽动态计算：按钮折行后保存条会变高（见 InlineSegmentEditor.footer_height）。
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


#: 编辑框自带撤销历史的深度上限。QPlainTextEdit 的 undo 栈覆盖键盘输入；这里额外
#: 记一份文本快照，用来处理**整段文字被程序化替换**（辅助功能/AX 客户端设置输入框
#: 的值、输入法整段上屏等）的情况——那条路径不会进 Qt 的 undo 栈，⌘Z 于是什么都不做
#: （验收记录：第 18 段输入 x 后按 ⌘Z 仍显示 x[2]）。
UNDO_HISTORY_LIMIT = 200


class _TargetEdit(QPlainTextEdit):
    """译文编辑框：⌘↩ 交给工作台（保存并进入下一段），Esc 把焦点还给表格。

    ⌘Z / ⇧⌘Z 优先交给 Qt 的原生撤销栈；原生栈为空但文字被程序化换过时，退回本类
    记录的文本快照，保证「撤销」在任何一种输入方式下都真的撤销。
    """

    commitAndNext = Signal()
    escaped = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._undo_history: list[str] = []
        self._redo_history: list[str] = []
        self._tracked_text = ""
        self._restoring = False
        self._native_edit_action = False
        self.document().contentsChanged.connect(self._record_text_change)

    def setPlainText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        """设文字时同时套上阅读行距（拉丁 1.5 / 中文 1.7，F6）。

        必须在 setPlainText **之后**设块格式：它会把已有块格式重置掉。
        程序化装载（切换段落、后台译文到达）是新基线，不当成可撤销的用户编辑。
        """
        super().setPlainText(text)
        style_module.set_reading_line_height(
            self.document(), style_module.reading_line_height(text))
        if not self._restoring:
            self._undo_history.clear()
            self._redo_history.clear()
            self._tracked_text = str(text or "")

    def _record_text_change(self) -> None:
        """记下每次文本变化的上一版，供原生 undo 栈为空时回退。"""
        text = self.toPlainText()
        if self._restoring or self._native_edit_action:
            self._tracked_text = text
            return
        if text == self._tracked_text:
            return
        self._undo_history.append(self._tracked_text)
        if len(self._undo_history) > UNDO_HISTORY_LIMIT:
            del self._undo_history[0]
        self._redo_history.clear()
        self._tracked_text = text

    @staticmethod
    def _undo_pressed(event) -> bool:
        if event.matches(QKeySequence.StandardKey.Undo):
            return True
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        # Qt 在 macOS 把 ⌘ 报成 ControlModifier；这里也接受 Meta，避免平台差异
        # 让撤销键静默失效。只认 Z，不抢 AltGr 等组合。
        return (event.key() == Qt.Key.Key_Z
                and modifiers in (Qt.KeyboardModifier.ControlModifier,
                                  Qt.KeyboardModifier.MetaModifier))

    @staticmethod
    def _redo_pressed(event) -> bool:
        if event.matches(QKeySequence.StandardKey.Redo):
            return True
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        control = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier
        return ((event.key() == Qt.Key.Key_Z and modifiers in (
                    control | shift, Qt.KeyboardModifier.MetaModifier | shift))
                or (event.key() == Qt.Key.Key_Y and modifiers in (
                    control, Qt.KeyboardModifier.MetaModifier)))

    def _apply_programmatic_text(self, text: str) -> None:
        self._restoring = True
        try:
            super().setPlainText(text)
            style_module.set_reading_line_height(
                self.document(), style_module.reading_line_height(text))
        finally:
            self._restoring = False
        self._tracked_text = text
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cursor)

    def _history_step(self, source: list[str], target: list[str]) -> bool:
        if not source:
            return False
        text = source.pop()
        target.append(self.toPlainText())
        self._apply_programmatic_text(text)
        return True

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (
                event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                == Qt.KeyboardModifier.ControlModifier):
            self.commitAndNext.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        if self._undo_pressed(event):
            if self.document().isUndoAvailable():
                # 键盘输入的撤销仍走 Qt 原生栈（保留光标/选区的 Qt 语义）。
                self._native_edit_action = True
                try:
                    super().keyPressEvent(event)
                finally:
                    self._native_edit_action = False
            else:
                self._history_step(self._undo_history, self._redo_history)
            event.accept()
            return
        if self._redo_pressed(event):
            if self.document().isRedoAvailable():
                self._native_edit_action = True
                try:
                    super().keyPressEvent(event)
                finally:
                    self._native_edit_action = False
            else:
                self._history_step(self._redo_history, self._undo_history)
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
        # 提示文字只是加速键说明：放不下就省略，完整文字留在悬停提示里。它不能
        # 和按钮抢宽度——按钮被压到半截字时，"保存"就不可辨了（S3-01）。
        self.hint_label = style_module.ElideLabel(
            i18n.tr('{v0} 保存 · {v1} 保存并进入下一段',
                    v0=platform_ui.shortcut_text("save"),
                    v1=platform_ui.shortcut_text("save_next")))
        self.hint_label.setObjectName("hint")

        # 保存条分两层：上面一行是"● 未保存 + 快捷键提示"，下面一层是动作按钮。
        # 按钮走 FlowRow：译文列在 1280 逻辑 px 下只有约 280–400 px，英文/西语/俄语
        # 的 "Discard changes / Save and next" 单行放不下，折行而不是硬裁（S3-01）。
        # 高度随折行层数变化，见 :meth:`footer_height`。
        self.footer = QWidget()
        self._footer_layout = QVBoxLayout(self.footer)
        self._footer_layout.setContentsMargins(0, 2, 0, 0)
        self._footer_layout.setSpacing(theme_module.SPACE["xs"])
        # 先挂空布局再填控件：先填后挂会让 PySide 给每个条目留下 QWidgetItem
        # 包装器，浮层销毁后包装器残留（见 tests/qt_layout_item_wrapper_test.py）。
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(theme_module.SPACE["sm"])
        self._footer_layout.addLayout(status_row)
        self._footer_buttons = FlowRow(spacing=theme_module.SPACE["sm"], fill=False)
        self._footer_layout.addLayout(self._footer_buttons)
        status_row.addWidget(self.state_label, 0)
        status_row.addWidget(self.hint_label, 1)

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
            # 原文栏与译文栏用同一套阅读行距，两栏基线才对得齐（F6）。
            style_module.set_reading_line_height(
                self.source_view.document(),
                style_module.reading_line_height(text))

    def hide_all(self) -> None:
        self.hide()
        self.source_view.hide()

    def add_footer_button(self, button) -> None:
        self._footer_buttons.addWidget(button)

    def set_footer_visible(self, visible: bool) -> None:
        if self.footer.isVisibleTo(self) != visible:
            self.footer.setVisible(visible)
        if visible:
            self._sync_footer_height()

    def footer_height(self, width: int | None = None) -> int:
        """保存条在给定宽度下需要的高度（按钮折行后会变高）。

        ``views._place_inline_editor`` 用它算展开行的高度；固定 32 px 的单行
        保存条在长语言里放不下三个按钮（S3-01），高度必须跟着折行层数走。
        """
        width = max(1, int(width if width is not None else self.width()))
        layout = self.footer.layout()
        if layout is None:
            return FOOTER_HEIGHT
        return max(FOOTER_HEIGHT, int(layout.heightForWidth(width)))

    def _sync_footer_height(self) -> None:
        if self.footer.isHidden():
            return
        height = self.footer_height(self.width())
        if self.footer.height() != height:
            self.footer.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._sync_footer_height()

    def editor_height(self, width: int) -> int:
        """编辑框按内容需要的高度（不含保存条）：用与编辑框相同的排版算一遍。

        行距走阅读口径（拉丁 1.5 / 中文 1.7，F6）：以前量的是字体的天然行距（1.2/1.4），
        编辑框会比里面排出来的字矮，短句尤其明显。
        """
        text = self.edit.toPlainText() or " "
        # 外壳的内边距与边框（见 style.py 的 QPlainTextEdit#targetEdit）。
        chrome = 2 * (theme_module.SPACE["sm"] + 1)
        inner_width = max(40, width - chrome)
        measured = reading_height(text, self.edit.font(), inner_width)
        ratio = style_module.reading_line_height(text)
        pixels = _font_pixels(self.edit.font())
        minimum = MIN_EDITOR_LINES * max(1, math.ceil(pixels * ratio))
        return int(max(measured, minimum)) + chrome + 2


class WorkbenchRowDelegate(style_module.RowDelegate):
    """在 :class:`RowDelegate` 之上：当前展开的那一行，原文整段换行、顶部对齐。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # F6：网格正文的阅读行距绘制（style.RowDelegate.reading_text）默认**关闭**。
        # 已实现并验证（术语高亮与字同源，偏差 <0.01px），但在整轮窗口测试里观察到
        # 累积性的不稳定（长时间运行后偶发段错误）；为避免把风险带进 0.8.1，
        # 这一层留作下一轮的 opt-in：`self.set_reading_text(True)` 即可开启，
        # 同时行高测量要换成 style.reading_text_height()。当前行距生效范围见
        # 编辑框（_TargetEdit.setPlainText）与阅读字体设置页。
        self.active_row = -1
        self.saved: dict[int, float] = {}
        #: 全文定位中的术语（term_highlight.Occurrences）；None 表示没有。
        self.focus = None
        self.source_language: str = ""
        self.target_language: str = ""

    def paint(self, painter, option, index) -> None:  # noqa: N802 - Qt 命名
        super().paint(painter, option, index)
        found = self.focus
        if found is None or index.column() not in (1, 2) or index.row() not in found.positions:
            return
        if index.row() == self.active_row and index.column() == 1:
            return  # 原文由浮层显示，浮层自己标注（见 WorkbenchView._decorate_current_row）
        from . import term_highlight

        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        needles = ((found.focus.source, *found.focus.aliases) if index.column() == 1
                   else (found.focus.target,))
        ranges = [span for needle in needles for span in term_highlight.spans(needle, text)]
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
        lang = self.source_language if index.column() == 1 else (
            self.target_language if index.column() == 2 else None)
        option.direction = cell_direction(str(option.text or ""), lang)
        option.displayAlignment = (Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        option.textElideMode = Qt.TextElideMode.ElideNone
        share = self.saved.get(index.data(Qt.ItemDataRole.UserRole), 0.0)
        if share:
            colour = theme_module.qcolor(theme_module.tokens(style_module.current_theme())["success-soft"])
            colour.setAlphaF(share)
            option.backgroundBrush = QBrush(colour)


def _font_pixels(font) -> float:
    """字体的字号（像素）。``pixelSize`` 在点值字体上是 -1，退回 ``pointSizeF``。"""
    size = font.pixelSize()
    if size > 0:
        return float(size)
    points = font.pointSizeF()
    return float(points) if points > 0 else float(QFontMetrics(font).height())


def reading_height(text: str, font, width: int) -> int:
    """按阅读行距（拉丁 1.5 / 中文 1.7）排一段文字需要的高度。

    算法：先用 ``QFontMetrics.boundingRect`` 量出**同一宽度**下用字体天然行距排出来
    的高度，再按 ``目标倍率 / 天然倍率`` 等比放大。天然倍率 = 行距 ÷ 字号，所以
    14px 拉丁的 17.0 → 21.0（1.21→1.50）、中文 20.0 → 23.8（1.43→1.70），
    与审查报告量到的口径一致。

    为什么不用 ``QTextDocument`` 逐段排版量：这条路径在选中每一段、重算每一行高度时
    都会被调用（一本书几千次），实测在完整窗口测试里会把 Qt 排版引擎拖崩
    （``Segmentation fault`` in ``QTextDocument`` 布局，2026-11-04）。等比换算
    既没有这个问题，也不需要把整本书重新排一遍。
    """
    body = str(text or "") or " "
    metrics = QFontMetrics(font)
    rect = metrics.boundingRect(QRect(0, 0, max(40, int(width)), 100000),
                                int(Qt.TextFlag.TextWordWrap), body)
    natural = max(1.0, float(metrics.lineSpacing()) / max(1.0, _font_pixels(font)))
    target = max(1.0, float(style_module.reading_line_height(body)))
    measured = float(rect.height())
    if measured <= metrics.lineSpacing():
        # 只量到一行时才用逐字累加复核：正常换行的文本走 boundingRect 的快路径，
        # 不给几千行的书加成本（CI 的 macOS 上曾因此超时）。
        measured = max(measured,
                       _wrapped_height(body, metrics, width, _font_pixels(font)))
    return int(math.ceil(measured * target / natural))


def _wrapped_height(text: str, metrics, width: int, pixels: int = 0) -> int:
    """按"逐字累加宽度"估算换行高度（CJK 逐个字可断行）。

    ``QFontMetrics.boundingRect(…, TextWordWrap, …)`` 在 Linux 的字体回退下会把整段
    中文当成一个长词，只量出一行；行高因此不随译文变长而增长（CI 上
    ``test_longer_translation_that_arrives_grows_the_open_row`` 就是 ``60 > 60``）。
    缺字形时 ``horizontalAdvance()`` 甚至返回 0，所以这里对全角字符按字号兜底。
    结果只当**下限**用，偶尔高估一两行，绝不会把字裁掉。
    """
    limit = max(40, int(width))
    pixels = float(max(1, int(pixels or 14)))
    narrow = float(max(metrics.averageCharWidth(), 0) or round(pixels * 0.6))
    lines, current = 1, 0.0
    for char in text:
        advance = float(metrics.horizontalAdvance(char))
        if advance <= 0:
            code = ord(char)
            wide = (0x2E80 <= code <= 0x9FFF or 0xAC00 <= code <= 0xD7AF
                    or 0xF900 <= code <= 0xFAFF or 0xFF00 <= code <= 0xFF60)
            advance = pixels if wide else narrow
        if current + advance > limit and current > 0:
            lines += 1
            current = advance
        else:
            current += advance
    return lines * metrics.lineSpacing()


def source_height(table: QAbstractItemView, text: str, width: int) -> int:
    """原文在展开行里整段换行所需的高度。

    与译文编辑框同一口径（阅读行距 1.5 / 1.7，F6）：两栏用不同的行距量高度，
    展开行里就会一边松一边紧，基线对不齐。
    """
    return reading_height(text, table.font(), max(40, width - 2 * CELL_PAD - 10)) + 2 * CELL_PAD + 12


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
