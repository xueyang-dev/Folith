"""Same RGB page bytes -> both production adapters; no LLM or image cleanup.

Run with the optional Paddle environment from the repository root:
  FOLITH_OCR_MODEL_DIR=benchmark/paddle-models .venv/paddle/bin/python benchmark/run_engine_comparison.py
Results are persisted after every page so a failed run remains inspectable.
"""
import argparse
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import threading
import time

import pymupdf
import psutil

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import bench_lib as B
from folith.ocr_backends import TesseractBackend, PaddleOCRBackend
from folith.ocr_tesseract import _ocr_page_with_tesseract


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Debug evidence is portable across checkouts; keep model paths relative.
    text = json.dumps(value, ensure_ascii=False, indent=2).replace(str(ROOT.parent) + "/", "")
    path.write_text(text)


class MemorySample:
    def __init__(self, children=False, exclude_pid=None):
        self.children = children
        self.exclude_pid = exclude_pid
        self.peak = 0
        self.done = threading.Event()
        self.process = psutil.Process()
    def __enter__(self):
        def sample():
            while not self.done.is_set():
                try:
                    processes = self.process.children(recursive=True) if self.children else [self.process]
                    self.peak = max(self.peak, sum(p.memory_info().rss for p in processes
                                                 if p.pid != self.exclude_pid and p.is_running()))
                except psutil.Error:
                    pass
                self.done.wait(.02)
        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()
        return self
    def __exit__(self, *args):
        self.done.set()
        self.thread.join()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=os.environ.get("FOLITH_BENCHMARK_PDF", str(Path.home() / "Downloads" / "Alam_al-din_(IA_alamaldin00alimuoft).pdf")))
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    selected = json.loads((ROOT / "select_pages.json").read_text())["pages"]
    indices = [p["index"] for p in selected]
    indices += [i + 1 for i in indices]
    indices = indices[:args.count]
    windows = json.loads((ROOT / "gt_windows.json").read_text())
    # Exact same rendered inputs; the engines retain their native inference resizing.
    os.environ["TESSDATA_PREFIX"] = str(ROOT / "tessdata_best")
    version = subprocess.check_output(["tesseract", "--version"], text=True).splitlines()[0]
    tess = TesseractBackend(shutil.which("tesseract"), version, _ocr_page_with_tesseract, psm=6)
    paddle = PaddleOCRBackend("ara")
    output = ROOT / "engine-comparison"
    summary = {"source": str(Path(args.pdf).resolve()), "dpi": 350, "psm": 6,
               "preprocessing": "RGB rendering only; both engines receive identical PNG bytes",
               "paddle_workers": 1, "gpu": False, "vram": None,
               "ground_truth_scope": "10 saved passages; uncertain historical orthography documented in gt_meta.json",
               "rows": [], "status": "running"}
    started = time.perf_counter()
    with MemorySample(children=True) as memory:
        paddle.load(print)
    summary["paddle_model_load_s"] = time.perf_counter() - started
    summary["paddle_model_load_peak_rss_bytes"] = memory.peak
    summary["engines"] = {"tesseract": tess.runtime_info("ara").to_dict(), "paddle": paddle.runtime_info("ara").to_dict()}
    save(output / "results.json", summary)
    with pymupdf.open(args.pdf) as doc:
        for index in indices:
            page = doc[index]
            cached = ROOT / "pages" / f"page_{index:04d}" / "dpi350" / "page.png"
            render_start = time.perf_counter()
            png = cached.read_bytes() if cached.exists() else page.get_pixmap(dpi=350, alpha=False).tobytes("png")
            render_s = time.perf_counter() - render_start
            for name, engine in [("tesseract", tess), ("paddle", paddle)]:
                start = time.perf_counter()
                with MemorySample(children=True,
                                  exclude_pid=paddle._process.pid if name == "tesseract" else None) as memory:
                    result = engine.recognize_page(png, page_index=index, page_number=index + 1, language="ara", dpi=350)
                elapsed = time.perf_counter() - start
                record = result.to_record(page.rect.width, page.rect.height)
                save(output / f"page-{index:04d}-{name}.json", record)
                row = {"engine": name, "page_index": index, "pdf_page": index + 1,
                       "render_s": render_s, "recognition_s": elapsed, "peak_rss_bytes": memory.peak,
                       "memory_scope": "selected engine native worker(s), excluding the other loaded engine",
                       "error": result.error, "blocks": len(result.blocks)}
                if str(index) in windows:
                    y0, y1 = windows[str(index)]
                    # The existing benchmark selects whole word rows by their centre.
                    pieces = []
                    for block in record["blocks"]:
                        for line in block["lines"]:
                            if name == "tesseract":
                                words = [word["text"] for word in line["words"]
                                         if 28 <= (word["left"] + word["right"]) * 36 / 350 <= 356
                                         and y0 <= (word["top"] + word["bottom"]) * 36 / 350 <= y1]
                                if words:
                                    pieces.append(" ".join(words))
                            elif y0 <= (line["top"] + line["bottom"]) * 36 / 350 <= y1:
                                pieces.append(line["text"])
                    text = "\n".join(pieces)
                    gt = (ROOT / "ground_truth" / f"page_{index:04d}.txt").read_text()
                    row["character_errors"] = B.cer(gt, text)
                    row["word_errors"] = B.wer(gt, text)
                    row.update(cer=row["character_errors"]["cer"], wer=row["word_errors"]["wer"])
                    row.update(B.line_metrics(gt, text))
                    row.update(B.rtl_order_error(gt, text))
                    row["cer_folded"] = B.cer_folded(gt, text)
                    (output / f"page-{index:04d}-{name}-gt-region.txt").write_text(text)
                summary["rows"].append(row)
                save(output / "results.json", summary)
                print(name, index, "seconds", round(elapsed, 2), "CER", row.get("cer"), "RSS MiB", round(memory.peak / 2**20), flush=True)
    for name in ["tesseract", "paddle"]:
        rows = [r for r in summary["rows"] if r["engine"] == name]
        measured = [r for r in rows if "cer" in r]
        summary[name] = {"pages": len(rows), "mean_cer": sum(r["cer"] for r in measured) / len(measured),
                         "mean_wer": sum(r["wer"] for r in measured) / len(measured),
                         "first_page_s": rows[0]["recognition_s"],
                         "steady_state_mean_s": sum(r["recognition_s"] for r in rows[1:]) / max(1, len(rows) - 1),
                         "batch_recognition_s": sum(r["recognition_s"] for r in rows),
                         "peak_rss_bytes": max(r["peak_rss_bytes"] for r in rows),
                         "missing_lines": sum(r["missing_lines"] for r in measured),
                         "spurious_lines": sum(r["spurious_lines"] for r in measured),
                         "reading_order_failures": sum(r["rtl_order_error_count"] for r in measured)}
    summary["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary["status"] = "completed"
    paddle.close()
    save(output / "results.json", summary)


if __name__ == "__main__":
    main()
