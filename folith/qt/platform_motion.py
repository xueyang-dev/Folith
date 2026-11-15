"""OS reads for reduce-motion. Qt 6.11 has no cross-platform hint for it.

macOS comes from ``NSWorkspace`` through the Objective-C runtime (the bundle
has no pyobjc). Windows comes from ``SystemParametersInfoW`` with
``SPI_GETCLIENTAREAANIMATION``: the BOOL is TRUE when client-area animations
are enabled, so reduce-motion is the inverse. Any other platform, or a failed
read, means "not reduced" — the same fail-open the macOS reader already used.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import sys

# winuser.h. GET writes a BOOL through pvParam; that BOOL is a 4-byte int.
SPI_GETCLIENTAREAANIMATION = 0x1042


def system_reduce_motion() -> bool:
    """Whether the OS asks apps to skip animation."""
    if sys.platform == "win32":
        enabled = _windows_client_area_animation_enabled()
        if enabled is None:
            return False
        return not enabled
    if sys.platform == "darwin":
        return _macos_accessibility(b"accessibilityDisplayShouldReduceMotion")
    return False


def system_reduce_transparency() -> bool:
    """macOS "reduce transparency". Other platforms have no equivalent here."""
    if sys.platform != "darwin":
        return False
    return _macos_accessibility(b"accessibilityDisplayShouldReduceTransparency")


def _windows_client_area_animation_enabled(user32=None) -> bool | None:
    """TRUE from the OS means animations are on. None means the read failed.

    ``user32`` is the WinDLL (or a test double). Production loads ``user32``.
    The flag is a BOOL, which is four bytes; ``c_bool`` is one byte and would
    let the real call write past the buffer.
    """
    try:
        library = user32 if user32 is not None else ctypes.WinDLL(
            "user32", use_last_error=True)
        getter = library.SystemParametersInfoW
        getter.argtypes = [
            ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        getter.restype = ctypes.c_int
        enabled = ctypes.c_int(0)
        ok = getter(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0)
        if not ok:
            return None
        return bool(enabled.value)
    except Exception:  # noqa: BLE001 - an unreadable preference is "not reduced"
        return None


def _macos_accessibility(selector: bytes) -> bool:
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
        return bool(send_bool(workspace, objc.sel_registerName(selector)))
    except Exception:  # noqa: BLE001 - an unreadable preference means "not reduced"
        return False
