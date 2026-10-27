"""Source-script leftovers in a translation, judged by writing system, not by language pair.

A translation that still contains letters of a script the target language does not
use, while the source had that script, has not been finished: kana left in a
Chinese translation, Cyrillic left in an English one.  The check only needs to know
which scripts a target language is written in, so adding a language means adding
one row to ``_TARGET_SCRIPTS``; an unknown target language is simply not checked.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

# (script, ranges); ranges are inclusive code points.  The Japanese middle dot and the
# prolonged-sound mark are left out: Chinese text uses both as ordinary punctuation.
_RANGES = {
    "latin": ((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F), (0x1E00, 0x1EFF)),
    "han": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2A6DF)),
    "kana": ((0x3041, 0x30FA), (0x30FD, 0x30FF), (0xFF66, 0xFF9F)),
    "hangul": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
    "cyrillic": ((0x400, 0x4FF),),
    "greek": ((0x370, 0x3FF),),
    "arabic": ((0x600, 0x6FF), (0x750, 0x77F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)),
    "hebrew": ((0x590, 0x5FF),),
    "devanagari": ((0x900, 0x97F),),
    "thai": ((0xE00, 0xE7F),),
}
_LATIN_LANGUAGES = ("english", "spanish", "español", "french", "français", "german", "deutsch",
                    "portuguese", "português", "italian", "italiano", "dutch", "polish",
                    "turkish", "vietnamese", "indonesian", "swedish", "czech", "romanian",
                    "英语", "英文", "西班牙", "法语", "法文", "德语", "德文", "葡萄牙", "意大利",
                    "荷兰", "波兰", "土耳其", "越南", "印度尼西亚", "瑞典", "捷克", "罗马尼亚")
# keyword (lower-case, matched as a substring of the target-language label) -> allowed scripts.
_TARGET_SCRIPTS = (
    (("日本語", "日语", "日文", "japanese"), frozenset({"han", "kana", "latin"})),
    (("한국", "韩语", "韩文", "朝鲜语", "korean"), frozenset({"hangul", "han", "latin"})),
    (("中文", "汉语", "漢語", "chinese", "mandarin", "cantonese", "粤语"), frozenset({"han", "latin"})),
    (("русск", "俄语", "俄文", "russian", "ukrain", "乌克兰", "bulgarian", "保加利亚", "serbian"),
     frozenset({"cyrillic", "latin"})),
    (("العربية", "阿拉伯", "arabic", "فارسی", "persian", "波斯", "urdu", "乌尔都"),
     frozenset({"arabic", "latin"})),
    (("עברית", "希伯来", "hebrew"), frozenset({"hebrew", "latin"})),
    (("हिन्दी", "印地", "hindi"), frozenset({"devanagari", "latin"})),
    (("ไทย", "泰语", "thai"), frozenset({"thai", "latin"})),
    (("ελληνικά", "希腊", "greek"), frozenset({"greek", "latin"})),
    (_LATIN_LANGUAGES, frozenset({"latin"})),
)


def _script_of(ch: str) -> Optional[str]:
    code = ord(ch)
    for script, ranges in _RANGES.items():
        for low, high in ranges:
            if low <= code <= high:
                return script
    return None


def script_counts(text: str) -> Counter:
    """Letters of each known script in ``text``; digits, punctuation and symbols are ignored."""
    counts: Counter = Counter()
    for ch in text or "":
        if ch.isalpha():
            script = _script_of(ch)
            if script:
                counts[script] += 1
    return counts


def allowed_scripts(target_lang: str) -> Optional[frozenset]:
    """Scripts the target language is written in, or ``None`` when we do not know."""
    label = str(target_lang or "").strip().lower()
    if not label:
        return None
    for keywords, scripts in _TARGET_SCRIPTS:
        if any(word in label for word in keywords):
            return scripts
    return None


def leaked_scripts(source: str, target: str, target_lang: str) -> dict:
    """``{script: letters left in the target}`` for scripts the source has and the target must not.

    Latin is always allowed in a non-Latin target (names, abbreviations, preserved terms).
    """
    allowed = allowed_scripts(target_lang)
    if allowed is None:
        return {}
    in_source = script_counts(source)
    in_target = script_counts(target)
    return {script: count for script, count in in_target.items()
            if script not in allowed and in_source.get(script)}
