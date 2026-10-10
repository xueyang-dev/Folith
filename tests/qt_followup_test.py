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


def test_workbench_tab_counts_the_open_items(qapp, job):
    from folith.qt import views

    bench = views.WorkbenchView()
    state = adapter.load_state(job)
    bench.load(job, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job, state), [])
    text = bench.tabs.tabText(bench.tabs.indexOf(bench.followup_panel))
    assert "(1)" in text
