"""工作台右侧操作栏里的"本段参考"：需要注意的地方、术语、前后文、询问 Agent。

右栏按"看 → 查 → 改 → 记"组织（译者处理一段的顺序）：

* **需要注意**：本段未解决的检查项（可以直接「保留译文」或「采用建议」，不用跳去
  审校页）和翻译决策痕迹（中性观察，不是错误）；
* **术语**：本段命中的术语；悬停在本段里标出，点击在全文中定位，↵ 插入首选译名；
  译文出现禁用译名、或没用首选译名时提示；
* **前后文**：上一段、下一段（只在筛选或搜索时显示——平时网格里相邻的行就是前后文）；
* **询问 Agent**：就这一段自由提问，只回答、不改写译文。

这里只放控件与信号；数据来自 :mod:`folith.qt.adapter`，动作由工作台转给窗口。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
    QWidget,
)

from . import i18n, platform_ui
from . import icons
from . import style as style_module
from . import theme as theme_module

_SEVERITY_LABELS = {"blocking": "必须处理", "actionable": "建议处理",
                    "informational": "提示"}

#: 候选卡片上"当前译文是什么来路"的说明。键与
#: ``folith.translation_candidates.PROTECTION_*`` 一致；在调用时取词，切换界面
#: 语言后立刻生效（模块级 tr 会冻结在导入时的语言）。
def protection_text(marks) -> str:
    """Human-readable protection summary for a candidate view (empty if none)."""
    labels = {
        "human_accepted": i18n.tr('人工确认过的译文'),
        "human_edit": i18n.tr('人工改写的译文'),
        "reviewed": i18n.tr('已审校的译文'),
    }
    return "、".join(labels.get(str(mark), str(mark)) for mark in marks or [])


def _clear(layout) -> None:
    """Remove and discard every widget in ``layout``.

    Hide before detaching.  A widget added to a visible layout is shown by a
    *queued* call; if it is detached in the same turn (the inspector is rebuilt
    twice by "save and next"), that queued show then runs on a parentless widget
    and it pops up as a stray top-level window that steals focus.  An explicit
    ``hide()`` makes the queued show a no-op.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()  # before detaching (see segment_assist._clear)
            widget.setParent(None)
            widget.deleteLater()


def _wrapped(text: str, name: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if name:
        label.setObjectName(name)
    return label


class AttentionList(QWidget):
    """本段需要注意的地方：检查项（可直接处置）+ 翻译决策痕迹。"""

    preserveRequested = Signal(dict)      # 审校队列项：保留当前译文
    acceptRequested = Signal(dict)        # 审校队列项：采用建议译文
    locateRequested = Signal(dict)        # 决策痕迹：在原文 / 译文里标出对应片段
    analyzeRequested = Signal()           # 进一步审视本段（一次模型调用）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme_module.SPACE["sm"])
        self._texts: list[str] = []

    def texts(self) -> list[str]:
        """当前列出的每一条（测试与无障碍用）。"""
        return list(self._texts)

    def set_items(self, findings: list[dict], decisions: list[dict],
                  resolved_count: int = 0, *, can_analyze: bool = False,
                  analyzing: bool = False) -> None:
        _clear(self._layout)
        self._texts = []
        self.analyze_button = None
        for item in findings:
            self._add_finding(item)
        for decision in decisions:
            self._add_decision(decision)
        if not findings and not decisions:
            text = i18n.tr('没有需要注意的地方。')
            self._layout.addWidget(_wrapped(text, "hint"))
            self._texts.append(text)
        if resolved_count:
            text = i18n.tr('本段另有 {v0} 项已解决。', v0=resolved_count)
            self._layout.addWidget(_wrapped(text, "hint"))
            self._texts.append(text)
        if can_analyze:
            button = QPushButton(i18n.tr('正在审视…') if analyzing
                                 else i18n.tr('进一步审视本段'))
            style_module.set_button(button, "plain")
            button.setIcon(icons.icon("sparkles", 14, "accent"))
            button.setToolTip(i18n.tr('让模型找出这一段值得注意的翻译取舍（一次模型调用；只说明，不改写）'))
            button.setEnabled(not analyzing)
            button.clicked.connect(self.analyzeRequested.emit)
            self._layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
            self.analyze_button = button

    def _add_finding(self, item: dict) -> None:
        severity = str(item.get("severity") or "informational")
        summary = str(item.get("summary") or item.get("reason") or "")
        card = QWidget()
        card.setObjectName("attentionItem")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(theme_module.SPACE["sm"], theme_module.SPACE["xs"],
                                  theme_module.SPACE["sm"], theme_module.SPACE["xs"])
        layout.setSpacing(theme_module.SPACE["xs"])
        head = QHBoxLayout()
        head.setSpacing(theme_module.SPACE["xs"])
        tone = "danger" if severity == "blocking" else (
            "warning" if severity == "actionable" else "text-muted")
        head.addWidget(icons.IconLabel("alert" if severity != "informational" else "info",
                                       14, tone), 0, Qt.AlignmentFlag.AlignTop)
        label = _wrapped(summary, "attentionText")
        head.addWidget(label, 1)
        layout.addLayout(head)
        self._texts.append(summary)
        suggestion = str(item.get("suggested_target") or "").strip()
        if suggestion:
            layout.addWidget(_wrapped(i18n.tr('建议：{v0}', v0=suggestion), "hint"))
        decidable = bool(item.get("finding_id")) and item.get("decidable", True)
        if decidable:
            actions = QHBoxLayout()
            actions.setSpacing(theme_module.SPACE["xs"])
            actions.addStretch(1)
            keep = QPushButton(i18n.tr('保留译文'))
            style_module.set_button(keep, "plain")
            keep.setToolTip(i18n.tr('确认当前译文没有问题，记录为人工决定'))
            keep.clicked.connect(lambda _checked=False, data=dict(item):
                                 self.preserveRequested.emit(data))
            actions.addWidget(keep)
            if suggestion:
                accept = QPushButton(i18n.tr('采用建议'))
                accept.setToolTip(i18n.tr('把建议译文写入这一段'))
                accept.clicked.connect(lambda _checked=False, data=dict(item):
                                       self.acceptRequested.emit(data))
                actions.addWidget(accept)
            layout.addLayout(actions)
        card.setToolTip(i18n.tr(_SEVERITY_LABELS.get(severity, "提示")))
        self._layout.addWidget(card)

    def _add_decision(self, decision: dict) -> None:
        label = str(decision.get("label") or decision.get("decision_type") or "")
        reason = str(decision.get("reason") or "")
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(theme_module.SPACE["xs"])
        row.addWidget(icons.IconLabel("sparkles", 14, "text-muted"), 0,
                      Qt.AlignmentFlag.AlignTop)
        text = f"{label}：{reason}" if reason else label
        row.addWidget(_wrapped(text, "decisionText"), 1)
        column.addLayout(row)
        # 证据：原文与译文里对应的片段（逐字摘录，模型给不出准确片段的条目已被丢弃）。
        source_span = str(decision.get("source_span") or "")
        target_span = str(decision.get("target_span") or "")
        if source_span or target_span:
            evidence = " → ".join(part for part in (
                f"“{source_span}”" if source_span else "",
                f"“{target_span}”" if target_span else "") if part)
            locate = QPushButton(i18n.tr('标出 {v0}', v0=evidence))
            locate.setObjectName("termChip")
            locate.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            locate.setMinimumWidth(1)
            locate.setToolTip(i18n.tr('在原文和译文里标出对应位置'))
            locate.clicked.connect(lambda _checked=False, data=dict(decision):
                                   self.locateRequested.emit(data))
            column.addWidget(locate)
        self._layout.addWidget(holder)
        self._texts.append(text)


class TermChip(QFrame):
    """一条术语：原文 → **首选译名** · 本文 N 处，右边一个「插入」。

    悬停 = 当前行里这一对亮起来；点击 = 全文定位（网格高亮 + 滚动条标记 + 逐处跳转）；
    右侧 ↵ 或 ⌥点击 = 把首选译名插入到译文光标处（原先点击的行为）。
    """

    hovered = Signal(object)       # 进入时是术语 dict，离开时是 None
    activated = Signal(object)     # 点击：全文定位
    insertRequested = Signal(str)

    def __init__(self, term: dict, count: int | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.term = dict(term)
        self.setObjectName("termChip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(1)
        self.setProperty("active", "false")
        target = str(term.get("target") or "")
        source = QLabel(str(term.get("source") or ""))
        source.setObjectName("termSource")
        arrow = QLabel("→")
        arrow.setObjectName("termArrow")
        preferred = QLabel(target or "—")
        preferred.setObjectName("termTarget")
        self.count_label = QLabel(i18n.tr('本文 {v0} 处', v0=count) if count else "", self)
        self.count_label.setObjectName("termCount")
        self.count_label.setVisible(bool(count))
        self.insert_button = QToolButton()
        self.insert_button.setObjectName("termInsert")
        self.insert_button.setText("↵")
        self.insert_button.setToolTip(i18n.tr('插入「{v0}」到译文光标处（{v1}点击术语也可以）', v0=target, v1=platform_ui.modifier_text(Qt.KeyboardModifier.AltModifier)))
        self.insert_button.setAccessibleName(i18n.tr('插入译名'))
        self.insert_button.setEnabled(bool(target) and target != "—")
        self.insert_button.clicked.connect(lambda: self.insertRequested.emit(target))
        for label in (source, arrow, preferred, self.count_label):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(theme_module.SPACE["sm"], 3, 3, 3)
        row.setSpacing(theme_module.SPACE["xs"])
        row.addWidget(source)
        row.addWidget(arrow)
        row.addWidget(preferred)
        row.addStretch(1)
        row.addWidget(self.count_label)
        row.addWidget(self.insert_button)
        self.setToolTip(i18n.tr('点击：在全文中定位这个术语；悬停：在本段里标出它'))
        self.setAccessibleName(f"{term.get('source')} → {target}")

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        style_module.repolish(self)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.hovered.emit(self.term)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.hovered.emit(None)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
                event.position().toPoint()):
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.insert_button.click()
            else:
                self.activated.emit(self.term)
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.term)
            return
        super().keyPressEvent(event)


class TermList(QWidget):
    """本段命中的术语（见 :class:`TermChip`）。"""

    insertRequested = Signal(str)
    hovered = Signal(object)        # 术语 dict 或 None
    activated = Signal(object)      # 术语 dict：全文定位

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        self.buttons: list[TermChip] = []
        self.terms: list[dict] = []
        self._key = None

    def set_terms(self, terms: list[dict], counts: dict | None = None) -> None:
        terms = [dict(term) for term in terms or []]
        counts = counts or {}
        # 每秒的轮询都会用同样的术语再调一次：内容没变就什么都不做。以前每次都拆掉重建
        # 所有术语条，整个检查器重新排版、整窗重绘——后台的 Folith 在台前调度和菜单栏里
        # 一直闪（2026-10-16 试用反馈）。
        key = repr((terms, sorted((str(k), repr(v)) for k, v in counts.items()
                                  if any(str(t.get("source") or "") == str(k) for t in terms))))
        if key == self._key:
            return
        self._key = key
        _clear(self._layout)
        self.buttons = []
        self.terms = terms
        if not self.terms:
            self._layout.addWidget(_wrapped(i18n.tr('本段没有命中术语。'), "hint"))
            return
        for term in self.terms:
            chip = TermChip(term, counts.get(str(term.get("source") or "")))
            chip.insertRequested.connect(self.insertRequested.emit)
            chip.hovered.connect(self.hovered.emit)
            chip.activated.connect(self.activated.emit)
            self._layout.addWidget(chip)
            self.buttons.append(chip)
            for hit in term.get("forbidden_hits") or []:
                self._layout.addWidget(
                    _wrapped(i18n.tr('译文用了禁用译名「{v0}」', v0=hit), "forbiddenTerm"))
            if term.get("missing_preferred"):
                self._layout.addWidget(
                    _wrapped(i18n.tr('译文里没有用首选译名「{v0}」', v0=term.get("target")),
                             "termMissing"))

    def set_active(self, source: str) -> None:
        for chip in self.buttons:
            chip.set_active(bool(source) and chip.term.get("source") == source)


class _ContextRow(QWidget):
    """一行前后文：标题 + 原文 + 译文，各自按宽度省略，点一下跳过去。"""

    clicked = Signal()

    def __init__(self, title: str, source: str, target: str) -> None:
        super().__init__()
        self.setObjectName("contextRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["sm"], theme_module.SPACE["xs"],
                                  theme_module.SPACE["sm"], theme_module.SPACE["xs"])
        layout.setSpacing(0)
        heading = QLabel(title)
        heading.setObjectName("hint")
        layout.addWidget(heading)
        for text, name in ((source, "contextSource"), (target or "—", "contextTarget")):
            label = style_module.ElideLabel(text)
            label.setObjectName(name)
            layout.addWidget(label)
        self._text = f"{title}\n{source}\n{target}"

    def text(self) -> str:
        return self._text

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ContextView(QWidget):
    """前后文：上一段、下一段各一行（原文 / 译文）。点一下跳过去。"""

    jumpRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme_module.SPACE["xs"])
        self.rows: dict[str, _ContextRow] = {}
        self._key = None

    def set_context(self, context: dict) -> None:
        # 轮询每秒都会用同样的前后文再调一次：没变就不重建（重建会让检查器重排、整窗重绘）。
        key = repr({k: (v.get("index"), v.get("source"), v.get("target"))
                    for k, v in (context or {}).items()
                    if k in ("previous", "next") and isinstance(v, dict)})
        if key == self._key:
            return
        self._key = key
        _clear(self._layout)
        self.rows = {}
        for key, label in (("previous", i18n.tr('上一段')), ("next", i18n.tr('下一段'))):
            item = (context or {}).get(key)
            if not item:
                continue
            row = _ContextRow(f"{label} #{item['index'] + 1}", item["source"], item["target"])
            row.setToolTip(i18n.tr('跳到第 {v0} 段', v0=item["index"] + 1))
            row.clicked.connect(lambda index=item["index"]: self.jumpRequested.emit(index))
            self._layout.addWidget(row)
            self.rows[key] = row
        if not self.rows:
            self._layout.addWidget(_wrapped(i18n.tr('没有相邻段落。'), "hint"))


class AskAgentBox(QWidget):
    """询问 Agent：自由问答。只回答，不改写译文。"""

    askRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.input = QLineEdit()
        self.input.setPlaceholderText(i18n.tr('问这一段，如：为什么这样译'))
        self.input.returnPressed.connect(self._ask)
        self.button = QPushButton(i18n.tr('提问'))
        self.button.clicked.connect(self._ask)
        self.answer = _wrapped("", "agentAnswer")
        self.answer.setVisible(False)
        row = QHBoxLayout()
        row.setSpacing(theme_module.SPACE["xs"])
        row.addWidget(self.input, 1)
        row.addWidget(self.button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"])
        layout.addLayout(row)
        layout.addWidget(self.answer)

    def _ask(self) -> None:
        question = self.input.text().strip()
        if question:
            self.set_busy(True)
            self.askRequested.emit(question)

    def set_busy(self, busy: bool) -> None:
        self.button.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if busy:
            self.answer.setText(i18n.tr('正在询问…'))
            self.answer.setVisible(True)

    def set_answer(self, text: str) -> None:
        self.set_busy(False)
        self.answer.setText(text)
        self.answer.setVisible(bool(text))

    def reset(self) -> None:
        """换段时清空：上一段的回答不能留在下一段上。"""
        self.set_busy(False)
        self.input.clear()
        self.answer.clear()
        self.answer.setVisible(False)


class CandidateBox(QWidget):
    """待采用的重译候选：当前译文 vs 候选译文，采用 / 丢弃。

    生成候选不会改稿，所以这里必须把"当前译文"和"候选"并排摆出来，用户才能判断
    要不要采用。差异按字符高亮（新增绿、删除红），沿用审校页已有的颜色语义。
    采用是核心事务（带身份与基线校验），失败时按钮保持可用并把原因写在状态行。
    """

    adoptRequested = Signal(str)      # candidate_id
    discardRequested = Signal(str)    # candidate_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._candidate_id = ""
        self._candidate_target = ""
        self._key = None
        self.label = style_module.SectionLabel(i18n.tr('待采用候选'))
        self.meta = _wrapped("", "hint")
        self.current = _wrapped("", "candidateCurrent")
        self.proposal = QLabel("")
        self.proposal.setWordWrap(True)
        self.proposal.setTextFormat(Qt.TextFormat.RichText)
        self.proposal.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.proposal.setObjectName("candidateProposal")
        self.checks = _wrapped("", "candidateChecks")
        self.status = _wrapped("", "hint")
        self.status.setVisible(False)
        self.adopt_button = QPushButton(i18n.tr('采用候选'))
        self.adopt_button.setToolTip(
            i18n.tr('把候选译文写进这一段（会重新标记为待审校，并留下变更记录）'))
        self.adopt_button.clicked.connect(
            lambda: self.adoptRequested.emit(self._candidate_id))
        self.discard_button = QPushButton(i18n.tr('丢弃'))
        style_module.set_button(self.discard_button, "plain")
        self.discard_button.setToolTip(i18n.tr('关掉这个候选；当前译文保持不变'))
        self.discard_button.clicked.connect(
            lambda: self.discardRequested.emit(self._candidate_id))
        self._current_heading = QLabel(i18n.tr('当前译文'))
        self._current_heading.setObjectName("hint")
        actions = QHBoxLayout()
        actions.setSpacing(theme_module.SPACE["xs"])
        actions.addStretch(1)
        actions.addWidget(self.discard_button)
        actions.addWidget(self.adopt_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"])
        layout.addWidget(self.label)
        layout.addWidget(self.meta)
        layout.addWidget(self._current_heading)
        layout.addWidget(self.current)
        layout.addWidget(self.proposal)
        layout.addWidget(self.checks)
        layout.addWidget(self.status)
        layout.addLayout(actions)
        self.setVisible(False)

    def set_candidates(self, items: list[dict]) -> None:
        """Show the newest pending candidate; hide the box when there is none.

        Only an untouched rebuild is skipped: the block is rebuilt whenever the
        candidate or the current translation changed, because the diff depends on
        both and a stale diff is worse than no diff.
        """
        item = dict(items[0]) if items else {}
        key = repr((item.get("candidate_id"), item.get("status"),
                    item.get("candidate_target"), item.get("current_target"),
                    item.get("protection_labels")))
        if not item:
            # 没有候选时按钮不能是活的：点下去只会发出空 id，窗口只能静默忽略。
            self.adopt_button.setEnabled(False)
            self.discard_button.setEnabled(False)
        if key == self._key:
            self.setVisible(bool(item))
            return
        self._key = key
        self._candidate_id = str(item.get("candidate_id") or "")
        self._candidate_target = str(item.get("candidate_target") or "")
        if not item:
            self.setVisible(False)
            return
        self.status.setVisible(False)
        self.setVisible(True)
        origin = str(item.get("origin_label") or "")
        # 当前译文是人工处理过的：直接写在来源行里，采用前就能看到代价。
        protection = protection_text(item.get("protection"))
        meta = " · ".join(part for part in (
            origin,
            protection,
            str(item.get("model") or ""),
            str(item.get("generated_at") or "").replace("T", " ")[:16]) if part)
        self.meta.setText(meta)
        self.meta.setVisible(bool(meta))
        self.current.setText(str(item.get("current_target") or "") or "—")
        self.proposal.setText(self._diff_html(item.get("diff") or {},
                                              str(item.get("candidate_target") or "")))
        checks = item.get("checks") or {}
        warning = []
        if checks.get("blocking"):
            warning.append(i18n.tr('{v0} 项必须处理', v0=checks["blocking"]))
        if checks.get("actionable"):
            warning.append(i18n.tr('{v0} 项建议检查', v0=checks["actionable"]))
        self.checks.setText(i18n.tr('候选本身仍有：{v0}', v0="、".join(warning))
                            if warning else "")
        self.checks.setVisible(bool(warning))
        adoptable = bool(item.get("adoptable", True))
        self.adopt_button.setEnabled(adoptable)
        self.discard_button.setEnabled(True)
        if not adoptable:
            self.adopt_button.setToolTip(str(item.get("conflict_reason") or ""))
            self.set_status(str(item.get("conflict_reason") or ""), error=True)

    def candidate_text(self) -> str:
        """当前展示的候选原文（测试与无障碍读取用，不带高亮标记）。"""
        return self._candidate_target

    def candidate_id(self) -> str:
        return self._candidate_id

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setObjectName("candidateError" if error else "hint")
        self.status.setVisible(bool(text))
        style_module.repolish(self.status)

    def _diff_html(self, diff: dict, fallback: str) -> str:
        from html import escape

        chunks = diff.get("chunks") or []
        if not chunks:
            return escape(fallback) or "—"
        parts = []
        for chunk in chunks:
            text = escape(str(chunk.get("text") or ""))
            tag = str(chunk.get("tag") or "equal")
            if tag == "insert":
                parts.append(f'<span style="background:#1f7a4d;color:#ffffff">{text}</span>')
            elif tag == "delete":
                parts.append(f'<span style="background:#7a1f1f;color:#e8b4b4;'
                             f'text-decoration:line-through">{text}</span>')
            else:
                parts.append(text)
        return "".join(parts) or "—"

    def reset(self) -> None:
        self._key = None
        self._candidate_id = ""
        self._candidate_target = ""
        self._candidate_target = ""
        self.proposal.setText("")
        self.current.setText("")
        self.checks.setVisible(False)
        self.status.setVisible(False)
        self.setVisible(False)


class NoteBox(QWidget):
    """段落批注：留给自己或审校的一句话。不是译文，不进交付，不影响审校。"""

    saveRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QPlainTextEdit

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(i18n.tr('给这一段留个批注（只自己和审校看得到）'))
        self.edit.setFixedHeight(64)
        self.edit.textChanged.connect(self._changed)
        self.meta = _wrapped("", "hint")
        self.save_button = QPushButton(i18n.tr('保存批注'))
        self.save_button.clicked.connect(lambda: self.saveRequested.emit(self.edit.toPlainText()))
        self.save_button.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.meta, 1)
        row.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"])
        layout.addWidget(self.edit)
        layout.addLayout(row)
        self._saved = ""

    def set_note(self, note: dict | None) -> None:
        note = note or {}
        self._saved = str(note.get("text") or "")
        self.edit.blockSignals(True)
        self.edit.setPlainText(self._saved)
        self.edit.blockSignals(False)
        stamp = str(note.get("updated_at") or "")[:16].replace("T", " ")
        self.meta.setText(i18n.tr('更新于 {v0}', v0=stamp) if stamp else "")
        self.save_button.setEnabled(False)

    def _changed(self) -> None:
        self.save_button.setEnabled(self.edit.toPlainText().strip() != self._saved.strip())


__all__ = ["AskAgentBox", "AttentionList", "CandidateBox", "ContextView",
           "NoteBox", "TermList"]
