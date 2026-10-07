"""Cheap local source geometry; rough OCR text is alignment evidence only."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import unicodedata

from folith.document_ir import SourceSpan, normalized_bbox


@dataclass(frozen=True)
class SpatialRegion:
    region_id: str
    bbox: list[float]
    rough_text: str
    reading_order: int


@dataclass
class SpatialTextIndex:
    page_id: str
    regions: list[SpatialRegion] = field(default_factory=list)
    error: str | None = None

    @classmethod
    def from_ocr(cls, page, blocks, *, image_width, image_height):
        regions = []
        for block in blocks:
            raw = block.to_contract_dict() if hasattr(block, 'to_contract_dict') else block
            for line in raw.get('lines') or [raw]:
                if not line.get('geometry_known', raw.get('geometry_known', True)):
                    continue
                box = normalized_bbox([line.get(k) for k in ('left', 'top', 'right', 'bottom')], image_width, image_height)
                if box:
                    regions.append(SpatialRegion(str(len(regions) + 1), box,
                                                 str(line.get('text') or ''), len(regions)))
        return cls(page.page_id, regions)


def alignment_text(text):
    value = unicodedata.normalize('NFKC', str(text or '')).casefold()
    # Remove Arabic marks/tatweel, punctuation and spacing only for matching.
    return ''.join(c for c in value if c.isalnum() and c != '\u0640')


def align_blocks(page, blocks, index):
    """Match contiguous local regions, abstaining on ambiguous/weak evidence.

    Region IDs from the semantic provider are usable only when present in the
    actual local index. No model call is needed for alignment. A failed index
    yields honest page-only spans and never prevents semantic translation.
    """
    regions = index.regions
    known = {r.region_id: r for r in regions}
    cursor = 0
    anchors = []
    for block in blocks:
        selected = []
        quality = 'approximate'
        method = 'region_alignment'
        if block.region_ids and all(r in known for r in block.region_ids):
            selected = [known[r] for r in block.region_ids]
        else:
            needle = alignment_text(block.text)
            candidates = []
            if len(needle) >= 8:
                for start in range(cursor, len(regions)):
                    joined = ''
                    for end in range(start, len(regions)):
                        joined += alignment_text(regions[end].rough_text)
                        if len(joined) > len(needle) * 1.8:
                            break
                        if len(joined) < len(needle) * .6:
                            continue
                        score = SequenceMatcher(None, needle, joined, autojunk=False).ratio()
                        candidates.append((score, start, end + 1))
                candidates.sort(reverse=True)
                # The measured historical scan has unique paragraph matches
                # at .70-.75 despite damaged OCR. Long matches can honestly be
                # approximate; short text needs the stricter threshold.
                threshold = .68 if len(needle) >= 24 else .78
                if candidates and candidates[0][0] >= threshold:
                    best = candidates[0]
                    # Overlapping windows identify the same region. Competing
                    # disjoint near-ties are ambiguous, especially repeated text.
                    ambiguous = any(c[0] >= best[0] - .04 and (c[2] <= best[1] or c[1] >= best[2])
                                    for c in candidates[1:])
                    if not ambiguous:
                        selected = regions[best[1]:best[2]]
                        quality = 'strong' if best[0] >= .94 else 'approximate'
                        cursor = best[2]
        if selected:
            anchors.append([asdict(SourceSpan(page.page_id, bbox=r.bbox, region_id=r.region_id,
                                              grounding_method=method, grounding_quality=quality))
                            for r in selected])
        else:
            anchors.append([asdict(SourceSpan(page.page_id))])
    return anchors
