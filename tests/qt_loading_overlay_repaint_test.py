"""B-109 · 遮罩不在回调里同步重绘（0.8.4 Windows G1a 崩溃路径）。

证据（W 的候选 1 `e33ca261`，Windows 冻结包、offscreen、默认口径，35 轮崩 2 次）：
两条栈都经过 `workers.py _done` → `window.py done` → `open_job` → `_show_loading`
→ `LoadingOverlay.present`，一次栈顶在 `present`（即它末尾的 `repaint()`），一次在
`ProgressTrack.paintEvent` 的 `painter.setBrush`，最后都是 Qt6Gui access violation。

这里钉住修法本身，而不是复现崩溃（本机是 macOS，复现不了 Windows 的 Qt6Gui 崩溃）：

1. `present()` 不再同步重绘——用 `update()` 把这一帧交给事件循环；
2. 它也不调用 `processEvents()`（注释里的约束：排队中的轮询定时器不能重入 open）；
3. 这一帧没有丢：事件循环过一轮之后遮罩确实被画出来；
4. 在 worker `done` 那种回调里调用 `open_job` 式的「显示遮罩」不会重入，也不会同步绘制。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.qt import components  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


def _paint_count(overlay: components.LoadingOverlay) -> dict:
    """Count real paints of *this* overlay by wrapping its ``paintEvent``."""
    counts = {"paints": 0}
    real = overlay.paintEvent

    def counting(event):
        counts["paints"] += 1
        return real(event)

    overlay.paintEvent = counting
    return counts


def _spy_repaint(monkeypatch) -> dict:
    """Record every ``QWidget.repaint`` call; a synchronous paint is the bug."""
    calls = {"n": 0}
    real = QWidget.repaint

    def counting(self, *args, **kwargs):
        calls["n"] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QWidget, "repaint", counting)
    return calls


def test_present_schedules_the_frame_instead_of_painting_now(app, monkeypatch):
    host = QWidget()
    host.resize(400, 300)
    host.show()
    overlay = components.LoadingOverlay(host)
    repaint_calls = _spy_repaint(monkeypatch)

    overlay.present("正在打开任务…")

    assert overlay.isVisible()
    assert overlay._label.text() == "正在打开任务…"
    assert repaint_calls["n"] == 0, "present() 不得同步重绘（崩溃栈顶就是它）"


def test_the_frame_is_not_lost_one_event_loop_turn_later(app, monkeypatch):
    host = QWidget()
    host.resize(400, 300)
    host.show()
    overlay = components.LoadingOverlay(host)
    _spy_repaint(monkeypatch)
    paints = _paint_count(overlay)

    overlay.present("正在打开任务…")
    app.processEvents()  # 事件循环转一轮：这就是 present() 之后调用方立刻会到达的地方

    assert paints["paints"] >= 1, "延迟绘制不能变成不绘制"


def test_present_never_processes_events(app, monkeypatch):
    """注释里的约束：不能在 present 里跑事件循环，否则排队的轮询可能重入 open。"""
    host = QWidget()
    host.resize(400, 300)
    host.show()
    overlay = components.LoadingOverlay(host)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("present() 不得调用 processEvents()")

    monkeypatch.setattr(QApplication, "processEvents", staticmethod(forbidden))
    overlay.present("正在载入译文…")
    assert overlay.isVisible()


def test_presenting_from_a_worker_done_callback_does_not_re_enter(app, monkeypatch):
    """worker 的 ``done`` 是排队回主线程的回调；在里面显示遮罩不能同步绘制。"""
    host = QWidget()
    host.resize(400, 300)
    host.show()
    repaint_calls = _spy_repaint(monkeypatch)
    re_entries = {"n": 0}

    def worker_done() -> None:  # 与 TaskRunner 把 on_done 投递到 GUI 线程等价
        re_entries["n"] += 1
        overlay = components.LoadingOverlay(host)
        overlay.present("正在打开任务…")

    worker_done()

    assert re_entries["n"] == 1
    assert repaint_calls["n"] == 0
