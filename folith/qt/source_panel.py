"""流式源（DOCX / EPUB / TXT / Markdown）的「原位置」上下文面板。

PDF 任务在左侧看原页（``pdf_panel``）；这些格式没有页码，面板展示的是片段的
原始上下文——章节 / 标题路径 / 行号，加上相邻段落，高亮匹配到的块。解析在
后台线程做（整本书解析要以秒计），完成前先如实说「正在读取」；定位与渲染
只做展示，位置由适配器（``folith.source_context``）给出。
"""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from . import adapter
from . import i18n
from . import icons

#: 与 PDF 原页高亮同一种暖黄，两个面板的「找到了」是同一种颜色。
_HIGHLIGHT_STYLE = "background-color:#ffe08a;color:#202124;"


class _LoadSignals(QObject):
    done = Signal(int)


class _LoadTask(QRunnable):
    """后台解析一次源文件（结果缓存在 ``source_context`` 里），完成即通知。"""

    def __init__(self, generation: int, job_id: str) -> None:
        super().__init__()
        self.signals = _LoadSignals()
        self.args = (generation, job_id)

    def run(self) -> None:  # pragma: no cover - exercised through the pool
        generation, job_id = self.args
        try:
            adapter.source_document_blocks(job_id)
        except Exception:  # noqa: BLE001 - 读不出来的任务在定位时如实说明
            pass
        self.signals.done.emit(generation)


class SourcePanel(QWidget):
    closeRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.job_id = ""
        self._segment = -1
        self._generation = 0
        self._ready = False
        self._pending = -1
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._task: _LoadTask | None = None

        def nav(name: str, tip: str) -> QToolButton:
            button = QToolButton()
            button.setObjectName("roundButton")
            button.setIcon(icons.icon(name, 16, "text-secondary"))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            return button

        self.title = QLabel(i18n.tr('原位置'))
        self.title.setObjectName("sectionTitle")
        self.close_button = nav("x", i18n.tr('收起原位置'))
        self.close_button.clicked.connect(self.closeRequested.emit)
        self.location = QLabel("")
        self.location.setObjectName("hint")
        self.location.setWordWrap(True)
        self.locate_button = QPushButton(i18n.tr('定位当前段'))
        self.locate_button.setIcon(icons.icon("eye", 16, "text-secondary"))
        self.locate_button.setIconSize(QSize(16, 16))
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setAccessibleName(i18n.tr('原文上下文'))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.title, 1)
        head.addWidget(self.close_button)
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.locate_button)
        bar.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addLayout(head)
        layout.addLayout(bar)
        layout.addWidget(self.location)
        layout.addWidget(self.hint)
        layout.addWidget(self.view, 1)

        self.locate_button.clicked.connect(lambda: self.locate(self._segment))
        self._sync()

    # ================= 数据 =================
    def load(self, job_id: str) -> None:
        if job_id == self.job_id:
            return
        self.job_id = job_id
        self._generation += 1
        self._ready = False
        self._segment = -1
        self._pending = -1
        self.location.clear()
        self.hint.clear()
        self.view.clear()
        if job_id:
            task = _LoadTask(self._generation, job_id)
            task.setAutoDelete(False)
            task.signals.done.connect(self._loaded)
            self._task = task
            self._pool.start(task)
        self._sync()

    def set_segment(self, index: int) -> None:
        """工作台选中的段落变了：记下来，「定位当前段」据此工作。"""
        self._segment = int(index)
        self._sync()

    def locate(self, index: int, state: dict | None = None) -> None:
        """展示第 ``index`` 段的原文上下文并高亮（``state``：调用方已读好的状态）。"""
        if index < 0 or not self.job_id:
            return
        self._segment = int(index)
        if not self._blocks_ready():
            self._pending = int(index)
            self.hint.setText(i18n.tr('正在读取源文件…'))
            return
        context = adapter.segment_source_context(self.job_id, int(index), state=state)
        self._show_context(context)

    def _blocks_ready(self) -> bool:
        if self._ready:
            return True
        if not self.window().isVisible():
            # 后台脚本 / offscreen 测试：同步解析，行为可预期。
            adapter.source_document_blocks(self.job_id)
            self._ready = True
            return True
        return False

    def _loaded(self, generation: int) -> None:
        if generation != self._generation:
            return
        self._ready = True
        if self._pending >= 0:
            index, self._pending = self._pending, -1
            self.locate(index)

    def _show_context(self, context: dict) -> None:
        self.location.setText(i18n.tr_dynamic(str(context.get("title") or "")))
        self.hint.setText(i18n.tr_dynamic(str(context.get("hint") or "")))
        blocks = context.get("blocks") or []
        if not context.get("found") or not blocks:
            self.view.clear()
            self._sync()
            return
        parts: list[str] = []
        anchor = ""
        for offset, block in enumerate(blocks):
            text = escape(str(block.get("text") or ""))
            if block.get("highlight"):
                text = f'<span style="{_HIGHLIGHT_STYLE}">{text}</span>'
                anchor = anchor or f"blk{offset}"
            tag = "h3" if block.get("role") == "heading" else "p"
            parts.append(f'<a name="blk{offset}"></a><{tag}>{text}</{tag}>')
        self.view.setHtml("".join(parts))
        if anchor:
            self.view.scrollToAnchor(anchor)
        self._sync()

    def _sync(self) -> None:
        self.locate_button.setEnabled(self._segment >= 0 and bool(self.job_id))
