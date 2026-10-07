"""Original-layout PDF renderer.

Turns a finished job (DocumentIR + translated pairs + persisted source spans)
into a PDF that keeps the source document's page geometry while carrying the
translated text in place.

Pipeline::

    source PDF ─┬─ SourceDocument (line geometry, styles)  ─┐
                │                                           ├─ slots → Typst overlay → compose
    job state ──┴─ pairs + source_spans → regions → pieces ─┘

Public entry point: :func:`render_original_layout`.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

from . import compose, layout, typeset
from .typeset import Block

#: Hard floor for target text.  Declared before acceptance; it is never relaxed
#: afterwards to turn a failing page into a passing one.
MIN_FONT_PT = 7.0

#: A translation may not shrink below this share of its source size.
MIN_FONT_RATIO = 0.62

#: Bump whenever the rendering decisions change.  The per-page cache key includes
#: it, so a code change can never be masked by a stale cached page.
RENDERER_VERSION = 5

__all__ = ["MIN_FONT_PT", "MIN_FONT_RATIO", "render_original_layout",
           "RenderResult", "verify_pages"]

_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


class RenderResult:
    def __init__(self, data: bytes, report: dict):
        self.data = data
        self.report = report

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        totals = self.report.get("totals", {})
        return (f"<RenderResult {len(self.data)}B pages={totals.get('pages')} "
                f"placed={totals.get('placed')} unfinished={totals.get('unfinished')}>")


def _effective_line_height(families, direction: str, text: str,
                           source_line_height: float) -> float:
    """Never let the target's line boxes collide.

    The source document's leading is in the *source* font's metrics.  Re-setting
    the translation in a different font — especially CJK or Arabic — needs more
    advance for the same size, or the rendered lines overlap even though Typst
    reports the paragraph as fitting.
    """
    required = typeset.minimum_line_height(families, direction, text)
    return max(float(source_line_height or 1.0), required)


def _boxes_conflict(a, b, margin: float = 0.5) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > margin
            and min(a[3], b[3]) - max(a[1], b[1]) > margin)


def _floor_for(size: float, min_font_pt: float, min_font_ratio: float) -> float:
    """Declared readability floor for a slot.

    A target may not go below 62% of its source size, and never below 7pt unless
    the source itself was already smaller (footnotes and figure captions in real
    papers are set at ~7pt, and shrinking them further is worse than leaving
    them).  The same function is used by the acceptance check, so the rule
    cannot drift between rendering and verification.
    """
    return max(min(min_font_pt, size), size * min_font_ratio)


def _resolve_page(page_slots, source_page, *, min_font_pt: float,
                  min_font_ratio: float, build_block):
    """Lay a page's slots out vertically, letting translated text push the rest.

    Returns a list of dicts with the final ``top``, ``size``, ``height`` and an
    ``overflow`` flag.  The cascade is what makes block-level layout possible:
    a paragraph that needs three more lines moves the following paragraphs down
    instead of overlapping them, and only if the whole column still does not fit
    are the sizes reduced, never below the declared floor.
    """
    order = layout.reading_order(page_slots)
    if not order:
        return []
    limit = layout.page_bottom_limit(source_page, order)
    source_tops = [slot.source_box[1] for slot in order]
    styles = [_slot_style(slot) for slot in order]
    source_sizes = [st[0] for st in styles]
    floors = [_floor_for(size, min_font_pt, min_font_ratio)
              for size in source_sizes]
    sizes = list(source_sizes)
    heights = [0.0] * len(order)
    tops = list(source_tops)

    for _attempt in range(12):
        for index, slot in enumerate(order):
            block = build_block(slot, styles[index], sizes[index], tops[index])
            # Reserve the glyph-box overhang as well: Typst measures the line
            # box, a viewer sees roughly 0.15em more, and without the padding
            # the cascade would place the next block on top of a descender.
            heights[index] = (typeset.measure_height(block)
                              + typeset.ink_pad(sizes[index]))
        new_tops = list(source_tops)
        for index in range(len(order)):
            push = 0.0
            for other in range(index):
                # Any horizontal overlap means the two blocks share vertical
                # space, so the lower one must clear the upper one.  A threshold
                # here would leave narrow blocks (author columns, table cells)
                # free to collide with their neighbours.
                if layout.horizontal_overlap(order[other].box,
                                             order[index].box) <= 0.5:
                    continue
                bottom = new_tops[other] + heights[other]
                if bottom > source_tops[index]:
                    push = max(push, bottom - source_tops[index])
            new_tops[index] = source_tops[index] + push
        tops = new_tops

        # Correct only the slots that actually reach past the bottom of the
        # page, and correct them by the amount they are over.  A page-wide
        # proportional shrink converges far too slowly to close a one-point
        # overshoot at the foot of a full column.
        adjusted = False
        for index in range(len(order)):
            over = tops[index] + heights[index] - limit
            if over <= 0.5:
                continue
            target = max(4.0, limit - tops[index])
            ratio = min(1.0, target / max(heights[index], 1e-6))
            candidate = max(floors[index], round(sizes[index] * (ratio ** 0.5), 2))
            if candidate < sizes[index]:
                sizes[index] = candidate
                adjusted = True
        if not adjusted:
            break

    # Never let the cascade push a block off the page: clamp each top so the
    # text at the floor size can still start on the page.  A genuine shortage
    # then shows up as a reported overlap instead of silently lost text.
    floor_heights = []
    for index, slot in enumerate(order):
        probe = build_block(slot, styles[index], floors[index], source_tops[index])
        floor_heights.append(typeset.measure_height(probe)
                             + typeset.ink_pad(floors[index]))
    for index in range(len(order)):
        highest = limit - min(floor_heights[index], limit - source_tops[index])
        tops[index] = min(tops[index], max(source_tops[index], highest))

    # Final strict pass: no two placed boxes may intersect.  This is the exact
    # property the acceptance check measures, so it is enforced here too.
    for _ in range(6):
        boxes = []
        for index, slot in enumerate(order):
            probe = build_block(slot, styles[index], sizes[index], tops[index])
            boxes.append((slot.box[0], tops[index], slot.box[2],
                          tops[index] + typeset.measure_height(probe)
                          + typeset.ink_pad(sizes[index])))
        conflicts = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _boxes_conflict(boxes[i], boxes[j]):
                    conflicts.append((i, j))
        if not conflicts:
            break
        for i, j in conflicts:
            lower = j if tops[j] >= tops[i] else i
            upper = i if lower == j else j
            needed = boxes[upper][3] + 0.5 - tops[lower]
            if needed <= 0:
                continue
            if boxes[lower][3] + needed <= limit or lower == len(order) - 1:
                tops[lower] += needed
            else:
                ratio = max(4.0, limit - tops[lower]) / max(
                    boxes[lower][3] - tops[lower], 1e-6)
                tops[lower] = min(tops[lower] + needed, limit - 4.0)
                sizes[lower] = max(floors[lower],
                                   round(sizes[lower] * (min(1.0, ratio) ** 0.5), 2))

    def _measure_all(current_tops):
        out = []
        for index, slot in enumerate(order):
            probe = build_block(slot, styles[index], sizes[index], current_tops[index])
            out.append(typeset.measure_height(probe)
                       + typeset.ink_pad(sizes[index]))
        return out

    heights = _measure_all(tops)
    if any(tops[i] + heights[i] > limit + 0.5 for i in range(len(order))):
        # The page cannot hold the re-flowed text.  Falling back to the source
        # block positions keeps the damage local and the reading order intact,
        # instead of piling every block onto the foot of the page.  The regions
        # that still do not fit are reported individually.
        tops = list(source_tops)
        heights = _measure_all(tops)

    resolved = []
    for index, slot in enumerate(order):
        resolved.append({
            "slot": slot, "order": index, "top": tops[index], "size": sizes[index],
            "floor": floors[index], "source_size": source_sizes[index],
            "height": heights[index], "style": styles[index], "limit": limit,
            "box": (slot.box[0], tops[index], slot.box[2],
                    tops[index] + heights[index]),
            "overlap_with": [],
            "overflow": bool(tops[index] + heights[index] > limit + 0.5),
        })
    for i in range(len(resolved)):
        for j in range(i + 1, len(resolved)):
            if _boxes_conflict(resolved[i]["box"], resolved[j]["box"]):
                resolved[j]["overlap_with"].append(resolved[i]["slot"].pieces[0]
                                                   .item.segment_index
                                                   if resolved[i]["slot"].pieces
                                                   else -1)
    return resolved


def _page_cache_key(source_text: str, resolved, mode: str, settings: dict) -> str:
    payload = {
        "mode": mode,
        "settings": settings,
        "text": source_text,
        "slots": [{
            "top": round(entry["top"], 2),
            "size": round(entry["size"], 3),
            "height": round(entry["height"], 2),
            "source_box": [round(v, 2) for v in entry["slot"].source_box],
            # Restored markers are part of the page's content: leaving them out
            # served a stale page whenever only a mark changed.
            "marks": [{
                "text": str(mark.get("text") or ""),
                "bbox": [round(float(v), 2) for v in (mark.get("bbox") or [])],
                "size": round(float(mark.get("size") or 0.0), 3),
                "body_size": round(float(mark.get("body_size") or 0.0), 3),
            } for mark in (entry["slot"].marks or [])],
            "pieces": [{
                "segment": piece.item.segment_index,
                "text": piece.text,
                "font": piece.item.font,
                "flags": piece.item.flags,
                "color": [round(c, 4) for c in piece.item.color],
                "direction": piece.item.direction,
                "align": piece.item.align,
                "line_height": round(piece.item.line_height, 3),
                "bold": bool(int(piece.item.flags or 0) & 16),
                "italic": bool(int(piece.item.flags or 0) & 2),
            } for piece in entry["slot"].pieces],
        } for entry in resolved],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _bold(flags: int) -> bool:
    return bool(int(flags or 0) & 16)


def _italic(flags: int) -> bool:
    return bool(int(flags or 0) & 2)


def _slot_style(slot: layout.Slot):
    items = [piece.item for piece in slot.pieces]
    line_height = min((item.line_height for item in items), default=1.22)
    size = max((item.size for item in items), default=10.0)
    font = next((item.font for item in items if item.font), "")
    flags = 0
    for item in items:
        flags |= int(item.flags or 0)
    color = next((item.color for item in items), (0.0, 0.0, 0.0))
    direction = "rtl" if any(item.direction == "rtl" for item in items) else "ltr"
    align = next((item.align for item in items), "start")
    if align in {"left", "right"}:
        align = "right" if direction == "rtl" else "start"
    return size, font, flags, color, direction, align, line_height


def _luminance(color) -> float:
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def _ensure_contrast(color, background):
    """Keep the source colour when it stays legible on the local background."""
    if abs(_luminance(color) - _luminance(background)) >= 0.25:
        return color, False
    return ((1.0, 1.0, 1.0) if _luminance(background) < 0.5 else (0.0, 0.0, 0.0)), True


def _block_for(slot: layout.Slot, *, size: float, families, color, direction,
               align, line_height: float, top: float | None = None,
               height: float | None = None, weight: str = "regular",
               italic: bool = False):
    return Block(
        x=slot.box[0], y=slot.box[1] if top is None else top,
        width=max(8.0, slot.box[2] - slot.box[0]),
        height=(max(4.0, slot.box[3] - slot.box[1]) if height is None
                else max(4.0, height)),
        text=slot.text, size=size, families=list(families), color=color,
        direction=direction, align=align, line_height=line_height,
        weight=weight, italic=italic, marks=list(slot.marks or []),
    )


def render_original_layout(job_id: str, state=None, source_bytes: bytes | None = None,
                           *, mode: str = "translated",
                           min_font_pt: float = MIN_FONT_PT,
                           min_font_ratio: float = MIN_FONT_RATIO,
                           page_cache_dir=None,
                           use_cache: bool = True) -> RenderResult:
    """Render the original-layout PDF for ``job_id``.

    ``mode='translated'`` places the current translations.  ``mode='same_text'``
    places the source text back, which exercises removal + re-typesetting +
    composition without involving the translation core.
    """
    if state is None:
        import core
        state = core.load_job_state(job_id) or {}
    if source_bytes is None:
        import core
        source_bytes = core.load_source(job_id)
    if not source_bytes:
        raise ValueError("任务没有保存的 PDF 源文件，无法生成原排版 PDF")

    source = layout.SourceDocument(source_bytes)
    sampler = compose.BackgroundSampler(source_bytes)
    try:
        items = layout.build_items(state, source, mode=mode)
        pieces = layout.expand_pieces(items)
        slots = layout.build_slots(pieces, source)

        plans: dict[int, compose.PagePlan] = {}
        page_reports: dict[int, dict] = {}
        unfinished: list[dict] = []
        font_notes: list[dict] = []
        unlocatable_by_page: dict[int, int] = {}
        for record in unfinished:
            if record.get("reason") == "no_verified_region" and record.get("page_index") is not None:
                unlocatable_by_page[record["page_index"]] = \
                    unlocatable_by_page.get(record["page_index"], 0) + 1
        untranslated: list[dict] = []
        for item in items:
            if not item.skipped_reason:
                continue
            record = {
                "segment_index": item.segment_index,
                "page_index": item.regions[0].page_index if item.regions else None,
                "reason": item.skipped_reason,
                "source_excerpt": item.source[:160],
                "target_excerpt": item.target[:160],
            }
            # A pair with no target text keeps its source in place.  That is a
            # deliberate, reported state, not a layout failure.
            if item.skipped_reason == "empty_target":
                untranslated.append(record)
            else:
                unfinished.append(record)

        cache = _PageCache(page_cache_dir, enabled=use_cache)
        settings = {"min_font_pt": min_font_pt, "min_font_ratio": min_font_ratio,
                    "renderer": RENDERER_VERSION,
                    "typst": typeset.typst_version(),
                    "platform": typeset.platform_tag()}

        by_page: dict[int, list[layout.Slot]] = {}
        for slot in slots:
            by_page.setdefault(slot.page_index, []).append(slot)

        # Pairs that never resolved to a region cannot be marked individually;
        # they get a page-level note so the page still declares its own gap.
        for page_index in sorted(by_page):
            page_slots = by_page[page_index]
            source_page = source.pages[page_index]
            plan = compose.PagePlan(page_index=page_index)
            page_report = {
                "index": page_index, "page_id": f"p{page_index + 1}",
                "slots": len(page_slots), "placed": 0, "overflow": 0,
                "scan_page": all(region.method not in layout.NATIVE_TEXT_METHODS
                                 for slot in page_slots
                                 for region in slot.regions),
                "fonts": [], "sizes": [], "placements": [],
                "from_cache": False,
            }

            unlocatable = unlocatable_by_page.get(page_index, 0)
            if unlocatable:
                plan.page_note = (
                    f"{unlocatable} source segment(s) on this page have no verified "
                    f"location and were not replaced.")

            def build_block(slot, style, size, top):
                size_, font, flags, color, direction, align, line_height = style
                background = sampler.sample(page_index, slot.source_box)
                resolved, changed = _ensure_contrast(color, background)
                families = typeset.choose_fonts(
                    slot.text, font_style=font, bold=_bold(flags),
                    italic=_italic(flags))
                return _block_for(slot, size=size, families=families.families,
                                  color=resolved, direction=direction, align=align,
                                  line_height=line_height, top=top)

            resolved_page = _resolve_page(
                page_slots, source_page, min_font_pt=min_font_pt,
                min_font_ratio=min_font_ratio, build_block=build_block)

            cache_key = _page_cache_key(_page_text(source_page), resolved_page,
                                        mode, settings)
            cached = cache.load(page_index, cache_key)
            if cached is not None:
                plan.overlay = cached["overlay"]
                plan.erase_rects = cached["erase_rects"]
                plan.cover_rects = cached["cover_rects"]
                plan.markers = cached.get("markers") or []
                plan.page_note = cached.get("page_note") or ""
                page_report.update(cached["report"])
                page_report["from_cache"] = True
                # A cached page still owns its failure records: a hit must not
                # make a reported overflow disappear from the run report.
                unfinished.extend(cached.get("unfinished") or [])
                plans[page_index] = plan
                page_reports[page_index] = page_report
                continue

            blocks: list[Block] = []
            page_unfinished: list[dict] = []
            for entry in resolved_page:
                slot = entry["slot"]
                style = entry["style"]
                size_, font, flags, color, direction, align, line_height = style
                families = typeset.choose_fonts(
                    slot.text, font_style=font, bold=_bold(flags),
                    italic=_italic(flags))
                if families.missing:
                    font_notes.append({
                        "page_index": page_index,
                        "segment_indexes": [p.item.segment_index
                                            for p in slot.pieces],
                        "missing": families.missing[:16],
                    })
                background = sampler.sample(page_index, slot.source_box)
                fill, changed = _ensure_contrast(color, background)
                line_height = _effective_line_height(
                    families.families, direction, slot.text, line_height)
                block = _block_for(slot, size=entry["size"],
                                   families=families.families, color=fill,
                                   direction=direction, align=align,
                                   line_height=line_height, top=entry["top"],
                                   height=max(entry["height"], 4.0),
                                   weight="bold" if _bold(flags) else "regular",
                                   italic=_italic(flags))
                blocks.append(block)
                page_report["placed"] += 1
                page_report["sizes"].append(round(entry["size"], 2))
                page_report["fonts"].extend(families.families)
                if entry["overflow"]:
                    page_report["overflow"] += 1
                    plan.markers.append({
                        "box": [round(slot.box[0], 2), round(entry["top"], 2),
                                round(slot.box[2], 2),
                                round(entry["top"] + entry["height"], 2)],
                        "label": "unfinished: text does not fit",
                    })
                    record = {
                        "segment_indexes": [p.item.segment_index
                                            for p in slot.pieces],
                        "page_index": page_index,
                        "rect": [round(v, 2) for v in slot.source_box],
                        "reason": "overflow",
                        "placed_box": [round(slot.box[0], 2),
                                       round(entry["top"], 2),
                                       round(slot.box[2], 2),
                                       round(entry["top"] + entry["height"], 2)],
                        "available_height": round(
                            layout.page_bottom_limit(source_page, page_slots)
                            - entry["top"], 2),
                        "size_pt": round(entry["size"], 2),
                        "floor_pt": round(entry["floor"], 2),
                        "measured_height": round(entry["height"], 2),
                        "target_excerpt": slot.text[:160],
                    }
                    page_unfinished.append(record)
                    unfinished.append(record)
                if entry["overlap_with"]:
                    overlap_record = {
                        "segment_indexes": [p.item.segment_index
                                            for p in slot.pieces],
                        "page_index": page_index,
                        "rect": [round(v, 2) for v in slot.source_box],
                        "reason": "overlap",
                        "placed_box": [round(v, 2) for v in entry["box"]],
                        "size_pt": round(entry["size"], 2),
                        "floor_pt": round(entry["floor"], 2),
                        "target_excerpt": slot.text[:160],
                    }
                    page_unfinished.append(overlap_record)
                    unfinished.append(overlap_record)
                page_report["placements"].append({
                    "segment_indexes": [p.item.segment_index for p in slot.pieces],
                    "box": [round(slot.box[0], 2), round(entry["top"], 2),
                            round(slot.box[2], 2),
                            round(entry["top"] + entry["height"], 2)],
                    "source_box": [round(v, 2) for v in slot.source_box],
                    "size_pt": round(entry["size"], 2),
                    "source_size_pt": round(entry["source_size"], 2),
                    "floor_pt": round(entry["floor"], 2),
                    "line_height": round(line_height, 3),
                    "families": families.families,
                    "direction": direction,
                    "align": align,
                    "bold": _bold(flags),
                    "italic": _italic(flags),
                    "fill": [round(c, 4) for c in fill],
                    "fill_changed": changed,
                    "measured_height": round(entry["height"], 2),
                    "text": slot.text[:400],
                    "text_sha256": hashlib.sha256(
                        slot.text.encode("utf-8")).hexdigest()[:16],
                    "overflow": bool(entry["overflow"]),
                })
                # Two independent things can be under a region: real text
                # objects and scanned pixels.  A page can have both (a mixed
                # PDF keeps a text layer next to its images), and painting over
                # a text layer does not remove it — the extractor would still
                # return the old wording.  So always redact whatever text
                # objects the region covers, and additionally paint over the
                # pixels when the region's geometry came from a visual model
                # rather than from the text layer itself.
                native_text = any(region.method in layout.NATIVE_TEXT_METHODS
                                  for region in slot.regions)
                plan.erase_rects.extend(
                    line.rect for line in
                    source.lines_in(page_index, slot.source_box))
                if not native_text:
                    # Cover the full column width, not just the model's box:
                    # scanned words routinely reach past the reported bbox and
                    # would otherwise survive as faint leftovers.
                    plan.cover_rects.append(
                        (slot.box[0], slot.source_box[1], slot.box[2],
                         slot.source_box[3]))

            plan.overlay = typeset.render_page(source_page.width,
                                               source_page.height, blocks)
            cache.store(page_index, cache_key, plan, page_report, page_unfinished)
            plans[page_index] = plan
            page_reports[page_index] = page_report

        data, composition = compose.compose(source_bytes, plans)
        report = _build_report(source_bytes, data, items, slots, page_reports,
                               unfinished, font_notes, composition, mode, settings,
                               cache, untranslated)
        report["_slots_by_page"] = {
            str(page_index): [slot.text for slot in page_slots]
            for page_index, page_slots in by_page.items()
        }
        return RenderResult(data, report)
    finally:
        source.close()
        sampler.close()


def _page_text(page) -> str:
    return " ".join(line.text for line in page.lines)


class _PageCache:
    """Per-page overlay cache so editing one translation re-renders one page."""

    def __init__(self, directory, *, enabled: bool = True):
        self.enabled = bool(enabled) and directory is not None
        self.directory = pathlib.Path(directory) if directory else None
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _paths(self, page_index: int):
        return (self.directory / f"page-{page_index:05d}.json",
                self.directory / f"page-{page_index:05d}.pdf")

    def load(self, page_index: int, key: str):
        if not self.enabled:
            return None
        meta_path, overlay_path = self._paths(page_index)
        if not (meta_path.is_file() and overlay_path.is_file()):
            self.misses += 1
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            self.misses += 1
            return None
        if meta.get("key") != key:
            self.misses += 1
            return None
        self.hits += 1
        return {
            "overlay": overlay_path.read_bytes(),
            "erase_rects": meta.get("erase_rects") or [],
            "cover_rects": meta.get("cover_rects") or [],
            "markers": meta.get("markers") or [],
            "page_note": meta.get("page_note") or "",
            "report": meta.get("report") or {},
            "unfinished": meta.get("unfinished") or [],
        }

    def store(self, page_index: int, key: str, plan, report: dict,
              unfinished=()) -> None:
        if not self.enabled:
            return
        meta_path, overlay_path = self._paths(page_index)
        overlay_path.write_bytes(plan.overlay or b"")
        meta_path.write_text(json.dumps({
            "key": key,
            "erase_rects": plan.erase_rects,
            "cover_rects": plan.cover_rects,
            "markers": plan.markers,
            "page_note": plan.page_note,
            "report": {k: v for k, v in report.items() if k != "from_cache"},
            "unfinished": list(unfinished),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_report(source_bytes, output_bytes, items, slots, page_reports,
                  unfinished, font_notes, composition, mode, settings, cache,
                  untranslated=()) -> dict:
    pages = [page_reports[k] for k in sorted(page_reports)]
    source_pages, source_toc = compose.page_geometry(source_bytes)
    out_pages, out_toc = compose.page_geometry(output_bytes)
    source_sizes = {round(p["width"]): 0 for p in source_pages}
    return {
        "mode": mode,
        "settings": settings,
        "typst": typeset.typst_version(),
        "platform": typeset.platform_tag(),
        "totals": {
            "pages": len(source_pages),
            "pages_rendered": len(pages),
            "slots": len(slots),
            "items": len(items),
            "placed": sum(r["placed"] for r in pages),
            "unresolved_items": sum(1 for i in items if i.skipped_reason),
            "untranslated": len(untranslated),
            "unfinished": len(unfinished),
            "overflow": sum(r["overflow"] for r in pages),
            "links_before": sum(p["links"] for p in source_pages),
            "links_after": sum(p["links"] for p in out_pages),
            "toc_before": len(source_toc),
            "toc_after": len(out_toc),
            "page_count_preserved": len(source_pages) == len(out_pages),
            "page_geometry_preserved": all(
                abs(a["width"] - b["width"]) < 0.01
                and abs(a["height"] - b["height"]) < 0.01
                and a["rotation"] == b["rotation"]
                for a, b in zip(source_pages, out_pages)),
            "missing_glyph_slots": len(font_notes),
            "page_cache_hits": cache.hits,
            "page_cache_misses": cache.misses,
        },
        "composition": composition,
        "pages": pages,
        "unfinished": unfinished,
        "untranslated": list(untranslated),
        "missing_glyphs": font_notes,
        "markers": _marker_evidence(items, slots),
        "source_page_sizes": source_sizes,
        "bytes": len(output_bytes),
        "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
    }


def _marker_evidence(items, slots) -> dict:
    """Marker restoration evidence, with the two failure modes kept apart.

    ``unlocated_source_marks`` are upstream: the segment never resolved to a
    source region, so no renderer could place its mark.  ``unplaced_renderer_marks``
    are this renderer's own failures.  Reporting them as one number would hide
    which stage to fix.
    """
    source_total = sum(len(item.marks or []) for item in items)
    unlocated = sum(len(item.marks or []) for item in items
                    if item.skipped_reason == "no_verified_region")
    placed = sum(len(slot.marks or []) for slot in slots)
    return {
        "source_marks": source_total,
        "restored_marks": placed,
        "unlocated_source_marks": unlocated,
        "unplaced_renderer_marks": max(0, source_total - unlocated - placed),
    }


def verify_pages(source_bytes: bytes, output_bytes: bytes, report: dict) -> dict:
    """Page-by-page structural checks on a rendered result.

    Checks target text presence in the real text layer, leftover source text
    (ghosts), size floors and page structure.  Whole-page image similarity is
    deliberately not used as a pass signal.
    """
    import pymupdf

    output = pymupdf.open(stream=output_bytes, filetype="pdf")
    source = pymupdf.open(stream=source_bytes, filetype="pdf")
    slot_texts = report.get("_slots_by_page") or {}
    rows = []
    try:
        for page in report.get("pages") or []:
            index = page["index"]
            if not (0 <= index < len(output)):
                continue
            out_page = output[index]
            normalized = _normalize(out_page.get_text())
            texts = slot_texts.get(str(index)) or []
            present, missing = 0, []
            for text in texts:
                needle = _normalize(text)
                if needle and needle in normalized:
                    present += 1
                elif needle:
                    missing.append(text[:100])
            ghosts = []
            src_page = source[index]
            for line in src_page.get_text("dict").get("blocks") or []:
                if line.get("type") != 0:
                    continue
                for text_line in line.get("lines") or []:
                    raw = "".join(str(s.get("text") or "")
                                  for s in text_line.get("spans") or [])
                    raw = _normalize(raw)
                    if len(raw) < 12:
                        continue
                    if raw in normalized:
                        rect = [round(v, 2) for v in text_line["bbox"]]
                        if not any(_covers(rect, p.get("source_box"))
                                   for p in page.get("placements") or []):
                            ghosts.append({"text": raw[:80], "rect": rect})
            sizes = page.get("sizes") or []
            source_sizes = [p.get("source_size_pt") for p in
                            page.get("placements") or []]
            rows.append({
                "index": index,
                "placed": page["placed"],
                "target_present": present,
                "target_missing": missing,
                "ghosts": ghosts,
                "overflow": page["overflow"],
                "min_size": min(sizes) if sizes else None,
                "max_size": max(sizes) if sizes else None,
                "min_size_ratio": (min(sizes) / min(source_sizes)
                                   if sizes and source_sizes and min(source_sizes) else None),
                "below_floor": [s for s, src in zip(sizes, source_sizes)
                                if s < MIN_FONT_PT - 1e-6],
            })
    finally:
        output.close()
        source.close()
    return {"pages": rows}


def _covers(rect, box) -> bool:
    if not box:
        return False
    return (rect[0] >= box[0] - 1 and rect[1] >= box[1] - 1
            and rect[2] <= box[2] + 1 and rect[3] <= box[3] + 1)
