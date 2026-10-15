"""「准备」页左栏：这份文档本身。

用户在开始之前要先确认"agent 读到的是什么"：第一页 / 封面、文件名、页数与大小，
以及几条用用户能理解的话写的注意事项（哪些页没有文字层需要识别、含多少图片）。
全部来自本地读取（:mod:`folith.qt.doc_facts`、:mod:`folith.qt.thumbnails`），
不调用模型；读不出来的项就不显示，不猜。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from . import adapter
from . import components
from . import i18n
from . import icons
from . import theme as theme_module


def _human_size(size: int) -> str:
    value = float(max(0, int(size or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""


def describe(facts: dict, *, source_lang: str = "") -> tuple[str, list[tuple[str, str]]]:
    """事实 → （一行元信息, [(语气, 一句注意事项)]）。语气是 ``ok`` / ``warn``。

    纯函数：测试与界面共用同一套措辞。
    """
    meta: list[str] = []
    if facts.get("format"):
        meta.append(str(facts["format"]))
    if facts.get("pages"):
        meta.append(i18n.tr('{count} 页', count=int(facts["pages"])))
    if facts.get("chapters"):
        meta.append(i18n.tr('{count} 章', count=int(facts["chapters"])))
    if facts.get("bytes"):
        meta.append(_human_size(int(facts["bytes"])))
    if source_lang:
        from folith.source_languages import display_name

        meta.append(i18n.tr('原文：{v0}', v0=display_name(source_lang)))
    notes: list[tuple[str, str]] = []
    if facts.get("encrypted"):
        notes.append(("warn", i18n.tr('文件已加密，无法读取内容')))
    pages = int(facts.get("pages") or 0)
    missing = list(facts.get("pages_without_text") or [])
    if pages and "pages_without_text" in facts:
        if not missing:
            notes.append(("ok", i18n.tr('每页都有文字层，可直接读取正文')))
        elif len(missing) >= pages:
            notes.append(("warn", i18n.tr('整份文档没有文字层，正文需要识别')))
        else:
            from .doc_facts import page_ranges

            notes.append(("warn", i18n.tr('第 {pages} 页没有文字层，需要识别',
                                          pages=page_ranges(missing))))
    if facts.get("images"):
        notes.append(("ok", i18n.tr('含 {count} 张图片', count=int(facts["images"]))))
    return " · ".join(meta), notes


class DocumentOverview(QWidget):
    """左栏：预览 + 文件名 + 元信息 + 注意事项。"""

    WIDTH = 260

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self._job_id = ""
        self._filename = ""
        self._source_lang = ""

        self.paper = components.DocThumb(200, 260)
        self.name_label = QLabel("")
        self.name_label.setObjectName("overviewTitle")
        self.name_label.setWordWrap(True)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("hint")
        self.meta_label.setWordWrap(True)
        # 读取中：文件名下面一条细的不确定进度条和一句话，读完就收起。没有它，
        # 大文件在这里会空白好几秒，看起来像卡住了。
        self.loading = QWidget()
        loading_layout = QVBoxLayout(self.loading)
        loading_layout.setContentsMargins(0, 0, 0, 0)
        loading_layout.setSpacing(theme_module.SPACE["xs"])
        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 0)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setFixedHeight(4)
        self.loading_label = QLabel(i18n.tr('正在读取文档…'))
        self.loading_label.setObjectName("hint")
        loading_layout.addWidget(self.loading_bar)
        loading_layout.addWidget(self.loading_label)
        self.loading.setVisible(False)
        self.notes = QVBoxLayout()
        self.notes.setContentsMargins(0, 0, 0, 0)
        self.notes.setSpacing(theme_module.SPACE["xs"])

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme_module.SPACE["sm"])
        column.addWidget(self.paper, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(theme_module.SPACE["xs"])
        column.addWidget(self.name_label)
        column.addWidget(self.meta_label)
        column.addWidget(self.loading)
        column.addSpacing(theme_module.SPACE["xs"])
        column.addLayout(self.notes)
        column.addStretch(1)

        from . import doc_facts, thumbnails

        self._previews = thumbnails.loader()
        self._facts = doc_facts.loader()
        self._previews.ready.connect(self._ready)
        self._facts.ready.connect(self._ready)

    def set_document(self, job_id: str, filename: str, *, source_lang: str = "") -> None:
        changed = job_id != self._job_id
        self._job_id = str(job_id or "")
        self._filename = str(filename or "")
        self._source_lang = str(source_lang or "")
        self.name_label.setText(self._filename)
        self.name_label.setToolTip(self._filename)
        if changed:
            self.paper.set_image(None)
            self.paper.set_document(self._filename)
        self._refresh()

    def _ready(self, job_id: str) -> None:
        if job_id and job_id == self._job_id:
            self._refresh()

    def _refresh(self) -> None:
        if not self._job_id:
            return
        source = adapter.source_path(self._job_id)
        image = self._previews.request(self._job_id, source, self._filename, priority=10)
        if image is not None:
            self.paper.set_image(image)
        facts = self._facts.request(self._job_id, source, self._filename, priority=10)
        self.loading.setVisible(facts is None and self._facts.pending(self._job_id))
        meta, notes = describe(facts or {}, source_lang=self._source_lang)
        self.meta_label.setText(meta)
        self.meta_label.setVisible(bool(meta))
        self._set_notes(notes)

    def note_texts(self) -> list[str]:
        texts = []
        for index in range(self.notes.count()):
            row = self.notes.itemAt(index).widget()
            label = row.findChild(QLabel, "overviewNote") if row else None
            if label is not None:
                texts.append(label.text())
        return texts

    def _set_notes(self, notes: list[tuple[str, str]]) -> None:
        while self.notes.count():
            item = self.notes.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()  # before detaching (see segment_assist._clear)
                widget.setParent(None)
                widget.deleteLater()
        for tone, text in notes:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(theme_module.SPACE["xs"] + 2)
            # 颜色只用来提示"需要注意"：一切正常的事实用中性色。
            mark = icons.IconLabel("alert" if tone == "warn" else "check", 14,
                                   "warning" if tone == "warn" else "text-muted")
            label = QLabel(text)
            label.setObjectName("overviewNote")
            label.setWordWrap(True)
            layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label, 1)
            self.notes.addWidget(row)
