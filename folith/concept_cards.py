"""概念级术语决策卡片：一个概念（一组 surface 变体 + 竞争译法）一张卡。

Issue #96。设计见 docs/issue-96-concept-decision-cards.md：

- **卡片构建是只读建议**：`build_concept_cards` 汇集 #95 聚类与 registry
  observations，永不写状态；唯一写路径是显式的人工决策。
- **决策持久化在 ``state["concept_decisions"]``**：文档级首选译名 +
  segment/section 级例外 + keep_variants（文字游戏/多义/各自符合语境）。
- **继承按 member_keys 交集匹配**，不依赖 card_id 精确命中——概念后来长出
  新变体时老决策仍生效，不会出现第二张矛盾卡。
- **复用既有真相**：译法选项来自 `consistency.current_occurrences`（逐段
  实际用法），写回走 `EntityRegistry.lock`；不建立平行数据。零模型调用。
"""
from __future__ import annotations

import datetime
import hashlib
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import consistency
from . import terminology_variants as tv

__all__ = [
    "normalize_concept_decisions",
    "card_id",
    "build_concept_cards",
    "decision_for_keys",
    "decision_for_form",
    "record_concept_decision",
    "split_concept_card",
]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clean(value: Any, limit: int = 300) -> str:
    return str(value or "").strip()[:limit].rstrip()


def _casefold(value: Any) -> str:
    return tv.casefold_key(value)


# ---------------- 决策结构 ----------------

_EMPTY: Dict[str, Any] = {
    "version": 1,
    "disabled": False,
    "cards": {},
    "manual_splits": [],
    "log": [],
}


def normalize_concept_decisions(raw: Any) -> Dict[str, Any]:
    """归一化概念决策结构；垃圾输入落到安全默认，绝不抛异常。"""
    decisions = {
        "version": 1,
        "disabled": False,
        "cards": {},
        "manual_splits": [],
        "log": [],
    }
    if not isinstance(raw, dict):
        return decisions
    decisions["disabled"] = bool(raw.get("disabled"))
    for pair in raw.get("manual_splits") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        left, right = _casefold(pair[0]), _casefold(pair[1])
        if left and right and left != right:
            decisions["manual_splits"].append(sorted([left, right]))
    decisions["manual_splits"].sort()
    for key, entry in (raw.get("cards") or {}).items():
        if not isinstance(entry, dict):
            continue
        member_keys = sorted({_casefold(form) for form in entry.get("member_keys") or []
                              if _casefold(form)})
        if not member_keys:
            continue
        exceptions = []
        for exception in entry.get("exceptions") or []:
            if not isinstance(exception, dict):
                continue
            scope = _clean(exception.get("scope"), 80)
            target = _clean(exception.get("target"), 300)
            if not scope or not target or \
                    not (scope.startswith("segment:") or scope.startswith("section:")):
                continue
            exceptions.append({"scope": scope, "target": target,
                               "note": _clean(exception.get("note"), 240)})
        decisions["cards"][str(key)] = {
            "member_keys": member_keys,
            "preferred_target": _clean(entry.get("preferred_target")),
            "keep_variants": bool(entry.get("keep_variants")),
            "exceptions": exceptions,
            "decided_at": _clean(entry.get("decided_at"), 40),
            "note": _clean(entry.get("note"), 240),
        }
    for entry in raw.get("log") or []:
        if isinstance(entry, dict):
            decisions["log"].append(dict(entry))
    return decisions


def card_id(member_keys: Sequence[str]) -> str:
    """概念身份：member casefold 键集合的哈希。继承按键交集，不靠它精确命中。"""
    digest = hashlib.sha1()
    for key in sorted({_casefold(key) for key in member_keys if _casefold(key)}):
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
    return "concept-" + digest.hexdigest()[:16]


def decision_for_keys(state: Any, keys: Iterable[str]) -> Optional[Dict[str, Any]]:
    """按探测键与 member_keys 的交集查找已存决策（继承的核心入口）。"""
    decisions = normalize_concept_decisions(state)
    if decisions.get("disabled"):
        return None
    probe = {_casefold(key) for key in keys or [] if _casefold(key)}
    if not probe:
        return None
    best, best_id, best_overlap = None, None, 0
    for entry_id, entry in decisions["cards"].items():
        overlap = len(probe & set(entry["member_keys"]))
        if overlap > best_overlap:
            best, best_id, best_overlap = entry, entry_id, overlap
    return {**best, "card_id": best_id} if best is not None else None


def decision_for_form(state: Any, form: str, *, segment_id: Any = None,
                      section_id: Any = None) -> Optional[Dict[str, Any]]:
    """任一 surface form（含后来出现的新变体）解析到它的概念决策。

    命中 ``segment:<id>`` / ``section:<id>`` 例外时返回例外译名
    （``exception=True``）；否则返回文档级决策。
    """
    hit = decision_for_keys(state, [_casefold(form), tv.punct_key(form)])
    if hit is None:
        return None
    wanted = ""
    if segment_id is not None:
        wanted = f"segment:{segment_id}"
    elif section_id is not None:
        wanted = f"section:{section_id}"
    for exception in hit.get("exceptions") or []:
        if wanted and exception.get("scope") == wanted:
            return {**hit, "preferred_target": exception.get("target") or "",
                    "exception": True}
    return {**hit, "exception": False}


# ---------------- 卡片构建（只读） ----------------

def _record_groups(records: List[Dict[str, Any]],
                   manual_splits: List[List[str]]) -> List[List[Dict[str, Any]]]:
    """把记录按 #95 聚类相连成概念组；无聚类的记录自成一组。"""
    by_id = {str(record.get("id")): record for record in records
             if str(record.get("id"))}
    members = tv.registry_members(SimpleNamespace(records=records))
    clusters = tv.build_candidate_clusters(
        members, {"manual_splits": manual_splits, "disabled": False})
    parent = {key: key for key in by_id}

    def _find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    tiers: Dict[tuple, str] = {}
    _TIER_RANK = {"exact": 0, "punctuation": 1, "morphological": 2}
    for cluster in clusters:
        ids = [str(m["record_id"]) for m in cluster["members"] if m.get("record_id")]
        for index in range(len(ids)):
            for other in ids[index + 1:]:
                _union(ids[index], other)
                tiers[tuple(sorted([ids[index], other]))] = cluster["tier"]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for key, record in by_id.items():
        groups.setdefault(_find(key), []).append(record)
    output = []
    for group in groups.values():
        group_tier = None
        for (a, b), tier in tiers.items():
            if _find(a) == _find(b) and (group_tier is None
                                         or _TIER_RANK[tier] < _TIER_RANK[group_tier]):
                group_tier = tier
        group.sort(key=lambda record: str(record.get("id")))
        output.append((group, group_tier))
    output.sort(key=lambda item: str(item[0][0].get("id")))
    return output


def _options_for_group(group: List[Dict[str, Any]],
                       pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """聚合组内记录的逐段实际译法：每种译法一个选项（频次 = 涉及段数）。"""
    seen_rows: Dict[tuple, Dict[str, Any]] = {}
    for record in group:
        for row in consistency.current_occurrences(record, pairs):
            for variant in row.get("variants") or []:
                name = consistency.spelling(variant)
                if not name:
                    continue
                key = (int(row.get("index", -1)), consistency.spelling(name).casefold())
                seen_rows.setdefault(key, {"index": int(row.get("index", -1)),
                                           "variant": name,
                                           "source": row.get("source") or "",
                                           "target": row.get("target") or ""})
    by_variant: Dict[str, Dict[str, Any]] = {}
    for row in seen_rows.values():
        key = consistency.spelling(row["variant"]).casefold()
        option = by_variant.setdefault(key, {"target": row["variant"], "count": 0,
                                             "segment_indexes": [], "evidence": []})
        if row["index"] not in option["segment_indexes"]:
            option["segment_indexes"].append(row["index"])
        if len(option["evidence"]) < 6:
            option["evidence"].append({
                "segment_index": row["index"],
                "source": row["source"],
                "target": row["target"],
            })
    options = []
    for option in by_variant.values():
        option["count"] = len(option["segment_indexes"])
        option["segment_indexes"] = sorted(option["segment_indexes"])
        options.append(option)
    options.sort(key=lambda o: (-o["count"], o["target"].casefold()))
    return options


def build_concept_cards(records: Any, pairs: Any,
                        decisions: Any = None) -> List[Dict[str, Any]]:
    """构建概念卡（纯只读）：一个概念 = 相连的 surface 变体组 + 各译法选项。

    带已存决策的卡是 resolved 卡（``decision`` 非空）。``disabled`` 时返回空。
    """
    decisions = normalize_concept_decisions(decisions)
    if decisions.get("disabled"):
        return []
    from . import entity_registry as _entity_registry
    normalized = _entity_registry.normalize_registry(
        list(records) if isinstance(records, list) else [])
    try:
        pair_rows = [dict(pair) for pair in (pairs or []) if isinstance(pair, dict)]
    except (TypeError, ValueError):
        pair_rows = []
    cards = []
    for group, tier in _record_groups(normalized, decisions["manual_splits"]):
        options = _options_for_group(group, pair_rows)
        distinct = {consistency.spelling(o["target"]).casefold() for o in options}
        if len(group) < 2 and len(distinct) < 2:
            continue  # 单记录且译法唯一：没有需要决策的东西
        forms: List[str] = []
        keys: List[str] = []
        record_ids = []
        for record in group:
            record_ids.append(record.get("id"))
            for form in [record.get("source_form"), *(record.get("aliases") or [])]:
                name = consistency.spelling(form)
                if name and name not in forms:
                    forms.append(name)
                key = _casefold(name)
                if key and key not in keys:
                    keys.append(key)
        decision = decision_for_keys(decisions, keys)
        cards.append({
            "card_id": card_id(keys),
            "members": forms,
            "member_keys": sorted(keys),
            "record_ids": record_ids,
            "tier": tier,
            "confidence": ({"exact": "high", "punctuation": "medium",
                            "morphological": "low"}.get(tier)
                           if tier else None),
            "suggestion_only": tier == "morphological",
            "options": options,
            "affected_segments": sorted({index for option in options
                                         for index in option["segment_indexes"]}),
            "decision": decision,
        })
    cards.sort(key=lambda c: c["member_keys"][0])
    return cards


# ---------------- 人工决策（唯一写路径） ----------------

def _log(state: Dict[str, Any], action: str, **fields: Any) -> None:
    state.setdefault("log", []).append({"at": _now_iso(), "action": action,
                                        **fields})


def record_concept_decision(
    state: Dict[str, Any],
    *,
    card: Optional[Dict[str, Any]] = None,
    member_keys: Optional[Sequence[str]] = None,
    preferred_target: Optional[str] = None,
    keep_variants: bool = False,
    exceptions: Optional[Sequence[dict]] = None,
    note: str = "",
    registry: Any = None,
) -> Dict[str, Any]:
    """记录一次人工概念决策（state 就地更新，返回归一化后的决策结构）。

    若已有卡片与本次键交集命中（概念后来长大/重复确认），合并进既有卡片
    而不是新建——这正是"不出现矛盾重复卡"的机制。给了 ``registry`` 时把
    首选译名写回各成员记录（``EntityRegistry.lock``），翻译侧复用既有 hint。
    """
    decisions = normalize_concept_decisions(state)
    if card is not None:
        keys = [str(key) for key in card.get("member_keys") or []]
    else:
        keys = [_casefold(key) for key in member_keys or [] if _casefold(key)]
    keys = sorted({key for key in keys if key})
    if not keys:
        return decisions
    existing = decision_for_keys(decisions, keys)
    if existing is not None:
        keys = sorted(set(keys) | set(existing["member_keys"]))
        entry_id = card_id(existing["member_keys"])
    else:
        entry_id = card_id(keys)
    entry = {
        "member_keys": keys,
        "preferred_target": _clean(preferred_target) if not keep_variants else "",
        "keep_variants": bool(keep_variants),
        "exceptions": normalize_concept_decisions({
            "cards": {"x": {"member_keys": keys,
                            "exceptions": list(exceptions or [])}}})["cards"]["x"]["exceptions"],
        "decided_at": _now_iso(),
        "note": _clean(note, 240),
    }
    decisions["cards"][entry_id] = entry
    action = "keep" if keep_variants else "decide"
    _log(decisions, action, card_id=entry_id,
         preferred_target=entry["preferred_target"], note=entry["note"])
    if registry is not None and entry["preferred_target"]:
        for key in keys:
            record = next((r for r in registry.records
                           if _casefold(r.get("source_form")) == key
                           or any(_casefold(alias) == key
                                  for alias in r.get("aliases") or [])), None)
            if record is not None:
                registry.lock(record.get("source_form"), entry["preferred_target"],
                              note=f"concept decision: {entry['note']}")
    state.clear()
    state.update(decisions)
    return decisions


def split_concept_card(state: Dict[str, Any], form_a: str, form_b: str, *,
                       note: str = "", registry: Any = None) -> Dict[str, Any]:
    """人工断言两个 surface form 是不同概念：拆分既有卡，映射互不串。"""
    decisions = normalize_concept_decisions(state)
    key_a, key_b = _casefold(form_a), _casefold(form_b)
    if not key_a or not key_b or key_a == key_b:
        return decisions
    pair = sorted([key_a, key_b])
    if pair not in decisions["manual_splits"]:
        decisions["manual_splits"].append(pair)
        decisions["manual_splits"].sort()
    hit = decision_for_keys(decisions, [key_a, key_b])
    if hit is not None:
        old_id = card_id(hit["member_keys"])
        # 约定：第二个形式是"离开"的一方。它（及其探测键）离开卡片，
        # 卡片保留其余成员（含 key_a）。
        removed = {key_b} & set(hit["member_keys"])
        remaining = sorted(set(hit["member_keys"]) - removed)
        decisions["cards"].pop(old_id, None)
        if remaining:
            moved = dict(hit)
            moved["member_keys"] = remaining
            decisions["cards"][card_id(remaining)] = moved
        if registry is not None:
            for key in removed:
                record = next((r for r in registry.records
                               if _casefold(r.get("source_form")) == key), None)
                if record is not None and record.get("status") == "locked":
                    # 拆出去的记录解除文档级锁定，恢复自己观察到的主流译法。
                    observed = consistency.spelling(record.get("observed_target")) \
                        or consistency.spelling(record.get("preferred_target"))
                    record["status"] = "reviewed"
                    record["provenance"] = "reviewed"
                    if observed:
                        record["preferred_target"] = observed
    _log(decisions, "split", forms=[key_a, key_b], note=_clean(note, 240))
    state.clear()
    state.update(decisions)
    return decisions
