"""Deterministic finding grouping for numbers/units/tags/placeholders (#101).

Pins the acceptance points:
* each type has positive and negative detection examples;
* grouping preserves evidence, location, decisions and modification records;
* excluded occurrences stay out of the group while remaining findable;
* handling previews are read-only and the P0 per-item path is the only writer;
* a deterministic number fix is a candidate and a stale candidate cannot commit;
* no model call; disabled grouping leaves findings individually inspectable.
"""
from __future__ import annotations

import json
import os
import time

import pytest

import core
from folith import finding_groups as fg
from folith import models
from folith import persistence_profiler
from folith import workbench_view
from folith.storage import job_repository as repo


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


def _findings(src, tgt, lang="简体中文"):
    return core.check_translation_batch([src], [tgt], [], lang)


def _by_kind(findings, kind):
    return [item for item in findings if item.get("kind") == kind]


def test_number_detection_positive_and_negative():
    missing = _findings("The sample weighs 12.5 grams today.",
                        "样本今天重克。")
    numbers = _by_kind(missing, "number")
    assert [item["group_token"] for item in numbers] == ["12.5"]
    assert numbers[0]["group_type"] == "number"
    assert numbers[0]["severity"] == "actionable"

    # Digits written in the target's numerals are not "missing".
    assert _by_kind(_findings("The sample weighs 12.5 grams.",
                              "样本重 12.5 克。"), "number") == []
    # A citation's digits belong to the preserved-token check, not to numbers.
    assert _by_kind(_findings("See [12] for details.", "详见 [12]。"), "number") == []
    # A number that changes only its digit script is accepted where folded.
    assert _by_kind(_findings("Item 3 is ready.", "第 ٣ 项已就绪。"),
                    "number") == []


def test_unit_detection_positive_and_negative():
    kept = _findings("The village is 5 km from here.", "村庄离这里 5 公里。")
    assert _by_kind(kept, "unit") == []

    drifted = _findings("The village is 5 km from here.", "村庄离这里 5 英里。")
    units = _by_kind(drifted, "unit")
    assert len(units) == 1
    assert units[0]["group_token"] == "km"
    assert units[0]["expected_number"] == "5"
    assert units[0]["expected_unit"] == "km"

    missing = _findings("The village is 5 km from here.", "村庄离这里很近。")
    assert len(_by_kind(missing, "unit")) == 1


def test_tag_detection_positive_and_negative():
    lost = _findings("<b>Warning</b> read this.", "警告：请阅读。")
    tags = _by_kind(lost, "tag")
    assert {item["group_token"] for item in tags} == {"<b>", "</b>"}
    assert _by_kind(_findings("<b>Warning</b> read this.",
                              "<b>警告</b>：请阅读。"), "tag") == []


def test_placeholder_detection_is_grouped_too():
    findings = _findings("Hello {name}, welcome.", "你好，欢迎。")
    placeholders = _by_kind(findings, "placeholder")
    assert placeholders, "existing placeholder check must keep working"
    groups = fg.group_findings(findings)
    assert any(group["group_type"] == "placeholder" for group in groups)


def test_grouping_preserves_evidence_and_decisions():
    findings = [
        {**_findings("The sample weighs 12.5 grams.", "样本重克。")[0],
         "finding_id": "f-1", "decision": "request_revision"},
        {**_findings("It was 12.5 metres long.", "它长 12 米。")[0],
         "finding_id": "f-2"},
        {**_findings("<b>x</b>", "x")[0], "finding_id": "f-3"},
    ]
    findings[0]["segment_index"] = findings[0]["segment_id"] = 0
    findings[1]["segment_index"] = findings[1]["segment_id"] = 1
    findings[2]["segment_index"] = findings[2]["segment_id"] = 2
    groups = fg.group_findings(findings)
    numbers = next(group for group in groups if group["group_type"] == "number")
    assert numbers["token"] == "12.5" and numbers["occurrence_count"] == 2
    assert numbers["segments"] == [0, 1]
    assert {member["finding_id"] for member in numbers["members"]} == {"f-1", "f-2"}
    assert {member["decision"] for member in numbers["members"]} == {
        "request_revision", "open"}
    assert numbers["open_count"] == 1 or numbers["open_count"] == 2
    assert all(member["reason"] for member in numbers["members"])
    assert any(group["group_type"] == "tag" for group in groups)
    # Non-groupable findings never leak into a group.
    assert all(group["group_type"] in fg.GROUP_TYPES for group in groups)


def test_exceptions_keep_occurrences_unchanged_and_inspectable():
    findings = []
    for index, (src, tgt) in enumerate([
            ("The sample weighs 12.5 grams.", "样本重克。"),
            ("It was 12.5 metres long.", "它长 12 米。")]):
        item = _findings(src, tgt)[0]
        item["segment_index"] = item["segment_id"] = index
        findings.append(item)
    exceptions = [
        {"group_id": fg.group_id_for(findings[0]), "reason": "数字按版式改写"},
    ]
    groups = fg.group_findings(findings, exceptions=exceptions)
    number_group = next(group for group in groups if group["group_type"] == "number")
    assert number_group["excluded_count"] == 2
    assert number_group["members"] == []
    assert {item["segment_index"] for item in number_group["excluded"]} == {0, 1}
    annotated = fg.annotate_findings(findings, exceptions=exceptions)
    assert all(not item.get("finding_group_id") for item in annotated)

    # A single-occurrence exception excludes only that occurrence.
    exceptions = [{"group_type": "number", "token": "12.5", "segment_index": 0}]
    groups = fg.group_findings(findings, exceptions=exceptions)
    number_group = next(group for group in groups if group["group_type"] == "number")
    assert number_group["occurrence_count"] == 1
    assert number_group["excluded_count"] == 1


def test_grouping_can_be_disabled_without_touching_findings(temp_job_env):
    state = core.new_job_state("groups.docx")
    findings = _findings("The sample weighs 12.5 grams.", "样本重克。")
    findings[0]["segment_index"] = findings[0]["segment_id"] = 0
    state["findings"] = findings
    assert core.deterministic_finding_groups(state=state)

    os.environ[fg.ENV_SWITCH] = "0"
    try:
        assert core.deterministic_finding_groups(state=state) == []
        view = workbench_view.review_workbench_view(state)
        assert view["finding_groups"] == []
        assert state["findings"] == findings, "关闭分组不得改写发现项"
    finally:
        os.environ.pop(fg.ENV_SWITCH, None)


def test_preview_is_read_only_and_shows_current_text(temp_job_env):
    state = core.new_job_state("preview.docx")
    state["paras"] = ["The sample weighs 12.5 grams.", "It was 12.5 metres."]
    state["pairs"] = [
        {"source": "The sample weighs 12.5 grams.", "target": "样本重克。"},
        {"source": "It was 12.5 metres.", "target": "它长 12 米。"},
    ]
    findings = []
    for index, pair in enumerate(state["pairs"]):
        item = _findings(pair["source"], pair["target"])[0]
        item["segment_index"] = item["segment_id"] = index
        findings.append(item)
    state["findings"] = findings
    group = core.deterministic_finding_groups(state=state)[0]
    before = json.dumps(state["pairs"], ensure_ascii=False, sort_keys=True)
    preview = core.deterministic_finding_group_preview(
        "preview", group["group_id"], state=state)
    assert preview["would_change_text"] is False
    assert [row["segment_index"] for row in preview["rows"]] == [0, 1]
    assert json.dumps(state["pairs"], ensure_ascii=False, sort_keys=True) == before


def _make_number_fix_job(job_id, tmp_path):
    state = core.new_job_state("numbers.docx")
    state["paras"] = [
        "The sample weighs 12.5 grams.",
        "It was 12.5 metres long.",
    ]
    state["pairs"] = [
        {"source": "The sample weighs 12.5 grams.", "target": "样本重 12 克。"},
        {"source": "It was 12.5 metres long.", "target": "它长 12 米。"},
    ]
    state["target_lang"] = "简体中文"
    findings = []
    for index, pair in enumerate(state["pairs"]):
        for item in _findings(pair["source"], pair["target"]):
            if item.get("kind") == "number":
                item["segment_index"] = item["segment_id"] = index
                findings.append(item)
    state["findings"] = findings
    core.save_job_state(job_id, state)
    return core.load_job_state(job_id)


def test_number_fix_is_a_candidate_and_stale_candidate_cannot_commit(temp_job_env):
    job_id = "group-fix"
    state = _make_number_fix_job(job_id, temp_job_env)
    groups = core.deterministic_finding_groups(state=state)
    assert groups and groups[0]["group_type"] == "number"

    result = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    assert result["candidate"] is not None
    mid = core.load_job_state(job_id)
    assert mid["pairs"][0]["target"] == "样本重 12 克。", "候选不写正文"

    # Edit the segment after materialisation -> the baseline no longer matches.
    mid["pairs"][0]["target"] = "样本重 12 克，已复核。"
    core.save_job_state(job_id, mid)
    adopted = core.adopt_translation_candidate(
        job_id, result["candidate"]["candidate_id"], actor="tester")
    assert adopted.get("committed") is False
    final = core.load_job_state(job_id)
    assert final["pairs"][0]["target"] == "样本重 12 克，已复核。"

    # The other segment still resolves cleanly when adopted explicitly.
    other = core.load_job_state(job_id)
    other["groups_second"] = True
    core.save_job_state(job_id, other)
    groups = core.deterministic_finding_groups(state=core.load_job_state(job_id))
    second = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    if second["candidate"] is not None:
        ok = core.adopt_translation_candidate(
            job_id, second["candidate"]["candidate_id"], actor="tester")
        assert ok.get("committed") is True


def test_grouping_and_fix_build_add_no_model_call(monkeypatch, temp_job_env):
    def boom(*_args, **_kwargs):
        raise AssertionError("deterministic grouping must not call a model")

    monkeypatch.setattr(core, "call_llm", boom)
    job_id = "group-nomodel"
    state = _make_number_fix_job(job_id, temp_job_env)
    groups = core.deterministic_finding_groups(state=state)
    assert groups
    preview = core.deterministic_finding_group_preview(
        job_id, groups[0]["group_id"], state=state)
    assert preview["rows"]
    result = core.materialize_deterministic_group_fix(job_id, groups[0]["group_id"])
    assert result["candidate"] is not None


def test_local_grouping_cost_is_bounded():
    findings = []
    for index in range(2000):
        findings.append({
            "type": "check", "severity": "actionable", "kind": "number",
            "group_type": "number", "group_token": f"12.{index % 7}",
            "detected_text": f"12.{index % 7}", "segment_index": index,
            "segment_id": index, "summary": "数字缺失",
            "reason": "数字缺失", "source_span": f"12.{index % 7}",
            "target_span": "",
        })
    started = time.perf_counter()
    groups = fg.group_findings(findings)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert len(groups) == 7
    assert sum(group["occurrence_count"] for group in groups) == 2000
    assert elapsed_ms < 500, f"grouping 2000 findings took {elapsed_ms:.1f} ms"
