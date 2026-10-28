"""Knowledge extraction runs off the serial commit path (issue #55).

The auxiliary model round trip only feeds hints to *later* batches, so commits
must not wait for it.  These tests pin the timing win and the contracts that had
to survive the move: same candidates/events as the serial fold, the final batch
is observed, cancel returns promptly, and a resumed run neither loses nor
double-applies an interrupted extraction.
"""
from __future__ import annotations

import json
import re
import threading
import time

import pytest

import core
from folith import knowledge

KNOWLEDGE_MARKER = "翻译流知识抽取器"
TRANSLATION_MARKER = "【待翻译段落（按序号返回等长数组）】"
REVIEW_MARKER = "独立的翻译审校专家"

PARAGRAPHS = [f"Segment number {i} mentions Term{i % 3} and Gadget{i}." for i in range(8)]


def _translate(line):
    number = re.search(r"number (\d+)", line).group(1)
    term = re.search(r"Term\d", line).group(0)
    return f"第{number}段提到{term}和Gadget{number}。"


class Provider:
    def __init__(self, knowledge_delay=0.0, gate=None):
        self.knowledge_delay = knowledge_delay
        self.gate = gate
        self.knowledge_calls = 0
        self._lock = threading.Lock()

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        if KNOWLEDGE_MARKER in (system_prompt or ""):
            with self._lock:
                self.knowledge_calls += 1
            if self.gate is not None:
                self.gate.wait(timeout=30)
            time.sleep(self.knowledge_delay)
            items = []
            for segment_id, source in re.findall(
                    r"segment_id:\s*(\d+)\n原文：(.*)\n译文：", user_prompt):
                term = re.search(r"Term\d", source).group(0)
                items.append({"segment_id": int(segment_id),
                              "source_expression": term,
                              "observed_target": term, "kind": "term"})
            return json.dumps(items, ensure_ascii=False)
        if REVIEW_MARKER in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []})
        tail = user_prompt.split(TRANSLATION_MARKER, 1)[-1]
        lines = re.findall(r"(?m)^\d+\.\s+(.*)$", tail)
        return json.dumps([_translate(line) for line in lines], ensure_ascii=False)


@pytest.fixture
def job_env(tmp_path):
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_output


def _run(job_id, provider, monkeypatch, *, state=None, concurrency=4):
    monkeypatch.setattr(core, "call_llm", provider)
    if state is None:
        state = core.new_job_state("doc.txt")
        state["paras"] = list(PARAGRAPHS)
        core.save_job_state(job_id, state)
    return core.translate_stage(
        state, job_id, [], "DeepSeek", "key", "model", "简体中文", "",
        enable_review=False, use_tm=False, batch_size=1,
        knowledge_feedback_interval=1, use_translation_cache=False,
        use_knowledge_cache=False, translation_concurrency=concurrency)


def _serial_reference(pairs):
    """The old behaviour: fold ``observe_batch`` over the batches, in order."""
    candidates, events = [], []
    provider = Provider()
    for index, pair in enumerate(pairs):
        candidates, batch_events, _ = knowledge.observe_batch(
            [pair["source"]], [pair["target"]], list(PARAGRAPHS), pairs[:index],
            [], index, "DeepSeek", "key", "model",
            existing_candidates=candidates, call_llm=provider,
            segment_ids=[index], observation_provenance="generated_continuity")
        events.extend(batch_events)
    return candidates, events


def _shape(candidates):
    return [(c["source"], c["observed_target"], c["observed_segments"],
             c["occurrences"]) for c in candidates]


def test_commit_path_no_longer_waits_on_the_knowledge_call(job_env, monkeypatch):
    # Block actual knowledge requests until every translation commit is durable.
    # Total wall time also includes snapshot I/O and runner scheduling, so it
    # cannot tell whether a commit waited on the auxiliary provider (#68).
    release = threading.Event()
    knowledge_started = threading.Event()
    all_committed = threading.Event()
    finished = threading.Event()
    provider = Provider()
    committed = []
    results = []
    errors = []
    original_timing = core._batch_timing

    def gated_provider(*args, **kwargs):
        system_prompt = args[3] if len(args) > 3 else kwargs.get("system_prompt", "")
        if KNOWLEDGE_MARKER in (system_prompt or ""):
            knowledge_started.set()
            release.wait()
        return provider(*args, **kwargs)

    def observe_commit(job_id, stage, batch_id, phase, **metadata):
        original_timing(job_id, stage, batch_id, phase, **metadata)
        if stage != "translation" or phase != "commit_finished":
            return
        assert metadata["status"] == "completed"
        assert knowledge_started.wait(timeout=30), "knowledge extraction never started"
        assert provider.knowledge_calls == 0, "knowledge finished before all commits"
        committed.append(batch_id)
        if len(committed) == len(PARAGRAPHS):
            persisted = core.load_job_state(job_id)
            assert [pair["target"] for pair in persisted["pairs"]] == [
                _translate(source) for source in PARAGRAPHS]
            all_committed.set()

    monkeypatch.setattr(core, "_batch_timing", observe_commit)
    def run_translation():
        try:
            results.append(_run("async-timing", gated_provider, monkeypatch))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=run_translation, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 60
        while not all_committed.is_set() and not finished.is_set() and time.monotonic() < deadline:
            all_committed.wait(timeout=0.1)
        completed_before_release = all_committed.is_set()
    finally:
        release.set()
        worker.join(timeout=30)
    assert not worker.is_alive(), "translation did not stop after the knowledge gate was released"
    if errors:
        raise errors[0]
    assert completed_before_release, "commits waited for the blocked knowledge request"
    result = results[0]
    assert committed == list(range(1, len(PARAGRAPHS) + 1))
    assert len(result["pairs"]) == len(PARAGRAPHS)
    assert provider.knowledge_calls == len(PARAGRAPHS)
    assert result["knowledge_feedback_policy"]["pending"] == []


def test_results_match_the_serial_fold(job_env, monkeypatch):
    result = _run("async-equiv", Provider(knowledge_delay=0.05), monkeypatch)
    ref_candidates, ref_events = _serial_reference(result["pairs"])
    assert _shape(result["knowledge_candidates"]) == _shape(ref_candidates)
    assert result["translation_continuity"] == result["knowledge_candidates"]
    got = [(e["type"], e["source"], e["segment_id"])
           for e in result["knowledge_events"] if e["type"] != "extract_failed"]
    want = [(e["type"], e["source"], e["segment_id"]) for e in ref_events]
    assert got == want
    # Events keep the batch that produced them, not the commit that merged them.
    assert [e["batch"] for e in result["knowledge_events"]] == \
        [e["segment_id"] for e in result["knowledge_events"]]


def test_final_batch_is_observed_and_nothing_is_left_pending(job_env, monkeypatch):
    provider = Provider(knowledge_delay=0.2)
    result = _run("async-final", provider, monkeypatch)
    last = len(PARAGRAPHS) - 1
    assert any(last in c["observed_segments"]
               for c in result["knowledge_candidates"])
    policy = result["knowledge_feedback_policy"]
    assert policy["pending"] == []
    assert policy["observed_batches"] == list(range(len(PARAGRAPHS)))
    assert policy["cache_misses"] == len(PARAGRAPHS)
    assert provider.knowledge_calls == len(PARAGRAPHS)


def test_cancel_while_waiting_for_extractions_returns_promptly(job_env, monkeypatch):
    gate = threading.Event()            # extraction never finishes on its own
    cancel = threading.Event()
    monkeypatch.setattr(core, "_runtime_cancel_requested",
                        lambda _job: cancel.is_set())
    threading.Timer(0.8, cancel.set).start()
    started = time.perf_counter()
    try:
        with pytest.raises(RuntimeError, match="请求取消"):
            _run("async-cancel", Provider(gate=gate), monkeypatch)
        assert time.perf_counter() - started < 5
    finally:
        gate.set()
    persisted = core.load_job_state("async-cancel")
    assert persisted["knowledge_feedback_policy"]["pending"], \
        "interrupted extractions must stay recorded for resume"


def test_resume_rederives_pending_extractions_once(job_env, monkeypatch):
    gate = threading.Event()
    cancel = threading.Event()
    monkeypatch.setattr(core, "_runtime_cancel_requested",
                        lambda _job: cancel.is_set())
    threading.Timer(0.8, cancel.set).start()
    try:
        with pytest.raises(RuntimeError):
            _run("async-resume", Provider(gate=gate), monkeypatch)
    finally:
        gate.set()
    interrupted = core.load_job_state("async-resume")
    pending = interrupted["knowledge_feedback_policy"]["pending"]
    assert pending and interrupted["knowledge_candidates"] == []
    committed = len(interrupted["pairs"])
    assert {item["batch"] for item in pending} <= set(range(committed))

    cancel.clear()
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda _job: False)
    resumed = _run("async-resume", Provider(), monkeypatch, state=interrupted)

    assert len(resumed["pairs"]) == len(PARAGRAPHS)
    assert resumed["knowledge_feedback_policy"]["pending"] == []
    ref_candidates, _ = _serial_reference(resumed["pairs"])
    assert _shape(resumed["knowledge_candidates"]) == _shape(ref_candidates)
    for candidate in resumed["knowledge_candidates"]:
        segments = candidate["observed_segments"]
        assert segments == sorted(set(segments))      # no double application
