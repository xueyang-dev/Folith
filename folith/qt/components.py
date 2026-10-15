"""界面组件库：把设计稿里的"积木"做成可复用的 Qt 控件。

对应设计稿的组件：Sidebar Item、Search Bar、Metric Card、Filter/Category Chip、
Section Card + Card Header Strip、Icon Badge。

约束（与 ``style.py`` 一致）：

* **颜色只来自语义 token**。控件里不出现颜色字面值；图标只携带 token 名
  （见 :mod:`folith.qt.icons`），因此主题切换（日间 / 夜间 / 纸面 / 以后的自定义主题色）
  不需要重建任何控件。
* **QSS 能表达的交给 QSS**（规则在 ``style.build_qss`` 的"组件库"一节）；QSS 画不出来的
  （进度条的圆头、卡片投影、背景渐变）在这里自绘，并且在**绘制时**读当前主题。
* 动态属性避开 ``tone``（那是 :data:`style.CHIP_TONES` 的全局属性选择器）。
"""
from __future__ import annotations

import math
import sys

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QKeySequence, QLinearGradient,
                           QIcon, QPainter, QPainterPath, QPen, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from . import icons
from . import motion
from . import style as style_module
from . import theme as theme_module
from .style import Card, SegmentedControl, repolish

#: 徽标 / 指标图标的语义色调 -> (图标 token)。底色由 QSS 的 ``[badge=…]`` 规则给出。
BADGE_TONES = {
    "accent": "accent",
    "ok": "success",
    "warn": "warning",
    "danger": "danger",
    "info": "info",
    "muted": "text-secondary",
}


def _tokens() -> dict[str, str]:
    return theme_module.tokens(style_module.current_theme())


# --------------------------------------------------------------------------
# Icon badge
# --------------------------------------------------------------------------
class SlidingPanel(QWidget):
    """Inspector and replacement drawer, clipped at their final text widths.

    The splitter resizes this container; its children keep their final widths
    during a transition, so their paragraphs do not wrap on every frame.
    """

    def __init__(self, inspector: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.inspector = inspector
        inspector.setParent(self)
        self.drawer: QWidget | None = None
        self.fraction = 0.0
        self._widths: tuple[int, int] | None = None

    def set_drawer(self, drawer: QWidget) -> None:
        self.drawer = drawer
        drawer.setParent(self)
        drawer.hide()

    def begin(self, inspector_width: int, drawer_width: int) -> None:
        self._widths = (inspector_width, drawer_width)
        self._place()
        self.inspector.show()
        self.drawer.show()
        self.drawer.raise_()

    def set_fraction(self, fraction: float) -> None:
        self.fraction = fraction
        self._place()

    def settle(self, open_: bool) -> None:
        self._widths = None
        self.fraction = float(open_)
        self.inspector.setVisible(not open_)
        self.drawer.setVisible(open_)
        self._place()

    def _place(self) -> None:
        if self.drawer is None:
            self.inspector.setGeometry(self.rect())
            return
        iw, dw = self._widths or (self.width(), self.width())
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        self.inspector.setGeometry(QRect(0 if rtl else self.width() - iw, 0, iw, self.height()))
        offset = round((1.0 - self.fraction) * dw)
        self.drawer.setGeometry(QRect(-offset if rtl else self.width() - dw + offset,
                                      0, dw, self.height()))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()


class ClippedWidget(QWidget):
    """Reveal a fixed-width panel without rewrapping its contents each frame."""

    def __init__(self, content: QWidget, width: int, parent=None) -> None:
        super().__init__(parent)
        self.content, self.content_width = content, width
        content.setParent(self)
        content.show()
        self.resize(width, self.height())

    def freeze(self, width: int | None) -> None:
        self.content_width = width
        self._place()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        width = self.content_width if self.content_width is not None else self.width()
        self.content.setGeometry(self.width() - width if rtl else 0, 0, width, self.height())


class TrailingClip(QWidget):
    """Hold a page at a fixed width while its container resizes, anchored to
    the trailing edge -- the way a macOS sidebar collapses: the page slides out
    from under the sidebar instead of being re-laid out on every frame.

    ``freeze(None)`` lets the page follow the container again.  Size hints come
    from the page, so the window's minimum size is unchanged.
    """

    def __init__(self, content: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.content = content
        self.content_width: int | None = None
        content.setParent(self)
        content.show()

    def freeze(self, width: int | None) -> None:
        self.content_width = width
        self._place()

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.content.sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.content.minimumSizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        width = self.content_width if self.content_width is not None else self.width()
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        # LTR: the sidebar is on the left, so the page keeps its right edge.
        self.content.setGeometry(0 if rtl else self.width() - width, 0, width, self.height())


class ButtonActivity(QObject):
    """Inline activity with an optional delayed, non-modal progress window."""

    def __init__(self, buttons: list, text: str, parent) -> None:
        super().__init__(parent)
        self.active = True
        self.text = text
        self._original = [(b, b.text(), b.icon(), b.iconSize(), b.isEnabled(),
                           b.accessibleDescription()) for b in buttons]
        self._angle = 0
        self._dialog = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._tick()
        self._timer.start()

    def _tick(self) -> None:
        if not motion.reduced():
            self._angle = (self._angle + 18) % 360
        pixmap = QPixmap(36, 36)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(theme_module.qcolor(_tokens()["accent"]), 2,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(3, 3, 12, 12), -self._angle * 16, 270 * 16)
        painter.end()
        for button, *_ in self._original:
            button.setText(self.text)
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(18, 18))
            button.setEnabled(False)
            button.setAccessibleDescription(self.text)

    def show_progress_after(self, milliseconds: int, factory) -> None:
        def show() -> None:
            if self.active:
                self._dialog = factory()
        QTimer.singleShot(milliseconds, self, show)

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        self._timer.stop()
        if self._dialog is not None:
            self._dialog.close()
        for button, text, icon, size, enabled, description in self._original:
            button.setText(text)
            button.setIcon(icon)
            button.setIconSize(size)
            button.setEnabled(enabled)
            button.setAccessibleDescription(description)
        self.deleteLater()


class IconBadge(QWidget):
    """圆形底 + 双色图标（设计稿 Metric Card 左上角那一枚）。

    自绘而不是 QSS：QSS 的 ``border-radius`` 一旦超过半高就退化成直角，圆要自己画。
    """

    #: 语义色调 -> 底色 token
    FILLS = {"accent": "accent-soft", "ok": "success-soft", "warn": "warning-soft",
             "danger": "danger-soft", "info": "info-soft", "muted": "bg-surface-sunken"}

    def __init__(self, icon_name: str, tone: str = "accent", *, size: int = 36,
                 icon_size: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._icon_name = icon_name
        self._icon_size = icon_size
        self._tone = "accent"
        self._icon = icons.icon(icon_name, icon_size, "accent", duotone=True)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        self._tone = tone if tone in BADGE_TONES else "accent"
        self.setProperty("badge", self._tone)
        self._icon = icons.icon(self._icon_name, self._icon_size, BADGE_TONES[self._tone],
                                duotone=True)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme_module.qcolor(_tokens()[self.FILLS[self._tone]]))
        painter.drawEllipse(QRectF(self.rect()))
        side = self._icon_size
        target = self.rect().adjusted((self.width() - side) // 2, (self.height() - side) // 2,
                                      -((self.width() - side + 1) // 2),
                                      -((self.height() - side + 1) // 2))
        self._icon.paint(painter, target, Qt.AlignmentFlag.AlignCenter)
        painter.end()


# --------------------------------------------------------------------------
# Metric cards
# --------------------------------------------------------------------------
class _MetricCard(Card):
    """一张指标卡：图标徽标 + 标签，下面是大号数字（+ 可选的一行补充说明）。"""

    def __init__(self, label: str, icon_name: str, tone: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        space = theme_module.SPACE
        column = QVBoxLayout(self)
        column.setContentsMargins(space["lg"], space["md"], space["lg"], space["md"])
        column.setSpacing(space["sm"])
        self.badge = IconBadge(icon_name, tone, size=32, icon_size=16)
        self.name = QLabel(label)
        self.name.setObjectName("metricLabel")
        head = QHBoxLayout()
        head.setSpacing(space["sm"])
        head.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.name, 1, Qt.AlignmentFlag.AlignVCenter)
        self.value = QLabel("—")
        self.value.setObjectName("metricValue")
        column.addLayout(head)
        column.addWidget(self.value)
        self._shadow_applied = False
        self._apply_shadow()

    def _apply_shadow(self) -> None:
        style_module.apply_card_shadow(self, level="sm")

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 主题切换（QSS 重载）时让投影跟着换：投影色来自 token，不会自己更新。
        if event.type() == QEvent.Type.StyleChange and self.graphicsEffect() is not None:
            if style_module.current_theme() != getattr(self, "_shadow_theme", ""):
                self._shadow_theme = style_module.current_theme()
                self._apply_shadow()
        super().changeEvent(event)


class MetricCards(QFrame):
    """一排指标卡。接口与 :class:`style.StatStrip` 相同（``set_value`` / ``value`` / ``cell``）。

    ``items`` 的每一项是 ``(key, label, icon_name, badge_tone)``。
    ``emphasis`` 只改数字颜色：需要被看见的（运行中 / 有错误）才上色，其余保持正文色。
    """

    def __init__(self, items: list[tuple[str, str, str, str]], *,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("metricRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_module.SPACE["md"])
        self._cards: dict[str, _MetricCard] = {}
        for key, label, icon_name, tone in items:
            card = _MetricCard(label, icon_name, tone, self)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(card, 1)
            self._cards[key] = card

    def set_value(self, key: str, value, *, emphasis: str = "default") -> None:
        card = self._cards.get(key)
        if card is None:
            return
        card.value.setText(str(value))
        if card.value.property("emphasis") != emphasis:
            card.value.setProperty("emphasis", emphasis)
            repolish(card.value)

    def value(self, key: str) -> str:
        card = self._cards.get(key)
        return card.value.text() if card is not None else ""

    def cell(self, key: str):
        return self._cards.get(key)


# --------------------------------------------------------------------------
# Search bar
# --------------------------------------------------------------------------
class SearchBar(QLineEdit):
    """搜索框：前置放大镜、右侧快捷键提示（⌘K），聚焦或有内容时提示自动收起。"""

    def __init__(self, placeholder: str = "", *, shortcut_scope: QWidget | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchBar")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("search", 16, "text-muted"),
                       QLineEdit.ActionPosition.LeadingPosition)
        self.hint = QLabel("⌘ K" if sys.platform == "darwin" else "Ctrl K", self)
        self.hint.setObjectName("keyHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hint.adjustSize()
        self.textChanged.connect(self._sync_hint)
        # Qt 的 "Ctrl" 在 macOS 上就是 ⌘。
        self._shortcut = QShortcut(QKeySequence("Ctrl+K"), shortcut_scope or self)
        self._shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut.activated.connect(self.focus_and_select)
        self._sync_hint()

    def focus_and_select(self) -> None:
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.selectAll()

    def _sync_hint(self, *_args) -> None:
        self.hint.setVisible(not self.text() and not self.hasFocus())
        margin = self.hint.width() + 12 if not self.text() else 0
        self.setTextMargins(0, 0, margin, 0)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self.hint.adjustSize()
        self.hint.move(self.width() - self.hint.width() - 10,
                       (self.height() - self.hint.height()) // 2)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().focusInEvent(event)
        self._sync_hint()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().focusOutEvent(event)
        self._sync_hint()


# --------------------------------------------------------------------------
# Filter chips
# --------------------------------------------------------------------------
class FilterChips(SegmentedControl):
    """药丸形分段（设计稿 Filter/Category Chip、Tab Pills）：选中项是浅色底的小胶囊。"""

    TRACK = "pillTrack"
    BUTTON = "pill"


# --------------------------------------------------------------------------
# Project tabs
# --------------------------------------------------------------------------
#: 项目色点轮换用的 token（按项目 id 稳定取色：同一个项目在哪里都是同一种颜色）。
#: 不用 danger（像报错）、brand-navy（日间近黑、夜间近白）、brand-cyan（白底上太刺眼）。
PROJECT_DOT_TOKENS = ("accent", "success", "warning", "info", "brand-azure")


def project_dot_token(project_id: str) -> str:
    """项目 → 色点 token。未归属（空 id）是次要灰。"""
    if not project_id:
        return "text-muted"
    total = sum((index + 1) * ord(char) for index, char in enumerate(project_id))
    return PROJECT_DOT_TOKENS[total % len(PROJECT_DOT_TOKENS)]


class _ProjectTab(QPushButton):
    """一枚项目标签：色点 + 名称 + 任务数（数字是次要色，所以自己画）。"""

    MAX_WIDTH = 220
    HEIGHT = 30

    def __init__(self, project_id: str, name: str, count: int, parent=None) -> None:
        super().__init__(parent)
        self.project_id = project_id
        self.name = name
        self.count = count
        self.setObjectName("projectTab")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(self.HEIGHT)
        self._hover = False
        self.set_data(name, count)

    def set_data(self, name: str, count: int) -> None:
        self.name, self.count = name, int(count)
        self.setToolTip(name)
        self.setAccessibleName(f"{name} {count}")
        self.setText(name)                     # 测试与无障碍读到的文字
        self.updateGeometry()
        self.update()

    def _bold_font(self):
        font = self.font()
        font.setWeight(font.Weight.DemiBold)
        return font

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        from PySide6.QtGui import QFontMetrics

        name_w = QFontMetrics(self._bold_font()).horizontalAdvance(self.name)
        count_w = QFontMetrics(self.font()).horizontalAdvance(str(self.count))
        width = 12 + 8 + 8 + name_w + 8 + count_w + 12
        return QSize(min(self.MAX_WIDTH, width), self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.sizeHint()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        from PySide6.QtGui import QFontMetrics

        tokens = _tokens()
        checked = self.isChecked()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 9.0
        if checked:
            fill, border = tokens["accent-soft"], tokens["accent-border"]
        elif self._hover:
            fill, border = tokens["bg-hover"], tokens["border-subtle"]
        else:
            fill, border = tokens["bg-surface"], tokens["border-hairline"]
        painter.setPen(QPen(theme_module.qcolor(border), 1))
        painter.setBrush(theme_module.qcolor(fill))
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus():
            painter.setPen(QPen(theme_module.qcolor(tokens["focus-ring"]), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)

        x = 12.0
        mid = rect.center().y()
        dot = theme_module.qcolor(tokens[project_dot_token(self.project_id)]
                                  if self.project_id != ALL_PROJECTS else tokens["accent"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(x + 4, mid), 4, 4)
        x += 16

        count_text = str(self.count)
        count_w = QFontMetrics(self.font()).horizontalAdvance(count_text)
        name_font = self._bold_font() if checked else self.font()
        available = max(0, int(self.width() - x - 8 - count_w - 12))
        name = QFontMetrics(name_font).elidedText(self.name, Qt.TextElideMode.ElideMiddle,
                                                  available)
        painter.setFont(name_font)
        painter.setPen(theme_module.qcolor(tokens["accent" if checked else "text-primary"]))
        name_rect = QRectF(x, rect.top(), available, rect.height())
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         name)
        painter.setFont(self.font())
        painter.setPen(theme_module.qcolor(tokens["accent" if checked else "text-muted"]))
        painter.drawText(QRectF(rect.right() - 12 - count_w, rect.top(), count_w, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, count_text)
        painter.end()


#: :class:`ProjectTabs` 里「全部」那一枚的值。
ALL_PROJECTS = ""


class ProjectTabs(QWidget):
    """任务库顶部的一排项目标签（像浏览器标签页）：全部 / 项目 A / 项目 B / 未归属。

    放不下时横向滚动（滚轮 / 触控板都行，不显示滚动条），选中的那枚始终滚进视野。
    ``changed(project_id)``：``""`` = 全部，``"__none__"`` 由调用方自定义也可以。
    """

    changed = Signal(str)

    def __init__(self, all_label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QButtonGroup, QScrollArea

        self.setObjectName("projectTabs")
        self._all_label = all_label
        self._value = ALL_PROJECTS
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.tabs: dict[str, _ProjectTab] = {}

        self.strip = QWidget()
        self.strip.setObjectName("transparent")
        self._row = QHBoxLayout(self.strip)
        self._row.setContentsMargins(2, 2, 2, 2)
        self._row.setSpacing(theme_module.SPACE["sm"])
        self._row.addStretch(1)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("transparent")
        self.scroll.setWidget(self.strip)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setFixedHeight(_ProjectTab.HEIGHT + 4)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.scroll)
        self.set_projects([])

    def set_projects(self, projects: list[tuple[str, str, int]]) -> None:
        """``projects``：``[(project_id, 名称, 任务数)]``，「全部」由这里补在最前面。"""
        total = sum(count for _pid, _name, count in projects)
        wanted = [(ALL_PROJECTS, self._all_label, total)] + [
            (str(pid), str(name), int(count)) for pid, name, count in projects]
        if [pid for pid, _n, _c in wanted] == list(self.tabs):
            for pid, name, count in wanted:
                self.tabs[pid].set_data(name, count)
        else:
            for tab in self.tabs.values():
                self._group.removeButton(tab)
                self._row.removeWidget(tab)
                tab.hide()  # before detaching (see segment_assist._clear)
                tab.setParent(None)
                tab.deleteLater()
            self.tabs = {}
            for position, (pid, name, count) in enumerate(wanted):
                tab = _ProjectTab(pid, name, count, self.strip)
                tab.clicked.connect(lambda _checked=False, v=pid: self._pick(v))
                self._group.addButton(tab)
                self._row.insertWidget(position, tab)
                self.tabs[pid] = tab
        if self._value not in self.tabs:
            self._value = ALL_PROJECTS
        self.tabs[self._value].setChecked(True)

    def value(self) -> str:
        return self._value

    def set_value(self, project_id: str) -> None:
        project_id = project_id if project_id in self.tabs else ALL_PROJECTS
        self._value = project_id
        self.tabs[project_id].setChecked(True)
        self._reveal(self.tabs[project_id])

    def _pick(self, project_id: str) -> None:
        self._reveal(self.tabs.get(project_id))
        if project_id == self._value:
            return
        self._value = project_id
        self.changed.emit(project_id)

    def _reveal(self, tab) -> None:
        if tab is not None and self.isVisible():
            QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(tab, 24, 0))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # 竖向滚轮 → 横向滚动（鼠标用户没有横向滚轮）。
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta()
            step = delta.x() or delta.y()
            bar = self.scroll.horizontalScrollBar()
            if bar.maximum() > 0 and step:
                bar.setValue(bar.value() - step)
                return True
        return super().eventFilter(watched, event)


# --------------------------------------------------------------------------
# Navigation
# --------------------------------------------------------------------------
def nav_item(label: str, icon_name: str, *, checkable: bool = False,
             icon_size: int = 18) -> QPushButton:
    """侧栏导航项：静止 = 次要色线性图标；选中 = 强调色双色图标 + 左侧强调条（QSS）。"""
    button = QPushButton(label)
    button.setObjectName("navItem")
    button.setCheckable(checkable)
    button.setIcon(icons.icon(icon_name, icon_size, "text-secondary",
                              color_on="accent", duotone_on=True))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    glass_selection(button)
    return button


class _GlassShadow(QObject):
    """选中时给按钮一层柔和投影；主题切换（StyleChange）时按新主题换颜色。"""

    def __init__(self, button: QPushButton) -> None:
        super().__init__(button)
        self.button = button
        button.toggled.connect(self.sync)
        button.installEventFilter(self)
        self.sync()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.StyleChange:
            self.sync()
        return False

    def sync(self, *_args) -> None:
        from PySide6.QtWidgets import QGraphicsDropShadowEffect

        if not self.button.isChecked():
            if self.button.graphicsEffect() is not None:
                self.button.setGraphicsEffect(None)
            return
        effect = self.button.graphicsEffect()
        if not isinstance(effect, QGraphicsDropShadowEffect):
            effect = QGraphicsDropShadowEffect(self.button)
            self.button.setGraphicsEffect(effect)
        night = theme_module.resolve(style_module.current_theme()) == "dark"
        effect.setOffset(0, 2)
        effect.setBlurRadius(16 if night else 14)
        effect.setColor(QColor(0, 0, 0, 110) if night else QColor(30, 41, 70, 34))


def glass_selection(button: QPushButton) -> QPushButton:
    """侧栏导航 / 项目条目：选中时是一片带投影的浅色玻璃（底与亮边在 QSS 里）。"""
    _GlassShadow(button)
    return button


# --------------------------------------------------------------------------
# Section card
# --------------------------------------------------------------------------
class SectionCard(Card):
    """带"头部条"的容器卡：标题 + 右侧操作区，下面是正文（设计稿 Section Card）。"""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent, raised=False)
        self.setObjectName("sectionCard")
        space = theme_module.SPACE
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = QFrame(self)
        self.header.setObjectName("sectionHeader")
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(space["lg"], space["sm"], space["md"], space["sm"])
        self.header_layout.setSpacing(space["sm"])
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        self.header_layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.header_layout.addStretch(1)
        self.body = QWidget(self)
        self.body.setObjectName("transparent")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(0)
        outer.addWidget(self.header)
        outer.addWidget(self.body, 1)

    def add_header_widget(self, widget: QWidget, *, stretch: int = 0) -> None:
        self.header_layout.addWidget(widget, stretch, Qt.AlignmentFlag.AlignVCenter)


# --------------------------------------------------------------------------
# Page canvas with the brand wash
# --------------------------------------------------------------------------
class GradientCanvas(QWidget):
    """主工作区的底：页面色 → 淡淡的品牌蓝（135°，设计稿 Background Gradient）。

    夜间主题的两端相同，所以自然是纯色，不会"发蓝光"。
    """

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        start = theme_module.qcolor(tokens.get("bg-gradient-start", tokens["bg-page"]))
        end = theme_module.qcolor(tokens.get("bg-gradient-end", tokens["bg-page"]))
        rect = QRectF(self.rect())
        gradient = QLinearGradient(QPointF(rect.left(), rect.top()),
                                   QPointF(rect.right(), rect.bottom()))
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)
        painter.end()


# --------------------------------------------------------------------------
# Golden Gate：任务库的小积木（文档缩略图、细进度条、状态点、相对时间）
# --------------------------------------------------------------------------
class DocThumb(QWidget):
    """文档本身：一张纸、一叠纸，或一本书——画得像实物，而不是一个色块。

    * **纸**（PDF 正文页、DOCX、TXT、Markdown）：页面下面垫着几张错开一点的纸，文档越长
      垫得越厚；TXT / Markdown 右上角折一个角。页面上是真实的第一页预览（后台生成，见
      :mod:`folith.qt.thumbnails`），之前先画一张主题内的示意纸。
    * **书**（EPUB，或第一页看起来就是封面的 PDF）：左侧有书脊的压痕与暗部，右侧露出书页的
      切口，封面右侧圆角、左侧几乎是直角。
    * **抬起**（``set_lift``，0–1）：悬停时整份文档上移一点、投影变大变柔，接触阴影变淡；
      按下时略微下沉。动画由卡片驱动（尊重「减少动态效果」）。

    ``faded`` 用于读不出的任务。接口与旧版相同：``set_image`` / ``has_image``。
    """

    PAPER_EXTS = ("pdf", "docx", "doc", "txt", "md", "markdown")
    FOLD_EXTS = ("txt", "md", "markdown")

    def __init__(self, width: int = 74, height: int = 96, *, faded: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width + 16, height + 18)
        self._page = QSize(width, height)
        self._faded = faded
        self._image: QImage | None = None
        self._ext = ""
        self._sheets = 1
        self._lift = 0.0
        self._cover = False
        self._ghost = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    # ---- 数据 ----
    def set_document(self, filename: str, segments: int = 0) -> None:
        """格式决定画成纸还是书；长度决定纸垫几层。"""
        self._ext = str(filename or "").rsplit(".", 1)[-1].lower() if "." in str(filename or "") else ""
        count = int(segments or 0)
        self._sheets = 0 if count < 40 else 1 if count < 400 else 2 if count < 2000 else 3
        self.update()

    def set_image(self, image: QImage | None) -> None:
        """换上真实页面预览（``None`` = 回到示意纸）。"""
        self._image = image if image is not None and not image.isNull() else None
        self._cover = self._image is not None and _looks_like_cover(self._image)
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def is_book(self) -> bool:
        return self._ext == "epub" or (self._cover and self._ext not in ("txt", "md", "markdown", "docx"))

    def set_lift(self, value: float) -> None:
        self._lift = max(-0.4, min(1.0, float(value)))
        self.update()

    def lift(self) -> float:
        return self._lift

    def set_ghost(self, ghost: bool) -> None:
        """飞行中的文档正落向这里：先不画，落地那一刻再出现（同一份文档不出现两份）。"""
        self._ghost = bool(ghost)
        self.update()

    def page_rect(self) -> QRectF:
        """页面在本控件里的位置（含抬起位移）：打开任务的过渡从这里出发。"""
        w, h = self._page.width(), self._page.height()
        rise = 4.0 * self._lift
        return QRectF((self.width() - w) / 2 - 2, self.height() - h - 9 - rise, w, h)

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        if self._ghost:
            return
        tokens = _tokens()
        dark = _night()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._faded:
            painter.setOpacity(0.55)
        page = self.page_rect()
        self._paint_shadow(painter, page, dark)
        if self.is_book():
            self._paint_book(painter, page, tokens, dark)
        else:
            self._paint_paper(painter, page, tokens, dark)
        painter.end()

    def _paint_shadow(self, painter: QPainter, page: QRectF, dark: bool) -> None:
        lift = max(0.0, self._lift)
        strength = 1.6 if dark else 1.0
        # 环境投影：抬得越高，离得越远、越大、越淡。
        for i, (spread, alpha) in enumerate(((12, 8), (8, 12), (5, 18), (2, 26))):
            grow = spread * (1 + 0.8 * lift)
            offset = 2 + 6 * lift + i * 0.4
            colour = QColor(15, 20, 30, int(alpha * strength * (1 - 0.25 * lift)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(page.adjusted(-grow / 2, offset, grow / 2, offset + grow / 3),
                                    4 + grow / 3, 4 + grow / 3)
        # 接触阴影：贴着纸底的一道深色，抬起时变淡变宽。
        contact = QRectF(page.left() + 4, page.bottom() + 2 + 4 * lift, page.width() - 8, 3)
        painter.setBrush(QColor(10, 12, 18, int((70 if dark else 45) * (1 - 0.6 * lift))))
        painter.drawEllipse(contact.adjusted(-6 * lift, 0, 6 * lift, 1 + 2 * lift))

    def _paper_colour(self, tokens: dict, dark: bool) -> QColor:
        return QColor("#dcd8cf") if dark else QColor("#fdfcf8")

    def _paint_paper(self, painter: QPainter, page: QRectF, tokens: dict, dark: bool) -> None:
        paper = self._paper_colour(tokens, dark)
        edge = QColor(0, 0, 0, 38 if not dark else 70)
        # 垫在下面的纸：每层错开一点、略微转一点角度、颜色略深。
        for layer in range(self._sheets, 0, -1):
            painter.save()
            sheet = QRectF(page).translated(2.2 * layer, 1.6 * layer)
            painter.translate(sheet.center())
            painter.rotate((0.9 if layer % 2 else -0.6) * layer)
            painter.translate(-sheet.center())
            painter.setBrush(paper.darker(100 + 4 * layer))
            painter.setPen(QPen(edge, 0.8))
            painter.drawRoundedRect(sheet, 2, 2)
            painter.restore()
        fold = 11.0 if self._ext in self.FOLD_EXTS else 0.0
        path = QPainterPath()
        if fold:
            path.moveTo(page.topLeft())
            path.lineTo(page.right() - fold, page.top())
            path.lineTo(page.right(), page.top() + fold)
            path.lineTo(page.bottomRight())
            path.lineTo(page.bottomLeft())
            path.closeSubpath()
        else:
            path.addRoundedRect(page, 2, 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(paper)
        painter.drawPath(path)
        painter.save()
        painter.setClipPath(path)
        if self._image is not None:
            self._paint_image(painter, page)
        else:
            self._paint_placeholder(painter, page, tokens)
        # 纸面的光：上方极淡的一层亮、下方极淡的一层暗——像一张有厚度的纸。
        sheen = QLinearGradient(page.topLeft(), page.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
        sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.0, QColor(0, 0, 0, 14))
        painter.fillRect(page, sheen)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge, 0.8))
        painter.drawPath(path)
        if fold:
            # 折角：一块折过来的三角，背面颜色略深，带一点阴影。
            corner = QPainterPath()
            corner.moveTo(page.right() - fold, page.top())
            corner.lineTo(page.right() - fold, page.top() + fold)
            corner.lineTo(page.right(), page.top() + fold)
            corner.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 30))
            painter.drawPath(corner.translated(-1, 1))
            back = QLinearGradient(QPointF(page.right() - fold, page.top()),
                                   QPointF(page.right(), page.top() + fold))
            back.setColorAt(0.0, paper.darker(112))
            back.setColorAt(1.0, paper.darker(102))
            painter.setBrush(back)
            painter.drawPath(corner)

    def _paint_book(self, painter: QPainter, page: QRectF, tokens: dict, dark: bool) -> None:
        # 书页切口：封面右侧与底部露出一点米色的书页（几条细线就是一叠纸）。
        block = QRectF(page.left() + 3, page.top() + 2, page.width(), page.height() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#efe8d8") if not dark else QColor("#cfc7b6"))
        painter.drawRoundedRect(block, 2, 2)
        lines = QColor(120, 100, 70, 60)
        for i in range(1, 4):
            x = block.right() - i * 1.1
            painter.fillRect(QRectF(x, block.top() + 2, 0.6, block.height() - 4), lines)
        # 封面：左侧近乎直角（装订处），右侧圆角。
        cover = QPainterPath()
        r = 3.0
        cover.moveTo(page.left(), page.top())
        cover.lineTo(page.right() - r, page.top())
        cover.quadTo(page.right(), page.top(), page.right(), page.top() + r)
        cover.lineTo(page.right(), page.bottom() - r)
        cover.quadTo(page.right(), page.bottom(), page.right() - r, page.bottom())
        cover.lineTo(page.left(), page.bottom())
        cover.closeSubpath()
        painter.save()
        painter.setClipPath(cover)
        if self._image is not None:
            self._paint_image(painter, page, centred=True)
        else:
            painter.fillRect(page, theme_module.qcolor(tokens["accent"]).darker(130))
        # 书脊：左缘暗部 + 一道压痕高光 + 压痕暗线。
        spine = QLinearGradient(page.topLeft(), QPointF(page.left() + page.width() * 0.14, page.top()))
        spine.setColorAt(0.0, QColor(0, 0, 0, 90))
        spine.setColorAt(0.35, QColor(0, 0, 0, 25))
        spine.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(page, spine)
        hinge = page.left() + page.width() * 0.075
        painter.fillRect(QRectF(hinge, page.top(), 0.8, page.height()), QColor(255, 255, 255, 70))
        painter.fillRect(QRectF(hinge + 1.2, page.top(), 0.8, page.height()), QColor(0, 0, 0, 55))
        # 封面的光泽：左上一层斜向的淡光。
        gloss = QLinearGradient(page.topLeft(), page.bottomRight())
        gloss.setColorAt(0.0, QColor(255, 255, 255, 40))
        gloss.setColorAt(0.45, QColor(255, 255, 255, 0))
        painter.fillRect(page, gloss)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 50), 0.8))
        painter.drawPath(cover)

    def _paint_placeholder(self, painter: QPainter, page: QRectF, tokens: dict) -> None:
        w, h = page.width(), page.height()
        ink = QColor(40, 40, 45, 150)
        muted = QColor(60, 60, 70, 70)
        pad = max(8.0, w * 0.12)
        x, y, inner = page.left() + pad, page.top() + pad, w - 2 * pad
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        painter.drawRect(QRectF(x, y, inner * 0.72, max(3.0, h * 0.045)))
        y += h * 0.09
        painter.setBrush(muted)
        painter.drawRect(QRectF(x, y, inner * 0.44, 2))
        y += h * 0.1
        step = max(4.5, h * 0.06)
        while y < page.bottom() - pad:
            painter.drawRect(QRectF(x, y, inner, 1.6))
            y += step

    def _paint_image(self, painter: QPainter, page: QRectF, *, centred: bool = False) -> None:
        """按"铺满纸面"缩放：页面预览靠上对齐（标题在上方），封面居中裁切。"""
        image = self._image
        scale = max(page.width() / image.width(), page.height() / image.height())
        width, height = image.width() * scale, image.height() * scale
        target = QRectF(page.left() + (page.width() - width) / 2, page.top(), width, height)
        if centred or (height > page.height() and image.width() / image.height() < 0.7):
            target.moveTop(page.top() - (height - page.height()) / 2)
        painter.drawImage(target, image)


class CoverArt(QWidget):
    """任务库里的一本「书面」：文档本身就是卡片（2026-10-15 效果图定稿）。

    * 书（EPUB、第一页是封面的 PDF）画封面，左缘一点书脊压痕；纸质文档画第一页的暖白纸面；
    * 底部一层**磨砂玻璃**：同一张图模糊后，从最底下（不透明）渐隐到封面中线（全透明）；
    * 玻璃上再压一层暗色，最底部最重、到中线全透明——文字浮在上面读得清。暗层的浓淡
      按封面底部的亮度自适应：浅色封面压得重一点，深色封面轻一点，避免一条死黑的带子；
    * 外缘一道极细的亮边，像玻璃的边。

    文字（文件名、信息、状态、进度）是放在它上面的真实控件（可省略、可翻译、读屏器能读），
    由 :class:`folith.qt.views._TaskCard` 摆放。接口与 :class:`DocThumb` 兼容：
    ``set_document`` / ``set_image`` / ``has_image`` / ``is_book`` / ``page_rect`` /
    ``set_ghost`` / ``lift``（抬起由卡片移动整个控件实现）。
    """

    RADIUS_BOOK = 7.0
    RADIUS_PAPER = 5.0

    def __init__(self, parent: QWidget | None = None, *, faded: bool = False) -> None:
        super().__init__(parent)
        self._faded = faded
        self._image: QImage | None = None
        self._glass: QImage | None = None
        self._ext = ""
        self._cover = False
        self._ghost = False
        self._lift = 0.0
        self._scrim = 0.7

    # ---- 数据（与 DocThumb 同名） ----
    def set_document(self, filename: str, segments: int = 0) -> None:
        name = str(filename or "")
        self._ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        self.update()

    def set_image(self, image: QImage | None) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self._cover = self._image is not None and _looks_like_cover(self._image)
        self._glass = None
        self._scrim = _bottom_scrim(self._image)
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def is_book(self) -> bool:
        return self._ext == "epub" or (self._cover and self._ext not in ("txt", "md", "markdown", "docx"))

    def page_rect(self) -> QRectF:
        return QRectF(self.rect())

    def set_ghost(self, ghost: bool) -> None:
        self._ghost = bool(ghost)
        self.update()

    def set_lift(self, value: float) -> None:
        self._lift = float(value)

    def lift(self) -> float:
        return self._lift

    def radius(self) -> float:
        return self.RADIUS_BOOK if self.is_book() else self.RADIUS_PAPER

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._glass = None

    # ---- 绘制 ----
    def _source(self) -> QImage:
        """铺满书面的源图（真实封面 / 第一页；还没有时画一张示意纸或纯色封面）。"""
        ratio = self.devicePixelRatioF() or 1.0
        size = QSize(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        canvas = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor("#fdfcf8"))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        target = QRectF(0, 0, size.width(), size.height())
        if self._image is not None:
            image = self._image
            scale = max(target.width() / image.width(), target.height() / image.height())
            w, h = image.width() * scale, image.height() * scale
            top = (target.height() - h) / 2 if self.is_book() else 0.0
            painter.drawImage(QRectF((target.width() - w) / 2, top, w, h), image)
        elif self._ext == "epub":
            painter.fillRect(target, theme_module.qcolor(_tokens()["accent"]).darker(140))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(40, 40, 46, 170))
            pad = target.width() * 0.11
            painter.drawRect(QRectF(pad, pad, target.width() * 0.56, target.height() * 0.028))
            painter.setBrush(QColor(60, 60, 70, 80))
            y = pad + target.height() * 0.08
            while y < target.height() * 0.92:
                painter.drawRect(QRectF(pad, y, target.width() - 2 * pad, max(1.0, ratio)))
                y += target.height() * 0.028
        painter.end()
        return canvas

    def _glass_image(self, source: QImage) -> QImage:
        """磨砂玻璃：模糊后的同一张图，用一条从底部（不透明）到中线（透明）的遮罩裁出来。"""
        if self._glass is not None and self._glass.size() == source.size():
            return self._glass
        factor = 14
        small = source.scaled(max(1, source.width() // factor), max(1, source.height() // factor),
                              Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        glass = small.scaled(source.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied)
        mask = QImage(glass.size(), QImage.Format.Format_ARGB32_Premultiplied)
        mask.fill(Qt.GlobalColor.transparent)
        painter = QPainter(mask)
        fade = QLinearGradient(0, mask.height(), 0, mask.height() * 0.5)
        fade.setColorAt(0.0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.55, QColor(0, 0, 0, 200))
        fade.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(mask.rect(), fade)
        painter.end()
        painter = QPainter(glass)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, mask)
        painter.end()
        self._glass = glass
        return glass

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        if self._ghost:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._faded:
            painter.setOpacity(0.55)
        rect = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(rect, self.radius(), self.radius())
        painter.setClipPath(clip)
        source = self._source()
        painter.drawImage(rect, source)
        if self.is_book():
            spine = QLinearGradient(rect.topLeft(), QPointF(rect.left() + rect.width() * 0.12, rect.top()))
            spine.setColorAt(0.0, QColor(0, 0, 0, 80))
            spine.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(rect, spine)
            painter.fillRect(QRectF(rect.left() + rect.width() * 0.07, rect.top(), 0.8, rect.height()),
                             QColor(255, 255, 255, 55))
        painter.drawImage(rect, self._glass_image(source))
        scrim = QLinearGradient(rect.bottomLeft(), QPointF(rect.left(), rect.top() + rect.height() * 0.5))
        scrim.setColorAt(0.0, QColor(0, 0, 8, int(255 * self._scrim)))
        scrim.setColorAt(0.45, QColor(0, 0, 8, int(255 * self._scrim * 0.54)))
        scrim.setColorAt(1.0, QColor(0, 0, 8, 0))
        painter.fillRect(rect, scrim)
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60 if _night() else 80), 1))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self.radius(), self.radius())
        painter.end()


def _bottom_scrim(image: QImage | None) -> float:
    """封面底部越亮，暗层越重（0.45–0.72）：白纸和浅色封面也读得清，深色封面不压成死黑。"""
    if image is None:
        return 0.68
    small = image.scaled(12, 16, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    values = [QColor(small.pixel(x, y)).lightnessF()
              for y in range(small.height() // 2, small.height()) for x in range(small.width())]
    light = sum(values) / max(1, len(values))
    return 0.45 + 0.27 * max(0.0, min(1.0, (light - 0.2) / 0.7))


class DocumentFlight(QWidget):
    """打开 / 关闭任务时，文档从卡片「飞」到工作台（或飞回卡片）的那一下。

    只是一张截图在窗口最上层移动、缩放、淡出：不挡鼠标、不改任何布局，结束就删掉。
    打开：从卡片上的位置放大到内容区，后 40% 淡出——像拿起一份文件、凑近、翻开。
    关闭：反过来，从内容区缩回卡片，最后 30% 淡入到卡片上。减少动态效果时不出现。
    """

    def __init__(self, window: QWidget, pixmap: QPixmap, start: QRectF, end: QRectF, *,
                 opening: bool, on_done=None, land: bool = False) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(window.rect())
        self._pixmap = pixmap
        self._start, self._end = QRectF(start), QRectF(end)
        self._opening = opening
        self._land = land          # 终点是同一份文档的真实位置：全程不透明，落地交接
        self._t = 0.0
        self._motion = motion.Motion(self)
        self.show()
        self.raise_()

        def finished() -> None:
            self.hide()
            self.deleteLater()
            if on_done is not None:
                on_done()

        self._motion.animate("flight", 0.0, 1.0, self._step, kind="view", finished=finished)

    def _step(self, value: float) -> None:
        self._t = float(value)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        t = self._t
        rect = QRectF(self._start.x() + (self._end.x() - self._start.x()) * t,
                      self._start.y() + (self._end.y() - self._start.y()) * t,
                      self._start.width() + (self._end.width() - self._start.width()) * t,
                      self._start.height() + (self._end.height() - self._start.height()) * t)
        if self._land:
            opacity = 1.0
        elif self._opening:
            opacity = 1.0 if t < 0.6 else max(0.0, 1.0 - (t - 0.6) / 0.4)
        else:
            opacity = min(1.0, 0.35 + t)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(opacity)
        # 投影随飞行高度变化：中段最高。
        height = math.sin(math.pi * t)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 15, 25, int(40 * height * opacity)))
        painter.drawRoundedRect(rect.adjusted(-6 * height, 8 * height, 6 * height, 14 * height), 8, 8)
        painter.drawPixmap(rect, self._pixmap, QRectF(self._pixmap.rect()))
        painter.end()


def _looks_like_cover(image: QImage) -> bool:
    """第一页像封面（大面积颜色或深色），而不是白底黑字的正文页。"""
    small = image.scaled(16, 20, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    total = sat = dark = 0
    for y in range(small.height()):
        for x in range(small.width()):
            colour = QColor(small.pixel(x, y))
            total += 1
            sat += colour.hsvSaturationF()
            dark += colour.valueF() < 0.45
    return total > 0 and (sat / total > 0.2 or dark / total > 0.35)


def _night() -> bool:
    from . import style

    return style.night_mode()


class ProgressLine(QWidget):
    """4px 圆头细进度条（QSS 的 QProgressBar 画不出圆头 + 0 高度的轨道）。"""

    def __init__(self, value: int = 0, *, token: str = "accent",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._value = max(0, min(100, int(value)))
        self._token = token

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = max(0, min(100, int(value)))
        self.update()

    def set_token(self, token: str) -> None:
        self._token = token
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(QColor(255, 255, 255, 60) if getattr(self, "on_glass", False)
                         else theme_module.qcolor(tokens["bg-surface-sunken"]))
        painter.drawRoundedRect(rect, 2, 2)
        if self._value:
            painter.setBrush(theme_module.qcolor(tokens[self._token]))
            width = rect.width() * self._value / 100
            x = rect.width() - width if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else 0
            painter.drawRoundedRect(QRectF(x, 0, width, rect.height()), 2, 2)
        painter.end()


class StatusDot(QWidget):
    """7px 状态圆点（实心 = 有状态，空心环 = 待处理/排队）。颜色在绘制时按 token 取。"""

    def __init__(self, token: str = "accent", *, hollow: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._token = token
        self._hollow = hollow

    def token(self) -> str:
        return self._token

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = theme_module.qcolor(_tokens().get(self._token, _tokens()["text-muted"]))
        rect = QRectF(0.75, 0.75, 6.5, 6.5)
        if self._hollow:
            from PySide6.QtGui import QPen

            painter.setPen(QPen(colour, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
        painter.drawEllipse(rect)
        painter.end()


def relative_time(stamp: str, *, now=None) -> str:
    """ISO 时间戳 → "刚刚 / N 分钟前 / N 小时前 / N 天前 / 日期"。

    任务卡右上角的"多久没动了"。解析失败时原样返回前 16 个字符（不吞掉信息）。
    """
    from datetime import datetime

    from . import i18n

    text = str(stamp or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text.replace("T", " ")[:16]
    if now is None:
        now = datetime.now(moment.tzinfo) if moment.tzinfo else datetime.now()
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return i18n.tr('刚刚')
    if seconds < 3600:
        return i18n.tr('{v0} 分钟前', v0=int(seconds // 60))
    if seconds < 86400:
        return i18n.tr('{v0} 小时前', v0=int(seconds // 3600))
    if seconds < 86400 * 14:
        return i18n.tr('{v0} 天前', v0=int(seconds // 86400))
    return moment.strftime("%Y-%m-%d")


__all__ = [
    "BADGE_TONES", "DocThumb", "FilterChips", "GradientCanvas", "IconBadge", "MetricCards",
    "ProgressLine", "SearchBar", "SectionCard", "StatusDot", "nav_item", "relative_time",
]


# --------------------------------------------------------------------------
# Settings rows (macOS System Settings style)
# --------------------------------------------------------------------------
class Switch(style_module.CheckBox):
    """iOS / macOS 风格的开关：药丸形轨道 + 圆形滑块。

    仍然是 ``QCheckBox``（``isChecked`` / ``setChecked`` / ``stateChanged`` 不变），
    只是画法不同；文字不画在开关上——设置行自己的标题就是它的标签
    （无障碍名由 :meth:`setAccessibleName` 提供）。
    """

    TRACK = QSize(38, 22)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setFixedSize(self.TRACK)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(self.TRACK)

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt 命名
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        if not self.isEnabled():
            fill = tokens["disabled-bg"]
        else:
            fill = tokens["accent"] if on else tokens["border-strong"]
        painter.setBrush(theme_module.qcolor(fill))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob = track.height() - 4
        x = track.right() - 2 - knob if on else track.left() + 2
        painter.setBrush(theme_module.qcolor(tokens["switch-knob"]))
        painter.drawEllipse(QRectF(x, track.top() + 2, knob, knob))
        if self.hasFocus():
            pen = painter.pen()
            ring = theme_module.qcolor(tokens["accent"])
            ring.setAlpha(120)
            from PySide6.QtGui import QPen

            painter.setPen(QPen(ring, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
            painter.setPen(pen)
        painter.end()


# --------------------------------------------------------------------------
# Run progress (docs/qt-beta/acceptance-feedback-2026-10-14.md §6, §11)
# --------------------------------------------------------------------------

#: Run tone → fill token.  Colour is never the only signal: the run status line
#: beside the track always says the same thing in words.
PROGRESS_TONES = {
    "running": "accent", "waiting": "accent", "retrying": "warning",
    "stalled": "warning", "failed": "danger", "paused": "text-muted",
    "done": "success", "idle": "text-muted",
}


class ProgressTrack(QWidget):
    """A native-looking progress bar (macOS NSProgressIndicator, bar style).

    Rounded, slightly sunken track; fill in the run's tone colour with a soft top
    highlight.  ``waiting`` / ``retrying`` / an indeterminate range show a slow
    moving sheen so a long model call never looks frozen; with reduced motion
    the sheen is not drawn.  The value API mirrors ``QProgressBar``
    (``setRange`` / ``setValue`` / ``minimum`` / ``maximum`` / ``value``;
    ``setRange(0, 0)`` means indeterminate).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._minimum, self._maximum, self._value = 0, 100, 0
        self._tone = "running"
        self._previous_tone = "running"
        self._tone_mix = 1.0
        self._display_value = 0.0
        self._stage = None
        self._snap_next = True
        self._motion = motion.Motion(self)
        self._phase = 0.0
        self._sheen = QTimer(self)
        self._sheen.setInterval(33)
        self._sheen.timeout.connect(self._advance)

    # QProgressBar-compatible value API --------------------------------
    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802 - Qt naming
        changed = (self._minimum, self._maximum) != (int(minimum), max(int(minimum), int(maximum)))
        self._minimum, self._maximum = int(minimum), max(int(minimum), int(maximum))
        self._value = max(self._minimum, min(self._value, self._maximum))
        if changed:
            self._motion.stop("value")
            self._display_value = float(self._value)
            self._snap_next = True
        self._sync_sheen()
        self.update()

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt naming
        previous = self._value
        self._value = max(self._minimum, min(int(value), self._maximum))
        if self._value == previous and not self._snap_next:
            return
        if self._snap_next or self._value < previous:
            self._motion.stop("value")
            self._set_display(float(self._value))
        else:
            self._motion.animate("value", self._display_value, float(self._value),
                                 self._set_display, kind="progress")
        self._snap_next = False

    def set_stage(self, stage) -> None:
        if stage != self._stage:
            self._stage = stage
            self._motion.stop("value")
            self._snap_next = True

    def _set_display(self, value: float) -> None:
        self._display_value = min(float(self._value), value)
        self.update()

    def minimum(self) -> int:
        return self._minimum

    def maximum(self) -> int:
        return self._maximum

    def value(self) -> int:
        return self._value

    def indeterminate(self) -> bool:
        return self._maximum == self._minimum == 0

    def fraction(self) -> float | None:
        span = self._maximum - self._minimum
        return None if span <= 0 else (self._value - self._minimum) / span

    def set_tone(self, tone: str) -> None:
        tone = tone if tone in PROGRESS_TONES else "running"
        if tone != self._tone:
            self._previous_tone, self._tone = self._tone, tone
            self._sync_sheen()
            self._motion.animate("tone", 0, 1, self._set_tone_mix, kind="progress")

    def _set_tone_mix(self, value: float) -> None:
        self._tone_mix = value
        self.update()

    def tone(self) -> str:
        return self._tone

    # -------------------------------------------------------------------
    def _wants_sheen(self) -> bool:
        return (self.indeterminate() or self._tone in ("waiting", "retrying")) \
            and not motion.reduced()

    def _sync_sheen(self) -> None:
        if self._wants_sheen() and self.isVisible():
            if not self._sheen.isActive():
                self._sheen.start()
        else:
            self._sheen.stop()

    def _advance(self) -> None:
        speed = 0.008 if self._tone == "retrying" else 0.016
        self._phase = (self._phase + speed) % 1.0
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self._sync_sheen()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self._sheen.stop()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        # Track: sunken surface, a hairline darker at the top (the groove).
        painter.setBrush(theme_module.qcolor(tokens["bg-surface-sunken"]))
        painter.drawRoundedRect(rect, radius, radius)
        groove = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        shade = QColor(0, 0, 0, 28)
        groove.setColorAt(0.0, shade)
        groove.setColorAt(0.5, QColor(0, 0, 0, 0))
        painter.setBrush(groove)
        painter.drawRoundedRect(rect, radius, radius)
        # Fill.
        colour = theme_module.qcolor(tokens[PROGRESS_TONES.get(self._tone, "accent")])
        previous = theme_module.qcolor(tokens[PROGRESS_TONES[self._previous_tone]])
        colour = QColor.fromRgbF(*[a + (b - a) * self._tone_mix for a, b in zip(
            previous.getRgbF(), colour.getRgbF())])
        span = self._maximum - self._minimum
        share = None if span <= 0 else (self._display_value - self._minimum) / span
        if share is None:
            share = 1.0 if self._tone in ("failed", "done", "stalled") else 0.0
        width = rect.width() * max(0.0, min(1.0, share))
        if width > 0:
            if width < rect.height():
                width = rect.height()  # keep the rounded cap visible
            fill = QRectF(rect.right() - width if rtl else rect.left(), rect.top(),
                          width, rect.height())
            body = QLinearGradient(fill.topLeft(), fill.bottomLeft())
            body.setColorAt(0.0, colour.lighter(118))
            body.setColorAt(1.0, colour)
            painter.setBrush(body)
            painter.drawRoundedRect(fill, radius, radius)
        # Sheen: a soft band sweeping across (indeterminate or waiting).
        if self._wants_sheen():
            band = rect.width() * 0.35
            start = -band + (rect.width() + band) * self._phase
            x = rect.right() - start - band if rtl else rect.left() + start
            sheen = QLinearGradient(QPointF(x, 0), QPointF(x + band, 0))
            glow = QColor(colour if width <= 0 else QColor(255, 255, 255))
            glow.setAlpha(0)
            peak = QColor(glow)
            peak.setAlpha(90 if width > 0 else 120)
            sheen.setColorAt(0.0, glow)
            sheen.setColorAt(0.5, peak)
            sheen.setColorAt(1.0, glow)
            painter.setBrush(sheen)
            clip = QPainterPath()
            clip.addRoundedRect(rect, radius, radius)
            painter.setClipPath(clip)
            painter.drawRect(QRectF(x, rect.top(), band, rect.height()))
        painter.end()


# --------------------------------------------------------------------------
# Popover
# --------------------------------------------------------------------------

class Popover(QFrame):
    """A light popover anchored under a control (run log, task details, …).

    ``Qt.Popup``: a click outside or Esc closes it — the system behaviour, no
    global click filter.  ✕ closes it too.  It is placed below the anchor,
    right-aligned with it, and kept inside the anchor's screen.
    """

    closed = Signal()

    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None, *,
                 size: QSize = QSize(440, 380)) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(size)
        self._popup_size = QSize(size)
        self._motion = motion.Motion(self)
        self._closing = False
        self._accept_close = False
        self._anchor_rect = QRect()
        self.body = QWidget(self)
        self.body.resize(size)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("hint")
        self.subtitle_label.setVisible(False)
        self.close_button = QToolButton()
        self.close_button.setObjectName("roundButton")
        self.close_button.setIcon(icons.icon("x", 14, "text-secondary"))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setAccessibleName(title)
        self.close_button.setToolTip(QKeySequence("Esc").toString())
        self.close_button.clicked.connect(self.close)
        head_text = QVBoxLayout()
        head_text.setContentsMargins(0, 0, 0, 0)
        head_text.setSpacing(0)
        head_text.addWidget(self.title_label)
        head_text.addWidget(self.subtitle_label)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addLayout(head_text, 1)
        head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                                  theme_module.SPACE["md"], theme_module.SPACE["md"])
        layout.setSpacing(theme_module.SPACE["sm"])
        layout.addLayout(head)
        self.content = content
        layout.addWidget(content, 1)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def open_at(self, anchor: QWidget) -> None:
        """Show below ``anchor``, right edges aligned, clamped to its screen."""
        self.resize(self._popup_size)
        below = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + 6))
        x, y = below.x() - self.width(), below.y()
        if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
            x = anchor.mapToGlobal(QPoint(0, 0)).x()
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
            if y + self.height() > area.bottom() - 8:
                y = max(area.top() + 8,
                        anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6)
        target = QRect(x, y, self._popup_size.width(), self._popup_size.height())
        self._anchor_rect = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
        self._closing = False
        self._accept_close = False
        self._motion.stop("popup")
        self.body.setGeometry(QRect(QPoint(0, 0), target.size()))
        self.body.hide()
        self.show()
        self.raise_()

        def ready():
            self.body.show()
            if self.isVisible() and QApplication.activePopupWidget() is self:
                self.close_button.setFocus(Qt.FocusReason.PopupFocusReason)

        self._animate_geometry(self._anchor_rect, target, "popover", ready)

    def _animate_geometry(self, start, end, kind, finished):
        def frame(value):
            self.setGeometry(QRect(*[round(a + (b - a) * value) for a, b in zip(
                (start.x(), start.y(), start.width(), start.height()),
                (end.x(), end.y(), end.width(), end.height()))]))
        self._motion.animate("popup", 0, 1, frame, kind=kind, finished=finished)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._accept_close or motion.reduced() or not self.isVisible():
            super().closeEvent(event)
            return
        event.ignore()
        if self._closing:
            return
        self._closing = True
        self.body.hide()

        def finish():
            self._accept_close = True
            self.close()

        self._animate_geometry(self.geometry(), self._anchor_rect, "popover-close", finish)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._motion.stop("popup")
        super().hideEvent(event)
        self.closed.emit()
