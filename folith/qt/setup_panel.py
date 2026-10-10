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
from . import i18n
from . import icons
from . import style as style_module
from .style import Card
from . import theme as theme_module

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

#: 面板能改的策略字段。预设之间的差异**只在这些字段上比较** —— 预设里若有这个
#: 面板没暴露的键（未来的新开关），把它算成"用户改过"会让"已自定义"常年亮着。
_MANAGED_STRATEGY_KEYS: tuple[str, ...] = (
    "auto_term", "use_tm", "enable_understanding", "enable_review",
    "strict_terminology_governance", "segmentation_mode",
    "translation_concurrency", "batch_profile",
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

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.button = QPushButton()
        self.button.setObjectName("disclosureHeader")
        self.button.setCheckable(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.setAccessibleName(title)
        self.title_label = style_module.SectionLabel(title)
        self.summary_label = ElidedLabel("")
        self.summary_label.setObjectName("hint")
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("statusError")
        self.warning_label.setVisible(False)
        self.chevron = QLabel()
        self.chevron.setFixedSize(13, 13)

        row = QHBoxLayout(self.button)
        # 左右边距为 0：折叠头里的标题要和卡片里其它区块标题共享同一条左基线
        # （内缩 4px 就看得出参差），悬停高亮也因此铺满整行宽（与其它设置行同宽）。
        # 上下留一点，别让高亮贴着字。
        row.setContentsMargins(0, theme_module.SPACE["xxs"],
                               0, theme_module.SPACE["xxs"])
        row.setSpacing(theme_module.SPACE["sm"])
        row.addWidget(self.title_label)
        row.addWidget(self.summary_label, 1)
        row.addWidget(self.warning_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(theme_module.SPACE["sm"])

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        column.addWidget(self.button)
        column.addWidget(self.body)

        self.button.toggled.connect(self._sync)
        self._sync(False)

    def _sync(self, expanded: bool) -> None:
        self.body.setVisible(bool(expanded))
        self.chevron.setPixmap(icons.pixmap(
            "chevron-up" if expanded else "chevron-down", 13,
            style_module.text_color("text-muted")))
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


class PresetOption(QPushButton):
    """一个翻译模式选项：图标 + 标题 + 一行摘要。

    ⚠️ ``QPushButton`` 的 ``sizeHint`` 是按**文字/图标**算的，看不见里面的布局 ——
    所以必须自己覆盖 ``sizeHint``/``minimumSizeHint``，否则三个选项会被压到
    "一个字都没写"的高度（内容全被裁掉）。同理 QSS 只管盒子，内边距在代码里给。

    两个标签都是 :class:`ElidedLabel`：一行说明在窄处省略而不是被切掉。
    """

    def __init__(self, key: str, title: str, description: str, icon_name: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("presetOption")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(132)
        # 无障碍名走 accessibleName：按钮自己的文字永远是空的（文案由内部的两个
        # 标签承担），读屏与自动化却仍能读到这个选项叫什么。
        self.setAccessibleName(title)
        self.key = str(key)
        self._icon_name = icon_name

        column = QVBoxLayout(self)
        column.setContentsMargins(theme_module.SPACE["sm"] + 2, theme_module.SPACE["sm"],
                                  theme_module.SPACE["sm"] + 2, theme_module.SPACE["sm"])
        column.setSpacing(theme_module.SPACE["xxs"])
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme_module.SPACE["xs"] + 2)
        self._icon = QLabel(self)
        self._icon.setFixedSize(15, 15)
        self._title = ElidedLabel(title, self)
        self._title.setObjectName("presetTitle")
        head.addWidget(self._icon)
        head.addWidget(self._title, 1)
        self._description = ElidedLabel(description, self)
        self._description.setObjectName("presetDesc")
        column.addLayout(head)
        column.addWidget(self._description)
        self.toggled.connect(self._sync_icon)
        self._sync_icon(False)
        self.setToolTip(f"{title} · {description}" if description else title)

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
        token = "accent" if checked else "text-secondary"
        self._icon.setPixmap(icons.pixmap(
            self._icon_name, 15, style_module.text_color(token)))


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
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(theme_module.SPACE["sm"])

    def set_options(self, options: list[dict]) -> None:
        """按目录重建选项（``set_choices`` 时调一次）。

        每项是 ``{"key", "label", "description"}``；图标名由本模块的
        :data:`_MODE_ICONS` 决定（表现层的事，不进共享模块）。
        """
        for button in self._options.values():
            self._group.removeButton(button)
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
            button = PresetOption(
                key, i18n.tr(str(item.get("label") or key)),
                i18n.tr(str(item.get("description") or "")),
                _MODE_ICONS.get(key, "check"))
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

    def options(self) -> tuple[str, ...]:
        return tuple(self._options)


class TaskSetupPanel(QWidget):
    """译前准备：输入与上下文 / 翻译设置 / 交付内容 / 开始翻译。"""

    startRequested = Signal(dict)
    #: 「归入项目」旁边的新建入口。面板自己**不建**项目：创建会写盘、要弹名称输入框、
    #: 还要刷新首页的项目列表，那些都是 window 的职责（它持有对话框与其它页面）。
    #: 面板只负责发出意图，再由 window 调 `add_project()` 把结果填回来。
    createProjectRequested = Signal()
    termbaseAddRequested = Signal()
    styleProfileRequested = Signal()
    termbaseRemoveRequested = Signal()

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
        self._loading = False
        #: 当前任务是不是 PDF。扫描件识别整块只对 PDF 出现（见 `_sync_ocr_visibility`）。
        self._is_pdf = False
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

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["md"])
        column.addLayout(head)
        column.addWidget(self._build_context_card())
        column.addWidget(self._build_strategy_card())
        column.addWidget(self._build_delivery_card())
        column.addWidget(self._build_footer())
        column.addStretch(1)

        # 高级项改动后重新计算"与预设相差几项"，并联动研究产物的可见性。
        self.auto_term_box.stateChanged.connect(self._on_advanced_changed)
        self.tm_box.stateChanged.connect(self._on_advanced_changed)
        self.review_box.stateChanged.connect(self._on_advanced_changed)
        self.strict_terms_box.stateChanged.connect(self._on_advanced_changed)
        # 「建立文档画像」既是策略模板的一份子（要更新"已自定义"），也决定折叠摘要
        # 里的那段话，所以走一个同时管两件事的槽。
        self.understanding_box.stateChanged.connect(self._on_understanding_changed)
        self.seg_mode_seg.changed.connect(self._on_advanced_changed)
        self.concurrency_box.currentIndexChanged.connect(self._on_advanced_changed)
        self.batch_seg.changed.connect(self._on_advanced_changed)

    # ================= 构建 =================

    def _build_context_card(self) -> Card:
        """输入与上下文：两个视觉组。

        ① **主要设置**（目标语言 / 归入项目 / 术语库）—— 每次导入都要确认的决定，
           常驻可见，宽度够时两列并排；
        ② **文档识别**（:class:`DisclosureSection`，默认收起）—— "这个文档怎么读成
           文字"的整组参数。收起时标题右侧的摘要已经把答案写出来了
           （``视觉理解 · 自动判断``），要改的人再展开。

        解释文字一律收进控件下方的一行或 tooltip：这一块曾经在普通选择器下面堆了
        三段说明，把三项主要设置挤到了首屏之外。
        """
        card, body = _panel(i18n.tr('输入与上下文'))

        self.target_box = style_module.ComboBox()
        self.target_box.setEditable(True)
        self.target_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Fixed)

        self.project_box = style_module.ComboBox()
        self.project_box.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)

        pair = QWidget()
        pair_layout = QGridLayout(pair)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setHorizontalSpacing(theme_module.SPACE["md"])
        pair_layout.addWidget(_field(i18n.tr('目标语言'), self.target_box), 0, 0)
        pair_layout.addWidget(_field(i18n.tr('归入项目'), self._build_project_row()), 0, 1)
        pair_layout.setColumnStretch(0, 1)
        pair_layout.setColumnStretch(1, 1)
        body.addWidget(pair)

        body.addWidget(self._build_glossary_row())
        body.addWidget(_hairline())
        self.doc_section = self._build_document_section()
        body.addWidget(self.doc_section)
        return card

    def _build_glossary_row(self) -> QWidget:
        """术语库：一行只读事实（标签在左、取值在右）。

        说明是"为什么只读"的上下文帮助，放进 tooltip —— 它不该再占一行，把主要
        设置往下推。取值用 :class:`ElidedLabel`：术语库名可以很长，硬裁会像排版坏了。
        """
        hint = i18n.tr('可选：添加术语库（Excel / CSV / TBX）或翻译记忆（TMX），用于保持术语与专名一致。')
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["md"])
        label = style_module.field_label(i18n.tr('术语库'))
        label.setToolTip(hint)
        self.glossary_value = ElidedLabel("—")
        self.glossary_value.setObjectName("hint")
        self.glossary_value.setToolTip(hint)
        self.glossary_add_button = QPushButton(i18n.tr('添加…'))
        self.glossary_add_button.setToolTip(
            i18n.tr('支持 Excel、CSV、TBX（术语）和 TMX（翻译记忆）。'))
        self.glossary_add_button.clicked.connect(self.termbaseAddRequested.emit)
        self.glossary_remove_button = QPushButton(i18n.tr('移除'))
        self.glossary_remove_button.clicked.connect(self.termbaseRemoveRequested.emit)
        self.glossary_remove_button.setVisible(False)
        layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.glossary_value, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.glossary_add_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.glossary_remove_button, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

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
        section = DisclosureSection(i18n.tr('文档识别'))

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
        self.new_project_button = QPushButton(i18n.tr('新建项目…'))
        self.new_project_button.setObjectName("subtleAction")
        self.new_project_button.setToolTip(
            i18n.tr('创建新项目，并把它选为本次任务的归属。'))
        self.new_project_button.clicked.connect(
            lambda: self.createProjectRequested.emit())
        layout.addWidget(self.new_project_button)
        return row

    def _build_strategy_card(self) -> Card:
        """翻译设置：翻译模式（主决定）→ 内容处理 → 高级设置（默认收起）。

        三层的分工是这一版的重点：**模式**决定用哪套模板，**内容处理**是每次翻译
        都要面对的两三个选择，**高级设置**是执行参数（审校、术语锁定、并发、调度），
        它们影响的是任务怎么跑、API 怎么用，而不是译文是否更准 —— 所以退到折叠区里，
        并且不复述"更快/更好"这种预设里并不成立的暗示。
        """
        card, body = _panel(i18n.tr('翻译设置'))

        # ---- Level 1：翻译模式 -------------------------------------------------
        body.addWidget(style_module.SectionLabel(i18n.tr('翻译模式')))
        self.mode_selector = ModeSelector()
        self.mode_selector.changed.connect(self._on_strategy_preset)
        body.addWidget(self.mode_selector)

        # 自定义状态：只在**实际生效的配置**与所选预设不同时亮出来，
        # 并且同时给出"退回该预设默认值"的出口（否则用户只能靠记忆手动改回去）。
        self.strategy_note = QLabel("")
        self.strategy_note.setObjectName("hint")
        self.strategy_note.setWordWrap(True)
        self.restore_button = QPushButton(i18n.tr('恢复默认'))
        self.restore_button.setObjectName("linkAction")
        self.restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_button.clicked.connect(self._restore_strategy_preset)
        status_row = QWidget()
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(theme_module.SPACE["sm"])
        status_layout.addWidget(self.strategy_note, 1)
        status_layout.addWidget(self.restore_button, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addWidget(status_row)

        body.addWidget(_hairline())

        # ---- Level 2：内容处理 -------------------------------------------------
        body.addWidget(style_module.SectionLabel(i18n.tr('内容处理')))
        self.seg_mode_seg = style_module.SegmentedControl([
            ("sentence", i18n.tr('按句')),
            ("paragraph", i18n.tr('按段'))])
        self.seg_mode_seg.setToolTip(i18n.tr('按句提供主流 CAT 工具的逐句对照；'
                                            '按段保留清洗后的整段。'))
        body.addWidget(_setting_row(
            i18n.tr('文本切分方式'), self.seg_mode_seg,
            i18n.tr('决定翻译单元的大小')))

        self.auto_term_box = style_module.CheckBox("")
        self.tm_box = style_module.CheckBox("")
        body.addWidget(ToggleRow(
            i18n.tr('自动识别术语'), i18n.tr('提取关键术语，保持译名一致'),
            self.auto_term_box))
        body.addWidget(ToggleRow(
            i18n.tr('使用翻译记忆'), i18n.tr('参考已保存的历史译文'),
            self.tm_box))

        body.addWidget(_hairline())
        body.addWidget(self._build_style_section())
        body.addWidget(_hairline())

        # ---- Level 3：高级设置（默认收起） --------------------------------------
        # 与「文档识别」同一个组件：折叠头、摘要位、警示位、展开体都由它提供，
        # 面板只挂自己的属性名（测试与 window 都按这些名字访问）。
        self.advanced_section = DisclosureSection(
            i18n.tr('高级设置 · 审校与执行参数'))
        self.advanced_toggle = self.advanced_section.button
        self.advanced_label = self.advanced_section.title_label
        self.advanced_chevron = self.advanced_section.chevron
        self.advanced_body = self.advanced_section.body
        self.advanced_toggle.setAccessibleName(i18n.tr('高级设置'))
        self.advanced_toggle.setToolTip(i18n.tr('展开或收起审校与执行参数'))
        body.addWidget(self.advanced_section)
        advanced = self.advanced_body.layout()

        self.review_box = style_module.CheckBox("")
        self.strict_terms_box = style_module.CheckBox("")
        advanced.addWidget(ToggleRow(
            i18n.tr('译后自动审校'), i18n.tr('完成初译后进行额外检查'),
            self.review_box))
        advanced.addWidget(ToggleRow(
            i18n.tr('审定并锁定术语'), i18n.tr('审查候选术语，锁定已确认的译名'),
            self.strict_terms_box))

        self.concurrency_box = style_module.ComboBox()
        for value in CONCURRENCY_OPTIONS:
            self.concurrency_box.addItem(str(value), value)
        # 按内容宽度摆：它只有 1/2/4/6/8，拉满整行会读成"这里要填一段话"。
        self.concurrency_box.setSizePolicy(QSizePolicy.Policy.Fixed,
                                          QSizePolicy.Policy.Fixed)
        self.concurrency_box.setMinimumWidth(76)
        advanced.addWidget(_setting_row(
            i18n.tr('并发请求数'), self.concurrency_box,
            i18n.tr('同时向模型发出的批次请求数；影响速度与限频，不改变译文质量')))

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
        """翻译风格：基础风格 + 四个微调 + 自定义规则，或让模型抽样后给出推荐。

        不选风格时沿用设置里的风格规则（原来的行为）；选了就落成规则文本交给管线。
        """
        self.style_section = DisclosureSection(i18n.tr('翻译风格'))
        layout = self.style_section.body.layout()
        self._style_recommendation: dict = {}
        self.style_box = style_module.ComboBox()
        self.style_box.addItem(i18n.tr('沿用设置里的风格规则'), "")
        for choice in adapter.style_profile_choices():
            self.style_box.addItem(i18n.tr_dynamic(choice["name"]), choice["id"])
            self.style_box.setItemData(self.style_box.count() - 1,
                                       i18n.tr_dynamic(choice["summary"]), Qt.ItemDataRole.ToolTipRole)
        self.style_box.currentIndexChanged.connect(self._style_changed)
        layout.addWidget(_setting_row(
            i18n.tr('基础风格'), self.style_box,
            i18n.tr('选择后会覆盖设置里的默认风格规则；任务开始后随任务保存。')))

        self.style_sliders: dict[str, QSlider] = {}
        for key, caption, default in adapter.STYLE_ADJUSTMENTS:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setValue(default)
            slider.setMinimumWidth(180)
            slider.valueChanged.connect(self._style_changed)
            self.style_sliders[key] = slider
            layout.addWidget(_setting_row(i18n.tr(caption), slider))
        self.style_custom = QLineEdit()
        self.style_custom.setPlaceholderText(i18n.tr('补充风格约束（可选）'))
        self.style_custom.textChanged.connect(self._style_changed)
        layout.addWidget(self.style_custom)

        self.style_profile_button = QPushButton(i18n.tr('智能画像'))
        self.style_profile_button.setToolTip(
            i18n.tr('抽样读几段原文，让模型推荐适合的风格（需要一次模型调用）'))
        self.style_profile_button.clicked.connect(self.styleProfileRequested.emit)
        self.style_accept_button = QPushButton(i18n.tr('接受推荐'))
        self.style_accept_button.clicked.connect(self._accept_style_recommendation)
        self.style_accept_button.setVisible(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.style_profile_button)
        buttons.addWidget(self.style_accept_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.style_status = QLabel("")
        self.style_status.setObjectName("hint")
        self.style_status.setWordWrap(True)
        self.style_status.setVisible(False)
        layout.addWidget(self.style_status)
        self._style_changed()
        return self.style_section

    def _style_changed(self, *_args) -> None:
        chosen = bool(self.style_box.currentData())
        for slider in self.style_sliders.values():
            slider.setEnabled(chosen)
        self.style_custom.setEnabled(chosen)
        self.style_section.set_summary(
            self.style_box.currentText() if chosen else i18n.tr('沿用设置'))

    def style_selection(self) -> dict | None:
        """用户选定的风格（没选返回 ``None``，沿用设置里的规则）。"""
        base = str(self.style_box.currentData() or "")
        if not base:
            return None
        return {"selected": base, "source": "user_override",
                "adjustments": {key: slider.value() for key, slider in self.style_sliders.items()},
                "custom_rules": self.style_custom.text().strip()}

    def set_style_recommendation(self, result: dict) -> None:
        """窗口层把智能画像的结果交回来：显示理由，并给出「接受推荐」。"""
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
        # 失败降级的"通用风格"不是推荐，不给「接受」
        self.style_accept_button.setVisible(not failed)

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
            self.style_section.button.setChecked(True)

    def _build_delivery_card(self) -> Card:
        card, body = _panel(i18n.tr('交付内容'))

        self.delivery_seg = style_module.SegmentedControl([])
        self.delivery_seg.changed.connect(self._on_delivery_preset)
        # 与「翻译设置」里的行同构：分段控件按内容宽度摆在右侧，不拉满整行。
        # 四个满宽的分段看起来像工具栏，把"这是表单的一行"这件事盖掉了。
        body.addWidget(_setting_row(i18n.tr('交付方案'), self.delivery_seg))

        # 实时汇总：把"选了哪套方案"与"实际会产出什么"直接对上。方案是**快捷入口**，
        # 勾选才是事实，两者一旦不一致就换到「自定义」，这一行说的就是事实。
        self.delivery_summary = QLabel("")
        self.delivery_summary.setObjectName("hint")
        self.delivery_summary.setWordWrap(True)
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
        return card

    def _build_delivery_group(self, title: str, key: str) -> QWidget:
        """一组交付产物复选框（标题 + 竖排勾选）。"""
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["xs"])
        column.addWidget(style_module.SectionLabel(title))
        self._group_widgets[key] = holder
        return holder

    def _build_footer(self) -> QWidget:
        self.start_button = QPushButton(i18n.tr('开始翻译'))
        self.start_button.setObjectName("primaryAction")
        self.start_button.setIcon(icons.icon(
            "play", 16, style_module.text_color("text-on-accent")))
        self.start_button.setIconSize(QSize(16, 16))
        self.start_button.clicked.connect(
            lambda: self.startRequested.emit(self.payload()))
        self.footer_note = QLabel(i18n.tr('开始后可继续编辑译文；要生成的文件可在「交付」页查看。'))
        self.footer_note.setObjectName("hint")
        self.footer_note.setWordWrap(True)
        self.footer_bar = QWidget()
        row = QHBoxLayout(self.footer_bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_module.SPACE["md"])
        row.addWidget(self.footer_note, 1)
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
            self.project_box.clear()
            # 「未分类」（系统工作区）必须排第一：`_set_combo_data` 找不到条目时会落到
            # 第 0 项，而第 0 项若不是未分类，一个项目已归档的任务就会被**静默改归**到
            # 磁盘顺序上的第一个项目。未分类经共享命令归一成 `None`（"没有长期归属"），
            # 所以这个落点是安全的。
            for project in sorted(self._choices.get("projects") or [],
                                  key=lambda item: not item.get("is_system")):
                # ⚠️ 项目名是**用户数据**，绝不能进 i18n：一个叫「标准」的项目会被
                # 静默改写成 "Standard"。只有兜底占位符是界面文案。
                self.project_box.addItem(
                    str(project.get("name") or i18n.tr('未命名项目')),
                    project.get("project_id"))
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
            holder = self._group_widgets[key]
            column = holder.layout()
            while column.count() > 1:
                item = column.takeAt(column.count() - 1)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self._delivery_boxes = {
                k: v for k, v in self._delivery_boxes.items()
                if k not in {str(i.get("key")) for i in self._choices.get(items_key) or []}}
            for item in self._choices.get(items_key) or []:
                key_name = str(item.get("key") or "")
                label = i18n.tr(str(item.get("title") or key_name))
                fmt = str(item.get("format") or "")
                box = style_module.CheckBox(f"{label}（{fmt}）" if fmt else label)
                box.setToolTip(i18n.tr(str(item.get("detail") or "")))
                box.stateChanged.connect(
                    lambda _state, k=key_name: self._on_delivery_item_changed(k))
                self._delivery_boxes[key_name] = box
                column.addWidget(box)

    def load(self, view: dict) -> None:
        """按视图回填控件（adapter.task_setup_view()）。"""
        self._view = dict(view or {})
        self._loading = True
        try:
            # 文件名已经在工作台头部，这里只说"这一步要做什么"。
            self.title_label.setText(i18n.tr('译前准备'))
            self.subtitle_label.setText(i18n.tr('确认设置和要生成的文件，选择会保存到本任务。'))
            strategy = self._view.get("strategy") or {}
            self._set_combo_data(self.target_box, self._view.get("target_lang"))
            self._set_combo_data(self.project_box, self._view.get("project_id"))
            count = int(self._view.get("glossary_count") or 0)
            name = str(self._view.get("glossary_name") or "")
            if count and name:
                glossary_text = i18n.tr('{name} · {count} 条术语', name=name, count=count)
            elif count:
                glossary_text = i18n.tr('已添加 · {count} 条术语', count=count)
            else:
                glossary_text = i18n.tr('未使用（可选，用于保持术语一致）')
            self.glossary_value.setText(glossary_text)
            self.glossary_value.setToolTip(glossary_text)
            self.glossary_add_button.setText(i18n.tr('更换…') if count else i18n.tr('添加…'))
            self.glossary_remove_button.setVisible(bool(count))

            self.understanding_box.setChecked(bool(strategy.get("enable_understanding")))
            self.auto_term_box.setChecked(bool(strategy.get("auto_term")))
            self.tm_box.setChecked(bool(strategy.get("use_tm")))
            self.review_box.setChecked(bool(strategy.get("enable_review")))
            self.strict_terms_box.setChecked(
                bool(strategy.get("strict_terminology_governance")))
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

    def _sync_strategy_note(self) -> None:
        """自定义状态：由**实际生效的配置**推出来，不是"某个控件响过信号"。

        差异只在 :data:`_MANAGED_STRATEGY_KEYS`（这个面板真的暴露的字段）上比较：
        预设里若多出一个面板没有的开关，把它算进来会让状态常年显示"已自定义"。
        """
        key = self.mode_selector.value()
        preset = self._preset_config(key)
        if not preset:
            # 配置对应不上任何预设（任务是从别处带配置进来的）：不假装停在某个模式上。
            self.strategy_note.setText(
                i18n.tr('当前配置不对应任何预设；可直接调整下面的选项。'))
            self.restore_button.setVisible(False)
            return
        label = self._preset_label(key)
        changed = sum(1 for name in _MANAGED_STRATEGY_KEYS
                      if name in preset and self._strategy_value(name) != preset[name])
        if changed:
            self.strategy_note.setText(
                i18n.tr('{label}模式 · 已自定义', label=label))
            self.restore_button.setText(i18n.tr('恢复「{label}」默认', label=label))
            self.restore_button.setVisible(True)
        else:
            self.strategy_note.setText(
                i18n.tr('当前使用「{label}」默认配置', label=label))
            self.restore_button.setVisible(False)

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
        parts: list[str] = []
        if self._is_pdf:
            parts.append(_short_label(self.ocr_backend_box.currentText()))
            parts.append(_short_label(self.ocr_mode_box.currentText()))
        else:
            parts.append(i18n.tr('文本层解析'))
        if self.understanding_box.isChecked():
            parts.append(i18n.tr('含文档画像'))
        self.doc_section.set_summary(" · ".join(p for p in parts if p))

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
        theory = self.theory_box.currentData()
        if theory is None:
            theory = str(self._view.get("translation_theory") or "")
        return {
            "filename": str(self._view.get("filename") or ""),
            "is_pdf": bool(self._view.get("is_pdf")),
            "target_lang": self.target_box.currentText().strip(),
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


def _short_label(label: str) -> str:
    """把下拉框标签压成摘要里放得下的短名（取括号之前的部分）。

    目录里的标签带着补充说明，那是**选项列表**里需要的完整表述；折叠摘要只有一行。
    空标签原样返回，交给调用方过滤。
    """
    # 例：视觉理解标签 → 去掉括号里的补注；「总是识别（强制 OCR）」同理。
    # （写成注释而不是文档字符串：`test_the_stale_ocr_wording_is_gone_and_has_one_source`
    # 扫的是源码里的**用户可见字面量**，注释里引用旧文案是故意的、不算回归。）
    text = str(label or "").strip()
    for opener in ("（", " ("):
        if opener in text:
            text = text.split(opener, 1)[0].strip()
    return text or str(label or "").strip()


def _fallback_ocr_languages() -> tuple[tuple[str, str], ...]:
    return (("自动：简体中文 + English", "chi_sim+eng"),
            ("简体中文", "chi_sim"), ("English", "eng"), ("العربية", "ara"))
