"""模型无关的原文结构解析（#27，「准备」页的只读原文结构）。

只读本地源文件，不调用模型、不做 OCR——扫描页没有文字层就如实说「未识别」，
绝不猜。解析出的 ``paragraphs`` 与 :func:`core.extract_document_paragraphs_with_report`
在文字层路径上出自同一批提取函数、逐字相同，后续按结构消费它的界面
（#28 的翻译范围、#61 的原位置定位）可以直接复用这份结果与锚点，
不需要每个界面再写一套源解析。

读不出的格式或损坏文件返回空结构（``nodes``/``paragraphs`` 为空），不报错。
"""
from __future__ import annotations

import hashlib
import io
import json
import re

#: 逐页检查上限（与 doc_facts 同一口径）：超长 PDF 只看前这么多页，并如实标记。
MAX_SCAN_PAGES = 2000
#: 节点数上限：一页一个节点的 PDF 超过它就按页分组，列表不无限增长。
MAX_OUTLINE_NODES = 400
#: TXT / Markdown 没有章节结构时按连续段落分组的大小。
PARAGRAPH_GROUP_SIZE = 20
#: 一页至少有这么多可见字符才算"有文字层"（页眉页码这类零碎不算）。
MIN_TEXT_CHARS = 20
SAMPLE_CHARS = 160
TITLE_MAX_CHARS = 60


def _sample(text: str) -> str:
    value = " ".join(str(text or "").split())
    return value[:SAMPLE_CHARS] + ("…" if len(value) > SAMPLE_CHARS else "")


def _node(index: int, *, kind: str, title: str, level: int = 1,
          paragraphs: list[str] | None = None, para_start: int = 0,
          pages: int = 0, page_start: int | None = None,
          page_end: int | None = None, textless_pages: int = 0) -> dict:
    texts = list(paragraphs or [])
    scanned = bool(pages) and not texts and textless_pages >= pages
    return {
        "id": f"n{index:04d}",
        "kind": kind,
        "title": (str(title or "").strip()[:TITLE_MAX_CHARS] or "未命名部分"),
        "level": int(level),
        "page_start": page_start,
        "page_end": page_end,
        "pages": int(pages),
        "textless_pages": int(textless_pages),
        "para_start": int(para_start),
        "para_count": len(texts),
        "chars": sum(len(item) for item in texts),
        "sample": _sample(texts[0]) if texts else "",
        "scanned": scanned,
        "paragraphs": texts,
    }


def _finish(outline: dict) -> dict:
    """统一收尾：节点 id 按顺序落定、总量与指纹就绪。"""
    for index, node in enumerate(outline.get("nodes") or []):
        node["id"] = f"n{index:04d}"
    outline["total_paragraphs"] = len(outline.get("paragraphs") or [])
    outline["total_chars"] = sum(len(item) for item in outline.get("paragraphs") or [])
    outline["fingerprint"] = outline_fingerprint(outline)
    return outline


def outline_fingerprint(outline: dict) -> str:
    """结构指纹：同一次解析复算必相同；结构变了（增删章节/段落）必不同。

    重复打开「准备」页用它可以确认拿到的是同一份结构；位置编号（n0003）
    本身不跨解析稳定，指纹才是判断"结构还是不是那份"的依据。
    """
    payload = {
        "paragraphs": int(outline.get("total_paragraphs") or 0),
        "chars": int(outline.get("total_chars") or 0),
        "nodes": [[str(node.get("id") or ""), str(node.get("title") or ""),
                   int(node.get("para_start") or 0), int(node.get("para_count") or 0)]
                  for node in outline.get("nodes") or []],
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False,
                                   sort_keys=True).encode("utf-8")).hexdigest()


def empty_outline(filename: str) -> dict:
    name = str(filename or "")
    return {"format": name.split(".")[-1].upper() if "." in name else "",
            "nodes": [], "paragraphs": [], "pages": 0, "scanned": False,
            "partial": False}


def parse_source_outline(filename: str, data: bytes) -> dict:
    """读源文件，返回结构与逐段原文（文字层路径；不 OCR、不调模型）。"""
    name = str(filename or "").lower()
    data = bytes(data or b"")
    try:
        if name.endswith(".pdf"):
            outline = _pdf_outline(data)
        elif name.endswith(".docx"):
            outline = _docx_outline(data)
        elif name.endswith(".epub"):
            outline = _epub_outline(data)
        elif name.endswith((".md", ".markdown")):
            outline = _markdown_outline(data)
        elif name.endswith(".txt"):
            outline = _text_outline(data)
        else:
            outline = empty_outline(filename)
    except Exception:  # noqa: BLE001 - 坏文件只是没有结构可看，不能影响导入
        outline = empty_outline(filename)
    return _finish(outline)


# ---------------------------------------------------------------------------
# PDF：版面段落（带页码）+ 目录章节（get_toc，本地读取，不调模型）
# ---------------------------------------------------------------------------

def _pdf_paragraphs(data: bytes) -> tuple[list[str], list[int]]:
    """与 core 文字层路径同一批提取函数：段落文本 + 1 起始页码（未知记 0）。"""
    from folith.engine.llm_provider import clean_xml_chars
    from folith.pdf_ingestion import (ORNAMENT_RE, classify_blocks,
                                      extract_layout_blocks,
                                      reconstruct_paragraph_records)

    paragraphs: list[str] = []
    page_of: list[int] = []
    records = reconstruct_paragraph_records(
        classify_blocks(extract_layout_blocks(data)))
    for record in records:
        text = record.get("text")
        if not text or ORNAMENT_RE.fullmatch(text):
            continue
        paragraphs.append(clean_xml_chars(text))
        numbers = [int(item) for item in (record.get("page_numbers") or [])
                   if isinstance(item, (int, float)) and int(item) > 0]
        page_of.append(numbers[0] if numbers else 0)
    return paragraphs, page_of


def _pdf_outline(data: bytes) -> dict:
    import fitz

    outline = {**empty_outline("a.pdf"), "nodes": [], "paragraphs": [],
               "textless_pages": []}
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            outline["encrypted"] = True
            return outline
        page_count = int(document.page_count)
        outline["pages"] = page_count
        textless: set[int] = set()
        for index in range(min(page_count, MAX_SCAN_PAGES)):
            text = "".join(document[index].get_text("text").split())
            if len(text) < MIN_TEXT_CHARS:
                textless.add(index + 1)
        outline["textless_pages"] = sorted(textless)
        outline["partial"] = page_count > MAX_SCAN_PAGES
        outline["scanned"] = bool(page_count) and len(textless) >= page_count
        paragraphs, page_of = _pdf_paragraphs(data)
        outline["paragraphs"] = paragraphs
        # 每页覆盖到的段落区间（页码未知=0 的段落不属于任何页，但留在总列表里）。
        spans: dict[int, tuple[int, int]] = {}
        for position, page in enumerate(page_of):
            if not page:
                continue
            first, last = spans.get(page, (position, position))
            spans[page] = (min(first, position), max(last, position))

        def _node_for(title: str, *, page_start: int, page_end: int,
                      level: int, index: int) -> dict:
            bounds = [spans[page] for page in range(page_start, page_end + 1)
                      if page in spans]
            textless_count = sum(1 for page in range(page_start, page_end + 1)
                                 if page in textless and page not in spans)
            if bounds:
                first = min(item[0] for item in bounds)
                last = max(item[1] for item in bounds)
                texts = paragraphs[first:last + 1]
            else:
                first, texts = 0, []
            return _node(index, kind="chapter" if level else "page",
                         title=title, level=max(1, level), paragraphs=texts,
                         para_start=first, pages=page_end - page_start + 1,
                         page_start=page_start, page_end=page_end,
                         textless_pages=textless_count)

        try:
            toc = [tuple(item) for item in (document.get_toc(simple=True) or [])
                   if isinstance(item, (list, tuple)) and len(item) >= 3]
        except Exception:  # noqa: BLE001 - 没有/损坏的目录就退回分页结构
            toc = []
        chapters = [item for item in toc
                    if int(item[0] or 0) == 1 and str(item[1] or "").strip()]
        nodes: list[dict] = []
        if chapters:
            for position, item in enumerate(chapters):
                page_start = max(1, int(item[2] or 1))
                page_end = (max(1, int(chapters[position + 1][2] or 1)) - 1
                            if position + 1 < len(chapters) else page_count)
                page_end = max(page_start, min(page_end, page_count))
                # 目录之前还有正文（封面后的前置页）就单列一节，不让它混进第一章。
                if position == 0 and page_start > 1 and any(
                        page in spans for page in range(1, page_start)):
                    nodes.append(_node_for(
                        "前置页", page_start=1, page_end=page_start - 1,
                        level=1, index=len(nodes)))
                nodes.append(_node_for(str(item[1]), page_start=page_start,
                                       page_end=page_end, level=1,
                                       index=len(nodes)))
        else:
            # 没有目录：按页给结构；页数太多时按连续页分组，列表保持可用。
            group = max(1, -(-page_count // MAX_OUTLINE_NODES))
            for start in range(1, page_count + 1, group):
                page_end = min(start + group - 1, page_count)
                title = (f"第 {start} 页" if group == 1
                         else f"第 {start}–{page_end} 页")
                nodes.append(_node_for(title, page_start=start,
                                       page_end=page_end, level=0,
                                       index=len(nodes)))
        outline["nodes"] = nodes
    return outline


# ---------------------------------------------------------------------------
# DOCX：标题样式（Heading 1–6 / Title）分章；段落过滤与 core 完全一致
# ---------------------------------------------------------------------------

_DOCX_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-6])$", re.IGNORECASE)


def _docx_heading_level(style_name: str) -> int:
    text = str(style_name or "").strip()
    match = _DOCX_HEADING_RE.match(text)
    if match:
        return int(match.group(1))
    if text.lower() in {"title", "标题"}:
        return 1
    return 0


def _docx_outline(data: bytes) -> dict:
    from docx import Document

    import core as _core  # 延迟导入：复用 core 提取用的同一条装饰线过滤
    from folith.engine.llm_provider import clean_xml_chars

    outline = {**empty_outline("a.docx"), "nodes": []}
    document = Document(io.BytesIO(data))
    paragraphs: list[str] = []
    levels: list[int] = []
    for paragraph in document.paragraphs:
        style = ""
        try:
            style = str(paragraph.style.name or "") if paragraph.style else ""
        except Exception:  # noqa: BLE001 - 样式缺失只是没有标题层级
            style = ""
        level = _docx_heading_level(style)
        for sub in re.split(r"\n+", clean_xml_chars(paragraph.text or "")):
            text = sub.strip()
            if len(text) > 1 and not _core._ORNAMENT_RE.match(text):
                paragraphs.append(text)
                levels.append(level)
    outline["paragraphs"] = paragraphs

    nodes: list[dict] = []
    current: list[str] = []
    current_start = 0
    current_title, current_level = "未分章内容", 1

    def _close() -> None:
        if current:
            nodes.append(_node(len(nodes), kind="chapter", title=current_title,
                               level=current_level, paragraphs=list(current),
                               para_start=current_start))
            current.clear()

    for position, (text, level) in enumerate(zip(paragraphs, levels)):
        if level:
            _close()
            current_title, current_level, current_start = text, level, position
        current.append(text)
    _close()
    outline["nodes"] = nodes
    return outline


# ---------------------------------------------------------------------------
# EPUB / TXT / Markdown：与 folith.text_import 同一批提取函数
# ---------------------------------------------------------------------------

def _epub_outline(data: bytes) -> dict:
    from folith.text_import import extract_text_source

    outline = {**empty_outline("a.epub"), "nodes": []}
    chapters, _unsupported, facts = extract_text_source(
        "book.epub", data, per_chapter=True)
    paragraphs: list[str] = []
    nodes: list[dict] = []
    for index, texts in enumerate(chapters):
        title = next((item for item in texts if len(item) <= TITLE_MAX_CHARS),
                     "") or f"第 {index + 1} 节"
        nodes.append(_node(index, kind="chapter", title=title, level=1,
                           paragraphs=texts, para_start=len(paragraphs)))
        paragraphs.extend(texts)
    outline["paragraphs"] = paragraphs
    outline["nodes"] = nodes
    outline["chapters"] = int(facts.get("chapters") or len(chapters))
    return outline


def _markdown_outline(data: bytes) -> dict:
    from markdown_it import MarkdownIt

    from folith.text_import import _html_paragraphs, decode_text

    outline = {**empty_outline("a.md"), "nodes": []}
    markup = MarkdownIt("commonmark").enable("table").render(decode_text(data))
    headings: list[dict] = []
    paragraphs = list(_html_paragraphs(markup, headings=headings)[0]) \
        if markup.strip() else []
    outline["paragraphs"] = paragraphs
    boundaries = [(int(item.get("index") or 0), int(item.get("level") or 1))
                  for item in headings]
    if not boundaries:
        return _group_paragraphs(outline)
    starts = [index for index, _level in boundaries]
    nodes: list[dict] = []
    if starts[0] > 0:
        nodes.append(_node(0, kind="chapter", title="未分章内容", level=1,
                           paragraphs=paragraphs[:starts[0]], para_start=0))
    for position, (index, level) in enumerate(boundaries):
        end = (starts[position + 1] if position + 1 < len(starts)
               else len(paragraphs))
        texts = paragraphs[index:end]
        nodes.append(_node(len(nodes), kind="chapter",
                           title=texts[0] if texts else "", level=level,
                           paragraphs=texts, para_start=index))
    outline["nodes"] = nodes
    return outline


def _text_outline(data: bytes) -> dict:
    from folith.text_import import decode_text

    outline = {**empty_outline("a.txt"), "nodes": []}
    text = decode_text(data)
    outline["paragraphs"] = [part.strip()
                             for part in re.split(r"\n\s*\n", text) if part.strip()]
    return _group_paragraphs(outline)


def _group_paragraphs(outline: dict) -> dict:
    """没有章节结构的文本：按固定大小给连续段落分组（段落边界是真实结构）。"""
    paragraphs = list(outline.get("paragraphs") or [])
    nodes: list[dict] = []
    for start in range(0, len(paragraphs), PARAGRAPH_GROUP_SIZE):
        texts = paragraphs[start:start + PARAGRAPH_GROUP_SIZE]
        end = start + len(texts)
        title = (f"第 {start + 1} 段" if start + 1 == end
                 else f"第 {start + 1}–{end} 段")
        nodes.append(_node(len(nodes), kind="group", title=title, level=1,
                           paragraphs=texts, para_start=start))
    outline["nodes"] = nodes
    return outline
