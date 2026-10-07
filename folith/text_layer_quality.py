"""PDF text-layer quality gate for the ingestion contract.

Why this exists
---------------
The ingestion path used to accept a text layer whenever PyMuPDF returned *any*
non-empty text (``paragraph_count > 0``).  The 1882 *علم الدين* scan proves that
assumption wrong: its Internet Archive text layer stores Arabic words in a
broken order (``فيرلا يف ةماقالل اهانزكذ`` for ``الريف في للقمامة ذكرناها``-like
text), so 59 mangled paragraphs were treated as the source and the (now fixed)
Arabic OCR chain was never reached.

This module answers a different question from "is there text?": **is the text
layer usable?**  It is a *quality* gate, not language identification: the
declared source language is an input, never an output, and no page or document
is ever re-labelled.

Sampling follows the existing quick-profile contract: first / middle / last
representative pages, not all 390.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Sequence

from .textual import normalize_language

TEXT_LAYER_QUALITY_VERSION = 1

#: score >= ACCEPT and no hard reason -> accepted
ACCEPT_THRESHOLD = 0.70
#: score <= REJECT or any hard reason -> rejected
REJECT_THRESHOLD = 0.45
#: a usable layer needs at least this many characters on a median sampled page
MIN_MEDIAN_CHARS = 40
MIN_TOTAL_CHARS = 20

ARABIC_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFF),
)
PRESENTATION_FORM_RANGES = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))

#: Very common Arabic words.  A correctly extracted layer contains them in this
#: form; a character-reversed layer contains their mirror image instead.
_ARABIC_COMMON_WORDS = {
    "من", "في", "على", "الى", "إلى", "التي", "الذي", "هذا", "هذه", "ما", "لا",
    "ان", "أن", "إن", "كان", "بين", "عن", "مع", "كل", "هو", "هي", "ثم", "قد",
    "ولا", "اذا", "إذا", "عند", "غير", "بعد", "قبل", "حتى", "لكن", "او", "أو",
    "يا", "قال", "عليه", "عليها", "منه", "فيه", "بها", "ذلك", "تلك", "الا",
    "إلا", "هؤلاء", "هناك", "هنا", "ايضا", "أيضا", "به", "له", "لها",
}
_ARABIC_SCRIPT_LANGUAGES = {
    "arabic", "ara", "ar", "العربية", "arab", "modern standard arabic",
    "persian", "farsi", "fas", "fa", "فارسی", "urdu", "urd", "ur", "اردو",
    "pashto", "pus", "ps", "uyghur", "ug", "sindhi", "sd", "kurdish", "ckb",
    "per", "uig", "snd", "阿拉伯语", "现代标准阿拉伯语", "波斯语", "乌尔都语",
    "普什图语", "پښتو", "维吾尔语", "ئۇيغۇرچە",
}
_WORD_STRIP = ".,!?;:\"'()[]{}«»…،؛؟۔\u200f\u200e\u0640"


def is_arabic_script_language(source_language: Any) -> bool:
    """Whether the declared language uses the Arabic script (never a guess)."""
    label = normalize_language(source_language)
    if not label:
        return False
    if label in _ARABIC_SCRIPT_LANGUAGES:
        return True
    parts = label.replace("_", "-").split("-")
    scripts = [part for part in parts[1:] if len(part) == 4 and part.isalpha()]
    if scripts:
        return scripts[0] == "arab"
    return parts[0] in _ARABIC_SCRIPT_LANGUAGES


def _in_ranges(char: str, ranges) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in ranges)


def _is_arabic_letter(char: str) -> bool:
    return _in_ranges(char, ARABIC_RANGES)


def _strip_token(token: str) -> str:
    # NFKC folds Arabic presentation forms (U+FE70..) to their base letters so
    # the order heuristics work on glyph-encoded layers too.
    folded = unicodedata.normalize("NFKC", str(token or ""))
    return folded.strip(_WORD_STRIP).strip()


def _arabic_order_evidence(tokens: Sequence[str]) -> Dict[str, Any]:
    """Detect character- and word-order reversal from the text itself.

    Two independent, dictionary-light signals:

    * **common-word mirroring** — a correctly extracted layer contains ``من`` /
      ``في`` / ``على``; a character-reversed layer contains ``نم`` / ``يف`` /
      ``ىلع`` instead.
    * **definite-article asymmetry** — ``ال`` is overwhelmingly a word *prefix*
      in Arabic.  A layer where ``ال`` is overwhelmingly a *suffix* is reversed.
    """
    forward_hits = reverse_hits = article_start = article_end = 0
    for raw in tokens:
        token = _strip_token(raw)
        if len(token) < 2 or not any(_is_arabic_letter(c) for c in token):
            continue
        if token in _ARABIC_COMMON_WORDS:
            forward_hits += 1
        elif token[::-1] in _ARABIC_COMMON_WORDS:
            reverse_hits += 1
        if len(token) > 3:
            if token.startswith("ال"):
                article_start += 1
            if token.endswith("ال"):
                article_end += 1
    total = forward_hits + reverse_hits
    common_score = (reverse_hits / total) if total else 0.0
    article_total = article_start + article_end
    article_score = (article_end / article_total) if article_total else 0.0
    return {
        "common_words_forward": forward_hits,
        "common_words_reversed": reverse_hits,
        "article_prefix": article_start,
        "article_suffix": article_end,
        "common_word_reversal_score": round(common_score, 4),
        "article_reversal_score": round(article_score, 4),
        "reversal_evidence_count": reverse_hits + article_end,
    }


def _visual_order_evidence(words: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    """Detect text stored in visual (left-to-right) order.

    PyMuPDF returns words in content-stream order.  For a logically ordered RTL
    layer the x-coordinate decreases along a line; a layer whose x-coordinate
    increases monotonically is stored in visual order and would come out
    reversed downstream.
    """
    lines: Dict[Any, List[float]] = {}
    for word in words or []:
        try:
            # PyMuPDF get_text("words") -> (x0, y0, x1, y1, text, block, line, word)
            x0 = float(word[0])
            text = str(word[4] or "")
            block, line = word[5], word[6]
        except (IndexError, TypeError, ValueError):
            continue
        if not any(_is_arabic_letter(c) for c in text):
            continue
        lines.setdefault((block, line), []).append(x0)
    increasing = decreasing = 0
    for xs in lines.values():
        if len(xs) < 4:
            continue
        up = sum(1 for a, b in zip(xs, xs[1:]) if b > a)
        down = sum(1 for a, b in zip(xs, xs[1:]) if b < a)
        if up >= len(xs) - 1:
            increasing += 1
        elif down >= len(xs) - 1:
            decreasing += 1
    total = increasing + decreasing
    return {
        "visual_order_lines": increasing,
        "logical_order_lines": decreasing,
        "visual_order_ratio": round(increasing / total, 4) if total else 0.0,
    }


def assess_text_layer_sample(samples: Sequence[Dict[str, Any]],
                             source_language: Any = None) -> Dict[str, Any]:
    """Judge a text-layer sample. Pure function, no PDF access.

    ``samples`` items: ``{"page_index": int, "text": str, "words": [...]}``.
    Returns ``status`` ∈ {absent, accepted, rejected, uncertain} plus the score,
    reasons and the evidence used to reach it.
    """
    arabic = is_arabic_script_language(source_language)
    sampled = [s for s in (samples or []) if isinstance(s, dict)]
    pages_with_text = sum(1 for s in sampled if str(s.get("text") or "").strip())
    texts = [str(s.get("text") or "") for s in sampled]
    joined = "\n".join(texts)
    chars = len(joined)
    letters = sum(1 for c in joined if c.isalpha())
    arabic_letters = sum(1 for c in joined if _is_arabic_letter(c))
    replacements = joined.count("\ufffd")
    controls = sum(1 for c in joined
                   if (ord(c) < 32 and c not in "\t\n\r") or 0xD800 <= ord(c) <= 0xDFFF)
    presentation = sum(1 for c in joined if _in_ranges(c, PRESENTATION_FORM_RANGES))
    printable = sum(1 for c in joined if c.isprintable() or c in "\t\n\r")

    tokens: List[str] = []
    for s in sampled:
        tokens.extend(str(s.get("text") or "").split())
    order = _arabic_order_evidence(tokens)
    visual: Dict[str, Any] = {"visual_order_lines": 0, "logical_order_lines": 0,
                              "visual_order_ratio": 0.0}
    for s in sampled:
        part = _visual_order_evidence(s.get("words") or [])
        visual["visual_order_lines"] += part["visual_order_lines"]
        visual["logical_order_lines"] += part["logical_order_lines"]
    vtotal = visual["visual_order_lines"] + visual["logical_order_lines"]
    visual["visual_order_ratio"] = round(
        visual["visual_order_lines"] / vtotal, 4) if vtotal else 0.0

    per_page_chars = sorted(len(t.strip()) for t in texts) or [0]
    median_chars = per_page_chars[len(per_page_chars) // 2]

    ratios = {
        "printable_ratio": round(printable / chars, 4) if chars else 0.0,
        "letter_ratio": round(letters / chars, 4) if chars else 0.0,
        "arabic_ratio": round(arabic_letters / letters, 4) if letters else 0.0,
        "replacement_ratio": round(replacements / chars, 6) if chars else 0.0,
        "control_ratio": round(controls / chars, 6) if chars else 0.0,
        "presentation_form_ratio": round(presentation / chars, 6) if chars else 0.0,
    }
    evidence = {
        "version": TEXT_LAYER_QUALITY_VERSION,
        "sampled_pages": [s.get("page_index") for s in sampled],
        "pages_with_text": pages_with_text,
        "chars": chars,
        "median_page_chars": median_chars,
        **ratios, **order, **visual,
    }

    if not sampled or pages_with_text == 0 or chars == 0:
        return {"status": "absent", "score": 0.0,
                "reasons": ["no_text_layer"], "evidence": evidence,
                "source_language_script": "arabic" if arabic else "other"}

    reasons: List[str] = []
    hard: List[str] = []
    score = 1.0
    if replacements:
        hard.append("replacement_characters")
        score -= 0.5
    if controls:
        hard.append("control_characters")
        score -= 0.4
    if ratios["printable_ratio"] < 0.90:
        reasons.append("low_printable_ratio")
        score -= 0.3
    if ratios["letter_ratio"] < 0.35:
        reasons.append("low_text_density")
        score -= 0.25
    if presentation:
        # Presentation forms are a compatibility-encoding smell, not proof of a
        # broken order: many generators emit logical-order U+FE70.. glyph forms.
        # Penalise but do not reject on this alone; the order signals below
        # decide whether the layer is actually unusable.
        reasons.append("arabic_presentation_forms")
        score -= 0.20
    if median_chars < MIN_MEDIAN_CHARS:
        reasons.append("text_layer_sparse")
        score -= 0.35
    if arabic:
        if ratios["arabic_ratio"] < 0.50:
            hard.append("arabic_script_mismatch")
            score -= 0.5
        if (order["common_word_reversal_score"] > 0.5
                and order["reversal_evidence_count"] >= 3):
            hard.append("arabic_letter_order_reversed")
            score -= 0.8
        if visual["visual_order_ratio"] > 0.6 and visual["visual_order_lines"] >= 2:
            hard.append("arabic_word_order_reversed")
            score -= 0.6

    score = max(0.0, min(1.0, score))
    if hard:
        status = "rejected"
    elif score >= ACCEPT_THRESHOLD:
        status = "accepted"
    elif score <= REJECT_THRESHOLD:
        status = "rejected"
    else:
        status = "uncertain"
    return {
        "status": status,
        "score": round(score, 4),
        "reasons": hard + reasons,
        "hard_reasons": hard,
        "evidence": evidence,
        "source_language_script": "arabic" if arabic else "other",
    }


def sample_pdf_text_pages(file_bytes, max_pages: int = 3) -> List[Dict[str, Any]]:
    """First / middle / last representative pages of a PDF text layer."""
    import fitz
    samples: List[Dict[str, Any]] = []
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as document:
            count = document.page_count
            if count <= 0:
                return []
            if max_pages is None or max_pages >= count:
                indices = list(range(count))
            else:
                indices = sorted({0, count // 2, count - 1})[:max_pages]
            for index in indices:
                page = document[index]
                try:
                    words = [list(w) for w in page.get_text("words")]
                except Exception:  # noqa: BLE001 - sampling must never raise
                    words = []
                samples.append({
                    "page_index": index,
                    "text": str(page.get_text("text") or ""),
                    "words": words,
                })
    except Exception:  # noqa: BLE001 - an unreadable PDF has no usable layer
        return []
    return samples


def assess_pdf_text_layer(file_bytes, *, source_language: Any = None,
                          max_pages: int = 3) -> Dict[str, Any]:
    """Sample a PDF text layer and judge whether it is usable."""
    samples = sample_pdf_text_pages(file_bytes, max_pages=max_pages)
    report = assess_text_layer_sample(samples, source_language=source_language)
    report["sampled_pages"] = [s.get("page_index") for s in samples]
    return report


__all__ = [
    "ACCEPT_THRESHOLD", "MIN_MEDIAN_CHARS", "REJECT_THRESHOLD",
    "TEXT_LAYER_QUALITY_VERSION",
    "assess_pdf_text_layer", "assess_text_layer_sample",
    "is_arabic_script_language", "sample_pdf_text_pages",
]
