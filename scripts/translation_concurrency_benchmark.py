#!/usr/bin/env python3
"""Concurrency benchmark for the translation stage — evidence, not a policy change.

This script produces the measurements behind ``docs/translation-throughput.md``.
It is *not* the place where a default is decided: the core's own fallback is 1,
the shared contract sends 4, and the three workflow presets send 6 / 4 / 4.  What
this script can settle is which of those levels were actually measured, and
whether raising the level changes what a batch sees.  It reports:

* wall-clock throughput at the requested concurrency levels with a deterministic
  fake LLM and a fixed per-request latency (no API spend);
* whether the **continuity context** a batch sees (``previous_target``) is
  identical across concurrency levels — the real risk of naive parallelism;
* whether the produced translations stay identical (segment count, order,
  source order, target digest);
* the per-stage cost split (generation vs deterministic QA vs review vs state
  commit) and a projection of how much of the review latency could be hidden by
  overlapping review of batch *i* with generation of batch *i+1*;
* the section-aware wave plan for the same fixture, so the alternative
  scheduling design can be compared on paper.

Two measurement notes that matter when reading the numbers:

* **Span is not cost.**  ``translate_stage`` opens a stage handle once and closes
  it at the end, so a stage's ``wall_time_s`` is the *span* of the whole run and
  every stage reports roughly the same value.  The occupied time is
  ``interval_union_s`` (union of item intervals) — that is what the overlap
  projection uses.  Reporting the span as "stage cost" is what previously made
  every stage look identical.
* **The fake LLM caps the apparent speedup.**  With a fixed per-request latency
  and a fake provider, everything the pipeline does besides waiting (prompt
  assembly, journal/state writes, TM commits) is serialised on the calling
  thread, so throughput plateaus well below the concurrency level.  The plateau
  bounds the *harness*, not a real provider.  Use this script to prove semantic
  equivalence and to detect regressions between revisions, not to promise a
  real-world speedup factor.

Run:

    python3 scripts/translation_concurrency_benchmark.py \
        --out tmp/translation-concurrency-benchmark \
        --segments 120 --sections 6 --latency 0.05 --review \
        --concurrency 1,2,3,4,6,8 --repeat 3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.application import job_config, task_setup  # noqa: E402
from folith import context as _context  # noqa: E402
from folith import section_scheduler as _sched  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402

REVIEW_SYSTEM_MARKER = "独立的翻译审校专家"
KNOWLEDGE_SYSTEM_MARKER = "翻译流知识抽取器"
TRANSLATION_MARKER = "【待翻译段落（按序号返回等长数组）】"
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
    lock = threading.Lock()

    def llm(_provider, _api_key, _model, system_prompt, user_prompt, **_kwargs):
        system = system_prompt or ""
        role = ("knowledge" if KNOWLEDGE_SYSTEM_MARKER in system else
                "review" if REVIEW_SYSTEM_MARKER in system else "translation")
        call = {"role": role, "started": time.monotonic()}
        with lock:
            calls.append(call)
        try:
            if latency:
                time.sleep(latency)
            if role == "review":
                return json.dumps({"findings": [], "evidence_requests": []})
            if role == "knowledge":
                ids = re.findall(r"segment_id:\s*(\d+)\n原文：", user_prompt)
                return json.dumps([
                    {"segment_id": int(index), "source_expression": "manuscript",
                     "observed_target": "手稿", "kind": "term"} for index in ids],
                    ensure_ascii=False)
            tail = user_prompt.split(TRANSLATION_MARKER, 1)[-1]
            sources = re.findall(r"(?m)^\d+\.\s+(.*)$", tail)
            results = []
            for source in sources:
                match = re.match(r"Section (\d+) paragraph (\d+):", source)
                if match is None:
                    raise ValueError(f"unexpected benchmark source: {source[:80]}")
                section, paragraph = match.groups()
                results.append(f"第{section}节第{paragraph}段：档案记录描述了手稿在北方各省之间的流传。")
            return json.dumps(results, ensure_ascii=False)
        finally:
            call["finished"] = time.monotonic()
    return llm


def _timing_summary(events):
    batches = {}
    for event in events:
        if event["stage"] == "translation":
            batches.setdefault(event["batch_id"], {})[event["phase"]] = event["at"]
    result = {}
    for label, start, end in (
            ("prepare", "worker_started", "result_ready"),
            ("commit_wait", "result_ready", "commit_started"),
            ("commit", "commit_started", "commit_finished")):
        values = sorted(max(0.0, row[end] - row[start]) for row in batches.values()
                        if start in row and end in row)
        if values:
            result[label] = {"count": len(values), "sum_s": round(sum(values), 6),
                             "median_s": round(statistics.median(values), 6),
                             "p95_s": round(values[int((len(values) - 1) * .95)], 6),
                             "max_s": round(values[-1], 6)}
    return result


def _stage_split(snapshot: dict) -> dict:
    """Per-stage cost, using occupied time rather than the stage's span.

    ``wall_time_s`` on a stage record is the span from the first ``start_stage``
    to ``finish``; because ``translate_stage`` keeps every stage open for the
    whole run, that value is the run length for every stage and tells us nothing
    about where the time went.  ``interval_union_s`` is the union of the stage's
    item intervals — the time the stage actually occupied — and
    ``active_wall_time_s`` is the plain sum of item durations (which exceeds the
    union when items overlap, i.e. under concurrency).

    ``review_last_batch_s`` is the number that matters for overlap: in a
    pipelined design the reviewer of the final batch cannot be hidden behind any
    later translation, so it is the irreducible tail.
    """
    stages = snapshot.get("stages") or {}
    split = {}
    for name in TRANSLATION_STAGES:
        record = stages.get(name) or {}
        per_item = (record.get("per_item") or {}).get("wall_time_s") or {}
        union = record.get("interval_union_s")
        active = record.get("active_wall_time_s")
        split[name] = {
            "span_s": round(float(record.get("wall_time_s") or 0.0), 4),
            "occupied_s": round(float(union if union is not None else 0.0), 4),
            "item_sum_s": round(float(active if active is not None else 0.0), 4),
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
    original_llm = core.call_llm
    original_timing = core._batch_timing
    events = []
    lock = threading.Lock()

    def record_timing(job, stage, batch_id, phase, **metadata):
        with lock:
            events.append({"stage": stage, "batch_id": batch_id, "phase": phase,
                           "at": time.monotonic(), **metadata})
        original_timing(job, stage, batch_id, phase, **metadata)

    def recording_compile(*args, **kwargs):
        packet = original_compile(*args, **kwargs)
        entry = {
            "current_batch": list(kwargs.get("current_batch") or []),
            "previous_target": [
                (item.get("target") if isinstance(item, dict) else str(item))
                for item in (kwargs.get("previous_target") or [])
            ],
        }
        with lock:
            contexts.append(entry)
        return packet

    _context.compile_context_packet = recording_compile
    core.call_llm = _fake_llm_factory(latency, calls)
    core._batch_timing = record_timing
    profiler = PipelineProfiler(run_id=f"concurrency-{concurrency}")
    try:
        begin = time.perf_counter()
        begin_cpu = time.process_time()
        result = core.translate_stage(
            state, job_id, glossary=[], provider="DeepSeek", api_key="k",
            model="m", target_lang="简体中文", style_rules="",
            enable_review=enable_review, use_tm=enable_review, batch_size=4,
            translation_concurrency=concurrency,
            knowledge_feedback_interval=1,
            use_translation_cache=False, use_knowledge_cache=False,
            document_profile=state["document_profile"], profiler=profiler)
        wall = time.perf_counter() - begin
        cpu = time.process_time() - begin_cpu
    finally:
        _context.compile_context_packet = original_compile
        core.call_llm = original_llm
        core._batch_timing = original_timing

    pairs = result.get("pairs") or []
    timings = _timing_summary(events)
    planned = result["batch_plan"]["batch_count"]
    if any(timings.get(name, {}).get("count") != planned
           for name in ("prepare", "commit_wait", "commit")):
        raise AssertionError("batch timing does not cover the complete run")
    return {
        "concurrency": concurrency,
        "wall_time_s": round(wall, 4),
        "process_cpu_s": round(cpu, 4),
        "process_cpu_percent_one_core": round(100 * cpu / wall, 2),
        "request_count": len(calls),
        "requests_by_role": {role: sum(call["role"] == role for call in calls)
                             for role in ("translation", "knowledge", "review")},
        "segment_count": len(pairs),
        "source_order_digest": _digest(p["source"] for p in pairs),
        "target_digest": _digest(p["target"] for p in pairs),
        "contexts": contexts,
        "batch_plan": result.get("batch_plan"),
        "stage_split": _stage_split(profiler.snapshot()),
        "batch_timing": events,
        "batch_timing_summary": timings,
        "knowledge_pending": len((result.get("knowledge_feedback_policy") or {}).get("pending") or []),
        "knowledge_observed_batches": len((result.get("knowledge_feedback_policy") or {}).get("observed_batches") or []),
        "blocking_findings": sum(f.get("severity") == "blocking" and not f.get("resolved")
                                 for f in result.get("findings") or []),
    }


def _overlap_projection(split: dict) -> dict:
    """How much review latency a translator/reviewer pipeline could hide.

    Serial model: generation then review, per batch, so the review cost is fully
    exposed.  Pipelined model: review of batch *i* runs while batch *i+1* is
    being generated, so only the last batch's review is irreducible.

    Both sides are occupied time (``interval_union_s``), never the stage span —
    with the span, ``generation`` and ``review`` are both the run length and the
    projection degenerates to an arbitrary constant.
    """
    generation = float((split.get("translation_generation") or {}).get("occupied_s") or 0.0)
    review = float((split.get("translation_semantic_review") or {}).get("occupied_s") or 0.0)
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
    by_batch = {tuple(item["current_batch"]): item for item in other}
    for base in baseline:
        candidate = by_batch.get(tuple(base["current_batch"]))
        if candidate is None:
            missing += 1
            continue
        if base.get("previous_target") != candidate.get("previous_target"):
            diverged += 1
    return {"diverged_batches": diverged, "missing_batches": missing,
            "total_batches": len(baseline)}


def _conclusion(runs: dict, comparison: dict) -> dict:
    """What this run established, stated as measurements — never as policy.

    The previous version returned a fixed sentence about the default staying at
    1, which read as a decision the script had reached.  It had not: the levels
    it was asked to run are an input, and the plateau it observes is a property
    of the fake provider plus serialised bookkeeping.
    """
    levels = sorted(runs)
    fastest = min(levels, key=lambda level: runs[level]["wall_time_s"])
    fastest_wall = float(runs[fastest]["wall_time_s"])
    total_batches = (next(iter(comparison.values()))["continuity"]["total_batches"]
                     if comparison else 0)
    return {
        "fastest_level": fastest,
        "fastest_wall_time_s": round(fastest_wall, 4),
        "speedup_vs_baseline": {level: item["speedup_vs_baseline"]
                                for level, item in comparison.items()},
        "saturation_level_within_10_percent": next(
            level for level in levels
            if float(runs[level]["wall_time_s"]) <= fastest_wall * 1.10),
        "target_digest_identical_across_levels": all(
            item["target_digest_matches"] for item in comparison.values()),
        "continuity_diverged_batches": {
            level: item["continuity"]["diverged_batches"]
            for level, item in comparison.items()},
        "continuity_total_batches": total_batches,
        "scope": (
            "Digest equality only shows this fixture's fake provider ignores the "
            "continuity context; it does not show a real provider's wording is "
            "unaffected. The wall-clock plateau is a harness bound (fake latency "
            "plus serialised bookkeeping), not a provider-latency bound."),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/translation-concurrency-benchmark")
    parser.add_argument("--segments", type=int, default=120)
    parser.add_argument("--sections", type=int, default=6)
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--concurrency", default="1,2,3,4,6")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run every level N times and report the median "
                             "sample, so one slow filesystem burst cannot be "
                             "mistaken for a concurrency effect")
    parser.add_argument("--review", action="store_true",
                        help="also run the independent review stage (enables TM "
                             "promotion) so the review/overlap projection is real")
    args = parser.parse_args()
    implementation = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    levels = [int(value) for value in str(args.concurrency).split(",") if value.strip()]
    repeats = max(1, int(args.repeat or 1))
    segments = _build_segments(args.segments, args.sections)
    section_ids = [f"section-{index}" for index in range(args.sections)]

    runs = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # Avoid charging lazy imports/initialization only to concurrency 1.
        warmup_segments = segments[:8]
        _run_once(tmp_dir, "warmup", warmup_segments, 1, 0.0, section_ids,
                  enable_review=args.review)
        for concurrency in levels:
            samples = [
                _run_once(tmp_dir, f"concurrency-{concurrency}-r{attempt}",
                          segments, concurrency, args.latency, section_ids,
                          enable_review=args.review)
                for attempt in range(repeats)
            ]
            walls = [float(sample["wall_time_s"]) for sample in samples]
            median = statistics.median(walls)
            # Report one real sample rather than a stitched-together average, so
            # the stage split always belongs to the wall time next to it.
            chosen = dict(min(samples, key=lambda item: abs(item["wall_time_s"] - median)))
            chosen["wall_time_samples_s"] = [round(value, 4) for value in walls]
            chosen["wall_time_spread_s"] = round(max(walls) - min(walls), 4)
            chosen["target_digest_stable_across_repeats"] = (
                len({sample["target_digest"] for sample in samples}) == 1)
            runs[concurrency] = chosen
            raw = runs[concurrency].pop("batch_timing")
            with (out_dir / f"concurrency-{concurrency}-batch-timing.jsonl").open("w", encoding="utf-8") as stream:
                for event in raw:
                    stream.write(json.dumps(event) + "\n")
            print(f"concurrency={concurrency}: {runs[concurrency]['wall_time_s']}s", flush=True)

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
        "implementation": implementation,
        "configuration_defaults": {
            "core_fallback": core.DEFAULT_TRANSLATION_CONCURRENCY,
            "shared_task_default": job_config.DEFAULT_TRANSLATION_CONCURRENCY,
            "presets": {name: values["translation_concurrency"]
                        for name, values in task_setup.STRATEGY_PRESETS.items()},
        },
        "fixture": {
            "segments": len(segments),
            "sections": args.sections,
            "fake_llm_latency_s": args.latency,
            "batch_size": 4,
            "review_enabled": args.review,
            "knowledge_feedback_interval": 1,
            "translation_cache_enabled": False,
            "knowledge_cache_enabled": False,
            "warmup_segments": len(warmup_segments),
            "repeats": repeats,
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
        "conclusion": _conclusion(runs, comparison),
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
