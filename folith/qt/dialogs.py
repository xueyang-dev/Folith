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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QRadioButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import i18n
from . import adapter
from . import bootstrap
from . import icons
from . import prompts
from . import style as style_module
from .style import Card
from . import theme as theme_module
from .workers import TaskRunner


def _panel(title: str, hint: str = "") -> tuple[Card, QVBoxLayout]:
    """一张带标题的卡片 + 竖排内容区（层级靠"面"，不靠边框堆叠）。"""
    card = Card()
    outer = QVBoxLayout(card)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName("cardTitle")
    outer.addWidget(heading)
    if hint:
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        outer.addWidget(note)
    return card, outer


def _field(label: str, widget: QWidget, hint: str = "") -> QWidget:
    """表单字段：标签在控件**上方**（旧界面表单的排版，不是右侧窄列对齐）。

    右侧对齐的标签列在中文标签长短不一时会把输入框起点推得忽左忽右，
    长说明也没有地方放；标签在上、说明在下的结构更接近用户已有的旧界面。
    """
    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(4)
    column.addWidget(style_module.field_label(label))
    column.addWidget(widget)
    if hint:
        note = QLabel(hint)
        note.setObjectName("fieldHint")
        note.setWordWrap(True)
        column.addWidget(note)
    return holder


def _scroll_page(inner: QWidget) -> QScrollArea:
    """标签页内容放进滚动区：设置页最长的一栏不该把整个对话框顶出屏幕。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setWidget(inner)
    return area


def _set_status(label: QLabel, text: str, tone: str = "muted") -> None:
    """内联状态文字：成功/失败要有颜色，不能都是同一撮灰字。"""
    label.setText(text)
    label.setObjectName({"ok": "statusOk", "danger": "statusError"}.get(tone, "hint"))
    style_module.repolish(label)


class EngineDialog(QDialog):
    """Model connection + the task defaults a new job will use."""

    def __init__(self, config: adapter.EngineConfig, runner: TaskRunner,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('设置'))
        self.setMinimumWidth(680)
        self.resize(820, 720)
        self.config = config
        self.runner = runner
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName("hint")

        self.theme_preference = theme_module.FOLLOW_SYSTEM
        self.language_preference = i18n.language()

        title = QLabel(i18n.tr('设置'))
        title.setObjectName("pageTitle")
        subtitle = QLabel(i18n.tr('模型连接、任务默认值与外观。配置保存在本机，仅当前用户可读。'))
        subtitle.setObjectName("hint")

        header = QVBoxLayout()
        header.setSpacing(2)
        header.addWidget(title)
        header.addWidget(subtitle)

        tabs = QTabWidget(self)
        tabs.addTab(_scroll_page(self._build_connection_tab()),
                    icons.icon("sparkles", 16, style_module.text_color("text-secondary")),
                    i18n.tr('模型连接'))
        tabs.addTab(_scroll_page(self._build_task_tab()),
                    icons.icon("package", 16, style_module.text_color("text-secondary")),
                    i18n.tr('任务与交付'))
        tabs.addTab(_scroll_page(self._build_ocr_tab()),
                    icons.icon("eye", 16, style_module.text_color("text-secondary")),
                    i18n.tr('扫描件 OCR'))
        tabs.addTab(_scroll_page(self._build_appearance_tab()),
                    icons.icon("image", 16, style_module.text_color("text-secondary")),
                    i18n.tr('外观'))
        tabs.setIconSize(QSize(16, 16))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel, self)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText(i18n.tr('保存'))
        save_button.setIcon(icons.icon(
            "save", 16, style_module.text_color("text-on-accent")))
        save_button.setIconSize(QSize(16, 16))
        style_module.mark_primary(save_button)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(i18n.tr('取消'))
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(tabs, 1)
        layout.addWidget(self._status)
        layout.addWidget(buttons)

    # -- connection --------------------------------------------------------
    def _build_connection_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(12)
        connection_card, body = _panel(
            i18n.tr('模型连接'), i18n.tr('译文与审校都通过这里的模型调用；API Key 只保存在本机。'))

        self.provider_box = QComboBox()
        self.provider_box.addItems(list(self.config.providers.keys()))
        if self.config.provider:
            self.provider_box.setCurrentText(self.config.provider)
        self.provider_box.currentTextChanged.connect(self._provider_changed)

        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_box.setCurrentText(self.config.model)

        self.fetch_button = QPushButton(i18n.tr('获取可用模型'))
        self.fetch_button.setIcon(icons.icon("refresh", 16))
        self.fetch_button.setIconSize(QSize(16, 16))
        self.fetch_button.clicked.connect(self._fetch_models)
        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(8)
        model_layout.addWidget(self.model_box, 1)
        model_layout.addWidget(self.fetch_button)

        # 旧界面把"服务商 / 模型"并排放在一行：两项都短，分开排会白占一屏高度。
        pair = QWidget()
        pair_layout = QGridLayout(pair)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setHorizontalSpacing(12)
        pair_layout.addWidget(_field(i18n.tr('模型提供商'), self.provider_box), 0, 0)
        pair_layout.addWidget(_field(i18n.tr('模型'), model_row), 0, 1)
        pair_layout.setColumnStretch(0, 1)
        pair_layout.setColumnStretch(1, 2)

        self.key_field = style_module.SecretField(self.config.api_key)
        self.key_edit = self.key_field.line_edit        # 取值/校验仍走 QLineEdit
        self.key_edit.setPlaceholderText(i18n.tr('保存在本机，仅当前用户可读'))

        self.base_edit = QLineEdit(self.config.base_url)
        self.base_edit.setPlaceholderText(i18n.tr('http(s)://主机/v1'))

        self.target_box = QComboBox()
        self.target_box.setEditable(True)
        self.target_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target_box.addItems(list(adapter.TARGET_LANGUAGES))
        self.target_box.setCurrentText(self.config.target_lang)

        self.test_button = QPushButton(i18n.tr('测试连接'))
        self.test_button.setIcon(icons.icon("check-circle", 16))
        self.test_button.setIconSize(QSize(16, 16))
        self.test_button.clicked.connect(self._test_connection)
        self.test_label = QLabel(i18n.tr('尚未测试'))
        self.test_label.setObjectName("hint")
        self.test_label.setWordWrap(True)
        test_row = QWidget()
        test_layout = QHBoxLayout(test_row)
        test_layout.setContentsMargins(0, 0, 0, 0)
        test_layout.setSpacing(10)
        test_layout.addWidget(self.test_button)
        test_layout.addWidget(self.test_label, 1)

        body.addWidget(pair)
        body.addWidget(_field(i18n.tr('目标语言'), self.target_box))
        body.addWidget(_field(i18n.tr('API 密钥'), self.key_field,
                              i18n.tr('留空表示沿用已保存的密钥；输入内容不会显示在诊断包或日志里。')))
        body.addWidget(_field(i18n.tr('API 地址'), self.base_edit))
        body.addWidget(_field(i18n.tr('连通性'), test_row))

        reviewer_card, reviewer_body = _panel(
            i18n.tr('独立审校模型（可选）'),
            i18n.tr('译审同源时审校只是自查；换一个模型能降低同一模型自我确认的风险，代价是更慢、需要额外的额度。'))
        self.reviewer_same_radio = style_module.RadioButton(i18n.tr('与翻译模型相同'))
        self.reviewer_separate_radio = style_module.RadioButton(i18n.tr('单独配置'))
        self.reviewer_group = QButtonGroup(self)
        self.reviewer_group.addButton(self.reviewer_same_radio)
        self.reviewer_group.addButton(self.reviewer_separate_radio)
        self.reviewer_same_radio.setChecked(
            self.config.reviewer_mode != "separate")
        self.reviewer_separate_radio.setChecked(
            self.config.reviewer_mode == "separate")
        self.reviewer_same_radio.toggled.connect(self._reviewer_changed)
        mode_row = QWidget()
        mode_layout = QHBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(16)
        mode_layout.addWidget(self.reviewer_same_radio)
        mode_layout.addWidget(self.reviewer_separate_radio)
        mode_layout.addStretch(1)

        self.reviewer_provider_box = QComboBox()
        self.reviewer_provider_box.addItems([""] + list(self.config.providers.keys()))
        self.reviewer_provider_box.setCurrentText(self.config.reviewer_provider)
        self.reviewer_model_edit = QLineEdit(self.config.reviewer_model)
        self.reviewer_key_field = style_module.SecretField(self.config.reviewer_api_key)
        self.reviewer_key_edit = self.reviewer_key_field.line_edit
        self.reviewer_base_edit = QLineEdit(self.config.reviewer_base_url)
        self.reviewer_fields = QWidget()
        reviewer_fields_layout = QVBoxLayout(self.reviewer_fields)
        reviewer_fields_layout.setContentsMargins(0, 0, 0, 0)
        reviewer_fields_layout.setSpacing(10)
        reviewer_fields_layout.addWidget(_field(i18n.tr('提供商'), self.reviewer_provider_box))
        reviewer_fields_layout.addWidget(_field(i18n.tr('模型名'), self.reviewer_model_edit))
        reviewer_fields_layout.addWidget(_field(i18n.tr('API 密钥'), self.reviewer_key_field))
        reviewer_fields_layout.addWidget(_field(
            i18n.tr('API 地址'), self.reviewer_base_edit,
            i18n.tr('留空时：提供商与正文相同则沿用正文地址，不同则必须填写。')))

        reviewer_body.addWidget(mode_row)
        reviewer_body.addWidget(self.reviewer_fields)

        page_layout.addWidget(connection_card)
        page_layout.addWidget(reviewer_card)
        page_layout.addStretch(1)

        self._provider_changed(self.provider_box.currentText())
        self._reviewer_changed()
        return page

    def _build_task_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(12)

        workflow, workflow_body = _panel(
            i18n.tr('翻译流程'), i18n.tr('这些是新建任务的默认值；已有任务保留它自己保存的选择。'))
        self.auto_term_box = style_module.CheckBox(i18n.tr('自动抽取并注入术语'))
        self.auto_term_box.setChecked(self.config.auto_term)
        self.review_box = style_module.CheckBox(i18n.tr('完成后自动审校'))
        self.review_box.setChecked(self.config.enable_review)
        self.tm_box = style_module.CheckBox(i18n.tr('复用翻译记忆'))
        self.tm_box.setChecked(self.config.use_tm)
        self.understanding_box = style_module.CheckBox(i18n.tr('先建立文档画像（更慢，术语更准）'))
        self.understanding_box.setChecked(self.config.enable_understanding)
        self.cleanup_box = style_module.CheckBox(i18n.tr('对 PDF 原文做模型纠错（更慢）'))
        self.cleanup_box.setChecked(self.config.enable_source_cleanup)
        self.report_box = style_module.CheckBox(i18n.tr('生成实践报告（需要更长的运行时间）'))
        self.report_box.setChecked(self.config.enable_report)
        # 两列网格：六项都是单行短句，一列排下来会白占半屏。
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for position, box in enumerate((self.auto_term_box, self.review_box,
                                        self.tm_box, self.understanding_box,
                                        self.cleanup_box, self.report_box)):
            grid.addWidget(box, position // 2, position % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        workflow_body.addLayout(grid)
        outer.addWidget(workflow)

        delivery, delivery_body = _panel(
            i18n.tr('交付文件'), i18n.tr('只生成勾选的格式；冻结交付后快照不可修改。'))
        self.deliver_plain_box = style_module.CheckBox(i18n.tr('纯译文 DOCX（translation.docx）'))
        self.deliver_plain_box.setChecked(bool(getattr(self.config, "deliver_plain_docx", True)))
        self.deliver_bilingual_box = style_module.CheckBox(i18n.tr('双语对照 DOCX（bilingual.docx）'))
        self.deliver_bilingual_box.setChecked(bool(getattr(self.config, "deliver_bilingual_docx", True)))
        self.deliver_terms_box = style_module.CheckBox(i18n.tr('术语表 XLSX（terms.xlsx）'))
        self.deliver_terms_box.setChecked(bool(getattr(self.config, "deliver_terms_xlsx", True)))
        self.deliver_evidence_box = style_module.CheckBox(i18n.tr('段落证据 JSONL（segment_evidence.jsonl）'))
        self.deliver_evidence_box.setChecked(bool(getattr(self.config, "deliver_evidence", True)))
        self.deliver_original_pdf_box = style_module.CheckBox(
            i18n.tr('原排版译文 PDF（Beta，original_layout.pdf）'))
        self.deliver_original_pdf_box.setChecked(
            bool(getattr(self.config, "deliver_original_pdf", False)))
        # This dialog edits client defaults before a file is chosen, so it
        # cannot know the source type; the hint states the constraint and the
        # import mapping drops the option for a non-PDF source.
        self.deliver_original_pdf_box.setToolTip(
            i18n.tr('仅 PDF 任务可用：按原文件的版式重建译文页面。'))
        self.deliver_pdf_box = style_module.CheckBox(i18n.tr('译文 PDF（translation.pdf）'))
        self.deliver_pdf_box.setChecked(
            bool(getattr(self.config, "deliver_pdf", False)))
        for box in (self.deliver_plain_box, self.deliver_bilingual_box,
                    self.deliver_terms_box, self.deliver_evidence_box,
                    self.deliver_pdf_box, self.deliver_original_pdf_box):
            delivery_body.addWidget(box)
        outer.addWidget(delivery)
        outer.addStretch(1)
        return page

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

    def _build_ocr_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(12)
        ocr_card, body = _panel(
            i18n.tr('扫描件 OCR'), i18n.tr('只影响没有文本层的扫描件；文本层 PDF 与 DOCX 不走 OCR。'))
        self.ocr_language_box = QComboBox()
        # 可编辑：既能从核心注册表里选（30 项，带中文标签），也能直接填语言代码。
        self.ocr_language_box.setEditable(True)
        self.ocr_language_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for label, value in adapter.ocr_language_choices():
            self.ocr_language_box.addItem(label, value)
        self._select_ocr_language(self.config.ocr_language)
        self.ocr_mode_box = QComboBox()
        # 标签给用户看，data 给核心用：两者必须对齐，否则核心会静默回退成 auto
        # （历史缺陷：界面写 always/never，核心只认 force/off）。
        for label, mode in adapter.OCR_MODE_CHOICES:
            self.ocr_mode_box.addItem(label, mode)
        self.ocr_mode_box.setCurrentText(
            adapter.ocr_mode_label(self.config.ocr_mode))
        self.ocr_backend_box = QComboBox()
        # 取值仍然是核心认识的那三个，但显示的是人话（此前直接显示 online / tesseract）。
        for label, value in (("联网识别（默认）", "online"),
                             ("Tesseract（本机）", "tesseract"),
                             ("PaddleOCR（本机）", "paddle")):
            self.ocr_backend_box.addItem(label, value)
        index = self.ocr_backend_box.findData(self.config.ocr_backend or "online")
        self.ocr_backend_box.setCurrentIndex(index if index >= 0 else 0)
        body.addWidget(_field(i18n.tr('识别语言'), self.ocr_language_box))
        body.addWidget(_field(i18n.tr('何时识别'), self.ocr_mode_box))
        body.addWidget(_field(i18n.tr('识别引擎'), self.ocr_backend_box))

        notice = style_module.NoticeStrip(
            i18n.tr('扫描件需要本机或随包安装 Tesseract 语言包。Beta 基线为 eng / chi_sim / ara；缺少语言包时会明确报错，不会静默改用英语。'), tone="info")
        page_layout.addWidget(ocr_card)
        page_layout.addWidget(notice)
        page_layout.addStretch(1)
        return page

    def _build_appearance_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(12)
        card, body = _panel(
            i18n.tr('界面主题'),
            i18n.tr('「跟随系统」随 macOS 浅色/深色外观自动切换。三套主题与旧界面共用同一份品牌 token（folith.ui.theme_tokens），因此不存在两套配色。'))
        self.theme_box = QComboBox()
        for name in (theme_module.FOLLOW_SYSTEM, *theme_module.theme_names()):
            self.theme_box.addItem(theme_module.label(name), name)
        index = self.theme_box.findData(self.theme_preference)
        self.theme_box.setCurrentIndex(index if index >= 0 else 0)
        self.theme_box.currentIndexChanged.connect(self._sync_theme_cards)
        previews = self._build_theme_previews()
        body.addWidget(_field(i18n.tr('主题'), self.theme_box))
        body.addWidget(_field(i18n.tr('预览'), previews, i18n.tr('点一张色卡即可选中对应主题。')))

        self.language_box = QComboBox()
        for code, label in i18n.available_languages():
            self.language_box.addItem(label, code)
        self._select_language(self.language_preference)
        body.addWidget(_field(
            i18n.tr('界面语言'), self.language_box,
            i18n.tr('非中文界面为实验功能（尚未完成独立的语言审阅）。语言在应用启动时生效，保存后请重启；阿拉伯语会整体从右到左排版。')))
        page_layout.addWidget(card)
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

    def _build_theme_previews(self) -> QWidget:
        """三张可点选的色卡：颜色直接取自 token，不写死。

        此前色卡是"只能用来看"的方框——它长得像按钮却不是按钮，点了没有反应。
        现在每张卡就是主题选择器：选中态用 accent 描边（状态由 `_sync_theme_cards`
        跟随下拉框同步），下拉框仍然是保存时的唯一真值。
        """
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._theme_cards: dict[str, QPushButton] = {}
        for name in theme_module.theme_names():
            tokens = theme_module.tokens(name)
            card = QPushButton()
            card.setObjectName("themeCard")
            card.setCheckable(True)
            card.setFixedSize(120, 78)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setToolTip(i18n.tr('使用「{v0}」主题', v0=theme_module.label(name)))
            card.clicked.connect(
                lambda _checked=False, value=name: self._pick_theme(value))
            inner = QVBoxLayout(card)
            inner.setContentsMargins(7, 7, 7, 7)
            inner.setSpacing(5)
            swatch = QFrame()
            swatch.setFixedHeight(34)
            swatch.setStyleSheet(
                f"QFrame {{ background: {tokens['bg-surface']};"
                f" border: 1px solid {tokens['border-subtle']};"
                f" border-radius: 8px; }}")
            swatch_layout = QVBoxLayout(swatch)
            swatch_layout.setContentsMargins(8, 8, 8, 8)
            swatch_layout.setSpacing(0)
            accent = QFrame()
            accent.setFixedHeight(9)
            accent.setStyleSheet(
                f"QFrame {{ background: {tokens['accent']}; border: none;"
                f" border-radius: 4px; }}")
            swatch_layout.addWidget(accent)
            caption = QLabel(theme_module.label(name))
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setFixedHeight(16)
            caption.setStyleSheet(
                f"QLabel {{ color: {tokens['text-secondary']}; font-size: 11px;"
                f" background: transparent; }}")
            inner.addWidget(swatch)
            inner.addWidget(caption)
            row.addWidget(card)
            self._theme_cards[name] = card
        row.addStretch(1)
        self._sync_theme_cards()
        return holder

    def _pick_theme(self, name: str) -> None:
        index = self.theme_box.findData(name)
        if index >= 0:
            self.theme_box.setCurrentIndex(index)

    def _sync_theme_cards(self, *_args) -> None:
        """下拉框 -> 色卡选中态（保存仍以下拉框的 data 为准）。"""
        current = str(self.theme_box.currentData() or "")
        for name, card in getattr(self, "_theme_cards", {}).items():
            card.setChecked(name == current)

    # -- behaviour ---------------------------------------------------------
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

    def _reviewer_changed(self) -> None:
        """同一模型时**整块收起**：四个灰输入框既不能用，又占着半个卡片。"""
        separate = self.reviewer_separate_radio.isChecked()
        self.reviewer_fields.setVisible(separate)
        self.reviewer_fields.setEnabled(separate)
        for widget in (self.reviewer_provider_box, self.reviewer_model_edit,
                       self.reviewer_key_field, self.reviewer_base_edit):
            widget.setEnabled(separate)

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
            reviewer_mode="separate" if self.reviewer_separate_radio.isChecked()
            else "same",
            reviewer_provider=self.reviewer_provider_box.currentText().strip(),
            reviewer_model=self.reviewer_model_edit.text().strip(),
            reviewer_api_key=self.reviewer_key_edit.text().strip(),
            reviewer_base_url=self.reviewer_base_edit.text().strip(),
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
        return config

    def _save(self) -> None:
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
            config.save()
        except Exception as exc:  # noqa: BLE001 - the user must see the reason
            if bootstrap.automation():
                self._status.setText(i18n.tr('无法保存设置：{v0}', v0=exc))
            else:
                prompts.NoticeDialog.notify(self, i18n.tr('保存失败'),
                                          i18n.tr('无法保存设置：{v0}', v0=exc), tone="danger")
            return
        self.config = config
        self.theme_preference = str(
            self.theme_box.currentData() or theme_module.FOLLOW_SYSTEM)
        self.language_preference = str(
            self.language_box.currentData() or i18n.DEFAULT_LANGUAGE)
        self.accept()

    def _test_connection(self) -> None:
        config = self._collect()
        self.test_button.setEnabled(False)
        _set_status(self.test_label, "正在测试…")

        def done(result) -> None:
            ok, message = result
            self.test_button.setEnabled(True)
            _set_status(self.test_label,
                        i18n.tr("连接正常：{v0}", v0=message) if ok
                        else i18n.tr("连接失败：{v0}", v0=message),
                        "ok" if ok else "danger")

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

    def _fetch_models(self) -> None:
        provider = self.provider_box.currentText().strip()
        api_key = self.key_edit.text().strip()
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
                prompts.NoticeDialog.notify(self, i18n.tr('保存失败'),
                                          i18n.tr('无法写出诊断包：{v0}', v0=exc), tone="danger")
            return
        if bootstrap.automation():
            self._status.setText(i18n.tr('诊断包已写入：{v0}', v0=written))
        else:
            prompts.NoticeDialog.notify(self, i18n.tr('已保存'),
                                      i18n.tr('诊断包已写入：\n{v0}', v0=written), tone="ok")


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
                           style_module.text_color("text-secondary")), label)
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

        self.move_tm_box = style_module.CheckBox(i18n.tr('同时把已审校译文并入目标项目的翻译记忆'))
        self.move_tm_box.setToolTip(i18n.tr('只增不改：目标项目已有的译法不会被覆盖'))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText(i18n.tr('移动'))
        ok_button.setIcon(icons.icon(
            "arrow-right", 16, style_module.text_color("text-on-accent")))
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
        layout.addWidget(self.move_tm_box)
        layout.addWidget(buttons)

    def selected_project(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def move_translations(self) -> bool:
        return self.move_tm_box.isChecked()


class ProjectDialog(QDialog):
    """项目管理：新建 / 重命名 / 归档 / 删除（保护由核心执行）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.tr('项目'))
        self.setMinimumWidth(620)
        self.setMinimumHeight(420)
        self.changed = False

        title = QLabel(i18n.tr('项目'))
        title.setObjectName("pageTitle")
        hint = QLabel(
            i18n.tr('项目决定任务注入哪一套记忆与术语。删除项目受核心保护：系统工作区不可删除、必须逐字输入名称、项目下仍有任务时拒绝删除、删除前会写入可恢复备份。'))
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
            "plus", 16, style_module.text_color("text-on-accent")))
        style_module.mark_primary(self.new_button)
        self.rename_button = QPushButton(i18n.tr('重命名…'))
        self.rename_button.setIcon(icons.icon(
            "file-text", 16, style_module.text_color("text-secondary")))
        self.archive_button = QPushButton(i18n.tr('归档'))
        self.archive_button.setIcon(icons.icon(
            "folder", 16, style_module.text_color("text-secondary")))
        self.delete_button = QPushButton(i18n.tr('删除项目…'))
        self.delete_button.setObjectName("dangerAction")
        self.delete_button.setIcon(icons.icon(
            "trash", 16, style_module.text_color("danger")))
        for button in (self.new_button, self.rename_button,
                       self.archive_button, self.delete_button):
            button.setIconSize(QSize(16, 16))
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
        actions.addWidget(self.archive_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)

        close_row = QHBoxLayout()
        close_row.addWidget(self.status, 1)
        close_button = QPushButton(i18n.tr('关闭'))
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addLayout(close_row)
        self.reload()

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
                    item.setForeground(QColor(tokens["text-secondary"]))
                if column == 2:
                    # 颜色表达语义：使用中=成功、归档/系统=中性（不要用红色吓人）
                    item.setForeground(QColor(
                        tokens["text-muted"]
                        if (project["archived"] or project["is_system"])
                        else tokens["success"]))
                if column == 3:
                    item.setForeground(QColor(tokens["text-secondary"]))
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
        self.rename_button.setEnabled(bool(project) and not system)
        self.archive_button.setEnabled(bool(project) and not system)
        self.delete_button.setEnabled(bool(project) and not system)
        if project:
            self.archive_button.setText(
                "恢复" if project["archived"] else "归档")

    def _note(self, text: str) -> None:
        self.status.setText(text)

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
        self.changed = True
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
        self.changed = True
        self.reload()
        self._note(f"已重命名为「{name.strip()}」")

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
        self.changed = True
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
        self.changed = True
        self.reload()
        backups = adapter.project_backups()
        self._note(
            i18n.tr("已删除项目「{v0}」；删除前已写入可恢复备份", v0=name)
            + (i18n.tr("（{v0}）", v0=backups[-1]) if backups else ""))
