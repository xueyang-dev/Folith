"""读页过程中的事件通道：把工作线程里的请求重试 / 失败交给主线程去显示。

识别一页可能在服务商那里重试几分钟（例如中转站 HTTP 524，每次约两分钟）。这期间
进度不动，用户只看到「没有进展」。重试发生在工作线程，不能直接写任务状态；
这里让读页任务先把「当前页 + 接收函数」绑在线程上，传输层发生重试 / 失败时调用
:func:`emit`，接收函数（通常是线程安全的队列追加）把它交给主线程。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

_local = threading.local()
Sink = Callable[[dict], None]


def bind(page: Any, sink: Optional[Sink]) -> None:
    _local.context = (page, sink)


def unbind() -> None:
    _local.context = None


def emit(kind: str, **info: Any) -> None:
    """``kind``：``retry``（还会再试）或 ``failed``（这一页读失败了）。没绑定时什么也不做。"""
    context = getattr(_local, "context", None)
    if not context or context[1] is None:
        return
    try:
        context[1]({"page": context[0], "kind": kind, **info})
    except Exception:  # noqa: BLE001 - 状态展示不能影响识别
        pass
