"""Contract tests for the batch-level state feed (issue #324, A1 P0).

The feed is the read-side projection of ``state_journal.jsonl``: it must turn
one durable journal record into one typed delta, stay quiet when nothing was
appended, and fall back to a whole-state resync whenever the journal can no
longer be trusted.  Nothing here writes product state.

These tests are pure logic (no Qt): a journal file on disk and an injected
environment are enough.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from folith import state_feed
from folith.storage import state_journal as journal_module


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
def _pair(uid: str, source: str, target: str = "", **fields) -> dict:
    pair = {"segment_uid": uid, "source": source, "target": target,
            "reviewed": False, "review_status": "not_reviewed",
            "target_provenance": "generated", "from_tm": False,
            "human_edited": False, "excluded": False}
    pair.update(fields)
    return pair


def _record(seq: int, offset: int, pairs: list[dict], *, batch: int = 0,
            set_values: dict | None = None, **extra) -> dict:
    record = {
        "schema": journal_module.JOURNAL_SCHEMA_VERSION,
        "mutation": journal_module.MUTATION_TRANSLATION_BATCH,
        "mutation_id": uuid.uuid4().hex,
        "ts": "2026-01-01T00:00:00+00:00",
        "batch": batch,
        "offset": offset,
        "segment_ids": [pair.get("segment_uid") or f"seg-{offset + i}"
                        for i, pair in enumerate(pairs)],
        "pairs": pairs,
        "seq": seq,
    }
    if set_values:
        record["set"] = set_values
    record.update(extra)
    return record


class _Job:
    def __init__(self, tmp_path: Path, job_id: str = "job1") -> None:
        self.dir = tmp_path / job_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.journal = self.dir / journal_module.JOURNAL_FILE
        self.state = self.dir / "state.json"
        self.pairs: list[dict] = []
        self.write_state({})

    def write_state(self, state: dict) -> None:
        payload = {"job_id": "job1", "pairs": list(self.pairs), "journal_cursor": 0}
        payload.update(state)
        self.state.write_text(json.dumps(payload), encoding="utf-8")

    def append(self, record: dict, *, newline: bool = True) -> None:
        with self.journal.open("ab") as stream:
            stream.write(json.dumps(record).encode("utf-8"))
            if newline:
                stream.write(b"\n")

    def feed(self, **kwargs) -> "state_feed.StateFeed":
        return state_feed.StateFeed(
            journal_path=self.journal, state_path=self.state, job_id="job1",
            environ={}, **kwargs)

    def previous_pair(self, index: int):
        return self.pairs[index] if 0 <= index < len(self.pairs) else None


# ---------------------------------------------------------------------------
# delta application
# ---------------------------------------------------------------------------
def test_append_then_poll_yields_one_typed_delta(tmp_path):
    job = _Job(tmp_path)
    first = _record(1, 0, [_pair("u0", "Hello", "你好")], batch=0,
                    set_values={"review_stats": {"reviewed_segments": 0}})
    second = _record(2, 1, [_pair("u1", "World", "世界", reviewed=True)], batch=1,
                     set_values={"review_stats": {"reviewed_segments": 1},
                                 "translation_truth": {"version": 2}})
    job.append(first)
    job.append(second)

    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll(previous_pair=job.previous_pair)

    assert isinstance(result, state_feed.DeltaBatch)
    assert result.from_seq == 1 and result.seq == 2
    assert result.batch == 1 and result.batches == (0, 1)
    assert [segment.index for segment in result.segments] == [0, 1]
    assert [segment.segment_uid for segment in result.segments] == ["u0", "u1"]
    assert result.segments[1].pair["target"] == "世界"
    assert result.counters["review_stats"] == {"reviewed_segments": 1}
    assert result.counters["translation_truth"] == {"version": 2}
    assert "rows" in result.domains and "counts" in result.domains
    assert "stage" in result.domains
    assert feed.cursor.seq == 2

    # Nothing new: the same poll is silent.
    assert isinstance(feed.poll(previous_pair=job.previous_pair), state_feed.Idle)


def test_endless_replay_is_idempotent(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")]))
    job.append(_record(2, 1, [_pair("u1", "World", "世界")]))

    batches = []
    for _ in range(2):  # a fresh feed replays from the same snapshot cursor
        feed = job.feed()
        feed.start(cursor_seq=0)
        result = feed.poll(previous_pair=job.previous_pair)
        batches.append({segment.index: dict(segment.pair) for segment in result.segments})
    # Absolute values: replaying the same suffix reproduces the same text.
    assert batches[0] == batches[1]
    assert batches[0][0]["target"] == "你好"
    assert batches[0][1]["target"] == "世界"


def test_delta_carries_only_the_segment_fields_that_changed(tmp_path):
    job = _Job(tmp_path)
    old = _pair("u0", "Hello", "你好")
    job.pairs.append(old)
    new = _pair("u0", "Hello", "您好", reviewed=True)
    job.append(_record(1, 0, [new]))
    feed = job.feed()
    feed.start(cursor_seq=0)

    result = feed.poll(previous_pair=job.previous_pair)
    changed = set(result.segments[0].fields)
    assert "target" in changed and "reviewed" in changed
    assert "source" not in changed
    assert result.segments[0].pair["target"] == "您好"


def test_segment_uid_may_be_empty_until_human_edit(tmp_path):
    job = _Job(tmp_path)
    pair = _pair("", "Hello", "你好")
    job.append(_record(1, 0, [pair]))
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert result.segments[0].segment_uid == ""


# ---------------------------------------------------------------------------
# gap / rotation / truncation / corruption
# ---------------------------------------------------------------------------
def test_sequence_gap_requires_a_resync(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(2, 0, [_pair("u0", "Hello", "你好")]))  # seq 1 is missing
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.ResyncRequired)
    assert result.reason == state_feed.REASON_SEQ_GAP
    assert not result.permanent


def test_journal_rotation_continues_by_sequence_not_by_offset(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")]))
    feed = job.feed()
    feed.start(cursor_seq=0)
    first = feed.poll()
    assert isinstance(first, state_feed.DeltaBatch) and first.seq == 1

    # Compaction truncates the journal; sequence numbers continue from the
    # snapshot cursor, so the next record is seq 2 even though the file shrank.
    before_size = job.journal.stat().st_size
    job.journal.write_bytes(b"")
    job.append(_record(2, 1, [_pair("u1", "World", "世界")]))
    assert job.journal.stat().st_size == before_size, "the case under test needs equal length"
    second = feed.poll()
    assert isinstance(second, state_feed.DeltaBatch), type(second)
    assert second.from_seq == 2 and second.seq == 2


def test_truncated_tail_waits_and_never_forces_a_rebuild(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")]))
    job.append(_record(2, 1, [_pair("u1", "World", "世界")]), newline=False)
    raw = job.journal.read_bytes()
    job.journal.write_bytes(raw[:len(raw) - 20])  # tear the last record

    feed = job.feed()
    feed.start(cursor_seq=0)
    first = feed.poll()
    assert isinstance(first, state_feed.DeltaBatch)
    assert [segment.index for segment in first.segments] == [0]
    assert first.seq == 1

    # The writer finishes the record; the same byte offset now yields it.
    with job.journal.open("ab") as stream:
        stream.write(b'{"seq":2}')  # still not a complete record
    assert isinstance(feed.poll(), state_feed.Idle)

    with job.journal.open("ab") as stream:
        stream.write(b"\n")
    second = feed.poll()
    # The completed line is not a valid batch record: the feed must not spin on
    # it forever and must not silently ignore it.
    assert isinstance(second, state_feed.ResyncRequired)
    assert second.reason == state_feed.REASON_CORRUPT_RECORD


def test_a_complete_but_unparseable_record_requires_a_resync(tmp_path):
    job = _Job(tmp_path)
    job.journal.write_bytes(b'{"seq":1,"schema":2,"mutation":"translation_batch_committed"}\n')
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.ResyncRequired)
    assert result.reason == state_feed.REASON_CORRUPT_RECORD


def test_unknown_schema_or_mutation_is_a_permanent_fallback(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")], schema=99))
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.ResyncRequired)
    assert result.reason == state_feed.REASON_UNKNOWN_SCHEMA
    assert result.permanent

    job2 = _Job(tmp_path / "other")
    job2.append(_record(1, 0, [], mutation="something_else"))
    feed2 = job2.feed()
    feed2.start(cursor_seq=0)
    result2 = feed2.poll()
    assert isinstance(result2, state_feed.ResyncRequired)
    assert result2.reason == state_feed.REASON_UNKNOWN_MUTATION
    assert result2.permanent


def test_record_above_the_byte_budget_requires_a_resync(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "x" * 4096, "y" * 4096)]))
    feed = job.feed(max_record_bytes=1024)
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.ResyncRequired)
    assert result.reason == state_feed.REASON_RECORD_TOO_LARGE


def test_backlog_above_the_limit_requires_a_resync(tmp_path):
    job = _Job(tmp_path)
    for seq in range(1, 6):
        job.append(_record(seq, seq - 1, [_pair(f"u{seq}", "Hello", "你好")]))
    feed = job.feed(max_records_per_poll=2)
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.ResyncRequired)
    assert result.reason == state_feed.REASON_BACKLOG


# ---------------------------------------------------------------------------
# snapshot observation
# ---------------------------------------------------------------------------
def test_snapshot_change_is_reported_once_and_carries_worker_state(tmp_path):
    job = _Job(tmp_path)
    feed = job.feed()
    feed.start(cursor_seq=0)
    assert isinstance(feed.poll(), state_feed.Idle)

    job.write_state({"snapshot_revision": 3})
    changed = feed.poll(worker_active=True)
    assert isinstance(changed, state_feed.SnapshotChanged)
    assert changed.worker_active is True
    assert isinstance(feed.poll(worker_active=True), state_feed.Idle)


def test_snapshot_change_without_a_live_worker_is_an_external_write(tmp_path):
    job = _Job(tmp_path)
    feed = job.feed()
    feed.start(cursor_seq=0)
    job.write_state({"snapshot_revision": 2})
    changed = feed.poll(worker_active=False)
    assert isinstance(changed, state_feed.SnapshotChanged)
    assert changed.worker_active is False


# ---------------------------------------------------------------------------
# rollback switch
# ---------------------------------------------------------------------------
def test_delta_env_switch_disables_the_feed(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")]))
    feed = state_feed.StateFeed(journal_path=job.journal, state_path=job.state,
                                job_id="job1",
                                environ={state_feed.DELTA_ENV: "0"})
    feed.start(cursor_seq=0)
    assert feed.mode == "legacy"
    result = feed.poll()
    assert isinstance(result, state_feed.LegacyMode)
    assert state_feed.delta_enabled({state_feed.DELTA_ENV: "1"}) is True
    assert state_feed.delta_enabled({state_feed.DELTA_ENV: "off"}) is False
    assert state_feed.delta_enabled({}) is True


# ---------------------------------------------------------------------------
# cross-process payload + reader equivalence
# ---------------------------------------------------------------------------
def test_delta_payload_is_plain_json_and_round_trips(tmp_path):
    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")], batch=0,
                       set_values={"delivery_status": "pending"}))
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    payload = result.as_payload()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["schema"] == state_feed.DELTA_SCHEMA_VERSION
    assert payload["segments"][0]["pair"]["target"] == "你好"
    assert payload["delivery"] == {"delivery_status": "pending"}


def test_feed_reads_the_same_suffix_as_the_journal_helper(tmp_path):
    job = _Job(tmp_path)
    for seq in range(1, 4):
        job.append(_record(seq, seq - 1, [_pair(f"u{seq}", "s", f"t{seq}")]))
    feed = job.feed()
    feed.start(cursor_seq=0)
    result = feed.poll()
    assert isinstance(result, state_feed.DeltaBatch)
    assert result.seq == 3

    records, next_offset = journal_module.read_mutations_from(
        "job1", 0, 0, output_dir=tmp_path)
    assert [record["seq"] for record in records] == [1, 2, 3]
    assert next_offset == job.journal.stat().st_size
    assert feed.cursor.offset == next_offset

def test_in_place_rewrite_with_identical_size_and_time_is_still_detected(tmp_path):
    """Windows-portable rotation detection.

    Windows stamps the last-write time from a coarse system clock (~15.6 ms), so
    a truncate-and-refill can leave exactly the same ``(mtime_ns, size)`` as
    before; ``st_ino`` is also 0/unstable on some Windows filesystems.  The feed
    therefore compares the *content head* of the journal -- an append-only
    journal never changes it, any replacement does.  This test reproduces the
    Windows situation deterministically on every platform by restoring the old
    timestamp after the rewrite.
    """
    import os

    job = _Job(tmp_path)
    job.append(_record(1, 0, [_pair("u0", "Hello", "你好")]))
    feed = job.feed()
    feed.start(cursor_seq=0)
    assert isinstance(feed.poll(), state_feed.DeltaBatch)
    before = job.journal.stat()

    job.journal.write_bytes(b"")
    job.append(_record(2, 1, [_pair("u1", "World", "世界")]))
    after = job.journal.stat()
    assert after.st_size == before.st_size
    # Inode identity is only an extra hint, and is 0/unstable on some Windows
    # filesystems -- never require it in a cross-platform test.
    if before.st_ino and after.st_ino:
        assert after.st_ino == before.st_ino
    os.utime(job.journal, ns=(before.st_atime_ns, before.st_mtime_ns))
    restored = job.journal.stat()
    assert restored.st_mtime_ns == before.st_mtime_ns

    second = feed.poll()
    assert isinstance(second, state_feed.DeltaBatch), type(second)
    assert [segment.index for segment in second.segments] == [1]
    assert second.seq == 2
