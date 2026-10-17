"""Manual / whole-book review sends paragraphs in batches, two requests at a time."""
import json
import re
import threading

import pytest

import core
from folith import workflow_errors


def job(tmp_path, monkeypatch, count):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("batch.docx")
    sources = [f"Sentence number {index} is complete." for index in range(count)]
    state.update(paras=sources,
                 pairs=[{"source": source, "target": f"第 {index} 句完整的译文。"}
                        for index, source in enumerate(sources)],
                 p1_done=True, p2_done=True, target_lang="简体中文", use_tm=False)
    core.save_job_state("batch", state)


def ids_in(prompt):
    return [int(value) for value in re.findall(r"^segment_id: (\d+)$", prompt, re.M)]


def review(call, indexes, **kwargs):
    return core.review_translation_segments(
        "batch", list(indexes), "p", "k", "m", "简体中文", call_llm_fn=call, **kwargs)


def checkpoints(tmp_path):
    events = (tmp_path / "batch" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["segment_ids"] for line in events
            if json.loads(line).get("phase") == "semantic_review_done"]


def test_full_review_sends_ten_paragraphs_per_request_and_saves_per_batch(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch, 25)
    batches, progress = [], []
    lock = threading.Lock()

    def call(*args, **kwargs):
        with lock:
            batches.append(ids_in(args[4]))
        return "[]"

    state, result = review(call, range(25),
                           on_progress=lambda done, total: progress.append((done, total)))

    assert sorted(batches) == [list(range(0, 10)), list(range(10, 20)), list(range(20, 25))]
    assert progress == [(10, 25), (20, 25), (25, 25)]
    assert checkpoints(tmp_path) == [list(range(0, 10)), list(range(10, 20)),
                                     list(range(20, 25))]
    assert result["reviewed_segment_ids"] == list(range(25))
    assert all(pair["reviewed"] for pair in core.load_job_state("batch")["pairs"])


def test_batches_follow_consecutive_runs_and_the_character_budget(monkeypatch):
    pairs = [{"source": "a" * 10, "target": "b" * 10} for _ in range(30)]
    def ids(batches):
        return [batch_ids for batch_ids, _ in batches]

    groups = [[0], [1], [2], [5], [6], [20]]
    assert ids(core._manual_review_groups(groups, pairs, 10, 6000)) == \
        [[0, 1, 2], [5, 6], [20]]
    long_pairs = [{"source": "a" * 2000, "target": "b" * 1000} for _ in range(5)]
    groups = [[i] for i in range(5)]
    assert ids(core._manual_review_groups(groups, long_pairs, 10, 6000)) == \
        [[0, 1], [2, 3], [4]]


def test_a_sentence_group_is_never_split_across_batches():
    pairs = [{"source": "a", "target": "b"} for _ in range(12)]
    groups = [[0, 1, 2, 3], [4, 5, 6, 7, 8], [9, 10, 11]]
    batches = core._manual_review_groups(groups, pairs, 10, 6000)
    assert batches == [
        ([0, 1, 2, 3, 4, 5, 6, 7, 8], [[0, 1, 2, 3], [4, 5, 6, 7, 8]]),
        ([9, 10, 11], [[9, 10, 11]]),
    ]


def test_two_requests_are_in_flight_at_once(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch, 20)
    both_waiting = threading.Barrier(2, timeout=5)
    calls = []

    def call(*args, **kwargs):
        calls.append(ids_in(args[4]))
        if len(calls) <= 2:
            both_waiting.wait()     # raises if the second request never starts
        return "[]"

    _, result = review(call, range(20))
    assert len(calls) == 2
    assert result["failed_segment_ids"] == []
    assert result["reviewed_segment_ids"] == list(range(20))


def test_a_failed_batch_is_retried_one_paragraph_at_a_time(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch, 10)
    calls = []

    def call(*args, **kwargs):
        ids = ids_in(args[4])
        calls.append(ids)
        if len(ids) > 1:
            return "not json"           # the whole batch fails to parse
        return "garbage" if ids == [4] else "[]"

    state, result = review(call, range(10))

    assert calls[0] == list(range(10))
    assert calls[1:] == [[index] for index in range(10)]
    assert result["failed_segment_ids"] == [4]
    assert result["reviewed_segment_ids"] == [0, 1, 2, 3, 5, 6, 7, 8, 9]
    saved = core.load_job_state("batch")["pairs"]
    assert saved[4]["review_status"] == "review_failed"
    assert all(saved[index]["reviewed"] for index in range(10) if index != 4)
    # The failed batch itself is not recorded; each retried paragraph is.
    assert checkpoints(tmp_path) == [[index] for index in range(10)]


def test_an_access_failure_stops_the_run_without_per_paragraph_retries(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch, 40)
    calls = []

    def call(*args, **kwargs):
        calls.append(ids_in(args[4]))
        raise RuntimeError("Error code: 401 - invalid_api_key")

    with pytest.raises(workflow_errors.WorkflowStopped):
        review(call, range(40))

    # Only the requests already in flight went out; nothing was retried.
    assert len(calls) <= 2 and all(len(ids) == 10 for ids in calls)
    saved = core.load_job_state("batch")["pairs"]
    assert all(saved[index]["review_status"] == "review_failed" for index in range(10))
    assert not any(pair.get("reviewed") for pair in saved)


def test_findings_stay_with_the_paragraph_they_name(tmp_path, monkeypatch):
    job(tmp_path, monkeypatch, 3)
    finding = {
        "segment_id": 1, "category": "semantic_accuracy", "severity": "actionable",
        "summary": "漏译", "explanation": "译文缺少一个分句", "recommendation": "补全",
        "source_span": None, "target_span": None, "confidence": 0.8,
        "detector": "Semantic QA",
    }

    def call(*args, **kwargs):
        return json.dumps({"findings": [finding], "evidence_requests": []},
                          ensure_ascii=False)

    _, result = review(call, range(3))
    saved = core.load_job_state("batch")["pairs"]
    assert result["reviewed_segment_ids"] == [0, 2]
    assert saved[1]["review_status"] == "reviewed_with_findings"
    assert saved[0]["review_status"] == saved[2]["review_status"] == "reviewed_clean"
