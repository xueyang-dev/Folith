"""Source geometry and slot layout for the original-layout renderer.

The unit of layout is the **source layout block**: a native PDF paragraph owns
one or more rectangular regions on the page, and every sentence segment split
out of that paragraph maps back onto the same region(s).  Sentence splitting is
therefore a many-to-one relation (many segments, one region) while a merged or
cross-page paragraph is one-to-many.  Both fall out of the same model below:

1. each translation pair resolves to source regions (persisted ``source_spans``
   first, then a strict literal search of the source text);
2. a pair's target text is split across its regions in proportion to the source
   text found in each, so nothing is duplicated on a second page;
3. pieces that overlap on a page merge into one slot in reading order;
4. a slot may grow downwards into the empty gap before the next slot.

Nothing here guesses geometry.  A region is only usable when it carries verified
grounding (``exact``/``strong``/``approximate``) or when the whole source text
was literally found on the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

#: Grounding qualities that carry real geometry.
VERIFIED_QUALITIES = frozenset({"exact", "strong", "approximate"})

#: Grounding methods whose text exists as real PDF text objects, so it can be
#: removed by redaction.  Everything else (VLM boxes over page pixels, OCR
#: regions) has to be covered instead.  A page-level "does it have a text
#: layer" test is not enough: a page can mix native text with a scan region, and
#: a page whose only text is a short heading still has text objects.
NATIVE_TEXT_METHODS = frozenset({"native_pdf", "text_search"})

#: A literal search must recover at least this share of the pair's source text
#: before the match may be used as a region.  Below it we refuse to erase.
SEARCH_COVERAGE_MIN = 0.90

#: Two rects join one slot when their intersection covers this share of the
#: smaller one (a sentence split shares its paragraph block exactly).
OVERLAP_JOIN = 0.25

#: Cap on how far a slot may grow past its own source box, as a multiple of the
#: source box height, so one short heading cannot swallow a whole page.
GROWTH_CAP = 3.0

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def rect_of(bbox, width: float, height: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = bbox
    return (x0 * width, y0 * height, x1 * width, y1 * height)


def _intersection(a, b) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _area(rect) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _union(rects):
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def _overlaps(a, b) -> bool:
    shared = _intersection(a, b)
    if shared <= 0:
        return False
    smaller = min(_area(a), _area(b))
    return smaller > 0 and shared / smaller >= OVERLAP_JOIN


# ---------------------------------------------------------------------------
# Source page model
# ---------------------------------------------------------------------------

@dataclass
class LineBox:
    rect: tuple[float, float, float, float]
    text: str
    size: float
    flags: int
    color: tuple[float, float, float]
    font: str
    vertical: bool = False


@dataclass
class SourcePage:
    index: int
    width: float
    height: float
    rotation: int
    lines: list[LineBox]
    has_text: bool


class SourceDocument:
    """Read-only view of the source PDF in unrotated page space."""

    def __init__(self, data: bytes):
        self._doc = pymupdf.open(stream=data, filetype="pdf")
        self.pages: list[SourcePage] = []
        for index, page in enumerate(self._doc):
            rotation = page.rotation
            if rotation:
                page.set_rotation(0)
            rect = page.rect
            lines: list[LineBox] = []
            try:
                raw = page.get_text("dict")
            except Exception:
                raw = {"blocks": []}
            for block in raw.get("blocks") or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines") or []:
                    spans = [s for s in (line.get("spans") or []) if str(s.get("text") or "").strip()]
                    if not spans:
                        continue
                    text = "".join(str(s.get("text") or "") for s in spans)
                    sizes = [float(s.get("size") or 0) for s in spans]
                    flags = 0
                    for span in spans:
                        flags |= int(span.get("flags") or 0)
                    color = (0.0, 0.0, 0.0)
                    for span in spans:
                        value = int(span.get("color") or 0)
                        if value:
                            color = (((value >> 16) & 255) / 255.0,
                                     ((value >> 8) & 255) / 255.0,
                                     (value & 255) / 255.0)
                            break
                    direction = line.get("dir") or (1.0, 0.0)
                    lines.append(LineBox(
                        rect=tuple(float(v) for v in line["bbox"]),
                        text=text,
                        size=sorted(s for s in sizes if s)[len(sizes) // 2] if sizes else 10.0,
                        flags=flags,
                        color=color,
                        font=str(spans[0].get("font") or ""),
                        vertical=abs(float(direction[1])) > abs(float(direction[0])),
                    ))
            self.pages.append(SourcePage(
                index=index, width=rect.width, height=rect.height,
                rotation=rotation, lines=lines,
                has_text=bool("".join(l.text for l in lines).strip())))

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass

    def lines_in(self, page_index: int, rect) -> list[LineBox]:
        page = self.pages[page_index]
        found = []
        for line in page.lines:
            centre = ((line.rect[0] + line.rect[2]) / 2, (line.rect[1] + line.rect[3]) / 2)
            if (rect[0] - 2 <= centre[0] <= rect[2] + 2
                    and rect[1] - 2 <= centre[1] <= rect[3] + 2):
                found.append(line)
        found.sort(key=lambda l: (round(l.rect[1], 1), l.rect[0]))
        return found


# ---------------------------------------------------------------------------
# Regions and items
# ---------------------------------------------------------------------------

@dataclass
class Region:
    page_index: int
    rect: tuple[float, float, float, float]
    method: str
    quality: str
    block_id: str | None = None
    source_text: str = ""
    #: text recovered on this specific page for a cross-page pair
    page_source_text: str = ""


@dataclass
class Item:
    """One translation pair bound to the regions it came from."""

    segment_index: int
    target: str
    source: str
    regions: list[Region]
    size: float = 10.0
    font: str = ""
    flags: int = 0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    direction: str = "ltr"
    align: str = "start"
    block_type: str = "paragraph"
    line_height: float = 1.22
    size_source: str = "span"      # 'span' | 'line' | 'default'
    skipped_reason: str = ""


@dataclass
class Piece:
    item: Item
    region: Region
    text: str


@dataclass
class Slot:
    page_index: int
    box: tuple[float, float, float, float]
    source_box: tuple[float, float, float, float]
    pieces: list[Piece] = field(default_factory=list)
    limit_bottom: float = 0.0
    regions: list[Region] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(_norm(p.text) for p in self.pieces if _norm(p.text))


def _style_from_lines(lines: list[LineBox], fallback_size: float) -> dict:
    if not lines:
        return {"size": fallback_size, "font": "", "flags": 0,
                "color": (0.0, 0.0, 0.0), "size_source": "default",
                "line_height": 1.22}
    sizes = sorted(l.size for l in lines if l.size > 0)
    size = sizes[len(sizes) // 2] if sizes else fallback_size
    fonts: dict[str, int] = {}
    for line in lines:
        if line.font:
            fonts[line.font] = fonts.get(line.font, 0) + len(line.text)
    font = max(fonts, key=fonts.get) if fonts else ""
    flags = 0
    for line in lines:
        flags |= line.flags
    color = lines[0].color
    return {"size": size, "font": font, "flags": flags, "color": color,
            "size_source": "line", "line_height": _leading_from_lines(lines, size)}


def _leading_from_lines(lines: list[LineBox], size: float) -> float:
    """Measured baseline-to-baseline leading as a multiple of the font size.

    Re-typeset text has to occupy roughly the vertical band the source text
    occupied, otherwise every paragraph "overflows" purely because the default
    leading is looser than the document's.
    """
    if size <= 0 or len(lines) < 2:
        return 1.22
    ordered = sorted(lines, key=lambda l: l.rect[1])
    deltas = [ordered[i + 1].rect[3] - ordered[i].rect[3]
              for i in range(len(ordered) - 1)]
    deltas = [d for d in deltas if 0 < d < size * 3]
    if not deltas:
        return 1.22
    deltas.sort()
    median = deltas[len(deltas) // 2]
    return max(1.0, min(1.8, median / size))


def _align_from_lines(lines: list[LineBox]) -> str:
    if len(lines) < 2:
        return "start"
    body = lines[:-1]
    if body:
        body_rights = [l.rect[2] for l in body]
        body_lefts = [l.rect[0] for l in body]
        if (max(body_rights) - min(body_rights) <= 2.0
                and max(body_lefts) - min(body_lefts) <= 2.0
                and len(lines) >= 3):
            return "justify"
        if (max(body_lefts) - min(body_lefts) <= 1.5
                and max(body_rights) - min(body_rights) > 8.0):
            return "start"
    centres = [(l.rect[0] + l.rect[2]) / 2 for l in lines]
    if max(centres) - min(centres) <= 2.0:
        return "center"
    return "start"


# ---------------------------------------------------------------------------
# Splitting a pair's target text across the regions it came from
# ---------------------------------------------------------------------------

#: Characters a proportional character-level cut prefers to fall after.
CLAUSE_MARKS = "。！？；，、.!?;:）)】」》\u060c\u061b\u061f"


def split_by_weights(text: str, weights: list[float]) -> list[str]:
    """Split ``text`` into ``len(weights)`` non-empty parts.

    Whitespace-separated words are used when there are enough of them.  A CJK or
    Arabic sentence is often a single whitespace token, so in that case the cut
    is made between characters, preferring a clause mark near the proportional
    boundary.  Every part is non-empty and concatenating them reproduces the
    input, because a part that received no text would mean a source region was
    erased and left blank.
    """
    if len(weights) <= 1:
        return [text.strip()]
    parts = text.split()
    total = sum(weights) or float(len(weights))
    if len(parts) >= len(weights):
        counts: list[int] = []
        remaining = len(parts)
        for index, weight in enumerate(weights):
            if index == len(weights) - 1:
                counts.append(remaining)
                break
            share = int(round(len(parts) * (weight / total)))
            take = max(1, min(share, remaining - (len(weights) - index - 1)))
            counts.append(take)
            remaining -= take
        out = []
        cursor = 0
        for count in counts:
            out.append(" ".join(parts[cursor:cursor + count]))
            cursor += count
        return out

    stripped = text.strip()
    length = len(stripped)
    count = len(weights)
    if length < count:
        # More regions than characters: give the text to the first region and
        # tell the caller the rest have nothing rather than erasing them.
        return [stripped] + [""] * (count - 1)
    cuts = [0]
    for index in range(count - 1):
        # Measure from what is left, so an earlier clause-aligned cut cannot
        # starve the parts that follow it.
        remaining = length - cuts[-1]
        tail_weight = sum(weights[index:]) or 1.0
        target = cuts[-1] + int(round(remaining * (weights[index] / tail_weight)))
        target = max(cuts[-1] + 1, min(target, length - (count - index - 1)))
        chosen = None
        for delta in range(0, 7):
            for candidate in (target + delta, target - delta):
                if cuts[-1] < candidate < length and stripped[candidate - 1] in CLAUSE_MARKS:
                    chosen = candidate
                    break
            if chosen:
                break
        cuts.append(chosen or target)
    cuts.append(length)
    return [stripped[cuts[i]:cuts[i + 1]].strip() for i in range(count)]


# ---------------------------------------------------------------------------
# Pair -> items
# ---------------------------------------------------------------------------

def _document_pages(state) -> dict:
    document = (state.get("extraction_report") or {}).get("document_ir") or {}
    return {page["page_id"]: page for page in document.get("pages") or []}


def literal_regions(source: SourceDocument, text: str, *, start_page: int = 0):
    """Find the whole source text literally on a page.

    Returns ``(page_index, rects, coverage)`` or ``None``.  Only used when the
    persisted spans carry no usable geometry; a partial match is rejected
    because partially located text must not be erased.
    """
    needle = _norm(text)
    if len(needle) < 8:
        return None
    order = list(range(start_page, len(source.pages))) + list(range(0, start_page))
    for page_index in order:
        page = source.pages[page_index]
        if not page.has_text:
            continue
        rects = page_rects_for(source, page_index, needle)
        if rects:
            joined = _norm(" ".join(
                _norm(l.text) for l in source.lines_in(page_index, _union(rects))))
            coverage = len(needle) / max(1, len(joined))
            if joined.startswith(needle) or needle in joined:
                coverage = min(1.0, len(needle) / max(len(needle), len(joined)))
            if coverage >= SEARCH_COVERAGE_MIN:
                return page_index, rects, coverage
    return None


def page_rects_for(source: SourceDocument, page_index: int, needle: str):
    """Line rects on ``page_index`` whose joined text contains ``needle``."""
    page = source.pages[page_index]
    joined = ""
    spans = []
    for line in page.lines:
        piece = _norm(line.text)
        if not piece:
            continue
        start = len(joined) + (1 if joined else 0)
        joined = f"{joined} {piece}".strip()
        spans.append((start, start + len(piece), line.rect))
    position = joined.find(needle)
    if position < 0:
        return []
    end = position + len(needle)
    rects = [rect for start, stop, rect in spans if start < end and stop > position]
    return rects


def _line_text_map(source: SourceDocument, page_index: int, rect):
    """Map block text offsets to the PDF lines inside ``rect``."""
    lines = source.lines_in(page_index, rect)
    joined = ""
    spans = []
    for line in lines:
        piece = _norm(line.text)
        if not piece:
            continue
        start = len(joined) + (1 if joined else 0)
        joined = f"{joined} {piece}".strip()
        spans.append((start, start + len(piece), line))
    return joined, spans


def build_items(state, source: SourceDocument, *, mode: str = "translated") -> list[Item]:
    """Resolve every translation pair to source regions and a layout style."""
    pairs = state.get("pairs") or []
    fallback_spans = state.get("segment_source_spans") or []
    pages = _document_pages(state)
    items: list[Item] = []

    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            continue
        source_text = str(pair.get("source") or "")
        if mode == "same_text":
            target = source_text
        else:
            target = str(pair.get("target") or "")
        spans = pair.get("source_spans")
        if spans is None and index < len(fallback_spans):
            spans = fallback_spans[index]
        spans = spans or []

        regions: list[Region] = []
        for span in spans:
            page = pages.get(str(span.get("page_id") or ""))
            if not page:
                continue
            quality = str(span.get("grounding_quality") or "")
            bbox = span.get("bbox")
            if quality in VERIFIED_QUALITIES and bbox:
                try:
                    rect = rect_of(bbox, float(page["width"]), float(page["height"]))
                except (TypeError, ValueError):
                    continue
                if rect[2] - rect[0] < 0.5 or rect[3] - rect[1] < 0.5:
                    continue
                regions.append(Region(
                    page_index=int(page["page_index"]), rect=rect,
                    method=str(span.get("grounding_method") or "span"),
                    quality=quality,
                    block_id=span.get("block_id"),
                    page_source_text=source_text))

        if not regions and source_text.strip():
            # No persisted geometry at all: a strict literal search of the whole
            # segment is the only remaining evidence, and it must cover the full
            # text or we refuse the region.
            hint = 0
            hit = literal_regions(source, source_text, start_page=hint)
            if hit:
                page_index, rects, _coverage = hit
                box = _union(rects)
                regions.append(Region(
                    page_index=page_index, rect=box, method="text_search",
                    quality="approximate", block_id=None,
                    page_source_text=source_text))

        item = Item(
            segment_index=index, target=target, source=source_text,
            regions=regions,
            direction="ltr",
            block_type="paragraph",
        )
        # Style and direction come from the source PDF, not from DocumentIR:
        # ingestion never persisted colour, alignment or line geometry.
        if regions:
            first = regions[0]
            lines = source.lines_in(first.page_index, first.rect)
            style = _style_from_lines(lines, 10.0)
            item.size = style["size"]
            item.font = style["font"]
            item.flags = style["flags"]
            item.color = style["color"]
            item.size_source = style["size_source"]
            item.line_height = style["line_height"]
            item.align = _align_from_lines(lines)
        from .typeset import dominant_direction
        item.direction = dominant_direction(item.target or item.source)
        if not regions:
            item.skipped_reason = "no_verified_region"
        elif not _norm(target):
            item.skipped_reason = "empty_target"
        items.append(item)

    _split_cross_region(items, source)
    return items


def _split_cross_region(items: list[Item], source: SourceDocument) -> None:
    """Give each item one text piece per region, proportioned by source volume."""
    for item in items:
        if len(item.regions) <= 1 or not item.regions:
            if item.regions:
                item.regions[0].source_text = item.source
            continue
        weights = []
        for region in item.regions:
            lines = source.lines_in(region.page_index, region.rect)
            weights.append(max(1.0, float(len("".join(l.text for l in lines)))))
        pieces = split_by_weights(item.target, weights)
        for region, piece in zip(item.regions, pieces):
            region.source_text = piece


def _band_is_contiguous(last, region) -> bool:
    """True when two regions are stacked lines of the same paragraph."""
    if last.page_index != region.page_index:
        return False
    shared = _intersection_horizontal(last.rect, region.rect)
    narrower = min(last.rect[2] - last.rect[0], region.rect[2] - region.rect[0])
    if narrower <= 0 or shared < 0.5 * narrower:
        return False
    if last.block_id and region.block_id and last.block_id == region.block_id:
        return True
    height = max(1.0, last.rect[3] - last.rect[1])
    return region.rect[1] - last.rect[3] <= max(2.0, 0.5 * height)


def merge_bands(regions: list[Region]) -> list[tuple]:
    """Merge stacked regions of one paragraph into a single layout band.

    A paragraph reconstructed as one region per source line must be typeset as
    one block.  Treating each line as its own slot re-typesets every line
    separately and scrambles the reading order into a column of fragments, which
    is not something a reader should have to repair by hand.
    """
    ordered = sorted(regions, key=lambda r: (r.page_index, round(r.rect[1], 1),
                                             round(r.rect[0], 1)))
    bands: list[list[Region]] = []
    for region in ordered:
        if bands and _band_is_contiguous(bands[-1][-1], region):
            bands[-1].append(region)
        else:
            bands.append([region])
    return [(_union([r.rect for r in band]), band) for band in bands]


def expand_pieces(items: list[Item]) -> list[Piece]:
    """One piece per (item, layout band); the piece carries its share of text.

    Text is only ever divided between regions that are genuinely separate — a
    different page, or a different column.  Lines stacked in one column become a
    single band that receives the whole translation.
    """
    pieces: list[Piece] = []
    for item in items:
        if not item.regions or not _norm(item.target):
            continue
        bands = merge_bands(item.regions)
        if len(bands) == 1:
            rect, members = bands[0]
            merged = Region(page_index=members[0].page_index, rect=rect,
                            method=members[0].method, quality=members[0].quality,
                            block_id=members[0].block_id)
            pieces.append(Piece(item=item, region=merged, text=item.target))
            continue
        weights = []
        for rect, members in bands:
            weights.append(max(1.0, sum(float(len(r.source_text or " "))
                                        for r in members)))
        parts = split_by_weights(item.target, weights)
        for (rect, members), part in zip(bands, parts):
            if not _norm(part):
                continue
            merged = Region(page_index=members[0].page_index, rect=rect,
                            method=members[0].method, quality=members[0].quality,
                            block_id=members[0].block_id)
            pieces.append(Piece(item=item, region=merged, text=part))
    return pieces


def build_slots(pieces: list[Piece], source: SourceDocument) -> list[Slot]:
    """Merge pieces into per-page slots and compute how far each may grow."""
    by_page: dict[int, list[Piece]] = {}
    for piece in pieces:
        by_page.setdefault(piece.region.page_index, []).append(piece)

    slots: list[Slot] = []
    for page_index, page_pieces in sorted(by_page.items()):
        page_pieces.sort(key=lambda p: (round(p.region.rect[1], 1),
                                        round(p.region.rect[0], 1),
                                        p.item.segment_index))
        groups: list[list[Piece]] = []
        group_rects: list[list[tuple]] = []
        for piece in page_pieces:
            placed = False
            for group, rects in zip(groups, group_rects):
                if any(_overlaps(piece.region.rect, rect) for rect in rects):
                    group.append(piece)
                    rects.append(piece.region.rect)
                    placed = True
                    break
            if not placed:
                groups.append([piece])
                group_rects.append([piece.region.rect])
        page = source.pages[page_index]
        for group, rects in zip(groups, group_rects):
            union = _union(rects)
            slots.append(Slot(
                page_index=page_index, box=union, source_box=union,
                pieces=sorted(group, key=lambda p: (round(p.region.rect[1], 1),
                                                    round(p.region.rect[0], 1),
                                                    p.item.segment_index)),
                regions=[p.region for p in group],
                limit_bottom=page.height,
            ))

    by_page_slots: dict[int, list[Slot]] = {}
    for slot in slots:
        by_page_slots.setdefault(slot.page_index, []).append(slot)
    for page_index, page_slots in by_page_slots.items():
        page = source.pages[page_index]
        page_slots.sort(key=lambda s: (round(s.source_box[1], 1), round(s.source_box[0], 1)))
        for slot in page_slots:
            slot.box = widen_to_column(page, slot)
        for i, slot in enumerate(page_slots):
            limit = page.height
            for other in page_slots:
                if other is slot:
                    continue
                if other.source_box[1] <= slot.source_box[1] + 0.5:
                    continue
                if _intersection_horizontal(other.source_box, slot.source_box) <= 0:
                    continue
                limit = min(limit, other.source_box[1])
            # Fingerprint footers/page numbers below the last content slot.
            limit = min(limit, slot.source_box[1] + max(
                slot.source_box[3] - slot.source_box[1], 12.0) * GROWTH_CAP)
            slot.limit_bottom = max(limit, slot.source_box[3])
            slot.box = (slot.box[0], slot.source_box[1],
                        slot.box[2], slot.limit_bottom)
    return slots


def widen_to_column(source_page: SourcePage, slot: Slot) -> tuple:
    """Give a narrow block the width of the column it sits in.

    A heading such as "3.2 Attention" owns a block box only as wide as the two
    words, but its translation needs the column.  The column width is read from
    the source lines that share this block's left margin in the same vertical
    band; a line that starts to the right of the block bounds how far it may
    reach.  The return value never shrinks the original box.
    """
    x0, y0, x1, y1 = slot.source_box
    band = [line for line in source_page.lines
            if line.rect[1] >= y0 - 2.0 and line.rect[1] <= y1 + 40.0]
    right = x1
    for line in band:
        if line.rect[0] <= x0 + 6.0:
            right = max(right, line.rect[2])
    boundary = None
    for line in band:
        if line.rect[0] > x1 + 3.0:
            boundary = line.rect[0] if boundary is None else min(boundary, line.rect[0])
    if boundary is not None:
        right = min(right, boundary)
    right = max(x1, min(right, source_page.width - 6.0))
    return (x0, y0, right, y1)


def _intersection_horizontal(a, b) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


# ---------------------------------------------------------------------------
# Page-level vertical resolution
# ---------------------------------------------------------------------------

def horizontal_overlap(a, b) -> float:
    """Width of the shared horizontal band, in points."""
    return min(a[2], b[2]) - max(a[0], b[0])


def x_overlap_ratio(a, b) -> float:
    """Horizontal overlap as a share of the narrower rectangle."""
    shared = horizontal_overlap(a, b)
    if shared <= 0:
        return 0.0
    narrower = min(a[2] - a[0], b[2] - b[0])
    return shared / narrower if narrower > 0 else 0.0


def reading_order(slots: list) -> list:
    return sorted(slots, key=lambda s: (round(s.source_box[1], 1),
                                        round(s.source_box[0], 1)))


def page_bottom_limit(source_page: SourcePage, slots: list) -> float:
    """Lowest y a re-typeset block may reach on this page.

    Stops above a detected page footer (a source line that sits clearly below
    the last replaced region) and otherwise keeps a normal bottom margin.
    """
    limit = source_page.height - 12.0
    if not slots:
        return limit
    lowest = max(slot.source_box[3] for slot in slots)
    footer_tops = [line.rect[1] for line in source_page.lines
                   if line.rect[1] > lowest + 6.0
                   and line.rect[1] > source_page.height * 0.75]
    if footer_tops:
        limit = min(limit, min(footer_tops) - 3.0)
    return max(limit, lowest)
