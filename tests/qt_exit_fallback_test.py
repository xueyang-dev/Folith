"""#363 boundary (c): every exit path out of ``main()`` must be bounded.

``TaskRunner.abandon_all()`` used to be called only from
``MainWindow._acquire_close``.  Any other way out of ``folith.qt.app.main`` --
``--smoke``, ``--selftest``, an exception while the window was being built, a
scripted ``app.quit()`` -- left its busy ``QThreadPool`` to interpreter
shutdown, where ``~QThreadPool`` joins the running runnable **on the main
thread**.  Measured on macOS / PySide6 6.11.2 with a 4 s task: 4.11 s between
``main()`` returning and the process exiting (the whole remaining task).  With
the ``main()`` fallback the same probe exits in 0.20 s.

The subprocess tests below run that probe unchanged against whatever ``app.py``
is checked out: on the old code it finds no fallback, so nothing detaches the
pool and the process is dragged to the end of the task (red).  The in-process
tests cover the two properties a shutdown hook must have -- idempotent after
``_acquire_close`` already detached, and incapable of turning an exit into an
exception.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import app as app_module  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402

#: The task the probe leaves running while ``main()`` returns.  Long enough that
#: "the process waited for it" and "the process detached it" are unmistakable.
TASK_S = 4.0

#: Acceptance bound.  The reaper only has to start a thread, so the honest
#: value is a fraction of a second; 1 s leaves room for a loaded CI machine and
#: still fails the old behaviour by a wide margin (4.1 s).
BOUND_S = 1.0


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --- subprocess probes: the real ``main()`` boundary ------------------------

#: One probe, two modes.  ``return`` and ``raise`` both leave a task running
#: inside ``main()``; the only difference is how the inner body ends.  The
#: legacy branch is what the old code does -- there is no fallback to call, so
#: the probe reproduces the unbounded exit instead of asserting it away.
_PROBE = r'''
import os
import sys
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.getcwd())

from PySide6.QtWidgets import QApplication

from folith.qt import app as app_module
from folith.qt.workers import TaskRunner

MODE = sys.argv[1]
TASK_S = float(sys.argv[2])

app = QApplication([])
started = threading.Event()


def work():
    started.set()
    time.sleep(TASK_S)


def busy_inner(argv=None):
    runner = TaskRunner()
    runner.submit(work, label="exit-fallback-probe")
    deadline = time.monotonic() + 5.0
    while not started.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    if not started.is_set():
        raise SystemExit("the worker never started")
    print("busy", flush=True)
    if MODE == "raise":
        raise RuntimeError("window construction failed")
    return 0


if not (hasattr(app_module, "_main")
        and hasattr(app_module, "detach_background_work_on_exit")):
    # Old code: no exit fallback exists.  Reproduce its shape -- main() returns
    # (or raises) with the pool still busy and nothing detaches it.
    print("legacy: no exit fallback", flush=True)
    busy_inner()
    sys.exit(0)

app_module._main = busy_inner
code = app_module.main([])
print("main returned", code, flush=True)
sys.exit(code)
'''


def _probe_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Keep the fallback's log line and Qt's settings out of the developer's
    # real app-support directory when the probe does not bootstrap the app.
    env["FOLITH_QT_SETTINGS_DIR"] = str(tmp_path / "settings")
    env["FOLITH_DESKTOP_DATA_DIR"] = str(tmp_path / "data")
    env["FOLITH_DESKTOP_LOG_DIR"] = str(tmp_path / "logs")
    return env


def _run_probe(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess, float]:
    script = tmp_path / f"exit_fallback_probe_{mode}.py"
    script.write_text(_PROBE, encoding="utf-8")
    started_at = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(script), mode, str(TASK_S)],
        cwd=str(ROOT), env=_probe_env(tmp_path),
        capture_output=True, text=True, timeout=30)
    return proc, time.perf_counter() - started_at


def test_main_returning_does_not_wait_for_a_running_task(tmp_path):
    """``main()`` returns, the 4 s task is still running: exit must be bounded."""
    proc, elapsed = _run_probe(tmp_path, "return")
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "busy" in proc.stdout, proc.stdout + proc.stderr[-2000:]
    assert elapsed < BOUND_S, (
        f"the process needed {elapsed:.2f}s to exit after main() returned with a "
        f"{TASK_S:.0f}s task still running (bound {BOUND_S:.1f}s); the pool was "
        "joined on the main thread at interpreter shutdown")
    assert "main returned 0" in proc.stdout
    log = tmp_path / "logs" / "qt-client.log"
    assert "main() fallback" in log.read_text(encoding="utf-8"), (
        "the fallback detached the pool but did not say so in the client log")


def test_main_raising_still_detaches_and_exits_bounded(tmp_path):
    """The ``finally`` must cover exceptions, not only a clean return."""
    proc, elapsed = _run_probe(tmp_path, "raise")
    assert proc.returncode != 0
    assert "RuntimeError: window construction failed" in proc.stderr
    assert elapsed < BOUND_S, (
        f"the process needed {elapsed:.2f}s to exit after main() raised with a "
        f"{TASK_S:.0f}s task still running (bound {BOUND_S:.1f}s); an exception "
        "path left the pool to its destructor")


# --- in-process: idempotency and shutdown safety ----------------------------


def _start_busy(app: QApplication, work_s: float) -> tuple[TaskRunner, threading.Event]:
    """Submit work that is provably running; return (runner, finished)."""
    runner = TaskRunner()
    started = threading.Event()
    finished = threading.Event()

    def work():
        started.set()
        try:
            time.sleep(work_s)
        finally:
            finished.set()

    runner.submit(work, label="exit-fallback-idempotent")
    deadline = time.monotonic() + 5.0
    while not started.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert started.is_set(), "the worker never started"
    return runner, finished


def _reaper_idents() -> set[int]:
    return {thread.ident for thread in threading.enumerate()
            if thread.name == "folith-task-pool-reaper"}


def test_fallback_after_acquire_close_is_a_harmless_noop(app):
    """``_acquire_close`` already detached: the fallback must do nothing.

    ``abandon_all()`` is idempotent by construction (a detached runner has
    ``pool is None``), and this is the property the exit hook relies on: the
    close flow and ``main()``'s ``finally`` must never reap the same pool twice
    or start a second reaper for it.
    """
    runner, finished = _start_busy(app, 1.0)
    try:
        # What MainWindow._acquire_close does: a bounded drain, then detach.
        assert runner.wait_for_done(50) is False
        assert TaskRunner.abandon_all() >= 1
        assert runner.pool is None
        reapers_before = _reaper_idents()

        started_at = time.perf_counter()
        handed_off = app_module.detach_background_work_on_exit()
        elapsed = time.perf_counter() - started_at

        assert handed_off == 0, handed_off
        assert elapsed < 0.1, f"the second detach blocked for {elapsed:.3f}s"
        assert not (_reaper_idents() - reapers_before), (
            "the fallback started a second reaper for an already-detached pool")
        assert runner.pool is None
    finally:
        finished.wait(3.0)


def test_fallback_detaches_a_pool_the_close_flow_never_saw(app):
    """The path with no ``_acquire_close`` before it: detach, never join."""
    runner, finished = _start_busy(app, 1.0)
    try:
        started_at = time.perf_counter()
        # >= 1, not == 1: abandon_all() is process-wide and another test may
        # still own a busy runner in this same interpreter.
        assert app_module.detach_background_work_on_exit() >= 1
        elapsed = time.perf_counter() - started_at

        assert runner.pool is None, "the busy pool was not detached"
        assert elapsed < 0.1, f"the fallback blocked for {elapsed:.3f}s"
    finally:
        finished.wait(3.0)


def test_main_runs_the_fallback_exactly_once_on_return_and_on_exception(monkeypatch):
    """No return path may skip it, no path may run it twice, and it must carry
    the status the process is about to exit with.

    The bounded exit path leaves through ``os._exit`` (P0-9h), which bypasses the
    interpreter's own status handling -- so a fallback that always said 0 would
    turn a startup crash into a success for CI and wrapper scripts.
    """
    calls: list[int] = []
    monkeypatch.setattr(app_module, "detach_background_work_on_exit",
                        lambda exit_code=0: calls.append(exit_code) or 0)
    monkeypatch.setattr(app_module, "_main", lambda argv=None: 7)

    assert app_module.main([]) == 7
    assert calls == [7]

    def boom(argv=None):
        raise RuntimeError("window construction failed")

    monkeypatch.setattr(app_module, "_main", boom)
    with pytest.raises(RuntimeError, match="window construction failed"):
        app_module.main([])
    assert calls == [7, 1], "an exception must not reach the exit hook as success"


def test_fallback_never_raises_into_the_exit_path(monkeypatch):
    """A failing ``abandon_all()`` must not replace the real exit code.

    The hook runs from a ``finally``; if it propagated, a window that failed to
    build would exit with a shutdown error instead of the original traceback.
    """
    def explode(cls):
        raise RuntimeError("abandon_all exploded")

    monkeypatch.setattr(TaskRunner, "abandon_all", classmethod(explode))
    assert app_module.detach_background_work_on_exit() == 0
