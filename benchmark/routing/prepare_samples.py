"""Materialise the text-layer routing benchmark.

Three groups, chosen so the reversal rule is judged on real documents rather
than on the challenge page alone:

``clean_arabic``
    Real documents whose Arabic text layer is correct.  Must stay accepted.
``degraded_reversed``
    Real historical scans whose text layer stores Arabic mirrored.  Must be
    rejected and routed to visual understanding.
``ambiguous``
    Non-Arabic, mixed-script, numeral-heavy and text-layer-less documents, plus
    the application's own Arabic PDF export (which has a *different*, genuine
    defect).  These guard against the rule over-firing.

Samples are not committed (several are megabytes); the manifest records where
each one came from, so the harness can rebuild them.

Usage::

    ./venv/bin/python benchmark/routing/prepare_samples.py
    ./venv/bin/python benchmark/routing/prepare_samples.py --force
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
MANIFEST = HERE / "samples.json"

#: Real Arabic prose taken from a public document, used to build *constructed*
#: controls with a renderer whose text layer is known to be correct (Typst).
PUBLIC_SOURCES = {
    "arabic-wikipedia-language": "https://ar.wikipedia.org/api/rest_v1/page/pdf/"
                       "%D8%A7%D9%84%D9%84%D8%BA%D8%A9_%D8%A7%D9%84%D8%B9%D8%B1%D8%A8%D9%8A%D8%A9",
    "arabic-wikipedia-cairo": "https://ar.wikipedia.org/api/rest_v1/page/pdf/"
                    "%D8%A7%D9%84%D9%82%D8%A7%D9%87%D8%B1%D8%A9",
}

REPO_SAMPLES = {
    "arabic-rtl-fixture": "benchmark/opdf/corpus/arabic-rtl.pdf",
    "rtl-fixture": "benchmark/vlm-migration/rtl.pdf",
    "arabic-mixed-fixture": "benchmark/paddle-smoke/arabic-mixed.pdf",
    "rtl-repaired-fixture": "benchmark/vlm-migration/rtl-repaired/jobs/after-rtl/source.bin",
    "challenge-historical": "benchmark/opdf/corpus/challenge-historical.pdf",
    "historical-scan": "benchmark/vlm-migration/historical.pdf",
    "historical-subset-4p": ".audit/ocr-sources-verification/subset-5-120-220-300.pdf",
    "english-native": "benchmark/opdf/corpus/native-english.pdf",
    "english-arxiv-2col": "benchmark/opdf/corpus/arxiv-attention.pdf",
    "english-report": "benchmark/opdf/corpus/report-middle-east.pdf",
    "english-book": "benchmark/opdf/corpus/book-fighter-pilot.pdf",
    "mixed-novel": "benchmark/opdf/corpus/novel-midnight-library.pdf",
    "scan-no-text-layer": "benchmark/opdf/corpus/scan-clean.pdf",
    "app-arabic-export": "exports/arabic-acceptance-20261001/en-to-ar-translation.pdf",
}

#: group -> sample id -> expectation
BENCHMARK = {
    "clean_arabic": {
        "arabic-wikipedia-language": "accepted",
        "arabic-wikipedia-cairo": "accepted",
        "arabic-rtl-fixture": "accepted",
        "rtl-fixture": "accepted",
        "arabic-mixed-fixture": "accepted",
        "rtl-repaired-fixture": "accepted",
        "constructed-arabic-paragraphs": "accepted",
        "constructed-arabic-numerals": "accepted",
        "constructed-arabic-mixed-script": "accepted",
    },
    "degraded_reversed": {
        "challenge-historical": "rejected",
        "historical-scan": "rejected",
        "historical-subset-4p": "rejected",
    },
    "ambiguous": {
        "english-native": "accepted",
        "english-arxiv-2col": "accepted",
        "english-report": "accepted",
        "english-book": "accepted",
        "mixed-novel": "accepted",
        "scan-no-text-layer": "absent",
        "app-arabic-export": "rejected",
    },
}

#: Where an expectation is not the obvious one, say why.
NOTES = {
    "app-arabic-export": (
        "The application's own Arabic PDF export. Its text layer carries a stray "
        "U+0006, so the pre-existing control-character rule rejects it. Recorded "
        "so the rejection is attributed to its real cause, not to this change."),
    "scan-no-text-layer": (
        "No text layer at all: the gate reports 'absent', which routes to visual "
        "understanding for a different reason than a quality rejection."),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch(url: str, target: pathlib.Path) -> None:
    import httpx
    response = httpx.get(url, follow_redirects=True, timeout=180, verify=False,
                         headers={"User-Agent": "Mozilla/5.0 (research corpus)"})
    response.raise_for_status()
    if response.content[:4] != b"%PDF":
        raise SystemExit(f"{url} did not return a PDF")
    target.write_bytes(response.content)


def _arabic_prose() -> list[str]:
    """Real Arabic paragraphs taken from the fetched public document."""
    source = SAMPLES / "arabic-wikipedia-language.pdf"
    if not source.is_file():
        return []
    import pymupdf
    with pymupdf.open(source) as document:
        text = "\n".join(page.get_text() for page in document)
    out = []
    for raw in text.split("\n"):
        paragraph = raw.strip()
        if len(paragraph) < 60:
            continue
        # Skip the export service's own furniture (edit links, percent-encoded
        # URLs); those are not prose and would make a poor control.
        if "http" in paragraph or "%D" in paragraph or "action=edit" in paragraph:
            continue
        letters = [c for c in paragraph if c.isalpha()]
        if not letters or sum(1 for c in letters if 0x0600 <= ord(c) <= 0x06FF) / len(letters) < 0.8:
            continue
        out.append(paragraph)
    return out


def _typst_pdf(paragraphs: list[str], target: pathlib.Path, *, extra: str = "",
               size: float = 11.0) -> None:
    """Render real Arabic prose with the renderer whose text layer is verified.

    The original-layout phase established that Typst writes a correct Arabic
    text layer (base characters, logical order, correct lam-alef ToUnicode), so
    a page produced this way is a valid *clean* control.
    """
    import typst
    from folith.pdfrender import typeset

    families = ", ".join(f'"{name}"' for name in typeset.choose_fonts("ا").families)
    lines = [
        "#set page(width: 470pt, height: 330pt, margin: 24pt)",
        f'#set text(font: ({families}), size: {size}pt, lang: "ar")',
        "#set par(leading: 0.75em, justify: true)",
    ]
    for paragraph in paragraphs:
        escaped = (paragraph.replace("\\", "\\\\").replace("#", "\\#")
                   .replace("[", "\\[").replace("]", "\\]")
                   .replace("*", "\\*").replace("_", "\\_"))
        lines.append(f"#text(dir: rtl)[{escaped}]")
        lines.append("#v(6pt)")
    if extra:
        lines.append(extra)
    target.write_bytes(typst.compile(("\n".join(lines) + "\n").encode("utf-8")))


def build(force: bool = False) -> dict:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "groups": {}, "notes": NOTES}

    def record(group: str, sample_id: str, path: pathlib.Path, provenance: str):
        data = path.read_bytes()
        entry = {
            "id": sample_id,
            "file": f"samples/{path.name}",
            "group": group,
            "expect": BENCHMARK[group][sample_id],
            "provenance": provenance,
            "bytes": len(data),
            "sha256": _sha256(data),
        }
        manifest["groups"].setdefault(group, []).append(entry)
        print(f"  {group:18s} {sample_id:32s} {len(data):>9d}B  expect={entry['expect']}")

    for sample_id, url in PUBLIC_SOURCES.items():
        target = SAMPLES / f"{sample_id}.pdf"
        if force or not target.is_file():
            print(f"fetching {sample_id} ...")
            _fetch(url, target)
        record("clean_arabic", sample_id,
               target, f"public:{url}")

    for sample_id, relative in REPO_SAMPLES.items():
        source = ROOT / relative
        if not source.is_file():
            raise SystemExit(f"missing repo sample {relative}")
        group = next(g for g, items in BENCHMARK.items() if sample_id in items)
        # Saved job sources are stored as `source.bin` but are PDFs; the
        # ingestion contract keys off the filename, so the extension must say
        # what the bytes are.
        suffix = ".pdf" if source.read_bytes()[:4] == b"%PDF" else (source.suffix or ".pdf")
        target = SAMPLES / f"{sample_id}{suffix}"
        if force or not target.is_file():
            shutil.copy2(source, target)
        record(group, sample_id, target, f"repo:{relative}")

    prose = _arabic_prose()
    if not prose:
        raise SystemExit("no Arabic prose available for the constructed controls")
    built = {
        "constructed-arabic-paragraphs": (prose[:6], ""),
        "constructed-arabic-numerals": (
            [p for p in prose if any(ch.isdigit() for ch in p)][:5] or prose[:5],
            "#text(dir: rtl)[2024 2019 63% 9% 1,400 270 94% 20% – 5]"),
        "constructed-arabic-mixed-script": (
            [p.replace("،", "، Google، Microsoft،") for p in prose[:5]],
            "#text(dir: ltr)[OAG Aviation Worldwide Limited 2025]"),
    }
    for sample_id, (paras, extra) in built.items():
        target = SAMPLES / f"{sample_id}.pdf"
        _typst_pdf(paras, target, extra=extra)
        record("clean_arabic", sample_id, target,
               "constructed:real Arabic prose from arabic-wikipedia-language, "
               "typeset with Typst (verified correct text layer)")

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    total = sum(len(v) for v in manifest["groups"].values())
    print(f"\n{total} samples; manifest written to {MANIFEST.relative_to(ROOT)}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="re-fetch and re-copy every sample")
    args = parser.parse_args()
    build(force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
