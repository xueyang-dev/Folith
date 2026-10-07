"""Real historical page -> production intake/cache/segments -> offline translation/export.

Only translation is replaced by the existing regression provider. OCR is native
Paddle, and its evidence is preserved before shared downstream processing.
"""
import json
import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import pymupdf
import core
from offline_provider import OfflineProvider
from folith import pdf_preview


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-suffix", default="")
    parser.add_argument("--page-index", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark")
    args = parser.parse_args()
    source = os.environ.get("FOLITH_BENCHMARK_PDF", "/Users/xueyang/Downloads/Alam_al-din_(IA_alamaldin00alimuoft).pdf")
    with pymupdf.open(source) as document, pymupdf.open() as excerpt:
        excerpt.insert_pdf(document, from_page=args.page_index, to_page=args.page_index)
        payload = excerpt.tobytes()
    core.OUTPUT_DIR = ROOT / ".audit" / "paddle-intake"
    core.call_llm = OfflineProvider()
    job = f"real-historical-page-{args.page_index + 1}" + args.job_suffix
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = dict(provider="DeepSeek", api_key="offline", model="fixture",
        target_lang="简体中文", auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], enable_review=False,
        enable_annotate=False, use_tm=False, enable_understanding=False,
        enable_source_cleanup=False, delivery_config={"deliver_report": False},
        ocr_language="ara", ocr_dpi=350, ocr_backend="paddle", on_status=print)
    started = time.perf_counter()
    state = core.run_job_pipeline(job, f"historical-page-{args.page_index + 1}.pdf", payload, **config)
    elapsed = time.perf_counter() - started
    assert state["p1_done"] and state["p2_done"]
    report = state["extraction_report"]
    ocr = report["ocr"]
    assert ocr["actual_engine"] == "paddleocr" and ocr["completed_pages"] == 1
    assert ocr["device"] == "cpu" and state["source_paras"]
    assets = core.build_delivery_assets(job, state)
    assert assets["translation.docx"].startswith(b"PK")
    performance = json.loads((core.job_dir(job) / "performance.json").read_text())
    assert performance["stages"]["ocr"]["status"] == "completed"
    assert performance["stages"]["ocr"]["metadata"]["actual_engine"] == "paddleocr"
    query = next(p for p in state["source_paras"] if len(p) > 30)
    page, rects = pdf_preview.find_segment_page_and_rects(job, query)
    assert page == 0 and rects
    image = pdf_preview.render_pdf_page_image(job, page, rects, dpi=120)
    (args.output_dir / "paddle-intake-preview.png").write_bytes(image)
    cached_started = time.perf_counter()
    _, _, cached = core._ocr_pdf_text_with_warnings(payload, max_pages=None,
        ocr_language="ara", dpi=350, backend="paddle", checkpoint_dir=core.job_dir(job), return_details=True)
    assert cached["ocr_cached_pages"] == 1 and cached["model_load_s"] == 0
    result = {"original_pdf_page": args.page_index + 1, "excerpt_pages": 1,
        "translation_provider": "deterministic test provider; no remote calls",
        "cleanup_enabled": False, "total_pipeline_s": elapsed,
        "raw_paragraphs": len(state["source_paras"]), "segments": len(state["paras"]),
        "pairs": len(state["pairs"]), "delivery_assets": list(assets),
        "source_location": {"page_index": page, "rects": rects, "query": query},
        "cache_hit_s": time.perf_counter() - cached_started,
        "cached_pages": cached["ocr_cached_pages"], "cache_hit_model_load_s": cached["model_load_s"],
        "performance": performance,
        "extraction_report": report, "pipeline_config": state["pipeline_config"]}
    text = json.dumps(result, ensure_ascii=False, indent=2).replace(str(ROOT) + "/", "")
    (args.output_dir / "paddle-intake-result.json").write_text(text)
    print("PASS: real Paddle intake, raw evidence, shared translation/export, preview and cache reuse", flush=True)


if __name__ == "__main__":
    main()
