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

from PySide6.QtCore import QObject, QEasingCurve, QVariantAnimation, Qt
from PySide6.QtWidgets import QGraphicsEffect

DURATION = {"select": 140, "local": 180, "panel": 240, "view": 300,
            "body": 250, "source": 280, "progress": 250, "popover": 200, "popover-close": 150, "save": 400}
EASING = {"select": QEasingCurve.Type.OutCubic,
          "local": QEasingCurve.Type.OutCubic,
          "panel": QEasingCurve.Type.InOutCubic,
          "view": QEasingCurve.Type.OutCubic,
          "body": QEasingCurve.Type.OutCubic,
          "source": QEasingCurve.Type.InOutCubic,
          "progress": QEasingCurve.Type.OutCubic,
          "popover": QEasingCurve.Type.OutCubic,
          "popover-close": QEasingCurve.Type.OutCubic,
          "save": QEasingCurve.Type.OutCubic}

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

    In order: ``FOLITH_REDUCE_MOTION`` (1/0; also lets probes run animations),
    scripted and offscreen runs (deterministic: always reduced), then the macOS
    setting.  An in-app switch is added with the settings window (P5); until a
    screen can write it, no stored preference is consulted -- otherwise tests
    and scripts would silently depend on the developer's real QSettings.
    """
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

    def reveal(self, key: str, widget, *, kind: str = "view") -> None:
        """Reveal a view at its final layout; do not move its input geometry."""
        effect = widget.graphicsEffect()
        if not isinstance(effect, RevealEffect):
            effect = RevealEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setEnabled(True)
        self.animate(key, 0.0, 1.0, effect.set_fraction, kind=kind,
                     finished=lambda: _remove_effect(widget, effect))

    def nod(self, key: str, widget) -> None:
        effect = widget.graphicsEffect()
        if not isinstance(effect, NodEffect):
            effect = NodEffect(widget)
            widget.setGraphicsEffect(effect)
        effect.setEnabled(True)
        self.animate(key, 0.0, 1.0, effect.set_fraction, kind="popover",
                     finished=lambda: _remove_effect(widget, effect))


def _remove_effect(widget, effect) -> None:
    """A finished transition leaves nothing behind: a disabled effect that stays
    installed still routes the widget's painting through the effect machinery,
    and nests with the cards' own drop shadows."""
    if widget.graphicsEffect() is effect:
        widget.setGraphicsEffect(None)  # Qt deletes the effect


class RevealEffect(QGraphicsEffect):
    """One clipped 8 px entrance, with no per-frame text re-layout."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.fraction = 1.0

    def set_fraction(self, value: float) -> None:
        self.fraction = value
        self.update()

    def draw(self, painter) -> None:
        painter.save()
        painter.setOpacity(self.fraction)
        rtl = self.parent().layoutDirection() == Qt.LayoutDirection.RightToLeft
        painter.translate((-1 if rtl else 1) * 8 * (1 - self.fraction), 0)
        self.drawSource(painter)
        painter.restore()


class NodEffect(RevealEffect):
    def draw(self, painter) -> None:
        painter.save()
        center = self.parent().rect().center()
        scale = 1 - .06 * (1 - (2 * self.fraction - 1) ** 2)
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        self.drawSource(painter)
        painter.restore()
