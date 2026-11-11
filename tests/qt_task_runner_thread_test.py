"""Lock the ``TaskRunner`` callback-thread invariant from issue #345.

The probe recorded in ``.loop/0.8.2-plan.md`` §6.4 found no reproducible defect on
``bc765adc`` / PySide6 6.11.2: the work function runs on a pool thread,
``on_done`` / ``on_error`` run on the GUI thread, and a result that arrives after
the owning runner was destroyed is not delivered.  ``workers.py`` gets that for
free from two properties this file pins down:

* the ``_TaskSignals`` object is created on the GUI thread, so a cross-thread
  ``emit`` is a queued call delivered on the GUI thread;
* the ``_done`` / ``_failed`` closures guard the user callback with
  ``isValid(self)``, so a queued call that lands after the runner's C++ object is
  gone does not reach it.

Issue #345 has no runnable reproduction, so this is a regression test only; no
product code is changed.  Assertions are synchronised through the GUI event loop
with explicit deadlines -- no bare ``sleep`` decides whether a callback ran.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QEventLoop,
    QThread,
    QTimer,
)
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import delete, isValid  # noqa: E402

from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _wait_until(app, predicate, timeout_ms=5000):
    """Spin the GUI event loop until ``predicate()`` holds or the deadline passes.

    A polling ``QTimer`` plus a single-shot timeout keeps the wait bounded without
    a fixed sleep; the extra ``processEvents`` pass flushes anything posted in the
    same loop iteration as the predicate change.
    """
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(5)
    poll.timeout.connect(lambda: predicate() and loop.quit())
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout_ms)
    loop.exec()
    poll.stop()
    deadline.stop()
    app.processEvents()
    return predicate()


def _dispose(runner):
    if not isValid(runner):
        return
    runner.wait_for_done(5000)
    delete(runner)


# --- callbacks execute on the GUI thread ------------------------------------


def test_on_done_runs_on_the_gui_thread(app):
    main_ident = threading.get_ident()
    runner = TaskRunner()
    seen: dict[str, object] = {}
    try:
        def work():
            seen["work_ident"] = threading.get_ident()
            seen["work_thread_is_gui"] = QThread.currentThread() is app.thread()
            return "result"

        def done(value):
            seen["value"] = value
            seen["done_ident"] = threading.get_ident()
            seen["done_thread_is_gui"] = QThread.currentThread() is app.thread()

        runner.submit(work, on_done=done, label="on-done-thread")
        assert _wait_until(app, lambda: "done_ident" in seen), "on_done never ran"
    finally:
        _dispose(runner)

    assert seen["work_ident"] != main_ident, "the work function ran on the GUI thread"
    assert seen["work_thread_is_gui"] is False
    assert seen["done_ident"] == main_ident, "on_done ran off the GUI thread"
    assert seen["done_thread_is_gui"] is True, "QThread.currentThread() was not the GUI thread"
    assert seen["value"] == "result"


def test_on_error_runs_on_the_gui_thread(app):
    main_ident = threading.get_ident()
    runner = TaskRunner()
    seen: dict[str, object] = {}
    try:
        def work():
            seen["work_ident"] = threading.get_ident()
            seen["work_thread_is_gui"] = QThread.currentThread() is app.thread()
            raise RuntimeError("probe failure")

        def failed(message, detail):
            seen["message"] = message
            seen["has_traceback"] = "RuntimeError" in detail
            seen["error_ident"] = threading.get_ident()
            seen["error_thread_is_gui"] = QThread.currentThread() is app.thread()

        runner.submit(work, on_error=failed, label="on-error-thread")
        assert _wait_until(app, lambda: "error_ident" in seen), "on_error never ran"
    finally:
        _dispose(runner)

    assert seen["work_ident"] != main_ident, "the work function ran on the GUI thread"
    assert seen["work_thread_is_gui"] is False
    assert seen["error_ident"] == main_ident, "on_error ran off the GUI thread"
    assert seen["error_thread_is_gui"] is True, "QThread.currentThread() was not the GUI thread"
    assert seen["message"] == "probe failure"
    assert seen["has_traceback"] is True


def test_on_progress_runs_on_the_gui_thread(app):
    main_ident = threading.get_ident()
    runner = TaskRunner()
    seen: dict[str, object] = {}
    try:
        def work(on_progress):
            seen["work_ident"] = threading.get_ident()
            seen["work_thread_is_gui"] = QThread.currentThread() is app.thread()
            on_progress(1, 2)
            return "done"

        def progress(done, total):
            seen["progress"] = (done, total)
            seen["progress_ident"] = threading.get_ident()
            seen["progress_thread_is_gui"] = QThread.currentThread() is app.thread()

        runner.submit(work, on_done=lambda value: None, on_progress=progress,
                      label="on-progress-thread")
        assert _wait_until(app, lambda: "progress_ident" in seen), "on_progress never ran"
    finally:
        _dispose(runner)

    assert seen["work_ident"] != main_ident, "the work function ran on the GUI thread"
    assert seen["work_thread_is_gui"] is False
    assert seen["progress"] == (1, 2)
    assert seen["progress_ident"] == main_ident, "on_progress ran off the GUI thread"
    assert seen["progress_thread_is_gui"] is True


# --- destruction before delivery drops the callback --------------------------


@pytest.mark.parametrize("outcome", ["done", "failed"])
def test_late_callback_is_not_delivered_after_delete_later(app, outcome):
    """A result that arrives after ``deleteLater()`` must not reach the callback."""
    main_ident = threading.get_ident()
    runner = TaskRunner()
    gate = threading.Event()
    calls: list[tuple[str, str]] = []
    released: list[bool] = []
    work_idents: list[int] = []

    def work():
        work_idents.append(threading.get_ident())
        # The slow part is bounded by the gate: the test opens it only after
        # deleteLater(), so the result cannot be ready before destruction starts.
        released.append(gate.wait(10.0))
        if outcome == "failed":
            raise RuntimeError("late failure")
        return "late value"

    task = runner.submit(
        work,
        on_done=lambda value: calls.append(("done", value)),
        on_error=lambda message, detail: calls.append(("failed", message)),
        label="late-callback",
    )
    try:
        assert runner.busy, "the task finished before deleteLater() could be exercised"
        runner.deleteLater()
        # No event loop runs between submit() and here, so even if the worker
        # emits before the delete completes, delivery is still queued behind it.
        gate.set()
        # Force the deferred delete.  It blocks in ~QThreadPool until the worker
        # returns, which is why the gate is already open: it cannot deadlock.
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(runner), "deleteLater() did not destroy the TaskRunner"
        # _finish() -> task.release() runs inside the queued _done/_failed slot
        # before the isValid() guard, so a released task proves the result signal
        # was really delivered; the empty ``calls`` then proves it was dropped.
        assert _wait_until(app, lambda: task.fn is None), "the result signal was never delivered"
        app.processEvents()
        assert calls == [], f"callback ran after the runner was destroyed: {calls}"
    finally:
        gate.set()
        _dispose(runner)

    assert released == [True], "the worker was never released"
    assert work_idents and all(ident != main_ident for ident in work_idents)
