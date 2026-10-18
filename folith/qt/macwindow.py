"""macOS 原生窗口的两处小调整（Qt 没有对应的 API）。

只用 ``ctypes`` 调 Objective-C 运行时（打包里没有 pyobjc，见 :mod:`folith.qt.motion`）。
任何一步失败都静默放弃：窗口照常可用，只是少了这点行为。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


def _objc():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    return objc


def _send(objc, receiver, selector: bytes, restype=ctypes.c_void_p, *args, argtypes=()):
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
        ("objc_msgSend", objc))
    return function(receiver, objc.sel_registerName(selector), *args)


def ns_window(widget: QWidget):
    """``widget`` 所在顶层窗口的 NSWindow 指针；不是 cocoa 平台或还没有原生窗口时为 None。"""
    if sys.platform != "darwin" or QApplication.platformName() != "cocoa":
        return None
    try:
        view = int(widget.window().winId())
        objc = _objc()
        window = _send(objc, view, b"window")
        return (objc, window) if window else None
    except Exception:  # noqa: BLE001 - 取不到就不调整
        return None


def hide_when_app_inactive(widget: QWidget) -> bool:
    """应用切到后台时把这个窗口一起藏起来、回到前台再出现（NSPanel 的默认行为）。

    设置窗口用 ``WindowStaysOnTopHint`` 浮在主窗口上面；有了这一条，它只在 Folith
    自己的窗口之上，不会盖住别的应用。
    """
    handle = ns_window(widget)
    if handle is None:
        return False
    objc, window = handle
    try:
        _send(objc, window, b"setHidesOnDeactivate:", None, True, argtypes=(ctypes.c_bool,))
        return bool(_send(objc, window, b"hidesOnDeactivate", ctypes.c_bool))
    except Exception:  # noqa: BLE001
        return False


def window_level(widget: QWidget) -> int | None:
    handle = ns_window(widget)
    if handle is None:
        return None
    objc, window = handle
    return int(_send(objc, window, b"level", ctypes.c_long))


def configure_title_bar(window, sync_safe_area) -> None:
    if QApplication.platformName() != "cocoa":
        return
    import warnings

    expanded = getattr(Qt.WindowType, "ExpandedClientAreaHint", None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if expanded is None:
            expanded = Qt.WindowType.MaximizeUsingFullscreenGeometryHint
        window.setWindowFlag(expanded, True)
        window.setWindowFlag(Qt.WindowType.NoTitleBarBackgroundHint, True)
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
    handle = window.windowHandle()
    if handle is None:
        window.winId()
        handle = window.windowHandle()
    if handle is not None:
        handle.safeAreaMarginsChanged.connect(lambda *_: sync_safe_area())
    sync_safe_area()
