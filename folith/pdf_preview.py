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
from folith.ocr_cache import OCRPageCache


# Cache rendered page images in memory (LRU) to prevent expensive re-rendering
# on every Streamlit interaction. (key -> PNG bytes)
_IMAGE_CACHE_MAX_ENTRIES = 128
_IMAGE_CACHE: collections.OrderedDict[Tuple[Any, ...], bytes] = collections.OrderedDict()

# Cache segment -> (page_index, rects) mapping
_SEGMENT_PAGE_CACHE: Dict[Tuple[Any, ...], Tuple[Optional[int], List[Any]]] = {}


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
        return _find_ocr_location(job_id, queries, page_order)
    except Exception:
        return None, []
    finally:
        doc.close()


def _find_ocr_location(job_id, queries, page_order):
    """Match retained logical OCR lines and highlight their source rectangles.

    Uses the extraction's exact cache identity, never whichever engine file
    happens to be newest. Cleanup paraphrases without a matching phrase remain
    unmapped. Boxes identify source lines, not fabricated word-level alignment.

    A backend may return text with **no** geometry (the online transcriber).
    Those lines are kept for matching but never produce a rectangle: highlighting
    a placeholder coordinate would point at the wrong place on the page. When the
    text is found but no line has real geometry, the page is returned with an
    empty rect list so the reviewer can still open the original page.
    """
    state = core.load_job_state(job_id) or {}
    ocr = (state.get("extraction_report") or {}).get("ocr") or {}
    identity = dict(ocr.get("cache_identity") or {})
    directory = ocr.get("ocr_cache_dir")
    if not identity or not directory:
        return None, []
    identity.pop("page_index", None)
    cache = OCRPageCache(directory)
    for page_index in page_order:
        params = {**identity, "dpi": (ocr.get("ocr_dpi_used_by_page") or {}).get(
            str(page_index), identity["dpi"])}
        records, _ = cache.load(indices=[page_index], **params)
        record = records.get(str(page_index)) or {}
        lines = []
        scale = record.get("pixels_to_points", 0)
        for block in record.get("blocks") or []:
            for line in block.get("lines") or []:
                text = re.sub(r"\s+", " ", str(line.get("text") or "")).strip()
                if not text:
                    continue
                known = bool(line.get("geometry_known", True)) and bool(
                    line.get("bbox_pdf") or scale)
                if not known:
                    # Keep the text for phrase matching, but mark it as
                    # un-highlightable rather than inventing a rectangle.
                    lines.append((text, None))
                    continue
                left, top, right, bottom = (float(line[key]) for key in
                                            ("left", "top", "right", "bottom"))
                if right <= left or bottom <= top:
                    lines.append((text, None))
                    continue
                lines.append((text, [left * scale, top * scale,
                                     right * scale, bottom * scale]))
        text = " ".join(line[0] for line in lines)
        for query in queries:
            start = text.find(query)
            if start < 0:
                continue
            end = start + len(query)
            cursor = 0
            rects = []
            for value, rect in lines:
                if rect is not None and cursor < end and cursor + len(value) > start:
                    rects.append(rect)
                cursor += len(value) + 1
            # A text-only match still locates the page; it simply cannot
            # highlight. Returning the page is strictly better than telling the
            # reviewer "not found" for text that OCR did read.
            return page_index, rects
    return None, []


def get_segment_source_locations(job_id, segment_index, state=None):
    """Resolve all persisted spans, including cross-page segments.

    An empty rectangle list is a usable page anchor, never a search failure.
    Existing legacy jobs without anchors continue through text search.
    ``state``: the caller's loaded state (read only); reloading copies the whole
    task state, which made following the selection in the original page stall.
    """
    from folith.document_ir import normalized_bbox
    state = state if state is not None else (core.load_job_state(job_id) or {})
    pairs = state.get("pairs") or []
    if segment_index < 0:
        return []
    if segment_index < len(pairs) and "source_spans" in pairs[segment_index]:
        spans = pairs[segment_index]["source_spans"]
    else:
        all_spans = state.get("segment_source_spans") or []
        spans = all_spans[segment_index] if segment_index < len(all_spans) else []
    document = (state.get("extraction_report") or {}).get("document_ir") or {}
    pages = {p["page_id"]:p for p in document.get("pages") or []}
    locations = {}
    for span in spans or []:
        page = pages.get(span.get("page_id"))
        if page is None:
            continue
        index = page["page_index"]
        rects = locations.setdefault(index, [])
        box = (normalized_bbox(span.get("bbox"))
               if span.get("grounding_quality") in {"exact", "strong", "approximate"} else None)
        if box:
            rect = [box[0]*page["width"], box[1]*page["height"],
                    box[2]*page["width"], box[3]*page["height"]]
            if rect not in rects:
                rects.append(rect)
    return list(locations.items())


def get_cached_segment_location(
    job_id: str,
    segment_index: int,
    segment_text: str,
    start_page_hint: int = 0,
) -> Tuple[Optional[int], List[Any]]:
    """Return cached or computed (page_index, rects) for a segment."""
    locations = get_segment_source_locations(job_id, segment_index)
    if locations:
        return locations[0]
    manifest = core.job_dir(job_id) / "ocr_cache" / "manifest.json"
    try:
        stamp = manifest.stat().st_mtime_ns
    except OSError:
        stamp = None
    cache_key = (job_id, segment_index, (segment_text or "").strip(), stamp)
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
