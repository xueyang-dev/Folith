"""Run the DPI x PSM x model OCR matrix and persist every raw artifact.

Accuracy is measured on the GT passage: the clip window is auto-aligned to the
OCR line that best matches the GT, so a partially visible neighbouring line
cannot inflate insertions.  Full-page OCR is captured separately for layout,
reconstruction and performance evidence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz

import bench_lib as B

ROOT = Path(__file__).resolve().parent
DPI_PSM = [(220, 3), (220, 6), (300, 3), (300, 6), (350, 3), (350, 6)]
MODELS = ["std", "fast", "best"]


def main() -> int:
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text(encoding="utf-8")
          for p in selection}
    windows = json.load(open(ROOT / "gt_windows.json"))
    clips = {p["index"]: [28.0, float(windows[str(p["index"])][0]),
                          356.0, float(windows[str(p["index"])][1])]
             for p in selection}
    doc = fitz.open(B.PDF_PATH)
    runs = []
    for page in selection:
        idx = page["index"]
        page_dir = ROOT / "pages" / f"page_{idx:04d}"
        for dpi in sorted({c[0] for c in DPI_PSM}):
            png, render_s, width, height = B.render_page(doc, idx, dpi)
            dpi_dir = page_dir / f"dpi{dpi}"
            dpi_dir.mkdir(parents=True, exist_ok=True)
            (dpi_dir / "page.png").write_bytes(png)
            B.write_json(dpi_dir / "render.json", {
                "dpi": dpi, "render_s": render_s, "width": width, "height": height,
                "png_bytes": len(png)})
            for psm in sorted({c[1] for c in DPI_PSM if c[0] == dpi}):
                cfg = f"dpi{dpi}_psm{psm}"
                out = dpi_dir / f"psm{psm}"
                out.mkdir(parents=True, exist_ok=True)
                tsv_run = B.run_tesseract(png, model="std", psm=psm, output="tsv")
                txt_run = B.run_tesseract(png, model="std", psm=psm, output="txt")
                tsv = tsv_run["stdout"]
                (out / "raw.tsv").write_text(tsv, encoding="utf-8")
                (out / "native.txt").write_text(txt_run["stdout"], encoding="utf-8")
                raw_text = B.tsv_to_logical_text(tsv)
                (out / "raw.txt").write_text(raw_text, encoding="utf-8")
                (out / "left_sorted_ablation.txt").write_text(
                    B.tsv_left_sorted_text(tsv), encoding="utf-8")
                prod = B.folith_reconstruct(tsv, direction="rtl")
                (out / "reconstructed.txt").write_text(prod["block_text"], encoding="utf-8")
                prod_fixed = B.folith_reconstruct(tsv, direction="rtl", repair_quotes=True)
                (out / "reconstructed_repaired.txt").write_text(
                    prod_fixed["block_text"], encoding="utf-8")

                clip = clips[idx]
                rtsv = B.region_tsv(tsv, clip, dpi)
                region_raw = B.region_text(tsv, clip, dpi, order="logical")
                region_left = B.region_text(tsv, clip, dpi, order="left")
                prod_region = B.folith_reconstruct(rtsv, direction="rtl")
                prod_region_fixed = B.folith_reconstruct(rtsv, direction="rtl",
                                                         repair_quotes=True)

                metrics = {
                    "page_index": idx, "page_label": page["label"],
                    "difficulty": page["difficulty"], "config": cfg,
                    "dpi": dpi, "psm": psm, "model": "std",
                    "gt_chars": len(B.normalize(gt[idx]).replace(" ", "")),
                    "gt_words": len(gt[idx].split()),
                    "render_s": render_s, "tsv_s": tsv_run["elapsed_s"],
                    "txt_s": txt_run["elapsed_s"],
                    "tsv_cpu_s": tsv_run["cpu_user_s"] + tsv_run["cpu_sys_s"],
                    "child_maxrss_bytes": tsv_run["child_maxrss_bytes"],
                    "returncode": tsv_run["returncode"],
                    "full_word_count": len(raw_text.split()),
                    "native_txt_matches_tsv_order": (
                        B.normalize(txt_run["stdout"]) == B.normalize(raw_text)),
                    "confidence_full": B.confidence_stats(tsv),
                    "confidence_region": B.region_confidence(tsv, clip, dpi),
                    "production_parser_quote_bug": B.tsv_has_unescaped_quote(tsv),
                    "production_word_count": len(prod["block_text"].split()),
                    "gt_window": windows[str(idx)],
                    "region_word_count": len(region_raw.split()),
                }
                for name, hyp in (("raw", region_raw), ("left_ablation", region_left),
                                  ("production", prod_region["block_text"]),
                                  ("production_repaired", prod_region_fixed["block_text"])):
                    c = B.cer(gt[idx], hyp)
                    w = B.wer(gt[idx], hyp)
                    lm = B.line_metrics(gt[idx], hyp)
                    rtl = B.rtl_order_error(gt[idx], hyp)
                    metrics.update({
                        f"{name}_cer": c["cer"], f"{name}_wer": w["wer"],
                        f"{name}_sub": c["sub"], f"{name}_del": c["del"],
                        f"{name}_ins": c["ins"],
                        f"{name}_missing_lines": lm["missing_lines"],
                        f"{name}_spurious_lines": lm["spurious_lines"],
                        f"{name}_missing_line_rate": lm["missing_line_rate"],
                        f"{name}_spurious_line_rate": lm["spurious_line_rate"],
                        f"{name}_word_order_reversed": rtl["word_order_reversed"],
                        f"{name}_line_order_reversed": rtl["line_order_reversed"],
                        f"{name}_rtl_order_error_count": rtl["rtl_order_error_count"],
                    })
                B.write_json(out / "metrics.json", metrics)
                runs.append(metrics)
                print(f"[std {cfg}] p{idx:>4} ({page['difficulty']:>6}) "
                      f"rawCER={metrics['raw_cer']:.3f} "
                      f"prodCER={metrics['production_cer']:.3f} "
                      f"repCER={metrics['production_repaired_cer']:.3f} "
                      f"leftCER={metrics['left_ablation_cer']:.3f} "
                      f"rtlErr={metrics['raw_rtl_order_error_count']} "
                      f"qbug={int(metrics['production_parser_quote_bug'])}", flush=True)
    B.write_json(ROOT / "matrix_std.json", runs)

    # ---- model sensitivity: all pages, best config from the std matrix ----
    sens = []
    for model in ("fast", "best"):
        for page in selection:
            idx = page["index"]
            for dpi, psm in ((300, 6), (350, 3)):
                png = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / "page.png").read_bytes()
                tsv_run = B.run_tesseract(png, model=model, psm=psm, output="tsv")
                tsv = tsv_run["stdout"]
                out = ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
                out.mkdir(parents=True, exist_ok=True)
                (out / f"raw_{model}.tsv").write_text(tsv, encoding="utf-8")
                (out / f"raw_{model}.txt").write_text(
                    B.tsv_to_logical_text(tsv), encoding="utf-8")
                clip = clips[idx]
                region_raw = B.region_text(tsv, clip, dpi, order="logical")
                c = B.cer(gt[idx], region_raw)
                w = B.wer(gt[idx], region_raw)
                rtl = B.rtl_order_error(gt[idx], region_raw)
                lm = B.line_metrics(gt[idx], region_raw)
                sens.append({
                    "page_index": idx, "difficulty": page["difficulty"],
                    "model": model, "dpi": dpi, "psm": psm,
                    "raw_cer": c["cer"], "raw_wer": w["wer"],
                    "missing_lines": lm["missing_lines"],
                    "spurious_lines": lm["spurious_lines"],
                    "rtl_order_error_count": rtl["rtl_order_error_count"],
                    "tsv_s": tsv_run["elapsed_s"],
                    "mean_conf": B.region_confidence(tsv, clip, dpi)["mean_conf"],
                    "gt_window": windows[str(idx)],
                })
                print(f"[{model} {dpi}/{psm}] p{idx:>4} CER={c['cer']:.3f} "
                      f"WER={w['wer']:.3f} conf={sens[-1]['mean_conf'] and round(sens[-1]['mean_conf'],1)}",
                      flush=True)
    B.write_json(ROOT / "matrix_model_sensitivity.json", sens)
    print(f"\n{len(runs)} std runs + {len(sens)} sensitivity runs written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
