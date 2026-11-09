"""交付工作区：内容优先的交付成果页。

页面回答的问题不是"程序生成了哪些文件"，而是"我要的成果在哪、怎么拿到"。因此
信息架构按**内容类型**分组（译文 / 双语对照 / 术语表），格式只是组内的一行；
技术证据与清单收在默认折叠的"技术与追溯附件"里。

数据只有一份真值：冻结快照的 manifest。文件数、总大小、分组统计、格式标签都由
:func:`build_model` 从同一份资产列表派生，界面不单独硬编码任何统计。

组件与平台的约定沿用工作台其它页：配色/间距/字号全部取 ``theme`` 的 token，
图标取 ``icons``，文字一律经 ``i18n.tr``；不新增第二套设计语言。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from . import components, i18n, icons, platform_ui
from . import style as style_module
from . import theme as theme_module

# ================= 内容分类 =================

#: 内容分类（顺序即页面顺序）：key、图标名。标题与说明在 :func:`content_text` 里
#: **以字面量**出现在 ``i18n.tr(...)`` 中——两个 i18n 门禁（check_ui_locales /
#: qt_i18n_coverage）都按字面量扫描，藏在模块常量里再传进 tr 的文案它们看不见。
CONTENT_GROUPS: tuple[tuple[str, str], ...] = (
    ("translation", "translate"),
    ("bilingual", "segments"),
    ("terms", "termbase"),
)
TECHNICAL_KEY = "technical"


def content_text(key: str) -> tuple[str, str]:
    """内容分组的（标题，说明）。字面量必须留在 tr() 里，别挪进常量。"""
    if key == "translation":
        return i18n.tr("译文"), i18n.tr("翻译后的完整文档")
    if key == "bilingual":
        return i18n.tr("双语对照"), i18n.tr("原文与译文并列展示")
    return i18n.tr("术语表"), i18n.tr("术语及其统一译法")


def technical_text() -> tuple[str, str]:
    """技术附件的（标题，说明）。"""
    return (i18n.tr("技术与追溯附件"),
            i18n.tr("翻译证据、交付清单、导出报告等"))

#: 已知交付文件 -> 内容分类。未列出的文件（含将来新增的格式）归入技术附件。
#: 语义：同一分类可以有多份文件、多种格式；分类由**成果**决定，不由扩展名决定。
ASSET_GROUPS: dict[str, str] = {
    "translation.docx": "translation",
    "translation.pdf": "translation",
    "translation.epub": "translation",
    "original_format.docx": "translation",
    "original_layout.pdf": "translation",
    "bilingual.docx": "bilingual",
    "annotated_bilingual.docx": "bilingual",
    "stage2_bilingual.docx": "bilingual",
    "terms.xlsx": "terms",
    "terms.tbx": "terms",
    "auto_terms.xlsx": "terms",
}

#: 没有登记的文件按文件名前缀兜底：``translation_*.docx`` 仍属于译文。
_PREFIX_GROUPS: tuple[tuple[str, str], ...] = (
    ("translation", "translation"),
    ("bilingual", "bilingual"),
    ("terms", "terms"),
    ("termbase", "terms"),
)

#: 扩展名 -> 行首图标。未知扩展名用 ``file``。
SUFFIX_ICONS: dict[str, str] = {
    ".docx": "file-text", ".doc": "file-text", ".pdf": "file-text",
    ".epub": "file-text", ".txt": "file-text", ".md": "file-text",
    ".markdown": "file-text",
    ".xlsx": "file-sheet", ".xls": "file-sheet", ".csv": "file-sheet",
    ".tbx": "file-sheet", ".tmx": "file-sheet",
    ".json": "file-code", ".jsonl": "file-code", ".xml": "file-code",
    ".zip": "package",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
}

#: 格式标签的语义色调（QSS 里按 ``format`` 属性取底色，不靠单一蓝色区分）。
FORMAT_TONES: dict[str, str] = {
    "DOCX": "accent", "PDF": "warning", "EPUB": "info", "XLSX": "success",
    "TBX": "success", "TMX": "info", "JSON": "muted", "JSONL": "muted",
    "ZIP": "muted", "MD": "muted", "CSV": "success",
}


def format_label(name: str) -> str:
    """文件名 -> 大写的格式标签（``translation.docx`` → ``DOCX``）。"""
    base = str(name or "").rsplit("/", 1)[-1]
    if "." not in base or base.startswith("."):
        return "FILE"
    return base.rsplit(".", 1)[-1].upper()[:8] or "FILE"


def asset_group(name: str) -> tuple[str, str]:
    """文件名 -> (内容分类 key, 图标名)。未知文件一律进技术附件。"""
    base = str(name or "").rsplit("/", 1)[-1]
    group = ASSET_GROUPS.get(base)
    if group is None:
        lowered = base.lower()
        group = next((key for prefix, key in _PREFIX_GROUPS
                      if lowered.startswith(prefix)), TECHNICAL_KEY)
    icon = "file"
    lowered = base.lower()
    for suffix, candidate in SUFFIX_ICONS.items():
        if lowered.endswith(suffix):
            icon = candidate
            break
    return group, icon


def human_size(size) -> str:
    """字节数 -> ``36.5 KB``。取不到大小就返回 ``—``，不猜一个数。

    与界面其它地方（``views._human_size``）同一套换算与舍入：顶部合计与分组统计
    走同一个函数，避免"上面 80.7、下面加起来 84.0"这类矛盾。
    """
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


def _list_separator() -> str:
    """文件名列表的连接符：中文用顿号，其它语言用逗号（不把中文标点漏出去）。"""
    return "、" if i18n.language().startswith("zh") else ", "


def count_label(count: int) -> str:
    return i18n.tr("{v0} 个文件", v0=int(count))


def size_label(total: int | None, count: int) -> str:
    """``3 个文件 · 6.5 KB``；大小未知时不编一个数。"""
    text = count_label(count)
    return f"{text} · {human_size(total)}" if total is not None else text


# ================= 数据模型 =================


@dataclass(frozen=True)
class FileEntry:
    """一个交付文件行。``path`` 为空表示无法解析（任务未加载或快照缺失）。"""

    name: str
    group: str
    icon: str
    format: str
    size: int | None = None
    sha256: str = ""
    missing: bool = False
    path: str = ""

    @property
    def size_text(self) -> str:
        return i18n.tr('文件缺失') if self.missing else human_size(self.size)


@dataclass(frozen=True)
class GroupEntry:
    key: str
    title: str
    hint: str
    icon: str
    files: tuple[FileEntry, ...] = ()

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def total_size(self) -> int | None:
        sizes = [item.size for item in self.files if isinstance(item.size, (int, float))]
        return int(sum(sizes)) if len(sizes) == len(self.files) and self.files else None

    @property
    def meta(self) -> str:
        return size_label(self.total_size, self.count)


@dataclass(frozen=True)
class DeliveryModel:
    """页面渲染所需的全部展示数据，唯一来源是冻结快照的 manifest。"""

    frozen: bool = False
    version: int | None = None
    created_at: str = ""
    frozen_at: str = ""
    source_name: str = ""
    groups: tuple[GroupEntry, ...] = ()
    technical: GroupEntry | None = None
    integrity: bool = True

    @property
    def all_files(self) -> tuple[FileEntry, ...]:
        files = [item for group in self.groups for item in group.files]
        if self.technical is not None:
            files.extend(self.technical.files)
        return tuple(files)

    @property
    def file_count(self) -> int:
        return len(self.all_files)

    @property
    def total_size(self) -> int | None:
        files = self.all_files
        sizes = [item.size for item in files if isinstance(item.size, (int, float))]
        return int(sum(sizes)) if files and len(sizes) == len(files) else None

    @property
    def missing(self) -> tuple[FileEntry, ...]:
        return tuple(item for item in self.all_files if item.missing)

    def signature(self) -> tuple:
        """内容签名：只有它变了才重建控件树（后台每秒刷新，重建会产生 AX churn）。"""
        return (self.frozen, self.version, self.created_at, self.source_name,
                self.integrity,
                tuple((item.name, item.size, item.sha256, item.missing, item.path)
                      for item in self.all_files))


def _entry(asset: dict, *, group: str, icon: str, resolve) -> FileEntry:
    name = str(asset.get("name") or "")
    size = asset.get("size")
    path = ""
    missing = False
    if resolve is not None:
        try:
            resolved = resolve(name)
        except Exception:  # noqa: BLE001 - 读不到快照目录就按缺失显示
            resolved = None
        if resolved is not None:
            path = str(resolved)
            missing = not getattr(resolved, "is_file", lambda: False)()
    return FileEntry(
        name=name, group=group, icon=icon, format=format_label(name),
        size=int(size) if isinstance(size, (int, float)) else None,
        sha256=str(asset.get("sha256") or ""), missing=missing, path=path)


def build_model(latest: dict | None, *, resolve=None, source_name: str = "",
                integrity: bool = True) -> DeliveryModel:
    """manifest -> 页面模型。``resolve`` 由调用方注入（快照资产路径），纯函数、可单测。"""
    latest = latest or {}
    assets = [item for item in (latest.get("assets") or [])
              if str(item.get("name") or "")]
    created_at = str(latest.get("created_at") or "")
    if not assets:
        return DeliveryModel(frozen=False, source_name=source_name,
                             created_at=created_at, integrity=bool(integrity))
    grouped: dict[str, list[FileEntry]] = {}
    for asset in assets:
        group, icon = asset_group(str(asset.get("name")))
        grouped.setdefault(group, []).append(
            _entry(asset, group=group, icon=icon, resolve=resolve))
    ordered: list[GroupEntry] = []
    for key, icon in CONTENT_GROUPS:
        files = grouped.get(key) or []
        if not files:
            # 没有实际文件的分类不出现：不留空卡片，也不为了填满界面造文件。
            continue
        title, hint = content_text(key)
        ordered.append(GroupEntry(key=key, title=title, hint=hint,
                                  icon=icon, files=tuple(files)))
    technical_files = grouped.get(TECHNICAL_KEY) or []
    technical_title, technical_hint = technical_text()
    technical = (GroupEntry(key=TECHNICAL_KEY, title=technical_title,
                            hint=technical_hint, icon="package",
                            files=tuple(technical_files))
                 if technical_files else None)
    return DeliveryModel(
        frozen=True, version=latest.get("snapshot_version"), created_at=created_at,
        frozen_at=local_stamp(created_at),
        source_name=source_name, groups=tuple(ordered), technical=technical,
        integrity=bool(integrity))


def local_stamp(value: str) -> str:
    """快照 ``created_at``（UTC ISO）-> 本地 ``YYYY-MM-DD HH:MM``；解析不了就原样。"""
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


# ================= 视觉积木 =================


class IconTile(QFrame):
    """分组图标：浅色圆角方块 + 语义色图标（不靠文字说"这是什么"）。"""

    SIZE = 44

    def __init__(self, name: str, tone: str = "accent", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deliveryTile")
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setProperty("tone", tone)
        self._icon = QLabel(self)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._icon)
        self.set_icon(name)

    def set_icon(self, name: str) -> None:
        self._icon.setPixmap(icons.pixmap(name, 22, self._icon_token()))

    def _icon_token(self) -> str:
        return {"translation": "accent", "bilingual": "accent",
                "terms": "success", "technical": "text-secondary"}.get(
                    str(self.property("tone")), "accent")

    def set_tone(self, tone: str) -> None:
        self.setProperty("tone", tone)
        style_module.repolish(self)


class FrozenPill(QFrame):
    """冻结版本芯片：图标 + 「已冻结 · v1」。颜色之外还有图标与文字，不只靠颜色表达状态。"""

    def __init__(self, text: str = "", tone: str = "success",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("frozenPill")
        self.setProperty("tone", tone)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["sm"], 2,
                                  theme_module.SPACE["md"], 2)
        layout.setSpacing(theme_module.SPACE["xs"])
        self.icon_label = QLabel(self)
        self.icon_label.setPixmap(icons.pixmap("check-circle", 14, tone))
        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("frozenPillText")
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text_label, 0, Qt.AlignmentFlag.AlignVCenter)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self.text_label.setText(text)
        self.setAccessibleName(text)
        # 芯片是 QFrame（不是 QLabel）：内部文字变了要显式告诉父布局重新量宽，
        # 否则窄窗口下会停在"空文本"时的宽度，只剩一个被裁掉的「已」。
        self.updateGeometry()

    def text(self) -> str:
        return self.text_label.text()


class FormatBadge(QLabel):
    """文件格式标签（DOCX / XLSX / JSONL…）：小圆角，按语义取色。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("formatBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_format(text)

    def set_format(self, text: str) -> None:
        value = str(text or "FILE").upper()
        self.setText(value)
        tone = FORMAT_TONES.get(value, "muted")
        if self.property("format") != tone:
            self.setProperty("format", tone)
            style_module.repolish(self)


class _RowIcon(QWidget):
    """行首文件类型图标（纯展示，不接收鼠标事件）。"""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label = QLabel(self)
        self._label.setPixmap(icons.pixmap(name, 16, "text-secondary"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label, 0, Qt.AlignmentFlag.AlignCenter)


class DeliveryFileRow(QFrame):
    """一个交付文件行：图标 + 文件名 + 格式 + 大小 + 打开 / 更多。

    * 点击「打开」用系统默认应用打开；「更多」菜单里放低频操作（在文件夹中显示、
      另存副本、复制路径、文件信息），它们各有各的图标与文字，不共用模糊的省略号。
    * 文件在磁盘上缺失时，大小位置写"文件缺失"、打开与另存变灰并在提示里说明原因；
      「查看文件信息」「复制路径」仍可用——错误要能解释，而不是整行消失。
    """

    ROW_HEIGHT = 48
    SIZE_WIDTH = 72

    openRequested = Signal(str)
    revealRequested = Signal(str)
    saveCopyRequested = Signal(str)
    copyPathRequested = Signal(str)
    infoRequested = Signal(str)

    def __init__(self, entry: FileEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setObjectName("deliveryFileRow")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["sm"], theme_module.SPACE["xxs"],
                                  theme_module.SPACE["xxs"], theme_module.SPACE["xxs"])
        layout.setSpacing(theme_module.SPACE["xs"] + 2)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.icon = _RowIcon(entry.icon)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self.name_label = style_module.ElideLabel(
            entry.name, mode=Qt.TextElideMode.ElideMiddle)
        self.name_label.setObjectName("deliveryFileName")
        self.name_label.setToolTip(self._name_tooltip())
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                      QSizePolicy.Policy.Preferred)
        layout.addWidget(self.name_label, 1)

        self.badge = FormatBadge(entry.format)
        self.badge.setFixedWidth(54)
        layout.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)

        # 大小列：普通 QLabel + 由工作区按当前语言量出的固定宽（见 _sync_size_column）。
        # 不用 ElideLabel 的原因：它在 resizeEvent 里改写文本会触发 updateGeometry，
        # 布局过程中被反复失效时 Qt 会把后面的按钮叠到这一列上（实测英文界面重叠）。
        self.size_label = QLabel(entry.size_text)
        self.size_label.setObjectName("deliveryFileSize")
        self.size_label.setProperty("missing", "true" if entry.missing else "false")
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self.size_label.setFixedWidth(self.SIZE_WIDTH)
        self.size_label.setToolTip(entry.size_text)
        layout.addWidget(self.size_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.open_button = style_module.icon_button(
            "external-link", self._open_tooltip(), parent=self)
        self.open_button.setAccessibleName(i18n.tr('打开 {v0}', v0=entry.name))
        self.open_button.setToolTip(self._open_tooltip())
        self.open_button.clicked.connect(
            lambda: self.openRequested.emit(self.entry.name))
        layout.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignVCenter)

        # 「更多」用 QToolButton + InstantPopup：键盘（Space/Enter）也能打开菜单，
        # 普通 QPushButton 挂菜单时按回车只会触发 clicked，菜单打不开。
        self.more_button = QToolButton(self)
        self.more_button.setObjectName("iconButton")
        self.more_button.setIcon(icons.icon("more", 16, "text-secondary"))
        self.more_button.setIconSize(QSize(16, 16))
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setToolTip(i18n.tr('更多操作'))
        self.more_button.setAccessibleName(i18n.tr('{v0} 的更多操作', v0=entry.name))
        self.more_button.setMenu(self._build_menu())
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignVCenter)

        if entry.missing:
            self.name_label.setProperty("missing", "true")
            style_module.repolish(self.name_label)
            self.open_button.setEnabled(False)
        self.setAccessibleName(i18n.tr(
            "文件 {v0}，{v1}，{v2}", v0=entry.name, v1=entry.format,
            v2=entry.size_text))
        self.setFixedHeight(self.ROW_HEIGHT)

    # ---- 行为 ----
    def _name_tooltip(self) -> str:
        if self.entry.missing:
            return i18n.tr(
                "{v0}\n文件在磁盘上找不到；冻结快照不可修改，请重新冻结交付或从备份恢复。",
                v0=self.entry.name)
        return i18n.tr("{v0}\n大小：{v1}", v0=self.entry.name,
                       v1=human_size(self.entry.size))

    def _open_tooltip(self) -> str:
        if self.entry.missing:
            return i18n.tr('文件在磁盘上找不到，无法打开')
        return i18n.tr('用默认应用打开')

    def _build_menu(self) -> QMenu:
        menu = QMenu(self.more_button)
        open_action = menu.addAction(icons.icon("external-link", 16, "text-secondary"),
                                     i18n.tr('打开文件'))
        open_action.setEnabled(not self.entry.missing)
        open_action.triggered.connect(lambda: self.openRequested.emit(self.entry.name))
        reveal = menu.addAction(icons.icon("folder", 16, "text-secondary"),
                                i18n.tr('在{v0}中显示', v0=platform_ui.file_manager_name()))
        reveal.setEnabled(not self.entry.missing)
        reveal.triggered.connect(lambda: self.revealRequested.emit(self.entry.name))
        menu.addSeparator()
        save = menu.addAction(icons.icon("save", 16, "text-secondary"),
                              i18n.tr('另存文件副本…'))
        save.setEnabled(not self.entry.missing)
        save.triggered.connect(lambda: self.saveCopyRequested.emit(self.entry.name))
        copy_path = menu.addAction(icons.icon("copy", 16, "text-secondary"),
                                   i18n.tr('复制文件路径'))
        copy_path.triggered.connect(lambda: self.copyPathRequested.emit(self.entry.name))
        menu.addSeparator()
        info = menu.addAction(icons.icon("info", 16, "text-secondary"),
                              i18n.tr('查看文件信息…'))
        info.triggered.connect(lambda: self.infoRequested.emit(self.entry.name))
        return menu

    # ---- 悬停 ----
    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_hover(False)
        super().leaveEvent(event)

    def _set_hover(self, on: bool) -> None:
        if self.property("hover") == ("true" if on else "false"):
            return
        self.setProperty("hover", "true" if on else "false")
        style_module.repolish(self)


def _hairline(parent: QWidget | None = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("deliveryHairline")
    line.setFixedHeight(1)
    return line


class DeliveryGroupCard(QFrame):
    """内容分组卡片：左侧成果身份（图标 + 标题 + 说明），右侧文件行（可多种格式）。

    只有一个文件时就是参考图里的一行；同一成果将来有 DOCX / PDF / EPUB 时，
    文件行在同一张卡片里向下排，用发丝线分隔，不重复标题。
    """

    def __init__(self, group: GroupEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.group = group
        self.setObjectName("deliveryGroup")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                  theme_module.SPACE["sm"], theme_module.SPACE["md"])
        self.setMinimumHeight(84)
        layout.setSpacing(theme_module.SPACE["lg"])

        identity = QWidget(self)
        identity.setFixedWidth(212)
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(0, theme_module.SPACE["xs"], 0, theme_module.SPACE["xs"])
        identity_layout.setSpacing(theme_module.SPACE["md"])
        self.tile = IconTile(group.icon, self._tone(group.key), identity)
        identity_layout.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignTop)
        # 先把子布局挂到父布局上再往里放控件：先填后挂会让 PySide 给每个条目留下
        # 一个 QWidgetItem 包装器，卡片重建后包装器残留，之后同地址的新对象会被
        # 误认成它而段错误（tests/qt_layout_item_wrapper_test.py）。
        text_column = QVBoxLayout()
        identity_layout.addLayout(text_column, 1)
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(theme_module.SPACE["xxs"])
        self.title_label = QLabel(group.title, identity)
        self.title_label.setObjectName("deliveryGroupTitle")
        # 身份列只有 212 px；"Bilingual document / Documento bilingüe /
        # Двуязычный документ" 一比一翻译就比这一列宽（S3-03）。标题折行而不是
        # 被硬裁，完整文字同时进悬停提示与无障碍名称。
        self.title_label.setWordWrap(True)
        self.title_label.setToolTip(group.title)
        self.title_label.setAccessibleName(group.title)
        self.hint_label = QLabel(group.hint, identity)
        self.hint_label.setObjectName("deliveryGroupHint")
        self.hint_label.setWordWrap(True)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.hint_label)
        text_column.addStretch(1)
        layout.addWidget(identity, 0, Qt.AlignmentFlag.AlignTop)

        divider = QFrame(self)
        divider.setObjectName("deliveryDivider")
        divider.setFixedWidth(1)
        layout.addWidget(divider, 0)

        files = QWidget(self)
        files_layout = QVBoxLayout(files)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)
        self.rows: list[DeliveryFileRow] = []
        for index, entry in enumerate(group.files):
            if index:
                files_layout.addWidget(_hairline(files))
            row = DeliveryFileRow(entry, files)
            self.rows.append(row)
            files_layout.addWidget(row, 0)
        files_layout.addStretch(1)
        layout.addWidget(files, 1)

    @staticmethod
    def _tone(key: str) -> str:
        return {"translation": "translation", "bilingual": "bilingual",
                "terms": "terms", "technical": "technical"}.get(key, "translation")


class TechnicalAttachments(QFrame):
    """技术与追溯附件：低视觉权重的折叠容器，默认收起。

    折叠头的整条都可点（鼠标与键盘 Space/Enter），右侧给出文件数与大小；展开后
    复用普通文件行，不另做一套行。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deliveryTechnical")
        self._open = False
        self._group: GroupEntry | None = None
        self._rows: list[DeliveryFileRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = _DisclosureHeader(self)
        self.header.toggled.connect(self.set_open)
        layout.addWidget(self.header)

        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(theme_module.SPACE["lg"], 0,
                                            theme_module.SPACE["lg"],
                                            theme_module.SPACE["sm"])
        self.body_layout.setSpacing(0)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self.rows: list[DeliveryFileRow] = self._rows

    def set_group(self, group: GroupEntry | None) -> None:
        self._group = group
        if group is None:
            # 没有技术附件时把行真正清掉：只 setVisible(False) 会让旧行留在树里，
            # file_rows()/row_for() 于是把上一个任务的文件当成当前交付的一部分。
            self._clear_rows()
            self.setVisible(False)
            return
        self.setVisible(True)
        self.header.set_content(group.title, group.hint, group.meta, group.icon)
        self.header.set_open(self._open)
        signature = tuple(
            (item.name, item.size, item.sha256, item.missing, item.path)
            for item in group.files)
        if getattr(self, "_signature", None) == signature:
            return
        self._clear_rows()
        for index, entry in enumerate(group.files):
            if index:
                self.body_layout.addWidget(_hairline(self.body))
            row = DeliveryFileRow(entry, self.body)
            self._rows.append(row)
            self.body_layout.addWidget(row, 0)
        self.rows = self._rows
        self._signature = signature

    def _clear_rows(self) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        self.rows = self._rows
        self._signature = None

    def set_open(self, open_state: bool) -> None:
        self._open = bool(open_state)
        self.header.set_open(self._open)
        self.body.setVisible(self._open)
        self.setProperty("open", "true" if self._open else "false")
        style_module.repolish(self)

    def is_open(self) -> bool:
        return self._open


class _DisclosureHeader(QFrame):
    """折叠头的交互区：整条可点、可聚焦，Space/Enter 切换。"""

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deliveryTechnicalHeader")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["sm"],
                                  theme_module.SPACE["md"], theme_module.SPACE["sm"])
        layout.setSpacing(theme_module.SPACE["md"])
        self.tile = IconTile("package", "technical", self)
        layout.addWidget(self.tile, 0, Qt.AlignmentFlag.AlignVCenter)
        text_column = QVBoxLayout()
        layout.addLayout(text_column, 1)
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(theme_module.SPACE["xxs"])
        self.title_label = QLabel(technical_text()[0], self)
        self.title_label.setObjectName("deliveryGroupTitle")
        self.hint_label = QLabel(technical_text()[1], self)
        self.hint_label.setObjectName("deliveryGroupHint")
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.hint_label)
        self.meta_label = QLabel("", self)
        self.meta_label.setObjectName("deliveryMeta")
        layout.addWidget(self.meta_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.chevron = QLabel(self)
        self.chevron.setPixmap(icons.pixmap("chevron-down", 16, "text-secondary"))
        layout.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._open = False
        self._title = technical_text()[0]
        self._meta = ""
        self._sync_accessible()

    def set_content(self, title: str, hint: str, meta: str, icon: str) -> None:
        self._title = title
        self._meta = meta
        self.title_label.setText(title)
        self.hint_label.setText(hint)
        self.meta_label.setText(meta)
        self.tile.set_icon(icon)
        self._sync_accessible()

    def set_open(self, open_state: bool) -> None:
        self._open = bool(open_state)
        self.chevron.setPixmap(icons.pixmap(
            "chevron-up" if self._open else "chevron-down", 16, "text-secondary"))
        self._sync_accessible()

    def _sync_accessible(self) -> None:
        state = i18n.tr('已展开') if self._open else i18n.tr('已折叠')
        name = i18n.tr("{v0}，{v1}，{v2}", v0=self._title, v1=self._meta, v2=state)
        tip = i18n.tr('{v0}（{v1}）', v0=i18n.tr('展开或收起'), v1=self._title)
        # 每次刷新都比对再写：Qt 对相同文本的 setAccessibleName/setToolTip 仍会
        # 发一次元数据变更事件，无谓的 churn 会拖慢辅助技术。
        if self.accessibleName() != name:
            self.setAccessibleName(name)
        if self.accessibleDescription() != i18n.tr('按空格键展开或收起'):
            self.setAccessibleDescription(i18n.tr('按空格键展开或收起'))
        if self.toolTip() != tip:
            self.setToolTip(tip)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggled.emit(not self._open)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggled.emit(not self._open)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setProperty("hover", "true")
        style_module.repolish(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setProperty("hover", "false")
        style_module.repolish(self)
        super().leaveEvent(event)


class DeliveryWorkspace(QWidget):
    """交付工作区：顶部成果信息与动作 + 内容分组 + 折叠的技术附件。

    控件只负责展示与收集意图；文件操作、门禁判断与写盘都在窗口/适配器层。
    刷新走签名比对：manifest 没变就不重建任何控件（后台每秒钟都会刷新一次）。
    """

    downloadZipRequested = Signal()
    openFolderRequested = Signal()
    savePackageRequested = Signal()
    deliverRequested = Signal()
    deliverWithRiskRequested = Signal()
    fixBlockingRequested = Signal()
    exportCopyRequested = Signal()
    openFileRequested = Signal(str)
    revealFileRequested = Signal(str)
    saveFileCopyRequested = Signal(str)
    copyFilePathRequested = Signal(str)
    fileInfoRequested = Signal(str)

    MAX_WIDTH = 1100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deliveryWorkspace")
        self._model = DeliveryModel()
        self._model_signature: tuple | None = None
        self._rows: list[DeliveryFileRow] = []
        self._groups: list[DeliveryGroupCard] = []
        self._version: int | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        column = QWidget(self)
        column.setMaximumWidth(self.MAX_WIDTH)
        outer.addWidget(column, 0, Qt.AlignmentFlag.AlignHCenter)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, theme_module.SPACE["lg"],
                                  0, theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["md"])

        self.header = _Header(self)
        self.header.download_button.clicked.connect(self.downloadZipRequested.emit)
        self.header.open_folder_button.clicked.connect(self.openFolderRequested.emit)
        self.header.save_package_button.clicked.connect(self.savePackageRequested.emit)
        self.header.deliver_button.clicked.connect(self.deliverRequested.emit)
        self.header.risk_button.clicked.connect(self.deliverWithRiskRequested.emit)
        self.header.fix_button.clicked.connect(self.fixBlockingRequested.emit)
        self.header.export_button.clicked.connect(self.exportCopyRequested.emit)
        layout.addWidget(self.header)
        layout.addSpacing(theme_module.SPACE["md"])

        self.notice = style_module.NoticeStrip("", tone="warn", parent=self)
        self.notice.setVisible(False)
        layout.addWidget(self.notice)

        self.diverged_notice = style_module.NoticeStrip("", tone="info", parent=self)
        self.diverged_notice.setVisible(False)
        layout.addWidget(self.diverged_notice)

        self.sections = QWidget(self)
        self.sections_layout = QVBoxLayout(self.sections)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(theme_module.SPACE["md"])
        layout.addWidget(self.sections)

        self.technical = TechnicalAttachments(self)
        self.technical.setVisible(False)
        layout.addWidget(self.technical)

        self.empty_state = style_module.EmptyState(
            icon="package", title=i18n.tr('尚未生成冻结交付'),
            hint=i18n.tr('完成翻译与审校后冻结交付，这里会列出交付包里的每个成果、文件与大小；冻结后的版本不可修改。'),
            parent=self)
        self.empty_state.setVisible(False)
        layout.addWidget(self.empty_state)
        layout.addStretch(1)
        self._column = column
        self._sync_width()

    def _sync_width(self) -> None:
        """内容列最宽 1040，窄窗口下左右各留 24——不靠布局的 sizeHint 猜。"""
        available = max(320, self.width() - 2 * theme_module.SPACE["xl"])
        self._column.setFixedWidth(min(self.MAX_WIDTH, available))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._sync_width()

    # ---- 对外只读入口（窗口层、测试共用） ----
    def model(self) -> DeliveryModel:
        return self._model

    def version(self) -> int | None:
        return self._version

    def file_names(self) -> list[str]:
        return [item.name for item in self._model.all_files]

    def file_rows(self) -> list[DeliveryFileRow]:
        """当前模型真正拥有的文件行（技术附件收起时也不把旧行混进来）。"""
        rows = list(self._rows)
        if self._model.technical is not None:
            rows.extend(self.technical._rows)
        return rows

    def row_for(self, name: str) -> DeliveryFileRow | None:
        return next((row for row in self.file_rows() if row.entry.name == name), None)

    def set_model(self, model: DeliveryModel) -> None:
        """应用新模型；内容没变就只做必要的可见性/提示更新，不重建控件。"""
        self._model = model
        self._version = model.version
        signature = model.signature()
        if signature != self._model_signature:
            self._model_signature = signature
            self._rebuild(model)
        self.header.apply(model)
        self.technical.set_group(model.technical)
        for row in self.technical.rows:
            self._connect_row(row)
        self._sync_size_column()
        self._sync_tab_order()
        self._sync_missing_notice(model)
        self._sync_download_state(model)
        self._sync_empty(model)

    def _rebuild(self, model: DeliveryModel) -> None:
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        self._groups = []
        for group in model.groups:
            card = DeliveryGroupCard(group, self.sections)
            self._groups.append(card)
            self.sections_layout.addWidget(card)
            self._rows.extend(card.rows)
        for row in self.file_rows():
            self._connect_row(row)

    def _connect_row(self, row: DeliveryFileRow) -> None:
        if getattr(row, "_wired", False):
            return
        row._wired = True
        row.openRequested.connect(self.openFileRequested)
        row.revealRequested.connect(self.revealFileRequested)
        row.saveCopyRequested.connect(self.saveFileCopyRequested)
        row.copyPathRequested.connect(self.copyFilePathRequested)
        row.infoRequested.connect(self.fileInfoRequested)

    def _sync_missing_notice(self, model: DeliveryModel) -> None:
        missing = model.missing
        if missing:
            names = _list_separator().join(item.name for item in missing[:3])
            more = "" if len(missing) <= 3 else i18n.tr(" 等 {v0} 个", v0=len(missing))
            self.notice.set_tone("warn")
            self.notice.set_text(i18n.tr(
                '冻结交付里有 {v0} 个文件在磁盘上找不到（{v1}{v2}）：快照不可修改，请重新冻结交付或从备份恢复。',
                v0=len(missing), v1=names, v2=more))
        elif model.frozen and not model.integrity:
            self.notice.set_tone("warn")
            self.notice.set_text(i18n.tr('冻结交付完整性校验未通过：文件内容与记录不一致，请重新冻结交付。'))
        else:
            self.notice.set_text("")

    def _sync_size_column(self) -> None:
        """按当前语言量出大小/「文件缺失」列宽：既不裁字，也不让各行的列对不齐。"""
        from PySide6.QtGui import QFontMetrics

        # 用主题字号量（控件此刻可能还没被 QSS polish，font() 会是默认小字号）。
        font = style_module.font_for(style_module.current_theme())
        font.setPixelSize(theme_module.FONT_SIZES["footnote"])
        metrics = QFontMetrics(font)
        width = 72
        for row in self.file_rows():
            width = max(width, metrics.horizontalAdvance(row.entry.size_text) + 6)
        width = min(width, 132)
        for row in self.file_rows():
            if row.size_label.width() != width:
                row.size_label.setFixedWidth(width)
            # 极端长的译文（超过 132px）省略显示，完整文字留在悬停提示里。
            shown = metrics.elidedText(row.entry.size_text, Qt.TextElideMode.ElideRight,
                                       width - 6)
            if row.size_label.text() != shown:
                row.size_label.setText(shown)
            if row.size_label.toolTip() != row.entry.size_text:
                row.size_label.setToolTip(row.entry.size_text)

    def _sync_tab_order(self) -> None:
        """键盘顺序 = 视觉顺序：头部动作 → 内容行 → 折叠的技术附件 → 技术行。

        控件按创建顺序参与焦点链（技术附件先建、内容行后建），不显式串起来时
        Tab 会先跳到页面最底部的折叠头，再回到上面的文件行。
        """
        chain = [self.header.download_button, self.header.open_folder_button,
                 self.header.save_package_button, self.header.note_edit,
                 self.header.deliver_button, self.header.fix_button,
                 self.header.risk_button, self.header.export_button]
        for row in self._rows:
            chain.extend((row.open_button, row.more_button))
        chain.append(self.technical.header)
        for row in self.technical.rows:
            chain.extend((row.open_button, row.more_button))
        signature = tuple(id(widget) for widget in chain)
        if signature == getattr(self, "_tab_signature", None):
            return  # 控件没变就不重排焦点链（每秒刷新时不制造无谓的链重建）
        self._tab_signature = signature
        for first, second in zip(chain, chain[1:]):
            if first is not second:
                QWidget.setTabOrder(first, second)

    def _sync_download_state(self, model: DeliveryModel) -> None:
        """打包会失败时按钮就不可用，并在提示里说清楚原因（不是点了才报错）。"""
        blocked = bool(model.missing) or (model.frozen and not model.integrity)
        self.header.set_download_blocked(blocked)

    def _sync_empty(self, model: DeliveryModel) -> None:
        empty = not model.frozen
        self.sections.setVisible(not empty)
        self.empty_state.setVisible(empty)

    # ---- 门禁区（未冻结时） ----
    def set_gate(self, *, frozen: bool, diverged: bool, status: str, tone: str,
                 detail: str, deliver_label: str, can_deliver: bool,
                 show_fix: bool, fix_label: str, show_risk: bool,
                 note_enabled: bool, show_gate: bool | None = None) -> None:
        if show_gate is None:
            show_gate = not frozen or diverged
        self.header.set_gate(
            frozen=not show_gate, status=status, tone=tone, detail=detail,
            deliver_label=deliver_label, can_deliver=can_deliver,
            show_fix=show_fix, fix_label=fix_label, show_risk=show_risk,
            note_enabled=note_enabled)
        if diverged:
            self.diverged_notice.set_text(i18n.tr(
                '已冻结的交付版本保持不变；工作版本已有新的修改，可以冻结新版本。'))
        else:
            self.diverged_notice.set_text("")

    # ---- 旧调用点兼容：状态标签仍由窗口层直接驱动 ----
    @property
    def status_label(self):
        return self.header.status_label

    @property
    def deliver_button(self) -> QPushButton:
        return self.header.deliver_button

    @property
    def fix_button(self) -> QPushButton:
        return self.header.fix_button

    @property
    def risk_button(self) -> QPushButton:
        return self.header.risk_button

    @property
    def export_button(self) -> QPushButton:
        return self.header.export_button

    @property
    def save_package_button(self) -> QPushButton:
        return self.header.save_package_button

    @property
    def download_button(self) -> QPushButton:
        return self.header.download_button

    @property
    def open_folder_button(self) -> QPushButton:
        return self.header.open_folder_button

    @property
    def note_edit(self):
        return self.header.note_edit

    @property
    def cover(self) -> components.DocThumb:
        return self.header.cover

    @property
    def title_label(self) -> QLabel:
        return self.header.title_label

    @property
    def badge(self):
        return self.header.badge

    @property
    def filename_label(self) -> QLabel:
        return self.header.filename_label

    @property
    def meta_labels(self) -> dict[str, QLabel]:
        return self.header.meta_labels


class _Header(QWidget):
    """交付页顶部：成果身份（封面、标题、冻结版本、文件名、统计）与主要动作。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("deliveryHeader")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["md"])

        identity = QHBoxLayout()
        layout.addLayout(identity)
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(theme_module.SPACE["xl"])
        self.cover = components.DocThumb(width=96, height=128, parent=self)
        self.cover.setVisible(False)
        identity.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        self.identity_column = QWidget(self)
        column = QVBoxLayout(self.identity_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["xs"])
        title_row = QHBoxLayout()
        column.addLayout(title_row)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(theme_module.SPACE["md"])
        self.title_label = QLabel(i18n.tr('交付已完成'), self.identity_column)
        self.title_label.setObjectName("deliveryTitle")
        self.badge = FrozenPill("", "success", self.identity_column)
        self.badge.setVisible(False)
        title_row.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addStretch(1)
        self.filename_label = style_module.ElideLabel(
            "", mode=Qt.TextElideMode.ElideMiddle, parent=self.identity_column)
        self.filename_label.setObjectName("deliveryDocName")
        column.addWidget(self.filename_label)
        self.meta_row = QWidget(self.identity_column)
        meta_layout = QHBoxLayout(self.meta_row)
        meta_layout.setContentsMargins(0, theme_module.SPACE["xs"], 0, 0)
        meta_layout.setSpacing(theme_module.SPACE["md"])
        self.meta_labels: dict[str, QLabel] = {}
        for index, key in enumerate(("count", "size", "time")):
            if index:
                divider = QFrame(self.meta_row)
                divider.setObjectName("deliveryMetaDivider")
                divider.setFixedSize(1, 14)
                meta_layout.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
            item = QWidget(self.meta_row)
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(theme_module.SPACE["xs"])
            icon = QLabel(item)
            icon.setPixmap(icons.pixmap(
                {"count": "file-text", "size": "package", "time": "clock"}[key],
                14, "text-muted"))
            label = QLabel("", item)
            label.setObjectName("deliveryMeta")
            item_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            item_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
            self.meta_labels[key] = label
            meta_layout.addWidget(item, 0, Qt.AlignmentFlag.AlignVCenter)
        meta_layout.addStretch(1)
        column.addWidget(self.meta_row)
        column.addSpacing(theme_module.SPACE["sm"])

        self.actions = components.FlowRow(spacing=theme_module.SPACE["md"], fill=False)
        column.addLayout(self.actions)
        self.download_button = style_module.ElideButton(i18n.tr('下载全部（ZIP）'))
        style_module.set_button(self.download_button, "primary")
        self.download_button.setIcon(icons.icon("download", 16, "text-on-accent"))
        self.download_button.setIconSize(QSize(16, 16))
        self.download_button.setToolTip(i18n.tr(
            '把这个冻结版本的全部交付文件打包成一个 ZIP，并选择保存位置'))
        self.open_folder_button = style_module.ElideButton(i18n.tr('打开文件夹'))
        style_module.set_button(self.open_folder_button, "secondary")
        self.open_folder_button.setIcon(icons.icon("folder", 16, "text-secondary"))
        self.open_folder_button.setIconSize(QSize(16, 16))
        self.open_folder_button.setToolTip(i18n.tr(
            '在{v0}中打开冻结交付文件所在的文件夹', v0=platform_ui.file_manager_name()))
        self.save_package_button = style_module.ElideButton(i18n.tr('另存交付文件…'))
        style_module.set_button(self.save_package_button, "secondary")
        self.save_package_button.setIcon(icons.icon("save", 16, "text-secondary"))
        self.save_package_button.setIconSize(QSize(16, 16))
        self.save_package_button.setToolTip(i18n.tr(
            '另选一个文件夹，把冻结交付里的文件逐个复制过去（不会改动原交付）'))
        self.actions.addWidget(self.download_button)
        self.actions.addWidget(self.open_folder_button)
        self.actions.addWidget(self.save_package_button)

        # ---- 门禁区：未冻结时才有意义（状态、交付说明、冻结/去处理/接受风险） ----
        self.gate = QWidget(self.identity_column)
        gate_layout = QVBoxLayout(self.gate)
        gate_layout.setContentsMargins(0, 0, 0, 0)
        gate_layout.setSpacing(theme_module.SPACE["sm"])
        self.status_label = components.StatusLabel(
            i18n.tr('交付状态：—'), "idle", wrap=True)
        gate_layout.addWidget(self.status_label)
        self.note_edit = QLineEdit(self.gate)
        self.note_edit.setPlaceholderText(
            i18n.tr('交付说明（可选，例如：已完成人工审校）'))
        gate_layout.addWidget(self.note_edit)
        self.gate_actions = components.FlowRow(spacing=theme_module.SPACE["sm"], fill=False)
        gate_layout.addLayout(self.gate_actions)
        self.fix_button = style_module.ElideButton(i18n.tr('去处理必须处理的问题'))
        style_module.set_button(self.fix_button, "primary")
        self.fix_button.setIcon(icons.icon("shield-check", 16, "text-on-accent"))
        self.fix_button.setIconSize(QSize(16, 16))
        self.deliver_button = style_module.ElideButton(i18n.tr('审校完成后冻结交付'))
        style_module.set_button(self.deliver_button, "primary")
        self.risk_button = style_module.ElideButton(i18n.tr('接受风险并交付…'))
        style_module.set_button(self.risk_button, "plain")
        self.risk_button.setToolTip(i18n.tr(
            '仍有必须处理的问题时，写下说明并确认后继续交付；问题不会被删除'))
        self.export_button = style_module.ElideButton(i18n.tr('导出当前译文副本…'))
        style_module.set_button(self.export_button, "plain")
        self.export_button.setIcon(icons.icon("download", 16, "text-secondary"))
        self.export_button.setIconSize(QSize(16, 16))
        self.export_button.setToolTip(i18n.tr(
            '把当前工作版本（不是冻结交付）的译文导出到文件夹'))
        for button in (self.fix_button, self.deliver_button,
                       self.risk_button, self.export_button):
            self.gate_actions.addWidget(button)
        column.addWidget(self.gate)
        identity.addWidget(self.identity_column, 1)
        identity.addStretch(0)

    # ---- 应用模型 ----
    def apply(self, model: DeliveryModel) -> None:
        frozen = model.frozen
        self.cover.setVisible(frozen)
        self.badge.setVisible(frozen)
        self.filename_label.setText(model.source_name or i18n.tr('未命名任务'))
        self.filename_label.setToolTip(model.source_name)
        self.title_label.setText(i18n.tr('交付已完成') if frozen
                                 else i18n.tr('准备交付'))
        if frozen:
            version = model.version
            badge_text = (i18n.tr('已冻结 v{v0}', v0=version)
                          if version is not None else i18n.tr('已冻结'))
            if self.badge.text() != badge_text:
                self.badge.setText(badge_text)
            self.meta_labels["count"].setText(count_label(model.file_count))
            self.meta_labels["size"].setText(
                i18n.tr('总大小 {v0}', v0=human_size(model.total_size)))
            self.meta_labels["time"].setText(
                i18n.tr('交付于 {v0}', v0=model.frozen_at)
                if model.frozen_at else i18n.tr('交付时间未知'))
        self.meta_row.setVisible(frozen)
        self.set_frozen_actions(frozen)

    def set_download_blocked(self, blocked: bool) -> None:
        """打包按钮的可用性与说明只在状态变化时更新（相同文案不发 ToolTipChange）。"""
        if self.download_button.isEnabled() == bool(blocked):
            self.download_button.setEnabled(not blocked)
        tip = (i18n.tr('冻结交付里有文件缺失或校验失败，无法打包；请重新冻结交付')
               if blocked else
               i18n.tr('把这个冻结版本的全部交付文件打包成一个 ZIP，并选择保存位置'))
        if self.download_button.toolTip() != tip:
            self.download_button.setToolTip(tip)

    def set_frozen_actions(self, frozen: bool) -> None:
        for button in (self.download_button, self.open_folder_button,
                       self.save_package_button):
            button.setVisible(frozen)

    def set_gate(self, *, frozen: bool, status: str, tone: str, detail: str,
                 deliver_label: str, can_deliver: bool, show_fix: bool,
                 fix_label: str, show_risk: bool, note_enabled: bool) -> None:
        self.gate.setVisible(not frozen)
        self.status_label.set_status(status, tone, detail)
        self.note_edit.setEnabled(note_enabled)
        self.deliver_button.setText(deliver_label)
        self.deliver_button.setEnabled(can_deliver)
        self.fix_button.setVisible(show_fix)
        if show_fix:
            self.fix_button.setText(fix_label)
        self.deliver_button.setVisible(not show_fix)
        self.risk_button.setVisible(show_risk)
        self.export_button.setVisible(True)
