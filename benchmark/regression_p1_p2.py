"""Post-fix regression against the existing Arabic benchmark (no re-selection).

* P1: re-run the end-to-end chain on the pages the quote bug corrupted and show
  the output is no longer expanded with embedded TSV fragments.
* P1 accuracy: recompute the *production* region CER with the fixed parser and
  confirm it now equals the raw OCR CER (the parser no longer damages text).
* P2: run the real Alam al-Din PDF through production ingestion in auto / force
  / off and show the broken text layer is rejected and OCR is used.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import fitz

import bench_lib as B

ROOT = Path(__file__).resolve().parent
CONFIGS = [(300, 6), (350, 3), (350, 6)]
EMBEDDED = re.compile(r"\b\d{1,3}\.\d{5,7}\b")


def _one_page_pdf(png_bytes, width_pt, height_pt):
    doc = fitz.open()
    page = doc.new_page(width=width_pt, height=height_pt)
    page.insert_image(fitz.Rect(0, 0, width_pt, height_pt), stream=png_bytes)
    data = doc.tobytes()
    doc.close()
    return data


def p1_regression():
    import core
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    windows = json.load(open(ROOT / "gt_windows.json"))
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text()
          for p in selection}
    old = {}
    for row in json.load(open(ROOT / "reconstruction.json")):
        old[(row["page_index"], row["config"])] = row
    doc = fitz.open(B.PDF_PATH)
    sizes = {p["index"]: (float(doc[p["index"]].rect.width),
                          float(doc[p["index"]].rect.height)) for p in selection}
    os.environ["TESSDATA_PREFIX"] = str(B.TESSDATA["best"])
    rows = []
    for dpi, psm in CONFIGS:
        for page in selection:
            idx = page["index"]
            png = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / "page.png").read_bytes()
            text, _warnings, details = core._ocr_pdf_text_with_warnings(
                _one_page_pdf(png, *sizes[idx]), ocr_language="ara", dpi=dpi,
                ocr_psm=psm, return_details=True)
            embedded = len(EMBEDDED.findall(text))
            key = f"best_{dpi}_{psm}"
            before = old.get((idx, key), {})
            clip = [28.0, float(windows[str(idx)][0]), 356.0, float(windows[str(idx)][1])]
            tsv = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
                   / "raw_best.tsv").read_text(encoding="utf-8")
            rtsv = B.region_tsv(tsv, clip, dpi)
            prod = core._parse_tesseract_tsv(rtsv, direction="rtl")
            prod_text = " ".join(str(b.get("text") or "") for b in prod)
            raw_region = B.region_text(tsv, clip, dpi, order="logical")
            rows.append({
                "page_index": idx, "difficulty": page["difficulty"],
                "config": key, "dpi": dpi, "psm": psm,
                "before_chars": before.get("output_chars"),
                "before_embedded_tsv": before.get("embedded_tsv_evidence"),
                "after_chars": len(text), "after_embedded_tsv": embedded,
                "recovered": bool(before.get("embedded_tsv_evidence")
                                  and embedded == 0),
                "raw_region_cer": B.cer(gt[idx], raw_region)["cer"],
                "production_region_cer": B.cer(gt[idx], prod_text)["cer"],
                "trigger": details.get("ocr_trigger"),
                "quality": details.get("pdf_text_layer_quality"),
            })
            print(f"P1 p{idx:>4} {key}: before_chars={before.get('output_chars')} "
                  f"after_chars={len(text)} before_tsv={before.get('embedded_tsv_evidence')} "
                  f"after_tsv={embedded} prodCER={rows[-1]['production_region_cer']:.3f} "
                  f"rawCER={rows[-1]['raw_region_cer']:.3f}", flush=True)
    B.write_json(ROOT / "regression_p1.json", rows)
    corrupted = [r for r in rows if r["before_embedded_tsv"]]
    print(f"\nP1: {len(corrupted)} previously corrupted runs; "
          f"{sum(1 for r in corrupted if r['after_embedded_tsv'] == 0)} now clean")
    return rows


def p2_regression():
    import core
    source = Path(B.PDF_PATH).read_bytes()
    results = {}
    for mode in ("auto", "force", "off"):
        _paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
            "Alam_al-din.pdf", source, ocr_max_pages=3, ocr_mode=mode,
            source_language="Arabic", ocr_language="ara", ocr_dpi=350, ocr_psm=6)
        results[mode] = {
            "ocr_used": report.get("ocr_used"),
            "ocr_trigger": report.get("ocr_trigger"),
            "pdf_text_layer_present": report.get("pdf_text_layer_present"),
            "pdf_text_layer_quality": report.get("pdf_text_layer_quality"),
            "pdf_text_layer_quality_score": report.get("pdf_text_layer_quality_score"),
            "pdf_text_layer_rejection_reasons": report.get(
                "pdf_text_layer_rejection_reasons"),
            "paragraph_count": len(_paragraphs),
            "first_paragraph": (_paragraphs[0][:60] if _paragraphs else ""),
            "warnings": warnings[:2],
        }
        print(f"P2 mode={mode:<5} ocr_used={results[mode]['ocr_used']} "
              f"trigger={results[mode]['ocr_trigger']} "
              f"quality={results[mode]['pdf_text_layer_quality']} "
              f"score={results[mode]['pdf_text_layer_quality_score']} "
              f"paras={results[mode]['paragraph_count']}", flush=True)
    B.write_json(ROOT / "regression_p2.json", results)
    return results


if __name__ == "__main__":
    p1_regression()
    p2_regression()
