"""State-journal contracts (Phase B).

``state.json`` stays the single authoritative complete snapshot.  The journal is
a *durability record* for the translation hot path: it lets a crash be repaired
by replaying committed batches instead of re-translating them, which is what
allows the snapshot cadence to stop scaling with the document.

These tests pin the guarantees that trade rests on, and the ones it must not
break:

* the record is a fixed, named, strong-typed shape -- not a diff;
* replay is a pure function of ``(snapshot, journal)`` and applies each record
  at most once, so repeating it is a no-op;
* replay never calls a model and never writes the snapshot;
* a truncated tail line is dropped; a corrupt line stops the replay rather than
  skipping a mutation out of order;
* all six crash classes from the design leave a complete, non-duplicated truth
  path;
* a human edit is never overwritten by an older generated record;
* an undescribable batch delta costs performance, never durability;
* the journal is retired only after a snapshot that provably contains it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path


import pytest

import core
from folith import persistence_profiler
from folith.storage import job_repository as repo
from folith.storage import state_journal as journal

TARGET_LANG = "简体中文"

#: Wall-clock flushing would make the write counts non-deterministic; the tests
#: that depend on "only the loop-exit snapshot happens" disable it explicitly.
NO_TIME_TRIGGER = "3600"

TRUTH_FIELDS = (
    "source", "initial_target", "target", "accepted_target",
    "reviewed", "review_status", "target_provenance",
)


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    """No test in this module may reach a provider.

    Tests that need to count calls re-patch ``core.call_llm`` on top of this;
    ``monkeypatch`` unwinds in reverse, so their counter wins for their duration
    and this one is restored afterwards.
    """
    monkeypatch.setattr(core, "call_llm", _fake_llm)


@pytest.fixture
def temp_job_env(tmp_path: Path, monkeypatch):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    repo.reset_cache_stats()
    journal.reset_dir_cache()
    persistence_profiler.disable()
    persistence_profiler.reset()
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    monkeypatch.delenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", raising=False)
    monkeypatch.delenv("FOLITH_STATE_JOURNAL", raising=False)
    monkeypatch.delenv("FOLIOTHREAD_STATE_JOURNAL", raising=False)
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()
        journal.reset_dir_cache()
        persistence_profiler.disable()
        persistence_profiler.reset()


def _fake_llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
    if "独立的翻译审校专家" in (system_prompt or ""):
        return json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    return json.dumps([f"第{line.split('.', 1)[0].strip()}段译文。" for line in lines],
                      ensure_ascii=False)


def _paragraphs(count: int):
    return [f"Sentence {i} records the northern archive." for i in range(count)]


def _truth_digest(state: dict) -> str:
    import hashlib
    payload = json.dumps(
        [{key: pair.get(key) for key in TRUTH_FIELDS}
         for pair in state.get("pairs") or []],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _seed(job_id: str, paragraphs):
    state = core.new_job_state("doc.txt")
    state["paras"] = list(paragraphs)
    core.save_job_state(job_id, state)
    return state


def _translate(state, job_id, *, batch_size=2, **kwargs):
    return core.translate_stage(
        state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang=TARGET_LANG, style_rules="",
        enable_review=True, use_tm=True, batch_size=batch_size,
        translation_concurrency=1, **kwargs)


def _run(job_id: str, paragraphs, *, batch_size=2, **kwargs):
    return _translate(_seed(job_id, paragraphs), job_id,
                      batch_size=batch_size, **kwargs)


def _crash_run(job_id: str, paragraphs, *, batch_size=2, llm=None):
    """Run a translation whose snapshot write always fails.

    That is the interesting crash: the batch is appended to the journal and the
    snapshot never happens, so the only thing standing between the user and a
    re-translation is the journal.
    """
    state = _seed(job_id, paragraphs)
    real_save = core.save_job_state
    real_llm = core.call_llm

    def exploding_save(_job_id, _state):
        if not _state.get("pairs"):
            # The one baseline snapshot taken before the first batch commits.
            return real_save(_job_id, _state)
        raise OSError("simulated crash: snapshot write failed")

    core.save_job_state = exploding_save
    core.call_llm = llm or _fake_llm
    try:
        with pytest.raises(Exception):
            _translate(state, job_id, batch_size=batch_size)
    finally:
        core.save_job_state = real_save
        core.call_llm = real_llm
    # The in-memory state is gone; only what reached the disk survives.
    repo.invalidate_cache()
    return state


def _rewrite_journal(job_id: str, records):
    path = journal.journal_path(job_id)
    if not records:
        path.unlink(missing_ok=True)
        return
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False,
                           separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8")


# --------------------------------------------------------------------------
# record shape
# --------------------------------------------------------------------------
def test_record_is_strong_typed_and_carries_the_design_fields(
        temp_job_env: Path):
    """A record must describe the mutation, not encode a diff of two states."""
    job_id = "record-shape"
    _crash_run(job_id, _paragraphs(8))

    records = journal.read_mutations(job_id)
    assert len(records) == 4, "8 segments / batch 2 = 4 committed batches"
    first = records[0]
    assert first["schema"] == journal.JOURNAL_SCHEMA_VERSION
    assert first["mutation"] == journal.MUTATION_TRANSLATION_BATCH
    for field in ("seq", "mutation_id", "ts", "batch", "offset", "segment_ids",
                  "pairs"):
        assert field in first, f"the design requires a {field!r} field"
    assert first["offset"] == 0 and first["batch"] == 0
    assert len(first["segment_ids"]) == 2
    # Sequence numbers are strictly increasing and start at 1.
    assert [record["seq"] for record in records] == [1, 2, 3, 4]
    # Pairs are addressed absolutely: record k starts where record k-1 ended.
    assert [record["offset"] for record in records] == [0, 2, 4, 6]
    # Extend entries carry the length they were appended to, so a double
    # application is detectable rather than silently duplicating items.
    for record in records:
        for entry in (record.get("extend") or {}).values():
            assert isinstance(entry["base"], int)
            assert isinstance(entry["items"], list)


# --------------------------------------------------------------------------
# replay semantics
# --------------------------------------------------------------------------
def test_replay_is_idempotent(temp_job_env: Path):
    """Repeating a recovery from the same snapshot reproduces the same state."""
    job_id = "idempotent"
    _crash_run(job_id, _paragraphs(10))

    snapshot = core.load_job_state(job_id)          # already replayed once
    assert len(snapshot["pairs"]) == 10

    base = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    base["journal_cursor"] = 0                      # pretend the snapshot is old
    first, second = copy.deepcopy(base), copy.deepcopy(base)
    report_a = journal.replay(job_id, first)
    report_b = journal.replay(job_id, second)
    assert report_a["applied"] == 5 and report_a["gap"] is False
    assert second == first, "a second replay must change nothing"
    # And a replay on an already-caught-up state is a no-op, not a failure.
    assert journal.replay(job_id, first)["applied"] == 0
    assert report_b["cursor"] == report_a["cursor"]


def test_truncated_tail_line_is_dropped(temp_job_env: Path):
    """A crash mid-``write`` leaves a partial last line; it must be ignored."""
    job_id = "truncated-tail"
    _crash_run(job_id, _paragraphs(10))
    path = journal.journal_path(job_id)
    raw = path.read_bytes()
    path.write_bytes(raw[: int(len(raw) * 0.95)])   # tear the last record

    state = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    state["journal_cursor"] = 0
    report = journal.replay(job_id, state)
    assert report["gap"] is False, "a partial tail is normal, not a gap"
    assert report["applied"] == 4, "the torn record is simply not applied"
    assert len(state["pairs"]) == 8


def test_corrupt_middle_line_stops_replay_instead_of_skipping(
        temp_job_env: Path):
    """Applying a later mutation while an earlier one is lost is never safe."""
    job_id = "corrupt-middle"
    _crash_run(job_id, _paragraphs(10))
    path = journal.journal_path(job_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = '{"seq": 3, "schema": 1, "mutation": "translation_bat'
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    state = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    state["journal_cursor"] = 0
    report = journal.replay(job_id, state)
    assert report["applied"] == 2, "replay stops at the first unreadable line"
    assert len(journal.read_mutations(job_id)) == 2, (
        "nothing after the damage is read: skipping a mutation could apply a "
        "later one out of order")
    assert len(state["pairs"]) == 4
    # The records after the damage are untouched on disk, so a later, healthy
    # recovery can still pick them up.
    assert journal.size(job_id) > 0
    assert len(path.read_text(encoding="utf-8").splitlines()) == 5


def test_replay_never_calls_a_model(temp_job_env: Path, monkeypatch):
    """The whole point: recovery is I/O, not re-translation."""
    job_id = "no-model-calls"
    _crash_run(job_id, _paragraphs(20))

    calls = {"n": 0}

    def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return _fake_llm(*args, **kwargs)

    monkeypatch.setattr(core, "call_llm", counting_llm)
    recovered = core.load_job_state(job_id)
    assert calls["n"] == 0
    assert len(recovered["pairs"]) == 20

    # Resuming a fully recovered job must not re-request anything either.
    resumed = _translate(recovered, job_id, batch_size=2)
    assert calls["n"] == 0
    assert len(resumed["pairs"]) == 20


# --------------------------------------------------------------------------
# crash classes
# --------------------------------------------------------------------------
def test_crash_before_append_recomputes_only_that_batch(
        temp_job_env: Path, monkeypatch):
    """Class 1: the append never landed, so that batch does not exist yet."""
    job_id = "crash-before-append"
    _crash_run(job_id, _paragraphs(12))
    records = journal.read_mutations(job_id)
    assert len(records) == 6
    _rewrite_journal(job_id, records[:-1])       # the last append never landed

    recovered = core.load_job_state(job_id)
    assert len(recovered["pairs"]) == 10, "only the durable batches come back"

    calls = {"n": 0}

    def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return _fake_llm(*args, **kwargs)

    monkeypatch.setattr(core, "call_llm", counting_llm)
    resumed = _translate(recovered, job_id, batch_size=2)
    assert len(resumed["pairs"]) == 12
    assert [pair["source"] for pair in resumed["pairs"]] == _paragraphs(12)
    assert calls["n"] > 0, "the missing batch must be recomputed"


def test_crash_after_append_before_snapshot_recovers_without_model_calls(
        temp_job_env: Path, monkeypatch):
    """Class 2: the append landed, the snapshot never did."""
    job_id = "crash-after-append"
    _crash_run(job_id, _paragraphs(20))

    # The snapshot on disk really is behind: it is the seed.
    on_disk = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    assert on_disk["pairs"] == []
    assert on_disk.get("journal_cursor") in (None, 0)
    assert journal.size(job_id) > 0

    calls = {"n": 0}

    def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return _fake_llm(*args, **kwargs)

    monkeypatch.setattr(core, "call_llm", counting_llm)
    recovered = core.load_job_state(job_id)
    assert calls["n"] == 0, "recovery must not re-translate anything"
    assert len(recovered["pairs"]) == 20
    assert recovered["review_stats"]["reviewed_segments"] == 20
    assert recovered["review_stats"]["batches_reviewed"] == 10


def test_replay_after_snapshot_does_not_duplicate_pairs(temp_job_env: Path):
    """Class 3: the snapshot is written, compaction has not run yet.

    Replaying the records it already contains must be a no-op -- this is the
    ``journal_cursor`` contract.
    """
    job_id = "snapshot-then-replay"
    state = _run(job_id, _paragraphs(20), batch_size=2)
    assert len(state["pairs"]) == 20

    # Re-create the post-snapshot / pre-compaction situation: the journal is
    # back on disk, and the snapshot says it already contains all of it.
    records = []
    for index, offset in enumerate(range(0, 20, 2), start=1):
        records.append({
            "schema": journal.JOURNAL_SCHEMA_VERSION,
            "mutation": journal.MUTATION_TRANSLATION_BATCH,
            "mutation_id": f"replay-{index}", "ts": "2026-01-01T00:00:00+00:00",
            "seq": index, "batch": index - 1, "offset": offset,
            "segment_ids": [f"seg-{offset}", f"seg-{offset + 1}"],
            "pairs": state["pairs"][offset:offset + 2],
        })
    _rewrite_journal(job_id, records)

    raw = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    assert raw["journal_cursor"] == len(records)

    repo.invalidate_cache()
    reloaded = core.load_job_state(job_id)
    assert len(reloaded["pairs"]) == 20, "records must not be applied twice"
    assert _truth_digest(reloaded) == _truth_digest(state)
    assert journal.read_mutations(job_id) == records, "the journal is untouched"


def test_crash_during_compaction_leaves_a_complete_truth_path(temp_job_env: Path):
    """Class 4: compaction is snapshot-replace then rename-then-unlink.

    A crash between the two halves of compaction must still leave exactly one
    complete truth path -- never two, never none.
    """
    job_id = "compaction-crash"
    _crash_run(job_id, _paragraphs(12))
    path = journal.journal_path(job_id)
    records = journal.read_mutations(job_id)
    assert len(records) == 6

    # Half one: the snapshot was replaced, the journal was NOT retired.
    recovered = core.load_job_state(job_id)
    assert len(recovered["pairs"]) == 12
    core.save_job_state(job_id, recovered)          # cursor == max_seq: retires
    assert journal.size(job_id) == 0
    _rewrite_journal(job_id, records)               # ...then put it back
    assert journal.size(job_id) > 0
    repo.invalidate_cache()
    again = core.load_job_state(job_id)
    assert len(again["pairs"]) == 12, "records must not be applied twice"
    assert _truth_digest(again) == _truth_digest(recovered)

    # Half two: the rename happened, the unlink did not.
    path.replace(path.with_suffix(".jsonl.applied"))
    assert not path.exists()
    assert journal.max_seq(job_id) == 0, "a retired journal is invisible"
    repo.invalidate_cache()
    final = core.load_job_state(job_id)
    assert len(final["pairs"]) == 12
    assert _truth_digest(final) == _truth_digest(recovered)


def test_crash_with_pending_tm_promotions_does_not_duplicate_memory(
        temp_job_env: Path, monkeypatch):
    """Class 5: the TM cache is derived, so replaying must stay idempotent."""
    job_id = "tm-pending"
    _crash_run(job_id, _paragraphs(20))

    # The promotions never reached tm.json: the snapshot that would have
    # flushed them is exactly the write that failed.
    assert core.load_tm(None) == {}

    recovered = core.load_job_state(job_id)
    assert len(recovered["pairs"]) == 20

    # Resuming re-derives the memory once, with no duplicates.
    _translate(recovered, job_id, batch_size=2)
    tm = core.load_tm(None)
    assert len(tm) == 20, "one entry per segment, never two"
    for paragraph in _paragraphs(20):
        assert core.tm_scope_key(TARGET_LANG, paragraph) in tm, (
            "every recovered segment must have exactly one memory entry")


def test_human_edit_wins_over_older_generated_records(
        temp_job_env: Path, monkeypatch):
    """Class 6: recovery happens before an edit, never over it."""
    job_id = "human-edit"
    _crash_run(job_id, _paragraphs(12))

    # The user opens the job: the load replays the journal, so the state the
    # editor works on already contains every committed batch.
    state = core.load_job_state(job_id)
    assert len(state["pairs"]) == 12
    assert journal.cursor(state) == journal.max_seq(job_id)

    state["pairs"][5]["target"] = "人工修订后的译文。"
    core._mark_translation_truth_changed(
        job_id, state, [5], "用户人工修订第 6 段译文",
        actor="user", action="translation_edit")
    version_after_edit = state["translation_truth"]["version"]
    core.save_job_state(job_id, state)

    # The save compacted the journal: it no longer exists to be replayed over
    # the edit.
    assert journal.size(job_id) == 0

    repo.invalidate_cache()
    reloaded = core.load_job_state(job_id)
    assert reloaded["pairs"][5]["target"] == "人工修订后的译文。"
    assert reloaded["translation_truth"]["version"] == version_after_edit
    assert len(reloaded["pairs"]) == 12

    # And it stays that way through another crash/recover cycle that commits
    # further batches on top of the edit.
    resumed = _translate(reloaded, job_id, batch_size=2)
    assert resumed["pairs"][5]["target"] == "人工修订后的译文。"
    assert len(resumed["pairs"]) == 12


# --------------------------------------------------------------------------
# the trade's safety net
# --------------------------------------------------------------------------
def test_unusable_batch_delta_falls_back_to_a_snapshot(
        temp_job_env: Path, monkeypatch):
    """An undescribable delta costs performance, never durability."""
    job_id = "unusable-delta"
    real_builder = core.build_batch_journal_record
    calls = {"n": 0}

    def sometimes_none(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            return None
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(core, "build_batch_journal_record", sometimes_none)
    state = _run(job_id, _paragraphs(12), batch_size=2)

    assert len(state["pairs"]) == 12
    assert state["state_journal"]["unusable_batches"] == 1
    assert state["state_journal"]["enabled"] is False, (
        "the journal is abandoned for the rest of the run")
    # Everything is still durable: the snapshot took over.
    repo.invalidate_cache()
    reloaded = core.load_job_state(job_id)
    assert len(reloaded["pairs"]) == 12
    assert _truth_digest(reloaded) == _truth_digest(state)


def test_journal_is_retired_only_after_the_snapshot_covers_it(
        temp_job_env: Path):
    """A save of a state that is behind the journal must not drop records."""
    job_id = "rotation-guard"
    _crash_run(job_id, _paragraphs(12))
    records = journal.read_mutations(job_id)
    assert len(records) == 6

    # A legitimate save of the *seed* state: it is behind the journal, so the
    # journal has to survive -- otherwise those six batches would be redone.
    seed = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    assert seed.get("journal_cursor") in (None, 0)
    core.save_job_state(job_id, seed)
    assert len(journal.read_mutations(job_id)) == 6

    # Once the state carries the records, the next save retires the journal.
    recovered = core.load_job_state(job_id)
    core.save_job_state(job_id, recovered)
    assert journal.size(job_id) == 0
    assert journal.max_seq(job_id) == 0


def _inflate_last_record(job_id: str) -> int:
    """Make the newest record larger than the tail-scan window.

    A long document's batch carries the whole entity registry in ``set``, so a
    single record of several hundred KB is ordinary, not adversarial.
    """
    records = journal.read_mutations(job_id)
    registry = {f"entity-{i}": {"source": f"Name {i}", "target": "名" * 400}
                for i in range(700)}
    records[-1].setdefault("set", {})["entity_registry"] = registry
    _rewrite_journal(job_id, records)
    assert len(json.dumps(records[-1], ensure_ascii=False).encode("utf-8")) \
        > journal._TAIL_SCAN_BYTES
    return records[-1]["seq"]


def test_record_larger_than_the_scan_window_is_still_seen(temp_job_env: Path):
    job_id = "oversized-record"
    _crash_run(job_id, _paragraphs(12))
    newest = _inflate_last_record(job_id)

    assert journal.max_seq(job_id) == newest

    state = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    state["journal_cursor"] = 0
    report = journal.replay(job_id, state)
    assert report["applied"] == newest
    assert len(state["pairs"]) == 12
    assert len(state["entity_registry"]) == 700


def test_oversized_record_keeps_the_journal_through_a_behind_save(
        temp_job_env: Path):
    """The rotation guard must not read "no records" as "all applied"."""
    job_id = "oversized-guard"
    _crash_run(job_id, _paragraphs(12))
    newest = _inflate_last_record(job_id)

    seed = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    core.save_job_state(job_id, seed)
    assert [r["seq"] for r in journal.read_mutations(job_id)][-1] == newest


def test_undeterminable_tail_blocks_rotation_but_not_replay(temp_job_env: Path):
    job_id = "garbage-tail"
    _crash_run(job_id, _paragraphs(12))
    path = journal.journal_path(job_id)
    with path.open("ab") as stream:
        stream.write(b"not json\n" * (journal._TAIL_SCAN_LINES + 1))

    state = json.loads(core.job_state_path(job_id).read_text(encoding="utf-8"))
    state["journal_cursor"] = 0
    assert journal.rotate_if_applied(job_id, state) is False
    assert path.exists()
    report = journal.replay(job_id, state)
    assert report["applied"] == 6, "the readable prefix is still replayed"


def test_journal_disabled_restores_the_bounded_cadence(
        temp_job_env: Path, monkeypatch):
    """The pre-journal behaviour must remain exactly reachable."""
    monkeypatch.setenv("FOLITH_STATE_JOURNAL", "0")
    job_id = "journal-off"
    state = _run(job_id, _paragraphs(60), batch_size=2)

    plan = state["batch_plan"]["snapshot_cadence"]
    assert plan["journal_enabled"] is False
    assert plan["every_batches"] == 3, "60 batches, 10% cap"
    assert plan["journal_bytes"] == 0
    assert plan["crash_window_segments"] == 6
    assert journal.size(job_id) == 0
    # Snapshots are back to one per cadence window plus the loop exit.
    assert state["state_journal"]["snapshots"] == 11


def test_snapshot_count_is_decoupled_from_segment_count(
        temp_job_env: Path, monkeypatch):
    """The regression that motivated the journal, as a count.

    A batch-count cadence produces O(segments) full snapshots.  A byte budget
    on the journal produces a number that does not move when the document does.
    """
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "1")
    dense = _run("count-dense", _paragraphs(60), batch_size=2)
    assert dense["state_journal"]["snapshots"] == 32, "baseline + one per batch + exit"

    monkeypatch.delenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY")
    journal.reset_dir_cache()
    repo.invalidate_cache()
    with_journal = _run("count-journal", _paragraphs(60), batch_size=2)
    bigger = _run("count-journal-big", _paragraphs(600), batch_size=2)

    # One baseline snapshot before the first batch, one at loop exit.
    assert with_journal["state_journal"]["snapshots"] == 2
    assert bigger["state_journal"]["snapshots"] == 2, (
        "ten times the document, the same number of full snapshots")


def test_cursor_is_part_of_the_state_cache_signature(temp_job_env: Path):
    """A journal append must invalidate the cached state, not be invisible."""
    job_id = "cache-signature"
    _crash_run(job_id, _paragraphs(8))

    first = core.load_job_state(job_id)
    assert len(first["pairs"]) == 8

    records = journal.read_mutations(job_id)
    _rewrite_journal(job_id, records[:1])
    second = core.load_job_state(job_id)
    assert len(second["pairs"]) == 2, (
        "the cache must not hide a journal that changed on disk")


def test_fallback_snapshot_failure_preserves_previously_committed_journal(temp_job_env, monkeypatch):
    import errno
    job_id = 'fallback-disk-full'
    state = _seed(job_id, _paragraphs(2))
    builder = core.build_batch_journal_record
    writer = repo.save_job_state_atomic

    def fail_second_delta(state, *args, **kwargs):
        return None if kwargs['offset'] > 0 else builder(state, *args, **kwargs)

    def fail_snapshot(job, value):
        if len(value.get('pairs') or []) >= 2:
            raise OSError(errno.ENOSPC, 'controlled snapshot failure')
        return writer(job, value)

    monkeypatch.setattr(core, 'build_batch_journal_record', fail_second_delta)
    monkeypatch.setattr(repo, 'save_job_state_atomic', fail_snapshot)
    try:
        with pytest.raises(Exception, match='磁盘|snapshot failure'):
            core.translate_stage(
                state, job_id, [], 'DeepSeek', 'fake', 'fake', TARGET_LANG, '', False,
                use_tm=False, batch_size=1, max_batch_chars=1000)
        repo.invalidate_cache()
        assert len(core.load_job_state(job_id)['pairs']) == 1
    finally:
        core._workflow_errors.reset(job_id)


# --------------------------------------------------------------------------
# knowledge-candidate delta (issue #56)
# --------------------------------------------------------------------------
def _candidate(n: int) -> dict:
    return {"source": f"Term {n}", "observed_target": f"术语{n}",
            "occurrences": [n], "observed_segments": [n],
            "status": "emergent_candidate", "provenance": "generated_continuity",
            "scope": "document", "kind": "term", "confidence": 0.35,
            "first_observed_segment": n}


def _journal_batch(state: dict, new_candidates, *, batch: int, update=None):
    """Mutate ``state`` like one commit and return its journal record."""
    baseline = core.journal_baseline(state)
    offset = len(state["pairs"])
    pairs = [{"source": f"s{offset}", "target": f"t{offset}"}]
    state["pairs"].extend(pairs)
    if update:
        index, fields = update
        state["knowledge_candidates"][index].update(fields)
    state["knowledge_candidates"] = (
        list(state["knowledge_candidates"]) + list(new_candidates))
    state["translation_continuity"] = list(state["knowledge_candidates"])
    record = core.build_batch_journal_record(
        state, baseline, {}, batch=batch, offset=offset,
        segment_ids=[offset], pairs=pairs)
    assert record is not None
    return record


def _base_state() -> dict:
    state = {key: [] for key in core._JOURNAL_EXTEND_KEYS}
    state.update(pairs=[], knowledge_candidates=[], translation_continuity=[])
    return state


def test_candidate_journal_record_size_stays_bounded():
    """Per-batch record size must not follow the candidate count (#56)."""
    state = _base_state()
    sizes = []
    for batch in range(400):
        record = _journal_batch(state, [_candidate(batch)], batch=batch)
        record["seq"] = batch + 1
        assert "translation_continuity" not in (record.get("set") or {})
        assert "knowledge_candidates" not in (record.get("set") or {})
        sizes.append(len(json.dumps(record, ensure_ascii=False)))
    assert len(state["knowledge_candidates"]) == 400
    assert max(sizes) < 2 * sizes[0] + 200, (
        f"record size grew from {sizes[0]} to {max(sizes)} bytes")
    assert sum(sizes) < 400 * (sizes[0] + 200), "total journal growth is not linear"


def test_incremental_replay_equals_full_replay(temp_job_env: Path):
    """Replaying records one at a time lands on the same state as replaying all."""
    job_id = "candidate-incremental"
    live = _base_state()
    records = []
    for batch in range(12):
        update = (0, {"occurrences": [0, batch], "observed_target": f"改{batch}"}) \
            if batch and batch % 3 == 0 else None
        record = _journal_batch(live, [_candidate(batch + 1)], batch=batch,
                                update=update)
        record["seq"] = batch + 1
        records.append(record)
    journal.journal_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    _rewrite_journal(job_id, records)

    full = _base_state()
    report = journal.replay(job_id, full)
    assert report["applied"] == 12 and not report["gap"]
    assert full["knowledge_candidates"] == live["knowledge_candidates"]
    assert full["translation_continuity"] == live["knowledge_candidates"]
    assert full["pairs"] == live["pairs"]

    stepped = _base_state()
    for upto in range(1, 13):
        _rewrite_journal(job_id, records[:upto])
        journal.replay(job_id, stepped)
    assert stepped == full


def test_old_full_list_records_still_replay(temp_job_env: Path):
    """Journals written before the delta format (schema 1) keep working."""
    job_id = "candidate-legacy"
    items = [_candidate(1), _candidate(2)]
    record = {
        "schema": 1, "mutation": journal.MUTATION_TRANSLATION_BATCH,
        "mutation_id": "x", "ts": "t", "batch": 0, "offset": 0,
        "segment_ids": [0], "pairs": [{"source": "a", "target": "b"}],
        "set": {"knowledge_candidates": items,
                "translation_continuity": list(items)},
        "seq": 1,
    }
    journal.journal_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    _rewrite_journal(job_id, [record])
    state = _base_state()
    assert journal.replay(job_id, state)["applied"] == 1
    assert state["knowledge_candidates"] == items
    assert state["translation_continuity"] == items


def test_unfaithful_candidate_change_falls_back_to_a_snapshot():
    """A shrunken queue cannot be described as a delta."""
    state = _base_state()
    state["knowledge_candidates"] = [_candidate(1), _candidate(2)]
    baseline = core.journal_baseline(state)
    state["knowledge_candidates"] = state["knowledge_candidates"][:1]
    assert core.build_batch_journal_record(
        state, baseline, {}, batch=0, offset=0, segment_ids=[], pairs=[]) is None


def test_gap_does_not_leave_a_partially_applied_record(temp_job_env: Path):
    """A record whose bases do not line up writes none of its pairs (#56 part 2)."""
    job_id = "candidate-gap"
    state = _base_state()
    first = _journal_batch(state, [_candidate(1)], batch=0)
    first["seq"] = 1
    second = _journal_batch(state, [_candidate(2)], batch=1)
    second["seq"] = 2
    second["extend"] = {"findings": {"base": 234, "items": [{"x": 1}]}}
    journal.journal_path(job_id).parent.mkdir(parents=True, exist_ok=True)
    _rewrite_journal(job_id, [first, second])
    replayed = _base_state()
    report = journal.replay(job_id, replayed)
    assert report["gap"] and report["applied"] == 1
    assert len(replayed["pairs"]) == 1


def test_pre_loop_state_changes_survive_a_crash(temp_job_env: Path, monkeypatch):
    """Findings added before batch 0 must be in the snapshot the journal extends.

    Regression for #56 part 2: the source-quality findings are appended before
    the first batch, so without a baseline snapshot record 2 carried an
    ``extend.findings`` base the snapshot did not have, and replay stopped after
    one batch.
    """
    job_id = "pre-loop-findings"
    paragraphs = _paragraphs(8)
    state = _seed(job_id, paragraphs)
    # The gate quarantines segment 1 and appends a finding before batch 0.
    monkeypatch.setattr(core, "_audit_source_quality_for_state",
                        lambda _state, **_kw: {"flags": [{"segment_index": 1}]})
    monkeypatch.setattr(core, "_source_quality_finding", lambda flag: {
        "finding_id": "sq1", "type": "source_quality_gate",
        "segment_index": flag["segment_index"]})
    real_save = core.save_job_state

    def exploding_save(_job_id, _state):
        if not _state.get("pairs"):
            return real_save(_job_id, _state)
        raise OSError("simulated crash: snapshot write failed")

    core.save_job_state = exploding_save
    try:
        with pytest.raises(Exception):
            _translate(state, job_id, batch_size=2)
    finally:
        core.save_job_state = real_save
    repo.invalidate_cache()
    recovered = core.load_job_state(job_id)
    assert len(recovered["pairs"]) == 8
    assert [f["finding_id"] for f in recovered["findings"]
            if f.get("type") == "source_quality_gate"] == ["sq1"]


def test_suffix_reader_retries_a_partial_line_and_stops_at_corruption(temp_job_env):
    job_id = "suffix-reader"
    live = _base_state()
    records = []
    for batch in range(3):
        record = _journal_batch(live, [_candidate(batch)], batch=batch)
        record["seq"] = batch + 1
        records.append((json.dumps(record) + "\n").encode())
    path = journal.journal_path(job_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(records[0] + records[1][:50])
    state = _base_state()
    first = journal.replay_from(job_id, state)
    assert first["applied"] == 1
    assert first["byte_offset"] == len(records[0])
    with path.open("ab") as stream:
        stream.write(records[1][50:])
    second = journal.replay_from(job_id, state, first["byte_offset"])
    assert second["applied"] == 1
    assert second["byte_offset"] == len(records[0]) + len(records[1])
    with path.open("ab") as stream:
        stream.write(b"not json\n" + records[2])
    stopped = journal.replay_from(job_id, state, second["byte_offset"])
    assert stopped["applied"] == 0
    assert stopped["byte_offset"] == second["byte_offset"]
    assert len(state["pairs"]) == 2


def test_suffix_replay_equals_full_replay_with_candidate_updates(temp_job_env):
    job_id = "suffix-equivalence"
    live, stepped = _base_state(), _base_state()
    offset = 0
    for batch in range(20):
        record = _journal_batch(
            live, [_candidate(batch)], batch=batch,
            update=(0, {"observed_target": f"修订{batch}"}) if batch else None)
        record["seq"] = batch + 1
        journal.append_mutation(job_id, record)
        report = journal.replay_from(job_id, stepped, offset)
        assert report["applied"] == 1 and not report["gap"]
        offset = report["byte_offset"]
        full = _base_state()
        journal.replay(job_id, full)
        assert stepped == full


def test_cached_append_reads_only_the_suffix_without_reloading_snapshot(
        temp_job_env, monkeypatch):
    job_id = "cached-suffix"
    _crash_run(job_id, _paragraphs(8))
    records = journal.read_mutations(job_id)
    _rewrite_journal(job_id, records[:1])
    repo.invalidate_cache()
    first = core.load_job_state(job_id)
    first_size = journal.size(job_id)
    reads = repo.get_cache_stats()["disk_reads"]
    real_reader = journal.read_mutations_from
    offsets = []

    def track_offset(_job_id, offset=0, *args, **kwargs):
        offsets.append(offset)
        return real_reader(_job_id, offset, *args, **kwargs)

    monkeypatch.setattr(journal, "read_mutations_from", track_offset)
    journal.append_mutation(job_id, records[1])
    second = core.load_job_state(job_id)
    assert len(first["pairs"]) == 2  # Caller isolation survives an incremental load.
    assert len(second["pairs"]) == 4
    assert offsets == [first_size]
    assert repo.get_cache_stats()["disk_reads"] == reads
    cold = repo.load_job_state_cached(job_id, force_reload=True)
    assert second == cold


@pytest.mark.parametrize("replace", [False, True])
def test_replaced_or_truncated_journal_does_not_reuse_a_byte_offset(
        temp_job_env, replace):
    job_id = "journal-replaced"
    _crash_run(job_id, _paragraphs(8))
    records = journal.read_mutations(job_id)
    _rewrite_journal(job_id, records[:2])
    repo.invalidate_cache()
    assert len(core.load_job_state(job_id)["pairs"]) == 4
    path = journal.journal_path(job_id)
    if replace:
        replacement = path.with_suffix(".replacement")
        # A replacement at least as large as the old journal still requires a
        # cold read: size alone cannot establish that it retained the prefix.
        record = copy.deepcopy(records[0])
        record["pairs"][0]["target"] = "新文件里的译文" * 300
        replacement.write_text(json.dumps(record) + "\n", encoding="utf-8")
        replacement.replace(path)
    else:
        _rewrite_journal(job_id, records[:1])
    loaded = core.load_job_state(job_id)
    cold = repo.load_job_state_cached(job_id, force_reload=True)
    assert loaded == cold
    assert len(loaded["pairs"]) == 2


def test_card_progress_follows_durable_commits_without_full_summary_rebuild(
        temp_job_env, monkeypatch):
    real_update = repo.update_job_summary_progress
    real_build = repo.build_job_summary
    builds = []
    seen = []

    def count_build(job_id, state):
        builds.append(job_id)
        return real_build(job_id, state)

    def observe(job_id, state, translated_count, **kwargs):
        real_update(job_id, state, translated_count, **kwargs)
        summary = repo.load_job_summary(job_id)
        seen.append((summary["progress_percent"], summary["segment_count"],
                     len(core.load_job_state(job_id)["pairs"])))

    monkeypatch.setattr(repo, "update_job_summary_progress", observe)
    monkeypatch.setattr(repo, "build_job_summary", count_build)
    _run("card-progress", _paragraphs(8))
    assert seen == [(25, 8, 2), (50, 8, 4), (75, 8, 6), (100, 8, 8)]
    assert len(builds) == 3  # Seed, pre-loop baseline, loop-exit snapshot only.
