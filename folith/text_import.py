"""Text and reflowable ebook ingestion into the shared paragraph pipeline."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from urllib.parse import unquote

from defusedxml import ElementTree
from lxml import etree, html
from markdown_it import MarkdownIt


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


def _html_paragraphs(markup: str | bytes):
    if isinstance(markup, bytes):
        markup = decode_text(markup)
    root = html.fromstring(markup)
    def tag_name(element):
        return str(element.tag).split("}")[-1].split(":")[-1].lower()
    omitted = {}
    for element in list(root.iter()):
        tag = tag_name(element)
        if tag in {"head", "script", "style", "pre", "img", "svg", "audio", "video"}:
            if tag not in {"head", "script", "style"}:
                kind = "code" if tag == "pre" else "image" if tag in {"img", "svg"} else "media"
                omitted[kind] = omitted.get(kind, 0) + 1
            element.drop_tree()
    body = next((node for node in root.iter() if tag_name(node) == "body"), root)
    blocks = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "div", "section"}
    paragraphs = []
    # Flush around block boundaries; nested lists/divs never duplicate text.
    pending = []
    def flush():
        text = re.sub(r"\s+", " ", "".join(pending)).strip()
        if text:
            paragraphs.append(text)
        pending.clear()
    def walk(node):
        tag = tag_name(node)
        block = tag in blocks
        if block:
            flush()
        if node.text:
            pending.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                if tag_name(child) == "br":
                    pending.append(" ")
                else:
                    walk(child)
                if tag_name(child) in {"td", "th"}:
                    pending.append(" | ")
            if child.tail:
                pending.append(child.tail)
        if block:
            flush()
    walk(body)
    flush()
    return paragraphs, omitted


def extract_text_source(filename: str, data: bytes):
    """Return paragraphs, omitted-content records and factual import metadata."""
    lower = filename.lower()
    omitted = {}
    facts = {}
    if lower.endswith(".epub"):
        with zipfile.ZipFile(io.BytesIO(data)) as book:
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
            paragraphs = []
            chapters = 0
            for ref in spine:
                item = manifest.get(ref.get("idref"))
                if item is None:
                    raise ValueError("EPUB 阅读顺序引用了不存在的章节。")
                path = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), unquote(item.get("href", "").split("#")[0])))
                if path in encrypted:
                    raise ValueError("EPUB 正文已加密，无法导入；请提供无 DRM 的版本。")
                if ref.get("linear") == "no" or item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                    omitted["non_body"] = omitted.get("non_body", 0) + 1
                    continue
                content = book.read(path)
                if item.get("media-type") == "application/xhtml+xml":
                    document = etree.fromstring(content, etree.XMLParser(resolve_entities=False, no_network=True))
                    content = etree.tostring(document, encoding="unicode")
                chapter, missing = _html_paragraphs(content)
                paragraphs.extend(chapter)
                chapters += 1
                for kind, count in missing.items():
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
    return paragraphs, unsupported, facts
