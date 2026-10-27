"""术语页：审核候选术语、锁定、冻结，翻译开始后逐条修订。

旧界面的"术语"工作区在 Qt 里原来没有，而严格术语治理的任务会在候选术语未冻结时
被核心拒绝开始翻译——没有这个面板，这类任务（包括接入的旧任务）就没有出口。

只做展示与发信号；真正写状态的是窗口层调用的适配器（沿用核心的术语版本机制）：

* 翻译开始前：面板里**暂存**修改，「保存草稿 / 冻结并继续 / 跳过冻结」才落盘；
* 翻译开始后：每次修订立即走核心的逐条接口，由核心生成新的术语版本。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QDialog, QFormLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
    QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import components
from . import i18n
from . import icons
from . import style as style_module
from .style import Card
from .workers import TaskRunner
from folith.workspace_presentation import term_impact, terminology_duplicates
from folith.terminology import entry_matches

STATUS_ORDER = ("candidate", "provisional", "locked", "rejected")
STATUS_LABELS = {"candidate": "候选", "provisional": "暂定",
                 "locked": "已锁定", "rejected": "已拒绝"}
COMPACT_STATUS_LABELS = {"candidate": "候选", "provisional": "暂定",
                         "locked": "锁定", "rejected": "拒绝"}
COLUMNS = ("源术语", "首选译名", "状态", "次数", "冲突数量", "最近修改")


class _TermsEditorCard(Card):
    """Card whose minimum height follows the wrapped action rows."""

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self.sync_minimum_height)

    def sync_minimum_height(self) -> None:
        layout = self.layout()
        if layout is None or self.width() <= 0:
            return
        required = layout.heightForWidth(self.width())
        if required > 0 and self.minimumHeight() != required:
            self.setMinimumHeight(required)


class TermsPanel(QWidget):
    """术语表编辑器。"""

    workspaceRequested = Signal()
    locateRequested = Signal(dict)
    segmentRequested = Signal(int)
    replacementRequested = Signal(str, str, object)
    replacementUndoRequested = Signal()
    conflictsRequested = Signal()
    mergeRequested = Signal(str, str, list)
    draftSaveRequested = Signal(list)           # rows
    freezeRequested = Signal(list)              # rows
    unfreezeRequested = Signal()
    bypassRequested = Signal()
    entryUpdateRequested = Signal(str, dict)    # entry id, fields
    entryAddRequested = Signal(dict)
    entryDeleteRequested = Signal(str)
    statusRequested = Signal(list, str)         # entry ids, status（翻译开始后批量改状态）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._job_id = ""
        self._rows: list[dict] = []
        self._dirty = False
        self._form_drafts = {}
        self._form_key = None
        self._form_baseline = None
        self._loading_form = False
        self._busy = False
        self._message = ""
        self._shown: list[int] = []
        #: 术语为 0 时点了「添加术语…」：只露出新建表单，不露空表格和筛选。
        self._adding = False
        self._compact = False
        self._pairs = []
        self._segment_source = ''
        self._conflict_counts = {}
        self._review_items = []
        self._impact_generation = 0
        self._impact_runner = TaskRunner(self, max_threads=1)
        self._impact_timer = QTimer(self)
        self._impact_timer.setSingleShot(True)
        self._impact_timer.timeout.connect(self._compute_impact)

        def button(text: str, icon_name: str, token: str = "text-secondary",
                   name: str = "") -> QPushButton:
            widget = QPushButton(text)
            widget.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
            widget.setIcon(icons.icon(icon_name, 16, token))
            widget.setIconSize(QSize(16, 16))
            if name:
                style_module.set_button(widget, name)
            return widget

        self.banner = style_module.NoticeStrip("", tone="info")
        self.heading = QLabel("")
        self.heading.setObjectName("termsHeading")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("termsTable")
        self.table.setHorizontalHeaderLabels([i18n.tr(c) for c in COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        self.table.itemSelectionChanged.connect(self._load_form)

        self.source_edit = QLineEdit()
        self.preferred_edit = QLineEdit()
        self.status_box = style_module.ComboBox()
        for value in STATUS_ORDER:
            self.status_box.addItem(i18n.tr(STATUS_LABELS[value]), value)
        self.domain_edit = QLineEdit()
        self.note_edit = QLineEdit()
        # 禁用译名：译文里出现就报术语问题，工作台右栏也会标红。
        self.forbidden_edit = QLineEdit()
        self.forbidden_edit.setPlaceholderText(i18n.tr('多个用逗号或分号隔开'))
        for edit in (self.source_edit, self.preferred_edit, self.domain_edit,
                     self.note_edit, self.forbidden_edit):
            edit.textChanged.connect(self._form_changed)
        self.status_box.currentIndexChanged.connect(self._form_changed)

        # 筛选：搜索源术语 / 按状态 / 只看冲突项（术语多时一屏看不过来）。
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.tr('搜索源术语或译名'))
        self.search_edit.textChanged.connect(self._fill_table)
        self.filter_box = style_module.ComboBox()
        self.filter_box.addItem(i18n.tr('全部状态'), "")
        for value in STATUS_ORDER:
            self.filter_box.addItem(i18n.tr(STATUS_LABELS[value]), value)
        self.filter_box.currentIndexChanged.connect(self._fill_table)
        self.conflict_box = style_module.CheckBox(i18n.tr('只看冲突项'))
        self.conflict_box.toggled.connect(self._fill_table)
        self.current_segment_box = style_module.CheckBox(i18n.tr('当前段落'))
        self.current_segment_box.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        self.current_segment_box.toggled.connect(self._fill_table)
        # 复选框的实际宽度比 sizeHint 宽，按整框排，否则折行算错、末字被裁掉。
        self.conflict_box.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        # macOS 默认输入框停在建议宽度：窄栏里留一大块空白。
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow(i18n.tr('源术语'), self.source_edit)
        form.addRow(i18n.tr('首选译名'), self.preferred_edit)
        form.addRow(i18n.tr('状态'), self.status_box)
        form.addRow(i18n.tr('领域'), self.domain_edit)
        form.addRow(i18n.tr('禁用译名'), self.forbidden_edit)
        form.addRow(i18n.tr('备注'), self.note_edit)
        self.form_layout = form

        self.apply_row_button = button(i18n.tr('应用到所选术语'), "check")
        self.apply_row_button.clicked.connect(self._apply_row)
        self.undo_form_button = button(i18n.tr('撤销未保存修改'), 'refresh')
        self.undo_form_button.clicked.connect(self._undo_form)
        self.add_button = button(i18n.tr('添加术语'), "upload")
        self.add_button.clicked.connect(self._add_row)
        self.delete_button = button(i18n.tr('删除所选'), "trash", "danger")
        self.delete_button.clicked.connect(self._delete_row)
        self.lock_all_button = button(i18n.tr('锁定全部候选'), "shield-check")
        self.lock_all_button.setToolTip(i18n.tr('把所有「候选」和「暂定」术语一次性改为「已锁定」'))
        self.lock_all_button.clicked.connect(self._lock_candidates)
        self.lock_selected_button = button(i18n.tr('锁定所选'), "check")
        self.lock_selected_button.clicked.connect(lambda: self._set_selected_status("locked"))
        self.reject_selected_button = button(i18n.tr('拒绝所选'), "x")
        self.reject_selected_button.clicked.connect(lambda: self._set_selected_status("rejected"))

        self.save_button = button(i18n.tr('保存草稿'), "save")
        self.save_button.clicked.connect(lambda: self._save_staged(False))
        self.freeze_button = button(i18n.tr('冻结并继续翻译'), "check-circle",
                                    "text-on-accent", "primary")
        self.freeze_button.clicked.connect(lambda: self._save_staged(True))
        self.bypass_button = button(i18n.tr('跳过冻结，使用暂定术语'), "arrow-right")
        self.bypass_button.clicked.connect(self.bypassRequested.emit)
        self.unfreeze_button = button(i18n.tr('解除冻结'), "refresh")
        self.unfreeze_button.clicked.connect(self.unfreezeRequested.emit)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")
        self.workspace_button = QPushButton(i18n.tr('打开完整术语管理'))
        self.workspace_button.clicked.connect(self.workspaceRequested.emit)
        self.conflicts_button = QPushButton(i18n.tr('已记录的术语审校问题'))
        self.conflicts_button.clicked.connect(self.conflictsRequested.emit)
        self.duplicates_button = QPushButton(i18n.tr('检查潜在重复'))
        self.duplicates_button.clicked.connect(self._find_duplicates)
        self.locate_button = QPushButton(i18n.tr('定位相关段落'))
        self.locate_button.clicked.connect(self._locate_current)
        self.impact_heading = QLabel('')
        self.impact_heading.setWordWrap(True)
        self.impact_table = QTableWidget(0, 3)
        self.impact_table.setHorizontalHeaderLabels([i18n.tr('段'), i18n.tr('原文'), i18n.tr('译文')])
        self.impact_table.verticalHeader().setVisible(False)
        self.impact_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.impact_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.impact_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.impact_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.impact_table.cellDoubleClicked.connect(self._open_impact_segment)
        self.replace_one_button = QPushButton(i18n.tr('预览并应用所选段'))
        self.replace_one_button.clicked.connect(lambda: self._request_replacement(True))
        self.replace_all_button = QPushButton(i18n.tr('预览并批量应用'))
        self.replace_all_button.clicked.connect(lambda: self._request_replacement(False))
        self.undo_replace_button = QPushButton(i18n.tr('撤销上次术语替换'))
        self.undo_replace_button.clicked.connect(self.replacementUndoRequested.emit)
        self.impact_actions = QWidget()
        actions = components.FlowRow(self.impact_actions, spacing=6)
        for widget in (self.replace_one_button, self.replace_all_button, self.undo_replace_button):
            actions.addWidget(widget)

        # 按钮行放不下就折行：术语表在翻译页左栏里展开，只有约 400px 宽。
        # Let button rows reflow from the actual font and control metrics. A
        # fixed 3-column grid overlapped at high display scaling because the
        # style's button height exceeded the grid's assumed row height.
        edit_actions_host = QWidget()
        edit_policy = edit_actions_host.sizePolicy()
        edit_policy.setHeightForWidth(True)
        edit_actions_host.setSizePolicy(edit_policy)
        edit_actions = components.FlowRow(edit_actions_host, spacing=6)
        for widget in (self.apply_row_button, self.add_button, self.delete_button,
                       self.lock_selected_button, self.reject_selected_button,
                       self.lock_all_button, self.undo_form_button):
            edit_actions.addWidget(widget)
        gate_actions = components.FlowRow(spacing=6)
        for widget in (self.freeze_button, self.save_button, self.bypass_button,
                       self.unfreeze_button):
            gate_actions.addWidget(widget)

        editor_card = _TermsEditorCard()
        editor_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(16, 14, 16, 14)
        editor_layout.setSpacing(10)
        self.details_button = QPushButton(i18n.tr('详细属性'))
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._sync_details)
        editor_layout.addWidget(self.details_button)
        editor_layout.addLayout(form)
        editor_layout.addWidget(edit_actions_host)

        # 术语为 0 时整页是一个空状态（设计审校 40 号 6.2：原来提示条、"0 个术语"、
        # 空表格、整张新建表单和四个禁用按钮把同一个"空"说了四遍）。
        self.empty_state = style_module.EmptyState(
            icon="termbase", page=True, title=i18n.tr('这个任务还没有术语'),
            hint=i18n.tr('添加一条术语，翻译会按它统一译名。'),
            action=i18n.tr('添加术语…'))
        self.empty_state.actionTriggered.connect(self._start_adding)
        self.editor_card = editor_card
        self.filter_bar = QWidget()
        filter_layout = QVBoxLayout(self.filter_bar)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        filter_top = components.FlowRow(spacing=6)
        filter_top.addWidget(self.search_edit)
        filter_top.addWidget(self.current_segment_box)
        filters = components.FlowRow(spacing=6)
        self.search_edit.setMinimumWidth(160)
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filter_layout.addLayout(filter_top)
        filters.addWidget(self.filter_box)
        filters.addWidget(self.conflict_box)
        filter_layout.addLayout(filters)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.banner)
        layout.addWidget(self.heading)
        layout.addWidget(self.filter_bar)
        self.content_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        list_layout = QVBoxLayout()
        list_layout.addWidget(self.table, 1)
        list_layout.addWidget(self.empty_state, 1)
        # The workspace has enough horizontal room for the term table and a
        # complete editor, but a scaled 960x560 window is deliberately shorter
        # than the expanded editor plus impact table. Let the detail column own
        # one explicit vertical scroll region; otherwise Qt compresses each
        # nested section into the same pixels and the action rows overpaint the
        # impact heading. Compact Quick Access keeps the direct layout and
        # relies on its smaller set of visible controls.
        self.detail_content = QWidget()
        # A QScrollArea viewport is deliberately transparent. The canvas behind
        # the inset card shadow must still be painted, including when Qt only
        # repaints the child after the sidebar changes from compact to expanded.
        # Reuse the existing page-canvas token rather than layering in a local
        # stylesheet; the same widget also lives directly in compact mode.
        self.detail_content.setObjectName("pageCanvas")
        detail_layout = QVBoxLayout(self.detail_content)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(editor_card)
        detail_layout.addWidget(self.impact_heading)
        detail_layout.addWidget(self.impact_table, 1)
        detail_layout.addWidget(self.impact_actions)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("termsDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.viewport().setAutoFillBackground(False)
        self.detail_scroll.setWidget(self.detail_content)
        self.detail_direct_page = QWidget()
        self.detail_direct_layout = QVBoxLayout(self.detail_direct_page)
        self.detail_direct_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_scroll_page = QWidget()
        scroll_page_layout = QVBoxLayout(self.detail_scroll_page)
        scroll_page_layout.setContentsMargins(0, 0, 0, 0)
        scroll_page_layout.addWidget(self.detail_scroll)
        self.detail_host = QStackedWidget()
        self.detail_host.addWidget(self.detail_direct_page)
        self.detail_host.addWidget(self.detail_scroll_page)
        self.detail_host.setCurrentWidget(self.detail_scroll_page)
        self._detail_in_scroll = True
        self.content_layout.addLayout(list_layout, 3)
        self.content_layout.addWidget(self.detail_host, 2)
        layout.addLayout(self.content_layout, 1)
        layout.addLayout(gate_actions)
        quick_actions = components.FlowRow(spacing=6)
        quick_actions.addWidget(self.locate_button)
        quick_actions.addWidget(self.workspace_button)
        quick_actions.addWidget(self.conflicts_button)
        layout.addLayout(quick_actions)
        layout.addWidget(self.duplicates_button)
        layout.addWidget(self.hint)
        layout.addStretch(0)
        self.workspace_button.hide()
        self._sync_details()
        self._sync()
        QTimer.singleShot(0, self.editor_card, self.editor_card.sync_minimum_height)

    #: 窄栏里收起的列：次数、备注（备注在下面的表单里照样能看能改）。
    COMPACT_HIDDEN = (3, 4, 5)

    def set_compact(self, compact: bool) -> None:
        """放进翻译页左栏时：表格只留源术语、译名、状态三列。"""
        self._compact = compact
        if compact and self._detail_in_scroll:
            self.detail_scroll.takeWidget()
            self.detail_direct_layout.addWidget(self.detail_content)
            self.detail_host.setCurrentWidget(self.detail_direct_page)
            self.detail_host.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            self._detail_in_scroll = False
        elif not compact and not self._detail_in_scroll:
            self.detail_direct_layout.removeWidget(self.detail_content)
            self.detail_scroll.setWidget(self.detail_content)
            self.detail_host.setCurrentWidget(self.detail_scroll_page)
            self.detail_host.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self._detail_in_scroll = True
        self.content_layout.setDirection(QBoxLayout.Direction.TopToBottom if compact
                                         else QBoxLayout.Direction.LeftToRight)
        # In the sidebar, the compact editor keeps its natural height and the
        # list takes the remaining space. Splitting both at a fixed 3:2 ratio
        # cut fields and buttons when the workbench was short.
        self.content_layout.setStretch(0, 1 if compact else 3)
        self.content_layout.setStretch(1, 0 if compact else 2)
        header = self.table.horizontalHeader()
        for column in range(self.table.columnCount()):
            # The workspace must keep all six required columns visible under
            # display scaling. Stretch distributes the available viewport
            # instead of retaining a 512 px fixed-width floor that produces a
            # horizontal scrollbar at 150% scaling.
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.filter_box.setVisible(not compact)
        self.conflict_box.setVisible(not compact)
        self.details_button.setVisible(not compact)
        self._sync_details()
        QTimer.singleShot(0, self.editor_card, self.editor_card.sync_minimum_height)
        self.workspace_button.setVisible(compact)
        # The compact table already carries per-term conflict counts. Keep the
        # detailed conflict queue in the full workspace so the sidebar has
        # room for its high-frequency edit and navigation actions.
        self.conflicts_button.setVisible(not compact)
        self.duplicates_button.setVisible(not compact)
        self.impact_heading.setVisible(not compact)
        self.impact_table.setVisible(not compact)
        self.impact_actions.setVisible(not compact)
        for column in self.COMPACT_HIDDEN:
            self.table.setColumnHidden(column, compact)
        self.form_layout.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows if compact
            else QFormLayout.RowWrapPolicy.DontWrapRows)
        # At 150% display scaling a 680 px initial window can end up only
        # ~523 logical px tall. 100 px forced adjacent vertical sections to
        # overlap. Keep the table independently scrollable while reserving
        # enough room for one data row under its header.
        self.table.setMinimumHeight(60 if compact else 0)
        self._fill_table()
        self._schedule_impact()
        self._sync()

    def _sync_details(self, *_args) -> None:
        visible = not self._compact and self.details_button.isChecked()
        for field in (self.source_edit, self.status_box, self.domain_edit,
                      self.forbidden_edit, self.note_edit):
            field.setVisible(visible)
            label = self.form_layout.labelForField(field)
            if label:
                label.setVisible(visible)
        # Hiding rows changes QFormLayout.heightForWidth() without resizing the
        # card immediately. Wait for Qt to settle, then give wrapped buttons
        # their real minimum height in both the sidebar and full workspace.
        QTimer.singleShot(0, self.editor_card, self.editor_card.sync_minimum_height)

    def set_pairs(self, pairs: list) -> None:
        # Copy only fields used by the worker; it never accesses live Qt state.
        self._pairs = [{"source": p.get("source"), "target": p.get("target"),
                        "excluded": p.get("excluded")} for p in pairs]
        self._schedule_impact()

    def set_current_source(self, source: str) -> None:
        source = str(source or '')
        if source != self._segment_source:
            self._segment_source = source
            if self.current_segment_box.isChecked():
                self._fill_table()

    def set_review_items(self, items: list) -> None:
        """Use the review queue's current findings, not a second QA count."""
        terms = [item for item in items if item.get('kind') == 'finding'
                 and item.get('category') == 'terminology_consistency']
        self._review_items = terms
        counts = self._conflict_counts_for(terms)
        changed = counts != self._conflict_counts
        self._conflict_counts = counts
        self.conflicts_button.setText(i18n.tr('已记录的术语审校问题 · {v0}', v0=len(terms)))
        if changed and (self.conflict_box.isChecked() or self._compact):
            self._fill_table()
        elif changed:
            sorting = self.table.isSortingEnabled()
            self.table.setSortingEnabled(False)
            for position in range(self.table.rowCount()):
                cell = self.table.item(position, 0)
                count = self.table.item(position, 4)
                if cell is not None and count is not None:
                    row = self._rows[int(cell.data(Qt.ItemDataRole.UserRole))]
                    count.setData(Qt.ItemDataRole.DisplayRole,
                                  counts.get(str(row.get('id') or ''), 0))
            self.table.setSortingEnabled(sorting)

    def _conflict_counts_for(self, terms: list) -> dict:
        counts = {}
        for item in terms:
            ids = set(item.get('entry_ids') or []) or {str(item.get('entry_id') or '')}
            if not any(ids):
                # Glossary staleness is segment-scoped and therefore has no
                # entry ID. Attribute it by matching the segment source against
                # the same rows shown in the table so the count cannot silently
                # disappear from the term column.
                item_source = str(item.get('source') or '')
                ids = {str(row.get('id') or '') for row in self._rows
                       if item_source and entry_matches(row, item_source)}
                ids.discard('')
            for entry_id in ids:
                if entry_id:
                    counts[entry_id] = counts.get(entry_id, 0) + 1
        return counts

    def _find_duplicates(self) -> None:
        self.duplicates_button.setEnabled(False)
        self.duplicates_button.setText(i18n.tr('正在检查潜在重复…'))
        snapshot = [dict(row) for row in self._rows]
        job_id = self._job_id

        def done(candidates):
            self.duplicates_button.setEnabled(True)
            self.duplicates_button.setText(i18n.tr('检查潜在重复'))
            if job_id != self._job_id or snapshot != self._rows:
                self.hint.setText(i18n.tr('术语已变化，请重新检查潜在重复。'))
                return
            self._show_duplicates(candidates)

        def failed(message, _detail):
            self.duplicates_button.setEnabled(True)
            self.duplicates_button.setText(i18n.tr('检查潜在重复'))
            self.hint.setText(i18n.tr('无法检查潜在重复：{v0}', v0=message))

        self._impact_runner.submit(terminology_duplicates, snapshot, on_done=done, on_error=failed)

    def _show_duplicates(self, candidates: list) -> None:
        dialog = QDialog(self.window())
        dialog.setWindowTitle(i18n.tr('潜在重复术语'))
        dialog.resize(850, 500)
        layout = QVBoxLayout(dialog)
        label = QLabel(i18n.tr('找到 {v0} 组候选。确定性归一仍不代表含义相同；推测相似仅供人工审查。',
                              v0=len(candidates)))
        label.setWordWrap(True)
        layout.addWidget(label)
        table = QTableWidget(len(candidates), 5)
        table.setHorizontalHeaderLabels([i18n.tr('类型'), i18n.tr('源术语'), i18n.tr('首选译名'),
                                         i18n.tr('候选源术语'), i18n.tr('候选译名')])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, candidate in enumerate(candidates):
            values = (i18n.tr('确定性归一') if candidate['kind'] == 'normalized'
                      else i18n.tr('推测相似（需确认）'),
                      candidate['left_source'], candidate['left_target'],
                      candidate['right_source'], candidate['right_target'])
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table, 1)
        merge = QPushButton(i18n.tr('合并所选：保留左侧术语'))
        merge.setEnabled(bool(candidates) and not self.is_dirty())
        layout.addWidget(merge)
        merge_right = QPushButton(i18n.tr('合并所选：保留右侧术语'))
        merge_right.setEnabled(bool(candidates) and not self.is_dirty())
        layout.addWidget(merge_right)
        def confirm_merge(keep_right=False):
            index = table.currentRow()
            if not 0 <= index < len(candidates):
                return
            candidate = candidates[index]
            keep = next((r for r in self._rows if r.get('id') == candidate['left_id']), None)
            remove = next((r for r in self._rows if r.get('id') == candidate['right_id']), None)
            if keep_right:
                keep, remove = remove, keep
            if not keep or not remove:
                return
            message = i18n.tr('保留「{v0} → {v1}」的状态和译名，把「{v2} → {v3}」作为源形式并保留原记录。仅在确认两者含义相同时合并。',
                              v0=keep['source'], v1=keep['preferred'],
                              v2=remove['source'], v3=remove['preferred'])
            if QMessageBox.question(dialog, i18n.tr('确认合并术语'), message,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            fields = ('id', 'source', 'source_aliases', 'preferred', 'status', 'domain', 'scope', 'behavior', 'forbidden')
            expected = [{key: row.get(key) for key in fields} for row in (keep, remove)]
            dialog.accept()
            self.mergeRequested.emit(str(keep['id']), str(remove['id']), expected)
        merge.clicked.connect(lambda: confirm_merge(False))
        merge_right.clicked.connect(lambda: confirm_merge(True))
        if candidates:
            table.selectRow(0)
        close = QPushButton(i18n.tr('关闭'))
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def _schedule_impact(self) -> None:
        self._impact_generation += 1
        self._impact_timer.stop()
        if not self._compact and self._current() >= 0:
            self.impact_heading.setText(i18n.tr('正在计算影响范围…'))
            self.impact_table.setRowCount(0)
            self._impact_timer.start(100)

    def _compute_impact(self) -> None:
        position = self._current()
        if self._compact or position < 0:
            return
        row = self._rows[position]
        generation = self._impact_generation
        self._impact_runner.submit(term_impact, {'source': row.get('source'),
                                                'source_aliases': row.get('source_aliases') or []},
                                   str(row.get('preferred') or ''), self._pairs,
                                   on_done=lambda result: self._show_impact(generation, result),
                                   on_error=lambda message, _detail: self._impact_error(generation, message))

    def _impact_error(self, generation: int, message: str) -> None:
        if generation == self._impact_generation:
            self.impact_heading.setText(i18n.tr('无法计算影响范围：{v0}', v0=message))

    def _show_impact(self, generation: int, result: dict) -> None:
        if generation != self._impact_generation or self._compact:
            return
        self.impact_heading.setText(i18n.tr(
            '相关段落 {v0} · 已采用 {v1} · 不一致 {v2} · 未翻译 {v3}（双击定位）',
            v0=result['total'], v1=result['correct'], v2=result['inconsistent'],
            v3=result['untranslated']))
        # Bound widget creation on book-sized projects; the summary covers all hits.
        segments = result['segments'][:200]
        self.impact_table.setRowCount(len(segments))
        for row, segment in enumerate(segments):
            for column, value in enumerate((str(segment['index'] + 1), segment['source'], segment['target'])):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, segment['index'])
                self.impact_table.setItem(row, column, item)
        if result['total'] > len(segments):
            self.impact_heading.setText(self.impact_heading.text() + i18n.tr(' · 仅显示前 200 段'))

    def _open_impact_segment(self, row: int, _column: int) -> None:
        item = self.impact_table.item(row, 0)
        if item is not None:
            self.segmentRequested.emit(int(item.data(Qt.ItemDataRole.UserRole)))

    def _locate_current(self) -> None:
        position = self._current()
        if position >= 0:
            row = self._rows[position]
            self.locateRequested.emit({'source': row.get('source'), 'target': row.get('preferred'),
                                       'source_aliases': row.get('source_aliases') or [],
                                       'forbidden': row.get('forbidden') or []})

    def _request_replacement(self, selected_only: bool) -> None:
        position = self._current()
        if position < 0:
            return
        indexes = None
        if selected_only:
            selected = self.impact_table.currentRow()
            item = self.impact_table.item(selected, 0) if selected >= 0 else None
            if item is None:
                self.impact_heading.setText(i18n.tr('请先选择一个相关段落。'))
                return
            indexes = [int(item.data(Qt.ItemDataRole.UserRole))]
        old, ok = QInputDialog.getText(self, i18n.tr('替换原有译名'),
                                       i18n.tr('请输入译文中需要替换的原有译名：'))
        if ok and old.strip():
            self.replacementRequested.emit(str(self._rows[position].get('id') or ''), old.strip(), indexes)

    def _start_adding(self) -> None:
        self._adding = True
        self.details_button.setChecked(True)
        if self._compact:
            self.workspaceRequested.emit()
        self._sync()
        self.source_edit.setFocus()

    # ================= 数据 =================
    def apply(self, job_id: str, view: dict, *, force: bool = False,
              busy: bool = False) -> None:
        """刷新。用户有暂存修改时不覆盖（除非 ``force``，例如落盘之后）。"""
        switched = job_id != self._job_id
        if switched:
            self._adding = False
            self._form_drafts.clear()
            self._form_key = None
            self._form_baseline = None
        self._message = ""
        self._job_id = job_id
        self._view = view or {}
        self._busy = busy
        if force or switched or not self._dirty:
            self._rows = [dict(row) for row in self._view.get("rows") or []]
            self._conflict_counts = self._conflict_counts_for(self._review_items)
            self.conflicts_button.setText(i18n.tr(
                '已记录的术语审校问题 · {v0}', v0=len(self._review_items)))
            self._dirty = False
            self._fill_table()
        else:
            self._refresh_counts()
        self._sync()

    def rows(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    def is_dirty(self) -> bool:
        return self._dirty or bool(self._form_drafts)

    def mark_clean(self) -> None:
        self._dirty = False

    def discard_unsaved(self) -> None:
        self._form_drafts.clear()
        self._dirty = False
        self.apply(self._job_id, self._view, force=True)

    def _visible(self) -> list[int]:
        """筛选后可见的行（指向 ``self._rows`` 的位置）。"""
        query = self.search_edit.text().strip().casefold()
        status = str(self.filter_box.currentData() or "")
        conflicts = self.conflict_box.isChecked()
        current_only = self.current_segment_box.isChecked()
        return [index for index, row in enumerate(self._rows)
                if (not query or query in f"{row.get('source')}\n{row.get('preferred')}\n{' '.join(row.get('source_aliases') or [])}".casefold())
                and (not status or row.get("status") == status)
                and (not current_only or entry_matches(row, self._segment_source))
                and (not conflicts or self._conflict_counts.get(str(row.get('id') or '')))]

    def _fill_table(self, *_args) -> None:
        selected = self._current()
        self._shown = self._visible()
        self.table.blockSignals(True)
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._shown))
        for position, index in enumerate(self._shown):
            row = self._rows[index]
            status_labels = COMPACT_STATUS_LABELS if self._compact else STATUS_LABELS
            status = i18n.tr(status_labels.get(row.get("status"), "暂定"))
            conflict_count = self._conflict_counts.get(str(row.get('id') or ''), 0)
            if self._compact and conflict_count:
                status += i18n.tr(' · 冲突 {v0}', v0=conflict_count)
            values = (row.get("source"), row.get("preferred") or "—",
                      status,
                      str(row.get("occurrences", 0)),
                      conflict_count,
                      str(row.get('modified_at') or '')[:10] or '—')
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, index)
                if column == 3:
                    item.setData(Qt.ItemDataRole.DisplayRole, int(row.get('occurrences') or 0))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif column == 4:
                    item.setData(Qt.ItemDataRole.DisplayRole, int(value))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif column == 5 and row.get('modified_at'):
                    item.setToolTip(str(row['modified_at']))
                elif column in (0, 1, 2, 4):
                    item.setToolTip(str(value))
                self.table.setItem(position, column, item)
        self.table.setSortingEnabled(sorting)
        self.table.blockSignals(False)
        if self._shown:
            target = next((row for row in range(self.table.rowCount())
                           if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == selected), 0)
            self.table.selectRow(target)
        self._load_form()

    def _refresh_counts(self) -> None:
        fresh = {row["id"]: row for row in self._view.get("rows") or []}
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        for position in range(self.table.rowCount()):
            index = self.table.item(position, 0).data(Qt.ItemDataRole.UserRole)
            row = self._rows[index]
            known = fresh.get(row.get("id"))
            if known is not None:
                row["occurrences"] = known.get("occurrences", 0)
                item = self.table.item(position, 3)
                if item is not None:
                    item.setData(Qt.ItemDataRole.DisplayRole, int(row["occurrences"]))
        self.table.setSortingEnabled(sorting)

    def apply_counts(self, view: dict) -> None:
        """Refresh derived counts without overwriting an in-progress form edit."""
        self._view = view
        self._refresh_counts()

    def _current(self) -> int:
        """当前行在 ``self._rows`` 里的位置（表格只显示筛选后的子集）。"""
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else -1

    def _selected(self) -> list[int]:
        rows = sorted({item.row() for item in self.table.selectedItems()})
        return [int(self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)) for row in rows]

    def _set_selected_status(self, status: str) -> None:
        """锁定 / 拒绝所选：开始前改暂存行，开始后一次生成一个新的术语版本。"""
        selected = self._selected()
        if not selected:
            return
        if self._staged():
            for index in selected:
                self._rows[index]["status"] = status
            self._dirty = True
            self._fill_table()
            self._sync()
        else:
            self.statusRequested.emit(
                [str(self._rows[index].get("id")) for index in selected
                 if self._rows[index].get("id")], status)

    def _load_form(self) -> None:
        position = self._current()
        row = self._rows[position] if position >= 0 else {}
        self._form_key = self._key_for_row(row) if row else None
        self._form_baseline = self._fields_for_row(row) if row else None
        fields = self._form_drafts.get(self._form_key, self._form_baseline) or {}
        self._loading_form = True
        for edit, key in ((self.source_edit, "source"), (self.preferred_edit, "preferred"),
                          (self.domain_edit, "domain"), (self.note_edit, "note")):
            edit.setText(str(fields.get(key) or ""))
        self.forbidden_edit.setText("、".join(str(item) for item in fields.get("forbidden") or []))
        index = self.status_box.findData(fields.get("status") or "provisional")
        self.status_box.setCurrentIndex(max(index, 0))
        self._loading_form = False
        self._schedule_impact()
        self._sync()

    @staticmethod
    def _key_for_row(row: dict):
        return ('id', str(row['id'])) if row.get('id') else ('local', id(row))

    @staticmethod
    def _fields_for_row(row: dict) -> dict:
        return {'source': str(row.get('source') or '').strip(),
                'preferred': str(row.get('preferred') or '').strip(),
                'status': str(row.get('status') or 'provisional'),
                'domain': str(row.get('domain') or '').strip(),
                'note': str(row.get('note') or '').strip(),
                'forbidden': list(row.get('forbidden') or [])}

    def _form_changed(self, *_args) -> None:
        if self._loading_form or self._form_key is None or self._form_baseline is None:
            return
        fields = self._form_fields()
        if fields == self._form_baseline:
            self._form_drafts.pop(self._form_key, None)
        else:
            self._form_drafts[self._form_key] = fields
        self._sync()

    def _undo_form(self) -> None:
        self._form_drafts.pop(self._form_key, None)
        self._load_form()

    def mark_form_saved(self, entry_id: str) -> None:
        self._form_drafts.pop(('id', str(entry_id)), None)
        if self._form_key == ('id', str(entry_id)):
            self._load_form()

    def _save_staged(self, freeze: bool) -> None:
        for row in self._rows:
            fields = self._form_drafts.get(self._key_for_row(row))
            if fields:
                row.update(fields)
        self._form_drafts.clear()
        self._dirty = True
        (self.freezeRequested if freeze else self.draftSaveRequested).emit(self.rows())

    def _form_fields(self) -> dict:
        return {"source": self.source_edit.text().strip(),
                "preferred": self.preferred_edit.text().strip(),
                "status": str(self.status_box.currentData() or "provisional"),
                "domain": self.domain_edit.text().strip(),
                "note": self.note_edit.text().strip(),
                "forbidden": [text.strip() for text in
                              re.split(r"[,，;；、\n]", self.forbidden_edit.text())
                              if text.strip()]}

    # ================= 编辑 =================
    def _staged(self) -> bool:
        return not self._view.get("started")

    def _apply_row(self) -> None:
        position = self._current()
        if position < 0:
            return
        fields = self._form_fields()
        if not fields["source"]:
            return
        if not self._staged() and fields['source'] != str(self._rows[position].get('source') or ''):
            self._message = i18n.tr('已开始翻译的源术语不能直接改名；请新增术语或恢复原文。')
            self._sync()
            return
        if self._staged():
            self._rows[position].update(fields)
            self._form_drafts.pop(self._form_key, None)
            self._dirty = True
            self._fill_table()
            self._sync()
        else:
            self.entryUpdateRequested.emit(str(self._rows[position].get("id")), fields)

    def _add_row(self) -> None:
        if not self.details_button.isChecked():
            self.details_button.setChecked(True)
            self.source_edit.setFocus()
            self._message = i18n.tr('填写新术语后再次点击添加。')
            self._sync()
            return
        self._message = ""
        fields = self._form_fields()
        if not fields["source"] or not fields["preferred"]:
            self._message = i18n.tr('新增术语需要同时填写「源术语」和「首选译名」。')
            self._sync()
            return
        if self._staged():
            self._rows.append({"id": "", "occurrences": 0, **fields})
            self._dirty = True
            self._fill_table()
            for row in range(self.table.rowCount()):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == len(self._rows) - 1:
                    self.table.selectRow(row)
                    break
            self._sync()
        else:
            self.entryAddRequested.emit(fields)

    def _delete_row(self) -> None:
        position = self._current()
        if position < 0:
            return
        if self._staged():
            self._form_drafts.pop(self._key_for_row(self._rows[position]), None)
            del self._rows[position]
            self._dirty = True
            self._fill_table()
            self._sync()
        else:
            self.entryDeleteRequested.emit(str(self._rows[position].get("id")))

    def _lock_candidates(self) -> None:
        for row in self._rows:
            if row.get("status") in ("candidate", "provisional"):
                row["status"] = "locked"
        self._dirty = True
        self._fill_table()
        self._sync()

    # ================= 状态 =================
    def _banner(self, text: str, tone: str) -> None:
        self.banner.set_tone(tone)
        self.banner.set_text(text)

    def _term_status_text(self, *, staged: bool, pending: int) -> tuple[str, str]:
        """Return (short sidebar suffix, full notice) for the current term state."""
        view = self._view
        if view.get("frozen"):
            return (
                i18n.tr('v{v0} 已冻结', v0=view.get("frozen_version")),
                i18n.tr('术语版本 v{v0} 已冻结；仍可人工修改',
                        v0=view.get("frozen_version")),
            )
        if view.get("bypassed"):
            return (
                i18n.tr('使用暂定术语'),
                i18n.tr('本任务跳过了人工冻结，当前使用暂定术语。'),
            )
        if view.get("blocks_start"):
            return (
                i18n.tr('待确认 {v0}', v0=view.get("pending_count", pending)),
                i18n.tr(
                    '严格术语治理：还有 {v0} 个候选术语未确认。冻结术语表后才能开始翻译。',
                    v0=view.get("pending_count", pending)),
            )
        if not self._rows:
            return "", i18n.tr('这个任务还没有术语。')
        if not staged:
            return (
                i18n.tr('修改需重新确认相关审校'),
                i18n.tr('翻译开始后修改术语，受影响的译文和审校结果需要重新确认。'),
            )
        return (
            i18n.tr('保存草稿后生效'),
            i18n.tr('翻译开始前可以暂存修改；点「保存草稿」或「冻结」才会写入任务。'),
        )

    def _sync(self) -> None:
        view = self._view
        staged = self._staged()
        total = len(self._rows)
        pending = sum(1 for row in self._rows if row.get("status") == "candidate")
        status_suffix, notice = self._term_status_text(staged=staged, pending=pending)
        heading = i18n.tr('{v0} 个术语', v0=total)
        if pending and not view.get("blocks_start"):
            heading += i18n.tr(' · {v0} 个候选待确认', v0=pending)
        if self._compact and total and status_suffix:
            heading += f" · {status_suffix}"
        self.heading.setText(heading)
        self.heading.setToolTip(notice)
        tone = "ok" if view.get("frozen") else (
            "warn" if view.get("blocks_start") else "info")
        self._banner(notice, tone)
        empty = total == 0 and not view.get("frozen") and not view.get("blocks_start")
        if total:
            self._adding = False
        # 空：只有空状态；点了「添加术语…」：只有新建表单。表格、筛选与提示条
        # 在有第一条术语之后才出现。
        self.empty_state.setVisible(empty and not self._adding)
        for widget in (self.heading, self.filter_bar, self.table):
            widget.setVisible(not empty)
        # The compact sidebar has no room for a multi-line notice. Its essential
        # state is in the heading; the complete workspace keeps the full notice.
        self.banner.setVisible(not empty and not self._compact)
        self.editor_card.setVisible(not empty or self._adding)
        position = self._current()
        has_row = position >= 0
        editable = not self._busy
        # 作用于选中行的操作：没选中就不出现（隐藏优于禁用，审校文档第五节第 4 条）。
        self.apply_row_button.setText(i18n.tr('保存译名') if self._compact else i18n.tr('应用到所选术语'))
        self.apply_row_button.setVisible(has_row)
        self.undo_form_button.setVisible(has_row and self._form_key in self._form_drafts)
        self.locate_button.setEnabled(has_row)
        self.apply_row_button.setEnabled(editable)
        self.delete_button.setVisible(has_row and not self._compact)
        self.delete_button.setEnabled(editable)
        selected = len(self._selected())
        self.lock_selected_button.setVisible(selected > 0 and not self._compact)
        self.lock_selected_button.setEnabled(editable)
        self.reject_selected_button.setVisible(selected > 0 and not self._compact)
        self.reject_selected_button.setEnabled(editable)
        self.add_button.setVisible(not self._compact)
        self.add_button.setEnabled(editable)
        self.lock_all_button.setVisible(staged and total > 0 and not self._compact)
        self.lock_all_button.setEnabled(editable)
        self.save_button.setVisible(staged and (self._dirty or total > 0))
        self.save_button.setEnabled(editable)
        self.freeze_button.setVisible(staged and total > 0 and not self._compact)
        self.freeze_button.setEnabled(editable)
        self.bypass_button.setVisible(
            not self._compact and staged and bool(view.get("quality_mode")) and not view.get("frozen")
            and not view.get("bypassed"))
        self.bypass_button.setEnabled(editable)
        self.unfreeze_button.setVisible(not self._compact and staged and bool(view.get("frozen")))
        self.unfreeze_button.setEnabled(editable)
        if self._message:
            self.hint.setText(self._message)
        elif self._dirty or self._form_drafts:
            self.hint.setText(i18n.tr('有尚未保存的术语修改。'))
        else:
            self.hint.setText("")
        # Button visibility depends on selection, dirty state and translation
        # stage, all of which can change the wrapped card height here.
        QTimer.singleShot(0, self.editor_card, self.editor_card.sync_minimum_height)
