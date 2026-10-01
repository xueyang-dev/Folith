"""Translation-phase instrumentation contracts.

The profiler must expose the same level of evidence for translation that the
PDF path already had, and recording timings must not change the workflow state
machine or the produced translations.
"""
from __future__ import annotations

import json
from pathlib import Path

import core
from folith.performance import PipelineProfiler

EXPECTED_STAGES = (
    "translation_generation",
    "translation_deterministic_qa",
    "translation_repair",
    "translation_semantic_review",
    "knowledge_feedback",
    "state_commit",
    "tm_commit",
    "runtime_telemetry",
)


def _mock_llm(_provider, _api_key, _model, _system_prompt, user_prompt, **_kwargs):
    lines = [line.strip() for line in user_prompt.splitlines()
             if line.strip() and line[0].isdigit()]
    results = [f"T: {line.split('. ', 1)[1]}" for line in lines]
    return json.dumps(results)


def _run(tmp_path: Path, job_id: str, *, concurrency: int = 1,
         enable_review: bool = False, profiler=None):
    paragraphs = [f"Item {index} is a sentence." for index in range(8)]
    state = core.new_job_state("doc.txt")
    state["paras"] = paragraphs
    core.save_job_state(job_id, state)
    return core.translate_stage(
        state, job_id, glossary=[], provider="DeepSeek", api_key="k",
        model="m", target_lang="简体中文", style_rules="",
        enable_review=enable_review, use_tm=True, batch_size=2,
        translation_concurrency=concurrency, profiler=profiler,
    )


def test_profiler_reports_every_translation_sub_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "call_llm", _mock_llm)
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="run-1")
    try:
        state = _run(tmp_path, "instrumented-job", profiler=profiler)
    finally:
        core.OUTPUT_DIR = old_output

    snapshot = profiler.snapshot()
    for name in EXPECTED_STAGES:
        assert name in snapshot["stages"], name
        record = snapshot["stages"][name]
        assert record["status"] == "completed"
        assert record["wall_time_s"] >= 0.0

    generation = snapshot["stages"]["translation_generation"]
    assert generation["completed_batch_count"] == 4
    assert generation["items"], "generation must carry per-batch items"
    assert "wall_time_s" in generation["per_item"]
    assert generation["metadata"]["phase"] == "generation"

    state_commit = snapshot["stages"]["state_commit"]
    assert state_commit["completed_batch_count"] == 4
    assert state_commit["wall_time_s"] >= 0.0

    telemetry = snapshot["stages"]["runtime_telemetry"]
    assert telemetry["metadata"]["batch_count"] == 4
    assert telemetry["metadata"]["state_writes"] >= 0
    assert telemetry["metadata"]["fsyncs"] >= 0

    # The state machine outcome is unchanged by instrumentation.
    assert state["p2_done"] is False or state.get("pairs")
    assert len(state["pairs"]) == 8
    assert state["pairs"][0]["target"].startswith("T: ")


def test_instrumentation_records_effective_concurrency(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "call_llm", _mock_llm)
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="run-2")
    try:
        _run(tmp_path, "concurrent-job", concurrency=3, profiler=profiler)
    finally:
        core.OUTPUT_DIR = old_output
    snapshot = profiler.snapshot()
    assert snapshot["stages"]["translation_generation"]["concurrency"] == 3
    assert snapshot["stages"]["state_commit"]["concurrency"] == 3


def test_missing_stages_are_reported_as_skipped_not_faked(tmp_path, monkeypatch):
    """When translation is not applicable, stages must say so, not invent time."""
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="run-3")
    for name in EXPECTED_STAGES:
        if profiler.get(name) is None:
            profiler.skipped(name, metadata={"reason": "not_applicable"})
    snapshot = profiler.snapshot()
    for name in EXPECTED_STAGES:
        assert snapshot["stages"][name]["status"] == "skipped"
        assert snapshot["stages"][name]["wall_time_s"] < 0.01


def test_translation_timings_do_not_change_translations(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "call_llm", _mock_llm)
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    try:
        with_profiler = _run(
            tmp_path, "with-profiler",
            profiler=PipelineProfiler(tmp_path / "p1.json", run_id="a"))
        without = _run(tmp_path, "without-profiler", profiler=None)
    finally:
        core.OUTPUT_DIR = old_output
    assert [p["target"] for p in with_profiler["pairs"]] == \
        [p["target"] for p in without["pairs"]]
    assert [p["source"] for p in with_profiler["pairs"]] == \
        [p["source"] for p in without["pairs"]]
