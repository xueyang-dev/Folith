#!/usr/bin/env python3
"""Concurrency benchmark for the translation stage — evidence, not a policy change.

The product default stays ``translation_concurrency=1``.  This script exists to
produce the evidence needed before that could ever change:

* wall-clock throughput at concurrency 1 / 2 / 3 with a deterministic fake LLM
  and a fixed per-request latency (no API spend);
* whether the **continuity context** a batch sees (``previous_target``) is
  identical across concurrency levels — the real risk of naive parallelism;
* whether the produced translations stay identical (segment count, order,
  source order, target digest);
* the per-stage cost split (generation vs deterministic QA vs review vs state
  commit) and a projection of how much of the review latency could be hidden by
  overlapping review of batch *i* with generation of batch *i+1*;
* the section-aware wave plan for the same fixture, so the alternative
  scheduling design can be compared on paper.

Run:

    python3 scripts/translation_concurrency_benchmark.py \
        --out tmp/translation-concurrency-benchmark \
        --segments 120 --sections 6 --latency 0.05 --review
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as _context  # noqa: E402
from folith import section_scheduler as _sched  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402

REVIEW_SYSTEM_MARKER = "独立的翻译审校专家"
TRANSLATION_STAGES = (
    "translation_generation",
    "translation_deterministic_qa",
    "translation_repair",
    "translation_semantic_review",
    "knowledge_feedback",
    "state_commit",
    "tm_commit",
)


def _digest(values) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_segments(count: int, sections: int) -> list[str]:
    """Synthetic prose long enough that sentence segmentation keeps each unit."""
    out = []
    for index in range(count):
        section = index * sections // max(1, count)
        out.append(
            f"Section {section} paragraph {index}: the archival record describes "
            f"the transmission of the manuscript across the northern provinces.")
    return out


def _fake_llm_factory(latency: float, calls: list):
    def llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
        calls.append(time.monotonic())
        if latency:
            time.sleep(latency)
        if REVIEW_SYSTEM_MARKER in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []},
                              ensure_ascii=False)
        lines = [line.strip() for line in user_prompt.splitlines()
                 if line.strip() and line[0].isdigit()]
        results = []
        for line in lines:
            ordinal = line.split(".", 1)[0].strip()
            results.append(f"第{ordinal}段记录了手稿在同年冬季之前经由北方各省的档案南运。")
        return json.dumps(results, ensure_ascii=False)
    return llm


def _stage_split(snapshot: dict) -> dict:
    """Per-stage wall time plus the max single-batch cost of the review stage.

    ``review_last_batch_s`` is the number that matters for overlap: in a
    pipelined design the reviewer of the final batch cannot be hidden behind any
    later translation, so it is the irreducible tail.
    """
    stages = snapshot.get("stages") or {}
    split = {}
    for name in TRANSLATION_STAGES:
        record = stages.get(name) or {}
        per_item = (record.get("per_item") or {}).get("wall_time_s") or {}
        split[name] = {
            "wall_time_s": round(float(record.get("wall_time_s") or 0.0), 4),
            "batch_count": int(record.get("completed_batch_count") or 0),
            "max_batch_s": round(float(per_item.get("max") or 0.0), 6),
        }
    return split


def _run_once(tmp_dir: Path, job_id: str, segments: list[str], concurrency: int,
              latency: float, section_ids: list[str],
              enable_review: bool = False) -> dict:
    core.OUTPUT_DIR = tmp_dir / job_id
    (tmp_dir / job_id).mkdir(parents=True, exist_ok=True)
    state = core.new_job_state("bench.txt")
    state["paras"] = segments
    state["document_profile"] = {"sections": [{"id": sid, "title": sid}
                                              for sid in section_ids]}
    core.save_job_state(job_id, state)

    calls: list = []
    contexts: list = []
    original_compile = _context.compile_context_packet

    def recording_compile(*args, **kwargs):
        packet = original_compile(*args, **kwargs)
        contexts.append({
            "current_batch": list(kwargs.get("current_batch") or []),
            "previous_target": [
                (item.get("target") if isinstance(item, dict) else str(item))
                for item in (kwargs.get("previous_target") or [])
            ],
        })
        return packet

    _context.compile_context_packet = recording_compile
    core.call_llm = _fake_llm_factory(latency, calls)
    profiler = PipelineProfiler(run_id=f"concurrency-{concurrency}")
    try:
        begin = time.perf_counter()
        result = core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=enable_review, use_tm=enable_review, batch_size=4,
            translation_concurrency=concurrency,
            document_profile=state["document_profile"], profiler=profiler)
        wall = time.perf_counter() - begin
    finally:
        _context.compile_context_packet = original_compile

    pairs = result.get("pairs") or []
    return {
        "concurrency": concurrency,
        "wall_time_s": round(wall, 4),
        "request_count": len(calls),
        "segment_count": len(pairs),
        "source_order_digest": _digest(p["source"] for p in pairs),
        "target_digest": _digest(p["target"] for p in pairs),
        "contexts": contexts,
        "batch_plan": result.get("batch_plan"),
        "stage_split": _stage_split(profiler.snapshot()),
    }


def _overlap_projection(split: dict) -> dict:
    """How much review latency a translator/reviewer pipeline could hide.

    Serial model: generation then review, per batch, so the review cost is fully
    exposed.  Pipelined model: review of batch *i* runs while batch *i+1* is
    being generated, so only the last batch's review is irreducible.
    """
    generation = float((split.get("translation_generation") or {}).get("wall_time_s") or 0.0)
    review = float((split.get("translation_semantic_review") or {}).get("wall_time_s") or 0.0)
    review_tail = float((split.get("translation_semantic_review") or {}).get("max_batch_s") or 0.0)
    projected = generation + review_tail
    serial = generation + review
    return {
        "generation_s": round(generation, 4),
        "review_s": round(review, 4),
        "review_tail_batch_s": round(review_tail, 4),
        "serial_generation_plus_review_s": round(serial, 4),
        "projected_pipelined_s": round(projected, 4),
        "projected_saving_s": round(max(0.0, serial - projected), 4),
        "projected_saving_ratio": round(
            max(0.0, serial - projected) / serial, 4) if serial else 0.0,
    }


def _context_divergence(baseline: list, other: list) -> dict:
    """How many batches saw a different continuity context."""
    diverged = 0
    missing = 0
    for index, base in enumerate(baseline):
        if index >= len(other):
            missing += 1
            continue
        if base.get("previous_target") != other[index].get("previous_target"):
            diverged += 1
    return {"diverged_batches": diverged, "missing_batches": missing,
            "total_batches": len(baseline)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/translation-concurrency-benchmark")
    parser.add_argument("--segments", type=int, default=120)
    parser.add_argument("--sections", type=int, default=6)
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--concurrency", default="1,2,3")
    parser.add_argument("--review", action="store_true",
                        help="also run the independent review stage (enables TM "
                             "promotion) so the review/overlap projection is real")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = [int(value) for value in str(args.concurrency).split(",") if value.strip()]
    segments = _build_segments(args.segments, args.sections)
    section_ids = [f"section-{index}" for index in range(args.sections)]

    runs = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for concurrency in levels:
            runs[concurrency] = _run_once(
                tmp_dir, f"concurrency-{concurrency}", segments, concurrency,
                args.latency, section_ids, enable_review=args.review)

    baseline = runs[levels[0]]
    comparison = {}
    for concurrency in levels[1:]:
        candidate = runs[concurrency]
        comparison[str(concurrency)] = {
            "wall_time_s": candidate["wall_time_s"],
            "speedup_vs_baseline": round(
                baseline["wall_time_s"] / max(1e-9, candidate["wall_time_s"]), 3),
            "target_digest_matches": (
                candidate["target_digest"] == baseline["target_digest"]),
            "source_order_digest_matches": (
                candidate["source_order_digest"] == baseline["source_order_digest"]),
            "segment_count_matches": (
                candidate["segment_count"] == baseline["segment_count"]),
            "continuity": _context_divergence(
                baseline["contexts"], candidate["contexts"]),
        }

    batch_sizes = [4] * ((len(segments) + 3) // 4)
    section_of_segment = [
        index * args.sections // max(1, len(segments))
        for index in range(len(segments))
    ]
    schedule = _sched.build_schedule(batch_sizes, section_of_segment,
                                     concurrency=max(levels),
                                     section_ids=section_ids)

    overlap = {
        str(concurrency): _overlap_projection(run["stage_split"])
        for concurrency, run in runs.items()
    } if args.review else {}

    report = {
        "fixture": {
            "segments": len(segments),
            "sections": args.sections,
            "fake_llm_latency_s": args.latency,
            "batch_size": 4,
            "review_enabled": args.review,
        },
        "runs": {
            str(concurrency): {
                key: value for key, value in run.items() if key != "contexts"
            }
            for concurrency, run in runs.items()
        },
        "comparison_vs_baseline": comparison,
        "review_overlap_projection": overlap,
        "section_aware_plan": schedule.to_dict(),
        "wall_time_spread_s": round(
            statistics.pstdev([run["wall_time_s"] for run in runs.values()]), 6),
        "conclusion": (
            "Default concurrency stays at 1: the target digest is compared per "
            "level, and any continuity divergence is reported rather than "
            "assumed harmless."),
    }
    (out_dir / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "fixture": report["fixture"],
        "runs": report["runs"],
        "comparison": comparison,
        "review_overlap_projection": overlap,
    }, ensure_ascii=False, indent=2))
    print(f"\nwrote {out_dir / 'benchmark.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
