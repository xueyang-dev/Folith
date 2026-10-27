#!/usr/bin/env python3
"""Phase 2 offline research against an explicitly selected runtime checkout.

No live mode. All provider calls are replaced by reference responses or a fake
SDK. --job-dir reads an existing job, but only allowlisted numeric aggregates
leave temporary directories. Queue alternatives are research-only generators.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
import copy
from datetime import datetime
import heapq
import json
from pathlib import Path
import re
import random
import statistics
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch


def dist(values):
    xs = sorted(values)
    if not xs:
        return None
    return dict(n=len(xs), sum=sum(xs), mean=statistics.fmean(xs),
                p50=xs[round(.5*(len(xs)-1))], p95=xs[round(.95*(len(xs)-1))], max=xs[-1])


def queue_replay(durations, commits=None, *, workers=2, capacity=2, ordered_refill=False):
    """Discrete-event counterfactual, fixed observed service times, no network.

    capacity includes running + ready + committing tasks. A capacity equal to
    workers deliberately cannot hide an early straggler without durable spill.
    """
    commits = commits or [0.] * len(durations)
    n = len(durations)
    events, ready = [], set()
    submitted = committed = active = 0
    committing = False
    now = 0.
    peak_ready = peak_outstanding = 0
    starts, ends = {}, {}

    def fill():
        nonlocal submitted, active, peak_outstanding
        while submitted < n and active < workers and submitted-committed < capacity:
            if ordered_refill and submitted >= committed+workers:
                break
            starts[submitted] = now
            heapq.heappush(events, (now+durations[submitted], 0, submitted))
            submitted += 1
            active += 1
            peak_outstanding = max(peak_outstanding, submitted-committed)

    fill()
    while events:
        now, kind, idx = heapq.heappop(events)
        if kind == 0:
            active -= 1
            ends[idx] = now
            ready.add(idx)
        else:
            ready.remove(idx)
            committed += 1
            committing = False
        peak_ready = max(peak_ready, len(ready))
        if committed in ready and not committing:
            committing = True
            heapq.heappush(events, (now+commits[committed], 1, committed))
        fill()
    assert committed == n
    return dict(wall_s=now, peak_ready=peak_ready, peak_outstanding=peak_outstanding,
                worker_idle_s=workers*now-sum(durations),
                starts=[starts[i] for i in range(n)], ends=[ends[i] for i in range(n)])


def completion_results(tasks, job_id, provider, api_key, model, target_lang, workers,
                       *, core, capacity, metrics):
    """Experimental completion collection + ordered yield, bounded total work.

    Does NOT implement durable result staging. Its speculative-work difference
    on save failure is measured, not hidden as semantic equivalence.
    """
    pending, ready = {}, {}
    submitted = next_commit = 0
    executor = ThreadPoolExecutor(max_workers=workers)

    def invoke(task):
        _, _, pairs, indexes = task
        return task, core.annotate_batch([pairs[i] for i in indexes], target_lang, provider, api_key, model) if indexes else None

    def fill():
        nonlocal submitted
        while submitted < len(tasks) and len(pending) < workers and submitted-next_commit < capacity:
            if core._runtime_cancel_requested(job_id):
                raise RuntimeError("任务已请求取消")
            pending[executor.submit(invoke, tasks[submitted])] = submitted
            submitted += 1
            metrics["submitted"] = submitted
            metrics["peak_outstanding"] = max(metrics.get("peak_outstanding", 0), submitted-next_commit)

    try:
        fill()
        while next_commit < len(tasks):
            if core._runtime_cancel_requested(job_id):
                raise RuntimeError("任务已请求取消")
            if next_commit in ready:
                yield ready.pop(next_commit)
                next_commit += 1
                fill()
                continue
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                ready[pending.pop(future)] = future.result()
            metrics["peak_ready"] = max(metrics.get("peak_ready", 0), len(ready))
            fill()
    finally:
        for f in pending:
            f.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def version_accept(result, current_versions, *, glossary_revision, qa_ready):
    """Research contract: compare existing revision identities, no text hashes."""
    return bool(result.get("status") == "ready" and qa_ready and result["glossary_revision"] == glossary_revision
                and all(current_versions.get(k) == v for k, v in result["inputs"].items()))


def overlap_model(translation, annotation, *, capacity, overlap):
    """Conservative DAG: sequential reviewed commits, then per-batch annotation.

    A translation duration includes its necessary review. Translation remains
    sequential in BOTH variants to preserve committed-context dependencies.
    Fixed service times and instantaneous durable commit are assumptions.
    """
    events, annotation_ready = [], []
    next_translation = finished_translation = finished_annotation = active = 0
    translation_active = False
    now = 0.
    peak = 0
    n = len(translation)
    while finished_annotation < n:
        if not translation_active and next_translation < n and active < capacity:
            heapq.heappush(events, (now+translation[next_translation], "translation", next_translation))
            next_translation += 1
            active += 1
            translation_active = True
        while annotation_ready and active < capacity and (overlap or finished_translation == n):
            i = annotation_ready.pop(0)
            heapq.heappush(events, (now+annotation[i], "annotation", i))
            active += 1
        peak = max(peak, active)
        now, role, index = heapq.heappop(events)
        active -= 1
        if role == "translation":
            translation_active = False
            finished_translation += 1
            annotation_ready.append(index)
        else:
            finished_annotation += 1
    return dict(wall_s=now, peak_requests=peak, committed_batches=finished_translation,
                annotated_batches=finished_annotation)


def annotation_probe(core, root, variant, *, failure=None):
    state = core.new_job_state("synthetic.docx")
    state["pipeline_config"] = {"translation_concurrency": 4}
    state["pairs"] = [{"source": f"Section {i} discusses Urban Air Mobility in Germany.",
                       "target": f"第{i}节讨论德国的城市空中交通。"} for i in range(12)]
    initial = copy.deepcopy(state)
    failure_mode = [failure]
    calls, saved, metrics = [], [], {}
    cancelled = threading.Event()
    state_at_save = []

    def annotate(pairs, *_):
        idx = int(re.search(r"Section (\d+)", pairs[0]["source"])[1])
        calls.append(idx)
        time.sleep(.06 if idx in (0, 6) else .002)
        if failure_mode[0] == "parse" and idx == 0:
            return core.AnnotationBatchResult(status="parse_failed", error="controlled parse failure")
        return core.AnnotationBatchResult([
            dict(seg=0, src="Urban Air Mobility", tgt="城市空中交通", type="domain", note="专业术语"),
            dict(seg=0, src="Germany", tgt="德国", type="domain", note="地名")])

    def save(job, current):
        if failure_mode[0] == "disk":
            raise OSError("controlled disk failure")
        saved.append(current.get("annotations_done_offset"))
        state_at_save[:] = [copy.deepcopy(current)]
        if failure_mode[0] == "cancel":
            cancelled.set()

    with ExitStack() as stack:
        for name, value in [("OUTPUT_DIR", root), ("ANNOT_BATCH_SIZE", 1), ("annotate_batch", annotate),
                ("save_job_state", save), ("_runtime_cancel_requested", lambda job: cancelled.is_set())]:
            stack.enter_context(patch.object(core, name, value))
        if variant != "A":
            capacity = 12 if variant == "B" else (2 if variant == "C2" else 4)
            stack.enter_context(patch.object(core, "_annotation_results", lambda *a:
                completion_results(*a, core=core, capacity=capacity, metrics=metrics)))
        start = time.perf_counter()
        error = None
        try:
            core.annotate_stage(state, "offline-annotation", [], "DeepSeek", "offline", "offline", "简体中文")
        except (RuntimeError, OSError) as exc:
            error = type(exc).__name__
        elapsed = time.perf_counter()-start
        calls_before = list(calls)
        commits_before = list(saved)
        metrics_before = copy.deepcopy(metrics)
        durable_before = state_at_save[0].get("annotations_done_offset", 0) if state_at_save else 0
        recovered = None
        if failure is not None:
            failure_mode[0] = None
            cancelled.clear()
            recovered = copy.deepcopy(state_at_save[0] if state_at_save else initial)
            core.annotate_stage(recovered, "offline-annotation", [], "DeepSeek", "offline", "offline", "简体中文")
        resume_calls = calls[len(calls_before):]
    result = {k: copy.deepcopy(state.get(k)) for k in
              ("annotations", "annotation_batches", "annotations_done", "annotations_done_offset")}
    return dict(variant=variant, failure=failure, wall_s=elapsed, calls=len(calls_before),
                commits=commits_before, error=error, metrics=metrics_before,
                durable_offset=durable_before,
                resume_complete=bool(recovered and recovered.get("annotations_done")) if failure else None,
                resume_calls=len(resume_calls), repeated_calls_after_resume=len(set(calls_before)&set(resume_calls))), result


def configuration_probe(core):
    from folith.application.settings import SettingsSources, resolve_effective_settings
    from folith.application.task_setup import STRATEGY_PRESETS
    from folith.qt import adapter
    tree = ast.parse(Path(core.__file__).read_text())
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) and t.value.id == "pipeline_config"
                and isinstance(t.slice, ast.Constant) and t.slice.value == "batch_strategy" for t in n.targets))
    expr = compile(ast.Expression(assignment.value), "<runtime-strategy-expression>", "eval")
    rows = []
    for preset, strategy in STRATEGY_PRESETS.items():
        effective = resolve_effective_settings(SettingsSources(
            explicit=dict(provider="OpenAI", model="offline", api_key="offline"),
            job_state={**strategy, "filename": "synthetic.txt"}))
        kwargs = effective.to_job_config().to_pipeline_kwargs()
        with patch.object(core, "load_provider_config", lambda: {}):
            qt_kwargs = adapter.pipeline_kwargs(adapter.EngineConfig(provider="OpenAI", model="offline", api_key="offline"),
                                               {**strategy, "filename": "synthetic.txt"})
        compared = ("translation_concurrency", "batch_size", "max_batch_chars", "enable_review", "knowledge_feedback")
        assert all(kwargs[k] == qt_kwargs[k] for k in compared)
        batch_strategy = eval(expr, {"_batch_strategy": core._batch_strategy,
            "DEFAULT_BATCH_STRATEGY": core.DEFAULT_BATCH_STRATEGY},
            dict(saved_runtime_config={}, state={}, batch_size=kwargs["batch_size"], max_batch_chars=kwargs["max_batch_chars"]))
        rows.append(dict(preset=preset, **{k: kwargs.get(k) for k in
            ("translation_concurrency", "batch_size", "max_batch_chars", "enable_review", "knowledge_feedback")},
            strategy=batch_strategy, qt_adapter_matches=True))
    return rows


def wire_probe(core):
    captured = []
    class FakeSDK:
        def __init__(self, **kwargs):
            self.http = kwargs.get("http_client")
            self.chat = SimpleNamespace(completions=self)
        def create(self, **body):
            captured.append(body)
            return SimpleNamespace(id="offline", choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))], usage=None)
        def close(self):
            if self.http:
                self.http.close()
    rows = []
    core.reset_llm_clients()
    try:
        with patch.object(core, "OpenAI", FakeSDK):
            for mode in ("provider_default", "thinking_disabled"):
                core.set_llm_reasoning_modes(mode, mode)
                for role in ("translation", "annotation", "auxiliary", "review"):
                    token = core._usage.CURRENT_ROLE.set(role)
                    try:
                        core.call_llm("自定义中转站", "offline", "dots3-note-prev", "s", "u", base_url="http://127.0.0.1:9/v1")
                    finally:
                        core._usage.CURRENT_ROLE.reset(token)
                    body = captured[-1]
                    rows.append(dict(mode=mode, role=role, thinking=body.get("extra_body"),
                                     reasoning_effort=body.get("reasoning_effort")))
    finally:
        core.set_llm_reasoning_modes(None, None)
        core.reset_llm_clients()
    return rows


def historical_analysis(job_dir):
    state = json.loads((job_dir/"state.json").read_text())
    run = job_dir/".performance/runs"/state["performance"]["run_id"]
    events = [json.loads(x) for x in (run/"requests.jsonl").read_text().splitlines()]
    runtime = [json.loads(x) for x in (job_dir/"runtime_events.jsonl").read_text().splitlines()]
    term_start = datetime.fromisoformat(next(e["timestamp"] for e in runtime if e.get("event") == "terminology"))
    calls = [e for e in events if e.get("event") == "model_call"]
    stages = state["performance"]["stages"]
    def belongs(c, name):
        return any(str(c.get("batch_id")) == str(i.get("item_id", "")).removeprefix("batch-")
            and i.get("started_monotonic", 0) <= c.get("started_monotonic", -1) < i.get("finished_monotonic", 0)
            for i in stages.get(name, {}).get("items", []))
    groups = defaultdict(list)
    for c in calls:
        role = c.get("role")
        if role == "translation":
            role = "repair" if belongs(c, "translation_repair") else "translation"
        elif role == "auxiliary":
            if c.get("batch_id") is not None:
                role = "knowledge_feedback"
            else:
                role = "terminology" if datetime.fromisoformat(c["started_at"]) >= term_start else "understanding"
        groups[role].append(c)
    roles = {}
    for role, cs in groups.items():
        roles[role] = dict(calls=len(cs), latency_s=dist([c["duration_s"] for c in cs]),
            failures=sum(c["status"] == "error" for c in cs),
            failure_types=dict(Counter(c.get("error_type") for c in cs if c["status"] == "error")),
            http_attempts=sum(c.get("http_attempts", 0) for c in cs),
            input_chars=sum(c.get("input_chars", 0) for c in cs),
            output_chars=sum(c.get("output_chars", 0) for c in cs),
            reasoning_mode=dict(Counter(c.get("metadata", {}).get("reasoning_mode", "unreported") for c in cs)),
            **{key: dict(total=sum(c.get(key) or 0 for c in cs) if any(c.get(key) is not None for c in cs) else None,
                         reported=sum(c.get(key) is not None for c in cs), total_calls=len(cs))
               for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "reasoning_chars")})
    points = defaultdict(dict)
    for e in events:
        if e.get("event") == "batch_timing" and e.get("stage") == "annotation":
            points[int(e["batch_id"])][e["phase"]] = e["monotonic"]
    ordered = [points[k] for k in sorted(points)]
    durations = [p["result_ready"]-p["worker_started"] for p in ordered]
    commits = [p["commit_finished"]-p["commit_started"] for p in ordered]
    replay = {label: queue_replay(durations, commits, capacity=cap, ordered_refill=(label=="A"))
              for label, cap in [("A", 2), ("B", len(durations)), ("C2", 2), ("C4", 4)]}
    for result in replay.values():
        result.pop("starts");result.pop("ends")
    return state, dict(run_id=run.name, roles=roles, annotation_services_s=durations,
        annotation_commits_s=commits, fixed_service_replays=replay,
        observed_annotation_window_s=max(p["commit_finished"] for p in ordered)-min(p["worker_started"] for p in ordered),
        current_annotation_count=sum(len(v) for v in (state.get("annotations") or {}).values()),
        current_annotation_status=state.get("annotations_status"),
        current_annotation_types=dict(Counter(a["type"] for xs in (state.get("annotations") or {}).values() for a in xs)))


def batch_replay(core, original, root, strategy, cap, *, use_tm=False, resume=False,
                 capture_state=None, resume_seed=None):
    from folith import context
    state = core.new_job_state("offline.pdf")
    for key in ("paras", "segmentation", "document_profile", "document_synopsis", "section_digests", "semantic_units", "entity_registry",
                "source_paras", "source_paragraphs", "source_cleanup", "extraction_report", "document_ir"):
        state[key] = copy.deepcopy(original.get(key) or ({} if key in ("segmentation", "document_profile", "document_synopsis") else []))
    state["pipeline_config"] = {"batch_strategy": strategy}
    reference = {p["source"]: p["target"] for p in original["pairs"]}
    calls, observations = [], []
    lock = threading.Lock()
    original_batcher = core.make_paragraph_batches
    if resume:
        batches = original_batcher(state["paras"], (state["segmentation"] or {}).get("paragraph_ranges"), max_chars=cap,
            semantic_units=state["semantic_units"] or state["section_digests"] or None) if strategy == "paragraph" else core.make_batches(state["paras"], 6, 4800, semantic_units=state["semantic_units"] or None)
        cut = sum(len(b) for b in batches[:max(1, len(batches)//2)])
        state["pairs"] = copy.deepcopy(resume_seed["pairs"][:cut])
        state["findings"] = [copy.deepcopy(f) for f in resume_seed["findings"]
            if isinstance(f.get("segment_index"), int) and f["segment_index"] < cut]
    else:
        cut = 0
    def call(provider, key, model, system, user, **kwargs):
        if "你是一位专业翻译人员" in system and "问题清单" not in user:
            tail = user.split(context.CURRENT_BATCH_TITLE, 1)[-1]
            sources = re.findall(r"^\d+\. (.*)$", tail, re.M)
            outputs = [reference[s] for s in sources]
            role = "translation"
            with lock:
                observations.append((len(sources), sum(map(len, sources)), sum(map(len, outputs))))
            response = json.dumps(outputs, ensure_ascii=False)
        elif "问题清单" in user:
            sources = re.findall(r"^\d+\. 原文：(.*)$", user, re.M)
            response = json.dumps([reference[s] for s in sources], ensure_ascii=False)
            role = "repair"
        elif "翻译流知识抽取器" in system:
            response, role = "[]", "knowledge"
        else:
            response, role = '{"findings":[],"evidence_requests":[]}', "review"
        with lock:
            calls.append(dict(role=role, input_chars=len(system)+len(user), output_chars=len(response)))
        return response
    glossary = copy.deepcopy(original.get("glossary") or [])
    with ExitStack() as stack:
        for name, value in [("OUTPUT_DIR", root), ("call_llm", call), ("PARAGRAPH_BATCH_MAX_CHARS", cap),
                ("make_paragraph_batches", lambda *a, **kw: original_batcher(*a, **{**kw, "max_chars": cap}))]:
            stack.enter_context(patch.object(core, name, value))
        state["target_lang"] = "简体中文"
        state["style_rules"] = str(original.get("style_rules") or "")
        core.save_job_state("offline-batch", state)
        if use_tm:
            tm = {}
            for p in original["pairs"][::7]:
                core.tm_put(tm, p["source"], p["target"], "简体中文")
            core.save_tm(tm, core.tm_project_id(state))
        started = time.perf_counter()
        result = core.translate_stage(state, "offline-batch", glossary, "自定义中转站", "offline", "dots3-note-prev",
            "简体中文", state["style_rules"], enable_review=False, use_tm=use_tm,
            document_profile=state["document_profile"], batch_size=6, max_batch_chars=4800,
            translation_concurrency=4, knowledge_feedback=True, use_translation_cache=False, use_knowledge_cache=False)
        elapsed = time.perf_counter()-started
    pairs = result["pairs"]
    if capture_state is not None:
        capture_state.update(pairs=copy.deepcopy(pairs), findings=copy.deepcopy(result.get("findings") or []))
    generated = [c for c in calls if c["role"] == "translation"]
    return dict(strategy=strategy, cap=cap, use_tm=use_tm, resume_segments=cut,
        planned_batches=result["batch_plan"]["batch_count"], roles=dict(Counter(c["role"] for c in calls)),
        wall_s_local=elapsed, generation_input_chars=sum(c["input_chars"] for c in generated),
        generation_input_tokens_estimate=sum((c["input_chars"]+3)//4 for c in generated),
        output_chars=dist([c["output_chars"] for c in generated]),
        batch_source_chars=dist([o[1] for o in observations]), batch_items=dist([o[0] for o in observations]),
        complete=len(pairs)==len(original["paras"]), alignment=[p["source"] for p in pairs]==[s.replace('\n',' ') for s in original["paras"]],
        reference_matches=sum(p["target"]==reference[p["source"]] for p in pairs),
        empty=sum(not p.get("target") for p in pairs), tm_used=result.get("tm_used_count", 0),
        protocol_fallback_batches=len(result.get("translation_failures") or []),
        findings_by_severity=dict(Counter(f.get("severity") for f in result.get("findings") or [])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    sys.path.insert(0, str(args.runtime_root))
    import core
    args.out.mkdir(parents=True, exist_ok=True)
    payload = dict(kind="offline research: fake SDK, reference replay and fixed-service counterfactual",
                   runtime_core_lines=len(Path(core.__file__).read_text().splitlines()),
                   configuration=configuration_probe(core), wire=wire_probe(core))
    payload["overlap_model"] = [dict(capacity=cap, overlap=overlap,
        **overlap_model([.05]*8, [.08]*8, capacity=cap, overlap=overlap))
        for cap in (1, 2) for overlap in (False, True)]
    with tempfile.TemporaryDirectory(prefix="folith-phase2-probes-") as tmp:
        root = Path(tmp)
        annotation, reference_result = [], None
        for variant in ("A", "B", "C2", "C4"):
            for failure in (None, "disk", "cancel", "parse"):
                row, result = annotation_probe(core, root, variant, failure=failure)
                if variant == "A" and failure is None:
                    reference_result = result
                row["result_equal_baseline"] = result == reference_result if failure is None else None
                annotation.append(row)
        payload["annotation_mock"] = annotation
        if args.job_dir:
            state, history = historical_analysis(args.job_dir)
            payload["history"] = history
            payload["batching"] = []
            payload["batch_order"] = []
            resume_seed = {}
            rng = random.Random(20261026)
            for repeat in range(args.repeats):
                variants = [("sentence", 4800), ("paragraph", 2000), ("paragraph", 4000), ("paragraph", 6000)]
                rng.shuffle(variants)
                payload["batch_order"].append([f"{s}-{c}" for s, c in variants])
                for strategy, cap in variants:
                    capture = resume_seed if strategy == "paragraph" and cap == 4000 and not resume_seed else None
                    row = batch_replay(core, state, root/f"batch-{repeat}-{strategy}-{cap}", strategy, cap, capture_state=capture)
                    row["repeat"] = repeat
                    payload["batching"].append(row)
            for use_tm, resume in [(True, False), (False, True)]:
                payload["batching"].append(batch_replay(core, state, root/f"reuse-{use_tm}-{resume}", "paragraph", 4000,
                    use_tm=use_tm, resume=resume, resume_seed=resume_seed))
    (args.out/"research.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n")
    print("Offline Phase 2 research complete; numeric evidence saved.")


if __name__ == "__main__":
    main()
