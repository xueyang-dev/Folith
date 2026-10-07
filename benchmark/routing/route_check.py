"""Route ledger: which path does each benchmark document actually take?

Runs the real ingestion entry with a *recording* visual-understanding provider
and a *forbidden* local OCR func, so every claim about routing is observed
rather than asserted in prose.  No request leaves the machine.

Also answers the cost question: how many documents and pages move from the
native text path to visual understanding because of the fixed gate.

Usage::

    ./venv/bin/python benchmark/routing/route_check.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE / "out"
MANIFEST = HERE / "samples.json"


class RecordingProvider:
    """A visual-understanding provider that records the pages it was asked for."""

    cache_identity = None

    def __init__(self):
        self.pages = []

    def understand_page(self, page):
        from folith.document_ir import PageUnderstanding, SemanticBlock
        self.pages.append(page.page_id)
        return PageUnderstanding(
            [SemanticBlock("paragraph", "Text read by visual understanding.",
                           region_ids=[f"{page.page_id}-b0"], direction="rtl",
                           language="ar")],
            "recording-stub")


def _forbidden(name, calls):
    def _raise(*_args, **_kwargs):
        calls.append(name)
        raise AssertionError(f"{name} must not run on this document")
    return _raise


def _stub_spatial(page):
    from folith.spatial_index import SpatialTextIndex
    return SpatialTextIndex(page.page_id)


def _quality_before(quality, path):
    """The verdict the previous phase produced for this document.

    Before the fix the order evidence was consulted only for a declared
    language, so simulating "no declared language, no script detection" gives
    the old verdict exactly.
    """
    original = quality.ARABIC_SCRIPT_MIN_LETTERS
    try:
        quality.ARABIC_SCRIPT_MIN_LETTERS = 10 ** 9
        return quality.assess_pdf_text_layer(path.read_bytes(),
                                             source_language=None)
    finally:
        quality.ARABIC_SCRIPT_MIN_LETTERS = original


def run() -> dict:
    import core
    from folith import text_layer_quality as quality

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ledger = {"documents": [], "totals": {}}
    before_vlm = after_vlm = 0
    pages_before = pages_after = 0
    try:
        import pymupdf
        for group, entries in manifest["groups"].items():
            for entry in entries:
                path = HERE / entry["file"]
                if not path.is_file():
                    raise SystemExit(
                        f"missing {entry['file']}; run prepare_samples.py first")
                data = path.read_bytes()
                with pymupdf.open(stream=data, filetype="pdf") as document:
                    page_count = len(document)

                before = _quality_before(quality, path)
                after = quality.assess_pdf_text_layer(data, source_language=None)

                provider = RecordingProvider()
                ocr_calls: list[str] = []
                original_ocr = core._ocr_pdf_text_with_warnings
                core._ocr_pdf_text_with_warnings = _forbidden("local_ocr", ocr_calls)
                started = time.perf_counter()
                try:
                    _paragraphs, _warnings, report = (
                        core.extract_document_paragraphs_with_report(
                            entry["file"], data,
                            source_language=None,
                            understanding_provider=provider,
                            spatial_engine=_stub_spatial,
                            checkpoint_dir=None,
                            ocr_mode="auto",
                        ))
                finally:
                    core._ocr_pdf_text_with_warnings = original_ocr
                elapsed = time.perf_counter() - started

                if report.get("semantic_source") is None:
                    raise SystemExit(
                        f"{entry['file']} was not handled as a PDF; "
                        f"report={ {k: report.get(k) for k in ('format', 'status')} }")
                vlm_called = bool(provider.pages)
                row = {
                    "id": entry["id"],
                    "group": group,
                    "expect": entry["expect"],
                    "pages": page_count,
                    "quality_before": before["status"],
                    "quality_after": after["status"],
                    "route_before": ("vlm" if before["status"] == "rejected"
                                     or before["status"] == "absent" else "native"),
                    "route_after": report.get("semantic_source"),
                    "native_text_used": report.get("semantic_source") == "native_pdf",
                    "vlm_provider_called": vlm_called,
                    "vlm_pages": list(provider.pages),
                    "local_ocr_called": bool(ocr_calls),
                    "ocr_used": bool(report.get("ocr_used")),
                    "ocr_trigger": report.get("ocr_trigger"),
                    "rejection_reasons": list(
                        report.get("pdf_text_layer_rejection_reasons") or []),
                    "extraction_seconds": round(elapsed, 3),
                }
                ledger["documents"].append(row)
                if row["route_before"] == "vlm":
                    before_vlm += 1
                    pages_before += page_count
                if report.get("semantic_source") in {"vlm", "mixed"}:
                    after_vlm += 1
                    pages_after += page_count
                print(f"{entry['id']:34s} {before['status']:9s} -> "
                      f"{after['status']:9s}  route={report.get('semantic_source'):9s}"
                      f" vlm_called={vlm_called!s:5s} ocr={bool(ocr_calls)!s:5s}")
    finally:
        pass

    ledger["totals"] = {
        "documents": len(ledger["documents"]),
        "documents_routed_to_vlm_before": before_vlm,
        "documents_routed_to_vlm_after": after_vlm,
        "pages_routed_to_vlm_before": pages_before,
        "pages_routed_to_vlm_after": pages_after,
        "extra_documents_routed_to_vlm": after_vlm - before_vlm,
        "extra_pages_routed_to_vlm": pages_after - pages_before,
        "local_ocr_calls": sum(1 for row in ledger["documents"]
                               if row["local_ocr_called"]),
        "vlm_calls": sum(len(row["vlm_pages"]) for row in ledger["documents"]),
        "expected_extra_visual_requests": pages_after - pages_before,
    }
    ledger["notes"] = (
        "One visual request per newly routed page, per the ingestion contract; "
        "each request carries the page image at 150 DPI. No translation call is "
        "made by extraction."
    )
    return ledger


def main() -> int:
    ledger = run()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "route-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    totals = ledger["totals"]
    print()
    for key, value in totals.items():
        print(f"{key:38s} {value}")
    print(f"\nwrote {(OUT / 'route-ledger.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
