"""#94: freshness, invalidation scope, idempotency, undo and interruption.

#29 established the minimum protections (uid resolution, baseline check, one
truth bump, atomic save).  This file is the *proof* part of #94: it pins what
happens around the transaction --

* an intervening manual edit, a structural split, an unknown uid and a legacy
  record without a baseline are all refused, and the text stays put;
* invalidation touches only the adopted segment: its review evidence, its
  acceptance markers and its TM entry go, the untouched segment keeps all three;
* two candidates for one segment cannot both win, repeated adoption does nothing
  the second time, and a running worker or a failed lock refuses the write
  instead of half-applying it;
* undo is a new change: text comes back, trust does not;
* a save that fails leaves neither text nor decision behind, and the journal
  cursor still describes what is on disk;
* old tasks (no candidate fields) open, gain identities on first use, and a
  candidate with no recorded baseline can never be adopted.
"""
from __future__ import annotations

import json

import pytest

import core
from folith import translation_candidates as candidates
from folith import job_lock


def _pairs(*targets, **overrides):
    pairs = []
    for position, target in enumerate(targets):
        pair = {
            "source": f"原文第{position + 1}段，足够长以通过完整性检查。" * 3,
            "target": target,
            "reviewed": False,
            "review_status": "not_reviewed",
            "target_provenance": "generated",
            "from_tm": False,
        }
        pair.update(overrides)
        pairs.append(pair)
    return pairs


def _job(job_id, *, pairs=None, **extra):
    pairs = pairs or _pairs("第一段译文。", "第二段译文。", "第三段译文。")
    state = core.new_job_state("integrity.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        # TM 作用域只认记下来的语言；没有语言时拒删是正确行为，这里给它。
        "target_lang": "简体中文",
        "paras": [pair["source"] for pair in pairs],
        "pairs": [dict(pair) for pair in pairs],
        "findings": [], "review_stats": {},
    })
    state.update(extra)
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def _candidate(job_id, index, text, *, origin=candidates.ORIGIN_TARGETED):
    state = core.load_job_state(job_id)
    pair = state["pairs"][index]
    created = core.commit_candidates(job_id, [{
        "segment_index": index,
        "segment_uid": pair.get("segment_uid") or "",
        "source": pair["source"],
        "candidate_target": text,
        "origin": origin,
        "baseline": candidates.pair_signature(pair),
    }])["created"]
    assert created, "夹具应生成候选"
    return created[0]["candidate_id"], created[0]["segment_uid"]


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


# --------------------------------------------------------------- 过期拒绝 -----


def test_adoption_after_a_manual_edit_is_refused(job_env):
    job_id = "integrity-manual-edit"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    core.save_translation_edit(job_id, 0, "我自己改的译文。")
    result = core.adopt_translation_candidate(job_id, candidate_id)

    state = core.load_job_state(job_id)
    assert not result["committed"]
    assert result["conflict"] == candidates.CONFLICT_TARGET_CHANGED
    assert state["pairs"][0]["target"] == "我自己改的译文。"
    assert candidates.by_id(state, candidate_id)["status"] == candidates.STATUS_STALE


def test_adoption_after_a_structural_split_is_refused(job_env):
    job_id = "integrity-split"
    state = _job(job_id)
    candidate_id, uid = _candidate(job_id, 0, "候选的译文。")

    core.mutate_translation_segments(
        job_id, 0, "split",
        source_a="前半句原文。", source_b="后半句原文。",
        target_a="前半句译文。", target_b="后半句译文。")

    result = core.adopt_translation_candidate(job_id, candidate_id)

    after = core.load_job_state(job_id)
    assert not result["committed"]
    assert result["conflict"] in {candidates.CONFLICT_SEGMENT_MISSING,
                                 candidates.CONFLICT_UID_MISMATCH}
    assert all(pair["target"] != "候选的译文。" for pair in after["pairs"]), \
        "拆分后不能按旧索引把候选写到新段落上"
    assert candidates.by_id(after, candidate_id)["segment_uid"] == uid


def test_a_candidate_for_an_unknown_uid_is_refused(job_env):
    job_id = "integrity-unknown-uid"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")
    state = core.load_job_state(job_id)
    record = candidates.by_id(state, candidate_id)
    record["segment_uid"] = "uid-that-does-not-exist"
    core.save_job_state(job_id, state)

    result = core.adopt_translation_candidate(job_id, candidate_id)

    state = core.load_job_state(job_id)
    assert not result["committed"]
    assert result["conflict"] == candidates.CONFLICT_SEGMENT_MISSING
    assert state["pairs"][0]["target"] == "第一段译文。"


# --------------------------------------------------------------- 失效范围 -----


def _review_event(index, source, target, event_id):
    """A narrowly scoped review event: this segment and nothing else.

    Built by hand on purpose.  Running the real reviewer declares the whole
    document as dependency context (the reviewer saw every translation), so
    every event would legitimately go stale -- which is the opposite of what
    this test is about.
    """
    return {
        "review_event_id": event_id,
        "phase": "formal_review",
        "segment_ids": [index],
        "reviewed_segment_ids": [index],
        "freshness_status": "current",
        "decision": "pass",
        "translation_core": {
            "review_truth": [{"segment_id": index, "source": source,
                              "target": target, "target_checked": True}],
            "dependency_truth": [],
        },
    }


def _seed_tm(job_id):
    state = core.load_job_state(job_id)
    tm = core.load_tm(core.tm_project_id(state))
    for index in range(len(state.get("pairs") or [])):
        core.tm_put(tm, state["pairs"][index]["source"],
                    state["pairs"][index]["target"], "简体中文")
    core.save_tm(tm, core.tm_project_id(state))


def _tm_has(job_id, index):
    state = core.load_job_state(job_id)
    tm = core.load_tm(core.tm_project_id(state))
    record, _mode = core.tm_lookup(tm, state["pairs"][index]["source"],
                                   target_lang="简体中文")
    return record is not None


def test_adoption_invalidates_only_the_segment_it_changed(job_env):
    job_id = "integrity-scope"
    _job(job_id, pairs=_pairs("第一段译文。", "第二段译文。",
                              "第三段译文。",
                              reviewed=True, review_status="reviewed",
                              target_provenance="reviewed"))
    state = core.load_job_state(job_id)
    state["review_evidence"] = [
        _review_event(0, state["pairs"][0]["source"],
                      state["pairs"][0]["target"], "review-0"),
        _review_event(2, state["pairs"][2]["source"],
                      state["pairs"][2]["target"], "review-2"),
    ]
    core.save_job_state(job_id, state)
    _seed_tm(job_id)
    assert _tm_has(job_id, 0) and _tm_has(job_id, 1)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    result = core.adopt_translation_candidate(job_id, candidate_id)

    after = core.load_job_state(job_id)
    assert result["committed"] and result["changed_text"]
    # 被改的那一段：审校证据失效、接受标记清掉、TM 条目撤掉。
    assert after["pairs"][0]["reviewed"] is False
    assert after["pairs"][0]["review_status"] == "not_reviewed"
    assert not _tm_has(job_id, 0)
    events = {event["review_event_id"]: event
              for event in after["review_evidence"]}
    assert events["review-0"]["stale_segment_ids"] == [0]
    # 没被改的段落：证据仍有效、审校标记与 TM 条目都还在。
    assert events["review-2"]["freshness_status"] == "current"
    assert after["pairs"][2]["reviewed"] is True
    assert after["pairs"][2]["review_status"] == "reviewed"
    assert _tm_has(job_id, 1) and _tm_has(job_id, 2)


def test_adoption_resets_the_delivery_approval_but_not_the_snapshot_history(job_env):
    job_id = "integrity-delivery"
    _job(job_id, delivery_status="final", stage="FINAL",
         delivery_approved_by_human=True,
         delivery_approval={"actor": "previous-reviewer"})
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    core.adopt_translation_candidate(job_id, candidate_id)

    after = core.load_job_state(job_id)
    assert after["delivery_approved_by_human"] is False
    assert after["delivery_approval"] is None
    assert after["delivery_status"] != "final"


# ----------------------------------------------------------- 幂等与并发 -----


def test_two_candidates_for_one_segment_cannot_both_win(job_env):
    job_id = "integrity-competing"
    _job(job_id)
    first, _uid = _candidate(job_id, 0, "第一个候选。")
    second, _uid = _candidate(job_id, 0, "第二个候选。")

    result = core.adopt_translation_candidates(job_id, [first, second])

    after = core.load_job_state(job_id)
    assert result["changed"] == 1
    assert [item["conflict"] for item in result["skipped"]] == [
        candidates.CONFLICT_NOT_PENDING]
    assert after["pairs"][0]["target"] == "第二个候选。", "后生成的候选是用户刚要求的那个"
    version = int(after["translation_truth"]["version"])
    adopted = [action for action in after["human_actions"] or []
               if action.get("action") == "candidate_adopted"]
    assert len(adopted) == 1 and version >= 1


def test_the_same_candidate_twice_in_one_call_changes_text_once(job_env):
    job_id = "integrity-duplicate-ids"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    result = core.adopt_translation_candidates(job_id, [candidate_id, candidate_id])

    after = core.load_job_state(job_id)
    assert result["changed"] == 1
    assert after["pairs"][0]["target"] == "候选的译文。"
    actions = [action for action in after["human_actions"] or []
               if action.get("action") == "candidate_adopted"]
    assert len(actions) == 1


def test_adoption_is_refused_while_a_worker_owns_the_job(job_env):
    job_id = "integrity-worker"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    lock_path = core.job_dir(job_id) / ".worker.lock"
    holder = job_lock.acquire(lock_path, blocking=False)
    assert holder is not None
    try:
        with pytest.raises(RuntimeError):
            core.adopt_translation_candidate(job_id, candidate_id)
    finally:
        holder.close()

    after = core.load_job_state(job_id)
    assert after["pairs"][0]["target"] == "第一段译文。", "被拒绝的写入不能留下正文"
    assert candidates.by_id(after, candidate_id)["status"] == candidates.STATUS_PENDING


def test_a_failed_lock_leaves_no_trace(job_env, monkeypatch):
    job_id = "integrity-lock-failure"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")

    from contextlib import contextmanager
    from folith.engine import locks

    @contextmanager
    def denied(*_args, **_kwargs):
        raise RuntimeError("任务「x」的状态正被另一个进程使用，请稍后重试。")
        yield  # pragma: no cover

    monkeypatch.setattr(locks, "job_state_mutation_lock", denied)
    with pytest.raises(RuntimeError):
        core.adopt_translation_candidate(job_id, candidate_id)

    after = core.load_job_state(job_id)
    assert after["pairs"][0]["target"] == "第一段译文。"
    assert candidates.by_id(after, candidate_id)["status"] == candidates.STATUS_PENDING


# ------------------------------------------------------------------ 撤销 -----


def test_undo_after_adoption_restores_text_but_not_trust(job_env):
    job_id = "integrity-undo"
    _job(job_id, pairs=_pairs("第一段译文。", "第二段译文。", "第三段译文。",
                              reviewed=True, review_status="reviewed"))
    _seed_tm(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")
    before_version = int(core.load_job_state(job_id)["translation_truth"]["version"])

    core.adopt_translation_candidate(job_id, candidate_id)
    restored = core.restore_translation_edit(job_id, 0)

    pair = restored["pairs"][0]
    assert pair["target"] == "第一段译文。", "撤销把正文还回来"
    assert pair["reviewed"] is False, "旧的 reviewed 标记不能跟着回来"
    assert pair["review_status"] == "not_reviewed"
    assert pair["from_tm"] is False
    assert not _tm_has(job_id, 0), "TM 条目必须重新取得，不能随撤销复活"
    assert int(restored["translation_truth"]["version"]) >= before_version + 2
    actions = [action.get("action") for action in restored["human_actions"]]
    assert "candidate_adopted" in actions, "撤销不能抹掉采用记录"
    assert "translation_restore" in actions, "撤销本身也是一次正文变更"


# --------------------------------------------------------------- 中断/恢复 ----


def test_an_interrupted_save_leaves_no_decision_behind(job_env, monkeypatch):
    job_id = "integrity-interrupt"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")
    before_actions = len(core.load_job_state(job_id).get("human_actions") or [])
    before_version = int(core.load_job_state(job_id)["translation_truth"]["version"])

    original = core.save_job_state

    def boom(*_args, **_kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(core, "save_job_state", boom)
    try:
        with pytest.raises(OSError):
            core.adopt_translation_candidate(job_id, candidate_id)
    finally:
        monkeypatch.setattr(core, "save_job_state", original)

    after = core.load_job_state(job_id)
    assert after["pairs"][0]["target"] == "第一段译文。"
    assert len(after.get("human_actions") or []) == before_actions, \
        "不能出现「决定记下了但正文没改」"
    assert int(after["translation_truth"]["version"]) == before_version
    assert candidates.by_id(after, candidate_id)["status"] == candidates.STATUS_PENDING


def test_the_journal_cursor_still_describes_the_saved_snapshot(job_env):
    from folith.storage import state_journal

    job_id = "integrity-journal"
    _job(job_id)
    candidate_id, _uid = _candidate(job_id, 0, "候选的译文。")
    core.adopt_translation_candidate(job_id, candidate_id)

    after = core.load_job_state(job_id)
    report = state_journal.state_report(job_id, after)
    # 采用走的是整份快照；游标必须不落后于 journal，否则重放会覆盖已采用的正文。
    assert report["cursor"] >= report["max_seq"]
    assert after["pairs"][0]["target"] == "候选的译文。"
    assert json.dumps(after["pairs"][0]["target"], ensure_ascii=False)


# --------------------------------------------------------------- 旧任务 -----


def test_a_legacy_task_without_candidate_fields_still_works(job_env):
    job_id = "integrity-legacy"
    state = core.new_job_state("legacy.docx")
    state.update({
        "p1_done": True, "p2_done": True,
        "paras": ["旧任务的原文一。", "旧任务的原文二。"],
        "pairs": [{"source": "旧任务的原文一。", "target": "旧任务译文一。"},
                  {"source": "旧任务的原文二。", "target": "旧任务译文二。"}],
        "findings": [],
    })
    state.pop("translation_candidates", None)
    state.pop("translation_candidate_seq", None)
    core.save_job_state(job_id, state)

    assert core.candidate_view(job_id) == []
    assert all(not pair.get("segment_uid")
               for pair in core.load_job_state(job_id)["pairs"])

    candidate_id, uid = _candidate(job_id, 0, "旧任务的新候选。")
    after = core.load_job_state(job_id)
    assert uid, "第一次写候选时补足段落身份"
    assert all(pair.get("segment_uid") for pair in after["pairs"])

    result = core.adopt_translation_candidate(job_id, candidate_id)
    assert result["committed"]
    assert core.load_job_state(job_id)["pairs"][0]["target"] == "旧任务的新候选。"


def test_a_legacy_candidate_without_a_baseline_cannot_be_adopted(job_env):
    job_id = "integrity-legacy-record"
    _job(job_id)
    state = core.load_job_state(job_id)
    pair = state["pairs"][0]
    state["translation_candidates"] = [{
        "candidate_id": "cand-legacy-00001",
        "segment_uid": "",
        "segment_index": 0,
        "candidate_target": "旧版本留下的候选。",
        "origin": candidates.ORIGIN_TARGETED,
        "status": candidates.STATUS_PENDING,
        "generated_at": candidates.now_iso(),
    }]
    core.save_job_state(job_id, state)

    result = core.adopt_translation_candidate(job_id, "cand-legacy-00001")

    after = core.load_job_state(job_id)
    assert not result["committed"]
    assert result["conflict"] in {candidates.CONFLICT_SOURCE_CHANGED,
                                 candidates.CONFLICT_SEGMENT_MISSING,
                                 candidates.CONFLICT_UID_MISMATCH}, \
        "没有基线依据的旧候选只能被拒绝，不能猜"
    assert after["pairs"][0]["target"] == pair["target"]
