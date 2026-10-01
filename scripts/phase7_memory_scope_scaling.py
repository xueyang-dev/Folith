"""Phase 7 (section 7): quantify the review Project-Memory scope cost in G and S.

What is being measured
----------------------
``folith.translation_evidence._scoped_project_memory`` is called once per
``build_runtime_review_packet`` (i.e. once per review batch).  Phase 6 could only
report it folded into ``review_input_packet_build``; this probe isolates it.

Two independent size axes:

``G``
    glossary / Project-Memory entries.  The fixture's glossary is *all locked*
    and carries no ``occurrences``, so the ``and`` in the comprehension never
    short-circuits and **every** entry reaches the ``term_matches`` scan.  That
    is the worst case, measured rather than assumed.
``S``
    sources in the current batch, i.e. ``batch_size``.  ``S`` is *not* the
    document length: the scan is per batch, so a run's *cumulative* cost is
    ``(n / S) x cost(G, S)``, which is ``O(n x G)`` -- see ``--extrapolate``.

The probe also splits the function into its instrumented sub-phases
(``normalize`` / ``hash`` / ``scan`` / ``project``) and measures the *second*
independent ``O(G x S)`` scan, which lives inside
``translation_core.build_review_packet``.

Timing discipline
-----------------
Every cell is reported as the **minimum** of ``--repeat`` runs.  The minimum is
the right statistic here: the sandbox inflates ``wall`` by up to 60x under load
(see the Phase 6 report), while the minimum converges to the un-contended cost.
CPU is reported alongside so a genuine CPU effect is never confused with noise.

Usage
-----
    venv/bin/python scripts/phase7_memory_scope_scaling.py
    venv/bin/python scripts/phase7_memory_scope_scaling.py --json tmp/phase7/memory_scope.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from folith import critical_path as _cp  # noqa: E402
from folith import models, terminology, translation_core  # noqa: E402
from folith import translation_evidence as _evidence  # noqa: E402

import translation_setup_audit as audit  # noqa: E402

DEFAULT_GLOSSARY_SIZES = (100, 1000, 10000)
DEFAULT_SOURCE_SIZES = (4, 16, 64, 256)


#: Deterministic work counters.  Wall and CPU drift by more than the effect
#: under study in this sandbox, so a comparison also needs counts that cannot
#: drift: how many times the glossary was normalised / hashed, and how many
#: term checks the ``O(G x S)`` scan actually ran.
_COUNTED = (
    (models, "normalize_glossary", "normalize_glossary_calls"),
    (models, "glossary_hash", "glossary_hash_calls"),
    (terminology, "term_matches", "term_matches_calls"),
)


def _install_counters(counts: Dict[str, int]) -> List[Tuple[Any, str, Any]]:
    saved: List[Tuple[Any, str, Any]] = []
    for module, name, key in _COUNTED:
        original = getattr(module, name)

        def counting(*args, _original=original, _key=key, **kwargs):
            counts[_key] = counts.get(_key, 0) + 1
            return _original(*args, **kwargs)

        setattr(module, name, counting)
        saved.append((module, name, original))
    return saved


def _restore_counters(saved: Sequence[Tuple[Any, str, Any]]) -> None:
    for module, name, original in saved:
        setattr(module, name, original)


def _work_counters(fn: Callable[[], Any]) -> Dict[str, int]:
    """Count the derivations one call actually performs (no timing)."""
    counts: Dict[str, int] = {}
    saved = _install_counters(counts)
    try:
        fn()
    finally:
        _restore_counters(saved)
    return counts


def _min_time(fn: Callable[[], Any], repeat: int) -> Tuple[float, float]:
    """Return (min wall, min CPU) over ``repeat`` calls of ``fn``."""
    walls: List[float] = []
    cpus: List[float] = []
    for _ in range(max(1, repeat)):
        started, cpu_started = time.perf_counter(), time.process_time()
        fn()
        walls.append(time.perf_counter() - started)
        cpus.append(time.process_time() - cpu_started)
    return min(walls), min(cpus)


def _sources_for(size: int) -> List[str]:
    """Batch sources: real fixture prose, so the regex path is exercised."""
    paragraphs = audit.make_paragraphs(max(1, size))
    return [str(value) for value in paragraphs[:size]]


def _instrumented(phase_names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    snapshot = _cp.snapshot()["phases"]
    return {name: snapshot[name] for name in phase_names if name in snapshot}


def _packet_state(sources: Sequence[str], glossary: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "pairs": [{"segment_id": index,
                   "source": sources[index % len(sources)],
                   "target": f"译文{index}"} for index in range(len(sources))],
        "glossary": glossary,
        "glossary_frozen": {"entries": glossary},
    }


def _legacy_batch(state: Dict[str, Any], glossary: Sequence[Dict[str, Any]],
                  sources: Sequence[str], ids: Sequence[int],
                  packet_state: Dict[str, Any]) -> Any:
    """Pre-Phase-7 shape: the glossary is normalised/hashed/scanned twice more."""
    memory = _evidence._scoped_project_memory(state, glossary, sources, ids)
    return translation_core.build_review_packet(
        packet_state, segment_ids=ids, project_memory=memory)


def _current_batch(state: Dict[str, Any], glossary: Sequence[Dict[str, Any]],
                   sources: Sequence[str], ids: Sequence[int],
                   packet_state: Dict[str, Any]) -> Any:
    """Phase 7 shape: derive the scope once and hand the views to the packet."""
    scope = _evidence._review_glossary_scope(state, glossary, sources, ids)
    return translation_core.build_review_packet(
        packet_state, segment_ids=ids, project_memory=scope.memory,
        glossary_entries=scope.entries, glossary_hash=scope.glossary_hash,
        relevant_glossary=scope.relevant_glossary)


def measure_cell(glossary_size: int, source_size: int, repeat: int) -> Dict[str, Any]:
    glossary = audit.make_glossary(glossary_size)
    sources = _sources_for(source_size)
    ids = list(range(source_size))
    state: Dict[str, Any] = {"confirmed_style_rules": []}

    entries = models.normalize_glossary(glossary)

    norm_wall, norm_cpu = _min_time(
        lambda: models.normalize_glossary(glossary), repeat)
    hash_wall, hash_cpu = _min_time(
        lambda: models.glossary_hash(entries), repeat)
    scan_fn = lambda: _evidence._scoped_project_memory(  # noqa: E731
        state, glossary, sources, ids)
    scope_wall, scope_cpu = _min_time(scan_fn, repeat)
    # ``sys.getallocatedblocks`` is not usable here: every temporary is freed
    # and the freelists are reused, so the net delta is ~0 regardless of how
    # much was allocated.  Derivation counts are the honest substitute.
    scope_work = _work_counters(scan_fn)

    # Instrumented split of one scope call.
    _cp.reset()
    _cp.enable()
    memory = _evidence._scoped_project_memory(state, glossary, sources, ids)
    scope_phases = _instrumented((
        "review_input_memory_normalize",
        "review_input_memory_hash",
        "review_input_memory_scan",
        "review_input_memory_project",
    ))
    _cp.disable()

    packet_state = _packet_state(sources, glossary)
    packet_fn = lambda: translation_core.build_review_packet(  # noqa: E731
        packet_state, segment_ids=ids, project_memory=memory)
    packet_wall, packet_cpu = _min_time(packet_fn, repeat)
    packet_work = _work_counters(packet_fn)

    # The two whole-batch shapes.  Before/after is measured *inside one process*
    # on identical inputs, which is the only honest way to compare them: the
    # sandbox makes absolute numbers drift by more than the effect under study.
    legacy_fn = lambda: _legacy_batch(  # noqa: E731
        state, glossary, sources, ids, packet_state)
    current_fn = lambda: _current_batch(  # noqa: E731
        state, glossary, sources, ids, packet_state)
    legacy_wall, legacy_cpu = _min_time(legacy_fn, repeat)
    legacy_work = _work_counters(legacy_fn)
    current_wall, current_cpu = _min_time(current_fn, repeat)
    current_work = _work_counters(current_fn)
    for work in (legacy_work, current_work):
        work["entries_normalized"] = (
            work.get("normalize_glossary_calls", 0) * glossary_size)
        work["entries_hashed"] = work.get("glossary_hash_calls", 0) * glossary_size

    _cp.reset()
    _cp.enable()
    translation_core.build_review_packet(
        packet_state, segment_ids=ids, project_memory=memory)
    packet_phases = _instrumented((
        "review_input_truth",
        "review_input_glossary_resolve",
        "review_input_relevant_glossary",
        "review_input_memory_deepcopy",
        "review_input_fingerprint",
    ))
    _cp.disable()

    return {
        "glossary_size": glossary_size,
        "source_size": source_size,
        "term_checks_predicted": glossary_size * source_size,
        "normalize_glossary": {"wall_s": norm_wall, "cpu_s": norm_cpu},
        "glossary_hash": {"wall_s": hash_wall, "cpu_s": hash_cpu},
        "scoped_project_memory": {
            "wall_s": scope_wall, "cpu_s": scope_cpu,
            "work": scope_work,
            "phases": scope_phases,
        },
        "build_review_packet": {
            "wall_s": packet_wall, "cpu_s": packet_cpu,
            "work": packet_work,
            "phases": packet_phases,
        },
        "batch_legacy": {
            "wall_s": legacy_wall, "cpu_s": legacy_cpu, "work": legacy_work,
        },
        "batch_current": {
            "wall_s": current_wall, "cpu_s": current_cpu, "work": current_work,
        },
    }


def _fmt_seconds(value: float) -> str:
    return f"{value * 1000.0:.3f} ms"


def print_matrix(rows: Sequence[Dict[str, Any]]) -> None:
    print("\n=== whole review batch: pre-Phase-7 vs Phase 7 (min of N runs, same process) ===")
    print(f"  {'G':>6} {'S':>5} {'G*S':>9} | {'legacy cpu':>11} | {'phase7 cpu':>11} "
          f"| {'speedup':>8} | {'norm':>7} {'hash':>7} {'term checks':>12}")
    for row in rows:
        legacy, current = row["batch_legacy"], row["batch_current"]
        speedup = legacy["cpu_s"] / current["cpu_s"] if current["cpu_s"] else float("inf")
        lw, cw = legacy["work"], current["work"]
        norm = f"{lw.get('normalize_glossary_calls', 0)}->{cw.get('normalize_glossary_calls', 0)}"
        hashed = f"{lw.get('glossary_hash_calls', 0)}->{cw.get('glossary_hash_calls', 0)}"
        terms = (f"{lw.get('term_matches_calls', 0)}"
                 f"->{cw.get('term_matches_calls', 0)}")
        print(f"  {row['glossary_size']:>6} {row['source_size']:>5} "
              f"{row['term_checks_predicted']:>9} | "
              f"{_fmt_seconds(legacy['cpu_s']):>11} | "
              f"{_fmt_seconds(current['cpu_s']):>11} | "
              f"{speedup:>7.2f}x | {norm:>7} {hashed:>7} {terms:>12}")

    print("\n=== per-call cost: _scoped_project_memory (min of N runs) ===")
    print(f"  {'G':>6} {'S':>5} {'G*S':>10} {'wall':>12} {'cpu':>12} "
          f"{'norm':>6} {'hash':>6} {'terms':>10} {'scan items':>12}")
    for row in rows:
        scope = row["scoped_project_memory"]
        scan = scope["phases"].get("review_input_memory_scan") or {}
        work = scope["work"]
        print(f"  {row['glossary_size']:>6} {row['source_size']:>5} "
              f"{row['term_checks_predicted']:>10} "
              f"{_fmt_seconds(scope['wall_s']):>12} "
              f"{_fmt_seconds(scope['cpu_s']):>12} "
              f"{work.get('normalize_glossary_calls', 0):>6} "
              f"{work.get('glossary_hash_calls', 0):>6} "
              f"{work.get('term_matches_calls', 0):>10} "
              f"{scan.get('items', 0):>12}")

    print("\n=== _scoped_project_memory sub-phases (one call, ms) ===")
    print(f"  {'G':>6} {'S':>5} {'normalize':>11} {'hash':>11} "
          f"{'scan':>11} {'project':>11} {'sum':>11}")
    for row in rows:
        phases = row["scoped_project_memory"]["phases"]

        def cpu(name: str) -> float:
            return float((phases.get(name) or {}).get("total_cpu_s", 0.0)) * 1000.0

        total = sum(cpu(name) for name in (
            "review_input_memory_normalize", "review_input_memory_hash",
            "review_input_memory_scan", "review_input_memory_project"))
        print(f"  {row['glossary_size']:>6} {row['source_size']:>5} "
              f"{cpu('review_input_memory_normalize'):>11.3f} "
              f"{cpu('review_input_memory_hash'):>11.3f} "
              f"{cpu('review_input_memory_scan'):>11.3f} "
              f"{cpu('review_input_memory_project'):>11.3f} {total:>11.3f}")

    print("\n=== build_review_packet sub-phases (one call, ms) ===")
    print(f"  {'G':>6} {'S':>5} {'glossary_resolve':>17} {'relevant_glossary':>18} "
          f"{'deepcopy':>10} {'fingerprint':>12}")
    for row in rows:
        phases = row["build_review_packet"]["phases"]

        def cpu(name: str) -> float:
            return float((phases.get(name) or {}).get("total_cpu_s", 0.0)) * 1000.0

        print(f"  {row['glossary_size']:>6} {row['source_size']:>5} "
              f"{cpu('review_input_glossary_resolve'):>17.3f} "
              f"{cpu('review_input_relevant_glossary'):>18.3f} "
              f"{cpu('review_input_memory_deepcopy'):>10.3f} "
              f"{cpu('review_input_fingerprint'):>12.3f}")

    print("\n=== linearity in G (fixed S = max) and in S (fixed G = max) ===")
    by_g = {row["glossary_size"]: row for row in rows
            if row["source_size"] == max(r["source_size"] for r in rows)}
    by_s = {row["source_size"]: row for row in rows
            if row["glossary_size"] == max(r["glossary_size"] for r in rows)}
    print("  G sweep (S fixed):")
    for g in sorted(by_g):
        scope = by_g[g]["scoped_project_memory"]
        print(f"    G={g:>6}  cpu={_fmt_seconds(scope['cpu_s'])}  "
              f"per-entry={scope['cpu_s'] / max(1, g) * 1e9:.1f} ns")
    print("  S sweep (G fixed):")
    for s in sorted(by_s):
        scope = by_s[s]["scoped_project_memory"]
        print(f"    S={s:>6}  cpu={_fmt_seconds(scope['cpu_s'])}  "
              f"per-source={scope['cpu_s'] / max(1, s) * 1e9:.1f} ns")


def extrapolate(rows: Sequence[Dict[str, Any]], segments: int,
                batch_size: int) -> None:
    """Cumulative scope cost for a run of ``segments`` at ``batch_size``."""
    print(f"\n=== cumulative projection (n={segments}, batch={batch_size}) ===")
    print(f"  {'G':>6} {'per-call cpu':>13} {'calls':>8} {'cumulative cpu':>16}")
    batches = segments // max(1, batch_size)
    for row in rows:
        if row["source_size"] != batch_size:
            continue
        per_call = row["scoped_project_memory"]["cpu_s"]
        print(f"  {row['glossary_size']:>6} {_fmt_seconds(per_call):>13} "
              f"{batches:>8} {_fmt_seconds(per_call * batches):>16}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glossary-sizes", default=",".join(
        str(v) for v in DEFAULT_GLOSSARY_SIZES))
    parser.add_argument("--source-sizes", default=",".join(
        str(v) for v in DEFAULT_SOURCE_SIZES))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--extrapolate", type=int, default=0,
                        help="project the cumulative cost for this n")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    glossary_sizes = tuple(int(x) for x in args.glossary_sizes.split(",") if x.strip())
    source_sizes = tuple(int(x) for x in args.source_sizes.split(",") if x.strip())

    rows: List[Dict[str, Any]] = []
    for g in glossary_sizes:
        for s in source_sizes:
            row = measure_cell(g, s, max(1, args.repeat))
            rows.append(row)
            print(f"measured G={g} S={s} "
                  f"(scope cpu={_fmt_seconds(row['scoped_project_memory']['cpu_s'])})",
                  flush=True)

    print_matrix(rows)
    if args.extrapolate:
        extrapolate(rows, args.extrapolate, args.batch_size)

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"repeat": args.repeat, "rows": rows},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
