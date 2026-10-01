#!/usr/bin/env python3
"""Exercise six Arabic language pairs in isolated local jobs, without an LLM.

Run: python3 scripts/arabic_acceptance_audit.py --out /tmp/folith-ar-audit
Reference responses test transport and Unicode preservation, not model quality.
No credentials are read and existing tasks are untouched.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
from xml.etree import ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core
import fitz
from docx import Document
from streamlit.testing.v1 import AppTest
from folith import segmentation
from folith.epub import build_translation_epub
from folith.textual import has_textual_content

W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
H = {"h": "http://www.w3.org/1999/xhtml"}


def docx_data(texts):
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def docx_direction(data):
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


def run(out, skip_ui=False):
    fixture = json.loads((ROOT / "tests/fixtures/arabic_acceptance.json").read_text())
    rows, languages = fixture["rows"], fixture["languages"]
    out.mkdir(parents=True, exist_ok=True)
    sources = {}
    for language in languages:
        sources[language] = docx_data([row[language] for row in rows])
        (out / f"source-{language}.docx").write_bytes(sources[language])
        (out / f"source-{language}.txt").write_text(
            "\n\n".join(row[language] for row in rows), encoding="utf-8")
    report = {"scope": "local deterministic acceptance; no real LLM calls",
              "reference_status": "draft; needs bilingual review", "directions": {}}
    old_output = core.OUTPUT_DIR
    with tempfile.TemporaryDirectory(prefix="folith-ar-jobs-") as temp:
        core.OUTPUT_DIR = Path(temp)
        try:
            for source, target in [(s, t) for other in ("zh", "en", "ru")
                                   for s, t in ((other, "ar"), ("ar", other))]:
                name = f"{source}-to-{target}"
                originals = [row[source] for row in rows]
                references = [row[target] for row in rows]
                result = {}
                extracted, _, _ = core.extract_document_paragraphs_with_report(
                    f"source-{source}.docx", sources[source])
                assert extracted == originals, name
                assert all(has_textual_content(text) for text in originals)
                result["docx_input_unicode"] = "PASS"
                calls = []

                def fake_llm(provider, key, model, system, user, **kwargs):
                    calls.append((system, user))
                    return json.dumps(references, ensure_ascii=False)

                translations = core.translate_batch(
                    originals, [], [], "", "", languages[target],
                    "OpenAI", "unused", "gpt-4o-mini", call_llm_fn=fake_llm)
                assert translations == references
                assert languages[target] in calls[0][0]
                assert all(text in calls[0][1] for text in originals)
                result["translation_protocol_mock_only"] = "PASS"
                state = core.new_job_state(f"source-{source}.docx")
                state.update(source_lang=languages[source], target_lang=languages[target],
                             source_paras=originals, paras=originals, p1_done=True, p2_done=True,
                             segmentation={"mode": "paragraph", "locked": True},
                             pairs=[{"source": s, "target": t, "initial_target": t,
                                     "reviewed": True, "target_provenance": "human_edit"}
                                    for s, t in zip(originals, references)])
                job_id = f"arabicaudit-{name}"
                core.save_job_state(job_id, state)
                loaded = core.load_job_state(job_id)
                assert [p["target"] for p in loaded["pairs"]] == references
                result["persistence_unicode"] = "PASS"
                memory = {}
                core.tm_put(memory, originals[0], references[0], languages[target])
                assert core.tm_lookup(memory, originals[0], target_lang=languages[target])[0]
                assert core.tm_lookup(memory, originals[0], target_lang=languages[source])[0] is None
                result["tm_language_isolation"] = "PASS"
                if not skip_ui:
                    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40)
                    at.run()
                    for key, value in {"active_job_id": job_id, "app_view": "workspace",
                                       "workspace_mode": True, "workspace_section": "translation"}.items():
                        at.session_state[key] = value
                    at.run()
                    assert not at.exception, str(at.exception)
                    assert all(text in [area.value for area in at.text_area] for text in references)
                    # Exercise real editor -> save -> reload, including combining marks.
                    area = next(area for area in at.text_area if area.value == references[0])
                    edited = references[0] + " ✓"
                    area.set_value(edited).run()
                    save = next(button for button in at.button
                                if button.key == f"cat_save_btn_{job_id}_0")
                    save.click().run()
                    assert not at.exception, str(at.exception)
                    assert core.load_job_state(job_id)["pairs"][0]["target"] == edited
                    at.session_state[f"translation_search_{job_id}"] = references[1]
                    at.run()
                    assert not at.exception, str(at.exception)
                    assert len([b for b in at.button if str(b.key).startswith(f"cat_sel_{job_id}_")]) == 1
                    result["workspace_render_edit_save_search"] = "PASS (AppTest; not browser layout)"
                for suffix, data in (("translation", core.translations_to_word(
                        state["pairs"], target_lang=languages[target]).getvalue()),
                        ("bilingual", core.pairs_to_word(state["pairs"],
                            source_lang=languages[source], target_lang=languages[target]).getvalue())):
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
                    result[f"{suffix}_docx_direction"] = direction_report
                pdf = core.translations_to_pdf(state["pairs"], target_lang=languages[target])
                (out / f"{name}-translation.pdf").write_bytes(pdf)
                with fitz.open(stream=pdf, filetype="pdf") as document:
                    result["pdf_pages"] = len(document)
                    for i, page in enumerate(document):
                        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(
                            str(out / f"{name}-pdf-page-{i+1}.png"))
                    result["pdf_text"] = "\n".join(page.get_text() for page in document)
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
                report["directions"][name] = result
                print(name, "local checks passed", flush=True)
        finally:
            core.OUTPUT_DIR = old_output
    question = rows[2]["ar"]
    units, _ = segmentation.segment_paragraphs([question], mode="sentence", source_lang="阿拉伯语")
    assert len(units) == 2 and " ".join(units) == question
    report["arabic_question_segmentation"] = {"input": question, "units": units}
    report["unchanged_arabic_residue"] = core.find_residuals(rows[0]["ar"], rows[0]["ar"], "English")
    report["unchanged_russian_residue"] = core.find_residuals(rows[0]["ru"], rows[0]["ru"], "阿拉伯语")
    (out / "audit-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args()
    run(args.out.resolve(), args.skip_ui)
