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


def _write_sidecar(job_id: str, payload: dict) -> None:
    directory = _c.job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / OUTLINE_SIDECAR_FILE
    data = dict(payload or {})
    data.update({
        "version": OUTLINE_SIDECAR_VERSION,
        "job_id": str(job_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    tmp = directory / (OUTLINE_SIDECAR_FILE + ".tmp")
    with _WRITE_LOCK:
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            replace_file(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


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


def load_outline(job_id: str, state: dict | None = None, *, parser=None) -> dict:
    """Return the task's outline, parsing the source only when the cache is stale.

    The returned payload is the sidecar shape plus two facts:
    ``parsed`` (the parser ran during this call) and ``invalidated``
    (``missing`` / ``source_changed`` / ``parser_changed`` / ``source_missing``).
    Nothing here is navigable state; call :func:`resolve` for that.
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


def _resolve_from_paragraphs(outline: dict, state: dict) -> dict[str, dict]:
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
        nodes[node_id] = _entry(
            PARTIAL if partial else RESOLVED, reason,
            first=ordered[0], last=ordered[-1], uids=uids,
            matched=matched, unmatched=max(total - matched, 0), boundary=boundary)
    return nodes


def _verify_cached(nodes: dict, state: dict) -> bool:
    """Whether a cached mapping still describes this exact structure."""
    segments = _segment_list(state)
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
        elif first >= len(segments):
            # Legacy positional cache: usable only while the structure is whole.
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
            state_value = (PARTIAL if entry.get("state") == PARTIAL else RESOLVED)
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

    ``mapping = {fingerprint, structure_revision, nodes: {node_id: entry}}``.
    ``payload`` may be the value returned by :func:`load_outline` so a task open
    parses at most once.
    """
    state = state if isinstance(state, dict) else {}
    payload = payload if isinstance(payload, dict) else load_outline(job_id, state)
    outline = payload.get("outline") or {}
    fingerprint = str(outline.get("fingerprint")
                      or payload.get("fingerprint") or "")
    revision = structure_revision(state)
    cached = payload.get("mapping") or {}
    cached_nodes = cached.get("nodes") if isinstance(cached.get("nodes"), dict) else {}

    same_document = (cached.get("fingerprint") == fingerprint and bool(cached_nodes))
    cached_revision = cached.get("structure_revision")
    cached_revision = int(cached_revision) if isinstance(cached_revision, int) else None
    if same_document and cached_revision == revision:
        if _verify_cached(cached_nodes, state):
            return {"fingerprint": fingerprint, "structure_revision": revision,
                    "nodes": cached_nodes, "source": "cache"}
        nodes = _resolve_by_uid(cached_nodes, state)
        source = "uid"
    elif same_document and cached_nodes:
        nodes = _resolve_by_uid(cached_nodes, state)
        source = "uid"
    else:
        nodes = _resolve_from_paragraphs(outline, state)
        source = "paragraphs"

    mapping = {
        "fingerprint": fingerprint,
        "structure_revision": revision,
        "nodes": nodes,
    }
    if persist:
        sidecar = _read_sidecar(job_id)
        if sidecar is not None:
            sidecar["mapping"] = mapping
            _write_sidecar(job_id, sidecar)
    return {**mapping, "source": source}
