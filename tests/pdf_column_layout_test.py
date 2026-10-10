"""多栏 / 悬挂缩进版面：段落边界必须按**列**判，而不是按全文档左边距。

回归语料里的 `scenario-100p.pdf` 是「单栏、统一左边距、首行缩进」——恰好是
`reconstruct_paragraph_records` 的缩进判据唯一成立的版面。真实期刊的另两类版式
从未进入语料，而它们在同一条判据下会**逐行成段**：

* **嵌套窄栏**：摘要排在与正文列起点不同的独立栏里。全局左边距会把该栏的每一行
  都判成"段首缩进"，于是整段摘要变成与行数相同的段落（实测：11 行 → 11 段）；
* **悬挂缩进列表**：条目首行在正文左边距，续行整体右移。全局左边距把每一续行都
  判成新段落（实测：一个 8 行的条目 → 8 段）。

本模块用确定性重建的 `scenario-columns.pdf` 把两类版式都钉住，并同时钉住反向
风险：不能因为放宽缩进判据就把两段正文粘成一段，也不能把相邻栏的行接起来——
后者**两个方向**都要钉（左栏末行 → 右栏首行，以及右栏末行 → 左栏标签），因为
阅读顺序并不保证从左到右。

第三类版式是**确定性续行**（`_is_obvious_continuation`）：同段文字被版面切成
两行，而 role / 缩进 / 长度那套判据都够不着。正例与负数对照都在这里钉住——
"能合回去"与"不乱合"是同一处改动的两面。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from make_scenario_fixtures import build_pdf_columns_scenario  # noqa: E402
from folith import pdf_ingestion, source_cleanup  # noqa: E402


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


@pytest.fixture(scope="module")
def page() -> dict:
    """确定性生成多栏 fixture，返回 (blocks, paragraphs, manifest)。"""
    import tempfile

    target = Path(tempfile.mkdtemp(prefix="pdf-columns-")) / "scenario-columns.pdf"
    info = build_pdf_columns_scenario(target)
    blocks = pdf_ingestion.classify_blocks(
        pdf_ingestion.extract_layout_blocks(target.read_bytes()))
    paragraphs = pdf_ingestion.reconstruct_paragraphs(blocks)
    return {"blocks": blocks, "paragraphs": paragraphs, "manifest": info}


def test_paragraph_count_recovers_pre_column_boundaries(page):
    """段落数就是这条判据的净效应：HEAD 口径 36 段，列局部化后 22，再 20。

    20 全部来自"把被逐行拆开的段重新合回去"，没有任何一段被拆得更碎；两侧都由
    本模块其余用例逐条钉住具体形状。
    """
    assert len(page["paragraphs"]) == 20, [
        (len(item), item[:60]) for item in page["paragraphs"]]
    assert len(page["paragraphs"]) == page["manifest"]["paragraphs"]


def test_nested_column_abstract_is_one_paragraph(page):
    """窄栏摘要整段是一段，且与原文逐字相同。"""
    expected = _flat(page["manifest"]["abstract"])
    matches = [item for item in page["paragraphs"] if item == expected]
    assert len(matches) == 1, [item[:60] for item in page["paragraphs"]]
    fragments = [item for item in page["paragraphs"]
                 if item and item in expected and item != expected]
    assert not fragments, f"摘要仍在逐行成段：{fragments}"


def test_column_jump_is_not_a_continuation(page):
    """左栏最后一行不得被接进右栏摘要首行（换列的**向右**一侧）。

    那行刻意超过 40 字符且不以句末标点结尾——去掉"换列不成段"这条判据，它会
    满足合并的全部条件，把元数据粘到摘要开头。
    """
    expected = _flat(page["manifest"]["abstract"])
    long_row = page["manifest"]["meta_rows"][-1]
    assert len(long_row) > 40
    assert long_row in page["paragraphs"], "元数据行必须自成一个段落"
    glued = [item for item in page["paragraphs"]
             if item != expected and expected in item]
    assert not glued, f"摘要被并进了别的段落：{[item[:60] for item in glued]}"
    assert not [item for item in page["paragraphs"]
                if item.startswith(long_row) and len(item) > len(long_row)]


def test_leftward_column_jump_is_not_a_continuation(page):
    """右栏末行不得被接进左栏标签——换列判据必须**双向**成立。

    阅读顺序并不保证从左到右：本 fixture 里右栏框（`_COLUMN_LEFTWARD_BOX`）之后
    紧接着左栏标签。框的末行刻意不以句末收尾（且 >40 字符），标签又刚好不缩进，
    于是合并的其余条件全部满足，唯一的否决证据是**横移 160pt**。

    原判据只判右移（有符号位移 ≤ 阈值，负数恒成立），这条就会红：真实期刊实测把
    右栏的作者单位（x0=419.88）接进了左栏的 "ARTICLE DATA"（x0=54.48），拼成
    "…Zagazig University ARTICLE DATA"。

    反方向的小位移必须继续合并：首行缩进的续行会回到正文边距（-14pt），
    由 `test_first_line_indent_still_separates_body_paragraphs` 钉住。
    """
    box = page["manifest"]["leftward_box"]
    label = page["manifest"]["leftward_label"]
    assert len(box) > 40 and not box.endswith(".")
    assert box in page["paragraphs"], "右栏框应当自身成段"
    assert label in page["paragraphs"], "左栏标签必须自成一个段落"
    assert not [item for item in page["paragraphs"]
                if item != box and box in item], "右栏框被并进了别的段落"
    assert not [item for item in page["paragraphs"]
                if item != label and label in item], \
        f"左栏标签被并进了别的段落：{[item[:70] for item in page['paragraphs']]}"


def test_hanging_indent_list_items_keep_their_lines(page):
    """悬挂缩进条目：首行与右移的续行属于同一段。"""
    for item in page["manifest"]["list_items"]:
        expected = _flat(item)
        assert expected in page["paragraphs"], f"条目未合成一段：{item[:40]}"
    # 任何条目的**前半截**都不该作为独立段落存在。这里不能改用"短且以句点收尾"
    # 之类的启发式：本页的负数对照里就有合法的短段落（"Station records confirm
    # the decline."），启发式会把它们一起判成碎片。
    for item in page["manifest"]["list_items"]:
        expected = _flat(item)
        for candidate in page["paragraphs"]:
            if candidate != expected and len(candidate) >= 12:
                assert not expected.startswith(candidate), \
                    f"条目被切成碎片：{candidate[:60]}"


def test_first_line_indent_still_separates_body_paragraphs(page):
    """反向风险：放宽缩进判据不等于取消它。

    两段正文都在正文边距起排，靠首行缩进分界；若把缩进判据简单删掉，它们会被
    粘成一段。
    """
    expected = [_flat(item) for item in page["manifest"]["body_paragraphs"]]
    for text in expected:
        assert text in page["paragraphs"], f"正文段落被拆开：{text[:40]}"
    joined = [item for item in page["paragraphs"]
              if expected[0][-20:] in item and expected[1][:20] in item]
    assert not joined, "两段正文被粘成了一段"


def test_layout_furniture_is_excluded(page):
    """重复页眉与页码仍然被剔除，不进入正文。"""
    roles: dict[str, int] = {}
    for block in page["blocks"]:
        roles[block.get("role")] = roles.get(block.get("role"), 0) + 1
    assert roles.get("header") == 2, roles
    assert roles.get("page_number") == 2, roles
    header = page["manifest"]["running_header"]
    assert not [item for item in page["paragraphs"] if header in item]
    assert not [item for item in page["paragraphs"] if item.strip().isdigit()]


def test_column_reference_is_local_to_the_column(page):
    """白盒：缩进参照取的是块**自己那一列**的左边界，不是全文档左边距。

    这是本模块其余用例的机制来源——参照错了，整列都会被判成段首缩进。
    """
    text_blocks = [block for block in page["blocks"]
                   if block.get("block_type") == "text"]
    global_edge = pdf_ingestion._body_left_edge(text_blocks)
    abstract_x0 = min(
        round(float(block["x0"]), 1) for block in text_blocks
        if round(float(block["x0"]), 1) > global_edge + 50
    )
    abstract_blocks = [block for block in text_blocks
                       if round(float(block["x0"]), 1) == abstract_x0]
    assert len(abstract_blocks) > 2, "摘要应当由多行组成"
    for block in abstract_blocks:
        assert block["local_body_x0"] == abstract_x0, (
            block["block_id"], block["local_body_x0"], abstract_x0)
    assert global_edge != abstract_x0
    meta_blocks = [block for block in text_blocks
                   if block.get("text") in page["manifest"]["meta_rows"]]
    assert meta_blocks
    for block in meta_blocks:
        assert block["local_body_x0"] == global_edge


# ---------------- 确定性续行（方案 2 的提取期断行整理） ----------------


def test_wrapped_line_pairs_are_joined(page):
    """同段文字被版面切成两行时必须合回去。

    fixture 里两条正例刻意躲开原有的 role / 缩进 / 长度判据：一条首行只有 22 字符
    （长度阈值 `> 40` 够不着），一条续行缩进 20pt 且该缩进在本页唯一（解不出列
    边界，缩进判据会把它当成段首缩进）。拿掉 `_is_obvious_continuation` 两条都红。
    """
    for expected in page["manifest"]["continuation_merged"]:
        assert expected in page["paragraphs"], [
            item[:70] for item in page["paragraphs"]]


def test_differently_typeset_line_is_not_a_continuation(page):
    """真标题与它引出的正文排版不同 → 不得并成一段。

    两行的首尾字符完全满足"上一行没写完、下一行小写起首"，唯一的否决证据是
    13pt vs 9.5pt 的字号差。拿掉字号门槛这条就会红。
    """
    first, second = page["manifest"]["continuation_kept"][0]
    assert first in page["paragraphs"] and second in page["paragraphs"]
    headings = [block for block in page["blocks"]
                if block.get("text") == first and block.get("role") == "heading"]
    assert headings, "13pt 的那一行应当被认成标题"
    assert not [item for item in page["paragraphs"]
                if first in item and second in item]


def test_capitalised_next_line_is_not_a_continuation(page):
    """同栏、同排版，但右行以大写起首 → 不是续行。"""
    first, second = page["manifest"]["continuation_kept"][1]
    assert first in page["paragraphs"] and second in page["paragraphs"]
    assert not [item for item in page["paragraphs"]
                if first in item and second in item]


def test_extraction_stage_subsumes_the_deterministic_joins(page):
    """方案 2 的目的是"把确定性断行整理提到提取阶段"，这条是它的可执行定义。

    提取完成之后，`source_cleanup` 的确定性层在同一份语料上**只应剩下我们故意
    不合并的那些位置**（真标题与它引出的正文排版不同）。同一判据在真实期刊 PDF
    上原本能标出 10~11 处真正的断行续行——那些现在必须在提取阶段就合掉。

    注意这条**不能**写成"确定性层不再标出任何东西"：`obvious_line_join` 没有排版
    门槛，它永远会想把标题和下一行并起来。
    """
    _, _, merges, _ = source_cleanup._deterministic_prepare(page["paragraphs"])
    refused = {tuple(pair) for pair in page["manifest"]["continuation_kept"]}
    for item in merges:
        first, second = item["input_indices"]
        pair = (page["paragraphs"][first], page["paragraphs"][second])
        assert pair in refused, f"仍有该合未合的续行：{pair}"


def test_cjk_full_width_terminator_is_not_an_open_line():
    """"未结句"判据不能把全角句末当成"没写完"。

    `pdf_ingestion.SENTENCE_TERMINAL` 是给拉丁/阿拉伯系定的，里面没有 `。！？`。
    照搬 `source_cleanup.obvious_line_join` 的口径（只要求 `not 句末`）会让**中文
    段落被逐段粘起来**——中文每段恰恰以全角句号收尾。
    """
    assert pdf_ingestion._ends_open("这一段已经写完了。") is False
    assert pdf_ingestion._ends_open("这一段还没写完") is True
    assert pdf_ingestion._ends_open("这一行在短语中间被切断，") is True
    # 逗号收尾可以被收：本路径只做拼接，不像 source_cleanup 那样会让模型改写这一行
    # （那才会删掉真标点）。
    assert pdf_ingestion._ends_open("A line cut mid phrase,") is True


def test_continuation_predicate_edges():
    """`_is_obvious_continuation` 的边界：长度上限、字号容忍度、右行大小写。"""
    def record(text, size=10.0):
        return {"text": text, "font_size": size}

    predicate = pdf_ingestion._is_obvious_continuation
    assert predicate(record("The canopy record is"), record("kept here."))
    assert not predicate(record("The canopy record is"), record("Kept here.")), \
        "右行大写起首不是续行"
    assert not predicate(record("Done."), record("next line starts lower"))
    too_long = "a" * pdf_ingestion.CONTINUATION_MAX_PREV_CHARS
    assert not predicate(record(too_long), record("and it continues"))
    assert not predicate(record("The canopy record is"),
                         record("kept here.", size=11.0)), "字号差超出容忍度"
    within = 10.0 * (1 + pdf_ingestion.CONTINUATION_SIZE_TOLERANCE)
    assert predicate(record("The canopy record is"),
                     record("kept here.", size=within))
