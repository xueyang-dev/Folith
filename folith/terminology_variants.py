"""术语变体归一化与候选聚类（issue #95 的建议层）。

设计要点（详见 docs/issue-95-terminology-variants.md）：

- **零模型调用**：纯本地计算，只依赖标准库；不新增任何 LLM/per-batch 调用。
- **建议层语义**：聚类结果永远是候选建议，绝不自动写入 entity registry 或
  glossary；唯一的写路径是显式的人工 merge/split 决策。
- **字符串相似度永不建立语义同一**：模块内没有 edit-distance / difflib；
  模糊关系只来自封闭的形态规则表（复数 / -ed / -ing / 首字母缩略 /
  连字符省略），且全部标记为 suggestion-only。CJK 之间不适用形态规则。
- **证据可追溯**：聚类成员携带原始 surface form、record id 与 occurrences；
  归一化只用于键，从不回写原形。
- **人工决策持久化**：manual_merges / manual_splits / disabled 存放在
  ``state["terminology_variant_decisions"]``；关闭聚类（disabled）后按原形
  展示，决策与证据原样保留。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence

__all__ = [
    "casefold_key",
    "punct_key",
    "normalize_variant_decisions",
    "build_candidate_clusters",
    "EMPTY_VARIANT_DECISIONS",
]

# ---------------- 归一化键 ----------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 破折号族：U+2010 .. U+2015、U+2043、U+2212 等常见横线一律折叠为 "-"。
_DASHES_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2043\u2212\uFE58\uFE63\uFF0D]")
# 引号族：弯引号折叠为直引号。
_QUOTES = {"\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
           "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
           "\u2032": "'", "\u2033": '"', "\u00ab": '"', "\u00bb": '"'}
_QUOTES_RE = re.compile("|".join(_QUOTES))
# 边缘标点（成簇时忽略条目首尾的引号/括号/句读，如 "Kafka" vs Kafka）。
_EDGE_PUNCT_RE = re.compile(r"^[\s\"'“”‘’()\[\]{}<>.,;:!?。、；：（）【】《》]+|"
                            r"[\s\"'“”‘’()\[\]{}<>.,;:!?。、；：（）【】《》]+$")


def _base_key(form: str) -> str:
    """NFKC + casefold + 空白折叠。这是所有更高层级键的前缀。"""
    value = unicodedata.normalize("NFKC", str(form or ""))
    value = re.sub(r"\s+", " ", value.strip())
    return value.casefold()


def casefold_key(form: str) -> str:
    """`exact` 层键：大小写 / 空白 / 全半角变体归一，其余字符原样保留。"""
    return _base_key(form)


def punct_key(form: str) -> str:
    """`punctuation` 层键：在 casefold_key 基础上折叠破折号族 / 引号族与边缘标点。

    注意：**连字符省略不属于本层**——"e-mail" vs "email" 比"哪个横线字符"
    激进得多（"re-cover" vs "recover" 是不同概念），归入形态建议层。
    """
    value = _base_key(form)
    value = _QUOTES_RE.sub(lambda m: _QUOTES[m.group(0)], value)
    value = _DASHES_RE.sub("-", value)
    value = _EDGE_PUNCT_RE.sub("", value)
    # 连续破折号折叠为一个（"co--op" ~ "co-op"），其余标点保持原样。
    value = re.sub(r"-{2,}", "-", value)
    return value


def _strip_hyphens(key: str) -> str:
    return key.replace("-", "")


# ---------------- 形态规则（封闭规则表；CJK 不适用） ----------------

def _plural_variants(key: str) -> List[str]:
    """复数 / 单数互推的受限形式。只做规则推导，不做相似度猜测。"""
    out = []
    if len(key) >= 3:
        out.append(key + "s")
        if key.endswith(("s", "x", "z", "ch", "sh")):
            out.append(key + "es")
        if key.endswith("y") and len(key) >= 4 and key[-2] not in "aeiou":
            out.append(key[:-1] + "ies")
    if key.endswith("ies") and len(key) >= 5:
        out.append(key[:-3] + "y")
    if key.endswith("es") and len(key) >= 5 and key[:-2].endswith(("s", "x", "z", "ch", "sh")):
        out.append(key[:-2])
    if key.endswith("s") and len(key) >= 4:
        out.append(key[:-1])
    return out


def _inflection_variants(key: str) -> List[str]:
    """-ed / -ing 屈折（含 e 脱落）。-able/-tion 等派生后缀**不在**规则内。"""
    out = []
    if len(key) >= 3:
        stem = key[:-1] if key.endswith("e") and len(key) >= 4 else key
        out.append(key + "ed")
        out.append(key + "ing")
        out.append(stem + "ed")
        out.append(stem + "ing")
    return out


def _initialism_key(form: str) -> Optional[str]:
    """多词全称的首字母缩略键；单词或含非字母词时返回 None。"""
    words = [w for w in re.findall(r"[A-Za-z]+", str(form or ""))]
    if len(words) < 2:
        return None
    return "".join(w[0] for w in words).casefold()


def _is_romanized(key: str) -> bool:
    """形态规则只适用于拉丁字母串（CJK 无词形变化）。"""
    return bool(key) and not _CJK_RE.search(key)


# ---------------- 决策结构 ----------------

EMPTY_VARIANT_DECISIONS = {
    "version": 1,
    "disabled": False,
    "manual_merges": [],
    "manual_splits": [],
}


def normalize_variant_decisions(raw: Any) -> Dict[str, Any]:
    """归一化人工决策结构；垃圾输入落到安全默认，绝不抛异常。"""
    decisions = {
        "version": 1,
        "disabled": False,
        "manual_merges": [],
        "manual_splits": [],
    }
    if not isinstance(raw, dict):
        return decisions
    decisions["disabled"] = bool(raw.get("disabled"))
    for group in raw.get("manual_merges") or []:
        if not isinstance(group, (list, tuple)) or len(group) < 2:
            continue
        keys = sorted({casefold_key(form) for form in group if str(form or "").strip()})
        if len(keys) >= 2:
            decisions["manual_merges"].append(keys)
    for pair in raw.get("manual_splits") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        left, right = casefold_key(pair[0]), casefold_key(pair[1])
        if left and right and left != right:
            decisions["manual_splits"].append(sorted([left, right]))
    decisions["manual_merges"].sort()
    decisions["manual_splits"].sort()
    return decisions


# ---------------- 聚类 ----------------

#: 边的置信层级（数字越小置信越高）。
_TIER_RANK = {"exact": 0, "punctuation": 1, "morphological": 2, "manual": 3}


def _member_key(member: Dict[str, Any]) -> str:
    return casefold_key(member.get("form"))


def build_candidate_clusters(
    members: Iterable[Dict[str, Any]],
    decisions: Any = None,
) -> List[Dict[str, Any]]:
    """把 surface forms 组成候选簇（纯函数，永不写入任何状态）。

    `members` 的每项：``{"form": str, "record_id": str|None, "occurrences": [int],
    "origin": str}``（record_id/occurrences/origin 可省略）。

    返回的每个簇：``{"tier", "confidence", "suggestion_only", "decision",
    "members"}``，成员携带原始 form 与全部证据字段。结果确定性排序。
    `decisions` 是 :func:`normalize_variant_decisions` 形状的结构（原始 dict
    也可以，会先归一化）；``disabled=True`` 时返回空列表。
    """
    if isinstance(decisions, dict) and decisions.get("version") != 1:
        decisions = normalize_variant_decisions(decisions)
    else:
        decisions = normalize_variant_decisions(decisions)
    if decisions.get("disabled"):
        return []

    # 去重：完全相同的 surface form 只保留一份（保留最完整的证据字段）。
    unique: Dict[str, Dict[str, Any]] = {}
    for raw in members:
        if not isinstance(raw, dict):
            continue
        form = str(raw.get("form") or "").strip()
        if not form:
            continue
        member = {
            "form": form,
            "record_id": raw.get("record_id"),
            "occurrences": sorted({
                int(i) for i in (raw.get("occurrences") or [])
                if isinstance(i, int) and not isinstance(i, bool)
            }),
            "origin": str(raw.get("origin") or "unknown"),
        }
        existing = unique.get(form)
        if existing is None or (existing.get("record_id") is None
                                and member.get("record_id") is not None):
            unique[form] = member
    items = list(unique.values())
    if len(items) < 2:
        return []

    keys = [_member_key(m) for m in items]
    pkeys = [punct_key(m["form"]) for m in items]

    # 同一 record 的成员之间不建边：registry 记录内部（source + alias）的
    # 形式差异不是"重复决策卡"，不该成为建议。
    same_record = keys is not None and all(
        items[i].get("record_id") is not None
        and items[i].get("record_id") == items[j].get("record_id")
        for i in range(len(items)) for j in range(i + 1, len(items))
    )
    if same_record:
        return []

    split_pairs = {tuple(pair) for pair in decisions.get("manual_splits") or []}

    def _edge(i: int, j: int) -> Optional[str]:
        pair = tuple(sorted([keys[i], keys[j]]))
        if pair in split_pairs:
            return None
        if keys[i] == keys[j]:
            return "exact"
        if pkeys[i] == pkeys[j]:
            return "punctuation"
        if _morph_edge(items[i]["form"], items[j]["form"], keys[i], keys[j]):
            return "morphological"
        return None

    n = len(items)
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # 边标签：成对记录（用于 split 剔除与层级判定）。
    edge_labels: Dict[tuple, str] = {}
    for i in range(n):
        for j in range(i + 1, n):
            label = _edge(i, j)
            if label:
                edge_labels[(i, j)] = label
                _union(i, j)

    # 人工 merge：无条件连接（split 优先——先拆后合的人工意图都成立，
    # split 在 _edge 里已剔除自然边；merge 边也受 split 约束）。
    manual_pairs: set = set()
    for group in decisions.get("manual_merges") or []:
        group_set = set(group)
        idxs = [i for i in range(n) if keys[i] in group_set]
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = min(idxs[a], idxs[b]), max(idxs[a], idxs[b])
                pair = tuple(sorted([keys[i], keys[j]]))
                if pair in split_pairs:
                    continue
                manual_pairs.add((i, j))
                if (i, j) not in edge_labels:
                    edge_labels[(i, j)] = "manual"
                _union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(_find(i), []).append(i)

    clusters = []
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        forms = {items[i]["form"] for i in idxs}
        if len(forms) < 2:
            continue
        labels = [edge_labels[(min(i, j), max(i, j))]
                  for i in idxs for j in idxs
                  if i < j and (min(i, j), max(i, j)) in edge_labels]
        if not labels:
            continue
        tier = min(labels, key=lambda name: _TIER_RANK[name])
        is_manual = any((min(i, j), max(i, j)) in manual_pairs
                        for i in idxs for j in idxs if i < j)
        clusters.append({
            "tier": tier,
            "confidence": {"exact": "high", "punctuation": "medium",
                           "morphological": "low", "manual": "manual"}[tier],
            "suggestion_only": tier == "morphological" and not is_manual,
            "decision": "manual" if is_manual else None,
            "members": sorted(
                (dict(items[i]) for i in idxs),
                key=lambda m: (casefold_key(m["form"]), m["form"]),
            ),
        })

    clusters.sort(key=lambda c: (
        _TIER_RANK[c["tier"]],
        casefold_key(c["members"][0]["form"]),
        c["members"][0]["form"],
    ))
    return clusters


def _morph_edge(form_a: str, form_b: str, key_a: str, key_b: str) -> bool:
    """封闭形态规则表。任一规则命中即建议（suggestion-only）；相似度不在规则内。"""
    if not (_is_romanized(key_a) and _is_romanized(key_b)):
        return False
    a, b = sorted([key_a, key_b])
    # 复数
    if a in _plural_variants(b) or b in _plural_variants(a):
        return True
    # -ed / -ing 屈折
    if a in _inflection_variants(b) or b in _inflection_variants(a):
        return True
    # 连字符省略（e-mail vs email；re-cover vs recover 属误合并风险，
    # 因此只允许作为 suggestion-only 建议，由人工确认）。
    if _strip_hyphens(a) and _strip_hyphens(a) == _strip_hyphens(b) and a != b:
        return True
    # 首字母缩略（WHO vs World Health Organization）
    for short, full in ((a, b), (b, a)):
        if len(short) >= 2 and short.isalpha() and short == short.casefold() \
                and short == (_initialism_key(form_a) if full == key_a else _initialism_key(form_b)) \
                and full != short:
            return True
    return False


# ---------------- Entity Registry 集成 ----------------


def registry_members(registry: Any) -> List[Dict[str, Any]]:
    """把 EntityRegistry 的记录展开为聚类成员（证据留原地，只读）。"""
    members = []
    for record in getattr(registry, "records", []) or []:
        record_id = record.get("id")
        members.append({
            "form": record.get("source_form"),
            "record_id": record_id,
            "occurrences": list(record.get("occurrences") or []),
            "origin": "entity_registry",
        })
        for alias in record.get("aliases") or []:
            members.append({
                "form": alias,
                "record_id": record_id,
                "occurrences": list(record.get("occurrences") or []),
                "origin": "entity_registry",
            })
    return [m for m in members if str(m.get("form") or "").strip()]
