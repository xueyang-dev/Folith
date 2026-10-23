"""Merge and compare two benchmark runs.

``python -m benchmarks.performance.compare --baseline a.json b.json \\
    --optimised c.json d.json --out comparison.json``

The comparison is deliberately explicit about *what* is being compared: the
same document, the same window, the same interactions.  A run that finishes
the same work faster is reported as a throughput win, not as a latency win.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List


def _load(paths: List[str]) -> Dict[str, Any]:
    document: Dict[str, Any] = {"scenarios": []}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        document["label"] = data.get("label", document.get("label"))
        document["generated_at"] = data.get("generated_at", document.get("generated_at"))
        document["python"] = data.get("python")
        document["platform"] = data.get("platform")
        for scenario in data.get("scenarios") or []:
            document["scenarios"].append(scenario)
    return document


def _metric(scenario: Dict[str, Any]) -> Dict[str, Any]:
    event_loop = scenario.get("event_loop") or {}
    delay = event_loop.get("delay_ms") or {}
    process = scenario.get("process") or {}
    counters = scenario.get("counters") or {}
    gui = scenario.get("gui_thread_ms") or {}
    refreshes = int(counters.get("workbench.apply") or 0)
    gui_total = float(gui.get("views.apply") or 0.0)
    wall = float(scenario.get("measured_seconds") or 0.0)
    translated = int(process.get("translated") or 0)
    return {
        "wall_seconds": round(wall, 2),
        "cpu_seconds": round(float(process.get("cpu_seconds") or 0.0), 2),
        "cpu_per_second": round(float(process.get("cpu_seconds") or 0.0) / wall, 3) if wall else None,
        "segments_translated": translated,
        "segments_per_second": round(translated / wall, 3) if wall and translated else None,
        "event_loop_delay_p50_ms": delay.get("p50"),
        "event_loop_delay_p95_ms": delay.get("p95"),
        "event_loop_delay_p99_ms": delay.get("p99"),
        "event_loop_delay_max_ms": delay.get("max"),
        "stalls_over_100ms": event_loop.get("stalls_over_100ms"),
        "stalls_over_250ms": event_loop.get("stalls_over_250ms"),
        "interaction_handler_p95_ms": (scenario.get("interaction_handler_ms") or {}).get("p95"),
        "interaction_handler_max_ms": (scenario.get("interaction_handler_ms") or {}).get("max"),
        "gui_refreshes": refreshes,
        "gui_thread_ms_total": round(gui_total, 1),
        "gui_thread_ms_per_refresh": round(gui_total / refreshes, 1) if refreshes else None,
        "gui_thread_ms_other": round(sum(v for k, v in gui.items()
                                          if k != "views.apply"), 1),
        "paints": scenario.get("paints"),
        "peak_rss_mb": process.get("peak_rss_mb"),
        "peak_threads": process.get("peak_threads"),
        "shutdown_seconds": scenario.get("shutdown_seconds"),
        # Normalised per unit of work: the optimised build finishes the same
        # document faster, so the absolute latency has to be read together with
        # the amount of work that latency bought.
        "gui_thread_ms_per_translated_segment": (
            round(gui_total / translated, 3) if translated else None),
        "stalls_per_100_segments": (
            round(float(event_loop.get("stalls_over_100ms") or 0) * 100.0 / translated, 2)
            if translated else None),
        "cpu_seconds_per_translated_segment": (
            round(float(process.get("cpu_seconds") or 0.0) / translated, 4)
            if translated else None),
    }


def _delta(before: float | None, after: float | None) -> float | None:
    if before in (None, 0) or after is None:
        return None
    return round((after - before) / before * 100.0, 1)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", nargs="+", required=True)
    parser.add_argument("--optimised", nargs="+", required=True)
    parser.add_argument("--out", default="benchmarks/performance/comparison.json")
    args = parser.parse_args(argv)

    before = _load(args.baseline)
    after = _load(args.optimised)
    before_by_name = {s["scenario"]: s for s in before["scenarios"]}
    after_by_name = {s["scenario"]: s for s in after["scenarios"]}

    scenarios = []
    for name in before_by_name:
        if name not in after_by_name:
            continue
        b = _metric(before_by_name[name])
        a = _metric(after_by_name[name])
        changes = {}
        for key in b:
            changes[key] = {"before": b[key], "after": a[key],
                            "change_percent": _delta(b[key], a[key])}
        scenarios.append({"scenario": name,
                           "background": before_by_name[name].get("background"),
                           "segments": before_by_name[name].get("segments"),
                           "metrics": changes})

    document = {
        "generated_at": after.get("generated_at"),
        "python": after.get("python"),
        "platform": after.get("platform"),
        "note": ("Both runs execute the same product code against the same "
                 "document and the same interaction script; the 'before' run "
                 "is the C1-C6 pre-optimisation reconstruction "
                 "(benchmarks/performance/revert_to_baseline.py, 27/27 edits)."),
        "scenarios": scenarios,
    }
    directory = os.path.dirname(os.path.abspath(args.out))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"wrote {args.out}", flush=True)
    for entry in scenarios:
        m = entry["metrics"]
        print(f"{entry['scenario']}: "
              f"wall {m['wall_seconds']['before']}s -> {m['wall_seconds']['after']}s | "
              f"cpu {m['cpu_seconds']['before']}s -> {m['cpu_seconds']['after']}s | "
              f"delay p95 {m['event_loop_delay_p95_ms']['before']} -> "
              f"{m['event_loop_delay_p95_ms']['after']} ms | "
              f"gui/refresh {m['gui_thread_ms_per_refresh']['before']} -> "
              f"{m['gui_thread_ms_per_refresh']['after']} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
