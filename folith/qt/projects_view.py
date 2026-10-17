"""项目页：左边项目列表，右边这个项目的内容（概览 / 术语库 / 翻译记忆 / 待审核）。

语言资产住在项目里：术语库和翻译记忆是项目的，待审核是项目里各任务发现的候选加上
"和术语库不一致"的待决冲突。「未分类」置顶，只汇总各任务自己的术语（只读），共享的翻译
记忆照旧，从不向新任务注入任何东西。

只做展示和发信号；读数据放在后台（窗口调适配层），写状态的是适配层。项目术语库的
增删改是几 KB 的小写入，直接在这里调适配层。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget, QWidgetAction,
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

TABS = (("overview", "概览"), ("terms", "术语库"), ("tm", "翻译记忆"))
UNSORTED_TABS = (("terms", "各任务的术语"), ("tm", "共享翻译记忆"))
#: 术语库里「待审核」的来源筛选。
BASIS_FILTERS = (("all", "全部来源"), ("task", "任务术语表"), ("reviewed", "已审校译文"),
                 ("machine", "机器译文"), ("conflict", "和术语库不一致"))
PAGE = 300                                  # 一次最多摆出的行数；筛选始终作用于全部


#: 单元格文字永远靠列的左缘（绝对左，不随文字方向翻转）；文字自己的方向不变。
LEADING = (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute
           | Qt.AlignmentFlag.AlignVCenter)


def _elide(text: str = "", role: str = "body") -> style_module.ElideLabel:
    """单行省略标签：长文件名省略显示，不撑开所在的行（宽度跟着容器走）。"""
    label = style_module.ElideLabel(text)
    label.setAlignment(LEADING)
    style_module.set_text_role(label, role)
    return label


class _Reflow(QWidget):
    """按宽度换行的网格：每格至少 ``min_item`` 宽，放不下就少放几列（2×2、竖排……）。

    内容宽度永远跟着视口走，不会撑出横向滚动条。``columns_for`` 可以限定允许的列数
    （例如四张统计卡只允许 4 / 2 / 1 列）。
    """

    def __init__(self, items: list[QWidget], *, min_item: int, spacing: int = 10,
                 weights: tuple[int, ...] = (), allowed: tuple[int, ...] = ()) -> None:
        super().__init__()
        self.setObjectName("transparent")
        self._items = items
        self._min_item = min_item
        self._weights = weights
        self._allowed = allowed or tuple(range(len(items), 0, -1))
        self._cols = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(spacing)
        self._grid.setVerticalSpacing(spacing)
        for item in items:
            item.setParent(self)
        self._arrange(10_000)

    def columns_for(self, width: int) -> int:
        fit = max(1, width // self._min_item)
        return next((c for c in sorted(self._allowed, reverse=True) if c <= fit), 1)

    def _arrange(self, width: int) -> None:
        cols = self.columns_for(width)
        if cols == self._cols:
            return
        self._cols = cols
        for item in self._items:
            self._grid.removeWidget(item)
        for column in range(len(self._items) + 1):
            self._grid.setColumnStretch(column, 0)
        for index, item in enumerate(self._items):
            self._grid.addWidget(item, index // cols, index % cols)
        for column in range(cols):
            weight = self._weights[column] if cols == len(self._items) and \
                column < len(self._weights) else 1
            self._grid.setColumnStretch(column, weight)

    def columns(self) -> int:
        return self._cols

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._arrange(event.size().width())


def _cell(text: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text or ""))
    item.setTextAlignment(LEADING)
    return item


def _text(text: str = "", role: str = "body", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    # 阿拉伯语等从右往左的文字在 QLabel 里默认靠右；列表里的术语列要和别的列一样靠左。
    label.setAlignment(LEADING)
    style_module.set_text_role(label, role)
    label.setWordWrap(wrap)
    return label


def _dir(label: str) -> str:
    """方向短写（"英 → 中"）：中文界面用单字，其它语言用语言代码。"""
    if i18n.language() == i18n.DEFAULT_LANGUAGE:
        return str(label)
    return " → ".join(assets.SHORT_LANGUAGE_CODES.get(part.strip()) or i18n.tr_dynamic(part.strip())
                      for part in str(label).split("→"))


def _scroll_area() -> QScrollArea:
    """透明的滚动区：viewport 不自己填底色，页面底色（或深色主题）才不会被一块白盖住。"""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.viewport().setAutoFillBackground(False)
    # 详情的内容宽度永远跟着视口走：不出横向滚动条。
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return area


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
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
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
        title = _elide(name, "name")
        self.meta = _elide(meta, "time")
        text.addWidget(title)
        text.addWidget(self.meta)
        row.addLayout(text, 1)
        if pending:
            row.addWidget(components.CountBadge(str(pending), "muted" if key == "unsorted" else "info"))
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
    #: 「用这个任务新建项目…」：直接打开「新建项目」那一页
    newFromTaskRequested = Signal(str)
    #: 清空这个项目的翻译记忆（确认框由窗口弹）
    clearMemoryRequested = Signal(str)
    #: 「把「未分类」的任务移进来…」：目标项目已选好
    pullRequested = Signal(str)
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
        self._fill_token = 0
        self._detail_cache: dict[tuple, dict] = {}     # 看过的项目详情：切回去时先显示它，再在后台刷新
        self._term_status = "all"          # 术语库的状态筛选：all / confirmed / pending
        self._keep_source = ""             # 确认等操作之后重新选中的那一行（按原文）
        self._term_rows: list[dict] = []
        self._review_items: list[dict] = []
        self._review_rows: dict[int, int] = {}
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
        nav.setFixedWidth(216)
        box = QVBoxLayout(nav)
        box.setContentsMargins(4, 12, 8, 8)
        box.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(10, 0, 0, 8)
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
        scroll = _scroll_area()
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
        self._detail_cache.clear()          # 列表刷新 = 有东西变了（确认、移动……）：重新读
        self._request_detail(use_cache=False)

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
        self._basis, self._show_singles, self._term_status = "all", False, "all"
        self._keep_source = ""
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
        box.setContentsMargins(20, 14, 12, 12)
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
        # 页签的最小宽度不参与窗口的最小宽度：窄窗口里内容跟着视口收缩，不撑出横向滚动。
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.pages.setMinimumWidth(0)
        self.page_overview = self._build_overview()
        self.page_terms = self._build_terms()
        self.page_tm = self._build_tm()
        self.page_unsorted_terms = self._build_unsorted_terms()
        for page in (self.page_overview, self.page_terms, self.page_tm,
                     self.page_unsorted_terms):
            self.pages.addWidget(page)
        box.addWidget(self.pages, 1)
        self.status = _text("", "meta", wrap=True)
        self.status.setVisible(False)          # 有消息时才占一行
        box.addWidget(self.status)
        self.empty = style_module.EmptyState(
            icon="folder", page=True, title=i18n.tr('还没有项目'),
            hint=i18n.tr('项目保存术语库和翻译记忆，新任务会自动用上。'),
            action=i18n.tr('新建项目…'))
        self.empty.actionTriggered.connect(self._create)
        box.addWidget(self.empty, 1)
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        return content

    def _request_detail(self, use_cache: bool = True) -> None:
        if not self._selected:
            self._render_empty()
            return
        key = (self._selected, self._basis, self._show_singles)
        cached = self._detail_cache.get(key) if use_cache else None
        if cached is not None:
            self._apply_detail_now(cached)       # 立刻显示上次的内容，后台再读一遍最新的
        self.detailRequested.emit(*key)

    def _render_empty(self) -> None:
        for widget in (self.tab_bar, self.pages, self.export_button, self.more_button):
            widget.setVisible(False)
        self.title.setText("")
        self.subtitle.setText("")
        self.empty.setVisible(True)

    def _note(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    # ---- 页签 -----------------------------------------------------------------
    def _tab_changed(self, key: str) -> None:
        if not key or key == self._tab:
            return
        self._tab = key
        self._show_tab()

    def _tab_options(self, detail: dict) -> list[tuple[str, str]]:
        unsorted = self._selected == "unsorted"
        counts = {
            "terms": int((detail.get("term_counts") or {}).get("all") or 0) if not unsorted
            else int(detail.get("term_total") or 0),
            "tm": len(detail.get("tm") or []),
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
                  "terms": self.page_unsorted_terms if unsorted else self.page_terms}[self._tab]
        self.pages.setCurrentWidget(target)

    # ================= 详情 =================
    def apply_detail(self, detail: dict) -> None:
        """后台读取完成（窗口已经丢掉过期的结果）。内容和已显示的一样就不重画，免得闪。"""
        key = (self._selected, self._basis, self._show_singles)
        same = self._detail_cache.get(key) == detail and self._detail is self._detail_cache.get(key)
        self._detail_cache[key] = detail
        if same:
            return
        self._apply_detail_now(detail)

    def _apply_detail_now(self, detail: dict) -> None:
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
        self.more_button.setVisible(not unsorted)     # 未分类的「更多」里只有导出，和旁边的按钮重复
        if unsorted:
            self.title.setText(i18n.tr('未分类'))
            count = int((self._overview.get("unsorted") or {}).get("job_count") or 0)
            self.subtitle.setText(i18n.tr('{v0} 个没有归入项目的任务', v0=count))
            self.export_button.setText(i18n.tr('导出…'))
            self._fill_unsorted_terms()
            self._fill_tm()
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
        self._show_tab()

    # ================= 概览 =================
    def _build_overview(self) -> QWidget:
        scroll = _scroll_area()
        holder = QWidget()
        holder.setObjectName("transparent")
        self.overview_box = QVBoxLayout(holder)
        self.overview_box.setContentsMargins(0, 0, 8, 0)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        """提示条，动作按钮在条内右端。warn = 琥珀色（浅底、琥珀图标、深琥珀字）。"""
        if tone != "warn":
            strip = style_module.NoticeStrip(text, tone=tone)
            strip.set_action(button, slot)
            style_module.set_button(strip.action_button(), "secondary", small=True)
            return strip
        tokens = theme_module.tokens(style_module.current_theme())
        strip = style_module.Surface(elevation="flat", fill="warning-soft")
        strip.RADIUS = theme_module.RADIUS["md"]
        row = QHBoxLayout(strip)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(10)
        row.addWidget(icons.IconLabel("alert", 16, "warning"), 0, Qt.AlignmentFlag.AlignVCenter)
        label = _text(text, "body", wrap=True)
        label.setStyleSheet(f"color: {tokens['warning']}; background: transparent;")
        row.addWidget(label, 1)
        action = QPushButton(button)
        style_module.set_button(action, "secondary", small=True)
        action.clicked.connect(slot)
        row.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        return strip

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
        review_total = int((detail.get("term_counts") or {}).get("pending") or 0)
        stats = (
            (i18n.tr('术语库'), str(len(detail.get("terms") or [])), i18n.tr('已确认，新任务自动使用'), "terms"),
            (i18n.tr('翻译记忆'), str(len(detail.get("tm") or [])), i18n.tr('已审校的句对'), "tm"),
            (i18n.tr('待审核'), str(review_total),
             i18n.tr('来自 {v0} 个任务', v0=detail.get("review_tasks", 0)), "pending"),
            (i18n.tr('风格规则'), str(detail.get("style_rule_count", 0)), i18n.tr('审校中确认'), ""))
        self.stat_grid = _Reflow([self._stat_tile(*stat) for stat in stats],
                                 min_item=150, allowed=(4, 2, 1))
        self.overview_box.addWidget(self.stat_grid)
        conflicts = int(detail.get("conflict_count") or 0)
        if conflicts:
            self.overview_box.addWidget(self._notice_row(
                i18n.tr('有 {v0} 条术语的译名和术语库里的不一致，等你决定用哪一个。', v0=conflicts),
                i18n.tr('逐条处理'), self._open_conflicts, "warn"))
        self.card_grid = _Reflow([self._tasks_card(), self._log_card()], min_item=360,
                                 spacing=14, weights=(3, 2), allowed=(2, 1))
        self.overview_box.addWidget(self.card_grid)
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
            tile.mousePressEvent = lambda _e, key=target: self._open_terms(
                "pending" if key == "pending" else "all")
        if target == "tm":
            tile.mousePressEvent = lambda _e: self.tab_bar.set_value("tm") or self._tab_changed("tm")
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
            name = _elide(task["name"], "body")
            row.addWidget(name, 1)
            tone = ("error" if task.get("runtime_status") == "failed" else
                    "active" if task.get("active") else
                    "done" if "交付" in str(task.get("status")) else "idle")
            status = components.StatusLabel(
                i18n.tr_dynamic(str(task.get("status") or "")) or i18n.tr('未开始'), tone)
            status.setMinimumWidth(96)
            row.addWidget(status)
            open_button = QPushButton(i18n.tr('打开'))
            style_module.set_button(open_button, "plain", small=True)
            open_button.clicked.connect(lambda _c=False, j=task["job_id"]: self.openJobRequested.emit(j, -1))
            row.addWidget(open_button)
            box.addLayout(row)
        if len(tasks) > 8:
            box.addWidget(_text(i18n.tr('还有 {v0} 个，在任务库中查看', v0=len(tasks) - 8), "time"))
        box.addStretch(1)
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
        box.addStretch(1)
        return card

    def _open_terms(self, status: str, basis: str = "all") -> None:
        """跳到术语库，并打开某个状态 / 来源筛选（「待审核」统计卡、冲突提示条用）。"""
        self._tab, self._term_status = "terms", status
        if basis != self._basis:
            self._basis = basis
            self._request_detail()
            return
        self.tab_bar.set_value("terms")
        self._fill_terms()
        self._show_tab()

    def _open_conflicts(self) -> None:
        self._open_terms("pending", "conflict")

    # ================= 术语库（已确认 + 待审核，一张表） =================
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
        self.term_import = QPushButton(i18n.tr('从文件导入…'))
        self.term_import.setToolTip(i18n.tr('导入别处导出的项目记忆文件（只增不改）'))
        self.term_import.clicked.connect(lambda: self._import_memory())
        self.term_add = QPushButton(i18n.tr('添加术语'))
        style_module.set_button(self.term_add, "secondary")
        self.term_add.setIcon(icons.icon("plus", 16, "text-secondary"))
        self.term_add.clicked.connect(lambda: self._add_term())
        bar.addWidget(self.term_import)
        self.batch_button = QPushButton(i18n.tr('批量确认…'))
        self.batch_button.clicked.connect(self._batch_accept)
        bar.addWidget(self.batch_button)
        bar.addWidget(self.term_add)
        box.addLayout(bar)

        # 工具条：状态筛选、来源下拉（只在「待审核」）、右侧「显示只出现过 1 次」和「批量确认…」。
        # 宽度不够时右侧那组换到下一行，不挤压表格以外的任何东西。
        self.toolbar = QGridLayout()
        self.toolbar.setContentsMargins(0, 0, 0, 0)
        self.toolbar.setHorizontalSpacing(8)
        self.toolbar.setVerticalSpacing(6)
        self.term_filter = style_module.SegmentedControl([("all", "")])
        self.term_filter.changed.connect(self._term_filter_changed)
        self.basis_combo = style_module.ComboBox()
        self.basis_combo.setAccessibleName(i18n.tr('待审核的来源'))
        self.basis_combo.activated.connect(self._combo_activated)
        self.singles_button = QPushButton("")
        style_module.set_button(self.singles_button, "plain", small=True)
        self.singles_button.clicked.connect(self._toggle_singles)
        self.pending_tools = QWidget()
        self.pending_tools.setObjectName("transparent")
        tools = QHBoxLayout(self.pending_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(8)
        tools.addWidget(self.singles_button)
        self.toolbar.addWidget(self.term_filter, 0, 0)
        self.toolbar.addWidget(self.basis_combo, 0, 1)
        self.toolbar.setColumnStretch(2, 1)
        self._toolbar_narrow = None
        self._arrange_toolbar(False)
        box.addLayout(self.toolbar)
        self.term_note = _text("", "time", wrap=True)     # 只在没有已确认术语时显示一行

        self.term_table = _table(["", i18n.tr('原文'), i18n.tr('译名'), i18n.tr('状态'),
                                  i18n.tr('来源'), i18n.tr('出现')])
        header = self.term_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.term_table.setColumnWidth(0, 40)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.term_table.setColumnWidth(3, 84)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.term_table.setColumnWidth(4, 132)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.term_table.itemSelectionChanged.connect(self._term_selected)
        box.addWidget(self.term_table, 1)
        box.addWidget(self.term_note)
        self.term_none = _text(i18n.tr('没有符合条件的术语。'), "meta")
        box.addWidget(self.term_none)

        # 已确认的行：详情 + 编辑 / 移除。
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

        # 待审核的行：译名可改，确认 / 只用于这个任务 / 不是术语。
        self.review_detail = style_module.Card()
        review = QVBoxLayout(self.review_detail)
        review.setContentsMargins(16, 8, 16, 8)
        review.setSpacing(4)
        self.look_button = QPushButton(i18n.tr('看原文'))
        style_module.set_button(self.look_button, "plain", small=True)
        head = QHBoxLayout()
        self.detail_title = _text("", "name")
        self.detail_meta = _elide("", "time")
        head.addWidget(self.detail_title)
        head.addWidget(self.detail_meta, 1)
        head.addWidget(self.look_button)
        review.addLayout(head)
        self.detail_context = _text("", "body", wrap=True)
        self.detail_context.setTextFormat(Qt.TextFormat.RichText)
        review.addWidget(self.detail_context)
        self.name_edit = QLineEdit()
        self.name_edit.setMinimumWidth(80)
        self.name_edit.setPlaceholderText(i18n.tr('译名'))
        self.name_edit.setAccessibleName(i18n.tr('译名'))
        self.accept_button = QPushButton(i18n.tr('确认'))
        style_module.set_button(self.accept_button, "primary", small=True)
        self.task_button = QPushButton(i18n.tr('只用于这个任务'))
        style_module.set_button(self.task_button, "secondary", small=True)
        self.reject_button = QPushButton(i18n.tr('不是术语'))
        style_module.set_button(self.reject_button, "danger", small=True)
        self.accept_button.clicked.connect(self._accept_current)
        self.task_button.clicked.connect(lambda: self._decide_current("task_only"))
        self.reject_button.clicked.connect(lambda: self._decide_current("rejected"))
        self.look_button.clicked.connect(self._look)
        self.review_actions = QGridLayout()
        self.review_actions.setContentsMargins(0, 0, 0, 0)
        self.review_actions.setHorizontalSpacing(8)
        self.review_actions.setVerticalSpacing(6)
        self._actions_narrow = None
        self._arrange_actions(False)
        review.addLayout(self.review_actions)
        box.addWidget(self.review_detail)

        self.term_empty = style_module.EmptyState(
            icon="termbase", page=True, title=i18n.tr('术语库是空的'),
            hint=i18n.tr('任务里出现的术语会先在这里等你确认；也可以手动添加。'),
            action=i18n.tr('添加术语…'))
        self.term_empty.actionTriggered.connect(lambda: self._add_term())
        box.addWidget(self.term_empty, 1)
        return page

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        width = event.size().width()
        # 窄窗口：「来源」让位给原文 / 译名（来源在下方的详情里仍可看到）。
        self.term_table.setColumnHidden(4, width < 216 + 620)
        narrow = width < 216 + 20 + 12 + 640
        self._arrange_toolbar(narrow)
        # 窄页：「从文件导入…」让位给「批量确认…」（导入仍在「更多」菜单里）。
        self.term_import.setVisible(not narrow)

    def _arrange_toolbar(self, narrow: bool) -> None:
        if narrow == self._toolbar_narrow:
            return
        first = self._toolbar_narrow is None
        self._toolbar_narrow = narrow
        if first:
            self.toolbar.addWidget(self.pending_tools, 0, 3, Qt.AlignmentFlag.AlignRight)
        elif self._detail:
            self._fill_terms()          # 来源下拉在窄页上不带数字

    def _arrange_actions(self, narrow: bool) -> None:
        """详情的动作永远一行：译名、确认、只用于这个任务、不是术语；「看原文」在标题行右端。"""
        if self._actions_narrow is not None:
            return
        self._actions_narrow = False
        grid = self.review_actions
        for column, widget in enumerate((self.name_edit, self.accept_button, self.task_button,
                                         self.reject_button)):
            grid.addWidget(widget, 0, column)
        grid.setColumnStretch(0, 1)

    def _combo_activated(self, _index: int) -> None:
        key = self.basis_combo.currentData()
        if key == "singles":
            self._toggle_singles()
        else:
            self._basis_changed(key)

    def _term_filter_changed(self, key: str) -> None:
        if not key or key == self._term_status:
            return
        self._term_status = key
        if key != "pending" and self._basis != "all":
            self._basis = "all"
            self._request_detail()
            return
        self._fill_terms()

    def _term_entries(self) -> list[dict]:
        """表里的全部行（未筛选）：已确认的术语 + 待审核项，按原文排序，确认后行不会跳位。"""
        entries = [{"kind": "confirmed", "source": t["source"], "term": t}
                   for t in self._detail.get("terms") or []]
        entries += [{"kind": "pending", "source": i["source"], "item": i}
                    for i in (self._detail.get("review") or {}).get("items") or []]
        entries.sort(key=lambda e: (e["source"].casefold(), e["kind"]))
        return entries

    def _fill_terms(self) -> None:
        detail = self._detail
        counts = detail.get("term_counts") or {}
        review = detail.get("review") or {}
        confirmed, pending = int(counts.get("confirmed") or 0), int(counts.get("pending") or 0)
        options = [("all", i18n.tr('全部') + f"  {confirmed + pending}"),
                   ("confirmed", i18n.tr('已确认') + f"  {confirmed}"),
                   ("pending", i18n.tr('待审核') + f"  {pending}")]
        self.term_filter.set_options(options)
        self.term_filter.set_value(self._term_status)
        # 来源下拉：只在看「待审核」时出现，数字取自收起之后的各来源条数。
        self.basis_combo.blockSignals(True)
        self.basis_combo.clear()
        by_basis = review.get("counts") or {}
        for key, label in BASIS_FILTERS:
            self.basis_combo.addItem(
                i18n.tr_dynamic(label) + ("" if self._toolbar_narrow else f"  {by_basis.get(key, 0)}"),
                key)
        collapsed = int(review.get("collapsed") or 0)
        if self._toolbar_narrow and (collapsed or self._show_singles):
            # 窄页放不下独立的链接：同一个开关放进来源下拉的最后一项。
            self.basis_combo.insertSeparator(self.basis_combo.count())
            self.basis_combo.addItem(
                i18n.tr('显示只出现过 1 次的 {v0} 条', v0=collapsed) if not self._show_singles
                else i18n.tr('收起只出现过 1 次的'), "singles")
        self.basis_combo.setCurrentIndex(max(0, self.basis_combo.findData(self._basis)))
        self.basis_combo.blockSignals(False)
        in_pending = self._term_status == "pending"
        self.basis_combo.setVisible(in_pending)
        self.singles_button.setVisible((bool(collapsed) or self._show_singles) and not self._toolbar_narrow)
        self.singles_button.setText(
            i18n.tr('显示只出现过 1 次的 {v0} 条', v0=collapsed) if not self._show_singles
            else i18n.tr('收起只出现过 1 次的'))
        project = detail.get("project") or {}
        hint = i18n.tr('已确认的术语会自动用于新任务（{v0}）；待审核的确认后才会用',
                       v0=_dir(project.get("direction_label") or i18n.tr('未设定')))
        self.term_note.setText(hint)
        self.term_filter.setToolTip(hint)       # 平时只在筛选条上悬停时出现，不占一行

        needle = self.term_query.text().strip().casefold()
        status = self._term_status
        rows = []
        for entry in self._term_entries():
            if status != "all" and entry["kind"] != status:
                continue
            if needle:
                data = entry.get("term") or entry["item"]
                hay = " ".join([entry["source"], str(data.get("target") or ""),
                                str(data.get("domain") or "")]).casefold()
                if needle not in hay:
                    continue
            rows.append(entry)
        keep = self._keep_source
        self._term_rows = rows[:PAGE]
        self._review_items = [e["item"] for e in self._term_rows if e["kind"] == "pending"]
        self._review_rows = {}
        table = self.term_table
        table.blockSignals(True)
        table.setRowCount(len(self._term_rows))
        # 行里的芯片 / 复选框是真控件，几百行一次建完会卡一下：先建能看到的（和要选中的那一行），
        # 其余在空闲时分批补上。切到别的项目 / 再次刷新时，没补完的批次作废。
        self._fill_token += 1
        target = next((i for i, e in enumerate(self._term_rows) if e["source"] == keep), 0)
        first = max(self.FIRST_ROWS, target + 1)
        for r in range(min(first, len(self._term_rows))):
            self._fill_term_row(r, self._term_rows[r])
        self._fill_rest(first, self._fill_token)
        table.blockSignals(False)
        has_any = bool(confirmed or pending or collapsed)
        for widget in (table, self.term_query, self.term_filter):
            widget.setVisible(has_any)
        self.term_note.setVisible(has_any and not confirmed)    # 还没有已确认的术语时才占一行
        self.term_empty.setVisible(not has_any)
        self.term_none.setVisible(has_any and not self._term_rows)
        self.batch_button.setVisible(any(e["kind"] == "pending" for e in self._term_rows)
                                     and bool(project))
        self.pending_tools.setVisible(in_pending and self.singles_button.isVisibleTo(self.pending_tools))
        if self._term_rows:
            index = next((i for i, e in enumerate(self._term_rows) if e["source"] == keep), 0)
            table.selectRow(index)
        self._keep_source = ""
        self._term_selected()
        self._sync_batch()

    FIRST_ROWS = 40
    ROW_CHUNK = 60

    def _fill_rest(self, start: int, token: int) -> None:
        if start >= len(self._term_rows):
            return

        def step(at=start) -> None:
            if token != self._fill_token:
                return
            end = min(at + self.ROW_CHUNK, len(self._term_rows))
            table = self.term_table
            table.setUpdatesEnabled(False)
            for r in range(at, end):
                self._fill_term_row(r, self._term_rows[r])
            table.setUpdatesEnabled(True)
            if end < len(self._term_rows):
                QTimer.singleShot(0, lambda: step(end))
        QTimer.singleShot(0, step)

    def flush_rows(self) -> None:
        """把还没补上的行立刻补完（测试、导出用）。"""
        self._fill_token += 1
        for r in range(len(self._term_rows)):
            if self.term_table.item(r, 1) is None:
                self._fill_term_row(r, self._term_rows[r])

    def _fill_term_row(self, r: int, entry: dict) -> None:
        table = self.term_table
        pending = entry["kind"] == "pending"
        if pending:
            item = entry["item"]
            source = item["source"] + (
                "  · " + " · ".join(item["variants"]) if item.get("variants") else "")
            check = style_module.CheckBox("")
            check.setFixedSize(20, 20)
            check.setAccessibleName(i18n.tr('选择 {v0}', v0=item["source"]))
            check.toggled.connect(lambda _c: self._sync_batch())
            holder = QWidget()
            holder.setObjectName("transparent")
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(10, 0, 0, 0)
            lay.addWidget(check)
            table.setCellWidget(r, 0, holder)
            self._review_rows[r] = self._review_items.index(item)
            target, tone = item["target"], {"conflict": "warn"}.get(item["basis"], "info")
            status, origin = i18n.tr('待审核'), i18n.tr_dynamic(item["basis_label"])
            if item["basis"] == "conflict":
                status = i18n.tr('待审核')
            hits = str(item["hits"]) if item.get("positions_known") or item.get("kind") != "conflict" else "—"
        else:
            term = entry["term"]
            source, target, tone = term["source"], term["target"], "ok"
            status, origin = i18n.tr('已确认'), i18n.tr_dynamic(term["origin_text"])
            hits = str(term["used_total"]) if term["used_tasks"] else "—"
        table.setItem(r, 0, QTableWidgetItem(""))
        table.setItem(r, 1, _cell(source))
        table.setItem(r, 2, _cell(target))
        chip_host = QWidget()
        chip_host.setObjectName("transparent")
        chip_lay = QHBoxLayout(chip_host)
        chip_lay.setContentsMargins(0, 0, 6, 0)
        chip_lay.addWidget(style_module.Chip(status, tone))
        chip_lay.addStretch(1)
        table.setCellWidget(r, 3, chip_host)
        table.setItem(r, 3, QTableWidgetItem(""))
        table.setItem(r, 4, _cell(origin))
        count = QTableWidgetItem(hits)
        count.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        table.setItem(r, 5, count)

    def selected_entry(self) -> dict:
        row = self.term_table.currentRow()
        return self._term_rows[row] if 0 <= row < len(self._term_rows) else {}

    def selected_term(self) -> dict:
        entry = self.selected_entry()
        return entry.get("term") or {} if entry.get("kind") == "confirmed" else {}

    def selected_review_item(self) -> dict:
        entry = self.selected_entry()
        return entry.get("item") or {} if entry.get("kind") == "pending" else {}

    def checked_review_items(self) -> list[dict]:
        table = self.term_table
        out = []
        for row, index in self._review_rows.items():
            holder = table.cellWidget(row, 0)
            box = holder.findChild(style_module.CheckBox) if holder else None
            if box is not None and box.isChecked():
                out.append(self._review_items[index])
        return out

    def _sync_batch(self) -> None:
        count = len(self.checked_review_items())
        self.batch_button.setText(i18n.tr('批量确认 {v0} 条…', v0=count) if count
                                  else i18n.tr('批量确认…'))

    def _term_selected(self) -> None:
        self._clear(self.term_detail_body)
        entry = self.selected_entry()
        confirmed = entry.get("kind") == "confirmed"
        pending = entry.get("kind") == "pending"
        self.term_detail.setVisible(confirmed)
        self.review_detail.setVisible(pending)
        if pending:
            self._review_selected()
        if not confirmed:
            return
        term = entry["term"]
        origin = i18n.tr_dynamic(term["origin_text"])
        if term["origin_job"]:
            origin += " · " + term["origin_job"].rsplit(".", 1)[0]
        lines = [(i18n.tr('怎么来的'), origin)]
        if term["domain"]:
            lines.append((i18n.tr('领域'), term["domain"]))
        if term["forbidden"]:
            lines.append((i18n.tr('不要译成'), "、".join(term["forbidden"])))
        if term["note"]:
            lines.append((i18n.tr('备注'), term["note"]))
        if term["used_tasks"]:
            lines.append((i18n.tr('出现在'), "　".join(
                f"{name.rsplit('.', 1)[0]} · {i18n.tr('{v0} 处', v0=n)}" for name, n in term["used_tasks"])))
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
        # 翻译记忆存储里没有条目时间戳，所以不放「更新」列（空列比没有更糟）。
        self.tm_table = _table([i18n.tr('原文'), i18n.tr('译文'), i18n.tr('目标语言')])
        tm_header = self.tm_table.horizontalHeader()
        tm_header.setStretchLastSection(False)
        tm_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tm_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tm_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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
                      row.get("target_lang") or i18n.tr('未标注（不会自动复用）'))
            for c, value in enumerate(values):
                self.tm_table.setItem(r, c, _cell(value))
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
        scroll = _scroll_area()
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
        collapsed = int((self._detail.get("review") or {}).get("collapsed") or 0)
        if collapsed or self._show_singles:
            self.unsorted_singles = QPushButton(
                i18n.tr('显示只出现过 1 次的 {v0} 条', v0=collapsed) if not self._show_singles
                else i18n.tr('收起只出现过 1 次的'))
            style_module.set_button(self.unsorted_singles, "plain", small=True)
            self.unsorted_singles.clicked.connect(self._toggle_singles)
            row = QHBoxLayout()
            row.addWidget(self.unsorted_singles)
            row.addStretch(1)
            self.unsorted_box.addLayout(row)
        tasks = self._detail.get("tasks") or []
        if not tasks:
            self.unsorted_box.addWidget(style_module.EmptyState(
                icon="inbox", page=True, title=i18n.tr('没有未归属的任务'),
                hint=i18n.tr('导入文档时没有选项目的任务会先放在这里。')))
        # 各卡片共用一套列规则（状态 = 芯片宽、出现 = 窄列右对齐、动作列只有存在动作时才占宽，
        # 原文 / 译名平分剩下的空间），所以整页对齐；宽度全部跟着视口，不设固定像素。
        has_action = any(t["status"] == "pending" for task in tasks for t in task["terms"])
        self.unsorted_box.addStretch(1)
        # 卡片是真控件，任务多时一次建完会卡一下：先建前几张，其余在空闲时分批补上。
        self._fill_token += 1
        token = self._fill_token
        first = 3
        for task in tasks[:first]:
            self.unsorted_box.insertWidget(self.unsorted_box.count() - 1,
                                           self._unsorted_card(task, has_action))

        def more(at: int = first) -> None:
            if token != self._fill_token or at >= len(tasks):
                return
            for task in tasks[at:at + 3]:
                self.unsorted_box.insertWidget(self.unsorted_box.count() - 1,
                                               self._unsorted_card(task, has_action))
            QTimer.singleShot(0, lambda: more(at + 3))
        if len(tasks) > first:
            QTimer.singleShot(0, more)

    def _unsorted_card(self, task: dict, has_action: bool = False) -> QWidget:
        card = _TaskCard()
        box = QVBoxLayout(card)
        box.setContentsMargins(0, 0, 0, 6)
        box.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(16, 10, 14, 6)
        head.setSpacing(8)
        head.addWidget(icons.IconLabel("file-text", 16, "text-secondary"))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(_elide(task["name"], "name"))
        names.addWidget(_elide(
            f"{_dir(task['direction_label'])} · "
            + i18n.tr('{v0} 条术语，{v1} 条已确认', v0=task["term_count"], v1=task["locked_count"]),
            "time"))
        head.addLayout(names, 1)
        open_button = QPushButton(i18n.tr('打开任务'))
        style_module.set_button(open_button, "plain", small=True)
        open_button.clicked.connect(lambda _c=False, j=task["job_id"]: self.openJobRequested.emit(j, -1))
        head.addWidget(open_button)
        make = QPushButton(i18n.tr('用这个任务新建项目…'))
        style_module.set_button(make, "secondary", small=True)
        make.setIcon(icons.icon("folder", 14, "accent"))
        card.make_button = make        # 卡片太窄时变成只有图标的按钮（带提示）
        make.setToolTip(i18n.tr('用这个任务新建项目…'))
        make.clicked.connect(lambda _c=False, j=task["job_id"]: self.newFromTaskRequested.emit(j))
        head.addWidget(make)
        box.addLayout(head)
        grid = QGridLayout()
        grid.setContentsMargins(16, 4, 16, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        chip_width = style_module.Chip(i18n.tr('已确认')).sizeHint().width() + 4
        for column, text in enumerate((i18n.tr('原文'), i18n.tr('译名'), i18n.tr('状态'), i18n.tr('出现'))):
            title = _text(text, "time")
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if column == 3:
                title.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(title, 0, column)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(2, chip_width)
        grid.setColumnMinimumWidth(3, 40)
        card.grid, card.has_action = grid, has_action
        if has_action:
            grid.addWidget(QWidget(), 0, 4)       # 动作列在每张卡里都存在，宽度才一致
            grid.setColumnMinimumWidth(4, card.action_width(False))
        for r, term in enumerate(task["terms"], start=1):
            for column, (text, role) in enumerate(((term["source"], "name"), (term["target"], "body"))):
                label = _elide(text, role)
                # 文字长短不参与列宽：整页所有卡片的列起点一致。
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                grid.addWidget(label, r, column)
            # 和项目术语库同一套说法：锁定 = 已确认；暂定和智能体候选 = 待审核。
            confirmed = term["status"] == "locked"
            chip_host = QWidget()
            chip_host.setObjectName("transparent")
            chip_lay = QHBoxLayout(chip_host)
            chip_lay.setContentsMargins(0, 0, 0, 0)
            chip = style_module.Chip(i18n.tr('已确认') if confirmed else i18n.tr('待审核'),
                                     "ok" if confirmed else "info")
            if term["status"] == "pending":
                chip.setToolTip(i18n.tr_dynamic(term.get("basis_label") or ""))
            chip_lay.addWidget(chip)
            chip_lay.addStretch(1)
            grid.addWidget(chip_host, r, 2)
            grid.addWidget(_text(str(term["hits"]), "meta"), r, 3, Qt.AlignmentFlag.AlignRight)
            if term["status"] == "pending":
                keep = QPushButton(i18n.tr('只用于这个任务'))
                style_module.set_button(keep, "plain", small=True)
                keep.setToolTip(i18n.tr('只用于这个任务'))
                card.keep_buttons.append(keep)
                keep.clicked.connect(lambda _c=False, i=term["item"]: self.decideRequested.emit(i, "task_only"))
                grid.addWidget(keep, r, 4, Qt.AlignmentFlag.AlignRight)
        box.addLayout(grid)
        if task["more"]:
            box.addWidget(_text("  " + i18n.tr('还有 {v0} 条', v0=task["more"]), "time"))
        return card

    # ================= 待审核行的动作（都在术语库的行与详情里） =================
    def _basis_changed(self, key: str) -> None:
        if key and key != self._basis:
            self._basis = key
            self._keep_source = self.selected_entry().get("source", "")
            self._request_detail()

    def _toggle_singles(self) -> None:
        self._show_singles = not self._show_singles
        self._request_detail()

    def _review_selected(self) -> None:
        item = self.selected_review_item()
        self.review_detail.setVisible(bool(item))
        if not item:
            return
        conflict = item.get("basis") == "conflict"
        task_term = item.get("kind") == "task_term"
        documents = [d.rsplit(".", 1)[0] for d in item.get("documents") or []]
        meta = []
        if item.get("variants"):
            meta.append(i18n.tr('也写作 {v0}', v0=" / ".join(item["variants"])))
        if item.get("kind") != "conflict" or item.get("positions_known"):
            meta.append(i18n.tr('{v0} 处', v0=item["hits"]))
        meta.append(i18n.tr('来自{v0}', v0=i18n.tr_dynamic(item["basis_label"]))
                    if not conflict else i18n.tr_dynamic(item["basis_label"]))
        if len(documents) == 1:
            meta.append(documents[0])
        elif documents:
            meta.append(i18n.tr('{v0} 个任务', v0=len(documents)))
        self.detail_title.setText(item["source"])
        self.detail_meta.setText(" · ".join(meta))
        context = ""
        if conflict:
            context = i18n.tr('项目术语库里是「{v0}」，这里建议「{v1}」。确认会改用新译名；保留则什么都不变。',
                              v0=item.get("local", ""), v1=item["target"])
        elif task_term:
            context = i18n.tr('这是任务自己术语表里的条目，确认后才会进入项目术语库、用于新任务。')
        elif item.get("source_context"):
            context = f"{_escape(item['source_context'])} → {_escape(item.get('target_context') or '')}"
            context = context.replace(_escape(item["source"]), f"<b>{_escape(item['source'])}</b>")
            context = context.replace(_escape(item["target"]), f"<b>{_escape(item['target'])}</b>")
        self.detail_context.setText(context)
        self.detail_context.setVisible(bool(context))
        self.name_edit.setText(item["target"])
        self.name_edit.setVisible(not conflict)
        if conflict:
            self.accept_button.setText(i18n.tr('改用「{v0}」', v0=item["target"]))
            self.task_button.setText(i18n.tr('保留原译名'))
        else:
            self.accept_button.setText(i18n.tr('确认'))
            self.task_button.setText(i18n.tr('只用于这个任务'))
        # 任务术语本来就只在这个任务里，没有「只用于这个任务」可选。
        self.task_button.setVisible(not task_term)
        self.reject_button.setVisible(not conflict)
        self.look_button.setVisible(bool(item.get("job_id")) and not conflict)

    def _accept_current(self) -> None:
        item = self.selected_review_item()
        if item:
            self._keep_source = item["source"]
            self._mark_confirmed(self.term_table.currentRow())      # 立刻让这一行变成已确认
            self.acceptRequested.emit(item, self.name_edit.text().strip())

    def _mark_confirmed(self, row: int) -> None:
        """乐观更新：确认之后这一行马上显示「已确认」；真正的数据随后台刷新校正。"""
        entry = self.selected_entry()
        if entry.get("kind") != "pending":
            return
        host = self.term_table.cellWidget(row, 3)
        chip = host.findChild(style_module.Chip) if host else None
        if chip is not None:
            chip.setText(i18n.tr('已确认'))
            chip.set_tone("ok")
        origin = self.term_table.item(row, 4)
        if origin is not None:
            origin.setText(i18n.tr('审核收进'))

    def _decide_current(self, decision: str) -> None:
        item = self.selected_review_item()
        if item:
            self._keep_source = item["source"]
            self.decideRequested.emit(item, "keep" if item.get("kind") == "conflict" else decision)

    def _look(self) -> None:
        item = self.selected_review_item()
        if not item or not item.get("job_id"):
            return
        member = (item.get("members") or [item])[0]
        entries = member.get("position_entries") or []
        index = entries[0]["index"] if entries else member.get("first_observed_segment")
        if index is None and member.get("occurrences"):
            index = member["occurrences"][0]
        self.openJobRequested.emit(str(member.get("job_id") or item["job_id"]),
                                   int(index) if isinstance(index, int) else -1)

    def _batch_accept(self) -> None:
        items = self.checked_review_items()
        if not items:
            # 没勾选时：把依据可靠的（已审校译文、任务术语表）一起确认；机器译文要自己勾。
            items = [i for i in self._review_items if i["basis"] in ("reviewed", "task")]
        if items:
            self.batchAcceptRequested.emit(items)

    # ================= 更多菜单 / 项目动作 =================
    def _open_more(self) -> None:
        menu = QMenu(self)
        unsorted = self._selected == "unsorted"
        project = self.selected_project() or {}

        def add(label, slot, *, danger=False):
            if danger:
                # 原生菜单项画不出红色：用带样式的标签做 QWidgetAction（点击、回车、空格都能触发）。
                action = _DangerAction(label, menu)
                menu.addAction(action)
            else:
                action = menu.addAction(label)
            action.triggered.connect(lambda _c=False: slot())
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
        project = self.selected_project()
        if not project:
            return
        if not adapter.unsorted_jobs():
            self._note(i18n.tr('「未分类」里没有任务。'))
            return
        self.pullRequested.emit(project["project_id"])

    def _clear_tm(self) -> None:
        project = self.selected_project()
        if project:
            self.clearMemoryRequested.emit(project["project_id"])

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


class _TaskCard(style_module.Card):
    """未分类里的任务卡。窄时（约 1000 宽的窗口）：「用这个任务新建项目…」和行内的
    「只用于这个任务」收成只有图标的按钮（带提示），标题行与列宽都不再挤。"""

    NARROW = 520

    def __init__(self) -> None:
        super().__init__()
        self.make_button: QPushButton | None = None
        self.keep_buttons: list[QPushButton] = []
        self.grid: QGridLayout | None = None
        self.has_action = False
        self._compact: bool | None = None

    def action_width(self, compact: bool) -> int:
        sample = QPushButton("" if compact else i18n.tr('只用于这个任务'))
        if compact:
            sample.setIcon(icons.icon("check", 14, "accent"))
        style_module.set_button(sample, "plain", small=True)
        return sample.sizeHint().width()

    def apply(self, compact: bool) -> None:
        self._compact = compact
        if self.make_button is not None:
            self.make_button.setText("" if compact else self.make_button.toolTip())
        for button in self.keep_buttons:
            button.setText("" if compact else button.toolTip())
            button.setIcon(icons.icon("check", 14, "accent") if compact else QIcon())
        if self.grid is not None and self.has_action:
            self.grid.setColumnMinimumWidth(4, self.action_width(compact))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        compact = event.size().width() < self.NARROW
        if compact != self._compact:
            self.apply(compact)


class _DangerLabel(QLabel):
    """菜单里的危险项：danger 色文字，悬停 / 键盘焦点时 danger-soft 底。"""

    activated = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paint(False)

    def _paint(self, active: bool) -> None:
        tokens = theme_module.tokens(style_module.current_theme())
        background = tokens["danger-soft"] if active else "transparent"
        self.setStyleSheet(f"QLabel {{ color: {tokens['danger']}; background: {background};"
                           " padding: 5px 24px 5px 22px; border-radius: 4px; }")

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._paint(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._paint(self.hasFocus())
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._paint(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._paint(False)
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.activated.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
        else:
            super().keyPressEvent(event)


class _DangerAction(QWidgetAction):
    def __init__(self, text: str, parent) -> None:
        super().__init__(parent)
        self.setText(text)
        self._label = _DangerLabel(text)
        self.setDefaultWidget(self._label)
        self._label.activated.connect(self._fire)

    def _fire(self) -> None:
        menu = self.parent()
        if menu is not None:
            menu.close()
        self.trigger()


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
