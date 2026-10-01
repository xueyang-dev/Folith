"""`CompiledGlossary` 的语义等价、编译行为、mutation 契约与有界性。

Performance Phase 8。背景：`_term_pattern` 是「按 term 键控 + 4096 固定容量」的 LRU，
而真实工作集是整张术语表。实测（`scripts/phase8_terminology_pattern_scaling.py`）：

    G=4096  ->  1.00 compiles / unique pattern
    G=4097  ->  3.00 compiles / unique pattern     (3 = 批数，即每轮重编译全表)
    G=20000 ->  3.00

一个 term 之差 CPU +178%：这不是平滑曲线，是容量开关。本文件守住替代方案的四件事：

1. **结果不变**：与改造前的实现（`_legacy_term_matches`，本文件内的 oracle）逐例一致；
2. **每个 revision 每个 unique pattern 只编译一次**，与批数无关；
3. **mutation 契约**：增/删/改 source、改 target、改 status/scope、换术语表都不得复用
   过期 pattern 或带出过期元数据；
4. **有界**：修订数与 pattern 总数都有硬上界，不随文档增长。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from folith import models, terminology  # noqa: E402

import phase8_terminology_differential_gate as gate  # noqa: E402


def _legacy_term_matches(term: str, text: str) -> bool:
    """Phase 8 之前的匹配实现（本文件的 oracle；语义权威定义）。"""
    return gate.legacy_term_matches(term, text)


def _entries(sources):
    return models.normalize_glossary(
        [{"source": s, "target": f"译名{s[:4]}", "status": "locked"}
         for s in sources])


def _reset():
    terminology.reset_compiled_glossary_cache()
    terminology.reset_term_pattern_cache()
    re.purge()


# ---------------- 1. 语义等价 ----------------

#: §6 的语义面逐条点名（每个维度至少一例显式断言，便于失败时定位）。
CURATED = [
    ("AI", "AI is not MAIN and not MTI", True),          # 词界 + 大小写
    ("MT", "an MT system", True),
    ("MT", "The MTI tool translates books.", False),      # 类复数/邻词
    ("alpha", "alphabet soup", False),                    # 子串假阳性
    ("GA", "GAB", False),
    ("term_2", "term_2 and term_20", True),               # 下划线边界
    ("C++", "we write C++ code", True),                   # 正则元字符
    ("e.g.", "see e.g. the appendix", True),
    ("x.y", "x.y is a key", True),                        # 点号非通配
    ("x.y", "xZy is a key", False),
    ("co-op", "a co-op model", True),                     # 连字符
    ("Skopos", "the SKOPOS theory", True),                # 大小写不敏感
    ("  spaced  ", "spaced out", True),                   # 空白归一
    ("目的论", "翻译目的论是核心", True),                     # CJK 子串
    ("目的论", "翻译目的是核心", False),
    ("BERT模型", "BERT模型很大", True),                     # 混合
    ("語", "简体字里没有", False),                          # 繁体 vs 简体
    ("séance", "the se\u0301ance is here", False),        # NFC vs NFD 是不同串
    ("séance", "the séance is here", True),
    ("", "anything", False),
    ("term", "", False),
    ("term", None, False),
]


def test_curated_matrix_agrees_with_oracle():
    _reset()
    compiled = terminology.compiled_glossary(_entries(
        [case[0] for case in CURATED if case[0]]))
    for term, text, expected in CURATED:
        assert _legacy_term_matches(term, text) is expected, (term, text)
        assert compiled.matches(term, text) is expected, (term, text)


def test_differential_matches_legacy_oracle():
    """确定性随机差分：与 oracle 逐例一致（维度覆盖见 gate 的输出）。"""
    _reset()
    rng = gate.random.Random(20260926)
    glossary_terms = [case[0] for case in CURATED if case[0]] + \
        [f"randomterm{i}" for i in range(120)]
    report = gate.run_gate(gate.generate_cases(rng, 4000, glossary_terms),
                           glossary_terms)
    assert report["mismatches"] == 0, report["first_mismatches"]
    assert report["result"] == "EQUIVALENT"
    # 防空跑：正向与负向都要有，且维度数量要够
    assert report["positive_matches"] > 500, report
    assert report["negative_matches"] > 300, report
    assert len(report["dimensions"]) >= 12, report["dimensions"]


def test_unknown_string_falls_back_to_term_matches():
    """不在本 revision 内的字符串（如译名）必须走通用实现，不得静默返回 False。"""
    _reset()
    compiled = terminology.compiled_glossary(_entries(["Skopos"]))
    assert compiled.matches("never-compiled", "never-compiled here") is True
    assert compiled.matches("never-compiled", "absent") is False


# ---------------- 2. 编译行为：每个 revision 每个 pattern 只编译一次 ----------------

def test_compiles_once_per_revision_regardless_of_batches():
    """跨越 4096 边界后，编译数必须等于 unique patterns，而不是 patterns × 批数。"""
    _reset()
    sources = [f"CompileTerm{index}" for index in range(5000)]
    entries = _entries(sources)
    batches = [["alpha beta", "gamma delta"], ["other text", "more text"]]

    counter = {"count": 0}
    real_compile = re.compile

    def counting_compile(*args, **kwargs):
        counter["count"] += 1
        return real_compile(*args, **kwargs)

    re.compile = counting_compile
    try:
        compiled = terminology.compiled_glossary(entries)
        for _ in range(4):                      # 4 轮 × 2 批重扫
            for batch in batches:
                for entry in entries:
                    any(compiled.matches(entry["source"], seg) for seg in batch)
    finally:
        re.compile = real_compile

    assert compiled.pattern_count == 5000
    assert counter["count"] == 5000, counter          # 不是 5000 × 4 × 2
    # 同内容再次取用：完全不再编译
    before = counter["count"]
    assert terminology.compiled_glossary(entries).revision == compiled.revision
    assert counter["count"] == before


def test_legacy_lru_path_still_thrashes_above_capacity():
    """反向守卫：老路径在超容量时确实会反复编译（证明这条 cliff 是真实的）。"""
    _reset()
    sources = [f"ThrashTerm{index}" for index in range(300)]
    compiled = terminology.compile_glossary(sources)
    terminology.reset_term_pattern_cache()
    terminology.reset_compiled_glossary_cache()
    terminology._TERM_PATTERN_CACHE_MAX = 64          # 把容量压到工作集以下
    try:
        counter = {"count": 0}
        real_compile = terminology.re.compile

        def counting_compile(*args, **kwargs):
            counter["count"] += 1
            return real_compile(*args, **kwargs)

        terminology.re.compile = counting_compile
        try:
            for _ in range(3):
                for source in sources:
                    terminology.term_matches(source, "no match at all")
        finally:
            terminology.re.compile = real_compile
        # 3 轮 × 300 个不同术语，容量只有 64 -> 每轮都重编译
        assert counter["count"] == 900, counter
    finally:
        terminology._TERM_PATTERN_CACHE_MAX = 4096
        terminology.reset_term_pattern_cache()
    assert compiled.pattern_count == 300


def test_compiled_path_does_not_depend_on_term_lru():
    """清掉 term 级 LRU 后，已编译视图的结果与编译数都不受影响。"""
    _reset()
    compiled = terminology.compiled_glossary(_entries(["Alpha", "Beta"]))
    terminology.reset_term_pattern_cache()
    assert compiled.matches("Alpha", "the alpha value") is True
    assert terminology.term_pattern_cache_info()["size"] == 0


def test_default_fields_compile_source_and_all_glossary_names():
    """守卫「全部被匹配字符串」的口径：source / preferred / target 都要进视图。

    缓存键是这些字符串的内容修订号。若日后有人收窄字段集合或键，译名与首选译名就会
    退回逐次 `term_matches` 回落——结果仍然正确，但会重新引入随调用次数增长的编译，
    正是 Phase 8 要消除的那个模式。本测试把它钉住。
    """
    _reset()
    entries = _entries(["Alpha"])
    entries[0]["preferred"] = "Alternate"
    entries[0]["target"] = "Alternate"
    view = terminology.compiled_glossary(entries)
    assert view.pattern("Alpha") is not None
    assert view.pattern("Alternate") is not None                 # target/preferred 也编译
    assert view.pattern_count == 2, view.patterns
    # 且全程没有触碰 term 级 LRU（证明这两个 pattern 真来自视图，不是回落）
    assert terminology.term_pattern_cache_info()["size"] == 0
    assert view.matches("Alternate", "we use the alternate form") is True
    assert view.matches("Alternate", "no such word here") is False


# ---------------- 3. mutation 契约 ----------------# ---------------- 3. mutation 契约 ----------------
def test_add_term_is_visible():
    _reset()
    first = terminology.compiled_glossary(_entries(["Alpha"]))
    assert first.matches("Beta", "beta here") is True      # 回落也能匹配
    assert first.pattern("Beta") is None
    second = terminology.compiled_glossary(_entries(["Alpha", "Beta"]))
    assert second.pattern("Beta") is not None
    assert second.revision != first.revision


def test_remove_term_drops_the_pattern():
    _reset()
    terminology.compiled_glossary(_entries(["Alpha", "Beta"]))
    after = terminology.compiled_glossary(_entries(["Alpha"]))
    assert after.pattern("Beta") is None
    # 且旧视图不会通过缓存被返回
    assert terminology.compiled_glossary(_entries(["Alpha"])).revision == after.revision


def test_edit_source_invalidates_pattern():
    _reset()
    before = terminology.compiled_glossary(_entries(["Alpha"]))
    after = terminology.compiled_glossary(_entries(["AlphaX"]))
    assert after.revision != before.revision
    assert after.pattern("Alpha") is None
    assert after.pattern("AlphaX") is not None


def test_edit_target_changes_revision_only_when_target_is_a_match_field():
    """revision = 「全部被匹配字符串」的内容修订号，字段集合决定它是否对译名敏感。"""
    _reset()
    a = _entries(["Alpha"])
    b = _entries(["Alpha"])
    b[0]["target"] = b[0]["preferred"] = "另一个译名"
    # 只把 source 纳入匹配字段时，改译名不改变编译内容 -> 同一 revision
    with_sources_only = terminology.compiled_glossary(a, fields=("source",))
    other = terminology.compiled_glossary(b, fields=("source",))
    assert with_sources_only.revision == other.revision
    # 默认字段包含 target/preferred，因此默认取用会给出新 revision（译名也被匹配）
    assert terminology.compiled_glossary(a).revision != \
        terminology.compiled_glossary(b).revision


def test_status_change_does_not_leak_through_pattern_reuse():
    """pattern 可以复用，但 eligibility 必须由调用方当次的条目决定。"""
    _reset()
    locked = _entries(["Alpha"])
    locked[0]["status"] = "locked"
    rejected = _entries(["Alpha"])
    rejected[0]["status"] = "rejected"

    # 同一份 pattern（source / preferred / target 都相同）
    assert terminology.compiled_glossary(locked).revision == \
        terminology.compiled_glossary(rejected).revision

    segments = ["Alpha appears here"]
    assert terminology.select_glossary_for_segments(segments, locked)[0]
    assert terminology.select_glossary_for_segments(segments, rejected)[0] == []


def test_scope_change_is_caller_side():
    _reset()
    document = _entries(["Alpha"])
    document[0]["scope"] = "document"
    section = _entries(["Alpha"])
    section[0]["scope"] = "section:s9"
    segments = ["Alpha appears here"]
    assert terminology.select_glossary_for_segments(segments, document)[0]
    assert terminology.select_glossary_for_segments(
        segments, section, section_profile={"section_id": "s9"})[0]
    assert terminology.select_glossary_for_segments(
        segments, section, section_profile={"section_id": "s1"})[0] == []


def test_distinct_glossaries_do_not_share_patterns():
    """不同 glossary（不同 source 集合）不得串用已编译视图。"""
    _reset()
    one = terminology.compiled_glossary(_entries(["Alpha", "Beta"]))
    two = terminology.compiled_glossary(_entries(["Gamma"]))
    assert one.revision != two.revision
    assert two.pattern("Alpha") is None
    assert one.pattern("Gamma") is None


def test_compiled_glossary_retains_no_entries():
    """它只是派生加速结构：不得持有权威/可变状态（条目、状态、findings）。"""
    _reset()
    entries = _entries(["Alpha", "目的论"])
    compiled = terminology.compiled_glossary(entries)
    # 字段集合是契约：只有修订号 + 两部分编译产物，没有任何 entries 字段
    assert set(compiled.__dataclass_fields__) == {"revision", "patterns", "plain"}
    # 持有的成员全部是正则/字符串，没有任何 dict 形式的条目
    assert all(isinstance(p, re.Pattern) for p in compiled.patterns.values())
    assert all(isinstance(p, str) for p in compiled.patterns)
    assert all(isinstance(p, str) for p in compiled.plain)
    # 就地修改传入条目不影响已编译视图（视图不持引用）
    entries[0]["source"] = "Changed"
    assert compiled.pattern("Alpha") is not None


# ---------------- 4. 有界性 / 生命周期 ----------------

def test_cache_is_bounded_in_revisions_and_patterns():
    _reset()
    max_revisions = terminology.compiled_glossary_cache_info()["max_revisions"]
    for index in range(max_revisions + 6):
        terminology.compiled_glossary(_entries([f"Bounded{index}", f"Extra{index}"]))
    info = terminology.compiled_glossary_cache_info()
    assert info["revisions"] <= max_revisions, info
    assert info["patterns"] <= info["max_patterns"], info
    assert info["stat_evictions"] >= 6, info


def test_cache_hit_avoids_rebuild():
    _reset()
    entries = _entries([f"Hit{i}" for i in range(50)])
    first_view = terminology.compiled_glossary(entries)
    first = terminology.compiled_glossary_cache_info()
    second_view = terminology.compiled_glossary(entries)
    second = terminology.compiled_glossary_cache_info()
    assert second["stat_builds"] == first["stat_builds"]
    assert second["stat_hits"] == first["stat_hits"] + 1
    assert second_view is first_view          # 命中即复用同一份编译产物


def test_cache_info_and_reset_are_consistent():
    _reset()
    terminology.compiled_glossary(_entries(["Alpha"]))
    assert terminology.compiled_glossary_cache_info()["revisions"] == 1
    terminology.reset_compiled_glossary_cache()
    info = terminology.compiled_glossary_cache_info()
    assert info["revisions"] == 0 and info["patterns"] == 0


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  ✗ {name}: {exc}")
    print(json.dumps({"failures": failures}))
    raise SystemExit(1 if failures else 0)
