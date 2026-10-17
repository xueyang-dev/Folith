"""边识别边翻译：扫描书的视觉识别在后台线程里跑，翻译跟在后面。

整本扫描书走视觉模型时，原来要等全部页读完才开始翻译（300 页约半小时）。这里把
识别放到后台线程：:class:`ScanStream` 逐页把结果交给
:class:`~folith.document_understanding.VisualDocumentAssembler`，主线程按窗口
（第一窗口 5 页，之后 20 页）取走已经定稿的段落、追加到翻译单元里，再翻这一批；翻译期间识别
继续往前跑。

线程边界刻意很窄：后台线程只碰 assembler（加锁），任务状态 ``state`` 只由主线程
读写。末段可能被下一页续上，所以没定稿之前不交给主线程（见
``VisualDocumentAssembler.committed_paragraph_count``）。
"""
from __future__ import annotations

import copy
import threading
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import fitz

from folith.document_understanding import VisualDocumentAssembler, iter_page_understanding

#: 后续窗口：每攒够这么多页就交给翻译一次，批量大些效率高。
DEFAULT_WINDOW_PAGES = 20
#: 第一窗口：满这么多页就先开始翻译，用户不必等 20 页（慢模型每页可能 40–90 秒）。
FIRST_WINDOW_PAGES = 5
_WAIT_SLICE_S = 0.5


class ScanStream:
    """后台逐页识别一份 PDF，主线程按需取走定稿的段落。"""

    def __init__(self, filename: str, data: bytes, provider, *, spatial_engine=None,
                 checkpoint_dir=None, cancel_check: Optional[Callable[[], bool]] = None,
                 page_gate: Optional[Callable[[], bool]] = None,
                 workers: Optional[int] = None, dpi: int = 150,
                 window_pages: int = DEFAULT_WINDOW_PAGES,
                 first_window_pages: int = FIRST_WINDOW_PAGES):
        with fitz.open(stream=data, filetype="pdf") as pdf:
            self.pages_total = len(pdf)
        self.window_pages = max(1, int(window_pages or DEFAULT_WINDOW_PAGES))
        self.first_window_pages = max(1, int(first_window_pages or FIRST_WINDOW_PAGES))
        self.pages_done = 0
        self.pages_retried = 0  # 重试过才读成的页（模型曾中途失败）
        self.finished = False
        self.error: Optional[BaseException] = None
        # 工作线程里的页级重试 / 失败事件（deque.append 线程安全），主线程在 tick 里取走。
        self._page_events: deque = deque(maxlen=200)
        self._assembler = VisualDocumentAssembler(filename)
        self._result = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._cancel_check = cancel_check

        # ``page_gate`` 在读每一页之前调用（可阻塞，用于暂停），返回是否取消；
        # 只在识别线程里用，主线程等待时只看 ``cancel_check``。
        gate = page_gate or cancel_check

        def cancelled():
            return self._stop.is_set() or bool(gate and gate())

        self._pages = iter_page_understanding(
            filename, data, provider, spatial_engine=spatial_engine, dpi=dpi,
            cancel_check=cancelled, checkpoint_dir=checkpoint_dir, workers=workers,
            page_event_sink=self._page_events.append)
        self._thread = threading.Thread(target=self._run, name="scan-stream", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for envelope, result in self._pages:
                with self._cond:
                    self._assembler.add_page(envelope, result)
                    self.pages_done += 1
                    if self._assembler.diagnostics and int(
                            self._assembler.diagnostics[-1].get("attempt_count") or 0) > 1:
                        self.pages_retried += 1
                    self._cond.notify_all()
            with self._cond:
                self._result = self._assembler.finish()
                self.finished = True
                self._cond.notify_all()
        except BaseException as exc:  # noqa: BLE001 - handed to the main thread
            with self._cond:
                self.error = exc
                self._cond.notify_all()
        finally:
            self._pages.close()

    def drain_page_events(self) -> List[dict]:
        """取走自上次以来工作线程报告的页级事件（重试 / 失败）。"""
        events = []
        while True:
            try:
                events.append(self._page_events.popleft())
            except IndexError:
                return events

    def wait_for_pages(self, pages: int,
                       on_tick: Optional[Callable[[int, int, int], None]] = None) -> None:
        """等到至少识别完 ``pages`` 页（或全部完成）。识别出错时在这里抛出。

        ``on_tick(pages_done, pages_total, pages_retried)`` 在锁外调用：每完成一页、
        以及每个等待间隔各一次。调用方在这里更新进度、响应暂停（可阻塞）或抛出取消；
        暂停时识别线程被 ``page_gate`` 拦住，不会等不到窗口而互相卡死。
        """
        target = min(int(pages), self.pages_total)
        while True:
            with self._cond:
                if self.error is not None:
                    raise self.error
                if self.finished or (self.pages_done >= target
                                     and target < self.pages_total):
                    return
                if self._cancel_check and self._cancel_check():
                    raise RuntimeError("任务已请求取消")
                seen = self.pages_done
                self._cond.wait_for(
                    lambda: self.error is not None or self.finished
                    or self.pages_done != seen, _WAIT_SLICE_S)
                done, retried = self.pages_done, self.pages_retried
            if on_tick is not None:
                on_tick(done, self.pages_total, retried)

    def snapshot(self) -> Dict[str, Any]:
        """当前可以定稿的段落和对应的 DocumentIR（深拷贝，后台线程不会再改它）。"""
        with self._cond:
            if self.error is not None:
                raise self.error
            if self.finished:
                paragraphs, document = self._result
                committed = len(paragraphs)
            else:
                paragraphs = self._assembler.paragraphs
                document = self._assembler.document
                committed = self._assembler.committed_paragraph_count()
            document = copy.deepcopy(document)
            document["provenance"] = {
                "provider": "vlm",
                "page_diagnostics": copy.deepcopy(self._assembler.diagnostics),
            }
            return {
                "paragraphs": list(paragraphs[:committed]),
                "document": document,
                "pages_done": self.pages_done,
                "pages_total": self.pages_total,
                "finished": self.finished,
            }

    def close(self) -> None:
        """停止后台识别（已在途的请求跑完即止）。"""
        self._stop.set()
        self._thread.join(timeout=120)
