"""#363: destroying a ``TaskRunner`` with work in flight must not block the caller.

``TaskRunner`` used to parent its ``QThreadPool`` to itself
(``QThreadPool(self)``).  A ``QThreadPool``'s destructor waits for every running
runnable without a timeout, so destroying the runner -- closing the import
wizard, quitting the app, dropping a panel -- froze the calling (GUI) thread for
the whole remaining task.  The measurements below are what the Issue asked for:

* destroying a busy runner: < 100 ms (it used to equal the remaining task);
* the real ``ImportWizardDialog`` closed during an import: < 100 ms;
* the exit drain's timeout is checked and not handed to a second, unbounded
  wait in ``~QThreadPool``.

``QCoreApplication.processEvents()`` does **not** deliver ``DeferredDelete``
(measured on PySide6 6.11.2), so the teardown cases force it with
``sendPostedEvents(None, QEvent.Type.DeferredDelete)`` -- the trap called out in
the Issue -- or delete the C++ object synchronously.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QTimer,
)
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import delete, isValid  # noqa: E402

from folith.qt import import_wizard, window as window_module  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402

#: The Issue's acceptance bound.  The old code measured the whole remaining
#: task here (0.5 s -> 0.503 s, 25 s -> 25.009 s); a real fix is well under a
#: frame, so 100 ms leaves room for a loaded CI machine without hiding a block.
BOUND_S = 0.1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _start_busy(app, work_s: float, *, label: str = "busy") -> tuple[TaskRunner, threading.Event, threading.Event]:
    """Submit a task that is provably running and return (runner, started, finished)."""
    runner = TaskRunner()
    started = threading.Event()
    finished = threading.Event()

    def work():
        started.set()
        try:
            time.sleep(work_s)
        finally:
            finished.set()
        return "done"

    runner.submit(work, label=label)
    deadline = time.monotonic() + 5.0
    while not started.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert started.is_set(), "the worker never started"
    return runner, started, finished


def _pump_until(app, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _dispose_busy(runner: TaskRunner, finished: threading.Event) -> None:
    """Let the off-screen work finish and deliver its (dropped) callback."""
    finished.wait(5.0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if not runner.busy:
            break
        time.sleep(0.005)
    QApplication.processEvents()


# --- 1. destroying a busy runner -------------------------------------------


def test_destroying_a_busy_runner_returns_immediately(app):
    """Old code: 0.6 s task -> ``delete(runner)`` blocked ~0.6 s."""
    runner, _started, finished = _start_busy(app, 0.6)
    try:
        started_at = time.perf_counter()
        delete(runner)  # the child-destruction path (parent teardown does this)
        elapsed = time.perf_counter() - started_at
    finally:
        _dispose_busy(runner, finished)
    assert not isValid(runner)
    assert elapsed < BOUND_S, (
        f"destroying a busy TaskRunner blocked for {elapsed:.3f}s "
        f"(bound {BOUND_S:.3f}s)")


def test_delete_later_teardown_of_a_busy_runner_returns_immediately(app):
    """The real event-loop path: ``deleteLater`` + ``DeferredDelete``.

    ``processEvents()`` alone would report a fake 0.0 s (it does not deliver
    deferred deletes); the freeze lands on the next loop turn, so the test
    forces exactly that step.
    """
    runner, _started, finished = _start_busy(app, 0.6)
    try:
        runner.deleteLater()
        started_at = time.perf_counter()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        elapsed = time.perf_counter() - started_at
    finally:
        _dispose_busy(runner, finished)
    assert not isValid(runner)
    assert elapsed < BOUND_S, (
        f"deferred deletion of a busy TaskRunner blocked for {elapsed:.3f}s "
        f"(bound {BOUND_S:.3f}s)")


# --- 2. the real import wizard ---------------------------------------------


def _wizard_controller(started: threading.Event, work_s: float):
    def preflight(path, *, project_id=None, target_lang=""):
        return {"kind": "tmx", "expected_units": 3,
                "language_pair": {"status": "match"}, "blocking": "",
                "warnings": []}

    def importer(path, *, project_id=None, target_lang="", preflight=None):
        started.set()
        time.sleep(work_s)
        return {"report": {"status": "imported",
                           "counts": {"total": 3, "imported": 3}},
                "store": {"written": 3, "skipped": 0}}

    return import_wizard.ImportWizardController(preflight=preflight,
                                                importer=importer)


def test_real_import_wizard_close_during_import_returns_immediately(app, tmp_path):
    """Old code: closing during a 1.0 s import froze teardown for ~0.9 s.

    The wizard has no cancel; the fix is that closing it (Esc / X / 关闭) returns
    at once and the import finishes off-screen.  Both halves are measured:
    ``dialog.exec()`` returning and the C++ dialog teardown that follows it (the
    half the old code blocked in -- the audit's ``teardown_blocked_s``).
    """
    started = threading.Event()
    dialog = import_wizard.ImportWizardDialog(
        controller=_wizard_controller(started, 1.0))
    runner = dialog.runner
    try:
        source = tmp_path / "memory.tmx"
        source.write_text("<tmx/>", encoding="utf-8")
        dialog.set_path(str(source))
        dialog._go_next()  # the real "开始导入" path
        assert dialog._busy, "the wizard did not enter the importing step"
        deadline = time.monotonic() + 5.0
        while not started.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.002)
        assert started.is_set(), "the engine import never started"

        QTimer.singleShot(50, dialog.reject)  # Esc / close button

        started_at = time.perf_counter()
        dialog.exec()
        exec_elapsed = time.perf_counter() - started_at

        teardown_at = time.perf_counter()
        delete(dialog)  # what a parent teardown / WA_DeleteOnClose does
        teardown_elapsed = time.perf_counter() - teardown_at
        total_elapsed = time.perf_counter() - started_at
        abandoned = getattr(dialog, "_abandoned", False)
    finally:
        if isValid(dialog):
            delete(dialog)
        for _ in range(200):
            QApplication.processEvents()
            if not runner.busy:
                break
            time.sleep(0.005)

    assert exec_elapsed < BOUND_S, (
        f"dialog.exec() took {exec_elapsed:.3f}s after a close during import")
    assert teardown_elapsed < BOUND_S, (
        f"tearing the dialog down took {teardown_elapsed:.3f}s after a close "
        f"during import")
    assert total_elapsed < BOUND_S, (
        f"closing the wizard during an import blocked for {total_elapsed:.3f}s "
        f"(bound {BOUND_S:.3f}s)")
    assert abandoned, "closing during an import must mark the dialog abandoned"


# --- 3. the exit drain is bounded and checked ------------------------------


def test_wait_for_done_timeout_is_not_repeated_by_the_destructor(app):
    """Old code: 3 s drain + remaining task in ``~QThreadPool`` = 6 s (B-2)."""
    runner, _started, finished = _start_busy(app, 1.0)
    try:
        drain_at = time.perf_counter()
        drained = runner.wait_for_done(250)
        drain_elapsed = time.perf_counter() - drain_at
        assert drained is False, "a 250 ms drain cannot finish a 1 s task"
        assert 0.2 <= drain_elapsed < 0.6, f"drain took {drain_elapsed:.3f}s"

        teardown_at = time.perf_counter()
        delete(runner)
        teardown_elapsed = time.perf_counter() - teardown_at
        total_elapsed = time.perf_counter() - drain_at
    finally:
        _dispose_busy(runner, finished)

    assert teardown_elapsed < BOUND_S, (
        f"after a timed-out drain the destructor blocked again for "
        f"{teardown_elapsed:.3f}s")
    assert total_elapsed < 0.6, (
        f"exit-path worst case was {total_elapsed:.3f}s; it must stay bounded "
        f"by the drain timeout")


def test_acquire_close_checks_the_drain_and_bounds_the_exit(app, monkeypatch):
    """``MainWindow._acquire_close`` must not ignore a timed-out drain (B-2).

    The 3000 ms budget is the module constant ``window._EXIT_DRAIN_MS`` so this
    test can shrink it; the old code ignored both the value and the
    ``wait_for_done`` result and waited for the whole task instead.
    """
    monkeypatch.setattr(window_module, "_EXIT_DRAIN_MS", 250, raising=False)
    runner, _started, finished = _start_busy(app, 1.0, label="exit-stop-like")
    log: list[str] = []
    calls: list[str] = []

    def close() -> None:
        # The real window's destruction deletes the runner as a child; do the
        # same here so teardown is inside the measured window.
        calls.append("close")
        delete(runner)

    fake_window = SimpleNamespace(
        _pending_update=False,
        _exit_in_progress=True,
        _in_close_event=False,
        _closing_accepted=False,
        _edit_queue=[],
        updates=SimpleNamespace(cancel=lambda: None),
        runner=runner,
        _log=log.append,
        _close_busy_dialogs=lambda: None,
        _close_other_windows=lambda: None,
        close=close,
    )
    started_at = time.perf_counter()
    try:
        window_module.MainWindow._acquire_close(fake_window)
        elapsed = time.perf_counter() - started_at
    finally:
        _dispose_busy(runner, finished)

    assert calls == ["close"]
    assert fake_window._closing_accepted is True
    assert elapsed < 0.6, (
        f"_acquire_close blocked for {elapsed:.3f}s after its drain timed out")
    assert runner.pool is None, "the exit path must detach the pool instead of waiting again"
    assert any("still running" in line for line in log), (
        f"a timed-out drain must be logged, got {log!r}")


# --- 4. submit() is a GUI-thread-only entry point --------------------------


def test_submit_from_a_worker_thread_is_rejected(app):
    """Old code accepted it and delivered ``on_done`` on the worker thread."""
    runner = TaskRunner()
    outcome: dict[str, object] = {}

    def submit_off_thread() -> None:
        try:
            runner.submit(lambda: "value",
                          on_done=lambda _value: outcome.setdefault("ran", True),
                          label="cross-thread")
        except RuntimeError as exc:
            outcome["error"] = str(exc)
        else:
            outcome["accepted"] = True

    worker = threading.Thread(target=submit_off_thread, name="cross-thread-submit")
    worker.start()
    worker.join(5.0)
    try:
        assert not worker.is_alive()
        assert "error" in outcome, (
            f"submit() from a worker thread was accepted: {outcome!r}")
        assert "GUI thread" in str(outcome["error"])
        assert "accepted" not in outcome
        # Nothing was queued: no callback and no running task.
        app.processEvents()
        assert "ran" not in outcome
        assert runner.busy is False
    finally:
        delete(runner)


def test_submit_from_the_gui_thread_still_works(app):
    runner = TaskRunner()
    seen: list[str] = []
    try:
        runner.submit(lambda: "ok", on_done=seen.append, label="gui-thread")
        assert _pump_until(app, lambda: seen == ["ok"]), "the GUI-thread submit never ran"
    finally:
        delete(runner)


def test_process_exit_with_a_running_task_is_bounded(tmp_path):
    """End-to-end: a 30 s task must not keep the process alive after exit.

    This is the B-2 symptom users saw ("窗口关了但进程不退"): at interpreter
    shutdown the still-running pool used to be destroyed -- and joined -- on the
    main thread (measured: 4.1 s for a 4 s task, i.e. the whole task).  The
    script mirrors ``MainWindow._acquire_close``: bounded drain, then detach.
    """
    script = tmp_path / "exit_probe.py"
    script.write_text(
        "import os, sys, threading, time\n"
        "os.environ['QT_QPA_PLATFORM'] = 'offscreen'\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from folith.qt.workers import TaskRunner\n"
        "app = QApplication([])\n"
        "runner = TaskRunner()\n"
        "started = threading.Event()\n"
        "def work():\n"
        "    started.set()\n"
        "    time.sleep(30.0)\n"
        "runner.submit(work, label='exit-stop-like')\n"
        "while not started.is_set():\n"
        "    app.processEvents()\n"
        "    time.sleep(0.001)\n"
        "assert runner.wait_for_done(200) is False\n"
        "handed = TaskRunner.abandon_all()\n"
        "assert handed == 1, handed\n"
        "print('abandoned', flush=True)\n",
        encoding="utf-8")
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["FOLITH_QT_SETTINGS_DIR"] = str(tmp_path / "settings")
    started_at = time.perf_counter()
    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=15)
    elapsed = time.perf_counter() - started_at
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "abandoned" in proc.stdout
    assert elapsed < 6.0, (
        f"the process needed {elapsed:.1f}s to exit with a detached 30 s task")


def test_a_runner_reused_after_abandon_gets_a_fresh_pool(app):
    """``abandon_all()`` is global (another window may still be alive).

    A late submit on such a runner must not raise from a timer slot; it starts a
    fresh pool and works normally.  The abandoned work is still reaped off-thread.
    """
    runner, _started, finished = _start_busy(app, 0.4)
    try:
        assert runner.abandon() is False
        assert runner.pool is None
        seen: list[str] = []
        runner.submit(lambda: "fresh", on_done=seen.append, label="after-abandon")
        assert _pump_until(app, lambda: seen == ["fresh"]), "the fresh pool never ran"
        assert runner.pool is not None
    finally:
        _dispose_busy(runner, finished)
        if runner.pool is not None:
            runner.pool.waitForDone(3000)
        delete(runner)
