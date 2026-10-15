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
    """一张"纸"：白色页面 + 投影 + 标题线 + 正文行。

    任务卡与"继续上次的工作"用它表示文档本身。真实页面（PDF 第一页 / 书封面 /
    正文第一页）由 :mod:`folith.qt.thumbnails` 在后台生成，好了以后经
    :meth:`set_image` 换上；在那之前、或文件读不出来时，画一张主题内的示意纸。
    `faded` 用于读不出的任务。
    """

    def __init__(self, width: int = 74, height: int = 96, *, faded: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width + 12, height + 14)
        self._page = QSize(width, height)
        self._faded = faded
        self._image: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_image(self, image: QImage | None) -> None:
        """换上真实页面预览（``None`` = 回到示意纸）。"""
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def has_image(self) -> bool:
        return self._image is not None

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._faded:
            painter.setOpacity(0.55)
        w, h = self._page.width(), self._page.height()
        page = QRectF((self.width() - w) / 2, 4, w, h)
        shadow = theme_module.qcolor(tokens["text-primary"])
        for spread, alpha in ((5, 10), (3, 16), (1, 26)):
            shadow.setAlpha(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(page.adjusted(-spread / 2, spread / 2, spread / 2, spread), 4, 4)
        painter.setBrush(theme_module.qcolor(tokens["bg-surface-raised"]))
        border = theme_module.qcolor(tokens["border-subtle"])
        painter.setPen(border)
        painter.drawRoundedRect(page, 3, 3)
        if self._image is not None:
            self._paint_image(painter, page, border)
            return
        ink = theme_module.qcolor(tokens["text-primary"])
        ink.setAlpha(170)
        muted = theme_module.qcolor(tokens["text-muted"])
        muted.setAlpha(110)
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
            painter.drawRect(QRectF(x, y, inner, 2))
            y += step
        painter.end()



    def _paint_image(self, painter: QPainter, page: QRectF, border) -> None:
        """按"铺满纸面"缩放：页面预览靠上对齐（标题在上方），封面居中裁切。"""
        image = self._image
        scale = max(page.width() / image.width(), page.height() / image.height())
        width, height = image.width() * scale, image.height() * scale
        target = QRectF(page.left() + (page.width() - width) / 2, page.top(), width, height)
        if height > page.height() and image.width() / image.height() < 0.7:
            # 竖长的封面：上下居中裁切，不只露出书名区域。
            target.moveTop(page.top() - (height - page.height()) / 2)
        path = QPainterPath()
        path.addRoundedRect(page, 3, 3)
        painter.save()
        painter.setClipPath(path)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(target, image)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(border)
        painter.drawRoundedRect(page, 3, 3)

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

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = _tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        painter.setBrush(theme_module.qcolor(tokens["bg-surface-sunken"]))
        painter.drawRoundedRect(rect, 2, 2)
        if self._value:
            painter.setBrush(theme_module.qcolor(tokens[self._token]))
            painter.drawRoundedRect(QRectF(0, 0, rect.width() * self._value / 100, rect.height()), 2, 2)
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
