"""概念决策之后的逐段预览与受控批量修订（issue #97）。

一次概念决策（#96）只回答"这个概念统一译成什么"；本模块回答"哪些段落
受影响、每段改成什么、哪些段落绝不碰"：

- **逐段 diff**：预览行携带 before/after 与命中的竞争译法，可追溯；
- **排除与例外**：调用方排除的段只标记不暂存；segment/section 例外段
  用例外译名；
- **人工保护**：``locked_protection`` 命中的段（人工确认/人工改写）只
  列入 ``protected``，绝不进批量；
- **禁止无上下文替换**：只有原文里出现该概念 surface form 的段落才进
  预览——不存在整书正则替换的入口；
- **写入走 #92 候选事务**：暂存（``core.stage_concept_revision``）只在
  一个事务里创建候选、不改正文；采用复用既有批量采用入口（基线门禁、
  每项可追溯、重复点击幂等）。

预览是纯函数，零模型调用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import consistency
from . import concept_cards as cc
from . import terminology as _terminology
from . import translation_candidates as _cand

__all__ = ["preview_concept_revision"]


def _spelling(value: Any) -> str:
    return consistency.spelling(value)


def preview_concept_revision(
    card: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    decisions: Any = None,
    *,
    preferred_target: str = "",
    exclusions: Sequence[int] = (),
) -> Dict[str, Any]:
    """一次概念决策的受影响段落预览（纯函数，不写任何状态）。

    ``card`` 来自 :func:`folith.concept_cards.build_concept_cards`；``decisions``
    是 ``state["concept_decisions"]``。``exclusions`` 是调用方（UI 逐段勾选）
    决定不参与本次批量的 segment 索引——这些段仍出现在 ``rows`` 里（带
    ``excluded: true``）供确认，但 ``stage`` 会跳过它们。

    返回 ``{"preferred_target", "rows", "protected", "unchanged", "reason"}``；
    ``reason`` 非空表示没有可执行的修订（keep_variants / 未决策 / 无竞争译法）。
    """
    decisions = cc.normalize_concept_decisions(decisions)
    result: Dict[str, Any] = {"preferred_target": "", "rows": [],
                              "protected": [], "unchanged": 0, "reason": ""}
    if decisions.get("disabled"):
        result["reason"] = "disabled"
        return result
    card = dict(card) if isinstance(card, Mapping) else {}
    hit = cc.decision_for_keys(decisions, card.get("member_keys") or [])
    if hit and hit.get("keep_variants"):
        # 文字游戏 / 多义 / 各自符合语境：概念级"保持原样"，无批量可言。
        result["reason"] = "keep_variants"
        return result
    preferred = _spelling(preferred_target) or _spelling(
        (hit or {}).get("preferred_target")
        or (card.get("decision") or {}).get("preferred_target"))
    if not preferred:
        result["reason"] = "no_decision"
        return result
    result["preferred_target"] = preferred

    variants = [_spelling(option.get("target"))
                for option in card.get("options") or []
                if _spelling(option.get("target")).casefold() != preferred.casefold()]
    pattern = consistency.variant_pattern(variants)
    if pattern is None:
        result["reason"] = "nothing_to_replace"
        return result

    members = [_spelling(form) for form in card.get("members") or [] if _spelling(form)]
    excluded = set(exclusions or ())
    rows: List[Dict[str, Any]] = []
    for index, pair in enumerate(pairs or []):
        if not isinstance(pair, Mapping) or pair.get("excluded"):
            continue
        source = str(pair.get("source") or "")
        # 无上下文替换的硬护栏：原文不含该概念的段一律不进批量。
        if not any(_terminology.term_matches(form, source) for form in members if form):
            continue
        protection = _cand.locked_protection(pair)
        if protection:
            result["protected"].append({"segment_index": index,
                                        "protection": list(protection)})
            continue
        target = str(pair.get("target") or "")
        matched = list(dict.fromkeys(match.group() for match in pattern.finditer(target)))
        if not matched:
            continue
        exception = next((item for item in (hit or {}).get("exceptions") or []
                          if item.get("scope") == f"segment:{index}"), None)
        replacement = _spelling(exception.get("target")) if exception else preferred
        if not replacement:
            continue
        after = pattern.sub(lambda _match: replacement, target)
        if after == target:
            result["unchanged"] += 1
            continue
        rows.append({
            "segment_index": index,
            "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
            "source": source,
            "before": target,
            "after": after,
            "variants": sorted(set(matched)),
            "exception": exception is not None,
            "excluded": index in excluded,
        })
    rows.sort(key=lambda row: row["segment_index"])
    result["rows"] = rows
    result["protected"].sort(key=lambda item: item["segment_index"])
    return result
