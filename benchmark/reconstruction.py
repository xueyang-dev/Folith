"""Reconstruction / layout evidence from the end-to-end production outputs."""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz

import bench_lib as B

ROOT = Path(__file__).resolve().parent
DIGITS = re.compile(r"^[\u0660-\u0669\u06f0-\u06f90-9]{1,4}$")


def edge_page_number(doc, idx: int) -> str:
    """OCR the top/bottom 8% bands to find the printed page number."""
    page = doc[idx]
    h = page.rect.height
    found = []
    for clip in (fitz.Rect(0, 0, page.rect.width, h * 0.10),
                 fitz.Rect(0, h * 0.90, page.rect.width, h)):
        png, _, _, _ = B.render_page(doc, idx, 400, clip=clip)
        run = B.run_tesseract(png, model="best", psm=7, output="txt")
        for token in re.split(r"\s+", run["stdout"].strip()):
            token = token.strip("|.:-")
            if DIGITS.match(token):
                found.append(token)
    return found[0] if found else ""


def main() -> int:
    selection = json.load(open(ROOT / "select_pages.json"))["pages"]
    gt = {p["index"]: (ROOT / "ground_truth" / f"page_{p['index']:04d}.txt").read_text()
          for p in selection}
    doc = fitz.open(B.PDF_PATH)
    records = []
    for page in selection:
        idx = page["index"]
        printed = edge_page_number(doc, idx)
        for path in sorted((ROOT / "pages" / f"page_{idx:04d}" / "end_to_end").glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            lines = [ln for ln in text.splitlines() if ln.strip()]
            rtl = B.rtl_order_error(gt[idx], text)
            leaked = bool(printed and printed in text)
            records.append({
                "page_index": idx, "difficulty": page["difficulty"],
                "config": path.stem,
                "printed_page_number": printed,
                "page_number_leaked": leaked,
                "output_chars": len(text),
                "gt_chars": len(gt[idx].replace("\n", "").replace(" ", "")),
                "output_lines": len(lines),
                "gt_lines": len(gt[idx].splitlines()),
                "extension_ratio": len(text) / max(1, len(gt[idx])),
                "rtl_order_error_count": rtl["rtl_order_error_count"],
                "word_reversed_line_count": rtl["word_reversed_line_count"],
                "line_order_reversed": rtl["line_order_reversed"],
                "embedded_tsv_evidence": len(re.findall(r"\b\d{1,3}\.\d{5,7}\b", text)),
            })
    B.write_json(ROOT / "reconstruction.json", records)
    for r in records:
        print(f"p{r['page_index']:>4} {r['config']:<12} printed={r['printed_page_number']!r:>6} "
              f"leak={int(r['page_number_leaked'])} chars={r['output_chars']:>5} "
              f"ext_ratio={r['extension_ratio']:.1f} rtlErr={r['rtl_order_error_count']} "
              f"tsv_evid={r['embedded_tsv_evidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
