"""Guards for the concurrency benchmark's reporting, not for the pipeline.

The bug this covers: ``translate_stage`` opens every stage handle at the start of
the run and closes it at the end, so a stage's ``wall_time_s`` is the *span* of
the whole run.  The benchmark used to report that number as the stage's cost,
which made every stage read identically ("generation 4.184 s / review 4.184 s")
and put the review-overlap projection on top of one shared number.

These tests feed hand-built snapshots in the shape ``PipelineProfiler`` produces
and assert that the reporting layer reads the occupied time.  They deliberately
do not run the pipeline: the failure mode was in the arithmetic, and a real run
would make the test slow and flaky without covering anything more.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_benchmark_module():
    path = ROOT / "scripts" / "translation_concurrency_benchmark.py"
    spec = importlib.util.spec_from_file_location("_concurrency_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_benchmark_module()


def _stage(*, span: float, occupied: float, item_sum: float, items: int,
           max_item: float = 0.0) -> dict:
    return {
        "wall_time_s": span,
        "interval_union_s": occupied,
        "active_wall_time_s": item_sum,
        "completed_batch_count": items,
        "per_item": {"wall_time_s": {"max": max_item}},
    }


def _snapshot(**stages) -> dict:
    return {"stages": stages}


def test_stage_split_reports_occupied_time_not_the_span():
    # Every stage spans the same 10 s run; only the occupied time differs.
    snapshot = _snapshot(
        translation_generation=_stage(span=10.0, occupied=2.0, item_sum=2.5, items=30),
        translation_semantic_review=_stage(span=10.0, occupied=1.5, item_sum=1.5, items=30),
    )
    split = bench._stage_split(snapshot)

    assert split["translation_generation"]["span_s"] == 10.0
    assert split["translation_generation"]["occupied_s"] == 2.0
    assert split["translation_semantic_review"]["occupied_s"] == 1.5
    # The defect being guarded: two stages must not report one identical cost.
    assert (split["translation_generation"]["occupied_s"]
            != split["translation_semantic_review"]["occupied_s"])


def test_missing_stage_is_reported_as_zero_rather_than_crashing():
    split = bench._stage_split(_snapshot())
    assert split["translation_repair"]["occupied_s"] == 0.0
    assert split["translation_repair"]["span_s"] == 0.0


def test_overlap_projection_uses_occupied_time():
    snapshot = _snapshot(
        translation_generation=_stage(span=10.0, occupied=2.0, item_sum=2.5, items=30),
        translation_semantic_review=_stage(span=10.0, occupied=1.5, item_sum=1.5,
                                          items=30, max_item=0.06),
    )
    projection = bench._overlap_projection(bench._stage_split(snapshot))

    assert projection["generation_s"] == 2.0
    assert projection["review_s"] == 1.5
    assert projection["serial_generation_plus_review_s"] == 3.5
    assert projection["projected_pipelined_s"] == 2.06
    # A projection built on the 10 s span would land near 10.06 / 20.0 here.
    assert projection["projected_saving_ratio"] == round(1.44 / 3.5, 4)


def test_conclusion_reports_measurements_instead_of_a_policy_sentence():
    runs = {
        1: {"wall_time_s": 8.0, "target_digest": "a"},
        2: {"wall_time_s": 4.0, "target_digest": "a"},
        4: {"wall_time_s": 3.9, "target_digest": "a"},
    }
    comparison = {
        level: {
            "speedup_vs_baseline": round(8.0 / runs[level]["wall_time_s"], 3),
            "target_digest_matches": True,
            "continuity": {"diverged_batches": level, "total_batches": 30},
        }
        for level in (2, 4)
    }
    conclusion = bench._conclusion(runs, comparison)

    assert conclusion["fastest_level"] == 4
    assert conclusion["saturation_level_within_10_percent"] == 2
    assert conclusion["target_digest_identical_across_levels"] is True
    assert conclusion["continuity_diverged_batches"] == {2: 2, 4: 4}
    assert conclusion["continuity_total_batches"] == 30
    # The old implementation returned a fixed sentence about the default staying
    # at 1; the run's own levels are an input, so the output must not claim one.
    assert set(conclusion) >= {"fastest_level", "speedup_vs_baseline", "scope"}
    assert "harness bound" in conclusion["scope"]
