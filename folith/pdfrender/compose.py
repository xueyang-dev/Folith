"""PDF composition for the original-layout renderer.

Composition is deliberately narrow: erase exactly the source text that a
translation replaces, then stamp one vector overlay on top.  Everything else on
the page — images, vector art, table rules, page size, rotation, links and the
outline — is left to the source document.

Measured behaviour this relies on (page 5 of the Middle East report): redacting
every text line with ``fill=None`` and applying with
``images=PDF_REDACT_IMAGE_NONE, graphics=PDF_REDACT_LINE_ART_NONE`` removes 100%
of the extracted text while leaving drawings 13 -> 13 and images 1 -> 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

#: Points of halo around a replaced region that may legitimately differ from the
#: text-erased source raster when verifying a rendered page.
VERIFY_HALO_PT = 2.0


@dataclass
class PagePlan:
    """Everything one output page needs."""

    page_index: int
    #: source line rects whose text this page's translations replace
    erase_rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: raster regions to cover before stamping (scanned pixels have no text
    #: objects to redact)
    cover_rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    overlay: bytes | None = None
    #: visible marks for regions the renderer could not finish
    markers: list = field(default_factory=list)
    #: a page-level note when content could not be located at all
    page_note: str = ""
    #: diagnostics copied into the run report
    detail: dict = field(default_factory=dict)


def _mode_color(pixmap, rect) -> tuple[float, float, float]:
    """Most common colour around the border of ``rect`` (the local background)."""
    if pixmap is None:
        return (1.0, 1.0, 1.0)
    try:
        width, height = pixmap.width, pixmap.height
        if width <= 0 or height <= 0:
            return (1.0, 1.0, 1.0)
        samples = []
        step_x = max(1, width // 24)
        step_y = max(1, height // 24)
        for x in range(0, width, step_x):
            for y in (0, 1, height - 2, height - 1):
                if 0 <= y < height:
                    samples.append(pixmap.pixel(x, y))
        for y in range(0, height, step_y):
            for x in (0, 1, width - 2, width - 1):
                if 0 <= x < width:
                    samples.append(pixmap.pixel(x, y))
        if not samples:
            return (1.0, 1.0, 1.0)
        counts: dict[tuple[int, int, int], int] = {}
        for sample in samples:
            counts[sample] = counts.get(sample, 0) + 1
        best = max(counts, key=counts.get)
        return (best[0] / 255.0, best[1] / 255.0, best[2] / 255.0)
    except Exception:
        return (1.0, 1.0, 1.0)


def background_for(page: pymupdf.Page, rect) -> tuple[float, float, float]:
    clip = pymupdf.Rect(*rect)
    if clip.is_empty or clip.is_infinite:
        return (1.0, 1.0, 1.0)
    try:
        pixmap = page.get_pixmap(clip=clip, dpi=72, alpha=False)
    except Exception:
        return (1.0, 1.0, 1.0)
    return _mode_color(pixmap, rect)


class BackgroundSampler:
    """Local background colour per region, read from the source document once.

    The colour decides two things: what to paint over scanned pixels, and
    whether a source text colour stays legible after the text is re-typeset.
    """

    def __init__(self, source_bytes: bytes):
        self._doc = pymupdf.open(stream=source_bytes, filetype="pdf")
        self._cache: dict[tuple, tuple[float, float, float]] = {}

    def sample(self, page_index: int, rect) -> tuple[float, float, float]:
        key = (page_index, round(rect[0], 1), round(rect[1], 1),
               round(rect[2], 1), round(rect[3], 1))
        if key in self._cache:
            return self._cache[key]
        value = (1.0, 1.0, 1.0)
        try:
            page = self._doc[page_index]
            if page.rotation:
                page.set_rotation(0)
            value = background_for(page, rect)
        except Exception:
            value = (1.0, 1.0, 1.0)
        self._cache[key] = value
        return value

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass


def text_ink_extent(page: pymupdf.Page, band, *, dpi: int = 100,
                    dark: int = 190, background: int = 225,
                    min_background: float = 0.70):
    """Horizontal extent of the text ink in a row band of a scanned page.

    A visual-model box is often narrower than the scanned line it describes, and
    an uncovered tail of the original wording survives as a faint ghost.  The
    extent is only accepted when the area outside the reported box is mostly
    background, so a figure or a colour block is never painted over.
    """
    clip = pymupdf.Rect(band[0], band[1], band[2], band[3])
    if clip.is_empty or clip.is_infinite:
        return None
    try:
        pixmap = page.get_pixmap(clip=clip, dpi=dpi, alpha=False, colorspace=pymupdf.csGRAY)
    except Exception:
        return None
    width, height = pixmap.width, pixmap.height
    if width <= 0 or height <= 0:
        return None
    try:
        data = pixmap.samples
    except Exception:
        return None
    scale = 72.0 / dpi
    left_column = None
    right_column = None
    for x in range(width):
        column_dark = False
        for y in range(0, height, max(1, height // 40)):
            if data[y * width + x] < dark:
                column_dark = True
                break
        if column_dark:
            if left_column is None:
                left_column = x
            right_column = x
    if left_column is None or right_column is None:
        return None
    original_start = max(0.0, (band[0] - clip.x0) / scale)
    original_end = min(float(width), (band[2] - clip.x0) / scale)
    extra = 0
    total = 0
    for x in range(width):
        if original_start - 1 <= x <= original_end + 1:
            continue
        for y in range(0, height, max(1, height // 40)):
            total += 1
            if data[y * width + x] >= background:
                extra += 1
    if total and (extra / total) < min_background:
        return None
    return (clip.x0 + left_column * scale, clip.x0 + (right_column + 1) * scale)


def erase_text(page: pymupdf.Page, rects: list[tuple]) -> int:
    """Remove the text covered by ``rects``; returns the number of annotations."""
    added = 0
    for rect in rects:
        quad = pymupdf.Rect(*rect)
        if quad.is_empty or quad.is_infinite:
            continue
        page.add_redact_annot(quad, fill=None)
        added += 1
    if not added:
        return 0
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return added


def cover_pixels(page: pymupdf.Page, rects: list[tuple],
                 colors: list[tuple]) -> None:
    """Paint sampled background over scanned pixels that a translation replaces."""
    for rect, color in zip(rects, colors):
        quad = pymupdf.Rect(*rect)
        if quad.is_empty or quad.is_infinite:
            continue
        page.draw_rect(quad, color=color, fill=color, width=0, overlay=True)


MARK_COLOR = (0.85, 0.33, 0.05)


def mark_unfinished(page: pymupdf.Page, plan: PagePlan) -> int:
    """Draw the regions a human still has to fix.

    The exported PDF has to show its own gaps: a reader must be able to see that
    a region is unfinished instead of trusting a silent partial result.
    """
    drawn = 0
    for marker in plan.markers:
        rect = pymupdf.Rect(*marker["box"])
        if rect.is_empty or rect.is_infinite:
            continue
        try:
            page.draw_rect(rect, color=MARK_COLOR, width=1.2,
                           dashes="[4 3] 0", overlay=True)
            label = str(marker.get("label") or "")
            if label:
                box = pymupdf.Rect(rect.x0, max(page.rect.y0, rect.y0 - 9),
                                   min(page.rect.x1, rect.x0 + 260), rect.y0 - 1)
                if box.height > 4 and box.width > 20:
                    page.insert_textbox(box, label, fontsize=6.5,
                                        color=MARK_COLOR, fontname="helv")
            drawn += 1
        except Exception:
            continue
    if plan.page_note:
        box = pymupdf.Rect(18, 6, min(page.rect.x1 - 18, 420), 20)
        try:
            page.insert_textbox(box, plan.page_note, fontsize=7.5,
                                color=MARK_COLOR, fontname="helv")
            drawn += 1
        except Exception:
            pass
    return drawn


def stamp(page: pymupdf.Page, overlay_bytes: bytes) -> bool:
    """Draw a page-sized vector overlay on top of the page.

    ``show_pdf_page`` keeps the overlay's text as real text, so the target
    language stays searchable and copyable in the output.
    """
    if not overlay_bytes:
        return False
    with pymupdf.open(stream=overlay_bytes, filetype="pdf") as overlay_doc:
        if len(overlay_doc) == 0:
            return False
        page.show_pdf_page(page.rect, overlay_doc, 0)
    return True


def compose(source_bytes: bytes, plans: dict[int, PagePlan]) -> tuple[bytes, dict]:
    """Apply the page plans to a copy of the source document."""
    doc = pymupdf.open(stream=source_bytes, filetype="pdf")
    diagnostics = {"pages_touched": 0, "erase_rects": 0, "cover_rects": 0,
                   "stamped": 0, "marked": 0, "links_restored": 0,
                   "rotation_restored": 0}
    for page_index, plan in sorted(plans.items()):
        if not (0 <= page_index < len(doc)):
            continue
        page = doc[page_index]
        rotation = page.rotation
        if rotation:
            page.set_rotation(0)
            diagnostics["rotation_restored"] += 1

        links_before = page.get_links()
        if plan.cover_rects:
            widened = []
            for rect in plan.cover_rects:
                extent = text_ink_extent(page, rect)
                if extent and extent[0] < rect[0] - 0.5:
                    pass
                if extent and (extent[0] < rect[0] - 0.5 or extent[1] > rect[2] + 0.5):
                    widened.append((min(rect[0], extent[0]), rect[1],
                                    max(rect[2], extent[1]), rect[3]))
                else:
                    widened.append(rect)
            colors = [background_for(page, rect) for rect in widened]
            cover_pixels(page, widened, colors)
            diagnostics["cover_widened"] = diagnostics.get("cover_widened", 0) + sum(
                1 for a, b in zip(plan.cover_rects, widened) if a != b)
            diagnostics["cover_rects"] += len(plan.cover_rects)
        if plan.erase_rects:
            diagnostics["erase_rects"] += erase_text(page, plan.erase_rects)
        if plan.overlay:
            if stamp(page, plan.overlay):
                diagnostics["stamped"] += 1
        if plan.markers or plan.page_note:
            diagnostics["marked"] = diagnostics.get("marked", 0) + mark_unfinished(
                page, plan)

        if links_before:
            after = page.get_links()
            if len(after) < len(links_before):
                have = {(l.get("kind"), str(l.get("uri") or ""), tuple(l["from"]))
                        for l in after}
                for link in links_before:
                    key = (link.get("kind"), str(link.get("uri") or ""),
                           tuple(link["from"]))
                    if key in have:
                        continue
                    try:
                        page.insert_link(link)
                        diagnostics["links_restored"] += 1
                    except Exception:
                        continue

        if rotation:
            page.set_rotation(rotation)
        diagnostics["pages_touched"] += 1

    data = doc.tobytes(garbage=4, deflate=True, clean=False)
    doc.close()
    return data, diagnostics


def page_geometry(source_bytes: bytes) -> list[dict]:
    """Sizes/rotations/links/toc of a document, for before/after comparison."""
    doc = pymupdf.open(stream=source_bytes, filetype="pdf")
    pages = []
    for index, page in enumerate(doc):
        pages.append({
            "index": index,
            "width": round(page.rect.width, 3),
            "height": round(page.rect.height, 3),
            "rotation": page.rotation,
            "links": len(page.get_links()),
            "text": page.get_text(),
        })
    toc = doc.get_toc() or []
    doc.close()
    return pages, toc
