"""User-owned corrections to an extracted outline; source and segments stay untouched."""
from __future__ import annotations

import copy
import hashlib
import json
import threading

from folith.engine._link import core as _c
from folith.source_outline import outline_fingerprint
from folith.storage.atomic_file import replace_file

FILENAME = "source_outline_corrections.json"
_LOCK = threading.RLock()


def _path(job_id):
    return _c.job_dir(job_id) / FILENAME


def _read(job_id):
    try:
        value = json.loads(_path(job_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and isinstance(value.get("entries"), list) else {}
    except (OSError, ValueError):
        return {}


def _write(job_id, value):
    path = _path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def clear(job_id):
    with _LOCK:
        _path(job_id).unlink(missing_ok=True)


def has_corrections(job_id):
    with _LOCK:
        return bool(_read(job_id).get("entries"))


def _first_paragraph_hash(node):
    paragraphs = node.get("paragraphs") or []
    if not paragraphs or not str(paragraphs[0] or "").strip():
        return None
    return hashlib.sha256(str(paragraphs[0]).encode("utf-8")).hexdigest()


def _reparse_matches(nodes, entry):
    anchor = entry.get("first_paragraph_sha256")
    if not anchor or not entry.get("original_title_unique"):
        return []
    return [node for node in nodes if node.get("title") == entry.get("original_title")
            and _first_paragraph_hash(node) == anchor]


def save(job_id, extracted, node_id, *, title=None, level=None, para_start=None, delete=False):
    """Save one correction against the extracted identity, never an edited title."""
    nodes = extracted.get("nodes") or []
    node = next((n for n in nodes if n.get("id") == node_id), None)
    if node is None:
        raise ValueError("Unknown outline node")
    paragraphs = extracted.get("paragraphs") or []
    if title is not None and not str(title).strip():
        raise ValueError("Title cannot be empty")
    if level is not None and not 1 <= int(level) <= 20:
        raise ValueError("Level must be between 1 and 20")
    if para_start is not None and not 0 <= int(para_start) < len(paragraphs):
        raise ValueError("Paragraph is outside the document")
    with _LOCK:
        saved = _read(job_id)
        if saved.get("fingerprint") != extracted.get("fingerprint"):
            migrated = []
            for old in saved.get("entries") or []:
                matches = _reparse_matches(nodes, old)
                if len(matches) != 1:
                    continue
                updated = dict(old, node_id=matches[0]["id"])
                if "para_start" in updated:
                    positions = [i for i, text in enumerate(paragraphs)
                                 if text == updated.get("boundary_text")]
                    if len(positions) != 1:
                        continue
                    updated["para_start"] = positions[0]
                migrated.append(updated)
            saved = {"fingerprint": extracted.get("fingerprint"), "entries": migrated}
        entries = saved["entries"]
        entry = next((e for e in entries if e.get("node_id") == node_id), None)
        if entry is None:
            entry = {"node_id": node_id, "original_title": node.get("title", ""),
                     "first_paragraph_sha256": _first_paragraph_hash(node),
                     "original_title_unique": sum(n.get("title") == node.get("title") for n in nodes) == 1}
            entries.append(entry)
        if title is not None:
            entry["title"] = str(title).strip()
        if level is not None:
            entry["level"] = int(level)
        if para_start is not None:
            position = nodes.index(node)
            before = int(nodes[position - 1].get("para_start") or 0) if position else -1
            after = int(nodes[position + 1].get("para_start") or 0) if position + 1 < len(nodes) else len(paragraphs)
            if not before < int(para_start) < after:
                raise ValueError("The boundary must remain between adjacent headings")
            entry["para_start"] = int(para_start)
            entry["boundary_text"] = paragraphs[int(para_start)]
        if delete:
            entry["delete"] = True
        _write(job_id, saved)


def apply(job_id, extracted):
    """Apply exact identities, or unique title and paragraph matches after a reparse.

    Returns the effective outline and a count of corrections that could not be
    safely matched. A changed boundary is never reidentified by position alone.
    """
    with _LOCK:
        saved = _read(job_id)
    entries = saved.get("entries") or []
    if not entries:
        return copy.deepcopy(extracted), 0
    result = copy.deepcopy(extracted)
    nodes = result.get("nodes") or []
    paragraphs = result.get("paragraphs") or []
    same = saved.get("fingerprint") == extracted.get("fingerprint")
    used = set()
    dropped = 0
    structural = False
    for entry in entries:
        if not same and not entry.get("original_title_unique"):
            dropped += 1
            continue
        candidates = ([n for n in nodes if n.get("id") == entry.get("node_id")
                       and (not entry.get("first_paragraph_sha256") or
                            _first_paragraph_hash(n) == entry["first_paragraph_sha256"])]
                      if same else _reparse_matches(nodes, entry))
        if len(candidates) != 1 or candidates[0]["id"] in used:
            dropped += 1
            continue
        node = candidates[0]
        start = entry.get("para_start")
        if start is not None and not same:
            positions = [i for i, text in enumerate(paragraphs) if text == entry.get("boundary_text")]
            if len(positions) != 1:
                dropped += 1
                continue
            start = positions[0]
        if start is not None:
            position = nodes.index(node)
            before = int(nodes[position - 1].get("para_start") or 0) if position else -1
            after = int(nodes[position + 1].get("para_start") or 0) if position + 1 < len(nodes) else len(paragraphs)
            if not before < int(start) < after:
                dropped += 1
                continue
        used.add(node["id"])
        if entry.get("delete"):
            node["_deleted"] = True
        else:
            for key in ("title", "level"):
                if key in entry:
                    node[key] = entry[key]
            if start is not None:
                node["para_start"] = int(start)
            structural |= start is not None or "level" in entry
            node["provenance"] = "manual"
    # Recalculate spans only for structural edits; deleting a navigation node
    # alone must not change the range of any remaining node.
    if structural:
        for i, node in enumerate(nodes):
            start = int(node.get("para_start") or 0)
            level = int(node.get("level") or 1)
            end = next((int(n.get("para_start") or 0) for n in nodes[i + 1:]
                        if int(n.get("level") or 1) <= level), len(paragraphs))
            texts = paragraphs[start:max(start, end)]
            node.update(para_count=len(texts), paragraphs=texts,
                        chars=sum(len(t) for t in texts), sample=" ".join(texts[0].split())[:160] if texts else "")
    nodes = [n for n in nodes if not n.pop("_deleted", False)]
    stack = []
    for node in nodes:
        level = int(node.get("level") or 0)
        while stack and stack[-1][0] >= level:
            stack.pop()
        node["parent_id"] = stack[-1][1] if level and stack else ""
        if level:
            stack.append((level, node["id"]))
    result["nodes"] = nodes
    if used:
        # Include level/provenance in the cache key, even when positions are unchanged.
        result["fingerprint"] = hashlib.sha256(json.dumps(
            [outline_fingerprint(result), nodes], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result, dropped
