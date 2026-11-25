"""P2-21：找出含 Qt 包装器的循环垃圾（macOS 本机也能跑，不需要等崩溃）。

``FOLITH_GC_GARBAGE=1`` 打开：``gc.set_debug(gc.DEBUG_SAVEALL)`` 让**所有**被判定为
垃圾的对象留在 ``gc.garbage`` 里（不释放），每次 collect 之后扫一遍，只关心
``type(o).__module__`` 以 ``PySide6`` / ``shiboken6`` 开头的对象：

* 按类型计数；
* 每种类型取 1–3 个样本，用 ``gc.get_referrers`` 往上爬 ≤4 层，打出引用链；
* 扫完清空 ``gc.garbage``，避免把进程内存撑爆。

输出（``FOLITH_GC_GARBAGE_OUT``，默认 ``gc-garbage.json``）是一个汇总对象：
``{"collections": n, "qt_types": {type: count}, "chains": [...]}``。
"""
from __future__ import annotations

import gc
import json
import os
import threading
import time

ENV_ENABLE = "FOLITH_GC_GARBAGE"
ENV_OUT = "FOLITH_GC_GARBAGE_OUT"
DEFAULT_OUT = "gc-garbage.json"
QT_MODULES = ("PySide6", "shiboken6")
MAX_CHAINS_PER_TYPE = 3
MAX_DEPTH = 4

_state: dict = {
    "installed": False,
    "path": None,
    "collections": 0,
    "qt_types": {},
    "chains": [],
    "samples_taken": {},
    "lock": threading.Lock(),
}



#: `TaskRunner` 的创建现场（id(runner) → {stack, parent}）。只用于归因，不求精确：
#: id 会被复用，所以它只用来回答「这个 runner 是哪条路径建的」。
_runner_origins: dict = {}
_task_origins: dict = {}


def _install_origin_tracking() -> None:
    """记录 TaskRunner 的创建栈，以及 Task 的 label。"""
    try:
        from folith.qt import workers as _workers
    except Exception:  # noqa: BLE001
        return
    origin_init = _workers.TaskRunner.__init__
    if getattr(origin_init, "_p21_origin", False):
        return

    def tracked_init(self, *args, **kwargs):
        origin_init(self, *args, **kwargs)
        try:
            import traceback

            frames = traceback.extract_stack()[-6:-1]
            _runner_origins[id(self)] = {
                "parent": type(self.parent()).__name__ if self.parent() else None,
                "stack": [f"{Path(fr.filename).name}:{fr.lineno} {fr.name}" for fr in frames],
            }
        except Exception:  # noqa: BLE001
            pass

    tracked_init._p21_origin = True
    _workers.TaskRunner.__init__ = tracked_init

    origin_task_init = _workers.Task.__init__
    if getattr(origin_task_init, "_p21_origin", False):
        return

    def tracked_task_init(self, *args, **kwargs):
        origin_task_init(self, *args, **kwargs)
        try:
            _state["created"] = _state.get("created", 0) + 1
            # 给 signals 打标：垃圾里直接读得到 label，不用 get_referrers 反查。
            try:
                self.signals._p21_label = getattr(self, "label", "")
                self.signals._p21_task = id(self)
            except Exception:  # noqa: BLE001
                pass
            _task_origins[id(self)] = {"label": getattr(self, "label", "")}
        except Exception:  # noqa: BLE001
            pass

    tracked_task_init._p21_origin = True
    _workers.Task.__init__ = tracked_task_init


def _attribute_signals(obj) -> dict:
    """一个 _TaskSignals 属于哪个 Task / runner，送达了没有。"""
    import gc as _gc

    info: dict = {}
    try:
        owners = [r for r in _gc.get_referrers(obj)
                  if hasattr(r, "__dict__") and r.__dict__.get("signals") is obj]
    except Exception:  # noqa: BLE001
        owners = []
    if not owners:
        return {"orphan": True}
    task = owners[0]
    info["label"] = getattr(task, "label", "")
    info["released"] = getattr(task, "fn", "?") is None
    info["args_len"] = len(getattr(task, "args", ()) or ())
    info["task_origin"] = _task_origins.get(id(task), {})
    try:
        runners = [r for r in _gc.get_referrers(task)
                   if isinstance(getattr(r, "__dict__", {}).get("_running"), list)
                   and task in r.__dict__["_running"]]
    except Exception:  # noqa: BLE001
        runners = []
    if runners:
        runner = runners[0]
        info["runner_origin"] = _runner_origins.get(id(runner), {})
        info["runner_running"] = len(runner.__dict__.get("_running") or [])
    else:
        info["runner_origin"] = "no runner reachable from the task"
    return info

def _is_qt(obj) -> bool:
    """PySide6/shiboken6 模块里的对象，**或者任何 QObject 子类实例**。

    后者很关键：`folith.qt.workers._TaskSignals` 这种自己写的 QObject 子类，
    `type(o).__module__` 是 `folith.qt.workers`，按模块名筛会漏掉——而它正是
    「Qt 对象被当成垃圾析构」的候选。
    """
    module = getattr(type(obj), "__module__", "") or ""
    if module.startswith(QT_MODULES):
        return True
    try:
        from PySide6.QtCore import QObject

        return isinstance(obj, QObject)
    except Exception:  # noqa: BLE001
        return False


def _describe(obj, depth: int = 0) -> str:
    try:
        name = type(obj).__name__
        module = getattr(type(obj), "__module__", "") or ""
        extra = ""
        if isinstance(obj, dict):
            extra = f" keys={sorted(map(str, obj.keys()))[:6]}"
        elif isinstance(obj, (list, tuple, set)):
            extra = f" len={len(obj)}"
        elif callable(obj):
            extra = f" callable={getattr(obj, '__qualname__', repr(obj))[:60]}"
        return f"{module}.{name}{extra}"
    except Exception:  # noqa: BLE001
        return "?"


def _chains_for(obj, limit_chains: int = 2) -> list[list[str]]:
    """从 obj 往上爬引用者（≤MAX_DEPTH），返回若干条链。"""
    found: list[list[str]] = []
    seen: set[int] = set()

    def walk(node, path: list[str], depth: int) -> None:
        if len(found) >= limit_chains or depth > MAX_DEPTH:
            return
        try:
            referrers = gc.get_referrers(node)
        except Exception:  # noqa: BLE001
            return
        for ref in referrers:
            if ref is path or id(ref) in seen:
                continue
            seen.add(id(ref))
            if ref is _state["qt_types"] or (isinstance(ref, dict) and ref is _state):
                continue
            if isinstance(ref, list) and ref is gc.garbage:
                continue
            child_path = path + [_describe(ref, depth)]
            if _is_qt(ref):
                walk(ref, child_path, depth + 1)
            else:
                found.append(child_path)
            if len(found) >= limit_chains:
                return

    try:
        walk(obj, [_describe(obj)], 0)
    except Exception:  # noqa: BLE001
        pass
    return found


def _scan() -> None:
    with _state["lock"]:
        _state["collections"] += 1
        _state["created"] = _state.get("created", 0)
        garbage = gc.garbage
        if not garbage:
            return
        qt_objects = [obj for obj in garbage if _is_qt(obj)]
        for obj in qt_objects:
            name = f"{getattr(type(obj), '__module__', '?')}.{type(obj).__name__}"
            if not name.startswith(QT_MODULES):
                _state.setdefault("folith_qt_types", {})
                _state["folith_qt_types"][name] = _state["folith_qt_types"].get(name, 0) + 1
        for obj in qt_objects:
            if type(obj).__name__ == "_TaskSignals":
                try:
                    info = {"label": getattr(obj, "_p21_label", "?"),
                            "task_id": getattr(obj, "_p21_task", None)}
                    _state.setdefault("labels", {})
                    key = info["label"] or "?"
                    _state["labels"][key] = _state["labels"].get(key, 0) + 1
                    _state.setdefault("task_signals", []).append(info)
                except Exception:  # noqa: BLE001
                    pass
            name = type(obj).__name__
            _state["qt_types"][name] = _state["qt_types"].get(name, 0) + 1
            taken = _state["samples_taken"].get(name, 0)
            if taken < MAX_CHAINS_PER_TYPE:
                _state["samples_taken"][name] = taken + 1
                for chain in _chains_for(obj):
                    _state["chains"].append({"type": name, "chain": chain})
        # 必须清空：DEBUG_SAVEALL 下这些对象不释放，留着会吃掉内存。
        garbage.clear()


def _on_gc(phase: str, info: dict) -> None:
    if phase != "stop":
        return
    if int(info.get("collected") or 0) == 0 and not gc.garbage:
        return
    try:
        _scan()
    except Exception:  # noqa: BLE001 - 探针不能影响被测进程
        pass


def install(path: str | None = None, *, force: bool = False) -> bool:
    if _state["installed"] and not force:
        return False
    if not force and os.environ.get(ENV_ENABLE, "").strip() not in {"1", "true", "yes", "on"}:
        return False
    _state["path"] = path or os.environ.get(ENV_OUT) or DEFAULT_OUT
    _install_origin_tracking()
    gc.set_debug(gc.DEBUG_SAVEALL)
    gc.callbacks.append(_on_gc)
    _state["installed"] = True
    return True


def dump() -> dict:
    payload = {
        "collections": _state["collections"],
        "folith_qt_types": dict(_state.get("folith_qt_types") or {}),
        "qt_types": dict(_state["qt_types"]),
        "chains": _state["chains"],
        "task_signals_labels": dict(_state.get("labels") or {}),
        "task_signals": _state.get("task_signals") or [],
        "runner_origins": list(_runner_origins.values()),
        "tasks_created": _state.get("created", 0),
        "tasks_released": _state.get("released", 0),
        "task_runs": _state.get("runs", 0),
        "task_runs_ok": _state.get("runs_ok", 0),
        "task_runs_raising": _state.get("runs_raising", 0),
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


def summary() -> dict:
    return {
        "installed": _state["installed"],
        "collections": _state["collections"],
        "qt_types": dict(_state["qt_types"]),
        "chains": len(_state["chains"]),
        "path": _state["path"],
    }
