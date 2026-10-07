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
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
MARKER_MIN_ORDER_RATIO = 0.8
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
    """文档级准入：排除数学指数一类的上标数字。"""
    if len(candidates) < MARKER_MIN_COUNT:
        return False
    values = [int(item["text"]) for item in candidates]
    ordered = sum(1 for a, b in zip(values, values[1:]) if b >= a)
    ratio = ordered / max(1, len(values) - 1)
    if ratio < MARKER_MIN_ORDER_RATIO:
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
                        "relative_width": block_box["width"] / width if width else 0.0,
                        "page_width": width,
                        "page_height": height,
                        "text": text,
                        "lines": line_records,
                        "marks": [],
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
        block["text"] = _normalise_line(
            " ".join(line["text"] for line in lines)
        )
        block["marks"] = [dict(item) for line in lines
                          for item in (line.get("marks") or [])]


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
    value = re.sub(r"[\]\)}」』”’]+$", "", value).rstrip()
    if ABBREV_RE.search(value[-12:]):
        return False
    return value[-1] in SENTENCE_TERMINAL


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
        if block.get("block_type") == "image":
            image_by_page[int(block.get("page_number") or 0)].append(block)
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
        elif font_size <= body_size * 0.78 and block["y0"] > page_height * 0.68:
            role = "footnote"
        elif BIBLIOGRAPHY_CUE_RE.match(text) and len(text) <= 700:
            role = "bibliography"
        elif (
            len(text) < 100
            and (repeated[_normalise_repeat(text)] >= repeat_threshold
                 or font_size <= body_size * 0.9)
            and (block["y0"] < page_height * 0.12
                 or block["y1"] > page_height * 0.88)
        ):
            role = "header" if block["y0"] < page_height * 0.2 else "footer"
        elif (
            (font_size >= body_size * 1.18 or block.get("font_flags", 0) & 16)
            and len(text) <= 220
            and len(block.get("lines") or []) <= 4
            and not _is_sentence_terminal(text)
        ):
            role = "heading"
        elif block["x0"] > body_x0 + body_size * 2.2 and len(text) <= 700:
            role = "quote"
        block["role"] = role
        block["body_x0"] = body_x0
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
            current = {
                "text": group["text"],
                "role": group["role"],
                "page_numbers": [group["page_number"]],
                "block_ids": [group["block_id"]],
                "first_line_x0": group["first_line_x0"],
                "last_y1": group["last_y1"],
                "page_height": group["page_height"],
                "reading_order": current_order,
                "marks": [dict(item) for item in (group.get("marks") or [])],
            }
            if records:
                previous = records[-1]
                same_role = (
                    previous["role"] in JOINABLE_ROLES
                    and role in JOINABLE_ROLES
                )
                body_x0 = float(
                    block.get("body_x0") or block.get("x0") or 0
                )
                is_indented = current["first_line_x0"] >= body_x0 + max(
                    2.0, float(block.get("body_font_size") or 10) * 0.14
                )
                page_break = (
                    current["page_numbers"][0]
                    != previous["page_numbers"][-1]
                )
                gap = current["last_y1"] - previous["last_y1"]
                close_enough = page_break or gap <= max(
                    220.0, float(block.get("body_font_size") or 10) * 12
                ) or intervening_image
                if (
                    same_role
                    and not is_indented
                    and not _is_sentence_terminal(previous["text"])
                    and (len(previous["text"]) > 40 or intervening_image)
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
