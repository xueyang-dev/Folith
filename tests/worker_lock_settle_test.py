"""The two paths that take ``.worker.lock`` must agree on the teardown rule.

A save and a direct stage call both need the job's execution lock.  They used to
disagree: the save waited ``WORKER_LOCK_SETTLE_SECONDS`` once the runtime said the
run was terminal, the direct path failed at once -- so while the worker thread was
still winding down, saving worked and a manual review was refused with
"任务正在后台处理".  Windows CI exposed it in ``qt_window_migration_test``.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import job_lock  # noqa: E402
from folith.engine import locks  # noqa: E402


def _hold_lock(data_dir: Path, job_id: str) -> "job_lock._LockStream":
    path = data_dir / job_id / ".worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = job_lock.acquire(path)
    assert stream is not None
    return stream


def test_a_terminal_run_gets_a_settle_window(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setattr(core, "OUTPUT_DIR", data_dir)
        held = _hold_lock(data_dir, "settle-job")
        released = threading.Event()

        def release_later() -> None:
            time.sleep(0.3)
            held.close()
            released.set()

        threading.Thread(target=release_later, daemon=True).start()
        started = time.monotonic()
        lock = locks.acquire_worker_lock_or_settle("settle-job", status="interrupted")
        elapsed = time.monotonic() - started
        try:
            assert lock is not None, "a terminal run must wait out the teardown"
            assert elapsed >= 0.2, elapsed
            assert elapsed < 2.0, elapsed
            assert released.is_set()
        finally:
            if lock is not None:
                lock.close()


def test_an_active_run_is_refused_immediately(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setattr(core, "OUTPUT_DIR", data_dir)
        held = _hold_lock(data_dir, "active-job")
        try:
            started = time.monotonic()
            lock = locks.acquire_worker_lock_or_settle("active-job", status="running")
            elapsed = time.monotonic() - started
            assert lock is None, "an active run owns the lock; do not wait for it"
            assert elapsed < 0.2, elapsed
        finally:
            held.close()


def test_the_settle_window_is_bounded(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setattr(core, "OUTPUT_DIR", data_dir)
        monkeypatch.setattr(core, "WORKER_LOCK_SETTLE_SECONDS", 0.3, raising=False)
        held = _hold_lock(data_dir, "stuck-job")
        try:
            started = time.monotonic()
            lock = locks.acquire_worker_lock_or_settle("stuck-job", status="completed")
            elapsed = time.monotonic() - started
            assert lock is None
            assert 0.25 <= elapsed < 1.0, elapsed
        finally:
            held.close()


def test_the_save_path_uses_the_same_helper(monkeypatch):
    """``core._acquire_save_worker_lock`` must not keep its own copy of the rule."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)
        monkeypatch.setattr(core, "OUTPUT_DIR", data_dir)
        held = _hold_lock(data_dir, "save-job")
        threading.Thread(target=lambda: (time.sleep(0.2), held.close()), daemon=True).start()
        lock = core._acquire_save_worker_lock("save-job", {"status": "completed"})
        try:
            assert lock is not None
        finally:
            if lock is not None:
                lock.close()
