"""Small platform boundary for the shared Qt pages.

Qt owns native dialogs, menus and window decorations. Actions and their hints
use the same key sequences here; translated document content never passes
through platform word replacement.
"""
from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence


def is_macos() -> bool:
    return sys.platform == "darwin"


def system_name() -> str:
    if is_macos():
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    return platform.system()


def system_version() -> str:
    if is_macos():
        return platform.mac_ver()[0] or platform.release()
    if sys.platform == "win32":
        return platform.win32_ver()[1] or platform.release()
    return platform.release()


def file_manager_name() -> str:
    from . import i18n

    if is_macos():
        return "Finder"
    return i18n.tr("资源管理器" if sys.platform == "win32" else "文件管理器")


def device_name() -> str:
    from . import i18n

    return "Mac" if is_macos() else i18n.tr("电脑")


def settings_label() -> str:
    from . import i18n

    return i18n.tr("偏好设置…" if is_macos() else "设置…")


_STANDARD = {
    "open": QKeySequence.StandardKey.Open,
    "save": QKeySequence.StandardKey.Save,
    "close": QKeySequence.StandardKey.Close,
    "quit": QKeySequence.StandardKey.Quit,
    "settings": QKeySequence.StandardKey.Preferences,
}
# Qt interprets Ctrl as Command on macOS, including custom application actions.
_CUSTOM = {
    "settings": "Ctrl+,",  # Qt has no Preferences binding on some platforms.
    "home": "Ctrl+Shift+H",
    "search": "Ctrl+K",
    "start": "Ctrl+R",
    "reload": "Ctrl+Shift+R",
    "save_next": "Ctrl+Return",
    "source": "Ctrl+Alt+P",
    "concordance": "F3",
    "escape": "Esc",
    "previous_segment": "Up",
    "next_segment": "Down",
}


def shortcut(action: str) -> QKeySequence:
    if action in _STANDARD:
        sequence = QKeySequence(_STANDARD[action])
        if not sequence.isEmpty():
            return sequence
    return QKeySequence(_CUSTOM[action]) if action in _CUSTOM else QKeySequence()


def shortcut_text(action: str) -> str:
    return shortcut(action).toString(QKeySequence.SequenceFormat.NativeText)


def modifier_text(modifier: Qt.KeyboardModifier) -> str:
    # Let Qt render its native modifier names, including macOS glyphs.
    return QKeySequence(modifier.value).toString(
        QKeySequence.SequenceFormat.NativeText).rstrip("+")


def configure_title_bar(window, sync_safe_area) -> None:
    if is_macos():
        from .macwindow import configure_title_bar as configure

        configure(window, sync_safe_area)
