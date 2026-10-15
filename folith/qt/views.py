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

import dataclasses
import time

from PySide6.QtCore import QEvent, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from . import i18n
from . import adapter
from . import components
from . import motion
from . import icons
from .activity_panel import ActivityPanel
from .cases_panel import CasesPanel
from .followup_panel import FollowupPanel
from .pdf_panel import PdfPanel
from .qa_panel import QAPanel
from .report_panel import ReportPanel
from .review_panel import ReviewPanel
from .terms_panel import TermsPanel
from .setup_panel import TaskSetupPanel
from .task_details_panel import TaskDetailsPanel
from .stagebar import StageBar
from . import stage as stage_model
from . import style as style_module
from . import theme as theme_module
from . import segment_assist
from . import segment_editor
from .style import Card, Chip

DRAFT_AUTOSAVE_MS = 1500

#: 核心运行视图里"正在占用管线"的状态（头部进度条与暂停/取消按钮只在这些状态出现）。
#: 可以请求暂停的运行状态（核心在当前操作结束后暂停）。
PAUSABLE_STATUSES = frozenset({"running", "waiting_external", "starting", "resume_requested"})
RUNNING_STATUSES = frozenset({"running", "pausing", "cancelling", "resume_requested",
                              "starting", "queued", "waiting_external", "waiting_manual"})
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


#: 任务表单元格对齐：文字列左对齐（与表头同一侧），计数与时间右对齐。
#: 值保持 `AlignmentFlag`（**不要** `int(...)`）：PySide6 里 `setTextAlignment(int)`
#: 是已弃用重载，会逐次调用打 DeprecationWarning。
ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


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


def _row_percent(row) -> int | None:
    """从进度/状态文案里取出百分比（"正在翻译 · 63%"），没有就返回 None。"""
    import re

    runtime = getattr(row, "runtime", {}) or {}
    total = int(runtime.get("total_units") or 0)
    if total > 0:
        return max(0, min(100, round(100 * int(runtime.get("completed_units") or 0) / total)))
    saved = getattr(row, "progress_percent", None)
    if saved is not None:
        return max(0, min(100, int(saved)))
    for text in (getattr(row, "progress_label", ""), getattr(row, "status_label", "")):
        match = re.search(r"(\d{1,3})\s*%", str(text or ""))
        if match:
            return max(0, min(100, int(match.group(1))))
    return None


def library_run_view(row) -> tuple[str, str]:
    """Same runtime colour semantics as the workbench, from lightweight reads."""
    status = getattr(row, "runtime_status", "")
    note = i18n.tr_dynamic(row.status_label)
    if status == "stalled":
        note = i18n.tr('暂无运行信号')
    elif status == "failed":
        note = i18n.tr('步骤失败') + (" · " + row.error if row.error else "")
    elif status in ("paused", "interrupted", "cancelled", "idle_incomplete"):
        note = i18n.tr_dynamic({"paused": "已暂停", "interrupted": "上次运行已中断",
                                "cancelled": "任务已取消", "idle_incomplete": "未完成"}[status])
    tone, line = run_status_view(
        {"status": status, "runtime": getattr(row, "runtime", {})},
        note=note, running=row.active,
        completed=0, total=0, unit="")
    return tone, line or i18n.tr_dynamic(row.status_label)


def _row_format(row) -> str:
    name = str(getattr(row, "filename", "") or "")
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    return suffix.upper()[:4] if suffix else ""


class _TaskCard(Card):
    """首页卡片视图里的一张任务卡（Golden Gate 版式）：单击选中、双击打开。

    上半是"文档"——一张纸的示意 + 左上格式角标 + 右上"多久前更新"；下半是文件名、
    段数与目标语言、状态点 + 状态文案，运行中/有百分比时再给一条细进度条。

    与表格共用同一份选中状态（卡片只是把表格行"画"成卡片），因此底部动作
    （打开/移动/删除）在两种视图下语义一致，不需要第二套选择逻辑。
    """

    clicked = Signal(str)
    activated = Signal(str)
    attentionRequested = Signal(str)
    #: 卡片的最小宽度（px）：列数 = 可用宽度能放下几张这么宽的卡。
    MIN_WIDTH = 200

    def __init__(self, row: adapter.JobRow, *, tone: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskCard")
        self.job_id = row.job_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(i18n.tr('单击选中；双击打开任务'))
        self.setMinimumWidth(self.MIN_WIDTH)
        # 卡片视图是默认视图：纯键盘用户必须能用 Tab 进到卡片、方向键换卡、回车打开。
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(row.title or "")
        self._selected = False
        # 属性一开始就要有值：QSS 的 [selected="true"] 与外部探测都读它，
        # 只在状态变化时设置会留下"未定义"的中间态。
        self.setProperty("selected", "false")

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, theme_module.SPACE["md"])
        column.setSpacing(theme_module.SPACE["sm"])

        # ---- 上半：文档示意 ----------------------------------------------
        thumb = QFrame()
        thumb.setObjectName("tileThumb")
        thumb.setFixedHeight(124)
        thumb_layout = QGridLayout(thumb)
        thumb_layout.setContentsMargins(theme_module.SPACE["sm"], theme_module.SPACE["sm"],
                                        theme_module.SPACE["sm"], 0)
        badge_text = _row_format(row)
        # ⚠️ 子控件一律先给父对象再改可见性：对无父控件 setVisible(True) 会把它
        # 当成顶层窗口弹出来，抢走主窗口的激活与键盘焦点（实测）。
        self.badge = QLabel(badge_text, thumb)
        self.badge.setObjectName("tileBadge")
        self.badge.setVisible(bool(badge_text))
        self.time_label = QLabel(components.relative_time(row.updated_at))
        self.time_label.setObjectName("tileTime")
        self.time_label.setToolTip((row.updated_at or "").replace("T", " ")[:16])
        unreadable = bool(str(getattr(row, "error", "") or "").strip())
        paper = components.DocThumb(66, 86, faded=unreadable)
        self.paper = paper
        thumb_layout.addWidget(self.badge, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        thumb_layout.addWidget(self.time_label, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        thumb_layout.addWidget(paper, 0, 0, 2, 3, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
        thumb_layout.setColumnStretch(1, 1)
        column.addWidget(thumb)

        # ---- 下半：文字 ----------------------------------------------------
        text = QVBoxLayout()
        text.setContentsMargins(theme_module.SPACE["md"], 0, theme_module.SPACE["md"], 0)
        text.setSpacing(theme_module.SPACE["xxs"] + 1)
        title = style_module.ElideLabel(row.title or i18n.tr("未命名任务"),
                                        mode=Qt.TextElideMode.ElideMiddle)
        title.setObjectName("taskCardTitle")
        title.setToolTip(row.title or "")
        meta_parts = [part for part in (
            i18n.tr("{v0} 段", v0=row.segment_count) if row.segment_count else "",
            i18n.tr_dynamic(row.target_lang) if row.target_lang else "",
        ) if part]
        meta = QLabel(" · ".join(meta_parts) or "—")
        meta.setObjectName("taskCardMeta")

        run_tone, run_line = library_run_view(row)
        tone = {"running": "info", "waiting": "info", "retrying": "warn",
                "stalled": "warn", "failed": "danger", "done": "ok",
                "paused": "muted", "idle": "muted"}.get(run_tone, tone)
        if row.runtime_status == "unreadable":
            tone = "danger"
        hollow = tone == "muted" and not row.active
        dot = components.StatusDot({"ok": "success", "warn": "warning", "danger": "danger",
                                    "info": "accent", "brand": "brand-ink"}.get(tone, "text-muted"),
                                   hollow=hollow)
        status_text = (run_line if run_tone in ("running", "waiting", "retrying", "stalled", "failed", "paused")
                       else i18n.tr_dynamic(row.status_label)) or "—"
        percent = _row_percent(row)
        if row.active and percent is not None and f"{percent}%" not in status_text:
            status_text = f"{status_text} · {percent}%"
        self.status_label = style_module.ElideLabel(status_text, mode=Qt.TextElideMode.ElideRight)
        self.status_label.setObjectName("taskCardStatus")
        self.status_label.setProperty("state", tone)
        status_row = QHBoxLayout()
        status_row.setSpacing(theme_module.SPACE["xs"] + 2)
        status_row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row.addWidget(self.status_label, 1)
        self.attention = CheckStatusButton(self)
        self.attention.set_count(row.attention_count, row.attention_tone or "warning")
        self.attention.setVisible(bool(row.attention_count))
        self.attention.clicked.connect(lambda: self.attentionRequested.emit(self.job_id))
        status_row.addWidget(self.attention)
        self.setAccessibleDescription(status_text + (" · " + i18n.tr('需要处理 · {v0}',
                                      v0=row.attention_count) if row.attention_count else ""))
        # 兼容旧接口：状态芯片仍然存在（外部/测试会读它的文案），但不再显示。
        self.chip = Chip(_elide(i18n.tr_dynamic(row.status_label) or "—", 12), tone)
        self.chip.setParent(self)
        self.chip.setVisible(False)

        text.addWidget(title)
        text.addWidget(meta)
        text.addSpacing(theme_module.SPACE["xxs"])
        text.addLayout(status_row)
        self.progress = None
        if percent is not None:
            self.progress = components.ProgressLine(percent, token=components.PROGRESS_TONES[run_tone])
            text.addSpacing(theme_module.SPACE["xxs"])
            text.addWidget(self.progress)
        column.addLayout(text)

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

    #: 方向键换卡：由 HomeView 接上（它知道卡片的顺序与列数）。
    navigate = Signal(str, int)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 键盘焦点到了哪张卡，哪张卡就是选中项（与表格"当前行即选中行"同一条规则）。
        self.clicked.emit(self.job_id)
        super().focusInEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activated.emit(self.job_id)
            event.accept()
            return
        if key == Qt.Key.Key_Space:
            self.clicked.emit(self.job_id)
            event.accept()
            return
        steps = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -100,
                 Qt.Key.Key_Down: 100}
        if key in steps:
            self.navigate.emit(self.job_id, steps[key])
            event.accept()
            return
        super().keyPressEvent(event)


HOME_SORTS = (("最近更新", "updated"), ("最早更新", "oldest"),
              ("按名称", "name"), ("按状态", "status"))


def sort_job_rows(rows: list[adapter.JobRow], key: str) -> list[adapter.JobRow]:
    """首页排序。运行中的任务始终排在最前；同一键下保持原顺序（稳定排序）。"""
    rows = list(rows)
    if key == "oldest":
        rows.sort(key=lambda row: row.updated_at or "")
    elif key == "name":
        rows.sort(key=lambda row: (row.title or row.filename or "").casefold())
    elif key == "status":
        rows.sort(key=lambda row: row.status_label or "")
    else:
        rows.sort(key=lambda row: row.updated_at or "", reverse=True)
    rows.sort(key=lambda row: not row.active)
    return rows


class HomeView(QWidget):
    """任务库：品牌栏 + 指标芯片 + 状态化任务表 + 空状态。"""

    importRequested = Signal()
    openRequested = Signal(str)
    checkRequested = Signal(str)
    deleteRequested = Signal(str)
    refreshRequested = Signal()
    #: 侧栏「任务库」：从项目页 / 术语页回到任务库。
    tasksRequested = Signal()
    manageProjectsRequested = Signal()
    libraryRequested = Signal()
    moveToProjectRequested = Signal(list)
    settingsRequested = Signal()
    connectionRequested = Signal()          # 侧栏连接状态芯片：打开逐项状态的浮窗

    COLUMNS = ("文档", "状态", "目标语言", "段落", "更新时间")
    #: 侧栏品牌字标的逻辑高度（px）。Golden Gate 版式里它在侧栏左下角、设置按钮左侧。
    LOGO_HEIGHT = 48
    #: 侧栏宽度（px）。
    SIDEBAR_WIDTH = 240

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[adapter.JobRow] = []
        self._last_row_count = 0
        #: 脚注上的"覆盖消息"（读列表失败之类）。为空时脚注显示选中摘要。
        self._status_override = ""

        # ---- 品牌（Golden Gate：移到侧栏左下角，与设置按钮同一行） ---------------
        from . import branding  # noqa: F401 - _refresh_logo 用

        self._logo_label = QLabel()
        self._logo_theme = ""
        self._refresh_logo()

        # ---- 侧栏动作（全部是真实入口，不放没实现的导航） -------------------
        # 「导入文档…」是侧栏顶部唯一的实心按钮：任务库的第一动作。
        self.import_button = QPushButton(i18n.tr('导入文档…'))
        self.import_button.setObjectName("heroAction")
        self.import_button.setIcon(icons.icon("upload", 18, "text-on-hero"))
        self.import_button.setIconSize(QSize(18, 18))
        self.import_button.setMinimumHeight(40)
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self.importRequested.emit)
        style_module.fit_button(self.import_button, max_width=self.SIDEBAR_WIDTH - 24)

        def nav_button(label: str, icon_name: str, *, checkable: bool = False) -> QPushButton:
            button = components.nav_item(label, icon_name, checkable=checkable)
            # 侧栏是固定宽度：长语言（英/俄/阿）必须省略显示，否则按钮被截断
            style_module.fit_button(button, max_width=self.SIDEBAR_WIDTH - 24)
            return button

        # 侧栏是窗口级的导航：任务库 / 项目 / 术语与翻译记忆三页共用（窗口通过
        # :meth:`take_sidebar` 把它放到页面栈左侧），当前页由 :meth:`set_current_nav` 点亮。
        self.tasks_button = nav_button(i18n.tr('任务库'), "projects", checkable=True)
        self.tasks_button.setChecked(True)
        self.tasks_button.clicked.connect(self.tasksRequested.emit)
        self.manage_projects_button = nav_button(i18n.tr('项目'), "folder", checkable=True)
        self.manage_projects_button.setToolTip(i18n.tr('新建、重命名、归档项目，管理项目知识'))
        self.manage_projects_button.clicked.connect(self.manageProjectsRequested.emit)
        self.library_button = nav_button(i18n.tr('术语与翻译记忆'), "termbase", checkable=True)
        self.library_button.setToolTip(i18n.tr('跨任务浏览术语与翻译记忆，并审核 Agent 发现的候选词条'))
        self.library_button.clicked.connect(self.libraryRequested.emit)
        self._nav_buttons = {"tasks": self.tasks_button,
                             "projects": self.manage_projects_button,
                             "library": self.library_button}

        # 设置：侧栏左下角的圆形按钮（文案仍然是「设置…」，作为无障碍名称与提示）。
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("roundButton")
        self.settings_button.setText(i18n.tr('设置…'))
        self.settings_button.setToolTip(i18n.tr('设置…'))
        self.settings_button.setAccessibleName(i18n.tr('设置…'))
        self.settings_button.setIcon(icons.icon("settings", 16, "text-secondary"))
        self.settings_button.setIconSize(QSize(16, 16))
        self.settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.clicked.connect(self.settingsRequested.emit)

        # 「最近项目」：真实的项目分组，点一下 = 只看这个项目的任务（与页头的项目筛选同源）。
        self.project_nav_label = QLabel(i18n.tr('最近项目'))
        self.project_nav_label.setObjectName("navSection")
        self.project_nav_label.setVisible(False)
        self.project_nav_box = QVBoxLayout()
        self.project_nav_box.setContentsMargins(0, 0, 0, 0)
        self.project_nav_box.setSpacing(theme_module.SPACE["xxs"])
        self._project_nav_buttons: dict[str, QPushButton] = {}

        # ---- 侧栏底部：引擎状态条 + 品牌行（字标 + 设置） ---------------------
        self.engine_dot = QLabel()
        self.engine_dot.setObjectName("engineDot")
        self.engine_dot.setFixedSize(8, 8)
        # ⚠️ 属性名是 **state** 不是 tone：`QLabel[tone="…"]` 是 chip 的全局属性选择器，
        # 同名会让这个点套上 chip 的底色/描边/padding（详见 style.py 的说明）。
        self.engine_dot.setProperty("state", "warn")
        self.engine_label = QLabel(i18n.tr('未配置'))
        self.engine_label.setObjectName("engineState")
        self.engine_model = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
        self.engine_model.setObjectName("engineModel")
        self.engine_model.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.engine_model.setVisible(False)
        engine_card = QFrame()
        engine_card.setObjectName("engineCard")
        # 连接状态芯片（12-B）：「已配置」不等于「能用」，点开逐项看翻译 / 审校 / 视觉。
        engine_card.setCursor(Qt.CursorShape.PointingHandCursor)
        engine_card.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        engine_card.setAccessibleName(i18n.tr('模型连接状态'))
        engine_card.installEventFilter(self)
        self.engine_card = engine_card
        engine_row = QHBoxLayout(engine_card)
        engine_row.setContentsMargins(theme_module.SPACE["sm"] + 2, theme_module.SPACE["sm"],
                                      theme_module.SPACE["sm"] + 2, theme_module.SPACE["sm"])
        engine_row.setSpacing(theme_module.SPACE["sm"])
        engine_row.addWidget(self.engine_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        engine_row.addWidget(self.engine_label, 0, Qt.AlignmentFlag.AlignVCenter)
        engine_row.addWidget(self.engine_model, 1, Qt.AlignmentFlag.AlignVCenter)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(theme_module.SPACE["xxs"], 0, 0, 0)
        brand_row.setSpacing(theme_module.SPACE["sm"])
        brand_row.addWidget(self._logo_label, 0, Qt.AlignmentFlag.AlignVCenter)
        brand_row.addStretch(1)
        brand_row.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)

        sidebar_footer = QFrame()
        sidebar_footer.setObjectName("sidebarFooterGG")
        footer_layout = QVBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(theme_module.SPACE["md"])
        footer_layout.addWidget(engine_card)
        footer_rule = QFrame()
        footer_rule.setObjectName("hairline")
        footer_rule.setFixedHeight(1)
        footer_layout.addWidget(footer_rule)
        footer_layout.addLayout(brand_row)

        sidebar = QWidget()
        self.sidebar = sidebar
        sidebar.setObjectName("sidebar")
        # Golden Gate：侧栏贴满窗口左侧（边到边），右侧一条发丝线。宽 240：底部的品牌行
        # 要放下 48px 高的字标（≈174px）和 30px 的圆形设置按钮。
        sidebar.setFixedWidth(self.SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["lg"],
                                theme_module.SPACE["md"], theme_module.SPACE["md"])
        side.setSpacing(theme_module.SPACE["xxs"])
        side.addWidget(self.import_button)
        # 分组之间 16px、组内 2px：只在"组"上写间距，行与行之间保持同一条节奏。
        side.addSpacing(theme_module.SPACE["lg"])
        side.addWidget(self.tasks_button)
        side.addWidget(self.manage_projects_button)
        side.addWidget(self.library_button)
        side.addSpacing(theme_module.SPACE["lg"])
        side.addWidget(self.project_nav_label)
        side.addSpacing(theme_module.SPACE["xxs"])
        side.addLayout(self.project_nav_box)
        side.addStretch(1)
        side.addWidget(sidebar_footer)

        # ---- 页头：大标题 + 一句说明，右侧是搜索 ------------------------------------
        title = QLabel(i18n.tr('任务库'))
        title.setObjectName("displayTitle")
        self.subtitle = style_module.ElideLabel(
            i18n.tr('导入 PDF、DOCX、EPUB、TXT 或 Markdown，翻译、审校，然后交付。'))
        self.subtitle.setObjectName("subtitle")
        title_box = QVBoxLayout()
        title_box.setSpacing(theme_module.SPACE["xxs"])
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        # 一行统计（数字加粗、其余是次要色）：有任务时替代上面那句导入说明。
        self.stats_line = QLabel("")
        self.stats_line.setObjectName("statsLine")
        self.stats_line.setTextFormat(Qt.TextFormat.RichText)
        self.stats_line.setVisible(False)
        title_box.addWidget(self.stats_line)

        self.refresh_icon_button = style_module.icon_button("refresh", i18n.tr('刷新任务'))
        self.refresh_icon_button.clicked.connect(self.refreshRequested.emit)

        # 表格 / 卡片：旧界面首页是"按项目分组的卡片"，表格适合几十个任务横着扫。
        # 两种视图共用同一份选中状态，切换不丢选择。
        self.view_switch = style_module.SegmentedControl(
            [("shelf", i18n.tr("书架")), ("cards", i18n.tr("卡片")), ("table", i18n.tr("表格"))])
        # 默认是书架（每个项目一层、每个任务一本书脊，见 shelf_view）；卡片看缩略图，
        # 表格留给"几十个任务横着扫"。三种视图共用同一份选中状态。
        self.view_switch.set_value("shelf")
        self._view_mode = "shelf"
        self.view_switch.changed.connect(self._set_view_mode)

        # 状态筛选：与下方指标卡同一组词（运行中 / 已交付 / 有错误）。
        self._status_key = "all"
        self.status_filter = components.FilterChips([
            ("all", i18n.tr('全部')), ("active", i18n.tr('运行中')),
            ("delivered", i18n.tr('已交付')), ("errored", i18n.tr('有错误'))])
        self.status_filter.changed.connect(self._status_filter_changed)

        self.project_filter = style_module.ComboBox()
        self.project_filter.addItem(i18n.tr('全部项目'), "")
        self.project_filter.setMinimumWidth(150)
        self.project_filter.setMaximumWidth(220)
        self.project_filter.setToolTip(i18n.tr('按项目筛选任务'))
        self.project_filter.currentIndexChanged.connect(self._apply_filter)
        self.project_filter.currentIndexChanged.connect(self._sync_project_nav)

        self.search_edit = components.SearchBar(
            i18n.tr('搜索任务：文档名、状态、语言…'), shortcut_scope=self)
        self.search_edit.setMinimumWidth(300)
        self.search_edit.setMaximumWidth(340)
        self.search_edit.textChanged.connect(self._apply_filter)

        self.sort_box = style_module.ComboBox()
        for label, key in HOME_SORTS:
            self.sort_box.addItem(i18n.tr(label), key)
        self.sort_box.setToolTip(i18n.tr('任务排序'))
        self.sort_box.currentIndexChanged.connect(self._apply_filter)

        header_actions = QHBoxLayout()
        header_actions.setSpacing(theme_module.SPACE["sm"])
        header_actions.addWidget(self.search_edit)
        header_actions.addWidget(self.refresh_icon_button)

        page_header = QHBoxLayout()
        page_header.setSpacing(theme_module.SPACE["lg"])
        page_header.addLayout(title_box, 1)
        page_header.addLayout(header_actions, 0)

        # ---- 首次运行提示（只有未配置模型时出现） --------------------------
        self.setup_banner = style_module.NoticeStrip(tone="warn")
        self.setup_banner.set_action(i18n.tr('配置模型连接…'), self.settingsRequested.emit)
        self.setup_banner.setVisible(False)

        # ---- 指标卡（接口与旧的内联指标条相同：set_value / value / cell） ---------
        # Golden Gate：四张指标卡并进了筛选芯片（"运行中 1"），不再单独占一行。
        # 对象保留（计数仍在这里维护，窗口/测试读它），但不显示。
        self.stats = components.MetricCards([
            ("total", i18n.tr('任务总数'), "projects", "accent"),
            ("active", i18n.tr('运行中'), "pulse", "info"),
            ("delivered", i18n.tr('已交付'), "check-circle", "ok"),
            ("errored", i18n.tr('有错误'), "alert", "danger"),
        ])
        self.stats.setParent(self)
        self.stats.setVisible(False)
        self._status_labels = {"all": i18n.tr('全部'), "active": i18n.tr('运行中'),
                               "delivered": i18n.tr('已交付'), "errored": i18n.tr('有错误')}

        # ---- 继续上次的工作（最近一个还没交付、也没出错的任务） -----------------
        self.continue_card = self._build_continue_card()
        self._continue_job_id = ""

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
        # 只有**文档名**伸展：旧版让"文档 + 状态"一起伸展，于是「待处理」这种四个字的
        # 状态拿到了 370px 的列宽，表格中间空出一大片、右边四列挤成一堆。状态/目标语言/
        # 段落/更新时间都按内容收窄，多出来的宽度全部留给文件名——它才是最长的那个。
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(self.COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        # 表头是列名，不是内容：左对齐（跟随单元格），不要居中。
        for column in range(len(self.COLUMNS)):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(ALIGN_LEFT)
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
        self.card_layout.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                                            theme_module.SPACE["md"], theme_module.SPACE["md"])
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
        empty_icon = icons.IconLabel("file-text", 40, "text-muted")
        empty_title = QLabel(i18n.tr('还没有任务'))
        empty_title.setObjectName("sectionTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label = QLabel(i18n.tr('点击「导入文档…」选择 PDF 或 DOCX，即可开始翻译。'))
        self.empty_label.setObjectName("hint")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_button = QPushButton(i18n.tr('导入文档…'))
        self.empty_button.setObjectName("primaryAction")
        self.empty_button.setIcon(icons.icon("upload", 16, "text-on-accent"))
        self.empty_button.setIconSize(QSize(16, 16))
        self.empty_button.clicked.connect(self.importRequested.emit)
        empty_buttons = QHBoxLayout()
        empty_buttons.addStretch(1)
        empty_buttons.addWidget(self.empty_button)
        empty_buttons.addStretch(1)
        empty_layout.addWidget(empty_icon, 0, Qt.AlignmentFlag.AlignHCenter)
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
        from .shelf_view import ShelfView

        self.shelf = ShelfView()
        self.shelf.clicked.connect(self._select_job_from_card)
        self.shelf.activated.connect(self._open_job_from_card)
        self.shelf_scroll = QScrollArea()
        self.shelf_scroll.setObjectName("shelfScroll")
        self.shelf_scroll.setWidgetResizable(True)
        self.shelf_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.shelf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.shelf_scroll.viewport().setAutoFillBackground(False)
        self.shelf_scroll.setWidget(self.shelf)
        self.stack.addWidget(self.shelf_scroll)

        # ---- 底部动作 + 状态 ------------------------------------------------
        self.open_button = QPushButton(i18n.tr('打开任务'))
        self.open_button.setObjectName("primaryAction")
        self.open_button.setIcon(icons.icon("arrow-right", 16, "text-on-accent"))
        self.open_button.setIconSize(QSize(16, 16))
        self.move_button = QPushButton(i18n.tr('移动到项目…'))
        self.move_button.setIcon(icons.icon("folder", 16, "text-secondary"))
        self.move_button.setIconSize(QSize(16, 16))
        self.delete_button = QPushButton(i18n.tr('删除任务'))
        self.delete_button.setObjectName("dangerAction")
        self.delete_button.setIcon(icons.icon("trash", 16, "danger"))
        self.delete_button.setIconSize(QSize(16, 16))
        self.open_button.clicked.connect(self._open_selected)
        self.move_button.clicked.connect(self._move_selected)
        self.delete_button.clicked.connect(self._delete_selected)
        self.status_label = style_module.ElideLabel("")
        self.status_label.setObjectName("hint")
        self.status_label.setMaximumWidth(340)

        # 列表脚注：贴着表格的底边（只隔一条 1px 发丝线），行高 36 而不是
        # "34px 控件 + 16px 空白"。右侧放**选中摘要**（"已选择 2 个任务"），
        # 不再是"共 107 个任务"——那是指标条已经在说的事。
        self.actions_frame = QFrame()
        self.actions_frame.setObjectName("listFooter")
        actions = QHBoxLayout(self.actions_frame)
        actions.setContentsMargins(0, theme_module.SPACE["sm"], 0, 0)
        actions.setSpacing(theme_module.SPACE["sm"])
        actions.addWidget(self.open_button)
        actions.addWidget(self.move_button)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        actions.addWidget(self.status_label)

        # 任务区块 = 一张容器卡：头部条（标题 + 状态筛选 + 视图/项目/排序） + 列表 + 脚注。
        self.list_card = components.SectionCard(i18n.tr('任务列表'))
        self.list_card.add_header_widget(self.status_filter)
        self.list_card.header_layout.addStretch(1)
        for control in (self.view_switch, self.project_filter, self.sort_box):
            self.list_card.add_header_widget(control)
        actions.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                                   theme_module.SPACE["md"], theme_module.SPACE["sm"])
        self.list_card.body_layout.addWidget(self.stack, 1)
        self.list_card.body_layout.addWidget(self.actions_frame)

        # ---- 主区组装 ------------------------------------------------------
        main = components.GradientCanvas()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["lg"])
        layout.addLayout(page_header)
        layout.addWidget(self.setup_banner)
        layout.addWidget(self.continue_card)
        layout.addWidget(self.list_card, 1)

        shell = QHBoxLayout(self)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(sidebar)
        shell.addWidget(main, 1)
        self._shell = shell

        # 卡片缩略图：后台读源文件生成第一页 / 封面，好了以后换到对应卡片上。
        from . import thumbnails

        self._previews = thumbnails.loader()
        self._previews.ready.connect(self._preview_ready)

    # -- 侧栏（窗口级导航） --------------------------------------------------
    def take_sidebar(self) -> QWidget:
        """把侧栏交给窗口，放在整个页面栈左侧：任务库、项目、术语页都看得到它。"""
        self._shell.removeWidget(self.sidebar)
        return self.sidebar

    def set_current_nav(self, name: str) -> None:
        """点亮当前页对应的侧栏项（``tasks`` / ``projects`` / ``library``）。"""
        for key, button in self._nav_buttons.items():
            button.setChecked(key == name)

    # -- 卡片缩略图 ---------------------------------------------------------
    def _request_preview(self, job_id: str, filename: str, paper) -> None:
        image = self._previews.request(job_id, adapter.source_path(job_id), filename)
        if image is not None:
            paper.set_image(image)

    def _preview_ready(self, job_id: str) -> None:
        row = next((item for item in self._rows if item.job_id == job_id), None)
        if row is None:
            return
        card = self._cards.get(job_id)
        if card is not None:
            self._request_preview(job_id, row.filename, card.paper)
        self._shelf_colour(row)
        if getattr(self, "_continue_job_id", "") == job_id:
            self._request_preview(job_id, row.filename, self.continue_paper)

    # -- 继续上次的工作 ----------------------------------------------------
    def _build_continue_card(self) -> Card:
        card = Card()
        card.setObjectName("continueCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                               theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        row.setSpacing(theme_module.SPACE["xl"])
        self.continue_paper = components.DocThumb(80, 104)
        row.addWidget(self.continue_paper, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(theme_module.SPACE["xs"])
        eyebrow = QLabel(i18n.tr('继续上次的工作'))
        eyebrow.setObjectName("eyebrow")
        self.continue_title = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideMiddle)
        self.continue_title.setObjectName("continueTitle")
        self.continue_meta = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
        self.continue_meta.setObjectName("subtitle")
        self.continue_status = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
        self.continue_status.setObjectName("continueStatus")
        self.continue_progress = components.ProgressLine(0)
        self.continue_progress.setMaximumWidth(320)
        self.continue_button = QPushButton(i18n.tr('继续'))
        self.continue_button.setObjectName("primaryAction")
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.clicked.connect(
            lambda: self._continue_job_id and self.openRequested.emit(self._continue_job_id))
        style_module.fit_button(self.continue_button, max_width=200)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme_module.SPACE["sm"])
        buttons.addWidget(self.continue_button)
        buttons.addStretch(1)
        text.addWidget(eyebrow)
        text.addWidget(self.continue_title)
        text.addWidget(self.continue_meta)
        text.addSpacing(theme_module.SPACE["xxs"])
        text.addWidget(self.continue_status)
        text.addWidget(self.continue_progress)
        text.addSpacing(theme_module.SPACE["sm"])
        text.addLayout(buttons)
        row.addLayout(text, 1)
        card.setVisible(False)
        return card

    def _continue_candidate(self) -> adapter.JobRow | None:
        """最近更新的、还没交付也没出错的任务（运行中的优先）。"""
        candidates = [row for row in self._rows
                      if not getattr(row, "snapshot_version", None)
                      and not str(getattr(row, "error", "") or "").strip()]
        if not candidates:
            return None
        return sort_job_rows(candidates, "updated")[0]

    def _refresh_continue(self) -> None:
        row = self._continue_candidate()
        # 搜索/筛选时它会和结果打架：只在"看全部"时出现。
        show = (row is not None and not self.search_edit.text().strip()
                and self._status_key == "all")
        self.continue_card.setVisible(show)
        if not show:
            self._continue_job_id = ""
            return
        if self._continue_job_id != row.job_id:
            self.continue_paper.set_image(None)
        self._continue_job_id = row.job_id
        self._request_preview(row.job_id, row.filename, self.continue_paper)
        self.continue_title.setText(row.title)
        self.continue_title.setToolTip(row.title)
        meta = [part for part in (
            _row_format(row),
            i18n.tr("{v0} 段", v0=row.segment_count) if row.segment_count else "",
            i18n.tr_dynamic(row.target_lang) if row.target_lang else "",
            components.relative_time(row.updated_at),
        ) if part]
        self.continue_meta.setText(" · ".join(meta))
        status = i18n.tr_dynamic(row.status_label) or ""
        progress = i18n.tr_dynamic(row.progress_label) if row.progress_label else ""
        parts = [part for part in (status, progress if progress != status else "") if part]
        self.continue_status.setText(" · ".join(parts))
        percent = _row_percent(row)
        self.continue_progress.setVisible(percent is not None)
        if percent is not None:
            self.continue_progress.set_value(percent)
            self.continue_progress.set_token(components.PROGRESS_TONES[library_run_view(row)[0]])
        self.continue_button.setText(i18n.tr('继续'))

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
        self._rebuild_project_nav(projects)

    #: 侧栏「最近项目」最多列几个：它是快捷入口，不是项目管理（那是「项目…」）。
    PROJECT_NAV_LIMIT = 6

    def _rebuild_project_nav(self, projects: list[dict]) -> None:
        for button in self._project_nav_buttons.values():
            self.project_nav_box.removeWidget(button)
            button.hide()  # before detaching (see segment_assist._clear)
            button.setParent(None)
            button.deleteLater()
        self._project_nav_buttons = {}
        shown = [project for project in projects
                 if project.get("project_id") and not project.get("is_system")]
        for project in shown[:self.PROJECT_NAV_LIMIT]:
            project_id = str(project.get("project_id"))
            name = str(project.get("name") or i18n.tr("未命名项目"))
            button = QPushButton(name)
            button.setObjectName("navProject")
            button.setCheckable(True)
            button.setIcon(icons.icon("dot", 14, "text-muted", color_on="accent"))
            button.setIconSize(QSize(14, 14))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(name)
            style_module.fit_button(button, max_width=168)
            button.clicked.connect(
                lambda _checked=False, pid=project_id: self._pick_project_from_nav(pid))
            self.project_nav_box.addWidget(button)
            self._project_nav_buttons[project_id] = button
        self.project_nav_label.setVisible(bool(self._project_nav_buttons))
        self._sync_project_nav()

    def _pick_project_from_nav(self, project_id: str) -> None:
        # 再点一次已选中的项目 = 取消筛选，回到"全部项目"。
        current = self.project_filter.currentData() or ""
        target = "" if current == project_id else project_id
        index = self.project_filter.findData(target)
        self.project_filter.setCurrentIndex(max(index, 0))
        # 侧栏在项目页 / 术语页也可见：点项目 = 回到任务库看这个项目的任务。
        self.tasksRequested.emit()

    def _sync_project_nav(self, *_args) -> None:
        current = self.project_filter.currentData() or ""
        for project_id, button in self._project_nav_buttons.items():
            button.setChecked(project_id == current)

    def _status_filter_changed(self, key: str) -> None:
        self._status_key = key or "all"
        self._apply_filter()

    def _matches_status(self, row: adapter.JobRow) -> bool:
        key = self._status_key
        if key == "active":
            return bool(row.active)
        if key == "delivered":
            return bool(getattr(row, "snapshot_version", None))
        if key == "errored":
            return bool(str(getattr(row, "error", "") or "").strip())
        return True

    def set_status(self, text: str) -> None:
        """列表脚注：有消息时显示消息，否则显示选中摘要（"已选择 2 个任务"）。

        窗口用它报"读取任务列表失败"这类**真的要说**的情况；正常刷新时传空串，
        脚注不会退化成"共 107 个任务"——那是指标条已经在说的事。
        """
        self._status_override = str(text or "")
        self._sync_status_label()

    def _sync_status_label(self) -> None:
        if self._status_override:
            self.status_label.setText(self._status_override)
            return
        count = len(self.selected_job_ids())
        self.status_label.setText(
            i18n.tr("已选择 {v0} 个任务", v0=count) if count else "")

    def set_engine_problem(self, problem: str, model_label: str = "") -> None:
        """模型连接状态。

        三处状态的分工：顶部提示条只说**阻塞**（模型缺失，附配置入口），
        侧栏底部块说**当前引擎是什么**（常驻属性），底栏只说"刚刚发生了什么"。

        ⚠️ "配置模型连接…"只出现在顶部提示条里（它就在主工作区正上方，说的是同一
        件事）。侧栏底部是"状态 + 设置…"：设置是整个应用的入口（模型、默认值、外观），
        不是"更改模型"的别名，所以不再另挂一个同义按钮。
        """
        has_model = bool(model_label)
        if problem:
            self.setup_banner.set_text(
                i18n.tr("尚未配置可用的模型连接：{v0}。", v0=problem)
                + i18n.tr("配置并测试连接后即可开始翻译。"))
            self.setup_banner.setVisible(True)
            self.engine_label.setText(i18n.tr("未配置"))
            self.engine_dot.setProperty("state", "warn")
        else:
            self.setup_banner.setVisible(False)
            self.engine_label.setText(i18n.tr("已配置") if has_model
                                      else i18n.tr("未配置"))
            self.engine_dot.setProperty("state", "ok" if has_model else "muted")
        style_module.repolish(self.engine_dot)
        self.engine_model.setText(model_label or "")
        self.engine_model.setVisible(bool(model_label))

    def _visible_rows(self) -> list[adapter.JobRow]:
        rows = [row for row in self._filtered_rows() if self._matches_status(row)]
        return sort_job_rows(rows, self.sort_box.currentData() or "updated")

    def _filtered_rows(self) -> list[adapter.JobRow]:
        project = self.project_filter.currentData() or ""
        rows = self._rows if not project else [
            row for row in self._rows if (row.project_id or "") == project]
        query = self.search_edit.text().strip().casefold()
        if not query:
            return list(rows)
        # 任务多了以后靠滚动找不到：按文档名、状态、语言、任务号模糊匹配（所有词都要命中）。
        words = query.split()
        matched = []
        for row in rows:
            haystack = " ".join((
                row.title, row.filename or "", row.status_label or "",
                row.progress_label or "", row.target_lang or "", row.job_id,
                self.project_filter.itemText(
                    max(self.project_filter.findData(row.project_id or ""), 0)),
            )).casefold()
            if all(word in haystack for word in words):
                matched.append(row)
        return matched

    def _refresh_logo(self) -> None:
        from . import branding

        theme = current_theme()
        if theme == self._logo_theme:
            return
        # 字标自带品牌名与标语，所以侧栏不再重复写文字。
        # 高度 LOGO_HEIGHT（逻辑像素）：字标宽高比 3.62，52px 高 ≈ 188px 宽，正好铺满
        # 232px 侧栏的内容区（208px）；按设备像素比出图，Retina 屏上不发糊。
        ratio = max(1.0, float(self.devicePixelRatioF()))
        logo = branding.logo_pixmap(round(self.LOGO_HEIGHT * ratio), theme,
                                    tint=theme_module.tokens(theme)["accent"])
        if not logo.isNull():
            logo.setDevicePixelRatio(ratio)
            self._logo_label.setPixmap(logo)
        self._logo_theme = theme

    def _sync_stats_line(self, scope, active, delivered) -> None:
        """「12 个任务 · 3 个进行中 · 5 个已交付 · 2 个需要处理」：数字加粗，一眼读完。"""
        if not scope:
            self.stats_line.setVisible(False)
            self.subtitle.setVisible(True)
            return
        attention = [row for row in scope if int(getattr(row, "attention_count", 0) or 0)]

        def part(template: str, value: int) -> str:
            number = f"<b style='color:{style_module.text_color('text-primary')}'>{value:,}</b>"
            return i18n.tr(template, v0="\x00").replace("\x00", number)

        parts = [part('{v0} 个任务', len(scope))]
        if active:
            parts.append(part('{v0} 个进行中', len(active)))
        if delivered:
            parts.append(part('{v0} 个已交付', len(delivered)))
        if attention:
            parts.append(part('{v0} 个需要处理', len(attention)))
        self.stats_line.setText(" · ".join(parts))
        self.stats_line.setVisible(True)
        self.subtitle.setVisible(False)

    def _apply_filter(self) -> None:
        rows = self._visible_rows()
        self._refresh_logo()
        tokens = theme_module.tokens(current_theme())
        # 指标按"项目 + 搜索"的范围统计，**不受状态筛选影响**：点「有错误」芯片时，
        # 「任务总数」还应该是这个范围里的总数，而不是被筛成 3。
        scope = self._filtered_rows()
        active = [row for row in scope if row.active]
        # 指标的值只用行里**确实存在**的字段（不猜状态字符串）
        delivered = [row for row in scope if getattr(row, "snapshot_version", None)]
        errored = [row for row in scope if str(getattr(row, "error", "") or "").strip()]
        self._sync_stats_line(scope, active, delivered)
        # ---- 指标卡 --------------------------------------------------------
        rows_count = len(rows)
        self.stats.set_value("total", len(scope))
        self.stats.set_value("active", len(active),
                             emphasis="accent" if active else "muted")
        self.stats.set_value("delivered", len(delivered),
                             emphasis="default" if delivered else "muted")
        # "有错误"是这四个数字里唯一需要被看见的那个：0 的时候退成灰，非 0 变红。
        self.stats.set_value("errored", len(errored),
                             emphasis="danger" if errored else "muted")
        for key, count in (("all", len(scope)), ("active", len(active)),
                           ("delivered", len(delivered)), ("errored", len(errored))):
            button = self.status_filter.button(key)
            if button is not None:
                label = self._status_labels[key]
                button.setText(f"{label}  {count}" if count else label)
        self._refresh_continue()
        self.table.setRowCount(rows_count)
        for row_index, row in enumerate(rows):
            tone = _status_tone(row)
            values = (
                row.title,
                ("● " if row.active else "") + i18n.tr_dynamic(row.status_label),
                i18n.tr_dynamic(row.target_lang) if row.target_lang else "—",
                str(row.segment_count) if row.segment_count else "—",
                (row.updated_at or "").replace("T", " ")[:16] or "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.job_id)
                    # 文档名是这一行的主信息：加粗一点，扫描时先看到它
                    font = item.font()
                    font.setWeight(font.Weight.DemiBold)
                    item.setFont(font)
                # 文字列左对齐（与表头同侧），计数与时间右对齐：
                # "223" 与 "8" 右对齐后才好比较大小，时间戳右对齐也成一条竖线。
                item.setTextAlignment(
                    ALIGN_RIGHT if column in (3, 4) else ALIGN_LEFT)
                if column == 4:
                    item.setForeground(style_module.qcolor(tokens["text-muted"]))
                if column == 3:
                    item.setForeground(style_module.qcolor(tokens["text-secondary"]))
                if column == 1:
                    item.setForeground(style_module.qcolor(_tone_color(tokens, tone)))
                self.table.setItem(row_index, column, item)
        self._fill_cards(rows)
        self._fill_shelf(rows)
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
        elif self._view_mode == "shelf":
            self.stack.setCurrentWidget(self.shelf_scroll)
        elif self._view_mode == "cards":
            self.stack.setCurrentWidget(self.card_scroll)
        else:
            self.stack.setCurrentWidget(self.table)

    def _set_view_mode(self, value: str) -> None:
        self._view_mode = value if value in ("table", "cards", "shelf") else "table"
        self._reflow_stack()
        # 切到卡片视图时按**当前**宽度重算列数。首次填充（set_rows）时窗口往往还没
        # 被布局到最终尺寸，`_card_columns()` 会算出 1 列；此后没人再问一次，于是
        # 1180 宽的窗口里卡片以整幅宽度一张一行地堆着——正是要消掉的"松散"观感。
        if self._view_mode == "cards" and self._cards:
            if self._card_columns() != self._card_columns_applied:
                self._fill_cards(self._visible_rows())
        self._sync_card_selection()

    def set_view_mode(self, value: str) -> None:
        """给窗口/测试用：切换视图时同步分段控件的选中态。"""
        self._set_view_mode(value)
        self.view_switch.set_value(self._view_mode)

    def _card_columns(self) -> int:
        """能并排放下几张 ≥ ``_TaskCard.MIN_WIDTH`` 的卡（Golden Gate 的缩略图网格）。

        先看视图自己的宽度（滚动区在首次填充时往往还没被布局到最终尺寸），再减去侧栏、
        主区左右留白与容器卡的内边距。
        """
        width = self.width() or self.card_scroll.viewport().width()
        available = width - self.SIDEBAR_WIDTH - 2 * theme_module.SPACE["xl"] \
            - 2 * theme_module.SPACE["md"]
        gap = theme_module.SPACE["lg"]
        return max(1, min(6, (available + gap) // (_TaskCard.MIN_WIDTH + gap)))

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
        # 卡片可以拿键盘焦点：重建前先记住焦点在哪张卡上，并把焦点移出卡片页。
        # 否则下面 setParent(None) 会把"持有焦点的控件"整个摘出窗口，整个窗口随之失焦
        # （键盘用户的焦点凭空消失，Tab 也没有起点）。
        focused_job = next((job_id for job_id, card in self._cards.items()
                            if card.hasFocus()), "")
        if focused_job or (self.focusWidget() is not None
                           and self.card_page.isAncestorOf(self.focusWidget())):
            self.card_scroll.setFocus(Qt.FocusReason.OtherFocusReason)
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 先摘出布局树再排队销毁：只 deleteLater() 的话旧卡片会一直挂到
                # 事件循环回收那一刻，期间和新卡片叠在一起（看起来像重影）。
                widget.hide()  # before detaching (see segment_assist._clear)
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
            grid.setHorizontalSpacing(theme_module.SPACE["lg"])
            grid.setVerticalSpacing(theme_module.SPACE["lg"])
            for position, row in enumerate(items):
                card = _TaskCard(row, tone=_status_tone(row), parent=section)
                card.clicked.connect(self._select_job_from_card)
                card.activated.connect(self._open_job_from_card)
                card.attentionRequested.connect(self.checkRequested.emit)
                card.navigate.connect(self._navigate_cards)
                grid.addWidget(card, position // columns, position % columns)
                self._cards[row.job_id] = card
                self._request_preview(row.job_id, row.filename, card.paper)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            section_layout.addLayout(grid)
            self.card_layout.addWidget(section)
        self.card_layout.addStretch(1)
        if focused_job in self._cards:
            self._cards[focused_job].setFocus(Qt.FocusReason.OtherFocusReason)

    def _select_job_from_card(self, job_id: str) -> None:
        for row_index in range(self.table.rowCount()):
            item = self.table.item(row_index, 0)
            if item is not None and str(item.data(
                    Qt.ItemDataRole.UserRole) or "") == job_id:
                self.table.clearSelection()
                self.table.selectRow(row_index)
                return

    def _navigate_cards(self, job_id: str, step: int) -> None:
        """方向键：左右 = 前/后一张，上下 = 上/下一行（±100 表示"一行"）。"""
        order = list(self._cards)
        if job_id not in order:
            return
        columns = max(1, self._card_columns_applied or 1)
        delta = step if abs(step) < 100 else (columns if step > 0 else -columns)
        target = order.index(job_id) + delta
        if 0 <= target < len(order):
            card = self._cards[order[target]]
            card.setFocus(Qt.FocusReason.OtherFocusReason)
            self.card_scroll.ensureWidgetVisible(card)

    def _open_job_from_card(self, job_id: str) -> None:
        self._select_job_from_card(job_id)
        self.openRequested.emit(job_id)

    def _sync_card_selection(self) -> None:
        selected = set(self.selected_job_ids())
        for job_id, card in self._cards.items():
            card.set_selected(job_id in selected)
        if hasattr(self, "shelf"):
            self.shelf.set_selected(selected)

    def _fill_shelf(self, rows: list[adapter.JobRow]) -> None:
        """书架：按项目分组，每组一层；书脊颜色取自缩略图（已经生成的先用上）。"""
        groups: dict[str, list[adapter.JobRow]] = {}
        for row in rows:
            groups.setdefault(row.project_id or "", []).append(row)
        self.shelf.fill([(self._project_label(project_id), items)
                         for project_id, items in groups.items()])
        for row in rows:
            self._shelf_colour(row)
        self.shelf.set_selected(set(self.selected_job_ids()))

    def _shelf_colour(self, row) -> None:
        from . import shelf_view

        image = self._previews.request(row.job_id, adapter.source_path(row.job_id), row.filename)
        colour = shelf_view.cover_colour(image, style_module.night_mode()) if image is not None \
            else None
        if colour is not None:
            self.shelf.set_colour(row.job_id, colour)

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
        if watched is getattr(self, "table", None) and event.type() == QEvent.Type.FocusIn:
            self._select_current_row_for_keyboard()
        if watched is getattr(self, "engine_card", None) and (
                event.type() == QEvent.Type.MouseButtonRelease
                or (event.type() == QEvent.Type.KeyPress
                    and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space))):
            self.connectionRequested.emit()
            return True
        return super().eventFilter(watched, event)

    def set_connection_overview(self, overview: dict) -> None:
        """芯片显示最严重的那一条（● 连接正常 / ● 视觉模型未测试 / ● 翻译模型连接失败）。

        配置本身不完整时由 :meth:`set_engine_problem` 说「未配置」，这里不覆盖。
        """
        if self.setup_banner.isVisible():
            return
        from . import connection_status

        self.engine_label.setText(connection_status.describe(overview))
        self.engine_dot.setProperty("state", {"ok": "ok", "danger": "error",
                                              "warning": "warn"}.get(overview.get("tone"), "muted"))
        style_module.repolish(self.engine_dot)
        self.engine_card.setToolTip(i18n.tr('点击查看各模型连接的状态并测试'))

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
        self._sync_status_label()

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


#: Core stage id → short label for the run status line.
_RUN_STAGE_LABELS = {
    "document_processing": "读取原文", "segment_build": "切分段落",
    "terminology": "术语", "understanding": "全文理解", "translation": "翻译",
    "review": "审校", "annotation": "标注", "delivery": "交付", "report": "报告",
}
_STALL_AFTER_S = 120


def _parse_iso(value) -> float | None:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def run_status_view(runtime: dict, *, note: str, running: bool, completed: int,
                    total: int, unit: str, now: float | None = None) -> tuple[str, str]:
    """(tone, line) for the run status group: colour and the words that say the same.

    Tones: running / waiting (model call in flight) / retrying / stalled / failed /
    paused / done / idle.  "Stalled" uses the core's own ``stalled`` status, or no
    progress for ``_STALL_AFTER_S`` seconds while running.
    """
    now = time.time() if now is None else now
    status = str(runtime.get("status") or "")
    # The view's ``runtime`` is the raw runtime record (timestamps, stage, operation).
    raw = runtime.get("runtime") if isinstance(runtime.get("runtime"), dict) else runtime
    operation = str(raw.get("operation_id") or raw.get("operation")
                    or runtime.get("operation_id") or "")
    stage_id = str(raw.get("stage") or "")
    stage = i18n.tr(_RUN_STAGE_LABELS[stage_id]) if stage_id in _RUN_STAGE_LABELS else ""
    count = f"{completed:,} / {total:,} {i18n.tr_dynamic(unit)}".strip() if total else ""
    head = " · ".join(part for part in (stage, count) if part)
    last_progress = _parse_iso(raw.get("last_progress_at"))
    waiting_since = _parse_iso(raw.get("model_wait_started_at"))
    if status == "completed":
        started = _parse_iso(raw.get("started_at"))
        finished = _parse_iso(raw.get("finished_at")) or now
        minutes = int(round((finished - started) / 60)) if started else 0
        return "done", (i18n.tr('已完成 · {v0} 分钟', v0=max(1, minutes)) if started
                        else i18n.tr('已完成'))
    if status == "failed":
        return "failed", " · ".join(p for p in (head, note or i18n.tr('步骤失败')) if p)
    if status == "stalled":
        return "stalled", " · ".join(p for p in (head, note or i18n.tr('暂无运行信号')) if p)
    if status in ("paused", "interrupted", "cancelled", "idle_incomplete"):
        return "paused", " · ".join(p for p in (head, note) if p) or i18n.tr('已暂停')
    if not running:
        return "idle", " · ".join(p for p in (head, note) if p)
    if "retry" in operation:
        return "retrying", " · ".join(p for p in (head, note) if p)
    if last_progress and now - last_progress > _STALL_AFTER_S:
        minutes = int((now - last_progress) // 60)
        return "stalled", " · ".join(p for p in (head, i18n.tr(
            '已有 {v0} 分钟没有进展，可以暂停后重试', v0=max(2, minutes))) if p)
    if waiting_since and (not last_progress or waiting_since >= last_progress):
        seconds = int(max(0, now - waiting_since))
        wait = i18n.tr('正在等待模型响应 · {v0} 秒', v0=seconds)
        return "waiting", " · ".join(p for p in (head, wait) if p)
    return "running", " · ".join(p for p in (head, note) if p)


class ClickableLabel(QLabel):
    """一行可点的文字（悬停有下划线与手型光标；回车 / 空格也能触发）。"""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class RunStatusGroup(QFrame):
    """顶栏的运行状态组：进度条 + 百分比，下面一行写「阶段 · 进度 · 正在做什么」。

    整组是一个可点区域（鼠标、回车 / 空格），点开运行记录浮窗。颜色只是辅助：
    同一个状态在下面那行文字里总会写出来（见 acceptance-feedback 第 11 条）。
    """

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runStatus")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(i18n.tr('查看运行记录'))
        self.setAccessibleName(i18n.tr('运行状态'))
        self.track = components.ProgressTrack()
        self.percent = QLabel("")
        self.percent.setObjectName("runStatusPercent")
        self.line = style_module.ElideLabel("", mode=Qt.TextElideMode.ElideRight)
        self.line.setObjectName("runStatusLine")
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme_module.SPACE["xs"] + 2)
        top.addWidget(self.track, 1, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.percent, 0, Qt.AlignmentFlag.AlignVCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["xs"] + 2, 4, theme_module.SPACE["xs"] + 2, 4)
        layout.setSpacing(3)
        layout.addLayout(top)
        layout.addWidget(self.line)
        self.setFixedWidth(232)

    def set_state(self, *, tone: str, line: str, line_tone: str | None = None) -> None:
        """``line_tone``：只给文字着色（例如一次操作失败），进度条仍表示任务本身的状态。"""
        self.track.set_tone(tone)
        share = self.track.fraction()
        self.percent.setText("" if share is None or tone == "done"
                             else f"{int(round(share * 100))}%")
        self.line.setText(line)
        self.line.setProperty("tone", line_tone or tone)
        style_module.repolish(self.line)
        self.setAccessibleDescription(line)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


class CheckStatusButton(QToolButton):
    """「检查与修复」：进度条旁的圆形图标。平时灰；有待处理项变黄，有失败批次变红。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("roundButton")
        self.setCheckable(True)
        self.setIcon(icons.icon("shield-check", 16, "text-secondary"))
        self.setIconSize(QSize(16, 16))
        self.badge = QLabel("", self)
        self.badge.setObjectName("countBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setVisible(False)
        self._tone = ""
        self._motion = motion.Motion(self)
        self.set_count(0, "")

    def set_count(self, count: int, tone: str) -> None:
        tone = tone if count else ""
        if tone != self._tone:
            self._tone = tone
            self.setProperty("check", tone)
            token = {"warning": "warning", "danger": "danger"}.get(tone, "text-secondary")
            self.setIcon(icons.icon("shield-check", 16, token))
            style_module.repolish(self)
            if tone:
                self._motion.nod("check-tone", self)
        self.badge.setText(str(count) if count else "")
        self.badge.setVisible(bool(count))
        self.badge.adjustSize()
        self.badge.move(0 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
                        else self.width() - self.badge.width(), 0)
        label = i18n.tr('检查与修复')
        self.setToolTip(i18n.tr('检查与修复 · {v0} 项待处理', v0=count) if count else label)
        self.setAccessibleName(label)
        self.setAccessibleDescription(self.toolTip())

    def tone(self) -> str:
        return self._tone

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self.badge.move(0 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft
                        else self.width() - self.badge.width(), 0)


class WorkbenchView(QWidget):
    """多段工作台：就地编辑的双语网格 + 右侧操作栏 + 交付标签页。

    设计要点（对应验收项）：

    * **译文在网格里改**（docs/agent-inspector-workspace.md："一个内容只能有一个
      可写位置"）：当前行展开，译文格就是编辑框；右侧操作栏作用于选中的行，
      不重复原文与译文；
    * **网格是虚拟的**：只存在一个活动编辑器（浮在当前行上，见
      :mod:`folith.qt.segment_editor`），所以几千段的文档也不会创建大量重型
      编辑控件（§6.B）；
    * **后台刷新不重建正在输入的编辑器**：同一段落再次被选中时不触碰编辑器，
      列表重建按稳定段身份恢复选中（§6.B）；
    * **过滤复用核心语义**：搜索/状态筛选调用 `core.translation_visible_indexes`，
      界面不另写一套筛选规则（不制造第二套真值）；
    * **导航是阶段步骤条**：准备 → 翻译 → 审校 → 交付为主线，术语与"更多"
      （原页、检查与修复、运行记录，学术模式下还有报告/案例/合规）为旁路；
      `tabs` 只是页面容器，它的标签栏不显示（见 `show_page` / `stage.stage_view`）。
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
    revealLogRequested = Signal(str)           # job_id
    followupRetryRequested = Signal(str)       # "understanding" | "annotation"
    followupResumeRequested = Signal()
    entityPreviewRequested = Signal(str, str)
    entityRepairRequested = Signal(str, str, list)
    entityAcceptRequested = Signal(str)
    rescanRequested = Signal(dict)
    reportRepairRequested = Signal(str, list)
    reportAnswerRequested = Signal(str, str)
    reportRebuildRequested = Signal()
    reportTemplateAddRequested = Signal()
    reportTemplateRemoveRequested = Signal()
    caseApproveRequested = Signal(str)
    caseExcludeRequested = Signal(str)
    caseReplaceRequested = Signal(str)
    caseBaselineSaveRequested = Signal(str, str)
    caseBaselineRejectRequested = Signal(str, str)
    qaRenderRequested = Signal()
    qaFactToggled = Signal(str, bool)
    restoreRequested = Signal(int)             # 恢复人工修改之前的译文
    structureRequested = Signal(str, int)     # 操作名（见 adapter.STRUCTURE_OPERATIONS / "undo"）, 段索引
    deliverRequested = Signal()
    deliverWithRiskRequested = Signal()        # 接受剩余风险后交付
    supplementRequested = Signal(str)          # 补生成：交付开关键（如 deliver_epub）
    exportRequested = Signal()
    saveSnapshotRequested = Signal(int)
    refreshRequested = Signal()
    termAddRequested = Signal(str, str)     # 从选中文字加入术语库：原文, 译文
    askAgentRequested = Signal(int, str)    # 询问 Agent：段索引, 问题
    suggestRequested = Signal(int, str)     # 按要求重译：段索引, 要求
    analyzeRequested = Signal(int)          # 进一步审视本段
    saveAllDraftsRequested = Signal()
    discardAllDraftsRequested = Signal()
    profileEditRequested = Signal()         # 修改文档画像
    promoteMemoryRequested = Signal()       # 把本任务确认过的知识提升到项目记忆
    styleRuleRequested = Signal(dict, str)  # 审校：保存为项目风格规则
    reportExportRequested = Signal(str)     # 报告导出："docx" | "md"
    findReplaceRequested = Signal()
    excludeFragmentsRequested = Signal(list)
    connectionSettingsRequested = Signal()
    draftSaved = Signal(str, str, str)      # job_id, segment_uid, text
    editSubmitted = Signal(int)
    revealRequested = Signal(str)
    openAssetRequested = Signal(str)
    setupStartRequested = Signal(dict)      # 译前准备的「开始翻译」载荷
    setupCreateProjectRequested = Signal()  # 译前准备里点「新建项目…」
    setupTermbaseAddRequested = Signal()
    setupStyleProfileRequested = Signal()
    setupTermbaseRemoveRequested = Signal()
    setupLiteratureAddRequested = Signal()
    setupLiteratureRemoveRequested = Signal()
    # 审校页：人工处置（由窗口层调用适配器写状态）
    findingResolveRequested = Signal(list, str, str)    # finding_ids, action, note
    suggestionApplyRequested = Signal(dict, str)        # queue item, note
    sourceConfirmRequested = Signal(int, str)           # segment index, note
    # 术语页（暂存修改 / 冻结 / 逐条修订）
    glossaryDraftRequested = Signal(list)
    glossaryFreezeRequested = Signal(list)
    glossaryUnfreezeRequested = Signal()
    glossaryBypassRequested = Signal()
    glossaryEntryUpdateRequested = Signal(str, dict)
    glossaryEntryAddRequested = Signal(dict)
    glossaryStatusRequested = Signal(list, str)   # entry ids, status
    glossaryEntryDeleteRequested = Signal(str)

    COLUMNS = ("段", "原文", "译文")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = ""
        self.config: adapter.EngineConfig | None = None
        self._all_rows: list[adapter.SegmentRow] = []
        #: Submitted edits whose background save has not landed yet (index → text).
        self._pending_saved: dict[int, str] = {}
        self._rows: list[adapter.SegmentRow] = []
        # 每个网格位置上一次画出去的行数据 + 画它时的（主题, 语言）。后台每秒刷新时
        # 绝大多数行没有变化：只重画变了的行，否则 4800 段的任务每秒要白画 5 万次。
        self._painted: dict[int, adapter.SegmentRow] = {}
        self._painted_look: tuple = ()
        self._state: dict = {}
        # 译前准备视图（adapter.task_setup_view）。``None`` 表示"没有准备面"，
        # 此时一律按"已开始"处理，旧的调用路径（测试、恢复）不受影响。
        self._setup_view: dict | None = None
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
        self._review_key: tuple = ()
        self._review_items: list[dict] = []
        self._assist_index = -1
        self._terms_key: tuple = ()
        self._followup_key: tuple = ()
        self._qa_key: tuple = ()
        self._cases_key: tuple = ()
        self._report_key: tuple = ()

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(DRAFT_AUTOSAVE_MS)
        self._autosave.timeout.connect(self.flush_draft)

        # ---- 头部：两行 ------------------------------------------------------
        #   第一行 = 身份与运行：返回 / 文件名 / 状态 / 运行说明 / 进度 / 动作
        #   第二行 = 阶段步骤条（准备 → 翻译 → 审校 → 交付）
        # 原来第二行是三个事实芯片（进度 / 审校 / 必须处理），它们各自用不同口径回答
        # "现在怎么样"，互相对不上；这些事实现在是步骤条上每一步的一句状态，
        # 全部来自 `stage.stage_view` 这一个出处。
        # Golden Gate 统一工具栏：返回是一枚圆形按钮（文案作为提示与无障碍名称）。
        self.back_button = QToolButton()
        self.back_button.setObjectName("roundButton")
        self.back_button.setText(i18n.tr('任务列表'))
        self.back_button.setToolTip(i18n.tr('任务列表'))
        self.back_button.setAccessibleName(i18n.tr('任务列表'))
        self.back_button.setIcon(icons.icon("chevron-left", 16, "text-primary"))
        self.back_button.setIconSize(QSize(16, 16))
        self.back_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.back_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_button.clicked.connect(self.backRequested.emit)
        # 文件名与运行时说明都用**可省略**标签：长文件名不能再把进度条和运行控制
        # 挤出可视区（旧版是普通 QLabel，sizeHint 就是整串文字的宽度）。
        self.title_label = style_module.ElideLabel("—")
        self.title_label.setObjectName("workbenchTitle")
        # 只给下限、**不给上限**：ElideLabel 是 Ignored 宽度策略，若再加上限，
        # 布局把剩下的余量分不到它头上，就会回流给返回按钮和状态芯片（实测两者
        # 被撑到 281px，像两个巨大的空按钮）。
        self.title_label.setMinimumWidth(120)
        self.chip_state = Chip("—", "muted")

        def _icon_button(button: QPushButton, name: str, token: str = "text-secondary"):
            button.setIcon(icons.icon(name, 16, token))
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

        # 审校动作：`review_selected_button` 跟当前选区绑定，留在筛选条；
        # "审校全部段落"不再是头部常驻按钮，而是审校阶段由步骤条推荐的下一步。
        self.review_selected_button = _icon_button(QPushButton(i18n.tr('审校所选段落')), "shield-check")
        self.review_selected_button.setEnabled(False)
        self.review_selected_button.clicked.connect(
            lambda: self.reviewSelectedRequested.emit(self.selected_indexes()))

        # 推荐的下一步：随阶段变化（重试失败的审校 / 处理问题 / 冻结并交付）。
        self.stage_action_button = _icon_button(QPushButton(""), "arrow-right", "text-on-accent")
        self.stage_action_button.setObjectName("primaryAction")
        self.stage_action_button.clicked.connect(self._run_stage_action)
        self.stage_action_button.setVisible(False)
        self._stage_action: dict | None = None

        # 运行状态组（进度 + 百分比 + 状态说明，点开运行记录）与「检查与修复」图标。
        # `progress` 仍指向那根进度条（QProgressBar 同名接口），`status_label`
        # 是状态组里那行说明：原先在左上角文件名下面，与进度隔着整个顶栏。
        self.run_status = RunStatusGroup()
        self.run_status.clicked.connect(self.open_activity)
        self.progress = self.run_status.track
        self.status_label = self.run_status.line
        self.check_button = CheckStatusButton()
        self.check_button.clicked.connect(self._toggle_followup_drawer)
        self.details_button = QToolButton()
        self.details_button.setObjectName("roundButton")
        self.details_button.setIcon(icons.icon("info", 16, "text-secondary"))
        self.details_button.setIconSize(QSize(16, 16))
        self.details_button.setToolTip(i18n.tr('任务详情'))
        self.details_button.setAccessibleName(i18n.tr('任务详情'))
        self.details_button.clicked.connect(self.open_details)

        # 运行控制单独放进一个零边距容器：全部隐藏时整组收掉，不留空槽位。
        run_controls = QHBoxLayout()
        run_controls.setContentsMargins(0, 0, 0, 0)
        run_controls.setSpacing(theme_module.SPACE["sm"])
        for button in (self.start_button, self.pause_button,
                       self.cancel_button, self.retry_button, self.stage_action_button):
            run_controls.addWidget(button)
        self.header_actions_bar = QWidget()
        self.header_actions_bar.setContentsMargins(0, 0, 0, 0)
        self.header_actions_bar.setLayout(run_controls)

        # ---- Golden Gate 统一工具栏（一行，贴住窗口顶边，底部一条发丝线） ----------
        #   返回 · 文件名（下面一行是状态芯片 + 运行说明） · 阶段胶囊（居中）
        #   · 术语/更多 · 进度 · 运行动作
        subtitle = QHBoxLayout()
        subtitle.setContentsMargins(0, 0, 0, 0)
        subtitle.setSpacing(theme_module.SPACE["xs"] + 2)
        # 芯片按内容高居中：QLabel 的垂直策略允许被拉伸，不给对齐就会被撑到与按钮同高。
        self.chip_state.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        subtitle.addWidget(self.chip_state, 0, Qt.AlignmentFlag.AlignVCenter)
        subtitle.addStretch(1)
        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(1)
        identity.addWidget(self.title_label)
        identity.addLayout(subtitle)
        identity_box = QWidget()
        identity_box.setLayout(identity)
        identity_box.setMinimumWidth(180)
        identity_box.setMaximumWidth(360)

        self.stage_bar = StageBar()
        self.stage_bar.pageRequested.connect(self.show_page)

        header_card = QFrame()
        header_card.setObjectName("unifiedToolbar")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["sm"],
                                         theme_module.SPACE["lg"], theme_module.SPACE["sm"])
        header_layout.setSpacing(theme_module.SPACE["md"])
        header_layout.addWidget(self.back_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(identity_box, 1)
        header_layout.addWidget(self.stage_bar, 2)
        header_layout.addWidget(self.run_status, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.check_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.header_actions_bar, 0, Qt.AlignmentFlag.AlignVCenter)

        # ---- 翻译标签页 ----------------------------------------------------
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.tr('搜索原文或译文，或输入段号…'))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filters_changed)

        self.search_edit.addAction(
            icons.icon("search", 16, "text-muted"),
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

        # 筛选收进一个菜单：六个条件同时放在工具条上放不下，也读不过来。
        # 勾选多个时取交集（"含项目术语"且"原文质量问题"……）。
        self.filter_button = QToolButton()
        self.filter_button.setObjectName("toolbarButton")
        self.filter_button.setText(i18n.tr('筛选'))
        self.filter_button.setIcon(icons.icon("filter", 16, "text-secondary"))
        self.filter_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_menu = QMenu(self.filter_button)
        self.filter_actions: dict[str, QAction] = {}
        for key, label in (("edited", '只看人工修改'), ("issues", '只看必须处理'),
                           ("terms", '含项目术语'), ("tm", '使用了翻译记忆'),
                           ("source_quality", '原文质量问题'), ("term_issues", '术语问题'),
                           ("notes", '有批注')):
            action = self.filter_menu.addAction(i18n.tr(label))
            action.setCheckable(True)
            action.toggled.connect(self._filters_changed)
            self.filter_actions[key] = action
        self.filter_menu.addSeparator()
        clear = self.filter_menu.addAction(i18n.tr('清除全部筛选'))
        clear.triggered.connect(self.clear_filters)
        self.filter_button.setMenu(self.filter_menu)
        self.only_edited_box = self.filter_actions["edited"]
        self.only_issues_box = self.filter_actions["issues"]

        toolbar = QHBoxLayout()
        # 零边距：`QWidget()` 包装体的默认布局边距（macOS 上约 9–12px × 上下）
        # 会让这一条带子凭空高 24px —— 表格因此要往下挪一整行的距离。
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(theme_module.SPACE["sm"])
        # 搜索框给一个下限：窄窗时它是被压得最狠的控件，压到 164px 连"搜索原文或
        # 译文"的占位文字都读不全。余量先由右侧的弹性占位让，再由它自己收。
        self.search_edit.setMinimumWidth(200)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.status_segments)
        # 「筛选」是一组新的开关，与左侧的状态分段控件之间给一组间距离（12px），
        # 组内仍是 8px —— 三种控件挤在一起时，组边界是唯一的分组线索。
        toolbar.addSpacing(theme_module.SPACE["xs"])
        toolbar.addWidget(self.filter_button)
        self.find_replace_button = QToolButton()
        self.find_replace_button.setObjectName("toolbarButton")
        self.find_replace_button.setText(i18n.tr('查找替换…'))
        self.find_replace_button.setIcon(icons.icon("search", 16, "text-secondary"))
        self.find_replace_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.find_replace_button.clicked.connect(self._request_find_replace)
        toolbar.addWidget(self.find_replace_button)
        # 原页：只有 PDF 来源的任务才显示；点一下在左侧展开（⌘⌥P）。
        self.source_button = QToolButton()
        self.source_button.setObjectName("toolbarButton")
        self.source_button.setCheckable(True)
        self.source_button.setText(i18n.tr('原页'))
        self.source_button.setIcon(icons.icon("file", 16, "text-secondary"))
        self.source_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.source_button.setToolTip(i18n.tr('在左侧展开 PDF 原页，跟随选中的段落（⌘⌥P）'))
        self.source_button.clicked.connect(lambda checked: self.set_source_open(checked))
        self.source_button.setVisible(False)
        toolbar.addWidget(self.source_button)
        toolbar.addStretch(1)
        # ⚠️ Qt 布局坑（macOS + QSS 主题实测）：QHBoxLayout 给 QLabel / QCheckBox
        # 分配的槽位比控件自身的 sizeHint **窄**（复选框 110→99、"筛选"标签 24→22），
        # 于是两颗复选框互相压住 3px —— "只看人工修改"的末字被下一颗的方框盖住。
        # WA_LayoutUsesWidgetRect 让布局按控件整框分配，实测 shortfall 11px → 0。
        # 只挂在这一条工具条上：设置页的复选框在竖排布局里，改全局会动那里已校准的高度。
        # 未提交修改必须看得见："切换段落 / 筛选 / 任务都不丢稿"的承诺在这里兑现。
        self.drafts_banner = QWidget()
        self.drafts_banner.setObjectName("draftsBanner")
        self.drafts_banner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        banner = QHBoxLayout(self.drafts_banner)
        banner.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["xs"],
                                  theme_module.SPACE["md"], theme_module.SPACE["xs"])
        self.drafts_label = QLabel("")
        self.save_all_drafts_button = QPushButton(i18n.tr('全部保存'))
        self.save_all_drafts_button.clicked.connect(self._save_all_drafts)
        self.discard_all_drafts_button = QPushButton(i18n.tr('全部丢弃'))
        self.discard_all_drafts_button.setObjectName("ghostAction")
        self.discard_all_drafts_button.clicked.connect(self._discard_all_drafts)
        banner.addWidget(self.drafts_label, 1)
        banner.addWidget(self.discard_all_drafts_button)
        banner.addWidget(self.save_all_drafts_button)
        self.drafts_banner.setVisible(False)
        self.toolbar_bar = QWidget()
        self.toolbar_bar.setContentsMargins(0, 0, 0, 0)
        self.toolbar_bar.setLayout(toolbar)

        self.segment_list = QTableWidget(0, len(self.COLUMNS), self)
        self.segment_list.setObjectName("segmentGrid")
        self.segment_list.setHorizontalHeaderLabels(
            [i18n.tr(column) for column in self.COLUMNS])
        self.segment_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.segment_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.segment_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.segment_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.segment_list.setShowGrid(False)
        # 全部展开、按原文 / 译文中较长的一方自适应行高（acceptance-feedback 第 20 条）。
        self.segment_list.setWordWrap(True)
        self.row_delegate = segment_editor.WorkbenchRowDelegate(self.segment_list)
        self.segment_list.setItemDelegate(self.row_delegate)
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
        # 译文就地编辑：唯一的编辑框浮在表格上，跟着当前行走（见 segment_editor）。
        self.inline_editor = segment_editor.InlineSegmentEditor(self.segment_list)
        self._active_position = -1
        segment_editor.install_repositioning(self.segment_list, self._place_inline_editor)
        self.segment_list.itemSelectionChanged.connect(self._selection_changed)
        self.segment_list.currentCellChanged.connect(
            lambda row, *_rest: self._row_selected(row))
        self._height_cache: dict = {}
        self._heights_pending: list[int] = []
        self._height_timer = QTimer(self)
        self._height_timer.setInterval(0)
        self._height_timer.timeout.connect(self._apply_row_height_chunk)
        self._height_resize = QTimer(self)
        self._height_resize.setSingleShot(True)
        self._height_resize.setInterval(120)
        self._height_resize.timeout.connect(lambda: self._schedule_row_heights(reset=True))
        self.segment_list.horizontalHeader().sectionResized.connect(self._columns_resized)

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
        self.discard_draft_button = QPushButton(i18n.tr('放弃修改'))
        self.discard_draft_button.setObjectName("ghostAction")
        self.discard_draft_button.setToolTip(
            i18n.tr('删除这段未提交的草稿，编辑器回到当前已提交的译文'))
        self.discard_draft_button.clicked.connect(
            lambda: self.discardDraftRequested.emit(self._current_index))
        self.copy_source_button = _icon_button(QPushButton(i18n.tr('复制原文到译文')), "copy", "text-secondary")
        self.copy_source_button.setObjectName("ghostAction")
        self.copy_source_button.clicked.connect(self._copy_source_to_target)
        self.save_next_button = QPushButton(i18n.tr('保存并下一段'))
        self.save_next_button.setToolTip(i18n.tr('保存这一段并进入下一个未确认的段（⌘↩）'))
        self.save_next_button.clicked.connect(self.save_and_next)
        self.submit_button = QPushButton(i18n.tr('保存'))
        self.submit_button.setToolTip(i18n.tr('保存并提交这一段（⌘S）'))
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
        self.restore_button = _icon_button(QPushButton(i18n.tr('恢复原译')), "refresh", "text-secondary")
        self.restore_button.setToolTip(i18n.tr('撤销这一段的人工修改，回到修改之前的译文；该段需要重新审校'))
        self.restore_button.clicked.connect(
            lambda: self.restoreRequested.emit(self._current_index))
        self.restore_button.setVisible(False)
        self.structure_button = _icon_button(QPushButton(i18n.tr('段落结构…')), "list", "text-secondary")
        self.structure_button.setToolTip(
            i18n.tr('改正原文、拆分、合并、插入或删除空段；可撤销最近一次结构操作'))
        self.structure_menu = QMenu(self.structure_button)
        self._structure_actions: dict[str, QAction] = {}
        for key, label in (("source_edit", '修改原文…'), ("split", '拆分本段…'),
                           ("merge", '与下一段合并…'), ("insert", '在其后插入空段'),
                           ("delete", '删除空段')):
            action = self.structure_menu.addAction(i18n.tr(label))
            action.triggered.connect(
                lambda _checked=False, op=key:
                self.structureRequested.emit(op, self._current_index))
            self._structure_actions[key] = action
        self.structure_menu.addSeparator()
        self.undo_structure_action = self.structure_menu.addAction(i18n.tr('撤销上一次结构操作'))
        self.undo_structure_action.triggered.connect(
            lambda _checked=False: self.structureRequested.emit("undo", self._current_index))
        self.structure_button.setMenu(self.structure_menu)
        self.view_source_button = _icon_button(QPushButton(i18n.tr('查看原位置')), "eye", "text-secondary")
        self.view_source_button.setToolTip(i18n.tr('在 PDF 原页里定位并高亮这一段'))
        self.view_source_button.clicked.connect(
            lambda: self.show_source_location(self._current_index))
        self.view_source_button.setVisible(False)
        self.review_button = _icon_button(QPushButton(i18n.tr('审校本段')), "shield-check", "text-secondary")
        self.review_button.clicked.connect(
            lambda: self.reviewCurrentRequested.emit(self._current_index))

        nav_row = QHBoxLayout()
        nav_row.setSpacing(theme_module.SPACE["xs"])
        nav_row.addWidget(self.prev_button)
        nav_row.addWidget(self.next_button)
        nav_row.addStretch(1)

        # 编辑框下面那一条：只在改过之后出现（未保存 / 放弃 / 保存并下一段 / 保存）。
        self.target_edit = self.inline_editor.edit
        self.target_edit.textChanged.connect(self._text_changed)
        self.target_edit.commitAndNext.connect(self._commit_and_next)
        self.target_edit.escaped.connect(self.segment_list.setFocus)
        for button in (self.discard_draft_button, self.save_next_button, self.submit_button):
            self.inline_editor.add_footer_button(button)

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

        # ---- 右侧操作栏 ------------------------------------------------------
        # 译文只在中间改；右栏回答"对选中的段落能做什么、它现在怎么样"。按钮作用于
        # 表格里选中的行：选一行时是这一段的全部操作，选多行时只留能批量做的（审校）。
        for button in (self.review_button, self.review_selected_button, self.retranslate_button,
                       self.copy_source_button, self.restore_button, self.exclude_button,
                       self.structure_button, self.view_source_button):
            button.setObjectName("panelAction")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.review_button.setObjectName("panelPrimary")
        self.review_button.setIcon(icons.icon("shield-check", 16, "accent"))
        self.exclude_button.setObjectName("panelDanger")
        self.exclude_button.setIcon(icons.icon("eye-off", 16, "danger"))
        # 「AI 点评」挨着「审校本段」，并用一句话说清两者的区别：审校会改变审校
        # 状态，点评只给建议（此前它是"需要注意"里一个蓝色链接，名字和审校分不清）。
        self.analyze_button = _icon_button(QPushButton(i18n.tr('AI 点评本段')), "sparkles", "text-secondary")
        self.analyze_button.setObjectName("panelAction")
        self.analyze_button.setToolTip(
            i18n.tr('让模型指出这一段值得注意的翻译取舍（最多 3 条，一次模型调用）；只给建议，不改变审校状态'))
        self.analyze_button.clicked.connect(self._request_analysis)
        self.review_hint = QLabel(i18n.tr('审校会更新这一段的审校状态；点评只给建议。'))
        self.review_hint.setObjectName("hint")
        self.review_hint.setWordWrap(True)
        self.analyze_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.selection_title = QLabel(i18n.tr('尚未选择段落'))
        self.selection_title.setObjectName("cardTitle")

        # 看 → 查 → 改 → 记：译者处理一段的顺序（见 segment_assist）。
        self.attention_list = segment_assist.AttentionList()
        self.attention_list.preserveRequested.connect(
            lambda item: self.findingResolveRequested.emit(
                [str(item.get("finding_id") or "")], adapter.RESOLVE_PRESERVED,
                "工作台：保留当前译文"))
        self.attention_list.acceptRequested.connect(
            lambda item: self.suggestionApplyRequested.emit(item, "工作台：采用建议译文"))
        self.attention_list.locateRequested.connect(self._locate_decision)
        self.attention_list.analyzeRequested.connect(self._request_analysis)
        self._analyzing_index = -1
        self.term_list = segment_assist.TermList()
        self.term_list.insertRequested.connect(self._insert_into_target)
        self.term_list.hovered.connect(self._hover_term)
        self.term_list.activated.connect(self.focus_term)
        self._hover = None
        self._decision_marks: dict = {}    # 决策痕迹的高亮（叠在术语标注之上）
        self._term_focus = None            # term_highlight.Occurrences
        self._term_counts: dict = {}
        self.term_add_button = _icon_button(QPushButton(i18n.tr('加入术语库…')), "plus",
                                            "text-secondary")
        self.term_add_button.setToolTip(
            i18n.tr('先在原文或译文里选中词语，再点这里；也可以直接填写'))
        self.term_add_button.clicked.connect(self._request_term_add)
        self.context_view = segment_assist.ContextView()
        self.context_view.jumpRequested.connect(self.select_segment)
        self.note_box = segment_assist.NoteBox()
        self.note_box.saveRequested.connect(self._save_note)
        self._notes: dict[str, dict] = {}
        self.suggest_box = segment_assist.SuggestBox()
        self.suggest_box.suggestRequested.connect(
            lambda instruction: self.suggestRequested.emit(self._current_index, instruction))
        self.suggest_box.adoptRequested.connect(self._adopt_suggestion)
        self.ask_box = segment_assist.AskAgentBox()
        self.ask_box.askRequested.connect(
            lambda question: self.askAgentRequested.emit(self._current_index, question))
        for button in (self.term_add_button,):
            button.setObjectName("panelAction")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        inspector = Card()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                            theme_module.SPACE["lg"], theme_module.SPACE["md"])
        inspector_layout.setSpacing(theme_module.SPACE["sm"])
        title_row = QHBoxLayout()
        title_row.addWidget(self.selection_title, 1)
        title_row.addLayout(nav_row)
        inspector_layout.addLayout(title_row)
        inspector_layout.addWidget(self.segment_status)
        self.segment_status.setWordWrap(True)
        self.segment_status.setMinimumHeight(self.segment_status.fontMetrics().lineSpacing() * 2)

        # 操作固定在上方；数量会变的注意事项、术语与前后文在下方有界滚动，
        # 切换段落不再把操作按钮推走（P4 验证切片；无问题时仍隐藏注意标题）。
        def tool(button: QPushButton, label: str, tip: str, name: str = "inspectorTool") -> QPushButton:
            button.setText(label)
            button.setObjectName(name)
            button.setToolTip(tip)
            button.setAccessibleName(tip.split("：")[0].split("（")[0])
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return button

        for button, tip in ((self.copy_source_button, i18n.tr('复制原文到译文')),
                            (self.restore_button, self.restore_button.toolTip()),
                            (self.structure_button, self.structure_button.toolTip()),
                            (self.view_source_button, self.view_source_button.toolTip()),
                            (self.exclude_button, self.exclude_button.toolTip())):
            label = {self.copy_source_button: i18n.tr('复制原文'),
                     self.restore_button: i18n.tr('恢复原译'),
                     self.structure_button: i18n.tr('结构'),
                     self.view_source_button: i18n.tr('原位置'),
                     self.exclude_button: i18n.tr('排除')}[button]
            full = {self.copy_source_button: i18n.tr('复制原文到译文'),
                    self.restore_button: i18n.tr('恢复原译'),
                    self.structure_button: i18n.tr('段落结构…'),
                    self.view_source_button: i18n.tr('查看原位置'),
                    self.exclude_button: i18n.tr('排除本段')}[button]
            tool(button, label, f"{full}：{tip}" if tip and tip != full else full,
                 "inspectorDanger" if button is self.exclude_button else "inspectorTool")
        tool(self.review_button, i18n.tr('审校'),
             i18n.tr('审校本段：正式审校这一段，会更新它的审校状态'), "inspectorPrimary")
        tool(self.review_selected_button, i18n.tr('审校所选'),
             i18n.tr('审校所选段落'), "inspectorPrimary")
        tool(self.analyze_button, i18n.tr('点评'), self.analyze_button.toolTip())
        tool(self.retranslate_button, i18n.tr('重译'),
             i18n.tr('重译本段：只重译这一段，并刷新它的审校状态'))

        # 一个共用输入框：写一句要求点「重译」，或者问一个问题点「提问」。
        self.assist_input = QLineEdit()
        # 空状态就是说明书：告诉用户可以先选中一句再直接打字（见 eventFilter）。
        self.assist_input.setPlaceholderText(i18n.tr('选中一句，直接输入问题或重译要求…'))
        self.assist_retranslate = QPushButton(i18n.tr('按要求重译'))
        self.assist_ask = QPushButton(i18n.tr('提问'))
        for button in (self.assist_retranslate, self.assist_ask):
            button.setObjectName("inspectorTool")

        def send(box) -> None:
            text = self.assist_input.text().strip()
            if not text:
                return
            box.input.setText(text)
            (box._suggest if box is self.suggest_box else box._ask)()

        self.assist_retranslate.clicked.connect(lambda: send(self.suggest_box))
        self.assist_ask.clicked.connect(lambda: send(self.ask_box))
        self.assist_input.returnPressed.connect(lambda: send(self.suggest_box))
        for box in (self.suggest_box, self.ask_box):
            box.input.setVisible(False)
            box.button.setVisible(False)

        def row_of(*widgets) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(theme_module.SPACE["xs"])
            for widget in widgets:
                row.addWidget(widget, 1)
            return row

        # 只对单独一段有意义的部分：选多段时整组收起。
        self.single_segment_section = QWidget()
        single = QVBoxLayout(self.single_segment_section)
        single.setContentsMargins(0, 0, 0, 0)
        single.setSpacing(theme_module.SPACE["sm"])
        self.attention_label = style_module.SectionLabel(i18n.tr('需要注意'))
        self.attention_label.setObjectName("attentionHeading")
        self.segment_ops_section = QWidget()
        ops = QVBoxLayout(self.segment_ops_section)
        ops.setContentsMargins(0, 0, 0, 0)
        ops.setSpacing(theme_module.SPACE["xs"])
        ops.addWidget(style_module.SectionLabel(i18n.tr('段落操作')))
        ops.addLayout(row_of(self.copy_source_button, self.restore_button,
                             self.structure_button, self.view_source_button,
                             self.exclude_button))
        single.addWidget(self.segment_ops_section)
        inspector_layout.addWidget(self.single_segment_section)

        inspector_layout.addWidget(style_module.SectionLabel(i18n.tr('AI 操作')))
        inspector_layout.addLayout(row_of(self.review_button, self.review_selected_button,
                                          self.analyze_button, self.retranslate_button))
        self.assist_row = QWidget()
        assist = QHBoxLayout(self.assist_row)
        assist.setContentsMargins(0, 0, 0, 0)
        assist.setSpacing(theme_module.SPACE["xs"])
        assist.addWidget(self.assist_input, 1)
        assist.addWidget(self.assist_retranslate)
        assist.addWidget(self.assist_ask)
        inspector_layout.addWidget(self.assist_row)
        # The two result boxes share one zero-spacing holder: empty, they take no room.
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)
        results_layout.addWidget(self.suggest_box)
        results_layout.addWidget(self.ask_box)
        inspector_layout.addWidget(results)

        self.segment_extra_section = QScrollArea()
        self.segment_extra_section.setObjectName("segmentReferences")
        self.segment_extra_section.setWidgetResizable(True)
        self.segment_extra_section.setFrameShape(QScrollArea.Shape.NoFrame)
        self.segment_extra_section.viewport().setAutoFillBackground(False)
        self.segment_extra_section.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 填满操作按钮下方的剩余高度（随窗口伸缩），不再固定 360 px：大屏不浪费，
        # 外层滚动只在窗口矮到装不下最小高度时才出现，平时只有这一层滚动。
        self.segment_extra_section.setMinimumHeight(180)
        self.segment_extra_section.setSizePolicy(QSizePolicy.Policy.Preferred,
                                                 QSizePolicy.Policy.Expanding)
        self.segment_extra_section.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.segment_extra_section.setAccessibleName(" / ".join(
            i18n.tr(label) for label in ('需要注意', '术语', '前后文', '批注')))
        self.segment_extra_section.setAccessibleDescription(
            i18n.tr('滚动查看本段的注意事项、术语、前后文和批注。'))
        references = QWidget()
        references.setObjectName("segmentReferencesBody")
        references.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # The scroll area must grow its content instead of squeezing reference
        # widgets below their natural heights (which clips term button text).
        for widget in (self.attention_list, self.term_list, self.context_view, self.note_box):
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        extra = QVBoxLayout(references)
        extra.setContentsMargins(0, 0, 0, 0)
        extra.setSpacing(theme_module.SPACE["sm"])
        extra.addWidget(self.attention_label)
        extra.addWidget(self.attention_list)
        self.term_heading = style_module.SectionLabel(i18n.tr('术语'))
        extra.addWidget(self.term_heading)
        extra.addWidget(self.term_list)
        extra.addWidget(self.term_add_button)
        # 前后文：平时网格里相邻的行就是前后文（整段显示之后这一节是重复的）；
        # 只有筛选 / 搜索让相邻行不再相邻时才出现。
        self.context_heading = style_module.SectionLabel(i18n.tr('原文中的前后文'))
        extra.addWidget(self.context_heading)
        extra.addWidget(self.context_view)
        extra.addWidget(style_module.SectionLabel(i18n.tr('批注')))
        extra.addWidget(self.note_box)
        extra.addStretch(1)
        self.segment_extra_section.setWidget(references)
        # setWidget() enables autoFillBackground on its child. Its Base palette
        # stays light on macOS even in our dark theme, obscuring pale text.
        references.setAutoFillBackground(False)
        # 「段落事实」只是把顶上那行状态再说一遍：来源并进状态行，这一节不再单独占位。
        inspector_layout.addWidget(self.segment_extra_section, 1)
        self.review_hint.setVisible(False)
        self.multi_hint = QLabel(i18n.tr('按住 ⇧ 或 ⌘ 点选可以多选；批量操作会作用于所有选中的段落。'))
        self.multi_hint.setObjectName("hint")
        self.multi_hint.setWordWrap(True)
        self.multi_hint.setVisible(False)
        inspector_layout.addWidget(self.multi_hint)
        # 多选时参考区隐藏，由这段弹性空白把内容顶到上方；单选时剩余高度都给参考区。
        inspector_layout.addStretch(0)

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
        self.right_panel = components.SlidingPanel(inspector_scroll)
        self._inspector_width = 300
        translation_split.addWidget(self.segment_list)
        translation_split.addWidget(self.right_panel)
        translation_split.setStretchFactor(0, 1)
        translation_split.setStretchFactor(1, 0)
        # 检查器 400（原 460）：它是**编辑当前一段**的面板，不该比段落列表还宽。
        # 旧值下窗口缩到 940 宽时 splitter 给出 [455, 460] —— 右侧比左侧还宽，
        # 而左侧正是用户要扫的段落表。列表给一个最小宽度，保证它先拿到空间。
        translation_split.setSizes([860, 300])
        self.segment_list.setMinimumWidth(360)
        self.translation_split = translation_split
        self.inspector_scroll = inspector_scroll

        translation_page = QWidget()
        translation_layout = QVBoxLayout(translation_page)
        translation_layout.setContentsMargins(0, 8, 0, 0)
        translation_layout.setSpacing(8)
        translation_layout.addWidget(self.toolbar_bar)
        translation_layout.addWidget(self._build_term_focus_bar())
        translation_layout.addWidget(self.drafts_banner)
        translation_layout.addWidget(self.translation_split, 1)
        # 字数统计：底栏一行（整份进度 + 当前段 / 所选段），点「详情」看分项。
        self.word_bar = QWidget()
        word_layout = QHBoxLayout(self.word_bar)
        word_layout.setContentsMargins(theme_module.SPACE["xs"], 0, 0, 0)
        word_layout.setSpacing(theme_module.SPACE["md"])
        # 字数只有一处：底栏左边这一行本身可点，点开浮窗看分项与当前段 / 所选段
        # （acceptance-feedback 第 4 条：去掉右下角的「字数统计…」与右侧那行）。
        self.word_total_label = ClickableLabel("")
        self.word_total_label.setObjectName("wordTotal")
        self.word_total_label.setToolTip(i18n.tr('字数统计'))
        self.word_total_label.clicked.connect(self._show_word_count)
        self.word_selection_label = QLabel("")
        self.word_selection_label.setObjectName("hint")
        self.word_selection_label.setWordWrap(True)
        word_layout.addWidget(self.word_total_label)
        word_layout.addStretch(1)
        translation_layout.addWidget(self.word_bar)
        self._word_count_key: tuple = ()
        self._word_count = None
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

        # ---- 译前准备面板 --------------------------------------------------
        # 导入之后、开始翻译之前，正文区放的是「译前准备」：译者在这里选翻译策略
        # 与产出物。它是任务开始前的唯一决策面，因此**替代**了"还没有可编辑的段落"
        # 空状态——那个空状态用一句"点上方开始翻译"概括掉的，正是这一整步。
        # 面板比窗口高，套一层无边框滚动区，内容可达而不把窗口顶出屏幕。
        self.setup_panel = TaskSetupPanel()
        self.setup_panel.startRequested.connect(self.setupStartRequested.emit)
        # 建项目要弹输入框、写盘、刷新首页列表 —— 那些是 window 的职责，这里只转发。
        self.setup_panel.termbaseAddRequested.connect(self.setupTermbaseAddRequested.emit)
        self.setup_panel.styleProfileRequested.connect(self.setupStyleProfileRequested.emit)
        self.setup_panel.profileEditRequested.connect(self.profileEditRequested.emit)
        self.setup_panel.connectionSettingsRequested.connect(self.connectionSettingsRequested.emit)
        self.setup_panel.termbaseRemoveRequested.connect(self.setupTermbaseRemoveRequested.emit)
        self.setup_panel.literatureAddRequested.connect(self.setupLiteratureAddRequested.emit)
        self.setup_panel.literatureRemoveRequested.connect(
            self.setupLiteratureRemoveRequested.emit)
        self.setup_panel.createProjectRequested.connect(
            self.setupCreateProjectRequested.emit)
        setup_scroll = QScrollArea()
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        setup_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        setup_scroll.viewport().setAutoFillBackground(False)
        setup_scroll.setWidget(self.setup_panel)
        self.setup_scroll = setup_scroll
        # 「开始翻译」留在面板右栏、紧跟设置之后（交互路径最短）；两栏布局在常见窗口
        # 高度里一屏放得下，展开次要设置时它随内容一起滚动。
        self.setup_page = QWidget()
        setup_layout = QVBoxLayout(self.setup_page)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(0)
        setup_layout.addWidget(self.setup_scroll, 1)
        self.setup_page.setVisible(False)
        translation_layout.addWidget(self.setup_page, 1)

        # ---- 交付标签页 ----------------------------------------------------
        # 交付清单按 Web 界面的表达重做：一行一个文件（类型图标 + 名称 + 大小），
        # 按用途分组；没有冻结交付、没有排除段落时给明确的空状态，而不是一块空白列表。
        self.delivery_label = QLabel(i18n.tr('交付状态：—'))
        self.delivery_label.setWordWrap(True)
        self.deliver_button = QPushButton(i18n.tr('审校完成后冻结交付'))
        self.deliver_button.setObjectName("primaryAction")
        self.deliver_button.clicked.connect(self.deliverRequested.emit)
        # 交付说明：写进交付快照的批准记录（以前固定写"桌面端人工确认"）。
        self.delivery_note = QLineEdit()
        self.delivery_note.setPlaceholderText(i18n.tr('交付说明（可选，例如：已完成人工审校）'))
        self.risk_button = QPushButton(i18n.tr('接受风险并交付…'))
        self.risk_button.setObjectName("dangerAction")
        self.risk_button.setToolTip(
            i18n.tr('仍有必须处理的问题时，写下说明并确认后继续交付；问题不会被删除'))
        self.risk_button.clicked.connect(self.deliverWithRiskRequested.emit)
        self.risk_button.setVisible(False)
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
        delivery_actions.addWidget(self.risk_button)
        delivery_actions.addWidget(self.export_button)
        delivery_actions.addWidget(self.save_snapshot_button)
        delivery_actions.addStretch(1)

        delivery_card = Card()
        delivery_layout = QVBoxLayout(delivery_card)
        delivery_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                           theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        delivery_layout.setSpacing(theme_module.SPACE["md"])
        delivery_layout.addWidget(self.delivery_label)
        delivery_layout.addWidget(self.delivery_note)
        delivery_layout.addLayout(delivery_actions)
        delivery_layout.addLayout(asset_header)
        delivery_layout.addWidget(self.asset_notice)
        delivery_layout.addWidget(self.asset_list)
        delivery_layout.addWidget(self.asset_empty)
        delivery_layout.addLayout(asset_footer)

        # 补生成：翻译完成后还能从已保存的译文生成、但当初没选的文件（例如电子书）。
        # 不用重新翻译；点一下就走同一道交付门禁，冻结一个包含它的新版本。
        self.supplement_card = Card()
        supplement_layout = QVBoxLayout(self.supplement_card)
        supplement_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                             theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        supplement_layout.setSpacing(theme_module.SPACE["sm"])
        supplement_title = QLabel(i18n.tr('补生成交付文件'))
        supplement_title.setObjectName("cardTitle")
        self.supplement_hint = QLabel(
            i18n.tr('不用重新翻译：从已完成的译文生成，交付会更新为新版本，原有文件保持不变。'))
        self.supplement_hint.setObjectName("hint")
        self.supplement_hint.setWordWrap(True)
        self.supplement_grid = QGridLayout()
        self.supplement_grid.setHorizontalSpacing(theme_module.SPACE["sm"])
        self.supplement_grid.setVerticalSpacing(theme_module.SPACE["sm"])
        supplement_layout.addWidget(supplement_title)
        supplement_layout.addWidget(self.supplement_hint)
        supplement_layout.addLayout(self.supplement_grid)
        self.supplement_buttons: dict[str, QPushButton] = {}
        self._supplement_keys: tuple = ()
        self.supplement_card.setVisible(False)

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
        # Golden Gate：交付是一张居中的"检查单"，内容列最宽 820px，两侧留白。
        delivery_column = QWidget()
        delivery_column.setMaximumWidth(820)
        delivery_content_layout = QVBoxLayout(delivery_column)
        delivery_content_layout.setContentsMargins(0, theme_module.SPACE["lg"], 0, 0)
        delivery_content_layout.setSpacing(theme_module.SPACE["md"])
        delivery_content_layout.addWidget(delivery_card)
        delivery_content_layout.addWidget(self.supplement_card)
        delivery_content_layout.addWidget(excluded_card)
        delivery_content_layout.addStretch(1)
        delivery_centre = QHBoxLayout(delivery_content)
        delivery_centre.setContentsMargins(0, 0, 0, 0)
        delivery_centre.addStretch(1)
        delivery_centre.addWidget(delivery_column, 100)
        delivery_centre.addStretch(1)
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

        # 页面容器：沿用 QTabWidget（页面增删、可见性、`setCurrentWidget` 都现成），
        # 但它自己的标签栏不显示——导航由头部的阶段步骤条承担（见 `show_page`）。
        self.tabs = QTabWidget()
        self.tabs.tabBar().setVisible(False)
        self.tabs.setIconSize(QSize(16, 16))
        self.tabs.addTab(translation_page,
                         icons.icon("translate", 16, "text-secondary"),
                         i18n.tr('翻译'))
        # 严格术语治理：核心在候选术语未冻结时拒绝开始翻译。没有这条提示，任务只会
        # 停在"待术语确认"，用户不知道该去哪里。
        self.terms_gate = style_module.NoticeStrip("", tone="warn")
        self.terms_gate.setVisible(False)
        self.terms_panel = TermsPanel()
        self.terms_panel.draftSaveRequested.connect(self.glossaryDraftRequested.emit)
        self.terms_panel.freezeRequested.connect(self.glossaryFreezeRequested.emit)
        self.terms_panel.unfreezeRequested.connect(self.glossaryUnfreezeRequested.emit)
        self.terms_panel.bypassRequested.connect(self.glossaryBypassRequested.emit)
        self.terms_panel.entryUpdateRequested.connect(self.glossaryEntryUpdateRequested.emit)
        self.terms_panel.entryAddRequested.connect(self.glossaryEntryAddRequested.emit)
        self.terms_panel.statusRequested.connect(self.glossaryStatusRequested.emit)
        self.terms_panel.entryDeleteRequested.connect(self.glossaryEntryDeleteRequested.emit)
        self.tabs.addTab(self.terms_panel,
                         icons.icon("file-sheet", 16, "text-secondary"),
                         i18n.tr('术语'))
        self.review_panel = ReviewPanel()
        self.review_panel.styleRuleRequested.connect(self.styleRuleRequested.emit)
        self.review_panel.resolveRequested.connect(self.findingResolveRequested.emit)
        self.review_panel.applySuggestionRequested.connect(self.suggestionApplyRequested.emit)
        self.review_panel.confirmSourceRequested.connect(self.sourceConfirmRequested.emit)
        self.review_panel.reviewAllRequested.connect(self.reviewAllRequested.emit)
        self.review_panel.reviewRequested.connect(self.reviewSelectedRequested.emit)
        self.review_panel.retranslateRequested.connect(self.retranslateRequested.emit)
        self.review_panel.editRequested.connect(self.show_segment_for_edit)
        self.tabs.addTab(self.review_panel,
                         icons.icon("shield-check", 16, "text-secondary"),
                         i18n.tr('审校'))
        self.followup_panel = FollowupPanel()
        self.followup_panel.excludeFragmentsRequested.connect(self.excludeFragmentsRequested.emit)
        self.followup_panel.retryRequested.connect(self.followupRetryRequested.emit)
        self.followup_panel.resumeRequested.connect(self.followupResumeRequested.emit)
        self.followup_panel.previewRequested.connect(self.entityPreviewRequested.emit)
        self.followup_panel.repairRequested.connect(self.entityRepairRequested.emit)
        self.followup_panel.acceptRequested.connect(self.entityAcceptRequested.emit)
        self.followup_panel.rescanRequested.connect(self.rescanRequested.emit)
        self.followup_panel.locateRequested.connect(self._locate_segment_from_followup)
        self.followup_panel.profileEditRequested.connect(self.profileEditRequested.emit)
        self.activity_panel = ActivityPanel()
        self.activity_panel.retry_button.clicked.connect(self.retryRequested.emit)
        self.activity_panel.revealLogRequested.connect(
            lambda: self.revealLogRequested.emit(self.job_id))
        self.tabs.addTab(delivery_page,
                         icons.icon("package", 16, "text-secondary"),
                         i18n.tr('交付'))
        # 学术专业模式的页：默认隐藏，设置里主动打开（或任务本身已带着学术产物）才出现。
        self.report_panel = ReportPanel()
        self.report_panel.exportRequested.connect(self.reportExportRequested.emit)
        self.report_panel.repairRequested.connect(self.reportRepairRequested.emit)
        self.report_panel.answerSubmitted.connect(self.reportAnswerRequested.emit)
        self.report_panel.rebuildRequested.connect(self.reportRebuildRequested.emit)
        self.report_panel.templateAddRequested.connect(self.reportTemplateAddRequested.emit)
        self.report_panel.templateRemoveRequested.connect(self.reportTemplateRemoveRequested.emit)
        self.report_panel.openReviewRequested.connect(
            lambda: self.tabs.setCurrentWidget(self.review_panel))
        self.report_panel.openCasesRequested.connect(
            lambda: self.tabs.setCurrentWidget(self.cases_panel))
        self.tabs.addTab(self.report_panel,
                         icons.icon("file-text", 16, "text-secondary"),
                         i18n.tr('报告'))
        self.cases_panel = CasesPanel()
        self.cases_panel.approveRequested.connect(self.caseApproveRequested.emit)
        self.cases_panel.excludeRequested.connect(self.caseExcludeRequested.emit)
        self.cases_panel.replaceRequested.connect(self.caseReplaceRequested.emit)
        self.cases_panel.baselineSaveRequested.connect(self.caseBaselineSaveRequested.emit)
        self.cases_panel.baselineRejectRequested.connect(self.caseBaselineRejectRequested.emit)
        self.cases_panel.editRequested.connect(self.show_segment_for_edit)
        self.tabs.addTab(self.cases_panel,
                         icons.icon("file-text", 16, "text-secondary"),
                         i18n.tr('案例'))
        self.qa_panel = QAPanel()
        self.qa_panel.renderRequested.connect(self.qaRenderRequested.emit)
        self.qa_panel.factToggled.connect(self.qaFactToggled.emit)
        self.tabs.addTab(self.qa_panel,
                         icons.icon("shield-check", 16, "text-secondary"),
                         i18n.tr('合规与 QA'))
        self._academic_widgets = [self.report_panel, self.cases_panel, self.qa_panel]
        self._academic_mode = False
        self.tabs.currentChanged.connect(self._tab_changed)
        # 原页：工作台左侧可展开的面板（acceptance-feedback D2），不再是一整页。
        self.pdf_panel = PdfPanel()
        self.pdf_panel.closeRequested.connect(lambda: self.set_source_open(False))
        self.source_clip = components.ClippedWidget(self.pdf_panel, 400)
        self._source_open = False
        self._source_locate_pending = False
        self.source_clip.hide()
        self.translation_split.insertWidget(0, self.source_clip)
        self.translation_split.setStretchFactor(0, 0)
        self._pdf_ok = False
        self._pdf_width = 0
        self._pdf_follow = QTimer(self)
        self._pdf_follow.setSingleShot(True)
        self._pdf_follow.setInterval(150)
        self._pdf_follow.timeout.connect(self._follow_source)
        self.translation_split.splitterMoved.connect(self._remember_source_width)
        source_shortcut = QAction(i18n.tr('原页'), self)
        source_shortcut.setShortcut(QKeySequence("Ctrl+Alt+P"))
        source_shortcut.triggered.connect(lambda: self.set_source_open(not self.source_open()))
        self.addAction(source_shortcut)
        # 检查与修复：从右侧滑出的面板，暂时替换段落检查器（点网格里的段落不会
        # 关掉它，可以逐条处理）；运行记录：从运行状态组弹出的浮窗。两者都不再
        # 占满主屏（acceptance-feedback D3 / Q1 / Q2）。
        self.followup_drawer = QFrame()
        self.followup_drawer.setObjectName("followupDrawer")
        self.followup_drawer.setAccessibleName(i18n.tr('检查与修复'))
        self.followup_drawer.setAccessibleDescription(
            i18n.tr('处理任务中需要注意的问题；按 Esc 收起并返回编辑框。'))
        self._drawer_open = False
        self._motion = motion.Motion(self)
        drawer_title = QLabel(i18n.tr('检查与修复'))
        drawer_title.setObjectName("sectionTitle")
        drawer_close = QToolButton()
        drawer_close.setObjectName("roundButton")
        drawer_close.setIcon(icons.icon("x", 14, "text-secondary"))
        drawer_close.setIconSize(QSize(14, 14))
        drawer_close.setAccessibleName(i18n.tr('关闭检查与修复'))
        self.drawer_close = drawer_close
        drawer_close.clicked.connect(lambda: self.set_followup_drawer_open(False))
        drawer_head = QHBoxLayout()
        drawer_head.setContentsMargins(0, 0, 0, 0)
        drawer_head.addWidget(drawer_title, 1)
        drawer_head.addWidget(drawer_close)
        drawer_layout = QVBoxLayout(self.followup_drawer)
        drawer_layout.setContentsMargins(theme_module.SPACE["sm"], 0, 0, 0)
        drawer_layout.setSpacing(theme_module.SPACE["sm"])
        drawer_layout.addLayout(drawer_head)
        drawer_scroll = QScrollArea()
        drawer_scroll.setWidgetResizable(True)
        drawer_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        drawer_scroll.viewport().setAutoFillBackground(False)
        drawer_scroll.setWidget(self.followup_panel)
        drawer_layout.addWidget(drawer_scroll, 1)
        self.right_panel.set_drawer(self.followup_drawer)
        drawer_escape = QAction(self)
        drawer_escape.setShortcut(QKeySequence("Esc"))
        drawer_escape.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        drawer_escape.triggered.connect(self._escape_pressed)
        drawer_escape.setEnabled(False)
        self.addAction(drawer_escape)
        self.drawer_escape = drawer_escape
        self.activity_panel.heading.setVisible(False)  # the popover has the title
        self.activity_panel.hint.setVisible(False)     # repeats the Finder button
        # In a 440 px popover long steps wrap instead of scrolling sideways.
        self.activity_panel.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.activity_panel.list.setWordWrap(True)
        self.activity_popover = components.Popover(i18n.tr('运行记录'), self.activity_panel, self)
        self.details_panel = TaskDetailsPanel()
        self.details_panel.profileEditRequested.connect(self.profileEditRequested.emit)
        self.details_panel.promoteRequested.connect(self.promoteMemoryRequested.emit)
        # 任务详情：顶栏 ⓘ 打开的浮窗（任务开始后，准备阶段的选择、文档画像与用量都在
        # 这里；acceptance-feedback 第 12 条 E / D3）。
        details_scroll = QScrollArea()
        details_scroll.setWidgetResizable(True)
        details_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details_scroll.viewport().setAutoFillBackground(False)
        details_scroll.setWidget(self.details_panel)
        self.details_popover = components.Popover(i18n.tr('任务详情'), details_scroll, self,
                                                  size=QSize(560, 620))
        self._details_key: tuple = ()
        #: 页面 id → 页面。步骤条与代码里的跳转都用页面 id，不再依赖页签序号。
        self._pages: dict[str, QWidget] = {
            "translate": translation_page, "terms": self.terms_panel,
            "review": self.review_panel, "deliver": delivery_page,
            "report": self.report_panel, "cases": self.cases_panel,
            "qa": self.qa_panel,

        }
        self.tabs.currentChanged.connect(lambda _index: self._sync_stage_page())
        self._followup_count = 0
        self._terms_blocked = False
        self._sync_academic_tabs()

        body = QVBoxLayout()
        body.setContentsMargins(20, 12, 20, 12)
        body.setSpacing(10)
        body.addWidget(self.terms_gate)
        body.addWidget(self.tabs, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header_card)
        layout.addLayout(body, 1)

        # ⌘↩：编辑框里由编辑框自己接住（保存并进入下一段）；焦点在表格时是"下一段"。
        next_shortcut = QAction(i18n.tr('下一段'), self)
        next_shortcut.setShortcut(QKeySequence("Ctrl+Return"))
        next_shortcut.triggered.connect(self._commit_and_next)
        self.addAction(next_shortcut)

    # ================= 数据 =================
    def load(self, job_id: str, config: adapter.EngineConfig,
             state: dict, rows: list[adapter.SegmentRow],
             snapshots: list[dict]) -> None:
        """Attach a job.  Only call this when switching jobs."""
        for saved_index in self.row_delegate.saved:
            self._motion.stop(f"saved:{saved_index}")
        self.row_delegate.saved.clear()
        if self.job_id and self.job_id != job_id:
            self.flush_draft()
        # 换任务总是从翻译页开始：上一个任务停在哪一页（交付、术语……）与这个任务
        # 无关，带过来只会让人以为"怎么直接是交付页"。下一步在头部的步骤条上。
        self.show_page("translate")
        self.job_id = job_id
        self.config = config
        self._state = state
        self._snapshots = snapshots
        self._pending_saved = {}
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
        self._load_pdf(job_id)
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
        self._all_rows = [self._with_pending_save(row) for row in rows]
        if job_id != getattr(self, "_notes_job", None) or not self._notes_loaded_at_state(state):
            self._notes = adapter.segment_notes(job_id) if job_id else {}
            self._notes_job = job_id
            self._notes_state = id(state)
        self._render_segments()
        self._render_excluded()
        # 交付预检先于运行区：头部状态芯片要用它给出的任务状态标签。
        self._render_delivery(force=refresh_delivery)
        self._render_runtime()
        self._render_segment_detail()
        self._render_review()
        self._render_terms()
        self._render_followup()
        self._sync_academic_tabs()
        self._render_report()
        self._render_cases()
        self._render_qa()
        self._render_activity()
        self._render_details()
        self._render_stage()

    # ================= 阶段与页面导航 =================
    def show_page(self, page_id: str) -> None:
        """切到某一页（页面 id 见 `_pages`）。步骤条、跳转链接与测试都走这一个入口。

        ``followup`` 打开右侧的检查与修复面板，``activity`` 打开运行记录浮窗：
        它们不再是整页，但旧的跳转与菜单仍然用这两个 id。
        """
        if page_id == "followup":
            self.set_followup_drawer_open(True)
            return
        if page_id == "activity":
            self.open_activity()
            return
        if page_id == "source":
            self.set_source_open(True)
            return
        if page_id == "details":
            self.open_details()
            return
        page = self._pages.get(page_id)
        if page is not None:
            self.tabs.setCurrentWidget(page)

    #: 运行状态行里一条操作结果停留多久（秒）：普通告知很快让位给运行状态，
    #: 错误多留一会儿；完整内容一直在运行记录里。
    TASK_NOTICE_SECONDS = {False: 8, True: 60}

    def set_task_notice(self, message: str, *, error=False):
        """一次操作的结果借用运行状态行显示一会儿，然后让位给任务本身的状态。

        P6 曾让它一直留到「运行状态变化」为止——已完成或暂停的任务永远不变，于是
        「已提交人工修改…」或一次小操作的失败会一直占着这一行，进度条也一直是红的。
        现在按时长过期；操作失败只把文字标红，不把进度条染成「任务失败」。
        """
        runtime = self._last_runtime
        self._task_notice = (self.job_id, message, error,
                             (runtime.get("status"), runtime.get("detail"),
                              runtime.get("current_operation")), time.monotonic())
        seconds = self.TASK_NOTICE_SECONDS[bool(error)]
        QTimer.singleShot(int(seconds * 1000) + 50, self._render_runtime)
        self.run_status.setVisible(True)
        self.run_status.set_state(tone=self.run_status.track.tone(), line=message,
                                  line_tone="failed" if error else None)
        self.run_status.track.setVisible(self.task_started())
        self.run_status.percent.setVisible(self.task_started())
        self._render_activity()

    def open_activity(self) -> None:
        """运行记录浮窗：锚定在运行状态组下面；运行状态组不可见时锚定在顶栏右端。"""
        anchor = self.run_status if self.run_status.isVisible() else self.check_button
        if not anchor.isVisible():
            anchor = self
        self._render_activity()
        self.activity_popover.set_subtitle(self.run_status.line.text())
        self.activity_popover.open_at(anchor)

    def open_details(self) -> None:
        """任务详情浮窗：锚定在顶栏的 ⓘ 上。"""
        anchor = self.details_button if self.details_button.isVisible() else self.check_button
        self.details_popover.open_at(anchor if anchor.isVisible() else self)
        self._details_key = ()
        self._render_details()

    def followup_drawer_open(self) -> bool:
        return self._drawer_open

    def set_followup_drawer_open(self, open_: bool) -> None:
        """打开时切回翻译页，面板暂时替换右侧的段落检查器；关闭时检查器回来。"""
        open_ = bool(open_)
        if open_ == self.followup_drawer_open():
            self.check_button.setChecked(open_)
            if open_:
                self.tabs.setCurrentWidget(self._pages["translate"])
            return
        if open_:
            self.tabs.setCurrentWidget(self._pages["translate"])
        if open_ and self.right_panel.fraction == 0:
            width = self.translation_split.sizes()[self.translation_split.indexOf(self.right_panel)]
            if width:
                self._inspector_width = width
        self._drawer_open = open_
        self.check_button.setChecked(open_)
        self._drawer_open_for_escape = open_
        self.drawer_escape.setEnabled(open_ or self._term_focus is not None)
        if open_:
            # apply() already refreshes this data when the task state changes.
            # Clearing its signature here re-scans a whole book on every click
            # (~450 ms), freezing the transition before its first frame.
            self._render_followup()
        self._transition_columns()
        if open_:
            self.drawer_close.setFocus()
        else:
            self.focus_editor()

    def _transition_columns(self) -> None:
        """A single interruptible transition coordinates both side panels."""
        split = self.translation_split
        start_sizes = split.sizes()
        end_sizes = self._translation_column_sizes()
        start_fraction = self.right_panel.fraction
        self._motion.stop("translation-columns")
        side = split.indexOf(self.right_panel)
        source = split.indexOf(self.source_clip)
        self.followup_panel.set_preview_deferred(True)
        self.pdf_panel.set_render_deferred(True)
        self.source_clip.setVisible(bool(start_sizes[source] or end_sizes[source]))
        self.source_clip.freeze(max(1, end_sizes[source] or start_sizes[source]))
        self.right_panel.setVisible(bool(start_sizes[side] or end_sizes[side]))
        # Both sides are laid out at their own *final* widths even when this
        # transition interrupts another one halfway (P4 review item 7): the
        # current, intermediate split width would re-wrap the panel once.
        self.right_panel.begin(
            max(1, self._translation_column_sizes(drawer_open=False)[side]
                or self._inspector_width),
            max(1, self._translation_column_sizes(drawer_open=True)[side]))

        def frame(value: float) -> None:
            # Sizes share a curve. Text inside the clipped panel is laid out
            # once at its destination width, never at each intermediate width.
            split.setSizes([round(a + (b - a) * value)
                            for a, b in zip(start_sizes, end_sizes)])
            self.right_panel.set_fraction(start_fraction + (float(self._drawer_open) - start_fraction) * value)
            self._place_inline_editor()

        def settled() -> None:
            self.source_clip.setVisible(self._source_open)
            self.source_clip.freeze(None)
            self.right_panel.settle(self._drawer_open)
            self.right_panel.setVisible(bool(end_sizes[side]))
            self.followup_panel.set_preview_deferred(False)
            self._resume_source_render()

        self._motion.animate("translation-columns", 0.0, 1.0, frame,
                             kind="source" if start_sizes[source] != end_sizes[source] else "panel",
                             finished=settled)

    def _escape_pressed(self) -> None:
        """侧栏打开时的 Esc：焦点在译文编辑框里，Esc 仍按编辑框的约定把焦点还给表格
        （一个键只做一件事，按最近的上下文）；其余位置的 Esc 收起检查与修复。"""
        focus = QApplication.focusWidget()
        if focus is not None and (focus is self.target_edit
                                  or self.inline_editor.isAncestorOf(focus)):
            self.target_edit.escaped.emit()
            return
        if self._term_focus is not None:
            self.clear_term_focus()
            return
        self.set_followup_drawer_open(False)

    def _sync_escape(self) -> None:
        self.drawer_escape.setEnabled(self.followup_drawer_open()
                                      or self._term_focus is not None)

    # ================= 术语：本段标注、悬停高亮、全文定位 =================
    def _filters_active(self) -> bool:
        return bool(self.search_edit.text().strip()) or self._status_filter != "全部" or any(
            action.isChecked() for action in self.filter_actions.values())

    def _sync_context_visibility(self) -> None:
        shown = self._filters_active() and bool(self._rows)
        self.context_heading.setVisible(shown)
        self.context_view.setVisible(shown)

    def _document_term_counts(self, terms: list[dict]) -> dict:
        """每条术语在全文原文里出现在几段（按任务状态缓存）。"""
        from . import term_highlight

        key = id(self._state)
        if self._term_counts.get("__state__") != key:
            self._term_counts = {"__state__": key}
        counts = {}
        for term in terms:
            source = str(term.get("source") or "")
            if source not in self._term_counts:
                self._term_counts[source] = sum(
                    1 for row in self._all_rows if term_highlight.spans(source, row.source))
            counts[source] = self._term_counts[source]
        return counts

    def _hover_term(self, term) -> None:
        self._hover = dict(term) if term else None
        self._decorate_current_row()

    def _decorate_current_row(self) -> None:
        """当前行：原文里的术语、译文里的首选译名加虚线；悬停的那一对铺底色；
        禁用译名是红色波浪线。用 ExtraSelections，不改动文字本身。"""
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        from PySide6.QtWidgets import QTextEdit

        from . import term_highlight

        tokens = theme_module.tokens(style_module.current_theme())
        accent = theme_module.qcolor(tokens["accent"])
        soft = theme_module.qcolor(tokens["accent"])
        soft.setAlphaF(0.22)
        danger = theme_module.qcolor(tokens["danger"])
        hover = self._hover or {}

        def selections(edit, pairs) -> list:
            result = []
            document_text = edit.toPlainText()
            for needle, kind in pairs:
                for start, end in term_highlight.spans(needle, document_text):
                    selection = QTextEdit.ExtraSelection()
                    cursor = QTextCursor(edit.document())
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                    fmt = QTextCharFormat()
                    if kind == "forbidden":
                        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                        fmt.setUnderlineColor(danger)
                    else:
                        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
                        fmt.setUnderlineColor(accent)
                        if kind == "hover":
                            fmt.setBackground(soft)
                    selection.cursor = cursor
                    selection.format = fmt
                    result.append(selection)
            return result

        terms = self.term_list.terms
        source_pairs, target_pairs = [], []
        for term in terms:
            hovered = hover.get("source") == term.get("source")
            source_pairs.append((term.get("source"), "hover" if hovered else "term"))
            target_pairs.append((term.get("target"), "hover" if hovered else "term"))
            target_pairs.extend((word, "forbidden") for word in term.get("forbidden") or [])
        decisions = getattr(self, "_decision_marks", {})
        source_view = self.inline_editor.source_view
        source_view.setExtraSelections(selections(source_view, source_pairs)
                                       + decisions.get(id(source_view), []))
        self.target_edit.setExtraSelections(selections(self.target_edit, target_pairs)
                                            + decisions.get(id(self.target_edit), []))

    def _ask_about_selection(self, event) -> bool:
        """原文里选中了一句、直接开始打字：把这句引用进提问框，接着输入（不用先点提问框）。"""
        cursor = self.inline_editor.source_view.textCursor()
        text = event.text()
        if (not cursor.hasSelection() or not text or not text.isprintable()
                or event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                        | Qt.KeyboardModifier.MetaModifier)
                or not self.assist_input.isVisibleTo(self)):
            return False
        quote = " ".join(cursor.selectedText().split())
        self.assist_input.setText(f"「{quote}」{text}")
        self.assist_input.setFocus()
        self.assist_input.end(False)
        return True

    def _source_tooltip(self, position) -> str:
        """原文浮层里鼠标下的那个术语 → 「首选译名 · 点击检查器里的术语在全文定位」。"""
        from . import term_highlight

        cursor = self.inline_editor.source_view.cursorForPosition(position)
        offset = cursor.position()
        text = self.inline_editor.source_view.toPlainText()
        for term in self.term_list.terms:
            for start, end in term_highlight.spans(term.get("source"), text):
                if start <= offset <= end:
                    return i18n.tr('术语：{v0} → {v1}', v0=term.get("source"), v1=term.get("target"))
        return ""

    def _build_term_focus_bar(self) -> QWidget:
        from . import term_highlight

        bar = QFrame()
        bar.setObjectName("termFocusBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme_module.SPACE["sm"], 3, 3, 3)
        row.setSpacing(theme_module.SPACE["xs"])
        self.term_focus_label = QLabel("")
        self.term_focus_label.setTextFormat(Qt.TextFormat.PlainText)

        def tool(text: str, tip: str, slot) -> QToolButton:
            button = QToolButton()
            button.setObjectName("roundButton")
            button.setText(text)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(slot)
            return button

        self.term_prev_button = tool("↑", i18n.tr('上一处'), lambda: self.step_term_focus(-1))
        self.term_next_button = tool("↓", i18n.tr('下一处'), lambda: self.step_term_focus(1))
        self.term_close_button = tool("✕", i18n.tr('结束定位（Esc）'), self.clear_term_focus)
        row.addWidget(self.term_focus_label, 1)
        row.addWidget(self.term_prev_button)
        row.addWidget(self.term_next_button)
        row.addWidget(self.term_close_button)
        bar.setVisible(False)
        self.term_focus_bar = bar
        self.term_markers = term_highlight.ScrollMarkers(self.segment_list.verticalScrollBar())
        self.inline_editor.source_view.viewport().setMouseTracking(True)
        self.inline_editor.source_view.viewport().installEventFilter(self)
        self.inline_editor.source_view.installEventFilter(self)
        self.target_edit.textChanged.connect(self._decorate_current_row)
        return bar

    def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
        if (event.type() == QEvent.Type.KeyPress and watched is self.inline_editor.source_view
                and self._ask_about_selection(event)):
            return True
        if (event.type() == QEvent.Type.ToolTip
                and watched is self.inline_editor.source_view.viewport()):
            from PySide6.QtWidgets import QToolTip

            text = self._source_tooltip(event.pos())
            if text:
                QToolTip.showText(event.globalPos(), text, watched)
            else:
                QToolTip.hideText()
            return True
        return super().eventFilter(watched, event)

    def focus_term(self, term) -> None:
        """点击术语：网格里所有出现处高亮、滚动条标出位置，跳到当前段之后的第一处。"""
        from . import term_highlight

        if not term:
            return self.clear_term_focus()
        if self._term_focus is not None and self._term_focus.focus.source == term.get("source"):
            return self.step_term_focus(1)
        focus = term_highlight.Focus(str(term.get("source") or ""), str(term.get("target") or ""),
                                     tuple(term.get("forbidden") or ()))
        self._term_focus = term_highlight.occurrences(
            self._rows, focus, lambda row: row.draft_text if row.has_draft else row.target)
        self._apply_term_focus()
        current = self._active_position
        after = [pos for pos in self._term_focus.positions if pos > current]
        if after and current not in self._term_focus.positions:
            self._jump_occurrence(after[0])
        self._sync_term_focus_label()

    def _apply_term_focus(self) -> None:
        found = self._term_focus
        self.row_delegate.focus = found
        self.term_list.set_active(found.focus.source if found else "")
        was_visible = self.term_focus_bar.isVisible()
        self.term_focus_bar.setVisible(found is not None)
        if was_visible != (found is not None):
            # 定位条出现 / 消失会改变网格高度：当前编辑的行不能因此滑出视野。
            QTimer.singleShot(0, self._keep_active_row_visible)
        self._sync_escape()
        self._update_term_markers()
        self.segment_list.viewport().update()

    def _update_term_markers(self) -> None:
        found = self._term_focus
        if found is None:
            self.term_markers.set_marks([])
            return
        header = self.segment_list.verticalHeader()
        total = max(1, header.length())
        self.term_markers.set_marks([
            ((header.sectionPosition(pos) + header.sectionSize(pos) / 2) / total,
             "warning" if pos in found.inconsistent else "accent")
            for pos in found.positions])

    def _sync_term_focus_label(self) -> None:
        found = self._term_focus
        if found is None:
            return
        n = len(found)
        current = self._active_position
        where = (found.positions.index(current) + 1) if current in found.positions else 0
        parts = [f"{found.focus.source} → {found.focus.target}"]
        parts.append(i18n.tr('第 {v0} / {v1} 处', v0=where, v1=n) if where
                     else i18n.tr('共 {v0} 处', v0=n))
        if found.inconsistent:
            parts.append(i18n.tr('{v0} 处没用首选译名', v0=len(found.inconsistent)))
        if self._filters_active():
            parts.append(i18n.tr('（只在当前筛选结果里）'))
        self.term_focus_label.setText(" · ".join(parts))
        self.term_prev_button.setEnabled(n > 0)
        self.term_next_button.setEnabled(n > 0)

    def step_term_focus(self, step: int) -> None:
        found = self._term_focus
        if not found or not found.positions:
            return
        current = self._active_position
        if step > 0:
            later = [pos for pos in found.positions if pos > current]
            target = later[0] if later else found.positions[0]
        else:
            earlier = [pos for pos in found.positions if pos < current]
            target = earlier[-1] if earlier else found.positions[-1]
        self._jump_occurrence(target)
        self._sync_term_focus_label()

    def _jump_occurrence(self, position: int) -> None:
        if 0 <= position < len(self._rows):
            self.select_segment(self._rows[position].index)

    def _keep_active_row_visible(self) -> None:
        position = self._active_position
        if 0 <= position < self.segment_list.rowCount():
            self.segment_list.scrollToItem(self.segment_list.item(position, 0),
                                           QAbstractItemView.ScrollHint.EnsureVisible)
            self._place_inline_editor()

    def clear_term_focus(self) -> None:
        self._term_focus = None
        self._apply_term_focus()

    def _refresh_term_focus(self) -> None:
        """网格重画（筛选、保存、状态变化）之后按新的行重新找一遍。"""
        if self._term_focus is None:
            return
        term = {"source": self._term_focus.focus.source, "target": self._term_focus.focus.target,
                "forbidden": list(self._term_focus.focus.forbidden)}
        from . import term_highlight

        focus = term_highlight.Focus(term["source"], term["target"], tuple(term["forbidden"]))
        self._term_focus = term_highlight.occurrences(
            self._rows, focus, lambda row: row.draft_text if row.has_draft else row.target)
        self._apply_term_focus()
        self._sync_term_focus_label()

    def _toggle_followup_drawer(self) -> None:
        self.set_followup_drawer_open(not self.followup_drawer_open())

    # ---- 翻译页的列：原页 | 段落网格 | 检查器 / 检查与修复 ---------------------
    def source_open(self) -> bool:
        return self._source_open

    def set_source_open(self, open_: bool) -> None:
        """原页在工作台左侧展开 / 收起；中间的网格随之收缩（acceptance-feedback D2）。"""
        open_ = bool(open_) and self._pdf_ok
        self.source_button.setChecked(open_)
        if open_ == self.source_open():
            return
        if open_:
            self.tabs.setCurrentWidget(self._pages["translate"])
        self._source_open = open_
        self._source_locate_pending = open_ and self._current_index >= 0
        self._transition_columns()

    def _follow_source(self) -> None:
        if self.source_open() and self._current_index >= 0:
            if self._motion.running("translation-columns"):
                self._source_locate_pending = True
                return
            self._source_locate_pending = False
            self.pdf_panel.locate(self._current_index, state=self._state or None)

    def _resume_source_render(self) -> None:
        if getattr(self, "_source_locate_pending", False):
            self._follow_source()
        self.pdf_panel.set_render_deferred(False)

    def _remember_source_width(self, *_args) -> None:
        self._motion.stop("translation-columns")
        self.source_clip.freeze(None)
        self.source_clip.setVisible(self._source_open)
        self.right_panel.settle(self._drawer_open)
        self.followup_panel.set_preview_deferred(False)
        self._resume_source_render()
        if not self._drawer_open and self.right_panel.isVisibleTo(self):
            self._inspector_width = self.translation_split.sizes()[
                self.translation_split.indexOf(self.right_panel)]
        if self.source_open():
            self._pdf_width = self.translation_split.sizes()[
                self.translation_split.indexOf(self.source_clip)]

    def _layout_translation_columns(self) -> None:
        """按当前打开的面板分配宽度：原页约 40%（或用户拖过的宽度）、检查与修复
        约 440px、检查器保持原宽；窗口太窄时，原页打开会让检查器让位。"""
        split = self.translation_split
        self._motion.stop("translation-columns")
        drawer_open = self.followup_drawer_open()
        source = self.source_open()
        narrow = self.width() and self.width() < 1200
        self.source_clip.setVisible(source)
        self.source_clip.freeze(None)
        self.right_panel.settle(drawer_open)
        self.followup_panel.set_preview_deferred(False)
        self._resume_source_render()
        self.right_panel.setVisible(drawer_open or not (source and narrow))
        split.setSizes(self._translation_column_sizes())
        # The grid's columns are re-laid out after this returns; the inline
        # editor must follow them, or it hangs past the grid's new right edge.
        QTimer.singleShot(0, self._place_inline_editor)

    def _translation_column_sizes(self, *, drawer_open: bool | None = None) -> list[int]:
        """Compute the destination without laying out and then undoing a frame.

        ``drawer_open`` asks for the layout with the drawer open / closed
        regardless of its current state (the widths each side is laid out at).
        """
        split = self.translation_split
        if drawer_open is None:
            drawer_open = self.followup_drawer_open()
        source = self.source_open()
        sizes = split.sizes()
        total = sum(sizes) or split.width() or max(self.width() - 40, 800)
        index = {widget: split.indexOf(widget) for widget in (
            self.segment_list, self.right_panel)}
        index[self.pdf_panel] = split.indexOf(self.source_clip)
        side = 0
        if drawer_open:
            side = min(440, max(360, total // 3))
        elif not (source and self.width() < 1200):
            side = self._inspector_width
        pdf = 0
        if source:
            pdf = self._pdf_width or int(total * 0.4)
            pdf = max(320, min(pdf, total - side - 360))
        side = min(side, max(0, total - pdf - 360))
        new = [0] * split.count()
        new[index[self.pdf_panel]] = pdf
        new[index[self.right_panel]] = side
        new[index[self.segment_list]] = max(360, total - pdf - side)
        return new

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self._motion.running("translation-columns"):
            self._layout_translation_columns()
        if self.source_open():
            narrow = self.width() < 1200
            if self.inspector_scroll.isVisibleTo(self) == narrow and not self.followup_drawer_open():
                self._layout_translation_columns()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._motion.running("translation-columns"):
            self._layout_translation_columns()
        super().hideEvent(event)


    def current_page_id(self) -> str:
        widget = self.tabs.currentWidget()
        return next((key for key, page in self._pages.items() if page is widget), "")

    def _sync_stage_page(self) -> None:
        self.stage_bar.set_active_page(self.current_page_id())

    def _sync_more_pages(self) -> None:
        """"更多"菜单里的页面：随 PDF 来源与学术模式变化。收不收起由内容决定。"""
        pages: list[tuple[str, str]] = []
        if self.academic_visible():
            pages.extend((("report", i18n.tr('报告')), ("cases", i18n.tr('案例')),
                          ("qa", i18n.tr('合规与 QA'))))
        # 待处理数只挂在「检查与修复」图标上：挂在「更多」上会读成"运行记录 (2)"。
        self.stage_bar.set_more_pages(pages)

    def _render_stage(self) -> None:
        """用审校投影、交付预检与运行视图重算阶段步骤条与"下一步"。"""
        view = stage_model.stage_view(
            started=self._task_started(), runtime=self._last_runtime,
            state=self._state, review=self.review_panel.view(),
            delivery=self._delivery_preview, terms_blocked=self._terms_blocked)
        self.stage_bar.set_stages(view)
        # 术语与"更多"里的页面只对已经开始的任务有意义：准备阶段没有术语表、没有记录。
        self.stage_bar.set_side_visible(self._task_started())
        self._sync_stage_page()
        self._stage_action = view.get("action")
        action = self._stage_action
        self.stage_action_button.setVisible(bool(action))
        if action:
            self.stage_action_button.setText(str(action.get("label") or ""))
        self._sync_header_actions()

    def _run_stage_action(self) -> None:
        action = self._stage_action or {}
        kind = action.get("kind")
        if kind == "review_segments":
            self.reviewSelectedRequested.emit(list(action.get("segment_ids") or []))
        elif kind == "review_all":
            self.reviewAllRequested.emit()
        elif kind == "handle_finding":
            self.show_page("review")
            self.review_panel.select_item_id(str(action.get("item_id") or ""))
        elif kind == "deliver":
            self.deliverRequested.emit()

    # ================= 学术专业模式 =================
    def set_academic_mode(self, on: bool) -> None:
        self._academic_mode = bool(on)
        self._sync_academic_tabs()
        self._qa_key = ()
        self._cases_key = ()
        self._report_key = ()
        self._render_report()
        self._render_cases()
        self._render_qa()

    def academic_visible(self) -> bool:
        """打开了模式，或这个任务自己就带着学术产物（否则会被交付门禁卡住而无处下手）。"""
        return self._academic_mode or adapter.task_needs_academic(self._state or {})

    def _sync_academic_tabs(self) -> None:
        visible = self.academic_visible()
        for widget in self._academic_widgets:
            index = self.tabs.indexOf(widget)
            if index >= 0 and self.tabs.isTabVisible(index) != visible:
                self.tabs.setTabVisible(index, visible)
        self._sync_more_pages()

    def _render_report(self) -> None:
        if not self.job_id or not self.academic_visible():
            return
        # 报告页要读取运行视图与多份学术产物：随状态对象变化时才重算，
        # 并且只在它的页签可见时算（避免没有打开学术模式的人为它付出代价）。
        status = str((self._last_runtime or {}).get("status") or "")
        key = (self.job_id, id(self._state), status)
        if key == self._report_key:
            return
        self._report_key = key
        try:
            view = adapter.report_view(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 学术页坏了不能拖垮翻译页
            view = {}
        self.report_panel.apply(
            view, busy=status in ("running", "starting", "queued"),
            connection_ok=not (self.config.connection_problem() if self.config else "connection"))

    def _render_cases(self) -> None:
        if not self.job_id or not self.academic_visible():
            return
        key = (self.job_id, id(self._state))
        if key == self._cases_key:
            return
        self._cases_key = key
        try:
            views = adapter.case_views(self.job_id, self._state)
            truth = adapter.translation_truth(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 学术页坏了不能拖垮翻译页
            views, truth = [], {}
        self.cases_panel.apply(views, truth)

    def _render_qa(self) -> None:
        if not self.job_id or not self.academic_visible():
            return
        key = (self.job_id, id(self._state))
        if key == self._qa_key:
            return
        self._qa_key = key
        try:
            view = adapter.qa_view(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 学术页坏了不能拖垮翻译页
            view = {}
        self.qa_panel.apply(view)

    def _tab_changed(self, index: int) -> None:
        """打开"合规与 QA"页时，把 DOCX 结构检查的最新结论记下（和旧界面一样，写入只发生在这里）。"""
        if self.tabs.widget(index) is self.details_panel:
            self._render_details()
        if self.tabs.widget(index) is self.terms_panel and self.job_id:
            self._render_terms()
        if self.tabs.widget(index) is not self.qa_panel or not self.job_id:
            return
        try:
            view = adapter.qa_view(self.job_id, self._state)
            if view.get("sync_needed"):
                adapter.sync_structural_qa(self.job_id, view["structural_value"])
        except Exception:  # noqa: BLE001
            pass

    def _render_followup(self) -> None:
        """收尾页随任务状态对象重算（它要读检查点与实体登记，不放在每秒轮询里）。"""
        key = (self.job_id, id(self._state))
        if key == self._followup_key:
            return
        self._followup_key = key
        try:
            view = adapter.followup_view(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 收尾页坏了不能拖垮翻译页
            view = {}
        self.followup_panel.apply(view)
        # 顶栏图标与抽屉说同一件事：抽屉里有几张 ✕ / ⚠ 卡，角标就是几。
        count = self.followup_panel.issue_count()
        self._followup_count = count
        self.check_button.set_count(count, self.followup_panel.worst_tone() or "warning")
        self._sync_more_pages()

    def _locate_segment_from_followup(self, index: int) -> None:
        """「检查与修复」里点一个失败批次：回到翻译页并选中那一段（清掉会挡住它的筛选）。"""
        self.show_page("translate")
        if not self.select_segment(index):
            self.search_edit.clear()
            self.set_status_filter("全部")
            self.select_segment(index)

    def _render_details(self) -> None:
        """任务详情只在打开这一页、且状态对象换了时重算。

        翻译运行时状态每批都会换；这一页不在眼前就不算——它要读项目记忆、原文整理
        等好几样东西，放在每次刷新里会让整本书的工作台一卡一卡。
        """
        if not self.job_id or self.config is None:
            return
        if not self.details_popover.isVisible():
            return
        key = (self.job_id, id(self._state))
        if key == self._details_key:
            return
        self._details_key = key
        try:
            groups = adapter.task_details(self.config, self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 详情页坏了不能拖垮翻译页
            groups = []
        profile = dict(self._state.get("document_profile") or {})
        self.details_panel.apply(groups, profile, adapter.profile_summary(profile))
        try:
            facts = adapter.run_facts(self._state)
        except Exception:  # noqa: BLE001 - 用量/理解读不出只是少两张卡
            facts = {}
        self.details_panel.apply_run_facts(facts)

    def _render_activity(self) -> None:
        """活动记录按事件日志的签名重读：日志没变就不碰（轮询每秒一次）。"""
        if not self.job_id:
            return
        signature = f"{self.job_id}|{adapter.activity_signature(self.job_id)}"
        if signature == self.activity_panel.signature():
            return
        try:
            rows = adapter.activity_events(self.job_id)
        except Exception:  # noqa: BLE001 - 活动页坏了不能拖垮翻译页
            rows = []
        self.activity_panel.set_rows(rows[-50:], signature=signature)

    def _render_terms(self, *, force: bool = False) -> None:
        key = (self.job_id, id(self._state))
        if key == self._terms_key and not force:
            return
        # The full table counts every term's occurrences across the document
        # (~0.35 s on a book).  Off-page the panel gets the same view without
        # those counts (the start gate and layout stay current); the counts are
        # filled in when the page is shown (see _tab_changed).
        on_page = force or self.tabs.currentWidget() is self.terms_panel
        if on_page:
            self._terms_key = key
        try:
            view = adapter.glossary_view(self.job_id, self._state,
                                         count_occurrences=on_page)
        except Exception:  # noqa: BLE001 - 术语页坏了不能拖垮翻译页
            view = {}
        self.terms_panel.apply(self.job_id, view, force=force)
        self._sync_terms_gate(view)

    def _sync_terms_gate(self, view: dict | None = None) -> None:
        view = view if view is not None else self.terms_panel._view
        blocked = bool((view or {}).get("blocks_start")) and self._task_started()
        self._terms_blocked = blocked
        if not blocked:
            self.terms_gate.set_action("", None)
            self.terms_gate.set_text("")
            return
        self.terms_gate.set_text(i18n.tr(
            '严格术语治理：还有 {v0} 个候选术语未确认，翻译不会开始。冻结术语表后才能继续。',
            v0=(view or {}).get("pending_count", 0)))
        self.terms_gate.set_action(i18n.tr('前往术语页'), self.show_terms)

    def show_terms(self) -> None:
        self.show_page("terms")

    def refresh_terms(self) -> None:
        """显式动作（保存/冻结/修订）落盘之后：丢弃暂存并按磁盘重画。"""
        self._terms_key = ()
        self.terms_panel.mark_clean()
        self._render_terms(force=True)

    def _render_review(self) -> None:
        """审校页只在状态真的换了对象时重算（队列要过一遍原文质量门禁）。"""
        key = (self.job_id, id(self._state))
        if key == self._review_key:
            return
        self._review_key = key
        try:
            view = adapter.review_queue(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 审校页坏了不能拖垮翻译页
            view = {}
        self._review_items = list(view.get("queue_items") or [])
        self.review_panel.apply(view)

    def _load_pdf(self, job_id: str) -> None:
        """PDF 任务才有「原页」页签与「查看原位置」；切换任务时判断一次。"""
        try:
            self._pdf_ok = adapter.pdf_available(job_id)
        except Exception:  # noqa: BLE001 - 读不出原文时当作没有原页
            self._pdf_ok = False
        self.pdf_panel.load(job_id if self._pdf_ok else "")
        self.source_button.setVisible(self._pdf_ok)
        if not self._pdf_ok:
            self.set_source_open(False)
        self.view_source_button.setVisible(self._pdf_ok)
        self._sync_more_pages()

    def show_source_location(self, index: int) -> None:
        """切到原页并高亮第 ``index`` 段。"""
        if not self._pdf_ok or index < 0:
            return
        self.set_source_open(True)
        self.pdf_panel.locate(int(index), state=self._state or None)

    def show_segment_for_edit(self, index: int) -> None:
        """从审校页跳到翻译页的某一段（用户要自己改译文）。"""
        self.tabs.setCurrentIndex(0)
        self.select_segment(int(index))
        self.focus_editor()

    # ================= 过滤（复用核心语义） =================
    def set_status_filter(self, name: str) -> None:
        self._status_filter = name if name in ("全部", "待审校", "已审校") else "全部"
        for key, button in self.status_buttons.items():
            button.setChecked(key == self._status_filter)
        self._filters_changed()

    def set_search(self, text: str) -> None:
        self.search_edit.setText(text)

    def _filters_changed(self, *_args) -> None:
        QTimer.singleShot(0, self._sync_context_visibility)
        self._search = self.search_edit.text()
        self._only_edited = self.only_edited_box.isChecked()
        self._only_issues = self.only_issues_box.isChecked()
        active = sum(action.isChecked() for action in self.filter_actions.values())
        self.filter_button.setText(i18n.tr('筛选 · {v0}', v0=active) if active
                                   else i18n.tr('筛选'))
        # 筛过的列表一眼就能认出来：按钮整颗高亮，不只是菜单里打勾。
        self.filter_button.setProperty("active", "true" if active else "false")
        style_module.repolish(self.filter_button)
        self._render_segments()

    def clear_filters(self) -> None:
        for action in self.filter_actions.values():
            action.blockSignals(True)
            action.setChecked(False)
            action.blockSignals(False)
        self._filters_changed()

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
        visible = list(adapter.core.translation_visible_indexes(
            self._state, self._search,
            STATUS_FILTER_VALUES.get(self._status_filter, "全部"),
            filter_edited=self._only_edited, filter_issues=self._only_issues,
            filter_terms=self.filter_actions["terms"].isChecked(),
            filter_tm=self.filter_actions["tm"].isChecked(),
            issue_indexes=issues))
        # 核心只有一个"问题段"集合；另外两类问题在这里取交集。
        if self.filter_actions["source_quality"].isChecked():
            flagged = adapter.source_quality_indexes(self._state)
            visible = [index for index in visible if index in flagged]
        if self.filter_actions["term_issues"].isChecked():
            flagged = adapter.term_issue_indexes(self._state)
            visible = [index for index in visible if index in flagged]
        if self.filter_actions["notes"].isChecked():
            noted = {row.index for row in self._all_rows if row.segment_uid in self._notes}
            visible = [index for index in visible if index in noted]
        return visible

    def _visible_rows(self) -> list[adapter.SegmentRow]:
        visible = set(self.filtered_indexes())
        return [row for row in self._all_rows if row.index in visible]

    # ================= 渲染 =================
    def _task_started(self) -> bool:
        """任务是否已经开始（有段落 / p1 / p2 / 管线配置）。

        判据来自准备视图（``adapter.task_setup_view`` 的 ``started``）。没有准备
        视图时按"已开始"处理：旧的调用路径（测试、直接恢复）不该突然看到准备面。
        """
        if self._setup_view is None:
            return True
        return bool(self._setup_view.get("started"))

    def task_started(self) -> bool:
        return self._task_started()

    def set_setup(self, view: dict | None) -> None:
        """设置「译前准备」的数据（``adapter.task_setup_view``），并填进面板。"""
        self._setup_view = dict(view) if view else None
        if self._setup_view is not None:
            self.setup_panel.load(self._setup_view)

    def set_setup_choices(self, choices: dict) -> None:
        """填准备面板的目录（``adapter.setup_choices``）；进程内只需一次。"""
        self.setup_panel.set_choices(choices or {})

    def add_setup_project(self, project_id: str, name: str) -> None:
        """把新建的项目填进准备面板的选择器并选中（见 ``TaskSetupPanel.add_project``）。"""
        self.setup_panel.add_project(project_id, name)

    def _sync_segment_placeholder(self) -> None:
        """决定正文区放什么：译前准备 / 段落网格 / 空状态说明。

        三态互斥：
        * 任务未开始 → 「译前准备」面板（这是任务开始前唯一的决策面）；
        * 已开始且有段落 → 网格 + 检查器；
        * 已开始但没有段落（筛选为空、全被排除）→ 一段说明。

        判据用 `_all_rows`（总段数）而不是过滤后的 `_rows`：筛选结果为空时
        表格空着是**正确**的，不能把它说成"这份文档没有段落"。
        """
        started = self._task_started()
        has_rows = bool(self._all_rows)
        show_setup = not started
        was_setup = getattr(self, "_showing_setup", show_setup)
        self._showing_setup = show_setup
        self.setup_page.setVisible(show_setup)
        self.toolbar_bar.setVisible(started and has_rows)
        self.translation_split.setVisible(started and has_rows)
        self.no_segments_card.setVisible(started and not has_rows)
        if was_setup and started and has_rows:
            self._motion.reveal("translation-body", self.translation_split, kind="body")
        if show_setup or has_rows:
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
            self._painted.clear()  # 下面会换成全新的空单元格，旧缓存不再对应屏幕内容
            # 行全部重建：展开行要在选回当前段之后重新展开（位置可能变了）。
            self._active_position = -1
            self.row_delegate.active_row = -1
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
                # 选回的是同一格时 Qt 不发 currentCellChanged：展开行要自己补上。
                self._row_selected(target)
            else:
                self._activate_position(-1)
            self._sync_drafts_banner()
            self._sync_word_count()
            self._schedule_row_heights()
            self._refresh_term_focus()
            return
        for position, row in enumerate(self._rows):
            self._paint_row(position, row)
        if self._rows and self._current_index < 0:
            # Returning from the library can reuse exactly the same row count
            # and selection. Qt then emits no selection signal after load().
            target = max(0, min(self.segment_list.currentRow(), len(self._rows) - 1))
            self.segment_list.selectRow(target)
            self._row_selected(target)
        self._place_inline_editor()
        self._sync_drafts_banner()
        self._sync_word_count()
        self._schedule_row_heights()
        self._refresh_term_focus()

    def _sync_word_count(self) -> None:
        """整份文档的字数与进度（按原文计）。状态没换就不重算。"""
        from folith import word_count

        key = (self.job_id, id(self._state), len(self._all_rows))
        if key != self._word_count_key:
            self._word_count_key = key
            self._word_count = word_count.document_count(self._all_rows)
        count = self._word_count
        if count is None or not count.total.segments:
            self.word_total_label.setText("")
            self.word_bar.setVisible(False)
            return
        self.word_bar.setVisible(True)
        self.word_total_label.setText(i18n.tr(
            '原文 {v0} 词 · 已译 {v1}% · 已审校 {v2}%',
            v0=f"{count.total.words:,}", v1=count.percent(count.translated),
            v2=count.percent(count.reviewed)))
        self._sync_selection_count()

    def _sync_selection_count(self) -> None:
        """当前段（或所选多段）的词数：原文 → 译文。"""
        from folith import word_count

        if not hasattr(self, "word_selection_label"):
            return
        indexes = set(self.selected_indexes())
        if len(indexes) <= 1 and self._current_index >= 0:
            indexes = {self._current_index}
        rows = [row for row in self._all_rows if row.index in indexes]
        if not rows:
            self.word_selection_label.setText("")
            return
        data = word_count.selection_count(rows)
        head = (i18n.tr('所选 {v0} 段', v0=data["segments"]) if data["segments"] > 1
                else i18n.tr('第 {v0} 段', v0=rows[0].index + 1))
        self.word_selection_label.setText(i18n.tr(
            '{v0}：原文 {v1} 词 / {v2} 字符 → 译文 {v3} 词 / {v4} 字符',
            v0=head, v1=data["source_words"], v2=data["source_chars"],
            v3=data["target_words"], v4=data["target_chars"]))

    def _show_word_count(self) -> None:
        """字数浮窗：当前段 / 所选段在最上面，下面是整份文档的分项。"""
        from .dialogs import WordCountView

        self._sync_word_count()
        if self._word_count is None:
            return
        rows = [row for row in self._all_rows if row.index in set(self.selected_indexes())]
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["sm"])
        current = QLabel(self.word_selection_label.text())
        current.setWordWrap(True)
        layout.addWidget(current)
        layout.addWidget(WordCountView(self._word_count, rows), 1)
        popover = components.Popover(i18n.tr('字数统计'), content, self, size=QSize(560, 400))
        popover.closed.connect(popover.deleteLater)
        self._word_popover = popover
        popover.open_at(self.word_total_label)

    def _sync_drafts_banner(self) -> None:
        count = sum(1 for row in self._all_rows if row.has_draft)
        stale = sum(1 for row in self._all_rows if row.has_draft and row.stale_draft)
        text = i18n.tr('有 {v0} 段未保存的修改。', v0=count)
        if stale:
            text += i18n.tr('其中 {v0} 段基于旧译文，「全部保存」会跳过它们。', v0=stale)
        self.drafts_label.setText(text)
        self.drafts_banner.setVisible(count > 0)

    def _save_all_drafts(self) -> None:
        self.flush_draft()
        self.saveAllDraftsRequested.emit()

    def _discard_all_drafts(self) -> None:
        self.discardAllDraftsRequested.emit()

    def _paint_row(self, position: int, row: adapter.SegmentRow) -> None:
        look = (current_theme(), i18n.language())
        if look != self._painted_look:
            # 主题或语言变了：颜色与提示都要重画，缓存整体作废。
            self._painted.clear()
            self._painted_look = look
        if self._painted.get(position) == row:
            return
        tokens = theme_module.tokens(current_theme())
        tone = _segment_tone(row)
        number = self.segment_list.item(position, 0)
        source = self.segment_list.item(position, 1)
        target = self.segment_list.item(position, 2)
        if number is None or source is None or target is None:
            return
        self._painted[position] = dataclasses.replace(row)
        if row.segment_uid in self._notes:
            # 批注变了也要重画：缓存按行数据比较，批注不在行数据里。
            self._painted.pop(position, None)
        marker = ("⚠" if row.stale_draft else "●") if row.has_draft else (
            "✓ " if row.human_edited and row.index not in self._pending_saved else "")
        noted = "✎" if row.segment_uid in self._notes else ""
        number_text = f"{marker}{row.index + 1}{noted}"
        if number.text() != number_text:
            number.setText(number_text)
        number.setForeground(style_module.qcolor(_tone_color(tokens, tone)))
        number.setToolTip(i18n.tr_dynamic(row.status))
        # 展开行显示整段原文（委托负责换行）；其余行一行一段。
        source_text = str(row.source or "")
        if source.text() != source_text:
            source.setText(source_text)
        target_text = str((row.draft_text if row.has_draft else row.target) or "")
        untranslated = not target_text.strip()
        if untranslated:
            # 真正没有译文的段用占位说明，和已译文字一眼区分（第 5 条第 7 点）。
            target_text = i18n.tr('（未翻译）')
        if target.text() != target_text:
            target.setText(target_text)
        target.setForeground(style_module.qcolor(
            tokens["text-muted"] if untranslated
            else tokens["text-secondary"] if row.has_draft else tokens["text-primary"]))
        for column in range(3):
            item = self.segment_list.item(position, column)
            if item is not None:
                item.setData(Qt.ItemDataRole.UserRole, row.index)
                item.setData(Qt.ItemDataRole.UserRole + 1, row.segment_uid)

    def _render_runtime(self) -> None:
        runtime = self._last_runtime or {}
        raw_status = str(runtime.get("status") or "")
        headline = str(runtime.get("headline_status") or "")
        label = str(runtime.get("headline") or "")
        detail = str(runtime.get("detail") or "")
        started = self._task_started()
        running = raw_status in RUNNING_STATUSES
        troubled = raw_status in ("failed", "stalled", "interrupted")
        # 运行异常时让运行时说明整行转红。旧代码是把一个**从未填过文字**的空标签
        # 换 objectName 变色（`phase_label`），用户看不到任何东西；现在颜色落在
        # 真正有文字的那一行上。属性名用 `error` 而不是 `tone`（tone 是 chip 的
        # 全局选择器，见 style.py 的说明）。
        self.status_label.setProperty("error", "true" if troubled else "false")
        style_module.repolish(self.status_label)

        # 状态芯片只回答一件事：这个任务现在处于什么状态。
        # * 正在运行 / 出了问题：说运行的事（核心的运行视图）；
        # * 其余时候：用与任务列表同一个标签（`task_status_label`）。否则头部会说
        #   "已完成"（翻译跑完了），而交付页说"待审校"（审校没过）——同一个任务
        #   两个答案。
        task_label = str((self._delivery_preview or {}).get("status_label") or "")
        if started and not running and not troubled and task_label:
            chip_raw, chip_tone = task_label, status_tone_for_label(task_label)
        else:
            chip_raw, chip_tone = headline, RUNTIME_STATUS_TONES.get(headline, "muted")
        chip_text = i18n.tr_dynamic(chip_raw) or "—"
        # 状态芯片说了"已完成"，右边再写一遍"已完成"就是同一句话读两次：
        # 只有运行区带来的信息**超出**芯片时才显示这一行文字（按原文比较，
        # 显示时再翻译——两边都翻译后比较会因为词表不同而漏判）。
        note = detail or label or ""
        if note in (chip_raw, headline):
            note = ""
        note = i18n.tr_dynamic(note)

        # 任务还没开始：状态芯片、运行说明、进度与运行控制整组收掉。"未完成 · 当前
        # 进度已保存"对一个还没开始的任务是错的；「开始翻译」由准备面板承担，
        # 两个"开始翻译"并存会让用户不确定哪个才带上刚选的策略（面板里那个会
        # 先把选择写回任务再启动）。
        self.run_status.track.setVisible(started)
        self.run_status.percent.setVisible(started)
        header_visible = started
        self.chip_state.setVisible(header_visible)
        self.chip_state.setText(chip_text)
        self.chip_state.set_tone(chip_tone)
        self.run_status.setVisible(header_visible)
        if not started:
            notice = self._live_task_notice()
            if notice:
                self.run_status.setVisible(True)
                self.run_status.set_state(tone="idle", line=notice[1],
                                          line_tone="failed" if notice[2] else None)
            for button in (self.start_button, self.pause_button, self.cancel_button,
                           self.retry_button):
                button.setVisible(False)
            self._sync_header_actions()
            return

        # 进度条用核心自己的进度与它的单位（翻译阶段是"批次"），不另算一套。
        # `progress_completed/total` 只在学术写作阶段才有值；此前只读它，于是普通
        # 翻译运行期间进度条整段是空的、最后一下跳到满——不可信。
        stage_progress = runtime.get("stage_progress") or {}
        self.progress.set_stage((self.job_id, (runtime.get("runtime") or {}).get("stage")))
        completed = int(runtime.get("progress_completed") or 0)
        total = int(runtime.get("progress_total") or 0)
        if not total:
            total = int(stage_progress.get("total") or 0)
            completed = int(stage_progress.get("completed") or 0)
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(min(completed, total))
        elif running:
            # 这一步没有可数的进度（读取原文、识别画像、抽取术语、等模型回应）：
            # 用来回走动的忙碌条，而不是一根停在 0 的空条——后者看起来像卡死了。
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        tone, line = run_status_view(runtime, note=note, running=running,
                                     completed=completed, total=total,
                                     unit=str(stage_progress.get("unit") or ""))
        # 边识别边翻译：识别还在后台推进时如实说明（原先挤在步骤条里）。
        streaming = (self._state or {}).get("streaming_ingest") or {}
        if streaming.get("status") == "running" and streaming.get("pages_total"):
            recognised = i18n.tr("已识别 {v0}/{v1} 页", v0=int(streaming.get("pages_done") or 0),
                                 v1=int(streaming.get("pages_total") or 0))
            line = f"{line} · {recognised}" if line else recognised
        self.run_status.set_state(tone=tone, line=line)

        # 按钮可见性一律跟随核心的运行视图契约，不再自己猜标签：
        # * `available_actions` 是核心给出的可执行动作（resume/retry/cancel/…）；
        # * 暂停是管线能力（pause_requested），只在真正运行时出现；
        # * 停滞任务核心也提供 cancel，因此取消按钮必须跟着动作走——否则用户
        #   在"暂无运行信号"时反而看不到取消入口。
        actions = set(runtime.get("available_actions") or [])
        self.start_button.setVisible("resume" in actions)
        self.start_button.setText(
            "继续翻译" if self._state.get("p1_done") else "开始翻译")
        # 暂停只在"管线确实在跑"时出现，但不能只认 `running`：每次请求模型时状态会
        # 在 running ⇄ waiting_external 之间来回切换，按钮于是一闪一闪、时有时无。
        # 等待模型响应时请求暂停同样有效（当前请求结束后暂停）。已请求暂停时按钮
        # 留在原位、变成不可点的"正在暂停…"，而不是突然消失。
        pausing = raw_status == "pausing"
        self.pause_button.setVisible(raw_status in PAUSABLE_STATUSES or pausing)
        self.pause_button.setEnabled(not pausing)
        self.pause_button.setText(i18n.tr('正在暂停…') if pausing else i18n.tr('暂停'))
        self.cancel_button.setVisible(running or "cancel" in actions)
        self.retry_button.setVisible("retry" in actions)
        self.activity_panel.retry_button.setVisible("retry" in actions)
        notice = self._live_task_notice(runtime)
        if notice:
            self.run_status.setVisible(True)
            self.run_status.set_state(tone=tone, line=notice[1],
                                      line_tone="failed" if notice[2] else None)
        self._sync_header_actions()

    def _live_task_notice(self, runtime: dict | None = None):
        """还在有效期内、属于当前任务、且运行状态没变过的那条操作结果；否则清掉。"""
        notice = getattr(self, "_task_notice", None)
        if not notice:
            return None
        job_id, _message, error, fingerprint, at = notice
        expired = time.monotonic() - at > self.TASK_NOTICE_SECONDS[bool(error)]
        moved = runtime is not None and fingerprint != (
            runtime.get("status"), runtime.get("detail"), runtime.get("current_operation"))
        if job_id != self.job_id or expired or moved:
            self._task_notice = None
            return None
        return notice

    def _sync_header_actions(self) -> None:
        """运行控制整组收掉：没有可执行动作时不留一条空槽位。"""
        controls = (self.start_button, self.pause_button,
                    self.cancel_button, self.retry_button, self.stage_action_button)
        self.header_actions_bar.setVisible(
            any(not button.isHidden() for button in controls))

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
        preview = adapter.delivery_preview(self.job_id, self.config, state=self._state) \
            if self.job_id else {}
        self._delivery_preview = preview
        self._delivery_rendered = True
        self._delivery_preview_at = now
        self._delivery_key = key
        status = i18n.tr_dynamic(preview.get("status_label")) or "—"
        issues = preview.get("issues") or []
        detail = (i18n.tr("；还需处理：{v0}",
                          v0=_elide("；".join(i18n.tr_dynamic(str(i)) for i in issues), 200))
                  if issues
                  else i18n.tr("；交付文件按任务声明生成，冻结后不可修改。"))
        missing = [index for index, pair in enumerate(self._state.get("pairs") or [])
                   if not pair.get("excluded") and not str(pair.get("target") or "").strip()]
        if missing:
            numbers = "、".join(str(index + 1) for index in missing[:20])
            detail += i18n.tr("\n有 {v0} 段尚无译文：第 {v1}{v2} 段。", v0=len(missing), v1=numbers,
                              v2=i18n.tr(' 等') if len(missing) > 20 else "")
        self.delivery_label.setText(i18n.tr("交付状态：{v0}", v0=status) + detail)
        self.deliver_button.setEnabled(bool(preview.get("can_deliver")))
        self.risk_button.setVisible(bool(preview.get("can_accept_risk")))
        self.delivery_note.setEnabled(bool(preview.get("can_deliver")
                                           or preview.get("can_accept_risk")))
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
        # 补生成只在"交付已是当前版本"或"现在就能冻结"时出现：其余时候点了也会被
        # 门禁拒绝，不如不给这个入口。
        offer = bool(snapshot.get("current") or preview.get("can_deliver"))
        self._fill_supplements(adapter.supplementable_outputs(self.job_id, self._state)
                               if offer and self.job_id else [])

    def _fill_supplements(self, items: list[dict]) -> None:
        """按"还能补生成的文件"重建按钮（每秒刷新：清单没变就不动控件）。"""
        keys = tuple(item["key"] for item in items)
        if keys == self._supplement_keys:
            return
        self._supplement_keys = keys
        while self.supplement_grid.count():
            widget = self.supplement_grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # before detaching (see segment_assist._clear)
                widget.setParent(None)
                widget.deleteLater()
        self.supplement_buttons = {}
        from .setup_panel import _FORMAT_ICONS

        for index, item in enumerate(items):
            fmt = str(item.get("format") or "")
            title = i18n.tr(str(item.get("title") or item["key"]))
            button = QPushButton(i18n.tr('生成{v0}', v0=title)
                                 + (f"（{fmt}）" if fmt else ""))
            button.setIcon(icons.icon(_FORMAT_ICONS.get(fmt.upper(), "file-text"), 16))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(i18n.tr(str(item.get("detail") or "")))
            button.clicked.connect(
                lambda _checked=False, k=item["key"]: self.supplementRequested.emit(k))
            self.supplement_buttons[item["key"]] = button
            self.supplement_grid.addWidget(button, index // 3, index % 3)
        self.supplement_card.setVisible(bool(items))

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

    def _sync_structure_menu(self, row) -> None:
        """结构菜单：哪些操作此刻可用，由这一段和最近一次操作决定。"""
        pairs = self._state.get("pairs") or []
        has_pair = 0 <= row.index < len(pairs)
        editable = has_pair and not row.excluded
        empty = has_pair and not str(pairs[row.index].get("source") or "").strip() \
            and not str(pairs[row.index].get("target") or "").strip()
        has_next = has_pair and row.index + 1 < len(pairs)
        self._structure_actions["source_edit"].setEnabled(editable)
        self._structure_actions["split"].setEnabled(editable)
        self._structure_actions["merge"].setEnabled(editable and has_next)
        self._structure_actions["insert"].setEnabled(has_pair)
        self._structure_actions["delete"].setEnabled(bool(empty))
        undo = adapter.structure_undo_info(self._state)
        self.undo_structure_action.setEnabled(bool(undo.get("available")))
        self.undo_structure_action.setText(
            i18n.tr('撤销上一次结构操作：{v0}', v0=i18n.tr_dynamic(str(undo.get("label") or "")))
            if undo.get("available") else i18n.tr('撤销上一次结构操作'))
        self.structure_button.setEnabled(has_pair or bool(undo.get("available")))

    def _restore_excluded(self) -> None:
        item = self.excluded_list.currentItem()
        if item is None:
            return
        self.includeRequested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def _render_segment_detail(self) -> None:
        if not (0 <= self._current_index < len(self._all_rows)) or not self._rows:
            self.segment_status.setText(i18n.tr('尚未选择段落'))
            self.attention_list.set_items([], [])
            self.attention_label.setVisible(False)
            self.attention_list.setVisible(False)
            self.term_list.set_terms([])
            self.context_view.set_context({})
            self.submit_button.setEnabled(False)
            self.review_button.setEnabled(False)
            self.analyze_button.setEnabled(False)
            for label in (self.fact_state, self.fact_review, self.fact_source,
                          self.fact_terms, self.fact_excluded):
                label.setText("—")
            self.retranslate_button.setEnabled(False)
            self.exclude_button.setVisible(True)
            self.discard_draft_button.setVisible(False)
            self.restore_button.setVisible(False)
            self._activate_position(-1)
            self._sync_operation_panel()
            for action in self._structure_actions.values():
                action.setEnabled(False)
            undo = adapter.structure_undo_info(self._state)
            self.undo_structure_action.setEnabled(bool(undo.get("available")))
            self.structure_button.setEnabled(bool(undo.get("available")))
            return
        row = next((item for item in self._all_rows
                    if item.index == self._current_index), None)
        if row is None:
            return
        position = next((index for index, item in enumerate(self._rows)
                         if item.index == row.index), -1)
        self.submit_button.setEnabled(not row.excluded)
        self.copy_source_button.setEnabled(not row.excluded)
        self.review_button.setEnabled(not row.excluded)
        self.retranslate_button.setEnabled(not row.excluded)
        self.exclude_button.setVisible(not row.excluded)
        self._sync_structure_menu(row)
        self.pdf_panel.set_segment(row.index)
        if self.source_open():
            self._pdf_follow.start()  # follow the selection (debounced for key repeat)
        self.restore_button.setVisible(bool(row.human_edited) and not row.excluded)
        self._sync_inline_footer()
        self._sync_operation_panel()
        # 段号已经在操作栏标题里（"第 N 段"），这一行只说位置与状态。
        parts = [f"{position + 1}/{len(self._rows)}" if position >= 0 else "—",
                 row.status]
        if row.human_edited and row.status != "人工修改":
            parts.append("人工修改")
        if row.has_draft:
            parts.append("⚠ 草稿基于旧译文，提交会覆盖较新的文本"
                         if row.stale_draft else "有未提交草稿")
        self.segment_status.setText(" · ".join(parts))
        self.fact_state.setText(row.status)
        self.fact_review.setText("已审校" if row.reviewed else "未审校")
        pairs_now = self._state.get("pairs") or []
        from_tm = 0 <= row.index < len(pairs_now) and bool(pairs_now[row.index].get("from_tm"))
        self.fact_source.setText("人工修改" if row.human_edited
                                 else ("翻译记忆（已匹配并复用）" if from_tm else "机器翻译"))
        if from_tm and not row.human_edited:
            self.segment_status.setText(self.segment_status.text() + " · "
                                        + i18n.tr('来自翻译记忆'))
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
        open_count = sum(1 for item in all_findings if not item.get("resolved"))
        resolved_count = len(all_findings) - open_count
        # 可处置的检查项用审校队列里的同一份（带 finding_id / 建议译文 / 能否决定），
        # 这样「保留译文」「采用建议」与审校页走同一条路径。
        self._render_review()
        queue = [item for item in self._review_items
                 if item.get("kind") == "finding" and item.get("segment_index") == row.index]
        decisions = adapter.segment_decisions(self._state, row.index)
        self.attention_list.set_items(queue, decisions, resolved_count)
        # 「需要注意」只在真的有项时出现：标题下写"没有需要注意的地方"是自相矛盾。
        attention = len(queue) + len(decisions)
        self.attention_label.setText(i18n.tr('需要注意 · {v0}', v0=attention) if attention
                                     else i18n.tr('需要注意'))
        self.attention_label.setVisible(bool(attention))
        self.attention_list.setVisible(bool(attention))
        can_analyze = bool(str(row.target or "").strip()) and not row.excluded
        analyzing = self._analyzing_index == row.index
        self.analyze_button.setEnabled(can_analyze and not analyzing)
        self.analyze_button.setText(i18n.tr('点评中…') if analyzing else i18n.tr('点评'))
        terms = adapter.segment_terms(self._state, row.index)
        self.term_list.set_terms(terms, self._document_term_counts(terms))
        self.term_list.set_active(self._term_focus.focus.source if self._term_focus else "")
        self.term_heading.setText(i18n.tr('术语 · {v0}', v0=len(terms)) if terms
                                  else i18n.tr('术语'))
        self.context_view.set_context(adapter.segment_context(self._state, row.index))
        self._sync_context_visibility()
        self._decorate_current_row()
        if self._assist_index != row.index:
            self._assist_index = row.index
            self.note_box.set_note(self._notes.get(row.segment_uid))
            self._clear_highlights()
            self.ask_box.reset()
            self.suggest_box.reset()

    # ================= 选择 / 编辑器 =================
    def _position_of_loaded_segment(self) -> int | None:
        if not self._loaded_uid:
            return None
        for position, row in enumerate(self._rows):
            if row.segment_uid == self._loaded_uid:
                return position
        return None

    def _row_selected(self, position: int) -> None:
        # Restore the target's visible position after the previous editor row
        # regains its natural height AND the new editor loads its actual text.
        # Natural rows already show all text (P3b); animating their text layout
        # would add motion without helping the user locate the selection.
        table = self.segment_list
        anchor = table.visualRect(table.model().index(position, 1)).top() \
            if 0 <= position < table.rowCount() else -1
        try:
            self._load_selected_row(position)
        finally:
            if 0 <= anchor < table.viewport().height():
                after = table.visualRect(table.model().index(position, 1)).top()
                bar = table.verticalScrollBar()
                bar.setValue(bar.value() + after - anchor)
                self._place_inline_editor()

    def _load_selected_row(self, position: int) -> None:
        QTimer.singleShot(0, self._sync_term_focus_label)
        if position < 0 or position >= len(self._rows):
            self._activate_position(-1)
            return
        row = self._rows[position]
        self._activate_position(-1 if row.excluded else position)
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
        self._place_inline_editor()
        if self._loading:
            return
        self._dirty = True
        self._sync_inline_footer()
        self._autosave.start()

    # ---- 就地编辑：当前行展开、编辑框跟随 ----------------------------------
    def _activate_position(self, position: int) -> None:
        """把网格里的第 ``position`` 行设为展开的编辑行（-1 = 收起）。"""
        previous = self._active_position
        if previous == position:
            self._place_inline_editor()
            return
        self._active_position = position
        self.row_delegate.active_row = position
        if 0 <= previous < self.segment_list.rowCount():
            self.segment_list.setRowHeight(previous, self._natural_height(previous))
            self._painted.pop(previous, None)
            if previous < len(self._rows):
                self._paint_row(previous, self._rows[previous])
        if 0 <= position < len(self._rows):
            self._painted.pop(position, None)
            self._paint_row(position, self._rows[position])
        self._place_inline_editor()

    # ---- 自适应行高：每行按原文 / 译文中较长的一方换行后的高度 ------------------
    def _natural_height(self, position: int) -> int:
        """第 ``position`` 行全部展开时需要的高度（按当前列宽，结果有缓存）。"""
        if not (0 <= position < len(self._rows)):
            return segment_editor.ROW_HEIGHT
        row = self._rows[position]
        table = self.segment_list
        source = str(row.source or "")
        target = str((row.draft_text if row.has_draft else row.target) or "") or i18n.tr('（未翻译）')
        widths = (table.columnWidth(1), table.columnWidth(2))
        key = (source, target, widths)
        cached = self._height_cache.get(key)
        if cached is None:
            metrics = table.fontMetrics()
            tallest = 0
            for text, width in ((source, widths[0]), (target, widths[1])):
                rect = metrics.boundingRect(
                    QRect(0, 0, max(40, width - 2 * segment_editor.CELL_PAD - 10), 100000),
                    int(Qt.TextFlag.TextWordWrap), text or " ")
                tallest = max(tallest, rect.height())
            cached = max(segment_editor.ROW_HEIGHT, tallest + 2 * segment_editor.CELL_PAD + 4)
            self._height_cache[key] = cached
        return cached

    def _schedule_row_heights(self, *, reset: bool = False) -> None:
        """排队重算行高：先算眼前这一屏，再分批（每批 200 行）算其余的。

        一次算完 3 856 行要约 0.3 s，放在主线程一口气做会卡；分批做，每批几毫秒。
        """
        if reset:
            self._height_cache.clear()
        table = self.segment_list
        count = table.rowCount()
        first = max(0, table.rowAt(0))
        last = table.rowAt(table.viewport().height() - 1)
        last = count - 1 if last < 0 else last
        visible = list(range(first, min(count, last + 40)))
        rest = [p for p in range(count) if p < first or p >= last + 40]
        self._heights_pending = visible + rest
        if not self._height_timer.isActive():
            self._height_timer.start()

    def _apply_row_height_chunk(self) -> None:
        table = self.segment_list
        position = self._active_position
        anchor = table.visualRect(table.model().index(position, 1))
        keep_anchor = anchor.intersects(table.viewport().rect())
        batch, self._heights_pending = self._heights_pending[:200], self._heights_pending[200:]
        started = time.perf_counter()
        for offset, position in enumerate(batch):
            # Wrapping a book after a column transition must leave time for
            # input and paint events; 200 long paragraphs can take 10–30 ms.
            if offset and time.perf_counter() - started >= 0.004:
                self._heights_pending = batch[offset:] + self._heights_pending
                break
            if position == self._active_position or position >= table.rowCount():
                continue
            height = self._natural_height(position)
            if table.rowHeight(position) != height:
                table.setRowHeight(position, height)
        if not self._heights_pending:
            self._height_timer.stop()
            self._update_term_markers()
        self._place_inline_editor()
        if keep_anchor:
            after = table.visualRect(table.model().index(self._active_position, 1)).top()
            bar = table.verticalScrollBar()
            bar.setValue(bar.value() + after - anchor.top())
            self._place_inline_editor()

    def _columns_resized(self, index: int, _old: int, _new: int) -> None:
        if index in (1, 2):
            self._height_resize.start()

    def _place_inline_editor(self) -> None:
        """按当前行的单元格位置摆放编辑框，并让这一行刚好容下原文与译文。"""
        editor = self.inline_editor
        position = self._active_position
        if not (0 <= position < self.segment_list.rowCount()) or position >= len(self._rows):
            editor.hide_all()
            return
        table = self.segment_list
        source_width = table.columnWidth(1)
        target_width = table.columnWidth(2)
        footer = segment_editor.FOOTER_HEIGHT if editor.footer.isVisibleTo(editor) else 0
        cap = max(160, int(table.viewport().height() * 0.6))
        wanted = max(segment_editor.source_height(table, self._rows[position].source,
                                                  source_width),
                     editor.editor_height(target_width - 8) + footer + 8,
                     segment_editor.ROW_HEIGHT)
        height = min(wanted, cap)
        if table.rowHeight(position) != height:
            table.setRowHeight(position, height)
        rect = table.visualRect(table.model().index(position, 2))
        if not rect.isValid() or rect.bottom() < 0 or rect.top() > table.viewport().height():
            editor.hide_all()
            return
        editor.setGeometry(rect.adjusted(4, 4, -4, -4))
        source_rect = table.visualRect(table.model().index(position, 1))
        if editor.source_view.toPlainText() != (self._rows[position].source or ""):
            editor.set_source(self._rows[position].source)
            self._decorate_current_row()   # 浮层换了原文：术语标注跟着重排
        editor.source_view.setGeometry(source_rect.adjusted(
            segment_editor.CELL_PAD + 1, 2, -2, -2))
        from folith.textual import text_direction

        editor.source_view.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft
            if text_direction(self._rows[position].source, None) == "rtl"
            else Qt.LayoutDirection.LeftToRight)
        if not editor.source_view.isVisible():
            editor.source_view.show()
            editor.source_view.raise_()
        if not editor.isVisible():
            editor.show()
            editor.raise_()

    def _sync_inline_footer(self) -> None:
        """改过（或有草稿）才出现保存条；没改时编辑框下面什么都没有。"""
        row = next((item for item in self._all_rows
                    if item.index == self._current_index), None)
        has_draft = bool(row and row.has_draft)
        stale = bool(row and row.stale_draft)
        visible = bool(self._dirty or has_draft)
        if stale:
            text = i18n.tr('⚠ 草稿基于旧译文')
        elif visible:
            text = i18n.tr('● 未保存')
        else:
            text = ""
        self.inline_editor.state_label.setText(text)
        self.inline_editor.state_label.setProperty("error", "true" if stale else "false")
        style_module.repolish(self.inline_editor.state_label)
        self.discard_draft_button.setVisible(has_draft)
        was = self.inline_editor.footer.isVisibleTo(self.inline_editor)
        self.inline_editor.set_footer_visible(visible)
        if was != visible:
            self._place_inline_editor()

    def _commit_and_next(self) -> None:
        """⌘↩：改过就保存并进入下一段，没改就只是下一段。"""
        row = next((item for item in self._all_rows
                    if item.index == self._current_index), None)
        if self._dirty or (row is not None and row.has_draft):
            self.save_and_next()
        else:
            self._step(1)
        if self._active_position >= 0:
            self.target_edit.setFocus()

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

    def show_saved_locally(self, index: int, text: str) -> None:
        """Show a submitted edit in the grid before the background save lands.

        The window writes the edit off the GUI thread and refreshes from disk
        when it is done; until then the row shows what the user committed, so
        "save and next" moves on immediately instead of freezing on the write.
        The overlay survives the 1 s background refresh (which still reads the
        pre-save state) until :meth:`settle_saved` drops it.
        """
        self._pending_saved[index] = text
        self._all_rows = [self._with_pending_save(row) for row in self._all_rows]
        for position, row in enumerate(self._rows):
            if row.index == index:
                self._rows[position] = self._with_pending_save(row)
                self._painted.pop(position, None)
                self._paint_row(position, self._rows[position])

    def settle_saved(self, index: int) -> None:
        """The background save for ``index`` finished or failed: disk is the truth again."""
        self._pending_saved.pop(index, None)

    def confirm_saved(self, index: int) -> None:
        """Acknowledge persistence in the row, without changing editor text."""
        for position, row in enumerate(self._rows):
            if row.index == index:
                self._painted.pop(position, None)
                self._paint_row(position, row)
                break
        def tint(value):
            self.row_delegate.saved[index] = value
            self.segment_list.viewport().update()

        def finished():
            self.row_delegate.saved.pop(index, None)
            self.segment_list.viewport().update()

        self._motion.animate(f"saved:{index}", 1.0, 0.0, tint, kind="save", finished=finished)

    def _with_pending_save(self, row: adapter.SegmentRow) -> adapter.SegmentRow:
        text = self._pending_saved.get(row.index)
        if text is None:
            return row
        return dataclasses.replace(
            row, target=text, human_edited=True, has_draft=False, draft_text="",
            stale_draft=False, status=row.status if row.excluded else "人工修改")

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
        multiple = len(indexes) > 1
        self._sync_selection_count()
        self.review_selected_button.setEnabled(multiple)
        self.review_selected_button.setText(
            i18n.tr('审校所选 {v0} 段', v0=len(indexes)) if multiple
            else i18n.tr('审校所选段落'))
        self._sync_operation_panel()

    def _sync_operation_panel(self) -> None:
        """右侧操作栏跟着选区走：选一段 = 这一段的全部操作；选多段 = 只留批量审校。"""
        indexes = self.selected_indexes()
        multiple = len(indexes) > 1
        self.review_selected_button.setVisible(multiple)
        self.review_button.setVisible(not multiple)
        self.analyze_button.setVisible(not multiple)
        self.single_segment_section.setVisible(not multiple)
        self.segment_ops_section.setVisible(not multiple)
        self.segment_extra_section.setVisible(not multiple)
        self.assist_row.setVisible(not multiple)
        self.ask_box.setVisible(not multiple)
        self.suggest_box.setVisible(not multiple)
        self.segment_status.setVisible(not multiple)
        self.multi_hint.setVisible(multiple)
        for button in (self.retranslate_button, self.copy_source_button,
                       self.structure_button):
            button.setVisible(not multiple)
        if multiple:
            self.selection_title.setText(i18n.tr('已选 {v0} 段', v0=len(indexes)))
            self.exclude_button.setVisible(False)
            self.restore_button.setVisible(False)
            self.view_source_button.setVisible(False)
        elif 0 <= self._current_index:
            self.selection_title.setText(i18n.tr('第 {v0} 段', v0=self._current_index + 1))
            row = next((item for item in self._all_rows
                        if item.index == self._current_index), None)
            if row is not None:
                self.exclude_button.setVisible(not row.excluded)
                self.restore_button.setVisible(bool(row.human_edited) and not row.excluded)
            self.view_source_button.setVisible(bool(getattr(self, "_pdf_ok", False)))
        else:
            self.selection_title.setText(i18n.tr('尚未选择段落'))

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
            self._select_only(target)

    def _insert_into_target(self, text: str) -> None:
        """把术语译名插到译文光标处（只改编辑框，保存仍由用户决定）。"""
        if not text or self._active_position < 0:
            return
        self.target_edit.insertPlainText(text)
        self.target_edit.setFocus()

    def _request_term_add(self) -> None:
        """加入术语库：原文取原文里选中的词，译文取编辑框里选中的词（可留空再填）。"""
        source = self.inline_editor.source_view.textCursor().selectedText().strip()
        target = self.target_edit.textCursor().selectedText().strip()
        self.termAddRequested.emit(source, target)

    def _request_analysis(self) -> None:
        if self._current_index < 0:
            return
        self._analyzing_index = self._current_index
        self._render_segment_detail()
        self.analyzeRequested.emit(self._current_index)

    def analysis_finished(self, index: int, error: str = "") -> None:
        """窗口通知「进一步审视」结束；结果已写进状态，随刷新出现在需要注意里。"""
        if self._analyzing_index == index:
            self._analyzing_index = -1
        if index == self._current_index:
            self._render_segment_detail()
            if error:
                self.segment_status.setText(error)

    def _locate_decision(self, decision: dict) -> None:
        """把决策痕迹对应的原文 / 译文片段标出来（只是高亮，不改文字、不动光标）。"""
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        from PySide6.QtWidgets import QTextEdit

        tokens = theme_module.tokens(current_theme())
        fmt = QTextCharFormat()
        fmt.setBackground(style_module.qcolor(tokens["accent-soft"]))
        for widget, span in ((self.inline_editor.source_view, decision.get("source_span")),
                             (self.target_edit, decision.get("target_span"))):
            selections = []
            text = widget.toPlainText()
            start = text.find(str(span)) if span else -1
            if start >= 0:
                cursor = QTextCursor(widget.document())
                cursor.setPosition(start)
                cursor.setPosition(start + len(str(span)), QTextCursor.MoveMode.KeepAnchor)
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format = fmt
                selections.append(selection)
            self._decision_marks[id(widget)] = selections
        self._decorate_current_row()

    def _clear_highlights(self) -> None:
        """清掉决策痕迹的高亮；术语标注是这一段的常驻标注，保留。"""
        self._decision_marks = {}
        self._decorate_current_row()

    def _request_find_replace(self) -> None:
        self.flush_draft()
        self.findReplaceRequested.emit()

    def _notes_loaded_at_state(self, state: dict) -> bool:
        return getattr(self, "_notes_state", None) == id(state)

    def _save_note(self, text: str) -> None:
        if not self.job_id or not self._loaded_uid:
            return
        try:
            adapter.save_segment_note(self.job_id, self._loaded_uid, text)
        except OSError as exc:
            self.segment_status.setText(i18n.tr('批注保存失败：{v0}', v0=exc))
            return
        self._notes = adapter.segment_notes(self.job_id)
        self.note_box.set_note(self._notes.get(self._loaded_uid))
        self._painted.clear()
        self._render_segments()

    def set_suggestion(self, index: int, text: str, error: str = "") -> None:
        """窗口把「按要求重译」的候选交回来；用户已经换段时丢掉。"""
        if index == self._current_index:
            self.suggest_box.set_candidate(text, error=error)

    def _adopt_suggestion(self, text: str) -> None:
        """采用候选：放进编辑框（成为未保存的修改），保存仍由用户决定。"""
        if not text or self._active_position < 0:
            return
        self.target_edit.setPlainText(text)
        self.target_edit.setFocus()
        self.suggest_box.reset_candidate()

    def set_agent_answer(self, index: int, text: str) -> None:
        """窗口把「询问 Agent」的回答交回来；用户已经换段时丢掉。"""
        if index == self._current_index:
            self.ask_box.set_answer(text)

    def _select_only(self, position: int) -> None:
        """选中这一行、且只选它。

        扩展多选模式下 ``selectRow`` 会**追加**到现有选区：逐段前进时选区越滚越大，
        右侧操作栏就会误以为用户多选了几段。
        """
        self.segment_list.setCurrentCell(position, 0)
        self.segment_list.clearSelection()
        self.segment_list.selectRow(position)

    def _submit(self) -> None:
        if self._current_index < 0:
            return
        self.flush_draft()
        self.editSubmitted.emit(self._current_index)

    def save_and_next(self) -> None:
        """保存并进入下一个**还没确认**的段（没有就进入下一段）。

        连续校对要消灭的是未完成项，不是顺序推进：已审校、已人工确认的段直接跳过。
        """
        self._submit()
        target = self._next_unconfirmed_position()
        if target is None:
            self._step(1)
        else:
            self._select_only(target)

    def _next_unconfirmed_position(self) -> int | None:
        """当前段之后（到底再从头）第一个未确认的可见段：没有译文、待翻译 / 待审校，
        或仍有未解决的检查项。"""
        if not self._rows:
            return None
        position = next((index for index, row in enumerate(self._rows)
                         if row.index == self._current_index), -1)
        open_issues = {int(item["segment_index"])
                       for item in self._state.get("findings") or []
                       if isinstance(item, dict) and not item.get("resolved")
                       and item.get("segment_index") is not None
                       and item.get("severity") in ("blocking", "actionable")}
        order = list(range(position + 1, len(self._rows))) + list(range(0, max(position, 0)))
        for candidate in order:
            row = self._rows[candidate]
            if row.excluded:
                continue
            if (not str(row.target or "").strip() or row.status in ("待翻译", "待审校")
                    or row.index in open_issues):
                return candidate
        return None

    def focus_editor(self) -> None:
        self.target_edit.setFocus()

    # ---- 供测试与探针使用的稳定接口（不依赖具体控件类型） ----
    def visible_segment_count(self) -> int:
        return len(self._rows)

    def select_segment(self, index: int) -> bool:
        for position, row in enumerate(self._rows):
            if row.index == index:
                self._select_only(position)
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
