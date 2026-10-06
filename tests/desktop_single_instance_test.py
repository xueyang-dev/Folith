"""D1 application single-instance lock and window raise (roadmap §3.1.4).

Covers the unit semantics (exclusive lock, stale record, release) and a real
two-launch experiment through `gui.run_desktop`: the second launch must not
start a second service, must report the running instance, and must release the
lock when the first one exits.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
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


def test_startup_refuses_while_a_previous_service_is_alive(tmp_paths, capsys, monkeypatch):
    """A leftover service must block startup instead of racing on the data dir."""
    import gui

    import subprocess

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        # Simulate a crashed launcher: the OS lock is free, but its record still
        # names a service that has not finished exiting.
        stale = single_instance.AppInstanceLock(tmp_paths)
        assert stale.acquire()
        stale.release()
        tmp_paths.instance_record.write_text(
            _record(tmp_paths, launcher_pid=999_999, service_pid=sleeper.pid).to_json() + "\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(single_instance, "wait_for_service_exit",
                            lambda pid, timeout=25.0: False)
        started = time.monotonic()
        code = gui.run_desktop(_desktop_args())
        elapsed = time.monotonic() - started
        captured = capsys.readouterr().out
        assert elapsed < 30, f"refused startup must not block (took {elapsed:.1f}s)"
        assert code == 11, captured
        assert "仍未退出" in captured
        assert desktop_process.pid_alive(sleeper.pid)
        # The lock must not be left held by a refused startup.
        fresh = single_instance.AppInstanceLock(tmp_paths)
        assert fresh.acquire() is True
        fresh.release()
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=10)


# --- real two-launch experiment -------------------------------------------


def _desktop_args(ready_timeout: float = 90.0) -> argparse.Namespace:
    return argparse.Namespace(
        desktop=True, no_browser=True, browser=False, legacy=False, lan=False,
        port=0, ready_timeout=ready_timeout, automation_script=None,
        automation_result=None,
    )


def test_second_launch_raises_instead_of_starting_another_service(tmp_paths, capsys):
    import gui

    outcome: dict = {}

    def first_launch() -> None:
        outcome["code"] = gui.run_desktop(_desktop_args())

    thread = threading.Thread(target=first_launch, daemon=True)
    thread.start()

    record = None
    deadline = time.monotonic() + 120
    lock = single_instance.AppInstanceLock(tmp_paths)
    while time.monotonic() < deadline and record is None:
        record = lock.record()
        time.sleep(0.3)
    assert record is not None, "the first instance never published its record"
    assert desktop_process.pid_alive(record.service_pid)

    # Second launch in this same process: must refuse, must not spawn a service.
    second_code = gui.run_desktop(_desktop_args())
    captured = capsys.readouterr().out
    assert second_code == 10, captured
    assert "已经在运行" in captured
    assert record.instance_id in captured
    assert "窗口唤起：" in captured

    # The first instance is untouched and still the only service.
    assert desktop_process.pid_alive(record.service_pid)
    assert lock.acquire() is False, "the first instance must still hold the lock"

    # Shut the first instance down; the lock must be released with it.
    desktop_process.terminate_process(record.service_pid, timeout=10)
    thread.join(timeout=120)
    assert outcome.get("code") in (0, 9), outcome
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not tmp_paths.instance_record.exists():
            break
        time.sleep(0.3)
    assert not tmp_paths.instance_record.exists(), "the record must be removed on exit"
    fresh = single_instance.AppInstanceLock(tmp_paths)
    assert fresh.acquire() is True
    fresh.release()
