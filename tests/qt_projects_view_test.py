"""项目页（主从布局）与「移到项目」面板。数据来自合成夹具，不含真实文档。"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import style  # noqa: E402
from tests.project_language_fixture import build  # noqa: E402
from tests.qt_delivery_supplement_test import env  # noqa: F401,E402  (fixture)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    return instance


def _view(env, app):  # noqa: F811
    from folith.qt.projects_view import ProjectsView

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    view = ProjectsView()
    view.resize(1200, 760)
    requested: list[tuple] = []
    view.detailRequested.connect(lambda *a: requested.append(a))
    view.apply_overview(adapter.projects_overview())
    return adapter, ids, view, requested


def _load(view, adapter, key, **kw):
    detail = adapter.unsorted_detail(**kw) if key == "unsorted" else adapter.project_detail(key, **kw)
    view.apply_detail(detail)


def test_overview_opens_the_project_with_the_most_pending_and_pins_unsorted(env, app):  # noqa: F811
    adapter, ids, view, requested = _view(env, app)
    assert view.selected_key() == ids["device"]
    assert requested[-1][0] == ids["device"]
    _load(view, adapter, ids["device"])
    labels = [view.tab_bar.button(k).text() for k in ("overview", "terms", "tm", "review")]
    assert labels[0].startswith("概览") and "3" in labels[1] and "5" in labels[3]
    assert "设备说明书项目" == view.title.text()
    assert "英 → 中" in view.subtitle.text()
    # 未分类置顶，并且是列表里的第一行。
    assert view.unsorted_slot.count() == 1
    assert "导出记忆" in view.export_button.text()


def test_review_tab_groups_by_document_with_basis_labels(env, app):  # noqa: F811
    adapter, ids, view, requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._tab_changed("review")
    labels = [view.review_tree.item(r, 3).text() for r in view._review_rows]
    assert set(labels) == {"已审校译文", "机器译文", "和术语库不一致"}
    assert not any("0." in v for v in labels), "不再显示小数置信度"
    hub = next(i for i in view._review_items if i["source"] == "hub")
    assert hub["variants"] == ["hubs"]
    # 只出现一次的收起，点「显示」会重新请求带 show_singles 的详情。
    assert view.singles_button.isVisible() or not view.singles_button.isHidden()
    view.singles_button.click()
    assert requested[-1][2] is True


def test_accept_uses_the_editable_translation_field(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._tab_changed("review")
    got: list[tuple] = []
    view.acceptRequested.connect(lambda item, target: got.append((item["source"], target)))
    view.review_tree.selectRow(next(iter(view._review_rows)))
    assert view.accept_button.text() == "收进术语库"
    view.name_edit.setText("集线盒")
    view.accept_button.click()
    assert got == [("hub", "集线盒")]


def test_conflict_rows_switch_the_primary_action(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._tab_changed("review")
    row = next(r for r, i in view._review_rows.items()
               if view._review_items[i]["basis"] == "conflict")
    view.review_tree.selectRow(row)
    assert "状态灯" in view.accept_button.text()
    assert view.task_button.text() == "保留原译名"
    assert view.name_edit.isHidden() and view.reject_button.isHidden()


def test_unsorted_page_is_a_readonly_summary_with_make_project_buttons(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    view.select("unsorted")
    _load(view, adapter, "unsorted")
    assert [k for k, _ in view._tab_options(view._detail)] == ["terms", "tm", "review"]
    assert view.menu_labels() == ["导出…"]
    moved: list[list] = []
    view.moveRequested.connect(moved.append)
    from PySide6.QtWidgets import QPushButton

    buttons = [b for b in view.page_unsorted_terms.findChildren(QPushButton)
               if b.text().startswith("用这个任务新建项目")]
    assert len(buttons) == 3
    buttons[0].click()
    assert len(moved) == 1 and len(moved[0]) == 1


def test_batch_accept_goes_through_a_preview(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._tab_changed("review")
    batches: list[list] = []
    view.batchAcceptRequested.connect(batches.append)
    view.batch_button.click()
    assert batches and all(i["basis"] == "reviewed" for i in batches[0])
    plan = adapter.preview_accept(ids["device"], batches[0])
    assert plan["add"] and not plan["differs"]
    from folith.qt.project_sheets import BatchAcceptDialog

    dialog = BatchAcceptDialog(plan, "设备说明书项目")
    assert dialog.add_count == len(plan["add"])


def test_move_sheet_new_project_shows_counts_and_builds_the_request(env, app):  # noqa: F811
    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _view_, _r = _view(env, app)
    sheet = MoveToProjectSheet(["notes"])
    sheet.mode.set_value("new")
    sheet._refresh()
    assert sheet.carry_terms_title.text() == "3 条已锁定的术语，收进项目术语库"
    assert "1 条暂定术语留在任务里" in sheet.carry_terms_hint.text()
    assert "2 条翻译记忆" in sheet.carry_tm_title.text()
    assert sheet.name_edit.text() == "Release notes 4.2"    # 取自文件名（去掉扩展名）
    sheet.name_edit.setText("发布说明")
    sheet.carry_tm.setChecked(False)
    request = sheet.request()
    assert request["new_project"] == {"name": "发布说明"}
    assert request["carry_terms"] is True and request["carry_tm"] is False
    assert sheet.ok_button.text() == "新建并移入"


def test_move_sheet_existing_previews_conflicts_and_direction_mismatch(env, app):  # noqa: F811
    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _view_, _r = _view(env, app)
    core = env["core"]
    core.set_project_term(ids["device"], "rollback", "回退")
    sheet = MoveToProjectSheet(["notes", "arabic"], project_id=ids["device"])
    preview = sheet._preview
    first, second = preview["reports"]
    assert first["terms_conflicting"] == [{"source": "rollback", "task": "回滚", "project": "回退"}]
    assert second["direction_mismatch"] and second["suggested_project"]["project_id"] == ids["middle"]
    assert sheet.ok_button.text() == "移动 2 个任务"
    # 点「改放进…」切到方向相同的项目：预览随之重算，方向不同的提示消失。
    sheet._pick(ids["middle"])
    assert sheet.target_project() == ids["middle"]
    assert sheet.request()["project_id"] == ids["middle"]


def test_move_sheet_to_unsorted_carries_nothing(env, app):  # noqa: F811
    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _view_, _r = _view(env, app)
    sheet = MoveToProjectSheet(["manual-ch3"])
    sheet._pick(env["core"].SYSTEM_PROJECT_ID)
    report = sheet._preview["reports"][0]
    assert report["carries_nothing"] and not report["terms_added"]


def test_sidebar_badge_shows_the_total_pending(env, app):  # noqa: F811
    from folith.qt import views

    adapter, ids, _view_, _r = _view(env, app)
    home = views.HomeView()
    home.set_pending_badge(adapter.pending_total())
    assert home.pending_badge.text() == str(adapter.pending_total())
    assert not home.pending_badge.isHidden()
    home.set_pending_badge(0)
    assert home.pending_badge.isHidden()
    assert not hasattr(home, "library_button")
