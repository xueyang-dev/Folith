"""Text-layer routing: a mirrored Arabic layer must not be trusted.

The defect this file pins down: the order evidence that proves a layer is
mirrored was only acted upon when the caller *declared* an Arabic-script
language.  A job that declared nothing therefore accepted a 1920s scan whose
common words were mirrored 25 times against 3 correct ones, fed that reversed
text to translation, and never reached visual understanding.

Everything here uses the frozen routing benchmark or built-in fixtures; the only
model interaction is a stub provider, so no request leaves the machine.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pymupdf
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import text_layer_quality as quality  # noqa: E402

ROUTING = ROOT / "benchmark" / "routing"
BENCHMARK = ROUTING / "samples.json"

#: Forward / mirrored counts that must decide the verdict.
STRONG_REVERSAL = (3, 25)
CLEAN_NOISE = (20, 2)
TINY_SAMPLE = (0, 2)


def _arabic_sample(text: str, words=None) -> dict:
    return {"page_index": 0, "text": text, "words": words or []}


#: One common Arabic word and its mirror; repeated to hit an exact count.
_FORWARD_WORD = "من"
_MIRRORED_WORD = "نم"


def _sample_with_counts(forward: int, reversed_: int) -> dict:
    """A sample whose common-word counts are exactly the requested ones.

    The gate counts a token as forward when it is a common Arabic word and as
    mirrored when its reverse is, so the counts are produced one word at a time.
    """
    text = " ".join([_FORWARD_WORD] * forward + [_MIRRORED_WORD] * reversed_
                    + ["كتاب"] * 20)
    return _arabic_sample(text)


# ---------------------------------------------------------------------------
# Section 7 — the reversal rule
# ---------------------------------------------------------------------------
def test_strong_reversal_rejects_without_a_declared_language():
    """fwd=3 / rev=25 must be rejected even though nothing was declared."""
    forward, reversed_ = STRONG_REVERSAL
    report = quality.assess_text_layer_sample(
        [_sample_with_counts(forward, reversed_)], source_language=None)
    assert report["status"] == "rejected", report
    assert "arabic_letter_order_reversed" in report["reasons"]
    assert report["evidence"]["reversal_check"]["mode"] == "detected_script"
    evidence = report["evidence"]
    assert evidence["common_words_forward"] == forward
    assert evidence["common_words_reversed"] == reversed_
    assert evidence["common_word_reversal_score"] > 0.5


def test_strong_reversal_also_rejects_with_a_declared_language():
    """The declared path must keep rejecting the same page."""
    forward, reversed_ = STRONG_REVERSAL
    report = quality.assess_text_layer_sample(
        [_sample_with_counts(forward, reversed_)], source_language="Arabic")
    assert report["status"] == "rejected"
    assert report["evidence"]["reversal_check"]["mode"] == "declared_language"


def test_normal_arabic_native_text_is_accepted():
    text = ("هذا نص عربي سليم مكتوب بترتيب منطقي صحيح. "
            "يحتوي على كلمات شائعة مثل من وفي وعلى التي وهذا. " * 4)
    for language in (None, "Arabic", "ar", "العربية"):
        report = quality.assess_text_layer_sample(
            [_arabic_sample(text)], source_language=language)
        assert report["status"] == "accepted", (language, report)
        assert "arabic_letter_order_reversed" not in report["reasons"]


def test_small_noise_does_not_reject():
    """A couple of mirrored words in normally ordered text is not evidence."""
    forward, reversed_ = CLEAN_NOISE
    report = quality.assess_text_layer_sample(
        [_sample_with_counts(forward, reversed_)], source_language=None)
    assert report["status"] == "accepted", report
    assert report["evidence"]["reversal_check"]["mode"] is None


def test_low_sample_count_does_not_overreact():
    """Two mirrored words and nothing else is too little to condemn a page."""
    forward, reversed_ = TINY_SAMPLE
    report = quality.assess_text_layer_sample(
        [_sample_with_counts(forward, reversed_)], source_language=None)
    assert report["status"] == "accepted", report
    assert report["evidence"]["common_word_reversal_score"] == 1.0
    assert report["evidence"]["reversal_check"]["mode"] is None


def test_a_latin_page_is_never_judged_by_arabic_rules():
    latin = ("The quick brown fox jumps over the lazy dog. " * 6)
    report = quality.assess_text_layer_sample(
        [_arabic_sample(latin)], source_language=None)
    assert report["status"] == "accepted"
    assert report["evidence"]["arabic_text_detected"] is False
    assert "reversal_check" not in report["evidence"]


def test_mixed_arabic_english_page_is_not_rejected_for_reversal():
    """An Arabic page carrying English names and lots of digits stays usable."""
    text = ("أطلقت OAG Aviation Worldwide Limited تقريرها لعام 2024 عن السعة، "
            "حيث بلغت 270 مليون مقعد وبنسبة 63% و9% و56% و1,400 رحلة. " * 3)
    report = quality.assess_text_layer_sample(
        [_arabic_sample(text)], source_language=None)
    assert report["status"] == "accepted", report
    assert "arabic_letter_order_reversed" not in report["reasons"]
    assert report["evidence"]["arabic_text_detected"] is True


def test_the_new_rule_is_what_rejects_a_strongly_mirrored_layer(monkeypatch):
    """Negative control: raise the floor and the same page is accepted again."""
    forward, reversed_ = STRONG_REVERSAL
    sample = [_sample_with_counts(forward, reversed_)]
    assert quality.assess_text_layer_sample(
        sample, source_language=None)["status"] == "rejected"
    monkeypatch.setattr(quality, "REVERSAL_MIN_HITS", 1000)
    revived = quality.assess_text_layer_sample(sample, source_language=None)
    assert revived["status"] == "accepted"
    assert revived["evidence"]["common_words_reversed"] == reversed_
    monkeypatch.setattr(quality, "REVERSAL_MIN_RATIO", 1000.0)
    monkeypatch.setattr(quality, "REVERSAL_MIN_HITS", 8)
    by_ratio = quality.assess_text_layer_sample(sample, source_language=None)
    assert by_ratio["status"] == "accepted"


# ---------------------------------------------------------------------------
# Section 7 Test 6 — the frozen challenge page, not a synthetic count
# ---------------------------------------------------------------------------
def _benchmark_entry(sample_id: str) -> dict:
    manifest = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    for entries in manifest["groups"].values():
        for entry in entries:
            if entry["id"] == sample_id:
                return entry
    raise AssertionError(f"{sample_id} is not in the routing benchmark")


@pytest.mark.parametrize("sample_id", ["challenge-historical",
                                       "historical-scan",
                                       "historical-subset-4p"])
def test_frozen_degraded_scans_are_rejected(sample_id):
    entry = _benchmark_entry(sample_id)
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    report = quality.assess_pdf_text_layer(path.read_bytes(), source_language=None)
    assert report["status"] == "rejected", report
    assert "arabic_letter_order_reversed" in report["reasons"]
    assert report["evidence"]["reversal_check"]["mode"] == "detected_script"


def test_the_challenge_page_was_accepted_under_the_old_rule(monkeypatch):
    """Pin the regression itself: the evidence was always there.

    The old rule only consulted it for a declared language.  Re-enabling that
    behaviour for one call shows the page returning to `accepted` at score 1.0,
    which is what the previous phase shipped.
    """
    entry = _benchmark_entry("challenge-historical")
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    data = path.read_bytes()
    assert quality.assess_pdf_text_layer(
        data, source_language=None)["status"] == "rejected"
    # Reproduce the old contract exactly: with nothing declared, the order
    # evidence was never consulted.  Forcing the script detector off restores
    # that behaviour for this one call.
    monkeypatch.setattr(quality, "ARABIC_SCRIPT_MIN_LETTERS", 10 ** 9)
    old = quality.assess_pdf_text_layer(data, source_language=None)
    assert old["status"] == "accepted"
    assert old["score"] == 1.0
    assert old["reasons"] == []
    assert old["evidence"]["common_words_reversed"] == 25
    assert old["evidence"]["common_words_forward"] == 3


# ---------------------------------------------------------------------------
# Section 12 — the benchmark as a whole
# ---------------------------------------------------------------------------
def test_benchmark_has_no_false_accept_or_false_reject():
    if not BENCHMARK.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    sys.path.insert(0, str(ROUTING))
    import measure

    report = measure.measure(source_language=None)
    result = measure.confusion(report)
    assert result["false_accept"] == [], result
    assert result["false_reject"] == [], result
    assert result["matched"] == result["total"]


def test_app_arabic_export_is_rejected_for_its_own_reason():
    """The application's Arabic output must not be re-attributed to this change."""
    entry = _benchmark_entry("app-arabic-export")
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    report = quality.assess_pdf_text_layer(path.read_bytes(), source_language=None)
    assert report["status"] == "rejected"
    assert "control_characters" in report["reasons"]
    assert "arabic_letter_order_reversed" not in report["reasons"]


# ---------------------------------------------------------------------------
# Section 8 — the route the product actually takes
# ---------------------------------------------------------------------------
class _StubUnderstanding:
    """A visual-understanding provider that answers locally."""

    cache_identity = None

    def __init__(self):
        self.pages = []

    def understand_page(self, page):
        from folith.document_ir import PageUnderstanding, SemanticBlock
        self.pages.append(page.page_id)
        return PageUnderstanding(
            [SemanticBlock("paragraph",
                           "Revised text from visual understanding.",
                           region_ids=[f"{page.page_id}-b0"], direction="rtl",
                           language="ar")],
            "stub-vlm")


def test_challenge_page_routes_to_visual_understanding(tmp_path):
    """Rejected native layer must reach the VLM path, not local OCR."""
    entry = _benchmark_entry("challenge-historical")
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    data = path.read_bytes()
    provider = _StubUnderstanding()
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "challenge.pdf", data,
        source_language=None,
        understanding_provider=provider,
        spatial_engine=lambda page: _stub_spatial(page),
        checkpoint_dir=None,
        ocr_mode="auto",
    )
    assert provider.pages, "the visual-understanding provider was never called"
    assert report.get("semantic_source") == "vlm", report
    assert report.get("ocr_trigger") == "text_layer_quality_rejected", report
    assert report.get("pdf_text_layer_quality") == "rejected"
    assert report.get("ocr_used") is False
    assert "arabic_letter_order_reversed" in (
        report.get("pdf_text_layer_rejection_reasons") or [])
    assert paragraphs == ["Revised text from visual understanding."]
    assert not any("Reversed" in p or "ةيقيقدلا" in p for p in paragraphs)


def test_accepted_native_layer_never_calls_the_provider():
    """The other half of the contract: a good layer must not cost a VLM call."""
    entry = _benchmark_entry("english-native")
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    provider = _StubUnderstanding()
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "english.pdf", path.read_bytes(),
        source_language="English",
        understanding_provider=provider,
        spatial_engine=lambda page: _stub_spatial(page),
        checkpoint_dir=None,
        ocr_mode="auto",
    )
    assert provider.pages == []
    assert report.get("semantic_source") == "native_pdf"
    assert report.get("ocr_trigger") == "text_layer_accepted"
    assert paragraphs, paragraphs
    assert "clear scanned english document" in paragraphs[0].lower()


def test_rejected_layer_does_not_fall_back_to_local_ocr(tmp_path, monkeypatch):
    """A rejection must reach visual understanding, not the legacy OCR chain."""
    entry = _benchmark_entry("challenge-historical")
    path = ROUTING / entry["file"]
    if not path.is_file():
        pytest.skip("run benchmark/routing/prepare_samples.py first")
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("ocr")
        raise AssertionError("local OCR was used for a rejected text layer")

    monkeypatch.setattr(core, "_ocr_pdf_text_with_warnings", forbidden)
    provider = _StubUnderstanding()
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "challenge.pdf", path.read_bytes(),
        source_language=None,
        understanding_provider=provider,
        spatial_engine=lambda page: _stub_spatial(page),
        checkpoint_dir=None,
        ocr_mode="auto",
    )
    assert calls == []
    assert provider.pages == ["p1"]
    assert report.get("semantic_source") == "vlm"
    assert report.get("ocr_used") is False
    assert "ocr" not in report


def _stub_spatial(page):
    """A spatial index that recognises nothing: grounding may abstain."""
    from folith.spatial_index import SpatialTextIndex
    return SpatialTextIndex(page.page_id)


def test_declared_language_still_overrides_nothing():
    """A Latin layer with Arabic declared stays rejected for the declaration."""
    latin = "The quick brown fox jumps over the lazy dog. " * 6
    report = quality.assess_text_layer_sample(
        [_arabic_sample(latin)], source_language="Arabic")
    assert report["status"] == "rejected"
    assert "arabic_script_mismatch" in report["reasons"]
    assert "arabic_letter_order_reversed" not in report["reasons"]


def test_evidence_version_marks_the_contract_change():
    assert quality.TEXT_LAYER_QUALITY_VERSION >= 2
    report = quality.assess_text_layer_sample(
        [_sample_with_counts(*STRONG_REVERSAL)], source_language=None)
    assert report["evidence"]["version"] == quality.TEXT_LAYER_QUALITY_VERSION
    assert "reversal_check" in report["evidence"]


def test_no_repair_is_attempted_on_a_rejected_layer():
    """The phase detects and routes; it must not rewrite the extracted text."""
    text = " ".join(["نم يف ىلع"] * 12)
    report = quality.assess_text_layer_sample([_arabic_sample(text)],
                                              source_language=None)
    assert report["status"] == "rejected"
    assert report["evidence"]["common_words_reversed"] > 0
    # Nothing in the report claims a repaired ordering.
    assert "repair" not in json.dumps(report).lower()


def test_the_ratio_floor_is_what_protects_clean_arabic(monkeypatch):
    """Negative control NC3: a permissive ratio rejects clean Arabic.

    Clean samples do contain a few mirrored words by chance (measured: 1-5 per
    page against 7-151 correct ones).  The ratio condition is what keeps those
    from condemning a usable layer.
    """
    clean = ("هذا نص عربي سليم يحتوي على كلمات شائعة كثيرة مثل من وفي وعلى "
             "التي وهذا والتي وتلك وهناك وهنا وايضا وقد وكان ") * 4 + " نم"
    sample = [_arabic_sample(clean)]
    assert quality.assess_text_layer_sample(
        sample, source_language=None)["status"] == "accepted"
    monkeypatch.setattr(quality, "REVERSAL_MIN_HITS", 1)
    monkeypatch.setattr(quality, "REVERSAL_MIN_RATIO", 0.01)
    permissive = quality.assess_text_layer_sample(sample, source_language=None)
    assert permissive["status"] == "rejected"
    assert "arabic_letter_order_reversed" in permissive["reasons"]


def test_the_hit_floor_is_what_protects_a_short_sample(monkeypatch):
    """Negative control NC4: only the floor stands between 3 words and a reject."""
    sample = [_arabic_sample("نم نم نم " + "كتاب " * 20)]
    assert quality.assess_text_layer_sample(
        sample, source_language=None)["status"] == "accepted"
    monkeypatch.setattr(quality, "REVERSAL_MIN_HITS", 1)
    without = quality.assess_text_layer_sample(sample, source_language=None)
    assert without["status"] == "rejected"
    assert without["evidence"]["reversal_check"]["ratio"] >= quality.REVERSAL_MIN_RATIO


def test_the_visual_signal_stands_on_its_own():
    """Both reversal signals must be independently decisive.

    The degraded pages carry 12-19 visual-order lines; a page with correct
    dictionary evidence but a visual-order layer must still be rejected.
    """
    words = []
    for line in range(2):
        for index in range(5):
            words.append([10 + index * 30, 12 + line * 20, 40 + index * 30,
                          24 + line * 20, f"كلمة{index}", 0, line, index])
    text = " ".join(w[4] for w in words)
    report = quality.assess_text_layer_sample(
        [{"page_index": 0, "text": text, "words": words}], source_language=None)
    assert report["evidence"]["visual_order_ratio"] == 1.0
    assert report["status"] == "rejected"
    assert "arabic_word_order_reversed" in report["reasons"]


# ---------------------------------------------------------------------------
# Section 15 — routing metadata must not invalidate the renderer's page cache
# ---------------------------------------------------------------------------
def test_quality_metadata_does_not_invalidate_the_page_cache(tmp_path):
    """Re-extraction metadata is not a layout input.

    The original-layout renderer caches one overlay per page.  Recording a new
    quality verdict, evidence blob or rejection reason must not throw those away:
    only the source text and the placement inputs may.
    """
    from folith import pdfrender

    source = _tiny_pdf()
    state = _tiny_state(source)
    cache = tmp_path / "cache"
    job = "routing-cache-job"
    first = pdfrender.render_original_layout(job, state, source,
                                             page_cache_dir=cache, use_cache=True)
    assert first.report["totals"]["page_cache_misses"] == 1

    # Same document, richer routing metadata.
    state["extraction_report"] = dict(state.get("extraction_report") or {})
    state["extraction_report"].update({
        "pdf_text_layer_quality": "rejected",
        "pdf_text_layer_rejection_reasons": ["arabic_letter_order_reversed"],
        "pdf_text_layer_evidence": {"common_words_reversed": 25,
                                    "common_words_forward": 3},
        "ocr_trigger": "text_layer_quality_rejected",
        "semantic_source": "vlm",
    })
    second = pdfrender.render_original_layout(job, state, source,
                                              page_cache_dir=cache, use_cache=True)
    assert second.report["totals"]["page_cache_hits"] == 1
    assert second.report["totals"]["page_cache_misses"] == 0
    # Serialisation is not byte-deterministic, so compare the rendered page.
    assert _page_png(first.data) == _page_png(second.data)


def _page_png(pdf_bytes: bytes) -> bytes:
    import pymupdf
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
        return document[0].get_pixmap(dpi=72).tobytes("png")


def _tiny_pdf() -> bytes:
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=300, height=180)
    page.insert_text((40, 60), "A source sentence.", fontsize=12, fontname="helv")
    data = document.tobytes()
    document.close()
    return data


def _tiny_state(source: bytes) -> dict:
    import pymupdf
    with pymupdf.open(stream=source, filetype="pdf") as document:
        line = document[0].get_text("dict")["blocks"][0]["lines"][0]
        box = [round(v, 2) for v in line["bbox"]]
    span = {"page_id": "p1", "block_id": "b0", "char_start": None,
            "char_end": None,
            "bbox": [box[0] / 300, box[1] / 180, box[2] / 300, box[3] / 180],
            "region_id": None, "grounding_method": "native_pdf",
            "grounding_quality": "exact"}
    return {
        "pairs": [{"source": "A source sentence.", "target": "一句原文。",
                   "source_spans": [span]}],
        "extraction_report": {
            "semantic_source": "native_pdf",
            "document_ir": {
                "version": 1,
                "metadata": {"document_id": "doc", "filename": "doc.pdf"},
                "pages": [{"page_id": "p1", "page_index": 0, "width": 300.0,
                           "height": 180.0, "rotation": 0,
                           "display_page_number": None,
                           "source_artifact": "source.bin"}],
                "blocks": [], "assets": [], "references": [],
                "provenance": {}, "translation_block_ids": [],
            },
        },
        "source_lang": "en", "target_lang": "简体中文",
    }
