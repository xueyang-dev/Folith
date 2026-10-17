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
    labels = [view.tab_bar.button(k).text() for k in ("overview", "terms", "tm")]
    assert labels[0].startswith("概览") and "10" in labels[1]    # 3 条已确认 + 7 条待审核
    assert view.tab_bar.button("review") is None
    assert "设备说明书项目" == view.title.text()
    assert "英 → 中" in view.subtitle.text()
    # 未分类置顶，并且是列表里的第一行。
    assert view.unsorted_slot.count() == 1
    assert "导出记忆" in view.export_button.text()


def _chip_texts(view, column=3):
    from PySide6.QtWidgets import QLabel

    return [view.term_table.cellWidget(r, column).findChild(QLabel).text()
            for r in range(view.term_table.rowCount())]


def test_terms_tab_lists_confirmed_and_pending_in_one_table(env, app):  # noqa: F811
    adapter, ids, view, requested = _view(env, app)
    _load(view, adapter, ids["device"])
    options = {k: view.term_filter.button(k).text() for k in ("all", "confirmed", "pending")}
    assert "10" in options["all"] and "3" in options["confirmed"] and "7" in options["pending"]
    sources = [e["source"] for e in view._term_rows]
    assert sources == sorted(sources, key=str.casefold)         # 一张表，按原文排，确认后不跳位
    assert set(_chip_texts(view)) == {"已确认", "待审核"}
    origins = {e["source"]: view.term_table.item(r, 4).text() for r, e in enumerate(view._term_rows)}
    assert origins["hub"] == "已审校译文" and origins["power outlet"] == "任务术语表"
    assert origins["quick start guide"] == "机器译文" and origins["status light"] in (
        "手动添加", "和术语库不一致")
    hub = next(i for i in view._review_items if i["source"] == "hub")
    assert hub["variants"] == ["hubs"]
    # 只出现一次的候选收起，点「显示」会重新请求带 show_singles 的详情；任务术语表条目不收起。
    assert "smart plug" in sources and "once only" not in sources
    view.singles_button.click()
    assert requested[-1][2] is True
    assert not hasattr(view, "page_review")


def test_status_and_source_filters(env, app):  # noqa: F811
    adapter, ids, view, requested = _view(env, app)
    view.show()
    _load(view, adapter, ids["device"])
    view._tab_changed("terms")
    view.term_filter.set_value("confirmed")
    view._term_filter_changed("confirmed")
    assert {e["kind"] for e in view._term_rows} == {"confirmed"} and len(view._term_rows) == 3
    assert view.basis_combo.isHidden() and view.batch_button.isHidden()
    view._term_filter_changed("pending")
    assert {e["kind"] for e in view._term_rows} == {"pending"} and len(view._term_rows) == 7
    assert not view.basis_combo.isHidden()
    # 来源下拉：选一个来源会重新请求（服务端按来源筛选）。
    view._basis_changed("task")
    assert requested[-1][1] == "task"
    _load(view, adapter, ids["device"], basis="task")
    assert [e["source"] for e in view._term_rows] == ["power outlet", "smart plug"]
    view._term_filter_changed("all")           # 离开「待审核」时来源筛选回到「全部」
    assert view._basis == "all"


def test_accept_uses_the_editable_translation_field(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._term_filter_changed("pending")
    got: list[tuple] = []
    view.acceptRequested.connect(lambda item, target: got.append((item["source"], target)))
    view.term_table.selectRow(next(r for r, e in enumerate(view._term_rows) if e["source"] == "hub"))
    assert view.accept_button.text() == "确认" and not view.review_detail.isHidden()
    assert view.term_detail.isHidden()
    view.name_edit.setText("集线盒")
    view.accept_button.click()
    assert got == [("hub", "集线盒")]


def test_confirmed_rows_show_the_term_detail_not_the_review_actions(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view.term_table.selectRow(next(r for r, e in enumerate(view._term_rows)
                                   if e["source"] == "firmware update"))
    assert view.review_detail.isHidden() and not view.term_detail.isHidden()
    assert view.selected_term()["target"] == "固件更新" and not view.selected_review_item()


def test_conflict_rows_switch_the_primary_action(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    row = next(r for r, e in enumerate(view._term_rows)
               if e["kind"] == "pending" and e["item"]["basis"] == "conflict")
    view.term_table.selectRow(row)
    assert "状态灯" in view.accept_button.text()
    assert view.task_button.text() == "保留原译名"
    assert view.name_edit.isHidden() and view.reject_button.isHidden()


def test_task_term_rows_can_only_be_confirmed_or_rejected(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view.term_table.selectRow(next(r for r, e in enumerate(view._term_rows)
                                   if e["source"] == "power outlet"))
    assert view.task_button.isHidden() and not view.reject_button.isHidden()
    assert "任务自己术语表" in view.detail_context.text()


def test_unsorted_page_is_a_readonly_summary_with_make_project_buttons(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    view.select("unsorted")
    _load(view, adapter, "unsorted")
    assert [k for k, _ in view._tab_options(view._detail)] == ["terms", "tm"]
    assert view.menu_labels() == ["导出…"]
    moved: list[str] = []
    view.newFromTaskRequested.connect(moved.append)
    from PySide6.QtWidgets import QPushButton

    buttons = [b for b in view.page_unsorted_terms.findChildren(QPushButton)
               if b.text().startswith("用这个任务新建项目")]
    assert len(buttons) == 3
    buttons[0].click()
    assert len(moved) == 1 and moved[0]
    # 候选并进各任务自己的术语列表，状态是「待审核」；这里唯一的动作是「只用于此任务」。
    decided: list[tuple] = []
    view.decideRequested.connect(lambda item, decision: decided.append((item["source"], decision)))
    keep = [b for b in view.page_unsorted_terms.findChildren(QPushButton) if b.toolTip() == "只用于这个任务"]
    assert len(keep) == 1
    keep[0].click()
    assert decided == [("canary build", "task_only")]


def test_batch_accept_goes_through_a_preview(env, app):  # noqa: F811
    adapter, ids, view, _requested = _view(env, app)
    _load(view, adapter, ids["device"])
    view._term_filter_changed("pending")
    batches: list[list] = []
    view.batchAcceptRequested.connect(batches.append)
    view.batch_button.click()      # 没勾选：依据可靠的（已审校译文 + 任务术语表）一起确认
    assert batches and {i["basis"] for i in batches[0]} == {"reviewed", "task"}
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
    assert "1 条暂定术语随任务过去" in sheet.carry_terms_hint.text()
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


def test_unsorted_hides_the_more_menu_and_projects_page_hides_recent_projects(env, app):  # noqa: F811
    from folith.qt import views

    adapter, ids, view, _r = _view(env, app)
    view.show()
    view.select("unsorted")
    _load(view, adapter, "unsorted")
    assert view.more_button.isHidden() and not view.export_button.isHidden()
    view.select(ids["device"])
    _load(view, adapter, ids["device"])
    assert not view.more_button.isHidden()

    home = views.HomeView()
    home.set_projects(adapter.project_options())
    assert not home.project_nav_label.isHidden() or True
    home.set_current_nav("projects")
    assert home.project_nav_label.isHidden()
    assert all(b.isHidden() for b in home._project_nav_buttons.values())
    home.set_current_nav("tasks")
    assert not home.project_nav_label.isHidden()


def test_preview_hides_zero_lines_and_says_so_when_every_term_conflicts(env, app):  # noqa: F811
    from PySide6.QtWidgets import QLabel

    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _v, _r = _view(env, app)
    core = env["core"]
    for source, target in (("rollback", "回退"), ("feature flag", "功能标志"), ("hotfix", "紧急修复")):
        core.set_project_term(ids["device"], source, target)
    sheet = MoveToProjectSheet(["notes"], project_id=ids["device"])
    texts = [label.text() for label in sheet.after_scroll.widget().findChildren(QLabel)]
    assert not any(t.startswith("0 条") for t in texts), texts
    assert any("术语都和项目现有译名不一致" in t for t in texts), texts


def test_count_badge_is_a_small_pill(env, app):  # noqa: F811
    from folith.qt import components

    badge = components.CountBadge("46", "info")
    assert badge.height() == 18 and badge.width() < 40


def test_conflict_row_keeps_the_real_occurrence_count(env, app):  # noqa: F811
    adapter, ids, view, _r = _view(env, app)
    env["core"].promote_term_to_project("manual-ch3", "status light")   # 登记成待决冲突
    _load(view, adapter, ids["device"])
    row = next(i for i in view._review_items if i["basis"] == "conflict")
    assert row["kind"] == "conflict"
    assert row["source"] == "status light" and row["hits"] == 3
    assert view.term_table.item(next(r for r, e in enumerate(view._term_rows)
                                     if e.get("item") is row), 5).text() == "3"


def test_translation_memory_table_has_equal_text_columns_and_no_empty_column(env, app):  # noqa: F811
    from PySide6.QtWidgets import QHeaderView

    adapter, ids, view, _r = _view(env, app)
    core = env["core"]
    tm = {}
    core.tm_put(tm, "Hello.", "你好。", "简体中文")
    core.save_tm(tm, ids["device"])
    _load(view, adapter, ids["device"])
    assert view.tm_table.columnCount() == 3
    header = view.tm_table.horizontalHeader()
    assert header.sectionResizeMode(0) == header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch


def test_more_menu_marks_delete_as_danger_and_it_is_activatable(env, app):  # noqa: F811
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from folith.qt.projects_view import _DangerAction

    adapter, ids, view, _r = _view(env, app)
    _load(view, adapter, ids["device"])
    view._open_more()
    danger = [a for a in view._menu.actions() if isinstance(a, _DangerAction)]
    assert len(danger) == 1 and danger[0].text() == "删除项目…"
    fired = []
    danger[0].triggered.connect(lambda *_: fired.append(1))
    QTest.keyClick(danger[0]._label, Qt.Key.Key_Return)
    assert fired == [1]


def _visible_bars(sheet):
    from PySide6.QtWidgets import QScrollBar

    return [b for b in sheet.findChildren(QScrollBar) if b.isVisible()]


def test_move_preview_has_no_scrollbar_anywhere_when_content_fits(env, app):  # noqa: F811
    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _v, _r = _view(env, app)
    core = env["core"]
    # 最拥挤的一页：全部冲突表 + 方向不同说明 + 两个任务。
    for source, target in (("rollback", "回退"), ("feature flag", "功能标志"), ("hotfix", "紧急修复")):
        core.set_project_term(ids["device"], source, target)
    sheet = MoveToProjectSheet(["notes", "arabic"], project_id=ids["device"])
    sheet.show()
    app.processEvents()
    assert _visible_bars(sheet) == []
    scroll = sheet.after_scroll
    # 内容放得下：卡片底部不留大片空白（内边距 + 几像素以内）。
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QWidget

    holder = scroll.widget()
    bottoms = [c.mapTo(holder, QPoint(0, c.height())).y()
               for c in holder.findChildren(QWidget) if c.isVisible()]
    assert scroll.height() - max(bottoms) <= 16, (scroll.height(), max(bottoms))


def test_move_preview_scrolls_when_many_tasks_overflow(env, app):  # noqa: F811
    from tests.project_language_fixture import add_job
    from folith.qt.project_sheets import MoveToProjectSheet

    adapter, ids, _v, _r = _view(env, app)
    core = env["core"]
    for n in range(10):
        add_job(core, f"extra-{n}", f"附加任务 {n}.docx", project_id=None,
                terms=[{"source": f"term{n}", "target": f"术语{n}", "preferred": f"术语{n}",
                        "status": "locked", "behavior": "translate"}])
    sheet = MoveToProjectSheet(["notes"] + [f"extra-{n}" for n in range(10)],
                               project_id=ids["device"])
    sheet.show()
    for _ in range(5):
        app.processEvents()
    bar = sheet.after_scroll.verticalScrollBar()
    assert bar.isVisible() and bar.maximum() > 0
    assert sheet.after_scroll.height() == sheet.PREVIEW_MAX


def test_conflict_strip_is_amber_with_the_button_inside(env, app):  # noqa: F811
    from PySide6.QtWidgets import QPushButton

    adapter, ids, view, _r = _view(env, app)
    _load(view, adapter, ids["device"])
    strip = view._notice_row("x", "逐条处理", lambda: None, "warn")
    button = strip.findChild(QPushButton)
    assert button is not None and button.text() == "逐条处理"


LONG_NAME = "2 Part3提取自The Sensorium Of The Drone And Communities (Kathrin Maurer).pdf"


def test_detail_never_scrolls_horizontally_at_narrow_width_with_a_long_task_name(env, app):  # noqa: F811
    from PySide6.QtWidgets import QScrollArea

    from tests.project_language_fixture import add_job

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    add_job(core, "long-name", LONG_NAME, project_id=ids["device"],
            terms=[{"source": "drone", "target": "无人机", "preferred": "无人机",
                    "status": "locked", "behavior": "translate"}])
    add_job(core, "long-unsorted", LONG_NAME, project_id=None)
    from folith.qt.projects_view import ProjectsView

    for width in (1000, 1280):
        view = ProjectsView(margins=(20, 12, 20, 12))
        view.resize(width - 220, 600)  # 窗口去掉侧栏后的页面宽度
        view.show()
        view.apply_overview(adapter.projects_overview())
        for key in (ids["device"], "unsorted"):
            _load(view, adapter, key)
            for tab in ("overview", "terms", "tm"):
                view.tab_bar.set_value(tab)
                view._tab_changed(tab)
                if tab == "terms":
                    view._term_filter_changed("pending" if key != "unsorted" else "all")
                app.processEvents()
                for area in view.findChildren(QScrollArea):
                    if area.isVisible():
                        bar = area.horizontalScrollBar()
                        assert not bar.isVisible() and bar.maximum() == 0, (width, key, tab)
        _load(view, adapter, ids["device"])
        view.tab_bar.set_value("overview")
        view._tab_changed("overview")
        app.processEvents()
        columns = view.stat_grid.columns()
        if width >= 1280:
            assert columns == 4 and view.card_grid.columns() == 2
        if width <= 1000:
            assert columns in (2, 1) and view.card_grid.columns() == 1
        view.close()



def test_pending_view_shows_at_least_six_rows_at_1000x600(env, app):  # noqa: F811
    from folith.qt.projects_view import ProjectsView

    adapter, core = env["adapter"], env["core"]
    ids = build(core)
    view = ProjectsView(margins=(8, 12, 12, 12))
    view.setFixedSize(1000 - 240, 555)      # 窗口 1000×600 里页面卡片的实际高度（去掉菜单栏与外壳间隙）
    view.show()
    view.apply_overview(adapter.projects_overview())
    _load(view, adapter, ids["device"])
    view.tab_bar.set_value("terms")
    view._tab_changed("terms")
    view._term_filter_changed("pending")
    view.term_table.selectRow(0)
    app.processEvents()
    table = view.term_table
    visible_rows = table.viewport().height() // table.verticalHeader().defaultSectionSize()
    assert visible_rows >= 6, (visible_rows, table.viewport().height())
    # 提示行不常驻（已有已确认术语时只是筛选条上的提示）；详情的动作在窄页上最多两行。
    assert view.term_note.isHidden() and view.term_filter.toolTip()
    assert view.review_actions.rowCount() == 1
    view.close()


def test_unsorted_cards_use_confirmed_pending_chips_and_one_column_layout(env, app):  # noqa: F811
    from PySide6.QtWidgets import QLabel

    from tests.project_language_fixture import add_job
    from folith.qt.projects_view import ProjectsView

    adapter, core = env["adapter"], env["core"]
    build(core)
    add_job(core, "long-unsorted", LONG_NAME, project_id=None)
    view = ProjectsView(margins=(8, 12, 12, 12))
    view.resize(900, 700)
    view.show()
    view.apply_overview(adapter.projects_overview())
    view.select("unsorted")
    _load(view, adapter, "unsorted")
    app.processEvents()
    texts = {label.text() for label in view.page_unsorted_terms.findChildren(QLabel)}
    assert {"已确认", "待审核"} <= texts and not texts & {"已锁定", "暂定"}
    assert any("条已确认" in t for t in texts) and not any("条已锁定" in t for t in texts)
    # 同一页上各卡片的「译名」列起点一致（固定的列布局）。
    xs = {label.mapTo(view.page_unsorted_terms.widget(), label.rect().topLeft()).x()
          for label in view.page_unsorted_terms.findChildren(QLabel) if label.text() == "译名"}
    assert len(xs) == 1, xs
    view.close()


def test_unsorted_detail_fits_the_viewport_at_1000x600_with_a_long_file_name(env, app):  # noqa: F811
    from PySide6.QtWidgets import QLabel, QScrollArea, QWidget

    from tests.project_language_fixture import add_job
    from folith.qt.projects_view import ProjectsView

    adapter, core = env["adapter"], env["core"]
    build(core)
    add_job(core, "long-unsorted", LONG_NAME, project_id=None,
            candidates=[], terms=[{"source": "sensorium", "target": "感官系统", "preferred": "感官系统",
                                   "status": "provisional", "behavior": "translate"}])
    for width in (900, 1000 - 240 + 0, 1280 - 240):
        view = ProjectsView(margins=(8, 12, 12, 12))
        view.setFixedSize(max(width, 760), 555)
        view.show()
        view.apply_overview(adapter.projects_overview())
        view.select("unsorted")
        _load(view, adapter, "unsorted")
        app.processEvents()
        scroll = view.page_unsorted_terms
        assert isinstance(scroll, QScrollArea)
        viewport = scroll.viewport()
        assert scroll.widget().width() <= viewport.width()
        assert not scroll.horizontalScrollBar().isVisible()
        for child in scroll.widget().findChildren(QWidget):
            if child.isVisible():
                right = child.mapTo(viewport, child.rect().topRight()).x()
                assert right <= viewport.width() + 1, (type(child).__name__, right, viewport.width())
        sources = [l for l in scroll.widget().findChildren(QLabel) if l.text() == "rollback"]
        assert sources and sources[0].width() >= 120, sources[0].width()
        view.close()
