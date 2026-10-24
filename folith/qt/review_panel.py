"""审校页：队列 + 详情 + 人工处置。

旧界面里审校发现项可以由人逐条处理（应用建议、确认已解决、保留当前译文、
回看原页后确认原文）；Qt 工作台原来只在检查器里只读地列出它们，于是一条
误报就能永远挡住交付。这个面板把处置放回界面。

"确认问题存在"与"确认已解决"是两件事：前者只记录人的判断，发现项保持待处理、
仍挡住交付；后者才结束它。两个按钮不能合并成一个含混的动作。

只做展示与发出信号：队列由 :func:`adapter.review_queue` 给出（复用
``folith.workbench_view``，不另写一套规则），真正写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import icons
from . import style as style_module
from .name_conflict import NameConflictWidget
from .style import Card

#: 筛选分段（与旧界面一致）：值 → 标签。「需决定」是默认页签：待处理、需重审和
#: 名字译法不一；其余建议与「仅供参考」留在各自的页签里。
FILTERS = (("actionable", "需决定"), ("all", "全部"), ("pending", "待处理"), ("rereview", "需重审"),
           ("skipped", "已跳过"), ("suggested", "有建议"),
           ("reference", "仅供参考"))

_SEVERITY_TONE = {"blocking": "danger", "actionable": "warn",
                  "informational": "info"}

#: 队列项里「需要重新审校」的几种合成项。
_REVIEW_TASK_KINDS = {"failed", "stale", "missing", "skipped"}


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._items: list[dict] = []
        self._visible: list[dict] = []
        self._selected_id = ""
        self._filter = "actionable"
        self._user_filter = False       # 用户点过页签后，不再替他换回默认页签
        self._busy = False

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
        self.filter_control = style_module.SegmentedControl(
            [(value, i18n.tr(label)) for value, label in FILTERS])
        self.filter_control.changed.connect(self._set_filter)

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
        self.list.currentItemChanged.connect(self._current_changed)
        self.empty_label = QLabel("")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setProperty("role", "muted")

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.filter_control)
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

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(i18n.tr('处理说明（可选）：这次决定的依据'))

        self.source_confirm_box = style_module.CheckBox(
            i18n.tr('我已回看 PDF 原页，确认这段是有效正文'))
        self.source_confirm_box.toggled.connect(self._sync_buttons)
        self.source_note_edit = QLineEdit()
        self.source_note_edit.setPlaceholderText(
            i18n.tr('原文核对依据，例如：原页正文清晰可读，且与前后句连续。'))
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

        action_row = QHBoxLayout()
        for widget in (self.apply_button, self.open_button, self.confirm_button,
                       self.confirm_source_button, self.preserve_button,
                       self.review_button, self.edit_button,
                       self.retranslate_button, self.style_rule_button):
            action_row.addWidget(widget)
        action_row.addStretch(1)

        # 名字译法不一：同一个部件（与「检查与修复」抽屉共用），代替逐段的动作行。
        self.conflict = NameConflictWidget()
        self.conflict.previewRequested.connect(self.entityPreviewRequested.emit)
        self.conflict.repairRequested.connect(self.entityRepairRequested.emit)
        self.conflict.acceptRequested.connect(self.entityAcceptRequested.emit)
        self.conflict.locateRequested.connect(self.editRequested.emit)
        self.conflict.setVisible(False)

        title_row = QHBoxLayout()
        title_row.addWidget(self.item_title, 1)
        title_row.addWidget(self.item_chip, 0, Qt.AlignmentFlag.AlignTop)

        detail_card = Card()
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(10)
        detail_layout.addLayout(title_row)
        detail_layout.addWidget(self.item_body, 1)
        detail_layout.addWidget(self.conflict)
        detail_layout.addWidget(self.source_confirm_box)
        detail_layout.addWidget(self.source_note_edit)
        detail_layout.addWidget(self.note_edit)
        detail_layout.addLayout(action_row)
        detail_layout.addWidget(self.hint)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 0)
        body.addWidget(detail_card, 1)

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
        layout.addWidget(self.content, 1)
        layout.addWidget(self.empty_state, 1)

        self._render_detail()

    # ================= 数据 =================
    def view(self) -> dict:
        """最近一次 `apply` 收到的视图（阶段步骤条与它共用同一份审校投影）。"""
        return self._view

    def apply(self, view: dict, *, busy: bool = False) -> None:
        """用新的视图模型重画队列；保持当前选中项（按稳定标识）。"""
        self._view = view or {}
        self._busy = busy
        self._items = list(self._view.get("queue_items") or [])
        readiness = self._view.get("readiness") or {}
        self.headline.setText(i18n.tr_dynamic(str(readiness.get("label") or "")))
        self.detail.setText(i18n.tr_dynamic(str(readiness.get("detail") or "")))
        counts = dict(self._view.get("filter_counts") or {})
        counts["actionable"] = sum(adapter.is_actionable_review_item(item)
                                   for item in self._items)
        if not self._user_filter:
            # 默认页签没有项时退回「全部」，不给一页空白。
            self._filter = "actionable" if counts["actionable"] else "all"
        self.filter_control.set_options([
            (value, f"{i18n.tr(label)} {counts.get(value, 0)}")
            for value, label in FILTERS])
        self.filter_control.set_value(self._filter)
        self._rebuild_list()
        self._sync_empty(readiness)

    def _sync_empty(self, readiness: dict) -> None:
        """队列整个是空的：整页换成一个空状态，最多一个动作。"""
        empty = not self._items
        self.content.setVisible(not empty)
        if not empty:
            self.empty_state.setVisible(False)
            return
        status = str(readiness.get("status") or "")
        title = i18n.tr_dynamic(str(readiness.get("label") or "")) or i18n.tr('尚未审校')
        hint = i18n.tr_dynamic(str(readiness.get("detail") or ""))
        # 审校已完成：没有要做的事，就不给按钮。
        self.empty_state.set_text(title, hint)
        coverage_complete = readiness.get("coverage_complete")
        if coverage_complete is None:
            coverage_complete = status == "current" and not readiness.get("skipped_count")
        done = status == "not_required" or (
            bool(readiness.get("ready")) and bool(coverage_complete))
        self.empty_state.set_action("" if done else i18n.tr('审校全部段落'))
        self.empty_state.setEnabled(not self._busy)

    def _set_filter(self, value: str) -> None:
        self._filter = value or "all"
        self._user_filter = True
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        wanted = self._selected_id
        self._visible = [
            item for item in self._items
            if self._filter == "all" or (
                adapter.is_actionable_review_item(item) if self._filter == "actionable"
                else item.get("filter_group") == self._filter)]
        self._visible.sort(key=lambda item: (item.get("priority", 9),
                                             item.get("segment_index") or 0))
        self.list.blockSignals(True)
        self.list.clear()
        for item in self._visible:
            title = self._item_title(item)
            label = i18n.tr_dynamic(str(item.get("severity_label") or ""))
            summary = str(item.get("summary") or "")[:60]
            # 标题已经写了"审校未完成"，第二行就不再重复一遍同一个词。
            second = summary if (not label or label in title) else f"{label} · {summary}"
            entry = QListWidgetItem(f"{title}\n{second}")
            entry.setData(Qt.ItemDataRole.UserRole, item.get("id"))
            self.list.addItem(entry)
        row = next((i for i, item in enumerate(self._visible)
                    if item.get("id") == wanted), 0 if self._visible else -1)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self._selected_id = (str(self._visible[row].get("id"))
                             if 0 <= row < len(self._visible) else "")
        self.empty_label.setVisible(not self._visible)
        self.empty_label.setText(
            i18n.tr('审校队列是空的：没有需要人工处理的项目。')
            if not self._items else i18n.tr('这个筛选下没有项目。'))
        self._render_detail()

    def _current_changed(self, current, _previous) -> None:
        self._selected_id = str(current.data(Qt.ItemDataRole.UserRole)) if current else ""
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
    def _item_title(item: dict) -> str:
        """名字译法不一按名字列一行，不写第一处出现的段号。"""
        source = str(item.get("entity_source") or "")
        if not source:
            return str(item.get("title") or "")
        count = len(item.get("segment_indexes") or [])
        title = i18n.tr('译名不一致 · {v0}', v0=source)
        return title + (i18n.tr('（{v0} 处）', v0=count) if count > 1 else "")

    def _entity_for(self, item: dict) -> dict:
        """名字译法不一的发现项对应的实体（抽屉里的同一份数据）；找不到就是空。"""
        source = str(item.get("entity_source") or "")
        return next((entity for entity in self._view.get("entities") or []
                     if entity.get("source") == source), {}) if source else {}

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
            return
        self.item_title.setText(self._item_title(item))
        self.item_chip.setText(i18n.tr_dynamic(str(item.get("severity_label") or "")))
        self.item_chip.set_tone(_SEVERITY_TONE.get(str(item.get("severity")), "info"))
        parts = []
        for label, key in (("问题", "summary"), ("原因", "reason"),
                           ("说明", "explanation"), ("建议", "recommendation")):
            value = str(item.get(key) or "").strip()
            if value and not any(value in part for part in parts):
                parts.append(f"<p><b>{_escape(i18n.tr(label))}：</b>{_escape(value)}</p>")
        from folith.textual import text_direction

        for label, key in (("原文", "source"), ("当前译文", "target"),
                           ("建议译文", "suggested_target")):
            value = str(item.get(key) or "").strip()
            if value:
                # 原文与译文各按自己的书写方向排：中文原文从左往右、阿拉伯语译文从右往左，
                # 同一个面板里混排（试用反馈）。
                rtl = text_direction(value, None) == "rtl"
                block = ' dir="rtl"' if rtl else ""
                parts.append(f"<p style=\"margin-bottom:0\"><b>{_escape(i18n.tr(label))}：</b></p>"
                             f"<p{block} style=\"margin-top:2px\">{_escape(value)}</p>")
        self.item_body.setHtml("".join(parts))
        entity = self._entity_for(item)
        if entity:
            self.conflict.set_entity(entity, busy=self._busy)
        self.conflict.setVisible(bool(entity))
        self._sync_buttons()

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

    # ================= 信号 =================
    def _emit_resolve(self, action: str) -> None:
        item = self.selected_item()
        if item:
            self.resolveRequested.emit(
                [str(item.get("finding_id") or "")], action, self.note_edit.text().strip())

    def _emit_apply(self) -> None:
        item = self.selected_item()
        if item:
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
            for row, item in enumerate(self._visible):
                if item.get("id") == item_id:
                    self.list.setCurrentRow(row)
                    return True
            # 目标不在当前页签里（例如从别处跳到一条仅供参考的项）：切到「全部」再找。
            if attempt or not any(item.get("id") == item_id for item in self._items):
                break
            self.filter_control.set_value("all")
            self._set_filter("all")
        return False
