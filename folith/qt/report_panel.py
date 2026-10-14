"""实践报告页（学术专业模式）。

一个整体状态、按「该做什么」分组的问题、清理过的当前稿件、运行详情。报告本身由管线
生成；这里让作者看清卡在哪、回答待补充的问题、按范围触发定点重建，并管理报告结构模板。
只做展示与发信号；写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import i18n
from . import style as style_module
from .style import Card

_SEVERITY_MARK = {"blocker": "■", "warning": "▲", "human_review": "●"}
_SEVERITY_LABEL = {"blocker": "阻塞", "warning": "警告", "human_review": "需人工"}
_TONE = {"danger": "danger", "warning": "warn", "success": "ok", "neutral": "info"}


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "<br>"))


class ReportPanel(QWidget):
    repairRequested = Signal(str, list)            # category, sections
    answerSubmitted = Signal(str, str)             # question_id, answer
    openReviewRequested = Signal()
    openCasesRequested = Signal()
    rebuildRequested = Signal()
    templateAddRequested = Signal()
    templateRemoveRequested = Signal()
    exportRequested = Signal(str)                  # "docx" | "md"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._groups: list[dict] = []
        self._busy = False
        self._connection_ok = True
        self._answer_boxes: dict[str, QPlainTextEdit] = {}

        self.title = QLabel(i18n.tr('报告'))
        self.title.setObjectName("sectionTitle")
        self.kicker = QLabel(i18n.tr('研究与报告 · 专用能力'))
        self.kicker.setObjectName("hint")
        self.status = style_module.NoticeStrip("", tone="info")
        self.chips = QLabel("")
        self.chips.setWordWrap(True)
        self.gate_note = QLabel("")
        self.gate_note.setObjectName("hint")
        self.gate_note.setWordWrap(True)
        self.impact = style_module.NoticeStrip("", tone="warn")
        self.rebuild_button = QPushButton(i18n.tr('按影响范围继续重建'))
        self.rebuild_button.clicked.connect(self.rebuildRequested.emit)

        # ---- 问题与修复 ------------------------------------------------------
        self.issue_list = QListWidget()
        self.issue_list.setObjectName("reportIssues")
        self.issue_list.setMinimumWidth(240)
        self.issue_list.currentRowChanged.connect(self._issue_selected)
        self.issue_detail = QTextBrowser()
        self.issue_detail.setObjectName("reportIssueDetail")
        self.issue_detail.setMinimumHeight(150)
        self.questions_box = QWidget()
        self.questions_layout = QVBoxLayout(self.questions_box)
        self.questions_layout.setContentsMargins(0, 0, 0, 0)
        self.repair_button = QPushButton("")
        self.repair_button.setObjectName("primaryAction")
        self.repair_button.clicked.connect(self._emit_repair)
        self.open_review_button = QPushButton(i18n.tr('打开审校工作区'))
        self.open_review_button.clicked.connect(self.openReviewRequested.emit)
        self.open_cases_button = QPushButton(i18n.tr('打开案例终审'))
        self.open_cases_button.clicked.connect(self.openCasesRequested.emit)
        self.repair_hint = QLabel("")
        self.repair_hint.setObjectName("hint")
        self.repair_hint.setWordWrap(True)
        self.no_issues = QLabel(i18n.tr('当前没有需要处理的报告问题。'))
        self.no_issues.setProperty("role", "muted")
        buttons = QHBoxLayout()
        for widget in (self.repair_button, self.open_review_button, self.open_cases_button):
            buttons.addWidget(widget)
        buttons.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(self.issue_detail, 1)
        right.addWidget(self.questions_box)
        right.addLayout(buttons)
        right.addWidget(self.repair_hint)
        body = QHBoxLayout()
        body.addWidget(self.issue_list, 0)
        body.addLayout(right, 1)
        issues_page = QWidget()
        issues_layout = QVBoxLayout(issues_page)
        issues_layout.setContentsMargins(0, 8, 0, 0)
        issues_layout.addWidget(self.no_issues)
        issues_layout.addLayout(body, 1)

        # ---- 当前稿件 --------------------------------------------------------
        self.heading_box = QComboBox()
        self.heading_box.currentIndexChanged.connect(self._jump_to_heading)
        self.draft = QTextBrowser()
        self.draft.setObjectName("reportDraft")
        self.draft.setOpenExternalLinks(False)
        self.draft_hint = QLabel(i18n.tr('这是可预览的稿件；是否可交付以上方状态为准。'))
        self.draft_hint.setObjectName("hint")
        draft_page = QWidget()
        draft_layout = QVBoxLayout(draft_page)
        draft_layout.setContentsMargins(0, 8, 0, 0)
        for widget in (self.draft_hint, self.heading_box):
            draft_layout.addWidget(widget)
        draft_layout.addWidget(self.draft, 1)
        # 导出当前稿件：DOCX 按报告模板排版，Markdown 是原始稿件。
        self.export_docx_button = QPushButton(i18n.tr('导出 DOCX…'))
        self.export_docx_button.clicked.connect(lambda: self.exportRequested.emit("docx"))
        self.export_md_button = QPushButton(i18n.tr('导出 Markdown…'))
        self.export_md_button.clicked.connect(lambda: self.exportRequested.emit("md"))
        export_row = QHBoxLayout()
        export_row.addWidget(self.export_docx_button)
        export_row.addWidget(self.export_md_button)
        export_row.addStretch(1)
        draft_layout.addLayout(export_row)

        # ---- 运行详情 + 模板 ----------------------------------------------------
        self.runtime = QTextBrowser()
        self.runtime.setObjectName("reportRuntime")
        self.template_line = QLabel("")
        self.template_line.setWordWrap(True)
        self.template_add = QPushButton(i18n.tr('选择报告模板（DOCX）…'))
        self.template_add.clicked.connect(self.templateAddRequested.emit)
        self.template_remove = QPushButton(i18n.tr('移除报告模板'))
        self.template_remove.clicked.connect(self.templateRemoveRequested.emit)
        template_card = Card()
        template = QVBoxLayout(template_card)
        template.setContentsMargins(16, 12, 16, 12)
        template_title = QLabel(i18n.tr('报告结构模板'))
        template_title.setObjectName("sectionTitle")
        template_hint = QLabel(i18n.tr('可选：用 DOCX 模板固定章节、标题层级、前后置部分和 Word 样式。'))
        template_hint.setObjectName("hint")
        template_hint.setWordWrap(True)
        template_buttons = QHBoxLayout()
        template_buttons.addWidget(self.template_add)
        template_buttons.addWidget(self.template_remove)
        template_buttons.addStretch(1)
        for widget in (template_title, template_hint, self.template_line):
            template.addWidget(widget)
        template.addLayout(template_buttons)
        runtime_page = QWidget()
        runtime_layout = QVBoxLayout(runtime_page)
        runtime_layout.setContentsMargins(0, 8, 0, 0)
        runtime_layout.addWidget(self.runtime, 1)
        runtime_layout.addWidget(template_card)

        self.tabs = QTabWidget()
        self.tabs.addTab(issues_page, i18n.tr('问题与修复'))
        self.tabs.addTab(draft_page, i18n.tr('当前稿件'))
        self.tabs.addTab(runtime_page, i18n.tr('运行详情'))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.title)
        layout.addWidget(self.kicker)
        layout.addWidget(self.status)
        layout.addWidget(self.chips)
        layout.addWidget(self.gate_note)
        layout.addWidget(self.impact)
        layout.addWidget(self.rebuild_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.tabs, 1)
        self.apply({})

    # ================= 数据 =================
    def apply(self, view: dict, *, busy: bool = False, connection_ok: bool = True) -> None:
        self._view = view or {}
        self._busy = busy
        self._connection_ok = connection_ok
        label = i18n.tr_dynamic(str(self._view.get("overall_label") or ""))
        cta = i18n.tr_dynamic(str(self._view.get("primary_cta") or ""))
        self.status.set_tone(_TONE.get(str(self._view.get("tone") or ""), "info"))
        self.status.set_text(f"{label}　→　{cta}" if label and cta else label)
        self.chips.setText("　".join(
            f"{i18n.tr(name)} <b>{_escape(i18n.tr_dynamic(value))}</b>"
            for name, value in self._view.get("chips") or []))
        self.chips.setTextFormat(Qt.TextFormat.RichText)
        self.gate_note.setText(i18n.tr_dynamic(str(self._view.get("gate_note") or "")))
        self.gate_note.setVisible(bool(self._view.get("gate_note")))

        impact = self._view.get("impact") or {}
        self.impact.set_text(
            i18n.tr('报告需要更新：{v0}', v0=impact.get("reason") or "") if impact.get("stale") else "")
        self.rebuild_button.setVisible(bool(impact.get("stale")))
        self.rebuild_button.setEnabled(connection_ok and not busy)
        has_report = bool(self._view.get("has_report"))
        self.export_docx_button.setEnabled(has_report and not busy)
        self.export_md_button.setEnabled(has_report and not busy)

        keep = self._selected_category()
        self._groups = list(self._view.get("groups") or [])
        self.issue_list.blockSignals(True)
        self.issue_list.clear()
        for group in self._groups:
            mark = _SEVERITY_MARK.get(group.get("severity"), "·")
            self.issue_list.addItem(QListWidgetItem(
                f"{mark}  {i18n.tr_dynamic(group['category'])}　［{i18n.tr(_SEVERITY_LABEL.get(group.get('severity'), '问题'))}］"))
        self.issue_list.blockSignals(False)
        categories = [g["category"] for g in self._groups]
        row = categories.index(keep) if keep in categories else (0 if categories else -1)
        self.issue_list.setCurrentRow(row)
        self.no_issues.setVisible(not self._groups)
        self.issue_list.setVisible(bool(self._groups))
        self._issue_selected(row)

        self._fill_draft()
        rows = "".join(f"<tr><td>{_escape(i18n.tr_dynamic(k))}&nbsp;&nbsp;</td><td><b>{_escape(i18n.tr_dynamic(v))}</b></td></tr>"
                       for k, v in self._view.get("runtime_rows") or [])
        self.runtime.setHtml(f"<table>{rows}</table>" if rows else "")
        template = self._view.get("template") or {}
        self.template_line.setText(
            i18n.tr('当前模板：{v0}', v0=template.get("name") or "—") if template.get("present")
            else i18n.tr('未配置模板。'))
        self.template_remove.setVisible(bool(template.get("present")))
        self.template_add.setEnabled(not busy)

    def _selected_category(self) -> str:
        row = self.issue_list.currentRow()
        return str(self._groups[row]["category"]) if 0 <= row < len(self._groups) else ""

    def current_group(self) -> dict:
        row = self.issue_list.currentRow()
        return self._groups[row] if 0 <= row < len(self._groups) else {}

    # ================= 问题详情 =================
    def _issue_selected(self, row: int) -> None:
        group = self.current_group()
        for box in list(self._answer_boxes.values()):
            box.hide()  # before detaching (see segment_assist._clear)
            box.setParent(None)
        self._answer_boxes.clear()
        while self.questions_layout.count():
            item = self.questions_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()  # before detaching (see segment_assist._clear)
                item.widget().setParent(None)
        if not group:
            self.issue_detail.setHtml("")
            for widget in (self.repair_button, self.open_review_button, self.open_cases_button):
                widget.setVisible(False)
            self.repair_hint.setText("")
            return
        category = group["category"]
        parts = [f"<h3>{_escape(i18n.tr_dynamic(group.get('action') or category))}</h3>",
                 f"<p><b>{_escape(i18n.tr_dynamic(category))}</b></p>"]
        if group.get("impact"):
            parts.append(f"<p>{_escape(i18n.tr_dynamic(group['impact']))}</p>")
        if group.get("recommendation"):
            parts.append(f"<p><i>{_escape(i18n.tr_dynamic(group['recommendation']))}</i></p>")
        details = list(group.get("details") or [])
        details += [d for d in (self._view.get("issue_details") or {}).get(category, [])
                    if d not in details]
        if details:
            parts.append("<ul>" + "".join(f"<li>{_escape(i18n.tr_dynamic(d))}</li>" for d in details) + "</ul>")
        sections = group.get("sections") or []
        if sections and category == "章节需要重新生成":
            parts.append("<p>" + _escape(i18n.tr('将只重新生成问题章节：') + "、".join(
                i18n.tr('第 {v0} 节', v0=value) for value in sections)) + "</p>")
        if category == "案例不足":
            rows = self._view.get("case_rows") or []
            parts.append("<p>" + _escape(i18n.tr('选择状态：{v0} · 当前案例 {v1} 个',
                                                 v0=self._view.get("case_status") or i18n.tr('未记录'),
                                                 v1=len(rows))) + "</p>")
        if category == "文献证据缺失":
            literature = self._view.get("literature") or {}
            parts.append("<p>" + _escape(i18n.tr('已登记来源 {v0} · 可核验证据 {v1} · 文献主张 {v2}',
                                                 v0=len(literature.get("sources") or []),
                                                 v1=literature.get("evidence", 0),
                                                 v2=literature.get("claims", 0))) + "</p>")
            parts.append("<ul>" + "".join(f"<li>{_escape(s)}</li>" for s in literature.get("sources") or []) + "</ul>")
        self.issue_detail.setHtml("".join(parts))

        questions = self._view.get("open_questions") or [] if category == "需要人工补充" else []
        for question in questions:
            qid = str(question.get("question_id"))
            label = QLabel(f"<b>{_escape(question.get('question') or i18n.tr('请补充项目过程信息'))}</b>")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            self.questions_layout.addWidget(label)
            context = (question.get("context") or {}).get("source")
            if context:
                hint = QLabel(i18n.tr('原文：{v0}', v0=str(context)[:180]))
                hint.setObjectName("hint")
                hint.setWordWrap(True)
                self.questions_layout.addWidget(hint)
            box = QPlainTextEdit()
            box.setPlaceholderText(i18n.tr('如无法回忆，可填写「不记得/没有相关记录」。'))
            box.setMaximumHeight(80)
            self._answer_boxes[qid] = box
            submit = QPushButton(i18n.tr('提交回答'))
            submit.clicked.connect(lambda _c=False, q=qid: self._submit_answer(q))
            self.questions_layout.addWidget(box)
            self.questions_layout.addWidget(submit, 0, Qt.AlignmentFlag.AlignLeft)

        is_review = group.get("target") == "review"
        self.open_review_button.setVisible(is_review)
        self.open_cases_button.setVisible(category == "案例不足")
        can_repair = bool(group.get("can_repair"))
        self.repair_button.setVisible(can_repair)
        self.repair_button.setText(i18n.tr_dynamic(group.get("repair_label") or ""))
        waiting = category == "需要人工补充" and bool(questions)
        self.repair_button.setEnabled(can_repair and self._connection_ok and not waiting and not self._busy)
        if can_repair and not self._connection_ok:
            self.repair_hint.setText(i18n.tr('继续生成需要先在「设置」里配置当前模型的 API Key。'))
        elif waiting:
            self.repair_hint.setText(i18n.tr('请先提交上方所有待回答问题；无法回忆时可以如实说明。'))
        else:
            self.repair_hint.setText("")

    def _submit_answer(self, question_id: str) -> None:
        box = self._answer_boxes.get(question_id)
        self.answerSubmitted.emit(question_id, box.toPlainText() if box else "")

    def _emit_repair(self) -> None:
        group = self.current_group()
        if group:
            self.repairRequested.emit(group["category"], list(group.get("sections") or []))

    # ================= 稿件 =================
    def _fill_draft(self) -> None:
        headings = list(self._view.get("headings") or [])
        self.heading_box.blockSignals(True)
        self.heading_box.clear()
        self.heading_box.addItem(i18n.tr('跳到章节…'), -1)
        for item in headings:
            self.heading_box.addItem("　" * max(0, int(item["level"]) - 1) + item["title"], item["anchor"])
        self.heading_box.blockSignals(False)
        text = self._view.get("report_text") or ""
        if text:
            self.draft.setMarkdown(text)
        else:
            self.draft.setPlainText(i18n.tr('尚未生成报告稿。'))
        self.heading_box.setVisible(bool(headings))

    def _jump_to_heading(self, index: int) -> None:
        anchor = self.heading_box.itemData(index)
        title = self.heading_box.itemText(index).strip()
        if anchor in (None, -1) or not title:
            return
        self.draft.moveCursor(self.draft.textCursor().MoveOperation.Start)
        self.draft.find(title)
