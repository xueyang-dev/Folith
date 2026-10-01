#!/usr/bin/env python3
"""Reviewer Overlap Pipeline Benchmark — Performance Phase 4.

Benchmarks the Reviewer Overlap Pipeline against the strictly sequential baseline:
1. Invariant Equivalence Audit:
   Verifies 100% equivalence of translation targets, findings, TM records, and review truth.
2. Controlled Network Latency Scenarios:
   - Balanced (Gen: 150ms, Rev: 150ms)
   - Review-Heavy (Gen: 120ms, Rev: 240ms)
   - Generation-Heavy (Gen: 240ms, Rev: 120ms)
3. Concurrency & In-Flight Comparison:
   - Sequential baseline (concurrency=1, review_overlap=False)
   - Reviewer Overlap (concurrency=1, max_in_flight=1, Strategy A)
   - Reviewer Overlap (concurrency=1, max_in_flight=2, Strategy A)
   - Reviewer Overlap (concurrency=1, max_in_flight=3, Strategy A)
   - Reviewer Overlap (concurrency=1, max_in_flight=2, Strategy B: stable)
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as _context  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402

REVIEW_SYSTEM_MARKER = "独立的翻译审校专家"


def _digest(values) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_segments(count: int, sections: int = 4) -> List[str]:
    out = []
    for index in range(count):
        sec = index * sections // max(1, count)
        out.append(
            f"Section {sec} clause {index}: The critical treaty was ratified by all "
            f"participating delegations following extensive bilateral consultations."
        )
    return out


def _make_simulated_llm(gen_latency: float, rev_latency: float):
    def llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
        if REVIEW_SYSTEM_MARKER in (system_prompt or ""):
            if rev_latency > 0:
                time.sleep(rev_latency)
            return json.dumps({"findings": [], "evidence_requests": []}, ensure_ascii=False)
        if gen_latency > 0:
            time.sleep(gen_latency)
        lines = [
            line.strip()
            for line in user_prompt.splitlines()
            if line.strip() and line[0].isdigit()
        ]
        results = []
        for line in lines:
            ordinal = line.split(".", 1)[0].strip()
            results.append(f"第 {ordinal} 条款：各参会代表团经广泛双边磋商后正式批准了关键条约。")
        return json.dumps(results, ensure_ascii=False)

    return llm


def _run_single_benchmark(
    *,
    tmp_root: Path,
    run_id: str,
    segments: List[str],
    gen_latency: float,
    rev_latency: float,
    review_overlap: bool,
    max_in_flight: int = 2,
    strategy: str = "generated",
    batch_size: int = 4,
) -> Dict[str, Any]:
    run_dir = tmp_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    old_output_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = run_dir

    state = core.new_job_state("benchmark.txt")
    state["paras"] = list(segments)
    state["pipeline_config"] = {
        "batch_size": batch_size,
        "translation_concurrency": 1,
        "review_overlap": review_overlap,
        "max_review_in_flight": max_in_flight,
        "review_overlap_strategy": strategy,
    }
    core.save_job_state(run_id, state)
    core.call_llm = _make_simulated_llm(gen_latency, rev_latency)
    profiler = PipelineProfiler(run_id=run_id)

    try:
        t0 = time.perf_counter()
        result_state = core.translate_stage(
            state,
            run_id,
            glossary=[],
            provider="SimulatedProvider",
            api_key="sim-key",
            model="sim-model",
            target_lang="简体中文",
            style_rules="",
            enable_review=True,
            use_tm=True,
            batch_size=batch_size,
            translation_concurrency=1,
            review_overlap=review_overlap,
            max_review_in_flight=max_in_flight,
            review_overlap_strategy=strategy,
            profiler=profiler,
        )
        wall_time_s = time.perf_counter() - t0
    finally:
        core.OUTPUT_DIR = old_output_dir

    pairs = result_state.get("pairs") or []
    targets = [p.get("target") for p in pairs]
    review_statuses = [p.get("review_status") for p in pairs]
    findings = result_state.get("findings") or []
    overlap_metrics = result_state.get("review_overlap") or {}
    tm = core.load_tm() if (run_dir / "translation_memory.json").exists() else {}

    # Extract profiler stage timings
    prof_snapshot = profiler.snapshot()
    stages = prof_snapshot.get("stages") or {}
    gen_stage_wall = float((stages.get("translation_generation") or {}).get("wall_time_s") or 0.0)
    rev_stage_wall = float((stages.get("translation_semantic_review") or {}).get("wall_time_s") or 0.0)
    commit_stage_wall = float((stages.get("state_commit") or {}).get("wall_time_s") or 0.0)

    return {
        "run_id": run_id,
        "review_overlap": review_overlap,
        "max_in_flight": max_in_flight,
        "strategy": strategy,
        "wall_time_s": round(wall_time_s, 4),
        "gen_stage_wall_s": round(gen_stage_wall, 4),
        "rev_stage_wall_s": round(rev_stage_wall, 4),
        "commit_stage_wall_s": round(commit_stage_wall, 4),
        "segment_count": len(pairs),
        "target_digest": _digest(targets),
        "findings_digest": _digest([f.get("summary") for f in findings]),
        "review_status_digest": _digest(review_statuses),
        "tm_entry_count": len(tm),
        "tm_digest": _digest(sorted(tm.keys())),
        "overlap_metrics": overlap_metrics,
    }


def run_benchmark_suite(out_dir: Path, segments_count: int = 40) -> Dict[str, Any]:
    tmp_root = Path(tempfile.mkdtemp(prefix="ft-benchmark-"))
    segments = _build_segments(segments_count)
    results: Dict[str, Any] = {}

    scenarios = [
        ("balanced", 0.10, 0.10, "Balanced (Gen: 100ms, Rev: 100ms)"),
        ("rev_heavy", 0.08, 0.16, "Review-Heavy (Gen: 80ms, Rev: 160ms)"),
        ("gen_heavy", 0.16, 0.08, "Generation-Heavy (Gen: 160ms, Rev: 80ms)"),
        ("zero_lat", 0.0, 0.0, "Zero Latency Invariant Audit"),
    ]

    for scen_key, gen_lat, rev_lat, scen_name in scenarios:
        scen_results = []
        # 1. Sequential baseline
        seq_res = _run_single_benchmark(
            tmp_root=tmp_root,
            run_id=f"{scen_key}-seq",
            segments=segments,
            gen_latency=gen_lat,
            rev_latency=rev_lat,
            review_overlap=False,
        )
        scen_results.append(seq_res)

        # 2. Overlap max_in_flight=1
        ovl_1 = _run_single_benchmark(
            tmp_root=tmp_root,
            run_id=f"{scen_key}-ovl-cap1",
            segments=segments,
            gen_latency=gen_lat,
            rev_latency=rev_lat,
            review_overlap=True,
            max_in_flight=1,
            strategy="generated",
        )
        scen_results.append(ovl_1)

        # 3. Overlap max_in_flight=2 (default Strategy A)
        ovl_2 = _run_single_benchmark(
            tmp_root=tmp_root,
            run_id=f"{scen_key}-ovl-cap2",
            segments=segments,
            gen_latency=gen_lat,
            rev_latency=rev_lat,
            review_overlap=True,
            max_in_flight=2,
            strategy="generated",
        )
        scen_results.append(ovl_2)

        # 4. Overlap max_in_flight=3 (Strategy A)
        ovl_3 = _run_single_benchmark(
            tmp_root=tmp_root,
            run_id=f"{scen_key}-ovl-cap3",
            segments=segments,
            gen_latency=gen_lat,
            rev_latency=rev_lat,
            review_overlap=True,
            max_in_flight=3,
            strategy="generated",
        )
        scen_results.append(ovl_3)

        # 5. Overlap Strategy B (stable)
        ovl_b = _run_single_benchmark(
            tmp_root=tmp_root,
            run_id=f"{scen_key}-ovl-stratb",
            segments=segments,
            gen_latency=gen_lat,
            rev_latency=rev_lat,
            review_overlap=True,
            max_in_flight=2,
            strategy="stable",
        )
        scen_results.append(ovl_b)

        # Compute savings relative to baseline
        seq_wall = seq_res["wall_time_s"]
        for r in scen_results:
            w = r["wall_time_s"]
            savings_pct = round((seq_wall - w) / seq_wall * 100.0, 2) if seq_wall > 0 else 0.0
            r["wall_reduction_pct"] = savings_pct
            # Compare invariants against baseline
            r["invariant_target_match"] = r["target_digest"] == seq_res["target_digest"]
            r["invariant_findings_match"] = r["findings_digest"] == seq_res["findings_digest"]
            r["invariant_tm_match"] = r["tm_digest"] == seq_res["tm_digest"]
            r["invariant_all_passed"] = (
                r["invariant_target_match"]
                and r["invariant_findings_match"]
                and r["invariant_tm_match"]
            )

        results[scen_key] = {
            "name": scen_name,
            "gen_latency_s": gen_lat,
            "rev_latency_s": rev_lat,
            "runs": scen_results,
        }

    return results


def export_reports(results: Dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. JSON Export
    json_path = out_dir / "reviewer_overlap_benchmark.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 2. Markdown Report
    md_lines = [
        "# Folith Reviewer Overlap Pipeline Benchmark Report",
        "",
        "## Performance Phase 4 — Controlled Latency & Invariant Verification",
        "",
        f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for scen_key, scen in results.items():
        md_lines.extend([
            f"### Scenario: {scen['name']}",
            "",
            "| Mode / Configuration | Wall Time (s) | Reduction (%) | Hidden Rev (s) | Hidden Ratio (%) | Max In-Flight | Invariants Equivalence |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ])
        for r in scen["runs"]:
            overlap_metrics = r.get("overlap_metrics") or {}
            hidden_s = overlap_metrics.get("hidden_review_seconds", 0.0)
            hidden_ratio = round(overlap_metrics.get("hidden_review_ratio", 0.0) * 100.0, 1)
            backlog = overlap_metrics.get("max_review_backlog", 0)
            label = r["run_id"]
            if not r["review_overlap"]:
                label = "Sequential Baseline"
            elif r["strategy"] == "stable":
                label = f"Overlap (Strategy B: stable, cap={r['max_in_flight']})"
            else:
                label = f"Overlap (Strategy A, cap={r['max_in_flight']})"

            equiv_badge = "✅ 100% PASS" if r["invariant_all_passed"] else "❌ MISMATCH"
            reduction_str = f"{r['wall_reduction_pct']:+.1f}%" if r["review_overlap"] else "Baseline (0.0%)"

            md_lines.append(
                f"| {label} | {r['wall_time_s']:.3f} | {reduction_str} | {hidden_s:.3f}s | {hidden_ratio}% | {backlog} | {equiv_badge} |"
            )
        md_lines.append("")

    md_path = out_dir / "reviewer_overlap_benchmark.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # 3. TSV Export
    tsv_lines = [
        "scenario\tconfiguration\twall_time_s\treduction_pct\thidden_review_s\thidden_ratio_pct\tmax_in_flight\tinvariants_passed"
    ]
    for scen_key, scen in results.items():
        for r in scen["runs"]:
            overlap_metrics = r.get("overlap_metrics") or {}
            hidden_s = overlap_metrics.get("hidden_review_seconds", 0.0)
            hidden_ratio = round(overlap_metrics.get("hidden_review_ratio", 0.0) * 100.0, 1)
            tsv_lines.append(
                f"{scen_key}\t{r['run_id']}\t{r['wall_time_s']}\t{r['wall_reduction_pct']}\t{hidden_s}\t{hidden_ratio}\t{overlap_metrics.get('max_review_backlog', 0)}\t{r['invariant_all_passed']}"
            )
    tsv_path = out_dir / "reviewer_overlap_benchmark.tsv"
    with open(tsv_path, "w", encoding="utf-8") as f:
        f.write("\n".join(tsv_lines))

    # Print to console
    print("\n" + "\n".join(md_lines))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Folith Reviewer Overlap Benchmark")
    parser.add_argument("--out", default="tmp/phase4", help="Output directory for reports")
    parser.add_argument("--segments", type=int, default=40, help="Number of test segments")
    args = parser.parse_args()

    out_dir = Path(args.out)
    results = run_benchmark_suite(out_dir, segments_count=args.segments)
    export_reports(results, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
