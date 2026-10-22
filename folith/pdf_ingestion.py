"""Deterministic PDF layout extraction and paragraph reconstruction.

The extractor deliberately keeps layout evidence until after block roles are
classified. Captions, footnotes and running furniture are therefore never
fed into the paragraph joiner merely because a preceding sentence is open.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import fitz


SENTENCE_TERMINAL = set('.!?"”’…:;؟۔)')
ORNAMENT_RE = re.compile(r"^[\s*•·▪◦‣❦❧—–\-]{1,12}$")
PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?\s*$",
    re.IGNORECASE,
)
CAPTION_CUE_RE = re.compile(
    r"^(?:fig(?:ure)?\.?\s*\d+|plate\s*\d+|photo(?:graph)?\.?|"
    r"image\s*\d+|table\s*\d+|source\s*:|courtesy\s*:)",
    re.IGNORECASE,
)
BIBLIOGRAPHY_CUE_RE = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s+\S")
ABBREV_RE = re.compile(
    r"\b(?:Lt|Col|Gen|Maj|Capt|Sgt|Brig|Mr|Mrs|Ms|Dr|St|No|Vol|pp|"
    r"e\.g|i\.e|vs|etc|a\.m|p\.m|U\.S|A\.F|B\.C|A\.D)\.",
    re.IGNORECASE,
)

SKIPPED_ROLES = {
    "caption", "footnote", "header", "footer", "page_number", "ornament", "image",
}
JOINABLE_ROLES = {"body", "quote", "bibliography"}

#: 脚注多证据判定（见 :func:`_footnote_evidence`）。旧的 0.78 单阈值保留为快速路径；
#: 真实报告里脚注字号常是正文的 0.8 倍左右，落在阈值外。
FOOTNOTE_FAST_SIZE_RATIO = 0.78
FOOTNOTE_SIZE_RATIO = 0.9
FOOTNOTE_REGION_MIN = 0.6
FOOTNOTE_LEAD_RE = re.compile(r"^\s*(\d{1,3}|[*†‡§])(?=\s|\D)")
#: 分隔线：很短的水平线（宽度不超过页宽一半、近乎零高度），位于脚注块之上。
FOOTNOTE_RULE_MAX_WIDTH = 0.5
FOOTNOTE_RULE_MAX_HEIGHT = 2.0
FOOTNOTE_RULE_REACH_EM = 2.0
FOOTNOTE_GAP_EM = 1.0
#: 夹层记录：B 路径只愿意跨过这样的页底小字号记录。
BRIDGE_SIZE_RATIO = 0.95
BRIDGE_MAX_RECORDS = 3

#: PyMuPDF span flags 的 bold 位（bit 4）。``_font_metrics`` 把它按 ``max()`` 聚合，
#: 所以**一个**加粗 span 就能把整行/整块点亮——单看这一位不足以断言"这块是标题"。
FONT_FLAG_BOLD = 16

#: 加粗要算「整块的排版」必须覆盖过半**字符**（见 :func:`_bold_share`）。
BOLD_DOMINANCE_MIN = 0.5

#: 加粗分支的字号下限（相对正文）：**比正文还小**的加粗文本不是标题。
#:
#: 本仓实测：一篇期刊的摘要在窄栏里排 10.75pt（正文 11.7pt，0.92×），字体回退在
#: 个别字符上换成了带 bold 位的替代字体 → ``font_flags`` 的 max() 把整行点亮，
#: 整段摘要被逐行判成 ``heading``。标题从定义上不小于它统领的正文，所以这一条
#: 不会有真标题被误伤；0.92× 同时还在 ``header``/``footer`` 的 0.9× 阈值之上，
#: 所以它落进 ``quote`` 是唯一正确的归宿。
HEADING_BOLD_MIN_SIZE_RATIO = 0.98

# --- 扫描件上的 OCR 文本层（「图片 + 隐形文字」页）-------------------------------
# 扫描书（Internet Archive、OCRmyPDF、扫描仪自带 OCR）每页是一张整页图片，上面盖
# 一层**隐形**文字（render mode 3，PyMuPDF 里 span 的 ``alpha == 0``）。这层文字的
# 几何是 OCR 估出来的，不是排版：
#
# * 字号逐词抖动（为了让隐形字宽对上图里的词宽），所以"排版一致"的字号判据失效；
# * 行起点逐行抖动，居中排版（封底、扉页）每行起点都不同，"缩进 = 新段"失效；
# * 图里的纹理、装饰会被识别成 ``©`` ``.`` ``—`` 这类孤立符号行，夹在正文行之间，
#   把本该相连的两行隔开；
# * 整页图片让每一行都"挨着图片"，图注判据会把正文行当成图注静默丢掉。
#
# 这类页面改用只依赖**行距**的判据（见 :func:`_overlay_continues`）。
#: 一页的字符里隐形文字占到这个比例，就按 OCR 文本层处理。
OCR_OVERLAY_INVISIBLE_SHARE = 0.6
#: 图片面积占页面这个比例以上算"整页背景图"：它不是图注能依附的插图。
FULL_PAGE_IMAGE_AREA = 0.85
#: 两行之间的空白超过这么多个行高，就是段间空行。
OVERLAY_PARAGRAPH_GAP = 0.9
#: 上一行没结句时允许的行距上限（行高倍数）。
OVERLAY_OPEN_GAP = 2.2
#: 左对齐时，起点右移超过这么多个行高算首行缩进。
OVERLAY_INDENT = 0.8
#: 两行的水平中点相差在这么多个行高以内、起点却不对齐，算居中排版。
OVERLAY_CENTRE_TOLERANCE = 1.5
#: 结句的上一行如果右端离文本右边界还差这么多个行高，就是段末短行。
OVERLAY_SHORT_LINE = 3.0
_ALNUM_RE = re.compile(r"[^\W_]")
#: OCR 层行内的孤立噪声记号（图里的竖线、下划线、波纹）：整词只由这些字符组成时删掉。
#: 破折号、引号等真标点不在其列。
_OVERLAY_NOISE_TOKEN_RE = re.compile(r"(?<!\S)[_|¦~=]+(?!\S)")


#: 标题里可以小写的虚词（Title Case 惯例）。
_TITLE_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "or", "the", "to", "with",
}
#: OCR 层上，按字号判成标题的块至少要有这么多实词首字母大写。
OVERLAY_HEADING_CAPITALISED_SHARE = 0.6


def _looks_like_title(text: str) -> bool:
    """文字本身像不像标题：全大写，或实词大多首字母大写（Title Case）。

    OCR 层的字号逐词抖动，封底一类居中的正文块常被估得比正文大一截，单凭字号
    会被判成标题、从而不再和上下文拼接。真标题（``The Moving Shelves``
    ``in the Middle of Nowhere``）的写法本身就是证据，正文句子不是。
    """
    words = re.findall(r"[^\W\d_][\w’'-]*", text)
    content = [word for word in words if word.casefold() not in _TITLE_MINOR_WORDS]
    if not words:
        # 纯数字的章题（``00:00:00``）没有大小写可看。
        return any(char.isdigit() for char in text)
    if not content:
        return False
    capitalised = sum(1 for word in content if word[:1].isupper())
    return capitalised / len(content) >= OVERLAY_HEADING_CAPITALISED_SHARE


def _is_full_page_image(block: Dict[str, Any]) -> bool:
    width = float(block.get("page_width") or 0)
    height = float(block.get("page_height") or 0)
    if width <= 0 or height <= 0:
        return False
    area = float(block.get("x1", 0) - block.get("x0", 0)) * float(
        block.get("y1", 0) - block.get("y0", 0))
    return area >= width * height * FULL_PAGE_IMAGE_AREA


def _page_is_ocr_overlay(page_dict: Dict[str, Any]) -> bool:
    """这一页的文字是不是盖在扫描图上的隐形 OCR 层。"""
    total = invisible = 0
    for raw in page_dict.get("blocks") or []:
        if int(raw.get("type") or 0) != 0:
            continue
        for line in raw.get("lines") or []:
            for span in line.get("spans") or []:
                size = len(str(span.get("text") or "").strip())
                total += size
                if span.get("alpha") == 0:
                    invisible += size
    return bool(total) and invisible / total >= OCR_OVERLAY_INVISIBLE_SHARE


def _bbox(value: Sequence[Any]) -> Dict[str, float]:
    values = list(value or [])[:4]
    values += [0.0] * (4 - len(values))
    x0, y0, x1, y1 = (float(item or 0) for item in values)
    return {
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": max(0.0, x1 - x0),
        "height": max(0.0, y1 - y0),
    }


def _normalise_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _normalise_repeat(text: str) -> str:
    return re.sub(r"\d+", "#", _normalise_line(text)).casefold()


def _font_metrics(spans: Sequence[Dict[str, Any]]) -> Tuple[float, int, List[str]]:
    sizes = [
        float(span.get("size") or 0)
        for span in spans
        if float(span.get("size") or 0) > 0
    ]
    flags = [int(span.get("flags") or 0) for span in spans]
    fonts = [str(span.get("font") or "") for span in spans if span.get("font")]
    return (
        statistics.median(sizes) if sizes else 0.0,
        max(flags) if flags else 0,
        sorted(set(fonts)),
    )


def _bold_share(spans: Sequence[Dict[str, Any]]) -> float:
    """加粗 span 覆盖的**字符**占比（按 span 文本长度加权）。

    ``_font_metrics`` 的 ``font_flags`` 用 ``max()`` 聚合：一个加粗 span 就能把整行
    点亮。真实 PDF 里这常常不是排版意图——本仓实测一篇期刊摘要的引文里，字体回退把
    个别字符换成带 bold 位的替代字体，于是相邻行的 ``font_flags`` 在 4 / 20 之间交替
    而字号完全相同（10.746），整段引文被逐行判成 ``heading``。

    标题的加粗是**整块**的排版属性，所以这里按字符加权——与 ``font_size`` 取
    ``median`` 同源：要么整块是加粗的，要么不是。
    """
    total = 0
    bold = 0
    for span in spans:
        text = str(span.get("text") or "")
        if not text.strip():
            continue
        total += len(text)
        if int(span.get("flags") or 0) & FONT_FLAG_BOLD:
            bold += len(text)
    return (bold / total) if total else 0.0


# --- 脚注/尾注角标（superscript reference marks）---------------------------
# 角标是最容易在抽取阶段被抹掉的一类行内结构：它一旦和正文拼成同一个字符串，
# 下游就无法把它和真实数字（年份、页码、量词）区分开。这里在 span 级别识别，
# 从可翻译文本中剥离，并把几何证据留在 marks 侧表里供保真链路使用。
#
# 判定同时要求「明显更小」和「明显上抬」两个几何条件：只靠 PyMuPDF 的
# superscript 位会把数学指数（x²）也算进来，而指数恰好是同样被标记为上标、
# 但字号与正文相同的一类 span。
SUPERSCRIPT_FLAG = 1
MARKER_TEXT_RE = re.compile(r"^\d{1,3}$")
MARKER_SIZE_RATIO_MAX = 0.82
MARKER_RAISE_EM_MIN = 0.12
# 文档级准入：真正的脚注角标数量多、编号基本递增、并在正文中分散出现；
# 数学指数往往集中在一小段公式里。三者取其二即可排除公式型误判。
MARKER_MIN_COUNT = 3
#: 编号重置只可能落在很小的值上；跌回一个大数字说明这些不是脚注编号。
MARKER_RESET_MAX = 3
MARKER_MIN_MEDIAN_GAP = 40
MARKER_MIN_PAGES = 3
MARKER_RAISE_EM_MAX = 1.5


def _marker_evidence(span: Dict[str, Any], body_size: float,
                     baselines: Sequence[float]) -> Optional[Dict[str, Any]]:
    """单个 span 的角标证据；不是角标时返回 None。

    ``baselines`` 是所在页正文行的基线（升序）。角标总是坐在它所属正文行的
    基线之上，所以取「第一个高于该 span 的正文基线」作参照，而不是取该 span
    自己所在行的最大 origin_y——后者在 PDF 把角标单独切成一个 line 对象时会
    退化成零抬升，从而漏检。
    """
    text = str(span.get("text") or "").strip()
    if not MARKER_TEXT_RE.fullmatch(text):
        return None
    size = float(span.get("size") or 0.0)
    if size <= 0 or body_size <= 0:
        return None
    if size > body_size * MARKER_SIZE_RATIO_MAX:
        return None
    origin = span.get("origin") or (0.0, 0.0)
    origin_y = float(origin[1])
    reference = next((value for value in baselines if value > origin_y + 0.5), None)
    if reference is None:
        return None
    raise_em = (reference - origin_y) / size
    if raise_em < MARKER_RAISE_EM_MIN or raise_em > MARKER_RAISE_EM_MAX:
        return None
    return {
        "text": text,
        "size": round(size, 3),
        "body_size": round(body_size, 3),
        "raise_em": round(raise_em, 3),
        "flags": int(span.get("flags") or 0),
        "bbox": [round(float(v), 3) for v in (span.get("bbox") or [])],
    }


def _page_body_metrics(page_dict: Dict[str, Any]):
    """页面级正文基准：中位字号 + 正文行基线（升序、去重）。"""
    sizes: List[float] = []
    rows: List[Tuple[float, float]] = []
    for raw in page_dict.get("blocks") or []:
        if int(raw.get("type") or 0) != 0:
            continue
        for line in raw.get("lines") or []:
            for span in line.get("spans") or []:
                if not str(span.get("text") or "").strip():
                    continue
                size = float(span.get("size") or 0.0)
                if size <= 0:
                    continue
                sizes.append(size)
                rows.append((size, float((span.get("origin") or (0.0, 0.0))[1])))
    if not sizes:
        return 0.0, []
    body_size = statistics.median(sizes)
    baselines = sorted({y for size, y in rows if size >= body_size * 0.9})
    return body_size, baselines


def _marker_population_is_footnote_like(candidates: Sequence[Dict[str, Any]]) -> bool:
    """文档级准入：排除数学指数一类的上标数字。

    编号会在章节边界重新从 1 开始，所以"序列必须单调"这条判据会把一本正常的书
    整体否掉——实测一份阿拉伯语语料（自带 4,2,1,5,5 的残段编号）叠加正文角标
    后，单调比只有 0.7，于是全文角标被静默放弃。改成只否决"跌回一个大数字"：
    那才是普通数值混进来的特征，而重新从 1 开始是合法的编号重置。
    """
    if len(candidates) < MARKER_MIN_COUNT:
        return False
    values = [int(item["text"]) for item in candidates]
    for previous, current in zip(values, values[1:]):
        if current < previous and current > MARKER_RESET_MAX:
            return False
    positions = [int(item.get("doc_position") or 0) for item in candidates]
    gaps = [b - a for a, b in zip(positions, positions[1:]) if b > a]
    median_gap = statistics.median(gaps) if gaps else 0
    pages = {item.get("page_number") for item in candidates}
    return median_gap >= MARKER_MIN_MEDIAN_GAP or len(pages) >= MARKER_MIN_PAGES


def _strip_markers(spans: Sequence[Dict[str, Any]],
                   evidence: Dict[int, Dict[str, Any]]):
    """返回 (干净文本, marks)；被剥离的角标按其在干净文本中的位置登记。"""
    parts: List[str] = []
    marks: List[Dict[str, Any]] = []
    cursor = 0
    last_char = ""
    for index, span in enumerate(spans):
        text = str(span.get("text") or "")
        item = evidence.get(index)
        if item is None:
            parts.append(text)
            cursor += len(text)
            if text:
                last_char = text[-1]
            continue
        # 角标两侧都不是空白时补一个空格，避免剥离后把两个词粘成一个。
        right = ""
        for later in spans[index + 1:]:
            right = str(later.get("text") or "")
            if right:
                break
        if last_char and not last_char.isspace() and right and not right[:1].isspace():
            parts.append(" ")
            cursor += 1
            last_char = " "
        record = dict(item)
        record["char_start"] = cursor
        marks.append(record)
    return "".join(parts), marks


def superscript_marker_census(file_bytes: bytes) -> Dict[str, Any]:
    """Span-level census of superscript markers in a PDF text layer.

    Cheap on purpose: it never reconstructs paragraphs, so the text-layer
    quality gate can report marker counts before paying for a full extraction.
    ``footnote_body_candidates`` applies the same typographic rule
    ``classify_blocks`` uses for footnote blocks, and exists so that "N markers
    but no note text in this document" is visible to the operator.
    """
    markers: List[Dict[str, Any]] = []
    footnote_lines = 0
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            for page_number, page in enumerate(doc):
                height = float(page.rect.height) or 1.0
                page_dict = page.get_text("dict")
                body_size, baselines = _page_body_metrics(page_dict)
                for raw in page_dict.get("blocks") or []:
                    if int(raw.get("type") or 0) != 0:
                        continue
                    for line in raw.get("lines") or []:
                        spans = [s for s in line.get("spans") or []
                                 if str(s.get("text") or "")]
                        if not spans:
                            continue
                        for span in spans:
                            found = _marker_evidence(span, body_size, baselines)
                            if found:
                                markers.append({
                                    "page_number": page_number + 1,
                                    "text": found["text"],
                                    "size": found["size"],
                                    "raise_em": found["raise_em"],
                                })
                        line_size = _font_metrics(spans)[0] or body_size
                        text = _normalise_line(
                            "".join(str(s.get("text") or "") for s in spans))
                        box = _bbox(line.get("bbox"))
                        if (body_size and line_size <= body_size * 0.78
                                and box["y0"] > height * 0.68
                                and len(text) > 20):
                            footnote_lines += 1
    except Exception:  # noqa: BLE001 - a census must never break ingestion
        return {"status": "unavailable", "marker_count": 0,
                "marker_pages": [], "footnote_body_candidates": 0}
    page_numbers = sorted({item["page_number"] for item in markers})
    return {
        "status": "ok",
        "marker_count": len(markers),
        "marker_pages": page_numbers,
        "footnote_body_candidates": footnote_lines,
        "sample": markers[:5],
        "note": ("正文含角标但未发现脚注正文，角标已按引用标记剥离"
                 if markers and not footnote_lines else ""),
    }


def extract_layout_blocks(file_bytes: bytes) -> List[Dict[str, Any]]:
    """Extract text and image blocks while retaining page/layout metadata.

    Returned objects are plain dictionaries so they can be inspected in tests
    and persisted as JSON without introducing a second state model.

    Footnote reference marks are removed from the translatable text and kept in
    ``marks`` (additive key) so that fidelity/provenance consumers can still
    place them, while translation never sees a bare digit glued to a sentence.
    """
    blocks: List[Dict[str, Any]] = []
    all_candidates: List[Dict[str, Any]] = []
    block_pending: List[Tuple[List[Dict[str, Any]], List[Any]]] = []
    doc_position = 0
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        order = 0
        for page_number, page in enumerate(doc):
            # Text geometry is unrotated PDF space, including rotated pages.
            page.set_rotation(0)
            width, height = float(page.rect.width), float(page.rect.height)
            page_dict = page.get_text("dict")
            page_body_size, baselines = _page_body_metrics(page_dict)
            ocr_overlay = _page_is_ocr_overlay(page_dict)
            rules = _page_rules(page, width)
            line_heights = [
                float(line["bbox"][3]) - float(line["bbox"][1])
                for raw in page_dict.get("blocks") or [] if int(raw.get("type") or 0) == 0
                for line in raw.get("lines") or []
                if len(line.get("bbox") or ()) == 4
                and float(line["bbox"][3]) > float(line["bbox"][1])
            ]
            page_line_height = (
                statistics.median(line_heights) if line_heights else 0.0
            )
            line_right_edges = sorted(
                float(line["bbox"][2])
                for raw in page_dict.get("blocks") or [] if int(raw.get("type") or 0) == 0
                for line in raw.get("lines") or [] if len(line.get("bbox") or ()) == 4
            )
            page_text_x1 = (
                line_right_edges[int(len(line_right_edges) * 0.9)]
                if line_right_edges else 0.0
            )
            line_left_edges = sorted(
                float(line["bbox"][0])
                for raw in page_dict.get("blocks") or [] if int(raw.get("type") or 0) == 0
                for line in raw.get("lines") or [] if len(line.get("bbox") or ()) == 4
            )
            page_text_x0 = (
                line_left_edges[int(len(line_left_edges) * 0.1)]
                if line_left_edges else 0.0
            )
            for raw_number, raw in enumerate(page_dict.get("blocks") or []):
                block_box = _bbox(raw.get("bbox"))
                block_id = f"p{page_number + 1}-b{raw_number}"
                block_type = int(raw.get("type") or 0)
                if block_type == 1:
                    blocks.append(
                        {
                            "block_id": block_id,
                            "block_type": "image",
                            "role": "image",
                            "page_number": page_number + 1,
                            "reading_order": order,
                            "bbox": block_box,
                            "x0": block_box["x0"],
                            "y0": block_box["y0"],
                            "x1": block_box["x1"],
                            "y1": block_box["y1"],
                            "font_size": 0.0,
                            "font_flags": 0,
                            "font_style": [],
                            "relative_width": block_box["width"] / width if width else 0.0,
                            "page_width": width,
                            "page_height": height,
                            "text": "",
                            "lines": [],
                        }
                    )
                    order += 1
                    continue
                if block_type != 0:
                    continue
                line_records = []
                all_spans = []
                pending_lines = []
                superscripts: List[str] = []
                for line_number, line in enumerate(raw.get("lines") or []):
                    spans = [
                        span
                        for span in line.get("spans") or []
                        if str(span.get("text") or "")
                    ]
                    text = "".join(str(span.get("text") or "") for span in spans)
                    if not _normalise_line(text):
                        continue
                    line_box = _bbox(line.get("bbox"))
                    size, flags, fonts = _font_metrics(spans)
                    evidence: Dict[int, Dict[str, Any]] = {}
                    for index, span in enumerate(spans):
                        found = _marker_evidence(span, page_body_size, baselines)
                        if found:
                            evidence[index] = found
                            superscripts.append(found["text"])
                    clean_text, marks = _strip_markers(spans, evidence)
                    for item in marks:
                        item["page_number"] = page_number + 1
                        item["block_id"] = block_id
                        item["line_id"] = f"{block_id}-l{line_number}"
                    candidates = []
                    for item in marks:
                        candidate = dict(item)
                        candidate["doc_position"] = doc_position + item["char_start"]
                        candidates.append(candidate)
                    if candidates:
                        for candidate in candidates:
                            all_candidates.append(candidate)
                    doc_position += len(text)
                    line_records.append(
                        {
                            "line_id": f"{block_id}-l{line_number}",
                            "text": text,
                            "bbox": line_box,
                            "x0": line_box["x0"],
                            "y0": line_box["y0"],
                            "x1": line_box["x1"],
                            "y1": line_box["y1"],
                            "font_size": size,
                            "font_flags": flags,
                            "font_style": fonts,
                            "marks": [],
                            "clean_text": clean_text,
                        }
                    )
                    pending_lines.append((line_records[-1], clean_text, marks))
                    all_spans.extend(spans)
                if not line_records:
                    continue
                size, flags, fonts = _font_metrics(all_spans)
                text = _normalise_line(
                    " ".join(line["text"] for line in line_records)
                )
                block_pending.append((line_records, pending_lines))
                blocks.append(
                    {
                        "block_id": block_id,
                        "block_type": "text",
                        "role": "unknown",
                        "page_number": page_number + 1,
                        "reading_order": order,
                        "bbox": block_box,
                        "x0": block_box["x0"],
                        "y0": block_box["y0"],
                        "x1": block_box["x1"],
                        "y1": block_box["y1"],
                        "font_size": size,
                        "font_flags": flags,
                        "font_style": fonts,
                        # 加粗是"整块的排版"还是"个别 span 的强调"：见 _bold_share。
                        # 与 font_flags（max）并存，让 heading 判据能区分两者。
                        "bold_share": round(_bold_share(all_spans), 4),
                        "relative_width": block_box["width"] / width if width else 0.0,
                        "page_width": width,
                        "page_height": height,
                        "text": text,
                        "lines": line_records,
                        "marks": [],
                        # 与文档级准入无关的原始角标证据，供脚注判定核对编号。
                        "superscript_markers": superscripts,
                        "rule_above": _rule_above(block_box, rules, page_body_size),
                        "ocr_overlay": ocr_overlay,
                        "page_line_height": page_line_height,
                        "page_text_x1": page_text_x1,
                        "page_text_x0": page_text_x0,
                    }
                )
                order += 1
    _apply_marker_decisions(blocks, block_pending, all_candidates)
    return blocks


def _apply_marker_decisions(blocks, block_pending, all_candidates) -> None:
    """文档级准入后落定角标：文本剥离，几何证据进 marks。"""
    accepted = _marker_population_is_footnote_like(all_candidates)
    for line_records, pending_lines in block_pending:
        for record, clean_text, marks in pending_lines:
            record.pop("clean_text", None)
            if accepted and marks:
                record["text"] = clean_text
                record["marks"] = [dict(item) for item in marks]
    for block in blocks:
        if block.get("block_type") != "text":
            continue
        lines = block.get("lines") or []
        # Record where each mark sits in the block text.  Segments are ordered
        # slices of that text, so an offset is what lets a mark be assigned to
        # exactly one segment instead of every sibling.
        parts: List[str] = []
        marks: List[Dict[str, Any]] = []
        cursor = 0
        for line in lines:
            text = line["text"]
            if parts:
                cursor += 1  # the single space the join inserts
            for item in line.get("marks") or []:
                record = dict(item)
                record["text_offset"] = cursor + int(item.get("char_start") or 0)
                marks.append(record)
            parts.append(text)
            cursor += len(text)
        block["text"] = _normalise_line(" ".join(parts))
        block["marks"] = marks


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _near_image(
    block: Dict[str, Any], image: Dict[str, Any], body_size: float
) -> bool:
    horizontal = _overlap(
        block["x0"], block["x1"], image["x0"], image["x1"]
    )
    if horizontal <= 0:
        return False
    gap = max(image["y0"] - block["y1"], block["y0"] - image["y1"], 0.0)
    return gap <= max(12.0, body_size * 2.8)


def _is_sentence_terminal(text: str) -> bool:
    value = _normalise_line(text)
    if not value:
        return False
    value = re.sub(r"[\]\)}」』”’']+$", "", value).rstrip()
    if ABBREV_RE.search(value[-12:]):
        return False
    return value[-1] in SENTENCE_TERMINAL


def _is_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value or "")


#: 「句内标点」：以它们收尾的行是**在短语中间被切断**的直接证据。
#:
#: 这里刻意不必与 `source_cleanup._is_unfinished_line` 完全同口径。那个判据服务
#: 的是"模型可以改写这一行"的准入，它排除逗号是因为**改写**可能把真标点删掉；
#: 本路径只做拼接（`_join_text` 除了行末连字符不删任何字符），所以逗号可以收。
#:
#: 注意**不能**把全角句末（`。！？`）算进来：它们虽不在 pdf_ingestion 的
#: `SENTENCE_TERMINAL`（那是给拉丁/阿拉伯系用的）里，但同样是句末，收进来会把
#: 中文段落逐段粘起来。
INLINE_PUNCTUATION = set(",，、")


def _ends_open(text: str) -> bool:
    """这一行是否"明显还没写完"：不是句末，且以字母 / CJK / 句内标点收尾。

    只认最强的一类证据，比 `source_cleanup._is_unfinished_line` 多收一类句内标点
    （理由见 :data:`INLINE_PUNCTUATION`）；以 ``’`` ``—`` 收尾的行仍然不收。
    """
    value = _normalise_line(text)
    if not value or _is_sentence_terminal(value):
        return False
    last = value[-1:]
    return last.isalpha() or _is_cjk(last) or last in INLINE_PUNCTUATION


def _starts_continuation(left: str, right: str) -> bool:
    """右行是否"明显是左行的续行"：以小写字母开头，或两侧同为 CJK。

    CJK 分支必须同时看左边，否则「中文段落不以句号收尾」时任何两段都会被并起来。
    """
    head = _normalise_line(right)[:1]
    if not head:
        return False
    if head.islower():
        return True
    return _is_cjk(_normalise_line(left)[-1:]) and _is_cjk(head)


def _is_obvious_continuation(
    previous: Dict[str, Any], current: Dict[str, Any]
) -> bool:
    """两行是否构成「确定性断行整理」意义上的一处续行。

    这是 ``source_cleanup.obvious_line_join`` 的**提取期等价物**。放在这里的理由：
    这几条证据全部只依赖版面与首尾字符，不需要模型；而模型那一步（原始文本整理）
    是 opt-in 的，断行却是版面的确定事实 —— 不该等到模型那一步才生效。

    与合并判据里那套 role / 缩进口径**故意不同**的两点：

    * 不看 role：role 是**跨块**的标签，而"这段文字是否连续"是版面事实，两者不该
      互相牵制。曾经的具体表现是同一段引文里的行因字体回退被逐行判成 ``quote`` /
      ``heading``（本仓实测同段引文里 flag 4 与 20 交替、字号完全相同 10.746）；
      那个误判的**源头**已由 :func:`_bold_heading_evidence` 修掉，但本路径依旧不把
      role 当门槛——窄栏里 role 与"是否连续"本来就是两件事。
    * 不看 ``is_indented``：悬挂缩进的续行**本来就该**缩进；而列边界解不出来的
      窄栏（本仓实测作者单位栏 x0=63.8，落在全局左边距 54.5 右侧）整列都会被误判
      成"段首缩进"。

    取而代之的门槛是**排版一致**：真标题与它引出的正文排版不同，而同一段文字逐行
    的字号只会轻微抖动。没有这一条，一个不以标点收尾的短标题会被并进下一段。
    """
    left = _normalise_line(previous.get("text"))
    right = _normalise_line(current.get("text"))
    if not left or not right:
        return False
    if len(left) >= CONTINUATION_MAX_PREV_CHARS:
        return False
    if not _ends_open(left):
        return False
    if not _starts_continuation(left, right):
        return False
    previous_size = float(previous.get("font_size") or 0)
    current_size = float(current.get("font_size") or 0)
    if previous_size <= 0 or current_size <= 0:
        return False
    return abs(previous_size - current_size) <= (
        max(previous_size, current_size) * CONTINUATION_SIZE_TOLERANCE
    )


def _body_left_edge(text_blocks: Sequence[Dict[str, Any]]) -> float:
    counts = Counter(
        round(float(block.get("x0") or 0), 1) for block in text_blocks
    )
    if not counts:
        return 0.0
    max_count = max(counts.values())
    candidates = [
        x for x, count in counts.items() if count >= max(1, max_count * 0.45)
    ]
    return min(candidates) if candidates else counts.most_common(1)[0][0]


#: 一个左边界要算"文本列"，至少要由连续这么多个同起点的块支撑。
#
#: 「首行缩进」与「列边界」在缩进量上无法区分——本仓实测的期刊版式里，正文列
#: 左边界 49.08、列表续行缩进 66.60（1.5em），与常见的段落缩进同量级。能区分的
#: 是**持续性**：段落缩进是"缩进一行、下一行退回正文边界"，成不了连续 run；
#: 栏、窄框和悬挂缩进的续行则是连续多行同起点。所以用 run 而不是缩进量来判列，
#: 也避免了"两行短段落"把缩进值顶成列的假阳性。
COLUMN_EDGE_MIN_RUN = 2

#: 列边界的纵向作用域容差（正文行距只有几 pt，收紧会把同栏判成不相邻）。
COLUMN_EDGE_SLACK_MIN = 16.0
COLUMN_EDGE_SLACK_EM = 1.5

#: 参照换成列边界后，「起点正好落在某条列边界上」有两种含义：悬挂缩进的续行
#: （相对本段首行只横移一个缩进量）和换到另一列（横移上百 pt）。用位移量区分。
#: 位移取**绝对值**——换列可能是向右（接下一栏）也可能是向左（回到上一栏）。
COLUMN_JUMP_MIN = 36.0
COLUMN_JUMP_EM = 3.5

#: 「确定性断行整理」的规模上限，与 ``source_cleanup.obvious_line_join`` 同口径：
#: 超过这个长度的"未结句行"更可能是真的段落（或表格行），不再自动续行。
CONTINUATION_MAX_PREV_CHARS = 180

#: 续行对的字号容忍度（相对字号）。同一段引文/正文的逐行字号会因字体回退而轻微
#: 抖动（本仓实测 9.69 vs 9.23，差值 4.8%），而**真标题与它引出的正文排版不同**——
#: 这条容忍度就是"排版一致"与"标题不同"之间的分界线。取 6% 是为了覆盖前者、
#: 拦住后者（实测真标题 13pt vs 正文 9.5pt，差值 37%）。
CONTINUATION_SIZE_TOLERANCE = 0.06


def _page_left_edges(
    text_blocks: Sequence[Dict[str, Any]],
) -> Dict[int, Dict[float, List[Tuple[float, float]]]]:
    """每页的候选列左边界，附带支撑它的纵向区间。

    ``_body_left_edge`` 给的是**全文档**一个左边距；只要某段内容的列起点不等于
    它，整列都会被当成"段首缩进"，于是逐行成段（实测：双栏前置区的摘要 11 行变
    11 段、悬挂缩进列表每个续行都成一段）。这里按页给出若干列起点作为每块自己
    的参照，而不替换掉那个全局值——全局值仍是 ``_line_groups`` 与 ``quote`` 的
    口径，保持不变。

    记录纵向区间是为了让边界只在**它自己所在的那一片版面上**生效：同一个页面上
    存在互不相邻的窄栏时（本仓实测：左栏元数据 54.6 与正文列 49.1 同页），不按
    纵向剪裁就会把正文的缩进段认领到无关的栏上去。
    """
    by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for block in text_blocks:
        by_page[int(block.get("page_number") or 0)].append(block)
    edges: Dict[int, Dict[float, List[Tuple[float, float]]]] = {}
    for page, items in by_page.items():
        ordered = sorted(items, key=lambda item: item.get("reading_order", 0))
        supported: Dict[float, List[Tuple[float, float]]] = defaultdict(list)
        index = 0
        while index < len(ordered):
            x0 = round(float(ordered[index].get("x0") or 0), 1)
            stop = index + 1
            while stop < len(ordered) and round(
                float(ordered[stop].get("x0") or 0), 1
            ) == x0:
                stop += 1
            if stop - index >= COLUMN_EDGE_MIN_RUN:
                for item in ordered[index:stop]:
                    supported[x0].append(
                        (float(item.get("y0") or 0), float(item.get("y1") or 0))
                    )
            index = stop
        edges[page] = dict(supported)
    return edges


def _local_left_edge(
    block: Dict[str, Any],
    page_edges: Mapping[int, Mapping[float, Sequence[Tuple[float, float]]]],
    fallback: float,
    slack: float,
) -> float:
    """该块所属文本列的左边界：不右于该块 x0 的最大候选边界。

    ``slack`` 用于跨过相邻行之间与块之间的竖向间隙（排版行距只有几 pt），否则
    同一栏的支撑区间会因为 7pt 的行距而判成不相邻。
    """
    page = int(block.get("page_number") or 0)
    x0 = round(float(block.get("x0") or 0), 1)
    y0 = float(block.get("y0") or 0) - slack
    y1 = float(block.get("y1") or 0) + slack
    best: Optional[float] = None
    for edge, spans in (page_edges.get(page) or {}).items():
        if edge > x0:
            continue
        if not any(span_y0 <= y1 and y0 <= span_y1 for span_y0, span_y1 in spans):
            continue
        if best is None or edge > best:
            best = edge
    return best if best is not None else fallback


def _bold_heading_evidence(
    block: Dict[str, Any], font_size: float, body_size: float
) -> bool:
    """加粗能不能当作"这块是标题"的证据。

    原来的判据只有 ``font_flags & FONT_FLAG_BOLD`` 这一个条件，而 ``font_flags`` 是
    span flags 的 ``max()``——**一个**加粗 span 就足以把整块点亮。本仓实测的形状
    （期刊摘要里的引文，相邻行 flag 4 / 20 交替、字号完全相同 10.746）正是这样被
    逐行判成标题的。所以加粗要同时满足：

    * :data:`BOLD_DOMINANCE_MIN`——加粗覆盖过半**字符**，即它是整块的排版而不是
      个别 span 的强调；
    * :data:`HEADING_BOLD_MIN_SIZE_RATIO`——不比正文小；加粗且更小的文本是强调、
      引文或注释，不是标题。

    没有 ``bold_share`` 的块（测试里手工构造的块、以及更早版本写下的状态块）保持
    旧行为：只看 flags 位。
    """
    if not int(block.get("font_flags") or 0) & FONT_FLAG_BOLD:
        return False
    share = block.get("bold_share")
    if share is not None and float(share) < BOLD_DOMINANCE_MIN:
        return False
    return (
        float(font_size or 0)
        >= float(body_size or 0) * HEADING_BOLD_MIN_SIZE_RATIO
    )


def _page_rules(page: Any, width: float) -> List[Tuple[float, float, float]]:
    """本页的短水平线 ``(x0, x1, y)``；脚注分隔线的几何证据。"""
    rules: List[Tuple[float, float, float]] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - 线条只是附加证据
        return rules
    for item in drawings:
        rect = item.get("rect")
        if rect is None:
            continue
        w, h = float(rect.width), float(rect.height)
        if h <= FOOTNOTE_RULE_MAX_HEIGHT and 10.0 <= w <= width * FOOTNOTE_RULE_MAX_WIDTH:
            rules.append((float(rect.x0), float(rect.x1), float(rect.y0 + rect.y1) / 2))
    return rules


def _rule_above(box: Dict[str, float], rules: Sequence[Tuple[float, float, float]],
                body_size: float) -> bool:
    reach = body_size * FOOTNOTE_RULE_REACH_EM
    return any(
        box["y0"] - reach <= y <= box["y0"] + 1.0
        and x0 - body_size <= box["x0"] <= x1 + body_size
        for x0, x1, y in rules
    )


def _footnote_evidence(block: Dict[str, Any], body_size: float,
                       markers: Dict[int, set], previous_y1: Optional[float]) -> bool:
    """页底候选块是否为脚注：字号 + 任一证据，或 标记 + 分隔线。"""
    font_size = float(block.get("font_size") or body_size)
    page = int(block.get("page_number") or 0)
    lead = FOOTNOTE_LEAD_RE.match(block.get("text") or "")
    known = (markers.get(page, set()) | markers.get(page - 1, set()))
    marker = bool(lead and lead.group(1) in known)
    rule = bool(block.get("rule_above"))
    gap = (previous_y1 is not None
           and float(block["y0"]) - previous_y1 > body_size * FOOTNOTE_GAP_EM)
    small = font_size <= body_size * FOOTNOTE_SIZE_RATIO
    return (small and (marker or rule or gap)) or (marker and rule)


def _is_bottom_footnote(block, by_page, body_size, markers, page_height) -> bool:
    """页底区域内、本页最后一个正文主体块之后的块，按多证据判脚注。"""
    if not page_height or block["y0"] <= page_height * FOOTNOTE_REGION_MIN:
        return False
    peers = by_page.get(int(block.get("page_number") or 0), [])
    previous_y1: Optional[float] = None
    for other in peers:
        if other is block:
            continue
        if other["y0"] > block["y0"]:
            if (float(other.get("font_size") or 0) > body_size * FOOTNOTE_SIZE_RATIO
                    and len(other.get("text") or "") > 40):
                return False
        elif previous_y1 is None or other["y1"] > previous_y1:
            previous_y1 = other["y1"]
    return _footnote_evidence(block, body_size, markers, previous_y1)


def classify_blocks(blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Assign deterministic roles using typography, position and image relation."""
    out = [dict(block) for block in blocks or []]
    text_blocks = [block for block in out if block.get("block_type") == "text"]
    if not text_blocks:
        return out
    page_counts = {
        int(block.get("page_number") or 0)
        for block in out
        if block.get("page_number")
    }
    body_x0 = _body_left_edge(text_blocks)
    page_edges = _page_left_edges(text_blocks)
    sizes = [
        float(block.get("font_size") or 0)
        for block in text_blocks
        if float(block.get("font_size") or 0) > 0
    ]
    size_counts = Counter(round(size, 1) for size in sizes)
    max_size_count = max(size_counts.values()) if size_counts else 0
    body_candidates = [
        size for size, count in size_counts.items() if count == max_size_count
    ]
    body_size = max(body_candidates) if body_candidates else 10.0
    repeated = Counter(_normalise_repeat(block.get("text")) for block in text_blocks)
    image_by_page = defaultdict(list)
    for block in out:
        if block.get("block_type") == "image" and not _is_full_page_image(block):
            image_by_page[int(block.get("page_number") or 0)].append(block)
    markers: Dict[int, set] = defaultdict(set)
    for block in text_blocks:
        markers[int(block.get("page_number") or 0)].update(
            block.get("superscript_markers") or [])
    by_page: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for block in text_blocks:
        by_page[int(block.get("page_number") or 0)].append(block)
    page_number = len(page_counts)
    repeat_threshold = max(2, math.ceil(page_number * 0.2)) if page_number > 1 else 99
    for block in out:
        if block.get("block_type") != "text":
            continue
        text = _normalise_line(block.get("text"))
        page_height = float(block.get("page_height") or 0)
        font_size = float(block.get("font_size") or body_size)
        near_image = any(
            _near_image(block, image, body_size)
            for image in image_by_page.get(block.get("page_number"), [])
        )
        role = "body"
        if ORNAMENT_RE.fullmatch(text):
            role = "ornament"
        elif PAGE_NUMBER_RE.fullmatch(text) and (
            block["y0"] < page_height * 0.12
            or block["y1"] > page_height * 0.88
        ):
            role = "page_number"
        elif bool(CAPTION_CUE_RE.match(text)) and (
            font_size <= body_size * 1.05 and len(text) <= 420
        ):
            role = "caption"
        elif near_image and (
            bool(CAPTION_CUE_RE.match(text))
            or (
                font_size <= body_size * 0.96
                and len(text) <= 420
                and len(block.get("lines") or []) <= 6
            )
        ):
            role = "caption"
        elif (font_size <= body_size * FOOTNOTE_FAST_SIZE_RATIO
              and block["y0"] > page_height * 0.68):
            role = "footnote"
        elif BIBLIOGRAPHY_CUE_RE.match(text) and len(text) <= 700:
            role = "bibliography"
        elif (
            len(text) < 100
            and (repeated[_normalise_repeat(text)] >= repeat_threshold
                 # OCR 层的字号是估出来的、逐行抖动，不能当页眉页脚的证据。
                 or (font_size <= body_size * 0.9
                     and not block.get("ocr_overlay")))
            and (block["y0"] < page_height * 0.12
                 or block["y1"] > page_height * 0.88)
        ):
            role = "header" if block["y0"] < page_height * 0.2 else "footer"
        elif _is_bottom_footnote(block, by_page, body_size, markers, page_height):
            role = "footnote"
        elif (
            (
                font_size >= body_size * 1.18
                # 加粗只有在"整块加粗且不小于正文"时才算标题的证据：见
                # _bold_heading_evidence。单看 font_flags 的 bold 位会让一个加粗
                # span 把整行判成标题（实测：期刊摘要引文的相邻行 flag 4/20 交替、
                # 字号完全相同，整段被逐行判成 heading）。
                or _bold_heading_evidence(block, font_size, body_size)
            )
            and len(text) <= 220
            and len(block.get("lines") or []) <= 4
            and not _is_sentence_terminal(text)
            and (not block.get("ocr_overlay") or _looks_like_title(text))
        ):
            role = "heading"
        elif block["x0"] > body_x0 + body_size * 2.2 and len(text) <= 700:
            role = "quote"
        block["role"] = role
        block["body_x0"] = body_x0
        # 该块自己所属列的左边界。合并判据用它当参照，缩进才有意义——用全文档
        # 左边距会让任何列起点不等于它的内容整列被当成段首缩进。
        block["local_body_x0"] = _local_left_edge(
            block, page_edges, body_x0,
            max(COLUMN_EDGE_SLACK_MIN, body_size * COLUMN_EDGE_SLACK_EM),
        )
        block["body_font_size"] = body_size
        block["near_image"] = near_image
        for line in block.get("lines") or []:
            line["role"] = role
    return out


def _join_text(left: str, right: str) -> str:
    left, right = _normalise_line(left), _normalise_line(right)
    if left.endswith("-") and right[:1].islower():
        return left[:-1] + right
    return f"{left} {right}".strip()


def _line_groups(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Stripping a marker can empty a whole line (a PDF often emits the marker as
    # its own line object).  Those lines must not take their marks with them.
    lines: List[Dict[str, Any]] = []
    carry: List[Dict[str, Any]] = []
    for line in block.get("lines") or []:
        if _normalise_line(line.get("text")):
            if carry:
                line = dict(line)
                line["marks"] = list(carry) + list(line.get("marks") or [])
                carry = []
            lines.append(line)
        else:
            carry.extend(line.get("marks") or [])
    if carry and lines:
        last = dict(lines[-1])
        last["marks"] = list(last.get("marks") or []) + carry
        lines[-1] = last
    if not lines:
        return []
    if block.get("ocr_overlay"):
        return _overlay_rows(block, lines)
    body_x0 = float(block.get("body_x0") or block.get("x0") or 0)
    body_size = float(
        block.get("body_font_size") or block.get("font_size") or 10
    )
    groups: List[List[Dict[str, Any]]] = [[]]
    for line in lines:
        if groups[-1] and line["x0"] >= body_x0 + max(2.0, body_size * 0.14):
            groups.append([])
        groups[-1].append(line)
    result = []
    for group in groups:
        text = _normalise_line(group[0]["text"])
        marks = [dict(item) for item in (group[0].get("marks") or [])]
        for line in group[1:]:
            text = _join_text(text, line["text"])
            marks.extend(dict(item) for item in (line.get("marks") or []))
        result.append(
            {
                "text": text,
                "role": block.get("role") or "body",
                "page_number": block.get("page_number"),
                "block_id": block.get("block_id"),
                "first_line_x0": group[0].get("x0", block.get("x0", 0)),
                "last_y1": group[-1].get("y1", block.get("y1", 0)),
                "page_height": block.get("page_height", 0),
                "marks": marks,
            }
        )
    return result


def _overlay_rows(
    block: Dict[str, Any], lines: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """OCR 层的块：每个视觉行一组，跨行合并交给 :func:`_overlay_continues`。

    OCR 层常把同一视觉行拆成几个"行"对象（逐词定位）；纵向重叠的相邻行先并回
    一行。没有任何字母或数字的行（``©`` ``.`` ``— ]``）是图里纹理的误识别，直接
    丢掉——留着它会把前后本该相连的两行隔开。
    """
    rows: List[Dict[str, Any]] = []
    for line in lines:
        text = _normalise_line(
            _OVERLAY_NOISE_TOKEN_RE.sub(" ", str(line.get("text") or "")))
        if not _ALNUM_RE.search(text):
            continue
        y0, y1 = float(line.get("y0") or 0), float(line.get("y1") or 0)
        if rows:
            last = rows[-1]
            overlap = _overlap(last["y0"], last["y1"], y0, y1)
            if overlap >= 0.5 * min(last["y1"] - last["y0"], y1 - y0) and (
                float(line.get("x0") or 0) >= last["last_x1"] - 1.0
            ):
                last["text"] = _join_text(last["text"], text)
                last["last_x1"] = float(line.get("x1") or 0)
                last["marks"].extend(dict(item) for item in (line.get("marks") or []))
                continue
        rows.append({
            "text": text,
            "role": block.get("role") or "body",
            "page_number": block.get("page_number"),
            "block_id": block.get("block_id"),
            "first_line_x0": float(line.get("x0") or 0),
            "last_x0": float(line.get("x0") or 0),
            "last_x1": float(line.get("x1") or 0),
            "y0": y0,
            "y1": y1,
            "last_y1": y1,
            "page_height": block.get("page_height", 0),
            "marks": [dict(item) for item in (line.get("marks") or [])],
            "ocr_overlay": True,
        })
    return rows


def _overlay_continues(previous: Dict[str, Any], current: Dict[str, Any]) -> bool:
    """OCR 层上，``current`` 这一行是否接着 ``previous`` 那一段。

    只用行距、对齐和句末这几样 OCR 估得准的东西：

    * 段间有空行（行距超过 :data:`OVERLAY_PARAGRAPH_GAP` 个行高）→ 新段；
    * 上一行没结句 → 续行（不看下一行首字母大小写：OCR 常在行首带进噪声字符）；
    * 上一行结句时，居中排版看行距（上面已判）；左对齐看首行缩进和段末短行。
    """
    line_height = float(current.get("line_height") or 0) or 12.0
    if current["page_numbers"][0] != previous["page_numbers"][-1]:
        if current["page_numbers"][0] != previous["page_numbers"][-1] + 1:
            return False
        return not _is_sentence_terminal(previous["text"])
    gap = float(current.get("y0") or 0) - float(previous.get("last_y1") or 0)
    if gap < -line_height:
        return False
    if not _is_sentence_terminal(previous["text"]):
        # 没结句的行后面可以多容一行空白：被丢掉的噪声行（``©`` ``.``）常常
        # 占着一个行位。
        return gap <= line_height * OVERLAY_OPEN_GAP
    if gap > line_height * OVERLAY_PARAGRAPH_GAP:
        return False
    prev_x0, prev_x1 = float(previous["last_x0"]), float(previous["last_x1"])
    cur_x0, cur_x1 = float(current["first_line_x0"]), float(current["last_x1"])
    tolerance = line_height * OVERLAY_CENTRE_TOLERANCE
    # 居中：中点对齐、起点不对齐（起点也对齐的两行只是等宽，按左对齐判即可）。
    centred = (
        abs((prev_x0 + prev_x1) / 2 - (cur_x0 + cur_x1) / 2) <= tolerance
        and abs(prev_x0 - cur_x0) > tolerance
    )
    if centred:
        return True
    if cur_x0 > prev_x0 + line_height * OVERLAY_INDENT:
        return False
    right = float(current.get("page_text_x1") or 0)
    return not (right and prev_x1 < right - line_height * OVERLAY_SHORT_LINE)


def _open_paragraph_behind(
    records: Sequence[Dict[str, Any]], current: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """页底夹层记录之后的续接目标：被它们隔开的、尚未写完的正文段。

    只在换页时启用，且夹层必须是句末收尾的页底小字号记录（漏判的脚注 / 页脚
    之类）；标题不是目标。夹层记录留在原处，也就排在合并后的段落之后。
    """
    last = records[-1]
    if (
        last["page_numbers"][-1] == current["page_numbers"][0]
        or not _is_sentence_terminal(last["text"])
    ):
        return None
    index = len(records) - 1
    while index >= 0 and len(records) - 1 - index < BRIDGE_MAX_RECORDS:
        record = records[index]
        body = float(record.get("body_font_size") or 0)
        height = float(record.get("page_height") or 0)
        small = (
            body > 0 and record["font_size"] <= body * BRIDGE_SIZE_RATIO
            and height > 0 and record["last_y1"] > height * FOOTNOTE_REGION_MIN
            and record["page_numbers"][-1] == last["page_numbers"][-1]
        )
        if not small:
            break
        index -= 1
    else:
        return None
    if index < 0 or index == len(records) - 1:
        return None
    target = records[index]
    if (
        target["role"] not in JOINABLE_ROLES
        or target.get("ocr_overlay")
        or target["page_numbers"][-1] != last["page_numbers"][-1]
        or _is_sentence_terminal(target["text"])
    ):
        return None
    return target


def reconstruct_paragraph_records(
    blocks: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Join only compatible text roles and return text plus layout provenance."""
    records: List[Dict[str, Any]] = []
    ordered_blocks = sorted(
        blocks or [], key=lambda item: item.get("reading_order", 0)
    )
    last_kept_order = None
    for block in ordered_blocks:
        role = block.get("role") or "body"
        if role in SKIPPED_ROLES:
            continue
        current_order = block.get("reading_order", 0)
        intervening_image = any(
            item.get("role") == "image"
            and (last_kept_order is None or
                 last_kept_order < item.get("reading_order", 0) < current_order)
            for item in ordered_blocks
        )
        for group in _line_groups(block):
            if not group["text"] or group["role"] in SKIPPED_ROLES:
                continue
            column_x0 = float(
                block.get("local_body_x0") or block.get("body_x0")
                or block.get("x0") or 0
            )
            current = {
                "text": group["text"],
                "role": group["role"],
                "page_numbers": [group["page_number"]],
                "block_ids": [group["block_id"]],
                "first_line_x0": group["first_line_x0"],
                "last_y1": group["last_y1"],
                "page_height": group["page_height"],
                "reading_order": current_order,
                "column_x0": column_x0,
                "font_size": float(block.get("font_size") or 0),
                "body_font_size": float(block.get("body_font_size") or 0),
                "marks": [dict(item) for item in (group.get("marks") or [])],
            }
            overlay = bool(group.get("ocr_overlay"))
            if overlay:
                current.update({
                    "ocr_overlay": True,
                    "y0": group["y0"],
                    "last_x0": group["last_x0"],
                    "last_x1": group["last_x1"],
                    "line_height": float(block.get("page_line_height") or 0),
                    "page_text_x1": float(block.get("page_text_x1") or 0),
                    "page_text_x0": float(block.get("page_text_x0") or 0),
                })
            if records and overlay and records[-1].get("ocr_overlay"):
                previous = records[-1]
                if (
                    previous["role"] in JOINABLE_ROLES
                    and group["role"] in JOINABLE_ROLES
                    and _overlay_continues(previous, current)
                ):
                    previous["text"] = _join_text(previous["text"], current["text"])
                    previous["page_numbers"].extend(current["page_numbers"])
                    previous["block_ids"].extend(current["block_ids"])
                    previous["marks"].extend(current["marks"])
                    previous["last_y1"] = current["last_y1"]
                    previous["last_x0"] = current["last_x0"]
                    previous["last_x1"] = current["last_x1"]
                    previous["reading_order"] = current_order
                    last_kept_order = current_order
                    continue
                records.append(current)
                last_kept_order = current_order
                continue
            if records:
                previous = records[-1]
                bridged = _open_paragraph_behind(records, current)
                if bridged is not None:
                    previous = bridged
                same_role = (
                    previous["role"] in JOINABLE_ROLES
                    and role in JOINABLE_ROLES
                )
                is_indented = current["first_line_x0"] >= column_x0 + max(
                    2.0, float(block.get("body_font_size") or 10) * 0.14
                )
                # 参照换成列边界之后，"不缩进"不再等于"同一列"。位移量区分两者：
                # 悬挂缩进的续行相对本段首行只右移一个缩进量（实测 1.5em 量级），
                # 而换列会横移上百 pt。没有这条，左栏末行会被接进右栏首行
                # （实测：页 1 的 "Corresponding author" 行接进摘要首行）。
                #
                # 位移量取**绝对值**：换列可能向右（接下一栏）也可能向左（回到上一
                # 栏），阅读顺序并不保证从左到右。原实现只判右移，负数恒成立，于是
                # 实测把右栏的作者单位（x0=419.88）接进了左栏的 "ARTICLE DATA"
                # （x0=54.48，左移 365pt）——同一行文被拼成
                # "…Zagazig University ARTICLE DATA"。
                column_jump = (
                    current["first_line_x0"] - previous["first_line_x0"]
                )
                same_column = abs(column_jump) <= max(
                    COLUMN_JUMP_MIN,
                    float(block.get("body_font_size") or 10) * COLUMN_JUMP_EM,
                )
                page_break = (
                    current["page_numbers"][0]
                    != previous["page_numbers"][-1]
                )
                gap = current["last_y1"] - previous["last_y1"]
                close_enough = page_break or gap <= max(
                    220.0, float(block.get("body_font_size") or 10) * 12
                ) or intervening_image
                # 两条合并路径。第一条第 role/缩进/长度的原口径（**逐字不变**）；
                # 第二条认「确定性断行整理」，用来收掉同段文字因版面被切开、
                # 而 role 口径又够不着的位置（见 _is_obvious_continuation）。
                role_path = (
                    same_role
                    and not is_indented
                    and (len(previous["text"]) > 40 or intervening_image)
                )
                continuation_path = _is_obvious_continuation(previous, current)
                if (
                    (role_path or continuation_path)
                    and same_column
                    and not _is_sentence_terminal(previous["text"])
                    and close_enough
                ):
                    previous["text"] = _join_text(
                        previous["text"], current["text"]
                    )
                    previous["page_numbers"].extend(current["page_numbers"])
                    previous["block_ids"].extend(current["block_ids"])
                    previous["marks"].extend(current["marks"])
                    previous["last_y1"] = current["last_y1"]
                    previous["reading_order"] = current_order
                    last_kept_order = current_order
                    continue
            records.append(current)
            last_kept_order = current_order
    return records


def reconstruct_paragraphs(blocks: Sequence[Dict[str, Any]]) -> List[str]:
    """Reconstruct main-text paragraphs after role classification."""
    return [
        record["text"]
        for record in reconstruct_paragraph_records(blocks)
        if record.get("text") and not ORNAMENT_RE.fullmatch(record["text"])
    ]


def extract_pdf_paragraphs(file_bytes: bytes) -> List[str]:
    """Extract body/heading paragraphs while excluding captions and page furniture."""
    return reconstruct_paragraphs(classify_blocks(extract_layout_blocks(file_bytes)))
