"""准备页的「原文结构」区块（#27，只读）+ 译前范围入口（#28）。

左邻 :class:`folith.qt.doc_overview.DocumentOverview`（这份文档长什么样），这里是
**里面写了什么**：本地解析的章节/页/段落分组 + 原文预览。不调用模型、不做
OCR——扫描页如实显示"未识别"，绝不装作已读出。结构解析走
:mod:`folith.source_outline`，后台线程计算，按源文件身份缓存（与缩略图同一套
:class:`folith.qt.thumbnails.PreviewLoader` 机制，重复打开同源文件直接命中缓存）。

结构本身仍然只读；这里的「翻译范围…」按钮是 #28 在**开始翻译之前**的唯一入口：
打开的范围对话框按同一份大纲选择要排除的页/章/节，排除在第一次模型调用之前
生效（不产生付费翻译）。任务开始后范围入口回到工作台工具栏，按工作列表口径
选择。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMenu,
                               QListWidgetItem, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from . import adapter
from . import i18n
from . import style as style_module
from . import theme as theme_module
from .thumbnails import PreviewLoader
from .outline_loader import OutlineLoader, outline_loader

#: 预览最多显示的字符数：再长滚动也没人读，原文在工作台/PDF 面板里还能看全。
PREVIEW_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Legacy PreviewLoader-based loader (kept for tests that monkeypatch it).
# New code should use ``outline_loader()`` from ``folith.qt.outline_loader``.
# ---------------------------------------------------------------------------

_LOADER: PreviewLoader | None = None


def _render_outline(source: Path, filename: str) -> dict:
    """PreviewLoader 的 render：本地解析结构，读不出返回 ``None``。

    Still used by tests that monkeypatch the module-level ``loader``.
    The primary code path now uses :func:`outline_loader` instead.
    """
    from folith.source_outline import parse_source_outline

    outline = parse_source_outline(filename, Path(source).read_bytes())
    return outline or None


def loader() -> PreviewLoader:
    """进程内唯一的结构加载器（缓存跨页面刷新复用）。

    .. deprecated::
        New code should use :func:`folith.qt.outline_loader.outline_loader`.
        This function is kept for test compatibility.
    """
    global _LOADER
    if _LOADER is None:
        _LOADER = PreviewLoader(render=_render_outline)
    return _LOADER


def _count_label(node: dict) -> str:
    count = int(node.get("para_count") or 0)
    return i18n.tr('{count} 段', count=count) if count else i18n.tr('未识别')


def _page_label(node: dict) -> str:
    start, end = node.get("page_start"), node.get("page_end")
    if not isinstance(start, int) or not start:
        return ""
    if isinstance(end, int) and end > start:
        return i18n.tr('第 {start}–{end} 页', start=start, end=end)
    return i18n.tr('第 {page} 页', page=start)


def node_item_text(node: dict) -> str:
    """列表一行的文案：标题 · 页码 · 段数（扫描页如实写"未识别"）。"""
    parts = [str(node.get("title") or "")]
    provenance = str(node.get("provenance") or "")
    if provenance == "manual":
        parts.append(i18n.tr('手动修正'))
    elif provenance == "ai_inferred":
        parts.append(i18n.tr('AI 推断'))
    elif provenance.startswith("rule_"):
        parts.append(i18n.tr('按编号推断'))
    pages = _page_label(node)
    if pages:
        parts.append(pages)
    if node.get("scanned"):
        parts.append(i18n.tr('未识别'))
    else:
        parts.append(_count_label(node))
    return " · ".join(part for part in parts if part)


def node_item_tooltip(node: dict) -> str:
    if not node.get("scanned"):
        return ""
    return i18n.tr(
        '这个范围没有文字层，本地读不出内容；开始翻译后由视觉模型逐页识别。')


def node_preview_text(node: dict) -> str:
    """点选节点时的原文预览（只读；扫描页说清"还没读"）。"""
    if node.get("scanned"):
        return i18n.tr('【未识别】这个范围没有文字层，本地读不出内容。\n'
                       '开始翻译后由视觉模型逐页识别；在识别完成前这里不会显示原文，'
                       '也不会把它当成已识别内容。')
    paragraphs = [str(item or "") for item in node.get("paragraphs") or []]
    text = "\n\n".join(paragraphs)
    if len(text) > PREVIEW_MAX_CHARS:
        return text[:PREVIEW_MAX_CHARS] + i18n.tr(
            '\n\n……（预览到此为止，共 {count} 段；完整原文可在开始后的工作台查看）',
            count=int(node.get("para_count") or 0))
    return text or i18n.tr('（这个范围没有可显示的文本层内容）')


def outline_summary(outline: dict) -> str:
    """收起时的一行答案：{N 章/页/组} · {M 段} · 本地读取。"""
    nodes = outline.get("nodes") or []
    if not nodes:
        return ""
    if outline.get("scanned"):
        return i18n.tr('扫描件 · {pages} 页 · 本地读不出文字，开始后由视觉模型识别',
                       pages=int(outline.get("pages") or 0))
    chapters = sum(1 for node in nodes if node.get("kind") == "chapter")
    pages = sum(1 for node in nodes if node.get("kind") == "page")
    groups = len(nodes) - chapters - pages
    parts = [text for count, text in (
        (chapters, i18n.tr('{count} 章', count=chapters)),
        (pages, i18n.tr('{count} 页', count=pages)),
        (groups, i18n.tr('{count} 组', count=groups)),
    ) if count]
    parts.append(i18n.tr('{count} 段', count=int(outline.get("total_paragraphs") or 0)))
    if not any(node.get("provenance") == "ai_inferred" for node in nodes):
        parts.append(i18n.tr('本地读取，未调用模型'))
    return " · ".join(parts)


class SourceOutlineSection(QWidget):
    """「原文结构」：结构列表（只读）+ 原文预览 + 一行事实 + 译前范围入口。"""

    #: 打开翻译范围对话框（#28）。真正的选择与状态变更在 window/引擎里，
    #: 这里只发出意图——与准备页其它按钮（新建项目、术语库…）同一模式。
    scopeRequested = Signal()
    aiRecoveryRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .setup_panel import DisclosureSection  # 避免与 setup_panel 循环导入

        self._job_id = ""
        self._filename = ""
        self._view: dict = {}
        self._state: dict = {}
        self._outline: dict = {}
        self._base_outline: dict = {}
        self._correction_base: dict = {}
        self._pending_selection = None

        self.section = DisclosureSection(i18n.tr('原文结构'), icon="book-open")
        self.section.set_summary(i18n.tr('正在读取结构…'))
        body = self.section.body_layout
        body.setSpacing(theme_module.SPACE["sm"])

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        body.addWidget(self.status_label)

        self.node_list = QListWidget()
        self.node_list.setObjectName("sourceOutlineList")
        self.node_list.setMinimumHeight(180)
        self.node_list.setUniformItemSizes(True)
        self.node_list.currentItemChanged.connect(self._show_preview)
        body.addWidget(self.node_list)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName("sourceOutlinePreview")
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            i18n.tr('选择上面一项，在这里看那部分原文（本地读取，未调用模型）。'))
        self.preview.setMinimumHeight(120)
        body.addWidget(self.preview)

        # 译前范围入口（#28）：任务开始前唯一的范围选择入口——排除在第一次
        # 模型调用之前生效。任务开始后这里随整个区块停用，入口回到工作台工具栏。
        scope_row = QHBoxLayout()
        scope_row.setSpacing(theme_module.SPACE["sm"])
        self.scope_button = QPushButton(i18n.tr('翻译范围…'))
        style_module.set_button(self.scope_button, "secondary")
        self.scope_button.setToolTip(i18n.tr(
            '开始翻译前按页 / 章 / 节排除不需要翻译的内容：被排除的范围不产生'
            '模型调用，原文保留、开始后也可以恢复。'))
        self.scope_button.clicked.connect(self.scopeRequested.emit)
        scope_row.addWidget(self.scope_button)
        edit = QToolButton()
        edit.setObjectName("toolbarButton")
        edit.setText(i18n.tr('修正'))
        edit.setAccessibleName(i18n.tr('修正文档结构'))
        menu = QMenu(edit)
        for label, operation in [('重命名', 'rename'), ('修改层级', 'level'),
                                 ('移动起始段落', 'boundary'), ('删除节点', 'delete'),
                                 ('恢复原始结构', 'restore')]:
            action = menu.addAction(i18n.tr(label))
            action.triggered.connect(lambda _checked=False, op=operation: self._edit_outline(op))
        edit.setMenu(menu)
        edit.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        scope_row.addWidget(edit)
        self.edit_button = edit
        recovery = QToolButton()
        recovery.setObjectName("toolbarButton")
        recovery.setText(i18n.tr('恢复结构'))
        recovery_menu = QMenu(recovery)
        recovery_menu.addAction(i18n.tr('按编号恢复')).triggered.connect(self._recover_outline)
        recovery_menu.addAction(i18n.tr('使用 AI 分析候选标题')).triggered.connect(self._request_ai)
        recovery_menu.addAction(i18n.tr('撤销结构恢复')).triggered.connect(self._reject_recovery)
        recovery.setMenu(recovery_menu)
        recovery.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        scope_row.addWidget(recovery)
        self.recovery_button = recovery
        scope_row.addStretch(1)
        body.addLayout(scope_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.section)

        loader().ready.connect(self._outline_ready)
        outline_loader().ready.connect(self._outline_ready)

    # ---- 数据 ---------------------------------------------------------------
    def load(self, job_id: str, filename: str, view: dict,
             state: dict | None = None) -> None:
        """按任务载入：请求结构解析（只读，不动任务状态）。

        *state* is the raw task-state dict.  It is forwarded to
        :class:`folith.qt.outline_loader.OutlineLoader` so the background parse
        can use it without a second ``load_state`` call.
        """
        self._pending_selection = (self._selection_identity() or self._pending_selection
                                   if str(job_id or "") == self._job_id else None)
        self._job_id = str(job_id or "")
        self._filename = str(filename or "")
        self._view = dict(view or {})
        self._state = dict(state or {})
        self.recovery_button.setEnabled(True)
        self.edit_button.setEnabled(True)
        self._outline = {}
        self._base_outline = {}
        self._correction_base = {}
        self.node_list.clear()
        self.preview.setPlainText("")
        started = bool(self._view.get("started"))
        self.setEnabled(not started)
        if started:
            self.section.set_summary("")
            self._set_status(i18n.tr('任务已开始；这里只用于开始前查看结构，'
                                     '原文对照请在工作台打开。'))
            return
        self._set_status("")
        self.section.set_summary(i18n.tr('正在读取结构…'))
        source = adapter.source_path(self._job_id)
        if source is None:
            # 源文件不在盘上（例如导入中断）：没有结构可看，也别永远停在"读取中"。
            self.section.set_summary(i18n.tr('没有可解析的结构'))
            self._set_status(i18n.tr('本地找不到这份任务的源文件；不影响已有译文。'))
            self.scope_button.setEnabled(False)
            return
        # Scope controls stay disabled until the outline arrives so the user
        # cannot open the scope dialog against an empty outline (#28).
        self.scope_button.setEnabled(False)
        # Try the shared OutlineLoader first (prefers the sidecar cache).
        payload = outline_loader().request(self._job_id, self._state)
        if payload is not None and not payload.get("pending"):
            # payload["outline"] is the native outline from outline_anchor;
            # _apply_outline will apply corrections on top of it.
            self._apply_outline(payload["outline"])
            self.scope_button.setEnabled(True)
            return
        if outline_loader().is_pending(self._job_id):
            # OutlineLoader already queued a parse; don't also trigger the
            # legacy PreviewLoader (that would run a second concurrent parse).
            # Wait for outline_loader.ready → _outline_ready.
            return
        # Fall back to the legacy PreviewLoader (kept for test compat — tests
        # monkeypatch loader() to avoid touching the real sidecar cache).
        outline = loader().request(self._job_id, source, self._filename, priority=10)
        if outline is not None:
            self._apply_outline(outline)
            self.scope_button.setEnabled(True)
            return
        # 否则等 outline_loader.ready 或 loader.ready（_outline_ready）。

    def _outline_ready(self, job_id: str) -> None:
        if not job_id or job_id != self._job_id:
            return
        # Try the shared OutlineLoader first.
        payload = outline_loader().request(self._job_id, self._state)
        if payload is not None and not payload.get("pending"):
            # payload["outline"] is the native outline; apply corrections here.
            self._apply_outline(payload["outline"])
            self.scope_button.setEnabled(True)
            return
        # Fallback: legacy PreviewLoader.
        source = adapter.source_path(self._job_id)
        outline = loader().request(self._job_id, source, self._filename, priority=10)
        if outline is not None:
            self._apply_outline(outline)
            self.scope_button.setEnabled(True)
        elif not loader().pending(self._job_id) and not outline_loader().is_pending(self._job_id):
            self.section.set_summary(i18n.tr('没有可解析的结构'))
            self._set_status(i18n.tr('这个文件本地读不出结构；不影响导入与翻译。'))

    def _apply_outline(self, outline: dict) -> None:
        from folith import outline_corrections, outline_recovery
        selection = self._selection_identity() or self._pending_selection
        self._pending_selection = None
        self._base_outline = dict(outline or {})
        self._correction_base = outline_recovery.load(self._job_id, self._base_outline)
        self._outline, dropped = outline_corrections.apply(self._job_id, self._correction_base)
        self.node_list.clear()
        for node in self._outline.get("nodes") or []:
            item = QListWidgetItem(node_item_text(node))
            # 只读列表：可选中（选中即预览），不可编辑、没有勾选位。
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(Qt.ItemDataRole.UserRole, str(node.get("id") or ""))
            tip = node_item_tooltip(node)
            if tip:
                item.setToolTip(tip)
            self.node_list.addItem(item)
        summary = outline_summary(self._outline)
        provenances = {n.get("provenance") for n in self._outline.get("nodes") or []}
        if "ai_inferred" in provenances:
            summary += ' · ' + i18n.tr('AI 推断')
        elif any(str(p or "").startswith('rule_') for p in provenances):
            summary += ' · ' + i18n.tr('按编号推断')
        elif any(str(p or "").startswith('native_') for p in provenances):
            summary += ' · ' + i18n.tr('文档原有结构')
        self.section.set_summary(summary or i18n.tr('没有可解析的结构'))
        self._set_status(i18n.tr('无法读取结构修正文件；原文件已保留。')
                         if dropped == outline_corrections.UNREADABLE else
                         i18n.tr('有 {count} 项结构修正无法匹配。', count=dropped)
                         if dropped else ("" if summary else i18n.tr(
            '这个文件本地读不出结构；不影响导入与翻译。')))
        if self.node_list.count():
            row = next((index for index, node in enumerate(self._outline.get("nodes") or [])
                        if self._node_identity(node) == selection), 0)
            self.node_list.setCurrentRow(row)

    @staticmethod
    def _node_identity(node: dict) -> tuple:
        # IDs are positional: require the original title and boundary too so a
        # changed outline cannot silently select an unrelated node with the same ID.
        return (str(node.get("id") or ""), str(node.get("title") or ""),
                node.get("para_start"))

    def _selection_identity(self) -> tuple | None:
        node = self._current_node()
        return self._node_identity(node) if node else None

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text or ""))
        self.status_label.setVisible(bool(text))

    # ---- 交互 ---------------------------------------------------------------
    def _edit_outline(self, operation: str) -> None:
        from . import outline_edit
        node = self._current_node() or {}
        if outline_edit.edit(self, self._job_id, self._correction_base, self._outline,
                             operation, str(node.get("id") or "")):
            self._apply_outline(self._base_outline)

    def _recover_outline(self) -> None:
        from folith import outline_corrections, outline_recovery
        if outline_corrections.has_corrections(self._job_id):
            self._set_status(i18n.tr('已有手动修正，请先恢复原始结构再分析。'))
            return
        recovered = outline_recovery.recover(self._base_outline)
        if recovered != self._base_outline:
            try:
                outline_recovery.save(self._job_id, self._base_outline, recovered,
                    rejected_ai=outline_recovery.ai_rejected(self._job_id, self._base_outline))
            except OSError:
                self._set_status(i18n.tr('无法保存结构恢复结果。'))
                return
            self._apply_outline(self._base_outline)
        else:
            self._set_status(i18n.tr('没有可恢复的编号结构。'))

    def _reject_recovery(self) -> None:
        from folith import outline_corrections, outline_recovery
        if outline_corrections.has_corrections(self._job_id):
            self._set_status(i18n.tr('已有手动修正，请先恢复原始结构再分析。'))
            return
        try:
            outline_recovery.reject(self._job_id, self._base_outline)
        except OSError:
            self._set_status(i18n.tr('无法保存结构恢复结果。'))
            return
        self._apply_outline(self._base_outline)

    def _request_ai(self) -> None:
        if not self._base_outline:
            return
        self.recovery_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self._set_status(i18n.tr('正在分析候选标题（最多一次请求）。'))
        self.aiRecoveryRequested.emit(str(self._base_outline.get("fingerprint") or ""))

    def ai_recovery_finished(self, error: str = "") -> None:
        self.recovery_button.setEnabled(True)
        self.edit_button.setEnabled(True)
        self._apply_outline(self._base_outline)
        if error:
            self._set_status(error)

    def _show_preview(self, current: QListWidgetItem | None,
                      _previous: QListWidgetItem | None = None) -> None:
        node = self._current_node(current)
        self.preview.setPlainText(node_preview_text(node) if node else "")

    def _current_node(self, item: QListWidgetItem | None = None) -> dict | None:
        item = item or self.node_list.currentItem()
        node_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if not node_id:
            return None
        for node in self._outline.get("nodes") or []:
            if str(node.get("id") or "") == node_id:
                return node
        return None

    # ---- 给测试的只读出口 ----------------------------------------------------
    def outline(self) -> dict:
        return dict(self._outline)

