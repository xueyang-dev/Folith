"""流式源（DOCX / EPUB / TXT / Markdown）的原位置上下文。

PDF 任务在原页上按坐标高亮（``folith.pdf_preview``）；这些格式没有页码和坐标，
能如实给出的是片段的**原始上下文**：所在标题路径（DOCX / Markdown）、所在章节
（EPUB）、行号（TXT / Markdown），加上相邻段落，并把匹配到的片段标出来。

只做结构化文本，不渲染原始样式——导入范围说明本来就不承诺原格式保真（#26）。
解析结果按 ``source.bin`` 的修改时间缓存；定位复用 ``pdf_preview`` 的候选短语
策略（整句优先、逐级缩短、找不到时如实说明）。
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import core

#: 有源上下文的流式格式（PDF 走原页对照，不在这里）。
STREAMING_SUFFIXES: tuple[str, ...] = (".docx", ".epub", ".txt", ".md", ".markdown")

#: 上下文窗口：匹配块前后各带几个相邻块。
CONTEXT_RADIUS = 2

_BLOCKS_CACHE_MAX = 8
_BLOCKS_CACHE: "OrderedDict[tuple[str, int], list[dict]]" = OrderedDict()

_KINDS = {".docx": "docx", ".epub": "epub", ".txt": "text",
          ".md": "markdown", ".markdown": "markdown"}

# ================= 可用性 =================

def source_suffix(job_id: str) -> str:
    """任务源文件的流式格式后缀；不是流式格式（或没有文件名）返回空串。"""
    if not job_id:
        return ""
    state = core.load_job_state(job_id) or {}
    name = str(state.get("filename") or "").lower()
    for suffix in STREAMING_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def has_streaming_source(job_id: str) -> bool:
    """任务是否带着一份可以读出上下文的流式源文件。"""
    if not job_id or not source_suffix(job_id):
        return False
    source = core.load_source(job_id)
    return bool(source) and isinstance(source, (bytes, bytearray))


def source_kind(job_id: str) -> str:
    return _KINDS.get(source_suffix(job_id), "")


# ================= 解析 =================

def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _path_for(job_id: str) -> Path:
    return Path(core.job_dir(job_id)) / "source.bin"


def load_blocks(job_id: str) -> list[dict]:
    """解析任务源文件为顺序块；结果按 (任务, 源文件 mtime) 缓存。

    每个块：``{"text", "role" ("heading"|"body"), "level"?, "line"?, "title"}``，
    ``title`` 是该块所在的标题路径（元组，EPUB 为章节名）；解析失败返回空表。
    """
    suffix = source_suffix(job_id)
    if not suffix:
        return []
    path = _path_for(job_id)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return []
    key = (job_id, stamp)
    cached = _BLOCKS_CACHE.get(key)
    if cached is not None:
        _BLOCKS_CACHE.move_to_end(key)
        return cached
    source = core.load_source(job_id)
    if not source or not isinstance(source, (bytes, bytearray)):
        return []
    try:
        if suffix == ".docx":
            blocks = _docx_blocks(source)
        elif suffix == ".epub":
            blocks = _epub_blocks(source)
        else:
            blocks = _text_blocks(source, suffix in (".md", ".markdown"))
    except Exception:  # noqa: BLE001 - 源文件读不出来就如实说没有上下文
        blocks = []
    _BLOCKS_CACHE[key] = blocks
    while len(_BLOCKS_CACHE) > _BLOCKS_CACHE_MAX:
        _BLOCKS_CACHE.popitem(last=False)
    return blocks


def _docx_blocks(source: bytes) -> list[dict]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    document = Document(io.BytesIO(source))
    blocks: list[dict] = []
    path: tuple[str, ...] = ()
    for element in document.element.body.iterchildren():
        if element.tag == qn("w:p"):
            paragraph = Paragraph(element, document)
            text = _normalize(paragraph.text)
            if not text:
                continue
            style = paragraph.style.style_id if paragraph.style is not None else ""
            level = _heading_level(style)
            if level is not None:
                path = (*path[:level - 1], text)
                blocks.append({"text": text, "role": "heading", "level": level,
                               "line": None, "title": path})
                continue
            blocks.append({"text": text, "role": "body", "line": None,
                           "title": path})
        elif element.tag == qn("w:tbl"):
            # 表格按行以文本呈现（单元格用「 · 」连接），保持它们在正文里的顺序。
            table = Table(element, document)
            for row in table.rows:
                text = _normalize(" · ".join(cell.text for cell in row.cells))
                if text:
                    blocks.append({"text": text, "role": "body", "line": None,
                                   "title": path})
    return blocks


def _heading_level(style: str) -> int | None:
    """内置标题样式（Heading1 / Heading 3 / Title）的层级；正文返回 ``None``。"""
    if style == "Title":
        return 1
    if not style.startswith("Heading"):
        return None
    digits = re.sub(r"\D", "", style)
    return int(digits) if digits else 1


class _XhtmlText(HTMLParser):
    """按块级元素抽取 EPUB 正文的文本（不保留任何原始标记）。"""

    _HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    _BLOCK_TAGS = {"p", "li", "blockquote", *_HEADINGS}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.headings: list[str] = []
        self._tag = ""
        self._level = 0
        self._depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BLOCK_TAGS:
            self._depth += 1
            if not self._tag:
                self._tag = tag
                self._level = int(tag[1]) if tag in self._HEADINGS else 0
                self._chunks = []

    def handle_endtag(self, tag):
        if tag in self._BLOCK_TAGS and self._depth:
            self._depth -= 1
            if tag == self._tag and not self._depth:
                self._emit()

    def handle_data(self, data):
        if self._tag:
            self._chunks.append(data)

    def _emit(self):
        text = _normalize("".join(self._chunks))
        tag, level = self._tag, self._level
        self._tag, self._level, self._chunks = "", 0, []
        if not text:
            return
        if level:
            self.headings.append(text)
            self.blocks.append({"text": text, "role": "heading", "level": level,
                                "line": None, "title": ()})
            return
        self.blocks.append({"text": text, "role": "body", "line": None,
                            "title": ()})


def _epub_blocks(source: bytes) -> list[dict]:
    """按 spine 顺序读取每章正文；章节名取章内第一个标题，退回文件名。"""
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        opf_path = _opf_path(archive)
        if not opf_path:
            return []
        base = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        opf = ET.fromstring(archive.read(opf_path))
        hrefs: dict[str, str] = {}
        for item in opf.iter():
            if item.tag.endswith("}item") or item.tag == "item":
                identifier, href = item.get("id"), item.get("href")
                if identifier and href:
                    hrefs[identifier] = href
        spine = [item.get("idref") for item in opf.iter()
                 if (item.tag.endswith("}itemref") or item.tag == "itemref")
                 and item.get("idref")]
        blocks: list[dict] = []
        for idref in spine:
            href = hrefs.get(idref or "")
            if not href:
                continue
            try:
                markup = archive.read(base + href)
            except KeyError:
                continue
            parser = _XhtmlText()
            try:
                parser.feed(markup.decode("utf-8", errors="replace"))
            except Exception:  # noqa: BLE001 - 一章坏掉不拖垮整本
                continue
            chapter = next((text for text in parser.headings if text),
                           href.rsplit("/", 1)[-1])
            for block in parser.blocks:
                block["title"] = (chapter,)
                blocks.append(block)
    return blocks


def _opf_path(archive: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except (KeyError, ET.ParseError):
        return ""
    for rootfile in container.iter():
        if (rootfile.tag.endswith("}rootfile") or rootfile.tag == "rootfile") \
                and rootfile.get("full-path"):
            return str(rootfile.get("full-path"))
    return ""


_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _text_blocks(source: bytes, markdown: bool) -> list[dict]:
    """TXT / Markdown：块 = 非空行，带行号；Markdown 另记标题路径并跳过代码块。"""
    text = source.decode("utf-8-sig", errors="replace")
    blocks: list[dict] = []
    path: tuple[str, ...] = ()
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if markdown and _FENCE_RE.match(stripped):
            fenced = not fenced
            continue
        if not stripped or fenced:
            continue
        if markdown:
            heading = _HEADING_RE.match(stripped)
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                path = (*path[:level - 1], title)
                blocks.append({"text": title, "role": "heading", "level": level,
                               "line": number, "title": path})
                continue
        blocks.append({"text": _normalize(stripped), "role": "body",
                       "line": number, "title": path})
    return blocks


# ================= 定位 =================

def _candidate_queries(text: str) -> list[str]:
    """与 PDF 定位同一套候选短语：整句优先、逐级缩短、至少两个字符。"""
    from folith.pdf_preview import _candidate_search_queries

    return _candidate_search_queries(text)


def _title_text(block: dict, kind: str) -> str:
    """定位到的位置说明：EPUB 是章节名，DOCX 是标题路径，TXT/MD 加行号。"""
    if kind == "text":
        return i18n_line(block.get("line"))
    parts = [str(part) for part in (block.get("title") or ()) if part]
    if kind == "markdown" and block.get("line") is not None:
        parts.append(i18n_line(block.get("line")))
    return " › ".join(parts)


def i18n_line(line: int | None) -> str:
    return f"第 {line} 行" if line else ""


def locate_segment(job_id: str, segment_text: str, start_hint: int = 0) -> dict:
    """在第 ``start_hint`` 块之后（再回卷）找包含这段原文的位置。

    返回 ``{"kind", "found", "title", "blocks", "hint"}``：``blocks`` 是匹配块
    前后各 ``CONTEXT_RADIUS`` 个相邻块，匹配到的块带 ``highlight: True``。
    """
    kind = source_kind(job_id)
    empty = {"kind": kind, "found": False, "title": "", "blocks": [], "hint": ""}
    blocks = load_blocks(job_id)
    if not blocks:
        return {**empty, "hint": "无法读取源文件，或源文档没有可定位的正文。"}
    queries = _candidate_queries(segment_text)
    if not queries:
        return {**empty, "hint": "这一段没有原文，无法定位。"}
    start = max(0, min(int(start_hint), len(blocks) - 1))
    order = list(range(start, len(blocks))) + list(range(0, start))
    matched = -1
    for query in queries:
        for index in order:
            if query in blocks[index]["text"]:
                matched = index
                break
        if matched >= 0:
            break
    if matched < 0:
        return {**empty, "hint": "没能在源文件里定位这一段。"}
    window = blocks[max(0, matched - CONTEXT_RADIUS):matched + CONTEXT_RADIUS + 1]
    context = []
    for block in window:
        context.append({**block, "title": list(block.get("title") or ()),
                        "highlight": block is blocks[matched]})
    title = _title_text(blocks[matched], kind)
    return {"kind": kind, "found": True, "title": title, "blocks": context,
            "hint": "已定位并在上下文中高亮。"}
