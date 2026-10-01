#!/usr/bin/env python3
"""Prove runtime telemetry no longer scales linearly with LLM request count.

Before the coalescer, every in-flight LLM status change wrote the whole
``runtime_state.json`` (plus an event append and an fsync).  The cost therefore
grew 1:1 with model calls.  This benchmark drives the real coalescer at three
request volumes in two modes:

    uncoalesced  flush_interval = 0.0   -> every submit hits the disk (old shape)
    coalesced    flush_interval = 0.35  -> leading edge + trailing edge merge

and reports full-state writes, event appends, fsyncs and wall time.  The
coalescer's own counters (submits / immediate / coalesced) are included so the
ratio is auditable rather than asserted.

Usage:
    python scripts/runtime_telemetry_benchmark.py --out tmp/runtime-telemetry-benchmark
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402


def _measure(job_id, requests, *, flush_interval, workers=1):
    coalescer = core._RuntimeTelemetryCoalescer(
        job_id, flush_interval=flush_interval)
    core.reset_runtime_persistence_stats()
    before = core.runtime_persistence_stats()
    per_worker = max(1, requests // workers)

    def worker():
        for _ in range(per_worker):
            coalescer.request_started()
            coalescer.request_finished()

    started = time.perf_counter()
    pool = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    coalescer.close()
    wall = time.perf_counter() - started
    after = core.runtime_persistence_stats()
    delta = {key: after[key] - before[key] for key in before}
    stats = coalescer.stats()
    return {
        "requests": per_worker * workers,
        "workers": workers,
        "submits": stats["submits"],
        "immediate": stats["immediate"],
        "coalesced": stats["coalesced"],
        "flushes": stats["flushes"],
        "state_writes": delta["state_writes"],
        "event_appends": delta["event_appends"],
        "fsyncs": delta["fsyncs"],
        "state_write_seconds": round(delta["state_write_seconds"], 4),
        "wall_time_s": round(wall, 4),
        "writes_per_1k_requests": round(
            delta["state_writes"] / max(1, per_worker * workers) * 1000.0, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("tmp/runtime-telemetry-benchmark"))
    parser.add_argument("--volumes", default="50,200,800")
    parser.add_argument("--workers", default="1,8",
                        help="worker counts to run per volume")
    args = parser.parse_args()

    volumes = [int(v) for v in args.volumes.split(",") if v.strip()]
    worker_counts = [int(v) for v in args.workers.split(",") if v.strip()]
    workdir = Path(tempfile.mkdtemp(prefix="telemetry-bench-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = workdir
    rows = []
    try:
        job_id = "tlm000000000000001"
        core.save_job_state(job_id, core.new_job_state("telemetry-bench.docx"))
        for requests in volumes:
            for workers in worker_counts:
                for mode, interval in (("uncoalesced", 0.0), ("coalesced", 0.35)):
                    row = _measure(job_id, requests, flush_interval=interval,
                                   workers=workers)
                    row["mode"] = mode
                    rows.append(row)
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(workdir, ignore_errors=True)

    payload = {
        "volumes": volumes,
        "workers": worker_counts,
        "flush_interval_s": 0.35,
        "rows": rows,
    }
    by_key = {}
    for row in rows:
        by_key.setdefault((row["requests"], row["workers"]), {})[row["mode"]] = row
    payload["write_growth"] = {
        f"{requests}req/{workers}w": {
            "uncoalesced_state_writes": pair["uncoalesced"]["state_writes"],
            "coalesced_state_writes": pair["coalesced"]["state_writes"],
            "reduction_pct": round(100.0 * (
                1 - pair["coalesced"]["state_writes"]
                / max(1, pair["uncoalesced"]["state_writes"])), 1),
            "uncoalesced_fsyncs": pair["uncoalesced"]["fsyncs"],
            "coalesced_fsyncs": pair["coalesced"]["fsyncs"],
        }
        for (requests, workers), pair in sorted(by_key.items())
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'requests':>9} {'workers':>8} {'mode':>12} {'submits':>8} "
          f"{'flushes':>8} {'state_wr':>9} {'fsync':>7} {'wall_s':>8}")
    for row in rows:
        print(f"{row['requests']:>9} {row['workers']:>8} {row['mode']:>12} "
              f"{row['submits']:>8} {row['flushes']:>8} "
              f"{row['state_writes']:>9} {row['fsyncs']:>7} "
              f"{row['wall_time_s']:>8.3f}")
    print()
    for key, item in payload["write_growth"].items():
        print(f"  {key:>14}: state writes "
              f"{item['uncoalesced_state_writes']} -> {item['coalesced_state_writes']} "
              f"({item['reduction_pct']}% fewer); fsyncs "
              f"{item['uncoalesced_fsyncs']} -> {item['coalesced_fsyncs']}")
    print(f"artifact: {args.out / 'benchmark.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
