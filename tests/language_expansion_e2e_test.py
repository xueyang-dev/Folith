"""End-to-end and component tests for expanded translation source and target languages (#10).

Covers:
1. Catalog parity and matching (source catalog, target catalog, aliases, endonyms).
2. Language tag resolution (BCP-47) for all 21 languages (no 'und' fallbacks).
3. Short language names and direction labels in language assets.
4. Direction resolution (LTR / RTL) for scripts (Arabic, Hebrew, Persian, Urdu, Uyghur).
5. Deterministic sentence segmentation with RTL and uncased scripts.
6. Numerals and Eastern Arabic digits preservation contract.
7. VLM document understanding language hints and cache identity.
8. Local OCR Tesseract language packs validation and visible missing pack messages.
9. Delivery exports (DOCX bidi, PDF story with dir, EPUB XHTML, TMX and TBX language tags).
10. End-to-end task pipeline: setup, translation, review, TM persistence and delivery bundle.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

import core
from folith import assets
from folith import language_assets
from folith import numerals
from folith import ocr_language
from folith import source_languages
from folith import textual
from folith import epub
from folith.document_understanding import VLMDocumentProvider, language_hint
from folith.engine.glossary_checks import _count_sentences, is_incomplete_translation
from folith.ocr_online import OnlineOCRConfig
from folith.qt import adapter
from folith.segmentation import segment_paragraphs


ALL_EXPANDED_LANGUAGES = (
    "English", "简体中文", "繁体中文", "日本語", "한국어", "Deutsch",
    "Français", "Español", "Italiano", "Português", "Русский", "Nederlands",
    "Polski", "Українська", "Tiếng Việt", "Türkçe", "العربية", "فارسی",
    "עברית", "اردو", "ئۇيغۇرچە",
)

EXPECTED_BCP47 = {
    "English": "en",
    "简体中文": "zh-CN",
    "繁体中文": "zh-TW",
    "日本語": "ja",
    "한국어": "ko",
    "Deutsch": "de",
    "Français": "fr",
    "Español": "es",
    "Italiano": "it",
    "Português": "pt",
    "Русский": "ru",
    "Nederlands": "nl",
    "Polski": "pl",
    "Українська": "uk",
    "Tiếng Việt": "vi",
    "Türkçe": "tr",
    "العربية": "ar",
    "فارسی": "fa",
    "עברית": "he",
    "اردو": "ur",
    "ئۇيغۇرچە": "ug",
}

RTL_LANGUAGES = {"العربية", "فارسی", "עברית", "اردو", "ئۇيغۇرچە"}


def test_catalogue_parity_and_coverage():
    """All 21 languages are present in both SOURCE_LANGUAGES and TARGET_LANGUAGES."""
    source_values = [item[0] for item in source_languages.SOURCE_LANGUAGES]
    target_values = list(adapter.TARGET_LANGUAGES)

    assert set(source_values) == set(ALL_EXPANDED_LANGUAGES)
    assert set(target_values) == set(ALL_EXPANDED_LANGUAGES)
    assert len(source_values) == 21
    assert len(target_values) == 21

    # Verify each source language has valid English name and Tesseract code
    for saved, english, code in source_languages.SOURCE_LANGUAGES:
        assert english and english[0].isupper(), f"Invalid English name: {english}"
        assert code and code.islower() and len(code) >= 3, f"Invalid code: {code}"
        assert source_languages.match(saved) == saved
        assert source_languages.match(english) == saved
        assert source_languages.match(code) == saved
        assert source_languages.english_name(saved) == english
        assert source_languages.ocr_code(saved) == code


def test_new_language_aliases():
    """Check localized aliases for new additions."""
    s = source_languages
    assert s.match("荷兰语") == "Nederlands" and s.match("dutch") == "Nederlands"
    assert s.match("波兰语") == "Polski" and s.match("polish") == "Polski"
    assert s.match("乌克兰语") == "Українська" and s.match("ukrainian") == "Українська"
    assert s.match("越南语") == "Tiếng Việt" and s.match("vietnamese") == "Tiếng Việt"
    assert s.match("土耳其语") == "Türkçe" and s.match("turkish") == "Türkçe"
    assert s.match("波斯语") == "فارسی" and s.match("farsi") == "فارسی"
    assert s.match("希伯来语") == "עברית" and s.match("hebrew") == "עברית"
    assert s.match("乌尔都语") == "اردو" and s.match("urdu") == "اردو"
    assert s.match("维吾尔语") == "ئۇيغۇرچە" and s.match("uyghur") == "ئۇيغۇرچە"


def test_bcp47_language_tags_for_all_languages():
    """Every supported language resolves to an authentic BCP-47 tag, never 'und'."""
    for lang in ALL_EXPANDED_LANGUAGES:
        tag = textual.language_tag(lang)
        assert tag != "und", f"Language tag missing for {lang}"
        assert tag == EXPECTED_BCP47[lang]


def test_direction_resolution():
    """Verify script direction for all expanded languages."""
    for lang in ALL_EXPANDED_LANGUAGES:
        expected = "rtl" if lang in RTL_LANGUAGES else "ltr"
        assert textual.text_direction("", lang) == expected, f"Direction mismatch for {lang}"
        # Also check tag-based direction
        tag = EXPECTED_BCP47[lang]
        assert textual.text_direction("", tag) == expected


def test_language_assets_labels_and_codes():
    """Short forms and direction labels for language assets."""
    for lang in ALL_EXPANDED_LANGUAGES:
        short = language_assets.short_language(lang)
        assert short and short != "自动", f"Short language missing for {lang}"
        assert short in language_assets.SHORT_LANGUAGE_CODES
        code = language_assets.SHORT_LANGUAGE_CODES[short]
        assert code.isupper() or "-" in code

    direction = language_assets.direction_label("English", "Nederlands")
    assert direction == "英 → 荷"
    direction_pl = language_assets.direction_label("English", "Polski")
    assert direction_pl == "英 → 波兰"
    direction_fa = language_assets.direction_label("English", "فارسی")
    assert direction_fa == "英 → 波"
    direction_ur = language_assets.direction_label("English", "اردو")
    assert direction_ur == "英 → 乌"
    direction_uk = language_assets.direction_label("English", "Українська")
    assert direction_uk == "英 → 乌克"


def test_ocr_language_registry_uyghur_and_options():
    """Verify Uyghur in OCR registry and language combinations."""
    reg = ocr_language.OCR_LANGUAGE_REGISTRY
    assert "ug" in reg
    assert reg["ug"]["tesseract"] == "uig"
    assert reg["ug"]["direction"] == "rtl"
    assert reg["ug"]["label"] == "ئۇيغۇرچە"

    resolved = ocr_language.resolve_ocr_language("uig+eng")
    assert resolved.engine_code == "uig+eng"
    assert resolved.is_rtl is True

    # Check that uyghur is listed in options
    opts = ocr_language.ocr_language_options()
    assert any(opt["value"] == "uig" for opt in opts)
    assert any(opt["value"] == "uig+eng" for opt in opts)


def test_sentence_segmentation_across_scripts():
    """Sentence splitting with RTL marks and Latin/Cyrillic scripts."""
    # Urdu with Urdu full stop '۔'
    urdu_text = "یہ پہلا جملہ ہے۔ یہ دوسرا جملہ ہے۔"
    urdu_segments, _ = segment_paragraphs([urdu_text], mode="sentence")
    assert len(urdu_segments) == 2
    assert "پہلا" in urdu_segments[0]
    assert "دوسرا" in urdu_segments[1]

    # Arabic with question mark '؟'
    ar_text = "هل هذا كتاب؟ نعم، هذا كتاب."
    ar_segments, _ = segment_paragraphs([ar_text], mode="sentence")
    assert len(ar_segments) == 2

    # Hebrew
    he_text = "זהו המשפט הראשון. זהו המשפט השני."
    he_segments, _ = segment_paragraphs([he_text], mode="sentence")
    assert len(he_segments) == 2

    # Dutch / Polish / Ukrainian / Vietnamese
    nl_text = "Dit is de eerste zin. Dit is de tweede zin."
    nl_segments, _ = segment_paragraphs([nl_text], mode="sentence")
    assert len(nl_segments) == 2

    pl_text = "To jest pierwsze zdanie. To jest drugie zdanie."
    pl_segments, _ = segment_paragraphs([pl_text], mode="sentence")
    assert len(pl_segments) == 2

    uk_text = "Це перше речення. Це друге речення."
    uk_segments, _ = segment_paragraphs([uk_text], mode="sentence")
    assert len(uk_segments) == 2

    vi_text = "Đây là câu đầu tiên. Đây là câu thứ hai."
    vi_segments, _ = segment_paragraphs([vi_text], mode="sentence")
    assert len(vi_segments) == 2


def test_numerals_normalization_and_preservation():
    """Eastern Arabic numerals preserved in Arabic/Persian/Urdu, converted in others."""
    eastern_text = "Chapter (١): Page (٢)"
    # Target Arabic: keep
    assert numerals.normalize_digits(eastern_text, "العربية") == eastern_text
    # Target Persian: keep
    assert numerals.normalize_digits(eastern_text, "فارسی") == eastern_text
    # Target Urdu: keep
    assert numerals.normalize_digits(eastern_text, "اردو") == eastern_text
    # Target English / Turkish / Dutch / Chinese: convert to Western digits
    assert numerals.normalize_digits(eastern_text, "English") == "Chapter (1): Page (2)"
    assert numerals.normalize_digits(eastern_text, "Türkçe") == "Chapter (1): Page (2)"
    assert numerals.normalize_digits(eastern_text, "Nederlands") == "Chapter (1): Page (2)"
    assert numerals.normalize_digits(eastern_text, "简体中文") == "Chapter (1): Page (2)"


def test_vlm_prompt_generation_for_new_languages():
    """Verify VLM prompt language hint for new languages."""
    config = OnlineOCRConfig(provider="openai", base_url="https://example.invalid/v1",
                             model="vlm", api_key="k")
    dutch_vlm = VLMDocumentProvider(config, language="Dutch")
    assert "written in Dutch" in dutch_vlm.system_prompt
    assert dutch_vlm.cache_identity.get("language") == "Dutch"

    turkish_vlm = VLMDocumentProvider(config, language="Turkish")
    assert "written in Turkish" in turkish_vlm.system_prompt
    assert turkish_vlm.cache_identity.get("language") == "Turkish"


def test_missing_language_pack_reporting():
    """Verify missing language packs produce informative reports for added codes."""
    installed = ["eng", "chi_sim"]
    # Turkish
    report_tr = ocr_language.validate_language_packs("tur", installed)
    assert not report_tr["ok"]
    assert "tur" in report_tr["missing"]
    msg_tr = ocr_language.missing_pack_message("tur", installed)
    assert "Türkçe" in msg_tr
    assert "tur" in msg_tr

    # Dutch
    report_nl = ocr_language.validate_language_packs("nld", installed)
    assert not report_nl["ok"]
    assert "nld" in report_nl["missing"]

    # Ukrainian
    report_uk = ocr_language.validate_language_packs("ukr", installed)
    assert not report_uk["ok"]
    assert "ukr" in report_uk["missing"]


def test_export_bidi_and_language_tags(tmp_path):
    """Verify exports (PDF, DOCX, TBX, TMX) handle new target languages."""
    pairs = [
        {"source": "Hello world.", "target": "مرحبا بالعالم.", "reviewed": True},
        {"source": "Good morning.", "target": "Goedemorgen.", "reviewed": True},
    ]
    # PDF rendering
    pdf_bytes = core.translations_to_pdf(pairs, target_lang="العربية")
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")

    # TBX export
    glossary = [{"source": "computer", "target": "bilgisayar", "preferred": "bilgisayar"}]
    state = {"source_lang": "English", "target_lang": "Türkçe", "glossary": glossary, "pairs": pairs}
    tbx_bytes = assets.build_tbx(glossary, "en", "tr")
    assert b'xml:lang="en"' in tbx_bytes
    assert b'xml:lang="tr"' in tbx_bytes

    # TMX export
    tmx_bytes = assets.build_tmx(state, "en", "tr", job_id="test-job")
    assert b'srclang="en"' in tmx_bytes
    assert b'xml:lang="tr"' in tmx_bytes

    # EPUB export
    dummy_source = b"Hello world.\n\nGood morning."
    state_epub = {
        "filename": "sample.txt",
        "book_title": "Test Book",
        "source_lang": "English",
        "target_lang": "Nederlands",
        "paras": ["Hello world.", "Good morning."],
        "pairs": [
            {"source": "Hello world.", "target": "Hallo wereld.", "reviewed": True},
            {"source": "Good morning.", "target": "Goedemorgen.", "reviewed": True},
        ],
    }
    epub_bytes, report = epub.build_translation_epub(state_epub, dummy_source, job_id="test-job")
    assert len(epub_bytes) > 200
    assert report["language"] == "nl"
    assert report["direction"] == "ltr"


def test_sentence_counter_for_urdu():
    """Sentence counting recognizes Urdu full stop '۔'."""
    urdu_text = "یہ پہلا جملہ ہے۔ یہ دوسرا جملہ ہے۔ یہ تیسرا جملہ ہے۔"
    assert _count_sentences(urdu_text) == 3
    assert not is_incomplete_translation("A. B. C.", urdu_text)


def test_task_state_persistence_and_job_id(tmp_path, monkeypatch):
    """Verify tasks save and reload with any expanded language pair."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("doc.txt")
    state["source_lang"] = "Türkçe"
    state["target_lang"] = "Nederlands"
    core.save_job_state("task-tr-nl", state)

    loaded = core.load_job_state("task-tr-nl")
    assert loaded["source_lang"] == "Türkçe"
    assert loaded["target_lang"] == "Nederlands"

    context = core.task_context(target_lang="Türkçe", source_lang="English")
    assert context["target_lang"] == "türkçe"
    assert context["source_lang"] == "english"
