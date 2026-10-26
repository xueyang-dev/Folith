"""Deterministic paragraph-level alignment engine for source update (v1 -> v2).

Architecture constraints:
1. Deterministic local computation; zero model calls.
2. Identifies:
   - unchanged (exact or normalized match under tm_normalize)
   - changed (minor edits in place, similarity >= threshold)
   - added (new paragraphs in v2)
   - deleted (paragraphs from v1 absent in v2)
   - suspected_changed (moved segments or ambiguous modifications, requiring human confirmation)
3. Anchor stability takes precedence when stable anchors are provided (e.g. DOCX #26).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from folith.engine.tm_store import tm_normalize


class Classification:
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    ADDED = "added"
    DELETED = "deleted"
    SUSPECTED_CHANGED = "suspected_changed"

    ALL = {UNCHANGED, CHANGED, ADDED, DELETED, SUSPECTED_CHANGED}


class MatchMethod:
    EXACT = "exact"
    NORMALIZED = "normalized"
    ANCHOR_EXACT = "anchor_exact"
    ANCHOR_CHANGED = "anchor_changed"
    SIMILARITY_CHANGED = "similarity_changed"
    MOVED_SUSPECTED = "moved_suspected"
    AMBIGUOUS_SUSPECTED = "ambiguous_suspected"
    INSERTED = "inserted"
    REMOVED = "removed"


@dataclass
class AlignedSegment:
    v1_index: Optional[int]
    v2_index: Optional[int]
    classification: str
    match_method: str
    confidence: float
    v1_source: Optional[str] = None
    v2_source: Optional[str] = None
    suspected_reason: Optional[str] = None
    anchor_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "v1_index": self.v1_index,
            "v2_index": self.v2_index,
            "classification": self.classification,
            "match_method": self.match_method,
            "confidence": round(self.confidence, 4),
            "v1_source": self.v1_source,
            "v2_source": self.v2_source,
            "suspected_reason": self.suspected_reason,
            "anchor_id": self.anchor_id,
            "metadata": self.metadata,
        }


def string_similarity(text_a: str, text_b: str) -> float:
    """Compute normalized sequence similarity ratio [0.0, 1.0]."""
    norm_a = tm_normalize(text_a)
    norm_b = tm_normalize(text_b)
    if not norm_a and not norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 1.0
    return difflib.SequenceMatcher(None, norm_a, norm_b, autojunk=False).ratio()


def _format_anchor_key(anchor: Any) -> Optional[str]:
    """Convert an anchor object/dict into a deterministic string key."""
    if anchor is None:
        return None
    if isinstance(anchor, str):
        return anchor
    if isinstance(anchor, dict):
        # Stable sort of keys
        parts = []
        for k in sorted(anchor.keys()):
            parts.append(f"{k}:{anchor[k]}")
        return "|".join(parts)
    return str(anchor)


def align_source_paragraphs(
    paras_v1: Sequence[str],
    paras_v2: Sequence[str],
    anchors_v1: Optional[Sequence[Any]] = None,
    anchors_v2: Optional[Sequence[Any]] = None,
    *,
    changed_threshold: float = 0.60,
    ambiguous_threshold: float = 0.50,
) -> List[AlignedSegment]:
    """Align source paragraphs between v1 and v2.

    Returns an ordered list of AlignedSegment objects covering all v1 and v2 paragraphs.
    """
    n1 = len(paras_v1)
    n2 = len(paras_v2)

    norm_v1 = [tm_normalize(p) for p in paras_v1]
    norm_v2 = [tm_normalize(p) for p in paras_v2]

    aligned_results: List[AlignedSegment] = []
    matched_v1: Set[int] = set()
    matched_v2: Set[int] = set()

    anchor_keys_v1 = [_format_anchor_key(a) for a in anchors_v1] if anchors_v1 else None
    anchor_keys_v2 = [_format_anchor_key(a) for a in anchors_v2] if anchors_v2 else None

    # Step 1: Global Monotonic Backbone (LCS of normalized strings)
    # Finds the optimal non-crossing alignment of unchanged paragraphs across the document.
    matcher = difflib.SequenceMatcher(None, norm_v1, norm_v2, autojunk=False)
    for block in matcher.get_matching_blocks():
        for d in range(block.size):
            i = block.a + d
            j = block.b + d
            matched_v1.add(i)
            matched_v2.add(j)
            p1 = paras_v1[i]
            p2 = paras_v2[j]

            has_matching_anchor = (
                anchor_keys_v1 and anchor_keys_v2 and
                i < len(anchor_keys_v1) and j < len(anchor_keys_v2) and
                anchor_keys_v1[i] is not None and
                anchor_keys_v1[i] == anchor_keys_v2[j]
            )
            anchor_id = anchor_keys_v1[i] if has_matching_anchor else None

            if has_matching_anchor:
                method = MatchMethod.ANCHOR_EXACT
            elif p1 == p2:
                method = MatchMethod.EXACT
            else:
                method = MatchMethod.NORMALIZED

            aligned_results.append(AlignedSegment(
                v1_index=i, v2_index=j,
                classification=Classification.UNCHANGED,
                match_method=method,
                confidence=1.0,
                v1_source=p1, v2_source=p2,
                anchor_id=anchor_id,
            ))

    # Step 2: Detecting Moved Segments (Translocations)
    # Segments that exist in both v1 and v2 with exact/normalized identity
    # but outside the monotonic sequence.
    unmatched_v1 = [i for i in range(n1) if i not in matched_v1]
    unmatched_v2 = [j for j in range(n2) if j not in matched_v2]

    norm_map_v2: Dict[str, List[int]] = {}
    for j in unmatched_v2:
        norm_map_v2.setdefault(norm_v2[j], []).append(j)

    for i in list(unmatched_v1):
        target_norm = norm_v1[i]
        if target_norm in norm_map_v2 and norm_map_v2[target_norm]:
            j = norm_map_v2[target_norm].pop(0)
            matched_v1.add(i)
            matched_v2.add(j)
            unmatched_v1.remove(i)
            unmatched_v2.remove(j)
            p1 = paras_v1[i]
            p2 = paras_v2[j]
            aligned_results.append(AlignedSegment(
                v1_index=i, v2_index=j,
                classification=Classification.SUSPECTED_CHANGED,
                match_method=MatchMethod.MOVED_SUSPECTED,
                confidence=0.9,
                v1_source=p1, v2_source=p2,
                suspected_reason="moved_segment",
                metadata={"origin_v1_index": i, "target_v2_index": j},
            ))

    # Step 3: Disjoint Region Analysis (In-place changes, additions, deletions)
    # For remaining unmatched segments, attempt similarity pairing
    remaining_v1 = sorted(list(unmatched_v1))
    remaining_v2 = sorted(list(unmatched_v2))

    # If anchor keys match between remaining segments, prioritize anchor match
    used_v1: Set[int] = set()
    used_v2: Set[int] = set()

    if anchor_keys_v1 and anchor_keys_v2:
        for i in remaining_v1:
            k1 = anchor_keys_v1[i]
            if k1 is None:
                continue
            for j in remaining_v2:
                if j in used_v2:
                    continue
                k2 = anchor_keys_v2[j]
                if k1 == k2:
                    sim = string_similarity(paras_v1[i], paras_v2[j])
                    if sim >= changed_threshold:
                        used_v1.add(i)
                        used_v2.add(j)
                        aligned_results.append(AlignedSegment(
                            v1_index=i, v2_index=j,
                            classification=Classification.CHANGED,
                            match_method=MatchMethod.ANCHOR_CHANGED,
                            confidence=sim,
                            v1_source=paras_v1[i], v2_source=paras_v2[j],
                            anchor_id=k1,
                        ))
                        break

    # Calculate similarity matrix between remaining unmatched v1 and v2
    sim_candidates: List[Tuple[float, int, int]] = []
    for i in remaining_v1:
        if i in used_v1:
            continue
        for j in remaining_v2:
            if j in used_v2:
                continue
            sim = string_similarity(paras_v1[i], paras_v2[j])
            if sim >= ambiguous_threshold:
                sim_candidates.append((sim, i, j))

    # Greedily match by highest similarity
    sim_candidates.sort(key=lambda x: x[0], reverse=True)

    for sim, i, j in sim_candidates:
        if i in used_v1 or j in used_v2:
            continue
        used_v1.add(i)
        used_v2.add(j)
        p1 = paras_v1[i]
        p2 = paras_v2[j]

        if sim >= changed_threshold:
            aligned_results.append(AlignedSegment(
                v1_index=i, v2_index=j,
                classification=Classification.CHANGED,
                match_method=MatchMethod.SIMILARITY_CHANGED,
                confidence=sim,
                v1_source=p1, v2_source=p2,
            ))
        else:
            aligned_results.append(AlignedSegment(
                v1_index=i, v2_index=j,
                classification=Classification.SUSPECTED_CHANGED,
                match_method=MatchMethod.AMBIGUOUS_SUSPECTED,
                confidence=sim,
                v1_source=p1, v2_source=p2,
                suspected_reason="low_similarity_modification",
                metadata={"similarity": sim},
            ))

    # Remaining v1 without match -> DELETED
    for i in remaining_v1:
        if i not in used_v1:
            aligned_results.append(AlignedSegment(
                v1_index=i, v2_index=None,
                classification=Classification.DELETED,
                match_method=MatchMethod.REMOVED,
                confidence=1.0,
                v1_source=paras_v1[i],
                v2_source=None,
            ))

    # Remaining v2 without match -> ADDED
    for j in remaining_v2:
        if j not in used_v2:
            aligned_results.append(AlignedSegment(
                v1_index=None, v2_index=j,
                classification=Classification.ADDED,
                match_method=MatchMethod.INSERTED,
                confidence=1.0,
                v1_source=None,
                v2_source=paras_v2[j],
            ))

    # Step 4: Deterministic Ordering
    def sort_key(item: AlignedSegment) -> Tuple[float, int]:
        if item.v2_index is not None:
            return (float(item.v2_index), 0)
        v1_idx = item.v1_index if item.v1_index is not None else 0
        return (float(v1_idx) + 0.5, 1)

    aligned_results.sort(key=sort_key)
    return aligned_results
    # Order should present the document progression of v2, with deleted segments
    # placed relative to their historical v1 position.
    def sort_key(item: AlignedSegment) -> Tuple[float, int]:
        if item.v2_index is not None:
            # Placed directly at its v2 position
            return (float(item.v2_index), 0)
        # Deleted segment (v2_index is None)
        # Interpolate its position based on v1_index
        v1_idx = item.v1_index if item.v1_index is not None else 0
        # Find the preceding v1 segment that mapped to a v2_index
        # Default interpolation:
        return (float(v1_idx) + 0.5, 1)

    aligned_results.sort(key=sort_key)
    return aligned_results


def align_docx_units(
    units_v1: Sequence[Any],
    units_v2: Sequence[Any],
    *,
    changed_threshold: float = 0.60,
    ambiguous_threshold: float = 0.50,
) -> List[AlignedSegment]:
    """Align TranslatableUnit objects extracted from DOCX roundtrip packages."""
    paras_v1 = [getattr(u, "source_plain", getattr(u, "source", str(u))) for u in units_v1]
    paras_v2 = [getattr(u, "source_plain", getattr(u, "source", str(u))) for u in units_v2]

    anchors_v1 = [getattr(u, "source_anchor", None) for u in units_v1]
    anchors_v2 = [getattr(u, "source_anchor", None) for u in units_v2]

    return align_source_paragraphs(
        paras_v1,
        paras_v2,
        anchors_v1=anchors_v1,
        anchors_v2=anchors_v2,
        changed_threshold=changed_threshold,
        ambiguous_threshold=ambiguous_threshold,
    )
