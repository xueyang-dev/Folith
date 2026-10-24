"""「任务详情」页：开始翻译以后，这个任务用的是什么设置。

开始翻译后「准备」页就收起了，之前的选择（翻译模式、术语、审校、识别方式、交付
文件……）在界面上再也看不到。这一页把它们如实列出来（来自
:func:`folith.qt.adapter.task_details`），并放着文档画像——画像判断错了可以在这里改。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import i18n
from . import style as style_module
from . import theme as theme_module


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


#: 全文摘要在任务详情里最多显示这么多字（完整内容仍在模型上下文里，不在这里铺开）。
SUMMARY_LIMIT = 320


class TaskDetailsPanel(QWidget):
    """任务详情：从顶栏 ⓘ 下面展开的「凸」字卡片里的内容。

    卡片里不再套卡片（2026-10-17 试用反馈：太多太杂）：几段平铺、发丝线隔开——
    全文摘要、文档画像、本任务的设置、用量一行、提升到项目记忆。
    """

    profileEditRequested = Signal()
    promoteRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groups: list[dict] = []
        self._sections: list[QWidget] = []
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        self.profile_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.profile_button = QPushButton(i18n.tr('修改画像…'))
        self.profile_button.clicked.connect(self.profileEditRequested.emit)
        profile_section, profile_layout = self._section('文档画像', self.profile_button)
        profile_layout.addWidget(self.profile_label)

        self.groups_card = QWidget()
        self._groups_layout = QVBoxLayout(self.groups_card)
        self._groups_layout.setContentsMargins(0, 0, 0, 0)
        self._groups_layout.setSpacing(theme_module.SPACE["sm"])

        # 项目记忆：把这个任务里**人确认过**的术语、风格规则与决定带给同项目的后续任务。
        self.promote_button = QPushButton(i18n.tr('提升到项目记忆'))
        self.promote_button.setToolTip(i18n.tr(
            '把本任务锁定的术语、确认的风格规则与人工决定提升到项目记忆，'
            '同项目的后续任务会用上它们。候选和未审校的内容不会被提升。'))
        self.promote_button.clicked.connect(self.promoteRequested.emit)
        memory = QHBoxLayout()
        memory.setContentsMargins(0, 0, 0, 0)
        memory.addWidget(self.promote_button)
        memory.addStretch(1)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["md"])
        column.addWidget(self._build_understanding_card())
        column.addWidget(self._divider())
        column.addWidget(profile_section)
        column.addWidget(self._divider())
        column.addWidget(self.groups_card)
        column.addWidget(self._divider())
        column.addWidget(self._build_usage_card())
        column.addLayout(memory)
        column.addStretch(1)

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setObjectName("hairline")
        line.setFixedHeight(1)
        return line

    def _section(self, title: str, action: QWidget | None = None) -> tuple[QWidget, QVBoxLayout]:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"] + 2)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(style_module.SectionLabel(i18n.tr(title)), 1)
        if action is not None:
            head.addWidget(action)
        layout.addLayout(head)
        return section, layout

    # ---- 全文理解 / 用量与复用（来自 adapter.run_facts） --------------------
    def _build_understanding_card(self) -> QWidget:
        """全文摘要：模型读完全文后给出的概要（翻译每一段时作为上下文）。只放摘要。"""
        card, layout = self._section('全文摘要')
        self.understanding_notice = style_module.NoticeStrip("", tone="warn")
        self.understanding_notice.setVisible(False)
        self.understanding_view = QLabel("")
        self.understanding_view.setObjectName("understandingSummary")
        self.understanding_view.setWordWrap(True)
        self.understanding_view.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.understanding_view)
        layout.addWidget(self.understanding_notice)
        self.understanding_card = card
        card.setVisible(False)
        return card

    def _build_usage_card(self) -> QWidget:
        """用量：一行——调用了多少次模型、大约多少 token；翻译记忆和缓存省下了多少。"""
        card, layout = self._section('用量')
        self.usage_summary = QLabel("")
        self.usage_summary.setWordWrap(True)
        self.usage_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.usage_note = QLabel("")
        self.usage_note.setVisible(False)  # 说明进 tooltip，不占一行
        self.reuse_label = QLabel("")
        self.reuse_label.setObjectName("hint")
        self.reuse_label.setWordWrap(True)
        layout.addWidget(self.usage_summary)
        layout.addWidget(self.reuse_label)
        self.usage_card = card
        card.setVisible(False)
        return card

    def apply_run_facts(self, facts: dict) -> None:
        facts = facts or {}
        self._apply_understanding(facts.get("understanding") or {})
        self._apply_usage(facts.get("usage") or {}, facts.get("reuse") or {})

    def _apply_understanding(self, view: dict) -> None:
        from .segment_editor import cell_direction

        summary = str(view.get("summary") or "").strip()
        available = bool(view.get("available")) and bool(summary)
        self.understanding_card.setVisible(available)
        if not available:
            return
        warnings = []
        if view.get("synopsis_fallback"):
            warnings.append(i18n.tr('全文概要没有拿到模型的结构化结果，暂用各单元摘要拼接。'))
        self.understanding_notice.set_text("\n".join(warnings))
        self.understanding_notice.setVisible(bool(warnings))
        short = summary if len(summary) <= SUMMARY_LIMIT else summary[:SUMMARY_LIMIT].rstrip() + "…"
        self.understanding_view.setText(short)
        self.understanding_view.setToolTip(summary if short != summary else "")
        # 摘要用译文语言写（例如阿拉伯语）：按它自己的书写方向排。
        direction = cell_direction(short)
        self.understanding_view.setLayoutDirection(direction)
        self.understanding_view.setAlignment(
            Qt.AlignmentFlag.AlignAbsolute | (Qt.AlignmentFlag.AlignRight
                                              if direction == Qt.LayoutDirection.RightToLeft
                                              else Qt.AlignmentFlag.AlignLeft))

    def _apply_usage(self, usage: dict, reuse: dict) -> None:
        has_usage, has_reuse = bool(usage.get("available")), bool(reuse.get("available"))
        self.usage_card.setVisible(has_usage or has_reuse)
        if has_usage:
            tokens = format(int(usage.get("total_tokens") or 0), ",")
            minutes = float(usage.get("seconds") or 0.0) / 60.0
            text = i18n.tr('共调用模型 {v0} 次，约 {v1} token，模型累计耗时 {v2} 分钟。',
                           v0=format(int(usage.get("calls") or 0), ","), v1=tokens,
                           v2=f"{minutes:.1f}")
            if usage.get("failures"):
                text += " " + i18n.tr('其中 {v0} 次失败（已重试或记入失败批次）。', v0=usage["failures"])
            self.usage_summary.setText(text)
        else:
            self.usage_summary.setText(i18n.tr('还没有模型调用记录。'))
        reuse_parts = []
        if reuse.get("tm_status") == "disabled":
            reuse_parts.append(i18n.tr('翻译记忆本次未启用（没有逐段查询）'))
        elif reuse.get("tm_status") == "no_target_language":
            reuse_parts.append(i18n.tr('目标语言无法确定，翻译记忆未参与查询'))
        if reuse.get("tm_used"):
            reuse_parts.append(i18n.tr('翻译记忆直接复用 {v0} 段', v0=reuse["tm_used"]))
        if reuse.get("tm_recovered"):
            reuse_parts.append(i18n.tr('恢复进度时从记忆找回 {v0} 段', v0=reuse["tm_recovered"]))
        if reuse.get("tm_conflicts"):
            reuse_parts.append(i18n.tr('同原文不同上下文、仅作建议 {v0} 段',
                                       v0=reuse["tm_conflicts"]))
        if reuse.get("tm_legacy_unused"):
            reuse_parts.append(i18n.tr('项目里有 {v0} 条旧记忆没有语言标注，'
                                       '保留但未参与自动复用',
                                       v0=reuse["tm_legacy_unused"]))
        if reuse.get("cache_hits"):
            reuse_parts.append(i18n.tr('相同原文复用已有译文 {v0} 次', v0=reuse["cache_hits"]))
        self.reuse_label.setText(("；".join(reuse_parts) + "。") if reuse_parts else "")
        self.reuse_label.setVisible(bool(reuse_parts))
        self.usage_note.setText(
            i18n.tr('token 数在提供方没有回报时按字符估算（约 4 个字符 1 个 token），用于比较不同设置，不是账单。')
            if usage.get("estimated") else i18n.tr('token 数来自模型提供方的回报。'))
        self.usage_summary.setToolTip(self.usage_note.text() if has_usage else "")

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
