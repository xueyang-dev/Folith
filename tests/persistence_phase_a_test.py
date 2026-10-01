"""Persistence Phase A contracts.

Phase A optimises the *full snapshot* path while preserving the state authority
model: ``state.json`` remains the single authoritative complete task snapshot.
These tests pin every guarantee the optimisation is allowed to rely on, and the
ones it must not break:

* semantic JSON identity of the compact snapshot (no key/value/Unicode change);
* a bounded automatic-snapshot cadence with a hard worst-case crash window;
* the derived ``summary.json`` read model staying derived;
* save/resume, cancellation and in-loop failure all leaving a resumable,
  complete-at-that-point authoritative snapshot;
* translation-truth fields unchanged by the persistence work;
* stable invariants (serialization count, bytes written, snapshot count)
  instead of fixed wall-clock thresholds.

Journal / compaction / crash-replay contracts are deliberately absent: the
Phase A benchmark left ``state_commit`` at 8.1% of a 5000-segment run, so
Phase B was not justified by data and no journal exists to test.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import core
from folith import persistence_profiler
from folith.storage import job_repository as repo

TRUTH_FIELDS = (
    "source", "initial_target", "target", "accepted_target",
    "reviewed", "review_status", "target_provenance",
)


@pytest.fixture
def temp_job_env(tmp_path: Path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    repo.reset_cache_stats()
    persistence_profiler.disable()
    # Counters are process-global: a test that asserts an exact save count must
    # not inherit the previous test's records.
    persistence_profiler.reset()
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()
        persistence_profiler.disable()
        persistence_profiler.reset()


def _fake_llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
    if "独立的翻译审校专家" in (system_prompt or ""):
        return json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    results = [f"译文：{line.split('. ', 1)[-1]}" for line in lines]
    return json.dumps(results, ensure_ascii=False)


def _run_translation(job_id: str, paragraphs, *, batch_size: int = 2,
                     concurrency: int = 1, profiler=None, **kwargs):
    state = core.new_job_state("doc.txt")
    state["paras"] = list(paragraphs)
    core.save_job_state(job_id, state)
    return core.translate_stage(
        state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang="简体中文", style_rules="",
        enable_review=True, use_tm=True, batch_size=batch_size,
        translation_concurrency=concurrency, profiler=profiler, **kwargs)


def _truth_digest(state: dict) -> str:
    import hashlib
    payload = json.dumps(
        [{key: pair.get(key) for key in TRUTH_FIELDS}
         for pair in state.get("pairs") or []],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# §4 compact serialization
# --------------------------------------------------------------------------
def test_compact_snapshot_is_semantically_identical(temp_job_env: Path):
    """Whitespace is not data: compact output must decode to the same object."""
    job_id = "compact-identity"
    state = core.new_job_state("书稿.docx")
    state["paras"] = ['第一段包含引号 "q"、反斜杠 \\ 与换行\n以及制表符\t。']
    state["pairs"] = [{
        "source": "第一段", "initial_target": "初稿", "target": "译文第一段",
        "reviewed": True, "review_status": "reviewed_clean",
    }]
    core.save_job_state(job_id, state)

    raw = core.job_state_path(job_id).read_text(encoding="utf-8")
    pretty = json.dumps(state, ensure_ascii=False, indent=2)

    assert json.loads(raw) == json.loads(pretty), (
        "compact JSON must be semantically identical to the pretty encoding")
    assert json.loads(raw) == state, "no key or value may change"
    # Unicode stays literal and control characters stay escaped.
    assert "第一段" in raw
    assert "\\u7b2c" not in raw
    assert "\\n" in raw and "\\t" in raw
    # No pretty-printing: compact output has no structural newlines at all.
    assert "\n" not in raw
    assert '"  "' not in raw


def test_snapshot_round_trips_through_the_authoritative_loader(temp_job_env: Path):
    job_id = "round-trip"
    state = core.new_job_state("doc.txt")
    state["paras"] = [f"Segment {i}" for i in range(6)]
    state["pairs"] = [{"source": f"Segment {i}", "target": f"译文 {i}"}
                      for i in range(6)]
    core.save_job_state(job_id, state)

    loaded = core.load_job_state(job_id)
    assert loaded is not None
    assert loaded["pairs"] == state["pairs"]
    assert loaded["paras"] == state["paras"]


def test_repeated_saves_leave_the_last_state_authoritative(temp_job_env: Path):
    """Every save must produce a complete snapshot, not a delta."""
    job_id = "repeated-saves"
    state = core.new_job_state("doc.txt")
    state["paras"] = [f"S{i}" for i in range(8)]
    for count in range(1, 9):
        state["pairs"] = [{"source": f"S{i}", "target": f"T{i}"}
                          for i in range(count)]
        core.save_job_state(job_id, state)
        loaded = core.load_job_state(job_id)
        assert len(loaded["pairs"]) == count
        assert loaded["paras"] == state["paras"], (
            "the authoritative snapshot must always be the complete task state")


# --------------------------------------------------------------------------
# §6 derived summary stays derived
# --------------------------------------------------------------------------
def test_summary_is_a_derived_read_model(temp_job_env: Path):
    job_id = "summary-derived"
    state = core.new_job_state("book.docx")
    state["pairs"] = [{"source": "S1", "target": "T1"},
                      {"source": "S2", "target": "T2"}]
    core.save_job_state(job_id, state)

    summary_path = repo.get_job_summary_path(job_id)
    assert summary_path.is_file()
    on_disk = json.loads(summary_path.read_text(encoding="utf-8"))
    assert on_disk == repo.build_job_summary(job_id, state)

    # Deleting the derived model must not change the authoritative snapshot,
    # and the read path must rebuild it in memory without writing to disk.
    summary_path.unlink()
    assert core.load_job_state(job_id)["pairs"] == state["pairs"]
    summaries = core.list_job_summaries()
    assert [item["job_id"] for item in summaries] == [job_id]
    assert not summary_path.exists(), "read path must not recreate summary.json"


def test_summary_build_is_constant_time(temp_job_env: Path):
    """§6: the summary is built from scalars, so it never rescans pairs."""
    small = core.new_job_state("s.docx")
    small["pairs"] = [{"source": f"S{i}", "target": f"T{i}"} for i in range(2)]
    large = core.new_job_state("l.docx")
    large["pairs"] = [{"source": f"S{i}", "target": f"T{i}"} for i in range(4000)]
    assert repo.build_job_summary("s", small)["segment_count"] == 2
    assert repo.build_job_summary("l", large)["segment_count"] == 4000


# --------------------------------------------------------------------------
# §7 bounded snapshot cadence
# --------------------------------------------------------------------------
def test_snapshot_cadence_is_bounded_and_caps_short_documents(monkeypatch):
    """Two regimes, and the pre-journal one stays exactly reachable.

    With the state journal on, the batch-count trigger is replaced by a
    journal-byte budget: the crash window in *segments* is 0 because recovery
    replays committed batches instead of re-translating them.  With the journal
    off the original bounded cadence is restored verbatim -- which is what keeps
    this change auditable rather than a leap of faith.
    """
    monkeypatch.delenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", raising=False)
    monkeypatch.delenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", raising=False)
    monkeypatch.delenv("FOLITH_STATE_JOURNAL", raising=False)
    monkeypatch.delenv("FOLIOTHREAD_STATE_JOURNAL", raising=False)

    journal_on = core.state_snapshot_cadence(total_batches=250)
    assert journal_on["every_batches"] == 0, "no batch-count trigger"
    assert journal_on["journal_bytes"] > 0, "a replay budget instead"
    assert core.state_snapshot_cadence(total_batches=1250)["every_batches"] == 0
    assert core.state_snapshot_crash_window(batch_size=4, total_batches=25) == 0
    assert core.state_snapshot_crash_window(
        batch_size=4, total_batches=1250) == 0

    monkeypatch.setenv("FOLITH_STATE_JOURNAL", "0")
    # Long documents get the full reduction; short ones snapshot more often so
    # a crash can never discard a large fraction of a small job.
    assert core.state_snapshot_cadence(total_batches=3)["every_batches"] == 1
    assert core.state_snapshot_cadence(total_batches=25)["every_batches"] == 3
    assert core.state_snapshot_cadence(total_batches=250)["every_batches"] == 8
    assert core.state_snapshot_cadence(total_batches=1250)["every_batches"] == 8
    assert core.state_snapshot_cadence(total_batches=250)["journal_bytes"] == 0

    # Hard cap on unsnapshotted work -- never "tens of minutes".
    assert core.state_snapshot_crash_window(batch_size=4, total_batches=25) == 12
    assert core.state_snapshot_crash_window(batch_size=4, total_batches=1250) == 32


def test_snapshot_count_follows_the_cadence(temp_job_env: Path, monkeypatch):
    monkeypatch.setattr(core, "call_llm", _fake_llm)
    # 60 segments / batch_size 2 = 30 batches.  The 10%-of-document cap forces
    # the cadence down to 3 for this size, so a crash can lose at most 6
    # segments -- and that cap must win over the configured value.
    paragraphs = [f"Segment {i} is a sentence." for i in range(60)]

    observed = {}
    real_save = core.save_job_state
    for every, expected in ((1, 30), (8, 10)):
        monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", str(every))
        monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", "3600")
        job_id = f"cadence-{every}"
        # Seed the job before installing the counter so only the snapshots
        # taken by the translation itself are counted.
        seeded = core.new_job_state("doc.txt")
        seeded["paras"] = list(paragraphs)
        core.save_job_state(job_id, seeded)

        calls = []

        def counting(_job_id, _state, _calls=calls):
            _calls.append(_job_id)
            return real_save(_job_id, _state)

        monkeypatch.setattr(core, "save_job_state", counting)
        state = core.translate_stage(
            seeded, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=True, use_tm=True, batch_size=2,
            translation_concurrency=1)
        monkeypatch.setattr(core, "save_job_state", real_save)
        observed[every] = len(calls)
        assert state["batch_plan"]["snapshot_cadence"]["every_batches"] == min(
            every, 3), "the document-fraction cap must bound the configured cadence"
        assert len(calls) == expected, (
            f"cadence every={every} over 30 batches: expected {expected} "
            f"snapshots, saw {len(calls)}")

    assert observed[1] > observed[8], "a larger cadence must write fewer snapshots"


def test_snapshot_cadence_declares_its_window_in_the_batch_plan(
        temp_job_env: Path, monkeypatch):
    monkeypatch.setattr(core, "call_llm", _fake_llm)
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "4")
    # 100 segments / batch_size 2 = 50 batches -> the 10% cap (5) does not bind.
    paragraphs = [f"Segment {i} is a sentence." for i in range(100)]
    state = _run_translation("cadence-plan", paragraphs, batch_size=2)

    plan = state["batch_plan"]["snapshot_cadence"]
    assert plan["every_batches"] == 4
    assert plan["crash_window_segments"] == 8
    assert plan["max_interval_s"] >= 0.0


# --------------------------------------------------------------------------
# §7 mandatory immediate snapshots
# --------------------------------------------------------------------------
def test_cancellation_flushes_the_pending_window(temp_job_env: Path, monkeypatch):
    """A cancelled run must persist every batch it already committed."""
    monkeypatch.setattr(core, "call_llm", _fake_llm)
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "8")
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", "3600")

    cancel = {"flag": False}
    monkeypatch.setattr(core, "_runtime_cancel_requested",
                        lambda job_id: cancel["flag"])
    def cancelling_llm(*args, **kwargs):
        result = _fake_llm(*args, **kwargs)
        if len(core.load_job_state("cancelled-run").get("pairs") or []) >= 2:
            cancel["flag"] = True
        return result

    monkeypatch.setattr(core, "call_llm", cancelling_llm)
    paragraphs = [f"Segment {i} is a sentence." for i in range(20)]
    job_id = "cancelled-run"
    state = core.new_job_state("doc.txt")
    state["paras"] = paragraphs
    core.save_job_state(job_id, state)

    with pytest.raises(RuntimeError):
        core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=True, use_tm=True, batch_size=2,
            translation_concurrency=1)

    persisted = core.load_job_state(job_id)
    assert len(persisted["pairs"]) == 4, (
        "the two committed batches (2 x 2 segments) must be on disk")
    assert persisted["pairs"][0]["source"] == paragraphs[0]

    # Resume must recompute only what was never committed.
    resume_state = core.load_job_state(job_id)
    cancel["flag"] = False
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda job_id: False)
    resumed = core.translate_stage(
        resume_state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang="简体中文", style_rules="",
        enable_review=True, use_tm=True, batch_size=2,
        translation_concurrency=1)
    assert len(resumed["pairs"]) == 20
    assert [pair["source"] for pair in resumed["pairs"]] == paragraphs


def test_failure_inside_the_batch_loop_still_snapshots(temp_job_env: Path,
                                                       monkeypatch):
    """A fatal error must not take the committed batches down with it."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "8")
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", "3600")

    calls = {"n": 0}

    def flaky(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
        if "独立的翻译审校专家" in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []},
                              ensure_ascii=False)
        calls["n"] += 1
        # Fail from the third call onward, including the degraded single-segment
        # retry, so the batch genuinely aborts instead of recovering.
        if calls["n"] >= 3:
            raise RuntimeError("provider exploded")
        lines = [line.strip() for line in user_prompt.splitlines()
                 if line.strip() and line[0].isdigit()]
        return json.dumps([f"译文：{line}" for line in lines], ensure_ascii=False)

    monkeypatch.setattr(core, "call_llm", flaky)
    paragraphs = [f"Segment {i} is a sentence." for i in range(12)]
    job_id = "failing-run"
    state = core.new_job_state("doc.txt")
    state["paras"] = paragraphs
    core.save_job_state(job_id, state)

    with pytest.raises(Exception):
        core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=True, use_tm=True, batch_size=2,
            translation_concurrency=1)

    persisted = core.load_job_state(job_id)
    assert persisted is not None
    assert len(persisted["pairs"]) >= 2, (
        "batches committed before the failure must be durable")
    assert len(persisted["pairs"]) % 2 == 0


# --------------------------------------------------------------------------
# §5 save-path syscall hygiene
# --------------------------------------------------------------------------
def test_save_recovers_when_the_job_directory_vanishes(temp_job_env: Path):
    """The cached "directory exists" hint must never turn into a failed save."""
    job_id = "vanishing"
    state = core.new_job_state("v.docx")
    core.save_job_state(job_id, state)
    assert core.job_state_path(job_id).is_file()

    shutil.rmtree(core.job_dir(job_id))
    core.save_job_state(job_id, state)
    assert core.job_state_path(job_id).is_file()
    assert core.load_job_state(job_id)["filename"] == "v.docx"


def test_delivery_snapshot_scan_is_skipped_until_delivery_is_final(
        temp_job_env: Path, monkeypatch):
    scans = []

    def boom(_job_root):
        scans.append(_job_root)
        raise AssertionError(
            "the delivery snapshot directory must not be scanned for a "
            "non-final job")

    monkeypatch.setattr(core._snapshots, "latest_snapshot", boom)
    state = core.new_job_state("draft.docx")
    state["pairs"] = [{"source": "S", "target": "T"}]
    core.save_job_state("draft-job", state)
    assert scans == []
    assert state.get("delivery_status") != "final"

    # Positive control: a final delivery must still verify its snapshot.
    monkeypatch.setattr(core._snapshots, "latest_snapshot",
                        lambda job_root: (scans.append(job_root), None)[1])
    final_state = core.new_job_state("final.docx")
    final_state["delivery_status"] = "final"
    core.save_job_state("final-job", final_state)
    assert scans, "a final delivery must still be reconciled against its snapshot"


# --------------------------------------------------------------------------
# §17 translation truth is not a persistence concern
# --------------------------------------------------------------------------
def test_persistence_work_does_not_change_translation_truth(tmp_path, monkeypatch):
    """Profiling and snapshot cadence must be invisible in the translation."""
    monkeypatch.setattr(core, "call_llm", _fake_llm)
    paragraphs = [f"Segment {i} is a sentence." for i in range(12)]

    old_output = core.OUTPUT_DIR
    results = {}
    try:
        for label, env in (("plain", "1"), ("coalesced", "8")):
            workdir = tmp_path / label
            workdir.mkdir()
            core.OUTPUT_DIR = workdir
            repo.invalidate_cache()
            monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", env)
            monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", "3600")
            persistence_profiler.reset()
            persistence_profiler.enable()
            state = _run_translation(f"truth-{label}", paragraphs, batch_size=2)
            persistence_profiler.disable()
            persisted = core.load_job_state(f"truth-{label}")
            results[label] = {
                "digest": _truth_digest(persisted),
                "version": (persisted.get("translation_truth") or {}).get("version"),
                "glossary_hash": (persisted.get("glossary_frozen") or {}).get(
                    "glossary_hash"),
                "pairs": len(persisted["pairs"]),
                "reviewed": sum(1 for p in persisted["pairs"] if p.get("reviewed")),
            }
    finally:
        core.OUTPUT_DIR = old_output
        repo.invalidate_cache()

    assert results["plain"]["digest"] == results["coalesced"]["digest"]
    assert results["plain"]["version"] == results["coalesced"]["version"]
    assert results["plain"]["pairs"] == results["coalesced"]["pairs"] == 12
    assert results["plain"]["reviewed"] == results["coalesced"]["reviewed"]


def test_resume_after_completion_recomputes_nothing(temp_job_env: Path,
                                                    monkeypatch):
    monkeypatch.setattr(core, "call_llm", _fake_llm)
    paragraphs = [f"Segment {i} is a sentence." for i in range(12)]
    state = _run_translation("resume-complete", paragraphs, batch_size=2)
    assert len(state["pairs"]) == 12

    calls = {"n": 0}

    def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return _fake_llm(*args, **kwargs)

    monkeypatch.setattr(core, "call_llm", counting_llm)
    resumed = core.translate_stage(
        core.load_job_state("resume-complete"), "resume-complete", glossary=[],
        provider="DeepSeek", api_key="k", model="m", target_lang="简体中文",
        style_rules="", enable_review=True, use_tm=True, batch_size=2,
        translation_concurrency=1)
    assert calls["n"] == 0, "a completed run must not re-request any batch"
    assert len(resumed["pairs"]) == 12


# --------------------------------------------------------------------------
# §3/§18 the profiler and the invariants that replace wall-clock gates
# --------------------------------------------------------------------------
def test_persistence_profiler_is_off_by_default(temp_job_env: Path):
    persistence_profiler.disable()
    persistence_profiler.reset()
    assert persistence_profiler.profiler().enabled is False
    with persistence_profiler.phase("json_serialization") as sink:
        sink.serialized(999)
    assert persistence_profiler.snapshot()["save_calls"] == 0
    assert persistence_profiler.snapshot()["phases"]["json_serialization"][
        "call_count"] == 0, "a disabled profiler must record nothing at all"


def test_persistence_profiler_records_every_named_save_phase(temp_job_env: Path):
    persistence_profiler.reset()
    persistence_profiler.enable()
    state = core.new_job_state("profiled.docx")
    state["pairs"] = [{"source": "S", "target": "T"}]
    core.save_job_state("profiled-job", state)
    snapshot = persistence_profiler.snapshot()
    persistence_profiler.disable()

    assert snapshot["save_calls"] == 1
    for name in persistence_profiler.SAVE_PHASES:
        record = snapshot["phases"][name]
        assert record["call_count"] == 1, name
        assert record["total_wall_s"] >= 0.0
    serialization = snapshot["phases"]["json_serialization"]
    assert serialization["bytes_serialized"] > 0
    assert snapshot["phases"]["state_file_write"]["bytes_written"] == \
        serialization["bytes_serialized"]
    assert snapshot["phases"]["atomic_replace"]["call_count"] == 1


def test_persistence_invariants_are_counts_not_wall_clock(temp_job_env: Path):
    """§18: the stable signals are counts and bytes, not elapsed seconds."""
    persistence_profiler.reset()
    persistence_profiler.enable()
    job_id = "invariant-job"
    state = core.new_job_state("i.docx")
    for count in (1, 2, 3):
        state["pairs"] = [{"source": f"S{i}", "target": f"T{i}"}
                          for i in range(count)]
        core.save_job_state(job_id, state)
    snapshot = persistence_profiler.snapshot()
    persistence_profiler.disable()

    assert snapshot["save_calls"] == 3
    assert snapshot["phases"]["json_serialization"]["call_count"] == 3
    assert snapshot["phases"]["state_file_write"]["call_count"] == 3
    assert snapshot["phases"]["summary_write"]["call_count"] == 3
    assert snapshot["phases"]["atomic_replace"]["call_count"] == 3

    final_bytes = core.job_state_path(job_id).stat().st_size
    cumulative = snapshot["phases"]["state_file_write"]["bytes_written"]
    # The snapshot count is the stable signal.  Bytes are bounded by it because
    # each snapshot is at most the final (largest) one.
    assert cumulative >= final_bytes
    assert cumulative <= 3 * final_bytes, (
        "three snapshots cannot write more than three times the final payload")
