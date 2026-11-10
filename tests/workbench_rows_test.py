"""Row model and incremental word-count tests (issue #324, A1 P1).

The row model is the read-side counterpart of one delta: it applies only the
segments a delta names and keeps the whole-document counters correct without
walking the book.  ``DocumentCountIndex`` is the piece that replaces the
116 ms ``word_count.document_count`` pass at 10k segments (measured on the
development machine; the number is a property of the old code, not a promise
of this module).

Pure logic, no Qt.
"""
from __future__ import annotations

import random
import time

import pytest

from folith import state_feed
from folith import word_count
from folith import workbench_rows


def _pair(uid: str, source: str, target: str = "", **fields) -> dict:
    pair = {"segment_uid": uid, "source": source, "target": target,
            "reviewed": False, "review_status": "not_reviewed",
            "target_provenance": "generated", "from_tm": False,
            "human_edited": False, "excluded": False}
    pair.update(fields)
    return pair


def _state(pairs: list[dict], paras: list[str] | None = None) -> dict:
    return {"pairs": pairs, "paras": paras if paras is not None else [p["source"] for p in pairs]}


def _delta(*segments: _pair, **kwargs) -> "state_feed.DeltaBatch":
    changes = tuple(
        state_feed.SegmentChange(index=index, segment_uid=pair.get("segment_uid") or "",
                                 pair=pair, fields=tuple(kwargs.get("fields", ())))
        for index, pair in enumerate(segments))
    return state_feed.DeltaBatch(job_id="job1", from_seq=1, seq=1, batch=0,
                                 batches=(0,), offset=0, segments=changes,
                                 domains=("rows", "counts"))


# ---------------------------------------------------------------------------
# row construction
# ---------------------------------------------------------------------------
def test_rows_from_a_translated_state():
    pairs = [_pair("u0", "Hello", "你好"),
             _pair("u1", "World", "", reviewed=False),
             _pair("u2", "Ignored", "忽略", excluded=True)]
    rows = workbench_rows.rows_from_state(_state(pairs))

    assert [row.segment_uid for row in rows] == ["u0", "u1", "u2"]
    assert rows[0].status == workbench_rows.STATUS_PENDING_REVIEW
    assert rows[1].status == workbench_rows.STATUS_UNTRANSLATED
    assert rows[2].status == workbench_rows.STATUS_EXCLUDED
    assert rows[2].excluded is True
    assert rows[0].source_preview == "Hello"


def test_rows_from_paragraphs_before_translation():
    state = {"pairs": [], "paras": ["First", {"text": "Second"}]}
    rows = workbench_rows.rows_from_state(state)
    assert [row.source for row in rows] == ["First", "Second"]
    assert all(row.segment_uid == str(index) for index, row in enumerate(rows))
    assert all(row.status == workbench_rows.STATUS_UNTRANSLATED for row in rows)


def test_draft_overlay_marks_stale_drafts():
    pair = _pair("u0", "Hello", "新译文")
    drafts = {"u0": {"text": "草稿", "baseline": "旧译文"}}
    row = workbench_rows.row_from_pair(0, pair, drafts.get("u0"))
    assert row.has_draft is True
    assert row.draft_text == "草稿"
    assert row.stale_draft is True

    fresh = workbench_rows.row_from_pair(0, pair, {"text": "草稿", "baseline": "新译文"})
    assert fresh.stale_draft is False


def test_status_code_vocabulary_is_shared_with_the_ui():
    assert workbench_rows.pair_status_code(_pair("u", "s", "t")) == \
        workbench_rows.STATUS_PENDING_REVIEW
    assert workbench_rows.pair_status_code(
        _pair("u", "s", "t", reviewed=True)) == workbench_rows.STATUS_REVIEWED
    assert workbench_rows.pair_status_code(
        _pair("u", "s", "t", human_edited=True)) == workbench_rows.STATUS_HUMAN_EDITED


# ---------------------------------------------------------------------------
# read model: apply
# ---------------------------------------------------------------------------
def test_read_model_applies_a_batch_and_appends_rows():
    state = _state([_pair("u0", "A", "甲"), _pair("u1", "B", "乙")])
    model = workbench_rows.WorkbenchReadModel()
    model.reset_from(state)

    delta = state_feed.DeltaBatch(
        job_id="job1", from_seq=1, seq=1, batch=0, batches=(0,), offset=2,
        segments=(state_feed.SegmentChange(
            index=2, segment_uid="u2", pair=_pair("u2", "C", "丙"),
            fields=("target",)),),
        domains=("rows", "counts"))
    applied = model.apply(delta)

    assert applied.positions == (2,)
    assert model.rows[2].target == "丙"
    assert len(model.rows) == 3
    assert model.counts.as_document_count() == word_count.document_count(model.rows)


def test_read_model_updates_existing_rows_without_touching_others():
    pairs = [_pair("u0", "A", "甲"), _pair("u1", "B", "乙")]
    model = workbench_rows.WorkbenchReadModel()
    model.reset_from(_state(pairs))
    before_second = model.rows[1]

    delta = state_feed.DeltaBatch(
        job_id="job1", from_seq=1, seq=1, batch=1, batches=(1,), offset=0,
        segments=(state_feed.SegmentChange(
            index=0, segment_uid="u0",
            pair=_pair("u0", "A", "甲改", human_edited=True),
            fields=("target", "human_edited")),))
    applied = model.apply(delta)

    assert applied.positions == (0,)
    assert model.rows[0].target == "甲改"
    assert model.rows[0].human_edited is True
    assert model.rows[1] is before_second
    assert model.counts.as_document_count() == word_count.document_count(model.rows)


def test_non_append_offset_outside_the_book_requires_a_resync():
    model = workbench_rows.WorkbenchReadModel()
    model.reset_from(_state([_pair("u0", "A", "甲")]))
    delta = state_feed.DeltaBatch(
        job_id="job1", from_seq=1, seq=1, batch=0, batches=(0,), offset=5,
        segments=(state_feed.SegmentChange(index=5, segment_uid="u5",
                                           pair=_pair("u5", "X", "Y")),))
    with pytest.raises(workbench_rows.NeedResync):
        model.apply(delta)


def test_uid_mismatch_requires_a_resync():
    model = workbench_rows.WorkbenchReadModel()
    model.reset_from(_state([_pair("u0", "A", "甲")]))
    delta = state_feed.DeltaBatch(
        job_id="job1", from_seq=1, seq=1, batch=0, batches=(0,), offset=0,
        segments=(state_feed.SegmentChange(index=0, segment_uid="u9",
                                           pair=_pair("u9", "A", "乙")),))
    with pytest.raises(workbench_rows.NeedResync):
        model.apply(delta)


# ---------------------------------------------------------------------------
# DocumentCountIndex
# ---------------------------------------------------------------------------
def test_incremental_counts_equal_a_full_recount_on_a_long_book():
    rng = random.Random(7)
    pairs = [_pair(f"u{i}", f"Source paragraph {i}. " + "lorem ipsum dolor " * (i % 4),
                   (f"译文{i}" if i % 3 else ""))
             for i in range(1000)]
    rows = workbench_rows.rows_from_state(_state(pairs))
    index = word_count.DocumentCountIndex(rows)
    assert index.as_document_count() == word_count.document_count(rows)

    for _ in range(300):
        position = rng.randrange(len(rows))
        old = rows[position]
        new = workbench_rows.WorkbenchRow(
            **{**old.__dict__,
               "target": old.target + "补" if old.target else f"新译{position}",
               "reviewed": rng.random() < 0.4,
               "human_edited": rng.random() < 0.1})
        rows[position] = new
        index.update(position, new)
        assert index.as_document_count() == word_count.document_count(rows)


def test_incremental_counts_handle_repetitions_and_exclusions_on_append():
    index = word_count.DocumentCountIndex()
    rows = []
    for i, source in enumerate(["same", "other", "same", "same"]):
        row = workbench_rows.WorkbenchRow(
            index=i, segment_uid=f"u{i}", source=source, target="",
            status=workbench_rows.STATUS_UNTRANSLATED, reviewed=False,
            human_edited=False, excluded=(i == 3))
        rows.append(row)
        index.append(row)
        assert index.as_document_count() == word_count.document_count(rows)


def test_changing_a_source_text_requires_a_full_reset():
    rows = workbench_rows.rows_from_state(_state([_pair("u0", "A", "甲")]))
    index = word_count.DocumentCountIndex(rows)
    changed = workbench_rows.WorkbenchRow(
        **{**rows[0].__dict__, "source": "B"})
    with pytest.raises(ValueError):
        index.update(0, changed)


def test_incremental_update_is_cheaper_than_a_full_recount(capsys):
    """Measured on this machine; the numbers are reported, not asserted as a gate."""
    pairs = [_pair(f"u{i}", "Source paragraph text with several words. " * (1 + i % 3),
                   f"译文{i}" if i % 2 else "")
             for i in range(10000)]
    rows = workbench_rows.rows_from_state(_state(pairs))

    started = time.perf_counter()
    word_count.document_count(rows)
    full_ms = (time.perf_counter() - started) * 1000

    index = word_count.DocumentCountIndex(rows)
    started = time.perf_counter()
    for position in range(0, len(rows), 10):
        old = rows[position]
        new = workbench_rows.WorkbenchRow(
            **{**old.__dict__, "target": old.target or f"新译{position}",
               "reviewed": True})
        rows[position] = new
        index.update(position, new)
    incremental_ms = (time.perf_counter() - started) * 1000
    assert index.as_document_count() == word_count.document_count(rows)
    # One incremental batch update (the hot path) is 4 rows, not 1000.
    print(f"\nword_count: full recount={full_ms:.1f} ms, "
          f"1000 incremental updates={incremental_ms:.1f} ms")
    assert incremental_ms < full_ms
