"""Real three-source OCR verification on a small, fixed page set.

What this measures
------------------
The same four pages of the Arabic scanned book are recognised through the
production pipeline (``core._ocr_pdf_text_with_warnings``) once per source:

* ``tesseract`` — the local Tesseract build with the ``tessdata_best`` Arabic
  pack (``TESSDATA_PREFIX`` must point at ``benchmark/tessdata_best``);
* ``paddle`` — the local PP-OCRv5 adapter with the task-level selection of the
  book-specific Arabic model (``benchmark/paddle-models/alam-al-din-finetuned``);
* ``online`` — the OpenAI-compatible image transcription backend against the
  configured OpenCode Go connection.

Accuracy reuses the **fixed reference fragments** already published in
``benchmark/ocr-vision-pilot/dataset.json`` (never sent to any model). For the
two local sources the fragment is cut out of the full-page result by the same
pixel window the pilot used. The online source returns no geometry, so its
fragment CER is measured on a region crop of exactly the same window — the only
way to compare like with like — and its full-page run is used for the
completeness / reading-order / usage record.

Request budget: at most 4 distinct pages and at most 8 recognition requests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))
import bench_lib as B  # noqa: E402

OUT = ROOT / "benchmark/ocr-sources-verification"
SCRATCH = ROOT / ".audit/ocr-sources-verification"
PILOT = ROOT / "benchmark/ocr-vision-pilot"
BOOK_MODEL = "alam-al-din-finetuned"
DEFAULT_PAGES = (5, 120, 220, 300)
ONLINE_MODEL = os.environ.get("FOLITH_OCR_ONLINE_MODEL", "deepseek-v4-flash-vision-exp")
CREDENTIAL_SOURCE = "translation_profile"
REASONING_OVERRIDE = ""


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_pdf() -> Path:
    explicit = os.environ.get("FOLITH_BENCHMARK_PDF")
    candidates = [Path(explicit).expanduser()] if explicit else []
    candidates.append(Path("/Users/xueyang/Downloads/Alam_al-din_(IA_alamaldin00alimuoft).pdf"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("Book PDF not found; set FOLITH_BENCHMARK_PDF")


def subset_pdf(source: Path, pages) -> bytes:
    """Deterministic page subset.

    The bytes are cached on disk because ``Document.tobytes()`` embeds a fresh
    file id and timestamps: rebuilding it on every run would change the OCR
    source hash and silently invalidate the page cache (and, for the online
    source, spend the same requests again).
    """
    import pymupdf
    cache = SCRATCH / f"subset-{'-'.join(str(page) for page in pages)}.pdf"
    if cache.is_file():
        return cache.read_bytes()
    cache.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as document:
        subset = pymupdf.open()
        try:
            for page in pages:
                subset.insert_pdf(document, from_page=page, to_page=page)
            subset.set_metadata({})
            payload = subset.tobytes(garbage=4, deflate=True)
        finally:
            subset.close()
    cache.write_bytes(payload)
    return payload


def reference_pages():
    dataset = json.loads((PILOT / "dataset.json").read_text(encoding="utf-8"))
    return {int(page["page_index"]): page for page in dataset["pages"]}


def region_text(record, window, dpi=350):
    """Cut the reference window out of retained line evidence."""
    x0, y0, x1, y1 = window
    pieces = []
    for block in record.get("blocks") or []:
        for line in block.get("lines") or []:
            if not line.get("geometry_known", True):
                continue
            centre_y = (float(line["top"]) + float(line["bottom"])) / 2
            centre_x = (float(line["left"]) + float(line["right"])) / 2
            if y0 <= centre_y <= y1 and x0 <= centre_x <= x1:
                pieces.append(str(line["text"]))
    return "\n".join(pieces)


def load_page_records(details, checkpoint_dir):
    from folith.ocr_cache import OCRPageCache
    identity = dict(details.get("cache_identity") or {})
    identity.pop("page_index", None)
    cache = OCRPageCache(Path(checkpoint_dir) / "ocr_cache")
    return cache, identity


def online_config(credential_source="translation_profile"):
    import core
    config, error = core.resolve_ocr_online_config({"credential_source": credential_source})
    if error is not None:
        raise SystemExit(error["message"])
    if config is None or config.unavailable_reason():
        raise SystemExit(config.unavailable_reason() if config else "online OCR not configured")
    from dataclasses import replace
    overrides = {"model": ONLINE_MODEL}
    if REASONING_OVERRIDE:
        overrides["reasoning_mode"] = REASONING_OVERRIDE
    return replace(config, **overrides).normalized()


def read_existing(source):
    path = OUT / f"results-{source}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_pipeline(source, pages, checkpoint_dir):
    import core
    kwargs = {}
    if source == "paddle":
        kwargs["paddle_model"] = BOOK_MODEL
    elif source == "online":
        kwargs["online_config"] = online_config(CREDENTIAL_SOURCE)
    started = time.perf_counter()
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        subset_pdf(source_pdf(), pages), backend=source, ocr_language="ara",
        dpi=350, checkpoint_dir=checkpoint_dir, workers=1, queue_size=1,
        return_details=True, **kwargs)
    return {
        "seconds": time.perf_counter() - started,
        "text": text,
        "warnings": list(warnings),
        "details": details,
    }


def online_region_crop(page_index, window):
    import pymupdf
    from PIL import Image
    import io
    with pymupdf.open(source_pdf()) as document:
        clip = pymupdf.Rect(*[value * 72.0 / 350.0 for value in window])
        pix = document[page_index].get_pixmap(dpi=350, clip=clip, alpha=False)
        return pix.tobytes("png")


def run_online_crop(page_index, window, reasoning_mode=None):
    global REASONING_OVERRIDE
    from folith.ocr_online import OnlineOCRBackend
    if reasoning_mode:
        REASONING_OVERRIDE = reasoning_mode
    backend = OnlineOCRBackend(online_config(CREDENTIAL_SOURCE))
    started = time.perf_counter()
    blocks, error = backend.ocr_page(online_region_crop(page_index, window),
                                     page_index=page_index, page_number=page_index + 1,
                                     language="ara", dpi=350)
    return {
        "seconds": time.perf_counter() - started,
        "text": "\n".join(block.text for block in blocks),
        "error": error,
        "request": dict(backend._last_request),
    }


INSPECTION_PAGE = int(os.environ.get("FOLITH_OCR_INSPECTION_PAGE", "120"))


def normalize(value):
    return B.normalize(str(value or ""))


def inspect_full_page(result, references):
    """Completeness and reading order on one whole page.

    Checks the published reference fragment against the *whole-page* output:
    every reference line must appear, in the published order, inside the
    transcription.  This is weaker than a full-page ground truth (there is
    none), but it does catch dropped pages, reordered blocks and truncated
    output on a page nobody else has to re-read.
    """
    from difflib import SequenceMatcher
    entry = next((item for item in result["page_results"]
                  if item["page_index"] == INSPECTION_PAGE), None)
    if entry is None or not entry.get("full_page_text"):
        return {}
    page_lines = [normalize(line) for line in (entry.get("full_page_lines")
                                               or entry["full_page_text"].splitlines())
                  if normalize(line)]
    matched, missing, order = [], [], []
    for line in references[INSPECTION_PAGE]["reference_lines"]:
        needle = normalize(line)
        best_index, best_ratio = -1, 0.0
        for index, candidate in enumerate(page_lines):
            ratio = SequenceMatcher(None, needle, candidate).ratio()
            if ratio > best_ratio:
                best_index, best_ratio = index, ratio
        if best_ratio >= 0.7:
            matched.append({"reference": line, "page_line_index": best_index,
                            "ratio": round(best_ratio, 4)})
            order.append(best_index)
        else:
            missing.append({"reference": line, "best_ratio": round(best_ratio, 4),
                            "best_page_line": page_lines[best_index] if best_index >= 0 else ""})
    return {
        "page_index": INSPECTION_PAGE,
        "pdf_page": INSPECTION_PAGE + 1,
        "full_page_line_count": entry["full_page_line_count"],
        "reference_lines": len(references[INSPECTION_PAGE]["reference_lines"]),
        "reference_lines_matched": len(matched),
        "reference_lines_missing": missing,
        "reference_order_preserved": order == sorted(order),
        "full_page_characters": len(entry["full_page_text"]),
    }


def summarize(online_page=None):
    results = {}
    for source in ("tesseract", "paddle", "online"):
        path = OUT / f"results-{source}.json"
        if path.is_file():
            results[source] = json.loads(path.read_text(encoding="utf-8"))
    if not results:
        raise SystemExit("No result files yet; run the verifications first")
    references = reference_pages()
    summary = {"sources": {}, "requests": 0}
    for source, result in results.items():
        entry = {
            "actual_engine": result.get("actual_engine"),
            "model": result.get("model"),
            "requests": result.get("requests", 0),
            "wall_seconds": result.get("wall_seconds"),
            "completed_pages": result.get("completed_pages"),
            "failed_pages": result.get("failed_pages"),
            "summary": result.get("summary"),
        }
        summary["sources"][source] = entry
        summary["requests"] += int(result.get("requests") or 0)
    for source, result in results.items():
        summary[f"{source}_full_page_inspection"] = inspect_full_page(result, references)
    if "online" in results:
        # Score the provider-default probe on the same fixed fragment.
        for item in results["online"]["page_results"]:
            probe = item.get("reasoning_probe")
            if not probe or not probe.get("text"):
                continue
            summary.setdefault("online_reasoning_probe", {
                "page_index": item["page_index"],
                "reasoning_mode": probe["reasoning_mode"],
                "error": probe.get("error"),
                "finish_reason": (probe.get("request") or {}).get("finish_reason"),
                "usage": (probe.get("request") or {}).get("usage"),
                "character_errors": B.cer(item["reference"], probe["text"]),
                "word_errors": B.wer(item["reference"], probe["text"]),
            })
    write(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("saved", OUT / "summary.json")


def main():
    global CREDENTIAL_SOURCE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("tesseract", "paddle", "online", "summarize"))
    parser.add_argument("--pages", default=",".join(str(page) for page in DEFAULT_PAGES),
                        help="0-indexed PDF pages; at most 4")
    parser.add_argument("--credential-source", default=CREDENTIAL_SOURCE,
                        choices=("ocr_profile", "translation_profile"))
    parser.add_argument("--reasoning-mode", default="",
                        choices=("", "provider_default", "thinking_disabled"),
                        help="explicit thinking control for the online source")
    parser.add_argument("--crop-pages", default="",
                        help="pages that also get one reference-window crop request "
                             "(online only; keeps the total at or below 8)")
    parser.add_argument("--probe-reasoning-page", type=int, default=None,
                        help="one extra crop request on this page with the opposite "
                             "thinking setting, to record the default-reasoning behaviour")
    args = parser.parse_args()
    global REASONING_OVERRIDE
    if args.source == "summarize":
        summarize()
        return
    CREDENTIAL_SOURCE = args.credential_source
    REASONING_OVERRIDE = args.reasoning_mode
    crop_pages = {int(value) for value in args.crop_pages.split(",") if value.strip()}
    pages = [int(value) for value in args.pages.split(",") if value.strip()]
    if len(pages) > 4:
        raise SystemExit("At most 4 distinct pages are allowed for this verification")

    references = reference_pages()
    missing = [page for page in pages if page not in references]
    if missing:
        raise SystemExit(f"No fixed reference fragment for pages {missing}")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = SCRATCH / f"cache-{args.source}"
    run = run_pipeline(args.source, pages, checkpoint_dir)
    details = run["details"]
    cache, identity = load_page_records(details, checkpoint_dir)

    pages_out = []
    requests = 0
    # Count only requests actually issued in this run: a page served from the
    # page cache performs no request.
    if args.source == "online":
        requests += len(details.get("ocr_page_requests") or {})
    for position, page_index in enumerate(pages):
        reference = "\n".join(references[page_index]["reference_lines"])
        records, _manifest = cache.load(indices=[position], **identity)
        record = records.get(str(position)) or {}
        lines = [line for block in record.get("blocks") or []
                 for line in (block.get("lines") or [])]
        # Logical line texts, engine-agnostic: the retained per-line evidence when
        # the engine provides it, otherwise the block text split on newlines.
        line_texts = [str(line.get("text") or "").strip() for line in lines
                      if str(line.get("text") or "").strip()]
        if not line_texts:
            line_texts = [piece.strip()
                          for block in record.get("blocks") or []
                          for piece in str(block.get("text") or "").split("\n")
                          if piece.strip()]
        entry = {
            "page_index": page_index,
            "pdf_page": page_index + 1,
            "reference": reference,
            "full_page_line_count": len(lines),
            "full_page_lines": line_texts,
            "full_page_text": record.get("normalized_text") or "",
            "cohort": references[page_index].get("cohort"),
            "window": references[page_index]["crop_bbox_pixels"],
        }
        if args.source == "online":
            entry["region_text"] = None
            entry["region_source"] = "pending crop request"
        else:
            entry["region_text"] = region_text(record, references[page_index]["crop_bbox_pixels"])
            entry["region_source"] = "full-page evidence cut by the pilot window"
        pages_out.append(entry)

    existing = read_existing(args.source)
    existing_pages = {int(item["page_index"]): item
                      for item in (existing or {}).get("page_results", [])}
    active_reasoning = REASONING_OVERRIDE or "provider_default"
    if args.source == "online" and crop_pages:
        for entry in pages_out:
            if entry["page_index"] not in crop_pages:
                continue
            previous = existing_pages.get(entry["page_index"]) or {}
            previous_request = previous.get("region_request") or {}
            if (previous.get("region_text")
                    and previous_request.get("reasoning_mode") == active_reasoning):
                # Already paid for, with the same window and the same request
                # settings: keep it instead of spending another request.
                for key in ("region_text", "region_source", "region_seconds",
                            "region_error", "region_request"):
                    entry[key] = previous.get(key)
                continue
            crop = run_online_crop(entry["page_index"], entry["window"])
            requests += 1
            entry["region_text"] = crop["text"]
            entry["region_source"] = "dedicated region crop (same window as the pilot)"
            entry["region_seconds"] = crop["seconds"]
            entry["region_error"] = crop["error"]
            entry["region_request"] = crop["request"]
        if args.probe_reasoning_page is not None:
            probe_page = int(args.probe_reasoning_page)
            for entry in pages_out:
                if entry["page_index"] != probe_page:
                    continue
                probe_mode = ("provider_default"
                              if REASONING_OVERRIDE == "thinking_disabled"
                              else "thinking_disabled")
                previous = (existing_pages.get(probe_page) or {}).get("reasoning_probe") or {}
                if previous.get("reasoning_mode") == probe_mode and previous.get("text"):
                    entry["reasoning_probe"] = previous
                    continue
                crop = run_online_crop(probe_page, entry["window"], probe_mode)
                requests += 1
                entry["reasoning_probe"] = {
                    "reasoning_mode": probe_mode,
                    "text": crop["text"],
                    "error": crop["error"],
                    "seconds": crop["seconds"],
                    "request": crop["request"],
                }
            REASONING_OVERRIDE = args.reasoning_mode

    for entry in pages_out:
        if not entry.get("region_text"):
            continue
        entry["character_errors"] = B.cer(entry["reference"], entry["region_text"])
        entry["word_errors"] = B.wer(entry["reference"], entry["region_text"])

    scored = [entry for entry in pages_out if entry.get("character_errors")]
    summary = {}
    if scored:
        characters = {key: sum(entry["character_errors"][key] for entry in scored)
                      for key in ("sub", "del", "ins", "ref_len")}
        words = {key: sum(entry["word_errors"][key] for entry in scored)
                 for key in ("sub", "del", "ins", "ref_words")}
        summary = {
            "pages": len(scored),
            "cer": (characters["sub"] + characters["del"] + characters["ins"]) / characters["ref_len"],
            "wer": (words["sub"] + words["del"] + words["ins"]) / words["ref_words"],
            "character_errors": characters,
            "word_errors": words,
        }

    result = {
        "source": args.source,
        "model": (details.get("model"),
                  details.get("model_id") or details.get("model_label")),
        "actual_engine": details.get("actual_engine"),
        "pages": pages,
        "requests": requests,
        "reasoning_mode": REASONING_OVERRIDE or "provider_default",
        "wall_seconds": run["seconds"],
        "all_pages_completed": details.get("all_pages_completed"),
        "completed_pages": details.get("completed_pages"),
        "failed_pages": details.get("failed_pages"),
        "page_failures": details.get("page_failures"),
        "ocr_usage": details.get("ocr_usage"),
        "ocr_usage_unknown_pages": details.get("ocr_usage_unknown_pages"),
        "ocr_retried_requests": details.get("ocr_retried_requests"),
        "pages_without_geometry": details.get("ocr_pages_without_geometry"),
        "warnings": run["warnings"],
        "summary": summary,
        "page_results": pages_out,
    }
    write(OUT / f"results-{args.source}.json", result)
    print(json.dumps({key: result[key] for key in
                      ("source", "actual_engine", "model", "requests", "wall_seconds",
                       "completed_pages", "failed_pages", "ocr_usage", "summary")},
                     ensure_ascii=False, indent=2))
    for entry in pages_out:
        print(f"PDF page {entry['pdf_page']}: lines={entry['full_page_line_count']} "
              f"CER={((entry.get('character_errors') or {}).get('cer'))}")
    print("saved", OUT / f"results-{args.source}.json")


if __name__ == "__main__":
    main()
