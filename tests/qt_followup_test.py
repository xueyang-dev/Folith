"""收尾页：长文跑完留下的尾巴必须看得见、补得了。

* 同一专名在不同段译法不一 → 预览要改哪几段 → 只应用用户看过的那一组；
* "这些译法各自合理"可以被确认，队列随之清空；
* 全文理解降级 / 标注批次失败 → 能标记补跑（成功部分保留）；
* 没有尾巴时给出"没有需要收尾"，而不是一块空白；
* 任务还在执行时核心拒绝，原因原样给到界面。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402


def _entity_state():
    state = core.new_job_state("names.docx")
    state.update(
        p1_done=True, p2_done=True,
        paras=["Lydia arrived.", "Lydia left.", "Another person arrived."],
        pairs=[{"source": "Lydia arrived.", "target": "莉迪亚来了。", "reviewed": True},
               {"source": "Lydia left.", "target": "莉迪娅离开了。", "reviewed": True},
               {"source": "Another person arrived.", "target": "莉迪娅来了。", "reviewed": True}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪亚", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    return state


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "followup000000001"
    core.save_job_state(job_id, _entity_state())
    return job_id


def test_view_lists_the_conflicting_entity(job):
    view = adapter.followup_view(job)
    assert view["has_work"] is True
    entity = view["entities"][0]
    assert entity["source"] == "Lydia"
    assert set(entity["observed_targets"]) == {"莉迪亚", "莉迪娅"}


def test_preview_changes_only_the_confirmed_occurrences(job):
    preview = adapter.entity_repair_preview(job, "Lydia", "莉迪亚")
    assert [row["index"] for row in preview] == [1]
    assert adapter.entity_repair_preview(job, "Lydia", "  ") == []


def test_unknown_entity_is_a_readable_error(job):
    with pytest.raises(adapter.AdapterError):
        adapter.entity_repair_preview(job, "Nobody", "x")


def test_applying_the_reviewed_preview_unifies_and_invalidates_review(job):
    preview = adapter.entity_repair_preview(job, "Lydia", "莉迪亚")
    adapter.apply_entity_repair(job, "Lydia", "莉迪亚", preview)
    state = adapter.load_state(job)
    assert state["pairs"][1]["target"] == "莉迪亚离开了。"
    assert state["pairs"][2]["target"] == "莉迪娅来了。", "预览之外的段落不能被改"
    assert not state["pairs"][1]["reviewed"], "被改过的段落审校必须失效"
    assert adapter.followup_view(job)["entities"] == []


def test_applying_nothing_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.apply_entity_repair(job, "Lydia", "莉迪亚", [])


def test_accepting_the_variants_clears_the_queue(job):
    adapter.accept_entity_variants(job, "Lydia")
    assert adapter.followup_view(job)["entities"] == []


def test_running_task_refuses_repairs_with_the_core_reason(job, monkeypatch):
    preview = adapter.entity_repair_preview(job, "Lydia", "莉迪亚")
    monkeypatch.setattr(core, "_job_execution_alive", lambda job_id: True)
    with pytest.raises(adapter.AdapterError, match="仍在执行"):
        adapter.apply_entity_repair(job, "Lydia", "莉迪亚", preview)
    assert adapter.load_state(job)["pairs"][1]["target"] == "莉迪娅离开了。"
    assert adapter.followup_view(job)["busy"] is True


def test_annotation_retry_is_marked_and_keeps_translation(job):
    state = adapter.load_state(job)
    state.update(enable_annotate=True, annotations_done=True,
                 annotation_batches={"0": {"status": "request_failed"}})
    core.save_job_state(job, state)
    assert adapter.followup_view(job)["annotation"]["status"] == "partial"
    adapter.prepare_followup_retry(job, "annotation")
    retried = adapter.load_state(job)
    assert retried["annotations_done"] is False
    assert retried["pairs"][1]["target"] == "莉迪娅离开了。"


def test_unknown_retry_part_is_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.prepare_followup_retry(job, "everything")


def test_clean_task_has_no_work(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("clean.docx")
    state.update(p1_done=True, p2_done=True, paras=["a"], pairs=[{"source": "a", "target": "甲"}])
    core.save_job_state("clean0000000001", state)
    assert adapter.followup_view("clean0000000001")["has_work"] is False


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_panel_for_a_clean_task_says_there_is_nothing_to_do(qapp):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply({"recovery": {}, "understanding": {}, "annotation": {}, "entities": [],
                 "busy": False, "has_work": False})
    assert not panel.empty.isHidden()
    assert panel.entity_card.isHidden() and panel.retry_card.isHidden()


def test_panel_flow_for_a_conflicting_name(qapp, job):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    previews, repairs = [], []
    panel.previewRequested.connect(lambda s, t: previews.append((s, t)))
    panel.repairRequested.connect(lambda s, t, p: repairs.append((s, t, p)))
    panel.apply(adapter.followup_view(job))
    assert not panel.entity_card.isHidden() and panel.empty.isHidden()
    assert "莉迪" in panel.entity_variants.text()
    assert not panel.repair_button.isEnabled(), "没有预览就不能确认"
    panel.target_edit.setText("莉迪亚")
    assert previews[-1] == ("Lydia", "莉迪亚")
    panel.set_preview(adapter.entity_repair_preview(job, "Lydia", "莉迪亚"))
    assert panel.repair_button.isEnabled()
    panel.repair_button.click()
    assert repairs and repairs[0][0] == "Lydia" and len(repairs[0][2]) == 1


def test_panel_disables_actions_while_the_task_runs(qapp, job):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    view = adapter.followup_view(job)
    view["busy"] = True
    panel.apply(view)
    panel.set_preview([{"index": 1, "source": "s", "before": "a", "after": "b"}])
    assert not panel.repair_button.isEnabled() and not panel.accept_button.isEnabled()


def test_panel_offers_the_right_annotation_retry_label(qapp):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply({"annotation": {"status": "partial", "failed_batches": 2,
                                "legacy_unlocated": False}, "busy": False})
    assert panel.annotation_button.text() == "只补跑失败标注批次"
    panel.apply({"annotation": {"status": "partial", "legacy_unlocated": True}, "busy": False})
    assert panel.annotation_button.text() == "重新运行全文标注"
    seen = []
    panel.retryRequested.connect(seen.append)
    panel.annotation_button.click()
    assert seen == ["annotation"]


def test_check_icon_counts_the_open_items_and_opens_the_drawer(qapp, job):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    # 待办数只挂在进度条旁的「检查与修复」图标上（有项变黄）；「更多」不再带数字，
    # 菜单里也不再有这一页（acceptance-feedback D3 / 1a）。
    assert bench.check_button.badge.text() == "1"
    assert bench.check_button.tone() == "warning"
    assert "(" not in bench.stage_bar.more_button.text()
    assert "followup" not in bench.stage_bar.more_page_ids()
    # 点图标：右侧面板打开、段落检查器让位；再点一次收起。
    bench.check_button.click()
    assert bench.followup_drawer_open()
    assert not bench.inspector_scroll.isVisibleTo(bench)
    bench.show_page("followup")  # old jump target still lands on the drawer
    assert bench.followup_drawer_open()
    bench.check_button.click()
    assert not bench.followup_drawer_open()
    assert bench.inspector_scroll.isVisibleTo(bench)


# --------------------------------------------------------- 重新扫描 PDF -------

@pytest.fixture()
def pdf_job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("scan.pdf")
    state.update(p1_done=True, p2_done=True, paras=["a"],
                 pairs=[{"source": "a", "target": "甲"}],
                 ocr_config={"ocr_backend": "online", "ocr_mode": "auto",
                             "ocr_language": "ara", "ocr_quality": "high"})
    core.save_job_state("scanjob000000001", state)
    return "scanjob000000001"


def test_rescan_card_is_only_for_pdf_tasks(job, pdf_job):
    assert adapter.followup_view(job)["rescan"]["available"] is False
    view = adapter.followup_view(pdf_job)["rescan"]
    assert view["available"] and view["language"] == "ara" and view["quality"] == "high"


def test_requesting_a_rescan_registers_the_options_for_the_next_run(pdf_job):
    options = adapter.request_source_rescan(
        pdf_job, backend="online", mode="force", language="ara", quality="historical")
    state = adapter.load_state(pdf_job)
    assert state["source_rescan_requested"] is True
    assert state["source_rescan_options"] == options
    assert options["ocr_dpi"] == core.ocr_quality_dpi("historical")
    assert options["ocr_online"] is None, "视觉连接不随任务快照保存"
    assert options["ocr_mode"] == "force"
    assert state["pairs"][0]["target"] == "甲", "登记请求本身不能动译文（失效发生在执行时）"


def test_rescan_is_refused_while_running(pdf_job, monkeypatch):
    monkeypatch.setattr(core, "job_is_active", lambda job_id: True)
    with pytest.raises(adapter.AdapterError, match="正在运行"):
        adapter.request_source_rescan(pdf_job, backend="online", mode="auto",
                                      language="", quality="standard")
    assert "source_rescan_requested" not in adapter.load_state(pdf_job)


def test_rescan_is_refused_for_non_pdf(job):
    with pytest.raises(adapter.AdapterError, match="PDF"):
        adapter.request_source_rescan(job, backend="online", mode="auto",
                                      language="", quality="standard")


def test_rescan_with_an_unavailable_local_engine_is_refused_with_the_reason(pdf_job, monkeypatch):
    monkeypatch.setattr(core, "ocr_engine_status", lambda *a, **k: {
        "ok": False, "message": "未找到 Tesseract 语言包 ara", "quality_note": ""})
    with pytest.raises(adapter.AdapterError, match="Tesseract"):
        adapter.request_source_rescan(pdf_job, backend="tesseract", mode="auto",
                                      language="ara", quality="standard")
    assert "source_rescan_requested" not in adapter.load_state(pdf_job)


def test_panel_rescan_card_flow(qapp, pdf_job):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply(adapter.followup_view(pdf_job))
    assert not panel.rescan_card.isHidden()
    assert panel.rescan_language.currentData() == "ara", "初始值取自任务当前设置"
    seen = []
    panel.rescanRequested.connect(seen.append)
    panel.rescan_button.click()
    assert seen and seen[0]["language"] == "ara" and seen[0]["quality"] == "high"


def test_panel_keeps_the_users_rescan_choice_across_refreshes(qapp, pdf_job):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply(adapter.followup_view(pdf_job))
    panel.rescan_quality.setCurrentIndex(panel.rescan_quality.findData("historical"))
    panel.apply(adapter.followup_view(pdf_job))
    assert panel.rescan_quality.currentData() == "historical"


def test_panel_hides_the_rescan_card_for_non_pdf_tasks(qapp, job):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply(adapter.followup_view(job))
    assert panel.rescan_card.isHidden()


# ------------------------------------------------------------ 导入范围 -------

def _report_state(**extra):
    report = core._build_extraction_report(
        "scan.pdf", ["a", "b", "c"], ocr_used=True,
        unsupported=[{"label": "表格", "detail": "2 个表格未纳入", "count": 2, "sample": "Q1 | Q2"}],
        facts={"pages": 4}, warnings=["第 3 页文字层损坏，已改用识别"])
    report.update(extra)
    return {"extraction_report": report}


def test_extraction_view_reports_scope_unsupported_and_warnings():
    view = adapter.extraction_report_view(_report_state(
        ocr={"ocr_used": True, "actual_engine": "vision", "all_pages_completed": False}))
    assert view["available"] and view["paragraphs"] == 3 and view["pages"] == 4
    assert view["ocr_used"] and view["ocr_engine"] == "vision" and view["pages_incomplete"]
    assert view["unsupported"] == [{"label": "表格", "detail": "2 个表格未纳入", "sample": "Q1 | Q2"}]
    assert view["warnings"] == ["第 3 页文字层损坏，已改用识别"]


def test_tasks_without_a_report_show_nothing():
    assert adapter.extraction_report_view({}) == {"available": False}
    assert adapter.extraction_report_view({"extraction_report": {"x": 1}}) == {"available": False}


def test_panel_shows_the_extraction_card(qapp):
    from folith.qt.followup_panel import FollowupPanel

    panel = FollowupPanel()
    panel.apply({"extraction": adapter.extraction_report_view(_report_state())})
    assert not panel.extraction_card.isHidden()
    assert "3" in panel.extraction_summary.text() and "4" in panel.extraction_summary.text()
    assert "表格" in panel.extraction_details.text() and "Q1 | Q2" in panel.extraction_details.text()
    assert "文字层损坏" in panel.extraction_warning.text()
    panel.apply({"extraction": {"available": False}})
    assert panel.extraction_card.isHidden()
