"""Canonical Task Overview state matrix.

These tests pin the single derived status model that the workspace header,
sidebar, hero, cards and pipeline all render.  They exist because the page used
to show "Ready for delivery", "暂不满足交付条件" and "可以准备交付" at the same
time: each surface owned a private derivation.  Every assertion below is about
one derivation (`derive_task_overview_state`) and the vocabulary it is allowed
to produce.
"""
from __future__ import annotations

import json

import pytest

from folith import delivery
from folith import task_overview as to
from folith.translation_core import fingerprint


# ---------------- fixtures ----------------

def _state(count=2, *, review_required=False, report=False, targets=None):
    targets = targets or [f"译文 {index}" for index in range(count)]
    pairs = [{"source": f"Source {index}", "target": targets[index]}
             for index in range(count)]
    return {
        "p1_done": True,
        "p2_done": True,
        "paras": [pair["source"] for pair in pairs],
        "pairs": pairs,
        "translation_core_review_required": review_required,
        "report_enabled": report,
        "findings": [],
        "review_evidence": [],
        "human_actions": [],
    }


def _review_event(segment_id):
    target = f"译文 {segment_id}"
    return {
        "phase": "formal_review",
        "review_scope": "current_translation",
        "review_event_id": f"review-{segment_id}",
        "segment_ids": [segment_id],
        "freshness_status": "current",
        "completion_receipt": {"status": "completed",
                               "reviewed_segment_ids": [segment_id]},
        "translation_core": {
            "final_consumed_input_fingerprint": fingerprint(
                {"segment": segment_id, "target": target}),
            "review_truth": [{"segment_id": segment_id,
                              "source": f"Source {segment_id}",
                              "target": target, "target_checked": True}],
        },
    }


def _reviewed_state(count=2):
    state = _state(count, review_required=True)
    state["review_evidence"] = [_review_event(index) for index in range(count)]
    return state


_REPORT_READY_FACTS = {
    "structural_qa": "PASS",
    "compliance": {"status": "pass", "counts": {}},
    "artifact_status": {"report": "valid", "final_docx_validation": "valid",
                        "libreoffice_render": "valid"},
    "case_gate": {"status": "pass", "required_count": 1, "blocked_count": 0},
}

_QA_CONFIRMED = {
    "structural_qa": "PASS",
    "libreoffice_render": "PASS",
    "author_visual_review": "CONFIRMED",
    "word_final_review": "CONFIRMED",
}


def _report_state(**overrides):
    state = _state(1, report=True)
    state.update({"p3_done": True, "report_status": "generated",
                  "final_qa": dict(_QA_CONFIRMED)})
    state.update(overrides)
    return state


# ---------------- lifecycle matrix ----------------

def test_draft_before_any_translation():
    overview = to.derive_task_overview_state({})

    assert overview["lifecycle"] == to.DRAFT
    assert overview["tone"] == to.GRAY
    assert overview["label"] == "尚未开始"
    assert overview["primary_action"]["label"] == "开始翻译"


def test_translation_in_progress_is_blue():
    state = _state(2, targets=["译文 0", ""])
    state["p2_done"] = False

    overview = to.derive_task_overview_state(state)

    assert overview["lifecycle"] == to.TRANSLATING
    assert overview["tone"] == to.BLUE
    assert overview["primary_action"]["destination"] == "translation"


def test_blocking_problem_is_red_and_offers_no_delivery_action():
    state = _state(1, targets=["TODO: replace before delivery"])

    overview = to.derive_task_overview_state(state)

    assert overview["lifecycle"] == to.NEEDS_ATTENTION
    assert overview["tone"] == to.RED
    assert overview["blocking_count"] == 1
    assert "问题" in overview["primary_action"]["label"]
    assert overview["primary_action"]["kind"] == "handle_blocking"
    assert all(action["kind"] != "prepare_delivery"
               for action in [overview["primary_action"]] + overview["secondary_actions"])


def test_pending_review_is_attention_not_blocking():
    state = _state(1, review_required=True)

    overview = to.derive_task_overview_state(state)

    assert overview["lifecycle"] == to.NEEDS_ATTENTION
    assert overview["tone"] == to.AMBER
    assert overview["blocking_count"] == 0
    assert overview["primary_action"]["destination"] == "review"


def test_completed_review_reaches_ready_for_delivery_prep():
    overview = to.derive_task_overview_state(_reviewed_state(2))

    assert overview["lifecycle"] == to.READY_FOR_DELIVERY_PREP
    assert overview["tone"] == to.GREEN
    assert overview["label"] == "可以准备交付"
    assert overview["detail"] == "2 / 2 段已翻译，没有必须处理的问题。"
    assert overview["primary_action"]["label"] == "准备交付"


def test_suggestions_are_amber_attention_and_never_blocking():
    state = _state(1, targets=["短"])
    state["paras"] = ["A" * 200]
    state["pairs"][0]["source"] = "A" * 200

    overview = to.derive_task_overview_state(state)

    assert overview["lifecycle"] == to.READY_FOR_DELIVERY_PREP
    assert overview["tone"] == to.AMBER
    assert overview["suggestion_count"] == 1
    assert overview["blocking_count"] == 0
    assert "不阻断交付" in overview["detail"] or "不会阻止交付" in overview["detail"]
    assert "必须处理" not in overview["detail"].replace("0 项必须处理", "")
    assert overview["primary_action"]["kind"] == "prepare_delivery"
    assert [action["label"] for action in overview["secondary_actions"]] == ["查看 1 项建议"]


def test_report_preparation_is_active_work_not_delivery_ready():
    state = _report_state()

    overview = to.derive_task_overview_state(state)

    assert overview["lifecycle"] == to.PREPARING_DELIVERY
    assert overview["tone"] == to.BLUE
    assert overview["delivery_ready"] is False


def test_delivery_ready_requires_generated_and_checked_assets():
    state = _report_state()

    overview = to.derive_task_overview_state(
        state, facts=dict(_REPORT_READY_FACTS))

    assert overview["lifecycle"] == to.DELIVERY_READY
    assert overview["tone"] == to.GREEN
    assert overview["label"] == "可以正式交付"
    assert overview["delivery_ready"] is True
    assert overview["primary_action"]["label"] == "生成冻结交付"


def test_report_task_without_generated_report_never_claims_delivery_ready():
    state = _report_state(p3_done=False, report_status="draft")

    overview = to.derive_task_overview_state(
        state, facts={**_REPORT_READY_FACTS,
                      "artifact_status": {"report": "missing"}})

    assert overview["lifecycle"] != to.DELIVERY_READY
    assert overview["delivery_ready"] is False


def test_delivered_only_when_the_snapshot_matches_the_working_version():
    state = _state(1)

    assert to.derive_task_overview_state(state)["lifecycle"] == \
        to.READY_FOR_DELIVERY_PREP

    delivered = to.derive_task_overview_state(
        state, facts={"snapshot_current": True, "snapshot_version": 3})

    assert delivered["lifecycle"] == to.DELIVERED
    assert delivered["tone"] == to.GREEN
    assert delivered["label"] == "已交付"
    assert delivered["snapshot_version"] == 3


def test_diverged_working_version_returns_to_delivery_preparation():
    overview = to.derive_task_overview_state(
        _state(1), facts={"snapshot_diverged": True, "snapshot_version": 2})

    assert overview["lifecycle"] == to.NEEDS_ATTENTION
    assert overview["tone"] == to.AMBER
    assert overview["reason"] == "outdated_delivery"
    assert overview["primary_action"]["label"] == "准备新的交付版本"


def test_delivery_gate_failure_is_red():
    overview = to.derive_task_overview_state(
        _report_state(),
        facts={"compliance": {"status": "fail", "counts": {"fail": 2}}})

    assert overview["lifecycle"] == to.NEEDS_ATTENTION
    assert overview["tone"] == to.RED
    assert overview["blocking_count"] == 0
    assert "2 项合规检查未通过" in overview["detail"]


def test_runtime_failure_and_interruption():
    state = _state(1, targets=[""])
    state["p2_done"] = False

    failed = to.derive_task_overview_state(
        state, facts={"runtime_status": "failed", "runtime_label": "运行失败"})
    stopped = to.derive_task_overview_state(
        state, facts={"runtime_status": "interrupted"})

    assert failed["lifecycle"] == to.NEEDS_ATTENTION and failed["tone"] == to.RED
    assert failed["primary_action"]["label"] == "继续处理"
    assert stopped["lifecycle"] == to.NEEDS_ATTENTION and stopped["tone"] == to.AMBER
    assert stopped["primary_action"]["label"] == "继续处理"


def test_runtime_active_before_segments_started():
    state = {}  # fresh task, 0 segments, p1_done not set
    running = to.derive_task_overview_state(
        state, facts={
            "runtime_status": "running",
            "runtime_label": "正在运行",
            "runtime_stage": "LLM 原文纠错与断行整理",
        })
    assert running["lifecycle"] == to.TRANSLATING
    assert running["tone"] == to.BLUE
    assert running["label"] == "正在运行"
    assert "LLM 原文纠错与断行整理" in running["detail"]
    assert running["stages"][0]["state"] == to.CURRENT
    assert running["stages"][0]["detail"] == "正在解析原文"


def test_runtime_cancelling_state():
    state = {}
    cancelling = to.derive_task_overview_state(
        state, facts={
            "runtime_status": "cancelling",
            "runtime_label": "正在取消",
        })
    assert cancelling["lifecycle"] == to.TRANSLATING
    assert cancelling["tone"] == to.AMBER
    assert cancelling["label"] == "正在取消"


def test_lifecycle_labels_never_use_delivery_ready_wording_for_preparation():
    preparation = to.derive_task_overview_state(_state(1))
    ready = to.derive_task_overview_state(
        _report_state(), facts=dict(_REPORT_READY_FACTS))

    assert preparation["lifecycle"] != ready["lifecycle"]
    assert preparation["label"] != ready["label"]
    assert "Ready for delivery" not in preparation["label"]
    assert preparation["label"] == to.LIFECYCLE_LABELS[to.READY_FOR_DELIVERY_PREP]
    assert ready["label"] == to.LIFECYCLE_LABELS[to.DELIVERY_READY]


# ---------------- optional stage states ----------------

def test_disabled_review_stage_is_skipped_not_completed():
    overview = to.derive_task_overview_state(_state(2))
    review = next(stage for stage in overview["stages"] if stage["id"] == "review")

    assert review["state"] == to.SKIPPED
    assert review["glyph"] == "—"
    assert review["tone"] == to.GRAY
    assert review["required"] is False
    assert "独立审校未启用" in overview["disabled_summary"]


def test_disabled_review_never_reports_review_progress():
    overview = to.derive_task_overview_state(_state(2))

    assert overview["review"]["enabled"] is False
    assert overview["review"]["ready"] is True
    assert all(not (item["id"] == "review" and item["state"] == to.COMPLETED)
               for item in overview["capabilities"])


def test_enabled_review_stage_can_be_completed():
    overview = to.derive_task_overview_state(_reviewed_state(2))
    review = next(stage for stage in overview["stages"] if stage["id"] == "review")

    assert review["state"] == to.COMPLETED
    assert review["glyph"] == "✓"
    assert "独立审校未启用" not in overview["disabled_summary"]


def test_failed_review_stage_is_blocked():
    state = _state(1, review_required=True)
    event = _review_event(0)
    event["decision"] = "failed"
    event["completion_receipt"] = {"status": "failed", "reviewed_segment_ids": [0]}
    state["review_evidence"] = [event]

    overview = to.derive_task_overview_state(state)
    review = next(stage for stage in overview["stages"] if stage["id"] == "review")

    assert review["state"] == to.BLOCKED
    assert review["glyph"] == "!"


def test_report_is_auxiliary_and_not_a_pipeline_stage():
    overview = to.derive_task_overview_state(_report_state())

    labels = [stage["label"] for stage in overview["stages"]]
    assert "术语提取" not in labels
    assert "报告草稿" not in labels
    assert overview["pipeline"] == list(to.PIPELINE_STAGES)


def test_disabled_report_is_skipped_in_capabilities():
    overview = to.derive_task_overview_state(_state(1))
    report = next(item for item in overview["capabilities"]
                  if item["id"] == to.CAP_REPORT)

    assert report["enabled"] is False
    assert report["state"] == to.SKIPPED
    assert report["detail"] == "未启用"
    assert "报告未启用" in overview["disabled_summary"]


def test_stage_states_and_glyphs_stay_in_sync():
    for state, tone in to.STAGE_TONES.items():
        assert tone in to.TONES
    assert to.STAGE_GLYPHS[to.SKIPPED] == "—"
    assert to.STAGE_GLYPHS[to.COMPLETED] == "✓"
    assert to.STAGE_GLYPHS[to.BLOCKED] == "!"
    assert to.STAGE_TONES[to.SKIPPED] == to.GRAY
    assert to.STAGE_TONES[to.BLOCKED] == to.RED
    assert to.STAGE_TONES[to.COMPLETED] == to.GREEN
    assert to.STAGE_TONES[to.CURRENT] == to.BLUE


# ---------------- colour + surface consistency ----------------

def test_every_surface_maps_one_tone_to_one_token():
    for tone in to.TONES:
        tokens = to.SURFACE_TONES[tone]
        assert set(tokens) == {"badge", "verdict", "nav", "step", "card"}
    # green is the only tone that may render a completed/green check.
    assert to.surface_token("badge", to.GREEN) == "success"
    assert to.surface_token("nav", to.GREEN) == "done"
    assert to.surface_token("step", to.STAGE_TONES[to.BLOCKED]) == "is-blocked"
    assert to.surface_token("step", to.GRAY) == "is-pending"


def test_unknown_tone_and_surface_degrade_to_gray():
    assert to.surface_token("verdict", "chartreuse") == \
        to.surface_token("verdict", to.GRAY)
    assert to.surface_token("nope", to.GREEN) == ""


def test_canonical_tone_matches_lifecycle_palette():
    """green=完成/可交付，blue=进行中，amber=建议，red=阻断，gray=待开始。"""
    cases = [
        ({}, {}, to.DRAFT, to.GRAY),
        (_state(1), {}, to.READY_FOR_DELIVERY_PREP, to.GREEN),
        (_report_state(), {}, to.PREPARING_DELIVERY, to.BLUE),
        (_report_state(), dict(_REPORT_READY_FACTS), to.DELIVERY_READY, to.GREEN),
        (_state(1), {"snapshot_current": True, "snapshot_version": 1},
         to.DELIVERED, to.GREEN),
        (_state(1, targets=["TODO"]), {}, to.NEEDS_ATTENTION, to.RED),
        (_state(1, review_required=True), {}, to.NEEDS_ATTENTION, to.AMBER),
    ]
    for state, facts, lifecycle, tone in cases:
        overview = to.derive_task_overview_state(state, facts=facts)
        assert (overview["lifecycle"], overview["tone"]) == (lifecycle, tone)

    # 只有 DELIVERY_READY / DELIVERED 可以声称"最终交付就绪"。
    assert to.DELIVERY_LIFECYCLE_STATES == {to.DELIVERY_READY, to.DELIVERED}


# ---------------- single source of truth ----------------

def test_planner_verdict_reads_the_canonical_derivation():
    from folith.translation_planner import workspace_progress

    state = _state(2)
    overview = to.derive_task_overview_state(state)
    verdict = workspace_progress(state)["verdict"]

    assert verdict["label"] == overview["label"]
    assert verdict["tone"] == overview["tone"]
    assert verdict["lifecycle"] == overview["lifecycle"]


def test_delivery_gate_blocking_is_never_overlooked():
    state = _state(1)
    state["findings"] = [{
        "type": "review", "finding_id": "finding-blocking", "severity": "blocking",
        "status": "open", "segment_index": 0, "summary": "译文与原文不一致",
        "requires_human_confirmation": True,
    }]

    overview = to.derive_task_overview_state(state)

    assert delivery.unresolved_blocking(state)
    assert overview["blocking_count"] >= 1
    assert overview["tone"] == to.RED
    assert overview["primary_action"]["kind"] == "handle_blocking"


def test_derivation_is_read_only_and_deterministic():
    state = _reviewed_state(2)
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)

    first = to.derive_task_overview_state(state)
    second = to.derive_task_overview_state(state)

    assert first == second
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before


@pytest.mark.parametrize("value", [None, {}, [], "text", 0, {"pairs": "bad"},
                                   {"paras": None, "pairs": None}])
def test_malformed_state_is_total(value):
    overview = to.derive_task_overview_state(value)

    assert overview["lifecycle"] in to.LIFECYCLE_STATES
    assert overview["tone"] in to.TONES
    assert overview["label"]
    assert overview["primary_action"]["destination"]


def test_lifecycle_rank_orders_states():
    ranks = [to.lifecycle_rank(state) for state in to.LIFECYCLE_STATES]
    assert ranks == sorted(ranks)
    assert to.lifecycle_rank("nonsense") == -1


# ---------------- 已删除的「概览」路由不再是任何动作的落点 ----------------

@pytest.mark.parametrize("facts", [
    {"runtime_status": "interrupted", "runtime_label": "上次运行已中断"},
    {"runtime_status": "failed", "runtime_label": "当前步骤失败"},
    {"runtime_status": "stalled", "runtime_label": "暂无运行信号"},
])
def test_runtime_actions_target_the_translation_workbench(facts):
    """中断/失败/停滞的恢复动作必须在翻译工作台上可达。

    任务工作台已经没有 `overview` 这一级；恢复动作由该页 Banner 的运行区提供，
    所以 canonical 状态不允许再把用户送进一个不存在的页面。

    前置条件：任务尚未业务完成（否则运行状态不参与判定）——用"有段落但译文为空"
    的真实形态，而不是已译完的 state。
    """
    incomplete = {
        "p1_done": True,
        "p2_done": False,
        "stage": "TRANSLATING",
        "paras": ["Source 0", "Source 1"],
        "pairs": [{"source": "Source 0", "target": ""},
                  {"source": "Source 1", "target": ""}],
        "findings": [],
        "review_evidence": [],
        "human_actions": [],
    }
    overview = to.derive_task_overview_state(incomplete, facts=facts)

    assert overview["reason"] in {"runtime_failed", "runtime_stopped"}
    assert overview["primary_action"]["destination"] == "translation"


def test_no_surface_targets_the_removed_overview_route():
    overview = to.derive_task_overview_state(_state(1))

    destinations = {overview["primary_action"].get("destination")}
    destinations.update(action.get("destination")
                        for action in overview["secondary_actions"])
    destinations.update(stage.get("destination") for stage in overview["stages"])
    assert "overview" not in destinations
