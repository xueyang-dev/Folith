"""The batch-progress runtime status must be merged, not written per phase.

Regression test for the measured defect: a translation batch used to issue
seven durable rewrites of ``runtime_state.json`` (each a full
read-modify-write plus an fsync'd event append), which cost 26.8 seconds of
CPU inside a 40-second run and starved the GUI thread of the GIL.

The contract that must hold:

* many status updates collapse into a small, constant number of durable
  writes;
* every event still reaches ``runtime_events.jsonl`` -- only the *state file*
  is merged, the activity log keeps its history;
* the merged state on disk is the merged state, not the last one;
* a cancel request is never overwritten by a pending status flush;
* a terminal state is never overwritten by a late flush.
"""
from __future__ import annotations

import threading
import time

import core


def _job(tmp_path, job_id="runtime-coalesce-1"):
    core.OUTPUT_DIR = tmp_path
    core.save_job_state(job_id, core.new_job_state("coalesce.docx"))
    return job_id


def test_many_status_updates_collapse_into_few_durable_writes(tmp_path, monkeypatch):
    job_id = _job(tmp_path)
    writes: list[dict] = []
    original = core.update_runtime_state

    def counting(job, **changes):
        writes.append(dict(changes))
        return original(job, **changes)

    monkeypatch.setattr(core, "update_runtime_state", counting)
    core.reset_runtime_status()
    try:
        for index in range(50):
            core._submit_runtime_status(
                job_id, status="running", phase="translation",
                event=f"正在翻译第 {index} 段", event_name="translation_generation",
                event_visibility="user", event_category="progress")
        core.flush_runtime_status(job_id)
    finally:
        core.reset_runtime_status(job_id)

    assert len(writes) <= 3, f"50 status updates must not become 50 writes: {len(writes)}"
    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "running"
    assert runtime["phase"] == "translation"


def test_every_event_still_reaches_the_activity_log(tmp_path):
    job_id = _job(tmp_path, "runtime-coalesce-2")
    core.reset_runtime_status()
    try:
        for index in range(20):
            core._submit_runtime_status(
                job_id, status="running", phase="translation",
                event=f"正在翻译第 {index} 段", event_name="translation_generation",
                event_visibility="user", event_category="progress")
        core.flush_runtime_status(job_id)
    finally:
        core.reset_runtime_status(job_id)

    events = core.read_runtime_events(job_id, visibility="user")
    messages = [str(item.get("message") or "") for item in events]
    assert len(messages) == 20, f"the activity log must keep every event: {len(messages)}"
    assert "正在翻译第 19 段" in messages


def test_a_pending_status_flush_never_overwrites_a_cancel(tmp_path):
    job_id = _job(tmp_path, "runtime-coalesce-3")
    core.reset_runtime_status()
    try:
        core._submit_runtime_status(job_id, status="running", phase="translation",
                                    event="正在翻译", event_name="translation_generation")
        # The user cancels: that write is authoritative and immediate.
        core.update_runtime_state(job_id, cancel_requested=True, status="cancelling",
                                   phase="cancelling", phase_label="正在取消")
        # A late batch status arrives and is merged.
        core._submit_runtime_status(job_id, status="running", phase="translation",
                                    event="还在翻译", event_name="translation_generation")
        core.flush_runtime_status(job_id)
    finally:
        core.reset_runtime_status(job_id)

    runtime = core.load_runtime_state(job_id)
    assert runtime["cancel_requested"] is True
    assert runtime["status"] == "cancelling", runtime["status"]


def test_a_late_flush_never_overwrites_a_terminal_state(tmp_path):
    job_id = _job(tmp_path, "runtime-coalesce-4")
    core.reset_runtime_status()
    try:
        core._submit_runtime_status(job_id, status="running", phase="translation",
                                    event="正在翻译", event_name="translation_generation")
        # The worker reaches a terminal state: the status channel is closed first.
        core.flush_runtime_status(job_id)
        core.reset_runtime_status(job_id)
        core.update_runtime_state(job_id, status="completed", phase="completed",
                                  phase_label="已完成")
        # A straggler arrives after the terminal state was written.
        core._submit_runtime_status(job_id, status="running", phase="translation",
                                    event="迟到的状态", event_name="translation_generation")
        core.flush_runtime_status(job_id)
    finally:
        core.reset_runtime_status(job_id)

    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "completed", runtime["status"]


def test_the_writer_actually_writes_before_its_window_expires(tmp_path):
    """A single update must reach the disk without waiting for the window."""
    job_id = _job(tmp_path, "runtime-coalesce-5")
    core.reset_runtime_status()
    try:
        core._submit_runtime_status(job_id, status="running", phase="translation",
                                    event="刚开始", event_name="translation_generation")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if core.load_runtime_state(job_id).get("phase") == "translation":
                break
            time.sleep(0.02)
        assert core.load_runtime_state(job_id).get("phase") == "translation"
    finally:
        core.reset_runtime_status(job_id)


def test_concurrent_batches_do_not_lose_status(tmp_path):
    """Four concurrent batch threads must not corrupt the merged state."""
    job_id = _job(tmp_path, "runtime-coalesce-6")
    core.reset_runtime_status()
    try:
        def worker(index: int) -> None:
            for step in range(10):
                core._submit_runtime_status(
                    job_id, status="running", phase="translation",
                    event=f"批次 {index} 第 {step} 步",
                    event_name="translation_generation",
                    event_visibility="user", event_category="progress")

        threads = [threading.Thread(target=worker, args=(index,))
                   for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        core.flush_runtime_status(job_id)
    finally:
        core.reset_runtime_status(job_id)

    runtime = core.load_runtime_state(job_id)
    assert runtime["status"] == "running"
    events = core.read_runtime_events(job_id, visibility="user")
    assert len(events) >= 40, f"every concurrent event must survive: {len(events)}"


def test_only_durable_appends_fsync_the_runtime_event_log(tmp_path, monkeypatch):
    job_id = _job(tmp_path, "runtime-fsync-1")
    calls = []
    real_fsync = core.os.fsync

    def counting(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(core.os, "fsync", counting)
    core._append_runtime_event(
        job_id, {"event": "progress", "message": "no flush"},
        event_name="progress", sync=False)
    assert calls == []

    core._append_runtime_event(
        job_id, {"event": "terminal", "message": "durable"},
        event_name="terminal", sync=True)
    assert len(calls) == 1


def test_durable_phase_is_published_immediately_but_progress_is_not(tmp_path, monkeypatch):
    job_id = _job(tmp_path, "runtime-durable-phase-1")
    writes = []
    original = core.update_runtime_state

    def counting(job, **changes):
        writes.append(dict(changes))
        return original(job, **changes)

    monkeypatch.setattr(core, "update_runtime_state", counting)
    core.reset_runtime_status(job_id)
    try:
        core._submit_runtime_status(
            job_id, status="running", phase="state_commit",
            event="正在写入", event_name="state_commit")
        assert writes and writes[-1].get("phase") == "state_commit"

        before = len(writes)
        core._submit_runtime_status(
            job_id, status="running", phase="translation",
            event="正在翻译", event_name="translation_generation")
        assert len(writes) == before
        core.flush_runtime_status(job_id)
        assert len(writes) > before
    finally:
        core.reset_runtime_status(job_id)
