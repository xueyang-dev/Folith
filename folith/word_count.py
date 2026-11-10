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




# ---------------------------------------------------------------------------
# incremental whole-document counting (issue #324, A1)
# ---------------------------------------------------------------------------
class DocumentCountIndex:
    """``document_count`` kept current one row at a time.

    Why: :func:`document_count` walks the whole book (116 ms for 10 000 rows on
    the development machine) and the workbench used to call it on every
    committed batch.  A batch changes a handful of rows, so replacing a row's
    contribution is O(changed rows) instead of O(book).

    Authority: this is a *cache* of :func:`document_count`.  In every state
    ``as_document_count()`` equals a full recount of the same rows; the tests
    assert exactly that.  Nothing here writes document state.

    Structural operations that change the source text, the exclusion flag or
    the row count must call :meth:`reset`; :meth:`update` raises ``ValueError``
    instead of silently reporting stale counts.
    """

    def __init__(self, rows: Iterable[Any] | None = None) -> None:
        self._rows: List[Dict[str, Any]] = []
        self._seen: Dict[str, int] = {}
        self._result = DocumentCount()
        if rows is not None:
            self.reset(rows)

    # -- building ---------------------------------------------------------
    def reset(self, rows: Iterable[Any]) -> None:
        self._rows = []
        self._seen = {}
        self._result = DocumentCount()
        for row in rows:
            self.append(row)

    def append(self, row: Any) -> None:
        """Add one row at the end (the pipeline appends committed batches)."""
        contribution = self._contribution(row)
        self._rows.append(contribution)
        result = self._result
        source = _counts(contribution)
        if contribution["excluded"]:
            _bucket_add(result.excluded, source)
            return
        _bucket_add(result.total, source)
        if contribution["has_target"]:
            _bucket_add(result.translated, source)
            result.target_words += contribution["target_words"]
            result.target_chars += contribution["target_chars"]
        else:
            _bucket_add(result.untranslated, source)
        if contribution["reviewed"]:
            _bucket_add(result.reviewed, source)
        if contribution["human_edited"]:
            _bucket_add(result.human_edited, source)
        key = contribution["source_key"]
        if key:
            if key in self._seen:
                _bucket_add(result.repetitions, source)
            self._seen[key] = self._seen.get(key, 0) + 1

    # -- updating ---------------------------------------------------------
    def update(self, position: int, row: Any) -> None:
        """Replace one row whose *source* is unchanged (target/flags may differ).

        Source, repetition and exclusion buckets stay put by construction: the
        source key and the row order are unchanged, and an exclusion change is
        a structural edit that must rebuild the index.
        """
        old = self._rows[position]
        new = self._contribution(row)
        if (new["source_key"] != old["source_key"]
                or new["source_words"] != old["source_words"]
                or new["source_chars"] != old["source_chars"]):
            raise ValueError("source text changed; rebuild the DocumentCountIndex")
        if new["excluded"] != old["excluded"]:
            raise ValueError("exclusion changed; rebuild the DocumentCountIndex")
        result = self._result
        source = _counts(new)
        if old["has_target"]:
            _bucket_sub(result.translated, source)
            result.target_words -= old["target_words"]
            result.target_chars -= old["target_chars"]
        else:
            _bucket_sub(result.untranslated, source)
        if new["has_target"]:
            _bucket_add(result.translated, source)
            result.target_words += new["target_words"]
            result.target_chars += new["target_chars"]
        else:
            _bucket_add(result.untranslated, source)
        if old["reviewed"]:
            _bucket_sub(result.reviewed, source)
        if new["reviewed"]:
            _bucket_add(result.reviewed, source)
        if old["human_edited"]:
            _bucket_sub(result.human_edited, source)
        if new["human_edited"]:
            _bucket_add(result.human_edited, source)
        self._rows[position] = new

    @staticmethod
    def _contribution(row: Any) -> Dict[str, Any]:
        source_text = str(getattr(row, "source", "") or "")
        source = count_text(source_text)
        target_text = str(getattr(row, "target", "") or "")
        has_target = bool(target_text.strip())
        target = count_text(target_text) if has_target else {"words": 0, "chars": 0}
        return {
            "source_key": _key(source_text),
            "source_words": source["words"],
            "source_chars": source["chars"],
            "has_target": has_target,
            "target_words": target["words"],
            "target_chars": target["chars"],
            "reviewed": bool(getattr(row, "reviewed", False)),
            "human_edited": bool(getattr(row, "human_edited", False)),
            "excluded": bool(getattr(row, "excluded", False)),
        }

    def row_count(self) -> int:
        return len(self._rows)

    # -- reading ----------------------------------------------------------
    def as_document_count(self) -> DocumentCount:
        result = self._result
        return DocumentCount(
            total=_copy_bucket(result.total),
            translated=_copy_bucket(result.translated),
            untranslated=_copy_bucket(result.untranslated),
            reviewed=_copy_bucket(result.reviewed),
            human_edited=_copy_bucket(result.human_edited),
            repetitions=_copy_bucket(result.repetitions),
            excluded=_copy_bucket(result.excluded),
            target_words=result.target_words,
            target_chars=result.target_chars,
        )


def _counts(contribution: Dict[str, Any]) -> Dict[str, int]:
    return {"words": contribution["source_words"],
            "chars": contribution["source_chars"]}


def _bucket_add(bucket: Bucket, counts: Dict[str, int]) -> None:
    bucket.segments += 1
    bucket.words += counts["words"]
    bucket.chars += counts["chars"]


def _bucket_sub(bucket: Bucket, counts: Dict[str, int]) -> None:
    bucket.segments -= 1
    bucket.words -= counts["words"]
    bucket.chars -= counts["chars"]


def _copy_bucket(bucket: Bucket) -> Bucket:
    return Bucket(segments=bucket.segments, words=bucket.words, chars=bucket.chars)


__all__: List[str] = ["Bucket", "DocumentCount", "DocumentCountIndex",
                      "count_text", "document_count", "selection_count"]
