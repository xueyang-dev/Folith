"""语言资产页：待审核候选 / 术语库 / 翻译记忆。

Agent 在翻译中发现的候选词条（``knowledge_candidates``）需要人来决定：接受为项目术语、
仅用于本任务，或拒绝——这些在 Qt 里原来没有任何入口，候选永远待审。术语库与各项目的
翻译记忆也一并可查。行模型来自 ``folith.language_assets``（与界面框架无关）。

只做展示与发信号；写状态的是窗口层调用的适配器。表格有显示上限（候选可以有几千条），
"显示更多"逐批展开，筛选始终作用于全部数据。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from folith import language_assets as assets

from . import components
from . import i18n
from . import style as style_module

PAGE = 200                                  # 每批显示的行数
KIND_FILTERS = (("all", "全部类型"), ("term", "术语"), ("expression", "固定表达"),
                ("name", "专名"))
QUICK_FILTERS = (("all", "全部"), ("high", "高置信度"), ("conflict", "有冲突"), ("new", "新术语"))


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


def _table(headers: list[str], *, multi: bool = False) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if multi
                           else QAbstractItemView.SelectionMode.SingleSelection)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    return table


class LibraryView(QWidget):
    backRequested = Signal()
    refreshRequested = Signal(str)                  # tm_project_id
    decideRequested = Signal(list, str)             # candidate rows, decision
    openJobRequested = Signal(str, int)             # job_id, segment index (-1 = none)
    newTermRequested = Signal()
    clearMemoryRequested = Signal(str)              # project id
    acceptEditedRequested = Signal(dict)            # candidate row（先改译名再接受）
    termEditRequested = Signal(dict)                # term row（在所在任务里修改）
    termDeleteRequested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: dict = {}
        self._shown: dict[str, list[dict]] = {"candidates": [], "terms": [], "tm": []}
        self._limit = {"candidates": PAGE, "terms": PAGE, "tm": PAGE}
        self._busy = False

        self.title = QLabel(i18n.tr('术语与翻译记忆'))
        self.title.setObjectName("pageTitle")
        self.subtitle = QLabel(i18n.tr('维护项目语言资产，并审核 Agent 发现的候选内容'))
        self.subtitle.setObjectName("hint")
        self.back_button = QPushButton(i18n.tr('返回任务列表'))
        self.back_button.clicked.connect(self.backRequested.emit)
        # 窗口里左侧有共用侧栏（「任务库」就是返回入口），这里不再重复一个返回按钮；
        # 控件保留，单独使用本页时仍可显示。
        self.back_button.setVisible(False)
        self.refresh_button = QPushButton(i18n.tr('刷新'))
        self.refresh_button.clicked.connect(lambda: self.refreshRequested.emit(self.tm_project_id()))
        self.new_term_button = QPushButton(i18n.tr('添加术语…'))
        self.new_term_button.clicked.connect(self.newTermRequested.emit)
        header = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        header.addLayout(text, 1)
        for widget in (self.new_term_button, self.refresh_button, self.back_button):
            header.addWidget(widget, 0, Qt.AlignmentFlag.AlignTop)

        # ---- 待审核 ----------------------------------------------------------
        self.cand_query = QLineEdit()
        self.cand_query.setPlaceholderText(i18n.tr('搜索候选术语、译文或来源……'))
        self.cand_query.setClearButtonEnabled(True)
        self.cand_kind = style_module.ComboBox()
        for value, label in KIND_FILTERS:
            self.cand_kind.addItem(i18n.tr(label), value)
        self.cand_quick = style_module.SegmentedControl(
            [(value, i18n.tr(label)) for value, label in QUICK_FILTERS])
        self.cand_quick.set_value("all")
        for signal in (self.cand_query.textChanged, self.cand_kind.currentIndexChanged,
                       self.cand_quick.changed):
            signal.connect(lambda *_a: self._reset_limit("candidates"))
        self.cand_table = _table([i18n.tr('候选术语'), i18n.tr('建议译文'), i18n.tr('类型'),
                                  i18n.tr('置信度'), i18n.tr('出现'), i18n.tr('来源文档')], multi=True)
        self.cand_table.itemSelectionChanged.connect(self._candidate_selected)
        self.cand_more = QPushButton("")
        self.cand_more.clicked.connect(lambda: self._more("candidates"))
        self.accept_button = QPushButton(i18n.tr('接受为项目术语'))
        style_module.set_button(self.accept_button, "primary")
        self.task_button = QPushButton(i18n.tr('仅用于本任务'))
        self.reject_button = QPushButton(i18n.tr('拒绝'))
        self.jump_button = QPushButton(i18n.tr('打开原文位置'))
        self.edit_accept_button = QPushButton(i18n.tr('改译名后接受…'))
        self.edit_accept_button.setToolTip(i18n.tr('建议译文不对时，先改成正确的译名再接受为项目术语'))
        self.edit_accept_button.clicked.connect(self._edit_accept)
        self.accept_button.clicked.connect(lambda: self._decide("project_term"))
        self.task_button.clicked.connect(lambda: self._decide("task_only"))
        self.reject_button.clicked.connect(lambda: self._decide("rejected"))
        self.jump_button.clicked.connect(self._jump)
        self.high_button = QPushButton("")
        self.high_button.clicked.connect(self._accept_all_high)
        actions = QHBoxLayout()
        for widget in (self.accept_button, self.edit_accept_button, self.task_button,
                       self.reject_button, self.jump_button, self.high_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        self.cand_detail = QTextBrowser()
        self.cand_detail.setObjectName("candidateDetail")
        self.cand_detail.setMaximumHeight(170)
        self.cand_empty = QLabel(i18n.tr('没有待审核的候选词条。'))
        self.cand_empty.setProperty("role", "muted")
        self.cand_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filters = QHBoxLayout()
        filters.addWidget(self.cand_query, 1)
        filters.addWidget(self.cand_kind)
        # 一页的数据整个为空：筛选、表格、详情与动作一起让位给一个空状态
        # （设计审校 40 号第五节）；只是"这个筛选下没有"时仍用表格位置的一行说明。
        self.cand_body = QWidget()
        cand = QVBoxLayout(self.cand_body)
        cand.setContentsMargins(0, 0, 0, 0)
        cand.addLayout(filters)
        cand.addWidget(self.cand_quick)
        # 没有候选时表格隐藏，多出的高度给空状态说明，而不是把筛选分段条拉高。
        self.cand_quick.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        cand.addWidget(self.cand_empty, 1)
        cand.addWidget(self.cand_table, 1)
        cand.addWidget(self.cand_more)
        cand.addWidget(self.cand_detail)
        cand.addLayout(actions)
        self.cand_empty_state = style_module.EmptyState(
            icon="sparkles", page=True, title=i18n.tr('没有待审核的候选词条'),
            hint=i18n.tr('翻译中发现的新术语会出现在这里，由你决定是否纳入项目术语。'))
        cand_page = QWidget()
        cand_outer = QVBoxLayout(cand_page)
        cand_outer.setContentsMargins(0, 8, 0, 0)
        cand_outer.addWidget(self.cand_body, 1)
        cand_outer.addWidget(self.cand_empty_state, 1)

        # ---- 术语库 ----------------------------------------------------------
        self.term_query = QLineEdit()
        self.term_query.setPlaceholderText(i18n.tr('搜索术语、译名、领域或文档……'))
        self.term_query.setClearButtonEnabled(True)
        self.term_query.textChanged.connect(lambda *_a: self._reset_limit("terms"))
        self.term_table = _table([i18n.tr('原术语'), i18n.tr('首选译名'), i18n.tr('状态'),
                                  i18n.tr('领域'), i18n.tr('使用'), i18n.tr('所在')])
        self.term_table.itemSelectionChanged.connect(self._term_selected)
        self.term_more = QPushButton("")
        self.term_more.clicked.connect(lambda: self._more("terms"))
        self.term_detail = QTextBrowser()
        self.term_detail.setMaximumHeight(150)
        self.term_open = QPushButton(i18n.tr('打开所在任务'))
        self.term_open.clicked.connect(self._open_term_job)
        self.term_edit = QPushButton(i18n.tr('编辑…'))
        self.term_edit.clicked.connect(lambda: self.termEditRequested.emit(self.selected_term()))
        self.term_delete = QPushButton(i18n.tr('删除…'))
        style_module.set_button(self.term_delete, "danger")
        self.term_delete.clicked.connect(lambda: self.termDeleteRequested.emit(self.selected_term()))
        self.term_body = QWidget()
        term = QVBoxLayout(self.term_body)
        term.setContentsMargins(0, 0, 0, 0)
        term.addWidget(self.term_query)
        term.addWidget(self.term_table, 1)
        term.addWidget(self.term_more)
        term.addWidget(self.term_detail)
        term_actions = QHBoxLayout()
        for widget in (self.term_edit, self.term_delete, self.term_open):
            term_actions.addWidget(widget)
        term_actions.addStretch(1)
        term.addLayout(term_actions)
        self.term_empty_state = style_module.EmptyState(
            icon="termbase", page=True, title=i18n.tr('术语库是空的'),
            hint=i18n.tr('接受的候选和手动添加的术语会出现在这里。'),
            action=i18n.tr('添加术语…'))
        self.term_empty_state.actionTriggered.connect(self.newTermRequested.emit)
        term_page = QWidget()
        term_outer = QVBoxLayout(term_page)
        term_outer.setContentsMargins(0, 8, 0, 0)
        term_outer.addWidget(self.term_body, 1)
        term_outer.addWidget(self.term_empty_state, 1)

        # ---- 翻译记忆 --------------------------------------------------------
        self.tm_query = QLineEdit()
        self.tm_query.setPlaceholderText(i18n.tr('搜索原文或译文……'))
        self.tm_query.setClearButtonEnabled(True)
        self.tm_query.textChanged.connect(lambda *_a: self._reset_limit("tm"))
        self.tm_project = style_module.ComboBox()
        self.tm_project.currentIndexChanged.connect(self._tm_project_changed)
        self.tm_table = _table([i18n.tr('原文'), i18n.tr('译文'), i18n.tr('目标语言'), i18n.tr('更新')])
        self.tm_more = QPushButton("")
        self.tm_more.clicked.connect(lambda: self._more("tm"))
        self.tm_clear = QPushButton(i18n.tr('清空该项目的翻译记忆…'))
        self.tm_clear.clicked.connect(lambda: self.clearMemoryRequested.emit(self.tm_project_id()))
        self.tm_note = QLabel(i18n.tr('翻译记忆按项目隔离，清空只影响当前查看的项目。'))
        self.tm_note.setObjectName("hint")
        self.tm_note.setWordWrap(True)
        tm_filters = QHBoxLayout()
        tm_filters.addWidget(self.tm_query, 1)
        tm_filters.addWidget(self.tm_project)
        # 翻译记忆按项目隔离：空的时候项目选择仍然留着，好切到别的项目看。
        self.tm_body = QWidget()
        tm_rows = QVBoxLayout(self.tm_body)
        tm_rows.setContentsMargins(0, 0, 0, 0)
        tm_rows.addWidget(self.tm_table, 1)
        tm_rows.addWidget(self.tm_more)
        tm_rows.addWidget(self.tm_note)
        tm_rows.addWidget(self.tm_clear, 0, Qt.AlignmentFlag.AlignLeft)
        self.tm_empty_state = style_module.EmptyState(
            icon="memory", page=True, title=i18n.tr('这个项目还没有翻译记忆'),
            hint=i18n.tr('翻译记忆按项目隔离；可以在上方切换到其他项目。'))
        tm_page = QWidget()
        tm = QVBoxLayout(tm_page)
        tm.setContentsMargins(0, 8, 0, 0)
        tm.addLayout(tm_filters)
        tm.addWidget(self.tm_body, 1)
        tm.addWidget(self.tm_empty_state, 1)

        self.tabs = QTabWidget()
        self.tabs.addTab(cand_page, "")
        self.tabs.addTab(term_page, "")
        self.tabs.addTab(tm_page, "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        self.apply({})

    # ================= 数据 =================
    def tm_project_id(self) -> str:
        return str(self.tm_project.currentData() or "")

    def apply(self, data: dict, *, busy: bool = False) -> None:
        self._data = data or {}
        self._busy = busy
        summary = self._data.get("summary") or {}
        self.tabs.setTabText(0, i18n.tr('待审核 {v0}', v0=summary.get("candidates", 0)))
        self.tabs.setTabText(1, i18n.tr('术语库 {v0}', v0=summary.get("terms", 0)))
        self.tabs.setTabText(2, i18n.tr('翻译记忆 {v0}', v0=summary.get("tm", 0)))
        self.tm_project.blockSignals(True)
        self.tm_project.clear()
        for project in self._data.get("projects") or []:
            self.tm_project.addItem(str(project["name"]), project["project_id"])
        index = self.tm_project.findData(self._data.get("tm_project_id"))
        self.tm_project.setCurrentIndex(max(index, 0))
        self.tm_project.blockSignals(False)
        self.refresh_button.setEnabled(not busy)
        for key in self._limit:
            self._limit[key] = PAGE
        self._fill_candidates()
        self._fill_terms()
        self._fill_tm()

    def _reset_limit(self, which: str) -> None:
        self._limit[which] = PAGE
        {"candidates": self._fill_candidates, "terms": self._fill_terms, "tm": self._fill_tm}[which]()

    def _more(self, which: str) -> None:
        self._limit[which] += PAGE
        {"candidates": self._fill_candidates, "terms": self._fill_terms, "tm": self._fill_tm}[which]()

    def _tm_project_changed(self, _index: int) -> None:
        if self.tm_project_id():
            self.refreshRequested.emit(self.tm_project_id())

    @staticmethod
    def _more_text(button: QPushButton, total: int, shown: int) -> None:
        remaining = total - shown
        button.setVisible(remaining > 0)
        if remaining > 0:
            button.setText(i18n.tr('显示更多（还剩 {v0} 条）', v0=remaining))

    # ================= 待审核 =================
    def _filtered_candidates(self) -> list[dict]:
        quick = self.cand_quick.value() or "all"
        return assets.filter_candidates(
            self._data.get("candidates") or [], query=self.cand_query.text(),
            kind=str(self.cand_kind.currentData() or "all"),
            only_high=quick == "high", only_conflicts=quick == "conflict",
            only_new=quick == "new")

    def _fill_candidates(self) -> None:
        rows = self._filtered_candidates()
        shown = rows[: self._limit["candidates"]]
        self._shown["candidates"] = shown
        self.cand_table.blockSignals(True)
        self.cand_table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            values = (row["source"], row["target"], i18n.tr_dynamic(row["kind_label"]),
                      assets.confidence_text(row), i18n.tr_dynamic(assets.usage_text(row)), row["document"])
            for c, value in enumerate(values):
                self.cand_table.setItem(r, c, QTableWidgetItem(str(value or "")))
            # 置信度一列带状态点：有冲突 = 需处理，高置信度 = 完成，新术语 = 进行中。
            tone = ("attention" if row.get("has_conflict") else
                    "done" if row.get("high_confidence") else
                    "active" if row.get("is_new") else "idle")
            confidence = self.cand_table.item(r, 3)
            confidence.setIcon(components.status_dot_icon(tone))
            confidence.setData(Qt.ItemDataRole.UserRole + 1, tone)
        self.cand_table.blockSignals(False)
        total_all = len(self._data.get("candidates") or [])
        self.cand_body.setVisible(bool(total_all))
        self.cand_empty_state.setVisible(not total_all)
        self.cand_empty.setVisible(not rows)
        self.cand_empty.setText(i18n.tr('没有待审核的候选词条。') if not total_all
                                else i18n.tr('这个筛选下没有候选词条。'))
        self.cand_table.setVisible(bool(rows))
        self._more_text(self.cand_more, len(rows), len(shown))
        high = [r for r in rows if r.get("high_confidence") and not r.get("has_conflict")]
        self.high_button.setText(i18n.tr('接受全部高置信度（{v0}）', v0=len(high)))
        self.high_button.setVisible(bool(high))
        self._candidate_selected()

    def selected_candidates(self) -> list[dict]:
        rows = {index.row() for index in self.cand_table.selectionModel().selectedRows()} \
            if self.cand_table.selectionModel() else set()
        return [self._shown["candidates"][r] for r in sorted(rows)
                if 0 <= r < len(self._shown["candidates"])]

    def _candidate_selected(self) -> None:
        picked = self.selected_candidates()
        count = len(picked)
        # 作用于选中项的操作：没选中就不出现（隐藏优于禁用）。
        for button in (self.accept_button, self.task_button, self.reject_button):
            button.setVisible(bool(picked))
            button.setEnabled(not self._busy)
        self.accept_button.setText(i18n.tr('接受为项目术语') if count <= 1
                                   else i18n.tr('接受所选 {v0} 条为项目术语', v0=count))
        self.jump_button.setVisible(count == 1)
        self.edit_accept_button.setVisible(count == 1)
        self.edit_accept_button.setEnabled(not self._busy)
        self.high_button.setEnabled(not self._busy)
        if count != 1:
            self.cand_detail.setHtml(
                f"<p>{_escape(i18n.tr('选择一条候选查看上下文与出现位置。') if not count else i18n.tr('已选 {v0} 条；可批量处理。', v0=count))}</p>")
            return
        row = picked[0]
        parts = [f"<h3>{_escape(row['source'])} → {_escape(row['target'])}</h3>",
                 f"<p>{_escape(i18n.tr_dynamic(row['kind_label']))} · "
                 f"{_escape(i18n.tr('置信度 {v0}', v0=assets.confidence_text(row)))} · "
                 f"{_escape(i18n.tr_dynamic(assets.usage_text(row)))} · {_escape(row['document'])}</p>"]
        if row.get("source_context"):
            parts.append(f"<p><b>{_escape(i18n.tr('原文上下文'))}</b><br>{_escape(row['source_context'])}</p>")
        if row.get("target_context"):
            parts.append(f"<p><b>{_escape(i18n.tr('译文上下文'))}</b><br>{_escape(row['target_context'])}</p>")
        if row.get("has_conflict"):
            parts.append(f"<p style='color:#b00'>{_escape(row['conflict_summary'])}</p>")
        if row.get("positions"):
            parts.append("<p>" + _escape(i18n.tr('出现位置：') + "、".join(row["positions"])) + "</p>")
        self.cand_detail.setHtml("".join(parts))

    def _decide(self, decision: str) -> None:
        picked = self.selected_candidates()
        if picked:
            self.decideRequested.emit(picked, decision)

    def _edit_accept(self) -> None:
        picked = self.selected_candidates()
        if len(picked) == 1:
            self.acceptEditedRequested.emit(dict(picked[0]))

    def _accept_all_high(self) -> None:
        rows = [r for r in self._filtered_candidates()
                if r.get("high_confidence") and not r.get("has_conflict")]
        if rows:
            self.decideRequested.emit(rows, "project_term")

    def _jump(self) -> None:
        picked = self.selected_candidates()
        if len(picked) != 1:
            return
        row = picked[0]
        entries = row.get("position_entries") or []
        index = entries[0]["index"] if entries else row.get("first_observed_segment")
        self.openJobRequested.emit(str(row["job_id"]), int(index) if isinstance(index, int) else -1)

    # ================= 术语库 =================
    def _fill_terms(self) -> None:
        total_all = len(self._data.get("terms") or [])
        self.term_body.setVisible(bool(total_all))
        self.term_empty_state.setVisible(not total_all)
        rows = assets.filter_terms(self._data.get("terms") or [], query=self.term_query.text())
        shown = rows[: self._limit["terms"]]
        self._shown["terms"] = shown
        self.term_table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            where = row.get("document") or row.get("project_name") or ""
            values = (row["source"], row.get("preferred") or row.get("proposed_target") or "",
                      i18n.tr_dynamic(assets.status_label(row.get("status"))),
                      row.get("domain") or "", row.get("usage") or 0, where)
            for c, value in enumerate(values):
                self.term_table.setItem(r, c, QTableWidgetItem(str(value or "")))
        self._more_text(self.term_more, len(rows), len(shown))
        self._term_selected()

    def selected_term(self) -> dict:
        row = self.term_table.currentRow()
        return self._shown["terms"][row] if 0 <= row < len(self._shown["terms"]) else {}

    def _term_selected(self) -> None:
        row = self.selected_term()
        self.term_open.setVisible(bool(row.get("job_id")))
        # 编辑 / 删除作用在具体某个任务的术语表上；只来自项目记忆的术语没有任务可改。
        editable = bool([task for task in row.get("tasks") or [] if task.get("job_id")])
        self.term_edit.setVisible(editable)
        self.term_delete.setVisible(editable)
        self.term_edit.setEnabled(not self._busy)
        self.term_delete.setEnabled(not self._busy)
        if not row:
            self.term_detail.setHtml("")
            return
        parts = [f"<h3>{_escape(row['source'])} → {_escape(row.get('preferred') or '—')}</h3>",
                 f"<p>{_escape(i18n.tr_dynamic(assets.status_label(row.get('status'))))} · "
                 f"{_escape(row.get('domain') or '')} · "
                 f"{_escape(i18n.tr_dynamic(assets.scope_label(row.get('scope'))))}</p>"]
        if row.get("forbidden"):
            parts.append("<p>" + _escape(i18n.tr('禁用译法：') + "、".join(row["forbidden"])) + "</p>")
        if row.get("note"):
            parts.append(f"<p>{_escape(row['note'])}</p>")
        self.term_detail.setHtml("".join(parts))

    def _open_term_job(self) -> None:
        row = self.selected_term()
        if row.get("job_id"):
            self.openJobRequested.emit(str(row["job_id"]), -1)

    # ================= 翻译记忆 =================
    def _fill_tm(self) -> None:
        has_tm = bool(self._data.get("tm"))
        self.tm_body.setVisible(has_tm)
        self.tm_query.setVisible(has_tm)
        self.tm_empty_state.setVisible(not has_tm)
        rows = assets.filter_tm(self._data.get("tm") or [], query=self.tm_query.text())
        shown = rows[: self._limit["tm"]]
        self._shown["tm"] = shown
        self.tm_table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            values = (row["source"], row["target"], row.get("target_lang") or i18n.tr('未标注（不会自动复用）'),
                      str(row.get("updated_at") or "")[:16].replace("T", " "))
            for c, value in enumerate(values):
                self.tm_table.setItem(r, c, QTableWidgetItem(str(value or "")))
        self._more_text(self.tm_more, len(rows), len(shown))
        self.tm_clear.setEnabled(bool(self._data.get("tm")) and not self._busy)
