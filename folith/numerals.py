"""Digits in a translation use the target language's numerals.

Arabic, Persian and Urdu sources write numbers with Eastern Arabic digits
(٠١٢… / ۰۱۲…).  Models usually convert them, but not reliably: footnote markers
such as ``(١)`` and phone numbers survive into Chinese or English output because
the prompt also says to keep citation markers and identifiers intact.  The value
must stay the same; only the script changes.

The conversion is deliberately narrow: a digit run that sits next to an Arabic
letter belongs to Arabic text the translation quotes on purpose (a title kept
in the original), so it is left alone.  Targets that use these numerals
themselves are never touched.
"""
from __future__ import annotations

import re
import unicodedata

_DIGITS = "٠١٢٣٤٥٦٧٨٩" \
          "۰۱۲۳۴۵۶۷۸۹"
_TO_ASCII = str.maketrans({**{digit: str(index % 10) for index, digit in enumerate(_DIGITS)},
                           "٫": ".",     # ARABIC DECIMAL SEPARATOR
                           "٬": ","})    # ARABIC THOUSANDS SEPARATOR
_RUN = re.compile(f"[{_DIGITS}]+(?:[٫٬][{_DIGITS}]+)*")
_ARABIC_SCRIPT = re.compile(
    "[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

#: Target languages whose own writing uses Eastern Arabic digits (matched loosely
#: against whatever label the task stores: "العربية", "Arabic", "阿拉伯语"…).
_KEEPS_EASTERN_DIGITS = (
    "arab", "阿拉伯", "عرب", "persian", "farsi", "波斯", "فارس",
    "urdu", "乌尔都", "اردو", "pashto", "普什图", "پښتو", "sindhi", "信德",
    "kurd", "库尔德",
)


def keeps_eastern_digits(target_lang: str | None) -> bool:
    label = str(target_lang or "").casefold()
    return any(marker in label for marker in _KEEPS_EASTERN_DIGITS)


def _arabic_letter(char: str) -> bool:
    return bool(_ARABIC_SCRIPT.match(char)) and unicodedata.category(char).startswith("L")


def _neighbour(text: str, index: int, step: int) -> str:
    while 0 <= index < len(text) and text[index].isspace():
        index += step
    return text[index] if 0 <= index < len(text) else ""


def normalize_digits(text: str, target_lang: str | None) -> str:
    """Write Eastern Arabic digits as 0-9 unless the target uses them."""
    if not text or keeps_eastern_digits(target_lang) or not _RUN.search(text):
        return text

    def convert(match: re.Match) -> str:
        before = _neighbour(text, match.start() - 1, -1)
        after = _neighbour(text, match.end(), 1)
        if (before and _arabic_letter(before)) or (after and _arabic_letter(after)):
            return match.group(0)
        return match.group(0).translate(_TO_ASCII)

    return _RUN.sub(convert, text)


def normalize_targets(targets, target_lang: str | None) -> list:
    return [normalize_digits(item, target_lang) if isinstance(item, str) else item
            for item in targets]
