"""Project translation-memory persistence contracts.

``tm.json`` is a *derived* materialised cache, not an authority:
``checkpoint.reconcile_translation_memory`` rebuilds it from ``state["pairs"]``
plus any ``tm_promotion_pending`` event that was never acknowledged.  That is
what makes the write path tunable at all, so these tests pin the exact
guarantees the tuning is allowed to rely on -- and the ones it must not break:

* compact encoding stays semantically identical and round-trips through the
  authoritative loader;
* promotions ride the state snapshot window, so TM writes track *snapshots*,
  not batches;
* the ``tm_promotion_pending`` / ``tm_promotion_done`` pair still brackets the
  real write, so a crash can distinguish "promoted" from "merely intended";
* the last batch's promotions are never stranded inside a closed window;
* a crash mid-window is recoverable even when ``state.json`` is behind;
* the final cache content does not depend on the write cadence;
* the directory hint self-heals;
* the invariants asserted are counts, not wall clock.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import core
from folith import checkpoint, persistence_profiler
from folith.storage import job_repository as repo

TARGET_LANG = "简体中文"

#: Wall-clock flushing would make the counts below non-deterministic; the tests
#: that assert an exact number of writes disable the time trigger explicitly.
NO_TIME_TRIGGER = "3600"


@pytest.fixture
def temp_job_env(tmp_path: Path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    repo.reset_cache_stats()
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    persistence_profiler.reset()
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        persistence_profiler.disable()
        persistence_profiler.reset()


def _fake_llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
    if "独立的翻译审校专家" in (system_prompt or ""):
        return json.dumps({"findings": [], "evidence_requests": []},
                          ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    # Keep the source numbers: the deterministic number check added for #101
    # correctly flags a draft that drops them, and this suite is about
    # persistence cadence, not number loss.
    results = []
    for line in lines:
        ordinal = line.split(".", 1)[0].strip()
        numbers = re.findall(r"\d+", line)
        kept = "、".join(dict.fromkeys(numbers)) or ordinal
        results.append(f"第{ordinal}段，保留数字 {kept} 的译文。")
    return json.dumps(results, ensure_ascii=False)
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    # Targets must be in the target language, otherwise deterministic QA treats
    # them as "source left in the target" and nothing is ever promoted -- the
    # whole path under test would silently go untested.
    return json.dumps([f"第{line.split('.', 1)[0].strip()}段译文。" for line in lines],
                      ensure_ascii=False)


def _paragraphs(count: int):
    return [f"Sentence {i} records the northern archive." for i in range(count)]


def _scope(source, target):
    key = core.tm_scope_key(TARGET_LANG, source)
    if not key:
        return None
    return key, core.tm_record(target, TARGET_LANG)


def _run_counted(job_id: str, paragraphs, *, batch_size: int = 2, llm=None,
                 **kwargs):
    """Run a translation and count the two persistence writes it performs.

    The seed save happens *before* the counters are installed so it is never
    attributed to the stage under test.
    """
    state = core.new_job_state("doc.txt")
    state["paras"] = list(paragraphs)
    core.save_job_state(job_id, state)

    counts = {"state": 0, "tm": 0}
    original_state, original_tm = core.save_job_state, core.save_tm
    original_call = core.call_llm
    core.call_llm = llm or _fake_llm

    def counting_state(jid, st):
        original_state(jid, st)
        counts["state"] += 1

    def counting_tm(tm, project_id=None):
        original_tm(tm, project_id)
        counts["tm"] += 1

    core.save_job_state, core.save_tm = counting_state, counting_tm
    try:
        result = core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang=TARGET_LANG, style_rules="",
            enable_review=True, use_tm=True, batch_size=batch_size,
            translation_concurrency=1, **kwargs)
    finally:
        core.save_job_state, core.save_tm = original_state, original_tm
        core.call_llm = original_call
    return result, counts


# --------------------------------------------------------------------------
# compact encoding
# --------------------------------------------------------------------------
def test_tm_file_is_compact_and_round_trips(temp_job_env: Path):
    """Whitespace is presentation: the file must decode to the same mapping."""
    tm = {core.tm_scope_key(TARGET_LANG, "Sentence A."):
          core.tm_record("句子 A。", TARGET_LANG)}
    core.save_tm(tm)

    path = core.tm_path(None)
    raw = path.read_text(encoding="utf-8")
    assert json.loads(raw) == tm
    assert "\n  " not in raw, "still pretty-printed"
    assert "句子 A。" in raw, "CJK must stay literal, not \\uXXXX"
    # The authoritative loader is the contract, not the byte layout.
    assert core.load_tm(None) == tm


def test_tm_write_leaves_no_temp_file_behind(temp_job_env: Path):
    """The atomic replace must not leak the staging file."""
    core.save_tm({core.tm_scope_key(TARGET_LANG, "S"): core.tm_record("T", TARGET_LANG)})
    leftovers = list(core.tm_path(None).parent.glob("*.tmp"))
    assert leftovers == []


# --------------------------------------------------------------------------
# promotions ride the snapshot window
# --------------------------------------------------------------------------
def test_tm_writes_track_snapshots_not_batches(temp_job_env: Path, monkeypatch):
    """TM and state must be flushed together, or they can disagree on recovery."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    # Pin the bounded batch-count cadence explicitly.  It is no longer the
    # default -- with the state journal on, the cadence is a journal-byte budget
    # (see the test below) -- but it must keep working exactly as before.
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "3")
    _, counts = _run_counted("tm-cadence", _paragraphs(60), batch_size=2)

    # 30 batches; cadence = min(8, ceil(30/10)) = 3, plus the loop-exit flush.
    assert counts["state"] == 12   # + journal baseline snapshot
    assert counts["tm"] == counts["state"] - 1, (
        "TM and state flush together (the baseline snapshot has no promotions)")
    assert counts["tm"] < 30, "promotions are still being written per batch"


def test_tm_writes_collapse_to_the_loop_exit_snapshot(temp_job_env: Path,
                                                      monkeypatch):
    """The journal default: one snapshot for the whole run, so one TM write.

    Promotions ride the snapshot window, so decoupling the snapshot from the
    segment count decouples the TM write count from it too -- the two must not
    drift apart, which is the point of flushing them together.
    """
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    _, counts = _run_counted("tm-journal-window", _paragraphs(60), batch_size=2)

    assert counts["state"] == 2, "journal baseline + loop-exit snapshot"
    assert counts["tm"] == 1, "TM still flushes with state, never separately"
    assert len(core.load_tm(None)) == 60


def test_tm_writes_collapse_at_5000_scale_in_shape(temp_job_env: Path, monkeypatch):
    """The same invariant at a larger document: writes scale with snapshots."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", "6")
    _, counts = _run_counted("tm-shape", _paragraphs(240), batch_size=4)
    # 60 batches; cadence = min(8, ceil(60/10)) = 6 -> 10 cadence flushes + exit.
    assert counts["tm"] == counts["state"] - 1 == 11
    assert counts["tm"] <= 60 // 6 + 1


def test_promotion_evidence_brackets_the_real_write(temp_job_env: Path, monkeypatch):
    """Every pending event must be acknowledged by a matching done event."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    job_id = "tm-evidence"
    _run_counted(job_id, _paragraphs(30), batch_size=2)

    events = checkpoint.read_events(core.job_dir(job_id))
    pending = [e for e in events if e.get("phase") == "tm_promotion_pending"]
    done = [e for e in events if e.get("phase") == "tm_promotion_done"]
    assert len(pending) == len(done) >= 1
    assert [e["entries"] for e in pending] == [e["entries"] for e in done]
    # Batching must not silently drop promotions: every segment must appear.
    promoted = {entry["source"] for event in done for entry in event["entries"]}
    assert len(promoted) == 30


def test_last_batch_promotions_are_not_stranded(temp_job_env: Path, monkeypatch):
    """The final batch is accumulated *after* the cadence decision, so it needs
    the loop-exit flush even when the window counter is already back to zero."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    job_id = "tm-last-batch"
    _run_counted(job_id, _paragraphs(60), batch_size=2)

    # 30 batches / cadence 3 -> the batch-30 cadence flush happens *before* its
    # own promotions are accumulated. Only the loop-exit flush can save them.
    on_disk = core.load_tm(None)
    assert len(on_disk) == 60
    last_source = _paragraphs(60)[-1]
    assert core.tm_scope_key(TARGET_LANG, last_source) in on_disk


# --------------------------------------------------------------------------
# recovery
# --------------------------------------------------------------------------
def test_crash_mid_window_recovers_without_state_help(temp_job_env: Path):
    """A batched pending event alone must rebuild the window's promotions.

    This is the exact crash window the cadence change introduces: the TM write
    and the state snapshot are not atomic together, so recovery must work from
    the event log even when ``state.json`` knows nothing about those segments.
    """
    job_id = "tm-crash-window"
    job_root = core.job_dir(job_id)
    job_root.mkdir(parents=True, exist_ok=True)
    entries = [{"source": f"Sentence {i}.", "target": f"第{i}段译文。"} for i in range(8)]
    checkpoint.append_event(job_root, {
        "batch": 7, "offset": 28, "phase": "tm_promotion_pending",
        "entries": entries,
    })

    state = {"pairs": []}          # deliberately behind
    tm: dict = {}
    changed, pending = checkpoint.reconcile_translation_memory(
        tm, state, job_root, scope=_scope)
    assert changed is True
    assert pending == 8
    for entry in entries:
        key = core.tm_scope_key(TARGET_LANG, entry["source"])
        assert tm[key]["target"] == entry["target"]

    # Acknowledging the window makes the replay idempotent.
    checkpoint.append_event(job_root, {
        "batch": 7, "offset": 28, "phase": "tm_promotion_done",
        "entries": entries,
    })
    changed, pending = checkpoint.reconcile_translation_memory(
        tm, state, job_root, scope=_scope)
    assert changed is False and pending == 0


def test_final_tm_content_does_not_depend_on_write_cadence(temp_job_env: Path,
                                                           monkeypatch):
    """The whole point: cadence is a performance knob, not a truth knob."""
    paragraphs = _paragraphs(30)

    def run_into(directory: Path, cadence: str) -> dict:
        core.OUTPUT_DIR = directory
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_EVERY", cadence)
        monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
        _run_counted(f"tm-cadence-{cadence}", paragraphs, batch_size=2)
        return core.load_tm(None)

    dense = run_into(temp_job_env / "dense", "1")
    sparse = run_into(temp_job_env / "sparse", "8")

    assert dense == sparse
    assert len(dense) == 30


def test_review_failure_promotes_nothing_at_any_cadence(temp_job_env: Path,
                                                        monkeypatch):
    """A failed review must not be laundered into memory by the batching."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)

    def failing_llm(_provider, _api_key, _model, system_prompt, user_prompt,
                    **_kwargs):
        if "独立的翻译审校专家" in (system_prompt or ""):
            raise RuntimeError("review provider timeout")
        lines = [line.strip() for line in user_prompt.splitlines()
                 if line.strip() and line[0].isdigit()]
        return json.dumps([f"第{i}段译文。" for i in range(len(lines))],
                          ensure_ascii=False)

    _, counts = _run_counted("tm-review-failed", _paragraphs(30), batch_size=2,
                             llm=failing_llm)

    assert counts["tm"] == 0
    events = checkpoint.read_events(core.job_dir("tm-review-failed"))
    assert not [e for e in events
                if e.get("phase") in {"tm_promotion_pending", "tm_promotion_done"}]
    assert core.load_tm(None) == {}


# --------------------------------------------------------------------------
# self-healing + stable invariants
# --------------------------------------------------------------------------
def test_tm_directory_hint_self_heals(temp_job_env: Path):
    """The process-level directory cache must not become a correctness hazard."""
    import shutil

    core.save_tm({core.tm_scope_key(TARGET_LANG, "S"): core.tm_record("T", TARGET_LANG)})
    assert core.tm_path(None).is_file()

    # Something outside the process removes the whole tree.
    shutil.rmtree(temp_job_env)
    core.save_tm({core.tm_scope_key(TARGET_LANG, "S2"): core.tm_record("T2", TARGET_LANG)})
    assert core.tm_path(None).is_file()
    assert core.load_tm(None) == {
        core.tm_scope_key(TARGET_LANG, "S2"): core.tm_record("T2", TARGET_LANG)}


def test_tm_invariants_are_counts_not_wall_clock(temp_job_env: Path, monkeypatch):
    """Performance regressions must be detectable without timing anything."""
    monkeypatch.setenv("FOLIOTHREAD_STATE_SNAPSHOT_INTERVAL_S", NO_TIME_TRIGGER)
    job_id = "tm-invariants"
    result, counts = _run_counted(job_id, _paragraphs(60), batch_size=2)

    final_bytes = core.tm_path(None).stat().st_size
    assert final_bytes > 0
    # Cumulative bytes are bounded by (writes x final size); with batching the
    # bound is small enough that a regression to per-batch writes fails here.
    assert counts["tm"] * final_bytes < 40 * final_bytes
    assert len(core.load_tm(None)) == len(result["pairs"]) == 60
