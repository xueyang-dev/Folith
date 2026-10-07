"""Render the corpus with the original-layout renderer and check every page.

Usage::

    ./venv/bin/python benchmark/opdf/render_corpus.py --mode offline
    ./venv/bin/python benchmark/opdf/render_corpus.py --mode live --only scan-clean

Outputs, under ``benchmark/opdf/out/<mode>/``:

* ``<sample>.pdf``            the rendered document
* ``<sample>.report.json``    per-page placements, sizes and unfinished regions
* ``<sample>.verify.json``    structural per-page verification
* ``<sample>-p<NN>.before.png`` / ``-after.png`` page rasters for inspection
* ``summary.json``            the corpus verdict
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
JOBS = HERE / "jobs"

#: Declared before acceptance.  Never widened afterwards.
MIN_FONT_PT = 7.0
MIN_FONT_RATIO = 0.62
#: Share of a slot's characters that must be found in the output text layer.
TARGET_PRESENCE_MIN = 0.95
#: Raster difference allowed outside the planned placement boxes (halo 2pt).
OUTSIDE_HALO_MAX = 0.001

_WS = re.compile(r"\s+")


def squeeze(text: str) -> str:
    """Whitespace-free, compatibility-normalized form for comparisons.

    NFKC folds Arabic presentation forms (U+FE70..U+FEFF) back to base letters,
    so a source whose text layer is written in presentation forms can still be
    compared with the renderer's output, which is deliberately base characters.
    """
    return _WS.sub("", unicodedata.normalize("NFKC", text or ""))


def declared_floor(source_size_pt: float) -> float:
    """The readability floor declared before acceptance, per source size."""
    return max(min(MIN_FONT_PT, source_size_pt), source_size_pt * MIN_FONT_RATIO)


def _page_rasters(source_bytes, output_bytes, index, out: pathlib.Path, tag: str):
    import pymupdf
    for label, data in (("before", source_bytes), ("after", output_bytes)):
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            if 0 <= index < len(doc):
                doc[index].get_pixmap(dpi=110).save(str(out / f"{tag}-p{index + 1:02d}-{label}.png"))
        finally:
            doc.close()


#: RTL PDF text extraction is reader-dependent.  MuPDF reorders an Arabic line
#: into logical word order but reverses the two characters inside the lam-alef
#: ligature cluster; pypdf keeps the ligature but not the word order.  The
#: embedded ToUnicode mapping is correct (see the renderer tests).  This fallback
#: therefore checks the target's words, accepting the one documented cluster
#: swap, and reports the exact-substring rate separately so the gap stays
#: visible instead of being averaged away.
#: RTL PDF text extraction is reader-dependent.  MuPDF reorders an Arabic line
#: into logical word order but reverses the two characters inside a lam-alef
#: ligature cluster, moves a word-initial vowel sign, and sometimes merges the
#: space before a numeral.  The embedded ToUnicode mapping itself is correct
#: (see the renderer's own test), so word presence is checked on a canonical
#: form that removes those artifacts while still failing on a dropped,
#: truncated or replaced word.
_ALEFS = "\u0627\u0622\u0623\u0625"
_LIGATURE_PAIRS = tuple(("\u0644" + alef, alef + "\u0644") for alef in _ALEFS)
_IGNORED = set(" \t\n.,;:!?()[]{}\u060c\u061b\u061f\u00ab\u00bb\u2013\u2014-\u201c\u201d'\"")
_DIACRITICS = set(range(0x064B, 0x0653)) | {0x0653, 0x0654, 0x0655, 0x0670, 0x0640}


def _canonical(text: str) -> str:
    out = []
    for char in text or "":
        code = ord(char)
        if code in _DIACRITICS or char in _IGNORED:
            continue
        out.append(char)
    return "".join(out)


def _rtl_variants(word: str) -> set[str]:
    """A word plus the lam-alef cluster orders a reader may report."""
    variants = {word}
    for lam_alef, reversed_pair in _LIGATURE_PAIRS:
        if lam_alef in word:
            variants.add(word.replace(lam_alef, reversed_pair))
        if reversed_pair in word:
            variants.add(word.replace(reversed_pair, lam_alef))
    return variants


#: A word counts as present when some page window is this similar to it.
#: MuPDF duplicates a consonant next to a vowel sign and can move the sign, so
#: "تعمقًا" comes back as "تعمًقًقا"; requiring an exact substring would reject
#: text that is actually there, while a genuinely dropped word scores far below.
_WORD_SIMILARITY = 0.75


def _word_present(word: str, haystack: str, digits: str) -> bool:
    if not word:
        return True
    if any(variant in haystack for variant in _rtl_variants(word)):
        return True
    if re.fullmatch(r"[0-9%.,\u0660-\u0669]+", word):
        wanted = "".join(ch for ch in word if ch.isdigit())
        return not wanted or wanted in digits
    import difflib

    length = len(word)
    start = haystack.find(word[0])
    guards = 0
    while start >= 0 and guards < 400:
        guards += 1
        for width in (length, length + 1, length + 2, length - 1):
            if width <= 0:
                continue
            window = haystack[start:start + width]
            if not window:
                continue
            if difflib.SequenceMatcher(None, word, window).ratio() >= _WORD_SIMILARITY:
                return True
        start = haystack.find(word[0], start + 1)
    return False


def _rtl_word_presence(text: str, page_text: str) -> float:
    """Share of the target's words recoverable from the page's text layer."""
    haystack = _canonical(page_text)
    digits = "".join(ch for ch in haystack if ch.isdigit())
    words = [_canonical(w) for w in _WS.split(text or "")]
    words = [w for w in words if w]
    if not words:
        return 0.0
    hits = sum(1 for word in words if _word_present(word, haystack, digits))
    return hits / len(words)


def _text_lines(page):
    """Rendered lines with their boxes, for collision and overflow checks."""
    lines = []
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            text = squeeze("".join(str(s.get("text") or "")
                                   for s in line.get("spans") or []))
            if text:
                lines.append((text, [round(v, 2) for v in line["bbox"]]))
    return lines


def _inside(box, rect) -> bool:
    return (rect[0] >= box[0] - 1.0 and rect[1] >= box[1] - 1.0
            and rect[2] <= box[2] + 1.0 and rect[3] <= box[3] + 1.0)


def cross_block_line_collisions(page, boxes) -> list:
    """Rendered lines belonging to *different* placed boxes that collide.

    Line spacing inside one placed block is the typesetter's job and is already
    guarded by the measured-leading rule and the box-overflow check.  What the
    renderer must guarantee is that two separately placed blocks never overlap,
    so only cross-block pairs are counted.  A formula's sub- and superscript
    pieces are separate extracted lines inside one block by construction, in the
    source document as much as in the output, so counting them would be a false
    positive.
    """
    owned = []
    for text, rect in _text_lines(page):
        for index, box in enumerate(boxes):
            if _inside(box, rect):
                owned.append((index, text, rect))
                break
    found = []
    for i in range(len(owned)):
        for j in range(i + 1, len(owned)):
            if owned[i][0] == owned[j][0]:
                continue
            a, b = owned[i][2], owned[j][2]
            shared_w = min(a[2], b[2]) - max(a[0], b[0])
            shared_h = min(a[3], b[3]) - max(a[1], b[1])
            if shared_w > 0.5 and shared_h > 0.5:
                found.append({"a": owned[i][1][:30], "b": owned[j][1][:30],
                              "area": round(shared_w * shared_h, 2),
                              "boxes": [owned[i][0], owned[j][0]]})
                if len(found) >= 20:
                    return found
    return found


def box_overflow(page, placements):
    """Placed text that extends meaningfully past its own box.

    This is the "was anything truncated or squeezed out of its region" check:
    the rendered lines that belong to a box must stay inside it.
    """
    found = []
    lines = _text_lines(page)
    for placement in placements:
        box = placement.get("box")
        if not box:
            continue
        inside = [rect for _text, rect in lines if _inside(box, rect)]
        if not inside:
            continue
        bottom = max(rect[3] for rect in inside)
        right = max(rect[2] for rect in inside)
        # The rendered glyph box runs up to ~0.1em past the measured line box,
        # which the layout already reserves.  A *real* overflow is at least half
        # a line, because that is the smallest amount by which a wrapped line
        # can fail to fit; anything below that is measurement noise.
        size = max(4.0, float(placement.get("size_pt") or 10.0))
        vertical_slack = 0.5 * size
        # Horizontally the rendered glyph box can also stick out by ~0.15em,
        # while a real horizontal overflow is at least one character (~0.5em).
        horizontal_slack = 0.15 * size
        if bottom > box[3] + vertical_slack or right > box[2] + horizontal_slack:
            found.append({
                "segments": placement.get("segment_indexes"),
                "box": box,
                "text_bottom": round(bottom, 2),
                "text_right": round(right, 2),
            })
    return found


def _allowed_boxes(results) -> list:
    """Erased source rectangles plus the rectangles the new text was placed in."""
    boxes = []
    for result in results:
        for key in ("source_box", "box"):
            if result.get(key):
                boxes.append(result[key])
    return boxes


def outside_halo_diff(source_bytes, output_bytes, index, boxes, *,
                      dpi: int = 72, halo: float = 2.0):
    """Share of pixels that differ outside the erased and placed regions.

    The reference is the source page with exactly the replaced source lines
    removed, so anything the renderer changed elsewhere — a leftover glyph, a
    damaged rule, a moved image — shows up here.  Everything the placed text
    actually covers is allowed to differ, including the sliver of a line's
    glyph box that sticks out past the measured height.
    """
    import numpy as np
    import pymupdf

    reference = pymupdf.open(stream=source_bytes, filetype="pdf")
    output = pymupdf.open(stream=output_bytes, filetype="pdf")
    try:
        ref_page = reference[index]
        for box in boxes:
            for block in ref_page.get_text("dict").get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    rect = line["bbox"]
                    if (rect[0] >= box[0] - 1.5 and rect[1] >= box[1] - 1.5
                            and rect[2] <= box[2] + 1.5 and rect[3] <= box[3] + 1.5):
                        ref_page.add_redact_annot(pymupdf.Rect(rect), fill=None)
        ref_page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        ref_pix = ref_page.get_pixmap(dpi=dpi, alpha=False)
        out_pix = output[index].get_pixmap(dpi=dpi, alpha=False)
        scale = dpi / 72.0
        ref = np.frombuffer(ref_pix.samples, dtype=np.uint8).reshape(
            ref_pix.height, ref_pix.width, ref_pix.n)
        out = np.frombuffer(out_pix.samples, dtype=np.uint8).reshape(
            out_pix.height, out_pix.width, out_pix.n)
        height = min(ref.shape[0], out.shape[0])
        width = min(ref.shape[1], out.shape[1])
        ref, out = ref[:height, :width], out[:height, :width]

        allowed = list(boxes)
        for block in output[index].get_text("dict").get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                rect = line["bbox"]
                if any(rect[0] < b[2] + 1 and rect[2] > b[0] - 1
                       and rect[1] < b[3] + 1 and rect[3] > b[1] - 1
                       for b in boxes):
                    allowed.append([rect[0], rect[1], rect[2], rect[3]])

        mask = np.zeros((height, width), dtype=bool)
        for box in allowed:
            x0 = max(0, int((box[0] - halo) * scale))
            y0 = max(0, int((box[1] - halo) * scale))
            x1 = min(width, int((box[2] + halo) * scale) + 1)
            y1 = min(height, int((box[3] + halo) * scale) + 1)
            if x1 > x0 and y1 > y0:
                mask[y0:y1, x0:x1] = True
        differs = (np.abs(ref.astype(np.int16) - out.astype(np.int16)).max(axis=2) > 24)
        outside = ~mask
        total = int(outside.sum())
        if not total:
            return 0.0
        return round(float((differs & outside).sum()) / total, 6)
    finally:
        reference.close()
        output.close()


def _spans_in(page, rect) -> list[str]:
    out = []
    for block in page.get_text("dict").get("blocks") or []:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            box = line.get("bbox") or (0, 0, 0, 0)
            if (box[2] < rect[0] - 1.5 or box[0] > rect[2] + 1.5
                    or box[3] < rect[1] - 1.5 or box[1] > rect[3] + 1.5):
                continue
            for span in line.get("spans") or []:
                text = str(span.get("text") or "")
                if text.strip():
                    out.append(text)
    return out


def src_page_for_ink(source, index):
    return source[index]


def check_page(source_bytes, output_bytes, report, index,
               render_mode: str = "translated") -> dict:
    import pymupdf
    source = pymupdf.open(stream=source_bytes, filetype="pdf")
    output = pymupdf.open(stream=output_bytes, filetype="pdf")
    try:
        out_page = output[index]
        out_squeezed = squeeze(out_page.get_text())
        placements = []
        for page in report["pages"]:
            if page["index"] == index:
                placements = page.get("placements") or []
                page_meta = page
                break
        else:
            page_meta = {"index": index, "placed": 0, "sizes": [], "overflow": 0}

        out_spaced = _WS.sub(" ", out_page.get_text())
        results = []
        for placement in placements:
            text = placement.get("text") or ""
            needle = squeeze(text)
            exact = 1.0
            if not needle:
                found = 1.0
            else:
                exact = 1.0 if needle in out_squeezed else 0.0
                found = exact
                if not found:
                    # tolerate a wrap-induced split: measure the longest prefix found
                    longest = 0
                    for size in range(len(needle), max(0, len(needle) - 40), -1):
                        if needle[:size] in out_squeezed:
                            longest = size
                            break
                    found = longest / len(needle)
                # The prefix ratio is only a diagnostic for a wrap-split line;
                # for reordered scripts it is low even when every word is
                # present, so the word-level result must be able to win.
                found = max(found, _rtl_word_presence(text, out_spaced))
            results.append({
                "segments": placement.get("segment_indexes"),
                "present": round(found, 3),
                "exact": round(exact, 3),
                "size_pt": placement.get("size_pt"),
                "source_size_pt": placement.get("source_size_pt"),
                "box": placement.get("box"),
                "source_box": placement.get("source_box"),
                "overflow": placement.get("overflow"),
            })

        # A ghost is source text that was supposed to be replaced (its line sits
        # inside a replaced region) and is still extractable from the output.
        # Source lines outside every replaced region are deliberately kept (the
        # pair had no target text) and are not ghosts.
        # A ghost is source text still physically present inside a region we
        # replaced.  The check is done by clipping the output page to the source
        # line's own box, so an identical string somewhere else on the page
        # (e.g. "Google Brain" twice on the arXiv title page) cannot be mistaken
        # for a leftover.
        # In same-text rebuild mode the placed text *is* the source text, so
        # "leftover source" has no meaning; the meaningful check there is that
        # every source line is reproduced (target presence, below).
        ghosts = []
        replaced_boxes = [p.get("source_box") for p in placements]
        src_page = source[index]
        if render_mode == "same_text":
            replaced_boxes = []
        for block in src_page.get_text("dict").get("blocks") or []:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                raw = squeeze("".join(str(s.get("text") or "")
                                      for s in line.get("spans") or []))
                if len(raw) < 4:
                    continue
                rect = line["bbox"]
                if not any(
                        box and rect[0] >= box[0] - 1.5 and rect[1] >= box[1] - 1.5
                        and rect[2] <= box[2] + 1.5 and rect[3] <= box[3] + 1.5
                        for box in replaced_boxes):
                    continue
                # Text physically inside this source line's box in the output.
                # Newly placed target text is expected there; only text that
                # still matches the *source* wording is a leftover.
                targets = [squeeze(p.get("text") or "")
                           for p in placements
                           if p.get("source_box")
                           and rect[0] >= p["source_box"][0] - 1.5
                           and rect[1] >= p["source_box"][1] - 1.5
                           and rect[2] <= p["source_box"][2] + 1.5
                           and rect[3] <= p["source_box"][3] + 1.5]
                for span in ([] if render_mode == "same_text" else _spans_in(out_page, rect)):
                    piece = squeeze(span)
                    if len(piece) < 3:
                        continue
                    if piece not in raw:
                        continue
                    if any(piece in target for target in targets):
                        continue
                    ghosts.append({"text": piece[:70], "source": raw[:70],
                                   "rect": [round(v, 2) for v in rect]})
                    break

        unresolved_on_page = []
        for entry in report.get("unfinished") or []:
            if entry.get("page_index") == index and entry.get("reason") != "overflow":
                unresolved_on_page.append(entry)
        untranslated_on_page = [entry for entry in report.get("untranslated") or []
                                if entry.get("page_index") == index]
        # No two placed boxes may intersect.  This is measured on the boxes the
        # renderer actually placed, so a page that looks overlapped cannot pass.
        overlaps = []
        boxes = [r["box"] for r in results if r.get("box")]
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                shared_w = min(a[2], b[2]) - max(a[0], b[0])
                shared_h = min(a[3], b[3]) - max(a[1], b[1])
                if shared_w > 0.5 and shared_h > 0.5:
                    overlaps.append({"a": i, "b": j,
                                     "area": round(shared_w * shared_h, 2),
                                     "boxes": [a, b]})

        placed_boxes = [r["box"] for r in results if r.get("box")]
        ink_overlaps = cross_block_line_collisions(out_page, placed_boxes)
        overflows = box_overflow(out_page, results)

        exacts = [r["exact"] for r in results if r.get("exact") is not None]
        missing_targets = [r for r in results if r["present"] < TARGET_PRESENCE_MIN]
        sizes = [r["size_pt"] for r in results if r["size_pt"]]
        ratios = [r["size_pt"] / r["source_size_pt"] for r in results
                  if r["size_pt"] and r["source_size_pt"]]
        # Same declared rule the renderer used: never below 62% of the source
        # size, and never below 7pt unless the source itself was smaller.
        below_floor = [r for r in results
                       if r["size_pt"] and r["source_size_pt"]
                       and r["size_pt"] < declared_floor(r["source_size_pt"]) - 1e-6]
        return {
            "index": index,
            "placements": len(results),
            "target_missing": missing_targets,
            "ghosts": ghosts,
            "min_size": min(sizes) if sizes else None,
            "min_ratio": round(min(ratios), 3) if ratios else None,
            "exact_presence": round(sum(exacts) / len(exacts), 3) if exacts else None,
            "below_floor": below_floor,
            "overflow": page_meta.get("overflow", 0),
            "overlaps": overlaps,
            "ink_overlaps": ink_overlaps,
            "box_overflow": overflows,
            "outside_halo_diff": outside_halo_diff(
                source_bytes, output_bytes, index, _allowed_boxes(results)),
            "unresolved": unresolved_on_page,
            "untranslated": untranslated_on_page,
            "scan_page": page_meta.get("scan_page", False),
            # A page needs no manual layout work only when nothing was
            # truncated, nothing was left behind, no size fell under the
            # declared floor and the renderer did not have to report an
            # overflow for it.
            "pass": (not missing_targets and not ghosts and not below_floor
                     and (min(ratios) if ratios else 1.0) >= MIN_FONT_RATIO - 1e-6
                     and not page_meta.get("overflow")
                     and not unresolved_on_page
                     and not overlaps and not ink_overlaps and not overflows
                     and outside_halo_diff(
                         source_bytes, output_bytes, index,
                         _allowed_boxes(results)) <= OUTSIDE_HALO_MAX),
        }
    finally:
        source.close()
        output.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="offline",
                        help="job namespace: offline / live, or any other "
                             "suffix used when the job was built")
    parser.add_argument("--only", default="")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--render-mode", choices=("translated", "same_text"),
                        default="translated")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--include-challenge", action="store_true")
    args = parser.parse_args()

    import core
    core.OUTPUT_DIR = JOBS
    from folith import pdfrender

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    samples = list(manifest["samples"])
    if args.include_challenge:
        samples += list(manifest["challenge"])
    wanted = {s for s in args.only.split(",") if s}

    out_root = HERE / "out" / args.mode
    if args.render_mode != "translated":
        out_root = out_root / args.render_mode
    out_root.mkdir(parents=True, exist_ok=True)

    summary = {"mode": args.mode, "render_mode": args.render_mode,
               "generated_at": time.time(),
               "min_font_pt": MIN_FONT_PT, "min_font_ratio": MIN_FONT_RATIO,
               "target_presence_min": TARGET_PRESENCE_MIN,
               "samples": [], "totals": {"pages": 0, "pass": 0, "fail": 0,
                                          "unfinished": 0, "overflow": 0}}
    for sample in samples:
        if wanted and sample["id"] not in wanted:
            continue
        job_id = f"opdf-{args.mode}-{sample['id']}"
        if not (JOBS / job_id / "state.json").is_file():
            print(f"{sample['id']:26s} no job built for mode {args.mode} - skipped")
            summary["samples"].append({"id": sample["id"], "skipped": "no job"})
            continue
        source_bytes = (HERE / sample["file"]).read_bytes()
        cache_dir = JOBS / job_id / "original_pdf_cache"
        started = time.time()
        try:
            result = pdfrender.render_original_layout(
                job_id, source_bytes=source_bytes, mode=args.render_mode,
                page_cache_dir=cache_dir, use_cache=not args.no_cache)
        except Exception as exc:
            summary["samples"].append({"id": sample["id"], "error":
                                       f"{type(exc).__name__}: {exc}"})
            print(f"{sample['id']:26s} RENDER FAILED {type(exc).__name__}: {exc}")
            continue
        elapsed = time.time() - started

        pdf_path = out_root / f"{sample['id']}.pdf"
        pdf_path.write_bytes(result.data)
        (out_root / f"{sample['id']}.report.json").write_text(
            json.dumps({k: v for k, v in result.report.items()
                        if not k.startswith("_")},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        pages = []
        for index in range(sample["page_count"]):
            record = check_page(source_bytes, result.data, result.report, index,
                                args.render_mode)
            pages.append(record)
            if not args.no_images:
                _page_rasters(source_bytes, result.data, index, out_root,
                              f"{sample['id']}-{args.mode}")
        (out_root / f"{sample['id']}.verify.json").write_text(
            json.dumps(pages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        passed = sum(1 for p in pages if p["pass"])
        failed = [p["index"] for p in pages if not p["pass"]]
        entry = {
            "id": sample["id"], "job_id": job_id, "pages": len(pages),
            "pass": passed, "fail": len(failed), "failed_pages": failed,
            "unfinished": result.report["totals"]["unfinished"],
            "overflow": result.report["totals"]["overflow"],
            "unresolved_items": result.report["totals"]["unresolved_items"],
            "missing_glyph_slots": result.report["totals"]["missing_glyph_slots"],
            "untranslated": result.report["totals"].get("untranslated", 0),
            "links_before": result.report["totals"]["links_before"],
            "links_after": result.report["totals"]["links_after"],
            "toc_before": result.report["totals"]["toc_before"],
            "toc_after": result.report["totals"]["toc_after"],
            "geometry_preserved": result.report["totals"]["page_geometry_preserved"],
            "seconds": round(elapsed, 2),
            "pdf_bytes": len(result.data),
        }
        summary["samples"].append(entry)
        summary["totals"]["pages"] += len(pages)
        summary["totals"]["pass"] += passed
        summary["totals"]["fail"] += len(failed)
        summary["totals"]["unfinished"] += entry["unfinished"]
        summary["totals"]["overflow"] += entry["overflow"]
        print(f"{sample['id']:26s} {passed}/{len(pages)} pages pass  "
              f"unfinished={entry['unfinished']} overflow={entry['overflow']} "
              f"links {entry['links_before']}->{entry['links_after']} "
              f"toc {entry['toc_before']}->{entry['toc_after']} {elapsed:.2f}s")

    total = summary["totals"]["pages"]
    summary["totals"]["pass_rate"] = round(
        summary["totals"]["pass"] / total, 4) if total else 0.0
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\npages {summary['totals']['pass']}/{total} pass "
          f"({summary['totals']['pass_rate'] * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
