"""Freeze the Original-layout PDF Renderer v1 acceptance corpus.

The corpus is written once into ``benchmark/opdf/corpus/`` with a manifest that
records where each page came from and what it is expected to exercise.  Later
stages read the manifest; they never re-derive the page set, so a failing page
cannot quietly leave the denominator.

Run:
    ./venv/bin/python benchmark/opdf/prepare_corpus.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pymupdf

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT = HERE / "corpus"
MANIFEST = HERE / "manifest.json"

# provenance: where the bytes came from.  ``repo:<path>`` is a document that is
# already stored in this working tree (a real job's saved source PDF or a repo
# fixture); ``public:<url>`` was fetched once and its sha256 is pinned below.
CORPUS = [
    {
        "id": "arxiv-attention",
        "title": "Attention Is All You Need (arXiv:1706.03762v7)",
        "source": "public:https://arxiv.org/pdf/1706.03762v7",
        "pages": [1, 8],
        "lang": "en",
        "target_lang": "简体中文",
        "features": ["native", "two-column", "formula", "table", "links", "toc"],
        "expected": (
            "Two-column academic page. Title/abstract spans both columns. Equations are "
            "vector drawings and must survive text removal untouched. Table rules and the "
            "TOC-derived bookmarks must survive. Links (citations/URLs) must stay valid."
        ),
    },
    {
        "id": "book-fighter-pilot",
        "title": "When the Sky Was Ours — A Fighter Pilot's Odyssey, pp. 7-12",
        "source": "repo:outputs/56b0ecebf5a56a2f/source.bin",
        "pages": [7, 12],
        "lang": "en",
        "target_lang": "简体中文",
        "features": ["native", "single-column", "cross-page-paragraph", "toc", "links"],
        "expected": (
            "Single-column continuous prose. Pages 9->10 and 11->12 continue one paragraph, "
            "so the segment mapping must survive a page break. Chapter headings are large "
            "and bold; body text is 15pt LiberationSerif."
        ),
    },
    {
        "id": "report-middle-east",
        "title": "Middle East Aviation 2025 report, pp. 4-9",
        "source": "repo:outputs/c537f06f797b24ad/source.bin",
        "pages": [4, 9],
        "lang": "en",
        "target_lang": "العربية",
        "features": ["native", "graphics", "table", "images", "heading"],
        "expected": (
            "Colour branding bands, shaded table rows and two embedded chart images. "
            "Removing the source text must not damage the bands, the shading or the charts. "
            "Translated text is Arabic, so it must be shaped RTL and stay searchable."
        ),
    },
    {
        "id": "novel-midnight-library",
        "title": "The Midnight Library (Matt Haig), pp. 8-9",
        "source": "repo:outputs/586f32a79a54e8f4/source.bin",
        "pages": [8, 9],
        "lang": "en",
        "target_lang": "简体中文",
        "features": ["mixed", "images"],
        "expected": (
            "This book is a mixed PDF: most pages carry a text layer, a few are image-only. "
            "Native text objects and scanned pixels must be handled separately."
        ),
    },
    {
        "id": "scan-clean",
        "title": "Clean scanned English page (repo fixture)",
        "source": "repo:benchmark/vlm-migration/clean_scan.pdf",
        "pages": [1, 1],
        "lang": "en",
        "target_lang": "简体中文",
        "features": ["scan", "no-text-layer"],
        "expected": (
            "One full-page raster with no text layer. Only regions with verified geometry "
            "may be covered; anything else must be reported, never silently skipped."
        ),
    },
    {
        "id": "native-english",
        "title": "Minimal native English page (repo fixture)",
        "source": "repo:benchmark/vlm-migration/native.pdf",
        "pages": [1, 1],
        "lang": "en",
        "target_lang": "العربية",
        "features": ["native", "single-column"],
        "expected": "Small native control page; translated to Arabic.",
    },
    {
        "id": "arabic-rtl",
        "title": "Arabic RTL native page + Arabic/English mixed page (repo fixtures)",
        "source": "repo-merge:benchmark/vlm-migration/rtl.pdf+benchmark/paddle-smoke/arabic-mixed.pdf",
        "pages": [1, 2],
        "lang": "ar",
        "target_lang": "简体中文",
        "features": ["native", "rtl", "arabic"],
        "expected": (
            "Source is Arabic. The renderer must remove Arabic source text and place Chinese "
            "target text without leaving RTL source ghosts."
        ),
    },
]

#: Not part of the pass/fail denominator.  Degraded historical Arabic scans; the
#: renderer is only required to report honestly on them.
CHALLENGE = [
    {
        "id": "challenge-historical",
        "title": "1920s printed Arabic page with a rejected text layer",
        "source": "repo:benchmark/vlm-migration/historical.pdf",
        "pages": [1, 1],
        "lang": "ar",
        "target_lang": "简体中文",
        "features": ["scan", "degraded", "bad-text-layer"],
        "expected": (
            "The recorded text layer is rejected (reversed letters/words). Grounding may "
            "abstain; the page must then be reported as unfinished rather than erased."
        ),
    },
]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_bytes(spec: str) -> list[bytes]:
    if spec.startswith("repo-merge:"):
        return [(ROOT / part).read_bytes() for part in spec[len("repo-merge:"):].split("+")]
    if spec.startswith("repo:"):
        return [(ROOT / spec[len("repo:"):]).read_bytes()]
    if spec.startswith("public:"):
        cached = OUT / (spec.rsplit("/", 1)[-1] + ".src")
        if not cached.is_file():
            raise SystemExit(
                f"missing cached public source {cached}; re-fetch {spec} first")
        return [cached.read_bytes()]
    raise SystemExit(f"unknown source spec {spec!r}")


def _materialize(entry: dict) -> dict:
    start, end = entry["pages"]
    parts = _source_bytes(entry["source"])
    merged = entry["source"].startswith("repo-merge:")
    if merged:
        # A merge of separate documents: insert_pdf is the only option and the
        # merged sources carry no outline or links of their own.
        out = pymupdf.open()
        copied = []
        for part in parts:
            with pymupdf.open(stream=part, filetype="pdf") as pdf:
                for index in range(len(pdf)):
                    page_no = index + 1
                    if start <= page_no <= end:
                        out.insert_pdf(pdf, from_page=index, to_page=index,
                                       links=True, annots=True)
                        copied.append(page_no)
    else:
        # ``select`` keeps the outline and the in-document link annotations of
        # the pages we keep; building the subset with insert_pdf silently drops
        # both, which would make the link/bookmark acceptance vacuous.
        out = pymupdf.open(stream=parts[0], filetype="pdf")
        keep = [i for i in range(len(out)) if start <= i + 1 <= end]
        out.select(keep)
        copied = [i + 1 for i in keep]
    target = OUT / f"{entry['id']}.pdf"
    data = out.tobytes(garbage=0, deflate=True)
    out.close()
    target.write_bytes(data)

    pages = []
    with pymupdf.open(target) as doc:
        toc = doc.get_toc() or []
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            pages.append({
                "index": i,
                "page_id": f"p{i + 1}",
                "source_page": copied[i] if i < len(copied) else None,
                "width": round(page.rect.width, 3),
                "height": round(page.rect.height, 3),
                "rotation": page.rotation,
                "chars": len(text),
                "has_text_layer": len(text) > 20,
                "images": len(page.get_images(full=True)),
                "drawings": len(page.get_drawings()),
                "links": len(page.get_links()),
            })
    return {
        **{k: entry[k] for k in ("id", "title", "source", "lang", "target_lang",
                                 "features", "expected")},
        "pages": [start, end],
        "file": f"corpus/{entry['id']}.pdf",
        "sha256": _sha256(data),
        "bytes": len(data),
        "page_count": len(pages),
        "toc_entries": len(toc),
        "total_links": sum(p["links"] for p in pages),
        "page_detail": pages,
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for entry in CORPUS + CHALLENGE:
        if entry["source"].startswith("public:"):
            url = entry["source"][len("public:"):]
            cache = OUT / (url.rsplit("/", 1)[-1] + ".src")
            if not cache.is_file():
                import httpx
                response = httpx.get(url, follow_redirects=True, timeout=120,
                                     headers={"User-Agent": "Mozilla/5.0"},
                                     verify=False)
                response.raise_for_status()
                cache.write_bytes(response.content)
                print(f"fetched {url} -> {cache.name} ({len(response.content)} bytes)")

    samples = []
    for entry in CORPUS:
        record = _materialize(entry)
        samples.append(record)
        print(f"{record['id']:28s} {record['page_count']:3d}p "
              f"{record['bytes']:>9d}B  {record['features']}")
    challenge = []
    for entry in CHALLENGE:
        record = _materialize(entry)
        challenge.append(record)
        print(f"[challenge] {record['id']:18s} {record['page_count']:3d}p")

    total = sum(item["page_count"] for item in samples)
    manifest = {
        "version": 1,
        "frozen_at": "2026-10-07",
        "notes": (
            "The page set is fixed before any acceptance run. Pages may not be removed "
            "and the denominator may not change to improve the pass rate; a page that "
            "fails stays in the corpus and is reported."
        ),
        "acceptance_page_count": total,
        "challenge_page_count": sum(item["page_count"] for item in challenge),
        "samples": samples,
        "challenge": challenge,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"\nacceptance pages: {total} (>=24 required)")
    print(f"challenge pages: {manifest['challenge_page_count']}")
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 0 if total >= 24 else 1


if __name__ == "__main__":
    sys.exit(main())
