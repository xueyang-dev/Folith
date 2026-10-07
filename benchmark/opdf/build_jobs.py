"""Build the corpus jobs (original layout renderer acceptance).

Two modes:

``offline``
    Runs the real extraction / DocumentIR / segmentation / translation pipeline
    with the repository's deterministic ``OfflineProvider`` standing in for the
    model.  Zero API cost.  Used to isolate renderer defects from translation
    defects.  Scanned and mixed samples are skipped because their semantics come
    from a real VLM call that cannot be faked without inventing geometry.

``live``
    Runs every corpus sample against the configured provider, including the
    scanned pages.  Real usage is recorded in the run report.

Usage::

    ./venv/bin/python benchmark/opdf/build_jobs.py --mode offline
    ./venv/bin/python benchmark/opdf/build_jobs.py --mode live
    ./venv/bin/python benchmark/opdf/build_jobs.py --mode offline --cleanup
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

HERE = pathlib.Path(__file__).resolve().parent
JOBS = HERE / "jobs"
MANIFEST = HERE / "manifest.json"
PROVIDER_CONFIG = (pathlib.Path.home() / "Library" / "Application Support"
                   / "Folith" / "data" / "provider_config.json")

#: Samples whose semantics require a real visual-model call.
NEEDS_VLM = {"scan-clean", "novel-midnight-library"}


def provider_settings() -> dict:
    data = json.loads(PROVIDER_CONFIG.read_text(encoding="utf-8"))
    return {
        "provider": str(data.get("provider") or ""),
        "api_key": str(data.get("api_key") or ""),
        "model": str(data.get("model") or ""),
        "base_url": str(data.get("base_url") or ""),
    }


def build_one(sample: dict, *, mode: str, cleanup: bool) -> dict:
    import core

    sample_id = sample["id"]
    job_id = f"opdf-{mode}-{sample_id}"
    data = (HERE / sample["file"]).read_bytes()
    filename = f"{sample['id']}.pdf"
    settings = provider_settings()
    record = {
        "sample": sample_id, "job_id": job_id, "mode": mode,
        "cleanup": cleanup, "target_lang": sample["target_lang"],
        "filename": filename, "started_at": time.time(),
    }

    if mode == "offline":
        from offline_provider import OfflineProvider
        original = core.call_llm
        core.call_llm = OfflineProvider()
        provider, api_key, model, base_url = "DeepSeek", "offline", "offline", ""
    else:
        original = None
        provider = settings["provider"]
        api_key = settings["api_key"]
        model = settings["model"]
        base_url = settings["base_url"] or None

    kwargs = dict(
        provider=provider, api_key=api_key, model=model,
        target_lang=sample["target_lang"], auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], enable_review=False,
        enable_annotate=False, use_tm=False, enable_understanding=False,
        delivery_config={"deliver_report": False},
        enable_source_cleanup=cleanup, segmentation_mode="sentence",
    )
    if base_url:
        kwargs["translator_base_url"] = base_url
    if sample_id in NEEDS_VLM:
        kwargs["ocr_mode"] = "force"
    try:
        state = core.run_job_pipeline(job_id, filename, data, **kwargs)
    finally:
        if original is not None:
            core.call_llm = original

    usage = state.get("llm_usage") or {}
    report = (state.get("extraction_report") or {})
    document = report.get("document_ir") or {}
    pairs = state.get("pairs") or []
    with_spans = sum(1 for p in pairs if p.get("source_spans"))
    usable = 0
    for pair in pairs:
        spans = pair.get("source_spans") or []
        if any(s.get("grounding_quality") in {"exact", "strong", "approximate"}
               and s.get("bbox") for s in spans):
            usable += 1
    record.update({
        "p1_done": bool(state.get("p1_done")),
        "p2_done": bool(state.get("p2_done")),
        "paragraphs": len(state.get("source_paragraphs") or []),
        "segments": len(pairs),
        "pairs_with_spans": with_spans,
        "pairs_with_usable_region": usable,
        "semantic_source": report.get("semantic_source"),
        "ir_pages": len(document.get("pages") or []),
        "ir_blocks": len(document.get("blocks") or []),
        "usage": {
            "calls": (usage.get("calls") if isinstance(usage, dict) else None),
            "input_tokens": (usage.get("input_tokens") if isinstance(usage, dict) else None),
            "output_tokens": (usage.get("output_tokens") if isinstance(usage, dict) else None),
        },
        "finished_at": time.time(),
    })
    record["seconds"] = round(record["finished_at"] - record["started_at"], 2)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--cleanup", action="store_true",
                        help="keep LLM source cleanup enabled (default: off)")
    parser.add_argument("--only", default="", help="comma separated sample ids")
    args = parser.parse_args()

    import core
    core.OUTPUT_DIR = JOBS
    core._ensure_output_dir()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = {s for s in args.only.split(",") if s}
    records = []
    for sample in manifest["samples"]:
        if wanted and sample["id"] not in wanted:
            continue
        if args.mode == "offline" and sample["id"] in NEEDS_VLM:
            print(f"skip {sample['id']} (needs a real visual-model call)")
            continue
        print(f"[{args.mode}] {sample['id']} -> {sample['target_lang']}")
        try:
            record = build_one(sample, mode=args.mode, cleanup=args.cleanup)
        except Exception as exc:  # keep the corpus going; the failure is evidence
            record = {"sample": sample["id"], "mode": args.mode, "error":
                      f"{type(exc).__name__}: {exc}"}
            print(f"   FAILED: {record['error']}")
        else:
            print(f"   p1={record['p1_done']} p2={record['p2_done']} "
                  f"segments={record['segments']} usable_region="
                  f"{record['pairs_with_usable_region']}/{record['segments']} "
                  f"{record['seconds']}s")
        records.append(record)

    out = HERE / f"jobs-{args.mode}{'-cleanup' if args.cleanup else ''}.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
