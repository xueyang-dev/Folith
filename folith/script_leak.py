"""Source-script leftovers in a translation, judged by writing system, not by language pair.

A translation that still contains letters of a script the target language does not
use, while the source had that script, has not been finished: kana left in a
Chinese translation, Cyrillic left in an English one.  Nothing here knows a
language pair or hard-codes a script: two facts are asked of existing sources.

* **Which script is this character?**  The character's own Unicode name says so
  (``HIRAGANA LETTER A`` → kana, ``CJK UNIFIED IDEOGRAPH-6F22`` → han,
  ``ARMENIAN CAPITAL LETTER AYB`` → armenian).  A new language therefore needs no
  range table here; scripts that were never listed before are still recognised.
* **Which scripts does a target language use?**  The language is resolved through
  the repository's single language registry (``folith.ocr_language``) and one row
  of ``_SCRIPTS_BY_KEY`` says what it is written in.  An unknown target language is
  not guessed at and is simply not checked.

False-positive guard rails, in the order they are applied:

1. Latin is allowed in every target: names, brands, URLs and abbreviations are
   not translated, not even into a non-Latin target.
2. A script the source never used cannot be a leftover from the source.
3. Letters inside quotes or brackets are treated as deliberately kept original
   text (a quoted title, a cited name).  A whole quoted run is therefore not
   reported; only letters outside quoting count.  This deliberately accepts a
   false negative in exchange for never flagging kept originals: a wrongly
   flagged segment triggers an automatic re-translation of a segment that was
   already correct.
"""
from __future__ import annotations

import unicodedata
from collections import Counter
from typing import Dict, Iterator, Optional, Tuple

from folith import ocr_language as _languages

#: Latin is allowed in every target language: names, brands, abbreviations and
#: URLs stay as they are, including in Chinese, Japanese or Arabic text.
_ALWAYS_ALLOWED = frozenset({"latin"})

#: Unicode name word → script.  Only the synonyms need a row: ``HIRAGANA`` and
#: ``KATAKANA`` are the same writing system, and every other script word (LATIN,
#: GREEK, ARMENIAN, TAMIL …) is its own script name.
_SCRIPT_WORDS: Dict[str, str] = {
    "HIRAGANA": "kana",
    "KATAKANA": "kana",
    # A style prefix must not hide the script: FULLWIDTH LATIN / CIRCLED LATIN.
    "LATIN": "latin",
}

#: Name words that describe a letter's shape or position, never a script.  A name
#: whose first word is one of these (``MODIFIER LETTER SMALL H``) is left
#: unidentified: no script claim beats a wrong script claim.
_STYLE_WORDS = frozenset({
    "COMBINING", "HALFWIDTH", "FULLWIDTH", "MODIFIER", "CIRCLED", "SQUARED",
    "PARENTHESIZED", "SUPERSCRIPT", "SUBSCRIPT", "SMALL", "CAPITAL", "LETTER",
    "DIGIT", "NUMBER", "SIGN", "SYMBOL", "MATHEMATICAL", "DOUBLE", "STRUCK",
    "BLACK", "WHITE", "REGIONAL", "INDICATOR", "VARIATION", "SELECTOR", "TAG",
    "VERTICAL", "HORIZONTAL", "IDEOGRAPHIC", "MEDIAL", "FINAL", "INITIAL",
    "ISOLATED", "REVERSED", "TURNED", "ROTATED", "ALIEN", "EXTENDED",
})

#: Scripts each language of the repository registry is written in, keyed by the
#: registry key (``folith.ocr_language.OCR_LANGUAGE_REGISTRY``).  A language the
#: registry knows but this table does not is left unchecked (never guessed), and
#: ``tests/script_leak_test.py`` fails when a registry entry has no row here.
_SCRIPTS_BY_KEY: Dict[str, frozenset] = {
    "en": frozenset({"latin"}),
    "de": frozenset({"latin"}),
    "fr": frozenset({"latin"}),
    "es": frozenset({"latin"}),
    "pt": frozenset({"latin"}),
    "it": frozenset({"latin"}),
    "nl": frozenset({"latin"}),
    "pl": frozenset({"latin"}),
    "tr": frozenset({"latin"}),
    "vi": frozenset({"latin"}),
    "zh-CN": frozenset({"han", "bopomofo"}),
    "zh-TW": frozenset({"han", "bopomofo"}),
    "ja": frozenset({"han", "kana"}),
    "ko": frozenset({"hangul", "han"}),
    "ru": frozenset({"cyrillic"}),
    "uk": frozenset({"cyrillic"}),
    "he": frozenset({"hebrew"}),
    "ar": frozenset({"arabic"}),
    "fa": frozenset({"arabic"}),
    "ur": frozenset({"arabic"}),
    "ug": frozenset({"arabic"}),
    "hi": frozenset({"devanagari"}),
    "th": frozenset({"thai"}),
}

#: Quoted / bracketed spans hold deliberately kept original text.  Every pair is
#: unambiguous, so a span always closes where the writer closed it; a quoted run
#: never swallows the rest of the sentence.  ``"`` is handled separately (it is
#: symmetric) and only when the text has an even number of them; ``'`` is never a
#: quote here because it is an apostrophe in ordinary words ("don't").
_QUOTE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("「", "」"), ("『", "』"), ("«", "»"), ("“", "”"), ("‘", "’"),
    ("《", "》"), ("〈", "〉"), ("【", "】"), ("(", ")"), ("（", "）"),
    ("[", "]"), ("〔", "〕"),
)
_QUOTE_CLOSES = dict(_QUOTE_PAIRS)


def script_of(character: str) -> Optional[str]:
    """Writing system of one letter, derived from its Unicode name.

    Returns ``None`` for non-letters, unassigned code points and letters whose
    name does not identify a script (a modifier letter, say).  A script this
    repository has no language row for is still reported: the *name* is the
    answer, not a list of known languages.
    """
    text = str(character or "")
    if len(text) != 1 or not text.isalpha():
        return None
    name = unicodedata.name(text, "")
    if not name:
        return None
    words = name.split()
    if not words:
        return None
    if words[0] == "CJK":
        # CJK UNIFIED IDEOGRAPH-… / CJK COMPATIBILITY IDEOGRAPH-… are Han.
        return "han"
    for word in words:
        script = _SCRIPT_WORDS.get(word)
        if script:
            return script
    if words[0] not in _STYLE_WORDS:
        return words[0].casefold()
    return None


def script_counts(text: str) -> Counter:
    """Letters of each known script in ``text``; digits, punctuation and symbols are ignored."""
    counts: Counter = Counter()
    for character in text or "":
        script = script_of(character)
        if script:
            counts[script] += 1
    return counts


def allowed_scripts(target_lang: str) -> Optional[frozenset]:
    """Scripts the target language is written in, or ``None`` when we do not know.

    The language is resolved through ``folith.ocr_language`` (the repository's
    language registry, which already knows labels, aliases and language codes),
    so a new spelling of an existing language needs no change here.  A language
    with no script row, an empty label and an unrecognised label all return
    ``None``: the caller then checks nothing instead of guessing.
    """
    resolved = _languages.try_resolve_ocr_language(str(target_lang or "").strip())
    if resolved is None or not resolved.keys:
        return None
    scripts = set()
    for key in resolved.keys:
        row = _SCRIPTS_BY_KEY.get(key)
        if row is None:
            # A combination such as "Arabic + English" is fine; one unknown part
            # makes the whole answer unknown, because the unchecked part could be
            # the script we would otherwise report.
            return None
        scripts.update(row)
    return frozenset(scripts) | _ALWAYS_ALLOWED


def _unquoted_letters(text: str) -> Iterator[str]:
    """Yield the letters of ``text`` that are outside quoted / bracketed spans."""
    value = str(text or "")
    # A symmetric double quote is only a delimiter when the text closes it.
    paired_double = value.count('"') % 2 == 0
    stack = []
    for character in value:
        if stack:
            if character == stack[-1]:
                stack.pop()
            continue
        if character == '"' and paired_double:
            stack.append('"')
            continue
        closer = _QUOTE_CLOSES.get(character)
        if closer is not None:
            stack.append(closer)
            continue
        yield character


def leaked_scripts(source: str, target: str, target_lang: str) -> dict:
    """``{script: letters left in the target}`` for scripts the source has and the target must not.

    Only letters outside quoted spans count (see the module docstring); Latin is
    always allowed, and a target language whose scripts are unknown is not
    checked at all.
    """
    allowed = allowed_scripts(target_lang)
    if allowed is None:
        return {}
    in_source = script_counts(source)
    if not in_source:
        return {}
    leaked: Counter = Counter()
    for character in _unquoted_letters(target):
        script = script_of(character)
        if script and script not in allowed and in_source.get(script):
            leaked[script] += 1
    return dict(leaked)
