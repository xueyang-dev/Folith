"""P2-21：`capture_other_thread_frames` 不能把别的线程的帧拖进循环垃圾。

采样器用 `sys._current_frames()` 取一张 ``{ident: frame}`` 快照。那张字典**装着这个函数
自己的帧**，而这个帧的局部变量又装着那张字典——于是每次采样都留下一个「帧 ↔ 字典」的
二元环，环里挂着被检查线程的**帧对象**，连同它们局部变量里的一切：打开任务的工作状态、
`_TaskSignals`（一个 `QObject`）、绘制期的 `QPainter` 等等。

这些对象对 Python 来说不可达，所以循环 GC 会在**任意线程**上收掉它们；而 `QObject` 必须在
拥有它的线程上析构。Windows 上这就是 P2-21 的崩溃（主线程绘制时 access violation，或退出时
`0xC0000409`）。

这些测试钉住的是：采样返回之后，**别的线程的帧不再被它留住**，因此它们的局部变量里的
`QObject` 不会进入循环垃圾。退回旧实现（不清 `targets` / `frames`）时第一个测试变红。
"""
from __future__ import annotations

import gc
import threading

from PySide6.QtCore import QObject

from folith.qt.jank_monitor import capture_other_thread_frames


def _garbage_qobjects() -> list[str]:
    gc.set_debug(gc.DEBUG_SAVEALL)
    try:
        gc.collect()
        gc.collect()
        return [type(obj).__name__ for obj in gc.garbage if isinstance(obj, QObject)]
    finally:
        gc.set_debug(0)
        gc.garbage.clear()


def test_capturing_other_thread_frames_does_not_pin_their_locals() -> None:
    """工作线程结束后，它的局部 `QObject` 不能因为一次采样而进循环垃圾。"""
    gc.collect()
    ready = threading.Event()
    release = threading.Event()

    def worker() -> None:
        payload = QObject()  # noqa: F841 - 存在的意义就是被帧的局部变量引用
        ready.set()
        release.wait(5.0)

    thread = threading.Thread(target=worker, name="p21-frame-worker")
    thread.start()
    assert ready.wait(5.0)

    frames, _truncated = capture_other_thread_frames()
    assert frames, "the sampler saw no other thread"

    release.set()
    thread.join(5.0)
    assert not thread.is_alive()

    # 线程已经结束：它的帧只可能被采样器留下的环拖住。
    found = _garbage_qobjects()
    assert found == [], f"a sampled thread's frame was kept in cyclic garbage: {found}"


def test_the_snapshot_itself_still_describes_the_other_thread() -> None:
    """修掉环不能把采样结果也一起丢掉。"""
    ready = threading.Event()
    release = threading.Event()

    def worker() -> None:
        ready.set()
        release.wait(5.0)

    thread = threading.Thread(target=worker, name="p21-frame-worker-2")
    thread.start()
    assert ready.wait(5.0)
    try:
        frames, truncated = capture_other_thread_frames()
    finally:
        release.set()
        thread.join(5.0)

    assert frames, "the sampler returned nothing"
    names = [row.name for row in frames.values()]
    assert "p21-frame-worker-2" in names
    assert isinstance(truncated, bool)
    assert all(isinstance(row.frame, str) for row in frames.values())
