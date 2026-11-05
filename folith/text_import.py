"""Text and reflowable ebook ingestion into the shared paragraph pipeline."""
from __future__ import annotations

import contextlib
import io
import posixpath
import re
import zipfile
from urllib.parse import unquote

from defusedxml import ElementTree
from lxml import etree, html
from markdown_it import MarkdownIt

BODY_MEDIA_TYPES = frozenset({"application/xhtml+xml", "text/html"})


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = data.decode("gb18030")
            except UnicodeDecodeError as exc:
                raise ValueError("文本编码无法识别，请另存为 UTF-8 后导入。") from exc
    if "\x00" in text:
        raise ValueError("文本包含空字符，请确认文件格式并另存为 UTF-8。")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _tag_name(element) -> str:
    return str(element.tag).split("}")[-1].split(":")[-1].lower()


def _href(element) -> str:
    """The URL an image element points at (``src``, ``href`` or ``xlink:href``)."""
    src = element.get("src")
    if src is not None:
        return str(src)
    for key, value in element.attrib.items():
        if str(key).lower().endswith("href"):
            return str(value)
    return ""


def _image_records(element, tag: str) -> list[dict]:
    """Describe the source images one element references, in document order.

    ``<img>`` yields one record; an SVG yields one record per embedded
    ``<image>`` (a pure vector SVG yields one ``vector-svg`` record).  Only
    source locations are collected here — no file is opened and no bytes are
    decoded, so callers keep full control over resource handling.
    """
    if tag == "img":
        return [{"tag": "img", "href": _href(element), "at": None}]
    records = [{"tag": "svg-image", "href": _href(node), "at": None}
               for node in element.iter()
               if node is not element and _tag_name(node) == "image"]
    if not records:
        return [{"tag": "vector-svg", "href": "", "at": None}]
    return records


def _object_records(element) -> list[dict]:
    """A bitmap referenced through ``<object data>`` must not vanish silently.

    The object's fallback children are still walked, so the paragraph text
    contract is unchanged; when the object has a ``data`` resource, any nested
    fallback ``img`` would be a duplicate of the same picture on screen, so
    image collection is suppressed while walking those children.
    """
    data = element.get("data")
    if not data:
        return []
    return [{"tag": "object", "href": str(data), "at": None}]


def _html_paragraphs(markup: str | bytes, headings: list | None = None,
                     anchors: dict | None = None,
                     images: list | None = None):
    """返回 ``(段落, 未纳入内容)``；传入 ``headings`` 列表时，把每个标题块
    ``{"index": 标题段落在结果里的下标, "level": 1–6}`` 追加进去（结构解析用）。
    传入 ``anchors`` 字典时，把元素 id/name 映射到其段落在结果里的下标。

    传入 ``images`` 列表时不再丢弃 ``img``/``svg``/``object``：每处引用追加一条
    ``{"tag": "img"|"svg-image"|"vector-svg"|"object", "href": 原始地址, "at": 段落边界}``。
    ``at`` 是“图片之前的完整段落数”：独立图片块落在前后段落之间的边界上，
    段内图片落在该完整段落之后；图片元素本身没有文字，因此不会改变段落序列。
    图片后方的 tail 文本照常并入段落；``<object data>`` 的 fallback 文字照常
    参与段落，图片资源本身由调用方保留或计入跳过。"""
    if isinstance(markup, bytes):
        markup = decode_text(markup)
    root = html.fromstring(markup)
    omitted = {}
    keep_images = images is not None
    for element in list(root.iter()):
        tag = _tag_name(element)
        drop = tag in {"head", "script", "style", "pre", "audio", "video"}
        if not keep_images and tag in {"img", "svg"}:
            drop = True
        if not drop:
            continue
        if tag not in {"head", "script", "style"}:
            kind = "code" if tag == "pre" else "image" if tag in {"img", "svg"} else "media"
            omitted[kind] = omitted.get(kind, 0) + 1
        element.drop_tree()
    body = next((node for node in root.iter() if _tag_name(node) == "body"), root)
    blocks = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "div", "section"}
    paragraphs = []
    # Flush around block boundaries; nested lists/divs never duplicate text.
    pending = []
    # Each open block remembers the images that belong *after that block*.
    # ``has_child_blocks`` decides what "after the block" means: a leaf block
    # (p/li/h1 that only contains inline content) resolves its images after its
    # own flush; a container block resolves them at the boundary captured when
    # the image was met, so an image between two child paragraphs cannot drift
    # to the end of the whole container.
    block_frames: list[dict] = []
    deferred: list[dict] = []
    suppress_images = [0]

    def flush():
        text = re.sub(r"\s+", " ", "".join(pending)).strip()
        if text:
            paragraphs.append(text)
        pending.clear()
        for record in deferred:
            record["at"] = len(paragraphs)
        deferred.clear()

    def add_image(record: dict) -> None:
        if not keep_images:
            return
        images.append(record)
        if any(chunk.strip() for chunk in pending):
            # Inline image: after the paragraph that contains it.
            deferred.append(record)
        elif block_frames:
            record["candidate"] = len(paragraphs)
            block_frames[-1]["records"].append(record)
        else:
            record["at"] = len(paragraphs)

    def walk(node):
        tag = _tag_name(node)
        block = tag in blocks
        if block:
            flush()
            if block_frames:
                block_frames[-1]["has_child_blocks"] = True
            block_frames.append({"records": [], "has_child_blocks": False})
        if anchors is not None:
            node_id = node.get("id") or node.get("name")
            if node_id and str(node_id) not in anchors:
                anchors[str(node_id)] = len(paragraphs)
        if node.text:
            pending.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                child_tag = _tag_name(child)
                if child_tag == "br":
                    pending.append(" ")
                elif child_tag in {"img", "svg"}:
                    if keep_images and not suppress_images[0]:
                        for record in _image_records(child, child_tag):
                            add_image(record)
                elif child_tag == "object" and keep_images and not suppress_images[0] \
                        and child.get("data"):
                    for record in _object_records(child):
                        add_image(record)
                    suppress_images[0] += 1
                    try:
                        walk(child)
                    finally:
                        suppress_images[0] -= 1
                else:
                    walk(child)
                if child_tag in {"td", "th"}:
                    pending.append(" | ")
            if child.tail:
                pending.append(child.tail)
        if block:
            before = len(paragraphs)
            flush()
            # 标题块在这里成段：只有真产出了标题文本才登记（空标题不算结构）。
            if (headings is not None and tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    and len(paragraphs) > before):
                headings.append({"index": len(paragraphs) - 1,
                                 "level": int(tag[1])})
            frame = block_frames.pop()
            for record in frame["records"]:
                # A leaf block (p/li/h1…) owns its inline images: they follow
                # its complete paragraph. A container block only provides the
                # current boundary, captured when the image was met.
                if frame["has_child_blocks"]:
                    record["at"] = record.pop("candidate", len(paragraphs))
                else:
                    record.pop("candidate", None)
                    record["at"] = len(paragraphs)
    walk(body)
    flush()
    return paragraphs, omitted


@contextlib.contextmanager
def open_epub(data: bytes):
    """Open an EPUB payload and yield its package facts.

    Yields ``(book, package_path, package, manifest, spine, encrypted)``.  The
    ZIP handle is closed on exit; every caller reads resources from that handle
    only (never from the filesystem).
    """
    book = zipfile.ZipFile(io.BytesIO(data))
    try:
        container = ElementTree.fromstring(book.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("EPUB 缺少书籍目录。")
        package_path = rootfile.get("full-path")
        package = ElementTree.fromstring(book.read(package_path))
        manifest = {item.get("id"): item for item in package.findall("./{*}manifest/{*}item")}
        spine = package.findall("./{*}spine/{*}itemref")
        if not spine:
            raise ValueError("EPUB 缺少阅读顺序。")
        if "META-INF/encryption.xml" in book.namelist():
            encryption = ElementTree.fromstring(book.read("META-INF/encryption.xml"))
            encrypted = {unquote(item.get("URI", "")) for item in encryption.findall(".//{*}CipherReference")}
        else:
            encrypted = set()
        yield book, package_path, package, manifest, spine, encrypted
    finally:
        book.close()


def epub_item_path(package_path: str, href) -> str:
    """Resolve an EPUB href against the package document (POSIX zip paths)."""
    return posixpath.normpath(posixpath.join(
        posixpath.dirname(package_path),
        unquote(str(href or "").split("#")[0])))


def epub_spine_entries(book, package_path: str, manifest: dict, spine, encrypted):
    """Yield ``(item, path, content)`` for every spine entry in reading order.

    ``content`` is the decoded/serialized XHTML markup for body documents and
    ``None`` for entries the text pipeline skips (``linear="no"`` or non-body
    media types).  Encrypted or missing body documents raise, exactly like the
    importer has always done; resource-level failures are the caller's problem.
    """
    for ref in spine:
        item = manifest.get(ref.get("idref"))
        if item is None:
            raise ValueError("EPUB 阅读顺序引用了不存在的章节。")
        path = epub_item_path(package_path, item.get("href", ""))
        if path in encrypted:
            raise ValueError("EPUB 正文已加密，无法导入；请提供无 DRM 的版本。")
        if ref.get("linear") == "no" or item.get("media-type") not in BODY_MEDIA_TYPES:
            yield item, path, None
            continue
        content = book.read(path)
        if item.get("media-type") == "application/xhtml+xml":
            document = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True))
            content = etree.tostring(document, encoding="unicode")
        yield item, path, content


def extract_text_source(filename: str, data: bytes, *, per_chapter: bool = False):
    """Return paragraphs, omitted-content records and factual import metadata.

    ``per_chapter=True``（结构解析用）额外把 EPUB 每个阅读顺序章节的段落原样
    分组返回：``(章节段落列表, unsupported, facts)``；其余格式只有一组。

    EPUB 里的图片不再算作“未纳入内容”：译文 EPUB / DOCX 按阅读顺序保留原图。
    图片本身不进入翻译单元，Markdown 图片仍按原行为忽略。
    """
    lower = filename.lower()
    omitted = {}
    facts = {}
    chapter_groups: list[list[str]] = []
    if lower.endswith(".epub"):
        with open_epub(data) as (book, package_path, _package, manifest, spine, encrypted):
            paragraphs = []
            chapters = 0
            for _item, _path, content in epub_spine_entries(
                    book, package_path, manifest, spine, encrypted):
                if content is None:
                    omitted["non_body"] = omitted.get("non_body", 0) + 1
                    continue
                chapter, missing = _html_paragraphs(content)
                paragraphs.extend(chapter)
                chapter_groups.append(list(chapter))
                chapters += 1
                for kind, count in missing.items():
                    # EPUB images are preserved by the edition exports, so they
                    # are not an unsupported-content failure (unlike Markdown).
                    if kind == "image":
                        continue
                    omitted[kind] = omitted.get(kind, 0) + count
            facts["chapters"] = chapters
    elif lower.endswith((".md", ".markdown")):
        markup = MarkdownIt("commonmark").enable("table").render(decode_text(data))
        paragraphs, omitted = _html_paragraphs(markup) if markup.strip() else ([], {})
    else:
        text = decode_text(data)
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    labels = {"code": "代码块", "image": "图片", "media": "音视频", "non_body": "非正文阅读项"}
    unsupported = [{"kind": kind, "label": labels[kind], "count": count,
                    "detail": "未纳入文本翻译；保留在源文件中。", "sample": ""}
                   for kind, count in omitted.items()]
    if per_chapter:
        if not chapter_groups and paragraphs:
            chapter_groups = [list(paragraphs)]
        return chapter_groups, unsupported, facts
    return paragraphs, unsupported, facts
