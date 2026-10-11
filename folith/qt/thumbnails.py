"""任务卡缩略图：PDF 第一页、EPUB 封面，其余格式把开头的正文排成一页。

首页卡片原来画的是一张与文档无关的"示意纸"。这里读任务留存的源文件
（``source.bin``），生成一张真实的预览图：

* PDF：第一页（MuPDF 直接渲染，加密 PDF 不渲染）；
* EPUB：书的封面（EPUB 3 的 ``cover-image``，或 EPUB 2 的 ``<meta name="cover">``）；
  没有封面就退回到正文第一页；
* DOCX / TXT / Markdown：把开头几段正文排在一张白纸上，缩小后就是"第一页"的样子。

渲染放在后台线程（只产生 ``QImage``，不碰任何控件），结果按
"任务 + 源文件修改时间 + 大小"缓存；读不出来的文件也记一次失败，卡片保持示意纸，
不会每次刷新都重读。
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QObject, QRectF, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter

#: 渲染用的纸张宽度（px）。卡片上只显示约 70px 宽，按 2 倍以上渲染，Retina 上也不糊。
PAGE_WIDTH = 240
#: A4 纵横比。
PAGE_RATIO = 297 / 210
#: 太大的封面图不解码（异常文件保护）。
MAX_COVER_BYTES = 20 * 1024 * 1024
#: 文本页只取开头这么多段，够排满一页即可。
MAX_TEXT_PARAGRAPHS = 40

_TEXT_SUFFIXES = (".txt", ".md", ".markdown")


def _pdf_first_page(data: bytes) -> QImage | None:
    import fitz

    with fitz.open(stream=data, filetype="pdf") as document:
        if document.needs_pass or document.page_count < 1:
            return None
        page = document[0]
        zoom = PAGE_WIDTH / max(1.0, page.rect.width)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = QImage.fromData(pixmap.tobytes("png"), "PNG")
    return None if image.isNull() else image


def _epub_package(book: zipfile.ZipFile):
    from defusedxml import ElementTree

    container = ElementTree.fromstring(book.read("META-INF/container.xml"))
    rootfile = container.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        return None, ""
    path = rootfile.get("full-path")
    return ElementTree.fromstring(book.read(path)), path


def _epub_cover(data: bytes) -> QImage | None:
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        package, package_path = _epub_package(book)
        if package is None:
            return None
        items = package.findall("./{*}manifest/{*}item")
        images = [item for item in items
                  if str(item.get("media-type") or "").startswith("image/")]
        cover = next((item for item in images
                      if "cover-image" in str(item.get("properties") or "").split()), None)
        if cover is None:
            meta = next((node for node in package.findall("./{*}metadata/{*}meta")
                         if node.get("name") == "cover"), None)
            wanted = meta.get("content") if meta is not None else None
            cover = next((item for item in images if wanted and item.get("id") == wanted), None)
        if cover is None:
            cover = next((item for item in images
                          if "cover" in str(item.get("id") or "").lower()
                          or "cover" in str(item.get("href") or "").lower()), None)
        if cover is None:
            return None
        href = unquote(str(cover.get("href") or "").split("#")[0])
        name = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), href))
        try:
            info = book.getinfo(name)
        except KeyError:
            return None
        if info.file_size > MAX_COVER_BYTES:
            return None
        image = QImage.fromData(book.read(name))
    return None if image.isNull() else image


def _docx_paragraphs(data: bytes) -> list[str]:
    from defusedxml import ElementTree

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    paragraphs: list[str] = []
    for node in root.iter(f"{namespace}p"):
        text = "".join(run.text or "" for run in node.iter(f"{namespace}t")).strip()
        if text:
            paragraphs.append(text)
            if len(paragraphs) >= MAX_TEXT_PARAGRAPHS:
                break
    return paragraphs


def _text_paragraphs(filename: str, data: bytes) -> list[str]:
    from folith import text_import

    paragraphs, _omitted, _facts = text_import.extract_text_source(filename, data)
    return [re.sub(r"\s+", " ", item).strip() for item in paragraphs[:MAX_TEXT_PARAGRAPHS]
            if str(item).strip()]


def _text_page(paragraphs: list[str]) -> QImage | None:
    """把开头的正文排在一张白纸上：第一段当标题加粗，其余按正文排，排满为止。"""
    if not paragraphs:
        return None
    width, height = PAGE_WIDTH, round(PAGE_WIDTH * PAGE_RATIO)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    margin = round(width * 0.1)
    area = QRectF(margin, margin, width - 2 * margin, height - 2 * margin)
    painter.setClipRect(area)
    flags = int(Qt.TextFlag.TextWordWrap)
    y = area.top()
    for index, text in enumerate(paragraphs):
        font = QFont()
        font.setPixelSize(13 if index == 0 else 7)
        font.setBold(index == 0)
        painter.setFont(font)
        painter.setPen(QColor("#1f2328" if index == 0 else "#3d434b"))
        box = QRectF(area.left(), y, area.width(), area.bottom() - y)
        used = painter.boundingRect(box, flags, text)
        painter.drawText(box, flags, text)
        y += used.height() + (7 if index == 0 else 3)
        if y >= area.bottom():
            break
    painter.end()
    return image


def render_preview(source: Path, filename: str) -> QImage | None:
    """读源文件，返回预览图；不认识的格式或读不出来时返回 ``None``（卡片保持示意纸）。"""
    suffix = Path(str(filename or "")).suffix.lower()
    data = Path(source).read_bytes()
    if not data:
        return None
    if suffix == ".pdf":
        return _pdf_first_page(data)
    if suffix == ".epub":
        return _epub_cover(data) or _text_page(_text_paragraphs(filename, data))
    if suffix == ".docx":
        return _text_page(_docx_paragraphs(data))
    if suffix in _TEXT_SUFFIXES:
        return _text_page(_text_paragraphs(filename, data))
    return None


class _Job(QRunnable):
    def __init__(self, loader: "PreviewLoader", key: tuple, source: Path, filename: str) -> None:
        super().__init__()
        self._loader = loader
        self._key = key
        self._source = source
        self._filename = filename

    def run(self) -> None:
        try:
            image = self._loader._render(self._source, self._filename)
        except Exception:  # noqa: BLE001 - 坏文件只是没有预览，不能影响首页
            image = None
        self._loader._finished.emit(self._key, image)


class PreviewLoader(QObject):
    """后台读源文件、按源文件身份缓存结果。``ready(job_id)`` 在主线程发出。

    默认产出预览图（:func:`render_preview`）；传入别的 ``render`` 就是同一套后台
    与缓存机制算别的东西（例如 :mod:`folith.qt.doc_facts` 的文档概览事实）。
    """

    ready = Signal(str)
    _finished = Signal(object, object)

    #: 缓存上限（张）。每张约 240×340 RGB32 ≈ 0.3 MB。
    LIMIT = 160

    def __init__(self, parent: QObject | None = None, *, render=None) -> None:
        super().__init__(parent)
        self._render = render or render_preview
        self._cache: dict[tuple, object] = {}
        self._pending: set[tuple] = set()
        self._pool = QThreadPool(self)
        # 一个线程就够：预览只在首页卡片出现，不值得与翻译 worker 抢 CPU。
        self._pool.setMaxThreadCount(1)
        self._finished.connect(self._store)

    @staticmethod
    def _key(job_id: str, source: Path) -> tuple | None:
        try:
            stat = Path(source).stat()
        except OSError:
            return None
        return (job_id, stat.st_mtime_ns, stat.st_size)

    def request(self, job_id: str, source: Path | None, filename: str) -> QImage | None:
        """有缓存就直接返回；否则排队生成，好了以后发 ``ready(job_id)``。"""
        if source is None:
            return None
        key = self._key(job_id, source)
        if key is None:
            return None
        if key in self._cache:
            return self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            self._pool.start(_Job(self, key, Path(source), filename))
        return None

    def _store(self, key: tuple, image) -> None:
        self._pending.discard(key)
        # 同一任务的旧版本预览（源文件被替换过）直接丢掉。
        for stale in [item for item in self._cache if item[0] == key[0] and item != key]:
            self._cache.pop(stale, None)
        if isinstance(image, QImage) and image.isNull():
            image = None
        self._cache[key] = image
        while len(self._cache) > self.LIMIT:
            self._cache.pop(next(iter(self._cache)))
        if self._cache[key] is not None:
            self.ready.emit(key[0])

    def wait(self, msecs: int = 5000) -> bool:
        """测试与自检用：等后台队列清空。"""
        return self._pool.waitForDone(msecs)


_LOADER: PreviewLoader | None = None


def loader() -> PreviewLoader:
    """进程内唯一的加载器（缓存跨首页刷新复用）。"""
    global _LOADER
    if _LOADER is None:
        _LOADER = PreviewLoader()
    return _LOADER
