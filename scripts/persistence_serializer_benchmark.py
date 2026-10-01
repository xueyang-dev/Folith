#!/usr/bin/env python3
"""Compare JSON serializers for the authoritative state snapshot.

The persistence brief allows changing the serializer only when the benchmark is
clearly faster, packaging risk is unchanged, Unicode behaviour is correct and
the deterministic-artifact contract still holds.  This script produces exactly
that evidence and nothing else.

What is compared
----------------
``pretty``
    The status-quo writer: ``json.dumps(state, ensure_ascii=False, indent=2)``.
``compact``
    ``json.dumps(state, ensure_ascii=False, separators=(",", ":"))`` -- the same
    stdlib encoder with whitespace removed, so no new dependency and no change
    to keys, values, ordering or Unicode escaping.
``orjson``
    Only when the package happens to be importable.  Reported for completeness;
    a third-party C extension is not adopted for serialization speed alone.

Correctness gate
----------------
Every candidate must round-trip to an object that compares equal to the
original *after* ``json.loads``, and must decode the exact same non-ASCII text.
Byte size and wall time are reported per size so the trade-off is visible.

Run:

    python3 scripts/persistence_serializer_benchmark.py --sizes 100,1000,5000
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402


def _build_state(count: int) -> dict:
    """A state shaped like a real mid-translation snapshot.

    Deliberately includes CJK text, escapes, newlines and nested findings so a
    serializer cannot win by mishandling Unicode or control characters.
    """
    state = core.new_job_state("serializer-bench.txt")
    paras = [
        f"第 {index} 段记录了手稿在同年冬季之前经由北方各省的档案南运，"
        f"其中包含引号 \"quoted\"、反斜杠 \\\\ 与换行\n以及制表符\t。"
        for index in range(count)
    ]
    state["paras"] = paras
    state["pairs"] = [
        {
            "source": paras[index],
            "initial_target": f"译文初稿 {index}：档案南运记录。",
            "target": f"译文终稿 {index}：档案在冬季前南运。",
            "accepted_target": f"译文终稿 {index}：档案在冬季前南运。",
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "reviewed",
            "from_tm": False,
        }
        for index in range(count)
    ]
    state["findings"] = [
        {
            "finding_id": f"f-{index}",
            "segment_id": index,
            "severity": "minor",
            "status": "open",
            "reason": f"第 {index} 段术语与冻结术语表存在轻微不一致。",
        }
        for index in range(count)
    ]
    state["review_evidence"] = [
        {
            "review_event_id": f"ev-{index}",
            "segment_ids": [index],
            "freshness_status": "current",
            "translation_core": {"glossary_hash": "deadbeef", "dependency_truth": []},
        }
        for index in range(count)
    ]
    state["glossary_frozen"] = {
        "glossary_hash": "deadbeef",
        "entries": [
            {"source": f"术语 {i}", "target": f"Term {i}"} for i in range(40)
        ],
    }
    return state


def _candidates() -> dict:
    options = {
        "pretty": lambda value: json.dumps(value, ensure_ascii=False, indent=2),
        "compact": lambda value: json.dumps(value, ensure_ascii=False,
                                            separators=(",", ":")),
    }
    try:  # pragma: no cover - optional dependency, reported only when present
        import orjson  # type: ignore

        options["orjson"] = lambda value: orjson.dumps(
            value, option=orjson.OPT_NON_STR_KEYS).decode("utf-8")
    except Exception:
        pass
    return options


def _time_serialize(fn, state: dict, repeats: int) -> list:
    samples = []
    for _ in range(repeats):
        begin = time.perf_counter()
        fn(state)
        samples.append(time.perf_counter() - begin)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="100,1000,5000")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--out", default="tmp/phase3/serializer-benchmark.json")
    args = parser.parse_args()

    sizes = [int(value) for value in str(args.sizes).split(",") if value.strip()]
    candidates = _candidates()
    report = {"sizes": sizes, "repeats": args.repeats, "results": []}

    for size in sizes:
        state = _build_state(size)
        reference = json.loads(json.dumps(state, ensure_ascii=False))
        row = {"segments": size, "candidates": {}}
        baseline_time = None
        for name, fn in candidates.items():
            payload = fn(state)
            # Correctness gate: identical decoded object and identical Unicode.
            decoded_ok = json.loads(payload) == reference
            unicode_ok = "第 0 段" in payload and "\\\\" in payload
            samples = _time_serialize(fn, state, args.repeats)
            median = statistics.median(samples)
            if name == "pretty":
                baseline_time = median
            row["candidates"][name] = {
                "median_ms": round(median * 1000, 4),
                "min_ms": round(min(samples) * 1000, 4),
                "bytes": len(payload.encode("utf-8")),
                "decoded_equal": bool(decoded_ok),
                "unicode_preserved": bool(unicode_ok),
            }
        for name, record in row["candidates"].items():
            if baseline_time and name != "pretty":
                record["speedup_vs_pretty"] = round(
                    baseline_time / (record["median_ms"] / 1000), 3)
                record["bytes_reduction_pct"] = round(
                    100.0 * (1 - record["bytes"] /
                             row["candidates"]["pretty"]["bytes"]), 2)
        report["results"].append(row)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    for row in report["results"]:
        print(f"\n--- {row['segments']} segments ---")
        header = (f"{'serializer':>10} {'median_ms':>10} {'bytes':>10} "
                  f"{'speedup':>8} {'bytes_delta%':>13} {'equal':>6} {'uni':>5}")
        print(header)
        print("-" * len(header))
        for name, record in row["candidates"].items():
            print(f"{name:>10} {record['median_ms']:>10.3f} {record['bytes']:>10} "
                  f"{record.get('speedup_vs_pretty', 1.0):>8.3f} "
                  f"{record.get('bytes_reduction_pct', 0.0):>13.2f} "
                  f"{str(record['decoded_equal']):>6} "
                  f"{str(record['unicode_preserved']):>5}")
    if "orjson" not in candidates:
        print("\norjson not installed: not evaluated (no dependency will be "
              "added for serialization speed alone).")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
