"""Background execution for the Qt client.

Rule: a widget is only ever touched from the GUI thread.  Slow core calls
(document import, the model connection test, review, delivery, the exit wait)
run on a :class:`QRunnable`; the result comes back as a Qt signal carrying plain
Python values.

The core's own translation worker is not managed here: ``core.start_job_worker``
already owns that thread, its job lock and its runtime state, and duplicating
that lifecycle would create a second truth about whether a task is running.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from shiboken6 import isValid


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str, str)  # (message, traceback)
    progress = Signal(int, int)  # (done, total)


class Task(QRunnable):
    """One callable executed off the GUI thread.

    ``on_done``/``on_error`` are optional convenience callbacks invoked on the
    GUI thread (the signals are emitted there because the receiver lives there).
    """

    def __init__(self, fn: Callable[..., Any], *args: Any,
                 label: str = "", **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.label = label or getattr(fn, "__name__", "task")
        self.signals = _TaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # pragma: no cover - exercised through the pool
        try:
            value = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
            self.signals.failed.emit(str(exc) or type(exc).__name__,
                                     traceback.format_exc())
            return
        self.signals.done.emit(value)


class TaskRunner(QObject):
    """Owns the pool and keeps strong references to in-flight tasks."""

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 4) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(1, max_threads))
        self._running: list[Task] = []
        self._failures: list[dict] = []

    @property
    def busy(self) -> bool:
        return bool(self._running)

    @property
    def failures(self) -> list[dict]:
        return list(self._failures)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        label: str = "",
        **kwargs: Any,
    ) -> Task:
        """``on_progress``：``fn`` 收到一个 ``on_progress(done, total)`` 关键字参数，
        在工作线程里调用；回调在 GUI 线程上执行。"""
        task = Task(fn, *args, label=label, **kwargs)
        # Free-function slots may already be queued when the QObject owner is
        # destroyed. Do not let those callbacks touch its deleted widgets.
        if on_progress is not None:
            task.kwargs["on_progress"] = task.signals.progress.emit

            def _progress(done: int, total: int) -> None:
                if isValid(self):
                    on_progress(done, total)

            task.signals.progress.connect(_progress)
        self._running.append(task)

        def _finish() -> None:
            if task in self._running:
                self._running.remove(task)

        def _done(value: Any) -> None:
            _finish()
            if on_done is not None and isValid(self):
                on_done(value)

        def _failed(message: str, detail: str) -> None:
            _finish()
            # Keep the raw failure: a retry must not erase the fact that the
            # first attempt failed, and support needs the trace.
            self._failures.append({"label": task.label, "message": message,
                                   "traceback": detail})
            if len(self._failures) > 50:
                del self._failures[:-50]
            if on_error is not None and isValid(self):
                on_error(message, detail)

        task.signals.done.connect(_done)
        task.signals.failed.connect(_failed)
        self.pool.start(task)
        return task

    def wait_for_done(self, timeout_ms: int = 2000) -> bool:
        """Best-effort drain used on the way out (never blocks the GUI long)."""
        return self.pool.waitForDone(timeout_ms)
