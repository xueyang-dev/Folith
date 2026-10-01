#!/usr/bin/env python
"""Translation setup hot-path audit — Performance Phase 5 (read-only).

Why this script exists
----------------------
`core.translate_stage` already measures a `_setup` phase (the interval between the
batch's `generation_started` event and the Translator LLM call), but the
telemetry deliberately drops names starting with `_`, so that number is never
persisted.  Before spending any effort on optimisation we need to know *where*
those setup milliseconds go and whether any of it grows with document length.

This module answers that with **read-only instrumentation**: it monkeypatches the
setup helpers in-process and runs the real pipeline with a deterministic offline
provider.  It does not edit `core.py`, `folith/*`, prompts, contracts or any
persisted state; every wrapper delegates to the original implementation and only
records counters.

What it measures
----------------
* the true `_setup` wall time per batch (event -> Translator call boundary)
* per-sub-step: calls / total / avg / p50 / p95 / items scanned / chars /
  cache hits / results
* scaling curves for each sub-step at 100 / 1k / 5k / 10k / 20k segments
* dedicated microbenchmarks for TM (100 / 1k / 10k / 50k entries) and
  terminology (100 / 1k / 10k terms)

Usage
-----
    venv/bin/python scripts/translation_setup_audit.py --sizes 100,1000,5000
    venv/bin/python scripts/translation_setup_audit.py --micro-only
    venv/bin/python scripts/translation_setup_audit.py --json tmp/phase5/setup_audit.json

Never touches the network: `core.call_llm` is replaced with a deterministic stub.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import context as ctx  # noqa: E402
from folith import knowledge as knowledge_mod  # noqa: E402
from folith import models as models_mod  # noqa: E402
from folith import terminology as terminology_mod  # noqa: E402
from folith.entity_registry import EntityRegistry  # noqa: E402

TRANSLATOR_MARK = "学术翻译专家"
REVIEWER_MARK = "独立的翻译审校专家"
KNOWLEDGE_MARK = "翻译流知识抽取器"
DIGEST_MARK = "文档理解器"
SYNOPSIS_MARK = "全书理解器"

CURRENT_BATCH_HEADER = "【待翻译段落（按序号返回等长数组）】"
_NUMBERED_RE = re.compile(r"^\s*(\d+)\.\s?(.*)$", re.M)


# --------------------------------------------------------------------------- #
# recorder
# --------------------------------------------------------------------------- #
class Recorder:
    """Accumulates per-step counters. Read-only: never mutates pipeline state."""

    def __init__(self) -> None:
        self.steps: Dict[str, Dict[str, Any]] = {}
        self.by_phase: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.by_caller: Dict[str, Dict[str, Dict[str, float]]] = {}
        self.per_batch: Dict[int, Dict[str, Dict[str, float]]] = {}
        self.batch_order: List[int] = []
        self.current: Optional[int] = None
        self.boundaries: Dict[int, Dict[str, float]] = {}
        self.regex_compiles = 0
        self.phase = "stage_prelude"

    def _bump(self, table: Dict[str, Dict[str, Dict[str, float]]], key: str,
              name: str, seconds: float, items: int) -> None:
        cell = table.setdefault(key, {}).setdefault(name, {"s": 0.0, "calls": 0, "items": 0})
        cell["s"] += seconds
        cell["calls"] += 1
        cell["items"] += items

    def add(self, name: str, seconds: float, *, items: int = 0, chars: int = 0,
            results: int = 0, hit: Optional[bool] = None, caller: str = "") -> None:
        step = self.steps.setdefault(name, {
            "calls": 0, "total_s": 0.0, "items": 0, "chars": 0,
            "results": 0, "hits": 0, "misses": 0, "samples": [],
        })
        step["calls"] += 1
        step["total_s"] += seconds
        step["items"] += items
        step["chars"] += chars
        step["results"] += results
        if hit is True:
            step["hits"] += 1
        elif hit is False:
            step["misses"] += 1
        step["samples"].append(seconds)
        self._bump(self.by_phase, self.phase, name, seconds, items)
        if caller:
            self._bump(self.by_caller, name, caller, seconds, items)
        if self.current is not None:
            bucket = self.per_batch.setdefault(self.current, {})
            cell = bucket.setdefault(name, {"s": 0.0, "calls": 0, "items": 0})
            cell["s"] += seconds
            cell["calls"] += 1
            cell["items"] += items

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    def start_batch(self, index: int) -> None:
        self.current = index
        if index not in self.batch_order:
            self.batch_order.append(index)

    # -- summaries ---------------------------------------------------------- #
    def percentiles(self, name: str) -> Tuple[float, float, float, float]:
        samples = sorted(self.steps.get(name, {}).get("samples") or [])
        if not samples:
            return 0.0, 0.0, 0.0, 0.0
        total = sum(samples)
        avg = total / len(samples)
        p50 = statistics.median(samples)
        idx = min(len(samples) - 1, int(round(0.95 * (len(samples) - 1))))
        return total, avg, p50, samples[idx]

    def summary_rows(self) -> List[Dict[str, Any]]:
        rows = []
        for name, step in sorted(self.steps.items()):
            total, avg, p50, p95 = self.percentiles(name)
            calls = step["calls"] or 1
            rows.append({
                "step": name,
                "calls": step["calls"],
                "total_s": round(total, 6),
                "total_ms": round(total * 1000, 3),
                "avg_ms": round(avg * 1000, 4),
                "p50_ms": round(p50 * 1000, 4),
                "p95_ms": round(p95 * 1000, 4),
                "items_scanned": step["items"],
                "items_per_call": round(step["items"] / calls, 2),
                "chars": step["chars"],
                "results": step["results"],
                "cache_hits": step["hits"],
                "cache_misses": step["misses"],
            })
        return rows


REC = Recorder()


@contextmanager
def _wrap(owner: Any, attr: str, name: str, probe: Callable[..., Tuple] | None = None):
    """Temporarily replace `owner.attr` with a timing proxy. Restores on exit."""
    original = getattr(owner, attr)
    if isinstance(owner, type):  # classmethod path (EntityRegistry.hints_for)
        def proxy(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            t0 = time.perf_counter()
            out = original(self, *args, **kwargs)
            dt = time.perf_counter() - t0
            items = chars = results = 0
            hit = None
            if probe is not None:
                items, chars, results, hit = probe(self, args, kwargs, out)
            REC.add(name, dt, items=items, chars=chars, results=results, hit=hit,
                    caller=_caller())
            return out
    else:
        def proxy(*args, **kwargs):  # type: ignore[no-untyped-def]
            t0 = time.perf_counter()
            out = original(*args, **kwargs)
            dt = time.perf_counter() - t0
            items = chars = results = 0
            hit = None
            if probe is not None:
                items, chars, results, hit = probe(args, kwargs, out)
            REC.add(name, dt, items=items, chars=chars, results=results, hit=hit,
                    caller=_caller())
            return out
    setattr(owner, attr, proxy)
    try:
        yield
    finally:
        setattr(owner, attr, original)


def _caller() -> str:
    """Name of the real code that invoked the wrapped helper (2 frames up)."""
    try:
        return str(sys._getframe(2).f_code.co_name)
    except Exception:  # noqa: BLE001 - attribution is best-effort, never fatal
        return ""


class _RegexProxy:
    """Counts `re.compile` calls so repeated-term-regex work becomes visible."""

    def __init__(self, real: Any) -> None:
        self.real = real

    def __getattr__(self, item: str) -> Any:
        return getattr(self.real, item)

    def compile(self, *args: Any, **kwargs: Any) -> Any:
        REC.regex_compiles += 1
        return self.real.compile(*args, **kwargs)


@contextmanager
def _count_regex_compiles(owner: Any):
    """Swap a module's `re` for a counting proxy, restoring it afterwards."""
    real = owner.re
    owner.re = _RegexProxy(real)
    try:
        yield
    finally:
        owner.re = real


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #
def _probe_select_target(pairs: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    limit = 0
    args = pairs
    if len(args) > 1:
        limit = int(args[1] or 0)
    return len(args[0]) if args else 0, limit, len(out or []), None


def _probe_one_arg_items(attr: str) -> Callable[..., Tuple]:
    def probe(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
        container = args[0] if args else kwargs.get(attr)
        try:
            items = len(container)
        except TypeError:
            items = 0
        return items, 0, 1 if out else 0, None
    return probe


def _probe_string(arg_index: int = 0) -> Callable[..., Tuple]:
    def probe(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
        value = args[arg_index] if len(args) > arg_index else ""
        chars = len(value) if isinstance(value, str) else 0
        return 0, chars, len(out or []), None
    return probe


def _probe_section_profile(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    profile = args[0] if args else None
    sections = (profile or {}).get("sections") or []
    return len(sections), 0, 1 if out else 0, None


def _probe_digest(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    digests = args[0] if args else []
    try:
        items = len(digests)
    except TypeError:
        items = 0
    return items, 0, 1 if out else 0, None


def _probe_glossary_select(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    segments = args[0] if args else []
    glossary = args[1] if len(args) > 1 else []
    chars = sum(len(s or "") for s in segments)
    selected = out[0] if isinstance(out, tuple) and out else []
    return len(glossary), chars, len(selected or []), None


def _probe_entity_hints(cls: Any, args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    texts = args[0] if args else []
    chars = sum(len(str(t or "")) for t in texts)
    return len(cls.records), chars, len(out or []), None


def _probe_packet(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    packet = args[0] if args else kwargs.get("packet") or {}
    current = (packet or {}).get("current_batch") or []
    rendered = out[0] if isinstance(out, tuple) and out else out
    chars = len(rendered) if isinstance(rendered, str) else 0
    return len(current), chars, 1 if out else 0, None


def _probe_evidence(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    paras = args[0] if args else []
    pairs = args[1] if len(args) > 1 else []
    batch = args[2] if len(args) > 2 else []
    try:
        items = len(paras) + len(pairs) + len(batch)
    except TypeError:
        items = 0
    return items, 0, 1, None


def _probe_review_context(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    offset = args[1] if len(args) > 1 else 0
    batch_len = args[2] if len(args) > 2 else 0
    return int(offset or 0) + int(batch_len or 0), 0, 1, None


def _probe_tm_lookup(args: Any, kwargs: Any, out: Any) -> Tuple[int, int, int, None]:
    tm = args[0] if args else {}
    source = args[1] if len(args) > 1 else ""
    index = args[2] if len(args) > 2 else kwargs.get("index")
    try:
        items = len(tm or {})
    except TypeError:
        items = 0
    hit = bool(out and out[0])
    return items, len(source or ""), 1 if hit else 0, index is not None


# --------------------------------------------------------------------------- #
# deterministic offline provider
# --------------------------------------------------------------------------- #
def _segments_from_prompt(user: str) -> List[str]:
    """Recover the batch's source segments from any of our prompt shapes."""
    if CURRENT_BATCH_HEADER in user:
        tail = user.rsplit(CURRENT_BATCH_HEADER, 1)[1]
        return [m.group(2) for m in _NUMBERED_RE.finditer(tail)]
    if "问题清单" in user:  # repair prompt: "1. 原文：…\n   译文：…"
        return [m.group(2) for m in re.finditer(r"^\s*\d+\.\s?原文：(.*)$", user, re.M)]
    return [m.group(2) for m in _NUMBERED_RE.finditer(user)]


def make_provider() -> Callable[..., str]:
    def llm(provider: str, api_key: str, model: str, system: str, user: str,
            temperature: float = 0.1, **kwargs: Any) -> str:
        if REVIEWER_MARK in system:
            return json.dumps({"findings": [], "evidence_requests": []})
        if KNOWLEDGE_MARK in system:
            return "[]"
        if DIGEST_MARK in system or SYNOPSIS_MARK in system:
            return json.dumps({
                "summary": "段落概要", "key_entities": [], "key_terms": [],
                "open_threads": [], "translation_notes": [],
                "document_arc": "", "themes": [], "entities": [], "terms": [],
            })
        if TRANSLATOR_MARK in system or "翻译" in system:
            sources = _segments_from_prompt(user)
            return json.dumps([f"第{index + 1}句描写了宁静的河岸场景。" if False else _fake_target(src)
                               for index, src in enumerate(sources)])
        return "[]"

    return llm


_CJK_ONLY = re.compile(r"[A-Za-z]")


def _fake_target(source: str) -> str:
    """Deterministic, residue-free target: keeps bracketed tokens, Chinese body."""
    kept = " ".join(re.findall(r"\[[^\]]+\]|%[sd]|\{[^}]+\}", source or ""))
    body = "该段描述了宁静的河岸场景。"
    return (body + (" " + kept if kept else "")).strip()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def make_paragraphs(count: int) -> List[str]:
    """Deterministic English paragraphs, ~90 chars each: ~4-5 per 1600-char batch."""
    return [
        f"Sentence {index + 1} of the audit corpus describes a quiet riverbank "
        f"scene with reeds and a distant boat."
        for index in range(count)
    ]


def make_glossary(count: int, *, prefix: str = "AuditTerm") -> List[Dict[str, Any]]:
    return [
        {"source": f"{prefix}{index}", "target": f"审计术语{index}",
         "behavior": "translate", "status": "locked", "preferred": f"审计术语{index}"}
        for index in range(count)
    ]


def make_entities(registry: EntityRegistry, count: int) -> None:
    for index in range(count):
        registry.observe(f"Entity{index}", f"实体{index}",
                         entity_type="organization", provenance="human_locked")


def make_candidates(count: int) -> List[Dict[str, Any]]:
    return [
        {"source": f"CandidateTerm{index}", "observed_target": f"候选术语{index}",
         "decision": "", "kind": "concept", "occurrences": [0],
         "provenance": "generated_continuity", "confidence": 0.4}
        for index in range(count)
    ]


def make_sections(count: int, total_segments: int) -> Dict[str, Any]:
    if count <= 0:
        return {}
    size = max(1, total_segments // count)
    sections = []
    for index in range(count):
        start = index * size
        end = total_segments - 1 if index == count - 1 else min(total_segments - 1,
                                                               start + size - 1)
        sections.append({"section_id": f"s{index + 1}", "start_segment": start,
                         "end_segment": end, "topic": f"主题{index + 1}"})
    return {"sections": sections}


def make_section_digests(count: int, total_segments: int) -> List[Dict[str, Any]]:
    if count <= 0:
        return []
    size = max(1, total_segments // count)
    digests = []
    for index in range(count):
        start = index * size
        end = total_segments - 1 if index == count - 1 else min(total_segments - 1,
                                                               start + size - 1)
        digests.append({"section_id": f"s{index + 1}", "start_segment": start,
                        "end_segment": end, "summary": f"第{index + 1}节概要",
                        "translation_notes": []})
    return digests


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
class CaseConfig:
    def __init__(self, segments: int, *, batch_size: int = 4, glossary_size: int = 0,
                 entity_count: int = 0, candidate_count: int = 0, section_count: int = 0,
                 tm_size: int = 0, use_tm: bool = False, enable_review: bool = False,
                 label: str = "") -> None:
        self.segments = segments
        self.batch_size = batch_size
        self.glossary_size = glossary_size
        self.entity_count = entity_count
        self.candidate_count = candidate_count
        self.section_count = section_count
        self.tm_size = tm_size
        self.use_tm = use_tm
        self.enable_review = enable_review
        self.label = label or f"n{segments}"


def run_case(config: CaseConfig) -> Dict[str, Any]:
    """Run one real `translate_stage` under instrumentation, return counters."""
    global REC
    REC = Recorder()
    original_call = core.call_llm
    original_output = core.OUTPUT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="setup-audit-"))
    started = time.perf_counter()
    per_batch_batches: List[Dict[str, Any]] = []
    try:
        core.OUTPUT_DIR = tmp
        core.reset_tm_dir_cache()
        core.call_llm = make_provider()

        paras = make_paragraphs(config.segments)
        state = core.new_job_state(f"{config.label}.docx")
        state["paras"] = paras
        state["target_lang"] = "简体中文"
        glossary = make_glossary(config.glossary_size)
        registry = EntityRegistry()
        if config.entity_count:
            make_entities(registry, config.entity_count)
        if config.candidate_count:
            state["knowledge_candidates"] = make_candidates(config.candidate_count)
        state["entity_registry"] = registry.to_list()

        profile = make_sections(config.section_count, config.segments)
        digests = make_section_digests(config.section_count, config.segments)
        state["section_digests"] = digests
        state["document_profile"] = profile

        if config.tm_size:
            tm = {}
            for index in range(config.tm_size):
                src = f"Unrelated memory sentence number {index}."
                tm[core.tm_scope_key("简体中文", src)] = core.tm_record(
                    f"无关记忆第{index}句。", "简体中文")
            core.save_tm(tm)

        event_times: List[Tuple[str, int, float]] = []
        phase_walls: Dict[str, float] = {}
        previous_marker: List[Tuple[str, float]] = []

        # Map each pipeline event onto the phase that *follows* it. This is how
        # every wrapped sub-step gets attributed to "setup" vs "qa" vs "commit"
        # instead of being reported as one undifferentiated blob.
        phase_after = {
            "generation_started": "setup",
            "generation_done": "deterministic_qa_and_repair",
            "deterministic_qa_done": "semantic_review",
            "semantic_review_done": "knowledge_feedback",
            "semantic_review_skipped": "knowledge_feedback",
            "knowledge_feedback_done": "state_commit",
            "state_commit_done": "commit_tail",
        }

        def _event_proxy(path: Any, event: Any) -> Any:
            if isinstance(event, dict):
                phase = event.get("phase")
                now = time.perf_counter()
                if phase == "generation_started":
                    offset = int(event.get("offset") or 0)
                    REC.start_batch(offset // max(1, config.batch_size))
                    event_times.append(("gen", offset, now))
                if phase in phase_after:
                    if previous_marker:
                        previous, started = previous_marker[0]
                        phase_walls[previous] = phase_walls.get(previous, 0.0) + (now - started)
                    previous_marker[:] = [(phase_after[phase], now)]
                    REC.set_phase(phase_after[phase])
            return original_event(path, event)

        original_event = core._checkpoint.append_event
        core._checkpoint.append_event = _event_proxy

        translate_calls: List[float] = []
        original_translate_batch = core.translate_batch

        def _translate_proxy(*args: Any, **kwargs: Any) -> Any:
            translate_calls.append(time.perf_counter())
            t0 = time.perf_counter()
            out = original_translate_batch(*args, **kwargs)
            REC.add("translator_call_wall", time.perf_counter() - t0)
            return out

        core.translate_batch = _translate_proxy

        with _wrap(terminology_mod, "select_glossary_for_segments",
                   "glossary_selection", _probe_glossary_select), \
             _wrap(terminology_mod, "glossary_block", "glossary_block_render",
                   _probe_string(0)), \
             _wrap(ctx, "select_target_context", "previous_target_selection",
                   _probe_select_target), \
             _wrap(ctx, "digest_for_segment", "section_digest_lookup", _probe_digest), \
             _wrap(ctx, "compile_context_packet", "context_packet_assembly",
                   _probe_packet), \
             _wrap(ctx, "_render_context_packet", "prompt_assembly_budgeting",
                   _probe_packet), \
             _wrap(core, "_batch_section_profile", "section_profile_lookup",
                   _probe_section_profile), \
             _wrap(core, "_runtime_review_context", "review_context_build",
                   _probe_review_context), \
             _wrap(core, "_translation_evidence_index", "evidence_index_construction",
                   _probe_evidence), \
             _wrap(core, "tm_lookup", "tm_lookup", _probe_tm_lookup), \
             _wrap(knowledge_mod, "provisional_hints", "knowledge_candidate_selection",
                   _probe_one_arg_items("candidates")), \
             _wrap(models_mod, "normalize_glossary", "glossary_normalization",
                   _probe_one_arg_items("entries")), \
             _wrap(terminology_mod, "term_matches", "terminology_term_matches",
                   _probe_string(1)), \
             _wrap(EntityRegistry, "hints_for", "entity_hint_construction",
                   _probe_entity_hints):
            with _count_regex_compiles(terminology_mod):
                result = core.translate_stage(
                    state, f"setup-audit-{config.label}", glossary, "StubProvider",
                    "k", "m", "简体中文", "", config.enable_review,
                    use_tm=config.use_tm, document_profile=profile or None,
                    batch_size=config.batch_size,
                    translation_concurrency=1,
                )

        core._checkpoint.append_event = original_event
        core.translate_batch = original_translate_batch

        # True `_setup` wall = generation_started event -> Translator call.
        gen_started = [t for kind, _offset, t in event_times if kind == "gen"]
        for index, t_call in enumerate(translate_calls):
            if index < len(gen_started):
                REC.boundaries[index] = {"setup_s": t_call - gen_started[index]}
                per_batch_batches.append({
                    "batch": index,
                    "setup_s": t_call - gen_started[index],
                })

        elapsed = time.perf_counter() - started
        pairs = result.get("pairs") or []
        return {
            "label": config.label,
            "segments": config.segments,
            "batch_size": config.batch_size,
            "batches": len(gen_started),
            "glossary_size": config.glossary_size,
            "entity_count": config.entity_count,
            "candidate_count": config.candidate_count,
            "section_count": config.section_count,
            "tm_size": config.tm_size,
            "pairs": len(pairs),
            "wall_s": round(elapsed, 3),
            "total_setup_s": round(sum(b["setup_s"] for b in per_batch_batches), 6),
            "phase_walls": {k: round(v, 6) for k, v in sorted(phase_walls.items())},
            "setup_rows": REC.summary_rows(),
            "phase_rows": _table_rows(REC.by_phase, "phase", "step"),
            "caller_rows": _table_rows(REC.by_caller, "step", "caller", min_ms=0.05),
            "per_batch": per_batch_batches,
            "regex_compiles": REC.regex_compiles,
        }
    finally:
        core.call_llm = original_call
        core.OUTPUT_DIR = original_output
        core.reset_tm_dir_cache()


# --------------------------------------------------------------------------- #
# microbenchmarks
# --------------------------------------------------------------------------- #
def micro_tm(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    for size in sizes:
        tm = {}
        for index in range(size):
            src = f"Memory sentence number {index} about riverbanks."
            tm[core.tm_scope_key("简体中文", src)] = core.tm_record(f"记忆{index}。", "简体中文")
        built = time.perf_counter()
        index_map = core.tm_index(tm, "简体中文")
        build_s = time.perf_counter() - built
        probe_source = "Memory sentence number 0 about riverbanks."
        samples = []
        for _ in range(200):
            t0 = time.perf_counter()
            core.tm_lookup(tm, probe_source, index_map, target_lang="简体中文")
            samples.append(time.perf_counter() - t0)
        miss = []
        for _ in range(200):
            t0 = time.perf_counter()
            core.tm_lookup(tm, "A sentence that is not in memory at all.", index_map,
                           target_lang="简体中文")
            miss.append(time.perf_counter() - t0)
        rows.append({
            "tm_entries": size,
            "index_build_ms": round(build_s * 1000, 4),
            "exact_hit_us": round(statistics.median(samples) * 1e6, 3),
            "normalized_probe_us": round(statistics.median(miss) * 1e6, 3),
            "scanned_per_lookup": "O(1) dict + O(len(source)) normalize",
        })
    return rows


def micro_terminology(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    segments = make_paragraphs(4)
    for size in sizes:
        glossary = make_glossary(size)
        samples = []
        for _ in range(5):
            t0 = time.perf_counter()
            terminology_mod.select_glossary_for_segments(segments, glossary)
            samples.append(time.perf_counter() - t0)
        REC.regex_compiles = 0
        with _count_regex_compiles(terminology_mod):
            t0 = time.perf_counter()
            terminology_mod.select_glossary_for_segments(segments, glossary)
            one_shot = time.perf_counter() - t0
            compiles = REC.regex_compiles
        rows.append({
            "terms": size,
            "selection_ms": round(statistics.median(samples) * 1000, 4),
            "selection_ms_one_shot": round(one_shot * 1000, 4),
            "regex_compiles_per_batch": compiles,
            "regex_per_term": round(compiles / max(1, size), 3),
        })
    return rows


def micro_glossary_batch(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    """Cost of ONE 4-segment batch's terminology work as the glossary grows.

    This mirrors what the real pipeline does per batch: glossary selection, the
    deterministic QA pass (which re-checks compliance per segment) and the
    cross-segment conflict scan. Reported separately so the per-batch scaling
    curve is visible without running a whole document.
    """
    rows = []
    sources = make_paragraphs(4)
    targets = [_fake_target(s) for s in sources]
    for size in sizes:
        glossary = make_glossary(size)
        with _count_regex_compiles(terminology_mod):
            REC.regex_compiles = 0
            t0 = time.perf_counter()
            terminology_mod.select_glossary_for_segments(sources, glossary)
            select_s = time.perf_counter() - t0
            t0 = time.perf_counter()
            core.check_translation_batch(sources, targets, glossary, "简体中文", None)
            qa_s = time.perf_counter() - t0
            pairs = [{"source": s, "target": t} for s, t in zip(sources, targets)]
            t0 = time.perf_counter()
            terminology_mod.detect_glossary_conflicts(pairs, glossary)
            conflict_s = time.perf_counter() - t0
            compiles = REC.regex_compiles
        total = select_s + qa_s + conflict_s
        rows.append({
            "terms": size,
            "select_ms": round(select_s * 1000, 3),
            "deterministic_qa_ms": round(qa_s * 1000, 3),
            "conflicts_ms": round(conflict_s * 1000, 3),
            "batch_total_ms": round(total * 1000, 3),
            # NB: this is the number of `re.compile` calls observed during the
            # batch, NOT the number of `term_matches` calls -- `term_matches`
            # calls it once per (term, text) pair *before* the Phase 5 pattern
            # cache existed, so it doubles as the "how much recompiling are we
            # paying for" counter. Renamed from `term_matches_calls` after the
            # audit flagged the old label as misleading.
            "regex_compiles": compiles,
            "us_per_regex_compile": round(total * 1e6 / max(1, compiles), 2),
        })
    return rows


def micro_context(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    for size in sizes:
        pairs = [{"source": f"Source {index}", "target": f"译文{index}",
                  "accepted_target": f"译文{index}", "reviewed": True,
                  "target_provenance": "reviewed"} for index in range(size)]
        # Two implementations, four configurations. `select_target_context` always
        # materialises `list(pairs)[:before_index]` before `limit` is consulted, so
        # both of its `limit` settings are O(offset) -- showing that shrinking the
        # limit alone cannot fix the call site. `recent_target_context` scans the
        # tail and stops at `count`, which is the actual Phase 5 Opt A fix.
        probes = (
            ("select_target_context(limit=2)", 2, False),
            ("select_target_context(limit=max(2,offset))", None, False),
            ("recent_target_context(count=2)", 2, True),
        )
        for mode_label, limit, is_recent in probes:
            samples = []
            for _ in range(5):
                t0 = time.perf_counter()
                if is_recent:
                    ctx.recent_target_context(pairs, size, count=limit)
                else:
                    ctx.select_target_context(pairs, size, limit=limit or max(2, size))
                samples.append(time.perf_counter() - t0)
            rows.append({
                "committed_segments": size,
                "mode": mode_label,
                "select_ms": round(statistics.median(samples) * 1000, 4),
                "ms_per_1000_committed": round(
                    statistics.median(samples) * 1000 / max(1, size) * 1000, 4),
            })
    return rows


def micro_knowledge(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    for size in sizes:
        candidates = make_candidates(size)
        glossary = make_glossary(50)
        samples = []
        for _ in range(5):
            t0 = time.perf_counter()
            knowledge_mod.provisional_hints(candidates, authoritative_entries=glossary)
            samples.append(time.perf_counter() - t0)
        rows.append({
            "candidates": size,
            "provisional_hints_ms": round(statistics.median(samples) * 1000, 4),
            "scanned": size,
        })
    return rows


def micro_entity(sizes: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    texts = make_paragraphs(4)
    for size in sizes:
        registry = EntityRegistry()
        make_entities(registry, size)
        for index in range(min(1, size)):
            registry.lock(f"Entity{index}", f"实体{index}")
        samples = []
        for _ in range(5):
            t0 = time.perf_counter()
            registry.hints_for(texts, glossary_entries=[], limit=12)
            samples.append(time.perf_counter() - t0)
        rows.append({
            "entities": size,
            "hints_for_ms": round(statistics.median(samples) * 1000, 4),
            "scanned": size,
        })
    return rows


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _table_rows(table: Dict[str, Dict[str, Dict[str, float]]], outer_name: str,
                inner_name: str, min_ms: float = 0.0) -> List[Dict[str, Any]]:
    """Flatten a {outer key -> {inner key -> counters}} table into printable rows."""
    rows: List[Dict[str, Any]] = []
    for outer, inner_map in table.items():
        for inner, cell in inner_map.items():
            if cell["s"] * 1000 < min_ms:
                continue
            rows.append({
                outer_name: outer,
                inner_name: inner,
                "calls": int(cell["calls"]),
                "total_ms": round(cell["s"] * 1000, 3),
                "items_scanned": int(cell["items"]),
            })
    rows.sort(key=lambda row: (-row["total_ms"], str(row[outer_name])))
    return rows


def _print_table(title: str, rows: Sequence[Dict[str, Any]]) -> None:
    print(f"\n{title}")
    if not rows:
        print("  (no rows)")
        return
    keys = list(rows[0].keys())
    widths = [max(len(str(k)), *(len(str(r.get(k, ""))) for r in rows)) for k in keys]
    print("  " + " | ".join(str(k).ljust(w) for k, w in zip(keys, widths)))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in rows:
        print("  " + " | ".join(str(row.get(k, "")).ljust(w) for k, w in zip(keys, widths)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="100,1000,5000",
                        help="comma separated segment counts for the pipeline audit")
    parser.add_argument("--micro-sizes", default="100,1000,5000,10000,20000")
    parser.add_argument("--tm-sizes", default="100,1000,10000,50000")
    parser.add_argument("--glossary-size", type=int, default=0)
    parser.add_argument("--entity-count", type=int, default=0)
    parser.add_argument("--candidate-count", type=int, default=0)
    parser.add_argument("--section-count", type=int, default=0)
    parser.add_argument("--section-every", type=int, default=0,
                        help="one section per N segments (keeps batches intact)")
    parser.add_argument("--review", action="store_true", help="enable the reviewer phase")
    parser.add_argument("--micro-only", action="store_true")
    parser.add_argument("--json", dest="json_out", default="")
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args(argv)

    payload: Dict[str, Any] = {"pipeline": [], "micro": {}}

    if not args.micro_only:
        sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
        for size in sizes:
            section_count = args.section_count
            if args.section_every:
                section_count = max(1, -(-size // args.section_every))
            config = CaseConfig(
                size, batch_size=args.batch_size, glossary_size=args.glossary_size,
                entity_count=args.entity_count, candidate_count=args.candidate_count,
                section_count=section_count, enable_review=args.review,
            )
            print(f"[pipeline] segments={size} batch_size={args.batch_size} "
                  f"glossary={args.glossary_size} sections={section_count} ...",
                  flush=True)
            result = run_case(config)
            payload["pipeline"].append(result)
            print(f"  batches={result['batches']} wall={result['wall_s']}s "
                  f"setup_total={result['total_setup_s']}s "
                  f"regex_compiles={result['regex_compiles']}", flush=True)

    micro_sizes = [int(s) for s in args.micro_sizes.split(",") if s.strip()]
    tm_sizes = [int(s) for s in args.tm_sizes.split(",") if s.strip()]
    payload["micro"]["tm"] = micro_tm(tm_sizes)
    payload["micro"]["terminology"] = micro_terminology(micro_sizes[:3])
    payload["micro"]["glossary_batch"] = micro_glossary_batch(micro_sizes[:4])
    payload["micro"]["context"] = micro_context(micro_sizes)
    payload["micro"]["knowledge"] = micro_knowledge(micro_sizes)
    payload["micro"]["entity"] = micro_entity(micro_sizes)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")

    print("\n================ PIPELINE SUMMARY ================")
    for result in payload["pipeline"]:
        print(f"\n--- {result['label']} (segments={result['segments']}, "
              f"batches={result['batches']}, wall={result['wall_s']}s, "
              f"setup_total={result['total_setup_s']}s, "
              f"regex_compiles={result['regex_compiles']}) ---")
        _print_table("setup step breakdown", result["setup_rows"])
        if result.get("phase_rows"):
            _print_table("step cost by PIPELINE PHASE", result["phase_rows"])
        if result.get("caller_rows"):
            _print_table("step cost by CALL SITE", result["caller_rows"])

    print("\n================ MICRO ================")
    for name, rows in payload["micro"].items():
        _print_table(f"micro: {name}", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
