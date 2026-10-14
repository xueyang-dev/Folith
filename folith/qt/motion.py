"""Motion preferences for the Qt client.

Only the *reduce motion* decision lives here for now; durations, easing and the
interruption rule are added with the motion work (docs/qt-beta/motion-audit-2026-10-14.md
§3.2).  Every animated widget asks :func:`reduced` instead of reading settings
itself.

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

    In order: ``FOLITH_REDUCE_MOTION`` (1/0), scripted runs and offscreen tests
    (deterministic: always reduced), then the system setting.
    """
    forced = os.environ.get("FOLITH_REDUCE_MOTION")
    if forced is not None and forced.strip() != "":
        return forced.strip() not in ("0", "false", "no")
    if os.environ.get("FOLITH_QT_NONINTERACTIVE") == "1" \
            or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True
    return _system_reduce_motion()
