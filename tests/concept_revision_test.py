"""#97: 概念决策后的逐段预览与受控批量提交。

一次概念决策（#96）之后：

* 预览定位所有受影响段，逐段给出 diff，标出排除项与人工保护段；
* 排除的段落一字不改；上下文例外的段用例外译名；
* 暂存走 #92 候选事务（一个事务、基线门禁），暂存本身不改正文；
* 预览到采用之间发生的编辑/重译 → 候选过期，不会写错段；
* 重复点击不产生重复写；每次结果可追溯（origin=concept_revision）。

模型是确定性 stub：这些测试只断言状态转换。
"""
from __future__ import annotations

import pytest

import core
from folith import concept_cards as cc
from folith import concept_revision as cr
from folith.entity_registry import EntityRegistry
from folith.engine import candidates as engine_candidates


def _pairs():
    return [
        {   # 已是首选译名：无行
            "source": "Kafka was born in Prague.",
            "target": "卡夫卡出生在布拉格。",
            "target_provenance": "generated",
        },
        {   # 竞争译法「Kafka 的项目」：预览行
            "source": "Kafka's project was unfinished.",
            "target": "Kafka 的项目没完成。",
            "target_provenance": "generated",
        },
        {   # 人工改写过且带竞争译法：保护段，不进批量
            "source": "He discussed Kafka's project.",
            "target": "他谈到了 Kafka 的项目。",
            "human_edited": True,
            "target_provenance": "human_edit",
        },
        {   # 竞争译法「弗朗茨·卡夫卡」：预览行
            "source": "He is the Franz Kafka of our time.",
            "target": "他是我们时代的弗朗茨·卡夫卡。",
            "target_provenance": "generated",
        },
        {   # 首选译名：无行
            "source": "Kafka studied law.",
            "target": "卡夫卡学习法律。",
            "target_provenance": "generated",
        },
        {   # 译文中出现竞争译法但原文没有该概念：禁止无上下文替换
            "source": "A different sentence entirely.",
            "target": "像 Kafka 的项目一样。",
            "target_provenance": "generated",
        },
    ]


def _state():
    registry = EntityRegistry()
    registry.observe("Kafka", "卡夫卡", segment_id=0, provenance="reviewed")
    registry.observe("Kafka’s Project", "Kafka 的项目", segment_id=1)
    registry.observe("Franz Kafka", "弗朗茨·卡夫卡", segment_id=3)
    registry.merge_variants(["Kafka", "Kafka’s Project", "Franz Kafka"],
                            note="同一概念")
    decisions: dict = {}
    cards = cc.build_concept_cards(registry.to_list(), _pairs())
    card = next(c for c in cards if any(k == "kafka" for k in c["member_keys"]))
    cc.record_concept_decision(decisions, card=card, preferred_target="卡夫卡")
    card = next(c for c in cc.build_concept_cards(registry.to_list(), _pairs(), decisions)
                if c["card_id"] == card["card_id"])
    return registry.to_list(), card, decisions


def _make_job(job_id):
    records, _card, decisions = _state()
    state = core.new_job_state("concept-revision.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in _pairs()],
        "pairs": _pairs(),
        "entity_registry": records,
        "concept_decisions": decisions,
        "findings": [],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _targets(state):
    return [pair.get("target") for pair in state.get("pairs") or []]


def _pending(state):
    return [r for r in (state.get("translation_candidates") or [])
            if r.get("status") == "pending"]


# ------------------------------------------------------------ 预览 --------

def test_preview_lists_affected_segments_with_diffs(job_env):
    records, card, decisions = _state()
    preview = cr.preview_concept_revision(card, records, _pairs(), decisions)
    assert preview["preferred_target"] == "卡夫卡"
    indexes = {row["segment_index"] for row in preview["rows"]}
    assert indexes == {1, 3}, "只有竞争译法出现的段进预览"
    row1 = next(r for r in preview["rows"] if r["segment_index"] == 1)
    assert row1["before"] == "Kafka 的项目没完成。"
    assert row1["after"] == "卡夫卡没完成。"
    assert row1["segment_uid"] or row1["segment_index"] == 1
    # 上下文可追溯：带原文与译文
    assert row1["source"].startswith("Kafka's project")


def test_excluded_occurrences_marked_and_untouched_after_commit(job_env):
    _make_job("job-x")
    state = core.load_job_state("job-x")
    records = state["entity_registry"]
    decisions = state["concept_decisions"]
    card = next(c for c in cc.build_concept_cards(records, state["pairs"], decisions)
                if any(k == "kafka" for k in c["member_keys"]))

    preview = cr.preview_concept_revision(card, records, state["pairs"], decisions,
                                          exclusions=[1])
    row1 = next(r for r in preview["rows"] if r["segment_index"] == 1)
    assert row1["excluded"] is True

    result = core.stage_concept_revision("job-x", card, exclusions=[1],
                                         note="统一译名")
    staged_indexes = {item["segment_index"] for item in result["created"]}
    assert staged_indexes == {3}, "排除段不产生候选"
    assert result["preview"]["rows"] and \
        next(r for r in result["preview"]["rows"]
             if r["segment_index"] == 1)["excluded"] is True

    ids = [item["candidate_id"] for item in result["created"]]
    engine_candidates.adopt_translation_candidates("job-x", ids)
    state = core.load_job_state("job-x")
    targets = _targets(state)
    assert targets[1] == "Kafka 的项目没完成。", "排除段一字不改"
    assert targets[3] == "他是我们时代的卡夫卡。"


def test_exception_target_applies_per_segment(job_env):
    records, card, decisions = _state()
    cc.record_concept_decision(decisions, card=card,
                               exceptions=[{"scope": "segment:3",
                                            "target": "弗兰茨·卡夫卡",
                                            "note": "修辞"}])
    preview = cr.preview_concept_revision(card, records, _pairs(), decisions)
    row3 = next(r for r in preview["rows"] if r["segment_index"] == 3)
    assert row3["exception"] is True
    assert row3["after"] == "他是我们时代的弗兰茨·卡夫卡。"
    # 其他段仍走文档级决策
    row1 = next(r for r in preview["rows"] if r["segment_index"] == 1)
    assert row1["exception"] is False


def test_keep_variants_yields_no_revision(job_env):
    records, card, decisions = _state()
    cc.record_concept_decision(decisions, card=card, keep_variants=True,
                               note="文字游戏")
    preview = cr.preview_concept_revision(card, records, _pairs(), decisions)
    assert preview["rows"] == []
    assert preview["reason"] == "keep_variants"


def test_protected_segments_are_listed_and_not_staged(job_env):
    _make_job("job-x")
    state = core.load_job_state("job-x")
    card = next(c for c in cc.build_concept_cards(state["entity_registry"],
                                                  state["pairs"],
                                                  state["concept_decisions"])
                if any(k == "kafka" for k in c["member_keys"]))
    preview = cr.preview_concept_revision(card, state["entity_registry"],
                                          state["pairs"], state["concept_decisions"])
    assert {item["segment_index"] for item in preview["protected"]} == {2}

    result = core.stage_concept_revision("job-x", card)
    assert 2 not in {item["segment_index"] for item in result["created"]}
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    state = core.load_job_state("job-x")
    assert _targets(state)[2] == "他谈到了 Kafka 的项目。", "人工改写不被批量覆盖"


def test_no_context_free_replacement(job_env):
    _make_job("job-x")
    state = core.load_job_state("job-x")
    card = next(c for c in cc.build_concept_cards(state["entity_registry"],
                                                  state["pairs"],
                                                  state["concept_decisions"])
                if any(k == "kafka" for k in c["member_keys"]))
    preview = cr.preview_concept_revision(card, state["entity_registry"],
                                          state["pairs"], state["concept_decisions"])
    assert 5 not in {row["segment_index"] for row in preview["rows"]}, \
        "原文不含概念的段绝不进批量（禁止整书正则替换）"
    result = core.stage_concept_revision("job-x", card)
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    assert _targets(core.load_job_state("job-x"))[5] == "像 Kafka 的项目一样。"


# ------------------------------------------------------------ 暂存与采用 --------

def test_staging_writes_nothing_and_is_traced(job_env):
    _make_job("job-x")
    state = core.load_job_state("job-x")
    card = next(c for c in cc.build_concept_cards(state["entity_registry"],
                                                  state["pairs"],
                                                  state["concept_decisions"])
                if any(k == "kafka" for k in c["member_keys"]))
    before = _targets(state)
    result = core.stage_concept_revision("job-x", card, note="统一译名")
    assert _targets(core.load_job_state("job-x")) == before, "暂存不改正文"
    created = result["created"]
    assert {item["segment_index"] for item in created} == {1, 3}
    assert all(item["origin"] == "concept_revision" for item in created)
    assert all(item["candidate_id"] for item in created), "每个候选可追溯"


def test_stale_segment_between_preview_and_stage_is_not_written(job_env):
    _make_job("job-x")
    state = core.load_job_state("job-x")
    card = next(c for c in cc.build_concept_cards(state["entity_registry"],
                                                  state["pairs"],
                                                  state["concept_decisions"])
                if any(k == "kafka" for k in c["member_keys"]))
    # 预览后、暂存前，第 3 段被重译成不含竞争译法的文本
    live = core.load_job_state("job-x")
    live["pairs"][3]["target"] = "重译后的句子。"
    core.save_job_state("job-x", live)

    result = core.stage_concept_revision("job-x", card)
    assert 3 not in {item["segment_index"] for item in result["created"]}, \
        "重译后的段不再匹配，绝不写入"
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    state = core.load_job_state("job-x")
    assert _targets(state)[3] == "重译后的句子。", "过期目标不被覆盖"
    assert _targets(state)[1] == "卡夫卡没完成。", "未受影响的段正常提交"


def test_repeated_staging_leaves_one_adoptable_candidate_per_segment(job_env):
    _make_job("job-x")
    card = next(c for c in cc.build_concept_cards(
        core.load_job_state("job-x")["entity_registry"],
        core.load_job_state("job-x")["pairs"],
        core.load_job_state("job-x")["concept_decisions"])
        if any(k == "kafka" for k in c["member_keys"]))
    core.stage_concept_revision("job-x", card)
    core.stage_concept_revision("job-x", card)  # 重复点击
    state = core.load_job_state("job-x")
    by_segment: dict = {}
    for record in _pending(state):
        by_segment.setdefault(record["segment_uid"], 0)
        by_segment[record["segment_uid"]] += 1
    assert all(count == 1 for count in by_segment.values()), \
        "重复暂存后每段只有一个可采用候选"

    result = core.stage_concept_revision("job-x", card)
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    state = core.load_job_state("job-x")
    assert _targets(state)[1] == "卡夫卡没完成。", "重复点击不产生重复写"


def test_adoption_commit_is_per_item_traced_and_atomic(job_env):
    _make_job("job-x")
    card = next(c for c in cc.build_concept_cards(
        core.load_job_state("job-x")["entity_registry"],
        core.load_job_state("job-x")["pairs"],
        core.load_job_state("job-x")["concept_decisions"])
        if any(k == "kafka" for k in c["member_keys"]))
    result = core.stage_concept_revision("job-x", card, note="统一译名")
    ids = [item["candidate_id"] for item in result["created"]]
    batch = engine_candidates.adopt_translation_candidates("job-x", ids,
                                                          note="确认批量")
    assert all(item.get("committed") for item in batch["results"]), \
        f"两条候选都应提交：{batch}"
    state = core.load_job_state("job-x")
    versions = [(r.get("candidate_id"), r.get("adopted_revision"))
                for r in state.get("translation_candidates") or []
                if r.get("status") == "adopted"]
    assert len(versions) == 2 and all(rev for _, rev in versions), \
        "每条候选带采用的 truth revision（可追溯）"
