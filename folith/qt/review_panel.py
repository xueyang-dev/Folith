"""审校页：队列 + 详情 + 人工处置。

旧界面里审校发现项可以由人逐条处理（应用建议、确认已解决、保留当前译文、
回看原页后确认原文）；Qt 工作台原来只在检查器里只读地列出它们，于是一条
误报就能永远挡住交付。这个面板把处置放回界面。

"确认问题存在"与"确认已解决"是两件事：前者只记录人的判断，发现项保持待处理、
仍挡住交付；后者才结束它。两个按钮不能合并成一个含混的动作。

右栏详情里内嵌一份 ``PdfPanel``（与翻译页同一个部件）：选中的项变了，原页就跟着翻到
那一段并高亮。人工判断"这段到底该不该改"时，原文的位置与它在整页里的上下文就在旁边，
不用先跳到翻译页去看。没有 PDF 原文的任务不显示这一列。

只做展示与发出信号：队列由 :func:`adapter.review_queue` 给出（复用
``folith.workbench_view``，不另写一套规则），真正写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QSizePolicy, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import adapter
from . import components
from . import i18n
from . import icons
from . import pdf_panel as pdf_panel_module
from . import style as style_module
# #289: 复用仓库里唯一已有的可展开行原语 IssueCard（「检查与修复」抽屉同款）。
# followup_panel 不 import 本模块，无循环依赖；本模块也不改它。
from .followup_panel import IssueCard
from .name_conflict import NameConflictWidget
from .style import Card

#: 左栏分组（xueyang-dev/Folith#102）：必须处理展开，其余默认收起。
#: #289 新增「自动处理（审计）」组：只在 QA triage 开关打开且有项时出现，
#: 默认收起；它是同一个复核页里的一组，不是新页面、不是新窗口，
#: 也不按操作系统分叉。
GROUP_ORDER = ("must", "suggested", "reference", "auto")
GROUP_LABELS = {
    "must": "必须处理",
    "suggested": "建议",
    "reference": "已处理 / 仅供参考",
    "auto": "自动处理（审计）",
}
GROUP_EXPANDED_BY_DEFAULT = {"must": True, "suggested": False, "reference": False,
                             "auto": False}

#: When only automatic-handling records exist, open the audit group so the pane
#: is not blank — but only up to this many; a large audit-only task keeps the
#: group collapsed (header count still visible) instead of building hundreds of
#: card widgets on open.
AUDIT_AUTO_EXPAND_LIMIT = 50

#: #289: 界面显示 actor_type（谁产出的结论），不显示 actor 字符串——
#: "qa_triage" 绝不能看起来像人工决定。
_ACTOR_TYPE_LABELS = {"rule": "规则", "model": "模型", "human": "人工"}

#: #289: disposition 的界面措辞；查不到的（旧式人工处置动作）原样显示代码名。
_DISPOSITION_LABELS = {
    "verified_safe": "证据核验安全",
    "auto_resolvable": "确定性自动修复",
    "needs_investigation": "需要调查",
    "human_required": "需人工决定",
    "blocking": "阻断",
    "human_fixed": "确认已解决",
    "preserved": "保留当前译文",
    "accepted_risk": "接受风险",
    "confirmed_open": "确认问题存在",
    "resolved": "已解决",
    "dismissed": "已驳回",
}

_SEVERITY_TONE = {"blocking": "danger", "actionable": "warn",
                  "informational": "info"}

#: 队列项里「需要重新审校」的几种合成项。
_REVIEW_TASK_KINDS = {"failed", "stale", "missing", "skipped"}

#: 术语卡详情里最多并排列出的段落数；再多就只报数。
TERM_ROWS_SHOWN = 40


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _term_card_title(item: dict) -> str:
    """术语卡的标题：术语、冻结译法、影响段数——不写某一处的段号。"""
    source = str(item.get("term_source") or "").strip()
    target = str(item.get("term_target") or "").strip()
    count = int(item.get("term_segment_total") or 0)
    if source and target:
        return i18n.tr('{v0} → {v1} · 影响 {v2} 段',
                       v0=source, v1=target, v2=count)
    if source:
        return i18n.tr('术语「{v0}」· 影响 {v1} 段', v0=source, v1=count)
    return i18n.tr_dynamic(str(item.get("title") or ""))


def _decision_reference_html(frozen: str, previous: str, total: int) -> str:
    """两类术语卡共用的决定参考行：冻结译法、旧译法、影响段落（#277）。

    过期译法卡（#102）与概念决策卡都走这里，避免各写一套。缺值不占位：
    没有冻结译法、或旧译法与冻结译法一致时就不画那一行——不留一个空字段
    冒充有值。
    """
    sep = _colon()
    parts = []
    if frozen:
        parts.append(f"<p><b>{_escape(i18n.tr('冻结译法'))}{sep}</b>{_escape(frozen)}</p>")
    if previous and previous != frozen:
        parts.append(f"<p><b>{_escape(i18n.tr('旧译法'))}{sep}</b>{_escape(previous)}</p>")
    parts.append(f"<p><b>{_escape(i18n.tr('影响段落'))}{sep}</b>"
                 f"{_escape(i18n.tr('{v0} 段', v0=total))}</p>")
    return "".join(parts)


def _trim(text: object, limit: int = 90) -> str:
    """表格单元格只放一行摘要：太长就把中间略掉，保留首尾。"""
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    head = limit // 2
    tail = limit - head
    return f"{value[:head]}…{value[-tail:]}"


def _colon() -> str:
    """字段名后的冒号：中文用全角，其他语言用半角 + 空格。"""
    return "：" if i18n.language().startswith(("zh", "ja", "ko")) else ": "


def _list_separator() -> str:
    """并列短句的分隔符：中日韩用全角分号，其余语言用半角 + 空格。"""
    return "；" if i18n.language().startswith(("zh", "ja", "ko")) else "; "


def _localized_reason_breakdown(reasons) -> str:
    """把核心给出的跳过原因码按当前语言重排成「原因 N 段」列表。

    核心只交出原因码与段数（见 ``workbench_view.coverage_copy_parts``），
    这里的每个词都走目录，所以英文界面不会漏出「无确定性风险信号 10 段」。
    """
    from folith import workbench_view as _workbench_view

    parts: list[str] = []
    for entry in reasons or []:
        if not isinstance(entry, dict):
            continue
        count = int(entry.get("count") or 0)
        if count <= 0:
            continue
        code = str(entry.get("reason") or "")
        label = i18n.tr(_workbench_view.skip_reason_label(code))
        parts.append(i18n.tr("{v0} {v1} 段", v0=label, v1=count))
    return _list_separator().join(parts)


def _coverage_texts(view: dict) -> tuple[str, str]:
    """审校就绪的标题与说明：覆盖被整段/部分跳过时按当前语言重排。

    其它就绪状态的标题已在目录里，仍走 ``tr_dynamic``；只有核心拼好、带跳过
    原因明细的句子需要在这里用结构化部件重新拼，否则英文界面会显示中文。
    """
    readiness = view.get("readiness") or {}
    label = i18n.tr_dynamic(str(readiness.get("label") or ""))
    detail = i18n.tr_dynamic(str(readiness.get("detail") or ""))
    coverage = view.get("coverage") or {}
    kind = str(coverage.get("kind") or "")
    breakdown = _localized_reason_breakdown(coverage.get("reasons"))
    if kind == "skipped":
        label = i18n.tr("{v0} 段未执行本轮独立语义审校（按策略跳过）",
                        v0=int(coverage.get("skipped") or 0))
        detail = i18n.tr(
            "按策略跳过：{v0}。这些段落没有独立审校结论，"
            "不等于审校通过；现有交付门禁保持不变。", v0=breakdown)
    elif kind == "partial":
        label = i18n.tr("部分译文未执行独立语义审校")
        detail = i18n.tr(
            "{v0} / {v1} 段已完成独立审校，{v2} 段未执行本轮独立语义审校"
            "（按策略跳过：{v3}）。",
            v0=int(coverage.get("reviewed") or 0),
            v1=int(coverage.get("total") or 0),
            v2=int(coverage.get("skipped") or 0),
            v3=breakdown)
    return label, detail


def _localized_task_copy(item: dict) -> dict:
    """合成审校项（待审 / 需重审 / 失败 / 跳过）的界面文案。

    发现项的摘要、说明与建议是模型产出，必须保持原样；只有这几类**合成项**
    的句子是核心拼好的界面文案，需要按当前语言重排。返回需要覆盖的字段；
    未覆盖的字段由调用方回落到 ``tr_dynamic`` / 原文。
    """
    kind = str(item.get("kind") or "")
    if kind not in _REVIEW_TASK_KINDS:
        return {}
    if kind == "skipped":
        from folith import workbench_view as _workbench_view

        overrides: dict[str, str] = {}
        code = str(item.get("skip_reason") or "")
        reason = i18n.tr(_workbench_view.skip_reason_label(code)) if code else ""
        if reason:
            summary = i18n.tr(
                "按策略跳过：{v0}。没有独立语义审校结果，不等于审校通过。",
                v0=reason)
            overrides["summary"] = summary
            overrides["reason"] = summary
            overrides["explanation"] = summary
        overrides["recommendation"] = i18n.tr(
            "如需覆盖本段，请重新运行独立审校。")
        return overrides
    return {"recommendation": i18n.tr(
        "重新运行此段审校，生成与当前译文一致的新结果。")}


class ReadingTextBrowser(QTextBrowser):
    """A bounded, content-sized reading column using the current Qt font metrics."""

    def __init__(self) -> None:
        super().__init__()
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_document)
        self.setMaximumHeight(420)
        self.document().documentLayout().documentSizeChanged.connect(self._schedule_fit)
        self.textChanged.connect(self._schedule_fit)
        self._set_reading_width()

    def _set_reading_width(self) -> None:
        self.setMaximumWidth(max(1, self.fontMetrics().horizontalAdvance("n" * 82)))

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.ApplicationFontChange):
            self._set_reading_width()
            self._schedule_fit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_fit()

    def _schedule_fit(self, *_args) -> None:
        if not self._fit_timer.isActive():
            self._fit_timer.start(0)

    def _fit_document(self) -> None:
        height = int(self.document().size().height() + 2 * self.frameWidth() + 12)
        height = max(self.fontMetrics().lineSpacing() * 4, min(420, height))
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)


class ReviewPanel(QWidget):
    """审校队列与详情。"""

    resolveRequested = Signal(list, str, str)       # finding_ids, action, note
    applySuggestionRequested = Signal(dict, str)    # item, note
    editRequested = Signal(int)                     # segment index
    reviewRequested = Signal(list)                  # segment indexes
    retranslateRequested = Signal(int)
    confirmSourceRequested = Signal(int, str)       # segment index, note
    reviewAllRequested = Signal()
    styleRuleRequested = Signal(dict, str)          # item, note（保存为项目风格规则）
    entityPreviewRequested = Signal(str, str)       # source, target
    entityRepairRequested = Signal(str, str, list)  # source, target, preview
    entityAcceptRequested = Signal(str)             # source
    conceptRequested = Signal()
    #: #289: 审计列表的撤销——面板只发信号（triage 记录键），写状态的是
    #: 窗口层经适配器调用既有核心函数；面板自己不写状态。
    triageRevokeRequested = Signal(str)             # triage record key

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._items: list[dict] = []
        self._visible: list[dict] = []
        self._selected_id = ""
        self._handled: list[dict] = []
        # #289: 自动处理审计列表（开关关闭时永远是空的，页面与今天一致）。
        self._auto: list[dict] = []
        self._triage_on = False
        # 审计检查器：选择一条自动处理记录只用于「查看/撤销」，不进入人工决定循环。
        self._selected_audit_key = ""
        self._auto_by_key: dict[str, dict] = {}
        self._group_summaries: dict[str, dict] = {}
        self._expanded = dict(GROUP_EXPANDED_BY_DEFAULT)
        self._initialised = False
        self._group_buttons: dict[str, QPushButton] = {}
        self._busy = False
        # 内嵌原页：另一份 PdfPanel 实例（翻译页那份挂在它自己的分栏里，带不动）。
        self._pdf_ok = False
        self._pdf_closed = False
        self._pdf_index = -1        # 最近定位过的段；不变就不重新渲染一页
        self._pdf_stale = False     # 收起再打开：重新定位一次
        self._state: dict = {}      # 任务状态：定位原文位置用（不每次重读）
        self._advance_hint = ""         # 处置成功后要去的下一项（窗口层消费）

        def button(text: str, icon_name: str, token: str = "text-secondary",
                   name: str = "") -> QPushButton:
            widget = QPushButton(text)
            widget.setIcon(icons.icon(icon_name, 16, token))
            widget.setIconSize(QSize(16, 16))
            if name:
                style_module.set_button(widget, name)
            return widget

        # ---- 顶部：就绪状态 + 筛选 ------------------------------------------
        self.headline = QLabel("")
        self.headline.setObjectName("reviewHeadline")
        self.headline.setWordWrap(True)
        self.headline.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setProperty("role", "muted")
        self.review_all_button = button(i18n.tr('审校全部段落'), "shield-check")
        self.review_all_button.clicked.connect(self.reviewAllRequested.emit)
        self.concept_button = button(i18n.tr('概念决策'), "list")
        self.concept_button.clicked.connect(self.conceptRequested.emit)
        # 窄窗（<1000px）下证据检查器单独做成可切换面板，避免三栏把窗口撑出横向溢出。
        self.evidence_toggle = button(i18n.tr('证据'), "info")
        self.evidence_toggle.setCheckable(True)
        self.evidence_toggle.setChecked(True)
        self._inspector_collapsed = False
        self.evidence_toggle.clicked.connect(self._toggle_evidence)
        # 标签复用左栏分组已有的、已翻译的词条，避免引入新 key 后在非中文界面
        # 回落成中文。
        self.overview = style_module.StatStrip([
            ("must", i18n.tr(GROUP_LABELS["must"])),
            ("suggested", i18n.tr(GROUP_LABELS["suggested"])),
            ("auto", i18n.tr(GROUP_LABELS["auto"])),
            ("reference", i18n.tr(GROUP_LABELS["reference"])),
        ])
        self.toolbar = QFrame()
        self.toolbar.setObjectName("qaToolbar")
        top = QVBoxLayout(self.toolbar)
        top.setContentsMargins(16, 10, 16, 10)
        top.setSpacing(6)
        top.addWidget(self.overview, 0)
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        text_column.addWidget(self.headline)
        text_column.addWidget(self.detail)
        # Readiness is independent of QA counts. Keep the secondary actions on
        # the same row while allowing the status copy to wrap in long languages.
        controls_row = QHBoxLayout()
        controls_row.setSpacing(8)
        controls_row.addLayout(text_column, 1)
        controls_row.addWidget(self.evidence_toggle, 0, Qt.AlignmentFlag.AlignTop)
        controls_row.addWidget(self.review_all_button, 0, Qt.AlignmentFlag.AlignTop)
        controls_row.addWidget(self.concept_button, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(controls_row)

        # ---- 左：队列 --------------------------------------------------------
        self.list = QListWidget()
        self.list.setObjectName("reviewQueue")
        self.list.setMinimumWidth(240)
        # 队列项是两行纯文本，超出列宽就省略；开横向滚动条会让 RTL（阿拉伯语）
        # 下的长行把列表撑出可视区，文字贴到窗口边缘。
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAccessibleName(i18n.tr('审校队列'))
        self.list.currentItemChanged.connect(self._current_changed)
        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setProperty("role", "muted")

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.list, 1)
        left.addWidget(self.empty_label)

        # ---- 区域 A：决策队列 -------------------------------------------------
        # 用紧凑的分组筛选片 + 行式列表取代手风琴式大按钮；片可点，点某组就把
        # 该组展开、其余收起（仍是同一个 self.list，行为与既有键盘导航一致）。
        self.queue_heading = style_module.SectionLabel(i18n.tr('审校队列'))
        self._filter_chips: dict[str, QPushButton] = {}
        # 长语言下分组标签很长：用可换行的 FlowRow，窄列里折行而不是互相挤压。
        chip_row = components.FlowRow(spacing=6, fill=False)
        chip_row.setContentsMargins(12, 0, 12, 0)
        for key in GROUP_ORDER:
            chip = QPushButton("")
            chip.setProperty("qaFilter", True)
            chip.setCheckable(True)
            chip.clicked.connect(
                lambda _checked=False, name=key: self._focus_group(name))
            chip_row.addWidget(chip)
            self._filter_chips[key] = chip
        self.queue_area = QFrame()
        self.queue_area.setObjectName("qaQueue")
        queue_layout = QVBoxLayout(self.queue_area)
        queue_layout.setContentsMargins(0, 0, 0, 12)
        queue_layout.setSpacing(8)
        queue_header = QFrame()
        queue_header.setObjectName("qaPanelHeader")
        queue_header.setMinimumHeight(48)
        queue_header_layout = QHBoxLayout(queue_header)
        queue_header_layout.setContentsMargins(16, 10, 16, 10)
        queue_header_layout.addWidget(self.queue_heading)
        queue_layout.addWidget(queue_header)
        queue_layout.addLayout(chip_row)
        queue_layout.addWidget(self.list, 1)
        queue_layout.addWidget(self.empty_label)
        self.queue_area.setMinimumWidth(250)
        self.queue_area.setMaximumWidth(320)

        # ---- 右：详情 + 动作 -------------------------------------------------
        self.item_title = QLabel("")
        self.item_title.setObjectName("reviewItemTitle")
        self.item_title.setWordWrap(True)
        self.item_chip = style_module.Chip("", "info")
        self.item_body = ReadingTextBrowser()
        self.item_body.setObjectName("reviewDetail")
        self.item_body.setOpenExternalLinks(False)
        self.item_body.setOpenLinks(False)
        self.item_body.anchorClicked.connect(self._reading_link)
        self.item_body.setSizePolicy(QSizePolicy.Policy.Preferred,
                                     QSizePolicy.Policy.Maximum)
        self.item_body.setAccessibleName(i18n.tr('审校详情'))
        # 区域 C：证据检查器（独立只读面，不与工作区共用一张大卡片）。
        self.evidence_body = QTextBrowser()
        self.evidence_body.setObjectName("reviewEvidence")
        self.evidence_body.setOpenExternalLinks(False)
        self.evidence_body.setAccessibleName(i18n.tr('证据'))
        self.audit_hint = QLabel("")
        self.audit_hint.setWordWrap(True)
        self.audit_hint.setProperty("role", "muted")

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(i18n.tr('处理说明（可选）：这次决定的依据'))
        self.note_edit.setAccessibleName(i18n.tr('处理说明'))

        self.source_confirm_box = style_module.CheckBox(
            i18n.tr('我已回看 PDF 原页，确认这段是有效正文'))
        self.source_confirm_box.toggled.connect(self._sync_buttons)
        self.source_note_edit = QLineEdit()
        self.source_note_edit.setPlaceholderText(
            i18n.tr('原文核对依据，例如：原页正文清晰可读，且与前后句连续。'))
        self.source_note_edit.setAccessibleName(i18n.tr('原文核对依据'))
        self.source_note_edit.textChanged.connect(self._sync_buttons)
        self.confirm_source_button = button(
            i18n.tr('确认原文并重新翻译'), "check-circle", "text-on-accent", "primary")
        self.confirm_source_button.clicked.connect(self._emit_confirm_source)

        self.apply_button = button(
            i18n.tr('暂存为候选'), "sparkles", "text-on-accent", "primary")
        self.apply_button.setToolTip(
            i18n.tr('把建议译文暂存为候选并显示差异；采用后才会写入译文'))
        self.apply_button.clicked.connect(self._emit_apply)
        self.open_button = button(i18n.tr('确认问题存在'), "alert")
        self.open_button.setToolTip(
            i18n.tr('问题是真的，但译文还没改：这一项保持待处理，仍会挡住交付'))
        self.open_button.clicked.connect(
            lambda: self._emit_resolve("confirmed_open"))
        self.confirm_button = button(i18n.tr('确认已解决'), "check", "text-on-accent",
                                     "primary")
        self.confirm_button.setToolTip(
            i18n.tr('当前译文已经满足这一项要求（不修改译文）'))
        self.confirm_button.clicked.connect(
            lambda: self._emit_resolve("human_fixed"))
        self.preserve_button = button(i18n.tr('保留当前译文'), "shield-check")
        self.preserve_button.setToolTip(
            i18n.tr('这次不改：驳回这一项或接受它的风险，并记录理由'))
        self.preserve_button.clicked.connect(
            lambda: self._emit_resolve("preserved"))
        self.edit_button = button(i18n.tr('修改译文'), "translate")
        self.edit_button.clicked.connect(self._emit_edit)
        self.review_button = button(i18n.tr('审校本段'), "shield-check")
        self.review_button.clicked.connect(self._emit_review)
        self.retranslate_button = button(i18n.tr('重译本段'), "refresh")
        self.retranslate_button.clicked.connect(self._emit_retranslate)
        # 风格类问题：把这条建议确认成项目风格规则，以后的任务也按它译。
        self.style_rule_button = button(i18n.tr('保存为项目风格规则…'), "sparkles")
        self.style_rule_button.clicked.connect(
            lambda: self.styleRuleRequested.emit(self.selected_item() or {},
                                                 self.note_edit.text().strip()))
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "muted")
        # 键位是没有写在按钮上的操作方式：给键盘用户一行看得见的清单。
        self.shortcut_hint = QLabel(i18n.tr(
            '快捷键：N 下一项 · A 暂存建议 · O 确认问题存在 · C 确认已解决 · '
            'K 保留当前译文 · E 修改译文'))
        self.shortcut_hint.setWordWrap(True)
        self.shortcut_hint.setProperty("role", "muted")
        self.shortcut_hint.setAccessibleName(i18n.tr('键盘快捷键'))

        # 动作按钮会折行：俄语/西语/阿拉伯语的按钮文字比中文长得多，固定一行会把
        # 整页撑到 2600px 宽（默认窗口只有 1180px），右半列被裁掉。
        self.audit_revoke_button = button(i18n.tr('撤销自动处理'), "refresh")
        self.audit_revoke_button.setToolTip(i18n.tr(
            '撤销这条自动处理结论；撤销会暂存撤回候选，仍需采用才会改正文。'))
        self.audit_revoke_button.clicked.connect(self._emit_audit_revoke)
        self.audit_revoke_button.setVisible(False)
        action_row = components.FlowRow(spacing=6, fill=False)
        for widget in (self.apply_button, self.edit_button, self.confirm_button,
                       self.confirm_source_button, self.review_button,
                       self.audit_revoke_button):
            action_row.addWidget(widget)
        alternative_row = components.FlowRow(spacing=6, fill=False)
        for widget in (self.open_button, self.preserve_button,
                       self.retranslate_button, self.style_rule_button):
            alternative_row.addWidget(widget)

        # 名字译法不一：同一个部件（与「检查与修复」抽屉共用），代替逐段的动作行。
        self.conflict = NameConflictWidget()
        self.conflict.previewRequested.connect(self.entityPreviewRequested.emit)
        self.conflict.repairRequested.connect(self.entityRepairRequested.emit)
        self.conflict.acceptRequested.connect(self.entityAcceptRequested.emit)
        self.conflict.locateRequested.connect(self._locate_segment)
        self.conflict.setVisible(False)

        # 原页：与翻译页同一个部件，收起后可用标题行里的「原页」再打开。
        self.pdf = pdf_panel_module.PdfPanel()
        self.pdf.setMinimumWidth(280)
        self.pdf.closeRequested.connect(self._close_source)
        self.pdf.setVisible(False)
        self.source_button = button(i18n.tr('原页'), "eye")
        self.source_button.setVisible(False)
        self.source_button.clicked.connect(self._open_source)

        workspace_header = QFrame()
        workspace_header.setObjectName("qaPanelHeader")
        workspace_header.setMinimumHeight(48)
        title_row = QHBoxLayout(workspace_header)
        title_row.setContentsMargins(20, 10, 20, 10)
        title_row.addWidget(self.item_title, 1)
        title_row.addWidget(self.source_button, 0, Qt.AlignmentFlag.AlignTop)
        title_row.addWidget(self.item_chip, 0, Qt.AlignmentFlag.AlignTop)

        content_column = QVBoxLayout()
        content_column.setContentsMargins(0, 0, 0, 0)
        content_column.setSpacing(10)
        content_column.addWidget(self.item_body, 0)
        # 术语决定控件紧跟在文本之后：决定与它所影响的译文保持就近。
        content_column.addWidget(self.conflict)
        content_column.addWidget(self.source_confirm_box)
        content_column.addWidget(self.source_note_edit)
        content_column.addStretch(1)
        self.action_bar = QFrame()
        self.action_bar.setObjectName("qaActionBar")
        action_layout = QVBoxLayout(self.action_bar)
        action_layout.setContentsMargins(16, 12, 16, 12)
        action_layout.setSpacing(8)
        action_layout.addWidget(self.note_edit)
        action_layout.addLayout(action_row)
        action_layout.addLayout(alternative_row)
        action_layout.addWidget(self.hint)
        action_layout.addWidget(self.shortcut_hint)

        # ---- 区域 B：译文工作区（中间，视觉中心；右侧可并排原页）---------------
        self.workspace_area = QFrame()
        self.workspace_area.setObjectName("qaWorkspace")
        workspace = QVBoxLayout(self.workspace_area)
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        workspace.addWidget(workspace_header)
        work_split = QHBoxLayout()
        work_split.setContentsMargins(20, 16, 20, 16)
        work_split.setSpacing(14)
        work_split.addLayout(content_column, 2)
        work_split.addWidget(self.pdf, 1)
        workspace.addLayout(work_split, 1)
        workspace.addWidget(self.action_bar)

        # ---- 区域 C：证据检查器（右侧，独立只读面）------------------------------
        self.evidence_title = style_module.SectionLabel(i18n.tr('证据'))
        self.evidence_area = QFrame()
        self.evidence_area.setObjectName("qaEvidence")
        evidence_layout = QVBoxLayout(self.evidence_area)
        evidence_layout.setContentsMargins(0, 0, 0, 12)
        evidence_layout.setSpacing(8)
        evidence_header = QFrame()
        evidence_header.setObjectName("qaPanelHeader")
        evidence_header.setMinimumHeight(48)
        evidence_header_layout = QHBoxLayout(evidence_header)
        evidence_header_layout.setContentsMargins(16, 10, 16, 10)
        evidence_header_layout.addWidget(self.evidence_title)
        evidence_layout.addWidget(evidence_header)
        evidence_layout.addWidget(self.evidence_body, 1)
        evidence_layout.addWidget(self.audit_hint)
        self.evidence_area.setMinimumWidth(180)
        self.evidence_area.setMaximumWidth(320)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.queue_area, 0)
        body.addWidget(self.workspace_area, 2)
        body.addWidget(self.evidence_area, 0)

        # 队列与详情装在一个容器里：审校队列整个是空的时，它整体让位给一个空状态
        # （设计审校 40 号 4.1：不再画五个 0 计数的筛选、一个空列表和一块空白详情）。
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.toolbar)
        content_layout.addLayout(body, 1)
        self.empty_state = style_module.EmptyState(icon="shield-check", page=True,
                                                   action=i18n.tr('审校全部段落'))
        self.empty_state.actionTriggered.connect(self.reviewAllRequested.emit)
        self.empty_state.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.content, 1)
        layout.addWidget(self.empty_state, 1)

        # 键盘连续处理（issue #98）：看 → 决定 → 下一项不离开键盘。守卫只挂
        # 在快捷键上：焦点在文本输入里（正在写备注）时不触发；按钮点击不走
        # 这个守卫——全局焦点在段编辑器里时点按钮必须照常生效。
        def _guarded(handler):
            def run():
                if not self._typing():
                    handler()
            return run

        self._shortcut_handlers: dict[str, object] = {
            "next": _guarded(self._goto_next_item),
            "apply": _guarded(self._emit_apply),
            "open": _guarded(lambda: self._emit_resolve("confirmed_open")),
            "confirm": _guarded(lambda: self._emit_resolve("human_fixed")),
            "keep": _guarded(lambda: self._emit_resolve("preserved")),
            "edit": _guarded(self._emit_edit),
        }
        self._shortcuts = []
        for key, sequence in (("next", "N"), ("apply", "A"), ("open", "O"),
                              ("confirm", "C"), ("keep", "K"), ("edit", "E")):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(self._shortcut_handlers[key])
            self._shortcuts.append(shortcut)
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._sync_shortcut_focus)
        self._sync_shortcut_focus()

        self._render_detail()
        self._sync_accessibility()

    def _sync_accessibility(self) -> None:
        """把工具提示里的语义补成辅助描述，并固定详情区的 Tab 顺序。

        Qt 的默认顺序是**创建顺序**（备注框建于来源核对字段之前），键盘用户从
        处理说明按 Tab 会跳回上方的勾选框。这里按界面从上到下的阅读顺序重排；
        隐藏的控件由 Qt 自动跳过。
        """
        for widget, key in ((self.apply_button, "A"), (self.open_button, "O"),
                            (self.confirm_button, "C"),
                            (self.preserve_button, "K"),
                            (self.edit_button, "E")):
            detail = widget.toolTip() or widget.text()
            widget.setAccessibleDescription(f"{detail} ({key})")
        self.list.setAccessibleDescription(self.shortcut_hint.text())
        self.setAccessibleName(i18n.tr('审校工作台'))
        chain = (self.list, self.source_confirm_box, self.source_note_edit,
                 self.note_edit, self.apply_button, self.open_button,
                 self.confirm_button, self.preserve_button,
                 self.confirm_source_button, self.review_button,
                 self.edit_button, self.retranslate_button,
                 self.style_rule_button)
        for current, following in zip(chain, chain[1:]):
            QWidget.setTabOrder(current, following)

    # ================= 数据 =================
    def view(self) -> dict:
        """最近一次 `apply` 收到的视图（阶段步骤条与它共用同一份审校投影）。"""
        return self._view

    def apply(self, view: dict, *, busy: bool = False) -> None:
        """用新的视图模型重画队列；保持当前选中项（按稳定标识）。"""
        self._view = view or {}
        self._busy = busy
        self.concept_button.setEnabled(not busy)
        self._items = list(self._view.get("queue_items") or [])
        self._handled = list(self._view.get("handled_items") or [])
        # #289: 审计列表。开关关闭时视图里没有 `auto_handled_items` 这个键，
        # 下面的列表就是空的，页面渲染与今天完全一致。
        self._triage_on = bool(self._view.get("qa_triage_enabled"))
        self._auto = [item for item in (self._view.get("auto_handled_items") or [])]
        self._auto_by_key = {
            str(item.get("triage_record_key")): item for item in self._auto
            if item.get("triage_record_key")}
        # 程序自己拥有、但可能被选中的审计记录在刷新后要重新解析：键还在就沿用。
        if self._selected_audit_key not in self._auto_by_key:
            self._selected_audit_key = ""
        # RTL：整块面板按界面语言镜像（三栏的顺序、对齐、阅读方向都跟着走）。
        _rtl = i18n.language().startswith("ar")
        self.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if _rtl else Qt.LayoutDirection.LeftToRight)
        self._group_summaries = {
            str(group.get("group_id") or ""): dict(group)
            for group in (self._view.get("finding_groups") or [])}
        readiness = self._view.get("readiness") or {}
        headline, detail = _coverage_texts(self._view)
        self.headline.setText(headline)
        self.detail.setText(detail)
        counts = self._group_counts()
        self._sync_overview(counts)
        self._sync_filter_chips(counts)
        if not self._initialised:
            # 第一次打开：有「必须处理」就只展开它；没有就把第一个非空组展开，
            # 免得左栏看起来是空的。审计组永远不参与这个回退——它默认收起，
            # 收起不等于看不见：组头计数与页脚的总数守恒都在。
            self._initialised = True
            if not counts.get("must"):
                first = next((key for key in GROUP_ORDER
                              if key != "auto" and counts.get(key)), "")
                if first:
                    self._expanded = {key: key == first for key in GROUP_ORDER}
                elif 0 < int(counts.get("auto") or 0) <= AUDIT_AUTO_EXPAND_LIMIT:
                    # 只有审计项时别让详情区空着：展开「已自动处理」，让这些记录
                    # 仍然一眼可达（默认收起只为让步于人工队列，不是要把它们藏起来）。
                    # 数量很大时仍保持收起，避免一打开就构建几百张卡片。
                    self._expanded = {key: key == "auto" for key in GROUP_ORDER}
        self._rebuild_list()
        self._sync_empty(readiness)
        self.shortcut_hint.setVisible(bool(self._items))

    def _sync_empty(self, readiness: dict) -> None:
        """队列整个是空的：整页换成一个空状态，最多一个动作。

        #289: 人工队列空但审计列表非空时**不算空**——审计项必须仍然可达，
        不能整页换成空状态把它们藏起来。
        """
        empty = not self._items and not self._handled and not self._auto
        self.content.setVisible(not empty)
        if not empty:
            self.empty_state.setVisible(False)
            return
        status = str(readiness.get("status") or "")
        title, hint = _coverage_texts(self._view)
        title = title or i18n.tr('尚未审校')
        # 审校已完成：没有要做的事，就不给按钮。
        self.empty_state.set_text(title, hint)
        coverage_complete = readiness.get("coverage_complete")
        if coverage_complete is None:
            coverage_complete = status == "current" and not readiness.get("skipped_count")
        done = status == "not_required" or (
            bool(readiness.get("ready")) and bool(coverage_complete))
        self.empty_state.set_action("" if done else i18n.tr('审校全部段落'))
        self.empty_state.setEnabled(not self._busy)

    def _group_counts(self) -> dict:
        counts = self._view.get("group_counts") or {}
        if counts:
            return {key: int(counts.get(key) or 0) for key in GROUP_ORDER}
        rows = list(self._items) + list(self._handled)
        result = {key: sum(self._group_of(item) == key for item in rows)
                  for key in GROUP_ORDER}
        result["auto"] = len(self._auto)
        return result

    def _sync_overview(self, counts: dict) -> None:
        """把分组计数投到顶部指标条。数字全部来自投影；分诊关闭时「已自动处理」
        写「未启用」，不把停用的能力显示成 0。需要处理的非零值用危险色挑出来。"""
        self.overview.set_value(
            "must", int(counts.get("must") or 0),
            emphasis="danger" if counts.get("must") else "default")
        self.overview.set_value("suggested", int(counts.get("suggested") or 0))
        self.overview.set_value(
            "auto", int(counts.get("auto") or 0) if self._triage_on
            else i18n.tr('未启用'))
        self.overview.set_value("reference", int(counts.get("reference") or 0))

    def _group_of(self, item: dict) -> str:
        group = str(item.get("list_group") or "")
        if group in GROUP_ORDER:
            return group
        if item.get("handled"):
            return "reference"
        if item.get("kind") in _REVIEW_TASK_KINDS:
            return "must"
        return {"pending": "must", "rereview": "must",
                "suggested": "suggested"}.get(str(item.get("filter_group") or ""),
                                              "reference")

    @staticmethod
    def _sorted(rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda item: (
            item.get("priority", 9),
            item.get("segment_index") if isinstance(item.get("segment_index"), int) else 10**9,
            str(item.get("id") or "")))

    def _rebuild_list(self) -> None:
        wanted = self._selected_id
        buckets: dict[str, list[dict]] = {key: [] for key in GROUP_ORDER}
        for item in self._items:
            buckets[self._group_of(item)].append(item)
        for item in self._handled:
            buckets["reference"].append(item)
        # #289: 审计组保持投影给出的顺序（按记录创建时间），不再按优先级重排。
        buckets["auto"] = list(self._auto)
        for key, rows in buckets.items():
            if key != "auto":
                buckets[key] = self._sorted(rows)
        self.list.blockSignals(True)
        self.list.clear()
        self._group_buttons.clear()
        self._visible = []
        for key in GROUP_ORDER:
            rows = buckets[key]
            if not rows:
                continue
            self._add_group_header(key, len(rows))
            if not self._expanded[key]:
                continue
            for item in rows:
                if key == "auto":
                    self._add_audit_card(item)
                else:
                    self._add_card(item)
                    self._visible.append(item)
        row = next((position for position, item in enumerate(self._visible)
                    if item.get("id") == wanted), 0 if self._visible else -1)
        if self._visible:
            self.list.setCurrentRow(self._list_row(row))
        else:
            self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self._selected_id = (str(self._visible[row].get("id"))
                             if 0 <= row < len(self._visible) else "")
        self.empty_label.setVisible(not self._visible and not (
            self._expanded["auto"] and self._auto))
        self.empty_label.setText(
            i18n.tr('展开分组查看其中的项目。')
            if self._items or self._handled or self._auto
            else i18n.tr('审校队列是空的：没有需要人工处理的项目。'))
        self._sync_filter_chips(self._group_counts())
        self._render_detail()

    def _add_group_header(self, key: str, count: int) -> None:
        entry = QListWidgetItem()
        entry.setFlags(Qt.ItemFlag.NoItemFlags)
        entry.setSizeHint(QSize(0, 30))
        self.list.addItem(entry)
        expanded = self._expanded[key]
        button = QPushButton(f"{i18n.tr(GROUP_LABELS[key])} · {count}")
        button.setObjectName("reviewGroupHeader")
        button.setCheckable(True)
        button.setChecked(expanded)
        button.setIcon(icons.icon("chevron-down" if expanded else "chevron-right",
                                  13, "text-muted"))
        button.setIconSize(QSize(13, 13))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(lambda _checked=False, name=key: self._toggle_group(name))
        if key == "auto":
            # #289: 组头让键盘/读屏用户也能核对总数守恒——自动处理不是把问题藏起来。
            stats = self._view.get("triage_stats") or {}
            buckets = stats.get("buckets") or {}
            button.setAccessibleDescription(i18n.tr(
                '必须处理 {v0} · 建议处理 {v1} · 参考 {v2} · 自动处理 {v3}，'
                '合计等于本次投影的 {v4} 条发现。',
                v0=int(buckets.get("must") or 0), v1=int(buckets.get("suggested") or 0),
                v2=int(buckets.get("reference") or 0), v3=int(buckets.get("auto") or 0),
                v4=int(stats.get("total_findings") or 0)))
        self.list.setItemWidget(entry, button)
        self._group_buttons[key] = button

    def _add_card(self, item: dict) -> None:
        title = self._item_title(item)
        label = i18n.tr_dynamic(str(item.get("severity_label") or ""))
        decision = i18n.tr_dynamic(str(item.get("decision_label") or ""))
        summary = str(item.get("summary") or "")
        if item.get("kind") in _REVIEW_TASK_KINDS:
            # 合成项（待审/需重审/失败/跳过）的摘要也是界面文案。
            summary = _localized_task_copy(item).get("summary") \
                or i18n.tr_dynamic(summary)
        else:
            # 发现项摘要多数是模型产出：tr_dynamic 查不到 key 就原样保留。
            # 但确定性发现的摘要是程序模板（以中文为 key），这里命中即本地化。
            summary = i18n.tr_dynamic(summary)
        # 标题已经写了"审校未完成"，第二行就不再重复一遍同一个词。
        tags = [value for value in (decision, label)
                if value and value not in title]
        if self._triage_on and item.get("handled") and item.get("triage_actor_type"):
            # #289 D13: 人工决定与机器消解从此不同形——显示 actor_type，
            # 不显示 actor 字符串（"qa_triage" 不是人工决定）。
            who = i18n.tr(_ACTOR_TYPE_LABELS.get(
                str(item.get("triage_actor_type")),
                str(item.get("triage_actor_type"))))
            decided = i18n.tr_dynamic(str(_DISPOSITION_LABELS.get(
                str(item.get("triage_disposition") or ""),
                str(item.get("triage_disposition") or ""))))
            tags.insert(0, f"{who} · {decided}" if decided else who)
        if summary:
            tags.append(summary)
        if self._triage_on:
            group_copy = self._finding_group_copy(item)
            if group_copy:
                # #289 D12: 已有的 finding_groups 分组结果第一次被渲染出来
                # （只渲染，不改分组算法）——重复的数字/单位/标记问题不再是 N 行孤岛。
                tags.append(group_copy)
        second = " · ".join(dict.fromkeys(tags))
        text = f"{title}\n{second}" if second else title
        entry = QListWidgetItem(text)
        entry.setData(Qt.ItemDataRole.UserRole, item.get("id"))
        # 统一紧凑行高：两行摘要固定到 46px，队列看起来是整齐的一列而不是高度参差的卡片。
        entry.setSizeHint(QSize(0, 46))
        # 队列只有两行；完整问题放进悬停提示，读屏读同一份文本，不再显示
        # 截断到 60 字却没有省略号的半句话。
        entry.setToolTip(text)
        entry.setData(Qt.ItemDataRole.AccessibleTextRole,
                      f"{title}. {second}".strip())
        self.list.addItem(entry)

    def _finding_group_copy(self, item: dict) -> str:
        """一行分组摘要：类型 · token · 出现次数 · 影响段落数（数据来自 #101）。"""
        group_id = str(item.get("finding_group_id") or "")
        if not group_id:
            return ""
        group = self._group_summaries.get(group_id) or {}
        token = str(item.get("finding_group_token") or group.get("token") or "")
        if not token:
            return ""
        kind_label = i18n.tr_dynamic(str(item.get("finding_group_label")
                                         or group.get("label") or ""))
        return i18n.tr('{v0}组 {v1} · 出现 {v2} 次 · 影响 {v3} 段',
                       v0=kind_label, v1=token,
                       v2=int(group.get("occurrence_count") or 0),
                       v3=int(group.get("segment_count") or 0))

    def _add_audit_card(self, item: dict) -> None:
        """#289: 审计列表里的一行——可展开看证据与快照，可撤销回人工队列。

        复用仓库已有的可展开行原语 ``IssueCard``（「检查与修复」抽屉同款）。
        这一行**不在** ``_visible`` 里：它不参与 N/A/C/K 键盘处置循环，
        人工决定按钮也不会作用在它身上。撤销只发信号，面板不写状态。
        """
        record_key = str(item.get("triage_record_key") or "")
        entry = QListWidgetItem()
        # 没有 UserRole：导航（_list_row/_visible_index）仍会跳过这一行，
        # 它不属于人工处置循环；但允许被选中，用于在右侧检查器里查看/撤销。
        entry.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        entry.setData(Qt.ItemDataRole.UserRole + 1, record_key)
        card = IssueCard("info", collapsible=True, expanded=False)
        card.setObjectName("qaAuditItem")
        who = i18n.tr(_ACTOR_TYPE_LABELS.get(
            str(item.get("triage_actor_type") or ""),
            str(item.get("triage_actor_type") or "")))
        disposition = i18n.tr_dynamic(str(_DISPOSITION_LABELS.get(
            str(item.get("triage_disposition") or ""),
            str(item.get("triage_disposition") or ""))))
        card.set_title(self._item_title(item))
        head = " · ".join(value for value in (who, disposition) if value)
        detail_bits = []
        if item.get("triage_candidate_id"):
            detail_bits.append(i18n.tr('候选 {v0}', v0=str(item["triage_candidate_id"])))
            detail_bits.append(i18n.tr('已提交') if item.get("triage_committed")
                               else i18n.tr('未提交'))
        card.set_summary(" · ".join([value for value in (head, " ".join(detail_bits))
                                    if value]) or head)

        sep = _colon()
        for evidence in item.get("triage_evidence") or []:
            line = QLabel(f"<b>{_escape(i18n.tr('证据'))}{sep}</b>"
                          f"{_escape(str(evidence.get('kind') or ''))}"
                          f" · {_escape(str(evidence.get('ref') or ''))}"
                          f" · {_escape(str(evidence.get('detail') or ''))}")
            line.setObjectName("qaAuditEvidence")
            line.setWordWrap(True)
            line.setProperty("role", "muted")
            card.details_layout.addWidget(line)
        snapshot = item.get("triage_snapshot") or {}
        for label_key, text_key in (("当时原文", "source"), ("当时译文", "target")):
            value = str(snapshot.get(text_key) or "")
            if not value:
                continue
            line = QLabel(f"<b>{_escape(i18n.tr(label_key))}{sep}</b>"
                          f"{_escape(value)}")
            line.setObjectName("qaAuditSnapshot")
            line.setWordWrap(True)
            card.details_layout.addWidget(line)
        summary_text = str(snapshot.get("summary") or "")
        if summary_text:
            line = QLabel(f"<b>{_escape(i18n.tr('问题'))}{sep}</b>{_escape(summary_text)}")
            line.setObjectName("qaAuditSnapshot")
            line.setWordWrap(True)
            line.setProperty("role", "muted")
            card.details_layout.addWidget(line)

        revoke = QPushButton(i18n.tr('撤销'))
        style_module.set_button(revoke, "secondary", small=True)
        revoke.setToolTip(i18n.tr('撤销这条自动处理结论；这一项回到人工队列。'))
        revoke.clicked.connect(
            lambda _checked=False, key=record_key: self.triageRevokeRequested.emit(key))
        card.add_action(revoke)
        card.finish_actions()

        entry.setSizeHint(card.sizeHint())
        card.toggle.toggled.connect(
            lambda checked=False, c=card, e=entry: e.setSizeHint(c.sizeHint()))
        accessible = " · ".join(value for value in
                                (card.title.text(), head,
                                 i18n.tr('自动处理（审计）')) if value)
        entry.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
        self.list.addItem(entry)
        self.list.setItemWidget(entry, card)

    def _list_row(self, visible_index: int) -> int:
        """``_visible`` 里的位置 → 列表行（跳过组标题）。"""
        seen = -1
        for row in range(self.list.count()):
            entry = self.list.item(row)
            if entry.data(Qt.ItemDataRole.UserRole):
                seen += 1
                if seen == visible_index:
                    return row
        return max(0, self.list.count() - 1)

    def _toggle_group(self, key: str) -> None:
        self._expanded[key] = not self._expanded[key]
        self._rebuild_list()

    def _current_changed(self, current, _previous) -> None:
        # 人工处置项带 UserRole；审计行带 UserRole+1（record key）。两者分开：
        # 选中审计记录只更新检查器，绝不进入人工决定循环。
        action_id = str(current.data(Qt.ItemDataRole.UserRole) or "") if current else ""
        audit_key = str(current.data(Qt.ItemDataRole.UserRole + 1) or "") if current else ""
        self._selected_id = action_id
        self._selected_audit_key = "" if action_id else audit_key
        self.note_edit.clear()
        self.source_note_edit.clear()
        self.source_confirm_box.setChecked(False)
        self._render_detail()

    def selected_item(self) -> dict:
        return next((item for item in self._visible
                     if item.get("id") == self._selected_id), {})

    # ================= 渲染 =================
    def _is_source_item(self, item: dict) -> bool:
        return item.get("finding_type") == "source_quality_gate"

    @staticmethod
    def _highlight(text: str, span: str) -> str:
        """只高亮**有证据**的 span：span 确实出现在文本里才标，否则原样返回。

        陈旧/错误的 span 不高亮——宁可没有标记，也不假装指到了问题。"""
        text, span = str(text or ""), str(span or "")
        if span and span in text:
            before, after = text.split(span, 1)
            return f"{_escape(before)}<mark>{_escape(span)}</mark>{_escape(after)}"
        return _escape(text)

    @staticmethod
    def _item_title(item: dict) -> str:
        """名字译法不一按名字列一行，不写第一处出现的段号。"""
        if item.get("term_card"):
            return _term_card_title(item)
        source = str(item.get("entity_source") or "")
        if source:
            count = len(item.get("segment_indexes") or [])
            title = i18n.tr('译名不一致 · {v0}', v0=source)
            return title + (i18n.tr('（{v0} 处）', v0=count) if count > 1 else "")
        number = item.get("segment_number")
        detail = str(item.get("identity_detail") or "")
        if isinstance(number, int) and detail:
            return f"{i18n.tr('第 {v0} 段', v0=number)} · {i18n.tr_dynamic(detail)}"
        return i18n.tr_dynamic(str(item.get("title") or ""))

    def _entity_for(self, item: dict) -> dict:
        """名字译法不一的发现项对应的实体（抽屉里的同一份数据）；找不到就是空。"""
        source = str(item.get("entity_source") or "")
        return next((entity for entity in self._view.get("entities") or []
                     if entity.get("source") == source), {}) if source else {}

    def _term_html(self, item: dict) -> str:
        """Bounded, navigable occurrences using only the projected term state."""
        source = str(item.get("term_source") or "").strip()
        target = str(item.get("term_target") or "").strip()
        previous = str(item.get("term_previous_target") or "").strip()
        rows = list(item.get("term_segments") or [])
        total = int(item.get("term_segment_total") or len(rows))
        sep = _colon()
        parts = []
        if source:
            parts.append(f"<p><b>{_escape(i18n.tr('术语'))}{sep}</b>{_escape(source)}</p>")
        parts.append(_decision_reference_html(target, previous, total))
        status = i18n.tr_dynamic(str(item.get("decision_label") or item.get("status_label") or ""))
        if status:
            parts.append(f"<p><b>{_escape(i18n.tr('状态'))}{sep}</b>{_escape(status)}</p>")
        shown = rows[:TERM_ROWS_SHOWN]
        if shown:
            parts.append(f"<p><b>{_escape(i18n.tr('影响段落'))}</b></p>")
            for row in shown:
                index = row.get("index")
                title = _escape(i18n.tr('第 {v0} 段', v0=row.get('number')))
                if isinstance(index, int) and not isinstance(index, bool):
                    title = f'<a href="segment:{index}">{title}</a>'
                current = str(row.get("target") or "").strip()
                mark = (i18n.tr('（未翻译）') if not current else
                        i18n.tr('符合冻结译法') if target and row.get("uses_frozen") else
                        i18n.tr('未用冻结译法') if target else "")
                parts.append(f"<p><b>{title}</b> · {_escape(mark)}<br>"
                             f"{_escape(i18n.tr('原文'))}{sep}{_escape(_trim(row.get('source')))}<br>"
                             f"{_escape(i18n.tr('当前译文'))}{sep}{_escape(_trim(current) or i18n.tr('（未翻译）'))}</p>")
        if total > len(shown):
            parts.append(f"<p>{_escape(i18n.tr('另有 {v0} 段未列出', v0=total - len(shown)))}</p>")
        return "".join(parts)

    def _reading_link(self, url) -> None:
        if url.scheme() != "segment":
            return
        try:
            index = int(url.path())
        except ValueError:
            return
        indexes = {row.get("index") for row in self.selected_item().get("term_segments", [])}
        if index in indexes:
            self._locate_segment(index)

    def _translation_html(self, item: dict) -> str:
        """工作区里把原文、当前译文、建议译文各画成独立区块（不是一整块灰）。"""
        from folith.textual import text_direction
        muted = style_module.html_color("text-secondary", over="bg-surface")
        rule = style_module.html_color("border-subtle", over="bg-surface")
        blocks: list[str] = []
        for label, key, span_key in (("原文", "source", "source_span"),
                                     ("当前译文", "target", "target_span"),
                                     ("建议译文", "suggested_target", None)):
            value = str(item.get(key) or "").strip()
            if not value:
                if key != "target":
                    continue
                value = i18n.tr('（未翻译）')
            rtl = text_direction(value, None) == "rtl"
            block = ' dir="rtl"' if rtl else ""
            # The empty-target label is UI copy, not document text or evidence.
            span = str(item.get(span_key) or "") if span_key and item.get(key) else ""
            if blocks:
                blocks.append(f'<hr style="color:{rule}; background-color:{rule}"/>')
            blocks.append(
                f"<p style=\"font-size:12px;font-weight:700;color:{muted};"
                f"margin-bottom:2px\">{_escape(i18n.tr(label))}</p>"
                f"<p{block} style=\"margin-top:0;line-height:145%\">"
                f"{self._highlight(value, span)}</p>")
        return "".join(blocks)

    def _evidence_html(self, item: dict, sep: str) -> str:
        """证据检查器的内容：术语参考、已记录决定、分组摘要。空字符串表示没有。"""
        parts: list[str] = []
        if item.get("entity_source"):
            total = int(item.get("term_segment_total") or 0)
            if total > 0:
                parts.append(_decision_reference_html(
                    str(item.get("term_frozen_target") or "").strip(),
                    str(item.get("term_previous_target") or "").strip(), total))
        decision = i18n.tr_dynamic(str(item.get("decision_label") or ""))
        if decision:
            parts.append(f"<p><b>{_escape(i18n.tr('已记录的决定'))}{sep}</b>"
                         f"{_escape(decision)}</p>")
        group_copy = self._finding_group_copy(item)
        if group_copy:
            parts.append(f"<p>{_escape(group_copy)}</p>")
        return "".join(parts)

    def _focus_group(self, key: str) -> None:
        """点分组筛选片：只展开该组（其余收起），即按组过滤队列。"""
        self._expanded = {name: name == key for name in GROUP_ORDER}
        self._rebuild_list()

    def _sync_filter_chips(self, counts: dict) -> None:
        labels = {"must": GROUP_LABELS["must"],
                  "suggested": GROUP_LABELS["suggested"],
                  "auto": GROUP_LABELS["auto"],
                  "reference": GROUP_LABELS["reference"]}
        for key, chip in self._filter_chips.items():
            chip.setText(i18n.tr(labels[key]))
            # 分诊关闭时没有「自动处理」这种能力：不显示这一片，避免出现一个
            # 会误导的 0。其余分组始终显示。
            chip.setVisible(not (key == "auto" and not self._triage_on))
            chip.setChecked(bool(self._expanded.get(key)))

    def _toggle_evidence(self) -> None:
        if self.width() >= 1000:
            self._inspector_collapsed = not self._inspector_collapsed
            self._apply_narrow_evidence(getattr(self, "_narrow_evidence", False))
        else:
            self._apply_narrow_evidence(not getattr(self, "_narrow_evidence", False))

    def _apply_narrow_evidence(self, show_evidence: bool) -> None:
        """窄窗下证据检查器与译文工作区互斥显示，避免三栏把窗口撑出横向溢出。"""
        self._narrow_evidence = bool(show_evidence)
        if self.width() >= 1000:
            self.workspace_area.setVisible(True)
            self.evidence_area.setVisible(not self._inspector_collapsed)
            self.evidence_toggle.setChecked(not self._inspector_collapsed)
            self.evidence_toggle.setText(i18n.tr('证据'))
            return
        self.workspace_area.setVisible(not show_evidence)
        self.evidence_area.setVisible(show_evidence)
        self.evidence_toggle.setChecked(show_evidence)
        self.evidence_toggle.setText(
            i18n.tr('证据'))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if not hasattr(self, "workspace_area"):
            return
        self._apply_narrow_evidence(getattr(self, "_narrow_evidence", False))

    def _block(self, label: str, value: str) -> str:
        """Work-area 里一个独立文本块（标签 + 正文），原文/译文各自成块。"""
        from folith.textual import text_direction
        muted = style_module.html_color("text-secondary", over="bg-surface")
        rtl = text_direction(value, None) == "rtl"
        attr = ' dir="rtl"' if rtl else ""
        return (f"<p style=\"font-size:11px;font-weight:700;color:{muted};"
                f"margin-bottom:2px\">{_escape(i18n.tr(label))}</p>"
                f"<p{attr} style=\"margin-top:0\">{_escape(value)}</p>")

    def _render_audit(self) -> None:
        """审计检查器：选中一条自动处理记录，填充译文工作区与证据检查器。

        只读：绝不显示也不触发任何人工决定动作；唯一可用的是「撤销自动处理」，
        它仍走既有核心路径（提交过的修复只暂存撤回候选，不改正文）。
        """
        key = self._selected_audit_key
        item = self._auto_by_key.get(key, {})
        if not item:
            self._render_detail_empty()
            return
        self._selected_id = ""
        self.evidence_area.setMinimumWidth(280)
        self.evidence_area.setMaximumWidth(320)
        self.item_title.setText(self._item_title(item) or i18n.tr('自动处理'))
        disposition_name = i18n.tr_dynamic(str(_DISPOSITION_LABELS.get(
            str(item.get("triage_disposition") or ""),
            str(item.get("triage_disposition") or ""))))
        self.item_chip.setText(disposition_name or i18n.tr('自动处理'))
        self.item_chip.set_tone("info")
        self.item_chip.setVisible(True)
        for widget in (self.item_title, self.item_chip, self.item_body,
                       self.evidence_body):
            widget.setVisible(True)
        # 审计记录不是人工决定项：隐藏全部人工处置按钮，只留撤销。
        self._set_actions(source=False, task=False, decision=False,
                          suggestion=False, edit=False)
        self.audit_revoke_button.setVisible(not self._busy)
        self.hint.setText(i18n.tr(
            '这是自动处理记录，仅供查看与撤销；不会作为人工决定项。'))
        self.conflict.setVisible(False)
        sep = _colon()
        snap = item.get("triage_snapshot") or {}
        committed = bool(item.get("triage_committed"))
        disposition = str(item.get("triage_disposition") or "")
        before = str(item.get("triage_before_target") or snap.get("target") or "")
        after = str(item.get("triage_after_target") or "")
        current = str(item.get("target") or "")
        candidate_status = str(item.get("triage_candidate_status") or "")
        parts = [f"<p><b>{_escape(i18n.tr('自动处理'))}{sep}</b>"
                 f"{_escape(disposition_name)}</p>"]
        if disposition == "verified_safe":
            # 证据核验安全：没有改动译文，也不该假装有 before/after。
            parts.append(f"<p>{_escape(i18n.tr('未修改译文（证据核验安全）'))}</p>")
        else:
            if candidate_status in ("discarded", "stale"):
                status = i18n.tr('已撤销或已回退')
            elif not committed:
                status = i18n.tr('仅暂存未采用')
            elif not after:
                status = i18n.tr('原修复结果已无法重新构造')
            elif current and current.strip() == after.strip():
                status = i18n.tr('已提交且仍为当前')
            else:
                status = i18n.tr('已提交但已被修改')
            # 状态紧跟在处置之后、置于最上方：它是审计最关键的一句，不能被
            # 正文滚动区裁到下面看不见。
            parts.append(f"<p><b>{_escape(i18n.tr('状态'))}{sep}</b>"
                         f"{_escape(status)}</p>")
        source = str(snap.get("source") or "")
        if source:
            parts.append(self._block("原文", source))
        if disposition != "verified_safe":
            if before:
                parts.append(self._block("处理前译文", before))
            if after:
                parts.append(self._block("处理后译文", after))
            if current:
                parts.append(self._block("当前译文", current))
        self.item_body.setHtml("".join(parts))
        # 审计要完整展示前/后/当前/状态：给正文更大的最小高度（普通短内容仍是自适应）。
        evidence_lines = []
        for entry in item.get("triage_evidence") or []:
            if not isinstance(entry, dict):
                continue
            detail = str(entry.get("detail") or "")
            line = (f"<p><b>{_escape(i18n.tr('证据'))}{sep}</b>"
                    f"{_escape(str(entry.get('kind') or ''))} · "
                    f"{_escape(str(entry.get('ref') or ''))}")
            if detail:
                line += f" · {_escape(detail)}"
            evidence_lines.append(line + "</p>")
        who = i18n.tr(_ACTOR_TYPE_LABELS.get(
            str(item.get("triage_actor_type") or ""),
            str(item.get("triage_actor_type") or "")))
        evidence_lines.append(f"<p><b>{_escape(i18n.tr('处置'))}{sep}</b>"
                              f"{_escape(disposition_name)} · {_escape(who)}</p>")
        if item.get("triage_candidate_id"):
            state = i18n.tr('已提交') if committed else i18n.tr('未提交')
            evidence_lines.append(f"<p><b>{_escape(i18n.tr('候选'))}{sep}</b>"
                                  f"{_escape(str(item['triage_candidate_id']))} · "
                                  f"{_escape(state)}</p>")
        if snap.get("summary"):
            evidence_lines.append(f"<p><b>{_escape(i18n.tr('问题'))}{sep}</b>"
                                  f"{_escape(str(snap['summary']))}</p>")
        self.evidence_body.setHtml("".join(evidence_lines))
        self.audit_hint.setText("")
        # 审计记录没有对应的可定位原文面板。
        self._sync_source()

    def _emit_audit_revoke(self) -> None:
        if self._busy or not self._selected_audit_key:
            return
        self.triageRevokeRequested.emit(self._selected_audit_key)

    def _render_detail_empty(self) -> None:
        self.item_title.setText("")
        self.item_body.setHtml("")
        self.evidence_body.setHtml("")
        self.audit_hint.setText("")
        self._selected_id = ""
        self._selected_audit_key = ""
        self.audit_revoke_button.setVisible(False)
        self._set_actions(source=False, task=False, decision=False,
                          suggestion=False, edit=False)
        self.hint.setText("")
        self.conflict.setVisible(False)
        self._sync_source()

    def _render_detail(self) -> None:
        if self._selected_audit_key:
            self._render_audit()
            return
        self.audit_revoke_button.setVisible(False)
        item = self.selected_item()
        has = bool(item)
        for widget in (self.item_title, self.item_chip, self.item_body,
                       self.evidence_body):
            widget.setVisible(has)
        if not has:
            self.item_title.setText("")
            self.item_body.setHtml("")
            self.evidence_body.setHtml("")
            self.audit_hint.setText("")
            self._set_actions(source=False, task=False, decision=False,
                              suggestion=False, edit=False)
            self.hint.setText("")
            self.conflict.setVisible(False)
            self._sync_source()
            return
        self.item_title.setText(self._item_title(item))
        chip_text = i18n.tr_dynamic(str(item.get("severity_label") or ""))
        self.item_chip.setText(chip_text)
        self.item_chip.set_tone(_SEVERITY_TONE.get(str(item.get("severity")), "info"))
        # 没有严重度文字的项不留一个只有颜色、没有字的芯片（颜色不是唯一信号）。
        self.item_chip.setVisible(bool(chip_text))
        if item.get("term_card"):
            # 术语卡的重点是「哪些段还在用旧译法」，不是某一段的问题描述。
            self.item_body.setHtml(self._term_html(item))
            self._set_evidence(self._evidence_html(item, _colon()))
            self.audit_hint.setText("")
            self._sync_source()
            self._sync_buttons()
            return
        sep = _colon()
        task_item = str(item.get("kind") or "") in _REVIEW_TASK_KINDS
        task_copy = _localized_task_copy(item) if task_item else {}
        # 工作区：问题 → 原文 / 当前译文 / 建议译文（各自独立的区块）。
        problem_parts: list[str] = []
        for label, key in (("问题", "summary"), ("原因", "reason"),
                           ("说明", "explanation"), ("建议", "recommendation")):
            value = str(item.get(key) or "").strip()
            if value:
                # 程序自有的确定性文案以中文为 i18n key，走 tr_dynamic 命中即本地化；
                # 模型/文档文本查不到 key，原样保留（不会被改写）。
                override = task_copy.get(key) if task_item else ""
                value = override or i18n.tr_dynamic(value)
            if value and not any(value in part for part in problem_parts):
                muted = style_module.html_color("text-secondary", over="bg-surface")
                problem_parts.append(
                    f'<p style="font-size:12px;color:{muted};line-height:140%">'
                    f"<b>{_escape(i18n.tr(label))}{sep}</b>{_escape(value)}</p>")
        # Keep the concise finding first, then prioritize bilingual reading;
        # the complete explanation remains available below it in the same view.
        summary = problem_parts[:1]
        details = problem_parts[1:]
        staged = (item.get("triage_candidate_id") and not item.get("triage_committed")
                  and item.get("triage_candidate_status") == "pending")
        if staged:
            summary.append(self._block("状态", i18n.tr('仅暂存未采用')))
        self.item_body.setHtml("".join(summary) + self._translation_html(item)
                               + "".join(details))
        # 证据检查器：术语参考 / 已记录决定 / 分组摘要；空则给说明，不留白。
        self._set_evidence(self._evidence_html(item, sep))
        self.audit_hint.setText("")
        summary_a11y = (task_copy.get("summary") if task_item else "") \
            or str(item.get("summary") or "")
        self.item_body.setAccessibleDescription(
            " · ".join(value for value in (self._item_title(item), chip_text,
                                          summary_a11y)
                       if value))
        entity = self._entity_for(item)
        if entity:
            self.conflict.set_entity(entity, busy=self._busy)
        self.conflict.setVisible(bool(entity))
        self._sync_buttons()
        self._sync_source()

    def _set_evidence(self, html: str) -> None:
        """An empty inspector remains discoverable but gives reading space back."""
        populated = bool(html.strip())
        self.evidence_area.setMinimumWidth(280 if populated else 180)
        self.evidence_area.setMaximumWidth(320 if populated else 200)
        self.evidence_body.setHtml(html or f"<p>{_escape(i18n.tr('此项目没有额外证据。'))}</p>")

    # ================= 原页 =================
    def set_pdf_job(self, job_id: str, state: dict | None = None) -> None:
        """PDF 任务才在右栏显示原页（``job_id`` 为空：没有原页可用）。

        ``state`` 是任务状态：给了它，定位就不必每次都把整份状态再读一遍
        （翻译页那一份原页也是这么做的）。
        """
        self._pdf_ok = bool(job_id)
        self._pdf_closed = False
        self._pdf_index = -1
        self._pdf_stale = False
        self._state = dict(state or {})
        self.pdf.load(job_id)
        self._sync_source()

    def set_source_state(self, state: dict) -> None:
        """任务状态换了（又跑了一轮翻译/审校）：定位按新的原文来。"""
        self._state = dict(state or {})

    def _segment_for_source(self) -> int:
        """选中项对应的段：普通发现项取自己的段，译名项取第一次出现。"""
        item = self.selected_item()
        index = item.get("segment_index")
        if isinstance(index, int):
            return index
        indexes = [int(value) for value in item.get("segment_indexes") or []
                   if isinstance(value, int)]
        return indexes[0] if indexes else -1

    def _sync_source(self, index: int | None = None) -> None:
        """让内嵌原页跟着选中项：翻到那一段并高亮；段号没变就不重渲染。

        给了 ``index``（段号链接）就按它定位，不再看选中项是第几段。
        """
        forced = index is not None
        if index is None:
            index = self._segment_for_source()
        has = self._pdf_ok and index >= 0
        self.source_button.setVisible(has)
        self.pdf.setVisible(has and not self._pdf_closed)
        if not has or self._pdf_closed:
            self._pdf_index = -1
            return
        if not forced and index == self._pdf_index and not self._pdf_stale:
            return
        self._pdf_index = index
        self._pdf_stale = False
        self.pdf.locate(index, state=self._state or None)

    def _open_source(self) -> None:
        """「原页」按钮：把收起的原页重新打开，并回到当前选中项。"""
        self._pdf_closed = False
        self._pdf_index = -1
        self._sync_source()

    def _close_source(self) -> None:
        self._pdf_closed = True
        self._sync_source()

    def _locate_segment(self, index: int) -> None:
        """段号链接：有原页就在原页里定位；没有原页才退回"跳过去改译文"。"""
        if not self._pdf_ok:
            self.editRequested.emit(int(index))
            return
        self._pdf_closed = False
        self._sync_source(int(index))

    def _set_actions(self, *, source: bool, task: bool, decision: bool,
                     suggestion: bool, edit: bool) -> None:
        self.source_confirm_box.setVisible(source)
        self.source_note_edit.setVisible(source)
        self.confirm_source_button.setVisible(source)
        self.apply_button.setVisible(suggestion)
        self.open_button.setVisible(decision)
        self.confirm_button.setVisible(decision)
        self.preserve_button.setVisible(decision)
        self.review_button.setVisible(task)
        self.edit_button.setVisible(edit)
        self.retranslate_button.setVisible(edit)
        self.note_edit.setVisible(decision or suggestion)
        item = self.selected_item() or {}
        self.style_rule_button.setVisible(str(item.get("category") or "") == "style")

    def _set_button_kind(self, button, kind: str, cache: str) -> None:
        """只在层级真的变了时重新 polish（备注框每次输入都会走一遍这里）。"""
        if getattr(self, cache, "") == kind:
            return
        style_module.set_button(button, kind)
        icon_name = {"_apply_kind": "sparkles", "_edit_kind": "translate",
                     "_confirm_kind": "check", "_review_kind": "shield-check",
                     "_source_kind": "check-circle"}.get(cache)
        if icon_name:
            button.setIcon(icons.icon(icon_name, 16,
                                     "text-on-accent" if kind == "primary"
                                     else "text-secondary"))
        setattr(self, cache, kind)

    def _sync_buttons(self, *_args) -> None:
        item = self.selected_item()
        if not item:
            return
        if self._entity_for(item):
            self._set_actions(source=False, task=False, decision=False,
                              suggestion=False, edit=False)
            self.style_rule_button.setVisible(False)
            self.hint.setText("")
            return
        kind = str(item.get("kind") or "")
        index = item.get("segment_index")
        has_segment = isinstance(index, int)
        task = kind in _REVIEW_TASK_KINDS
        source = (not task) and self._is_source_item(item)
        decidable = bool(item.get("decidable"))
        core_finding = bool(item.get("core_finding"))
        # 只有"人工确认型"的核心发现项和旧式发现项能记人工决定；
        # 纯建议（不阻止交付）只能看、改、应用。
        decision = (not task) and (not source) and decidable and (
            (not core_finding) or bool(item.get("requires_human_confirmation")))
        suggestion = (not task) and (not source) and bool(
            str(item.get("suggested_target") or "").strip()) and has_segment
        self._set_actions(source=source, task=task, decision=decision,
                          suggestion=suggestion, edit=has_segment and not task)
        # 呈现层的行动优先级：按项的真实状态选“唯一实心主行动”，而不是只看严重度。
        # 有建议 → 暂存候选；整段未翻译、或无法安全记录人工决定（证据/定位不足）→
        # 主行动是“修改译文”，把“确认已解决”降为次要；其余可判定项才把“确认已解决”
        # 作为主行动。
        target_text = str(item.get("target") or "").strip()
        informational = str(item.get("severity") or "") == "informational"
        completion_finding = (item.get("category") == "translation_completion"
                              and not item.get("handled") and not item.get("decision"))
        staged = (item.get("triage_candidate_id") and not item.get("triage_committed")
                  and item.get("triage_candidate_status") == "pending")
        term_conflict = bool(item.get("term_card") and any(
            row.get("target") and not row.get("uses_frozen", True)
            for row in item.get("term_segments") or []))
        edit_primary = (not informational and not suggestion and not source and not task and has_segment
                        and (not target_text or completion_finding or staged or not decidable
                             or item.get("category") == "style"))
        self._set_button_kind(
            self.apply_button, "primary" if suggestion and not informational else "secondary", "_apply_kind")
        self._set_button_kind(
            self.edit_button, "primary" if edit_primary else "secondary", "_edit_kind")
        self._set_button_kind(
            self.confirm_button,
            "primary" if (decision and not informational and not suggestion
                          and not edit_primary and not term_conflict)
            else "secondary", "_confirm_kind")
        self._set_button_kind(self.review_button,
                              "primary" if task and has_segment and not informational else "secondary",
                              "_review_kind")
        self._set_button_kind(self.confirm_source_button,
                              "primary" if source and not informational else "secondary",
                              "_source_kind")
        if task:
            self.review_button.setText(i18n.tr({
                "failed": "重试审校本段", "stale": "重新审校本段",
                "missing": "审校本段", "skipped": "审校本段"}[kind]))
            self.edit_button.setVisible(has_segment)
        enabled = not self._busy
        self.apply_button.setEnabled(enabled and suggestion)
        self.open_button.setEnabled(enabled and decision)
        self.confirm_button.setEnabled(enabled and decision)
        self.preserve_button.setEnabled(enabled and decision)
        self.review_button.setEnabled(enabled and task and has_segment)
        self.edit_button.setEnabled(enabled and has_segment)
        self.retranslate_button.setEnabled(enabled and has_segment and not task)
        self.confirm_source_button.setEnabled(
            enabled and source and has_segment
            and self.source_confirm_box.isChecked()
            and bool(self.source_note_edit.text().strip()))
        if source:
            self.hint.setText(i18n.tr(
                '这段在 OCR 清洗前后都疑似残片。请先回看 PDF 原页确认它确实是正文；'
                '核对前不会把它发给翻译或审校模型。'))
        elif kind == "skipped":
            self.hint.setText(i18n.tr(
                '这段按策略跳过，没有独立语义审校结论；跳过不等于审校通过。'
                '如需覆盖本段，可单独运行审校。'))
        elif item.get("invariant"):
            self.hint.setText(i18n.tr(
                '这是交付完整性检查：译文本身不能直接交付。改好译文或重译本段后会自动放行导出。'))
        elif not decidable and kind == "finding":
            self.hint.setText(i18n.tr(
                '此发现来自临时定位，无法安全记录人工决定；请先修改译文或重新审校该段。'))
        elif kind == "finding" and not decision and not suggestion:
            self.hint.setText(i18n.tr(
                '该建议不需要人工决定，也不会阻止交付；可修改译文，或处理下一项。'))
        else:
            self.hint.setText("")

    # ================= 键盘与导航 =================
    def shortcut_handlers(self) -> dict:
        """测试 / 调用方用：键位 → 动作。N=下一项 A=暂存建议 O=确认问题存在
        C=确认已解决 K=保留当前译文 E=修改译文。"""
        return dict(self._shortcut_handlers)

    def _typing(self) -> bool:
        """焦点在文本输入控件里：快捷键让位给打字。"""
        focus = QApplication.focusWidget()
        return isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QComboBox))

    def _sync_shortcut_focus(self, *_args) -> None:
        # A guarded QShortcut still consumes the key before its handler runs.
        # Disable it while typing so letters reach the editor in the first place.
        enabled = not self._typing()
        for shortcut in self._shortcuts:
            shortcut.setEnabled(enabled)

    def _visible_index(self) -> int:
        """当前列表行在 ``_visible`` 里的下标；分组标题行不算（返回 -1）。"""
        row = self.list.currentRow()
        seen = -1
        for position in range(self.list.count()):
            if self.list.item(position).data(Qt.ItemDataRole.UserRole):
                seen += 1
                if position == row:
                    return seen
        return -1

    def _goto_next_item(self) -> None:
        if self._typing():
            return
        index = self._visible_index()
        if 0 <= index + 1 < len(self._visible):
            self.list.setCurrentRow(self._list_row(index + 1))

    def _next_item_id(self) -> str:
        index = self._visible_index()
        if 0 <= index + 1 < len(self._visible):
            return str(self._visible[index + 1].get("id") or "")
        return ""

    def consume_advance_hint(self) -> bool:
        """窗口层在**动作成功**后调用：前进到处置前记下的下一项。

        动作失败时窗口层不调用——队列停在原地，未处理的发现项不会被跳过，
        也不会被顺手标成已解决。"""
        hint, self._advance_hint = self._advance_hint, ""
        return bool(hint) and self.select_item_id(hint)

    # ================= 信号 =================
    def _emit_resolve(self, action: str) -> None:
        item = self.selected_item()
        if item:
            self._advance_hint = self._next_item_id()
            self.resolveRequested.emit(
                [str(item.get("finding_id") or "")], action, self.note_edit.text().strip())

    def _emit_apply(self) -> None:
        item = self.selected_item()
        if item:
            self._advance_hint = self._next_item_id()
            self.applySuggestionRequested.emit(dict(item), self.note_edit.text().strip())

    def _emit_edit(self) -> None:
        item = self.selected_item()
        if isinstance(item.get("segment_index"), int):
            self.editRequested.emit(int(item["segment_index"]))

    def _emit_review(self) -> None:
        item = self.selected_item()
        if isinstance(item.get("segment_index"), int):
            self.reviewRequested.emit([int(item["segment_index"])])

    def _emit_retranslate(self) -> None:
        item = self.selected_item()
        if isinstance(item.get("segment_index"), int):
            self.retranslateRequested.emit(int(item["segment_index"]))

    def _emit_confirm_source(self) -> None:
        item = self.selected_item()
        if isinstance(item.get("segment_index"), int):
            self.confirmSourceRequested.emit(
                int(item["segment_index"]), self.source_note_edit.text().strip())

    # 测试辅助
    def select_item_id(self, item_id: str) -> bool:
        for attempt in range(2):
            for position, item in enumerate(self._visible):
                if item.get("id") == item_id:
                    self.list.setCurrentRow(self._list_row(position))
                    return True
            # 目标不在展开的组里（例如跳到一条仅供参考的项）：展开它所在的组再找。
            if attempt:
                break
            target = next((item for item in list(self._items) + list(self._handled)
                           if item.get("id") == item_id), None)
            if target is None:
                break
            key = self._group_of(target)
            if self._expanded.get(key):
                break
            self._expanded[key] = True
            self._rebuild_list()
        return False
