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
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QPushButton, QSizePolicy, QSplitter, QSplitterHandle, QToolButton,
                               QVBoxLayout, QWidget)

from . import platform_ui, i18n
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
        if self.drawer is not None:
            self.drawer.show()
            self.drawer.raise_()

    def set_fraction(self, fraction: float) -> None:
        self.fraction = fraction
        self._place()

    def settle(self, open_: bool) -> None:
        self._widths = None
        open_ = bool(open_) and self.drawer is not None
        self.fraction = float(open_)
        self.inspector.setVisible(not open_)
        if self.drawer is not None:
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
        # 投影由 Card（style.Surface）自己画，不再叠 QGraphicsDropShadowEffect。


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


class DropTile(QFrame):
    """任务卡片末尾的虚线「拖入文档」格：拖进来先告诉你能不能放，放下后给出结果。

    五个状态（``state`` 属性，QSS 按它换色）：
    ``idle`` 虚线 + 加号；``ready`` 文件拖到了页面上、且至少有一个能导入——强调色
    实线 + 「松开即可导入」；``reject`` 拖来的全是不支持的格式——红色 + 原因；
    ``busy`` 已放下、正在导入；``done`` / ``error`` 导入结果（几秒后回到 idle）。
    它只是显示：接收与导入由页面完成。点一下 = 打开系统选择面板。
    """

    clicked = Signal()
    #: 状态变成需要被看见的（可以放 / 不能放 / 结果）：页面把它滚进视野。
    attentionNeeded = Signal()
    WIDTH, HEIGHT = 168, 212

    def __init__(self, formats: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropTile")
        # 默认尺寸只是起点：放进任务库时由外层占位格撑满，和封面一样大。
        self.resize(self.WIDTH, self.HEIGHT)
        self.setMinimumSize(120, 160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(i18n.tr('拖入文档'))
        self._formats = formats
        self._idle = (i18n.tr('拖入文档'), formats)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(6)
        column.addStretch(1)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setObjectName("dropTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName("dropDetail")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        column.addWidget(self.icon)
        column.addWidget(self.title)
        column.addWidget(self.detail)
        column.addStretch(1)
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.timeout.connect(self.set_idle)
        self.set_idle()

    def _show(self, state: str, icon_name: str, tone: str, title: str, detail: str,
              *, revert_ms: int = 0) -> None:
        self._reset.stop()
        self.setProperty("state", state)
        self.icon.setPixmap(icons.icon(icon_name, 22, tone).pixmap(22, 22))
        self.title.setText(title)
        self.detail.setText(detail)
        self.setAccessibleDescription(f"{title} {detail}")
        repolish(self)
        repolish(self.title)
        if revert_ms:
            self._reset.start(revert_ms)
        if state != "idle":
            self.attentionNeeded.emit()

    def state(self) -> str:
        return str(self.property("state") or "idle")

    def set_idle(self) -> None:
        self._show("idle", "plus", "accent", *self._idle)

    def set_ready(self, count: int) -> None:
        self._show("ready", "upload", "accent", i18n.tr('松开即可导入'),
                   i18n.tr('{v0} 个文档', v0=count) if count > 1 else self._formats)

    def set_reject(self, names: list[str]) -> None:
        self._show("reject", "alert", "danger", i18n.tr('这种格式不能导入'),
                   i18n.tr('支持 {v0}', v0=self._formats))

    def set_busy(self, count: int) -> None:
        self._show("busy", "upload", "accent", i18n.tr('正在导入…'),
                   i18n.tr('{v0} 个文档', v0=count))

    def show_result(self, imported: list[str], failed: list[str], skipped: list[str] = ()) -> None:
        """放下之后的结果：成功几个（点名前两个）、哪些没成、哪些格式不支持。"""
        lines = []
        if imported:
            names = "、".join(imported[:2]) + (i18n.tr(' 等') if len(imported) > 2 else "")
            lines.append(names)
        if failed:
            lines.append(i18n.tr('{v0} 个导入失败', v0=len(failed)))
        if skipped:
            lines.append(i18n.tr('{v0} 个格式不支持', v0=len(skipped)))
        if imported and not (failed or skipped):
            self._show("done", "check-circle", "success",
                       i18n.tr('已导入 {v0} 个', v0=len(imported)), "\n".join(lines),
                       revert_ms=6000)
        elif imported:
            self._show("warn", "alert", "warning",
                       i18n.tr('已导入 {v0} 个', v0=len(imported)), "\n".join(lines),
                       revert_ms=8000)
        else:
            self._show("error", "alert", "danger", i18n.tr('没有导入'),
                       "\n".join(lines), revert_ms=8000)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _GlanceTile(QFrame):
    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("glanceTile")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 8, 14, 8)
        column.setSpacing(0)
        self.name = QLabel(label)
        self.name.setObjectName("glanceLabel")
        self.value = QLabel("—")
        self.value.setObjectName("glanceValue")
        self.hint = QLabel("")
        self.hint.setObjectName("glanceHint")
        column.addWidget(self.name)
        column.addWidget(self.value)
        column.addWidget(self.hint)


class GlanceStrip(QFrame):
    """E 层次 v2：一眼概览。四格数字嵌在一条下沉的凹槽里（没有四张各自的卡）。

    接口与 :class:`MetricCards` 相同（``set_value`` / ``value`` / ``cell``），多一个
    ``hint``：数字下面一行说明。凹槽比页面深一档、格子不带描边——凹进去的东西
    不抢卡片的层次。
    """

    def __init__(self, items: list[tuple[str, str]], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("glanceStrip")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self._tiles: dict[str, _GlanceTile] = {}
        for key, label in items:
            tile = _GlanceTile(label, self)
            tile.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(tile, 1)
            self._tiles[key] = tile

    def set_value(self, key: str, value, *, emphasis: str = "default", hint: str = "") -> None:
        tile = self._tiles.get(key)
        if tile is None:
            return
        tile.value.setText(str(value))
        tile.hint.setText(hint)
        if tile.value.property("emphasis") != emphasis:
            tile.value.setProperty("emphasis", emphasis)
            repolish(tile.value)

    def value(self, key: str) -> str:
        tile = self._tiles.get(key)
        return tile.value.text() if tile is not None else ""

    def cell(self, key: str):
        return self._tiles.get(key)


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
        self.hint = QLabel(platform_ui.shortcut_text("search"), self)
        self.hint.setObjectName("keyHint")
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hint.adjustSize()
        self.textChanged.connect(self._sync_hint)
        # Qt 的 "Ctrl" 在 macOS 上就是 ⌘。
        self._shortcut = QShortcut(platform_ui.shortcut("search"), shortcut_scope or self)
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

    # Windows font metrics are wider than macOS for the same point size: the
    # Spanish "Todos los proyectos" is 228 px at DemiBold here, so the old 220 px
    # cap elided the selected tab even with the bold-width size hint.  Keep the
    # cap about one long label wide rather than exactly fitting one platform.
    MAX_WIDTH = 320
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

        # 量宽用**画出来的那只字体**：选中时是半粗体，比常规体宽几个像素，
        # 按常规体量宽会让标签名被中间省略（「All …ects」）。再给省略号留一点
        # 余量，长名字被省略时也不会正好卡在边上。
        name_font = self._bold_font() if self.isChecked() else self.font()
        metrics = QFontMetrics(name_font)
        name_w = (metrics.horizontalAdvance(self.name)
                  + metrics.horizontalAdvance("…"))
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
        radius = float(theme_module.RADIUS["md"])
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
    button = style_module.ElideButton(label)
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
        effect.setOffset(0, 1)
        effect.setBlurRadius(4)
        effect.setColor(QColor(0, 0, 0, 50) if night else QColor(30, 41, 70, 16))


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
class Sheet(QWidget):
    """E 层次 v2：内容页是浮在"外壳"上的一张圆角纸。

    外壳（窗口底 + 侧栏）是一层更深的灰；内容页是它上面更亮的一张 14px 圆角面，
    带三层柔和的投影。三层表面（外壳 < 页面 < 卡片）就是整个界面的层次来源，
    页面里不再靠描边分区。夜间主题同样适用：外壳更深、页面更亮一点。
    """

    RADIUS = 14
    GAP = 8

    def __init__(self, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.content = content
        self._lead = 0
        self.setObjectName("transparent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, self.GAP, self.GAP, self.GAP)
        layout.setSpacing(0)
        layout.addWidget(content)

    def set_leading_gap(self, gap: int) -> None:
        """侧栏收起时（工作台），页面左边也留出与右边相同的间隙，圆角不贴窗口边。"""
        gap = max(0, min(self.GAP, int(gap)))
        if gap == self._lead:
            return
        self._lead = gap
        self.layout().setContentsMargins(gap, self.GAP, self.GAP, self.GAP)
        self.update()

    def leading_gap(self) -> int:
        return self._lead

    def _sheet_rect(self) -> QRectF:
        return QRectF(self._lead, self.GAP, self.width() - self.GAP - self._lead,
                      self.height() - 2 * self.GAP)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        from PySide6.QtGui import QPainterPath, QRegion

        path = QPainterPath()
        path.addRoundedRect(QRectF(self.content.rect()), self.RADIUS, self.RADIUS)
        self.content.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self._sheet_rect()
        dark = tokens.get("bg-page", "#fff").lower() < "#808080"
        painter.setPen(Qt.PenStyle.NoPen)
        for grow, offset, alpha in ((14, 8, 6), (7, 3, 8), (2, 1, 12)):
            ink = style_module.shadow_ink()
            ink.setAlpha(int(alpha * (2.2 if dark else 1.0)))
            painter.setBrush(ink)
            painter.drawRoundedRect(rect.adjusted(-grow / 2, -grow / 4 + offset, grow / 2, grow / 2 + offset),
                                    self.RADIUS + grow / 3, self.RADIUS + grow / 3)
        painter.end()


class WindowDragArea(QObject):
    """把一块空白区域变成"标题栏"：按住拖动整个窗口，双击缩放（macOS 的习惯）。

    窗口内容伸进了透明标题栏（见 ``MainWindow._extend_shell_under_title_bar``），系统
    标题栏的拖动区域被内容挡住了，所以由这里接管。只处理落在该控件本身（或子控件没有
    接收、冒泡上来）的按下；按钮、输入框等自己接收了的点击不受影响。``top_band``
    给出时，只有这条高度以内算拖动区域（外壳的其余部分是侧栏背景）。
    """

    def __init__(self, widget: QWidget, *, top_band=None) -> None:
        super().__init__(widget)
        self._widget = widget
        self._top_band = top_band
        widget.installEventFilter(self)

    def _inside(self, event) -> bool:
        if self._top_band is None:
            return True
        return event.position().y() < self._top_band()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        # 拆除过程中仍可能收到事件，那时 Python 这一半的属性可能已经不在了。
        if watched is not getattr(self, "_widget", None):
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton \
                and self._inside(event):
            handle = self._widget.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                return True
        elif kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton \
                and self._inside(event):
            window = self._widget.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            return True
        return False


def _expands(item) -> bool:
    widget = item.widget()
    if widget is None:
        return False
    return widget.sizePolicy().horizontalPolicy() in (
        QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Ignored)


class FlowRow(QLayout):
    """一行按钮：放得下就排成一行、铺满宽度；放不下就按顺序折到下一行，每行同样铺满。

    操作台（段落检查器）的按钮行用它：长语言（俄语）或窄栏里不再把按钮挤出可见区域，
    最小宽度只是最宽的那一个按钮。多出来的宽度分给横向可扩展的控件，都不可扩展时平分。

    ``fill=False`` 时不铺满：每个控件保持自己的宽度，多出来的空间留在右边。
    交付页那排操作按钮用它——它们各有各的措辞，铺满会让四个按钮一样宽、读起来像表格。
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


class ShellCanvas(QWidget):
    """窗口最底的一层（外壳）：比页面深一档的灰，纵向微渐变。"""

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        start = theme_module.qcolor(tokens.get("bg-shell-start", tokens["bg-glass"]))
        end = theme_module.qcolor(tokens.get("bg-shell-end", tokens["bg-glass"]))
        rect = QRectF(self.rect())
        gradient = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)
        painter = QPainter(self)
        painter.fillRect(self.rect(), gradient)
        painter.end()


class GradientCanvas(QWidget):
    """主工作区的底：页面色 → 淡淡的品牌蓝（135°，设计稿 Background Gradient）。

    夜间主题的两端相同，所以自然是纯色，不会"发蓝光"。
    """

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        start = theme_module.qcolor(tokens.get("bg-gradient-start", tokens["bg-page"]))
        end = theme_module.qcolor(tokens.get("bg-gradient-end", tokens["bg-page"]))
        rect = QRectF(self.rect())
        gradient = QLinearGradient(QPointF(0, rect.top()), QPointF(0, rect.bottom()))
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
        # 磨砂跟着主题走（色彩审校 C4）：夜间是暗层配白字；浅色主题是**浅色**磨砂配
        # 深色字——白纸文档上压一层近黑，在浅色首页上就是一块发灰发脏的深色带。
        if _night():
            base, strength = QColor(0, 0, 8), self._scrim
        else:
            base, strength = theme_module.qcolor(_tokens()["bg-surface"]), 0.88
        for stop, share in ((0.0, 1.0), (0.45, 0.62), (1.0, 0.0)):
            colour = QColor(base)
            colour.setAlpha(int(255 * strength * share))
            scrim.setColorAt(stop, colour)
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
        if getattr(self, "on_glass", False):
            # 玻璃上的轨道：夜间暗玻璃用半透明白，浅色磨砂用分隔线色。
            track = (QColor(255, 255, 255, 60) if _night()
                     else theme_module.qcolor(tokens["border-divider"]))
        else:
            track = theme_module.qcolor(tokens["bg-surface-sunken"])
        painter.setBrush(track)
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


class StatusLabel(QWidget):
    """界面里唯一的状态样子：6px 圆点 + 同色文字（theme.STATUS_TONES 五种语气）。

    任务卡、继续卡、段落检查器、术语候选、设置里的连接状态都用它——状态不再一处是
    芯片、一处是彩字、一处是彩色横幅。
    """

    def __init__(self, text: str = "", tone: str = "idle", parent: QWidget | None = None,
                 *, wrap: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("transparent")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.dot = StatusDot(theme_module.STATUS_TONES["idle"])
        # 单行省略（卡片、设置里的短状态）；wrap=True 时折行（检查器里的整句状态），
        # 圆点对齐第一行文字的中线。
        if wrap:
            self.label = style_module.ParagraphLabel(text)
        else:
            self.label = style_module.ElideLabel(text, mode=Qt.TextElideMode.ElideRight)
        style_module.set_text_role(self.label, "status")
        #: 折行模式下状态短语之后的说明（次要色，不着色）——只有状态本身带语气色。
        self.detail = None
        if wrap:
            dot_box = QVBoxLayout()
            dot_box.setContentsMargins(0, max(0, (self.label.fontMetrics().height() - 8) // 2 + 1), 0, 0)
            dot_box.addWidget(self.dot)
            dot_box.addStretch(1)
            row.addLayout(dot_box)
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(2)
            column.addWidget(self.label)
            self.detail = style_module.ParagraphLabel("")
            style_module.set_text_role(self.detail, "meta")
            self.detail.setVisible(False)
            column.addWidget(self.detail)
            row.addLayout(column, 1)
        else:
            row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(self.label, 1, Qt.AlignmentFlag.AlignVCenter)
        self._tone = "idle"
        self.set_status(text, tone)

    # QLabel 兼容：原来用纯标签的地方（setText / text）不用改调用方式，语气保持不变。
    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self.label.setText(text)

    def setWordWrap(self, on: bool) -> None:  # noqa: N802 - Qt 命名
        if isinstance(self.label, QLabel) and not isinstance(self.label, style_module.ElideLabel):
            self.label.setWordWrap(on)

    def set_tone(self, tone: str) -> None:
        self.set_status(self.label.text(), tone)

    def set_status(self, text: str, tone: str = "idle", detail: str = "") -> None:
        tone = tone if tone in theme_module.STATUS_TONES else "idle"
        self._tone = tone
        if self.dot._token != theme_module.STATUS_TONES[tone]:
            self.dot._token = theme_module.STATUS_TONES[tone]
            self.dot.update()
        self.label.setText(text)
        self.label.setProperty("tone", tone)
        repolish(self.label)
        if self.detail is not None:
            self.detail.setText(detail)
            self.detail.setVisible(bool(detail))

    def tone(self) -> str:
        return self._tone

    def text(self) -> str:
        detail = self.detail.text() if self.detail is not None else ""
        return self.label.text() + (("\n" + detail) if detail else "")


def status_dot_icon(tone: str):
    """表格单元格里用的状态圆点（``QIcon``）：与 :class:`StatusLabel` 同色同大小。"""
    from PySide6.QtGui import QIcon, QPixmap

    token = theme_module.STATUS_TONES.get(tone, theme_module.STATUS_TONES["idle"])
    ratio = 2
    pixmap = QPixmap(8 * ratio, 8 * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme_module.qcolor(_tokens().get(token, _tokens()["text-muted"])))
    painter.drawEllipse(QRectF(1, 1, 6, 6))
    painter.end()
    return QIcon(pixmap)


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
    "ProgressLine", "SearchBar", "SectionCard", "StatusDot", "StatusLabel", "nav_item", "relative_time",
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


class LoadingOverlay(QWidget):
    """Covers a page during background loading, with actual phase progress.

    Paint immediately before dispatch, then update as read phases finish. Avoid
    a continuous shimmer: it caused window flicker in earlier native trials.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.hide()
        column = QVBoxLayout(self)
        column.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.setSpacing(theme_module.SPACE["sm"])
        self._icon = icons.IconLabel("clock", 22, "accent")
        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._track = ProgressTrack()
        self._track.setRange(0, 0)
        self._track.setFixedWidth(220)
        column.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._label, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(self._track, 0, Qt.AlignmentFlag.AlignHCenter)

    def present(self, message: str) -> None:
        """Show over the whole parent and paint now (do not wait for the event loop)."""
        self._label.setText(message)
        self._track.setAccessibleName(message)
        self._track.setRange(0, 0)
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self.repaint()      # no processEvents: a queued poll timer must not re-enter the open

    def dismiss(self) -> None:
        self.hide()

    def set_progress(self, completed: int, total: int) -> None:
        self._track.setRange(0, total)
        self._track.setValue(completed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        colour = theme_module.qcolor(_tokens()["bg-surface"])
        colour.setAlpha(240)
        painter.fillRect(self.rect(), colour)


class ProgressTrack(QWidget):
    """A native-looking progress bar (macOS NSProgressIndicator, bar style).

    Rounded, slightly sunken track; fill in the run's tone colour with a soft top
    highlight.  It never animates on its own: a running sheen repainted the window
    ~30 times a second for the whole of every model wait, which made the macOS menu
    bar and the Stage Manager thumbnail flicker (trial feedback 2026-10-16).  The
    status line under it says what is happening and counts the seconds instead.
    The value API mirrors ``QProgressBar``
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

    # QProgressBar-compatible value API --------------------------------
    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802 - Qt naming
        changed = (self._minimum, self._maximum) != (int(minimum), max(int(minimum), int(maximum)))
        self._minimum, self._maximum = int(minimum), max(int(minimum), int(maximum))
        self._value = max(self._minimum, min(self._value, self._maximum))
        if changed:
            self._motion.stop("value")
            self._display_value = float(self._value)
            self._snap_next = True
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
            self._motion.animate("tone", 0, 1, self._set_tone_mix, kind="progress")

    def _set_tone_mix(self, value: float) -> None:
        self._tone_mix = value
        self.update()

    def tone(self) -> str:
        return self._tone

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
        painter.end()


# --------------------------------------------------------------------------
# Popover
# --------------------------------------------------------------------------

def tab_path(rect: QRectF, radius: float) -> QPainterPath:
    """「凸」字上半截的外形：上面两个圆角，底边是直的（下面接浮层）。"""
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + radius)
    path.arcTo(QRectF(rect.left(), rect.top(), 2 * radius, 2 * radius), 180, -90)
    path.lineTo(rect.right() - radius, rect.top())
    path.arcTo(QRectF(rect.right() - 2 * radius, rect.top(), 2 * radius, 2 * radius), 90, -90)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path


def tab_outline(rect: QRectF, radius: float) -> QPainterPath:
    """同一外形的发丝线：左、上、右三面，底边敞开。"""
    rect = rect.adjusted(0.5, 0.5, -0.5, 0)
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom() + 1)
    path.lineTo(rect.left(), rect.top() + radius)
    path.arcTo(QRectF(rect.left(), rect.top(), 2 * radius, 2 * radius), 180, -90)
    path.lineTo(rect.right() - radius, rect.top())
    path.arcTo(QRectF(rect.right() - 2 * radius, rect.top(), 2 * radius, 2 * radius), 90, -90)
    path.lineTo(rect.right(), rect.bottom() + 1)
    return path


#: 「凸」字周围的柔和投影：向上、向左右散开的几层（外扩，不透明度）。
TAB_SHADOW = ((1.0, 0.09), (2.0, 0.05), (3.0, 0.025))


def paint_tab_shadow(painter: QPainter, rect: QRectF, radius: float) -> None:
    ink = style_module.shadow_ink()
    gain = theme_module.ELEVATION_NIGHT_GAIN if style_module.night_mode() else 1.0
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    for spread, alpha in TAB_SHADOW:
        colour = QColor(ink)
        colour.setAlphaF(min(1.0, alpha * gain))
        painter.setBrush(colour)
        painter.drawPath(tab_path(rect.adjusted(-spread, -spread, spread, 0), radius + spread))
    painter.restore()


def outline_colour() -> QColor:
    """浮层与「凸」字共用的发丝线颜色。"""
    return theme_module.qcolor(_tokens()["border-subtle"])


class Popover(QFrame):
    """A light popover anchored under a control (run log, task details, …).

    ``Qt.Popup``: a click outside or Esc closes it — the system behaviour, no
    global click filter.  ✕ closes it too.  It is placed below the anchor,
    right-aligned with it, and kept inside the anchor's screen.
    """

    closed = Signal()

    def __init__(self, title: str, content: QWidget, parent: QWidget | None = None, *,
                 size: QSize = QSize(440, 380), header: bool = True) -> None:
        # 不要系统窗口阴影：投影是自己画的。macOS 的阴影按第一帧（展开动画里那一小条）
        # 算出来再跟着窗口拉伸，刚展开时卡片外面会套一圈灰框（试用反馈的照片）。
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("popover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(size)
        self._popup_size = QSize(size)
        self._motion = motion.Motion(self)
        self._closing = False
        self._accept_close = False
        self._anchor_rect = QRect()
        #: 展开它的那个控件（``tether=True`` 时）：浮层画成从它底边长出来的一块。
        self._tether: QWidget | None = None
        self._neck = (0.0, 0.0)
        self.body = QWidget(self)
        self.body.resize(size)
        self.title_label = QLabel(title, self.body)
        self.title_label.setObjectName("sectionTitle")
        self.subtitle_label = QLabel("", self.body)
        self.subtitle_label.setObjectName("hint")
        self.subtitle_label.setVisible(False)
        self.close_button = QToolButton(self.body)
        self.close_button.setObjectName("roundButton")
        self.close_button.setIcon(icons.icon("x", 14, "text-secondary"))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setAccessibleName(title)
        self.close_button.setToolTip(QKeySequence("Esc").toString())
        self.close_button.clicked.connect(self.close)
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                                  theme_module.SPACE["md"], theme_module.SPACE["md"])
        layout.setSpacing(theme_module.SPACE["sm"])
        if header:
            head_text = QVBoxLayout()
            head_text.setContentsMargins(0, 0, 0, 0)
            head_text.setSpacing(0)
            head_text.addWidget(self.title_label)
            head_text.addWidget(self.subtitle_label)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.addLayout(head_text, 1)
            head.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(head)
        else:
            # 小卡（侧栏连接浮窗）：内容自己说清楚是什么，点外面或 Esc 关闭，不要 ×。
            for widget in (self.title_label, self.subtitle_label, self.close_button):
                widget.setParent(self.body)
                widget.hide()
            layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"] + 2,
                                      theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        self.content = content
        layout.addWidget(content, 1)

    #: 系着锚点时：颈部高度、颈部与面板相接处的内凹圆角。
    TETHER_NECK = 6
    TETHER_FILLET = 6

    def _tether_path(self) -> tuple[QPainterPath, QRectF]:
        """颈部（锚点宽）+ 面板（浮层宽），相接处是内凹圆角——一整块，像从锚点底边长出来。"""
        neck, fillet = self.TETHER_NECK, self.TETHER_FILLET
        radius = theme_module.RADIUS["md"]
        panel = QRectF(self.rect()).adjusted(9, neck, -9, -11)
        # 展开动画的头几帧窗口还很矮：面板至少保持圆角的高度（超出部分被窗口裁掉）。
        panel.setHeight(max(panel.height(), 2 * radius + 1))
        left, right = self._neck
        # 锚点贴着卡片的边（例如顶栏最右的 ⓘ，卡片被屏幕挡住不能居中）：那一侧颈部
        # 与卡片的边对齐成一条直线，卡片那个角是直角，不再有内凹圆角——否则颈部被
        # 夹窄，和上半截对不上，看起来是断开的。
        flush_left = left <= panel.left() + radius + fillet
        flush_right = right >= panel.right() - radius - fillet
        if flush_left:
            left = panel.left()
        if flush_right:
            right = panel.right()
        path = QPainterPath()
        path.addRoundedRect(panel, radius, radius)
        for flush, x in ((flush_left, panel.left()), (flush_right, panel.right() - radius)):
            if flush:
                corner = QPainterPath()
                corner.addRect(QRectF(x, panel.top(), radius, radius))
                path = path.united(corner)
        stem = QPainterPath()
        stem.addRect(QRectF(left, -1, right - left, neck + radius + 1))
        path = path.united(stem)
        for x, side in ((left, -1), (right, 1)):
            if (side < 0 and flush_left) or (side > 0 and flush_right):
                continue
            corner = QPainterPath()
            square = QRectF(x if side > 0 else x - fillet, neck - fillet, fillet, fillet)
            corner.addRect(square)
            hole = QPainterPath()
            centre = QPointF(x + side * fillet, neck - fillet)
            hole.addEllipse(centre, fillet, fillet)
            path = path.united(corner.subtracted(hole))
        return path, panel

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._tether is not None:
            path, panel = self._tether_path()
            painter.setPen(Qt.PenStyle.NoPen)
            style_module.paint_elevation(painter, panel, theme_module.RADIUS["md"], "floating")
            # 颈部两侧接着「凸」字上半截的投影往下走（上沿在窗口外，被裁掉）。
            left, right = self._neck
            paint_tab_shadow(painter, QRectF(left, -40, right - left, 40 + self.TETHER_NECK),
                             theme_module.RADIUS["md"])
            painter.setBrush(theme_module.qcolor(_tokens()["bg-surface-raised"]))
            painter.drawPath(path)
            painter.setPen(QPen(outline_colour(), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.end()
            return
        rect = QRectF(self.rect()).adjusted(9, 7, -9, -11)
        radius = theme_module.RADIUS["md"]
        painter.setPen(Qt.PenStyle.NoPen)
        style_module.paint_elevation(painter, rect, radius, "floating")
        painter.setBrush(theme_module.qcolor(_tokens()["bg-surface-raised"]))
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()

    def set_popup_size(self, size: QSize) -> None:
        """下次打开时的大小（例如按内容决定高度）。"""
        self._popup_size = QSize(size)

    def body_margin(self, tether: bool) -> QSize:
        """浮层外框比内容多出来的宽、高（投影留白 + 内边距；系着锚点时还有颈部）。"""
        if tether:
            pad = theme_module.SPACE["md"]
            return QSize(2 * (9 + pad), self.TETHER_NECK + pad + 11 + pad)
        return QSize(20, 20)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def open_at(self, anchor: QWidget, *, align: str = "right", tether: bool = False,
                flush: str = "") -> None:
        """Show below ``anchor`` (above it near the screen bottom), clamped to its screen.

        ``align="right"`` lines up the right edges (toolbar buttons); ``"left"`` lines up
        the left edges (the sidebar's connection row, whose card grows into the page);
        ``"center"`` centres it under the anchor.

        ``tether=True``: no gap — the popover grows out of the anchor's bottom edge.  The
        anchor gets ``tethered=true`` (QSS fills it with the popover's surface and squares
        its bottom corners) so the two read as one shape, and the popover unfolds downwards.
        """
        self.resize(self._popup_size)
        gap = 0 if tether else 6
        below = anchor.mapToGlobal(QPoint(anchor.width(), anchor.height() + gap))
        x, y = below.x() - self.width(), below.y()
        left_edge = (align == "left") != (self.layoutDirection() == Qt.LayoutDirection.RightToLeft)
        origin = anchor.mapToGlobal(QPoint(0, 0))
        if align == "center":
            x = origin.x() + anchor.width() // 2 - self.width() // 2
        elif left_edge:
            x = origin.x() - 10
        if tether:
            y -= 1  # 盖住锚点底边那 1px，接缝处不漏底色
        screen = anchor.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
            if not tether and y + self.height() > area.bottom() - 8:
                y = max(area.top() + 8, origin.y() - self.height() - 6)
        target = QRect(x, y, self._popup_size.width(), self._popup_size.height())
        self._set_tether(anchor if tether else None)
        if tether:
            # 颈部与锚点画出来的「凸」字上半截同宽（锚点四边内缩 TAB_INSET 留给投影）。
            inset = float(getattr(anchor, "TAB_INSET", 0))
            # 卡片被屏幕边挡住、锚点离卡片的边比圆角还近：把卡片挪到边与锚点的上半截
            # 对齐（画成一条直线，见 _tether_path），而不是把颈部夹窄。
            pad, reach = 9, theme_module.RADIUS["md"] + self.TETHER_FILLET
            tab_left, tab_right = origin.x() + inset, origin.x() + anchor.width() - inset
            if flush == "right":
                # 固定样式：卡片右边与上半截右边对齐成一条直线（顶栏最右的 ⓘ）。
                x = round(tab_right - target.width() + pad)
            elif tab_right > x + target.width() - pad - reach:
                x = round(tab_right - target.width() + pad)
            elif tab_left < x + pad + reach:
                x = round(tab_left - pad)
            target.moveLeft(x)
            self._neck = (tab_left - x, tab_right - x)
            self._anchor_rect = QRect(x, y, target.width(), self.TETHER_NECK + 1)
        else:
            self._anchor_rect = QRect(origin, anchor.size())
        self._closing = False
        self._accept_close = False
        self._motion.stop("popup")
        inner = QRect(QPoint(0, 0), target.size())
        if tether:
            inner = inner.adjusted(9 + theme_module.SPACE["md"], self.TETHER_NECK + theme_module.SPACE["md"],
                                   -9 - theme_module.SPACE["md"], -11 - theme_module.SPACE["md"])
        else:
            inner = inner.adjusted(10, 10, -10, -10)
        self.body.setGeometry(inner)
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
        # Rapid input can finish synchronously. Qt suppresses a nested close()
        # inside this closeEvent, so accept the original event in that case.
        if self._accept_close:
            event.accept()

    def _set_tether(self, anchor: QWidget | None) -> None:
        previous, self._tether = self._tether, anchor
        for widget, value in ((previous, False), (anchor, True)):
            if widget is not None:
                widget.setProperty("tethered", value)
                style_module.repolish(widget)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._motion.stop("popup")
        super().hideEvent(event)
        self._set_tether(None)
        self.closed.emit()


# --------------------------------------------------------------------------
# Scroll edge fade
# --------------------------------------------------------------------------
class ScrollEdgeFade(QObject):
    """Fade a scroll area's content out at an edge it can still scroll past.

    The fade is an opacity mask on the viewport, not a painted colour band, so
    it works over glass, cards and every theme without knowing the background.
    At rest against an edge that edge is sharp; with nothing to scroll the
    effect is removed entirely and the viewport paints as before.
    """

    SIZE = 24
    #: Alpha ramp across the fade, eased (t ** 1.5) so the edge has no visible step.
    STOPS = tuple((i / 6, (i / 6) ** 1.5) for i in range(7))

    def __init__(self, area, size: int = SIZE) -> None:
        super().__init__(area)
        self._area = area
        self._size = size
        self._edges: tuple[bool, bool] | None = None
        bar = area.verticalScrollBar()
        bar.valueChanged.connect(self.refresh)
        bar.rangeChanged.connect(self.refresh)
        area.viewport().installEventFilter(self)
        self.refresh()

    def edges(self) -> tuple[bool, bool]:
        """(top, bottom): which edges are currently faded."""
        return self._edges or (False, False)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Resize:
            self._edges = None
            self.refresh()
        return False

    def refresh(self, *_args) -> None:
        bar = self._area.verticalScrollBar()
        edges = (bar.value() > bar.minimum(), bar.value() < bar.maximum())
        if edges == self._edges:
            return
        self._edges = edges
        viewport = self._area.viewport()
        effect = viewport.graphicsEffect()
        ours = effect if getattr(effect, "_edge_fade", False) else None
        if not any(edges):
            if ours is not None:
                viewport.setGraphicsEffect(None)  # Qt deletes the effect
            return
        if effect is not None and ours is None:
            return  # someone else's effect owns this viewport
        if ours is None:
            from PySide6.QtWidgets import QGraphicsOpacityEffect

            ours = QGraphicsOpacityEffect(viewport)
            ours._edge_fade = True
            viewport.setGraphicsEffect(ours)
        height = max(1, viewport.height())
        size = min(self._size, height / 3) / height
        gradient = QLinearGradient(0, 0, 0, height)
        black = QColor(0, 0, 0)
        for at, alpha in self.STOPS:
            if edges[0]:
                black.setAlphaF(alpha)
                gradient.setColorAt(at * size, QColor(black))
            if edges[1]:
                black.setAlphaF(alpha)
                gradient.setColorAt(1 - at * size, QColor(black))
        black.setAlphaF(1.0)
        if not edges[0]:
            gradient.setColorAt(0, QColor(black))
        if not edges[1]:
            gradient.setColorAt(1, QColor(black))
        ours.setOpacity(1.0)
        ours.setOpacityMask(gradient)


def scroll_fade(area, size: int = ScrollEdgeFade.SIZE) -> ScrollEdgeFade:
    """Install :class:`ScrollEdgeFade` on ``area`` (a QAbstractScrollArea)."""
    return ScrollEdgeFade(area, size)


# --------------------------------------------------------------------------
# Resize splitter
# --------------------------------------------------------------------------
class _ResizeHandle(QSplitterHandle):
    """A splitter handle with a dead zone, Esc-to-cancel and double-click."""

    #: Pointer travel (px) before a press becomes a drag: a click or the first
    #: half of a double-click must not nudge the columns.
    DEAD_ZONE = 4

    def __init__(self, orientation, splitter) -> None:
        super().__init__(orientation, splitter)
        self._press: QPoint | None = None
        self._dragging = False
        self.cancelled = False

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._dragging = False
            self.cancelled = False
            self.splitter()._drag_began(self)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._press is None or self.cancelled:
            return
        if not self._dragging:
            travel = event.globalPosition().toPoint() - self._press
            if max(abs(travel.x()), abs(travel.y())) < self.DEAD_ZONE:
                return
            self._dragging = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            dragged = self._dragging and not self.cancelled
            self._press = None
            self._dragging = False
            self.splitter()._drag_ended(self, dragged)
        if not self.cancelled:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            splitter = self.splitter()
            splitter.resetRequested.emit(splitter.indexOf(self))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ResizeSplitter(QSplitter):
    """QSplitter with deliberate drags.

    * a 4 px dead zone, so clicks never move the columns;
    * Esc during a drag puts every pane back where the drag started;
    * double-click on a handle emits :attr:`resetRequested` (handle index) so
      the owner can return that pane to its default width;
    * a pane given a collapse threshold, dragged and released narrower than
      it, emits :attr:`collapseRequested` with its width before the drag --
      the owner closes it through its own toggle and remembers that width.
    """

    resetRequested = Signal(int)
    collapseRequested = Signal(QWidget, int)

    def __init__(self, orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._thresholds: dict[QWidget, int] = {}
        self._start_sizes: list[int] | None = None
        self._handle: _ResizeHandle | None = None

    def createHandle(self):  # noqa: N802 - Qt naming
        return _ResizeHandle(self.orientation(), self)

    def set_collapse_threshold(self, widget: QWidget, width: int) -> None:
        self._thresholds[widget] = int(width)

    def dragging(self) -> bool:
        return self._handle is not None

    def cancel_drag(self) -> bool:
        """Undo the drag in progress; returns False when there is none."""
        handle, start = self._handle, self._start_sizes
        if handle is None or start is None:
            return False
        handle.cancelled = True
        self.setSizes(start)
        index = self.indexOf(handle)
        position = handle.pos().x() if self.orientation() == Qt.Orientation.Horizontal \
            else handle.pos().y()
        self.splitterMoved.emit(position, index)  # listeners re-read the widths
        self._release()
        return True

    def _drag_began(self, handle: _ResizeHandle) -> None:
        self._release()
        self._handle = handle
        self._start_sizes = self.sizes()
        QApplication.instance().installEventFilter(self)

    def _drag_ended(self, handle: _ResizeHandle, dragged: bool) -> None:
        start = self._start_sizes or []
        self._release()
        if not dragged:
            return
        sizes = self.sizes()
        for widget, width in self._thresholds.items():
            index = self.indexOf(widget)
            if index < 0 or not widget.isVisibleTo(self) or index >= len(start):
                continue
            if sizes[index] < width <= start[index]:
                self.collapseRequested.emit(widget, start[index])
                return

    def _release(self) -> None:
        if self._handle is not None:
            QApplication.instance().removeEventFilter(self)
        self._handle = None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        kind = event.type()
        if self._handle is not None and kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) \
                and event.key() == Qt.Key.Key_Escape:
            event.accept()  # the workbench's own Esc shortcut must not fire mid-drag
            if kind == QEvent.Type.KeyPress:
                self.cancel_drag()
            return True
        return False


# ---------------------------------------------------------------------------
# 表单：分组 + 分组行（设计审校 40 号第三节：全应用只用这一种表单写法）
# ---------------------------------------------------------------------------

#: 分组行的左缩进：12 内边距 + 16 图标 + 12 间距。发丝线与折叠体都从这里开始。
FORM_ROW_INSET = 40
#: 没有图标的行：发丝线从内边距开始。
FORM_ROW_INSET_PLAIN = 12
#: 分组行最小高度（标题一行时；带说明的行自然长高）。
FORM_ROW_HEIGHT = 44


class OptionCard(QPushButton):
    """可点选的选项卡片：（图标 +）标题，下面一行细节（阶段 1 画布：视觉与识别、默认翻译方式）。

    选中 = 强调色描边 + 强调色标题；不可用 = 浅底灰字，细节行里可以放链接（如「如何安装」）。
    QPushButton 的 sizeHint 看不见内部布局，所以用布局的尺寸覆盖它。
    """

    def __init__(self, key: str, title: str, detail: str = "", icon: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("optionCard")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setAccessibleName(title)
        self.key = str(key)
        self._icon_name = icon
        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["md"] + 2, theme_module.SPACE["sm"] + 2,
                                  theme_module.SPACE["md"] + 2, theme_module.SPACE["sm"] + 2)
        column.setSpacing(theme_module.SPACE["xxs"])
        head = QHBoxLayout()
        head.setSpacing(theme_module.SPACE["xs"] + 2)
        # 子控件一开始就带父窗口：没有父窗口时 setVisible(True) 会让它闪成顶层小窗。
        self.icon_label = QLabel(self)
        self.icon_label.setVisible(bool(icon))
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("optionTitle")
        head.addWidget(self.icon_label)
        head.addWidget(self.title_label, 1)
        self.detail_label = QLabel(detail, self)
        self.detail_label.setObjectName("optionDetail")
        self.detail_label.setTextFormat(Qt.TextFormat.RichText)
        # 长语言（俄、阿）里细节行会比卡片宽：折行，而不是把整页撑出横向滚动。
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumWidth(1)
        self.detail_label.setVisible(bool(detail))
        column.addLayout(head)
        column.addWidget(self.detail_label)
        self.toggled.connect(self._sync)
        self._sync()

    def set_detail(self, html: str) -> None:
        self.detail_label.setText(html)
        self.detail_label.setVisible(bool(html))
        self.updateGeometry()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync()
        elif event.type() == QEvent.Type.StyleChange:
            QTimer.singleShot(0, self._sync)      # 主题名在样式表之后才改

    def _sync(self, *_args) -> None:
        # 标题颜色在这里写：QSS 的「父控件 :checked 下的子标签」在夜间主题里不可靠。
        title = ("accent" if self.isChecked() else "text-primary") if self.isEnabled() \
            else "text-muted"
        self.title_label.setStyleSheet(f"color: {style_module.html_color(title)};")
        if not self._icon_name:
            return
        token = ("accent" if self.isChecked() else "text-secondary") if self.isEnabled() \
            else "text-muted"
        self.icon_label.setPixmap(icons.icon(self._icon_name, 15, token).pixmap(15, 15))

    def setText(self, value: str) -> None:  # noqa: N802 - 文案由内部标签承担
        return

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt 命名
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt 命名
        return self.layout().totalHeightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        hint = self.layout().sizeHint()
        return QSize(min(hint.width(), 220), self.heightForWidth(min(hint.width(), 220)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return QSize(96, self.layout().minimumSize().height())


class AccentPicker(QWidget):
    """一排主题色点（色点 + 名称）：选中的那个外面多一圈同色描边（阶段 1 画布）。"""

    changed = Signal(str)
    DOT = 20

    def __init__(self, options: list[tuple[str, str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["lg"])
        self._buttons: dict[str, QPushButton] = {}
        for key, label, colour in options:
            button = QPushButton(label)
            button.setObjectName("accentOption")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(self._dot_icon(colour))
            button.setIconSize(QSize(self.DOT + 6, self.DOT + 6))
            button.toggled.connect(lambda on, value=key: on and self.changed.emit(value))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch(1)

    def _dot_icon(self, colour: str) -> QIcon:
        icon = QIcon()
        size = self.DOT + 6
        for state, ring in ((QIcon.State.Off, False), (QIcon.State.On, True)):
            ratio = 2
            pixmap = QPixmap(size * ratio, size * ratio)
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            fill = theme_module.qcolor(colour)
            if ring:
                painter.setPen(QPen(fill, 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(1, 1, size - 2, size - 2))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(QRectF(3 + (0 if ring else 0), 3, size - 6, size - 6))
            painter.end()
            icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
        return icon

    def value(self) -> str:
        return next((key for key, button in self._buttons.items() if button.isChecked()), "")

    def set_value(self, value: str) -> None:
        button = self._buttons.get(value)
        if button is not None and not button.isChecked():
            button.blockSignals(True)
            button.setChecked(True)
            button.blockSignals(False)

    def button(self, value: str) -> QPushButton | None:
        return self._buttons.get(value)


class FormRow(QWidget):
    """一行设置：（图标）+ 标题（+ 一行说明）+（取值）+ 右侧控件。

    左边永远是"这是什么"，右边永远是"现在是什么 / 改它"。图标统一用 ``text-muted``：
    它只帮眼睛定位，不表达状态；颜色留给当前选择、需要注意的提示和主行动。
    """

    def __init__(self, title: str, *controls: QWidget, icon: str = "",
                 hint: str = "", value: QWidget | None = None, tooltip: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("groupRow")
        self.setMinimumHeight(FORM_ROW_HEIGHT)
        self.inset = FORM_ROW_INSET if icon else FORM_ROW_INSET_PLAIN
        sp = theme_module.SPACE
        layout = QHBoxLayout(self)
        layout.setContentsMargins(sp["md"], sp["xs"] + 2, sp["md"], sp["xs"] + 2)
        layout.setSpacing(sp["md"])
        if icon:
            layout.addWidget(icons.IconLabel(icon, 16, "text-muted"), 0,
                             Qt.AlignmentFlag.AlignVCenter)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("rowTitle")
        self.hint_label: QLabel | None = None
        stretch = 0 if value is not None else 1
        if hint:
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(sp["xxs"])
            column.addWidget(self.title_label)
            self.hint_label = QLabel(hint)
            self.hint_label.setObjectName("hint")
            self.hint_label.setWordWrap(True)
            column.addWidget(self.hint_label)
            layout.addLayout(column, stretch)
        else:
            layout.addWidget(self.title_label, stretch, Qt.AlignmentFlag.AlignVCenter)
        if value is not None:
            layout.addWidget(value, 1, Qt.AlignmentFlag.AlignVCenter)
        for control in controls:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        if tooltip:
            self.title_label.setToolTip(tooltip)


class FormGroup(style_module.Surface):
    """macOS「系统设置」式的圆角分组：一列 :class:`FormRow`，行间是从标题起的缩进发丝线。

    分组本身是一张卡片（:class:`style.Surface`，投影代替外框）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, object_name="settingsGroup")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(0)
        self._count = 0

    def add_row(self, widget: QWidget) -> QWidget:
        if self._count:
            holder = QWidget()
            line = QHBoxLayout(holder)
            line.setContentsMargins(getattr(widget, "inset", FORM_ROW_INSET), 0, 0, 0)
            rule = QFrame()
            rule.setObjectName("rowRule")
            rule.setFixedHeight(1)
            line.addWidget(rule)
            self._layout.addWidget(holder)
        self._layout.addWidget(widget)
        self._count += 1
        return widget

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # before detaching (see segment_assist._clear)
                widget.setParent(None)
                widget.deleteLater()
        self._count = 0


class CountBadge(QWidget):
    """计数小药丸：18px 高、半高圆角、11px 半粗体（侧栏「项目」与项目列表的待审核数）。

    自己画，不走 QLabel 的芯片 QSS：那一套带 10px 横向内边距和描边，数字会被撑成方块。
    ``tone``：``info``（强调色浅底）/ ``muted``（中性）。
    """

    HEIGHT = 18

    def __init__(self, text: str = "", tone: str = "info", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = str(text)
        self._tone = tone
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._fit()

    def _font(self):
        font = self.font()
        font.setPixelSize(11)
        font.setWeight(font.Weight.DemiBold)
        return font

    def _fit(self) -> None:
        from PySide6.QtGui import QFontMetrics

        width = QFontMetrics(self._font()).horizontalAdvance(self._text) + 12
        self.setFixedWidth(max(self.HEIGHT + 4, width))

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self._text = str(text)
        self._fit()
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        tokens = theme_module.tokens(style_module.current_theme())
        fill, ink = ((tokens["accent-soft"], tokens["accent"]) if self._tone == "info"
                     else (tokens["bg-subtle"] if "bg-subtle" in tokens else tokens["border-hairline"],
                           tokens["text-secondary"]))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme_module.qcolor(fill))
        painter.drawRoundedRect(QRectF(self.rect()), self.HEIGHT / 2, self.HEIGHT / 2)
        painter.setFont(self._font())
        painter.setPen(theme_module.qcolor(ink))
        painter.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()
