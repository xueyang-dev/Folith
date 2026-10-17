"""术语页：审核候选术语、锁定、冻结，翻译开始后逐条修订。

旧界面的"术语"工作区在 Qt 里原来没有，而严格术语治理的任务会在候选术语未冻结时
被核心拒绝开始翻译——没有这个面板，这类任务（包括接入的旧任务）就没有出口。

只做展示与发信号；真正写状态的是窗口层调用的适配器（沿用核心的术语版本机制）：

* 翻译开始前：面板里**暂存**修改，「保存草稿 / 冻结并继续 / 跳过冻结」才落盘；
* 翻译开始后：每次修订立即走核心的逐条接口，由核心生成新的术语版本。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFormLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import components
from . import i18n
from . import icons
from . import style as style_module
from .style import Card

STATUS_ORDER = ("candidate", "provisional", "locked", "rejected")
STATUS_LABELS = {"candidate": "候选", "provisional": "暂定",
                 "locked": "已锁定", "rejected": "已拒绝"}
COLUMNS = ("源术语", "首选译名", "状态", "次数", "备注")


class TermsPanel(QWidget):
    """术语表编辑器。"""

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
        self._busy = False
        self._message = ""
        self._shown: list[int] = []
        #: 术语为 0 时点了「添加术语…」：只露出新建表单，不露空表格和筛选。
        self._adding = False

        def button(text: str, icon_name: str, token: str = "text-secondary",
                   name: str = "") -> QPushButton:
            widget = QPushButton(text)
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
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
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
        self.save_button.clicked.connect(lambda: self.draftSaveRequested.emit(self.rows()))
        self.freeze_button = button(i18n.tr('冻结并继续翻译'), "check-circle",
                                    "text-on-accent", "primary")
        self.freeze_button.clicked.connect(lambda: self.freezeRequested.emit(self.rows()))
        self.bypass_button = button(i18n.tr('跳过冻结，使用暂定术语'), "arrow-right")
        self.bypass_button.clicked.connect(self.bypassRequested.emit)
        self.unfreeze_button = button(i18n.tr('解除冻结'), "refresh")
        self.unfreeze_button.clicked.connect(self.unfreezeRequested.emit)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")

        # 按钮行放不下就折行：术语表在翻译页左栏里展开，只有约 400px 宽。
        edit_actions = components.FlowRow(spacing=6)
        for widget in (self.apply_row_button, self.add_button, self.delete_button,
                       self.lock_selected_button, self.reject_selected_button,
                       self.lock_all_button):
            edit_actions.addWidget(widget)
        gate_actions = components.FlowRow(spacing=6)
        for widget in (self.freeze_button, self.save_button, self.bypass_button,
                       self.unfreeze_button):
            gate_actions.addWidget(widget)

        editor_card = Card()
        editor_layout = QVBoxLayout(editor_card)
        editor_layout.setContentsMargins(16, 14, 16, 14)
        editor_layout.setSpacing(10)
        editor_layout.addLayout(form)
        editor_layout.addLayout(edit_actions)

        # 术语为 0 时整页是一个空状态（设计审校 40 号 6.2：原来提示条、"0 个术语"、
        # 空表格、整张新建表单和四个禁用按钮把同一个"空"说了四遍）。
        self.empty_state = style_module.EmptyState(
            icon="termbase", page=True, title=i18n.tr('这个任务还没有术语'),
            hint=i18n.tr('添加一条术语，翻译会按它统一译名。'),
            action=i18n.tr('添加术语…'))
        self.empty_state.actionTriggered.connect(self._start_adding)
        self.editor_card = editor_card
        self.filter_bar = QWidget()
        filters = components.FlowRow(self.filter_bar, spacing=6)
        self.search_edit.setMinimumWidth(160)
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filters.addWidget(self.search_edit)
        filters.addWidget(self.filter_box)
        filters.addWidget(self.conflict_box)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.banner)
        layout.addWidget(self.heading)
        layout.addWidget(self.filter_bar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty_state, 1)
        layout.addWidget(editor_card)
        layout.addLayout(gate_actions)
        layout.addWidget(self.hint)
        layout.addStretch(0)
        self._sync()

    #: 窄栏里收起的列：次数、备注（备注在下面的表单里照样能看能改）。
    COMPACT_HIDDEN = (3, 4)

    def set_compact(self, compact: bool) -> None:
        """放进翻译页左栏时：表格只留源术语、译名、状态三列。"""
        for column in self.COMPACT_HIDDEN:
            self.table.setColumnHidden(column, compact)
        self.form_layout.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapLongRows if compact
            else QFormLayout.RowWrapPolicy.DontWrapRows)
        # 窄栏里表格至少露出几行，不被下面的表单挤成一条缝（整栏上下滚动）。
        self.table.setMinimumHeight(200 if compact else 0)

    def _start_adding(self) -> None:
        self._adding = True
        self._sync()
        self.source_edit.setFocus()

    # ================= 数据 =================
    def apply(self, job_id: str, view: dict, *, force: bool = False,
              busy: bool = False) -> None:
        """刷新。用户有暂存修改时不覆盖（除非 ``force``，例如落盘之后）。"""
        switched = job_id != self._job_id
        if switched:
            self._adding = False
        self._message = ""
        self._job_id = job_id
        self._view = view or {}
        self._busy = busy
        if force or switched or not self._dirty:
            self._rows = [dict(row) for row in self._view.get("rows") or []]
            self._dirty = False
            self._fill_table()
        else:
            self._refresh_counts()
        self._sync()

    def rows(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    def is_dirty(self) -> bool:
        return self._dirty

    def mark_clean(self) -> None:
        self._dirty = False

    def _visible(self) -> list[int]:
        """筛选后可见的行（指向 ``self._rows`` 的位置）。"""
        query = self.search_edit.text().strip().casefold()
        status = str(self.filter_box.currentData() or "")
        conflicts = self.conflict_box.isChecked()
        return [index for index, row in enumerate(self._rows)
                if (not query or query in f"{row.get('source')}\n{row.get('preferred')}".casefold())
                and (not status or row.get("status") == status)
                and (not conflicts or row.get("conflict"))]

    def _fill_table(self, *_args) -> None:
        selected = self._current()
        self._shown = self._visible()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._shown))
        for position, row in enumerate(self._rows[index] for index in self._shown):
            values = (row.get("source"), row.get("preferred") or "—",
                      i18n.tr(STATUS_LABELS.get(row.get("status"), "暂定")),
                      str(row.get("occurrences", 0)), row.get("note") or "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(position, column, item)
        self.table.blockSignals(False)
        if self._shown:
            target = self._shown.index(selected) if selected in self._shown else 0
            self.table.selectRow(target)
        self._load_form()

    def _refresh_counts(self) -> None:
        fresh = {row["id"]: row for row in self._view.get("rows") or []}
        for position, index in enumerate(getattr(self, "_shown", [])):
            row = self._rows[index]
            known = fresh.get(row.get("id"))
            if known is not None:
                row["occurrences"] = known.get("occurrences", 0)
                item = self.table.item(position, 3)
                if item is not None:
                    item.setText(str(row["occurrences"]))

    def _current(self) -> int:
        """当前行在 ``self._rows`` 里的位置（表格只显示筛选后的子集）。"""
        shown = getattr(self, "_shown", [])
        row = self.table.currentRow()
        return shown[row] if 0 <= row < len(shown) else -1

    def _selected(self) -> list[int]:
        shown = getattr(self, "_shown", [])
        rows = sorted({item.row() for item in self.table.selectedItems()})
        return [shown[row] for row in rows if 0 <= row < len(shown)]

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
        for edit, key in ((self.source_edit, "source"), (self.preferred_edit, "preferred"),
                          (self.domain_edit, "domain"), (self.note_edit, "note")):
            edit.setText(str(row.get(key) or ""))
        self.forbidden_edit.setText("、".join(str(item) for item in row.get("forbidden") or []))
        index = self.status_box.findData(row.get("status") or "provisional")
        self.status_box.setCurrentIndex(max(index, 0))
        self._sync()

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
        if self._staged():
            self._rows[position].update(fields)
            self._dirty = True
            self._fill_table()
            self._sync()
        else:
            self.entryUpdateRequested.emit(str(self._rows[position].get("id")), fields)

    def _add_row(self) -> None:
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
            if len(self._rows) - 1 in self._shown:
                self.table.selectRow(self._shown.index(len(self._rows) - 1))
            self._sync()
        else:
            self.entryAddRequested.emit(fields)

    def _delete_row(self) -> None:
        position = self._current()
        if position < 0:
            return
        if self._staged():
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

    def _sync(self) -> None:
        view = self._view
        staged = self._staged()
        total = len(self._rows)
        pending = sum(1 for row in self._rows if row.get("status") == "candidate")
        self.heading.setText(i18n.tr('{v0} 个术语', v0=total)
                             + (i18n.tr(' · {v0} 个候选待确认', v0=pending) if pending else ""))
        if view.get("frozen"):
            self._banner(i18n.tr('术语已冻结 · v{v0}', v0=view.get("frozen_version")), "ok")
        elif view.get("bypassed"):
            self._banner(i18n.tr('本任务跳过了人工冻结，当前使用暂定术语。'), "info")
        elif view.get("blocks_start"):
            self._banner(
                i18n.tr('严格术语治理：还有 {v0} 个候选术语未确认。冻结术语表后才能开始翻译。',
                        v0=view.get("pending_count", pending)), "warn")
        elif not total:
            self._banner(i18n.tr('这个任务还没有术语。'), "info")
        else:
            self._banner(
                i18n.tr('翻译开始后修改术语，受影响的译文和审校结果需要重新确认。')
                if not staged else
                i18n.tr('翻译开始前可以暂存修改；点「保存草稿」或「冻结」才会写入任务。'), "info")
        empty = total == 0 and not view.get("frozen") and not view.get("blocks_start")
        if total:
            self._adding = False
        # 空：只有空状态；点了「添加术语…」：只有新建表单。表格、筛选与提示条
        # 在有第一条术语之后才出现。
        self.empty_state.setVisible(empty and not self._adding)
        for widget in (self.banner, self.heading, self.filter_bar, self.table):
            widget.setVisible(not empty)
        self.editor_card.setVisible(not empty or self._adding)
        position = self._current()
        has_row = position >= 0
        editable = not self._busy
        # 作用于选中行的操作：没选中就不出现（隐藏优于禁用，审校文档第五节第 4 条）。
        self.apply_row_button.setVisible(has_row)
        self.apply_row_button.setEnabled(editable)
        self.delete_button.setVisible(has_row)
        self.delete_button.setEnabled(editable)
        selected = len(self._selected())
        self.lock_selected_button.setVisible(selected > 0)
        self.lock_selected_button.setEnabled(editable)
        self.reject_selected_button.setVisible(selected > 0)
        self.reject_selected_button.setEnabled(editable)
        self.add_button.setEnabled(editable)
        self.lock_all_button.setVisible(staged and total > 0)
        self.lock_all_button.setEnabled(editable)
        self.save_button.setVisible(staged and (self._dirty or total > 0))
        self.save_button.setEnabled(editable)
        self.freeze_button.setVisible(staged and total > 0)
        self.freeze_button.setEnabled(editable)
        self.bypass_button.setVisible(
            staged and bool(view.get("quality_mode")) and not view.get("frozen")
            and not view.get("bypassed"))
        self.bypass_button.setEnabled(editable)
        self.unfreeze_button.setVisible(staged and bool(view.get("frozen")))
        self.unfreeze_button.setEnabled(editable)
        if self._message:
            self.hint.setText(self._message)
        elif self._dirty:
            self.hint.setText(i18n.tr('有尚未保存的术语修改。'))
        else:
            self.hint.setText("")
