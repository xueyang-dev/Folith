"""Top-bar run status group, run-log popover, progress track and check icon (P2).

docs/qt-beta/acceptance-feedback-2026-10-14.md §1, §6, §11, D3.
"""
from __future__ import annotations

import os

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Animations and Qt.Popup activation are timing-sensitive on the real
# Cocoa platform; like the other Qt test files, run deterministically.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import components, motion, views  # noqa: E402

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def _at(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _view(status, *, running, raw=None, total=3201, completed=2905, note=""):
    runtime = {"status": status, "runtime": dict(raw or {}, stage="translation")}
    return views.run_status_view(runtime, note=note, running=running, completed=completed,
                                 total=total, unit="段", now=NOW.timestamp())


def test_tones_and_lines_say_the_same_thing():
    tone, line = _view("running", running=True,
                       raw={"last_progress_at": _at(5), "model_wait_started_at": _at(12)})
    assert tone == "running" and line.startswith("翻译 · 2,905 / 3,201 段")

    tone, line = _view("running", running=True,
                       raw={"last_progress_at": _at(20), "model_wait_started_at": _at(12)})
    assert tone == "waiting" and "正在等待模型响应 · 12 秒" in line

    tone, line = _view("running", running=True, note="正在重试翻译请求（第 2/3 次尝试）",
                       raw={"operation_id": "translation_retry", "last_progress_at": _at(5)})
    assert tone == "retrying" and "第 2/3 次" in line

    tone, line = _view("running", running=True, raw={"last_progress_at": _at(300)})
    assert tone == "stalled" and "5 分钟没有进展" in line

    assert _view("stalled", running=False)[0] == "stalled"
    tone, line = _view("failed", running=False, note="服务商暂时不可用（HTTP 500）")
    assert tone == "failed" and "HTTP 500" in line
    assert _view("paused", running=False)[0] == "paused"

    tone, line = _view("completed", running=False,
                       raw={"started_at": _at(12 * 60), "finished_at": _at(0)})
    assert (tone, line) == ("done", "已完成 · 12 分钟")


def test_progress_track_keeps_the_progress_bar_api_and_tones(app):
    track = components.ProgressTrack()
    track.setRange(0, 10)
    track.setValue(4)
    assert (track.minimum(), track.maximum(), track.value()) == (0, 10, 4)
    assert track.fraction() == pytest.approx(0.4)
    track.setValue(99)
    assert track.value() == 10
    track.setRange(0, 0)
    assert track.indeterminate() and track.fraction() is None
    track.set_tone("nonsense")
    assert track.tone() == "running"
    for tone in components.PROGRESS_TONES:
        track.set_tone(tone)
        track.resize(160, 6)
        track.grab()  # paints in every tone without raising


def test_waiting_track_does_not_repaint_on_its_own(app, monkeypatch):
    """等待模型时进度条不再自己动：30 帧的扫光让整窗不停重绘，菜单栏和台前调度跟着闪。"""
    from PySide6.QtCore import QTimer

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    track = components.ProgressTrack()
    track.resize(160, 6)
    track.show()
    for tone in ("waiting", "retrying"):
        track.set_tone(tone)
        QTest.qWait(400)  # the tone cross-fade finishes
        assert not [timer for timer in track.findChildren(QTimer) if timer.isActive()], tone
    track.setRange(0, 0)  # indeterminate
    assert not [timer for timer in track.findChildren(QTimer) if timer.isActive()]
    track.close()


def test_reduce_motion_override_and_tests_are_deterministic(monkeypatch):
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    assert motion.reduced() is True
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    assert motion.reduced() is False
    monkeypatch.delenv("FOLITH_REDUCE_MOTION")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert motion.reduced() is True


def test_status_group_opens_the_run_log_by_click_and_keyboard(app):
    bench = views.WorkbenchView()
    bench.resize(1400, 800)
    bench.show()
    app.processEvents()
    bench.run_status.setVisible(True)
    app.processEvents()
    QTest.mouseClick(bench.run_status, Qt.MouseButton.LeftButton)
    app.processEvents()
    popover = bench.activity_popover
    assert popover.isVisible()
    assert popover.isAncestorOf(bench.activity_panel)
    # 从进度条下面长出来：居中对齐、没有缝，状态组与浮层同一块表面。
    # (浮层本身会被夹在屏幕里；颈部始终正对着状态组。)
    status = bench.run_status.mapToGlobal(bench.run_status.rect().center())
    left, right = popover._neck
    assert abs(popover.geometry().x() + (left + right) / 2 - status.x()) <= 1
    bottom = bench.run_status.mapToGlobal(bench.run_status.rect().bottomLeft()).y()
    assert popover.geometry().top() <= bottom + 1
    assert bench.run_status.property("tethered") is True
    assert popover.close_button.isHidden() and popover.title_label.isHidden()
    popover.close()
    app.processEvents()
    assert not popover.isVisible()
    assert bench.run_status.property("tethered") is False

    bench.run_status.setFocus()
    QTest.keyClick(bench.run_status, Qt.Key.Key_Return)
    app.processEvents()
    assert bench.activity_popover.isVisible()
    QTest.keyClick(bench.activity_popover, Qt.Key.Key_Escape)
    app.processEvents()
    assert not bench.activity_popover.isVisible()
    bench.show_page("activity")  # the old page id now opens the popover
    app.processEvents()
    assert bench.activity_popover.isVisible()
    bench.activity_popover.close()
    bench.close()


def test_check_icon_tones(app):
    button = views.CheckStatusButton()
    button.set_count(0, "danger")
    assert button.tone() == "" and not button.badge.isVisibleTo(button)
    button.set_count(2, "warning")
    assert button.tone() == "warning" and button.badge.text() == "2"
    button.set_count(1, "danger")
    assert button.tone() == "danger" and "1 项待处理" in button.toolTip()


def test_every_workbench_tool_button_has_a_designed_style(app):
    """未指定样式的 QToolButton 会退回 macOS 原生的灰色斜面按钮（「筛选」「查找替换…」
    「字数统计…」就是这样）。Qt 自己的内部按钮（输入框清除、页签滚动）除外。"""
    from PySide6.QtWidgets import QLineEdit, QTabBar, QToolButton

    styled = {"roundButton", "toolbarButton", "issueToggle"}
    bench = views.WorkbenchView()
    bench.resize(1400, 900)
    bench.show()
    app.processEvents()
    plain = [button.text() or button.toolTip() or repr(button)
             for button in bench.findChildren(QToolButton)
             if not isinstance(button.parent(), (QLineEdit, QTabBar))
             and button.objectName() not in styled]
    bench.close()
    assert plain == []


def test_word_count_lives_in_the_bottom_left_line(app):
    bench = views.WorkbenchView()
    assert not hasattr(bench, "word_detail_button")
    assert isinstance(bench.word_total_label, views.ClickableLabel)
    assert bench.word_selection_label.parent() is not bench.word_bar


def test_shared_colours_cannot_be_dimmed_by_one_caller():
    """回归：缩略图阴影对 qcolor() 的返回值 setAlpha(26)，因为它是缓存里的同一个
    对象，工作台里所有用 text-primary 的译文都变成了 10% 不透明。"""
    from folith.qt import theme

    first = theme.qcolor("#131c2e")
    first.setAlpha(26)
    assert theme.qcolor("#131c2e").alpha() == 255
