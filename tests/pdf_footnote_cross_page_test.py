"""跨页正文段被页底脚注截断（#81）：脚注多证据判定 + 拼接器跨夹层续接。

用 fitz 现造最小 PDF：第 1 页正文末句未完 + 页底数字脚注（带分隔线），第 2 页首行
续句。期望输出"一个完整正文段 + 脚注在其后"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from folith import pdf_ingestion as pi  # noqa: E402

BODY = 10.0
LINES = [
    "The first officer reported the approach was stable and the crew continued",
    "toward the runway while the captain monitored the instruments closely until",
    "the airplane was about eleven seconds from impact at an",
]
NOTE = "The pilot's reaction at this point would be consistent with the lights."
TAIL = "altitude of 124 ft and a speed of 114 knots."


def _pdf(ratio: float, rule: bool = True, marker: bool = True,
         bottom_body: bool = False) -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    y = 80.0
    for line in LINES:
        page.insert_text((50, y), line, fontsize=BODY, fontname="helv")
        y += 14
    if marker:
        width = fitz.get_text_length(LINES[-1], fontname="helv", fontsize=BODY)
        page.insert_text((50 + width + 0.5, y - 14 - 3), "1", fontsize=6,
                         fontname="helv")
    if bottom_body:
        page.insert_text((50, 520), "A closing remark at the foot of the page.",
                         fontsize=BODY, fontname="helv")
    else:
        if rule:
            page.draw_line((50, 500), (130, 500), width=0.5)
        page.insert_text((50, 520), "1 " + NOTE, fontsize=BODY * ratio, fontname="helv")
    page2 = doc.new_page(width=400, height=600)
    page2.insert_text((50, 80), TAIL, fontsize=BODY, fontname="helv")
    page2.insert_text((50, 94),
                      "A new paragraph follows here and it ends properly.",
                      fontsize=BODY, fontname="helv")
    return doc.tobytes()


def _records(data: bytes, force_body_note: bool = False):
    blocks = pi.classify_blocks(pi.extract_layout_blocks(data))
    if force_body_note:
        for block in blocks:
            if block.get("role") == "footnote":
                block["role"] = "body"
    return blocks, pi.reconstruct_paragraph_records(blocks)


def _joined(records) -> bool:
    return any("eleven seconds" in r["text"] and "altitude of 124 ft" in r["text"]
               for r in records)


def test_footnote_ratios_join_paragraph():
    for ratio in (0.75, 0.8, 0.85):
        blocks, records = _records(_pdf(ratio))
        notes = [b for b in blocks if b.get("role") == "footnote"]
        assert len(notes) == 1 and notes[0]["text"].startswith("1 "), ratio
        assert _joined(records), ratio
        assert not any(NOTE in r["text"] for r in records), ratio


def test_bridge_joins_when_footnote_stays_body():
    for ratio in (0.8, 0.85):
        blocks, records = _records(_pdf(ratio), force_body_note=False)
        for block in blocks:
            if block.get("role") == "footnote":
                block["role"] = "body"
        records = pi.reconstruct_paragraph_records(blocks)
        assert _joined(records), ratio
        texts = [r["text"] for r in records]
        para = next(i for i, t in enumerate(texts) if "altitude of 124 ft" in t)
        note = next(i for i, t in enumerate(texts) if NOTE in t)
        assert para < note


def test_same_size_bottom_body_stays_body():
    blocks, _ = _records(_pdf(1.0, rule=False, marker=False, bottom_body=True))
    last = [b for b in blocks if b["page_number"] == 1][-1]
    assert last["role"] == "body"


def test_same_size_numbered_block_without_rule_stays_body():
    blocks, _ = _records(_pdf(1.0, rule=False))
    note = [b for b in blocks if b["page_number"] == 1][-1]
    assert note["role"] == "body"
