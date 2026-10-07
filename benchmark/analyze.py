"""Aggregate the matrix into results.csv / results.json and summary tables."""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "median": None, "min": None, "max": None, "n": 0}
    return {"mean": sum(vals) / len(vals), "median": statistics.median(vals),
            "min": min(vals), "max": max(vals), "n": len(vals)}


def main() -> int:
    runs = json.load(open(ROOT / "matrix_std.json"))
    sens = json.load(open(ROOT / "matrix_model_sensitivity.json"))

    # ---------- results.csv (std matrix) ----------
    cols = ["page_index", "page_label", "difficulty", "config", "dpi", "psm", "model",
            "raw_cer", "raw_cer_folded", "raw_wer", "production_cer", "production_repaired_cer",
            "left_ablation_cer", "raw_missing_lines", "raw_spurious_lines",
            "raw_missing_line_rate", "raw_spurious_line_rate",
            "raw_rtl_order_error_count", "production_rtl_order_error_count",
            "left_ablation_rtl_order_error_count", "production_parser_quote_bug",
            "confidence_region", "tsv_s", "render_s"]
    with open(ROOT / "results.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in runs:
            row = dict(r)
            row["confidence_region"] = (r.get("confidence_region") or {}).get("mean_conf")
            writer.writerow(row)

    # ---------- per-config aggregates ----------
    by_config = defaultdict(list)
    for r in runs:
        by_config[r["config"]].append(r)
    config_summary = {}
    for cfg, rows in sorted(by_config.items()):
        config_summary[cfg] = {
            "raw_cer": _stats([r["raw_cer"] for r in rows]),
            "raw_cer_folded": _stats([r.get("raw_cer_folded") for r in rows]),
            "raw_wer": _stats([r["raw_wer"] for r in rows]),
            "production_cer": _stats([r["production_cer"] for r in rows]),
            "production_repaired_cer": _stats([r["production_repaired_cer"] for r in rows]),
            "left_ablation_cer": _stats([r["left_ablation_cer"] for r in rows]),
            "raw_missing_line_rate": _stats([r["raw_missing_line_rate"] for r in rows]),
            "raw_spurious_line_rate": _stats([r["raw_spurious_line_rate"] for r in rows]),
            "raw_rtl_errors": sum(r["raw_rtl_order_error_count"] for r in rows),
            "production_rtl_errors": sum(r["production_rtl_order_error_count"] for r in rows),
            "left_ablation_rtl_errors": sum(r["left_ablation_rtl_order_error_count"] for r in rows),
            "quote_bug_pages": sum(1 for r in rows if r["production_parser_quote_bug"]),
            "production_cer_corrupted": sum(
                1 for r in rows if r["production_cer"] > r["raw_cer"] + 0.05),
            "tsv_s": _stats([r["tsv_s"] for r in rows]),
            "render_s": _stats([r["render_s"] for r in rows]),
            "worst_page": max(rows, key=lambda r: r["raw_cer"])["page_index"],
            "worst_cer": max(r["raw_cer"] for r in rows),
            "best_page": min(rows, key=lambda r: r["raw_cer"])["page_index"],
            "best_cer": min(r["raw_cer"] for r in rows),
        }
    # ---------- per-difficulty ----------
    by_diff = defaultdict(list)
    for r in runs:
        by_diff[r["difficulty"]].append(r)
    difficulty_summary = {
        d: {"raw_cer": _stats([r["raw_cer"] for r in rows]),
            "raw_wer": _stats([r["raw_wer"] for r in rows]),
            "pages": sorted({r["page_index"] for r in rows})}
        for d, rows in sorted(by_diff.items())}

    # ---------- per-model (sensitivity) ----------
    by_model = defaultdict(list)
    for r in sens:
        by_model[r["model"]].append(r)
    model_summary = {
        m: {"raw_cer": _stats([r["raw_cer"] for r in rows]),
            "raw_cer_folded": _stats([r.get("raw_cer_folded") for r in rows]),
            "raw_wer": _stats([r["raw_wer"] for r in rows]),
            "mean_conf": _stats([r["mean_conf"] for r in rows]),
            "tsv_s": _stats([r.get("tsv_s") for r in rows]),
            "rtl_errors": sum(r["rtl_order_error_count"] for r in rows)}
        for m, rows in sorted(by_model.items())}

    # ---------- per-model per-config (300/6 vs 350/3) ----------
    model_config = {}
    for m in by_model:
        for cfg in ("300/6", "350/3"):
            dpi, psm = (int(x) for x in cfg.split("/"))
            rows = [r for r in by_model[m] if r["dpi"] == dpi and r["psm"] == psm]
            model_config[f"{m}|{cfg}"] = {
                "raw_cer": _stats([r["raw_cer"] for r in rows]),
                "raw_cer_folded": _stats([r.get("raw_cer_folded") for r in rows]),
                "raw_wer": _stats([r["raw_wer"] for r in rows]),
                "mean_conf": _stats([r["mean_conf"] for r in rows]),
                "tsv_s": _stats([r.get("tsv_s") for r in rows]),
            }

    payload = {
        "run_count": len(runs),
        "model_sensitivity_run_count": len(sens),
        "config_summary": config_summary,
        "difficulty_summary": difficulty_summary,
        "model_summary": model_summary,
        "model_config_summary": model_config,
        "page_difficulty": {str(r["page_index"]): r["difficulty"] for r in runs},
    }
    json.dump(payload, open(ROOT / "results.json", "w"), ensure_ascii=False, indent=2)

    # ---------- print ----------
    print("=== per-config (std model, 10 pages) ===")
    print(f"{'config':<14}{'meanCER':>8}{'medCER':>8}{'maxCER':>8}{'minCER':>8}{'meanWER':>8}{'missL%':>8}{'spurL%':>8}{'rtl':>5}{'qbug':>6}{'sec':>6}")
    for cfg, s in config_summary.items():
        print(f"{cfg:<14}{s['raw_cer']['mean']:>8.3f}{s['raw_cer']['median']:>8.3f}"
              f"{s['raw_cer']['max']:>8.3f}{s['raw_cer']['min']:>8.3f}"
              f"{s['raw_wer']['mean']:>8.3f}"
              f"{s['raw_missing_line_rate']['mean']*100:>8.1f}"
              f"{s['raw_spurious_line_rate']['mean']*100:>8.1f}"
              f"{s['raw_rtl_errors']:>5}{s['quote_bug_pages']:>6}{s['tsv_s']['mean']:>6.2f}")
    print("\n=== per-difficulty (std, all configs) ===")
    for d, s in difficulty_summary.items():
        print(f"{d:<8} meanCER={s['raw_cer']['mean']:.3f} medianCER={s['raw_cer']['median']:.3f} "
              f"meanWER={s['raw_wer']['mean']:.3f} pages={s['pages']}")
    print("\n=== per-model (10 pages x {300/6, 350/3}) ===")
    for m, s in model_summary.items():
        sec = s['tsv_s']['mean']
        print(f"{m:<6} meanCER={s['raw_cer']['mean']:.3f} medianCER={s['raw_cer']['median']:.3f} "
              f"minCER={s['raw_cer']['min']:.3f} maxCER={s['raw_cer']['max']:.3f} "
              f"foldedCER={s['raw_cer_folded']['mean']:.3f} "
              f"meanWER={s['raw_wer']['mean']:.3f} meanConf={s['mean_conf']['mean']:.1f} "
              f"sec={sec if sec is not None else float('nan'):.2f} rtl={s['rtl_errors']}")
    print("\n=== model x config ===")
    for k, s in model_config.items():
        sec = s['tsv_s']['mean']
        print(f"{k:<12} CER={s['raw_cer']['mean']:.3f} WER={s['raw_wer']['mean']:.3f} "
              f"conf={s['mean_conf']['mean']:.1f} sec={sec if sec is not None else float('nan'):.2f}")
    print("\n=== production reconstruction (std) ===")
    total_qbug = sum(s["quote_bug_pages"] for s in config_summary.values())
    total_corrupt = sum(s["production_cer_corrupted"] for s in config_summary.values())
    print(f"runs with a raw '\"' in TSV: {total_qbug}/60; runs where production CER exceeded raw CER by >5pts: {total_corrupt}/60")
    print(f"raw RTL order errors: {sum(s['raw_rtl_errors'] for s in config_summary.values())}/60")
    print(f"production RTL order errors: {sum(s['production_rtl_errors'] for s in config_summary.values())}/60")
    print(f"pre-fix left-sort RTL order errors: {sum(s['left_ablation_rtl_errors'] for s in config_summary.values())}/60")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
