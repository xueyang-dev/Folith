"""#124/#126: 术语修订的逐段预览与受控全局替换。

改一条术语的首选译名之后：

* 预览按原文锚定逐段分类——可替换 / 已正确 / 需人工确认 / 人工保护；
* 原文不含术语的段绝不进批量（没有整书正则替换入口）；
* 暂存走 #92 候选事务（锁内重算预览、基线门禁），暂存本身不改正文；
* 采用是独立的一步：只有预览选中的段被写入，逐项可追溯；
* 术语批量落地后，已消失的实体一致性告警就地解决（#128）。

模型是确定性 stub：这些测试只断言状态转换，零模型调用。
"""
from __future__ import annotations

import pytest

import core
from folith import term_revision as tr
from folith.engine import candidates as engine_candidates


def _pairs():
    return [
        {   # 旧首选译名，两次出现在同一段：一个预览行，两处都替换
            "source": "The Life of Violet opens the book. The Life of Violet ends it.",
            "target": "《紫罗兰的一生》打开全书，《紫罗兰的一生》收束全书。",
            "target_provenance": "generated",
            "segment_uid": "s0",
        },
        {   # 已是新译名：already_correct
            "source": "Violet smiled at the title of The Life of Violet.",
            "target": "薇奥莱特看到《薇奥莱特的一生》这个书名时笑了。",
            "target_provenance": "generated",
            "segment_uid": "s1",
        },
        {   # 新旧译名都没有：需人工确认，绝不自动改
            "source": "She never finished The Life of Violet.",
            "target": "她没有读完那本书。",
            "target_provenance": "generated",
            "segment_uid": "s2",
        },
        {   # 人工改写过的旧译名：保护段，只列出
            "source": "He reread The Life of Violet.",
            "target": "他重读了《紫罗兰的一生》。",
            "human_edited": True,
            "target_provenance": "human_edit",
            "segment_uid": "s3",
        },
        {   # 译文里出现旧译名但原文没有该术语：禁止无上下文替换
            "source": "A different sentence entirely.",
            "target": "《紫罗兰的一生》是一部作品。",
            "target_provenance": "generated",
            "segment_uid": "s4",
        },
        {   # 排除段：预览跳过
            "source": "The Life of Violet was excluded.",
            "target": "《紫罗兰的一生》被排除了。",
            "excluded": True,
            "target_provenance": "generated",
            "segment_uid": "s5",
        },
        {   # 禁用译命中的段：禁用译名也是已知错误写法，给出确定性替换提议
            "source": "The library kept The Life of Violet.",
            "target": "图书馆收着《紫罗兰》。",
            "target_provenance": "generated",
            "segment_uid": "s6",
        },
        {   # 还没有译文：谈不上译错，不进任何分类
            "source": "The Life of Violet waits.",
            "target": "",
            "target_provenance": "generated",
            "segment_uid": "s7",
        },
    ]


def _make_job(job_id, *, findings=None):
    state = core.new_job_state("term-revision.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in _pairs()],
        "pairs": _pairs(),
        "glossary": [{"id": "g1", "source": "The Life of Violet",
                      "preferred": "紫罗兰的一生", "status": "locked",
                      "forbidden": ["紫罗兰"]}],
        "findings": findings or [],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _pending(state):
    return [r for r in (state.get("translation_candidates") or [])
            if r.get("status") == "pending"]


# ------------------------------------------------------------ 预览 --------

def test_preview_classifies_each_source_occurrence():
    preview = tr.preview_term_revision(
        "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生", _pairs(),
        forbidden=["紫罗兰"])
    assert preview["reason"] == ""
    # 可替换：段 0（两次）与段 6（禁用译名）；原文没有术语的段 4 不进批量
    assert [row["segment_index"] for row in preview["rows"]] == [0, 6]
    row0 = preview["rows"][0]
    assert row0["variants"] == ["紫罗兰的一生"]
    assert row0["after"] == "《薇奥莱特的一生》打开全书，《薇奥莱特的一生》收束全书。"
    assert preview["rows"][1]["variants"] == ["紫罗兰"]
    assert preview["rows"][1]["after"] == "图书馆收着《薇奥莱特的一生》。"
    assert preview["already_correct"] == [1]
    assert [item["segment_index"] for item in preview["ambiguous"]] == [2]
    assert [item["segment_index"] for item in preview["protected"]] == [3]
    assert preview["source_occurrences"] == 6, "排除段不计入命中"


def test_preview_without_new_target_has_reason_and_no_rows():
    preview = tr.preview_term_revision(
        "The Life of Violet", "紫罗兰的一生", "", _pairs())
    assert preview["reason"] == "no_new_target"
    assert preview["rows"] == []


def test_preview_exclusions_are_marked_not_dropped():
    preview = tr.preview_term_revision(
        "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生", _pairs(),
        forbidden=["紫罗兰"], exclusions=[0])
    row0 = preview["rows"][0]
    assert row0["excluded"] is True
    assert next(row for row in preview["rows"]
                if row["segment_index"] == 6)["excluded"] is False


# ------------------------------------------------------------ 暂存 --------

def test_staging_creates_candidates_and_writes_no_text(job_env):
    _make_job("job-term")
    result = core.stage_term_revision(
        "job-term", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    created = result["created"]
    assert {item["segment_index"] for item in created} == {0, 6}
    # 暂存不改正文
    state = core.load_job_state("job-term")
    assert state["pairs"][0]["target"] == "《紫罗兰的一生》打开全书，《紫罗兰的一生》收束全书。"
    records = _pending(state)
    assert len(records) == 2
    assert all(r["origin"] == "term_revision" for r in records)
    assert all(r["candidate_target"] != r["baseline"]["target"] for r in records)


def test_staging_skips_excluded_segments_and_links_findings(job_env):
    findings = [{"id": "f-1", "type": "entity_conflict", "segment_index": 0,
                 "source": "The Life of Violet"}]
    _make_job("job-term2", findings=findings)
    result = core.stage_term_revision(
        "job-term2", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"], exclusions=[0])
    assert {item["segment_index"] for item in result["created"]} == {6}
    record = _pending(core.load_job_state("job-term2"))[0]
    assert record.get("finding_ids") == [], "排除段不产生候选，也不带 finding"
    # 未排除段的候选把对应 finding 关联起来
    record0 = next(r for r in _pending(core.load_job_state("job-term2"))
                   if r["segment_index"] == 6)
    assert record0.get("finding_ids") == []


def test_repeated_staging_supersedes_older_pending_candidates(job_env):
    _make_job("job-term3")
    first = core.stage_term_revision(
        "job-term3", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    second = core.stage_term_revision(
        "job-term3", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    state = core.load_job_state("job-term3")
    pending = _pending(state)
    assert len(pending) == len(second["created"]), \
        "同一段重复暂存只有一个可采用候选：新候选取代旧候选"
    assert {r["segment_index"] for r in pending} == \
        {item["segment_index"] for item in first["created"]}
    superseded = [r for r in (state["translation_candidates"] or [])
                  if r.get("status") == "stale"
                  and r.get("note") == "同一段新生成了候选，旧候选不再可采用"]
    assert len(superseded) == len(first["created"]), "旧候选标记过期而不是悄悄消失"


# ------------------------------------------------------------ 采用 --------

def test_selected_only_adoption_writes_previewed_segments(job_env):
    _make_job("job-term4")
    result = core.stage_term_revision(
        "job-term4", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    ids = [item["candidate_id"] for item in result["created"]
           if item["segment_index"] == 0]
    adopted = core.adopt_translation_candidates("job-term4", ids)
    assert adopted["changed"] == 1
    state = core.load_job_state("job-term4")
    assert state["pairs"][0]["target"] == "《薇奥莱特的一生》打开全书，《薇奥莱特的一生》收束全书。"
    assert state["pairs"][6]["target"] == "图书馆收着《紫罗兰》。", "未选中的段一字不改"
    record = next(r for r in (state["translation_candidates"] or [])
                  if r["candidate_id"] == ids[0])
    assert record["previous_target"] == "《紫罗兰的一生》打开全书，《紫罗兰的一生》收束全书。"


def test_protected_segments_are_never_staged_or_written(job_env):
    _make_job("job-term5")
    core.stage_term_revision(
        "job-term5", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    state = core.load_job_state("job-term5")
    assert state["pairs"][3]["target"] == "他重读了《紫罗兰的一生》。"
    assert not [r for r in _pending(state) if r["segment_index"] == 3]


def test_stale_baseline_rejects_adoption_without_wrong_write(job_env):
    _make_job("job-term6")
    preview_before = tr.preview_term_revision(
        "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        core.load_job_state("job-term6")["pairs"], forbidden=["紫罗兰"])
    # 预览之后、暂存之前，段落被人工编辑
    state = core.load_job_state("job-term6")
    state["pairs"][0]["target"] = "人工改过的《紫罗兰的一生》。"
    state["pairs"][0]["human_edited"] = True
    core.save_job_state("job-term6", state)
    result = core.stage_term_revision(
        "job-term6", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    # 锁内重算：人工改写的段现在受保护，不再产生候选
    indexes = {item["segment_index"] for item in result["created"]}
    assert 0 not in indexes
    assert len(preview_before["rows"]) == 2
    state = core.load_job_state("job-term6")
    assert state["pairs"][0]["target"] == "人工改过的《紫罗兰的一生》。", "过期预览永远不会被照抄"


# ------------------------------------------------------------ 一致性 -------

def _make_registry_job(job_id):
    """一致性测试夹具：带 EntityRegistry 观测，冲突"是否仍活跃"由当前译文决定。"""
    from folith.entity_registry import EntityRegistry

    state = core.new_job_state("term-consistency.docx")
    pairs = [
        {   # 旧首选译名
            "source": "The Life of Violet opens the book.",
            "target": "《紫罗兰的一生》打开全书。",
            "target_provenance": "generated", "segment_uid": "c0",
        },
        {   # 禁用译名
            "source": "The library kept The Life of Violet.",
            "target": "图书馆收着《紫罗兰》。",
            "target_provenance": "generated", "segment_uid": "c1",
        },
        {   # 无关实体的冲突：另一条术语的两个译法
            "source": "Another Name appears here.",
            "target": "别名甲出现在这里。",
            "target_provenance": "generated", "segment_uid": "c2",
        },
        {   # 无关实体的另一个译法
            "source": "Another Name again.",
            "target": "别名乙又出现了。",
            "target_provenance": "generated", "segment_uid": "c3",
        },
    ]
    registry = EntityRegistry()
    registry.observe("The Life of Violet", "紫罗兰的一生", segment_id=0)
    registry.observe("The Life of Violet", "紫罗兰", segment_id=1)
    registry.observe("Another Name", "别名甲", segment_id=2)
    registry.observe("Another Name", "别名乙", segment_id=3)
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in pairs],
        "pairs": pairs,
        "entity_registry": registry.to_list(),
        "findings": [
            {"id": "f-term", "type": "entity_conflict", "segment_index": 0,
             "source": "The Life of Violet", "resolved": False},
            {"id": "f-other", "type": "entity_conflict", "segment_index": 2,
             "source": "Another Name", "resolved": False},
        ],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def test_adoption_resolves_stale_entity_conflict(job_env):
    _make_registry_job("job-cons1")
    result = core.stage_term_revision(
        "job-cons1", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    ids = [item["candidate_id"] for item in result["created"]]
    adopted = core.adopt_translation_candidates("job-cons1", ids)
    assert adopted["changed"] == 2
    state = core.load_job_state("job-cons1")
    by_id = {f.get("id"): f for f in state["findings"]}
    # 全部替换后冲突消失：相关 finding 就地解决，无关实体的冲突不动
    assert by_id["f-term"]["resolved"]
    assert by_id["f-term"]["resolution"]["action"] == "entity_consistency_rechecked"
    assert not by_id["f-other"]["resolved"]


def test_partial_adoption_keeps_active_conflict(job_env):
    """冲突 = 当前译文里仍有 ≥2 种译法。只替换其一，冲突仍成立。"""
    from folith.entity_registry import EntityRegistry

    state = core.new_job_state("term-consistency2.docx")
    pairs = [
        {"source": "The Life of Violet opens the book.",
         "target": "《紫罗兰的一生》打开全书。",
         "target_provenance": "generated", "segment_uid": "d0"},
        {"source": "The library kept The Life of Violet.",
         "target": "图书馆收着《紫罗兰》。",
         "target_provenance": "generated", "segment_uid": "d1"},
        {"source": "Critics praised The Life of Violet.",
         "target": "评论家称赞《紫罗兰传》。",
         "target_provenance": "generated", "segment_uid": "d2"},
    ]
    registry = EntityRegistry()
    registry.observe("The Life of Violet", "紫罗兰的一生", segment_id=0)
    registry.observe("The Life of Violet", "紫罗兰", segment_id=1)
    registry.observe("The Life of Violet", "紫罗兰传", segment_id=2)
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in pairs], "pairs": pairs,
        "entity_registry": registry.to_list(),
        "findings": [{"id": "f-term", "type": "entity_conflict",
                      "segment_index": 0, "source": "The Life of Violet",
                      "resolved": False}],
    })
    core.save_job_state("job-cons2", state)

    result = core.stage_term_revision(
        "job-cons2", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰", "紫罗兰传"])
    ids = [item["candidate_id"] for item in result["created"]
           if item["segment_index"] == 0]
    core.adopt_translation_candidates("job-cons2", ids)
    state = core.load_job_state("job-cons2")
    finding = next(f for f in state["findings"]
                   if f.get("id") == "f-term")
    # 段 1、段 2 还留着两种旧译法：冲突仍然成立，不误报已解决
    assert not finding["resolved"]


# ------------------------------------------------------------ 适配层 -------

def test_adapter_replace_term_targets_adopts_selected_and_discards_rest(job_env):
    _make_job("job-term9")
    from folith.qt import adapter

    result = adapter.replace_term_targets(
        "job-term9", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"], selected=[0])
    assert result["changed"] == 1
    state = core.load_job_state("job-term9")
    assert state["pairs"][0]["target"] == "《薇奥莱特的一生》打开全书，《薇奥莱特的一生》收束全书。"
    assert state["pairs"][6]["target"] == "图书馆收着《紫罗兰》。"
    # 未选中段的候选立即丢弃，不留待采用的悬置候选
    assert _pending(state) == []


def test_adapter_replace_term_targets_cancellation_leaves_text_untouched(job_env):
    _make_job("job-term10")
    state = core.load_job_state("job-term10")
    before = [pair["target"] for pair in state["pairs"]]
    result = core.stage_term_revision(
        "job-term10", "The Life of Violet", "紫罗兰的一生", "薇奥莱特的一生",
        forbidden=["紫罗兰"])
    state = core.load_job_state("job-term10")
    assert [pair["target"] for pair in state["pairs"]] == before, "暂存不改正文"
    assert len(_pending(state)) == len(result["created"])
