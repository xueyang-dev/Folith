"""`events.jsonl` append contracts.

Phase 6 attribution found that ``checkpoint.append_event`` called
``Path.mkdir(parents=True, exist_ok=True)`` on *every* event -- several per
batch, so tens of thousands of times per long document -- even though the
directory is created once and never moves.  ``state_journal._ensure_dir``
already cached that guard; this file pins the same guarantee for the audit
event log, plus the recovery behaviour the cache must not lose.

The behaviour that must not change:

* one JSON object per line, ``sort_keys=True`` / ``ensure_ascii=False`` as
  before, appended in call order;
* a directory that disappears underneath the writer is recreated and the
  append retried -- not silently dropped;
* the cached guard is invalidated by ``reset_dir_cache()``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from folith import checkpoint


@pytest.fixture(autouse=True)
def _clean_dir_cache():
    checkpoint.reset_dir_cache()
    yield
    checkpoint.reset_dir_cache()


def _read_events(root: Path):
    return [json.loads(line) for line in
            checkpoint.events_path(root).read_text(encoding="utf-8").splitlines()]


def test_events_append_one_json_object_per_line_in_order(tmp_path: Path):
    root = tmp_path / "job1"
    checkpoint.append_event(root, {"batch": 0, "phase": "generation_started"})
    checkpoint.append_event(root, {"batch": 0, "phase": "generation_done"})
    checkpoint.append_event(root, {"batch": 1, "phase": "generation_started"})

    events = _read_events(root)
    assert events == [
        {"batch": 0, "phase": "generation_started"},
        {"batch": 0, "phase": "generation_done"},
        {"batch": 1, "phase": "generation_started"},
    ]


def test_event_encoding_is_stable(tmp_path: Path):
    """Non-ASCII stays literal and keys stay sorted (the persisted contract)."""
    root = tmp_path / "job1"
    checkpoint.append_event(root, {"原因": "已完成", "batch": 2, "phase": "x"})
    raw = checkpoint.events_path(root).read_text(encoding="utf-8")
    assert "已完成" in raw                      # ensure_ascii=False
    assert raw.index('"batch"') < raw.index('"phase"') < raw.index('"原因"')


def test_directory_is_created_once_not_per_event(tmp_path: Path, monkeypatch):
    """The redundant per-event directory walk is gone.

    Counts real ``Path.mkdir`` invocations while appending many events to the
    same job directory.  The guard is allowed to run once; re-running it per
    event is exactly the repeated work this test exists to prevent.
    """
    root = tmp_path / "job1"
    calls = {"n": 0}
    real_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        calls["n"] += 1
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)

    for index in range(25):
        checkpoint.append_event(root, {"batch": index, "phase": "generation"})

    assert len(_read_events(root)) == 25
    assert calls["n"] == 1, f"mkdir called {calls['n']} times for 25 appends"


def test_second_job_directory_is_created_once_each(tmp_path: Path, monkeypatch):
    """The cache is per parent path, not a single global 'done' flag."""
    calls = {"n": 0}
    real_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        calls["n"] += 1
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)

    for name in ("jobA", "jobB"):
        root = tmp_path / name
        for index in range(4):
            checkpoint.append_event(root, {"batch": index})
        assert len(_read_events(root)) == 4

    assert calls["n"] == 2


def test_vanished_directory_is_recreated_and_append_retried(tmp_path: Path):
    """Caching the guard must not turn a transient disappearance into data loss."""
    root = tmp_path / "job1"
    checkpoint.append_event(root, {"batch": 0})
    assert len(_read_events(root)) == 1

    # Simulate an external cleanup between events while the guard is still warm.
    checkpoint.events_path(root).unlink()
    root.rmdir()

    checkpoint.append_event(root, {"batch": 1})

    # The retry recreated the directory and wrote the event; nothing was lost.
    assert _read_events(root) == [{"batch": 1}]
    assert checkpoint.events_path(root).parent.is_dir()


def test_reset_dir_cache_rearms_the_guard(tmp_path: Path, monkeypatch):
    root = tmp_path / "job1"
    checkpoint.append_event(root, {"batch": 0})

    calls = {"n": 0}
    real_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        calls["n"] += 1
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)
    checkpoint.append_event(root, {"batch": 1})
    assert calls["n"] == 0, "warm guard must not re-run mkdir"

    checkpoint.reset_dir_cache()
    checkpoint.append_event(root, {"batch": 2})
    assert calls["n"] == 1
