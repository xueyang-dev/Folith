"""OS-owned locks for worker lifetime and runtime read/merge/write updates."""
from contextlib import contextmanager
import os
import time

from .workflow_errors import raise_if_fatal

#: How long a *bounded* acquisition waits for another holder before failing.
#:
#: Every critical section guarded by this module is a small JSON read → merge →
#: atomic replace; an uncontended ``update_runtime_state`` measured 0.7 ms
#: median / 1.0 ms max on macOS, so two seconds is ~2000x headroom for a slow
#: disk or an antivirus scan.  It is also:
#:
#: * five times below the ten whole seconds ``msvcrt.locking(LK_LOCK)`` used to
#:   burn (M-1: 4.063 s = 4 whole seconds + 63 ms), and
#: * below the three-second window the deterministic contention test holds the
#:   lock for, so the bound is actually observable, not a value nobody reaches.
DEFAULT_TIMEOUT_SECONDS = 2.0

#: How often a bounded acquisition retries.  The same value ``store_lock`` uses,
#: so both bounded waits behave identically (20 ms is far below the 1 s quantum
#: of the Windows blocking primitive and far above any plausible write time).
DEFAULT_POLL_SECONDS = 0.02


class JobLockTimeout(RuntimeError):
    """A bounded acquisition gave up before the other holder released the lock.

    Callers must surface this as a retriable conflict — never as success, and
    never by silently dropping the update they were about to write.
    """


def acquire(path, *, blocking=True):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open('a+b')
    except OSError as error:
        raise_if_fatal(error, path.parent.name)
        raise
    try:
        if os.name == 'nt':
            import msvcrt
            # Windows can lock bytes beyond EOF. Initialising the file first
            # races with another opener that has already locked byte zero:
            # its mandatory lock would reject our delayed buffered write.
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except (BlockingIOError, PermissionError):
        stream.close()
        if not blocking:
            return None
        raise
    except BaseException:
        stream.close()
        raise
    return stream


def acquire_bounded(path, *, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Acquire ``path`` with a non-blocking poll and an explicit deadline.

    Same shape as ``store_lock._acquire_or_timeout``: the OS primitive is only
    ever asked *non-blocking*, so the caller can never be parked for the whole
    second retry loop of ``msvcrt.locking(LK_LOCK)``.  Raises
    :class:`JobLockTimeout` when the deadline expires; never returns ``None``.
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        stream = acquire(path, blocking=False)
        if stream is not None:
            return stream
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise JobLockTimeout(
                f"另一个 Folith 进程持有 {path.name} 超过 {timeout:g} 秒；"
                "本次更新未写入，请稍后重试。")
        time.sleep(min(DEFAULT_POLL_SECONDS, remaining))


@contextmanager
def exclusive(path, *, timeout=None):
    """Hold the lock at ``path`` for the duration of the ``with`` block.

    ``timeout=None`` keeps the historical blocking acquire.  Passing a number
    bounds the wait and turns exhaustion into :class:`JobLockTimeout`; that is
    what a caller whose wait someone else can feel (``update_runtime_state``)
    must use.
    """
    stream = acquire(path) if timeout is None else acquire_bounded(
        path, timeout=timeout)
    try:
        yield
    finally:
        stream.close()
