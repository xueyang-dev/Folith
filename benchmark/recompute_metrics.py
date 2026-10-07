"""Recompute region metrics from saved raw.tsv evidence (no OCR re-run)."""
from __future__ import annotations

import json
from pathlib import Path

import bench_lib as B

ROOT = Path(__file__).resolve().parent
DPI_PSM = [(220, 3), (220, 6), (300, 3), (300, 6), (350, 3), (350, 6)]


def recompute_std():
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    windows = json.load(open(ROOT / "gt_windows.json"))
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text()
          for p in selection}
    runs = []
    for page in selection:
        idx = page["index"]
        clip = [28.0, float(windows[str(idx)][0]), 356.0, float(windows[str(idx)][1])]
        for dpi, psm in DPI_PSM:
            out = ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
            tsv = (out / "raw.tsv").read_text(encoding="utf-8")
            metrics = json.load(open(out / "metrics.json"))
            rtsv = B.region_tsv(tsv, clip, dpi)
            prod = B.folith_reconstruct(rtsv, direction="rtl")
            prod_fixed = B.folith_reconstruct(rtsv, direction="rtl", repair_quotes=True)
            region_raw = B.region_text(tsv, clip, dpi, order="logical")
            region_left = B.region_text(tsv, clip, dpi, order="left")
            lm = B.line_metrics(gt[idx], region_raw)
            metrics["raw_missing_lines"] = lm["missing_lines"]
            metrics["raw_spurious_lines"] = lm["spurious_lines"]
            metrics["raw_missing_line_rate"] = lm["missing_line_rate"]
            metrics["raw_spurious_line_rate"] = lm["spurious_line_rate"]
            metrics["gt_line_count"] = lm["ref_line_count"]
            metrics["ocr_line_count"] = lm["hyp_line_count"]
            for name, hyp in (("raw", region_raw), ("left_ablation", region_left),
                              ("production", prod["block_text"]),
                              ("production_repaired", prod_fixed["block_text"])):
                metrics[f"{name}_cer_folded"] = B.cer_folded(gt[idx], hyp)
                rtl = B.rtl_order_error(gt[idx], hyp)
                metrics[f"{name}_word_order_reversed"] = rtl["word_order_reversed"]
                metrics[f"{name}_line_order_reversed"] = rtl["line_order_reversed"]
                metrics[f"{name}_word_reversed_line_count"] = rtl["word_reversed_line_count"]
                metrics[f"{name}_rtl_order_error_count"] = rtl["rtl_order_error_count"]
            B.write_json(out / "metrics.json", metrics)
            runs.append(metrics)
    B.write_json(ROOT / "matrix_std.json", runs)
    return runs


def recompute_sensitivity():
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    windows = json.load(open(ROOT / "gt_windows.json"))
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text()
          for p in selection}
    sens = []
    prior = {}
    existing = ROOT / "matrix_model_sensitivity.json"
    if existing.is_file():
        for row in json.load(open(existing)):
            prior[(row.get("model"), row.get("page_index"), row.get("dpi"),
                   row.get("psm"))] = row
    for model in ("fast", "best"):
        for page in selection:
            idx = page["index"]
            clip = [28.0, float(windows[str(idx)][0]), 356.0, float(windows[str(idx)][1])]
            for dpi, psm in ((300, 6), (350, 3)):
                out = ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
                tsv = (out / f"raw_{model}.tsv").read_text(encoding="utf-8")
                region = B.region_text(tsv, clip, dpi, order="logical")
                c = B.cer(gt[idx], region)
                w = B.wer(gt[idx], region)
                rtl = B.rtl_order_error(gt[idx], region)
                lm = B.line_metrics(gt[idx], region)
                sens.append({
                    "page_index": idx, "difficulty": page["difficulty"], "model": model,
                    "dpi": dpi, "psm": psm, "raw_cer": c["cer"], "raw_wer": w["wer"],
                    "raw_cer_folded": B.cer_folded(gt[idx], region),
                    "missing_lines": lm["missing_lines"], "spurious_lines": lm["spurious_lines"],
                    "missing_line_rate": lm["missing_line_rate"],
                    "spurious_line_rate": lm["spurious_line_rate"],
                    "rtl_order_error_count": rtl["rtl_order_error_count"],
                    "word_reversed_line_count": rtl["word_reversed_line_count"],
                    "mean_conf": B.region_confidence(tsv, clip, dpi)["mean_conf"],
                })
                old = prior.get((model, idx, dpi, psm)) or {}
                for key in ("tsv_s", "tsv_cpu_s", "child_maxrss_bytes"):
                    if old.get(key) is not None:
                        sens[-1][key] = old[key]
    B.write_json(ROOT / "matrix_model_sensitivity.json", sens)
    return sens


if __name__ == "__main__":
    r = recompute_std()
    s = recompute_sensitivity()
    print(f"recomputed {len(r)} std runs and {len(s)} sensitivity runs")
