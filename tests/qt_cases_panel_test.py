"""案例终审页（学术专业模式）。

逐例确认案例是否可纳入学术分析。这里钉住：

* 视图来自核心已有的产物（selected_cases 等），并带上来源标签、状态、过期判断；
* 批准 / 排除（必须写原因）/ 从合格池替换 / 修改模拟初译，都走核心的同一组动作；
* 批准不会改写译文或把合成对照变成历史初译；
* 当前译文变了，旧批准作废并在页面上说明；
* 筛选（来源 / 状态 / 搜索）与队列统计。
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
from folith import workspace_cases  # noqa: E402
from folith.qt import adapter  # noqa: E402
from stage3_case_review_test import _setup, _synthetic_case  # noqa: E402


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    cases = [_synthetic_case("SC-15", "382", "较好的模拟初译", "较弱的当前译文"),
             _synthetic_case("SC-16", "other", "另一个模拟初译", "另一段当前译文")]
    job_id, _state, _selected = _setup(tmp_path, cases)
    return job_id


def _by_id(job_id, case_id):
    return next(v for v in adapter.case_views(job_id) if v["case_id"] == case_id)


def test_views_carry_provenance_status_and_identity(job):
    views = adapter.case_views(job)
    assert [v["case_id"] for v in views] == ["SC-15", "SC-16"]
    first = views[0]
    assert first["origin_label"] == "合成对照"
    assert first["status_label"] == "待人工确认"
    assert first["identity_label"] == "第 1 段"
    assert first["validity_label"] == "待人工确认" and first["validity_tone"] == "pending"
    assert first["current_text"] == "较弱的当前译文"


def test_approval_records_the_decision_and_leaves_text_and_provenance_alone(job):
    before = adapter.load_state(job)["pairs"][0]["target"]
    adapter.review_case(job, "SC-15", "approved")
    view = _by_id(job, "SC-15")
    assert view["review_status"] == "approved" and view["status_label"] == "已批准纳入"
    assert view["origin_label"] == "合成对照", "批准不能把合成对照变成历史初译"
    assert adapter.load_state(job)["pairs"][0]["target"] == before


def test_exclusion_needs_a_reason(job):
    with pytest.raises(adapter.AdapterError, match="排除原因"):
        adapter.review_case(job, "SC-15", "rejected", "   ")
    assert _by_id(job, "SC-15")["review_status"] == "unreviewed"
    adapter.review_case(job, "SC-15", "rejected", "与本研究问题无关")
    view = _by_id(job, "SC-15")
    assert view["review_status"] == "rejected" and view["review_note"] == "与本研究问题无关"


def test_unknown_decision_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.review_case(job, "SC-15", "maybe")


def test_unknown_case_is_a_readable_error(job):
    with pytest.raises(adapter.AdapterError):
        adapter.review_case(job, "NOPE-1", "approved")


def test_baseline_can_be_modified_or_rejected_without_touching_the_translation(job):
    before = adapter.load_state(job)["pairs"][0]["target"]
    adapter.update_case_baseline(job, "SC-15", "改过的模拟初译")
    assert _by_id(job, "SC-15")["initial_text"] == "改过的模拟初译"
    adapter.update_case_baseline(job, "SC-15", "改过的模拟初译", reject=True)
    assert _by_id(job, "SC-15")["baseline_status"] == "rejected"
    assert adapter.load_state(job)["pairs"][0]["target"] == before


def test_replacing_needs_a_rejected_case(job):
    with pytest.raises(adapter.AdapterError):
        adapter.replace_rejected_case(job, "SC-15")


def test_changing_the_translation_voids_an_earlier_approval(job):
    adapter.review_case(job, "SC-15", "approved")
    assert not _by_id(job, "SC-15")["stale"]
    adapter.submit_edit(job, 0, "人工改过的当前译文")
    view = _by_id(job, "SC-15")
    assert view["stale"] is True and view["validity_label"] == "需要重新检查"


def test_filters_and_counts():
    views = [
        {"case_id": "a", "origin_label": "合成对照", "status_label": "待人工确认",
         "identity_label": "第 1 段", "source_text": "alpha", "current_text": "甲",
         "review_status": "unreviewed"},
        {"case_id": "b", "origin_label": "真实修订", "status_label": "已批准纳入",
         "identity_label": "第 2 段", "source_text": "beta", "current_text": "乙",
         "review_status": "approved"},
        {"case_id": "c", "origin_label": "真实修订", "status_label": "已排除",
         "identity_label": "第 3 段", "source_text": "gamma", "current_text": "丙",
         "review_status": "rejected"},
    ]
    assert [v["case_id"] for v in workspace_cases.filter_views(views, origin="真实修订")] == ["b", "c"]
    assert [v["case_id"] for v in workspace_cases.filter_views(views, status="已排除")] == ["c"]
    assert [v["case_id"] for v in workspace_cases.filter_views(views, query="ALPHA")] == ["a"]
    assert workspace_cases.counts(views) == {"total": 3, "approved": 1, "rejected": 1, "pending": 1}


def test_labels_cover_the_core_vocabulary():
    assert workspace_cases.review_status_label("approved") == "已批准纳入"
    assert workspace_cases.review_status_label("") == "待人工确认"
    assert workspace_cases.identity_label({"case_id": "seg-1", "segment_index": 4}) == "第 5 段"
    assert workspace_cases.identity_label({"case_id": ""}) == "未命名案例"
    assert workspace_cases.state_label("pass") == "已通过"
    assert workspace_cases.state_label(None) == "—"


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _panel(job_id):
    from folith.qt.cases_panel import CasesPanel

    panel = CasesPanel()
    panel.apply(adapter.case_views(job_id), adapter.translation_truth(job_id))
    return panel


def test_panel_lists_cases_and_selects_the_first(qapp, job):
    panel = _panel(job)
    assert panel.queue.count() == 2 and panel.queue.currentRow() == 0
    assert "合成对照" in panel.queue.item(0).text()
    html = panel.detail.toHtml()
    assert "较弱的当前译文" in html and "模拟初译" in html
    assert not panel.baseline_box.isHidden(), "合成对照要能改模拟初译"
    assert "较好的模拟初译" in panel.baseline_edit.toPlainText()


def test_panel_actions_emit_the_selected_case(qapp, job):
    panel = _panel(job)
    seen = []
    panel.approveRequested.connect(lambda c: seen.append(("approve", c)))
    panel.excludeRequested.connect(lambda c: seen.append(("exclude", c)))
    panel.editRequested.connect(lambda i: seen.append(("edit", i)))
    panel.baselineSaveRequested.connect(lambda c, t: seen.append(("save", c, t)))
    panel.queue.setCurrentRow(1)
    panel.approve_button.click()
    panel.exclude_button.click()
    panel.edit_button.click()
    panel.baseline_edit.setPlainText("新的模拟初译")
    panel.baseline_save.click()
    assert seen == [("approve", "SC-16"), ("exclude", "SC-16"), ("edit", 1),
                    ("save", "SC-16", "新的模拟初译")]


def test_panel_replace_is_only_for_excluded_cases(qapp, job):
    panel = _panel(job)
    assert not panel.replace_button.isEnabled()
    adapter.review_case(job, "SC-15", "rejected", "无关")
    panel.apply(adapter.case_views(job), {})
    panel.queue.setCurrentRow(0)
    assert panel.replace_button.isEnabled()


def test_panel_warns_when_an_approval_went_stale(qapp, job):
    adapter.review_case(job, "SC-15", "approved")
    adapter.submit_edit(job, 0, "人工改过")
    panel = _panel(job)
    assert not panel.stale_notice.isHidden()
    assert panel.approve_button.isEnabled(), "过期的批准必须能重新批准"
    assert panel.approve_button.text() == "保持批准"


def test_panel_filters_narrow_the_queue(qapp, job):
    panel = _panel(job)
    panel.status_box.setCurrentIndex(panel.status_box.findData("已排除"))
    assert panel.queue.count() == 0
    assert "当前筛选下没有案例" in panel.detail.toPlainText()
    panel.status_box.setCurrentIndex(0)
    panel.search_edit.setText("另一段")
    assert panel.queue.count() == 1


def test_panel_without_cases_says_so(qapp):
    from folith.qt.cases_panel import CasesPanel

    panel = CasesPanel()
    assert not panel.empty.isHidden() and panel.queue.count() == 0


def test_the_cases_tab_follows_the_academic_mode(qapp, tmp_path, monkeypatch):
    from folith.qt import views

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("plain.docx")
    state.update(p1_done=True, p2_done=True, paras=["a"], pairs=[{"source": "a", "target": "甲"}])
    core.save_job_state("plain0000000002", state)
    bench = views.WorkbenchView()
    loaded = adapter.load_state("plain0000000002")
    bench.load("plain0000000002", adapter.EngineConfig(target_lang="简体中文"), loaded,
               adapter.segment_rows("plain0000000002", loaded), [])
    index = bench.tabs.indexOf(bench.cases_panel)
    assert not bench.tabs.isTabVisible(index)
    bench.set_academic_mode(True)
    assert bench.tabs.isTabVisible(index)
