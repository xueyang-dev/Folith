"""项目页：左边项目列表，右边这个项目的内容（概览 / 术语库 / 翻译记忆 / 待审核）。

语言资产住在项目里：术语库和翻译记忆是项目的，待审核是项目里各任务发现的候选加上
"和术语库不一致"的待决冲突。「未分类」置顶，只汇总各任务自己的术语（只读），共享的翻译
记忆照旧，从不向新任务注入任何东西。

只做展示和发信号；读数据放在后台（窗口调适配层），写状态的是适配层。项目术语库的
增删改是几 KB 的小写入，直接在这里调适配层。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from folith import language_assets as assets

from . import adapter
from . import bootstrap
from . import components
from . import i18n
from . import icons
from . import project_sheets
from . import prompts
from . import style as style_module
from . import theme as theme_module

TABS = (("overview", "概览"), ("terms", "术语库"), ("tm", "翻译记忆"), ("review", "待审核"))
UNSORTED_TABS = (("terms", "各任务的术语"), ("tm", "共享翻译记忆"), ("review", "待审核"))
BASIS_FILTERS = (("all", "全部"), ("reviewed", "来自已审校译文"),
                 ("machine", "来自机器译文"), ("conflict", "和术语库不一致"))
PAGE = 300                                  # 一次最多摆出的行数；筛选始终作用于全部


def _text(text: str = "", role: str = "body", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    # 阿拉伯语等从右往左的文字在 QLabel 里默认靠右；列表里的术语列要和别的列一样靠左。
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    style_module.set_text_role(label, role)
    label.setWordWrap(wrap)
    return label


def _dir(label: str) -> str:
    return " → ".join(i18n.tr_dynamic(part.strip()) for part in str(label).split("→"))


def _tone_color(tone: str) -> str:
    tokens = theme_module.tokens(style_module.current_theme())
    return tokens[theme_module.STATUS_TONES.get(tone, "text-muted")]


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.horizontalHeader().setStretchLastSection(True)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.horizontalHeader().setFixedHeight(theme_module.CONTROL["header"])
    table.horizontalHeader().setHighlightSections(False)
    table.verticalHeader().setDefaultSectionSize(theme_module.CONTROL["row"] - 4)
    table.setItemDelegate(style_module.RowDelegate(table))
    return table


class _ListRow(style_module.Surface):
    """左栏里的一行（未分类 / 项目）：图标 + 名称 + 副标题 + 待审核角标。"""

    clicked = Signal(str)

    def __init__(self, key: str, name: str, meta: str, pending: int, *, icon: str,
                 selected: bool, tone: str = "text-secondary") -> None:
        super().__init__(elevation="raised" if selected else "flat",
                         fill="bg-surface" if selected else "none")
        self.key = key
        self.set_selected(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(name)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        row.addWidget(icons.IconLabel(icon, 18, "accent" if selected else tone))
        text = QVBoxLayout()
        text.setSpacing(0)
        title = _text(name, "name")
        title.setMinimumWidth(0)
        self.meta = _text(meta, "time")
        text.addWidget(title)
        text.addWidget(self.meta)
        row.addLayout(text, 1)
        if pending:
            row.addWidget(style_module.Chip(str(pending), "info"))
        self.setToolTip(f"{name}\n{meta}")

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class ProjectsView(QWidget):
    """项目页（主从布局）。"""

    #: 要读哪个项目的详情（项目 id / 依据筛选 / 是否展开只出现一次的候选）
    detailRequested = Signal(str, str, bool)
    #: 需要重新读取项目列表与当前详情（数据被改过）
    reloadRequested = Signal()
    openJobRequested = Signal(str, int)
    moveRequested = Signal(list)
    newProjectRequested = Signal()
    acceptRequested = Signal(dict, str)         # 待审核项, 译名
    decideRequested = Signal(dict, str)         # 待审核项, task_only / rejected / keep
    batchAcceptRequested = Signal(list)
    projectsChanged = Signal()                  # 新建 / 改名 / 归档 / 删除之后
    pendingChanged = Signal(int)                # 侧栏「项目」角标

    def __init__(self, parent: QWidget | None = None, *,
                 margins: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        super().__init__(parent)
        self.changed = False
        self._overview: dict = {}
        self._detail: dict = {}
        self._selected = ""                 # 项目 id；"unsorted" = 未分类
        self._tab = "overview"
        self._basis = "all"
        self._show_singles = False
        self._archived_open = False
        self._review_items: list[dict] = []
        self._review_rows: dict[int, int] = {}
        self._term_rows: list[dict] = []

        self.nav = self._build_nav()
        self.content = self._build_content()
        row = QHBoxLayout(self)
        row.setContentsMargins(*margins)
        row.setSpacing(0)
        row.addWidget(self.nav)
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setObjectName("hairline")
        row.addWidget(divider)
        row.addWidget(self.content, 1)
        self.apply_overview({})

    # ================= 左栏 =================
    def _build_nav(self) -> QWidget:
        nav = QWidget()
        nav.setFixedWidth(264)
        box = QVBoxLayout(nav)
        box.setContentsMargins(12, 12, 12, 8)
        box.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(8, 0, 0, 8)
        title = _text(i18n.tr('项目'), "page-title")
        head.addWidget(title, 1)
        self.new_button = style_module.icon_button("plus", i18n.tr('新建项目'))
        self.new_button.setAccessibleName(i18n.tr('新建项目'))
        self.new_button.clicked.connect(self._create)
        head.addWidget(self.new_button)
        box.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.tr('搜索项目'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._fill_nav())
        box.addWidget(self.search)
        box.addSpacing(8)
        self.unsorted_slot = QVBoxLayout()
        box.addLayout(self.unsorted_slot)
        self.rule = QFrame()
        self.rule.setFixedHeight(1)
        self.rule.setObjectName("hairline")
        box.addSpacing(6)
        box.addWidget(self.rule)
        box.addSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("transparent")
        self.list_box = QVBoxLayout(holder)
        self.list_box.setContentsMargins(0, 0, 0, 0)
        self.list_box.setSpacing(2)
        scroll.setWidget(holder)
        box.addWidget(scroll, 1)
        self.archived_button = QPushButton("")
        style_module.set_button(self.archived_button, "plain")
        self.archived_button.clicked.connect(self._toggle_archived)
        box.addWidget(self.archived_button, 0, Qt.AlignmentFlag.AlignLeft)
        return nav

    def apply_overview(self, data: dict) -> None:
        self._overview = data or {}
        total = int(self._overview.get("pending_total") or 0)
        self.pendingChanged.emit(total)
        if not self._selected or not self._exists(self._selected):
            active = [p for p in self._overview.get("projects") or [] if not p.get("archived")]
            if active:
                self._selected = max(active, key=lambda p: (p.get("pending", 0), p["name"]))["project_id"]
            elif self._overview.get("unsorted"):
                self._selected = "unsorted"
            else:
                self._selected = ""
            self._tab = "terms" if self._selected == "unsorted" else "overview"
        self._fill_nav()
        self._request_detail()

    def _exists(self, key: str) -> bool:
        if key == "unsorted":
            return bool(self._overview.get("unsorted"))
        return any(p["project_id"] == key for p in self._overview.get("projects") or [])

    def _fill_nav(self) -> None:
        for layout in (self.unsorted_slot, self.list_box):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        unsorted = self._overview.get("unsorted")
        if unsorted:
            count = int(unsorted.get("job_count") or 0)
            meta = i18n.tr('{v0} 个未归属的任务', v0=count) if count else i18n.tr('没有未归属的任务')
            self.unsorted_slot.addWidget(self._make_row(
                "unsorted", i18n.tr('未分类'), meta, int(unsorted.get("pending") or 0),
                "inbox"))
        needle = self.search.text().strip().casefold()
        active, archived = [], []
        for project in self._overview.get("projects") or []:
            if needle and needle not in project["name"].casefold():
                continue
            (archived if project.get("archived") else active).append(project)
        for project in active:
            self.list_box.addWidget(self._make_project_row(project))
        if not active:
            self.list_box.addWidget(_text(
                i18n.tr('还没有项目。点右上角的 + 新建一个。') if not needle
                else i18n.tr('没有匹配的项目。'), "meta", wrap=True))
        if self._archived_open:
            for project in archived:
                self.list_box.addWidget(self._make_project_row(project))
        self.list_box.addStretch(1)
        self.archived_button.setVisible(bool(archived))
        self.archived_button.setText(
            ("▾ " if self._archived_open else "▸ ") + i18n.tr('已归档 · {v0}', v0=len(archived)))

    def _make_project_row(self, project: dict) -> _ListRow:
        meta = f"{_dir(project.get('direction_label') or i18n.tr('未设定'))} · " \
            + i18n.tr('{v0} 个任务', v0=project.get("job_count", 0))
        return self._make_row(project["project_id"], project["name"], meta,
                              int(project.get("pending") or 0), "folder")

    def _make_row(self, key, name, meta, pending, icon) -> _ListRow:
        row = _ListRow(key, name, meta, pending, icon=icon, selected=key == self._selected)
        row.clicked.connect(self.select)
        return row

    def _toggle_archived(self) -> None:
        self._archived_open = not self._archived_open
        self._fill_nav()

    def select(self, key: str) -> None:
        if key == self._selected:
            return
        self._selected = key
        self._tab = "terms" if key == "unsorted" else "overview"
        self._basis, self._show_singles = "all", False
        self._fill_nav()
        self._request_detail()

    def selected_key(self) -> str:
        return self._selected

    def selected_project(self) -> dict | None:
        return next((p for p in self._overview.get("projects") or []
                     if p["project_id"] == self._selected), None)

    # ================= 右栏骨架 =================
    def _build_content(self) -> QWidget:
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(28, 14, 20, 12)
        box.setSpacing(12)
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = _text("", "page-title")
        self.subtitle = _text("", "meta")
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles, 1)
        self.export_button = QPushButton(i18n.tr('导出记忆'))
        self.export_button.setIcon(icons.icon("download", 16, "text-secondary"))
        self.export_button.clicked.connect(lambda: self._export_memory())
        self.more_button = style_module.icon_button("more", i18n.tr('更多操作'))
        self.more_button.setAccessibleName(i18n.tr('更多操作'))
        self.more_button.clicked.connect(self._open_more)
        head.addWidget(self.export_button, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(head)

        self.tab_bar = style_module.SegmentedControl([("overview", "")])
        self.tab_bar.changed.connect(self._tab_changed)
        box.addWidget(self.tab_bar, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages = QStackedWidget()
        self.page_overview = self._build_overview()
        self.page_terms = self._build_terms()
        self.page_tm = self._build_tm()
        self.page_review = self._build_review()
        self.page_unsorted_terms = self._build_unsorted_terms()
        for page in (self.page_overview, self.page_terms, self.page_tm, self.page_review,
                     self.page_unsorted_terms):
            self.pages.addWidget(page)
        box.addWidget(self.pages, 1)
        self.status = _text("", "meta", wrap=True)
        box.addWidget(self.status)
        self.empty = style_module.EmptyState(
            icon="folder", page=True, title=i18n.tr('还没有项目'),
            hint=i18n.tr('项目保存术语库和翻译记忆，新任务会自动用上。'),
            action=i18n.tr('新建项目…'))
        self.empty.actionTriggered.connect(self._create)
        box.addWidget(self.empty, 1)
        return content

    def _request_detail(self) -> None:
        if not self._selected:
            self._render_empty()
            return
        self.detailRequested.emit(self._selected, self._basis, self._show_singles)

    def _render_empty(self) -> None:
        for widget in (self.tab_bar, self.pages, self.export_button, self.more_button):
            widget.setVisible(False)
        self.title.setText("")
        self.subtitle.setText("")
        self.empty.setVisible(True)

    def _note(self, text: str) -> None:
        self.status.setText(text)

    # ---- 页签 -----------------------------------------------------------------
    def _tab_changed(self, key: str) -> None:
        if not key or key == self._tab:
            return
        self._tab = key
        self._show_tab()

    def _tab_options(self, detail: dict) -> list[tuple[str, str]]:
        unsorted = self._selected == "unsorted"
        counts = {
            "terms": len(detail.get("terms") or []) if not unsorted
            else int(detail.get("term_total") or 0),
            "tm": len(detail.get("tm") or []),
            "review": int((detail.get("review") or {}).get("total") or 0),
        }
        options = []
        for key, label in (UNSORTED_TABS if unsorted else TABS):
            count = counts.get(key)
            text = i18n.tr_dynamic(label) + (f"  {count}" if count is not None else "")
            options.append((key, text))
        return options

    def _show_tab(self) -> None:
        unsorted = self._selected == "unsorted"
        target = {"overview": self.page_overview, "tm": self.page_tm,
                  "review": self.page_review,
                  "terms": self.page_unsorted_terms if unsorted else self.page_terms}[self._tab]
        self.pages.setCurrentWidget(target)

    # ================= 详情 =================
    def apply_detail(self, detail: dict) -> None:
        """后台读取完成。详情属于哪个项目由 ``_detail_for`` 记着，过期的结果直接丢掉。"""
        self._detail = detail or {}
        self.empty.setVisible(False)
        unsorted = self._selected == "unsorted"
        for widget in (self.tab_bar, self.pages, self.more_button):
            widget.setVisible(True)
        self.export_button.setVisible(True)
        options = self._tab_options(self._detail)
        valid = [key for key, _ in options]
        if self._tab not in valid:
            self._tab = valid[0]
        self.tab_bar.set_options(options)
        self.tab_bar.set_value(self._tab)
        if unsorted:
            self.title.setText(i18n.tr('未分类'))
            count = int((self._overview.get("unsorted") or {}).get("job_count") or 0)
            self.subtitle.setText(i18n.tr('{v0} 个没有归入项目的任务', v0=count))
            self.export_button.setText(i18n.tr('导出…'))
            self._fill_unsorted_terms()
            self._fill_tm()
            self._fill_review()
        else:
            project = self._detail.get("project") or {}
            self.title.setText(str(project.get("name") or ""))
            when = components.relative_time(str(project.get("updated_at") or ""))
            self.subtitle.setText(" · ".join(part for part in (
                _dir(project.get("direction_label") or i18n.tr('未设定')),
                i18n.tr('{v0} 个任务', v0=len(self._detail.get("tasks") or [])),
                i18n.tr('{v0}更新', v0=when) if when else "") if part))
            self.export_button.setText(i18n.tr('导出记忆'))
            self._fill_overview()
            self._fill_terms()
            self._fill_tm()
            self._fill_review()
        self._show_tab()

    # ================= 概览 =================
    def _build_overview(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("transparent")
        self.overview_box = QVBoxLayout(holder)
        self.overview_box.setContentsMargins(0, 0, 8, 0)
        self.overview_box.setSpacing(14)
        scroll.setWidget(holder)
        return scroll

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear(item.layout())

    def _notice_row(self, text: str, button: str, slot, tone: str = "info") -> QWidget:
        strip = style_module.NoticeStrip(text, tone=tone)
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(strip, 1)
        action = QPushButton(button)
        style_module.set_button(action, "secondary", small=True)
        action.clicked.connect(slot)
        row.addWidget(action)
        return holder

    def _fill_overview(self) -> None:
        self._clear(self.overview_box)
        detail = self._detail
        project = detail.get("project") or {}
        description = str(project.get("description") or "")
        head = QHBoxLayout()
        head.addWidget(_text(description or i18n.tr('还没有项目说明（客户、领域、语气要求……）'),
                             "body" if description else "meta", wrap=True), 1)
        edit = style_module.icon_button("file-text", i18n.tr('编辑说明'))
        edit.setAccessibleName(i18n.tr('编辑说明'))
        edit.clicked.connect(self._describe)
        head.addWidget(edit)
        self.overview_box.addLayout(head)
        if not project.get("target_lang"):
            self.overview_box.addWidget(self._notice_row(
                i18n.tr('还没有设置语言方向。设置之后，新任务只会用到方向相同的术语。'),
                i18n.tr('设置…'), self._set_direction, "warn"))
        elif project.get("direction_inferred"):
            self.overview_box.addWidget(self._notice_row(
                i18n.tr('语言方向是按这个项目里的任务推断的（{v0}）。', v0=_dir(project["direction_label"])),
                i18n.tr('确认方向…'), self._set_direction))
        tiles = QGridLayout()
        tiles.setHorizontalSpacing(10)
        tiles.setVerticalSpacing(10)
        review_total = int((detail.get("review") or {}).get("total") or 0)
        stats = (
            (i18n.tr('术语库'), str(len(detail.get("terms") or [])), i18n.tr('新任务自动使用'), "terms"),
            (i18n.tr('翻译记忆'), str(len(detail.get("tm") or [])), i18n.tr('已审校的句对'), "tm"),
            (i18n.tr('待审核'), str(review_total),
             i18n.tr('来自 {v0} 个任务', v0=detail.get("review_tasks", 0)), "review"),
            (i18n.tr('风格规则'), str(detail.get("style_rule_count", 0)), i18n.tr('审校中确认'), ""))
        for column, (label, value, hint, target) in enumerate(stats):
            tiles.addWidget(self._stat_tile(label, value, hint, target), 0, column)
            tiles.setColumnStretch(column, 1)
        self.overview_box.addLayout(tiles)
        conflicts = int(detail.get("conflict_count") or 0)
        if conflicts:
            self.overview_box.addWidget(self._notice_row(
                i18n.tr('有 {v0} 条术语的译名和术语库里的不一致，等你决定用哪一个。', v0=conflicts),
                i18n.tr('逐条处理'), self._open_conflicts, "warn"))
        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._tasks_card(), 3)
        columns.addWidget(self._log_card(), 2)
        self.overview_box.addLayout(columns)
        self.overview_box.addStretch(1)

    def _stat_tile(self, label: str, value: str, hint: str, target: str) -> QWidget:
        tile = style_module.Card()
        tile.setCursor(Qt.CursorShape.PointingHandCursor if target else Qt.CursorShape.ArrowCursor)
        box = QVBoxLayout(tile)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(0)
        box.addWidget(_text(label, "label"))
        number = _text(value, "card-title")
        box.addWidget(number)
        box.addWidget(_text(hint, "time"))
        if target:
            tile.mousePressEvent = lambda _e, key=target: self.tab_bar.set_value(key) \
                or self._tab_changed(key)
        return tile

    def _tasks_card(self) -> QWidget:
        card = style_module.Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_text(i18n.tr('任务'), "section"), 1)
        box.addLayout(head)
        tasks = self._detail.get("tasks") or []
        if not tasks:
            box.addWidget(_text(i18n.tr('这个项目里还没有任务。可以在任务库里把任务移进来。'),
                                "meta", wrap=True))
        for task in tasks[:8]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(icons.IconLabel("file-text", 16, "text-secondary"))
            name = _text(task["name"], "body")
            row.addWidget(name, 1)
            tone = ("error" if task.get("runtime_status") == "failed" else
                    "active" if task.get("active") else
                    "done" if "交付" in str(task.get("status")) else "idle")
            row.addWidget(components.StatusLabel(
                i18n.tr_dynamic(str(task.get("status") or "")) or i18n.tr('未开始'), tone))
            open_button = QPushButton(i18n.tr('打开'))
            style_module.set_button(open_button, "plain", small=True)
            open_button.clicked.connect(lambda _c=False, j=task["job_id"]: self.openJobRequested.emit(j, -1))
            row.addWidget(open_button)
            box.addLayout(row)
        if len(tasks) > 8:
            box.addWidget(_text(i18n.tr('还有 {v0} 个，在任务库中查看', v0=len(tasks) - 8), "time"))
        return card

    def _log_card(self) -> QWidget:
        card = style_module.Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        box.addWidget(_text(i18n.tr('最近变化'), "section"))
        log = self._detail.get("log") or []
        if not log:
            box.addWidget(_text(i18n.tr('还没有变化。收进术语、确认风格规则后会记在这里。'),
                                "meta", wrap=True))
        for entry in log[:6]:
            row = QHBoxLayout()
            row.addWidget(_text(i18n.tr_dynamic(entry["what"]), "body", wrap=True), 1)
            row.addWidget(_text(components.relative_time(entry["when"]), "time"))
            box.addLayout(row)
        return card

    def _open_conflicts(self) -> None:
        self._basis = "conflict"
        self._tab = "review"
        self._request_detail()

    # ================= 术语库 =================
    def _build_terms(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        bar = QHBoxLayout()
        self.term_query = QLineEdit()
        self.term_query.setPlaceholderText(i18n.tr('搜索原文、译名或领域'))
        self.term_query.setClearButtonEnabled(True)
        self.term_query.textChanged.connect(lambda _t: self._fill_terms())
        bar.addWidget(self.term_query, 1)
        self.term_note = _text("", "time")
        bar.addWidget(self.term_note)
        self.term_import = QPushButton(i18n.tr('从文件导入…'))
        self.term_import.setToolTip(i18n.tr('导入别处导出的项目记忆文件（只增不改）'))
        self.term_import.clicked.connect(lambda: self._import_memory())
        self.term_add = QPushButton(i18n.tr('添加术语'))
        style_module.set_button(self.term_add, "primary")
        self.term_add.setIcon(icons.icon("plus", 16, "text-on-accent"))
        self.term_add.clicked.connect(lambda: self._add_term())
        bar.addWidget(self.term_import)
        bar.addWidget(self.term_add)
        box.addLayout(bar)
        self.term_table = _table([i18n.tr('原文'), i18n.tr('译名'), i18n.tr('领域'),
                                  i18n.tr('怎么来的'), i18n.tr('用到')])
        self.term_table.itemSelectionChanged.connect(self._term_selected)
        box.addWidget(self.term_table, 1)
        self.term_detail = style_module.Card()
        detail = QVBoxLayout(self.term_detail)
        detail.setContentsMargins(16, 10, 16, 10)
        detail.setSpacing(4)
        self.term_detail_body = QVBoxLayout()
        self.term_detail_body.setSpacing(4)
        detail.addLayout(self.term_detail_body)
        actions = QHBoxLayout()
        self.term_edit = QPushButton(i18n.tr('编辑'))
        self.term_remove = QPushButton(i18n.tr('从术语库移除'))
        style_module.set_button(self.term_remove, "danger")
        self.term_edit.clicked.connect(lambda: self._edit_term())
        self.term_remove.clicked.connect(lambda: self._remove_term())
        actions.addWidget(self.term_edit)
        actions.addWidget(self.term_remove)
        actions.addStretch(1)
        detail.addLayout(actions)
        box.addWidget(self.term_detail)
        self.term_empty = style_module.EmptyState(
            icon="termbase", page=True, title=i18n.tr('术语库是空的'),
            hint=i18n.tr('在待审核里收进候选，或者手动添加；新任务会自动使用。'),
            action=i18n.tr('添加术语…'))
        self.term_empty.actionTriggered.connect(lambda: self._add_term())
        box.addWidget(self.term_empty, 1)
        return page

    def _fill_terms(self) -> None:
        terms = self._detail.get("terms") or []
        needle = self.term_query.text().strip().casefold()
        rows = [t for t in terms if not needle or needle in " ".join(
            [t["source"], t["target"], t["domain"]]).casefold()]
        self._term_rows = rows[:PAGE]
        project = self._detail.get("project") or {}
        self.term_note.setText(i18n.tr('已锁定，新任务（{v0}）会自动使用',
                                       v0=_dir(project.get("direction_label") or i18n.tr('未设定'))))
        self.term_table.setRowCount(len(self._term_rows))
        for r, term in enumerate(self._term_rows):
            origin = i18n.tr_dynamic(term["origin_text"])
            if term["origin_job"]:
                origin += " · " + term["origin_job"].rsplit(".", 1)[0]
            used = (i18n.tr('{v0} 个任务 · {v1} 处', v0=len(term["used_tasks"]), v1=term["used_total"])
                    if term["used_tasks"] else i18n.tr('尚未用到'))
            for c, value in enumerate((term["source"], term["target"], term["domain"], origin, used)):
                self.term_table.setItem(r, c, QTableWidgetItem(str(value or "")))
        has = bool(terms)
        for widget in (self.term_table, self.term_query, self.term_detail):
            widget.setVisible(has)
        self.term_empty.setVisible(not has)
        if self._term_rows:
            self.term_table.selectRow(0)
        self._term_selected()

    def selected_term(self) -> dict:
        row = self.term_table.currentRow()
        return self._term_rows[row] if 0 <= row < len(self._term_rows) else {}

    def _term_selected(self) -> None:
        self._clear(self.term_detail_body)
        term = self.selected_term()
        self.term_detail.setVisible(bool(term) and bool(self._detail.get("terms")))
        if not term:
            return
        lines = []
        if term["forbidden"]:
            lines.append((i18n.tr('不要译成'), "、".join(term["forbidden"])))
        if term["note"]:
            lines.append((i18n.tr('备注'), term["note"]))
        if term["used_tasks"]:
            lines.append((i18n.tr('出现在'), "　".join(
                f"{name.rsplit('.', 1)[0]} · {i18n.tr('{v0} 处', v0=n)}" for name, n in term["used_tasks"])))
        if not lines:
            lines.append((i18n.tr('详情'), i18n.tr('没有备注或禁用译名。')))
        for label, value in lines:
            row = QHBoxLayout()
            title = _text(label, "label")
            title.setFixedWidth(64)
            row.addWidget(title, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(_text(value, "body", wrap=True), 1)
            self.term_detail_body.addLayout(row)

    def _term_dialog(self, source: str = "", target: str = "", domain: str = "",
                     note: str = "") -> dict | None:
        project = self._detail.get("project") or {}
        dialog = NewTermDialog(project_name=str(project.get("name") or ""), source=source,
                               target=target, domain=domain, note=note,
                               locked_source=bool(source), parent=self)
        if bootstrap.automation() or dialog.exec() != dialog.DialogCode.Accepted:
            return None
        return dialog.values()

    def _add_term(self, values: dict | None = None) -> None:
        project = self._detail.get("project") or {}
        if not project.get("project_id"):
            return
        values = values or self._term_dialog()
        if not values:
            return
        try:
            adapter.add_project_term(project["project_id"], values["source"], values["target"],
                                     domain=values["domain"], note=values.get("note", ""))
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self._note(i18n.tr('已把「{v0}」收进术语库。', v0=values["source"]))
        self.reloadRequested.emit()

    def _edit_term(self, values: dict | None = None) -> None:
        term = self.selected_term()
        if not term:
            return
        values = values or self._term_dialog(term["source"], term["target"], term["domain"],
                                             term["note"])
        if values:
            self._add_term(values)

    def _remove_term(self) -> None:
        term, project = self.selected_term(), self._detail.get("project") or {}
        if not term or not project.get("project_id"):
            return
        if not bootstrap.automation() and not prompts.ConfirmDialog.ask(
                self, i18n.tr('从术语库移除'),
                i18n.tr('从「{v0}」的术语库里移除「{v1}」？任务自己的术语表不受影响。',
                        v0=project["name"], v1=term["source"]),
                yes=i18n.tr('移除'), no=i18n.tr('取消'), tone="danger"):
            return
        try:
            adapter.remove_project_term(project["project_id"], term["source"])
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self.reloadRequested.emit()

    # ================= 翻译记忆 =================
    def _build_tm(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        self.tm_query = QLineEdit()
        self.tm_query.setPlaceholderText(i18n.tr('搜索原文或译文……'))
        self.tm_query.setClearButtonEnabled(True)
        self.tm_query.textChanged.connect(lambda _t: self._fill_tm())
        box.addWidget(self.tm_query)
        self.tm_table = _table([i18n.tr('原文'), i18n.tr('译文'), i18n.tr('目标语言'), i18n.tr('更新')])
        box.addWidget(self.tm_table, 1)
        self.tm_note = _text("", "meta", wrap=True)
        box.addWidget(self.tm_note)
        self.tm_empty = style_module.EmptyState(
            icon="memory", page=True, title=i18n.tr('还没有翻译记忆'),
            hint=i18n.tr('审校通过的句对会记在这里，同一方向的新任务遇到相同的句子会直接复用。'))
        box.addWidget(self.tm_empty, 1)
        return page

    def _fill_tm(self) -> None:
        rows = assets.filter_tm(self._detail.get("tm") or [], query=self.tm_query.text())
        shown = rows[:PAGE]
        self.tm_table.setRowCount(len(shown))
        for r, row in enumerate(shown):
            values = (row["source"], row["target"],
                      row.get("target_lang") or i18n.tr('未标注（不会自动复用）'),
                      str(row.get("updated_at") or "")[:16].replace("T", " "))
            for c, value in enumerate(values):
                self.tm_table.setItem(r, c, QTableWidgetItem(str(value or "")))
        has = bool(self._detail.get("tm"))
        self.tm_table.setVisible(has)
        self.tm_query.setVisible(has)
        self.tm_empty.setVisible(not has)
        unsorted = self._selected == "unsorted"
        self.tm_note.setText(
            i18n.tr('「未分类」的翻译记忆是共享的：所有未归属的任务都用它。') if unsorted else
            i18n.tr('翻译记忆按项目隔离；清空只影响这个项目（在「更多」里）。'))
        self.tm_note.setVisible(has)

    # ================= 未分类：各任务的术语 =================
    def _build_unsorted_terms(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("transparent")
        self.unsorted_box = QVBoxLayout(holder)
        self.unsorted_box.setContentsMargins(0, 0, 8, 0)
        self.unsorted_box.setSpacing(12)
        scroll.setWidget(holder)
        return scroll

    def _fill_unsorted_terms(self) -> None:
        self._clear(self.unsorted_box)
        self.unsorted_box.addWidget(style_module.NoticeStrip(
            i18n.tr('这些术语各归各的任务，不会用到别的任务上；这里只是汇总给你查看和导出。'),
            tone="info"))
        tasks = self._detail.get("tasks") or []
        if not tasks:
            self.unsorted_box.addWidget(style_module.EmptyState(
                icon="inbox", page=True, title=i18n.tr('没有未归属的任务'),
                hint=i18n.tr('导入文档时没有选项目的任务会先放在这里。')))
        for task in tasks:
            self.unsorted_box.addWidget(self._unsorted_card(task))
        self.unsorted_box.addStretch(1)

    def _unsorted_card(self, task: dict) -> QWidget:
        card = style_module.Card()
        box = QVBoxLayout(card)
        box.setContentsMargins(0, 0, 0, 6)
        box.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 10, 14, 10)
        head.setSpacing(10)
        head.addWidget(icons.IconLabel("file-text", 16, "text-secondary"))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_text(task["name"], "name"))
        names.addWidget(_text(
            f"{_dir(task['direction_label'])} · "
            + i18n.tr('{v0} 条术语，{v1} 条已锁定', v0=task["term_count"], v1=task["locked_count"]),
            "time"))
        head.addLayout(names, 1)
        open_button = QPushButton(i18n.tr('打开任务'))
        style_module.set_button(open_button, "plain", small=True)
        open_button.clicked.connect(lambda _c=False, j=task["job_id"]: self.openJobRequested.emit(j, -1))
        head.addWidget(open_button)
        make = QPushButton(i18n.tr('用这个任务新建项目…'))
        style_module.set_button(make, "secondary", small=True)
        make.setIcon(icons.icon("folder", 14, "accent"))
        make.clicked.connect(lambda _c=False, j=task["job_id"]: self.moveRequested.emit([j]))
        head.addWidget(make)
        box.addLayout(head)
        grid = QGridLayout()
        grid.setContentsMargins(16, 4, 16, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for column, text in enumerate((i18n.tr('原文'), i18n.tr('译名'), i18n.tr('状态'), i18n.tr('出现'))):
            grid.addWidget(_text(text, "time"), 0, column)
        grid.setColumnStretch(0, 6)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 2)
        for r, term in enumerate(task["terms"], start=1):
            grid.addWidget(_text(term["source"], "name"), r, 0)
            grid.addWidget(_text(term["target"], "body"), r, 1)
            locked = term["status"] == "locked"
            grid.addWidget(components.StatusLabel(
                i18n.tr('已锁定') if locked else i18n.tr('暂定'), "done" if locked else "idle"), r, 2)
            grid.addWidget(_text(str(term["hits"]), "meta"), r, 3, Qt.AlignmentFlag.AlignRight)
        box.addLayout(grid)
        if task["more"]:
            box.addWidget(_text("  " + i18n.tr('还有 {v0} 条', v0=task["more"]), "time"))
        return card

    # ================= 待审核 =================
    def _build_review(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        bar = QHBoxLayout()
        self.basis_bar = style_module.SegmentedControl([("all", "")])
        self.basis_bar.changed.connect(self._basis_changed)
        bar.addWidget(self.basis_bar)
        bar.addStretch(1)
        self.batch_button = QPushButton(i18n.tr('批量收进…'))
        self.batch_button.clicked.connect(self._batch_accept)
        bar.addWidget(self.batch_button)
        box.addLayout(bar)
        note_row = QHBoxLayout()
        self.review_note = _text("", "time")
        self.singles_button = QPushButton("")
        style_module.set_button(self.singles_button, "plain", small=True)
        self.singles_button.clicked.connect(self._toggle_singles)
        note_row.addWidget(self.review_note)
        note_row.addWidget(self.singles_button)
        note_row.addStretch(1)
        box.addLayout(note_row)
        self.review_tree = _table(["", i18n.tr('候选'), i18n.tr('建议译法'),
                                   i18n.tr('依据'), i18n.tr('出现')])
        header = self.review_tree.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.review_tree.setColumnWidth(0, 36)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.review_tree.itemSelectionChanged.connect(self._review_selected)
        self.review_tree.itemChanged.connect(lambda *_a: self._sync_batch())
        box.addWidget(self.review_tree, 1)

        self.review_detail = style_module.Card()
        detail = QVBoxLayout(self.review_detail)
        detail.setContentsMargins(16, 10, 16, 10)
        detail.setSpacing(6)
        head = QHBoxLayout()
        self.detail_title = _text("", "name")
        self.detail_meta = _text("", "time")
        head.addWidget(self.detail_title)
        head.addWidget(self.detail_meta, 1)
        detail.addLayout(head)
        self.detail_context = _text("", "body", wrap=True)
        self.detail_context.setTextFormat(Qt.TextFormat.RichText)
        detail.addWidget(self.detail_context)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.name_label = _text(i18n.tr('译名'), "label")
        self.name_edit = QLineEdit()
        self.name_edit.setMinimumWidth(160)
        self.accept_button = QPushButton(i18n.tr('收进术语库'))
        style_module.set_button(self.accept_button, "primary")
        self.task_button = QPushButton(i18n.tr('只用于这个任务'))
        self.look_button = QPushButton(i18n.tr('看原文'))
        style_module.set_button(self.look_button, "plain")
        self.reject_button = QPushButton(i18n.tr('不是术语'))
        style_module.set_button(self.reject_button, "plain")
        self.accept_button.clicked.connect(self._accept_current)
        self.task_button.clicked.connect(lambda: self._decide_current("task_only"))
        self.reject_button.clicked.connect(lambda: self._decide_current("rejected"))
        self.look_button.clicked.connect(self._look)
        for widget in (self.name_label, self.name_edit, self.accept_button, self.task_button):
            actions.addWidget(widget)
        actions.addStretch(1)
        actions.addWidget(self.look_button)
        actions.addWidget(self.reject_button)
        detail.addLayout(actions)
        box.addWidget(self.review_detail)
        self.review_empty = style_module.EmptyState(
            icon="sparkles", page=True, title=i18n.tr('没有待审核的候选词条'),
            hint=i18n.tr('翻译中发现的新术语会出现在这里，由你决定是否收进术语库。'))
        box.addWidget(self.review_empty, 1)
        return page

    def _basis_changed(self, key: str) -> None:
        if key and key != self._basis:
            self._basis = key
            self._request_detail()

    def _toggle_singles(self) -> None:
        self._show_singles = not self._show_singles
        self._request_detail()

    def _fill_review(self) -> None:
        review = self._detail.get("review") or {}
        counts = review.get("counts") or {}
        unsorted = self._selected == "unsorted"
        options = []
        for key, label in BASIS_FILTERS:
            if unsorted and key == "conflict":
                continue
            options.append((key, f"{i18n.tr_dynamic(label)}  {counts.get(key, 0)}"))
        self.basis_bar.set_options(options)
        self.basis_bar.set_value(self._basis if self._basis in dict(options) else "all")
        collapsed = int(review.get("collapsed") or 0)
        self.review_note.setText(
            i18n.tr('词形变体已合并（hub / hubs 算一条）') + (
                " · " + i18n.tr('只出现过 1 次的 {v0} 条已收起', v0=collapsed)
                if collapsed and not self._show_singles else ""))
        self.singles_button.setVisible(bool(collapsed) or self._show_singles)
        self.singles_button.setText(i18n.tr('显示') if not self._show_singles else i18n.tr('收起'))
        table = self.review_tree
        table.blockSignals(True)
        table.clearSpans()
        table.setRowCount(0)
        self._review_items = []
        self._review_rows: dict[int, int] = {}          # 表格行 -> 待审核项下标
        first_row = -1
        for document, items in review.get("groups") or []:
            row = table.rowCount()
            table.insertRow(row)
            table.setSpan(row, 0, 1, 5)
            head = QTableWidgetItem(f"{document}    {i18n.tr('{v0} 条', v0=len(items))}")
            head.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = head.font()
            font.setBold(True)
            head.setFont(font)
            head.setIcon(icons.icon("file-text", 16, "text-secondary"))
            table.setItem(row, 0, head)
            for item in items[:PAGE]:
                row = table.rowCount()
                table.insertRow(row)
                source = item["source"] + (
                    "  · " + " · ".join(item["variants"]) if item.get("variants") else "")
                check = QTableWidgetItem("")
                check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                               | Qt.ItemFlag.ItemIsSelectable)
                check.setCheckState(Qt.CheckState.Unchecked)
                table.setItem(row, 0, check)
                table.setItem(row, 1, QTableWidgetItem(source))
                table.setItem(row, 2, QTableWidgetItem(item["target"]))
                basis = QTableWidgetItem(i18n.tr_dynamic(item["basis_label"]))
                basis.setForeground(style_module.qcolor(_tone_color(
                    {"reviewed": "done", "machine": "idle", "conflict": "attention"}[item["basis"]])))
                table.setItem(row, 3, basis)
                hits = QTableWidgetItem(str(item["hits"]) if item.get("kind") != "conflict" else "—")
                hits.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, 4, hits)
                self._review_rows[row] = len(self._review_items)
                self._review_items.append(item)
                if first_row < 0:
                    first_row = row
        table.blockSignals(False)
        has = bool(review.get("total")) or bool(collapsed)
        shown = bool(self._review_items)
        self.review_tree.setVisible(shown)
        self.review_detail.setVisible(shown)
        self.review_empty.setVisible(not has)
        if has and not shown:
            self.review_empty.setVisible(False)
        self.basis_bar.setVisible(has)
        self.batch_button.setVisible(shown and not unsorted)
        if first_row >= 0:
            table.selectRow(first_row)
        self._review_selected()
        self._sync_batch()

    def selected_review_item(self) -> dict:
        index = self._review_rows.get(self.review_tree.currentRow())
        return self._review_items[index] if index is not None else {}

    def checked_review_items(self) -> list[dict]:
        table = self.review_tree
        return [self._review_items[index] for row, index in self._review_rows.items()
                if table.item(row, 0) is not None
                and table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def _sync_batch(self) -> None:
        count = len(self.checked_review_items())
        self.batch_button.setText(i18n.tr('批量收进 {v0} 条…', v0=count) if count
                                  else i18n.tr('批量收进…'))

    def _review_selected(self) -> None:
        item = self.selected_review_item()
        unsorted = self._selected == "unsorted"
        self.review_detail.setVisible(bool(item))
        if not item:
            return
        conflict = item.get("basis") == "conflict"
        title = item["source"]
        meta = []
        if item.get("variants"):
            meta.append(i18n.tr('也写作 {v0}', v0=" / ".join(item["variants"])))
        if item.get("kind") != "conflict":
            meta.append(i18n.tr('{v0} 处', v0=item["hits"]))
        meta.append(i18n.tr('来自{v0}', v0=i18n.tr_dynamic(item["basis_label"]))
                    if not conflict else i18n.tr_dynamic(item["basis_label"]))
        self.detail_title.setText(title)
        self.detail_meta.setText(" · ".join(meta))
        context = ""
        if conflict:
            context = i18n.tr('项目术语库里是「{v0}」，这里建议「{v1}」。收进会改用新译名；保留则什么都不变。',
                              v0=item.get("local", ""), v1=item["target"])
        elif item.get("source_context"):
            context = f"{_escape(item['source_context'])} → {_escape(item.get('target_context') or '')}"
            context = context.replace(_escape(item["source"]), f"<b>{_escape(item['source'])}</b>")
            context = context.replace(_escape(item["target"]), f"<b>{_escape(item['target'])}</b>")
        self.detail_context.setText(context)
        self.detail_context.setVisible(bool(context))
        self.name_edit.setText(item["target"])
        project_review = not unsorted
        for widget in (self.name_label, self.name_edit):
            widget.setVisible(project_review and not conflict)
        if conflict:
            self.accept_button.setText(i18n.tr('改用「{v0}」', v0=item["target"]))
            self.task_button.setText(i18n.tr('保留原译名'))
        elif unsorted:
            self.accept_button.setText(i18n.tr('只用于这个任务'))
            self.task_button.setVisible(False)
        else:
            self.accept_button.setText(i18n.tr('收进术语库'))
            self.task_button.setText(i18n.tr('只用于这个任务'))
        self.task_button.setVisible(not unsorted)
        self.reject_button.setVisible(not conflict)
        self.look_button.setVisible(bool(item.get("job_id")) and not conflict)

    def _accept_current(self) -> None:
        item = self.selected_review_item()
        if not item:
            return
        if self._selected == "unsorted":
            self.decideRequested.emit(item, "task_only")
        else:
            self.acceptRequested.emit(item, self.name_edit.text().strip())

    def _decide_current(self, decision: str) -> None:
        item = self.selected_review_item()
        if item:
            self.decideRequested.emit(item, "keep" if item.get("kind") == "conflict" else decision)

    def _look(self) -> None:
        item = self.selected_review_item()
        if not item or not item.get("job_id"):
            return
        member = (item.get("members") or [item])[0]
        entries = member.get("position_entries") or []
        index = entries[0]["index"] if entries else member.get("first_observed_segment")
        self.openJobRequested.emit(str(item["job_id"]), int(index) if isinstance(index, int) else -1)

    def _batch_accept(self) -> None:
        items = self.checked_review_items()
        if not items:
            items = [i for i in self._review_items if i["basis"] == "reviewed"]
        if items:
            self.batchAcceptRequested.emit(items)

    # ================= 更多菜单 / 项目动作 =================
    def _open_more(self) -> None:
        menu = QMenu(self)
        unsorted = self._selected == "unsorted"
        project = self.selected_project() or {}

        def add(label, slot, *, danger=False):
            action = menu.addAction(label)
            action.triggered.connect(lambda _c=False: slot())
            if danger:
                action.setProperty("danger", True)
            return action

        if unsorted:
            add(i18n.tr('导出…'), self._export_memory)
        else:
            add(i18n.tr('重命名…'), self._rename)
            add(i18n.tr('设置语言方向…') + f"    {_dir(project.get('direction_label') or '')}"
                if project.get("target_lang") else i18n.tr('设置语言方向…'), self._set_direction)
            menu.addSeparator()
            add(i18n.tr('导入记忆…'), self._import_memory)
            add(i18n.tr('导出记忆…'), self._export_memory)
            add(i18n.tr('把「未分类」的任务移进来…'), self._pull_unsorted)
            add(i18n.tr('清空翻译记忆…'), self._clear_tm)
            menu.addSeparator()
            add(i18n.tr('恢复') if project.get("archived") else i18n.tr('归档'), self._toggle_archive)
            add(i18n.tr('删除项目…'), self._delete, danger=True)
        self._menu = menu
        if bootstrap.automation():
            return
        menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def menu_labels(self) -> list[str]:
        """更多菜单里有哪些项（测试用，不弹出菜单）。"""
        unsorted = self._selected == "unsorted"
        project = self.selected_project() or {}
        if unsorted:
            return [i18n.tr('导出…')]
        return [i18n.tr('重命名…'), i18n.tr('设置语言方向…'), i18n.tr('导入记忆…'),
                i18n.tr('导出记忆…'), i18n.tr('把「未分类」的任务移进来…'),
                i18n.tr('清空翻译记忆…'),
                i18n.tr('恢复') if project.get("archived") else i18n.tr('归档'),
                i18n.tr('删除项目…')]

    def _mark_changed(self) -> None:
        self.changed = True
        self.projectsChanged.emit()

    def _ask(self, title: str, label: str, text: str = "") -> tuple[str, bool]:
        if bootstrap.automation():
            self._note(f"{title}：自动化运行不弹出输入框，请改用命令行或交互运行。")
            return "", False
        return prompts.TextInputDialog.get_text(self, title, label, text=text)

    def _create(self, values: dict | None = None) -> None:
        if not values:
            if bootstrap.automation():
                self._note(i18n.tr('新建项目：自动化运行不弹出输入框。'))
                return
            dialog = project_sheets.NewProjectDialog(self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            values = dialog.values()
        try:
            project = adapter.create_project(values["name"], source_lang=values.get("source_lang", ""),
                                             target_lang=values.get("target_lang", ""))
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法创建项目：{v0}', v0=exc))
            return
        self._selected = project["project_id"]
        self._tab = "overview"
        self._mark_changed()
        self.reloadRequested.emit()
        self._note(i18n.tr('已创建项目「{v0}」', v0=project.get("name")))

    def _rename(self) -> None:
        project = self.selected_project()
        if not project:
            return
        name, ok = self._ask(i18n.tr('重命名项目'), i18n.tr('新名称：'), project["name"])
        if not ok or not name.strip() or name.strip() == project["name"]:
            return
        try:
            adapter.rename_project(project["project_id"], name)
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法重命名：{v0}', v0=exc))
            return
        self._mark_changed()
        self.reloadRequested.emit()

    def _describe(self) -> None:
        project = self.selected_project()
        if not project:
            return
        text, ok = self._ask(i18n.tr('项目说明'), i18n.tr('说明（客户、领域、要求……）：'),
                             str(project.get("description") or ""))
        if not ok:
            return
        try:
            adapter.update_project_description(project["project_id"], text)
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法保存说明：{v0}', v0=exc))
            return
        self.reloadRequested.emit()

    def _set_direction(self, values: tuple[str, str] | None = None) -> None:
        project = self.selected_project()
        detail_project = self._detail.get("project") or {}
        if not project:
            return
        if not values:
            if bootstrap.automation():
                return
            dialog = project_sheets.DirectionDialog(
                detail_project.get("source_lang", ""), detail_project.get("target_lang", ""), self)
            if dialog.exec() != dialog.DialogCode.Accepted:
                return
            values = dialog.values()
        try:
            adapter.set_project_direction(project["project_id"], values[0], values[1])
        except adapter.AdapterError as exc:
            self._note(str(exc))
            return
        self._mark_changed()
        self.reloadRequested.emit()

    def _toggle_archive(self) -> None:
        project = self.selected_project()
        if not project:
            return
        try:
            adapter.archive_project(project["project_id"], archived=not project["archived"])
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法更改归档状态：{v0}', v0=exc))
            return
        self._mark_changed()
        self.reloadRequested.emit()

    def _delete(self) -> None:
        project = self.selected_project()
        if not project:
            return
        name = project["name"]
        if project["job_count"]:
            self._note(i18n.tr('「{v0}」下仍有 {v1} 个任务，不能删除；请先把这些任务移到其他项目或删除它们。',
                               v0=name, v1=project["job_count"]))
            return
        confirm, ok = self._ask(i18n.tr('删除项目'),
                                i18n.tr('删除项目「{v0}」不可撤销。\n\n请输入项目名称以确认：', v0=name))
        if not ok:
            return
        try:
            adapter.delete_project(project["project_id"], confirm_name=confirm)
        except adapter.AdapterError as exc:
            self._note(i18n.tr('无法删除：{v0}', v0=exc))
            return
        self._selected = ""
        self._mark_changed()
        self.reloadRequested.emit()
        self._note(i18n.tr('已删除项目「{v0}」；删除前已写入可恢复备份', v0=name))

    def _pull_unsorted(self) -> None:
        jobs = adapter.unsorted_jobs()
        if not jobs:
            self._note(i18n.tr('「未分类」里没有任务。'))
            return
        self.moveRequested.emit([j["job_id"] for j in jobs[:1]])

    def _clear_tm(self) -> None:
        project = self.selected_project()
        if not project:
            return
        if not bootstrap.automation() and not prompts.ConfirmDialog.ask(
                self, i18n.tr('清空翻译记忆'),
                i18n.tr('这会删除「{v0}」的全部翻译记忆，且不可恢复。其他项目不受影响。\n\n确定清空吗？',
                        v0=project["name"]),
                yes=i18n.tr('清空'), no=i18n.tr('取消'), tone="danger"):
            return
        adapter.clear_project_memory(project["project_id"])
        self.reloadRequested.emit()

    def _export_memory(self, path: str = "") -> None:
        key = self._selected
        if not key:
            return
        project = self.selected_project() or {"name": i18n.tr('未分类')}
        project_id = adapter.core.SYSTEM_PROJECT_ID if key == "unsorted" else key
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
            adapter.export_project_memory_to(project_id, path)
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
        self._note(i18n.tr('已导入到项目「{v0}」（只增不改；译名不一致的会放进待审核）。',
                           v0=result["project"].get("name") or ""))
        self._mark_changed()
        self.reloadRequested.emit()


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


class NewTermDialog(QDialog):
    """添加 / 编辑项目术语：写进项目的术语库（不是某个任务）。"""

    def __init__(self, project_name: str = "", *, source: str = "", target: str = "",
                 domain: str = "", note: str = "", locked_source: bool = False,
                 hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = project_sheets._sheet_base(
            self, i18n.tr('编辑术语') if locked_source else i18n.tr('添加术语'), 460)
        layout.addWidget(project_sheets._label(
            i18n.tr('编辑术语') if locked_source else i18n.tr('添加术语'), "card-title"))
        layout.addWidget(project_sheets._label(
            hint or i18n.tr('保存到项目「{v0}」的术语库；新任务会自动使用，已有任务不受影响。',
                            v0=project_name),
            "meta", wrap=True))
        self.source_edit = QLineEdit(source)
        self.source_edit.setReadOnly(locked_source)
        self.target_edit = QLineEdit(target)
        self.domain_edit = QLineEdit(domain)
        self.note_edit = QLineEdit(note)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for row, (label, edit) in enumerate((
                (i18n.tr('原术语'), self.source_edit), (i18n.tr('译名'), self.target_edit),
                (i18n.tr('领域（可选）'), self.domain_edit), (i18n.tr('备注（可选）'), self.note_edit))):
            grid.addWidget(project_sheets._label(label, "label"), row, 0)
            grid.addWidget(edit, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.error = project_sheets._label("", "status")
        self.error.setVisible(False)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(i18n.tr('保存') if locked_source else i18n.tr('添加术语'))
        style_module.mark_primary(ok)
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        (self.target_edit if locked_source else self.source_edit).setFocus()

    def _accept(self) -> None:
        if not self.source_edit.text().strip() or not self.target_edit.text().strip():
            self.error.setText(i18n.tr('请同时填写「原术语」和「译名」。'))
            self.error.setVisible(True)
            return
        self.accept()

    def values(self) -> dict:
        return {"source": self.source_edit.text().strip(), "target": self.target_edit.text().strip(),
                "domain": self.domain_edit.text().strip(), "note": self.note_edit.text().strip()}
