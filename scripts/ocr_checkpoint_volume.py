#!/usr/bin/env python3
"""Before/after write volume for the OCR durable checkpoint.

The previous implementation rewrote a single growing ``ocr_checkpoint.json``
containing **every page OCR'd so far** after each page completed.  That is an
O(n²) write pattern: for an n-page document the total bytes written are

    Σ_{i=1..n} size(pages[0..i])

The page-granular cache introduced in this round writes each page exactly once
plus a small manifest, i.e. O(n).  This script measures both numbers on the same
real Tesseract output so the comparison is not an estimate:

1. run the real extraction on a generated scanned fixture and read the bytes the
   new cache actually wrote (``ocr_cache_bytes_written``);
2. replay the *legacy* serialization on the identical per-page payloads and sum
   the sizes, reproducing what the old code would have written.

It also measures the quick-profile → full-run reuse: a 3-page preview followed
by a full run must OCR only the pages the preview did not already cover.

Run:

    python3 scripts/ocr_checkpoint_volume.py --out tmp/ocr-checkpoint-volume \
        --pages 20,120
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402
from pdf_ingestion_benchmark import _build_pdf  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402


LEGACY_VERSION = 1


def _legacy_bytes(pages: dict, *, source_hash: str, indices: list,
                  language: str) -> int:
    """Reproduce the old ``_save_ocr_checkpoint`` write volume exactly.

    The old code re-serialized the full accumulated ``pages`` mapping after every
    page, with ``indent=2`` and ``ensure_ascii=False``.
    """
    accumulated: dict = {}
    total = 0
    for index in indices:
        key = str(index)
        if key not in pages:
            continue
        accumulated[key] = pages[key]
        payload = {
            "version": LEGACY_VERSION,
            "source_sha256": source_hash,
            "indices": list(indices),
            "language": language,
            "pages": accumulated,
            "status": "running",
            "updated_at": "2026-01-01T00:00:00.000+00:00",
        }
        total += len(json.dumps(payload, ensure_ascii=False, indent=2)
                     .encode("utf-8"))
    return total


def _read_page_files(cache_dir: Path) -> dict:
    """Reconstruct the ``{str(index): record}`` mapping the old checkpoint held.

    Each page file wraps the OCR record in an envelope carrying its own identity
    key; only the inner ``record`` is the payload the legacy file stored.
    """
    pages_dir = cache_dir / "pages"
    pages = {}
    if not pages_dir.is_dir():
        return pages
    for path in sorted(pages_dir.glob("page-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        index = payload.get("page_index")
        record = payload.get("record")
        if index is None or not isinstance(record, dict):
            continue
        pages[str(index)] = record
    return pages


def _measure_pages(pages: int, workdir: Path) -> dict:
    pdf_bytes = _build_pdf(pages, scanned=True)
    checkpoint_dir = workdir / f"p{pages}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    profiler = PipelineProfiler(checkpoint_dir / "performance.json",
                                run_id=f"volume-{pages}")

    started = time.perf_counter()
    _paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        f"volume-{pages}.pdf", pdf_bytes, ocr_max_pages=None,
        profiler=profiler, checkpoint_dir=checkpoint_dir,
        ocr_workers=2, ocr_queue_size=4)
    wall = time.perf_counter() - started

    ocr = report.get("ocr") or {}
    cache_dir = Path(str(ocr.get("ocr_cache_dir") or ""))
    page_records = _read_page_files(cache_dir) if cache_dir else {}
    indices = sorted(int(key) for key in page_records)

    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    language = str(ocr.get("language") or "eng")
    legacy = _legacy_bytes(page_records, source_hash=source_hash,
                           indices=indices, language=language)
    new_bytes = int(ocr.get("ocr_cache_bytes_written") or 0)
    manifest_bytes = 0
    manifest_path = cache_dir / "manifest.json" if cache_dir else None
    if manifest_path and manifest_path.is_file():
        manifest_bytes = manifest_path.stat().st_size

    return {
        "pages": pages,
        "wall_time_s": round(wall, 3),
        "page_files": len(page_records),
        "page_order_ok": indices == list(range(pages)),
        "legacy_bytes_written": legacy,
        "new_bytes_written": new_bytes,
        "new_manifest_bytes": manifest_bytes,
        "new_page_bytes": max(0, new_bytes - manifest_bytes),
        "reduction_ratio": round(1 - new_bytes / legacy, 4) if legacy else 0.0,
        "legacy_MB": round(legacy / 1e6, 3),
        "new_MB": round(new_bytes / 1e6, 3),
    }


def _measure_quick_profile_reuse(pages: int, workdir: Path,
                                 *, separate_dirs: bool) -> dict:
    """Quick profile (3 representative pages) then the full run.

    Two layouts are measured because both must work:

    * ``separate_dirs=False`` — both runs share one checkpoint directory
      (the job-scoped case, e.g. a resumed task);
    * ``separate_dirs=True`` — the profile writes to the shared
      content-addressed profile cache and the full run keeps its own job
      directory, which is what the application actually does because profiling
      happens before a job exists.
    """
    pdf_bytes = _build_pdf(pages, scanned=True)
    label = "split" if separate_dirs else "shared"
    profile_dir = workdir / f"reuse-{pages}-{label}-profile"
    job_dir = workdir / f"reuse-{pages}-{label}-job"
    # In the shared layout both runs use the same checkpoint directory; in the
    # split layout the full run keeps its own job directory and reads the
    # profile cache as a secondary, read-only source.
    preview_checkpoint = profile_dir
    full_checkpoint = job_dir if separate_dirs else profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)

    preview = PipelineProfiler(profile_dir / "preview.json", run_id="preview")
    core.extract_document_paragraphs_with_report(
        f"reuse-{pages}.pdf", pdf_bytes, ocr_max_pages=3,
        profiler=preview, checkpoint_dir=preview_checkpoint,
        ocr_workers=2, ocr_queue_size=4)
    preview_ocr = preview.snapshot()["stages"].get("ocr") or {}
    preview_pages = int(preview_ocr.get("completed_page_count") or 0)
    preview_cached = int((preview_ocr.get("metadata") or {}).get("cached_pages") or 0)

    full = PipelineProfiler(full_checkpoint / "full.json", run_id="full")
    _paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        f"reuse-{pages}.pdf", pdf_bytes, ocr_max_pages=None,
        profiler=full, checkpoint_dir=full_checkpoint,
        profile_cache_dir=profile_dir if separate_dirs else None,
        ocr_workers=2, ocr_queue_size=4)
    full_ocr = full.snapshot()["stages"].get("ocr") or {}
    full_meta = full_ocr.get("metadata") or {}
    ocr = report.get("ocr") or {}

    return {
        "pages": pages,
        "layout": label,
        "preview_ocr_pages": preview_pages,
        "preview_cache_hits": preview_cached,
        "full_run_cached_pages": int(full_meta.get("cached_pages") or 0),
        "full_run_ocr_pages": int(full_ocr.get("completed_page_count") or 0),
        "full_run_page_order": ocr.get("page_order") or [],
        "page_order_complete": (ocr.get("page_order") or []) == \
            list(range(1, pages + 1)),
        "duplicate_pages": len(ocr.get("page_order") or []) !=
            len(set(ocr.get("page_order") or [])),
        "failed_pages": int(ocr.get("failed_pages") or 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tmp/ocr-checkpoint-volume")
    parser.add_argument("--pages", default="20,120")
    args = parser.parse_args()

    sizes = [int(value) for value in str(args.pages).split(",") if value.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    original_output = core.OUTPUT_DIR
    volume, reuse = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="ocr-volume-") as tmp:
            workdir = Path(tmp)
            for pages in sizes:
                volume.append(_measure_pages(pages, workdir))
            for pages in sizes:
                for separate in (False, True):
                    reuse.append(_measure_quick_profile_reuse(
                        pages, workdir, separate_dirs=separate))
    finally:
        core.OUTPUT_DIR = original_output

    report = {
        "benchmark": "ocr_checkpoint_volume",
        "run_token": uuid.uuid4().hex,
        "write_volume": volume,
        "quick_profile_reuse": reuse,
        "note": ("legacy_bytes_written replays the previous single-file "
                 "checkpoint serialization on the same page payloads; "
                 "new_bytes_written is measured from the real cache."),
    }
    (out_dir / "volume.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'pages':>6} {'legacy_MB':>10} {'new_MB':>8} {'reduction':>10} "
          f"{'files':>6}")
    print("-" * 46)
    for row in volume:
        print(f"{row['pages']:>6} {row['legacy_MB']:>10.3f} "
              f"{row['new_MB']:>8.3f} {row['reduction_ratio']:>9.1%} "
              f"{row['page_files']:>6}")
    print()
    for row in reuse:
        print(f"quick-profile reuse {row['pages']}p [{row['layout']}]: "
              f"preview OCR'd {row['preview_ocr_pages']} pages, full run reused "
              f"{row['full_run_cached_pages']} pages, OCR'd "
              f"{row['full_run_ocr_pages']} "
              f"(order_complete={row['page_order_complete']}, "
              f"duplicates={row['duplicate_pages']}, "
              f"failed={row['failed_pages']})")
    print(f"\nwrote {out_dir / 'volume.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
