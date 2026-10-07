"""Explicit OCR language, language-pack validation, RTL ordering and Arabic cleanup.

These tests pin the contract introduced by the OCR/RTL repair phase:

* the OCR language comes from the user's task configuration, never from the set
  of installed language packs;
* a missing language pack fails visibly instead of silently running ``eng``;
* Tesseract TSV logical word order (``word_num``) wins over visual x-coordinates,
  so RTL documents are not re-reversed;
* Arabic punctuation participates in segmentation and cleanup without the
  English lowercase / hyphen heuristics leaking in;
* the OCR language / direction / DPI / PSM travel through checkpoints, resume
  and rescan.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core
from folith import ocr_cache
from folith import source_cleanup
from folith.ocr_language import (
    OCR_LANGUAGE_REGISTRY,
    direction_for,
    resolve_ocr_language,
    validate_language_packs,
)
from folith.segmentation import segment_paragraphs


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _pdf_bytes(page_count: int = 1) -> bytes:
    import fitz
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=595, height=842)
    payload = document.tobytes()
    document.close()
    return payload


def _tsv(words):
    """Build a Tesseract word-level TSV.

    ``words`` is a list of dicts with ``text``, ``word_num`` and ``left``.
    """
    header = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\t"
              "left\ttop\twidth\theight\tconf\ttext")
    rows = [header]
    for index, word in enumerate(words):
        left = float(word["left"])
        rows.append("\t".join([
            "5", "1", "1", "1", "1", str(int(word["word_num"])),
            str(int(left)), str(int(word.get("top", 10))),
            "60", "20", "96.5", str(word["text"]),
        ]))
    return "\n".join(rows) + "\n"


ARABIC_WORDS = [
    {"text": "هذا", "word_num": 1, "left": 320},
    {"text": "نص", "word_num": 2, "left": 240},
    {"text": "عربي", "word_num": 3, "left": 150},
    {"text": "جميل", "word_num": 4, "left": 70},
]
ENGLISH_WORDS = [
    {"text": "The", "word_num": 1, "left": 70},
    {"text": "quick", "word_num": 2, "left": 130},
    {"text": "brown", "word_num": 3, "left": 210},
    {"text": "fox", "word_num": 4, "left": 290},
]


@pytest.fixture
def routed_ocr(monkeypatch):
    """Capture the language / direction actually handed to the page engine."""
    calls = []

    def engine(_command, _png, language, **_kwargs):
        calls.append(language)
        return ([{"text": "page body", "confidence": 99.0, "left": 1.0,
                  "top": 1.0, "right": 2.0, "bottom": 2.0}], None)

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)
    return calls


# --------------------------------------------------------------------------
# Test A — Arabic language routing
# --------------------------------------------------------------------------
def test_arabic_language_routing_uses_the_selected_language(
        monkeypatch, routed_ocr):
    monkeypatch.setattr(core, "_tesseract_languages",
                        lambda _cmd: ["eng", "ara", "chi_sim"])
    result = core._ocr_pdf_text_with_warnings(
        _pdf_bytes(), workers=1, queue_size=1, ocr_language="ara",
        return_details=True)
    assert routed_ocr == ["ara"]
    assert result[2]["ocr_language_engine_code"] == "ara"
    assert result[2]["ocr_text_direction"] == "rtl"
    assert "eng" not in routed_ocr and "ara+eng" not in routed_ocr


def test_tesseract_command_uses_the_explicit_language(monkeypatch):
    seen = {}

    class _Proc:
        returncode = 0
        stdout = _tsv(ARABIC_WORDS)
        stderr = ""

    def fake_run(argv, **_kwargs):
        seen["argv"] = argv
        return _Proc()

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    blocks, error = core._ocr_page_with_tesseract(
        "tesseract", b"png", "ara", direction="rtl")
    assert error is None
    assert seen["argv"][seen["argv"].index("-l") + 1] == "ara"
    assert blocks and blocks[0]["text"] == "هذا نص عربي جميل"


# --------------------------------------------------------------------------
# Test B — explicit Arabic + English
# --------------------------------------------------------------------------
def test_explicit_arabic_plus_english_routes_to_ara_eng(
        monkeypatch, routed_ocr):
    monkeypatch.setattr(core, "_tesseract_languages",
                        lambda _cmd: ["eng", "ara", "chi_sim"])
    result = core._ocr_pdf_text_with_warnings(
        _pdf_bytes(), workers=1, queue_size=1, ocr_language="ara+eng",
        return_details=True)
    assert routed_ocr == ["ara+eng"]
    assert result[2]["ocr_language_engine_code"] == "ara+eng"
    assert result[2]["ocr_text_direction"] == "rtl"


def test_registry_supports_single_and_combined_selection():
    assert resolve_ocr_language("Arabic").engine_code == "ara"
    assert resolve_ocr_language("العربية").engine_code == "ara"
    assert resolve_ocr_language("ar").engine_code == "ara"
    assert resolve_ocr_language("Arabic + English").engine_code == "ara+eng"
    assert resolve_ocr_language("ara+eng").direction == "rtl"
    assert resolve_ocr_language("Chinese + English").engine_code == "chi_sim+eng"
    assert resolve_ocr_language("English").engine_code == "eng"
    assert resolve_ocr_language("English").direction == "ltr"
    # The registry is the single source of truth, not scattered if/else.
    assert {"ara", "chi_sim", "eng"} <= {
        item["tesseract"] for item in OCR_LANGUAGE_REGISTRY.values()}


# --------------------------------------------------------------------------
# Test C — missing language pack must fail visibly
# --------------------------------------------------------------------------
def test_missing_arabic_pack_fails_visibly_without_fallback(
        monkeypatch, routed_ocr):
    monkeypatch.setattr(core, "_tesseract_languages",
                        lambda _cmd: ["eng", "chi_sim"])
    text, warnings, details = core._ocr_pdf_text_with_warnings(
        _pdf_bytes(), workers=1, queue_size=1, ocr_language="ara",
        return_details=True)
    assert text == ""
    assert routed_ocr == []  # the engine is never invoked with a wrong language
    assert details["ocr_error"] == "language_pack_missing"
    assert details["installed_languages"] == ["eng", "chi_sim"]
    assert any("ara" in warning for warning in warnings)
    assert any("eng" in warning and "chi_sim" in warning for warning in warnings)


def test_validate_language_packs_reports_every_missing_code():
    report = validate_language_packs("ara+eng", ["eng", "chi_sim"])
    assert report["ok"] is False
    assert report["missing"] == ["ara"]
    assert report["engine_code"] == "ara+eng"
    assert validate_language_packs("eng", ["eng"])["ok"] is True


# --------------------------------------------------------------------------
# Test D / E — RTL logical order, LTR regression
# --------------------------------------------------------------------------
def test_rtl_tsv_uses_logical_word_order_not_visual_x():
    blocks = core._parse_tesseract_tsv(_tsv(ARABIC_WORDS), direction="rtl")
    assert [block["text"] for block in blocks] == ["هذا نص عربي جميل"]
    # The visual left-to-right order is the reverse of the logical order; the
    # parser must not have produced that reversal.
    assert blocks[0]["text"] != "جميل عربي نص هذا"


def test_ltr_tsv_keeps_the_existing_left_to_right_order():
    blocks = core._parse_tesseract_tsv(_tsv(ENGLISH_WORDS), direction="ltr")
    assert [block["text"] for block in blocks] == ["The quick brown fox"]


def test_rtl_fallback_without_word_num_reads_right_to_left():
    words = [dict(word, word_num=0) for word in ARABIC_WORDS]
    blocks = core._parse_tesseract_tsv(_tsv(words), direction="rtl")
    assert [block["text"] for block in blocks] == ["هذا نص عربي جميل"]


# --------------------------------------------------------------------------
# Test F — Arabic punctuation in segmentation
# --------------------------------------------------------------------------
def test_arabic_question_mark_is_terminal_and_comma_is_not():
    text = "هذا نص عربي. هل هذا صحيح؟ نعم، هذا صحيح."
    units, _ = segment_paragraphs([text], mode="sentence", source_lang="ar")
    assert units == ["هذا نص عربي.", "هل هذا صحيح؟", "نعم، هذا صحيح."]
    assert sum("،" in unit for unit in units) == 1


# --------------------------------------------------------------------------
# Test G — Arabic cleanup must not use English heuristics
# --------------------------------------------------------------------------
def test_arabic_question_mark_is_a_sentence_terminal_in_cleanup():
    assert source_cleanup._is_sentence_terminal("هل هذا صحيح؟") is True
    assert source_cleanup._is_sentence_terminal("نعم، هذا صحيح.") is True
    assert source_cleanup._is_sentence_terminal("نعم، هذا صحيح") is False


def test_arabic_lines_are_not_joined_by_the_lowercase_heuristic():
    items = ["هذا نص عربي", "متابعة النص"]
    cleaned, break_after, merges, _changed = source_cleanup._deterministic_prepare(items)
    assert merges == []
    assert break_after == [True, True]
    assert cleaned == items


def test_arabic_hallucination_is_rejected_by_script_aware_similarity():
    before = "هذا نص عربي طويل يتحدث عن التاريخ واللغة والثقافة في المنطقة."
    after = "هذا مقال مختلف تماما عن الطقس والرياضة ولا علاقة له بالنص الأصلي."
    assert source_cleanup._safe_candidate(before, after) == before


# --------------------------------------------------------------------------
# Test H — checkpoint isolation by OCR configuration
# --------------------------------------------------------------------------
def test_checkpoint_is_not_reused_across_ocr_language(tmp_path, monkeypatch):
    calls = []

    def engine(_command, _png, language, **_kwargs):
        calls.append(language)
        return ([{"text": f"body {language}", "confidence": 99.0,
                  "left": 1.0, "top": 1.0, "right": 2.0, "bottom": 2.0}], None)

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_tesseract_languages",
                        lambda _cmd: ["eng", "ara", "chi_sim"])
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)
    pdf = _pdf_bytes(3)
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, ocr_language="eng")
    assert len(calls) == 3
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, ocr_language="ara")
    assert len(calls) == 3  # nothing from the eng run may be reused


def test_checkpoint_is_not_reused_across_psm(tmp_path, monkeypatch):
    calls = []

    def engine(_command, _png, language, **_kwargs):
        calls.append(language)
        return ([{"text": "body", "confidence": 99.0, "left": 1.0, "top": 1.0,
                  "right": 2.0, "bottom": 2.0}], None)

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_tesseract_languages", lambda _cmd: ["eng"])
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)
    pdf = _pdf_bytes(2)
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, ocr_language="eng",
        ocr_psm=3)
    assert len(calls) == 2
    calls.clear()
    core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path, ocr_language="eng",
        ocr_psm=6)
    assert len(calls) == 2


def test_identity_distinguishes_language_direction_and_psm():
    base = dict(source_sha256="a" * 64, page_index=0, backend="tesseract",
                model="tesseract-lstm", backend_version="5.0.0")
    keys = {
        ocr_cache.OCRPageIdentity(language="eng", dpi=220, **base).key(),
        ocr_cache.OCRPageIdentity(language="ara", dpi=220, **base).key(),
        ocr_cache.OCRPageIdentity(language="ara+eng", dpi=220, **base).key(),
        ocr_cache.OCRPageIdentity(language="chi_sim", dpi=220, **base).key(),
        ocr_cache.OCRPageIdentity(language="ara", dpi=220, direction="rtl",
                                  **base).key(),
        ocr_cache.OCRPageIdentity(language="ara", dpi=220, psm=6, **base).key(),
    }
    assert len(keys) == 6


# --------------------------------------------------------------------------
# Test I — resume keeps language / direction / DPI
# --------------------------------------------------------------------------
def test_resume_keeps_ocr_language_direction_and_dpi(tmp_path, monkeypatch):
    seen = []

    def engine(_command, _png, language, **_kwargs):
        seen.append(language)
        return ([{"text": "body", "confidence": 99.0, "left": 1.0, "top": 1.0,
                  "right": 2.0, "bottom": 2.0}], None)

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_tesseract_languages", lambda _cmd: ["ara"])
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)
    pdf = _pdf_bytes(4)
    cancel = {"flag": False}

    def cancel_check():
        if len(seen) >= 2:
            cancel["flag"] = True
        return cancel["flag"]

    with pytest.raises(RuntimeError, match="取消"):
        core._ocr_pdf_text_with_warnings(
            pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path,
            ocr_language="ara", dpi=300, ocr_psm=6, cancel_check=cancel_check)
    cancel["flag"] = False
    seen.clear()
    _text, _warnings, details = core._ocr_pdf_text_with_warnings(
        pdf, workers=1, queue_size=1, checkpoint_dir=tmp_path,
        ocr_language="ara", dpi=300, ocr_psm=6, return_details=True)
    assert details["ocr_language_engine_code"] == "ara"
    assert details["ocr_text_direction"] == "rtl"
    assert details["ocr_dpi"] == 300
    assert details["ocr_psm"] == 6
    assert details["installed_language_available"] is True
    assert details["page_order"] == [1, 2, 3, 4]


# --------------------------------------------------------------------------
# Test J — rescan with a changed language invalidates downstream artifacts
# --------------------------------------------------------------------------
def test_rescan_with_changed_language_invalidates_downstream(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    captured = {}

    monkeypatch.setattr(core, "_pdf_extraction_facts", lambda _b: {})
    monkeypatch.setattr(core, "extract_pdf_paragraphs", lambda _b: [])

    def fake_extract(filename, file_bytes, **kwargs):
        captured["ocr_language"] = kwargs.get("ocr_language")
        return (["نص عربي جديد."], [], {"ocr": {"paragraph_confidences": [95.0]}})

    monkeypatch.setattr(core, "extract_document_paragraphs_with_report", fake_extract)
    monkeypatch.setattr(core, "load_source", lambda _job_id: b"%PDF-fake")
    monkeypatch.setattr(
        core._source_cleanup, "cleanup_source_paragraphs",
        lambda *a, **k: (["نص عربي جديد."], {"status": "completed"}, []))

    state = core.new_job_state("scan.pdf")
    state.update({
        "p1_done": True,
        "pairs": [{"source": "old", "target": "قديم"}],
        "paras": ["old"],
        "source_paras": ["old"],
        "pipeline_config": {"ocr_language": "eng"},
        "glossary_frozen": {"version": 1},
    })
    core.save_job_state("rescan-lang-test", state)
    result = core.rescan_pdf_source(
        "rescan-lang-test", provider="DeepSeek", api_key="k", model="m",
        ocr_language="ara", _allow_active=True)
    assert captured["ocr_language"] == "ara"
    assert result["pipeline_config"]["ocr_language"] == "ara"
    assert result["pairs"] == []  # old source-derived translations invalidated


# --------------------------------------------------------------------------
# DPI / quality profiles and observability
# --------------------------------------------------------------------------
def test_scan_quality_profiles_map_to_explicit_dpi():
    assert core.ocr_quality_dpi("standard") == 220
    assert core.ocr_quality_dpi("high") == 300
    assert core.ocr_quality_dpi("historical") == 350
    assert [item["dpi"] for item in core.ocr_quality_options()] == [220, 300, 350]


def test_effective_config_keeps_saved_language_dpi_and_psm():
    saved = {"ocr_language": "ara", "ocr_dpi": 350, "ocr_psm": 6,
             "ocr_quality": "historical"}
    language, dpi, psm, quality, resolved = core._effective_ocr_config(
        None, None, None, saved)
    assert language == "ara"
    assert (dpi, psm, quality) == (350, 6, "historical")
    assert resolved.engine_code == "ara" and resolved.direction == "rtl"
    # A new task with no explicit language must stay None so the OCR stage uses
    # its documented legacy fallback instead of silently guessing here.
    assert core._effective_ocr_config(None, None, None, {})[0] is None


def test_extraction_report_records_the_real_ocr_configuration(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    captured = {}

    def fake_ocr(file_bytes, max_pages=None, on_progress=None, **kwargs):
        captured.update(kwargs)
        return ("نص عربي.", [], {
            "ocr_language": "ara", "ocr_language_engine_code": "ara",
            "ocr_text_direction": "rtl", "ocr_dpi": 300, "ocr_psm": 13,
            "installed_language_available": True, "installed_languages": ["ara"],
            "ocr_backend": "tesseract", "paragraph_confidences": [95.0],
        })

    monkeypatch.setattr(core, "_ocr_pdf_text_with_warnings", fake_ocr)
    monkeypatch.setattr(core, "_pdf_extraction_facts", lambda _b: {})
    monkeypatch.setattr(core, "extract_pdf_paragraphs", lambda _b: [])
    # The local engine is named explicitly: an unspecified source resolves to the
    # online engine for a new task, which fails before the local OCR entry point
    # this test replaces.
    _paragraphs, _warnings, report = core.extract_document_paragraphs_with_report(
        "scan.pdf", b"%PDF-fake", ocr_language="ara", ocr_dpi=300, ocr_psm=13,
        ocr_backend="tesseract")
    assert captured["ocr_language"] == "ara"
    assert captured["dpi"] == 300 and captured["ocr_psm"] == 13
    ocr = report["ocr"]
    for key, value in {"ocr_language_engine_code": "ara",
                       "ocr_text_direction": "rtl", "ocr_dpi": 300,
                       "ocr_psm": 13, "installed_language_available": True}.items():
        assert ocr[key] == value


# --------------------------------------------------------------------------
# UI selector
# --------------------------------------------------------------------------
def test_new_task_shows_explicit_ocr_language_selector_for_pdf(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    at = AppTest.from_file(str(app_path), default_timeout=60)
    at.session_state["app_view"] = "new"
    at.session_state["workspace_mode"] = False
    at.session_state["task_step"] = 1
    at.session_state["ocr_language"] = "ara"
    at.session_state["task_files"] = [{"name": "scan.pdf", "bytes": b"%PDF-1.4"}]
    at.run()
    assert not at.exception, at.exception
    keys = {widget.key for widget in at.selectbox}
    assert "ocr_language" in keys
    assert "ocr_quality" in keys
    selector = next(widget for widget in at.selectbox if widget.key == "ocr_language")
    assert selector.value == "ara"
    # The selector is a visible, modifiable configuration, not a detected value.
    assert selector.proto.options


def test_performance_artifact_records_the_real_ocr_configuration(
        tmp_path, monkeypatch):
    from folith.performance import PipelineProfiler

    def engine(_command, _png, language, **_kwargs):
        return ([{"text": "نص", "confidence": 99.0, "left": 1.0, "top": 1.0,
                  "right": 2.0, "bottom": 2.0}], None)

    monkeypatch.setattr(core, "_find_tesseract", lambda: "fake-tesseract")
    monkeypatch.setattr(core, "_tesseract_languages", lambda _cmd: ["ara", "eng"])
    monkeypatch.setattr(core, "_ocr_backend_version", lambda *_a, **_k: "5.0.0")
    monkeypatch.setattr(core, "_ocr_page_with_tesseract", engine)
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id="ocr-config")
    core._ocr_pdf_text_with_warnings(
        _pdf_bytes(2), workers=1, queue_size=1, checkpoint_dir=tmp_path / "job",
        ocr_language="ara", dpi=300, ocr_psm=6, profiler=profiler,
        return_details=True)
    metadata = profiler.snapshot()["stages"]["ocr"]["metadata"]
    assert metadata["ocr_language_engine_code"] == "ara"
    assert metadata["ocr_text_direction"] == "rtl"
    assert metadata["ocr_dpi"] == 300
    assert metadata["ocr_psm"] == 6
    assert metadata["ocr_backend"] == "tesseract"
    assert metadata["installed_language_available"] is True
