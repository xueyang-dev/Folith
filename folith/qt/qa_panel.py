"""合规与最终 QA 页（学术专业模式）。

每一项检查只决定一件事：结构检查和页面渲染通过，也不等于作者与 Word 的最终复核
已经完成。页面把「合规规则清单」和「四项独立质量事实」分开呈现，只有它们都满足，
版本才能被标为最终确认。只做展示与发信号；写状态的是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from . import i18n
from . import style as style_module
from .style import Card

_RULE_MARK = {"pass": "✓", "fail": "✕", "manual_review": "!", "not_checked": "…",
              "not_applicable": "–"}
_FACT_TONE = {"PASS": "ok", "CONFIRMED": "ok", "FAIL": "danger", "STALE": "warn",
              "NOT_RUN": "muted", "NOT_CONFIRMED": "muted"}


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class QAPanel(QWidget):
    renderRequested = Signal()
    factToggled = Signal(str, bool)        # field, confirmed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view: dict = {}
        self._rules: list[dict] = []
        self._busy = False

        self.kicker = QLabel(i18n.tr('合规与最终 QA'))
        self.kicker.setObjectName("sectionTitle")
        self.lead = QLabel(i18n.tr('每一项检查都独立决定一件事；结构检查和页面渲染通过，也不等于作者与 Word 的最终复核已经完成。'))
        self.lead.setObjectName("hint")
        self.lead.setWordWrap(True)
        self.profile_line = QLabel("")
        self.profile_line.setObjectName("hint")
        self.summary = style_module.NoticeStrip("", tone="warn")

        self.rule_list = QListWidget()
        self.rule_list.setObjectName("ruleList")
        self.rule_list.setMinimumHeight(180)
        self.rule_list.currentRowChanged.connect(self._rule_selected)
        self.rule_detail = QTextBrowser()
        self.rule_detail.setObjectName("ruleDetail")
        self.rule_detail.setMinimumHeight(110)
        self.rule_detail.setOpenExternalLinks(True)
        rules_card = Card()
        rules = QVBoxLayout(rules_card)
        rules.setContentsMargins(16, 14, 16, 14)
        rules.setSpacing(8)
        rules_title = QLabel(i18n.tr('合规检查清单'))
        rules_title.setObjectName("sectionTitle")
        rules.addWidget(rules_title)
        rules.addWidget(self.rule_list)
        rules.addWidget(self.rule_detail)

        self.facts_layout = QVBoxLayout()
        self.facts_layout.setSpacing(8)
        facts_card = Card()
        facts = QVBoxLayout(facts_card)
        facts.setContentsMargins(16, 14, 16, 14)
        facts.setSpacing(8)
        facts_title = QLabel(i18n.tr('独立最终质量事实'))
        facts_title.setObjectName("sectionTitle")
        facts.addWidget(facts_title)
        facts.addLayout(self.facts_layout)
        self._fact_rows: dict[str, dict] = {}

        self.pages_title = QLabel(i18n.tr('关键页面定位（作者需在 Word 中复核）'))
        self.pages_title.setObjectName("sectionTitle")
        self.pages = QTableWidget(0, 3)
        self.pages.setHorizontalHeaderLabels([i18n.tr('位置'), i18n.tr('页码'), i18n.tr('依据')])
        self.pages.verticalHeader().setVisible(False)
        self.pages.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.pages.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.pages.setMinimumHeight(190)
        self.final_warning = QLabel(i18n.tr('只有以上四项独立质量事实和合规门禁都分别满足要求，才能把版本标为最终确认。'))
        self.final_warning.setObjectName("hint")
        self.final_warning.setWordWrap(True)

        content = QWidget()
        content.setObjectName("pageCanvas")
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(12)
        for widget in (self.kicker, self.lead, self.profile_line, self.summary, rules_card,
                       facts_card, self.pages_title, self.pages, self.final_warning):
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

    # ================= 数据 =================
    def apply(self, view: dict, *, busy: bool = False) -> None:
        self._view = view or {}
        self._busy = busy
        profile = self._view.get("profile") or {}
        self.profile_line.setText(
            i18n.tr('{v0} · 参考映射状态：{v1}', v0=profile.get("name") or "—",
                    v1=i18n.tr_dynamic(str(profile.get("mapping") or "—")))
            if profile else "")
        attention = int(self._view.get("attention") or 0)
        if attention:
            parts = []
            if self._view.get("failed"):
                parts.append(i18n.tr('{v0} 项自动检查失败', v0=self._view["failed"]))
            if self._view.get("manual"):
                parts.append(i18n.tr('{v0} 项需要人工复核', v0=self._view["manual"]))
            if self._view.get("not_run"):
                parts.append(i18n.tr('{v0} 项尚未运行', v0=self._view["not_run"]))
            self.summary.set_text(i18n.tr('还有 {v0} 项需要处理', v0=attention) + "：" + "　".join(parts))
        else:
            self.summary.set_text("")
        self._fill_rules()
        self._fill_facts()
        key_pages = list(self._view.get("key_pages") or [])
        self.pages_title.setVisible(bool(key_pages))
        self.pages.setVisible(bool(key_pages))
        self.pages.setRowCount(len(key_pages))
        for row, ref in enumerate(key_pages):
            for column, value in enumerate((ref.get("label"), ref.get("page") or "—", ref.get("basis"))):
                self.pages.setItem(row, column, QTableWidgetItem(i18n.tr_dynamic(str(value or ""))))

    def _fill_rules(self) -> None:
        keep = self.rule_list.currentRow()
        self._rules = list(self._view.get("rules") or [])
        self.rule_list.blockSignals(True)
        self.rule_list.clear()
        for rule in self._rules:
            item = QListWidgetItem(
                f"{_RULE_MARK.get(rule['status'], '·')}  {rule['label']}　［{i18n.tr_dynamic(rule['status_label'])}］")
            item.setForeground(style_module.qcolor(style_module.text_color(
                {"pass": "text-secondary", "fail": "danger", "manual_review": "warning",
                 "not_checked": "text-muted"}.get(rule["status"], "text-secondary"))))
            self.rule_list.addItem(item)
        self.rule_list.blockSignals(False)
        if self._rules:
            first = next((i for i, r in enumerate(self._rules) if r["needs_attention"]), 0)
            self.rule_list.setCurrentRow(keep if 0 <= keep < len(self._rules) else first)
        else:
            self.rule_detail.setHtml("")
        self._rule_selected(self.rule_list.currentRow())

    def _rule_selected(self, row: int) -> None:
        if not (0 <= row < len(self._rules)):
            self.rule_detail.setHtml("")
            return
        rule = self._rules[row]
        lines = [f"<p><b>{_escape(rule['label'])}</b></p>"]
        if rule["message"]:
            lines.append(f"<p>{_escape(rule['message'])}</p>")
        lines.append("<p>" + _escape(i18n.tr('规则来源：{v0} · 页码/条款：{v1}',
                                             v0=i18n.tr_dynamic(rule["source"]),
                                             v1=rule["page_or_clause"])) + "</p>")
        if rule["source_url"]:
            lines.append(f'<p><a href="{_escape(rule["source_url"])}">{_escape(i18n.tr("打开来源"))}</a></p>')
        lines.append("<p>" + _escape(i18n.tr('适用范围：{v0} · 检查方式：{v1} · 规则层级：{v2}',
                                             v0=rule["scope"],
                                             v1=i18n.tr_dynamic(rule["check_level"]),
                                             v2=i18n.tr_dynamic(rule["authority"]))) + "</p>")
        if rule["implementation"]:
            lines.append("<p>" + _escape(i18n.tr('项目实现依据（非规范来源）：{v0}',
                                                 v0=rule["implementation"])) + "</p>")
        lines.append("<p class='muted'>" + _escape(i18n.tr('规则标识：{v0}', v0=rule["id"] or "—")) + "</p>")
        self.rule_detail.setHtml("".join(lines))

    def _fill_facts(self) -> None:
        for field in list(self._fact_rows):
            row = self._fact_rows.pop(field)
            row["widget"].hide()  # before detaching (see segment_assist._clear)
            row["widget"].setParent(None)
            row["widget"].deleteLater()
        for fact in self._view.get("facts") or []:
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            text = QLabel(f"<b>{_escape(i18n.tr(fact['label']))}</b><br>"
                          f"<span>{_escape(i18n.tr(fact['proof']))}</span>")
            text.setWordWrap(True)
            chip = style_module.Chip(i18n.tr_dynamic(str(fact["status_label"])),
                                     _FACT_TONE.get(str(fact["status"]), "muted"))
            button = QPushButton("")
            field = fact["field"]
            if field == "libreoffice_render":
                button.setText(i18n.tr('运行 LibreOffice 页面预检'))
                button.setEnabled(bool(self._view.get("p2_done")) and not self._busy)
                button.clicked.connect(self.renderRequested.emit)
            elif fact["manual"]:
                confirmed = bool(fact["confirmed"])
                button.setText(i18n.tr('撤销确认') if confirmed else i18n.tr('确认已完成'))
                button.setEnabled(not self._busy)
                button.clicked.connect(
                    lambda _checked=False, f=field, c=confirmed: self.factToggled.emit(f, not c))
            else:
                button.setVisible(False)
            layout.addWidget(text, 1)
            layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.facts_layout.addWidget(holder)
            self._fact_rows[field] = {"widget": holder, "chip": chip, "button": button}

    def fact_button(self, field: str) -> QPushButton:
        return self._fact_rows[field]["button"]

    def fact_chip_text(self, field: str) -> str:
        return self._fact_rows[field]["chip"].text()
