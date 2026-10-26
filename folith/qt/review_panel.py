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
    QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

from . import adapter
from . import components
from . import i18n
from . import icons
from . import pdf_panel as pdf_panel_module
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


def _colon() -> str:
    """字段名后的冒号：中文用全角，其他语言用半角 + 空格。"""
    return "：" if i18n.language().startswith(("zh", "ja", "ko")) else ": "


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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._items: list[dict] = []
        self._visible: list[dict] = []
        self._selected_id = ""
        self._filter = "actionable"
        self._user_filter = False       # 用户点过页签后，不再替他换回默认页签
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
        self.filter_control = style_module.SegmentedControl(
            [(value, i18n.tr(label)) for value, label in FILTERS])
        self.filter_control.changed.connect(self._set_filter)
        self.filter_control.setAccessibleName(i18n.tr('审校筛选'))

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
        # 筛选条横跨整页：七个页签放进 260px 的队列表头会把俄语/西语挤成四行。
        content_layout.addWidget(self.filter_control)
        content_layout.addLayout(body, 1)
        self.empty_state = style_module.EmptyState(icon="shield-check", page=True,
                                                   action=i18n.tr('审校全部段落'))
        self.empty_state.actionTriggered.connect(self.reviewAllRequested.emit)
        self.empty_state.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.concept_button, 0, Qt.AlignmentFlag.AlignRight)
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
        self.shortcut_hint.setVisible(bool(self._items))

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
            decision = i18n.tr_dynamic(str(item.get("decision_label") or ""))
            summary = str(item.get("summary") or "")
            if item.get("kind") in _REVIEW_TASK_KINDS:
                # 合成项（待审/需重审/失败/跳过）的摘要也是界面文案；
                # 发现项的摘要是模型产出，必须原样显示。
                summary = i18n.tr_dynamic(summary)
            # 标题已经写了"审校未完成"，第二行就不再重复一遍同一个词。
            tags = [value for value in (decision, label)
                    if value and value not in title]
            if summary:
                tags.append(summary)
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
        sep = _colon()
        task_item = str(item.get("kind") or "") in _REVIEW_TASK_KINDS
        parts = []
        decision = i18n.tr_dynamic(str(item.get("decision_label") or ""))
        if decision:
            # 已经记下的人工判断：确认问题存在之后问题仍在队列里，必须说清它
            # 不是"待处理但没人看过"。
            parts.append(f"<p><b>{_escape(i18n.tr('已记录的决定'))}{sep}</b>"
                         f"{_escape(decision)}</p>")
        for label, key in (("问题", "summary"), ("原因", "reason"),
                           ("说明", "explanation"), ("建议", "recommendation")):
            value = str(item.get(key) or "").strip()
            if value and task_item:
                value = i18n.tr_dynamic(value)
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
        self.item_body.setAccessibleDescription(
            " · ".join(value for value in (self._item_title(item), chip_text,
                                          str(item.get("summary") or ""))
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

    def _goto_next_item(self) -> None:
        if self._typing():
            return
        row = self.list.currentRow()
        if 0 <= row + 1 < self.list.count():
            self.list.setCurrentRow(row + 1)

    def _next_item_id(self) -> str:
        row = self.list.currentRow()
        if 0 <= row + 1 < len(self._visible):
            return str(self._visible[row + 1].get("id") or "")
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
