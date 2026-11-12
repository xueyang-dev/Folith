"""Small platform boundary for the shared Qt pages.

Qt owns native dialogs, menus and window decorations. Actions and their hints
use the same key sequences here; translated document content never passes
through platform word replacement.
"""
from __future__ import annotations

import platform
import sys

from PySide6.QtCore import QEvent, QKeyCombination, QObject, Qt
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
    "outline": "Ctrl+Alt+O",
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


def install_shortcut_guard(widget, actions) -> QObject | None:
    """Let a window-level action shortcut win over a focused text widget.

    On Windows ``Ctrl+Alt+<letter>`` *is* AltGr: a text editor that has focus
    claims the key through ``QEvent.ShortcutOverride`` and inserts a character,
    so the ``QAction`` that owns the shortcut never fires. macOS is unaffected
    because Command suppresses text insertion, which is why the same binding
    works there (#276).

    The guard ignores that override for our own actions so Qt first tries the
    shortcut map, and — when Windows reports AltGr as ``GroupSwitchModifier``,
    which can't match a ``Ctrl+Alt`` sequence — triggers the action itself on the
    resulting key press. Callers pass the very ``QAction`` objects that hold the
    bindings, so the binding and the visible hint still come from one definition.
    """
    if actions is None:
        return None
    if hasattr(actions, "shortcuts"):  # a single QAction
        actions = (actions,)
    entries = [(sequence, action) for action in actions
               for sequence in (action.shortcuts() or [action.shortcut()])
               if isinstance(sequence, QKeySequence) and not sequence.isEmpty()]
    if widget is None or not entries:
        return None

    class _ShortcutGuard(QObject):
        def _action_for(self, event):
            pressed = QKeySequence(event.keyCombination())
            modifiers = event.modifiers()
            for sequence, action in entries:
                if not action.isEnabled():
                    continue
                if pressed == sequence:
                    return action
                # Windows' AltGr (right Alt) is reported as GroupSwitchModifier,
                # which Qt drops from QKeySequence (Ctrl+Alt+P becomes Ctrl+P).
                # Drop GroupSwitch and restore Alt so the same physical chord
                # still matches our Ctrl+Alt binding.
                if modifiers & Qt.KeyboardModifier.GroupSwitchModifier:
                    alt_modifiers = ((modifiers & ~Qt.KeyboardModifier.GroupSwitchModifier)
                                     | Qt.KeyboardModifier.AltModifier)
                    alt = QKeySequence(QKeyCombination(alt_modifiers, Qt.Key(event.key())))
                    if alt == sequence:
                        return action
            return None

        def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
            if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
                return False
            action = self._action_for(event)
            if action is None:
                return False
            if event.type() == QEvent.Type.ShortcutOverride:
                # 不接受这次 override：文本框不把它当输入，Qt 先走快捷键分发。
                event.ignore()
                return True
            # GroupSwitch/AltGr 时快捷键表匹配不到同一条 QKeySequence，这里直接
            # 触发动作，保证与正常 Ctrl+Alt 行为一致（且不插入 AltGr 字符）。
            action.trigger()
            event.accept()
            return True

    guard = _ShortcutGuard(widget)
    widget.installEventFilter(guard)
    # 事件过滤器在 Qt 里按指针调用：把 Python 包装保活到控件上，否则会被回收。
    widget._shortcut_guard = guard
    return guard


def modifier_text(modifier: Qt.KeyboardModifier) -> str:
    # Let Qt render its native modifier names, including macOS glyphs.
    return QKeySequence(modifier.value).toString(
        QKeySequence.SequenceFormat.NativeText).rstrip("+")


def configure_title_bar(window, sync_safe_area) -> None:
    if is_macos():
        from .macwindow import configure_title_bar as configure

        configure(window, sync_safe_area)
