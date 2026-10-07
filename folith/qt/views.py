"""Home (task library) and workbench (multi-segment editing) views.

Design rules that come from the Beta acceptance list:

* the segment list is a *virtual* list: one editor exists for the current
  segment, so a 200-segment or 10,000-character document does not create
  hundreds of heavy edit widgets;
* a background refresh never rebuilds the editor that is being typed into, and
  never clears the selection, the cursor, the undo history or the uncommitted
  text;
* an uncommitted edit is written to the core's draft file (so a crash, a task
  switch or a quit cannot lose it) and is restored when the segment is reopened.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import i18n
from . import adapter
from . import icons
from . import style as style_module
from . import theme as theme_module
from .style import Card, Chip

DRAFT_AUTOSAVE_MS = 1500
# Re-validating the whole delivery (every segment, every finding) costs ~240 ms
# on a 400-segment job.  It must not run on every background poll tick, so it is
# throttled; explicit actions and terminal state changes still force it.
DELIVERY_REFRESH_SECONDS = 5.0
# Even a forced refresh is rate-limited this much, so bursts collapse.
DELIVERY_FORCE_FLOOR_SECONDS = 1.0


# 当前主题由样式层持有（`folith.qt.style`）；这里保留同名入口，视图与测试
# 继续用 `views.current_theme()`，但只有一个真值。
def current_theme() -> str:
    return style_module.current_theme()


def set_current_theme(name: str) -> None:
    style_module.set_current_theme(name)


def _tone_color(tokens: dict, tone: str) -> str:
    key = {"ok": "success", "warn": "warning", "danger": "danger",
           "info": "accent", "brand": "brand-ink"}.get(tone, "text-secondary")
    return tokens.get(key, tokens["text-secondary"])


#: 界面标签 -> 核心 `translation_visible_indexes` 认识的取值。
#: 界面写 "待审校"，核心只认 "待审"；传错标签不会报错，只是**静默不过滤**。
STATUS_FILTER_VALUES = {"全部": "全部", "待审校": "待审", "已审校": "已审校"}


def _segment_tone(row) -> str:
    """段落状态 -> 语义色调。"""
    if getattr(row, "excluded", False):
        return "muted"
    if getattr(row, "human_edited", False):
        return "info"
    if getattr(row, "reviewed", False):
        return "ok"
    if getattr(row, "has_draft", False):
        return "warn"
    return "muted"


#: `core.task_status_label()` 能给出的全部标签 -> 语义色调。
#: **顺序即优先级**：`工作版本已偏离冻结交付 v2` 里也含"已冻结交付"三个字，
#: 若先匹配"已冻结交付"就会把"偏离"显示成成功的绿色。
TASK_STATUS_TONES = (
    ("无法读取", "danger"),
    ("工作版本已偏离冻结交付", "warn"),
    ("已冻结交付", "ok"),
    ("可以冻结交付", "ok"),
    ("待审校", "warn"),
    ("待学术复核", "warn"),
    ("待术语确认", "warn"),
    ("暂不满足交付条件", "warn"),
    ("处理中断", "warn"),
    ("待处理", "muted"),
)

#: `job_repository` 的 `headline_status` 标签 -> 运行状态芯片的色调。
#: 需要用户注意的状态（暂无运行信号 / 未完成 / 待术语确认）不能是中性灰。
RUNTIME_STATUS_TONES = {
    "已完成": "ok",
    "正在运行": "info",
    "正在恢复任务": "info",
    "正在取消": "warn",
    "正在暂停": "warn",
    "已暂停": "warn",
    "上次运行已中断": "warn",
    "未完成": "warn",
    "待术语确认": "warn",
    "暂无运行信号": "danger",
    "当前步骤失败": "danger",
    "任务已取消": "muted",
    "空闲": "muted",
}


def status_tone_for_label(label: str) -> str:
    """把一个任务状态标签映射成语义色调（未知标签按中性处理）。"""
    text = str(label or "")
    for needle, tone in TASK_STATUS_TONES:
        if needle in text:
            return tone
    return "muted"


def _status_tone(row) -> str:
    """任务状态 -> 语义色调（供状态列与芯片共用）。"""
    if getattr(row, "active", False):
        return "info"
    return status_tone_for_label(getattr(row, "status_label", ""))


def _elide(text: str, limit: int = 90) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


# ================= 交付资产清单 =================
#: 交付文件分组（顺序即界面顺序）：译文在前，其次是术语、证据与记录。
#: Web 界面里"交付"是一列带图标、带大小、按用途分组的文件，而不是一串文件名。
DELIVERY_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("translation", "译文与对照", "file-text"),
    ("terms", "术语表", "file-sheet"),
    ("evidence", "证据与清理", "file-code"),
    ("record", "交付记录", "file-code"),
    ("report", "报告", "file-text"),
    ("image", "图片与预览", "image"),
    ("other", "其它文件", "file"),
)

#: 已知交付文件 -> (分组, 图标)。未知文件按后缀取图标并归入"其它文件"。
DELIVERY_ASSET_KINDS: dict[str, tuple[str, str]] = {
    "translation.docx": ("translation", "file-text"),
    "bilingual.docx": ("translation", "file-text"),
    "translation.pdf": ("translation", "file-text"),
    "terms.xlsx": ("terms", "file-sheet"),
    "segment_evidence.jsonl": ("evidence", "file-code"),
    "source_cleanup.json": ("evidence", "file-code"),
    "delivery_manifest.json": ("record", "file-code"),
}

DELIVERY_SUFFIX_ICONS = {
    ".docx": "file-text", ".doc": "file-text", ".pdf": "file-text",
    ".xlsx": "file-sheet", ".xls": "file-sheet", ".csv": "file-sheet",
    ".json": "file-code", ".jsonl": "file-code", ".xml": "file-code",
    ".md": "file-text", ".txt": "file-text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".epub": "file-text",
}


def _asset_kind(name: str) -> tuple[str, str]:
    """交付文件名 -> (分组 key, 图标名)。"""
    base = str(name or "").split("/")[-1]
    known = DELIVERY_ASSET_KINDS.get(base)
    if known is not None:
        return known
    lowered = base.lower()
    for suffix, icon in DELIVERY_SUFFIX_ICONS.items():
        if lowered.endswith(suffix):
            return "image" if icon == "image" else "other", icon
    return "other", "file"


def _human_size(size) -> str:
    """字节数 -> `36.5 KB`。取不到大小就返回 `—`，不猜一个数。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        return "—"
    if value < 1024:
        return f"{int(value)} B"
    for unit, divisor in (("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3)):
        if value < divisor * 1024 or unit == "GB":
            return f"{value / divisor:.1f} {unit}"
    return f"{value:.0f} B"


def _asset_summary(assets: list[dict]) -> str:
    total = 0
    known = 0
    for asset in assets:
        size = asset.get("size")
        if isinstance(size, (int, float)):
            total += int(size)
            known += 1
    text = f"{len(assets)} 个文件"
    if known:
        text += f" · {_human_size(total)}"
    return text


def _frozen_at_label(value) -> str:
    """快照 `created_at`（ISO UTC）-> 本地 `YYYY-MM-DD HH:MM`，取不到就不显示。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        from datetime import datetime

        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone()
        return moment.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return text[:16].replace("T", " ")


def _asset_rows(latest: dict | None, *, resolve=None) -> list[dict]:
    """把快照 manifest 变成"分组标题 + 文件行"的展示数据。

    纯函数（路径探测由调用方通过 `resolve` 注入），因此能脱离窗口单测。
    """
    assets = [item for item in ((latest or {}).get("assets") or [])
              if str(item.get("name") or "")]
    if not assets:
        return []
    grouped: dict[str, list[dict]] = {}
    for asset in assets:
        group, icon = _asset_kind(str(asset.get("name")))
        grouped.setdefault(group, []).append({"asset": asset, "icon": icon})
    rows: list[dict] = []
    for group, title, _group_icon in DELIVERY_GROUPS:
        items = grouped.get(group)
        if not items:
            continue
        rows.append({"kind": "header", "title": title,
                     "meta": _asset_summary([item["asset"] for item in items])})
        for item in items:
            asset = item["asset"]
            name = str(asset.get("name"))
            missing = False
            if resolve is not None:
                try:
                    path = resolve(name)
                except Exception:  # noqa: BLE001 - 快照读不到就按缺失显示
                    path = None
                missing = not (path and getattr(path, "is_file", lambda: False)())
            size = asset.get("size")
            rows.append({
                "kind": "asset", "title": name, "icon": item["icon"],
                "size": int(size) if isinstance(size, (int, float)) else None,
                "missing": missing,
                "meta": "文件缺失" if missing else _human_size(size),
                "sha256": str(asset.get("sha256") or ""),
            })
    return rows


class _TaskCard(Card):
    """首页卡片视图里的一张任务卡：单击选中、双击打开。

    与表格共用同一份选中状态（卡片只是把表格行"画"成卡片），因此底部动作
    （打开/移动/删除）在两种视图下语义一致，不需要第二套选择逻辑。
    """

    clicked = Signal(str)
    activated = Signal(str)

    def __init__(self, row: adapter.JobRow, *, tone: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
        self.job_id = row.job_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.tr('单击选中；双击打开任务'))
        self._selected = False
        # 属性一开始就要有值：QSS 的 [selected="true"] 与外部探测都读它，
        # 只在状态变化时设置会留下"未定义"的中间态。
        self.setProperty("selected", "false")

        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                  theme_module.SPACE["md"], theme_module.SPACE["md"])
        column.setSpacing(theme_module.SPACE["xs"])

        title = QLabel(row.title or "未命名任务")
        title.setObjectName("taskCardTitle")
        title.setToolTip(row.title or "")
        self.chip = Chip(_elide(i18n.tr_dynamic(row.status_label) or "—", 12), tone)

        file_icon = QLabel()
        file_icon.setPixmap(icons.pixmap("file-text", 16,
                                         style_module.text_color("text-secondary")))
        file_icon.setFixedSize(16, 16)

        top = QHBoxLayout()
        top.setSpacing(theme_module.SPACE["sm"])
        top.addWidget(file_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(title, 1)
        top.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignTop)

        meta_parts = [part for part in (
            row.target_lang or "",
            f"{row.segment_count} 段" if row.segment_count else "",
            (row.updated_at or "").replace("T", " ")[:16] or "",
        ) if part]
        meta = QLabel(" · ".join(meta_parts))
        meta.setObjectName("taskCardMeta")
        meta.setToolTip(row.title or "")

        # 旧界面首页卡片右下角有"查看项目 →"：这里给一个可见的打开入口，
        # 而不是只靠"双击"这种没有提示的隐藏操作。点击仍然由卡片本身接收。
        open_hint = QLabel(i18n.tr('打开任务 →'))
        open_hint.setObjectName("link")

        footer = QHBoxLayout()
        footer.setSpacing(theme_module.SPACE["sm"])
        footer.addWidget(open_hint)
        footer.addStretch(1)

        column.addLayout(top)
        column.addWidget(meta)
        column.addStretch(1)
        column.addLayout(footer)

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self.setProperty("selected", "true" if selected else "false")
        style_module.repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.job_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.job_id)
        super().mouseDoubleClickEvent(event)


class HomeView(QWidget):
    """任务库：品牌栏 + 指标芯片 + 状态化任务表 + 空状态。"""

    importRequested = Signal()
    openRequested = Signal(str)
    deleteRequested = Signal(str)
    refreshRequested = Signal()
    createProjectRequested = Signal()
    manageProjectsRequested = Signal()
    moveToProjectRequested = Signal(list)
    settingsRequested = Signal()

    COLUMNS = ("文档", "状态", "目标语言", "段落", "更新时间")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[adapter.JobRow] = []
        self._last_row_count = 0

        # ---- 品牌栏（侧栏顶部） --------------------------------------------
        from . import branding

        self._logo_label = QLabel()
        self._logo_theme = ""
        self._refresh_logo()
        # 侧栏品牌块用**字标**（自带品牌名与标语），和旧 Web 界面一致：
        # 图形标记（app icon）在 30px 下只是一团深色，读不出品牌。
        brand_row = QHBoxLayout()
        brand_row.setSpacing(theme_module.SPACE["sm"])
        brand_row.addWidget(self._logo_label)
        brand_row.addStretch(1)

        # ---- 侧栏动作（全部是真实入口，不放没实现的导航） -------------------
        self.import_button = QPushButton(i18n.tr('导入文档…'))
        self.import_button.setObjectName("primaryAction")
        self.import_button.setIcon(icons.icon("upload", 16, style_module.text_color("text-on-accent")))
        self.import_button.setIconSize(QSize(16, 16))
        self.import_button.setMinimumHeight(theme_module.CONTROL["lg"])
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self.importRequested.emit)
        style_module.fit_button(self.import_button, max_width=168)

        def nav_button(label: str, icon_name: str) -> QPushButton:
            button = QPushButton(label)
            button.setObjectName("navItem")
            button.setIcon(icons.icon(icon_name, 16, style_module.text_color("text-secondary")))
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            # 侧栏是固定宽度：长语言（英/俄/阿）必须省略显示，否则按钮被截断
            style_module.fit_button(button, max_width=168)
            return button

        self.refresh_button = nav_button(i18n.tr('刷新任务'), "refresh")
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        self.project_button = nav_button(i18n.tr('新建项目…'), "plus")
        self.project_button.clicked.connect(self.createProjectRequested.emit)
        self.manage_projects_button = nav_button(i18n.tr('项目…'), "folder")
        self.manage_projects_button.clicked.connect(self.manageProjectsRequested.emit)
        self.settings_button = nav_button(i18n.tr('设置…'), "settings")
        self.settings_button.clicked.connect(self.settingsRequested.emit)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["lg"],
                                theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        side.setSpacing(theme_module.SPACE["sm"])
        side.addLayout(brand_row)
        side.addSpacing(theme_module.SPACE["md"])
        side.addWidget(self.import_button)
        side.addSpacing(theme_module.SPACE["sm"])
        side.addWidget(style_module.SectionLabel(i18n.tr('任务')))
        side.addWidget(self.refresh_button)
        side.addSpacing(theme_module.SPACE["sm"])
        side.addWidget(style_module.SectionLabel(i18n.tr('项目')))
        side.addWidget(self.manage_projects_button)
        side.addWidget(self.project_button)
        side.addSpacing(theme_module.SPACE["sm"])
        side.addWidget(style_module.SectionLabel(i18n.tr('应用')))
        side.addWidget(self.settings_button)
        side.addStretch(1)

        # 引擎状态卡：模型 + 连接状态 + 配置入口（旧 Web 界面侧栏底部那一块）
        engine_card = Card()
        engine_layout = QVBoxLayout(engine_card)
        engine_layout.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                         theme_module.SPACE["md"], theme_module.SPACE["md"])
        engine_layout.setSpacing(theme_module.SPACE["xs"])
        self.engine_label = QLabel(i18n.tr('模型：未配置'))
        self.engine_label.setObjectName("caption")
        self.engine_label.setWordWrap(True)
        self.setup_chip = Chip(i18n.tr('未配置'), "warn")
        chip_row = QHBoxLayout()
        chip_row.setSpacing(theme_module.SPACE["sm"])
        chip_row.addWidget(self.setup_chip)
        chip_row.addStretch(1)
        self.setup_button = QPushButton(i18n.tr('配置模型连接…'))
        self.setup_button.setObjectName("subtleAction")
        style_module.fit_button(self.setup_button, max_width=148)
        self.setup_button.clicked.connect(self.settingsRequested.emit)
        engine_layout.addWidget(style_module.SectionLabel(i18n.tr('AI 引擎')))
        engine_layout.addWidget(self.engine_label)
        engine_layout.addLayout(chip_row)
        engine_layout.addWidget(self.setup_button)
        side.addWidget(engine_card)

        # ---- 页头 ----------------------------------------------------------
        title = QLabel(i18n.tr('任务库'))
        title.setObjectName("displayTitle")
        subtitle = QLabel(i18n.tr('导入 PDF / DOCX，翻译、审校，然后交付。'))
        subtitle.setObjectName("subtitle")
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        self.refresh_icon_button = style_module.icon_button("refresh", i18n.tr('刷新任务'))
        self.refresh_icon_button.clicked.connect(self.refreshRequested.emit)
        self.settings_icon_button = style_module.icon_button("settings", i18n.tr('设置…'))
        self.settings_icon_button.clicked.connect(self.settingsRequested.emit)
        header_actions = QHBoxLayout()
        header_actions.setSpacing(theme_module.SPACE["sm"])
        header_actions.addWidget(self.refresh_icon_button)
        header_actions.addWidget(self.settings_icon_button)

        page_header = QHBoxLayout()
        page_header.setSpacing(theme_module.SPACE["lg"])
        page_header.addLayout(title_box)
        page_header.addStretch(1)
        page_header.addLayout(header_actions)

        # ---- 首次运行提示（只有未配置模型时出现） --------------------------
        self.setup_banner = style_module.NoticeStrip(tone="warn")
        self.setup_banner.set_action(i18n.tr('配置模型连接…'), self.settingsRequested.emit)
        self.setup_banner.setVisible(False)

        # ---- 指标卡 --------------------------------------------------------
        self.kpi_total = style_module.KpiCard(i18n.tr('任务总数'))
        self.kpi_active = style_module.KpiCard(i18n.tr('运行中'))
        self.kpi_delivered = style_module.KpiCard(i18n.tr('已交付'))
        self.kpi_errored = style_module.KpiCard(i18n.tr('有错误'))
        kpis = QHBoxLayout()
        kpis.setSpacing(theme_module.SPACE["md"])
        for card in (self.kpi_total, self.kpi_active, self.kpi_delivered, self.kpi_errored):
            kpis.addWidget(card, 1)

        # ---- 区块标题 + 项目筛选 -------------------------------------------
        self.section_title = QLabel(i18n.tr('任务列表'))
        self.section_title.setObjectName("sectionTitle")
        self.metric_jobs = Chip(i18n.tr('0 个任务'), "muted")
        self.metric_active = Chip(i18n.tr('0 个运行中'), "info")
        self.metric_active.setVisible(False)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(theme_module.SPACE["sm"])
        filter_row.addWidget(self.section_title)
        filter_row.addWidget(self.metric_jobs)
        filter_row.addWidget(self.metric_active)
        filter_row.addStretch(1)
        # 表格 / 卡片：旧界面首页是"按项目分组的卡片"，表格适合几十个任务横着扫。
        # 两种视图共用同一份选中状态，切换不丢选择。
        self.view_switch = style_module.SegmentedControl(
            [("table", i18n.tr("表格")), ("cards", i18n.tr("卡片"))])
        self._view_mode = "table"
        self.view_switch.changed.connect(self._set_view_mode)
        filter_row.addWidget(self.view_switch)
        filter_row.addWidget(style_module.SectionLabel(i18n.tr('项目')))
        self.project_filter = QComboBox()
        self.project_filter.addItem(i18n.tr('全部项目'), "")
        self.project_filter.setMinimumWidth(160)
        self.project_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.project_filter)

        # ---- 任务表 --------------------------------------------------------
        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(
            [i18n.tr(column) for column in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 多选：任务多起来以后，逐个删除/改归属太慢（旧界面也支持批量）。
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(theme_module.CONTROL["row"])
        self.table.horizontalHeader().setFixedHeight(theme_module.CONTROL["header"])
        self.table.horizontalHeader().setHighlightSections(False)
        # 文档与状态一起伸展：只伸展第一列会把其余列挤到最右侧，中间留出
        # 一大片空白，看起来像表格没做完。
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, len(self.COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setItemDelegate(style_module.RowDelegate(self.table))
        self.table.itemSelectionChanged.connect(self._selection_changed)
        # Qt 在 FocusIn 之后才设置 currentIndex：这里补一次，保证"键盘进来就有选中行"
        self.table.currentCellChanged.connect(
            lambda *_args: self._select_current_row_for_keyboard())
        # 键盘用户进入表格时给它一个"当前行即选中行"的起点（否则按钮永远是灰的）
        self.table.installEventFilter(self)
        self.table.doubleClicked.connect(lambda _index: self._open_selected())

        # ---- 卡片视图（按项目分组） ------------------------------------------
        self.card_page = QWidget()
        self.card_page.setObjectName("pageCanvas")
        self.card_layout = QVBoxLayout(self.card_page)
        self.card_layout.setContentsMargins(0, 0, theme_module.SPACE["sm"], 0)
        self.card_layout.setSpacing(theme_module.SPACE["lg"])
        self.card_scroll = QScrollArea()
        self.card_scroll.setWidgetResizable(True)
        self.card_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.card_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.card_scroll.viewport().setAutoFillBackground(False)
        self.card_scroll.setWidget(self.card_page)
        self._cards: dict[str, _TaskCard] = {}
        self._project_names: dict[str, str] = {}
        self._card_columns_applied = 0

        # ---- 空状态 --------------------------------------------------------
        self.empty_card = Card()
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["xxl"],
                                        theme_module.SPACE["xl"], theme_module.SPACE["xxl"])
        empty_layout.setSpacing(theme_module.SPACE["sm"])
        empty_icon = QLabel()
        empty_icon.setPixmap(icons.pixmap("file-text", 40, style_module.text_color("text-muted")))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_title = QLabel(i18n.tr('还没有任务'))
        empty_title.setObjectName("sectionTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label = QLabel(i18n.tr('点击「导入文档…」选择 PDF 或 DOCX，即可开始翻译。'))
        self.empty_label.setObjectName("hint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_button = QPushButton(i18n.tr('导入文档…'))
        self.empty_button.setObjectName("primaryAction")
        self.empty_button.setIcon(icons.icon("upload", 16, style_module.text_color("text-on-accent")))
        self.empty_button.setIconSize(QSize(16, 16))
        self.empty_button.clicked.connect(self.importRequested.emit)
        empty_buttons = QHBoxLayout()
        empty_buttons.addStretch(1)
        empty_buttons.addWidget(self.empty_button)
        empty_buttons.addStretch(1)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(self.empty_label)
        empty_layout.addSpacing(theme_module.SPACE["sm"])
        empty_layout.addLayout(empty_buttons)
        empty_wrap = QVBoxLayout()
        empty_wrap.addStretch(1)
        empty_wrap.addWidget(self.empty_card)
        empty_wrap.addStretch(2)
        self.empty_page = QWidget()
        self.empty_page.setLayout(empty_wrap)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.empty_page)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.card_scroll)

        # ---- 底部动作 + 状态 ------------------------------------------------
        self.open_button = QPushButton(i18n.tr('打开任务'))
        self.open_button.setObjectName("primaryAction")
        self.open_button.setIcon(icons.icon("arrow-right", 16, style_module.text_color("text-on-accent")))
        self.open_button.setIconSize(QSize(16, 16))
        self.move_button = QPushButton(i18n.tr('移动到项目…'))
        self.move_button.setIcon(icons.icon("folder", 16, style_module.text_color("text-secondary")))
        self.move_button.setIconSize(QSize(16, 16))
        self.delete_button = QPushButton(i18n.tr('删除任务'))
        self.delete_button.setObjectName("dangerAction")
        self.delete_button.setIcon(icons.icon("trash", 16, style_module.text_color("danger")))
        self.delete_button.setIconSize(QSize(16, 16))
        self.open_button.clicked.connect(self._open_selected)
        self.move_button.clicked.connect(self._move_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")

        actions = QHBoxLayout()
        actions.setSpacing(theme_module.SPACE["sm"])
        actions.addWidget(self.open_button)
        actions.addWidget(self.move_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        actions.addWidget(self.status_label)

        # ---- 主区组装 ------------------------------------------------------
        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["lg"])
        layout.addLayout(page_header)
        layout.addWidget(self.setup_banner)
        layout.addLayout(kpis)
        layout.addLayout(filter_row)
        layout.addWidget(self.stack, 1)
        layout.addLayout(actions)

        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(sidebar)
        shell.addWidget(main, 1)

    # -- data --------------------------------------------------------------
    def set_rows(self, rows: list[adapter.JobRow]) -> None:
        self._rows = list(rows)
        self._apply_filter()

    def set_projects(self, projects: list[dict]) -> None:
        current = self.project_filter.currentData() or ""
        self._project_names = {
            str(project.get("project_id") or ""):
                (str(project.get("name") or i18n.tr("未命名项目"))
                 + (i18n.tr("（未分类）") if project.get("is_system") else ""))
            for project in projects
        }
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem(i18n.tr('全部项目'), "")
        for project in projects:
            self.project_filter.addItem(str(project.get("name") or "未命名项目"),
                                        project.get("project_id") or "")
        index = self.project_filter.findData(current)
        self.project_filter.setCurrentIndex(index if index >= 0 else 0)
        self.project_filter.blockSignals(False)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_engine_problem(self, problem: str, model_label: str = "") -> None:
        """模型连接状态：侧栏引擎卡 + 主区提示条（同一份信息，两处都看得见）。"""
        if problem:
            detail = i18n.tr("尚未配置可用的模型连接：{v0}。", v0=problem)
            if model_label:
                detail += i18n.tr("当前：{v0}。", v0=model_label)
            detail += i18n.tr("配置并测试连接后即可开始翻译。")
            self.setup_banner.set_text(detail)
            self.setup_banner.setVisible(True)
            self.setup_chip.set_tone("warn")
            self.setup_chip.setText(i18n.tr("未配置"))
            self.engine_label.setText(
                i18n.tr("模型：{v0}", v0=model_label) if model_label
                else i18n.tr("模型：未配置"))
        else:
            self.setup_banner.setVisible(False)
            self.setup_chip.set_tone("ok")
            # The chip's *text* used to stay at its construction value, so a
            # correctly configured client still showed 「未配置」 next to a green
            # chip while the status bar said the model was ready.  Only the tone
            # was being updated here.
            self.setup_chip.setText(i18n.tr("已配置"))
            self.engine_label.setText(
                i18n.tr("模型：{v0}", v0=model_label) if model_label
                else i18n.tr("模型：已配置"))

    def _visible_rows(self) -> list[adapter.JobRow]:
        project = self.project_filter.currentData() or ""
        if not project:
            return self._rows
        return [row for row in self._rows if (row.project_id or "") == project]

    def _refresh_logo(self) -> None:
        from . import branding

        theme = current_theme()
        if theme == self._logo_theme:
            return
        # 字标自带品牌名与标语，所以侧栏不再重复写文字
        logo = branding.logo_pixmap(32, theme)
        if not logo.isNull():
            self._logo_label.setPixmap(logo)
        self._logo_theme = theme

    def _apply_filter(self) -> None:
        rows = self._visible_rows()
        self._refresh_logo()
        tokens = theme_module.tokens(current_theme())
        self.metric_jobs.setText(i18n.tr('{v0} 个任务', v0=len(rows)))
        active = [row for row in rows if row.active]
        self.metric_active.setVisible(bool(active))
        if active:
            self.metric_active.setText(i18n.tr('{v0} 个运行中', v0=len(active)))
        # 指标卡：只用行里**确实存在**的字段（不猜状态字符串）
        delivered = [row for row in rows if getattr(row, "snapshot_version", None)]
        errored = [row for row in rows if str(getattr(row, "error", "") or "").strip()]
        self.kpi_total.set_value(str(len(rows)))
        self.kpi_active.set_value(str(len(active)))
        self.kpi_delivered.set_value(str(len(delivered)))
        self.kpi_errored.set_value(str(len(errored)))
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            tone = _status_tone(row)
            values = (
                row.title,
                ("● " if row.active else "") + i18n.tr_dynamic(row.status_label),
                i18n.tr_dynamic(row.target_lang) if row.target_lang else "—",
                str(row.segment_count) if row.segment_count else "—",
                (row.updated_at or "").replace("T", " ")[:19] or "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.job_id)
                    # 文档名是这一行的主信息：加粗一点，扫描时先看到它
                    font = item.font()
                    font.setWeight(font.Weight.DemiBold)
                    item.setFont(font)
                if column in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 4:
                    item.setForeground(QColor(tokens["text-muted"]))
                if column == 1:
                    item.setForeground(QColor(_tone_color(tokens, tone)))
                if row.active:
                    item.setForeground(QColor(tokens["accent"]))
                self.table.setItem(row_index, column, item)
        self._fill_cards(rows)
        self._reflow_stack(rows)
        row_count_changed = len(rows) != self._last_row_count
        self._last_row_count = len(rows)
        if row_count_changed and self.table.hasFocus():
            self._select_current_row_for_keyboard()
        self._sync_card_selection()
        self._selection_changed()

    # -- 视图切换 / 卡片视图 ------------------------------------------------
    def _reflow_stack(self, rows: list[adapter.JobRow] | None = None) -> None:
        """按"有没有任务 + 当前视图"决定显示哪一页（空列表永远显示空状态）。"""
        rows = self._visible_rows() if rows is None else rows
        if not rows:
            self.stack.setCurrentWidget(self.empty_page)
        elif self._view_mode == "cards":
            self.stack.setCurrentWidget(self.card_scroll)
        else:
            self.stack.setCurrentWidget(self.table)

    def _set_view_mode(self, value: str) -> None:
        self._view_mode = value if value in ("table", "cards") else "table"
        self._reflow_stack()
        self._sync_card_selection()

    def set_view_mode(self, value: str) -> None:
        """给窗口/测试用：切换视图时同步分段控件的选中态。"""
        self._set_view_mode(value)
        self.view_switch.set_value(self._view_mode)

    def _card_columns(self) -> int:
        # 先看视图自己的宽度（滚动区在首次填充时往往还没被布局到最终尺寸）。
        width = self.width() or self.card_scroll.viewport().width()
        if width >= 1240:
            return 3
        if width >= 880:
            return 2
        return 1

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """窗口宽度跨过断点时重排卡片：否则列数会和可用宽度脱节。"""
        super().resizeEvent(event)
        if self._view_mode != "cards" or not self._cards:
            return
        columns = self._card_columns()
        if columns != self._card_columns_applied:
            self._fill_cards(self._visible_rows())
            self._sync_card_selection()

    def _project_label(self, project_id: str) -> str:
        if not project_id:
            return "未归属项目"
        return self._project_names.get(project_id) or "未命名项目"

    def _fill_cards(self, rows: list[adapter.JobRow]) -> None:
        """按项目分组重建卡片页：分组标题 + 网格任务卡（选中态与表格同步）。"""
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先摘出布局树再排队销毁：只 deleteLater() 的话旧卡片会一直挂到
                # 事件循环回收那一刻，期间和新卡片叠在一起（看起来像重影）。
                widget.setParent(None)
                widget.deleteLater()
        self._cards = {}

        groups: dict[str, list[adapter.JobRow]] = {}
        for row in rows:
            groups.setdefault(row.project_id or "", []).append(row)

        columns = self._card_columns()
        self._card_columns_applied = columns
        for project_id, items in groups.items():
            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(theme_module.SPACE["sm"])
            heading_row = QHBoxLayout()
            heading_row.setSpacing(theme_module.SPACE["sm"])
            heading = QLabel(self._project_label(project_id))
            heading.setObjectName("groupHeading")
            heading_row.addWidget(heading)
            heading_row.addWidget(Chip(i18n.tr('{v0} 个任务', v0=len(items)), "muted"))
            heading_row.addStretch(1)
            section_layout.addLayout(heading_row)

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme_module.SPACE["md"])
            grid.setVerticalSpacing(theme_module.SPACE["md"])
            for position, row in enumerate(items):
                card = _TaskCard(row, tone=_status_tone(row), parent=section)
                card.clicked.connect(self._select_job_from_card)
                card.activated.connect(self._open_job_from_card)
                grid.addWidget(card, position // columns, position % columns)
                self._cards[row.job_id] = card
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            section_layout.addLayout(grid)
            self.card_layout.addWidget(section)
        self.card_layout.addStretch(1)

    def _select_job_from_card(self, job_id: str) -> None:
        for row_index in range(self.table.rowCount()):
            item = self.table.item(row_index, 0)
            if item is not None and str(item.data(
                    Qt.ItemDataRole.UserRole) or "") == job_id:
                self.table.clearSelection()
                self.table.selectRow(row_index)
                return

    def _open_job_from_card(self, job_id: str) -> None:
        self._select_job_from_card(job_id)
        self.openRequested.emit(job_id)

    def _sync_card_selection(self) -> None:
        selected = set(self.selected_job_ids())
        for job_id, card in self._cards.items():
            card.set_selected(job_id in selected)

    def selected_job_ids(self) -> list[str]:
        """当前选中的任务（按列表顺序，去重）。"""
        rows = sorted({item.row() for item in self.table.selectedItems()})
        ids: list[str] = []
        for row in rows:
            item = self.table.item(row, 0)
            if item is None:
                continue
            job_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            if job_id and job_id not in ids:
                ids.append(job_id)
        return ids

    def selected_job_id(self) -> str:
        """兼容单选用途：返回第一个选中的任务。"""
        ids = self.selected_job_ids()
        return ids[0] if ids else ""

    def _select_current_row_for_keyboard(self) -> None:
        """键盘用户进入表格时，把"当前行"变成"选中行"。

        Qt 的行为（R63 实测）：`QTableWidget` 填完行以后 `currentRow()` 已经是 0，但**没有任何
        选中项**；只有一行（或已经停在第一行）时方向键不会改变 current，于是永远不触发
        `itemSelectionChanged`——三个动作按钮对纯键盘用户**永远是灰的**，而且 Tab 也到不了它们
        （禁用控件会被焦点链跳过）。这里在表格拿到键盘焦点（或行数变化且表格有焦点）时，
        只要还没有任何选中项就选中当前行。只在"没有选中"时动手，所以不会清掉鼠标多选
        （`selectRow` 会清空多选），也不会和用户的取消选择打架。
        """
        if self.selected_job_ids():
            return
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount() > 0:
            # 焦点进入表格时 Qt 还没设置 current（FocusIn 先于 currentIndex 初始化），
            # 此时按"第一行"给一个起点，与 Qt 随后的行为一致。
            row = 0
        if 0 <= row < self.table.rowCount():
            self.table.selectRow(row)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt 命名
        """表格拿到键盘焦点时给它一个"当前行即选中行"的起点（键盘可达性的前提）。"""
        if watched is self.table and event.type() == QEvent.Type.FocusIn:
            self._select_current_row_for_keyboard()
        return super().eventFilter(watched, event)

    def _selection_changed(self) -> None:
        ids = self.selected_job_ids()
        self.open_button.setEnabled(len(ids) == 1)
        self.move_button.setEnabled(bool(ids))
        self.delete_button.setEnabled(bool(ids))
        self.move_button.setText(
            i18n.tr("移动到项目…（{v0} 个）", v0=len(ids)) if len(ids) > 1
            else i18n.tr("移动到项目…"))
        self._sync_card_selection()
        self.delete_button.setText(
            i18n.tr("删除任务（{v0} 个）", v0=len(ids)) if len(ids) > 1
            else i18n.tr("删除任务"))

    def _move_selected(self) -> None:
        ids = self.selected_job_ids()
        if ids:
            self.moveToProjectRequested.emit(ids)

    def _open_selected(self) -> None:
        job_id = self.selected_job_id()
        if job_id:
            self.openRequested.emit(job_id)

    def _delete_selected(self) -> None:
        for job_id in self.selected_job_ids():
            self.deleteRequested.emit(job_id)

    def select_job(self, job_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole)) == job_id:
                self.table.selectRow(row)
                return


class WorkbenchView(QWidget):
    """多段工作台：CAT 网格 + 右侧检查器 + 交付标签页。

    设计要点（对应验收项）：

    * **网格是虚拟的**：只存在一个活动编辑器（右侧检查器里），所以 200 段、
      16k 字符的文档不会创建大量重型编辑控件（§6.B）；
    * **后台刷新不重建正在输入的编辑器**：同一段落再次被选中时不触碰编辑器，
      列表重建按稳定段身份恢复选中（§6.B）；
    * **过滤复用核心语义**：搜索/状态筛选调用 `core.translation_visible_indexes`，
      界面不另写一套筛选规则（不制造第二套真值）；
    * **只放真正可用的入口**：标签页只有「翻译」「交付」两个；旧界面的
      术语/审校标签页与 AGENT 改写动作在 Qt 端没有实现，因此不显示。
    """

    backRequested = Signal()
    startRequested = Signal()
    pauseRequested = Signal()
    cancelRequested = Signal()
    retryRequested = Signal()
    reviewCurrentRequested = Signal(int)
    reviewAllRequested = Signal()
    reviewSelectedRequested = Signal(list)
    retranslateRequested = Signal(int)
    discardDraftRequested = Signal(int)
    excludeRequested = Signal(int)
    includeRequested = Signal(str)
    deliverRequested = Signal()
    exportRequested = Signal()
    saveSnapshotRequested = Signal(int)
    refreshRequested = Signal()
    draftSaved = Signal(str, str, str)      # job_id, segment_uid, text
    editSubmitted = Signal(int)
    revealRequested = Signal(str)
    openAssetRequested = Signal(str)

    COLUMNS = ("段", "原文", "译文")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = ""
        self.config: adapter.EngineConfig | None = None
        self._all_rows: list[adapter.SegmentRow] = []
        self._rows: list[adapter.SegmentRow] = []
        self._state: dict = {}
        self._current_index = -1
        self._loaded_uid = ""
        self._loading = False
        self._dirty = False
        self._last_runtime: dict = {}
        self._snapshots: list[dict] = []
        self._delivery_rendered = False
        self._delivery_preview: dict = {}
        self._delivery_preview_at = 0.0
        self._delivery_key: tuple = ()
        self._search = ""
        self._status_filter = "全部"
        self._only_edited = False
        self._only_issues = False
        self._excluded_signature: tuple | None = None

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(DRAFT_AUTOSAVE_MS)
        self._autosave.timeout.connect(self.flush_draft)

        # ---- 头部卡片 ------------------------------------------------------
        self.back_button = QPushButton(i18n.tr('任务列表'))
        self.back_button.setObjectName("ghostAction")
        self.back_button.setIcon(icons.icon("arrow-left", 16,
                                            style_module.text_color("text-secondary")))
        self.back_button.setIconSize(QSize(16, 16))
        self.back_button.clicked.connect(self.backRequested.emit)
        self.title_label = QLabel("—")
        self.title_label.setObjectName("pageTitle")
        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")

        self.chip_state = Chip("—", "muted")
        self.chip_progress = Chip(i18n.tr('0/0 段'), "muted")
        self.chip_review = Chip(i18n.tr('审校 0/0'), "muted")
        self.chip_blocking = Chip(i18n.tr('必须处理 0'), "muted")

        def _icon_button(button: QPushButton, name: str, token: str = "text-secondary"):
            button.setIcon(icons.icon(name, 16, style_module.text_color(token)))
            button.setIconSize(QSize(16, 16))
            return button

        self.start_button = _icon_button(QPushButton(i18n.tr('开始翻译')), "play", "text-on-accent")
        self.start_button.setObjectName("primaryAction")
        self.pause_button = _icon_button(QPushButton(i18n.tr('暂停')), "pause")
        self.cancel_button = _icon_button(QPushButton(i18n.tr('取消任务')), "stop", "danger")
        self.cancel_button.setObjectName("dangerAction")
        self.retry_button = _icon_button(QPushButton(i18n.tr('重试当前步骤')), "refresh")
        for button, signal in ((self.start_button, self.startRequested),
                               (self.pause_button, self.pauseRequested),
                               (self.cancel_button, self.cancelRequested),
                               (self.retry_button, self.retryRequested)):
            button.clicked.connect(signal.emit)

        # 审校动作：`review_all_button` 是 Header 上的"推荐下一步"，
        # `review_selected_button` 留在工具栏（它跟当前选区绑定）。
        self.review_selected_button = _icon_button(QPushButton(i18n.tr('审校所选段落')), "shield-check")
        self.review_selected_button.setEnabled(False)
        self.review_selected_button.clicked.connect(
            lambda: self.reviewSelectedRequested.emit(self.selected_indexes()))
        self.review_all_button = _icon_button(QPushButton(i18n.tr('审校全部段落')), "shield-check")
        self.review_all_button.clicked.connect(self.reviewAllRequested.emit)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setFixedWidth(220)
        self.phase_label = QLabel("")
        self.phase_label.setWordWrap(True)
        self.phase_label.setObjectName("hint")

        top = QHBoxLayout()
        top.setSpacing(theme_module.SPACE["sm"])
        top.addWidget(self.back_button)
        top.addWidget(self.title_label)
        top.addWidget(self.chip_state)
        top.addStretch(1)
        top.addWidget(self.progress)
        top.addWidget(self.status_label)

        # 元信息行：进度/审校/必须处理三个芯片 + 阶段说明，右侧是任务动作
        meta = QHBoxLayout()
        meta.setSpacing(theme_module.SPACE["sm"])
        meta.addWidget(self.chip_progress)
        meta.addWidget(self.chip_review)
        meta.addWidget(self.chip_blocking)
        meta.addWidget(self.phase_label, 1)

        actions = QHBoxLayout()
        actions.setSpacing(theme_module.SPACE["sm"])
        actions.addStretch(1)
        for button in (self.start_button, self.pause_button,
                       self.cancel_button, self.retry_button):
            actions.addWidget(button)
        # 推荐下一步放在最右（旧界面 Header 的主动作位）：审校全部段落是
        # "翻完还没审"状态下唯一该点的那件事，工具栏只留与选区有关的动作。
        actions.addWidget(self.review_all_button)
        self.header_actions_bar = QWidget()
        self.header_actions_bar.setLayout(actions)

        header_card = Card(raised=True)
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                         theme_module.SPACE["lg"], theme_module.SPACE["md"])
        header_layout.setSpacing(theme_module.SPACE["sm"])
        header_layout.addLayout(top)
        header_layout.addLayout(meta)
        header_layout.addWidget(self.header_actions_bar)

        # ---- 翻译标签页 ----------------------------------------------------
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.tr('搜索原文或译文，或输入段号…'))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filters_changed)

        self.search_edit.addAction(
            icons.icon("search", 16, style_module.text_color("text-muted")),
            QLineEdit.ActionPosition.LeadingPosition)
        self.search_edit.setObjectName("searchField")

        # 分段控件（Web 界面的"全部/待审校/已审校"）：保留 status_buttons 映射，
        # 因为 set_status_filter() 仍然按名字设置选中态。
        self.status_segments = style_module.SegmentedControl(
            [("全部", i18n.tr("全部")), ("待审校", i18n.tr("待审校")),
             ("已审校", i18n.tr("已审校"))])
        self.status_segments.changed.connect(self.set_status_filter)
        self.status_buttons: dict[str, QPushButton] = {
            name: self.status_segments.button(name) for name in ("全部", "待审校", "已审校")}

        self.only_edited_box = style_module.CheckBox(i18n.tr('只看人工修改'))
        self.only_edited_box.toggled.connect(self._filters_changed)
        self.only_issues_box = style_module.CheckBox(i18n.tr('只看必须处理'))
        self.only_issues_box.toggled.connect(self._filters_changed)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(theme_module.SPACE["sm"])
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.status_segments)
        # "筛选"分组标签：两个复选框原来和分段控件并排，读起来像散落的开关，
        # 旧界面这里是"筛选 ▾"一个入口。
        toolbar.addWidget(style_module.SectionLabel(i18n.tr('筛选')))
        toolbar.addWidget(self.only_edited_box)
        toolbar.addWidget(self.only_issues_box)
        toolbar.addStretch(1)
        toolbar.addWidget(self.review_selected_button)
        self.toolbar_bar = QWidget()
        self.toolbar_bar.setLayout(toolbar)

        self.segment_list = QTableWidget(0, len(self.COLUMNS), self)
        self.segment_list.setHorizontalHeaderLabels(
            [i18n.tr(column) for column in self.COLUMNS])
        self.segment_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.segment_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.segment_list.setShowGrid(False)
        self.segment_list.setWordWrap(False)
        self.segment_list.setItemDelegate(style_module.RowDelegate(self.segment_list))
        self.segment_list.verticalHeader().setVisible(False)
        self.segment_list.verticalHeader().setDefaultSectionSize(26)
        self.segment_list.horizontalHeader().setHighlightSections(False)
        self.segment_list.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed)
        self.segment_list.setColumnWidth(0, 64)
        self.segment_list.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.segment_list.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.segment_list.itemSelectionChanged.connect(self._selection_changed)
        self.segment_list.currentCellChanged.connect(
            lambda row, *_rest: self._row_selected(row))

        # ---- 右侧检查器 ----------------------------------------------------
        self.segment_status = QLabel(i18n.tr('尚未选择段落'))
        self.segment_status.setObjectName("hint")
        self.fact_state = QLabel("—")
        self.fact_review = QLabel("—")
        self.fact_source = QLabel("—")
        self.fact_terms = QLabel("—")
        self.fact_excluded = QLabel("—")
        for label in (self.fact_state, self.fact_review, self.fact_source,
                      self.fact_terms, self.fact_excluded):
            label.setWordWrap(True)

        self.prev_button = _icon_button(QPushButton(i18n.tr('上一段')), "chevron-left")
        self.next_button = _icon_button(QPushButton(i18n.tr('下一段')), "chevron-right")
        self.prev_button.clicked.connect(lambda: self._step(-1))
        self.next_button.clicked.connect(lambda: self._step(1))
        self.discard_draft_button = _icon_button(QPushButton(i18n.tr('放弃草稿')), "x", "text-secondary")
        self.discard_draft_button.setObjectName("ghostAction")
        self.discard_draft_button.setToolTip(
            i18n.tr('删除这段未提交的草稿，编辑器回到当前已提交的译文'))
        self.discard_draft_button.clicked.connect(
            lambda: self.discardDraftRequested.emit(self._current_index))
        self.copy_source_button = _icon_button(QPushButton(i18n.tr('复制原文到译文')), "copy", "text-secondary")
        self.copy_source_button.setObjectName("ghostAction")
        self.copy_source_button.clicked.connect(self._copy_source_to_target)
        self.save_next_button = _icon_button(QPushButton(i18n.tr('保存并进入下一段')), "save", "text-secondary")
        self.save_next_button.clicked.connect(self.save_and_next)
        self.submit_button = _icon_button(QPushButton(i18n.tr('保存并提交（⌘S）')), "save", "text-on-accent")
        self.submit_button.setObjectName("primaryAction")
        self.submit_button.clicked.connect(self._submit)
        self.retranslate_button = _icon_button(QPushButton(i18n.tr('重译本段')), "sparkles", "text-secondary")
        self.retranslate_button.setToolTip(i18n.tr('只重译这一段，并刷新它的审校状态'))
        self.retranslate_button.clicked.connect(
            lambda: self.retranslateRequested.emit(self._current_index))
        self.exclude_button = _icon_button(QPushButton(i18n.tr('排除本段')), "eye-off", "text-secondary")
        self.exclude_button.setToolTip(
            i18n.tr('排除后本段从工作台移出，不进入译文与交付；原文与当前译文完整保留，可在「交付」页的已排除清单里恢复'))
        self.exclude_button.clicked.connect(
            lambda: self.excludeRequested.emit(self._current_index))
        self.review_button = _icon_button(QPushButton(i18n.tr('审校本段')), "shield-check", "text-secondary")
        self.review_button.clicked.connect(
            lambda: self.reviewCurrentRequested.emit(self._current_index))

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.next_button)
        nav_row.addStretch(1)

        edit_row = QHBoxLayout()
        edit_row.addWidget(self.submit_button)
        edit_row.addWidget(self.save_next_button)
        edit_row.addWidget(self.copy_source_button)
        edit_row.addWidget(self.discard_draft_button)

        # 段落事实：与设置页同一套"标签在左、值在右"的小表（此前是右对齐标签列，
        # 中文两字标签会浮在中间，值列起点还随标签长度跳）。
        facts = QGridLayout()
        facts.setContentsMargins(0, 0, 0, 0)
        facts.setHorizontalSpacing(theme_module.SPACE["lg"])
        facts.setVerticalSpacing(theme_module.SPACE["xs"])
        for row_index, (name, value) in enumerate((
                ("状态", self.fact_state), ("审校", self.fact_review),
                ("来源", self.fact_source), ("相关术语", self.fact_terms))):
            facts.addWidget(style_module.field_label(name), row_index, 0,
                            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            facts.addWidget(value, row_index, 1)
        facts.setColumnStretch(1, 1)

        self.source_view = QPlainTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setObjectName("sourceView")
        self.source_view.setPlaceholderText(i18n.tr('原文'))
        self.source_view.setMaximumHeight(120)
        self.target_edit = QPlainTextEdit()
        self.target_edit.setPlaceholderText(i18n.tr('译文（可直接编辑；切换段落或退出时自动保留草稿）'))
        self.target_edit.textChanged.connect(self._text_changed)

        inspector = Card()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                            theme_module.SPACE["lg"], theme_module.SPACE["md"])
        inspector_layout.setSpacing(theme_module.SPACE["sm"])
        inspector_layout.addWidget(self.segment_status)
        inspector_layout.addLayout(nav_row)
        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('原文')))
        inspector_layout.addWidget(self.source_view)
        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('译文')))
        inspector_layout.addWidget(self.target_edit, 1)

        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('编辑动作')))
        edit_grid = QGridLayout()
        edit_grid.setHorizontalSpacing(theme_module.SPACE["sm"])
        edit_grid.setVerticalSpacing(theme_module.SPACE["sm"])
        edit_grid.addWidget(self.submit_button, 0, 0, 1, 2)
        edit_grid.addWidget(self.save_next_button, 1, 0)
        edit_grid.addWidget(self.copy_source_button, 1, 1)
        edit_grid.addWidget(self.discard_draft_button, 2, 0, 1, 2)
        inspector_layout.addLayout(edit_grid)

        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('审校与段落')))
        review_grid = QGridLayout()
        review_grid.setHorizontalSpacing(theme_module.SPACE["sm"])
        review_grid.setVerticalSpacing(theme_module.SPACE["sm"])
        review_grid.addWidget(self.review_button, 0, 0)
        review_grid.addWidget(self.retranslate_button, 0, 1)
        review_grid.addWidget(self.exclude_button, 1, 0, 1, 2)
        inspector_layout.addLayout(review_grid)

        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('段落事实')))
        inspector_layout.addLayout(facts)
        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('检查项')))
        self.qa_view = QTextBrowser()
        # 检查项是只读的"报告面"，不是输入框：给它一个沉底面板样式，
        # 否则默认会画出一圈聚焦边框，看起来像可以打字。
        self.qa_view.setObjectName("qaView")
        self.qa_view.setOpenExternalLinks(True)
        self.qa_view.setMinimumHeight(70)
        inspector_layout.addWidget(self.qa_view, 1)

        # 检查器的内容比小屏更高（实测 minimumSizeHint ≈609pt）：直接塞进 splitter 会把主窗口的
        # 最小高度顶到 902pt——在 1440×900 的 Mac 上（可用高度约 875pt）窗口装不下，窗口底部的
        # 动作栏（打开任务/删除任务/提交）会被挤出屏幕，等于主流程不可用。套一层无边框滚动区后，
        # 窗口可以缩进屏幕内，检查器内部滚动，控件仍然全部可达（见 R63 证据）。
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector_scroll.viewport().setAutoFillBackground(False)
        inspector_scroll.setWidget(inspector)

        translation_split = QSplitter(Qt.Orientation.Horizontal, self)
        translation_split.addWidget(self.segment_list)
        translation_split.addWidget(inspector_scroll)
        translation_split.setStretchFactor(0, 1)
        translation_split.setStretchFactor(1, 0)
        translation_split.setSizes([720, 460])
        self.translation_split = translation_split
        self.inspector_scroll = inspector_scroll

        translation_page = QWidget()
        translation_layout = QVBoxLayout(translation_page)
        translation_layout.setContentsMargins(0, 8, 0, 0)
        translation_layout.setSpacing(8)
        translation_layout.addWidget(self.toolbar_bar)
        translation_layout.addWidget(self.translation_split, 1)
        # 没有段落时正文只放一段说明：空表格 + 空检查器看起来像加载失败，
        # 而这个状态下用户真正要做的事只有 Header 上那一个动作。
        self.no_segments_card = Card()
        no_segments_layout = QVBoxLayout(self.no_segments_card)
        no_segments_layout.setContentsMargins(0, 0, 0, 0)
        no_segments_layout.setSpacing(0)
        self.no_segments_state = style_module.EmptyState(
            icon="file-text", title=i18n.tr('还没有可编辑的段落'),
            hint=i18n.tr('这份文档目前没有切分出的段落。点上方「开始翻译」会导入并切分正文；进度会自动保存，可以随时离开再回来。'))
        self.no_segments_state.setObjectName("emptyState")
        no_segments_layout.addWidget(self.no_segments_state)
        translation_layout.addWidget(self.no_segments_card, 1)

        # ---- 交付标签页 ----------------------------------------------------
        # 交付清单按 Web 界面的表达重做：一行一个文件（类型图标 + 名称 + 大小），
        # 按用途分组；没有冻结交付、没有排除段落时给明确的空状态，而不是一块空白列表。
        self.delivery_label = QLabel(i18n.tr('交付状态：—'))
        self.delivery_label.setWordWrap(True)
        self.deliver_button = QPushButton(i18n.tr('审校完成后冻结交付'))
        self.deliver_button.setObjectName("primaryAction")
        self.deliver_button.clicked.connect(self.deliverRequested.emit)
        self.export_button = QPushButton(i18n.tr('导出当前译文副本…'))
        self.export_button.setIcon(icons.icon("download", 16))
        self.export_button.setIconSize(QSize(16, 16))
        self.export_button.clicked.connect(self.exportRequested.emit)
        self.save_snapshot_button = QPushButton(i18n.tr('保存冻结交付文件…'))
        self.save_snapshot_button.setIcon(icons.icon("save", 16))
        self.save_snapshot_button.setIconSize(QSize(16, 16))
        self.save_snapshot_button.clicked.connect(self._save_snapshot)
        self.open_asset_button = QPushButton(i18n.tr('用默认应用打开'))
        self.open_asset_button.setIcon(icons.icon("external-link", 16))
        self.open_asset_button.setIconSize(QSize(16, 16))
        self.open_asset_button.clicked.connect(self._open_asset)
        self.reveal_asset_button = QPushButton(i18n.tr('在 Finder 中显示'))
        self.reveal_asset_button.setIcon(icons.icon("folder", 16))
        self.reveal_asset_button.setIconSize(QSize(16, 16))
        self.reveal_asset_button.clicked.connect(self._reveal_asset)
        self.asset_list = QListWidget()
        self.asset_list.setObjectName("assetList")
        self.asset_list.setItemDelegate(style_module.AssetRowDelegate(self.asset_list))
        self.asset_list.setMouseTracking(True)
        self.asset_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.asset_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.asset_list.setMinimumHeight(0)
        self.asset_list.itemDoubleClicked.connect(self._reveal_asset)
        self.asset_list.currentItemChanged.connect(self._asset_selection_changed)

        self.asset_header_label = style_module.SectionLabel(i18n.tr('冻结交付文件'))
        self.asset_meta = QLabel("")
        self.asset_meta.setObjectName("assetMeta")
        asset_header = QHBoxLayout()
        asset_header.setSpacing(theme_module.SPACE["sm"])
        asset_header.addWidget(self.asset_header_label)
        asset_header.addStretch(1)
        asset_header.addWidget(self.asset_meta)

        self.asset_notice = style_module.NoticeStrip("", tone="warn")
        self.asset_notice.setVisible(False)
        self.asset_empty = style_module.EmptyState(
            icon="package", title=i18n.tr('尚未生成冻结交付'),
            hint=i18n.tr('完成翻译与审校后点「冻结交付」，这里会列出交付包里的每个文件、大小与用途；冻结后的版本不可修改。'))
        self.asset_empty.setVisible(False)

        asset_footer = QHBoxLayout()
        asset_footer.setSpacing(theme_module.SPACE["sm"])
        self.asset_hint = QLabel(i18n.tr('双击文件可在 Finder 中显示'))
        self.asset_hint.setObjectName("hint")
        asset_footer.addWidget(self.asset_hint)
        asset_footer.addStretch(1)
        asset_footer.addWidget(self.open_asset_button)
        asset_footer.addWidget(self.reveal_asset_button)

        delivery_actions = QHBoxLayout()
        delivery_actions.setSpacing(8)
        delivery_actions.addWidget(self.deliver_button)
        delivery_actions.addWidget(self.export_button)
        delivery_actions.addWidget(self.save_snapshot_button)
        delivery_actions.addStretch(1)

        delivery_card = Card()
        delivery_layout = QVBoxLayout(delivery_card)
        delivery_layout.setContentsMargins(16, 14, 16, 14)
        delivery_layout.setSpacing(10)
        delivery_layout.addWidget(self.delivery_label)
        delivery_layout.addLayout(delivery_actions)
        delivery_layout.addLayout(asset_header)
        delivery_layout.addWidget(self.asset_notice)
        delivery_layout.addWidget(self.asset_list)
        delivery_layout.addWidget(self.asset_empty)
        delivery_layout.addLayout(asset_footer)

        # 已排除段落：它们已不在工作台里，恢复必须从这里进行。
        self.excluded_list = QListWidget()
        self.excluded_list.setMaximumHeight(140)
        self.restore_excluded_button = QPushButton(i18n.tr('恢复所选段落'))
        self.restore_excluded_button.clicked.connect(self._restore_excluded)
        self.excluded_hint = QLabel(
            i18n.tr('被排除的段落不进入译文与交付；原文与排除时的译文完整保留。交付包里会附上这份排除清单。'))
        self.excluded_hint.setObjectName("hint")
        self.excluded_hint.setWordWrap(True)
        excluded_actions = QHBoxLayout()
        excluded_actions.addWidget(self.excluded_list, 1)
        excluded_actions.addWidget(self.restore_excluded_button)
        self.excluded_empty = style_module.EmptyState(
            icon="check-circle", title=i18n.tr('没有被排除的段落'),
            hint=i18n.tr('排除的段落会出现在这里，并附在交付包中；恢复后段落回到工作台。'))
        excluded_card = Card()
        excluded_layout = QVBoxLayout(excluded_card)
        excluded_layout.setContentsMargins(16, 14, 16, 14)
        excluded_layout.setSpacing(8)
        self.excluded_title = QLabel(i18n.tr('已排除段落（0）'))
        self.excluded_title.setObjectName("cardTitle")
        excluded_layout.addWidget(self.excluded_title)
        excluded_layout.addWidget(self.excluded_hint)
        excluded_layout.addLayout(excluded_actions)
        excluded_layout.addWidget(self.excluded_empty)

        # 交付页内容随清单长度变化：套一层滚动区，窗口不高时整页滚动，
        # 而不是把最后一组文件挤成半行。
        delivery_content = QWidget()
        delivery_content.setObjectName("pageCanvas")
        delivery_content_layout = QVBoxLayout(delivery_content)
        delivery_content_layout.setContentsMargins(0, 8, 0, 0)
        delivery_content_layout.setSpacing(theme_module.SPACE["md"])
        delivery_content_layout.addWidget(delivery_card)
        delivery_content_layout.addWidget(excluded_card)
        delivery_content_layout.addStretch(1)
        delivery_scroll = QScrollArea()
        delivery_scroll.setWidgetResizable(True)
        delivery_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        delivery_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        delivery_scroll.viewport().setAutoFillBackground(False)
        delivery_scroll.setWidget(delivery_content)

        delivery_page = QWidget()
        delivery_page_layout = QVBoxLayout(delivery_page)
        delivery_page_layout.setContentsMargins(0, 0, 0, 0)
        delivery_page_layout.addWidget(delivery_scroll)
        # 还没有清单时"打开/显示"就该是灰的，而不是点了没反应。
        self._asset_selection_changed()

        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.addTab(translation_page,
                         icons.icon("translate", 16, style_module.text_color("text-secondary")),
                         i18n.tr('翻译'))
        self.tabs.addTab(delivery_page,
                         icons.icon("package", 16, style_module.text_color("text-secondary")),
                         i18n.tr('交付'))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(12)
        layout.addWidget(header_card)
        layout.addWidget(self.tabs, 1)

        next_shortcut = QAction(i18n.tr('下一段'), self)
        next_shortcut.setShortcut(QKeySequence("Ctrl+Return"))
        next_shortcut.triggered.connect(lambda: self._step(1))
        self.addAction(next_shortcut)

    # ================= 数据 =================
    def load(self, job_id: str, config: adapter.EngineConfig,
             state: dict, rows: list[adapter.SegmentRow],
             snapshots: list[dict]) -> None:
        """Attach a job.  Only call this when switching jobs."""
        if self.job_id and self.job_id != job_id:
            self.flush_draft()
        self.job_id = job_id
        self.config = config
        self._state = state
        self._snapshots = snapshots
        self._all_rows = list(rows)
        self.title_label.setText(str(state.get("filename") or job_id))
        # 注意：这里**不**先 setRowCount(0)。清空再重建会让可访问性树经历
        # "全部行消失 → 重新出现"两次变更；macOS 27 上外部辅助功能客户端
        # （AI Computer Use / AX 扫描）正在遍历时，Qt 的 Cocoa 桥会踩到失效对象
        # 而崩溃（SIGSEGV/SIGBUS，栈顶为 libqcocoa + NSAccessibility…）。
        # 直接让 _render_segments() 一步把行数设成最终值；行数没变时它只重画内容，
        # 连一次结构变更都不产生。
        self._current_index = -1
        self._loaded_uid = ""
        self._excluded_signature = None      # 切换任务必须重建已排除列表
        self._set_editor_text("", "")
        # 搜索词只对上一个任务有意义：跟着切过去会让新任务的网格"看起来是空的"。
        # 状态筛选（待审校/已审校/只看人工修改）是"模式"，保留，且工具栏上看得见。
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._search = ""
        # 切换任务时重置交付面板的渲染状态：节流键已含任务标识，这里再显式清一次，
        # 让"换了任务就一定会重画"成为显然的事实。
        self._delivery_rendered = False
        self._delivery_key = ()
        self.apply(job_id, state, rows, self._last_runtime, snapshots,
                   refresh_delivery=True)

    def apply(self, job_id: str, state: dict, rows: list[adapter.SegmentRow],
              runtime: dict, snapshots: list[dict],
              *, refresh_delivery: bool = False) -> None:
        """Refresh everything that is *not* the text the user is typing."""
        self.job_id = job_id
        self._state = state
        self._last_runtime = runtime or {}
        self._snapshots = snapshots or []
        self._all_rows = list(rows)
        self._render_segments()
        self._render_excluded()
        self._render_runtime()
        self._render_delivery(force=refresh_delivery)
        self._render_segment_detail()

    # ================= 过滤（复用核心语义） =================
    def set_status_filter(self, name: str) -> None:
        self._status_filter = name if name in ("全部", "待审校", "已审校") else "全部"
        for key, button in self.status_buttons.items():
            button.setChecked(key == self._status_filter)
        self._filters_changed()

    def set_search(self, text: str) -> None:
        self.search_edit.setText(text)

    def _filters_changed(self) -> None:
        self._search = self.search_edit.text()
        self._only_edited = self.only_edited_box.isChecked()
        self._only_issues = self.only_issues_box.isChecked()
        self._render_segments()

    def filtered_indexes(self) -> list[int]:
        """Visible segment indexes, filtered by the core's own rules."""
        pairs = self._state.get("pairs") or []
        if not pairs:
            # Before translation there is nothing to filter on: show the source.
            return [row.index for row in self._all_rows]
        # 与"必须处理"芯片**同一条规则**（未解决的阻断级检查项）：勾选这个筛选就是
        # 要找到"什么在挡住交付"。此前用的是"任何检查项"（含已解决的），于是芯片说
        # "必须处理 1"、筛选却列出 3 行，用户找不出到底是哪一行。
        issues = sorted({
            int(item["segment_index"])
            for item in adapter.unresolved_blocking(self.job_id, self._state)
            if item.get("segment_index") is not None})
        return list(adapter.core.translation_visible_indexes(
            self._state, self._search,
            STATUS_FILTER_VALUES.get(self._status_filter, "全部"),
            filter_edited=self._only_edited, filter_issues=self._only_issues,
            issue_indexes=issues))

    def _visible_rows(self) -> list[adapter.SegmentRow]:
        visible = set(self.filtered_indexes())
        return [row for row in self._all_rows if row.index in visible]

    # ================= 渲染 =================
    def _sync_segment_placeholder(self) -> None:
        """有段落才显示网格与检查器；没有段落时正文换成一段说明。

        判据用 `_all_rows`（总段数）而不是过滤后的 `_rows`：筛选结果为空时
        表格空着是**正确**的，不能把它说成"这份文档没有段落"。
        """
        has_rows = bool(self._all_rows)
        self.toolbar_bar.setVisible(has_rows)
        self.translation_split.setVisible(has_rows)
        self.no_segments_card.setVisible(not has_rows)
        if has_rows:
            return
        state = self._state or {}
        if state.get("excluded_segments"):
            title = "所有段落都被排除了"
            hint = ("被排除的段落不进入译文与交付。可以在「交付」页的已排除清单里恢复，"
                    "原文与当前译文都完整保留。")
        elif state.get("paras") or state.get("pairs"):
            title = "当前筛选下没有段落"
            hint = "清空搜索词或把状态筛选切回「全部」，就能看到剩下的段落。"
        else:
            title = "还没有可编辑的段落"
            hint = ("这份文档目前没有切分出的段落。点上方「开始翻译」会导入并切分正文；"
                    "进度会自动保存，可以随时离开再回来。")
        self.no_segments_state.set_text(title, hint)

    def _render_segments(self) -> None:
        """按可见集合更新网格；重建时按稳定段身份恢复选中，不触碰编辑器。"""
        self._sync_segment_placeholder()
        self._rows = self._visible_rows()
        if self.segment_list.rowCount() != len(self._rows):
            self.segment_list.blockSignals(True)
            self.segment_list.setRowCount(len(self._rows))
            for position, row in enumerate(self._rows):
                self.segment_list.setItem(position, 0, QTableWidgetItem(""))
                self.segment_list.setItem(position, 1, QTableWidgetItem(""))
                self.segment_list.setItem(position, 2, QTableWidgetItem(""))
            # 重建之后必须立刻把内容画上去：否则要等到下一次后台刷新才显示文字，
            # 中间会看到一片空白行（打开任务或段落数变化时最明显）。
            for position, row in enumerate(self._rows):
                self._paint_row(position, row)
            self.segment_list.blockSignals(False)
            target = self._position_of_loaded_segment()
            if target is None:
                target = 0 if self._rows else -1
            if target >= 0:
                self.segment_list.selectRow(target)
            return
        for position, row in enumerate(self._rows):
            self._paint_row(position, row)

    def _paint_row(self, position: int, row: adapter.SegmentRow) -> None:
        tokens = theme_module.tokens(current_theme())
        tone = _segment_tone(row)
        number = self.segment_list.item(position, 0)
        source = self.segment_list.item(position, 1)
        target = self.segment_list.item(position, 2)
        if number is None or source is None or target is None:
            return
        marker = ("⚠" if row.stale_draft else "●") if row.has_draft else ""
        number_text = f"{marker}{row.index + 1}"
        if number.text() != number_text:
            number.setText(number_text)
        number.setForeground(QColor(_tone_color(tokens, tone)))
        number.setToolTip(i18n.tr_dynamic(row.status))
        source_text = _elide(row.source, 160)
        if source.text() != source_text:
            source.setText(source_text)
        target_text = _elide(row.draft_text if row.has_draft else row.target, 160)
        if target.text() != target_text:
            target.setText(target_text)
        target.setForeground(QColor(tokens["text-secondary"] if row.has_draft
                                    else tokens["text-primary"]))
        for column in range(3):
            item = self.segment_list.item(position, column)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, row.index)
                item.setData(Qt.ItemDataRole.UserRole + 1, row.segment_uid)

    def _render_runtime(self) -> None:
        runtime = self._last_runtime or {}
        status = str(runtime.get("headline_status") or "")
        label = str(runtime.get("headline") or "")
        detail = str(runtime.get("detail") or "")
        if str(runtime.get("status") or "") in ("failed", "stalled", "interrupted"):
            self.phase_label.setObjectName("statusError")
        else:
            self.phase_label.setObjectName("hint")
        style_module.repolish(self.phase_label)

        self.chip_state.setText(i18n.tr_dynamic(status) or "—")
        self.chip_state.set_tone(RUNTIME_STATUS_TONES.get(status, "muted"))
        # 状态芯片说了"已完成"，右边再写一遍"已完成"就是同一句话读两次：
        # 只有运行区带来的信息**超出**芯片时才显示这一行文字。
        note = detail or label or ""
        if note == self.chip_state.text():
            note = ""
        self.status_label.setText(note)
        self.status_label.setVisible(bool(note))

        completed = int(runtime.get("progress_completed") or 0)
        total = int(runtime.get("progress_total") or 0)
        pairs_count = len(self._state.get("pairs") or [])
        # 核心在翻译阶段给出的是**批次**进度（`stage_progress`），而
        # `progress_completed/total` 只在学术写作阶段才有值。此前界面只读后者，
        # 于是普通翻译运行期间进度条**整段是空的**、最后一下跳到满——不可信。
        # 现在直接用核心自己的进度与它的单位（"批次"），不另算一套。
        stage_progress = runtime.get("stage_progress") or {}
        stage_total = int(stage_progress.get("total") or 0)
        if total:
            self.chip_progress.setText(i18n.tr('{v0}/{v1} 段', v0=completed, v1=total))
            self.chip_progress.set_tone("info" if completed < total else "ok")
            self.progress.setRange(0, total)
            self.progress.setValue(min(completed, total))
        elif stage_total:
            done = int(stage_progress.get("completed") or 0)
            unit = str(stage_progress.get("unit") or "")
            self.chip_progress.setText(i18n.tr(
                "{v0}/{v1} {v2}", v0=done, v1=stage_total,
                v2=i18n.tr_dynamic(unit)).strip())
            self.chip_progress.set_tone("info" if done < stage_total else "ok")
            self.progress.setRange(0, stage_total)
            self.progress.setValue(min(done, stage_total))
        elif pairs_count:
            # 既没有单位进度也没有批次进度时，至少给出真实的段落数，而不是一个"—"。
            self.chip_progress.setText(i18n.tr('{v0} 段', v0=pairs_count))
            self.chip_progress.set_tone("muted")
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if status == "已完成" else 0)
        else:
            self.chip_progress.setText("—")
            self.chip_progress.set_tone("muted")
            self.progress.setRange(0, 1)
            self.progress.setValue(1 if status == "已完成" else 0)

        pairs = self._state.get("pairs") or []
        reviewed = sum(1 for pair in pairs if pair.get("reviewed"))
        if pairs:
            text = i18n.tr("审校 {v0}/{v1}", v0=reviewed, v1=len(pairs))
            tone = "ok" if reviewed == len(pairs) else "muted"
            # 标记数不等于门禁结论：恢复/旧格式数据里可能"全标了 reviewed"但当前
            # 审校收据已失效。此时必须说出来，否则用户以为已经可以交付。
            readiness = adapter.review_readiness(self.job_id, self._state)
            if readiness.get("required") and not readiness.get("ready"):
                text += i18n.tr("（需重新审校）")
                tone = "warn"
            self.chip_review.setText(text)
            self.chip_review.set_tone(tone)
        else:
            self.chip_review.setText(i18n.tr('审校 —'))
            self.chip_review.set_tone("muted")

        # 计数用核心自己的规则：检查项以 `resolved` 表示是否已解决，
        # 而不是 `status == "open"`（按后者会把已解决的阻断项也算进来）。
        blocking = adapter.unresolved_blocking(self.job_id, self._state)
        self.chip_blocking.setText(i18n.tr('必须处理 {v0}', v0=len(blocking)))
        self.chip_blocking.set_tone("danger" if blocking else "muted")

        # 按钮可见性一律跟随核心的运行视图契约，不再自己猜标签：
        # * `available_actions` 是核心给出的可执行动作（resume/retry/cancel/…）；
        # * 暂停是管线能力（pause_requested），只在真正运行时出现；
        # * 停滞任务核心也提供 cancel，因此取消按钮必须跟着动作走——否则用户
        #   在"暂无运行信号"时反而看不到取消入口。
        actions = set(runtime.get("available_actions") or [])
        raw_status = str(runtime.get("status") or "")
        running = raw_status in ("running", "pausing", "cancelling",
                                 "resume_requested", "starting", "queued",
                                 "waiting_external", "waiting_manual")
        self.start_button.setVisible("resume" in actions)
        self.start_button.setText(
            "继续翻译" if self._state.get("p1_done") else "开始翻译")
        self.pause_button.setVisible(running and raw_status == "running")
        self.cancel_button.setVisible(running or "cancel" in actions)
        self.retry_button.setVisible("retry" in actions)
        # 审校全部段落是 Header 上的"推荐下一步"：有段落、且任务没在跑时才给。
        self.review_all_button.setVisible(bool(self._all_rows) and not running)
        self._sync_header_actions()

    def _sync_header_actions(self) -> None:
        """动作区没有可见按钮时整行收掉：空白的一行比没有这一行更像没做完。"""
        buttons = (self.start_button, self.pause_button, self.cancel_button,
                   self.retry_button, self.review_all_button)
        self.header_actions_bar.setVisible(
            any(not button.isHidden() for button in buttons))

    def _render_delivery(self, *, force: bool = False) -> None:
        """刷新交付面板：有节流，但**绝不显示上一个任务的内容**。

        节流键必须包含任务标识：两个任务都是"已冻结交付 v1"时，只看"快照数 + 交付
        状态"会认为"没变化"，于是在切换任务后的 1 秒地板内继续显示**上一个任务**的
        资产清单（真实缺陷：A 声明了 PDF、B 没声明，切换后面板仍列着 `translation.pdf`）。
        """
        now = time.monotonic()
        key = (self.job_id, len(self._snapshots),
               str(self._state.get("delivery_status") or ""))
        unchanged = self._delivery_rendered and key == self._delivery_key
        if unchanged and not force and (
                now - self._delivery_preview_at) < DELIVERY_REFRESH_SECONDS:
            return
        if unchanged and (now - self._delivery_preview_at) < DELIVERY_FORCE_FLOOR_SECONDS:
            return
        preview = adapter.delivery_preview(self.job_id, self.config) \
            if self.job_id else {}
        self._delivery_preview = preview
        self._delivery_rendered = True
        self._delivery_preview_at = now
        self._delivery_key = key
        status = i18n.tr_dynamic(preview.get("status_label")) or "—"
        issues = preview.get("issues") or []
        detail = (i18n.tr("；阻断 {v0} 项：{v1}", v0=len(issues),
                          v1=_elide("；".join(str(i) for i in issues), 200))
                  if issues
                  else i18n.tr("；交付文件按任务声明生成，冻结后不可修改。"))
        self.delivery_label.setText(i18n.tr("交付状态：{v0}", v0=status) + detail)
        self.deliver_button.setEnabled(bool(preview.get("can_deliver")))
        snapshot = preview.get("snapshot") or {}
        if snapshot.get("current"):
            self.deliver_button.setText(
                i18n.tr('已冻结交付 v{v0}', v0=snapshot.get('latest', {}).get('snapshot_version')))
        elif preview.get("can_deliver"):
            self.deliver_button.setText(
                i18n.tr('冻结交付（生成 v{v0}）', v0=len(self._snapshots) + 1))
        else:
            self.deliver_button.setText(i18n.tr('审校完成后冻结交付'))
        self.export_button.setEnabled(bool(self._state.get("p2_done")))
        latest = self._snapshots[-1] if self._snapshots else None
        self.save_snapshot_button.setEnabled(latest is not None)
        if latest is not None:
            self.save_snapshot_button.setText(
                i18n.tr('保存冻结交付文件 v{v0}…', v0=latest.get('snapshot_version')))
        self.asset_list.clear()
        self._fill_delivery_assets(snapshot.get("latest"), snapshot=snapshot)

    def _fill_delivery_assets(self, latest: dict | None, *, snapshot: dict | None = None) -> None:
        """渲染交付文件清单：分组 + 图标 + 大小；空/缺失/不完整各自有话可说。"""
        latest = latest or {}
        assets = [item for item in (latest.get("assets") or []) if item.get("name")]

        def resolve(name: str):
            version = latest.get("snapshot_version")
            if not self.job_id or not version:
                return None
            return adapter.core.delivery_snapshot_asset_path(self.job_id, version, name)

        rows = _asset_rows(latest, resolve=resolve if self.job_id else None)
        self.asset_list.clear()
        selected_name = self._selected_asset_name()
        for row in rows:
            item = QListWidgetItem(str(row.get("title") or ""))
            item.setData(Qt.ItemDataRole.UserRole, row)
            if row.get("kind") == "header":
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            else:
                item.setToolTip(
                    i18n.tr("{v0}\n大小：{v1}", v0=row.get("title"),
                            v1=_human_size(row.get("size")))
                    + (i18n.tr("\n校验：sha256 {v0}…",
                               v0=str(row.get("sha256"))[:16])
                       if row.get("sha256") else "")
                    + (i18n.tr("\n文件在磁盘上找不到；快照本身不可修改，"
                               "请重新冻结交付。")
                       if row.get("missing") else ""))
            self.asset_list.addItem(item)

        version = latest.get("snapshot_version")
        frozen_at = _frozen_at_label(latest.get("created_at"))
        if not rows:
            self.asset_list.setVisible(False)
            self.asset_empty.setVisible(True)
            self.asset_meta.setText("")
            self.asset_header_label.setText(i18n.tr('冻结交付文件'))
        else:
            self.asset_list.setVisible(True)
            self.asset_empty.setVisible(False)
            self.asset_header_label.setText(i18n.tr('冻结交付文件'))
            summary = _asset_summary(
                [item for item in (latest.get("assets") or []) if item.get("name")])
            self.asset_meta.setText(
                " · ".join(part for part in (
                    f"v{version}" if version else "", summary,
                    f"冻结于 {frozen_at}" if frozen_at else "") if part))
            # 行高由委托固定：这里按真实行数给列表一个刚好放得下的高度，
            # 少量文件时不出现"最后一行只露一半"的内部滚动条；文件很多时
            # 才退回滚动（上限 14 行）。
            headers = sum(1 for row in rows if row.get("kind") == "header")
            files = len(rows) - headers
            natural = (headers * style_module.AssetRowDelegate.HEADER_HEIGHT
                       + files * style_module.AssetRowDelegate.ROW_HEIGHT + 2)
            cap = 14 * style_module.AssetRowDelegate.ROW_HEIGHT
            self.asset_list.setFixedHeight(min(natural, cap))

        missing = [row for row in rows
                   if row.get("kind") == "asset" and row.get("missing")]
        if missing:
            names = "、".join(str(row.get("title")) for row in missing[:3])
            more = f" 等 {len(missing)} 个" if len(missing) > 3 else ""
            self.asset_notice.set_text(
                i18n.tr('快照里有 {v0} 个文件在磁盘上缺失（{v1}{v2}）：冻结快照不可修改，请重新冻结交付或从备份恢复。', v0=len(missing), v1=names, v2=more))
        elif (snapshot or {}).get("integrity") is False and rows:
            self.asset_notice.set_text(i18n.tr('冻结交付完整性校验未通过：请重新冻结交付。'))
        else:
            self.asset_notice.set_text("")

        # 选回用户刚才选中的文件（刷新是每秒发生的，选择不能被轮询清掉）。
        for index in range(self.asset_list.count()):
            item = self.asset_list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "asset" and item.text() == selected_name:
                self.asset_list.setCurrentItem(item)
                break
        self._asset_selection_changed()

    def _asset_selection_changed(self, *_args) -> None:
        """选中态决定"打开/在 Finder 中显示"是否可用（点分组标题不算选中文件）。"""
        selectable = bool(self._selected_asset_name())
        if selectable == getattr(self, "_asset_selectable", None):
            return
        self._asset_selectable = selectable
        tip = "" if selectable else "先选择一个交付文件"
        self.open_asset_button.setEnabled(selectable)
        self.reveal_asset_button.setEnabled(selectable)
        self.open_asset_button.setToolTip(tip)
        self.reveal_asset_button.setToolTip(tip)

    def _render_excluded(self) -> None:
        records = adapter.excluded_segments(self.job_id, self._state) \
            if self.job_id else []
        # 内容没变就**不动列表**：后台每 1 秒刷新一次，clear()+重建会让
        # ① 用户在这一栏里的选择被清掉（"恢复本段"随即失效），
        # ② 可访问性树的子元素每秒换一批身份——macOS 27 上外部 AX 扫描遍历时
        #    正是这类churn 把 Qt Cocoa 桥的失效窗口拉长（见 R61 记录）。
        signature = tuple(
            (record.get("excluded_id"), record.get("segment_index"),
             record.get("source"), record.get("target"), record.get("reason"),
             bool(record.get("restorable", True))) for record in records)
        unchanged = (signature == self._excluded_signature
                     and self.excluded_list.count() == len(records))
        self._excluded_signature = signature
        if not unchanged:
            self._rebuild_excluded(records)
        self.excluded_title.setText(i18n.tr('已排除段落（{v0}）', v0=len(records)))
        self.restore_excluded_button.setEnabled(
            any(record.get("restorable", True) for record in records))
        # 空清单不留一块空白列表：换成一句"没有被排除的段落"。只在状态变化时
        # 切换可见性，避免每次轮询都动一遍可见性与控件树。
        has_records = bool(records)
        if has_records != getattr(self, "_excluded_has_records", None):
            self._excluded_has_records = has_records
            self.excluded_list.setVisible(has_records)
            self.excluded_hint.setVisible(has_records)
            self.restore_excluded_button.setVisible(has_records)
            self.excluded_empty.setVisible(not has_records)

    def _rebuild_excluded(self, records: list[dict]) -> None:
        self.excluded_list.clear()
        for record in records:
            item = QListWidgetItem(i18n.tr(
                "第 {v0} 段 · {v1}{v2}",
                v0=(record.get("segment_index") or 0) + 1,
                v1=_elide(record.get("source"), 60),
                v2=(f" · {record['reason']}" if record.get("reason") else "")))
            item.setData(Qt.ItemDataRole.UserRole, record.get("excluded_id"))
            item.setToolTip(i18n.tr(
                "原文：{v0}\n排除时的译文：{v1}",
                v0=record.get("source") or "", v1=record.get("target") or ""))
            self.excluded_list.addItem(item)
        self.excluded_title.setText(i18n.tr('已排除段落（{v0}）', v0=len(records)))
        self.restore_excluded_button.setEnabled(
            any(record.get("restorable", True) for record in records))

    def _restore_excluded(self) -> None:
        item = self.excluded_list.currentItem()
        if item is None:
            return
        self.includeRequested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _render_segment_detail(self) -> None:
        if not (0 <= self._current_index < len(self._all_rows)) or not self._rows:
            self.segment_status.setText(i18n.tr('尚未选择段落'))
            self.qa_view.setHtml("")
            self.submit_button.setEnabled(False)
            self.review_button.setEnabled(False)
            for label in (self.fact_state, self.fact_review, self.fact_source,
                          self.fact_terms, self.fact_excluded):
                label.setText("—")
            self.retranslate_button.setEnabled(False)
            self.exclude_button.setVisible(True)
            self.discard_draft_button.setVisible(False)
            return
        row = next((item for item in self._all_rows
                    if item.index == self._current_index), None)
        if row is None:
            return
        position = next((index for index, item in enumerate(self._rows)
                         if item.index == row.index), -1)
        self.submit_button.setEnabled(not row.excluded)
        self.review_button.setEnabled(not row.excluded)
        self.retranslate_button.setEnabled(not row.excluded)
        self.exclude_button.setVisible(not row.excluded)
        self.discard_draft_button.setVisible(bool(row.has_draft))
        parts = [f"段落 #{row.index + 1}",
                 f"{position + 1}/{len(self._rows)}" if position >= 0 else "—",
                 row.status]
        if row.human_edited and row.status != "人工修改":
            parts.append("人工修改")
        if row.has_draft:
            parts.append("⚠ 草稿基于旧译文，提交会覆盖较新的文本"
                         if row.stale_draft else "有未提交草稿")
        self.segment_status.setText(" · ".join(parts))
        self.fact_state.setText(row.status)
        self.fact_review.setText("已审校" if row.reviewed else "未审校")
        self.fact_source.setText("人工修改" if row.human_edited else "机器翻译")
        reason = ""
        for record in adapter.excluded_segments(self.job_id, self._state):
            if record.get("segment_index") == row.index:
                reason = str(record.get("reason") or "")
                break
        self.fact_excluded.setText("已排除" if row.excluded else "未排除")
        if reason:
            self.fact_excluded.setText(i18n.tr('已排除（{v0}）', v0=reason))
        pair = None
        pairs = self._state.get("pairs") or []
        if 0 <= row.index < len(pairs):
            pair = pairs[row.index]
        # `core.translation_terms_for_pair` 的契约是 [(source, target, kind), …]，
        # 不是字典列表：按字典取键会直接抛 AttributeError（真实运行中已出现过）。
        terms = adapter.core.translation_terms_for_pair(self._state, pair) \
            if pair else []
        labels = []
        for item in terms[:6]:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                labels.append(f"{item[0]} → {item[1]}")
            elif isinstance(item, dict):
                labels.append(str(item.get("target") or item.get("source") or ""))
            else:
                labels.append(str(item))
        self.fact_terms.setText("、".join(label for label in labels if label) or "—")
        all_findings = [item for item in adapter.findings(self.job_id, self._state)
                        if item.get("segment_index") == row.index]
        open_findings = [item for item in all_findings if not item.get("resolved")]
        resolved_count = len(all_findings) - len(open_findings)
        if open_findings:
            rows_html = "".join(
                f"<li>[{item.get('severity') or 'info'}] "
                f"{str(item.get('summary') or item.get('reason') or '')}</li>"
                for item in open_findings)
            suffix = f"<p>另有 {resolved_count} 项已解决。</p>" if resolved_count else ""
            self.qa_view.setHtml(f"<ul>{rows_html}</ul>{suffix}")
        else:
            self.qa_view.setHtml(
                i18n.tr("<p>没有未解决的检查项。</p>")
                + (i18n.tr("<p>本段有 {v0} 项已解决。</p>", v0=resolved_count)
                   if resolved_count else ""))

    # ================= 选择 / 编辑器 =================
    def _position_of_loaded_segment(self) -> int | None:
        if not self._loaded_uid:
            return None
        for position, row in enumerate(self._rows):
            if row.segment_uid == self._loaded_uid:
                return position
        return None

    def _row_selected(self, position: int) -> None:
        if position < 0 or position >= len(self._rows):
            return
        row = self._rows[position]
        if row.segment_uid == self._loaded_uid:
            # Same segment: the editor owns the text.  Refreshing the grid or the
            # facts must not reload it (that would clear the cursor, the undo
            # history and any uncommitted edit).
            self._current_index = row.index
            self._render_segment_detail()
            return
        if row.excluded:
            self._current_index = row.index
            self.segment_status.setText(i18n.tr('该段落已被排除，不参与翻译与交付。'))
            return
        if self._dirty and self._loaded_uid:
            self.flush_draft()
        self._current_index = row.index
        text = row.draft_text if row.has_draft else row.target
        self._set_editor_text(row.source, text,
                              language=self._state.get("target_lang"))
        self._loaded_uid = row.segment_uid
        self._render_segment_detail()

    def _set_editor_text(self, source: str, target: str,
                         language: str | None = None) -> None:
        from folith.textual import text_direction

        self._loading = True
        try:
            self.source_view.setPlainText(source or "")
            direction = Qt.LayoutDirection.RightToLeft \
                if text_direction(source or "", language) == "rtl" \
                else Qt.LayoutDirection.LeftToRight
            self.source_view.setLayoutDirection(direction)
            if self.target_edit.toPlainText() != (target or ""):
                self.target_edit.setPlainText(target or "")
            self.target_edit.setLayoutDirection(
                Qt.LayoutDirection.RightToLeft
                if text_direction(target or "", language) == "rtl"
                else Qt.LayoutDirection.LeftToRight)
            self.target_edit.document().setModified(False)
        finally:
            self._loading = False
        self._dirty = False

    def _text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._autosave.start()

    def flush_draft(self) -> bool:
        """Persist the uncommitted editor text through the core's draft file."""
        if not self._dirty or not self._loaded_uid or not self.job_id:
            return True
        text = self.target_edit.toPlainText()
        committed = ""
        for row in self._all_rows:
            if row.segment_uid == self._loaded_uid:
                committed = row.target
                break
        try:
            self.draftSaved.emit(self.job_id, self._loaded_uid, text)
            adapter.save_draft(self.job_id, self._loaded_uid, text, committed)
        except Exception as exc:  # noqa: BLE001 - never lose the text silently
            self.segment_status.setText(i18n.tr('草稿保存失败：{v0}', v0=exc))
            return False
        self._dirty = False
        marker = i18n.tr(" · 已保存草稿")
        self.segment_status.setText(
            self.segment_status.text().split(marker)[0] + marker)
        return True

    def draft_text(self) -> str:
        return self.target_edit.toPlainText()

    def current_uid(self) -> str:
        return self._loaded_uid

    def current_index(self) -> int:
        return self._current_index

    def has_unsaved_editor_text(self) -> bool:
        return bool(self._dirty)

    def selected_indexes(self) -> list[int]:
        """Segment indexes currently selected in the grid, in grid order."""
        positions = sorted({item.row() for item in
                            self.segment_list.selectedItems()})
        return [self._rows[position].index for position in positions
                if 0 <= position < len(self._rows)]

    def _selection_changed(self) -> None:
        indexes = self.selected_indexes()
        self.review_selected_button.setEnabled(len(indexes) > 1)
        self.review_selected_button.setText(
            f"审校所选 {len(indexes)} 段" if len(indexes) > 1 else "审校所选段落")

    def _copy_source_to_target(self) -> None:
        """把原文放进编辑器（只改未提交内容，不写业务真值）。"""
        if self._current_index < 0:
            return
        row = next((item for item in self._all_rows
                    if item.index == self._current_index), None)
        if row is None:
            return
        self.target_edit.setPlainText(row.source)
        self._dirty = True
        self._autosave.start()

    def reload_current_segment(self, *, force: bool = False) -> None:
        """把当前段落的**已提交**译文重新读进编辑器。

        后台刷新绝不能碰编辑器（会清掉光标、撤销历史与未提交文本）。但显式动作
        （放弃草稿、定点重译）之后必须重新读：否则编辑器还留着旧文本，用户一按
        "保存并提交"就把较新的译文覆盖回去了。

        编辑器里有未提交内容时**默认不重载**（那是用户的工作），此时该段会以
        "草稿基于旧译文"的警示呈现，由用户决定保留还是放弃。
        """
        if self._dirty and not force:
            return
        position = next((index for index, row in enumerate(self._rows)
                         if row.index == self._current_index), -1)
        if position < 0:
            return
        self._loaded_uid = ""
        self._dirty = False
        self._row_selected(position)

    def _step(self, delta: int) -> None:
        if not self._rows:
            return
        position = next((index for index, row in enumerate(self._rows)
                         if row.index == self._current_index), -1)
        target = position + delta
        if 0 <= target < len(self._rows):
            self.segment_list.selectRow(target)

    def _submit(self) -> None:
        if self._current_index < 0:
            return
        self.flush_draft()
        self.editSubmitted.emit(self._current_index)

    def save_and_next(self) -> None:
        index = self._current_index
        self._submit()
        self._step(1)
        if self._current_index == index:
            self.editSubmitted.emit(index)

    def focus_editor(self) -> None:
        self.target_edit.setFocus()

    # ---- 供测试与探针使用的稳定接口（不依赖具体控件类型） ----
    def visible_segment_count(self) -> int:
        return len(self._rows)

    def select_segment(self, index: int) -> bool:
        for position, row in enumerate(self._rows):
            if row.index == index:
                self.segment_list.selectRow(position)
                return True
        return False

    def select_segments(self, indexes: list[int]) -> None:
        self.segment_list.clearSelection()
        wanted = set(indexes)
        for position, row in enumerate(self._rows):
            if row.index in wanted:
                for column in range(len(self.COLUMNS)):
                    item = self.segment_list.item(position, column)
                    if item is not None:
                        item.setSelected(True)
        self._selection_changed()

    def segment_row_text(self, row: int, column: int = 1) -> str:
        item = self.segment_list.item(row, column)
        return item.text() if item is not None else ""

    def set_editor_text_for_test(self, text: str) -> None:
        """Used by automation to simulate typing (never by production code)."""
        self.target_edit.setPlainText(text)

    # ---- 交付资产 ----
    def _asset_path(self, name: str) -> str:
        latest = self._snapshots[-1] if self._snapshots else None
        if latest is None:
            return ""
        for asset in latest.get("assets") or []:
            if str(asset.get("name")) == name:
                return str(adapter.core.delivery_snapshot_asset_path(
                    self.job_id, latest.get("snapshot_version"), name))
        return ""

    def _selected_asset_item(self):
        """当前选中的**文件**行；选中分组标题或没有选择时返回 None。"""
        item = self.asset_list.currentItem()
        if item is not None:
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "asset" and data.get("title"):
                return item
        if self.asset_list.count():
            for index in range(self.asset_list.count()):
                candidate = self.asset_list.item(index)
                data = candidate.data(Qt.ItemDataRole.UserRole) or {}
                if data.get("kind") == "asset":
                    return candidate
        return None

    def _selected_asset_name(self) -> str:
        item = self._selected_asset_item()
        if item is None:
            return ""
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        return str(data.get("title") or item.text())

    def _open_asset(self) -> None:
        path = self._asset_path(self._selected_asset_name())
        if path:
            self.openAssetRequested.emit(path)

    def _reveal_asset(self, *_args) -> None:
        path = self._asset_path(self._selected_asset_name())
        if path:
            self.revealRequested.emit(path)

    def _save_snapshot(self) -> None:
        if not self._snapshots:
            return
        self.saveSnapshotRequested.emit(
            int(self._snapshots[-1].get("snapshot_version") or 1))
