"""空状态替换结构（docs/ui-audit/40-design-review-2026-10-16 第五节，P3）。

一页的数据整个为空时：不画表格、筛选、表单和禁用按钮，只有一个居中的说明
和最多一个动作。只是"这个筛选下没有"时，结构留着、在列表位置说一句。
作用于选中项的动作没选中就不出现（隐藏优于禁用）。
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

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, style, views  # noqa: E402
from folith.qt.projects_view import ProjectsView  # noqa: E402
from folith.qt.review_panel import ReviewPanel  # noqa: E402
from folith.qt.terms_panel import TermsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


def test_page_empty_state_has_one_action(app):
    state = style.EmptyState(title="空", hint="说明", action="做点什么", page=True)
    fired = []
    state.actionTriggered.connect(lambda: fired.append(True))
    state.action_button.click()
    assert fired == [True]
    assert state.property("page") == "true"
    state.set_action("")
    assert state.action_button.isHidden()


def test_review_that_is_off_is_one_empty_state(app):
    panel = ReviewPanel()
    asked = []
    panel.reviewAllRequested.connect(lambda: asked.append(True))
    panel.apply({"queue_items": [], "filter_counts": {},
                 "readiness": {"status": "not_required", "ready": True,
                               "label": "当前任务未启用独立审校", "detail": "此任务沿用原有交付流程"}})
    assert panel.content.isHidden(), "不再画 0 计数的筛选、空列表和空白详情"
    assert not panel.empty_state.isHidden()
    assert panel.empty_state.title_label.text() == "当前任务未启用独立审校"
    panel.empty_state.action_button.click()
    assert asked == [True]
    # 审校已完成、队列为空：没有要做的事，不给按钮。
    panel.apply({"queue_items": [], "readiness": {"status": "current", "ready": True,
                                                  "label": "当前译文已完成审校"}})
    assert panel.empty_state.action_button.isHidden()


def test_review_with_items_keeps_the_queue(app):
    panel = ReviewPanel()
    panel.apply({"queue_items": [{"id": "a", "filter_group": "pending", "title": "第 1 段",
                                  "summary": "x", "kind": "finding"}],
                 "readiness": {"status": "missing", "label": "还有内容待处理"}})
    assert not panel.content.isHidden() and panel.empty_state.isHidden()


def test_terms_with_no_entries_is_one_empty_state(app):
    panel = TermsPanel()
    panel.apply("job", {"rows": [], "started": False, "blocks_start": False,
                        "frozen": False, "quality_mode": False})
    assert not panel.empty_state.isHidden()
    for widget in (panel.table, panel.filter_bar, panel.banner, panel.editor_card):
        assert widget.isHidden(), widget
    panel.empty_state.action_button.click()
    assert not panel.editor_card.isHidden(), "点了「添加术语…」只露出新建表单"
    assert panel.table.isHidden() and panel.empty_state.isHidden()
    assert panel.delete_button.isHidden(), "没有选中行，作用于选中行的操作不出现"


def test_projects_page_with_no_projects_is_one_empty_state(app):
    view = ProjectsView()
    view.resize(1200, 800)
    view.apply_overview({})
    assert not view.empty.isHidden(), "没有项目也没有「未分类」时整页让位给一个空状态"
    assert view.tab_bar.isHidden() and view.pages.isHidden()
    asked = []
    view.new_button.clicked.disconnect()
    view.new_button.clicked.connect(lambda: asked.append(True))
    view.new_button.click()
    assert asked == [True]


def test_project_tabs_empty_out_cleanly(app):
    view = ProjectsView()
    view.apply_overview({"projects": [{"project_id": "p1", "name": "空项目", "job_count": 0,
                                       "pending": 0, "archived": False, "is_system": False,
                                       "direction_label": "未设定", "description": ""}],
                         "unsorted": None, "pending_total": 0})
    view.apply_detail({"project": {"project_id": "p1", "name": "空项目", "direction_label": "未设定",
                                   "description": ""},
                       "terms": [], "tm": [], "tasks": [], "log": [], "style_rule_count": 0,
                       "conflict_count": 0, "review_tasks": 0,
                       "review": {"groups": [], "collapsed": 0, "counts": {}, "total": 0,
                                  "items": []}})
    assert view.term_table.isHidden() and not view.term_empty.isHidden()
    assert view.tm_table.isHidden() and not view.tm_empty.isHidden()
    assert view.review_detail.isHidden()
    # 只要有待审核的术语，术语库就不是空的：空状态只在真的一条都没有时出现。
    item = {"source": "drone", "target": "无人机", "basis": "task", "basis_label": "任务术语表",
            "kind": "task_term", "hits": 1, "variants": [], "documents": ["a.pdf"], "job_id": "j1",
            "positions_known": True}
    view.apply_detail({"project": {"project_id": "p1", "name": "空项目", "direction_label": "未设定",
                                   "description": ""},
                       "terms": [], "tm": [], "tasks": [], "log": [], "style_rule_count": 0,
                       "conflict_count": 0, "review_tasks": 1,
                       "term_counts": {"confirmed": 0, "pending": 1, "all": 1},
                       "review": {"groups": [], "collapsed": 0, "counts": {"all": 1}, "total": 1,
                                  "items": [item]}})
    assert view.term_empty.isHidden() and not view.term_table.isHidden()
    added = []
    view._add_term = lambda values=None: added.append(True)
    view.term_empty.action_button.click()
    assert added == [True]


def _job(job_id: str) -> adapter.JobRow:
    return adapter.JobRow(job_id=job_id, filename=f"{job_id}.docx", project_id=None,
                          target_lang="简体中文", status_label="待处理", progress_label="",
                          runtime_status="idle", segment_count=2,
                          updated_at="2026-10-05T19:40:00", active=False)


def test_home_with_no_tasks_replaces_the_list_card(app):
    home = views.HomeView()
    home.resize(1400, 900)
    home.set_rows([])
    assert home.list_card.isHidden() and not home.empty_state.isHidden()
    assert home.empty_state.action_button.property("kind") == "secondary", \
        "主行动在侧栏，空状态里的导入是次级按钮"
    home.set_rows([_job("j1")])
    assert not home.list_card.isHidden() and home.empty_state.isHidden()
    home.table.clearSelection()
    home._selection_changed()
    assert home.delete_button.isHidden() and home.open_button.isHidden()
    home.select_job("j1")
    assert not home.delete_button.isHidden() and home.open_button.isEnabled()
    home.deleteLater()
