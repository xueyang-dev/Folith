"""Evaluate original and adapted native OCR with identical held-out inputs."""
import argparse
import io
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
import bench_lib as B


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2).replace(
        str(ROOT.parent) + "/", "") + "\n")


def region_text(record, window):
    pieces = []
    for block in record["blocks"]:
        for line in block["lines"]:
            if record["engine"]["backend"] == "tesseract":
                words = [word["text"] for word in line["words"]
                         if 28 <= (word["left"] + word["right"]) * 36 / 350 <= 356
                         and window[0] <= (word["top"] + word["bottom"]) * 36 / 350 <= window[1]]
                if words:
                    pieces.append(" ".join(words))
            elif window[0] <= (line["top"] + line["bottom"]) * 36 / 350 <= window[1]:
                pieces.append(line["text"])
    return "\n".join(pieces)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT.parent / ".audit/paddle-finetune/dataset")
    parser.add_argument("--adapted-root", type=Path, default=ROOT.parent / ".audit/paddle-finetune/model-root")
    parser.add_argument("--output", type=Path, default=ROOT / "paddle-finetune/native-evaluation.json")
    parser.add_argument("--crops-only", action="store_true")
    args = parser.parse_args()
    original_root = ROOT / "paddle-models"
    os.environ["FOLITH_OCR_MODEL_DIR"] = str(original_root)
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(original_root / "paddle/3.7.0")
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    from paddleocr import TextRecognition
    from folith.ocr_paddle import PaddleOCRBackend
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    rows = [r for r in manifest["rows"] if r["split"] in {"validation", "test"}]
    output = {"scope": "page-disjoint held-out validation and test; no LLM cleanup",
              "crops": [], "pages": [], "status": "running"}
    for name, root in (("original", original_root), ("adapted", args.adapted_root)):
        directory = root / "paddle/3.7.0/official_models/arabic_PP-OCRv5_mobile_rec"
        model = TextRecognition(model_name="arabic_PP-OCRv5_mobile_rec", model_dir=str(directory),
                                device="cpu", enable_mkldnn=False, cpu_threads=2)
        for row in rows:
            result = next(iter(model.predict(str(args.dataset / row["image"]), batch_size=1)))
            text = result["rec_text"]
            output["crops"].append({"model": name, "split": row["split"],
                "page_index": row["page_index"], "line_index": row["line_index"],
                "reference": row["text"], "text": text,
                "character_errors": B.cer(row["text"], text), "word_errors": B.wer(row["text"], text)})
        del model
        save(args.output, output)
        print("Crop inference completed", name, flush=True)
    if not args.crops_only:
        from PIL import Image
        # Models live in separate roots. Each native adapter owns its own process.
        for name, root in (("original", original_root), ("adapted", args.adapted_root)):
            os.environ["FOLITH_OCR_MODEL_DIR"] = str(root)
            backend = PaddleOCRBackend("ara")
            try:
                backend.load(print)
                for page in sorted({row["page_index"] for row in rows}):
                    png = (ROOT / "pages" / f"page_{page:04d}" / "dpi350/page.png").read_bytes()
                    started = time.perf_counter()
                    result = backend.recognize_page(png, page_index=page, page_number=page + 1,
                                                    language="ara", dpi=350)
                    if result.error:
                        raise RuntimeError(result.error)
                    # Same PDF-point mapping as the renderer; no new rasterization.
                    with Image.open(io.BytesIO(png)) as image:
                        width, height = image.size
                    record = result.to_record(width * 72 / 350, height * 72 / 350)
                    save(args.output.parent / f"page-{page:04d}-{name}.json", record)
                    reference = "\n".join(r["text"] for r in manifest["rows"] if r["page_index"] == page)
                    text = region_text(record, manifest["corrected_evaluation_windows_pdf"][str(page)])
                    split = manifest["page_splits"][str(page)]
                    output["pages"].append({"model": name, "split": split, "page_index": page,
                        "reference": reference, "text": text, "recognition_s": time.perf_counter() - started,
                        "character_errors": B.cer(reference, text), "word_errors": B.wer(reference, text)})
                    save(args.output, output)
                    print("Page", page + 1, name, output["pages"][-1]["character_errors"]["cer"], flush=True)
            finally:
                backend.close()
        # Existing Tesseract output, same page images and corrected reference scope.
        for page in sorted({row["page_index"] for row in rows}):
            record = json.loads((ROOT / "engine-comparison" / f"page-{page:04d}-tesseract.json").read_text())
            reference = "\n".join(r["text"] for r in manifest["rows"] if r["page_index"] == page)
            text = region_text(record, manifest["corrected_evaluation_windows_pdf"][str(page)])
            output["pages"].append({"model": "tesseract_best", "split": manifest["page_splits"][str(page)],
                "page_index": page, "reference": reference, "text": text,
                "character_errors": B.cer(reference, text), "word_errors": B.wer(reference, text)})
    output["summary"] = {}
    for kind in ("crops", "pages"):
        output["summary"][kind] = {}
        for name in sorted({r["model"] for r in output[kind]}):
            output["summary"][kind][name] = {}
            for split in ("validation", "test"):
                selected = [r for r in output[kind] if r["model"] == name and r["split"] == split]
                if not selected:
                    continue
                c = {key: sum(r["character_errors"][key] for r in selected)
                     for key in ("sub", "del", "ins", "ref_len")}
                w = {key: sum(r["word_errors"][key] for r in selected)
                     for key in ("sub", "del", "ins", "ref_words")}
                output["summary"][kind][name][split] = {"cer": (c["sub"] + c["del"] + c["ins"]) / c["ref_len"],
                    "wer": (w["sub"] + w["del"] + w["ins"]) / w["ref_words"], "character_errors": c,
                    "count": len(selected)}
    output["status"] = "completed"
    save(args.output, output)
    print(json.dumps(output["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
