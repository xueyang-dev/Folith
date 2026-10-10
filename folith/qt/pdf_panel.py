"""PDF 原页对照：把一段译文对回原页，并高亮它的位置。

只做展示：位置与渲染由适配器（``folith.pdf_preview``）给出。没有行级坐标的来源只能
定位到页，页面会明说"仅定位页面"，不会假装高亮。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import style as style_module

ZOOMS = ((100, "100%"), (150, "150%"), (200, "200%"))


class PdfPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = ""
        self._page = 0
        self._pages = 0
        self._rects: list = []
        self._segment = -1

        self.prev_button = QPushButton("◀")
        self.next_button = QPushButton("▶")
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.total_label = QLabel("/ —")
        self.zoom_box = style_module.ComboBox()
        for dpi, label in ZOOMS:
            self.zoom_box.addItem(label, dpi)
        self.zoom_box.setCurrentIndex(1)
        self.locate_button = QPushButton(i18n.tr('定位当前段'))
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.image)

        bar = QHBoxLayout()
        for widget in (self.prev_button, self.page_spin, self.total_label, self.next_button,
                       self.zoom_box, self.locate_button):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.hint)
        layout.addWidget(self.scroll, 1)

        self.prev_button.clicked.connect(lambda: self.goto(self._page - 1))
        self.next_button.clicked.connect(lambda: self.goto(self._page + 1))
        self.page_spin.valueChanged.connect(lambda value: self.goto(value - 1))
        self.zoom_box.currentIndexChanged.connect(lambda _i: self._render())
        self.locate_button.clicked.connect(lambda: self.locate(self._segment))
        self._sync()

    # ================= 数据 =================
    def load(self, job_id: str) -> None:
        if job_id == self.job_id:
            return
        self.job_id = job_id
        self._rects = []
        self._page = 0
        self._segment = -1
        self._pages = adapter.pdf_page_count(job_id) if job_id else 0
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, self._pages))
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.total_label.setText(f"/ {self._pages}" if self._pages else "/ —")
        self.hint.setText("" if self._pages else i18n.tr('无法加载 PDF 源文件或文档为空。'))
        self._render()

    def set_segment(self, index: int) -> None:
        """工作台选中的段落变了：记下来，"定位当前段"据此工作。"""
        self._segment = int(index)
        self._sync()

    def goto(self, page: int, rects: list | None = None) -> None:
        if not self._pages:
            return
        page = max(0, min(int(page), self._pages - 1))
        self._rects = list(rects) if rects is not None else (self._rects if page == self._page else [])
        self._page = page
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        self._render()

    def locate(self, index: int) -> None:
        """跳到第 ``index`` 段所在的原页并高亮。"""
        if index < 0 or not self.job_id:
            return
        self._segment = int(index)
        location = adapter.segment_pdf_location(self.job_id, index, current_page=self._page)
        if location["page"] is None:
            self.hint.setText(i18n.tr_dynamic(location["hint"]))
            return
        self.goto(location["page"], location["rects"])
        self.hint.setText(i18n.tr('第 {v0} 段：{v1}', v0=index + 1, v1=i18n.tr_dynamic(location["hint"])))

    # ================= 渲染 =================
    def _render(self) -> None:
        self._sync()
        if not self.job_id or not self._pages:
            self.image.clear()
            return
        png = adapter.pdf_page_png(self.job_id, self._page, self._rects,
                                   dpi=int(self.zoom_box.currentData() or 150))
        if not png:
            self.image.setText(i18n.tr('这一页无法渲染。'))
            return
        pixmap = QPixmap()
        pixmap.loadFromData(png)
        self.image.setPixmap(pixmap)

    def _sync(self) -> None:
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._pages and self._page < self._pages - 1)
        self.locate_button.setEnabled(self._segment >= 0 and bool(self._pages))
        self.page_spin.setEnabled(bool(self._pages))

    def current_page(self) -> int:
        return self._page
