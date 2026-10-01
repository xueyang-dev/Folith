"""`select_target_context` 与 `recent_target_context` 的差分等价测试。

背景（Performance Phase 5 setup 审计）：
`_process_batch_payload` 里原来的调用是

    select_target_context(pairs, offset, limit=max(2, offset))[-2:]

`limit >= offset >= 候选数`，所以层级优先循环**不会截断**，该表达式恒等于
"offset 之前最近 2 个有译文的段落"。但实现上它要 `list(pairs)[:offset]` 并给每个
历史段落建 dict —— 每批 O(offset)、累计 O(n²)（20k 段实测占 setup 65%）。

`recent_target_context` 是这条等价的专用快路径。本文件用**逐例差分**证明等价，
而不是靠推理：对随机生成的大量 (pairs, offset, blockers) 组合，与原实现输出恒等。
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from folith import context


# --------------------------------------------------------------------------- #
# 原实现的逐字副本：证明"共享函数没有被静默改掉"，同时充当差分的参照方
# --------------------------------------------------------------------------- #
def _reference_select_target_context(pairs, before_index, limit=2):
    candidates = []
    for index, pair in enumerate(list(pairs)[:max(0, before_index)]):
        target = pair.get("accepted_target") or pair.get("target")
        if target:
            candidates.append({
                "segment_index": index,
                "source": context._clean(pair.get("source"), 800),
                "target": context._clean(target, 1200),
                "level": context.target_context_level(pair),
            })
    selected = []
    for level in context.TARGET_CONTEXT_LEVELS:
        selected.extend(item for item in reversed(candidates) if item["level"] == level)
        if len(selected) >= max(0, limit):
            break
    return sorted(selected[:max(0, limit)], key=lambda item: item["segment_index"])


def _legacy_call_site(pairs, offset, blocked):
    """原调用点的完整语义：全量选择 -> 过滤 blocked -> 取末 2 个。"""
    return [
        item for item in _reference_select_target_context(
            pairs, offset, limit=max(2, offset))
        if item.get("segment_index") not in blocked
    ][-2:]


# --------------------------------------------------------------------------- #
# 随机夹具
# --------------------------------------------------------------------------- #
LEVEL_SHAPES = (
    {"target_provenance": "reviewed", "reviewed": True},
    {"target_provenance": "tm_approved", "reviewed": True, "from_tm": True},
    {"human_accepted": True, "reviewed": True},
    {"target_provenance": "generated", "reviewed": False},
)


def _random_pairs(rng: random.Random, size: int):
    pairs = []
    for index in range(size):
        shape = dict(rng.choice(LEVEL_SHAPES))
        pair = {"source": f"source {index}", **shape}
        roll = rng.random()
        if roll < 0.12:
            pair["target"] = ""            # 无译文：不是候选
        elif roll < 0.18:
            pair.pop("target", None)
        else:
            pair["target"] = f"译文{index}"
            if rng.random() < 0.5:
                pair["accepted_target"] = f"已接受{index}"
        pairs.append(pair)
    return pairs


def test_shared_function_still_matches_reference():
    """共享函数本身没有被改动（差分基线）。

    `select_target_context` 在 Phase 5 被重写为"反向扫描 + 每层只留最新 limit
    条"以免物化前缀，所以这条差分基线从"证明没被动过"变成"证明改写是透明的"：
    规模、offset 越界、limit 取值（含 0 / 大于 size）都要覆盖。
    """
    rng = random.Random(20260926)
    for _ in range(2000):
        size = rng.randint(0, 120)
        pairs = _random_pairs(rng, size)
        offset = rng.randint(-2, size + 3)
        limit = rng.choice([0, 1, 2, 3, 5, max(2, offset), size + 1])
        assert context.select_target_context(pairs, offset, limit) == \
            _reference_select_target_context(pairs, offset, limit), \
            (pairs, offset, limit)


def test_shared_function_does_not_materialise_the_prefix():
    """改写后 `_clean` 只对入选段落执行 —— 不得为每个历史段落建 dict。"""
    calls = []
    original_clean = context._clean

    def counting_clean(value, size):
        calls.append(size)
        return original_clean(value, size)

    context._clean = counting_clean
    try:
        pairs = _random_pairs(random.Random(31), 4000)
        result = context.select_target_context(pairs, 4000, limit=2)
    finally:
        context._clean = original_clean
    assert len(result) <= 8
    # 每入选一段两次 `_clean`（source + target），上限 = limit × 层数 × 2。
    assert len(calls) <= 2 * 2 * len(context.TARGET_CONTEXT_LEVELS), \
        f"对 {len(pairs)} 段历史调用了 {len(calls)} 次 _clean"


def test_early_exit_only_applies_to_the_top_priority_level():
    """非最高优先层**不得**提前退出：更早的同层段落会改写结果。

    只有 index 3000 是 `reviewed`，3001/3002 是 `generated`。limit=2 时正确的
    答案是 [3000(reviewed), 3002(generated)]——`reviewed` 层只有 1 条、填不满，
    于是 `generated` 层按"最新优先"补 1 条（3002，不是 3001）。若扫描一凑满 2 条
    就停，会错答成 [3001, 3002] 并丢掉更高优先的 3000。
    """
    pairs = [{"source": f"s{i}", "target": f"t{i}",
              "target_provenance": "generated", "reviewed": False}
             for i in range(3003)]
    pairs[3000] = {"source": "s3000", "target": "t3000",
                   "target_provenance": "reviewed", "reviewed": True}
    got = context.select_target_context(pairs, 3003, limit=2)
    assert [item["segment_index"] for item in got] == [3000, 3002]
    assert [item["level"] for item in got] == ["reviewed", "generated"]
    assert got == _reference_select_target_context(pairs, 3003, limit=2)


def test_top_priority_level_stops_the_scan_early():
    """最高优先层凑满 limit 条即可停扫（唯一的合法提前退出）。"""
    class CountingPairs(list):
        def __init__(self, items):
            super().__init__(items)
            self.reads = 0

        def __getitem__(self, item):
            self.reads += 1
            return super().__getitem__(item)

    pairs = [{"source": f"s{i}", "target": f"t{i}", "human_accepted": True,
              "reviewed": True} for i in range(5000)]
    pairs[0] = {"source": "s0", "target": "t0",
                "target_provenance": "generated", "reviewed": False}
    proxy = CountingPairs(pairs)
    got = context.select_target_context(proxy, 5000, limit=2)
    assert [item["segment_index"] for item in got] == [4998, 4999]
    assert proxy.reads < 50, f"扫了 {proxy.reads} 个元素，最高优先层没有提前退出"


def test_recent_target_context_equals_filtered_tail():
    """快路径 == `select_target_context(limit=max(2, offset))[:-2] 前过滤`。"""
    rng = random.Random(20260927)
    for _ in range(800):
        size = rng.randint(0, 60)
        pairs = _random_pairs(rng, size)
        offset = rng.randint(0, size + 3)
        blocked = {index for index in range(size + 3) if rng.random() < 0.2}
        got = context.recent_target_context(pairs, offset, 2, exclude_indexes=blocked)
        want = _legacy_call_site(pairs, offset, blocked)
        assert got == want, (pairs, offset, blocked)


def test_recent_target_context_level_metadata_matches():
    """level / source / target 三个字段都必须逐字一致（不只 segment_index）。"""
    rng = random.Random(20260928)
    for _ in range(400):
        pairs = _random_pairs(rng, rng.randint(0, 30))
        offset = rng.randint(0, len(pairs) + 2)
        got = context.recent_target_context(pairs, offset, 2)
        want = _legacy_call_site(pairs, offset, set())
        assert [item["level"] for item in got] == [item["level"] for item in want]
        assert [item["source"] for item in got] == [item["source"] for item in want]
        assert [item["target"] for item in got] == [item["target"] for item in want]


def test_recent_target_context_scans_only_the_tail():
    """扫描量必须有界：快路径不得触碰整段历史。"""
    class CountingPairs(list):
        def __init__(self, items):
            super().__init__(items)
            self.reads = 0

        def __getitem__(self, item):
            if isinstance(item, slice):
                self.reads += len(range(*item.indices(len(self))))
            else:
                self.reads += 1
            return super().__getitem__(item)

    pairs = CountingPairs(_random_pairs(random.Random(7), 5000))
    result = context.recent_target_context(pairs, 5000, 2)
    assert len(result) == 2
    assert pairs.reads <= 8, f"快路径读了 {pairs.reads} 个元素，不再是尾部扫描"


def test_recent_target_context_edge_cases():
    assert context.recent_target_context([], 0) == []
    assert context.recent_target_context([{"source": "a", "target": "甲"}], 0) == []
    assert context.recent_target_context([{"source": "a", "target": "甲"}], 1) == [
        {"segment_index": 0, "source": "a", "target": "甲", "level": "generated"}]
    # offset 超过实际长度时按长度截断，不越界
    pairs = [{"source": "a", "target": "甲"}, {"source": "b", "target": "乙"}]
    assert [i["segment_index"] for i in
            context.recent_target_context(pairs, 99, 2)] == [0, 1]
    # 全部被 blocked 时返回空，而不是回退到更早的段落
    assert context.recent_target_context(pairs, 2, 2, exclude_indexes={0, 1}) == []
    # 无译文的段落不是候选
    assert context.recent_target_context(
        [{"source": "a", "target": "甲"}, {"source": "b"}, {"source": "c", "target": ""}],
        3, 2) == [{"segment_index": 0, "source": "a", "target": "甲",
                   "level": "generated"}]


def test_recent_target_context_respects_level_metadata():
    pairs = [
        {"source": "s0", "target": "t0", "reviewed": True,
         "target_provenance": "reviewed"},
        {"source": "s1", "target": "t1", "human_accepted": True, "reviewed": True},
        {"source": "s2", "target": "t2", "from_tm": True, "reviewed": True,
         "target_provenance": "tm_approved"},
    ]
    got = context.recent_target_context(pairs, 3, 2)
    assert [item["level"] for item in got] == ["human_accepted", "tm_approved"]
    assert [item["segment_index"] for item in got] == [1, 2]


def test_review_context_keeps_level_priority_contract():
    """Phase 7 §5：审校侧必须继续走 `select_target_context` 的层级优先语义。

    审校调用点传的是 `limit=2`，**会真的截断**，所以生成侧快路径
    `recent_target_context` 在这里不等价——它取"offset 之前最近 count 个有译文的
    段落"，完全不看 provenance 层级。这条断言锁住两类回归：
    ①有人把审校侧"顺手"换成快路径；②有人把快路径的等价位条件（limit 不参与截断）
    改坏后仍宣称等价。
    """
    import core

    # 夹具刻意做成"层级顺序**与位置顺序相反**"：唯一一条 human_accepted 在最老的位置，
    # 最近两条都是有译文但未审校的 generated。生成侧快路径会取最近两条（generated），
    # 层级优先选择器会先取 human_accepted 再补一条 generated —— 两者真的不同。
    pairs = [
        {"segment_id": 0, "source": "s0", "target": "t0", "reviewed": True,
         "human_accepted": True},                                           # human_accepted
        {"segment_id": 1, "source": "s1", "target": "t1"},                  # generated
        {"segment_id": 2, "source": "s2", "target": "t2"},                  # generated
        {"segment_id": 3, "source": "s3", "target": ""},
        {"segment_id": 4, "source": "s4", "target": ""},
        {"segment_id": 5, "source": "s5", "target": ""},
    ]
    state = {
        "pairs": pairs,
        "paras": [pair["source"] for pair in pairs],
        "document_profile": {},
        "section_digests": [],
        "document_synopsis": {},
    }

    calls = {"select": [], "recent": []}
    real_select = context.select_target_context
    real_recent = context.recent_target_context

    def spy_select(pairs_arg, before_index, limit=2):
        calls["select"].append((before_index, limit))
        return real_select(pairs_arg, before_index, limit)

    def spy_recent(pairs_arg, before_index, count=2, exclude_indexes=None):
        calls["recent"].append((before_index, count))
        return real_recent(pairs_arg, before_index, count, exclude_indexes)

    context.select_target_context = spy_select
    context.recent_target_context = spy_recent
    try:
        ctx = core._runtime_review_context(state, 3, 3, "", "", "简体中文")
    finally:
        context.select_target_context = real_select
        context.recent_target_context = real_recent

    # ① 走的是层级优先选择器，且截断参数原样保留
    assert calls["select"] == [(3, 2)], calls
    assert calls["recent"] == [], "审校侧回退到了生成侧快路径"

    # ② 层级优先：human_accepted 先占一个名额，再补最近的一条 generated；
    #    随后 caller 过滤掉 generated，于是只剩那条 human_accepted。
    assert [item["segment_index"] for item in ctx["previous_target_context"]] == [0]
    assert [item["level"] for item in ctx["previous_target_context"]] == [
        "human_accepted"]

    # ③ 守卫非空转：快路径只看位置，取到的是两条 generated，
    #    经 caller 过滤后**一条都不剩**——正是"退化为 provisional/generated"的形态。
    fast = real_recent(pairs, 3, 2)
    assert [item["segment_index"] for item in fast] == [1, 2]
    assert [item["level"] for item in fast] == ["generated", "generated"]
    assert [item for item in fast
            if item["level"] in {"human_accepted", "reviewed", "tm_approved"}] == []


def test_review_context_filters_provisional_and_generated_levels():
    """Phase 7 §5：审校 context 只接受已审校/已确认的来源，不出现 generated。"""
    import core

    pairs = [
        {"segment_id": 0, "source": "s0", "target": "t0"},                  # generated
        {"segment_id": 1, "source": "s1", "target": "t1"},
        {"segment_id": 2, "source": "s2", "target": "t2", "from_tm": True,
         "reviewed": True, "target_provenance": "tm_approved"},             # tm_approved
        {"segment_id": 3, "source": "s3", "target": ""},
    ]
    state = {
        "pairs": pairs,
        "paras": [pair["source"] for pair in pairs],
        "document_profile": {},
        "section_digests": [],
        "document_synopsis": {},
    }
    ctx = core._runtime_review_context(state, 3, 1, "", "", "简体中文")
    levels = {item["level"] for item in ctx["previous_target_context"]}
    assert levels <= {"human_accepted", "reviewed", "tm_approved"}, levels
    assert "generated" not in levels


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ✓ {name}")
