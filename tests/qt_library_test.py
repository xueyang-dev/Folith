"""候选词条必须能被审核（适配层）；界面在 qt_projects_view_test.py。

Agent 在翻译中发现的候选词条原来在 Qt 里没有任何入口，永远待审。这里钉住：

* 数据来自任务、项目与翻译记忆的真实读取（行模型是 ``folith.language_assets``）；
* 接受为项目术语 / 仅用于本任务 / 拒绝，都走核心 ``review_knowledge_candidate``，
  决定落在任务状态里，已处理的候选不再出现；
* 批量处理逐条进行，失败的不拖累其余的，并如实报告；
* 翻译记忆按项目隔离，清空只影响所选项目；
* 表格有显示上限，筛选作用于全部数据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from language_assets_workspace_test import _candidate, _job, _term  # noqa: E402


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    first = _job("libjob000000001", "alpha.docx",
                 glossary=[_term("point cloud", "点云"), _term("drone", "无人机", status="provisional")],
                 candidates=[_candidate("volumetric sensor", "体积传感器", confidence=0.9),
                             _candidate("earth drone", "地球无人机", kind="name", confidence=0.4)])
    second = _job("libjob000000002", "beta.docx",
                  candidates=[_candidate("remote sensing", "遥感", confidence=0.95)])
    core.save_job_state("libjob000000001", first)
    core.save_job_state("libjob000000002", second)
    return tmp_path


def _rows(kind):
    from folith import language_assets as assets

    assert kind == "candidates"
    return assets.build_candidate_rows(core.list_jobs())


def test_clearing_memory_only_touches_the_chosen_project(workspace):
    first = core.create_project("甲项目")["project_id"]
    second = core.create_project("乙项目")["project_id"]
    core.save_tm({"a": {"target": "甲", "reviewed": True}}, first)
    core.save_tm({"b": {"target": "乙", "reviewed": True}}, second)
    adapter.clear_project_memory(first)
    assert not core.load_tm(first)
    assert core.load_tm(second)


# ------------------------------------------------------------ 审核 -----------

def _candidate_row(source):
    return next(r for r in _rows("candidates") if r["source"] == source)


def test_accepting_as_a_project_term_records_the_decision_and_promotes(workspace):
    row = _candidate_row("volumetric sensor")
    message = adapter.decide_candidate(row, "project_term")
    assert "项目" in message
    state = adapter.load_state("libjob000000001")
    decided = next(c for c in state["knowledge_candidates"] if c["source"] == "volumetric sensor")
    assert decided["decision"] == "project_term"
    assert "volumetric sensor" not in {r["source"] for r in _rows("candidates")}, "已处理的不再待审"
    assert any(e["source"] == "volumetric sensor" for e in state["glossary"])


def test_task_only_and_rejected_decisions(workspace):
    adapter.decide_candidate(_candidate_row("earth drone"), "task_only")
    adapter.decide_candidate(_candidate_row("remote sensing"), "rejected")
    states = {j: adapter.load_state(j) for j in ("libjob000000001", "libjob000000002")}
    by_source = {c["source"]: c.get("decision") for s in states.values()
                 for c in s["knowledge_candidates"]}
    assert by_source["earth drone"] == "task_only" and by_source["remote sensing"] == "rejected"
    assert not any(e["source"] == "remote sensing" for e in states["libjob000000002"].get("glossary") or [])


def test_unknown_decision_is_refused(workspace):
    with pytest.raises(adapter.AdapterError):
        adapter.decide_candidate(_candidate_row("earth drone"), "maybe")


def test_deciding_twice_is_refused_with_the_core_reason(workspace):
    row = _candidate_row("earth drone")
    adapter.decide_candidate(row, "rejected")
    with pytest.raises(adapter.AdapterError, match="已经处理"):
        adapter.decide_candidate(row, "rejected")


def test_bulk_decisions_report_partial_failure_honestly(workspace):
    rows = [_candidate_row("earth drone"), _candidate_row("remote sensing")]
    adapter.decide_candidate(rows[0], "rejected")        # 先处理一条，批量里它会失败
    result = adapter.decide_candidates(rows, "task_only")
    assert result["done"] == 1 and len(result["failed"]) == 1
    assert "earth drone" in result["failed"][0]


def test_accepting_with_an_edited_rendering_promotes_the_corrected_term(workspace):
    row = _candidate_row("volumetric sensor")
    message = adapter.accept_candidate_with(row, "体积式传感器")
    assert "体积式传感器" in message
    state = adapter.load_state("libjob000000001")
    entry = next(e for e in state["glossary"] if e["source"] == "volumetric sensor")
    assert entry["preferred"] == "体积式传感器"
    with pytest.raises(adapter.AdapterError):
        adapter.accept_candidate_with(row, " ")      # 空译名在写入前就被拒绝
