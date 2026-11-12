"""术语在正文里的位置：当前行下划线、悬停高亮、点击后全文定位。

三层（2026-10-15 设计讨论）：

1. 当前行：原文里的术语、译文里的首选译名都加一条虚线下划线；译文用了禁用译名时
   是红色波浪线。
2. 悬停检查器里的术语 → 当前行原文与译文里对应的那一对亮起来。
3. 点击术语 → 网格里所有出现的位置都高亮，滚动条上标出位置，可以逐处跳转；
   原文出现了术语、译文却没有用首选译名的那几处标成琥珀色。

匹配规则与 :func:`folith.qt.adapter._term_in` 相同：含拉丁字母的按整词、不分大小写，
其余按子串。这里只做「在哪里」，不改任何译文。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QWidget

from . import theme as theme_module


def spans(term: str, text: str) -> list[tuple[int, int]]:
    """``term`` 在 ``text`` 里每一处的 ``(start, end)``。"""
    from folith.terminology import term_spans
    return term_spans(str(term or ''), str(text or ''))


@dataclass(frozen=True)
class Focus:
    """被点开（全文定位）的那条术语。"""

    source: str
    target: str
    forbidden: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass
class Occurrences:
    focus: Focus
    positions: list[int] = field(default_factory=list)       # 网格行号（出现的行）
    inconsistent: set[int] = field(default_factory=set)      # 原文有术语、译文没用首选译名

    def __len__(self) -> int:
        return len(self.positions)


def occurrences(rows, focus: Focus, target_of) -> Occurrences:
    """在网格行里找 ``focus``：原文出现即算一处；``target_of(row)`` 给出这一行的译文。"""
    found = Occurrences(focus)
    for position, row in enumerate(rows):
        if not any(spans(form, getattr(row, 'source', '')) for form in (focus.source, *focus.aliases)):
            continue
        found.positions.append(position)
        target = str(target_of(row) or "")
        if target and focus.target and not spans(focus.target, target):
            found.inconsistent.add(position)
    return found


# ---------------------------------------------------------------- 网格里的高亮 --
def layout_char_rects(layout, ranges, origin: QPointF) -> list[QRectF]:
    """在**已经排好**的 ``QTextLayout`` 上，把 ``ranges`` 映射成矩形（布局坐标 + ``origin``）。

    与 :func:`text_rects` 的区别：这里不重新排版。行距改成 1.5 / 1.7 之后，
    "另建一份 layout 去估" 会把荧光笔画到上一行去——所以绘制方（``style.RowDelegate``）
    把画字用的那份 layout 交给这里，位置天然一致。
    """
    result: list[QRectF] = []
    if not ranges or layout is None:
        return result
    for start, end in ranges:
        if start >= end:
            continue
        for number in range(layout.lineCount()):
            line = layout.lineAt(number)
            first = line.textStart()
            last = first + line.textLength()
            lo, hi = max(start, first), min(end, last)
            if lo >= hi:
                continue
            # cursorToX 收的是**整段文字里的绝对位置**（传相对位置会被夹到行首）。
            x1, _ = line.cursorToX(lo)
            x2, _ = line.cursorToX(hi)
            left = origin.x() + line.x() + min(x1, x2)
            width = max(1.0, abs(x2 - x1))
            result.append(QRectF(left, origin.y() + line.y(), width, line.height()))
    return result


def _painted_layout(option, text: str, widget):
    """绘制方（``style.RowDelegate``）刚画这一格时用的 layout；没有就返回 ``None``。"""
    try:
        delegate = widget.itemDelegate() if widget is not None else None
    except (AttributeError, RuntimeError):
        return None, None
    layout = getattr(delegate, "painted_layout", None)
    if layout is None:
        return None, None
    painted_text = getattr(delegate, "painted_text", None)
    cell = getattr(delegate, "painted_cell_rect", None)
    if painted_text != text or (cell is not None and cell != option.rect):
        return None, None
    text_rect = getattr(delegate, "painted_text_rect", None)
    return layout, text_rect


def text_rects(option, text: str, ranges: list[tuple[int, int]], widget) -> list[QRectF]:
    """``ranges`` 覆盖的矩形（用来在字上铺一层荧光笔）。

    **优先用绘制方刚刚用过的那份 layout**（设置 · 阅读 开了阅读行距之后，行距由
    委托自己排）；没有就退回旧路径：与 Qt 的 ``viewItemDrawText`` 同一套参数
    （文本区由样式给出，左右各留 ``PM_FocusFrameHMargin + 1``，按词换行、顶部对齐）。
    """
    if not ranges or not text:
        return []
    painted, text_rect = _painted_layout(option, text, widget)
    if painted is not None and text_rect is not None:
        return layout_char_rects(painted, ranges, QPointF(text_rect.topLeft()))
    if widget is None:
        return []
    text_area = _style_item_text_rect(option, widget)
    layout = QTextLayout(text, option.font)
    text_option = QTextOption()
    text_option.setWrapMode(QTextOption.WrapMode.WordWrap)
    layout.setTextOption(text_option)
    layout.beginLayout()
    lines = []
    height = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(text_area.width())
        line.setPosition(QPointF(0, height))
        height += line.height()
        lines.append(line)
    layout.endLayout()
    result: list[QRectF] = []
    for start, end in ranges:
        for line in lines:
            first, last = line.textStart(), line.textStart() + line.textLength()
            lo, hi = max(start, first), min(end, last)
            if lo >= hi:
                continue
            x1, _ = line.cursorToX(lo)
            x2, _ = line.cursorToX(hi)
            result.append(QRectF(text_area.left() + x1, text_area.top() + line.y(),
                                 max(1.0, x2 - x1), line.height()))
    return result


def _style_item_text_rect(option, widget) -> QRectF:
    """单元格文字区（与 Qt 画字时同一个口径；定义在 ``style`` 里，避免两处各写一遍）。"""
    from . import style as style_module

    return style_module.item_text_rect(option, widget)

def paint_marks(painter: QPainter, rects: list[QRectF], tone: str) -> None:
    """荧光笔：半透明底色 + 一条底线（在字已经画好之后叠上去）。"""
    tokens = theme_module.tokens(_current_theme())
    base = theme_module.qcolor(tokens["warning" if tone == "warning" else "accent"])
    fill = QColor(base)
    fill.setAlphaF(0.22)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for rect in rects:
        painter.fillRect(rect.adjusted(-1, 0, 1, 0), fill)
        painter.fillRect(QRectF(rect.left() - 1, rect.bottom() - 1.5, rect.width() + 2, 1.5),
                         base)
    painter.restore()


def _current_theme() -> str:
    from . import style

    return style.current_theme()


# ---------------------------------------------------------------- 滚动条标记 ----
class ScrollMarkers(QWidget):
    """贴在纵向滚动条上的位置标记（像代码编辑器里的查找结果）。

    ``marks``：``[(fraction, tone)]``，fraction 是这一行在整张表里的纵向位置（0–1）。
    不接收鼠标事件：滚动条照常拖动。
    """

    def __init__(self, scrollbar) -> None:
        super().__init__(scrollbar)
        self.scrollbar = scrollbar
        self.marks: list[tuple[float, str]] = []
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        scrollbar.installEventFilter(self)

    def set_marks(self, marks: list[tuple[float, str]]) -> None:
        if list(marks) == self.marks and self.isHidden() != bool(self.marks):
            return  # 轮询每秒都会给同样的标记：不重排、不重绘
        self.marks = list(marks)
        self.setVisible(bool(self.marks))
        self._fit()
        self.update()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        from PySide6.QtCore import QEvent

        if watched is self.scrollbar and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._fit()
        return False

    def _fit(self) -> None:
        self.setGeometry(self.scrollbar.rect())
        self.raise_()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        if not self.marks:
            return
        tokens = theme_module.tokens(_current_theme())
        painter = QPainter(self)
        usable = max(1, self.height() - 6)
        for fraction, tone in self.marks:
            colour = theme_module.qcolor(tokens["warning" if tone == "warning" else "accent"])
            y = 3 + round(max(0.0, min(1.0, fraction)) * usable)
            painter.fillRect(QRectF(1, y - 1, self.width() - 2, 3), colour)
        painter.end()
