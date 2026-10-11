"""「准备」页左栏的文档概览：本地读出的事实与用户能看懂的措辞。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _pdf(tmp_path, *, blank_pages=(), pages=6, password=False) -> Path:
    import fitz

    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        if index in blank_pages:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), 0)
            pix.clear_with(180)
            page.insert_image(page.rect, pixmap=pix)
            continue
        page.insert_text((72, 72), "A page with enough real text on it to count.", fontsize=11)
    path = tmp_path / "source.bin"
    if password:
        path.write_bytes(document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                          owner_pw="o", user_pw="u"))
    else:
        path.write_bytes(document.tobytes())
    return path


def test_pdf_facts_find_the_pages_without_a_text_layer(tmp_path):
    from folith.qt import doc_facts

    facts = doc_facts.document_facts(_pdf(tmp_path, blank_pages=(2, 3, 5)), "book.pdf")
    assert facts["format"] == "PDF" and facts["pages"] == 6
    assert facts["pages_without_text"] == [3, 4, 6]
    assert facts["images"] == 1 and facts["bytes"] > 0


def test_page_ranges_read_naturally():
    from folith.qt.doc_facts import page_ranges

    assert page_ranges([3, 4, 5, 9]) == "3–5、9"
    assert page_ranges([1]) == "1" and page_ranges([]) == ""


def test_overview_wording_speaks_about_effects(app, tmp_path):
    from folith.qt import doc_facts, doc_overview

    facts = doc_facts.document_facts(_pdf(tmp_path, blank_pages=(1, 2)), "book.pdf")
    meta, notes = doc_overview.describe(facts, source_lang="English")
    assert meta.startswith("PDF · 6 页")
    assert ("warn", "第 2–3 页没有文字层，需要识别") in notes
    assert ("ok", "含 1 张图片") in notes

    clean = doc_facts.document_facts(_pdf(tmp_path), "book.pdf")
    _meta, notes = doc_overview.describe(clean)
    assert notes == [("ok", "每页都有文字层，可直接读取正文")]

    scanned = doc_facts.document_facts(_pdf(tmp_path, blank_pages=range(6)), "book.pdf")
    _meta, notes = doc_overview.describe(scanned)
    assert notes[0] == ("warn", "整份文档没有文字层，正文需要识别")


def test_encrypted_or_unknown_files_do_not_raise(app, tmp_path):
    from folith.qt import doc_facts, doc_overview

    facts = doc_facts.document_facts(_pdf(tmp_path, password=True), "locked.pdf")
    assert facts.get("encrypted") is True
    _meta, notes = doc_overview.describe(facts)
    assert notes == [("warn", "文件已加密，无法读取内容")]
    odd = tmp_path / "odd.bin"
    odd.write_bytes(b"not a document")
    assert doc_facts.document_facts(odd, "notes.docx")["format"] == "DOCX"


def test_overview_widget_fills_in_from_the_background(app, tmp_path, monkeypatch):
    import time

    from folith.qt import adapter, doc_facts, doc_overview, thumbnails

    source = _pdf(tmp_path, blank_pages=(4,))
    monkeypatch.setattr(adapter, "source_path", lambda _job: source)
    view = doc_overview.DocumentOverview()
    view.set_document("job-overview", "book.pdf")
    deadline = time.time() + 10
    while time.time() < deadline and not view.note_texts():
        doc_facts.loader().wait(200)
        thumbnails.loader().wait(200)
        app.processEvents()
    assert "第 5 页没有文字层，需要识别" in view.note_texts()
    assert view.meta_label.text().startswith("PDF · 6 页")
    assert view.name_label.text() == "book.pdf"
