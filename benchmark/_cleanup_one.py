"""One cleanup run (invoked as a subprocess so it can be timed out)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bench_lib as B

ROOT = Path(__file__).resolve().parent


def main() -> int:
    idx = int(sys.argv[1])
    model = sys.argv[2]
    dpi, psm = int(sys.argv[3]), int(sys.argv[4])
    window = json.loads(sys.argv[5])
    clip = [28.0, float(window[0]), 356.0, float(window[1])]

    import core
    output_dir = core.OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = ROOT.parent / output_dir
    cfg = json.loads((output_dir / "provider_config.json").read_text())
    core.set_llm_base_url(cfg.get("base_url") or None)
    provider, api_key, llm_model = cfg["provider"], cfg["api_key"], cfg["model"]

    tsv = (ROOT / "pages" / f"page_{idx:04d}" / f"dpi{dpi}" / f"psm{psm}"
           / f"raw_{model}.tsv").read_text(encoding="utf-8")
    gt = (ROOT / "ground_truth" / f"page_{idx:04d}.txt").read_text(encoding="utf-8")
    lines = [ln for ln in B.region_text(tsv, clip, dpi, order="logical").splitlines()
             if ln.strip()]
    before = "\n".join(lines)
    try:
        cleaned, meta, warns = core.cleanup_source_paragraphs(
            lines, provider, api_key, llm_model, call_llm_fn=core.call_llm,
            ocr_language="ara", source_language="Arabic", text_direction="rtl",
            ocr_engine_code="ara", confidence_threshold=0.0)
        after = "\n".join(cleaned)
        status = meta.get("status")
    except Exception as exc:  # noqa: BLE001
        cleaned, meta, warns = lines, {}, [str(exc)[:300]]
        after, status = before, f"error:{type(exc).__name__}"

    c_before, c_after = B.cer(gt, before), B.cer(gt, after)
    w_before, w_after = B.wer(gt, before), B.wer(gt, after)
    trans = B.error_transition(before, after, gt)
    record = {
        "page_index": idx, "ocr_model": model, "dpi": dpi, "psm": psm,
        "status": status,
        "cer_before": c_before["cer"], "cer_after": c_after["cer"],
        "cer_before_folded": B.cer_folded(gt, before),
        "cer_after_folded": B.cer_folded(gt, after),
        "wer_before": w_before["wer"], "wer_after": w_after["wer"],
        "cer_delta": c_after["cer"] - c_before["cer"],
        "wer_delta": w_after["wer"] - w_before["wer"],
        "corrected_errors": trans["corrected"],
        "introduced_errors": trans["introduced"],
        "unchanged_errors": trans["unchanged"],
        "gt_len": trans["gt_len"],
        "changed_segments": len(meta.get("changed_segments") or []),
        "paragraph_merges": len(meta.get("paragraph_merges") or []),
        "llm_input_count": meta.get("llm_input_count"),
        "input_count": len(lines), "output_count": len(cleaned),
        "warnings": warns[:3],
    }
    out = ROOT / "pages" / f"page_{idx:04d}" / "cleanup"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{model}_{dpi}_{psm}_before.txt").write_text(before, encoding="utf-8")
    (out / f"{model}_{dpi}_{psm}_after.txt").write_text(after, encoding="utf-8")
    B.write_json(out / f"{model}_{dpi}_{psm}.json", record)
    print(f"p{idx} {model} {dpi}/{psm} status={status} "
          f"CER {c_before['cer']:.3f}->{c_after['cer']:.3f} "
          f"folded {record['cer_before_folded']:.3f}->{record['cer_after_folded']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
