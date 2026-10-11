"""New sources preserve text and order and disclose omitted content."""
import io
import zipfile

import pytest

import core
from folith.qt import adapter


def epub_bytes(*, encrypted=False, missing=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>')
        book.writestr("OEBPS/book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="b" href="second.xhtml" media-type="application/xhtml+xml"/><item id="a" href="first%20chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
        if not missing:
            book.writestr("OEBPS/first chapter.xhtml", '<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>Metadata only</title></head><body><h1>第一章</h1><p>العربية <em>正文</em>。</p><img src="cover.png"/></body></html>')
        book.writestr("OEBPS/second.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second chapter.</p></body></html>')
        if encrypted:
            book.writestr("META-INF/encryption.xml", '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#"><CipherData><CipherReference URI="OEBPS/second.xhtml"/></CipherData></EncryptedData></encryption>')
    return stream.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_encoding_and_paragraph_boundaries(encoding):
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(
        "source.TXT", "第一段。\r\n连续行。\r\n\r\n二".encode(encoding))
    assert paragraphs == ["第一段。\n连续行。", "二"]
    assert not warnings
    assert report["status"] == "complete"
    assert not report["ocr_used"]


@pytest.mark.parametrize("suffix", [".md", ".markdown"])
def test_markdown_readable_text_and_explicit_omissions(suffix):
    source = '# Heading\n\nA **bold** [link](https://example.com).\n\n- One\n- Two\n\n| A | B |\n|---|---|\n| C | D |\n\n```python\nSECRET_CODE\n```\n\n![cover](image.png)\n'
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report("source" + suffix, source.encode())
    assert paragraphs[:4] == ["Heading", "A bold link.", "One", "Two"]
    assert any("C" in text and "D" in text for text in paragraphs)
    assert "SECRET_CODE" not in "\n".join(paragraphs)
    assert {item["kind"] for item in report["unsupported"]} == {"code", "image"}
    assert report["status"] == "partial"
    assert not warnings


def test_epub_spine_order_multilingual_text_and_omissions():
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report("book.epub", epub_bytes())
    assert paragraphs == ["第一章", "العربية 正文。", "Second chapter."]
    assert not warnings
    assert report["extracted"]["chapters"] == 2
    assert report["unsupported"][0]["kind"] == "image"
    assert report["status"] == "partial"


@pytest.mark.parametrize("data", [b"not a zip", epub_bytes(encrypted=True), epub_bytes(missing=True)])
def test_invalid_or_encrypted_ebook_fails_without_partial_translation(data):
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report("bad.epub", data)
    assert not paragraphs
    assert warnings
    assert report["status"] == "failed"
    assert "DOCX" not in report["error"]


def test_prefixed_xhtml_excludes_metadata_and_preserves_body():
    from folith.text_import import _html_paragraphs
    paragraphs, omitted = _html_paragraphs('<x:html xmlns:x="http://www.w3.org/1999/xhtml"><x:head><x:title>Metadata</x:title></x:head><x:body><x:p>Readable <x:br/>body.</x:p></x:body></x:html>')
    assert paragraphs == ["Readable body."]
    assert not omitted


def test_utf8_html_without_charset_preserves_multilingual_text():
    from folith.text_import import _html_paragraphs
    paragraphs, omitted = _html_paragraphs('<html><body><p>中文 العربية</p></body></html>'.encode())
    assert paragraphs == ["中文 العربية"]
    assert not omitted


@pytest.mark.parametrize("suffix,data", [(".txt", b"Text paragraph."), (".md", b"# Heading"), (".markdown", b"Body text."), (".epub", epub_bytes())])
def test_actual_file_admission_reaches_shared_extraction(tmp_path, suffix, data):
    path = tmp_path / ("source" + suffix)
    path.write_bytes(data)
    name, raw = adapter.read_source_file(path)
    paragraphs, warnings, report = core.extract_document_paragraphs_with_report(name, raw)
    assert paragraphs, warnings
    assert report["status"] != "failed"


@pytest.mark.parametrize("suffix,data", [(".txt", b"Text paragraph."), (".md", b"# Heading"), (".epub", epub_bytes())])
def test_new_sources_can_export_translation_epub(suffix, data):
    from folith.epub import build_translation_epub
    paragraphs, _, _ = core.extract_document_paragraphs_with_report("source" + suffix, data)
    state = {"filename": "source" + suffix, "source_paras": paragraphs,
             "source_paragraphs": paragraphs, "target_lang": "简体中文",
             "pairs": [{"source": text, "target": f"译文 {index}", "epub_source_paragraphs": [index]}
                       for index, text in enumerate(paragraphs)]}
    output, report = build_translation_epub(state, data)
    assert report["image_count"] == 0
    assert any("纯文字" in warning for warning in report["warnings"])
    with zipfile.ZipFile(io.BytesIO(output)) as book:
        translated = "".join(book.read(name).decode() for name in book.namelist() if "chapter-" in name)
    assert all(f"译文 {index}" in translated for index in range(len(paragraphs)))
