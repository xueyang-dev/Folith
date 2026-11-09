"""Source-to-delivery EPUB regressions with actual PDF/DOCX image fixtures."""
import io
import json
import re
from copy import deepcopy
from pathlib import PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

import fitz
import pytest
from docx import Document
from docx.shared import Inches

import core
from folith.engine.document_export import build_translation_docx
from folith.epub import (
    SOURCE_REFS, EpubExportError, _epub_source, bind_source_references, build_translation_epub,
)

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


_EPUB_XHTML = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<html xmlns="http://www.w3.org/1999/xhtml" '
               'xmlns:xlink="http://www.w3.org/1999/xlink">'
               '<head><title>Chapter</title></head><body>{}</body></html>')

_EPUB_XHTML_STYLED = ('<?xml version="1.0" encoding="UTF-8"?>'
                      '<html xmlns="http://www.w3.org/1999/xhtml" '
                      'xmlns:xlink="http://www.w3.org/1999/xlink">'
                      '<head><title>Chapter</title>'
                      '<link rel="stylesheet" type="text/css" href="{css}"/>'
                      '</head><body>{body}</body></html>')

_IMAGE_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "gif": "image/gif", "webp": "image/webp",
                      "svg": "image/svg+xml", "bmp": "image/bmp"}


def jpeg_image(rgb=(0.75, 0.2, 0.2)):
    with fitz.open() as document:
        page = document.new_page(width=48, height=36)
        page.draw_rect(page.rect, color=rgb, fill=rgb)
        return page.get_pixmap(alpha=False).tobytes("jpg")


def colored_png(rgb):
    with fitz.open() as document:
        page = document.new_page(width=32, height=24)
        page.draw_rect(page.rect, color=rgb, fill=rgb)
        return page.get_pixmap(alpha=False).tobytes("png")


def webp_image(rgb=(0.2, 0.7, 0.3)):
    from PIL import Image

    stream = io.BytesIO()
    Image.new("RGB", (12, 9), tuple(round(value * 255) for value in rgb)).save(stream, "WEBP")
    return stream.getvalue()


def synthetic_epub(chapters, images=None, *, cover=None, cover_meta=False,
                   cover_property=True, encryption=(), stylesheets=None):
    """Build a synthetic EPUB in memory; no sample book is committed.

    ``chapters`` items are dicts with ``href``/``body`` plus optional ``zip_name``,
    ``linear``, ``media_type`` and ``stylesheet`` (package-relative CSS href,
    linked from that chapter's head).  ``images`` maps package-relative ZIP names
    to bytes; their manifest href is percent-encoded.  ``stylesheets`` maps
    package-relative names to CSS text.  ``cover`` selects one of those image
    names for the cover declaration.
    """
    from urllib.parse import quote

    images = dict(images or {})
    files = {}
    manifest = []
    spine = []
    for index, chapter in enumerate(chapters):
        href = chapter["href"]
        zip_name = chapter.get("zip_name") or unquote(href)
        media_type = chapter.get("media_type", "application/xhtml+xml")
        if media_type != "application/xhtml+xml":
            content = chapter["body"]
        elif chapter.get("stylesheet"):
            content = _EPUB_XHTML_STYLED.format(css=chapter["stylesheet"],
                                                body=chapter["body"])
        else:
            content = _EPUB_XHTML.format(chapter["body"])
        files[zip_name] = content.encode("utf-8")
        manifest.append(f'<item id="text-{index}" href="{quote(href, safe="/")}" '
                        f'media-type="{media_type}"/>')
        linear = ' linear="no"' if chapter.get("linear") == "no" else ""
        spine.append(f'<itemref idref="text-{index}"{linear}/>')
    for index, (name, text) in enumerate((stylesheets or {}).items()):
        manifest.append(f'<item id="stylesheet-{index}" href="{quote(name)}" '
                        f'media-type="text/css"/>')
        files[name] = text.encode("utf-8")
    image_ids = {}
    for index, (name, data) in enumerate(images.items()):
        item_id = f"image-{index}"
        image_ids[name] = item_id
        extension = name.rsplit(".", 1)[-1].lower()
        media_type = _IMAGE_MEDIA_TYPES.get(extension, "application/octet-stream")
        properties = ' properties="cover-image"' if cover == name and cover_property else ""
        manifest.append(f'<item id="{item_id}" href="{quote(name)}" '
                        f'media-type="{media_type}"{properties}/>')
        files[name] = data
    cover_meta_xml = ""
    if cover and cover_meta:
        cover_meta_xml = f'<meta name="cover" content="{image_ids[cover]}"/>'
    encryption_xml = ""
    if encryption:
        refs = "".join(
            '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData>'
            f'<CipherReference URI="OEBPS/{quote(name)}"/></CipherData></EncryptedData>'
            for name in encryption)
        encryption_xml = ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                          + refs + '</encryption>')
    package = ('<?xml version="1.0"?>'
               '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
               f'<metadata>{cover_meta_xml}</metadata>'
               f'<manifest>{"".join(manifest)}</manifest>'
               f'<spine>{"".join(spine)}</spine></package>')
    stream = io.BytesIO()
    with ZipFile(stream, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/book.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>')
        if encryption_xml:
            book.writestr("META-INF/encryption.xml", encryption_xml)
        book.writestr("OEBPS/book.opf", package)
        for name, content in files.items():
            book.writestr("OEBPS/" + name, content)
    return stream.getvalue()


def image_sources(book):
    return [match.group(1) for match in re.finditer(r'<img src="([^"]+)"', book)]


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


def visual_pdf_state():
    from folith.document_ir import PageEnvelope, SemanticBlock
    from folith.document_understanding import VisualDocumentAssembler
    from folith.document_ir import PageUnderstanding
    from folith.spatial_index import SpatialTextIndex
    import time

    with fitz.open() as pdf:
        page = pdf.new_page(width=500, height=650)
        page.insert_image(page.rect, stream=illustration())
        source = pdf.tobytes()
    assembler = VisualDocumentAssembler("scan.pdf")
    blocks = [SemanticBlock("title", "A scanned chapter"),
              SemanticBlock("paragraph", "Text before the picture. Another sentence."),
              SemanticBlock("figure", ""), SemanticBlock("caption", "A caption."),
              SemanticBlock("paragraph", "Text after the picture."),
              SemanticBlock("figure", "")]
    anchors = [[{"page_id": "p1", "bbox": None, "grounding_method": "page_only",
                 "grounding_quality": "page_only"}] for _ in blocks]
    anchors[2] = [{"page_id": "p1", "bbox": [0.2, 0.3, 0.8, 0.5],
                   "grounding_method": "region_alignment", "grounding_quality": "strong"}]
    assembler.add_page(PageEnvelope("scan.pdf", "p1", 0, 500, 650),
                       (PageUnderstanding(blocks, "vlm"), anchors,
                        SpatialTextIndex("p1"), time.perf_counter()))
    raw = assembler.paragraphs
    state = core.new_job_state("scan.pdf")
    state["source_paras"] = raw
    state["extraction_report"] = {"semantic_source": "vlm", "document_ir": assembler.document}
    units, _ = core._apply_source_segmentation(state, raw, mode="sentence")
    state.update(paras=units, target_lang="简体中文", p1_done=True, p2_done=True,
                 pairs=[{"source": text, "target": f"识别译文{i}"} for i, text in enumerate(units)])
    return source, state


@pytest.mark.parametrize("semantic_source", ["vlm", "mixed"])
def test_semantic_pdf_epub_uses_recognized_text_and_only_grounded_figures(semantic_source):
    source, state = visual_pdf_state()
    state["extraction_report"]["semantic_source"] = semantic_source
    before = deepcopy(state)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert state == before
    book = content(data)
    body = ET.fromstring(book).find("h:body", XHTML)
    body_text = "".join(body.itertext())
    for pair in state["pairs"]:
        assert body_text.count(pair["target"]) == 1
    assert report["image_count"] == 1
    assert book.index("识别译文1") < book.index("<img ") < book.index("识别译文3")
    assert "<h1" in book
    assert any("缺少完整区域坐标" in warning for warning in report["warnings"])
    with ZipFile(io.BytesIO(data)) as archive:
        pix = fitz.Pixmap(archive.read("EPUB/images/image-0001.png"))
        assert (pix.width, pix.height) == (600, 260)  # region, not the whole scan


def test_semantic_epub_keeps_cat_merge_and_exclusion_references(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    source, state = visual_pdf_state()
    core.save_source("semantic", source)
    # First bind the source identity before structure edits, as the editor does.
    assert bind_source_references(state)
    core.save_job_state("semantic", state)
    state = core.mutate_translation_segments("semantic", 1, "merge")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("识别译文1") == 1
    assert content(data).count("识别译文2") == 1
    state = core.mutate_translation_segments("semantic", 1, "exclude", exclude_reason="test")
    data, _ = build_translation_epub(state, source)
    assert "识别译文1" not in content(data)
    assert content(data).count("<img ") == 1


def test_semantic_epub_rejects_mismatched_canonical_source():
    source, state = visual_pdf_state()
    state["source_paras"][0] = "Different recognized source"
    with pytest.raises(EpubExportError, match="来源不一致"):
        build_translation_epub(state, source)


def test_mixed_pdf_uses_page_order_and_does_not_embed_full_scan():
    from folith.document_ir import PageUnderstanding, SemanticBlock
    from folith.document_understanding import understand_mixed_pdf

    class Provider:
        def understand_page(self, page):
            return PageUnderstanding([SemanticBlock("paragraph", "Recognized middle page.")], "vlm")

    with fitz.open(stream=pdf_source(), filetype="pdf") as pdf:
        page = pdf.new_page(width=500, height=650)
        page.insert_image(page.rect, stream=illustration())
        page = pdf.new_page(width=500, height=650)
        page.insert_text((50, 70), "The final native paragraph.")
        source = pdf.tobytes()
    raw, document = understand_mixed_pdf("mixed.pdf", source, [1], Provider())
    state = core.new_job_state("mixed.pdf")
    state.update(source_paras=raw, target_lang="简体中文",
                 extraction_report={"semantic_source": "mixed", "document_ir": document})
    units, _ = core._apply_source_segmentation(state, raw, mode="paragraph")
    state.update(paras=units, pairs=[{"source": text, "target": f"译文{i}"}
                                     for i, text in enumerate(units)])
    data, report = build_translation_epub(state, source)
    validate_package(data)
    book = content(data)
    # The native illustration survives; the full-page scan does not.
    assert report["image_count"] == 1
    bodies = []
    with ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if "/chapter-" in name:
                bodies.extend("".join(node.itertext()) for node in
                              ET.fromstring(archive.read(name)).find("h:body", XHTML)
                              if node.tag in {f'{{{XHTML["h"]}}}p', f'{{{XHTML["h"]}}}h1'})
    assert bodies == [pair["target"] for pair in state["pairs"]]
    first = next(pair["target"] for pair in state["pairs"] if "first paragraph" in pair["source"])
    second = next(pair["target"] for pair in state["pairs"] if "second paragraph" in pair["source"])
    assert book.index(first) < book.index("<img ") < book.index(second)


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


# ---------------------------------------------------------------------------
# EPUB source: images keep their source paragraph boundary
# ---------------------------------------------------------------------------

def test_epub_images_restored_at_source_paragraph_boundaries():
    photo = illustration()
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>First paragraph.</p><div><img src="../images/pic.png"/></div>'
                  '<p>Figure 1.1: A caption.</p><p>Last paragraph.</p>'}],
        {"images/pic.png": photo})
    state = translated_state("book.epub", source)
    before = deepcopy(state)
    data, report = build_translation_epub(state, source, job_id="epub-images")
    validate_package(data)
    assert state == before
    assert report["image_count"] == report["image_reference_count"] == 1
    assert report["source_image_reference_count"] == 1
    assert report["skipped_image_count"] == 0
    book = content(data)
    first = next(pair["target"] for pair in state["pairs"] if "First paragraph" in pair["source"])
    caption = next(pair["target"] for pair in state["pairs"] if "Figure 1.1" in pair["source"])
    last = next(pair["target"] for pair in state["pairs"] if "Last paragraph" in pair["source"])
    assert book.index(first) < book.index("<img ") < book.index(caption) < book.index(last)
    assert book.count("<img ") == 1
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert archive.read(media[0]) == photo


def test_epub_inline_image_follows_complete_paragraph_and_keeps_tail_text():
    photo = illustration()
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>Before <img src="../images/pic.png"/> after tail.</p>'
                  '<p>Next paragraph.</p>'}],
        {"images/pic.png": photo})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert paragraphs == ["Before after tail.", "Next paragraph."]
    state = translated_state("book.epub", source)
    data, _ = build_translation_epub(state, source)
    book = content(data)
    assert book.count("<img ") == 1
    before = next(pair["target"] for pair in state["pairs"] if "Before" in pair["source"])
    nxt = next(pair["target"] for pair in state["pairs"] if "Next paragraph" in pair["source"])
    assert "after tail." in before
    assert book.index(before) < book.index("<img ") < book.index(nxt)


def test_epub_chapter_boundaries_and_image_only_chapter_keep_reading_order():
    side_a = colored_png((1, 0, 0))
    side_b = colored_png((0, 1, 0))
    side_c = colored_png((0, 0, 1))
    source = synthetic_epub(
        [{"href": "text/c1.xhtml",
          "body": '<p><img src="../images/a.png"/></p><p>Chapter one text.</p>'},
         {"href": "text/c2.xhtml", "body": '<div><img src="../images/b.png"/></div>'},
         {"href": "text/c3.xhtml",
          "body": '<p>Chapter three text.</p><p><img src="../images/c.png"/></p>'}],
        {"images/a.png": side_a, "images/b.png": side_b, "images/c.png": side_c})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert paragraphs == ["Chapter one text.", "Chapter three text."]
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 3
    book = content(data)
    one = next(pair["target"] for pair in state["pairs"] if "Chapter one" in pair["source"])
    three = next(pair["target"] for pair in state["pairs"] if "Chapter three" in pair["source"])
    first_img = 'src="images/image-0001.png"'
    second_img = 'src="images/image-0002.png"'
    third_img = 'src="images/image-0003.png"'
    assert (book.index(first_img) < book.index(one) < book.index(second_img)
            < book.index(three) < book.index(third_img))


def test_epub_repeated_image_keeps_each_position_and_stores_one_file():
    logo = colored_png((0.4, 0.6, 0.2))
    source = synthetic_epub(
        [{"href": "text/c1.xhtml",
          "body": '<p>One.</p><div><img src="../images/logo.png"/></div><p>Two.</p>'},
         {"href": "text/c2.xhtml",
          "body": '<p>Three.</p><div><img src="../images/logo.png"/></div><p>Four.</p>'}],
        {"images/logo.png": logo})
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == 1
    assert report["image_reference_count"] == 2
    book = content(data)
    assert book.count("<img ") == 2
    assert image_sources(book) == ["images/image-0001.png", "images/image-0001.png"]
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert archive.read(media[0]) == logo
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        items = [item for item in package.findall("o:manifest/o:item", OPF)
                 if item.get("properties") != "nav"]
        image_items = [item for item in items
                       if str(item.get("media-type") or "").startswith("image/")]
        assert len(image_items) == 1


def test_epub_percent_encoded_and_nested_image_paths():
    photo = colored_png((0.2, 0.3, 0.9))
    source = synthetic_epub(
        [{"href": "text/sub/ch.xhtml",
          "body": '<p>Start.</p><div><img src="../../assets/pic%20one.png#frag"/></div>'
                  '<p>End.</p>'}],
        {"assets/pic one.png": photo})
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    assert content(data).count("<img ") == 1
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert archive.read(media[0]) == photo


def test_epub_svg_cover_wrapper_becomes_cover_and_is_not_repeated():
    cover_bytes = jpeg_image((0.8, 0.1, 0.1))
    body_picture = colored_png((0.1, 0.7, 0.4))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
           'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1200 1920">'
           '<image xlink:href="../images/cover.jpg" width="1200" height="1920"/></svg>')
    source = synthetic_epub(
        [{"href": "text/cover.xhtml", "body": svg},
         {"href": "text/ch1.xhtml",
          "body": '<p>Text.</p><div><img src="../images/pic.png"/></div>'}],
        {"images/cover.jpg": cover_bytes, "images/pic.png": body_picture},
        cover="images/cover.jpg")
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["has_cover"] is True
    assert report["image_count"] == 2
    assert report["source_image_reference_count"] == 2
    assert report["cover_reference_count"] == 1
    assert report["image_reference_count"] == 1
    book = content(data)
    assert book.count("<img ") == 1
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        manifest = {item.get("id"): item
                    for item in package.findall("o:manifest/o:item", OPF)}
        cover_item = next(item for item in manifest.values()
                          if item.get("properties") == "cover-image")
        assert archive.read("EPUB/" + cover_item.get("href")) == cover_bytes
        first = package.find("o:spine/o:itemref", OPF).get("idref")
        assert manifest[first].get("href") == "cover.xhtml"
        assert cover_item.get("href") not in content(data)


def test_epub_manifest_cover_meta_is_used_without_body_reference():
    cover_bytes = jpeg_image((0.1, 0.2, 0.8))
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": '<p>Only text.</p>'}],
        {"images/cover.jpg": cover_bytes}, cover="images/cover.jpg",
        cover_meta=True, cover_property=False)
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    validate_package(data)
    assert report["has_cover"] is True
    assert report["image_count"] == 1
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        cover_item = next(item for item in package.findall("o:manifest/o:item", OPF)
                          if item.get("properties") == "cover-image")
        assert archive.read("EPUB/" + cover_item.get("href")) == cover_bytes


def test_epub_svg_cover_resource_is_unwrapped_to_its_bitmap():
    cover_bytes = jpeg_image((0.3, 0.6, 0.3))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" '
           'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10">'
           '<image xlink:href="cover.jpg" width="10" height="10"/></svg>').encode()
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": '<p>Only text.</p>'}],
        {"images/wrap.svg": svg, "images/cover.jpg": cover_bytes},
        cover="images/wrap.svg")
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    validate_package(data)
    assert report["has_cover"] is True
    assert report["image_count"] == 1
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        cover_item = next(item for item in package.findall("o:manifest/o:item", OPF)
                          if item.get("properties") == "cover-image")
        assert cover_item.get("media-type") == "image/jpeg"
        assert archive.read("EPUB/" + cover_item.get("href")) == cover_bytes


def test_epub_bad_images_are_skipped_with_reasons_not_blocking_delivery():
    good = colored_png((0.2, 0.5, 0.8))
    locked = colored_png((0.6, 0.1, 0.1))
    body = ('<p>Before.</p>'
            '<div><img src="../images/missing.png"/></div>'
            '<div><img src="https://example.com/remote.png"/></div>'
            '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
            '<div><img src="../images/locked.png"/></div>'
            '<div><img src="../images/broken.png"/></div>'
            '<div><img src="../images/good.png"/></div><p>After.</p>')
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": body}],
        {"images/locked.png": locked, "images/broken.png": b"not an image",
         "images/good.png": good},
        encryption=("images/locked.png",))
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    assert report["source_image_reference_count"] == 6
    assert report["skipped_image_count"] == 5
    assert report["skipped_image_reasons"] == {
        "missing": 1, "external": 1, "vector_svg": 1,
        "encrypted": 1, "unsupported": 1}
    warning = next(item for item in report["warnings"] if "未纳入译文" in item)
    for label in ("资源缺失", "外链", "矢量 SVG", "已加密", "格式无法识别"):
        assert label in warning
    assert content(data).count("<img ") == 1
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1 and archive.read(media[0]) == good


def test_epub_paragraph_sequence_and_image_boundaries_are_stable():
    pictures = [colored_png(rgb) for rgb in
                ((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0))]
    body = ('<p>Alpha</p><p><img src="../images/a.png"/></p>'
            '<p><img src="../images/b.png"/>Beta</p>'
            '<p>Gamma<img src="../images/c.png"/>Delta</p><p> </p>'
            '<div><img src="../images/d.png"/><p>Nested</p></div><p>Omega</p>')
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": body}],
        {"images/a.png": pictures[0], "images/b.png": pictures[1],
         "images/c.png": pictures[2], "images/d.png": pictures[3]})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert paragraphs == ["Alpha", "Beta", "GammaDelta", "Nested", "Omega"]
    epub_paragraphs, nodes, _media, _source_warnings, _cover = _epub_source(source)
    assert epub_paragraphs == paragraphs
    shape = [f"T{node['ref']}" if node["kind"] == "text" else "IMG" for node in nodes]
    # ``<div><img/><p>Nested</p></div>``: the image sits at the current
    # boundary before the nested paragraph, not at the end of the whole div.
    assert shape == ["T0", "IMG", "T1", "IMG", "T2", "IMG", "IMG", "T3", "T4"]


def test_legacy_epub_task_without_source_refs_exports_images_to_both_formats():
    photo = colored_png((0.9, 0.4, 0.1))
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>First paragraph.</p><div><img src="../images/pic.png"/></div>'
                  '<p>Last paragraph.</p>'}],
        {"images/pic.png": photo})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "legacy.epub", source)
    state = {"filename": "legacy.epub", "source_paras": list(paragraphs),
             "target_lang": "简体中文",
             "pairs": [{"source": text, "target": f"译：{text}"}
                       for text in paragraphs]}
    data, report = build_translation_epub(state, source)
    assert report["image_reference_count"] == 1
    book = content(data)
    assert book.count("<img ") == 1
    assert book.index("译：First paragraph.") < book.index("<img ") < book.index("译：Last paragraph.")
    payload, docx_report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert len(document.inline_shapes) == 1
    assert docx_report["image_count"] == 1
    texts = [paragraph.text for paragraph in document.paragraphs]
    first = next(index for index, text in enumerate(texts) if "译：First" in text)
    last = next(index for index, text in enumerate(texts) if "译：Last" in text)
    picture = next(index for index, text in enumerate(texts) if not text and index > first)
    assert first < picture < last


def test_epub_cat_split_merge_and_exclusion_keep_every_image_once(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    first_picture = colored_png((1, 0, 0))
    second_picture = colored_png((0, 0, 1))
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>First paragraph.</p><div><img src="../images/a.png"/></div>'
                  '<p>Middle paragraph.</p><div><img src="../images/b.png"/></div>'
                  '<p>Last paragraph.</p>'}],
        {"images/a.png": first_picture, "images/b.png": second_picture})
    state = translated_state("cat.epub", source)
    core.save_source("catepub", source)
    core.save_job_state("catepub", state)
    state = core.mutate_translation_segments(
        "catepub", 0, "split", source_a="First", source_b="paragraph.",
        target_a="图前第一部分", target_b="图前第二部分")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 2
    assert content(data).index("图前第一部分") < content(data).index("<img ")
    state = core.mutate_translation_segments("catepub", 0, "merge")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 2
    assert "图前第一部分" in content(data)
    state = core.mutate_translation_segments("catepub", 1, "exclude", exclude_reason="测试排除")
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 2
    assert "译文2：" not in content(data)
    state = core.mutate_translation_segments(
        "catepub", 1, "include",
        excluded_id=state["excluded_segments"][0]["excluded_id"])
    data, _ = build_translation_epub(state, source)
    assert content(data).count("<img ") == 2
    assert "译文2：" in content(data)


def test_epub_docx_export_converts_webp_and_skips_unconvertible_image():
    pytest.importorskip("PIL")
    good = colored_png((0.2, 0.5, 0.8))
    webp = webp_image()
    broken_webp = b"RIFF\x20\x00\x00\x00WEBP" + b"garbage"
    body = ('<p>Before.</p>'
            '<div><img src="../images/photo.webp"/></div>'
            '<div><img src="../images/bad.webp"/></div>'
            '<div><img src="../images/pic.png"/></div><p>After.</p>')
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": body}],
        {"images/photo.webp": webp, "images/bad.webp": broken_webp,
         "images/pic.png": good})
    state = translated_state("convert.epub", source)
    epub_data, epub_report = build_translation_epub(state, source)
    validate_package(epub_data)
    assert epub_report["image_count"] == 3
    with ZipFile(io.BytesIO(epub_data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        webp_item = next(item for item in package.findall("o:manifest/o:item", OPF)
                         if item.get("media-type") == "image/webp")
        assert archive.read("EPUB/" + webp_item.get("href")) == webp
    payload, report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert len(document.inline_shapes) == 2
    assert report["image_count"] == 2
    assert report["skipped_image_count"] == 1
    assert report["skipped_image_reasons"] == {"unsupported_format": 1}
    assert any("1 张图片格式不受支持" in warning for warning in report["warnings"])


def test_epub_without_images_falls_back_to_plain_text_docx():
    source = synthetic_epub([{"href": "text/ch1.xhtml", "body": '<p>Plain text.</p>'}])
    state = translated_state("plain.epub", source)
    payload, report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert not document.inline_shapes
    assert report["image_count"] == 0
    assert any("没有可保留的图片" in warning for warning in report["warnings"])
    assert [paragraph.text for paragraph in document.paragraphs][1:] == [
        pair["target"] for pair in state["pairs"]]


def test_epub_non_linear_spine_items_are_skipped_like_text():
    note_picture = colored_png((0.5, 0.5, 0.5))
    source = synthetic_epub(
        [{"href": "text/c1.xhtml", "body": '<p>Body text.</p>'},
         {"href": "text/notes.xhtml", "linear": "no",
          "body": '<p>Note text.</p><div><img src="../images/note.png"/></div>'}],
        {"images/note.png": note_picture})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert paragraphs == ["Body text."]
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    assert report["image_count"] == 0
    assert report["image_reference_count"] == 0
    assert "Note text." not in content(data)


# ---------------------------------------------------------------------------
# Regression: images must stay between child paragraphs of a container block
# ---------------------------------------------------------------------------

def _node_shape(nodes):
    return [f"T{node['ref']}" if node["kind"] == "text" else "IMG" for node in nodes]


@pytest.mark.parametrize("body,paragraphs,shape", [
    # Image directly between two child paragraphs of a container block.
    ('<div><p>A</p><img src="../images/a.png"/><p>B</p></div>',
     ["A", "B"], ["T0", "IMG", "T1"]),
    ('<section><p>A</p><img src="../images/a.png"/><p>B</p>'
     '<img src="../images/b.png"/><p>C</p></section>',
     ["A", "B", "C"], ["T0", "IMG", "T1", "IMG", "T2"]),
    ('<blockquote><p>A</p><img src="../images/a.png"/><p>B</p></blockquote>',
     ["A", "B"], ["T0", "IMG", "T1"]),
    # Figure image + caption: image before the caption paragraph.
    ('<section><p>A</p><figure><img src="../images/a.png"/>'
     '<figcaption>Cap</figcaption></figure><p>B</p></section>',
     ["A", "Cap", "B"], ["T0", "IMG", "T1", "T2"]),
    ('<p>A</p><figure><img src="../images/a.png"/>'
     '<figcaption>Cap</figcaption></figure><p>B</p>',
     ["A", "Cap", "B"], ["T0", "IMG", "T1", "T2"]),
    # Nested containers must not push the image to the outer block's end.
    ('<div><div><p>A</p><img src="../images/a.png"/><p>B</p></div></div>',
     ["A", "B"], ["T0", "IMG", "T1"]),
    ('<section><div><p>A</p><img src="../images/a.png"/><p>B</p></div>'
     '<p>C</p></section>',
     ["A", "B", "C"], ["T0", "IMG", "T1", "T2"]),
    # Image and paragraphs share the outer block; a leading image stays before A.
    ('<div><img src="../images/a.png"/><p>A</p>'
     '<img src="../images/b.png"/><p>B</p><img src="../images/c.png"/></div>',
     ["A", "B"], ["IMG", "T0", "IMG", "T1", "IMG"]),
])
def test_epub_images_stay_between_child_paragraphs(body, paragraphs, shape):
    image = illustration()
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": body}],
        {"images/a.png": image, "images/b.png": image, "images/c.png": image})
    extracted, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert extracted == paragraphs
    epub_paragraphs, nodes, _media, _source_warnings, _cover = _epub_source(source)
    assert epub_paragraphs == paragraphs
    assert _node_shape(nodes) == shape


@pytest.mark.parametrize("body,paragraphs,shape", [
    ('<p><img src="../images/a.png"/>文字</p><p>B</p>',
     ["文字", "B"], ["T0", "IMG", "T1"]),
    ('<p>文字<img src="../images/a.png"/>尾部</p><p>B</p>',
     ["文字尾部", "B"], ["T0", "IMG", "T1"]),
    ('<div><img src="../images/a.png"/>文字</div><p>B</p>',
     ["文字", "B"], ["T0", "IMG", "T1"]),
    ('<div>文字<img src="../images/a.png"/>尾部</div><p>B</p>',
     ["文字尾部", "B"], ["T0", "IMG", "T1"]),
])
def test_epub_inline_images_follow_their_complete_paragraph(body, paragraphs, shape):
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "body": body}],
        {"images/a.png": illustration()})
    extracted, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert extracted == paragraphs
    epub_paragraphs, nodes, _media, _source_warnings, _cover = _epub_source(source)
    assert epub_paragraphs == paragraphs
    assert _node_shape(nodes) == shape


def test_epub_object_data_image_is_kept_once_and_fallback_text_survives():
    picture = colored_png((0.3, 0.4, 0.7))
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><object data="../images/obj.png">'
                  '<p>Fallback text</p><img src="../images/obj.png"/>'
                  '</object><p>B</p>'}],
        {"images/obj.png": picture})
    paragraphs, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", source)
    assert paragraphs == ["A", "Fallback text", "B"]
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    assert content(data).count("<img ") == 1
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert archive.read(media[0]) == picture


def test_epub_object_data_that_is_not_an_image_is_reported_not_lost():
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><object data="../files/not-image.bin"></object><p>B</p>'}],
        {"files/not-image.bin": b"not an image resource"})
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    validate_package(data)
    assert report["image_count"] == 0
    assert report["source_image_reference_count"] == 1
    assert report["skipped_image_count"] == 1
    assert report["skipped_image_reasons"] == {"unsupported": 1}
    assert any("未纳入译文" in warning for warning in report["warnings"])


def test_epub_img_referenced_svg_is_kept_in_epub_and_rasterized_for_docx():
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
           b'<rect width="40" height="20" fill="red"/></svg>')
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><div><img src="../images/vector.svg"/></div><p>B</p>'}],
        {"images/vector.svg": svg})
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        item = next(item for item in package.findall("o:manifest/o:item", OPF)
                    if item.get("media-type") == "image/svg+xml")
        assert archive.read("EPUB/" + item.get("href")) == svg
    payload, docx_report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert len(document.inline_shapes) == 1
    assert docx_report["image_count"] == 1


def test_epub_unconvertible_svg_is_skipped_for_docx_with_a_warning():
    broken = b"<svg><this is not a valid svg"
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><div><img src="../images/broken.svg"/></div><p>B</p>'}],
        {"images/broken.svg": broken})
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == 1
    payload, docx_report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert not document.inline_shapes
    assert docx_report["image_count"] == 0
    assert docx_report["skipped_image_count"] == 1
    assert docx_report["skipped_image_reasons"] == {"unsupported_format": 1}
    assert any("格式不受支持" in warning for warning in docx_report["warnings"])


def test_epub_docx_falls_back_to_text_when_source_association_cannot_be_bound():
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>First paragraph.</p><div><img src="../images/a.png"/></div>'
                  '<p>Last paragraph.</p>'}],
        {"images/a.png": illustration()})
    state = translated_state("legacy.epub", source)
    state["segment_structure_history"] = [{"operation": "merge"}]
    payload, report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert not document.inline_shapes
    assert report["image_count"] == 0
    assert any("纯文字重建" in warning and "来源关联" in warning
               for warning in report["warnings"])
    # The fallback must not keep claiming that images were preserved.
    assert not any("按原阅读顺序保留" in warning or "图片已保留" in warning
                   or "图片内部文字保持原样" in warning
                   for warning in report["warnings"])
    texts = [paragraph.text for paragraph in document.paragraphs][1:]
    assert texts == [pair["target"] for pair in state["pairs"]]


def test_epub_svg_wrapper_with_embedded_bitmap_is_unwrapped_for_epub_and_docx():
    inner = illustration()
    wrapper = (b'<svg xmlns="http://www.w3.org/2000/svg" '
               b'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 240 120">'
               b'<image xlink:href="inner.png" width="240" height="120"/></svg>')
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><div><img src="../images/wrap.svg"/></div><p>B</p>'}],
        {"images/wrap.svg": wrapper, "images/inner.png": inner})
    state = translated_state("book.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert media[0].endswith(".png")
        assert archive.read(media[0]) == inner
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        assert not any(item.get("media-type") == "image/svg+xml"
                       for item in package.findall("o:manifest/o:item", OPF))
    payload, docx_report = build_translation_docx(state, source)
    document = Document(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert len(document.inline_shapes) == 1
    assert docx_report["image_count"] == 1
    with ZipFile(io.BytesIO(payload.getvalue() if hasattr(payload, "getvalue") else payload)) as archive:
        media_parts = [archive.read(name) for name in archive.namelist()
                       if name.startswith("word/media/")]
    assert media_parts == [inner]


@pytest.mark.parametrize("href,reason", [
    ("missing.png", "missing"),
    ("https://example.com/remote.png", "external"),
])
def test_epub_svg_wrapper_without_a_readable_bitmap_is_skipped(href, reason):
    wrapper = (f'<svg xmlns="http://www.w3.org/2000/svg" '
               f'xmlns:xlink="http://www.w3.org/1999/xlink">'
               f'<image xlink:href="{href}" width="10" height="10"/></svg>').encode()
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>A</p><div><img src="../images/wrap.svg"/></div><p>B</p>'}],
        {"images/wrap.svg": wrapper})
    data, report = build_translation_epub(translated_state("book.epub", source), source)
    validate_package(data)
    assert report["image_count"] == 0
    assert report["source_image_reference_count"] == 1
    assert report["skipped_image_count"] == 1
    assert report["skipped_image_reasons"] == {reason: 1}
    assert any("未纳入译文" in warning for warning in report["warnings"])
    assert content(data).count("<img ") == 0


# --- RC3 acceptance fixes -------------------------------------------------

def test_source_refs_rebuild_without_guessing_spaces_between_segments():
    """S1-01: a paragraph split at a boundary with no blank must still bind.

    The RC3 freeze failed because the map was verified by joining the segments
    with a single space, so ``这是第一句。这是第二句。`` became
    ``这是第一句。 这是第二句。`` and the paragraph looked stale.
    """

    def build(separators):
        row = {"paragraph_index": 0, "start": 0, "end": 2,
               "gate": {"status": "passed"}}
        if separators:
            row["separators"] = [""]
        return {
            "source_paras": ["这是第一句。这是第二句。"],
            "source_paragraphs": ["这是第一句。这是第二句。"],
            "pairs": [{"source": "这是第一句。"}, {"source": "这是第二句。"}],
            "excluded_segments": [{"excluded_id": "ex-1"}],
            "segmentation": {"mode": "sentence", "paragraph_ranges": [row]},
        }

    for separators in (True, False):
        state = build(separators)
        assert bind_source_references(state) is True
        assert [pair[SOURCE_REFS] for pair in state["pairs"]] == [[0], [0]]

    # A real structural change still fails closed: no image may bind to it.
    edited = build(True)
    edited["pairs"][1]["source"] = "这是第二段。"
    assert bind_source_references(edited) is False
    assert SOURCE_REFS not in edited["pairs"][1]


def test_excluded_segment_still_binds_a_paragraph_split_without_spaces(tmp_path, monkeypatch):
    """S1-01 end-to-end: EPUB and DOCX delivery survive the exclusion.

    The RC3 freeze failed exactly here: the task was translated, the map was
    never backfilled (the space-less boundary looked stale), one segment was
    excluded, and both exports refused to build.  Dropping the references
    reproduces that state on disk.
    """
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    document = Document()
    document.add_paragraph("这是第一句。这是第二句。")
    document.add_paragraph("另一段。")
    stream = io.BytesIO()
    document.save(stream)
    source = stream.getvalue()
    state = translated_state("cjk.docx", source)
    assert [pair["source"] for pair in state["pairs"]][:2] == ["这是第一句。", "这是第二句。"]
    core.save_source("cjksplit", source)
    core.save_job_state("cjksplit", state)
    index = next(i for i, pair in enumerate(state["pairs"])
                 if pair["source"] == "另一段。")
    state = core.mutate_translation_segments(
        "cjksplit", index, "exclude", exclude_reason="测试排除")
    assert state["excluded_segments"]
    for pair in state["pairs"]:
        pair.pop(SOURCE_REFS, None)
    core.save_job_state("cjksplit", state)

    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["chapter_count"] >= 1

    payload, docx_report = build_translation_docx(state, source)
    assert not any("来源关联" in warning for warning in docx_report["warnings"])
    rebuilt = Document(io.BytesIO(
        payload.getvalue() if hasattr(payload, "getvalue") else payload))
    assert [paragraph.text for paragraph in rebuilt.paragraphs][1:] == [
        pair["target"] for pair in state["pairs"]]


def test_epub_export_keeps_source_spine_and_heading_structure():
    """S2-01: three source chapters stay three chapters with a usable nav."""
    source = synthetic_epub([
        {"href": "text/ch1.xhtml",
         "body": "<h1>Chapter One</h1><p>First chapter body.</p>"},
        {"href": "text/ch2.xhtml",
         "body": "<h1>Chapter Two</h1><p>Second chapter body.</p>"},
        {"href": "text/ch3.xhtml",
         "body": "<h1>Chapter Three</h1><p>Third chapter body.</p>"},
    ])
    state = translated_state("structured.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["source_document_count"] == 3
    assert report["source_heading_count"] == 3
    assert report["chapter_count"] == 3
    with ZipFile(io.BytesIO(data)) as archive:
        package = ET.fromstring(archive.read("EPUB/content.opf"))
        spine = package.findall("o:spine/o:itemref", OPF)
        assert len(spine) == 3
        titles = [next(pair["target"] for pair in state["pairs"]
                       if pair["source"] == heading)
                  for heading in ("Chapter One", "Chapter Two", "Chapter Three")]
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        for title in titles:
            assert title in nav
        for name in sorted(item for item in archive.namelist()
                           if item.endswith(".xhtml") and "chapter-" in item):
            page = ET.fromstring(archive.read(name))
            assert page.find(".//h:h1", XHTML) is not None


def test_epub_css_background_image_reaches_the_delivery():
    """S2-02: a picture that only exists as a CSS background is not dropped."""
    backdrop = colored_png((0.25, 0.45, 0.8))
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "stylesheet": "../1.css",
          "body": '<p>Before the picture.</p>'
                  '<table class="rabbithole"><tr><td>After the picture.</td></tr></table>'}],
        {"images/rabbithole.png": backdrop},
        stylesheets={"1.css": 'table.rabbithole { background-image: url("images/rabbithole.png"); }'})
    state = translated_state("background.epub", source)
    data, report = build_translation_epub(state, source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    assert report["skipped_image_count"] == 0
    assert report["skipped_image_reasons"] == {}
    with ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("EPUB/images/")]
        assert len(media) == 1
        assert archive.read(media[0]) == backdrop
    page = content(data)
    assert page.index("<img ") < page.index("After the picture.")


def test_epub_unused_css_background_is_reported_instead_of_silently_dropped():
    """S2-02: a stylesheet image no element uses is counted, not claimed as 0."""
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "stylesheet": "../1.css",
          "body": "<p>No table in this chapter.</p>"}],
        {"images/rabbithole.png": colored_png((0.1, 0.6, 0.2))},
        stylesheets={"1.css": 'table.rabbithole { background-image: url("images/rabbithole.png"); }'})
    data, report = build_translation_epub(translated_state("unused.epub", source), source)
    validate_package(data)
    assert report["image_count"] == 0
    assert report["skipped_image_count"] == 1
    assert report["skipped_image_reasons"] == {"css_background": 1}
    assert any("样式表背景图" in warning for warning in report["warnings"])
    assert content(data).count("<img ") == 0


def test_epub_css_background_used_in_one_chapter_is_not_reported_as_skipped():
    """S2-02: a shared stylesheet must not inflate the skipped count."""
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml", "stylesheet": "../1.css",
          "body": '<table class="rabbithole"><tr><td>Picture chapter.</td></tr></table>'},
         {"href": "text/ch2.xhtml", "stylesheet": "../1.css",
          "body": "<p>Plain chapter.</p>"}],
        {"images/rabbithole.png": colored_png((0.3, 0.3, 0.7))},
        stylesheets={"1.css": 'table.rabbithole { background-image: url("images/rabbithole.png"); }'})
    data, report = build_translation_epub(translated_state("shared.epub", source), source)
    validate_package(data)
    assert report["image_count"] == 1
    assert report["skipped_image_count"] == 0
    assert report["skipped_image_reasons"] == {}


def test_epub_inline_style_background_is_collected():
    """S2-02: the same rule applies when the declaration lives on the element."""
    source = synthetic_epub(
        [{"href": "text/ch1.xhtml",
          "body": '<p>Intro.</p><div style="background-image:url(../images/inline.png)">Block.</div>'}],
        {"images/inline.png": colored_png((0.6, 0.2, 0.4))})
    data, report = build_translation_epub(translated_state("inline.epub", source), source)
    validate_package(data)
    assert report["image_count"] == report["image_reference_count"] == 1
    assert report["skipped_image_count"] == 0
