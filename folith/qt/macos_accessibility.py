"""macOS 外部辅助功能（AX）扫描导致 Qt 平台插件崩溃的运行时修复。

## 缺陷（上游 QTBUG-149612，不是本项目的业务代码）

macOS 26/27 上，外部 AX 客户端（AI "Computer Use"、VoiceOver、窗口管理器的
AX 扫描）遍历 Qt 界面时会读取 `kAXSelectedChildrenAttribute`。Qt 的 Cocoa
桥（`libqcocoa`）为表格合成了 row/cell 占位元素，这些占位元素**故意与父表共用
同一个 accessibility id**（`QMacAccessibilityElement` 的私有成员 `axid`），
由父表管理、不进入 `QAccessibleCache`。

问题出在元素销毁：`-[QMacAccessibilityElement dealloc]` 无条件调用
`QAccessibleCache::instance()->deleteInterface(axid)`。占位元素/行/列被释放时，
它带着的 `axid` 是**父表**的 id，于是把父表的 `QAccessibleInterface` 删掉；
已经物化的真实单元格也被这样删掉，而 `QAccessibleTable` 还缓存着这些指针。下一次
AX 查询（`accessibilitySelectedChildren` → `selectedItems()`）读到已释放内存，
进程以 `EXC_BAD_ACCESS / SIGSEGV / SIGBUS` 退出。

崩溃栈与用户回传的 9 份 `.ips` 逐帧一致：主线程 `libqcocoa` 内
（`QMacAccessibilityElement`）← AppKit `-[NSAccessibilityAttributeAccessorInfo
getAttributeValue:forObject:]` ← `NSAccessibilityPerformEntryPointObject` ←
HIServices `__AXCopyAttributeValueForHierarchy`。

## 上游修复与为什么现在要自己做

上游修复（commit `8148c48deb8e4345bacb797cb75c011df6f797fa`，"macOS: Don't let
native a11y elements delete accessible interfaces"）2026-10-27 合入 `dev` 与
`6.12` 分支：不再从 native 元素销毁路径删除 interface（interface 由
`QAccessibleCache` 在 `QObject` 析构时回收，合成单元格由 `QAccessibleTable`
自己删除）。

但**目前没有任何已发布的 PySide6 含这个修复**：6.11.2 是随包版本；6.11.3 于
2026-10-26 发布（早于合入）；6.12.0 于 2026-10-08 发布；带修复的 6.12.1 计划
2026-11-17。要现在就修，只能在应用进程里把上游修复的语义做出来。

## 本模块做什么

用 `ctypes` 调 Objective-C 运行时（与 :mod:`folith.qt.macwindow`、
:mod:`folith.qt.motion` 同一套做法，打包里没有 pyobjc），把上游删掉的两个删除点
都变成空操作：

1. `-[QMacAccessibilityElement dealloc]` → 换成"先把私有 `axid` 清零，再调用原
   实现"；`deleteInterface(0)` 是空操作。
2. `+[QMacAccessibilityElement removeElementsFromCache:]` → 换成空实现；它原本会
   遍历 rows/columns/cells 数组，把里面元素的 interface 全部删掉。

两处都必须处理：只改第 1 处时，表格在 `QAccessibleTable` 里缓存的单元格 interface
仍会由第 2 处删掉，下一次模型变更（`QAccessible::updateAccessibility` →
`QAccessibleTable::modelChange`）就会读已释放内存——实测在首页 `项目筛选`
下拉框清空时崩（`QComboBox::clear` → `QStandardItemModel::removeRows`）。

修复后 AX 语义不变：`tests` 里的确定性复现器（上游 QTBUG-149612 附件的最小
复现，已改写成纯 `ctypes`）要求"查询选中子元素后父表的 interface 仍然存在"，
安装本修复后通过；真实应用在连续 AX 扫描 + 打开任务下存活（见证据日志 R64）。
**未验证**：本机卡片视图路径上 AX 从未暴露 `AXTable`/`AXRow`/`AXCell`，所以
"表格角色在修复后仍完整暴露"这一条没有实测证据，只有进程内复现器的
"父表 interface 仍可解析、选中子元素仍有 7 个"。

## 安全边界（任何一步不满足就什么都不做，绝不抛异常）

- 只在 macOS + cocoa 平台、且 Qt 版本属于**已知受影响**的范围时安装：
  6.11.x 与 6.12.0。Qt >= 6.12.1 视为已由上游修复，不安装（升级 PySide6 后
  本模块自动失效，届时可以连同调用点一起删除）。
- 安装前自检：类、`axid` ivar（偏移/类型）、`dealloc` 方法都必须存在；
  安装后回读 IMP 确认真的换上了，否则恢复原状。
- arm64e 进程上 Objective-C 方法指针带 PAC 签名，`ctypes` 无法安全调用原
  实现，直接放弃（随包 Python 是 arm64，不受影响）。
- 诊断开关：环境变量 ``FOLITH_QT_AX_WORKAROUND=0``（也接受 off/false/no）
  跳过安装，用来做 A/B 证据；**不是**给用户的"关掉辅助功能"开关（那种开关
  在 Qt 6.11 上无效，R62 已删除）。

## 删除时机

PySide6 升级到 6.12.1+ 后：本模块不再安装（版本门），确认回归用例与 AX 扫描
探针后，删除本文件与 :mod:`folith.qt.app` 里的调用点。
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import sys
import threading

#: 诊断用环境变量：设为 0/off/false/no 时不安装修复（A/B 证据、复现上游缺陷）。
WORKAROUND_ENV = "FOLITH_QT_AX_WORKAROUND"

#: 上游缺陷编号与修复提交（写进日志与文档，方便后来者核对）。
UPSTREAM_ISSUE = "QTBUG-149612"
UPSTREAM_FIX_COMMIT = "8148c48deb8e4345bacb797cb75c011df6f797fa"

_OBJC_CLASS = b"QMacAccessibilityElement"
_AXID_IVAR = b"axid"
_DEALLOC_SELECTOR = b"dealloc"
_REMOVE_CACHE_SELECTOR = b"removeElementsFromCache:"

_DISABLED_VALUES = frozenset({"0", "off", "false", "no", "disabled"})

#: `QAccessible::Id` 的 ivar 编码 → 字节宽度。Qt 6.11 里它是 ``unsigned``（编码
#: ``I``，4 字节）；将来若加宽成 64 位会是 ``Q``/``L``。只认这两种，布局变了就放弃。
_AXID_TYPE_SIZES = {b"I": 4, b"Q": 8, b"L": 8}

_lock = threading.Lock()
_state: dict = {
    "installed": False,
    "objc": None,
    "class": None,
    "method": None,
    "original": None,
    "original_address": None,
    "callback": None,
    "ivar_offset": None,
    "qt_version": "",
}
_report: dict = {}


def workaround_disabled() -> bool:
    """``FOLITH_QT_AX_WORKAROUND`` 是否显式关闭（只有诊断/A-B 时才关）。"""
    import os

    value = os.environ.get(WORKAROUND_ENV, "").strip().lower()
    return value in _DISABLED_VALUES


def _version_tuple(version: object) -> tuple[int, int, int] | None:
    """``"6.11.2"``/``"6.12.0-beta1"`` → ``(6, 11, 2)``；解析不了返回 None。"""
    parts: list[int] = []
    for chunk in str(version or "").split(".")[:3]:
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def is_affected_qt_version(version: object) -> bool:
    """Qt 版本是否落在已知受影响、且上游尚未发布修复的范围。

    受影响：6.11.x（含 6.11.3，它 2026-10-26 发布，早于 2026-10-27 的修复合入）
    与 6.12.0。6.12.1 起视为已修复；更早的 6.10 及以前没有这条清理逻辑，
    不在本模块的处置范围（也不在 0.8.1 的运行时里）。
    """
    parsed = _version_tuple(version)
    if parsed is None:
        return False
    major, minor, patch = parsed
    if major != 6:
        return False
    if minor == 11:
        return True
    return minor == 12 and patch == 0


def _process_arch() -> str:
    """当前进程的 CPU 类型（``arm64``/``arm64e``/``unknown``），用于安全自检。"""
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        get_header = libsystem._NSGetMachExecuteHeader
        get_header.restype = ctypes.c_void_p
        get_header.argtypes = []
        header = get_header()
        if not header:
            return "unknown"
        # struct mach_header { uint32 magic; cpu_type_t cputype; cpu_subtype_t cpusubtype; ... }
        cputype = ctypes.c_int32.from_address(header + 4).value
        cpusubtype = ctypes.c_int32.from_address(header + 8).value & 0x00FFFFFF
        if cputype != 0x0100000C:  # CPU_TYPE_ARM64
            return "x86_64"
        return "arm64e" if cpusubtype == 2 else "arm64"  # CPU_SUBTYPE_ARM64E == 2
    except Exception:  # noqa: BLE001 - 读不到就不把进程当成特殊架构
        return "unknown"


def _objc_runtime() -> dict:
    """加载 Objective-C 运行时并声明本模块用到的函数签名。"""
    path = ctypes.util.find_library("objc")
    if not path:
        raise OSError("libobjc not found")
    objc = ctypes.CDLL(path)
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.class_getInstanceMethod.restype = ctypes.c_void_p
    objc.class_getInstanceMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.method_getImplementation.restype = ctypes.c_void_p
    objc.method_getImplementation.argtypes = [ctypes.c_void_p]
    objc.method_setImplementation.restype = ctypes.c_void_p
    objc.method_setImplementation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.object_getClass.restype = ctypes.c_void_p
    objc.object_getClass.argtypes = [ctypes.c_void_p]
    objc.class_getClassMethod.restype = ctypes.c_void_p
    objc.class_getClassMethod.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    objc.class_getInstanceVariable.restype = ctypes.c_void_p
    objc.class_getInstanceVariable.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    objc.ivar_getOffset.restype = ctypes.c_ssize_t
    objc.ivar_getOffset.argtypes = [ctypes.c_void_p]
    objc.ivar_getTypeEncoding.restype = ctypes.c_char_p
    objc.ivar_getTypeEncoding.argtypes = [ctypes.c_void_p]
    return objc


def _dealloc_stub(self_ptr, _cmd, _state=_state):
    """替换后的 ``-dealloc``：清零 ``axid``，再调用原实现。

    **这段代码在 ObjC 的析构路径里运行，必须不抛异常、不做额外工作**：只把一个
    整数 ivar 写 0，然后把控制权交回原实现（原实现负责 `[super dealloc]`）。
    """
    offset = _state.get("ivar_offset")
    if offset is not None and self_ptr:
        try:
            address = int(self_ptr) + int(offset)
            # Qt 6.11 的 QAccessible::Id 是 32 位；只写 ivar 自己的宽度，
            # 不要碰到后面紧挨着的 m_rowIndex。
            writer = ctypes.c_uint64 if _state.get("ivar_size") == 8 else ctypes.c_uint32
            writer.from_address(address).value = 0
        except Exception:  # noqa: BLE001 - 指针异常时宁可少改，也必须继续析构
            pass
    original = _state.get("original")
    if original is not None:
        original(self_ptr, _cmd)


def _remove_cache_stub(_cls, _cmd, _array=None):
    """替换后的 ``+removeElementsFromCache:``：什么都不做。

    原实现会删掉 rows/columns/cells 数组里每个元素的 interface，而
    `QAccessibleTable` 仍缓存着这些指针；上游修复（8148c48d）把这处删除整段删掉，
    由 `QAccessibleCache` 在 QObject 析构时统一回收。
    """


def _finish_report(**fields) -> dict:
    global _report

    report = {
        "installed": bool(fields.pop("installed", False)),
        "reason": str(fields.pop("reason", "")),
        "platform": "darwin/cocoa",
        "issue": UPSTREAM_ISSUE,
    }
    report.update(fields)
    _report = report
    return report


def install(log=None) -> dict:
    """安装运行时修复；返回报告字典。任何失败都返回 ``installed=False``，绝不抛。"""
    report = _install()
    if log is not None:
        try:
            log("macOS AX workaround: "
                f"{'installed' if report.get('installed') else 'not installed'}"
                f" ({report.get('reason', '')}); Qt {report.get('qt_version', '?')};"
                f" upstream {UPSTREAM_ISSUE}")
        except Exception:  # noqa: BLE001 - 日志写不出去不影响启动
            pass
    return report


def _install() -> dict:
    with _lock:
        if _state["installed"]:
            return _finish_report(installed=True, reason="already-installed",
                                  qt_version=_state["qt_version"],
                                  ivar_offset=_state["ivar_offset"],
                                  ivar_size=_state.get("ivar_size"))
        try:
            if sys.platform != "darwin":
                return _finish_report(reason="not-macos", platform=sys.platform)
            from PySide6.QtCore import qVersion
            from PySide6.QtWidgets import QApplication

            qt_version = qVersion()
            if QApplication.platformName() != "cocoa":
                return _finish_report(reason="not-cocoa",
                                      platform=QApplication.platformName(),
                                      qt_version=qt_version)
            if workaround_disabled():
                return _finish_report(reason="disabled-by-env", qt_version=qt_version)
            if not is_affected_qt_version(qt_version):
                return _finish_report(reason="qt-version-not-affected",
                                      qt_version=qt_version)
            arch = _process_arch()
            if arch == "arm64e":
                return _finish_report(reason="unsupported-arm64e",
                                      qt_version=qt_version, arch=arch)
            objc = _objc_runtime()
            cls = objc.objc_getClass(_OBJC_CLASS)
            if not cls:
                return _finish_report(reason="class-not-found", qt_version=qt_version)
            ivar = objc.class_getInstanceVariable(cls, _AXID_IVAR)
            if not ivar:
                return _finish_report(reason="ivar-not-found", qt_version=qt_version)
            offset = int(objc.ivar_getOffset(ivar))
            encoding = objc.ivar_getTypeEncoding(ivar) or b""
            if not (0 <= offset < 512) or offset % 8:
                return _finish_report(reason="unexpected-ivar-offset",
                                      qt_version=qt_version, ivar_offset=offset)
            ivar_size = _AXID_TYPE_SIZES.get(encoding[:1])
            if not ivar_size:
                return _finish_report(reason="unexpected-ivar-type",
                                      qt_version=qt_version,
                                      ivar_offset=offset,
                                      ivar_encoding=encoding.decode("ascii", "replace"))
            selector = objc.sel_registerName(_DEALLOC_SELECTOR)
            method = objc.class_getInstanceMethod(cls, selector)
            if not method:
                return _finish_report(reason="dealloc-not-found", qt_version=qt_version)
            original = int(objc.method_getImplementation(method))
            if not original:
                return _finish_report(reason="dealloc-imp-missing", qt_version=qt_version)
            callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(
                _dealloc_stub)
            call_original = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(
                original)
            remove_selector = objc.sel_registerName(_REMOVE_CACHE_SELECTOR)
            remove_method = objc.class_getClassMethod(cls, remove_selector)
            if not remove_method:
                return _finish_report(reason="remove-cache-not-found",
                                      qt_version=qt_version)
            remove_original = int(objc.method_getImplementation(remove_method))
            if not remove_original:
                return _finish_report(reason="remove-cache-imp-missing",
                                      qt_version=qt_version)
            remove_callback = ctypes.CFUNCTYPE(
                None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(_remove_cache_stub)
            # 先把状态放好，stub 运行时才能读到 offset / 原实现。
            _state.update({
                "objc": objc,
                "class": cls,
                "method": method,
                "original": call_original,
                "original_address": original,
                "callback": callback,
                "remove_cache_method": remove_method,
                "remove_cache_original": remove_original,
                "remove_cache_callback": remove_callback,
                "ivar_offset": offset,
                "ivar_size": ivar_size,
                "qt_version": qt_version,
            })
            objc.method_setImplementation(method, ctypes.cast(callback, ctypes.c_void_p))
            installed_imp = int(objc.method_getImplementation(method) or 0)
            if installed_imp != ctypes.cast(callback, ctypes.c_void_p).value:
                # 没换上（或平台 ABI 不同）：恢复原实现，什么都不做。
                objc.method_setImplementation(method, ctypes.c_void_p(original))
                _state.update({"original": None, "original_address": None,
                               "callback": None, "ivar_offset": None})
                return _finish_report(reason="swizzle-not-applied",
                                      qt_version=qt_version, ivar_offset=offset)
            objc.method_setImplementation(remove_method,
                                          ctypes.cast(remove_callback, ctypes.c_void_p))
            remove_installed = int(objc.method_getImplementation(remove_method) or 0)
            if remove_installed != ctypes.cast(remove_callback, ctypes.c_void_p).value:
                # 第二处没换上就整体回退，不要留下半套修复。
                objc.method_setImplementation(remove_method,
                                              ctypes.c_void_p(remove_original))
                objc.method_setImplementation(method, ctypes.c_void_p(original))
                _state.update({"original": None, "original_address": None,
                               "callback": None, "remove_cache_method": None,
                               "remove_cache_original": None,
                               "remove_cache_callback": None, "ivar_offset": None})
                return _finish_report(reason="remove-cache-swizzle-not-applied",
                                      qt_version=qt_version, ivar_offset=offset)
            _state["installed"] = True
            _register_shutdown_hook()
            return _finish_report(installed=True, reason="installed",
                                  qt_version=qt_version, arch=arch,
                                  ivar_offset=offset, ivar_size=ivar_size)
        except Exception as exc:  # noqa: BLE001 - 修复失败不能影响应用启动
            return _finish_report(reason=f"error:{type(exc).__name__}:{exc}")


def _register_shutdown_hook() -> None:
    """退出前恢复原实现。

    stub 是 Python 回调；解释器收尾时它可能先被回收，而 Qt 的可访问性元素仍可能
    在之后析构。退出阶段已经没有 AX 查询，恢复原实现不会造成用户可见问题。
    """
    try:
        from PySide6.QtCore import QCoreApplication, Qt

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(uninstall, Qt.ConnectionType.DirectConnection)
    except Exception:  # noqa: BLE001
        pass
    atexit.register(uninstall)


def uninstall() -> None:
    """恢复原始 ``-dealloc``（可重复调用；未安装时是空操作）。"""
    with _lock:
        if not _state["installed"]:
            return
        try:
            objc = _state["objc"]
            method = _state["method"]
            address = _state.get("original_address")
            if objc and method and address:
                objc.method_setImplementation(method, ctypes.c_void_p(int(address)))
            remove_method = _state.get("remove_cache_method")
            remove_address = _state.get("remove_cache_original")
            if objc and remove_method and remove_address:
                objc.method_setImplementation(
                    remove_method, ctypes.c_void_p(int(remove_address)))
        except Exception:  # noqa: BLE001
            pass
        finally:
            _state["installed"] = False
            _state["original"] = None
            _state["original_address"] = None
            _state["callback"] = None
            _state["remove_cache_method"] = None
            _state["remove_cache_original"] = None
            _state["remove_cache_callback"] = None


def report() -> dict:
    """最近一次 :func:`install` 的报告（没调用过时是空字典）。"""
    return dict(_report)


def is_installed() -> bool:
    return bool(_state["installed"])
