"""Justify the reversal thresholds from the benchmark, not from taste.

Samples each benchmark document once, then re-judges it under a grid of
(minimum mirrored hits, minimum ratio) pairs and reports how many pages are
accepted or rejected wrongly.  The shipped pair must lie inside a plateau of
correct decisions, with margin on both sides.

Usage::

    ./venv/bin/python benchmark/routing/sweep.py
    ./venv/bin/python benchmark/routing/sweep.py --json out/sweep.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "samples.json"

MIN_HITS_GRID = (1, 2, 3, 4, 6, 8, 12, 16, 25, 40)
MIN_RATIO_GRID = (1.0, 1.5, 2.0, 2.5, 4.0, 8.0, 16.0)


def _load_samples() -> list[dict]:
    from folith import text_layer_quality as quality

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    prepared = []
    for group, entries in manifest["groups"].items():
        for entry in entries:
            path = HERE / entry["file"]
            if not path.is_file():
                raise SystemExit(
                    f"missing {entry['file']}; run prepare_samples.py first")
            prepared.append({
                "id": entry["id"],
                "group": group,
                "expect": entry["expect"],
                "pages": quality.sample_pdf_text_pages(path.read_bytes(), max_pages=3),
            })
    return prepared


def _judge(quality, prepared) -> tuple[list, list]:
    false_accept, false_reject = [], []
    for sample in prepared:
        report = quality.assess_text_layer_sample(
            sample["pages"], source_language=None)
        rejected = report["status"] == "rejected"
        if sample["expect"] == "rejected" and not rejected:
            false_accept.append(sample["id"])
        elif sample["expect"] != "rejected" and rejected:
            false_reject.append(sample["id"])
    return sorted(false_accept), sorted(false_reject)


def sweep(prepared: list[dict] | None = None) -> dict:
    """Grid the dictionary rule twice: with and without the visual signal.

    The first grid is the shipped behaviour (either signal can reject).  The
    second switches the visual signal off, which isolates how much separation
    the dictionary rule carries on its own.
    """
    from folith import text_layer_quality as quality

    prepared = prepared if prepared is not None else _load_samples()
    original = (quality.REVERSAL_MIN_HITS, quality.REVERSAL_MIN_RATIO,
                quality.VISUAL_ORDER_MIN_RATIO)
    grids = {}
    try:
        for label, visual_ratio in (("shipped", original[2]), ("dictionary_only", 2.0)):
            quality.VISUAL_ORDER_MIN_RATIO = visual_ratio
            rows = []
            for min_hits in MIN_HITS_GRID:
                for min_ratio in MIN_RATIO_GRID:
                    quality.REVERSAL_MIN_HITS = min_hits
                    quality.REVERSAL_MIN_RATIO = min_ratio
                    false_accept, false_reject = _judge(quality, prepared)
                    rows.append({
                        "min_hits": min_hits, "min_ratio": min_ratio,
                        "false_accept": false_accept,
                        "false_reject": false_reject,
                        "correct": not false_accept and not false_reject,
                    })
            grids[label] = rows
    finally:
        (quality.REVERSAL_MIN_HITS, quality.REVERSAL_MIN_RATIO,
         quality.VISUAL_ORDER_MIN_RATIO) = original
    shipped = {
        "min_hits": original[0], "min_ratio": original[1],
        "visual_order_min_ratio": original[2],
        "row": next((r for r in grids["shipped"]
                     if r["min_hits"] == original[0]
                     and r["min_ratio"] == original[1]), None),
        "dictionary_only_row": next(
            (r for r in grids["dictionary_only"]
             if r["min_hits"] == original[0] and r["min_ratio"] == original[1]), None),
    }
    return {"grid": {"min_hits": list(MIN_HITS_GRID),
                     "min_ratio": list(MIN_RATIO_GRID)},
            "grids": grids, "shipped": shipped}


GRID_LABELS = {"shipped": "shipped (either signal may reject)",
               "dictionary_only": "dictionary rule alone (visual signal disabled)"}


def _print_grid(report: dict, key: str) -> None:
    ratios = report["grid"]["min_ratio"]
    corner = "hits/ratio"
    header = f"{corner:>10s}" + "".join(f"{r:>8}" for r in ratios)
    print(f"\n{GRID_LABELS[key]}")
    print(header)
    print("-" * len(header))
    by_hits: dict[int, list[dict]] = {}
    for row in report["grids"][key]:
        by_hits.setdefault(row["min_hits"], []).append(row)
    for min_hits in report["grid"]["min_hits"]:
        cells = []
        for row in sorted(by_hits[min_hits], key=lambda r: r["min_ratio"]):
            if row["correct"]:
                cells.append("ok")
            elif row["false_accept"] and not row["false_reject"]:
                cells.append(f"FA{len(row['false_accept'])}")
            elif row["false_reject"] and not row["false_accept"]:
                cells.append(f"FR{len(row['false_reject'])}")
            else:
                cells.append("both")
        print(f"{min_hits:>10}" + "".join(f"{c:>8}" for c in cells))


def print_sweep(report: dict) -> None:
    _print_grid(report, "shipped")
    _print_grid(report, "dictionary_only")
    print("\nok = every benchmark page judged correctly; "
          "FA = mirrored layer still accepted; FR = clean layer rejected")
    shipped = report["shipped"]
    print(f"\nshipped: min_hits={shipped['min_hits']} min_ratio={shipped['min_ratio']}"
          f" visual_min_ratio={shipped['visual_order_min_ratio']}")
    print(f"  with both signals   -> {shipped['row']}")
    print(f"  dictionary only     -> {shipped['dictionary_only_row']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    report = sweep()
    print_sweep(report)
    if args.json:
        path = pathlib.Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
