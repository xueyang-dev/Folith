#!/usr/bin/env python3
"""运行时探测器：找出制造「过期 PySide 包装器」的布局调用点。

背景（见 ``docs/segfault-bisect-guide-0.8.1.md`` 与
``tests/qt_layout_item_wrapper_test.py``）：PySide6 里把一个**已经有条目**的子布局
挂到一个**已经装在控件上**的布局上（``outer.addLayout(inner)``，outer 已 attach、
inner.count() > 0），PySide 会立刻为 inner 里的每个条目创建 ``QWidgetItem``
Python 包装器。这些包装器被外层布局的包装器持有；C++ 侧条目随重建被 Qt 删除后，
Shiboken 的登记表里仍留着它们，之后同地址的新对象会被错认，进程在**不相关**的行
SIGSEGV。正确写法是先挂布局、再往里填控件。

本脚本做四件事：

1. monkeypatch 布局挂接 API（``QBoxLayout.addLayout/insertLayout``、
   ``QGridLayout.addLayout``、``QFormLayout.setLayout``、``QWidget.setLayout``、
   ``QLayout.addItem`` 系列），在调用前用 ``parentWidget()/parent()`` 判断接收方
   是否已挂在控件上、子布局是否已有条目；命中时记录调用点、Python 栈、子布局条目
   数，并实测调用前后的 ``QWidgetItem`` 包装器增量。
2. 提供包装器快照/差分：统计 ``QWidgetItem`` 与常见受害类型（``QListWidgetItem``
   ``QTreeWidgetItem`` ``QTableWidgetItem`` ``QSpacerItem``）的 total/valid/stale，
   按操作点（checkpoint）报告增长归因。
3. 驱动真实负载（离屏）：完整 MainWindow 构建/切页/resize/主题切换/交付页刷新、
   DeliveryWorkspace 反复 set_model、PdfPanel 反复 resize（``_place_tools`` 往返）、
   ReviewPanel 反复 apply、扫描占位刷新、通知刷新（activity 事件 + set_task_notice）。
4. 以 pytest 插件方式跑现有 Qt 套件，记录每个用例前后的包装器增量。

用法::

    # 全部内置负载，写机器可读结果
    QT_QPA_PLATFORM=offscreen python scripts/qt_stale_wrapper_probe.py \
        --scenario all --json /tmp/qt_probe_all.json

    # 单个负载，反复多次（配合 MallocScribble=1 放大 UAF）
    MallocScribble=1 QT_QPA_PLATFORM=offscreen python scripts/qt_stale_wrapper_probe.py \
        --scenario delivery --repeat 300 --json /tmp/qt_probe_delivery.json

    # 跑现有套件（每个测试文件一个子进程，插件注入）
    QT_QPA_PLATFORM=offscreen python scripts/qt_stale_wrapper_probe.py \
        --suite all --json /tmp/qt_probe_suite.json

    # 单个测试文件也可以直接挂插件
    QT_QPA_PLATFORM=offscreen FOLITH_WRAPPER_PROBE_JSON=/tmp/x.json \
        PYTHONPATH=scripts pytest -p qt_stale_wrapper_probe tests/qt_scan_progress_test.py

    # 真实路径（源码模式应用 + selftest：启动→OCR 扫描→翻译→审校→冻结交付）
    QT_QPA_PLATFORM=offscreen python scripts/qt_stale_wrapper_probe.py \
        --scenario baseline --baseline-cases eng --json /tmp/qt_probe_baseline.json

退出码：0 正常；1 参数/环境错误；3 探测过程中发现可疑调用（有包装器增长或形式命中）。
只读产品代码：本文件是新脚本，不改 ``folith/qt/``。
"""
from __future__ import annotations

import argparse
import atexit
import gc
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

#: 目标树。默认是本脚本所在的 worktree；用 FOLITH_PROBE_ROOT 可以显式指定
#: （例如对未修复的旧检出复跑同一个探测：FOLITH_PROBE_ROOT=/path/to/old/tree）。
ROOT = Path(os.environ.get("FOLITH_PROBE_ROOT") or
            Path(__file__).resolve().parents[1]).expanduser().resolve()

# 这个 venv 里有一个 editable 安装把 ``folith`` 指向主仓库
# (<主工作树>)。脚本方式运行时 sys.path[0] 是 scripts/，cwd
# 不在 sys.path 里，于是 ``import folith`` 会绕过当前 worktree。必须把 ROOT
# 插到最前面，否则探测的是另一棵树（2026-11-09 的第一次探测就踩了这一点）。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_JSON = "FOLITH_WRAPPER_PROBE_JSON"
ENV_FLOW = "FOLITH_WRAPPER_PROBE_FLOW"

VICTIM_TYPES = (
    "QWidgetItem",
    "QListWidgetItem",
    "QTreeWidgetItem",
    "QTableWidgetItem",
    "QSpacerItem",
)

#: 挂接 API → (类名, 方法名, 被挂接布局/条目在参数里的位置, 语义)
ATTACH_SPECS = (
    ("QBoxLayout", "addLayout", 0, "attach"),
    ("QBoxLayout", "insertLayout", 1, "attach"),
    ("QGridLayout", "addLayout", 0, "attach"),
    ("QFormLayout", "setLayout", 2, "attach"),
    ("QWidget", "setLayout", 0, "widget_set_layout"),
    ("QLayout", "addItem", 0, "attach_item"),
    ("QBoxLayout", "addItem", 0, "attach_item"),
    ("QBoxLayout", "insertItem", 1, "attach_item"),
    ("QGridLayout", "addItem", 0, "attach_item"),
)

MOVE_SPECS = (
    ("QBoxLayout", "addWidget", 0),
    ("QBoxLayout", "insertWidget", 1),
    ("QGridLayout", "addWidget", 0),
)

TAKE_SPECS = (
    ("QLayout", "takeAt", 0),
    ("QBoxLayout", "takeAt", 0),
)

DEFAULT_SUITES = (
    "tests/qt_layout_item_wrapper_test.py",
    "tests/qt_delivery_workspace_test.py",
    "tests/qt_client_window_test.py",
    "tests/qt_scan_progress_test.py",
    "tests/qt_activity_test.py",
    "tests/qt_delivery_ui_test.py",
)

_ACTIVE_PROBE: "Probe | None" = None

#: pytest 只在插件模式用到；脚本模式（探测负载）不要求装 pytest。
try:  # pragma: no cover - 环境相关
    from pytest import hookimpl as _hookimpl
except Exception:  # noqa: BLE001
    def _hookimpl(*args, **kwargs):
        def decorator(function):
            return function
        return decorator if not args or callable(args[0]) else decorator


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return str(path)


def _now() -> float:
    return time.monotonic()


def _limit(seq, cap):
    return list(seq)[:cap]


def _format_stack(skip_probe_frames: int = 2, max_frames: int = 40) -> str:
    """调用方的完整 Python 栈（去掉探测器自己的帧）。"""
    frame = sys._getframe(skip_probe_frames)
    frames = []
    while frame is not None and len(frames) < max_frames:
        frames.append(frame)
        frame = frame.f_back
    frames.reverse()
    parts = ["Traceback (most recent call last):\n"]
    for fr in frames:
        parts.append(
            f'  File "{_rel(fr.f_code.co_filename)}", line {fr.f_lineno}, '
            f"in {fr.f_code.co_name}\n")
    return "".join(parts)


def _safe_is_valid(shiboken, obj) -> bool:
    try:
        return bool(shiboken.isValid(obj))
    except Exception:  # noqa: BLE001 - 包装器失效时任何调用都可能抛
        return False


def _cpp_pointer(shiboken, obj) -> int:
    """包装器里登记的 C++ 指针（**只读指针，不解引用**）。

    ``Shiboken.getCppPointer`` 只返回 Shiboken 登记表里的地址；``Shiboken.dump``
    也只读 Python 侧登记信息。两者都不会碰 C++ 对象本身，所以对一个已被 Qt 删除的
    条目也是安全的。**绝不能**用 ``item.widget()`` 之类的方法去"验证"失效包装器：
    那会解引用悬空指针，直接把进程打死（2026-11-09 的三份 Python-*.ips 就是这么来的，
    栈顶 ``Sbk_QWidgetItemFunc_widget``）。
    """
    try:
        pointer = shiboken.getCppPointer(obj)
    except Exception:  # noqa: BLE001
        return 0
    if not pointer:
        return 0
    try:
        return int(pointer[0])
    except (TypeError, IndexError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 包装器统计
# ---------------------------------------------------------------------------


class WrapperRegistry:
    """统计常见受害类型的 Python 包装器数量 / 有效性。"""

    def __init__(self) -> None:
        import PySide6.QtWidgets as qtwidgets
        import shiboken6

        self._qtwidgets = qtwidgets
        self._shiboken = shiboken6
        self.types: dict[str, type] = {}
        for name in VICTIM_TYPES:
            cls = getattr(qtwidgets, name, None)
            if cls is not None:
                self.types[name] = cls

    @property
    def shiboken(self):
        return self._shiboken

    def census(self, *, collect: bool = True, keep: tuple[str, ...] = ()) -> dict:
        """全量快照。``keep`` 里的类型同时保留包装器对象（用于身份差集）。"""
        if collect:
            gc.collect()
        objects = gc.get_objects()
        counts: dict[str, dict[str, int]] = {}
        kept: dict[str, list] = {}
        for name, cls in self.types.items():
            total = valid = stale = 0
            items = []
            for obj in objects:
                if type(obj) is not cls:
                    continue
                total += 1
                if _safe_is_valid(self._shiboken, obj):
                    valid += 1
                else:
                    stale += 1
                if name in keep:
                    items.append(obj)
            counts[name] = {"total": total, "valid": valid, "stale": stale}
            if name in keep:
                kept[name] = items
        return {"counts": counts, "objects": kept}

    def delta(self, before: dict, after: dict) -> dict:
        out: dict[str, dict[str, int]] = {}
        for name in self.types:
            b = before["counts"].get(name, {})
            a = after["counts"].get(name, {})
            out[name] = {key: a.get(key, 0) - b.get(key, 0)
                         for key in ("total", "valid", "stale")}
        return out


# ---------------------------------------------------------------------------
# 探测器
# ---------------------------------------------------------------------------


class Probe:
    """挂接布局 API、统计包装器、记录触发点。"""

    def __init__(self, *, scenario: str = "", measure_limit_per_site: int = 24,
                 stack_limit_per_site: int = 2, example_limit_per_site: int = 3,
                 max_sites: int = 400, verbose: bool = False) -> None:
        self.registry = WrapperRegistry()
        self.scenario = scenario
        self.measure_limit_per_site = measure_limit_per_site
        self.stack_limit_per_site = stack_limit_per_site
        self.example_limit_per_site = example_limit_per_site
        self.max_sites = max_sites
        self.verbose = verbose
        self.sites: dict[str, dict] = {}
        self.events: list[dict] = []
        self.checkpoints: list[dict] = []
        self._patches: list[tuple[type, str, object]] = []
        self._busy = False
        self._current_checkpoint = ""
        self._detached: dict[int, dict] = {}   # id(widget) -> {"weak": ref, "from": site}
        self._installed = False
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.moves = 0
        self.attach_calls = 0
        self._last_census: dict | None = None
        self._json_path = ""
        #: 驱动层附加信息（场景耗时、过期包装器审计、真实路径结果等）
        self.extra: dict = {}
        #: 场景内审计结果（label -> audit dict）
        self.audits: dict[str, dict] = {}
        #: 已消失/已失效的包装器指针（用于检测「同地址被新对象复用」）
        self._retired_pointers: dict[int, dict] = {}
        #: 最近一次 checkpoint 结束时的包装器指针集合
        self._checkpoint_pointers: set[int] = set()
        #: 上一次审计时的「活布局条目指针」集合
        self._audit_baseline: set[int] = set()
        #: 疑似站点创建的包装器指针 -> 站点（审计时检查是否已变成过期包装器）
        self.created_pointers: dict[int, str] = {}

    # -- 安装/卸载 ----------------------------------------------------------
    def install(self) -> "Probe":
        global _ACTIVE_PROBE
        if self._installed:
            return self
        for cls_name, method_name, arg_index, kind in ATTACH_SPECS:
            cls = getattr(self.registry._qtwidgets, cls_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            self._patch(cls, method_name,
                        self._make_attach_hook(cls_name, method_name, arg_index, kind))
        for cls_name, method_name, arg_index in MOVE_SPECS:
            cls = getattr(self.registry._qtwidgets, cls_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            self._patch(cls, method_name,
                        self._make_move_hook(cls_name, method_name, arg_index))
        for cls_name, method_name, arg_index in TAKE_SPECS:
            cls = getattr(self.registry._qtwidgets, cls_name, None)
            if cls is None or not hasattr(cls, method_name):
                continue
            self._patch(cls, method_name,
                        self._make_take_hook(cls_name, method_name))
        self._installed = True
        _ACTIVE_PROBE = self
        return self

    def uninstall(self) -> None:
        global _ACTIVE_PROBE
        for cls, name, orig in self._patches:
            try:
                setattr(cls, name, orig)
            except Exception:  # noqa: BLE001
                pass
        self._patches.clear()
        self._installed = False
        if _ACTIVE_PROBE is self:
            _ACTIVE_PROBE = None

    def _patch(self, cls, name, hook) -> None:
        orig = getattr(cls, name)
        setattr(cls, name, hook)
        self._patches.append((cls, name, orig))

    # -- 站点记账 ----------------------------------------------------------
    @staticmethod
    def _callsite(depth: int = 2) -> tuple[str, int, str]:
        frame = sys._getframe(depth)
        while frame is not None and Path(frame.f_code.co_filename).resolve() == Path(__file__).resolve():
            frame = frame.f_back
        if frame is None:
            return ("<unknown>", 0, "<unknown>")
        return (_rel(frame.f_code.co_filename), frame.f_lineno, frame.f_code.co_name)

    def _site(self, api: str, depth: int = 3) -> dict:
        file, line, function = self._callsite(depth)
        key = f"{file}:{line}:{function}:{api}"
        record = self.sites.get(key)
        if record is None:
            if len(self.sites) >= self.max_sites:
                key = "<overflow>"
                record = self.sites.get(key)
                if record is None:
                    record = {
                        "key": key, "site": key, "file": "<overflow>", "line": 0,
                        "function": "", "api": api, "calls": 0, "suspicious": 0,
                        "measured": 0, "wrappers_created": 0, "max_delta": 0,
                        "victim_deltas": {}, "child_counts": [], "scenarios": [],
                        "stacks": [], "examples": [], "moves": 0,
                        "wrappers_invalidated": 0, "product": False,
                    }
                    self.sites[key] = record
            else:
                record = {
                    "key": key,
                    "site": f"{file}:{line} in {function}",
                    "file": file, "line": line, "function": function,
                    "api": api, "calls": 0, "suspicious": 0, "measured": 0,
                    "wrappers_created": 0, "max_delta": 0,
                    "victim_deltas": {}, "child_counts": [], "scenarios": [],
                    "stacks": [], "examples": [], "moves": 0,
                    "wrappers_invalidated": 0,
                    "product": Path(file).parts[:1] == ("folith",),
                }
                self.sites[key] = record
        if self.scenario and self.scenario not in record["scenarios"]:
            record["scenarios"].append(self.scenario)
        return record

    def set_scenario(self, name: str) -> None:
        self.scenario = name

    # -- 判定 --------------------------------------------------------------
    def _layout_attached(self, layout) -> bool:
        """布局是否已经（直接或经由父布局）挂在某个控件上。"""
        try:
            if layout.parentWidget() is not None:
                return True
        except (RuntimeError, AttributeError):
            return False
        # 父布局链的根如果挂在控件上，子布局的 parentWidget() 也已经非空；
        # 这里再确认一次 parent() 只是为了把「挂在别的布局里」也当作已 attach。
        try:
            parent = layout.parent()
        except (RuntimeError, AttributeError):
            parent = None
        if parent is None:
            return False
        try:
            return parent.parentWidget() is not None
        except (RuntimeError, AttributeError):
            return False

    @staticmethod
    def _layout_count(layout) -> int:
        try:
            return int(layout.count())
        except (RuntimeError, AttributeError):
            return 0

    def _widget_attached(self, widget) -> bool:
        try:
            return widget.parentWidget() is not None or widget.isVisible()
        except (RuntimeError, AttributeError):
            return False

    # -- attach hook -------------------------------------------------------
    def _make_attach_hook(self, cls_name: str, method_name: str,
                          arg_index: int, kind: str):
        api = f"{cls_name}.{method_name}"
        probe = self

        def hook(self, *args, **kwargs):  # noqa: N802 - 替换 Qt 方法
            orig = probe._orig_for(cls_name, method_name)
            try:
                probe.attach_calls += 1
                target = args[arg_index] if len(args) > arg_index else kwargs.get(
                    _param_name(method_name, arg_index))
                return probe._dispatch_attach(
                    api, kind, self, target, lambda: orig(self, *args, **kwargs))
            except Exception:  # noqa: BLE001 - 探测器出错绝不能影响产品
                if probe.verbose:
                    traceback.print_exc()
                return orig(self, *args, **kwargs)

        return hook

    def _orig_for(self, cls_name: str, method_name: str):
        # 首次调用时从保存的补丁表里取原始函数；表在 install() 里建好。
        for cls, name, orig in self._patches:
            if name == method_name and cls.__name__ == cls_name:
                return orig
        for cls, name, orig in self._patches:
            if name == method_name and issubclass(getattr(self.registry._qtwidgets,
                                                          cls_name), cls):
                return orig
        raise RuntimeError(f"no original for {cls_name}.{method_name}")

    def _dispatch_attach(self, api: str, kind: str, receiver, target, invoke):
        if target is None:
            return invoke()
        site = self._site(api, depth=4)
        site["calls"] += 1
        if kind == "attach_item":
            layout_cls = self.registry._qtwidgets.QLayout
            if not isinstance(target, layout_cls):
                return invoke()
            child = target
            suspicious = self._layout_attached(receiver) and self._layout_count(child) > 0
        elif kind == "widget_set_layout":
            child = target
            suspicious = (self._layout_count(child) > 0
                          and self._widget_attached(receiver))
        else:
            child = target
            suspicious = (self._layout_attached(receiver)
                          and self._layout_count(child) > 0)
        if not suspicious:
            return invoke()
        site["suspicious"] += 1
        child_count = self._layout_count(child)
        if len(site["child_counts"]) < 24:
            site["child_counts"].append(child_count)
        if len(site["stacks"]) < self.stack_limit_per_site:
            site["stacks"].append(_format_stack(skip_probe_frames=3))
        if self._busy or site["measured"] >= self.measure_limit_per_site:
            return invoke()
        self._busy = True
        try:
            before = self.registry.census(collect=True, keep=("QWidgetItem",))
            result = invoke()
            after = self.registry.census(collect=True, keep=("QWidgetItem",))
            self._accumulate(site, api, kind, child_count, before, after)
            return result
        finally:
            self._busy = False

    def _accumulate(self, site: dict, api: str, kind: str, child_count: int,
                    before: dict, after: dict) -> None:
        site["measured"] += 1
        delta = self.registry.delta(before, after)
        created = max(0, delta.get("QWidgetItem", {}).get("total", 0))
        site["wrappers_created"] += created
        if created > site["max_delta"]:
            site["max_delta"] = created
        for name, change in delta.items():
            positive = max(0, change.get("total", 0))
            if positive:
                site["victim_deltas"][name] = site["victim_deltas"].get(name, 0) + positive
        before_ids = {id(obj) for obj in before["objects"].get("QWidgetItem", [])}
        new_items = [obj for obj in after["objects"].get("QWidgetItem", [])
                     if id(obj) not in before_ids]
        for obj in new_items:
            pointer = _cpp_pointer(self.registry.shiboken, obj)
            if pointer:
                self.created_pointers[pointer] = site["site"]
        example = {
            "api": api,
            "kind": kind,
            "child_count": child_count,
            "before": before["counts"].get("QWidgetItem", {}),
            "after": after["counts"].get("QWidgetItem", {}),
            "delta": delta,
            "created": len(new_items),
            "at": round(time.monotonic() - self.started_monotonic, 3),
            "checkpoint": self._current_checkpoint,
        }
        if new_items and self._busy:
            details = []
            for obj in _limit(new_items, 4):
                info = {"valid": _safe_is_valid(self.registry.shiboken, obj),
                        "cpp_pointer": hex(_cpp_pointer(self.registry.shiboken, obj))}
                try:
                    holders = []
                    for ref in gc.get_referrers(obj):
                        module = type(ref).__module__ or ""
                        if module.startswith("PySide6"):
                            holders.append(type(ref).__name__)
                    info["holders"] = sorted(set(holders))
                except Exception:  # noqa: BLE001
                    info["holders"] = []
                details.append(info)
            example["new_wrappers"] = details
        if len(site["examples"]) < self.example_limit_per_site:
            site["examples"].append(example)
        self.events.append({
            "site": site["site"], "api": api, "kind": kind,
            "created": len(new_items), "child_count": child_count,
            "scenario": self.scenario, "checkpoint": self._current_checkpoint,
            "at": round(time.monotonic() - self.started_monotonic, 3),
        })
        if self.verbose:
            print(f"[probe] {site['site']} {api} child_items={child_count} "
                  f"new QWidgetItem wrappers={len(new_items)}"
                  + (f" -> {site['examples'][-1].get('new_wrappers')}"
                     if site["examples"][-1].get("new_wrappers") else ""),
                  flush=True)

    # -- widget move 检测 --------------------------------------------------
    def _make_move_hook(self, cls_name: str, method_name: str, arg_index: int):
        api = f"{cls_name}.{method_name}"
        probe = self

        def hook(self, *args, **kwargs):
            orig = probe._orig_for(cls_name, method_name)
            try:
                widget = args[arg_index] if len(args) > arg_index else None
                if widget is None:
                    return orig(self, *args, **kwargs)
                key = id(widget)
                previous = probe._detached.pop(key, None)
                if previous is None:
                    previous = probe._widget_layouts.pop(key, None)
                if previous is not None and probe._layout_attached(self):
                    probe.moves += 1
                    site = probe._site(api, depth=3)
                    site["moves"] += 1
                    site["calls"] += 1
                    if len(site["examples"]) < probe.example_limit_per_site:
                        site["examples"].append({
                            "api": api, "kind": "widget_move",
                            "from_layout": previous.get("layout"),
                            "child_count": probe._layout_count(self),
                            "checkpoint": probe._current_checkpoint,
                            "at": round(time.monotonic() - probe.started_monotonic, 3),
                        })
                    if probe.verbose:
                        print(f"[probe] widget move {site['site']} {api} "
                              f"from={previous.get('layout')}", flush=True)
                result = orig(self, *args, **kwargs)
                probe._widget_layouts[id(widget)] = {
                    "layout": f"{cls_name}.{method_name}",
                    "weak": _weakref(widget),
                }
                return result
            except Exception:  # noqa: BLE001
                if probe.verbose:
                    traceback.print_exc()
                return orig(self, *args, **kwargs)

        return hook

    def _make_take_hook(self, cls_name: str, method_name: str):
        """只记录 ``takeAt`` 调用点与返回条目的类型/指针，**不解引用**返回的条目。

        ``takeAt`` 把条目的所有权交给调用方；返回的 ``QLayoutItem`` 包装器如果被
        Python 长期持有，C++ 条目被删后就是典型的过期包装器。这里绝不去取
        ``item.widget()``（那会解引用可能已失效的条目），只留证据。
        """
        api = f"{cls_name}.{method_name}"
        probe = self

        def hook(self, *args, **kwargs):
            orig = probe._orig_for(cls_name, method_name)
            item = orig(self, *args, **kwargs)
            try:
                site = probe._site(api, depth=3)
                site["calls"] += 1
                if item is not None and len(site["examples"]) < probe.example_limit_per_site:
                    site["examples"].append({
                        "api": api, "kind": "take_item",
                        "item_type": type(item).__name__,
                        "cpp_pointer": hex(_cpp_pointer(probe.registry.shiboken, item)),
                        "checkpoint": probe._current_checkpoint,
                        "at": round(time.monotonic() - probe.started_monotonic, 3),
                    })
            except Exception:  # noqa: BLE001
                if probe.verbose:
                    traceback.print_exc()
            return item

        return hook

    def _get_widget_layouts(self) -> dict:
        if not hasattr(self, "_widget_layouts"):
            self._widget_layouts = {}
        return self._widget_layouts

    def __getattr__(self, name):
        # 只为 _widget_layouts 这种可选状态兜底，其它属性照常报错。
        if name == "_widget_layouts":
            object.__setattr__(self, name, {})
            return object.__getattribute__(self, name)
        raise AttributeError(name)

    # -- checkpoint --------------------------------------------------------
    @contextmanager
    def checkpoint(self, name: str, *, scan: bool = True):
        """包住一段操作，统计前后的包装器数量，输出增长归因。"""
        previous = self._current_checkpoint
        self._current_checkpoint = name
        before = None
        if scan:
            before = self.registry.census(collect=True, keep=("QWidgetItem",))
        started = _now()
        try:
            yield self
        finally:
            elapsed = _now() - started
            if before is not None:
                after = self.registry.census(collect=True, keep=("QWidgetItem",))
                delta = self.registry.delta(before, after)
                before_ids = {id(obj) for obj in before["objects"].get("QWidgetItem", [])}
                created = [obj for obj in after["objects"].get("QWidgetItem", [])
                           if id(obj) not in before_ids]
                continuity = self._pointer_continuity(name, before, after)
                record = {
                    "name": name,
                    "scenario": self.scenario,
                    "seconds": round(elapsed, 3),
                    "before": before["counts"],
                    "after": after["counts"],
                    "delta": delta,
                    "new_widget_items": len(created),
                    "events_in_window": sum(1 for event in self.events
                                            if event.get("checkpoint") == name),
                    "pointer_continuity": continuity,
                }
                if created:
                    record["sample"] = _sample_wrappers(self.registry.shiboken, created)
                self.checkpoints.append(record)
                self._last_census = after
                self._checkpoint_pointers = {
                    _cpp_pointer(self.registry.shiboken, obj)
                    for obj in after["objects"].get("QWidgetItem", [])
                    if _cpp_pointer(self.registry.shiboken, obj)}
                if self.verbose:
                    growth = {k: v["total"] for k, v in delta.items() if v["total"]}
                    print(f"[probe] checkpoint {name}: {elapsed:.2f}s "
                          f"new QWidgetItem wrappers={len(created)} growth={growth}",
                          flush=True)
                if self._json_path:
                    self.dump(self._json_path)
            self._current_checkpoint = previous

    def _pointer_continuity(self, name: str, before: dict, after: dict) -> dict:
        """包装器 C++ 指针的前后连续性：消失 / 失效 / **同地址被新对象复用**。

        复用检测不需要解引用：只比较 ``getCppPointer`` 记录的地址与包装器身份。
        若一个地址在旧检查点对应的包装器已消失或失效，而这里又出现一个新包装器
        占用同一地址，就是「地址被回收再用」的直接证据。
        """
        shiboken = self.registry.shiboken
        before_items = (before.get("objects") or {}).get("QWidgetItem") or []
        after_items = (after.get("objects") or {}).get("QWidgetItem") or []
        before_map = {}
        for obj in before_items:
            pointer = _cpp_pointer(shiboken, obj)
            if pointer:
                before_map[pointer] = obj
        after_map = {}
        for obj in after_items:
            pointer = _cpp_pointer(shiboken, obj)
            if pointer:
                after_map[pointer] = obj
        created = sorted(set(after_map) - set(before_map))
        vanished = sorted(set(before_map) - set(after_map))
        invalidated = []
        for pointer in sorted(set(before_map) & set(after_map)):
            old = before_map[pointer]
            new = after_map[pointer]
            if id(old) == id(new) and _safe_is_valid(shiboken, old) \
                    and not _safe_is_valid(shiboken, new):
                invalidated.append(pointer)
        reused = [pointer for pointer in created
                  if pointer in self._retired_pointers]
        for pointer in vanished:
            old = before_map[pointer]
            self._retired_pointers[pointer] = {
                "valid": _safe_is_valid(shiboken, old),
                "checkpoint": name, "scenario": self.scenario}
        for pointer in invalidated:
            old = before_map[pointer]
            self._retired_pointers[pointer] = {
                "valid": _safe_is_valid(shiboken, old),
                "checkpoint": name, "scenario": self.scenario}
        return {
            "created_pointers": [hex(pointer) for pointer in created[:8]],
            "vanished": len(vanished),
            "invalidated": len(invalidated),
            "address_reuse": [
                {"cpp_pointer": hex(pointer),
                 "prior_valid": self._retired_pointers[pointer].get("valid"),
                 "prior_checkpoint": self._retired_pointers[pointer].get("checkpoint")}
                for pointer in reused[:8]],
        }

    def audit_now(self, label: str) -> dict:
        """在场景内部（对象还活着时）做一次过期包装器审计。

        会与「上一次观察到的活条目指针集合」（上一次审计，否则最近一次
        checkpoint）比较，报出「当时还活着、现在不在任何布局里、包装器却还在」
        的条目——这正是过期包装器的定义。
        """
        previous = self._audit_baseline or self._checkpoint_pointers
        result = audit_stale_wrappers(self, previous_live=set(previous))
        self._audit_baseline = set(result.pop("_live_set", set()))
        result["label"] = label
        result["scenario"] = self.scenario
        self.audits[label] = result
        if self.verbose:
            print(f"[probe] audit {label}: stale={result.get('stale_wrappers')} "
                  f"invalid={result.get('invalid_wrappers')} "
                  f"walked={result.get('layout_wrappers_walked')}", flush=True)
        return result

    # -- 输出 --------------------------------------------------------------
    def summary(self) -> dict:
        suspicious_sites = [site for site in self.sites.values()
                            if site["suspicious"] and site["wrappers_created"] > 0]
        attach_only = [site for site in self.sites.values()
                       if site["suspicious"] and site["wrappers_created"] == 0]
        suspicious_sites.sort(key=lambda s: (-s["wrappers_created"], s["site"]))
        attach_only.sort(key=lambda s: (-s["suspicious"], s["site"]))
        return {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "scenario": self.scenario,
            "python": sys.version.split()[0],
            "pyside6": _pyside_version(),
            "platform": f"{sys.platform}/{platform.machine()}",
            "git_head": _git_head(),
            "environment": {
                "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", ""),
                "MallocScribble": os.environ.get("MallocScribble", ""),
                "MallocGuardEdges": os.environ.get("MallocGuardEdges", ""),
            },
            "counts": {
                "attach_api_calls": self.attach_calls,
                "sites_seen": len(self.sites),
                "suspicious_sites": len([s for s in self.sites.values()
                                         if s["suspicious"]]),
                "sites_with_wrapper_growth": len(suspicious_sites),
                "widget_moves": self.moves,
                "events": len(self.events),
            },
            "sites": sorted(self.sites.values(),
                            key=lambda s: (-s["wrappers_created"], -s["suspicious"],
                                           s["site"])),
            "leaking_sites": suspicious_sites,
            "attach_without_growth": attach_only,
            "checkpoints": self.checkpoints,
            "events": self.events[-500:],
            "audits": self.audits,
            "driver": self.extra,
        }

    def dump(self, path: str) -> None:
        self._json_path = path
        data = self.summary()
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                      default=str), encoding="utf-8")
            tmp.replace(target)
        except Exception:  # noqa: BLE001 - 进程将死时也不该把产品拖下去
            print(f"[probe] JSON dump failed for {path}", file=sys.stderr)
            traceback.print_exc()


def _param_name(method_name: str, index: int) -> str:
    names = {
        "addLayout": ("layout", "stretch"),
        "insertLayout": ("index", "layout", "stretch"),
        "setLayout": ("row", "role", "layout"),
        "addItem": ("item",),
        "insertItem": ("index", "item"),
        "addWidget": ("widget", "stretch", "alignment"),
        "insertWidget": ("index", "widget", "stretch", "alignment"),
    }
    params = names.get(method_name, ())
    return params[index] if index < len(params) else ""


def _weakref(obj):
    try:
        import weakref

        return weakref.ref(obj)
    except TypeError:
        return None


def _sample_wrappers(shiboken, wrappers: list) -> list[dict]:
    sample = []
    for obj in _limit(wrappers, 5):
        info = {"valid": _safe_is_valid(shiboken, obj),
                "cpp_pointer": hex(_cpp_pointer(shiboken, obj))}
        holders = set()
        try:
            for ref in gc.get_referrers(obj):
                module = type(ref).__module__ or ""
                if module.startswith("PySide6"):
                    holders.add(type(ref).__name__)
        except Exception:  # noqa: BLE001
            pass
        info["holders"] = sorted(holders)
        sample.append(info)
    return sample


def _pyside_version() -> str:
    try:
        import PySide6

        return PySide6.__version__
    except Exception:  # noqa: BLE001
        return ""


def _git_head() -> str:
    try:
        run = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return run.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Qt 环境
# ---------------------------------------------------------------------------


def ensure_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def flush_events(app, rounds: int = 1) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    for _ in range(max(1, rounds)):
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def pump(app, seconds: float, interval: float = 0.01) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(min(interval, 0.005))
    app.processEvents()


# ---------------------------------------------------------------------------
# 负载 1：完整 MainWindow（应用启动路径）
# ---------------------------------------------------------------------------


def _delivery_manifest(names: list[str]) -> dict:
    return {"created_at": "2026-11-07T08:00:00+00:00", "snapshot_version": 1,
            "assets": [{"name": name, "size": 100 + index, "sha256": "0" * 64}
                       for index, name in enumerate(names)]}


def scenario_window(probe: Probe, workdir: Path, args) -> None:
    """像 tests/qt_client_window_test.py 的 ``client`` fixture 那样构建真实 MainWindow。"""
    from PySide6.QtWidgets import QApplication  # noqa: F401

    app = ensure_app()
    data_dir = workdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env_backup = dict(os.environ)
    try:
        os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
        os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(workdir / "logs")
        os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)
        from folith.desktop import paths as desktop_paths
        from folith.qt import adapter, i18n as i18n_module, preferences, style
        from folith.qt import window as window_module

        os.environ[preferences.SETTINGS_DIR_ENV] = str(workdir / "settings")
        i18n_module.set_language("zh-CN")
        style.set_current_theme("light")
        paths = desktop_paths.resolve_paths()
        config = adapter.EngineConfig(
            provider="自定义中转站", model="stub-model", api_key="stub-key",
            base_url="", target_lang="简体中文", enable_review=True,
            enable_report=False, enable_understanding=False,
            enable_source_cleanup=False)
        repeat = max(1, args.repeat)
        for index in range(repeat):
            with probe.checkpoint(f"window.construct[{index}]"):
                window = window_module.MainWindow(config, paths, None)
            with probe.checkpoint(f"window.show[{index}]"):
                window.show()
                flush_events(app, 2)
            for page in ("home", "workbench", "projects"):
                with probe.checkpoint(f"window.page.{page}[{index}]"):
                    window.stack.setCurrentWidget(getattr(window, page))
                    flush_events(app, 2)
            with probe.checkpoint(f"window.resize[{index}]"):
                for width, height in ((900, 700), (1400, 900), (1180, 800)):
                    window.resize(width, height)
                    flush_events(app)
            with probe.checkpoint(f"window.delivery_refresh[{index}]"):
                # 交付页的真实刷新入口：窗口里的 DeliveryWorkspace 反复重建分组卡片。
                workbench = window.workbench
                manifests = (
                    ["translation.docx", "bilingual.docx", "terms.xlsx",
                     "delivery_manifest.json"],
                    ["translation.docx", "terms.xlsx", "delivery_manifest.json"],
                    ["translation.docx", "bilingual.docx", "delivery_manifest.json"])
                for round_index in range(3):
                    workbench._fill_delivery_assets(
                        _delivery_manifest(manifests[round_index]))
                    flush_events(app)
            with probe.checkpoint(f"window.theme[{index}]"):
                style.apply_theme(app, "dark")
                flush_events(app, 2)
                style.apply_theme(app, "light")
                flush_events(app, 2)
            probe.audit_now(f"window.before_close[{index}]")
            with probe.checkpoint(f"window.close[{index}]"):
                window.timer.stop()
                window._closing_accepted = True
                window.close()
                app.removeEventFilter(window)
                window.deleteLater()
                flush_events(app, 3)
                del window
                gc.collect()
    finally:
        os.environ.clear()
        os.environ.update(env_backup)


# ---------------------------------------------------------------------------
# 负载 2：DeliveryWorkspace 反复 set_model（冻结交付刷新路径）
# ---------------------------------------------------------------------------


def scenario_delivery(probe: Probe, workdir: Path, args) -> None:
    from folith.qt import delivery_panel

    app = ensure_app()
    with probe.checkpoint("delivery.construct"):
        workspace = delivery_panel.DeliveryWorkspace()
        workspace.resize(1040, 900)
        workspace.show()
        flush_events(app, 2)
    manifests = (
        ["translation.docx", "bilingual.docx", "terms.xlsx", "delivery_manifest.json"],
        ["translation.docx", "terms.xlsx", "delivery_manifest.json"],
        ["translation.docx", "bilingual.docx", "delivery_manifest.json"])
    repeat = max(1, args.repeat)
    with probe.checkpoint("delivery.set_model_loop"):
        for index in range(repeat):
            workspace.set_model(delivery_panel.build_model(
                _delivery_manifest(manifests[index % len(manifests)])))
            flush_events(app)
            gc.collect()
    with probe.checkpoint("delivery.set_gate_loop"):
        for index in range(min(repeat, 24)):
            workspace.set_gate(
                frozen=bool(index % 2), diverged=bool(index % 3 == 0),
                status="done", tone="done", detail="", deliver_label="已冻结交付 v1",
                can_deliver=True, show_fix=False, fix_label="", show_risk=False,
                note_enabled=True)
            flush_events(app)
    probe.audit_now("delivery.before_teardown")
    with probe.checkpoint("delivery.teardown"):
        workspace.hide()
        workspace.deleteLater()
        flush_events(app, 3)


# ---------------------------------------------------------------------------
# 负载 3：PdfPanel 构建 + 反复 resize（_place_tools 往返）
# ---------------------------------------------------------------------------


def scenario_pdf(probe: Probe, workdir: Path, args) -> None:
    from folith.qt.pdf_panel import PdfPanel

    app = ensure_app()
    with probe.checkpoint("pdf.construct"):
        panel = PdfPanel()
        panel.resize(280, 700)
        panel.show()
        flush_events(app, 2)
    repeat = max(1, args.repeat)
    with probe.checkpoint("pdf.resize_loop"):
        for _ in range(repeat):
            for width in (280, 420, 1200, 280, 900, 1600, 240):
                panel.resize(width, 700)
                flush_events(app)
                gc.collect()
    probe.audit_now("pdf.before_teardown")
    with probe.checkpoint("pdf.teardown"):
        panel.hide()
        panel.deleteLater()
        flush_events(app, 3)


# ---------------------------------------------------------------------------
# 负载 4：ReviewPanel 构建 + 反复 apply（审校页重建）
# ---------------------------------------------------------------------------


def _review_states():
    fixture_dir = ROOT / "docs" / "ui-audit" / "qa-decision-studio-v3.1"
    if fixture_dir.is_dir():
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_qa_visual_fixtures", fixture_dir / "fixtures.py")
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            old_path = list(sys.path)
            sys.path.insert(0, str(fixture_dir))
            try:
                spec.loader.exec_module(module)
                return module.scenarios(), module.projection
            except Exception:  # noqa: BLE001 - 夹具不可用时退回最小视图
                pass
            finally:
                sys.path[:] = old_path
    return None, None


def _minimal_review_view() -> dict:
    return {
        "queue_items": [{
            "item_id": "probe-finding-1", "kind": "finding",
            "source": "源句", "target": "译文", "summary": "译文为空",
            "category": "translation_completion", "severity": "warning",
            "segment_index": 0,
        }],
        "handled_items": [],
        "finding_groups": [],
        "readiness": {},
        "entities": [],
    }


def scenario_review(probe: Probe, workdir: Path, args) -> None:
    from folith.qt import i18n
    from folith.qt.review_panel import ReviewPanel

    app = ensure_app()
    i18n.set_language("zh-CN")
    states, projection = _review_states()
    with probe.checkpoint("review.construct"):
        panel = ReviewPanel()
        panel.resize(1280, 900)
        panel.show()
        flush_events(app, 2)
    repeat = max(1, args.repeat)
    with probe.checkpoint("review.apply_loop"):
        for _ in range(repeat):
            if states is not None:
                for key in ("empty_target", "short", "term", "audit", "safe",
                            "empty_queue"):
                    try:
                        panel.apply(projection(states[key]))
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()
                    flush_events(app)
            else:
                panel.apply(_minimal_review_view())
                flush_events(app)
            gc.collect()
    probe.audit_now("review.before_teardown")
    with probe.checkpoint("review.teardown"):
        panel.hide()
        panel.deleteLater()
        flush_events(app, 3)


# ---------------------------------------------------------------------------
# 负载 5：扫描进度占位刷新（qt_scan_progress_test 的调用方式）
# ---------------------------------------------------------------------------


def scenario_scan(probe: Probe, workdir: Path, args) -> None:
    from folith.qt import i18n, views

    app = ensure_app()
    i18n.set_language("zh-CN")
    with probe.checkpoint("scan.construct"):
        bench = views.WorkbenchView()
        bench.resize(1200, 800)
        bench.show()
        flush_events(app, 2)
    bench._state = {}
    bench._all_rows = []
    bench._setup_view = None
    bench._last_runtime = {}
    repeat = max(1, args.repeat)
    with probe.checkpoint("scan.placeholder_loop"):
        for index in range(repeat * 8):
            done = index % 80
            bench._last_runtime = {
                "status": "running",
                "stage_progress": {"completed": done, "total": 80, "unit": "页"}}
            bench._state = {}
            bench._all_rows = []
            bench._sync_segment_placeholder()
            flush_events(app)
            # 空状态在「有段落 / 筛选为空 / 被排除」之间来回切。
            bench._all_rows = [object()] if index % 3 == 1 else []
            bench._state = ({"paras": ["x"]} if index % 5 == 2 else
                            {"excluded_segments": [{"source": "x"}]} if index % 5 == 3
                            else {})
            bench._sync_segment_placeholder()
            flush_events(app)
            gc.collect()
    probe.audit_now("scan.before_teardown")
    with probe.checkpoint("scan.teardown"):
        bench.hide()
        bench.deleteLater()
        flush_events(app, 3)


# ---------------------------------------------------------------------------
# 负载 6：通知 / 活动刷新（guide 里抓到的 Python 栈那条路径）
# ---------------------------------------------------------------------------


def _write_runtime_events(core, job_id: str, events: list[dict]) -> None:
    path = core.runtime_events_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False)
                              for event in events) + "\n", encoding="utf-8")


def _event(name: str, message: str = "", at: str = "2026-10-10T08:00:00+00:00") -> dict:
    return {"event": name, "message": message, "timestamp": at,
            "visibility": "user"}


def scenario_notice(probe: Probe, workdir: Path, args) -> None:
    """运行记录 + ``set_task_notice``：guide 里抓到的
    ``activity_panel.set_rows ← _render_activity ← set_task_notice`` 路径。"""
    import core
    from folith.qt import adapter, i18n, views

    app = ensure_app()
    i18n.set_language("zh-CN")
    saved_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = workdir / "outputs"
    try:
        job_id = "probjob000000001"
        with probe.checkpoint("notice.seed_job"):
            core.save_job_state(job_id, core.new_job_state("probe.docx"))
            _write_runtime_events(core, job_id, [_event("llm_request_started")])
        with probe.checkpoint("notice.construct"):
            bench = views.WorkbenchView()
            bench.resize(1200, 800)
            bench.show()
            flush_events(app, 2)
            state = adapter.load_state(job_id)
            bench.load(job_id, adapter.EngineConfig(target_lang="简体中文"), state,
                       adapter.segment_rows(job_id, state), [])
            flush_events(app)
        repeat = max(1, args.repeat)
        events = [_event("llm_request_started")]
        with probe.checkpoint("notice.apply_loop"):
            for index in range(repeat * 4):
                events.append(_event("llm_response_received",
                                     f"第 {index} 段"))
                _write_runtime_events(core, job_id, events)
                bench.apply(job_id, state, adapter.segment_rows(job_id, state),
                            {}, [])
                flush_events(app)
                gc.collect()
        with probe.checkpoint("notice.set_task_notice_loop"):
            saved_seconds = views.WorkbenchView.TASK_NOTICE_SECONDS
            views.WorkbenchView.TASK_NOTICE_SECONDS = {False: 0.05, True: 0.05}
            try:
                for index in range(min(repeat * 2, 40)):
                    bench.set_task_notice(f"已提交人工修改 {index}",
                                          error=bool(index % 5 == 0))
                    pump(app, 0.08)
            finally:
                views.WorkbenchView.TASK_NOTICE_SECONDS = saved_seconds
        with probe.checkpoint("notice.open_activity"):
            bench.open_activity()
            flush_events(app, 2)
            bench.activity_popover.close()
            flush_events(app, 2)
        probe.audit_now("notice.before_teardown")
        with probe.checkpoint("notice.teardown"):
            bench.hide()
            bench.deleteLater()
            flush_events(app, 3)
    finally:
        core.OUTPUT_DIR = saved_output


# ---------------------------------------------------------------------------


SCENARIOS = {
    "window": scenario_window,
    "delivery": scenario_delivery,
    "pdf": scenario_pdf,
    "review": scenario_review,
    "scan": scenario_scan,
    "notice": scenario_notice,
}


# ---------------------------------------------------------------------------
# 负载 7：现有测试套件（pytest 插件）
# ---------------------------------------------------------------------------


def _pytest_plugin_marker() -> bool:
    return bool(os.environ.get(ENV_JSON))


def pytest_configure(config):
    """以 ``pytest -p qt_stale_wrapper_probe`` 加载时启用。"""
    if not os.environ.get(ENV_JSON):
        return
    global _PLUGIN_PROBE
    _PLUGIN_PROBE = Probe(scenario="suite", measure_limit_per_site=6,
                          stack_limit_per_site=1, example_limit_per_site=2,
                          verbose=bool(os.environ.get("FOLITH_WRAPPER_PROBE_VERBOSE")))
    _PLUGIN_PROBE.install()
    _PLUGIN_PROBE.set_scenario("suite")
    config.addinivalue_line(
        "markers", "wrapper_probe_ignore: skip wrapper bookkeeping for this test")


_PLUGIN_PROBE: "Probe | None" = None
_PLUGIN_RESULTS: dict[str, dict] = {}


@_hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """包住 setup+call+teardown：用例前后的包装器增量按 nodeid 记账。

    必须是 ``wrapper=True``（旧式裸生成器在 pytest 8 里会被当成普通 hook 返回值，
    整个用例协议被吞掉：测试根本不会执行）。
    """
    probe = _PLUGIN_PROBE
    if probe is None:
        return (yield)
    if item.get_closest_marker("wrapper_probe_ignore"):
        return (yield)
    node = item.nodeid
    try:
        total_before = _total_wrappers(probe.registry)
    except Exception:  # noqa: BLE001
        return (yield)
    probe.set_scenario(node)
    result = yield
    try:
        total_after = _total_wrappers(probe.registry)
        counts = probe.registry.census(collect=True)["counts"]
        _PLUGIN_RESULTS[node] = {
            "total_before": total_before,
            "total_after": total_after,
            "delta": {name: after["total"]
                      - (total_before.get(name) or {}).get("total", 0)
                      for name, after in counts.items()},
            "after": counts,
        }
        if probe._json_path:
            probe.dump(probe._json_path)
    except Exception:  # noqa: BLE001
        pass
    return result


def pytest_sessionstart(session):
    probe = _PLUGIN_PROBE
    if probe is None:
        return
    probe._json_path = os.environ.get(ENV_JSON, "")
    if probe._json_path:
        probe.dump(probe._json_path)


def pytest_sessionfinish(session, exitstatus):
    probe = _PLUGIN_PROBE
    if probe is None:
        return
    data = probe.summary()
    data["tests"] = _PLUGIN_RESULTS
    data["pytest_exitstatus"] = int(exitstatus)
    try:
        if not os.environ.get("FOLITH_WRAPPER_PROBE_NO_AUDIT"):
            stale_audit = audit_stale_wrappers(probe)
            stale_audit.pop("_live_set", None)
            data["driver"] = {"stale_audit": stale_audit}
    except Exception:  # noqa: BLE001
        pass
    path = os.environ.get(ENV_JSON, "")
    if path:
        try:
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                  encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    probe.uninstall()


def pytest_unconfigure(config):
    global _PLUGIN_PROBE
    if _PLUGIN_PROBE is not None:
        _PLUGIN_PROBE.uninstall()
        _PLUGIN_PROBE = None


def _total_wrappers(registry: WrapperRegistry) -> dict[str, dict[str, int]]:
    return registry.census(collect=True)["counts"]


# ---------------------------------------------------------------------------
# 负载 8：真实路径（源码模式 selftest：启动→OCR 扫描→翻译→审校→冻结交付）
# ---------------------------------------------------------------------------


def _build_fixture(workdir: Path, kind: str = "pdf_scanned") -> Path:
    sys.path.insert(0, str(ROOT / "scripts"))
    import d0_fixtures

    manifest = d0_fixtures.build([kind], workdir / "fixtures")
    return Path(manifest["inputs"][kind]["path"])


class _StubServer:
    """``scripts/d0_fake_llm_server.py`` 的最小启动器（与验收脚本同一服务）。"""

    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.process: subprocess.Popen | None = None
        self.url = ""
        self._handle = None

    def start(self) -> str:
        ready = self.workdir / "stub-ready.json"
        ready.unlink(missing_ok=True)
        log = self.workdir / "stub-llm.jsonl"
        server_log = self.workdir / "stub-server.log"
        self._handle = server_log.open("ab")
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "d0_fake_llm_server.py"),
             "--delay", "0", "--port", "0", "--log", str(log),
             "--ready-file", str(ready)],
            stdout=self._handle, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if ready.exists():
                try:
                    payload = json.loads(ready.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    time.sleep(0.2)
                    continue
                self.url = str(payload.get("url") or "")
                if self.url:
                    return self.url
            time.sleep(0.2)
        raise RuntimeError("stub LLM server did not become ready")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        if self._handle is not None:
            self._handle.close()
            self._handle = None


_SITECUSTOMIZE = '''\
"""启动钩子：在真实应用进程里安装包装器探测器（由 qt_stale_wrapper_probe.py 生成）。"""
import os

if os.environ.get("{env_json}"):
    try:
        import qt_stale_wrapper_probe as _probe

        _probe.install_from_env()
    except Exception:
        pass
'''


def install_from_env():
    """sitecustomize 里调用：安装探测器并在进程退出时写 JSON。"""
    path = os.environ.get(ENV_JSON, "")
    if not path:
        return None
    probe = Probe(
        scenario="baseline",
        verbose=bool(os.environ.get("FOLITH_WRAPPER_PROBE_VERBOSE")),
        measure_limit_per_site=int(
            os.environ.get("FOLITH_WRAPPER_PROBE_MEASURE_LIMIT", "8")))
    probe.install()
    probe._json_path = path
    probe.dump(path)
    atexit.register(lambda: probe.dump(path))
    if os.environ.get(ENV_FLOW):
        _watch_selftest_flow(probe, path,
                             audit=not os.environ.get("FOLITH_WRAPPER_PROBE_NO_AUDIT"))
    return probe


def _watch_selftest_flow(probe: Probe, path: str, *, audit: bool = True) -> None:
    """sitecustomize 不能提前 import 产品包（会把 core.OUTPUT_DIR 钉死），
    所以轮询等待 ``folith.qt.selftest`` 被导入后再包 Flow.step，标出每个阶段。"""

    def run() -> None:
        while True:
            module = sys.modules.get("folith.qt.selftest")
            flow = getattr(module, "Flow", None) if module is not None else None
            if flow is not None:
                original = flow.step

                def step(self, name, **extra):
                    try:
                        with probe.checkpoint(f"baseline.step.{name}"):
                            result = original(self, name, **extra)
                        # 阶段边界上审计一次：对象还活着时，看「包装器在、条目没了」。
                        # 审计要遍历所有布局并 itemAt（会新建包装器），对真实流程有
                        # 扰动；崩溃归因不明确时用 --no-audit 关掉它再跑。
                        if audit:
                            probe.audit_now(f"baseline.step.{name}")
                        probe.dump(path)
                        return result
                    except Exception:  # noqa: BLE001
                        return original(self, name, **extra)

                flow.step = step
                probe.dump(path)
                return
            time.sleep(0.2)

    threading.Thread(target=run, name="wrapper-probe-flow", daemon=True).start()


def scenario_baseline(probe: Probe, workdir: Path, args) -> dict:
    """在真实源码应用里跑 selftest：启动 → OCR 扫描 → 通知 → 冻结交付。"""
    cases = [item for item in (args.baseline_cases or "eng").split(",") if item]
    ocr_language = {"eng": "eng", "chi": "chi_sim", "ara": "ara"}
    summary: dict = {"cases": []}
    fixture = _build_fixture(workdir, "pdf_scanned")
    stub = _StubServer(workdir)
    url = stub.start()
    hook_dir = workdir / "probehook"
    hook_dir.mkdir(parents=True, exist_ok=True)
    (hook_dir / "sitecustomize.py").write_text(
        _SITECUSTOMIZE.format(env_json=ENV_JSON), encoding="utf-8")
    try:
        for case in cases:
            language = ocr_language.get(case, case)
            data_dir = workdir / f"data-{case}"
            result_path = workdir / f"report-{case}.json"
            child_json = workdir / f"probe-{case}.json"
            env = dict(os.environ)
            env.update({
                "QT_QPA_PLATFORM": "offscreen",
                "FOLITH_QT_NONINTERACTIVE": "1",
                "FOLITH_EXIT_FORCE_PROMPT": "0",
                "FOLITH_QT_SELFTEST_BASE_URL": url,
                "FOLITH_QT_SELFTEST_OCR": "1",
                "FOLITH_QT_SELFTEST_OCR_LANGUAGE": language,
                "FOLITH_DESKTOP_DATA_DIR": str(data_dir),
                "FOLITH_DESKTOP_LOG_DIR": str(workdir / f"logs-{case}"),
                "FOLITH_OUTPUT_DIR": str(data_dir),
                "PYTHONPATH": os.pathsep.join(
                    [str(hook_dir), str(ROOT / "scripts"), str(ROOT),
                     env.get("PYTHONPATH", "")]),
                ENV_JSON: str(child_json),
                ENV_FLOW: "1",
            })
            env["FOLITH_WRAPPER_PROBE_MEASURE_LIMIT"] = str(
                min(args.measure_limit, 8))
            if args.no_audit:
                env["FOLITH_WRAPPER_PROBE_NO_AUDIT"] = "1"
            if args.baseline_malloc_scribble:
                env["MallocScribble"] = "1"
                env["MallocGuardEdges"] = "1"
            command = [sys.executable, "-X", "faulthandler", "-m", "folith.qt",
                       "--data-dir", str(data_dir), "--selftest",
                       "--selftest-source", str(fixture),
                       "--result", str(result_path)]
            started = time.monotonic()
            case_summary = {"case": case, "ocr_language": language}
            try:
                run = subprocess.run(command, env=env, cwd=str(workdir),
                                     capture_output=True, text=True,
                                     encoding="utf-8", errors="replace",
                                     timeout=args.baseline_timeout)
                case_summary["exit_code"] = run.returncode
                case_summary["stderr_tail"] = (run.stderr or "")[-4000:]
            except subprocess.TimeoutExpired as exc:
                case_summary["exit_code"] = "timeout"
                case_summary["stderr_tail"] = (exc.stderr or "")[-4000:] if exc.stderr else ""
            case_summary["seconds"] = round(time.monotonic() - started, 1)
            if result_path.is_file():
                try:
                    report = json.loads(result_path.read_text(encoding="utf-8"))
                    case_summary["selftest_ok"] = report.get("ok")
                    case_summary["steps"] = [
                        step.get("step") for step in report.get("steps") or []
                        if isinstance(step, dict)]
                except ValueError:
                    pass
            if child_json.is_file():
                try:
                    child = json.loads(child_json.read_text(encoding="utf-8"))
                except ValueError:
                    child = {}
                case_summary["child_counts"] = child.get("counts")
                case_summary["child_leaking_sites"] = [
                    {"site": site["site"], "api": site["api"],
                     "wrappers_created": site["wrappers_created"],
                     "measured": site["measured"],
                     "scenarios": site["scenarios"]}
                    for site in child.get("leaking_sites") or []]
                case_summary["child_checkpoints"] = [
                    {"name": cp["name"], "new_widget_items": cp["new_widget_items"],
                     "delta": {name: value["total"]
                               for name, value in cp.get("delta", {}).items()
                               if value.get("total")}}
                    for cp in child.get("checkpoints") or []]
            summary["cases"].append(case_summary)
    finally:
        stub.stop()
    return summary


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def _default_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="qt-wrapper-probe-"))


def audit_stale_wrappers(probe: Probe, *, max_reported: int = 12,
                        previous_live: "set[int] | None" = None,
                        previous_label: str = "") -> dict:

    """离线审计：包装器登记的 C++ 指针是否还对应一个活着的布局条目。

    只用 ``Shiboken.getCppPointer`` / ``Shiboken.isValid``，**不解引用任何条目**。
    活的条目指针集合来自活着的 ``QLayout`` 包装器（含递归子布局）与
    ``QApplication.allWidgets()`` 的布局。审计本身会通过 ``itemAt()`` 新建一批
    包装器，因此放在负载末尾执行，并把审计自己新建的数量单独记下来。
    """
    from PySide6.QtWidgets import QLayout

    registry = probe.registry
    shiboken = registry.shiboken
    item_type = registry.types.get("QWidgetItem")
    if item_type is None:
        return {"error": "QWidgetItem missing"}

    def wrapper_pointers() -> dict[int, object]:
        found: dict[int, object] = {}
        for obj in gc.get_objects():
            if type(obj) is item_type:
                found[_cpp_pointer(shiboken, obj)] = obj
        return found

    gc.collect()
    before = wrapper_pointers()
    live: set[int] = set()
    layouts_walked = 0
    items_seen = 0

    def walk(layout, depth: int = 0) -> None:
        nonlocal layouts_walked, items_seen
        if depth > 10:
            return
        try:
            count = layout.count()
        except (RuntimeError, AttributeError):
            return
        layouts_walked += 1
        for index in range(count):
            try:
                item = layout.itemAt(index)
            except (RuntimeError, AttributeError):
                continue
            items_seen += 1
            if item is None:
                continue
            pointer = _cpp_pointer(shiboken, item)
            if pointer:
                live.add(pointer)
            if isinstance(item, QLayout):
                walk(item, depth + 1)

    try:
        for obj in gc.get_objects():
            if not isinstance(obj, QLayout):
                continue
            if not _safe_is_valid(shiboken, obj):
                continue
            try:
                top = obj.parentWidget() is None and obj.parent() is None
            except (RuntimeError, AttributeError):
                top = False
            if top and _layout_count(obj) == 0:
                continue
            walk(obj)
    except Exception:  # noqa: BLE001
        pass
    try:
        from PySide6.QtWidgets import QApplication

        application = QApplication.instance()
        if application is not None:
            for widget in application.allWidgets():
                try:
                    layout = widget.layout()
                except (RuntimeError, AttributeError):
                    layout = None
                if layout is not None:
                    walk(layout)
    except Exception:  # noqa: BLE001
        pass

    after = wrapper_pointers()
    stale_pointers = sorted(pointer for pointer in after
                            if pointer and pointer not in live)
    # 「上一次观察时还是活的条目，现在不在任何布局里，而 Python 包装器还活着」：
    # 这是 C++ 条目被删、包装器仍留在 Shiboken 登记表里的直接证据（不靠解引用）。
    previous_live = previous_live or set()
    gone = sorted(pointer for pointer in previous_live
                  if pointer and pointer not in live)
    stale_via_previous = [
        {"cpp_pointer": hex(pointer),
         "valid_flag": _safe_is_valid(shiboken, after[pointer])}
        for pointer in gone if pointer in after][:max_reported]
    invalid = [pointer for pointer, obj in after.items()
               if not _safe_is_valid(shiboken, obj)]
    # 审计自己会新建包装器（itemAt），所以「过期」要只在审计前就存在的包装器里数，
    # 否则会把审计之后负载销毁时失效的那批也算进来，数字失真。
    stale_pre_audit = sorted(pointer for pointer in before
                             if pointer and pointer not in live)
    invalid_pre_audit = [pointer for pointer, obj in before.items()
                         if not _safe_is_valid(shiboken, obj)]
    sample = []
    for pointer in stale_pointers[:max_reported]:
        obj = after[pointer]
        holders = set()
        try:
            for ref in gc.get_referrers(obj):
                module = type(ref).__module__ or ""
                if module.startswith("PySide6"):
                    holders.add(type(ref).__name__)
        except Exception:  # noqa: BLE001
            pass
        sample.append({"cpp_pointer": hex(pointer),
                       "valid_flag": _safe_is_valid(shiboken, obj),
                       "holders": sorted(holders)})
    # 站点归因：被测量站点新建的包装器里，有多少现在已经不在任何活布局条目上
    # （包装器本身还活着 = 登记表里留着失效条目）。
    stale_from_sites: dict[str, list] = {}
    for pointer, site_name in getattr(probe, "created_pointers", {}).items():
        if not pointer or pointer in live or pointer not in after:
            continue
        stale_from_sites.setdefault(site_name, []).append({
            "cpp_pointer": hex(pointer),
            "valid_flag": _safe_is_valid(shiboken, after[pointer]),
        })
    return {
        "layout_wrappers_walked": layouts_walked,
        "items_seen": items_seen,
        "wrappers_before_audit": len(before),
        "wrappers_after_audit": len(after),
        "audit_created_wrappers": max(0, len(after) - len(before)),
        "live_item_pointers": len(live),
        "stale_wrappers": len(stale_pointers),
        "invalid_wrappers": len(invalid),
        "stale_wrappers_pre_audit": len(stale_pre_audit),
        "invalid_wrappers_pre_audit": len(invalid_pre_audit),
        "stale_pre_audit_sample": [
            {"cpp_pointer": hex(pointer),
             "valid_flag": _safe_is_valid(shiboken, before[pointer])}
            for pointer in stale_pre_audit[:max_reported]],
        "stale_sample": sample,
        "previous_audit": previous_label,
        "gone_since_previous_items": len(gone),
        "stale_after_previous_audit": len([1 for pointer in gone if pointer in after]),
        "stale_after_previous_sample": stale_via_previous,
        "stale_from_sites": {
            site_name: {"count": len(items),
                        "valid_flag_any": any(item["valid_flag"] for item in items),
                        "sample": items[:max_reported // 2]}
            for site_name, items in stale_from_sites.items()},
        "_live_set": live,
        "note": ("stale_wrappers = 包装器登记指针不在任何活布局条目里；"
                 "启发式：布局若没有 Python 包装器且宿主控件也未加壳，可能误报；"
                 "地址回收复用会让「已失效」看起来仍然活着（同样危险，但这里测不到）。"),
    }


def _selected_scenarios(args) -> list[str]:
    names = ["window", "delivery", "pdf", "review", "scan", "notice"]
    if args.scenario == "all":
        return names
    if args.scenario == "baseline":
        return []
    return [args.scenario]


def cmd_scenarios(args) -> int:
    """在**本进程**里跑负载（隔离模式下由父进程以 ``--in-process`` 调用）。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    workdir = Path(args.workdir).expanduser() if args.workdir else _default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    selected = _selected_scenarios(args)
    probe = Probe(verbose=args.verbose,
                  measure_limit_per_site=args.measure_limit,
                  stack_limit_per_site=args.stack_limit)
    probe.install()
    if args.json:
        probe._json_path = args.json
        probe.dump(args.json)
    payload: dict = {"workdir": str(workdir), "pid": os.getpid()}
    exit_code = 0
    try:
        for name in selected:
            probe.set_scenario(name)
            started = _now()
            try:
                SCENARIOS[name](probe, workdir, args)
            except Exception:  # noqa: BLE001 - 单个负载失败不丢其它结果
                traceback.print_exc()
                payload.setdefault("scenario_errors", {})[name] = \
                    traceback.format_exc()[-4000:]
                exit_code = 1
            payload.setdefault("scenarios", {})[name] = {
                "seconds": round(_now() - started, 2)}
            if args.json:
                probe.dump(args.json)
        if args.scenario == "baseline":
            probe.set_scenario("baseline")
            started = _now()
            try:
                payload["baseline"] = scenario_baseline(probe, workdir, args)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                payload.setdefault("scenario_errors", {})["baseline"] = \
                    traceback.format_exc()[-4000:]
                exit_code = 1
            payload.setdefault("scenarios", {})["baseline"] = {
                "seconds": round(_now() - started, 2)}
        if not args.no_audit:
            try:
                stale_audit = audit_stale_wrappers(probe)
                stale_audit.pop("_live_set", None)
                payload["stale_audit"] = stale_audit
            except Exception:  # noqa: BLE001
                payload.setdefault("scenario_errors", {})["stale_audit"] = \
                    traceback.format_exc()[-4000:]
    finally:
        probe.uninstall()
    probe.extra = payload
    data = probe.summary()
    data["workdir"] = str(workdir)
    if args.json:
        probe.dump(args.json)
        print(f"probe json -> {args.json}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    _print_site_table(data["leaking_sites"], data["attach_without_growth"])
    audit = payload.get("stale_audit") or {}
    print(f"\n== 过期包装器审计：stale(净)={audit.get('stale_wrappers_pre_audit', '?')} "
          f"stale(含审计噪声)={audit.get('stale_wrappers', '?')} "
          f"walked_layouts={audit.get('layout_wrappers_walked', '?')} "
          f"items_seen={audit.get('items_seen', '?')}", file=sys.stderr)
    for entry in (audit.get("stale_sample") or [])[:6]:
        print(f"   stale {entry['cpp_pointer']} valid_flag={entry['valid_flag']} "
              f"holders={entry['holders']}", file=sys.stderr)
    if data["leaking_sites"]:
        exit_code = 3
    return exit_code


def _signal_name(number: int) -> str:
    try:
        import signal

        return signal.Signals(number).name
    except Exception:  # noqa: BLE001
        return str(number)


def cmd_isolated(args) -> int:
    """每个负载各跑一个子进程：崩了也能把「哪个负载解引用了失效包装器」记下来。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    workdir = Path(args.workdir).expanduser() if args.workdir else _default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    names = _selected_scenarios(args)
    rounds = max(1, args.isolate_rounds)
    runs: list[dict] = []
    leaking: dict[str, dict] = {}
    errors = 0
    for name in names:
        for round_index in range(rounds):
            child_json = workdir / f"{name}-{round_index}.json"
            child_workdir = workdir / f"{name}-{round_index}"
            child_workdir.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(Path(__file__).resolve()),
                       "--scenario", name, "--in-process",
                       "--workdir", str(child_workdir),
                       "--repeat", str(args.repeat),
                       "--measure-limit", str(args.measure_limit),
                       "--stack-limit", str(args.stack_limit),
                       "--json", str(child_json)]
            if args.no_audit:
                command.append("--no-audit")
            if args.verbose:
                command.append("--verbose")
            env = dict(os.environ)
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
            env["PYTHONPATH"] = os.pathsep.join(
                [str(ROOT), str(ROOT / "scripts"), env.get("PYTHONPATH", "")])
            entry: dict = {"scenario": name, "round": round_index,
                           "command": command, "workdir": str(child_workdir)}
            started = _now()
            try:
                run = subprocess.run(command, cwd=str(ROOT), env=env,
                                     capture_output=True, text=True,
                                     encoding="utf-8", errors="replace",
                                     timeout=args.scenario_timeout)
                code = run.returncode
                entry["stdout_tail"] = (run.stdout or "")[-2000:]
                entry["stderr_tail"] = (run.stderr or "")[-5000:]
            except subprocess.TimeoutExpired as exc:
                code = "timeout"
                entry["stderr_tail"] = str(exc)[-2000:]
            entry["exit_code"] = code
            entry["seconds"] = round(_now() - started, 1)
            if isinstance(code, int) and code < 0:
                entry["crashed"] = True
                entry["signal"] = _signal_name(-code)
            elif code not in (0, 3):
                errors += 1
            child: dict = {}
            if child_json.is_file():
                try:
                    child = json.loads(child_json.read_text(encoding="utf-8"))
                except ValueError:
                    child = {}
            entry["counts"] = child.get("counts")
            entry["checkpoints"] = [
                {"name": cp.get("name"),
                 "new_widget_items": cp.get("new_widget_items"),
                 "delta": {key: value["total"]
                           for key, value in (cp.get("delta") or {}).items()
                           if value.get("total")}}
                for cp in child.get("checkpoints") or []
                if cp.get("new_widget_items") or any(
                    value.get("total") for value in (cp.get("delta") or {}).values())]
            entry["stale_audit"] = (child.get("driver") or {}).get("stale_audit")
            entry["audits"] = {
                label: {"stale_wrappers": audit.get("stale_wrappers"),
                        "invalid_wrappers": audit.get("invalid_wrappers"),
                        "layout_wrappers_walked": audit.get("layout_wrappers_walked"),
                        "items_seen": audit.get("items_seen"),
                        "audit_created_wrappers": audit.get("audit_created_wrappers")}
                for label, audit in (child.get("audits") or {}).items()}
            entry["leaking_sites"] = [
                {"site": site.get("site"), "api": site.get("api"),
                 "calls": site.get("calls"), "suspicious": site.get("suspicious"),
                 "measured": site.get("measured"),
                 "wrappers_created": site.get("wrappers_created"),
                 "max_delta": site.get("max_delta"),
                 "child_counts": site.get("child_counts")}
                for site in child.get("leaking_sites") or []]
            for site in entry["leaking_sites"]:
                merged = leaking.setdefault(site["site"], {
                    "site": site["site"], "api": site["api"],
                    "wrappers_created": 0, "max_delta": 0,
                    "scenarios": [], "calls": 0, "suspicious": 0,
                    "child_counts": []})
                merged["wrappers_created"] = max(merged["wrappers_created"],
                                                 site["wrappers_created"] or 0)
                merged["max_delta"] = max(merged["max_delta"], site["max_delta"] or 0)
                merged["calls"] = max(merged["calls"], site["calls"] or 0)
                merged["suspicious"] = max(merged["suspicious"], site["suspicious"] or 0)
                merged["child_counts"] = site.get("child_counts") or merged["child_counts"]
                if name not in merged["scenarios"]:
                    merged["scenarios"].append(name)
            runs.append(entry)
            print(f"[isolate] {name}[{round_index}] exit={code}"
                  + (f" signal={entry.get('signal')}" if entry.get("crashed") else "")
                  + f" {entry['seconds']}s leaks={len(entry['leaking_sites'])}"
                  + (f" stale={(entry.get('stale_audit') or {}).get('stale_wrappers')}"
                     if entry.get("stale_audit") else ""),
                  file=sys.stderr, flush=True)
    crashes = [{"scenario": entry["scenario"], "round": entry["round"],
                "signal": entry.get("signal"), "exit_code": entry.get("exit_code")}
               for entry in runs if entry.get("crashed")]
    stale_total = sum((entry.get("stale_audit") or {}).get("stale_wrappers") or 0
                      for entry in runs)
    summary = {
        "mode": "isolated",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_head": _git_head(),
        "python": sys.version.split()[0],
        "pyside6": _pyside_version(),
        "platform": f"{sys.platform}/{platform.machine()}",
        "environment": {
            "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", ""),
            "MallocScribble": os.environ.get("MallocScribble", ""),
            "MallocGuardEdges": os.environ.get("MallocGuardEdges", ""),
        },
        "scenario_rounds": rounds,
        "runs": runs,
        "crashes": crashes,
        "leaking_sites": sorted(leaking.values(),
                                key=lambda item: -item["wrappers_created"]),
        "summary": {
            "scenarios": len(names), "rounds": len(runs),
            "crashes": len(crashes),
            "crashed_scenarios": sorted({entry["scenario"] for entry in crashes}),
            "errors": errors,
            "leaking_sites": len(leaking),
            "stale_wrappers": stale_total,
            "max_stale_wrappers": max([(entry.get("stale_audit") or {})
                                       .get("stale_wrappers") or 0
                                       for entry in runs] or [0]),
            "audits_by_label": {
                label: value
                for entry in runs
                for label, value in (entry.get("audits") or {}).items()},
        },
    }
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"isolated probe json -> {args.json}", file=sys.stderr)
    else:
        print(json.dumps(summary["summary"], ensure_ascii=False, indent=1))
    _print_site_table([{"site": item["site"], "api": item["api"],
                        "calls": item["calls"], "suspicious": item["suspicious"],
                        "measured": "?", "wrappers_created": item["wrappers_created"],
                        "max_delta": item["max_delta"],
                        "scenarios": item["scenarios"]}
                       for item in summary["leaking_sites"]], [])
    if crashes:
        print("\n== 子进程崩溃（该负载解引用了失效包装器 / 或相关内存破坏）==",
              file=sys.stderr)
        for entry in crashes:
            print(f"  {entry['scenario']} round={entry['round']} "
                  f"signal={entry['signal']}", file=sys.stderr)
    if crashes or leaking:
        return 3
    return 1 if errors else 0


def _print_site_table(leaking: list[dict], attach_only: list[dict]) -> None:
    print("\n== 可疑站点（挂接时子布局已有条目，且实测产生 QWidgetItem 包装器）==",
          file=sys.stderr)
    if not leaking:
        print("  （无）", file=sys.stderr)
    for site in leaking:
        print(f"  {site['site']}  [{site['api']}] calls={site['calls']} "
              f"suspicious={site['suspicious']} measured={site['measured']} "
              f"new_wrappers={site['wrappers_created']} "
              f"max_delta={site['max_delta']} scenarios={site['scenarios']}",
              file=sys.stderr)
    print("\n== 形式命中但实测未增长（先挂后填 / 子布局为空）==", file=sys.stderr)
    for site in attach_only:
        print(f"  {site['site']}  [{site['api']}] calls={site['calls']} "
              f"suspicious={site['suspicious']}", file=sys.stderr)


def cmd_suite(args) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if args.suite in ("all", "ALL", ""):
        files = list(DEFAULT_SUITES)
    else:
        files = [item.strip() for item in args.suite.split(",") if item.strip()]
    workdir = Path(args.workdir).expanduser() if args.workdir else _default_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    combined: dict = {"suite_runs": [], "tests": {}, "sites": {},
                      "stale_audits": {}}
    failed = False
    for index, relative in enumerate(files):
        target = ROOT / relative
        if not target.is_file():
            combined["suite_runs"].append({"file": relative, "error": "missing"})
            failed = True
            continue
        child_json = workdir / f"suite-{index}.json"
        env = dict(os.environ)
        env.update({
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": os.pathsep.join(
                [str(ROOT / "scripts"), str(ROOT), env.get("PYTHONPATH", "")]),
            ENV_JSON: str(child_json),
        })
        command = [sys.executable, "-m", "pytest", "-q", "--no-header",
                   "-p", "qt_stale_wrapper_probe", relative]
        started = time.monotonic()
        try:
            run = subprocess.run(command, cwd=str(ROOT), env=env,
                                 capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=args.suite_timeout)
            exit_code = run.returncode
            tail = (run.stdout or "")[-1500:]
            errors_tail = (run.stderr or "")[-2000:]
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
            tail = errors_tail = "timeout"
        entry = {
            "file": relative,
            "exit_code": exit_code,
            "seconds": round(time.monotonic() - started, 1),
            "tail": tail,
            "stderr_tail": errors_tail,
        }
        combined["suite_runs"].append(entry)
        if exit_code not in (0, 5):
            failed = True
        if child_json.is_file():
            try:
                child = json.loads(child_json.read_text(encoding="utf-8"))
            except ValueError:
                child = {}
            for node, result in (child.get("tests") or {}).items():
                combined["tests"][node] = {
                    "delta": {name: info for name, info in result.get("delta", {}).items()
                              if info},
                    "total_after": {name: info["total"]
                                    for name, info in (result.get("after") or {}).items()
                                    if info.get("total")},
                }
            for site in child.get("sites") or []:
                if not site.get("suspicious"):
                    continue
                combined["sites"][site["site"]] = {
                    "api": site["api"], "calls": site["calls"],
                    "suspicious": site["suspicious"],
                    "wrappers_created": site["wrappers_created"],
                    "max_delta": site["max_delta"],
                    "scenarios": site["scenarios"],
                }
            if (child.get("driver") or {}).get("stale_audit"):
                combined["stale_audits"][relative] = \
                    child["driver"]["stale_audit"]
    leaking = {site: info for site, info in combined["sites"].items()
               if info["wrappers_created"] > 0}
    combined["summary"] = {
        "files": len(files),
        "failed_files": [entry["file"] for entry in combined["suite_runs"]
                         if entry.get("exit_code") not in (0, 5)],
        "leaking_sites": len(leaking),
        "stale_wrappers_by_file": {
            name: audit.get("stale_wrappers")
            for name, audit in combined["stale_audits"].items()},
        "tests_with_wrapper_growth": {
            node: result["delta"] for node, result in combined["tests"].items()
            if any(value > 0 for value in result["delta"].values())},
    }
    if args.json:
        Path(args.json).write_text(json.dumps(combined, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
        print(f"suite probe json -> {args.json}", file=sys.stderr)
    else:
        print(json.dumps(combined["summary"], ensure_ascii=False, indent=1))
    _print_site_table(
        [{"site": site, **info} for site, info in leaking.items()], [])
    return 1 if failed else (3 if leaking else 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行时探测器：找出制造过期 PySide 包装器的布局调用点。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="all",
                        choices=["all", "window", "delivery", "pdf", "review",
                                 "scan", "notice", "baseline"],
                        help="驱动哪个负载（默认 all：前六个；baseline=真实 selftest 路径）")
    parser.add_argument("--suite", default="",
                        help=("跑现有测试文件：'all' 或逗号分隔的 tests/xxx_test.py；"
                              "给了就只跑套件，不跑负载"))
    parser.add_argument("--repeat", type=int, default=3,
                        help="负载重复次数（delivery/pdf/scan/notice/window）")
    parser.add_argument("--json", default="", help="机器可读结果 JSON 输出路径")
    parser.add_argument("--workdir", default="", help="临时工作目录（默认 mkdtemp）")
    parser.add_argument("--measure-limit", type=int, default=24,
                        help="每个站点最多实测多少次包装器增量（默认 24）")
    parser.add_argument("--stack-limit", type=int, default=2,
                        help="每个站点保存几份完整栈")
    parser.add_argument("--in-process", action="store_true",
                        help="在本进程跑负载（默认每个负载一个子进程，崩溃可控）")
    parser.add_argument("--isolate-rounds", type=int, default=1,
                        help="隔离模式下每个负载跑几轮（崩溃复现用）")
    parser.add_argument("--scenario-timeout", type=int, default=1800,
                        help="隔离模式单个负载子进程超时秒数")
    parser.add_argument("--no-audit", action="store_true",
                        help="跳过末尾的过期包装器审计（审计会用 itemAt 新建一批包装器）")
    parser.add_argument("--suite-timeout", type=int, default=1800,
                        help="单个测试文件超时秒数")
    parser.add_argument("--baseline-cases", default="eng",
                        help="baseline 跑哪些 OCR 用例：eng,chi,ara（逗号分隔）")
    parser.add_argument("--baseline-timeout", type=int, default=900)
    parser.add_argument("--baseline-malloc-scribble", action="store_true",
                        help="baseline 子进程加 MallocScribble=1（放大 use-after-free）")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.json:
        parent = Path(args.json).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
    if args.suite:
        return cmd_suite(args)
    if args.scenario == "baseline" or args.in_process:
        return cmd_scenarios(args)
    return cmd_isolated(args)


if __name__ == "__main__":
    sys.exit(main())
