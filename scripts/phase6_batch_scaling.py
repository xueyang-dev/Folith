#!/usr/bin/env python
"""Per-batch incremental attribution -- Performance Phase 6 (read-only).

Why this script exists
----------------------
A stage total cannot distinguish "this batch does a fixed amount of work" from
"this batch rescans everything committed so far".  Both look like one number.
Phase 6 (§7) asks for exactly that distinction: ``items_scanned`` versus
``committed_segment_count``, so a per-batch cost that grows with the batch index
can be labelled *cumulative O(n^2)* instead of guessed at.

What it does
------------
Runs **one** real ``translate_stage`` with the critical-path profiler enabled and
samples the profiler's cumulative scalars at every batch boundary, storing the
*delta* since the previous boundary.  Because the sample is a diff of running
totals (:meth:`CriticalPathProfiler.totals`), the sampler itself is O(phases)
per batch and does not perturb the scaling it observes.

It also wraps a small set of self-contained helpers (``load_runtime_state``,
``_runtime_cancel_requested``) and attributes each call to the stage that was
open when it ran -- so an untimed residue can be attributed by call site rather
than by subtraction.

Output
------
* a per-decile table: mean per-batch wall/CPU/items for the first decile versus
  the last, plus an implied exponent ``k`` (``t ~ index**k``).  ``k`` near 0 is
  flat/constant work; ``k`` near 1 means the per-batch cost is linear in the
  document length, i.e. *cumulative O(n^2)*.
* a call-site table for the wrapped helpers, grouped by stage.

Read-only guarantees
--------------------
Same contract as ``translation_critical_path_audit``: ``core.call_llm`` is
replaced with the deterministic offline stub, ``core.OUTPUT_DIR`` is redirected
to a temporary directory, and nothing about prompts, contracts or persisted
state is changed.  No model is ever contacted.

Usage
-----
    venv/bin/python scripts/phase6_batch_scaling.py --segments 4000
    venv/bin/python scripts/phase6_batch_scaling.py --segments 4000 --json out.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
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

import translation_critical_path_audit as cpa  # noqa: E402
import translation_setup_audit as audit  # noqa: E402

STAGE_ORDER = cpa.STAGE_ORDER

#: Helpers whose call sites are worth attributing to a stage.  These are the
#: candidates an untimed residual (stage total minus instrumented phases) could
#: hide in.  ``(dotted target, short label)`` -- each is resolved to the module
#: that actually owns the attribute, so ``folith.context.f`` and
#: ``core.f`` are both patchable without a hard-coded import list.
DEFAULT_SPIES: Sequence[Tuple[str, str]] = (
    ("core.load_runtime_state", "load_runtime_state"),
    ("core._runtime_cancel_requested", "cancel_check"),
    ("core._batch_section_profile", "batch_section_profile"),
    ("core._runtime_review_dependency_ids", "review_dependency_ids"),
    ("folith.terminology.select_glossary_for_segments", "select_glossary"),
    ("folith.terminology.glossary_block", "glossary_block"),
    # ``recent_target_context`` early-exits once it has ``count`` items, so its
    # typical scan is O(count).  ``select_target_context`` only early-exits when
    # the *top* provenance level fills ``limit`` -- a machine-translated document
    # has no human_accepted segment, so it scans to index 0 every batch.
    ("folith.context.recent_target_context", "recent_target_context"),
    ("folith.context.select_target_context", "select_target_context"),
    ("folith.context.digest_for_segment", "digest_for_segment"),
    ("folith.context.compile_context_packet", "compile_context_packet"),
    ("folith.knowledge.provisional_hints", "provisional_hints"),
)

#: Best-effort "how much did this call look at" per label.  Returning 0 simply
#: leaves the items column empty rather than inventing a number.  For the two
#: context selectors this is a *declared window* (what the call is allowed to
#: examine), not a measured element count -- the measured evidence is the wall
#: time, and the two agree only when the selector really does scan that far.
_SIZE_OF: Dict[str, Callable[[Tuple[Any, ...], Dict[str, Any]], int]] = {
    "recent_target_context":
        lambda a, k: min(int(a[2]) if len(a) > 2 else 2,
                         int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0),
    "select_target_context":
        lambda a, k: int(a[1]) if len(a) > 1 and isinstance(a[1], int) else 0,
    "digest_for_segment":
        lambda a, k: len(a[0]) if a and hasattr(a[0], "__len__") else 0,
    "batch_section_profile":
        lambda a, k: len((a[0] or {}).get("sections") or [])
        if a and isinstance(a[0], dict) else 0,
    "select_glossary":
        lambda a, k: len(a[1]) if len(a) > 1 and hasattr(a[1], "__len__") else 0,
    "glossary_block":
        lambda a, k: len(a[0]) if a and hasattr(a[0], "__len__") else 0,
    "provisional_hints":
        lambda a, k: len(a[0]) if a and hasattr(a[0], "__len__") else 0,
    "compile_context_packet":
        lambda a, k: len(k.get("current_batch") or []),
}


def _resolve(dotted: str) -> Tuple[Any, str]:
    """Return ``(module, attr)`` for a dotted path, importing what is needed."""
    import importlib

    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        return module, ".".join(parts[split:])
    raise ImportError(f"cannot resolve {dotted!r} to a module attribute")


# --------------------------------------------------------------------------- #
# per-batch sampler
# --------------------------------------------------------------------------- #
class BatchDeltaRecorder:
    """Stage timeline plus per-batch diffs of the phase totals."""

    def __init__(self) -> None:
        self._stage: Optional[str] = None
        self._t0 = 0.0
        self._c0 = 0.0
        self._stage_cum: Dict[str, List[float]] = {}
        self._prev_stage: Dict[str, List[float]] = {}
        self._prev_phase: Dict[str, Dict[str, float]] = {}
        self._samples: List[Dict[str, Any]] = []
        self._stage_open_for_batch = False
        self.batches = 0
        self._pending: Optional[Dict[str, Dict[str, float]]] = None
        self._counters_prev: Dict[str, float] = {}

    # -- stage timeline ----------------------------------------------------
    @staticmethod
    def _clocks() -> Tuple[float, float]:
        return time.perf_counter(), time.process_time()

    def _open_stage(self, name: str) -> None:
        self._stage = name
        self._t0, self._c0 = self._clocks()

    def _close_stage(self) -> None:
        if self._stage is None:
            return
        now, cpu = self._clocks()
        acc = self._stage_cum.setdefault(self._stage, [0.0, 0.0, 0.0])
        acc[0] += max(0.0, now - self._t0)
        acc[1] += max(0.0, cpu - self._c0)
        acc[2] += 1.0
        self._stage = None

    def mark(self, name: str) -> None:
        self._close_stage()
        self._open_stage(name)

    # -- marker bridge -----------------------------------------------------
    def event(self, phase: str) -> None:
        if phase == "generation_started":
            if self._stage == "commit_tail_residual":
                self._close_stage()
            if self._stage_open_for_batch:
                self._finalize()
            self._stage_open_for_batch = True
            self.batches += 1
            self._open_stage("setup")
            return
        if phase in cpa._OPEN_AFTER:
            self.mark(cpa._OPEN_AFTER[phase])
        if phase in ("state_commit_done", "semantic_review_done",
                     "semantic_review_skipped"):
            self._pending = _cp.totals()

    def finish(self) -> None:
        if self._stage == "commit_tail_residual":
            self._close_stage()
        if self._stage_open_for_batch:
            self._finalize()
        self._stage_open_for_batch = False

    # -- one row per batch -------------------------------------------------
    def _finalize(self) -> None:
        stages: Dict[str, Dict[str, float]] = {}
        for name in STAGE_ORDER:
            acc = self._stage_cum.get(name, [0.0, 0.0, 0.0])
            prev = self._prev_stage.get(name, [0.0, 0.0, 0.0])
            stages[name] = {
                "wall_s": acc[0] - prev[0],
                "cpu_s": acc[1] - prev[1],
                "calls": acc[2] - prev[2],
            }
        self._prev_stage = {k: list(v) for k, v in self._stage_cum.items()}

        phases: Dict[str, Dict[str, float]] = {}
        counters: Dict[str, float] = {}
        sample = self._pending
        self._pending = None
        if sample:
            for name, cur in sample["phases"].items():
                prev = self._prev_phase.get(name)
                if prev is None:
                    # First sample: the phase is brand new this batch, so the
                    # running total *is* this batch's delta.
                    prev = {k: 0.0 for k in cur}
                phases[name] = {
                    "calls": cur["calls"] - prev.get("calls", 0.0),
                    "wall_s": cur["wall_s"] - prev.get("wall_s", 0.0),
                    "cpu_s": cur["cpu_s"] - prev.get("cpu_s", 0.0),
                    "items": cur["items"] - prev.get("items", 0.0),
                    "chars": cur["chars"] - prev.get("chars", 0.0),
                    "bytes_written": cur["bytes_written"] - prev.get("bytes_written", 0.0),
                }
            self._prev_phase = sample["phases"]
            for name, value in sample["counters"].items():
                counters[name] = value - self._counters_prev.get(name, 0.0)
            self._counters_prev = dict(sample["counters"])

        self._samples.append({
            "batch": len(self._samples),
            "stages": stages,
            "phases": phases,
            "counters": counters,
        })

    # -- reporting ---------------------------------------------------------
    def rows(self) -> List[Dict[str, Any]]:
        return self._samples


class StageSpy:
    """Attribute wrapped helper calls to the stage that was open.

    Attribution only: a helper that is *also* instrumented inline would be
    counted twice, so the table is read as "who called what, and when", never as
    an additive budget.
    """

    def __init__(self, recorder: BatchDeltaRecorder) -> None:
        self._rec = recorder
        self._originals: List[Tuple[Any, str, Any]] = []
        self.stats: Dict[str, Dict[str, float]] = {}
        self.missing: List[str] = []

    def wrap(self, dotted: str, label: str) -> None:
        try:
            module, attr = _resolve(dotted)
        except ImportError:
            self.missing.append(dotted)
            return
        if not hasattr(module, attr):
            self.missing.append(dotted)
            return
        original = getattr(module, attr)
        self._originals.append((module, attr, original))
        recorder = self._rec
        size_of = _SIZE_OF.get(label)

        def proxy(*args: Any, **kwargs: Any) -> Any:
            t0, c0 = time.perf_counter(), time.process_time()
            try:
                return original(*args, **kwargs)
            finally:
                now, cpu = time.perf_counter(), time.process_time()
                key = f"{label} @ {recorder._stage or '?'}"
                row = self.stats.setdefault(
                    key, {"calls": 0.0, "wall_s": 0.0, "cpu_s": 0.0, "items": 0.0})
                row["calls"] += 1.0
                row["wall_s"] += max(0.0, now - t0)
                row["cpu_s"] += max(0.0, cpu - c0)
                if size_of is not None:
                    try:
                        row["items"] += float(size_of(args, kwargs))
                    except Exception:
                        pass

        setattr(module, attr, proxy)

    def restore(self) -> None:
        for module, attr, original in self._originals:
            setattr(module, attr, original)
        self._originals = []


# --------------------------------------------------------------------------- #
# one measured run
# --------------------------------------------------------------------------- #
def run(
    segments: int,
    *,
    batch_size: int = 4,
    enable_review: bool = True,
    reviewer_mode: str = "clean",
    spies: Sequence[Tuple[str, str]] = DEFAULT_SPIES,
    glossary_size: int = 0,
    entity_count: int = 0,
    candidate_count: int = 0,
    label: str = "",
) -> Dict[str, Any]:
    """Run one real ``translate_stage`` and return per-batch deltas."""
    label = label or f"scale_n{segments}"
    original_call = core.call_llm
    original_output = core.OUTPUT_DIR
    original_event = _checkpoint.append_event
    original_translate = core.translate_batch
    tmp = Path(tempfile.mkdtemp(prefix="batch-scaling-"))

    recorder = BatchDeltaRecorder()
    stage_spy = StageSpy(recorder)

    _cp.reset()
    _pprof.reset()
    _cp.enable()
    _pprof.enable()
    core.reset_runtime_persistence_stats()

    def _event_proxy(path: Any, event: Any) -> Any:
        if isinstance(event, dict):
            recorder.event(str(event.get("phase") or ""))
        return original_event(path, event)

    def _translate_proxy(*args: Any, **kwargs: Any) -> Any:
        recorder.mark("translation_generation")
        try:
            return original_translate(*args, **kwargs)
        finally:
            recorder.mark("translation_postprocess")

    wall_started, cpu_started = time.perf_counter(), time.process_time()
    try:
        core.OUTPUT_DIR = tmp
        core.reset_tm_dir_cache()
        core.call_llm = cpa.make_provider(reviewer_mode=reviewer_mode)
        _checkpoint.append_event = _event_proxy
        core.translate_batch = _translate_proxy
        for dotted, spy_label in spies:
            stage_spy.wrap(dotted, spy_label)

        state = core.new_job_state(f"{label}.docx")
        state["paras"] = audit.make_paragraphs(segments)
        state["target_lang"] = "简体中文"
        glossary = audit.make_glossary(glossary_size)
        registry = EntityRegistry()
        if entity_count:
            audit.make_entities(registry, entity_count)
        if candidate_count:
            state["knowledge_candidates"] = audit.make_candidates(candidate_count)
        state["entity_registry"] = registry.to_list()

        section_count = max(1, segments // 25)
        profile_map = audit.make_sections(section_count, segments)
        state["section_digests"] = audit.make_section_digests(section_count, segments)
        state["document_profile"] = profile_map

        profiler = PipelineProfiler(tmp / "performance.json", run_id="phase6-scale")
        result = core.translate_stage(
            state, f"batch-scaling-{label}", glossary, "StubProvider",
            "k", "m", "简体中文", "", enable_review,
            use_tm=False, document_profile=profile_map or None,
            batch_size=batch_size,
            translation_concurrency=1,
            profiler=profiler,
        )
        recorder.finish()
        return {
            "label": label,
            "segments": segments,
            "batch_size": batch_size,
            "enable_review": enable_review,
            "reviewer_mode": reviewer_mode,
            "glossary_size": glossary_size,
            "entity_count": entity_count,
            "candidate_count": candidate_count,
            "wall_s": round(time.perf_counter() - wall_started, 4),
            "cpu_s": round(time.process_time() - cpu_started, 4),
            "batches": recorder.batches,
            "pairs": len(result.get("pairs") or []),
            "rows": recorder.rows(),
            "spies": {k: dict(v) for k, v in sorted(stage_spy.stats.items())},
            "spies_missing": list(stage_spy.missing),
            "phases_total": _cp.snapshot()["phases"],
            "counters": _cp.snapshot()["counters"],
        }
    finally:
        stage_spy.restore()
        core.call_llm = original_call
        core.OUTPUT_DIR = original_output
        core.reset_tm_dir_cache()
        _checkpoint.append_event = original_event
        core.translate_batch = original_translate


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #
def _decile_means(rows: List[Dict[str, Any]], section: str, name: str,
                  field: str, deciles: int) -> List[float]:
    """Mean per-batch value of ``rows[i][section][name][field]`` by decile."""
    n = len(rows)
    if not n:
        return []
    means: List[float] = []
    for d in range(deciles):
        lo = (n * d) // deciles
        hi = (n * (d + 1)) // deciles
        chunk = rows[lo:hi]
        if not chunk:
            means.append(0.0)
            continue
        total = 0.0
        for row in chunk:
            entry = (row.get(section) or {}).get(name) or {}
            total += float(entry.get(field) or 0.0)
        means.append(total / len(chunk))
    return means


def _mean_index(rows: int, deciles: int, d: int) -> float:
    lo = (rows * d) // deciles
    hi = (rows * (d + 1)) // deciles
    return (lo + hi - 1) / 2.0


def _exponent(first: float, last: float, idx_first: float, idx_last: float) -> Optional[float]:
    if first <= 0 or last <= 0 or idx_first <= 1 or idx_last <= idx_first:
        return None
    return math.log(last / first) / math.log(idx_last / idx_first)


def _names(rows: List[Dict[str, Any]], section: str) -> List[str]:
    seen: Dict[str, None] = {}
    for row in rows:
        for name in (row.get(section) or {}):
            seen.setdefault(name, None)
    return list(seen)


def analyse(result: Dict[str, Any], *, deciles: int = 10) -> Dict[str, Any]:
    rows = result["rows"]
    out: Dict[str, Any] = {"stages": {}, "phases": {}, "counters": {}}
    idx_first = _mean_index(len(rows), deciles, 0) + 1.0
    idx_last = _mean_index(len(rows), deciles, deciles - 1) + 1.0

    for name in STAGE_ORDER:
        wall = _decile_means(rows, "stages", name, "wall_s", deciles)
        cpu = _decile_means(rows, "stages", name, "cpu_s", deciles)
        out["stages"][name] = {
            "wall_deciles": [round(v * 1000.0, 3) for v in wall],
            "cpu_deciles": [round(v * 1000.0, 3) for v in cpu],
            "wall_exponent": _exponent(wall[0], wall[-1], idx_first, idx_last),
            "cpu_exponent": _exponent(cpu[0], cpu[-1], idx_first, idx_last),
        }

    for name in _names(rows, "phases"):
        wall = _decile_means(rows, "phases", name, "wall_s", deciles)
        cpu = _decile_means(rows, "phases", name, "cpu_s", deciles)
        items = _decile_means(rows, "phases", name, "items", deciles)
        if not any(wall) and not any(items):
            continue
        out["phases"][name] = {
            "wall_deciles": [round(v * 1000.0, 4) for v in wall],
            "cpu_deciles": [round(v * 1000.0, 4) for v in cpu],
            "items_deciles": [round(v, 1) for v in items],
            "wall_exponent": _exponent(wall[0], wall[-1], idx_first, idx_last),
            "cpu_exponent": _exponent(cpu[0], cpu[-1], idx_first, idx_last),
        }

    counters_total: Dict[str, float] = {}
    for row in rows:
        for name, value in (row.get("counters") or {}).items():
            counters_total[name] = counters_total.get(name, 0.0) + float(value)
    out["counters"] = {k: round(v, 4) for k, v in sorted(counters_total.items())}
    return out


def print_report(result: Dict[str, Any], analysis: Dict[str, Any],
                 *, deciles: int = 10) -> None:
    rows = result["rows"]
    print(f"[{result['label']}] segments={result['segments']} "
          f"batches={result['batches']} wall={result['wall_s']}s "
          f"cpu={result['cpu_s']}s pairs={result['pairs']}")

    print("\n  per-batch stage cost by decile (ms/batch)")
    print(f"    {'stage':<28} " + " ".join(f"D{d + 1:>2}" for d in range(deciles))
          + "   k(wall)  k(cpu)")
    for name in STAGE_ORDER:
        info = analysis["stages"][name]
        cells = " ".join(f"{v:>5.2f}" for v in info["wall_deciles"])
        k_wall = info["wall_exponent"]
        k_cpu = info["cpu_exponent"]
        print(f"    {name:<28} {cells}   "
              f"{'  n/a ' if k_wall is None else f'{k_wall:>6.2f}'}   "
              f"{'  n/a ' if k_cpu is None else f'{k_cpu:>6.2f}'}")

    print("\n  per-batch phase cost by decile (ms/batch) -- superlinear only")
    print(f"    {'phase':<34} " + " ".join(f"D{d + 1:>2}" for d in range(deciles))
          + "   k(wall)")
    interesting = []
    for name, info in analysis["phases"].items():
        k = info["wall_exponent"]
        grew = info["wall_deciles"][-1] > info["wall_deciles"][0] * 1.25
        if k is not None and k > 0.15 and grew:
            interesting.append((name, info))
    interesting.sort(key=lambda kv: -kv[1]["wall_deciles"][-1])
    for name, info in interesting:
        cells = " ".join(f"{v:>5.2f}" for v in info["wall_deciles"])
        print(f"    {name:<34} {cells}   {info['wall_exponent']:>6.2f}")
    if not interesting:
        print("    (none: every phase is flat across deciles)")

    print("\n  phase items scanned by decile -- evidence of rescanning")
    print(f"    {'phase':<34} " + " ".join(f"D{d + 1:>2}" for d in range(deciles)))
    for name, info in analysis["phases"].items():
        items = info["items_deciles"]
        if not any(items):
            continue
        if items[-1] <= items[0]:
            continue
        cells = " ".join(f"{v:>5.0f}" for v in items)
        print(f"    {name:<34} {cells}")

    print("\n  call sites attributed to the stage that was open")
    print(f"    {'call site':<48} {'calls':>7} {'wall_s':>9} {'cpu_s':>9} "
          f"{'items':>12}")
    for key, row in sorted(result["spies"].items(),
                           key=lambda kv: -kv[1]["wall_s"]):
        print(f"    {key:<48} {int(row['calls']):>7} "
              f"{row['wall_s']:>9.3f} {row['cpu_s']:>9.3f} "
              f"{int(row.get('items') or 0):>12}")
    if result.get("spies_missing"):
        print("    unresolved: " + ", ".join(result["spies_missing"]))

    total_phases: Dict[str, Dict[str, Any]] = result["phases_total"]
    print("\n  phase totals (whole run)")
    print(f"    {'phase':<34} {'wall_s':>9} {'cpu_s':>9} {'wait_s':>9} "
          f"{'calls':>7} {'items':>12}")
    interesting_totals = [p for p in _cp.ALL_PHASES if p in total_phases]
    for name in interesting_totals:
        p = total_phases[name]
        print(f"    {name:<34} {p['total_wall_s']:>9.3f} {p['total_cpu_s']:>9.3f} "
              f"{p['wait_s']:>9.3f} {p['calls']:>7} {p['items']:>12}")
    if result["counters"]:
        print("  counters: " + ", ".join(
            f"{k}={v:g}" for k, v in sorted(result["counters"].items())))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=int, default=4000)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--deciles", type=int, default=10)
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--reviewer-mode", default="clean")
    parser.add_argument("--glossary-size", type=int, default=0)
    parser.add_argument("--entity-count", type=int, default=0)
    parser.add_argument("--candidate-count", type=int, default=0)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    result = run(
        args.segments, batch_size=args.batch_size,
        enable_review=not args.no_review,
        reviewer_mode=args.reviewer_mode,
        glossary_size=args.glossary_size,
        entity_count=args.entity_count,
        candidate_count=args.candidate_count,
    )
    analysis = analyse(result, deciles=args.deciles)
    print_report(result, analysis, deciles=args.deciles)
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"result": result, "analysis": analysis},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
