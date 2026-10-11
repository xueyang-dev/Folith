"""Bounded book-specific OCR/text/image experiment, outside production defaults.

Reference text is used only for evaluation, never in API requests. Images are
unaltered RGB crops of the same page render used by native Paddle. The existing
source-cleanup implementation supplies its normal JSON validation and guards.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))
import bench_lib as B

OUT = ROOT / "benchmark/ocr-vision-pilot"
SCRATCH = ROOT / ".audit/ocr-vision-pilot"
VISION_MODEL = "deepseek-v4-flash-vision-exp"
MODES = ("existing_text", "vision_text_control", "image_guided", "image_only")
VISUAL_RULES = (
    "\n如有扫描图，扫描图是文字证据，OCR 只是可能有误的候选；"
    "如无扫描图，只修复有充分文字证据的错误。"
    "保留历史拼写，不自行补充原图没有的 hamza、变音符号或标点。"
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def region_lines(record, box):
    x0, y0, x1, y1 = box
    return [line for block in record["blocks"] for line in block["lines"]
            if x0 <= (line["left"] + line["right"]) / 2 <= x1
            and y0 <= (line["top"] + line["bottom"]) / 2 <= y1]


def levenshtein_mask(reference, hypothesis):
    """Minimal-edit alignment; unlike SequenceMatcher, no autojunk heuristic.

    Returns wrong reference positions and extra hypothesis characters. Counts
    of corrected/introduced reference positions are diagnostic, tie-dependent;
    CER edit distance remains the principal outcome.
    """
    r, h = B.normalize(reference), B.normalize(hypothesis)
    n, m = len(r), len(h)
    dp = [list(range(m + 1))] + [[i] + [0] * m for i in range(1, n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j - 1] + (r[i - 1] != h[j - 1]),
                           dp[i - 1][j] + 1, dp[i][j - 1] + 1)
    wrong = set()
    extra = 0
    i, j = n, m
    while i or j:
        if i and j and r[i - 1] == h[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            i, j = i - 1, j - 1
        elif i and j and dp[i][j] == dp[i - 1][j - 1] + 1:
            wrong.add(i - 1)
            i, j = i - 1, j - 1
        elif i and dp[i][j] == dp[i - 1][j] + 1:
            wrong.add(i - 1)
            i -= 1
        else:
            extra += 1
            j -= 1
    return wrong, extra


def metrics(reference, before, after):
    a, ai = levenshtein_mask(reference, before)
    b, bi = levenshtein_mask(reference, after)
    folded = B.cer(B.fold_orthography(reference), B.fold_orthography(after))
    return {"character_errors": B.cer(reference, after),
            "word_errors": B.wer(reference, after),
            "folded_cer": folded["cer"], "folded_character_errors": folded,
            "reference_positions_corrected": len(a - b),
            "reference_positions_introduced": len(b - a),
            "reference_positions_still_wrong": len(a & b),
            "extra_characters_before": ai, "extra_characters_after": bi,
            "net_edit_reduction": len(a) + ai - len(b) - bi}


def prepare():
    from PIL import Image
    import pymupdf
    from folith.ocr_paddle import PaddleOCRBackend

    OUT.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    source = os.environ.get("FOLITH_BENCHMARK_PDF",
        str(Path.home() / "Downloads" / "Alam_al-din_(IA_alamaldin00alimuoft).pdf"))
    manifest = json.loads((ROOT / "benchmark/paddle-finetune/dataset-manifest.json").read_text())
    training = {int(p) for p, group in manifest["page_splits"].items() if group == "train"}
    pages = []
    for page in (5, 220):
        rows = [r for r in manifest["rows"] if r["page_index"] == page]
        box = [min(r["bbox_pixels"][0] for r in rows),
               min(r["bbox_pixels"][1] for r in rows),
               max(r["bbox_pixels"][2] for r in rows),
               max(r["bbox_pixels"][3] for r in rows)]
        pages.append({"page_index": page, "pdf_page": page + 1,
            "cohort": "previous_test", "crop_bbox_pixels": box,
            "reference_lines": [r["text"] for r in rows],
            "uncertainties": [u for r in rows for u in r["uncertain_words"]],
            "label_method": "Existing human transcription, visually reviewed for this pilot; original dataset preserved.",
            "training_used": False, "checkpoint_selection_used": False})
    fresh = json.loads((OUT / "fresh-references.json").read_text())
    for page in fresh["pages"]:
        pages.append({**page, "label_method": fresh["label_method"]})
    assert not {p["page_index"] for p in pages} & training
    os.environ["FOLITH_OCR_MODEL_DIR"] = str(ROOT / "benchmark/paddle-models/alam-al-din-finetuned")
    backend = PaddleOCRBackend("ara")
    try:
        for page in pages:
            idx = page["page_index"]
            raster = (ROOT / f"benchmark/pages/page_{idx:04d}/dpi350/page.png"
                      if page["cohort"] == "previous_test" else SCRATCH / f"page-{idx:04d}.png")
            if not raster.exists():
                with pymupdf.open(source) as document:
                    document[idx].get_pixmap(dpi=350, alpha=False).save(raster)
            native = (ROOT / f"benchmark/paddle-finetune/page-{idx:04d}-adapted.json"
                      if page["cohort"] == "previous_test" else OUT / f"page-{idx:04d}-paddle.json")
            if not native.exists():
                started = time.perf_counter()
                result = backend.recognize_page(raster.read_bytes(), page_index=idx,
                    page_number=idx + 1, language="ara", dpi=350)
                if result.error:
                    raise RuntimeError(result.error)
                with Image.open(raster) as image:
                    record = result.to_record(image.width * 72 / 350, image.height * 72 / 350)
                record["pilot_recognition_wall_s"] = time.perf_counter() - started
                write(native, record)
            record = json.loads(native.read_text())
            selected = region_lines(record, page["crop_bbox_pixels"])
            if len(selected) != len(page["reference_lines"]):
                raise ValueError(f"PDF {idx+1}: expected {len(page['reference_lines'])} physical rows, got {len(selected)}; inspect bounds")
            image_path = SCRATCH / f"input-{idx:04d}.png"
            with Image.open(raster) as image:
                image.crop(page["crop_bbox_pixels"]).save(image_path)
            page.update(ocr_lines=[line["text"] for line in selected],
                line_confidences=[line.get("engine_confidence") for line in selected],
                image=str(image_path.relative_to(ROOT)), native_evidence=str(native.relative_to(ROOT)),
                baseline=metrics("\n".join(page["reference_lines"]),
                                 "\n".join(line["text"] for line in selected),
                                 "\n".join(line["text"] for line in selected)))
            print("Prepared PDF", idx + 1, page["cohort"], len(selected), "rows", flush=True)
    finally:
        backend.close()
    write(OUT / "dataset.json", {"source": source, "dpi": 350,
        "reference_fixed_before_api_calls": True, "training_pages_excluded": sorted(training),
        "pages": pages, "reference_limit": "Existing human references contain uncertain glyphs; fresh references independently transcribed by assistant, not an external expert."})


def request_content(prompt, image=None):
    if image is None:
        return prompt
    data = base64.b64encode(Path(image).read_bytes()).decode("ascii")
    return [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}},
            {"type": "text", "text": prompt}]


def run(args):
    import core
    import httpx
    from openai import OpenAI
    from folith import source_cleanup

    cfg = core.load_provider_config()
    if not cfg or not cfg.get("api_key"):
        raise RuntimeError("No saved model credentials")
    provider = cfg["provider"]
    if core.PROVIDERS[provider]["kind"] not in {"openai", "openai_compat"}:
        raise ValueError("This pilot uses the configured OpenAI-compatible provider")
    base = core.resolve_openai_base_url(provider, cfg.get("base_url") or None)
    http = httpx.Client(trust_env=not core.PROVIDERS[provider].get("proxy_bypass", False), timeout=150)
    client = OpenAI(api_key=cfg["api_key"], base_url=base or None,
                    http_client=http, timeout=150, max_retries=0)
    session = "folith-ocr-vision-pilot-" + str(uuid.uuid4())
    dataset = json.loads((OUT / "dataset.json").read_text())
    result_dir = OUT / args.result_subdir
    calls = 0
    try:
        for page in dataset["pages"]:
            if args.pages and page["page_index"] not in args.pages:
                continue
            for mode in args.modes:
                model = cfg["model"] if mode == "existing_text" else args.vision_model
                dest = result_dir / f"page-{page['page_index']:04d}-{mode}.json"
                if dest.exists():
                    saved = json.loads(dest.read_text())
                    if (saved.get("crop_bbox_pixels") != page["crop_bbox_pixels"]
                            or saved.get("requested_model") != model
                            or saved.get("thinking", "provider_default") != args.thinking):
                        raise ValueError("Stored result has different inputs/model/thinking; preserve it separately before rerunning")
                    print("Retained existing result", dest.name, flush=True)
                    continue
                if calls >= args.max_calls:
                    print("Stopped at requested call bound", args.max_calls, flush=True)
                    return
                requests = []
                raw_responses = []
                image = ROOT / page["image"] if mode in {"image_guided", "image_only"} else None
                started = time.perf_counter()

                def invoke(provider_name, key, model_name, system, prompt, **kwargs):
                    nonlocal calls
                    if calls >= args.max_calls:
                        raise RuntimeError("Pilot request bound reached")
                    calls += 1
                    if mode in {"vision_text_control", "image_guided"}:
                        system += VISUAL_RULES
                    body = {"model": model_name,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": request_content(prompt, image)}],
                        "temperature": 0.0, "max_tokens": args.max_tokens}
                    if args.thinking == "disabled":
                        body["extra_body"] = {"thinking": {"type": "disabled"}}
                    elif args.thinking == "low":
                        body["extra_body"] = {"thinking": {"type": "enabled"}}
                        body["reasoning_effort"] = "low"
                    if provider == "OpenCode Go":
                        body["extra_headers"] = {"User-Agent": "Folith/0.5.0", "x-opencode-session": session}
                    request_started = time.perf_counter()
                    attempt = {"status": "started", "model": model_name,
                        "page_index": page["page_index"], "mode": mode,
                        "thinking": args.thinking, "max_tokens": args.max_tokens,
                        "image_attached": image is not None,
                        "system_prompt": system, "user_prompt_text": prompt}
                    journal = result_dir / "requests" / f"{session}-{calls:02d}.json"
                    requests.append(attempt)
                    write(journal, attempt)
                    try:
                        response = client.chat.completions.create(**body)
                    except BaseException as exc:
                        attempt.update(status="failed", seconds=time.perf_counter() - request_started,
                            error_type=type(exc).__name__,
                            error=str(exc).replace(cfg["api_key"], "<redacted>")[:350])
                        write(journal, attempt)
                        raise
                    text = response.choices[0].message.content or ""
                    raw_responses.append(text)
                    attempt.update({"status": "returned", "request_id": response.id, "model": response.model,
                        "seconds": time.perf_counter() - request_started,
                        "finish_reason": response.choices[0].finish_reason,
                        "usage": response.usage.model_dump() if response.usage else None,
                        "image_attached": image is not None,
                        "system_prompt": system, "user_prompt_text": prompt})
                    write(journal, attempt)
                    if response.choices[0].finish_reason == "length":
                        raise ValueError("Model output truncated")
                    return text

                before = "\n".join(page["ocr_lines"])
                status, after, meta, warnings = "error", "", {}, []
                error = None
                try:
                    if mode == "image_only":
                        system = ("你是历史阿拉伯语扫描图的忠实转写员。逐字转写图中正文，"
                            "不翻译、不摘要、不补写、不现代化拼写。只保留看得见的符号。"
                            "按原始印刷行从上到下，每行一个 index；看不清的字用 [?] 标记。"
                            "只返回 JSON：{\"segments\":[{\"index\":0,\"text\":\"原文\",\"break_after\":true}]}。")
                        count = len(page["ocr_lines"])
                        prompt = f"转写图中全部 {count} 行正文，index 必须完整为 0 到 {count-1}。"
                        response = invoke(provider, cfg["api_key"], model, system, prompt)
                        rows = source_cleanup._decode(response, count)
                        after = "\n".join(row["text"] for row in rows)
                        meta = {"input_count": count, "output_count": len(rows)}
                        status = "completed"
                    else:
                        cleaned, meta, warnings = core.cleanup_source_paragraphs(
                            page["ocr_lines"], provider, cfg["api_key"], model,
                            call_llm_fn=invoke, ocr_language="ara", source_language="Arabic",
                            text_direction="rtl", ocr_engine_code="ar", ocr_backend="paddleocr",
                            confidence_threshold=0, parallelism=1, max_retries=0,
                            max_batch_items=30, max_batch_chars=9000)
                        after = "\n".join(cleaned)
                        status = meta.get("status", "error")
                    record_metrics = metrics("\n".join(page["reference_lines"]), before, after)
                except Exception as exc:
                    error = {"type": type(exc).__name__, "message": str(exc).replace(cfg["api_key"], "<redacted>")[:350]}
                    record_metrics = None
                record = {"mode": mode, "page_index": page["page_index"], "pdf_page": page["pdf_page"],
                    "cohort": page["cohort"], "crop_bbox_pixels": page["crop_bbox_pixels"],
                    "provider": provider, "requested_model": model,
                    "request_max_tokens": args.max_tokens,
                    "thinking": args.thinking,
                    "status": status, "seconds": time.perf_counter() - started,
                    "text": after, "metrics": record_metrics,
                    "requests": requests, "raw_responses": raw_responses,
                    "cleanup_metadata": meta,
                    "warnings": [str(w).replace(cfg["api_key"], "<redacted>") for w in warnings], "error": error}
                write(dest, record)
                rate = record_metrics["character_errors"]["cer"] if record_metrics else None
                print("PDF", page["pdf_page"], mode, status, "CER", rate,
                      "seconds", round(record["seconds"], 2), error or "", flush=True)
    finally:
        client.close()


def summarize():
    dataset = json.loads((OUT / "dataset.json").read_text())
    records = [json.loads(p.read_text()) for p in OUT.glob("page-*-*.json")
               if not p.name.endswith("-paddle.json")]
    summaries = {}
    for cohort in ("all", "fresh_unseen", "previous_test"):
        summaries[cohort] = {}
        selected_pages = [p for p in dataset["pages"] if cohort == "all" or p["cohort"] == cohort]
        for mode in ("raw_paddle", *MODES):
            selected = ([{"metrics": p["baseline"], "status": "completed", "seconds": 0} for p in selected_pages]
                        if mode == "raw_paddle" else [r for r in records
                        if r.get("mode") == mode and (cohort == "all" or r["cohort"] == cohort)])
            complete = [r for r in selected if r["status"] == "completed" and r.get("metrics")]
            if not complete:
                continue
            c = Counter(); w = Counter(); f = Counter()
            for row in complete:
                c.update({k: row["metrics"]["character_errors"][k] for k in ("sub", "del", "ins", "ref_len")})
                w.update({k: row["metrics"]["word_errors"][k] for k in ("sub", "del", "ins", "ref_words")})
                f.update({k: row["metrics"]["folded_character_errors"][k] for k in ("sub", "del", "ins", "ref_len")})
            summaries[cohort][mode] = {"completed": len(complete), "expected": len(selected_pages),
                "complete_cohort": len(complete) == len(selected_pages),
                "character_errors": dict(c), "word_errors": dict(w),
                "cer": (c["sub"] + c["del"] + c["ins"]) / c["ref_len"],
                "wer": (w["sub"] + w["del"] + w["ins"]) / w["ref_words"],
                "folded_cer": (f["sub"] + f["del"] + f["ins"]) / f["ref_len"],
                "seconds": sum(r["seconds"] for r in complete),
                "positions_corrected": sum(r["metrics"]["reference_positions_corrected"] for r in complete),
                "positions_introduced": sum(r["metrics"]["reference_positions_introduced"] for r in complete),
                "extra_characters_after": sum(r["metrics"]["extra_characters_after"] for r in complete),
                "net_edit_reduction": sum(r["metrics"]["net_edit_reduction"] for r in complete),
                "tokens": dict(sum_usage(complete)),
                "requests": sum(len(r.get("requests", [])) for r in complete)}
    history = [json.loads(p.read_text()) for p in OUT.rglob("*.json")]
    attempts = [r for r in history if r.get("mode") in MODES and "requests" in r]
    result = {"scope": "Fixed, page-disjoint pilot; no retraining; same raw scan crops",
        "models": sorted({r.get("requested_model") for r in records if r.get("requested_model")}),
        "summary": summaries,
        "pilot_consumption_including_probes": {"returned_requests": sum(len(r["requests"]) for r in attempts),
            "tokens": dict(sum_usage(attempts)),
            "cancelled_requests_without_usage": 1,
            "cancelled_request_note": "Initial 4096-token run interrupted during PDF 121 existing_text; provider consumption unknown."},
        "limits": [dataset["reference_limit"],
            "No paid-price claim: provider token usage is saved per request; subscription billing is not returned.",
            "Reference-position correction counts depend on alignment ties; insertions and CER are separately reported.",
            "Aggregate only compares identical page cohorts when every arm is complete."]}
    write(OUT / "results.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def sum_usage(records):
    usage = Counter()
    for record in records:
        for request in record.get("requests", []):
            row = request.get("usage") or {}
            usage.update({key: row.get(key, 0) or 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")})
            usage["reported_reasoning_tokens"] += (row.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
    return usage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "summarize"))
    parser.add_argument("--vision-model", default=VISION_MODEL)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--pages", nargs="+", type=int, help="Zero-based PDF page indices")
    parser.add_argument("--max-calls", type=int, default=16)
    parser.add_argument("--max-tokens", type=int, default=16384,
                        help="Includes reasoning tokens for the configured reasoning models")
    parser.add_argument("--thinking", choices=("provider_default", "disabled", "low"), default="provider_default",
                        help="Disabled uses the documented DeepSeek thinking control, only for this pilot")
    parser.add_argument("--result-subdir", default="", help="Preserve supplementary runs beneath the pilot output directory")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "run":
        run(args)
    else:
        summarize()


if __name__ == "__main__":
    main()
