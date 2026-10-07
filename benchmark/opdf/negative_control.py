"""Negative controls for the acceptance checks.

A passing corpus only means something if the checks can fail.  This script
deliberately breaks a rendered result in ways a real defect would, and asserts
that the verification reports it.

Usage:
    ./venv/bin/python benchmark/opdf/negative_control.py --mode live \\
        --sample arxiv-attention
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
HERE = pathlib.Path(__file__).resolve().parent
JOBS = HERE / "jobs"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="live")
    parser.add_argument("--sample", default="arxiv-attention")
    parser.add_argument("--page", type=int, default=0)
    args = parser.parse_args()

    import core
    core.OUTPUT_DIR = JOBS
    from folith import pdfrender
    from folith.pdfrender import compose
    import render_corpus as RC

    job_id = f"opdf-{args.mode}-{args.sample}"
    state = core.load_job_state(job_id)
    source_bytes = core.load_source(job_id)
    if not state or not source_bytes:
        raise SystemExit(f"no job {job_id}")

    results = {}

    def render(**kwargs):
        return pdfrender.render_original_layout(
            job_id, state, source_bytes, use_cache=False, **kwargs)

    baseline = render()
    baseline_pages = [
        RC.check_page(source_bytes, baseline.data, baseline.report, index)
        for index in range(len(baseline.report["pages"]))
    ]
    results["baseline"] = {
        "pass": sum(1 for p in baseline_pages if p["pass"]),
        "pages": len(baseline_pages),
    }

    # 1. No removal: the source text must be reported as a ghost.
    original_erase = compose.erase_text
    compose.erase_text = lambda page, rects: 0
    try:
        broken = render()
    finally:
        compose.erase_text = original_erase
    ghosts = sum(len(RC.check_page(source_bytes, broken.data, broken.report, i)["ghosts"])
                 for i in range(len(broken.report["pages"])))
    results["removal_disabled"] = {"ghosts_detected": ghosts,
                                   "detected": ghosts > 0}

    # 2. No overlay: the target text must be reported as missing.
    original_stamp = compose.stamp
    compose.stamp = lambda page, data: False
    try:
        broken = render()
    finally:
        compose.stamp = original_stamp
    missing = sum(len(RC.check_page(source_bytes, broken.data, broken.report, i)
                        ["target_missing"])
                  for i in range(len(broken.report["pages"])))
    results["overlay_removed"] = {"missing_slots": missing, "detected": missing > 0}

    original_offsets = pdfrender._resolve_page

    # 3. An overlay with an opaque background would repaint the whole page.  This
    #    is the defect the outside-box pixel comparison exists for: the diff
    #    check deliberately ignores text placement (other checks cover that) and
    #    watches everything the renderer must not touch.
    def opaque_stamp(page, overlay_bytes):
        page.draw_rect(page.rect, color=(1, 1, 1), fill=(1, 1, 1), width=0,
                       overlay=True)
        return compose.stamp.__wrapped__(page, overlay_bytes) \
            if hasattr(compose.stamp, "__wrapped__") else _real_stamp(page, overlay_bytes)

    _real_stamp = compose.stamp
    compose.stamp = opaque_stamp
    try:
        broken = render()
    finally:
        compose.stamp = _real_stamp
    worst = 0.0
    counted = 0
    for index in range(len(broken.report["pages"])):
        record = RC.check_page(source_bytes, broken.data, broken.report, index,
                               "translated")
        value = record["outside_halo_diff"] or 0.0
        worst = max(worst, value)
        if value > RC.OUTSIDE_HALO_MAX:
            counted += 1
    results["opaque_overlay_background"] = {
        "worst_outside_diff": worst,
        "pages_flagged": counted,
        "detected": counted > 0,
    }

    # 4. Two translations forced into the same vertical band must be reported as
    #    a collision.
    def colliding(page_slots, source_page, *, min_font_pt, min_font_ratio, build_block):
        resolved = original_offsets(page_slots, source_page,
                                    min_font_pt=min_font_pt,
                                    min_font_ratio=min_font_ratio,
                                    build_block=build_block)
        if len(resolved) >= 2:
            resolved[1]["top"] = resolved[0]["top"]
            resolved[1]["box"] = (resolved[1]["box"][0], resolved[0]["top"],
                                  resolved[1]["box"][2],
                                  resolved[0]["top"] + resolved[1]["height"])
            resolved[1]["overlap_with"] = ["forced"]
        return resolved

    pdfrender._resolve_page = colliding
    try:
        broken = render()
    finally:
        pdfrender._resolve_page = original_offsets
    collisions = 0
    for index in range(len(broken.report["pages"])):
        record = RC.check_page(source_bytes, broken.data, broken.report, index,
                               "translated")
        collisions += len(record["overlaps"]) + len(record["ink_overlaps"])
    results["forced_collision"] = {"collisions_detected": collisions,
                                   "detected": collisions > 0}

    results["all_detected"] = all(
        results[key]["detected"] for key in
        ("removal_disabled", "overlay_removed", "opaque_overlay_background",
         "forced_collision"))
    results["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    out = HERE / "out" / f"negative-control-{args.mode}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nnegative controls: {'ALL DETECTED' if results['all_detected'] else 'MISSED'}")
    return 0 if results["all_detected"] else 1


if __name__ == "__main__":
    sys.exit(main())
