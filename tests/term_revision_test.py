"""#126/#128: 术语条目驱动的逐段预览、受控批量修订与一致性同步。

一次术语修订（首选译名改了 / 新增禁用译名）之后：

* 预览按**原文命中**定位段落并分类：可替换 / 已正确 / 有歧义 / 人工保护；
  "原文出现次数"与"可替换译文名数"是两个不同的数字；
* 歧义段只列出、绝不自动改写；人工保护段绝不进批量；排除段一字不改；
* 暂存走 #92 候选事务（锁内重算预览、基线门禁），暂存本身不改正文；
* 采用在同一事务里刷新实体一致性（#128）：已统一的概念矛盾被解决，
  未统一的保持打开；无关发现不受影响；全程零模型调用。

模型从不被调用：``call_llm`` 被 stub 成直接抛错。
"""
from __future__ import annotations

import pytest

import core
from folith import term_revision as tr
from folith.entity_registry import EntityRegistry
from folith.engine import candidates as engine_candidates


def _pairs():
    return [
        {   # 旧首选译名：可替换
            "source": "The Life of Violet is a novel.",
            "target": "《紫罗兰的一生》是一部小说。",
            "target_provenance": "generated",
        },
        {   # 同一段里术语出现两次、旧译名出现两次：一行，全部替换
            "source": "She reads The Life of Violet twice; The Life of Violet repeats.",
            "target": "她把紫罗兰的一生读了两遍，紫罗兰的一生出现了两次。",
            "target_provenance": "generated",
        },
        {   # 已是新版译名：already_correct，无行
            "source": "The Life of Violet won a prize.",
            "target": "《薇奥莱特的一生》获奖了。",
            "target_provenance": "generated",
        },
        {   # 原文命中但译文里既没有旧译名也没有新译名：歧义，只列出
            "source": "He mentioned The Life of Violet.",
            "target": "他提到了那本书。",
            "target_provenance": "generated",
        },
        {   # 人工改写过且带旧译名：保护段
            "source": "He praised The Life of Violet.",
            "target": "他称赞了紫罗兰的一生。",
            "human_edited": True,
            "target_provenance": "human_edit",
        },
        {   # 人工确认过且带禁用译名：保护段
            "source": "They discussed The Life of Violet.",
            "target": "他们讨论了维奥莱特的一生。",
            "human_accepted": True,
            "accepted_target": "他们讨论了维奥莱特的一生。",
        },
        {   # 译文带旧译名但原文没有该术语：禁止无上下文替换
            "source": "Violet blooms in June.",
            "target": "紫罗兰的一生在六月开花。",
            "target_provenance": "generated",
        },
        {   # 已排除的段：整个跳过
            "source": "The Life of Violet ends quietly.",
            "target": "紫罗兰的一生安静地收尾。",
            "target_provenance": "generated",
            "excluded": True,
        },
        {   # 同一段还有另一条术语的译名：本条修订不能碰它
            "source": "The Life of Violet and The Midnight Library share a theme.",
            "target": "紫罗兰的一生和子夜图书馆共用一个主题。",
            "target_provenance": "generated",
        },
        {   # 审校过（reviewed 不算锁定保护）：可替换
            "source": "The Life of Violet begins in war.",
            "target": "紫罗兰的一生始于战乱。",
            "reviewed": True,
            "review_status": "reviewed",
        },
    ]


def _entry():
    return {"id": "term-violet", "source": "The Life of Violet",
            "new_preferred": "薇奥莱特的一生", "old_preferred": "紫罗兰的一生",
            "forbidden": ["维奥莱特的一生"]}


def _make_job(job_id, pairs=None, findings=None):
    pairs = _pairs() if pairs is None else pairs
    state = core.new_job_state("term-revision.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in pairs],
        "pairs": pairs,
        "glossary": [_entry()],
        "findings": findings or [],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    # 确定性路径绝不调模型：谁调用谁炸。
    def _no_llm(*_args, **_kwargs):
        raise AssertionError("术语修订的确定性路径调用了模型")
    monkeypatch.setattr(core, "call_llm", _no_llm)
    return tmp_path


def _targets(state):
    return [pair.get("target") for pair in state.get("pairs") or []]


def _pending(state):
    return [r for r in (state.get("translation_candidates") or [])
            if r.get("status") == "pending"]


# ------------------------------------------------------------ 预览 --------

def test_preview_classifies_segments_instead_of_assuming_replaceable(job_env):
    preview = tr.preview_term_revision(_entry(), _pairs())
    assert preview["new_preferred"] == "薇奥莱特的一生"
    assert {row["segment_index"] for row in preview["rows"]} == {0, 1, 8, 9}
    assert preview["already_correct"] == [2]
    assert {item["segment_index"] for item in preview["ambiguous"]} == {3}
    assert {item["segment_index"] for item in preview["protected"]} == {4, 5}
    # 计数语义：原文出现 8 段 ≠ 可替换 4 段。
    total = (len(preview["rows"]) + len(preview["ambiguous"])
             + len(preview["protected"]) + len(preview["already_correct"]))
    assert total == 8 and len(preview["rows"]) == 4


def test_multiple_occurrences_in_one_segment_become_one_row(job_env):
    preview = tr.preview_term_revision(_entry(), _pairs())
    row1 = next(row for row in preview["rows"] if row["segment_index"] == 1)
    assert row1["before"] == "她把紫罗兰的一生读了两遍，紫罗兰的一生出现了两次。"
    assert row1["after"] == "她把薇奥莱特的一生读了两遍，薇奥莱特的一生出现了两次。"
    assert row1["variants"] == ["紫罗兰的一生"], "本段命中的写法逐字记录"


def test_other_terms_renderings_are_not_touched(job_env):
    preview = tr.preview_term_revision(_entry(), _pairs())
    row8 = next(row for row in preview["rows"] if row["segment_index"] == 8)
    assert row8["after"] == "薇奥莱特的一生和子夜图书馆共用一个主题。"


def test_source_without_the_term_never_enters_the_batch(job_env):
    preview = tr.preview_term_revision(_entry(), _pairs())
    all_indexes = ({row["segment_index"] for row in preview["rows"]}
                   | {item["segment_index"] for item in preview["ambiguous"]}
                   | {item["segment_index"] for item in preview["protected"]}
                   | set(preview["already_correct"]))
    assert 6 not in all_indexes, "原文不含术语的段绝不进批量（禁止整书正则替换）"
    assert 7 not in all_indexes, "已排除的段整个跳过"


def test_reviewed_is_replaceable_but_human_work_is_protected(job_env):
    preview = tr.preview_term_revision(_entry(), _pairs())
    assert 9 in {row["segment_index"] for row in preview["rows"]}, \
        "reviewed 不是锁定保护：可替换（仍走候选事务）"
    protections = {item["segment_index"]: item["protection"]
                   for item in preview["protected"]}
    assert set(protections) == {4, 5}
    assert "human_edit" in protections[4] and "human_accepted" in protections[5]


def test_missing_source_or_new_preferred_has_no_revision(job_env):
    assert tr.preview_term_revision({"source": ""}, _pairs())["reason"] == "no_source_term"
    assert tr.preview_term_revision(
        {"source": "The Life of Violet", "new_preferred": ""},
        _pairs())["reason"] == "no_new_preferred"


# ------------------------------------------------------------ 暂存与采用 --------

def test_staging_writes_no_text_and_traces_origin(job_env):
    _make_job("job-x")
    before = _targets(core.load_job_state("job-x"))
    result = core.stage_term_revision("job-x", _entry(), note="统一译名")
    assert _targets(core.load_job_state("job-x")) == before, "暂存不改正文"
    assert {item["segment_index"] for item in result["created"]} == {0, 1, 8, 9}
    assert all(item["origin"] == "term_revision" for item in result["created"])
    assert all(item["candidate_id"] for item in result["created"])
    # 歧义与保护段只存在于预览里，绝不产生候选
    assert {item["segment_index"] for item in result["preview"]["ambiguous"]} == {3}
    assert {item["segment_index"] for item in result["preview"]["protected"]} == {4, 5}


def test_selected_only_staging_leaves_deselected_occurrences_unchanged(job_env):
    _make_job("job-x")
    result = core.stage_term_revision("job-x", _entry(), exclusions=[0, 9])
    assert {item["segment_index"] for item in result["created"]} == {1, 8}
    assert next(row for row in result["preview"]["rows"]
                if row["segment_index"] == 0)["excluded"] is True
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    targets = _targets(core.load_job_state("job-x"))
    assert targets[0] == "《紫罗兰的一生》是一部小说。", "取消勾选的段一字不改"
    assert targets[1] == "她把薇奥莱特的一生读了两遍，薇奥莱特的一生出现了两次。"


def test_stale_segment_between_preview_and_stage_is_not_written(job_env):
    _make_job("job-x")
    # 预览之后、暂存之前，第 9 段被重译成不含旧译名的文本
    live = core.load_job_state("job-x")
    live["pairs"][9]["target"] = "重译后的句子。"
    core.save_job_state("job-x", live)

    result = core.stage_term_revision("job-x", _entry())
    assert 9 not in {item["segment_index"] for item in result["created"]}, \
        "锁内重算：重译后的段不再匹配，绝不写入"
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    targets = _targets(core.load_job_state("job-x"))
    assert targets[9] == "重译后的句子。"
    assert targets[0] == "《薇奥莱特的一生》是一部小说。"


def test_stale_baseline_between_stage_and_adopt_skips_instead_of_writing(job_env):
    _make_job("job-x")
    result = core.stage_term_revision("job-x", _entry())
    ids = [item["candidate_id"] for item in result["created"]]
    # 暂存之后、采用之前，第 0 段被人工改写
    live = core.load_job_state("job-x")
    live["pairs"][0]["target"] = "人工改写的新译文。"
    live["pairs"][0]["human_edited"] = True
    live["pairs"][0]["target_provenance"] = "human_edit"
    core.save_job_state("job-x", live)

    batch = engine_candidates.adopt_translation_candidates("job-x", ids)
    by_id = {item["candidate_id"]: item for item in batch["results"]}
    assert by_id[ids[0]]["committed"] is False, "基线已过期：跳过而不是写错段"
    assert by_id[ids[0]]["conflict"]
    assert _targets(core.load_job_state("job-x"))[0] == "人工改写的新译文。"
    assert by_id[ids[1]]["committed"] is True, "未受影响的段正常提交"


def test_repeated_staging_leaves_one_adoptable_candidate_per_segment(job_env):
    _make_job("job-x")
    core.stage_term_revision("job-x", _entry())
    core.stage_term_revision("job-x", _entry())  # 重复点击
    state = core.load_job_state("job-x")
    by_segment: dict = {}
    for record in _pending(state):
        by_segment.setdefault(record["segment_uid"], 0)
        by_segment[record["segment_uid"]] += 1
    assert all(count == 1 for count in by_segment.values()), \
        "重复暂存后每段只有一个可采用候选"

    result = core.stage_term_revision("job-x", _entry())
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    state = core.load_job_state("job-x")
    assert _targets(state)[0] == "《薇奥莱特的一生》是一部小说。", "重复点击不产生重复写"
    batch = engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    assert batch["already_adopted"], "采用幂等"


def test_forbidden_renderings_are_replaced_too(job_env):
    pairs = _pairs()[:1]
    pairs[0]["target"] = "《维奥莱特的一生》是一部小说。"
    _make_job("job-x", pairs=pairs)
    result = core.stage_term_revision("job-x", _entry())
    assert {item["segment_index"] for item in result["created"]} == {0}
    engine_candidates.adopt_translation_candidates(
        "job-x", [item["candidate_id"] for item in result["created"]])
    assert _targets(core.load_job_state("job-x"))[0] == "《薇奥莱特的一生》是一部小说。"


def test_glossary_findings_are_linked_to_staged_candidates(job_env):
    findings = [{"id": "f-1", "type": "glossary", "entry_id": "term-violet",
                 "segment_index": 0, "severity": "actionable"}]
    _make_job("job-x", findings=findings)
    result = core.stage_term_revision("job-x", _entry())
    row0 = next(item for item in result["created"] if item["segment_index"] == 0)
    assert row0["finding_ids"] == ["f-1"], "暂存候选带上本段的术语发现"
    row1 = next(item for item in result["created"] if item["segment_index"] == 1)
    assert row1["finding_ids"] == [], "其他段不带无关发现"


# ------------------------------------------------------------ #128 一致性同步 --------

def _conflict_pairs():
    return [
        {"source": "Violet is the heroine.", "target": "紫罗兰的一生是主角。",
         "target_provenance": "generated"},
        {"source": "Violet grows up fast.", "target": "维奥莱特的一生成长很快。",
         "target_provenance": "generated"},
        {"source": "Everyone praises Violet.", "target": "薇奥莱特的一生人人称赞。",
         "target_provenance": "generated"},
    ]


def _conflict_state():
    registry = EntityRegistry()
    registry.observe("Violet", "紫罗兰的一生", segment_id=0, provenance="reviewed")
    registry.observe("Violet", "维奥莱特的一生", segment_id=1)
    registry.lock("Violet", "薇奥莱特的一生")
    return registry.to_list()


def _make_conflict_job(job_id, pairs=None):
    pairs = _conflict_pairs() if pairs is None else pairs
    findings = EntityRegistry(_conflict_state()).consistency_findings(pairs)
    state = core.new_job_state("term-consistency.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in pairs],
        "pairs": pairs,
        "entity_registry": _conflict_state(),
        "findings": findings,
        "glossary": [{"id": "term-violet", "source": "Violet",
                      "preferred": "薇奥莱特的一生", "status": "locked"}],
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def _conflict_entry():
    return {"id": "term-violet", "source": "Violet",
            "new_preferred": "薇奥莱特的一生", "old_preferred": "紫罗兰的一生",
            "forbidden": ["维奥莱特的一生"]}


def test_full_adoption_resolves_the_entity_conflict(job_env):
    _make_conflict_job("job-c")
    state = core.load_job_state("job-c")
    finding = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert not finding.get("resolved"), "前置：矛盾发现处于打开状态"

    result = core.stage_term_revision("job-c", _conflict_entry())
    ids = [item["candidate_id"] for item in result["created"]]
    engine_candidates.adopt_translation_candidates("job-c", ids)

    state = core.load_job_state("job-c")
    finding = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert finding.get("resolved") is True, "全部统一后矛盾被解决"
    assert finding["resolution"]["action"] == "entity_consistency_rechecked"
    assert _targets(state) == ["薇奥莱特的一生是主角。", "薇奥莱特的一生成长很快。",
                               "薇奥莱特的一生人人称赞。"]


def test_partial_adoption_keeps_the_finding_active(job_env):
    # 第 1 段人工改写：批量不碰它，禁用译名残留 → 矛盾保持打开
    pairs = _conflict_pairs()
    pairs[1]["target"] = "维奥莱特的一生成长很快。"
    pairs[1]["human_edited"] = True
    pairs[1]["target_provenance"] = "human_edit"
    _make_conflict_job("job-c", pairs=pairs)

    result = core.stage_term_revision("job-c", _conflict_entry())
    ids = [item["candidate_id"] for item in result["created"]]
    assert all(item["segment_index"] != 1 for item in result["created"]), \
        "保护段不进批量"
    engine_candidates.adopt_translation_candidates("job-c", ids)

    state = core.load_job_state("job-c")
    finding = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert not finding.get("resolved"), "仍存在未统一的写法：发现保持打开"


def test_unrelated_findings_survive_the_refresh(job_env):
    _make_conflict_job("job-c")
    live = core.load_job_state("job-c")
    live["findings"].append({"id": "f-other", "type": "glossary",
                             "entry_id": "g-other", "segment_index": 2,
                             "severity": "actionable"})
    core.save_job_state("job-c", live)

    result = core.stage_term_revision("job-c", _conflict_entry())
    engine_candidates.adopt_translation_candidates(
        "job-c", [item["candidate_id"] for item in result["created"]])

    state = core.load_job_state("job-c")
    other = next(f for f in state["findings"] if f.get("id") == "f-other")
    assert not other.get("resolved"), "无关发现不受术语修订影响"
    conflict = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert conflict.get("resolved") is True


def test_repeated_qa_runs_do_not_resurrect_resolved_findings(job_env):
    _make_conflict_job("job-c")
    result = core.stage_term_revision("job-c", _conflict_entry())
    engine_candidates.adopt_translation_candidates(
        "job-c", [item["candidate_id"] for item in result["created"]])
    state = core.load_job_state("job-c")
    finding = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert finding.get("resolved") is True

    from folith.engine.glossary_ops import _refresh_entity_consistency
    _refresh_entity_consistency(state, actor="system")
    _refresh_entity_consistency(state, actor="system")
    finding = next(f for f in state["findings"] if f["type"] == "entity_conflict")
    assert finding.get("resolved") is True, "重复 QA 不复活已解决的发现"


# ------------------------------------------------------------ adapter --------

def test_adapter_revise_term_stages_and_adopts_in_one_call(job_env):
    from folith.qt import adapter

    _make_job("job-x")
    result = adapter.revise_term("job-x", _entry())
    assert result["staged"] == 4 and result["changed"] == 4
    assert not result["skipped"] and not result["rejected"]
    targets = _targets(core.load_job_state("job-x"))
    assert targets[0] == "《薇奥莱特的一生》是一部小说。"
    assert targets[4] == "他称赞了紫罗兰的一生。", "人工保护段不被批量覆盖"

    preview_view = adapter.term_revision_preview("job-x", _entry())
    assert preview_view["preview"]["rows"] == [], "采用后无可替换段"
    assert preview_view["preview"]["already_correct"] == [0, 1, 2, 8, 9]
    assert len(preview_view["pairs"]) == 10, "预览视图带段落投影供对话框展示"
