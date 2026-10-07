"""End-to-end marker fidelity through the real pipeline.

The footnote work was previously verified module by module.  This test runs the
actual orchestration entry point — ingestion, quality gate, marker extraction,
DocumentIR, segmentation, translation, segment marks — and then renders the
original-layout PDF from the resulting state, so the claim "the mark survives
the whole chain and comes back" is tested rather than assumed.

Everything is offline: the provider is the same deterministic fixture used by
the scenario gate, so no request leaves the machine.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pymupdf
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import core  # noqa: E402
from offline_provider import OfflineProvider  # noqa: E402
from folith import pdfrender  # noqa: E402

PROVIDER = "OfflineFixture"
MODEL = "offline-fixture"
TARGET_LANG = "简体中文"


@contextmanager
def offline_job(provider):
    """Replace the provider call and the output directory with a temp sandbox."""
    tmp = Path(tempfile.mkdtemp(prefix="marker-e2e-"))
    old_output, old_call = core.OUTPUT_DIR, core.call_llm
    core.OUTPUT_DIR, core.call_llm = tmp, provider
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR, core.call_llm = old_output, old_call
        shutil.rmtree(tmp, ignore_errors=True)


def _source_pdf(pages: int = 4) -> bytes:
    """Prose pages carrying raised, smaller footnote marks."""
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        lead = f"Paragraph {index + 1} opens the survey in the archive."
        tail = f"Its {index + 1}th observation concerns the aerial view."
        page.insert_text((60, 300), lead, fontsize=9)
        page.insert_text((60, 340), tail, fontsize=9)
        end = 60 + pymupdf.get_text_length(tail, fontname="helv", fontsize=9)
        page.insert_text((end + 1, 336.6), str(index + 1), fontsize=5.4)
    data = doc.tobytes()
    doc.close()
    return data


def _run(job_id: str, path: Path) -> dict:
    return core.run_job_pipeline(
        job_id, path.name, path.read_bytes(),
        provider=PROVIDER, api_key="offline", model=MODEL,
        target_lang=TARGET_LANG, auto_term=False, enable_report=False,
        translation_theory="", user_glossary=[], style_rules="保持学术书面语。",
        enable_review=True, enable_annotate=False, use_tm=True,
        strict_terminology_governance=False,
        delivery_config={"deliver_report": False})


def test_marker_fidelity_end_to_end():
    data = _source_pdf()
    with offline_job(OfflineProvider()) as out:
        path = out / "marker-e2e.pdf"
        path.write_bytes(data)
        state = _run("marker-e2e", path)

    # 1. The translation text never received a bare mark digit.
    paras = state["paras"]
    assert paras, "the pipeline must produce translation units"
    for paragraph in paras:
        assert not paragraph.rstrip().endswith(("1", "2", "3", "4")), paragraph

    # 2. Provenance survived extraction, DocumentIR and segmentation.
    marks = [mark for item in (state.get("segment_source_marks") or [])
             for mark in item]
    assert marks, "segmentation must carry the marker provenance"
    assert all(mark.get("page_number") for mark in marks)
    assert all(mark.get("bbox") for mark in marks)

    # 3. The renderer restores exactly the marks the pipeline located.
    result = pdfrender.render_original_layout(
        "marker-e2e", state, data, mode="translated", use_cache=False)
    evidence = result.report["markers"]
    assert evidence["restored_marks"] == len(marks)
    assert evidence["unplaced_renderer_marks"] == 0

    doc = pymupdf.open(stream=result.data, filetype="pdf")
    try:
        found = 0
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    body = max((span["size"] for span in line["spans"]), default=0)
                    found += sum(
                        1 for span in line["spans"]
                        if span["text"].strip().isdigit()
                        and span["size"] < body * 0.85)
        assert found == len(marks), "each located mark must appear as a superscript"
    finally:
        doc.close()


def test_translation_text_stays_clean_when_no_marks_exist():
    """A document without markers must be untouched by the marker machinery."""
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((60, 300), f"Plain paragraph number {index + 1}.",
                         fontsize=9)
    data = doc.tobytes()
    doc.close()

    with offline_job(OfflineProvider()) as out:
        path = out / "plain-e2e.pdf"
        path.write_bytes(data)
        state = _run("plain-e2e", path)

    assert not any(state.get("segment_source_marks") or []), \
        "a marker-free document must not invent provenance"
    result = pdfrender.render_original_layout(
        "plain-e2e", state, data, mode="translated", use_cache=False)
    assert result.report["markers"] == {
        "source_marks": 0, "restored_marks": 0,
        "unlocated_source_marks": 0, "unplaced_renderer_marks": 0}
