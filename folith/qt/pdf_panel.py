"""PDF 原页对照：把一段译文对回原页，并高亮它的位置。

只做展示：位置与渲染由适配器（``folith.pdf_preview``）给出。没有行级坐标的来源只能
定位到页，页面会明说"仅定位页面"，不会假装高亮。

它是工作台左侧可展开的面板（acceptance-feedback D2）：选中的段落变了就跟着翻到
那一页并高亮。渲染一页要几百毫秒，所以在可见的窗口里放到后台线程做；渲染完成前
先保留上一张图，过期的结果直接丢掉。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout,
    QWidget,
)

from . import adapter
from . import i18n
from . import icons
from . import style as style_module

#: (dpi, label).  0 = fit the panel width (rendered at 150 dpi, scaled locally).
ZOOMS = ((0, "适合宽度"), (100, "100%"), (150, "150%"), (200, "200%"))
_FIT_DPI = 150


class _RenderSignals(QObject):
    done = Signal(int, bytes)


class _RenderTask(QRunnable):
    def __init__(self, generation: int, job_id: str, page: int, rects: list, dpi: int) -> None:
        super().__init__()
        self.signals = _RenderSignals()
        self.args = (generation, job_id, page, rects, dpi)

    def run(self) -> None:  # pragma: no cover - exercised through the pool
        generation, job_id, page, rects, dpi = self.args
        try:
            png = adapter.pdf_page_png(job_id, page, rects, dpi=dpi) or b""
        except Exception:  # noqa: BLE001 - a page that cannot render says so
            png = b""
        self.signals.done.emit(generation, png)


class PdfPanel(QWidget):
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._render_pending = False
        self.job_id = ""
        self._page = 0
        self._pages = 0
        self._rects: list = []
        self._segment = -1
        self._generation = 0
        self._source = QPixmap()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._tasks: list = []

        def nav(name: str, tip: str) -> QToolButton:
            button = QToolButton()
            button.setObjectName("roundButton")
            button.setIcon(icons.icon(name, 16, "text-secondary"))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            return button

        self.title = QLabel(i18n.tr('原页'))
        self.title.setObjectName("sectionTitle")
        self.prev_button = nav("chevron-left", i18n.tr('上一页'))
        self.next_button = nav("chevron-right", i18n.tr('下一页'))
        self.close_button = nav("x", i18n.tr('收起原页'))
        self.close_button.clicked.connect(self.closeRequested.emit)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.total_label = QLabel("/ —")
        self.zoom_box = style_module.ComboBox()
        for dpi, label in ZOOMS:
            self.zoom_box.addItem(i18n.tr(label), dpi)
        self.zoom_box.setCurrentIndex(0)
        self.locate_button = QPushButton(i18n.tr('定位当前段'))
        self.locate_button.setIcon(icons.icon("eye", 16, "text-secondary"))
        self.locate_button.setIconSize(QSize(16, 16))
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.image)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.title, 1)
        head.addWidget(self.close_button)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        for widget in (self.prev_button, self.page_spin, self.total_label, self.next_button,
                       self.zoom_box, self.locate_button):
            bar.addWidget(widget)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(head)
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
        self._source = QPixmap()
        self.image.clear()
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
        new_rects = list(rects) if rects is not None else (
            self._rects if page == self._page else [])
        unchanged = page == self._page and new_rects == self._rects and not self._source.isNull()
        self._rects = new_rects
        self._page = page
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        if unchanged:
            self._sync()
            return
        self._render()

    def locate(self, index: int, state: dict | None = None) -> None:
        """跳到第 ``index`` 段所在的原页并高亮（``state``：调用方已读好的任务状态）。"""
        if index < 0 or not self.job_id:
            return
        self._segment = int(index)
        location = adapter.segment_pdf_location(self.job_id, index, state=state,
                                                current_page=self._page)
        if location["page"] is None:
            self.hint.setText(i18n.tr_dynamic(location["hint"]))
            return
        self.goto(location["page"], location["rects"])
        self.hint.setText(i18n.tr('第 {v0} 段：{v1}', v0=index + 1, v1=i18n.tr_dynamic(location["hint"])))

    # ================= 渲染 =================
    def _dpi(self) -> int:
        return int(self.zoom_box.currentData() or 0) or _FIT_DPI

    def _render(self) -> None:
        self._sync()
        if not self.job_id or not self._pages:
            self.image.clear()
            return
        in_window = self.window().isVisible()
        if in_window and not self.isVisible():
            # Rendering a page costs ~0.6 s; opening a task used to pay it even
            # when the original page was never looked at.  Render on show.
            self._render_pending = True
            return
        self._render_pending = False
        self._generation += 1
        if not in_window:
            # Not on screen (scripts, tests): render synchronously.
            self._show_png(adapter.pdf_page_png(self.job_id, self._page, self._rects,
                                                dpi=self._dpi()))
            return
        task = _RenderTask(self._generation, self.job_id, self._page, list(self._rects),
                           self._dpi())
        task.setAutoDelete(False)
        task.signals.done.connect(self._rendered)
        self._tasks.append(task)
        self._pool.start(task)

    def _rendered(self, generation: int, png: bytes) -> None:
        self._tasks = [task for task in self._tasks if task.args[0] > generation]
        if generation != self._generation:
            return  # a newer page or zoom was asked for meanwhile
        self._show_png(png)

    def _show_png(self, png: bytes | None) -> None:
        if not png:
            self._source = QPixmap()
            self.image.setText(i18n.tr('这一页无法渲染。'))
            return
        pixmap = QPixmap()
        pixmap.loadFromData(png)
        self._source = pixmap
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self._source.isNull():
            return
        if int(self.zoom_box.currentData() or 0) == 0:
            width = max(120, self.scroll.viewport().width() - 8)
            ratio = self.devicePixelRatioF() or 1.0
            scaled = self._source.scaledToWidth(int(width * ratio),
                                                Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.image.setPixmap(scaled)
        else:
            self.image.setPixmap(self._source)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if int(self.zoom_box.currentData() or 0) == 0:
            self._apply_zoom()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if self._render_pending:
            self._render()

    def _sync(self) -> None:
        self.prev_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(bool(self._pages) and self._page < self._pages - 1)
        self.locate_button.setEnabled(self._segment >= 0 and bool(self._pages))
        self.page_spin.setEnabled(bool(self._pages))

    def current_page(self) -> int:
        return self._page
