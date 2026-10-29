"""Dialogs: model connection settings, task defaults and diagnostics.

Every dialog writes through :mod:`folith.qt.adapter`, never to a file of its
own: the connection is stored in the core's ``provider_config.json`` (0600) so
the old shell and the Qt client share one credential store, and the API key is
never displayed in clear text or copied into a report.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from . import i18n, platform_ui
from . import adapter
from . import bootstrap
from . import components
from . import icons
from . import prompts
from . import style as style_module
from .style import Card
from . import theme as theme_module
from .workers import TaskRunner


def _panel(title: str, hint: str = "") -> tuple[Card, QVBoxLayout]:
    """一张带标题的卡片 + 竖排内容区（层级靠"面"，不靠边框堆叠）。

    内边距 12 / 间距 8：这两处直接乘上"每张选项卡卡数 × 字段数"，
    是设置页纵向长度的主要来源（原先 14 / 10）。
    """
    card = Card()
    outer = QVBoxLayout(card)
    outer.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                             theme_module.SPACE["md"], theme_module.SPACE["md"])
    outer.setSpacing(theme_module.SPACE["sm"])
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    outer.addWidget(heading)
    if hint:
        heading.setToolTip(hint)
    return card, outer


def _setting_row(title: str, *controls: QWidget) -> QWidget:
    """设置分组里的一行：左边固定宽的名称，右边控件从左排起（阶段 1 画布）。"""
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme_module.SPACE["md"])
    for control in controls:
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    line = components.FormRow(title, value=holder)
    line.title_label.setFixedWidth(SETTING_LABEL_WIDTH)
    line.title_label.setWordWrap(True)        # 长语言的名称折成两行，不截断
    return line


#: 设置分组里左侧名称的宽度：五种语言里最长的「Interface language」也放得下。
SETTING_LABEL_WIDTH = 132


def _field(label: str, widget: QWidget, hint: str = "") -> QWidget:
    """表单字段：标签在控件**上方**（旧界面表单的排版，不是右侧窄列对齐）。

    右侧对齐的标签列在中文标签长短不一时会把输入框起点推得忽左忽右，
    长说明也没有地方放；标签在上、说明在下的结构更接近用户已有的旧界面。
    """
    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme_module.SPACE["xxs"])
    column.addWidget(style_module.field_label(label))
    column.addWidget(widget)
    if hint:
        widget.setToolTip(hint)
    return holder


def _scroll_page(inner: QWidget) -> QScrollArea:
    """标签页内容放进滚动区：设置页最长的一栏不该把整个对话框顶出屏幕。

    页面必须自带底色：`QScrollArea` 的 viewport 按约定是透明的（上面那行
    `setAutoFillBackground(False)`），内容比视口短时**剩下的那片就没有人画**——
    浅色主题下是纯黑。「扫描件识别」是唯一内容短到会露出来的一栏，所以此前没人看见。
    复用既有的 `QWidget#pageCanvas`（值与 `QDialog` 同为 `bg-page`），不新增 QSS。
    """
    inner.setObjectName("pageCanvas")
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setWidget(inner)
    return area


def _set_status(label: QLabel, text: str, tone: str = "muted") -> None:
    """内联状态文字：成功/失败要有颜色，不能都是同一撮灰字。

    空文本时整行收掉：设置页底部原先常驻一条**空的**状态标签，在内容区与
    「取消 / 保存」之间留出一条谁也说不清用途的空带（实测约 29px）。
    """
    label.setText(text)
    label.setObjectName({"ok": "statusOk", "danger": "statusError"}.get(tone, "hint"))
    label.setVisible(bool(str(text).strip()))
    style_module.repolish(label)


class EngineDialog(QDialog):
    """设置窗口：macOS 原生设置的形态（acceptance-feedback 8.3-1、D6、12-H）。

    * **非模态**：打开设置时仍可操作主窗口；⌘, 再按一次只是把它带到前面。
    * 顶部是带图标的页签（通用 / 模型 / 视觉与识别 / 任务默认值 / 存储 / 关于），
      窗口标题跟着当前页走，窗口高度随页内容伸缩。
    * **普通选项改了立即生效**（主题、语言、工作模式、任务默认值）——没有「保存 / 取消」。
      只有改错会让任务失败的「模型」「视觉与识别」两页保留「应用」，应用前先自动测试连接。

    控件与取值逻辑（``_collect`` / ``OWNED_FIELDS`` / ``_save``）沿用原来的对话框；
    ``_save`` 仍然是"把整页一次性落盘"，供脚本化运行与测试使用。
    """

    preferencesChanged = Signal()       # 立即生效的选项变了（窗口据此持久化并套用）
    connectionApplied = Signal()        # 模型 / 视觉连接已测试并保存
    revealRequested = Signal(str)
    diagnosticsRequested = Signal()
    shortcutsRequested = Signal()

    PAGES = (("general", "settings", "通用"), ("models", "sparkles", "模型"),
             ("defaults", "package", "任务默认值"),
             ("storage", "folder", "存储"), ("about", "info", "关于"))

    def __init__(self, config: adapter.EngineConfig, runner: TaskRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
        # 设置窗口始终在主窗口前面：点了主窗口它也不会被压到后面、找不回来。
        # 应用切到后台时它跟着藏起来（macwindow.hide_when_app_inactive），不会盖住别的应用。
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, platform_ui.is_macos())
        self.setModal(False)
        self.setWindowTitle(i18n.tr('设置'))
        self.setMinimumWidth(640)
        self.resize(720, 600)
        self.config = dataclasses.replace(config, connections=[dict(c) for c in config.connections],
                                          connection_roles=dict(config.connection_roles),
                                          connection_models=dict(config.connection_models))
        if not self.config.connections:
            self.config.ensure_connections()
        self.runner = runner
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("hint")
        # 空状态标签整行收掉（原因见 _set_status）。
        self._status.setVisible(False)

        self.theme_preference = theme_module.FOLLOW_SYSTEM
        self.language_preference = i18n.language()
        self.academic_preference = False
        self._loading = True

        from .settings_pages import AboutPage, StoragePage

        self.storage_page = StoragePage(runner)
        self.storage_page.revealRequested.connect(self.revealRequested.emit)
        self.about_page = AboutPage(runner, updater=getattr(parent, "updates", None))
        self.about_page.diagnosticsRequested.connect(self.diagnosticsRequested.emit)
        builders = {"general": self._build_appearance_tab,
                    "models": self._build_connection_tab,
                    "defaults": self._build_task_tab,
                    "storage": lambda: self.storage_page,
                    "about": lambda: self.about_page}
        # 工作模式的复选框在「任务默认值」里，但「通用」页先建：先把它造出来。
        self._make_academic_box()

        toolbar = QFrame()
        toolbar.setObjectName("settingsToolbar")
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["xs"],
                               theme_module.SPACE["md"], theme_module.SPACE["xs"])
        bar.setSpacing(theme_module.SPACE["xxs"])
        bar.addStretch(1)
        self.stack = QStackedWidget()
        self.tab_buttons: dict[str, QToolButton] = {}
        self.pages: dict[str, QWidget] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, icon, label in self.PAGES:
            button = QToolButton()
            button.setObjectName("settingsTab")
            button.setText(i18n.tr(label))
            button.setIcon(icons.icon(icon, 20, "text-secondary", color_on="accent"))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setAccessibleName(i18n.tr(label))
            button.clicked.connect(lambda _checked=False, value=key: self.show_page(value))
            group.addButton(button)
            bar.addWidget(button)
            self.tab_buttons[key] = button
            page = _scroll_page(builders[key]())
            self.pages[key] = page
            self.stack.addWidget(page)
        bar.addStretch(1)

        body = QVBoxLayout()
        body.setContentsMargins(20, theme_module.SPACE["sm"], 20, theme_module.SPACE["md"])
        body.setSpacing(theme_module.SPACE["sm"])
        body.addWidget(self.stack, 1)
        body.addWidget(self._status)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addLayout(body, 1)

        # 立即生效：改动合并 250 ms 再落盘（拖动、连续输入不会每一下都写一次）。
        self._defaults_timer = QTimer(self)
        self._defaults_timer.setSingleShot(True)
        self._defaults_timer.setInterval(250)
        self._defaults_timer.timeout.connect(self._apply_preferences)
        self._watch_immediate_fields()
        self._loading = False
        self.show_page("general")

    # -- 页签 ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        from . import macwindow

        macwindow.hide_when_app_inactive(self)

    def show_page(self, key: str) -> None:
        # 「视觉与识别」页已合并进「模型」；旧的 "vision" 调用方重定向到 "models"。
        if key == "vision":
            key = "models"
        if key not in self.pages:
            key = "general"
        self.tab_buttons[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        label = next(text for value, _icon, text in self.PAGES if value == key)
        self.setWindowTitle(i18n.tr(label))
        if key == "storage":
            self.storage_page.refresh()
        _set_status(self._status, "")
        self._fit_height()

    def current_page(self) -> str:
        widget = self.stack.currentWidget()
        return next((key for key, page in self.pages.items() if page is widget), "general")

    def _fit_height(self) -> None:
        """窗口高度随当前页的内容伸缩（macOS 设置窗口的行为），不超出屏幕。"""
        page = self.stack.currentWidget()
        inner = page.widget() if isinstance(page, QScrollArea) else page
        wanted = inner.sizeHint().height() + 120
        screen = (self.screen() or QGuiApplication.primaryScreen())
        cap = int(screen.availableGeometry().height() * 0.85) if screen else 820
        target = max(360, min(wanted, cap, 820))
        from . import motion

        start = self.height()
        if not self.isVisible():
            self.resize(self.width(), target)
            return
        if not hasattr(self, "_motion"):
            self._motion = motion.Motion(self)
        self._motion.animate("settings-height", start, target,
                             lambda value: self.resize(self.width(), round(value)),
                             kind="panel")

    # -- 立即生效的选项 -------------------------------------------------------
    #: 改错会让任务失败、因而要「应用」并先测试的字段；其余展示的字段改了立即生效。
    CONNECTION_FIELDS = frozenset({
        "provider", "model", "api_key", "base_url", "reasoning_effort",
        "reviewer_mode", "reviewer_provider", "reviewer_model", "reviewer_api_key",
        "reviewer_base_url", "connections", "connection_roles", "connection_models"})

    def _watch_immediate_fields(self) -> None:
        for box in (self.auto_term_box, self.review_box, self.tm_box, self.understanding_box,
                    self.cleanup_box, self.report_box, self.academic_box,
                    self.deliver_plain_box, self.deliver_bilingual_box, self.deliver_terms_box,
                    self.deliver_evidence_box, self.deliver_pdf_box,
                    self.deliver_original_pdf_box, self.deliver_original_docx_box,
                    self.deliver_epub_box):
            box.toggled.connect(self._immediate_changed)
        for combo in (self.theme_box, self.language_box, self.ocr_backend_box,
                      self.ocr_mode_box, self.ocr_language_box, self.default_mode_box, self.accent_box):
            combo.currentIndexChanged.connect(self._immediate_changed)
        self.target_box.currentTextChanged.connect(self._immediate_changed)
        self.ocr_language_box.editTextChanged.connect(self._immediate_changed)

    def _immediate_changed(self, *_args) -> None:
        if not self._loading:
            self._defaults_timer.start()

    def _defaults_config(self) -> adapter.EngineConfig:
        """当前**已保存**的连接 + 界面上的其余选项（正在改、还没应用的连接不混进来）。"""
        collected = self._collect()
        return dataclasses.replace(collected, **{
            name: getattr(self.config, name) for name in self.CONNECTION_FIELDS})

    def _apply_preferences(self) -> None:
        self._defaults_timer.stop()
        self.config = self._defaults_config()
        self.academic_preference = self.academic_box.isChecked()
        self.theme_preference = str(self.theme_box.currentData() or theme_module.FOLLOW_SYSTEM)
        self.language_preference = str(self.language_box.currentData() or i18n.DEFAULT_LANGUAGE)
        self.preferencesChanged.emit()

    def flush_preferences(self) -> None:
        """关闭窗口时把还在合并等待中的改动落盘。"""
        if self._defaults_timer.isActive():
            self._apply_preferences()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.flush_preferences()
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.flush_preferences()
        super().hideEvent(event)

    # -- 「应用」：模型与视觉连接 -------------------------------------------------
    def _apply_buttons(self) -> list:
        # 「视觉与识别」页已合并入「模型」页；只剩一个应用按钮。
        return [getattr(self, name) for name in ("models_apply_button",)
                if hasattr(self, name)]

    def _force_buttons(self) -> list:
        return [getattr(self, name) for name in ("models_force_button",)
                if hasattr(self, name)]

    def _apply_row(self, page: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, theme_module.SPACE["xs"], 0, 0)
        row.setSpacing(theme_module.SPACE["sm"])
        # 页面底部只有一个动作：用途改了以后「测试并保存」（阶段 1 画布）。
        apply_button = QPushButton(i18n.tr('测试并保存') if page == "models" else i18n.tr('保存'))
        style_module.mark_primary(apply_button)
        apply_button.clicked.connect(self.apply_connection)
        force = QPushButton(i18n.tr('仍然应用'))
        force.setToolTip(i18n.tr('不等测试通过就保存这份连接（未配置好之前无法启动翻译）。'))
        force.setVisible(False)
        force.clicked.connect(lambda: self._commit_connection(self._collect(), tested=False))
        row.addStretch(1)
        row.addWidget(force)
        row.addWidget(apply_button)
        setattr(self, f"{page}_apply_button", apply_button)
        setattr(self, f"{page}_force_button", force)
        return holder

    def apply_connection(self) -> None:
        """测试连接 → 通过才保存；失败时说明原因，并给出「仍然应用」。"""
        vision_problem = self._vision_problem()
        if vision_problem:
            _set_status(self._status, i18n.tr_dynamic(vision_problem), "danger")
            return
        config = self._collect()
        problem = i18n.tr_dynamic(config.connection_problem())
        if problem:
            _set_status(self._status, i18n.tr('无法应用：{v0}', v0=problem), "danger")
            self._show_force(True)
            return
        for button in self._apply_buttons():
            button.setEnabled(False)
        _set_status(self._status, i18n.tr('正在测试连接…'))

        def done(result) -> None:
            ok, message = result
            for button in self._apply_buttons():
                button.setEnabled(True)
            from . import connection_status

            connection_status.record("translation", config, ok, message)
            if ok:
                self._commit_connection(config, tested=True, message=message)
            else:
                _set_status(self._status, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
                self._show_force(True)

        def failed(message: str, _detail: str) -> None:
            for button in self._apply_buttons():
                button.setEnabled(True)
            _set_status(self._status, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
            self._show_force(True)

        self.runner.submit(adapter.test_connection, config, on_done=done, on_error=failed,
                           label="apply-connection")

    def _show_force(self, visible: bool) -> None:
        for button in self._force_buttons():
            button.setVisible(visible)

    def _commit_connection(self, collected: adapter.EngineConfig, *, tested: bool,
                           message: str = "") -> bool:
        """把连接字段（以及视觉连接）落盘；其余选项保持已生效的值。"""
        merged = dataclasses.replace(self.config, **{
            name: getattr(collected, name) for name in self.CONNECTION_FIELDS})
        try:
            self._apply_vision()
            merged.save()
        except Exception as exc:  # noqa: BLE001 - the user must see the reason
            _set_status(self._status, i18n.tr('无法保存设置：{v0}', v0=i18n.tr_dynamic(str(exc))),
                        "danger")
            return False
        self.config = merged
        self.connection_library.adopt(merged)
        self._show_force(False)
        _set_status(self._status, i18n.tr('连接正常，已应用：{v0}', v0=message) if tested
                    else i18n.tr('已应用（未通过测试）。'), "ok" if tested else "muted")
        self.connectionApplied.emit()
        return True

    # -- connection --------------------------------------------------------
    def _build_connection_tab(self) -> QWidget:
        """「模型」页：连接列表 + 用途（翻译/审校）+ 读取扫描页 + 测试并保存。

        原「视觉与识别」页合并到这里：读取扫描页是与「连接」「用途」并排的平级分组，
        「测试并保存」统一放在页面最下方。
        """
        from .connection_library import ConnectionLibrary
        self.provider_box = style_module.ComboBox()
        self.provider_box.addItems(list(self.config.providers))
        self.model_box = style_module.ComboBox()
        self.model_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_box.setMinimumContentsLength(20)
        self.model_box.setEditable(True)
        self.model_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.key_field = style_module.SecretField("")
        self.key_edit = self.key_field.line_edit
        self.base_edit = QLineEdit()
        self.reasoning_box = style_module.ComboBox()
        self.reasoning_row = _field(i18n.tr('推理强度'), self.reasoning_box)
        self.thinking_box = style_module.ComboBox()
        self.thinking_row = _field(i18n.tr('思考'), self.thinking_box)
        self.fetch_button = QPushButton(i18n.tr('获取可用模型'))
        self.fetch_button.clicked.connect(self._fetch_models)
        self.test_button = QPushButton(i18n.tr('测试连接'))
        self.test_button.clicked.connect(self._test_connection)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.provider_box.currentTextChanged.connect(self._provider_changed)
        self.model_box.currentTextChanged.connect(self._sync_reasoning)
        self.model_box.currentTextChanged.connect(self._sync_thinking)
        self._provider_changed(self.provider_box.currentText())
        self._sync_reasoning()
        self._sync_thinking()
        self.connection_library = ConnectionLibrary(self)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        layout.setSpacing(theme_module.SPACE["md"])
        layout.addWidget(self.connection_library)
        layout.addWidget(self._build_scan_section())
        layout.addStretch(1)
        layout.addWidget(self._apply_row("models"))
        return page

    def refresh_connection_states(self) -> None:
        """各连接标题下的状态行：圆点 + 同色文字（已连接 / 未测试 / 失败 / 沿用翻译模型）。"""
        from . import connection_status

        for role, label in (("translation", getattr(self, "translation_state", None)),
                            ("reviewer", getattr(self, "reviewer_state", None)),
                            ("vision", getattr(self, "vision_state", None))):
            if label is None:
                continue
            row = connection_status.status(role, self.config)
            text = {"ok": i18n.tr('已连接'), "failed": i18n.tr('上次测试失败'),
                    "untested": i18n.tr('未测试'), "missing": i18n.tr('未配置完整'),
                    "inherited": i18n.tr('沿用翻译模型')}[row["state"]]
            if row["at"]:
                text += " · " + i18n.tr('上次测试 {v0}', v0=row["at"].replace("T", " ")[:16])
            label.set_status(text, {"ok": "done", "danger": "error",
                                    "warning": "attention"}.get(row["tone"], "idle"))

    #: 默认翻译方式：（界面名, 预设键, 图标）。精细 = 核心里的「学术增强」预设。
    DEFAULT_MODES = (("快速", "快速", "play"), ("标准", "标准", "check-circle"),
                     ("精细", "学术增强", "shield-check"))
    #: 模式卡片下面的四步：（名称, 预设里的开关）。亮 = 这个模式会做。
    MODE_STEPS = (("画像", "enable_understanding"), ("术语", "auto_term"),
                  ("记忆", "use_tm"), ("审校", "enable_review"))

    def _build_task_tab(self) -> QWidget:
        """任务默认值（阶段 1 画布）：默认翻译方式 + MTI 一排小选项 · 目标语言与 PDF 纠错 · 交付文件。

        翻译流程的四个开关（术语、记忆、画像、审校）由翻译方式决定，准备页里还能逐项改；
        这里不再重复列出（控件仍在，保存时原样带回）。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        outer.setSpacing(theme_module.SPACE["md"])

        # 文案与「译前准备 · 翻译设置」逐字一致（同一个开关在两个面上必须同名）。
        self.auto_term_box = style_module.CheckBox(i18n.tr('自动识别术语'), page)
        self.auto_term_box.setChecked(self.config.auto_term)
        self.review_box = style_module.CheckBox(i18n.tr('译后自动审校'), page)
        self.review_box.setChecked(self.config.enable_review)
        self.tm_box = style_module.CheckBox(i18n.tr('使用翻译记忆'), page)
        self.tm_box.setChecked(self.config.use_tm)
        self.understanding_box = style_module.CheckBox(i18n.tr('建立文档画像'), page)
        self.understanding_box.setChecked(self.config.enable_understanding)
        for box in (self.auto_term_box, self.review_box, self.tm_box, self.understanding_box):
            box.hide()
        self.cleanup_box = components.Switch()
        self.cleanup_box.setAccessibleName(i18n.tr('PDF 原文先做模型纠错'))
        self.cleanup_box.setChecked(self.config.enable_source_cleanup)
        self.report_box = style_module.CheckBox(i18n.tr('完成后生成实践报告'), page)
        self.report_box.setChecked(self.config.enable_report)
        self.academic_box.toggled.connect(self._academic_toggled)
        self.report_box.setVisible(self.academic_box.isChecked())

        # ---- 默认翻译方式：与准备页同一组选择，这里只预选，不会自动开跑 ----------
        self.default_mode_box = style_module.ComboBox(page)
        for label, key, _icon in self.DEFAULT_MODES:
            self.default_mode_box.addItem(i18n.tr(label), key)
        self.default_mode_box.setCurrentIndex(max(0, self.default_mode_box.findData(
            self.config.default_translation_mode)))
        self.default_mode_box.hide()
        from folith.application.task_setup import STRATEGY_PRESETS
        self.mode_cards: dict[str, components.OptionCard] = {}
        cards = QHBoxLayout()
        cards.setSpacing(theme_module.SPACE["sm"])
        for label, key, icon in self.DEFAULT_MODES:
            preset = STRATEGY_PRESETS.get(key, {})
            card = components.OptionCard(key, i18n.tr(label), self._mode_steps_html(preset),
                                         icon=icon)
            card.toggled.connect(lambda on, value=key: on and self.default_mode_box.setCurrentIndex(
                self.default_mode_box.findData(value)))
            cards.addWidget(card, 1)
            self.mode_cards[key] = card
        self.default_mode_box.currentIndexChanged.connect(lambda _i: self._sync_mode_cards())
        self._sync_mode_cards()
        mti = QHBoxLayout()
        mti.setSpacing(theme_module.SPACE["lg"])
        mti_label = QLabel("MTI")
        style_module.set_text_role(mti_label, "label")
        mti.addWidget(mti_label)
        mti.addWidget(self.academic_box)
        mti.addWidget(self.report_box)
        mti.addStretch(1)
        mode_card, mode_body = _panel(i18n.tr('默认翻译方式'))
        mode_body.addLayout(cards)
        mode_body.addLayout(mti)
        outer.addWidget(mode_card)

        # ---- 目标语言 · PDF 纠错 ------------------------------------------------
        self.target_box = style_module.ComboBox()
        self.target_box.setEditable(True)
        self.target_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target_box.addItems(list(adapter.TARGET_LANGUAGES))
        self.target_box.setCurrentText(self.config.target_lang)
        self.target_box.setFixedWidth(200)
        slower = QLabel(i18n.tr('更慢'))
        style_module.set_text_role(slower, "time")
        general = components.FormGroup()
        general.add_row(_setting_row(i18n.tr('目标语言'), self.target_box))
        general.add_row(components.FormRow(i18n.tr('PDF 原文先做模型纠错'), self.cleanup_box,
                                           value=slower))
        outer.addWidget(general)

        # ---- 交付文件：两列复选框，不再列文件名 -----------------------------------
        delivery, delivery_body = _panel(i18n.tr('交付文件'))

        def fmt(attr: str, label: str, default: bool, badge: str = "") -> tuple:
            box = style_module.CheckBox(i18n.tr(label))
            box.setChecked(bool(getattr(self.config, attr, default)))
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme_module.SPACE["sm"])
            row.addWidget(box)
            if badge:
                row.addWidget(style_module.Chip(badge, "muted"))
            row.addStretch(1)
            setattr(self, f"_{attr}_holder", holder)
            return box, holder

        self.deliver_plain_box, plain = fmt("deliver_plain_docx", '纯译文 DOCX', True)
        self.deliver_bilingual_box, bilingual = fmt("deliver_bilingual_docx", '双语对照 DOCX', True)
        self.deliver_pdf_box, pdf = fmt("deliver_pdf", '译文 PDF', False)
        self.deliver_original_pdf_box, original = fmt(
            "deliver_original_pdf", '原排版译文 PDF', False, i18n.tr('仅 PDF'))
        # 默认值在选文件之前就定了，不知道源文件类型；导入时非 PDF 任务会去掉这一项。
        self.deliver_original_pdf_box.setToolTip(
            i18n.tr('仅 PDF 任务可用：按原文件的版式重建译文页面。'))
        self.deliver_original_docx_box, original_docx = fmt(
            "deliver_original_docx", '原格式译文 DOCX', False, i18n.tr('仅 DOCX'))
        # 与原排版 PDF 同理：默认值不知道源文件类型，非 DOCX 任务不会产出这一项。
        self.deliver_original_docx_box.setToolTip(
            i18n.tr('仅 DOCX 任务可用：在原 DOCX 文件上写回译文，保留样式、表格与页眉页脚。'))
        self.deliver_epub_box, epub = fmt("deliver_epub", 'EPUB 电子书', False)
        self.deliver_terms_box, terms = fmt("deliver_terms_xlsx", '术语表 XLSX', True)
        self.deliver_evidence_box, evidence = fmt("deliver_evidence", '段落证据 JSONL', True)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme_module.SPACE["xl"])
        grid.setVerticalSpacing(theme_module.SPACE["sm"])
        for index, widget in enumerate((plain, bilingual, pdf, original, original_docx, epub, terms, evidence)):
            grid.addWidget(widget, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        delivery_body.addLayout(grid)
        outer.addWidget(delivery)
        outer.addStretch(1)
        return page

    def _mode_steps_html(self, preset: dict) -> str:
        lit = style_module.html_color("accent")
        dim = style_module.html_color("text-muted")
        parts = []
        for label, key in self.MODE_STEPS:
            colour = lit if preset.get(key) else dim
            parts.append(f"<span style='color:{colour}'>●&nbsp;{i18n.tr(label)}</span>")
        return "&nbsp; ".join(parts)          # 末尾是普通空格：窄时按步折行

    def _sync_mode_cards(self) -> None:
        current = str(self.default_mode_box.currentData() or "标准")
        for key, card in self.mode_cards.items():
            card.blockSignals(True)
            card.setChecked(key == current)
            card.blockSignals(False)
            card._sync()

    def _make_academic_box(self) -> None:
        # 实践报告 / 案例 / 终稿 QA 是专用能力：默认不出现，用户主动打开"学术专业模式"才显示。
        self.academic_box = style_module.CheckBox(i18n.tr('实践报告 · 案例与合规 · 终稿 QA'))
        self.academic_box.setToolTip(i18n.tr('打开后，工作台多出实践报告、案例与合规、终稿 QA 三页。'))
        self.academic_box.setChecked(bool(self.academic_preference or self.config.enable_report))

    def _select_ocr_language(self, value: str) -> None:
        """按**配置值**选中对应条目。

        必须用 `setCurrentIndex`（而不是 `setCurrentText(标签)`）：可编辑下拉框里
        `setCurrentText` 只改显示文本、不选中条目，于是 `currentData()` 为空，保存时
        就会把**标签**写进配置（"简体中文 + English" 而不是 `chi_sim+eng`）——这正是
        我要找的那类"标签泄漏成取值"的问题。
        未知代码（用户自己填的）则直接显示代码本身，保存时按文本处理。
        """
        wanted = str(value or "").strip()
        for index in range(self.ocr_language_box.count()):
            if str(self.ocr_language_box.itemData(index) or "") == wanted:
                self.ocr_language_box.setCurrentIndex(index)
                return
        self.ocr_language_box.setCurrentIndex(-1)
        self.ocr_language_box.setEditText(wanted)

    # -- vision connection -------------------------------------------------
    def _vision_form(self) -> dict:
        selected = self.connection_library.roles.get("vision")
        profile = next((p for p in self.connection_library.profiles
                        if p["id"] == selected), None)
        if not profile:
            return {}
        from folith.model_connections import environment_key
        return {"provider": profile["provider"],
                "model": self.connection_library.role_models.get("vision") or profile["model"],
                "api_key": profile.get("api_key") or environment_key(profile["provider"]),
                "base_url": profile.get("base_url", ""),
                "max_tokens": self._vision_view["max_tokens"],
                "reasoning_mode": self._vision_view["reasoning_mode"]}

    def _vision_changed(self) -> bool:
        form = self._vision_form()
        if not form:
            return bool(self._vision_view["dedicated"])
        stored = adapter.core.load_ocr_online_config() or {}
        return any(form.get(k) != stored.get(k) for k in
                   ("provider", "model", "api_key", "base_url"))

    def _vision_problem(self) -> str:
        form = self._vision_form()
        if not form:
            return ""
        return adapter.vision_connection_problem(
            provider=form["provider"], model=form["model"], api_key=form["api_key"],
            has_saved_key=False)

    def _apply_vision(self) -> None:
        if not self._vision_changed():
            return
        form = self._vision_form()
        if form:
            self._vision_view = adapter.save_vision_connection(**form)
        else:
            self._vision_view = adapter.clear_vision_connection()

    def _build_scan_section(self) -> components.FormGroup:
        """读取扫描页：与「连接」「用途」并排的平级分组（无嵌套子卡）。

        行序（从上到下）：
          节标题 → 视觉模型（连接 + 模型 + 测试视觉）→ 输入类型提示（#48）→
          能力警告（#258）→ 静态说明 → 读取方式卡片 → 何时识别 → 识别语言（仅本机 OCR）。

        所有控件属性仍挂在 EngineDialog 上（ocr_backend_box / ocr_mode_picker /
        vision_option / vision_connection_box 等），供其他页面与测试引用。
        """
        # ── 平级 FormGroup（先建；后面的隐藏数据控件以它为 parent 确保在页面层级内）────
        group = components.FormGroup()

        # ── OCR 数据控件（hidden data sources，parent=group）──────────────────
        # 不显示、只当数据模型：parent=group 使 isAncestorOf 测试通过并避免顶层窗口出现。
        self.ocr_language_box = style_module.ComboBox(group)
        # 可编辑：既能从核心注册表里选（30 项，带中文标签），也能直接填语言代码。
        self.ocr_language_box.setEditable(True)
        self.ocr_language_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ocr_language_box.setFixedWidth(220)
        for label, value in adapter.ocr_language_choices():
            self.ocr_language_box.addItem(label, value)
        self._select_ocr_language(self.config.ocr_language)
        self.ocr_mode_box = style_module.ComboBox(group)
        # 标签给用户看，data 给核心用：两者必须对齐，否则核心会静默回退成 auto
        # （历史缺陷：界面写 always/never，核心只认 force/off）。
        for label, mode in adapter.OCR_MODE_CHOICES:
            self.ocr_mode_box.addItem(label, mode)
        self.ocr_mode_box.setCurrentText(adapter.ocr_mode_label(self.config.ocr_mode))
        self.ocr_mode_box.hide()
        self.ocr_backend_box = style_module.ComboBox(group)
        for label, value in adapter.OCR_BACKEND_CHOICES:
            self.ocr_backend_box.addItem(label, value)
        index = self.ocr_backend_box.findData(self.config.ocr_backend or "online")
        self.ocr_backend_box.setCurrentIndex(index if index >= 0 else 0)
        self.ocr_backend_box.hide()

        command = adapter.core._find_tesseract()
        local_index = self.ocr_backend_box.findData("tesseract")
        if local_index >= 0:
            self.ocr_backend_box.model().item(local_index).setEnabled(bool(command))
        if not command and self.ocr_backend_box.currentData() == "tesseract":
            self.ocr_backend_box.setCurrentIndex(self.ocr_backend_box.findData("online"))

        # ── 读取方式 OptionCards ────────────────────────────────────────────
        self.vision_option = components.OptionCard("online", i18n.tr('视觉理解'), icon="eye")
        if command:
            local_detail = ("Tesseract " + adapter.core._ocr_backend_version("tesseract", command)
                            + " · " + ", ".join(adapter.core.installed_ocr_languages()))
        else:
            local_detail = i18n.tr('未安装')
        self.local_option = components.OptionCard("tesseract", i18n.tr('本机 OCR'),
                                                  local_detail, icon="file-text")
        self.local_option.setEnabled(bool(command))
        self.ocr_local_info = self.local_option.detail_label if command else None
        options = QWidget()
        options_row = QHBoxLayout(options)
        options_row.setContentsMargins(0, 0, 0, 0)
        options_row.setSpacing(theme_module.SPACE["sm"])
        options_row.addWidget(self.vision_option, 1)
        options_row.addWidget(self.local_option, 1)
        options.setMinimumWidth(380)
        for option in (self.vision_option, self.local_option):
            option.toggled.connect(lambda on, value=option.key: on and self.ocr_backend_box.setCurrentIndex(
                self.ocr_backend_box.findData(value)))
        read_controls = [options]
        if not command:
            install = QPushButton(i18n.tr('如何安装…'))
            style_module.set_button(install, "plain", small=True)
            from .settings_pages import _open_url
            from .support import TESSERACT_INSTALL_URL
            install.clicked.connect(lambda: _open_url(TESSERACT_INSTALL_URL))
            read_controls.append(install)

        # ── 何时识别 分段控件 ──────────────────────────────────────────────
        self.ocr_mode_picker = style_module.SegmentedControl(
            [(mode, i18n.tr(label.split("（")[0])) for label, mode in adapter.OCR_MODE_CHOICES])
        self.ocr_mode_picker.set_value(str(self.ocr_mode_box.currentData() or "auto"))
        self.ocr_mode_picker.changed.connect(
            lambda value: self.ocr_mode_box.setCurrentIndex(self.ocr_mode_box.findData(value)))
        self.ocr_mode_box.currentIndexChanged.connect(
            lambda _i: self.ocr_mode_picker.set_value(str(self.ocr_mode_box.currentData() or "")))

        # 节标题行
        head = QWidget()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                                    theme_module.SPACE["sm"], theme_module.SPACE["xs"])
        head_label = QLabel(i18n.tr('读取扫描页'))
        style_module.set_text_role(head_label, "section")
        head_label.setToolTip(
            i18n.tr('只影响没有可用文本层的页面（扫描件）；文本层 PDF 与 DOCX 不走这一步。'))
        head_row.addWidget(head_label, 1)
        group.add_row(head)

        # 视觉模型行：连接选择 + 模型选择 + 测试视觉（均在同一行）
        scan_test = QPushButton(i18n.tr('测试视觉'))
        style_module.set_button(scan_test, "plain", small=True)
        scan_test.clicked.connect(self._test_scan_vision)
        self.scan_vision_test_button = scan_test
        cbox = self.vision_connection_box
        mbox = self.connection_library.model_selectors["vision"]
        # 给连接框和模型框各分一份弹性空间，防止德语等长文本被截断
        vrow_holder = QWidget()
        vrow = QHBoxLayout(vrow_holder)
        vrow.setContentsMargins(0, 0, 0, 0)
        vrow.setSpacing(theme_module.SPACE["sm"])
        vrow.addWidget(cbox, 1, Qt.AlignmentFlag.AlignVCenter)
        vrow.addWidget(mbox, 1, Qt.AlignmentFlag.AlignVCenter)
        vrow.addWidget(scan_test, 0, Qt.AlignmentFlag.AlignVCenter)
        vision_row = components.FormRow(i18n.tr('视觉模型'), value=vrow_holder)
        vision_row.title_label.setFixedWidth(SETTING_LABEL_WIDTH)
        vision_row.title_label.setWordWrap(True)
        group.add_row(vision_row)

        # 输入类型提示（#48，动态，直接在选择器下方）
        vm_hint = self.connection_library.vision_modality_hint
        vh_holder = QWidget()
        vh_layout = QHBoxLayout(vh_holder)
        vh_layout.setContentsMargins(theme_module.SPACE["lg"], 0, theme_module.SPACE["md"], 0)
        vh_layout.addWidget(vm_hint)
        vh_holder.inset = theme_module.SPACE["lg"]
        group.add_row(vh_holder)

        # 能力警告（#258，动态，直接在选择器下方）
        cap_warn = self.connection_library.capability_warnings.get("vision")
        if cap_warn:
            cw_holder = QWidget()
            cw_layout = QHBoxLayout(cw_holder)
            cw_layout.setContentsMargins(theme_module.SPACE["lg"], 0,
                                         theme_module.SPACE["md"], 0)
            cw_layout.addWidget(cap_warn)
            cw_holder.inset = theme_module.SPACE["lg"]
            group.add_row(cw_holder)

        # 静态说明行（始终可见）
        hint_lbl = QLabel(i18n.tr('需要能看图并按格式回答的视觉模型'))
        hint_lbl.setObjectName("hint")
        hint_holder = QWidget()
        hint_layout = QHBoxLayout(hint_holder)
        hint_layout.setContentsMargins(theme_module.SPACE["md"], 0, theme_module.SPACE["md"],
                                       theme_module.SPACE["xxs"])
        hint_layout.addWidget(hint_lbl)
        hint_holder.inset = components.FORM_ROW_INSET_PLAIN
        group.add_row(hint_holder)

        # 读取方式行
        group.add_row(_setting_row(i18n.tr('读取方式'), *read_controls))

        # 何时识别行
        group.add_row(_setting_row(i18n.tr('何时识别'), self.ocr_mode_picker))

        # 识别语言行（仅本机 OCR 时可见）
        self.ocr_language_row = _setting_row(i18n.tr('识别语言'), self.ocr_language_box)
        group.add_row(self.ocr_language_row)

        # ── 同步回调 ──────────────────────────────────────────────────────
        def sync_backend(*_args):
            value = str(self.ocr_backend_box.currentData() or "online")
            for option in (self.vision_option, self.local_option):
                option.blockSignals(True)
                option.setChecked(option.key == value)
                option.blockSignals(False)
            self.ocr_language_row.setVisible(value != "online")
        self.ocr_backend_box.currentIndexChanged.connect(sync_backend)
        sync_backend()
        self._sync_vision_detail()
        self.vision_connection_box.currentIndexChanged.connect(
            lambda _i: self._sync_vision_detail())
        self.connection_library.model_selectors["vision"].currentTextChanged.connect(
            lambda _t: self._sync_vision_detail())
        return group

    def _sync_vision_detail(self) -> None:
        """视觉理解那张卡的第二行：用的是哪个连接 · 模型。"""
        if not hasattr(self, "vision_option"):
            return
        library = getattr(self, "connection_library", None)
        selected = library.roles.get("vision") if library else ""
        profile = next((p for p in (library.profiles if library else [])
                        if p["id"] == selected), None)
        if profile:
            model = library.role_models.get("vision") or profile["model"]
            self.vision_option.set_detail(f"{profile['name']} · {model}")
        else:
            # 「沿用翻译连接」（默认）：卡片不显示具体连接，不指向别处。
            self.vision_option.set_detail(i18n.tr('沿用翻译连接'))

    def _test_scan_vision(self) -> None:
        """「测试视觉」：对当前视觉角色选中的模型发一次极小图像请求（同编辑器里的按钮）。"""
        from . import connection_status
        from folith.model_connections import environment_key
        lib = self.connection_library
        profile = next((p for p in lib.profiles if p["id"] == lib.roles.get("vision")), None)
        if not profile:
            _set_status(self._status, i18n.tr('请先选择专用视觉连接（或留空沿用翻译连接）'), "danger")
            return
        model = str(lib.role_models.get("vision") or profile["model"])
        provider = str(profile.get("provider") or "")
        key = str(profile.get("api_key") or "") or environment_key(provider)
        base = str(profile.get("base_url") or "")
        if hasattr(self, "scan_vision_test_button"):
            self.scan_vision_test_button.setEnabled(False)
        _set_status(self._status, i18n.tr('正在测试图像输入…'))

        def done(result) -> None:
            ok, message = result
            if hasattr(self, "scan_vision_test_button"):
                self.scan_vision_test_button.setEnabled(True)
            connection_status.record_vision_probe(provider, model, base, key, bool(ok), str(message))
            _set_status(self._status, str(message), "ok" if ok else "danger")
            lib._update_vision_hint()

        def failed(message, _detail) -> None:
            if hasattr(self, "scan_vision_test_button"):
                self.scan_vision_test_button.setEnabled(True)
            _set_status(self._status, i18n.tr('图像测试失败：{v0}', v0=message), "danger")

        self.runner.submit(adapter.test_vision_model, provider, key, model, base,
                           on_done=done, on_error=failed, label="test-scan-vision")

    def _build_appearance_tab(self) -> QWidget:
        """外观（三段）· 主题色（色点）· 界面语言：一张分组卡，改了立即生效（阶段 1 画布）。

        下拉框仍是保存时的数据来源（窗口读 ``theme_box`` / ``accent_box``），
        只是不显示：用户看到的是分段控件和色点。
        """
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        page_layout.setSpacing(theme_module.SPACE["sm"])
        self.theme_box = style_module.ComboBox(page)
        for name in (theme_module.FOLLOW_SYSTEM, "light", "dark"):
            self.theme_box.addItem(i18n.tr(theme_module.label(name)), name)
        index = self.theme_box.findData(self.theme_preference)
        self.theme_box.setCurrentIndex(max(0, index))
        self.theme_box.hide()
        self.accent_box = style_module.ComboBox(page)
        for name, label in theme_module.ACCENT_LABELS:
            self.accent_box.addItem(i18n.tr(label), name)
        self.accent_box.hide()
        self.accent_picker = components.AccentPicker(
            [(name, i18n.tr(label), theme_module.accent_swatch(name))
             for name, label in theme_module.ACCENT_LABELS])
        self.accent_picker.set_value(str(self.accent_box.currentData() or "blue"))
        self.accent_picker.changed.connect(
            lambda value: self.accent_box.setCurrentIndex(max(0, self.accent_box.findData(value))))
        self.accent_box.currentIndexChanged.connect(
            lambda _index: self.accent_picker.set_value(str(self.accent_box.currentData() or "blue")))

        self.language_box = style_module.ComboBox()
        for code, label in i18n.available_languages():
            self.language_box.addItem(label, code)
        self._select_language(self.language_preference)
        self.language_box.setFixedWidth(200)
        restart = QLabel(i18n.tr('重启后生效'))
        style_module.set_text_role(restart, "time")

        group = components.FormGroup()
        group.add_row(_setting_row(i18n.tr('外观'), self._build_appearance_picker()))
        group.add_row(_setting_row(i18n.tr('主题色'), self.accent_picker))
        group.add_row(_setting_row(i18n.tr('界面语言'), self.language_box, restart))
        page_layout.addWidget(group)
        page_layout.addStretch(1)
        return page

    def _build_appearance_picker(self) -> QWidget:
        """首次引导用的外观三段：和「外观」下拉框是同一个值，两边互相跟随。"""
        picker = style_module.SegmentedControl(
            [(name, i18n.tr(theme_module.label(name)))
             for name in (theme_module.FOLLOW_SYSTEM, "light", "dark")])
        picker.set_value(str(self.theme_box.currentData() or theme_module.FOLLOW_SYSTEM))
        picker.changed.connect(
            lambda value: self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(value))))
        self.theme_box.currentIndexChanged.connect(
            lambda _index: picker.set_value(
                str(self.theme_box.currentData() or theme_module.FOLLOW_SYSTEM)))
        return picker

    def _select_language(self, code: str) -> None:
        index = self.language_box.findData(i18n.normalize(code))
        self.language_box.setCurrentIndex(index if index >= 0 else 0)

    def set_language_preference(self, code: str) -> None:
        """窗口把**当前**语言带进对话框，保存时再读回去。"""
        self.language_preference = i18n.normalize(code)
        if hasattr(self, "language_box"):
            self._select_language(self.language_preference)

    def set_academic_preference(self, on: bool) -> None:
        """窗口把**当前**偏好带进对话框（原因同 set_language_preference）。"""
        self.academic_preference = bool(on)
        self.academic_box.setChecked(bool(on) or bool(self.config.enable_report))

    def _academic_toggled(self, on: bool) -> None:
        """关掉学术专业模式就不再生成实践报告（避免一个看不见的选项悄悄生效）。"""
        self.report_box.setVisible(on)
        if not on:
            self.report_box.setChecked(False)

    def _provider_changed(self, provider: str) -> None:
        models = self.config.models_for(provider)
        self.model_box.clear()
        self.model_box.addItems(models)
        if self.config.provider == provider and self.config.model:
            self.model_box.setCurrentText(self.config.model)
        elif models:
            default = self.config.default_model_for(provider) or models[0]
            self.model_box.setCurrentText(default)
        hint = self.config.provider_hint(provider)
        self.base_edit.setEnabled(True)
        self.base_edit.setPlaceholderText(hint or "http(s)://主机/v1")
        if self.config.needs_base_url(provider):
            self.base_edit.setToolTip(i18n.tr('自定义中转站必须填写 http(s)://…/v1 地址'))
        else:
            self.base_edit.setToolTip(i18n.tr('留空使用该提供商的官方地址'))

    #: 对话框真正**展示**的字段。其余字段必须原样保留——`_collect` 以前新建一份
    #: `EngineConfig` 只传这些字段，于是每次"打开设置 → 保存"都会把没展示的字段
    #: （翻译理论、风格规则、切分方式、并发数、标注、推理档位）静默重置成默认值。
    OWNED_FIELDS = frozenset({
        "provider", "model", "api_key", "base_url", "target_lang",
        "auto_term", "enable_review", "use_tm", "enable_understanding",
        "enable_source_cleanup", "enable_report", "reviewer_mode",
        "reviewer_provider", "reviewer_model", "reviewer_api_key",
        "reviewer_base_url", "ocr_language", "ocr_mode", "ocr_backend",
        "deliver_plain_docx", "deliver_bilingual_docx", "deliver_terms_xlsx",
        "deliver_evidence", "deliver_pdf", "deliver_original_pdf", "deliver_original_docx",
        "deliver_epub", "connections", "connection_roles", "connection_models", "default_translation_mode",
    })

    def _collect(self) -> adapter.EngineConfig:
        # 以现有配置为基底，只覆盖对话框展示的字段：任何未展示（或将来新增）的
        # 字段都不会因为一次"保存"而丢失。
        config = dataclasses.replace(
            self.config,
            provider=self.provider_box.currentText().strip(),
            model=self.model_box.currentText().strip(),
            api_key=self.key_edit.text().strip(),
            base_url=self.base_edit.text().strip(),
            target_lang=self.target_box.currentText().strip()
            or self.config.target_lang,
            auto_term=self.auto_term_box.isChecked(),
            enable_review=self.review_box.isChecked(),
            use_tm=self.tm_box.isChecked(),
            enable_understanding=self.understanding_box.isChecked(),
            enable_source_cleanup=self.cleanup_box.isChecked(),
            enable_report=self.report_box.isChecked(),
            # 当前模型提供推理强度选项时才取对话框里的选择；不提供时这一项没有显示，
            # 保留原值（核心对不支持的模型会忽略它），不能被一次"保存"悄悄清掉。
            reasoning_effort=str(self.reasoning_box.currentData() or "")
            if self.reasoning_box.count() > 1 else self.config.reasoning_effort,
        )
        # 优先取条目 data（核心取值）；没有选中条目时用输入文本（用户手填的代码）。
        index = self.ocr_language_box.currentIndex()
        if index >= 0:
            chosen = self.ocr_language_box.itemData(index)
        else:
            chosen = self.ocr_language_box.currentText().strip()
        if chosen in (None, "", adapter.OCR_LANGUAGE_AUTO):
            config.ocr_language = ""
        else:
            config.ocr_language = str(chosen)
        config.ocr_mode = str(self.ocr_mode_box.currentData()
                              or adapter.normalize_ocr_mode(
                                  self.ocr_mode_box.currentText()))
        config.ocr_backend = str(self.ocr_backend_box.currentData()
                                 or self.ocr_backend_box.currentText())
        config.deliver_plain_docx = self.deliver_plain_box.isChecked()
        config.deliver_bilingual_docx = self.deliver_bilingual_box.isChecked()
        config.deliver_terms_xlsx = self.deliver_terms_box.isChecked()
        config.deliver_evidence = self.deliver_evidence_box.isChecked()
        config.deliver_pdf = self.deliver_pdf_box.isChecked()
        config.deliver_original_pdf = self.deliver_original_pdf_box.isChecked()
        config.deliver_original_docx = self.deliver_original_docx_box.isChecked()
        config.deliver_epub = self.deliver_epub_box.isChecked()
        config.default_translation_mode = str(self.default_mode_box.currentData() or "标准")
        from folith.model_connections import effective_config
        library = self.connection_library
        profiles, roles = list(library.profiles), dict(library.roles)
        config.connection_models = dict(library.role_models)
        pending = library.pending_profile()
        if pending is not None:
            previous = next((p for p in profiles if p["id"] == pending["id"]), None)
            if previous is None:
                profiles.append(pending)
                roles["translation"] = pending["id"]
            else:
                profiles[profiles.index(previous)] = pending
                # 用途原本跟着这条连接的默认模型走：编辑区换了模型，用途也跟着换。
                for role, selected in roles.items():
                    if selected == pending["id"] and \
                            config.connection_models.get(role, previous["model"]) == previous["model"]:
                        config.connection_models[role] = pending["model"]
        return effective_config(config, profiles, roles)

    def _editor_config(self):
        from folith.model_connections import environment_key
        provider = self.provider_box.currentText()
        return dataclasses.replace(self.config, provider=provider, reviewer_mode="same",
            model=self.model_box.currentText().strip(),
            api_key=self.key_edit.text().strip() or environment_key(provider),
            base_url=self.base_edit.text().strip())

    def _save(self) -> None:
        vision_problem = self._vision_problem()
        if vision_problem:
            _set_status(self._status, i18n.tr_dynamic(vision_problem), "danger")
            return
        config = self._collect()
        problem = i18n.tr_dynamic(config.connection_problem())
        if problem and bootstrap.automation():
            # 脚本化运行：不弹"配置不完整，仍要保存吗"，直接按"保存"处理。
            self._status.setText(i18n.tr('配置不完整：{v0}（自动化运行按保存处理）', v0=problem))
        elif problem:
            if not prompts.ConfirmDialog.ask(
                    self, i18n.tr('配置不完整'),
                    i18n.tr('{v0}。\n\n仍然保存这份配置吗？（未配置好之前无法启动翻译）', v0=problem),
                    yes=i18n.tr('仍然保存'), no=i18n.tr('返回检查'), tone="warn"):
                return
        try:
            self._apply_vision()
        except adapter.AdapterError as exc:
            _set_status(self._status, i18n.tr_dynamic(str(exc)), "danger")
            return
        try:
            config.save()
        except Exception as exc:  # noqa: BLE001 - the user must see the reason
            if bootstrap.automation():
                self._status.setText(i18n.tr('无法保存设置：{v0}', v0=exc))
            else:
                _set_status(self._status, i18n.tr('无法保存设置：{v0}', v0=exc), "danger")
            return
        self.config = config
        self.academic_preference = self.academic_box.isChecked()
        self.theme_preference = str(
            self.theme_box.currentData() or theme_module.FOLLOW_SYSTEM)
        self.language_preference = str(
            self.language_box.currentData() or i18n.DEFAULT_LANGUAGE)
        self._defaults_timer.stop()
        self.preferencesChanged.emit()
        self.connectionApplied.emit()
        self.accept()

    def _test_connection(self) -> None:
        config = self._editor_config()
        self.test_button.setEnabled(False)
        _set_status(self.test_label, "正在测试…")

        def done(result) -> None:
            ok, message = result
            self.test_button.setEnabled(True)
            _set_status(self.test_label,
                        i18n.tr("连接正常：{v0}", v0=message) if ok
                        else i18n.tr("连接失败：{v0}", v0=message),
                        "ok" if ok else "danger")
            from . import connection_status

            connection_status.record("translation", config, ok, message)
            # 标题下的状态行按**已保存**的配置显示；测的是界面上的值，一致时才会变成「已连接」。
            self.refresh_connection_states()
            self.connection_library.refresh()

        def failed(message: str, _detail: str) -> None:
            self.test_button.setEnabled(True)
            _set_status(self.test_label, f"连接失败：{message}", "danger")

        if config.connection_problem():
            self.test_button.setEnabled(True)
            _set_status(self.test_label,
                        i18n.tr("无法测试：{v0}",
                                v0=i18n.tr_dynamic(
                                    config.connection_problem())), "danger")
            return
        self.runner.submit(adapter.test_connection, config,
                           on_done=done, on_error=failed, label="test-connection")

    def _sync_reasoning(self) -> None:
        """按当前提供商与模型给出可选的推理强度；不认识的模型不显示这一项。"""
        options = adapter.reasoning_effort_options(
            self.provider_box.currentText().strip(), self.model_box.currentText().strip())
        current = str(self.reasoning_box.currentData() or self.config.reasoning_effort or "")
        self.reasoning_box.blockSignals(True)
        self.reasoning_box.clear()
        self.reasoning_box.addItem(i18n.tr('自动'), "")
        labels = {"low": "低", "medium": "中", "high": "高"}
        for value in options:
            self.reasoning_box.addItem(i18n.tr(labels.get(value, value)), value)
        index = self.reasoning_box.findData(current)
        self.reasoning_box.setCurrentIndex(max(index, 0))
        self.reasoning_box.blockSignals(False)
        self.reasoning_row.setVisible(bool(options))

    def _sync_thinking(self) -> None:
        """按当前模型给出可用的思考开关；没实测过的模型不显示这一项，也不会被写入连接。"""
        options = adapter.thinking_control_options(
            self.provider_box.currentText().strip(), self.model_box.currentText().strip())
        current = str(self.thinking_box.currentData() or "")
        self.thinking_box.blockSignals(True)
        self.thinking_box.clear()
        self.thinking_box.addItem(i18n.tr('服务默认'), "provider_default")
        for value in options:
            self.thinking_box.addItem(
                i18n.tr(adapter.THINKING_MODE_LABELS.get(value, value)), value)
        self.thinking_box.setCurrentIndex(max(self.thinking_box.findData(current), 0))
        self.thinking_box.blockSignals(False)
        self.thinking_row.setVisible(bool(options))

    def _fetch_models(self) -> None:
        provider = self.provider_box.currentText().strip()
        api_key = self._editor_config().api_key
        base_url = self.base_edit.text().strip()
        self.fetch_button.setEnabled(False)
        _set_status(self.test_label, "正在获取模型列表…")

        def done(result) -> None:
            ok, models, message, modalities = result
            self.fetch_button.setEnabled(True)
            _set_status(self.test_label, str(message), "ok" if ok else "danger")
            if ok and models:
                current = self.model_box.currentText()
                self.model_box.clear()
                self.model_box.addItems([str(item) for item in models])
                self.model_box.setCurrentText(current or str(models[0]))
                known = [m.strip() for m in self.connection_library.models.text().split(",") if m.strip()]
                self.connection_library.models.setText(", ".join(dict.fromkeys(known + [str(m) for m in models])))
                # #48：目录里声明过的输入模态跟着这次拉取走，交给连接库显示/保存。
                self.connection_library.set_fetched_modalities(dict(modalities or {}))

        def failed(message: str, _detail: str) -> None:
            self.fetch_button.setEnabled(True)
            _set_status(self.test_label, f"获取失败：{message}", "danger")

        self.runner.submit(adapter.provider_models, provider, api_key, base_url,
                           on_done=done, on_error=failed, label="fetch-models")


class DiagnosticsDialog(QDialog):
    """Read-only support facts plus the paths a tester may need to hand over."""

    def __init__(self, report: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        import json

        self.setWindowTitle(i18n.tr('诊断信息'))
        self.resize(720, 560)
        self.report = report

        title = QLabel(i18n.tr('诊断信息'))
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            i18n.tr('支持人员需要的运行事实。**不包含** API Key、文档正文，也不会打包整个用户数据目录——只写入下面这份文本。'))
        subtitle.setObjectName("hint")
        subtitle.setWordWrap(True)

        view = QTextBrowser(self)
        view.setObjectName("diagnosticsView")
        view.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        view.setPlainText(json.dumps(report, ensure_ascii=False, indent=2))
        self.view = view

        # 关键事实单独列在顶部：出问题时最先要看的就是这几项。
        app = report.get("app") or {}
        support = report.get("support") or {}
        locations = report.get("locations") or {}
        runtime = report.get("runtime") or {}
        summary = Card()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        summary_layout.setSpacing(6)
        heading = QLabel(i18n.tr('概览'))
        heading.setObjectName("cardTitle")
        summary_layout.addWidget(heading)
        facts = QFormLayout()
        # 标签左对齐：右对齐的窄标签列会让"版本/运行方式"这些两字标签浮在中间，
        # 值列起点也随标签长度跳动；左对齐后这一栏才像一张事实表。
        facts.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        facts.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        facts.setHorizontalSpacing(16)
        facts.setVerticalSpacing(6)
        declared = [support.get("target_platform"), support.get("target_arch"),
                    support.get("minimum_system_version")]
        if all(str(item or "").strip() for item in declared):
            support_text = (f"{declared[0]} · {declared[1]} · 最低 {declared[2]}"
                            f"（{support.get('minimum_system_version_source') or '来源未记录'}）")
            support_tip = ""
        else:
            # 源码运行时没有随包构建记录：如实说明，而不是摆三个破折号。
            # 说明文字放在悬停提示里——QLabel 对没有空格的中文长句不会换行，会被裁掉。
            support_text = "源码运行"
            support_tip = ("源码运行时没有随包构建记录，因此没有声明最低系统版本与"
                           "目标架构；随包运行时会显示实测值。")
        for label, value in (
            ("版本", f"{app.get('version') or '—'}（{app.get('build_id') or '未知构建'}）"),
            ("运行方式", "随包运行" if app.get("frozen") else "源码运行"),
            ("Python / PySide6 / Qt",
             f"{runtime.get('python_version') or '—'} / "
             f"{runtime.get('pyside6_version') or '—'} / "
             f"{runtime.get('qt_version') or '—'}"),
            ("支持范围", support_text),
            ("数据目录", str(locations.get("data_dir") or "—")),
            ("日志目录", str(locations.get("log_dir") or "—")),
            ("任务数", str(locations.get("job_count", "—"))),
        ):
            text = str(value)
            if label.endswith("目录") and text and text != "—":
                # 路径要能完整看到、能选中复制：QLabel 对没有空格的路径无法换行，
                # 会被直接裁掉（"~/Dev/FolioThread/" 后面就看不见了）。
                field = QLineEdit(text)
                field.setObjectName("pathValue")
                field.setReadOnly(True)
                field.setCursorPosition(0)
                field.setToolTip(text)
                field.setFrame(False)
                field.setMinimumWidth(320)
                facts.addRow(style_module.field_label(label), field)
                continue
            value_label = QLabel(text)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            if label == "支持范围" and support_tip:
                value_label.setToolTip(support_tip)
            facts.addRow(style_module.field_label(label), value_label)
        summary_layout.addLayout(facts)

        # 状态标签：导出结果与失败原因都要有落点（脚本化运行不弹框）。
        self._status = QLabel("")
        self._status.setObjectName("hint")
        self._status.setWordWrap(True)

        self.export_button = QPushButton(i18n.tr('保存诊断包…'))
        self.export_button.setIcon(icons.icon("save", 16))
        self.reveal_button = QPushButton(i18n.tr('在{v0}中显示数据目录', v0=platform_ui.file_manager_name()))
        self.reveal_button.setIcon(icons.icon("folder", 16))
        self.copy_button = QPushButton(i18n.tr('复制到剪贴板'))
        self.copy_button.setIcon(icons.icon("copy", 16))
        self.close_button = QPushButton(i18n.tr('关闭'))
        for button in (self.export_button, self.reveal_button, self.copy_button):
            button.setIconSize(QSize(16, 16))
        self.export_button.clicked.connect(self._export)
        self.reveal_button.clicked.connect(self._reveal)
        self.copy_button.clicked.connect(self._copy)
        self.close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        for widget in (self.export_button, self.reveal_button,
                       self.copy_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(summary)
        layout.addWidget(style_module.SectionLabel(i18n.tr('完整报告（JSON）')))
        layout.addWidget(view, 1)
        layout.addWidget(self._status)
        layout.addLayout(buttons)

    def _copy(self) -> None:
        import json

        QGuiApplication.clipboard().setText(
            json.dumps(self.report, ensure_ascii=False, indent=2))

    def _reveal(self) -> None:
        adapter.reveal_in_finder(self.report.get("locations", {}).get(
            "data_dir", ""))

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        default = str(Path.home() / "Desktop" / "folith-diagnostics.json")
        if bootstrap.automation():
            # 脚本化运行不能弹原生保存面板（会一直等一个没人点的对话框）。
            # 写到一个确定的位置：日志目录（与诊断本身同源，不打扰用户桌面）。
            from folith.desktop import paths as desktop_paths

            target = str(desktop_paths.resolve_paths().log_dir
                         / "folith-diagnostics.json")
        else:
            target, _ = QFileDialog.getSaveFileName(
                self, "保存诊断包", default, "JSON (*.json)")
            if not target:
                return
        try:
            written = adapter.export_diagnostics(target)
        except Exception as exc:  # noqa: BLE001
            if bootstrap.automation():
                self._status.setText(i18n.tr('无法写出诊断包：{v0}', v0=exc))
            else:
                _set_status(self._status, i18n.tr('无法写出诊断包：{v0}', v0=exc), "danger")
            return
        if bootstrap.automation():
            self._status.setText(i18n.tr('诊断包已写入：{v0}', v0=written))
        else:
            _set_status(self._status, i18n.tr('诊断包已写入：{v0}', v0=written), "ok")


class SegmentSourceDialog(QDialog):
    """改正一段的原文（译文保持不变）。结构编辑可撤销，所以这里不再二次确认。"""

    def __init__(self, source: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('修改原文'))
        self.setMinimumWidth(560)
        hint = QLabel(i18n.tr('只改正原文（例如 OCR 识别错误）；当前译文保持不变，该段需要重新审校。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.edit = QPlainTextEdit(source)
        self.edit.setMinimumHeight(160)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(i18n.tr('保存原文'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.edit, 1)
        layout.addWidget(buttons)

    def source_text(self) -> str:
        return self.edit.toPlainText()


class SegmentSplitDialog(QDialog):
    """把一段拆成两个工作单元：两段原文必填，译文可以先留空。"""

    def __init__(self, source: str, target: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('拆分段落'))
        self.setMinimumWidth(720)
        hint = QLabel(i18n.tr('把当前段落拆成两个工作单元。两段原文都必须填写；译文可暂留空。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        source_a, source_b = adapter.split_parts(source)
        target_a, target_b = adapter.split_parts(target)
        self.source_a = QPlainTextEdit(source_a)
        self.source_b = QPlainTextEdit(source_b)
        self.target_a = QPlainTextEdit(target_a)
        self.target_b = QPlainTextEdit(target_b)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for column, (title_a, widget_a, title_b, widget_b) in enumerate((
                (i18n.tr('第一段原文'), self.source_a, i18n.tr('第二段原文'), self.source_b),
                (i18n.tr('第一段译文'), self.target_a, i18n.tr('第二段译文'), self.target_b))):
            grid.addWidget(style_module.field_label(title_a), column * 2, 0)
            grid.addWidget(style_module.field_label(title_b), column * 2, 1)
            grid.addWidget(widget_a, column * 2 + 1, 0)
            grid.addWidget(widget_b, column * 2 + 1, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(i18n.tr('拆分并保存'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(grid, 1)
        layout.addWidget(buttons)

    def parts(self) -> dict:
        return {"source_a": self.source_a.toPlainText(),
                "source_b": self.source_b.toPlainText(),
                "target_a": self.target_a.toPlainText(),
                "target_b": self.target_b.toPlainText()}


class WordCountView(QWidget):
    """字数统计的内容：按状态分项（以原文计）、译文总数、所选段落与计数规则。

    工作台底栏的字数浮窗与 :class:`WordCountDialog` 共用它。
    """

    def __init__(self, count, selected_rows: list, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from folith import word_count

        rows = [
            (i18n.tr('全部（不含已排除）'), count.total),
            (i18n.tr('已翻译'), count.translated),
            (i18n.tr('尚未翻译'), count.untranslated),
            (i18n.tr('已审校'), count.reviewed),
            (i18n.tr('人工修改'), count.human_edited),
            (i18n.tr('重复（原文与前文相同）'), count.repetitions),
            (i18n.tr('已排除'), count.excluded),
        ]
        self.table = QTableWidget(len(rows), 5, self)
        self.table.setHorizontalHeaderLabels(
            [i18n.tr('分类'), i18n.tr('段数'), i18n.tr('原文词数'), i18n.tr('原文字符'),
             i18n.tr('占比')])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):  # numbers take what they need; the label gets the rest
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for position, (label, bucket) in enumerate(rows):
            share = "" if bucket is count.excluded else f"{count.percent(bucket)}%"
            for column, value in enumerate((label, f"{bucket.segments:,}", f"{bucket.words:,}",
                                            f"{bucket.chars:,}", share)):
                item = QTableWidgetItem(value)
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(position, column, item)
        self.table.setMinimumHeight(min(320, 32 + 30 * len(rows)))
        target = QLabel(i18n.tr('译文：{v0} 词 / {v1} 字符', v0=f"{count.target_words:,}",
                                v1=f"{count.target_chars:,}"))
        rules = QLabel(i18n.tr('计数规则：中文、日文每个字算一个词；其他文字按空格分词。字符数不含空白。'))
        rules.setObjectName("hint")
        rules.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table)
        layout.addWidget(target)
        self.selection_label = None
        if len(selected_rows) > 1:
            data = word_count.selection_count(selected_rows)
            self.selection_label = QLabel(i18n.tr(
                '所选 {v0} 段：原文 {v1} 词 / {v2} 字符 → 译文 {v3} 词 / {v4} 字符',
                v0=data["segments"], v1=data["source_words"], v2=data["source_chars"],
                v3=data["target_words"], v4=data["target_chars"]))
            layout.addWidget(self.selection_label)
        layout.addWidget(rules)


class WordCountDialog(QDialog):
    """字数统计：整份文档按状态分项（以原文计），以及所选段落。"""

    def __init__(self, count, selected_rows: list, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('字数统计'))
        self.setMinimumWidth(520)
        self.view = WordCountView(count, selected_rows, self)
        self.table = self.view.table
        self.selection_label = self.view.selection_label
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(i18n.tr('关闭'))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class FindReplaceDialog(QDialog):
    """查找替换（只改译文）：先看命中了哪些段、改成什么样，再整批替换。

    替换走候选契约：每段先暂存候选，再由同一个采用事务写入。人工改写或人工确认过
    的段落默认跳过——批量操作不该覆盖已经完成的人工作业；要改就在段落里单独采用。
    """

    def __init__(self, state: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from . import adapter

        self._adapter = adapter
        self._state = state
        self.setWindowTitle(i18n.tr('查找替换译文'))
        self.resize(680, 480)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(i18n.tr('查找'))
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText(i18n.tr('替换为'))
        self.case_box = QCheckBox(i18n.tr('区分大小写'))
        self.protect_box = QCheckBox(i18n.tr('同时替换人工改写/人工确认过的段落（默认跳过）'))
        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        self.preview = QListWidget()
        for widget in (self.find_edit, self.replace_edit):
            widget.textChanged.connect(self._refresh)
        self.case_box.toggled.connect(self._refresh)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(i18n.tr('全部替换'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('关闭'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout()
        form.addRow(i18n.tr('查找'), self.find_edit)
        form.addRow(i18n.tr('替换为'), self.replace_edit)
        hint = QLabel(i18n.tr('只查找和替换译文。替换的段落记为人工修改，需要重新审校。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.case_box)
        layout.addWidget(self.protect_box)
        layout.addWidget(self.summary)
        layout.addWidget(self.preview, 1)
        layout.addWidget(buttons)
        self._refresh()

    def matches(self) -> list[dict]:
        return self._adapter.find_in_targets(
            self._state, self.find_edit.text(), self.replace_edit.text(),
            case_sensitive=self.case_box.isChecked())

    def _refresh(self, *_args) -> None:
        from folith import translation_candidates as _candidates

        matches = self.matches()
        pairs = self._state.get("pairs") or []
        self.preview.clear()
        protected = 0
        for item in matches[:200]:
            pair = pairs[item["index"]] if item["index"] < len(pairs) else {}
            locked = _candidates.locked_protection(pair)
            if locked:
                protected += 1
            marks = ("  · " + "、".join(_candidates.PROTECTION_LABELS.get(mark, mark)
                                       for mark in locked)) if locked else ""
            self.preview.addItem(QListWidgetItem(
                f"#{item['index'] + 1}{marks}  {item['before']}\n   → {item['after']}"))
        total = sum(item["count"] for item in matches)
        if matches:
            summary = i18n.tr('{v0} 段、{v1} 处命中', v0=len(matches), v1=total)
            if protected:
                summary += "  ·  " + i18n.tr('其中 {v0} 段是人工处理的译文，默认跳过',
                                             v0=protected)
        else:
            summary = i18n.tr('没有命中') if self.find_edit.text() else ""
        self.summary.setText(summary)
        self.ok_button.setEnabled(bool(matches))

    def values(self) -> dict:
        return {"query": self.find_edit.text(), "replacement": self.replace_edit.text(),
                "case_sensitive": self.case_box.isChecked(),
                "include_protected": self.protect_box.isChecked()}


class TermEditDialog(QDialog):
    """从工作区术语建议卡直接编辑一条术语（issue #124/#125）。

    只收集首选译名、禁用译名和状态；保存走既有的逐条修订通道（生成新术语
    版本、失效受影响段落）。勾选"预览受影响的译文"后，保存成功会打开逐段
    替换预览（:class:`TermReplaceDialog`）——术语修订和改译文是两个明确的
    操作，这里只是把两个入口接在一起。
    """

    def __init__(self, term: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .terms_panel import STATUS_LABELS, STATUS_ORDER

        self.setWindowTitle(i18n.tr('编辑术语'))
        self.setMinimumWidth(460)
        source = str(term.get("source") or "")
        source_label = QLabel(i18n.tr('原文术语：{v0}', v0=source or "—"))
        source_label.setObjectName("hint")
        self.preferred_edit = QLineEdit(str(term.get("preferred")
                                            or term.get("target") or ""))
        self.preferred_edit.setPlaceholderText(i18n.tr('首选译名'))
        self.forbidden_edit = QLineEdit("、".join(
            str(item) for item in term.get("forbidden") or []))
        self.forbidden_edit.setPlaceholderText(i18n.tr('多个用逗号或分号隔开'))
        self.status_box = QComboBox()
        for value in STATUS_ORDER:
            self.status_box.addItem(i18n.tr(STATUS_LABELS[value]), value)
        index = self.status_box.findData(str(term.get("status") or "provisional"))
        self.status_box.setCurrentIndex(max(index, 0))
        self.preview_box = QCheckBox(i18n.tr('保存后预览受影响的译文（逐段确认后再替换）'))
        self.preview_box.setChecked(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(i18n.tr('保存术语'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        hint = QLabel(i18n.tr(
            '保存会生成新的术语版本；之后的翻译会使用新译名。已有译文不会自动改动，'
            '可以在下一步逐段确认替换。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form = QFormLayout()
        form.addRow(i18n.tr('首选译名'), self.preferred_edit)
        form.addRow(i18n.tr('禁用译名'), self.forbidden_edit)
        form.addRow(i18n.tr('状态'), self.status_box)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(source_label)
        layout.addLayout(form)
        layout.addWidget(self.preview_box)
        layout.addWidget(buttons)
        self.preferred_edit.textChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_args) -> None:
        self.ok_button.setEnabled(bool(self.preferred_edit.text().strip()))

    def values(self) -> dict:
        return {"preferred": self.preferred_edit.text().strip(),
                "forbidden": [text.strip() for text in
                              self.forbidden_edit.text().replace("，", ",").replace("；", ",").replace("、", ",").split(",")
                              if text.strip()],
                "status": str(self.status_box.currentData() or "provisional"),
                "preview": self.preview_box.isChecked()}


class TermReplaceDialog(QDialog):
    """术语批量替换预览：逐段勾选要改的段（issue #124/#127）。

    只列原文里出现该术语的段落。可替换段给出 before/after 并默认勾选；
    人工改写/人工确认段只展示、绝不进批量；新旧译名都没有的段标记为
    "需人工确认"，同样不自动改。任何段都不会在未经预览的情况下被写入。
    """

    _MAX_ROWS = 500

    def __init__(self, preview: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from folith import translation_candidates as _candidates

        self._preview = preview
        self._rows = [row for row in preview.get("rows") or []
                      if not row.get("excluded")]
        self.setWindowTitle(i18n.tr('预览术语替换'))
        self.resize(680, 520)
        source = str(preview.get("source") or "")
        old_target = str(preview.get("old_target") or "")
        new_target = str(preview.get("new_target") or "")
        heading = QLabel(i18n.tr('{v0}：{v1} → {v2}', v0=source,
                                 v1=old_target or "（无首选译名）", v2=new_target))
        heading.setWordWrap(True)
        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        self.preview = QListWidget()
        self._selected: set[int] = set()
        shown = self._rows[:self._MAX_ROWS]
        hidden = len(self._rows) - len(shown)
        for row in shown:
            index = int(row.get("segment_index"))
            self._selected.add(index)
            variants = "、".join(str(item) for item in row.get("variants") or [])
            marks = i18n.tr('替换「{v0}」', v0=variants) if variants else ""
            item = QListWidgetItem(
                f"#{index + 1}{marks}  {row.get('before')}\n   → {row.get('after')}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.preview.addItem(item)
        for entry in preview.get("protected") or []:
            index = int(entry.get("segment_index"))
            labels = "、".join(
                _candidates.PROTECTION_LABELS.get(mark, mark)
                for mark in entry.get("protection") or [])
            label = i18n.tr('人工处理的译文，不会替换')
            item = QListWidgetItem(
                f"#{index + 1}  {label}" + (f"（{labels}）" if labels else ""))
            item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsUserCheckable
                                           | Qt.ItemFlag.ItemIsEnabled))
            self.preview.addItem(item)
        for entry in preview.get("ambiguous") or []:
            index = int(entry.get("segment_index"))
            label = i18n.tr('需人工确认（译文里没有新旧译名）：{v0}',
                            v0=entry.get("before"))
            item = QListWidgetItem(f"#{index + 1}  {label}")
            item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsUserCheckable
                                           | Qt.ItemFlag.ItemIsEnabled))
            self.preview.addItem(item)
        self.preview.itemChanged.connect(self._sync)
        select_row = QHBoxLayout()
        self.all_button = QPushButton(i18n.tr('全选'))
        self.none_button = QPushButton(i18n.tr('全不选'))
        select_row.addWidget(self.all_button)
        select_row.addWidget(self.none_button)
        select_row.addStretch(1)
        self.all_button.clicked.connect(lambda: self._set_all(True))
        self.none_button.clicked.connect(lambda: self._set_all(False))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('关闭'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        hint = QLabel(i18n.tr('替换的段落记为人工修改，需要重新审校；人工改写或人工确认过的段落不会被替换。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.summary)
        layout.addWidget(hint)
        layout.addWidget(self.preview, 1)
        layout.addLayout(select_row)
        layout.addWidget(buttons)
        self.ok_button.clicked.connect(lambda: self.accept())
        buttons.accepted.connect(self.accept)
        self._sync()

    def _set_all(self, checked: bool) -> None:
        for row in self.preview.findItems("*", Qt.MatchFlag.MatchWildcard):
            if row.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                row.setCheckState(Qt.CheckState.Checked if checked
                                  else Qt.CheckState.Unchecked)

    def _sync(self, *_args) -> None:
        for row in self.preview.findItems("*", Qt.MatchFlag.MatchWildcard):
            index = row.data(Qt.ItemDataRole.UserRole)
            if isinstance(index, int):
                if row.checkState() == Qt.CheckState.Checked:
                    self._selected.add(index)
                else:
                    self._selected.discard(index)
        counts = self._preview
        replaceable = len(self._rows)
        summary = i18n.tr(
            '原文 {v0} 段命中 · 可替换 {v1} 段 · 已是新译名 {v2} 段 · 需人工确认 {v3} 段 · 人工保护 {v4} 段 · 已选中 {v5} 段',
            v0=int(counts.get("source_occurrences") or 0), v1=replaceable,
            v2=len(counts.get("already_correct") or []),
            v3=len(counts.get("ambiguous") or []),
            v4=len(counts.get("protected") or []),
            v5=len(self._selected))
        hidden = len(self._rows) - min(len(self._rows), self._MAX_ROWS)
        if hidden:
            summary += "  ·  " + i18n.tr(
                '受影响段落较多，仅显示前 {v0} 段；未显示的段未选中，不会被替换。',
                v0=self._MAX_ROWS)
        self.summary.setText(summary)
        self.ok_button.setText(i18n.tr('替换选中的 {v0} 段', v0=len(self._selected)))
        self.ok_button.setEnabled(bool(self._selected))

    def values(self) -> dict:
        row_indexes = [int(row.get("segment_index")) for row in self._rows]
        selected = [index for index in row_indexes if index in self._selected]
        return {"selected": selected,
                "exclusions": [index for index in row_indexes
                               if index not in self._selected]}


class DocumentProfileDialog(QDialog):
    """修改文档画像：领域、细分领域、文本类型、读者、语域、风格约束。

    画像决定模型"把这份文档当成什么来译"。AI 判断错了（比如把小说当成学术
    文章），在这里改正；开始翻译前改，翻译就直接用它。
    """

    def __init__(self, profile: dict, *, started: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from . import adapter

        self.setWindowTitle(i18n.tr('文档画像'))
        self.setMinimumWidth(480)
        hint = QLabel(i18n.tr('翻译完成后修改画像，审校需要重新进行。') if started
                      else i18n.tr('开始翻译前保存，翻译会直接使用这里的画像。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form = QFormLayout()
        self.fields: dict[str, QWidget] = {}
        for key, label in adapter.PROFILE_FIELDS:
            if key == "style_constraints":
                widget = QPlainTextEdit(str(profile.get(key) or ""))
                widget.setMinimumHeight(70)
            else:
                widget = QLineEdit(str(profile.get(key) or ""))
            self.fields[key] = widget
            form.addRow(i18n.tr(label), widget)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(i18n.tr('保存画像'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {key: (widget.toPlainText() if isinstance(widget, QPlainTextEdit)
                      else widget.text()).strip()
                for key, widget in self.fields.items()}


class RiskAcceptanceDialog(QDialog):
    """接受剩余风险后交付：写下处理说明，并明确勾选确认。

    继续交付不会删除这些问题：核心把它们记为"已接受风险"的人工决定，处理说明
    写进交付快照的批准记录（与 Streamlit 交付页同一契约）。
    """

    def __init__(self, blocking_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('接受风险并交付'))
        self.setMinimumWidth(480)
        intro = QLabel(i18n.tr(
            '当前仍有 {v0} 个必须处理的问题。继续交付不会删除这些问题；系统会保存本次风险接受记录，'
            '并写进交付版本。', v0=blocking_count))
        intro.setWordWrap(True)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText(i18n.tr('说明为什么在这些问题仍然存在时决定继续交付…'))
        self.note_edit.setMinimumHeight(90)
        self.confirm_box = QCheckBox(i18n.tr('我确认理解这些问题仍然存在，并决定继续交付'))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(i18n.tr('确认风险并创建交付版本'))
        style_module.set_button(self.ok_button, "danger")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.note_edit.textChanged.connect(self._sync)
        self.confirm_box.toggled.connect(self._sync)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(QLabel(i18n.tr('处理说明')))
        layout.addWidget(self.note_edit)
        layout.addWidget(self.confirm_box)
        layout.addWidget(buttons)
        self._sync()

    def _sync(self) -> None:
        self.ok_button.setEnabled(self.confirm_box.isChecked()
                                  and bool(self.note_edit.toPlainText().strip()))

    def note(self) -> str:
        return self.note_edit.toPlainText().strip()




class TerminologyReplacementPreview(QDialog):
    """Display every proposed segment before the operator authorizes a batch."""

    def __init__(self, preview: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('确认术语替换'))
        self.resize(850, 600)
        layout = QVBoxLayout(self)
        changes = preview.get('changes') or []
        label = QLabel(i18n.tr('将修改 {v0} 段。请逐项检查；应用后这些段需要重新审校。', v0=len(changes)))
        label.setWordWrap(True)
        layout.addWidget(label)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setPlainText('\n\n'.join(
            i18n.tr('第 {v0} 段', v0=change['index'] + 1) + '\n'
            + i18n.tr('原文') + ': ' + change['source'] + '\n'
            + i18n.tr('修改前') + ': ' + change['before'] + '\n'
            + i18n.tr('修改后') + ': ' + change['after']
            for change in changes))
        layout.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(i18n.tr('确认应用'))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(changes))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
