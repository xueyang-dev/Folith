"""Real CPU inference on visually checked, clean English and mixed Arabic pages."""
import json
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import bench_lib as B
from folith.ocr_paddle import PaddleOCRBackend


def save(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2).replace(str(ROOT.parent) + "/", "")
    path.write_text(text)


def _offline_worker(connection, language):
    import socket
    def deny(*args, **kwargs):
        raise RuntimeError("Network disabled for offline OCR verification")
    socket.socket.connect = deny
    socket.create_connection = deny
    from folith.ocr_paddle import _paddle_worker
    _paddle_worker(connection, language)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "paddle-smoke")
    args = parser.parse_args()
    if args.offline:
        from folith import ocr_paddle
        ocr_paddle._paddle_worker = _offline_worker
    root = ROOT / "paddle-smoke"
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, language in [("arabic-mixed", "ara+eng"), ("english", "eng")]:
        backend = PaddleOCRBackend(language)
        try:
            if args.offline:
                assert backend.status()["state"] == "available", "Offline test requires cached models"
            started = time.perf_counter()
            backend.load(print)
            load_s = time.perf_counter() - started
            page = backend.recognize_page((root / (name + ".png")).read_bytes(),
                page_index=0, page_number=1, language=language, dpi=180)
            record = page.to_record(595, 600)
            save(output / (name + "-evidence.json"), record)
            text = "\n".join(b.text for b in page.blocks)
            (output / (name + "-ocr.txt")).write_text(text)
            gt = (root / (name + "-gt.txt")).read_text()
            row = {"sample": name, "model_load_s": load_s, "error": page.error,
                   "offline_network_blocked": args.offline,
                   "text": text, "cer": B.cer(gt, text), "wer": B.wer(gt, text),
                   "line_metrics": B.line_metrics(gt, text), "order": B.rtl_order_error(gt, text),
                   "timings": page.timings, "engine": page.engine}
            rows.append(row)
            save(output / ("results-offline.json" if args.offline else "results.json"), rows)
            print(name, json.dumps(row, ensure_ascii=False), flush=True)
        finally:
            backend.close()
    assert all(not row["error"] for row in rows)
    assert rows[1]["cer"]["cer"] < .10, "Clean English OCR is not functional"


if __name__ == "__main__":
    main()
