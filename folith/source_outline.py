"""模型无关的原文结构解析（#27，「准备」页的只读原文结构；#185，层级大纲提取）。

只读本地源文件，不调用模型、不做 OCR——扫描页没有文字层就如实说「未识别」，
绝不猜。解析出的 ``paragraphs`` 与 :func:`core.extract_document_paragraphs_with_report`
在文字层路径上出自同一批提取函数、逐字相同，后续按结构消费它的界面
（#28 的翻译范围、#61 的原位置定位、#186 的段落锚点）可以直接复用这份结果与锚点，
不需要每个界面再写一套源解析。

读不出的格式或损坏文件返回空结构（``nodes``/``paragraphs`` 为空），不报错。
"""
from __future__ import annotations

import hashlib
import io
import json
import re

#: 结构解析器版本。任何会改变节点 id、层级或段落范围的修改必须递增此版本号。
OUTLINE_PARSER_VERSION = 2

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
          parent_id: str = "", provenance: str = "page_group",
          paragraphs: list[str] | None = None, para_start: int = 0,
          pages: int = 0, page_start: int | None = None,
          page_end: int | None = None, textless_pages: int = 0) -> dict:
    texts = list(paragraphs or [])
    scanned = bool(pages) and not texts and textless_pages >= pages
    return {
        "id": f"n{index:04d}",
        "parent_id": str(parent_id or ""),
        "provenance": str(provenance or "page_group"),
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
    """统一收尾：节点 id 按顺序落定、层级父节点确定、总量与指纹就绪。"""
    nodes = outline.get("nodes") or []
    for index, node in enumerate(nodes):
        node["id"] = f"n{index:04d}"
    stack: list[tuple[int, str]] = []
    for node in nodes:
        level = int(node.get("level") or 0)
        if level <= 0:
            node["parent_id"] = ""
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        node["parent_id"] = stack[-1][1] if stack else ""
        stack.append((level, node["id"]))
    outline["parser_version"] = OUTLINE_PARSER_VERSION
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
            "parser_version": OUTLINE_PARSER_VERSION,
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
# PDF：版面段落（带页码）+ 目录章节（get_toc，本地读取，保留所有书签层级）
# ---------------------------------------------------------------------------

def _pdf_paragraphs(data: bytes) -> tuple[list[str], list[int], list[str]]:
    """与 core 文字层路径同一批提取函数：段落文本 + 1 起始页码（未知记 0）。"""
    from folith.engine.llm_provider import clean_xml_chars
    from folith.pdf_ingestion import (ORNAMENT_RE, classify_blocks,
                                      extract_layout_blocks,
                                      reconstruct_paragraph_records)

    paragraphs: list[str] = []
    page_of: list[int] = []
    roles: list[str] = []
    records = reconstruct_paragraph_records(
        classify_blocks(extract_layout_blocks(data)))
    for record in records:
        text = record.get("text")
        if not text or ORNAMENT_RE.fullmatch(text):
            continue
        paragraphs.append(clean_xml_chars(text))
        roles.append(str(record.get("role") or "body"))
        numbers = [int(item) for item in (record.get("page_numbers") or [])
                   if isinstance(item, (int, float)) and int(item) > 0]
        page_of.append(numbers[0] if numbers else 0)
    return paragraphs, page_of, roles


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
        paragraphs, page_of, roles = _pdf_paragraphs(data)
        outline["paragraphs"] = paragraphs
        outline["paragraph_roles"] = roles
        # 每页覆盖到的段落区间（页码未知=0 的段落不属于任何页，但留在总列表里）。
        spans: dict[int, tuple[int, int]] = {}
        for position, page in enumerate(page_of):
            if not page:
                continue
            first, last = spans.get(page, (position, position))
            spans[page] = (min(first, position), max(last, position))

        def _node_for(title: str, *, page_start: int, page_end: int,
                      level: int, index: int,
                      provenance: str = "page_group") -> dict:
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
                         title=title, level=max(1, level) if level else 0,
                         paragraphs=texts,
                         para_start=first, pages=page_end - page_start + 1,
                         page_start=page_start, page_end=page_end,
                         textless_pages=textless_count,
                         provenance=provenance)

        try:
            toc = [tuple(item) for item in (document.get_toc(simple=True) or [])
                   if isinstance(item, (list, tuple)) and len(item) >= 3]
        except Exception:  # noqa: BLE001 - 没有/损坏的目录就退回分页结构
            toc = []
        chapters = [item for item in toc
                    if int(item[0] or 0) >= 1 and str(item[1] or "").strip()]
        nodes: list[dict] = []
        if chapters:
            first_page = max(1, int(chapters[0][2] or 1))
            # 目录之前还有正文（封面后的前置页）就单列一节，不让它混进第一章。
            if first_page > 1 and any(
                    page in spans for page in range(1, first_page)):
                nodes.append(_node_for(
                    "前置页", page_start=1, page_end=first_page - 1,
                    level=1, index=len(nodes), provenance="page_group"))
            for position, item in enumerate(chapters):
                level = int(item[0] or 1)
                title = str(item[1])
                page_start = max(1, int(item[2] or 1))
                # 寻找同级或更高层级（数值 <= level）的下一个节点作为结束边界
                next_page = page_count + 1
                for next_item in chapters[position + 1:]:
                    if int(next_item[0] or 1) <= level:
                        next_page = max(1, int(next_item[2] or 1))
                        break
                page_end = max(page_start, min(next_page - 1, page_count))
                nodes.append(_node_for(title, page_start=page_start,
                                       page_end=page_end, level=level,
                                       index=len(nodes),
                                       provenance="native_toc"))
        else:
            # 没有目录：按页给结构；页数太多时按连续页分组，列表保持可用。
            group = max(1, -(-page_count // MAX_OUTLINE_NODES))
            for start in range(1, page_count + 1, group):
                page_end = min(start + group - 1, page_count)
                title = (f"第 {start} 页" if group == 1
                         else f"第 {start}–{page_end} 页")
                nodes.append(_node_for(title, page_start=start,
                                       page_end=page_end, level=0,
                                       index=len(nodes),
                                       provenance="page_group"))
        outline["nodes"] = nodes
    return outline


# ---------------------------------------------------------------------------
# DOCX：Heading 1–9 / w:outlineLvl / w:numPr(ilvl) 分章；段落过滤与 core 一致
# ---------------------------------------------------------------------------

_DOCX_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-9])$", re.IGNORECASE)


def _docx_paragraph_level_and_provenance(paragraph) -> tuple[int, str]:
    from docx.oxml.ns import qn

    # 1. 标题样式名称（Heading 1–9 / Title / 标题）
    style_name = ""
    try:
        style_name = str(paragraph.style.name or "").strip() if paragraph.style else ""
    except Exception:
        style_name = ""
    match = _DOCX_HEADING_RE.match(style_name)
    if match:
        return int(match.group(1)), "native_heading_style"
    if style_name.lower() in {"title", "标题"}:
        return 1, "native_heading_style"

    # 2. w:outlineLvl（段落直接格式或样式定义）
    ppr = getattr(paragraph._p, "pPr", None)
    outline = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
    if outline is None and getattr(paragraph, "style", None) is not None:
        style_elem = getattr(paragraph.style, "element", None)
        if style_elem is not None:
            style_ppr = style_elem.find(qn("w:pPr"))
            if style_ppr is not None:
                outline = style_ppr.find(qn("w:outlineLvl"))
    if outline is not None:
        try:
            val = int(outline.get(qn("w:val")))
            if 0 <= val <= 8:
                return val + 1, "native_outline_level"
        except (TypeError, ValueError):
            pass

    # 3. 多级编号大纲级别（w:numPr / w:ilvl）
    num_pr = ppr.find(qn("w:numPr")) if ppr is not None else None
    if num_pr is None and getattr(paragraph, "style", None) is not None:
        style_elem = getattr(paragraph.style, "element", None)
        if style_elem is not None:
            style_ppr = style_elem.find(qn("w:pPr"))
            if style_ppr is not None:
                num_pr = style_ppr.find(qn("w:numPr"))
    if num_pr is not None:
        ilvl = num_pr.find(qn("w:ilvl"))
        if ilvl is not None:
            try:
                val = int(ilvl.get(qn("w:val")))
                if 0 <= val <= 8:
                    return val + 1, "native_outline_level"
            except (TypeError, ValueError):
                pass

    return 0, ""


def _docx_heading_level(style_name: str) -> int:
    """保持向后兼容的 helper 函数。"""
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
    provenances: list[str] = []
    for paragraph in document.paragraphs:
        level, prov = _docx_paragraph_level_and_provenance(paragraph)
        first_sub = True
        for sub in re.split(r"\n+", clean_xml_chars(paragraph.text or "")):
            text = sub.strip()
            if len(text) > 1 and not _core._ORNAMENT_RE.match(text):
                paragraphs.append(text)
                if first_sub and level > 0:
                    levels.append(level)
                    provenances.append(prov)
                    first_sub = False
                else:
                    levels.append(0)
                    provenances.append("")
    outline["paragraphs"] = paragraphs

    nodes: list[dict] = []
    current: list[str] = []
    current_start = 0
    current_title, current_level, current_prov = "未分章内容", 1, "page_group"

    def _close() -> None:
        if current:
            nodes.append(_node(len(nodes), kind="chapter", title=current_title,
                               level=current_level, paragraphs=list(current),
                               para_start=current_start, provenance=current_prov))
            current.clear()

    for position, (text, level, prov) in enumerate(zip(paragraphs, levels, provenances)):
        if level:
            _close()
            current_title, current_level, current_start, current_prov = (
                text, level, position, prov
            )
        current.append(text)
    _close()
    outline["nodes"] = nodes
    return outline


# ---------------------------------------------------------------------------
# EPUB：Precedence: nav.xhtml -> toc.ncx -> XHTML headings -> spine boundary
# ---------------------------------------------------------------------------

def _tag_name(el) -> str:
    return str(getattr(el, "tag", "")).split("}")[-1].split(":")[-1].lower()


def _parse_epub3_nav(content: bytes) -> list[dict]:
    from lxml import html

    from folith.text_import import decode_text

    text = decode_text(content)
    cleaned = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    root = html.fromstring(cleaned)
    nav = next((el for el in root.iter()
                if _tag_name(el) == "nav"
                and any("toc" in el.get(attr, "").lower() for attr in ("epub:type", "role", "id"))), None)
    if nav is None:
        nav = next((el for el in root.iter() if _tag_name(el) == "nav"), None)
    if nav is None:
        return []
    top_list = next((el for el in nav.iter() if _tag_name(el) in {"ol", "ul"}), None)
    if top_list is None:
        return []

    def _parse_list(list_elem, level: int) -> list[dict]:
        items: list[dict] = []
        for li in list_elem:
            if _tag_name(li) != "li":
                continue
            a = next((el for el in li.iter() if _tag_name(el) == "a"), None)
            if a is not None:
                title = " ".join("".join(a.itertext()).split())
                href = a.get("href", "")
            else:
                span = next((el for el in li.iter() if _tag_name(el) == "span"), None)
                if span is not None:
                    title = " ".join("".join(span.itertext()).split())
                    href = ""
                else:
                    title = " ".join((li.text or "").split())
                    href = ""
            sub_list = next((el for el in li if _tag_name(el) in {"ol", "ul"}), None)
            children = _parse_list(sub_list, level + 1) if sub_list is not None else []
            if title:
                items.append({
                    "title": title,
                    "href": href,
                    "level": level,
                    "children": children,
                })
        return items

    return _parse_list(top_list, 1)


def _parse_epub2_ncx(content: bytes) -> list[dict]:
    from defusedxml import ElementTree

    root = ElementTree.fromstring(content)
    nav_map = root.find(".//{*}navMap")
    if nav_map is None:
        return []

    def _parse_nav_points(parent, level: int) -> list[dict]:
        items: list[dict] = []
        for nav_point in parent.findall("./{*}navPoint"):
            text_elem = nav_point.find("./{*}navLabel/{*}text")
            title = "".join(text_elem.itertext()).strip() if text_elem is not None else ""
            content_elem = nav_point.find("./{*}content")
            href = content_elem.get("src", "") if content_elem is not None else ""
            children = _parse_nav_points(nav_point, level + 1)
            if title:
                items.append({
                    "title": title,
                    "href": href,
                    "level": level,
                    "children": children,
                })
        return items

    return _parse_nav_points(nav_map, 1)


def _build_epub_toc_nodes(toc_entries: list[dict], toc_dir: str,
                          spine_records: list[dict],
                          all_paragraphs: list[str]) -> list[dict]:
    import posixpath
    from urllib.parse import unquote

    flat: list[dict] = []

    def _walk(entries):
        for entry in entries:
            flat.append(entry)
            if entry.get("children"):
                _walk(entry["children"])

    _walk(toc_entries)

    for entry in flat:
        href = entry.get("href", "")
        file_part, _, anchor_part = href.partition("#")
        target_path = posixpath.normpath(posixpath.join(toc_dir, unquote(file_part))) if file_part else ""
        spine_idx = next((i for i, rec in enumerate(spine_records) if rec["path"] == target_path), None)
        if spine_idx is not None:
            rec = spine_records[spine_idx]
            entry["spine_index"] = spine_idx
            if anchor_part and anchor_part in rec["anchors"]:
                entry["para_start"] = rec["start"] + rec["anchors"][anchor_part]
            else:
                matched = next((h for h in rec["headings"] if h["text"] == entry["title"]), None)
                if matched:
                    entry["para_start"] = matched["doc_index"]
                else:
                    entry["para_start"] = rec["start"]
        else:
            entry["para_start"] = 0
            entry["spine_index"] = 0

    covered_spines = {e["spine_index"] for e in flat if "spine_index" in e}
    nodes: list[dict] = []

    # 1. 目录覆盖前未命名的阅读顺序章节
    first_covered = min(covered_spines) if covered_spines else 0
    for idx in range(first_covered):
        rec = spine_records[idx]
        if rec["count"] > 0:
            title = rec["headings"][0]["text"] if rec["headings"] else f"第 {idx + 1} 节"
            nodes.append(_node(len(nodes), kind="chapter", title=title, level=1,
                               paragraphs=rec["paragraphs"], para_start=rec["start"],
                               provenance="spine_boundary"))

    # 2. 目录条目
    for i, entry in enumerate(flat):
        start = entry["para_start"]
        level = entry["level"]
        next_start = len(all_paragraphs)
        for next_entry in flat[i + 1:]:
            if next_entry["level"] <= level:
                next_start = next_entry["para_start"]
                break
        else:
            if covered_spines:
                last_covered = max(covered_spines)
                next_start = spine_records[last_covered]["start"] + spine_records[last_covered]["count"]
        para_count = max(0, next_start - start)
        texts = all_paragraphs[start:start + para_count]
        nodes.append(_node(len(nodes), kind="chapter", title=entry["title"],
                           level=level, paragraphs=texts, para_start=start,
                           provenance="native_toc"))

    # 3. 目录覆盖后未命名的阅读顺序章节
    last_covered = max(covered_spines) if covered_spines else -1
    for idx in range(last_covered + 1, len(spine_records)):
        rec = spine_records[idx]
        if rec["count"] > 0:
            title = rec["headings"][0]["text"] if rec["headings"] else f"第 {idx + 1} 节"
            nodes.append(_node(len(nodes), kind="chapter", title=title, level=1,
                               paragraphs=rec["paragraphs"], para_start=rec["start"],
                               provenance="spine_boundary"))

    return nodes


def _build_epub_heading_nodes(all_headings: list[dict], all_paragraphs: list[str]) -> list[dict]:
    nodes: list[dict] = []
    if all_headings[0]["doc_index"] > 0:
        first_idx = all_headings[0]["doc_index"]
        nodes.append(_node(0, kind="chapter", title="未分章内容", level=1,
                           paragraphs=all_paragraphs[:first_idx],
                           para_start=0, provenance="page_group"))
    for i, h in enumerate(all_headings):
        start = h["doc_index"]
        level = int(h["level"])
        next_start = len(all_paragraphs)
        for next_h in all_headings[i + 1:]:
            if int(next_h["level"]) <= level:
                next_start = next_h["doc_index"]
                break
        texts = all_paragraphs[start:next_start]
        nodes.append(_node(len(nodes), kind="chapter", title=h["text"],
                           level=level, paragraphs=texts,
                           para_start=start, provenance="native_heading_style"))
    return nodes


def _epub_outline(data: bytes) -> dict:
    import posixpath
    import zipfile
    from urllib.parse import unquote

    from defusedxml import ElementTree
    from lxml import etree

    from folith.text_import import _html_paragraphs

    outline = {**empty_outline("a.epub"), "nodes": []}
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        container = ElementTree.fromstring(book.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB 缺少书籍目录。")
        package_path = rootfile.get("full-path")
        package_dir = posixpath.dirname(package_path)
        package = ElementTree.fromstring(book.read(package_path))
        manifest = {item.get("id"): item for item in package.findall("./{*}manifest/{*}item")}
        spine = package.findall("./{*}spine/{*}itemref")
        if not spine:
            raise ValueError("EPUB 缺少阅读顺序。")
        spine_elem = package.find("./{*}spine")
        ncx_id = spine_elem.get("toc") if spine_elem is not None else None

        if "META-INF/encryption.xml" in book.namelist():
            encryption = ElementTree.fromstring(book.read("META-INF/encryption.xml"))
            encrypted = {unquote(item.get("URI", "")) for item in encryption.findall(".//{*}CipherReference")}
        else:
            encrypted = set()

        all_paragraphs: list[str] = []
        spine_records: list[dict] = []
        for ref in spine:
            item = manifest.get(ref.get("idref"))
            if item is None:
                continue
            href = item.get("href", "").split("#")[0]
            path = posixpath.normpath(posixpath.join(package_dir, unquote(href)))
            if path in encrypted:
                raise ValueError("EPUB 正文已加密，无法导入；请提供无 DRM 的版本。")
            if ref.get("linear") == "no" or item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            if path not in book.namelist():
                continue
            content = book.read(path)
            if item.get("media-type") == "application/xhtml+xml":
                document = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True))
                content = etree.tostring(document, encoding="unicode")
            headings: list[dict] = []
            anchors: dict[str, int] = {}
            chapter_paras, _ = _html_paragraphs(content, headings=headings, anchors=anchors)
            start_idx = len(all_paragraphs)
            all_paragraphs.extend(chapter_paras)
            for h in headings:
                h["doc_index"] = start_idx + int(h.get("index") or 0)
                h["text"] = all_paragraphs[h["doc_index"]] if h["doc_index"] < len(all_paragraphs) else ""
            spine_records.append({
                "id": ref.get("idref"),
                "path": path,
                "start": start_idx,
                "count": len(chapter_paras),
                "paragraphs": chapter_paras,
                "headings": headings,
                "anchors": anchors,
            })

        outline["paragraphs"] = all_paragraphs
        outline["chapters"] = len(spine_records)

        # ------------------------------------------------------------------
        # Precedence 1: nav.xhtml (epub:type="toc")
        # ------------------------------------------------------------------
        nav_entries: list[dict] = []
        nav_dir = ""
        nav_item = next((item for item in manifest.values()
                         if "nav" in (item.get("properties") or "").split()), None)
        if nav_item is None:
            nav_item = next((item for item in manifest.values()
                             if item.get("media-type") in {"application/xhtml+xml", "text/html"}
                             and unquote(item.get("href", "")).lower().endswith("nav.xhtml")), None)
        if nav_item is not None:
            nav_path = posixpath.normpath(posixpath.join(package_dir, unquote(nav_item.get("href", "").split("#")[0])))
            if nav_path in book.namelist():
                nav_dir = posixpath.dirname(nav_path)
                nav_entries = _parse_epub3_nav(book.read(nav_path))

        # ------------------------------------------------------------------
        # Precedence 2: toc.ncx
        # ------------------------------------------------------------------
        ncx_entries: list[dict] = []
        ncx_dir = ""
        if not nav_entries:
            ncx_item = manifest.get(ncx_id) if ncx_id else None
            if ncx_item is None:
                ncx_item = next((item for item in manifest.values()
                                 if item.get("media-type") == "application/x-dtbncx+xml"), None)
            if ncx_item is None:
                ncx_name = next((name for name in book.namelist() if name.lower().endswith(".ncx")), None)
                ncx_path = ncx_name if ncx_name else None
            else:
                ncx_path = posixpath.normpath(posixpath.join(package_dir, unquote(ncx_item.get("href", "").split("#")[0])))
            if ncx_path and ncx_path in book.namelist():
                ncx_dir = posixpath.dirname(ncx_path)
                ncx_entries = _parse_epub2_ncx(book.read(ncx_path))

        toc_entries = nav_entries or ncx_entries
        toc_dir = nav_dir if nav_entries else ncx_dir

        if toc_entries:
            nodes = _build_epub_toc_nodes(
                toc_entries, toc_dir, spine_records, all_paragraphs)
        else:
            # Precedence 3: XHTML heading hierarchy (h1-h6)
            all_headings = [h for rec in spine_records for h in rec["headings"]]
            if all_headings:
                nodes = _build_epub_heading_nodes(all_headings, all_paragraphs)
            else:
                # Precedence 4: Spine boundary
                nodes = []
                for idx, rec in enumerate(spine_records):
                    nodes.append(_node(len(nodes), kind="chapter",
                                       title=f"第 {idx + 1} 节",
                                       level=1,
                                       paragraphs=rec["paragraphs"],
                                       para_start=rec["start"],
                                       provenance="spine_boundary"))
        outline["nodes"] = nodes
    return outline


# ---------------------------------------------------------------------------
# Markdown / TXT：段落与标题提取
# ---------------------------------------------------------------------------

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
                           paragraphs=paragraphs[:starts[0]], para_start=0,
                           provenance="page_group"))
    for position, (index, level) in enumerate(boundaries):
        next_start = len(paragraphs)
        for next_idx, next_lvl in boundaries[position + 1:]:
            if next_lvl <= level:
                next_start = next_idx
                break
        texts = paragraphs[index:next_start]
        nodes.append(_node(len(nodes), kind="chapter",
                           title=texts[0] if texts else "", level=level,
                           paragraphs=texts, para_start=index,
                           provenance="native_heading_style"))
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
                           paragraphs=texts, para_start=start,
                           provenance="page_group"))
    outline["nodes"] = nodes
    return outline
