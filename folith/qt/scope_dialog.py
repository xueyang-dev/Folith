"""翻译范围选择（issue #28）：译前按页 / 章 / 节批量排除与恢复。

范围选择**不是**另一套排除真相：它把文档结构（页 / 章 / 节，来自 IR 区块与
标题启发式的只读投影）映射到既有排除账本（``excluded_segments``）的批量入口上。
对话框因此只做三件事——展示结构分组、把勾选变成范围键、把排除前后的统计摆在
用户面前；真正的状态变更全部走引擎的 :func:`core.exclude_translation_scope` /
:func:`core.restore_translation_scope`，与逐段排除共用同一条契约。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import style as style_module
from . import theme as theme_module


def _label(text: str = "", role: str = "body", *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    style_module.set_text_role(label, role)
    label.setWordWrap(wrap)
    return label


class ScopeDialog(QDialog):
    """按页 / 章 / 节选择翻译范围；被排除的段落可在同一对话框里恢复。"""

    excludeRequested = Signal(list)   # 范围键列表，如 ["page:0000", "chapter:0001"]
    restoreRequested = Signal(list)   # excluded_id 列表

    def __init__(self, job_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = str(job_id or "")
        self.scope = adapter.translation_scope(self.job_id)
        self._node_checks: dict[str, list] = {}   # key -> [checkbox, node]
        self._record_checks: dict[str, list] = {}  # excluded_id -> [checkbox, record]

        self.setObjectName("promptDialog")
        self.setWindowTitle(i18n.tr('翻译范围'))
        self.setMinimumWidth(560)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                  theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        layout.setSpacing(theme_module.SPACE["md"])

        layout.addWidget(_label(i18n.tr('翻译范围'), "card-title"))
        total = self.scope.get("total") or {}
        excluded = self.scope.get("excluded") or {}
        layout.addWidget(_label(
            i18n.tr('勾选不需要翻译的页、章或节：被排除的范围不产生模型调用、'
                    '不计入待完成与审校，原文完整保留、随时可恢复。当前 {v0} 段 / {v1} 字'
                    '（另有 {v2} 段已排除）。',
                    v0=total.get("segments", 0), v1=total.get("chars", 0),
                    v2=excluded.get("count", 0)),
            "meta", wrap=True))

        body = QVBoxLayout()
        body.setSpacing(theme_module.SPACE["sm"])
        if not self.scope.get("available"):
            body.addWidget(_label(
                i18n.tr('任务还没有可编辑的段落：等原文提取完成后再来选择范围。'),
                "meta", wrap=True))
        for group in self.scope.get("groups") or []:
            body.addWidget(self._build_group(group))
        if excluded.get("records"):
            body.addWidget(self._build_excluded(excluded["records"]))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("transparent")
        holder.setLayout(body)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)

        self.summary_label = _label("", "meta", wrap=True)
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.restore_button = QPushButton(i18n.tr('恢复所选段落'))
        style_module.set_button(self.restore_button, "secondary")
        self.restore_button.clicked.connect(self._restore_selected)
        buttons.addWidget(self.restore_button)
        self.cancel_button = QPushButton(i18n.tr('取消'))
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.exclude_button = QPushButton(i18n.tr('排除所选范围'))
        style_module.mark_primary(self.exclude_button)
        self.exclude_button.clicked.connect(self._exclude_selected)
        buttons.addWidget(self.exclude_button)
        layout.addLayout(buttons)

        self._sync()

    # ------------------------------------------------------------------ 构建
    def _build_group(self, group: dict) -> QWidget:
        card = style_module.Surface(elevation="flat")
        box = QVBoxLayout(card)
        box.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                               theme_module.SPACE["md"], theme_module.SPACE["sm"])
        box.setSpacing(theme_module.SPACE["xs"])
        header = QHBoxLayout()
        header.addWidget(_label(str(group.get("label") or ""), "name"))
        header.addStretch(1)
        group_checks: list = []
        clear = QPushButton(i18n.tr('清空选择'))
        style_module.set_button(clear, "plain")
        clear.clicked.connect(
            lambda _checked=False, checks=group_checks: self._set_checks(checks, False))
        header.addWidget(clear)
        select_all = QPushButton(i18n.tr('全选'))
        style_module.set_button(select_all, "plain")
        select_all.clicked.connect(
            lambda _checked=False, checks=group_checks: self._set_checks(checks, True))
        header.addWidget(select_all)
        box.addLayout(header)
        for node in group.get("nodes") or []:
            key = str(node.get("key") or "")
            text = i18n.tr('{v0}　·　第 {v1}–{v2} 段 · {v3} 段 / {v4} 字',
                           v0=node.get("label") or "—",
                           v1=(node.get("start") or 0) + 1, v2=node.get("end") or 0,
                           v3=node.get("segments", 0), v4=node.get("chars", 0))
            check = style_module.CheckBox(text)
            check.stateChanged.connect(self._sync)
            box.addWidget(check)
            self._node_checks[key] = [check, node]
            group_checks.append(check)
        return card

    def _build_excluded(self, records: list[dict]) -> QWidget:
        card = style_module.Surface(elevation="flat", fill="warning-soft")
        card.RADIUS = theme_module.RADIUS["sm"]
        box = QVBoxLayout(card)
        box.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                               theme_module.SPACE["md"], theme_module.SPACE["sm"])
        box.setSpacing(theme_module.SPACE["xs"])
        header = QHBoxLayout()
        header.addWidget(_label(i18n.tr('已排除的段落（勾选后可恢复）'), "name"))
        header.addStretch(1)
        record_checks: list = []
        clear = QPushButton(i18n.tr('清空选择'))
        style_module.set_button(clear, "plain")
        clear.clicked.connect(
            lambda _checked=False, checks=record_checks: self._set_checks(checks, False))
        header.addWidget(clear)
        box.addLayout(header)
        for record in records:
            excluded_id = str(record.get("excluded_id") or "")
            index = record.get("segment_index")
            where = i18n.tr('第 {v0} 段', v0=int(index) + 1) \
                if isinstance(index, int) else i18n.tr('原位置未知')
            source = " ".join(str(record.get("source") or "").split())[:60]
            reason = str(record.get("reason") or "")
            check = style_module.CheckBox(
                i18n.tr('{v0}　·　{v1}{v2}',
                        v0=where, v1=f"{source}　" if source else "",
                        v2=f"（{reason}）" if reason else ""))
            check.stateChanged.connect(self._sync)
            box.addWidget(check)
            self._record_checks[excluded_id] = [check, record]
            record_checks.append(check)
        return card

    # ------------------------------------------------------------------ 动作
    @staticmethod
    def _set_checks(checks: list, checked: bool) -> None:
        for check in checks:
            check.setChecked(checked)

    def selected_keys(self) -> list[str]:
        return [key for key, (check, _node) in self._node_checks.items()
                if check.isChecked()]

    def selected_restore_ids(self) -> list[str]:
        return [excluded_id for excluded_id, (check, _record)
                in self._record_checks.items() if check.isChecked()]

    def _exclude_selected(self) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        self.excludeRequested.emit(keys)
        self.accept()

    def _restore_selected(self) -> None:
        ids = self.selected_restore_ids()
        if not ids:
            return
        self.restoreRequested.emit(ids)
        self.accept()

    # ------------------------------------------------------------------ 状态
    def _sync(self, *_args) -> None:
        """统计跟勾选走：排除前后各多少段 / 字，让"会排除掉什么"先于按钮可见。"""
        keys = {key for key, (check, _node) in self._node_checks.items()
                if check.isChecked()}
        selected_segments = 0
        selected_chars = 0
        for key in keys:
            for group in self.scope.get("groups") or []:
                for node in group.get("nodes") or []:
                    if node.get("key") == key:
                        selected_segments += int(node.get("segments") or 0)
                        selected_chars += int(node.get("chars") or 0)
        total = self.scope.get("total") or {}
        restore_ids = self.selected_restore_ids()
        remaining_segments = max(0, int(total.get("segments") or 0) - selected_segments)
        if keys:
            self.summary_label.setText(i18n.tr(
                '将排除所选 {v0} 段 / {v1} 字；排除后剩余 {v2} 段 / {v3} 字。',
                v0=selected_segments, v1=selected_chars,
                v2=remaining_segments,
                v3=max(0, int(total.get("chars") or 0) - selected_chars)))
        elif restore_ids:
            self.summary_label.setText(i18n.tr(
                '将恢复所选的 {v0} 段；它们会回到工作台并重新计入待完成。',
                v0=len(restore_ids)))
        else:
            self.summary_label.setText(i18n.tr('尚未选择任何范围。'))
        self.exclude_button.setEnabled(bool(keys))
        self.restore_button.setEnabled(bool(restore_ids))
