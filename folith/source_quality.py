"""Deterministic source-admission checks for OCR output.

The PDF cleaner is allowed to repair text, but it must not turn an isolated
OCR fragment into a translation request.  This module is deliberately a
conservative quarantine gate: it never deletes source text and it never tries
to guess a replacement.  It only identifies short, structurally suspicious
items so the translation stage can keep them as source text and require a
human source check.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from .textual import has_textual_content


VERSION = 2

_WORD_RE = re.compile(r"[A-Za-z]+(?:['\u2019\u2013\u2014-][A-Za-z]+)?")
_TERMINAL_RE = re.compile(r"[.!?\u2026:;»\u201d\u2019\"'\-]+$")
_SPACED_DASH_RE = re.compile(
    r"\b[A-Za-z]{1,4}\s+-\s+[A-Za-z]{1,4}\b"
)
_IDENTIFIER_RE = re.compile(
    r"^(?:@\S+|(?:https?://|www\.)\S+|\S+@\S+|[A-Za-z0-9.-]+\.\w{2,})$",
    re.IGNORECASE,
)
_ALPHANUM_FRAGMENT_RE = re.compile(r"^(?:\d+[A-Za-z]+|[A-Za-z]+\d+)[A-Za-z0-9]*$")
_MODEL_DESIGNATION_RE = re.compile(r"^[A-Za-z]{1,4}-\d{1,4}(?:-[A-Za-z0-9]+)?$")
_TITLE_PARTICLES = {
    "a", "an", "the", "and", "but", "or", "for", "nor", "on", "at",
    "to", "from", "by", "of", "in", "with", "as", "is", "it", "its",
    "into", "over", "out", "up", "down", "off", "be", "are", "was",
    "were", "been", "being", "via", "than", "so", "no", "not", "only",
}


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def _has_unmatched_bracket(text: str) -> bool:
    if text.count(")") > text.count("("):
        return True
    if text.count("]") > text.count("["):
        return True
    if text.count("}") > text.count("{"):
        return True
    return False


def _is_title_case(words: Sequence[str]) -> bool:
    if not words or not words[0][:1].isupper():
        return False
    return all(w[:1].isupper() or w.lower() in _TITLE_PARTICLES for w in words)


def _reasons(text: str) -> List[str]:
    value = _normalise(text)
    if not value or not has_textual_content(value):
        return []
    # Handles page numbers/isolated OCR numerals without treating numbers in a
    # normal sentence as suspicious.
    if re.fullmatch(r"\d{1,4}", value):
        return ["孤立数字行，疑似页码或批注"]
    # Social handles, URLs and domains are legitimate short front/back-matter
    # items and should not be sent through this fragment gate.
    if _IDENTIFIER_RE.fullmatch(value):
        return []
    # A year between a name and a label is ordinary heading content, even
    # when a brand uses lowercase (e.g. flynas 2024 Network).
    if re.fullmatch(r"[A-Za-z]{4,}\s+(?:19|20)\d{2}\s+[A-Za-z]{4,}(?:\s+[A-Za-z]{4,}){0,2}", value):
        return []
    # Model designations and equipment/chapter codes (e.g. W-9, U-2, B-52, AK-47)
    # are legitimate headings/titles and not isolated OCR noise.
    if _MODEL_DESIGNATION_RE.fullmatch(value):
        return []
    words = _WORD_RE.findall(value)
    if not words or len(value) > 50:
        return []

    reasons: List[str] = []

    # 1. Alphanumeric noise tokens like 5p, 2a
    if len(words) == 1 and _ALPHANUM_FRAGMENT_RE.fullmatch(value) and len(value) <= 6:
        reasons.append("孤立字母数字杂质")

    # 2. Unmatched closing brackets in short text (e.g. Otte.))
    if _has_unmatched_bracket(value):
        reasons.append("含未配对的闭合括号")

    # 3. Lowercase start in Latin-based text (e.g. eth Se 5th, ie oy a oy ipennaa. aan in., stud with ber)
    if value[:1].islower() and len(words) <= 12:
        if not re.search(r"[.!?。！？]$", value) or len(words) <= 2:
            reasons.append("小写且未闭合的短片段")
        else:
            reasons.append("小写开头的疑似残片或 OCR 杂质")

    # 4. Ultra-short non-sentence token (len <= 3, 1 word) without terminal punctuation (e.g. TS, Mn, na, ve, rae)
    if len(value) <= 3 and len(words) == 1 and not re.search(r"[.!?。！？]$", value):
        reasons.append("孤立超短缩写或字母杂质")

    if (len(words) <= 4 and len(value) <= 25 and not _TERMINAL_RE.search(value)):
        if not _is_title_case(words) and any(
            len(w) <= 3 and w.islower() and w.lower() not in _TITLE_PARTICLES for w in words
        ):
            reasons.append("未闭合且非标题的短片段")

    # 6. Isolated single word in lowercase
    if len(words) == 1 and len(value) <= 12 and value.islower() and "孤立短词" not in reasons:
        reasons.append("孤立短词")

    # 7. Spaced dash: only ASCII hyphen with space, and only when not ending with terminal punctuation
    if len(value) <= 36 and _SPACED_DASH_RE.search(value) and not _TERMINAL_RE.search(value):
        reasons.append("短片段含异常空格连字符")

    return reasons


def is_integrated_numeric_fragment(fragment: str, segment: str) -> bool:
    """A retained number joined to valid text is no longer an isolated row."""
    number, text = _normalise(fragment), _normalise(segment)
    return bool(
        re.fullmatch(r"\d{1,4}", number)
        and any(char.isalpha() for char in text)
        and not _reasons(text)
        and re.search(r"(?<![\w.,])" + re.escape(number) + r"(?!\w|[.,]\d)", text)
    )


_LINE_WRAP_REASONS = {
    "小写且未闭合的短片段",
    "小写开头的疑似残片或 OCR 杂质",
    "与相邻可疑片段形成连续异常",
    "未闭合且非标题的短片段",
}


def is_integrated_line_continuation(fragment: str, preceding: str, segment: str) -> bool:
    """Recognize a short PDF wrap joined back to its preceding line or container.

    Require the actual neighboring words in the resulting segment/container. Single
    tokens, broken brackets and short noise runs retain their source flags.
    """
    tail, previous, text = map(_normalise, (fragment, preceding, segment))
    reasons = _reasons(tail)
    if not reasons:
        return True

    is_wrap = set(reasons) <= _LINE_WRAP_REASONS
    is_caps_wrap = (
        reasons == ["孤立超短缩写或字母杂质"]
        and tail.isupper() and previous.isupper()
    )
    if not (is_wrap or is_caps_wrap):
        return False

    if re.search(r"[.!?。！？]$", previous):
        return False

    prev_reasons = _reasons(previous)
    if prev_reasons and not (set(prev_reasons) <= _LINE_WRAP_REASONS or (prev_reasons == ["孤立超短缩写或字母杂质"] and previous.isupper())):
        return False

    if _reasons(text):
        return False

    tokens = lambda value: re.findall(r"\w+|[^\w\s]", value.replace("–", "-").replace("—", "-"))
    tail_tokens, prev_tokens, actual = tokens(tail), tokens(previous), tokens(text)
    if not tail_tokens:
        return True

    joined = prev_tokens + tail_tokens
    if any(actual[index:index + len(joined)] == joined
           for index in range(len(actual) - len(joined) + 1)):
        return True

    tail_str, prev_str, text_str = " ".join(tail_tokens), " ".join(prev_tokens), " ".join(actual)
    return (prev_str + " " + tail_str) in text_str or (tail_str in text_str and prev_str in text_str)


_HARD_NOISE_REASONS = {
    "孤立数字行，疑似页码或批注",
    "孤立字母数字杂质",
    "含未配对的闭合括号",
    "孤立超短缩写或字母杂质",
    "未闭合且非标题的短片段",
    "孤立短词",
    "短片段含异常空格连字符",
}


def _is_verse_continuation(index: int, values: Sequence[str]) -> bool:
    if index <= 0 or index >= len(values):
        return False
    if not re.search(r"[,;:.!?。！？]$", values[index]):
        return False
    curr_reasons = set(_reasons(values[index]))
    if not curr_reasons or not (curr_reasons <= {"小写且未闭合的短片段", "小写开头的疑似残片或 OCR 杂质"}):
        return False
    prev = values[index - 1]
    if _TERMINAL_RE.search(prev):
        return False
    prev_reasons = set(_reasons(prev))
    if prev_reasons & _HARD_NOISE_REASONS:
        return False
    if prev_reasons and not _is_verse_continuation(index - 1, values):
        return False
    return True


def audit_source_segments(paragraphs: Sequence[str]) -> Dict[str, Any]:
    """Return a JSON-safe source-admission report.

    The report is intentionally independent of a language dictionary.  It is
    used as a safety gate for arbitrary source languages, so a token is never
    rejected merely because it is unfamiliar.  A flagged item remains in the
    source list and must be handled explicitly by the translation stage.
    """
    values = [_normalise(item) for item in (paragraphs or [])]
    candidates: List[Dict[str, Any]] = []
    for index, text in enumerate(values):
        reasons = _reasons(text)
        if reasons:
            if _is_verse_continuation(index, values):
                continue
            candidates.append({
                "segment_index": index,
                "text": text,
                "reasons": reasons,
                "severity": "blocking",
            })

    # Adjacent/nearby suspicious items are stronger evidence of a broken layout
    # or OCR layer than a single short heading.  Keep the fact in the report so
    # the UI/review log can explain why the gate fired.
    # We bridge across non-textual or ultra-short gaps (e.g. isolated symbols ':').
    clusters: List[List[int]] = []
    current: List[int] = []
    for item in candidates:
        index = int(item["segment_index"])
        if current:
            is_adjacent = (index == current[-1] + 1)
            is_near_symbol_gap = (
                index - current[-1] == 2
                and current[-1] + 1 < len(values)
                and (not has_textual_content(values[current[-1] + 1])
                     or len(values[current[-1] + 1]) <= 6)
            )
            if not is_adjacent and not is_near_symbol_gap:
                clusters.append(current)
                current = []
        current.append(index)
    if current:
        clusters.append(current)
    cluster_by_index = {
        index: cluster for cluster in clusters for index in cluster
    }
    for item in candidates:
        cluster = cluster_by_index.get(int(item["segment_index"]), [])
        item["adjacent_flagged_segments"] = len(cluster)
        if len(cluster) >= 2 and "与相邻可疑片段形成连续异常" not in item["reasons"]:
            item["reasons"].append("与相邻可疑片段形成连续异常")

    return {
        "version": VERSION,
        "status": "blocked" if candidates else "passed",
        "checked_count": len(values),
        "flagged_count": len(candidates),
        "flags": candidates,
        "clusters": clusters,
    }


__all__ = ["VERSION", "audit_source_segments"]
