"""E-6p: the lifecycle probe's liveness/wait criteria are subprocess-aware.

The probe used to answer "is this task running?" with
``core.is_job_worker_alive`` — a worker *thread* of the probe's own process.
Under ``FOLITH_ENGINE_PROCESS=1`` the pipeline runs in an engine child, so that
probe is false forever and the lifecycle matrix would report "not running" for
a task that is running.

These tests pin the two halves of the replacement:

1. liveness comes from ``adapter.job_is_alive`` (the entry ``window.py`` uses),
   never from the thread probe — the stubs below have no
   ``is_job_worker_alive`` at all, so any regression raises ``AttributeError``;
2. a wait that times out reports the current state (status, liveness, engine
   child) instead of quietly returning ``False``;
3. E-6q: "execution finished" also requires the supervisor to have **forgotten**
   the child's handle.  ``job_is_alive`` goes false when the ownership token is
   released, and ``_forget`` happens a poll interval later; a wait that stopped
   at the first half read a leftover ``Popen`` handle after cancel — the
   Windows-only red this file now pins without Windows.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

import core


def _probe():
    """Import the probe without needing Qt when it is absent (it imports selftest)."""
    pytest.importorskip("PySide6")
    return importlib.import_module("scripts.qt_lifecycle_probe")


class _FakeAdapter:
    """Only the entries the probe may use.

    ``forbid_thread_probe`` makes the old decision probe (``is_job_worker_alive``)
    raise if a *criterion* touches it: the snapshot still records it as evidence,
    the decision must never be built on it.
    """

    def __init__(self, *, alive: bool, status: str, worker_id: str = "w-1",
                 start_in_flight: bool = False, lock_held: bool = False,
                 thread_alive: bool = False,
                 forbid_thread_probe: bool = False) -> None:
        self._alive = alive
        self._status = status
        self._start_in_flight = start_in_flight
        self._lock_held = lock_held
        self._thread_alive = thread_alive
        self._forbid_thread_probe = forbid_thread_probe
        self._runtime = {"status": status, "worker": {"worker_id": worker_id}}

        def thread_probe(_job_id: str) -> bool:
            if self._forbid_thread_probe:
                raise AssertionError("the thread probe must not decide liveness")
            return self._thread_alive

        self.core = SimpleNamespace(
            RUNTIME_ACTIVE_STATUSES=set(core.RUNTIME_ACTIVE_STATUSES),
            load_runtime_state=lambda _job: dict(self._runtime),
            is_job_worker_alive=thread_probe,
        )

    def job_is_alive(self, _job_id: str) -> bool:
        return self._alive

    def job_start_in_flight(self, _job_id: str) -> bool:
        return self._start_in_flight

    def load_state(self, _job_id: str) -> dict:
        return {"p1_done": True}

    def runtime_view(self, _job_id: str, _state=None) -> dict:
        return {"status": self._status}

    def exit_report(self, _job_id: str) -> dict:
        return {"worker_lock_held": self._lock_held}


def _install(monkeypatch, adapter) -> None:
    monkeypatch.setattr(_probe(), "_probe_adapter", lambda: adapter)


# ---------------------------------------------------------------------------
# liveness: adapter, not threads
# ---------------------------------------------------------------------------

def test_a_queued_engine_child_counts_as_processing(monkeypatch):
    _install(monkeypatch, _FakeAdapter(alive=True, status="queued",
                                       forbid_thread_probe=True))
    probe = _probe()
    assert probe._is_processing("job") is True


def test_a_live_child_owned_task_is_alive_even_without_a_local_thread(monkeypatch):
    # alive=True is exactly what adapter.job_is_alive reports when the run lives
    # in an engine child; the stub has no is_job_worker_alive, so a regression
    # that went back to the thread probe would raise instead of passing.
    _install(monkeypatch, _FakeAdapter(alive=True, status="running",
                                       forbid_thread_probe=True))
    probe = _probe()
    assert probe._is_processing("job") is True
    assert probe._execution_finished("job") is False


def test_a_child_that_has_not_published_its_run_is_not_processing_yet(monkeypatch):
    # Cold start: the process exists, but core.start_job_worker has not published
    # `worker_id`; a pause/cancel sent now would be reset by that publication.
    _install(monkeypatch, _FakeAdapter(alive=True, status="queued", worker_id="",
                                       forbid_thread_probe=True))
    probe = _probe()
    assert probe._owner_published("job") is False
    assert probe._is_processing("job") is False


def test_idle_and_not_alive_is_not_processing(monkeypatch):
    _install(monkeypatch, _FakeAdapter(alive=False, status="idle",
                                       forbid_thread_probe=True))
    assert _probe()._is_processing("job") is False


@pytest.mark.parametrize("alive,status,start_in_flight,expected", [
    (False, "interrupted", False, True),
    (False, "completed", False, True),
    (False, "cancelled", False, True),
    (False, "running", False, False),        # still active: not finished
    (False, "paused", False, False),         # paused keeps the run (and its lock)
    (True, "interrupted", False, False),     # child still exiting
    (False, "interrupted", True, False),     # a start this client just asked for
])
def test_execution_finished_uses_liveness_and_the_active_status_set(
        monkeypatch, alive, status, start_in_flight, expected):
    _install(monkeypatch, _FakeAdapter(
        alive=alive, status=status, start_in_flight=start_in_flight,
        forbid_thread_probe=True))
    assert _probe()._execution_finished("job") is expected


# ---------------------------------------------------------------------------
# the wait primitives
# ---------------------------------------------------------------------------

def test_wait_for_start_returns_when_the_child_is_processing(monkeypatch):
    _install(monkeypatch, _FakeAdapter(alive=True, status="running"))
    probe = _probe()
    seen: list = []

    def pump(seconds, predicate=None):
        seen.append(seconds)
        return bool(predicate()) if predicate is not None else True

    assert probe.pump_until_processing(pump, 5, "job") is True
    assert seen == [5]


def test_wait_for_finish_accepts_a_gone_child_with_a_terminal_status(monkeypatch):
    _install(monkeypatch, _FakeAdapter(alive=False, status="interrupted"))
    probe = _probe()

    def pump(seconds, predicate=None):
        return bool(predicate()) if predicate is not None else True

    report: dict = {}
    assert probe.pump_until_finished(pump, 5, ["a", "b"], report=report) is True
    assert report["waits"][-1]["timed_out"] is False


def test_timeout_raises_with_the_current_state_instead_of_returning_false(monkeypatch):
    _install(monkeypatch, _FakeAdapter(alive=True, status="running"))
    probe = _probe()

    def pump(_seconds, _predicate=None):
        return False

    report: dict = {}
    with pytest.raises(probe.ProbeTimeout) as caught:
        probe.pump_until_finished(pump, 0.01, "job", report=report, label="exit")
    message = str(caught.value)
    assert "超时未达成" in message
    assert '"status": "running"' in message
    assert '"alive": true' in message
    assert caught.value.snapshots[0]["status"] == "running"
    assert report["waits"][-1]["timed_out"] is True
    assert report["last_timeout"]["state"][0]["status"] == "running"


def test_exit_settled_also_waits_for_the_worker_lock(monkeypatch):
    adapter = _FakeAdapter(alive=False, status="interrupted", lock_held=True)
    _install(monkeypatch, adapter)
    probe = _probe()

    def pump(_seconds, predicate=None):
        return bool(predicate()) if predicate is not None else True

    with pytest.raises(probe.ProbeTimeout):
        probe._exit_settled(pump, 0.01, "job")
    adapter._lock_held = False
    assert probe._exit_settled(pump, 0.01, "job") is True


def test_snapshot_records_what_the_old_thread_probe_would_say(monkeypatch):
    """Evidence: a child-owned run is "alive" while the thread probe is false."""
    _install(monkeypatch, _FakeAdapter(alive=True, status="running",
                                       thread_alive=False))
    info = _probe()._runtime_snapshot("job")
    assert info["alive"] is True
    assert info["worker_thread"] is False


def test_snapshot_reports_the_engine_child_handle(monkeypatch):
    """A timeout must say whether a child still exists — support detail, not truth."""
    _install(monkeypatch, _FakeAdapter(alive=True, status="running"))
    probe = _probe()

    class _Child:
        def describe(self):
            return {"pid": 4242, "running": True, "exit_code": None}

        def output_tail(self):
            return "engine output"

    import folith.desktop.engine_supervisor as engine_supervisor
    monkeypatch.setattr(engine_supervisor, "supervisor", lambda _job: _Child())
    info = probe._runtime_snapshot("job")
    assert info["alive"] is True
    assert info["engine_child"]["pid"] == 4242
    assert info["engine_child"]["output_tail"] == "engine output"


# ---------------------------------------------------------------------------
# E-6q: "finished" means the supervisor forgot the child, not merely "not alive"
# ---------------------------------------------------------------------------

class _LeftoverChild:
    """The describe()/output_tail() surface a snapshot reads from a stale handle."""

    def describe(self) -> dict:
        # ``running`` is False and ``exit_code`` is None: exactly what the
        # Windows failure message showed — the child had exited, but the monitor
        # had not yet published and forgotten it.
        return {"pid": 4242, "running": False, "exit_code": None}

    def output_tail(self) -> str:
        return ""


def _patch_supervisor(monkeypatch, getter) -> None:
    import folith.desktop.engine_supervisor as engine_supervisor
    monkeypatch.setattr(engine_supervisor, "supervisor", getter)


def test_execution_finished_requires_the_supervisor_to_forget_the_child(monkeypatch):
    """E-6q: terminal status + no live owner is not yet "the child is gone".

    ``EngineSupervisor._watch`` releases the ownership token (which is what makes
    ``adapter.job_is_alive`` false) *before* it forgets the handle, so the two
    answers disagree for a poll interval.  The Windows run 43399538998 read the
    handle inside that window and reported a leftover engine child after cancel.
    """
    _install(monkeypatch, _FakeAdapter(alive=False, status="cancelled"))
    probe = _probe()
    _patch_supervisor(monkeypatch, lambda _job: _LeftoverChild())
    assert probe._engine_child_settled("job") is False
    assert probe._execution_finished("job") is False
    _patch_supervisor(monkeypatch, lambda _job: None)
    assert probe._engine_child_settled("job") is True
    assert probe._execution_finished("job") is True


def test_a_job_without_a_supervisor_is_settled(monkeypatch):
    """Thread mode (and a never-supervised job) has no handle to wait for."""
    _install(monkeypatch, _FakeAdapter(alive=False, status="cancelled"))
    _patch_supervisor(monkeypatch, lambda _job: None)
    assert _probe()._engine_child_settled("job") is True


def test_wait_for_finish_does_not_end_on_a_leftover_dead_child_handle(monkeypatch):
    """The E-6q race reproduced without Windows, deterministically.

    The durable answer is terminal from the first check, and the dead child's
    handle stays registered for two more pump turns.  The wait must survive
    those turns: ending on the first one is what produced
    ``assert report["cancel"]["engine_child"] is None`` failing on Windows.
    """
    _install(monkeypatch, _FakeAdapter(alive=False, status="cancelled"))
    probe = _probe()
    registry: dict = {"job": _LeftoverChild()}
    _patch_supervisor(monkeypatch, lambda job: registry.get(job))
    checks = {"n": 0}

    def pump(_seconds, predicate=None):
        # One event-loop turn per call to the predicate, like the real pump.
        for _ in range(10):
            checks["n"] += 1
            if checks["n"] == 3:      # the monitor reaps, publishes and forgets
                registry.pop("job", None)
            if predicate is not None and predicate():
                return True
        return False

    report: dict = {}
    assert probe.pump_until_finished(pump, 5, "job", report=report,
                                     label="lifecycle-main-cancel") is True
    assert checks["n"] == 3, "the wait ended before the handle was forgotten"
    assert report["waits"][-1]["timed_out"] is False
    assert registry == {}


def test_wait_for_finish_timeout_reports_the_leftover_child_handle(monkeypatch):
    """A handle that is never forgotten is a timeout with evidence, not a pass."""
    _install(monkeypatch, _FakeAdapter(alive=False, status="cancelled"))
    probe = _probe()
    _patch_supervisor(monkeypatch, lambda _job: _LeftoverChild())

    def pump(_seconds, _predicate=None):
        return False

    report: dict = {}
    with pytest.raises(probe.ProbeTimeout) as caught:
        probe.pump_until_finished(pump, 0.01, "job", report=report,
                                  label="lifecycle-main-cancel")
    assert caught.value.snapshots[0]["engine_child"]["pid"] == 4242
    assert report["waits"][-1]["timed_out"] is True


# ---------------------------------------------------------------------------
# the benchmark's second call point
# ---------------------------------------------------------------------------

def test_benchmark_worker_running_uses_the_same_entry_as_the_window(monkeypatch):
    pytest.importorskip("PySide6")
    from benchmarks.performance import runner
    from folith.qt import adapter

    session = SimpleNamespace(job_id="job-1")
    monkeypatch.setattr(adapter, "job_is_alive", lambda job: job == "job-1")
    assert runner.BenchSession.worker_running(session) is True
    monkeypatch.setattr(adapter, "job_is_alive", lambda job: False)
    assert runner.BenchSession.worker_running(session) is False
