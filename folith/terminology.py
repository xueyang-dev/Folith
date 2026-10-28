"""术语候选提取与相关术语选择。

- 分布式采样提取（覆盖首/中/尾），不再只截取前 10000 字符；
- 合并重复候选，记录术语出现的【所有】segment_id；
- 自动术语默认 status=candidate（不得自动 locked），证据标记为
  model_knowledge 且不允许伪造 URL；
- 根据 DocumentProfile / SectionProfile 附加 domain 与 scope；
- 解析失败可重试并保留 warning，绝不静默生成空术语并宣称成功。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from . import models
from .document_profile import distributed_sample
from .request_control import get_request_controller as _get_request_controller


# ---------------- 匹配（词边界 + 大小写 + 短词防误命中）----------------

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 已编译的词边界模式。条目身份 = 术语原文，模式完全由它决定，因此缓存是纯函数
#: 备忘录，不改变任何语义。
#:
#: 为什么需要它：`re` 模块自带的模式缓存只有 512 条，而一次任务里同一个术语会被
#: 反复匹配（术语表 × 每个段落 × 每个检查点）。超过 512 个不同术语之后，**每次调用
#: 都重新编译**——实测 1000 条术语时单次匹配 ~68µs、100 条时 ~15µs（见
#: `docs/translation-setup-hotpath-audit.md`）。
#: 上限固定，不随文档或术语表增长，因此不会引入新的无界内存。
#:
#: ⚠️ **Phase 8 起它是 fallback，不再是主路径的性能保证。** 它是「按 term 键控 +
#: 固定容量」的缓存，而真实工作集是**整张术语表**（无上界）。一旦
#: `unique_patterns > _TERM_PATTERN_CACHE_MAX`，每次 glossary 扫描都会把它冲干净、
#: 每个 pattern 每轮重编译一次（实测 G=4096 → 1.00 compiles/pattern，G=4097 → 3.00，
#: 一个 term 之差 CPU +178%，见 `docs/terminology-pattern-scaling.md` §2）。
#: 主 translation / review 路径请用 `CompiledGlossary`：它是**按 glossary 内容修订号
#: 键控**的，容量与术语表同阶（O(G)），因此「每个 revision 每个 unique pattern 只编译
#: 一次」。本缓存继续服务 ad-hoc / 单发调用与向后兼容路径。
_TERM_PATTERN_CACHE: "OrderedDict[str, re.Pattern[str]]" = OrderedDict()
_TERM_PATTERN_CACHE_MAX = 4096
_TERM_PATTERN_LOCK = threading.Lock()


def _term_pattern(term: str) -> "re.Pattern[str]":
    """返回术语的词边界模式（大小写不敏感 + 字母数字边界）。"""
    cached = _TERM_PATTERN_CACHE.get(term)
    if cached is not None:
        return cached
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE)
    with _TERM_PATTERN_LOCK:
        _TERM_PATTERN_CACHE[term] = pattern
        while len(_TERM_PATTERN_CACHE) > _TERM_PATTERN_CACHE_MAX:
            _TERM_PATTERN_CACHE.popitem(last=False)
    return pattern


def term_pattern_cache_info() -> Dict[str, int]:
    """缓存规模（测试与审计用；不参与匹配逻辑）。"""
    return {"size": len(_TERM_PATTERN_CACHE), "max": _TERM_PATTERN_CACHE_MAX}


def reset_term_pattern_cache() -> None:
    """清空模式缓存（测试用；匹配结果与缓存无关）。"""
    with _TERM_PATTERN_LOCK:
        _TERM_PATTERN_CACHE.clear()


def term_matches(term: str, text: str) -> bool:
    """术语是否出现在文本中。

    - CJK 术语：直接子串（中文无词界，子串即出现）；
    - ASCII/混合术语：大小写不敏感 + 字母数字边界（"AI" 不会误命中
      "MAIN" / "MTI" 中的 "MT"）。
    """
    term = (term or "").strip()
    text = text or ""
    if not term or not text:
        return False
    if _CJK_RE.search(term):
        return term in text
    return bool(_term_pattern(term).search(text))


def term_spans(term: str, text: str) -> List[Tuple[int, int]]:
    """Return display spans using exactly the same boundaries as term_matches."""
    term = (term or '').strip()
    text = text or ''
    if not term or not text:
        return []
    pattern = re.compile(re.escape(term)) if _CJK_RE.search(term) else _term_pattern(term)
    return [(match.start(), match.end()) for match in pattern.finditer(text)]


def source_forms(entry: Mapping[str, Any]) -> List[str]:
    return list(dict.fromkeys(str(value).strip() for value in
                              [entry.get('source'), *(entry.get('source_aliases') or [])]
                              if value and str(value).strip()))


def entry_matches(entry: Mapping[str, Any], text: str,
                  matcher: Optional['CompiledGlossary'] = None) -> bool:
    match = matcher.matches if matcher else term_matches
    return any(match(source, text) for source in source_forms(entry))


# ---------------- 已编译术语视图（Performance Phase 8）----------------
#
# 问题：`_term_pattern` 是「按 term 键控 + 4096 固定容量」的 LRU，而真实工作集是
# **整张术语表**。容量 < 工作集时命中率为 0，每轮扫描都重编译全部 pattern。
#
# 修法不是把容量调大（那只是把边界从 4096 挪到下一个数字，术语表超过它照样 thrash），
# 而是**换工作单元**：从「每次调用一次编译」变成「每个 glossary revision 每个 unique
# pattern 一次编译」。匹配模式是术语原文的纯函数，因此这件事可以一次做完并复用。


def _sources_revision(sources: Iterable[str]) -> str:
    """由「参与匹配的字符串序列」派生的内容修订号。

    为什么不用现成的 token：

    - `models.glossary_hash(entries)` 是 O(G log G)（canonical JSON + 排序），
      每次查询都算一遍会把 regex 问题换成 hash 问题；
    - `glossary_frozen.version` **不是**可靠的内容修订号——它只在 re-freeze 时 +1，
      而 `state["glossary"]` 存在绕过 re-freeze 的写入点（Phase 7 §6.4 已记录），
      拿它当缓存键会返回**过期 pattern**，属于静默语义错误；
    - `id(obj)` / 对象地址与列表身份同理，随时可能被就地更新而键不变。

    这里直接对**决定 pattern 的那个投影**做内容哈希：匹配结果只取决于被匹配的字符串
    本身（默认字段是 source/preferred/target），与 status/scope 等元数据无关——因此改
    状态不会白白让编译视图失效。条目顺序不影响匹配结果，但进入哈希是为了让键与输入
    一一对应。

    成本是 O(Σ|s|)，**不是**可以忽略的量级（实测中位数，`tmp/phase8/key_cost_probe.py`，
    真实 EN->ZH 形态）：G=1,000 → 0.54 ms，G=10,000 → 6.54 ms，G=20,000 → 11.73 ms。
    之所以仍然可接受：每个 translation batch 只派生常数次（`core.check_translation_batch`
    已把 matcher 预派生一次并向下透传），而它替代掉的是 G×S 量级的全表扫描。
    若未来 G 再上一个数量级、且 key 派生开始主导耗时，正确做法是**减少调用次数**
    （把 matcher 继续向下透传），而不是把键削弱成子集投影。
    """
    digest = hashlib.sha1()
    for value in sources:
        digest.update(str(value or "").strip().encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class CompiledGlossary:
    """一个 glossary revision 的**派生**运行时加速结构（不是权威状态）。

    只持有编译产物，**不持有 entries**：条目元数据（target/status/behavior…）由调用方
    自己那份 freshly-normalized 条目提供。这样「改译名但 pattern 不变」这类变更不会
    让本对象带出过期元数据——它只回答「这个字符串出没出现在这段文本里」。

    - `patterns`：非 CJK 字符串 -> 已编译词边界模式（每个 unique 字符串恰好编译一次）
    - `plain`：CJK 字符串集合（走子串匹配，不需要正则）
    - 两者之外的字符串 -> 回落 `term_matches`，语义与历史完全一致
    """

    revision: str
    patterns: "Mapping[str, re.Pattern[str]]"
    plain: "frozenset[str]"

    @property
    def pattern_count(self) -> int:
        return len(self.patterns)

    @property
    def plain_count(self) -> int:
        return len(self.plain)

    def pattern(self, source: str) -> "Optional[re.Pattern[str]]":
        """该字符串在本 revision 里的模式；CJK / 未知字符串返回 None。"""
        return self.patterns.get(str(source or "").strip())

    def matches(self, source: str, text: str) -> bool:
        """与 `term_matches(source, text)` **逐例等价**的实现。"""
        term = (source or "").strip()
        text = text or ""
        if not term or not text:
            return False
        pattern = self.patterns.get(term)
        if pattern is not None:
            return bool(pattern.search(text))
        if term in self.plain:
            return term in text
        # 不在本 revision 内（例如被匹配的是译名/禁止译名这类非 source 字符串）：
        # 走通用实现，保证语义与历史逐例一致。
        return term_matches(term, text)

    def retained_bytes(self) -> int:
        """近似 retained size（审计用；RSS 实测在 benchmark 里）。"""
        total = sys.getsizeof(self) + sys.getsizeof(self.patterns)
        total += sum(sys.getsizeof(k) + sys.getsizeof(v)
                     for k, v in self.patterns.items())
        total += sum(sys.getsizeof(t) for t in self.plain)
        return total


#: 按 revision 键控的有界缓存。生命周期是「translation stage / glossary revision」：
#: 命中即复用，超出修订数或超出 pattern 总预算就按 LRU 淘汰，因此是
#: O(G) 且有硬上界，不会退化成 process-global 无界 dict。
_COMPILED_CACHE: "OrderedDict[str, CompiledGlossary]" = OrderedDict()
_COMPILED_CACHE_MAX_REVISIONS = 8
_COMPILED_CACHE_MAX_PATTERNS = 60000
_COMPILED_CACHE_LOCK = threading.Lock()
_COMPILED_STATS: Dict[str, int] = {"builds": 0, "hits": 0, "evictions": 0}


def compile_glossary(sources: Iterable[str], *,
                     revision: Optional[str] = None) -> CompiledGlossary:
    """编译给定的字符串集合（不做缓存，纯构造）。"""
    patterns: Dict[str, "re.Pattern[str]"] = {}
    plain: set = set()
    for raw in sources:
        term = (raw or "").strip()
        if not term or term in patterns or term in plain:
            continue
        if _CJK_RE.search(term):
            plain.add(term)
        else:
            patterns[term] = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
                re.IGNORECASE)
    return CompiledGlossary(
        revision=revision if revision is not None else _sources_revision(sources),
        patterns=MappingProxyType(patterns),
        plain=frozenset(plain),
    )


def _compiled_total_patterns() -> int:
    return sum(item.pattern_count for item in _COMPILED_CACHE.values())


#: 默认纳入已编译视图的条目字段。`preferred` / `target` 也会被 `term_matches` 匹配
#: （`check_glossary_compliance` 检查译名是否出现、`translation_planner` 检查 expected
#: 译名），因此一并编译。对「中文为目标语」的术语表这些串走 plain 子串集合、**不产生
#: 额外正则**；对反向（英文为目标语）术语表则是 +2G 个 pattern，仍与 G 同阶。
_DEFAULT_MATCH_FIELDS = ("source", "preferred", "target")


def compiled_glossary(entries: Iterable[Any], *,
                      fields: Iterable[str] = _DEFAULT_MATCH_FIELDS,
                      extra_sources: Iterable[str] = ()) -> CompiledGlossary:
    """取（必要时构造）该 glossary 内容对应的已编译视图。

    `entries` 可以是 normalized 条目 dict，也可以是裸字符串序列。
    `fields` 选择要纳入的条目字段（默认 source/preferred/target），`extra_sources`
    用于补充任何其他会被 `term_matches` 匹配的字符串。

    同一张术语表在所有调用点得到**同一个键**（键只看这些被匹配字符串的内容），
    因此 batch 之间、setup 与 review 之间复用同一份编译产物。
    """
    sources: List[str] = []
    field_names = tuple(fields)
    for entry in entries:
        if isinstance(entry, Mapping):
            for name in field_names:
                sources.append(str(entry.get(name) or ""))
            if 'source' in field_names:
                sources.extend(source_forms(entry)[1:])
        else:
            sources.append(str(entry or ""))
    sources.extend(str(value or "") for value in extra_sources)

    # 键 = **全部**被匹配字符串的内容哈希（即 `revision` 是真正的内容修订号）。
    #
    # 曾评估过的替代方案：键只取 `fields` 的第一个字段（默认 source），把键的字节数
    # 降到 1/3。实测（`tmp/phase8/key_cost_probe.py`，真实 EN->ZH 形态，中位数）：
    #
    #     G       全字段键    只取首字段
    #     1,000   0.540 ms   0.146 ms
    #     4,096   2.408 ms   0.833 ms
    #    10,000   6.540 ms   3.025 ms
    #    20,000  11.729 ms   4.112 ms
    #
    # 它**是安全的**（已编译视图是「字符串 -> pattern」的纯函数；某字符串没进视图时
    # `matches()` 回落 `term_matches`，CJK 串只进 plain 子串集合，因此最坏情况是少编译
    # 几个字符串，永远不会命中过期 pattern）——但安全论证依赖一条未被强制的调用方不变式
    # （「查询的字符串必须来自调用方自己的 entries」），且收益只在 G > 4096 时兑现，
    # 而那正是本轮已经拿到 3.00x -> 1.00x（编译数）与 18.4x（工作负载）的场景。
    # 为 G=10,000 时约 9% 的收益去换一个有附加条件的缓存身份不划算，故取全字段键：
    # `revision` 无条件等于「这份编译视图的内容」，任何调用方都能直接依赖。
    key = _sources_revision(sources)
    with _COMPILED_CACHE_LOCK:
        cached = _COMPILED_CACHE.get(key)
        if cached is not None:
            _COMPILED_CACHE.move_to_end(key)
            _COMPILED_STATS["hits"] += 1
            return cached

    compiled = compile_glossary(sources, revision=key)
    with _COMPILED_CACHE_LOCK:
        cached = _COMPILED_CACHE.get(key)
        if cached is not None:                    # 并发下别人先建好了
            _COMPILED_CACHE.move_to_end(key)
            _COMPILED_STATS["hits"] += 1
            return cached
        _COMPILED_CACHE[key] = compiled
        _COMPILED_STATS["builds"] += 1
        while len(_COMPILED_CACHE) > 1 and (
                len(_COMPILED_CACHE) > _COMPILED_CACHE_MAX_REVISIONS
                or _compiled_total_patterns() > _COMPILED_CACHE_MAX_PATTERNS):
            _COMPILED_CACHE.popitem(last=False)
            _COMPILED_STATS["evictions"] += 1
    return compiled


def compiled_glossary_cache_info() -> Dict[str, int]:
    """已编译视图缓存规模（测试与审计用；不参与匹配逻辑）。"""
    with _COMPILED_CACHE_LOCK:
        return {
            "revisions": len(_COMPILED_CACHE),
            "max_revisions": _COMPILED_CACHE_MAX_REVISIONS,
            "patterns": _compiled_total_patterns(),
            "max_patterns": _COMPILED_CACHE_MAX_PATTERNS,
            **{f"stat_{k}": v for k, v in _COMPILED_STATS.items()},
        }


def reset_compiled_glossary_cache() -> None:
    """清空已编译视图缓存（测试用；匹配结果与缓存无关）。"""
    with _COMPILED_CACHE_LOCK:
        _COMPILED_CACHE.clear()
        for key in _COMPILED_STATS:
            _COMPILED_STATS[key] = 0


def find_occurrences(term: str, paragraphs: List[str]) -> List[int]:
    """返回术语在全部段落中的 segment_id 列表（不是只记第一次出现）。"""
    return [i for i, p in enumerate(paragraphs) if term_matches(term, p)]


# ---------------- 外部证据 provider 接口（预留，默认不接入网络）----------------

class TermEvidenceProvider(Protocol):
    """真实外部证据 provider 接口（预留）。

    当前版本不要求接入真实搜索服务，默认在无网络/无搜索 API 下正常工作
    （NoopEvidenceProvider）。接入真实 provider 时：
    - fetch_evidence 必须返回带真实 url 的 external 证据（url 由来源返回，
      绝不允许模型自行编造）；
    - 无法获取来源时返回 []（normalize_evidence 会把无 url 的 external
      自动降级为 model_knowledge）。
    """

    name: str

    def fetch_evidence(self, term: str,
                       domain: str = "") -> List[models.TermEvidence]:
        ...


class NoopEvidenceProvider:
    """默认空 provider：不发起任何网络/API 调用。"""

    name = "noop"

    def fetch_evidence(self, term: str,
                       domain: str = "") -> List[models.TermEvidence]:
        return []


def get_provider(name: str = "noop") -> TermEvidenceProvider:
    """按名称获取 provider（未注册/未知名称一律回退到 noop，保证离线可用）。"""
    providers = {"noop": NoopEvidenceProvider()}
    return providers.get(name, providers["noop"])


# ---------------- 自动抽取 ----------------

def _extract_system_prompt(target_lang: str, n_windows: int) -> str:
    return f"""你是长文翻译的术语编辑。
    用户会提供同一文档的多个分布样本。仅提取原文中确实出现、且对后文翻译一致性有帮助的
    专名或固定概念；按文档类型选择，不要为了达到指定数量而补造条目，允许返回空数组。

    给每个 source 提供一个适合样本语境的首选译名。不要用斜杠罗列备选译法；
    无法确定时省略该项。书名、人物、地点、组织和有名称的设定可作为候选，
    但不要把普通主题词或文学评论概念伪装成原文术语。
    译名使用{target_lang}，并按原文逐字核对 source。

    请严格输出合法的 JSON 数组格式，绝对不要包含任何其他多余的解释文字，格式如下：
    [
        {{"Source": "英文专业术语1", "Target": "中文专业译名1",
          "domain": "可选：所属领域", "scope": "可选：document 或 section:<id>"}}
    ]"""


def _parse_term_array(text: str) -> Optional[List[Dict[str, Any]]]:
    """宽容解析术语数组（沿用 core.parse_json_array 的策略，独立实现避免循环依赖）。"""
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.DOTALL)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.DOTALL).strip()
    try:
        obj = json.loads(candidate)
        if isinstance(obj, list):
            return obj
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\[", candidate):
        try:
            obj, _ = decoder.raw_decode(candidate[m.start():])
        except Exception:
            continue
        if isinstance(obj, list):
            return obj
    return None


def _assign_scope(entry: models.GlossaryEntry, sections: List[Dict[str, Any]]) -> str:
    """按出现位置分配 scope：全部落在同一 section 内 -> section:<id>；否则 document。"""
    occ = entry.get("occurrences") or []
    if not occ:
        return "document"
    for sec in sections or []:
        start = sec.get("start_segment")
        end = sec.get("end_segment")
        sid = sec.get("section_id")
        if start is None or end is None or not sid:
            continue
        if all(start <= i <= end for i in occ):
            return f"section:{sid}"
    return "document"


def extract_auto_terms_v2(
    paragraphs: List[str],
    target_lang: str,
    provider: str,
    api_key: str,
    model: str,
    document_profile: Optional[models.DocumentProfile] = None,
    sections: Optional[List[Dict[str, Any]]] = None,
    call_llm: Optional[Callable] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[List[models.GlossaryEntry], List[str]]:
    """分布式术语候选提取。返回 (entries, warnings)。

    entries：status=candidate，occurrences 为全部 segment_id，evidence 为
    model_knowledge（无 URL），domain/scope 来自画像或按出现位置推断。
    失败：返回 ([] , [warning])，绝不静默宣称成功。
    """
    def _is_cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    if _is_cancelled():
        raise RuntimeError("任务已请求取消")

    warnings: List[str] = []
    rejected_zero_occurrence: List[str] = []
    rejected_ambiguous: List[str] = []
    samples = distributed_sample(paragraphs)
    if not samples:
        return [], ["术语抽取失败：无文本样本"]

    sample_text = "\n\n".join(
        f"【样本 {w['window'] + 1}，段落 {w['start_segment']}-{w['end_segment']}】\n{w['text']}"
        for w in samples
    )
    sys_prompt = _extract_system_prompt(target_lang, len(samples))
    if call_llm is None:
        import core
        call_llm = core.call_llm

    parsed = None
    last_err = "模型未返回内容"
    # 限流走全进程共享的 provider 闸门；这条路径以前是自己循环 `sleep(1)` 15 次，
    # 是同一限流问题的第二套实现（而且 15 秒固定、与真实退避无关）。
    controller = _get_request_controller(provider, model)
    for _attempt in range(3):
        if _is_cancelled():
            raise RuntimeError("任务已请求取消")
        try:
            controller.acquire(_is_cancelled)
            res = call_llm(provider, api_key, model, sys_prompt, sample_text, temperature=0.1)
            controller.note_success()
            parsed = _parse_term_array(res)
            if parsed is None:
                raise ValueError("返回内容不是合法 JSON 数组")
            break
        except Exception as e:
            from .workflow_errors import raise_if_fatal
            raise_if_fatal(e)
            if "任务已请求取消" in str(e) or "请求取消" in str(e) or _is_cancelled():
                raise RuntimeError("任务已请求取消") from e
            last_err = str(e)
            if "429" in last_err or "RESOURCE_EXHAUSTED" in last_err \
                    or "rate limit" in last_err.lower():
                controller.note_rate_limited()
                continue
            break
    if parsed is None:
        warnings.append("术语抽取失败（限流或返回格式异常），已跳过该步骤；"
                        "可稍后点击“继续处理”重试。")
        return [], warnings

    profile_domain = (document_profile or {}).get("domain") or ""
    sections = sections or (document_profile or {}).get("sections") or []
    merged: Dict[str, models.GlossaryEntry] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        src = str(item.get("Source") or item.get("source") or "").strip()
        tgt = str(item.get("Target") or item.get("target") or "").strip()
        if len(src) <= 1 or not tgt:
            continue
        occurrences = find_occurrences(src, paragraphs)
        if not occurrences:
            rejected_zero_occurrence.append(src)
            continue
        if re.search(r"\s/\s", tgt):
            rejected_ambiguous.append(src)
            continue
        key = src.casefold()
        existing = merged.get(key)
        if existing is None:
            entry = models.normalize_glossary_entry({
                "source": src, "target": tgt,
                "domain": str(item.get("domain") or "").strip() or profile_domain,
                "scope": str(item.get("scope") or "").strip(),
                "status": "candidate",
                "occurrences": occurrences,
                "evidence": [{
                    "evidence_type": "model_knowledge",
                    "source_name": "",
                    "note": "自动抽取（模型知识，未经人工核实）",
                    "quote": "",
                    "url": "",
                    "confidence": 0.5,
                }],
            })
            if entry is not None:
                merged[key] = entry
        else:
            # 合并重复候选：并集 occurrences；保留首次译名
            occ = set(existing["occurrences"]) | set(occurrences)
            existing["occurrences"] = sorted(occ)

    if rejected_zero_occurrence:
        sample = "、".join(rejected_zero_occurrence[:5])
        warnings.append(
            f"已跳过 {len(rejected_zero_occurrence)} 个原文无命中的自动术语候选"
            + (f"（如：{sample}）" if sample else ""))
    if rejected_ambiguous:
        sample = "、".join(rejected_ambiguous[:5])
        warnings.append(
            f"已跳过 {len(rejected_ambiguous)} 个带多选译名的自动术语候选"
            + (f"（如：{sample}）" if sample else ""))

    entries = list(merged.values())
    for e in entries:
        if not e.get("scope"):
            e["scope"] = _assign_scope(e, sections)
        e["confidence"] = 0.5
    return entries, warnings


# ---------------- 相关术语选择 ----------------

def select_glossary_for_segments(
    segments: List[str],
    glossary: List[models.GlossaryEntry],
    document_profile: Optional[models.DocumentProfile] = None,
    section_profile: Optional[Dict[str, Any]] = None,
    max_provisional: int = 5,
    segment_ids: Optional[Sequence[int]] = None,
) -> Tuple[List[models.GlossaryEntry], List[str]]:
    """为当前批次筛选相关术语。返回 (selected_entries, injected_entry_ids)。

    规则：
    1. 只注入当前批次原文实际出现的 locked translate 条目；
    2. 只注入当前批次实际出现的 preserve 条目；
    3. provisional 条目只能作为建议，数量受限（max_provisional）；
    4. 支持 scope：global / document / section:<id> / segment:<id>；
    5. 大小写差异与词边界匹配（term_matches），避免短词 substring 误命中；
    6. rejected 条目永不注入。

    segment_ids 与 segments 逐段对齐，使用文档全局段号；省略时沿用批内下标。
    """
    if not segments or not glossary:
        return [], []
    joined = "\n".join(segments)
    selected: List[models.GlossaryEntry] = []
    seen_ids = set()
    provisional_count = 0

    normalized = models.normalize_glossary(glossary)
    # Phase 8：整表扫描走 revision 键控的已编译视图，而不是 4096 容量的 term 级 LRU。
    # 结果与 `term_matches` 逐例等价（见 tests/terminology_compiled_glossary_test.py）。
    matcher = compiled_glossary(normalized)
    for e in normalized:
        if e["status"] == "rejected":
            continue
        if not any(entry_matches(e, s, matcher) for s in segments):
            continue
        # scope 过滤
        scope = (e.get("scope") or "").strip()
        if scope and scope != "global":
            if scope == "document":
                pass  # 文档级术语对批次可见（仅当文档画像存在时视为已确认）
            elif scope.startswith("section:"):
                if not section_profile or section_profile.get("section_id") != scope[8:]:
                    continue
            elif scope.startswith("segment:"):
                seg_id = scope[8:]
                ids = segment_ids if segment_ids is not None else range(len(segments))
                if not any(str(i) == seg_id for i in ids):
                    continue
            else:
                continue  # 未知 scope 不注入
        if e["status"] == "provisional":
            if provisional_count >= max_provisional:
                continue
            provisional_count += 1
        if e["id"] in seen_ids:
            continue
        seen_ids.add(e["id"])
        selected.append(e)
    return selected, sorted(seen_ids)


def glossary_block(entries: List[models.GlossaryEntry]) -> str:
    """把筛选后的条目渲染成注入翻译/审校 prompt 的文本块（含 entry ID 便于审计）。"""
    translate = [e for e in entries if e["behavior"] == "translate"
                 and str(e.get("preferred") or e.get("target") or "").strip()]
    translations_by_source = {}
    for entry in translate:
        scope = str(entry.get("scope") or "").strip()
        scope = "document" if scope in ("", "global", "document") else scope
        key = (entry["source"].casefold(), scope)
        translations_by_source.setdefault(key, set()).add(
            str(entry.get("preferred") or entry.get("target") or "").casefold())
    # A prompt cannot resolve an ambiguous human decision. Keep affected terms
    # out of model input until the conflict is resolved; the delivery/review
    # projections still expose the conflict to the operator.
    ambiguous = {key for key, translations in translations_by_source.items()
                 if len(translations) > 1}

    def unambiguous(entry):
        scope = str(entry.get("scope") or "").strip()
        scope = "document" if scope in ("", "global", "document") else scope
        return (entry["source"].casefold(), scope) not in ambiguous

    locked_translate = [e for e in translate
                        if e["status"] == "locked" and unambiguous(e)]
    preserve = [e for e in entries if e["behavior"] == "preserve"]
    provisional = [e for e in translate
                   if e["status"] != "locked" and unambiguous(e)]
    lines = []
    if locked_translate:
        lines.append("【锁定术语（必须使用首选译名，不得使用禁止译名）】：")
        for e in locked_translate:
            seg = f"- {' / '.join(source_forms(e))} -> {e['preferred']}（ID: {e['id']}）"
            if e["forbidden"]:
                seg += f"（禁止：{'、'.join(e['forbidden'])}）"
            lines.append(seg)
    if preserve:
        ids = "、".join(f"{e['source']}（ID: {e['id']}）" for e in preserve)
        lines.append("【必须保留原文的术语/名称】：" + ids)
    if provisional:
        lines.append("【建议术语（仅供参考，请优先采用）】：")
        for e in provisional:
            lines.append(f"- {' / '.join(source_forms(e))} -> {e['target']}（ID: {e['id']}）")
    return "\n".join(lines)


def glossary_to_terms(entries: List[models.GlossaryEntry]) -> Dict[str, str]:
    """翻译行为术语 -> 扁平 dict（报告/旧接口兼容）。"""
    return {e["source"]: (e["preferred"] or e["target"])
            for e in models.normalize_glossary(entries)
            if e["behavior"] == "translate" and e["target"]}


# ---------------- 术语表依赖失效（glossary staleness）----------------

_SEMANTIC_FIELDS = ("source", "target", "preferred", "behavior", "status")


def glossary_changes_since(state: Dict[str, Any], old_hash: str) -> Optional[List[Dict[str, Any]]]:
    """Return old/new entries whose review inputs changed, or None if unknown."""
    versions = state.get('glossary_versions') or []
    previous = next((v for v in versions if isinstance(v, dict)
                     and v.get('glossary_hash') == old_hash), None)
    if previous is None:
        return None
    current = models.normalize_glossary((state.get('glossary_frozen') or {}).get('entries')
                                        or state.get('glossary') or [])
    old = models.normalize_glossary(previous.get('entries') or [])
    def grouped(entries):
        result = {}
        for entry in entries:
            result.setdefault(entry['source'].casefold(), []).append(entry)
        return result
    before, after = grouped(old), grouped(current)
    changed = []
    for key in before.keys() | after.keys():
        old_rows, new_rows = before.get(key, []), after.get(key, [])
        if sorted(models._canonical_entry_json(e) for e in old_rows) != \
                sorted(models._canonical_entry_json(e) for e in new_rows):
            changed.extend(old_rows + new_rows)
    return changed


def glossary_changes_affect_segments(state: Dict[str, Any], changes, indexes) -> bool:
    """Unknown historic inputs remain conservatively stale."""
    if changes is None:
        return True
    pairs = state.get('pairs') or []
    paras = state.get('paras') or []
    if not indexes or any(i < 0 or i >= max(len(pairs), len(paras)) for i in indexes):
        return True
    for i in indexes:
        source = str((pairs[i].get('source') if i < len(pairs) else '')
                     or (paras[i] if i < len(paras) else '') or '')
        if any(i in (entry.get('occurrences') or []) or entry_matches(entry, source)
               for entry in changes):
            return True
    return False


def _semantic_entry_key(e: models.GlossaryEntry) -> str:
    """术语决策的语义键：source/target/preferred/behavior/status + 排序后的 forbidden。"""
    e = models.normalize_glossary_entry(e) or {}
    payload = {k: e.get(k) for k in _SEMANTIC_FIELDS}
    payload["forbidden"] = sorted(e.get("forbidden") or [])
    if e.get('source_aliases'):
        payload['source_aliases'] = sorted(e['source_aliases'])
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def stale_segments_for_glossary(state: Dict[str, Any]) -> List[int]:
    """返回受冻结术语表变更影响的 segment indexes（纯函数，不修改状态）。

    规则：
    - pair 记录了 glossary_hash_used 时：按注入 entry 的语义变化精确判定；
    - 无记录（旧任务 / 快速模式）时：保守扫描“当前冻结中新增或收紧的
      锁定/保留约束”是否命中本段原文；
    - 语义未变化的术语不使段落失效（不做整本失效）。
    """
    pairs = state.get("pairs") or []
    frozen = state.get("glossary_frozen") or {}
    versions = [v for v in (state.get("glossary_versions") or [])
                if isinstance(v, dict)]
    current = models.normalize_glossary(frozen.get("entries") or [])
    cur_by_source = {e["source"].casefold(): e for e in current}
    ver_by_hash = {
        v.get("glossary_hash"): models.normalize_glossary(v.get("entries") or [])
        for v in versions
    }
    # Legacy pairs lack the version used at translation time.  When updating an
    # existing frozen glossary, compare against the immediately preceding
    # version instead of treating every unchanged locked entry as newly added.
    previous = models.normalize_glossary(versions[-2].get('entries') or []) \
        if len(versions) >= 2 else None
    previous_by_source = {e['source'].casefold(): e for e in previous or []}
    legacy_changed = [(old, cur_by_source[key])
                      for key, old in previous_by_source.items()
                      if key in cur_by_source and
                      _semantic_entry_key(old) != _semantic_entry_key(cur_by_source[key])]
    # Phase 8：这里的形状是「每个 pair 重扫整张当前术语表」，是全局最坏的一处扫描
    # （pairs × G）。每张版本表各自一份已编译视图，键是内容修订号，不会串用。
    cur_matcher = compiled_glossary(current)
    ver_matchers = {
        digest: compiled_glossary(entries)
        for digest, entries in ver_by_hash.items()
    }
    version_source_maps = {digest: {e['source'].casefold(): e for e in entries}
                           for digest, entries in ver_by_hash.items()}
    stale: List[int] = []
    for i, p in enumerate(pairs):
        used_hash = p.get("glossary_hash_used")
        used = ver_by_hash.get(used_hash) if used_hash else previous
        used_by_source = version_source_maps.get(used_hash, {}) if used_hash else previous_by_source
        used_matcher = ver_matchers.get(used_hash) if used_hash else None
        src = p.get("source") or ""
        affected = bool(not used_hash and p.get('stale_due_to_glossary'))
        if not used_hash and used is not None and not affected:
            # A changed preference matters for existing translations even when
            # old jobs did not record individual injected entry IDs.
            for old_entry, new_entry in legacy_changed:
                if entry_matches(old_entry, src) or entry_matches(new_entry, src):
                    affected = True
                    break
        if used is not None:
            used_id_map = {e["id"]: e for e in used}
            for uid in set(p.get("glossary_entry_ids") or []):
                ue = used_id_map.get(uid)
                if ue is None:
                    continue
                if used_matcher is None or \
                        not entry_matches(ue, src, used_matcher):
                    continue  # 注入是批次级的；只有本段实际出现该术语才算依赖
                ce = cur_by_source.get(ue["source"].casefold())
                if ce is None:
                    ce = next((entry for entry in current
                               if ue['source'].casefold() in
                               {form.casefold() for form in source_forms(entry)}), None)
                if ce is None:
                    continue  # 约束被移除：不算失效
                if _semantic_entry_key(ue) != _semantic_entry_key(ce):
                    affected = True
                    break
        if not affected:
            # 新增或收紧的约束命中本段原文 -> 失效
            for key, ce in cur_by_source.items():
                old = used_by_source.get(key)
                old_enforced = bool(old and (old["behavior"] == "preserve"
                                             or old["status"] == "locked"))
                new_enforced = ce["behavior"] == "preserve" \
                    or ce["status"] == "locked"
                if new_enforced and not old_enforced \
                        and entry_matches(ce, src, cur_matcher):
                    affected = True
                    break
        if affected:
            stale.append(i)
    return stale


# ---------------- 术语 QA（entry_id / segment_id 级）----------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{1,}$")


def _preserve_severity(source: str) -> str:
    """保留项丢失严重度：结构标识类（型号/编号/纯标识符）-> blocking，其余 actionable。"""
    src = (source or "").strip()
    if _IDENTIFIER_RE.match(src):
        return "blocking"
    return "actionable"


def _scope_applies(e: models.GlossaryEntry,
                   segment_id: Optional[int],
                   section_profile: Optional[Dict[str, Any]]) -> bool:
    """条目 scope 是否适用于当前段落/批次。

    - global / document / 空 scope：适用；
    - section:<id>：需要 section_profile 匹配；
    - segment:<id>：需要段号匹配；
    - 未知 scope：保守不适用（避免跨范围误报）。
    """
    scope = (e.get("scope") or "").strip()
    if not scope or scope in ("global", "document"):
        return True
    if scope.startswith("section:"):
        return bool(section_profile) and \
            section_profile.get("section_id") == scope[8:]
    if scope.startswith("segment:"):
        return segment_id is not None and str(segment_id) == scope[8:]
    return False


def check_glossary_compliance(
    src: str,
    tgt: str,
    glossary: List[models.GlossaryEntry],
    segment_id: Optional[int] = None,
    section_profile: Optional[Dict[str, Any]] = None,
    *,
    entries: Optional[List[models.GlossaryEntry]] = None,
    matcher: Optional[CompiledGlossary] = None,
) -> List[Dict[str, Any]]:
    """锁定术语的确定性合规检查（基于 entry_id + segment_id）。

    - locked translate：首选译名缺失 -> actionable；禁止译名出现 -> actionable；
    - locked preserve：丢失 -> 按类型 actionable / blocking；
    - 检查基于词边界匹配（term_matches），不依赖 reason 文本。

    `entries` / `matcher` 是**可选**的预派生视图：本函数按 segment 调用，批次内
    glossary 不变时调用方可以只派生一次（Phase 8 §9 的重复纯函数工作）。两个都不传
    时行为与历史完全一致。
    """
    findings: List[Dict[str, Any]] = []
    normalized = entries if entries is not None \
        else models.normalize_glossary(glossary)
    # Phase 8：本函数按 segment 调用，内层扫整张术语表——正是会被 4096 容量 LRU
    # 放大的形状（每段都重编译 G 个 pattern）。改走 revision 键控的已编译视图。
    matcher = matcher if matcher is not None else compiled_glossary(normalized)
    for e in normalized:
        if e["status"] != "locked":
            continue
        if not _scope_applies(e, segment_id, section_profile):
            continue
        if e["behavior"] == "preserve":
            matched_forms = [form for form in source_forms(e) if matcher.matches(form, src)]
            if matched_forms and not all(matcher.matches(form, tgt) for form in matched_forms):
                findings.append({
                    "type": "glossary", "severity": _preserve_severity(e["source"]),
                    "entry_id": e["id"], "segment_id": segment_id,
                    "category": "terminology_consistency",
                    "summary": f"译文遗漏锁定保留项「{e['source']}」",
                    "source_span": e["source"], "target_span": None,
                    "preferred_target": e["source"],
                    "explanation": f"项目术语表要求保留项「{e['source']}」原样出现，但当前译文中没有找到它。",
                    "recommendation": "确认该词是否确实需要保留；若需要，请补回原文形式并重新检查格式。",
                    "confidence": None, "detector": "Terminology QA",
                    "diagnostic_version": 1,
                    "reason": f"锁定保留项「{e['source']}」在译文中丢失",
                })
        else:
            preferred = e.get("preferred") or e.get("target")
            source_hit = entry_matches(e, src, matcher)
            if source_hit and preferred \
                    and not matcher.matches(preferred, tgt):
                findings.append({
                    "type": "glossary", "severity": "actionable",
                    "entry_id": e["id"], "segment_id": segment_id,
                    "category": "terminology_consistency",
                    "summary": f"术语「{e['source']}」未使用首选译名",
                    "source_span": e["source"], "target_span": None,
                    "preferred_target": preferred,
                    "explanation": f"项目术语表将「{e['source']}」锁定为首选译名「{preferred}」，但当前译文中未出现该译名。",
                    "recommendation": "检查当前语境是否有合理例外；如果没有，请统一为项目术语表中的首选译名。",
                    "confidence": None, "detector": "Terminology QA",
                    "diagnostic_version": 1,
                    "reason": f"锁定术语「{e['source']}」未使用首选译名「{preferred}」",
                })
            for fb in e.get("forbidden") or []:
                if source_hit and fb and matcher.matches(fb, tgt):
                    findings.append({
                        "type": "glossary", "severity": "actionable",
                        "entry_id": e["id"], "segment_id": segment_id,
                        "category": "terminology_consistency",
                        "summary": f"术语「{e['source']}」使用了禁止译名",
                        "source_span": e["source"], "target_span": fb,
                        "preferred_target": preferred,
                        "explanation": f"当前译文使用了术语「{e['source']}」的禁止译名「{fb}」，与已锁定术语规则冲突。",
                        "recommendation": "改用项目术语表中的首选译名，并检查同一术语在相邻段落中的译法。",
                        "confidence": None, "detector": "Terminology QA",
                        "diagnostic_version": 1,
                        "reason": f"术语「{e['source']}」使用了禁止译名「{fb}」",
                    })
    return findings


def detect_glossary_conflicts(
    pairs: List[Dict[str, str]],
    glossary: List[models.GlossaryEntry],
    sections: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """跨段扫描：同一 locked 术语在同一范围内出现多种译法时报告冲突。

    按术语在每段译文中的实际用法分组（首选 / 禁止译名 / 其他），
    同一术语出现 ≥2 种用法即报告 actionable 冲突（带 entry_id / segment_id）。
    """
    findings: List[Dict[str, Any]] = []
    normalized = models.normalize_glossary(glossary)
    matcher = compiled_glossary(normalized)
    for e in normalized:
        if e["status"] != "locked" or e["behavior"] != "translate":
            continue
        scope = (e.get("scope") or "").strip()
        if scope.startswith("segment:"):
            continue  # 单段条目不可能自身冲突
        want_section = scope[8:] if scope.startswith("section:") else None
        preferred = e.get("preferred") or e.get("target")
        if not preferred:
            continue
        usages: Dict[str, List[int]] = {}
        for i, p in enumerate(pairs):
            src, tgt = (p.get("source") or ""), (p.get("target") or "")
            if not entry_matches(e, src, matcher):
                continue
            if want_section is not None:
                sec_id = None
                for sec in sections or []:
                    s, en = sec.get("start_segment"), sec.get("end_segment")
                    if s is not None and en is not None and s <= i <= en:
                        sec_id = sec.get("section_id")
                        break
                if sec_id != want_section:
                    continue
            if matcher.matches(preferred, tgt):
                key = f"preferred:{preferred}"
            else:
                fb_hit = next((fb for fb in e.get("forbidden") or []
                               if fb and matcher.matches(fb, tgt)),
                              None)
                key = f"forbidden:{fb_hit}" if fb_hit else "other:未使用首选译名"
            usages.setdefault(key, []).append(i)
        if len(usages) > 1:
            for key, segs in sorted(usages.items()):
                kind = "首选" if key.startswith("preferred:") else (
                    "禁止" if key.startswith("forbidden:") else "其他")
                label = key.split(":", 1)[1] if ":" in key else key
                for i in segs:
                    findings.append({
                        "type": "glossary", "severity": "actionable",
                        "entry_id": e["id"], "segment_id": i, "conflict": True,
                        "category": "terminology_consistency",
                        "summary": f"术语「{e['source']}」在同一范围内出现多种译法",
                        "source_span": e["source"], "target_span": label,
                        "preferred_target": preferred,
                        "explanation": f"同一范围内的术语「{e['source']}」出现了不同译法，可能破坏项目术语的一致性。",
                        "recommendation": "检查这些译法是否由语境差异造成；若无明确理由，请统一为项目术语表中的译法。",
                        "confidence": None, "detector": "Terminology QA",
                        "diagnostic_version": 1,
                        "reason": f"锁定术语「{e['source']}」在同一范围内出现多种译法"
                                  f"（段 {i} 为{kind}译法「{label}」），需人工统一",
                    })
    return findings
