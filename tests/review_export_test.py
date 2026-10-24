"""Review handoff export (#103).

Pins the acceptance points: adoption vs keeping the target, manual edits,
skips, unreviewed and unresolved items are distinguishable; unchanged targets
are never reported as modified; the export is a read-only projection that still
matches current text after reopening; long English/Chinese text and traceable
identifiers survive.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core
from folith import persistence_profiler
from folith import review_export
from folith import translation_candidates as _cand
from folith.storage import job_repository as repo

SEGMENTS = [
    "The northern archive keeps the first map of the valley.",
    "Violet asked about the missing letter again.",
    "Nobody told the archivist what happened after the flood.",
]
TARGETS = [
    "北方档案馆保存着山谷的第一幅地图。",
    "维奥莱特再次询问那封丢失的信。",
    "没有人告诉档案管理员洪水之后发生了什么。",
]


@pytest.fixture
def temp_job_env(tmp_path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    core.reset_tm_dir_cache()
    persistence_profiler.disable()
    persistence_profiler.reset()
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()
        core.reset_tm_dir_cache()
        persistence_profiler.disable()
        persistence_profiler.reset()


class _Provider:
    def __call__(self, provider, api_key, model, system_prompt, user_prompt,
                 temperature=0.1, **kwargs):
        system_prompt = system_prompt or ""
        if "独立的翻译审校专家" in system_prompt:
            return "[]"
        if "翻译流知识抽取器" in system_prompt:
            return "[]"
        if "专业翻译人员" not in system_prompt:
            return "[]"
        sources = []
        for line in (user_prompt or "").splitlines():
            stripped = line.strip()
            head, sep, rest = stripped.partition(". ")
            if sep and head.isdigit():
                sources.append(rest)
            elif stripped.startswith("原文："):
                sources.append(stripped[len("原文："):])
        mapping = dict(zip(SEGMENTS, TARGETS))
        return json.dumps([mapping.get(source, "译文") for source in sources],
                          ensure_ascii=False)


def _run(job_id, *, review_mode="always", enable_review=True):
    state = core.new_job_state("export.txt")
    state["paras"] = list(SEGMENTS)
    state["target_lang"] = "简体中文"
    state["pairs"] = []
    state["review_mode"] = review_mode
    state["style_rules"] = ""
    original = core.call_llm
    core.call_llm = _Provider()
    try:
        core.translate_stage(
            state, job_id, [], "Benchmark", "key", "model", "简体中文", "",
            enable_review=enable_review, use_tm=False, batch_size=3,
            max_batch_chars=100000, translation_concurrency=1,
            review_mode=review_mode)
    finally:
        core.call_llm = original
    return core.load_job_state(job_id)


def _export(job_id):
    return json.loads(core.review_handoff_export(job_id, "json"))


def test_adoption_keep_manual_edit_unresolved_are_distinct(temp_job_env):
    job_id = "handoff-real"
    state = _run(job_id)
    pairs = state["pairs"]
    assert len(pairs) == 3

    # 1) adoption that changes the text
    draft = {
        "segment_index": 0,
        "segment_uid": str(pairs[0].get("segment_uid") or ""),
        "source": pairs[0]["source"],
        "candidate_target": "北方档案馆收藏着山谷的第一幅地图。",
        "origin": _cand.ORIGIN_REVIEW,
        "baseline": _cand.pair_signature(pairs[0]),
    }
    created = core.commit_candidates(job_id, [draft])
    adopted = core.adopt_translation_candidate(
        job_id, created["created"][0]["candidate_id"], actor="reviewer")
    assert adopted["committed"] is True and adopted["changed_text"] is True

    # 2) adoption that keeps the current target (same text)
    current = core.load_job_state(job_id)["pairs"][1]["target"]
    kept = {
        "segment_index": 1,
        "segment_uid": str(core.load_job_state(job_id)["pairs"][1].get("segment_uid") or ""),
        "source": SEGMENTS[1],
        "candidate_target": current,
        "origin": _cand.ORIGIN_REVIEW,
        "baseline": _cand.pair_signature(core.load_job_state(job_id)["pairs"][1]),
    }
    created = core.commit_candidates(job_id, [kept])
    core.adopt_translation_candidate(
        job_id, created["created"][0]["candidate_id"], actor="reviewer",
        note="保留当前译文")

    # 3) manual edit
    core.save_translation_edit(job_id, 2, "没有人把洪水之后发生的事告诉档案管理员。",
                               actor="handler")

    # 4) an open unresolved finding on the kept segment
    state = core.load_job_state(job_id)
    state.setdefault("findings", []).append({
        "type": "check", "severity": "actionable",
        "finding_id": "finding-open-1", "segment_index": 1, "segment_id": 1,
        "reason": "术语未统一", "detected_text": "档案管理员",
        "source_span": "archivist", "target_span": "档案管理员",
        "resolved": False,
    })
    core.save_job_state(job_id, state)

    payload = _export(job_id)
    rows = {row["segment_index"]: row for row in payload["segments"]}
    assert rows[0]["status"] == "adopted"
    assert rows[0]["modified"] is True
    assert rows[0]["changes"][0]["kind"] == "candidate_adoption"
    assert rows[0]["changes"][0]["before"] == TARGETS[0]
    assert rows[0]["changes"][0]["after"] == "北方档案馆收藏着山谷的第一幅地图。"
    assert rows[0]["adopted_candidate_id"]

    assert rows[1]["status"] == "unresolved"
    assert rows[1]["modified"] is False, "保留译文不得报告为修改"
    assert rows[1]["adopted_candidate_id"]
    # the same-text adoption is still visible as a decision, not as a change
    assert any(item.get("action") == "candidate_adopted_unchanged"
               for item in payload["decisions"])

    assert rows[2]["status"] == "manual_edit"
    assert rows[2]["modified"] is True
    assert rows[2]["changes"][0]["kind"] == "manual_edit"
    assert rows[2]["changes"][0]["before"] == TARGETS[2]
    assert rows[2]["changes"][0]["after"] == "没有人把洪水之后发生的事告诉档案管理员。"

    summary = payload["summary"]
    assert summary["adopted"] == 1 and summary["adopted_unchanged"] == 0
    assert summary["manual_edits"] == 1
    assert summary["modified_targets"] == 2
    assert summary["unchanged_targets"] == 1
    assert summary["unresolved_findings"] == 1
    assert summary["unresolved_segments"] == 1


def test_skipped_and_unreviewed_are_explicit_and_never_passes(temp_job_env):
    skipped_job = "handoff-skipped"
    _run(skipped_job, review_mode="risk_selective")
    payload = _export(skipped_job)
    assert payload["summary"]["skipped"] == 3
    assert payload["summary"]["reviewed"] == 0
    assert all(row["status"] == "skipped" for row in payload["segments"])
    assert all(row["skipped"] and not row["reviewed"]
               for row in payload["segments"])
    assert len(payload["skipped_segments"]) == 3
    assert payload["summary"]["coverage_complete"] is False

    # A required-but-not-yet-reviewed task: no events -> unreviewed.
    unreviewed_job = "handoff-unreviewed"
    state = core.new_job_state("unreviewed.txt")
    state["paras"] = list(SEGMENTS)
    state["pairs"] = [{"source": source, "target": target}
                      for source, target in zip(SEGMENTS, TARGETS)]
    state["translation_core_review_required"] = True
    core.save_job_state(unreviewed_job, state)
    payload = _export(unreviewed_job)
    assert payload["summary"]["unreviewed"] == 3
    assert all(row["status"] == "unreviewed" for row in payload["segments"])
    assert len(payload["unreviewed_segments"]) == 3


def test_reopen_matches_current_text_and_unchanged_is_never_modified(temp_job_env):
    job_id = "handoff-reopen"
    _run(job_id)
    first = _export(job_id)
    text_before = {row["segment_index"]: row["current_target"]
                   for row in first["segments"]}
    assert all(not row["modified"] for row in first["segments"])

    # Reopen: reload from disk (new state object) and export again.
    repo.invalidate_cache()
    reopened = core.load_job_state(job_id)
    assert reopened is not None
    second = json.loads(core.review_handoff_export(job_id, "json"))
    for row in second["segments"]:
        assert row["current_target"] == reopened["pairs"][row["segment_index"]]["target"]
        assert row["current_target"] == text_before[row["segment_index"]]
        assert row["modified"] is False

    # A manual edit after the first export is the only reported modification.
    core.save_translation_edit(job_id, 0, "北方档案馆保存着山谷的第一张地图。")
    third = _export(job_id)
    rows = {row["segment_index"]: row for row in third["segments"]}
    assert rows[0]["modified"] is True and rows[1]["modified"] is False
    assert third["summary"]["modified_targets"] == 1
    assert third["segments"][0]["current_target"] == \
        core.load_job_state(job_id)["pairs"][0]["target"]


def test_long_bilingual_text_and_identifiers_survive(temp_job_env):
    job_id = "handoff-long"
    long_source = ("The northern archive keeps the first map of the valley. " * 60).strip()
    long_target = ("北方档案馆保存着山谷的第一幅地图。" * 60).strip()
    state = core.new_job_state("long.txt")
    state["paras"] = [long_source]
    state["pairs"] = [{"source": long_source, "target": long_target,
                       "initial_target": long_target, "reviewed": True,
                       "review_status": "reviewed"}]
    state["translation_core_review_required"] = False
    core.save_job_state(job_id, state)

    payload = _export(job_id)
    row = payload["segments"][0]
    assert row["source"] == long_source
    assert row["current_target"] == long_target
    assert row["segment_uid"], "traceable segment identifier required"
    assert len(row["segment_uid"]) <= 80

    clipped = json.loads(core.review_handoff_export(job_id, "json"))
    assert clipped["segments"][0]["target_truncated"] is False
    # Explicit truncation only when the caller asks for it.
    compact = review_export.build_review_export(
        core.load_job_state(job_id), job_id, max_text_chars=40)
    assert compact["segments"][0]["target_truncated"] is True
    assert len(compact["segments"][0]["current_target"]) == 40

    markdown = core.review_handoff_export(job_id, "markdown")
    assert "## 概览" in markdown and "segment_uid" in markdown
    jsonl = core.review_handoff_export(job_id, "jsonl").splitlines()
    assert json.loads(jsonl[0])["kind"] == "summary"
    assert json.loads(jsonl[1])["kind"] == "segment"


def test_export_is_read_only_and_disable_keeps_records(temp_job_env):
    job_id = "handoff-readonly"
    _run(job_id)
    before = json.dumps(core.load_job_state(job_id), ensure_ascii=False,
                        sort_keys=True, default=str)
    _export(job_id)
    core.review_handoff_export(job_id, "markdown")
    after = json.dumps(core.load_job_state(job_id), ensure_ascii=False,
                       sort_keys=True, default=str)
    assert before == after, "导出不得修改任务状态"

    # There is no separate reporting truth: after deleting review history from a
    # copy, the export reflects the copy rather than a cached report.
    state = core.load_job_state(job_id)
    state["human_actions"] = []
    for pair in state["pairs"]:
        pair.pop("human_edited", None)
        pair.pop("_translation_edit_restore", None)
    core.save_job_state(job_id, state)
    payload = _export(job_id)
    assert payload["summary"]["decisions"] == 0
    assert payload["schemas"] if False else payload["schema_version"] == 1


def test_registered_exception_is_exported_and_does_not_modify_target(temp_job_env):
    job_id = "handoff-exception"
    state = core.new_job_state("exception.txt")
    state["paras"] = [SEGMENTS[0]]
    state["pairs"] = [{"source": SEGMENTS[0], "target": TARGETS[0],
                       "reviewed": True, "review_status": "reviewed_clean"}]
    state["translation_core_review_required"] = False
    state["findings"] = [{
        "type": "review", "severity": "informational",
        "finding_id": "finding-exception-1", "segment_index": 0, "segment_id": 0,
        "reason": "风格建议：可更书面化", "detected_text": "",
        "resolved": True, "status": "dismissed",
        "resolution": {"action": "dismiss", "note": "保留作者风格",
                       "actor": "reviewer", "timestamp": "2026-10-24T00:00:00Z"},
    }]
    core.save_job_state(job_id, state)

    payload = _export(job_id)
    assert payload["summary"]["dismissed_findings"] == 1
    assert payload["summary"]["resolved_findings"] == 1
    exception = payload["dismissed_findings"][0]
    assert exception["finding_id"] == "finding-exception-1"
    assert exception["resolution"]["note"] == "保留作者风格"
    row = payload["segments"][0]
    assert row["modified"] is False
    assert row["current_target"] == TARGETS[0]
    assert [item["finding_id"] for item in row["findings"]] == ["finding-exception-1"]
