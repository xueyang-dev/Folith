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
the exit path, where a still-running pool would otherwise be joined.

That wait cannot be moved off the main thread either (#363, P0-9h).  A
``QThreadPool`` destructor is ``waitForDone(Forever)`` while the pool has a
running runnable, so destroying a busy pool from a helper thread leaves that
thread blocked inside Qt for the rest of the task -- through the interpreter's
shutdown, where CPython and PySide are tearing the same objects down.  Linux
segfaults there.  The one wait that *can* be avoided is the join itself: an
abandoned pool is kept alive by :data:`_ABANDONED` and the process leaves with
:func:`_leave_without_teardown` instead of running a Qt teardown that would join
it.  See :meth:`TaskRunner.abandon`.
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
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
from shiboken6 import isValid

_log = logging.getLogger(__name__)

#: Pools handed off by :meth:`TaskRunner.abandon` while they still had work in
#: flight.  They are **never** released.  A ``QThreadPool`` cannot be destroyed
#: for free while a runnable runs (its destructor is an unbounded join), so
#: nothing may destroy these; this list is the reference that stops Python's own
#: refcount from doing it first.  A reference is not enough on its own -- PySide's
#: shutdown hook destroys every live Python-owned ``QObject`` wrapper on the main
#: thread whatever the reference count is -- which is what
#: :func:`_leave_without_teardown` is for.
_ABANDONED: "list[QThreadPool]" = []

#: Whether :func:`_guard_the_exit` already registered its ``atexit`` hook.
_EXIT_GUARDED = False

#: Status the ``atexit`` hook hands to ``os._exit``.  ``os._exit`` skips the
#: interpreter's own status handling, so the intended code has to be recorded
#: before the hook runs -- otherwise a startup exception would leave through a
#: "success" status and callers (CI, wrapper scripts, a user's shell loop) would
#: read a crash as success.
_EXIT_CODE = 0


def set_exit_code(code: int) -> None:
    """Record the status the exit hook should use.

    Called from ``app.main``'s ``finally`` with the code that path is about to
    return or raise, so :func:`_leave_without_teardown` can honour it instead of
    the unconditional 0 it used to hand to ``os._exit``.  Never raises.
    """
    global _EXIT_CODE
    try:
        _EXIT_CODE = int(code)
    except Exception:  # noqa: BLE001 - a status we cannot read is a failure
        _EXIT_CODE = 1


def _has_running_work() -> bool:
    """Whether an abandoned pool still has a runnable to join."""
    return any(pool.activeThreadCount() > 0 for pool in _ABANDONED)


def _leave_without_teardown() -> None:
    """Leave the process instead of joining an abandoned pool on the way out.

    Runs as an ``atexit`` hook, i.e. before PySide's own shutdown hook (registered
    when ``PySide6.QtCore`` was imported, so it is older than this one).  That
    hook is the problem: ``PySide::destroyQCoreApplication`` ->
    ``BindingManager::visitAllPyObjects`` -> ``destructionVisitor`` destroys every
    live Python-owned ``QObject``, so it finds the abandoned pool, calls
    ``~QThreadPool`` -> ``waitForDone(Forever)`` on the main thread and the process
    waits out the task the user just asked not to wait for.

    With work still running there is nothing left to shrink that wait: Qt only
    drops a pool thread once its runnable returns, and no Python-level owner can
    outlive the sweep.  So this exits here, after flushing what this module owns,
    and lets the kernel reclaim the pool and its thread.  The status comes from
    :func:`set_exit_code`, not a hardcoded 0, so a startup failure still exits
    non-zero.  With nothing running the
    hook returns and the ordinary shutdown happens unchanged -- a normal exit, and
    every test session, never takes this path.
    """
    if not _has_running_work():
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - never raise from an exit hook
            pass
    try:
        logging.shutdown()
    except Exception:  # noqa: BLE001 - never raise from an exit hook
        pass
    # ``MainWindow.closeEvent`` writes the window geometry through QSettings
    # *after* the abort point (``abandon_all()`` runs first), and QSettings only
    # reaches permanent storage on ``sync()`` or when it is destroyed -- neither
    # of which happens on this path.  Flushing here keeps the one preference the
    # close event still owes us.
    try:
        from . import preferences

        preferences.settings().sync()
    except Exception:  # noqa: BLE001 - preferences are not worth blocking exit
        pass
    os._exit(_EXIT_CODE)


def _guard_the_exit() -> None:
    """Install :func:`_leave_without_teardown` once per process."""
    global _EXIT_GUARDED
    if _EXIT_GUARDED:
        return
    _EXIT_GUARDED = True
    atexit.register(_leave_without_teardown)


def log_note(message: str) -> None:
    """Append ``message`` to the client log ``window.py`` writes.

    This app installs no ``logging`` handler, so ``_log.warning`` only reaches
    stderr; a background result dropped after its owner went away must still be
    findable in ``qt-client.log``.  Falls back to the stdlib logger when the Qt
    bootstrap paths are not ready (unit tests, early startup).
    """
    try:
        from .bootstrap import appender
        appender()(message)
    except Exception:  # noqa: BLE001 - a log line must never break the caller
        _log.warning("%s", message)


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
            # slot; the abandoned pool is parked in _ABANDONED and will never be
            # destroyed, so it cannot interfere with the new one.
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
                log_note(
                    f"background task {task.label!r} finished after its owner "
                    "was destroyed; result dropped")

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
                log_note(
                    f"background task {task.label!r} failed after its owner "
                    f"was destroyed; error dropped: {message}")

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
        """Release the pool without waiting for it, and without destroying it.

        Returns ``True`` when the pool was already idle: it is destroyed here,
        immediately, because a destructor with nothing to join returns at once
        (measured: 3 ms with a warm thread).  With work still in flight the pool
        is moved to :data:`_ABANDONED`, where it is never destroyed, and the
        process is told to leave without the Qt teardown that would join it
        (:func:`_leave_without_teardown`).

        Destroying the busy pool *somewhere else* is what #363 P0-9b did, and it
        is not survivable: ``~QThreadPool`` blocks in ``waitForDone(Forever)``
        until the runnable returns, so a helper thread sits inside Qt for the
        rest of the task -- and ``shiboken6.delete`` first mutates the global
        wrapper registry that PySide's shutdown hook is iterating at the same
        time, on the main thread, with the GIL released.  Linux segfaults.
        Not destroying it is the only option that cannot crash: the work keeps
        running off-screen, its callbacks are still delivered while the GUI event
        loop is alive, and the process exits when its owner does.
        """
        pool = self.pool
        if pool is None:
            return True
        self.pool = None
        if not self._running:
            # Idle: dropping the last reference destroys it here, and the
            # destructor only has to tell the threads to quit.
            return True
        _ABANDONED.append(pool)
        _guard_the_exit()
        return False

    @classmethod
    def abandon_all(cls) -> int:
        """Detach every live runner's pool; returns how many still had work.

        The window exit flow calls this after its bounded drain: a busy pool must
        not be joined anywhere -- not on the GUI thread (the freeze P0-9b fixed)
        and not on the main thread at interpreter shutdown (the linger it also
        fixed).  The import wizard's pool and the panels' pools are not children
        of the main window's runner, so they have to be found through this
        registry.  With any work still running this arms the exit hook described
        by :func:`_leave_without_teardown`, i.e. the process leaves without a Qt
        teardown that would join those pools.
        """
        handed_off = 0
        for runner in list(cls._LIVE):
            try:
                if not runner.abandon():
                    handed_off += 1
            except Exception:  # pragma: no cover - exit must not raise here
                _log.exception("failed to abandon a background task pool")
        return handed_off
