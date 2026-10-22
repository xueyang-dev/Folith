"""审校页：发现项必须能由人处置，且处置真的影响交付门禁。

之前 Qt 工作台只在检查器里只读地列出发现项，一条误报就能永远挡住交付。这里钉住：

* 队列来自 ``folith.workbench_view``（与旧界面同一份投影，不另写规则）；
* "确认已解决"/"保留当前译文"走核心的人工决定路径，发现项随之消失、门禁放行，
  并留下人工记录；
* "应用建议译文"写入译文并解决发现项；
* 面板按钮的显示规则：不需要人工决定的纯建议不给"确认已解决"，来源质量项只给
  "确认原文并重新翻译"，且必须勾选并写依据才可点。
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
from folith import delivery  # noqa: E402
from folith.qt import adapter  # noqa: E402


def _finding(**overrides):
    base = {"segment_index": 0, "type": "check", "severity": "blocking",
            "category": "format_integrity", "summary": "占位符未被保留",
            "source_span": "%s", "target_span": None,
            "explanation": "原文包含必须保留的占位符，但当前译文中没有找到它。",
            "recommendation": "补回占位符后重新检查格式完整性。",
            "detector": "Deterministic QA", "diagnostic_version": 1,
            "reason": "占位符丢失"}
    base.update(overrides)
    return base


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "reviewpanel0000001"
    state = core.new_job_state("review-panel.docx")
    state.update(
        p1_done=True, p2_done=True,
        paras=["Hello %s", "Second"],
        pairs=[{"source": "Hello %s", "target": "你好"},
               {"source": "Second", "target": "第二"}],
        findings=[
            _finding(),
            _finding(segment_index=1, severity="actionable",
                     summary="译文可以更自然", suggested_target="第二段（建议）",
                     reason="措辞"),
        ],
    )
    core.save_job_state(job_id, state)
    return job_id


def _open_ids(job_id):
    state = adapter.load_state(job_id)
    return {delivery.finding_id(item) for item in state.get("findings") or []
            if not item.get("resolved")}


def test_queue_lists_the_findings_the_gate_counts(job):
    view = adapter.review_queue(job)
    kinds = [item["kind"] for item in view["queue_items"]]
    assert kinds.count("finding") >= 2
    blocking = [item for item in view["queue_items"]
                if item.get("severity") == "blocking"]
    assert blocking and blocking[0]["segment_index"] == 0
    # 队列里的标识就是核心用来记录决定的标识
    assert blocking[0]["finding_id"] in _open_ids(job)


def test_confirming_a_finding_removes_it_and_records_a_human_action(job):
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if i.get("severity") == "blocking")
    assert adapter.unresolved_blocking(job)
    adapter.resolve_findings(job, [item["finding_id"]], adapter.RESOLVE_CONFIRMED,
                             "人工核对：占位符在原文页脚")
    assert item["finding_id"] not in _open_ids(job)
    assert not adapter.unresolved_blocking(job), "已解决的阻断项不能继续挡交付"
    actions = adapter.load_state(job).get("human_actions") or []
    assert any(a.get("finding_id") == item["finding_id"]
               and a.get("action") == "human_fixed"
               and a.get("note") == "人工核对：占位符在原文页脚" for a in actions)


def test_preserving_the_translation_is_recorded_as_such(job):
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if i.get("severity") == "blocking")
    adapter.resolve_findings(job, [item["finding_id"]], adapter.RESOLVE_PRESERVED)
    actions = adapter.load_state(job).get("human_actions") or []
    assert any(a.get("action") == "preserved"
               and a.get("finding_id") == item["finding_id"] for a in actions)


def test_unknown_action_and_missing_identity_are_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.resolve_findings(job, ["x"], "delete_everything")
    with pytest.raises(adapter.AdapterError):
        adapter.resolve_findings(job, [""], adapter.RESOLVE_CONFIRMED)


def test_applying_a_suggestion_writes_the_translation_and_resolves_the_finding(job):
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if i.get("suggested_target"))
    result = adapter.apply_suggested_translation(job, item, "采用建议")
    state = adapter.load_state(job)
    assert state["pairs"][1]["target"] == "第二段（建议）"
    assert result["index"] == 1
    assert item["finding_id"] not in _open_ids(job)


def test_applying_without_a_suggestion_is_refused(job):
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if not i.get("suggested_target"))
    with pytest.raises(adapter.AdapterError):
        adapter.apply_suggested_translation(job, item)


def test_source_confirmation_requires_a_written_basis(job):
    with pytest.raises(adapter.AdapterError):
        adapter.confirm_source_segment(job, 0, "   ")


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture()
def panel(qtbot_free_app):
    from folith.qt.review_panel import ReviewPanel

    return ReviewPanel()


@pytest.fixture(scope="module")
def qtbot_free_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _view(*items):
    return {"queue_items": list(items),
            "filter_counts": {"all": len(items), "pending": len(items)},
            "readiness": {"label": "待审校", "detail": "有未处理项"}}


def _item(**overrides):
    base = {"id": "finding:a", "kind": "finding", "filter_group": "pending",
            "priority": 2, "segment_index": 0, "finding_id": "a",
            "core_finding": False, "requires_human_confirmation": False,
            "decidable": True, "severity": "blocking", "severity_label": "必须处理",
            "title": "第 1 段 · x", "summary": "问题", "suggested_target": "",
            "finding_type": "check"}
    base.update(overrides)
    return base


def test_panel_offers_decisions_for_a_decidable_finding(panel):
    panel.apply(_view(_item()))
    assert panel.confirm_button.isVisibleTo(panel)
    assert panel.preserve_button.isVisibleTo(panel)
    assert not panel.apply_button.isVisibleTo(panel)
    assert not panel.confirm_source_button.isVisibleTo(panel)


def test_panel_hides_decisions_for_advice_that_needs_no_decision(panel):
    panel.apply(_view(_item(core_finding=True, requires_human_confirmation=False,
                            severity="informational", filter_group="reference")))
    assert not panel.confirm_button.isVisibleTo(panel)
    assert panel.edit_button.isVisibleTo(panel), "仍可修改译文"


def test_panel_offers_apply_when_there_is_a_suggestion(panel):
    panel.apply(_view(_item(suggested_target="更好的译文")))
    assert panel.apply_button.isVisibleTo(panel)
    assert panel.apply_button.isEnabled()


def test_panel_source_items_need_a_checkbox_and_a_basis(panel):
    panel.apply(_view(_item(finding_type="source_quality_gate")))
    assert panel.confirm_source_button.isVisibleTo(panel)
    assert not panel.confirm_button.isVisibleTo(panel)
    assert not panel.confirm_source_button.isEnabled()
    panel.source_confirm_box.setChecked(True)
    assert not panel.confirm_source_button.isEnabled(), "没写依据不能点"
    panel.source_note_edit.setText("原页正文清晰可读")
    assert panel.confirm_source_button.isEnabled()


def test_panel_emits_the_signals_the_window_listens_to(panel):
    panel.apply(_view(_item(suggested_target="新译文")))
    seen = {}
    panel.resolveRequested.connect(lambda ids, action, note: seen.update(
        resolve=(ids, action, note)))
    panel.applySuggestionRequested.connect(lambda item, note: seen.update(
        apply=(item["finding_id"], note)))
    panel.editRequested.connect(lambda index: seen.update(edit=index))
    panel.note_edit.setText("依据")
    panel.confirm_button.click()
    panel.preserve_button.click()
    panel.apply_button.click()
    panel.edit_button.click()
    assert seen["resolve"] == (["a"], "preserved", "依据")   # 后一次点击覆盖
    assert seen["apply"] == ("a", "依据")
    assert seen["edit"] == 0


def test_panel_review_task_items_offer_a_rereview(panel):
    panel.apply(_view(_item(kind="stale", finding_id="", decidable=False,
                            filter_group="rereview", title="第 1 段 · 需要重新审校")))
    assert panel.review_button.isVisibleTo(panel)
    assert not panel.confirm_button.isVisibleTo(panel)
    seen = []
    panel.reviewRequested.connect(seen.append)
    panel.review_button.click()
    assert seen == [[0]]


def test_panel_shows_skipped_coverage_as_reviewable_not_completed(panel):
    label = "1 段未执行本轮独立语义审校（按策略跳过）"
    panel.apply({
        "queue_items": [_item(
            id="skipped:segment:0", kind="skipped", filter_group="skipped",
            finding_id="", decidable=False, severity="informational",
            severity_label="未执行独立审校", title="第 1 段 · 未执行独立审校")],
        "filter_counts": {"all": 1, "skipped": 1},
        "readiness": {
            "status": "current", "ready": True,
            "coverage_complete": False, "coverage_status": "skipped",
            "skipped_count": 1, "label": label,
            "detail": "按策略跳过：无确定性风险信号 1 段。",
        },
    })

    assert panel.headline.text() == label
    assert panel.list.count() == 1
    assert panel.review_button.isVisibleTo(panel)
    assert not panel.confirm_button.isVisibleTo(panel)
    assert "跳过不等于审校通过" in panel.hint.text()


def test_filter_keeps_the_selection_and_shows_an_empty_hint(panel):
    panel.apply(_view(_item()))
    assert panel.list.count() == 1
    panel._set_filter("suggested")
    assert panel.list.count() == 0
    assert panel.empty_label.isVisibleTo(panel)
    panel._set_filter("all")
    assert panel.list.count() == 1


# ---- 名字译法不一：审校页与抽屉共用的选项部件 ----------------------------------

def _entity():
    return {"source": "Lycidas", "preferred_target": "利西达斯",
            "observed_targets": ["Lycidas", "利西达斯"],
            "occurrences": [
                {"index": 173, "variants": ["利西达斯"]},
                {"index": 792, "variants": ["Lycidas"]},
                {"index": 795, "variants": ["Lycidas"]}]}


def _entity_view():
    view = _view(_item(id="finding:e", finding_id="e", finding_type="entity_conflict",
                       entity_source="Lycidas", severity="actionable",
                       severity_label="建议处理", filter_group="suggested",
                       segment_index=173, title="第 174 段 · Lycidas"))
    view["entities"] = [_entity()]
    return view


def test_review_page_shows_the_shared_widget_instead_of_the_segment_actions(panel):
    panel.apply(_entity_view())
    assert panel.conflict.isVisibleTo(panel)
    for button in (panel.confirm_button, panel.preserve_button, panel.edit_button,
                   panel.retranslate_button, panel.apply_button):
        assert not button.isVisibleTo(panel)
    options = panel.conflict.options()
    assert [(o["kind"], o["target"]) for o in options] == [
        ("variant", "Lycidas"), ("variant", "利西达斯"), ("custom", ""), ("keep", "")]
    assert panel.conflict.selected_option()["target"] == "利西达斯", "默认选当前定名"


def test_review_page_confirm_paths_use_the_existing_signals(panel):
    panel.apply(_entity_view())
    seen = {"preview": [], "repair": [], "accept": []}
    panel.entityPreviewRequested.connect(lambda s, t: seen["preview"].append((s, t)))
    panel.entityRepairRequested.connect(lambda s, t, p: seen["repair"].append((s, t, p)))
    panel.entityAcceptRequested.connect(seen["accept"].append)
    panel.conflict.select("variant", "Lycidas")
    assert seen["preview"][-1] == ("Lycidas", "Lycidas")
    change = [{"index": 173, "source": "s", "before": "利西达斯", "after": "Lycidas"}]
    panel.conflict.set_preview(change, "Lycidas", "Lycidas")
    panel.conflict.confirm_button.click()
    assert seen["repair"] == [("Lycidas", "Lycidas", change)]
    panel.conflict.select("keep")
    panel.conflict.confirm_button.click()
    assert seen["accept"] == ["Lycidas"]


def test_review_page_segment_links_jump_to_the_segment(panel):
    panel.apply(_entity_view())
    seen = []
    panel.editRequested.connect(seen.append)
    panel.conflict._link("seg:792")
    assert seen == [792]


def test_review_item_without_a_known_entity_keeps_the_segment_actions(panel):
    view = _entity_view()
    view["entities"] = []
    panel.apply(view)
    assert not panel.conflict.isVisibleTo(panel)
    assert panel.edit_button.isVisibleTo(panel)


def test_review_queue_carries_the_entities_for_conflict_findings(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("names.docx")
    state.update(
        p1_done=True, p2_done=True, paras=["Lydia a.", "Lydia b."],
        pairs=[{"source": "Lydia a.", "target": "莉迪亚来了。"},
               {"source": "Lydia b.", "target": "莉迪娅来了。"}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪亚", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    state["findings"] = [{"type": "entity_conflict", "severity": "actionable",
                          "source": "Lydia", "segment_index": 0, "segment_id": 0,
                          "reason": "实体「Lydia」的当前译名需要确认"}]
    core.save_job_state("entityreview0000001", state)
    view = adapter.review_queue("entityreview0000001")
    items = [item for item in view["queue_items"] if item.get("entity_source")]
    assert [item["entity_source"] for item in items] == ["Lydia"]
    assert [entity["source"] for entity in view["entities"]] == ["Lydia"]
