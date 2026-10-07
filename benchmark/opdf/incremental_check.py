"""Incremental-update evidence for the original-layout renderer.

Proves the three things the phase requires:

1. editing one translation re-renders only the pages that contain it, without
   re-running extraction or translation (the LLM entry point is poisoned for the
   duration, so any model call would fail loudly);
2. an unrelated page's rendered bytes are unchanged;
3. editing the *source* text goes through the existing dependency mechanism
   (`core.mutate_translation_segments` -> `_mark_translation_truth_changed`) and
   then updates only the affected page.

Usage::

    ./venv/bin/python benchmark/opdf/incremental_check.py --mode offline \\
        --sample book-fighter-pilot
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
JOBS = HERE / "jobs"


def page_hashes(data: bytes) -> list[str]:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [hashlib.sha256(doc[i].get_pixmap(dpi=60).tobytes("png")).hexdigest()[:16]
                for i in range(len(doc))]
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="offline")
    parser.add_argument("--sample", default="book-fighter-pilot")
    parser.add_argument("--index", type=int, default=None,
                        help="pair index to edit (default: first placed pair)")
    args = parser.parse_args()

    import core
    core.OUTPUT_DIR = JOBS
    from folith import pdfrender

    job_id = f"opdf-{args.mode}-{args.sample}"
    state = core.load_job_state(job_id)
    if not state:
        raise SystemExit(f"no job {job_id}; run build_jobs.py first")
    source_bytes = core.load_source(job_id)
    cache_dir = JOBS / job_id / "original_pdf_cache"

    # Any model call during this script is a failure: the point is that a
    # translation edit needs no re-extraction and no re-translation.
    real_call_llm = core.call_llm
    calls: list[str] = []

    def poisoned(*a, **k):
        calls.append("call_llm")
        raise AssertionError("the renderer must not call the model")

    record: dict = {"job_id": job_id, "sample": args.sample, "mode": args.mode}

    first = pdfrender.render_original_layout(
        job_id, state, source_bytes, page_cache_dir=cache_dir, use_cache=True)
    first_hashes = page_hashes(first.data)
    record["first"] = {
        "pages": len(first_hashes),
        "cache_hits": first.report["totals"]["page_cache_hits"],
        "cache_misses": first.report["totals"]["page_cache_misses"],
        "bytes": len(first.data),
    }

    # Pick a segment that actually landed on a page.
    candidates = []
    for page in first.report["pages"]:
        for placement in page.get("placements") or []:
            if placement.get("segment_indexes"):
                candidates.append((page["index"], placement["segment_indexes"][0]))
    if not candidates:
        raise SystemExit("nothing was placed; cannot test incremental update")
    page_index, segment_index = (
        (args.index, args.index) if args.index is not None else candidates[0])
    if args.index is not None:
        page_index = next((p for p, s in candidates if s == args.index), candidates[0][0])
    record["edited_segment"] = segment_index
    record["expected_page"] = page_index

    # --- 1. edit one translation -----------------------------------------
    core.call_llm = poisoned
    try:
        core.save_translation_edit(
            job_id, segment_index,
            "【人工修订】这一句译文由验收脚本改写，用于验证局部更新。")
        state = core.load_job_state(job_id)
        second = pdfrender.render_original_layout(
            job_id, state, source_bytes, page_cache_dir=cache_dir, use_cache=True)
    finally:
        core.call_llm = real_call_llm
    second_hashes = page_hashes(second.data)
    changed = [i for i, (a, b) in enumerate(zip(first_hashes, second_hashes)) if a != b]
    record["translation_edit"] = {
        "llm_calls": len(calls),
        "changed_pages": changed,
        "cache_hits": second.report["totals"]["page_cache_hits"],
        "cache_misses": second.report["totals"]["page_cache_misses"],
        "unchanged_pages": len(second_hashes) - len(changed),
    }

    # --- 2. edit the source text ------------------------------------------
    target = core.load_job_state(job_id)
    original_source = str((target.get("pairs") or [])[segment_index].get("source") or "")
    core.call_llm = poisoned
    try:
        core.mutate_translation_segments(
            job_id, segment_index, "source_edit",
            source_text=original_source + " [来源已修订]")
        state = core.load_job_state(job_id)
        edited_pair = (state.get("pairs") or [])[segment_index]
        # The existing dependency mechanism must have marked the pair stale.
        stale_marked = bool(edited_pair.get("source_human_edited")) and \
            edited_pair.get("review_status") == "not_reviewed"
        # A source change is visible on the page wherever the *source* is the
        # rendered text (same-text rebuild), which proves the page is bound to
        # the source rather than to a stale copy of it.
        same_text = pdfrender.render_original_layout(
            job_id, state, source_bytes, mode="same_text",
            page_cache_dir=JOBS / job_id / "original_pdf_cache_same_text",
            use_cache=True)
    finally:
        core.call_llm = real_call_llm
    record["source_edit"] = {
        "llm_calls": len(calls) - record["translation_edit"]["llm_calls"],
        "stale_marked": stale_marked,
        "source_in_same_text_report": any(
            "[来源已修订]" in (placement.get("text") or "")
            for page in same_text.report["pages"]
            for placement in page.get("placements") or []),
    }

    # --- 2b. apply the recomputed translation, then re-render --------------
    core.call_llm = poisoned
    try:
        core.save_translation_edit(
            job_id, segment_index,
            "【重算后译文】来源已修订，译文随依赖机制重新生成。")
        state = core.load_job_state(job_id)
        fourth = pdfrender.render_original_layout(
            job_id, state, source_bytes, page_cache_dir=cache_dir, use_cache=True)
    finally:
        core.call_llm = real_call_llm
    fourth_hashes = page_hashes(fourth.data)
    changed_recomputed = [i for i, (a, b) in enumerate(zip(second_hashes, fourth_hashes))
                          if a != b]
    record["source_edit"]["recomputed_changed_pages"] = changed_recomputed
    record["source_edit"]["cache_hits"] = fourth.report["totals"]["page_cache_hits"]
    record["source_edit"]["cache_misses"] = fourth.report["totals"]["page_cache_misses"]

    # --- 3. restore --------------------------------------------------------
    core.mutate_translation_segments(
        job_id, segment_index, "source_edit", source_text=original_source)
    core.save_translation_edit(
        job_id, segment_index, str((core.load_job_state(job_id).get("pairs") or [])
                                   [segment_index].get("initial_target") or ""))

    record["generated_at"] = time.time()
    out = HERE / "out" / args.mode / f"{args.sample}.incremental.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    edit = record["translation_edit"]
    source = record["source_edit"]
    ok = (edit["llm_calls"] == 0 and changed == [page_index]
          and second.report["totals"]["page_cache_misses"] == 1
          and source["llm_calls"] == 0
          and source["stale_marked"]
          and source["source_in_same_text_report"]
          and source["recomputed_changed_pages"] == [page_index]
          and source["cache_misses"] == 1)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print(f"\nincremental update: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
