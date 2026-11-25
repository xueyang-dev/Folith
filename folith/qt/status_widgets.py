"""A-59: compact status widgets moved from views.py."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                              QToolButton, QVBoxLayout, QWidget)

from . import (components, i18n, icons, motion,
               style as style_module, theme as theme_module)

class ClickableLabel(QLabel):
    """一行可点的文字（悬停有下划线与手型光标；回车 / 空格也能触发）。"""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()  # 不让按下冒泡成拖动窗口（见 components.WindowDragArea）
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class StatusLineLabel(QLabel):
    """状态行文字：放不下时以标准省略号（…）收尾，完整文字在悬停提示里。

    这一行以前是横向滚动（MarqueeLine）：1440 宽的顶栏里它只拿到最小宽度，
    停在开头的文字被直接裁掉，末尾既没有省略号也看不出「还有下文」，读起来像
    故障。改成标准省略后，任何宽度下都看得出文字被省略了；``text()`` 仍是完整
    文字（调用方与测试读的是它），完整内容同时在提示与运行记录里。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(False)
        # 宽度由布局给（Ignored 让 sizeHint 不参与最小宽度），高度固定一行。
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def fullText(self) -> str:  # noqa: N802 - 与 ElideLabel 同名，调用方不用改
        return self.text()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt naming
        super().setText(text)
        self.setToolTip(text)
        self.update()

    def elided_text(self) -> str:
        """当前宽度下真正画出来的那一行（测试与取证脚本读它）。"""
        metrics = self.fontMetrics()
        width = self.contentsRect().width()
        full = self.text()
        if width <= 0 or metrics.horizontalAdvance(full) <= width:
            return full
        return metrics.elidedText(full, Qt.TextElideMode.ElideRight, width)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # QLabel 只会把尾巴硬裁掉；这里按最终宽度省略，并沿用 QSS 给的颜色。
        painter = QPainter(self)
        self.style().drawItemText(
            painter, self.contentsRect(),
            int(self.alignment() | Qt.AlignmentFlag.AlignVCenter),
            self.palette(), self.isEnabled(), self.elided_text(),
            self.foregroundRole())


class RunStatusGroup(QFrame):
    """顶栏的运行状态组：进度条 + 百分比，下面一行写「阶段 · 进度 · 正在做什么」。

    整组是一个可点区域（鼠标、回车 / 空格），点开运行记录浮窗。颜色只是辅助：
    同一个状态在下面那行文字里总会写出来（见 acceptance-feedback 第 11 条）。
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runStatus")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(i18n.tr('查看运行记录'))
        self.setAccessibleName(i18n.tr('运行状态'))
        self.track = components.ProgressTrack()
        self.percent = QLabel("")
        self.percent.setObjectName("runStatusPercent")
        self.line = StatusLineLabel("")
        self.line.setObjectName("runStatusLine")
        # 兼容旧调用点：状态行本身就是那个「视图」，不再套一层滚动容器。
        self.line_view = self.line
        layout = QVBoxLayout(self)
        # 四周留够白：展开运行记录时这一块是「凸」字的上半截，进度条贴着它的上、左、右
        # 边会显得局促（试用反馈）。
        layout.setContentsMargins(*self.PADDING)
        layout.setSpacing(3)
        # Attach before filling: see tests/qt_layout_item_wrapper_test.py (SIGSEGV).
        top = QHBoxLayout()
        layout.addLayout(top)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme_module.SPACE["xs"] + 2)
        top.addWidget(self.track, 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.line)
        # 状态行自己会省略，所以只有进度那一行需要硬下限。让整组可以缩到 144，
        # 窄窗口下才不至于把统一工具栏撑宽。
        self.setMinimumWidth(144)
        self.setMaximumWidth(256)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    #: 内容到状态组四边的距离（左、上、右、下）。
    PADDING = (14, 9, 14, 6)

    def set_state(self, *, tone: str, line: str, line_tone: str | None = None) -> None:
        """``line_tone``：只给文字着色（例如一次操作失败），进度条仍表示任务本身的状态。"""
        self.track.set_tone(tone)
        share = self.track.fraction()
        self.percent.setText("" if share is None or tone == "done"
                             else f"{int(round(share * 100))}%")
        self.line.setProperty("tone", line_tone or tone)
        style_module.repolish(self.line)
        self.line.setText(line)
        self.setAccessibleDescription(line)

    #: 展开运行记录时「凸」字上半截相对状态组四边的内缩（留给投影）。
    TAB_INSET = 3

    def tab_rect(self) -> QRectF:
        inset = self.TAB_INSET
        return QRectF(inset, inset, self.width() - 2 * inset, self.height() - inset)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        if self.property("tethered") is not True:
            return
        # 「凸」字的上半截：浮层的颈部从它底边接下去（components.Popover tether）。
        # 上、左、右三面有投影和一圈发丝线，底边敞开，与浮层连成一块。
        path = components.tab_path(self.tab_rect(), theme_module.RADIUS["md"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        components.paint_tab_shadow(painter, self.tab_rect(), theme_module.RADIUS["md"])
        tokens = theme_module.tokens(style_module.current_theme())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme_module.qcolor(tokens["bg-surface-raised"]))
        painter.drawPath(path)
        painter.setPen(QPen(components.outline_colour(), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(components.tab_outline(self.tab_rect(), theme_module.RADIUS["md"]))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # 收下按下：否则它冒泡到顶栏，被当成拖动窗口，松开时就点不开运行记录。
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class RoundTab(QWidget):
    """圆形图标按钮（检查与修复、任务详情）外面的一圈留白：展开卡片时它画成「凸」字
    的上半截——顶上是与按钮同心的半圆，两侧直落、丝滑接进卡片的边框。

    图标本身是 30px 的圆；直接拿它当上半截，圆贴着卡片的边，很挤（试用反馈）。
    """

    #: 「凸」字上半截相对这一圈四边的内缩（留给投影）。
    TAB_INSET = 2
    #: 上、左、右留白相同：半圆与按钮的圆同心。
    PADDING = (7, 7, 7, 4)

    def __init__(self, button: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.button = button
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*self.PADDING)
        layout.addWidget(button)

    def tab_rect(self) -> QRectF:
        inset = self.TAB_INSET
        return QRectF(inset, inset, self.width() - 2 * inset, self.height() - inset)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.property("tethered") is not True:
            return
        rect = self.tab_rect()
        radius = rect.width() / 2  # 顶上是半圆，与里面的圆形按钮同心
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        components.paint_tab_shadow(painter, rect, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme_module.qcolor(theme_module.tokens(style_module.current_theme())["bg-surface-raised"]))
        painter.drawPath(components.tab_path(rect, radius))
        painter.setPen(QPen(components.outline_colour(), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(components.tab_outline(rect, radius))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # 留白处的按下不冒泡成拖动窗口。
        event.accept()


CheckTab = RoundTab


class CheckStatusButton(QToolButton):
    """「检查与修复」：进度条旁的圆形图标。平时灰；有待处理项变黄，有失败批次变红。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("roundButton")
        self.setCheckable(True)
        self.setIcon(icons.icon("shield-check", 16, "text-secondary"))
        self.setIconSize(QSize(16, 16))
        self.badge = QLabel("", self)
        self.badge.setObjectName("countBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setVisible(False)
        self._tone = ""
        self._motion = motion.Motion(self)
        self.set_count(0, "")

    def set_count(self, count: int, tone: str) -> None:
        tone = tone if count else ""
        if tone != self._tone:
            self._tone = tone
            self.setProperty("check", tone)
            token = {"warning": "warning", "danger": "danger"}.get(tone, "text-secondary")
            self.setIcon(icons.icon("shield-check", 16, token))
            style_module.repolish(self)
            if tone:
                self._motion.nod("check-tone", self)
        self.badge.setText(str(count) if count else "")
        self.badge.setVisible(bool(count))
        self.badge.adjustSize()
        self.badge.move(0 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
                        else self.width() - self.badge.width(), 0)
        label = i18n.tr('检查与修复')
        self.setToolTip(i18n.tr('检查与修复 · {v0} 项待处理', v0=count) if count else label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(self.toolTip())

    def tone(self) -> str:
        return self._tone

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.badge.move(0 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
                        else self.width() - self.badge.width(), 0)


