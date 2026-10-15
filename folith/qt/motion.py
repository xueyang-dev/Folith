"""Motion preferences for the Qt client.

Preferences and interruptible transitions for the Qt client. Every animated
widget asks :func:`reduced` instead of reading settings itself.

Qt 6.11 exposes no reduce-motion hint (``QStyleHints.accessibility()`` only has
the contrast preference) and the bundle has no pyobjc, so on macOS the system
setting is read from ``NSWorkspace`` through the Objective-C runtime.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time

from PySide6.QtCore import QObject, QEasingCurve, QSettings, QVariantAnimation

DURATION = {"select": 140, "local": 180, "panel": 240, "view": 300}
EASING = {"select": QEasingCurve.Type.OutCubic,
          "local": QEasingCurve.Type.OutCubic,
          "panel": QEasingCurve.Type.InOutCubic,
          "view": QEasingCurve.Type.OutCubic}

#: Re-read the system setting at most this often (it can change while running).
_SYSTEM_TTL_S = 5.0
_cache: dict = {"at": 0.0, "value": False}


def _macos_reduce_motion() -> bool:
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/AppKit.framework/AppKit")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        send = objc.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        workspace = send(objc.objc_getClass(b"NSWorkspace"),
                         objc.sel_registerName(b"sharedWorkspace"))
        if not workspace:
            return False
        send_bool = ctypes.cast(objc.objc_msgSend,
                                ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                                 ctypes.c_void_p))
        return bool(send_bool(workspace, objc.sel_registerName(
            b"accessibilityDisplayShouldReduceMotion")))
    except Exception:  # noqa: BLE001 - an unreadable preference means "not reduced"
        return False


def _system_reduce_motion() -> bool:
    if sys.platform != "darwin":
        return False
    now = time.monotonic()
    if now - _cache["at"] > _SYSTEM_TTL_S:
        _cache["value"] = _macos_reduce_motion()
        _cache["at"] = now
    return bool(_cache["value"])


def reduced() -> bool:
    """Whether animations should jump straight to their end state.

    An explicit application preference wins over the environment override,
    scripted/offscreen runs, then the system setting. An absent preference
    follows the system. The environment override also lets probes run animations.
    """
    preference = QSettings("Folith", "QtDesktop").value("appearance/reduce_motion")
    if preference is not None:
        return str(preference).strip().lower() in ("1", "true", "yes")
    forced = os.environ.get("FOLITH_REDUCE_MOTION")
    if forced is not None and forced.strip() != "":
        return forced.strip() not in ("0", "false", "no")
    if os.environ.get("FOLITH_QT_NONINTERACTIVE") == "1" \
            or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True
    return _system_reduce_motion()


class Motion(QObject):
    """Transitions owned by a widget, with one animation per local key.

    Replacing an animation stops it at its current value. Rapid repeated input
    snaps to the latest destination rather than queuing work. Completion belongs
    to the latest request only; cancelled requests cannot restore stale layouts.
    """

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._active: dict[str, QVariantAnimation] = {}
        self._last: dict[str, float] = {}

    def stop(self, key: str) -> None:
        animation = self._active.pop(key, None)
        if animation is not None:
            animation.stop()
            animation.deleteLater()

    def running(self, key: str) -> bool:
        return key in self._active

    def animate(self, key: str, start: float, end: float, setter, *,
                kind: str = "panel", finished=None) -> None:
        self.stop(key)
        now = time.monotonic()
        rapid = (now - self._last.get(key, float("-inf"))) * 1000 < DURATION[kind]
        self._last[key] = now
        if start == end or rapid or reduced():
            setter(end)
            if finished is not None:
                finished()
            return
        animation = QVariantAnimation(self)
        animation.setDuration(DURATION[kind])
        animation.setEasingCurve(EASING[kind])
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.valueChanged.connect(setter)

        def complete() -> None:
            self._active.pop(key, None)
            animation.deleteLater()
            if finished is not None:
                finished()

        animation.finished.connect(complete)
        self._active[key] = animation
        setter(start)
        animation.start()
