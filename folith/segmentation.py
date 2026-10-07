"""Deterministic source segmentation for the CAT workbench.

Source extraction and OCR cleanup intentionally remain paragraph-oriented: the
cleanup model needs the surrounding physical layout to repair broken lines and
paragraph boundaries.  Translation, however, works better with sentence-sized
units.  This module is the small, deterministic boundary between those two
contracts.

The splitter is deliberately conservative.  It never joins neighbouring
paragraphs, does not split on commas/colons/semicolons, and keeps headings,
list items, table-like rows, URLs, e-mail addresses, abbreviations and decimal
numbers intact.  Ambiguous material remains one CAT unit and can still be
edited or split manually in the workbench.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple


RULES_VERSION = 5
MIGRATION_VERSION = 1
DEFAULT_MODE = "sentence"
SUPPORTED_MODES = ("sentence", "paragraph")

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LIST_RE = re.compile(
    r"^\s*(?:[-*+•◦▪·‣]\s+|\(?\d+[.)]\s+|[A-Za-z][.)]\s+|"
    r"[一二三四五六七八九十百千万]+[、.)]\s*)"
)
_HEADING_RE = re.compile(
    r"^\s*(?:chapter|part|section|appendix|volume|book|contents|"
    r"figure|table|preface|introduction|conclusion)\b",
    re.IGNORECASE,
)
_URL_EMAIL_RE = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+\.\S+", re.IGNORECASE)
_ABBREVIATIONS = {
    "asap", "approx", "dept", "dr", "e.g", "etc", "fig", "i.e", "inc", "jan",
    "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "jr", "misc", "mr", "mrs", "ms", "prof", "sr", "st",
    "vs", "no",
}
_NON_TERMINAL_ABBREVIATIONS = {
    "approx", "dept", "dr", "e.g", "fig", "i.e", "inc", "jan", "feb", "mar",
    "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "jr",
    "misc", "mr", "mrs", "ms", "prof", "sr", "st", "vs", "no",
}
_CLOSING_CHARS = set('"\'”’»)]}】》」』〉〕）］〗〙〛')
# Arabic ``؟`` and Urdu ``۔`` are sentence terminals; the Arabic comma ``،``
# and semicolon ``؛`` are not (they never split a sentence).
_SENTENCE_CHARS = set(".!?。！？؟۔")
# 坏掉的 PDF 文本层会在句中留下孤立句点（``…finds herself in the. Midnight
# Library…``），把一句话切成 ``…in the.`` + ``Midnight Library…``。冠词在英文里
# **不可能**结束句子，所以只拒绝这三个词。
#
# 刻意不扩到 of / to / in / by / from / that / with / is / was 一类：英文介词
# 悬空和省略极常见，实测在这本书的 6897 个真实单元里，``{the,a,an,of}`` 命中 3 处
# 全部是合法句（``This is the life you dreamed of.``），扩到介词/系动词后命中
# 30–95 处且绝大多数是合法句（``Apart from that.`` / ``Time slipped by.``）。
# 白名单越长越会误杀，所以这里只留最窄的一种。
_NON_TERMINAL_ARTICLES = {"the", "a", "an"}
_OPENING_PAIRS = {"‘": "’", "“": "”", "«": "»", "「": "」", "『": "』"}


def _find_close_quote(text: str, start: int, open_char: str, close_char: str,
                      max_distance: int = 250) -> int:
    sub = text[start:start + max_distance]
    next_open = sub.find(open_char)
    search_limit = next_open if next_open != -1 else len(sub)
    if close_char in ("’", "'"):
        m = re.search(r"[’'](?![A-Za-z])", sub[:search_limit])
        return start + m.start() if m else -1
    pos = sub[:search_limit].find(close_char)
    return start + pos if pos != -1 else -1



def resolve_mode(value: Any = None, fallback: Any = None,
                 environment: Any = None, default: Any = DEFAULT_MODE) -> str:
    """Resolve a user/configured mode without allowing an unknown value."""
    for candidate in (value, fallback, environment, default, DEFAULT_MODE):
        mode = str(candidate or "").strip().lower()
        if mode in SUPPORTED_MODES:
            return mode
    return DEFAULT_MODE


def _has_cjk(value: str) -> bool:
    return bool(_CJK_RE.search(value or ""))


def _starts_sentence(value: str) -> bool:
    """Whether ``value`` plausibly starts a new sentence.

    Case is English-specific: an uppercase letter, a CJK character or a digit
    all start a sentence.  Arabic, Hebrew and other uncased scripts have no
    uppercase form, so requiring ``isupper()`` silently suppressed every
    sentence boundary after a period in an RTL document.  Any uncased letter is
    treated as a valid sentence start, while a lowercase Latin continuation is
    still rejected (preserving the English behaviour).
    """
    if not value:
        return False
    if value.isupper() or value.isdigit() or _has_cjk(value):
        return True
    if value.isalpha():
        # Arabic / Hebrew / CJK etc. are neither upper nor lower cased.
        return not value.islower()
    return value in '"\'“‘（([{'


def _normalise_spaces(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _preserved_list_or_table_parts(raw: str, text: str) -> List[str] | None:
    lines = [_normalise_spaces(line) for line in str(raw or "").splitlines()]
    lines = [line for line in lines if line]
    if len(lines) > 1 and all(_LIST_RE.match(line) for line in lines):
        # List items are already structural units.  Keep each item together,
        # but do not make two neighbouring bullets one CAT segment.
        return lines
    if _LIST_RE.match(text):
        return [text]
    # A tab or multiple pipe separators are strong evidence that this is a
    # table row.  Keep it together; splitting a row would destroy columns.
    if "\t" in str(raw or "") or text.count("|") >= 2:
        return [text]
    return None


def _looks_like_heading(text: str) -> bool:
    if not text or len(text) > 180:
        return False
    if _HEADING_RE.match(text):
        return True
    if text.isupper() and len(text.split()) <= 18:
        return True
    # Short title-case lines without a terminal mark are usually headings.  Do
    # not classify a normal long prose paragraph this way.
    words = re.findall(r"[A-Za-z][A-Za-z'’-]*", text)
    if (words and len(words) <= 12 and not re.search(r"[.!?。！？؟]$", text)
            and sum(1 for word in words if word[:1].isupper()) >= max(2, len(words) // 2)):
        return True
    return False


def _token_before(text: str, index: int) -> str:
    """Return the word/dotted abbreviation ending immediately before ``index``."""
    prefix = text[:index]
    match = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)$", prefix)
    return match.group(1) if match else ""


def _dot_is_boundary(text: str, index: int) -> bool:
    """Decide whether a period is sentence punctuation rather than an inline dot."""
    if index + 1 < len(text) and text[index + 1] == ".":
        return False
    before = text[index - 1] if index else ""
    next_index = index + 1
    while next_index < len(text) and text[next_index] in _CLOSING_CHARS:
        next_index += 1
    after = text[next_index] if next_index < len(text) else ""
    if before.isdigit() and after.isdigit():
        return False
    # Dotted domains, e-mail addresses, and paths are never boundaries at an
    # inline dot.  A final period after a URL is still allowed below.
    line_start = max(text.rfind(" ", 0, index), text.rfind("\n", 0, index)) + 1
    token = text[line_start:index + 1]
    if ("@" in token or "://" in token or token.lower().startswith("www.")) \
            and after and not after.isspace():
        return False
    word = _token_before(text, index).casefold().rstrip(".")
    if word in _ABBREVIATIONS:
        if word in _NON_TERMINAL_ABBREVIATIONS:
            return False
        # Abbreviations such as ``etc.`` can also end a sentence.  Treat them
        # as a boundary only when the following token looks like a sentence
        # start; lowercase continuations remain atomic.
        while next_index < len(text) and text[next_index].isspace():
            next_index += 1
        if next_index >= len(text):
            return True
        next_char = text[next_index]
        return _starts_sentence(next_char)
    if len(word) == 1 and word.isalpha():
        return False
    # 冠词守卫（见 `_NON_TERMINAL_ARTICLES`）：只在句点落在 the/a/an 之后时生效，
    # 用于挡住文本层错字造出的假句末。前面所有规则（缩写、数字、URL、首字母缩写）
    # 都先于它判断，因此不会改变任何既有行为。
    if word in _NON_TERMINAL_ARTICLES:
        return False
    # Initials such as U.S.A. are covered even when the complete dotted token
    # is longer than the small abbreviation list.
    if re.search(r"(?:[a-z]\.){2,}[a-z]?\s*$",
                 text[max(0, index - 12):index + 1].casefold()):
        while next_index < len(text) and text[next_index].isspace():
            next_index += 1
        if next_index >= len(text):
            return True
        next_char = text[next_index]
        return _starts_sentence(next_char)
    if after and not after.isspace():
        # A period embedded in a word or URL cannot finish the sentence.
        return False
    if not after:
        return True
    while next_index < len(text) and text[next_index].isspace():
        next_index += 1
    if next_index >= len(text):
        return True
    next_char = text[next_index]
    # English sentence starts conventionally use an uppercase letter.  Uncased
    # scripts (Arabic / Hebrew) and CJK have no such distinction, so any letter
    # is a valid sentence start there; see ``_starts_sentence``.
    return _starts_sentence(next_char)


def _boundary_end(text: str, index: int) -> int:
    """Consume repeated terminal punctuation, closing quotes and whitespace."""
    end = index + 1
    while end < len(text) and text[end] in _SENTENCE_CHARS:
        end += 1
    while end < len(text) and text[end] in _CLOSING_CHARS:
        end += 1
    return end


def _lowercase_attribution_follows(text: str, end: int) -> bool:
    """Return whether a quoted terminal is followed by dialogue attribution.

    In prose, ``“Are we ready?” asks Sara.`` is one sentence.  Treating the
    question mark as a hard boundary would produce a stray ``asks Sara`` CAT
    unit.  An uppercase continuation still starts a new sentence, preserving
    the usual ``He said “Hello.” Next sentence.`` behaviour.
    """
    index = end
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return False
    return text[index].islower()


def _coverage_key(value: str) -> str:
    """Normalize only layout whitespace for lossless segmentation checks."""
    return re.sub(r"\s+", "", str(value or ""))


def _gate_sentence_parts(raw: str, parts: List[str]) -> Tuple[List[str], Dict[str, Any]]:
    """Admit only lossless, structurally safe sentence splits.

    The gate is deliberately deterministic and conservative.  It does not
    guess at a translation or silently discard punctuation: a split is allowed
    only when joining the emitted units reproduces the input text modulo
    layout whitespace.  This is the last line of defence for decimal points,
    abbreviations, URLs and malformed text layers that reach the splitter.
    """
    text = _normalise_spaces(raw)
    candidates = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if len(candidates) <= 1:
        return candidates or ([text] if text else []), {
            "status": "passed", "reason": "no_boundary_or_atomic_unit",
        }
    if _coverage_key("".join(candidates)) != _coverage_key(text):
        return [text], {
            "status": "blocked", "reason": "lossless_coverage_failed",
        }
    # A sentence fragment beginning with a lowercase word indicates a missed
    # attribution boundary or an otherwise ambiguous text-layer break.  Keep
    # the paragraph atomic until a human can decide where it belongs.
    for part in candidates[1:]:
        first = re.match(r"[A-Za-z]", part)
        if first and first.group(0).islower():
            return [text], {
                "status": "blocked", "reason": "lowercase_fragment",
            }
    return candidates, {"status": "passed", "reason": "lossless_sentence_split"}


def _split_sentences(raw: str) -> List[str]:
    text = _normalise_spaces(raw)
    if not text:
        return []
    preserved = _preserved_list_or_table_parts(raw, text)
    if preserved is not None or _looks_like_heading(text):
        return preserved or [text]
    # A standalone URL/e-mail is an atomic unit even if it contains dots.
    if _URL_EMAIL_RE.fullmatch(text):
        return [text]

    result: List[str] = []
    start = 0
    index = 0
    active_quote_close = -1

    while index < len(text):
        char = text[index]
        if char in _OPENING_PAIRS and index >= active_quote_close:
            matching_close = _OPENING_PAIRS[char]
            close_pos = _find_close_quote(text, index + 1, char, matching_close)
            if close_pos != -1:
                active_quote_close = close_pos
        elif char == '"' and index >= active_quote_close:
            close_pos = text.find('"', index + 1)
            if close_pos != -1 and (close_pos - index) <= 250:
                active_quote_close = close_pos

        boundary = char in "!?。！？؟۔" or (char == "." and _dot_is_boundary(text, index))
        if boundary:
            end = _boundary_end(text, index)
            # If we are inside an active quotation that closes LATER than this boundary,
            # do not split inside the quotation!
            if active_quote_close != -1 and end <= active_quote_close:
                index = end
                continue
            # A terminal quote followed by a lowercase attribution belongs to
            # the same sentence (``“...?” comes ...``).
            if active_quote_close != -1 and end > active_quote_close \
                    and _lowercase_attribution_follows(text, end):
                index = end
                continue

            candidate = text[start:end].strip()
            if candidate:
                result.append(candidate)
            start = end
            index = end
            active_quote_close = -1
            continue
        index += 1
    tail = text[start:].strip()
    if tail:
        result.append(tail)
    return result or [text]


def segment_paragraphs(
    paragraphs: Sequence[Any], *, mode: Any = DEFAULT_MODE,
    source_lang: Any = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Build ordered CAT units from cleaned source paragraphs.

    The returned metadata is intentionally compact: paragraph ranges let the
    UI/audit map each sentence back to its recovered paragraph without storing
    a second copy of every segment in ``state.json``.
    """
    resolved = resolve_mode(mode)
    values = [str(item or "").strip() for item in paragraphs]
    if resolved == "paragraph":
        segments = values
        ranges = [
            {"paragraph_index": index, "start": index, "end": index + 1}
            for index in range(len(values))
        ]
    else:
        segments = []
        ranges = []
        for paragraph_index, paragraph in enumerate(values):
            start = len(segments)
            raw_parts = _split_sentences(paragraph)
            parts, gate = _gate_sentence_parts(paragraph, raw_parts)
            segments.extend(parts)
            ranges.append({
                "paragraph_index": paragraph_index,
                "start": start,
                "end": len(segments),
                "gate": gate,
            })

    split_paragraphs = sum(
        1 for item in ranges if int(item["end"]) - int(item["start"]) > 1
    )
    metadata: Dict[str, Any] = {
        "rules_version": RULES_VERSION,
        "mode": resolved,
        "source_lang": str(source_lang or "").strip(),
        "input_paragraph_count": len(values),
        "output_segment_count": len(segments),
        "split_paragraph_count": split_paragraphs,
        "preserved_paragraph_count": len(values) - split_paragraphs,
        "paragraph_ranges": ranges,
    }
    blocked = [
        {
            "paragraph_index": item["paragraph_index"],
            "reason": (item.get("gate") or {}).get("reason", "unknown"),
        }
        for item in ranges
        if (item.get("gate") or {}).get("status") == "blocked"
    ]
    metadata["gate"] = {
        "version": MIGRATION_VERSION,
        "status": "blocked" if blocked else "passed",
        "checked_paragraph_count": len(values),
        "blocked_paragraphs": blocked,
    }
    return segments, metadata


def _split_text_field(value: Any, expected: int) -> List[str] | None:
    """Split a translated text field only when its sentence count is exact."""
    text = str(value or "")
    if not text.strip():
        return [""] * expected
    parts, gate = _gate_sentence_parts(text, _split_sentences(text))
    if gate.get("status") != "passed" or len(parts) != expected:
        return None
    return parts


def migrate_legacy_state(state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Upgrade an explicitly paragraph-segmented task to sentence units.

    Existing translated paragraphs are changed only when source and target
    sentence counts match exactly.  Ambiguous paragraphs stay intact and are
    recorded in the migration gate, so a user can resolve them manually.
    The function is pure with respect to storage and is idempotent after the
    migration marker is written into the returned state.
    """
    if not isinstance(state, dict):
        return state, {"status": "skipped", "reason": "invalid_state"}
    segmentation = state.get("segmentation")
    if not isinstance(segmentation, dict):
        return state, {"status": "skipped", "reason": "no_segmentation_metadata"}
    # Tasks created before the segmentation metadata was introduced have a
    # completed source stage plus `source_paragraphs`, but no recorded mode.
    # They are the other legacy paragraph contract and should receive the same
    # gated upgrade.  A newly started task always writes `locked` before source
    # extraction, so an explicit paragraph choice cannot be mistaken for this
    # compatibility case.
    if (not segmentation.get("mode") and state.get("p1_done")
            and (state.get("source_paragraphs") or state.get("source_paras"))
            and not segmentation.get("locked")):
        segmentation["mode"] = "paragraph"
    if segmentation.get("mode") != "paragraph" or segmentation.get("locked"):
        return state, {"status": "skipped", "reason": "not_legacy_paragraph_mode"}
    if (segmentation.get("migration") or {}).get("version") == MIGRATION_VERSION:
        return state, dict(segmentation.get("migration") or {})

    source_paragraphs = [str(item or "").strip() for item in (
        state.get("source_paragraphs") or state.get("source_paras") or state.get("paras") or [])]
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    if not source_paragraphs:
        segmentation["migration"] = {
            "version": MIGRATION_VERSION, "status": "skipped",
            "reason": "no_source_paragraphs",
        }
        segmentation["mode"] = DEFAULT_MODE
        segmentation["locked"] = True
        state["segmentation"] = segmentation
        return state, segmentation["migration"]
    if pairs and (len(pairs) != len(paras)
                  or len(source_paragraphs) != len(pairs)):
        report = {
            "version": MIGRATION_VERSION, "status": "blocked",
            "reason": "pair_paragraph_count_mismatch",
            "blocked_paragraphs": [],
        }
        segmentation["mode"] = DEFAULT_MODE
        segmentation["locked"] = True
        segmentation["migration"] = report
        state["segmentation"] = segmentation
        return state, report

    new_pairs: List[Dict[str, Any]] = []
    new_paras: List[str] = []
    new_ranges: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    split_count = 0
    for paragraph_index, source in enumerate(source_paragraphs):
        range_start = len(new_paras)
        pair = pairs[paragraph_index] if pairs and paragraph_index < len(pairs) else None
        if pair is not None and not isinstance(pair, dict):
            pair = None
        pair_source_mismatch = bool(
            pair is not None and str(pair.get("source") or "").strip() != source)
        if pair_source_mismatch:
            # A human may already have edited the source cell.  Never replace
            # that text with the old cleaned paragraph during migration.
            source = str(pair.get("source") or "").strip()
        source_parts, source_gate = _gate_sentence_parts(source, _split_sentences(source))
        target_parts = None
        if pair is not None and str(pair.get("target") or "").strip():
            target_parts = _split_text_field(pair.get("target"), len(source_parts))
        if pair_source_mismatch or source_gate.get("status") != "passed" or (
                pair is not None and str(pair.get("target") or "").strip()
                and target_parts is None):
            blocked.append({
                "paragraph_index": paragraph_index,
                "reason": "source_pair_mismatch" if pair_source_mismatch else
                source_gate.get("reason") if source_gate.get("status") != "passed"
                else "target_sentence_count_mismatch",
            })
            source_parts = [source]
            target_parts = [str(pair.get("target") or "")] if pair is not None else None
        else:
            if len(source_parts) > 1:
                split_count += 1

        for offset, source_part in enumerate(source_parts):
            new_paras.append(source_part)
            if pair is None:
                continue
            child = dict(pair)
            child["source"] = source_part
            if target_parts is not None:
                child["target"] = target_parts[offset]
            if offset > 0:
                uid = str(pair.get("segment_uid") or "")
                if uid:
                    child["segment_uid"] = f"{uid}-s{offset:03d}"
            # The old review evidence was paragraph-scoped.  Preserve the
            # translation text but require review again for every new unit.
            if len(source_parts) > 1:
                for key in ("reviewed", "review_status", "reviewed_at",
                            "human_accepted", "accepted_target"):
                    child.pop(key, None)
                child["target_provenance"] = "legacy_sentence_migration"
            new_pairs.append(child)
        new_ranges.append({
            "paragraph_index": paragraph_index,
            "start": range_start,
            "end": len(new_paras),
            "gate": {
                "status": "blocked" if any(
                    item.get("paragraph_index") == paragraph_index for item in blocked
                ) else "passed",
                "reason": next((item.get("reason") for item in blocked
                                if item.get("paragraph_index") == paragraph_index),
                               "lossless_sentence_split"),
            },
        })

    state["paras"] = new_paras
    if pairs:
        state["pairs"] = new_pairs
    segmentation["mode"] = DEFAULT_MODE
    segmentation["locked"] = True
    migration = {
        "version": MIGRATION_VERSION,
        "status": "partial" if blocked else "completed",
        "from_mode": "paragraph",
        "split_paragraph_count": split_count,
        "blocked_paragraphs": blocked,
        "gate": {
            "status": "blocked" if blocked else "passed",
            "blocked_count": len(blocked),
        },
    }
    segmentation["migration"] = migration
    segmentation["output_segment_count"] = len(new_paras)
    segmentation["split_paragraph_count"] = split_count
    segmentation["preserved_paragraph_count"] = len(source_paragraphs) - split_count
    segmentation["paragraph_ranges"] = new_ranges
    segmentation["gate"] = {
        "version": MIGRATION_VERSION,
        "status": "blocked" if blocked else "passed",
        "checked_paragraph_count": len(source_paragraphs),
        "blocked_paragraphs": blocked,
    }
    state["segmentation"] = segmentation
    return state, migration


__all__ = ["DEFAULT_MODE", "MIGRATION_VERSION", "RULES_VERSION", "SUPPORTED_MODES",
           "migrate_legacy_state", "resolve_mode", "segment_paragraphs"]
