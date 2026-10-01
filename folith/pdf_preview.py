"""PDF visual preview and segment source provenance tracking.

Provides fast, high-fidelity PDF page rendering with text highlight bounding
boxes using PyMuPDF (fitz), allowing translators and reviewers to visually
trace any segment or quality-gate issue back to its exact location on the
original PDF page.
"""
from __future__ import annotations

import collections
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import fitz
except ImportError:  # pragma: no cover
    fitz = None

import core


# Cache rendered page images in memory (LRU) to prevent expensive re-rendering
# on every Streamlit interaction. (key -> PNG bytes)
_IMAGE_CACHE_MAX_ENTRIES = 128
_IMAGE_CACHE: collections.OrderedDict[Tuple[Any, ...], bytes] = collections.OrderedDict()

# Cache segment -> (page_index, rects) mapping
_SEGMENT_PAGE_CACHE: Dict[Tuple[str, int, str], Tuple[Optional[int], List[Any]]] = {}


def has_pdf_source(job_id: str) -> bool:
    """Return whether a valid PDF source document is available for the job."""
    if not job_id or not fitz:
        return False
    source = core.load_source(job_id)
    if not source or not isinstance(source, (bytes, bytearray)):
        return False
    return source.startswith(b"%PDF")


def open_pdf_doc(job_id: str):
    """Open and return a PyMuPDF Document from the job's source.bin."""
    if not fitz or not job_id:
        return None
    source = core.load_source(job_id)
    if not source or not isinstance(source, (bytes, bytearray)):
        return None
    try:
        return fitz.open(stream=source, filetype="pdf")
    except Exception:
        return None


def get_pdf_page_count(job_id: str) -> int:
    """Return the total number of pages in the job's PDF source document."""
    doc = open_pdf_doc(job_id)
    if doc is None:
        return 0
    try:
        return doc.page_count
    finally:
        doc.close()


def _candidate_search_queries(text: str) -> List[str]:
    """Extract ordered candidate phrases from segment text for PDF searching."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return []
    queries = []
    # 1. Full cleaned string if not too long
    if len(cleaned) <= 120:
        queries.append(cleaned)
    # 2. First 6 words
    words = cleaned.split()
    if len(words) >= 4:
        queries.append(" ".join(words[:6]))
        queries.append(" ".join(words[:4]))
    # 3. Strip punctuation for noise fragments
    alphanumeric_words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", cleaned)
    if alphanumeric_words:
        if len(alphanumeric_words) >= 3:
            queries.append(" ".join(alphanumeric_words[:4]))
            queries.append(" ".join(alphanumeric_words[:2]))
        queries.append(alphanumeric_words[0])
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for q in queries:
        q_norm = q.strip()
        if len(q_norm) >= 2 and q_norm not in seen:
            seen.add(q_norm)
            unique.append(q_norm)
    return unique


def find_segment_page_and_rects(
    job_id: str,
    segment_text: str,
    start_page_hint: int = 0,
) -> Tuple[Optional[int], List[Any]]:
    """Locate the PDF page number (0-indexed) and highlight rects for segment text.

    Searches forward from ``start_page_hint`` first (since documents are read
    sequentially), falling back to full-document search. Returns ``(page_index, rects)``.
    """
    if not segment_text or not fitz:
        return None, []
    doc = open_pdf_doc(job_id)
    if doc is None:
        return None, []
    try:
        total_pages = doc.page_count
        if total_pages <= 0:
            return None, []

        queries = _candidate_search_queries(segment_text)
        if not queries:
            return None, []

        start_page = max(0, min(start_page_hint, total_pages - 1))
        # Search forward starting from hint, then wrap around
        page_order = list(range(start_page, total_pages)) + list(range(0, start_page))

        for query in queries:
            for page_idx in page_order:
                page = doc[page_idx]
                rects = page.search_for(query)
                if rects:
                    # Return primitive rect coordinates (x0, y0, x1, y1)
                    return page_idx, [[r.x0, r.y0, r.x1, r.y1] for r in rects]
        return None, []
    except Exception:
        return None, []
    finally:
        doc.close()


def get_cached_segment_location(
    job_id: str,
    segment_index: int,
    segment_text: str,
    start_page_hint: int = 0,
) -> Tuple[Optional[int], List[Any]]:
    """Return cached or computed (page_index, rects) for a segment."""
    cache_key = (job_id, segment_index, (segment_text or "").strip()[:80])
    if cache_key in _SEGMENT_PAGE_CACHE:
        return _SEGMENT_PAGE_CACHE[cache_key]
    location = find_segment_page_and_rects(job_id, segment_text, start_page_hint)
    if location[0] is not None:
        _SEGMENT_PAGE_CACHE[cache_key] = location
    return location


def render_pdf_page_image(
    job_id: str,
    page_index: int,
    highlight_rects: Optional[Sequence[Any]] = None,
    dpi: int = 150,
) -> Optional[bytes]:
    """Render a PDF page to high-definition PNG bytes with optional highlight rects."""
    if not fitz or not job_id:
        return None
    norm_rects = tuple(
        tuple(round(float(coord), 2) for coord in r)
        for r in (highlight_rects or [])
        if len(r) == 4
    )
    cache_key = (job_id, page_index, norm_rects, dpi)
    if cache_key in _IMAGE_CACHE:
        _IMAGE_CACHE.move_to_end(cache_key)
        return _IMAGE_CACHE[cache_key]

    doc = open_pdf_doc(job_id)
    if doc is None:
        return None
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return None
        page = doc[page_index]

        # Draw highlight boxes on the page before rasterizing
        if highlight_rects:
            for r in highlight_rects:
                if len(r) == 4:
                    rect = fitz.Rect(r[0], r[1], r[2], r[3])
                    # Semi-transparent warm yellow fill with darker golden border
                    page.draw_rect(
                        rect,
                        color=(0.90, 0.65, 0.05),
                        fill=(1.0, 0.90, 0.20),
                        fill_opacity=0.38,
                        width=1.8,
                    )

        pix = page.get_pixmap(dpi=dpi, alpha=False)
        png_bytes = pix.tobytes("png")
        if png_bytes:
            _IMAGE_CACHE[cache_key] = png_bytes
            if len(_IMAGE_CACHE) > _IMAGE_CACHE_MAX_ENTRIES:
                _IMAGE_CACHE.popitem(last=False)

        # Pre-render adjacent clean pages into memory cache while doc is open
        for adj_idx in (page_index + 1, page_index - 1):
            if 0 <= adj_idx < doc.page_count:
                adj_key = (job_id, adj_idx, (), dpi)
                if adj_key not in _IMAGE_CACHE:
                    try:
                        adj_page = doc[adj_idx]
                        adj_pix = adj_page.get_pixmap(dpi=dpi, alpha=False)
                        adj_bytes = adj_pix.tobytes("png")
                        if adj_bytes:
                            _IMAGE_CACHE[adj_key] = adj_bytes
                            if len(_IMAGE_CACHE) > _IMAGE_CACHE_MAX_ENTRIES:
                                _IMAGE_CACHE.popitem(last=False)
                    except Exception:
                        pass

        return png_bytes
    except Exception:
        return None
    finally:
        doc.close()
