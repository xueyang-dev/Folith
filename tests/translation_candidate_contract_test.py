"""Candidates: generation never writes, adoption is the only commit.

These are the #29 acceptance contracts (Milestone 5):

* generating a retranslation leaves the working translation, the acceptance
  markers, the review state, the findings and the delivery gate untouched;
* discarding, cancelling or failing changes nothing either;
* adoption is version-checked (segment identity, source, target, review state),
  idempotent, and writes exactly one traceable change;
* a late result never overwrites newer text;
* candidates survive a restart and old tasks can still be opened.

The model is a deterministic stub: these tests assert state transitions, not
translation quality, and must not cost a request.
"""
from __future__ import annotations

import json

import pytest

import core
from folith import translation_candidates as candidates


LONG_SOURCE = "The squadron prepared for the long-range mission. " * 4


def _state(filename="candidate.docx"):
    return core.new_job_state(filename)


def _save(job_id, state):
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def _job(job_id, *, pairs, filename="candidate.docx", **extra):
    state = _state(filename)
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": [pair["source"] for pair in pairs],
        "pairs": [dict(pair) for pair in pairs],
        "findings": [], "review_stats": {},
    })
    state.update(extra)
    return _save(job_id, state)


def _stub_llm(text="候选译文：中队已经为远程任务做好了充分准备。"):
    def llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        if "学术翻译专家" in system_prompt:
            return json.dumps([text])
        return "[]"

    return llm


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", _stub_llm())
    return tmp_path


PROTECTED_PAIRS = [
    {  # 人工修改过、还有待处理的检查项
        "source": LONG_SOURCE,
        "target": "人工修改后的译文。",
        "initial_target": "最初的机器译文。",
        "human_edited": True,
        "reviewed": False,
        "review_status": "not_reviewed",
        "target_provenance": "human_edit",
        "from_tm": False,
        "glossary_entry_ids": [],
    },
    {  # 人工接受过
        "source": "第二段原文足够长以通过完整性检查。",
        "target": "第二段已接受译文。",
        "accepted_target": "第二段已接受译文。",
        "human_accepted": True,
        "reviewed": True,
        "review_status": "reviewed_clean",
        "target_provenance": "human_accepted",
        "from_tm": False,
        "glossary_entry_ids": [],
    },
    {  # 已审校
        "source": "第三段原文同样足够长以通过完整性检查。",
        "target": "第三段已审校的译文。",
        "reviewed": True,
        "review_status": "reviewed_clean",
        "target_provenance": "generated",
        "from_tm": False,
        "glossary_entry_ids": [],
    },
]

PROTECTED_FINDINGS = [
    {"segment_index": 0, "severity": "blocking", "type": "check",
     "reason": "疑似漏译/截断：原文 200 字符，译文仅 9 字符"},
    {"segment_index": 1, "severity": "actionable", "type": "review",
     "reason": "需要人工确认的审校建议"},
]


def _protected_job(job_id):
    return _job(
        job_id, pairs=PROTECTED_PAIRS, findings=[dict(item) for item in PROTECTED_FINDINGS],
        delivery_status="final", stage="FINAL", delivery_approved_by_human=True,
        delivery_approval={"actor": "previous-reviewer"},
        review_stats={"blocking": 1, "actionable": 1, "informational": 0},
        has_blocking=True,
    )


def _snapshot(state):
    """Everything a generation call must not change."""
    pairs = state.get("pairs") or []
    return {
        "targets": [pair.get("target") for pair in pairs],
        "initial_targets": [pair.get("initial_target") for pair in pairs],
        "reviewed": [bool(pair.get("reviewed")) for pair in pairs],
        "review_status": [pair.get("review_status") for pair in pairs],
        "provenance": [pair.get("target_provenance") for pair in pairs],
        "accepted": [pair.get("accepted_target") for pair in pairs],
        "human_accepted": [pair.get("human_accepted") for pair in pairs],
        "from_tm": [bool(pair.get("from_tm")) for pair in pairs],
        "findings": [(item.get("reason"), item.get("resolved"))
                     for item in state.get("findings") or []],
        "human_actions": len(state.get("human_actions") or []),
        "truth_version": int((state.get("translation_truth") or {}).get("version") or 0),
        "delivery_status": state.get("delivery_status"),
        "stage": state.get("stage"),
        "review_stats": dict(state.get("review_stats") or {}),
    }


def test_generation_does_not_touch_protected_segments(job_env):
    job_id = "cand-protected-gen"
    before = _protected_job(job_id)
    baseline = _snapshot(before)

    state, created = core.retranslate_segments(
        job_id, [0, 1, 2], "DeepSeek", "k", "model", "简体中文", glossary=[])

    assert created == [0, 1, 2], "三段都应生成候选"
    assert _snapshot(state) == baseline, "生成候选不得改动任何受保护状态"

    stored = candidates.pending_records(state)
    assert len(stored) == 3
    assert all(record["candidate_target"].startswith("候选译文") for record in stored)
    assert all(record["status"] == candidates.STATUS_PENDING for record in stored)
    # 基线是生成时那一段的样子，采用时要拿它比对。
    assert stored[0]["baseline"]["target"] == "人工修改后的译文。"
    assert stored[1]["baseline"]["human_accepted"] is True
    assert stored[0]["usage"] == {"unknown": True}, "未报告的 token 不能写成 0"

    on_disk = core.load_job_state(job_id)
    assert _snapshot(on_disk) == baseline
    assert len(candidates.pending_records(on_disk)) == 3, "候选必须落盘"


def test_rejecting_or_failing_generation_leaves_the_manuscript_alone(job_env, monkeypatch):
    job_id = "cand-reject-fail"
    before = _protected_job(job_id)
    baseline = _snapshot(before)

    def broken(*_args, **_kwargs):
        raise RuntimeError("模型连接中断")

    monkeypatch.setattr(core, "call_llm", broken)
    with pytest.raises(ValueError):
        core.retranslate_segments(
            job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])

    state = core.load_job_state(job_id)
    assert _snapshot(state) == baseline
    assert candidates.pending_records(state) == []


def test_discarding_a_candidate_does_not_claim_the_issue_is_fixed(job_env):
    job_id = "cand-discard"
    _protected_job(job_id)
    core.retranslate_segments(
        job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])
    state = core.load_job_state(job_id)
    candidate_id = candidates.pending_records(state)[0]["candidate_id"]
    baseline = _snapshot(state)

    result = core.discard_translation_candidate(job_id, candidate_id, actor="user")

    assert result["discarded"] is True
    after = core.load_job_state(job_id)
    for key in ("targets", "initial_targets", "reviewed", "findings",
                "truth_version", "delivery_status", "stage"):
        assert _snapshot(after)[key] == baseline[key], key
    assert candidates.by_id(after, candidate_id)["status"] == candidates.STATUS_DISCARDED
    assert all(not item.get("resolved") for item in after.get("findings") or []), \
        "丢弃候选不等于处置检查项"


def test_adoption_is_version_checked_idempotent_and_traceable(job_env):
    job_id = "cand-adopt"
    before = _protected_job(job_id)
    truth_before = int((before.get("translation_truth") or {}).get("version") or 0)
    core.retranslate_segments(
        job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])
    state = core.load_job_state(job_id)
    record = candidates.pending_records(state)[0]
    candidate_id = record["candidate_id"]

    first = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert first["committed"] is True and first["changed_text"] is True
    after = core.load_job_state(job_id)
    pair = after["pairs"][0]
    assert pair["target"] == record["candidate_target"]
    assert pair["target_provenance"] == "candidate_adopted"
    assert pair["adopted_candidate_id"] == candidate_id
    assert pair["reviewed"] is False, "采用候选后必须重新审校"
    assert not pair.get("human_accepted"), "采用候选不得保留旧的人工接受标记"
    truth_after = int((after.get("translation_truth") or {}).get("version") or 0)
    assert truth_after == truth_before + 1, "一次采用只递增一次正文版本"
    actions = [item for item in after.get("human_actions") or []
               if item.get("action") == "candidate_adopted"]
    assert len(actions) == 1
    assert actions[0]["finding_id"] == f"candidate:{candidate_id}",         "决定记录必须指名被采用的候选"
    changes = [item for item in after.get("human_actions") or []
               if item.get("action") == "translation_candidate_adopted"]
    assert len(changes) == 1, "正文变更只记一次"
    assert actions[0]["finding_id"] == f"candidate:{candidate_id}", \
        "决定记录必须指名被采用的候选"
    changes = [item for item in after.get("human_actions") or []
               if item.get("action") == "translation_candidate_adopted"]
    assert len(changes) == 1, "正文变更只记一次"
    assert candidates.by_id(after, candidate_id)["status"] == candidates.STATUS_ADOPTED
    assert candidates.by_id(after, candidate_id)["adopted_revision"] == truth_after

    second = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert second["committed"] is True and second["already_adopted"] is True
    again = core.load_job_state(job_id)
    assert again["pairs"][0]["target"] == pair["target"]
    assert int((again.get("translation_truth") or {}).get("version") or 0) == truth_after
    assert len([item for item in again.get("human_actions") or []
                if item.get("action") == "candidate_adopted"]) == 1


def test_a_candidate_goes_stale_when_the_segment_moves(job_env):
    job_id = "cand-stale-target"
    _protected_job(job_id)
    core.retranslate_segments(
        job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])
    state = core.load_job_state(job_id)
    candidate_id = candidates.pending_records(state)[0]["candidate_id"]

    # 生成期间人工又改了这一段的译文：迟到的候选不能覆盖新稿。
    core.save_translation_edit(job_id, 0, "人工在生成期间写下的新译文。")

    result = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert result["committed"] is False
    assert result["conflict"] == candidates.CONFLICT_TARGET_CHANGED
    after = core.load_job_state(job_id)
    assert after["pairs"][0]["target"] == "人工在生成期间写下的新译文。"
    record = candidates.by_id(after, candidate_id)
    assert record["status"] == candidates.STATUS_STALE
    assert record["conflict"] == candidates.CONFLICT_TARGET_CHANGED


def test_a_candidate_goes_stale_when_the_source_is_corrected(job_env):
    job_id = "cand-stale-source"
    _protected_job(job_id)
    core.retranslate_segments(
        job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])
    state = core.load_job_state(job_id)
    candidate_id = candidates.pending_records(state)[0]["candidate_id"]

    core.mutate_translation_segments(
        job_id, 0, "source_edit", source_text="订正后的原文，长度足够通过完整性检查。" * 3,
        actor="user")

    result = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert result["committed"] is False
    assert result["conflict"] == candidates.CONFLICT_SOURCE_CHANGED
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "人工修改后的译文。"


def test_same_text_adoption_records_the_choice_without_a_text_change(job_env):
    job_id = "cand-same-text"
    monkeypatch_target = "候选译文：中队已经为远程任务做好了充分准备。"
    _protected_job(job_id)
    state = core.load_job_state(job_id)
    state["pairs"][2]["target"] = monkeypatch_target
    core.save_job_state(job_id, state)
    truth_before = int((core.load_job_state(job_id).get("translation_truth") or {})
                       .get("version") or 0)

    core.retranslate_segments(
        job_id, [2], "DeepSeek", "k", "model", "简体中文", glossary=[])
    candidate_id = candidates.pending_records(core.load_job_state(job_id))[0]["candidate_id"]
    result = core.adopt_translation_candidate(job_id, candidate_id, actor="user")

    assert result["committed"] is True and result["changed_text"] is False
    after = core.load_job_state(job_id)
    assert after["pairs"][2]["target"] == monkeypatch_target
    assert int((after.get("translation_truth") or {}).get("version") or 0) == truth_before, \
        "同文采用不得凭空递增正文版本"
    assert after["pairs"][2].get("adopted_candidate_id") is None, "正文没有变化"
    record = candidates.by_id(after, candidate_id)
    assert record["status"] == candidates.STATUS_ADOPTED
    assert record["note"].startswith("同文采用")


def test_adoption_follows_the_segment_identity_not_the_row_number(job_env):
    job_id = "cand-uid"
    _job(job_id, pairs=[
        {"source": LONG_SOURCE, "target": "第一段译文。", "reviewed": False,
         "review_status": "not_reviewed", "target_provenance": "generated",
         "from_tm": False},
        {"source": "第二段原文足够长以通过完整性检查。", "target": "第二段译文。",
         "reviewed": False, "review_status": "not_reviewed",
         "target_provenance": "generated", "from_tm": False},
    ])
    core.retranslate_segments(
        job_id, [1], "DeepSeek", "k", "model", "简体中文", glossary=[])
    candidate_id = candidates.pending_records(core.load_job_state(job_id))[0]["candidate_id"]
    candidate_target = candidates.pending_records(
        core.load_job_state(job_id))[0]["candidate_target"]

    # 在第一段之前插入一段：第二段的索引从 1 变成 2，但它的身份不变。
    core.mutate_translation_segments(
        job_id, 0, "insert", source_text="插入的一段新原文，长度足够通过完整性检查。")

    result = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert result["committed"] is True
    after = core.load_job_state(job_id)
    by_source = {pair["source"]: pair["target"] for pair in after["pairs"]}
    assert by_source["第二段原文足够长以通过完整性检查。"] == candidate_target
    assert by_source[LONG_SOURCE] == "第一段译文。"


def test_adoption_rejects_an_unknown_segment_identity(job_env):
    job_id = "cand-uid-mismatch"
    _protected_job(job_id)
    core.retranslate_segments(
        job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])
    state = core.load_job_state(job_id)
    record = candidates.pending_records(state)[0]
    record["segment_uid"] = "seg-does-not-exist"
    core.save_job_state(job_id, state)

    result = core.adopt_translation_candidate(
        job_id, record["candidate_id"], actor="user")
    assert result["committed"] is False
    assert result["conflict"] == candidates.CONFLICT_SEGMENT_MISSING
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "人工修改后的译文。"


def test_adoption_honours_the_source_quality_gate(job_env):
    job_id = "cand-source-gate"
    state = _state("scan.pdf")
    state.update(
        p1_done=True, p2_done=True, paras=["3"],
        pairs=[{"source": "3", "target": "", "reviewed": False,
                "review_status": "not_reviewed", "target_provenance": "generated",
                "from_tm": False}],
        findings=[], review_stats={},
    )
    _save(job_id, state)
    assert core._audit_source_quality_for_state(state)["flags"], "夹具必须是会被门禁拦下的段"

    with pytest.raises(ValueError):
        core.retranslate_segments(
            job_id, [0], "DeepSeek", "k", "model", "简体中文", glossary=[])

    # 手工塞一个候选（模拟旧版本留下的、或在门禁生效前生成的记录），采用仍必须被拒。
    state = core.load_job_state(job_id)
    core.assign_missing_segment_uids(state, job_id=job_id)
    record = candidates.make_record(
        candidate_id="cand-legacy-00001",
        segment_uid=core.segment_uid(state["pairs"][0]),
        segment_index=0,
        pair=state["pairs"][0],
        candidate_target="不该被写入的译文。",
        origin=candidates.ORIGIN_TARGETED,
    )
    core.save_job_state(job_id, {**state, candidates.CANDIDATES_KEY: [record]})

    result = core.adopt_translation_candidate(job_id, "cand-legacy-00001", actor="user")
    assert result["committed"] is False
    assert result["conflict"] == candidates.CONFLICT_SOURCE_UNCONFIRMED
    assert core.load_job_state(job_id)["pairs"][0]["target"] == ""


def test_candidates_survive_a_restart(job_env):
    job_id = "cand-restart"
    _protected_job(job_id)
    core.retranslate_segments(
        job_id, [1], "DeepSeek", "k", "model", "简体中文", glossary=[])
    candidate_id = candidates.pending_records(core.load_job_state(job_id))[0]["candidate_id"]

    reopened = core.load_job_state(job_id)
    pending = candidates.pending_records(reopened)
    assert [record["candidate_id"] for record in pending] == [candidate_id]

    result = core.adopt_translation_candidate(job_id, candidate_id, actor="user")
    assert result["committed"] is True


def test_old_task_without_candidates_still_opens(job_env):
    job_id = "cand-legacy-task"
    state = _job(job_id, pairs=[
        {"source": LONG_SOURCE, "target": "旧任务译文。", "reviewed": True,
         "review_status": "reviewed_clean", "target_provenance": "generated",
         "from_tm": False}])
    assert "translation_candidates" not in state
    view = core.candidate_view(job_id)
    assert view == []
    assert core.pending_candidate_count(job_id) == 0
