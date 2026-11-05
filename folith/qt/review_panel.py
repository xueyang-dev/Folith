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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
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
    triageToggleRequested = Signal(bool)            # 本任务的自动处理开关

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
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setProperty("role", "muted")
        self.review_all_button = button(i18n.tr('审校全部段落'), "shield-check")
        self.review_all_button.clicked.connect(self.reviewAllRequested.emit)
        self.concept_button = button(i18n.tr('概念决策'), "list")
        self.concept_button.clicked.connect(self.conceptRequested.emit)
        # clicked 只在用户操作时发出；apply() 同步勾选状态不会反过来写任务。
        self.triage_toggle = QCheckBox(i18n.tr('自动处理有确定依据的问题（实验）'))
        self.triage_toggle.setObjectName("reviewTriageToggle")
        self.triage_toggle.setToolTip(i18n.tr(
            '只对本任务生效。有确定依据的发现会移入「自动处理（审计）」组，每条都能撤销；'
            '不修改译文，不调用模型，也不放宽交付门禁。'))
        self.triage_toggle.setAccessibleDescription(self.triage_toggle.toolTip())
        self.triage_toggle.clicked.connect(
            lambda checked: self.triageToggleRequested.emit(bool(checked)))
        top = QHBoxLayout()
        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        text_column.addWidget(self.headline)
        text_column.addWidget(self.detail)
        top.addLayout(text_column, 1)
        top.addWidget(self.review_all_button, 0, Qt.AlignmentFlag.AlignTop)

        # ---- 左：队列 --------------------------------------------------------
        self.list = QListWidget()
        self.list.setObjectName("reviewQueue")
        self.list.setMinimumWidth(260)
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

        # ---- 右：详情 + 动作 -------------------------------------------------
        self.item_title = QLabel("")
        self.item_title.setObjectName("reviewItemTitle")
        self.item_title.setWordWrap(True)
        self.item_chip = style_module.Chip("", "info")
        self.item_body = QTextBrowser()
        self.item_body.setObjectName("reviewDetail")
        self.item_body.setOpenExternalLinks(False)
        self.item_body.setMinimumHeight(140)
        self.item_body.setAccessibleName(i18n.tr('审校详情'))

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
        action_row = components.FlowRow(spacing=6, fill=False)
        for widget in (self.apply_button, self.open_button, self.confirm_button,
                       self.confirm_source_button, self.preserve_button,
                       self.review_button, self.edit_button,
                       self.retranslate_button, self.style_rule_button):
            action_row.addWidget(widget)

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

        title_row = QHBoxLayout()
        title_row.addWidget(self.item_title, 1)
        title_row.addWidget(self.source_button, 0, Qt.AlignmentFlag.AlignTop)
        title_row.addWidget(self.item_chip, 0, Qt.AlignmentFlag.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        column.addWidget(self.item_body, 1)
        column.addWidget(self.conflict)
        column.addWidget(self.source_confirm_box)
        column.addWidget(self.source_note_edit)
        column.addWidget(self.note_edit)
        column.addLayout(action_row)
        column.addWidget(self.hint)
        column.addWidget(self.shortcut_hint)

        # 详情卡：左边是问题与决定，右边是这一段在原文里的位置与上下文。
        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)
        detail_row.addLayout(column, 2)
        detail_row.addWidget(self.pdf, 1)

        detail_card = Card()
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(10)
        detail_layout.addLayout(title_row)
        detail_layout.addLayout(detail_row, 1)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 1)
        body.addWidget(detail_card, 2)

        # 队列与详情装在一个容器里：审校队列整个是空的时，它整体让位给一个空状态
        # （设计审校 40 号 4.1：不再画五个 0 计数的筛选、一个空列表和一块空白详情）。
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)
        content_layout.addLayout(top)
        content_layout.addLayout(body, 1)
        self.empty_state = style_module.EmptyState(icon="shield-check", page=True,
                                                   action=i18n.tr('审校全部段落'))
        self.empty_state.actionTriggered.connect(self.reviewAllRequested.emit)
        self.empty_state.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        actions = QHBoxLayout()
        actions.addWidget(self.triage_toggle)
        actions.addStretch(1)
        actions.addWidget(self.concept_button)
        layout.addLayout(actions)
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
        self.triage_toggle.setChecked(self._triage_on)
        self.triage_toggle.setEnabled(not busy)
        self._auto = [item for item in (self._view.get("auto_handled_items") or [])]
        self._group_summaries = {
            str(group.get("group_id") or ""): dict(group)
            for group in (self._view.get("finding_groups") or [])}
        readiness = self._view.get("readiness") or {}
        headline, detail = _coverage_texts(self._view)
        self.headline.setText(headline)
        self.detail.setText(detail)
        if not self._initialised:
            # 第一次打开：有「必须处理」就只展开它；没有就把第一个非空组展开，
            # 免得左栏看起来是空的。审计组永远不参与这个回退——它默认收起，
            # 收起不等于看不见：组头计数与页脚的总数守恒都在。
            self._initialised = True
            counts = self._group_counts()
            if not counts.get("must"):
                first = next((key for key in GROUP_ORDER
                              if key != "auto" and counts.get(key)), "")
                if first:
                    self._expanded = {key: key == first for key in GROUP_ORDER}
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
        self.empty_label.setVisible(not self._visible)
        self.empty_label.setText(
            i18n.tr('展开分组查看其中的项目。')
            if self._items or self._handled or self._auto
            else i18n.tr('审校队列是空的：没有需要人工处理的项目。'))
        self._render_detail()

    def _add_group_header(self, key: str, count: int) -> None:
        entry = QListWidgetItem()
        entry.setFlags(Qt.ItemFlag.NoItemFlags)
        entry.setSizeHint(QSize(0, 34))
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
            # 合成项（待审/需重审/失败/跳过）的摘要也是界面文案；
            # 发现项的摘要是模型产出，必须原样显示。
            summary = _localized_task_copy(item).get("summary") \
                or i18n.tr_dynamic(summary)
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
        # 没有 UserRole 标识：导航（_list_row/_visible_index）会跳过这一行。
        entry.setFlags(Qt.ItemFlag.ItemIsEnabled)
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
        # #289: 审计行没有队列标识（不参与处置循环）：data 为空时选中项为空。
        self._selected_id = (str(current.data(Qt.ItemDataRole.UserRole) or "")
                             if current else "")
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
        """一张术语卡：术语、冻结/旧译法、受影响段落并排对照。"""
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
        shown = rows[:TERM_ROWS_SHOWN]
        if shown:
            cells = []
            for row in shown:
                mark = "" if row.get("uses_frozen", True) else (
                    f" <b>【{_escape(i18n.tr('未用冻结译法'))}】</b>")
                cells.append(
                    "<tr>"
                    f"<td valign=\"top\"><b>{_escape(i18n.tr('第 {v0} 段', v0=row.get('number')))}</b></td>"
                    f"<td valign=\"top\">{_escape(_trim(row.get('source')))}</td>"
                    f"<td valign=\"top\">{_escape(_trim(row.get('target')))}{mark}</td>"
                    "</tr>")
            parts.append(
                "<table width=\"100%\" cellspacing=\"0\" cellpadding=\"3\">"
                f"<tr><td></td><td><b>{_escape(i18n.tr('原文'))}</b></td>"
                f"<td><b>{_escape(i18n.tr('当前译文'))}</b></td></tr>"
                + "".join(cells) + "</table>")
        if total > len(shown):
            parts.append(f"<p>{_escape(i18n.tr('另有 {v0} 段未列出', v0=total - len(shown)))}</p>")
        return "".join(parts)

    def _render_detail(self) -> None:
        item = self.selected_item()
        has = bool(item)
        for widget in (self.item_title, self.item_chip, self.item_body):
            widget.setVisible(has)
        if not has:
            self.item_title.setText("")
            self.item_body.setHtml("")
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
            self._sync_source()
            return
        sep = _colon()
        task_item = str(item.get("kind") or "") in _REVIEW_TASK_KINDS
        parts = []
        if item.get("entity_source"):
            # #277: 概念决策类术语卡补上决定参考字段，渲染与 #102 过期译法卡同源。
            # 没有锁定译法时投影里就是空值，这里自然不画冻结/旧译法两行。
            total = int(item.get("term_segment_total") or 0)
            if total > 0:
                parts.append(_decision_reference_html(
                    str(item.get("term_frozen_target") or "").strip(),
                    str(item.get("term_previous_target") or "").strip(),
                    total))
        decision = i18n.tr_dynamic(str(item.get("decision_label") or ""))
        if decision:
            # 已经记下的人工判断：确认问题存在之后问题仍在队列里，必须说清它
            # 不是"待处理但没人看过"。
            parts.append(f"<p><b>{_escape(i18n.tr('已记录的决定'))}{sep}</b>"
                         f"{_escape(decision)}</p>")
        task_copy = _localized_task_copy(item) if task_item else {}
        for label, key in (("问题", "summary"), ("原因", "reason"),
                           ("说明", "explanation"), ("建议", "recommendation")):
            value = str(item.get(key) or "").strip()
            if value and task_item:
                value = task_copy.get(key) or i18n.tr_dynamic(value)
            if value and not any(value in part for part in parts):
                parts.append(f"<p><b>{_escape(i18n.tr(label))}{sep}</b>{_escape(value)}</p>")
        from folith.textual import text_direction

        for label, key, span_key in (("原文", "source", "source_span"),
                                     ("当前译文", "target", "target_span"),
                                     ("建议译文", "suggested_target", None)):
            value = str(item.get(key) or "").strip()
            if value:
                # 原文与译文各按自己的书写方向排：中文原文从左往右、阿拉伯语译文从右往左，
                # 同一个面板里混排（试用反馈）。
                rtl = text_direction(value, None) == "rtl"
                block = ' dir="rtl"' if rtl else ""
                span = str(item.get(span_key) or "") if span_key else ""
                parts.append(f"<p><b>{_escape(i18n.tr(label))}{sep}</b></p>"
                             f"<p{block} style=\"margin-top:2px\">"
                             f"{self._highlight(value, span)}</p>")
        self.item_body.setHtml("".join(parts))
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
        # 同一时刻只留一个实心主按钮：有建议时"暂存为候选"是下一步，没有建议时
        # "确认已解决"才是。两个主按钮并排会让人不知道该点哪个（设计审校 40）。
        self._set_button_kind(
            self.apply_button, "primary" if suggestion else "secondary", "_apply_kind")
        self._set_button_kind(
            self.confirm_button,
            "primary" if decision and not suggestion else "secondary", "_confirm_kind")
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
