#!/usr/bin/env python3
"""Audit Arabic DOCX files after Microsoft Word has rendered them to PDF.

This script never drives Word. It writes the DOCX exports, then audits the PDFs
that Word produced next to them (same stem). On macOS, Word can render them by
hand (`File > Save As > PDF`, or `File > Export`); the resulting PDFs are the
evidence. Run:

    python3 scripts/word_render_audit.py --dir docs/evidence/word-arabic-2026-10-26

Running the script once creates the DOCX inputs and reports which PDFs are
still missing. Run it again after Word has exported them.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf as fitz
from docx import Document

import core

WORD_PLIST = Path("/Applications/Microsoft Word.app/Contents/Info.plist")


def word_version() -> str:
    try:
        result = subprocess.run(
            ["defaults", "read", str(WORD_PLIST), "CFBundleShortVersionString"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (result.stdout or "").strip() or "unknown"


def arabic_spans(page) -> list[dict]:
    return [span for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", []) for span in line["spans"]
            if any(0x0600 <= ord(char) <= 0x06FF or 0xFE70 <= ord(char) <= 0xFEFF
                   for char in span["text"])]


def build_documents(rows: list[dict], directory: Path) -> dict[str, list[str]]:
    markers = [f"W{index:02d}" for index in range(len(rows))]
    arabic_targets = [f'{row["ar"]} [{marker}]' for row, marker in zip(rows, markers)]

    # Both DOCX files must come from the product export; a python-docx document
    # built by hand would not carry the Word RTL paragraph properties.
    (directory / "word-translation-ar.docx").write_bytes(
        core.translations_to_word(
            [{"source": row["en"], "target": text}
             for row, text in zip(rows, arabic_targets)],
            target_lang="العربية").getvalue())

    pairs = [{"source": row["en"], "target": text}
             for row, text in zip(rows, arabic_targets)]
    (directory / "word-bilingual-ar.docx").write_bytes(
        core.pairs_to_word(pairs, source_lang="English",
                           target_lang="العربية").getvalue())

    source = Document()
    for row in rows:
        source.add_paragraph(row["en"])
    source.save(directory / "word-source-en.docx")
    return {"word-translation-ar": markers,
            "word-bilingual-ar": markers,
            "word-source-en": []}


def audit_pdf(pdf_path: Path, markers: list[str], expect_arabic: bool) -> dict:
    with fitz.open(pdf_path) as doc:
        pages = len(doc)
        extracted = "".join(page.get_text() for page in doc)
        arabic = [span for page in doc for span in arabic_spans(page)]
        first_page = doc[0]
        first_body = [span for block in first_page.get_text("dict")["blocks"]
                      for line in block.get("lines", []) for span in line["spans"]
                      if span["text"].strip()]
        first_page_arabic = arabic_spans(first_page)
        right_edge_ratio = (
            max(span["bbox"][2] for span in first_body) / first_page.rect.width
            if first_body else 0.0)
        arabic_right_edge = (
            max(span["bbox"][2] for span in first_page_arabic) / first_page.rect.width
            if first_page_arabic else 0.0)
        percent_observation = None
    compact = "".join(extracted.split())
    if "95.5%." in compact:
        percent_observation = "95.5%."
    report = {
        "pages": pages,
        "arabic_span_count": len(arabic),
        "markers_found": [marker for marker in markers if marker in compact],
        "markers_missing": [marker for marker in markers if marker not in compact],
        "first_page_right_edge_ratio": round(right_edge_ratio, 3),
        "first_page_arabic_right_edge_ratio": round(arabic_right_edge, 3),
        "percent_visual_observation": percent_observation,
        "extracted_characters": len(compact),
    }
    if expect_arabic:
        if not arabic:
            raise AssertionError(f"{pdf_path.name}: no Arabic spans in the Word render")
        if arabic_right_edge <= 0.8:
            raise AssertionError(
                f"{pdf_path.name}: Arabic starts too far from the right margin "
                f"({arabic_right_edge:.3f})")
    if report["markers_missing"]:
        raise AssertionError(
            f"{pdf_path.name}: missing markers {report['markers_missing']}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path,
                        default=ROOT / "docs/evidence/word-arabic-2026-10-26")
    args = parser.parse_args()
    directory = args.dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)

    rows = json.loads((ROOT / "tests/fixtures/arabic_acceptance.json")
                      .read_text(encoding="utf-8"))["rows"]
    markers_by_document = build_documents(rows, directory)

    missing = [name for name in markers_by_document
               if not (directory / f"{name}.pdf").exists()]
    result: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "word_version": word_version(),
        "scope": "real Microsoft Word render of Folith Arabic exports",
        "documents": {},
    }
    if missing:
        result["status"] = "AWAITING_WORD_EXPORT"
        result["pdf_missing"] = missing
        print(f"DOCX inputs are ready in {directory}; export {missing} to PDF in Word.")
    else:
        for name, markers in markers_by_document.items():
            report = audit_pdf(directory / f"{name}.pdf", markers,
                               expect_arabic=name != "word-source-en")
            with fitz.open(directory / f"{name}.pdf") as doc:
                doc[0].get_pixmap(matrix=fitz.Matrix(2, 2)).save(
                    str(directory / f"{name}-page1.png"))
            result["documents"][name] = report
        result["status"] = "PASS"
        print(f"Word rendering evidence saved to {directory}")
    (directory / "evidence.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
