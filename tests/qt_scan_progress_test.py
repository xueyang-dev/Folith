"""扫描书识别期间的工作台：空状态显示识别进度；情境提示不再引用已删除的控件。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import i18n, views  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def chinese():
    i18n.set_language("zh-CN")
    yield


def _bench(runtime, state=None):
    bench = views.WorkbenchView()
    bench._state = state if state is not None else {}
    bench._all_rows = []
    bench._last_runtime = runtime
    bench._setup_view = None  # 没有准备视图 = 按「已开始」处理
    return bench


def _running(done, total=75, status="running"):
    return {"status": status,
            "stage_progress": {"completed": done, "total": total, "unit": "页"}}


def test_empty_state_shows_recognition_progress_and_updates(app):
    bench = _bench(_running(3))
    bench._sync_segment_placeholder()
    assert bench.no_segments_state.title_label.text() == "正在识别第 3/75 页"
    assert "满 5 页后开始翻译" in bench.no_segments_state.hint_label.text()
    assert "点上方" not in bench.no_segments_state.hint_label.text()
    bench._last_runtime = _running(4)
    bench._sync_segment_placeholder()
    assert bench.no_segments_state.title_label.text() == "正在识别第 4/75 页"


def test_empty_state_for_a_short_book_and_for_a_paused_run(app):
    bench = _bench(_running(1, total=3, status="paused"))
    bench._sync_segment_placeholder()
    assert bench.no_segments_state.title_label.text() == "正在识别第 1/3 页"
    assert "满 3 页后开始翻译" in bench.no_segments_state.hint_label.text()


def test_other_runs_keep_the_ordinary_empty_state(app):
    for runtime in ({}, {"status": "idle", "stage_progress": {"completed": 2, "total": 9, "unit": "页"}},
                    {"status": "running", "stage_progress": {"completed": 2, "total": 9, "unit": "批次"}}):
        bench = _bench(runtime)
        bench._sync_segment_placeholder()
        assert bench.no_segments_state.title_label.text() == "还没有可编辑的段落"


@pytest.mark.parametrize("language", ["en", "es", "ru", "ar", "fr", "de"])
@pytest.mark.parametrize("state,title,hint", [
    ({}, '还没有可编辑的段落', '这份文档目前没有切分出的段落。点上方「开始翻译」会导入并切分正文；进度会自动保存，可以随时离开再回来。'),
    ({"paras": ["source"]}, '当前筛选下没有段落', '清空搜索词或把状态筛选切回「全部」，就能看到剩下的段落。'),
    ({"excluded_segments": [{"source": "source"}]}, '所有段落都被排除了', '被排除的段落不进入译文与交付。可以在「交付」页的已排除清单里恢复，原文与当前译文都完整保留。'),
])
def test_empty_states_use_selected_locale(app, language, state, title, hint):
    bench = _bench({}, state)
    i18n.set_language(language)
    try:
        bench._sync_segment_placeholder()
        assert bench.no_segments_state.title_label.text() == i18n.tr(title) != title
        assert bench.no_segments_state.hint_label.text() == i18n.tr(hint) != hint
    finally:
        bench.close()
        i18n.set_language("zh-CN")


def test_offer_tips_does_not_reference_removed_controls(app):
    from folith.qt import window as window_module

    offered = []
    bench = SimpleNamespace(
        _showing_setup=False, _pdf_ok=True,
        current_page_id=lambda: "translate",
        check_button=SimpleNamespace(tone=lambda: "ok"),
        run_status=object(),
        view_source_button=SimpleNamespace(isVisible=lambda: True))
    marks = SimpleNamespace(showing=lambda: False,
                            offer=lambda tip, anchor: offered.append((tip, anchor)) or True)
    stack = SimpleNamespace(currentWidget=lambda: bench)
    fake = SimpleNamespace(stack=stack, workbench=bench, isVisible=lambda: True,
                           coach_marks=lambda: marks)
    assert window_module.MainWindow._offer_tips(fake, {"status": "idle"}) == "pdf"
    assert offered[0][1] is bench.view_source_button
    bench.view_source_button = SimpleNamespace(isVisible=lambda: False)
    assert window_module.MainWindow._offer_tips(fake, {"status": "idle"}) == ""


def test_header_status_line_is_scrollable_not_elided_and_tooltip_has_full_text(app):
    group = views.RunStatusGroup()
    group.show()
    app.processEvents()
    long_text = "第 12 页：服务商超时（HTTP 524），正在重试 2/3；" * 4
    group.set_state(tone="retrying", line=long_text)
    app.processEvents()
    view = group.line_view
    assert group.line.text() == long_text and "…" not in group.line.text()
    assert group.line.toolTip() == long_text and view.toolTip() == long_text
    assert view.overflows()
    bar = view.horizontalScrollBar()
    assert bar.maximum() > 0 and bar.value() == 0
    # 横向滚轮 / Shift+滚轮滚动。
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    event = QWheelEvent(QPointF(10, 5), QPointF(10, 5), QPoint(0, 0), QPoint(-60, 0),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    app.sendEvent(view.viewport(), event)
    assert bar.value() > 0
    # 悬停自动滚动，移开回到开头。
    view._hold = 0
    view.enterEvent(None)
    assert view._timer.isActive()
    view._advance()
    assert bar.value() > 0
    view.leaveEvent(None)
    assert not view._timer.isActive() and bar.value() == 0
    group.set_state(tone="running", line="短")
    assert not view.overflows()
    group.close()
