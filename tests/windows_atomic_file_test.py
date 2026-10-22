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


def test_draft_save_uses_the_retrying_replace(tmp_path, monkeypatch):
    import core
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_translation_drafts("draft-job", {"s1": {"text": "old"}})
    writes = []

    def replace_after_reader_releases(source, destination):
        writes.append(source.read_bytes())
        if len(writes) < 3:
            error = PermissionError(errno.EACCES, "draft reader open")
            error.winerror = 32
            raise error
        os.replace(source, destination)

    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(atomic_file, "_replace_once", replace_after_reader_releases)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda _: None)
    core.save_translation_drafts("draft-job", {"s1": {"text": "new"}})
    assert len(writes) == 3 and writes[0] == writes[1] == writes[2]
    assert core.load_translation_drafts("draft-job")["s1"]["text"] == "new"
    assert not list((tmp_path / "draft-job").glob("*.tmp"))


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


def _fake_reader(outcomes):
    """A ``shared_text_reader`` stand-in that fails or yields per call."""
    from contextlib import contextmanager
    import io

    @contextmanager
    def reader(path, encoding="utf-8"):
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        yield io.StringIO(outcome)
    return reader


def _winerror(code, cls=PermissionError):
    error = cls(errno.EACCES, "injected")
    error.winerror = code
    return error


@pytest.fixture
def windows_reader(monkeypatch):
    sleeps = []
    monkeypatch.setattr(atomic_file, "_WINDOWS", True)
    monkeypatch.setattr(atomic_file, "_read_seen", set())
    monkeypatch.setattr(atomic_file.time, "sleep", sleeps.append)
    return sleeps


def test_shared_read_retries_sharing_violations(tmp_path, monkeypatch, windows_reader):
    outcomes = [_winerror(32), _winerror(5), '{"status": "running"}']
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader(outcomes))
    assert atomic_file.read_shared_text(tmp_path / "s.json") == '{"status": "running"}'
    assert windows_reader == [0.005, 0.01]


def test_shared_read_never_written_file_fails_without_waiting(tmp_path, monkeypatch,
                                                              windows_reader):
    outcomes = [FileNotFoundError(errno.ENOENT, "missing")]
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader(outcomes))
    with pytest.raises(FileNotFoundError):
        atomic_file.read_shared_text(tmp_path / "s.json")
    assert windows_reader == []


def test_shared_read_retries_missing_file_mid_replace(tmp_path, monkeypatch, windows_reader):
    path = tmp_path / "s.json"
    outcomes = ["old", FileNotFoundError(errno.ENOENT, "swapping"), "new"]
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader(outcomes))
    assert atomic_file.read_shared_text(path) == "old"
    assert atomic_file.read_shared_text(path) == "new"
    assert windows_reader == [0.005]


def test_shared_read_deleted_file_stops_waiting(tmp_path, monkeypatch, windows_reader):
    path = tmp_path / "s.json"
    clock = iter(range(0, 1000))
    monkeypatch.setattr(atomic_file.time, "monotonic", lambda: next(clock) * 0.3)
    gone = FileNotFoundError(errno.ENOENT, "deleted")
    outcomes = ["old"] + [gone] * 10
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader(outcomes))
    atomic_file.read_shared_text(path)
    with pytest.raises(FileNotFoundError):
        atomic_file.read_shared_text(path)
    waited = len(windows_reader)
    assert waited >= 1
    # Once given up on, the next read of the deleted file fails at once.
    with pytest.raises(FileNotFoundError):
        atomic_file.read_shared_text(path)
    assert len(windows_reader) == waited


def test_shared_read_other_errors_propagate(tmp_path, monkeypatch, windows_reader):
    error = _winerror(112, OSError)
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader([error]))
    with pytest.raises(OSError) as caught:
        atomic_file.read_shared_text(tmp_path / "s.json")
    assert caught.value is error
    assert windows_reader == []


def test_snapshot_load_retries_instead_of_returning_empty_state(tmp_path, monkeypatch,
                                                              windows_reader):
    import core
    import json
    from folith.storage import job_repository as repo

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    state = core.new_job_state("retry.docx")
    state["pairs"] = [{"source": "source", "target": "target"}]
    payload = json.dumps(state)
    path = repo.get_job_state_path("sharing-read")
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(atomic_file, "shared_text_reader",
                        _fake_reader([_winerror(32), payload]))
    loaded = core.load_job_state("sharing-read")
    assert loaded["pairs"] == state["pairs"]
    assert windows_reader == [0.005]
    repo.invalidate_cache()


@pytest.mark.parametrize("code,error_class", [(32, PermissionError), (2, FileNotFoundError)])
def test_snapshot_signature_retries_replacement_gap(tmp_path, monkeypatch, windows_reader,
                                                  code, error_class):
    import core
    import json
    from folith.storage import job_repository as repo

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    state = core.new_job_state("signature.docx")
    state["pairs"] = [{"source": "source", "target": "target"}]
    path = repo.get_job_state_path("signature-read")
    path.parent.mkdir(parents=True)
    payload = json.dumps(state)
    path.write_text(payload, encoding="utf-8")
    # Successful stat establishes that the task exists before the swap.
    assert repo.state_signature("signature-read") is not None
    original_stat = Path.stat
    failures = [_winerror(code, error_class)]

    def racing_stat(self, *a, **kw):
        if self == path and failures:
            raise failures.pop()
        return original_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", racing_stat)
    monkeypatch.setattr(atomic_file, "shared_text_reader", _fake_reader([payload]))
    assert core.load_job_state("signature-read")["pairs"] == state["pairs"]
    assert windows_reader == [0.005]
    repo.invalidate_cache()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows ReplaceFileW semantics")
def test_real_windows_reader_never_sees_a_gap_during_replace(tmp_path):
    target = tmp_path / "runtime_state.json"
    target.write_text("0", encoding="utf-8")
    stop = threading.Event()

    def writer():
        index = 0
        while not stop.is_set():
            source = tmp_path / f"runtime_state.json.{index}.tmp"
            source.write_text(str(index), encoding="utf-8")
            atomic_file.replace_file(source, target)
            index += 1

    thread = threading.Thread(target=writer)
    thread.start()
    failures, reads = [], 0
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline:
            try:
                int(atomic_file.read_shared_text(target))
            except Exception as error:  # noqa: BLE001 - counted below
                failures.append(repr(error))
            reads += 1
    finally:
        stop.set()
        thread.join(5)
    assert reads > 100
    assert failures == []


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows snapshot replacement semantics")
def test_real_windows_snapshot_load_during_replacement(tmp_path, monkeypatch):
    import core
    from folith.storage import job_repository as repo

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    state = core.new_job_state("concurrent.docx")
    state["pairs"] = [{"source": "source", "target": "target 0"}]
    repo.save_job_state_atomic("snapshot-read", state)
    stop = threading.Event()
    failures = []

    def writer():
        try:
            index = 0
            while not stop.is_set():
                state["pairs"][0]["target"] = f"target {index}"
                repo.save_job_state_atomic("snapshot-read", state)
                index += 1
        except Exception as error:
            failures.append(repr(error))

    thread = threading.Thread(target=writer)
    thread.start()
    reads = 0
    deadline = time.monotonic() + 2
    try:
        while time.monotonic() < deadline:
            loaded = repo.load_job_state_cached("snapshot-read", force_reload=True)
            assert loaded and len(loaded["pairs"]) == 1
            assert loaded["pairs"][0]["source"] == "source"
            assert loaded["pairs"][0]["target"].startswith("target ")
            reads += 1
    finally:
        stop.set()
        thread.join(10)
        repo.invalidate_cache()
    assert not thread.is_alive()
    assert reads > 10
    assert failures == []
