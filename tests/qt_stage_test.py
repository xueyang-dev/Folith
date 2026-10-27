"""工作台的阶段模型与步骤条：准备 → 翻译 → 审校 → 交付。

钉住的是"现在走到哪一步、下一步做什么"这一个口径：

* 头部不再用三个互相对不上的芯片，而是由 `stage.stage_view` 一处算出每一步的状态；
* 审校一步的数字来自 `folith.workbench_view` 的同一份投影（与交付门禁同一规则）；
* 任务没开始时只有「准备」可去，后面三步是锁住的。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import stage  # noqa: E402


@pytest.fixture(autouse=True)
def chinese():
    from folith.qt import i18n

    i18n.set_language("zh-CN")
    yield


def _review(**progress) -> dict:
    """最小的审校投影：只放 stage_view 会读的字段。"""
    base = {"failed": 0, "stale": 0, "missing": 0, "blocking": 0, "skipped": 0}
    base.update(progress)
    ready = not any(base[key] for key in ("failed", "stale", "missing", "blocking"))
    return {"readiness": {"required": True, "ready": ready,
                          "status": "current" if ready else "failed",
                          "skipped_count": base["skipped"]},
            "progress": base,
            "primary_action": {"kind": "delivery", "label": "前往交付"}}


def _steps(view: dict) -> dict:
    return {item["id"]: item for item in view["steps"]}


def test_a_task_that_has_not_started_only_offers_preparation():
    view = stage.stage_view(started=False, runtime={}, state={}, review={}, delivery={})
    steps = _steps(view)
    assert view["current"] == stage.PREPARE
    assert steps["prepare"]["state"] == stage.CURRENT
    assert [steps[s]["state"] for s in ("translate", "review", "deliver")] == [stage.LOCKED] * 3
    assert view["action"] is None


def test_running_translation_shows_the_cores_own_progress_and_unit():
    view = stage.stage_view(
        started=True,
        runtime={"status": "running",
                 "stage_progress": {"completed": 2, "total": 8, "unit": "批次"}},
        state={"paras": ["p"] * 8, "pairs": [{"target": "a"}, {"target": "b"}, {"target": ""}]},
        review={}, delivery={})
    translate = _steps(view)["translate"]
    assert translate["state"] == stage.CURRENT
    # 已译 / 总段数，不是内部的「批次」（acceptance-feedback 第 5 条第 5 点）。
    assert translate["hint"] == "2/8 段"
    assert view["current"] == stage.TRANSLATE
    assert view["action"] is None, "运行中的控制在头部的运行按钮上，不重复推荐动作"


def test_streaming_recognition_is_not_crammed_into_the_step():
    state = {"paras": ["p"] * 8, "pairs": [{"target": "a"}, {"target": "b"}],
             "streaming_ingest": {"status": "running", "pages_done": 40, "pages_total": 308}}
    view = stage.stage_view(
        started=True,
        runtime={"status": "running",
                 "stage_progress": {"completed": 2, "total": 8, "unit": "批次"}},
        state=state, review={}, delivery={})
    assert _steps(view)["translate"]["hint"] == "2/8 段"


def test_unit_progress_beats_batch_progress_and_falls_back_to_the_segment_count():
    both = stage.stage_view(
        started=True,
        runtime={"status": "running", "progress_completed": 3, "progress_total": 10,
                 "stage_progress": {"completed": 1, "total": 4, "unit": "批次"}},
        state={}, review={}, delivery={})
    assert _steps(both)["translate"]["hint"] == "3/10 段"
    bare = stage.stage_view(started=True, runtime={"status": "running"},
                            state={"pairs": [{}, {}]}, review={}, delivery={})
    assert _steps(bare)["translate"]["hint"] == "0/2 段"
    nothing = stage.stage_view(started=True, runtime={"status": "idle"},
                               state={}, review={}, delivery={})
    assert _steps(nothing)["translate"]["hint"] == ""


def test_strict_terms_gate_holds_translation_on_the_terms_step():
    view = stage.stage_view(started=True, runtime={"status": "idle"}, state={"p1_done": True},
                            review={}, delivery={}, terms_blocked=True)
    translate = _steps(view)["translate"]
    assert translate["state"] == stage.ATTENTION and translate["hint"] == "待确认术语"
    assert view["current"] == stage.TRANSLATE


def test_a_failed_run_marks_translation_as_needing_attention():
    view = stage.stage_view(started=True, runtime={"status": "failed"},
                            state={"pairs": []}, review={}, delivery={})
    assert _steps(view)["translate"]["state"] == stage.ATTENTION


def test_failed_review_points_at_review_with_the_segments_to_retry():
    review = _review(failed=22)
    review["primary_action"] = {"kind": "review_segments", "label": "重试失败的 22 段",
                                "segment_ids": [3, 4]}
    view = stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}] * 647}, review=review,
        delivery={"can_deliver": False, "issues": ["审校未完成"]})
    steps = _steps(view)
    assert steps["translate"]["state"] == stage.DONE and steps["translate"]["hint"] == "647 段"
    assert steps["review"]["state"] == stage.ATTENTION
    assert steps["review"]["hint"] == "22 段未完成"
    assert steps["deliver"]["state"] == stage.TODO, "审校没过，交付还轮不到"
    assert view["current"] == stage.REVIEW
    assert view["action"]["kind"] == "review_segments"
    assert view["action"]["segment_ids"] == [3, 4]
    assert view["action"]["label"] == "重试失败的 22 段"


def test_review_hints_cover_stale_blocking_and_missing():
    def hint(**progress):
        view = stage.stage_view(started=True, runtime={"status": "completed"},
                                state={"p2_done": True}, review=_review(**progress),
                                delivery={})
        return _steps(view)["review"]

    assert hint(stale=2)["hint"] == "2 段需重审" and hint(stale=2)["state"] == stage.ATTENTION
    assert hint(blocking=1)["hint"] == "必须处理 1"
    assert hint(missing=5)["hint"] == "5 段待审" and hint(missing=5)["state"] == stage.CURRENT


def test_skipped_segments_are_visible_without_blocking_delivery():
    review = _review(skipped=4)
    review["readiness"].update(coverage_complete=False, coverage_status="partial")
    view = stage.stage_view(started=True, runtime={"status": "completed"},
                            state={"p2_done": True, "pairs": [{}] * 10},
                            review=review,
                            delivery={"can_deliver": True})
    steps = _steps(view)
    assert steps["review"]["state"] == stage.CURRENT
    assert steps["review"]["hint"] == "4 段按策略跳过"
    assert steps["deliver"]["state"] == stage.CURRENT
    assert steps["deliver"]["hint"] == "可以交付"


def test_blocking_findings_still_count_when_independent_review_is_off():
    """没启用独立审校的任务，旧的交付门禁仍然看未解决的阻断项。"""
    review = {"readiness": {"required": False, "ready": True, "status": "not_required"},
              "progress": {"blocking": 1},
              "primary_action": {"kind": "handle_finding", "label": "继续处理 1 个必须处理的问题",
                                 "item_id": "f1"}}
    view = stage.stage_view(started=True, runtime={"status": "completed"},
                            state={"p2_done": True}, review=review, delivery={})
    review_step = _steps(view)["review"]
    assert review_step["state"] == stage.ATTENTION and review_step["hint"] == "必须处理 1"
    assert view["action"]["kind"] == "handle_finding" and view["action"]["item_id"] == "f1"
    review["progress"]["blocking"] = 0
    cleared = stage.stage_view(started=True, runtime={"status": "completed"},
                               state={"p2_done": True}, review=review, delivery={})
    assert _steps(cleared)["review"]["state"] == stage.SKIPPED
    assert _steps(cleared)["review"]["hint"] == "未启用"
    # 跳过的审校不打"完成"的勾，但照样放行交付。
    assert _steps(cleared)["deliver"]["state"] == stage.CURRENT


def test_a_clean_review_hands_over_to_delivery():
    view = stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}]}, review=_review(),
        delivery={"can_deliver": True, "snapshot": {}})
    steps = _steps(view)
    assert steps["review"]["state"] == stage.DONE
    assert steps["deliver"]["state"] == stage.CURRENT and steps["deliver"]["hint"] == "可以交付"
    assert view["current"] == stage.DELIVER
    assert view["action"] == {"kind": "deliver", "label": "冻结并交付", "target": "deliver"}


def test_a_frozen_delivery_finishes_the_line():
    view = stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}]}, review=_review(),
        delivery={"can_deliver": False,
                  "snapshot": {"current": True, "latest": {"snapshot_version": 2}}})
    steps = _steps(view)
    assert steps["deliver"]["state"] == stage.DONE and steps["deliver"]["hint"] == "已冻结 v2"
    assert view["action"] is None


def test_other_delivery_blockers_are_flagged_on_the_delivery_step():
    view = stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True}, review=_review(),
        delivery={"can_deliver": False, "issues": ["译文未通过交付完整性检查"]})
    assert _steps(view)["deliver"]["state"] == stage.ATTENTION


def test_missing_inputs_never_raise():
    view = stage.stage_view(started=True, runtime=None, state=None, review=None,
                            delivery=None)
    assert [item["id"] for item in view["steps"]] == list(stage.STEP_IDS)


# ----------------------------------------------------------------- 步骤条 ----

@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _bar(qapp, view):
    from folith.qt.stagebar import StageBar

    bar = StageBar()
    bar.set_stages(view)
    return bar


def test_locked_steps_cannot_be_opened(qapp):
    bar = _bar(qapp, stage.stage_view(started=False, runtime={}, state={}, review={},
                                      delivery={}))
    seen: list[str] = []
    bar.pageRequested.connect(seen.append)
    for step in ("translate", "review", "deliver"):
        assert bar.steps[step].isEnabled() is False
        bar.steps[step].click()
    assert seen == []
    assert bar.steps["prepare"].state() == stage.CURRENT


def test_stage_hint_does_not_impose_its_preferred_width(qapp):
    bar = _bar(qapp, stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}]}, review=_review(failed=1), delivery={}))
    review = bar.steps["review"]

    assert review.hint() == "1 段未完成"
    assert "1 段未完成" in review.accessibleName()
    assert review.minimumSizeHint().width() < review.sizeHint().width()

    bar.resize(bar.minimumSizeHint().width(), bar.minimumSizeHint().height())
    bar.show()
    qapp.processEvents()
    assert review.width() < review.sizeHint().width(), \
        "步骤条压缩时应让状态提示退到无障碍名称，而不是继续撑宽工具栏"
    bar.close()


def test_steps_open_their_pages_and_prepare_is_inert_once_done(qapp):
    bar = _bar(qapp, stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}]}, review=_review(failed=1), delivery={}))
    seen: list[str] = []
    bar.pageRequested.connect(seen.append)
    bar.steps["review"].click()
    bar.steps["deliver"].click()
    bar.steps["prepare"].click()
    assert seen == ["review", "deliver"]
    assert bar.steps["prepare"].isEnabled() is False


def test_active_page_lights_the_matching_step_or_more(qapp):
    bar = _bar(qapp, stage.stage_view(
        started=True, runtime={"status": "completed"},
        state={"p2_done": True, "pairs": [{}]}, review=_review(), delivery={}))
    bar.set_more_pages([("terms", "术语"), ("report", "报告")],
                       actions=[("followup", "检查与修复"), ("activity", "运行记录")])
    bar.set_active_page("review")
    assert bar.steps["review"].is_active() and not bar.steps["deliver"].is_active()
    assert not bar.more_button.isChecked()
    bar.set_active_page("terms")
    # 正在看「更多」里的一页：按钮亮起并写出这一页的名字。
    assert bar.more_button.isChecked() and not bar.steps["review"].is_active()
    assert bar.more_button.text() == "术语"
    labels = [a.text() for a in bar.more_menu.actions()]
    assert labels == ["术语", "报告", "", "检查与修复", "运行记录"], labels
    checkable = [a.isCheckable() for a in bar.more_menu.actions() if a.text()]
    assert checkable == [True, True, False, False], "面板动作不是页面，不打勾"
    seen = []
    bar.pageRequested.connect(seen.append)
    bar.more_menu.actions()[3].trigger()
    assert seen == ["followup"]


def test_side_entries_hide_before_the_task_starts(qapp):
    bar = _bar(qapp, stage.stage_view(started=False, runtime={}, state={}, review={},
                                      delivery={}))
    bar.set_more_pages([("terms", "术语")], actions=[("followup", "检查与修复")])
    bar.set_side_visible(False)
    assert bar.more_button.isHidden()
    bar.set_side_visible(True)
    assert not bar.more_button.isHidden()
    bar.set_more_pages([])
    assert bar.more_button.isHidden(), "菜单里什么都没有时，更多整个收起"
