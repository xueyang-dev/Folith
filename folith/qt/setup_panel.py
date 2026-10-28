"""译前准备面板：导入之后、开始翻译之前，译者表达意图的唯一位置。

设计要点
--------

* **和 Streamlit 的「新建翻译任务」向导对齐**（app.py，四步向导的 01–03 步）。
  翻译模式、请求调度档、交付方案与产物清单都来自
  :mod:`folith.application.task_setup`，和 Web 端是同一份真源；这里只负责把它
  排成桌面端的形态，不再抄第二份常量。
* **面板是"决定"，不是"显示"**。它写回任务的那一步必须走共享命令
  ``application.commands.apply_task_setup``（经 adapter 暴露），因为
  ``resolve_effective_settings`` 让任务已保存的决定优先——面板只改自己的临时值
  会被任务本身忽略，用户选了产物却拿到导入时的默认值。
* **三层信息架构**（翻译设置这一块）：翻译模式（主决定）→ 内容处理（每行一项）
  → 高级设置（默认收起）。主要决定一眼可懂，执行参数退到折叠区里。
* **每个决定只写自己的字段**。「翻译模式」与「交付内容」是两组互不覆盖的用户决定：
  换模式只改策略模板，绝不动用户挑好的产物清单（见 :meth:`TaskSetupPanel._on_strategy_preset`）——
  "初始默认值"由 ``adapter.task_setup_view`` 的 pending 分支负责，与"用户改过的值"是两件事。
* **输入与上下文压成两组**：主要设置（目标语言 / 归入项目 / 术语库）常驻可见，
  「文档识别」（这个文档怎么读成文字）整组收进 :class:`DisclosureSection`，
  收起时标题右侧的摘要已经把答案写出来了。
* **样式只走向两个地方**：既有样式类（Card / SegmentedControl / ComboBox /
  CheckBox / SectionLabel / field_label / linkAction / hairline），加上
  ``build_qss`` 里两组通用规则 ``#presetOption`` 与 ``#disclosureHeader``。
  两组都只取 token、不用任何字面色，所以三套主题天然一致，
  也不存在"夜间专属色漏进浅色主题"的风险（见 ``qt-desktop-visual.md``）。
* **显示名可以翻译，取值一个都不许动**：目录里的标签统一经 ``i18n.tr()`` 显示，
  写回任务/序列化的永远是键或 ``currentData()``；语言自称与项目名（用户数据）
  刻意不过 i18n。
* 并发数与批次档用下拉框而**不是** QSpinBox：一是 Web 端本来就是 selectbox，
  二是仓库的 QSS 没有给 QSpinBox 上样式，直接放会露出原生控件。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from . import adapter
from . import components
from .doc_overview import DocumentOverview
from . import i18n
from . import icons
from .source_outline_panel import SourceOutlineSection
from . import style as style_module
from .style import Card
from . import theme as theme_module
from .workers import TaskRunner

#: 理论框架下拉框的取值。显示"自动推荐（建议）"，落库的是核心认识的那句话
#: （与 app.py 的 `_render_delivery_research_details` 同一套映射）。
_THEORY_CHOICES: tuple[tuple[str, str], ...] = (
    ("自动推荐（建议）", "基于文本特征、案例证据与可用文献自动推荐理论框架"),
    ("目的论 (Skopos Theory)", "目的论 (Skopos Theory)"),
    ("交际翻译与语义翻译 (Newmark)", "交际翻译与语义翻译 (Newmark)"),
    ("功能对等理论 (Nida)", "功能对等理论 (Nida)"),
    ("文本类型理论 (Reiss)", "文本类型理论 (Reiss)"),
    ("生态翻译学 (Hu Gengshen)", "生态翻译学 (Hu Gengshen)"),
)

CONCURRENCY_OPTIONS: tuple[int, ...] = (1, 2, 4, 6, 8)

#: 三个翻译模式的图标（**表现层的东西，不进共享模块**：共享那份是前端中立的，
#: 图标名只在 Qt 侧有意义）。含义刻意做成"检查量递增"：直接开跑 → 一个对勾 →
#: 带盾的对勾（精细多出审校与术语锁定两步）。
_MODE_ICONS: dict[str, str] = {
    "快速": "play",
    "标准": "check-circle",
    "学术增强": "shield-check",
}

#: 模式卡片上的"倾向"：这个模式的工作流包含哪几步（亮 = 包含，暗 = 不包含）。
#: 直接读预设里的真实开关，所以图标说的就是这个模式实际会做的事。
#: 交付文件格式 → 行图标（中性色）。研究产物统一用学位帽。
_FORMAT_ICONS: dict[str, str] = {
    "DOCX": "file-text", "PDF": "file", "EPUB": "book", "XLSX": "file-sheet",
    "TMX": "memory", "TBX": "termbase", "JSONL": "file-code",
}

_MODE_STEPS: tuple[tuple[str, str, str], ...] = (
    ("book-open", "建立文档画像", "enable_understanding"),
    ("termbase", "自动识别术语", "auto_term"),
    ("memory", "使用翻译记忆", "use_tm"),
    ("review", "译后自动审校", "enable_review"),
)

#: 面板能改的策略字段。预设之间的差异**只在这些字段上比较** —— 预设里若有这个
#: 面板没暴露的键（未来的新开关），把它算成"用户改过"会让"已自定义"常年亮着。
_MANAGED_STRATEGY_KEYS: tuple[str, ...] = (
    "auto_term", "use_tm", "enable_understanding", "enable_review",
    "strict_terminology_governance", "segmentation_mode",
    "translation_concurrency", "batch_profile", "knowledge_feedback",
)

#: 「识别语言」在两条提取路径上含义不同，提示语跟着换（键 = 是否本机 OCR）。
_OCR_LANGUAGE_HINTS: dict[bool, str] = {
    False: '视觉理解下只用于本机的定位证据；缺失或语言不符不影响翻译。',
    True: '本机 OCR 的识别语言，必须与扫描文字一致，不会自动检测。',
}

#: 扫描件识别区块的底部说明（键 = 是否本机 OCR）。
_OCR_SOURCE_HINTS: dict[bool, str] = {
    False: '只影响没有可用文本层的页面（扫描件）；文本层 PDF 与 DOCX 不走这一步。'
           '视觉理解需要可用的模型连接，缺凭据会明确报错，不会静默退回 OCR。',
    True: '只影响没有可用文本层的页面（扫描件）；文本层 PDF 与 DOCX 不走这一步。'
          '已选本机 OCR，本次不走视觉理解。',
}


def _panel(title: str, hint: str = "") -> tuple[Card, QVBoxLayout]:
    """带标题的卡片 + 竖排内容区（与设置页同一套"靠面分层"的做法）。"""
    card = Card()
    outer = QVBoxLayout(card)
    outer.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["md"],
                             theme_module.SPACE["md"], theme_module.SPACE["md"])
    outer.setSpacing(theme_module.SPACE["sm"])
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
    """表单字段：标签在上、控件在下（与设置页/旧界面一致）。"""
    holder = QWidget()
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme_module.SPACE["xxs"])
    column.addWidget(style_module.field_label(label))
    column.addWidget(widget)
    if hint:
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        column.addWidget(note)
    return holder


def _hairline() -> QFrame:
    """区块之间的 1px 发丝分隔（复用既有 ``QFrame#hairline`` 规则）。"""
    rule = QFrame()
    rule.setObjectName("hairline")
    rule.setFixedHeight(1)
    return rule


def _setting_row(title: str, control: QWidget, hint="") -> QWidget:
    """紧凑设置行：说明在左、控件在右。

    这是"信息密度"的关键——每项都做成一张独立卡片时，一屏放不下四项，而它们本来
    只是两个开关加两个下拉框。控件按**内容宽度**摆放（``addWidget(..., 0, ...)``），
    不再把分段控件拉满整行：那是"看起来像工具栏，其实是表单"的来源。
    """
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme_module.SPACE["md"])
    row.addLayout(_copy_column(title, hint), 1)
    row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return holder


def _copy_column(title: str, hint="") -> QVBoxLayout:
    """左侧的"标题 + 一行说明"。

    ``hint`` 既可以是一句话，也可以是一个 **QLabel** —— 后者用于说明会随控件
    取值变化的行（如「请求调度」的批次说明），调用方要能在后面接着改它的文案。
    """
    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(theme_module.SPACE["xxs"])
    column.addWidget(style_module.field_label(title))
    if isinstance(hint, QLabel):
        hint.setWordWrap(True)
        column.addWidget(hint)
    elif hint:
        note = QLabel(hint)
        note.setObjectName("hint")
        note.setWordWrap(True)
        column.addWidget(note)
    return column


#: 分组行的左缩进（发丝线与折叠体从这里开始）；分组与分组行在 components 里，全应用共用。
_ROW_INSET = components.FORM_ROW_INSET
SettingsGroup = components.FormGroup


def _group_header(text: str) -> QLabel:
    """分组上方的一行小标题（「翻译模式」「交付内容」……）。"""
    label = QLabel(text)
    label.setObjectName("groupHeader")
    return label


def _icon_row(icon_name: str, title: str, *controls: QWidget,
              value: QWidget | None = None, tooltip: str = "") -> QWidget:
    """一行次要设置：见 :class:`components.FormRow`。"""
    return components.FormRow(title, *controls, icon=icon_name, value=value, tooltip=tooltip)


class ToggleRow(QWidget):
    """开关行：说明在左、复选框在右，**整行都能点**。

    复选框在 macOS 上的可点区域就是那个 16px 的方块，而这一行的说明文字
    比它宽十倍。整行可点以后，点击目标和视觉目标一致（设置页的偏好行手感）。
    复选框自己的文字留空（由左侧标签承担），但它的 ``accessibleName`` 仍是那句话，
    读屏与自动化不会因此丢标签。
    """

    def __init__(self, title: str, hint: str, box: QCheckBox,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._box = box
        box.setText("")
        box.setAccessibleName(title)
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_module.SPACE["md"])
        row.addLayout(_copy_column(title, hint), 1)
        row.addWidget(box, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """空白处（标签、行内空隙）按下即切换。

        ⚠️ 点在复选框**本身**上时事件由它自己接受、不会冒泡到这里，所以不会翻两次。
        """
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self._box.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ElidedLabel(QLabel):
    """单行文案：宽度不够时**省略**，而不是把字切掉。

    为什么需要它：``QLabel`` 的 ``minimumSizeHint`` 是整行文字的宽度，放进一个宽度
    由布局分配的控件里（三个选项等分一行）时，窄一点就只剩"硬裁"——文字被切在半途，
    看起来像排版坏了。长语言（德语/俄语）与窄窗正是会碰到这个的组合。

    做法：横向 ``Ignored`` 把"我需要的宽度"从布局里摘掉（否则省略后 sizeHint 变小、
    布局再分配、再省略，来回抖），再按实际宽度取 ``elidedText``。完整文案仍可从
    按钮的 tooltip 读到。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(str(text), parent)
        self._full_text = str(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, value: str) -> None:  # noqa: N802 - Qt 命名
        """设置**完整**文案；要不要省略由 :meth:`_apply_elide` 按当前宽度决定。

        ⚠️ 覆写 ``setText`` 是必须的：折叠摘要这类文案会在载入/切换模式时改写，
        若只更新显示的那一行，``fullText()`` 与 tooltip 就会停在旧内容上。
        """
        self._full_text = str(value)
        self._apply_elide()

    def fullText(self) -> str:
        """未经省略的原文（自动化断言用）。"""
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._apply_elide()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            # 还没排过版：先放完整文案，等价于"装得下"。
            super().setText(self._full_text)
            return
        fitted = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width)
        if fitted != self.text():
            super().setText(fitted)


class DisclosureSection(QWidget):
    """紧凑折叠区块：标题 + **常驻摘要**（+ 可选警示）→ 展开才是正文。

    与「高级设置」的折叠头共用同一条样式规则（``#disclosureHeader``）：静止时它是一行
    标题（没有盒子），悬停才浮一点底色，焦点态由 QSS 自己画。因此三套主题天然一致，
    不需要任何夜间专属结构规则。

    与"只显示一个小箭头"的做法有两处刻意的差别：

    * **收起时也要读得懂**：摘要常驻在标题右侧（如 ``视觉理解 · 自动判断``），它是
      "这个文档现在按什么方式读"的答案 —— 不该要用户先展开才能知道；
    * **可执行的错误不藏在关着的区块里**：:meth:`set_warning` 把一条短警示挂在标题
      右侧（danger 色），收起状态下同样可见。

    折叠头是 ``QPushButton``（可 Tab、空格开合、有 ``accessibleName``），不是只有
    箭头能点。
    """

    toggled = Signal(bool)

    def __init__(self, title: str, parent: QWidget | None = None, *,
                 icon: str | None = None) -> None:
        super().__init__(parent)
        self.button = QPushButton()
        self.button.setObjectName("disclosureHeader")
        #: 放在 :class:`SettingsGroup` 里的折叠行：左侧中性色图标，标题用行标题字号，
        #: 摘要靠右（像「系统设置」的取值），展开体与发丝线同一条缩进。
        self.icon_tile = icons.IconLabel(icon, 16, "text-muted") if icon else None
        self.button.setCheckable(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setAccessibleName(title)
        # 分组里的折叠行用普通行标题（与上下的设置行同一字重）；独立的折叠节仍是小节标题。
        self.title_label = QLabel(title) if icon else style_module.SectionLabel(title)
        self.summary_label = ElidedLabel("")
        self.summary_label.setObjectName("hint")
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("statusError")
        self.warning_label.setVisible(False)
        self.chevron = icons.IconLabel("chevron-down", 13, "text-muted")

        row = QHBoxLayout(self.button)
        # 左右边距为 0：折叠头里的标题要和卡片里其它区块标题共享同一条左基线
        # （内缩 4px 就看得出参差），悬停高亮也因此铺满整行宽（与其它设置行同宽）。
        # 上下留一点，别让高亮贴着字。
        row.setContentsMargins(0, theme_module.SPACE["xxs"],
                               0, theme_module.SPACE["xxs"])
        row.setSpacing(theme_module.SPACE["sm"])
        if self.icon_tile is not None:
            self.button.setObjectName("settingsGroupHeader")
            self.button.setMinimumHeight(40)
            row.setContentsMargins(12, 6, 12, 6)
            row.setSpacing(12)
            row.addWidget(self.icon_tile, 0, Qt.AlignmentFlag.AlignVCenter)
            self.title_label.setObjectName("rowTitle")
            self.summary_label.setObjectName("rowValue")
            self.summary_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                            | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.title_label)
        row.addWidget(self.summary_label, 1)
        row.addWidget(self.warning_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        if self.icon_tile is not None:
            self.body_layout.setContentsMargins(_ROW_INSET, 0, 12, 12)
        else:
            self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(theme_module.SPACE["sm"])

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0 if self.icon_tile is not None else theme_module.SPACE["sm"])
        column.addWidget(self.button)
        column.addWidget(self.body)

        self.button.toggled.connect(self._sync)
        self._sync(False)

    def _sync(self, expanded: bool) -> None:
        self.body.setVisible(bool(expanded))
        self.chevron.set_icon("chevron-up" if expanded else "chevron-down", "text-muted")
        self.toggled.emit(bool(expanded))

    def set_summary(self, text: str) -> None:
        """收起状态下也看得见的一行答案。"""
        self.summary_label.setText(str(text or ""))
        self.summary_label.setToolTip(str(text or ""))

    def set_warning(self, text: str) -> None:
        """可执行的警示：挂在标题右侧，**不随折叠消失**。空串表示没有警示。"""
        value = str(text or "")
        self.warning_label.setText(value)
        self.warning_label.setVisible(bool(value))
        self.warning_label.setToolTip(value)

    def is_expanded(self) -> bool:
        return self.button.isChecked()


def _compact_count(value: int) -> str:
    """3.4 万 / 34k：中文界面按万，其余按 k / M。"""
    value = max(0, int(value or 0))
    if i18n.language().startswith("zh"):
        if value < 10000:
            return format(value, ",")
        return f"{value / 10000:.1f}".rstrip("0").rstrip(".") + " 万"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    return f"{round(value / 1000)}k" if value >= 1000 else str(value)


def _duration(minutes: float) -> str:
    minutes = float(minutes or 0)
    if minutes < 1:
        return i18n.tr('不到 1 分钟')
    if minutes < 90:
        return i18n.tr('约 {v0} 分钟', v0=int(round(minutes)))
    return i18n.tr('约 {v0} 小时', v0=f"{minutes / 60:.1f}".rstrip("0").rstrip("."))


def _ratio(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


class PresetOption(QPushButton):
    """一个翻译模式选项：图标 + 标题 + 一排工作流步骤小图标。

    一行摘要不再显示（仍保存在 ``_description`` 与悬停提示里）：模式之间的差别
    就是工作流的差别，用"包含哪几步"的小图标表达，比一句形容词更准确。

    ⚠️ ``QPushButton`` 的 ``sizeHint`` 是按**文字/图标**算的，看不见里面的布局 ——
    所以必须自己覆盖 ``sizeHint``/``minimumSizeHint``，否则三个选项会被压到
    "一个字都没写"的高度（内容全被裁掉）。同理 QSS 只管盒子，内边距在代码里给。

    两个标签都是 :class:`ElidedLabel`：一行说明在窄处省略而不是被切掉。
    """

    def __init__(self, key: str, title: str, description: str, icon_name: str,
                 parent: QWidget | None = None, *,
                 steps: tuple[tuple[str, str, bool], ...] = ()) -> None:
        super().__init__(parent)
        self.setObjectName("presetOption")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(72)
        # 无障碍名走 accessibleName：按钮自己的文字永远是空的（文案由内部的两个
        # 标签承担），读屏与自动化却仍能读到这个选项叫什么。
        self.setAccessibleName(title)
        self.key = str(key)
        self._icon_name = icon_name

        # 分段控件里的一段：只有名称。模式之间的差别由面板在下方用一句话说明
        # （:meth:`TaskSetupPanel._sync_mode_summary`），不靠小图标解释。
        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["md"], 5, theme_module.SPACE["md"], 5)
        column.setSpacing(0)
        self._icon = None
        self._title = ElidedLabel(title, self)
        self._title.setObjectName("presetTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head = QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._title)
        self._description = ElidedLabel(description, self)
        self._description.setObjectName("presetDesc")
        self._description.setVisible(False)
        column.addLayout(head)
        # 名称下三行预估：调用、输入 token 与耗时；窄窗口也能逐项比较。
        # 分两行是因为三段并排时每段只有一百多像素宽，放一行会被省略。
        self._estimate_calls = ElidedLabel("", self)
        self._estimate = ElidedLabel("", self)
        self._estimate_time = ElidedLabel("", self)
        for label in (self._estimate_calls, self._estimate, self._estimate_time):
            label.setObjectName("presetDesc")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setVisible(False)
            column.addWidget(label)
        # 工作流步骤只作为数据保存（悬停提示与面板的一句话说明都读它），不画图标。
        self.steps: dict[str, bool] = {label: bool(on) for _icon_name, label, on in steps}
        included = [label for label, on in self.steps.items() if on]
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)
        tip = [title]
        if description:
            tip.append(description)
        if included:
            tip.append(" · ".join(included))
        self._base_tooltip = "\n".join(tip)
        self.setToolTip(self._base_tooltip)
        self.setAccessibleDescription(self._base_tooltip)

    def set_estimate(self, tokens: str, duration: str = "", calls: str = "") -> None:
        """这个模式在这份文档上的大约成本（acceptance-feedback 13.6-3）。"""
        self._estimate_text = " · ".join(part for part in (calls, tokens, duration) if part)
        self._estimate_calls.setText(calls)
        self._estimate_calls.setVisible(bool(calls))
        self._estimate.setText(tokens)
        self._estimate.setVisible(bool(tokens))
        self._estimate_time.setText(duration)
        self._estimate_time.setVisible(bool(duration))
        self.setToolTip("\n".join(part for part in (
            self._base_tooltip, self._estimate_text) if part))
        self.setAccessibleDescription(self.toolTip())
        self.updateGeometry()

    def estimate(self) -> str:
        """完整的预估文字（标签在窄处会省略显示）。"""
        return getattr(self, "_estimate_text", "")

    def setText(self, value: str) -> None:  # noqa: N802 - Qt 命名
        """按钮自己的文字永远是空的：文案由内部的两个标签承担。

        Qt 会把 ``QPushButton.text()`` 当默认 ``sizeHint`` 的输入；这里显式挡掉，
        免得有人顺手 ``setText`` 之后得到一个"文字叠在布局上"的按钮。
        选项名走 :meth:`setAccessibleName`，不靠按钮文字。
        """
        return

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self.layout().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        return self.layout().minimumSize()

    def _sync_icon(self, checked: bool) -> None:
        """选中态由描边表达（QSS）；图标块自身颜色不随选中变化。"""
        return


class ModeSelector(QWidget):
    """三个翻译模式并排；选中的那个是"当前模板"。

    ``changed`` 只在**真的换了模式**时发出（与 :class:`style.SegmentedControl` 同
    语义）：重复点已选中的那个不该产生任何副作用。`value()` 为空表示当前配置不对应
    任何预设（用户改到哪儿都算"自定义"，界面据此不高亮任何一项）。
    """

    changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._options: dict[str, PresetOption] = {}
        self._value = ""
        # 一条分段轨道（与页面上其它分段控件同一种语法）。
        self.setObjectName("modeTrack")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(2, 2, 2, 2)
        self._row.setSpacing(2)

    def set_options(self, options: list[dict]) -> None:
        """按目录重建选项（``set_choices`` 时调一次）。

        每项是 ``{"key", "label", "description"}``；图标名由本模块的
        :data:`_MODE_ICONS` 决定（表现层的事，不进共享模块）。
        """
        for button in self._options.values():
            self._group.removeButton(button)
            button.hide()  # before detaching (see segment_assist._clear)
            button.setParent(None)
            button.deleteLater()
        self._options = {}
        self._value = ""
        for item in options:
            key = str(item.get("key") or "")
            if not key:
                continue
            # 标签与摘要都在这里过 i18n（本组件是面板的显示层）；模式的**键**仍是
            # 内部取值（快速/标准/学术增强），不受翻译影响。
            config = dict(item.get("config") or {})
            steps = tuple((icon_name, i18n.tr(label), bool(config.get(flag)))
                          for icon_name, label, flag in _MODE_STEPS) if config else ()
            button = PresetOption(
                key, i18n.tr(str(item.get("label") or key)),
                i18n.tr(str(item.get("description") or "")),
                _MODE_ICONS.get(key, "check"), steps=steps)
            button.clicked.connect(lambda _checked=False, k=key: self._pick(k))
            self._group.addButton(button)
            self._row.addWidget(button, 1)
            self._options[key] = button

    def _pick(self, key: str) -> None:
        if key == self._value:
            return
        self._value = key
        self.changed.emit(key)

    def value(self) -> str:
        return self._value

    def set_value(self, key: str) -> None:
        """选中某个模式；空值表示**不选中任何一项**（见类文档）。"""
        key = str(key or "")
        if key == self._value:
            return
        if not key:
            self._group.setExclusive(False)
            for button in self._options.values():
                button.setChecked(False)
            self._group.setExclusive(True)
            self._value = ""
            return
        button = self._options.get(key)
        if button is None:
            return
        button.setChecked(True)
        self._value = key

    def button(self, key: str) -> PresetOption | None:
        return self._options.get(key)

    def set_estimates(self, texts: dict[str, tuple[str, str, str]]) -> None:
        for key, button in self._options.items():
            tokens, duration, calls = texts.get(key) or ("", "", "")
            button.set_estimate(tokens, duration, calls)

    def options(self) -> tuple[str, ...]:
        return tuple(self._options)


class TaskSetupPanel(QWidget):
    """译前准备：输入与上下文 / 翻译设置 / 交付内容 / 开始翻译。"""

    startRequested = Signal(dict)
    #: 「归入项目」旁边的新建入口。面板自己**不建**项目：创建会写盘、要弹名称输入框、
    #: 还要刷新首页的项目列表，那些都是 window 的职责（它持有对话框与其它页面）。
    #: 面板只负责发出意图，再由 window 调 `add_project()` 把结果填回来。
    createProjectRequested = Signal()
    #: 译前翻译范围入口（#28）：结构区块只发意图，对话框与状态变更在 window。
    scopeRequested = Signal()
    termbaseAddRequested = Signal()
    styleProfileRequested = Signal()
    profileEditRequested = Signal()
    connectionSettingsRequested = Signal()
    termbaseRemoveRequested = Signal()
    literatureAddRequested = Signal()
    literatureRemoveRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 放在滚动区里时必须自带页面底色：`QScrollArea` 的 viewport 是透明的
        # （各页统一 `setAutoFillBackground(False)`），没有显式底色时卡片之间的
        # 缝隙会露出**未绘制的黑底**——浅色主题下最明显（标题黑字落在黑底上）。
        # 复用既有 `QWidget#pageCanvas` 规则，不新增任何 QSS。
        self.setObjectName("pageCanvas")
        self._choices: dict = {}
        self._view: dict = {}
        self._delivery: dict = {}
        self._delivery_boxes: dict[str, QWidget] = {}
        self._group_widgets: dict[str, QWidget] = {}
        self._delivery_groups: dict[str, "SettingsGroup"] = {}
        self._loading = False
        # 模式成本预估：原文的段数与字符数在后台读一次（每个任务一次）。
        self._size: dict = {}
        self._size_job = ""
        self._size_runner = TaskRunner(self, max_threads=1)
        #: 当前任务是不是 PDF。扫描件识别整块只对 PDF 出现（见 `_sync_ocr_visibility`）。
        self._is_pdf = False
        #: 当前任务是不是 DOCX。「原格式译文」只对 DOCX 原文可用（见 `_sync_docx_only`）。
        self._is_docx = False
        #: 研究产物那一组是否适用（由翻译策略决定）。汇总行不能用 ``isVisible()`` 判断：
        #: 面板还没 show 时任何子控件都是"不可见"的，会把研究产物静默漏掉。
        self._research_visible = False

        self.title_label = QLabel("")
        self.title_label.setObjectName("sectionTitle")
        self.title_label.setWordWrap(True)
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("hint")
        self.subtitle_label.setWordWrap(True)

        head = QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme_module.SPACE["xxs"])
        head.addWidget(self.title_label)
        head.addWidget(self.subtitle_label)

        # 两栏：左边是这份文档（agent 读到了什么），右边是要做的决定。
        # 右栏自上而下按"必须 → 常用 → 可以不管"排列，「开始翻译」紧跟在决定之后。
        page = QHBoxLayout(self)
        page.setContentsMargins(theme_module.SPACE["lg"], theme_module.SPACE["md"],
                                theme_module.SPACE["lg"], theme_module.SPACE["lg"])
        page.setSpacing(theme_module.SPACE["xl"] + 8)
        # 两栏作为一个整体居中：宽屏上左右留白对称，内容不贴在一边。
        page.addStretch(1)
        self.overview = DocumentOverview()
        page.addWidget(self.overview, 0, Qt.AlignmentFlag.AlignTop)
        content = QWidget()
        content.setMinimumWidth(420)
        content.setMaximumWidth(640)
        page.addWidget(content, 4, Qt.AlignmentFlag.AlignTop)
        page.addStretch(1)
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["lg"])
        # 页头（「译前准备」+ 说明）不再显示：工作台顶部的阶段条已经标出「准备」。
        self.title_label.setVisible(False)
        self.subtitle_label.setVisible(False)
        column.addLayout(head)

        options = self._build_context_card()
        # ① 必须：原文语言 → 目标语言（这一页仅有的两个大号控件）。
        # 原文语言默认"自动识别"；智能画像识别出来后先填上，用户看到不对再改。
        self.source_box = style_module.ComboBox()
        self.source_box.setObjectName("primaryCombo")
        self.source_box.setMinimumHeight(40)
        self.source_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.source_box.setToolTip(i18n.tr(
            '读扫描页时会告诉视觉模型这是哪种语言；共用一套字母的语言'
            '（如阿拉伯语、乌尔都语、波斯语、维吾尔语）明确指定后误读更少。'))
        self.source_box.addItem(i18n.tr('自动识别'), "")
        self.source_box.activated.connect(lambda _index: self._source_picked_by_user())
        self.source_box.currentIndexChanged.connect(lambda _index: self._source_changed())
        self._source_user_set = False
        self.source_hint = QLabel(i18n.tr('由智能画像识别，可修改'))
        self.source_hint.setObjectName("hint")
        self.source_hint.setContentsMargins(4, 0, 0, 0)
        self.source_hint.setVisible(False)
        languages = QGridLayout()
        languages.setContentsMargins(0, 0, 0, 0)
        languages.setHorizontalSpacing(theme_module.SPACE["md"])
        languages.setVerticalSpacing(theme_module.SPACE["xs"])
        languages.addWidget(_group_header(i18n.tr('原文语言')), 0, 0)
        languages.addWidget(_group_header(i18n.tr('目标语言')), 0, 1)
        languages.addWidget(self.source_box, 1, 0)
        languages.addWidget(self.target_box, 1, 1)
        languages.addWidget(self.source_hint, 2, 0, 1, 2)
        languages.setColumnStretch(0, 1)
        languages.setColumnStretch(1, 1)
        column.addLayout(languages)
        # ② 原文结构（#27，只读）：里面写了什么——本地解析的章节/页/段落分组
        #    与原文预览，未调用模型；摘要行收起时也把答案说出来。区块里带
        #    「翻译范围…」按钮（#28）：开始前排除不打算付费翻译的章节。
        self.source_section = SourceOutlineSection()
        self.source_section.scopeRequested.connect(self.scopeRequested.emit)
        column.addWidget(self.source_section)
        # ③ 常用：翻译模式（一条分段轨道 + 一句话说明）
        column.addWidget(self._build_strategy_card())
        # ④ 可以不管：项目 / 术语库 / 风格 / 文档读取 / 高级设置
        options.add_row(self.advanced_section)
        column.addWidget(options)
        # ⑤ 交付文件：一行摘要，点开才是文件清单（翻译完成后也能在「交付」补生成）
        self.delivery_section = DisclosureSection(i18n.tr('交付内容'), icon="package")
        self.delivery_section.body_layout.addWidget(self._build_delivery_card())
        delivery_group = SettingsGroup()
        delivery_group.add_row(self.delivery_section)
        column.addWidget(delivery_group)
        column.addWidget(self._build_footer())
        column.addStretch(1)

        # 示例译文跟着目标语言走（只有简体中文 / 日语 / 西班牙语有示例）。
        self.target_box.currentTextChanged.connect(lambda _text: self._sync_style_samples())
        self._sync_style_samples()

        # 高级项改动后重新计算"与预设相差几项"，并联动研究产物的可见性。
        self.auto_term_box.stateChanged.connect(self._on_advanced_changed)
        self.tm_box.stateChanged.connect(self._on_advanced_changed)
        self.review_box.stateChanged.connect(self._on_advanced_changed)
        self.strict_terms_box.stateChanged.connect(self._on_advanced_changed)
        self.knowledge_box.stateChanged.connect(self._on_advanced_changed)
        # 「建立文档画像」既是策略模板的一份子（要更新"已自定义"），也决定折叠摘要
        # 里的那段话，所以走一个同时管两件事的槽。
        self.understanding_box.stateChanged.connect(self._on_understanding_changed)
        self.seg_mode_seg.changed.connect(self._on_advanced_changed)
        self.concurrency_box.currentIndexChanged.connect(self._on_advanced_changed)
        self.batch_seg.changed.connect(self._on_advanced_changed)

    # ================= 构建 =================

    def _build_context_card(self) -> QWidget:
        """输入与上下文：两个视觉组。

        ① **主要设置**（目标语言 / 归入项目 / 术语库）—— 每次导入都要确认的决定，
           常驻可见，宽度够时两列并排；
        ② **文档识别**（:class:`DisclosureSection`，默认收起）—— "这个文档怎么读成
           文字"的整组参数。收起时标题右侧的摘要已经把答案写出来了
           （``视觉理解 · 自动判断``），要改的人再展开。

        解释文字一律收进控件下方的一行或 tooltip：这一块曾经在普通选择器下面堆了
        三段说明，把三项主要设置挤到了首屏之外。
        """
        self.target_box = style_module.ComboBox()
        self.target_box.setEditable(True)
        self.target_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Fixed)

        self.project_box = style_module.ComboBox()
        self.project_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)

        # 目标语言是这一页唯一必须做的决定：放在右栏最上方、用大号控件
        # （见 ``__init__``）。其余是可以不管的选项，进同一个分组，存在感一致地低。
        self.target_box.setObjectName("primaryCombo")
        self.target_box.setMinimumHeight(40)
        self.project_box.setFixedWidth(200)

        group = SettingsGroup()
        group.add_row(_icon_row("folder", i18n.tr('归入项目'), self._build_project_row()))
        group.add_row(self._build_glossary_row())
        group.add_row(self._build_style_card())
        self.doc_section = self._build_document_section()
        group.add_row(self.doc_section)
        self.options_group = group
        return group

    def _build_glossary_row(self) -> QWidget:
        """术语库：一行只读事实（标签在左、取值在右）。

        说明是"为什么只读"的上下文帮助，放进 tooltip —— 它不该再占一行，把主要
        设置往下推。取值用 :class:`ElidedLabel`：术语库名可以很长，硬裁会像排版坏了。
        """
        hint = i18n.tr('可选：添加术语库（Excel / CSV / TBX）或翻译记忆（TMX），用于保持术语与专名一致。')
        self.glossary_value = ElidedLabel("—")
        self.glossary_value.setObjectName("rowValue")
        self.glossary_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                         | Qt.AlignmentFlag.AlignVCenter)
        self.glossary_value.setToolTip(hint)
        self.glossary_add_button = QPushButton(i18n.tr('添加…'))
        style_module.set_button(self.glossary_add_button, "secondary", small=True)
        self.glossary_add_button.setToolTip(
            i18n.tr('支持 Excel、CSV、TBX（术语）和 TMX（翻译记忆）。'))
        self.glossary_add_button.clicked.connect(self.termbaseAddRequested.emit)
        self.glossary_remove_button = QPushButton(i18n.tr('移除'))
        style_module.set_button(self.glossary_remove_button, "secondary", small=True)
        self.glossary_remove_button.clicked.connect(self.termbaseRemoveRequested.emit)
        self.glossary_remove_button.setVisible(False)
        return _icon_row("termbase", i18n.tr('术语库'),
                         self.glossary_add_button, self.glossary_remove_button,
                         value=self.glossary_value, tooltip=hint)

    def _build_document_section(self) -> DisclosureSection:
        """「文档识别」：这个文档怎么读成文字。

        正文两段：

        * **建立文档画像**（``enable_understanding``，模式模板的一份子）—— 它决定要不要
          先分析全文，和"怎么读成文字"是同一件事的两面；
        * **扫描件识别** —— 只对 PDF 有意义：文本层 PDF 与 DOCX 不走这一步。
        """
        # 这里刻意不叫「OCR」。2026-10-07 的 VLM 迁移之后默认走的是**视觉理解**——而它
        # 恰恰是通过 `ocr_backend == "online"` 触发的（`core.py:2304`），旧标签
        # 「联网识别（默认）」是迁移前一天的词汇。用 OCR 当区块名会让用户以为自己的
        # 转向没生效（实测就是这一条被提出来了）。
        section = DisclosureSection(i18n.tr('文档识别'), icon="image")

        self.understanding_box = style_module.CheckBox("")
        section.body_layout.addWidget(ToggleRow(
            i18n.tr('建立文档画像'),
            i18n.tr('先分析全文，术语与上下文更准；更慢'),
            self.understanding_box))

        self.ocr_box = QWidget()
        ocr_layout = QVBoxLayout(self.ocr_box)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        ocr_layout.setSpacing(theme_module.SPACE["sm"])
        self.ocr_language_box = style_module.ComboBox()
        self.ocr_language_box.setEditable(True)
        self.ocr_language_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.ocr_mode_box = style_module.ComboBox()
        self.ocr_backend_box = style_module.ComboBox()
        self.ocr_quality_box = style_module.ComboBox()
        for box in (self.ocr_language_box, self.ocr_mode_box,
                    self.ocr_backend_box, self.ocr_quality_box):
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # 三个下拉都会改变摘要 / 警示（来源改方式，时机与语言改"读得对不对"）。
        self.ocr_backend_box.currentIndexChanged.connect(
            lambda _index: self._sync_ocr_visibility())
        self.ocr_mode_box.currentIndexChanged.connect(
            lambda _index: self._sync_document_summary())
        self.ocr_language_box.currentIndexChanged.connect(
            lambda _index: self._sync_document_summary())
        ocr_grid = QGridLayout()
        ocr_grid.setContentsMargins(0, 0, 0, 0)
        ocr_grid.setHorizontalSpacing(theme_module.SPACE["md"])
        ocr_grid.setVerticalSpacing(theme_module.SPACE["sm"])
        # 来源放第一行、跨两列：它是这一块唯一的主决定，其余三项都是它的参数。
        ocr_grid.addWidget(_field(
            i18n.tr('提取来源'), self.ocr_backend_box,
            i18n.tr('「视觉理解」把页面图像交给模型直接读成结构；「本机 OCR」用本机引擎'
                    '识别文字。两者互斥，选本机 OCR 就不再走视觉理解。')), 0, 0, 1, 2)
        ocr_grid.addWidget(_field(i18n.tr('何时识别'), self.ocr_mode_box), 1, 0)
        # 「识别语言」两条路径上都有意义，只是含义不同：本机 OCR 时它是语义识别语言，
        # 视觉理解时它只喂"本机定位证据"那个可选 pass。所以改提示语，不隐藏。
        # 提示语由 `_sync_ocr_visibility` 按来源改写，这里给的是视觉理解那一版。
        self.ocr_language_field = _field(
            i18n.tr('识别语言'), self.ocr_language_box,
            i18n.tr(_OCR_LANGUAGE_HINTS[False]))
        ocr_grid.addWidget(self.ocr_language_field, 1, 1)
        self.ocr_language_hint = self.ocr_language_field.findChild(
            QLabel, "hint")
        self.ocr_quality_field = _field(
            i18n.tr('识别精度'), self.ocr_quality_box,
            i18n.tr('只在本机 OCR 时生效；视觉理解固定按 150 DPI 送图，这一项无效。'))
        ocr_grid.addWidget(self.ocr_quality_field, 2, 0, 1, 2)
        ocr_grid.setColumnStretch(0, 1)
        ocr_grid.setColumnStretch(1, 1)
        ocr_layout.addWidget(style_module.SectionLabel(i18n.tr('扫描件识别')))
        ocr_layout.addLayout(ocr_grid)
        self.ocr_hint = QLabel(i18n.tr(_OCR_SOURCE_HINTS[False]))
        self.ocr_hint.setObjectName("hint")
        self.ocr_hint.setWordWrap(True)
        ocr_layout.addWidget(self.ocr_hint)
        self.ocr_box.setVisible(False)
        section.body_layout.addWidget(self.ocr_box)
        return section

    def _build_project_row(self) -> QWidget:
        """「归入项目」的字段行：选择器 + 新建入口。

        Web 端的译前步骤是从项目切换面板底部的「新建项目」建项目的
        （`app.py:_render_project_switcher_body`）。桌面端没有那个面板，选择器又是
        平铺下拉，于是**译前阶段根本没有建项目的路径**——用户只能先去首页建好再回来。
        把入口直接放在选择器旁边补上这一步。

        按钮用既有的 `#subtleAction`（次要动作样式），因此不新增 QSS。
        """
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"])
        layout.addWidget(self.project_box, 1)
        # 只留一个「+」：名称在悬停提示与无障碍名里，行里不再多一个文字按钮。
        self.new_project_button = QPushButton("")
        style_module.set_button(self.new_project_button, "secondary", small=True)
        self.new_project_button.setIcon(icons.icon("plus", 14, "text-secondary"))
        self.new_project_button.setAccessibleName(i18n.tr('新建项目…'))
        self.new_project_button.setToolTip(
            i18n.tr('创建新项目，并把它选为本次任务的归属。'))
        self.new_project_button.clicked.connect(
            lambda: self.createProjectRequested.emit())
        layout.addWidget(self.new_project_button)
        return row

    def _build_strategy_card(self) -> QWidget:
        """翻译模式：快速 / 标准 / 精细（只决定工作流）→ 高级设置（默认收起）。

        内容处理（切分、术语识别、翻译记忆）与审校、执行参数都在高级设置里：
        模式已经是它们的模板，大多数人不需要逐项改。下面是旧版三层分工的说明：

        三层的分工是这一版的重点：**模式**决定用哪套模板，**内容处理**是每次翻译
        都要面对的两三个选择，**高级设置**是执行参数（审校、术语锁定、并发、调度），
        它们影响的是任务怎么跑、API 怎么用，而不是译文是否更准 —— 所以退到折叠区里，
        并且不复述"更快/更好"这种预设里并不成立的暗示。
        """
        card = QWidget()
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme_module.SPACE["xs"])
        body.addWidget(_group_header(i18n.tr('翻译模式')))

        # ---- 翻译模式：一条分段轨道 + 一句话说明（只决定工作流；交付另算） -----------
        self.mode_selector = ModeSelector()
        self.mode_selector.changed.connect(self._on_strategy_preset)
        body.addWidget(self.mode_selector)
        self.mode_summary = QLabel("")
        self.mode_summary.setObjectName("hint")
        self.mode_summary.setWordWrap(True)
        self.mode_summary.setContentsMargins(4, 2, 0, 0)
        body.addWidget(self.mode_summary)

        # 自定义状态：只在**实际生效的配置**与所选预设不同时亮出来，
        # 并且同时给出"退回该预设默认值"的出口（否则用户只能靠记忆手动改回去）。
        self.strategy_note = QLabel("")
        self.strategy_note.setObjectName("hint")
        self.strategy_note.setWordWrap(True)
        self.restore_button = QPushButton(i18n.tr('恢复默认'))
        style_module.set_button(self.restore_button, "plain")
        self.restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_button.clicked.connect(self._restore_strategy_preset)
        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(theme_module.SPACE["sm"])
        status_layout.addWidget(self.strategy_note, 1)
        status_layout.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(status_row)
        # 默认配置时这一行不出现；只在"已自定义 / 对应不上预设"时出现（见 _sync_strategy_note）。
        self.strategy_status_row = status_row

        # ---- 高级设置（默认收起）：内容处理 + 审校与执行参数 ------------------------
        # 与「文档识别」同一个组件：折叠头、摘要位、警示位、展开体都由它提供，
        # 面板只挂自己的属性名（测试与 window 都按这些名字访问）。
        self.advanced_section = DisclosureSection(i18n.tr('高级设置'), icon="settings")
        self.advanced_toggle = self.advanced_section.button
        self.advanced_label = self.advanced_section.title_label
        self.advanced_chevron = self.advanced_section.chevron
        self.advanced_body = self.advanced_section.body
        self.advanced_toggle.setAccessibleName(i18n.tr('高级设置'))
        self.advanced_toggle.setToolTip(i18n.tr('展开或收起审校与执行参数'))
        # 高级设置是次要选项：由 ``__init__`` 放进「选项」分组的最后一行。
        advanced = self.advanced_body.layout()

        self.seg_mode_seg = style_module.SegmentedControl([
            ("sentence", i18n.tr('按句')),
            ("paragraph", i18n.tr('按段'))])
        self.seg_mode_seg.setToolTip(i18n.tr('按句提供主流 CAT 工具的逐句对照；'
                                            '按段保留清洗后的整段。'))
        advanced.addWidget(_setting_row(
            i18n.tr('文本切分方式'), self.seg_mode_seg,
            i18n.tr('决定翻译单元的大小')))

        self.auto_term_box = style_module.CheckBox("")
        self.tm_box = style_module.CheckBox("")
        advanced.addWidget(ToggleRow(
            i18n.tr('自动识别术语'), i18n.tr('提取关键术语，保持译名一致'),
            self.auto_term_box))
        advanced.addWidget(ToggleRow(
            i18n.tr('使用翻译记忆'), i18n.tr('参考已保存的历史译文'),
            self.tm_box))

        self.review_box = style_module.CheckBox("")
        self.strict_terms_box = style_module.CheckBox("")
        advanced.addWidget(ToggleRow(
            i18n.tr('译后自动审校'), i18n.tr('完成初译后进行额外检查'),
            self.review_box))
        advanced.addWidget(ToggleRow(
            i18n.tr('审定并锁定术语'), i18n.tr('审查候选术语，锁定已确认的译名'),
            self.strict_terms_box))
        self.knowledge_box = style_module.CheckBox("")
        advanced.addWidget(ToggleRow(
            i18n.tr('每批知识反馈'),
            i18n.tr('每批译完后记下专名与候选术语，保持前后译名一致；关闭可省约 15% token'),
            self.knowledge_box))

        self.concurrency_box = style_module.ComboBox()
        for value in CONCURRENCY_OPTIONS:
            self.concurrency_box.addItem(str(value), value)
        # 按内容宽度摆：它只有 1/2/4/6/8，拉满整行会读成"这里要填一段话"。
        self.concurrency_box.setSizePolicy(QSizePolicy.Policy.Fixed,
                                          QSizePolicy.Policy.Fixed)
        self.concurrency_box.setMinimumWidth(76)
        advanced.addWidget(_setting_row(
            i18n.tr('并发请求数'), self.concurrency_box,
            i18n.tr('同时请求的批次数；提高并发可能加速，也可能影响上下文衔接。重视连续性可选 1。')))

        self.batch_seg = style_module.SegmentedControl([])
        self.batch_seg.changed.connect(lambda _v: self._sync_batch_hint())
        self.batch_hint = QLabel("")
        self.batch_hint.setObjectName("hint")
        self.batch_hint.setWordWrap(True)
        advanced.addWidget(_setting_row(
            i18n.tr('请求调度'), self.batch_seg, self.batch_hint))

        # 折叠体默认收起由 DisclosureSection 自己负责（构造时已 _sync(False)）。
        return card

    def _build_style_section(self) -> DisclosureSection:
        """「翻译风格」折叠节本身（摘要由 :meth:`_style_changed` 维护）。

        内容由 :meth:`_build_style_card` 填进来，放在「输入与上下文」的最后一行。
        """
        self.style_section = DisclosureSection(i18n.tr('翻译风格'), icon="sparkles")
        return self.style_section

    def _build_style_card(self) -> QWidget:
        """翻译风格：「输入与上下文」里的一行折叠项（默认收起，摘要是当前风格）。

        风格不是核心设置：足够强的模型会自己判断语气与措辞，所以默认「沿用设置」，
        这一行收起时只读出答案。展开后是风格卡片、「智能画像」、所选风格的一句示例
        和一行可选的补充约束——不再有人工调节的滑块。

        真值仍是 ``style_box``（隐藏的下拉框，"沿用设置"在第 0 项）；四个强度值
        （``style_sliders``）保留为**不显示**的内部状态：选中风格时自动放到该风格的
        默认位置，随任务配置一起保存，配置格式与旧版一致。
        """
        from . import style_cards

        self._build_style_section()
        self._style_recommendation: dict = {}
        section = self.style_section
        inner = section.body_layout
        inner.setSpacing(theme_module.SPACE["md"])

        # ---- 入口：智能画像 / 沿用设置（说明进悬停提示） ------------------------
        self.style_profile_button = QPushButton(i18n.tr('智能画像'))
        style_module.set_button(self.style_profile_button, "plain")
        self.style_profile_button.setIcon(icons.icon("sparkles", 14, "accent"))
        self.style_profile_button.setToolTip(
            i18n.tr('抽样读几段原文，让模型推荐适合的风格（需要一次模型调用）'))
        self.style_profile_button.clicked.connect(self.styleProfileRequested.emit)
        self.style_inherit_button = QPushButton(i18n.tr('沿用设置'))
        style_module.set_button(self.style_inherit_button, "plain")
        self.style_inherit_button.setCheckable(True)
        self.style_inherit_button.setToolTip(i18n.tr('沿用设置里的风格规则'))
        self.style_inherit_button.clicked.connect(lambda: self.style_box.setCurrentIndex(0))
        actions = QHBoxLayout()
        actions.setSpacing(theme_module.SPACE["md"])
        actions.addStretch(1)
        actions.addWidget(self.style_profile_button)
        actions.addWidget(self.style_inherit_button)
        inner.addLayout(actions)

        # 推荐结果只在用过「智能画像」之后才出现。
        self.style_accept_button = QPushButton(i18n.tr('接受推荐'))
        style_module.set_button(self.style_accept_button, "primary")
        self.style_accept_button.clicked.connect(self._accept_style_recommendation)
        self.style_accept_button.setVisible(False)
        self.style_status = QLabel("")
        self.style_status.setObjectName("hint")
        self.style_status.setWordWrap(True)
        self.style_status.setVisible(False)
        reco_row = QHBoxLayout()
        reco_row.setSpacing(theme_module.SPACE["md"])
        reco_row.addWidget(self.style_status, 1)
        reco_row.addWidget(self.style_accept_button, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addLayout(reco_row)

        # 文档画像：模型把这份文档当成什么来译（领域 · 文本类型 · 读者 · 语域）。
        # 判断错了可以改；保存后翻译直接用它。
        self.profile_label = QLabel("")
        self.profile_label.setObjectName("hint")
        self.profile_label.setWordWrap(True)
        self.profile_edit_button = QPushButton(i18n.tr('修改画像…'))
        style_module.set_button(self.profile_edit_button, "plain")
        self.profile_edit_button.clicked.connect(self.profileEditRequested.emit)
        self.profile_row = QWidget()
        profile_row = QHBoxLayout(self.profile_row)
        profile_row.setContentsMargins(0, 0, 0, 0)
        profile_row.setSpacing(theme_module.SPACE["md"])
        profile_row.addWidget(self.profile_label, 1)
        profile_row.addWidget(self.profile_edit_button, 0, Qt.AlignmentFlag.AlignVCenter)
        inner.addWidget(self.profile_row)
        self._suggested_profile: dict = {}
        self._sync_profile_row()

        # ---- 风格卡片 ----------------------------------------------------------
        self.style_box = style_module.ComboBox()
        self.style_box.addItem(i18n.tr('沿用设置里的风格规则'), "")
        choices = adapter.style_profile_choices()
        for choice in choices:
            self.style_box.addItem(i18n.tr_dynamic(choice["name"]), choice["id"])
            self.style_box.setItemData(self.style_box.count() - 1,
                                       i18n.tr_dynamic(choice["summary"]), Qt.ItemDataRole.ToolTipRole)
        self.style_box.setVisible(False)
        self.style_box.currentIndexChanged.connect(self._style_changed)
        self.style_cards = style_cards.StyleCardGrid(choices)
        self.style_cards.picked.connect(self._pick_style_card)
        inner.addWidget(self.style_cards)

        # 示例：只显示**选中**风格的那一句（原文在悬停提示里）。
        sample_caption = QLabel(i18n.tr('示例'))
        sample_caption.setObjectName("faint")
        self.style_sample = QLabel("")
        self.style_sample.setObjectName("styleSample")
        self.style_sample.setWordWrap(True)
        self.style_sample.setTextFormat(Qt.TextFormat.PlainText)
        self.style_sample.setToolTip(
            i18n.tr('示例原文：{v0}', v0=style_cards.SAMPLE_SOURCE))
        self.style_sample_row = QWidget()
        sample_row = QHBoxLayout(self.style_sample_row)
        sample_row.setContentsMargins(0, 0, 0, 0)
        sample_row.setSpacing(theme_module.SPACE["md"])
        sample_row.addWidget(sample_caption, 0, Qt.AlignmentFlag.AlignTop)
        sample_row.addWidget(self.style_sample, 1)
        inner.addWidget(self.style_sample_row)

        # ---- 补充约束（可选；占位文字就是说明） -----------------------------------
        self.style_custom = QLineEdit()
        self.style_custom.setPlaceholderText(
            i18n.tr('例如：产品名保留原文；界面名称加引号；不使用感叹号。'))
        self.style_custom.setToolTip(i18n.tr('补充风格约束（可选）'))
        self.style_custom.textChanged.connect(self._style_changed)
        inner.addWidget(self.style_custom)

        # ---- 风格强度：内部状态，不显示 ------------------------------------------
        self.style_sliders: dict[str, QSlider] = {}
        for key, caption, default in adapter.STYLE_ADJUSTMENTS:
            slider = QSlider(Qt.Orientation.Horizontal, section)
            slider.setRange(0, 100)
            slider.setValue(default)
            slider.setAccessibleName(i18n.tr(caption))
            slider.setVisible(False)
            slider.valueChanged.connect(self._style_changed)
            self.style_sliders[key] = slider

        self._sync_style_samples()
        self._style_changed()
        return section

    def _pick_style_card(self, style_id: str) -> None:
        index = self.style_box.findData(style_id)
        if index < 0:
            return
        if index == self.style_box.currentIndex():
            return
        self.style_box.setCurrentIndex(index)
        self._apply_style_preset(style_id)

    def _apply_style_preset(self, style_id: str) -> None:
        """选中一种风格时，四个微调滑块回到这种风格的特征位置（等级 × 20）。"""
        from . import style_cards

        levels = style_cards.trait_levels(style_cards.profile_params(style_id))
        for key, slider in self.style_sliders.items():
            if key in levels:
                slider.setValue(levels[key] * 20)

    def _sync_style_samples(self) -> None:
        from . import style_cards

        lang = style_cards.sample_language(self.target_box.currentText()) \
            if hasattr(self, "target_box") else ""
        self.style_cards.set_sample_language(lang)
        self._sync_style_sample()

    def _sync_style_sample(self) -> None:
        """示例只跟着选中的风格走；沿用设置或目标语言没有示例时整行收起。"""
        text = self.style_cards.sample_for(str(self.style_box.currentData() or ""))
        self.style_sample.setText(text)
        self.style_sample_row.setVisible(bool(text))

    def _style_changed(self, *_args) -> None:
        chosen = bool(self.style_box.currentData())
        for slider in self.style_sliders.values():
            slider.setEnabled(chosen)
        self.style_custom.setEnabled(chosen)
        if hasattr(self, "style_cards"):
            self.style_cards.set_selected(str(self.style_box.currentData() or ""))
            self.style_inherit_button.setChecked(not chosen)
            self._sync_style_sample()
        # 没有手动选风格时，风格来自智能画像的分析建议（摘要说出这一点，而不是"沿用设置"）。
        self.style_section.set_summary(
            self.style_box.currentText() if chosen else i18n.tr('智能画像分析建议'))

    def style_selection(self) -> dict | None:
        """用户选定的风格（没选返回 ``None``，沿用设置里的规则）。"""
        base = str(self.style_box.currentData() or "")
        if not base:
            return None
        return {"selected": base, "source": "user_override",
                "adjustments": {key: slider.value() for key, slider in self.style_sliders.items()},
                "custom_rules": self.style_custom.text().strip()}

    # -- 原文语言 ------------------------------------------------------------
    def _source_picked_by_user(self) -> None:
        """用户亲手选过：之后的识别结果不再覆盖，"由智能画像识别"的提示也收起。"""
        self._source_user_set = True
        self.source_hint.setVisible(False)

    def _source_changed(self) -> None:
        value = str(self.source_box.currentData() or "")
        # 左栏的元信息跟着说"原文：…"（自动识别时不说）。
        if hasattr(self, "overview"):
            self.overview.set_document(self.overview._job_id, self.overview._filename,
                                       source_lang=value)
        # 本机 OCR 还没指定识别语言时，顺手用这门语言的语言包（只在列表里有它时）。
        from folith import source_languages as _source_languages

        code = _source_languages.ocr_code(value)
        if code and not str(self.ocr_language_box.currentData() or "").strip():
            index = self.ocr_language_box.findData(code)
            if index >= 0:
                self.ocr_language_box.setCurrentIndex(index)

    def apply_detected_source_language(self, value: str) -> bool:
        """智能画像识别出的原文语言：用户还没亲手选过时才填上，并说明来源。"""
        from folith import source_languages as _source_languages

        value = _source_languages.match(value)
        if not value or self._source_user_set:
            return False
        index = self.source_box.findData(value)
        if index < 0:
            return False
        self.source_box.setCurrentIndex(index)
        self.source_hint.setVisible(True)
        return True

    def set_style_recommendation(self, result: dict) -> None:
        """窗口层把智能画像的结果交回来：显示理由，并给出「接受推荐」。"""
        self.apply_detected_source_language(str((result or {}).get("source_language") or ""))
        recommendation = dict((result or {}).get("recommendation") or {})
        self._style_recommendation = recommendation
        self.style_profile_button.setEnabled(True)
        failed = bool((result or {}).get("failed"))
        names = {item["id"]: item["name"] for item in adapter.style_profile_choices()}
        style_id = str(recommendation.get("recommended_style") or "general")
        reasons = [str(r) for r in recommendation.get("reasons") or []][:3]
        head = i18n.tr('推荐风格：{v0}（置信度 {v1}）',
                       v0=i18n.tr_dynamic(names.get(style_id, style_id)),
                       v1=f"{float(recommendation.get('confidence') or 0):.2f}")
        self.style_status.setText(" · ".join([head] + reasons))
        self.style_status.setVisible(True)
        self._suggested_profile = dict((result or {}).get("document_profile") or {})
        self._sync_profile_row()
        # 失败降级的"通用风格"不是推荐，不给「接受」
        self.style_accept_button.setVisible(not failed)

    def set_connection_problem(self, problem: str) -> None:
        """模型连接的问题（空 = 可用）：开始翻译需要它。"""
        self.connection_note.setText(i18n.tr('还不能翻译：{v0}', v0=i18n.tr_dynamic(problem))
                                     if problem else "")
        self.connection_note.setVisible(bool(problem))
        self.connection_button.setVisible(bool(problem))
        # 做不了的事不该是一个可点的蓝色主按钮：旁边已经说了原因和去哪里配置。
        self.start_button.setEnabled(not problem)
        self.start_button.setToolTip(self.connection_note.text())

    def document_profile(self) -> dict:
        """当前该显示/编辑的画像：用户保存过的优先，否则是智能画像的建议。"""
        saved = dict((self._view or {}).get("document_profile") or {})
        return saved if adapter.profile_summary(saved) else dict(self._suggested_profile)

    def _sync_profile_row(self) -> None:
        saved = adapter.profile_summary((self._view or {}).get("document_profile"))
        suggested = adapter.profile_summary(self._suggested_profile)
        if saved:
            text = i18n.tr('文档画像（已保存）：{v0}', v0=saved)
        elif suggested:
            text = i18n.tr('文档画像（智能画像建议）：{v0}', v0=suggested)
        else:
            text = i18n.tr('文档画像：还没有。开始翻译时会自动分析，也可以先自己填写。')
        self.profile_label.setText(text)

    def reset_style_profile(self) -> None:
        """切换任务时清掉上一个任务的智能画像状态（面板是各任务共用的）。"""
        self._style_recommendation = {}
        self._suggested_profile = {}
        self._sync_profile_row()
        self.style_profile_button.setEnabled(True)
        self.style_accept_button.setVisible(False)
        self.style_status.setText("")
        self.style_status.setVisible(False)

    def set_style_busy(self, busy: bool) -> None:
        self.style_profile_button.setEnabled(not busy)
        if busy:
            self.style_status.setText(i18n.tr('正在读取原文并分析风格…'))
            self.style_status.setVisible(True)
    
    def _accept_style_recommendation(self) -> None:
        style_id = str(self._style_recommendation.get("recommended_style") or "")
        index = self.style_box.findData(style_id)
        if index >= 0:
            self.style_box.setCurrentIndex(index)
            self._apply_style_preset(style_id)
            self.style_section.button.setChecked(True)

    def _build_delivery_card(self) -> QWidget:
        card = QWidget()
        body = QVBoxLayout(card)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme_module.SPACE["sm"])

        # 小标题与方案选择同一行：方案是快捷入口，下面每个文件的开关才是事实。
        self.delivery_seg = style_module.SegmentedControl([])
        self.delivery_seg.changed.connect(self._on_delivery_preset)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addStretch(1)
        head.addWidget(self.delivery_seg)
        body.addLayout(head)

        # 实时汇总：把"选了哪套方案"与"实际会产出什么"直接对上。方案是**快捷入口**，
        # 勾选才是事实，两者一旦不一致就换到「自定义」，这一行说的就是事实。
        self.delivery_summary = QLabel("")
        self.delivery_summary.setObjectName("hint")
        self.delivery_summary.setWordWrap(True)
        self.delivery_summary.setContentsMargins(4, 0, 0, 0)
        body.addWidget(self.delivery_summary)

        self.translation_group = self._build_delivery_group(
            i18n.tr('译文文件'), "translation_items")
        self.asset_group = self._build_delivery_group(
            i18n.tr('语言资产'), "asset_items")
        self.research_group = self._build_delivery_group(
            i18n.tr('研究产物'), "research_items")

        groups = QWidget()
        groups_layout = QGridLayout(groups)
        groups_layout.setContentsMargins(0, 0, 0, 0)
        groups_layout.setHorizontalSpacing(theme_module.SPACE["lg"])
        groups_layout.setVerticalSpacing(theme_module.SPACE["md"])
        groups_layout.addWidget(self.translation_group, 0, 0)
        groups_layout.addWidget(self.asset_group, 0, 1)
        groups_layout.addWidget(self.research_group, 1, 0, 1, 2)
        groups_layout.setColumnStretch(0, 1)
        groups_layout.setColumnStretch(1, 1)
        body.addWidget(groups)

        # 报告打开时才需要理论框架（与 Web 端"研究产物 → 报告设置"一致）。
        self.theory_box = style_module.ComboBox()
        self.theory_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Fixed)
        self.theory_field = _field(
            i18n.tr('理论框架'), self.theory_box,
            i18n.tr('根据文本特征、案例证据与可用文献确定；仅在证据充分时使用。'))
        self.theory_field.setVisible(False)
        body.addWidget(self.theory_field)

        # 报告的文献证据来源（旧向导的「参考资料」）。同样只在报告打开时出现。
        self.literature_value = ElidedLabel("—")
        self.literature_value.setObjectName("hint")
        self.literature_add_button = QPushButton(i18n.tr('添加参考资料…'))
        self.literature_add_button.setToolTip(i18n.tr(
            '支持 PDF、DOCX、Markdown、TXT、BibTeX / RIS（含 Zotero 导出）；也可导入已有文献证据注册表（.json）。'))
        self.literature_add_button.clicked.connect(self.literatureAddRequested.emit)
        self.literature_remove_button = QPushButton(i18n.tr('移除参考资料'))
        self.literature_remove_button.clicked.connect(self.literatureRemoveRequested.emit)
        self.literature_remove_button.setVisible(False)
        literature_row = QWidget()
        literature_layout = QHBoxLayout(literature_row)
        literature_layout.setContentsMargins(0, 0, 0, 0)
        literature_layout.setSpacing(theme_module.SPACE["md"])
        literature_layout.addWidget(self.literature_value, 1)
        literature_layout.addWidget(self.literature_add_button)
        literature_layout.addWidget(self.literature_remove_button)
        self.literature_field = _field(
            i18n.tr('参考资料'), literature_row,
            i18n.tr('报告引用的文献来源；运行时解析并记录来源位置。'))
        self.literature_field.setVisible(False)
        body.addWidget(self.literature_field)
        return card

    def _build_delivery_group(self, title: str, key: str) -> QWidget:
        """一组交付产物：小标题 + 圆角分组（每个文件一行：格式图标 + 名称 + 开关）。"""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["xs"])
        column.addWidget(_group_header(title))
        group = SettingsGroup()
        column.addWidget(group)
        column.addStretch(1)
        self._group_widgets[key] = holder
        self._delivery_groups[key] = group
        return holder

    def _build_footer(self) -> QWidget:
        self.start_button = QPushButton(i18n.tr('开始翻译'))
        style_module.set_button(self.start_button, "primary")
        self.start_button.setIcon(icons.icon(
            "play", 16, "text-on-accent"))
        self.start_button.setIconSize(QSize(16, 16))
        self.start_button.clicked.connect(
            lambda: self.startRequested.emit(self.payload()))
        self.footer_note = QLabel(i18n.tr('开始后可继续编辑译文；要生成的文件可在「交付」页查看。'))
        self.footer_note.setObjectName("hint")
        self.footer_note.setWordWrap(True)
        # 底栏只留主行动：这句说明不影响"现在要不要开始"。
        self.footer_note.setVisible(False)
        # 模型连接还没配好时就在开始按钮旁边说清楚，而不是点了才报错。
        self.connection_note = QLabel("")
        self.connection_note.setObjectName("warningText")
        self.connection_note.setWordWrap(True)
        self.connection_note.setVisible(False)
        self.connection_button = QPushButton(i18n.tr('设置连接…'))
        style_module.set_button(self.connection_button, "plain")
        self.connection_button.clicked.connect(self.connectionSettingsRequested.emit)
        self.connection_button.setVisible(False)
        self.footer_bar = QWidget()
        row = QHBoxLayout(self.footer_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_module.SPACE["md"])
        row.addWidget(self.footer_note, 1)
        row.addWidget(self.connection_note, 1)
        row.addWidget(self.connection_button)
        row.addStretch(1)
        row.addWidget(self.start_button)
        return self.footer_bar

    def detach_footer(self) -> QWidget:
        """把底部的「开始翻译」条交给外层放到滚动区之外。

        面板比窗口高、要放进滚动区；主行动若跟着内容滚动，首屏就看不到它
        （实测 900px 高的窗口里它在第 980px）。宿主调用这个方法把它钉在滚动区下方。
        """
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.footer_bar)
        self.footer_bar.setParent(None)
        return self.footer_bar

    # ================= 数据 =================

    def set_choices(self, choices: dict) -> None:
        """填目录（adapter.setup_choices()）：预设、产物清单、OCR、项目、目标语言。

        ⚠️ **只有显示名走 `i18n.tr()`，取值一律原样保留。** 目录里的标签是中文原文，
        译名是给人看的；写回任务的是 `currentData()` / `key`。这样把界面语言换成英语
        不会把序列化取值也改掉（"不要用翻译后的显示串当持久标识"）。

        例外是**语言自称**（简体中文 / English / 日本語 / العربية…）：把语言名翻成
        界面语言是"翻译了不该翻译的东西"，所以那一组原样显示。
        """
        self._choices = dict(choices or {})
        self._loading = True
        try:
            self.target_box.clear()
            for lang in self._choices.get("target_languages") or []:
                self.target_box.addItem(str(lang), str(lang))
            while self.source_box.count() > 1:
                self.source_box.removeItem(1)
            from folith import source_languages as _source_languages

            for lang in self._choices.get("source_languages") or []:
                self.source_box.addItem(_source_languages.display_name(lang), str(lang))
            self.set_projects(self._choices.get("projects") or [])
            self.ocr_language_box.clear()
            for label, value in self._choices.get("ocr_languages") or []:
                # 语言自称：见方法文档的例外说明。
                self.ocr_language_box.addItem(str(label), str(value))
            if not self.ocr_language_box.count():
                for label, value in _fallback_ocr_languages():
                    self.ocr_language_box.addItem(label, value)
            self._fill_combo(self.ocr_mode_box, self._choices.get("ocr_modes") or [])
            self._fill_combo(self.ocr_backend_box,
                             self._choices.get("ocr_backends") or [])
            local_index = self.ocr_backend_box.findData("tesseract")
            if local_index >= 0:
                from . import adapter
                self.ocr_backend_box.model().item(local_index).setEnabled(bool(adapter.core._find_tesseract()))
            self._fill_combo(self.ocr_quality_box,
                             self._choices.get("ocr_quality") or [])
            self.theory_box.clear()
            for label, value in _THEORY_CHOICES:
                self.theory_box.addItem(i18n.tr(label), value)
            self.mode_selector.set_options(
                self._choices.get("strategy_presets") or [])
            self.batch_seg.set_options([
                (str(profile["key"]),
                 i18n.tr(str(profile.get("label") or profile["key"])))
                for profile in self._choices.get("batch_profiles") or []])
            self.delivery_seg.set_options([
                (str(preset["key"]), i18n.tr(str(preset["label"])))
                for preset in self._choices.get("delivery_presets") or []])
            self._build_delivery_boxes()
        finally:
            self._loading = False

    def set_projects(self, projects: list[dict]) -> None:
        """只刷新「归入项目」下拉框，并保留当前选中的项目（项目页新建/改名/归档/删除之后）。

        不动面板的其他选择：重建整个面板会抹掉用户在策略/交付上刚做的调整。
        """
        keep = self.project_box.currentData()
        was_loading = self._loading
        self._loading = True
        try:
            self.project_box.clear()
            # 「未分类」（系统工作区）必须排第一：`_set_combo_data` 找不到条目时会落到
            # 第 0 项，而第 0 项若不是未分类，一个项目已归档的任务就会被**静默改归**到
            # 磁盘顺序上的第一个项目。未分类经共享命令归一成 `None`（"没有长期归属"），
            # 所以这个落点是安全的。
            for project in sorted(projects or [], key=lambda item: not item.get("is_system")):
                # ⚠️ 项目名是**用户数据**，绝不能进 i18n：一个叫「标准」的项目会被
                # 静默改写成 "Standard"。只有兜底占位符是界面文案。
                self.project_box.addItem(
                    str(project.get("name") or i18n.tr('未命名项目')),
                    project.get("project_id"))
            index = self.project_box.findData(keep) if keep is not None else -1
            self.project_box.setCurrentIndex(max(index, 0))
        finally:
            self._loading = was_loading

    def add_project(self, project_id: str, name: str) -> bool:
        """把刚新建的项目加入选择器并选中它（window 建完之后调回来）。

        只动「归入项目」这一个下拉框。**不能**用 `load()` 刷新：那会把面板整体重建成
        任务的已保存选择，连带抹掉用户刚在策略/交付上做的调整。返回是否选中成功。
        """
        key = str(project_id or "").strip()
        if not key:
            return False
        index = self.project_box.findData(key)
        if index < 0:
            self.project_box.addItem(str(name or i18n.tr('未命名项目')), key)
            index = self.project_box.count() - 1
        self.project_box.setCurrentIndex(index)
        return True

    def _fill_combo(self, box: QComboBox, items: list[dict]) -> None:
        """把 ``{label, value}`` 目录填进下拉框：标签翻译、取值原样。"""
        box.clear()
        for item in items:
            box.addItem(i18n.tr(str(item.get("label") or item.get("value") or "")),
                        item.get("value"))

    def _build_delivery_boxes(self) -> None:
        """按目录重建三组交付复选框（重复调用只更新，不叠加）。"""
        for key, items_key in (("translation_items", "translation_items"),
                               ("asset_items", "asset_items"),
                               ("research_items", "research_items")):
            group = self._delivery_groups[key]
            group.clear()
            self._delivery_boxes = {
                k: v for k, v in self._delivery_boxes.items()
                if k not in {str(i.get("key")) for i in self._choices.get(items_key) or []}}
            for item in self._choices.get(items_key) or []:
                key_name = str(item.get("key") or "")
                label = i18n.tr(str(item.get("title") or key_name))
                fmt = str(item.get("format") or "")
                detail = i18n.tr(str(item.get("detail") or ""))
                box = components.Switch()
                box.setAccessibleName(f"{label}（{fmt}）" if fmt else label)
                box.setToolTip(detail)
                box.stateChanged.connect(
                    lambda _state, k=key_name: self._on_delivery_item_changed(k))
                self._delivery_boxes[key_name] = box
                icon_name = ("graduation" if key == "research_items"
                             else _FORMAT_ICONS.get(fmt.upper(), "file-text"))
                value = QLabel(fmt)
                value.setObjectName("rowValue")
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                group.add_row(_icon_row(icon_name, label, box, value=value, tooltip=detail))
        self._sync_docx_only()

    def _sync_docx_only(self) -> None:
        """仅适用于 DOCX 原文的交付项：其它来源禁用、取消勾选并说明原因。"""
        for items_key in ("translation_items", "asset_items"):
            for item in self._choices.get(items_key) or []:
                if not item.get("docx_only"):
                    continue
                box = self._delivery_boxes.get(str(item.get("key") or ""))
                if box is None:
                    continue
                detail = i18n.tr(str(item.get("detail") or ""))
                if self._is_docx:
                    box.setEnabled(True)
                    box.setToolTip(detail)
                else:
                    box.setChecked(False)
                    box.setEnabled(False)
                    box.setToolTip(i18n.tr('仅 DOCX 任务可用：需要原 DOCX 文件作为写回基础。'))

    def load(self, view: dict) -> None:
        """按视图回填控件（adapter.task_setup_view()）。"""
        self._view = dict(view or {})
        self._sync_profile_row()
        self._request_source_size(str(self._view.get("job_id") or ""))
        # 原文结构（#27，只读）：结构在后台线程本地解析，按源文件身份缓存。
        self.source_section.load(str(self._view.get("job_id") or ""),
                                  str(self._view.get("filename") or ""),
                                  self._view)
        self._loading = True
        try:
            # 左栏：这份文档本身（预览、页数、需要注意的地方）。
            self.overview.set_document(
                str(self._view.get("job_id") or ""), str(self._view.get("filename") or ""),
                source_lang=str(self._view.get("source_lang") or ""))
            # 文件名已经在工作台头部，这里只说"这一步要做什么"。
            self.title_label.setText(i18n.tr('译前准备'))
            self.subtitle_label.setText(i18n.tr('确认设置和要生成的文件，选择会保存到本任务。'))
            strategy = self._view.get("strategy") or {}
            self._set_combo_data(self.target_box, self._view.get("target_lang"))
            from folith import source_languages as _source_languages

            saved_source = _source_languages.match(self._view.get("source_lang"))
            index = self.source_box.findData(saved_source) if saved_source else 0
            self.source_box.setCurrentIndex(max(index, 0))
            # 任务里已经保存过原文语言 = 用户（或上次的准备）确认过，识别结果不再覆盖它。
            self._source_user_set = bool(saved_source)
            self.source_hint.setVisible(False)
            self._set_combo_data(self.project_box, self._view.get("project_id"))
            count = int(self._view.get("glossary_count") or 0)
            name = str(self._view.get("glossary_name") or "")
            if count and name:
                glossary_text = i18n.tr('{name} · {count} 条术语', name=name, count=count)
            elif count:
                glossary_text = i18n.tr('已添加 · {count} 条术语', count=count)
            else:
                # 取值只说状态；"可选、用于保持术语一致"在这一行的悬停提示里。
                glossary_text = i18n.tr('未使用')
            self.glossary_value.setText(glossary_text)
            self.glossary_value.setToolTip(glossary_text)
            self.glossary_add_button.setText(i18n.tr('更换…') if count else i18n.tr('添加…'))
            self.glossary_remove_button.setVisible(bool(count))
            literature = int(self._view.get("literature_count") or 0)
            names = [str(n) for n in self._view.get("literature_names") or []]
            if literature:
                label = names[0] if len(names) == 1 else (
                    i18n.tr('{name} 等 {count} 个文件', name=names[0], count=len(names))
                    if names else "")
                self.literature_value.setText(
                    i18n.tr('{label} · {count} 条来源', label=label, count=literature)
                    if label else i18n.tr('{count} 条来源', count=literature))
            else:
                self.literature_value.setText(i18n.tr('未添加（可选）'))
            self.literature_remove_button.setVisible(bool(literature))

            self.understanding_box.setChecked(bool(strategy.get("enable_understanding")))
            self.auto_term_box.setChecked(bool(strategy.get("auto_term")))
            self.tm_box.setChecked(bool(strategy.get("use_tm")))
            self.review_box.setChecked(bool(strategy.get("enable_review")))
            self.strict_terms_box.setChecked(
                bool(strategy.get("strict_terminology_governance")))
            self.knowledge_box.setChecked(bool(strategy.get("knowledge_feedback", True)))
            self._set_combo_data(self.concurrency_box,
                                 int(strategy.get("translation_concurrency") or 4))
            self.seg_mode_seg.set_value(
                str(strategy.get("segmentation_mode") or "sentence"))
            self.batch_seg.set_value(str(strategy.get("batch_profile") or "保守"))
            self.mode_selector.set_value(str(self._view.get("strategy_preset") or ""))

            self._delivery = dict(self._view.get("delivery") or {})
            for key, box in self._delivery_boxes.items():
                box.setChecked(bool(self._delivery.get(key)))
            self._normalise_delivery_child()
            self.delivery_seg.set_value(
                str(self._view.get("delivery_preset") or "custom"))
            self._sync_delivery_preset_label()
            self._set_combo_data(self.theory_box, self._view.get("translation_theory"))

            is_pdf = bool(self._view.get("is_pdf"))
            self._is_pdf = is_pdf
            self._is_docx = bool(self._view.get("is_docx"))
            self._sync_docx_only()
            if is_pdf:
                ocr = self._view.get("ocr") or {}
                self._set_combo_data(self.ocr_language_box, ocr.get("language"))
                self._set_combo_data(self.ocr_mode_box,
                                     _normalize_ocr_mode(ocr.get("mode")))
                self._set_combo_data(self.ocr_backend_box,
                                     ocr.get("backend") or "online")
                self._set_combo_data(self.ocr_quality_box,
                                     ocr.get("quality") or "standard")
            self._sync_ocr_visibility()
            self._sync_strategy_note()
            self._sync_batch_hint()
            self._sync_research_visibility()
            self._sync_delivery_summary()
        finally:
            self._loading = False

    def _set_combo_data(self, box: QComboBox, value) -> None:
        """按**取值**选中条目；未知取值直接显示（可编辑下拉框保留用户输入）。"""
        index = box.findData(value)
        if index >= 0:
            box.setCurrentIndex(index)
            return
        if box.isEditable():
            box.setEditText("" if value is None else str(value))
        elif box.count():
            box.setCurrentIndex(0)

    # ================= 交互 =================

    def _on_strategy_preset(self, key: str) -> None:
        """选一个翻译模式 = 把该模式的模板套上去。

        ⚠️ **只改模式自己拥有的那组字段。** 交付方案是另一套预设（「交付内容」有自己的
        方案选择），两者是各自独立的用户决定：换模式顺手把用户挑好的 DOCX / PDF /
        EPUB / 术语产物重置掉，等于让一个决定悄悄覆盖另一个决定。

        这条与 Web 向导**不同**，差异是有理由的：Web 的 `_apply_preset` 会把交付重置为
        标准交付，因为那边的交付是**下一步**、当时还不存在用户的决定；桌面面板把两个
        决定放在同一屏，用户改完交付再回头换模式时，交付必须留着。

        初始默认值仍然按产品规则给出（新任务开在 标准 + 标准交付）—— 那是
        `adapter.task_setup_view` 的 pending 分支，不是这里。"默认值"与"用户改过的值"
        是两件事，这条修复守的是后者。
        """
        if self._loading:
            return
        preset = self._preset_config(key)
        if not preset:
            return
        self._loading = True
        try:
            self._apply_strategy_config(preset)
        finally:
            self._loading = False
        self._sync_strategy_note()

    def _apply_strategy_config(self, preset: dict) -> None:
        """把预设模板套到各项控件上（调用方负责 ``_loading`` 与收尾刷新）。"""
        self.auto_term_box.setChecked(bool(preset.get("auto_term")))
        self.tm_box.setChecked(bool(preset.get("use_tm")))
        self.review_box.setChecked(bool(preset.get("enable_review")))
        self.understanding_box.setChecked(bool(preset.get("enable_understanding")))
        self.strict_terms_box.setChecked(
            bool(preset.get("strict_terminology_governance")))
        self.knowledge_box.setChecked(bool(preset.get("knowledge_feedback", True)))
        self.seg_mode_seg.set_value(str(preset.get("segmentation_mode") or "sentence"))
        self._set_combo_data(self.concurrency_box,
                             int(preset.get("translation_concurrency") or 4))
        self.batch_seg.set_value(str(preset.get("batch_profile") or "保守"))
        self._sync_batch_hint()
        self._sync_research_visibility()
        self._sync_document_summary()

    def _restore_strategy_preset(self) -> None:
        """把当前模式的默认配置重新套回去。

        **只动策略**：交付方案是另一套预设（「交付内容」有自己的方案选择），
        一个"恢复本模式默认"的动作不该顺手改掉用户挑好的产物清单。
        """
        preset = self._preset_config(self.mode_selector.value())
        if not preset:
            self._sync_strategy_note()
            return
        self._loading = True
        try:
            self._apply_strategy_config(preset)
        finally:
            self._loading = False
        self._sync_strategy_note()

    def _on_delivery_preset(self, key: str) -> None:
        if self._loading or key == "custom":
            return
        config = dict((self._choices.get("delivery_preset_configs") or {}).get(key) or {})
        if not config:
            return
        self._loading = True
        try:
            self._apply_delivery_config(config)
        finally:
            self._loading = False

    def _apply_delivery_config(self, config: dict) -> None:
        for key, box in self._delivery_boxes.items():
            box.setChecked(bool(config.get(key)))
        self._delivery = dict(config)
        self._normalise_delivery_child()
        self._sync_research_visibility()
        self._sync_delivery_summary()

    def _normalise_delivery_child(self) -> None:
        """子项的取值必须跟着父项走：「标记术语与翻译难点」属于「双语对照」。

        父项没勾时它换不来任何产物（``payload()`` 与 ``_sync_delivery_summary()`` 都按这条
        办），所以**显示**也必须跟着清掉 —— 否则就是一个"勾着却什么都不会发生"的复选框。
        Web 端把这一项置灰（``app.py:610``），桌面端没有禁用态样式，所以这里跟
        :meth:`_on_delivery_item_changed` 用同一条规则：**清掉**。

        三个入口（回填 / 勾选 / 套用交付方案）都调它，避免只对齐其中一处 —— `load()` 以前
        就是不调的那一处，从别处带进来的 ``enable_annotate=True`` + 双语关闭会原样显示。
        """
        annotated = self._delivery_boxes.get("enable_annotate")
        parent = self._delivery_boxes.get("deliver_bilingual_docx")
        if annotated is None or parent is None:
            return
        if not parent.isChecked() and annotated.isChecked():
            annotated.setChecked(False)
        self._delivery["enable_annotate"] = bool(annotated.isChecked())

    def _on_delivery_item_changed(self, key: str) -> None:
        if self._loading:
            return
        box = self._delivery_boxes.get(key)
        if box is None:
            return
        self._delivery[key] = bool(box.isChecked())
        self._loading = True
        try:
            # `_loading` 先立起来：下面清子项时会再发一次 stateChanged，
            # 那条信号必须被忽略，否则这套刷新会跑两遍。
            self._normalise_delivery_child()
            self._sync_delivery_preset_label()
            self._sync_research_visibility()
            self._sync_delivery_summary()
        finally:
            self._loading = False

    def _sync_delivery_preset_label(self) -> None:
        """勾选与预设不一致时，把方案切到「自定义」（与 Web 端一致）。"""
        current = self.delivery_seg.value()
        if current and current != "custom" and self._matches_delivery_preset(current):
            return
        for preset in (self._choices.get("delivery_presets") or []):
            key = str(preset["key"])
            if key != "custom" and self._matches_delivery_preset(key):
                self.delivery_seg.set_value(key)
                return
        if self.delivery_seg.button("custom") is not None:
            self.delivery_seg.set_value("custom")

    def _matches_delivery_preset(self, key: str) -> bool:
        config = (self._choices.get("delivery_preset_configs") or {}).get(key)
        if not config:
            return False
        return all(bool(self._delivery.get(name)) == bool(value)
                   for name, value in config.items())

    def _sync_mode_summary(self) -> None:
        """翻译模式下方的一句话：当前配置实际会做哪几步（读真实开关，不读模式名）。"""
        if not hasattr(self, "mode_summary"):
            return
        included, excluded = [], []
        for _icon_name, label, flag in _MODE_STEPS:
            (included if self._strategy_value(flag) else excluded).append(i18n.tr(label))
        text = "、".join(included)
        if excluded:
            text = (text + " · " if text else "") + i18n.tr('不含{v0}', v0="、".join(excluded))
        # 按**当前配置**（含自定义）估这份文档的成本（acceptance-feedback 13.6-3）。
        estimate = self._current_estimate()
        if estimate.get("calls"):
            line = i18n.tr('预估：约 {v0} 次模型调用 · 输入约 {v1} token · {v2}',
                           v0=format(estimate["calls"], ","),
                           v1=_compact_count(estimate["input_tokens"]),
                           v2=_duration(estimate["minutes"]))
            base = adapter.strategy_estimates(self._size).get("标准") or {}
            standard = self._preset_config("标准")
            if base.get("input_tokens") and any(
                    self._strategy_value(key) != value for key, value in standard.items()
                    if key in _MANAGED_STRATEGY_KEYS):
                line += i18n.tr('（约为「标准」的 {v0} 倍）', v0=_ratio(
                    estimate["input_tokens"] / base["input_tokens"]))
            text += "\n" + line
        self.mode_summary.setText(text)
        if self._size:
            tip = i18n.tr('按本文约 {v0} 段、{v1} 字符和基准实测比例估算，只计输入 token；'
                          '术语多、上下文长时实际会更高。',
                          v0=format(self._size.get("segments", 0), ","),
                          v1=format(self._size.get("chars", 0), ","))
            if self._size.get("approximate"):
                tip += i18n.tr('扫描页按每页约 1,800 字估算。')
            self.mode_summary.setToolTip(tip)
        else:
            self.mode_summary.setToolTip("")

    # ---- 模式成本预估 ----------------------------------------------------------
    def refresh_scope_estimate(self, job_id: str) -> None:
        """范围修改后，同一任务也必须重新读取预估；普通轮询仍复用缓存。"""
        if job_id != str(self._view.get("job_id") or ""):
            return
        self._size_job = ""
        self._request_source_size(job_id)

    def _request_source_size(self, job_id: str) -> None:
        if job_id == self._size_job:
            return
        self._size_job = job_id
        self._size = {}
        self.mode_selector.set_estimates({})
        if not job_id or self._view.get("started"):
            return
        self._size_runner.submit(
            adapter.source_size, job_id, label="source-size",
            on_done=lambda size, job=job_id: self._source_size_ready(job, size),
            on_error=lambda *_args: None)

    def _source_size_ready(self, job_id: str, size) -> None:
        if job_id != self._size_job:
            return  # the panel moved on to another task meanwhile
        self._size = dict(size or {})
        self.mode_selector.set_estimates({
            key: (i18n.tr('{v0} token', v0=_compact_count(value["input_tokens"])),
                  _duration(value["minutes"]),
                  i18n.tr('约 {v0} 次调用', v0=format(value["calls"], ",")))
            for key, value in adapter.strategy_estimates(self._size).items()
            if value.get("calls")})
        self._sync_mode_summary()

    def _current_estimate(self) -> dict:
        if not self._size:
            return {}
        strategy = {key: self._strategy_value(key) for key in _MANAGED_STRATEGY_KEYS}
        return adapter.strategy_estimate(strategy, self._size.get("segments", 0),
                                         self._size.get("chars", 0))

    def source_size(self) -> dict:
        return dict(self._size)

    def _sync_strategy_note(self) -> None:
        """自定义状态：由**实际生效的配置**推出来，不是"某个控件响过信号"。

        差异只在 :data:`_MANAGED_STRATEGY_KEYS`（这个面板真的暴露的字段）上比较：
        预设里若多出一个面板没有的开关，把它算进来会让状态常年显示"已自定义"。
        """
        self._sync_mode_summary()
        key = self.mode_selector.value()
        preset = self._preset_config(key)
        if not preset:
            # 配置对应不上任何预设（任务是从别处带配置进来的）：不假装停在某个模式上。
            self.strategy_note.setText(
                i18n.tr('当前配置不对应任何预设；可直接调整下面的选项。'))
            self.restore_button.setVisible(False)
            self.strategy_status_row.setVisible(True)
            return
        label = self._preset_label(key)
        changed = sum(1 for name in _MANAGED_STRATEGY_KEYS
                      if name in preset and self._strategy_value(name) != preset[name])
        if changed:
            self.strategy_note.setText(
                i18n.tr('{label}模式 · 已自定义', label=label))
            self.restore_button.setText(i18n.tr('恢复「{label}」默认', label=label))
            self.restore_button.setVisible(True)
            self.strategy_status_row.setVisible(True)
        else:
            self.strategy_note.setText(
                i18n.tr('当前使用「{label}」默认配置', label=label))
            self.restore_button.setVisible(False)
            # 默认配置不需要再说一遍：选中的模式本身已经说明了。
            self.strategy_status_row.setVisible(False)

    def _on_advanced_changed(self, *_args) -> None:
        if self._loading:
            return
        self._sync_strategy_note()

    def _on_understanding_changed(self, *_args) -> None:
        """「建立文档画像」有两个后果：它属于策略模板（更新"已自定义"），
        也决定「文档识别」的折叠摘要。两件事都做，别只做一半。"""
        self._sync_document_summary()
        self._on_advanced_changed()

    def _strategy_value(self, key: str):
        return {
            "auto_term": self.auto_term_box.isChecked(),
            "use_tm": self.tm_box.isChecked(),
            "enable_understanding": self.understanding_box.isChecked(),
            "enable_review": self.review_box.isChecked(),
            "strict_terminology_governance": self.strict_terms_box.isChecked(),
            "segmentation_mode": self.seg_mode_seg.value(),
            "translation_concurrency": int(self.concurrency_box.currentData() or 4),
            "batch_profile": self.batch_seg.value(),
            "knowledge_feedback": self.knowledge_box.isChecked(),
        }.get(key)

    def _sync_batch_hint(self) -> None:
        profile = str(self.batch_seg.value() or "保守")
        hint = ""
        for item in self._choices.get("batch_profiles") or []:
            if str(item.get("key")) == profile:
                hint = str(item.get("hint") or "")
                break
        self.batch_hint.setText(
            i18n.tr('{hint}。大文档建议先用「均衡」跑一章确认质量。',
                    hint=i18n.tr(hint)) if hint else "")

    def _is_local_ocr(self) -> bool:
        """提取来源是不是本机 OCR（决定 `识别精度` 有没有意义、要不要警示语言）。"""
        return str(self.ocr_backend_box.currentData() or "online") \
            in ("tesseract", "paddle")

    def _sync_ocr_visibility(self) -> None:
        """按「提取来源」决定扫描件这一块显示哪几项、说明怎么写。

        两处刻意的不对称：

        * **`识别精度` 只在本机 OCR 时出现。** 视觉理解路径在 core 里固定 `dpi=150`
          （`core.py:2336`），本机路径才读 `ocr_dpi`（`core.py:2384`）。把 220/300/350
          留在默认路径上，就是一个"转了没反应"的旋钮——比缺一个选项更糟。
        * **`识别语言` 不隐藏，只换说明。** 两条路径都用得到它，但含义不同（语义识别
          语言 vs. 本机定位证据的语言），隐藏会让视觉理解下的定位质量没法调。
        """
        self.ocr_box.setVisible(self._is_pdf)
        local = self._is_local_ocr()
        self.ocr_quality_field.setVisible(local)
        if self.ocr_language_hint is not None:
            self.ocr_language_hint.setText(i18n.tr(_OCR_LANGUAGE_HINTS[local]))
        self.ocr_hint.setText(i18n.tr(_OCR_SOURCE_HINTS[local]))
        self._sync_document_summary()

    def _sync_document_summary(self) -> None:
        """「文档识别」收起时的那行答案 + 可执行的警示。

        摘要回答"这个文档现在按什么方式读"：``来源 · 时机 [· 含文档画像]``。非 PDF
        没有扫描路径，就直接说"文本层解析"——不要拿一个不生效的来源去糊。

        警示挂在折叠头右侧，**收起状态下同样可见**：选本机 OCR 却没指定识别语言时，
        核心会静默退回英语（`core.py:1344` / `core.py:1370` 的 `ocr_language or "eng"`），
        不提醒的话用户要到译文出来才发现。这种"可执行的错误"不能关在折叠体里。
        """
        # 说效果，不说实现：用户关心的是"扫描页能不能读、版面会不会被理解"，
        # 不是"视觉理解 · 自动判断"这种技术选项名（技术名在展开后的控件里）。
        mode = str(self.ocr_mode_box.currentData() or "auto")
        if not self._is_pdf:
            text = i18n.tr('直接读取正文结构')
        elif mode == "off":
            text = i18n.tr('只读取文字层')
        elif self._is_local_ocr():
            text = (i18n.tr('每页都用本机 OCR 识别') if mode == "force"
                    else i18n.tr('扫描页用本机 OCR 识别'))
        else:
            text = (i18n.tr('每页都按版面理解') if mode == "force"
                    else i18n.tr('按版面理解页面，扫描页也能读取'))
        self.doc_section.set_summary(text)

        warning = ""
        if self._is_pdf and self._is_local_ocr() and not str(
                self.ocr_language_box.currentData() or "").strip():
            warning = i18n.tr('未指定识别语言，本机 OCR 会退回英语')
        self.doc_section.set_warning(warning)

    def _sync_research_visibility(self) -> None:
        """研究产物只在研究级策略下出现（与 Web 端的 `_research_outputs_visible` 一致）。"""
        visible = False
        for item in self._choices.get("research_strategy_presets") or []:
            if str(item) == self.mode_selector.value():
                visible = True
                break
        visible = visible or self._report_box_checked()
        self._research_visible = bool(visible)
        self.research_group.setVisible(visible)
        self.theory_field.setVisible(bool(visible and self._report_box_checked()))
        self.literature_field.setVisible(bool(visible and self._report_box_checked()))
        self._sync_delivery_summary()

    def _sync_delivery_summary(self) -> None:
        """一行说清"这套勾选实际会产出什么"（与 Web 端的文件汇总同一口径）。

        它让「交付方案」与下面的勾选对得上：方案只是快捷入口，勾选才是事实 ——
        两者不一致时 `_sync_delivery_preset_label` 会把方案切到「自定义」，
        这一行说的是**事实**。
        """
        counts: dict[str, int] = {}
        total = 0
        for items_key in ("translation_items", "asset_items", "research_items"):
            if items_key == "research_items" and not self._research_visible:
                continue
            for item in self._choices.get(items_key) or []:
                key = str(item.get("key") or "")
                box = self._delivery_boxes.get(key)
                if box is None or not box.isChecked():
                    continue
                # 标记术语是双语对照的子项：父项没勾就不算产物（与 Web 端一致）。
                if key == "enable_annotate":
                    parent = self._delivery_boxes.get("deliver_bilingual_docx")
                    if parent is None or not parent.isChecked():
                        continue
                for kind, amount in item.get("file_units") or ():
                    counts[str(kind)] = counts.get(str(kind), 0) + int(amount)
                    total += int(amount)
        if total:
            text = i18n.tr('将生成 {count} 个文件', count=total)
            parts = " · ".join(f"{kind} ×{amount}"
                               for kind, amount in counts.items())
            if parts:
                text += " · " + parts
        else:
            text = i18n.tr('尚未选择交付文件')
        self.delivery_summary.setText(text)
        # 交付行收起时也说清楚会得到哪些文件（用文件名，不用方案名）。
        names = []
        for items_key in ("translation_items", "asset_items", "research_items"):
            if items_key == "research_items" and not self._research_visible:
                continue
            for item in self._choices.get(items_key) or []:
                box = self._delivery_boxes.get(str(item.get("key") or ""))
                if box is not None and box.isChecked():
                    if item.get("key") == "enable_annotate" and not (
                            self._delivery_boxes.get("deliver_bilingual_docx")
                            and self._delivery_boxes["deliver_bilingual_docx"].isChecked()):
                        continue
                    names.append(i18n.tr(str(item.get("title") or item.get("key"))))
        if hasattr(self, "delivery_section"):
            self.delivery_section.set_summary(
                " · ".join(names) if names else i18n.tr('尚未选择交付文件'))

    def _report_box_checked(self) -> bool:
        box = self._delivery_boxes.get("enable_report")
        return bool(box is not None and box.isChecked())

    # ================= 输出 =================

    def payload(self) -> dict:
        """面板当前选择的纯值（交给 adapter.apply_task_setup）。"""
        delivery = dict(self._delivery)
        for key, box in self._delivery_boxes.items():
            delivery[key] = bool(box.isChecked())
        if not delivery.get("deliver_bilingual_docx"):
            delivery["enable_annotate"] = False
        if not self._is_docx:
            delivery["deliver_original_docx"] = False
        theory = self.theory_box.currentData()
        if theory is None:
            theory = str(self._view.get("translation_theory") or "")
        return {
            "filename": str(self._view.get("filename") or ""),
            "is_pdf": bool(self._view.get("is_pdf")),
            "target_lang": self.target_box.currentText().strip(),
            "source_lang": str(self.source_box.currentData() or ""),
            "project_id": self.project_box.currentData(),
            "strategy_preset": str(self.mode_selector.value() or ""),
            "strategy": {
                "auto_term": self.auto_term_box.isChecked(),
                "use_tm": self.tm_box.isChecked(),
                "enable_understanding": self.understanding_box.isChecked(),
                "enable_review": self.review_box.isChecked(),
                "strict_terminology_governance": self.strict_terms_box.isChecked(),
                "segmentation_mode": str(self.seg_mode_seg.value() or "sentence"),
                "translation_concurrency": int(self.concurrency_box.currentData() or 4),
                "batch_profile": str(self.batch_seg.value() or "保守"),
                "knowledge_feedback": self.knowledge_box.isChecked(),
            },
            "delivery": delivery,
            "translation_theory": str(theory),
            "style_rules": (adapter.style_rules_from_selection(self.style_selection())
                            if self.style_selection() else str(self._view.get("style_rules") or "")),
            "style_selection": self.style_selection(),
            "ocr": {
                "language": str(self.ocr_language_box.currentData() or ""),
                "mode": str(self.ocr_mode_box.currentData() or "auto"),
                "backend": str(self.ocr_backend_box.currentData() or "online"),
                "quality": str(self.ocr_quality_box.currentData() or "standard"),
            },
        }

    # ================= 小工具 =================

    def _preset_config(self, key: str) -> dict:
        for preset in self._choices.get("strategy_presets") or []:
            if str(preset["key"]) == key:
                return dict(preset.get("config") or {})
        return {}

    def _preset_label(self, key: str) -> str:
        for preset in self._choices.get("strategy_presets") or []:
            if str(preset["key"]) == key:
                # 显示名走 i18n；模式**键**（key）永远是内部取值，不进这里。
                return i18n.tr(str(preset.get("label") or key))
        return key

    # 供 window 使用：只读地暴露"是否允许开始"
    def is_ready(self) -> bool:
        return bool(self.target_box.currentText().strip())


def _normalize_ocr_mode(value) -> str:
    text = str(value or "").strip()
    return text or "auto"


def _fallback_ocr_languages() -> tuple[tuple[str, str], ...]:
    return (("自动：简体中文 + English", "chi_sim+eng"),
            ("简体中文", "chi_sim"), ("English", "eng"), ("العربية", "ara"))
