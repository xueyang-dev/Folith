"""Cross-script translation transport and RTL delivery regressions."""
import io
import json
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest
from docx import Document

import core
from folith.segmentation import segment_paragraphs
from folith.textual import text_direction

ARABIC = "OpenAI API: مهلة الطلب 30 ثانية (انظر القسم 2)."
SAMPLES = [
    ("Français", "Le rapport est prêt."),
    ("Deutsch", "Der Bericht ist fertig."),
    ("Español", "El informe está listo."),
    ("日本語", "報告書の準備ができました。"),
    ("한국어", "보고서가 준비되었습니다."),
    ("हिन्दी", "रिपोर्ट तैयार है।"),
    ("বাংলা", "প্রতিবেদন প্রস্তুত।"),
    ("ภาษาไทย", "รายงานพร้อมแล้ว"),
    ("Türkçe", "Rapor hazır."),
    ("Kiswahili", "Ripoti iko tayari."),
    ("Ελληνικά", "Η έκθεση είναι έτοιμη."),
    ("עברית", "הדוח מוכן."),
    ("فارسی", "گزارش آماده است."),
    ("اردو", "رپورٹ تیار ہے۔"),
]
W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@pytest.mark.parametrize("language,text", SAMPLES)
@pytest.mark.parametrize("reverse", [False, True])
def test_any_language_name_reaches_translation_protocol(language, text, reverse):
    source, target, target_language = (ARABIC, text, language) if reverse else (text, ARABIC, "العربية")

    def response(provider, key, model, system, user, **kwargs):
        assert target_language in system
        assert source in user
        return json.dumps([target], ensure_ascii=False)

    assert core.translate_batch([source], [], [], "", "", target_language,
                                "OpenAI", "unused", "gpt-4o-mini",
                                call_llm_fn=response) == [target]


@pytest.mark.parametrize("text,language,expected", [
    (ARABIC, "العربية", "rtl"), ("OpenAI API", "ar-SA", "rtl"),
    ("", "现代标准阿拉伯语", "rtl"), (ARABIC, None, "rtl"),
    ("AI: הדוח מוכן", "he", "rtl"), ("API: گزارش آماده است", "fa", "rtl"),
    ("مرحبا", "ar-Latn", "ltr"), ("", "az-Arab", "rtl"),
    ("漢字 ١٢٣٤", None, "ltr"), ("日本語", "日本語", "ltr"),
    ("1234", None, "ltr"), ("", "ar", "rtl"),
])
def test_direction_respects_language_script_and_mixed_prefixes(text, language, expected):
    assert text_direction(text, language) == expected


def test_docx_bilingual_columns_and_annotation_runs_keep_independent_directions():
    pairs = [{"source": "The API request timeout is 30 seconds.", "target": ARABIC}]
    output = core.pairs_to_word(pairs, source_lang="en", target_lang="ar",
                               annotations={0: [{"type": "domain", "src_span": [4, 7],
                                                 "tgt_span": [0, 10]}]})
    doc = Document(output)
    source, target = doc.tables[0].rows[1].cells
    assert source.text == pairs[0]["source"] and target.text == ARABIC
    assert source.paragraphs[0].alignment == 0
    assert target.paragraphs[0]._p.find("w:pPr/w:jc", W).get(f'{{{W["w"]}}}val') == "start"
    assert len(target.paragraphs[0].runs) > 1
    with ZipFile(output) as archive:
        xml = ET.fromstring(archive.read("word/document.xml"))
    assert xml.findall('.//w:bidi[@w:val="1"]', W)


def test_delivery_bundle_passes_language_to_single_bilingual_and_pdf_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    # A Latin-only preserved paragraph still belongs to an Arabic document.
    doc = Document()
    doc.add_paragraph("API")
    source = io.BytesIO()
    doc.save(source)
    state = core.new_job_state("source.docx")
    state.update(p1_done=True, p2_done=True, target_lang="ar", source_lang="en",
                 source_paras=["API"], paras=["API"],
                 pairs=[{"source": "API", "target": "API", "reviewed": True}],
                 delivery_config=core.normalize_delivery_config({
                     "deliver_plain_docx": True, "deliver_bilingual_docx": True,
                     "deliver_pdf": True, "deliver_epub": True}))
    core.save_source("rtl-bundle", source.getvalue())
    bundle = core._delivery_asset_bundle("rtl-bundle", state, "", "", "")
    for name in ["translation.docx", "bilingual.docx"]:
        with ZipFile(io.BytesIO(bundle[name])) as archive:
            xml = ET.fromstring(archive.read("word/document.xml"))
        assert xml.findall('.//w:bidi[@w:val="1"]', W)
        assert xml.findall('.//w:jc[@w:val="start"]', W)
    assert bundle["translation.pdf"].startswith(b"%PDF")
    with ZipFile(io.BytesIO(bundle["translation.epub"])) as archive:
        chapters = [ET.fromstring(archive.read(n)) for n in archive.namelist()
                    if n.endswith(".xhtml")]
        package = ET.fromstring(archive.read("EPUB/content.opf"))
    assert all(c.get("dir") == "rtl" for c in chapters)
    assert package.find("{http://www.idpf.org/2007/opf}spine").get("page-progression-direction") == "rtl"


def test_arabic_question_marks_split_without_losing_text():
    first = "متى يبدأ الاجتماع؟"
    second = "من المسؤول عن تقديم التقرير؟"
    units, _ = segment_paragraphs([first + " " + second], mode="sentence", source_lang="ar")
    assert units == [first, second]
    assert core._count_sentences(first + " " + second) == 2


def test_pdf_arabic_with_latin_prefix_reaches_right_margin():
    data = core.translations_to_pdf(
        [{"target": "API: مهلة الطلب 30 ثانية."}], target_lang="ar")
    with core.fitz.open(stream=data, filetype="pdf") as doc:
        spans = [span for block in doc[0].get_text("dict")["blocks"]
                 for line in block.get("lines", []) for span in line["spans"]
                 if any(unicodedata.bidirectional(c) == "AL" for c in span["text"])]
        assert spans
        assert max(span["bbox"][2] for span in spans) > doc[0].rect.width * 0.85


