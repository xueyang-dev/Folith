import threading
import copy
import time

import pytest

import core


def test_annotation_requests_overlap_but_commit_in_order(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    second_done = threading.Event()
    calls, commits = [], []
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": f"Sentence {i}.", "target": f"句子{i}。"} for i in range(4)]
    state["pipeline_config"] = {"translation_concurrency": 4}

    def annotate(pairs, *_args):
        source = pairs[0]["source"]
        calls.append(source)
        assert core._runtime_job_id() == "ordered"
        assert core._usage.CURRENT_ROLE.get() == "annotation"
        if source == "Sentence 0.":
            assert second_done.wait(2), "second request must finish before the first"
        elif source == "Sentence 1.":
            second_done.set()
        return core.AnnotationBatchResult(status="empty")

    monkeypatch.setattr(core, "annotate_batch", annotate)
    monkeypatch.setattr(core, "save_job_state", lambda _job, s: commits.append(s.get("annotations_done_offset")))
    core.annotate_stage(state, "ordered", [], "DeepSeek", "key", "model", "简体中文")
    assert len(calls) == 4
    assert commits == [1, 2, 3, 4, 4]
    assert state["annotations_done"]


def test_annotation_save_failure_does_not_refill_window(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": f"Sentence {i}.", "target": f"句子{i}。"} for i in range(5)]
    calls = []

    def annotate(pairs, *_args):
        calls.append(pairs[0]["source"])
        return core.AnnotationBatchResult(status="empty")

    def save(*_args):
        raise OSError("controlled save failure")

    monkeypatch.setattr(core, "annotate_batch", annotate)
    monkeypatch.setattr(core, "save_job_state", save)
    with pytest.raises(OSError, match="controlled"):
        core.annotate_stage(state, "save-failure", [], "DeepSeek", "key", "model", "简体中文")
    assert len(calls) <= 2


def test_parallel_annotation_matches_serial_global_filters(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    initial = core.new_job_state("sample.docx")
    initial["pairs"] = [{"source": f"Section {i} discusses Urban Air Mobility in Germany.",
                         "target": f"第{i}节讨论德国的城市空中交通。"} for i in range(4)]

    def annotate(pairs, *_args):
        if "Section 0" in pairs[0]["source"]:
            time.sleep(0.02)
        return core.AnnotationBatchResult([
            {"seg": 0, "src": "Urban Air Mobility", "tgt": "城市空中交通", "type": "domain", "note": "专业术语"},
            {"seg": 0, "src": "Germany", "tgt": "德国", "type": "domain", "note": "地名"}])

    monkeypatch.setattr(core, "annotate_batch", annotate)
    monkeypatch.setattr(core, "save_job_state", lambda *_args: None)
    states = []
    for workers in [1, 2]:
        state = copy.deepcopy(initial)
        state["pipeline_config"] = {"translation_concurrency": workers}
        core.annotate_stage(state, f"equivalence-{workers}", [], "DeepSeek", "key", "model", "简体中文")
        states.append(state)
    for key in ["annotations", "annotation_batches", "annotations_done", "annotations_done_offset"]:
        assert states[0][key] == states[1][key]
    assert sum(len(items) for items in states[1]["annotations"].values()) == 2


def test_cancel_after_commit_does_not_start_more_annotation_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": f"Sentence {i}.", "target": f"句子{i}。"} for i in range(5)]
    cancelled = threading.Event()
    calls = []
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda _job: cancelled.is_set())

    def annotate(pairs, *_args):
        calls.append(pairs[0]["source"])
        return core.AnnotationBatchResult(status="empty")

    monkeypatch.setattr(core, "annotate_batch", annotate)
    monkeypatch.setattr(core, "save_job_state", lambda *_args: cancelled.set())
    with pytest.raises(RuntimeError, match="请求取消"):
        core.annotate_stage(state, "cancelled", [], "DeepSeek", "key", "model", "简体中文")
    assert len(calls) <= 2
    assert state["annotations_done_offset"] == 1


def test_resume_retries_failed_batch_and_preserves_completed_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": f"Sentence {i}.", "target": f"句子{i}。"} for i in range(4)]
    state["annotations_done_offset"] = 2
    state["annotation_batches"] = {"0": {"status": "request_failed"}, "1": {"status": "empty"}}
    calls = []
    monkeypatch.setattr(core, "save_job_state", lambda *_args: None)

    def annotate(pairs, *_args):
        calls.append(pairs[0]["source"])
        return core.AnnotationBatchResult(status="empty")

    monkeypatch.setattr(core, "annotate_batch", annotate)
    core.annotate_stage(state, "resume", [], "DeepSeek", "key", "model", "简体中文")
    assert sorted(calls) == ["Sentence 0.", "Sentence 2.", "Sentence 3."]
    assert state["annotations_done_offset"] == 4
    assert state["annotations_done"]
