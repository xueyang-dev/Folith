"""Persist a task's source outline and resolve its nodes to segments (#186).

The preparation panel parses the outline on demand (#27/#185).  Translation
starts, the in-state outline is dropped, and nothing connects a heading to a
translation unit any more.  This module owns that missing chain:

    outline node -> raw paragraph (ordered subsequence on source_paras)
                 -> cleaned paragraph (source_cleanup.output_input_indices)
                 -> segment (segmentation.paragraph_ranges)

The outline lives in ``job_dir(job_id) / "source_outline.json"``, next to the
draft sidecar, because every node carries its paragraph texts: putting those in
task state would bloat every batch save and journal entry.  The sidecar is a
derived artifact, never a second translation truth.

Resolution is honest about failures.  A node is ``resolved`` (all its
paragraphs placed), ``partial`` (some paragraphs could not be placed; the
matched span is reported) or ``unresolved`` with a reason.  Once the segment
structure changed, a cached position is *never* used as a fallback: only
``segment_uid`` can re-identify a node, and a missing uid becomes
``structure_edited``.  Positions are a cache, not an anchor.

The anchor is for navigation only.  It is not a source-update or round-trip
anchor (see ``docs/architecture-boundaries.md``).
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone

from folith import source_outline as _source_outline
from folith.engine._link import core as _c
from folith.storage.atomic_file import replace_file

OUTLINE_SIDECAR_FILE = "source_outline.json"
OUTLINE_SIDECAR_VERSION = 1

RESOLVED = "resolved"
PARTIAL = "partial"
UNRESOLVED = "unresolved"

#: Navigation states that may jump somewhere; ``UNRESOLVED`` never may.
NAVIGABLE_STATES = (RESOLVED, PARTIAL)

_WRITE_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Parse-dedup: at most one parse per job_id at a time.
# A second caller waits for the first and then reads the freshly-written sidecar.
# ---------------------------------------------------------------------------

#: Protects mutations to ``_PARSE_EVENTS``.
_PARSE_EVENTS_LOCK = threading.Lock()
#: Active parse events: ``{job_id: Event}`` set when the parse completes.
_PARSE_EVENTS: dict[str, threading.Event] = {}


# ---------------------------------------------------------------------------
# Paragraph alignment (lifted from the 0.6 WIP; shared with the resolver)
# ---------------------------------------------------------------------------

def match_paragraph_positions(outline_paragraphs: list[str],
                              source_paragraphs: list[str]) -> dict[int, int]:
    """Align the outline's paragraph order onto the extracted source paragraphs.

    Both sides come from the same extraction helpers and are usually equal.  A
    mixed PDF (text layer plus VLM pages) can interleave extra recognised
    paragraphs, so this is an ordered *subsequence* match, not a positional
    comparison.  Returns ``{outline paragraph index: source paragraph index}``;
    outline paragraphs that did not match are absent.
    """
    if list(outline_paragraphs) == list(source_paragraphs):
        return {index: index for index in range(len(outline_paragraphs))}
    positions: dict[int, int] = {}
    sources = [str(item or "") for item in source_paragraphs]
    cursor = 0
    for index, text in enumerate(outline_paragraphs):
        text = str(text or "")
        found = -1
        for probe in range(cursor, len(sources)):
            if sources[probe] == text:
                found = probe
                break
        if found < 0:
            continue
        positions[index] = found
        cursor = found + 1
    return positions


# ---------------------------------------------------------------------------
# Sidecar storage
# ---------------------------------------------------------------------------

def outline_sidecar_path(job_id: str):
    return _c.job_dir(job_id) / OUTLINE_SIDECAR_FILE


def source_sha256(data: bytes) -> str:
    return hashlib.sha256(bytes(data or b"")).hexdigest()


def _read_sidecar(job_id: str) -> dict | None:
    path = outline_sidecar_path(job_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt cache is only a cache miss
        return None
    if not isinstance(raw, dict):
        return None
    if int(raw.get("version") or 0) != OUTLINE_SIDECAR_VERSION:
        return None
    if not isinstance(raw.get("outline"), dict):
        return None
    if not isinstance(raw.get("mapping"), dict):
        raw["mapping"] = {}
    return raw


def _sidecar_payload(job_id: str, payload: dict) -> dict:
    data = dict(payload or {})
    data.update({
        "version": OUTLINE_SIDECAR_VERSION,
        "job_id": str(job_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return data


def _write_sidecar_file(job_id: str, data: dict) -> None:
    """Write an already stamped payload; the caller holds ``_WRITE_LOCK``."""
    directory = _c.job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / OUTLINE_SIDECAR_FILE
    tmp = directory / (OUTLINE_SIDECAR_FILE + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_sidecar(job_id: str, payload: dict) -> None:
    data = _sidecar_payload(job_id, payload)
    with _WRITE_LOCK:
        _write_sidecar_file(job_id, data)


def _update_mapping(job_id: str, mapping: dict) -> None:
    """Replace the mapping under the write lock (read-modify-write is atomic)."""
    with _WRITE_LOCK:
        sidecar = _read_sidecar(job_id)
        if sidecar is None:
            return
        sidecar["mapping"] = dict(mapping or {})
        _write_sidecar_file(job_id, _sidecar_payload(job_id, sidecar))


def invalidate_mapping(job_id: str) -> None:
    """Drop cached node positions; the outline itself stays.

    Source bytes did not change, so the parsed outline is still valid, but every
    position in the mapping describes the previous segmentation (#186/#204).
    A missing sidecar is fine.
    """
    with _WRITE_LOCK:
        sidecar = _read_sidecar(job_id)
        if sidecar is None or not sidecar.get("mapping"):
            return
        sidecar["mapping"] = {}
        _write_sidecar_file(job_id, _sidecar_payload(job_id, sidecar))


def delete_outline_sidecar(job_id: str) -> None:
    """Drop the persisted outline (rescan / re-import).  Missing is fine."""
    try:
        outline_sidecar_path(job_id).unlink(missing_ok=True)
    except OSError:
        pass


def store_outline(job_id: str, outline: dict, *, data: bytes | None = None,
                  source_sha256_value: str = "") -> dict:
    """Persist a freshly parsed outline; the previous mapping is discarded."""
    payload = {
        "parser_version": int((outline or {}).get("parser_version")
                              or _source_outline.OUTLINE_PARSER_VERSION),
        "source_sha256": source_sha256_value or source_sha256(data or b""),
        "fingerprint": str((outline or {}).get("fingerprint") or ""),
        "outline": dict(outline or {}),
        "mapping": {},
    }
    _write_sidecar(job_id, payload)
    return payload


def _pending_payload(reason: str) -> dict:
    """Return a payload that signals the outline is not yet available.

    Consumers check ``payload.get("pending")`` to detect this state.  The
    ``invalidated`` key carries the reason so the UI can show the right hint
    while waiting.
    """
    return {
        "outline": {},
        "parser_version": 0,
        "source_sha256": "",
        "fingerprint": "",
        "mapping": {},
        "parsed": False,
        "pending": True,
        "invalidated": reason,
        "node_count": 0,
    }


def load_outline(job_id: str, state: dict | None = None, *,
                 parser=None, no_parse: bool = False) -> dict:
    """Return the task's outline, parsing the source only when the cache is stale.

    The returned payload is the sidecar shape plus two facts:
    ``parsed`` (the parser ran during this call) and ``invalidated``
    (``missing`` / ``source_changed`` / ``parser_changed`` / ``source_missing``).
    Nothing here is navigable state; call :func:`resolve` for that.

    When ``no_parse=True`` and the sidecar is cold or missing, the function
    returns immediately with a payload whose ``pending`` key is ``True`` instead
    of running the (potentially expensive) parser.  The caller is responsible
    for scheduling the parse via :func:`schedule_outline_build`.

    When ``no_parse=False`` (the default) and a concurrent parse is already
    running for the same ``job_id``, this call waits for it to finish and then
    reads the freshly-written sidecar, so only one parse ever runs.
    """
    state = state if isinstance(state, dict) else {}
    sidecar = _read_sidecar(job_id)
    data = _c.load_source(job_id)
    if not data:
        if sidecar is not None:
            return {
                "outline": sidecar["outline"],
                "parser_version": int(sidecar.get("parser_version") or 0),
                "source_sha256": str(sidecar.get("source_sha256") or ""),
                "fingerprint": str(sidecar.get("fingerprint") or ""),
                "mapping": sidecar.get("mapping") or {},
                "parsed": False,
                "invalidated": "source_missing",
            }
        stored = state.get("source_outline")
        outline = stored if isinstance(stored, dict) else _source_outline.empty_outline(
            str(state.get("filename") or ""))
        return {
            "outline": outline,
            "parser_version": int(outline.get("parser_version") or 0),
            "source_sha256": "",
            "fingerprint": str(outline.get("fingerprint") or ""),
            "mapping": {},
            "parsed": False,
            "invalidated": "source_missing" if outline.get("nodes") else "",
        }

    digest = source_sha256(data)
    version = _source_outline.OUTLINE_PARSER_VERSION
    if sidecar is not None:
        cached_version = int(sidecar.get("parser_version") or 0)
        if sidecar.get("source_sha256") == digest and cached_version == version:
            return {
                "outline": sidecar["outline"],
                "parser_version": cached_version,
                "source_sha256": digest,
                "fingerprint": str(sidecar.get("fingerprint")
                                     or sidecar["outline"].get("fingerprint") or ""),
                "mapping": sidecar.get("mapping") or {},
                "parsed": False,
                "invalidated": "",
            }
        reason = "parser_changed" if cached_version != version else "source_changed"
    else:
        reason = "missing"

    # Sidecar is cold or missing.
    if no_parse:
        return _pending_payload(reason)

    # Serialise concurrent cold parses for the same job.
    #
    # Loop invariant: after the loop body executes ``break``, ``my_event`` is
    # registered in ``_PARSE_EVENTS`` under the lock, and this thread is the
    # designated parser.  When we fall through (waiter path) we re-check the
    # sidecar after the event fires; if it is still cold we loop and try to
    # take the parser role ourselves.  The ``finally`` below always sets
    # ``my_event`` before removing it, so a waiter is never left blocked on a
    # stale event.
    while True:
        with _PARSE_EVENTS_LOCK:
            existing = _PARSE_EVENTS.get(job_id)
            if existing is None:
                my_event = threading.Event()
                _PARSE_EVENTS[job_id] = my_event
                break  # we are the designated parser
        # Another thread holds the parse slot; wait and re-check.
        existing.wait()
        sidecar2 = _read_sidecar(job_id)
        if sidecar2 is not None:
            cached_version2 = int(sidecar2.get("parser_version") or 0)
            if sidecar2.get("source_sha256") == digest and cached_version2 == version:
                return {
                    "outline": sidecar2["outline"],
                    "parser_version": cached_version2,
                    "source_sha256": digest,
                    "fingerprint": str(sidecar2.get("fingerprint")
                                       or sidecar2["outline"].get("fingerprint") or ""),
                    "mapping": sidecar2.get("mapping") or {},
                    "parsed": False,
                    "invalidated": "",
                }
        # Sidecar still absent or stale after waiting (the other parse may have
        # failed).  Loop and compete for the parser slot again.

    try:
        parse = parser or _source_outline.parse_source_outline
        parsed = parse(str(state.get("filename") or ""), data)
        payload = store_outline(job_id, parsed, source_sha256_value=digest)
        return {
            "outline": parsed,
            "parser_version": int(parsed.get("parser_version") or version),
            "source_sha256": digest,
            "fingerprint": str(parsed.get("fingerprint") or ""),
            "mapping": {},
            "parsed": True,
            "invalidated": reason,
        }
    finally:
        with _PARSE_EVENTS_LOCK:
            _PARSE_EVENTS.pop(job_id, None)
        my_event.set()


def schedule_outline_build(job_id: str, state: dict | None = None) -> bool:
    """Return True if a background parse is already in progress for this job.

    This is a read-only probe: it does not start anything.  The caller uses it
    to skip scheduling a new parse when one is already running.
    """
    with _PARSE_EVENTS_LOCK:
        return job_id in _PARSE_EVENTS


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def structure_revision(state: dict | None) -> int:
    """A cheap counter that changes whenever segment positions may have moved.

    ``segment_structure_version`` is the core's monotonic counter for split,
    merge, exclude, restore and undo.  Older states without it fall back to the
    history/exclusion lengths, which is what those edits used to grow.
    """
    state = state if isinstance(state, dict) else {}
    try:
        version = int(state.get("segment_structure_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version:
        return version
    return len(state.get("segment_structure_history") or []) + len(
        state.get("excluded_segments") or [])


def structure_signature(state: dict | None) -> str:
    """Content identity of the paragraph -> cleanup -> segment chain.

    ``structure_revision`` only tracks edits made *through the editor*.  A
    cleanup or re-segmentation rebuilds ``segmentation.paragraph_ranges`` and
    ``source_cleanup.output_input_indices`` without bumping it, and the segment
    uids are positional, so a cached mapping can look valid while every position
    moved.  Hashing the two structures catches that case.

    Only the position-bearing fields are hashed: the segmentation rows also
    carry per-row quality metadata (``gate``, ``separators``) that the pipeline
    may add or rewrite without moving a single segment.
    """
    state = state if isinstance(state, dict) else {}
    segmentation = state.get("segmentation")
    cleanup = state.get("source_cleanup")
    rows = segmentation.get("paragraph_ranges") \
        if isinstance(segmentation, dict) else None
    indices = cleanup.get("output_input_indices") \
        if isinstance(cleanup, dict) else None
    ranges: list[list] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ranges.append([row.get("paragraph_index"), row.get("start"), row.get("end")])
    lineage: list[list[int]] = []
    for row in indices or []:
        if not isinstance(row, (list, tuple)):
            continue
        lineage.append([int(item) for item in row
                        if isinstance(item, (int, float)) and math.isfinite(item)])
    canonical = json.dumps(
        {"paragraph_ranges": ranges, "output_input_indices": lineage},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _segment_list(state: dict) -> list:
    pairs = state.get("pairs")
    if isinstance(pairs, list):
        return pairs
    paras = state.get("paras")
    return paras if isinstance(paras, list) else []


def _uid_index(state: dict) -> tuple[dict[str, int], int]:
    segments = _segment_list(state)
    index: dict[str, int] = {}
    for position, item in enumerate(segments):
        if not isinstance(item, dict):
            continue
        uid = str(item.get("segment_uid") or "")
        if uid and uid not in index:
            index[uid] = position
    return index, len(segments)


def _by_paragraph(segmentation: dict | None) -> dict[int, list[int]]:
    rows = (segmentation or {}).get("paragraph_ranges") or []
    result: dict[int, list[int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            paragraph_index = int(row.get("paragraph_index"))
            start, end = int(row.get("start")), int(row.get("end"))
        except (TypeError, ValueError):
            continue
        result.setdefault(paragraph_index, []).extend(range(start, end))
    return result


def _cleaned_count(state: dict, output_inputs) -> int:
    if isinstance(output_inputs, list) and output_inputs:
        return len(output_inputs)
    segmentation = state.get("segmentation") or {}
    try:
        declared = int(segmentation.get("input_paragraph_count") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > 0:
        return declared
    return len(state.get("source_paras") or [])


def _cleaned_membership(cleaned_count: int, output_inputs,
                        raw_start: int, raw_end: int, raw_total: int):
    """Cleaned paragraphs that belong to a raw span, plus boundary count.

    A cleaned paragraph is node content only when *all* of its output inputs sit
    inside the span.  A paragraph merged across a boundary belongs to nobody:
    keeping it (translating one extra paragraph) is better than placing another
    node's text here.  Without lineage, only a one-to-one document is accepted.
    """
    if isinstance(output_inputs, list) and cleaned_count \
            and len(output_inputs) == cleaned_count:
        inside: list[int] = []
        boundary = 0
        for position, raws in enumerate(output_inputs):
            indexes = [int(item) for item in (raws or [])
                       if isinstance(item, (int, float))]
            if not indexes:
                continue
            hits = sum(1 for item in indexes if raw_start <= item <= raw_end)
            if hits == len(indexes):
                inside.append(position)
            elif hits:
                boundary += 1
        return inside, boundary
    if raw_total and cleaned_count == raw_total:
        return list(range(raw_start, min(raw_end + 1, cleaned_count))), 0
    return None, 0


def _raw_range(node: dict, positions: dict[int, int],
               pages_by_para: list[int]) -> tuple[tuple[int, int] | None, int, int, str]:
    count = int(node.get("para_count") or 0)
    if count > 0:
        start = int(node.get("para_start") or 0)
        hit = [positions[index] for index in range(start, start + count)
               if index in positions]
        if not hit:
            return None, 0, count, "no_paragraph_match"
        return (min(hit), max(hit)), len(hit), count, "paragraph_match"
    page_start, page_end = node.get("page_start"), node.get("page_end")
    if isinstance(page_start, int) and isinstance(page_end, int) and page_start:
        hit = [index for index, page in enumerate(pages_by_para)
               if isinstance(page, int) and page_start <= page <= page_end]
        if hit:
            return (min(hit), max(hit)), len(hit), len(hit), "page_match"
    return None, 0, 0, ("no_paragraph_text" if node.get("scanned")
                        else "no_paragraph_match")


def _entry(state_value: str, reason: str, *, first: int | None = None,
           last: int | None = None, uids: list[str] | None = None,
           matched: int = 0, unmatched: int = 0,
           boundary: int = 0) -> dict:
    return {
        "state": state_value,
        "reason": reason,
        "segment_uids": list(uids or []),
        "first_segment_index": first,
        "last_segment_index": last,
        "matched_paragraphs": int(matched),
        "unmatched_paragraphs": int(unmatched),
        "boundary_paragraphs": int(boundary),
    }


def _resolve_from_paragraphs(outline: dict, state: dict,
                             cached_nodes: dict | None = None) -> dict[str, dict]:
    source_paras = [str(item or "") for item in state.get("source_paras") or []]
    positions = match_paragraph_positions(
        [str(item or "") for item in outline.get("paragraphs") or []], source_paras)
    pages_by_para = list(
        (state.get("streaming_ingest") or {}).get("paragraph_pages") or [])
    output_inputs = (state.get("source_cleanup") or {}).get("output_input_indices")
    cleaned_count = _cleaned_count(state, output_inputs)
    ranges = _by_paragraph(state.get("segmentation"))
    segments = _segment_list(state)
    uid_index, segment_count = _uid_index(state)
    excluded = _excluded_uids(state) if cached_nodes else set()
    segmentation_missing = not ranges

    nodes: dict[str, dict] = {}
    for node in outline.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        raw_span, matched, total, match_reason = _raw_range(
            node, positions, pages_by_para)
        if raw_span is None:
            nodes[node_id] = _entry(UNRESOLVED, match_reason, matched=matched,
                                    unmatched=max(total - matched, 0))
            continue
        raw_start, raw_end = raw_span
        inside, boundary = _cleaned_membership(
            cleaned_count, output_inputs, raw_start, raw_end, len(source_paras))
        if inside is None:
            nodes[node_id] = _entry(UNRESOLVED, "cleanup_lineage_missing",
                                    matched=matched, unmatched=max(total - matched, 0))
            continue
        if segmentation_missing:
            nodes[node_id] = _entry(UNRESOLVED, "segmentation_missing",
                                    matched=matched, unmatched=max(total - matched, 0))
            continue
        segment_indexes: set[int] = set()
        for cleaned_index in inside:
            for segment_index in ranges.get(cleaned_index) or []:
                if 0 <= segment_index < segment_count:
                    segment_indexes.add(segment_index)
        if not segment_indexes:
            reason = "node_excluded" if inside else "no_segment_match"
            nodes[node_id] = _entry(UNRESOLVED, reason, matched=matched,
                                    unmatched=max(total - matched, 0),
                                    boundary=boundary)
            continue
        ordered = sorted(segment_indexes)
        uids = []
        for segment_index in ordered:
            item = segments[segment_index] if segment_index < len(segments) else {}
            uid = str(item.get("segment_uid") or "") if isinstance(item, dict) else ""
            if uid:
                uids.append(uid)
        partial = matched < total or boundary > 0
        reason = "partial_paragraph_match" if partial else match_reason
        if not uids:
            reason = "positional_fallback"
        entry = _entry(
            PARTIAL if partial else RESOLVED, reason,
            first=ordered[0], last=ordered[-1], uids=uids,
            matched=matched, unmatched=max(total - matched, 0), boundary=boundary)
        nodes[node_id] = _exclusion_refined(
            entry, (cached_nodes or {}).get(node_id), uid_index, excluded)
    return nodes


def _exclusion_refined(entry: dict, cached, uid_index: dict[str, int],
                       excluded: set[str]) -> dict:
    """Keep a paragraph-resolved node honest about a partial exclusion.

    Excluding segments rewrites ``paragraph_ranges``, so the structure
    signature changes and the resolver falls back to the paragraph chain.  That
    chain can place the node correctly and still not know that part of it left
    the work list.  The cached uids can: a cached uid that is gone from the
    current list but present in the exclusion ledger was removed by the user,
    while the rest of the node is still there.  Uids that vanished for any
    other reason (a split assigns new ones) fail the ledger check below and
    keep the paragraph chain's answer.

    The snapshot only supplies the *label*; the span stays the paragraph
    chain's.  A uid names one segment, and a re-segmentation reissues the
    positional ``seg-<job>-<n>`` strings, so a snapshot taken before one can
    still name existing uids that now belong to other segments.  When the
    surviving uids do not match what the chain just placed, the snapshot says
    nothing about this node and is ignored.
    """
    if entry.get("state") not in NAVIGABLE_STATES or not isinstance(cached, dict):
        return entry
    cached_uids = [str(uid) for uid in cached.get("segment_uids") or [] if str(uid)]
    missing = [uid for uid in cached_uids if uid not in uid_index]
    if not cached_uids or not missing \
            or not all(uid in excluded for uid in missing):
        return entry
    current = [str(uid) for uid in entry.get("segment_uids") or [] if str(uid)]
    survived = [uid for uid in cached_uids if uid in uid_index]
    if not current or survived != current:
        return entry
    return _entry(PARTIAL, "node_partial_excluded",
                  first=entry.get("first_segment_index"),
                  last=entry.get("last_segment_index"), uids=cached_uids,
                  matched=entry.get("matched_paragraphs") or 0,
                  unmatched=entry.get("unmatched_paragraphs") or 0,
                  boundary=entry.get("boundary_paragraphs") or 0)


def _verify_cached(nodes: dict, state: dict) -> bool:
    """Whether a cached mapping still describes this exact structure."""
    uid_index, segment_count = _uid_index(state)
    for entry in nodes.values():
        if not isinstance(entry, dict):
            return False
        first = entry.get("first_segment_index")
        last = entry.get("last_segment_index", first)
        if first is None and last is None:
            continue
        if not isinstance(first, int) or not 0 <= first < segment_count:
            return False
        if isinstance(last, int) and not first <= last < segment_count:
            return False
        uids = [str(uid) for uid in entry.get("segment_uids") or [] if str(uid)]
        if uids:
            positions = [uid_index[uid] for uid in uids if uid in uid_index]
            if len(positions) != len(uids):
                return False
            if min(positions) != first:
                return False
    return True


def _excluded_uids(state: dict) -> set[str]:
    """uids that left ``pairs`` through the exclusion ledger (not a structural edit)."""
    found: set[str] = set()
    for record in state.get("excluded_segments") or []:
        if not isinstance(record, dict):
            continue
        pair = record.get("pair")
        uid = str((pair or {}).get("segment_uid") or "") \
            if isinstance(pair, dict) else ""
        if uid:
            found.add(uid)
    return found


def _resolve_by_uid(cached_nodes: dict, state: dict) -> dict[str, dict]:
    """Re-identify nodes by stable uid after a structural change.

    A position cached before the change is not used at all.  A node keeps its
    state when every uid still exists.  Uids that only left through the
    exclusion ledger become ``node_excluded``; anything else becomes
    ``structure_edited`` rather than an off-by-N jump.
    """
    uid_index, _ = _uid_index(state)
    excluded = _excluded_uids(state)
    nodes: dict[str, dict] = {}
    for node_id, entry in cached_nodes.items():
        if not isinstance(entry, dict):
            continue
        uids = [str(uid) for uid in entry.get("segment_uids") or [] if str(uid)]
        positions = [uid_index[uid] for uid in uids if uid in uid_index]
        missing = [uid for uid in uids if uid not in uid_index]
        matched = int(entry.get("matched_paragraphs") or 0)
        unmatched = int(entry.get("unmatched_paragraphs") or 0)
        boundary = int(entry.get("boundary_paragraphs") or 0)
        if uids and not missing:
            # Every uid is back: a node that was only partially excluded is
            # whole again.  A partial *paragraph match* stays partial.
            still_partial = (entry.get("state") == PARTIAL
                             and entry.get("reason") != "node_partial_excluded")
            state_value = PARTIAL if still_partial else RESOLVED
            nodes[node_id] = _entry(
                state_value, "uid_match", first=min(positions), last=max(positions),
                uids=uids, matched=matched, unmatched=unmatched, boundary=boundary)
        elif missing and all(uid in excluded for uid in missing):
            if positions:
                # Part of the node was excluded: still navigable, but say so.
                nodes[node_id] = _entry(
                    PARTIAL, "node_partial_excluded", first=min(positions),
                    last=max(positions), uids=uids, matched=matched,
                    unmatched=unmatched, boundary=boundary)
            else:
                nodes[node_id] = _entry(
                    UNRESOLVED, "node_excluded", uids=uids, matched=matched,
                    unmatched=unmatched, boundary=boundary)
        elif not uids:
            # Legacy entry: its position was a cache, not an anchor.  After a
            # structural change it cannot be re-identified safely; an entry
            # that was already unresolved keeps its original reason.
            if entry.get("state") in NAVIGABLE_STATES:
                nodes[node_id] = _entry(
                    UNRESOLVED, "structure_edited", matched=matched,
                    unmatched=unmatched, boundary=boundary)
            else:
                nodes[node_id] = dict(entry)
        else:
            nodes[node_id] = _entry(
                UNRESOLVED, "structure_edited", uids=uids, matched=matched,
                unmatched=unmatched, boundary=boundary)
    return nodes


def resolve(job_id: str, state: dict, payload: dict | None = None, *,
            persist: bool = True) -> dict:
    """Resolve outline nodes to segments; returns the mapping wrapper.

    ``mapping = {fingerprint, structure_revision, structure_signature,
    nodes: {node_id: entry}}``.  ``payload`` may be the value returned by
    :func:`load_outline` so a task open parses at most once; the resolved
    mapping is written back into it, and when it carries no mapping yet the
    persisted one is read (a caller may hold the snapshot from before the
    first resolve).
    """
    state = state if isinstance(state, dict) else {}
    given_payload = payload if isinstance(payload, dict) else None
    payload = given_payload if given_payload is not None else load_outline(job_id, state)
    outline = payload.get("outline") or {}
    fingerprint = str(outline.get("fingerprint")
                      or payload.get("fingerprint") or "")
    revision = structure_revision(state)
    signature = structure_signature(state)
    cached = payload.get("mapping") or {}
    if not cached and given_payload is not None:
        # ``payload`` can be a snapshot held for the whole task (the Qt view
        # keeps the one read at task open).  A mapping resolved after that read
        # exists only in the sidecar, and its uids are what prove a partial
        # exclusion below.
        cached = (_read_sidecar(job_id) or {}).get("mapping") or {}
    cached_nodes = cached.get("nodes") if isinstance(cached.get("nodes"), dict) else {}

    same_document = (cached.get("fingerprint") == fingerprint and bool(cached_nodes))
    # A mapping written before the structure signature existed, or one whose
    # paragraph/cleanup chain changed (cleanup + resegmentation), cannot say
    # anything about the current positions.  Only the paragraph path can.
    same_structure = (bool(cached.get("structure_signature"))
                      and cached.get("structure_signature") == signature)
    cached_revision = cached.get("structure_revision")
    cached_revision = int(cached_revision) if isinstance(cached_revision, int) else None
    if same_document and same_structure and cached_revision == revision:
        if _verify_cached(cached_nodes, state):
            return {"fingerprint": fingerprint, "structure_revision": revision,
                    "structure_signature": signature, "nodes": cached_nodes,
                    "source": "cache"}
        nodes = _resolve_by_uid(cached_nodes, state)
        source = "uid"
    elif same_document and same_structure and cached_nodes:
        nodes = _resolve_by_uid(cached_nodes, state)
        source = "uid"
    else:
        nodes = _resolve_from_paragraphs(
            outline, state, cached_nodes if same_document else None)
        source = "paragraphs"

    mapping = {
        "fingerprint": fingerprint,
        "structure_revision": revision,
        "structure_signature": signature,
        "nodes": nodes,
    }
    if persist:
        _update_mapping(job_id, mapping)
    if isinstance(payload, dict):
        payload["mapping"] = mapping
    return {**mapping, "source": source}
