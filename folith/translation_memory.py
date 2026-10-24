"""翻译记忆的**作用域键**：一条记忆的身份是「目标语言 + 原文」。

为什么单独一个模块：这个格式是翻译记忆的身份定义，核心层（写入 / 命中）、
检查点恢复、以及语言资产界面（展示）都要用它。格式只写一遍，才不会出现
"核心层已经按语言隔离、展示层还在按原文找"的漂移——那种漂移的表现正是
界面看起来正常、命中却永远落空。

设计约束（发布阻断级）：

- 不给出目标语言就**无法**构造键，也就无法命中任何条目（fail closed）；
- 语言无法证明时**不写入**：写进去就是下一次跨语言命中的来源；
- 旧版以原文为键、没有语言标注的条目原样保留在文件里（不丢用户数据），
  但 `tm_record_language` 对它们返回空串，因此永远不会被自动命中。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

import hashlib
import re
import unicodedata

from .textual import normalize_language

# ␟ SYMBOL FOR UNIT SEPARATOR：真实文本里不会出现，因此键可以无歧义地拆开。
TM_SCOPE_SEP = "\u241f"


def tm_scope_key(target_lang, source) -> str:
    """作用域键 = 目标语言 + 原文；目标语言无法证明时返回空串。"""
    language = normalize_language(target_lang)
    if not language:
        return ""
    return f"{language}{TM_SCOPE_SEP}{source}"


def tm_unscope_key(key) -> tuple:
    """作用域键 -> (语言比较键, 原文)；旧版无作用域的键返回 ("", 键本身)。"""
    text = str(key or "")
    head, sep, tail = text.partition(TM_SCOPE_SEP)
    if not sep:
        return "", text
    return head, tail


def tm_record_language(key, record: Optional[Mapping[str, Any]] = None) -> str:
    """条目声明的目标语言比较键；无法证明时返回空串。

    记录字段优先（写入时落盘，是权威），键前缀兜底。旧版条目两者都没有，
    返回空串 = 语言未知 = **永远不参与自动命中**。
    """
    if isinstance(record, Mapping):
        declared = normalize_language(record.get("target_lang"))
        if declared:
            return declared
    head, _ = tm_unscope_key(key)
    return head


def tm_record(target, target_lang, **extra) -> Dict[str, Any]:
    """翻译记忆记录：`target_lang` 是记录的一部分，不是可选的注释。"""
    record = {"target": str(target or ""), "reviewed": True,
              "target_lang": str(target_lang)}
    record.update(extra)
    return record


def tm_put(tm: Dict[str, Any], source, target, target_lang, **extra) -> str:
    """把一条已审校译文写进记忆：键 = 目标语言 + 原文，返回写入的键。

    目标语言无法证明时**不写**（返回空串）：写进去就是下一个跨语言命中的
    来源。宁可少记一条，不可记错一条。
    """
    key = tm_scope_key(target_lang, source)
    if not key:
        return ""
    tm[key] = tm_record(target, target_lang, **extra)
    return key


def tm_discard(tm: Dict[str, Any], source, target_lang) -> bool:
    """删除某条原文在指定目标语言下的记忆；返回是否真的删除了条目。

    只删"语言可证明相同"的条目：语言未知的旧条目不是这条记忆，不能顺手删掉。
    """
    language = normalize_language(target_lang)
    if not language:
        return False
    removed = False
    scoped = tm_scope_key(target_lang, source)
    for candidate in (scoped, str(source or "")):
        if not candidate or candidate not in tm:
            continue
        if candidate != scoped \
                and tm_record_language(candidate, tm.get(candidate)) != language:
            continue
        del tm[candidate]
        removed = True
    return removed


def tm_legacy_keys(tm: Optional[Mapping[str, Any]]) -> List[str]:
    """语言无法证明的条目键：保留在文件里，但永不自动命中。"""
    return [key for key, record in (tm or {}).items()
            if not tm_record_language(key, record)]


# ---------------- 上下文指纹（#107：同一句话在不同语境下不是同一条记忆） ----------------
# 一条精确命中的记忆只证明「原文相同」，不证明「语境相同」。界面上一句
# "Close" 可以是按钮，也可以是句子里的动词；同一句标题在两章里的译法也可能
# 不同。因此每条新记忆在写入时同时记下前后原文段落的**归一化指纹**：
#   * 只存短哈希，不复制原文，tm.json 不会每个条目多出两段文字；
#   * 旧条目没有这两个字段 = 语境未知，保持原有自动复用行为（向后兼容）；
#   * 指纹只做排版归一化（NFKC/空白折叠），不做语义近似。

TM_CONTEXT_PREV = "ctx_prev"
TM_CONTEXT_NEXT = "ctx_next"
_TM_CONTEXT_DIGEST_SIZE = 6
_TM_CONTEXT_SPACE_RE = re.compile(r"\s+")


def tm_context_fingerprint(text: Optional[str]) -> str:
    """前后文段落的归一化指纹；空段落返回空串（表示"没有这一侧"）。"""
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = _TM_CONTEXT_SPACE_RE.sub(" ", value).strip()
    if not value:
        return ""
    return hashlib.blake2s(
        value.encode("utf-8"), digest_size=_TM_CONTEXT_DIGEST_SIZE).hexdigest()


def tm_context_fields(previous_source: Optional[str],
                      next_source: Optional[str]) -> Dict[str, str]:
    """写入记忆时附带的上下文字段（短哈希，可读性不是目的）。"""
    return {
        TM_CONTEXT_PREV: tm_context_fingerprint(previous_source),
        TM_CONTEXT_NEXT: tm_context_fingerprint(next_source),
    }


def tm_context_status(record: Optional[Mapping[str, Any]],
                      previous_source: Optional[str] = None,
                      next_source: Optional[str] = None) -> str:
    """比较记忆记录的上下文与当前上下文，返回 ``match`` / ``conflict`` / ``unknown``。

    * 记录没有任何上下文字段（旧版条目）-> ``unknown``：保持原有自动复用；
    * 记录有指纹，但当前那一侧缺失（首段/末段）-> 该侧不构成冲突；
    * 记录与当前两侧都一致 -> ``match``；
    * 任一侧指纹不一致 -> ``conflict``（调用方必须降级为"建议并确认"）。
    """
    if not isinstance(record, Mapping):
        return "unknown"
    recorded = {
        "prev": str(record.get(TM_CONTEXT_PREV) or ""),
        "next": str(record.get(TM_CONTEXT_NEXT) or ""),
    }
    if not recorded["prev"] and not recorded["next"]:
        return "unknown"
    current = {
        "prev": tm_context_fingerprint(previous_source),
        "next": tm_context_fingerprint(next_source),
    }
    for side in ("prev", "next"):
        if not recorded[side] or not current[side]:
            continue
        if recorded[side] != current[side]:
            return "conflict"
    return "match"


__all__ = [
    "TM_SCOPE_SEP", "tm_scope_key", "tm_unscope_key", "tm_record_language",
    "tm_record", "tm_put", "tm_discard", "tm_legacy_keys",
    "TM_CONTEXT_PREV", "TM_CONTEXT_NEXT", "tm_context_fingerprint",
    "tm_context_fields", "tm_context_status",
]
