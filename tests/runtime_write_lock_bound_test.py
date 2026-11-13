"""E-6t — ``.runtime-write.lock`` is acquired with a bounded deadline.

M-1 split the 7.063 s Windows delete into ``4.063 s`` "request cancel" plus
``3.000 s`` "settle".  The 4.063 s is *four whole seconds + 63 ms* — the
fingerprint of ``msvcrt.locking(LK_LOCK)``, the blocking acquire that
``core.update_runtime_state`` used for ``.runtime-write.lock``.  This module
holds the same OS lock from a separate process and asserts the caller returns
inside the declared bound instead of waiting out the holder, on macOS and on
Windows (the Windows risk job uploads the failing log/artifact).

Every test here has a "what the bug looked like" control: hold the lock longer
than the bound, and the bounded predicate fails.  Reverting the acquisition to
blocking therefore turns the first test red (it waits the whole 3 s and then the
update succeeds, so ``pytest.raises`` sees no exception).
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
import warnings

import pytest

import core
from folith import job_lock

ROOT = Path(__file__).resolve().parent.parent

#: The holder keeps the lock longer than the bound on purpose.  If the acquire
#: ever goes back to blocking, the caller is parked for this whole window
#: (locally) or for the whole-second retry ladder up to ten seconds (Windows).
HOLD_SECONDS = 3.0

#: Slack above the declared bound: poll interval, the Windows 15.6 ms timer,
#: process wake-up jitter.  Deliberately not a second, larger bound.
SLACK_SECONDS = 0.5

#: A foreign write shorter than the bound must be waited out, not refused —
#: otherwise "bounded" could be satisfied by failing immediately.
SHORT_HOLD_SECONDS = 0.3


class _LockHolder:
    """A separate process that really holds the OS lock, then lets go."""

    _SCRIPT = textwrap.dedent(
        """
        import sys
        import time
        from pathlib import Path
        from folith import job_lock

        stream = job_lock.acquire(Path(sys.argv[1]))
        print("locked", flush=True)
        try:
            time.sleep(float(sys.argv[2]))
        finally:
            stream.close()
        """)

    def __init__(self, path: Path, seconds: float) -> None:
        env = dict(os.environ)
        existing = [part for part in (env.get("PYTHONPATH") or "").split(os.pathsep)
                    if part]
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT), *existing])
        self.process = subprocess.Popen(
            [sys.executable, "-c", self._SCRIPT, str(path), str(seconds)],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self._error: str | None = None

    def wait_locked(self, timeout: float = 20.0) -> "_LockHolder":
        ready = threading.Event()

        def read_first_line() -> None:
            try:
                line = self.process.stdout.readline() or ""
            except (OSError, ValueError):  # pragma: no cover - pipe torn down
                return
            if line.strip() == "locked":
                ready.set()

        threading.Thread(target=read_first_line, daemon=True).start()
        if not ready.wait(timeout):
            error = self._stop()
            raise AssertionError(
                f"the lock holder never acquired the lock: {error.strip()}")
        return self

    def _stop(self) -> str:
        if self._error is not None:
            return self._error
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
            self.process.kill()
            self.process.wait(timeout=5)
        self._error = self.process.stderr.read() or ""
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        return self._error

    def __enter__(self) -> "_LockHolder":
        return self.wait_locked()

    def __exit__(self, *_exc) -> None:
        self._stop()


@pytest.fixture()
def runtime_job(tmp_path, monkeypatch):
    """An isolated output dir with one task that already has a runtime state."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "boundedlock0001"
    core.update_runtime_state(
        job_id, status="running", phase="translating", phase_label="正在翻译",
        event="已处理 1 页", event_visibility="user", event_category="progress")
    return job_id, core.runtime_state_path(job_id)


def test_update_runtime_state_returns_within_the_bound_while_a_process_holds_the_lock(
        runtime_job):
    job_id, state_path = runtime_job
    before = state_path.read_text(encoding="utf-8")
    lock_path = core.job_dir(job_id) / ".runtime-write.lock"
    bound = job_lock.DEFAULT_TIMEOUT_SECONDS

    with _LockHolder(lock_path, HOLD_SECONDS):
        started = time.monotonic()
        timed_out = False
        try:
            core.update_runtime_state(
                job_id, status="completed", progress=True,
                event="this update must not land",
                event_visibility="user", event_category="lifecycle")
        except job_lock.JobLockTimeout:
            timed_out = True
        elapsed = time.monotonic() - started

    # The measured value is the evidence: it must survive into the CI log even
    # when the test passes, so the Windows artifact can be compared with macOS.
    warnings.warn(
        "E-6t measured wait: update_runtime_state gave up after "
        f"{elapsed:.3f} s while another process held .runtime-write.lock "
        f"{HOLD_SECONDS:g} s (declared bound {bound:g} s)",
        stacklevel=1)

    assert timed_out, (
        f"update_runtime_state blocked for {elapsed:.3f} s while another process "
        f"held .runtime-write.lock {HOLD_SECONDS:g} s instead of failing at the "
        f"{bound:g} s bound")
    assert elapsed <= bound + SLACK_SECONDS, (
        f"update_runtime_state waited {elapsed:.3f} s while another process held "
        f".runtime-write.lock; the declared bound is {bound:g} s")
    # It must not "pass" by giving up instantly either: the sibling test proves
    # a 0.3 s writer is waited out; here we only reject an immediate failure.
    assert elapsed >= bound * 0.5, (
        f"the bounded wait gave up after only {elapsed:.3f} s")
    # Same contract as before: a timed-out update writes nothing at all.
    assert state_path.read_text(encoding="utf-8") == before
    assert core.load_runtime_state(job_id)["status"] == "running"


def test_a_short_foreign_write_is_waited_out_and_the_update_lands(runtime_job):
    job_id, _state_path = runtime_job
    lock_path = core.job_dir(job_id) / ".runtime-write.lock"

    with _LockHolder(lock_path, SHORT_HOLD_SECONDS):
        started = time.monotonic()
        current = core.update_runtime_state(
            job_id, status="paused", phase="paused",
            event="短写之后落地", event_visibility="user",
            event_category="progress")
        elapsed = time.monotonic() - started

    assert elapsed >= SHORT_HOLD_SECONDS * 0.5, (
        f"the update did not wait for the short foreign write: {elapsed:.3f} s")
    assert elapsed < job_lock.DEFAULT_TIMEOUT_SECONDS, (
        f"a {SHORT_HOLD_SECONDS:g} s writer must fit inside the bound, "
        f"waited {elapsed:.3f} s")
    assert current["status"] == "paused"
    assert core.load_runtime_state(job_id)["last_event"] == "短写之后落地"


def test_bounded_acquire_honours_a_sub_second_deadline(tmp_path):
    """The primitive itself never inherits ``LK_LOCK``'s whole-second quantum."""
    path = tmp_path / ".runtime-write.lock"
    holder = job_lock.acquire(path)
    try:
        started = time.monotonic()
        with pytest.raises(job_lock.JobLockTimeout):
            job_lock.acquire_bounded(path, timeout=0.25)
        elapsed = time.monotonic() - started
    finally:
        holder.close()

    assert 0.2 <= elapsed < 0.75, (
        f"a 0.25 s bounded acquire took {elapsed:.3f} s")


def test_bounded_primitive_measured_wait_on_this_platform(tmp_path):
    """Platform-level measurement, independent of ``update_runtime_state``.

    Kept separate on purpose: when the call site is temporarily reverted to the
    blocking acquire (rollback verification), this test still measures the
    *bounded* primitive on the same runner, so a single failing artifact carries
    both the old one's wait and the new one's.
    """
    path = tmp_path / ".runtime-write.lock"
    bound = job_lock.DEFAULT_TIMEOUT_SECONDS

    with _LockHolder(path, HOLD_SECONDS):
        started = time.monotonic()
        with pytest.raises(job_lock.JobLockTimeout):
            job_lock.acquire_bounded(path, timeout=bound)
        elapsed = time.monotonic() - started

    warnings.warn(
        f"E-6t bounded primitive measured wait: acquire_bounded gave up after "
        f"{elapsed:.3f} s while another process held the lock {HOLD_SECONDS:g} s "
        f"(declared bound {bound:g} s)", stacklevel=1)
    assert bound - 0.1 <= elapsed <= bound + SLACK_SECONDS, (
        f"a {bound:g} s bounded acquire took {elapsed:.3f} s")
