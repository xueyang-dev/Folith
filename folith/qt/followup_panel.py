"""检查与修复：任务跑完后留下的事项，一项一张卡，长得都一样。

每一项都是同一个结构（acceptance-feedback #21）：状态图标（✕ 必须处理 / ⚠ 建议处理 /
✓ 已检查 / ⓘ 工具）+ 标题与数量 + 一句"这意味着什么" + 一个主动作；长的证据（失败
批次、导入报告、改动预览）收在可展开的详情里。按严重程度排序：

* 翻译失败的批次、翻译中断——译文缺了，交付不了；
* 全文理解降级、标注未完成、文档画像缺失——质量依据弱了，可以补跑 / 补填；
* 同一个专名在不同段译法不一——列出每种译法出现在哪几段，统一或确认；
* 疑似原文碎片；
* 原文导入（已检查的提取范围）与重新扫描 PDF（工具，默认收起）。

只做展示与发信号；写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import icons
from . import style as style_module
from . import theme as theme_module


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


#: tone → (图标, 图标颜色 token)。
TONES = {
    "danger": ("alert", "danger"),
    "warning": ("alert", "warning"),
    "ok": ("check-circle", "success"),
    "info": ("info", "text-secondary"),
}
#: 需要用户处理的 tone，按严重程度。
ISSUE_TONES = ("danger", "warning")


def _tool(text: str, *, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    style_module.set_button(button, "secondary", small=True)
    return button


class IssueCard(QFrame):
    """一项：图标 | 标题（+ 展开箭头）/ 一句说明 / 内容 / 详情（可收起）/ 动作。"""

    def __init__(self, tone: str = "warning", *, collapsible: bool = False,
                 expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("issueCard")
        self._tone = ""
        self.icon = icons.IconLabel(size=16)
        self.title = QLabel("")
        self.title.setObjectName("issueTitle")
        self.title.setWordWrap(True)
        self.toggle = QToolButton()
        self.toggle.setObjectName("issueToggle")
        self.toggle.setFixedSize(20, 20)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setCheckable(True)
        self.toggle.setIconSize(QSize(14, 14))
        self.toggle.setAccessibleName(i18n.tr('详情'))
        self.toggle.toggled.connect(self._sync_toggle)
        self.toggle.setVisible(collapsible)
        self.summary = QLabel("")
        self.summary.setObjectName("issueSummary")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme_module.SPACE["sm"])
        self.details = QWidget()
        self.details_layout = QVBoxLayout(self.details)
        self.details_layout.setContentsMargins(0, 0, 0, 0)
        self.details_layout.setSpacing(theme_module.SPACE["sm"])
        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(theme_module.SPACE["xs"])
        self._actions_end = False

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme_module.SPACE["xs"])
        head.addWidget(self.title, 1)
        head.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["xs"] + 2)
        column.addLayout(head)
        column.addWidget(self.summary)
        column.addLayout(self.body)
        column.addWidget(self.details)
        column.addLayout(self.actions)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"] + 2,
                                 theme_module.SPACE["sm"], theme_module.SPACE["sm"] + 2)
        outer.setSpacing(theme_module.SPACE["sm"])
        outer.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(column, 1)
        self.set_tone(tone)
        self.toggle.setChecked(expanded or not collapsible)
        self._sync_toggle(self.toggle.isChecked())

    # ---- 外观 ----
    def set_tone(self, tone: str) -> None:
        tone = tone if tone in TONES else "info"
        if tone == self._tone:
            return
        self._tone = tone
        name, color = TONES[tone]
        self.icon.set_icon(name, color)
        self.setProperty("tone", tone)
        style_module.repolish(self)

    def tone(self) -> str:
        return self._tone

    def set_title(self, text: str) -> None:
        self.title.setText(text)

    def set_summary(self, text: str) -> None:
        self.summary.setText(text)
        self.summary.setVisible(bool(text))

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

    def finish_actions(self) -> None:
        if not self._actions_end:
            self.actions.addStretch(1)
            self._actions_end = True

    # ---- 展开 / 收起 ----
    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(bool(expanded))

    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def _sync_toggle(self, checked: bool) -> None:
        self.details.setVisible(bool(checked))
        self.toggle.setIcon(icons.icon("chevron-up" if checked else "chevron-down", 14,
                                       "text-secondary"))
        self.toggle.setToolTip(i18n.tr('收起详情') if checked else i18n.tr('展开详情'))


class FollowupPanel(QWidget):
    retryRequested = Signal(str)                    # "understanding" | "annotation" | "scan_pages"
    resumeRequested = Signal()
    previewRequested = Signal(str, str)             # source, target
    repairRequested = Signal(str, str, list)        # source, target, preview
    acceptRequested = Signal(str)                   # source
    rescanRequested = Signal(dict)                  # backend / mode / language / quality
    excludeFragmentsRequested = Signal(list)        # 疑似碎片段的索引
    locateRequested = Signal(int)                   # 定位到某一段（段索引）
    profileEditRequested = Signal()                 # 填写文档画像

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._preview: list[dict] = []
        self._busy = False
        self._rescan_seeded = False
        self._fragments: list[int] = []
        self._failures: list[dict] = []

        def label(*, muted: bool = False, rich: bool = False) -> QLabel:
            widget = QLabel("")
            widget.setWordWrap(True)
            if muted:
                widget.setProperty("role", "muted")
            if rich:
                widget.setTextFormat(Qt.TextFormat.RichText)
            return widget

        # ---- 总览：一句话说清"还有几件事" ---------------------------------------
        self.overview = QLabel("")
        self.overview.setObjectName("groupCaption")
        self.empty = IssueCard("ok")
        self.empty.set_title(i18n.tr('没有待处理的事项。'))
        self.empty.set_summary(i18n.tr('译文、全文理解与专名都没有发现需要你处理的问题。'))

        # ---- 翻译失败的批次（必须处理） --------------------------------------------
        self.failures_card = IssueCard("danger", collapsible=True, expanded=False)
        self.failures_list = QListWidget()
        self.failures_list.setObjectName("issueList")
        self.failures_list.setWordWrap(True)
        self.failures_list.itemDoubleClicked.connect(self._locate_failure)
        self.failures_card.details_layout.addWidget(self.failures_list)
        self.failures_locate = _tool(i18n.tr('定位到这一段'), primary=True)
        self.failures_locate.clicked.connect(lambda: self._locate_failure())
        self.failures_card.add_action(self.failures_locate)
        self.failures_card.finish_actions()
        # 旧名字：测试与窗口层读它们。
        self.failures_title = self.failures_card.title
        self.failures_text = self.failures_card.summary

        # ---- 翻译中断 / 暂停 ----------------------------------------------------
        self.recovery_card = IssueCard("warning")
        self.resume_button = _tool(i18n.tr('继续翻译'), primary=True)
        self.resume_button.clicked.connect(self.resumeRequested.emit)
        self.recovery_card.add_action(self.resume_button)
        self.recovery_card.finish_actions()
        self.recovery_title = self.recovery_card.title
        self.recovery_text = self.recovery_card.summary

        # ---- 全文理解 / 自动标注（可补跑） ------------------------------------------
        self.understanding_card = IssueCard("warning")
        self.understanding_button = _tool(i18n.tr('补跑失败的摘要'), primary=True)
        self.understanding_button.clicked.connect(
            lambda: self.retryRequested.emit("understanding"))
        self.understanding_card.add_action(self.understanding_button)
        self.understanding_card.finish_actions()
        # 读失败的扫描页：译稿缺页，挡住交付；继续时只补读这些页。
        self.scan_pages_card = IssueCard("danger", collapsible=True, expanded=False)
        self.scan_pages_list = QListWidget()
        self.scan_pages_list.setMaximumHeight(140)
        self.scan_pages_card.details_layout.addWidget(self.scan_pages_list)
        self.scan_pages_button = _tool(i18n.tr('补读失败页'), primary=True)
        self.scan_pages_button.clicked.connect(
            lambda: self.retryRequested.emit("scan_pages"))
        self.scan_pages_card.add_action(self.scan_pages_button)
        self.scan_pages_card.finish_actions()
        self.annotation_card = IssueCard("warning")
        self.annotation_button = _tool("", primary=True)
        self.annotation_button.clicked.connect(
            lambda: self.retryRequested.emit("annotation"))
        self.annotation_card.add_action(self.annotation_button)
        self.annotation_card.finish_actions()
        # 两张卡共用一个外壳：旧代码 / 测试用 retry_card 判断"有没有可补跑的"。
        self.retry_card = QWidget()
        retry = QVBoxLayout(self.retry_card)
        retry.setContentsMargins(0, 0, 0, 0)
        retry.setSpacing(theme_module.SPACE["sm"])
        retry.addWidget(self.scan_pages_card)
        retry.addWidget(self.understanding_card)
        retry.addWidget(self.annotation_card)

        # ---- 文档画像缺失 --------------------------------------------------------
        self.profile_card = IssueCard("warning")
        self.profile_card.set_title(i18n.tr('文档画像缺失'))
        self.profile_button = _tool(i18n.tr('填写画像…'), primary=True)
        self.profile_button.clicked.connect(self.profileEditRequested.emit)
        self.profile_card.add_action(self.profile_button)
        self.profile_card.finish_actions()

        # ---- 专名译法不一 --------------------------------------------------------
        self.entity_card = IssueCard("warning")
        self.entity_title = self.entity_card.title
        self.entity_hint = self.entity_card.summary
        self.entity_box = style_module.ComboBox()
        self.entity_box.currentIndexChanged.connect(self._entity_changed)
        self.entity_variants = label(rich=True)
        self.entity_variants.setObjectName("issueEvidence")
        self.entity_variants.linkActivated.connect(self._evidence_link)
        self.target_edit = QLineEdit()
        self.target_edit.setPlaceholderText(i18n.tr('统一使用的译名'))
        self.target_edit.textChanged.connect(self._target_changed)
        self.preview_hint = label()
        self.preview_hint.setObjectName("issueSummary")
        self.preview_view = QTextBrowser()
        self.preview_view.setObjectName("repairPreview")
        self.preview_view.setFixedHeight(90)
        for widget in (self.entity_box, self.entity_variants, self.target_edit,
                       self.preview_hint, self.preview_view):
            self.entity_card.body.addWidget(widget)
        self.repair_button = _tool(i18n.tr('统一为这个译名'), primary=True)
        self.repair_button.clicked.connect(self._emit_repair)
        self.accept_button = _tool(i18n.tr('各自符合语境'))
        self.accept_button.setToolTip(i18n.tr('确认这些译法在各自的段落里都是对的，不再提示。'))
        self.accept_button.clicked.connect(self._emit_accept)
        self.entity_card.add_action(self.repair_button)
        self.entity_card.add_action(self.accept_button)
        self.entity_card.finish_actions()

        # ---- 疑似原文碎片 --------------------------------------------------------
        self.fragments_card = IssueCard("warning")
        self.fragments_title = self.fragments_card.title
        self.fragments_text = self.fragments_card.summary
        self.fragments_button = _tool(i18n.tr('全部排除…'))
        self.fragments_button.clicked.connect(
            lambda: self.excludeFragmentsRequested.emit(list(self._fragments)))
        self.fragments_card.add_action(self.fragments_button)
        self.fragments_card.finish_actions()

        # ---- 原文导入：提取范围（已检查）与重新扫描（工具） ---------------------------
        self.import_caption = QLabel(i18n.tr('原文导入'))
        self.import_caption.setObjectName("groupCaption")
        self.extraction_card = IssueCard("ok", collapsible=True, expanded=False)
        self.extraction_card.set_title(i18n.tr('导入范围'))
        self.extraction_title = self.extraction_card.title
        self.extraction_summary = self.extraction_card.summary
        self.extraction_warning = label()
        self.extraction_warning.setObjectName("issueWarning")
        self.extraction_details = label(muted=True, rich=True)
        self.extraction_note = label(muted=True)
        self.extraction_note.setText(i18n.tr(
            '导出是重建的译文文档，不是原文件的格式保真输出：排版、表格、图片与样式不会还原。'))
        for widget in (self.extraction_warning, self.extraction_details, self.extraction_note):
            self.extraction_card.details_layout.addWidget(widget)

        self.rescan_card = IssueCard("info", collapsible=True, expanded=False)
        self.rescan_card.set_title(i18n.tr('重新扫描 PDF 原文'))
        self.rescan_title = self.rescan_card.title
        self.rescan_hint = self.rescan_card.summary
        self.rescan_card.set_summary(i18n.tr(
            '换提取来源、识别语言或精度后重读整份 PDF；已有译文需要重新翻译与审校。'))
        self.rescan_backend = style_module.ComboBox()
        for text, value in adapter.OCR_BACKEND_CHOICES:
            self.rescan_backend.addItem(i18n.tr(text), value)
        self.rescan_mode = style_module.ComboBox()
        for text, value in adapter.OCR_MODE_CHOICES:
            self.rescan_mode.addItem(i18n.tr(text), value)
        self.rescan_language = style_module.ComboBox()
        for text, value in adapter.ocr_language_choices():
            self.rescan_language.addItem(i18n.tr_dynamic(text), value)
        self.rescan_quality = style_module.ComboBox()
        for text, value in adapter.OCR_QUALITY_CHOICES:
            self.rescan_quality.addItem(i18n.tr_dynamic(text), value)
        self.rescan_status = label(muted=True)
        self.rescan_button = _tool(i18n.tr('重新扫描…'))
        self.rescan_button.clicked.connect(self._emit_rescan)
        for box in (self.rescan_backend, self.rescan_language):
            box.currentIndexChanged.connect(self._rescan_changed)
        for caption, box in ((i18n.tr('提取来源'), self.rescan_backend),
                             (i18n.tr('何时识别'), self.rescan_mode),
                             (i18n.tr('识别语言'), self.rescan_language),
                             (i18n.tr('识别精度'), self.rescan_quality)):
            self.rescan_card.details_layout.addWidget(style_module.field_label(caption))
            self.rescan_card.details_layout.addWidget(box)
        self.rescan_card.details_layout.addWidget(self.rescan_status)
        rescan_row = QHBoxLayout()
        rescan_row.setContentsMargins(0, 0, 0, 0)
        rescan_row.addWidget(self.rescan_button)
        rescan_row.addStretch(1)
        self.rescan_card.details_layout.addLayout(rescan_row)

        content = QWidget()
        content.setObjectName("pageCanvas")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 4, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        column.addWidget(self.overview)
        # 按严重程度：缺译文 → 中断 → 质量依据 → 专名 → 碎片；然后是导入相关。
        for widget in (self.empty, self.failures_card, self.recovery_card, self.retry_card,
                       self.profile_card, self.entity_card, self.fragments_card):
            column.addWidget(widget)
        column.addSpacing(theme_module.SPACE["xs"])
        for widget in (self.import_caption, self.extraction_card, self.rescan_card):
            column.addWidget(widget)
        column.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.apply({})

    # ================= 汇总（给顶栏的检查图标） =================
    def issue_cards(self) -> list[IssueCard]:
        """当前需要用户处理的项（可见、且是 ✕ / ⚠）。"""
        cards = (self.failures_card, self.recovery_card, self.scan_pages_card,
                 self.understanding_card,
                 self.annotation_card, self.profile_card, self.entity_card, self.fragments_card)
        return [card for card in cards
                if not card.isHidden() and card.tone() in ISSUE_TONES
                and not (card.parentWidget() is self.retry_card and self.retry_card.isHidden())]

    def issue_count(self) -> int:
        return len(self.issue_cards())

    def worst_tone(self) -> str:
        tones = {card.tone() for card in self.issue_cards()}
        return next((tone for tone in ISSUE_TONES if tone in tones), "")

    # ================= 数据 =================
    def apply(self, view: dict, *, busy: bool | None = None) -> None:
        keep_source = self._current_source()
        self._view = view or {}
        self._busy = bool(self._view.get("busy")) if busy is None else busy

        self._apply_failures(self._view.get("failures") or [])
        self._apply_recovery(self._view.get("recovery") or {})
        self._apply_scan_pages(self._view.get("scan_pages") or {})
        self._apply_understanding(self._view.get("understanding") or {})
        self._apply_annotation(self._view.get("annotation") or {})
        self.retry_card.setVisible(not self.scan_pages_card.isHidden()
                                   or not self.understanding_card.isHidden()
                                   or not self.annotation_card.isHidden())
        self._apply_profile(self._view.get("profile") or {})
        self._apply_entities(list(self._view.get("entities") or []), keep_source)
        self._apply_fragments([int(item) for item in self._view.get("fragments") or []])
        self._apply_rescan(self._view.get("rescan") or {})
        self._apply_extraction(self._view.get("extraction") or {})
        self.import_caption.setVisible(not self.extraction_card.isHidden()
                                       or not self.rescan_card.isHidden())

        count = self.issue_count()
        self.empty.setVisible(count == 0)
        self.overview.setText(i18n.tr('需要处理 · {v0}', v0=count) if count else "")
        self.overview.setVisible(bool(count) and getattr(self, "_show_overview", True))

    def set_show_overview(self, shown: bool) -> None:
        """「需要处理 · N」那一行（放进带数字的盾牌下拉卡片时不需要）。"""
        self._show_overview = bool(shown)
        self.overview.setVisible(self._show_overview and bool(self.issue_count()))

    def _apply_failures(self, failures: list[dict]) -> None:
        """翻译协议失败、当前译文还没补上的批次：列出段范围与原因，可定位过去。"""
        self._failures = [dict(item) for item in failures]
        self.failures_card.setVisible(bool(self._failures))
        self.failures_list.clear()
        if not self._failures:
            return
        segments = sum(int(item.get("count") or 0) for item in self._failures)
        self.failures_card.set_title(i18n.tr('翻译失败 · {v0} 批 {v1} 段',
                                             v0=len(self._failures), v1=segments))
        reasons = [adapter.explain_failure(_failure_detail(item)) for item in self._failures]
        reasons = [reason for reason in reasons if reason]
        summary = i18n.tr('这些段的模型回复无法使用，还没有译文；交付前要补上。')
        if reasons:
            common = max(set(reasons), key=reasons.count)
            summary += " " + i18n.tr('原因：{v0}', v0=i18n.tr_dynamic(common))
        self.failures_card.set_summary(summary)
        for item in self._failures:
            reason = " ".join(str(item.get("reason") or "").split())
            text = i18n.tr('第 {v0} 批 · 第 {v1}–{v2} 段', v0=item.get("batch"),
                           v1=int(item.get("start") or 0) + 1, v2=int(item.get("end") or 0) + 1)
            entry = QListWidgetItem(text)
            entry.setToolTip(reason)
            entry.setData(Qt.ItemDataRole.UserRole, int(item.get("start") or 0))
            self.failures_list.addItem(entry)
        rows = min(self.failures_list.count(), 5)
        self.failures_list.setFixedHeight(rows * self.failures_list.sizeHintForRow(0) + 8)
        self.failures_locate.setText(i18n.tr('定位到这一段') if len(self._failures) == 1
                                     else i18n.tr('定位'))

    def _locate_failure(self, item=None) -> None:
        item = item or self.failures_list.currentItem() or self.failures_list.item(0)
        if item is not None:
            self.locateRequested.emit(int(item.data(Qt.ItemDataRole.UserRole) or 0))

    def _apply_recovery(self, recovery: dict) -> None:
        current = recovery.get("current_batch")
        resumable = bool(recovery.get("can_resume"))
        self.recovery_card.setVisible(bool(current or resumable) and not self._busy)
        if self.recovery_card.isHidden():
            return
        total = int(recovery.get("total_batches") or 0)
        done = int(recovery.get("completed_batch_count") or 0)
        if current:
            self.recovery_card.set_title(i18n.tr('翻译中断 · 第 {v0} 批', v0=current.get("number")))
            text = i18n.tr('继续时只重做这一批剩下的 {v0} 段，已保存的批次不会重做。',
                           v0=current.get("regenerate_segments"))
        else:
            self.recovery_card.set_title(i18n.tr('翻译已暂停'))
            text = i18n.tr('继续时从上次保存的位置接着翻译。')
        facts = []
        if total:
            facts.append(i18n.tr('已完成 {v0} 批', v0=f"{done}/{total}"))
        if recovery.get("last_saved_at"):
            facts.append(i18n.tr('上次保存 {v0}',
                                 v0=str(recovery["last_saved_at"])[5:16].replace("T", " ")))
        self.recovery_card.set_summary(("　·　".join(facts) + "\n" if facts else "") + text)
        self.resume_button.setVisible(resumable)
        self.resume_button.setEnabled(not self._busy)

    def _apply_scan_pages(self, scan_pages: dict) -> None:
        pages = list(scan_pages.get("pages") or [])
        self.scan_pages_card.setVisible(bool(pages))
        if not pages:
            return
        self.scan_pages_card.set_title(i18n.tr('有 {v0} 页没读成', v0=len(pages)))
        self.scan_pages_card.set_summary(i18n.tr(
            '这些页的原文缺失，译稿不完整，不能交付。补读时只读失败的页，其余页沿用已识别的结果。'))
        self.scan_pages_list.clear()
        for row in pages:
            self.scan_pages_list.addItem(i18n.tr(
                '第 {v0} 页：{v1}', v0=row.get("page"),
                v1=i18n.tr_dynamic(str(row.get("reason") or row.get("code") or ""))))
        self.scan_pages_button.setEnabled(not self._busy)

    def _apply_understanding(self, understanding: dict) -> None:
        degraded = understanding.get("status") == "degraded"
        self.understanding_card.setVisible(degraded)
        if not degraded:
            return
        units = len(understanding.get("degraded_units") or [])
        total = understanding.get("total_units") or 0
        if units:
            title = i18n.tr('全文理解降级 · {v0}/{v1} 个单元', v0=units, v1=total)
        else:
            title = i18n.tr('全文理解降级')
        self.understanding_card.set_title(title)
        text = i18n.tr('这些单元用了首句代替摘要，翻译时的上下文会弱一些。')
        if understanding.get("synopsis_degraded"):
            text = i18n.tr('全文概要和部分单元摘要用了首句代替，翻译时的上下文会弱一些。') \
                if units else i18n.tr('全文概要用了首句代替，翻译时的上下文会弱一些。')
        reason = str(understanding.get("reason") or "")
        if reason:
            text += " " + i18n.tr('原因：{v0}', v0=i18n.tr_dynamic(reason))
        self.understanding_card.set_summary(text)
        self.understanding_button.setEnabled(not self._busy)

    def _apply_annotation(self, annotation: dict) -> None:
        partial = annotation.get("status") == "partial"
        self.annotation_card.setVisible(partial)
        if not partial:
            return
        if annotation.get("legacy_unlocated"):
            self.annotation_card.set_title(i18n.tr('自动标注未完成'))
            self.annotation_card.set_summary(i18n.tr(
                '旧标注记录仅保存失败数量，无法确定失败位置；重新运行会重建全文标注。'))
            self.annotation_button.setText(i18n.tr('重新运行全文标注'))
        else:
            self.annotation_card.set_title(i18n.tr('自动标注未完成 · {v0} 批',
                                                   v0=annotation.get("failed_batches")))
            self.annotation_card.set_summary(i18n.tr('成功的批次会保留，只补跑失败或待更新的批次。'))
            self.annotation_button.setText(i18n.tr('只补跑失败标注批次'))
        self.annotation_button.setEnabled(not self._busy)

    def _apply_profile(self, profile: dict) -> None:
        missing = bool(profile.get("missing"))
        self.profile_card.setVisible(missing)
        if not missing:
            return
        text = i18n.tr('翻译与审校不知道这是一份什么文档（领域、读者、语域），措辞会更泛。')
        reason = str(profile.get("reason") or "")
        if reason:
            text += " " + i18n.tr('原因：{v0}', v0=i18n.tr_dynamic(reason))
        self.profile_card.set_summary(text)

    def _apply_entities(self, entities: list[dict], keep_source: str) -> None:
        self.entity_box.blockSignals(True)
        self.entity_box.clear()
        for item in entities:
            self.entity_box.addItem(str(item.get("source") or ""), item.get("source"))
        wanted = self.entity_box.findData(keep_source) if keep_source else -1
        self.entity_box.setCurrentIndex(wanted if wanted >= 0 else (0 if entities else -1))
        self.entity_box.blockSignals(False)
        self.entity_card.setVisible(bool(entities))
        if not entities:
            return
        # 只有一个时不用下拉框：标题直接写它。
        self.entity_box.setVisible(len(entities) > 1)
        self.entity_card.set_summary(i18n.tr(
            '同一个名字在不同段落有不同译法（书名号与引号的差异不算）。选一个统一，或确认它们各自符合语境。'))
        self._entity_changed()

    def _apply_fragments(self, fragments: list[int]) -> None:
        self._fragments = fragments
        self.fragments_card.setVisible(bool(fragments))
        if not fragments:
            return
        numbers = "、".join(str(index + 1) for index in fragments[:12])
        self.fragments_card.set_title(i18n.tr('疑似原文碎片 · {v0} 段', v0=len(fragments)))
        self.fragments_card.set_summary(i18n.tr(
            '第 {v0}{v1} 段像页眉、页码或断句残片。排除后保留原文与译文，可在「交付」页恢复。',
            v0=numbers, v1=i18n.tr(' 等') if len(fragments) > 12 else ""))
        self.fragments_button.setEnabled(not self._busy)

    def _apply_extraction(self, report: dict) -> None:
        available = bool(report.get("available"))
        self.extraction_card.setVisible(available)
        if not available:
            return
        text = i18n.tr('已提取 {v0} 段', v0=format(int(report.get("paragraphs") or 0), ","))
        if report.get("pages"):
            text += i18n.tr('、{v0} 页', v0=format(int(report["pages"]), ","))
        text += "。"
        if report.get("ocr_used"):
            text += i18n.tr('正文由识别生成，可能含错字与假断行。')
        if report.get("ocr_engine"):
            text += " " + i18n.tr('识别引擎：{v0}', v0=report["ocr_engine"])
        warnings = []
        if report.get("pages_incomplete"):
            warnings.append(i18n.tr('该次识别未覆盖全部页面；失败页保持未完成状态，恢复后会重试这些页。'))
        warnings.extend(i18n.tr_dynamic(item) for item in report.get("warnings") or [])
        if warnings:
            text += " " + i18n.tr('{v0} 条提取警告，展开查看。', v0=len(warnings))
        self.extraction_summary.setText(text)
        self.extraction_card.set_tone("warning" if warnings else "ok")
        self.extraction_warning.setText("\n".join(f"⚠ {item}" for item in warnings))
        self.extraction_warning.setVisible(bool(warnings))
        unsupported = report.get("unsupported") or []
        if unsupported:
            rows = "".join(
                f"<p><b>{_escape(i18n.tr_dynamic(item['label']))}</b>："
                f"{_escape(i18n.tr_dynamic(item['detail']))}"
                + (f"<br/><small>{_escape(item['sample'])}</small>" if item.get("sample") else "")
                + "</p>" for item in unsupported)
            self.extraction_details.setText(
                f"<p>{_escape(i18n.tr('未纳入翻译'))}</p>{rows}")
        else:
            self.extraction_details.setText(_escape(i18n.tr('没有检测到表格、页眉页脚、脚注等未纳入的内容。')))

    def _apply_rescan(self, rescan: dict) -> None:
        available = bool(rescan.get("available"))
        self.rescan_card.setVisible(available)
        if not available:
            return
        # 只在第一次出现时带入当前设置；之后用户的选择不被后台刷新覆盖。
        if not self._rescan_seeded:
            self._rescan_seeded = True
            for box, key in ((self.rescan_backend, "backend"), (self.rescan_mode, "mode"),
                             (self.rescan_language, "language"), (self.rescan_quality, "quality")):
                index = box.findData(rescan.get(key) or "")
                if index >= 0:
                    box.setCurrentIndex(index)
        self._rescan_changed()

    def _rescan_changed(self, *_args) -> None:
        if self.rescan_card.isHidden():
            return
        status = adapter.rescan_engine_status(
            str(self.rescan_backend.currentData() or ""),
            str(self.rescan_language.currentData() or ""))
        text = i18n.tr_dynamic(status.get("message") or "") if not status.get("ok") \
            else i18n.tr_dynamic(status.get("quality_note") or "")
        self.rescan_status.setText(text)
        self.rescan_status.setVisible(bool(text))
        self.rescan_button.setEnabled(bool(status.get("ok")) and not self._busy)

    def _emit_rescan(self) -> None:
        self.rescanRequested.emit({
            "backend": str(self.rescan_backend.currentData() or ""),
            "mode": str(self.rescan_mode.currentData() or ""),
            "language": str(self.rescan_language.currentData() or ""),
            "quality": str(self.rescan_quality.currentData() or "")})

    # ================= 专名 =================
    def _current_source(self) -> str:
        return str(self.entity_box.currentData() or "")

    def _current_entity(self) -> dict:
        source = self._current_source()
        return next((item for item in self._view.get("entities") or []
                     if item.get("source") == source), {})

    def _entity_changed(self, *_args) -> None:
        item = self._current_entity()
        source = str(item.get("source") or "")
        count = len(self._view.get("entities") or [])
        self.entity_card.set_title(
            i18n.tr('译名不一致 · {v0}', v0=source) if count <= 1
            else i18n.tr('译名不一致 · {v0} 个名字', v0=count))
        self.entity_variants.setText(_variant_evidence(item))
        self.target_edit.blockSignals(True)
        self.target_edit.setText(str(item.get("preferred_target") or ""))
        self.target_edit.blockSignals(False)
        self.set_preview([])
        self._target_changed()

    def _evidence_link(self, href: str) -> None:
        if href.startswith("seg:"):
            try:
                self.locateRequested.emit(int(href[4:]))
            except ValueError:
                pass

    def _target_changed(self, *_args) -> None:
        self.set_preview([])
        if getattr(self, "_defer_preview", False) or (self.window().isVisible() and not self.isVisible()):
            # (only when shown in a window but on a hidden page)
            # The preview scans the whole document (~0.2 s on a book); the panel
            # is rebuilt on every state change, mostly while nobody looks at it.
            self._preview_pending = True
            return
        self._preview_pending = False
        target = self.target_edit.text().strip()
        if target and self._current_source():
            self.previewRequested.emit(self._current_source(), target)

    def set_preview_deferred(self, deferred: bool) -> None:
        """Keep the first-show entity scan out of the drawer's opening frames."""
        self._defer_preview = deferred
        if not deferred and getattr(self, "_preview_pending", False) and self.isVisible():
            self._target_changed()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if getattr(self, "_preview_pending", False):
            self._target_changed()

    def set_preview(self, preview: list[dict]) -> None:
        self._preview = list(preview or [])
        tokens = theme_module.tokens(style_module.current_theme())
        rows = []
        for change in self._preview:
            rows.append(
                f"<p><b>{_escape(i18n.tr('第 {v0} 段', v0=int(change.get('index', 0)) + 1))}</b>"
                f"<br><span style='color:{tokens['danger']}'>− {_escape(change.get('before'))}</span>"
                f"<br><span style='color:{tokens['success']}'>+ {_escape(change.get('after'))}</span></p>")
        self.preview_view.setHtml("".join(rows))
        has_target = bool(self.target_edit.text().strip())
        self.preview_hint.setText(
            i18n.tr('将修改 {v0} 段；确认后记录人工译名，并使受影响段落的审校和标注失效。',
                    v0=len(self._preview)) if has_target and self._preview else "")
        self.preview_hint.setVisible(bool(self.preview_hint.text()))
        self.preview_view.setVisible(bool(self._preview))
        if self._preview:
            # 预览框按内容高度（最多 180），不留一大块空白。
            document = self.preview_view.document()
            document.setTextWidth(max(120, self.preview_view.viewport().width()))
            self.preview_view.setFixedHeight(
                max(48, min(180, int(document.size().height())
                                  + 2 * self.preview_view.frameWidth() + 6)))
        self.repair_button.setEnabled(bool(self._preview) and not self._busy)
        self.accept_button.setEnabled(bool(self._current_source()) and not self._busy)

    def _emit_repair(self) -> None:
        if self._preview:
            self.repairRequested.emit(self._current_source(),
                                      self.target_edit.text().strip(), list(self._preview))

    def _emit_accept(self) -> None:
        if self._current_source():
            self.acceptRequested.emit(self._current_source())


def _failure_detail(item: dict) -> str:
    """失败批次的原因里，冒号后面才是模型调用的原话（"批次翻译失败（1 段）：Connection error"）。"""
    reason = str(item.get("reason") or "")
    for mark in ("：", ": "):
        if mark in reason:
            return reason.split(mark, 1)[1]
    return reason


def _variant_evidence(item: dict) -> str:
    """每种译法出现在哪几段：「埃拉 — 第 347、3713 段」，段号可点（定位过去）。"""
    by_variant: dict[str, list[int]] = {}
    for row in item.get("occurrences") or []:
        for value in row.get("variants") or []:
            key = str(value).strip()
            by_variant.setdefault(key, [])
            if int(row.get("index", -1)) not in by_variant[key]:
                by_variant[key].append(int(row.get("index", -1)))
    if not by_variant:
        return _escape(i18n.tr('当前译法：{v0}', v0=" / ".join(
            str(v) for v in item.get("observed_targets") or [])))
    lines = []
    for value, indexes in sorted(by_variant.items(), key=lambda pair: -len(pair[1])):
        links = "、".join(f"<a href='seg:{index}'>{index + 1}</a>" for index in indexes[:8]
                         if index >= 0)
        more = i18n.tr(' 等 {v0} 处', v0=len(indexes)) if len(indexes) > 8 else ""
        where = _escape(i18n.tr('第 {v0} 段', v0="\x00")).replace("\x00", links)
        lines.append(f"<b>{_escape(value)}</b>　{where}{_escape(more)}")
    return "<br>".join(lines)
