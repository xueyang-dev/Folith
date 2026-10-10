"""阶段步骤条：准备 → 翻译 → 审校 → 交付，加上"术语"和"更多"两个旁路入口。

它取代了工作台原来的十个页签。主线四步各自带一句状态（"22 段未完成"），读一眼就知道
卡在哪；次要页面（原页、检查与修复、运行记录，以及学术模式的报告/案例/合规）收进
"更多"。步骤按钮是自绘的：QSS 无法在同一个按钮里画"圆形序号 + 标签 + 弱化提示"。

只发信号，不碰任何业务状态：选哪一页由 `WorkbenchView` 决定。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QHBoxLayout, QMenu, QPushButton, QSizePolicy, QWidget,
)

from . import i18n
from . import icons
from . import stage as stage_model
from . import style as style_module
from . import theme as theme_module

#: 步骤 id → 页面 id（页面 id 与 `WorkbenchView` 的页面表一致）。
STEP_PAGES = {stage_model.PREPARE: "translate", stage_model.TRANSLATE: "translate",
              stage_model.REVIEW: "review", stage_model.DELIVER: "deliver"}


class StepButton(QAbstractButton):
    """一个步骤：圆形标记（序号 / ✓ / !） + 标签 + 弱化的状态提示。"""

    HEIGHT = 40
    MARKER = 20

    def __init__(self, step_id: str, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step_id = step_id
        self.number = number
        self._label = ""
        self._hint = ""
        self._state = stage_model.TODO
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.HEIGHT)

    # ---- 数据 ---------------------------------------------------------------
    def set_step(self, label: str, state: str, hint: str) -> None:
        if (label, state, hint) == (self._label, self._state, self._hint):
            return
        self._label, self._state, self._hint = label, state, hint
        # 「准备」一旦完成就不再是一个可去的地方：设置已写进任务，翻译页就是它的延续。
        inert = (state == stage_model.LOCKED
                 or (self.step_id == stage_model.PREPARE and state == stage_model.DONE))
        self.setEnabled(not inert)
        self.setCursor(Qt.CursorShape.ArrowCursor if inert
                       else Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.tr('译前设置已保存到本任务')
                        if self.step_id == stage_model.PREPARE and state == stage_model.DONE
                        else "")
        self.setAccessibleName(f"{label} {hint}".strip())
        self.updateGeometry()
        self.update()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def state(self) -> str:
        return self._state

    def hint(self) -> str:
        return self._hint

    def is_active(self) -> bool:
        return self._active

    # ---- 尺寸 ---------------------------------------------------------------
    def _fonts(self) -> tuple[QFont, QFont]:
        label = QFont(self.font())
        label.setPixelSize(theme_module.FONT_SIZES["body"] - 1)
        label.setWeight(QFont.Weight.DemiBold)
        hint = QFont(self.font())
        hint.setPixelSize(theme_module.FONT_SIZES["caption"])
        return label, hint

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        label_font, hint_font = self._fonts()
        width = 10 + self.MARKER + 8 + QFontMetrics(label_font).horizontalAdvance(self._label)
        if self._hint:
            width += 8 + QFontMetrics(hint_font).horizontalAdvance(self._hint)
        return QSize(width + 12, self.HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    # ---- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = theme_module.tokens(style_module.current_theme())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = theme_module.RADIUS["sm"]
        hovered = self.underMouse() and self.isEnabled()
        if self._active:
            painter.setPen(QPen(theme_module.qcolor(tokens["accent-border"]), 1))
            painter.setBrush(theme_module.qcolor(tokens["accent-soft"]))
            painter.drawRoundedRect(rect, radius, radius)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_module.qcolor(tokens["bg-hover"]))
            painter.drawRoundedRect(rect, radius, radius)

        state = self._state
        marker_color = {
            stage_model.DONE: tokens["success"], stage_model.CURRENT: tokens["accent"],
            stage_model.ATTENTION: tokens["warning"],
        }.get(state, tokens["text-muted"])
        # 从右到左的语言（阿拉伯语）里整条步骤条是镜像的：自绘内容也要跟着镜像，
        # 否则圆形标记还在左边、文字却被布局挪到了右边。
        rtl = self.isRightToLeft()
        marker_x = (self.width() - 10 - self.MARKER) if rtl else 10
        marker = QRectF(marker_x, (self.height() - self.MARKER) / 2, self.MARKER, self.MARKER)
        filled = state in (stage_model.DONE, stage_model.CURRENT, stage_model.ATTENTION)
        if filled:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_module.qcolor(marker_color))
        else:
            painter.setPen(QPen(theme_module.qcolor(marker_color), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(marker)
        glyph_color = theme_module.qcolor(tokens["text-on-accent"] if filled else marker_color)
        if state == stage_model.DONE:
            pixmap = icons.pixmap("check", self.MARKER - 8, glyph_color)
            painter.drawPixmap(int(marker.x() + 4), int(marker.y() + 4), pixmap)
        else:
            glyph = "!" if state == stage_model.ATTENTION else str(self.number)
            painter.setPen(glyph_color)
            number_font = QFont(self.font())
            number_font.setPixelSize(theme_module.FONT_SIZES["caption"])
            number_font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(number_font)
            painter.drawText(marker, int(Qt.AlignmentFlag.AlignCenter), glyph)

        label_font, hint_font = self._fonts()
        muted = state in (stage_model.TODO, stage_model.LOCKED)
        label_color = tokens["text-muted"] if state == stage_model.LOCKED else (
            tokens["text-secondary"] if muted and not self._active else tokens["text-primary"])
        if self._active:
            label_color = tokens["brand-ink"]
        label_width = QFontMetrics(label_font).horizontalAdvance(self._label) + 2
        hint_width = QFontMetrics(hint_font).horizontalAdvance(self._hint) + 2 if self._hint else 0
        height = self.height()
        if rtl:
            label_x = marker.left() - 8 - label_width
            hint_x = label_x - 8 - hint_width
        else:
            label_x = marker.right() + 8
            hint_x = label_x + label_width + 8
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        painter.setFont(label_font)
        painter.setPen(theme_module.qcolor(label_color))
        painter.drawText(QRectF(label_x, 0, label_width, height), align, self._label)
        if self._hint:
            painter.setFont(hint_font)
            hint_color = tokens["warning"] if state == stage_model.ATTENTION else tokens["text-muted"]
            painter.setPen(theme_module.qcolor(hint_color))
            painter.drawText(QRectF(hint_x, 0, hint_width, height), align, self._hint)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme_module.qcolor(tokens["focus-ring"]), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.update()
        super().leaveEvent(event)


class _Connector(QWidget):
    """步骤之间的细线（已完成的一段用成功色）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._done = False
        self.setFixedSize(20, StepButton.HEIGHT)

    def set_done(self, done: bool) -> None:
        if done != self._done:
            self._done = done
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        tokens = theme_module.tokens(style_module.current_theme())
        painter = QPainter(self)
        colour = tokens["success"] if self._done else tokens["border-strong"]
        painter.setPen(QPen(theme_module.qcolor(colour), 1.5))
        y = self.height() / 2
        painter.drawLine(QPointF(3, y), QPointF(self.width() - 3, y))


class StageBar(QWidget):
    """主线四步 + 旁路入口。"""

    #: 请求切到某一页（页面 id）。
    pageRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("stageBar")
        self._active_page = ""
        self._more: list[tuple[str, str]] = []
        self._badge = 0
        self._side_visible = True

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.steps: dict[str, StepButton] = {}
        self._connectors: list[_Connector] = []
        for number, step_id in enumerate(stage_model.STEP_IDS, start=1):
            if number > 1:
                connector = _Connector()
                self._connectors.append(connector)
                row.addWidget(connector)
            button = StepButton(step_id, number)
            button.clicked.connect(
                lambda _checked=False, s=step_id: self.pageRequested.emit(STEP_PAGES[s]))
            self.steps[step_id] = button
            row.addWidget(button)
        row.addStretch(1)

        def utility(text: str) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("stageUtility")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(StepButton.HEIGHT - 8)
            return button

        self.terms_button = utility(i18n.tr('术语'))
        self.terms_button.setIcon(icons.icon("file-sheet", 16,
                                             style_module.text_color("text-secondary")))
        self.terms_button.setIconSize(QSize(16, 16))
        self.terms_button.clicked.connect(lambda: self.pageRequested.emit("terms"))
        self.more_button = utility(i18n.tr('更多'))
        self.more_button.setIcon(icons.icon("chevron-down", 16,
                                            style_module.text_color("text-secondary")))
        self.more_button.setIconSize(QSize(16, 16))
        self.more_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        row.addWidget(self.terms_button)
        row.addSpacing(theme_module.SPACE["xs"])
        row.addWidget(self.more_button)
        self.more_button.setVisible(False)

    # ---- 数据 ---------------------------------------------------------------
    def set_stages(self, view: dict) -> None:
        steps = {item["id"]: item for item in view.get("steps") or []}
        previous_done = False
        for index, step_id in enumerate(stage_model.STEP_IDS):
            item = steps.get(step_id)
            button = self.steps[step_id]
            if item is None:
                button.set_step(i18n.tr(stage_model.STEP_LABELS[step_id]),
                                stage_model.LOCKED, "")
            else:
                button.set_step(item["label"], item["state"], item.get("hint", ""))
            if index > 0:
                self._connectors[index - 1].set_done(previous_done)
            previous_done = button.state() == stage_model.DONE
        self._sync_active()

    def set_more_pages(self, pages: list[tuple[str, str]], *, badge: int = 0) -> None:
        """"更多"菜单里的页面（页面 id, 标签）。没有任何页面时整个入口收起。"""
        # 每秒都会被调用（随任务状态刷新）：内容没变就不要动菜单，否则用户正打开着
        # "更多"时它会被清掉重建，菜单当场消失。
        if list(pages) == self._more and int(badge) == self._badge:
            return
        self._more = list(pages)
        self._badge = int(badge)
        self.more_menu.clear()
        for page_id, label in self._more:
            action = self.more_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(page_id == self._active_page)
            action.triggered.connect(
                lambda _checked=False, p=page_id: self.pageRequested.emit(p))
        self.more_button.setVisible(self._side_visible and bool(self._more))
        self._sync_active()

    def set_side_visible(self, visible: bool) -> None:
        """旁路入口（术语 / 更多）整体显示或收起。"更多"另外受自己是否有页面约束。"""
        self._side_visible = bool(visible)
        self.terms_button.setVisible(self._side_visible)
        self.more_button.setVisible(self._side_visible and bool(self._more))

    def more_page_ids(self) -> list[str]:
        return [page_id for page_id, _label in self._more]

    def set_active_page(self, page_id: str) -> None:
        self._active_page = page_id
        self._sync_active()

    def _sync_active(self) -> None:
        page = self._active_page
        # 准备与翻译共用一页：任务还没开始时亮"准备"，开始之后亮"翻译"。
        for step_id, button in self.steps.items():
            if STEP_PAGES[step_id] != page:
                button.set_active(False)
            elif page == "translate":
                started = self.steps[stage_model.TRANSLATE].state() != stage_model.LOCKED
                button.set_active(step_id == (stage_model.TRANSLATE if started
                                              else stage_model.PREPARE))
            else:
                button.set_active(True)
        self.terms_button.setChecked(page == "terms")
        self.more_button.setChecked(page in self.more_page_ids())
        # 正在看"更多"里的某一页时，按钮就写这一页的名字：只亮不写字，用户不知道自己在哪。
        title = next((label for page_id, label in self._more if page_id == page),
                     i18n.tr('更多'))
        self.more_button.setText(title + (f" ({self._badge})" if self._badge else ""))
        for action, (page_id, _label) in zip(self.more_menu.actions(), self._more):
            action.setChecked(page_id == page)
