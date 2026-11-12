#!/usr/bin/env python3
"""Offline audit extension: allowlisted log aggregates and real-runtime mock sweeps.

No credentials are read, no network provider is invoked, and user jobs are never
written. Reuses translation_efficiency_benchmark's provider/prompt extraction.
Run with --out DIR; optionally --job-dir DIR (repeatable) for read-only logs.
All emitted data is numeric/identifiers; source and target text stay in memory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
import json
from pathlib import Path
import platform
import re
import statistics
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]


def distribution(values):
    values = sorted(values)
    if not values:
        return None
    return {"n": len(values), "sum": sum(values), "mean": statistics.fmean(values),
            "min": values[0], "p50": values[round(.5 * (len(values) - 1))],
            "p95": values[round(.95 * (len(values) - 1))], "max": values[-1]}


def occupancy(intervals, bounds=None):
    events = []
    for start, end in intervals:
        if bounds:
            start, end = max(start, bounds[0]), min(end, bounds[1])
        if end > start:
            events.extend([(start, 1), (end, -1)])
    events.sort()
    active = peak = 0
    union = work = 0.
    last = None
    for stamp, delta in sorted(events):
        if last is not None:
            work += (stamp - last) * active
            union += (stamp - last) if active else 0.
        active += delta
        peak = max(peak, active)
        last = stamp
    span = bounds[1] - bounds[0] if bounds else (events[-1][0] - events[0][0] if events else 0.)
    return {"sum_s": work, "union_s": union, "span_s": span, "peak": peak,
            "average_active": work / span if span else None}


def batch_metrics(points):
    grouped = defaultdict(dict)
    for p in points:
        grouped[str(p["batch_id"])][p["phase"]] = p["monotonic"]
    workers = [(p["worker_started"], p["result_ready"]) for p in grouped.values()
               if "worker_started" in p and "result_ready" in p]
    return {"batches_observed": len(grouped), "workers": occupancy(workers),
            "queue_s": distribution([p["worker_started"] - p["queued"] for p in grouped.values()
                                     if "worker_started" in p and "queued" in p]),
            "ready_to_commit_s": distribution([p["commit_started"] - p["result_ready"]
                for p in grouped.values() if "commit_started" in p and "result_ready" in p]),
            "commit_s": distribution([p["commit_finished"] - p["commit_started"]
                for p in grouped.values() if "commit_finished" in p and "commit_started" in p])}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def collect_job(directory):
    import core
    state = read_json(directory / "state.json")
    cfg = state.get("pipeline_config") or {}
    perf = state.get("performance") or {}
    stages = perf.get("stages") or {}
    rows = []
    for path in sorted(directory.glob(".performance/runs/*/summary.json")):
        summary = read_json(path)
        events = read_lines(path.with_name("requests.jsonl"))
        calls = [e for e in events if e.get("event") == "model_call"]
        finished = [e for e in events if e.get("event") == "request_finished"]
        intervals = [(e["started_monotonic"], e["finished_monotonic"]) for e in finished]
        by_role = {}
        for role in sorted({c.get("role", "unknown") for c in calls}):
            cs = [c for c in calls if c.get("role", "unknown") == role]
            by_role[role] = {"calls": len(cs), "latency_s": distribution([c["duration_s"] for c in cs]),
                            **{k: sum(c.get(k) or 0 for c in cs) for k in
                               ("input_chars", "output_chars", "prompt_tokens", "completion_tokens", "reasoning_chars")}}
        headers = [e for e in events if e.get("event") == "http_response_headers"]
        translation_points = []
        plan_epoch = 0
        for e in events:
            if e.get("event") == "translation_plan":
                plan_epoch += 1
            if e.get("event") == "batch_timing" and e.get("stage") == "translation":
                translation_points.append({**e, "batch_id": f"{plan_epoch}:{e['batch_id']}"})
        annotation_points = [e for e in events if e.get("event") == "batch_timing" and e.get("stage") == "annotation"]
        row = {"run_id": path.parent.name, "wall_s": summary["wall_time_s"],
               "cpu_s": summary.get("process_cpu_time_s"), "status": summary.get("status"),
               "summary_calls": summary.get("model_metrics", {}).get("total_calls"),
               "calls_with_records": len(calls), "paired_requests": len(finished),
               "request_occupancy": occupancy(intervals), "by_role": by_role,
               "auxiliary_by_batch_presence": {label: {
                   "calls": len(group), **{k: sum(c.get(k) or 0 for c in group) for k in
                     ("duration_s", "prompt_tokens", "completion_tokens", "output_chars", "reasoning_chars")}}
                   for label, group in [("before_translation", [c for c in calls if c.get("role") == "auxiliary" and c.get("batch_id") is None]),
                                        ("batch_feedback", [c for c in calls if c.get("role") == "auxiliary" and c.get("batch_id") is not None])]},
               "http_attempts": sum(e.get("event") == "http_attempt_started" for e in events),
               "http_statuses": dict(Counter(e.get("status_code") for e in headers)),
               "failed_calls": sum(c.get("status") == "error" for c in calls),
               "ttft_observed": sum(c.get("ttft_s") is not None for c in calls),
               "reasoning_tokens_observed": sum(c.get("reasoning_tokens") is not None for c in calls),
               "translation_batches": batch_metrics(translation_points),
               "annotation_batches": batch_metrics(annotation_points),
               "milestones": [{k: e.get(k) for k in ("phase", "monotonic", "timestamp")}
                              for e in events if e.get("event") == "milestone"],
               "build": [{k: e.get(k) for k in ("app_version", "build_id", "build_identity_scope")}
                         for e in events if e.get("event") == "run_configuration"],
               "plans": [{k: e.get(k) for k in ("event", "monotonic", "concurrency", "batch_count")}
                         for e in events if e.get("event") in ("translation_plan", "annotation_plan")]}
        if path.parent.name == perf.get("run_id"):
            row["stages"] = {name: {k: s.get(k) for k in
                ("started_monotonic", "finished_monotonic", "wall_time_s", "item_wall_sum_s", "interval_union_s", "api_latency_s", "retry_count")}
                for name, s in stages.items()}
            for name, s in stages.items():
                items = s.get("items") or []
                spans = [(i["started_monotonic"], i["finished_monotonic"]) for i in items
                         if "started_monotonic" in i and "finished_monotonic" in i]
                # Only assign calls contained in a measured item span. Container
                # lifetimes overlap all phases and cannot identify request roles.
                cs = [c for c in calls if c.get("started_monotonic") is not None
                      and any(str(c.get("batch_id")) == str(i.get("item_id", "")).removeprefix("batch-")
                              and i.get("started_monotonic", 0) <= c["started_monotonic"] < i.get("finished_monotonic", 0)
                              for i in items)]
                row["stages"][name]["calls_started_in_items"] = {
                    "calls": len(cs), "failed": sum(c.get("status") == "error" for c in cs),
                    "latency_s": distribution([c["duration_s"] for c in cs]),
                    **{k: sum(c.get(k) or 0 for c in cs) for k in
                       ("prompt_tokens", "completion_tokens", "output_chars", "reasoning_chars")}}
                row["stages"][name]["item_source_chars"] = distribution([
                    i.get("metadata", {}).get("source_chars", 0) for i in items])
            gen_items = stages.get("translation_generation", {}).get("items", [])
            gen_requests = [i for i in finished if i.get("role") == "translation" and
                            any(str(i.get("batch_id")) == str(g.get("item_id", "")).removeprefix("batch-")
                                and g.get("started_monotonic", 0) <= i["started_monotonic"] < g.get("finished_monotonic", 0)
                                for g in gen_items)]
            row["generation_request_occupancy"] = occupancy([
                (i["started_monotonic"], i["finished_monotonic"]) for i in gen_requests])
            container = stages.get("translation_generation") or {}
            if container.get("finished_monotonic") is not None:
                row["translation_container_request_occupancy"] = occupancy(intervals,
                    (container["started_monotonic"], container["finished_monotonic"]))
            points = translation_points
            if points:
                bounds = (min(e["monotonic"] for e in points), max(e["monotonic"] for e in points))
                row["translation_request_occupancy"] = occupancy(intervals, bounds)
            row["runtime_io"] = {k: v for k, v in stages.get("runtime_telemetry", {}).get("metadata", {}).items()
                                 if isinstance(v, (int, float))}
        rows.append(row)
    cost = state.get("translation_prompt_cost") or {}
    packets = state.get("context_packet_log") or []
    runtime = read_lines(directory / "runtime_events.jsonl")
    sources = state.get("paras") or []
    candidate_plans = {}
    for cap in (2000, 4000, 6000, 8000):
        batches = core.make_paragraph_batches(sources,
            (state.get("segmentation") or {}).get("paragraph_ranges") or [], max_chars=cap,
            semantic_units=state.get("semantic_units") or state.get("section_digests") or None)
        candidate_plans[str(cap)] = {"batches": len(batches),
            "source_chars": distribution([sum(map(len, b)) for b in batches]),
            "items": distribution([len(b) for b in batches]),
            "singleton_batches": sum(len(b) == 1 for b in batches)}
    return {"job_id": directory.name, "segments": len(sources),
            "candidate_paragraph_plans_static_only": candidate_plans,
            "runtime_boundaries": [{k: e.get(k) for k in ("timestamp", "event", "stage", "operation")}
                for e in runtime if e.get("event") in ("worker_started", "document_processing", "terminology", "translation", "annotation")],
            "source_chars": sum(map(len, state.get("paras") or [])),
            "pairs": len(state.get("pairs") or []), "profiler_run_id": perf.get("run_id"),
            "target_chars": sum(len(p.get("target") or "") for p in state.get("pairs") or []),
            "config": {k: cfg.get(k) for k in ("translation_concurrency", "batch_size", "max_batch_chars", "batch_strategy", "enable_review", "enable_annotate", "knowledge_feedback")},
            "prompt_cost": {k: cost.get(k) for k in ("requests", "failed_requests", "segments", "prompt_chars", "output_chars", "source_chars", "category_chars", "requests_by_segment_count")},
            "packet_source_chars": distribution([p.get("current_batch_chars", 0) for p in packets]),
            "runs": rows}


def fixture(count=240):
    sources = [f"Record {i}: {{record_id}} the archive stores 12 manuscripts in the northern library. "
               "The archivist verifies each document carefully before moving it to the reading room."
               for i in range(count)]
    targets = [f"记录{i}：{{record_id}} 档案馆在北方图书馆保存了12份手稿。档案员将每份文件移至阅览室之前都会仔细核对。" for i in range(count)]
    return sources, targets


def mock_run(label, root, *, concurrency=4, chars=2000, paragraph=False,
             review=False, feedback=False, inject_error=False, latency=.02, qa=True):
    import core
    from folith import context
    from folith.performance import PipelineProfiler
    from translation_efficiency_benchmark import DeterministicProvider
    sources, targets = fixture()
    reference = dict(zip(sources, targets))
    provider = DeterministicProvider(reference, latency, 0.)
    calls, points, contexts = [], [], []
    lock = threading.Lock()
    original_compile = context.compile_context_packet
    original_paragraph_batches = core.make_paragraph_batches

    def compile_packet(*args, **kwargs):
        packet = original_compile(*args, **kwargs)
        current = tuple(kwargs.get("current_batch") or [])
        with lock:
            contexts.append((current, tuple(str(x.get("target", "")) if isinstance(x, dict) else str(x)
                for x in kwargs.get("previous_target") or [])))
        return packet

    def call(p, key, model, system, user, **kwargs):
        role = provider.classify(system, user)
        start = time.perf_counter()
        result = provider(p, key, model, system, user, **kwargs)
        if role == "translation":
            # The older shared extractor treats paragraph headings as source
            # continuation lines. Support the actual current paragraph renderer.
            tail = user.split(context.CURRENT_BATCH_TITLE, 1)[-1]
            requested = re.findall(r"^\d+\. (.*)$", tail, re.M)
            result = json.dumps([reference[s] for s in requested], ensure_ascii=False)
        if role == "repair":
            repair_sources = re.findall(r"^\d+\. 原文：(.*)$", user, re.M)
            result = json.dumps([reference[s] for s in repair_sources], ensure_ascii=False)
        if inject_error and role == "translation":
            result = result.replace("{record_id}", "")
        with lock:
            calls.append({"role": role, "start": start, "end": time.perf_counter(),
                          "input_chars": len(system) + len(user), "output_chars": len(result)})
        return result

    state = core.new_job_state("synthetic.txt")
    state.update(paras=sources, segmentation={"paragraph_ranges": [{"start": i, "end": i + 1, "paragraph_index": i} for i in range(len(sources))]},
                 pipeline_config={"batch_strategy": "paragraph" if paragraph else "sentence"})
    profiler = PipelineProfiler(run_id=label)
    with ExitStack() as stack:
        if not qa:
            # Controlled benchmark ablation only, never a production setting.
            stack.enter_context(patch.object(core, "check_translation_batch", lambda *a, **kw: []))
        for owner, name, value in [(core, "OUTPUT_DIR", root / label), (core, "call_llm", call),
                (core, "PARAGRAPH_BATCH_MAX_CHARS", chars),
                (core, "make_paragraph_batches", lambda *a, **kw:
                    original_paragraph_batches(*a, **{**kw, "max_chars": chars})),
                (context, "compile_context_packet", compile_packet),
                (core, "_batch_timing", lambda job, stage, batch_id, phase, **kw:
                    points.append(dict(batch_id=batch_id, phase=phase, monotonic=time.perf_counter())))]:
            stack.enter_context(patch.object(owner, name, value))
        core.OUTPUT_DIR.mkdir(parents=True)
        wall_start, cpu_start = time.perf_counter(), time.process_time()
        result = core.translate_stage(state, label, [], "Benchmark", "offline", "offline",
            "简体中文", "", review, use_tm=False, batch_size=4, max_batch_chars=2400,
            translation_concurrency=concurrency, knowledge_feedback=feedback,
            use_translation_cache=False, use_knowledge_cache=False, profiler=profiler)
        wall, cpu = time.perf_counter() - wall_start, time.process_time() - cpu_start
    pairs = result.get("pairs") or []
    generated = [c for c in calls if c["role"] == "translation"]
    gen_occ = occupancy([(c["start"], c["end"]) for c in generated])
    worker = batch_metrics(points)
    worker["workers"]["idle_capacity_s"] = max(0., concurrency * wall - worker["workers"]["sum_s"])
    snapshot = profiler.snapshot()["stages"]
    row = {"label": label, "configured_concurrency": concurrency, "plan": result["batch_plan"],
           "wall_s": wall, "cpu_s": cpu, "source_chars": sum(map(len, sources)),
           "source_chars_per_s": sum(map(len, sources)) / wall, "requests": len(calls),
           "roles": dict(Counter(c["role"] for c in calls)),
           "request_occupancy": occupancy([(c["start"], c["end"]) for c in calls]),
           "generation_occupancy": gen_occ, "workers": worker,
           "input_tokens_estimate": sum((c["input_chars"] + 3)//4 for c in calls),
           "output_tokens_estimate": sum((c["output_chars"] + 3)//4 for c in generated),
           "source_chars_per_batch": distribution([sum(map(len, c[0])) for c in contexts]),
           "quality": {"pairs": len(pairs), "empty": sum(not p.get("target") for p in pairs),
               "protocol_fallback_batches": len(result.get("translation_failures") or []),
               "source_order_correct": [p["source"] for p in pairs] == sources,
               "reference_matches": sum(p.get("target") == reference.get(p["source"]) for p in pairs),
               "placeholder_errors": sum("{record_id}" not in p.get("target", "") for p in pairs)},
           "prompt_cost": result.get("translation_prompt_cost"),
           "stages": {k: {f: v.get(f) for f in ("item_wall_sum_s", "interval_union_s", "wall_time_s")}
                      for k, v in snapshot.items()}}
    # Full context kept only for comparison in memory; never emitted.
    return row, dict(contexts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--job-dir", action="append", type=Path, default=[])
    parser.add_argument("--logs-only", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = [collect_job(p) for p in args.job_dir]
    (args.out / "real-jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=2) + "\n")
    if args.logs_only:
        return
    rows = []
    with tempfile.TemporaryDirectory(prefix="folith-perf-audit-") as tmp:
        root = Path(tmp)
        baseline_contexts = None
        for c in (1, 2, 4, 6, 8):
            row, contexts = mock_run(f"A-c{c}", root, concurrency=c)
            if baseline_contexts is None:
                baseline_contexts = contexts
            row["context_diverged_batches"] = sum(v != baseline_contexts.get(k) for k, v in contexts.items())
            rows.append(row)
        for chars in (2000, 4000, 6000, 8000):
            rows.append(mock_run(f"B-p{chars}", root, chars=chars, paragraph=True)[0])
        rows.append(mock_run("C-generation-only", root, qa=False)[0])
        for label, review, feedback, error in [("C-qa", False, False, False),
                ("C-repair", False, False, True), ("C-review", True, False, False),
                ("C-full", True, True, True)]:
            rows.append(mock_run(label, root, review=review, feedback=feedback, inject_error=error)[0])
    payload = {"kind": "offline mock; 20ms per call; no API throughput inference",
               "python": platform.python_version(), "platform": platform.system(), "runs": rows}
    (args.out / "mock-matrix.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps([{k: r[k] for k in ("label", "wall_s", "requests", "roles", "quality")} for r in rows], indent=2))


if __name__ == "__main__":
    main()
