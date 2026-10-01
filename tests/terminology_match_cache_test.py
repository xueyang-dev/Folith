"""`term_matches` 已编译模式缓存的正确性与有界性测试。

背景（Performance Phase 5 setup 审计）：
`term_matches` 原本每次调用都 `re.compile`。`re` 模块自带缓存只有 512 条，术语表
超过这个规模后**每次匹配都重新编译**：实测 1000 条术语时 ~68µs/次、100 条时
~15µs/次，而一次任务里同一术语要匹配 (术语表 × 段落 × 检查点) 次。

缓存只是纯函数备忘录（模式完全由术语文本决定），因此本文件同时守住两件事：
1. **结果不变**：与每次现编译的参照实现逐例一致；
2. **有界**：条目数不超过固定上限，不随术语表/文档增长。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import terminology


def _reference_term_matches(term: str, text: str) -> bool:
    """缓存改造前的原实现（每次现编译）。"""
    term = (term or "").strip()
    text = text or ""
    if not term or not text:
        return False
    if terminology._CJK_RE.search(term):
        return term in text
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE)
    return bool(pattern.search(text))


CASES = [
    ("AI", "AI is not MAIN and not MTI"),
    ("ai", "We use ai here"),
    ("MT", "MTI program"),
    ("MT", "an MT system"),
    ("alpha", "Alpha beta"),
    ("alpha", "alphabet soup"),
    ("术语", "这是术语测试"),
    ("GA", "gA and ga"),
    ("GA", "GAB"),
    ("term_2", "term_2 and term_20"),
    ("", "anything"),
    ("term", ""),
    ("  spaced  ", "spaced out"),
]


def test_results_match_uncached_reference():
    terminology.reset_term_pattern_cache()
    for term, text in CASES:
        assert terminology.term_matches(term, text) == \
            _reference_term_matches(term, text), (term, text)


def test_results_stable_across_many_distinct_terms():
    terminology.reset_term_pattern_cache()
    for index in range(1200):
        term = f"Term{index}"
        assert terminology.term_matches(term, f"the Term{index} appears") is True
        assert terminology.term_matches(term, "TermX appears") is False
        assert terminology.term_matches(term, f"preTerm{index}post") is False
    # 重复一轮：命中缓存后结果必须完全一致
    for index in range(1200):
        term = f"Term{index}"
        assert terminology.term_matches(term, f"the Term{index} appears") is True


def test_cache_is_bounded():
    terminology.reset_term_pattern_cache()
    limit = terminology.term_pattern_cache_info()["max"]
    for index in range(limit + 500):
        terminology.term_matches(f"Bounded{index}", "no match here")
    info = terminology.term_pattern_cache_info()
    assert info["size"] <= limit, info
    assert info["size"] == limit, info


def test_repeated_calls_do_not_recompile():
    """同一术语重复匹配时不得重复编译（这正是本轮的优化点）。"""
    terminology.reset_term_pattern_cache()
    compiles = {"count": 0}
    real_compile = re.compile

    def counting_compile(*args, **kwargs):
        compiles["count"] += 1
        return real_compile(*args, **kwargs)

    re.compile = counting_compile
    try:
        for _ in range(50):
            for index in range(200):
                terminology.term_matches(f"Repeat{index}", f"text Repeat{index} end")
    finally:
        re.compile = real_compile
    # 200 个不同术语：只允许编译 200 次，而不是 200 × 50 次
    assert compiles["count"] <= 200 + 5, compiles


def test_cjk_terms_bypass_pattern_cache():
    terminology.reset_term_pattern_cache()
    assert terminology.term_matches("术语", "包含术语的句子") is True
    assert terminology.term_matches("術語", "包含术语的句子") is False
    assert terminology.term_pattern_cache_info()["size"] == 0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
