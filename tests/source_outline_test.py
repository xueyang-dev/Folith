"""原文结构解析回归（#27）：模型无关、扫描页如实、与 core 提取逐字对齐。

这一批守护的是「准备」页只读原文结构的两条底线：

- **不猜**：本地读不出的（扫描页、加密、损坏文件）必须如实返回空/未识别，
  绝不把扫描页当成已识别内容，也绝不为看结构去调模型；
- **可对齐**：结构解析的段落与 :func:`core.extract_document_paragraphs_with_report`
  在文字层路径上必须逐字相同——按结构消费这份结果的界面（#28 的翻译范围、
  #61 的原位置定位）靠这份一致性直接复用锚点，不需要第二套解析。
"""
import io

import fitz
import pytest
from docx import Document

import core
from folith.source_outline import (empty_outline, outline_fingerprint,
                                   parse_source_outline)


def _docx(*blocks) -> bytes:
    document = Document()
    for kind, text in blocks:
        if kind == "h":
            document.add_heading(text, level=1)
        else:
            document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _pdf(pages: int, *, toc=None, textless=()) -> bytes:
    """每页有可读正文（页与页内容不同，避免被当页眉页脚归一）。"""
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        if index + 1 in textless:
            continue
        page.insert_text((72, 72), f"第{index + 1}章 章节标题文字", fontsize=16)
        for line in range(4):
            page.insert_text(
                (72, 130 + line * 20),
                f"这是第{index + 1}页的第{line + 1}句话，页页不同，用来当正文。",
                fontsize=11)
    if toc:
        document.set_toc(toc)
    return document.tobytes()


# ---------------------------------------------------------------- TXT / Markdown

def test_txt_paragraph_groups_are_identified_structure():
    data = "\n\n".join(f"段落 {index} 的内容。" for index in range(1, 46)).encode()
    outline = parse_source_outline("book.txt", data)
    assert outline["total_paragraphs"] == 45
    assert len(outline["nodes"]) == 3
    first = outline["nodes"][0]
    assert first["kind"] == "group"
    assert first["para_start"] == 0 and first["para_count"] == 20
    assert first["paragraphs"][0] == "段落 1 的内容。"
    # 分组必须恰好覆盖全部段落，一段不漏。
    assert sum(node["para_count"] for node in outline["nodes"]) == 45


def test_markdown_headings_bound_chapters():
    markdown = (b"# Intro\n\nFirst paragraph.\n\n"
                b"## Chapter\n\nBody text here.\n\n"
                b"# Fin\n\nLast words.")
    outline = parse_source_outline("doc.md", markdown)
    assert [(node["title"], node["level"]) for node in outline["nodes"]] == [
        ("Intro", 1), ("Chapter", 2), ("Fin", 1)]
    # 标题本身就是段落序列的一员（与 core 提取一致），落在自己章节的开头。
    assert outline["nodes"][1]["para_start"] == 2
    assert outline["paragraphs"][2] == "Chapter"


# ---------------------------------------------------------------- EPUB

def _epub() -> bytes:
    import zipfile

    chapter = ("<?xml version='1.0' encoding='utf-8'?>"
               "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
               "<h1>{title}</h1><p>{body}</p>"
               "</body></html>")
    container = ("<?xml version='1.0'?>"
                 "<container version='1.0' xmlns='urn:oasis:names:tc:opendocument:"
                 "xmlns:container'><rootfiles><rootfile full-path='OEBPS/content.opf'"
                 " media-type='application/oebps-package+xml'/></rootfiles></container>")
    opf = ("<?xml version='1.0'?>"
           "<package xmlns='http://www.idpf.org/2007/opf' version='2.0' "
           "unique-identifier='id'><metadata/>"
           "<manifest><item id='c1' href='c1.xhtml' "
           "media-type='application/xhtml+xml'/>"
           "<item id='c2' href='c2.xhtml' "
           "media-type='application/xhtml+xml'/></manifest>"
           "<spine><itemref idref='c1'/><itemref idref='c2'/></spine></package>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        book.writestr("META-INF/container.xml", container)
        book.writestr("OEBPS/content.opf", opf)
        book.writestr("OEBPS/c1.xhtml",
                      chapter.format(title="第一章", body="第一章的正文内容，足够长。"))
        book.writestr("OEBPS/c2.xhtml",
                      chapter.format(title="第二章", body="第二章的正文内容，足够长。"))
    return buffer.getvalue()


def test_epub_spine_chapters_bound_nodes_and_match_core_extraction():
    data = _epub()
    outline = parse_source_outline("book.epub", data)
    assert [node["title"] for node in outline["nodes"]] == ["第一章", "第二章"]
    assert outline["chapters"] == 2
    # 对齐契约：EPUB 段落序列与 core 提取（extract_text_source 同一批函数）逐字相同。
    extracted, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.epub", data, ocr_mode="off", ocr_max_pages=0)
    assert outline["paragraphs"] == [str(item) for item in extracted]


# ---------------------------------------------------------------- DOCX

def test_docx_heading_styles_bound_chapters_and_match_core_extraction():
    data = _docx(
        ("p", "开篇没有标题的一句正文，长度足够。"),
        ("h", "第一章"),
        ("p", "第一章的正文内容，长度足够被保留下来。"),
        ("h", "第二章"),
        ("p", "第二章的正文内容，长度也足够被保留。"),
    )
    outline = parse_source_outline("sample.docx", data)
    assert [(node["title"], node["para_start"], node["para_count"])
            for node in outline["nodes"]] == [
        ("未分章内容", 0, 1),
        ("第一章", 1, 2),
        ("第二章", 3, 2),
    ]
    # 对齐契约：DOCX 段落序列与 core 提取逐字相同。
    extracted, _warnings, _report = core.extract_document_paragraphs_with_report(
        "sample.docx", data, ocr_mode="off", ocr_max_pages=0)
    assert outline["paragraphs"] == list(extracted)


def test_docx_non_heading_styles_do_not_split():
    data = _docx(("h", "唯一一章"), ("p", "第一段。"), ("p", "第二段。"))
    outline = parse_source_outline("sample.docx", data)
    assert len(outline["nodes"]) == 1
    assert outline["nodes"][0]["title"] == "唯一一章"


# ---------------------------------------------------------------- PDF

def test_pdf_without_toc_uses_page_nodes():
    data = _pdf(3)
    outline = parse_source_outline("book.pdf", data)
    assert outline["pages"] == 3 and not outline["scanned"]
    assert [node["page_start"] for node in outline["nodes"]] == [1, 2, 3]
    assert all(node["para_count"] >= 1 for node in outline["nodes"])
    # 对齐契约：段落序列与 core 文字层路径逐字相同。
    extracted, _warnings, _report = core.extract_document_paragraphs_with_report(
        "book.pdf", data, ocr_mode="off", ocr_max_pages=0)
    assert outline["paragraphs"] == [str(item) for item in extracted]


def test_pdf_toc_chapters_cover_pages():
    data = _pdf(4, toc=[[1, "上篇", 1], [1, "下篇", 3]])
    outline = parse_source_outline("book.pdf", data)
    assert [node["title"] for node in outline["nodes"]] == ["上篇", "下篇"]
    assert (outline["nodes"][0]["page_start"], outline["nodes"][0]["page_end"]) == (1, 2)
    assert (outline["nodes"][1]["page_start"], outline["nodes"][1]["page_end"]) == (3, 4)
    # 章节覆盖全部段落：按页分给章节的段落总数 = 总段数（本文无游离段落）。
    assert sum(node["para_count"] for node in outline["nodes"]) \
        == outline["total_paragraphs"]


def test_pdf_textless_pages_reported_not_faked():
    data = _pdf(3, textless={2})
    outline = parse_source_outline("book.pdf", data)
    assert outline["textless_pages"] == [2]
    assert not outline["scanned"]
    page_two = next(node for node in outline["nodes"] if node["page_start"] == 2)
    assert page_two["para_count"] == 0
    assert page_two["scanned"] is True  # 单页无文字层 → 节点如实标记"未识别"


def test_fully_scanned_pdf_states_it_clearly():
    data = _pdf(2, textless={1, 2})
    outline = parse_source_outline("book.pdf", data)
    assert outline["scanned"] is True
    assert outline["total_paragraphs"] == 0
    assert all(node["scanned"] for node in outline["nodes"])


def test_encrypted_pdf_returns_no_content_without_error():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "secret body text that is long enough")
    buffer = io.BytesIO()
    document.save(buffer, encryption=fitz.PDF_ENCRYPT_AES_128,
                  owner_pw="x", user_pw="y")
    outline = parse_source_outline("locked.pdf", buffer.getvalue())
    assert outline.get("encrypted") is True
    assert outline["total_paragraphs"] == 0
    assert outline["nodes"] == []


def test_broken_file_returns_empty_outline_without_error():
    outline = parse_source_outline("book.pdf", b"this is not a pdf at all")
    assert outline["nodes"] == [] and outline["paragraphs"] == []
    assert outline["fingerprint"] == outline_fingerprint(empty_outline("book.pdf"))


# ---------------------------------------------------------------- 指纹与缓存口径

def test_fingerprint_stable_for_same_source_changes_with_structure():
    data = _docx(("h", "第一章"), ("p", "第一段正文。"))
    first = parse_source_outline("sample.docx", data)
    second = parse_source_outline("sample.docx", data)
    assert first["fingerprint"] == second["fingerprint"]
    changed = _docx(("h", "第一章"), ("p", "第一段正文。"), ("p", "多出来的一段。"))
    assert parse_source_outline("sample.docx", changed)["fingerprint"] \
        != first["fingerprint"]


def test_unknown_suffix_returns_empty_outline():
    outline = parse_source_outline("photo.jpg", b"\xff\xd8\xff")
    assert outline["nodes"] == [] and outline["paragraphs"] == []
    assert outline["format"] == "JPG"


@pytest.mark.parametrize("filename", ["book.pdf", "sample.docx", "book.epub",
                                      "doc.md", "notes.txt"])
def test_every_supported_format_returns_finished_outline(filename):
    """统一收尾（total/fingerprint/id 顺序落定）对每种格式都成立，缺省安全。"""
    data = {"book.pdf": _pdf(1), "sample.docx": _docx(("h", "章"), ("p", "正文段落。")),
            "book.epub": _epub(),
            "doc.md": b"# T\n\nBody.", "notes.txt": "一段正文。".encode()}[filename]
    outline = parse_source_outline(filename, data)
    assert outline["fingerprint"]
    assert outline["total_paragraphs"] == len(outline["paragraphs"])
    assert [node["id"] for node in outline["nodes"]] == \
        [f"n{index:04d}" for index in range(len(outline["nodes"]))]
