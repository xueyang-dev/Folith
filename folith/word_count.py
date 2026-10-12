"""字数统计（工作台底栏与「字数统计」对话框共用）。

计数规则与常见 CAT 工具一致，界面上也如实写明：

* **词数**：拉丁、西里尔、阿拉伯、希伯来、韩文等按空格分词（数字、带连字符或
  撇号的词算一个）；中文、日文的每个汉字 / 假名各算一个词——这类文字没有词间空格，
  按字计数是行业通行做法。
* **字符数**：不含空白的字符数。

纯函数，不依赖界面；输入是工作台的段落行（有 ``source`` / ``target`` / ``status``
等字段的对象）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence

#: 按字计数的文字：CJK 统一表意文字（含扩展 A、兼容）、平假名、片假名、半角片假名。
_CHAR_COUNTED = re.compile(
    "[㐀-䶿一-鿿豈-﫿぀-ゟ゠-ヿ"
    "ㇰ-ㇿｦ-ﾟ]")
#: 其余文字按"词"：字母或数字连成的串，中间允许撇号、连字符、点（3.14、don't、e-mail）。
_WORD = re.compile(r"[^\W_]+(?:['’\-.][^\W_]+)*")


def count_text(text: Any) -> Dict[str, int]:
    """一段文字的词数与字符数（不含空白）。"""
    value = str(text or "")
    if not value.strip():
        return {"words": 0, "chars": 0}
    char_counted = len(_CHAR_COUNTED.findall(value))
    rest = _CHAR_COUNTED.sub(" ", value)
    words = char_counted + len(_WORD.findall(rest))
    chars = len(re.sub(r"\s+", "", value))
    return {"words": words, "chars": chars}


def _key(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


@dataclass
class Bucket:
    """一类段落的段数 / 原文词数 / 原文字符数。"""

    segments: int = 0
    words: int = 0
    chars: int = 0

    def add(self, counts: Dict[str, int]) -> None:
        self.segments += 1
        self.words += counts["words"]
        self.chars += counts["chars"]

    def as_dict(self) -> Dict[str, int]:
        return {"segments": self.segments, "words": self.words, "chars": self.chars}


@dataclass
class DocumentCount:
    total: Bucket = field(default_factory=Bucket)
    translated: Bucket = field(default_factory=Bucket)
    untranslated: Bucket = field(default_factory=Bucket)
    reviewed: Bucket = field(default_factory=Bucket)
    human_edited: Bucket = field(default_factory=Bucket)
    repetitions: Bucket = field(default_factory=Bucket)
    excluded: Bucket = field(default_factory=Bucket)
    target_words: int = 0
    target_chars: int = 0

    def percent(self, part: Bucket) -> int:
        return round(100 * part.words / self.total.words) if self.total.words else 0

    def as_dict(self) -> Dict[str, Any]:
        data = {name: getattr(self, name).as_dict() for name in (
            "total", "translated", "untranslated", "reviewed", "human_edited",
            "repetitions", "excluded")}
        data.update(target_words=self.target_words, target_chars=self.target_chars)
        return data


def document_count(rows: Iterable[Any]) -> DocumentCount:
    """整份文档的统计。已排除的段单独计，不进总数。

    * 已译：有译文的段；
    * 已审校：审校通过（含人工修改后再审校通过）的段；
    * 人工修改：译者改过的段；
    * 重复：原文与前面某一段完全相同（忽略空白与大小写）的段——第一次出现不算重复。
    """
    result = DocumentCount()
    seen: set[str] = set()
    for row in rows:
        source = str(getattr(row, "source", "") or "")
        target = str(getattr(row, "target", "") or "")
        counts = count_text(source)
        if getattr(row, "excluded", False):
            result.excluded.add(counts)
            continue
        result.total.add(counts)
        if target.strip():
            result.translated.add(counts)
            target_counts = count_text(target)
            result.target_words += target_counts["words"]
            result.target_chars += target_counts["chars"]
        else:
            result.untranslated.add(counts)
        if getattr(row, "reviewed", False):
            result.reviewed.add(counts)
        if getattr(row, "human_edited", False):
            result.human_edited.add(counts)
        key = _key(source)
        if key:
            if key in seen:
                result.repetitions.add(counts)
            seen.add(key)
    return result


def selection_count(rows: Sequence[Any]) -> Dict[str, int]:
    """选中的若干段：段数、原文词数 / 字符数、译文词数 / 字符数。"""
    source_words = source_chars = target_words = target_chars = 0
    for row in rows:
        source = count_text(getattr(row, "source", ""))
        target = count_text(getattr(row, "target", ""))
        source_words += source["words"]
        source_chars += source["chars"]
        target_words += target["words"]
        target_chars += target["chars"]
    return {"segments": len(rows), "source_words": source_words,
            "source_chars": source_chars, "target_words": target_words,
            "target_chars": target_chars}


__all__: List[str] = ["Bucket", "DocumentCount", "count_text", "document_count",
                      "selection_count"]
