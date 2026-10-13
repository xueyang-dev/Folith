"""DocumentIR v1: provider-neutral semantics and independently owned source identity.

All persisted values are JSON mappings. Coordinates are top-left normalized in
unrotated PDF page space; renderers alone convert them to pixels/PDF points.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Protocol

BLOCK_TYPES = frozenset('title heading paragraph list_item table figure caption footnote header footer equation code page_number unknown'.split())
GROUNDING_QUALITIES = frozenset('exact strong approximate page_only unresolved'.split())
GROUNDING_METHODS = frozenset('native_pdf local_text_layer spatial_ocr layout_detector region_alignment vlm_bbox page_only'.split())


def normalized_bbox(values, width=1.0, height=1.0):
    """Reject unknown/invalid geometry instead of clamping it into a fake box."""
    if values is None:
        return None
    try:
        x0, y0, x1, y1 = map(float, values)
        width, height = float(width), float(height)
        if width <= 0 or height <= 0:
            return None
        box = [x0 / width, y0 / height, x1 / width, y1 / height]
        if not all(math.isfinite(x) and 0 <= x <= 1 for x in box):
            return None
        if box[0] >= box[2] or box[1] >= box[3]:
            return None
        return box
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SourceSpan:
    page_id: str
    block_id: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    bbox: list[float] | None = None
    region_id: str | None = None
    grounding_method: str = 'page_only'
    grounding_quality: str = 'page_only'

    def __post_init__(self):
        if self.grounding_method not in GROUNDING_METHODS or self.grounding_quality not in GROUNDING_QUALITIES:
            raise ValueError('Invalid source grounding')
        if self.bbox is not None and normalized_bbox(self.bbox) is None:
            raise ValueError('Invalid normalized source bbox')
        if self.bbox is not None and self.grounding_quality in {'page_only', 'unresolved'}:
            raise ValueError('Page-only/unresolved grounding cannot claim a region')
        if self.bbox is None and self.grounding_quality in {'exact', 'strong', 'approximate'}:
            raise ValueError('Region grounding requires real geometry')


@dataclass(frozen=True)
class PageEnvelope:
    document_id: str
    page_id: str
    page_index: int
    width: float
    height: float
    rotation: int = 0
    display_page_number: str | None = None
    source_artifact: str = 'source.bin'
    image_width: int | None = None
    image_height: int | None = None
    # No image hash: this path does not use one to avoid an expensive operation.
    image: bytes = field(default=b'', repr=False)

    def to_page(self):
        return {key: value for key, value in asdict(self).items()
                if key not in {'image', 'image_width', 'image_height', 'document_id'}}


@dataclass
class SemanticBlock:
    type: str
    text: str
    region_ids: list[str] = field(default_factory=list)
    language: str | None = None
    direction: str | None = None
    parent_id: str | None = None
    relations: list[dict] = field(default_factory=list)
    cells: list[dict] = field(default_factory=list)
    # 翻页续段（视觉模型看缩进与满行给出；None = 没给，交给文字规则判断）。
    continues_previous: bool | None = None
    continues_next: bool | None = None


@dataclass
class PageUnderstanding:
    blocks: list[SemanticBlock]
    provider: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


class DocumentUnderstandingProvider(Protocol):
    def understand_page(self, page: PageEnvelope) -> PageUnderstanding: ...


def new_document(document_id, filename):
    return {'version': 1, 'metadata': {'document_id': document_id, 'filename': filename},
            'pages': [], 'blocks': [], 'assets': [], 'references': [],
            'provenance': {}, 'translation_block_ids': []}


def segment_spans(document, paragraphs, segments, metadata):
    """Project paragraph membership through splitting without matching OCR text.

    Spans refer to original semantic blocks. A split uses the containing source
    regions; no exact subregion is claimed. Multiple spans survive merges.
    """
    ids = document.get('translation_block_ids') or []
    blocks = {b['block_id']: b for b in document.get('blocks') or []}
    result = [[] for _ in segments]
    for row in metadata.get('paragraph_ranges') or []:
        pi = row['paragraph_index']
        if pi >= len(ids):
            continue
        block = blocks.get(ids[pi]) or {}
        # Native cleanup can merge/rewrite paragraphs. Do not assign stale
        # positional provenance when this boundary no longer matches.
        if pi >= len(paragraphs) or paragraphs[pi] != block.get('text'):
            continue
        cursor = 0
        for si in range(row['start'], row['end']):
            result[si] = [dict(span) for span in (block.get('source_anchor') or {}).get('source_spans') or []]
            start = block['text'].find(segments[si], cursor)
            if start >= 0:
                cursor = start + len(segments[si])
                # These offsets index semantic block text, never rough OCR.
                for span in result[si]:
                    if span.get('block_id') == block['block_id']:
                        span['char_start'], span['char_end'] = start, cursor

    return result


def segment_marks(document, spans_by_segment, segments=None):
    """Project block-level footnote marks onto segments — exactly once each.

    Blocks are joined on ``block_id`` rather than on character offsets: native
    cleanup can merge or rewrite paragraph text, and offsets already degrade to
    ``None`` in that case, while block identity survives.

    A block's text is sliced into several segments when a paragraph splits, so
    handing every referencing segment the block's marks duplicated each mark
    once per sibling.  When ``segments`` is supplied the mark's ``text_offset``
    selects the single segment whose slice of the block text contains it; marks
    whose block no segment references stay unassigned rather than being copied.
    """
    blocks = {b.get('block_id'): b for b in document.get('blocks') or []}
    result = [[] for _ in spans_by_segment or []]
    if not segments:
        for index, spans in enumerate(spans_by_segment or []):
            found = []
            seen = set()
            for span in spans or []:
                block_id = span.get('block_id')
                if not block_id or block_id in seen:
                    continue
                seen.add(block_id)
                for mark in (blocks.get(block_id) or {}).get('marks') or []:
                    item = dict(mark)
                    item['block_id'] = block_id
                    found.append(item)
            result[index] = found
        return result

    by_block = {}
    for index, spans in enumerate(spans_by_segment or []):
        for span in spans or []:
            block_id = span.get('block_id')
            if block_id:
                by_block.setdefault(block_id, [])
                if index not in by_block[block_id]:
                    by_block[block_id].append(index)

    for block_id, indexes in by_block.items():
        marks = (blocks.get(block_id) or {}).get('marks') or []
        if not marks:
            continue
        ordered = sorted(indexes)
        ranges = []
        cursor = 0
        for index in ordered:
            length = len(segments[index] or '') if index < len(segments) else 0
            ranges.append((cursor, cursor + length))
            cursor += length
        for mark in marks:
            offset = mark.get('text_offset')
            chosen = None
            if isinstance(offset, int):
                for index, (start, end) in zip(ordered, ranges):
                    if start <= offset < end:
                        chosen = index
                        break
                if chosen is None:
                    # Past the end (or before the start): clamp inside the block.
                    chosen = ordered[-1] if offset >= ranges[-1][1] else ordered[0]
            if chosen is None:
                chosen = ordered[0]
            item = dict(mark)
            item['block_id'] = block_id
            result[chosen].append(item)
    return result


def attach_pair_spans(state):
    """Pairs own anchors once translations exist, including through human edits."""
    spans = state.get('segment_source_spans') or []
    marks = state.get('segment_source_marks') or []
    for index, pair in enumerate(state.get('pairs') or []):
        if 'source_spans' not in pair and index < len(spans):
            pair['source_spans'] = [dict(s) for s in spans[index]]
        if 'marks' not in pair and index < len(marks) and marks[index]:
            pair['marks'] = [dict(m) for m in marks[index]]
