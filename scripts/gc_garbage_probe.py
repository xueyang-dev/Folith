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


def _is_qt(obj) -> bool:
    module = getattr(type(obj), "__module__", "") or ""
    return module.startswith(QT_MODULES)


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
        garbage = gc.garbage
        if not garbage:
            return
        qt_objects = [obj for obj in garbage if _is_qt(obj)]
        for obj in qt_objects:
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
    gc.set_debug(gc.DEBUG_SAVEALL)
    gc.callbacks.append(_on_gc)
    _state["installed"] = True
    return True


def dump() -> dict:
    payload = {
        "collections": _state["collections"],
        "qt_types": dict(_state["qt_types"]),
        "chains": _state["chains"],
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
