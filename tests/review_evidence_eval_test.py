"""The #100 review-evidence harness itself must stay runnable and honest.

These are failure-case tests for the evaluation tooling (not production code):
the annotations must parse, the two policy arms must produce differentiated
metrics, skipped segments must stay non-reviewed/non-TM, and the harness must
restore the application's LLM callable and keep its output out of the repo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core
from eval.metrics import review_evidence as harness

ROOT = Path(__file__).resolve().parents[1]


def test_annotations_parse_and_cover_three_domains():
    samples, glossary, meta = harness.load_dataset()
    assert len(samples) == meta["count"] == 18
    domains = {sample["domain"] for sample in samples}
    assert domains == {"literary", "academic", "technical"}
    assert len(glossary) == 3
    for sample in samples:
        assert sample["source"] and sample["draft_target"]
        for error in sample["serious_errors"]:
            assert error["id"].startswith(sample["id"] + "-")
            assert error["category"] in meta["categories"]
    assert meta["annotation"]["independent_human_expert"] is False


def test_harness_compares_modes_and_keeps_skips_untrusted():
    original = core.call_llm
    report = harness.evaluate(batch_size=1)
    # The harness owns and restores the application's model callable.
    assert core.call_llm is original

    always = report["arms"]["always"]
    selective = report["arms"]["risk_selective"]
    assert always["serious_total"] == selective["serious_total"] == 12
    assert always["serious_detected"] == 12
    assert always["serious_recall"] == 1.0
    # Selective must be measured, not assumed: the fixture intentionally keeps
    # a few review-only errors (omission / mistranslation) invisible to
    # deterministic signals.  If the fixture stops exercising that, this test
    # fails instead of silently reporting an unearned 1.0.
    assert selective["serious_detected"] < always["serious_detected"]
    assert selective["missed_by_category"]
    assert set(selective["missed_by_category"]) <= {"omission", "mistranslation"}

    assert always["review_calls"] > selective["review_calls"]
    assert always["clean_segments_reviewed"] > selective["clean_segments_reviewed"]
    assert selective["review_prompt_tokens"] > 0

    for arm in (always, selective):
        assert arm["skip_violations"] == []
        assert arm["skipped_never_reviewed"] is True
        assert arm["skipped_not_tm_eligible"] is True
    assert selective["skipped_segments"] > 0
    assert selective["readiness"]["coverage_complete"] is False
    assert selective["readiness"]["skipped_count"] == selective["skipped_segments"]


def test_report_markdown_contains_denominators_and_limits():
    report = harness.evaluate(batch_size=2)
    markdown = harness.render_markdown(report)
    assert "严重错误召回率" in markdown
    assert "12/12" in markdown
    assert "限制" in markdown and "独立人类专家" in markdown
    assert "跳过语义验证" in markdown
    assert report["config"]["main_commit"]


def test_committed_result_matches_schema():
    results = sorted((ROOT / "eval" / "review_evidence" / "results").glob("*.json"))
    assert results, "at least one raw result must be retained"
    payload = json.loads(results[-1].read_text(encoding="utf-8"))
    assert payload["kind"] == "review_evidence_evaluation"
    assert payload["annotation_provenance"]["independent_human_expert"] is False
    for mode in ("always", "risk_selective"):
        assert mode in payload["arms"]
        assert payload["arms"][mode]["per_sample"]
