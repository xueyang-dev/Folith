"""Repeatable offline benchmark for the PDF ingestion/OCR cleanup path.

The benchmark intentionally uses a deterministic fake LLM.  It measures the
local raster/OCR work with the installed Tesseract runtime and uses a fixed
sleep to make remote-wait behavior visible without spending API credits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import subprocess
import threading
import time
import uuid
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - Windows
    resource = None

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF
    import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.performance import PipelineProfiler  # noqa: E402


def _rss_bytes() -> int:
    if resource is None:
        return 0
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss or 0)
    # macOS reports bytes; Linux reports KiB.
    return value if sys.platform == "darwin" else value * 1024


class _CpuMonitor:
    """Best-effort parent + child CPU sampler; no hard dependency on psutil."""

    def __init__(self) -> None:
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        try:
            import psutil  # type: ignore
        except ImportError:  # pragma: no cover - optional benchmark aid
            psutil = None
        self._psutil = psutil
        self._pid = os.getpid()

    def start(self) -> None:
        process = self._psutil.Process(self._pid) if self._psutil else None
        if process is not None:
            process.cpu_percent(None)

        def sample_with_ps() -> float:
            if process is not None:
                total = process.cpu_percent(None)
                for child in process.children(recursive=True):
                    try:
                        total += child.cpu_percent(None)
                    except self._psutil.Error:
                        continue
                return float(total)
            rows = subprocess.check_output(
                ["ps", "-axo", "pid=,ppid=,%cpu="],
                text=True, stderr=subprocess.DEVNULL,
            ).splitlines()
            values = {}
            children = {}
            for row in rows:
                parts = row.split()
                if len(parts) != 3:
                    continue
                pid, parent = int(parts[0]), int(parts[1])
                values[pid] = float(parts[2])
                children.setdefault(parent, []).append(pid)
            todo = [self._pid]
            descendants = set(todo)
            while todo:
                parent = todo.pop()
                for child in children.get(parent, []):
                    if child not in descendants:
                        descendants.add(child)
                        todo.append(child)
            return sum(values.get(pid, 0.0) for pid in descendants)

        def sample() -> None:
            while not self._stop.wait(0.25):
                try:
                    self.samples.append(sample_with_ps())
                except (OSError, ValueError, subprocess.SubprocessError,
                        self._psutil.Error if self._psutil else OSError):
                    return

        self._thread = threading.Thread(target=sample, name="benchmark-cpu", daemon=True)
        self._thread.start()

    def stop(self) -> float | None:
        if self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=2)
        return round(sum(self.samples) / len(self.samples), 2) if self.samples else None


def _page_blocks(page_index: int) -> list[str]:
    return [
        f"Folith benchmark page {page_index + 1}: scanned ingestion measures the local OCR path.",
        f"Page {page_index + 1} first paragraph is deliberately complete so the deterministic confidence gate can bypass it.",
        f"Page {page_index + 1} second paragraph contains enough words to make rasterization and OCR representative.",
        f"Page {page_index + 1} final paragraph is kept separate to test page ordering and segment construction.",
    ]


def _page_order_ok(markers: list[int], page_count: int) -> bool:
    expected = set(range(1, page_count + 1))
    return bool(markers) and markers == sorted(markers) and set(markers) == expected


def _build_pdf(pages: int, *, scanned: bool) -> bytes:
    document = fitz.open()
    try:
        for page_index in range(pages):
            page = document.new_page(width=595, height=842)
            y = 90
            blocks = _page_blocks(page_index)
            if scanned:
                # Render a text-only staging page and insert only its raster
                # into the output PDF.  The output therefore has no text layer.
                staging = fitz.open()
                try:
                    staging_page = staging.new_page(width=595, height=842)
                    for block in blocks:
                        staging_page.insert_textbox(
                            fitz.Rect(58, y, 535, y + 70), block,
                            fontsize=12, fontname="helv", color=(0, 0, 0),
                        )
                        y += 105
                    pix = staging_page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    # JPEG keeps the generated fixture small while preserving
                    # the real image-only PDF/OCR path.  PNG inserted through
                    # PyMuPDF is decoded to a large raw image stream on save.
                    page.insert_image(
                        page.rect, stream=pix.tobytes("jpeg", jpg_quality=75)
                    )
                finally:
                    staging.close()
            else:
                for block in blocks:
                    page.insert_textbox(
                        fitz.Rect(58, y, 535, y + 70), block,
                        fontsize=12, fontname="helv", color=(0, 0, 0),
                    )
                    y += 105
        return document.tobytes()
    finally:
        document.close()


def _fake_llm(delay_s: float, calls: list[float]):
    def call(_provider, _api_key, _model, _system, user, **kwargs):
        calls.append(time.perf_counter())
        if delay_s:
            time.sleep(delay_s)
        response_format = kwargs.get("response_format") or {}
        schema = (response_format.get("json_schema") or {}).get("schema") or {}
        segments = (schema.get("properties") or {}).get("segments") or {}
        expected = int(segments.get("maxItems") or 0)
        rows = []
        for index in range(expected):
            match = re.search(rf"^{index}\.\s(.*)$", user, flags=re.MULTILINE)
            text = match.group(1).strip() if match else f"benchmark item {index}"
            rows.append({"index": index, "text": text, "break_after": True})
        return json.dumps({"segments": rows}, ensure_ascii=False)

    return call


def _run_cleanup(
    paragraphs: list[str], confidences, *, mode: str, output_dir: Path,
    delay_s: float, force_uncertain: bool = False, profiler=None,
    checkpoint_dir: Path | None = None,
) -> dict:
    calls: list[float] = []
    if profiler is None:
        profiler = PipelineProfiler(output_dir / "performance.json", run_id=mode)
    if force_uncertain:
        confidence_values = [0.0] * len(paragraphs)
    else:
        confidence_values = confidences
    started = time.perf_counter()
    cleaned, metadata, warnings = core.cleanup_source_paragraphs(
        paragraphs, "benchmark", "offline", "fake-model",
        call_llm_fn=_fake_llm(delay_s, calls),
        parallelism=1 if mode == "serial" else 4,
        max_batch_items=4,
        confidence_by_index=confidence_values,
        checkpoint_dir=checkpoint_dir or output_dir,
        max_retries=0,
        profiler=profiler,
    )
    # Measure the actual CAT contract as well as the paragraph cleanup output.
    # The benchmark fixture is a PDF, so it exercises the default sentence mode.
    segments, segmentation = core.segment_source_paragraphs(
        cleaned, state={"filename": "benchmark.pdf"})
    segment_stage = profiler.start_stage(
        "segment_build", concurrency=1, item_count=len(segments),
        metadata={"source": "benchmark", "mode": segmentation.get("mode"),
                  "paragraph_count": len(cleaned)},
    )
    for index in range(len(segments)):
        segment_stage.item(index, segment_stage.started_monotonic,
                           metadata={"kind": "segment"})
    segment_stage.finish()
    snapshot = profiler.persist()
    return {
        "wall_time_s": round(time.perf_counter() - started, 4),
        "api_requests": len(calls),
        "llm_bypass_ratio": metadata.get("llm_bypass_ratio"),
        "llm_input_count": metadata.get("llm_input_count"),
        "warnings": warnings,
        "paragraph_count": len(paragraphs),
        "output_count": len(cleaned),
        "segment_count": len(segments),
        "segment_sha256": hashlib.sha256(
            json.dumps(segments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "duplicate_segment_count": len(segments) - len(set(segments)),
        "segmentation": segmentation,
        "output_sha256": hashlib.sha256(
            json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "duplicate_output_count": len(cleaned) - len(set(cleaned)),
        "performance": snapshot,
    }


def _run_case(
    name: str, pdf_bytes: bytes, *, scanned: bool, output_dir: Path,
    delay_s: float, ocr_workers: int, ocr_queue_size: int, run_token: str,
) -> dict:
    modes = {}
    for mode in ("serial", "optimized"):
        mode_dir = output_dir / name / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_dir = mode_dir / "checkpoints" / run_token
        profiler = PipelineProfiler(mode_dir / "performance.json", run_id=f"{name}-{mode}")
        monitor = _CpuMonitor()
        rss_before = _rss_bytes()
        started = time.perf_counter()
        monitor.start()
        paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
            f"{name}.pdf", pdf_bytes, ocr_max_pages=None,
            profiler=profiler, checkpoint_dir=checkpoint_dir,
            ocr_workers=ocr_workers if mode == "optimized" else 1,
            ocr_queue_size=ocr_queue_size if mode == "optimized" else 1,
        )
        confidences = (report.get("ocr") or {}).get("paragraph_confidences")
        # The serial run models the observed implementation: every source item
        # is considered uncertain because it has no confidence gate.
        cleanup = _run_cleanup(
            paragraphs, confidences if mode == "optimized" and scanned else None,
            mode=mode, output_dir=mode_dir, delay_s=delay_s, profiler=profiler,
            checkpoint_dir=checkpoint_dir,
        )
        snapshot = profiler.persist()
        average_cpu = monitor.stop()
        modes[mode] = {
            "total_wall_time_s": round(time.perf_counter() - started, 4),
            "rss_peak_bytes": max(rss_before, _rss_bytes()),
            "average_cpu_percent": average_cpu,
            "ocr_pages_per_s": round(
                (report.get("ocr") or {}).get("completed_pages", 0)
                / max(0.0001, (snapshot.get("stages", {}).get("ocr") or {}).get("wall_time_s", 0.0)),
                4,
            ) if report.get("ocr") else None,
            "warnings": warnings,
            "extracted_paragraphs": len(paragraphs),
            "extracted_sha256": hashlib.sha256(
                json.dumps(paragraphs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "page_markers": [
                int(match.group(1))
                for paragraph in paragraphs
                for match in [re.search(r"\bpage\s+(\d+)\b", paragraph, flags=re.I)]
                if match
            ],
            "page_order": list((report.get("ocr") or {}).get("page_order") or range(
                1, int((report.get("extracted") or {}).get("pages") or 0) + 1
            )),
            "ocr": report.get("ocr") or {},
            "cleanup": cleanup,
            "performance": snapshot,
        }

    # Force all items through cleanup once to isolate the LLM wait behavior
    # from the confidence-gate effect.  This is still offline and deterministic.
    probe_dir = output_dir / name / "concurrency_probe"
    # ``paragraphs`` is the last extracted mode's actual output.  Reuse that
    # corpus so the probe has the same batch count as the fixture instead of a
    # toy two-batch sample.
    probe_paragraphs = list(paragraphs or _page_blocks(0))
    probe_before = _run_cleanup(
        probe_paragraphs, None,
        mode="serial", output_dir=probe_dir / "serial", delay_s=delay_s,
        force_uncertain=True, checkpoint_dir=probe_dir / "serial" / run_token,
    )
    probe_after = _run_cleanup(
        probe_paragraphs, None,
        mode="optimized", output_dir=probe_dir / "optimized", delay_s=delay_s,
        force_uncertain=True, checkpoint_dir=probe_dir / "optimized" / run_token,
    )
    serial_mode = modes["serial"]
    optimized_mode = modes["optimized"]
    page_count = int((serial_mode["ocr"] or {}).get("page_count") or len(
        serial_mode["page_order"]
    ))
    invariants = {
        "extracted_order_and_text_equal": (
            serial_mode["extracted_sha256"] == optimized_mode["extracted_sha256"]
        ),
        "cleanup_order_and_text_equal": (
            serial_mode["cleanup"]["output_sha256"]
            == optimized_mode["cleanup"]["output_sha256"]
        ),
        "segment_order_and_text_equal": (
            serial_mode["cleanup"]["segment_sha256"]
            == optimized_mode["cleanup"]["segment_sha256"]
        ),
        "serial_page_order": _page_order_ok(serial_mode["page_order"], page_count),
        "optimized_page_order": _page_order_ok(optimized_mode["page_order"], page_count),
        "serial_duplicate_output_count": serial_mode["cleanup"]["duplicate_output_count"],
        "optimized_duplicate_output_count": optimized_mode["cleanup"]["duplicate_output_count"],
        "serial_duplicate_segment_count": serial_mode["cleanup"]["duplicate_segment_count"],
        "optimized_duplicate_segment_count": optimized_mode["cleanup"]["duplicate_segment_count"],
    }
    return {"scanned": scanned, "modes": modes, "invariants": invariants,
            "forced_uncertain_concurrency_probe": {
                "serial": probe_before, "optimized": probe_after,
            }}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("tmp/pdf-ingestion-benchmark"))
    parser.add_argument("--pages-20", type=int, default=20)
    parser.add_argument("--pages-100", type=int, default=120,
                        help="page count for the 100+ page scanned fixture")
    parser.add_argument("--delay", type=float, default=0.02,
                        help="fixed seconds per fake LLM request")
    parser.add_argument("--ocr-workers", type=int, default=2)
    parser.add_argument("--ocr-queue-size", type=int, default=4)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_token = uuid.uuid4().hex
    fixtures = {
        "text_pdf": (1, _build_pdf(1, scanned=False)),
        "scanned_20p": (args.pages_20, _build_pdf(args.pages_20, scanned=True)),
        "scanned_120p": (args.pages_100, _build_pdf(args.pages_100, scanned=True)),
    }
    results = {
        "benchmark": "pdf_ingestion_benchmark",
        "fake_llm_delay_s": args.delay,
        "ocr_runtime": {
            "backend": "tesseract",
            "device": "cpu",
            "workers": args.ocr_workers,
            "queue_size": args.ocr_queue_size,
        },
        "fixtures": {
            name: _run_case(
                name, data, scanned=name.startswith("scanned"), output_dir=out,
                delay_s=args.delay, ocr_workers=args.ocr_workers,
                ocr_queue_size=args.ocr_queue_size, run_token=run_token,
            )
            for name, (_pages, data) in fixtures.items()
        },
    }
    result_path = out / "benchmark.json"
    result_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, case in results["fixtures"].items():
        print(name)
        for mode, values in case["modes"].items():
            cleanup = values["cleanup"]
            print(
                f"  {mode:9s} wall={values['total_wall_time_s']:.3f}s "
                f"ocr_pages/s={values['ocr_pages_per_s'] or 0:.2f} "
                f"llm_wall={(values['performance'].get('stages', {}).get('llm_cleanup') or {}).get('wall_time_s', 0):.3f}s "
                f"requests={cleanup['api_requests']} "
                f"bypass={cleanup['llm_bypass_ratio']:.1%}"
            )
        probe = case["forced_uncertain_concurrency_probe"]
        print(
            f"  probe     serial={probe['serial']['wall_time_s']:.3f}s "
            f"optimized={probe['optimized']['wall_time_s']:.3f}s "
            f"requests={probe['optimized']['api_requests']}"
        )
    print(f"benchmark_json={result_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
