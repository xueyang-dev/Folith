"""#148 TM 模糊匹配：确定性评分、来源与候选排序。

验收对照（issue #148「Acceptance Criteria」）：

* 黄金集：100% / 规范化等价 / ~85% / 阈值边界（±1 字符）/ 明显不匹配，
  候选排序与分数在声明的容差内；
* 上下文冲突（与 #107 测试同构）在模糊候选上同样标注；legacy-unscoped
  旧条目不出现在候选里；
* 旧格式 ``translation_memory.json`` 无损加载，现有 exact/normalized 行为不变；
* 中→英、英→中两个语对，长段 / 短段 / 数字 / 人名差异样本；
* 回滚：``fuzzy=False``（或 ``FOLIOTHREAD_TM_FUZZY=0``）后与现状一致。

分数容差：difflib 的 ``ratio()`` 是确定性算法，但不同 Python 补丁版本之间
理论上可能有浮点差异，因此黄金集断言用 ``SCORE_TOLERANCE``（±1）。
"""
from __future__ import annotations

import difflib
import json

import pytest

import core
from folith.engine import tm_store
from folith.engine.tm_store import tm_candidate_index, tm_candidates

TARGET_ZH = "简体中文"
TARGET_EN = "英语"

#: 黄金集允许的分数偏差（声明容差；实际实现是确定性的，偏差应为 0）。
SCORE_TOLERANCE = 1

# 91 个字符的英文基准段；前缀等长替换 k 个字符后 ratio 精确等于 (91-k)/91。
GOLDEN_EN = ("The canopy closure index was recomputed after the spring flood "
             "in the upper catchment area.")
# 45 个字符的中文基准段（同样的替换构造）。
GOLDEN_ZH = ("野外调查组在春季洪水之后重新计算了林冠郁闭指数，"
             "并对照基准完成了西侧边界与北侧样带的复核。")

EN_NUMBER_OLD = ("The monitoring station documented the sediment budget against "
                 "the 2019 baseline before the winter closure.")
EN_NUMBER_NEW = ("The monitoring station documented the sediment budget against "
                 "the 2021 baseline before the winter closure.")
EN_NAME_OLD = "Dr. Wang submitted the canopy closure report after the spring flood."
EN_NAME_NEW = "Dr. Chen submitted the canopy closure report after the spring flood."
ZH_NUMBER_OLD = "监测站对照2019年基准记录了上游集水区的泥沙收支。"
ZH_NUMBER_NEW = "监测站对照2021年基准记录了上游集水区的泥沙收支。"
ZH_NAME_OLD = "王博士在春季洪水之后提交了林冠郁闭报告。"
ZH_NAME_NEW = "陈博士在春季洪水之后提交了林冠郁闭报告。"

UNMATCHED_EN = "The quarterly budget review was postponed until December."
UNMATCHED_ZH = "季度预算审议推迟到十二月进行。"


def _seed(tm, source, target, target_lang=TARGET_ZH, **extra):
    """走真实的写入路径（``core.tm_put``），不手工拼记录。"""
    return core.tm_put(tm, source, target, target_lang, **extra)


def _prefix_mutation(base: str, count: int, filler: str = "\u00ff") -> str:
    """把基准段的前 count 个字符替换成同一个字符：ratio 精确可算。

    用统一替换而不是随机噪声，是为了让黄金集的期望分数是**推导出来的**
    （``(len-count)/len``），而不是"跑一遍实现再把输出抄进断言"。
    """
    chars = list(base)
    for position in range(count):
        chars[position] = filler
    return "".join(chars)


def _candidate_for(items, source):
    return next((item for item in items if item["source"] == source), None)


def _difflib_ratio(left, right):
    return difflib.SequenceMatcher(None, left, right).ratio()


@pytest.fixture
def temp_job_env(tmp_path, monkeypatch):
    """Point ``core.OUTPUT_DIR`` at a scratch dir (same shape as #107 tests)."""
    from folith import persistence_profiler

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    try:
        yield tmp_path
    finally:
        core.reset_tm_dir_cache()
        persistence_profiler.disable()


# --------------------------------------------------------------------------
# golden set: 100% / normalized / ~85% / boundary ±1 char / clearly unmatched
# --------------------------------------------------------------------------
def test_golden_exact_and_normalized_are_100_and_rank_first():
    tm = {}
    _seed(tm, GOLDEN_EN, "林冠郁闭指数译文。")
    # 只差排版（连续空格 + 行尾空格）的同一句话：规范化等价，不是"相似"。
    _seed(tm, GOLDEN_EN.replace(" ", "  ", 1) + "  ", "林冠郁闭指数译文。")
    # 一条真正"相似但不等价"的记忆，用来证明 100 分候选必须排在它前面。
    _seed(tm, EN_NUMBER_OLD, "泥沙收支译文。")

    items = tm_candidates(tm, GOLDEN_EN, TARGET_ZH, threshold=1)
    assert [item["score"] for item in items[:2]] == [100, 100]
    assert [item["kind"] for item in items[:2]] == ["exact", "normalized"]
    assert items[0]["source"] == GOLDEN_EN
    assert tm_store.tm_normalize(items[1]["source"]) \
        == tm_store.tm_normalize(GOLDEN_EN)
    # 模糊候选永远排在可证明等价之后，且分数 <= 99。
    assert items[2:], "应存在一条相似但不等价的候选"
    assert all(item["score"] <= 99 for item in items[2:])
    assert all(item["kind"] == "fuzzy" for item in items[2:])


@pytest.mark.parametrize("base,target_lang,k,expected", [
    # (91-k)/91：k=14 -> 84.6 -> 85；k=23 -> 74.7 -> 75；k=24 -> 73.6 -> 74
    (GOLDEN_EN, TARGET_ZH, 14, 85),
    (GOLDEN_EN, TARGET_ZH, 23, 75),
    (GOLDEN_EN, TARGET_ZH, 24, 74),
    # (45-k)/45：k=7 -> 84.4 -> 84；k=11 -> 75.6 -> 76；k=12 -> 73.3 -> 73
    (GOLDEN_ZH, TARGET_EN, 7, 84),
    (GOLDEN_ZH, TARGET_EN, 11, 76),
    (GOLDEN_ZH, TARGET_EN, 12, 73),
])
def test_golden_fuzzy_scores_match_the_declared_expectation(
        base, target_lang, k, expected):
    tm = {}
    _seed(tm, base, "目标译文。", target_lang)
    query = _prefix_mutation(base, k)

    items = tm_candidates(tm, query, target_lang, threshold=1)
    candidate = _candidate_for(items, base)
    assert candidate is not None
    assert candidate["kind"] == "fuzzy"
    assert abs(candidate["score"] - expected) <= SCORE_TOLERANCE
    # 同一分数的独立参考实现（difflib 本身）也必须落在容差内：黄金集的期望
    # 值不是"实现自证"，而是 (len-k)/len 与 difflib 两条独立来源的对齐。
    reference = round(_difflib_ratio(base, query) * 100)
    assert abs(candidate["score"] - reference) <= SCORE_TOLERANCE


def test_golden_threshold_boundary_is_one_character_apart():
    """阈值边界：恰好高于阈值的一个字符之差入选，低一个字符的落选。"""
    tm = {}
    _seed(tm, GOLDEN_EN, "林冠郁闭指数译文。")

    above = _prefix_mutation(GOLDEN_EN, 23)   # 75 分：刚好过默认阈值
    below = _prefix_mutation(GOLDEN_EN, 24)   # 74 分：刚好低于默认阈值

    default_items = tm_candidates(tm, above, TARGET_ZH)
    assert _candidate_for(default_items, GOLDEN_EN)["score"] == 75
    assert tm_candidates(tm, below, TARGET_ZH) == []

    # 参数可控：把阈值降到 74，同一个 74 分样本就进来了。
    lowered = tm_candidates(tm, below, TARGET_ZH, threshold=74)
    assert _candidate_for(lowered, GOLDEN_EN)["score"] == 74
    # 抬到 76，75 分样本也退出候选（阈值是"最低分"，不是提示）。
    assert tm_candidates(tm, above, TARGET_ZH, threshold=76) == []
    # 100 = 可证明等价，不受模糊阈值影响；阈值 100 只去掉模糊候选。
    assert [item["score"] for item in
            tm_candidates(tm, GOLDEN_EN, TARGET_ZH, threshold=100)] == [100]


def test_golden_clearly_unmatched_has_no_candidates():
    en_tm, zh_tm = {}, {}
    _seed(en_tm, GOLDEN_EN, "林冠郁闭指数译文。")
    _seed(zh_tm, GOLDEN_ZH, "Target text.", TARGET_EN)

    assert tm_candidates(en_tm, UNMATCHED_EN, TARGET_ZH) == []
    assert tm_candidates(zh_tm, UNMATCHED_ZH, TARGET_EN) == []
    # 但分数本身不是 0：放宽阈值时它们会以"明显不匹配"的低分出现，
    # 这正是阈值必须由测量决定、而不是假装"模糊匹配总能命中"的原因。
    loose = tm_candidates(en_tm, UNMATCHED_EN, TARGET_ZH, threshold=1)
    assert loose and loose[0]["score"] < 50


# --------------------------------------------------------------------------
# ranking + provenance
# --------------------------------------------------------------------------
def test_candidates_are_ranked_by_score_descending_and_deterministic():
    entries = [EN_NUMBER_OLD, EN_NAME_OLD, GOLDEN_EN, EN_NUMBER_NEW]
    forward, backward = {}, {}
    for source in entries:
        _seed(forward, source, "译文。")
    for source in reversed(entries):
        _seed(backward, source, "译文。")

    items = tm_candidates(forward, EN_NUMBER_NEW, TARGET_ZH, threshold=1)
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True)
    assert items[0]["source"] == EN_NUMBER_NEW and items[0]["kind"] == "exact"
    # 插入顺序不影响排序（确定性比较，没有哈希顺序参与）。
    assert [item["key"] for item in items] == [
        item["key"] for item in tm_candidates(backward, EN_NUMBER_NEW, TARGET_ZH,
                                              threshold=1)]


def test_candidate_items_carry_scope_and_provenance():
    tm = {}
    key = _seed(tm, EN_NUMBER_OLD, "泥沙收支译文。")
    record = tm[key]
    record["source"] = "tmx_import"       # 导入来源由记录自带
    record.update(core.tm_context_fields("Previous paragraph.", "Next paragraph."))

    items = tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH,
                          previous_source="Previous paragraph.",
                          next_source="Next paragraph.", threshold=1)
    item = _candidate_for(items, EN_NUMBER_OLD)
    assert item["key"] == key
    assert item["target"] == "泥沙收支译文。"
    assert item["target_lang"] == TARGET_ZH
    assert item["reviewed"] is True
    assert item["provenance"] == "tmx_import"
    assert item["score"] == round(_difflib_ratio(EN_NUMBER_OLD, EN_NUMBER_NEW) * 100)
    assert item["context_status"] == "match"
    assert item["context_conflict"] is False
    assert item["legacy_unscoped"] is False


def test_limit_truncates_without_changing_the_order():
    tm = {}
    for source in (GOLDEN_EN, EN_NUMBER_OLD, EN_NAME_OLD):
        _seed(tm, source, "译文。")
    full = tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, threshold=1)
    assert len(full) >= 3
    assert tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, threshold=1, limit=2) \
        == full[:2]
    assert tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, limit=0) == []
    assert tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, limit=-1) == []


def test_top_k_pruning_is_exactly_the_sorted_prefix():
    """``limit`` 触发的是上界剪枝，不能改变前 N 名（含同分平局）。"""
    tm = {}
    sources = [GOLDEN_EN, EN_NUMBER_OLD, EN_NAME_OLD, ZH_NUMBER_OLD,
               "The monitoring station documented the sediment budget against "
               "the 2019 baseline before the winter closure of the northern gate."]
    for source in sources:
        _seed(tm, source, "译文。")
    reference = tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, threshold=1)
    for limit in (1, 2, 3, 5):
        assert tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, threshold=1,
                             limit=limit) == reference[:limit]


# --------------------------------------------------------------------------
# context (#107 / #108): conflicts are marked, legacy entries never appear
# --------------------------------------------------------------------------
def test_fuzzy_candidates_inherit_context_conflict_marking():
    """与 #107 的 ``tm_lookup`` 测试同构：原文相似 + 语境不同 -> 冲突标记。"""
    tm = {}
    _seed(tm, EN_NUMBER_OLD, "泥沙收支译文。",
          **core.tm_context_fields("Old previous paragraph.", "Old next paragraph."))
    query = EN_NUMBER_NEW

    matched = tm_candidates(tm, query, TARGET_ZH,
                            previous_source="Old previous paragraph.",
                            next_source="Old next paragraph.", threshold=1)
    exact_like = tm_candidates(tm, EN_NUMBER_OLD, TARGET_ZH,
                               previous_source="Old previous paragraph.",
                               next_source="Old next paragraph.")
    assert exact_like[0]["context_status"] == "match"

    conflicted = tm_candidates(tm, query, TARGET_ZH,
                               previous_source="The shop will",
                               next_source="at noon.", threshold=1)
    item = _candidate_for(conflicted, EN_NUMBER_OLD)
    assert item["context_status"] == "conflict"
    assert item["context_conflict"] is True
    assert matched  # 同一候选在语境一致时仍然给出

    # 旧条目（没有上下文指纹）是 unknown：不是冲突，但也不是 match。
    legacy_style = {}
    _seed(legacy_style, EN_NUMBER_OLD, "泥沙收支译文。")
    unknown = tm_candidates(legacy_style, query, TARGET_ZH,
                            previous_source="anything", next_source="else",
                            threshold=1)
    assert _candidate_for(unknown, EN_NUMBER_OLD)["context_status"] == "unknown"


def test_exact_candidates_also_carry_context_conflict():
    """精确命中一样要能被阻断复用（#107 的约束不能只在模糊分支生效）。"""
    tm = {}
    _seed(tm, GOLDEN_EN, "译文。", **core.tm_context_fields("Before.", "After."))
    items = tm_candidates(tm, GOLDEN_EN, TARGET_ZH,
                          previous_source="Different before.", next_source="After.")
    assert items[0]["kind"] == "exact" and items[0]["score"] == 100
    assert items[0]["context_conflict"] is True


def test_legacy_unscoped_entries_never_enter_candidates(temp_job_env):
    """#108：旧版无作用域条目保留在文件里，但永不参与候选（也不被模糊命中）。"""
    legacy_source = "The canopy closure index was recomputed by the old tool."
    core.save_tm({legacy_source: {"target": "旧工具的译文。", "reviewed": True}})
    scoped_source = GOLDEN_EN
    tm = core.load_tm(None)
    _seed(tm, scoped_source, "林冠郁闭指数译文。")
    core.save_tm(tm)

    tm = core.load_tm(None)
    index = tm_candidate_index(tm, TARGET_ZH)
    assert index.legacy_unscoped_count == 1, "旧条目仍在文件里，只是不参与"

    # 查询旧条目的原文：任何阈值下都不能把它作为候选返回。
    for threshold in (1, 50, 75, 100):
        items = tm_candidates(tm, legacy_source, TARGET_ZH, threshold=threshold)
        assert all(item["key"] != legacy_source for item in items)
        assert all(item["legacy_unscoped"] is False for item in items)
    # 只有语言可证明的新条目会被命中。
    assert all(item["legacy_unscoped"] is False
               for item in tm_candidates(tm, GOLDEN_EN, TARGET_ZH))
    # 现有行为不变：孤立旧条目仍然是 legacy_unscoped（不自动复用）。
    assert core.tm_lookup(tm, legacy_source, target_lang=TARGET_ZH)[1] \
        == "legacy_unscoped"


# --------------------------------------------------------------------------
# rollback: fuzzy off reproduces the current exact/normalized contract
# --------------------------------------------------------------------------
def test_fuzzy_off_returns_only_provable_equivalence():
    tm = {}
    _seed(tm, GOLDEN_EN, "林冠郁闭指数译文。")
    _seed(tm, GOLDEN_EN.replace(" ", "  ", 1), "林冠郁闭指数译文。")

    items = tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, fuzzy=False)
    assert items == [], "关闭模糊后，相似但不等价的段落没有任何候选"

    near_exact = tm_candidates(tm, GOLDEN_EN, TARGET_ZH, fuzzy=False)
    assert [item["score"] for item in near_exact] == [100, 100]
    assert {item["kind"] for item in near_exact} == {"exact", "normalized"}

    # 与现有读取路径逐条对齐：每个候选都能被 tm_lookup 以同样的 kind 证明。
    index = core.tm_index(tm, TARGET_ZH)
    for item in near_exact:
        hit, kind = core.tm_lookup(tm, item["source"], index, target_lang=TARGET_ZH)
        assert hit is not None and hit["target"] == item["target"]
        assert kind in ("exact", "normalized")


def test_env_switch_turns_fuzzy_off_for_unparameterised_callers(monkeypatch):
    tm = {}
    _seed(tm, GOLDEN_EN, "译文。")
    query = _prefix_mutation(GOLDEN_EN, 14)
    assert tm_candidates(tm, query, TARGET_ZH), "默认开启模糊"

    monkeypatch.setenv(tm_store.TM_FUZZY_ENV, "0")
    assert tm_store.tm_fuzzy_enabled_default() is False
    assert tm_candidates(tm, query, TARGET_ZH) == []
    # 显式传参优先于部署级开关（调用方可以按查询覆盖）。
    assert tm_candidates(tm, query, TARGET_ZH, fuzzy=True)
    monkeypatch.delenv(tm_store.TM_FUZZY_ENV)
    assert tm_candidates(tm, query, TARGET_ZH)


def test_threshold_is_configurable_and_monotone(monkeypatch):
    tm = {}
    _seed(tm, GOLDEN_EN, "译文。")
    query = _prefix_mutation(GOLDEN_EN, 14)          # 85 分

    strict = tm_candidates(tm, query, TARGET_ZH, threshold=90)
    loose = tm_candidates(tm, query, TARGET_ZH, threshold=60)
    assert strict == [], "85 分不够 90"
    assert loose and loose[0]["score"] == 85
    assert {item["score"] for item in strict} <= {item["score"] for item in loose}

    # 部署级阈值覆盖：解析失败时降级为默认值，不抛错（降级不失败）。
    monkeypatch.setenv(tm_store.TM_FUZZY_THRESHOLD_ENV, "90")
    assert tm_store.tm_fuzzy_default_threshold() == 90
    monkeypatch.setenv(tm_store.TM_FUZZY_THRESHOLD_ENV, "not-a-number")
    assert tm_store.tm_fuzzy_default_threshold() == tm_store.TM_FUZZY_DEFAULT_THRESHOLD


# --------------------------------------------------------------------------
# fail-closed scoping: no language / wrong language / not eligible
# --------------------------------------------------------------------------
def test_missing_or_wrong_target_language_never_yields_candidates():
    tm = {}
    _seed(tm, GOLDEN_EN, "译文。", TARGET_ZH)
    assert tm_candidates(tm, GOLDEN_EN, "") == []
    assert tm_candidates(tm, GOLDEN_EN, None) == []
    assert tm_candidate_index(tm, None).__len__() == 0
    # 同一原文的另一种目标语言：不得串用。
    assert tm_candidates(tm, GOLDEN_EN, TARGET_EN) == []


def test_unreviewed_and_withdrawn_records_are_not_candidates():
    source = GOLDEN_EN
    tm = {core.tm_scope_key(TARGET_ZH, source):
          {"target": "未审校译文。", "reviewed": False, "target_lang": TARGET_ZH}}
    assert tm_candidates(tm, source, TARGET_ZH) == []
    withdrawn = {}
    _seed(withdrawn, source, "撤销译文。")
    withdrawn[next(iter(withdrawn))]["reviewed"] = False
    assert tm_candidates(withdrawn, source, TARGET_ZH) == []


# --------------------------------------------------------------------------
# index reuse and parity with the existing exact index
# --------------------------------------------------------------------------
def test_prebuilt_index_matches_the_per_call_path():
    tm = {}
    for source in (GOLDEN_EN, EN_NUMBER_OLD, EN_NAME_OLD):
        _seed(tm, source, "译文。")
    query = EN_NUMBER_NEW
    ad_hoc = tm_candidates(tm, query, TARGET_ZH, threshold=1)
    indexed = tm_candidates(tm, query, TARGET_ZH, threshold=1,
                            index=tm_candidate_index(tm, TARGET_ZH))
    assert ad_hoc == indexed


def test_exact_index_is_a_subset_of_the_candidates():
    """新 API 是加法：`tm_index` 能命中的每一条，候选里都以 100 分出现。"""
    tm = {}
    for source in (GOLDEN_EN, EN_NUMBER_OLD, EN_NAME_OLD):
        _seed(tm, source, "译文。")
    exact_index = core.tm_index(tm, TARGET_ZH)
    items = tm_candidates(tm, GOLDEN_EN, TARGET_ZH, index=tm_candidate_index(
        tm, TARGET_ZH))
    by_key = {item["key"]: item for item in items}
    assert exact_index[tm_store.tm_normalize(GOLDEN_EN)] in by_key
    assert by_key[exact_index[tm_store.tm_normalize(GOLDEN_EN)]]["score"] == 100


def test_index_language_mismatch_is_explicit():
    tm = {}
    _seed(tm, GOLDEN_EN, "译文。")
    index = tm_candidate_index(tm, TARGET_EN)
    with pytest.raises(ValueError):
        tm_candidates(tm, GOLDEN_EN, TARGET_ZH, index=index)


# --------------------------------------------------------------------------
# long / short / number / name samples, both directions
# --------------------------------------------------------------------------
def test_number_and_name_differences_keep_useful_scores_both_directions():
    """数字/人名的单点差异：真实任务里最常见、也是最该被模糊命中改动的类型。"""
    tm = {}
    _seed(tm, EN_NUMBER_OLD, "泥沙收支译文。")
    _seed(tm, EN_NAME_OLD, "王博士报告译文。")
    _seed(tm, ZH_NUMBER_OLD, "sediment budget translation.", TARGET_EN)
    _seed(tm, ZH_NAME_OLD, "report translation.", TARGET_EN)

    en_items = tm_candidates(tm, EN_NUMBER_NEW, TARGET_ZH, threshold=1)
    assert _candidate_for(en_items, EN_NUMBER_OLD)["score"] == 99
    assert _candidate_for(tm_candidates(tm, EN_NAME_NEW, TARGET_ZH, threshold=1),
                          EN_NAME_OLD)["score"] == 96

    zh_items = tm_candidates(tm, ZH_NUMBER_NEW, TARGET_EN, threshold=1)
    assert _candidate_for(zh_items, ZH_NUMBER_OLD)["score"] == 96
    assert _candidate_for(tm_candidates(tm, ZH_NAME_NEW, TARGET_EN, threshold=1),
                          ZH_NAME_OLD)["score"] == 95


def test_long_segment_number_change_stays_above_threshold():
    """长段（>200 字符，difflib 的 autojunk 边界之外）也必须稳定给分。"""
    long_en = GOLDEN_EN + " " + EN_NUMBER_OLD + " " + EN_NAME_OLD
    long_en_query = long_en.replace("2019", "2024")
    zh_long = GOLDEN_ZH * 4
    assert len(long_en) > 200 and len(zh_long) > 150
    zh_long_query = zh_long.replace("洪水", "旱情")

    tm = {}
    _seed(tm, long_en, "长段译文。")
    _seed(tm, zh_long, "long segment target.", TARGET_EN)
    en_item = _candidate_for(tm_candidates(tm, long_en_query, TARGET_ZH, threshold=1),
                             long_en)
    zh_item = _candidate_for(tm_candidates(tm, zh_long_query, TARGET_EN, threshold=1),
                             zh_long)
    assert en_item is not None and en_item["score"] >= 90
    assert zh_item is not None and zh_item["score"] >= 90
    # 分数与 difflib 参考值一致（容差内）。
    assert abs(en_item["score"] - round(_difflib_ratio(long_en, long_en_query) * 100)) \
        <= SCORE_TOLERANCE


def test_short_segment_scores_are_honest_about_the_threshold():
    """短段的字符级相似度天然偏低：分数如实给出，不为凑候选而抬高。

    ``The gate is closed.`` / ``The gate is locked.`` 只有 19 个字符，一个词
    不同就是 89 分；中文 7 字短句改 1 个字只有 71 分，低于默认阈值 75。
    这不是缺陷，而是"短段需要更低阈值"的真实信号——调用方可以按场景降低
    ``threshold``，但默认值不会为了短段把长段的精确率赔进去。
    """
    tm = {}
    _seed(tm, "The gate is closed.", "大门关闭。")
    _seed(tm, "大门已经关闭。", "The gate is already closed.", TARGET_EN)

    en_item = _candidate_for(
        tm_candidates(tm, "The gate is locked.", TARGET_ZH, threshold=1),
        "The gate is closed.")
    assert en_item["score"] == 89
    assert _candidate_for(tm_candidates(tm, "The gate is locked.", TARGET_ZH),
                          "The gate is closed.") is not None

    zh_items = tm_candidates(tm, "大门已经锁上。", TARGET_EN, threshold=1)
    zh_item = _candidate_for(zh_items, "大门已经关闭。")
    assert zh_item["score"] == 71
    assert tm_candidates(tm, "大门已经锁上。", TARGET_EN) == []
    assert _candidate_for(tm_candidates(tm, "大门已经锁上。", TARGET_EN,
                                        threshold=65), "大门已经关闭。") is not None


# --------------------------------------------------------------------------
# old-format file compatibility (no loss, no migration, no behavior change)
# --------------------------------------------------------------------------
def test_legacy_unscoped_file_loads_without_loss_or_migration(temp_job_env):
    """旧格式（原文为键、无语言标注）文件：加载无损，候选不参与。"""
    legacy = {
        "The canopy closure index was recomputed by the old tool.":
            {"target": "旧工具的译文。", "reviewed": True},
    }
    path = core.tm_path(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(legacy, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")

    loaded = core.load_tm(None)
    assert loaded == legacy, "旧格式必须无损加载（不丢条目、不改字段）"
    assert path.read_text(encoding="utf-8") == payload, "读取不得改写文件"

    assert tm_candidates(loaded, "The canopy closure index was recomputed by the old tool.",
                         TARGET_ZH) == []
    assert tm_candidate_index(loaded, TARGET_ZH).legacy_unscoped_count == 1
    # 现有读取路径的诚实原因不变（#108）。
    assert core.tm_lookup(loaded, "The canopy closure index was recomputed by the old tool.",
                          target_lang=TARGET_ZH)[1] == "legacy_unscoped"


def test_pre_context_era_scoped_file_still_matches_exactly(temp_job_env):
    """#107 之前的落盘格式：有作用域键、没有上下文字段，依旧精确命中。"""
    source = GOLDEN_EN
    old_records = {
        core.tm_scope_key(TARGET_ZH, source): {
            "target": "林冠郁闭指数译文。", "reviewed": True,
            "target_lang": TARGET_ZH,
        },
    }
    path = core.tm_path(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(old_records, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    tm = core.load_tm(None)
    assert tm == old_records
    # 现有精确路径不变。
    hit, kind = core.tm_lookup(tm, source, target_lang=TARGET_ZH)
    assert hit is not None and kind == "exact"
    assert core.tm_index(tm, TARGET_ZH)[tm_store.tm_normalize(source)] \
        == core.tm_scope_key(TARGET_ZH, source)
    # 新接口只读地给出 100 分候选，上下文为 unknown（旧条目没有指纹）。
    items = tm_candidates(tm, source, TARGET_ZH,
                          previous_source="Nowhere.", next_source="Nowhere.")
    assert items[0]["score"] == 100 and items[0]["kind"] == "exact"
    assert items[0]["context_status"] == "unknown"
    assert path.read_text(encoding="utf-8") == before, "候选查询不得回写 TM 文件"
