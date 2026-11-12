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

What the subprocess tests assert is the *increment after* ``main()`` returned,
not the wall clock of the whole subprocess.  The difference is what made this
file flaky on a loaded runner (#383 Linux regression, run 43240028657 job
131257881108): see ``_run_probe`` and ``POST_RETURN_BOUND_S`` for the
measurement, and the note on ``INPROC_DETACH_BOUND_S`` for the in-process
bound that actually went red there.
"""
from __future__ import annotations

import dataclasses
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

#: The assertion for the subprocess tests: how much of the *still running* task
#: the exit may spend.  Half of it is the shape asked of this fix -- the exit is
#: not coupled to the task as long as less than the remaining task is spent --
#: and both sides have a wide margin: the bounded path spends milliseconds here,
#: while a join spends the whole remaining task (4.11 s measured).
#:
#: Deliberately *not* a bound on ``perf_counter()`` around the whole subprocess:
#: interpreter start-up, the PySide import and process reaping are unrelated to
#: the property and are exactly what a loaded runner inflates.
POST_RETURN_BOUND_S = 0.5 * TASK_S

#: Liveness only: how long the whole subprocess may take before the test reports
#: that the probe never started or never exited.  Far above the ~0.1 s a
#: developer machine spends and above any start-up cost a loaded runner adds,
#: because it must never be the assertion that encodes a property.
LIVENESS_BOUND_S = 20.0

#: In-process task length.  Long enough that "the fallback joined the task" and
#: "the runner was slow to schedule us" are distinguishable: a join lasts the
#: whole remaining task, a stall long enough to matter is a broken machine.
#: The task honours a stop event, so the cleanup of each test stays cheap.
INPROC_TASK_S = 3.0

#: How long a detach that hands a busy pool off may take, in seconds.  It drops
#: a reference and writes one log line, so its cost is bookkeeping plus that
#: append -- and the append is I/O on a shared runner.  #383's Linux runner
#: measured 0.128 s *with the pool detached correctly*, which is what the old
#: 0.1 s bound went red on.  This is a "quick call" budget, not the property:
#: "the call did not join the still running task" is asserted clock-free by
#: ``not finished.is_set()`` below.
INPROC_DETACH_BOUND_S = 0.5

#: The idempotency test's second detach hands nothing off, so it writes no log
#: line and only walks a registry.  That path stays at the original budget: it
#: measures real work, not a file append.
INPROC_NOOP_BOUND_S = 0.1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# --- subprocess probes: the real ``main()`` boundary ------------------------

#: One probe, two modes.  ``return`` and ``raise`` both leave a task running
#: inside ``main()``; the only difference is how the inner body ends.  The
#: legacy branch is what the old code does -- there is no fallback to call, so
#: the probe reproduces the unbounded exit instead of asserting it away.
#:
#: The probe prints monotonic stamps so the assertion can be about the increment
#: after ``main()`` returned instead of the subprocess wall clock.  ``start``
#: also lets the parent prove both processes read the same clock (see
#: ``_run_probe``); ``main-return`` is the instant the assertion measures from.
#: ``PROBE_POST_MAIN_WAIT`` is a test-only injection used by
#: ``test_the_bound_is_live_when_the_exit_waits`` -- the product never sets it.
_PROBE = r'''
import os
import sys
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.getcwd())

STAMP = "PROBE-STAMP"


def stamp(name):
    print(f"{STAMP} {name} {time.monotonic():.6f}", flush=True)


stamp("start")

from PySide6.QtWidgets import QApplication

from folith.qt import app as app_module
from folith.qt.workers import TaskRunner

MODE = sys.argv[1]
TASK_S = float(sys.argv[2])
POST_MAIN_WAIT_S = float(os.environ.get("PROBE_POST_MAIN_WAIT") or 0.0)

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


def body():
    if not (hasattr(app_module, "_main")
            and hasattr(app_module, "detach_background_work_on_exit")):
        # Old code: no exit fallback exists.  Reproduce its shape -- main()
        # returns (or raises) with the pool still busy and nothing detaches it.
        print("legacy: no exit fallback", flush=True)
        busy_inner()
        return 0
    app_module._main = busy_inner
    return app_module.main([])


try:
    code = body()
finally:
    # ``main()`` has already run its own ``finally`` (the fallback) when this
    # runs, so this is the instant the assertion is measured from.  Nothing
    # after it is ours to observe: the bounded path leaves through ``os._exit``
    # from an ``atexit`` hook, which runs *before* any hook registered here.
    stamp("main-return")
    if POST_MAIN_WAIT_S:
        # Injection only: simulate an exit path that really waits after main()
        # returned, so the assertion below can be shown to still fail.
        time.sleep(POST_MAIN_WAIT_S)
print("main returned", code, flush=True)
sys.exit(code)
'''

#: Prefix of the probe's stamps, and the names the parent needs.
_STAMP_MARKER = "PROBE-STAMP"
_STAMP_START = "start"
_STAMP_MAIN_RETURN = "main-return"


def _probe_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    # Keep the fallback's log line and Qt's settings out of the developer's
    # real app-support directory when the probe does not bootstrap the app.
    env["FOLITH_QT_SETTINGS_DIR"] = str(tmp_path / "settings")
    env["FOLITH_DESKTOP_DATA_DIR"] = str(tmp_path / "data")
    env["FOLITH_DESKTOP_LOG_DIR"] = str(tmp_path / "logs")
    return env


@dataclasses.dataclass(frozen=True)
class ProbeRun:
    """One probe execution, with the exit measured on the parent's clock."""

    returncode: int
    stdout: str
    stderr: str
    #: Spawn -> process reaped.  Reported in failure messages, never asserted:
    #: this is the load-sensitive number the old version of this file bounded.
    total_s: float
    #: The child's ``main()`` return stamp -> the parent reaping the process.
    #: This is the property: it only contains what the exit path itself did.
    post_return_s: float
    #: Child start stamp minus the parent's spawn stamp.  Both processes must
    #: read the same system-wide monotonic clock for ``post_return_s`` to mean
    #: anything, and this difference is what proves it.
    start_offset_s: float
    stamps: dict[str, float]


def _stamps(stdout: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in stdout.splitlines():
        if line.startswith(_STAMP_MARKER):
            _, name, value = line.split()
            found[name] = float(value)
    return found


def _run_probe(tmp_path: Path, mode: str,
               *, post_main_wait_s: float = 0.0) -> ProbeRun:
    """Run the probe and measure from its ``main()`` return to process death.

    Why the parent supplies one end of the interval: the bounded path leaves
    through ``os._exit`` inside the product's ``atexit`` hook, so no Python line
    of ours can run after ``main()`` returns there (verified: two hooks, one
    registered before PySide and one after the product's, and neither fires on
    the bounded path).  The child therefore stamps the start of the interval with
    its own monotonic clock and the parent stamps the end with its own -- the
    same clock, on every platform this project ships to, and ``start_offset_s``
    is asserted to be a plausible spawn delay so an unshared epoch fails loudly
    instead of silently turning the bound green.
    """
    script = tmp_path / f"exit_fallback_probe_{mode}.py"
    script.write_text(_PROBE, encoding="utf-8")
    env = _probe_env(tmp_path)
    if post_main_wait_s:
        env["PROBE_POST_MAIN_WAIT"] = str(post_main_wait_s)

    spawned_at = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, str(script), mode, str(TASK_S)],
        cwd=str(ROOT), env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=LIVENESS_BOUND_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        pytest.fail(
            f"the probe neither started nor exited within "
            f"{LIVENESS_BOUND_S:.0f}s (liveness bound, not the exit property); "
            f"stdout={stdout[-2000:]!r} stderr={stderr[-2000:]!r}")
    reaped_at = time.monotonic()

    stamps = _stamps(stdout)
    missing = {_STAMP_START, _STAMP_MAIN_RETURN} - set(stamps)
    assert not missing, (
        f"the probe did not print the {sorted(missing)} stamp(s), so the exit "
        f"was not measured; stdout={stdout[-2000:]!r} "
        f"stderr={stderr[-2000:]!r}")
    start_offset_s = stamps[_STAMP_START] - spawned_at
    assert 0.0 <= start_offset_s < LIVENESS_BOUND_S, (
        f"the child's monotonic clock does not share the parent's epoch "
        f"(child {_STAMP_START} minus parent spawn = {start_offset_s:+.3f}s); "
        "the post-main() bound compares a child stamp with the parent's reaping "
        "time and is only valid on one system-wide monotonic clock")
    return ProbeRun(
        returncode=proc.returncode, stdout=stdout, stderr=stderr,
        total_s=reaped_at - spawned_at,
        post_return_s=reaped_at - stamps[_STAMP_MAIN_RETURN],
        start_offset_s=start_offset_s, stamps=stamps)


def _assert_exit_does_not_wait_for_the_task(run: ProbeRun, how: str) -> None:
    """Assert the exit is not coupled to the task ``main()`` left running."""
    assert run.post_return_s < POST_RETURN_BOUND_S, (
        f"the process needed {run.post_return_s:.2f}s to exit after main() "
        f"{how} with a {TASK_S:.0f}s task still running (bound "
        f"{POST_RETURN_BOUND_S:.1f}s, half the remaining task); the pool was "
        "joined instead of detached. [measured from the child's main() return "
        f"stamp to the parent reaping it; the whole subprocess took "
        f"{run.total_s:.2f}s, which is not part of this bound]")


def test_main_returning_does_not_wait_for_a_running_task(tmp_path):
    """``main()`` returns, the 4 s task is still running: exit must be bounded."""
    run = _run_probe(tmp_path, "return")
    assert run.returncode == 0, run.stderr[-2000:]
    assert "busy" in run.stdout, run.stdout + run.stderr[-2000:]
    assert "main returned 0" in run.stdout
    _assert_exit_does_not_wait_for_the_task(run, "returned")
    log = tmp_path / "logs" / "qt-client.log"
    assert "main() fallback" in log.read_text(encoding="utf-8"), (
        "the fallback detached the pool but did not say so in the client log")


def test_main_raising_still_detaches_and_exits_bounded(tmp_path):
    """The ``finally`` must cover exceptions, not only a clean return."""
    run = _run_probe(tmp_path, "raise")
    assert run.returncode != 0
    assert "RuntimeError: window construction failed" in run.stderr
    _assert_exit_does_not_wait_for_the_task(run, "raised")


def test_the_bound_is_live_when_the_exit_waits(tmp_path):
    """Injection: a probe that really waits after ``main()`` must go red.

    The other two assertions are only worth the measurement they are built on,
    so this test makes the exit wait 1.25x the bound after ``main()`` returned
    (the shape of a fallback that joined the pool) and requires the measured
    increment to exceed it.  If the stamps ever stopped bracketing the real
    wait -- a stale stamp, a re-baselined clock -- every other assertion in this
    module would pass for the wrong reason and this one would fail.
    """
    injected_s = 1.25 * POST_RETURN_BOUND_S
    run = _run_probe(tmp_path, "return", post_main_wait_s=injected_s)
    assert run.returncode == 0, run.stderr[-2000:]
    assert run.post_return_s >= 0.9 * injected_s, (
        f"the injected {injected_s:.2f}s wait after main() was measured as only "
        f"{run.post_return_s:.2f}s, so the measurement does not bracket the exit")
    assert run.post_return_s > POST_RETURN_BOUND_S, (
        f"a probe that waited {injected_s:.2f}s after main() returned was "
        f"measured as {run.post_return_s:.2f}s and would still pass "
        f"test_main_returning_does_not_wait_for_a_running_task: the bound is "
        "dead")


# --- in-process: idempotency and shutdown safety ----------------------------


def _start_busy(app: QApplication, work_s: float
                ) -> tuple[TaskRunner, threading.Event, threading.Event]:
    """Submit work that is provably running; return (runner, finished, stop).

    The work runs for ``work_s`` unless ``stop`` is set, so a test can end it
    early instead of paying for the whole task in its cleanup.
    """
    runner = TaskRunner()
    started = threading.Event()
    finished = threading.Event()
    stop = threading.Event()

    def work():
        started.set()
        try:
            deadline = time.monotonic() + work_s
            while not stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                stop.wait(min(remaining, 0.05))
        finally:
            finished.set()

    runner.submit(work, label="exit-fallback-idempotent")
    deadline = time.monotonic() + 5.0
    while not started.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert started.is_set(), "the worker never started"
    return runner, finished, stop


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
    runner, finished, stop = _start_busy(app, INPROC_TASK_S)
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
        # This path does no I/O and starts no thread, so the tight budget holds.
        assert elapsed < INPROC_NOOP_BOUND_S, (
            f"the second detach blocked for {elapsed:.3f}s")
        assert not (_reaper_idents() - reapers_before), (
            "the fallback started a second reaper for an already-detached pool")
        assert runner.pool is None
    finally:
        stop.set()
        finished.wait(3.0)


def test_fallback_detaches_a_pool_the_close_flow_never_saw(app):
    """The path with no ``_acquire_close`` before it: detach, never join."""
    runner, finished, stop = _start_busy(app, INPROC_TASK_S)
    try:
        started_at = time.perf_counter()
        # >= 1, not == 1: abandon_all() is process-wide and another test may
        # still own a busy runner in this same interpreter.
        assert app_module.detach_background_work_on_exit() >= 1
        elapsed = time.perf_counter() - started_at

        assert runner.pool is None, "the busy pool was not detached"
        # The exact proof that the call did not join: the task it was supposed
        # to leave running is still running.  No clock is involved, so a loaded
        # runner cannot turn a correct detach red here.
        assert not finished.is_set(), (
            "the fallback returned only after the running task had finished")
        # ... and this catches a partial, bounded wait.  Its value is a "quick
        # call" budget (thread bookkeeping plus one log append on a shared
        # runner), not the proof above.
        assert elapsed < INPROC_DETACH_BOUND_S, (
            f"the fallback blocked for {elapsed:.3f}s, more than the "
            f"{INPROC_DETACH_BOUND_S:.1f}s budget for a detach that only drops a "
            "reference")
    finally:
        stop.set()
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
