"""Incremental workbench polling (issue #324, A1 P2).

The contract these tests pin down:

* one committed journal record is applied to the grid **without** reading the
  task state, rebuilding rows or recounting the document;
* only the rows the batch names are repainted;
* a sequence gap / external snapshot falls back to the whole-state path;
* ``FOLITH_WORKBENCH_DELTA=0`` is exactly the old behaviour;
* an older read can never replace a newer one (the #92/#94 "stale read" rule).

Fixtures reuse ``qt_client_window_test`` (the real client, offscreen).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "tests"))

from folith.qt.selftest import open_job_and_wait  # noqa: E402

from qt_client_window_test import (  # noqa: E402,F401
    _make_docx, _pump, client, qt_app, )

SEGMENTS = 900


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _book_job(client, segments: int = SEGMENTS) -> str:
    adapter, core, tmp = client["adapter"], client["core"], client["tmp"]
    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["x"]))
    job_id = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id
    state = core.load_job_state(job_id) or {}
    texts = [f"Synthetic paragraph {i}. " + "Lorem ipsum dolor sit amet " * (1 + i % 5)
             for i in range(segments)]
    state["paras"] = texts
    state["pairs"] = [
        {"source": text, "target": f"合成译文{i}" if i < segments // 2 else "",
         "reviewed": False, "segment_uid": f"u{i}"} for i, text in enumerate(texts)]
    core.save_job_state(job_id, state)
    return job_id


def _journal_path(core, job_id: str) -> Path:
    return core._state_journal.journal_path(job_id)


def _append_record(core, job_id: str, *, seq: int, offset: int, pairs: list[dict],
                   batch: int = 0) -> None:
    record = {
        "schema": core._state_journal.JOURNAL_SCHEMA_VERSION,
        "mutation": core._state_journal.MUTATION_TRANSLATION_BATCH,
        "mutation_id": uuid.uuid4().hex,
        "ts": "2026-01-01T00:00:00+00:00",
        "batch": int(batch),
        "offset": int(offset),
        "segment_ids": [pair.get("segment_uid") or f"seg-{offset + i}"
                        for i, pair in enumerate(pairs)],
        "pairs": pairs,
        "seq": int(seq),
    }
    path = _journal_path(core, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))


def _changed_pairs(state: dict, indexes, *, suffix: str = "新提交") -> list[dict]:
    pairs = []
    for index in indexes:
        pair = dict(state["pairs"][index])
        pair["target"] = f"{suffix}{index}"
        pair["reviewed"] = True
        pairs.append(pair)
    return pairs


class _Counters:
    def __init__(self, workbench) -> None:
        self.repainted = 0
        self.height_requests: list = []
        paint, heights = workbench._paint_row, workbench._schedule_row_heights

        def counting_paint(position, row):
            changed = paint(position, row)
            self.repainted += bool(changed)
            return changed

        def counting_heights(*args, **kwargs):
            self.height_requests.append(kwargs.get("positions", "ALL"))
            return heights(*args, **kwargs)

        workbench._paint_row = counting_paint
        workbench._schedule_row_heights = counting_heights


def _settle(client, workbench) -> None:
    app = client["app"]
    assert _pump(app, 20, lambda: workbench.segment_list.rowCount() == SEGMENTS
                 and not workbench._height_timer.isActive()
                 and not workbench._paint_timer.isActive())
    _pump(app, 0.3)


def _open(client) -> str:
    window, app = client["window"], client["app"]
    job_id = _book_job(client)
    open_job_and_wait(window, job_id)
    window.timer.stop()
    _settle(client, window.workbench)
    window.refresh_workbench()
    _settle(client, window.workbench)
    return job_id


# ---------------------------------------------------------------------------
# the fast path
# ---------------------------------------------------------------------------
def test_a_batch_delta_updates_rows_without_reading_the_state(client, monkeypatch):
    window, adapter, core, app = (client["window"], client["adapter"],
                                  client["core"], client["app"])
    job_id = _open(client)
    state = core.load_job_state(job_id)
    workbench = window.workbench
    counters = _Counters(workbench)

    calls = {"load_state": 0, "segment_rows": 0, "document_count": 0}
    original_load, original_rows = adapter.load_state, adapter.segment_rows
    from folith import word_count

    original_count = word_count.document_count
    adapter.load_state = lambda job: (calls.__setitem__("load_state", calls["load_state"] + 1),
                                      original_load(job))[1]
    adapter.segment_rows = lambda job, state=None: (
        calls.__setitem__("segment_rows", calls["segment_rows"] + 1),
        original_rows(job, state))[1]
    word_count.document_count = lambda rows: (
        calls.__setitem__("document_count", calls["document_count"] + 1),
        original_count(rows))[1]

    indexes = list(range(400, 404))
    _append_record(core, job_id, seq=1, offset=indexes[0],
                   pairs=_changed_pairs(state, indexes))

    window._poll_workbench()
    assert _pump(app, 20, lambda: not window._state_load_in_flight
                 and window._delta_applied_seq == 1
                 and workbench.segment_row_text(400, 2) == "新提交400")

    # The counterexample: the delta path must not have touched any of these.
    assert calls == {"load_state": 0, "segment_rows": 0, "document_count": 0}, calls
    # Only the batch's rows were repainted and re-measured.
    assert counters.repainted == 4, counters.repainted
    for request in counters.height_requests:
        assert request != "ALL"
        assert set(request) <= set(range(400, 404)), request
    # The incremental count replaced the full recount and still matches it.
    rows = list(workbench._all_rows)
    assert workbench._word_count == original_count(rows)
    assert workbench.segment_row_text(403, 2) == "新提交403"


def test_only_the_batch_rows_are_repainted_and_no_heights_for_untouched_rows(client):
    window, core, app = client["window"], client["core"], client["app"]
    job_id = _open(client)
    workbench = window.workbench
    state = core.load_job_state(job_id)
    counters = _Counters(workbench)
    indexes = [5, 6, 7]
    _append_record(core, job_id, seq=1, offset=5,
                   pairs=_changed_pairs(state, indexes))
    window._poll_workbench()
    assert _pump(app, 20, lambda: window._delta_applied_seq == 1)
    assert counters.repainted == 3
    assert all(set(request) <= set(indexes) for request in counters.height_requests)


# ---------------------------------------------------------------------------
# fallbacks: gap, external snapshot, env switch
# ---------------------------------------------------------------------------
def test_sequence_gap_falls_back_to_the_whole_state_path(client, monkeypatch):
    window, adapter, core, app = client["window"], client["adapter"], client["core"], client["app"]
    job_id = _open(client)
    state = core.load_job_state(job_id)
    workbench = window.workbench

    full_calls = {"count": 0}
    original_full = window._poll_workbench_full

    def counting_full():
        full_calls["count"] += 1
        return original_full()

    monkeypatch.setattr(window, "_poll_workbench_full", counting_full)
    indexes = [10, 11]
    _append_record(core, job_id, seq=7, offset=10,
                   pairs=_changed_pairs(state, indexes, suffix="断档重建"))

    window._poll_workbench()
    assert _pump(app, 20, lambda: full_calls["count"] >= 1
                 and workbench.segment_row_text(10, 2) == "断档重建10")
    # The full path rebuilt from the authoritative state (journal replay keeps
    # the committed pairs), so no delta is claimed as applied.
    assert window._delta_applied_seq in (0, 7)


def test_external_snapshot_falls_back_to_the_whole_state_path(client, monkeypatch):
    window, core, app = client["window"], client["core"], client["app"]
    job_id = _open(client)
    state = core.load_job_state(job_id)
    state["pairs"][20]["target"] = "落盘后的新译文"
    core.save_job_state(job_id, state)

    full_calls = {"count": 0}
    original_full = window._poll_workbench_full

    def counting_full():
        full_calls["count"] += 1
        return original_full()

    monkeypatch.setattr(window, "_poll_workbench_full", counting_full)
    window._poll_workbench()
    assert _pump(app, 20, lambda: full_calls["count"] >= 1
                 and window.workbench.segment_row_text(20, 2) == "落盘后的新译文")


def test_env_switch_restores_the_old_path_verbatim(client, monkeypatch):
    window, core, app = client["window"], client["core"], client["app"]
    job_id = _open(client)
    monkeypatch.setenv("FOLITH_WORKBENCH_DELTA", "0")
    assert window._delta_ready(job_id) is False

    state = core.load_job_state(job_id)
    state["pairs"][30]["target"] = "开关关掉后的译文"
    core.save_job_state(job_id, state)
    window._poll_workbench()
    assert _pump(app, 20, lambda: window.workbench.segment_row_text(30, 2)
                 == "开关关掉后的译文")


# ---------------------------------------------------------------------------
# stale reads (#92/#94 semantics)
# ---------------------------------------------------------------------------
def test_an_older_delta_can_never_replace_a_newer_one(client):
    window, adapter, core, app = client["window"], client["adapter"], client["core"], client["app"]
    job_id = _open(client)
    state = core.load_job_state(job_id)
    workbench = window.workbench
    _append_record(core, job_id, seq=1, offset=60,
                   pairs=_changed_pairs(state, [60], suffix="批一"))
    _append_record(core, job_id, seq=2, offset=60,
                   pairs=_changed_pairs(state, [60], suffix="批二"))
    window._poll_workbench()
    assert _pump(app, 20, lambda: window._delta_applied_seq == 2
                 and workbench.segment_row_text(60, 2) == "批二60")

    # A delayed older payload must be dropped, not re-applied.
    stale = adapter.DeltaBatch(
        job_id=job_id, from_seq=1, seq=1, batch=0, batches=(0,), offset=60,
        segments=(adapter.DeltaSegment(
            index=60, segment_uid="u60",
            pair={**state["pairs"][60], "target": "过期批一"}, fields=("target",)),))
    window._apply_feed_result(job_id, stale, {}, {})
    assert workbench.segment_row_text(60, 2) == "批二60"
    assert window._delta_applied_seq == 2


# ---------------------------------------------------------------------------
# GUI-thread cost is independent of book length (the acceptance measurement)
# ---------------------------------------------------------------------------
def test_per_batch_gui_cost_does_not_grow_with_the_book(client, capsys):
    """Measure the GUI-thread cost at 1k and 10k segments.

    Two numbers per size:
    * per-batch: the window's GUI-thread part of one committed batch (the
      ``apply_delta`` call: row upsert + repaint + count update), measured with
      the same seam the window uses;
    * one whole rebuild: ``refresh_workbench(force=True)`` on the same job --
      the path the batch used to take every second (state load + rows +
      projections + grid).
    Not a wall-clock gate (CI machines differ); the assertion only requires the
    per-batch number to stay in the same order of magnitude as the book grows
    10x.
    """
    window, adapter, core, app = client["window"], client["adapter"], client["core"], client["app"]
    timings = {}
    rebuild = {}
    for segments in (1000, 10000):
        job_id = _book_job(client, segments)
        # A previous size in this test may have used the same job id (same
        # document bytes); start this measurement from an empty journal.
        _journal_path(core, job_id).unlink(missing_ok=True)
        window._drop_delta_state()
        open_job_and_wait(window, job_id)
        window.timer.stop()
        window.refresh_workbench()
        workbench = window.workbench
        assert _pump(app, 300, lambda: workbench.segment_list.rowCount() == segments
                     and not workbench._paint_timer.isActive())
        state = core.load_job_state(job_id)

        samples = []
        original_apply = workbench.apply_delta

        def timed_apply(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original_apply(*args, **kwargs)
            finally:
                samples.append((time.perf_counter() - started) * 1000)

        workbench.apply_delta = timed_apply
        try:
            for batch in range(20):
                indexes = list(range(batch * 4, batch * 4 + 4))
                _append_record(core, job_id, seq=batch + 1, offset=indexes[0],
                               pairs=_changed_pairs(state, indexes, suffix=f"计时{batch}"))
                window._poll_workbench_delta(job_id)
                assert _pump(app, 60, lambda: window._delta_applied_seq == batch + 1)
        finally:
            workbench.apply_delta = original_apply
        samples.sort()
        timings[segments] = {
            "p50": samples[len(samples) // 2],
            "p95": samples[int(len(samples) * 0.95) - 1],
            "max": samples[-1],
        }

        started = time.perf_counter()
        window.refresh_workbench(force=True)
        rebuild[segments] = (time.perf_counter() - started) * 1000
        window.timer.stop()

    print("\nGUI-thread cost (ms):")
    for segments in (1000, 10000):
        value = timings[segments]
        print(f"  {segments:>6} segments: per-batch apply_delta "
              f"p50={value['p50']:.2f} p95={value['p95']:.2f} max={value['max']:.2f} | "
              f"whole rebuild once={rebuild[segments]:.0f}")
    assert timings[10000]["p50"] < timings[1000]["p50"] * 3 + 5.0, timings
