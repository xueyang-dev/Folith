"""Explicit, model-free outline recovery for documents without native headings."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from folith.engine._link import core as _c
from folith import source_outline
from folith.storage.atomic_file import replace_file

FILENAME = "source_outline_recovery.json"
MAX_AI_INPUT_CHARS = 6000
MAX_AI_CALLS = 1
AI_SYSTEM = ("Identify actual headings among the supplied candidates. Return only a JSON array "
             "of objects with index, level (1-3), confidence (0-1). Never create titles.")
_CHAPTER = re.compile(r"^(?:第[零〇一二三四五六七八九十百千0-9]+[章节卷篇部]|chapter\s+\d+)\b", re.I)
_CJK_CHAPTER = re.compile(r"^第[零〇一二三四五六七八九十百千0-9]+[章节卷篇部]")
_NUMBER = re.compile(r"^(\d+(?:\.\d+){0,2})(?:\.|\s)\s*\S")


def _identity(outline):
    return hashlib.sha256(json.dumps([outline.get("fingerprint"), outline.get("paragraphs") or []],
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def candidates(outline):
    """Return short, numbered paragraph candidates; font alone is never used."""
    if any(str(n.get("provenance") or "").startswith("native_")
           for n in outline.get("nodes") or []):
        return []
    found = []
    roles = outline.get("paragraph_roles") or []
    paragraphs = outline.get("paragraphs") or []
    frequencies = {}
    for paragraph in paragraphs:
        text = str(paragraph or "").strip()
        frequencies[text] = frequencies.get(text, 0) + 1
    for index, paragraph in enumerate(paragraphs):
        title = str(paragraph or "").strip()
        role = str(roles[index] if index < len(roles) else "")
        if (not title or len(title) > source_outline.TITLE_MAX_CHARS or "\n" in title
                or frequencies.get(title, 0) > 1 or role in ("header", "footer", "page_number")):
            continue
        number = _NUMBER.match(title)
        if _CHAPTER.match(title) or _CJK_CHAPTER.match(title):
            level = 2 if "节" in title[:8] else 1
        elif number:
            level = min(3, number.group(1).count(".") + 1)
        elif role == "heading" and (title.isupper() or title.istitle()) and len(title.split()) <= 8:
            level = 1
        else:
            continue
        found.append({"index": index, "title": title, "level": level})
    # Adjacent short candidates are commonly numbered list items, not headings.
    positions = {item["index"] for item in found}
    return [item for item in found if item["index"] - 1 not in positions
            and item["index"] + 1 not in positions][:source_outline.MAX_OUTLINE_NODES]


def recover(outline):
    """Return a candidate outline without changing its source paragraphs."""
    found = candidates(outline)
    if not found:
        return copy.deepcopy(outline)
    result = copy.deepcopy(outline)
    paragraphs = result.get("paragraphs") or []
    nodes = []
    if found[0]["index"]:
        start = found[0]["index"]
        nodes.append(source_outline._node(0, kind="group", title="前置内容", level=1,
                     para_start=0, paragraphs=paragraphs[:start], provenance="page_group"))
    for i, item in enumerate(found):
        start = item["index"]
        end = next((following["index"] for following in found[i + 1:]
                    if following["level"] <= item["level"]), len(paragraphs))
        nodes.append(source_outline._node(len(nodes), kind="chapter", title=item["title"],
                     level=item["level"], para_start=start, paragraphs=paragraphs[start:end],
                     provenance="rule_numbering"))
    result["nodes"] = nodes
    return source_outline._finish(result)


def save(job_id, extracted, recovered, *, previous=None, rejected_ai=False):
    path = _c.job_dir(job_id) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        payload = {"fingerprint": extracted.get("fingerprint"), "source_identity": _identity(extracted),
                   "outline": recovered}
        if rejected_ai:
            payload["rejected_ai"] = True
        if previous is not None:
            payload["previous"] = previous
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load(job_id, extracted):
    try:
        payload = json.loads((_c.job_dir(job_id) / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return extracted
    if (isinstance(payload, dict) and payload.get("source_identity") == _identity(extracted)
            and isinstance(payload.get("outline"), dict)):
        return payload["outline"]
    return extracted


def reject(job_id, native=None):
    path = _c.job_dir(job_id) / FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    previous = payload.get("previous") if isinstance(payload, dict) else None
    if isinstance(previous, dict):
        if not isinstance(native, dict) or payload.get("source_identity") != _identity(native):
            return
        save(job_id, native, previous, rejected_ai=True)
    elif payload.get("rejected_ai") and isinstance(native, dict):
        save(job_id, native, native, rejected_ai=True)
    else:
        path.unlink(missing_ok=True)


def ai_rejected(job_id, extracted):
    try:
        payload = json.loads((_c.job_dir(job_id) / FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("rejected_ai")
                and payload.get("source_identity") == _identity(extracted))


def ai_prompt(outline):
    """Build a bounded prompt from candidate lines and immediate context only."""
    paragraphs = outline.get("paragraphs") or []
    rows = []
    for candidate in candidates(outline):
        i = candidate["index"]
        row = {"index": i, "text": candidate["title"],
               "before": str(paragraphs[i - 1])[:120] if i else "",
               "after": str(paragraphs[i + 1])[:120] if i + 1 < len(paragraphs) else ""}
        trial = json.dumps(rows + [row], ensure_ascii=False)
        if len(trial) + len(AI_SYSTEM) > MAX_AI_INPUT_CHARS:
            break
        rows.append(row)
    candidate_indices = {row["index"] for row in rows}
    if len(candidate_indices) >= len(paragraphs):
        return "[]", []
    covered = set(candidate_indices)
    for row in rows:
        i = row["index"]
        if row["before"]:
            covered.add(i - 1)
        if row["after"]:
            covered.add(i + 1)
    if len(covered) >= len(paragraphs):
        for row in rows:
            row["before"] = row["after"] = ""
    return json.dumps(rows, ensure_ascii=False), rows


def from_ai(outline, rows, response):
    """Accept only listed source candidates, never model-authored titles."""
    try:
        parsed = json.loads(response)
    except (ValueError, TypeError):
        raise ValueError("Invalid structure analysis response") from None
    if not isinstance(parsed, list):
        raise ValueError("Invalid structure analysis response")
    allowed = {row["index"]: row["text"] for row in rows}
    chosen = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        index, level, confidence = item.get("index"), item.get("level"), item.get("confidence")
        if (type(index) is not int or index not in allowed or type(level) is not int
                or not 1 <= level <= 3 or type(confidence) not in (int, float)
                or not 0 <= confidence <= 1 or not allowed[index].strip()):
            continue
        chosen[index] = (level, float(confidence))
    if not chosen:
        raise ValueError("No valid source candidates were returned")
    result = copy.deepcopy(outline)
    paragraphs = result.get("paragraphs") or []
    selected = sorted(chosen)
    nodes = []
    if selected[0]:
        nodes.append(source_outline._node(0, kind="group", title="前置内容", level=1,
                     para_start=0, paragraphs=paragraphs[:selected[0]], provenance="page_group"))
    for pos, index in enumerate(selected):
        level, confidence = chosen[index]
        end = next((following for following in selected[pos + 1:]
                    if chosen[following][0] <= level), len(paragraphs))
        node = source_outline._node(len(nodes), kind="chapter", title=allowed[index],
                 level=level, para_start=index, paragraphs=paragraphs[index:end],
                 provenance="ai_inferred")
        node["confidence"] = confidence
        nodes.append(node)
    result["nodes"] = nodes
    return source_outline._finish(result)
