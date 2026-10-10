"""生成 Folith 三个端到端验收场景的源文档（内容确定性、可重复）。

蓝图 `docs/foliothread-agentic-native-blueprint.md` §8 把发布进度定义为三个
端到端场景，而不是模块数量：

| 场景 | 必须证明 |
| --- | --- |
| 20 页 DOCX | 导入、术语准备、翻译、审校、双语交付可完成 |
| 100 页 PDF | 中断恢复、章节上下文、受影响范围重建可用 |
| 术语密集文档 | 术语确认、TM 复用、独立审校和交付清单可信 |

本脚本把这三个场景的**源文档**变成可重复生成的产物，从而让
`tests/scenario_gate_test.py` 能在离线、无 provider 的情况下运行验收。
仓库不提交二进制 fixture：文档每次由本脚本按固定内容重建。

**确定性边界**：生成过程不使用随机数，产出的**文本内容**逐段一致
（manifest 中每个场景的 `content_sha256` 是内容指纹，可用于验证未漂移）。
但**容器字节不保证一致**：DOCX 的 zip 条目带写入时间戳，PDF 内部有文档
ID 与交叉引用偏移。因此不要用文件 sha256 判断 fixture 是否漂移，
请使用 `content_sha256`。

用法：

    python scripts/make_scenario_fixtures.py --out tmp/scenarios
    python scripts/make_scenario_fixtures.py --out tmp/scenarios --only pdf

生成物：

    scenario-20p.docx            20 页级结构化 DOCX（标题层级 + 正文 + 引用/URL）
    scenario-100p.pdf            100 页 PDF（重复页眉、页码页脚、跨行连字符）
    scenario-terms.docx          术语密集 DOCX
    scenario-terms.glossary.json 与术语密集文档配套的锁定术语表
    fixtures-manifest.json       每个场景的预期结构，供验收断言读取
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:  # PyMuPDF ≥1.24 同时提供新旧两个模块名
    import pymupdf as fitz
except ImportError:  # pragma: no cover - 兼容旧版 PyMuPDF
    import fitz

from docx import Document
from docx.shared import Pt


def content_sha256(lines: list[str]) -> str:
    """内容指纹：只覆盖文本内容，不含容器字节（zip 时间戳 / PDF ID）。"""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ================= 确定性语料 =================

# 每个场景的正文由固定句子池按固定顺序组合，不使用随机数：
# 文本内容在任何机器、任何时间都一致（容器字节不保证，见模块 docstring）。
_TOPICS = (
    ("生态恢复与演替", "Ecological restoration and succession"),
    ("土壤碳循环", "Soil carbon cycling"),
    ("林冠结构与光竞争", "Canopy structure and light competition"),
    ("菌根网络与养分转运", "Mycorrhizal networks and nutrient transfer"),
    ("干扰机制与恢复力", "Disturbance regimes and resilience"),
    ("流域尺度水文响应", "Catchment-scale hydrological response"),
)

_SENTENCES = (
    "Recent work on {topic} has shifted from describing static states to "
    "modelling the transitions between them.",
    "Field measurements were collected across {plots} plots during three "
    "consecutive growing seasons.",
    "The observed response was non-linear, which complicates the use of a "
    "single recovery rate.",
    "Earlier studies reported a comparable pattern, although their sampling "
    "design did not isolate the same mechanism.",
    "We therefore treat {topic} as a coupled process rather than a sequence "
    "of independent stages.",
    "We recorded the canopy structure and the understorey composition "
    "separately at each visit.",
    "Where the disturbance regime changed, the trajectories diverged within "
    "two seasons.",
    "These results are consistent with the hypothesis that legacy effects "
    "persist longer than the perturbation itself.",
    "Quantifying that lag requires either longer records or a stronger "
    "assumption about the underlying process.",
)

# 确定性检查会命中的保留项：URL、引用标注、占位符、DOI、邮箱。
# 全部落在 `core.PRESERVE_RE`（core.py:655）覆盖范围内，因此翻译后必须原样回填。
_RESERVED_TOKENS = (
    "See https://example.com/folio/thread for the full protocol.",
    "The framework follows the earlier treatment [12] and its corrigendum [13].",
    "Model output was written to {{artifact_path}} before aggregation.",
    "The dataset is archived under doi 10.1234/folio.2026.001 for inspection.",
    "Correspondence should be addressed to field-team@example.com directly.",
)

_TERM_SENTENCES = (
    "The {term} determines how quickly the system returns to its prior state.",
    "We measured {term} at each site and compared it with the regional mean.",
    "A change in {term} can mask a simultaneous change in the other "
    "components.",
    "Independent estimates of {term} were within the reported uncertainty.",
    "The {term} should not be conflated with the short-term response.",
)

# 术语密集场景的锁定术语（source -> 首选译名 / 禁止译名）
_LOCKED_TERMS = (
    ("ecological succession", "生态演替", ("生态继承",)),
    ("canopy closure", "林冠郁闭", ("冠层关闭",)),
    ("mycorrhizal network", "菌根网络", ("菌根网路",)),
    ("soil respiration", "土壤呼吸", ("土壤呼吸作用率",)),
    ("disturbance regime", "干扰机制", ("扰动政权",)),
)

# 保留专名：用于术语密集场景的 `preserve` 行为（该词不在 PRESERVE_RE 内，
# 因此会按"源语残留"被确定性检查记录为 actionable —— 这是预期行为，由
# tests/scenario_gate_test.py 显式断言，而不是让它在其它场景里变成噪声）。
_PRESERVE_SENTENCE = (
    "The instrument, a LiCOR-7500 analyser, was calibrated before each campaign."
)


# ================= 文本装配 =================


def _body_paragraphs(count: int, *, topic_offset: int = 0) -> list[str]:
    """按固定顺序生成 count 段正文，段落长度在 40–90 词之间。"""
    paragraphs: list[str] = []
    index = 0
    while len(paragraphs) < count:
        title, english = _TOPICS[(index + topic_offset) % len(_TOPICS)]
        sentences = []
        for offset in range(4):
            template = _SENTENCES[(index * 4 + offset) % len(_SENTENCES)]
            sentences.append(template.format(
                topic=english, plots=18 + (index * 7 + offset * 3) % 60))
        if index % 5 == 4:
            # 独立轮转：index % 5 == 4 是固定余数，用 index // 5 才不会退化成同一个 token。
            sentences.append(_RESERVED_TOKENS[(index // 5) % len(_RESERVED_TOKENS)])
        paragraphs.append(" ".join(sentences))
        index += 1
    return paragraphs


def _headings(count: int, *, topic_offset: int = 0) -> list[tuple[str, str]]:
    """生成 (中文标题, 英文标题) 序列，供标题层级与章节划分使用。"""
    return [
        _TOPICS[(index + topic_offset) % len(_TOPICS)] for index in range(count)
    ]


def _section_paragraphs(paragraphs: list[str], section_count: int) -> list[list[str]]:
    """把段落均分到若干个章节，保持顺序。"""
    sections: list[list[str]] = [[] for _ in range(section_count)]
    for index, paragraph in enumerate(paragraphs):
        sections[index % section_count].append(paragraph)
    return sections


# ================= 场景一：20 页 DOCX =================


def build_docx_scenario(
    path: Path, *, sections: int = 8, paragraphs: int = 175,
) -> dict:
    """生成结构化 DOCX：8 个二级标题章节、175 段正文。

    175 段 × 约 63 词 ≈ 11,000 词，在 A4 / 11pt 下约 20 页，与蓝图场景一致。
    """
    headings = _headings(sections)
    body = _body_paragraphs(paragraphs)
    grouped = _section_paragraphs(body, sections)

    document = Document()
    document.add_heading("长文档验收场景：生态恢复的过程与机制", level=0)
    document.add_paragraph(
        "本文件由 scripts/make_scenario_fixtures.py 确定性生成，"
        "用于 Folith 的端到端验收，不包含任何真实研究数据。"
    )
    for index, (title_cn, title_en) in enumerate(headings):
        document.add_heading(f"{index + 1}. {title_cn} / {title_en}", level=1)
        for paragraph in grouped[index]:
            document.add_paragraph(paragraph)
    for section in document.sections:
        for run in section.footer.paragraphs[0].runs or []:
            run.font.size = Pt(9)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))

    words = sum(len(item.split()) for item in body)
    return {
        "kind": "docx",
        "content_sha256": content_sha256(
            [f"{index + 1}. {title_cn} / {title_en}"
             for index, (title_cn, title_en) in enumerate(headings)] + body),
        "path": path.name,
        "sections": sections,
        "paragraphs": paragraphs,
        "words": words,
        "approx_pages": round(words / 550, 1),
        "reserved_tokens": list(_RESERVED_TOKENS),
        "preserve_sentence": _PRESERVE_SENTENCE,
    }


# ================= 场景二：100 页 PDF =================

_RUNNING_HEADER = "Folith Scenario Fixture — Ecological Restoration"
_PDF_BODY_X0 = 72.0          # 正文左边界
_PDF_INDENT = 14.0           # 首行缩进：运行时的段落边界判据
_PDF_LINE_WIDTH = 92         # 每行最大字符数
_PDF_LINE_HEIGHT = 12.5
_PDF_PARAGRAPH_GAP = 6.0


def _wrap_line(text: str, width: int = _PDF_LINE_WIDTH) -> list[str]:
    """按单词边界折行；行尾不切断单词（与真实排版一致）。"""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_pdf_scenario(
    path: Path, *, pages: int = 100, paragraphs_per_page: int = 6,
) -> dict:
    """生成 100 页 PDF，带重复页眉、页码页脚和一处跨行连字符。

    版面刻意采用真实排版惯例，使运行时能恢复段落边界：

    - 每段首行缩进 `_PDF_INDENT`：`reconstruct_paragraph_records` 据此判定
      新段落开始（`pdf_ingestion.reconstruct_paragraph_records`，参照取该块所属
      列的左边界）；
    - 行在单词边界折行，因此跨行拼接应当插入空格而不是粘连；
    - 固定位置注入一处 `can-` / `opy` 断词，验证去连字符还原
      （`pdf_ingestion._join_text`）；
    - 重复页眉与页码页脚用于验证 `classify_blocks` 的版面噪声剔除。
    """
    paragraphs = _body_paragraphs(pages * paragraphs_per_page)
    hyphen_target = 5 * paragraphs_per_page  # 第 6 页首段，位置可预测

    document = fitz.open()
    cursor = 0
    for page_index in range(pages):
        page = document.new_page(width=595, height=842)  # A4
        page.insert_text((_PDF_BODY_X0, 60), _RUNNING_HEADER, fontsize=8)
        page.insert_text((540, 812), str(page_index + 1), fontsize=8)

        y = 92.0
        for _ in range(paragraphs_per_page):
            if cursor >= len(paragraphs):
                break
            lines = _wrap_line(paragraphs[cursor])
            if cursor == hyphen_target:
                lines = _inject_hyphenation(lines, "canopy")
            for line_index, line in enumerate(lines):
                x = _PDF_BODY_X0 + (_PDF_INDENT if line_index == 0 else 0.0)
                page.insert_text((x, y), line, fontsize=9.5)
                y += _PDF_LINE_HEIGHT
            y += _PDF_PARAGRAPH_GAP
            cursor += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    document.close()

    words = sum(len(item.split()) for item in paragraphs)
    return {
        "kind": "pdf",
        "content_sha256": content_sha256(
            [p.replace("canopy", "can-\nopy", 1) if index == hyphen_target else p
             for index, p in enumerate(paragraphs)]),
        "path": path.name,
        "pages": pages,
        "paragraphs": len(paragraphs),
        "words": words,
        "running_header": _RUNNING_HEADER,
        "hyphenation": {
            "page": hyphen_target // paragraphs_per_page + 1,
            "word": "canopy",
            "joined": "canopy",
        },
    }


# ================= 场景四：多栏 + 悬挂缩进 PDF =================
#
# 现有 pdf_100p 场景是「单栏、统一左边距、首行缩进」——恰好是
# `reconstruct_paragraph_records` 里缩进判据唯一成立的版面，所以嵌套窄栏与悬挂
# 缩进这两类真实期刊排版从未进入回归语料：全局左边距会把它们的每一行都判成
# 「段首缩进」，于是逐行成段。本场景按真实版式重建这两种情形。
#
# 第 2 页末尾再加第三类：**确定性续行**——同段文字被版面切成两行，而
# role / 缩进 / 长度那套判据都够不着。正例之外还有两条负数对照（排版不同、
# 右行大写起首），因为"能合回去"和"不乱合"是同一处改动的两面。

_COLUMN_BODY_X0 = 72.0
_COLUMN_INDENT = 14.0            # 首行缩进与列表续行缩进共用同一量级
_COLUMN_BODY_FONT = 9.5
#: 行距刻意取字号（9.5pt）的 ~1.6 倍，与真实期刊一致（实测量：11.7pt 正文配
#: ~20pt 行距）。这不只是"好看"：PyMuPDF 只在行距接近字号时把连续行并成一个
#: block，宽松行距下一行一个 block——正是缺陷在真实文件里表现出的形状。收紧到
#: 12.5 会把整个摘要并成一个 block，反而掩盖被修的路径。
_COLUMN_LINE_HEIGHT = 15.0
_COLUMN_META_LABEL = "ARTICLE DATA"
_COLUMN_META_ROWS = (
    "Received: 01 January 2024",
    "Accepted: 20 February 2024",
    "Volume: 4",
    # 刻意超过 40 字符：它是唯一能触发"左栏末行接进右栏首行"误合并的行，
    # 让跨列不合并这条判据在语料里被真正压到。
    "Corresponding author: canopy@example.org, Department of Ecology",
)
_COLUMN_ABSTRACT_X0 = 232.0
_COLUMN_ABSTRACT_WIDTH = 54
_COLUMN_ABSTRACT = (
    "The canopy records show that the watcher measured leaf area across three "
    "growing seasons and reported a consistent decline in the upper storey of "
    "the restored plot, which the following section relates to the irrigation "
    "schedule that the station adopted after the second survey and to the "
    "archived correspondence with the district team, whose own measurements "
    "were taken with a different instrument and a different sampling interval."
)

#: 「左移换列」：右栏这个框的末行不以句末收尾，紧随其后的左栏标签又满足合并的
#: 其余全部条件——唯一该拦下它的证据是**横移 160pt**。跨列判据原先只判右移
#: （位移量取有符号值，负数恒成立），于是右栏末行会被接进左栏标签（真实期刊实测：
#: x0 419.88 → 54.48，拼成 "…Zagazig University ARTICLE DATA"）。
_COLUMN_LEFTWARD_BOX = (
    "Archived correspondence with the district team is filed under the",
    "restoration survey and the instrument log",
)
_COLUMN_LEFTWARD_LABEL = "SUBJECT TERMS"
_COLUMN_BODY_PARAGRAPHS = (
    "The station archives every measurement taken by the watcher, and the "
    "archive is the basis on which the regional office reviews the restoration "
    "programme each season, so that the recorded decline can be checked "
    "against the irrigation schedule and against the earlier survey.",
    "A second paragraph continues the report and keeps its own boundary, so a "
    "judgement that simply abandons the indent test would fuse it with the "
    "paragraph above and be caught here rather than in production, where the "
    "damage is a paragraph that mixes two topics.",
)
_COLUMN_LIST_INTRO = "There are Four principles under review:"
_COLUMN_LIST_ITEMS = (
    ("a", "First principle of the framework: the watcher records the canopy "
          "height at every station and compares it with the archived leaf area "
          "index of the plot, using the same instrument and the same sampling "
          "interval that the earlier survey used, so that the two records can "
          "be compared without a further correction."),
    ("b", "Second principle of the framework: the station reports the "
          "irrigation schedule to the regional office and archives the "
          "correspondence with the district team, together with the instrument "
          "log and the sampling notes, so that a later review can reconstruct "
          "the conditions under which each measurement was taken."),
)

#: 「确定性续行」——方案 2 的提取期断行整理——的四类形状。
#:
#: 前两条**必须**合成一段，且都刻意躲开原有那条 role/缩进/长度判据：
#: 第一条首行只有 22 字符（长度阈值 `> 40` 够不着）；第二条续行缩进 20pt，
#: 而 20pt 是本页**唯一**的缩进行 → 解不出列边界，缩进判据会把它当成段首缩进。
_COLUMN_CONTINUATION_MERGED = (
    ("The station archive is", "kept in the regional office.", 0.0),
    ("The regional office reviews the", "archived correspondence each season.",
     20.0),
)
_COLUMN_CONTINUATION_HEADING_SIZE = 13.0
#: 后两条**必须**保持分开，分别压住"排版一致"与"右行小写起首"这两条门槛。
_COLUMN_CONTINUATION_KEPT = (
    # 真标题与它引出的正文排版不同（13pt vs 9.5pt）→ 不得合并。
    ("Restoration findings", _COLUMN_CONTINUATION_HEADING_SIZE,
     "the watcher recorded the decline in the upper storey."),
    # 同栏、同排版，但右行以大写起首 → 不是续行。
    ("The archived correspondence", _COLUMN_BODY_FONT,
     "Station records confirm the decline."),
)


def build_pdf_columns_scenario(path: Path) -> dict:
    """生成「多栏前置区 + 悬挂缩进列表」PDF，页数固定为 2。

    版面（与真实期刊一致）：

    - 第 1 页上半是双栏前置区：左栏 `_COLUMN_META_ROWS`（列起点 = 正文左边距），
      右栏摘要（独立列起点 `_COLUMN_ABSTRACT_X0`，整段是一段）；
    - 摘要之后、正文之前还有一对**左移换列**：右栏框（`_COLUMN_LEFTWARD_BOX`）
      的末行不以句末收尾，紧接着阅读顺序拐回左栏标签
      （`_COLUMN_LEFTWARD_LABEL`）。这是"换列不成段"判据的向左一侧；
    - 第 1 页下半是通栏正文，每段首行缩进 `_COLUMN_INDENT`；
    - 第 2 页是悬挂缩进列表：条目首行在正文左边距，**续行缩进**到
      `_COLUMN_BODY_X0 + _COLUMN_INDENT`；
    - 第 2 页末尾还有「确定性续行」四例：前两例是**必须**合成的断行续行，
      后两例是**必须**保持分开的负数对照（见 `_COLUMN_CONTINUATION_*`）。
    """
    document = fitz.open()
    font = _COLUMN_BODY_FONT
    line_height = _COLUMN_LINE_HEIGHT

    page = document.new_page(width=595, height=842)  # A4
    page.insert_text((_COLUMN_BODY_X0, 60), _RUNNING_HEADER, fontsize=8)
    page.insert_text((540, 812), "1", fontsize=8)
    page.insert_text((_COLUMN_BODY_X0, 100),
                     "Column Layout Fixture: Restoration Report", fontsize=13)
    page.insert_text((_COLUMN_BODY_X0, 150), _COLUMN_META_LABEL, fontsize=font)
    row_y = 165.0
    for row in _COLUMN_META_ROWS:
        page.insert_text((_COLUMN_BODY_X0, row_y), row, fontsize=font)
        row_y += line_height
    abstract_lines = _wrap_line(_COLUMN_ABSTRACT, _COLUMN_ABSTRACT_WIDTH)
    abstract_y = 150.0
    for line in abstract_lines:
        page.insert_text((_COLUMN_ABSTRACT_X0, abstract_y), line, fontsize=font)
        abstract_y += line_height
    # 「左移换列」（见 `_COLUMN_LEFTWARD_BOX`）：右栏的框之后，阅读顺序拐回左栏。
    # 框的末行不以句末收尾、标签又刚好不缩进，所以合并的其余条件全部满足——只剩
    # "横移 160pt"这一条能拦下它。插入顺序即 reading_order，故这对必须紧跟在摘要
    # 之后、通栏正文之前。
    leftward_y = abstract_y
    for line in _COLUMN_LEFTWARD_BOX:
        page.insert_text((_COLUMN_ABSTRACT_X0, leftward_y), line, fontsize=font)
        leftward_y += line_height
    page.insert_text((_COLUMN_BODY_X0, leftward_y + line_height),
                     _COLUMN_LEFTWARD_LABEL, fontsize=font)
    body_y = max(row_y, leftward_y + line_height * 2) + line_height * 2
    for paragraph in _COLUMN_BODY_PARAGRAPHS:
        for index, line in enumerate(_wrap_line(paragraph)):
            x = _COLUMN_BODY_X0 + (_COLUMN_INDENT if index == 0 else 0.0)
            page.insert_text((x, body_y), line, fontsize=font)
            body_y += line_height
        body_y += line_height

    page = document.new_page(width=595, height=842)
    page.insert_text((_COLUMN_BODY_X0, 60), _RUNNING_HEADER, fontsize=8)
    page.insert_text((540, 812), "2", fontsize=8)
    list_y = 100.0
    page.insert_text((_COLUMN_BODY_X0, list_y), _COLUMN_LIST_INTRO, fontsize=font)
    list_y += line_height * 1.5
    for marker, sentence in _COLUMN_LIST_ITEMS:
        for index, line in enumerate(_wrap_line(f"{marker}. {sentence}")):
            # 悬挂缩进：首行在正文左边距，续行整体右移。
            x = _COLUMN_BODY_X0 + (_COLUMN_INDENT if index else 0.0)
            page.insert_text((x, list_y), line, fontsize=font)
            list_y += line_height
        list_y += line_height
    for first, second, indent in _COLUMN_CONTINUATION_MERGED:
        page.insert_text((_COLUMN_BODY_X0, list_y), first, fontsize=font)
        list_y += line_height
        page.insert_text((_COLUMN_BODY_X0 + indent, list_y), second,
                         fontsize=font)
        list_y += line_height * 1.6
    for first, size, second in _COLUMN_CONTINUATION_KEPT:
        page.insert_text((_COLUMN_BODY_X0, list_y), first, fontsize=size)
        list_y += line_height
        page.insert_text((_COLUMN_BODY_X0, list_y), second, fontsize=font)
        list_y += line_height * 1.6

    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    document.close()

    return {
        "kind": "pdf-columns",
        "content_sha256": content_sha256(
            [_COLUMN_ABSTRACT] + list(_COLUMN_BODY_PARAGRAPHS)
            + [_COLUMN_LIST_INTRO]
            + [f"{marker}. {sentence}" for marker, sentence in _COLUMN_LIST_ITEMS]
            + [first for first, _second, _indent in _COLUMN_CONTINUATION_MERGED]
            + [second for _first, second, _indent in _COLUMN_CONTINUATION_MERGED]
            + [first for first, _size, _second in _COLUMN_CONTINUATION_KEPT]
            + [second for _first, _size, second in _COLUMN_CONTINUATION_KEPT]
            + list(_COLUMN_LEFTWARD_BOX) + [_COLUMN_LEFTWARD_LABEL]),
        "path": path.name,
        "pages": 2,
        # 期望的**恢复**段落数（不是"生成的行数"）：本场景的价值恰恰在于它与行数
        # 不等——HEAD 口径会得到 36，只做列局部化得 22，再加确定性续行才是 20。
        # 逐条形状由 tests/pdf_column_layout_test.py 钉住。
        "paragraphs": 20,
        "running_header": _RUNNING_HEADER,
        "abstract": _COLUMN_ABSTRACT,
        "meta_rows": list(_COLUMN_META_ROWS),
        "body_paragraphs": list(_COLUMN_BODY_PARAGRAPHS),
        "list_intro": _COLUMN_LIST_INTRO,
        "list_items": [f"{marker}. {sentence}"
                       for marker, sentence in _COLUMN_LIST_ITEMS],
        "continuation_merged": [f"{first} {second}"
                                for first, second, _indent
                                in _COLUMN_CONTINUATION_MERGED],
        "continuation_kept": [[first, second] for first, _size, second
                              in _COLUMN_CONTINUATION_KEPT],
        "leftward_box": " ".join(_COLUMN_LEFTWARD_BOX),
        "leftward_label": _COLUMN_LEFTWARD_LABEL,
    }


def _inject_hyphenation(lines: list[str], word: str) -> list[str]:
    """把包含 `word` 的行拆成 `…can-` / `opy…` 两行，验证去连字符还原。"""
    for index, line in enumerate(lines):
        if word in line.lower():
            position = line.lower().index(word)
            head = line[:position] + word[:3] + "-"   # "can-"
            tail = line[position + 3:]                # "opy …"
            return lines[:index] + [head.rstrip(), tail.lstrip()] + lines[index + 1:]
    return lines


# ================= 场景三：术语密集 DOCX =================


def build_terminology_scenario(
    docx_path: Path, glossary_path: Path, *, paragraphs: int = 90,
) -> dict:
    """生成术语密集 DOCX + 配套锁定术语表。

    每个术语反复出现在多个段落中，用于验证术语注入范围、TM 复用和
    独立审校的术语一致性发现。
    """
    document = Document()
    document.add_heading("术语密集验收场景", level=0)
    document.add_paragraph(
        "本文件由脚本确定性生成，术语密度远高于普通长文档。"
    )

    produced: list[str] = []
    for index in range(paragraphs):
        term, target, forbidden = _LOCKED_TERMS[index % len(_LOCKED_TERMS)]
        template = _TERM_SENTENCES[(index // len(_LOCKED_TERMS)) % len(_TERM_SENTENCES)]
        sentence = template.format(term=term)
        # 前两轮重复同一术语，制造跨段落一致性压力。
        repeat = f" The same {term} was re-measured in the following season."
        paragraph = f"{sentence}{repeat}"
        if index % 23 == 22:
            paragraph = f"{paragraph} {_RESERVED_TOKENS[(index // 23) % len(_RESERVED_TOKENS)]}"
        if index % 29 == 28:
            paragraph = f"{paragraph} {_PRESERVE_SENTENCE}"
        produced.append(paragraph)
        document.add_paragraph(paragraph)

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(docx_path))

    entries = [
        {
            "source": term,
            "target": target,
            "preferred": target,
            "forbidden": list(forbidden),
            "behavior": "translate",
            "status": "locked",
            "domain": "ecology",
            "scope": "document",
            "note": "验收场景锁定术语",
        }
        for term, target, forbidden in _LOCKED_TERMS
    ]
    entries.append({
        "source": "LiCOR-7500 analyser",
        "target": "LiCOR-7500 analyser",
        "preferred": "LiCOR-7500 analyser",
        "forbidden": [],
        "behavior": "preserve",
        "status": "locked",
        "domain": "instrument",
        "scope": "document",
        "note": "仪表专名，保留原文",
    })
    glossary_path.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    term_hits = sum(paragraph.count(term) for term, _, _ in _LOCKED_TERMS
                    for paragraph in produced)
    words = sum(len(item.split()) for item in produced)
    return {
        "kind": "terms",
        "content_sha256": content_sha256(produced),
        "path": docx_path.name,
        "glossary": glossary_path.name,
        "paragraphs": len(produced),
        "words": words,
        "approx_pages": round(words / 550, 1),
        "terms": [term for term, _, _ in _LOCKED_TERMS],
        "locked_entries": len(entries),
        "term_occurrences": term_hits,
        "preserve_sentence": _PRESERVE_SENTENCE,
        "reserved_tokens": list(_RESERVED_TOKENS),
    }


# ================= 入口 =================


def build_all(out_dir: Path, *, only: set[str] | None = None) -> dict:
    """生成选中场景，返回 manifest（含每个场景的预期结构）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios: dict[str, dict] = {}

    if only is None or "docx" in only:
        scenarios["docx_20p"] = build_docx_scenario(out_dir / "scenario-20p.docx")
    if only is None or "pdf" in only:
        scenarios["pdf_100p"] = build_pdf_scenario(out_dir / "scenario-100p.pdf")
    if only is None or "pdf" in only:
        scenarios["pdf_columns"] = build_pdf_columns_scenario(
            out_dir / "scenario-columns.pdf")
    if only is None or "terms" in only:
        scenarios["terms_dense"] = build_terminology_scenario(
            out_dir / "scenario-terms.docx", out_dir / "scenario-terms.glossary.json")

    manifest = {
        "generator": "scripts/make_scenario_fixtures.py",
        "blueprint": "docs/foliothread-agentic-native-blueprint.md#8",
        "deterministic": True,
        "scenarios": scenarios,
    }
    (out_dir / "fixtures-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("tmp/scenarios"),
                        help="输出目录（默认 tmp/scenarios）")
    parser.add_argument("--only", choices=["docx", "pdf", "terms"], action="append",
                        help="只生成指定场景，可重复；默认全部")
    args = parser.parse_args(argv)

    manifest = build_all(args.out, only=set(args.only) if args.only else None)
    for name, info in manifest["scenarios"].items():
        size = (args.out / info["path"]).stat().st_size
        pages = info.get("pages") or info.get("approx_pages") or "-"
        print(f"  {name:12s} {info['path']:26s} "
              f"{info['paragraphs']:5d} 段  ~{pages} 页  {size / 1024:.0f} KB")
    print(f"manifest -> {args.out / 'fixtures-manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
