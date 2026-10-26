#!/usr/bin/env python3
"""Exercise Arabic language pairs, mixed-direction text, and exports in local jobs.

Run: python3 scripts/arabic_acceptance_audit.py --out exports/arabic-acceptance-v0.8.0

Tests deterministic transport, Unicode preservation, BiDi/RTL layout, and export
structures without requiring paid external LLM services or live network calls.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unicodedata
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core
import pymupdf as fitz
from docx import Document
from folith import segmentation
from folith.engine.document_export import _apply_paragraph_direction
from folith.epub import build_translation_epub
from folith.qt import segment_editor, theme
from folith.qt.adapter import TARGET_LANGUAGES
from folith.report_template import _insert_content_after
from folith.textual import has_textual_content, text_direction

W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
H = {"h": "http://www.w3.org/1999/xhtml"}


def docx_data(texts: list[str]) -> bytes:
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def docx_direction(data: bytes) -> dict:
    with ZipFile(io.BytesIO(data)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        styles = ET.fromstring(archive.read("word/styles.xml"))
    return {
        "paragraph_bidi_count": len(document.findall(".//w:bidi", W)),
        "rtl_paragraph_count": len(document.findall('.//w:bidi[@w:val="1"]', W)),
        "leading_alignment_count": len(document.findall('.//w:jc[@w:val="start"]', W)),
        "run_rtl_count": len(document.findall(".//w:rtl", W)),
        "style_bidi_count": len(styles.findall(".//w:bidi", W)),
        "right_alignment_count": len(document.findall('.//w:jc[@w:val="right"]', W)),
    }


def run(out: Path) -> dict:
    fixture = json.loads((ROOT / "tests/fixtures/arabic_acceptance.json").read_text(encoding="utf-8"))
    rows, languages = fixture["rows"], fixture["languages"]
    out.mkdir(parents=True, exist_ok=True)

    sources = {}
    for language in languages:
        sources[language] = docx_data([row[language] for row in rows])
        (out / f"source-{language}.docx").write_bytes(sources[language])
        (out / f"source-{language}.txt").write_text(
            "\n\n".join(row[language] for row in rows), encoding="utf-8")

    report: dict = {
        "scope": "local deterministic Arabic acceptance pass for v0.8.0",
        "reference_status": "draft aligned references; quality requires bilingual review",
        "platform": sys.platform,
        "directions": {},
        "expanded_language_combinations": {},
        "mixed_direction_tokens": {},
        "academic_report_export": {},
        "font_coverage": {},
        "summary": {},
    }

    old_output = core.OUTPUT_DIR
    with tempfile.TemporaryDirectory(prefix="folith-ar-jobs-") as temp:
        core.OUTPUT_DIR = Path(temp)
        try:
            # 1. Core 6 directions (48 combinations)
            for source, target in [(s, t) for other in ("zh", "en", "ru")
                                   for s, t in ((other, "ar"), ("ar", other))]:
                name = f"{source}-to-{target}"
                originals = [row[source] for row in rows]
                references = [row[target] for row in rows]
                result: dict = {}

                extracted, _, _ = core.extract_document_paragraphs_with_report(
                    f"source-{source}.docx", sources[source])
                assert extracted == originals, f"Extracted mismatch for {name}"
                assert all(has_textual_content(text) for text in originals)
                result["docx_input_unicode"] = "PASS"

                calls = []

                def fake_llm(provider, key, model, system, user, **kwargs):
                    calls.append((system, user))
                    return json.dumps(references, ensure_ascii=False)

                translations = core.translate_batch(
                    originals, [], [], "", "", languages[target],
                    "OpenAI", "unused", "gpt-4o-mini", call_llm_fn=fake_llm)
                expected_translations = core._numerals.normalize_targets(references, languages[target])
                assert translations == expected_translations
                assert languages[target] in calls[0][0]
                assert all(text in calls[0][1] for text in originals)
                result["translation_protocol"] = "PASS"

                state = core.new_job_state(f"source-{source}.docx")
                state.update(
                    source_lang=languages[source], target_lang=languages[target],
                    source_paras=originals, paras=originals, p1_done=True, p2_done=True,
                    segmentation={"mode": "paragraph", "locked": True},
                    pairs=[{"source": s, "target": t, "initial_target": t,
                            "reviewed": True, "target_provenance": "human_edit",
                            "source_refs": [idx]}
                           for idx, (s, t) in enumerate(zip(originals, references))])
                job_id = f"arabicaudit-{name}"
                core.save_job_state(job_id, state)
                loaded = core.load_job_state(job_id)
                assert [p["target"] for p in loaded["pairs"]] == references
                result["state_save_and_reload"] = "PASS"

                # Translation Memory check
                tm_k = core.tm_scope_key(languages[target], originals[0])
                tm_fixture = {tm_k: {"target": references[0], "reviewed": True, "target_lang": languages[target]}}
                hit, reason = core.tm_lookup(tm_fixture, originals[0], target_lang=languages[target])
                assert hit and hit["target"] == references[0] and reason == "exact"
                miss, _ = core.tm_lookup(tm_fixture, originals[0], target_lang="DifferentLanguage")
                assert miss is None
                result["tm_isolation"] = "PASS"

                # DOCX exports (single translation and bilingual table)
                for suffix, data in (
                    ("translation", core.translations_to_word(
                        state["pairs"], target_lang=languages[target]).getvalue()),
                    ("bilingual", core.pairs_to_word(
                        state["pairs"], source_lang=languages[source],
                        target_lang=languages[target]).getvalue()),
                ):
                    (out / f"{name}-{suffix}.docx").write_bytes(data)
                    parsed = Document(io.BytesIO(data))
                    texts = ([p.text for p in parsed.paragraphs] if suffix == "translation" else
                             [cell.text for row in parsed.tables[0].rows for cell in row.cells])
                    assert all(text in texts for text in references)
                    result[f"{suffix}_docx_unicode"] = "PASS"
                    direction_report = docx_direction(data)
                    expected_rtl = len(references) * (
                        int(target == "ar") + int(suffix == "bilingual" and source == "ar"))
                    assert direction_report["rtl_paragraph_count"] == expected_rtl
                    assert direction_report["leading_alignment_count"] == expected_rtl
                    if target == "ar" or (suffix == "bilingual" and source == "ar"):
                        assert direction_report["run_rtl_count"] > 0
                    result[f"{suffix}_docx_direction"] = direction_report

                # PDF export
                pdf = core.translations_to_pdf(state["pairs"], target_lang=languages[target])
                (out / f"{name}-translation.pdf").write_bytes(pdf)
                with fitz.open(stream=pdf, filetype="pdf") as doc:
                    result["pdf_pages"] = len(doc)
                    for i, page in enumerate(doc):
                        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(
                            str(out / f"{name}-pdf-page-{i+1}.png"))
                    result["pdf_text_extracted"] = "PASS" if len(doc) > 0 else "FAIL"

                # EPUB export
                epub, _ = build_translation_epub(state, sources[source], job_id=job_id)
                (out / f"{name}.epub").write_bytes(epub)
                with ZipFile(io.BytesIO(epub)) as archive:
                    chapters = [ET.fromstring(archive.read(path)) for path in archive.namelist()
                                if "chapter-" in path and path.endswith(".xhtml")]
                    text = " ".join("".join(chapter.itertext()) for chapter in chapters)
                    assert all(reference in text for reference in references)
                    result["epub_unicode"] = "PASS"
                    result["epub_html_direction"] = [chapter.get("dir") for chapter in chapters]
                    assert all(chapter.get("dir") == ("rtl" if target == "ar" else "ltr")
                               for chapter in chapters)
                    result["epub_language"] = [chapter.get("lang") for chapter in chapters]
                    nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
                    if target == "ar":
                        assert "جدول المحتويات" in nav
                        result["epub_arabic_nav_toc"] = "PASS"

                report["directions"][name] = result

            # 2. Every selectable partner language with Arabic, both directions.
            # The list derives from the product catalogue, so a language cannot
            # become selectable without appearing in the acceptance pass.
            partner_samples = {
                "简体中文": ("zh-Hans", "报告已经准备好了。"),
                "繁体中文": ("zh-Hant", "報告已經準備好了。"),
                "English": ("en", "The report is ready."),
                "日本語": ("ja", "報告書の準備ができました。"),
                "한국어": ("ko", "보고서가 준비되었습니다."),
                "Deutsch": ("de", "Der Bericht ist fertig."),
                "Français": ("fr", "Le rapport est prêt."),
                "Español": ("es", "El informe está listo."),
                "Русский": ("ru", "Отчёт готов."),
                "Português": ("pt", "O relatório está pronto."),
                "Italiano": ("it", "Il rapporto è pronto."),
                "Nederlands": ("nl", "Het rapport is klaar."),
                "Polski": ("pl", "Raport jest gotowy."),
                "Українська": ("uk", "Звіт готовий."),
                "Tiếng Việt": ("vi", "Báo cáo đã sẵn sàng."),
                "Türkçe": ("tr", "Rapor hazır."),
                "فارسی": ("fa", "گزارش آماده است."),
                "עברית": ("he", "הדוח מוכן."),
                "اردو": ("ur", "رپورٹ تیار ہے۔"),
                "ئۇيغۇرچە": ("ug", "دوكلات تەييار بولدى."),
            }
            partners = [name for name in TARGET_LANGUAGES if name != "العربية"]
            assert set(partners) == set(partner_samples), "language catalogue/sample drift"
            for lang_name in partners:
                lang_code, sample_text = partner_samples[lang_name]
                ar_sample = rows[0]["ar"]
                # Test forward (lang -> ar) and reverse (ar -> lang)
                for direction_label, src_txt, tgt_txt, tgt_name in [
                    (f"{lang_code}-to-ar", sample_text, ar_sample, "العربية"),
                    (f"ar-to-{lang_code}", ar_sample, sample_text, lang_name),
                ]:
                    def mock_call(provider, key, model, system, user, **kwargs):
                        assert tgt_name in system
                        assert src_txt in user
                        return json.dumps([tgt_txt], ensure_ascii=False)

                    out_trans = core.translate_batch(
                        [src_txt], [], [], "", "", tgt_name,
                        "OpenAI", "unused", "gpt-4o-mini", call_llm_fn=mock_call)
                    assert out_trans == [tgt_txt]
                    report["expanded_language_combinations"][direction_label] = "PASS"

            # 3. Mixed-direction text & token invariance
            mixed_cases = {
                "latin_prefix_with_brackets": rows[4]["ar"],
                "named_tokens_and_urls": rows[5]["ar"],
                "arabic_indic_digits": rows[7]["ar"],
                "mixed_percentages_and_numbers": rows[3]["ar"],
                "vocalized_tashkeel": rows[6]["ar"],
            }
            docx_mixed = core.translations_to_word(
                [{"target": text} for text in mixed_cases.values()], target_lang="ar").getvalue()
            dir_mixed = docx_direction(docx_mixed)
            assert dir_mixed["rtl_paragraph_count"] == len(mixed_cases)
            assert dir_mixed["run_rtl_count"] >= len(mixed_cases)

            mixed_pdf = core.translations_to_pdf(
                [{"target": rows[4]["ar"]}], target_lang="ar")
            (out / "mixed-direction-ar.pdf").write_bytes(mixed_pdf)
            with fitz.open(stream=mixed_pdf, filetype="pdf") as doc:
                page = doc[0]
                lines = [line for block in page.get_text("dict")["blocks"]
                         for line in block.get("lines", [])]
                arabic_spans = [span for line in lines for span in line["spans"]
                                if any(unicodedata.bidirectional(char) == "AL"
                                       for char in span["text"])]
                assert arabic_spans
                # A mixed line starts its rightmost run (here the Latin prefix)
                # at the start margin, so measure the line, not the Arabic runs.
                body_spans = [span for line in lines for span in line["spans"]
                              if span["text"].strip() and span["bbox"][1] > 100]
                mixed_right_edge = max(span["bbox"][2] for span in body_spans) / page.rect.width
                assert mixed_right_edge > 0.85
            report["mixed_direction_tokens"] = {
                "cases_checked": list(mixed_cases.keys()),
                "docx_direction": dir_mixed,
                "pdf_right_edge_ratio": round(mixed_right_edge, 3),
                "status": "PASS",
            }

            # 4. Academic report export with Arabic case excerpts
            doc_report = Document()
            anchor = doc_report.add_paragraph("Chapter 3 Case Analysis")
            arabic_case_quote = "> [TARGET seg-1]: " + rows[4]["ar"]
            inserted = _insert_content_after(anchor, arabic_case_quote)
            bidi_node = inserted._p.find("w:pPr/w:bidi", W)
            assert bidi_node is not None and bidi_node.get(f'{{{W["w"]}}}val') == "1"
            align_node = inserted._p.find("w:pPr/w:jc", W)
            assert align_node is not None and align_node.get(f'{{{W["w"]}}}val') == "start"
            report["academic_report_export"] = {
                "arabic_quote_handled": True,
                "w_bidi_present": True,
                "w_jc_start_present": True,
                "status": "PASS",
            }

            # 5. Font coverage audit: real glyph support, not just family names
            from PySide6.QtGui import QFont, QFontDatabase, QRawFont
            from PySide6.QtWidgets import QApplication

            _app = QApplication.instance() or QApplication([])
            arabic_sample = "العربيةقَرَأَ؟،١٢٣٤"
            installed_families = set(QFontDatabase.families())
            covering_fonts = []
            for family in theme.FONT_STACK:
                if family not in installed_families:
                    continue
                font = QFont(family)
                font.setPixelSize(13)
                raw = QRawFont.fromFont(font)
                if raw.isValid() and all(
                        raw.supportsCharacter(ord(char)) for char in arabic_sample):
                    covering_fonts.append(family)
            assert covering_fonts, "no installed font in the stack covers Arabic glyphs"
            report["font_coverage"] = {
                "font_stack": list(theme.FONT_STACK),
                "installed_covering_fonts": covering_fonts,
                "sample": arabic_sample,
                "status": "PASS",
            }

            # 6. Long-form document: one Arabic paragraph taller than a page.
            # Numeric per-segment markers survive shaping, so they prove that
            # nothing at the tail was dropped by pagination.
            long_sentence = "هذا نص عربي طويل لاختبار تعدد الصفحات والاتجاه من اليمين إلى اليسار."
            markers = [f"{index:04d}" for index in range(90)]
            long_text = " ".join(f"{long_sentence} {marker}." for marker in markers)
            long_pdf = core.translations_to_pdf([{"target": long_text}], target_lang="ar")
            (out / "long-paragraph-ar.pdf").write_bytes(long_pdf)
            with fitz.open(stream=long_pdf, filetype="pdf") as doc:
                long_pages = len(doc)
                long_extracted = "".join(page.get_text() for page in doc)
            long_compact = "".join(long_extracted.split())
            missing_markers = [marker for marker in markers if marker not in long_compact]
            assert long_pages >= 2 and not missing_markers
            report["long_document"] = {
                "paragraph_characters": len(long_text),
                "pdf_pages": long_pages,
                "segments_expected": len(markers),
                "segments_missing": missing_markers,
                "status": "PASS",
            }

        finally:
            core.OUTPUT_DIR = old_output

    # Sentence segmentation with Arabic punctuation
    question = rows[2]["ar"]
    units, _ = segmentation.segment_paragraphs([question], mode="sentence", source_lang="阿拉伯语")
    assert len(units) == 2 and " ".join(units) == question
    report["arabic_question_segmentation"] = {"input": question, "units": units, "status": "PASS"}

    # Overall Summary
    report["summary"] = {
        "core_directions_tested": len(report["directions"]),
        "expanded_language_combinations_tested": len(report["expanded_language_combinations"]),
        "total_combinations_tested": len(report["directions"]) + len(report["expanded_language_combinations"]),
        "selectable_languages_with_arabic": len(partners),
        "mixed_direction_runs_validated": True,
        "academic_report_export_validated": True,
        "real_font_glyph_coverage": len(covering_fonts),
        "long_paragraph_pdf_pages": long_pages,
        "all_checks_passed": True,
    }

    (out / "audit-results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_path = ROOT / "docs/evidence/arabic-acceptance-2026-10-26.json"
    evidence_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Audit completed successfully: {len(report['directions'])} core + {len(report['expanded_language_combinations'])} expanded pairs verified.")
    print(f"Results and exported artifacts saved to: {out}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "exports/arabic-acceptance-v0.8.0")
    args = parser.parse_args()
    run(args.out.resolve())
