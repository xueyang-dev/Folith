"""Animations that actually run.

The shared Qt job sets QT_QPA_PLATFORM=offscreen, which makes reduced() true
before the system setting. This file is the FOLITH_REDUCE_MOTION=0 group: each
test sets that variable itself, and the workflow step sets it for the process
so the group is not only a monkeypatch. Proof is setCurrentTime, not a sleep.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QWidget

from folith.qt import motion


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication([])


def _allow_motion(monkeypatch) -> None:
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")


def test_env_zero_beats_offscreen(monkeypatch):
    _allow_motion(monkeypatch)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("FOLITH_QT_NONINTERACTIVE", "1")
    assert motion.reduced() is False


def test_running_animation_emits_intermediate_frames(app, monkeypatch):
    _allow_motion(monkeypatch)
    owner = QWidget()
    controller = motion.Motion(owner)
    seen: list[float] = []
    controller.animate("panel", 0.0, 1.0, seen.append, kind="local")
    animation = controller._active["panel"]
    assert animation.state() == QAbstractAnimation.State.Running
    duration = motion.DURATION["local"]
    frames = []
    for step in range(1, 5):
        animation.setCurrentTime(duration * step // 5)
        frames.append(seen[-1])
    assert len(frames) == 4
    assert all(0 < value < 1 for value in frames), frames
    assert frames[-1] > frames[0] > 0
    controller.stop("panel")
    owner.deleteLater()
