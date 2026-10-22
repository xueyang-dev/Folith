"""The lifecycle probe must reject incomplete replay and lost committed fields."""
import json

import pytest

from folith.storage import state_journal as journal
from scripts.qt_crash_recovery_probe import (
    _assert_committed_prefix_preserved, _durable_state, _resume_finished)


@pytest.mark.parametrize("status", ["running", "waiting_external", "paused", "queued"])
def test_resume_wait_does_not_read_the_book_while_worker_is_active(status):
    from types import SimpleNamespace
    def full_read(_job):
        pytest.fail("active progress polling must not load/copy the full book")
    adapter = SimpleNamespace(
        core=SimpleNamespace(load_runtime_state=lambda _: {"status": status},
                             RUNTIME_ACTIVE_STATUSES={status}), load_state=full_read)
    assert not _resume_finished(adapter, "book")


@pytest.mark.parametrize("done", [False, True])
def test_resume_wait_checks_committed_completion_after_worker_stops(done):
    from types import SimpleNamespace
    adapter = SimpleNamespace(
        core=SimpleNamespace(load_runtime_state=lambda _: {"status": "completed"},
                             RUNTIME_ACTIVE_STATUSES={"running"}),
        load_state=lambda _: {"p2_done": done})
    assert _resume_finished(adapter, "book") is done


def _record(seq, offset):
    return {"schema": journal.JOURNAL_SCHEMA_VERSION,
            "mutation": journal.MUTATION_TRANSLATION_BATCH,
            "seq": seq, "offset": offset,
            "pairs": [{"source": f"source {offset}", "target": f"译文{offset}"}]}


def _seed(tmp_path):
    path = tmp_path / "job" / "state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"pairs": [], "paras": ["one", "two"]}), encoding="utf-8")


def test_probe_accepts_only_complete_records_before_a_torn_tail(tmp_path):
    _seed(tmp_path)
    journal.append_mutation("job", _record(1, 0), output_dir=tmp_path)
    with journal.journal_path("job", tmp_path).open("ab") as stream:
        stream.write(b'{"seq":2')
    state, report = _durable_state(tmp_path, "job")
    assert state["pairs"] == _record(1, 0)["pairs"]
    assert report["applied"] == 1 and report["cursor"] == 1


def test_probe_rejects_a_missing_sequence_even_when_offsets_line_up(tmp_path):
    _seed(tmp_path)
    journal.append_mutation("job", _record(1, 0), output_dir=tmp_path)
    journal.append_mutation("job", _record(3, 1), output_dir=tmp_path)
    with pytest.raises(AssertionError, match="complete ordered suffix"):
        _durable_state(tmp_path, "job")


def test_probe_rejects_a_replay_gap(tmp_path):
    _seed(tmp_path)
    record = _record(1, 0)
    record["extend"] = {"findings": {"base": 234, "items": [{"reason": "gap"}]}}
    journal.append_mutation("job", record, output_dir=tmp_path)
    with pytest.raises(AssertionError, match="durable tail"):
        _durable_state(tmp_path, "job")


@pytest.mark.parametrize("key", ["source", "target", "accepted_target", "reviewed"])
def test_probe_rejects_changes_to_previously_committed_fields(key):
    old = {"source": "source", "target": "译文", "accepted_target": "译文", "reviewed": True}
    changed = dict(old, **{key: "changed"})
    with pytest.raises(AssertionError, match=key):
        _assert_committed_prefix_preserved([old], [changed])


def test_probe_allows_new_derived_fields_without_relaxing_committed_fields():
    old = {"source": "source", "target": "译文"}
    new = dict(old, epub_source_paragraphs=[0])
    assert _assert_committed_prefix_preserved([old], [new]) == ["epub_source_paragraphs"]
