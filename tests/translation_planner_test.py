"""Deterministic findings/progress contract for the read-only translation planner."""
from __future__ import annotations

import json

import pytest

from folith.task_overview import LIFECYCLE_STATES as TASK_LIFECYCLES
from folith.task_overview import TONES as TASK_TONES
from folith.translation_planner import plan_findings, workspace_progress


# ---------------- fixtures ----------------

LONG_SOURCE = (
    "This deliberately long source paragraph exists only so the planner can "
    "flag a suspiciously short target for the coverage dimension."
)


def _terminology_state(status: str = "frozen") -> dict:
    return {
        "p2_done": True,
        "pairs": [
            {"source": "The sensorium responds.", "target": "感知中枢作出反应。"},
            {"source": "A sensorium model.", "target": "一个感觉中枢模型。"},
            {"source": "The sensorium again.", "target": "感知中枢再次出现。"},
        ],
        "glossary": [
            {"id": "term-1", "source": "sensorium", "preferred": "感知中枢",
             "status": status},
        ],
    }


def _rich_state() -> dict:
    return {
        "p2_done": True,
        "translation_core_review_required": True,
        "paras": ["Alpha sensorium [1].", "Beta sensorium TODO.", LONG_SOURCE],
        "pairs": [
            {"source": "Alpha sensorium [1].", "target": "阿尔法感知中枢。",
             "reviewed": True},
            {"source": "Beta sensorium TODO.", "target": "贝塔 TODO。",
             "reviewed": True},
            {"source": LONG_SOURCE, "target": "太短", "reviewed": False},
        ],
        "glossary": [
            {"id": "term-1", "source": "sensorium", "preferred": "感知中枢",
             "status": "frozen"},
        ],
        "delivery_validation": {
            "issues": [
                {"code": "transport_wrapper", "segment_index": 1,
                 "message": "译文仍是 JSON/Markdown transport wrapper"},
            ],
        },
    }


# ---------------- terminology ----------------

def test_frozen_terminology_inconsistency_is_blocking():
    findings = plan_findings(_terminology_state("frozen"))
    terminology = [item for item in findings if item["kind"] == "terminology"]
    assert len(terminology) == 1
    finding = terminology[0]
    assert finding["id"] == "terminology:sensorium"
    assert finding["severity"] == "blocking"
    assert finding["segments"] == [1]
    assert finding["action"] == "统一译法"
    assert "sensorium" in finding["title"]
    assert "3 处" in finding["title"]


def test_locked_terminology_inconsistency_is_actionable():
    finding = plan_findings(_terminology_state("locked"))[0]
    assert finding["id"] == "terminology:sensorium"
    assert finding["severity"] == "actionable"
    assert finding["segments"] == [1]


def test_unused_preferred_term_reports_mirror_case():
    state = {
        "pairs": [
            {"source": "The sensorium responds.", "target": "该结构作出反应。"},
            {"source": "A sensorium model.", "target": "一个模型。"},
        ],
        "glossary": [
            {"id": "term-1", "source": "sensorium", "preferred": "感知中枢",
             "status": "locked"},
        ],
    }
    findings = plan_findings(state)
    assert [item["id"] for item in findings] == ["terminology_unused:sensorium"]
    finding = findings[0]
    assert finding["kind"] == "terminology"
    assert finding["severity"] == "actionable"
    assert finding["action"] == "检查术语"
    assert finding["segments"] == [0, 1]


def test_auto_terms_are_checked_even_without_glossary():
    state = {
        "pairs": [
            {"source": "The sensorium responds.", "target": "感知中枢作出反应。"},
            {"source": "A sensorium model.", "target": "一个感觉中枢模型。"},
        ],
        "auto_terms": {"sensorium": "感知中枢"},
    }
    finding = plan_findings(state)[0]
    assert finding["id"] == "terminology:sensorium"
    assert finding["severity"] == "actionable"
    assert finding["segments"] == [1]


def test_consistent_terminology_reports_nothing():
    state = _terminology_state("frozen")
    state["pairs"][1]["target"] = "一个感知中枢模型。"
    assert plan_findings(state) == []


# ---------------- citations ----------------

def test_missing_numeric_citation_marker_is_detected():
    state = {"pairs": [
        {"source": "Deep learning works [1].", "target": "深度学习有效。"},
        {"source": "See also [2].", "target": "另见 [2]。"},
    ]}
    findings = [item for item in plan_findings(state) if item["kind"] == "citation"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["segments"] == [0]
    assert finding["severity"] == "actionable"
    assert finding["action"] == "补回引用标注"
    assert "[1]" in finding["title"]
    assert "[1]" in finding["detail"]


def test_citation_range_and_list_markers_are_named():
    state = {"pairs": [{"source": "Results [1-3] and [4,5].", "target": "结果。"}]}
    finding = plan_findings(state)[0]
    assert finding["kind"] == "citation"
    assert "[1-3]" in finding["detail"]
    assert "[4,5]" in finding["detail"]


def test_fullwidth_author_year_marker_matches_halfwidth_target():
    state = {"pairs": [
        {"source": "见（Smith，2019）。", "target": "See (Smith, 2019)."},
    ]}
    assert plan_findings(state) == []


def test_lost_author_year_marker_is_detected():
    state = {"pairs": [
        {"source": "As shown (Smith, 2019).", "target": "如前所述。"},
    ]}
    finding = plan_findings(state)[0]
    assert finding["kind"] == "citation"
    assert "(Smith, 2019)" in finding["detail"]


def test_citation_findings_are_capped():
    pairs = [{"source": f"Claim [{index}].",
              "target": "论断。"} for index in range(1, 11)]
    findings = [item for item in plan_findings({"pairs": pairs}, limit=100)
                if item["kind"] == "citation"]
    assert len(findings) == 6
    assert [item["segments"] for item in findings] == [[0], [1], [2], [3], [4], [5]]


# ---------------- placeholders ----------------

def test_placeholder_leftovers_and_todo_severity():
    state = {"pairs": [
        {"source": "Hi {name}", "target": "你好 {name}"},
        {"source": "Do it", "target": "请完成 TODO"},
        {"source": "Stats", "target": "共 %s 项"},
        {"source": "Book", "target": "Lorem ipsum dolor"},
        {"source": "Raw", "target": "TRANSLATE ME"},
        {"source": "Var", "target": "{{var}}"},
        {"source": "Mark", "target": "XXX"},
    ]}
    findings = {item["id"]: item for item in plan_findings(state)}
    assert findings["placeholder:0"]["severity"] == "actionable"
    assert findings["placeholder:0"]["kind"] == "placeholder"
    assert findings["placeholder:1"]["severity"] == "blocking"
    assert "TODO" in findings["placeholder:1"]["title"]
    assert findings["placeholder:2"]["severity"] == "actionable"
    assert findings["placeholder:3"]["severity"] == "blocking"
    assert findings["placeholder:4"]["severity"] == "blocking"
    assert findings["placeholder:5"]["severity"] == "actionable"
    assert findings["placeholder:6"]["severity"] == "actionable"
    assert findings["placeholder:1"]["segments"] == [1]


# ---------------- structure ----------------

def test_transport_wrapper_structure_finding_from_delivery_validation():
    state = {
        "pairs": [
            {"source": "Alpha", "target": "阿尔法"},
            {"source": "Beta", "target": "```json\n{\"text\": \"贝塔\"}\n```"},
        ],
        "delivery_validation": {
            "issues": [
                {"code": "empty_target", "segment_index": 0,
                 "message": "正文段落译文不能为空"},
                {"code": "transport_wrapper", "segment_index": 1,
                 "message": "译文仍是 JSON/Markdown transport wrapper"},
            ],
        },
    }
    findings = [item for item in plan_findings(state) if item["kind"] == "structure"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["id"] == "structure:1"
    assert finding["segments"] == [1]
    assert finding["severity"] == "blocking"
    assert finding["title"] == "第 2 段译文结构异常"
    assert finding["action"] == "重译该段"


# ---------------- coverage ----------------

def test_short_target_is_actionable_coverage_finding():
    assert len(LONG_SOURCE) >= 80
    state = {"pairs": [{"source": LONG_SOURCE, "target": "太短"}]}
    finding = plan_findings(state)[0]
    assert finding["id"] == "coverage:short"
    assert finding["kind"] == "coverage"
    assert finding["severity"] == "actionable"
    assert finding["segments"] == [0]
    assert "偏短" in finding["title"]


def test_empty_target_is_informational_only_when_translation_is_complete():
    state = {
        "p2_done": True,
        "pairs": [
            {"source": "Alpha", "target": "阿尔法"},
            {"source": "Beta", "target": ""},
        ],
    }
    finding = plan_findings(state)[0]
    assert finding["id"] == "coverage:empty"
    assert finding["kind"] == "coverage"
    assert finding["severity"] == "informational"
    assert finding["segments"] == [1]

    state["p2_done"] = False
    assert plan_findings(state) == []


# ---------------- review ----------------

def test_pending_review_is_aggregated_and_informational():
    state = {
        "translation_core_review_required": True,
        "pairs": [
            {"source": "Alpha", "target": "阿尔法", "reviewed": True},
            {"source": "Beta", "target": "贝塔"},
            {"source": "Gamma", "target": "伽马", "human_edited": True},
        ],
    }
    findings = [item for item in plan_findings(state) if item["kind"] == "review"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["id"] == "review:pending"
    assert finding["severity"] == "informational"
    assert finding["segments"] == [1]
    assert "1 段" in finding["title"]


def test_review_finding_is_absent_when_review_not_required():
    state = {"pairs": [{"source": "Alpha", "target": "阿尔法"}]}
    assert plan_findings(state) == []


# ---------------- ordering, limit, robustness ----------------

def _ordering_state() -> dict:
    return {
        "translation_core_review_required": True,
        "pairs": [
            {"source": "Alpha.", "target": "阿尔法。", "reviewed": True},
            {"source": "Beta TODO.", "target": "贝塔 TODO。", "reviewed": True},
            {"source": "Gamma [2].", "target": "伽马。", "reviewed": True},
            {"source": "Delta.", "target": "德尔塔。", "reviewed": False},
            {"source": "Epsilon TBD.", "target": "艾普西龙 TBD。", "reviewed": True},
        ],
        "delivery_validation": {
            "issues": [
                {"code": "transport_wrapper", "segment_index": 3,
                 "message": "译文仍是 JSON/Markdown transport wrapper"},
            ],
        },
    }


def test_findings_are_ordered_by_severity_then_segment():
    ids = [item["id"] for item in plan_findings(_ordering_state(), limit=100)]
    assert ids == [
        "placeholder:1",   # blocking, segment 1
        "structure:3",     # blocking, segment 3
        "placeholder:4",   # blocking, segment 4
        "citation:2",      # actionable, segment 2
        "review:pending",  # informational, segment 3
    ]


def test_limit_truncates_after_ordering():
    state = _ordering_state()
    assert [item["id"] for item in plan_findings(state, limit=2)] == [
        "placeholder:1", "structure:3"]
    assert plan_findings(state, limit=0) == []
    assert plan_findings(state, limit=-5) == []
    assert len(plan_findings(state, limit=100)) == 5


def test_finding_shape_contract():
    findings = plan_findings(_rich_state(), limit=100)
    assert {item["kind"] for item in findings} == {
        "terminology", "citation", "placeholder", "structure", "coverage", "review"}
    for finding in findings:
        assert set(finding) == {"id", "kind", "severity", "title", "detail",
                                "segments", "action"}
        assert finding["kind"] in {"terminology", "citation", "placeholder",
                                   "structure", "review", "coverage"}
        assert finding["severity"] in {"blocking", "actionable", "informational"}
        assert isinstance(finding["id"], str) and finding["id"]
        assert 0 < len(finding["title"]) <= 60
        assert 0 < len(finding["detail"]) <= 160
        assert isinstance(finding["segments"], list)
        assert finding["segments"] == sorted(set(finding["segments"]))
        assert all(isinstance(index, int) for index in finding["segments"])
        assert isinstance(finding["action"], str) and finding["action"]


@pytest.mark.parametrize("state", [
    None,
    {},
    {"pairs": [None, 3]},
    {"pairs": "not-a-list", "paras": None},
    ["not", "a", "mapping"],
    {
        "pairs": [{"source": None, "target": None}, 5, "row"],
        "glossary": [None, "entry", {"source": None}],
        "auto_terms": {"": ""},
        "delivery_validation": {"issues": [None, 1, {"code": None}]},
    },
])
def test_malformed_state_is_total(state):
    findings = plan_findings(state)
    assert findings == []
    progress = workspace_progress(state)
    assert set(progress) == {"translation", "terminology", "review", "issues",
                             "verdict"}
    assert progress["translation"]["done"] == 0
    # 畸形状态只承诺"不抛异常 + 给出合法状态"，不承诺某个具体生命周期。
    assert progress["verdict"]["tone"] in TASK_TONES
    assert progress["verdict"]["lifecycle"] in TASK_LIFECYCLES


def test_planner_is_read_only_and_deterministic():
    state = _rich_state()
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    first = plan_findings(state)
    second = plan_findings(state, job_id="job-42")
    assert first == second
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before
    assert workspace_progress(state, job_id="job-42") == workspace_progress(state)
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before


# ---------------- workspace progress ----------------

def test_workspace_progress_clean_ready_verdict():
    state = {"p1_done": True, "p2_done": True, "pairs": [
        {"source": "Alpha", "target": "阿尔法"},
        {"source": "Beta", "target": "贝塔"},
    ]}
    progress = workspace_progress(state)
    assert progress["translation"] == {"done": 2, "total": 2, "label": "2 / 2",
                                       "complete": True}
    assert progress["terminology"] == {"done": 0, "total": 0, "label": "不适用",
                                       "complete": True, "applicable": False}
    assert progress["review"] == {"done": 0, "total": 0, "label": "不适用",
                                  "complete": True, "applicable": False}
    assert progress["issues"] == {"count": 0, "blocking": 0, "actionable": 0,
                                  "informational": 0, "label": "未发现问题"}
    # READY_FOR_DELIVERY_PREP 的措辞只能是"可以准备交付"：
    # 它不等于 DELIVERY_READY（"Ready for delivery"）。
    assert progress["verdict"] == {
        "tone": "green",
        "label": "可以准备交付",
        "detail": "2 / 2 段已翻译，没有必须处理的问题。",
        "lifecycle": "ready_for_delivery_prep",
        "reason": "ready_for_delivery_prep",
    }


def test_workspace_progress_ready_claims_review_only_when_applicable():
    state = {
        "p1_done": True,
        "p2_done": True,
        "translation_core_review_required": True,
        "pairs": [{"source": "Alpha", "target": "阿尔法", "reviewed": True}],
        "glossary": [{"id": "term-1", "source": "Alpha", "preferred": "阿尔法",
                      "status": "locked"}],
    }
    progress = workspace_progress(state)
    assert progress["terminology"]["applicable"] is True
    assert progress["terminology"]["label"] == "1 / 1"
    assert progress["review"]["applicable"] is True
    assert progress["review"]["label"] == "1 / 1"
    # pair["reviewed"] 不是 Translation Core 的审校事实：没有审校事件时
    # canonical 状态不会声称审校完成，也不会进入交付准备。
    assert progress["verdict"]["tone"] == "amber"
    assert progress["verdict"]["label"] == "需要完成独立审校"
    assert progress["verdict"]["lifecycle"] == "needs_attention"


def test_workspace_progress_counts_terminology_and_review_dimensions():
    state = {
        "quality_mode": True,
        "translation_core_review_required": True,
        "p2_done": True,
        "pairs": [
            {"source": "Alpha", "target": "阿尔法", "reviewed": True},
            {"source": "Beta", "target": "贝塔", "human_edited": True},
            {"source": "Gamma", "target": "伽马", "reviewed": False},
            {"source": "Delta", "target": ""},
        ],
        "glossary": [
            {"id": "t1", "source": "Alpha", "preferred": "阿尔法", "status": "locked"},
            {"id": "t2", "source": "Beta", "preferred": "贝塔", "status": "frozen"},
            {"id": "t3", "source": "Gamma", "preferred": "伽马", "status": "candidate"},
        ],
    }
    progress = workspace_progress(state)
    assert progress["translation"] == {"done": 3, "total": 4, "label": "3 / 4",
                                       "complete": False}
    assert progress["terminology"]["applicable"] is True
    assert progress["terminology"]["done"] == 2
    assert progress["terminology"]["total"] == 3
    assert progress["terminology"]["label"] == "2 / 3"
    assert progress["terminology"]["complete"] is False
    assert progress["review"]["applicable"] is True
    assert progress["review"]["done"] == 2
    assert progress["review"]["total"] == 3
    assert progress["review"]["label"] == "2 / 3"
    assert progress["review"]["complete"] is False
    assert progress["verdict"]["tone"] == "blue"
    assert progress["verdict"]["label"] == "正在翻译"
    assert progress["verdict"]["lifecycle"] == "translating"


def test_workspace_progress_verdict_is_blocked_for_frozen_term():
    progress = workspace_progress(_terminology_state("frozen"))
    assert progress["translation"]["complete"] is True
    assert progress["terminology"]["applicable"] is True
    assert progress["issues"]["blocking"] == 1
    assert progress["verdict"]["tone"] == "red"
    assert progress["verdict"]["label"] == "需要处理问题"
    assert progress["verdict"]["lifecycle"] == "needs_attention"


def test_workspace_progress_attention_for_actionable_issue():
    state = {"p1_done": True, "p2_done": True,
             "paras": [LONG_SOURCE],
             "pairs": [{"source": LONG_SOURCE, "target": "太短"}]}
    progress = workspace_progress(state)
    assert progress["translation"]["complete"] is True
    assert progress["issues"]["actionable"] == 1
    assert progress["verdict"]["tone"] == "amber"
    assert progress["verdict"]["label"] == "可以准备交付"
    assert progress["verdict"]["lifecycle"] == "ready_for_delivery_prep"


def test_progress_total_falls_back_to_paras_without_pairs():
    progress = workspace_progress({"paras": ["Alpha", "Beta", "Gamma"]})
    assert progress["translation"] == {"done": 0, "total": 3, "label": "0 / 3",
                                       "complete": False}
    assert progress["verdict"]["tone"] == "blue"
    assert progress["verdict"]["lifecycle"] == "translating"
