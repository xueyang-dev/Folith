"""重复段传播（Issue #151）。

对与当前段源文相同的其他段应用当前译文，复用候选事务暂存/采纳与保护分类
（replaceable / already-correct / protected / excluded，与 #126 的分类语义一致），
零模型调用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from . import translation_candidates as _cand

__all__ = [
    "preview_source_propagation",
    "stage_source_propagation",
    "apply_source_propagation",
    "REPLACEABLE_ROW",
    "ALREADY_CORRECT",
    "PROTECTED",
    "EXCLUDED",
]

#: 分类标签（与 #126 term_revision 严格一致）
REPLACEABLE_ROW = "replaceable"
ALREADY_CORRECT = "already_correct"
PROTECTED = "protected"
EXCLUDED = "excluded"


def _clean_source(text: Any) -> str:
    return str(text or "").strip()


def preview_source_propagation(
    source_text: str,
    target_text: str,
    pairs: Sequence[Mapping[str, Any]],
    *,
    current_index: int | None = None,
    exclusions: Sequence[int] = (),
) -> Dict[str, Any]:
    """计算当前译文传播到相同源文段落的受影响分类（纯函数，零模型调用）。

    分类规则与 #126 语义严格对齐：
    - rows (replaceable): 原文完全一致、译文不同、非保护、非排除；
    - already_correct: 原文一致、译文已经与 target_text 完全一致；
    - protected: 原文一致，但受人工保护（human_edited / human_accepted / reviewed / locked），
      这些段落只出现在清单里供用户查看，绝不被暂存或覆盖；
    - excluded: 原文一致，但处于排除状态。
    """
    cleaned_source = _clean_source(source_text)
    cleaned_target = str(target_text or "")
    excluded_set = set(exclusions or ())

    result: Dict[str, Any] = {
        "source": cleaned_source,
        "target": cleaned_target,
        "current_index": current_index,
        "rows": [],
        "protected": [],
        "already_correct": [],
        "excluded": [],
        "source_occurrences": 0,
        "reason": "",
    }

    if not cleaned_source:
        result["reason"] = "no_source"
        return result
    if not cleaned_target.strip():
        result["reason"] = "no_target"
        return result

    rows: List[Dict[str, Any]] = []
    for index, pair in enumerate(pairs or []):
        if not isinstance(pair, Mapping):
            continue
        pair_source = _clean_source(pair.get("source"))
        if pair_source != cleaned_source:
            continue

        result["source_occurrences"] += 1

        # 当前正在编辑的发起段本身不作为待替换目标段
        if current_index is not None and index == current_index:
            continue

        if pair.get("excluded") or index in excluded_set:
            result["excluded"].append(index)
            continue

        protection = _cand.locked_protection(pair)
        if protection or pair.get("locked"):
            prot_list = list(protection) if protection else ["locked"]
            result["protected"].append({
                "segment_index": index,
                "protection": prot_list,
            })
            continue

        target = str(pair.get("target") or "")
        if target.strip() == cleaned_target.strip():
            result["already_correct"].append(index)
            continue

        rows.append({
            "segment_index": index,
            "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
            "source": pair.get("source") or "",
            "before": target,
            "after": cleaned_target,
            "excluded": False,
        })

    rows.sort(key=lambda r: r["segment_index"])
    result["rows"] = rows
    result["protected"].sort(key=lambda item: item["segment_index"])
    result["already_correct"].sort()
    result["excluded"].sort()
    return result


def stage_source_propagation(
    job_id: str,
    source_text: str,
    target_text: str,
    *,
    current_index: int | None = None,
    exclusions: Sequence[int] = (),
    indexes: Sequence[int] | None = None,
    load_state_fn=None,
    commit_candidates_fn=None,
) -> Dict[str, Any]:
    """把可替换的相同原文段落暂存为候选（不改正文真值）。"""
    from folith.qt import adapter

    load_state = load_state_fn or adapter.load_state
    commit_candidates = commit_candidates_fn or adapter.core.commit_candidates

    state = load_state(job_id)
    pairs = state.get("pairs") or []
    preview = preview_source_propagation(
        source_text, target_text, pairs,
        current_index=current_index, exclusions=exclusions)

    wanted = set(indexes) if indexes is not None else None
    drafts = []
    for row in preview["rows"]:
        if row.get("excluded"):
            continue
        index = row["segment_index"]
        if wanted is not None and index not in wanted:
            continue
        pair = pairs[index]
        drafts.append({
            "segment_index": index,
            "segment_uid": str(pair.get(_cand.SEGMENT_UID_FIELD) or ""),
            "source": str(pair.get("source") or ""),
            "candidate_target": target_text,
            "origin": "propagation",
            "instruction": f"传播自第 {current_index + 1} 段" if current_index is not None else "重复段传播",
            "baseline": _cand.pair_signature(pair),
            "usage": {"unknown": True},
        })

    if not drafts:
        return {"created": [], "rejected": [], "selected": 0, "preview": preview}

    result = commit_candidates(job_id, drafts, actor="user")
    return {
        "created": list(result.get("created") or []),
        "rejected": list(result.get("rejected") or []),
        "selected": len(drafts),
        "preview": preview,
    }


def apply_source_propagation(
    job_id: str,
    source_text: str,
    target_text: str,
    *,
    current_index: int | None = None,
    exclusions: Sequence[int] = (),
    indexes: Sequence[int] | None = None,
    allow_protected: bool = False,
    load_state_fn=None,
    commit_candidates_fn=None,
    adopt_candidates_fn=None,
) -> Dict[str, Any]:
    """暂存并采用重复段传播候选。"""
    from folith.qt import adapter

    adopt_candidates = adopt_candidates_fn or adapter.core.adopt_translation_candidates
    staged = stage_source_propagation(
        job_id, source_text, target_text,
        current_index=current_index, exclusions=exclusions, indexes=indexes,
        load_state_fn=load_state_fn, commit_candidates_fn=commit_candidates_fn)

    ids = [str(item.get("candidate_id") or "") for item in staged["created"]]
    ids = [cid for cid in ids if cid]
    if not ids:
        return {
            "staged": 0, "changed": 0, "committed": [], "skipped": [],
            "rejected": staged["rejected"], "preview": staged["preview"],
        }

    adopted = adopt_candidates(job_id, ids, allow_protected=allow_protected)
    return {
        "staged": len(ids),
        "changed": int(adopted.get("changed") or 0),
        "committed": list(adopted.get("committed") or []),
        "skipped": list(adopted.get("skipped") or []),
        "rejected": staged["rejected"],
        "preview": staged["preview"],
    }
