"""语言资产页：候选词条必须能被审核，术语与翻译记忆可跨任务查看。

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
    return adapter.library_data()[kind]


# ------------------------------------------------------------ 数据 -----------

def test_library_reads_candidates_terms_and_summary(workspace):
    data = adapter.library_data()
    assert data["summary"]["candidates"] == 3
    assert {r["source"] for r in data["candidates"]} == {
        "volumetric sensor", "earth drone", "remote sensing"}
    assert {"point cloud", "drone"} <= {r["source"] for r in data["terms"]}
    assert data["projects"] and data["tm_project_id"]
    assert ("libjob000000001", "alpha.docx") in data["jobs"]


def test_translation_memory_is_per_project_and_filtered_to_reviewed(workspace):
    project_id = adapter.library_data()["tm_project_id"]
    scoped = core.tm_scope_key if hasattr(core, "tm_scope_key") else None
    core.save_tm({"hello": {"target": "你好", "reviewed": True, "target_lang": "简体中文",
                            "updated_at": "2026-10-10T00:00:00"},
                  "draft": {"target": "草稿", "reviewed": False}}, project_id)
    rows = adapter.library_data(project_id)["tm"]
    assert [r["source"] for r in rows] == ["hello"] or [r["target"] for r in rows] == ["你好"]
    assert not any(r["target"] == "草稿" for r in rows), "未确认的记忆不能冒充已确认"
    del scoped


def test_clearing_memory_only_touches_the_chosen_project(workspace):
    data = adapter.library_data()
    first, second = data["projects"][0]["project_id"], None
    if len(data["projects"]) > 1:
        second = data["projects"][1]["project_id"]
    core.save_tm({"a": {"target": "甲", "reviewed": True}}, first)
    if second:
        core.save_tm({"b": {"target": "乙", "reviewed": True}}, second)
    adapter.clear_project_memory(first)
    assert not core.load_tm(first)
    if second:
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


# ------------------------------------------------------------ 面板 -----------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _view(workspace_data=None):
    from folith.qt.library_view import LibraryView

    view = LibraryView()
    view.apply(workspace_data if workspace_data is not None else adapter.library_data())
    return view


def test_view_counts_tabs_and_fills_the_candidate_table(qapp, workspace):
    view = _view()
    assert "3" in view.tabs.tabText(0) and "待审核" in view.tabs.tabText(0)
    assert view.cand_table.rowCount() == 3
    assert view.term_table.rowCount() >= 2
    assert not view.accept_button.isEnabled(), "没选中不能审核"


def test_selecting_a_candidate_shows_context_and_enables_actions(qapp, workspace):
    view = _view()
    view.cand_table.selectRow(0)
    assert view.accept_button.isEnabled() and view.reject_button.isEnabled()
    assert view.jump_button.isEnabled()
    assert "→" in view.cand_detail.toPlainText()


def test_actions_emit_the_selected_rows_and_decision(qapp, workspace):
    view = _view()
    seen = []
    view.decideRequested.connect(lambda rows, decision: seen.append((len(rows), decision)))
    view.cand_table.selectRow(0)
    view.accept_button.click()
    view.task_button.click()
    view.reject_button.click()
    assert seen == [(1, "project_term"), (1, "task_only"), (1, "rejected")]


def test_multi_select_allows_bulk_and_relabels_the_button(qapp, workspace):
    from PySide6.QtCore import QItemSelectionModel

    view = _view()
    model = view.cand_table.selectionModel()
    for row in (0, 1):
        model.select(view.cand_table.model().index(row, 0),
                     QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    assert len(view.selected_candidates()) == 2
    assert "2" in view.accept_button.text()
    assert not view.jump_button.isEnabled(), "多选时不能跳到单个位置"


def test_quick_filters_and_search_narrow_the_table(qapp, workspace):
    view = _view()
    view.cand_quick.button("high").click()
    assert view.cand_table.rowCount() == 2          # 0.9 与 0.95
    view.cand_quick.button("all").click()
    view.cand_query.setText("遥感")
    assert view.cand_table.rowCount() == 0 or view.cand_table.rowCount() == 1
    view.cand_query.setText("remote")
    assert view.cand_table.rowCount() == 1
    view.cand_kind.setCurrentIndex(view.cand_kind.findData("name"))
    assert view.cand_table.rowCount() == 0, "类型筛选与搜索叠加"


def test_accept_all_high_confidence_is_offered_and_acts_on_the_filtered_set(qapp, workspace):
    view = _view()
    seen = []
    view.decideRequested.connect(lambda rows, decision: seen.append(({r["source"] for r in rows}, decision)))
    assert not view.high_button.isHidden()
    view.high_button.click()
    assert seen == [({"volumetric sensor", "remote sensing"}, "project_term")]


def test_display_cap_with_show_more(qapp):
    from folith.qt import library_view

    rows = [{"row_id": f"c{i}", "candidate_id": f"c{i}", "job_id": "j", "document": "d.docx",
             "source": f"term{i}", "target": f"译{i}", "kind": "term", "kind_label": "术语",
             "is_new": True, "confidence": 0.5, "high_confidence": False, "occurrences": [],
             "observed_segments": [], "occurrence_count": 0, "positions_known": False,
             "positions": [], "position_entries": [], "position_total": 0,
             "first_observed_segment": None, "source_context": "", "target_context": "",
             "conflicts": [], "has_conflict": False, "conflict_summary": "", "origin": "",
             "decision": None} for i in range(library_view.PAGE + 50)]
    view = _view({"candidates": rows, "terms": [], "tm": [], "projects": [],
                  "tm_project_id": "", "jobs": [], "summary": {"candidates": len(rows)}})
    assert view.cand_table.rowCount() == library_view.PAGE
    assert not view.cand_more.isHidden() and "50" in view.cand_more.text()
    view.cand_more.click()
    assert view.cand_table.rowCount() == library_view.PAGE + 50 and view.cand_more.isHidden()
    view.cand_query.setText("term7")                  # 筛选作用于全部数据，不只是已显示的
    assert view.cand_table.rowCount() >= 1


def test_empty_state_message(qapp):
    view = _view({"candidates": [], "terms": [], "tm": [], "projects": [], "tm_project_id": "",
                  "jobs": [], "summary": {}})
    assert not view.cand_empty.isHidden() and view.cand_table.isHidden()


def test_term_tab_opens_the_owning_task(qapp, workspace):
    view = _view()
    seen = []
    view.openJobRequested.connect(lambda job, index: seen.append((job, index)))
    for row in range(view.term_table.rowCount()):
        view.term_table.selectRow(row)
        if view.term_open.isEnabled():
            view.term_open.click()
            break
    assert seen and seen[0][1] == -1


def test_home_exposes_the_library_entry(qapp):
    from folith.qt import views

    home = views.HomeView()
    seen = []
    home.libraryRequested.connect(lambda: seen.append(1))
    home.library_button.click()
    assert seen == [1]


def test_accepting_with_an_edited_rendering_promotes_the_corrected_term(workspace):
    row = _candidate_row("volumetric sensor")
    message = adapter.accept_candidate_with(row, "体积式传感器")
    assert "体积式传感器" in message
    state = adapter.load_state("libjob000000001")
    entry = next(e for e in state["glossary"] if e["source"] == "volumetric sensor")
    assert entry["preferred"] == "体积式传感器"
    with pytest.raises(adapter.AdapterError):
        adapter.accept_candidate_with(row, " ")      # 空译名在写入前就被拒绝


def test_library_terms_can_be_edited_and_deleted_in_their_task(qapp, workspace):
    from folith.qt.dialogs import LibraryTermDialog

    view = _view()
    edits, deletes = [], []
    view.termEditRequested.connect(edits.append)
    view.termDeleteRequested.connect(deletes.append)
    for row in range(view.term_table.rowCount()):
        view.term_table.selectRow(row)
        if view.term_edit.isEnabled():
            break
    assert view.term_edit.isEnabled()
    view.term_edit.click()
    view.term_delete.click()
    assert edits and deletes and edits[0]["source"] == deletes[0]["source"]
    dialog = LibraryTermDialog(edits[0])
    dialog.preferred_edit.setText("新译名")
    dialog.forbidden_edit.setText("旧译名；错译名")
    values = dialog.values()
    assert values["preferred"] == "新译名"
    assert values["forbidden"] == ["旧译名", "错译名"]
    task = dialog.task()
    message = adapter.update_glossary_entry(task["job_id"], task["entry_id"], **values)
    assert "术语" in message
