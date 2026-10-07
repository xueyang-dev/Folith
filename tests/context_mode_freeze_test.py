"""Context-architecture freeze contract (production `fixed` vs experimental `dynamic_v2`).

This file is the durable test form of ``docs/dynamic-context-v2-adr.md``.  It
exists so a future agent cannot quietly turn the experimental selector into the
implicit path, or make production pay for it.

What it pins, and why each assertion is here:

* the production default is ``fixed`` and the experimental mode is *only*
  reachable by an explicit, exact ``"dynamic_v2"`` (missing / unknown / malformed
  values all fail safe);
* a `fixed` run never calls into ``folith.dynamic_context`` at all — the guard
  test makes every dynamic entry point raise, so a single accidental call fails
  loudly instead of silently costing tokens;
* the `fixed` prompt is byte-identical whether or not the experiment module is
  reachable, so the production prompt fingerprint cannot drift;
* a `fixed` run writes no experimental keys into job state, and a resumed job
  keeps whatever mode it recorded;
* the experimental assets are still present and callable (the evidence is kept,
  not deleted).

Feature-level behaviour of the selector itself lives in
``tests/dynamic_context_test.py``; this file deliberately does not repeat it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core
from folith import dynamic_context as dc

LANG = "简体中文"
REPO_ROOT = Path(__file__).resolve().parent.parent

#: State keys that must never appear on a production `fixed` job.
EXPERIMENTAL_STATE_KEYS = (
    "context_mode",
    "context_budget_tokens",
    "context_ablations",
    "context_offline",
)


def _stub_llm(prompts):
    def llm(provider, api_key, model, system, user, temperature=0.1, **extra):
        if "翻译流知识抽取器" in system:
            return "[]"
        prompts.append(user)
        return json.dumps(["流水线译文。"], ensure_ascii=False)
    return llm


def _run(tmp_path, job_id, paras, *, state_overrides=None, **kwargs):
    """Run the real ``core.translate_stage`` with a deterministic stub provider."""
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR = tmp_path
    prompts = []
    core.call_llm = _stub_llm(prompts)
    try:
        state = core.new_job_state(f"{job_id}.txt")
        state["paras"] = list(paras)
        state["target_lang"] = LANG
        state.update(state_overrides or {})
        result = core.translate_stage(
            state, job_id, [], "DeepSeek", "key", "model", LANG, "",
            enable_review=False, use_tm=False, **kwargs)
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
    return result, prompts


class _Forbidden:
    """Fail loudly if any experimental entry point is touched."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(
            "the production `fixed` path called an experimental selector entry point")


# ---------------------------------------------------------------------------
# The frozen mode contract
# ---------------------------------------------------------------------------


def test_production_default_and_experimental_status_are_explicit():
    assert dc.PRODUCTION_CONTEXT_MODE == "fixed"
    assert dc.EXPERIMENTAL_CONTEXT_MODE == "dynamic_v2"
    # Exactly two modes: no third mode was added by the freeze.
    assert dc.CONTEXT_MODES == ("fixed", "dynamic_v2")
    assert dc.CONTEXT_MODE_STATUS == {
        "fixed": "production/stable/default",
        "dynamic_v2": "experimental/research/opt-in",
    }
    for name in ("PRODUCTION_CONTEXT_MODE", "EXPERIMENTAL_CONTEXT_MODE",
                 "CONTEXT_MODE_STATUS"):
        assert name in dc.__all__


@pytest.mark.parametrize("value", [
    "bogus", "FIXED", "fixed_v2", "dynamic", "dynamic_v3", "DYNAMIC_V2",
    True, 1, ["fixed"], {"mode": "fixed"},
])
def test_unrecognised_mode_argument_overrides_state_to_fixed(value):
    # A non-empty but unrecognised argument is an explicit (wrong) choice: it must
    # never fall through to an experimental state.
    assert dc.resolve_context_mode(value) == dc.PRODUCTION_CONTEXT_MODE
    assert dc.resolve_context_mode(value, {"context_mode": "dynamic_v2"}) == \
        dc.PRODUCTION_CONTEXT_MODE


@pytest.mark.parametrize("value", [None, "", "   ", False, 0])
def test_absent_mode_argument_falls_back_to_state_then_fixed(value):
    # An absent argument means "not specified", so the recorded mode is honoured
    # and only its absence resolves to the production default.
    assert dc.resolve_context_mode(value) == dc.PRODUCTION_CONTEXT_MODE
    assert dc.resolve_context_mode(
        value, {"context_mode": "dynamic_v2"}) == dc.EXPERIMENTAL_CONTEXT_MODE
    assert dc.resolve_context_mode(
        value, {"context_mode": "bogus"}) == dc.PRODUCTION_CONTEXT_MODE


@pytest.mark.parametrize("state", [
    {}, {"context_mode": None}, {"context_mode": ""}, {"context_mode": "bogus"},
    {"context_mode": ["dynamic_v2"]}, {"context_mode": 1},
])
def test_malformed_state_modes_fail_safe_to_fixed(state):
    assert dc.resolve_context_mode(None, state) == dc.PRODUCTION_CONTEXT_MODE


def test_only_an_exact_dynamic_v2_reaches_the_experiment():
    assert dc.resolve_context_mode("dynamic_v2") == dc.EXPERIMENTAL_CONTEXT_MODE
    assert dc.resolve_context_mode(
        None, {"context_mode": "dynamic_v2"}) == dc.EXPERIMENTAL_CONTEXT_MODE
    # Explicit argument wins over recorded state, in both directions.
    assert dc.resolve_context_mode(
        "fixed", {"context_mode": "dynamic_v2"}) == dc.PRODUCTION_CONTEXT_MODE


# ---------------------------------------------------------------------------
# Isolation: production must not touch the experiment
# ---------------------------------------------------------------------------


def test_fixed_mode_never_calls_the_dynamic_selector(tmp_path, monkeypatch):
    forbidden = _Forbidden()
    monkeypatch.setattr(dc, "build_dynamic_packet", forbidden)
    monkeypatch.setattr(dc, "collect_context_candidates", forbidden)
    monkeypatch.setattr(dc, "select_context", forbidden)
    monkeypatch.setattr(dc, "rank_candidates", forbidden)
    monkeypatch.setattr(dc.DistantIndex, "build", classmethod(
        lambda cls, *a, **k: forbidden(*a, **k)))

    result, prompts = _run(
        tmp_path, "freeze-fixed-isolated",
        ["The cat sat on the mat.", "The dog ran in the park."])

    assert not forbidden.calls
    assert prompts, "the fixed path must still translate"
    assert not result.get("dynamic_context_records")
    # No dynamic-only prompt section leaked into production.
    for title in ("【翻译记忆参考（近似匹配，仅供一致译法参考）】",
                  "【远距离相关段落（历史上下文，仅供一致性参考）】",
                  "【项目知识反馈（人工确认优先）】"):
        assert title not in prompts[0]


def test_fixed_prompt_is_identical_with_and_without_the_experiment_present(
        tmp_path, monkeypatch):
    baseline, baseline_prompts = _run(
        tmp_path / "baseline", "freeze-fixed-baseline",
        ["The cat sat on the mat.", "The dog ran in the park."])

    forbidden = _Forbidden()
    monkeypatch.setattr(dc, "build_dynamic_packet", forbidden)
    monkeypatch.setattr(dc.DistantIndex, "build", classmethod(
        lambda cls, *a, **k: forbidden(*a, **k)))
    guarded, guarded_prompts = _run(
        tmp_path / "guarded", "freeze-fixed-guarded",
        ["The cat sat on the mat.", "The dog ran in the park."])

    assert baseline_prompts == guarded_prompts
    assert [p.get("target") for p in baseline["pairs"]] == \
        [p.get("target") for p in guarded["pairs"]]
    assert not forbidden.calls


def test_fixed_mode_writes_no_experimental_state_keys(tmp_path):
    result, _prompts = _run(
        tmp_path, "freeze-fixed-state",
        ["The cat sat on the mat.", "The dog ran in the park."])
    for key in EXPERIMENTAL_STATE_KEYS:
        assert key not in result, f"fixed mode must not persist {key!r}"
    # The serialized task configuration keeps its historical shape too.
    assert "context_mode" not in (result.get("pipeline_config") or {})
    assert "context_budget_tokens" not in (result.get("pipeline_config") or {})


def test_unknown_mode_argument_is_indistinguishable_from_fixed(tmp_path):
    fixed, fixed_prompts = _run(
        tmp_path / "a", "freeze-unknown-a", ["The cat sat on the mat."])
    unknown, unknown_prompts = _run(
        tmp_path / "b", "freeze-unknown-b", ["The cat sat on the mat."],
        context_mode="not-a-mode")
    assert fixed_prompts == unknown_prompts
    assert not unknown.get("dynamic_context_records")
    assert [p.get("target") for p in fixed["pairs"]] == \
        [p.get("target") for p in unknown["pairs"]]


# ---------------------------------------------------------------------------
# The experiment stays reachable, and resume keeps a recorded mode
# ---------------------------------------------------------------------------


def test_explicit_dynamic_v2_still_reaches_the_selector(tmp_path):
    result, _prompts = _run(
        tmp_path, "freeze-dynamic-optin",
        ["The cat sat on the mat.", "The dog ran in the park."],
        context_mode="dynamic_v2")
    records = result.get("dynamic_context_records") or []
    assert records, "explicit dynamic_v2 must still run the experimental path"
    assert result["context_mode"] == dc.EXPERIMENTAL_CONTEXT_MODE
    assert records[0]["context_mode"] == dc.EXPERIMENTAL_CONTEXT_MODE


def test_resume_preserves_the_recorded_mode(tmp_path):
    # A job that recorded the experiment resumes in the experiment ...
    experimental, _ = _run(
        tmp_path / "dyn", "freeze-resume-dyn", ["The cat sat on the mat."],
        context_mode="dynamic_v2")
    resumed_experimental, _ = _run(
        tmp_path / "dyn", "freeze-resume-dyn-2", ["The cat sat on the mat."],
        state_overrides={
            "context_mode": experimental["context_mode"],
            "context_budget_tokens": experimental["context_budget_tokens"],
        })
    assert resumed_experimental["context_mode"] == dc.EXPERIMENTAL_CONTEXT_MODE
    assert resumed_experimental.get("dynamic_context_records")

    # ... and a job that recorded nothing resumes on the production default.
    plain, _ = _run(
        tmp_path / "fix", "freeze-resume-fixed", ["The cat sat on the mat."])
    resumed_plain, _ = _run(
        tmp_path / "fix", "freeze-resume-fixed-2", ["The cat sat on the mat."],
        state_overrides={key: plain[key] for key in plain
                         if key in EXPERIMENTAL_STATE_KEYS})
    assert dc.resolve_context_mode(None, resumed_plain) == \
        dc.PRODUCTION_CONTEXT_MODE
    assert not resumed_plain.get("dynamic_context_records")


# ---------------------------------------------------------------------------
# The experimental evidence is preserved, not cleaned up
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("relative", [
    "folith/dynamic_context.py",
    "tests/dynamic_context_test.py",
    "docs/dynamic-context-v2-adr.md",
    "docs/dynamic-context-v2-report.md",
    "docs/dynamic-context-v2-interface.md",
    "docs/dynamic-context-v2-evidence/comparison.json",
    "docs/dynamic-context-v2-evidence/consistency.json",
    "docs/dynamic-context-v2-evidence/review.json",
    "scripts/dynamic_context_ab.py",
    "scripts/dynamic_context_review.py",
    "scripts/dynamic_context_consistency.py",
    "scripts/dynamic_context_inspect.py",
    "scripts/dynamic_context_verify.py",
])
def test_experimental_assets_are_preserved(relative):
    assert (REPO_ROOT / relative).is_file(), \
        f"experimental asset missing: {relative}"


def test_no_forbidden_research_extensions_were_added():
    """The freeze must not have introduced graph/embedding/reranker machinery.

    Checked on the module's actual imports (not on prose: the module docstring
    legitimately *states* that it has no reranker).
    """
    import ast

    source = (REPO_ROOT / "folith/dynamic_context.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import inside the folith package
                imported.add("folith")
            elif node.module:
                imported.add(node.module.split(".")[0])
    forbidden = {"sentence_transformers", "faiss", "chromadb", "networkx",
                 "torch", "sklearn", "transformers", "numpy", "scipy"}
    assert not (imported & forbidden), \
        f"experimental module grew forbidden imports: {sorted(imported & forbidden)}"
    # Local, deterministic, standard-library-only retrieval.
    assert imported <= {"__future__", "re", "dataclasses", "difflib", "typing",
                        "folith"}, \
        f"unexpected imports in the experimental selector: {sorted(imported)}"
