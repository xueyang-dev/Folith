"""DocumentOutline persistence and segment anchoring (#186).

Navigation identity only: provides deterministic mapping from DocumentOutline nodes
to working segment UIDs and indices for workspace navigation and inspection.

Architectural boundary (see docs/architecture-boundaries.md):
"版本内 Instance ID、source XML anchor、跨版本 correspondence 各自独立；
不能把段落索引或 UUID 当成 Source Update 算法。"
This anchor is strictly for navigation within a task. It MUST NOT be used or
presented as a source-update (#152) or round-trip package reconstruction (#26) anchor.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Mapping

from folith.source_outline import (
    OUTLINE_PARSER_VERSION,
    empty_outline,
    outline_fingerprint,
    parse_source_outline,
)
from folith.storage.atomic_file import replace_file

logger = logging.getLogger(__name__)

#: Persisted sidecar filename in the job directory.
SOURCE_OUTLINE_FILE = "source_outline.json"

#: Document outline sidecar schema version.
SOURCE_OUTLINE_VERSION = 1

#: Field on segment pair holding its stable instance identifier.
SEGMENT_UID_FIELD = "segment_uid"

#: Field on segment pair holding raw source paragraph ordinals (from epub.py).
SOURCE_REFS = "epub_source_paragraphs"


def _normalize_text(text: Any) -> str:
    """Collapse all whitespace and strip string."""
    return " ".join(str(text or "").split()).strip()


def structure_revision(state: Mapping[str, Any] | None) -> int:
    """Return the current structural edit count (history + exclusions)."""
    if not isinstance(state, Mapping):
        return 0
    history = state.get("segment_structure_history") or []
    exclusions = state.get("excluded_segments") or []
    return len(history) + len(exclusions)


def source_outline_path(job_id: str) -> Path:
    """Return the absolute Path to source_outline.json for the given job."""
    import core
    return core.job_dir(job_id) / SOURCE_OUTLINE_FILE


def clear_source_outline(job_id: str) -> None:
    """Delete the source outline sidecar for the given job if it exists."""
    path = source_outline_path(job_id)
    path.unlink(missing_ok=True)


def load_source_outline(job_id: str) -> dict | None:
    """Read and return the persisted source outline sidecar dict, or None if absent/invalid."""
    path = source_outline_path(job_id)
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict) and "outline" in data:
            return data
    except Exception as exc:
        logger.warning("Failed to read source_outline.json for %s: %s", job_id, exc)
    return None


def save_source_outline(job_id: str, payload: dict) -> None:
    """Atomically write the source outline sidecar to job_dir(job_id) / 'source_outline.json'."""
    path = source_outline_path(job_id)
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (SOURCE_OUTLINE_FILE + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def match_paragraph_positions(
    query_paragraphs: list[str],
    raw_paragraphs: list[str],
    *,
    start_hint: int = 0,
) -> list[int | None]:
    """Match a sequence of query paragraphs against raw source paragraphs monotonically.

    Parameters:
        query_paragraphs: paragraphs belonging to an outline node (or document slice).
        raw_paragraphs: full list of raw extracted source paragraphs (state["source_paras"]).
        start_hint: 0-based paragraph index hint where searching should begin (e.g. node["para_start"]).

    Returns:
        A list of length len(query_paragraphs), where each element is the index in
        raw_paragraphs that matched, or None if no match was found.
    """
    if not query_paragraphs or not raw_paragraphs:
        return [None] * len(query_paragraphs or [])

    normalized_raw = [_normalize_text(p) for p in raw_paragraphs]
    n_raw = len(raw_paragraphs)
    matches: list[int | None] = []

    cursor = max(0, min(start_hint, n_raw - 1)) if n_raw > 0 else 0

    for q in query_paragraphs:
        norm_q = _normalize_text(q)
        if not norm_q:
            matches.append(None)
            continue

        found_idx = None
        # Monotonic search from current cursor
        for j in range(cursor, n_raw):
            if normalized_raw[j] == norm_q:
                found_idx = j
                break

        # Fallback search from 0 to cursor if cursor was shifted forward by hint
        if found_idx is None and cursor > 0:
            for j in range(0, cursor):
                if normalized_raw[j] == norm_q:
                    found_idx = j
                    break

        if found_idx is not None:
            matches.append(found_idx)
            cursor = found_idx + 1
        else:
            matches.append(None)

    return matches


def node_source_range(
    node: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    raw_paragraphs: list[str] | None = None,
    cleaned_paragraphs: list[str] | None = None,
    output_input_indices: list[list[int]] | None = None,
    paragraph_ranges: list[dict] | None = None,
    pairs: list[dict] | None = None,
) -> dict:
    """Resolve an outline node to segment space with honest partial results.

    Returns a dict with:
        state: 'resolved' | 'partial' | 'unresolved'
        reason: '' | 'no_paragraph_match' | 'cleanup_lineage_missing' |
                'structure_edited' | 'node_excluded' | 'positional_fallback'
        segment_uids: list[str]
        first_segment_index: int | None
        unplaced_paragraphs: int
        matched_paragraphs: int
        matched_span: [int, int] | None  # [first_segment_index, last_segment_index + 1]
    """
    raw = raw_paragraphs if raw_paragraphs is not None else (state.get("source_paras") or [])
    cleaned = cleaned_paragraphs if cleaned_paragraphs is not None else (state.get("source_paragraphs") or [])
    if not cleaned:
        cleaned = raw
    cleanup = state.get("source_cleanup") or {}
    origins = output_input_indices if output_input_indices is not None else cleanup.get("output_input_indices")
    segmentation = state.get("segmentation") or {}
    p_ranges = paragraph_ranges if paragraph_ranges is not None else (segmentation.get("paragraph_ranges") or [])
    pair_list = pairs if pairs is not None else (state.get("pairs") or [])
    paras_list = state.get("paras") or []

    query = list(node.get("paragraphs") or [])
    if not query:
        return {
            "state": "unresolved",
            "reason": "no_paragraph_match",
            "segment_uids": [],
            "first_segment_index": None,
            "unplaced_paragraphs": 0,
            "matched_paragraphs": 0,
            "matched_span": None,
        }

    matches = match_paragraph_positions(
        query, raw, start_hint=int(node.get("para_start") or 0))
    matched_raw_indices = [idx for idx in matches if idx is not None]
    matched_count = len(matched_raw_indices)
    unplaced_count = len(query) - matched_count

    if matched_count == 0:
        return {
            "state": "unresolved",
            "reason": "no_paragraph_match",
            "segment_uids": [],
            "first_segment_index": None,
            "unplaced_paragraphs": unplaced_count,
            "matched_paragraphs": 0,
            "matched_span": None,
        }

    # Trace raw paragraphs -> cleaned paragraphs
    if origins is None:
        if len(raw) == len(cleaned) or not cleaned:
            origins = [[i] for i in range(len(raw))]
        elif [_normalize_text(p) for p in raw] == [_normalize_text(p) for p in cleaned]:
            origins = [[i] for i in range(len(raw))]
        else:
            return {
                "state": "unresolved",
                "reason": "cleanup_lineage_missing",
                "segment_uids": [],
                "first_segment_index": None,
                "unplaced_paragraphs": unplaced_count,
                "matched_paragraphs": matched_count,
                "matched_span": None,
            }

    raw_to_cleaned: dict[int, list[int]] = {}
    for c_idx, raw_idxs in enumerate(origins):
        if isinstance(raw_idxs, list):
            for r in raw_idxs:
                if isinstance(r, int):
                    raw_to_cleaned.setdefault(r, []).append(c_idx)

    # Trace cleaned paragraphs -> segments
    cleaned_to_segments: dict[int, list[int]] = {}
    for row in p_ranges:
        if isinstance(row, dict):
            p_idx = row.get("paragraph_index")
            start, end = row.get("start"), row.get("end")
            if (isinstance(p_idx, int) and isinstance(start, int)
                    and isinstance(end, int) and start <= end):
                cleaned_to_segments.setdefault(p_idx, []).extend(range(start, end))

    # Alternate/fallback: SOURCE_REFS on pairs
    raw_to_segments: dict[int, list[int]] = {}
    if pair_list and any(SOURCE_REFS in p for p in pair_list if isinstance(p, dict)):
        for s_idx, p in enumerate(pair_list):
            if isinstance(p, dict):
                for r in p.get(SOURCE_REFS) or []:
                    if isinstance(r, int):
                        raw_to_segments.setdefault(r, []).append(s_idx)
    elif not p_ranges and (len(cleaned) == (len(pair_list) or len(paras_list))):
        cleaned_to_segments = {i: [i] for i in range(len(pair_list) or len(paras_list))}

    matched_seg_indices: list[int] = []
    for r in matched_raw_indices:
        if raw_to_segments and r in raw_to_segments:
            matched_seg_indices.extend(raw_to_segments[r])
        else:
            for c in raw_to_cleaned.get(r, []):
                matched_seg_indices.extend(cleaned_to_segments.get(c, []))

    matched_seg_indices = list(dict.fromkeys(matched_seg_indices))
    total_segs = len(pair_list) if pair_list else len(paras_list)
    active_seg_indices = [s for s in matched_seg_indices if 0 <= s < total_segs]

    if not active_seg_indices:
        excluded = state.get("excluded_segments") or []
        if excluded:
            return {
                "state": "unresolved",
                "reason": "node_excluded",
                "segment_uids": [],
                "first_segment_index": None,
                "unplaced_paragraphs": unplaced_count,
                "matched_paragraphs": matched_count,
                "matched_span": None,
            }
        return {
            "state": "unresolved",
            "reason": "no_paragraph_match",
            "segment_uids": [],
            "first_segment_index": None,
            "unplaced_paragraphs": unplaced_count,
            "matched_paragraphs": matched_count,
            "matched_span": None,
        }

    first_seg = active_seg_indices[0]
    matched_span = [active_seg_indices[0], active_seg_indices[-1] + 1]

    segment_uids: list[str] = []
    has_any_uids = False
    for s in active_seg_indices:
        if s < len(pair_list) and isinstance(pair_list[s], dict):
            uid = str(pair_list[s].get(SEGMENT_UID_FIELD) or "")
            if uid:
                segment_uids.append(uid)
                has_any_uids = True

    is_partial = (unplaced_count > 0) or (len(active_seg_indices) < len(matched_seg_indices))
    res_state = "partial" if is_partial else "resolved"
    reason = ""

    # Legacy tasks with no segment uids:
    # If structure is unchanged, resolve positionally and record positional_fallback.
    # If structure has changed, strictly refuse positional resolution.
    if pair_list and not has_any_uids:
        if structure_revision(state) == 0:
            reason = "positional_fallback"
        else:
            return {
                "state": "unresolved",
                "reason": "structure_edited",
                "segment_uids": [],
                "first_segment_index": None,
                "unplaced_paragraphs": unplaced_count,
                "matched_paragraphs": matched_count,
                "matched_span": None,
            }

    return {
        "state": res_state,
        "reason": reason,
        "segment_uids": segment_uids,
        "first_segment_index": first_seg,
        "unplaced_paragraphs": unplaced_count,
        "matched_paragraphs": matched_count,
        "matched_span": matched_span,
    }


def resolve_outline_mapping(
    outline: dict,
    state: dict,
    *,
    cached_mapping: dict | None = None,
) -> dict:
    """Resolve outline nodes to segments with strict structural edit protection.

    When structure_revision differs from cached_mapping, previous positional indices
    are strictly invalidated and re-resolved by segment_uid. Nodes whose uids were
    altered/destroyed by split/merge are reported as 'structure_edited' with
    first_segment_index=None, never guessed by position.
    """
    current_rev = structure_revision(state)
    cached_rev = (cached_mapping or {}).get("structure_revision")
    nodes_map: dict[str, dict] = {}
    nodes = outline.get("nodes") or []
    pairs = state.get("pairs") or []
    history = state.get("segment_structure_history") or []
    excluded = state.get("excluded_segments") or []

    # If cached_mapping is current and complete, reuse it
    if (cached_mapping and cached_rev is not None
            and cached_rev == current_rev
            and cached_mapping.get("nodes")):
        cached_nodes = cached_mapping["nodes"]
        if all(node.get("id") in cached_nodes for node in nodes):
            return dict(cached_mapping)

    # Structural revision changed: strictly resolve by segment_uid
    if cached_rev is not None and cached_rev != current_rev:
        current_uid_to_idx = {
            str(p.get(SEGMENT_UID_FIELD)): idx
            for idx, p in enumerate(pairs)
            if isinstance(p, dict) and p.get(SEGMENT_UID_FIELD)
        }
        excluded_uids = {
            str(rec.get("pair", {}).get(SEGMENT_UID_FIELD))
            for rec in excluded
            if isinstance(rec, dict) and isinstance(rec.get("pair"), dict)
            and rec["pair"].get(SEGMENT_UID_FIELD)
        }
        excluded_node_ids = {
            str(rec.get("node_id"))
            for rec in excluded
            if isinstance(rec, dict) and rec.get("node_id")
        }

        for node in nodes:
            node_id = str(node.get("id") or "")
            cached_node_entry = (cached_mapping.get("nodes") or {}).get(node_id) or {}
            cached_uids = list(cached_node_entry.get("segment_uids") or [])

            if not cached_uids:
                nodes_map[node_id] = {
                    "state": "unresolved",
                    "reason": "structure_edited",
                    "segment_uids": [],
                    "first_segment_index": None,
                    "unplaced_paragraphs": int(node.get("para_count") or 0),
                    "matched_paragraphs": 0,
                    "matched_span": None,
                }
                continue

            active_indices = [
                current_uid_to_idx[uid] for uid in cached_uids if uid in current_uid_to_idx
            ]
            remaining_uids = [
                uid for uid in cached_uids if uid in current_uid_to_idx
            ]

            if active_indices:
                first_idx = active_indices[0]
                span = [min(active_indices), max(active_indices) + 1]
                if len(remaining_uids) == len(cached_uids):
                    state_val = "resolved"
                    reason_val = ""
                else:
                    state_val = "partial"
                    reason_val = "structure_edited"
                nodes_map[node_id] = {
                    "state": state_val,
                    "reason": reason_val,
                    "segment_uids": remaining_uids,
                    "first_segment_index": first_idx,
                    "unplaced_paragraphs": len(cached_uids) - len(remaining_uids),
                    "matched_paragraphs": len(remaining_uids),
                    "matched_span": span,
                }
            else:
                all_excluded = (node_id in excluded_node_ids) or (bool(cached_uids) and all(uid in excluded_uids for uid in cached_uids))
                reason_val = "node_excluded" if all_excluded else "structure_edited"
                nodes_map[node_id] = {
                    "state": "unresolved",
                    "reason": reason_val,
                    "segment_uids": [],
                    "first_segment_index": None,
                    "unplaced_paragraphs": len(cached_uids),
                    "matched_paragraphs": 0,
                    "matched_span": None,
                }

        fp = outline.get("fingerprint") or outline_fingerprint(outline)
        return {
            "fingerprint": fp,
            "structure_revision": current_rev,
            "nodes": nodes_map,
        }

    # Initial mapping resolution (cached_rev is None or unchanged revision)
    # Pre-compute shared structures across nodes for high performance on large documents
    raw = state.get("source_paras") or []
    cleaned = state.get("source_paragraphs") or []
    if not cleaned:
        cleaned = raw
    cleanup = state.get("source_cleanup") or {}
    origins = cleanup.get("output_input_indices")
    segmentation = state.get("segmentation") or {}
    p_ranges = segmentation.get("paragraph_ranges") or []

    for node in nodes:
        node_id = str(node.get("id") or "")
        node_res = node_source_range(
            node,
            state,
            raw_paragraphs=raw,
            cleaned_paragraphs=cleaned,
            output_input_indices=origins,
            paragraph_ranges=p_ranges,
            pairs=pairs,
        )
        # If structure had been previously edited before any mapping was created,
        # refuse positional fallback
        if history and node_res.get("reason") == "positional_fallback":
            node_res["state"] = "unresolved"
            node_res["reason"] = "structure_edited"
            node_res["first_segment_index"] = None

        nodes_map[node_id] = node_res

    fp = outline.get("fingerprint") or outline_fingerprint(outline)
    return {
        "fingerprint": fp,
        "structure_revision": current_rev,
        "nodes": nodes_map,
    }


def get_or_create_source_outline(
    job_id: str,
    *,
    state: dict | None = None,
    source_bytes: bytes | None = None,
    filename: str | None = None,
    force_reparse: bool = False,
) -> dict:
    """Load or build the persisted DocumentOutline sidecar.

    1. Checks job_dir(job_id) / 'source_outline.json'.
    2. Validates source_sha256 and parser_version. If mismatched, re-parses and overwrites.
    3. Reuses cached outline when valid (asserted: no re-extraction on unchanged source).
    4. Updates mapping if structure_revision changed, resolving by segment_uid.
    5. Saves atomically to job_dir(job_id) / 'source_outline.json'.
    """
    import core

    if state is None:
        state = core.load_job_state(job_id) or {}
    if filename is None:
        filename = str(state.get("filename") or "")
    if source_bytes is None:
        source_bytes = core.load_source(job_id)

    sidecar = load_source_outline(job_id)
    current_sha256 = (
        hashlib.sha256(source_bytes).hexdigest() if source_bytes else ""
    )

    if sidecar is not None and isinstance(sidecar.get("outline"), dict):
        reparse_reason = None
        stored_sha256 = str(sidecar.get("source_sha256") or "")
        stored_parser_version = int(sidecar.get("parser_version") or 0)

        if force_reparse:
            reparse_reason = "force_reparse requested"
        elif source_bytes and stored_sha256 and stored_sha256 != current_sha256:
            reparse_reason = f"source_sha256 changed ({stored_sha256[:8]} -> {current_sha256[:8]})"
        elif stored_parser_version != OUTLINE_PARSER_VERSION:
            reparse_reason = (
                f"parser_version changed ({stored_parser_version} -> {OUTLINE_PARSER_VERSION})"
            )

        if reparse_reason:
            logger.info("Source outline invalidated: %s; re-parsing.", reparse_reason)
            try:
                core._append_runtime_technical_log(
                    job_id, f"source outline invalidated: {reparse_reason}; re-parsing outline."
                )
            except Exception:
                pass
        else:
            # Reusing persisted outline without invoking parser!
            current_rev = structure_revision(state)
            cached_mapping = sidecar.get("mapping") or {}
            cached_rev = cached_mapping.get("structure_revision")
            if cached_rev != current_rev or not cached_mapping.get("nodes"):
                new_mapping = resolve_outline_mapping(
                    sidecar["outline"], state, cached_mapping=cached_mapping
                )
                sidecar["mapping"] = new_mapping
                save_source_outline(job_id, sidecar)
            return sidecar

    # Needs parsing
    if source_bytes:
        outline = parse_source_outline(filename, source_bytes)
    else:
        outline = empty_outline(filename)

    fp = outline.get("fingerprint") or outline_fingerprint(outline)
    mapping = resolve_outline_mapping(outline, state)

    sidecar = {
        "version": SOURCE_OUTLINE_VERSION,
        "parser_version": OUTLINE_PARSER_VERSION,
        "source_sha256": current_sha256,
        "fingerprint": fp,
        "outline": outline,
        "mapping": mapping,
    }
    if job_id:
        save_source_outline(job_id, sidecar)
    return sidecar
