#!/usr/bin/env python
"""Translation critical-path attribution — Performance Phase 6 (read-only).

Why this script exists
----------------------
Phase 5 attributed ``_setup`` and found two real O(n^2) scans.  The next two
buckets on the critical path -- ``semantic_review`` and ``commit_tail`` -- were
still single opaque numbers.  Before touching either, this script answers three
questions with measurement instead of inference:

1. **Where does ``semantic_review`` wall time go** -- provider wait, local CPU,
   or queue wait?  The profiler records ``perf_counter`` *and*
   ``thread_time`` per phase, so ``wall - cpu`` isolates blocking.
2. **What is ``commit_tail + state_commit`` actually made of** -- pair apply,
   review-truth update, knowledge feedback, TM eligibility, journal append,
   journal fsync, or the full snapshot?
3. **Is the 101.9s ``semantic_review`` number "not model waiting"?**  Three
   controlled cases replace the earlier inference: a zero-latency reviewer, a
   reviewer with a fixed *local* delay (10/50/100ms), and a context-heavy
   reviewer with no network at all.

How it stays read-only
----------------------
The recorder observes only.  It never edits ``core.py`` behaviour, never
changes a prompt, contract, or persisted state: ``core.call_llm`` is replaced
with a deterministic offline stub, and every stage boundary is derived from
markers the pipeline already emits (``generation_started``, ``generation_done``,
``deterministic_qa_done``, ``semantic_review_done``, ``knowledge_feedback_done``,
``state_commit_done``) plus the existing ``translate_batch`` call.  No model is
ever contacted.

Usage
-----
    # baseline sweep
    venv/bin/python scripts/translation_critical_path_audit.py --sizes 100,1000

    # reviewer latency cases A / B / C
    venv/bin/python scripts/translation_critical_path_audit.py --latency-cases

    # everything, to JSON
    venv/bin/python scripts/translation_critical_path_audit.py --all --json out.json
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
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402
from folith import checkpoint as _checkpoint  # noqa: E402
from folith import critical_path as _cp  # noqa: E402
from folith import persistence_profiler as _pprof  # noqa: E402
from folith.entity_registry import EntityRegistry  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402

import translation_setup_audit as audit  # noqa: E402  (fixtures + stub provider)

TRANSLATOR_MARK = audit.TRANSLATOR_MARK
REVIEWER_MARK = audit.REVIEWER_MARK
EVIDENCE_ROUND_MARK = "【按审校请求返回的证据】"


# --------------------------------------------------------------------------- #
# stage timeline (marker driven, read-only)
# --------------------------------------------------------------------------- #
#: Stage order inside one batch.  ``commit_tail_residual`` is everything after
#: ``state_commit_done`` up to the next batch's ``generation_started``.
STAGE_ORDER = (
    "setup",
    "translation_generation",
    "translation_postprocess",
    "deterministic_qa_and_repair",
    "semantic_review",
    "knowledge_feedback",
    "state_commit",
    "commit_tail_residual",
)

#: Which stage the *next* marker opens, keyed by the marker it follows.
_OPEN_AFTER = {
    "generation_done": "deterministic_qa_and_repair",
    "deterministic_qa_done": "semantic_review",
    "semantic_review_done": "knowledge_feedback",
    "semantic_review_skipped": "knowledge_feedback",
    "knowledge_feedback_done": "state_commit",
    "knowledge_feedback_skipped": "state_commit",
    "knowledge_feedback_cache_hit": "state_commit",
    "state_commit_done": "commit_tail_residual",
}


class Timeline:
    """Aggregate per-batch stage wall/CPU from markers the pipeline emits."""

    def __init__(self) -> None:
        self.stages: Dict[str, Dict[str, float]] = {
            name: {"wall_s": 0.0, "cpu_s": 0.0, "calls": 0} for name in STAGE_ORDER}
        self.batches = 0
        self._open: Optional[str] = None
        self._t0 = 0.0
        self._c0 = 0.0

    @staticmethod
    def _clocks() -> Tuple[float, float]:
        return time.perf_counter(), time.process_time()

    def _open_stage(self, name: str) -> None:
        self._open = name
        self._t0, self._c0 = self._clocks()

    def _close_stage(self) -> None:
        if self._open is None:
            return
        now, cpu = self._clocks()
        row = self.stages[self._open]
        row["wall_s"] += max(0.0, now - self._t0)
        row["cpu_s"] += max(0.0, cpu - self._c0)
        row["calls"] += 1
        self._open = None

    def mark(self, name: str) -> None:
        """Close whatever is open, then open ``name``."""
        self._close_stage()
        self._open_stage(name)

    def event(self, phase: str) -> None:
        if phase == "generation_started":
            # A new batch begins: the previous batch's tail ends here.
            if self._open == "commit_tail_residual":
                self._close_stage()
            self.batches += 1
            self._open_stage("setup")
            return
        if phase in _OPEN_AFTER:
            self.mark(_OPEN_AFTER[phase])

    def finish(self) -> None:
        self._close_stage()

    def table(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for name in STAGE_ORDER:
            row = self.stages[name]
            out[name] = {
                "wall_s": round(row["wall_s"], 6),
                "cpu_s": round(row["cpu_s"], 6),
                "calls": int(row["calls"]),
            }
        return out


# --------------------------------------------------------------------------- #
# deterministic offline provider with controllable reviewer behaviour
# --------------------------------------------------------------------------- #
_SEG_RE = re.compile(r"^segment_id: (\d+)\n原文：(.*)\n译文：(.*)$", re.M)


def _review_segments(user: str) -> List[Tuple[int, str, str]]:
    return [(int(m.group(1)), m.group(2), m.group(3))
            for m in _SEG_RE.finditer(user)]


def make_provider(*, reviewer_latency_s: float = 0.0,
                  reviewer_mode: str = "clean") -> Callable[..., str]:
    """Deterministic offline provider.

    ``reviewer_latency_s`` sleeps *before* returning a reviewer response.  It is
    used to prove the profiler attributes delay to ``reviewer_transport`` rather
    than to local CPU: a sleep consumes no ``thread_time``, so a correct
    profiler must report the added seconds as ``wait_s``.
    """
    base = audit.make_provider()

    def llm(provider: str, api_key: str, model: str, system: str, user: str,
            temperature: float = 0.1, **kwargs: Any) -> str:
        if REVIEWER_MARK not in system:
            return base(provider, api_key, model, system, user,
                        temperature=temperature, **kwargs)
        if reviewer_latency_s:
            time.sleep(reviewer_latency_s)
        if reviewer_mode == "clean":
            return json.dumps({"findings": [], "evidence_requests": []})
        segments = _review_segments(user)
        if not segments:
            return json.dumps({"findings": [], "evidence_requests": []})
        findings = []
        for segment_id, source, target in segments[:1]:
            findings.append({
                "segment_id": segment_id,
                "category": "semantic_accuracy",
                "severity": "actionable",
                "summary": "建议复核该段语气",
                "source_span": None,
                "target_span": None,
                "explanation": "审计用固定 finding，用于触发建议覆盖与盲审路径。",
                "recommendation": "确认语气是否与全文一致。",
                "confidence": 0.5,
                "detector": "Semantic QA",
                # Deliberately a *variant* of what the translator produced, so
                # the suggestion is a real change and the overlay is exercised.
                "suggested_target": "经审校修订：" + audit._fake_target(source),
            })
        if reviewer_mode == "suggesting" and EVIDENCE_ROUND_MARK not in user:
            return json.dumps({
                "findings": findings,
                "evidence_requests": [{
                    "tool": "get_segment",
                    "arguments": {"segment_id": segments[0][0]},
                }],
            })
        return json.dumps({"findings": findings, "evidence_requests": []})

    return llm


def make_reviewer(provider_fn: Callable[..., str], *, reviewer_latency_s: float = 0.0):
    """Adapter matching the reviewer call signature, wrapping the provider.

    The reviewer path already routes through the same provider; this exists so a
    future case can add latency without touching the translator's stub.
    """
    def call(provider, api_key, model, system, user, **kwargs):
        return provider_fn(provider, api_key, model, system, user, **kwargs)
    return call


# --------------------------------------------------------------------------- #
# one measured run
# --------------------------------------------------------------------------- #
class RunConfig:
    def __init__(self, segments: int, *, batch_size: int = 4,
                 glossary_size: int = 0, entity_count: int = 0,
                 candidate_count: int = 0, section_count: int = 0,
                 enable_review: bool = True, reviewer_latency_s: float = 0.0,
                 reviewer_mode: str = "clean", label: str = "") -> None:
        self.segments = segments
        self.batch_size = batch_size
        self.glossary_size = glossary_size
        self.entity_count = entity_count
        self.candidate_count = candidate_count
        self.section_count = section_count
        self.enable_review = enable_review
        self.reviewer_latency_s = reviewer_latency_s
        self.reviewer_mode = reviewer_mode
        self.label = label or f"n{segments}"


@contextmanager
def _patch(obj: Any, attr: str, value: Any):
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


def run_case(config: RunConfig, *, profile: bool = True) -> Dict[str, Any]:
    """Run one real ``translate_stage`` and return the attribution tables."""
    original_call = core.call_llm
    original_output = core.OUTPUT_DIR
    tmp = Path(tempfile.mkdtemp(prefix="critical-path-"))
    timeline = Timeline()
    original_event = _checkpoint.append_event
    original_translate = core.translate_batch

    _cp.reset()
    _pprof.reset()
    if profile:
        _cp.enable()
        _pprof.enable()
    else:
        _cp.disable()
        _pprof.disable()
    core.reset_runtime_persistence_stats()

    def _event_proxy(path: Any, event: Any) -> Any:
        if isinstance(event, dict):
            timeline.event(str(event.get("phase") or ""))
        return original_event(path, event)

    def _translate_proxy(*args: Any, **kwargs: Any) -> Any:
        timeline.mark("translation_generation")
        try:
            return original_translate(*args, **kwargs)
        finally:
            timeline.mark("translation_postprocess")

    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        core.OUTPUT_DIR = tmp
        core.reset_tm_dir_cache()
        core.call_llm = make_provider(
            reviewer_latency_s=config.reviewer_latency_s,
            reviewer_mode=config.reviewer_mode)
        _checkpoint.append_event = _event_proxy
        core.translate_batch = _translate_proxy

        paras = audit.make_paragraphs(config.segments)
        state = core.new_job_state(f"{config.label}.docx")
        state["paras"] = paras
        state["target_lang"] = "简体中文"
        glossary = audit.make_glossary(config.glossary_size)
        registry = EntityRegistry()
        if config.entity_count:
            audit.make_entities(registry, config.entity_count)
        if config.candidate_count:
            state["knowledge_candidates"] = audit.make_candidates(
                config.candidate_count)
        state["entity_registry"] = registry.to_list()

        profile_map = audit.make_sections(config.section_count, config.segments)
        digests = audit.make_section_digests(config.section_count, config.segments)
        state["section_digests"] = digests
        state["document_profile"] = profile_map

        profiler = PipelineProfiler(tmp / "performance.json", run_id="phase6")
        result = core.translate_stage(
            state, f"critical-path-{config.label}", glossary, "StubProvider",
            "k", "m", "简体中文", "", config.enable_review,
            use_tm=False, document_profile=profile_map or None,
            batch_size=config.batch_size,
            translation_concurrency=1,
            profiler=profiler,
        )
        timeline.finish()
        cpu_elapsed = time.process_time() - cpu_started
        wall_elapsed = time.perf_counter() - wall_started

        return {
            "label": config.label,
            "segments": config.segments,
            "batch_size": config.batch_size,
            "glossary_size": config.glossary_size,
            "entity_count": config.entity_count,
            "candidate_count": config.candidate_count,
            "section_count": config.section_count,
            "enable_review": config.enable_review,
            "reviewer_latency_s": config.reviewer_latency_s,
            "reviewer_mode": config.reviewer_mode,
            "profile": profile,
            "wall_s": round(wall_elapsed, 4),
            "cpu_s": round(cpu_elapsed, 4),
            "batches": timeline.batches,
            "pairs": len(result.get("pairs") or []),
            "tmp_dir": str(tmp),
            "stages": timeline.table(),
            "phases": _cp.snapshot()["phases"] if profile else {},
            "counters": _cp.snapshot()["counters"] if profile else {},
            "persistence": _pprof.snapshot() if profile else {},
        }
    finally:
        core.call_llm = original_call
        core.OUTPUT_DIR = original_output
        core.reset_tm_dir_cache()
        _checkpoint.append_event = original_event
        core.translate_batch = original_translate


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def _fmt(value: float, width: int = 9) -> str:
    return f"{value:>{width}.3f}"


def print_stage_table(row: Dict[str, Any]) -> None:
    stages = row["stages"]
    print(f"  {'stage':<28} {'wall_s':>9} {'cpu_s':>9} {'wait_s':>9} {'calls':>7}")
    for name in STAGE_ORDER:
        s = stages[name]
        wait = max(0.0, s["wall_s"] - s["cpu_s"])
        print(f"  {name:<28} {_fmt(s['wall_s'])} {_fmt(s['cpu_s'])} "
              f"{_fmt(wait)} {s['calls']:>7}")
    print(f"  {'TOTAL':<28} {_fmt(row['wall_s'])} {_fmt(row['cpu_s'])} "
          f"{_fmt(max(0.0, row['wall_s'] - row['cpu_s']))} {row['batches']:>7}")


def print_phase_table(row: Dict[str, Any], title: str,
                      names: Sequence[str]) -> None:
    phases = row.get("phases") or {}
    print(f"  {title}")
    print(f"    {'phase':<34} {'wall_s':>9} {'cpu_s':>9} {'wait_s':>9} "
          f"{'calls':>7} {'items':>12} {'objects':>12} {'chars':>10}")
    total_wall = 0.0
    for name in names:
        p = phases.get(name)
        if not p:
            continue
        total_wall += p["total_wall_s"]
        print(f"    {name:<34} {_fmt(p['total_wall_s'])} "
              f"{_fmt(p['total_cpu_s'])} {_fmt(p['wait_s'])} {p['calls']:>7} "
              f"{p['items']:>12} {p.get('objects', 0):>12} {p['chars']:>10}")
    print(f"    {'':<34} {_fmt(total_wall)}")
    return total_wall


def print_counters(row: Dict[str, Any]) -> None:
    counters = row.get("counters") or {}
    if not counters:
        return
    print("  counters: " + ", ".join(f"{k}={v:g}" for k, v in sorted(counters.items())))


def print_persistence(row: Dict[str, Any]) -> None:
    persist = row.get("persistence") or {}
    phases = persist.get("phases") or {}
    if not phases:
        return
    print(f"  persistence (save_calls={persist.get('save_calls')}):")
    print(f"    {'phase':<28} {'total_s':>9} {'calls':>7} {'bytes_w':>12} {'items':>12}")
    for name, p in sorted(phases.items(), key=lambda kv: -kv[1]["total_wall_s"]):
        if not p["call_count"] and not p["bytes_written"]:
            continue
        print(f"    {name:<28} {p['total_wall_s']:>9.4f} {p['call_count']:>7} "
              f"{p['bytes_written']:>12} {p['items_traversed']:>12}")


# --------------------------------------------------------------------------- #
# case definitions
# --------------------------------------------------------------------------- #
BASELINE_SIZES = (100, 1000, 5000, 10000)
LATENCY_SIZES = (1000,)
LATENCY_SECONDS = (0.0, 0.01, 0.05, 0.10)

#: Everything case C piles on at once.  ``section_count`` is passed separately
#: by the caller (it has to scale with the segment count), so it is not here.
CONTEXT_HEAVY = dict(glossary_size=1000, entity_count=1000,
                     candidate_count=1000)


def baseline_configs(sizes: Sequence[int]) -> List[RunConfig]:
    return [RunConfig(n, section_count=max(1, n // 25), label=f"baseline_n{n}")
            for n in sizes]


def latency_configs(size: int, *, mode: str = "suggesting") -> List[RunConfig]:
    configs = [RunConfig(size, section_count=max(1, size // 25),
                         reviewer_latency_s=s, reviewer_mode=mode,
                         label=f"latency_{int(s * 1000)}ms")
               for s in LATENCY_SECONDS]
    configs.append(RunConfig(
        size, section_count=max(1, size // 25), reviewer_latency_s=0.0,
        reviewer_mode=mode, label="context_heavy",
        **CONTEXT_HEAVY))
    return configs


def latency_case_labels() -> List[str]:
    return [f"latency_{int(s * 1000)}ms" for s in LATENCY_SECONDS] + ["context_heavy"]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", default="")
    parser.add_argument("--latency-size", type=int, default=1000)
    parser.add_argument("--latency-cases", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--no-profile", action="store_true",
                        help="run with the critical-path profiler disabled")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    profile = not args.no_profile
    sizes = tuple(int(x) for x in args.sizes.split(",") if x.strip()) \
        or BASELINE_SIZES

    do_baseline = args.baseline or args.all or not args.latency_cases
    do_latency = args.latency_cases or args.all

    payload: Dict[str, Any] = {
        "profile": profile,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runs": [],
    }

    if do_baseline:
        print("=== baseline sweep ===")
        for config in baseline_configs(sizes):
            row = run_case(config, profile=profile)
            payload["runs"].append(row)
            print(f"\n[{row['label']}] segments={row['segments']} "
                  f"batches={row['batches']} wall={row['wall_s']}s "
                  f"cpu={row['cpu_s']}s")
            print_stage_table(row)
            print_phase_table(row, "review phases", _cp.REVIEW_INPUT_PHASES
                              + _cp.REVIEW_PACKET_PHASES + _cp.REVIEWER_PHASES)
            print_phase_table(row, "commit phases", _cp.COMMIT_PHASES)
            print_phase_table(row, "io phases", _cp.IO_PHASES)
            print_counters(row)
            print_persistence(row)

    if do_latency:
        print("\n=== reviewer latency / context cases ===")
        for config in latency_configs(args.latency_size):
            row = run_case(config, profile=profile)
            payload["runs"].append(row)
            print(f"\n[{row['label']}] reviewer_latency="
                  f"{row['reviewer_latency_s']}s mode={row['reviewer_mode']} "
                  f"wall={row['wall_s']}s cpu={row['cpu_s']}s")
            print_stage_table(row)
            print_phase_table(row, "review phases", _cp.REVIEW_INPUT_PHASES
                              + _cp.REVIEW_PACKET_PHASES + _cp.REVIEWER_PHASES)
            print_phase_table(row, "commit phases", _cp.COMMIT_PHASES)
            print_counters(row)

    if args.json:
        _write_json(Path(args.json), payload)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
