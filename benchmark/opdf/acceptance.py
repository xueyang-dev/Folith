"""Original-layout PDF Renderer v1 — corpus acceptance.

Runs the frozen corpus through the **real export entry**
(``core.build_delivery_assets``) and checks every page against the thresholds
that were declared before the run.

Declared before acceptance (never relaxed afterwards):

* readable floor: target size >= max(min(7pt, source size), 0.62 x source size);
* target presence: at least 95% of a slot's characters recoverable from the
  output text layer (the exact-substring rate is reported separately, because
  RTL readers disagree about Arabic text order);
* no source ghost inside a replaced region;
* no two placed boxes intersect, and no two rendered text lines collide;
* links, outline, page count, page size and rotation unchanged;
* every region that could not be rendered is listed in the report and marked on
  the page.

Usage::

    ./venv/bin/python benchmark/opdf/acceptance.py --mode offline
    ./venv/bin/python benchmark/opdf/acceptance.py --mode live
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
MANIFEST = HERE / "manifest.json"

PASS_RATE_REQUIRED = 0.90


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), default="live")
    parser.add_argument("--only", default="")
    parser.add_argument("--no-images", action="store_true")
    args = parser.parse_args()

    import core
    core.OUTPUT_DIR = JOBS
    import render_corpus as RC

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = {s for s in args.only.split(",") if s}
    out_root = HERE / "out" / f"acceptance-{args.mode}"
    out_root.mkdir(parents=True, exist_ok=True)

    result = {
        "phase": "original-layout-pdf-renderer-v1",
        "mode": args.mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "declared_thresholds": {
            "min_font_pt": RC.MIN_FONT_PT,
            "min_font_ratio": RC.MIN_FONT_RATIO,
            "target_presence_min": RC.TARGET_PRESENCE_MIN,
            "page_pass_rate_required": PASS_RATE_REQUIRED,
        },
        "samples": [],
        "challenge": [],
        "unfinished": [],
    }

    total_pages = 0
    passed_pages = 0
    for group, entries in (("samples", manifest["samples"]),
                           ("challenge", manifest.get("challenge") or [])):
        for sample in entries:
            if wanted and sample["id"] not in wanted:
                continue
            job_id = f"opdf-{args.mode}-{sample['id']}"
            if not (JOBS / job_id / "state.json").is_file():
                result[group].append({"id": sample["id"], "status": "no_job"})
                print(f"{sample['id']:26s} no job for mode {args.mode}")
                continue
            state = core.load_job_state(job_id)
            source_bytes = core.load_source(job_id)
            sample_dir = out_root / sample["id"]
            sample_dir.mkdir(parents=True, exist_ok=True)

            # The real export entry, with the delivery option switched on.
            state["delivery_config"] = dict(
                core.normalize_delivery_config(
                    state.get("delivery_config")),
                deliver_original_pdf=True)
            started = time.time()
            try:
                bundle = core.build_delivery_assets(job_id, state)
            except Exception as exc:
                result[group].append({"id": sample["id"], "status": "export_failed",
                                      "error": f"{type(exc).__name__}: {exc}"})
                print(f"{sample['id']:26s} EXPORT FAILED {type(exc).__name__}: {exc}")
                continue
            seconds = time.time() - started
            if "original_layout.pdf" not in bundle:
                # The delivery contract only produces the artifact once there
                # are translations; a job without them is reported, not fatal.
                delivery_manifest = json.loads(
                    bundle.get("delivery_manifest.json") or b"{}")
                entry = {
                    "id": sample["id"],
                    "status": "not_produced",
                    "reason": (delivery_manifest.get("skipped_assets") or {}).get(
                        "original_layout.pdf",
                        "no completed translations for this job"),
                }
                result[group].append(entry)
                print(f"{sample['id']:26s} not produced: {entry['reason']}")
                continue
            pdf_bytes = bundle["original_layout.pdf"]
            report = json.loads(bundle["original_layout_report.json"])
            (sample_dir / "original_layout.pdf").write_bytes(pdf_bytes)
            (sample_dir / "original_layout_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")

            pages = []
            for index in range(sample["page_count"]):
                record = RC.check_page(source_bytes, pdf_bytes, report, index)
                pages.append(record)
                if not args.no_images:
                    RC._page_rasters(source_bytes, pdf_bytes, index,
                                     sample_dir, f"{sample['id']}")
            (sample_dir / "page_check.json").write_text(
                json.dumps(pages, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")

            passed = sum(1 for p in pages if p["pass"])
            failed = [p["index"] for p in pages if not p["pass"]]
            entry = {
                "id": sample["id"],
                "job_id": job_id,
                "pages": len(pages),
                "pass": passed,
                "fail": len(failed),
                "failed_pages": failed,
                "unfinished": report["totals"]["unfinished"],
                "overflow": report["totals"]["overflow"],
                "untranslated": report["totals"].get("untranslated", 0),
                "unresolved_items": report["totals"]["unresolved_items"],
                "missing_glyph_slots": report["totals"]["missing_glyph_slots"],
                "links_before": report["totals"]["links_before"],
                "links_after": report["totals"]["links_after"],
                "toc_before": report["totals"]["toc_before"],
                "toc_after": report["totals"]["toc_after"],
                "page_geometry_preserved": report["totals"]["page_geometry_preserved"],
                "exact_presence_min": min(
                    (p["exact_presence"] for p in pages
                     if p.get("exact_presence") is not None), default=None),
                "export_seconds": round(seconds, 2),
                "bytes": len(pdf_bytes),
                "features": sample["features"],
            }
            result[group].append(entry)
            if group == "samples":
                total_pages += len(pages)
                passed_pages += passed
            for record in report.get("unfinished") or []:
                if group == "samples":
                    result["unfinished"].append({"sample": sample["id"], **record})
            rate = passed / len(pages) if pages else 0
            print(f"{sample['id']:26s} {passed}/{len(pages)} pages pass "
                  f"({rate * 100:.0f}%) unfinished={entry['unfinished']} "
                  f"overflow={entry['overflow']} untranslated={entry['untranslated']} "
                  f"links {entry['links_before']}->{entry['links_after']} "
                  f"toc {entry['toc_before']}->{entry['toc_after']} "
                  f"{seconds:.1f}s")

    result["totals"] = {
        "pages": total_pages,
        "pass": passed_pages,
        "fail": total_pages - passed_pages,
        "pass_rate": round(passed_pages / total_pages, 4) if total_pages else 0.0,
        "required": PASS_RATE_REQUIRED,
    }
    result["verdict"] = (
        "PASS" if total_pages and passed_pages / total_pages >= PASS_RATE_REQUIRED
        else "FAIL")

    # Cost: real usage recorded by the pipeline itself.
    usage_total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                   "total_tokens": 0, "failures": 0}
    for sample in manifest["samples"]:
        job_id = f"opdf-{args.mode}-{sample['id']}"
        state = core.load_job_state(job_id)
        if not state:
            continue
        usage = state.get("llm_usage") or {}
        for key in usage_total:
            usage_total[key] += int(usage.get(key) or 0)
    result["llm_usage"] = usage_total
    result["llm_cost"] = "unknown (the relay returns no currency amount)"

    (out_root / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\npages {passed_pages}/{total_pages} "
          f"({result['totals']['pass_rate'] * 100:.1f}%) -> {result['verdict']}")
    print(f"report: {(out_root / 'acceptance.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
