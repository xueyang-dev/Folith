"""E-6u — every ``update_runtime_state`` caller declares what a lock timeout means.

E-6t bounded the ``.runtime-write.lock`` acquire at two seconds and turned the
give-up into ``JobLockTimeout``.  That is correct for the hang it fixed (POSIX
``flock`` waited forever, Windows ``msvcrt.locking`` burned ten whole seconds),
but it made *every* caller a new failure surface: 44 call sites with three very
different meanings.

This module pins the three categories the call sites were classified into:

* **GUI/action** — a user is waiting on delete/cancel/pause/continue/launch, so
  the timeout must travel up to the interface with a readable "retry later"
  message and must not leave a half-written state behind;
* **progress/heartbeat** — a periodic internal write skips only this tick, the
  next tick publishes the newest values, and the heartbeat thread stays alive;
* **terminal** — completed / interrupted / failed / cancelled / idle_incomplete
  get bounded retries and, when every attempt fails, an explicit durable record
  instead of a silently stuck "running" task.

The injections are all monkeypatched: no test waits out the two-second bound,
and no test changes it.
"""
from __future__ import annotations

import threading

import pytest

import core
from folith import job_lock


class LockTimeouts:
    """Make the next ``remaining`` bounded acquires raise ``JobLockTimeout``.

    Patched on the real :mod:`folith.job_lock` module, i.e. exactly the object
    ``update_runtime_state`` calls, and reverted by ``monkeypatch``.
    """

    def __init__(self, monkeypatch):
        self.remaining = 0
        self.calls = []
        original = job_lock.exclusive

        def flaky(path, *, timeout=None):
            self.calls.append(path)
            if self.remaining > 0:
                self.remaining -= 1
                raise job_lock.JobLockTimeout(
                    f"另一个 Folith 进程持有 {path.name} 超过 {timeout:g} 秒；"
                    "本次更新未写入，请稍后重试。")
            return original(path, timeout=timeout)

        monkeypatch.setattr(job_lock, "exclusive", flaky)


@pytest.fixture()
def lock_timeouts(monkeypatch):
    return LockTimeouts(monkeypatch)


@pytest.fixture()
def job(tmp_path, monkeypatch):
    """An isolated task that is running and owned by ``worker-1``."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "e6u-lock-timeout"
    core.save_job_state(job_id, core.new_job_state("fixture.pdf"))
    core.update_runtime_state(
        job_id, status="running", phase="translating", phase_label="正在翻译",
        worker={"owner_pid": 4242, "worker_id": "worker-1",
                "lease_expires_at": "2000-01-01T00:00:00+00:00"},
        event="已开始", event_visibility="user", event_category="progress")
    return job_id


def stats_delta(before):
    """Counters are process-wide; every assertion here is a delta."""
    after = core.runtime_write_timeout_stats()
    return {key: after[key] - before[key] for key in after}


class GatedTicks:
    """A ``stop_event`` stand-in that blocks on the tick *after* the assertions.

    ``wait`` is what the heartbeat loop sleeps on; incrementing a counter lets a
    test run real ticks without waiting ``RUNTIME_HEARTBEAT_SECONDS``, and the
    gate keeps the loop alive while the test asserts on the thread.
    """

    def __init__(self, block_at_tick, gate, reached):
        self.tick = 0
        self.block_at_tick = block_at_tick
        self.gate = gate
        self.reached = reached

    def wait(self, _interval):
        self.tick += 1
        if self.tick >= self.block_at_tick:
            self.reached.set()
            self.gate.wait(10)
            return True
        return False


# ---------------------------------------------------------------------------
# the default contract is unchanged
# ---------------------------------------------------------------------------


def test_without_a_policy_a_timeout_still_raises(job, lock_timeouts):
    lock_timeouts.remaining = 1
    before = core.runtime_write_timeout_stats()
    with pytest.raises(job_lock.JobLockTimeout):
        core.update_runtime_state(job, status="running")
    assert stats_delta(before)["skipped"] == 0
    assert core.load_runtime_state(job)["status"] == "running"


def test_an_unknown_policy_is_rejected_instead_of_silently_defaulting(job):
    with pytest.raises(ValueError):
        core.update_runtime_state(job, on_timeout="ignore", status="running")


# ---------------------------------------------------------------------------
# GUI / action side: the error reaches the interface with a readable message
# ---------------------------------------------------------------------------


def test_gui_pause_timeout_reaches_the_adapter_with_a_retry_message(
        job, lock_timeouts, monkeypatch):
    """The window's pause action calls ``adapter.pause_job``; nothing swallows it.

    ``folith/qt/window.py::pause_job`` does not wrap the call in ``try/except``,
    so the assertion is made at the Qt boundary: the exception is intact there
    (not converted to ``False``/a silent no-op) and carries the readable
    "please retry" text the window would show.
    """
    from folith.qt import adapter

    monkeypatch.setattr(core, "is_job_worker_alive", lambda job_id: True)
    lock_timeouts.remaining = 1

    with pytest.raises(job_lock.JobLockTimeout) as excinfo:
        adapter.pause_job(job)

    message = str(excinfo.value)
    assert "稍后重试" in message
    assert "未写入" in message
    # The refused write must not pretend it landed.
    runtime = core.load_runtime_state(job)
    assert runtime["status"] == "running"
    assert not runtime.get("pause_requested")


def test_gui_retry_step_timeout_is_not_swallowed(job, lock_timeouts):
    core.update_runtime_state(job, status="failed",
                              error={"message": "provider timeout"})
    lock_timeouts.remaining = 10
    with pytest.raises(job_lock.JobLockTimeout) as excinfo:
        core.retry_job_step(job)
    assert "稍后重试" in str(excinfo.value)
    assert core.load_runtime_state(job)["status"] == "failed"


def test_delete_cancel_timeout_becomes_a_readable_refusal(monkeypatch):
    """A delete whose cancel request cannot be written says so and deletes nothing.

    This is the delete/GUI side of E-6u: ``_deliver_cancel`` used to swallow
    every exception, so a refused write looked like "the task ignored cancel".
    """
    from folith.engine import projects as engine_projects

    monkeypatch.setattr(engine_projects, "DELETE_CANCEL_SETTLE_SECONDS", 0.2)
    monkeypatch.setattr(engine_projects, "DELETE_CANCEL_REQUEST_SECONDS", 0.2)
    monkeypatch.setattr(engine_projects, "_job_still_running",
                        lambda job_id: True)

    def write_times_out(*_args, **_kwargs):
        raise job_lock.JobLockTimeout(
            "另一个 Folith 进程持有 .runtime-write.lock 超过 2 秒；"
            "本次更新未写入，请稍后重试。")

    # ``engine_projects`` reads core through a late-bound handle, so patching
    # the module attribute is what the helper actually calls.
    monkeypatch.setattr(core, "request_job_cancel", write_times_out)

    with pytest.raises(ValueError) as excinfo:
        engine_projects.stop_job_for_delete("e6u-delete-probe")

    message = str(excinfo.value)
    assert "稍后重试" in message
    assert "没有删除" in message
    assert "取消请求" in message
    assert "2 秒" in message
    assert engine_projects.delete_stop_stats()["cancel_error"]


def test_a_cancel_request_still_waiting_for_the_lock_is_reported(monkeypatch):
    """The pending-cancel case is the common one (2 s bound > 0.5 s deadline)."""
    from folith.engine import projects as engine_projects

    monkeypatch.setattr(engine_projects, "DELETE_CANCEL_SETTLE_SECONDS", 0.2)
    monkeypatch.setattr(engine_projects, "DELETE_CANCEL_REQUEST_SECONDS", 0.02)
    # First liveness probe: running (so the stop is attempted); later probes:
    # stopped, so the only reason left to refuse is the still-pending request.
    calls = {"n": 0}

    def liveness(_job_id):
        calls["n"] += 1
        return calls["n"] <= 1

    monkeypatch.setattr(engine_projects, "_job_still_running", liveness)
    release = threading.Event()

    def write_stays_blocked(*_args, **_kwargs):
        release.wait(10)
        raise job_lock.JobLockTimeout(
            "另一个 Folith 进程持有 .runtime-write.lock 超过 2 秒；"
            "本次更新未写入，请稍后重试。")

    monkeypatch.setattr(core, "request_job_cancel", write_stays_blocked)
    try:
        with pytest.raises(ValueError) as excinfo:
            engine_projects.stop_job_for_delete("e6u-delete-pending")
    finally:
        release.set()
        with engine_projects._DELETE_CANCEL_THREADS_LOCK:
            threads = list(engine_projects._DELETE_CANCEL_THREADS)
        for thread in threads:
            thread.join(10)

    text = str(excinfo.value)
    assert "状态锁" in text
    assert "稍后重试" in text
    assert "没有删除" in text


# ---------------------------------------------------------------------------
# progress / heartbeat side: skip this tick, keep the thread, write next tick
# ---------------------------------------------------------------------------


def test_a_skipped_progress_write_keeps_the_task_running(job, lock_timeouts):
    lock_timeouts.remaining = 1
    before = core.runtime_write_timeout_stats()
    result = core.update_runtime_state(
        job, on_timeout="skip", progress=True, event="已处理 3 批")
    assert result is core.RUNTIME_WRITE_SKIPPED
    assert core.load_runtime_state(job)["status"] == "running"
    assert stats_delta(before)["skipped"] == 1
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "skipped on this tick" in log


def test_the_heartbeat_tick_is_skipped_and_the_next_one_still_writes(
        job, lock_timeouts):
    """Real thread, real loop: one timed-out tick, then the lease is renewed."""
    lock_timeouts.remaining = 1
    gate, reached = threading.Event(), threading.Event()
    before = core.load_runtime_state(job)["last_heartbeat_at"]
    before_stats = core.runtime_write_timeout_stats()

    thread = threading.Thread(
        target=core._runtime_heartbeat_loop,
        args=(job, GatedTicks(3, gate, reached), "worker-1"),
        name="e6u-heartbeat", daemon=True)
    thread.start()
    try:
        assert reached.wait(10), "the heartbeat loop never reached the gate"
        assert thread.is_alive(), "a skipped tick must not end the heartbeat thread"
        assert len(lock_timeouts.calls) >= 2
        # Tick 2 ran after the skipped tick and published a newer heartbeat.
        assert core.load_runtime_state(job)["last_heartbeat_at"] != before
        assert stats_delta(before_stats)["skipped"] == 1
    finally:
        gate.set()
        thread.join(10)
    assert not thread.is_alive()
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "skipped on this tick" in log


def test_an_exception_outside_the_tick_is_logged_instead_of_killing_the_thread(job):
    """The outermost guard: even a broken wait primitive leaves a trace."""
    class ExplodingWait:
        def wait(self, _timeout):
            raise RuntimeError("event primitive broke")

    core._runtime_heartbeat_loop(job, ExplodingWait(), "worker-1")

    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "heartbeat thread stopped unexpectedly" in log
    assert "RuntimeError" in log


# ---------------------------------------------------------------------------
# terminal side: bounded retries; a final loss is explicit, never silent
# ---------------------------------------------------------------------------


def test_a_terminal_write_retries_and_lands(job, lock_timeouts, monkeypatch):
    monkeypatch.setattr(core, "RUNTIME_WRITE_RETRY_PAUSE_SECONDS", 0.0)
    lock_timeouts.remaining = 2
    before = core.runtime_write_timeout_stats()

    published = core._write_terminal_runtime_state(
        job, what="job_completed", status="completed", phase="completed",
        phase_label="已完成", event="任务已完成", event_visibility="user",
        event_category="lifecycle")

    assert published is not None
    assert published["status"] == "completed"
    assert len(lock_timeouts.calls) == 3
    assert stats_delta(before)["retried"] == 1
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "needed 3 bounded attempts" in log


def test_a_terminal_write_that_never_lands_is_reported_not_swallowed(
        job, lock_timeouts, monkeypatch):
    monkeypatch.setattr(core, "RUNTIME_WRITE_RETRY_PAUSE_SECONDS", 0.0)
    lock_timeouts.remaining = 99
    before = core.runtime_write_timeout_stats()

    published = core._write_terminal_runtime_state(
        job, what="job_completed", status="completed", phase="completed")

    assert published is None
    assert len(lock_timeouts.calls) == core.RUNTIME_WRITE_RETRY_ATTEMPTS
    # Nothing half-written, and the loss is durable and countable.
    assert core.load_runtime_state(job)["status"] == "running"
    assert stats_delta(before)["lost"] == 1
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "write lost after" in log
    assert "terminal runtime state (job_completed) could not be published" in log


def test_a_retry_policy_without_the_helper_still_raises_after_its_attempts(
        job, lock_timeouts, monkeypatch):
    """GUI terminal writes (cancel/exit with no worker) report, not swallow."""
    monkeypatch.setattr(core, "RUNTIME_WRITE_RETRY_PAUSE_SECONDS", 0.0)
    lock_timeouts.remaining = 99
    before = core.runtime_write_timeout_stats()
    with pytest.raises(job_lock.JobLockTimeout):
        core.update_runtime_state(job, on_timeout="retry", status="cancelled")
    assert len(lock_timeouts.calls) == core.RUNTIME_WRITE_RETRY_ATTEMPTS
    assert stats_delta(before)["lost"] == 1


def test_a_lost_failed_state_does_not_kill_the_worker_or_lie_about_success(
        job, monkeypatch):
    """The worker's terminal boundary reports the loss instead of rebranding.

    If the ``failed`` write could not land, the job must not silently turn into
    "failed anyway" (it never was published) and the thread must not die: the
    durable technical log and the counters carry the truth.
    """
    monkeypatch.setattr(core, "RUNTIME_WRITE_RETRY_PAUSE_SECONDS", 0.0)
    real_writer = core.update_runtime_state

    def fail_only_terminal(*args, **kwargs):
        if kwargs.get("status") == "failed":
            raise job_lock.JobLockTimeout("状态锁被占用；请稍后重试。")
        return real_writer(*args, **kwargs)

    def boom(*_args, **_kwargs):
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(core, "update_runtime_state", fail_only_terminal)
    monkeypatch.setattr(core, "run_job_pipeline", boom)
    try:
        core._run_job_worker(job, "fixture.pdf", None, {"enable_report": False})
    finally:
        core._RUNTIME_WORKERS.clear()

    runtime = core.load_runtime_state(job)
    assert runtime["status"] != "failed", "an unpublished failed state is a lie"
    log = core.runtime_technical_log_path(job).read_text(encoding="utf-8")
    assert "job_failed" in log
    assert "could not be published" in log
