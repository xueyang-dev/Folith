"""首页视图切换（表格 / 卡片）与项目对话框的界面用例。

卡片视图是"按项目分组浏览任务"的那一屏（旧 Web 界面首页的形态）。这里钉住三件事：

* 两种视图共用**同一份选中状态**：点卡片等于选中表格行，底部动作因此语义一致；
* 卡片按项目分组，未归属的任务有明确分组，空列表仍然走空状态；
* 列数随宽度变化（窄窗口不允许把卡片挤成一条）。

项目对话框侧只验证"外观积木确实换成了统一的一套"：行委托、图标、以及移动项目对话框里
那个复选框必须是带自绘对勾的 `style.CheckBox`。
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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, style, views  # noqa: E402
from folith.qt.dialogs import ProjectDialog, ProjectPickerDialog  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


def _job(job_id: str, filename: str, project_id: str | None = None,
         *, status: str = "待处理", segments: int = 2, active: bool = False) -> adapter.JobRow:
    return adapter.JobRow(
        job_id=job_id, filename=filename, project_id=project_id,
        target_lang="简体中文", status_label=status, progress_label="",
        runtime_status="running" if active else "idle",
        segment_count=segments, updated_at="2026-10-05T19:40:00",
        active=active)


ROWS = [
    _job("j1", "ecology-notes.docx", "p1"),
    _job("j2", "manual-ch1.docx", "p2"),
    _job("j3", "manual-ch2.docx", "p2"),
    _job("j4", "unfiled-draft.docx", None),
]
PROJECTS = [
    {"project_id": "p1", "name": "生态恢复研究", "job_count": 1},
    {"project_id": "p2", "name": "产品手册本地化", "job_count": 2},
    {"project_id": "", "name": "未分类", "job_count": 1, "is_system": True},
]


@pytest.fixture()
def home(app):
    view = views.HomeView()
    view.resize(1400, 900)
    view.set_projects(PROJECTS)
    view.set_rows(ROWS)
    yield view
    view.deleteLater()


def _headings(home) -> list[str]:
    return [label.text() for label in home.card_page.findChildren(views.QLabel)
            if label.objectName() == "groupHeading"]


def test_card_view_exists_and_starts_on_the_table(home):
    assert home.view_switch.value() == "table"
    assert home.stack.currentWidget() is home.table
    home.set_view_mode("cards")
    assert home.view_switch.value() == "cards"
    assert home.stack.currentWidget() is home.card_scroll


def test_cards_are_grouped_by_project(home):
    home.set_view_mode("cards")
    assert sorted(_headings(home)) == sorted(["产品手册本地化", "未归属项目", "生态恢复研究"])
    assert len(home._cards) == len(ROWS)
    # 分组标题带任务数，未归属的任务单独成组。
    assert home._project_label("") == "未归属项目"
    assert home._project_label("p1") == "生态恢复研究"


def test_clicking_a_card_selects_the_same_table_row(home):
    home.set_view_mode("cards")
    home._cards["j3"].clicked.emit("j3")
    assert home.selected_job_id() == "j3"
    assert home.open_button.isEnabled() is True
    assert home._cards["j3"].property("selected") == "true"
    assert home._cards["j1"].property("selected") == "false"


def test_table_selection_updates_cards(home):
    home.set_view_mode("cards")
    home.table.clearSelection()
    for row in range(home.table.rowCount()):
        item = home.table.item(row, 0)
        if str(item.data(views.Qt.ItemDataRole.UserRole)) == "j2":
            item.setSelected(True)
    assert home._cards["j2"].property("selected") == "true"
    assert home._cards["j1"].property("selected") == "false"


def test_double_clicking_a_card_opens_that_task(home):
    opened: list[str] = []
    home.openRequested.connect(opened.append)
    home.set_view_mode("cards")
    home._cards["j4"].activated.emit("j4")
    assert opened == ["j4"]
    assert home.selected_job_id() == "j4"


def test_empty_list_shows_the_empty_state_in_both_views(home):
    home.set_rows([])
    assert home.stack.currentWidget() is home.empty_page
    home.set_view_mode("cards")
    assert home.stack.currentWidget() is home.empty_page
    home.set_view_mode("table")
    assert home.stack.currentWidget() is home.empty_page


def test_column_count_follows_the_window_width(home):
    home.resize(1400, 900)
    assert home._card_columns() == 3
    home.resize(1000, 900)
    assert home._card_columns() == 2
    home.resize(700, 900)
    assert home._card_columns() == 1


def test_card_grid_is_rebuilt_without_ghost_widgets(home):
    home.set_view_mode("cards")
    first = home._cards["j1"]
    # 过滤到只剩一个项目：旧卡片必须**立刻**离开界面，而不是等 deleteLater 才消失。
    home.project_filter.setCurrentIndex(
        home.project_filter.findData("p2"))
    assert len(home._cards) == 2
    remaining = home.card_page.findChildren(views._TaskCard)
    assert len(remaining) == 2
    assert first not in remaining


def test_project_dialog_uses_the_shared_row_delegate_and_icon_buttons(app):
    dialog = ProjectDialog()
    dialog.show()
    app.processEvents()
    try:
        assert isinstance(dialog.table.itemDelegate(), style.RowDelegate)
        for button in (dialog.new_button, dialog.rename_button,
                       dialog.archive_button, dialog.delete_button):
            assert not button.icon().isNull(), button.text()
        if dialog.table.rowCount():
            assert not dialog.table.item(0, 0).icon().isNull(), "项目行应有图标"
    finally:
        dialog.close()


def test_project_picker_uses_painted_checkbox_and_folder_icons(app):
    picker = ProjectPickerDialog(PROJECTS, job_count=2)
    picker.show()
    app.processEvents()
    try:
        assert isinstance(picker.move_tm_box, style.CheckBox)
        item = picker.list.item(0)
        assert item is not None and not item.icon().isNull()
        assert picker.selected_project()
    finally:
        picker.close()

    empty = ProjectPickerDialog([], job_count=1)
    empty.show()
    app.processEvents()
    try:
        assert empty.empty_state.isHidden() is False
        assert empty.selected_project() == ""
    finally:
        empty.close()



# ------------------------------------------------------------ 首页搜索 -------

def test_home_search_filters_by_name_status_and_project(app):
    home = views.HomeView()
    home.resize(1400, 900)
    home.set_projects(PROJECTS)
    home.set_rows([
        _job("a1", "Skopos theory.pdf", "p1", status="已完成"),
        _job("b2", "Annual report.docx", "p2", status="待审校"),
        _job("c3", "论文初稿.docx", None, status="已完成"),
    ])
    try:
        assert home.table.rowCount() == 3
        home.search_edit.setText("skopos")
        assert home.table.rowCount() == 1
        assert home.table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "a1"
        home.search_edit.setText("已完成")
        assert home.table.rowCount() == 2
        home.search_edit.setText("已完成 论文")
        assert home.table.rowCount() == 1, "多个词必须同时命中"
        home.search_edit.setText("产品手册")      # 项目名也能搜
        assert home.table.rowCount() == 1
        home.search_edit.setText("没有这个")
        assert home.table.rowCount() == 0
        home.search_edit.clear()
        assert home.table.rowCount() == 3
    finally:
        home.deleteLater()
