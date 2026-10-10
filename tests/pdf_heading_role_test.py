"""段落角色：**个别** span 的加粗不得把整块判成标题。

`classify_blocks` 的 heading 判据原本只要求 bold 位（`font_flags & 16`），而
`font_flags` 是 span flags 的 `max()` —— **一个**加粗 span 就够。真实文件里这会逐行
误判：本仓实测一篇期刊的摘要在窄栏里排 10.746pt（正文 11.7pt，0.92×），字体回退在
个别字符上换成了带 bold 位的替代字体，于是相邻行的 `font_flags` 在 4 / 20 之间交替、
字号完全相同，整段摘要被逐行判成 `heading`。

这里用**实测到的形状**直接构造版面块（正文 11.7pt @ x0=51.10，窄栏 10.746pt @
x0=195.24），所以断言完全确定，不依赖 PyMuPDF 把段落切成几个 block、行距落在哪一侧。
正例之外有三条负数对照，证明加粗分支没有被整体关掉、字号分支也没被改动。

反向验证（手动）：注入 `tmp/eco/kill_bold_guard.py`（把判据还原成"只看 bold 位"）
后本模块应当变红。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from folith import pdf_ingestion  # noqa: E402

#: 实测值（任务 6819bee1568187b0 / An+Eco-Translatology+Approach.pdf）
BODY_SIZE = 11.7
BODY_X0 = 51.10
NARROW_SIZE = 10.746          # 0.92 × 正文：窄栏摘要/引文
NARROW_X0 = 195.24
FLAG_PLAIN = 4                # serifed
FLAG_BOLD_FALLBACK = 20       # serifed + bold：字体回退在个别字符上留下的 bold 位

#: 四段正文。都以句点收尾，因此**彼此不会合并**——这条测试只关心角色与窄栏那一对。
_BODY_TEXTS = (
    "The station archives every measurement taken by the watcher.",
    "The regional office reviews the archive each season.",
    "A further survey recorded the same decline in the upper storey.",
    "The irrigation schedule was adopted after the second survey.",
)

#: 窄栏里的两行。第一行以破折号收尾（既非句末、也不是"明显没写完"），第二行带
#: 字体回退留下的 bold 位——这正是真实文件里被误判成标题的那一行。
_NARROW_FIRST = (
    "The quoted passage continues without a terminator and then breaks off —"
)
_NARROW_SECOND = "the remainder of the quoted passage closes the citation here."


def _line(text, *, size, flags, x0, y0):
    return {
        "line_id": "p1-l",
        "text": text,
        "bbox": {"x0": x0, "y0": y0, "x1": x0 + 300.0, "y1": y0 + size,
                 "width": 300.0, "height": size},
        "x0": x0,
        "y0": y0,
        "x1": x0 + 300.0,
        "y1": y0 + size,
        "font_size": size,
        "font_flags": flags,
        "font_style": [],
        "marks": [],
    }


def _text_block(text, *, size, flags, x0, y0, order, bold_share=None):
    """构造一个与 `extract_layout_blocks` 同形状的单行文本块。"""
    block = {
        "block_id": f"p1-b{order}",
        "block_type": "text",
        "role": "unknown",
        "page_number": 1,
        "reading_order": order,
        "x0": x0,
        "y0": y0,
        "x1": x0 + 300.0,
        "y1": y0 + size,
        "bbox": {"x0": x0, "y0": y0, "x1": x0 + 300.0, "y1": y0 + size,
                 "width": 300.0, "height": size},
        "font_size": size,
        "font_flags": flags,
        "font_style": [],
        "relative_width": 300.0 / 595.0,
        "page_width": 595.0,
        "page_height": 842.0,
        "text": text,
        "lines": [_line(text, size=size, flags=flags, x0=x0, y0=y0)],
        "marks": [],
    }
    if bold_share is not None:
        block["bold_share"] = bold_share
    return block


def _raw_blocks(narrow_second_flags=FLAG_BOLD_FALLBACK,
                narrow_second_share=0.12):
    """正文四块 + 窄栏两块。

    窄栏那两块连成 ≥2 的同 x0 连续 run，这样 `_local_left_edge` 能解出 195.24，
    `is_indented` 才不会把"列起点"当成"段首缩进"。
    """
    blocks = []
    for index, text in enumerate(_BODY_TEXTS):
        blocks.append(_text_block(
            text, size=BODY_SIZE, flags=FLAG_PLAIN, x0=BODY_X0,
            y0=200.0 + 14.0 * index, order=index))
    blocks.append(_text_block(
        _NARROW_FIRST, size=NARROW_SIZE, flags=FLAG_PLAIN, x0=NARROW_X0,
        y0=300.0, order=4, bold_share=0.0))
    blocks.append(_text_block(
        _NARROW_SECOND, size=NARROW_SIZE, flags=narrow_second_flags,
        x0=NARROW_X0, y0=314.0, order=5, bold_share=narrow_second_share))
    return blocks


def _classified(**kwargs):
    return pdf_ingestion.classify_blocks(_raw_blocks(**kwargs))


def test_bold_fallback_span_does_not_promote_a_quoted_line():
    """字体回退点亮了 bold 位的那一行仍属于窄栏引文，不是标题。"""
    blocks = _classified()
    target = next(block for block in blocks if block["block_id"] == "p1-b5")
    # 机制：判据确实被压到了——bold 位是亮的，而加粗只覆盖少数字符。
    assert target["font_flags"] & pdf_ingestion.FONT_FLAG_BOLD
    assert target["bold_share"] < pdf_ingestion.BOLD_DOMINANCE_MIN
    assert target["role"] == "quote", target["role"]
    assert "heading" not in [block["role"] for block in blocks], [
        (block["block_id"], block["role"]) for block in blocks]


def test_mislabelled_heading_no_longer_blocks_the_merge():
    """误判成 heading 会挡住第一合并路径（role 必须同属 JOINABLE_ROLES）。

    第一行以破折号收尾：第二合并路径（`_is_obvious_continuation`）明确拒绝它，
    所以这两行能否合回一段**只**取决于角色是否被判对。
    """
    records = pdf_ingestion.reconstruct_paragraph_records(_classified())
    texts = [record["text"] for record in records]
    assert len(texts) == len(_BODY_TEXTS) + 1, texts
    assert pdf_ingestion._join_text(_NARROW_FIRST, _NARROW_SECOND) in texts, texts


def test_wholly_bold_line_at_body_size_is_still_a_heading():
    """负数对照：加粗分支没有被整体关掉。

    整块加粗、字号等于正文的短行仍然是标题——这是修改前就有的行为。
    """
    blocks = _raw_blocks()
    blocks.append(_text_block(
        "Restoration findings", size=BODY_SIZE, flags=FLAG_BOLD_FALLBACK,
        x0=BODY_X0, y0=400.0, order=9, bold_share=1.0))
    classified = pdf_ingestion.classify_blocks(blocks)
    target = next(item for item in classified if item["block_id"] == "p1-b9")
    assert target["role"] == "heading", target["role"]


def test_size_branch_is_untouched():
    """负数对照：字号分支（≥1.18× 正文）逐字未改。"""
    blocks = _raw_blocks()
    blocks.append(_text_block(
        "Restoration findings", size=BODY_SIZE * 1.18, flags=FLAG_PLAIN,
        x0=BODY_X0, y0=400.0, order=9))
    classified = pdf_ingestion.classify_blocks(blocks)
    target = next(item for item in classified if item["block_id"] == "p1-b9")
    assert target["role"] == "heading", target["role"]


def test_bold_evidence_truth_table():
    """`_bold_heading_evidence` 的两条门槛与历史兼容行为。"""
    bold = pdf_ingestion.FONT_FLAG_BOLD
    evidence = pdf_ingestion._bold_heading_evidence
    # 个别 span 加粗（0.12）不算"整块的排版"
    assert evidence({"font_flags": bold, "bold_share": 0.12},
                    BODY_SIZE, BODY_SIZE) is False
    # 过半加粗
    assert evidence({"font_flags": bold, "bold_share": 0.5},
                    BODY_SIZE, BODY_SIZE) is True
    # 整块加粗，但比正文小
    assert evidence({"font_flags": bold, "bold_share": 1.0},
                    NARROW_SIZE, BODY_SIZE) is False
    # 恰好在门槛上：0.98 × 正文
    assert evidence({"font_flags": bold, "bold_share": 1.0},
                    BODY_SIZE * pdf_ingestion.HEADING_BOLD_MIN_SIZE_RATIO,
                    BODY_SIZE) is True
    # 没有 bold_share 的块（手工构造 / 历史状态）保持旧行为：只看 flags 位
    assert evidence({"font_flags": bold}, BODY_SIZE, BODY_SIZE) is True
    # 没有 bold 位
    assert evidence({"font_flags": FLAG_PLAIN, "bold_share": 1.0},
                    BODY_SIZE, BODY_SIZE) is False


def test_bold_share_counts_characters_not_spans():
    """`_bold_share` 按**字符**加权：一个短加粗 span 不该点亮一整行。"""
    spans = [
        {"text": "The quoted passage continues and ", "flags": FLAG_PLAIN},
        {"text": "Red", "flags": FLAG_BOLD_FALLBACK},
        {"text": " closes the citation here.", "flags": FLAG_PLAIN},
    ]
    share = pdf_ingestion._bold_share(spans)
    assert 0.0 < share < pdf_ingestion.BOLD_DOMINANCE_MIN, share
    assert pdf_ingestion._bold_share(
        [{"text": "Red", "flags": FLAG_BOLD_FALLBACK}]) == 1.0
    assert pdf_ingestion._bold_share([]) == 0.0
    # 纯空白 span 不参与加权（否则空白会把占比算歪）
    assert pdf_ingestion._bold_share(
        [{"text": "  ", "flags": FLAG_PLAIN},
         {"text": "Red", "flags": FLAG_BOLD_FALLBACK}]) == 1.0
