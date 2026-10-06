#!/usr/bin/env python3
"""辅助功能（AX）扫描探针 —— 复现 macOS 27 上外部 AX 客户端遍历 Qt 界面导致的原生崩溃。

背景：2026-10-05 的人工验收显示，AI "Computer Use"（外部 AX 客户端）遍历候选界面并在
打开任务后崩溃（栈顶 `libqcocoa` + `NSAccessibilityAttributeAccessorInfo
getAttributeValue:forObject:` ← `accessibilityArrayAttributeCount:` ←
`__AXCopyAttributeValueForHierarchy`）。本探针以与外部扫描器相同的 API 顺序遍历目标进程：
对每个元素取属性名 → 取每个属性值 → 对数组属性取**数量**（`AXUIElementCopyAttributeValue`
返回 CFArray 时用 `CFArrayGetCount`，这正是 AppKit 里 `GetAttributeValueCount` 的路径）
→ 递归子元素。

诚实性约束：
- 本进程无辅助功能授权时，探针输出 `NOT_RUN`（退出码 2），**不**当作通过。
- 目标进程被探针观察期间若消失，报告 `CRASHED`/`GONE`（退出码 3）并附退出码与信号；
  探针**不能**证明崩溃的原因归属，只能证明"该扫描顺序下进程死了"。
- 探针不修改目标进程数据目录以外的任何状态。

用法：
    venv/bin/python scripts/qt_ax_scan_probe.py --pid <pid> [--duration 30] [--out out.json]
    venv/bin/python scripts/qt_ax_scan_probe.py --launch "<executable> --args..." [--out out.json]
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

KCFSTRINGENCODING_UTF8 = 0x08000100
AX_ERROR_SUCCESS = 0
AX_ERROR_API_DISABLED = -25211  # kAXErrorAPIDisabled
AX_ERROR_NOT_TRUSTED = -25204  # kAXErrorNotTrusted (older name) / cannot complete


class AXClient:
    def __init__(self) -> None:
        self.asr = ctypes.cdll.LoadLibrary(ctypes.util.find_library("ApplicationServices"))
        self.cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        self._declare()

    def _declare(self) -> None:
        asr, cf = self.asr, self.cf
        asr.AXIsProcessTrusted.restype = ctypes.c_bool
        asr.AXUIElementCreateApplication.restype = ctypes.c_void_p
        asr.AXUIElementCreateApplication.argtypes = [ctypes.c_int]
        asr.AXUIElementCreateSystemWide.restype = ctypes.c_void_p
        asr.AXUIElementCreateSystemWide.argtypes = []
        asr.AXUIElementGetPid.restype = ctypes.c_int
        asr.AXUIElementGetPid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        asr.AXUIElementGetTypeID.restype = ctypes.c_ulong
        asr.AXUIElementCopyAttributeNames.restype = ctypes.c_int
        asr.AXUIElementCopyAttributeNames.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        asr.AXUIElementCopyAttributeValue.restype = ctypes.c_int
        asr.AXUIElementCopyAttributeValue.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        asr.AXUIElementCopyActionNames.restype = ctypes.c_int
        asr.AXUIElementCopyActionNames.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        asr.AXUIElementPerformAction.restype = ctypes.c_int
        asr.AXUIElementPerformAction.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFRetain.restype = ctypes.c_void_p
        cf.CFRetain.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFGetTypeID.restype = ctypes.c_ulong
        cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetTypeID.restype = ctypes.c_ulong
        cf.CFStringGetTypeID.restype = ctypes.c_ulong
        cf.CFNumberGetTypeID.restype = ctypes.c_ulong
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFNumberGetValue.restype = ctypes.c_bool
        cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        cf.CFBooleanGetValue.restype = ctypes.c_bool
        cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        self._type_ids = {
            "array": cf.CFArrayGetTypeID(),
            "string": cf.CFStringGetTypeID(),
            "number": cf.CFNumberGetTypeID(),
            "ax": asr.AXUIElementGetTypeID(),
        }

    # --- 基础封装 -------------------------------------------------------
    def trusted(self) -> bool:
        return bool(self.asr.AXIsProcessTrusted())

    def cfstring(self, text: str):
        return self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), KCFSTRINGENCODING_UTF8)

    def to_text(self, value) -> str | None:
        if not value:
            return None
        if self.cf.CFGetTypeID(value) != self._type_ids["string"]:
            return None
        buf = ctypes.create_string_buffer(4096)
        if self.cf.CFStringGetCString(value, buf, 4096, KCFSTRINGENCODING_UTF8):
            return buf.value.decode("utf-8", "replace")
        return None

    def type_name(self, value) -> str:
        if not value:
            return "null"
        tid = self.cf.CFGetTypeID(value)
        for name, known in self._type_ids.items():
            if tid == known:
                return name
        return f"cf:{tid}"

    def element_pid(self, element) -> int | None:
        pid = ctypes.c_int(0)
        err = self.asr.AXUIElementGetPid(element, ctypes.byref(pid))
        return pid.value if err == AX_ERROR_SUCCESS else None

    def attribute_names(self, element) -> list[str]:
        arr = ctypes.c_void_p()
        err = self.asr.AXUIElementCopyAttributeNames(element, ctypes.byref(arr))
        if err != AX_ERROR_SUCCESS or not arr.value:
            return []
        names = self._strings_in_array(arr.value)
        self.cf.CFRelease(arr)
        return names

    def action_names(self, element) -> list[str]:
        arr = ctypes.c_void_p()
        err = self.asr.AXUIElementCopyActionNames(element, ctypes.byref(arr))
        if err != AX_ERROR_SUCCESS or not arr.value:
            return []
        names = self._strings_in_array(arr.value)
        self.cf.CFRelease(arr)
        return names

    def _strings_in_array(self, arr) -> list[str]:
        out = []
        for i in range(self.cf.CFArrayGetCount(arr)):
            item = self.cf.CFArrayGetValueAtIndex(arr, i)
            text = self.to_text(item)
            if text is not None:
                out.append(text)
        return out

    def copy_attribute(self, element, attribute: str):
        """返回 (err, value)；err == 0 时 value 需由调用方 CFRelease。"""
        name = self.cfstring(attribute)
        value = ctypes.c_void_p()
        try:
            err = self.asr.AXUIElementCopyAttributeValue(element, name, ctypes.byref(value))
        finally:
            self.cf.CFRelease(name)
        return err, value

    def perform_action(self, element, action: str) -> int:
        name = self.cfstring(action)
        try:
            return self.asr.AXUIElementPerformAction(element, name)
        finally:
            self.cf.CFRelease(name)

    def array_count(self, value) -> int:
        return self.cf.CFArrayGetCount(value)

    def array_item(self, value, index: int):
        return self.cf.CFArrayGetValueAtIndex(value, index)


def as_int(ptr) -> int:
    """把 c_void_p / int 统一成整数地址，便于做 seen 集合与队列元素。"""
    if isinstance(ptr, ctypes.c_void_p):
        return ptr.value or 0
    return int(ptr)


_ZOMBIE_CACHE: dict[str, float | int | bool | None] = {"pid": None, "at": 0.0, "alive": True}


def process_alive(pid: int) -> bool:
    """进程是否真的活着。

    **陷阱**：崩溃后的进程会变成僵尸（zombie），`os.kill(pid, 0)` 仍然成功——
    我第一版探针因此把已经 SIGSEGV 的目标报成 ALIVE（还多跑了 279 轮空扫描）。
    所以这里再用 `ps -o stat=` 确认不是 Z 状态；结果按 0.5s 缓存，避免扫描循环里反复 fork。
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    now = time.time()
    if _ZOMBIE_CACHE["pid"] == pid and now - float(_ZOMBIE_CACHE["at"] or 0.0) < 0.5:
        return bool(_ZOMBIE_CACHE["alive"])
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    alive = bool(stat) and not stat.startswith("Z")
    _ZOMBIE_CACHE.update(pid=pid, at=now, alive=alive)
    return alive


def is_element(value, client: AXClient) -> bool:
    return bool(value) and client.cf.CFGetTypeID(value) == client._type_ids["ax"]


class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class Mouse:
    """真实合成鼠标事件（与 computer use 点击等价）；需要辅助功能授权。"""

    KCG_EVENT_LEFT_MOUSE_DOWN = 1
    KCG_EVENT_LEFT_MOUSE_UP = 2
    KCG_EVENT_MOUSE_MOVED = 5
    KCG_HID_EVENT_TAP = 0
    KCG_MOUSE_BUTTON_LEFT = 0
    KAX_VALUE_CG_POINT_TYPE = 1
    KAX_VALUE_CG_SIZE_TYPE = 2

    def __init__(self, client: AXClient) -> None:
        self.c = client
        cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        self.cg = cg
        cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        cg.CGEventCreateMouseEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            CGPoint,
            ctypes.c_uint32,
        ]
        cg.CGEventPost.restype = None
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cg.CGEventSetIntegerValueField.restype = None
        cg.CGEventSetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int64]
        cg.CGWarpMouseCursorPosition.restype = ctypes.c_int
        cg.CGWarpMouseCursorPosition.argtypes = [CGPoint]
        self.c.asr.AXValueGetValue.restype = ctypes.c_bool
        self.c.asr.AXValueGetValue.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]

    def frame(self, element) -> tuple[float, float, float, float] | None:
        point = CGPoint()
        size = CGSize()
        ok_point = ok_size = False
        for attribute, holder, kind in (
            ("AXPosition", ctypes.byref(point), self.KAX_VALUE_CG_POINT_TYPE),
            ("AXSize", ctypes.byref(size), self.KAX_VALUE_CG_SIZE_TYPE),
        ):
            err, value = self.c.copy_attribute(element, attribute)
            if err == AX_ERROR_SUCCESS and value:
                ok = bool(self.c.asr.AXValueGetValue(value, kind, holder))
                self.c.cf.CFRelease(value)
                if attribute == "AXPosition":
                    ok_point = ok
                else:
                    ok_size = ok
        if not (ok_point and ok_size):
            return None
        return point.x, point.y, size.width, size.height

    def click(self, x: float, y: float) -> None:
        self.cg.CGWarpMouseCursorPosition(CGPoint(x, y))
        time.sleep(0.15)
        for kind in (self.KCG_EVENT_MOUSE_MOVED, self.KCG_EVENT_LEFT_MOUSE_DOWN, self.KCG_EVENT_LEFT_MOUSE_UP):
            event = self.cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), self.KCG_MOUSE_BUTTON_LEFT)
            if event:
                self.cg.CGEventPost(self.KCG_HID_EVENT_TAP, event)
                self.c.cf.CFRelease(event)
            time.sleep(0.08)

    def double_click(self, x: float, y: float) -> None:
        """真实双击（第二次按下时 clickState=2），用于触发表格的 doubleClicked 信号。"""
        self.cg.CGWarpMouseCursorPosition(CGPoint(x, y))
        time.sleep(0.15)
        # kCGEventMouseMoved=5, kCGEventLeftMouseDown=1, kCGEventLeftMouseUp=2
        # kCGMouseEventClickState=1
        for kind in (self.KCG_EVENT_MOUSE_MOVED, self.KCG_EVENT_LEFT_MOUSE_DOWN, self.KCG_EVENT_LEFT_MOUSE_UP):
            event = self.cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), self.KCG_MOUSE_BUTTON_LEFT)
            if event:
                self.cg.CGEventPost(self.KCG_HID_EVENT_TAP, event)
                self.c.cf.CFRelease(event)
            time.sleep(0.04)
        for kind in (self.KCG_EVENT_LEFT_MOUSE_DOWN, self.KCG_EVENT_LEFT_MOUSE_UP):
            event = self.cg.CGEventCreateMouseEvent(None, kind, CGPoint(x, y), self.KCG_MOUSE_BUTTON_LEFT)
            if event:
                self.cg.CGEventSetIntegerValueField(event, 1, 2)
                self.cg.CGEventPost(self.KCG_HID_EVENT_TAP, event)
                self.c.cf.CFRelease(event)
            time.sleep(0.04)

    def activate(self, pid: int) -> bool:
        """把目标应用带到最前（合成鼠标点击的前提）。

        只用 NSRunningApplication.activate（AppKit）。**不要**用 System Events 的
        `set frontmost ... to true`：实测它会把焦点交给别的应用（本机上是 WPS），
        于是后续点击落到错误的窗口上——这正是我第一轮复现失败的原因。
        """
        activated = False
        app_element = self.c.asr.AXUIElementCreateApplication(pid)
        if app_element:
            self.c.perform_action(app_element, "AXRaise")
            self.c.cf.CFRelease(app_element)
        try:
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

            running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            if running is not None:
                activated = bool(running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))
        except Exception as exc:  # pragma: no cover - 仅在缺 pyobjc 时走到
            print(f"[ax-probe] AppKit 激活不可用（{exc}），退回 AXRaise", flush=True)
        time.sleep(0.8)
        return activated

    def frontmost_pid(self) -> int | None:
        """当前最前应用的 pid（用于在点击前确认目标真的在最前）。"""
        system = self.c.asr.AXUIElementCreateSystemWide()
        if not system:
            return None
        try:
            err, focused = self.c.copy_attribute(system, "AXFocusedApplication")
            if err != AX_ERROR_SUCCESS or not focused:
                return None
            try:
                return self.c.element_pid(focused)
            finally:
                self.c.cf.CFRelease(focused)
        finally:
            self.c.cf.CFRelease(system)


def find_elements(client: AXClient, pid: int, title_substring: str, require_press: bool = False):
    """返回标题匹配的元素（完全匹配优先），并保留引用；调用方负责 CFRelease。"""
    app = client.asr.AXUIElementCreateApplication(pid)
    if not app:
        return []
    matches: list[tuple[int, str, str, int]] = []
    try:
        stack = [as_int(app)]
        seen = set()
        while stack:
            element = stack.pop()
            if element in seen:
                continue
            seen.add(element)
            err, title = client.copy_attribute(element, "AXTitle")
            if err == AX_ERROR_SUCCESS and title:
                text = client.to_text(title)
                client.cf.CFRelease(title)
                if text and (text == title_substring or title_substring in text):
                    err2, role_value = client.copy_attribute(element, "AXRole")
                    role = client.to_text(role_value) if err2 == AX_ERROR_SUCCESS and role_value else ""
                    if role_value:
                        client.cf.CFRelease(role_value)
                    actions = client.action_names(element)
                    if require_press and "AXPress" not in actions:
                        pass
                    else:
                        priority = 0 if text == title_substring else 1
                        if role in ("AXButton", "AXCell", "AXRow", "AXMenuItem", "AXRadioButton"):
                            priority -= 1
                        client.cf.CFRetain(element)
                        matches.append((priority, text, role, element))
            err, children = client.copy_attribute(element, "AXChildren")
            if err == AX_ERROR_SUCCESS and children:
                if client.cf.CFGetTypeID(children) == client._type_ids["array"]:
                    for i in range(client.cf.CFArrayGetCount(children)):
                        child = client.cf.CFArrayGetValueAtIndex(children, i)
                        if child:
                            client.cf.CFRetain(child)
                            stack.append(as_int(child))
                client.cf.CFRelease(children)
    finally:
        client.cf.CFRelease(app)
    matches.sort(key=lambda item: item[0])
    return matches



class Scanner:
    """按外部 AX 扫描器的顺序遍历：属性名 → 每个属性值 → 数组属性的数量 → 递归元素。"""

    def __init__(self, client: AXClient, pid: int, max_nodes: int = 4000, verbose: bool = True):
        self.c = client
        self.pid = pid
        self.max_nodes = max_nodes
        self.verbose = verbose
        self.nodes = 0
        self.passes = 0
        self.array_counts = 0
        self.errors: dict[str, int] = {}
        self.roles: dict[str, int] = {}
        self.trace: list[str] = []

    def scan_once(self) -> bool:
        """扫一遍整棵树；返回 False 表示目标进程已经不在了。"""
        self.passes += 1
        self.nodes = 0
        app = self.c.asr.AXUIElementCreateApplication(self.pid)
        if not app:
            return False
        try:
            queue = [app]
            seen = set()
            while queue:
                element = queue.pop(0)
                key = as_int(element)
                if key in seen:
                    self.c.cf.CFRelease(element)
                    continue
                seen.add(key)
                if not process_alive(self.pid):
                    return False
                role = self.visit(element, queue)
                if self.verbose and self.nodes <= 400:
                    self.trace.append(f"#{self.nodes} {role}")
                self.nodes += 1
                if self.nodes > self.max_nodes:
                    break
        finally:
            self.c.cf.CFRelease(app)
        return True

    def visit(self, element, queue: list) -> str:
        role_text = "?"
        attributes = self.c.attribute_names(element)
        for attribute in attributes:
            err, value = self.c.copy_attribute(element, attribute)
            if err != AX_ERROR_SUCCESS:
                self.errors[f"{attribute}:{err}"] = self.errors.get(f"{attribute}:{err}", 0) + 1
                continue
            if not value:
                continue
            kind = self.c.type_name(value)
            if kind == "array":
                count = self.c.array_count(value)
                self.array_counts += 1
                if attribute in ("AXChildren", "AXChildrenInNavigationOrder"):
                    for i in range(count):
                        child = self.c.array_item(value, i)
                        if is_element(child, self.c):
                            self.c.cf.CFRetain(child)
                            queue.append(as_int(child))
            elif kind == "string":
                text = self.c.to_text(value) or ""
                if attribute == "AXRole":
                    role_text = text
                    self.roles[text] = self.roles.get(text, 0) + 1
            self.c.cf.CFRelease(value)
        return role_text


def snapshot(
    pid: int,
    duration: float,
    client: AXClient,
    max_nodes: int,
    verbose: bool,
    actions: list[tuple[str, str]],
    action_timeout: float = 20.0,
):
    """先扫一遍，再按顺序执行动作（press=纯 AX 按下 / click=合成鼠标点击），然后持续扫描。"""
    scanner = Scanner(client, pid, max_nodes=max_nodes, verbose=verbose)
    mouse = Mouse(client)
    deadline = time.time() + duration
    died_at = None
    performed: list[dict] = []
    queue = list(actions)
    while time.time() < deadline:
        if not process_alive(pid):
            died_at = datetime.now(timezone.utc).isoformat()
            break
        if not scanner.scan_once():
            died_at = datetime.now(timezone.utc).isoformat()
            break
        if queue:
            kind, needle = queue.pop(0)
            time.sleep(0.4)
            if not process_alive(pid):
                died_at = datetime.now(timezone.utc).isoformat()
                performed.append({"kind": kind, "needle": needle, "ok": False, "note": "进程在动作前已消失"})
                break
            if kind == "click":
                ok, note = click_by_title(client, mouse, pid, needle, action_timeout)
            elif kind == "point":
                ok, note = click_point(client, mouse, pid, needle)
            elif kind == "dblpoint":
                ok, note = click_point(client, mouse, pid, needle, double=True)
            else:
                ok, note = press_by_title(client, pid, needle, action_timeout)
            performed.append({"kind": kind, "needle": needle, "ok": ok, "note": note})
            time.sleep(1.4)
        time.sleep(0.3)
    scanner.performed = performed
    return scanner, died_at


def click_by_title(client: AXClient, mouse: Mouse, pid: int, title_substring: str, timeout: float = 20.0) -> tuple[bool, str]:
    """用真实合成鼠标事件点击标题匹配的元素中心（与 computer use 的做法一致）。"""
    deadline = time.time() + timeout
    matches = find_elements(client, pid, title_substring)
    while not matches and time.time() < deadline and process_alive(pid):
        time.sleep(0.5)
        matches = find_elements(client, pid, title_substring)
    if not matches:
        return False, "未找到标题匹配的元素"
    last_note = "元素没有可用的 AXPosition/AXSize"
    for _priority, text, role, element in matches:
        frame = mouse.frame(element)
        if not frame:
            continue
        x, y, width, height = frame
        if width <= 0 or height <= 0:
            continue
        mouse.activate(pid)
        front = mouse.frontmost_pid()
        if front is not None and front != pid:
            last_note = f"目标不是最前应用（最前 pid={front}），未点击以免误触其他应用"
            continue
        mouse.click(x + width / 2, y + height / 2)
        print(f"[ax-probe] clicked title={text!r} role={role!r} at=({x + width / 2:.0f},{y + height / 2:.0f})", flush=True)
        last_note = f"clicked at ({x + width / 2:.0f},{y + height / 2:.0f})"
        for _p, _t, _r, held in matches:
            client.cf.CFRelease(held)
        return True, last_note
    for _p, _t, _r, held in matches:
        client.cf.CFRelease(held)
    return False, last_note


def click_point(client: AXClient, mouse: Mouse, pid: int, spec: str, double: bool = False) -> tuple[bool, str]:
    """在屏幕绝对坐标上点击（AX 树不可见时 agent 的做法：靠截图/坐标）。spec = "x,y"。"""
    try:
        x_text, y_text = spec.split(",")
        x, y = float(x_text), float(y_text)
    except ValueError:
        return False, f"坐标格式应为 'x,y'，收到 {spec!r}"
    mouse.activate(pid)
    front = mouse.frontmost_pid()
    if front is not None and front != pid:
        return False, f"目标不是最前应用（最前 pid={front}），未点击以免误触其他应用"
    if double:
        mouse.double_click(x, y)
        print(f"[ax-probe] double-clicked point ({x:.0f},{y:.0f})", flush=True)
        return True, f"double-clicked point ({x:.0f},{y:.0f})"
    mouse.click(x, y)
    print(f"[ax-probe] clicked point ({x:.0f},{y:.0f})", flush=True)
    return True, f"clicked point ({x:.0f},{y:.0f})"


def press_by_title(client: AXClient, pid: int, title_substring: str, timeout: float = 20.0) -> tuple[bool, str]:
    """用 AX 找到标题匹配、且支持 AXPress 的元素并按下去；控件由禁用变可用时等待。"""
    deadline = time.time() + timeout
    waited = 0.0
    matches = find_elements(client, pid, title_substring, require_press=True)
    while not matches and time.time() < deadline and process_alive(pid):
        time.sleep(0.5)
        waited += 0.5
        matches = find_elements(client, pid, title_substring, require_press=True)
    if not matches:
        return False, f"等待 {waited:.1f}s 后仍未找到标题匹配且可 AXPress 的元素"
    priority, text, role, element = matches[0]
    err = client.perform_action(element, "AXPress")
    print(f"[ax-probe] AXPress title={text!r} role={role!r} err={err}", flush=True)
    for _p, _t, _r, held in matches:
        client.cf.CFRelease(held)
    return err == AX_ERROR_SUCCESS, f"AXPress err={err}（等待 {waited:.1f}s）"


def run_trial(
    client: AXClient,
    launch: str,
    duration: float,
    max_nodes: int,
    actions: list[tuple[str, str]],
    action_timeout: float,
    verbose: bool,
    trial: int,
    warmup: float = 3.0,
) -> dict:
    """启动一次目标、扫描并执行动作、判定它是否活着。返回该次试验的结果。"""
    proc = subprocess.Popen(launch, shell=True)
    pid = proc.pid
    print(f"[ax-probe] trial {trial}: launched pid={pid}", flush=True)
    started = datetime.now(timezone.utc).isoformat()
    time.sleep(warmup)
    if not process_alive(pid):
        return {"trial": trial, "pid": pid, "status": "GONE", "started_at": started, "performed": []}
    scanner, died_at = snapshot(pid, duration, client, max_nodes, verbose, actions, action_timeout)
    alive = process_alive(pid)
    result = {
        "trial": trial,
        "pid": pid,
        "status": "ALIVE" if alive else "DIED_DURING_SCAN",
        "started_at": started,
        "died_at": died_at,
        "scan_passes": scanner.passes,
        "nodes_last_pass": scanner.nodes,
        "array_attribute_reads": scanner.array_counts,
        "roles": dict(sorted(scanner.roles.items(), key=lambda kv: -kv[1])),
        "copy_errors": dict(sorted(scanner.errors.items(), key=lambda kv: -kv[1])[:10]),
        "performed": getattr(scanner, "performed", []),
    }
    if not alive:
        try:
            result["exit_code"] = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            result["exit_code"] = None
        result["exit_signal"] = describe_wait_status(result.get("exit_code"))
        try:
            out = subprocess.run(
                ["log", "show", "--last", "3m", "--predicate", f"processID == {pid}", "--style", "compact"],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout
            result["log_tail"] = out.splitlines()[-20:]
        except Exception as exc:  # pragma: no cover - 诊断尽力而为
            result["log_tail"] = f"unavailable: {exc}"
    # 无论死没死都清理，避免僵尸与残留进程
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if process_alive(pid):
            try:
                os.kill(pid, sig)
            except OSError:
                pass
            time.sleep(0.8)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if verbose:
        print(json.dumps({k: v for k, v in result.items() if k not in ("roles", "copy_errors")}, ensure_ascii=False, indent=2))
    return result


def describe_wait_status(code) -> str:
    """把 Popen.wait() 的返回码翻译成信号描述（负值 = 被信号杀死）。"""
    if code is None:
        return "unknown"
    if code < 0:
        try:
            return f"signal {signal.Signals(-code).name} ({-code})"
        except ValueError:
            return f"signal {-code}"
    return f"exit {code}"


def main() -> int:
    parser = argparse.ArgumentParser(description="AX 扫描探针（复现 macOS 27 Qt 可访问性崩溃）")
    parser.add_argument("--pid", type=int, help="目标进程 pid（单次试验）")
    parser.add_argument("--launch", help="要启动的命令行（shell 拆分）；与 --pid 互斥")
    parser.add_argument("--duration", type=float, default=20.0, help="扫描时长（秒）")
    parser.add_argument("--warmup", type=float, default=3.0, help="启动后等界面就绪的秒数（冷启动约需 6s）")
    parser.add_argument("--trials", type=int, default=1, help="重复次数（每次都重新启动目标；需要 --launch）")
    parser.add_argument("--max-nodes", type=int, default=4000)
    parser.add_argument("--press", help="扫描开始后按下的按钮标题（子串匹配，如 打开任务）")
    parser.add_argument(
        "--press-sequence",
        help="按顺序用 AXPress 按下的标题列表（逗号分隔，先扫一遍再依次执行），如 '打开任务'",
    )
    parser.add_argument(
        "--click-sequence",
        help="按顺序用合成鼠标点击的标题列表（逗号分隔），如 'selftest.docx,打开任务'；与 computer use 点击等价",
    )
    parser.add_argument(
        "--actions",
        help="按顺序执行的动作，逗号分隔，每项 'click:标题' 或 'press:标题'，如 'click:selftest.docx,press:打开任务'",
    )
    parser.add_argument(
        "--click-points",
        help="屏幕绝对坐标点击列表，分号分隔，如 '456,272;280,893'（AX 树不可见时的 agent 做法）",
    )
    parser.add_argument(
        "--double-click-points",
        help="屏幕绝对坐标双击列表，分号分隔，如 '456,272'（用于触发表格 doubleClicked，不依赖 AX）",
    )
    parser.add_argument("--action-timeout", type=float, default=20.0, help="单个动作等待控件可用的上限（秒）")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--out", help="JSON 结果路径")
    parser.add_argument("--keep-running", action="store_true", help="单次试验结束时保留 --launch 启动的进程")
    args = parser.parse_args()

    client = AXClient()
    result: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "trusted": client.trusted(),
        "host_os": subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip(),
        "host_build": subprocess.run(["sw_vers", "-buildVersion"], capture_output=True, text=True).stdout.strip(),
    }
    if not client.trusted():
        result.update(status="NOT_RUN", reason="AXIsProcessTrusted() == False：本进程无辅助功能授权，无法驱动扫描")
        emit(result, args.out)
        print("[ax-probe] NOT_RUN：无辅助功能授权", file=sys.stderr)
        return 2

    actions: list[tuple[str, str]] = []
    for item in (args.actions or "").split(","):
        if not item:
            continue
        kind, _, title = item.partition(":")
        if kind not in ("click", "press", "point", "dblpoint") or not title:
            parser.error(f"--actions 项格式应为 'click:标题' 或 'press:标题'，收到 {item!r}")
        actions.append((kind, title))
    actions += [("press", item) for item in (args.press_sequence or args.press or "").split(",") if item]
    actions += [("click", item) for item in (args.click_sequence or "").split(",") if item]
    actions += [("point", item) for item in (args.click_points or "").split(";") if item]
    actions += [("dblpoint", item) for item in (args.double_click_points or "").split(";") if item]

    if args.trials > 1 and not args.launch:
        parser.error("--trials > 1 需要 --launch（每次试验重新启动目标）")

    if args.trials > 1:
        trials = []
        for index in range(1, args.trials + 1):
            trials.append(
                run_trial(
                    client,
                    args.launch,
                    args.duration,
                    args.max_nodes,
                    actions,
                    args.action_timeout,
                    not args.quiet,
                    index,
                    args.warmup,
                )
            )
            time.sleep(2.0)
        died = [t for t in trials if t["status"] != "ALIVE"]
        result.update(
            status="DIED" if died else "ALIVE",
            trials=trials,
            trials_total=len(trials),
            trials_died=len(died),
            crash_rate=round(len(died) / len(trials), 3),
        )
        emit(result, args.out)
        print(
            f"[ax-probe] {len(died)}/{len(trials)} 次试验中目标进程死亡；"
            f"状态={result['status']}",
            flush=True,
        )
        return 3 if died else 0

    # 单次试验：--pid 直接扫，--launch 走同一条 run_trial 逻辑但保留进程可选
    if args.pid:
        if not process_alive(args.pid):
            result.update(status="GONE", reason="目标进程在扫描开始前已不存在", pid=args.pid)
            emit(result, args.out)
            return 3
        scanner, died_at = snapshot(
            args.pid, args.duration, client, args.max_nodes, not args.quiet, actions, args.action_timeout
        )
        alive = process_alive(args.pid)
        result.update(
            status="ALIVE" if alive else "DIED_DURING_SCAN",
            pid=args.pid,
            scan_passes=scanner.passes,
            nodes_last_pass=scanner.nodes,
            array_attribute_reads=scanner.array_counts,
            roles=dict(sorted(scanner.roles.items(), key=lambda kv: -kv[1])),
            copy_errors=dict(sorted(scanner.errors.items(), key=lambda kv: -kv[1])[:20]),
            died_at=died_at,
            performed=getattr(scanner, "performed", []),
            trace=scanner.trace[:400] if not args.quiet else [],
        )
        emit(result, args.out)
        print(json.dumps({k: v for k, v in result.items() if k != "trace"}, ensure_ascii=False, indent=2))
        return 0 if alive else 3

    if not args.launch:
        parser.error("需要 --pid 或 --launch")

    if args.keep_running:
        # 保留进程时不能走 run_trial 的清理逻辑，手工启动并扫描
        proc = subprocess.Popen(args.launch, shell=True)
        pid = proc.pid
        print(f"[ax-probe] launched pid={pid}", flush=True)
        time.sleep(args.warmup)
        scanner, died_at = snapshot(
            pid, args.duration, client, args.max_nodes, not args.quiet, actions, args.action_timeout
        )
        alive = process_alive(pid)
        result.update(
            status="ALIVE" if alive else "DIED_DURING_SCAN",
            pid=pid,
            scan_passes=scanner.passes,
            nodes_last_pass=scanner.nodes,
            array_attribute_reads=scanner.array_counts,
            performed=getattr(scanner, "performed", []),
            died_at=died_at,
        )
        emit(result, args.out)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if alive else 3

    trial = run_trial(
        client,
        args.launch,
        args.duration,
        args.max_nodes,
        actions,
        args.action_timeout,
        not args.quiet,
        1,
        args.warmup,
    )
    result.update(trial)
    result["trace"] = []
    emit(result, args.out)
    return 0 if trial["status"] == "ALIVE" else 3


def emit(result: dict, out: str | None) -> None:
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print(f"[ax-probe] wrote {out}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
