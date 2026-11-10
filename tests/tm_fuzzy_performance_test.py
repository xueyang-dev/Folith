"""#148 性能基线：≥50,000 条 TM 的候选查询延迟（算法 / 机器 / 分位数）。

指标与结论记录在 ``docs/tm-fuzzy-matching-0.8.2.md``；本文件是可复现的测量
脚本兼冒烟测试：

    venv/bin/python -m pytest tests/tm_fuzzy_performance_test.py -s

断言刻意**宽松**（只拦数量级回归，不锁具体毫秒数）：CI 机器的绝对延迟与
开发机不同，脆弱的硬阈值只会变成随机红灯。真正被锁死的性质是：

* 50,000 条索引构建与查询都必须在"交互式"量级内完成（上界留了约 10 倍余量）；
* 长度分桶是**无损**的：窗口内算出的候选集合必须与全量扫描完全一致；
* 样板段（整篇同一句式，最坏情况）在 ``limit`` 下仍然可用。

两套语料，对应两种真实负载：

1. ``prose``：句式与长度都多样的普通 TM（真实文档占多数）——主要负载；
2. ``boilerplate``：整篇同一句式的样板 TM（如法规/表格类项目）——最坏情况，
   字符重集几乎相同，前几层上界剪枝剪不掉东西，只能靠 Top-K 上界。
"""
from __future__ import annotations

import bisect
import random
import time

from folith.engine import tm_store

ENTRY_COUNT = 50_000
TARGET_LANG = "简体中文"
SEED = 148

#: 词表：真实词 + 由固定种子生成、长度 4–12 的确定性伪词，合计 512 个。
#: 为什么不用"前缀+后缀"拼词：那样每个词共享字符，字符重集上界会接近 1，
#: 测出来的是样板段负载而不是普通 TM。词形要像真实英语那样彼此差异大。
_FUNCTION_WORDS = (
    "the of and to in for before after during against with without under over "
    "was were is are has have had been being recorded documented validated "
    "archived revisited recalibrated inspected flagged summarised reconciled "
    "team station unit crew office lab panel group survey report sample series "
    "index budget baseline protocol schedule inventory threshold estimate plan "
    "northern southern western eastern upper lower regional local seasonal "
    "spring winter summer autumn flood drought closure opening maintenance"
).split()


def _pseudo_words(count: int):
    """确定性伪词：固定种子，避免测试语料随 Python 版本变化。"""
    rng = random.Random(20261025)
    letters = "abcdefghijklmnopqrstuvwxyz"
    words = []
    while len(words) < count:
        length = rng.randint(4, 12)
        word = "".join(rng.choice(letters) for _ in range(length))
        if word not in words:
            words.append(word)
    return words


_VOCABULARY = (list(_FUNCTION_WORDS) + _pseudo_words(512))[:512]


def _prose_sentence(rng: random.Random, serial: int) -> str:
    """长度按对数正态分布取样（对齐真实文档段落：p5≈19 / p50≈49 / p95≈83）。"""
    word_count = max(4, min(34, int(rng.lognormvariate(3.3, 0.55))))
    words = [rng.choice(_VOCABULARY) for _ in range(word_count)]
    words[0] = words[0].capitalize()
    return " ".join(words) + f" ({serial})."


def _boilerplate_sentence(rng: random.Random, serial: int) -> str:
    return ("The monitoring station documented the sediment budget against the "
            f"2019 baseline before the winter closure ({serial}).")


def _build_tm(count: int, maker) -> dict:
    rng = random.Random(SEED)
    tm = {}
    for serial in range(count):
        source = maker(rng, serial)
        # 直接写记录：性能测试只关心读取路径，不经过审校/落盘事务。
        tm[f"{TARGET_LANG}\u241f{source}"] = {
            "target": f"第 {serial} 段译文。",
            "reviewed": True,
            "target_lang": TARGET_LANG,
        }
    return tm


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _time_queries(tm, index, queries, *, limit=None):
    latencies = []
    best_scores = []
    for query in queries:
        started = time.perf_counter()
        items = tm_store.tm_candidates(tm, query, TARGET_LANG, index=index,
                                       limit=limit)
        latencies.append((time.perf_counter() - started) * 1000)
        best_scores.append(items[0]["score"] if items else 0)
    return latencies, best_scores


def _distinct_queries(index, count, rng, mutate):
    queries = []
    for source in rng.sample(list(index.sources), count):
        candidate = mutate(source, rng)
        if candidate != source:
            queries.append(candidate)
    return queries


def _mutate_one_word(source, rng):
    words = source.split(" ")
    positions = [position for position, word in enumerate(words)
                 if word.strip("().").isalnum() and len(word) > 4]
    if not positions:
        return source
    position = rng.choice(positions)
    words[position] = "revised"
    return " ".join(words)


def _mutate_baseline_number(source, _rng):
    return source.replace("2019", "2024")


def test_fifty_thousand_entry_candidate_latency():
    started = time.perf_counter()
    tm = _build_tm(ENTRY_COUNT, _prose_sentence)
    build_tm_ms = (time.perf_counter() - started) * 1000

    started = time.perf_counter()
    index = tm_store.tm_candidate_index(tm, TARGET_LANG)
    build_index_ms = (time.perf_counter() - started) * 1000
    assert len(index) == ENTRY_COUNT, "索引必须收录全部可命中的条目"

    rng = random.Random(SEED + 1)
    queries = _distinct_queries(index, 200, rng, _mutate_one_word)

    latencies, best_scores = _time_queries(tm, index, queries)
    p50, p95, p99 = (_percentile(latencies, 0.5), _percentile(latencies, 0.95),
                     _percentile(latencies, 0.99))
    print(
        "\n#148 fuzzy candidate baseline (prose corpus, limit=None)"
        f"\n  entries={len(index)}  tm_build={build_tm_ms:.0f} ms"
        f"  index_build={build_index_ms:.0f} ms"
        f"\n  query p50={p50:.1f} ms  p95={p95:.1f} ms  p99={p99:.1f} ms"
        f"  max={max(latencies):.1f} ms  mean={sum(latencies) / len(latencies):.1f} ms"
        f"\n  best score: p50={_percentile(best_scores, 0.5)}"
        f"  p95={_percentile(best_scores, 0.95)}  min={min(best_scores)}")

    # 宽松上界：只拦数量级回归（实测值见文档，留有约 10 倍余量）。
    assert p95 < 1500, f"p95 {p95:.1f} ms 超出交互式上界"
    assert build_index_ms < 20_000, f"索引构建 {build_index_ms:.0f} ms 过慢"
    # 相似原文确实命中（否则"快"没有意义）。
    assert _percentile(best_scores, 0.05) >= tm_store.TM_FUZZY_DEFAULT_THRESHOLD


def test_boilerplate_corpus_stays_usable_with_limit():
    """最坏情况：整编样板段。前几层上界剪枝无效，Top-K 上界兜底。"""
    tm = _build_tm(ENTRY_COUNT, _boilerplate_sentence)
    index = tm_store.tm_candidate_index(tm, TARGET_LANG)
    rng = random.Random(SEED + 2)
    queries = [f"The monitoring station documented the sediment budget against "
               f"the 2024 baseline before the winter closure ({i})."
               for i in rng.sample(range(ENTRY_COUNT), 20)]

    latencies, best_scores = _time_queries(tm, index, queries, limit=5)
    truncated = any(item.get("scan_truncated")
                    for item in tm_store.tm_candidates(
                        tm, queries[0], TARGET_LANG, index=index, limit=5))
    p50, p95 = _percentile(latencies, 0.5), _percentile(latencies, 0.95)
    print("\n#148 boilerplate corpus (worst case, limit=5)"
          f"\n  query p50={p50:.1f} ms  p95={p95:.1f} ms"
          f"  max={max(latencies):.1f} ms"
          f"\n  best score min={min(best_scores)}"
          f"  truncated={truncated}")
    assert min(best_scores) >= tm_store.TM_FUZZY_DEFAULT_THRESHOLD
    # 预算耗尽时必须如实标注"部分检索"，否则就是静默截断。
    assert truncated is True
    assert p95 < 3000, f"样板段 p95 {p95:.1f} ms 超出可接受上界"


def test_length_bucketing_is_lossless():
    """长度分桶只能剪掉"注定过不了线"的条目，不能改变任何入选分数。"""
    tm = _build_tm(5_000, _prose_sentence)
    index = tm_store.tm_candidate_index(tm, TARGET_LANG)
    query = index.sources[1234].replace("2019", "2024")
    normalized = tm_store.tm_normalize(query)
    threshold = tm_store.TM_FUZZY_DEFAULT_THRESHOLD

    window = tm_store._tm_fuzzy_length_window(len(normalized), threshold)
    low, high = window
    # 预算放大到不可能触发，验证的是"窗口/上界剪枝无损"这个性质本身。
    generous = {"max_scan": 10**9, "max_evals": 10**9}
    windowed, truncated_window = tm_store._tm_fuzzy_scores(
        index, normalized, threshold, window=window, **generous)
    exhaustive, truncated_all = tm_store._tm_fuzzy_scores(
        index, normalized, threshold, window=None, **generous)
    assert truncated_window is False and truncated_all is False
    assert windowed == exhaustive, "窗口扫描与全量扫描必须给出同一批候选分数"

    # Top-K 上界剪枝同样不能改变前 K 名。
    reference = sorted(exhaustive, key=lambda item: (-item[0], item[1]))[:5]
    pruned, _truncated = tm_store._tm_fuzzy_scores(
        index, normalized, threshold, window=window, best_count=5, **generous)
    assert sorted(pruned, key=lambda item: (-item[0], item[1]))[:5] == reference

    # 工作预算确实会生效，并且如实报告"部分检索"而不是静默截断。
    budgeted, truncated = tm_store._tm_fuzzy_scores(
        index, normalized, threshold, window=window, max_scan=50, max_evals=5)
    assert truncated is True
    assert len(budgeted) <= 5

    # 而且窗口确实剪掉了大部分条目（否则"索引"只是装饰）。
    scanned = (bisect.bisect_right(index.sorted_lengths, high)
               - bisect.bisect_left(index.sorted_lengths, low))
    assert 0 < scanned < len(index), (scanned, len(index))
    print(f"\n  window [{low}, {high}] scans {scanned}/{len(index)} entries")
