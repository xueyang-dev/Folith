#!/usr/bin/env python3
"""Reproducible translation-runtime efficiency benchmark.

The benchmark drives the *real* ``core.translate_stage`` over a real document
workload with a deterministic local provider, so every number it reports is a
property of the production translation runtime rather than of a reimplementation.

What it measures
----------------
* API request count, split by role (translation / repair / review / auxiliary);
* input and output tokens, plus the prompt-composition split produced by
  ``state["translation_prompt_cost"]`` (instructions / glossary / style /
  document context / entities / neighbour context / source / other);
* wall-clock time, per-request latency (avg / p50 / p95), retries, errors;
* batching shape: batch count, segments per request, source tokens per request;
* reuse: TM hits and exact-cache hits;
* quality drift against a stored reference: segment count, empty targets,
  source ordering, deterministic QA findings, terminology compliance and
  agreement with the reference translation.

The provider is deterministic and maps each requested source segment to its
reference translation.  That keeps quality metrics stable across runs, so a
change in batching, context or caching that scrambles, drops or reorders work
shows up as a quality delta instead of hiding in a latency average.

Latency model
-------------
The local provider sleeps ``--latency`` seconds plus ``--latency-per-char``
seconds per prompt character.  Wall-clock numbers are therefore a *projection*
under a stated model; request counts, token counts and reuse are exact.

Run
---
    python3 scripts/translation_efficiency_benchmark.py \
        --fixture benchmark/translation-efficiency/workload.json \
        --out tmp/translation-efficiency --label baseline
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as _context  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402
from folith import usage as _usage  # noqa: E402

TRANSLATOR_MARKER = "你是一位专业翻译人员"
REVIEW_MARKER = "你是一位独立的翻译审校专家"
KNOWLEDGE_MARKER = "翻译流知识抽取器"
CURRENT_BATCH_MARKER = "【待翻译段落（按序号返回等长数组）】"
LEGACY_BATCH_MARKER = "待翻译段落（按序号返回等长译文数组）："
REPAIR_MARKER = "问题清单"

REVIEW_EMPTY = json.dumps(
    {"findings": [], "evidence_requests": []}, ensure_ascii=False)


def _estimate_tokens(chars: int) -> int:
    """Same estimator as ``folith.usage`` so numbers line up with the ledger."""
    return _usage._estimate_tokens(chars)


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return float(ordered[index])


def _digest(values: Sequence[Any]) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_fixture(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("segments", "target_lang", "glossary"):
        if key not in data:
            raise ValueError(f"fixture {path} is missing {key!r}")
    return data


def _extract_batch_sources(user_prompt: str) -> List[str]:
    """Recover the numbered source batch from a rendered translation prompt."""
    marker = CURRENT_BATCH_MARKER if CURRENT_BATCH_MARKER in user_prompt \
        else LEGACY_BATCH_MARKER
    if marker not in user_prompt:
        return []
    tail = user_prompt.split(marker, 1)[1]
    sources: List[str] = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        head, sep, rest = line.partition(". ")
        if not sep or not head.isdigit():
            if sources:
                # Continuation of a multi-line segment: keep it attached.
                sources[-1] = f"{sources[-1]}\n{line}"
            continue
        sources.append(rest)
    return sources


def _extract_repair_sources(user_prompt: str) -> List[str]:
    sources = []
    for line in user_prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("原文："):
            sources.append(stripped[len("原文："):])
    return sources


class DeterministicProvider:
    """Deterministic local provider; never touches the network."""

    def __init__(self, reference: Dict[str, str], latency: float,
                 latency_per_char: float,
                 knowledge_replay: Optional[Dict[int, List[Dict[str, Any]]]] = None):
        self.reference = reference
        self.knowledge_replay = dict(knowledge_replay or {})
        self.latency = max(0.0, float(latency))
        self.latency_per_char = max(0.0, float(latency_per_char))
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._counter = 0

    def classify(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt = system_prompt or ""
        user_prompt = user_prompt or ""
        if REVIEW_MARKER in system_prompt:
            return "review"
        if KNOWLEDGE_MARKER in system_prompt:
            return "auxiliary"
        if REPAIR_MARKER in user_prompt:
            return "repair"
        if TRANSLATOR_MARKER in system_prompt:
            return "translation"
        return "unknown"

    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        return self.invoke(provider, api_key, model, system_prompt, user_prompt,
                           temperature=temperature, **kwargs)

    def invoke(self, provider, api_key, model, system_prompt, user_prompt,
               temperature=0.1, **kwargs):
        role = self.classify(system_prompt, user_prompt)
        started = time.perf_counter()
        total_chars = len(system_prompt or "") + len(user_prompt or "")
        if self.latency or self.latency_per_char:
            time.sleep(self.latency + self.latency_per_char * total_chars)
        result = self.respond(role, user_prompt)
        elapsed = time.perf_counter() - started
        with self._lock:
            self._counter += 1
            self.calls.append({
                "request_id": f"{role}-{self._counter}",
                "role": role,
                "system_chars": len(system_prompt or ""),
                "user_chars": len(user_prompt or ""),
                "prompt_chars": total_chars,
                "completion_chars": len(result),
                "latency_s": round(elapsed, 6),
            })
        return result

    def respond(self, role: str, user_prompt: str) -> str:
        if role == "review":
            return REVIEW_EMPTY
        if role == "auxiliary":
            # Replay the real extractor's captured observations for this batch.
            # The knowledge prompt is keyed by the global segment ids it names,
            # so the first id maps back to the batch the live sweep observed.
            if self.knowledge_replay:
                match = re.search(r"segment_id:\s*(\d+)", user_prompt or "")
                if match:
                    offset = int(match.group(1))
                    captured = self.knowledge_replay.get(offset)
                    if captured is not None:
                        return json.dumps(captured, ensure_ascii=False)
            return "[]"
        if role == "repair":
            sources = _extract_repair_sources(user_prompt)
        else:
            sources = _extract_batch_sources(user_prompt)
        if not sources:
            return json.dumps([], ensure_ascii=False)
        targets = [self.reference.get(_normalize_source(s), "") for s in sources]
        return json.dumps(targets, ensure_ascii=False)


def _normalize_source(value: Any) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def build_state(fixture: Dict[str, Any]) -> Dict[str, Any]:
    state = core.new_job_state(fixture.get("filename") or "benchmark.txt")
    state["paras"] = list(fixture["segments"])
    state["target_lang"] = fixture.get("target_lang") or "简体中文"
    state["style_rules"] = fixture.get("style_rules") or ""
    state["document_profile"] = copy.deepcopy(fixture.get("document_profile") or {})
    state["document_synopsis"] = copy.deepcopy(fixture.get("document_synopsis") or {})
    state["section_digests"] = copy.deepcopy(fixture.get("section_digests") or [])
    state["semantic_units"] = copy.deepcopy(fixture.get("semantic_units") or [])
    state["entity_registry"] = copy.deepcopy(fixture.get("entity_registry") or [])
    return state


def run_once(fixture: Dict[str, Any], *, job_id: str, output_dir: Path,
             batch_size: int, max_batch_chars: int, concurrency: int,
             enable_review: bool, use_tm: bool, glossary: List[Dict[str, Any]],
             latency: float, latency_per_char: float,
             seed_pairs: Optional[List[Dict[str, Any]]] = None,
             existing_state: Optional[Dict[str, Any]] = None,
             review_mode: Optional[str] = None,
             knowledge_replay: Optional[Dict[int, List[Dict[str, Any]]]] = None,
             knowledge_feedback_interval: Optional[int] = None) -> Dict[str, Any]:
    """One translate_stage run. Returns raw results plus the measured metrics."""
    core.OUTPUT_DIR = output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    state = copy.deepcopy(existing_state) if existing_state is not None \
        else build_state(fixture)
    if seed_pairs is not None:
        state["pairs"] = copy.deepcopy(seed_pairs)
    elif existing_state is None:
        state["pairs"] = []
    core.save_job_state(job_id, state)

    reference = {
        _normalize_source(source): target
        for source, target in zip(fixture["segments"],
                                  fixture.get("reference_targets") or [])
    }
    provider = DeterministicProvider(reference, latency, latency_per_char,
                                     knowledge_replay=knowledge_replay)

    ledger_before = copy.deepcopy(state.get("llm_usage") or {})
    packet_lock = threading.Lock()
    packets: List[Dict[str, Any]] = []
    render_stats: List[Dict[str, Any]] = []
    original_compile = _context.compile_context_packet
    # Capture the render that actually assembles a translation request.
    # ``context_metadata`` calls ``_render_context_packet`` a second time purely
    # for the audit log; counting that too would double every section total.
    original_render_with_stats = _context.render_context_packet_with_stats

    def recording_compile(*args, **kwargs):
        packet = original_compile(*args, **kwargs)
        with packet_lock:
            packets.append({
                "current_batch": list(kwargs.get("current_batch") or []),
                "previous_source": list(kwargs.get("previous_source") or []),
                "next_source": list(kwargs.get("next_source") or []),
                "previous_target": [
                    (item.get("target") if isinstance(item, dict) else str(item))
                    for item in (kwargs.get("previous_target") or [])
                ],
                "glossary_chars": len(kwargs.get("glossary_text") or ""),
                "budget": kwargs.get("context_budget_chars"),
            })
        return packet

    def recording_render(packet):
        rendered, stats = original_render_with_stats(packet)
        with packet_lock:
            render_stats.append(stats)
        return rendered, stats

    _context.compile_context_packet = recording_compile
    _context.render_context_packet_with_stats = recording_render
    old_call = core.call_llm
    core.call_llm = provider
    profiler = None
    wall = 0.0
    error = None
    try:
        started = time.perf_counter()
        result = core.translate_stage(
            state, job_id, glossary, "Benchmark", "benchmark-key", "benchmark-model",
            state["target_lang"], state["style_rules"],
            enable_review=enable_review, use_tm=use_tm,
            document_profile=state["document_profile"],
            batch_size=batch_size, max_batch_chars=max_batch_chars,
            translation_concurrency=concurrency, profiler=profiler,
            review_mode=review_mode,
            knowledge_feedback_interval=knowledge_feedback_interval)
        wall = time.perf_counter() - started
    except Exception as exc:  # keep the benchmark run diagnosable
        wall = time.perf_counter() - started
        error = f"{type(exc).__name__}: {exc}"
        result = state
    finally:
        _context.compile_context_packet = original_compile
        _context.render_context_packet_with_stats = original_render_with_stats
        core.call_llm = old_call

    metrics = summarize(
        fixture=fixture, result=result, provider=provider, wall=wall,
        packets=packets, render_stats=render_stats, error=error,
        batch_size=batch_size, max_batch_chars=max_batch_chars,
        concurrency=concurrency, enable_review=enable_review,
        ledger_before=ledger_before)
    return {"metrics": metrics, "state": result, "calls": provider.calls}


def _review_metrics(result, by_role, enable_review) -> Dict[str, Any]:
    """Review-stage cost and yield, measured separately from translation."""
    review_calls = list(by_role.get("review") or [])
    stats = (result or {}).get("review_stats") or {}
    findings = [item for item in ((result or {}).get("findings") or [])
                if isinstance(item, dict) and item.get("type") == "review"]
    severity: Dict[str, int] = {}
    for item in findings:
        key = str(item.get("severity") or "unknown")
        severity[key] = severity.get(key, 0) + 1
    prompt_chars = sum(int(c.get("system_chars") or 0) + int(c.get("user_chars") or 0)
                       for c in review_calls)
    completion_chars = sum(int(c.get("completion_chars") or 0) for c in review_calls)
    latencies = [float(c.get("latency_s") or 0.0) for c in review_calls]
    events = [e for e in ((result or {}).get("review_evidence") or [])
              if isinstance(e, dict)]
    skipped_events = [e for e in events
                      if e.get("decision") == "skipped_low_risk"]
    batches_reviewed = int(stats.get("batches_reviewed") or 0)
    batches_skipped = int(stats.get("batches_skipped_low_risk") or 0)
    total_batches = batches_reviewed + batches_skipped
    zero_yield = 0
    if enable_review and review_calls:
        # A batch with no blocking/actionable finding is a review that bought
        # nothing beyond what the deterministic layer already had.
        zero_yield = batches_reviewed - len(
            {int(f.get("segment_id") or 0) for f in findings
             if str(f.get("severity")) in ("blocking", "actionable")})
    return {
        "enabled": bool(enable_review),
        "review_mode": str((result or {}).get("review_mode") or "always"),
        "calls": len(review_calls),
        "batches_reviewed": batches_reviewed,
        "batches_skipped_low_risk": batches_skipped,
        "segments_reviewed": int(stats.get("reviewed_segments") or 0),
        "segments_skipped_low_risk": int(stats.get("skipped_segments") or 0),
        "review_rate": round(batches_reviewed / total_batches, 4) if total_batches else 0.0,
        "input_tokens": _estimate_tokens(prompt_chars),
        "output_tokens": _estimate_tokens(completion_chars),
        "tokens_per_call": round(_estimate_tokens(prompt_chars) / len(review_calls), 1)
        if review_calls else 0.0,
        "latency_avg_s": round(statistics.fmean(latencies), 4) if latencies else 0.0,
        "wall_s": round(sum(latencies), 4),
        "findings_total": len(findings),
        "findings_by_severity": dict(sorted(severity.items())),
        "blocking_findings": severity.get("blocking", 0),
        "actionable_findings": severity.get("actionable", 0),
        "informational_findings": severity.get("informational", 0),
        "batches_with_zero_meaningful_findings": max(0, zero_yield),
        "skip_events": len(skipped_events),
        "review_failed": int(stats.get("review_failed") or 0),
    }


def _knowledge_metrics(result, by_role) -> Dict[str, Any]:
    """Knowledge-feedback cost and trigger coverage, measured on its own."""
    calls = list(by_role.get("auxiliary") or [])
    policy = (result or {}).get("knowledge_feedback_policy") or {}
    events = [event for event in ((result or {}).get("knowledge_events") or [])
              if isinstance(event, dict)]
    kinds: Dict[str, int] = {}
    for event in events:
        key = str(event.get("type") or "unknown")
        kinds[key] = kinds.get(key, 0) + 1
    prompt_chars = sum(int(call.get("system_chars") or 0)
                       + int(call.get("user_chars") or 0) for call in calls)
    system_chars = sum(int(call.get("system_chars") or 0) for call in calls)
    completion_chars = sum(int(call.get("completion_chars") or 0) for call in calls)
    latencies = [float(call.get("latency_s") or 0.0) for call in calls]
    return {
        "calls": len(calls),
        "interval": policy.get("interval"),
        "force_every_batch": bool(policy.get("force_every_batch")),
        "observed_batches": len(policy.get("observed_batches") or []),
        "skipped_batches": len(policy.get("skipped_batches") or []),
        "signal_batches": len(policy.get("signal_batches") or []),
        "skip_reasons": dict(policy.get("skip_reasons") or {}),
        "input_tokens": _estimate_tokens(prompt_chars),
        "output_tokens": _estimate_tokens(completion_chars),
        "system_tokens": _estimate_tokens(system_chars),
        "tokens_per_call": round(_estimate_tokens(prompt_chars) / len(calls), 1)
        if calls else 0.0,
        "system_tokens_per_call": round(_estimate_tokens(system_chars) / len(calls), 1)
        if calls else 0.0,
        "latency_avg_s": round(statistics.fmean(latencies), 4) if latencies else 0.0,
        "wall_s": round(sum(latencies), 4),
        "candidates_stored": len((result or {}).get("knowledge_candidates") or []),
        "entities_stored": len((result or {}).get("entity_registry") or []),
        "candidate_sources": sorted(
            str(c.get("source") or "")
            for c in ((result or {}).get("knowledge_candidates") or [])
            if isinstance(c, dict)),
        "entity_sources": sorted(
            str(r.get("source_form") or "")
            for r in ((result or {}).get("entity_registry") or [])
            if isinstance(r, dict)),
        "events_by_type": dict(sorted(kinds.items())),
        "failures": int((result or {}).get("knowledge_feedback_failures") or 0),
        "reuse": dict((result or {}).get("knowledge_cache") or {}),
    }


def _knowledge_replay_map(path: str, fixture: Dict[str, Any], batch_size: int,
                          max_batch_chars: int) -> Dict[int, List[Dict[str, Any]]]:
    """Map a batch's first global segment id to the live-extracted observations.

    The replay file is the output of ``scripts/knowledge_feedback_live.py``; the
    batch plan must match the one that produced it (same segment list, batch
    size and character budget), otherwise the mapping is meaningless.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    captures = payload.get("captures") or payload
    batches = core.make_batches(
        list(fixture["segments"]), batch_size=batch_size,
        max_chars=max_batch_chars,
        semantic_units=fixture.get("semantic_units") or None)
    mapping: Dict[int, List[Dict[str, Any]]] = {}
    offset = 0
    for index, batch in enumerate(batches):
        entry = captures.get(str(index))
        if isinstance(entry, dict):
            mapping[offset] = list(entry.get("observations") or [])
        offset += len(batch)
    return mapping


def summarize(*, fixture, result, provider, wall, packets, render_stats, error,
              batch_size, max_batch_chars, concurrency, enable_review,
              ledger_before=None) -> Dict[str, Any]:
    calls = provider.calls
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    for call in calls:
        by_role.setdefault(call["role"], []).append(call)

    latencies = [call["latency_s"] for call in calls]
    ledger = (result or {}).get("llm_usage") or _usage.empty_usage()
    ledger_before = ledger_before or {}
    # A resumed job carries the previous run's ledger; report only this run's
    # spend so warm/cold comparisons are a delta, not a cumulative total.
    prompt_tokens = (int(ledger.get("prompt_tokens") or 0)
                     - int(ledger_before.get("prompt_tokens") or 0))
    completion_tokens = (int(ledger.get("completion_tokens") or 0)
                         - int(ledger_before.get("completion_tokens") or 0))
    total_tokens = (int(ledger.get("total_tokens") or 0)
                    - int(ledger_before.get("total_tokens") or 0))
    prompt_cost = (result or {}).get("translation_prompt_cost") or {}

    pairs = (result or {}).get("pairs") or []
    sources = list(fixture["segments"])
    targets = [(pair.get("target") or "") if isinstance(pair, dict) else "" for pair in pairs]
    reference_targets = list(fixture.get("reference_targets") or [])

    initial_findings = [item for item in ((result or {}).get("findings") or [])
                        if isinstance(item, dict)]
    severity_counts: Dict[str, int] = {}
    for item in initial_findings:
        severity = str(item.get("severity") or "unknown")
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    glossary = fixture.get("glossary") or []
    compliance = 0
    try:
        compliance_findings = core.check_translation_batch(
            [str(s) for s in sources], targets, glossary,
            fixture.get("target_lang") or "简体中文")
        compliance = sum(
            1 for item in compliance_findings
            if str(item.get("severity")) in ("blocking", "actionable"))
    except Exception:  # pragma: no cover - diagnostic only
        compliance = -1

    matched_reference = sum(
        1 for index, target in enumerate(targets)
        if index < len(reference_targets)
        and _normalize_source(target) == _normalize_source(reference_targets[index]))

    category_chars = dict(prompt_cost.get("category_chars") or {})
    category_tokens = {
        name: _estimate_tokens(chars) for name, chars in category_chars.items()
    }
    total_category_tokens = sum(category_tokens.values()) or 0

    batch_sizes = prompt_cost.get("requests_by_segment_count") or {}
    source_token_samples = []
    for stats in render_stats:
        source_token_samples.append(_estimate_tokens(stats.get("source_chars") or 0))

    tm_hits = int((result or {}).get("tm_used_count") or 0)
    cache = (result or {}).get("translation_cache") or {}

    return {
        "label": None,
        "error": error,
        "config": {
            "batch_size": batch_size,
            "max_batch_chars": max_batch_chars,
            "concurrency": concurrency,
            "review": bool(enable_review),
            "segment_count": len(sources),
        },
        "requests": {
            "total": len(calls),
            "by_role": {role: len(items) for role, items in sorted(by_role.items())},
            "batches": int((result or {}).get("batch_plan", {}).get("batch_count") or 0),
            "by_batch_segment_count": batch_sizes,
            "segments_per_request": round(
                sum(int(k) * int(v) for k, v in batch_sizes.items())
                / max(1, sum(int(v) for v in batch_sizes.values())), 3)
            if batch_sizes else 0.0,
        },
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": total_tokens,
            "prompt_by_role": {
                role: int(slot.get("prompt_tokens") or 0)
                for role, slot in (ledger.get("by_role") or {}).items()
            },
            "composition_tokens": category_tokens,
            "composition_share": {
                name: round(count / total_category_tokens, 4)
                for name, count in sorted(category_tokens.items())
            } if total_category_tokens else {},
            "translation_requests_measured": int(prompt_cost.get("requests") or 0),
            "source_tokens_total": _estimate_tokens(
                sum(len(str(s)) for s in sources)),
            "source_tokens_per_request_avg": round(
                statistics.fmean(source_token_samples), 2)
            if source_token_samples else 0.0,
        },
        "time": {
            "wall_s": round(wall, 4),
            "latency_avg_s": round(statistics.fmean(latencies), 6) if latencies else 0.0,
            "latency_p50_s": round(_percentile(latencies, 0.5), 6),
            "latency_p95_s": round(_percentile(latencies, 0.95), 6),
            "latency_sum_s": round(sum(latencies), 6),
        },
        "review": _review_metrics(result, by_role, enable_review),
        "knowledge": _knowledge_metrics(result, by_role),
        "reuse": {
            "tm_hits": tm_hits,
            "cache_hits": int(cache.get("hits") or 0),
            "cache_misses": int(cache.get("misses") or 0),
            "cache_stores": int(cache.get("stores") or 0),
        },
        "reliability": {
            "retries": max(0, len([c for c in calls if c["role"] == "translation"])
                           - int(sum(int(v) for v in batch_sizes.values()))),
            "failures": int(ledger.get("failures") or 0),
            "unknown_role_calls": len(by_role.get("unknown") or []),
        },
        "quality": {
            "segments": len(pairs),
            "expected_segments": len(sources),
            "empty_targets": sum(1 for t in targets if not str(t).strip()),
            "source_order_digest": _digest(
                [(p.get("source") if isinstance(p, dict) else "") for p in pairs]),
            "expected_source_order_digest": _digest(sources),
            "target_digest": _digest(targets),
            "reference_agreement": round(
                matched_reference / max(1, len(reference_targets)), 4),
            "findings_total": len(initial_findings),
            "findings_by_severity": dict(sorted(severity_counts.items())),
            "terminology_violations": compliance,
            "blocked_source_segments": int(
                ((result or {}).get("source_quality_gate") or {}).get("flagged_count") or 0),
        },
        "context": {
            "packets": len(packets),
            "rendered": len(render_stats),
            "truncated_prefixes": sum(
                1 for stats in render_stats if stats.get("context_truncated")),
            "dedupe_removed": sum(
                int(stats.get("dedupe_removed_count") or 0) for stats in render_stats),
            "section_tokens": _section_token_totals(render_stats),
            "prefix_tokens_total": sum(
                _estimate_tokens(stats.get("context_prefix_chars") or 0)
                for stats in render_stats),
            "continuity_digest": _digest(
                [packet.get("previous_target") for packet in packets]),
            "continuity_empty_batches": sum(
                1 for packet in packets if not packet.get("previous_target")),
        },
    }


def _section_token_totals(render_stats: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for stats in render_stats:
        for title, chars in (stats.get("section_chars") or {}).items():
            totals[title] = totals.get(title, 0) + int(chars or 0)
    return {title: _estimate_tokens(chars)
            for title, chars in sorted(totals.items(), key=lambda kv: -kv[1])}


def _single_batch_glossary_change(glossary, fixture):
    """Add one confirmed locked term that matches exactly one batch.

    Used by the partial-invalidation benchmark: exactly one batch's glossary
    section changes, so exactly that batch should miss the exact cache.
    """
    segments = fixture.get("segments") or []
    if not segments:
        return list(glossary)
    # The document title segment is unique enough to land in one batch.
    anchor = str(segments[0]).strip()
    changed = [dict(entry) for entry in glossary]
    changed.append({
        "id": "bench-partial-1",
        "source": anchor,
        "target": "【基准变更译名】",
        "preferred": "【基准变更译名】",
        "forbidden": [],
        "behavior": "translate",
        "status": "locked",
        "scope": "global",
        "note": "",
    })
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture",
                        default="benchmark/translation-efficiency/workload.json")
    parser.add_argument("--out", default="tmp/translation-efficiency")
    parser.add_argument("--label", default="run")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--max-batch-chars", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--latency-per-char", type=float, default=0.0)
    parser.add_argument("--review", action="store_true",
                        help="enable the independent review stage")
    parser.add_argument("--review-mode", default=None,
                        choices=("always", "risk_selective"),
                        help="review policy: always (default) or risk_selective")
    parser.add_argument("--review-view-reductions", default="default",
                        help="review packet view reductions: 'default'/'safe', "
                             "'none', 'aggressive', or a comma-separated list")
    parser.add_argument("--review-context-minimal", action="store_true",
                        help="also drop document-level context from the review view")
    parser.add_argument("--no-tm", action="store_true",
                        help="disable translation memory (cold linguistic reuse)")
    parser.add_argument("--seed-tm", action="store_true",
                        help="pre-seed TM from the reference translation so the "
                             "run measures batch work, not terminology warm-up")
    parser.add_argument("--rerun", action="store_true",
                        help="run the workload again with a warm exact cache and "
                             "no carried-over progress (the exact-cache case)")
    parser.add_argument("--resume", action="store_true",
                        help="also run the workload again from the completed state "
                             "(the checkpoint-resume case)")
    parser.add_argument("--partial", action="store_true",
                        help="also change one translation-affecting item (a confirmed "
                             "locked term that matches a single batch) and rerun with a "
                             "warm cache to measure partial invalidation")
    parser.add_argument("--limit", type=int, default=None,
                        help="use only the first N segments (quick smoke runs)")
    parser.add_argument("--knowledge-replay", default=None,
                        help="replay real captured knowledge observations from a "
                             "scripts/knowledge_feedback_live.py sweep, keyed by "
                             "batch; requires the matching batch plan")
    parser.add_argument("--knowledge-interval", type=int, default=None,
                        help="override the knowledge-feedback interval "
                             "(1 = mandatory every-batch feedback)")
    args = parser.parse_args()

    if args.review_view_reductions in ("default", "safe"):
        active_reductions = _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS
    elif args.review_view_reductions == "none":
        active_reductions = frozenset()
    elif args.review_view_reductions in ("all", "aggressive"):
        # Every reduction, including the ones that drop unique document context.
        active_reductions = frozenset(_evidence.REVIEW_VIEW_REDUCTIONS)
    else:
        active_reductions = frozenset(
            part.strip() for part in args.review_view_reductions.split(",")
            if part.strip())
    if args.review_context_minimal:
        active_reductions = active_reductions | frozenset({"context_minimal"})
    _evidence.DEFAULT_REVIEW_VIEW_REDUCTIONS = active_reductions
    print(f"review-view reductions: {sorted(active_reductions) or ['<none>']}")

    fixture = load_fixture(Path(args.fixture))
    if args.limit:
        fixture["segments"] = fixture["segments"][:args.limit]
        fixture["reference_targets"] = (fixture.get("reference_targets") or [])[:args.limit]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_size = args.batch_size or core.BATCH_SIZE
    max_batch_chars = args.max_batch_chars or core.TRANSLATION_MAX_BATCH_CHARS
    concurrency = args.concurrency or core.DEFAULT_TRANSLATION_CONCURRENCY
    glossary = list(fixture.get("glossary") or [])
    knowledge_replay = (_knowledge_replay_map(
        args.knowledge_replay, fixture, batch_size, max_batch_chars)
        if args.knowledge_replay else None)

    seed_pairs = None
    if args.seed_tm:
        seed_pairs = [
            {"source": source, "target": target, "reviewed": True,
             "review_status": "reviewed", "target_provenance": "reviewed",
             "from_tm": False}
            for source, target in zip(fixture["segments"],
                                      fixture.get("reference_targets") or [])
        ]

    with tempfile.TemporaryDirectory() as tmp:
        # One shared output root per label: TM and the exact cache are
        # project-scoped, so a "warm rerun" is only meaningful when both runs
        # resolve to the same project directory.  Distinct job ids keep the two
        # runs' `state.json` files separate.
        shared_root = Path(tmp)
        first = run_once(
            fixture, job_id=f"{args.label}-cold", output_dir=shared_root,
            batch_size=batch_size, max_batch_chars=max_batch_chars,
            concurrency=concurrency, enable_review=args.review,
            use_tm=not args.no_tm, glossary=glossary,
            latency=args.latency, latency_per_char=args.latency_per_char,
            seed_pairs=seed_pairs, review_mode=args.review_mode,
            knowledge_replay=knowledge_replay,
            knowledge_feedback_interval=args.knowledge_interval)
        report: Dict[str, Any] = {
            "label": args.label,
            "fixture": str(args.fixture),
            "cold": first["metrics"],
        }
        if args.rerun:
            # Same project, same source, same configuration, but no carried-over
            # progress: the only possible reuse is the exact execution cache.
            warm = run_once(
                fixture, job_id=f"{args.label}-warm", output_dir=shared_root,
                batch_size=batch_size, max_batch_chars=max_batch_chars,
                concurrency=concurrency, enable_review=args.review,
                use_tm=not args.no_tm, glossary=glossary,
                latency=args.latency, latency_per_char=args.latency_per_char,
                review_mode=args.review_mode,
                knowledge_replay=knowledge_replay,
                knowledge_feedback_interval=args.knowledge_interval)
            report["warm"] = warm["metrics"]
            report["warm_reuse"] = _warm_delta(first["metrics"], warm["metrics"])
        if args.resume:
            resumed = run_once(
                fixture, job_id=f"{args.label}-resume", output_dir=shared_root,
                batch_size=batch_size, max_batch_chars=max_batch_chars,
                concurrency=concurrency, enable_review=args.review,
                use_tm=not args.no_tm, glossary=glossary,
                latency=args.latency, latency_per_char=args.latency_per_char,
                existing_state=first["state"], review_mode=args.review_mode,
                knowledge_replay=knowledge_replay,
                knowledge_feedback_interval=args.knowledge_interval)
            report["resume"] = resumed["metrics"]
            report["resume_reuse"] = _warm_delta(first["metrics"],
                                                 resumed["metrics"])
        if args.partial:
            changed = _single_batch_glossary_change(glossary, fixture)
            partial = run_once(
                fixture, job_id=f"{args.label}-partial", output_dir=shared_root,
                batch_size=batch_size, max_batch_chars=max_batch_chars,
                concurrency=concurrency, enable_review=args.review,
                use_tm=not args.no_tm, glossary=changed,
                latency=args.latency, latency_per_char=args.latency_per_char,
                review_mode=args.review_mode,
                knowledge_replay=knowledge_replay,
                knowledge_feedback_interval=args.knowledge_interval)
            report["partial"] = partial["metrics"]
            report["partial_change"] = {
                "changed_entry": changed[-1] if changed else None,
                "matched_segments": sum(
                    1 for segment in fixture["segments"]
                    if changed and changed[-1]["source"].lower() in segment.lower()),
                **{key: value for key, value in
                   _warm_delta(first["metrics"], partial["metrics"]).items()
                   if key in ("requests", "tokens", "wall_s", "cache",
                              "quality_matches", "empty_targets")},
            }

    for key in ("cold", "warm", "resume"):
        if key in report:
            report[key]["label"] = f"{args.label}-{key}"
    history = out_dir / "history.jsonl"
    with history.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False) + "\n")
    target = out_dir / f"{args.label}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    print(json.dumps({
        "label": report["label"],
        "cold_requests": report["cold"]["requests"],
        "cold_tokens": {k: v for k, v in report["cold"]["tokens"].items()
                        if k != "composition_share"},
        "cold_time": report["cold"]["time"],
        "cold_quality": report["cold"]["quality"],
        "cold_review": report["cold"]["review"],
        "cold_knowledge": report["cold"]["knowledge"],
        "cold_reuse": report["cold"]["reuse"],
        "warm": report.get("warm_reuse"),
        "resume": report.get("resume_reuse"),
        "partial": report.get("partial_change"),
    }, ensure_ascii=False, indent=2))
    if report["cold"].get("error"):
        print(f"\ncold run error: {report['cold']['error']}", file=sys.stderr)
        return 1
    if report["cold"]["requests"]["total"] == 0:
        print("\ncold run produced no requests: refusing to report an empty run",
              file=sys.stderr)
        return 1
    print(f"\nwrote {target}")
    return 0


def _warm_delta(cold: Dict[str, Any], warm: Dict[str, Any]) -> Dict[str, Any]:
    def delta(key_path: Sequence[str]) -> Dict[str, Any]:
        before: Any = cold
        after: Any = warm
        for part in key_path:
            before = (before or {}).get(part) if isinstance(before, dict) else None
            after = (after or {}).get(part) if isinstance(after, dict) else None
        return {"cold": before, "warm": after}

    requests_before = int(cold["requests"]["total"])
    requests_after = int(warm["requests"]["total"])
    tokens_before = int(cold["tokens"]["total"])
    tokens_after = int(warm["tokens"]["total"])
    wall_before = float(cold["time"]["wall_s"])
    wall_after = float(warm["time"]["wall_s"])
    return {
        "requests": {"cold": requests_before, "warm": requests_after,
                     "avoided": requests_before - requests_after},
        "tokens": {"cold": tokens_before, "warm": tokens_after,
                   "avoided": tokens_before - tokens_after},
        "wall_s": {"cold": wall_before, "warm": wall_after,
                   "avoided": round(wall_before - wall_after, 4)},
        "cache": delta(("reuse", "cache_hits")),
        "tm_hits": delta(("reuse", "tm_hits")),
        "quality_matches": (
            cold["quality"]["target_digest"] == warm["quality"]["target_digest"]),
        "empty_targets": delta(("quality", "empty_targets")),
    }


if __name__ == "__main__":
    raise SystemExit(main())
