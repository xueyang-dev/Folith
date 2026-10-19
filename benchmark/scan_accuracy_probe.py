"""Reproduce the issue #8 scanned-page recognition test case.

The bug report (GitHub issue #8) records four recognition errors on one page of
the public-domain scan of *علم الدين* at 150 DPI, read by a vision model through
the saved translation connection:

    كافعامل  <- كافعالهم   nonword
    احواله   <- احوالهم    plausible but incorrect word (hardest)
    كال      <- كمال       nonword
    وانكرة   <- والفكرة    nonword (the metaphor's subject is lost)

This script renders that page at a chosen DPI and runs the **production** online
OCR path (``folith.ocr_online.OnlineOCRBackend``) against the saved translation
connection, then reports:

* whether each known misread (and its correct form) appears in the output, so the
  reported behaviour is reproducible without a full human reference;
* the per-page provider usage (tokens) and wall-clock time, the cost basis the
  issue asks for;
* structural facts (paragraph count, page-number-like lines).

It is measurement-only.  It never writes into job state, never reuses the page
cache and never mutates application defaults.  A human reference file can be
passed with ``--reference`` to add CER/WER; without it, only the checkpoint and
cost facts are reported, and the script says so.

Usage
-----
    # render only, no request, to inspect the exact input
    python benchmark/scan_accuracy_probe.py render --page 13 --dpi 150

    # reproduce the report (needs saved credentials; issues a billable request)
    python benchmark/scan_accuracy_probe.py run --page 13 --dpi 150

    # report on a previously saved result
    python benchmark/scan_accuracy_probe.py report

The default PDF path can be overridden with ``FOLITH_BENCHMARK_PDF`` or
``--pdf``.  The archive.org item is ``alamaldin00alimuoft``; PDF page 13 carries
the printed page number ١٣.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))
import bench_lib as B  # noqa: E402

DEFAULT_PDF = os.environ.get(
    "FOLITH_BENCHMARK_PDF",
    str(Path.home() / "Downloads" / "Alam_al-din_(IA_alamaldin00alimuoft).pdf"))
OUT = ROOT / "benchmark/scan-accuracy-probe"
SCRATCH = ROOT / ".audit/scan-accuracy-probe"

#: The four errors from the issue, verbatim.  ``kind`` matters: the proposal's
#: acceptance turns on whether an option can catch the ``plausible`` class.
KNOWN_MISREADS: List[Dict[str, str]] = [
    {"read": "كافعامل", "original": "كافعالهم", "kind": "nonword"},
    {"read": "احواله", "original": "احوالهم", "kind": "plausible"},
    {"read": "كال", "original": "كمال", "kind": "nonword"},
    {"read": "وانكرة", "original": "والفكرة", "kind": "nonword"},
]

#: Printed page number on PDF page 13 of this scan; it must not enter the body.
_PAGE_NUMBER_RE = re.compile(r"^\s*[٠-٩0-9]{1,4}\s*$")


def normalize_token(text: str) -> str:
    """Diacritic-insensitive comparison form for a single token."""
    return B.normalize(text, strip_diacritics=True)


def _tokens(text: str) -> List[str]:
    """Whole-word tokens, punctuation-free, for exact token matching.

    Whole-word matching matters here: the reported misread ``احواله`` is a prefix
    of the correct ``احوالهم`` (and ``كال`` sits inside ``كمال``), so a substring
    test would call a fully corrected page a "reproduction".  A model that
    re-adds diacritics is still matched because tokens are diacritic-folded.
    """
    cleaned = B.normalize(text, strip_diacritics=True, strip_punct=True)
    return cleaned.split()


def checkpoint_hits(text: str) -> Dict[str, Any]:
    """Which known misreads / correct forms appear in one page's text.

    Matching is exact whole-word on a diacritic-insensitive normalization.  This
    is a targeted reproduction check, not a full accuracy metric.
    """
    tokens = set(_tokens(text))
    rows = []
    for entry in KNOWN_MISREADS:
        rows.append({
            **entry,
            "read_found": normalize_token(entry["read"]) in tokens,
            "original_found": normalize_token(entry["original"]) in tokens,
        })
    reproduced = sum(1 for row in rows if row["read_found"] and not row["original_found"])
    corrected = sum(1 for row in rows if row["original_found"] and not row["read_found"])
    return {
        "checkpoints": rows,
        "misread_reproduced": reproduced,
        "correct_form_only": corrected,
        "either_form_present": sum(
            1 for row in rows if row["read_found"] or row["original_found"]),
        "neither_form_present": sum(
            1 for row in rows if not row["read_found"] and not row["original_found"]),
        "total": len(rows),
        "note": ("targeted whole-word check on a fixed four-word list; a miss can mean "
                 "the word was fixed, dropped, split across tokens, or simply absent"),
    }


def structure_summary(lines: List[str]) -> Dict[str, Any]:
    """Paragraph count from ``break_after`` is not available after re-joining.

    Callers pass the raw line/paragraph list; here we only report counts and
    whether a page-number-like line was kept.  Structural correctness is
    secondary to the error reproduction, so this stays descriptive.
    """
    non_empty = [line for line in lines if str(line).strip()]
    page_numbers = [line for line in non_empty if _PAGE_NUMBER_RE.match(str(line))]
    return {"line_count": len(non_empty),
            "page_number_lines": len(page_numbers),
            "page_number_excluded": not page_numbers}


def reference_metrics(reference: Optional[str], hypothesis: str) -> Optional[Dict[str, Any]]:
    if not reference:
        return None
    return {"character_errors": B.cer(reference, hypothesis),
            "word_errors": B.wer(reference, hypothesis),
            "folded_cer": B.cer_folded(reference, hypothesis)}


def render_page_png(pdf_path: str, page_index: int, dpi: int) -> bytes:
    import pymupdf  # lazy: only needed when rendering

    with pymupdf.open(pdf_path) as document:
        page = document[page_index]
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("png")


def _resolve_online_config(model_override: str = "", max_tokens: Optional[int] = None):
    """Build the online OCR config from the saved translation connection."""
    from folith.engine import ocr_config

    config, error = ocr_config.resolve_ocr_online_config()
    if error:
        raise RuntimeError(f"{error.get('error')}: {error.get('message')}")
    if model_override or max_tokens:
        from dataclasses import replace
        config = replace(
            config,
            model=model_override or config.model,
            max_tokens=int(max_tokens) if max_tokens else config.max_tokens).normalized()
    return config


def run_online(png_bytes: bytes, *, page_index: int, page_number: int, language: str,
               dpi: int, model_override: str = "", max_tokens: Optional[int] = None):
    """One production online-OCR page call; returns ``(lines, evidence, config)``."""
    from folith.ocr_online import OnlineOCRBackend

    config = _resolve_online_config(model_override, max_tokens)
    backend = OnlineOCRBackend(config)
    try:
        evidence = backend.recognize_page(
            png_bytes, page_index=page_index, page_number=page_number,
            language=language, dpi=dpi)
    finally:
        backend.close()
    if evidence.error:
        raise RuntimeError(evidence.error)
    lines = [block.text for block in evidence.blocks if str(block.text).strip()]
    return lines, evidence, config


def _cost_from_evidence(evidence) -> Dict[str, Any]:
    request = dict(getattr(evidence, "request", {}) or {})
    usage = request.get("usage") or {}
    return {
        "reported": bool(usage.get("reported")),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "total_tokens": usage.get("total_tokens"),
        # The protocol does not return a money amount; it must stay unknown.
        "amount": None,
        "wall_seconds": float((getattr(evidence, "timings", {}) or {}).get("recognition_s") or 0.0),
        "attempt_count": request.get("attempt_count"),
        "finish_reason": request.get("finish_reason"),
    }


def _record_path(page_number: int, dpi: int, model: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model or "model")
    return OUT / f"page-{page_number:04d}-{dpi}dpi-{safe}.json"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def command_render(args) -> None:
    png = render_page_png(args.pdf, args.page - 1, args.dpi)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    dest = SCRATCH / f"page-{args.page:04d}-{args.dpi}dpi.png"
    dest.write_bytes(png)
    print(json.dumps({"image": str(dest.relative_to(ROOT)), "dpi": args.dpi,
                      "bytes": len(png)}, ensure_ascii=False))


def command_run(args) -> None:
    from PIL import Image
    import io

    if args.image:
        png = Path(args.image).read_bytes()
    else:
        png = render_page_png(args.pdf, args.page - 1, args.dpi)
    with Image.open(io.BytesIO(png)) as image:
        image_size = [image.width, image.height]

    lines, evidence, config = run_online(
        png, page_index=args.page - 1, page_number=args.page, language=args.language,
        dpi=args.dpi, model_override=args.model, max_tokens=args.max_tokens)

    reference = Path(args.reference).read_text(encoding="utf-8") if args.reference else None
    text = "\n".join(lines)
    record = {
        "issue": 8,
        "pdf": args.pdf,
        "page_number": args.page,
        "page_index": args.page - 1,
        "dpi": args.dpi,
        "language": args.language,
        "image_px": image_size,
        "provider": config.provider,
        "provider_model": config.model,
        "prompt_version": config.prompt_version,
        "reasoning_mode": config.reasoning_mode,
        "connection_owner": config.credential_owner,
        "lines": lines,
        "text": text,
        "structure": structure_summary(lines),
        "checkpoints": checkpoint_hits(text),
        "cost": _cost_from_evidence(evidence),
        "metrics": reference_metrics(reference, text),
        "reference_used": bool(reference),
        "limits": [
            "Targeted four-word checkpoint reproduction, not a full-page accuracy score.",
            "Provider token usage is saved; the protocol returns no money amount, so cost is not converted to currency.",
        ],
    }
    dest = _record_path(args.page, args.dpi, config.model)
    _write(dest, record)
    print(json.dumps({"result": str(dest.relative_to(ROOT)),
                      "checkpoints": record["checkpoints"]["misread_reproduced"],
                      "total": record["checkpoints"]["total"],
                      "cost": record["cost"]}, ensure_ascii=False, indent=2))


def command_report(args) -> None:
    records = sorted(OUT.glob("page-*.json")) if OUT.exists() else []
    if not records:
        print("no saved results under", OUT.relative_to(ROOT))
        return
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        checkpoints = record.get("checkpoints", {})
        cost = record.get("cost", {})
        print(f"{path.name}: dpi={record.get('dpi')} model={record.get('provider_model')} "
              f"misread_reproduced={checkpoints.get('misread_reproduced')}/"
              f"{checkpoints.get('total')} tokens={cost.get('total_tokens')} "
              f"seconds={cost.get('wall_seconds')}")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("render", "run", "report"))
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="Archive.org scan PDF path")
    parser.add_argument("--image", default="", help="Use this page image instead of rendering the PDF")
    parser.add_argument("--page", type=int, default=13, help="1-based PDF page (default 13)")
    parser.add_argument("--dpi", type=int, default=150, help="Render DPI (default 150, the reported case)")
    parser.add_argument("--language", default="ar", help="Declared source language (default ar)")
    parser.add_argument("--model", default="", help="Override the saved connection's model")
    parser.add_argument("--max-tokens", type=int, default=None, help="Override the completion budget")
    parser.add_argument("--reference", default="", help="Optional human-read page text for CER/WER")
    args = parser.parse_args(argv)
    if args.action == "render":
        command_render(args)
    elif args.action == "run":
        command_run(args)
    else:
        command_report(args)


if __name__ == "__main__":
    main()
