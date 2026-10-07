"""End-to-end Folith chain + LLM source-cleanup comparison (best 2 configs).

* ``end_to_end``: build a one-page PDF from the rendered scan, run the real
  ``core._ocr_pdf_text_with_warnings`` (ara, chosen DPI/PSM), and record what the
  production chain actually produces, including its runtime metadata.
* ``cleanup``: feed the GT-passage OCR text through the real
  ``core.cleanup_source_paragraphs`` and measure CER/WER before vs after, plus
  corrected / introduced / unchanged error counts and hallucination checks.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import fitz

import bench_lib as B

ROOT = Path(__file__).resolve().parent
CONFIGS = [("best", 300, 6), ("best", 350, 3)]


def _one_page_pdf(png_bytes: bytes, width_pt: float, height_pt: float) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    page.insert_image(fitz.Rect(0, 0, width_pt, height_pt), stream=png_bytes)
    data = doc.tobytes()
    doc.close()
    return data


def _gt_region_input(idx: int, model: str, dpi: int, psm: int, clip) -> list[str]:
    tsv = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
           / f"raw_{model}.tsv").read_text(encoding="utf-8")
    text = B.region_text(tsv, clip, dpi, order="logical")
    return [ln for ln in text.splitlines() if ln.strip()]


def main() -> int:
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    windows = json.load(open(ROOT / "gt_windows.json"))
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text()
          for p in selection}
    clips = {p["index"]: [28.0, float(windows[str(p["index"])][0]),
                          356.0, float(windows[str(p["index"])][1])] for p in selection}
    doc = fitz.open(B.PDF_PATH)
    page_sizes = {p["index"]: (float(doc[p["index"]].rect.width),
                               float(doc[p["index"]].rect.height)) for p in selection}

    import core  # production chain, read-only

    # ---------------- end to end ----------------
    e2e = []
    for model, dpi, psm in CONFIGS:
        os.environ["TESSDATA_PREFIX"] = str(B.TESSDATA[model])
        for page in selection:
            idx = page["index"]
            png = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / "page.png").read_bytes()
            pdf = _one_page_pdf(png, *page_sizes[idx])
            text, warnings, details = core._ocr_pdf_text_with_warnings(
                pdf, ocr_language="ara", dpi=dpi, ocr_psm=psm, return_details=True)
            out = ROOT / "pages" / f"page_{idx:04d}" / "end_to_end"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{model}_{dpi}_{psm}.txt").write_text(text, encoding="utf-8")
            B.write_json(out / f"{model}_{dpi}_{psm}.json", {
                "page_index": idx, "difficulty": page["difficulty"],
                "model": model, "dpi": dpi, "psm": psm,
                "ocr_language_engine_code": details.get("ocr_language_engine_code"),
                "ocr_text_direction": details.get("ocr_text_direction"),
                "ocr_dpi": details.get("ocr_dpi"), "ocr_psm": details.get("ocr_psm"),
                "ocr_backend": details.get("ocr_backend"),
                "installed_language_available": details.get("installed_language_available"),
                "mean_confidence": details.get("mean_confidence"),
                "page_order": details.get("page_order"),
                "completed_pages": details.get("completed_pages"),
                "failed_pages": details.get("failed_pages"),
                "warnings": warnings,
                "char_count": len(text), "paragraph_count": len(text.split("\n\n")),
            })
            e2e.append({"page_index": idx, "difficulty": page["difficulty"],
                        "model": model, "dpi": dpi, "psm": psm,
                        "chars": len(text), "warnings": warnings,
                        "details": {k: details.get(k) for k in (
                            "ocr_language_engine_code", "ocr_text_direction", "ocr_dpi",
                            "ocr_psm", "ocr_backend", "installed_language_available",
                            "mean_confidence", "page_order", "completed_pages",
                            "failed_pages")}})
            print(f"[e2e {model} {dpi}/{psm}] p{idx} chars={len(text)} "
                  f"conf={details.get('mean_confidence')}", flush=True)
    B.write_json(ROOT / "end_to_end.json", e2e)

    # ---------------- cleanup ----------------
    output_dir = core.OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT.parent / output_dir
    cfg = json.loads((output_dir / "provider_config.json").read_text())
    provider, api_key, model_name = cfg["provider"], cfg["api_key"], cfg["model"]
    base_url = cfg.get("base_url") or None
    core.set_llm_base_url(base_url)
    cleanup_runs = []
    for ocr_model, dpi, psm in CONFIGS:
        for page in selection:
            idx = page["index"]
            lines = _gt_region_input(idx, ocr_model, dpi, psm, clips[idx])
            before = "\n".join(lines)
            try:
                cleaned, meta, warns = core.cleanup_source_paragraphs(
                    lines, provider, api_key, model_name,
                    call_llm_fn=core.call_llm,
                    ocr_language="ara", source_language="Arabic",
                    text_direction="rtl", ocr_engine_code="ara",
                    confidence_threshold=0.0)
                after = "\n".join(cleaned)
                status = meta.get("status")
            except Exception as exc:  # noqa: BLE001 - provider failure is data too
                cleaned, meta, warns = lines, {}, [str(exc)[:300]]
                after, status = before, f"error:{type(exc).__name__}"
            c_before = B.cer(gt[idx], before)
            c_after = B.cer(gt[idx], after)
            w_before = B.wer(gt[idx], before)
            w_after = B.wer(gt[idx], after)
            trans = B.error_transition(before, after, gt[idx])
            record = {
                "page_index": idx, "difficulty": page["difficulty"],
                "ocr_model": ocr_model, "dpi": dpi, "psm": psm,
                "status": status,
                "cer_before": c_before["cer"], "cer_after": c_after["cer"],
                "wer_before": w_before["wer"], "wer_after": w_after["wer"],
                "cer_delta": c_after["cer"] - c_before["cer"],
                "wer_delta": w_after["wer"] - w_before["wer"],
                "corrected_errors": trans["corrected"],
                "introduced_errors": trans["introduced"],
                "unchanged_errors": trans["unchanged"],
                "gt_len": trans["gt_len"],
                "changed_segments": len(meta.get("changed_segments") or []),
                "paragraph_merges": len(meta.get("paragraph_merges") or []),
                "llm_input_count": meta.get("llm_input_count"),
                "input_count": len(lines),
                "output_count": len(cleaned),
                "warnings": warns[:3],
            }
            cleanup_runs.append(record)
            out = ROOT / "pages" / f"page_{idx:04d}" / "cleanup"
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{ocr_model}_{dpi}_{psm}_before.txt").write_text(before, encoding="utf-8")
            (out / f"{ocr_model}_{dpi}_{psm}_after.txt").write_text(after, encoding="utf-8")
            B.write_json(out / f"{ocr_model}_{dpi}_{psm}.json", {**record, "raw_meta": {
                k: v for k, v in (meta or {}).items()
                if k in {"status", "batch_count", "failed_batches", "llm_input_count",
                         "llm_bypassed_count", "uncertainty_counts", "resumed_batches"}}})
            print(f"[cleanup {ocr_model} {dpi}/{psm}] p{idx} status={status} "
                  f"CER {c_before['cer']:.3f} -> {c_after['cer']:.3f} "
                  f"(corr={trans['corrected']} intro={trans['introduced']})", flush=True)
    B.write_json(ROOT / "cleanup_results.json", cleanup_runs)
    print(f"\n{len(e2e)} e2e + {len(cleanup_runs)} cleanup runs written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
