"""术语修订之后的逐段预览与受控全局替换（issue #124/#126）。

改一条术语的首选译名，和把既有译文改过来，是两个明确的操作：本模块只回答
"哪些段落受影响、每段改成什么样、哪些段落绝不碰"，不写任何状态——写入走
#92 候选事务（``core.stage_term_revision`` 暂存，批量采用提交）。

与概念批量修订（#97，``folith/concept_revision.py``）同一套护栏：

- **原文锚定**：只有原文里出现该术语的段落才进预览——没有整书正则替换入口；
  原文命中数（56 处）和可安全替换数（14 处）是两个不同的数字；
- **逐段分类**，而不是把所有命中都当可替换：
  - ``rows``：译文里出现旧首选译名或已知错误译法（禁用译名），可确定性地
    before/after；
  - ``already_correct``：新译名已经在译文里，无需修改；
  - ``ambiguous``：原文命中但译文里新旧译名都没有——需要人工判断，绝不自动改；
  - ``protected``：``locked_protection``（人工改写/人工确认）段只列出，绝不进批量；
- **排除**：调用方（UI 逐段勾选）排除的段标记 ``excluded``，暂存会跳过它们。

预览是纯函数，零模型调用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import consistency
from . import terminology as _terminology
from . import translation_candidates as _cand

__all__ = ["preview_term_revision", "REPLACEABLE_ROW", "AMBIGUOUS",
           "ALREADY_CORRECT"]

#: 每个原文命中段落的分类标签（UI 与测试都直接断言这些值）。
REPLACEABLE_ROW = "replaceable"
AMBIGUOUS = "ambiguous"
ALREADY_CORRECT = "already_correct"


def _spelling(value: Any) -> str:
    return consistency.spelling(value)


def preview_term_revision(
    source_term: str,
    old_target: str,
    new_target: str,
    pairs: Sequence[Mapping[str, Any]],
    *,
    forbidden: Sequence[str] = (),
    exclusions: Sequence[int] = (),
) -> Dict[str, Any]:
    """一次术语修订的受影响段落预览（纯函数，不写任何状态）。

    ``old_target`` 是修订前的首选译名；``forbidden`` 是该术语的禁用译名——
    它们与旧首选译名一样是"已知错误的写法"，命中时给出确定性替换提议
    （每行记录实际命中的写法，UI 逐段勾选后才写入）。

    返回 ``{"source", "old_target", "new_target", "rows", "protected",
    "ambiguous", "already_correct", "source_occurrences", "reason"}``；
    ``reason`` 非空表示没有可执行的修订（如新译名为空）。
    """
    source_term = _spelling(source_term)
    new_target = _spelling(new_target)
    old_target = _spelling(old_target)
    forbidden_forms = [form for form in (_spelling(item) for item in forbidden or [])
                       if form]
    result: Dict[str, Any] = {
        "source": source_term, "old_target": old_target, "new_target": new_target,
        "rows": [], "protected": [], "ambiguous": [], "already_correct": [],
        "source_occurrences": 0, "reason": ""}
    if not source_term:
        result["reason"] = "no_source"
        return result
    if not new_target:
        result["reason"] = "no_new_target"
        return result

    replace_forms = ([old_target] if old_target else []) + forbidden_forms
    replace_pattern = consistency.variant_pattern(replace_forms)
    correct_pattern = consistency.variant_pattern([new_target])
    excluded = set(exclusions or ())

    rows: List[Dict[str, Any]] = []
    for index, pair in enumerate(pairs or []):
        if not isinstance(pair, Mapping) or pair.get("excluded"):
            continue
        source = str(pair.get("source") or "")
        # 无上下文替换的硬护栏：原文不含该术语的段一律不进批量。
        if not _terminology.term_matches(source_term, source):
            continue
        result["source_occurrences"] += 1
        protection = _cand.locked_protection(pair)
        if protection:
            result["protected"].append({"segment_index": index,
                                        "protection": list(protection)})
            continue
        target = str(pair.get("target") or "")
        matched = list(dict.fromkeys(match.group() for match in
                                     (replace_pattern.finditer(target)
                                      if replace_pattern else ())))
        if matched:
            after = (replace_pattern.sub(lambda _match: new_target, target)
                     if replace_pattern else target)
            if after != target:
                rows.append({
                    "segment_index": index,
                    "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
                    "source": source,
                    "before": target,
                    "after": after,
                    "variants": sorted(set(matched)),
                    "excluded": index in excluded,
                })
                continue
        if correct_pattern is not None and correct_pattern.search(target):
            result["already_correct"].append(index)
            continue
        if not target.strip():
            # 还没有译文：谈不上"译错了"，交给正常翻译流程。
            continue
        result["ambiguous"].append({
            "segment_index": index,
            "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
            "source": source,
            "before": target,
        })
    rows.sort(key=lambda row: row["segment_index"])
    result["rows"] = rows
    result["protected"].sort(key=lambda item: item["segment_index"])
    result["ambiguous"].sort(key=lambda item: item["segment_index"])
    result["already_correct"].sort()
    return result
