"""Background execution for the Qt client.

Rule: a widget is only ever touched from the GUI thread.  Slow core calls
(document import, the model connection test, review, delivery, the exit wait)
run on a :class:`QRunnable`; the result comes back as a Qt signal carrying plain
Python values.

The core's own translation worker is not managed here: ``core.start_job_worker``
already owns that thread, its job lock and its runtime state, and duplicating
that lifecycle would create a second truth about whether a task is running.

Pool ownership (#363): a :class:`QThreadPool` destructor waits for its running
runnables **without a timeout**.  Making the pool a child of the runner therefore
turned "destroy the runner" into "block this thread until the task ends", and
every real entry point does that on the GUI thread -- closing the import wizard
mid-import, quitting the app, dropping a panel.  The pool is now parentless: the
runner's Python object -- kept alive by the in-flight task callbacks, which close
over it -- owns the only regular reference, so the pool cannot be freed while
work is queued or running, and by the time it is freed its destructor has nothing
left to wait for.  :meth:`TaskRunner.abandon` covers the one remaining wait:
interpreter shutdown, where a still-running pool would be joined on the main
thread.
"""
from __future__ import annotations

import logging
import threading
import traceback
import warnings
import weakref
from typing import Any, Callable

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    QThread,
    QThreadPool,
    Signal,
    Slot,
)
from shiboken6 import delete, isValid

_log = logging.getLogger(__name__)


def _require_gui_thread(where: str) -> None:
    """Refuse work submitted from a thread that cannot own the callbacks.

    A queued connection belongs to the thread that receives it; submitting from
    a worker thread delivers ``on_done`` / ``on_error`` / ``on_progress`` on that
    thread (or drops them), and those callbacks touch widgets.  Every product
    call site runs on the GUI thread, so this guard is a permanent fence, not a
    behaviour change.
    """
    app = QCoreApplication.instance()
    if app is None:
        return
    if QThread.currentThread() is not app.thread():
        raise RuntimeError(
            f"{where} must run on the GUI thread; on another thread the "
            "result callbacks would be delivered there (or dropped) instead of "
            "the GUI thread. Use the owning window's runner from a GUI slot.")


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

    def release(self) -> None:
        """Drop everything a finished task holds on to.

        ``signals`` keeps the ``_done`` / ``_failed`` closures connected, and those
        close over this task, so a finished task and everything its callable
        captured (a poll read holds the whole task state, hundreds of MB on a
        book) stayed alive forever: a reference cycle through the Qt connection
        that the Python collector cannot see.
        """
        with warnings.catch_warnings():
            # A signal nobody connected (``progress``) warns instead of raising.
            warnings.simplefilter("ignore", RuntimeWarning)
            for signal in (self.signals.done, self.signals.failed,
                           self.signals.progress):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
        self.fn = None
        self.args = ()
        self.kwargs = {}

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
    """Owns the pool and keeps strong references to in-flight tasks.

    ``pool`` is deliberately **not** a QObject child of this runner (#363).  A
    ``QThreadPool`` destructor joins every running runnable with no timeout, so a
    parented pool froze the GUI thread in ``~QObject -> deleteChildren`` whenever
    its owner went away.  Without the parent, the pool stays alive exactly as
    long as the in-flight tasks do (their ``_done`` / ``_failed`` callbacks close
    over this runner) and is destroyed idle, when the destructor returns at once.
    """

    #: Every live runner, so the exit path can detach pools it does not own
    #: directly (the import wizard's, the project panels').  A weak set is
    #: enough: a runner with work in flight is referenced by that work's
    #: callbacks, so it cannot be collected before the work delivers.
    _LIVE: "weakref.WeakSet[TaskRunner]" = weakref.WeakSet()

    def __init__(self, parent: QObject | None = None, *, max_threads: int = 4) -> None:
        super().__init__(parent)
        self._max_threads = max(1, max_threads)
        self.pool = self._new_pool()
        self._running: list[Task] = []
        self._failures: list[dict] = []
        TaskRunner._LIVE.add(self)

    def _new_pool(self) -> QThreadPool:
        pool = QThreadPool()
        pool.setMaxThreadCount(self._max_threads)
        return pool

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
        _require_gui_thread("TaskRunner.submit()")
        if self.pool is None:
            # The owner used this runner again after abandon() (the exit flow
            # calls abandon_all() for every live pool).  Starting a fresh pool
            # keeps such a late submit working instead of crashing a timer
            # slot; the abandoned work is already being reaped off-thread.
            _log.warning("TaskRunner reused after its pool was abandoned; "
                         "starting a fresh pool for %r", label or "task")
            self.pool = self._new_pool()
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
            task.release()

        def _done(value: Any) -> None:
            _finish()
            if on_done is None:
                return
            if isValid(self):
                on_done(value)
            else:
                # The owner (wizard/panel) died first; the result is dropped so
                # it cannot touch deleted widgets, but a long import must not
                # vanish without a trace in the log.
                _log.warning(
                    "background task %r finished after its owner was destroyed; "
                    "result dropped", task.label)

        def _failed(message: str, detail: str) -> None:
            _finish()
            # Keep the raw failure: a retry must not erase the fact that the
            # first attempt failed, and support needs the trace.
            self._failures.append({"label": task.label, "message": message,
                                   "traceback": detail})
            if len(self._failures) > 50:
                del self._failures[:-50]
            if on_error is None:
                return
            if isValid(self):
                on_error(message, detail)
            else:
                _log.warning(
                    "background task %r failed after its owner was destroyed; "
                    "error dropped: %s", task.label, message)

        task.signals.done.connect(_done)
        task.signals.failed.connect(_failed)
        self.pool.start(task)
        return task

    def wait_for_done(self, timeout_ms: int = 2000) -> bool:
        """Best-effort drain used on the way out (never blocks the GUI long).

        Returns ``False`` when the deadline passed with work still in flight.
        The pool is *not* destroyed by this call, so a caller that gets ``False``
        must detach it (:meth:`abandon`) instead of letting the destructor
        resume the wait on the calling thread (#363).
        """
        pool = self.pool
        if pool is None:
            return not self._running
        return pool.waitForDone(timeout_ms)

    def abandon(self) -> bool:
        """Detach the pool and stop owning it.

        Returns ``True`` when the pool was already idle (it is destroyed here,
        immediately, because the destructor has nothing to wait for).  With work
        still in flight the pool is handed to a daemon thread whose whole job is
        to call the destructor: ``~QThreadPool`` joins the running runnables, so
        that join must not happen on the GUI thread (the freeze) and must not
        happen on the main thread at interpreter shutdown either (the process
        would linger).  The work keeps running off-screen; its callbacks are
        still delivered while the GUI event loop is alive.
        """
        pool = self.pool
        if pool is None:
            return True
        self.pool = None
        if not self._running:
            # An idle pool can be destroyed synchronously: waitForDone() returns
            # without waiting, and the threads are told to exit.
            return True
        threading.Thread(
            target=self._reap_pool, args=(pool,),
            name="folith-task-pool-reaper", daemon=True).start()
        return False

    @staticmethod
    def _reap_pool(pool: QThreadPool) -> None:
        # This thread blocks in ~QThreadPool until the tasks end; the process can
        # still exit meanwhile because it is a daemon thread and the destructor
        # releases the GIL while it waits.
        try:
            delete(pool)
        except Exception:  # pragma: no cover - shutdown race, nothing to report to
            _log.debug("task pool reaper could not delete its pool", exc_info=True)

    @classmethod
    def abandon_all(cls) -> int:
        """Detach every live runner's pool; returns how many still had work.

        The window exit flow calls this after its bounded drain: a busy pool must
        not be destroyed on the GUI thread, and at interpreter shutdown it must
        not be joined on the main thread either.  The import wizard's pool and
        the panels' pools are not children of the main window's runner, so they
        have to be found through this registry.
        """
        handed_off = 0
        for runner in list(cls._LIVE):
            try:
                if not runner.abandon():
                    handed_off += 1
            except Exception:  # pragma: no cover - exit must not raise here
                _log.exception("failed to abandon a background task pool")
        return handed_off
