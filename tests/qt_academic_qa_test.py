"""学术专业模式与「合规与 QA」页。

用户的决定：实践报告 / 案例 / QA 保留，但默认关闭，主动打开才出现。这里钉住：

* 默认情况下这些页不出现；打开模式、或任务本身已带着学术产物时才出现
  （否则带报告要求的任务会被交付门禁卡住而无处下手）；
* QA 页的视图只是对核心既有事实的整形：规则清单、四项独立质量事实、待处理计数；
* 作者视觉复核 / Word 最终复核可确认也可撤销，结果落进核心的 final_qa；
* 没装 LibreOffice 时如实报错，不伪造"通过"；
* 设置里关掉模式会一并取消"生成实践报告"，避免一个看不见的选项悄悄生效。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith import workspace_qa  # noqa: E402
from folith.qt import adapter  # noqa: E402


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "academicqa0000001"
    state = core.new_job_state("report.docx")
    state.update(p1_done=True, p2_done=True, report_enabled=True,
                 paras=["a"], pairs=[{"source": "a", "target": "甲", "reviewed": True}])
    core.save_job_state(job_id, state)
    return job_id


# ------------------------------------------------------------ 投影 -----------

def test_view_lists_rules_and_the_four_independent_facts(job):
    view = adapter.qa_view(job)
    assert view["rules"], "默认规则集应该有规则"
    assert [f["field"] for f in view["facts"]] == [
        "structural_qa", "libreoffice_render", "author_visual_review", "word_final_review"]
    assert view["profile"]["name"]
    assert view["attention"] == view["failed"] + view["manual"] + view["not_run"]
    assert view["attention"] > 0, "全新任务不可能已满足所有最终检查"


def test_manual_facts_are_flagged_and_unconfirmed_by_default(job):
    facts = {f["field"]: f for f in adapter.qa_view(job)["facts"]}
    assert facts["author_visual_review"]["manual"] and not facts["author_visual_review"]["confirmed"]
    assert facts["libreoffice_render"]["manual"] is False
    assert facts["structural_qa"]["status_label"]


def test_rule_rows_carry_source_and_attention_flags(job):
    for rule in adapter.qa_view(job)["rules"]:
        assert rule["label"] and rule["status_label"]
        assert rule["needs_attention"] == (rule["status"] in {"fail", "manual_review", "not_checked"})
        assert rule["source"], "没有可靠来源时也要明确写出，而不是留空"


def test_confirming_and_revoking_a_manual_fact(job):
    adapter.set_final_qa_fact(job, "author_visual_review", True)
    state = adapter.load_state(job)
    assert state["final_qa"]["author_visual_review"] == "CONFIRMED"
    facts = {f["field"]: f for f in adapter.qa_view(job)["facts"]}
    assert facts["author_visual_review"]["confirmed"]
    adapter.set_final_qa_fact(job, "author_visual_review", False)
    assert adapter.load_state(job)["final_qa"]["author_visual_review"] == "NOT_CONFIRMED"


def test_automatic_facts_cannot_be_confirmed_by_hand(job):
    with pytest.raises(adapter.AdapterError):
        adapter.set_final_qa_fact(job, "libreoffice_render", True)
    with pytest.raises(adapter.AdapterError):
        adapter.set_final_qa_fact(job, "structural_qa", True)


def test_render_qa_reports_a_missing_libreoffice_honestly(job, monkeypatch):
    def refuse(job_id):
        raise RuntimeError("未找到 LibreOffice，无法渲染页面")

    monkeypatch.setattr(core, "run_libreoffice_render_qa", refuse)
    with pytest.raises(adapter.AdapterError, match="LibreOffice"):
        adapter.run_render_qa(job)
    assert adapter.load_state(job)["final_qa"]["libreoffice_render"] != "PASS"


def test_structural_check_is_synced_only_on_request(job):
    view = adapter.qa_view(job)
    before = adapter.load_state(job).get("final_qa", {}).get("structural_qa")
    assert adapter.qa_view(job)["structural_value"] == view["structural_value"]
    assert adapter.load_state(job).get("final_qa", {}).get("structural_qa") == before, \
        "读视图不能写状态"
    adapter.sync_structural_qa(job, "FAIL")
    assert adapter.load_state(job)["final_qa"]["structural_qa"] == "FAIL"
    adapter.sync_structural_qa(job, "STALE")      # 非法取值被忽略
    assert adapter.load_state(job)["final_qa"]["structural_qa"] == "FAIL"


def test_projection_counts_stale_structure_as_not_run():
    compliance = {"counts": {"fail": 1, "manual_review": 2, "not_checked": 3}, "rules": []}
    view = workspace_qa.qa_view({}, compliance, {"profile_id": "p"}, "STALE")
    assert view["failed"] == 1
    assert view["manual"] == 2 + 2          # 2 条人工规则 + 作者/Word 两项未确认
    assert view["not_run"] == 3 + 1 + 1     # 3 条未检查 + 结构过期 + 页面渲染未运行


# ------------------------------------------------------------ 模式开关 ---------

def test_academic_pages_are_hidden_by_default_for_plain_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("plain.docx")
    state.update(p1_done=True, p2_done=True, paras=["a"], pairs=[{"source": "a", "target": "甲"}])
    assert adapter.task_needs_academic(state) is False


def test_tasks_that_carry_academic_state_always_show_the_pages():
    assert adapter.task_needs_academic({"report_enabled": True})
    assert adapter.task_needs_academic({"academic_state": {"artifacts": {"report": {}}}})
    assert adapter.task_needs_academic({"final_qa": {"author_visual_review": "CONFIRMED"}})
    assert not adapter.task_needs_academic({"final_qa": {"structural_qa": "NOT_RUN"}})


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _tab_visible(bench, widget) -> bool:
    return bench.tabs.isTabVisible(bench.tabs.indexOf(widget))


def _load(bench, job_id):
    state = adapter.load_state(job_id)
    bench.load(job_id, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job_id, state), [])


def test_workbench_hides_the_academic_tab_until_enabled(qapp, tmp_path, monkeypatch):
    from folith.qt import views

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("plain.docx")
    state.update(p1_done=True, p2_done=True, paras=["a"], pairs=[{"source": "a", "target": "甲"}])
    core.save_job_state("plain0000000001", state)
    bench = views.WorkbenchView()
    _load(bench, "plain0000000001")
    assert not _tab_visible(bench, bench.qa_panel)
    bench.set_academic_mode(True)
    assert _tab_visible(bench, bench.qa_panel)
    bench.set_academic_mode(False)
    assert not _tab_visible(bench, bench.qa_panel)


def test_a_report_task_shows_the_page_even_when_the_mode_is_off(qapp, job):
    from folith.qt import views

    bench = views.WorkbenchView()
    _load(bench, job)
    assert _tab_visible(bench, bench.qa_panel)
    assert bench.qa_panel.fact_chip_text("author_visual_review")


def test_opening_the_qa_tab_syncs_the_structural_result(qapp, job, monkeypatch):
    from folith.qt import views

    bench = views.WorkbenchView()
    _load(bench, job)
    stored = adapter.load_state(job).get("final_qa", {}).get("structural_qa")
    assert stored != "FAIL"
    monkeypatch.setattr(workspace_qa, "structural_qa", lambda state, validation: "FAIL")
    bench.tabs.setCurrentWidget(bench.qa_panel)          # 打开页面 = 显式动作
    assert adapter.load_state(job)["final_qa"]["structural_qa"] == "FAIL"


def test_rendering_the_workbench_alone_never_writes_the_structural_result(qapp, job, monkeypatch):
    from folith.qt import views

    monkeypatch.setattr(workspace_qa, "structural_qa", lambda state, validation: "FAIL")
    bench = views.WorkbenchView()
    _load(bench, job)
    assert adapter.load_state(job).get("final_qa", {}).get("structural_qa") != "FAIL", \
        "后台刷新/载入任务不能写状态"


def test_panel_buttons_emit_the_actions(qapp, job):
    from folith.qt.qa_panel import QAPanel

    panel = QAPanel()
    panel.apply(adapter.qa_view(job))
    seen, toggled = [], []
    panel.renderRequested.connect(lambda: seen.append(1))
    panel.factToggled.connect(lambda f, c: toggled.append((f, c)))
    panel.fact_button("libreoffice_render").click()
    panel.fact_button("author_visual_review").click()
    assert seen == [1] and toggled == [("author_visual_review", True)]
    assert panel.fact_button("author_visual_review").text() == "确认已完成"
    assert panel.fact_button("structural_qa").isHidden() or not panel.fact_button("structural_qa").text()


def test_panel_render_button_needs_a_finished_translation(qapp, job):
    from folith.qt.qa_panel import QAPanel

    panel = QAPanel()
    view = adapter.qa_view(job)
    view["p2_done"] = False
    panel.apply(view)
    assert not panel.fact_button("libreoffice_render").isEnabled()


def test_confirmed_facts_offer_to_revoke(qapp, job):
    from folith.qt.qa_panel import QAPanel

    adapter.set_final_qa_fact(job, "word_final_review", True)
    panel = QAPanel()
    panel.apply(adapter.qa_view(job))
    assert panel.fact_button("word_final_review").text() == "撤销确认"


def test_settings_dialog_turning_the_mode_off_clears_the_report_option(qapp):
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    dialog = EngineDialog(adapter.EngineConfig(target_lang="简体中文"), TaskRunner())
    try:
        assert not dialog.academic_box.isChecked()
        assert dialog.report_box.isHidden()
        dialog.academic_box.setChecked(True)
        assert not dialog.report_box.isHidden()
        dialog.report_box.setChecked(True)
        dialog.academic_box.setChecked(False)
        assert not dialog.report_box.isChecked() and dialog.report_box.isHidden()
    finally:
        dialog.close()


def test_a_config_that_already_generates_reports_opens_the_mode(qapp):
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    dialog = EngineDialog(adapter.EngineConfig(target_lang="简体中文", enable_report=True), TaskRunner())
    try:
        assert dialog.academic_box.isChecked() and not dialog.report_box.isHidden()
    finally:
        dialog.close()


def test_reasoning_effort_is_offered_only_for_reasoning_models(qapp):
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    dialog = EngineDialog(adapter.EngineConfig(provider="OpenAI", model="gpt-4o",
                                               target_lang="简体中文"), TaskRunner())
    # 推理强度在连接编辑区里：先打开正在用的那条连接。
    dialog.connection_library.edit(dialog.connection_library.roles["translation"])
    assert dialog.reasoning_row.isHidden()
    assert dialog._collect().reasoning_effort == ""
    dialog.model_box.setCurrentText("o3")
    assert not dialog.reasoning_row.isHidden()
    dialog.reasoning_box.setCurrentIndex(dialog.reasoning_box.findData("high"))
    assert dialog._collect().reasoning_effort == "high"
    dialog.model_box.setCurrentText("gpt-4o")        # 不支持的模型：这一项收起，保留原值
    assert dialog.reasoning_row.isHidden()
    assert dialog._collect().reasoning_effort == ""   # 原配置就是自动
    # 审校改为从已保存的连接里选，不再有自己的「获取模型」按钮。
