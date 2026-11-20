"""The review auto-handle mode switch with visible state and feedback (#523).

Screenshot evidence: see docs/evidence/0.8.6-c523-review-header.md (full page).

The mode is a lasting per-task setting; a bare checkbox under the headline
made it look like a one-shot trigger.  This widget puts it in the top-right
control row, states the mode in words, keeps the one-line explanation visible,
and tells the user what happened right after the switch changes.  Behaviour,
storage and delivery gates stay where they were: callers only see the same
``toggled`` signal and ``isChecked``/``setChecked`` API.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from . import components, i18n, theme


class TriageModeRow(QFrame):
    """Compact switch + labelled state + explanation + one-line feedback."""

    toggled = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("triageModeRow")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE["xs"])

        row = QHBoxLayout()
        outer.addLayout(row)   # attach before filling (SIGSEGV guard, see qt_layout_item_wrapper_test)
        row.setSpacing(theme.SPACE["sm"])
        self.switch = components.Switch(self)
        self.switch.setObjectName("reviewTriageToggle")
        self.switch.setToolTip(i18n.tr(
            '只对本任务生效。有确定依据的发现会移入「自动处理（审计）」组，每条都能撤销；'
            '不修改译文，不调用模型，也不放宽交付门禁。'))
        self.switch.setAccessibleDescription(self.switch.toolTip())
        self.switch.toggled.connect(self.toggled.emit)

        text = QVBoxLayout()
        row.addLayout(text, 1)   # attach before filling
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        self.state = QLabel("")
        self.state.setObjectName("rowTitle")
        self.note = QLabel(i18n.tr('只对本任务生效 · 每条可撤销 · 不改译文'))
        self.note.setObjectName("hint")
        text.addWidget(self.state)
        text.addWidget(self.note)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

        self.feedback = QLabel("")
        self.feedback.setObjectName("hint")
        self.feedback.setVisible(False)
        outer.addWidget(self.feedback)
        self._sync_state(False)

    # -- the same API the review panel used on its QCheckBox ------------------
    def isChecked(self) -> bool:  # noqa: N802 - Qt 命名
        return self.switch.isChecked()

    def setChecked(self, value: bool) -> None:  # noqa: N802 - Qt 命名
        # Programmatic apply must not emit `toggled` (the old QCheckBox setChecked
        # didn't either); the review panel's no-write-back tests rely on this.
        self.switch.blockSignals(True)
        self.switch.setChecked(bool(value))
        self.switch.blockSignals(False)
        self._sync_state(bool(value))

    def click(self) -> None:  # noqa: N802 - Qt 命名
        self.switch.click()

    def isEnabled(self) -> bool:  # noqa: N802 - Qt 命名
        return self.switch.isEnabled()

    def setToolTip(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self.switch.setToolTip(text)

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt 命名
        self.switch.setEnabled(bool(value))

    def toolTip(self) -> str:  # noqa: N802 - Qt 命名
        return self.switch.toolTip()

    def setAccessibleDescription(self, text: str) -> None:  # noqa: N802 - Qt 命名
        self.switch.setAccessibleDescription(text)

    def _sync_state(self, on: bool) -> None:
        self.state.setText(i18n.tr('自动处理（实验）：{v0}',
                                   v0=i18n.tr('开') if on else i18n.tr('关')))

    def show_result(self, count: int) -> None:
        """The switch was turned on: say what happened, or why nothing did."""
        if count:
            text = i18n.tr('已自动处理 {v0} 条', v0=count)
        else:
            text = i18n.tr('当前没有可以按确定依据处理的问题')
        self.feedback.setText(text)
        self.feedback.setVisible(True)

    def show_disabled_notice(self) -> None:
        self.feedback.setText(i18n.tr('已关闭；此前自动处理的记录仍在「自动处理（审计）」组，可逐条撤销。'))
        self.feedback.setVisible(True)
