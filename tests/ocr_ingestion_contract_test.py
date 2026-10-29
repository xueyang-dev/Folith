"""P2 regression: a broken PDF text layer must not silently bypass OCR.

The real *علم الدين* scan proves the old assumption (``paragraph_count > 0`` ⇒
usable text layer) wrong.  These tests pin the new ingestion contract:

* text existence != text quality;
* ``ocr_mode`` is user-controlled (auto / force / off) and is never overridden;
* the declared source language feeds the quality gate but is never re-detected;
* the extraction report records *why* a document went to OCR.
"""
from __future__ import annotations

import json

import pytest

import core
from folith import text_layer_quality as tlq

# The exact broken extraction from the real book (pages 50-52), copied from the
# benchmark evidence.  Words are character-reversed Arabic.
BROKEN_IA_TEXT = (
    "فيرلا يف ةماقالل اهانزكذ\n"
    "لو بلطلا\n"
    "نم نأ عم بعهلا ةرث كردت\n"
    "روف ههلعت لملا بجإو\n"
)
BROKEN_IA_WORDS = [
    [300, 100, 340, 120, "فيرلا", 0, 0, 0],
    [250, 100, 295, 120, "يف", 0, 0, 1],
    [180, 100, 240, 120, "ةماقالل", 0, 0, 2],
    [80, 100, 170, 120, "اهانزكذ", 0, 0, 3],
]
CLEAN_ARABIC_TEXT = (
    "هذا نص عربي سليم. من في البيت؟ على كل حال، كان ذلك في المدينة.\n"
    "قال لي ان هذا الكتاب من افضل ما كتب. وقد كان بين الناس معروفا.\n"
)
CLEAN_ARABIC_WORDS = [
    [300, 100, 340, 120, "هذا", 0, 0, 0],
    [250, 100, 290, 120, "نص", 0, 0, 1],
    [190, 100, 240, 120, "عربي", 0, 0, 2],
    [110, 100, 180, 120, "سليم", 0, 0, 3],
    [300, 140, 340, 160, "قال", 0, 1, 0],
    [240, 140, 290, 160, "لي", 0, 1, 1],
    [190, 140, 230, 160, "ان", 0, 1, 2],
    [100, 140, 180, 160, "هذا", 0, 1, 3],
]
CLEAN_ENGLISH_TEXT = (
    "The quick brown fox jumps over the lazy dog. This is a clean digital "
    "text layer with normal words and punctuation, used for regression.\n"
)
CLEAN_ENGLISH_WORDS = [
    [72, 100, 110, 120, "The", 0, 0, 0],
    [120, 100, 170, 120, "quick", 0, 0, 1],
    [180, 100, 230, 120, "brown", 0, 0, 2],
    [240, 100, 280, 120, "fox", 0, 0, 3],
]


def _sample(index, text, words=None):
    return {"page_index": index, "text": text, "words": words or []}


# --------------------------------------------------------------------------
# pure quality gate
# --------------------------------------------------------------------------
def test_broken_arabic_layer_is_rejected():
    report = tlq.assess_text_layer_sample(
        [_sample(0, BROKEN_IA_TEXT, BROKEN_IA_WORDS)], source_language="Arabic")
    assert report["status"] == "rejected"
    assert report["score"] < 0.5
    assert "arabic_letter_order_reversed" in report["reasons"]


def test_clean_arabic_layer_is_accepted():
    report = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ARABIC_TEXT, CLEAN_ARABIC_WORDS)], source_language="Arabic")
    assert report["status"] == "accepted", report
    assert report["score"] >= 0.7


def test_clean_english_layer_is_accepted():
    report = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ENGLISH_TEXT, CLEAN_ENGLISH_WORDS)], source_language="English")
    assert report["status"] == "accepted"


HEBREW_TEXT = (
    "זהו מסמך עברי תקין עם משפטים ברורים ומילים רגילות. "
    "הטקסט מיועד לבדיקה של שכבת טקסט תקינה במסמך.\n") * 3


@pytest.mark.parametrize("language", ["he", "Hebrew", "עברית", "heb"])
def test_clean_hebrew_layer_is_accepted(language):
    report = tlq.assess_text_layer_sample(
        [_sample(0, HEBREW_TEXT)], source_language=language)
    assert report["status"] == "accepted", report
    assert report["source_language_script"] == "other"


@pytest.mark.parametrize("language", ["ar", "ar-SA", "ar-Arab", "Arabic",
                                     "阿拉伯语", "波斯语", "ur", "ug-Arab"])
def test_arabic_script_languages_still_reject_latin_layer(language):
    report = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ENGLISH_TEXT)], source_language=language)
    assert report["status"] == "rejected"
    assert "arabic_script_mismatch" in report["reasons"]


def test_empty_layer_is_absent_not_rejected():
    report = tlq.assess_text_layer_sample([_sample(0, "", [])], source_language="Arabic")
    assert report["status"] == "absent"


def test_replacement_characters_are_rejected():
    text = "هذا نص \ufffd\ufffd عربي \ufffd سليم"
    report = tlq.assess_text_layer_sample([_sample(0, text)], source_language="Arabic")
    assert report["status"] == "rejected"
    assert "replacement_characters" in report["reasons"]


def test_logically_ordered_presentation_forms_are_not_rejected():
    """Presentation forms alone are a smell, not proof of a broken order."""
    text = "ﻫﺬﺍ ﻧﺺ ﻋﺮﺑﻲ ﺳﻠﻴﻢ ﻣﻦ ﻓﻲ ﺍﻟﺒﻴﺖ ﻋﻠﻰ ﻛﻞ ﺣﺎﻝ ﻛﺎﻥ ﺫﻟﻚ ﻓﻲ ﺍﻟﻤﺪﻳﻨﺔ"
    report = tlq.assess_text_layer_sample([_sample(0, text)], source_language="Arabic")
    assert report["status"] != "rejected", report
    assert "arabic_presentation_forms" in report["reasons"]


def test_presentation_forms_with_reversed_order_are_rejected():
    text = "ﻦﻣ ﻲﻓ ﺖﻴﺒﻟﺍ ﻰﻠﻋ ﻞﻛ ﻝﺎﺣ ﻥﺎﻛ ﻚﻟﺫ ﻲﻓ ﺔﻨﻳﺪﻤﻟﺍ"
    report = tlq.assess_text_layer_sample([_sample(0, text)], source_language="Arabic")
    assert report["status"] == "rejected"


def test_arabic_declared_but_latin_layer_is_rejected():
    report = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ENGLISH_TEXT, CLEAN_ENGLISH_WORDS)], source_language="Arabic")
    assert report["status"] == "rejected"
    assert "arabic_script_mismatch" in report["reasons"]


def test_sparse_layer_is_not_accepted():
    report = tlq.assess_text_layer_sample(
        [_sample(0, "12", []), _sample(1, "13", []), _sample(2, "14", [])],
        source_language="Arabic")
    assert report["status"] in {"rejected", "uncertain"}
    assert "text_layer_sparse" in report["reasons"]


def test_declared_language_is_input_not_output():
    """A clean English layer stays accepted even when Arabic was declared?"""
    # No: an Arabic task with a Latin-only layer is a mismatch (previous test).
    # But the gate must never *change* the language: it reports the script it
    # compared against, and the caller keeps the user's language.
    report = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ARABIC_TEXT, CLEAN_ARABIC_WORDS)], source_language="Arabic")
    assert report["source_language_script"] == "arabic"
    english = tlq.assess_text_layer_sample(
        [_sample(0, CLEAN_ENGLISH_TEXT, CLEAN_ENGLISH_WORDS)], source_language="English")
    assert english["source_language_script"] == "other"


# --------------------------------------------------------------------------
# ingestion contract
# --------------------------------------------------------------------------
def _pdf_bytes():
    import fitz
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), "Hello digital text layer", fontsize=14)
    payload = document.tobytes()
    document.close()
    return payload


@pytest.fixture
def fake_ocr(monkeypatch):
    calls = []

    def engine(file_bytes, max_pages=None, on_progress=None, **kwargs):
        calls.append({"max_pages": max_pages, **kwargs})
        return ("OCR page one.\n\nOCR page two.", [], {
            "ocr_language_engine_code": "ara", "ocr_text_direction": "rtl",
            "ocr_dpi": 350, "ocr_psm": 6, "ocr_backend": "tesseract",
            "installed_language_available": True, "paragraph_confidences": [90.0],
        })

    monkeypatch.setattr(core, "_ocr_pdf_text_with_warnings", engine)
    return calls


def _assess(status, score=0.0, reasons=None):
    return {"status": status, "score": score, "reasons": reasons or [],
            "hard_reasons": reasons or [],
            "evidence": {"pages_with_text": 0 if status == "absent" else 1},
            "sampled_pages": [0]}


def test_auto_uses_clean_text_layer_without_ocr(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("accepted", 1.0))
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "digital.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="English")
    assert report["ocr_used"] is False
    assert report["ocr_trigger"] == "text_layer_accepted"
    assert fake_ocr == []


def test_auto_rejects_broken_layer_and_runs_ocr(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("rejected", 0.0,
                                               ["arabic_letter_order_reversed"]))
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic",
        ocr_language="ara")
    assert report["ocr_used"] is True
    assert report["ocr_trigger"] == "text_layer_quality_rejected"
    assert report["pdf_text_layer_quality"] == "rejected"
    assert report["pdf_text_layer_rejection_reasons"] == ["arabic_letter_order_reversed"]
    assert fake_ocr and fake_ocr[0]["ocr_language"] == "ara"


def test_force_ocr_ignores_a_clean_text_layer(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("accepted", 1.0))
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "digital.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="force", source_language="English")
    assert report["ocr_used"] is True
    assert report["ocr_trigger"] == "ocr_mode_force"
    assert fake_ocr  # forced regardless of a perfect text layer


def test_text_layer_only_never_invokes_ocr(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("rejected", 0.0, ["arabic_letter_order_reversed"]))
    _p, warnings, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="off", source_language="Arabic")
    assert report["ocr_used"] is False
    assert report["ocr_trigger"] == "ocr_mode_text_layer_only"
    assert fake_ocr == []
    assert any("文本层" in w for w in warnings)


def _empty_pdf():
    import fitz
    document = fitz.open()
    document.new_page(width=595, height=842)
    payload = document.tobytes()
    document.close()
    return payload


def test_absent_layer_in_auto_runs_ocr(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("absent", 0.0, ["no_text_layer"]))
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", _empty_pdf(), ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic")
    assert report["ocr_used"] is True
    assert report["ocr_trigger"] == "no_text_layer"


def test_partial_sampling_absent_becomes_uncertain(monkeypatch, fake_ocr):
    """An empty first/last page must not force OCR on a real digital document."""
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("absent", 0.0, ["no_text_layer"]))
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "digital.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="English")
    assert report["pdf_text_layer_quality"] == "uncertain"
    assert "text_layer_partial_sampling" in report["pdf_text_layer_rejection_reasons"]
    assert report["ocr_used"] is False


def test_uncertain_layer_is_surfaced_not_silently_guessed(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("uncertain", 0.55))
    _p, warnings, report = core.extract_document_paragraphs_with_report(
        "digital.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="English")
    assert report["pdf_text_layer_quality"] == "uncertain"
    assert report["ocr_trigger"] == "text_layer_uncertain"
    assert any("uncertain" in w.lower() or "不确定" in w for w in warnings)


def test_report_records_full_ocr_decision_evidence(monkeypatch, fake_ocr):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("rejected", 0.0,
                                               ["arabic_letter_order_reversed"]))
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic")
    for key in ("pdf_text_layer_present", "pdf_text_layer_quality",
                "pdf_text_layer_quality_score", "pdf_text_layer_rejection_reasons",
                "ocr_mode", "ocr_trigger", "ocr_used"):
        assert key in report, key
    assert report["pdf_text_layer_present"] is True
    assert report["ocr_mode"] == "auto"
    json.dumps(report)  # must be serialisable into state


def test_ocr_language_is_not_derived_from_the_text_layer(monkeypatch, fake_ocr):
    """The gate judges quality only; the OCR language stays the user's choice."""
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("rejected", 0.0, ["arabic_script_mismatch"]))
    core.extract_document_paragraphs_with_report(
        "scan.pdf", _pdf_bytes(), ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic",
        ocr_language="ara")
    assert fake_ocr[0]["ocr_language"] == "ara"


@pytest.mark.parametrize("backend, source, selected, expected", [
    ("ocr_api", "日本語", "", "jpn"),
    ("ocr_api", "日本語", "eng", "jpn"),
    ("ocr_api", "English", "jpn", "eng"),
    ("tesseract", "日本語", "", ""),
    ("ocr_api", "unknown language", "", ""),
])
def test_ocr_api_uses_declared_source_language_when_unset(
        monkeypatch, fake_ocr, backend, source, selected, expected):
    monkeypatch.setattr(core._text_layer_quality, "assess_pdf_text_layer",
                        lambda *a, **k: _assess("rejected"))
    core.extract_document_paragraphs_with_report(
        "scan.pdf", _pdf_bytes(), ocr_backend=backend, ocr_mode="force",
        source_language=source, ocr_language=selected)
    assert fake_ocr[0]["ocr_language"] == expected


# --------------------------------------------------------------------------
# real PDF bytes (no monkeypatching of the gate)
# --------------------------------------------------------------------------
def _latin_pdf(text):
    import fitz
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), text, fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def _arabic_pdf(text):
    import fitz
    from pathlib import Path
    font = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
    if not Path(font).is_file():
        pytest.skip("no Arabic-capable font available")
    document = fitz.open()
    page = document.new_page(width=600, height=220)
    page.insert_htmlbox(
        fitz.Rect(20, 20, 580, 200),
        f'<div style="font-family: f; font-size: 20px; direction: rtl;">{text}</div>',
        css=f'@font-face {{font-family: f; src: url("{font}");}}')
    payload = document.tobytes()
    document.close()
    return payload


def test_real_clean_latin_pdf_is_accepted(monkeypatch, fake_ocr):
    payload = _latin_pdf(
        "The quick brown fox jumps over the lazy dog. A clean digital text "
        "layer with normal words and punctuation for the regression suite.")
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "digital.pdf", payload, ocr_backend="tesseract", ocr_mode="auto", source_language="English")
    assert report["pdf_text_layer_quality"] == "accepted"
    assert report["ocr_used"] is False
    assert fake_ocr == []


def test_real_clean_arabic_pdf_is_accepted(monkeypatch, fake_ocr):
    payload = _arabic_pdf(
        "هذا نص عربي سليم. من في البيت؟ على كل حال، كان ذلك في المدينة. "
        "قال لي ان هذا الكتاب من افضل ما كتب وقد كان بين الناس معروفا.")
    report_quality = tlq.assess_pdf_text_layer(payload, source_language="Arabic")
    assert report_quality["status"] != "rejected", report_quality
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "digital-ar.pdf", payload, ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic")
    assert report["ocr_used"] is False


def test_real_clean_hebrew_pdf_does_not_require_ocr(monkeypatch, fake_ocr):
    payload = _arabic_pdf(HEBREW_TEXT)
    paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "digital-he.pdf", payload, ocr_backend="tesseract", ocr_mode="auto", ocr_language="heb",
        source_language="עברית")
    assert paragraphs
    assert report["pdf_text_layer_quality"] == "accepted"
    assert report["ocr_used"] is False
    assert fake_ocr == []


def test_real_latin_pdf_on_arabic_task_is_rejected_and_ocrs(monkeypatch, fake_ocr):
    payload = _latin_pdf("A junk ASCII text layer attached to an Arabic scan.")
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", payload, ocr_backend="tesseract", ocr_mode="auto", source_language="Arabic",
        ocr_language="ara")
    assert report["pdf_text_layer_quality"] == "rejected"
    assert report["ocr_used"] is True
    assert "arabic_script_mismatch" in report["pdf_text_layer_rejection_reasons"]


# --------------------------------------------------------------------------
# mode normalisation, state and rescan
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    ("auto", "auto"), ("Auto", "auto"), ("自动", "auto"), ("", "auto"),
    (None, "auto"), ("force", "force"), ("Force OCR", "force"), ("强制OCR", "force"),
    ("off", "off"), ("text_layer_only", "off"), ("仅文本层", "off"),
    ("nonsense", "auto"),
])
def test_normalize_ocr_mode(value, expected):
    assert core.normalize_ocr_mode(value) == expected


def test_ocr_mode_and_language_are_independent_configuration():
    """Mode never derives the language, and vice versa."""
    assert [item["value"] for item in core.ocr_mode_options()] == ["auto", "force", "off"]
    assert "ara" in [item["value"] for item in core.ocr_language_options()]


@pytest.mark.parametrize("backend", ["tesseract", "ocr_api"])
def test_rescan_can_switch_to_force_ocr_and_invalidates_downstream(tmp_path, monkeypatch, backend):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    captured = {}

    def fake_extract(filename, file_bytes, **kwargs):
        captured.update(kwargs)
        return (["نص عربي."], [], {"ocr": {"paragraph_confidences": [95.0]},
                                   "ocr_trigger": "ocr_mode_force",
                                   "pdf_text_layer_quality": "rejected"})

    monkeypatch.setattr(core, "extract_document_paragraphs_with_report", fake_extract)
    monkeypatch.setattr(core, "load_source", lambda _job_id: b"%PDF-fake")
    monkeypatch.setattr(
        core._source_cleanup, "cleanup_source_paragraphs",
        lambda *a, **k: (["نص عربي."], {"status": "completed"}, []))

    state = core.new_job_state("scan.pdf")
    state.update({
        "p1_done": True,
        "pairs": [{"source": "old", "target": "قديم"}],
        "paras": ["old"], "source_paras": ["old"],
        "source_lang": "日本語",
        "pipeline_config": {"ocr_language": "ara", "ocr_mode": "auto"},
        "streaming_ingest": {"status": "running", "paragraph_pages": [1]},
        "glossary_frozen": {"version": 1},
    })
    core.save_job_state("rescan-mode-test", state)
    if backend == "ocr_api":
        core.save_ocr_online_config(provider="OpenCode Go", model="ocr-model",
                                    api_key="test-key", base_url="")
    result = core.rescan_pdf_source(
        "rescan-mode-test", provider="DeepSeek", api_key="k", model="m",
        ocr_backend=backend, ocr_online={} if backend == "ocr_api" else None,
        ocr_mode="force", _allow_active=True)
    assert captured["ocr_backend"] == backend
    if backend == "ocr_api":
        assert captured["ocr_online_config"].model == "ocr-model"
        assert captured["ocr_language"] == "jpn"
        assert result["pipeline_config"]["ocr_language"] == "jpn"
    assert captured["ocr_mode"] == "force"
    assert result["pipeline_config"]["ocr_mode"] == "force"
    assert result["ocr_mode"] == "force"
    assert result["source_cleanup"]["ocr_mode_changed"] is True
    assert result["pairs"] == []  # source-derived translations invalidated
    assert "streaming_ingest" not in result
    assert result["ocr_backend"] == backend


def test_legacy_state_without_ocr_mode_defaults_to_auto():
    state = core.new_job_state("legacy.pdf")
    assert state.get("ocr_mode") == "auto"


def test_real_clean_chinese_pdf_is_accepted(monkeypatch, fake_ocr):
    import fitz
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    # PyMuPDF ships a built-in Simplified Chinese font.
    page.insert_text((72, 120), "这是一个干净的电子文档文本层，用于回归测试。", fontsize=14,
                     fontname="china-s")
    payload = document.tobytes()
    document.close()
    _p, _w, report = core.extract_document_paragraphs_with_report(
        "digital-cn.pdf", payload, ocr_backend="tesseract", ocr_mode="auto", source_language="简体中文")
    assert report["pdf_text_layer_quality"] != "rejected", report.get(
        "pdf_text_layer_rejection_reasons")
    assert report["ocr_used"] is False
    assert fake_ocr == []
