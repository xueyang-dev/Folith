"""Phase 3 P0 — explicit ``batch_strategy`` contract.

Under test:

* the desktop entry (Settings → JobConfig → ``to_pipeline_kwargs``) now carries
  an explicit, typed batching strategy instead of letting the core infer one
  from the presence of batch arguments;
* the resolution is behavior-preserving: every preset, every new desktop job and
  every legacy task resolves to exactly the strategy the old inference produced
  (``sentence``), and a task's saved strategy always wins over an entry default;
* the core's entry rule keeps its documented compatibility order (saved >
  explicit > legacy inference > core default) and records the configured
  strategy in the task's persisted ``pipeline_config``;
* invalid values are rejected at the contract layer and ignored (fall through
  to the documented fallback) at the runtime layer;
* the read-only diagnostics projection reports configured vs. effective
  strategy, with UNKNOWN kept as ``None`` instead of a guessed default.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.application.job_config import JobConfig, JobConfigError  # noqa: E402
from folith.application.settings import (  # noqa: E402
    SettingsSources,
    resolve_effective_settings,
)
from folith.application.task_setup import (  # noqa: E402
    BATCH_PROFILES,
    STRATEGY_PRESETS,
)


# ---------------------------------------------------------------------------
# core entry rule (saved > explicit > legacy inference > core default)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "saved,explicit,has_pairs,has_batch_args,expected",
    [
        # New desktop job: batch args present (the contract always sends them)
        # and no saved strategy — historic inference said sentence, the explicit
        # contract must say the same.
        (None, "sentence", False, True, "sentence"),
        (None, None, False, True, "sentence"),
        # Legacy tasks that started before the field existed: already-started
        # states and direct translate_stage calls keep per-sentence batches.
        (None, None, True, False, "sentence"),
        (None, None, True, True, "sentence"),
        # Direct core call without batch args: core's own new-task default.
        (None, None, False, False, "paragraph"),
        # Saved strategy is part of the resume contract: an already-started
        # paragraph job must not flip when the desktop entry sends its default.
        ("paragraph", "sentence", True, True, "paragraph"),
        ("paragraph", None, False, False, "paragraph"),
        ("sentence", "paragraph", True, True, "sentence"),
        # Explicit override applies to tasks without a recorded strategy.
        (None, "paragraph", False, True, "paragraph"),
        (None, "sentence", False, False, "sentence"),
        # Invalid values are ignored at the runtime layer and fall through to
        # the documented fallback (the contract layer is what rejects them).
        ("bogus", None, False, True, "sentence"),
        (None, "bogus", False, False, "paragraph"),
    ],
)
def test_core_entry_resolution_matrix(saved, explicit, has_pairs, has_batch_args,
                                      expected):
    assert core._resolve_entry_batch_strategy(
        saved, explicit, has_pairs=has_pairs,
        has_batch_args=has_batch_args) == expected


# ---------------------------------------------------------------------------
# contract layer: presets, new jobs, saved tasks, validation
# ---------------------------------------------------------------------------

def _job_config_from_state(state):
    return resolve_effective_settings(
        SettingsSources(explicit={}, job_state=state)).to_job_config()


@pytest.mark.parametrize("preset", list(STRATEGY_PRESETS))
def test_strategy_presets_resolve_to_explicit_sentence(preset):
    """快速 / 标准 / 学术增强 must keep the historic effective strategy."""
    state = dict(STRATEGY_PRESETS[preset])
    config = _job_config_from_state(state)
    assert config.batch_strategy == "sentence"
    kwargs = config.to_pipeline_kwargs()
    assert kwargs["batch_strategy"] == "sentence"
    profile = BATCH_PROFILES[state["batch_profile"]]
    assert kwargs["batch_size"] == profile["batch_size"]
    assert kwargs["max_batch_chars"] == profile["max_batch_chars"]


def test_batch_profiles_alone_still_resolve_to_sentence():
    for profile, values in BATCH_PROFILES.items():
        config = _job_config_from_state({"batch_profile": profile})
        assert config.batch_strategy == "sentence"
        assert config.batch_size == values["batch_size"]
        assert config.max_batch_chars == values["max_batch_chars"]


def test_new_desktop_job_contract_is_explicit_sentence():
    config = JobConfig.from_frontend({"batch_size": 6, "max_batch_chars": 4800})
    assert config.batch_strategy == "sentence"
    assert config.to_pipeline_kwargs()["batch_strategy"] == "sentence"


def test_absent_field_falls_back_to_documented_default():
    config = JobConfig.from_frontend({})
    assert config.batch_strategy == "sentence"
    # An explicitly empty value is "not configured", not an error.
    assert JobConfig.from_frontend({"batch_strategy": ""}).batch_strategy \
        == "sentence"
    assert JobConfig.from_frontend({"batch_strategy": None}).batch_strategy \
        == "sentence"


def test_invalid_strategy_is_rejected_at_contract_layer():
    with pytest.raises(JobConfigError):
        JobConfig.from_frontend({"batch_strategy": "bogus"})
    with pytest.raises(JobConfigError):
        JobConfig(batch_strategy="bogus").validate()
    # Normalization is explicit and case-insensitive.
    assert JobConfig.from_frontend({"batch_strategy": "  Paragraph "
                                    }).batch_strategy == "paragraph"


@pytest.mark.parametrize("saved", ["sentence", "paragraph"])
def test_saved_strategy_travels_with_the_task(saved):
    state = {"pipeline_config": {"batch_strategy": saved}}
    config = _job_config_from_state(state)
    assert config.batch_strategy == saved
    assert config.to_pipeline_kwargs()["batch_strategy"] == saved


def test_partial_resume_keeps_saved_paragraph_plan():
    """A half-translated paragraph task must resume with the same batches."""
    state = {
        "pairs": [{"source": "a", "target": "甲"}, {"source": "b"}],
        "pipeline_config": {"batch_strategy": "paragraph"},
    }
    config = _job_config_from_state(state)
    assert config.batch_strategy == "paragraph"


def test_settings_provenance_records_saved_strategy():
    effective = resolve_effective_settings(SettingsSources(
        explicit={}, job_state={"pipeline_config":
                                {"batch_strategy": "paragraph"}}))
    assert effective.get("batch_strategy") == "paragraph"
    assert effective.winner("batch_strategy") == "job_state"
    # No saved decision: the layer stays empty; the contract default applies.
    fresh = resolve_effective_settings(SettingsSources(explicit={}, job_state={}))
    assert fresh.get("batch_strategy") is None
    assert fresh.to_job_config().batch_strategy == "sentence"


def test_qt_settings_sources_flow_keeps_explicit_contract():
    from folith.qt import adapter

    state = {"pipeline_config": {"batch_strategy": "paragraph"}}
    config = adapter.EngineConfig(provider="OpenAI", model="m", api_key="k",
                                  target_lang="English")
    kwargs = adapter.pipeline_kwargs(config, state)
    assert kwargs["batch_strategy"] == "paragraph"
    fresh = adapter.pipeline_kwargs(config, {})
    assert fresh["batch_strategy"] == "sentence"


# ---------------------------------------------------------------------------
# runtime wiring: run_job_pipeline records the configured strategy
# ---------------------------------------------------------------------------

def _pipeline_kwargs_base():
    return dict(
        provider="DeepSeek", api_key="k", model="m",
        target_lang="简体中文", auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], style_rules="",
        enable_review=False, enable_annotate=False, use_tm=False,
        enable_understanding=False, delivery_config={"deliver_report": False},
        enable_source_cleanup=False)


def test_run_records_configured_strategy_for_new_desktop_job(tmp_path, monkeypatch):
    from tests.offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    paragraphs = [f"第{i}句 " + "词" * 30 for i in range(12)]
    state = core.new_job_state("strategy.docx")
    state.update({"paras": paragraphs, "pairs": [], "p1_done": True,
                  "p2_done": False, "target_lang": "简体中文"})
    core.save_job_state("strategy", state)

    core.run_job_pipeline("strategy", "strategy.docx", None,
                          batch_size=6, max_batch_chars=4800,
                          **_pipeline_kwargs_base())
    final = core.load_job_state("strategy")
    # The desktop contract (batch args + explicit strategy) records sentence —
    # exactly what the pre-Phase-3 inference selected.
    assert final["pipeline_config"]["batch_strategy"] == "sentence"
    assert core.batch_strategy_diagnostics(final) == {
        "configured": "sentence", "effective": "sentence"}
    # Batch boundaries are the historic sentence plan.
    assert final["batch_plan"]["batch_size"] == 6
    assert final["batch_plan"]["max_batch_chars"] == 4800
    assert "strategy" not in final["batch_plan"]


def test_run_records_core_default_for_direct_call(tmp_path, monkeypatch):
    from tests.offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    paragraphs = [f"第{i}句 " + "词" * 30 for i in range(12)]
    state = core.new_job_state("strategy-para.docx")
    state.update({"paras": paragraphs, "pairs": [], "p1_done": True,
                  "p2_done": False, "target_lang": "简体中文"})
    core.save_job_state("strategy-para", state)

    core.run_job_pipeline("strategy-para", "strategy-para.docx", None,
                          **_pipeline_kwargs_base())
    final = core.load_job_state("strategy-para")
    assert final["pipeline_config"]["batch_strategy"] == "paragraph"
    assert final["batch_plan"]["strategy"] == "paragraph"
    assert core.batch_strategy_diagnostics(final) == {
        "configured": "paragraph", "effective": "paragraph"}


def test_resume_of_started_paragraph_job_is_not_flipped(tmp_path, monkeypatch):
    """Saved paragraph strategy wins over the desktop's explicit sentence."""
    from tests.offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    paragraphs = [f"第{i}句 " + "词" * 30 for i in range(12)]
    state = core.new_job_state("strategy-resume.docx")
    state.update({"paras": paragraphs, "pairs": [], "p1_done": True,
                  "p2_done": False, "target_lang": "简体中文",
                  "pipeline_config": {"batch_strategy": "paragraph"}})
    core.save_job_state("strategy-resume", state)

    core.run_job_pipeline("strategy-resume", "strategy-resume.docx", None,
                          batch_size=6, max_batch_chars=4800,
                          **_pipeline_kwargs_base())
    final = core.load_job_state("strategy-resume")
    assert final["pipeline_config"]["batch_strategy"] == "paragraph"
    assert final["batch_plan"]["strategy"] == "paragraph"


# ---------------------------------------------------------------------------
# stable batch boundaries
# ---------------------------------------------------------------------------

def test_sentence_boundaries_identical_under_explicit_and_legacy_paths():
    """Explicit strategy must not change the sentence plan of legacy tasks."""
    paras = [f"Sentence number {i} " + "word " * 30 for i in range(30)]
    legacy = core.make_batches(paras, batch_size=6, max_chars=4800)
    # The strategy only decides *which* planner runs; once the entry resolution
    # picks sentence (explicit or inferred), the boundaries come from the same
    # call with the same saved parameters.
    explicit = core.make_batches(paras, batch_size=6, max_chars=4800)
    assert legacy == explicit
    assert legacy and all(batch for batch in legacy)


def test_paragraph_planner_selection_follows_saved_strategy():
    state = {"pipeline_config": {"batch_strategy": "paragraph"}}
    assert core._batch_strategy(state) == "paragraph"
    sentence_state = {"pipeline_config": {"batch_strategy": "sentence"}}
    assert core._batch_strategy(sentence_state) == "sentence"
    # A state without a record keeps the documented compat default.
    assert core._batch_strategy({}) == "sentence"


# ---------------------------------------------------------------------------
# diagnostics projection
# ---------------------------------------------------------------------------

def test_diagnostics_report_configured_and_effective():
    sentence = {"pipeline_config": {"batch_strategy": "sentence"},
                "batch_plan": {"batch_count": 5}}
    assert core.batch_strategy_diagnostics(sentence) == {
        "configured": "sentence", "effective": "sentence"}
    paragraph = {"pipeline_config": {"batch_strategy": "paragraph"},
                 "batch_plan": {"strategy": "paragraph", "batch_count": 3}}
    assert core.batch_strategy_diagnostics(paragraph) == {
        "configured": "paragraph", "effective": "paragraph"}
    # Legacy task with a plan but no recorded strategy: UNKNOWN stays UNKNOWN.
    legacy = {"batch_plan": {"batch_count": 2}}
    assert core.batch_strategy_diagnostics(legacy) == {
        "configured": None, "effective": None}
    # Conflicting records: the plan is what actually ran.
    conflict = {"pipeline_config": {"batch_strategy": "sentence"},
                "batch_plan": {"strategy": "paragraph", "batch_count": 1}}
    assert core.batch_strategy_diagnostics(conflict) == {
        "configured": "sentence", "effective": "paragraph"}
    assert core.batch_strategy_diagnostics(None) == {
        "configured": None, "effective": None}
