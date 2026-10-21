"""Actual reader contention observed while retesting issue #13 on Windows."""
import errno
import os
from pathlib import Path
import threading
import time

import pytest

from folith.storage import atomic_file


def test_transient_windows_sharing_failure_retries_only_replace(tmp_path, monkeypatch):
    source, target = tmp_path / "state.tmp", tmp_path / "state.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    calls, sleeps = [], []
    def sharing_failure(path, destination):
        calls.append(path.read_bytes())
        if len(calls) < 3:
            error = PermissionError(errno.EACCES, "reader open")
            error.winerror = 5
            raise error
        # Simulate the successful Windows call without calling the
        # ctypes-only ReplaceFileW path, which does not exist on POSIX.
        os.replace(path, destination)
    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(atomic_file, "_replace_once", sharing_failure)
    monkeypatch.setattr(atomic_file.time, "sleep", sleeps.append)
    atomic_file.replace_file(source, target)
    assert calls == [b"new"] * 3
    assert sleeps == [0.01, 0.02]
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("windows,code,error_number,attempts", [
    (True, 5, errno.EACCES, atomic_file._MAX_ATTEMPTS),
    (True, 32, errno.EACCES, atomic_file._MAX_ATTEMPTS),
    (True, 33, errno.EACCES, atomic_file._MAX_ATTEMPTS),
    (False, 5, errno.EACCES, 1),
    (True, 112, errno.ENOSPC, 1),
])
def test_permanent_errors_propagate_and_preserve_old_file(tmp_path, monkeypatch,
                                                        windows, code, error_number, attempts):
    source, target = tmp_path / "state.tmp", tmp_path / "state.json"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    calls = []
    error = OSError(error_number, "injected failure")
    error.winerror = code
    def fail(*args):
        calls.append(1)
        raise error
    monkeypatch.setattr(atomic_file, "_WINDOWS", windows)
    monkeypatch.setattr(atomic_file, "_replace_once", fail)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda _: None)
    with pytest.raises(OSError) as caught:
        atomic_file.replace_file(source, target)
    assert caught.value is error
    assert len(calls) == attempts
    assert target.read_bytes() == b"old"
    assert source.read_bytes() == b"new"


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows sharing semantics")
def test_shared_reader_does_not_block_replace(tmp_path):
    source, target = tmp_path / "state.tmp", tmp_path / "state.json"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    with atomic_file.shared_text_reader(target) as handle:
        assert handle.read() == "old"
        atomic_file.replace_file(source, target)
    assert target.read_bytes() == b"new"
    assert not source.exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows sharing semantics")
def test_real_windows_reader_releases_file_during_replace(tmp_path):
    source, target = tmp_path / "state.tmp", tmp_path / "state.json"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    reader = target.open("rb")
    assert reader.read() == b"old"
    try:
        with pytest.raises(PermissionError):
            source.replace(target)
    except BaseException:
        reader.close()
        raise
    def close_reader():
        time.sleep(0.05)
        reader.close()
    thread = threading.Thread(target=close_reader)
    thread.start()
    try:
        atomic_file.replace_file(source, target)
    finally:
        reader.close()
        thread.join(1)
    assert target.read_bytes() == b"new"
    assert not source.exists()
