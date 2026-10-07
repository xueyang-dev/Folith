"""Marker provenance and reversal routing must coexist in one document.

Two chains were built separately and now share the same ingestion path:

* native text is trusted or rejected from its own order evidence, and a
  rejected layer is routed to visual understanding;
* superscript footnote marks are removed from the translation text and kept as
  provenance.

These tests prove the two survive each other.  Cross Test A uses a reliable
Arabic layer (the frozen routing corpus) plus inline marks: the layer must stay
accepted, the marks must still be detected, stripped and persisted, and the VLM
must never be consulted.  Cross Test B uses a mirrored Arabic layer: the layer
must be rejected and routed to the VLM, the VLM path must not fabricate marker
grounding, and the segment contract must stay valid.

No request leaves the machine: the visual provider is a local stub.
"""
from __future__ import annotations

import pathlib
import sys

import pymupdf
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import pdf_ingestion as ingestion  # noqa: E402
from folith import text_layer_quality as quality  # noqa: E402
from folith.document_ir import segment_marks, segment_spans  # noqa: E402
from folith.document_understanding import (  # noqa: E402
    PageUnderstanding, SemanticBlock, native_pdf_document)
from folith.spatial_index import SpatialTextIndex  # noqa: E402

#: Real, correctly ordered Arabic layer.  Generated (gitignored) by
#: benchmark/routing/prepare_samples.py, so these tests skip on a bare clone.
ARABIC_CORPUS = (ROOT / "benchmark" / "routing" / "samples"
                 / "arabic-wikipedia-language.pdf")
#: A system Arabic font is only needed to *write* the mirrored fixture.
ARABIC_FONT = pathlib.Path("/System/Library/Fonts/Supplemental/Al Nile.ttc")

#: Seventeen common Arabic words: the mirror of each one is what a reversed
#: layer contains.  Measured: 17 mirrored common words, 42 Arabic letters.
_MIRRORED_ARABIC = "من في على التي الذي هذا ما لا كان بين عن مع كل هو هي ثم قد"


class _RecordingProvider:
    """Local stand-in for the visual understanding provider."""

    def __init__(self):
        self.pages = []

    def understand_page(self, envelope):
        self.pages.append(envelope.page_id)
        return PageUnderstanding(
            [SemanticBlock("paragraph", "A visually recognised sentence.")],
            "cross-test-vlm")


def _add_marker_pages(document, count=6, *, body_size=9.0, marker_size=5.4):
    """Append pages of prose carrying raised, smaller footnote marks."""
    width, height = document[0].rect.width, document[0].rect.height
    number = 1
    for _ in range(count // 2):
        page = document.new_page(width=width, height=height)
        for _ in range(2):
            lead = f"Paragraph {number} of the survey opens in the archive."
            tail = f"Its {number}th observation concerns the aerial view."
            page.insert_text((60, 300), lead, fontsize=body_size)
            page.insert_text((60, 340), tail, fontsize=body_size)
            end = 60 + pymupdf.get_text_length(
                tail, fontname="helv", fontsize=body_size)
            page.insert_text((end + 1, 340 - 3.4), str(number),
                             fontsize=marker_size)
            number += 1
    return document


def _arabic_corpus_with_markers() -> bytes:
    with pymupdf.open(ARABIC_CORPUS) as corpus:
        document = pymupdf.open()
        document.insert_pdf(corpus)
        _add_marker_pages(document)
        data = document.tobytes()
        document.close()
    return data


def _mirrored_arabic_pdf() -> bytes:
    """A native layer whose Arabic comes out character-reversed."""
    document = pymupdf.open()
    page = document.new_page(width=612, height=792)
    page.insert_text((40, 60), _MIRRORED_ARABIC, fontsize=12,
                     fontname="F0", fontfile=str(ARABIC_FONT))
    page.insert_text((40, 120), "Note the observation.", fontsize=9)
    page.insert_text((40 + pymupdf.get_text_length(
        "Note the observation.", fontname="helv", fontsize=9) + 1, 116.6),
        "1", fontsize=5.4)
    data = document.tobytes()
    document.close()
    return data


@pytest.mark.skipif(not ARABIC_CORPUS.is_file(),
                    reason="run benchmark/routing/prepare_samples.py first")
def test_reliable_arabic_native_text_keeps_marker_provenance():
    data = _arabic_corpus_with_markers()
    report = quality.assess_pdf_text_layer(data)

    # The marker census must not disturb the routing verdict: the layer stays
    # accepted and no reversal is inferred from the digits themselves.
    assert report["status"] == "accepted", report.get("reasons")
    assert report["evidence"]["reversal_check"]["mode"] is None
    assert report["superscript_markers"]["marker_count"] >= 6

    paragraphs = ingestion.extract_pdf_paragraphs(data)
    marked = [p for p in paragraphs if "observation concerns" in p]
    assert marked, "the marker pages must survive paragraph reconstruction"
    for paragraph in marked:
        assert not paragraph.rstrip().endswith(("1", "2", "3", "4", "5", "6"))

    blocks = ingestion.extract_layout_blocks(data)
    marks = [mark for block in blocks for mark in (block.get("marks") or [])]
    assert len(marks) >= 6

    # Provenance survives into DocumentIR and onto segments.
    document = native_pdf_document("arabic.pdf", data, paragraphs)
    in_ir = [mark for block in document["blocks"]
             for mark in (block.get("marks") or [])]
    assert len(in_ir) == len(marks)
    metadata = {"paragraph_ranges": [
        {"paragraph_index": index, "start": index, "end": index + 1}
        for index in range(len(paragraphs))]}
    spans = segment_spans(document, paragraphs, paragraphs, metadata)
    projected = segment_marks(document, spans, paragraphs)
    assert sum(len(item) for item in projected) == len(in_ir)
    assert all(mark.get("page_number") for item in projected for mark in item)

    # A trusted layer is never sent to the visual provider.
    provider = _RecordingProvider()
    _, _, extraction = core.extract_document_paragraphs_with_report(
        "arabic.pdf", data, ocr_mode="off", understanding_provider=provider)
    assert extraction["semantic_source"] == "native_pdf"
    assert provider.pages == []


@pytest.mark.skipif(not ARABIC_FONT.is_file(),
                    reason="no system Arabic font to build the fixture")
def test_reversed_native_text_routes_to_vlm_without_marker_damage():
    data = _mirrored_arabic_pdf()
    report = quality.assess_pdf_text_layer(data)

    assert report["status"] == "rejected"
    assert "arabic_letter_order_reversed" in (report.get("reasons") or [])
    assert report["evidence"]["common_words_reversed"] >= 8

    provider = _RecordingProvider()
    paragraphs, _, extraction = core.extract_document_paragraphs_with_report(
        "mirrored.pdf", data, ocr_max_pages=None,
        understanding_provider=provider,
        spatial_engine=lambda page: SpatialTextIndex(page.page_id))

    assert extraction["semantic_source"] == "vlm"
    assert provider.pages, "a rejected layer must actually reach the VLM"

    # The VLM path has no marker grounding, and must not invent any.
    document = extraction["document_ir"]
    assert document and document["blocks"]
    assert not any(block.get("marks") for block in document["blocks"])

    # The segment contract still holds on the VLM route.
    state = core.new_job_state("mirrored.pdf")
    state["extraction_report"] = extraction
    segments, _ = core._apply_source_segmentation(state, paragraphs)
    assert state["segment_source_marks"] == [[] for _ in segments]
    assert len(state["segment_source_spans"]) == len(segments)
