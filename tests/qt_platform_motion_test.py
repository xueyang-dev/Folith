"""Platform boundary for reduce-motion.

Injected Win32 and Objective-C doubles cover both branches on either host.
They are unit tests. A mock is not a real-machine acceptance: the macOS test
below reads this machine's NSWorkspace, and the Windows toggle runs only when
FOLITH_WINDOWS_MOTION_PROBE=1 on a real win32 process.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

import pytest

from folith.qt import platform_motion


def _user32(value: int, *, ok: int = 1, error: BaseException | None = None):
    def system_parameters(action, _param, pv, _ini):
        system_parameters.actions.append(action)
        if error is not None:
            raise error
        ctypes.cast(pv, ctypes.POINTER(ctypes.c_int))[0] = value
        return ok

    system_parameters.actions = []
    library = type("User32", (), {})()
    library.SystemParametersInfoW = system_parameters
    return library


def test_windows_reader_parses_bool_and_ignores_a_failed_call():
    off = _user32(0)
    assert platform_motion._windows_client_area_animation_enabled(off) is False
    assert off.SystemParametersInfoW.actions == [
        platform_motion.SPI_GETCLIENTAREAANIMATION]
    assert platform_motion._windows_client_area_animation_enabled(_user32(1)) is True
    # A failed call must not treat the leftover buffer as "animations on".
    failed = _user32(1, ok=0)
    assert platform_motion._windows_client_area_animation_enabled(failed) is None
    broken = _user32(1, error=OSError("user32"))
    assert platform_motion._windows_client_area_animation_enabled(broken) is None


def test_windows_public_reader_inverts_the_enabled_flag(monkeypatch):
    monkeypatch.setattr(platform_motion.sys, "platform", "win32")
    monkeypatch.setattr(
        platform_motion, "_windows_client_area_animation_enabled",
        lambda user32=None: False)
    assert platform_motion.system_reduce_motion() is True
    monkeypatch.setattr(
        platform_motion, "_windows_client_area_animation_enabled",
        lambda user32=None: True)
    assert platform_motion.system_reduce_motion() is False
    monkeypatch.setattr(
        platform_motion, "_windows_client_area_animation_enabled",
        lambda user32=None: None)
    assert platform_motion.system_reduce_motion() is False


def test_darwin_public_reader_uses_the_motion_selector(monkeypatch):
    seen = []

    def read(selector: bytes) -> bool:
        seen.append(selector)
        return selector == b"accessibilityDisplayShouldReduceMotion"

    monkeypatch.setattr(platform_motion.sys, "platform", "darwin")
    monkeypatch.setattr(platform_motion, "_macos_accessibility", read)
    assert platform_motion.system_reduce_motion() is True
    assert seen == [b"accessibilityDisplayShouldReduceMotion"]
    assert platform_motion.system_reduce_transparency() is False
    assert seen[-1] == b"accessibilityDisplayShouldReduceTransparency"


def test_other_platforms_do_not_reduce(monkeypatch):
    calls = []
    monkeypatch.setattr(platform_motion.sys, "platform", "linux")
    monkeypatch.setattr(
        platform_motion, "_windows_client_area_animation_enabled",
        lambda user32=None: calls.append("win") or False)
    monkeypatch.setattr(
        platform_motion, "_macos_accessibility",
        lambda selector: calls.append(selector) or True)
    assert platform_motion.system_reduce_motion() is False
    assert platform_motion.system_reduce_transparency() is False
    assert calls == []


def test_motion_module_does_not_call_platform_apis():
    from folith.qt import motion
    text = open(motion.__file__, encoding="utf-8").read()
    assert "import ctypes" not in text
    assert "objc_msgSend" not in text
    assert "SystemParametersInfoW" not in text
    assert "platform_motion.system_reduce_motion" in text
    assert "platform_motion.system_reduce_transparency" in text


@pytest.mark.skipif(sys.platform != "darwin", reason="needs the real NSWorkspace")
def test_live_macos_reduce_motion_matches_universal_access():
    """Read this Mac. Not a toggle, and not a substitute for the Windows probe."""
    observed = platform_motion.system_reduce_motion()
    assert isinstance(observed, bool)
    completed = subprocess.run(
        ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
        check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        # Absent key is the system default: motion is not reduced.
        assert "Could not find" in completed.stderr
        stored = "0"
    else:
        stored = completed.stdout.strip()
    assert stored in {"0", "1"}
    assert observed is (stored == "1")


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("FOLITH_WINDOWS_MOTION_PROBE") != "1",
    reason="toggles the real Windows animation flag; CI sets the env on windows-latest",
)
def test_windows_client_area_animation_toggle_round_trip():
    """Turn client-area animation off and on through the real SPI, then restore.

    This is the OS, not a mock. It does not open the Settings app, and it does
    not write the user profile (no SPIF_UPDATEINIFILE). A mock cannot stand in
    for this result.
    """
    SPI_SETCLIENTAREAANIMATION = 0x1043
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    call = user32.SystemParametersInfoW
    call.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    call.restype = ctypes.c_int

    def current() -> bool:
        enabled = platform_motion._windows_client_area_animation_enabled(user32)
        assert enabled is not None, "SPI_GETCLIENTAREAANIMATION failed"
        return enabled

    def set_enabled(enabled: bool) -> None:
        ok = call(SPI_SETCLIENTAREAANIMATION, 0,
                  ctypes.c_void_p(1 if enabled else 0), 0)
        assert ok, f"SPI_SETCLIENTAREAANIMATION({enabled}) failed"

    original = current()
    try:
        set_enabled(False)
        assert current() is False
        assert platform_motion.system_reduce_motion() is True
        set_enabled(True)
        assert current() is True
        assert platform_motion.system_reduce_motion() is False
    finally:
        set_enabled(original)
        assert current() is original
