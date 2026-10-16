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

from . import i18n
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
             ("vision", "image", "视觉与识别"), ("defaults", "package", "任务默认值"),
             ("storage", "folder", "存储"), ("about", "info", "关于"))

    def __init__(self, config: adapter.EngineConfig, runner: TaskRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.Window, True)
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
        self.about_page = AboutPage(runner)
        self.about_page.diagnosticsRequested.connect(self.diagnosticsRequested.emit)
        builders = {"general": self._build_appearance_tab,
                    "models": self._build_connection_tab,
                    "vision": self._build_vision_tab,
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
    def show_page(self, key: str) -> None:
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
                    self.deliver_original_pdf_box, self.deliver_epub_box):
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
    def _apply_row(self, page: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, theme_module.SPACE["xs"], 0, 0)
        row.setSpacing(theme_module.SPACE["sm"])
        hint = QLabel("")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        apply_button = QPushButton(i18n.tr('保存用途'))
        style_module.mark_primary(apply_button)
        apply_button.clicked.connect(self.apply_connection)
        force = QPushButton(i18n.tr('仍然应用'))
        force.setToolTip(i18n.tr('不等测试通过就保存这份连接（未配置好之前无法启动翻译）。'))
        force.setVisible(False)
        force.clicked.connect(lambda: self._commit_connection(self._collect(), tested=False))
        row.addWidget(hint, 1)
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
        for button in (self.models_apply_button, self.vision_apply_button):
            button.setEnabled(False)
        _set_status(self._status, i18n.tr('正在测试连接…'))

        def done(result) -> None:
            ok, message = result
            for button in (self.models_apply_button, self.vision_apply_button):
                button.setEnabled(True)
            from . import connection_status

            connection_status.record("translation", config, ok, message)
            if ok:
                self._commit_connection(config, tested=True, message=message)
            else:
                _set_status(self._status, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
                self._show_force(True)

        def failed(message: str, _detail: str) -> None:
            for button in (self.models_apply_button, self.vision_apply_button):
                button.setEnabled(True)
            _set_status(self._status, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
            self._show_force(True)

        self.runner.submit(adapter.test_connection, config, on_done=done, on_error=failed,
                           label="apply-connection")

    def _show_force(self, visible: bool) -> None:
        for button in (self.models_force_button, self.vision_force_button):
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
        self._show_force(False)
        _set_status(self._status, i18n.tr('连接正常，已应用：{v0}', v0=message) if tested
                    else i18n.tr('已应用（未通过测试）。'), "ok" if tested else "muted")
        self.connectionApplied.emit()
        return True

    # -- connection --------------------------------------------------------
    def _build_connection_tab(self) -> QWidget:
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
        self.fetch_button = QPushButton(i18n.tr('获取可用模型'))
        self.fetch_button.clicked.connect(self._fetch_models)
        self.test_button = QPushButton(i18n.tr('测试连接'))
        self.test_button.clicked.connect(self._test_connection)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        self.provider_box.currentTextChanged.connect(self._provider_changed)
        self.model_box.currentTextChanged.connect(self._sync_reasoning)
        self._provider_changed(self.provider_box.currentText())
        self._sync_reasoning()
        self.connection_library = ConnectionLibrary(self)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.connection_library)
        return page

    def _build_vision_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])
        vision_field = self._build_vision_selector()
        layout.addWidget(self._build_scan_group())
        self._scan_body.insertWidget(2, vision_field)
        layout.addWidget(self._apply_row("vision"))
        layout.addStretch(1)

        def update_fields():
            online = self.ocr_backend_box.currentData() == "online"
            vision_field.setVisible(online)
            self.ocr_language_row.setVisible(not online)
            if hasattr(self, "ocr_local_info"):
                self.ocr_local_info.setVisible(not online)
        self.ocr_backend_box.currentIndexChanged.connect(update_fields)
        update_fields()
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

    def _build_task_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        outer.setSpacing(theme_module.SPACE["sm"])

        # 文案与「译前准备 · 翻译设置」逐字一致（同一个开关在两个面上必须同名）：
        # 这里曾经用「自动抽取并注入术语 / 完成后自动审校 / 复用翻译记忆」这套旧词，
        # 而准备面板用「自动识别术语 / 译后自动审校 / 使用翻译记忆」——同一个开关两个
        # 名字，用户会以为是两件事。准备面板那份措辞更短、更适合窄控件，以它为准。
        self.auto_term_box = style_module.CheckBox(i18n.tr('自动识别术语'))
        self.auto_term_box.setChecked(self.config.auto_term)
        self.review_box = style_module.CheckBox(i18n.tr('译后自动审校'))
        self.review_box.setChecked(self.config.enable_review)
        self.tm_box = style_module.CheckBox(i18n.tr('使用翻译记忆'))
        self.tm_box.setChecked(self.config.use_tm)
        self.understanding_box = style_module.CheckBox(i18n.tr('建立文档画像'))
        self.understanding_box.setChecked(self.config.enable_understanding)
        self.cleanup_box = style_module.CheckBox(i18n.tr('对 PDF 原文做模型纠错（更慢）'))
        self.cleanup_box.setChecked(self.config.enable_source_cleanup)
        self.report_box = style_module.CheckBox(i18n.tr('生成实践报告（需要更长的运行时间）'))
        self.report_box.setChecked(self.config.enable_report)
        self.academic_box.toggled.connect(self._academic_toggled)
        self.report_box.setVisible(self.academic_box.isChecked())
        # 工作模式决定工作台出现哪些页，属于工作流（8.3-4）：放在这一页最上面。
        mode_card, mode_body = _panel(
            i18n.tr('工作模式'),
            i18n.tr('默认关闭。打开后，工作台会多出实践报告、案例与合规 QA 三页，'
                    '下面也可以勾选「生成实践报告」。'))
        self.default_mode_box = style_module.ComboBox()
        for label, key in (("快速", "快速"), ("标准", "标准"), ("精细", "学术增强")):
            self.default_mode_box.addItem(i18n.tr(label), key)
        self.default_mode_box.setCurrentIndex(max(0, self.default_mode_box.findData(
            self.config.default_translation_mode)))
        mode_body.addWidget(_field(i18n.tr('默认翻译方式'), self.default_mode_box))
        workflow_body = mode_body
        outer.addWidget(mode_card)
        # 两列网格：六项都是单行短句，一列排下来会白占半屏。
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for position, box in enumerate((self.auto_term_box, self.review_box,
                                        self.tm_box, self.understanding_box,
                                        self.cleanup_box, self.academic_box, self.report_box)):
            grid.addWidget(box, position // 2, position % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.target_box = style_module.ComboBox()
        self.target_box.setEditable(True)
        self.target_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target_box.addItems(list(adapter.TARGET_LANGUAGES))
        self.target_box.setCurrentText(self.config.target_lang)
        workflow_body.addWidget(_field(i18n.tr('目标语言'), self.target_box))
        workflow_body.addLayout(grid)

        delivery, delivery_body = _panel(
            i18n.tr('交付文件'), i18n.tr('只生成勾选的格式；冻结交付后快照不可修改。'))

        def fmt(attr: str, label: str, filename: str, default: bool) -> tuple:
            """格式名是复选框，文件名是后面一行灰色小字（8.3-6）。"""
            box = style_module.CheckBox(i18n.tr(label))
            box.setChecked(bool(getattr(self.config, attr, default)))
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme_module.SPACE["sm"])
            row.addWidget(box)
            row.addStretch(1)
            setattr(self, f"_{attr}_holder", holder)
            return box, holder

        self.deliver_plain_box, plain = fmt("deliver_plain_docx", '纯译文 DOCX',
                                            "translation.docx", True)
        self.deliver_bilingual_box, bilingual = fmt("deliver_bilingual_docx", '双语对照 DOCX',
                                                    "bilingual.docx", True)
        self.deliver_pdf_box, pdf = fmt("deliver_pdf", '译文 PDF', "translation.pdf", False)
        self.deliver_epub_box, epub = fmt("deliver_epub", 'EPUB 电子书',
                                          "translation.epub", False)
        self.deliver_terms_box, terms = fmt("deliver_terms_xlsx", '术语表 XLSX',
                                            "terms.xlsx", True)
        self.deliver_evidence_box, evidence = fmt("deliver_evidence", '段落证据 JSONL',
                                                  "segment_evidence.jsonl", True)
        self.deliver_original_pdf_box, original = fmt(
            "deliver_original_pdf", '原排版译文 PDF', "original_layout.pdf", False)
        # This dialog edits client defaults before a file is chosen, so it
        # cannot know the source type; the hint states the constraint and the
        # import mapping drops the option for a non-PDF source.
        self.deliver_original_pdf_box.setToolTip(
            i18n.tr('仅 PDF 任务可用：按原文件的版式重建译文页面。'))
        beta = QLabel("Beta")
        beta.setObjectName("betaBadge")
        original.layout().insertWidget(1, beta)
        groups = QGridLayout()
        groups.setContentsMargins(0, 0, 0, 0)
        groups.setHorizontalSpacing(theme_module.SPACE["lg"])
        groups.setVerticalSpacing(theme_module.SPACE["xs"])
        for column, (caption, rows) in enumerate(((i18n.tr('文档'), (plain, bilingual, pdf)),
                                                  (i18n.tr('电子书与数据'),
                                                   (epub, terms, evidence)))):
            groups.addWidget(style_module.field_label(caption), 0, column)
            for index, widget in enumerate(rows, start=1):
                groups.addWidget(widget, index, column)
        groups.setColumnStretch(0, 1)
        groups.setColumnStretch(1, 1)
        delivery_body.addLayout(groups)
        delivery_body.addSpacing(theme_module.SPACE["xs"])
        delivery_body.addWidget(original)
        outer.addWidget(delivery)
        outer.addStretch(1)
        return page

    def _make_academic_box(self) -> None:
        # 实践报告 / 案例 / 终稿 QA 是专用能力：默认不出现，用户主动打开"学术专业模式"才显示。
        self.academic_box = style_module.CheckBox(
            i18n.tr('MTI：实践报告、案例与合规、终稿 QA'))
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
    def _build_vision_selector(self) -> QWidget:
        self._vision_view = adapter.vision_connection_view()
        self.vision_connection_box = style_module.ComboBox()
        self.vision_connection_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.vision_connection_box.setMinimumContentsLength(12)
        model_box = self.connection_library.make_model_selector("vision")
        self.connection_library.fill_selector(self.vision_connection_box, "vision")
        self.vision_connection_box.currentIndexChanged.connect(
            lambda _i: self.connection_library.select("vision"))
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.vision_connection_box, 1)
        row.addWidget(model_box, 1)
        return _field(i18n.tr('视觉模型'), holder)

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

    def _build_scan_group(self) -> QWidget:
        """扫描件的提取默认值（来源 / 时机 / 语言）：新任务的默认值，和翻译流程同属一页。"""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(theme_module.SPACE["sm"])
        ocr_card, body = _panel(
            i18n.tr('扫描件识别'),
            i18n.tr('只影响没有可用文本层的页面（扫描件）；文本层 PDF 与 DOCX 不走这一步。'))
        self.ocr_language_box = style_module.ComboBox()
        # 可编辑：既能从核心注册表里选（30 项，带中文标签），也能直接填语言代码。
        self.ocr_language_box.setEditable(True)
        self.ocr_language_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for label, value in adapter.ocr_language_choices():
            self.ocr_language_box.addItem(label, value)
        self._select_ocr_language(self.config.ocr_language)
        self.ocr_mode_box = style_module.ComboBox()
        # 标签给用户看，data 给核心用：两者必须对齐，否则核心会静默回退成 auto
        # （历史缺陷：界面写 always/never，核心只认 force/off）。
        for label, mode in adapter.OCR_MODE_CHOICES:
            self.ocr_mode_box.addItem(label, mode)
        self.ocr_mode_box.setCurrentText(
            adapter.ocr_mode_label(self.config.ocr_mode))
        self.ocr_backend_box = style_module.ComboBox()
        # 取值仍然是核心认识的那三个，但显示的是人话（此前直接显示 online / tesseract）。
        # 标签取自 adapter 的**同一份**真源：这里曾手抄第二条副本，改一处漏一处。
        for label, value in adapter.OCR_BACKEND_CHOICES:
            self.ocr_backend_box.addItem(label, value)
        index = self.ocr_backend_box.findData(self.config.ocr_backend or "online")
        self.ocr_backend_box.setCurrentIndex(index if index >= 0 else 0)
        # 顺序与译前面板一致：来源是主决定，语言/mode 是它的参数。
        body.addWidget(_field(i18n.tr('提取来源'), self.ocr_backend_box))
        body.addWidget(_field(i18n.tr('何时识别'), self.ocr_mode_box))
        self.ocr_language_row = _field(i18n.tr('识别语言'), self.ocr_language_box)
        body.addWidget(self.ocr_language_row)
        self._scan_body = body

        command = adapter.core._find_tesseract()
        local_index = self.ocr_backend_box.findData("tesseract")
        if local_index >= 0:
            self.ocr_backend_box.model().item(local_index).setEnabled(bool(command))
        if command:
            info = QLabel("Tesseract " + adapter.core._ocr_backend_version("tesseract", command)
                          + " · " + ", ".join(adapter.core.installed_ocr_languages()))
            info.setWordWrap(True)
            self.ocr_local_info = info
            body.addWidget(info)
        else:
            install = QPushButton(i18n.tr('如何安装…'))
            from .settings_pages import _open_url
            install.clicked.connect(lambda: _open_url(
                "https://tesseract-ocr.github.io/tessdoc/Installation.html"))
            body.addWidget(install)
            if self.ocr_backend_box.currentData() == "tesseract":
                self.ocr_backend_box.setCurrentIndex(self.ocr_backend_box.findData("online"))
        page_layout.addWidget(ocr_card)
        return page

    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        page_layout.setSpacing(theme_module.SPACE["sm"])
        card, body = _panel(
            i18n.tr('界面主题'),
            i18n.tr('「跟随系统」随 macOS 浅色/深色外观自动切换。所有主题共用同一份品牌配色，切换不会影响已导入的任务与译文。'))
        self.theme_box = style_module.ComboBox()
        for name in (theme_module.FOLLOW_SYSTEM, "light", "dark"):
            self.theme_box.addItem(i18n.tr(theme_module.label(name)), name)
        index = self.theme_box.findData(self.theme_preference)
        self.theme_box.setCurrentIndex(max(0, index))
        body.addWidget(_field(i18n.tr('外观'), self.theme_box))
        self.accent_box = style_module.ComboBox()
        for name, label in (("blue", "蓝色"), ("green", "翠绿"), ("slate", "石板色")):
            self.accent_box.addItem(i18n.tr(label), name)
        body.addWidget(_field(i18n.tr('主题（高光色）'), self.accent_box))

        self.language_box = style_module.ComboBox()
        for code, label in i18n.available_languages():
            self.language_box.addItem(label, code)
        self._select_language(self.language_preference)
        body.addWidget(_field(
            i18n.tr('界面语言（重启后生效）'), self.language_box,
            i18n.tr('非中文界面为实验功能（尚未完成独立的语言审阅）。切换后重启应用，整个窗口才会换成新语言；阿拉伯语会整体从右到左排版。')))
        page_layout.addWidget(card)

        keys, keys_body = _panel(i18n.tr('键盘'),
                                 i18n.tr('常用操作都有快捷键；这里列出当前版本的全部快捷键。'))
        self.shortcuts_button = QPushButton(i18n.tr('键盘快捷键…'))
        self.shortcuts_button.clicked.connect(self.shortcutsRequested.emit)
        row = QHBoxLayout()
        row.addWidget(self.shortcuts_button)
        row.addStretch(1)
        keys_body.addLayout(row)
        page_layout.addWidget(keys)
        page_layout.addStretch(1)
        return page

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
        "deliver_evidence", "deliver_pdf", "deliver_original_pdf",
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
        config.deliver_epub = self.deliver_epub_box.isChecked()
        config.default_translation_mode = str(self.default_mode_box.currentData() or "标准")
        from folith.model_connections import effective_config
        config.connection_models = dict(self.connection_library.role_models)
        return effective_config(config, self.connection_library.profiles,
                                self.connection_library.roles)

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

    def _fetch_models(self) -> None:
        provider = self.provider_box.currentText().strip()
        api_key = self._editor_config().api_key
        base_url = self.base_edit.text().strip()
        self.fetch_button.setEnabled(False)
        _set_status(self.test_label, "正在获取模型列表…")

        def done(result) -> None:
            ok, models, message = result
            self.fetch_button.setEnabled(True)
            _set_status(self.test_label, str(message), "ok" if ok else "danger")
            if ok and models:
                current = self.model_box.currentText()
                self.model_box.clear()
                self.model_box.addItems([str(item) for item in models])
                self.model_box.setCurrentText(current or str(models[0]))
                known = [m.strip() for m in self.connection_library.models.text().split(",") if m.strip()]
                self.connection_library.models.setText(", ".join(dict.fromkeys(known + [str(m) for m in models])))

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
        self.reveal_button = QPushButton(i18n.tr('在 Finder 中显示数据目录'))
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


class ProjectPickerDialog(QDialog):
    """把选中的任务移动到某个项目（运行中的任务由核心拒绝并给出原因）。"""

    def __init__(self, projects: list[dict], *, job_count: int,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('移动到项目'))
        self.setMinimumWidth(460)
        self.projects = projects

        title = QLabel(i18n.tr('移动到项目'))
        title.setObjectName("pageTitle")
        notice = style_module.NoticeStrip(
            i18n.tr('已选择 {v0} 个任务。运行中的任务无法改归属——它已经读过某个项目的记忆，中途切换会让同一个任务在两套记忆之间漂移。', v0=job_count), tone="info")

        self.list = QListWidget()
        self.list.setMinimumHeight(220)
        self.list.setIconSize(QSize(16, 16))
        self.list.setSpacing(2)
        for project in projects:
            label = f"{project['name']}（{project['job_count']} 个任务）"
            if project.get("is_system"):
                label = f"{project['name']}（未分类）"
            item = QListWidgetItem(
                icons.icon("folder", 16,
                           "text-secondary"), label)
            item.setData(Qt.ItemDataRole.UserRole, project.get("project_id"))
            item.setSizeHint(QSize(0, theme_module.CONTROL["md"] + 6))
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        self.empty_state = style_module.EmptyState(
            icon="folder", title=i18n.tr('还没有可用项目'),
            hint=i18n.tr('先在「项目…」里新建一个项目，再回来移动任务。'))
        self.empty_state.setVisible(not projects)

        # 在这里就能新建项目并直接选中（不必先去「项目」页建好再回来）。
        self.new_project_button = QPushButton(i18n.tr('新建项目…'))
        self.new_project_button.clicked.connect(self._new_project)
        self.new_project_note = QLabel("")
        self.new_project_note.setObjectName("hint")

        self.move_tm_box = style_module.CheckBox(i18n.tr('同时把已审校译文并入目标项目的翻译记忆'))
        self.move_tm_box.setToolTip(i18n.tr('只增不改：目标项目已有的译法不会被覆盖'))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText(i18n.tr('移动'))
        ok_button.setIcon(icons.icon(
            "arrow-right", 16, "text-on-accent"))
        ok_button.setIconSize(QSize(16, 16))
        style_module.mark_primary(ok_button)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(notice)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.empty_state)
        new_row = QHBoxLayout()
        new_row.addWidget(self.new_project_button)
        new_row.addWidget(self.new_project_note, 1)
        layout.addLayout(new_row)
        layout.addWidget(self.move_tm_box)
        layout.addWidget(buttons)

    def _new_project(self, name: str = "") -> None:
        from . import adapter

        if not name:
            if bootstrap.automation():
                return
            name, ok = prompts.TextInputDialog.get_text(
                self, i18n.tr('新建项目'), i18n.tr('项目名称：'))
            if not ok:
                return
        try:
            project = adapter.create_project(name)
        except adapter.AdapterError as exc:
            self.new_project_note.setText(str(exc))
            return
        item = QListWidgetItem(icons.icon("folder", 16, "text-secondary"),
                               f"{project['name']}（0 个任务）")
        item.setData(Qt.ItemDataRole.UserRole, project.get("project_id"))
        item.setSizeHint(QSize(0, theme_module.CONTROL["md"] + 6))
        self.list.addItem(item)
        self.list.setCurrentItem(item)
        self.empty_state.setVisible(False)
        self.new_project_note.setText(i18n.tr('已新建「{v0}」并选中。', v0=project["name"]))

    def selected_project(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def move_translations(self) -> bool:
        return self.move_tm_box.isChecked()


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
    """查找替换（只改译文）：先看命中了哪些段、改成什么样，再全部替换。

    每段替换都走人工提交：记为人工修改，需要重新审校；原文不动。
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
        self.summary = QLabel("")
        self.summary.setObjectName("hint")
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
        layout.addWidget(self.summary)
        layout.addWidget(self.preview, 1)
        layout.addWidget(buttons)
        self._refresh()

    def matches(self) -> list[dict]:
        return self._adapter.find_in_targets(
            self._state, self.find_edit.text(), self.replace_edit.text(),
            case_sensitive=self.case_box.isChecked())

    def _refresh(self, *_args) -> None:
        matches = self.matches()
        self.preview.clear()
        for item in matches[:200]:
            self.preview.addItem(QListWidgetItem(
                f"#{item['index'] + 1}  {item['before']}\n   → {item['after']}"))
        total = sum(item["count"] for item in matches)
        self.summary.setText(
            i18n.tr('{v0} 段、{v1} 处命中', v0=len(matches), v1=total) if matches
            else (i18n.tr('没有命中') if self.find_edit.text() else ""))
        self.ok_button.setEnabled(bool(matches))

    def values(self) -> dict:
        return {"query": self.find_edit.text(), "replacement": self.replace_edit.text(),
                "case_sensitive": self.case_box.isChecked()}


class LibraryTermDialog(QDialog):
    """在语言资产库里修改一条术语：作用在它所在的某个任务的术语表上（生成新版本）。"""

    STATUSES = (("locked", "已锁定"), ("provisional", "暂定"), ("candidate", "候选"),
                ("rejected", "已拒绝"))

    def __init__(self, row: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('编辑术语'))
        self.setMinimumWidth(460)
        hint = QLabel(i18n.tr('修改会生成新的术语版本，受影响段落的审校需要重新确认。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.task_box = style_module.ComboBox()
        for task in row.get("tasks") or []:
            if task.get("job_id"):
                self.task_box.addItem(str(task.get("document") or task.get("job_id")), dict(task))
        self.preferred_edit = QLineEdit(str(row.get("preferred") or ""))
        self.status_box = style_module.ComboBox()
        for value, label in self.STATUSES:
            self.status_box.addItem(i18n.tr(label), value)
        index = self.status_box.findData(str(row.get("status") or "locked"))
        self.status_box.setCurrentIndex(max(index, 0))
        self.domain_edit = QLineEdit(str(row.get("domain") or ""))
        self.forbidden_edit = QLineEdit("、".join(row.get("forbidden") or []))
        self.forbidden_edit.setPlaceholderText(i18n.tr('多个用逗号或分号隔开'))
        form = QFormLayout()
        form.addRow(i18n.tr('原术语'), QLabel(str(row.get("source") or "")))
        form.addRow(i18n.tr('所在任务'), self.task_box)
        form.addRow(i18n.tr('首选译名'), self.preferred_edit)
        form.addRow(i18n.tr('状态'), self.status_box)
        form.addRow(i18n.tr('领域'), self.domain_edit)
        form.addRow(i18n.tr('禁用译名'), self.forbidden_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(i18n.tr('保存术语'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def task(self) -> dict:
        return dict(self.task_box.currentData() or {})

    def values(self) -> dict:
        import re

        return {"preferred": self.preferred_edit.text().strip(),
                "status": str(self.status_box.currentData() or "locked"),
                "domain": self.domain_edit.text().strip(),
                "forbidden": [text.strip() for text in
                              re.split(r"[,，;；、\n]", self.forbidden_edit.text())
                              if text.strip()]}


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


class NewTermDialog(QDialog):
    """新建术语：术语保存在所选任务的术语表里，并生成新的术语版本。"""

    def __init__(self, jobs: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('添加术语'))
        self.setMinimumWidth(460)
        hint = QLabel(i18n.tr('术语保存在所选任务的术语表里，并生成新的术语版本。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.job_box = style_module.ComboBox()
        for job_id, name in jobs:
            self.job_box.addItem(name, job_id)
        self.source_edit = QLineEdit()
        self.target_edit = QLineEdit()
        self.domain_edit = QLineEdit()
        self.error = QLabel("")
        self.error.setObjectName("statusError")
        self.error.setVisible(False)
        form = QFormLayout()
        form.addRow(i18n.tr('保存到任务'), self.job_box)
        form.addRow(i18n.tr('原术语'), self.source_edit)
        form.addRow(i18n.tr('推荐译法'), self.target_edit)
        form.addRow(i18n.tr('分类 / 领域（可选）'), self.domain_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(i18n.tr('创建术语'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.job_box.currentData():
            self._fail(i18n.tr('还没有可保存的任务。'))
        elif not self.source_edit.text().strip() or not self.target_edit.text().strip():
            self._fail(i18n.tr('请同时填写「原术语」和「推荐译法」。'))
        else:
            self.accept()

    def _fail(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)

    def values(self) -> dict:
        return {"job_id": str(self.job_box.currentData() or ""),
                "source": self.source_edit.text().strip(),
                "target": self.target_edit.text().strip(),
                "domain": self.domain_edit.text().strip()}


class ProjectKnowledgeDialog(QDialog):
    """项目知识：记忆摘要，以及导入时发现、等待人工决定的冲突。"""

    def __init__(self, project: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = project
        self.setWindowTitle(i18n.tr('项目知识 · {v0}', v0=project.get("name") or ""))
        self.setMinimumSize(560, 420)
        self.summary = QTextBrowser()
        self.summary.setMaximumHeight(150)
        self.conflicts = QListWidget()
        self.conflicts.currentRowChanged.connect(self._sync)
        self.adopt_button = QPushButton(i18n.tr('采纳导入版本'))
        self.keep_button = QPushButton(i18n.tr('保留本地版本'))
        self.keep_all_button = QPushButton(i18n.tr('全部保留本地'))
        self.adopt_button.clicked.connect(lambda: self._resolve(True))
        self.keep_button.clicked.connect(lambda: self._resolve(False))
        self.keep_all_button.clicked.connect(self._keep_all)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        buttons = QHBoxLayout()
        for widget in (self.adopt_button, self.keep_button, self.keep_all_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        close = QPushButton(i18n.tr('关闭'))
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(style_module.field_label(i18n.tr('待决冲突')))
        layout.addWidget(self.conflicts, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.status)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._conflicts: list[dict] = []
        self.reload()

    def reload(self) -> None:
        info = adapter.project_knowledge(self.project["project_id"])
        memory = info["memory"]
        self._conflicts = info["conflicts"]
        rows = ((i18n.tr('锁定术语'), memory.get("glossary_count")),
                (i18n.tr('风格规则'), memory.get("style_rule_count")),
                (i18n.tr('翻译记忆'), memory.get("translation_memory_count")),
                (i18n.tr('人工决定'), memory.get("human_decision_count")),
                (i18n.tr('提升记录'), memory.get("promotion_count")),
                (i18n.tr('待决冲突'), memory.get("pending_conflict_count")))
        self.summary.setHtml("<table>" + "".join(
            f"<tr><td>{name}&nbsp;&nbsp;</td><td><b>{value or 0}</b></td></tr>" for name, value in rows)
            + "</table>")
        self.conflicts.clear()
        for conflict in self._conflicts:
            kind = i18n.tr_dynamic(adapter.CONFLICT_KIND_LABELS.get(conflict["kind"], conflict["kind"]))
            self.conflicts.addItem(
                f"{kind} · {conflict['source']}：{i18n.tr('本地')}「{conflict['local']}」"
                f" ↔ {i18n.tr('导入')}「{conflict['incoming']}」")
        if self._conflicts:
            self.conflicts.setCurrentRow(0)
        self._sync()

    def _sync(self, *_args) -> None:
        has = 0 <= self.conflicts.currentRow() < len(self._conflicts)
        self.adopt_button.setEnabled(has)
        self.keep_button.setEnabled(has)
        self.keep_all_button.setEnabled(bool(self._conflicts))

    def _resolve(self, adopt: bool) -> None:
        row = self.conflicts.currentRow()
        if not 0 <= row < len(self._conflicts):
            return
        try:
            adapter.resolve_project_conflict(
                self.project["project_id"], self._conflicts[row]["conflict_id"],
                adopt_incoming=adopt)
        except adapter.AdapterError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText(i18n.tr('已采纳导入版本。') if adopt else i18n.tr('已保留本地版本。'))
        self.reload()

    def _keep_all(self) -> None:
        try:
            handled = adapter.keep_all_local_conflicts(self.project["project_id"])
        except adapter.AdapterError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText(i18n.tr('已保留本地版本：{v0} 条。', v0=handled))
        self.reload()


class ProjectsPanel(QWidget):
    """项目管理：新建 / 重命名 / 归档 / 删除（保护由核心执行）。

    窗口把它作为一页放在共用侧栏右边；:class:`ProjectDialog` 是同一内容的对话框外壳。
    """

    #: 项目被新建、改名、归档或删除之后发出（窗口据此刷新任务库与侧栏的项目列表）。
    projectsChanged = Signal()

    def __init__(self, parent: QWidget | None = None, *,
                 margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self.changed = False

        title = QLabel(i18n.tr('项目'))
        title.setObjectName("pageTitle")
        hint = QLabel(
            i18n.tr('项目决定任务使用哪一套记忆与术语。删除项目需要逐字输入名称；项目下仍有任务时不能删除，删除前会自动备份。'))
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels([i18n.tr('项目'), i18n.tr('任务'), i18n.tr('状态'), i18n.tr('说明')])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setFixedHeight(theme_module.CONTROL["header"])
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.verticalHeader().setDefaultSectionSize(
            theme_module.CONTROL["row"] - 4)
        # 与首页任务表同一套行观感：发丝分隔线 + 选中行左侧强调条
        self.table.setItemDelegate(style_module.RowDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._selection_changed)

        self.new_button = QPushButton(i18n.tr('新建项目…'))
        self.new_button.setIcon(icons.icon(
            "plus", 16, "text-on-accent"))
        style_module.mark_primary(self.new_button)
        self.rename_button = QPushButton(i18n.tr('重命名…'))
        self.rename_button.setIcon(icons.icon(
            "file-text", 16, "text-secondary"))
        self.archive_button = QPushButton(i18n.tr('归档'))
        self.archive_button.setIcon(icons.icon(
            "folder", 16, "text-secondary"))
        self.delete_button = QPushButton(i18n.tr('删除项目…'))
        style_module.set_button(self.delete_button, "danger")
        self.delete_button.setIcon(icons.icon(
            "trash", 16, "danger"))
        for button in (self.new_button, self.rename_button,
                       self.archive_button, self.delete_button):
            button.setIconSize(QSize(16, 16))
        self.knowledge_button = QPushButton(i18n.tr('项目知识…'))
        self.export_button = QPushButton(i18n.tr('导出记忆…'))
        self.import_button = QPushButton(i18n.tr('导入记忆…'))
        self.describe_button = QPushButton(i18n.tr('编辑说明…'))
        self.describe_button.clicked.connect(self._describe)
        self.merge_button = QPushButton(i18n.tr('并入「未分类」记忆…'))
        self.merge_button.setToolTip(i18n.tr('把「未分类」里已审校的翻译记忆复制进这个项目'))
        self.merge_button.clicked.connect(self._merge_uncategorized)
        for button in (self.knowledge_button, self.export_button, self.import_button):
            button.setIconSize(QSize(16, 16))
        self.knowledge_button.clicked.connect(self._open_knowledge)
        self.export_button.clicked.connect(lambda: self._export_memory())
        self.import_button.clicked.connect(lambda: self._import_memory())
        self.new_button.clicked.connect(self._create)
        self.rename_button.clicked.connect(self._rename)
        self.archive_button.clicked.connect(self._toggle_archive)
        self.delete_button.clicked.connect(self._delete)

        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)

        actions = QHBoxLayout()
        actions.addWidget(self.new_button)
        actions.addWidget(self.rename_button)
        actions.addWidget(self.describe_button)
        actions.addWidget(self.archive_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        memory_actions = QHBoxLayout()
        for button in (self.knowledge_button, self.export_button, self.import_button,
                       self.merge_button):
            memory_actions.addWidget(button)
        memory_actions.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*margins)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addLayout(memory_actions)
        layout.addWidget(self.status)
        self.reload()

    def _mark_changed(self) -> None:
        self.changed = True
        self.projectsChanged.emit()

    # -- data -------------------------------------------------------------
    def reload(self) -> None:
        rows = adapter.project_rows()
        self._rows = rows
        self.table.setRowCount(len(rows))
        tokens = theme_module.tokens(style_module.current_theme())
        for index, project in enumerate(rows):
            values = (project["name"], str(project["job_count"]),
                      "系统工作区" if project["is_system"] else
                      ("已归档" if project["archived"] else "使用中"),
                      project.get("description") or "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, project["project_id"])
                    item.setIcon(icons.icon(
                        "folder", 16, tokens["text-secondary"]))
                    font = item.font()
                    font.setWeight(font.Weight.DemiBold)
                    item.setFont(font)
                if column in (1, 2):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    item.setForeground(style_module.qcolor(tokens["text-secondary"]))
                if column == 2:
                    # 颜色表达语义：使用中=成功、归档/系统=中性（不要用红色吓人）
                    item.setForeground(style_module.qcolor(
                        tokens["text-muted"]
                        if (project["archived"] or project["is_system"])
                        else tokens["success"]))
                if column == 3:
                    item.setForeground(style_module.qcolor(tokens["text-secondary"]))
                self.table.setItem(index, column, item)
        self._selection_changed()

    def selected_project(self) -> dict | None:
        items = self.table.selectedItems()
        if not items:
            return None
        job_id = str(self.table.item(items[0].row(), 0).data(
            Qt.ItemDataRole.UserRole))
        return next((row for row in self._rows
                     if row["project_id"] == job_id), None)

    def _selection_changed(self) -> None:
        project = self.selected_project()
        system = bool(project and project.get("is_system"))
        self.knowledge_button.setEnabled(bool(project))
        self.export_button.setEnabled(bool(project))
        self.rename_button.setEnabled(bool(project) and not system)
        self.describe_button.setEnabled(bool(project) and not system)
        self.merge_button.setEnabled(bool(project) and not system)
        self.archive_button.setEnabled(bool(project) and not system)
        self.delete_button.setEnabled(bool(project) and not system)
        if project:
            self.archive_button.setText(
                "恢复" if project["archived"] else "归档")

    def _note(self, text: str) -> None:
        self.status.setText(text)

    # -- 项目知识 / 记忆导入导出 ---------------------------------------------
    def _open_knowledge(self) -> None:
        project = self.selected_project()
        if project and not bootstrap.automation():
            ProjectKnowledgeDialog(project, self).exec()
            self.reload()

    def _export_memory(self, path: str = "") -> None:
        project = self.selected_project()
        if not project:
            return
        if not path:
            if bootstrap.automation():
                self._note(i18n.tr('导出记忆：自动化运行不打开系统面板，请直接提供文件路径。'))
                return
            path, _ = QFileDialog.getSaveFileName(
                self, i18n.tr('导出项目记忆'), str(Path.home() / f"{project['name']}-memory.json"),
                "JSON (*.json)")
            if not path:
                return
        try:
            adapter.export_project_memory_to(project["project_id"], path)
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self._note(i18n.tr('已导出到 {v0}', v0=path))

    def _import_memory(self, path: str = "") -> None:
        if not path:
            if bootstrap.automation():
                self._note(i18n.tr('导入记忆：自动化运行不打开系统面板，请直接提供文件路径。'))
                return
            path, _ = QFileDialog.getOpenFileName(
                self, i18n.tr('导入项目记忆'), str(Path.home()), "JSON (*.json)")
            if not path:
                return
        try:
            result = adapter.import_project_memory_from(path)
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self._note(i18n.tr('已导入到项目「{v0}」（只增不改；冲突需要在「项目知识」里决定）。',
                           v0=result["project"].get("name") or ""))
        self.reload()

    # -- actions ----------------------------------------------------------
    def _ask_name(self, title: str, label: str, text: str = "") -> tuple[str, bool]:
        """项目名输入。脚本化运行不弹输入框——不擅自编造名字，直接如实说明。"""
        if bootstrap.automation():
            self._note(f"{title}：自动化运行不弹出输入框，请改用命令行或交互运行。")
            return "", False
        return prompts.TextInputDialog.get_text(self, title, label, text=text)

    def _create(self) -> None:
        name, ok = self._ask_name(i18n.tr('新建项目'), i18n.tr('项目名称：'))
        if not ok or not name.strip():
            return
        try:
            project = adapter.create_project(name)
        except adapter.AdapterError as exc:
            self._note(f"无法创建项目：{exc}")
            return
        self._mark_changed()
        self.reload()
        self._note(f"已创建项目「{project.get('name')}」")

    def _rename(self) -> None:
        project = self.selected_project()
        if project is None:
            return
        name, ok = self._ask_name(i18n.tr('重命名项目'), i18n.tr('新名称：'), project["name"])
        if not ok or not name.strip() or name.strip() == project["name"]:
            return
        try:
            adapter.rename_project(project["project_id"], name)
        except adapter.AdapterError as exc:
            self._note(f"无法重命名：{exc}")
            return
        self._mark_changed()
        self.reload()
        self._note(f"已重命名为「{name.strip()}」")

    def _describe(self) -> None:
        project = self.selected_project()
        if project is None:
            return
        text, ok = self._ask_name(i18n.tr('项目说明'), i18n.tr('说明（客户、领域、要求……）：'),
                                  str(project.get("description") or ""))
        if not ok:
            return
        try:
            adapter.update_project_description(project["project_id"], text)
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法保存说明：{v0}', v0=exc))
            return
        self._mark_changed()
        self.reload()
        self._note(i18n.tr('已更新「{v0}」的说明。', v0=project["name"]))

    def _merge_uncategorized(self) -> None:
        project = self.selected_project()
        if project is None:
            return
        if not bootstrap.automation() and not prompts.ConfirmDialog.ask(
                self, i18n.tr('并入「未分类」记忆'),
                i18n.tr('把「未分类」里已审校的翻译记忆复制进「{v0}」。已有的条目不会被覆盖。确定吗？',
                        v0=project["name"]),
                yes=i18n.tr('并入'), no=i18n.tr('取消')):
            return
        try:
            count = adapter.merge_uncategorized_memory(project["project_id"])
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self.reload()
        self._note(i18n.tr('已并入 {v0} 条翻译记忆。', v0=count) if count
                   else i18n.tr('「未分类」里没有可并入的已审校记忆。'))

    def _toggle_archive(self) -> None:
        project = self.selected_project()
        if project is None:
            return
        target = not project["archived"]
        try:
            adapter.archive_project(project["project_id"], archived=target)
        except adapter.AdapterError as exc:
            self._note(f"无法更改归档状态：{exc}")
            return
        self._mark_changed()
        self.reload()
        self._note(i18n.tr("已归档「{v0}」", v0=project["name"]) if target
                   else i18n.tr("已恢复「{v0}」", v0=project["name"]))

    def _delete(self) -> None:
        project = self.selected_project()
        if project is None:
            return
        name = project["name"]
        if project["job_count"]:
            self._note(
                f"「{name}」下仍有 {project['job_count']} 个任务，不能删除；"
                "请先把这些任务移动到其他项目或删除它们。")
            return
        confirm = self._ask_name(
            i18n.tr('删除项目'), i18n.tr('删除项目「{v0}」不可撤销。\n\n请输入项目名称以确认：', v0=name))
        if not confirm[1]:
            return
        try:
            adapter.delete_project(project["project_id"], confirm_name=confirm[0])
        except adapter.AdapterError as exc:
            self._note(f"无法删除：{exc}")
            return
        self._mark_changed()
        self.reload()
        backups = adapter.project_backups()
        self._note(
            i18n.tr("已删除项目「{v0}」；删除前已写入可恢复备份", v0=name)
            + (i18n.tr("（{v0}）", v0=backups[-1]) if backups else ""))


class ProjectDialog(QDialog):
    """项目管理的对话框外壳：内容就是 :class:`ProjectsPanel`。

    窗口里项目管理已经是带侧栏的一页；对话框形态保留给单独调用（与既有用例）。
    面板的属性与方法（``table``、``reload``、``changed``……）直接从对话框上可用。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('项目'))
        self.setMinimumWidth(620)
        self.setMinimumHeight(420)
        self.panel = ProjectsPanel(self)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton(i18n.tr('关闭'))
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.panel, 1)
        layout.addLayout(close_row)

    def __getattr__(self, name: str):
        panel = self.__dict__.get("panel")
        if panel is None:
            raise AttributeError(name)
        return getattr(panel, name)
