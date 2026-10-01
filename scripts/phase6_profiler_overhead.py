#!/usr/bin/env python
"""Instrumentation overhead -- Performance Phase 6 (§9 / §10, read-only).

Why this script exists
----------------------
Every number in the Phase 6 critical-path table was produced by the very
instrumentation being audited.  Before those numbers can be trusted, the
instrumentation has to pay for itself: the *disabled* path (what production
runs) must be essentially free, and the *enabled* path (what the benchmark
runs) must stay small enough not to reshape the profile it reports.

What it measures
----------------
1. **Micro:** raw cost of one ``with critical_path.phase(...)`` entry, enabled
   versus disabled, over a fixed loop.  This is the per-phase tax, independent
   of anything the pipeline does.
2. **Macro:** one real ``translate_stage`` run with the profiler off and on, in
   a *symmetric alternating order* (``off on on off``) inside a single process,
   as §10 requires -- "yesterday's baseline versus today's optimised build" is
   explicitly not acceptable.  Both ``perf_counter`` wall and ``process_time``
   CPU are recorded, because a sandbox can inflate wall in ways CPU does not.

Artifact writes are counted too, so "the profiler is cheap" is not asserted
while it quietly emits a file per batch.

Usage
-----
    venv/bin/python scripts/phase6_profiler_overhead.py --segments 1500
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from folith import critical_path as _cp  # noqa: E402
import translation_critical_path_audit as cpa  # noqa: E402

MICRO_ITERATIONS = 200_000


def micro_benchmark() -> Dict[str, Any]:
    """Raw per-phase entry cost, enabled versus disabled."""
    out: Dict[str, Any] = {"iterations": MICRO_ITERATIONS}
    for label, enabled in (("disabled", False), ("enabled", True)):
        if enabled:
            _cp.enable()
        else:
            _cp.disable()
        started, cpu_started = time.perf_counter(), time.process_time()
        for _ in range(MICRO_ITERATIONS):
            with _cp.phase("reviewer_transport"):
                pass
        wall = time.perf_counter() - started
        cpu = time.process_time() - cpu_started
        _cp.reset()
        out[label] = {
            "wall_s": round(wall, 6),
            "cpu_s": round(cpu, 6),
            "ns_per_call": round(wall / MICRO_ITERATIONS * 1e9, 2),
        }
    _cp.disable()
    return out


def _tree_stats(root: str) -> Dict[str, int]:
    files = [p for p in Path(root).rglob("*") if p.is_file()]
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }


def macro_benchmark(segments: int, order: Sequence[bool]) -> Dict[str, Any]:
    """Alternate profiler off/on inside one process and diff the runs."""
    config = cpa.RunConfig(segments, section_count=max(1, segments // 25),
                           label=f"overhead_n{segments}")
    rows: List[Dict[str, Any]] = []
    for profile in order:
        row = cpa.run_case(config, profile=profile)
        tree = _tree_stats(row["tmp_dir"])
        rows.append({
            "profile": profile,
            "wall_s": row["wall_s"],
            "cpu_s": row["cpu_s"],
            "batches": row["batches"],
            "files": tree["files"],
            "bytes": tree["bytes"],
        })
    return {"segments": segments, "order": list(order), "runs": rows}


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summarise(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    off = [r for r in runs if not r["profile"]]
    on = [r for r in runs if r["profile"]]
    summary: Dict[str, Any] = {"off": len(off), "on": len(on)}
    for field in ("wall_s", "cpu_s"):
        off_mean = _mean([r[field] for r in off])
        on_mean = _mean([r[field] for r in on])
        summary[f"{field}_off"] = round(off_mean, 4)
        summary[f"{field}_on"] = round(on_mean, 4)
        summary[f"{field}_delta"] = round(on_mean - off_mean, 4)
        summary[f"{field}_pct"] = (
            round((on_mean - off_mean) / off_mean * 100.0, 3) if off_mean else None)
    summary["files_off"] = int(_mean([r["files"] for r in off]))
    summary["files_on"] = int(_mean([r["files"] for r in on]))
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segments", type=int, default=1500)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    micro = micro_benchmark()
    print("=== micro: one phase() entry ===")
    print(f"  iterations: {micro['iterations']}")
    for label in ("disabled", "enabled"):
        row = micro[label]
        print(f"  {label:<9} wall={row['wall_s']:.4f}s cpu={row['cpu_s']:.4f}s "
              f"({row['ns_per_call']:.0f} ns/call)")

    print(f"\n=== macro: real translate_stage, symmetric order "
          f"[off on on off] ===")
    macro = macro_benchmark(args.segments, (False, True, True, False))
    print(f"  {'#':>2} {'profiler':>9} {'wall_s':>9} {'cpu_s':>9} "
          f"{'batches':>8} {'files':>6} {'bytes':>10}")
    for index, row in enumerate(macro["runs"]):
        print(f"  {index:>2} {'on' if row['profile'] else 'off':>9} "
              f"{row['wall_s']:>9.4f} {row['cpu_s']:>9.4f} "
              f"{row['batches']:>8} {row['files']:>6} {row['bytes']:>10}")

    summary = _summarise(macro["runs"])
    print("\n  summary (mean of the alternating runs)")
    print(f"    wall: off={summary['wall_s_off']:.4f}s on={summary['wall_s_on']:.4f}s "
          f"delta={summary['wall_s_delta']:+.4f}s ({summary['wall_s_pct']}%)")
    print(f"    cpu : off={summary['cpu_s_off']:.4f}s on={summary['cpu_s_on']:.4f}s "
          f"delta={summary['cpu_s_delta']:+.4f}s ({summary['cpu_s_pct']}%)")
    print(f"    artifacts: off={summary['files_off']} files "
          f"on={summary['files_on']} files")

    payload = {"micro": micro, "macro": macro, "summary": summary}
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
