"""术语条目驱动的逐段预览与受控批量修订（issue #126）。

概念修订（#97）按"概念决策"找受影响段落；本模块按**术语表条目**找：
一条术语的首选译名改了（或新增了禁用译名），哪些段落要跟着改。

与目标串批量替换（``adapter.replace_in_targets``）的本质区别：

- **锚在原文上**：只有原文里出现该术语的段落才进预览——原文不含术语的段
  即使译文碰巧长一样也不会被改；
- **分类而不是假设可替换**：每段先判定 replaceable / already_correct /
  ambiguous，"原文出现次数"和"可替换译文名数"是两个不同的数字；
- **人工保护**：``locked_protection`` 命中的段（人工确认/人工改写）只列入
  ``protected``，绝不进批量——覆盖人工工作没有批量入口；
- **写入走 #92 候选事务**：暂存（``core.stage_term_revision``）在一个事务里
  创建候选、基线取自当前状态，不改正文；采用复用既有批量采用入口。

预览是纯函数，零模型调用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import consistency
from . import terminology as _terminology
from . import translation_candidates as _cand

__all__ = ["preview_term_revision"]

REPLACEABLE = "replaceable"
ALREADY_CORRECT = "already_correct"
AMBIGUOUS = "ambiguous"
PROTECTED = "protected"


def _spelling(value: Any) -> str:
    return consistency.spelling(value)


def _entry_field(entry: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = entry.get(name)
        if value:
            return _spelling(value)
    return ""


def preview_term_revision(
    entry: Mapping[str, Any],
    pairs: Sequence[Mapping[str, Any]],
    *,
    exclusions: Sequence[int] = (),
) -> Dict[str, Any]:
    """一次术语修订的受影响段落预览（纯函数，不写任何状态）。

    ``entry`` 是术语条目（或它的投影），需要：

    - ``source``：原文术语（决定哪些段落参与）；
    - ``new_preferred``（或 ``preferred`` / ``target``）：修订后的首选译名；
    - ``old_preferred``：修订前的首选译名；
    - ``forbidden``：禁用译名列表（含新加的）。

    要被替换掉的写法 = 旧首选译名 + 禁用译名（去掉与新版相同的那条）。
    ``exclusions`` 是调用方（UI 逐段勾选）决定不参与本次批量的 segment 索引：
    这些段仍出现在 ``rows`` 里（带 ``excluded: true``）供确认，但 stage 会跳过。

    返回 ``{"source", "new_preferred", "old_preferred", "renderings", "rows",
    "protected", "ambiguous", "already_correct", "unchanged", "reason"}``；
    ``reason`` 非空表示没有可执行的修订（没有原文术语 / 没有新版译名）。
    """
    entry = entry if isinstance(entry, Mapping) else {}
    result: Dict[str, Any] = {"source": "", "new_preferred": "", "old_preferred": "",
                              "renderings": [], "rows": [], "protected": [],
                              "ambiguous": [], "already_correct": [],
                              "unchanged": 0, "reason": ""}
    source_term = _spelling(entry.get("source"))
    new_preferred = _entry_field(entry, "new_preferred", "preferred", "target")
    if not source_term or not new_preferred:
        result["reason"] = "no_source_term" if not source_term else "no_new_preferred"
        return result
    old_preferred = _entry_field(entry, "old_preferred")
    forbidden = [_spelling(item) for item in entry.get("forbidden") or [] if _spelling(item)]
    result["source"] = source_term
    result["new_preferred"] = new_preferred
    result["old_preferred"] = old_preferred

    # 要消灭的写法：旧首选 + 全部禁用译名；与新版相同的写法不算（写它已经是对的）。
    renderings = [form for form in dict.fromkeys([old_preferred, *forbidden])
                  if form and form.casefold() != new_preferred.casefold()]
    result["renderings"] = sorted(set(renderings))
    pattern = consistency.variant_pattern(renderings)

    excluded = set(exclusions or ())
    rows: List[Dict[str, Any]] = []
    correct: List[int] = []
    ambiguous: List[Dict[str, Any]] = []
    for index, pair in enumerate(pairs or []):
        if not isinstance(pair, Mapping) or pair.get("excluded"):
            continue
        source = str(pair.get("source") or "")
        # 无上下文替换的硬护栏：原文不含该术语的段一律不进批量。
        if not _terminology.term_matches(source_term, source):
            continue
        protection = _cand.locked_protection(pair)
        if protection:
            result["protected"].append({"segment_index": index,
                                        "protection": list(protection)})
            continue
        target = str(pair.get("target") or "")
        matched = list(dict.fromkeys(match.group() for match in pattern.finditer(target))
                       ) if pattern is not None else []
        if not matched:
            # 原文命中但译文里既没有旧写法也没有新译名：可能是自由发挥、
            # 也可能这段不需要这个词——只能列出来人工看，绝不自动改。
            if not _terminology.term_matches(new_preferred, target):
                ambiguous.append({"segment_index": index, "source": source,
                                  "target": target})
            else:
                correct.append(index)
            continue
        after = pattern.sub(lambda _match: new_preferred, target)
        if after == target:
            correct.append(index)
            continue
        rows.append({
            "segment_index": index,
            "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
            "source": source,
            "before": target,
            "after": after,
            "variants": sorted(set(matched)),
            "excluded": index in excluded,
        })
    rows.sort(key=lambda row: row["segment_index"])
    result["rows"] = rows
    result["protected"].sort(key=lambda item: item["segment_index"])
    result["ambiguous"] = ambiguous
    result["ambiguous"].sort(key=lambda item: item["segment_index"])
    result["already_correct"] = sorted(correct)
    return result
