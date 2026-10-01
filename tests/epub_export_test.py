"""Source-to-delivery EPUB regressions with actual PDF/DOCX image fixtures."""
import io
import json
from copy import deepcopy
from pathlib import PurePosixPath
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import fitz
import pytest
from docx import Document
from docx.shared import Inches

import core
from folith.epub import SOURCE_REFS, EpubExportError, bind_source_references, build_translation_epub

XHTML = {"h": "http://www.w3.org/1999/xhtml"}
OPF = {"o": "http://www.idpf.org/2007/opf"}


def illustration():
    with fitz.open() as doc:
        page = doc.new_page(width=240, height=120)
        page.draw_rect(fitz.Rect(10, 10, 230, 110), color=(0.1, 0.35, 0.65), fill=(0.85, 0.93, 1))
        page.insert_text((30, 65), "Original illustration", fontsize=17)
        return page.get_pixmap(alpha=False).tobytes("png")


def docx_source():
    doc = Document()
    doc.add_heading("Chapter 1: An Illustrated Book", level=1)
    doc.add_paragraph("The first paragraph describes the picture. It has two sentences.")
    doc.add_picture(io.BytesIO(illustration()), width=Inches(3))
    doc.add_paragraph("Figure 1. A blue illustration.", style="Caption")
    doc.add_paragraph("The second paragraph follows the picture.")
    doc.add_heading("Chapter 2: Reading", level=1)
    doc.add_paragraph("The font size can change while the picture stays in order.")
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def pdf_source():
    with fitz.open() as doc:
        page = doc.new_page(width=500, height=650)
        page.insert_text((50, 70), "Chapter 1: An Illustrated Book", fontsize=19)
        page.insert_text((50, 115), "The first paragraph describes the picture.", fontsize=11)
        # Deliberately add later text before the picture to exercise geometric
        # ordering rather than assuming PDF drawing order is reading order.
        page.insert_text((50, 380), "The second paragraph follows the picture.", fontsize=11)
        page.insert_image(fitz.Rect(70, 150, 430, 330), stream=illustration())
        page.insert_text((80, 350), "Figure 1. A blue illustration.", fontsize=9)
        return doc.tobytes()


def translated_state(filename, source):
    raw, _, report = core.extract_document_paragraphs_with_report(filename, source)
    state = core.new_job_state(filename)
    state["source_paras"] = raw
    units, _ = core._apply_source_segmentation(state, raw, mode="sentence")
    state.update(paras=units, extraction_report=report, target_lang="简体中文", p1_done=True, p2_done=True,
                 pairs=[{"source": unit, "target": f"译文{i + 1}：{unit}", "reviewed": True,
                         "target_provenance": "human_edit"} for i, unit in enumerate(units)])
    return state


def content(data):
    with ZipFile(io.BytesIO(data)) as archive:
        pages = [archive.read(name).decode() for name in sorted(archive.namelist())
                 if "/chapter-" in name]
    return "".join(pages)


def validate_package(data):
    with ZipFile(io.BytesIO(data)) as archive:
        info = archive.infolist()[0]
        assert info.filename == "mimetype"
        assert info.compress_type == ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        assert rootfile.get("full-path") == "EPUB/content.opf"
        package = ET.fromstring(archive.read(rootfile.get("full-path")))
        manifest = {item.get("id"): item for item in package.findall("o:manifest/o:item", OPF)}
        assert sum(item.get("properties") == "nav" for item in manifest.values()) == 1
        for item in manifest.values():
            assert "EPUB/" + item.get("href") in archive.namelist()
        for item in package.findall("o:spine/o:itemref", OPF):
            assert item.get("idref") in manifest
        for name in archive.namelist():
            if name.endswith((".xhtml", ".ncx", ".opf", ".xml")):
                root = ET.fromstring(archive.read(name))
                for tag, attr in (("h:img", "src"), ("h:a", "href")):
                    for node in root.findall(".//" + tag, XHTML):
                        path = str(PurePosixPath(name).parent / node.get(attr).split("#")[0])
                        assert path in archive.namelist()
                        if "#" in node.get(attr):
                            fragment = node.get(attr).split("#", 1)[1]
                            destination = ET.fromstring(archive.read(path))
                            assert any(element.get("id") == fragment for element in destination.iter())


@pytest.mark.parametrize("filename,maker", [("illustrated.docx", docx_source), ("illustrated.pdf", pdf_source)])
def test_actual_source_to_epub_preserves_image_position(filename, maker):
    source = maker()
    state = translated_state(filename, source)
    before = deepcopy(state)
    data, report = build_translation_epub(state, source, job_id="illustrated")
    validate_package(data)
    assert state == before  # building a preview cannot invalidate a snapshot
    assert report["image_count"] == 1
    book = content(data)
    first = next(pair["target"] for pair in state["pairs"] if "first paragraph" in pair["source"])
    second = next(pair["target"] for pair in state["pairs"] if "second paragraph" in pair["source"])
    assert book.index(first) < book.index("<img ") < book.index(second)
    assert book.count("<img ") == 1
    assert book.index("<img ") < book.index("Figure 1.") < book.index(second)
    if filename.endswith(".pdf"):
        assert report["original_caption_count"] == 1
    else:
        assert report["chapter_count"] == 2
        # Both translated sentences in the same source paragraph form one p.
        with ZipFile(io.BytesIO(data)) as archive:
            root = ET.fromstring(archive.read("EPUB/chapter-0001.xhtml"))
            assert any("first paragraph" in "".join(p.itertext()) and "two sentences" in "".join(p.itertext())
                       for p in root.findall(".//h:p", XHTML))


def test_structure_edits_keep_sources_and_never_duplicate_images(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = docx_source()
    state = translated_state("edited.docx", source)
    core.save_source("edited", source)
    core.save_job_state("edited", state)
    state = core.mutate_translation_segments("edited", 1, "split", source_a="The first paragraph",
                                            source_b="describes the picture.", target_a="图前第一部分", target_b="图前第二部分")
    assert state["pairs"][1][SOURCE_REFS] == state["pairs"][2][SOURCE_REFS]
    state = core.mutate_translation_segments("edited", 1, "merge")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 1
    assert content(data).index("图前第一部分") < content(data).index("<img ")
    refs = state["pairs"][1][SOURCE_REFS]
    state = core.mutate_translation_segments("edited", 1, "exclude", exclude_reason="测试排除")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 1
    assert "图前第一部分" not in content(data)
    state = core.mutate_translation_segments("edited", 1, "include", excluded_id=state["excluded_segments"][0]["excluded_id"])
    assert state["pairs"][1][SOURCE_REFS] == refs
    data, _ = build_translation_epub(state, source)
    assert "图前第一部分" in content(data)
    state = core.undo_translation_segment_structure("edited")
    data, _ = build_translation_epub(state, source)
    assert "图前第一部分" not in content(data)
    assert content(data).count("<img ") == 1
    state = core.undo_translation_segment_structure("edited")
    data, _ = build_translation_epub(state, source)
    assert "图前第一部分" in content(data)


def test_cleanup_merge_and_manual_merge_across_picture_keep_target_once(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = docx_source()
    state = translated_state("merged.docx", source)
    # Two source paragraphs become one cleanup paragraph without losing either
    # origin. The picture remains after that whole translated paragraph.
    raw = state["source_paras"]
    cleaned = [raw[0], raw[1] + " " + raw[2], *raw[3:]]
    units, _ = core._apply_source_segmentation(state, cleaned, mode="sentence")
    state["source_cleanup"] = {"output_input_indices": [[0], [1, 2], *[[i] for i in range(3, len(raw))]]}
    state["paras"] = units
    state["pairs"] = [{"source": unit, "target": f"合并译文{i}"} for i, unit in enumerate(units)]
    assert bind_source_references(state)
    assert state["pairs"][1][SOURCE_REFS] == [1, 2]
    core.save_source("merged", source)
    core.save_job_state("merged", state)
    state = core.mutate_translation_segments("merged", 3, "merge")
    assert len(state["pairs"][3][SOURCE_REFS]) > 1
    data, _ = build_translation_epub(state, source)
    assert content(data).count("合并译文3") == 1
    assert content(data).count("<img ") == 1


def test_unmapped_legacy_edit_and_wrong_source_fail_explicitly():
    source = docx_source()
    state = translated_state("legacy.docx", source)
    state["segment_structure_history"] = [{"operation": "merge"}]
    with pytest.raises(EpubExportError, match="来源关联"):
        build_translation_epub(state, source)
    state.pop("segment_structure_history")
    state["source_paras"][0] = "A different source"
    with pytest.raises(EpubExportError, match="来源不一致"):
        build_translation_epub(state, source)


def test_escaping_missing_targets_and_unchanged_media():
    source = docx_source()
    state = translated_state("escape.docx", source)
    state["pairs"][1]["target"] = '<literal & text> 😀'
    state["pairs"][2]["target"] = ""
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert "&lt;literal &amp; text&gt; 😀" in content(data)
    assert report["missing_translation_count"] == 1
    with ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("EPUB/images/image-0001.png") == illustration()


def test_delivery_option_bundle_and_frozen_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = docx_source()
    state = translated_state("delivery.docx", source)
    config = {key: False for key in core.default_delivery_config()}
    config["deliver_epub"] = True
    state["delivery_config"] = config
    core.save_source("epubdelivery", source)
    core.save_job_state("epubdelivery", state)
    bundle = core.build_delivery_assets("epubdelivery", state)
    assert set(bundle) == {"translation.epub", "epub_export_report.json", "delivery_manifest.json"}
    assert json.loads(bundle["epub_export_report.json"])["image_count"] == 1
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert "translation.epub" in str(manifest)
    snapshot = core.create_delivery_snapshot("epubdelivery", state)
    assets = core.delivery_snapshot_assets("epubdelivery", snapshot["snapshot_version"])
    validate_package(assets["translation.epub"])
    assert core.default_delivery_config()["deliver_epub"] is False


def test_scanned_page_does_not_masquerade_as_an_inline_picture():
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_image(page.rect, stream=illustration())
        source = doc.tobytes()
    with pytest.raises(EpubExportError, match="扫描页"):
        build_translation_epub({"filename": "scan.pdf", "pairs": [{"source": "OCR", "target": "识别译文"}]}, source)


def test_image_only_plate_in_text_pdf_is_preserved():
    with fitz.open(stream=pdf_source(), filetype="pdf") as doc:
        page = doc.new_page(width=500, height=650)
        page.insert_image(fitz.Rect(50, 100, 450, 300), stream=illustration())
        source = doc.tobytes()
    state = translated_state("plate.pdf", source)
    data, report = build_translation_epub(state, source)
    assert report["image_count"] == 2
    assert content(data).count("<img ") == 2
    assert any("第 2 页" in warning and "原样保留" in warning for warning in report["warnings"])


def test_wizard_epub_selection_updates_summary():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.run()
    at.session_state["task_files"] = [{"name": "source.docx", "bytes": b"source"}]
    at.session_state["task_step"] = 3
    at.run()
    next(checkbox for checkbox in at.checkbox if checkbox.key == "deliver_epub").check()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["output_config"]["deliver_epub"] is True
    assert any("将生成 4 个文件" in item.value and "EPUB ×1" in item.value for item in at.markdown)


def test_existing_task_can_enable_and_download_epub(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source = docx_source()
    state = translated_state("delivery-ui.docx", source)
    state["report_enabled"] = False
    state["delivery_config"] = {key: False for key in core.default_delivery_config()}
    core.save_source("epubui", source)
    core.save_job_state("epubui", state)
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.run()
    at.session_state["active_job_id"] = "epubui"
    at.session_state["app_view"] = "workspace"
    at.session_state["workspace_mode"] = True
    at.session_state["workspace_section"] = "delivery"
    at.run()
    assert not at.exception, at.exception
    assert not any(checkbox.key == "delivery_epub_epubui" for checkbox in at.checkbox)
    assert any(popover.proto.popover.label == "调整交付内容" for popover in at.get("popover"))
    assert all(any(checkbox.key == f"delivery_content_epubui_{key}" for checkbox in at.checkbox)
               for key in ("deliver_plain_docx", "deliver_bilingual_docx", "deliver_pdf", "deliver_epub"))
    next(checkbox for checkbox in at.checkbox if checkbox.key == "delivery_content_epubui_deliver_epub").check()
    next(button for button in at.button if button.label == "应用").click()
    at.run()
    assert not at.exception, at.exception
    assert not at.error
    assert core.load_job_state("epubui")["delivery_config"]["deliver_epub"] is True
    assert any("译文电子书 EPUB" in item.value and "translation.epub" in item.value for item in at.markdown)
    assert any("1 张原图" in item.value for item in at.caption)
    assert not any(expander.label == "图文范围说明" for expander in at.expander)
    assert any(button.key == "dl_prim_epubui_translation_epub" for button in at.download_button)
    assert any("交付内容：译文电子书 · EPUB" in item.value for item in at.caption)
    # Format changes take effect on apply, and can be reversed in the same
    # settings entry without leaving a stale standalone EPUB checkbox behind.
    next(checkbox for checkbox in at.checkbox if checkbox.key == "delivery_content_epubui_deliver_epub").uncheck()
    next(button for button in at.button if button.label == "应用").click()
    at.run()
    assert not at.exception, at.exception
    assert core.load_job_state("epubui")["delivery_config"]["deliver_epub"] is False
    assert not any(button.key == "dl_prim_epubui_translation_epub" for button in at.download_button)


@pytest.mark.parametrize("filename", ["cover.pdf", "cover.docx"])
def test_original_cover_is_first_without_generated_reading_notes(filename):
    if filename.endswith(".pdf"):
        with fitz.open(stream=pdf_source(), filetype="pdf") as doc:
            page = doc.new_page(pno=0, width=500, height=650)
            page.insert_image(page.rect, stream=illustration())
            page.draw_rect(fitz.Rect(20, 20, 480, 630), color=(1, 0, 0))
            expected = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
            source = doc.tobytes()
    else:
        doc = Document(io.BytesIO(docx_source()))
        picture = doc.add_picture(io.BytesIO(illustration()), width=Inches(3))
        paragraph = picture._inline.getparent().getparent().getparent()
        doc.element.body.insert(0, paragraph)
        stream = io.BytesIO()
        doc.save(stream)
        source, expected = stream.getvalue(), illustration()
    data, report = build_translation_epub(translated_state(filename, source), source)
    validate_package(data)
    assert report["has_cover"] is True
    assert report["image_count"] == 2
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        manifest = {item.get("id"): item for item in package.findall("o:manifest/o:item", OPF)}
        first = package.find("o:spine/o:itemref", OPF).get("idref")
        assert manifest[first].get("href") == "cover.xhtml"
        cover = next(item for item in manifest.values() if item.get("properties") == "cover-image")
        assert archive.read("EPUB/" + cover.get("href")) == expected
        assert cover.get("href") not in content(data)
        assert "EPUB/title.xhtml" not in archive.namelist()
        assert all("阅读说明" not in archive.read(name).decode() for name in archive.namelist() if name.endswith(".xhtml"))
    assert content(data).count("<img ") == 1


def test_contents_entries_link_to_source_matched_chapter_anchors():
    doc = Document()
    doc.add_heading("CONTENTS", level=1)
    doc.add_paragraph("Vertigo ... 9")
    doc.add_paragraph("Back From the Moon")
    doc.add_paragraph("Unmatched title")
    doc.add_heading("VERTIGO", level=1)
    doc.add_paragraph("A night of extraordinary clarity.")
    doc.add_heading("BACK FROM", level=1)
    doc.add_heading("THE MOON", level=1)
    doc.add_paragraph("The final days of our training.")
    stream = io.BytesIO()
    doc.save(stream)
    source = stream.getvalue()
    state = translated_state("contents.docx", source)
    units, _ = core._apply_source_segmentation(state, state["source_paras"], mode="paragraph")
    state["paras"] = units
    state["pairs"] = [{"source": unit, "target": f"译文：{unit}"} for unit in units]
    # Directory and chapter translations may differ; correspondence uses source.
    for pair in state["pairs"]:
        if pair["source"] == "Vertigo ... 9":
            pair["target"] = "眩晕"
        elif pair["source"] == "VERTIGO":
            pair["target"] = "第一章：眩晕"
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["linked_toc_count"] == 2
    with ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read("EPUB/chapter-0001.xhtml"))
        links = root.findall(".//h:a", XHTML)
        assert len(links) == 2
        link = next(link for link in links if link.text == "眩晕")
        path, fragment = link.get("href").split("#")
        target = ET.fromstring(archive.read("EPUB/" + path))
        assert next(node for node in target.iter() if node.get("id") == fragment).text == "第一章：眩晕"
        assert "Unmatched title" in "".join(root.itertext())
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        assert not any(item.get("properties") == "cover-image" for item in package.findall("o:manifest/o:item", OPF))
