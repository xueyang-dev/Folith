"""#520 remaining: toast stacking, stack cap, and edge-safe coach-mark placement."""
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
from PySide6.QtCore import QRect, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.qt import notice_placement, notifications  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_toast_stack_caps_at_three_and_newest_is_on_top(app):
    parent = QWidget()
    parent.resize(1000, 700)
    stack = notifications.ToastStack(parent)
    t1 = stack.present("one", tone="info")
    t2 = stack.present("two", tone="info")
    t3 = stack.present("three", tone="info")
    t4 = stack.present("four", tone="info")
    assert len(stack.visible()) == 3
    assert t4._slot == 0
    assert t1.isHidden(), "oldest transient is dropped"
    assert t3._slot == 1 and t2._slot == 2
    # newest is top-most: its y is the smallest; older ones sit below it.
    assert t4.y() < t3.y() < t2.y()
    # the stack's geometry methods report the newest toast, not an empty widget
    assert stack.y() == t4.y() and stack.x() == t4.x()
    assert stack.width() == t4.width() and not stack.isHidden()


def test_error_toast_in_the_stack_is_not_auto_dropped(app):
    parent = QWidget()
    parent.resize(1000, 700)
    stack = notifications.ToastStack(parent)
    sticky = stack.present("error", error=True)
    for i in range(5):
        stack.present(f"info {i}", tone="info")
    assert not sticky.isHidden(), "a sticky error survives the cap"
    assert not sticky.timer.isActive()


def test_stale_slot_is_not_added_to_the_timer_on_hover(app):
    parent = QWidget()
    parent.resize(1000, 700)
    stack = notifications.ToastStack(parent)
    t = stack.present("ok", tone="success")
    t.enterEvent(None)
    assert not t.timer.isActive()
    t.leaveEvent(None)
    assert t.timer.interval() == notifications.DISMISS_MS


# ---- coach-mark placement (pure, no window) ----------------------------------

def test_tip_centres_below_the_anchor_when_there_is_room():
    window = QRect(0, 0, 1280, 800)
    anchor = QRect(500, 200, 100, 30)
    point = notice_placement.place_within(anchor, QSize(320, 150), window, window)
    assert point.x() == 500 + 50 - 160
    assert point.y() == 200 + 30 + 6


def test_tip_flips_above_near_the_bottom():
    window = QRect(0, 0, 1280, 800)
    anchor = QRect(500, 720, 100, 30)
    point = notice_placement.place_within(anchor, QSize(320, 150), window, window)
    rect = QRect(point, QSize(320, 150))
    assert rect.bottom() <= window.bottom()
    assert point.y() < anchor.y(), "flipped above the anchor"


@pytest.mark.parametrize("window_x,anchor_x", [(0, 5), (30, 0), (-20, 0)])
def test_tip_never_leaves_a_narrow_window(window_x, anchor_x):
    # A 360 px minimum-width window with a tip anchored near its left edge.
    window = QRect(window_x, 0, 360, 640)
    anchor = QRect(window_x + anchor_x, 100, 60, 30)
    point = notice_placement.place_within(anchor, QSize(320, 150), window, window)
    rect = QRect(point, QSize(320, 150))
    assert not notice_placement.clipped(rect, window), (rect, window)


def test_screen_bound_wins_when_window_is_off_screen():
    window = QRect(-100, 0, 360, 640)
    screen = QRect(0, 0, 1280, 800)
    anchor = QRect(-90, 100, 60, 30)
    point = notice_placement.place_within(anchor, QSize(320, 150), window, screen)
    rect = QRect(point, QSize(320, 150))
    assert not notice_placement.clipped(rect, screen)
