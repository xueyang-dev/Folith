"""Knowledge-feedback trigger policy contracts.

Knowledge feedback is the only per-batch auxiliary request on the translation
hot path, so its *trigger* is a correctness surface, not a tuning knob.  These
tests pin the behaviour that the pass depends on:

* a batch with no plausible new knowledge is skipped, and the skip is recorded
  as a skip — never as "feedback completed";
* a batch that introduces a name-like form the project does not already hold is
  always observed, even off the checkpoint grid;
* knowledge the project already has never causes a redundant request;
* mandatory mode (explicit interval 1, or strict ``quality_mode``) still runs
  every batch;
* a skip mutates no knowledge state, ordering stays correct, and a resume does
  not re-issue feedback.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import core
from folith import entity_registry, knowledge


TRANSLATION_MARKER = "【待翻译段落（按序号返回等长数组）】"
KNOWLEDGE_MARKER = "翻译流知识抽取器"


def _translations(user_prompt: str) -> str:
    tail = user_prompt.split(TRANSLATION_MARKER, 1)[-1]
    sources = re.findall(r"(?m)^\d+\.\s+(.*)$", tail)
    # Carry the observed target and each source's number: the extractor's
    # claim-checking needs the target, and the deterministic number check
    # (added for #101) correctly flags drafts that drop the source number.
    results = []
    for source in sources:
        numbers = re.findall(r"\d+", source)
        suffix = f" {numbers[-1]}" if numbers else ""
        results.append(f"欧洲空间局译文{suffix}")
    return json.dumps(results, ensure_ascii=False)


class RecordingProvider:
    """Counts knowledge-extraction requests and returns a fixed observation."""

    def __init__(self, observations=None):
        self.knowledge_calls = 0
        self.translation_calls = 0
        self.observations = observations if observations is not None else []

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        if KNOWLEDGE_MARKER in (system_prompt or ""):
            self.knowledge_calls += 1
            return json.dumps(self.observations, ensure_ascii=False)
        if TRANSLATION_MARKER in (user_prompt or ""):
            self.translation_calls += 1
            return _translations(user_prompt)
        return json.dumps([], ensure_ascii=False)


def _run(tmp_path: Path, job_id: str, paragraphs, *, provider=None,
         knowledge_feedback_interval=None, quality_mode=False,
         existing_state=None, state_mutator=None):
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        state = (existing_state if existing_state is not None
                 else core.new_job_state("feedback-policy.docx"))
        state["paras"] = list(paragraphs)
        state["quality_mode"] = quality_mode
        if state_mutator is not None:
            state_mutator(state)
        core.save_job_state(job_id, state)
        recorder = provider or RecordingProvider()
        core.call_llm = recorder
        result = core.translate_stage(
            state, job_id, [], "DeepSeek", "key", "model",
            "简体中文", "", enable_review=False, use_tm=False,
            batch_size=4, knowledge_feedback_interval=knowledge_feedback_interval,
            translation_concurrency=1)
        return result, recorder
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm


PLAIN = [f"这一段没有任何专有名称，只是普通的陈述句，编号 {index}。"
         for index in range(16)]


# ---------------------------------------------------------------------------
# Deterministic signals (no provider involved)
# ---------------------------------------------------------------------------


def test_named_forms_ignore_sentence_initial_capitals():
    forms = knowledge.named_source_forms(
        "The European Space Agency works with NASA. Urban Air Mobility grows.")
    assert "European Space Agency" in forms
    assert "NASA" in forms
    assert "The" not in forms
    assert "Urban" not in forms


def test_signal_fires_only_for_forms_the_project_does_not_hold():
    fresh = knowledge.batch_knowledge_signals(
        ["ESA and NASA cooperate."], known_forms=[])
    assert fresh["has_signal"] is True
    known = knowledge.batch_knowledge_signals(
        ["ESA and NASA cooperate."], known_forms=["esa", "nasa"])
    assert known["has_signal"] is False
    assert knowledge.batch_knowledge_signals(
        ["a plain sentence about nothing."])["has_signal"] is False


def test_should_run_reasons_are_explicit():
    assert knowledge.should_run_knowledge_feedback(
        batch_index=0, batch_count=8, interval=4,
        accepted_count=0) == (False, "no_accepted_segments")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=4, batch_count=8, interval=4,
        accepted_count=4) == (True, "checkpoint")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=1, batch_count=8, interval=4, accepted_count=4,
        sources=["ESA reports."]) == (True, "new_named_entity")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=1, batch_count=8, interval=4, accepted_count=4,
        sources=["plain text only."]) == (False, "no_signal")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=7, batch_count=8, interval=4,
        accepted_count=1) == (True, "final_batch")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=5, batch_count=8, interval=1,
        accepted_count=1) == (True, "mandatory")
    assert knowledge.should_run_knowledge_feedback(
        batch_index=5, batch_count=8, interval=4, accepted_count=1,
        force_every_batch=True) == (True, "mandatory")


# ---------------------------------------------------------------------------
# Pipeline behaviour
# ---------------------------------------------------------------------------


def test_no_op_batches_skip_feedback_and_are_recorded_as_skipped(tmp_path):
    result, recorder = _run(tmp_path, "kf-noop", PLAIN)
    policy = result["knowledge_feedback_policy"]
    assert policy["interval"] == 4
    assert policy["observed_batches"] == [0, 3]
    assert policy["skipped_batches"] == [1, 2]
    assert policy["skip_reasons"] == {"no_signal": 2}
    assert recorder.knowledge_calls == 2
    phases = [
        json.loads(line)["phase"]
        for line in (tmp_path / "kf-noop" / "events.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
    # A skipped batch must never be recorded as a completed feedback call.
    assert phases.count("knowledge_feedback_done") == 2
    assert phases.count("knowledge_feedback_skipped") == 2


def test_new_named_entity_forces_feedback_off_the_checkpoint_grid(tmp_path):
    paragraphs = list(PLAIN)
    # Batch 1 covers paragraphs 4..7 and is neither first, final, nor on the
    # interval-4 checkpoint grid.
    paragraphs[4] = "The European Space Agency coordinates the programme."
    recorder = RecordingProvider([
        {"segment_id": 4, "source_expression": "European Space Agency",
         "observed_target": "欧洲空间局", "kind": "organization"}])
    result, _ = _run(tmp_path, "kf-signal", paragraphs, provider=recorder)
    policy = result["knowledge_feedback_policy"]
    assert 1 in policy["observed_batches"]
    assert 1 in policy["signal_batches"]
    assert result["entity_registry"], "entity observation must reach the registry"
    assert result["entity_registry"][0]["source_form"] == "European Space Agency"


def test_already_known_entity_does_not_cause_redundant_feedback(tmp_path):
    paragraphs = list(PLAIN)
    paragraphs[4] = "The European Space Agency coordinates the programme."

    def seed(state):
        registry = entity_registry.EntityRegistry()
        registry.observe("European Space Agency", "欧洲空间局",
                         entity_type="organization", segment_id=4,
                         provenance="human_accepted", confidence=0.95)
        state["entity_registry"] = registry.to_list()

    result, recorder = _run(tmp_path, "kf-known", paragraphs,
                            state_mutator=seed)
    policy = result["knowledge_feedback_policy"]
    assert 1 not in policy["observed_batches"]
    assert policy["skip_reasons"] == {"no_signal": 2}
    assert recorder.knowledge_calls == 2


def test_mandatory_mode_runs_every_batch(tmp_path):
    result, recorder = _run(tmp_path, "kf-mandatory", PLAIN,
                            knowledge_feedback_interval=1)
    policy = result["knowledge_feedback_policy"]
    assert policy["force_every_batch"] is True
    assert policy["skipped_batches"] == []
    assert policy["observed_batches"] == list(range(4))
    assert recorder.knowledge_calls == 4


def test_strict_quality_mode_stays_mandatory(tmp_path):
    result, recorder = _run(tmp_path, "kf-strict", PLAIN, quality_mode=True)
    policy = result["knowledge_feedback_policy"]
    assert policy["force_every_batch"] is True
    assert policy["observed_batches"] == list(range(4))
    assert recorder.knowledge_calls == 4


def test_skip_does_not_mutate_knowledge_or_translation_state(tmp_path):
    result, _ = _run(tmp_path, "kf-immutable", PLAIN)
    # Nothing was observed, so nothing may have been invented.
    assert result["knowledge_candidates"] == []
    assert result["translation_continuity"] == []
    assert result["entity_registry"] == []
    assert result["knowledge_feedback_failures"] == 0
    assert len(result["pairs"]) == len(PLAIN)
    assert all(pair.get("target") for pair in result["pairs"])
    # The observation ledger and the persisted state must agree.
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        reloaded = core.load_job_state("kf-immutable")
    finally:
        core.OUTPUT_DIR = old_output
    assert reloaded["knowledge_feedback_policy"]["observed_batches"] == [0, 3]
    assert reloaded["knowledge_feedback_policy"]["skip_reasons"] == {"no_signal": 2}


def test_resume_does_not_reissue_feedback(tmp_path):
    paragraphs = list(PLAIN)
    paragraphs[4] = "The European Space Agency coordinates the programme."
    result, first = _run(tmp_path, "kf-resume", paragraphs)
    assert first.knowledge_calls > 0
    assert first.translation_calls > 0

    # Re-running the completed job from its own persisted state must not spend
    # a single provider request, knowledge feedback included.
    second = RecordingProvider()
    old_output, old_llm = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    try:
        core.call_llm = second
        resumed = core.translate_stage(
            core.load_job_state("kf-resume"), "kf-resume", [], "DeepSeek", "key",
            "model", "简体中文", "", enable_review=False, use_tm=False,
            batch_size=4, translation_concurrency=1)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_llm
    assert second.knowledge_calls == 0
    assert second.translation_calls == 0
    assert [pair["target"] for pair in resumed["pairs"]] == \
        [pair["target"] for pair in result["pairs"]]


def test_observed_batch_still_produces_candidates_and_stays_ordered(tmp_path):
    paragraphs = list(PLAIN)
    paragraphs[0] = "The European Space Agency leads."
    recorder = RecordingProvider([
        {"segment_id": 0, "source_expression": "European Space Agency",
         "observed_target": "欧洲空间局", "kind": "organization"}])
    result, _ = _run(tmp_path, "kf-observed", paragraphs, provider=recorder)
    assert result["knowledge_feedback_policy"]["observed_batches"] == [0, 3]
    assert [pair["source"] for pair in result["pairs"]] == paragraphs
    assert all(pair.get("target") for pair in result["pairs"])
    events = result.get("knowledge_events") or []
    assert any(event.get("type") == "entity_observation" for event in events)
