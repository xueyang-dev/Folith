"""审校页：发现项必须能由人处置，且处置真的影响交付门禁。

之前 Qt 工作台只在检查器里只读地列出发现项，一条误报就能永远挡住交付。这里钉住：

* 队列来自 ``folith.workbench_view``（与旧界面同一份投影，不另写规则）；
* "确认已解决"/"保留当前译文"走核心的人工决定路径，发现项随之消失、门禁放行，
  并留下人工记录；
* "暂存为候选"只生成候选（正文与发现项都不动），采用走同一套版本校验；
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


def test_confirming_the_problem_exists_keeps_it_open(job):
    """确认问题存在 ≠ 确认已解决：决定被记下，问题仍待处理、仍挡交付、正文不变。"""
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if i.get("severity") == "blocking")
    before = adapter.load_state(job)
    adapter.resolve_findings(job, [item["finding_id"]],
                             adapter.RESOLVE_CONFIRMED_OPEN, "确实丢了占位符")
    state = adapter.load_state(job)
    assert item["finding_id"] in _open_ids(job), "确认存在不能让问题消失"
    assert adapter.unresolved_blocking(job), "问题仍待修订，门禁照旧拦着"
    # 交付状态由存盘时的重算决定（夹具里存的是旧值 "draft"）；关键是它仍在
    # review_required——确认存在不推进交付。
    assert state["delivery_status"] == "review_required"
    actions = state.get("human_actions") or []
    confirmed = [a for a in actions
                 if a.get("finding_id") == item["finding_id"]
                 and a.get("action") == "confirmed_open"]
    assert confirmed and confirmed[0]["record_type"] == "finding_confirmation"
    assert confirmed[0]["note"] == "确实丢了占位符"
    assert not any(a.get("action") == "human_fixed" for a in actions), \
        "确认有问题不能顺手记成已修复"
    assert state["pairs"][0]["target"] == before["pairs"][0]["target"]


def test_unknown_action_and_missing_identity_are_refused(job):
    with pytest.raises(adapter.AdapterError):
        adapter.resolve_findings(job, ["x"], "delete_everything")
    with pytest.raises(adapter.AdapterError):
        adapter.resolve_findings(job, [""], adapter.RESOLVE_CONFIRMED)


def test_staging_a_suggestion_leaves_the_translation_and_the_finding_alone(job):
    """审校建议先落候选：正文不动、发现项不关；采用是另一个显式动作。"""
    item = next(i for i in adapter.review_queue(job)["queue_items"]
                if i.get("suggested_target"))
    result = adapter.apply_suggested_translation(job, item, "采用建议")
    state = adapter.load_state(job)
    assert result["index"] == 1
    assert state["pairs"][1]["target"] == "第二", "生成候选不能改正文"
    assert item["finding_id"] in _open_ids(job), "暂存建议不等于解决问题"
    created = result["created"]
    assert created and created[0]["origin"] == "review_suggestion"
    assert created[0]["status"] == "pending"
    assert created[0]["candidate_target"] == "第二段（建议）"

    adopted = adapter.adopt_candidate(job, created[0]["candidate_id"])
    assert adopted["committed"] and adopted["changed_text"]
    state = adapter.load_state(job)
    assert state["pairs"][1]["target"] == "第二段（建议）"
    assert state["pairs"][1]["target_provenance"] == "candidate_adopted"
    # 决定与文本变更分离：采用建议不替人把发现项标成已解决。
    assert item["finding_id"] in _open_ids(job)


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


def test_panel_separates_confirm_exists_from_confirm_resolved(panel):
    """两个决定必须分成两个按钮：一个说"问题是真的"，一个说"已经改好了"。"""
    panel.apply(_view(_item()))
    assert panel.open_button.isVisibleTo(panel)
    assert panel.open_button.text() != panel.confirm_button.text()
    assert "存在" in panel.open_button.text()
    assert "解决" in panel.confirm_button.text()
    seen = {}
    panel.resolveRequested.connect(lambda ids, action, note: seen.update(
        resolve=(ids, action, note)))
    panel.open_button.click()
    assert seen["resolve"] == (["a"], "confirmed_open", "")


def test_panel_hides_the_confirm_exists_button_for_plain_advice(panel):
    panel.apply(_view(_item(core_finding=True, requires_human_confirmation=False,
                            severity="informational", filter_group="reference")))
    assert not panel.open_button.isVisibleTo(panel)


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
    assert len(panel._visible) == 1
    assert panel.review_button.isVisibleTo(panel)
    assert not panel.confirm_button.isVisibleTo(panel)
    assert "跳过不等于审校通过" in panel.hint.text()


def test_collapsing_a_group_hides_its_cards_and_shows_a_hint(panel):
    panel.apply(_view(_item()))
    assert len(panel._visible) == 1
    panel._toggle_group(panel._group_of(panel._items[0]))
    assert panel._visible == []
    assert panel.empty_label.isVisibleTo(panel)
    panel._toggle_group(panel._group_of(panel._items[0]))
    assert len(panel._visible) == 1


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


# ---- 默认只列需要决定的项；名字译法不一一个名字一行 ------------------------------

def _mixed_view():
    view = _view(
        _item(id="finding:p", finding_id="p", title="第 1 段 · p"),
        _item(id="finding:s", finding_id="s", filter_group="suggested",
              severity="actionable", title="第 2 段 · s", segment_index=1),
        _item(id="finding:r", finding_id="r", filter_group="reference",
              severity="informational", title="第 3 段 · r", segment_index=2),
        _item(id="stale:segment:4", kind="stale", filter_group="rereview",
              title="第 5 段 · 需要重新审校", segment_index=4),
        _item(id="finding:e", finding_id="e", finding_type="entity_conflict",
              entity_source="Lycidas", filter_group="suggested", severity="actionable",
              segment_index=173, segment_indexes=[173, 792], title="第 174 段 · Lycidas"))
    view["filter_counts"] = {"all": 5, "pending": 1, "rereview": 1, "suggested": 2,
                             "reference": 1, "skipped": 0}
    view["entities"] = [_entity()]
    return view


def _visible_ids(panel):
    return [item["id"] for item in panel._visible]


def test_default_view_lists_only_items_that_need_a_decision(panel):
    panel.apply(_mixed_view())
    assert panel._expanded == {"must": True, "suggested": False,
                               "reference": False}
    assert set(_visible_ids(panel)) == {"finding:p", "stale:segment:4"}
    assert "finding:r" not in _visible_ids(panel), "仅供参考默认收起"
    assert "finding:s" not in _visible_ids(panel), "建议默认收起"


def test_tab_counts_for_the_existing_filters_are_unchanged(panel):
    panel.apply(_mixed_view())
    counts = panel._view["filter_counts"]
    assert counts["all"] == 5 and counts["reference"] == 1 and counts["suggested"] == 2


def test_every_group_can_be_expanded(panel):
    panel.apply(_mixed_view())
    for key in ("suggested", "reference"):
        panel._toggle_group(key)
    assert len(panel._visible) == 5
    panel._toggle_group("reference")
    assert "finding:r" not in _visible_ids(panel)


def test_the_first_non_empty_group_expands_when_nothing_must_be_handled(panel):
    view = _view(_item(id="finding:r", finding_id="r", filter_group="reference",
                       severity="informational"))
    view["filter_counts"] = {"all": 1, "reference": 1}
    panel.apply(view)
    assert _visible_ids(panel) == ["finding:r"]


def test_a_users_group_choice_survives_a_refresh(panel):
    panel.apply(_mixed_view())
    panel._toggle_group("reference")
    panel.apply(_mixed_view())
    assert panel._expanded["reference"] is True


def test_jumping_to_a_hidden_item_expands_its_group(panel):
    panel.apply(_mixed_view())
    assert panel.select_item_id("finding:r")
    assert panel._expanded["reference"] is True
    assert panel.selected_item()["id"] == "finding:r"


def test_entity_rows_are_titled_by_name(panel):
    panel.apply(_mixed_view())
    panel.select_item_id("finding:e")
    assert panel.item_title.text() == "译名不一致 · Lycidas（2 处）"


def test_review_queue_has_one_row_per_conflicting_name(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("names.docx")
    state.update(
        p1_done=True, p2_done=True, paras=["Lydia a.", "Lydia b.", "Lydia c."],
        pairs=[{"source": "Lydia a.", "target": "莉迪亚来了。"},
               {"source": "Lydia b.", "target": "莉迪娅来了。"},
               {"source": "Lydia c.", "target": "莉迪娅走了。"}])
    registry = core._entity_registry.EntityRegistry()
    registry.observe("Lydia", "莉迪亚", segment_id=0, entity_type="person")
    registry.observe("Lydia", "莉迪娅", segment_id=1, entity_type="person")
    state["entity_registry"] = registry.to_list()
    state["findings"] = [
        {"type": "entity_conflict", "severity": "actionable", "source": "Lydia",
         "segment_index": index, "segment_id": index, "reason": f"实体「Lydia」第 {index} 处"}
        for index in (0, 1, 2)]
    core.save_job_state("entitygroup00000001", state)
    view = adapter.review_queue("entitygroup00000001")
    rows = [item for item in view["queue_items"] if item.get("entity_source")]
    assert len(rows) == 1 and rows[0]["segment_indexes"] == [0, 1, 2]
    assert view["filter_counts"]["all"] == len(view["queue_items"])
    assert view["filter_counts"]["suggested"] == 1


# ---- 决策时看得见原文：审校详情里内嵌的原页 --------------------------------------

def _with_pdf(panel, monkeypatch, job_id="jobwithpdf00001", state=None):
    """挂上"这个任务有原页"，并把真正的取页/渲染挡在测试外面。"""
    seen = {"load": [], "locate": []}
    monkeypatch.setattr(panel.pdf, "load", lambda value: seen["load"].append(value))
    monkeypatch.setattr(panel.pdf, "locate",
                        lambda index, state=None: seen["locate"].append((index, state)))
    panel.set_pdf_job(job_id, state=state)
    return seen


def test_no_original_page_column_without_a_pdf_source(panel):
    panel.apply(_view(_item(segment_index=3)))
    assert not panel.pdf.isVisibleTo(panel)
    assert not panel.source_button.isVisibleTo(panel)


def test_selecting_an_item_locates_it_in_the_original_page(panel, monkeypatch):
    seen = _with_pdf(panel, monkeypatch)
    panel.apply(_view(_item(segment_index=7)))
    assert seen["load"] == ["jobwithpdf00001"]
    assert panel.pdf.isVisibleTo(panel), "有原页的任务：决定时原文就在旁边"
    assert panel.source_button.isVisibleTo(panel)
    assert seen["locate"][-1][0] == 7


def test_the_page_is_not_relocated_while_the_segment_is_unchanged(panel, monkeypatch):
    """同一段反复刷新（状态变化会重画整个面板）不该反复渲染同一页。"""
    seen = _with_pdf(panel, monkeypatch)
    panel.apply(_view(_item(segment_index=7)))
    panel.apply(_view(_item(segment_index=7)))
    assert [index for index, _ in seen["locate"]] == [7]
    panel.apply(_view(_item(segment_index=9)))
    assert [index for index, _ in seen["locate"]] == [7, 9]


def test_closing_the_original_page_hides_it_and_the_button_brings_it_back(panel, monkeypatch):
    seen = _with_pdf(panel, monkeypatch)
    panel.apply(_view(_item(segment_index=7)))
    panel._close_source()
    assert not panel.pdf.isVisibleTo(panel)
    assert panel.source_button.isVisibleTo(panel), "关掉之后还能再打开"
    panel.source_button.click()
    assert panel.pdf.isVisibleTo(panel)
    assert [index for index, _ in seen["locate"]] == [7, 7], "重新打开要重新定位"


def test_the_locator_gets_the_task_state_instead_of_rereading_it(panel, monkeypatch):
    state = {"pairs": [{"source": "Hello", "target": "你好"}]}
    seen = _with_pdf(panel, monkeypatch, state=state)
    panel.apply(_view(_item(segment_index=0)))
    assert seen["locate"][-1][1] == state


def test_entity_conflicts_locate_the_first_occurrence(panel, monkeypatch):
    """译名项是一个名字一行：原页翻到它第一次出现的那一段。"""
    seen = _with_pdf(panel, monkeypatch)
    panel.apply(_view(_item(id="finding:e", finding_id="e", entity_source="Lycidas",
                            segment_index=None, segment_indexes=[792, 795])))
    assert seen["locate"][-1][0] == 792


def test_segment_links_locate_in_the_original_page_not_the_grid(panel, monkeypatch):
    seen = _with_pdf(panel, monkeypatch)
    panel.apply(_entity_view())
    jumped = []
    panel.editRequested.connect(jumped.append)
    panel.conflict._link("seg:795")
    assert seen["locate"][-1][0] == 795
    assert jumped == [], "有原页时点段号是在原页里翻到它，不是跳去翻译页"
