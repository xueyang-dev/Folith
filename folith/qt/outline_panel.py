"""Document outline navigation panel for the translation workspace (#187).

The panel displays the persisted source hierarchy (#185/#186) and resolves a
click to the first segment of that node.  It is deliberately small:

* children are created on first expand, never one widget per segment;
* a node is navigable only when the resolver says ``resolved``/``partial``;
  ``structure_edited`` and ``unresolved`` are shown as such, not guessed at;
* selecting in the grid highlights the owning node, and highlighting a node
  never touches the grid, the editor or the task state.

Indentation stops at level 6.  Deeper nodes keep the level-6 indent (they become
siblings of the node that reached level 6) and the full real path is always in
the tooltip.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QMenu,
                               QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import i18n
from . import icons
from . import theme as theme_module

NODE_ROLE = Qt.ItemDataRole.UserRole
#: Deeper nodes keep this indent (and their level-6 parent) but not a new level.
MAX_INDENT_LEVEL = 6
PATH_SEPARATOR = " › "

#: state/reason → (short suffix shown on the row, tooltip explanation)
_REASON_LABELS = {
    "partial_paragraph_match": (
        '部分定位',
        '部分原文段落没有和当前文本对上；点击会跳到已定位的第一段。'),
    "structure_edited": (
        '结构已变化',
        '译文结构已经编辑过，不能按旧位置跳转；请按原文重新定位。'),
    "no_paragraph_match": (
        '未能定位',
        '这个范围的原文段落没有和当前文本对上。'),
    "no_paragraph_text": (
        '没有可定位的原文',
        '这个范围没有可读取的原文段落。'),
    "cleanup_lineage_missing": (
        '未能定位',
        '原文整理没有留下段落对应关系，无法安全定位。'),
    "node_excluded": (
        '已全部排除',
        '这个范围内的段落已经被排除，不参与翻译与交付。'),
    "node_partial_excluded": (
        '部分已排除',
        '这个范围的一部分段落已被排除；点击会跳到仍在翻译的第一段。'),
    "no_segment_match": (
        '未能定位',
        '这个范围没有对应的翻译段落。'),
    "segmentation_missing": (
        '未能定位',
        '任务还没有分段信息。'),
    "positional_fallback": (
        '按位置定位',
        '旧任务没有稳定段身份，按当前段落位置定位。'),
}


def reason_text(reason: str) -> tuple[str, str]:
    key = str(reason or "")
    if key in _REASON_LABELS:
        suffix, detail = _REASON_LABELS[key]
        return i18n.tr(suffix), i18n.tr(detail)
    return '', ''


def node_tooltip(node: dict, entry: dict, path: str) -> str:
    """Full path + resolver state for a tree node."""
    lines = [path or str(node.get('title') or '')]
    if node.get("provenance") == "ai_inferred" and isinstance(node.get("confidence"), (int, float)):
        lines.append(i18n.tr('AI 置信度：{value}', value=f"{node['confidence']:.0%}"))
    state = str((entry or {}).get("state") or "")
    if state == "partial":
        lines.append(i18n.tr('部分定位：{v0}', v0=i18n.tr(
            '部分原文段落没有和当前文本对上；点击会跳到已定位的第一段。')))
    elif state == "unresolved":
        suffix, detail = reason_text((entry or {}).get("reason") or "")
        lines.append(i18n.tr('不可跳转：{v0}', v0=suffix or i18n.tr('未能定位')))
        if detail:
            lines.append(detail)
    else:
        reason = str((entry or {}).get("reason") or "")
        if reason == "positional_fallback":
            _, detail = reason_text(reason)
            lines.append(detail)
        if int(node.get("para_count") or 0):
            lines.append(i18n.tr('{count} 段', count=int(node.get("para_count") or 0)))
    return "\n".join(line for line in lines if line)


class OutlinePanel(QWidget):
    """Left-column outline: hierarchy, explicit states, one navigation signal."""

    nodeActivated = Signal(str)
    closeRequested = Signal()
    correctionRequested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._nodes: dict[str, dict] = {}
        self._mapping: dict[str, dict] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._children: dict[str, list[str]] = {}
        self._capped_parent: dict[str, str] = {}
        self._paths: dict[str, str] = {}
        self._pending: set[str] = set()
        self._outline_fingerprint = ""

        title = QLabel(i18n.tr('文档结构'))
        title.setObjectName("sectionTitle")
        close = QToolButton()
        close.setObjectName("roundButton")
        close.setIcon(icons.icon("x", 16, "text-secondary"))
        close.setIconSize(QSize(16, 16))
        close.setToolTip(i18n.tr('收起文档结构'))
        close.setAccessibleName(i18n.tr('收起文档结构'))
        close.clicked.connect(self.closeRequested.emit)
        self.close_button = close

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(title, 1)
        edit = QToolButton()
        edit.setObjectName("toolbarButton")
        edit.setText(i18n.tr('修正'))
        edit.setAccessibleName(i18n.tr('修正文档结构'))
        menu = QMenu(edit)
        for label, operation in [('重命名', 'rename'), ('修改层级', 'level'),
                                 ('移动起始段落', 'boundary'), ('删除节点', 'delete'),
                                 ('恢复原始结构', 'restore')]:
            action = menu.addAction(i18n.tr(label))
            action.triggered.connect(lambda _checked=False, op=operation: self._request_correction(op))
        edit.setMenu(menu)
        edit.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        head.addWidget(edit)
        head.addWidget(close)

        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        self.summary.setVisible(False)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        self.empty = QLabel(i18n.tr('这份文档没有可显示的结构。'))
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        self.empty.setVisible(False)

        self.tree = QTreeWidget()
        self.tree.setObjectName("outlineTree")
        self.tree.setAccessibleName(i18n.tr('文档结构'))
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setAnimated(False)
        self.tree.itemExpanded.connect(self._expanded)
        self.tree.itemClicked.connect(self._activated)
        self.tree.itemActivated.connect(self._activated)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(theme_module.SPACE["xs"])
        layout.addLayout(head)
        layout.addWidget(self.summary)
        layout.addWidget(self.status)
        layout.addWidget(self.empty)
        layout.addWidget(self.tree, 1)

    # ---- data ---------------------------------------------------------
    def set_outline(self, outline: dict, mapping: dict | None = None, *,
                    invalidated: str = "", corrections_dropped: int = 0) -> None:
        """Show an outline and its resolved mapping; rebuild only on a new one."""
        outline = outline if isinstance(outline, dict) else {}
        nodes = [node for node in outline.get("nodes") or []
                 if isinstance(node, dict) and str(node.get("id") or "")]
        self._mapping = ((mapping or {}).get("nodes")
                         if isinstance(mapping, dict) else {}) or {}
        fingerprint = str(outline.get("fingerprint") or "")
        if not nodes:
            self.clear()
            self.empty.setVisible(True)
            if corrections_dropped:
                self.status.setText(i18n.tr('有 {count} 项结构修正无法匹配。', count=corrections_dropped))
                self.status.setVisible(True)
            return
        if not self._items or fingerprint != self._outline_fingerprint:
            self._rebuild(outline, nodes)
            self._outline_fingerprint = fingerprint
        else:
            self._refresh_items()
        self.empty.setVisible(False)
        provenance = {str(node.get("provenance") or "") for node in nodes}
        if "ai_inferred" in provenance:
            summary = i18n.tr('{count} 个节点 · AI 推断', count=len(nodes))
        elif any(item.startswith("rule_") for item in provenance):
            summary = i18n.tr('{count} 个节点 · 按编号推断', count=len(nodes))
        else:
            summary = i18n.tr('{count} 个节点 · 本地读取', count=len(nodes))
        self.summary.setText(summary)
        self.summary.setVisible(True)
        status = ""
        # A first read ("missing") is normal, not a warning: the sidecar is
        # written by the open worker and nothing the user had was replaced.
        if invalidated == "parser_changed":
            status = i18n.tr('解析器已更新，结构已重新读取。')
        elif invalidated == "source_changed":
            status = i18n.tr('原文已变化，结构已重新读取。')
        if corrections_dropped:
            status = (status + ' ' if status else '') + i18n.tr(
                '有 {count} 项结构修正无法匹配。', count=corrections_dropped)
        self.status.setText(status)
        self.status.setVisible(bool(status))

    def clear(self) -> None:
        self.tree.clear()
        self._nodes = {}
        self._mapping = {}
        self._items = {}
        self._children = {}
        self._capped_parent = {}
        self._paths = {}
        self._pending = set()
        self._outline_fingerprint = ""
        self.summary.clear()
        self.summary.setVisible(False)
        self.status.clear()
        self.status.setVisible(False)
        self.empty.setVisible(False)

    def navigable(self, node_id: str) -> bool:
        entry = self._mapping.get(str(node_id)) or {}
        return (entry.get("state") in ("resolved", "partial")
                and isinstance(entry.get("first_segment_index"), int))

    def activate_node(self, node_id: str) -> bool:
        """Activate a node; returns whether it actually navigates.

        Unresolved nodes still emit: the workspace has to say *why* nothing
        moved instead of swallowing the click (``clear filter`` / notice).
        """
        node_id = str(node_id or "")
        if node_id not in self._nodes:
            return False
        self.nodeActivated.emit(node_id)
        return self.navigable(node_id)

    def item_for(self, node_id: str) -> QTreeWidgetItem | None:
        return self._items.get(str(node_id))

    def node_ids(self) -> list[str]:
        return list(self._nodes)

    # ---- tree construction -------------------------------------------
    def _rebuild(self, outline: dict, nodes: list[dict]) -> None:
        self.tree.clear()
        self._items = {}
        self._pending = set()
        self._nodes = {str(node["id"]): node for node in nodes}
        depth: dict[str, int] = {}
        self._capped_parent = {}
        self._paths = {}
        self._children = {}
        for node in nodes:
            node_id = str(node["id"])
            parent_id = str(node.get("parent_id") or "")
            if parent_id not in self._nodes:
                parent_id = ""
            depth[node_id] = (depth.get(parent_id, 0) + 1) if parent_id else 1
            if parent_id and depth.get(parent_id, 0) >= MAX_INDENT_LEVEL:
                capped = self._capped_parent.get(parent_id, parent_id)
            else:
                capped = parent_id
            self._capped_parent[node_id] = capped
            # The tooltip keeps the *real* ancestry even when the indent stops.
            self._paths[node_id] = (
                f"{self._paths.get(parent_id, '')}{PATH_SEPARATOR}"
                f"{node.get('title') or ''}" if parent_id
                else str(node.get("title") or ""))
            self._children.setdefault(capped, []).append(node_id)
        for node_id in self._children.get("", []):
            self.tree.addTopLevelItem(self._create_item(node_id))

    def _create_item(self, node_id: str) -> QTreeWidgetItem:
        node = self._nodes[node_id]
        item = QTreeWidgetItem([""])
        item.setData(0, NODE_ROLE, node_id)
        self._items[node_id] = item
        self._apply_style(item)
        if self._children.get(node_id):
            item.addChild(QTreeWidgetItem([""]))
            self._pending.add(node_id)
        return item

    def _materialize(self, node_id: str) -> None:
        item = self._items.get(node_id)
        if item is None or node_id not in self._pending:
            return
        item.takeChildren()
        self._pending.discard(node_id)
        for child_id in self._children.get(node_id, []):
            item.addChild(self._create_item(child_id))

    def _expanded(self, item: QTreeWidgetItem) -> None:
        node_id = str(item.data(0, NODE_ROLE) or "")
        if node_id:
            self._materialize(node_id)

    def _apply_style(self, item: QTreeWidgetItem) -> None:
        node_id = str(item.data(0, NODE_ROLE) or "")
        node = self._nodes.get(node_id) or {}
        entry = self._mapping.get(node_id) or {}
        state = str(entry.get("state") or "")
        suffix, _ = reason_text(entry.get("reason") or "")
        title = str(node.get("title") or "")
        if node.get("provenance") == "manual":
            title = f"{title}{PATH_SEPARATOR}{i18n.tr('手动修正')}"
        elif node.get("provenance") == "ai_inferred":
            title = f"{title}{PATH_SEPARATOR}{i18n.tr('AI 推断')}"
        elif str(node.get("provenance") or "").startswith("rule_"):
            title = f"{title}{PATH_SEPARATOR}{i18n.tr('按编号推断')}"
        if state == "partial" or state == "unresolved":
            title = f"{title}{PATH_SEPARATOR}{suffix}" if suffix else title
        item.setText(0, title)
        item.setToolTip(0, node_tooltip(node, entry, self._paths.get(node_id, "")))
        # Always enabled: an unresolved *parent* may still have resolvable
        # children, and the click has to reach the workspace to get a notice.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled
                      | Qt.ItemFlag.ItemIsSelectable)

    def _refresh_items(self) -> None:
        for item in self._items.values():
            self._apply_style(item)

    # ---- selection / navigation ---------------------------------------
    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        node_id = str(item.data(0, NODE_ROLE) or "")
        if node_id:
            self.activate_node(node_id)

    def _request_correction(self, operation: str) -> None:
        item = self.tree.currentItem()
        node_id = str(item.data(0, NODE_ROLE) or "") if item else ""
        if operation == "restore" or node_id:
            self.correctionRequested.emit(operation, node_id)

    def highlight_node(self, node_id: str) -> None:
        """Select a node and expand its ancestors without emitting navigation."""
        node_id = str(node_id or "")
        if not node_id or node_id not in self._nodes:
            return
        chain: list[str] = []
        current = node_id
        while current:
            chain.append(current)
            current = self._capped_parent.get(current, "")
        for ancestor in reversed(chain[1:]):
            self._materialize(ancestor)
            item = self._items.get(ancestor)
            if item is not None and not item.isExpanded():
                item.setExpanded(True)
        item = self._items.get(node_id)
        if item is None:
            return
        blocked = self.tree.blockSignals(True)
        try:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(
                item, QAbstractItemView.ScrollHint.PositionAtCenter)
        finally:
            self.tree.blockSignals(blocked)
