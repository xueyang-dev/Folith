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

from folith.document_understanding import PageFailed, VisualDocumentAssembler, iter_page_understanding

#: 后续窗口：每攒够这么多页就交给翻译一次，批量大些效率高。
DEFAULT_WINDOW_PAGES = 20
#: 第一窗口：满这么多页就先开始翻译，用户不必等 20 页（慢模型每页可能 40–90 秒）。
FIRST_WINDOW_PAGES = 5
#: 连续这么多页都因服务商超时 / 不稳定而失败，就自动暂停并建议换视觉模型。
TIMEOUT_STREAK_LIMIT = 3
_WAIT_SLICE_S = 0.5


class _ProviderHub:
    """可以中途换掉的读页 provider：暂停后用户换了视觉模型，没读的页改用新连接。

    识别线程池里每个读页线程按需 ``clone()`` 自己的一份 provider（传输层把单次请求的
    诊断写在实例上）；``current`` 换掉之后，各线程在下一页请求前重新克隆，在途的请求
    由旧连接读完。
    """

    def __init__(self, provider):
        self.current = provider

    @property
    def cache_identity(self):
        return getattr(self.current, "cache_identity", None)

    def clone(self):
        return _HubWorker(self)

    def understand_page(self, envelope):
        return self.current.understand_page(envelope)


class _HubWorker:
    def __init__(self, hub):
        self._hub = hub
        self._seen = None
        self._provider = None

    def _resolve(self):
        current = self._hub.current
        if current is not self._seen:
            clone = getattr(current, "clone", None)
            self._provider = clone() if callable(clone) else current
            self._seen = current
        return self._provider

    @property
    def transport(self):
        return getattr(self._provider, "transport", None)

    def understand_page(self, envelope):
        return self._resolve().understand_page(envelope)


class ScanStream:
    """后台逐页识别一份 PDF，主线程按需取走定稿的段落。"""

    def __init__(self, filename: str, data: bytes, provider, *, spatial_engine=None,
                 checkpoint_dir=None, cancel_check: Optional[Callable[[], bool]] = None,
                 page_gate: Optional[Callable[[], bool]] = None,
                 workers: Optional[int] = None, dpi: int = 150,
                 window_pages: int = DEFAULT_WINDOW_PAGES,
                 first_window_pages: int = FIRST_WINDOW_PAGES,
                 late_pages: Optional[Dict[int, dict]] = None,
                 provider_label: str = "",
                 known_paragraphs: Optional[List[str]] = None,
                 timeout_streak_limit: int = TIMEOUT_STREAK_LIMIT):
        with fitz.open(stream=data, filetype="pdf") as pdf:
            self.pages_total = len(pdf)
        self.window_pages = max(1, int(window_pages or DEFAULT_WINDOW_PAGES))
        self.first_window_pages = max(1, int(first_window_pages or FIRST_WINDOW_PAGES))
        self.pages_done = 0
        self.provider_label = provider_label
        self.timeout_streak_limit = max(1, int(timeout_streak_limit))
        self.timeout_streak = 0
        # 连续超时触发后，识别线程在下一页前停住，直到主线程处理完（暂停→继续）。
        self._halted = threading.Event()
        # 上次没读成的页（page_index → 失败信息）：第一遍跳过，读完全书后补读，
        # 文字先接在末尾（已定稿并翻译的前文不受影响），段落自带页码，译完后按页序重排。
        self._late = {int(k): dict(v) for k, v in (late_pages or {}).items()}
        self._late_pending = set(self._late)
        self._resumed = False
        self.pages_retried = 0  # 重试过才读成的页（模型曾中途失败）
        self.finished = False
        self.error: Optional[BaseException] = None
        # 工作线程里的页级重试 / 失败事件（deque.append 线程安全），主线程在 tick 里取走。
        self._page_events: deque = deque(maxlen=200)
        self._assembler = VisualDocumentAssembler(filename, known_paragraphs)
        self._result = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._cancel_check = cancel_check

        # ``page_gate`` 在读每一页之前调用（可阻塞，用于暂停），返回是否取消；
        # 只在识别线程里用，主线程等待时只看 ``cancel_check``。
        gate = page_gate or cancel_check

        def cancelled():
            while (self._halted.is_set() and not self._stop.is_set()
                   and not (cancel_check and cancel_check())):
                threading.Event().wait(0.2)
            return self._stop.is_set() or bool(gate and gate())

        self._cancelled = cancelled
        self._filename, self._data, self._provider = filename, data, _ProviderHub(provider)
        self._spatial_engine, self._dpi = spatial_engine, dpi
        self._checkpoint_dir, self._workers = checkpoint_dir, workers

        self._pages = self._iterate(
            [i for i in range(self.pages_total) if i not in self._late]
            if self._late else None)
        self._thread = threading.Thread(target=self._run, name="scan-stream", daemon=True)
        self._thread.start()

    def _iterate(self, page_indices):
        return iter_page_understanding(
            self._filename, self._data, self._provider, spatial_engine=self._spatial_engine,
            dpi=self._dpi, cancel_check=self._cancelled, checkpoint_dir=self._checkpoint_dir,
            workers=self._workers, page_indices=page_indices,
            page_event_sink=self._page_events.append,
            skip_failed_pages=True, reuse_across_models=True)

    def _note_result(self, result) -> None:
        """按页序统计连续超时；够数就让识别线程停住等主线程处理。"""
        if isinstance(result, PageFailed):
            self.timeout_streak = self.timeout_streak + 1 if result.timeout_like else 0
            if self.timeout_streak >= self.timeout_streak_limit:
                self._halted.set()
        elif result is not None:
            self.timeout_streak = 0

    @property
    def streak_tripped(self) -> bool:
        return self._halted.is_set()

    def swap_provider(self, provider, label: str = "") -> None:
        """换视觉连接：之后才开始读的页（含补读）用新 provider，已识别的页缓存照常复用。"""
        self._provider.current = provider
        self.provider_label = label

    def clear_halt(self) -> None:
        """主线程已暂停并继续（用户可能已换模型）：重新计数，识别继续。"""
        with self._cond:
            self._resumed = True
            # 这次连续失败的页读完全书后补读一遍（换了模型多半能读成）。
            for failed in self._assembler.failed_pages.values():
                self._late.setdefault(failed.page_index, {
                    "page": failed.page_index + 1, "code": failed.code,
                    "http_status": failed.http_status, "reason": failed.describe()})
                self._late_pending.add(failed.page_index)
            self.timeout_streak = 0
            self._halted.clear()

    def failed_pages_info(self) -> List[dict]:
        """读失败、等待重试的页（页码从 1 起），并记下读它时用的连接。"""
        with self._cond:
            connection = self.provider_label or ""
            rows = {failed.page_index: {"page": failed.page_index + 1, "code": failed.code,
                                        "http_status": failed.http_status,
                                        "reason": failed.describe(),
                                        "connection": connection}
                    for failed in self._assembler.failed_pages.values()}
            for index in self._late_pending:
                rows.setdefault(index, {**self._late[index], "page": index + 1,
                                        "connection": self._late[index].get("connection")
                                        or connection})
        return [rows[key] for key in sorted(rows)]

    def _run(self) -> None:
        try:
            for envelope, result in self._pages:
                with self._cond:
                    self._assembler.add_page(envelope, result)
                    # Requests already in flight at resume can still fail on the
                    # old connection. Include those results in the same one-off
                    # repair pass as the failures collected by clear_halt().
                    if self._resumed and isinstance(result, PageFailed):
                        self._late.setdefault(result.page_index, {
                            "page": result.page_index + 1, "code": result.code,
                            "http_status": result.http_status, "reason": result.describe()})
                        self._late_pending.add(result.page_index)
                    self._note_result(result)
                    self.pages_done += 1
                    if self._assembler.diagnostics and int(
                            self._assembler.diagnostics[-1].get("attempt_count") or 0) > 1:
                        self.pages_retried += 1
                    self._cond.notify_all()
            if self._late:
                # 补读上次没读成的页：只处理它们，其余页的结果在第一遍里已经登记过。
                late_pages = self._iterate(sorted(self._late))
                try:
                    for envelope, result in late_pages:
                        if envelope.page_index not in self._late_pending:
                            continue
                        with self._cond:
                            self._assembler.add_page(envelope, result, late=True)
                            self._late_pending.discard(envelope.page_index)
                            self._note_result(result)
                            self.pages_retried += 0 if isinstance(result, PageFailed) else 1
                            self._cond.notify_all()
                finally:
                    late_pages.close()
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
        if on_tick is not None:
            on_tick(self.pages_done, self.pages_total, self.pages_retried)
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
                "paragraph_pages": list(self._assembler.paragraph_pages[:committed]),
                "document": document,
                "pages_done": self.pages_done,
                "pages_total": self.pages_total,
                "finished": self.finished,
                "failed_pages": self.failed_pages_info(),
            }

    def close(self) -> None:
        """停止后台识别（已在途的请求跑完即止）。"""
        self._stop.set()
        self._thread.join(timeout=120)
