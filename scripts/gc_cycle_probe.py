"""P2-21：在 `gc.garbage` 里找**真正的环**（强连通分量），而不是连带对象。

方法（与 §8/§9 的更正一致）：

1. 一次 collect 之后取快照 `garbage`，得到 id 集合；
2. 建图：节点是快照里的对象，边是 `gc.get_referents(o)` 中**指向同一批垃圾**的那些；
3. Tarjan 求强连通分量：大小 >1 的分量，或带自环的单点，才是环；其余都是被环拖下来的连带对象；
4. 每个环报告：成员类型计数、帧/函数/cell 的代码位置、有没有 `BaseException` 实例、
   从该环能到达多少个 `_TaskSignals` / `QPainter` / `QPaintEvent`；
5. 跨 collect 聚合，按「每次打开」归一化（打开次数由 `open-job` 提交次数计）。

`FOLITH_GC_CYCLES=1` 打开；`FOLITH_GC_CYCLES_OUT` 指定输出（默认 `gc-cycles.json`）。
"""
from __future__ import annotations

import gc
import json
import os
import sys
import threading
import time
import types

ENV_ENABLE = "FOLITH_GC_CYCLES"
ENV_OUT = "FOLITH_GC_CYCLES_OUT"
DEFAULT_OUT = "gc-cycles.json"
QT_MODULES = ("PySide6", "shiboken6")
MAX_REPORTED = 5

_state: dict = {
    "installed": False,
    "path": None,
    "collections": 0,
    "opens": 0,
    "cycles": {},          # signature -> {"count": n, "sample": {...}}
    "scanned": 0,
}


def _is_qt(obj) -> bool:
    module = getattr(type(obj), "__module__", "") or ""
    if module.startswith(QT_MODULES):
        return True
    try:
        from PySide6.QtCore import QObject

        return isinstance(obj, QObject)
    except Exception:  # noqa: BLE001
        return False


def _describe(obj) -> str:
    """稳定、可读的类型名；帧/函数/cell 带上代码位置。"""
    cls = type(obj)
    if isinstance(obj, types.FrameType):
        code = obj.f_code
        return f"frame {os.path.basename(code.co_filename)}:{obj.f_lineno} {code.co_name}"
    if isinstance(obj, types.FunctionType):
        code = obj.__code__
        return f"function {obj.__qualname__} @ {os.path.basename(code.co_filename)}:{code.co_firstlineno}"
    if isinstance(obj, types.CellType):
        return "cell"
    if isinstance(obj, types.TracebackType):
        frame = obj.tb_frame
        return f"traceback {os.path.basename(frame.f_code.co_filename)}:{obj.tb_lineno} {frame.f_code.co_name}"
    if isinstance(obj, BaseException):
        return f"exception {cls.__name__}"
    module = getattr(cls, "__module__", "") or ""
    return f"{module}.{cls.__name__}"


def _edges(nodes: list, ids: set) -> dict:
    """只保留指向同一批垃圾的边（按 id 过滤）。"""
    graph: dict = {}
    for node in nodes:
        try:
            refs = gc.get_referents(node)
        except Exception:  # noqa: BLE001
            refs = ()
        graph[id(node)] = [id(ref) for ref in refs if id(ref) in ids and ref is not node]
    return graph


def _tarjan(graph: dict) -> list[list[int]]:
    """强连通分量（迭代版，避免深递归）。"""
    index: dict = {}
    low: dict = {}
    on_stack: set = set()
    stack: list = []
    result: list = []
    counter = [0]

    for root in list(graph):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, pointer = work.pop()
            if pointer == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = graph.get(node, [])
            recursed = False
            while pointer < len(neighbours):
                nxt = neighbours[pointer]
                pointer += 1
                if nxt not in index:
                    work.append((node, pointer))
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if recursed:
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    component.append(item)
                    if item == node:
                        break
                result.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result


def _reachable_qt(component: list[int], graph: dict, by_id: dict) -> dict:
    """从环出发能到达的 Qt 对象计数（同一批垃圾内）。"""
    seen = set(component)
    queue = list(component)
    counts: dict = {}
    while queue:
        node = queue.pop()
        for nxt in graph.get(node, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            obj = by_id.get(nxt)
            if obj is None:
                continue
            name = type(obj).__name__
            if name in {"_TaskSignals", "QPainter", "QPaintEvent"}:
                counts[name] = counts.get(name, 0) + 1
            queue.append(nxt)
    return counts


def _signature(members: list, by_id: dict) -> tuple:
    """环的指纹：类型计数 + 最具体的代码位置（用来跨 collect 聚合）。"""
    kinds: dict = {}
    location = ""
    for node in members:
        obj = by_id.get(node)
        if obj is None:
            continue
        label = _describe(obj)
        kinds[label] = kinds.get(label, 0) + 1
        if not location and label.startswith(("frame ", "function ", "traceback ")):
            location = label
    return (tuple(sorted(kinds.items())), location)


def _scan() -> None:
    _state["collections"] += 1
    snapshot = list(gc.garbage)
    if not snapshot:
        return
    _state["scanned"] += 1
    by_id = {id(obj): obj for obj in snapshot}
    graph = _edges(snapshot, set(by_id))
    for component in _tarjan(graph):
        self_loop = len(component) == 1 and component[0] in graph.get(component[0], ())
        if len(component) == 1 and not self_loop:
            continue
        members = component
        kinds: dict = {}
        exceptions = []
        for node in members:
            obj = by_id.get(node)
            if obj is None:
                continue
            label = _describe(obj)
            kinds[label] = kinds.get(label, 0) + 1
            if isinstance(obj, BaseException):
                exceptions.append(f"{type(obj).__name__}: {obj}")
        signature, location = _signature(members, by_id)
        entry = _state["cycles"].setdefault(signature, {
            "count": 0,
            "location": location,
            "kinds": dict(kinds),
            "exceptions": exceptions[:3],
            "reachable_qt": {},
            "size": len(members),
        })
        entry["count"] += 1
        for name, number in _reachable_qt(members, graph, by_id).items():
            entry["reachable_qt"][name] = max(entry["reachable_qt"].get(name, 0), number)
    gc.garbage.clear()


def _on_gc(phase: str, info: dict) -> None:
    if phase != "stop":
        return
    try:
        _scan()
    except Exception:  # noqa: BLE001 - 探针不能影响被测进程
        pass


def _install_open_counter() -> None:
    """数「打开」次数：`open-job` 的提交次数。"""
    try:
        from folith.qt import workers as _workers
    except Exception:  # noqa: BLE001
        return
    original = _workers.Task.__init__
    if getattr(original, "_p21_cycles", False):
        return

    def counted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if getattr(self, "label", "") == "open-job":
            _state["opens"] += 1

    counted._p21_cycles = True
    _workers.Task.__init__ = counted


def install(path: str | None = None, *, force: bool = False) -> bool:
    if _state["installed"] and not force:
        return False
    if not force and os.environ.get(ENV_ENABLE, "").strip() not in {"1", "true", "yes", "on"}:
        return False
    _state["path"] = path or os.environ.get(ENV_OUT) or DEFAULT_OUT
    gc.set_debug(gc.DEBUG_SAVEALL)
    gc.callbacks.append(_on_gc)
    _install_open_counter()
    _state["installed"] = True
    return True


def dump() -> dict:
    opens = max(_state["opens"], 1)
    ranked = sorted(_state["cycles"].values(), key=lambda e: e["count"], reverse=True)
    payload = {
        "collections": _state["collections"],
        "opens": _state["opens"],
        "distinct_cycles": len(ranked),
        "top": [
            {
                "count": entry["count"],
                "per_open": round(entry["count"] / opens, 3),
                "size": entry["size"],
                "location": entry["location"],
                "kinds": entry["kinds"],
                "exceptions": entry["exceptions"],
                "reachable_qt": entry["reachable_qt"],
            }
            for entry in ranked[:MAX_REPORTED]
        ],
        "at": time.time(),
    }
    path = _state["path"]
    if path:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass
    return payload
