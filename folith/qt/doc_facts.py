"""文档概览：开始翻译之前就能在本地读出的事实（不调用模型）。

「准备」页左栏用它说明"这份文档是什么样的"：页数、哪些页没有文字层（需要识别）、
图片数、章节数。全部来自任务留存的源文件（``source.bin``），在后台线程里计算，
按源文件身份缓存（与缩略图共用 :class:`folith.qt.thumbnails.PreviewLoader`）。

读不出来的格式或损坏文件返回只有 ``format`` 与 ``bytes`` 的事实，不报错。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

#: 一页至少有这么多可见字符才算"有文字层"（页眉页码这类零碎不算）。
MIN_TEXT_CHARS = 20
#: 逐页检查的上限：超长 PDF 只看前这么多页，并如实标记。
MAX_PAGES_SCANNED = 2000


def _pdf_facts(data: bytes) -> dict:
    import fitz

    facts: dict = {}
    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass:
            facts["encrypted"] = True
            return facts
        facts["pages"] = document.page_count
        without_text: list[int] = []
        images: set[int] = set()
        for index in range(min(document.page_count, MAX_PAGES_SCANNED)):
            page = document[index]
            for item in page.get_images(full=False):
                images.add(int(item[0]))
            text = "".join(page.get_text("text").split())
            if len(text) < MIN_TEXT_CHARS:
                without_text.append(index + 1)
        facts["pages_without_text"] = without_text
        facts["images"] = len(images)
        facts["partial"] = document.page_count > MAX_PAGES_SCANNED
    return facts


def _epub_facts(data: bytes) -> dict:
    from folith.qt.thumbnails import _epub_package

    with zipfile.ZipFile(io.BytesIO(data)) as book:
        package, _path = _epub_package(book)
        if package is None:
            return {}
        items = package.findall("./{*}manifest/{*}item")
        spine = package.findall("./{*}spine/{*}itemref")
        return {
            "chapters": len(spine),
            "images": sum(1 for item in items
                          if str(item.get("media-type") or "").startswith("image/")),
        }


def _docx_facts(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        names = package.namelist()
    return {"images": sum(1 for name in names
                          if name.startswith("word/media/") and not name.endswith("/"))}


def document_facts(source: Path, filename: str) -> dict:
    """读源文件，返回概览事实。"""
    suffix = Path(str(filename or "")).suffix.lower()
    data = Path(source).read_bytes()
    facts: dict = {"format": suffix.lstrip(".").upper(), "bytes": len(data)}
    if not data:
        return facts
    try:
        if suffix == ".pdf":
            facts.update(_pdf_facts(data))
        elif suffix == ".epub":
            facts.update(_epub_facts(data))
        elif suffix == ".docx":
            facts.update(_docx_facts(data))
    except Exception:  # noqa: BLE001 - 读不出的细节只是不显示
        pass
    return facts


def page_ranges(pages: list[int]) -> str:
    """[3, 4, 5, 9] -> "3–5、9"（概览里列出没有文字层的页）。"""
    if not pages:
        return ""
    parts: list[str] = []
    start = previous = pages[0]
    for page in pages[1:] + [None]:
        if page is not None and page == previous + 1:
            previous = page
            continue
        parts.append(str(start) if start == previous else f"{start}–{previous}")
        if page is not None:
            start = previous = page
    return "、".join(parts)


_LOADER = None


def loader():
    """进程内唯一的事实加载器（与缩略图同一套后台与缓存机制）。"""
    global _LOADER
    if _LOADER is None:
        from folith.qt.thumbnails import PreviewLoader

        _LOADER = PreviewLoader(render=document_facts)
    return _LOADER
