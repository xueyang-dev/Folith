"""准备页的「原文结构」区块（#27，只读）。

左邻 :class:`folith.qt.doc_overview.DocumentOverview`（这份文档长什么样），这里是
**里面写了什么**：本地解析的章节/页/段落分组 + 原文预览。不调用模型、不做
OCR——扫描页如实显示"未识别"，绝不装作已读出。结构解析走
:mod:`folith.source_outline`，后台线程计算，按源文件身份缓存（与缩略图同一套
:class:`folith.qt.thumbnails.PreviewLoader` 机制，重复打开同源文件直接命中缓存）。

这里只读不改：不排除段落、不改翻译状态——开始翻译后要按范围排除，去翻译范围
入口（#28）；要在原位置对照，去工作台的原文上下文（#61）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QVBoxLayout, QWidget)

from . import adapter
from . import i18n
from . import theme as theme_module
from .thumbnails import PreviewLoader

#: 预览最多显示的字符数：再长滚动也没人读，原文在工作台/PDF 面板里还能看全。
PREVIEW_MAX_CHARS = 4000

_LOADER: PreviewLoader | None = None


def _render_outline(source: Path, filename: str) -> dict:
    """PreviewLoader 的 render：本地解析结构，读不出返回 ``None``。"""
    from folith.source_outline import parse_source_outline

    outline = parse_source_outline(filename, Path(source).read_bytes())
    return outline or None


def loader() -> PreviewLoader:
    """进程内唯一的结构加载器（缓存跨页面刷新复用）。"""
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
    parts.append(i18n.tr('本地读取，未调用模型'))
    return " · ".join(parts)


class SourceOutlineSection(QWidget):
    """「原文结构」：结构列表（只读）+ 原文预览 + 一行事实。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from .setup_panel import DisclosureSection  # 避免与 setup_panel 循环导入

        self._job_id = ""
        self._filename = ""
        self._view: dict = {}
        self._outline: dict = {}

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.section)

        loader().ready.connect(self._outline_ready)

    # ---- 数据 ---------------------------------------------------------------
    def load(self, job_id: str, filename: str, view: dict) -> None:
        """按任务载入：请求结构解析（只读，不动任务状态）。"""
        self._job_id = str(job_id or "")
        self._filename = str(filename or "")
        self._view = dict(view or {})
        self._outline = {}
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
            return
        outline = loader().request(self._job_id, source, self._filename, priority=10)
        if outline is not None:
            self._apply_outline(outline)
        # 否则等 loader.ready（_outline_ready）。

    def _outline_ready(self, job_id: str) -> None:
        if not job_id or job_id != self._job_id:
            return
        source = adapter.source_path(self._job_id)
        outline = loader().request(self._job_id, source, self._filename, priority=10)
        if outline is not None:
            self._apply_outline(outline)
        elif not loader().pending(self._job_id):
            self.section.set_summary(i18n.tr('没有可解析的结构'))
            self._set_status(i18n.tr('这个文件本地读不出结构；不影响导入与翻译。'))

    def _apply_outline(self, outline: dict) -> None:
        self._outline = dict(outline or {})
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
        self.section.set_summary(summary or i18n.tr('没有可解析的结构'))
        self._set_status("" if summary else i18n.tr(
            '这个文件本地读不出结构；不影响导入与翻译。'))
        if self.node_list.count():
            self.node_list.setCurrentRow(0)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(str(text or ""))
        self.status_label.setVisible(bool(text))

    # ---- 交互 ---------------------------------------------------------------
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

