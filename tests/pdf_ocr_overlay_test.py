"""扫描书的 OCR 文本层（整页图片 + 隐形文字）也要拼回句子和段落。

实测：一本扫描小说（Internet Archive 一类的 PDF），封底居中排版、OCR 字号逐词抖动、
图里纹理被识别成 ``©`` ``.`` 之类的孤立符号行——工作台里一行一段，个别行还被当成
图注静默丢掉。这里用同样形状的合成 PDF 复现。
"""
from __future__ import annotations

import random

import fitz

from folith import pdf_ingestion

WIDTH, HEIGHT = 420, 640

BACK_COVER = [
    "BETWEEN LIFE AND DEATH",
    "THERE IS A LIBRARY.",
    None,
    "When Nora Seed finds herself in the",
    ".",
    "_ Midnight Library, she has a chance to make things right.",
    "Up until now, her",
    "©",
    "life has been full of misery and regret.",
    "She feels she has let everyone down, including herself.",
    "But things are",
    "about to change.",
    None,
    "The books in the Midnight Library",
    "enable Nora to live as if she had done",
    "things differently.",
    None,
    "Before time runs out, she must answer",
    "the ultimate question: what is the",
    "best way to live?",
]


def _scan_page(doc, lines, *, centred, seed):
    rng = random.Random(seed)
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 42, 64), 0)
    pix.set_rect(pix.irect, (30, 90, 60))
    page.insert_image(page.rect, pixmap=pix)
    y = 60.0
    for line in lines:
        if line is None:
            y += 18
            continue
        size = rng.uniform(10.2, 12.4)
        words = line.split()
        width = sum(fitz.get_text_length(w, fontsize=size) + size * 0.3 for w in words)
        if centred:
            x = (WIDTH - width) / 2 + rng.uniform(-3, 3)
        else:
            x = 40 + rng.uniform(-1.5, 1.5) + (14 if line.startswith("  ") else 0)
        for word in words:
            # OCR 层逐词定位，字号逐词抖动；render_mode=3 即隐形文字。
            word_size = size * rng.uniform(0.93, 1.07)
            page.insert_text((x, y), word, fontsize=word_size, render_mode=3)
            x += fitz.get_text_length(word, fontsize=word_size) + size * 0.3
        y += 17 + rng.uniform(-1, 1)


def _pdf(pages):
    doc = fitz.open()
    for index, (lines, centred) in enumerate(pages):
        _scan_page(doc, lines, centred=centred, seed=index)
    return doc.tobytes()


def test_centred_back_cover_is_joined_into_its_paragraphs():
    paragraphs = pdf_ingestion.extract_pdf_paragraphs(_pdf([(BACK_COVER, True)]))
    assert paragraphs == [
        "BETWEEN LIFE AND DEATH THERE IS A LIBRARY.",
        "When Nora Seed finds herself in the Midnight Library, she has a chance to "
        "make things right. Up until now, her life has been full of misery and "
        "regret. She feels she has let everyone down, including herself. But things "
        "are about to change.",
        "The books in the Midnight Library enable Nora to live as if she had done "
        "things differently.",
        "Before time runs out, she must answer the ultimate question: what is the "
        "best way to live?",
    ]


def test_left_aligned_body_splits_at_indents_and_joins_across_pages():
    first = [
        "Nineteen years before she decided to die, Nora Seed sat in the warmth",
        "of the small library at Hazeldene School. She sat",
        "at a low table staring at a chessboard.",
        "  'Nora darling, it's natural to worry,' said the school librarian, Mrs",
        "Elm, her eyes twinkling with light. Mrs Elm made the first move. A knight",
        "jumping over the neat row of white pawns and into the",
    ]
    second = [
        "open middle of the board. 'Of course you'll be fine.'",
        "  Nora looked at the board for a long time.",
    ]
    paragraphs = pdf_ingestion.extract_pdf_paragraphs(
        _pdf([(first, False), (second, False)]))
    assert paragraphs == [
        "Nineteen years before she decided to die, Nora Seed sat in the warmth of "
        "the small library at Hazeldene School. She sat at a low table staring at a "
        "chessboard.",
        "'Nora darling, it's natural to worry,' said the school librarian, Mrs Elm, "
        "her eyes twinkling with light. Mrs Elm made the first move. A knight "
        "jumping over the neat row of white pawns and into the open middle of the "
        "board. 'Of course you'll be fine.'",
        "Nora looked at the board for a long time.",
    ]


def test_scan_lines_are_not_mistaken_for_captions_of_the_page_image():
    blocks = pdf_ingestion.classify_blocks(
        pdf_ingestion.extract_layout_blocks(_pdf([(BACK_COVER, True)])))
    text_blocks = [block for block in blocks if block.get("block_type") == "text"]
    assert text_blocks and all(block["ocr_overlay"] for block in text_blocks)
    assert not [block["text"] for block in text_blocks
                if block["role"] in pdf_ingestion.SKIPPED_ROLES
                and any(ch.isalpha() for ch in block["text"])]


def test_typeset_pages_are_not_treated_as_ocr_overlays():
    doc = fitz.open()
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    page.insert_text((40, 60), "A typeset line of visible text.", fontsize=11)
    blocks = pdf_ingestion.extract_layout_blocks(doc.tobytes())
    assert blocks and not any(block.get("ocr_overlay") for block in blocks)


def _overlay_block(text, size, y0):
    return {
        "block_type": "text", "text": text, "font_size": size, "font_flags": 0,
        "page_number": 1, "page_height": HEIGHT, "page_width": WIDTH,
        "x0": 60.0, "y0": y0, "x1": 300.0, "y1": y0 + 14, "ocr_overlay": True,
        "lines": [{"text": text, "x0": 60.0, "y0": y0, "x1": 300.0, "y1": y0 + 14}],
    }


def test_oversized_ocr_prose_is_not_a_heading_but_title_case_still_is():
    # 实测封底：OCR 把一个居中正文块的字号估成正文的 1.3 倍，整块被判成标题，
    # 于是 ``She feels … let everyone down,`` 再也接不上下一行。
    body = [_overlay_block("Body text line that sets the body size", 10.0, 200 + i * 16)
            for i in range(6)]
    prose = _overlay_block("‘She feels she has let everyone down, a including herself. "
                           "But things are", 13.0, 100)
    title = _overlay_block("The Moving Shelves", 13.0, 80)
    clock = _overlay_block("00:00:00", 13.0, 60)
    roles = {block["text"]: block["role"]
             for block in pdf_ingestion.classify_blocks([clock, title, prose, *body])}
    assert roles[prose["text"]] in pdf_ingestion.JOINABLE_ROLES
    assert roles["The Moving Shelves"] == "heading"
    assert roles["00:00:00"] == "heading"
