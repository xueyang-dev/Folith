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
    QAbstractButton, QFrame, QHBoxLayout, QMenu, QPushButton, QSizePolicy, QWidget,
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
    """一个步骤（Golden Gate 胶囊分段里的一段）：✓ / ! + 标签 + 弱化的状态提示。

    整条步骤条是工具栏中间的一枚胶囊（见 :class:`StageBar` 的 ``stageTrack``）；
    当前所在的一步是胶囊里浅色强调底的那一段，已完成的步骤前面一个成功色对勾，
    卡住的步骤前面一个警示色叹号，还没轮到的只有文字。
    """

    HEIGHT = 28
    MARKER = 12

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
        # ``Minimum`` treats sizeHint as a floor, which would keep every stage
        # hint in the toolbar and block window narrowing. ``Preferred`` may
        # shrink to minimumSizeHint, where paintEvent omits the nonessential
        # hint but retains the stage label and accessible description.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
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
            self.updateGeometry()
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
        label.setPixelSize(theme_module.FONT_SIZES["label"])
        label.setWeight(QFont.Weight.DemiBold if self._active else QFont.Weight.Medium)
        hint = QFont(self.font())
        hint.setPixelSize(theme_module.FONT_SIZES["caption"])
        return label, hint

    def _shows_hint(self) -> bool:
        """提示只在当前一步、或这一步卡住时出现（胶囊里放不下四句状态）。"""
        # 跳过的一步总是带着"未启用"：否则它和还没轮到的一步长得一样。
        return bool(self._hint) and (self._active or self._state in (
            stage_model.ATTENTION, stage_model.SKIPPED))

    def _has_marker(self) -> bool:
        return self._state in (stage_model.DONE, stage_model.ATTENTION)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self._size_hint(show_hint=self._shows_hint())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # The hint is useful context, not an input that has to remain at every
        # window width. Keeping it in the minimum width made the unified toolbar
        # (and therefore the workbench) refuse to shrink below a normal desktop
        # width.
        return self._size_hint(show_hint=False)

    def _size_hint(self, *, show_hint: bool) -> QSize:
        label_font, hint_font = self._fonts()
        width = 13 + QFontMetrics(label_font).horizontalAdvance(self._label) + 13
        if self._has_marker():
            width += self.MARKER + 5
        if show_hint:
            width += 6 + QFontMetrics(hint_font).horizontalAdvance(self._hint)
        return QSize(width, self.HEIGHT)

    # ---- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        tokens = theme_module.tokens(style_module.current_theme())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        hovered = self.underMouse() and self.isEnabled()
        if self._active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_module.qcolor(tokens["accent-soft"]))
            painter.drawRoundedRect(rect, radius, radius)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme_module.qcolor(tokens["bg-hover"]))
            painter.drawRoundedRect(rect, radius, radius)

        state = self._state
        # 从右到左的语言（阿拉伯语）里整条步骤条是镜像的：自绘内容也要跟着镜像。
        rtl = self.isRightToLeft()
        label_font, hint_font = self._fonts()
        label_width = QFontMetrics(label_font).horizontalAdvance(self._label) + 2
        # QHBoxLayout may allocate the minimum width before the preferred width
        # is available. Keep the hint in the accessible name and tooltip rather
        # than drawing it into the neighbouring step when compact.
        show_hint = self._shows_hint() and self.width() >= self.sizeHint().width()
        hint_width = (QFontMetrics(hint_font).horizontalAdvance(self._hint) + 2) if show_hint else 0
        height = self.height()
        cursor = 13.0
        if self._has_marker():
            marker_x = (self.width() - cursor - self.MARKER) if rtl else cursor
            marker = QRectF(marker_x, (height - self.MARKER) / 2, self.MARKER, self.MARKER)
            if state == stage_model.DONE:
                pixmap = icons.pixmap("check", self.MARKER,
                                      theme_module.qcolor(tokens["success"]))
                painter.drawPixmap(int(marker.x()), int(marker.y()), pixmap)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme_module.qcolor(tokens["warning"]))
                painter.drawEllipse(marker)
                painter.setPen(theme_module.qcolor(tokens["text-on-status"]))
                bang = QFont(self.font())
                bang.setPixelSize(theme_module.FONT_SIZES["micro"])
                bang.setWeight(QFont.Weight.Bold)
                painter.setFont(bang)
                painter.drawText(marker, int(Qt.AlignmentFlag.AlignCenter), "!")
            cursor += self.MARKER + 5
        if state in (stage_model.LOCKED, stage_model.SKIPPED) and not self._active:
            label_color = tokens["text-muted"]
        elif self._active:
            label_color = tokens["accent"]
        elif state == stage_model.TODO:
            label_color = tokens["text-secondary"]
        else:
            label_color = tokens["text-primary"]
        if rtl:
            label_x = self.width() - cursor - label_width
            hint_x = label_x - 6 - hint_width
        else:
            label_x = cursor
            hint_x = label_x + label_width + 6
        align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        painter.setFont(label_font)
        painter.setPen(theme_module.qcolor(label_color))
        painter.drawText(QRectF(label_x, 0, label_width, height), align, self._label)
        if show_hint:
            painter.setFont(hint_font)
            hint_color = tokens["warning"] if state == stage_model.ATTENTION else tokens["text-secondary"]
            painter.setPen(theme_module.qcolor(hint_color))
            painter.drawText(QRectF(hint_x, 0, hint_width, height), align, self._hint)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme_module.qcolor(tokens["focus-ring"]), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)

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
        self._actions: list[tuple[str, str]] = []
        self._badge = 0
        self._side_visible = True

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        # Golden Gate：四步装在工具栏中间的一枚胶囊里（统一工具栏的主控件）。
        self.track = QFrame()
        self.track.setObjectName("stageTrack")
        track_row = QHBoxLayout(self.track)
        track_row.setContentsMargins(2, 2, 2, 2)
        track_row.setSpacing(0)
        self.steps: dict[str, StepButton] = {}
        self._connectors: list[_Connector] = []
        for number, step_id in enumerate(stage_model.STEP_IDS, start=1):
            if number > 1:
                # 连接线保留为状态对象（"上一步是否完成"），胶囊版式里不再画出来。
                connector = _Connector()
                connector.setVisible(False)
                self._connectors.append(connector)
                track_row.addWidget(connector)
            button = StepButton(step_id, number)
            button.clicked.connect(
                lambda _checked=False, s=step_id: self.pageRequested.emit(STEP_PAGES[s]))
            self.steps[step_id] = button
            track_row.addWidget(button)
        # 胶囊居中：左右各一段弹性空间，旁路入口「更多」紧跟在胶囊右边。
        row.addStretch(1)
        row.addWidget(self.track)
        row.addSpacing(theme_module.SPACE["sm"])

        def utility(text: str) -> QPushButton:
            button = QPushButton(text)
            button.setObjectName("stageUtility")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFixedHeight(StepButton.HEIGHT + 4)
            return button

        # 术语、检查与修复、运行记录、任务详情都在「更多」里（设计审校 40 号第四节）：
        # 头部只留返回、文件名、步骤条和一个主按钮。
        self.more_button = utility(i18n.tr('更多'))
        self.more_button.setIcon(icons.icon("chevron-down", 16,
                                            "text-secondary"))
        self.more_button.setIconSize(QSize(16, 16))
        self.more_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        row.addWidget(self.more_button)
        row.addStretch(1)
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
            # 跳过的一步不挡路：后面那段连线照样算通。
            previous_done = button.state() in (stage_model.DONE, stage_model.SKIPPED)
        self._sync_active()

    def set_more_pages(self, pages: list[tuple[str, str]], *, badge: int = 0,
                       actions: list[tuple[str, str]] = ()) -> None:
        """"更多"菜单：上半是页面（页面 id, 标签，可勾选），分隔线下是打开面板的
        动作（检查与修复、运行记录、任务详情——不是页面，不勾选）。都没有时整个入口收起。"""
        # 每秒都会被调用（随任务状态刷新）：内容没变就不要动菜单，否则用户正打开着
        # "更多"时它会被清掉重建，菜单当场消失。
        if list(pages) == self._more and list(actions) == self._actions \
                and int(badge) == self._badge:
            return
        self._more = list(pages)
        self._actions = list(actions)
        self._badge = int(badge)
        self.more_menu.clear()
        for page_id, label in self._more:
            action = self.more_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(page_id == self._active_page)
            action.triggered.connect(
                lambda _checked=False, p=page_id: self.pageRequested.emit(p))
        if self._more and self._actions:
            self.more_menu.addSeparator()
        for page_id, label in self._actions:
            action = self.more_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, p=page_id: self.pageRequested.emit(p))
        self.more_button.setVisible(self._side_visible and bool(self._more or self._actions))
        self._sync_active()

    def set_side_visible(self, visible: bool) -> None:
        """旁路入口「更多」显示或收起（另外受菜单里是否有内容约束）。"""
        self._side_visible = bool(visible)
        self.more_button.setVisible(self._side_visible and bool(self._more or self._actions))

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
        self.more_button.setChecked(page in self.more_page_ids())
        # 正在看"更多"里的某一页时，按钮就写这一页的名字：只亮不写字，用户不知道自己在哪。
        title = next((label for page_id, label in self._more if page_id == page),
                     i18n.tr('更多'))
        self.more_button.setText(title + (f" ({self._badge})" if self._badge else ""))
        for action, (page_id, _label) in zip(self.more_menu.actions()[:len(self._more)], self._more):
            action.setChecked(page_id == page)
