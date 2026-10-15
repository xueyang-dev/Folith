"""任务库的「书架」视图：每个项目是一层书架，每个任务是一本书的书脊。

书脊本身就是信息，而不是装饰：

* **颜色**取自这份文档的封面 / 第一页（缩略图生成后取主色、再压到主题允许的明度范围），
  还没有缩略图时按文件名在一组低饱和色里稳定地挑一个；
* **高度与厚度**随文档长度变化（段数越多越厚、越高），一眼看出哪本是大部头；
* **书脊底部的色带**是状态：蓝 = 进行中，琥珀 = 有待处理的问题，红 = 出错，绿 = 已冻结交付；
  标签下沿的细线是翻译进度；
* 悬停时书脊抬起一点，上方出现一张「藏书票」：文件名、格式与段数、目标语言、
  状态、进度、问题数与更新时间。单击选中（与卡片 / 表格共用同一份选择），双击或回车打开。

整层书架是**一个自绘控件**，不是每本书一个控件：几百个任务也不会拖慢任务库。
动画（悬停抬起）尊重「减少动态效果」。
"""
from __future__ import annotations

import hashlib
import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import i18n
from . import motion
from . import style as style_module
from . import theme as theme_module

#: 没有封面色时的书脊色（浅色 / 深色主题各一组），低饱和、彼此区分得开。
_PALETTE = {
    False: ("#3f5a8a", "#6b4f8f", "#2f6f6a", "#8a5a3c", "#5b6b3a", "#7a3f52",
            "#4a6f8f", "#8a6f2f", "#55607a", "#3e7a52"),
    True: ("#5b7bb5", "#8d6fb3", "#4a958e", "#b07a55", "#7d8f52", "#a35a72",
           "#6a92b5", "#b0924a", "#76809e", "#5aa274"),
}
SHELF_GAP = 6            # 书与书之间
PLANK = 10               # 搁板厚度
LIFT = 8                 # 悬停抬起
MIN_W, MAX_W = 32, 62
MIN_H, MAX_H = 150, 214


def spine_size(segments: int, key: str = "") -> QSize:
    """段数 → 书脊的宽与高（对数增长：大部头明显更厚，但不会大到占一整层）。

    ``key``（文件名）给高度加一点稳定的参差（±10 px）：真实书架上的书高矮不一，
    同样长度的文档排成一排一样高，看起来像栅栏。
    """
    weight = math.log10(max(10, int(segments or 0)))      # 1 … ~4
    share = max(0.0, min(1.0, (weight - 1) / 3))
    jitter = 0
    if key:
        jitter = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:4], 16) % 21 - 10
    height = round(MIN_H + share * (MAX_H - MIN_H)) + jitter
    return QSize(round(MIN_W + share * (MAX_W - MIN_W)),
                 max(MIN_H - 10, min(MAX_H + 10, height)))


def fallback_colour(filename: str, dark: bool) -> QColor:
    digest = int(hashlib.sha1(str(filename or "").encode("utf-8")).hexdigest()[:8], 16)
    palette = _PALETTE[bool(dark)]
    return QColor(palette[digest % len(palette)])


def cover_colour(image, dark: bool) -> QColor | None:
    """封面 / 第一页图像的主色，压到适合做书脊的明度与饱和度。"""
    if image is None or image.isNull():
        return None
    small = image.scaled(24, 32, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    best, best_score = None, -1.0
    buckets: dict[tuple, list] = {}
    for y in range(small.height()):
        for x in range(small.width()):
            colour = QColor(small.pixel(x, y))
            h, s, v = colour.hsvHueF(), colour.hsvSaturationF(), colour.valueF()
            if s < 0.18 or v < 0.12 or v > 0.97:
                continue                       # 纸白、墨黑和灰不算「封面的颜色」
            key = (round(max(h, 0) * 12) % 12,)
            buckets.setdefault(key, []).append(colour)
    for colours in buckets.values():
        score = len(colours) * (0.5 + sum(c.hsvSaturationF() for c in colours) / len(colours))
        if score > best_score:
            best, best_score = colours, score
    if not best:
        return None
    r = sum(c.redF() for c in best) / len(best)
    g = sum(c.greenF() for c in best) / len(best)
    b = sum(c.blueF() for c in best) / len(best)
    colour = QColor.fromRgbF(r, g, b)
    h, s, l = colour.hslHueF(), colour.hslSaturationF(), colour.lightnessF()
    s = max(0.22, min(0.55, s))
    l = max(0.32, min(0.46, l)) if not dark else max(0.42, min(0.58, l))
    return QColor.fromHslF(max(h, 0), s, l)


def status_tone(row) -> str:
    """书脊底部色带：与卡片、进度条同一套语义。"""
    if str(getattr(row, "error", "") or "").strip():
        return "danger"
    if int(getattr(row, "attention_count", 0) or 0):
        return "warning"
    if getattr(row, "active", False):
        return "accent"
    if getattr(row, "snapshot_version", None):
        return "success"
    return ""


class ShelfStrip(QWidget):
    """一层（或几层，放不下时折行）书架：自绘书脊、搁板与悬停抬起。"""

    clicked = Signal(str)
    activated = Signal(str)
    hovered = Signal(str, QRect)          # job_id, 书脊在本控件里的矩形（离开时 job_id 为空）
    navigate = Signal(str, int)           # job_id, ±1：键盘左右越过这一层的边界

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list = []
        self.colours: dict[str, QColor] = {}
        self.selected: set[str] = set()
        self.current = ""
        self._hover = ""
        self._lift: dict[str, float] = {}
        self._rects: dict[str, QRect] = {}
        self._motion = motion.Motion(self)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(i18n.tr('书架'))

    # ---- 数据 ----
    def set_rows(self, rows: list) -> None:
        self.rows = list(rows)
        self._layout()
        self.update()

    def set_colour(self, job_id: str, colour: QColor | None) -> None:
        if colour is not None:
            self.colours[job_id] = colour
            self.update()

    def set_selected(self, ids: set[str]) -> None:
        self.selected = set(ids)
        self.update()

    # ---- 布局 ----
    def _layout(self) -> None:
        """从左到右排书脊，放不下就换一层；每层只有这一层最高那本书那么高，底对齐在搁板上。"""
        width = max(200, self.width() - 8)
        sizes = [(row.job_id, spine_size(getattr(row, "segment_count", 0), str(row.filename)))
                 for row in self.rows]
        lines: list[list] = [[]]
        x = 4
        for job_id, size in sizes:
            if x + size.width() > width and lines[-1]:
                lines.append([])
                x = 4
            lines[-1].append((job_id, size))
            x += size.width() + SHELF_GAP
        self._rects = {}
        top = LIFT + 4
        for line in lines:
            tallest = max((size.height() for _job, size in line), default=MIN_H)
            base = top + tallest
            x = 4
            for job_id, size in line:
                self._rects[job_id] = QRect(x, base - size.height(), size.width(), size.height())
                x += size.width() + SHELF_GAP
            top = base + PLANK + 18
        self._lines = len(lines)
        self.setFixedHeight(top - 10)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._layout()

    def spine_rect(self, job_id: str) -> QRect:
        rect = QRect(self._rects.get(job_id, QRect()))
        return rect.translated(0, -round(self._lift.get(job_id, 0.0) * LIFT))

    # ---- 绘制 ----
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        theme = style_module.current_theme()
        tokens = theme_module.tokens(theme)
        dark = style_module.night_mode()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 搁板：每一层一块，带一道底部阴影。
        # 搁板：上沿一道受光线、板面由浅到深、下面一层柔和的投影——像一块木板，而不是一条灰线。
        wood = QColor("#d9cdb8") if not dark else QColor("#4a4237")
        bases = sorted({rect.bottom() for rect in self._rects.values()})
        for base in bases:
            board = QRectF(0, base + 1, self.width(), PLANK)
            for i, alpha in enumerate((34, 20, 10, 4) if not dark else (70, 42, 20, 8)):
                painter.fillRect(QRectF(2, board.bottom() + i, self.width() - 4, 1),
                                 QColor(0, 0, 0, alpha))
            face = QLinearGradient(board.topLeft(), board.bottomLeft())
            face.setColorAt(0.0, wood.lighter(108))
            face.setColorAt(1.0, wood.darker(112))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(face)
            painter.drawRoundedRect(board, 2, 2)
            painter.fillRect(QRectF(board.left() + 2, board.top(), board.width() - 4, 1),
                             QColor(255, 255, 255, 110 if not dark else 40))
            # 书脚的接地阴影：每本书底下一抹，书才「立」在板上。
        for rect in self._rects.values():
            painter.fillRect(QRectF(rect.left() + 1, rect.bottom() + 1, rect.width() - 2, 2),
                             QColor(0, 0, 0, 40 if not dark else 80))
        for row in self.rows:
            self._paint_spine(painter, row, tokens, dark)
        painter.end()

    def _paint_spine(self, painter: QPainter, row, tokens: dict, dark: bool) -> None:
        rect = QRectF(self.spine_rect(row.job_id))
        if rect.isEmpty():
            return
        base = self.colours.get(row.job_id) or fallback_colour(row.filename, dark)
        # 立体感：左侧受光、右侧背光（很轻，只是让书脊不像色块）。
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0.0, base.lighter(118))
        gradient.setColorAt(0.18, base)
        gradient.setColorAt(0.85, base.darker(108))
        gradient.setColorAt(1.0, base.darker(125))
        path = QPainterPath()
        path.addRoundedRect(rect, 3, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, gradient)
        # 书名标签：一块浅色纸签，书名竖排在里面。
        label = rect.adjusted(4, 14, -4, -30)
        paper = QColor("#f6f1e6") if not dark else QColor("#ece5d6")
        painter.setBrush(paper)
        painter.drawRoundedRect(label, 2, 2)
        title = str(getattr(row, "title", "") or row.filename or "")
        painter.save()
        painter.translate(label.center())
        painter.rotate(90)
        font = QFont(painter.font())
        font.setFamilies([".New York", "New York", "Songti SC", "Georgia"])
        font.setPixelSize(max(10, min(13, int(label.width()) - 8)))
        painter.setFont(font)
        painter.setPen(QColor("#2b2a28"))
        metrics = QFontMetrics(font)
        text = metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(label.height()) - 10)
        painter.drawText(QRectF(-label.height() / 2 + 5, -label.width() / 2,
                                label.height() - 10, label.width()),
                         Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
        # 进度：标签下沿的一条细线。
        share = getattr(row, "progress_percent", None)
        if isinstance(share, (int, float)) and share > 0:
            track = QRectF(label.left(), label.bottom() + 4, label.width(), 2)
            painter.fillRect(track, QColor(255, 255, 255, 60))
            painter.fillRect(QRectF(track.left(), track.top(),
                                    track.width() * min(100, share) / 100, 2),
                             QColor(255, 255, 255, 200))
        # 状态色带。
        tone = status_tone(row)
        if tone:
            band = QRectF(rect.left() + 3, rect.bottom() - 12, rect.width() - 6, 5)
            painter.setBrush(theme_module.qcolor(tokens[tone]))
            painter.drawRoundedRect(band, 2, 2)
        # 选中 / 焦点：强调色描边。
        if row.job_id in self.selected or (row.job_id == self.current and self.hasFocus()):
            pen = QPen(theme_module.qcolor(tokens["accent"]), 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 4, 4)
            painter.setPen(Qt.PenStyle.NoPen)

    # ---- 交互 ----
    def job_at(self, point) -> str:
        for row in self.rows:
            if self.spine_rect(row.job_id).adjusted(-2, -LIFT, 2, 2).contains(point):
                return row.job_id
        return ""

    def _set_hover(self, job_id: str) -> None:
        if job_id == self._hover:
            return
        previous, self._hover = self._hover, job_id
        for key, target in ((previous, 0.0), (job_id, 1.0)):
            if not key:
                continue
            start = self._lift.get(key, 0.0)
            self._motion.animate(f"lift-{key}", start, target,
                                 lambda value, k=key: self._set_lift(k, value), kind="select")
        self.setCursor(Qt.CursorShape.PointingHandCursor if job_id else Qt.CursorShape.ArrowCursor)
        self.hovered.emit(job_id, self.spine_rect(job_id) if job_id else QRect())

    def _set_lift(self, job_id: str, value: float) -> None:
        self._lift[job_id] = float(value)
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._set_hover(self.job_at(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._set_hover("")
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        job_id = self.job_at(event.position().toPoint())
        if job_id and event.button() == Qt.MouseButton.LeftButton:
            self.current = job_id
            self.clicked.emit(job_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt naming
        job_id = self.job_at(event.position().toPoint())
        if job_id:
            self.activated.emit(job_id)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        order = [row.job_id for row in self.rows]
        if not order:
            return super().keyPressEvent(event)
        current = self.current if self.current in order else order[0]
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Left):
            step = 1 if event.key() == Qt.Key.Key_Right else -1
            if self.layoutDirection() == Qt.LayoutDirection.RightToLeft:
                step = -step
            index = order.index(current) + step
            if 0 <= index < len(order):
                self.focus_job(order[index])
                self.clicked.emit(self.current)
            else:
                self.navigate.emit(current, step)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(current)
            return
        super().keyPressEvent(event)

    def focus_job(self, job_id: str) -> None:
        self.current = job_id
        row = next((item for item in self.rows if item.job_id == job_id), None)
        if row is not None:
            self.setAccessibleDescription(describe(row))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()
        self.hovered.emit(job_id, self.spine_rect(job_id))


def describe(row) -> str:
    """一本书的一句话（藏书票与读屏器用同一句）。"""
    parts = [str(getattr(row, "title", "") or row.filename)]
    if getattr(row, "segment_count", 0):
        parts.append(i18n.tr('{v0} 段', v0=f"{row.segment_count:,}"))
    if getattr(row, "status_label", ""):
        parts.append(i18n.tr_dynamic(row.status_label))
    return " · ".join(parts)


class Bookplate(QFrame):
    """悬停时浮在书脊上方的「藏书票」。不抢焦点、不挡鼠标。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("bookplate")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(3)
        self.kicker = QLabel(i18n.tr('藏书票'))
        self.kicker.setObjectName("bookplateKicker")
        self.title = QLabel("")
        self.title.setObjectName("bookplateTitle")
        self.title.setWordWrap(True)
        self.meta = QLabel("")
        self.meta.setObjectName("bookplateMeta")
        self.meta.setWordWrap(True)
        self.status = QLabel("")
        self.status.setObjectName("bookplateStatus")
        self.status.setWordWrap(True)
        for widget in (self.kicker, self.title, self.meta, self.status):
            layout.addWidget(widget)
        self.setFixedWidth(240)
        self.hide()

    def show_for(self, row, anchor: QRect) -> None:
        self.title.setText(str(getattr(row, "title", "") or row.filename))
        meta = [str(row.filename).rsplit(".", 1)[-1].upper()]
        if row.segment_count:
            meta.append(i18n.tr('{v0} 段', v0=f"{row.segment_count:,}"))
        if row.target_lang:
            meta.append(i18n.tr_dynamic(row.target_lang))
        self.meta.setText(" · ".join(meta))
        status = [i18n.tr_dynamic(row.status_label or "")]
        if isinstance(row.progress_percent, (int, float)):
            status.append(i18n.tr('已译 {v0}%', v0=int(row.progress_percent)))
        if row.attention_count:
            status.append(i18n.tr('{v0} 项待处理', v0=row.attention_count))
        if getattr(row, "updated_at", ""):
            from .components import relative_time

            status.append(relative_time(row.updated_at))
        self.status.setText(" · ".join(part for part in status if part))
        self.adjustSize()
        parent = self.parentWidget()
        x = max(4, min(anchor.center().x() - self.width() // 2, parent.width() - self.width() - 4))
        y = anchor.top() - self.height() - 8
        if y < 4:
            y = anchor.bottom() + 8
        self.move(x, y)
        self.show()
        self.raise_()


class ShelfView(QWidget):
    """全部项目的书架：每个项目一个标题 + 一层（可折行的）书架。"""

    clicked = Signal(str)
    activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pageCanvas")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 16)
        self._layout.setSpacing(theme_module.SPACE["lg"])
        self.strips: list[ShelfStrip] = []
        self.rows: dict[str, object] = {}
        self.plate = Bookplate(self)
        self._hide_plate = QTimer(self)
        self._hide_plate.setSingleShot(True)
        self._hide_plate.setInterval(120)
        self._hide_plate.timeout.connect(self.plate.hide)

    def fill(self, groups: list[tuple[str, list]]) -> None:
        """``groups``：``[(项目名, 任务行…)]``。"""
        from .style import Chip

        focused = next((strip.current for strip in self.strips if strip.hasFocus()), "")
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self.strips = []
        self.rows = {}
        for label, items in groups:
            heading = QWidget()
            from PySide6.QtWidgets import QHBoxLayout

            row = QHBoxLayout(heading)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme_module.SPACE["sm"])
            name = QLabel(label)
            name.setObjectName("groupHeading")
            row.addWidget(name)
            row.addWidget(Chip(i18n.tr('{v0} 个任务', v0=len(items)), "muted"))
            row.addStretch(1)
            self._layout.addWidget(heading)
            strip = ShelfStrip()
            strip.set_rows(items)
            strip.clicked.connect(self.clicked.emit)
            strip.activated.connect(self.activated.emit)
            strip.hovered.connect(lambda job_id, rect, s=strip: self._hovered(s, job_id, rect))
            strip.navigate.connect(self._navigate)
            self._layout.addWidget(strip)
            self.strips.append(strip)
            for item in items:
                self.rows[item.job_id] = item
        self._layout.addStretch(1)
        self.plate.raise_()
        if focused:
            self.focus_job(focused)

    def strip_of(self, job_id: str) -> ShelfStrip | None:
        return next((strip for strip in self.strips
                     if any(row.job_id == job_id for row in strip.rows)), None)

    def set_colour(self, job_id: str, colour) -> None:
        strip = self.strip_of(job_id)
        if strip is not None:
            strip.set_colour(job_id, colour)

    def set_selected(self, ids) -> None:
        for strip in self.strips:
            strip.set_selected(set(ids))

    def focus_job(self, job_id: str) -> None:
        strip = self.strip_of(job_id)
        if strip is not None:
            strip.focus_job(job_id)

    def _navigate(self, job_id: str, step: int) -> None:
        order = [row.job_id for strip in self.strips for row in strip.rows]
        if job_id in order:
            index = order.index(job_id) + step
            if 0 <= index < len(order):
                self.focus_job(order[index])
                self.clicked.emit(order[index])

    def _hovered(self, strip: ShelfStrip, job_id: str, rect: QRect) -> None:
        if not job_id:
            self._hide_plate.start()
            return
        self._hide_plate.stop()
        row = self.rows.get(job_id)
        if row is None:
            return
        anchor = QRect(strip.mapTo(self, rect.topLeft()), rect.size())
        self.plate.show_for(row, anchor)
