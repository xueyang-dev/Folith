"""案例终审页（学术专业模式）。

逐例确认案例是否可纳入学术分析；批准不会把合成对照变成历史初译。当前译文或输入
变了，旧批准就作废（页面会明说）。只做展示与发信号；写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from folith import workspace_cases as cases_view

from . import i18n
from . import style as style_module
from .style import Card


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


class CasesPanel(QWidget):
    approveRequested = Signal(str)                 # case_id
    excludeRequested = Signal(str)                 # case_id（原因由窗口层询问）
    editRequested = Signal(int)                    # 段索引
    replaceRequested = Signal(str)
    baselineSaveRequested = Signal(str, str)       # case_id, text
    baselineRejectRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all: list[dict] = []
        self._shown: list[dict] = []
        self._selected = ""
        self._busy = False
        self._state_note = ""

        self.title = QLabel(i18n.tr('案例终审'))
        self.title.setObjectName("sectionTitle")
        self.lead = QLabel(i18n.tr('逐例确认案例是否可纳入学术分析；批准不会把合成对照变成历史初译。'))
        self.lead.setObjectName("hint")
        self.lead.setWordWrap(True)
        self.truth = style_module.NoticeStrip("", tone="info")

        self.origin_box = style_module.ComboBox()
        for value in cases_view.ORIGIN_FILTERS:
            self.origin_box.addItem(i18n.tr(value), value)
        self.status_box = style_module.ComboBox()
        for value in cases_view.STATUS_FILTERS:
            self.status_box.addItem(i18n.tr(value), value)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.tr('搜索段落、原文或译文…'))
        self.search_edit.setClearButtonEnabled(True)
        for signal in (self.origin_box.currentIndexChanged, self.status_box.currentIndexChanged,
                       self.search_edit.textChanged):
            signal.connect(self._refilter)
        filters = QHBoxLayout()
        filters.addWidget(self.origin_box)
        filters.addWidget(self.status_box)
        filters.addWidget(self.search_edit, 1)

        self.queue = QListWidget()
        self.queue.setObjectName("caseQueue")
        self.queue.setMinimumWidth(260)
        self.queue.currentRowChanged.connect(self._row_changed)
        self.queue_caption = QLabel("")
        self.queue_caption.setObjectName("hint")
        self.queue_caption.setWordWrap(True)
        self.empty = QLabel(i18n.tr('尚未生成案例选择产物。'))
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left = QVBoxLayout()
        left.addWidget(self.queue, 1)
        left.addWidget(self.queue_caption)

        self.detail = QTextBrowser()
        self.detail.setObjectName("caseDetail")
        self.detail.setMinimumHeight(240)
        self.stale_notice = style_module.NoticeStrip("", tone="warn")
        self.note_label = QLabel("")
        self.note_label.setObjectName("hint")
        self.note_label.setWordWrap(True)

        def button(text: str, name: str = "") -> QPushButton:
            widget = QPushButton(text)
            if name:
                widget.setObjectName(name)
            return widget

        self.approve_button = button(i18n.tr('批准纳入'), "primaryAction")
        self.exclude_button = button(i18n.tr('排除案例…'))
        self.edit_button = button(i18n.tr('修改当前译文'))
        self.replace_button = button(i18n.tr('从合格池替换'))
        self.approve_button.clicked.connect(lambda: self._emit("approve"))
        self.exclude_button.clicked.connect(lambda: self._emit("exclude"))
        self.edit_button.clicked.connect(lambda: self._emit("edit"))
        self.replace_button.clicked.connect(lambda: self._emit("replace"))
        actions = QHBoxLayout()
        for widget in (self.approve_button, self.exclude_button, self.edit_button,
                       self.replace_button):
            actions.addWidget(widget)
        actions.addStretch(1)

        self.baseline_title = QLabel(i18n.tr('模拟初译（分析对照，不是历史初译）'))
        self.baseline_title.setObjectName("sectionTitle")
        self.baseline_edit = QPlainTextEdit()
        self.baseline_edit.setMaximumHeight(120)
        self.baseline_save = button(i18n.tr('保存修改模拟初译'))
        self.baseline_reject = button(i18n.tr('拒绝模拟初译'))
        self.baseline_save.clicked.connect(lambda: self._emit("baseline_save"))
        self.baseline_reject.clicked.connect(lambda: self._emit("baseline_reject"))
        baseline_actions = QHBoxLayout()
        baseline_actions.addWidget(self.baseline_save)
        baseline_actions.addWidget(self.baseline_reject)
        baseline_actions.addStretch(1)
        self.baseline_box = QWidget()
        baseline_layout = QVBoxLayout(self.baseline_box)
        baseline_layout.setContentsMargins(0, 0, 0, 0)
        for widget in (self.baseline_title, self.baseline_edit):
            baseline_layout.addWidget(widget)
        baseline_layout.addLayout(baseline_actions)

        detail_card = Card()
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(8)
        for widget in (self.detail, self.stale_notice, self.note_label):
            detail_layout.addWidget(widget)
        detail_layout.addLayout(actions)
        detail_layout.addWidget(self.baseline_box)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 0)
        body.addWidget(detail_card, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        for widget in (self.title, self.lead, self.truth):
            layout.addWidget(widget)
        layout.addLayout(filters)
        layout.addWidget(self.empty)
        layout.addLayout(body, 1)
        self.apply([], {})

    # ================= 数据 =================
    def apply(self, views: list[dict], truth: dict, *, busy: bool = False) -> None:
        self._all = list(views or [])
        self._busy = busy
        version = (truth or {}).get("version")
        self.truth.set_text(i18n.tr('案例引用以当前译文为准（译文真值 v{v0}）', v0=version)
                            if version is not None else
                            i18n.tr('案例引用以当前译文为准'))
        self.empty.setVisible(not self._all)
        for widget in (self.origin_box, self.status_box, self.search_edit):
            widget.setVisible(bool(self._all))
        self._refilter()

    def _refilter(self, *_args) -> None:
        self._shown = cases_view.filter_views(
            self._all, str(self.origin_box.currentData() or "全部"),
            str(self.status_box.currentData() or "全部"), self.search_edit.text())
        self.queue.blockSignals(True)
        self.queue.clear()
        for item in self._shown:
            self.queue.addItem(QListWidgetItem(
                f"{i18n.tr_dynamic(item['identity_label'])} · {i18n.tr_dynamic(item['origin_label'])}"
                f" · {i18n.tr_dynamic(item['status_label'])}"))
        ids = [str(item.get("case_id")) for item in self._shown]
        row = ids.index(self._selected) if self._selected in ids else (0 if ids else -1)
        self.queue.setCurrentRow(row)
        self.queue.blockSignals(False)
        self._selected = ids[row] if row >= 0 else ""
        counts = cases_view.counts(self._all)
        self.queue_caption.setText(
            i18n.tr('显示 {v0} / 总计 {v1} · 已批准 {v2} · 已排除 {v3} · 待人工确认 {v4}',
                    v0=len(self._shown), v1=counts["total"], v2=counts["approved"],
                    v3=counts["rejected"], v4=counts["pending"]) if self._all else "")
        self._render()

    def _row_changed(self, row: int) -> None:
        self._selected = str(self._shown[row].get("case_id")) if 0 <= row < len(self._shown) else ""
        self._render()

    def current(self) -> dict:
        return next((i for i in self._shown if str(i.get("case_id")) == self._selected), {})

    # ================= 渲染 =================
    def _render(self) -> None:
        item = self.current()
        has = bool(item)
        for widget in (self.detail, self.note_label):
            widget.setVisible(has)
        self.baseline_box.setVisible(False)
        for widget in (self.approve_button, self.exclude_button, self.edit_button,
                       self.replace_button):
            widget.setVisible(has)
        self.stale_notice.set_text("")
        if not has:
            self.detail.setHtml(i18n.tr('当前筛选下没有案例。') if self._all else "")
            self.detail.setVisible(bool(self._all))
            return
        stale = bool(item.get("stale"))
        status = str(item.get("review_status") or "")
        display_initial = bool(item.get("initial_text"))
        parts = [f"<h3>{_escape(i18n.tr_dynamic(item['identity_label']))} "
                 f"<small>· {_escape(i18n.tr_dynamic(item['origin_label']))} · "
                 f"{_escape(i18n.tr_dynamic(item['status_label']))} · "
                 f"{_escape(i18n.tr_dynamic(item['validity_label']))}</small></h3>"]
        from folith import case_provenance as _prov
        contract = _prov.display_contract(item)
        parts.append(f"<p><i>{_escape(contract.get('origin_description') or '')}</i></p>")
        parts.append(f"<p><b>{_escape(i18n.tr('原文'))}</b><br>{_escape(item.get('source_text') or '—')}</p>")
        if display_initial:
            parts.append(f"<p><b>{_escape(contract.get('initial_label') or i18n.tr('初始文本'))}</b>"
                         f"<br>{_escape(item.get('initial_text'))}</p>")
        elif item.get("case_origin") != _prov.SYNTHETIC_BASELINE:
            parts.append(f"<p><i>{_escape(i18n.tr('无真实初译（不伪造对照）'))}</i></p>")
        parts.append(f"<p><b>{_escape(i18n.tr('当前译文'))}</b><br>{_escape(item.get('current_text') or '—')}</p>")
        context = " ".join(str(item.get(k) or "") for k in ("context_before", "context_after")).strip()
        if context:
            parts.append(f"<p><b>{_escape(i18n.tr('必要上下文'))}</b><br>{_escape(context)}</p>")
        parts.append(f"<h4>{_escape(i18n.tr('分析与证据'))}</h4><table>" + "".join(
            f"<tr><td>{_escape(i18n.tr(label))}&nbsp;&nbsp;</td><td><b>{_escape(i18n.tr_dynamic(value))}</b></td></tr>"
            for label, value in cases_view.evidence_rows(item)) + "</table>")
        index = item.get("segment_index")
        parts.append("<p>" + _escape(i18n.tr(
            '关联位置：第 {v0} 段 · 目标报告位置 {v1}{v2} · 相关审校发现 {v3} 条',
            v0=int(index) + 1 if isinstance(index, int) else "—",
            v1=item.get("target_subsection") or item.get("section_id") or "—",
            v2=f" · {item['section_title']}" if item.get("section_title") else "",
            v3=len(item.get("case_findings") or []))) + "</p>")
        parts.append(f"<h4>{_escape(i18n.tr('关联证据'))}</h4>")
        commentary = item.get("analytical_commentary") or []
        parts.append(("<ul>" + "".join(
            f"<li><b>{_escape(i18n.tr_dynamic(e.get('label') or '说明'))}</b>：{_escape(e.get('value') or '—')}</li>"
            for e in commentary) + "</ul>") if commentary
            else f"<p><i>{_escape(i18n.tr('尚未保存可展示的案例分析评论。'))}</i></p>")
        terms = item.get("related_terms") or []
        if terms:
            parts.append(f"<p><b>{_escape(i18n.tr('相关术语'))}</b></p><ul>" + "".join(
                f"<li>{_escape(t.get('source') or t.get('term') or '—')} → "
                f"{_escape(t.get('target') or t.get('preferred') or t.get('proposed_target') or '—')}</li>"
                for t in terms) + "</ul>")
        findings = item.get("case_findings") or []
        if findings:
            parts.append(f"<p><b>{_escape(i18n.tr('相关审校发现'))}</b></p><ul>" + "".join(
                f"<li>{_escape(i18n.tr_dynamic(cases_view.SEVERITY_LABELS.get(str(f.get('severity') or ''), '审校发现')))}"
                f" · {_escape(f.get('reason') or '审校发现')}</li>" for f in findings) + "</ul>")
        literature = item.get("literature_evidence") or []
        if literature:
            parts.append(f"<p><b>{_escape(i18n.tr('文献证据'))}</b></p><ul>" + "".join(
                f"<li>{_escape(s.get('title') or s.get('source_id') or '—')}</li>"
                for s in literature) + "</ul>")
        human = item.get("human_evidence") or []
        if human:
            parts.append(f"<p><b>{_escape(i18n.tr('作者补充证据'))}</b></p><ul>" + "".join(
                f"<li>{_escape(e.get('question_type') or '说明')}: {_escape(e.get('answer') or '—')}</li>"
                for e in human) + "</ul>")
        self.detail.setHtml("".join(parts))

        notes = []
        if item.get("review_note"):
            notes.append(i18n.tr('人工确认说明：{v0}', v0=item["review_note"]))
        if item.get("reviewed_at"):
            notes.append(i18n.tr('人工确认记录：{v0} · {v1}', v0=item["reviewed_at"],
                                 v1=item.get("review_actor") or "user"))
        self.note_label.setText("　".join(notes))
        self.note_label.setVisible(bool(notes))
        if stale:
            self.stale_notice.set_text(i18n.tr('当前译文或输入已变化，此案例需要重新检查；旧批准不能直接交付。'))
        enabled = not self._busy
        self.approve_button.setText(i18n.tr('保持批准') if status == "approved" else i18n.tr('批准纳入'))
        self.approve_button.setEnabled(enabled and not (status == "approved" and not stale))
        self.exclude_button.setEnabled(enabled)
        self.edit_button.setEnabled(enabled and isinstance(index, int))
        self.replace_button.setEnabled(enabled and status == "rejected")
        synthetic = item.get("case_origin") == _prov.SYNTHETIC_BASELINE
        self.baseline_box.setVisible(synthetic)
        if synthetic:
            keep = self.baseline_edit.property("case_id")
            if keep != self._selected:
                self.baseline_edit.setProperty("case_id", self._selected)
                self.baseline_edit.setPlainText(str(item.get("initial_text") or ""))

    # ================= 信号 =================
    def _emit(self, action: str) -> None:
        item = self.current()
        if not item:
            return
        case_id = str(item.get("case_id"))
        if action == "approve":
            self.approveRequested.emit(case_id)
        elif action == "exclude":
            self.excludeRequested.emit(case_id)
        elif action == "edit" and isinstance(item.get("segment_index"), int):
            self.editRequested.emit(int(item["segment_index"]))
        elif action == "replace":
            self.replaceRequested.emit(case_id)
        elif action == "baseline_save":
            self.baselineSaveRequested.emit(case_id, self.baseline_edit.toPlainText())
        elif action == "baseline_reject":
            self.baselineRejectRequested.emit(case_id, self.baseline_edit.toPlainText())
