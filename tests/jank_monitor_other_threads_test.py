"""P2-21b：跨线程取帧可以在采样器内部单独关掉，采样器本身照跑。

`FOLITH_JANK_OTHER_THREADS` 是判别实验用的开关：关掉之后 `JankMonitor` 仍然记录主线程自身的
停顿，但不再调 `capture_other_thread_frames`（那一步会物化/释放**别的线程**的帧对象）。
"""
from __future__ import annotations

from folith.qt.jank_monitor import (
    capture_other_thread_frames,
    jank_other_threads_enabled,
    other_threads_getter,
)


def test_the_product_default_does_not_walk_other_threads(monkeypatch) -> None:
    """P2-21b：默认不碰别的线程的帧（这条是判定门槛，别改回去）。"""
    monkeypatch.delenv("FOLITH_JANK_OTHER_THREADS", raising=False)
    assert jank_other_threads_enabled() is False
    assert other_threads_getter() is not capture_other_thread_frames


def test_an_empty_value_means_the_default(monkeypatch) -> None:
    monkeypatch.setenv("FOLITH_JANK_OTHER_THREADS", "  ")
    assert jank_other_threads_enabled() is False


def test_the_switch_still_allows_disabling_explicitly(monkeypatch) -> None:
    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv("FOLITH_JANK_OTHER_THREADS", value)
        assert jank_other_threads_enabled() is False, value
        getter = other_threads_getter()
        assert getter is not capture_other_thread_frames
        frames, truncated = getter(None)
        assert frames == {} and truncated is False


def test_the_runner_switch_turns_the_walk_back_on(monkeypatch) -> None:
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FOLITH_JANK_OTHER_THREADS", value)
        assert jank_other_threads_enabled() is True, value
        assert other_threads_getter() is capture_other_thread_frames
