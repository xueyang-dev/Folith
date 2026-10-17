"""借鉴桌面 AI 客户端的四处交互手感（滚动渐隐、跟随到底、分割条、运行中锁定展开）。

钉住：

* 滚动区只在还能往那边滚的边缘渐隐；没东西可滚时不留任何效果；
* 运行记录停在底部时跟随新记录；翻上去之后不抢视口，浮出"回到最新"并计数；
  用户刚动过滚轮的 150 ms 内也不抢；
* 分割条点一下不动（4px 死区）、Esc 撤销拖拽、双击回到默认宽度、原页拖窄后收起；
* 最新一步运行中时详情锁定展开并跟着它，结束后交还收起的权利。
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

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QApplication, QLabel, QScrollArea, QVBoxLayout,  # noqa: E402
                               QWidget)

from folith.qt import activity_panel as activity_module  # noqa: E402
from folith.qt import components  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _rows(count: int, *, last: str = "completed") -> list[dict]:
    rows = [{"status": "completed", "label": f"步骤 {i}", "timestamp": f"08:{i % 60:02d}:00",
             "detail": f"第 {i} 步的说明"} for i in range(count)]
    if rows:
        rows[-1]["status"] = last
    return rows


# ---- 滚动边缘渐隐 -----------------------------------------------------------
def _scroll_area(lines: int) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    for i in range(lines):
        layout.addWidget(QLabel(f"line {i}"))
    area.setWidget(body)
    area.resize(240, 200)
    return area


def test_fade_follows_the_edges_that_can_still_scroll(app):
    area = _scroll_area(80)
    fade = components.scroll_fade(area)
    area.show()
    app.processEvents()
    bar = area.verticalScrollBar()
    assert bar.maximum() > 0
    assert fade.edges() == (False, True)
    assert area.viewport().graphicsEffect() is not None
    bar.setValue(bar.maximum() // 2)
    assert fade.edges() == (True, True)
    bar.setValue(bar.maximum())
    assert fade.edges() == (True, False)
    area.close()


def test_fade_leaves_no_effect_when_nothing_scrolls(app):
    area = _scroll_area(2)
    fade = components.scroll_fade(area)
    area.show()
    app.processEvents()
    assert fade.edges() == (False, False)
    assert area.viewport().graphicsEffect() is None
    area.close()


def test_fade_keeps_its_hands_off_someone_elses_effect(app):
    from PySide6.QtWidgets import QGraphicsBlurEffect

    area = _scroll_area(80)
    other = QGraphicsBlurEffect()
    area.viewport().setGraphicsEffect(other)
    components.scroll_fade(area)
    area.show()
    app.processEvents()
    area.verticalScrollBar().setValue(10)
    assert area.viewport().graphicsEffect() is other
    area.close()


# ---- 运行记录：跟随到底 / 回到最新 ---------------------------------------------
@pytest.fixture()
def panel(app):
    widget = activity_module.ActivityPanel()
    widget.resize(420, 320)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def test_pinned_panel_follows_new_rows(panel, app):
    panel.set_rows(_rows(40))
    app.processEvents()
    assert panel.distance_from_bottom() == 0 and panel.pinned()
    panel.set_rows(_rows(45))
    assert panel.distance_from_bottom() == 0
    assert panel.jump_button.isHidden()


def test_running_rows_keep_the_panel_pinned(panel, app):
    panel.set_rows(_rows(40, last="running"))
    app.processEvents()
    assert panel.live()
    assert panel.distance_from_bottom() == 0


def test_scrolled_up_panel_keeps_its_place_and_offers_the_latest(panel, app):
    panel.set_rows(_rows(40))
    bar = panel.list.verticalScrollBar()
    bar.setValue(0)
    assert not panel.pinned()
    assert not panel.jump_button.isHidden()
    assert panel.jump_button.text() == "回到最新"
    panel.set_rows(_rows(43))
    assert bar.value() == 0, "翻上去之后新记录不能把视口拽回底部"
    assert panel.jump_button.text() == "3 条新记录"
    panel.jump_button.click()
    assert panel.distance_from_bottom() == 0 and panel.pinned()
    assert panel.jump_button.isHidden()


def test_recent_wheel_input_is_not_overridden(panel, app):
    panel.set_rows(_rows(40))
    panel._user_input()  # 用户刚动了滚轮（还停在底部）
    before = panel.list.verticalScrollBar().value()
    panel.set_rows(_rows(41))
    assert panel.list.verticalScrollBar().value() == before
    panel._input_at = float("-inf")
    panel.set_rows(_rows(42))
    assert panel.distance_from_bottom() == 0


def test_jump_button_sits_inside_the_list(panel, app):
    panel.set_rows(_rows(40))
    panel.list.verticalScrollBar().setValue(0)
    app.processEvents()
    assert panel.jump_button.parent() is panel.list
    assert panel.list.rect().contains(panel.jump_button.geometry())


# ---- 只有时间线 + 完整日志 ---------------------------------------------------------
def test_panel_is_only_the_timeline_and_the_full_log_button(panel, app):
    """运行记录从进度条展开：没有标题、关闭按钮或步骤详情区，详情在悬停提示里。"""
    panel.set_rows(_rows(3, last="running"))
    for name in ("heading", "hint", "detail", "detail_toggle", "retry_button"):
        assert not hasattr(panel, name), name
    assert "第 2 步的说明" in panel.list.item(2).toolTip()
    layout = panel.layout()
    assert layout.indexOf(panel.list) < layout.count() - 1, "完整日志按钮在列表下面"
    assert panel.log_button.parent() is panel


# ---- 分割条 -------------------------------------------------------------------
@pytest.fixture()
def split(app):
    from PySide6.QtWidgets import QFrame

    splitter = components.ResizeSplitter(Qt.Orientation.Horizontal)
    panes = [QFrame(), QFrame(), QFrame()]
    for pane in panes:
        splitter.addWidget(pane)
    splitter.resize(900, 300)
    splitter.setSizes([300, 300, 300])
    splitter.show()
    app.processEvents()
    yield splitter, panes
    splitter.close()


def _handle_center(splitter, index: int) -> QPoint:
    return splitter.handle(index).rect().center()


def test_a_click_on_the_handle_does_not_move_the_panes(split, app):
    splitter, _ = split
    handle = splitter.handle(1)
    before = splitter.sizes()
    center = _handle_center(splitter, 1)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(handle, center + QPoint(3, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center + QPoint(3, 0))
    assert splitter.sizes() == before


def test_escape_puts_the_panes_back_where_the_drag_started(split, app):
    splitter, _ = split
    handle = splitter.handle(1)
    before = splitter.sizes()
    moved = []
    splitter.splitterMoved.connect(lambda *a: moved.append(a))
    center = _handle_center(splitter, 1)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(handle, center + QPoint(-120, 0))
    assert splitter.sizes() != before and splitter.dragging()
    QTest.keyClick(QApplication.focusWidget() or splitter, Qt.Key.Key_Escape)
    assert splitter.sizes() == before
    assert not splitter.dragging()
    assert moved, "撤销后要通知监听者重新读宽度"
    QTest.mouseMove(handle, center + QPoint(-200, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center + QPoint(-200, 0))
    assert splitter.sizes() == before, "撤销之后到松手前的移动不再生效"


def test_double_click_asks_for_a_reset(split, app):
    splitter, _ = split
    seen = []
    splitter.resetRequested.connect(seen.append)
    QTest.mouseDClick(splitter.handle(2), Qt.MouseButton.LeftButton,
                      pos=_handle_center(splitter, 2))
    assert seen == [2]


def test_dragging_a_pane_below_its_threshold_asks_to_collapse_it(split, app):
    splitter, panes = split
    splitter.set_collapse_threshold(panes[0], 240)
    seen = []
    splitter.collapseRequested.connect(lambda widget, width: seen.append((widget, width)))
    start = splitter.sizes()[0]
    handle = splitter.handle(1)
    center = _handle_center(splitter, 1)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=center)
    QTest.mouseMove(handle, center + QPoint(-150, 0))
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=center + QPoint(-150, 0))
    assert seen == [(panes[0], start)]


# ---- 工作台接线 ---------------------------------------------------------------
@pytest.fixture()
def bench(app, tmp_path, monkeypatch):
    import core
    from folith.qt import adapter, views

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("physics.docx")
    sources = [f"Paragraph {i}." for i in range(30)]
    state.update(paras=sources, p1_done=True, p2_done=True,
                 pairs=[{"source": text, "target": "译文。", "segment_uid": f"s{i}"}
                        for i, text in enumerate(sources)])
    core.save_job_state("physicsjob", state)
    view = views.WorkbenchView()
    view.resize(1440, 900)
    view.show()
    view.load("physicsjob", adapter.EngineConfig(), adapter.load_state("physicsjob"),
              adapter.segment_rows("physicsjob", adapter.load_state("physicsjob")), [])
    app.processEvents()
    yield view
    view.close()


def test_double_click_returns_the_inspector_to_its_default_width(bench, app):
    split = bench.translation_split
    side = split.indexOf(bench.right_panel)
    grid = split.indexOf(bench.grid_card)
    sizes = split.sizes()
    sizes[grid] -= 120
    sizes[side] += 120
    split.setSizes(sizes)
    split.splitterMoved.emit(0, side)
    assert bench._inspector_width == bench.INSPECTOR_WIDTH + 120
    split.resetRequested.emit(side)
    app.processEvents()
    assert bench._inspector_width == bench.INSPECTOR_WIDTH
    assert abs(split.sizes()[side] - bench.INSPECTOR_WIDTH) <= 1


def test_grid_cannot_be_dragged_shut(bench):
    split = bench.translation_split
    assert not split.isCollapsible(split.indexOf(bench.grid_card))


def test_collapsing_the_source_closes_it_and_remembers_the_width(bench, app):
    bench._pdf_ok = True
    bench.set_source_open(True)
    app.processEvents()
    assert bench.source_open()
    bench.translation_split.collapseRequested.emit(bench.source_clip, 480)
    app.processEvents()
    assert not bench.source_open()
    assert bench._pdf_width == 480
