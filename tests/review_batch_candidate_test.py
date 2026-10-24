"""#92: review suggestions and batch revision go through the candidate contract.

The four write entries used to disagree: targeted and instructed retranslation
produced text (the first one wrote it), applying a review suggestion stitched
three writes together *and* closed the finding, and batch find/replace ran one
transaction per segment.  After #29/#93 they share one contract:

* generating (review suggestion, batch staging) never changes the translation;
* adopting is the only commit and is version- and identity-checked;
* a batch reports per-item results and refuses to overwrite human work;
* every text change leaves the same trace: candidate provenance, one
  translation-truth bump, one candidate-keyed audit action.

The model is a deterministic stub: these tests assert state transitions.
"""
from __future__ import annotations

import json

import pytest

import core
from folith import translation_candidates as candidates
from folith.qt import adapter

QUERY = "图书馆"
REPLACEMENT = "藏书楼"


def _pairs():
    return [
        {   # 人工改写过：批量不得覆盖
            "source": "第一段：我每周都去图书馆。",
            "target": "第一段：我每周都去图书馆。",
            "human_edited": True,
            "target_provenance": "human_edit",
            "reviewed": False,
            "review_status": "not_reviewed",
            "from_tm": False,
        },
        {   # 人工确认过：批量不得覆盖
            "source": "第二段：图书馆周六关门。",
            "target": "第二段：图书馆周六关门。",
            "accepted_target": "第二段：图书馆周六关门。",
            "human_accepted": True,
            "reviewed": True,
            "review_status": "reviewed_clean",
            "target_provenance": "human_accepted",
            "from_tm": False,
        },
        {   # 普通机器译文：批量可以替换
            "source": "第三段：图书馆里很安静。",
            "target": "第三段：图书馆里很安静。",
            "reviewed": False,
            "review_status": "not_reviewed",
            "target_provenance": "generated",
            "from_tm": False,
        },
    ]


FINDINGS = [
    {"segment_index": 2, "type": "review", "severity": "actionable",
     "reason": "措辞可以更自然", "summary": "措辞可以更自然",
     "suggested_target": "第三段：藏书楼里很安静。"},
]


def _make_job(job_id):
    state = core.new_job_state("review-batch.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in _pairs()],
        "pairs": _pairs(),
        "findings": [dict(item) for item in FINDINGS],
        "review_stats": {"blocking": 0, "actionable": 1, "informational": 0},
    })
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def _stub_llm(text="模型给出的新译文。"):
    calls = []

    def llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        calls.append((provider, model))
        if "学术翻译专家" in system_prompt:
            return json.dumps([text])
        return "[]"

    llm.calls = calls
    return llm


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    llm = _stub_llm()
    monkeypatch.setattr(core, "call_llm", llm)
    return tmp_path, llm


def _targets(state):
    return [pair.get("target") for pair in state.get("pairs") or []]


def _open_findings(state):
    return [item for item in state.get("findings") or []
            if not item.get("resolved")]


# ------------------------------------------------------------ 审校建议 --------


def test_a_review_suggestion_is_staged_without_writing(job_env):
    job_id = "batch-review-stage"
    before = _make_job(job_id)
    item = next(item for item in adapter.review_queue(job_id)["queue_items"]
                if item.get("suggested_target"))

    result = adapter.apply_suggested_translation(job_id, item, "采纳这条建议")

    state = core.load_job_state(job_id)
    assert _targets(state) == _targets(before), "暂存建议不能改正文"
    assert [item.get("resolved") for item in state["findings"]] \
        == [item.get("resolved") for item in before["findings"]]
    assert len(state.get("human_actions") or []) == len(before.get("human_actions") or []), \
        "暂存建议不是人工决定，不该留下决定记录"
    created = result["created"]
    assert len(created) == 1
    assert created[0]["origin"] == candidates.ORIGIN_REVIEW
    assert created[0]["status"] == candidates.STATUS_PENDING
    assert created[0]["candidate_target"] == FINDINGS[0]["suggested_target"]
    assert created[0]["finding_ids"], "候选要能追到它对应哪条发现项"


def test_adopting_a_review_suggestion_does_not_resolve_the_finding(job_env):
    job_id = "batch-review-adopt"
    _make_job(job_id)
    item = next(item for item in adapter.review_queue(job_id)["queue_items"]
                if item.get("suggested_target"))
    candidate_id = adapter.apply_suggested_translation(job_id, item)["created"][0][
        "candidate_id"]

    adopted = adapter.adopt_candidate(job_id, candidate_id)

    state = core.load_job_state(job_id)
    assert adopted["committed"] and adopted["changed_text"]
    assert state["pairs"][2]["target"] == FINDINGS[0]["suggested_target"]
    assert state["pairs"][2]["adopted_candidate_id"] == candidate_id
    # #93：采用是文本变更，解决发现项是一条独立的人工决定。
    assert _open_findings(state), "采用建议不能替人把发现项标成已解决"


# ------------------------------------------------------------ 批量修订 --------


def test_batch_staging_changes_nothing_and_is_per_segment(job_env):
    job_id = "batch-stage"
    before = _make_job(job_id)

    staged = adapter.stage_batch_replacement(job_id, QUERY, REPLACEMENT)

    state = core.load_job_state(job_id)
    assert _targets(state) == _targets(before)
    assert staged["selected"] == 3
    assert [item["segment_index"] for item in staged["created"]] == [0, 1, 2]
    assert all(item["origin"] == candidates.ORIGIN_BATCH for item in staged["created"])
    assert [item["adoptable"] for item in staged["created"]] == [True, True, True], \
        "受保护段落仍可逐段采用，只是批量默认跳过"


def test_batch_adoption_skips_human_work_and_reports_every_item(job_env):
    job_id = "batch-adopt"
    _make_job(job_id)

    result = adapter.replace_in_targets(job_id, QUERY, REPLACEMENT)

    state = core.load_job_state(job_id)
    assert result["staged"] == 3
    assert result["changed"] == 1, "只应写入没有被人工处理过的那一段"
    assert _targets(state) == [
        "第一段：我每周都去图书馆。",       # 人工改写：保持
        "第二段：图书馆周六关门。",         # 人工确认：保持
        "第三段：藏书楼里很安静。",         # 机器译文：替换
    ]
    skipped = {item["conflict"] for item in result["skipped"]}
    assert skipped == {candidates.CONFLICT_PROTECTED_HUMAN}
    assert len(result["skipped"]) == 2
    assert all(item["reason"] for item in result["skipped"])
    # 跳过的候选仍然待采用：用户可以在段落里单独处理。
    pending = candidates.pending_records(state)
    assert len(pending) == 2


def test_batch_can_replace_protected_segments_when_explicitly_asked(job_env):
    job_id = "batch-adopt-protected"
    _make_job(job_id)

    result = adapter.replace_in_targets(job_id, QUERY, REPLACEMENT,
                                        allow_protected=True)

    state = core.load_job_state(job_id)
    assert result["changed"] == 3
    assert all(REPLACEMENT in target for target in _targets(state))
    adopted = [record for record in candidates.candidate_records(state)
               if record.get("status") == candidates.STATUS_ADOPTED]
    assert all(record.get("overrode_protection") for record in adopted[:2]), \
        "覆盖人工作业必须记录在候选上，不能悄悄发生"
    # 人工接受标记随正文一起失效，而不是留在新译文上。
    assert not state["pairs"][1].get("human_accepted")
    assert not state["pairs"][1].get("accepted_target")


def test_batch_only_touches_the_selected_segments(job_env):
    job_id = "batch-selected"
    _make_job(job_id)

    result = adapter.replace_in_targets(job_id, QUERY, REPLACEMENT, indexes=[2])

    state = core.load_job_state(job_id)
    assert result["staged"] == 1 and result["changed"] == 1
    assert _targets(state)[:2] == [
        "第一段：我每周都去图书馆。",
        "第二段：图书馆周六关门。",
    ]


def test_a_stale_batch_item_is_skipped_without_blocking_the_rest(job_env):
    job_id = "batch-stale"
    _make_job(job_id)
    staged = adapter.stage_batch_replacement(job_id, QUERY, REPLACEMENT, indexes=[0, 2])
    ids = [item["candidate_id"] for item in staged["created"]]

    # 用户在采用前自己改了第 0 段：那条候选必须过期，其它段照常采用。
    state = core.load_job_state(job_id)
    state["pairs"][0]["target"] = "第一段：我自己改过了。"
    core.save_job_state(job_id, state)

    adopted = core.adopt_translation_candidates(job_id, ids)

    assert adopted["changed"] == 1
    conflicts = {item["conflict"] for item in adopted["skipped"]}
    assert conflicts == {candidates.CONFLICT_TARGET_CHANGED}
    state = core.load_job_state(job_id)
    assert state["pairs"][0]["target"] == "第一段：我自己改过了。"
    assert state["pairs"][2]["target"] == "第三段：藏书楼里很安静。"


def test_repeating_a_batch_adoption_is_idempotent(job_env):
    job_id = "batch-idempotent"
    _make_job(job_id)
    staged = adapter.stage_batch_replacement(job_id, QUERY, REPLACEMENT,
                                            indexes=[2])
    ids = [item["candidate_id"] for item in staged["created"]]

    first = core.adopt_translation_candidates(job_id, ids)
    version = int(core.load_job_state(job_id)["translation_truth"]["version"])
    second = core.adopt_translation_candidates(job_id, ids)
    state = core.load_job_state(job_id)

    assert first["changed"] == 1
    assert second["changed"] == 0, "重复点击不再产生第二次正文变更"
    assert second["already_adopted"] == ids
    assert int(state["translation_truth"]["version"]) == version
    adopted_actions = [action for action in state.get("human_actions") or []
                       if action.get("action") == "candidate_adopted"]
    assert len(adopted_actions) == 1


# ------------------------------------------------- 四个入口同一份写入契约 ------


def _adopt_all(job_id):
    state = core.load_job_state(job_id)
    ids = [record["candidate_id"] for record in candidates.pending_records(state)]
    # 逐段采用就是用户指着这一段点的：受保护段落允许覆盖，批量才默认跳过。
    return core.adopt_translation_candidates(job_id, ids, allow_protected=True)


def test_all_four_entry_points_share_one_write_contract(job_env):
    """同一夹具分别经过四条入口：生成不改稿，采用才写一次、留同一份记录。"""
    _tmp, llm = job_env
    entries = {
        "targeted": lambda job_id: core.retranslate_segments(
            job_id, [0], "DeepSeek", "k", "model", "简体中文",
            glossary=[], origin=candidates.ORIGIN_TARGETED),
        "instructed": lambda job_id: core.retranslate_segments(
            job_id, [1], "DeepSeek", "k", "model", "简体中文",
            glossary=[], instruction="更书面一些",
            origin=candidates.ORIGIN_INSTRUCTED),
        "review": lambda job_id: adapter.apply_suggested_translation(
            job_id, {"segment_index": 2,
                     "suggested_target": "第三段：审校建议的译文。"}),
        "batch": lambda job_id: adapter.stage_batch_replacement(
            job_id, QUERY, REPLACEMENT, indexes=[2]),
    }
    for name, run in entries.items():
        job_id = f"contract-{name}"
        before = _make_job(job_id)

        run(job_id)

        after = core.load_job_state(job_id)
        assert _targets(after) == _targets(before), f"{name}：生成不能改正文"
        assert int(after["translation_truth"]["version"]) \
            == int(before["translation_truth"]["version"]), f"{name}：生成不能升版本"

        result = _adopt_all(job_id)
        assert result["changed"] == 1, f"{name}：采用只写一次"
        adopted = core.load_job_state(job_id)
        changed = [position for position, pair in enumerate(adopted["pairs"])
                   if pair.get("target") != before["pairs"][position]["target"]]
        assert len(changed) == 1, f"{name}：只改一段"
        pair = adopted["pairs"][changed[0]]
        assert pair["target_provenance"] == "candidate_adopted"
        assert pair["adopted_candidate_id"], f"{name}：正文要能追到候选"
        actions = [action for action in adopted.get("human_actions") or []
                   if action.get("action") == "candidate_adopted"]
        assert len(actions) == 1, f"{name}：恰好一次可追溯的采用记录"
        assert actions[0]["finding_id"] == f"candidate:{pair['adopted_candidate_id']}"
        assert llm.calls, "重译入口确实调用了模型（stub）"


def test_a_failed_save_leaves_the_batch_unwritten(job_env, monkeypatch):
    job_id = "batch-save-failure"
    before = _make_job(job_id)
    staged = adapter.stage_batch_replacement(job_id, QUERY, REPLACEMENT)
    ids = [item["candidate_id"] for item in staged["created"]]

    original = core.save_job_state

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(core, "save_job_state", boom)
    try:
        with pytest.raises(OSError):
            core.adopt_translation_candidates(job_id, ids, allow_protected=True)
    finally:
        monkeypatch.setattr(core, "save_job_state", original)

    state = core.load_job_state(job_id)
    assert _targets(state) == _targets(before), "保存失败不能留下半批正文"
    assert all(record["status"] == candidates.STATUS_PENDING
               for record in candidates.candidate_records(state)
               if record["candidate_id"] in ids)
