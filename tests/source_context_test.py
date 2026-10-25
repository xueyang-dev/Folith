"""流式源（DOCX / EPUB / TXT / Markdown）的原位置上下文（#61）。

* 四种格式都能解析出顺序块；DOCX 带标题路径与表格行，EPUB 按章节分组，
  TXT / Markdown 带行号（Markdown 另有标题路径、跳过代码块）；
* 定位按候选短语匹配，给出标题 / 章节 / 行号 + 相邻段落并高亮匹配块；
* 找不到、没有原文、源文件缺失都如实说明，不崩溃。
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import source_context  # noqa: E402

docx = pytest.importorskip("docx")

SENTENCE = "The committee agreed that every revision must leave a visible trace."


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_heading("Report", level=1)
    document.add_paragraph("Introductory remarks before the findings.")
    document.add_heading("Findings", level=2)
    document.add_paragraph(SENTENCE)
    document.add_paragraph("Closing note after the findings.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Term"
    table.rows[0].cells[1].text = "Gloss"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _epub_bytes() -> bytes:
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
        'version="3.0" unique-identifier="id"><manifest>'
        '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
        '</package>')
    chapter_one = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title></head>'
        '<body><h1>The River</h1><p>Water ran quietly all through the night.</p>'
        f'<p>{SENTENCE}</p></body></html>')
    chapter_two = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c2</title></head>'
        '<body><h1>The Town</h1><p>Streets emptied before dawn.</p></body></html>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip",
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/ch1.xhtml", chapter_one)
        archive.writestr("OEBPS/ch2.xhtml", chapter_two)
    return buffer.getvalue()


TEXT_SOURCE = "first plain line\nsecond plain line\na third line appears here\nlast line\n"

MARKDOWN_SOURCE = (
    "# Guide\nintro paragraph\n## Deep Dive\n"
    "```python\nSENTENCE_IN_CODE = True\n```\n"
    f"detail line with {SENTENCE}\n")


@pytest.fixture()
def job_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    counter = iter(range(1, 100))

    def create(filename: str, source: bytes) -> str:
        job_id = f"srcctx{next(counter):09d}"
        state = core.new_job_state(filename)
        state.update(p1_done=True, p2_done=True, paras=[SENTENCE],
                     pairs=[{"source": SENTENCE, "target": "译文"}])
        core.save_job_state(job_id, state)
        core.save_source(job_id, source)
        return job_id

    return create


def test_only_streaming_tasks_have_a_source_context(job_factory):
    epub_job = job_factory("book.epub", _epub_bytes())
    pdf_job = job_factory("paper.pdf", b"%PDF-1.4 fake")
    assert source_context.has_streaming_source(epub_job) is True
    assert source_context.source_kind(epub_job) == "epub"
    assert source_context.has_streaming_source(pdf_job) is False
    assert source_context.has_streaming_source("") is False
    assert source_context.has_streaming_source("missing-job") is False


def test_docx_context_carries_heading_path_and_table(job_factory):
    job_id = job_factory("report.docx", _docx_bytes())
    context = source_context.locate_segment(job_id, SENTENCE)
    assert context["found"] and context["kind"] == "docx"
    assert context["title"] == "Report › Findings"
    highlighted = [block for block in context["blocks"] if block["highlight"]]
    assert len(highlighted) == 1 and highlighted[0]["text"] == SENTENCE
    roles = [block["role"] for block in context["blocks"]]
    assert "heading" in roles, "上下文里应带所属标题"
    table = source_context.load_blocks(job_id)
    assert any("Term · Gloss" in block["text"] for block in table), \
        "表格按行以文本呈现"


def test_epub_context_names_the_chapter(job_factory):
    job_id = job_factory("book.epub", _epub_bytes())
    context = source_context.locate_segment(job_id, SENTENCE)
    assert context["found"] and context["title"] == "The River"
    texts = [block["text"] for block in context["blocks"]]
    assert "Water ran quietly all through the night." in texts, "带相邻段落"
    other = source_context.locate_segment(job_id, "Streets emptied before dawn.")
    assert other["title"] == "The Town", "第二卷不会误报成第一章"


def test_text_context_reports_the_line(job_factory):
    job_id = job_factory("notes.txt", TEXT_SOURCE.encode("utf-8"))
    context = source_context.locate_segment(job_id, "a third line appears here")
    assert context["found"] and context["title"] == "第 3 行"
    assert [block["highlight"] for block in context["blocks"]].count(True) == 1


def test_markdown_context_skips_code_and_adds_the_path(job_factory):
    job_id = job_factory("guide.md", MARKDOWN_SOURCE.encode("utf-8"))
    context = source_context.locate_segment(job_id, SENTENCE)
    assert context["found"] and context["kind"] == "markdown"
    assert context["title"] == "Guide › Deep Dive › 第 7 行"
    all_blocks = source_context.load_blocks(job_id)
    assert not any("SENTENCE_IN_CODE" in block["text"] for block in all_blocks), \
        "代码块不参与定位"


def test_missing_segment_says_so(job_factory):
    job_id = job_factory("notes.txt", TEXT_SOURCE.encode("utf-8"))
    context = source_context.locate_segment(job_id, "zzzz qqqq nothing like this")
    assert context["found"] is False and "没能" in context["hint"]
    assert context["blocks"] == []


def test_unreadable_source_is_honest(job_factory):
    job_id = job_factory("broken.docx", b"PK\x03\x04 not a real docx")
    context = source_context.locate_segment(job_id, SENTENCE)
    assert context["found"] is False and "无法读取" in context["hint"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
