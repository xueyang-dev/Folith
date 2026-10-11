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

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QKeySequence, QLinearGradient, QPainter, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from . import icons
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

    任务卡与"继续上次的工作"用它表示文档本身。不渲染真实页面（那要读文件、
    很慢），只给一个与文档类型无关的、主题内的示意；`faded` 用于读不出的任务。
    """

    def __init__(self, width: int = 74, height: int = 96, *, faded: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width + 12, height + 14)
        self._page = QSize(width, height)
        self._faded = faded
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

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
