"""同一个专名在不同段译法不一：选一个统一，或保持原样——抽屉与审校页共用这一个部件。

每种观察到的译法一个单选项（「统一为「X」（改 N 段）」，下面是可点的段号），再加
「其他：[输入框]」与「保持原样（各自符合语境）」，一个「确认」按钮；选中统一类选项时
显示改动预览（逐段前后对照）。

只做展示与发信号：``previewRequested`` / ``repairRequested`` / ``acceptRequested``
沿用原来抽屉的信号与适配器路径，写状态的仍是窗口层调用的适配器。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import i18n
from . import style as style_module
from . import theme as theme_module

#: 选项种类。
VARIANT, CUSTOM, KEEP = "variant", "custom", "keep"


def _escape(text: object) -> str:
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def variant_groups(item: dict) -> list[dict]:
    """每种译法出现在哪几段：``[{"target", "indexes", "changes"}]``，出现多的在前。

    ``changes`` 是统一为这种译法时要改的段数（含有别的译法的段）。
    """
    by_variant: dict[str, list[int]] = {}
    rows = item.get("occurrences") or []
    for row in rows:
        index = int(row.get("index", -1))
        for value in row.get("variants") or []:
            key = str(value).strip()
            if key:
                indexes = by_variant.setdefault(key, [])
                if index not in indexes:
                    indexes.append(index)
    if not by_variant:
        for value in item.get("observed_targets") or []:
            by_variant.setdefault(str(value).strip(), [])
    groups = []
    for target, indexes in sorted(by_variant.items(), key=lambda pair: -len(pair[1])):
        if not target:
            continue
        changes = sum(
            1 for row in rows
            if any(str(v).strip().casefold() != target.casefold()
                   for v in row.get("variants") or []))
        groups.append({"target": target, "indexes": indexes, "changes": changes})
    return groups


def segment_links(indexes: list[int]) -> str:
    """「第 3、5 段」，段号可点（``seg:<索引>``）。"""
    shown = [index for index in indexes[:8] if index >= 0]
    if not shown:
        return ""
    links = "、".join(f"<a href='seg:{index}'>{index + 1}</a>" for index in shown)
    more = i18n.tr(' 等 {v0} 处', v0=len(indexes)) if len(indexes) > 8 else ""
    where = _escape(i18n.tr('第 {v0} 段', v0="\x00")).replace("\x00", links)
    return where + _escape(more)


class NameConflictWidget(QWidget):
    previewRequested = Signal(str, str)             # source, target
    repairRequested = Signal(str, str, list)        # source, target, preview
    acceptRequested = Signal(str)                   # source
    locateRequested = Signal(int)                   # 段索引

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item: dict = {}
        self._options: list[dict] = []
        self._preview: list[dict] = []
        self._requested: tuple[str, str] | None = None
        self._busy = False
        self._defer_preview = False
        self._preview_pending = False

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.idToggled.connect(self._selection_changed)
        self.options_layout = QVBoxLayout()
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(theme_module.SPACE["xs"])
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText(i18n.tr('统一使用的译名'))
        self.custom_edit.textChanged.connect(self._custom_changed)
        self.preview_hint = QLabel("")
        self.preview_hint.setObjectName("issueSummary")
        self.preview_hint.setWordWrap(True)
        self.preview_view = QTextBrowser()
        self.preview_view.setObjectName("repairPreview")
        self.preview_view.setFixedHeight(90)
        self.confirm_button = QPushButton(i18n.tr('确认'))
        style_module.set_button(self.confirm_button, "primary", small=True)
        self.confirm_button.clicked.connect(self._confirm)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addWidget(self.confirm_button)
        actions.addStretch(1)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        column.addLayout(self.options_layout)
        column.addWidget(self.preview_hint)
        column.addWidget(self.preview_view)
        column.addLayout(actions)
        self._rows: list[QWidget] = []

    # ================= 数据 =================
    def source(self) -> str:
        return str(self._item.get("source") or "")

    def options(self) -> list[dict]:
        return [dict(option) for option in self._options]

    def set_entity(self, item: dict, *, busy: bool = False) -> None:
        """换一个名字（或同一个名字数据刷新）：重建选项，默认选当前定名/最常见的译法。"""
        previous = self.selected_option()
        if str((item or {}).get("source") or "") != self.source():
            self.custom_edit.clear()
            previous = {}
        self._item = dict(item or {})
        self._busy = bool(busy)
        self.custom_edit.setParent(self)    # 不随旧的一行一起被删
        self.custom_edit.hide()
        for row in self._rows:
            self.options_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        for button in list(self.group.buttons()):
            self.group.removeButton(button)
        groups = variant_groups(self._item)
        self._options = [{"kind": VARIANT, **group} for group in groups]
        self._options.append({"kind": CUSTOM, "target": ""})
        self._options.append({"kind": KEEP, "target": ""})
        for position, option in enumerate(self._options):
            self.options_layout.addWidget(self._build_row(position, option))
        preferred = str(self._item.get("preferred_target") or "").strip().casefold()
        wanted = next((i for i, o in enumerate(self._options)
                       if o["kind"] == VARIANT and o["target"].casefold() == preferred), -1)
        if previous and previous["kind"] == VARIANT:
            kept = next((i for i, o in enumerate(self._options)
                         if o["kind"] == VARIANT and o["target"] == previous["target"]), -1)
            wanted = kept if kept >= 0 else wanted
        elif previous and previous["kind"] in (CUSTOM, KEEP):
            wanted = next(i for i, o in enumerate(self._options) if o["kind"] == previous["kind"])
        if wanted < 0:
            wanted = 0 if groups else len(self._options) - 2
        self.group.blockSignals(True)
        self.group.button(wanted).setChecked(True)
        self.group.blockSignals(False)
        self._selection_changed(wanted, True)

    def _build_row(self, position: int, option: dict) -> QWidget:
        kind = option["kind"]
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if kind == VARIANT:
            text = i18n.tr('统一为「{v0}」（改 {v1} 段）', v0=option["target"], v1=option["changes"])
        elif kind == CUSTOM:
            text = i18n.tr('其他：')
        else:
            text = i18n.tr('保持原样（各自符合语境）')
        radio = style_module.RadioButton(text)
        self.group.addButton(radio, position)
        if kind == CUSTOM:
            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(radio)
            line.addWidget(self.custom_edit, 1)
            self.custom_edit.show()
            layout.addLayout(line)
        else:
            layout.addWidget(radio)
        if kind == VARIANT and option["indexes"]:
            links = QLabel(segment_links(option["indexes"]))
            links.setTextFormat(Qt.TextFormat.RichText)
            links.setObjectName("issueEvidence")
            links.setWordWrap(True)
            links.setContentsMargins(theme_module.SPACE["lg"], 0, 0, 0)
            links.linkActivated.connect(self._link)
            layout.addWidget(links)
        elif kind == KEEP:
            note = QLabel(i18n.tr('确认这些译法在各自的段落里都是对的，不再提示。'))
            note.setProperty("role", "muted")
            note.setWordWrap(True)
            note.setContentsMargins(theme_module.SPACE["lg"], 0, 0, 0)
            layout.addWidget(note)
        self._rows.append(row)
        return row

    # ================= 选择 =================
    def selected_option(self) -> dict:
        position = self.group.checkedId()
        return dict(self._options[position]) if 0 <= position < len(self._options) else {}

    def select(self, kind: str, target: str = "") -> bool:
        """测试 / 调用方用：按种类（和译法）选中一项。"""
        for position, option in enumerate(self._options):
            if option["kind"] == kind and (kind != VARIANT or option["target"] == target):
                self.group.button(position).setChecked(True)
                if kind == CUSTOM:
                    self.custom_edit.setText(target)
                return True
        return False

    def selected_target(self) -> str:
        option = self.selected_option()
        if option.get("kind") == CUSTOM:
            return self.custom_edit.text().strip()
        if option.get("kind") == VARIANT:
            return str(option.get("target") or "")
        return ""

    def _selection_changed(self, _position: int, checked: bool = True) -> None:
        if not checked:
            return
        option = self.selected_option()
        self.custom_edit.setEnabled(option.get("kind") == CUSTOM)
        if option.get("kind") == CUSTOM:
            self.custom_edit.setFocus()
        self._request_preview()

    def _custom_changed(self, *_args) -> None:
        if self.selected_option().get("kind") == CUSTOM:
            self._request_preview()

    def _link(self, href: str) -> None:
        if href.startswith("seg:"):
            try:
                self.locateRequested.emit(int(href[4:]))
            except ValueError:
                pass

    # ================= 预览 =================
    def _request_preview(self) -> None:
        self.set_preview([])
        target = self.selected_target()
        window = self.window()
        if self._defer_preview or (window.isVisible() and not self.isVisible()):
            # 预览要扫整份文档（整本书约 0.2 秒），而这个部件在每次状态变化时都会重建，
            # 多数时候没人在看：等它真正显示出来再算。
            self._preview_pending = True
            return
        self._preview_pending = False
        if target and self.source():
            self._requested = (self.source(), target)
            self.previewRequested.emit(self.source(), target)

    def set_preview_deferred(self, deferred: bool) -> None:
        self._defer_preview = deferred
        if not deferred and self._preview_pending and self.isVisible():
            self._request_preview()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if self._preview_pending:
            self._request_preview()

    def set_preview(self, preview: list[dict], source: str | None = None,
                    target: str | None = None) -> None:
        """显示改动预览；给了 ``source``/``target`` 时只接受自己请求过的那一份。"""
        if source is not None and (source, str(target or "")) != self._requested \
                and preview:
            return
        self._preview = list(preview or [])
        tokens = theme_module.tokens(style_module.current_theme())
        rows = []
        for change in self._preview:
            rows.append(
                f"<p><b>{_escape(i18n.tr('第 {v0} 段', v0=int(change.get('index', 0)) + 1))}</b>"
                f"<br><span style='color:{tokens['danger']}'>− {_escape(change.get('before'))}</span>"
                f"<br><span style='color:{tokens['success']}'>+ {_escape(change.get('after'))}</span></p>")
        self.preview_view.setHtml("".join(rows))
        self.preview_hint.setText(
            i18n.tr('将修改 {v0} 段；确认后记录人工译名，并使受影响段落的审校和标注失效。',
                    v0=len(self._preview)) if self._preview else "")
        self.preview_hint.setVisible(bool(self.preview_hint.text()))
        self.preview_view.setVisible(bool(self._preview))
        if self._preview:
            # 预览框按内容高度（最多 180），不留一大块空白。
            document = self.preview_view.document()
            document.setTextWidth(max(120, self.preview_view.viewport().width()))
            self.preview_view.setFixedHeight(
                max(48, min(180, int(document.size().height())
                                  + 2 * self.preview_view.frameWidth() + 6)))
        self._sync_confirm()

    def _sync_confirm(self) -> None:
        kind = self.selected_option().get("kind")
        if kind == KEEP:
            ready = bool(self.source())
        else:
            ready = bool(self._preview)
        self.confirm_button.setEnabled(ready and not self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._sync_confirm()

    # ================= 确认 =================
    def _confirm(self) -> None:
        kind = self.selected_option().get("kind")
        if kind == KEEP:
            if self.source():
                self.acceptRequested.emit(self.source())
        elif self._preview:
            self.repairRequested.emit(self.source(), self.selected_target(), list(self._preview))
