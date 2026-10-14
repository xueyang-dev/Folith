"""「任务详情」页：开始翻译以后，这个任务用的是什么设置。

开始翻译后「准备」页就收起了，之前的选择（翻译模式、术语、审校、识别方式、交付
文件……）在界面上再也看不到。这一页把它们如实列出来（来自
:func:`folith.qt.adapter.task_details`），并放着文档画像——画像判断错了可以在这里改。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import i18n
from . import style as style_module
from . import theme as theme_module
from .style import Card


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


class TaskDetailsPanel(QWidget):
    profileEditRequested = Signal()
    promoteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[dict] = []
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        self.profile_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile_button = QPushButton(i18n.tr('修改画像…'))
        self.profile_button.clicked.connect(self.profileEditRequested.emit)
        profile_card = Card()
        profile_layout = QVBoxLayout(profile_card)
        profile_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                          theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        head = QHBoxLayout()
        title = QLabel(i18n.tr('文档画像'))
        title.setObjectName("cardTitle")
        head.addWidget(title, 1)
        head.addWidget(self.profile_button)
        profile_layout.addLayout(head)
        profile_layout.addWidget(self.profile_label)

        self.groups_card = Card()
        self._groups_layout = QVBoxLayout(self.groups_card)
        self._groups_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                               theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        self._groups_layout.setSpacing(theme_module.SPACE["md"])

        column = QWidget()
        column.setMaximumWidth(820)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, theme_module.SPACE["lg"], 0, 0)
        column_layout.setSpacing(theme_module.SPACE["md"])
        column_layout.addWidget(profile_card)
        column_layout.addWidget(self._build_understanding_card())
        column_layout.addWidget(self._build_usage_card())
        column_layout.addWidget(self.groups_card)
        # 项目记忆：把这个任务里**人确认过**的术语、风格规则与决定带给同项目的后续任务。
        memory_card = Card()
        memory_layout = QHBoxLayout(memory_card)
        memory_layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                         theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        memory_text = QLabel(i18n.tr('把本任务锁定的术语、确认的风格规则与人工决定提升到项目记忆，'
                                     '同项目的后续任务会用上它们。候选和未审校的内容不会被提升。'))
        memory_text.setObjectName("hint")
        memory_text.setWordWrap(True)
        self.promote_button = QPushButton(i18n.tr('提升到项目记忆'))
        self.promote_button.clicked.connect(self.promoteRequested.emit)
        memory_layout.addWidget(memory_text, 1)
        memory_layout.addWidget(self.promote_button, 0, Qt.AlignmentFlag.AlignVCenter)
        column_layout.addWidget(memory_card)
        column_layout.addStretch(1)
        content = QWidget()
        centre = QHBoxLayout(content)
        centre.setContentsMargins(0, 0, 0, 0)
        centre.addStretch(1)
        centre.addWidget(column, 100)
        centre.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    # ---- 全文理解 / 用量与复用（来自 adapter.run_facts） --------------------
    def _card(self, title: str) -> tuple[Card, QVBoxLayout]:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["sm"])
        heading = QLabel(i18n.tr(title))
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        return card, layout

    def _build_understanding_card(self) -> Card:
        """全文理解：模型读完全文后给出的概要、主线与各单元摘要（翻译时作为上下文）。"""
        card, layout = self._card('全文理解')
        self.understanding_hint = QLabel(i18n.tr('翻译每一段时，Agent 会带上这里的全文概要和所在单元的摘要。'))
        self.understanding_hint.setObjectName("hint")
        self.understanding_hint.setWordWrap(True)
        self.understanding_notice = style_module.NoticeStrip("", tone="warn")
        self.understanding_notice.setVisible(False)
        self.understanding_view = QTextBrowser()
        self.understanding_view.setObjectName("understandingView")
        self.understanding_view.setOpenLinks(False)
        layout.addWidget(self.understanding_hint)
        layout.addWidget(self.understanding_notice)
        layout.addWidget(self.understanding_view)
        self.understanding_card = card
        card.setVisible(False)
        return card

    def _build_usage_card(self) -> Card:
        """用量与复用：调用了多少次模型、大约多少 token；翻译记忆和缓存省下了多少。"""
        card, layout = self._card('用量与复用')
        self.usage_summary = QLabel("")
        self.usage_summary.setWordWrap(True)
        self.usage_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.usage_note = QLabel("")
        self.usage_note.setObjectName("hint")
        self.usage_note.setWordWrap(True)
        self.usage_grid_holder = QWidget()
        self.usage_grid = QGridLayout(self.usage_grid_holder)
        self.usage_grid.setContentsMargins(0, 0, 0, 0)
        self.usage_grid.setHorizontalSpacing(theme_module.SPACE["lg"])
        self.usage_grid.setVerticalSpacing(theme_module.SPACE["xs"])
        self.reuse_label = QLabel("")
        self.reuse_label.setWordWrap(True)
        layout.addWidget(self.usage_summary)
        layout.addWidget(self.usage_grid_holder)
        layout.addWidget(self.reuse_label)
        layout.addWidget(self.usage_note)
        self.usage_card = card
        card.setVisible(False)
        return card

    def apply_run_facts(self, facts: dict) -> None:
        facts = facts or {}
        self._apply_understanding(facts.get("understanding") or {})
        self._apply_usage(facts.get("usage") or {}, facts.get("reuse") or {})

    def _apply_understanding(self, view: dict) -> None:
        available = bool(view.get("available"))
        self.understanding_card.setVisible(available)
        if not available:
            return
        warnings = [i18n.tr_dynamic(item) for item in view.get("warnings") or []]
        fallback_units = sum(1 for item in view.get("sections") or [] if item.get("fallback"))
        if view.get("synopsis_fallback"):
            warnings.insert(0, i18n.tr('全文概要没有拿到模型的结构化结果，暂用各单元摘要拼接。'))
        if fallback_units:
            warnings.insert(0, i18n.tr('{v0} 个单元只用了首句作为临时摘要；可在「检查与修复」补跑。',
                                       v0=fallback_units))
        self.understanding_notice.set_text("\n".join(warnings))
        self.understanding_notice.setVisible(bool(warnings))
        parts: list[str] = []
        if view.get("summary"):
            parts.append(f"<p><b>{_escape(i18n.tr('全文概要'))}</b><br/>{_escape(view['summary'])}</p>")
        if view.get("arc"):
            parts.append(f"<p><b>{_escape(i18n.tr('论证 / 叙事主线'))}</b><br/>{_escape(view['arc'])}</p>")
        for title, key in (('主题', "themes"), ('全书关键概念', "terms"), ('翻译连续性提示', "notes")):
            items = view.get(key) or []
            if items:
                parts.append(f"<p><b>{_escape(i18n.tr(title))}</b><br/>"
                             + "；".join(_escape(item) for item in items) + "</p>")
        sections = view.get("sections") or []
        if sections:
            parts.append(f"<p><b>{_escape(i18n.tr('各单元摘要（{v0} 个）', v0=len(sections)))}</b></p>")
            for item in sections:
                span = ""
                if item.get("start") is not None and item.get("end") is not None:
                    span = i18n.tr('第 {v0}–{v1} 段', v0=item["start"] + 1, v1=item["end"] + 1)
                head = " · ".join(part for part in (item.get("label") or "", span) if part)
                tag = f" <small>({_escape(i18n.tr('首句备用'))})</small>" if item.get("fallback") else ""
                terms = ("<br/><small>" + _escape("、".join(item["terms"])) + "</small>"
                         if item.get("terms") else "")
                parts.append(f"<p><b>{_escape(head)}</b>{tag}<br/>"
                             f"{_escape(item.get('summary') or '')}{terms}</p>")
        self.understanding_view.setHtml("".join(parts))

    def _apply_usage(self, usage: dict, reuse: dict) -> None:
        has_usage, has_reuse = bool(usage.get("available")), bool(reuse.get("available"))
        self.usage_card.setVisible(has_usage or has_reuse)
        while self.usage_grid.count():
            widget = self.usage_grid.takeAt(0).widget()
            if widget is not None:
                widget.hide()  # before detaching (see segment_assist._clear)
                widget.setParent(None)
                widget.deleteLater()
        if has_usage:
            tokens = format(int(usage.get("total_tokens") or 0), ",")
            minutes = float(usage.get("seconds") or 0.0) / 60.0
            text = i18n.tr('共调用模型 {v0} 次，约 {v1} token，模型累计耗时 {v2} 分钟。',
                           v0=format(int(usage.get("calls") or 0), ","), v1=tokens,
                           v2=f"{minutes:.1f}")
            if usage.get("failures"):
                text += " " + i18n.tr('其中 {v0} 次失败（已重试或记入失败批次）。', v0=usage["failures"])
            self.usage_summary.setText(text)
            rows = [(item["label"], item["calls"], item["tokens"]) for item in usage.get("roles") or []]
            models = usage.get("models") or []
            if len(models) > 1:
                rows += [(item["label"], item["calls"], item["tokens"]) for item in models]
            for row, (label, calls, tokens_value) in enumerate(rows):
                self.usage_grid.addWidget(style_module.field_label(i18n.tr_dynamic(label)), row, 0)
                value = QLabel(i18n.tr('{v0} 次 · 约 {v1} token', v0=format(calls, ","),
                                       v1=format(tokens_value, ",")))
                self.usage_grid.addWidget(value, row, 1)
            self.usage_grid.setColumnStretch(1, 1)
        else:
            self.usage_summary.setText(i18n.tr('还没有模型调用记录。'))
        self.usage_grid_holder.setVisible(bool(self.usage_grid.count()))
        reuse_parts = []
        if reuse.get("tm_used"):
            reuse_parts.append(i18n.tr('翻译记忆直接复用 {v0} 段', v0=reuse["tm_used"]))
        if reuse.get("tm_recovered"):
            reuse_parts.append(i18n.tr('恢复进度时从记忆找回 {v0} 段', v0=reuse["tm_recovered"]))
        if reuse.get("cache_hits"):
            reuse_parts.append(i18n.tr('相同原文复用已有译文 {v0} 次', v0=reuse["cache_hits"]))
        self.reuse_label.setText(("；".join(reuse_parts) + "。") if reuse_parts else "")
        self.reuse_label.setVisible(bool(reuse_parts))
        self.usage_note.setText(
            i18n.tr('token 数在提供方没有回报时按字符估算（约 4 个字符 1 个 token），用于比较不同设置，不是账单。')
            if usage.get("estimated") else i18n.tr('token 数来自模型提供方的回报。'))
        self.usage_note.setVisible(has_usage)

    def apply(self, groups: list[dict], profile: dict, profile_text: str) -> None:
        self.profile_label.setText(
            profile_text or i18n.tr('还没有文档画像。可以自己填写：领域、文本类型、读者、语域。'))
        self.profile_label.setObjectName("" if profile_text else "hint")
        style_module.repolish(self.profile_label)
        if groups == self._groups:
            return
        self._groups = [dict(item) for item in groups]
        while self._groups_layout.count():
            item = self._groups_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # before detaching (see segment_assist._clear)
                widget.setParent(None)
                widget.deleteLater()
        for group in groups:
            self._groups_layout.addWidget(style_module.SectionLabel(i18n.tr(group["title"])))
            grid_holder = QWidget()
            grid = QGridLayout(grid_holder)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(theme_module.SPACE["lg"])
            grid.setVerticalSpacing(theme_module.SPACE["xs"])
            for row, (label, value) in enumerate(group["rows"]):
                grid.addWidget(style_module.field_label(i18n.tr_dynamic(label)), row, 0,
                               Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                text = QLabel(i18n.tr_dynamic(value))
                text.setWordWrap(True)
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(text, row, 1)
            grid.setColumnStretch(1, 1)
            self._groups_layout.addWidget(grid_holder)

    def texts(self) -> list[str]:
        return [f"{label}：{value}" for group in self._groups for label, value in group["rows"]]
