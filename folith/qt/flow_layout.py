"""放得下就排成一行、放不下就折行的横向布局。

按钮行（操作台、交付页、审校页、分段筛选）共用同一条规则：长语言（俄语、西语）
或窄栏里，一行按钮不该把整页的最小宽度撑到窗口装不下。``QLayout`` 没有现成的
折行版式，所以这里自绘一份，宽度只在真的有控件扩展时才分配多余空间。
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QWidget

from . import theme as theme_module


def _expands(item) -> bool:
    widget = item.widget()
    if widget is None:
        return False
    return widget.sizePolicy().horizontalPolicy() in (
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding,
        QSizePolicy.Policy.Ignored)


class FlowRow(QLayout):
    """一行控件：放得下就排成一行、铺满宽度；放不下就按顺序折到下一行，每行同样铺满。

    操作台（段落检查器）的按钮行用它：长语言（俄语）或窄栏里不再把按钮挤出可见区域，
    最小宽度只是最宽的那一个按钮。多出来的宽度分给横向可扩展的控件，都不可扩展时平分。

    ``fill=False`` 时不铺满：每个控件保持自己的宽度，多出来的空间留在右边。
    交付页和审校页那排操作按钮用它——它们各有各的措辞，铺满会让按钮一样宽、读起来像表格。
    """

    def __init__(self, parent: QWidget | None = None, spacing: int | None = None,
                 *, fill: bool = True) -> None:
        super().__init__(parent)
        self._items = []
        self._fill = bool(fill)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(theme_module.SPACE["xs"] if spacing is None else spacing)

    # QLayout plumbing
    def addItem(self, item) -> None:  # noqa: N802 - Qt naming
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt naming
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt naming
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt naming
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802 - Qt naming
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def _visible(self) -> list:
        return [item for item in self._items if not item.isEmpty()]

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        items = self._visible()
        width = sum(item.sizeHint().width() for item in items) + self.spacing() * max(0, len(items) - 1)
        height = max((item.sizeHint().height() for item in items), default=0)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt naming
        items = self._visible()
        width = max((max(item.minimumSize().width(), item.sizeHint().width()) for item in items),
                    default=0)
        height = max((item.sizeHint().height() for item in items), default=0)
        margins = self.contentsMargins()
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def _arrange(self, rect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        inner = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        space = self.spacing()
        rows: list[list] = []
        row: list = []
        used = 0
        for item in self._visible():
            width = item.sizeHint().width()
            if row and used + space + width > inner.width():
                rows.append(row)
                row, used = [], 0
            used += (space if row else 0) + width
            row.append(item)
        if row:
            rows.append(row)
        y = inner.y()
        rtl = self.parentWidget() is not None and \
            self.parentWidget().layoutDirection() == Qt.LayoutDirection.RightToLeft
        for items in rows:
            height = max(item.sizeHint().height() for item in items)
            if apply:
                widths = [item.sizeHint().width() for item in items]
                extra = max(0, inner.width() - sum(widths) - space * (len(items) - 1))
                grows = [_expands(item) for item in items]
                if not self._fill:
                    grows = [False] * len(items)
                elif not any(grows):
                    grows = [True] * len(items)
                share, remainder = divmod(extra, grows.count(True)) if grows.count(True) else (0, 0)
                x = inner.x()
                for index, item in enumerate(items):
                    width = widths[index]
                    if grows[index]:
                        width += share + (1 if remainder > 0 else 0)
                        remainder = max(0, remainder - 1)
                    width = min(width, inner.width())
                    left = inner.right() - (x - inner.x()) - width + 1 if rtl else x
                    item.setGeometry(QRect(left, y, width, height))
                    x += width + space
            y += height + space
        return max(0, y - space - inner.y()) + margins.top() + margins.bottom()
