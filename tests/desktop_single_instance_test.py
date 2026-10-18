"""D1 application single-instance lock and window raise (roadmap §3.1.4).

Covers the unit semantics (exclusive lock, stale record, release) and a real
two-launch experiment through `gui.run_desktop`: the second launch must not
start a second service, must report the running instance, and must release the
lock when the first one exits.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402
from folith.desktop import single_instance  # noqa: E402


@pytest.fixture()
def tmp_paths(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    monkeypatch.setenv(desktop_paths.DATA_DIR_ENV, str(data))
    monkeypatch.setenv(desktop_paths.LOG_DIR_ENV, str(logs))
    return desktop_paths.resolve_paths()


def _record(paths, **overrides) -> single_instance.InstanceRecord:
    values = {
        "instance_id": "test-instance",
        "launcher_pid": os.getpid(),
        "service_pid": os.getpid(),
        "port": 12345,
        "url": "http://127.0.0.1:12345",
        "app_version": "0.5.0",
        "build_id": "git:test",
        "started_at": time.time(),
    }
    values.update(overrides)
    return single_instance.InstanceRecord(**values)


# --- unit ------------------------------------------------------------------


def test_lock_is_exclusive_and_releasable(tmp_paths):
    first = single_instance.AppInstanceLock(tmp_paths)
    assert first.acquire() is True
    assert first.held is True
    assert tmp_paths.instance_lock.exists()

    second = single_instance.AppInstanceLock(tmp_paths)
    assert second.acquire() is False, "a second instance must not take the app lock"
    assert second.held is False

    first.release()
    assert first.held is False
    third = single_instance.AppInstanceLock(tmp_paths)
    assert third.acquire() is True, "the lock must be reusable after release"
    third.release()


def test_record_round_trip_and_stale_detection(tmp_paths):
    lock = single_instance.AppInstanceLock(tmp_paths)
    assert lock.acquire()
    record = _record(tmp_paths)
    lock.publish(record)
    stored = lock.record()
    assert stored == record
    assert lock.live_record() == record, "our own pid is alive"

    # A record whose launcher died must never be treated as a running instance.
    lock.publish(_record(tmp_paths, launcher_pid=999_999))
    assert lock.record() is not None
    assert lock.live_record() is None
    lock.release()
    assert not tmp_paths.instance_record.exists()


def test_publish_requires_the_lock(tmp_paths):
    lock = single_instance.AppInstanceLock(tmp_paths)
    with pytest.raises(RuntimeError):
        lock.publish(_record(tmp_paths))


def test_activation_reports_what_it_attempted(tmp_paths):
    assert single_instance.activate_existing_window(None) == "no-record"
    action = single_instance.activate_existing_window(_record(tmp_paths))
    assert isinstance(action, str) and action
    # A dead pid must not be reported as activated on macOS.
    if sys.platform == "darwin":
        dead = single_instance.activate_existing_window(_record(tmp_paths, launcher_pid=999_999))
        assert dead in {"macos-activation-unavailable", "macos-open-bundle"}


def test_state_dir_is_outside_user_data(tmp_paths):
    assert tmp_paths.state_dir == tmp_paths.data_dir.parent
    assert tmp_paths.instance_lock.parent == tmp_paths.state_dir
    assert tmp_paths.instance_record.parent == tmp_paths.state_dir


def test_wait_for_previous_service_exit(tmp_paths):
    import subprocess

    # A live process: the wait must time out without killing it.
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert single_instance.wait_for_service_exit(sleeper.pid, timeout=0.6) is False
        assert desktop_process.pid_alive(sleeper.pid), "must never kill a previous service"
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=10)
    assert single_instance.wait_for_service_exit(sleeper.pid, timeout=5) is True
    assert single_instance.wait_for_service_exit(0, timeout=0.1) is True


# --- real two-launch experiment -------------------------------------------


def _desktop_args(ready_timeout: float = 90.0) -> argparse.Namespace:
    return argparse.Namespace(
        desktop=True, no_browser=True, browser=False, legacy=False, lan=False,
        port=0, ready_timeout=ready_timeout, automation_script=None,
        automation_result=None,
    )


