"""Ctrl+Alt+<letter> shortcuts must survive a focused text widget (#276).

On Windows ``Ctrl+Alt+<letter>`` is the AltGr modifier. A focused text editor
(a ``QPlainTextEdit`` or ``QLineEdit``) claims that key through
``QEvent.ShortcutOverride`` and inserts a character, so the window-level
``QAction`` that owns the shortcut never fires — the CAT workbench looked dead
for ``Ctrl+Alt+P`` while the toolbar button worked.

Qt's own simulator cannot reproduce AltGr offscreen on macOS/Linux (there the
same ``QTest`` key press reaches the action), so the tests model the Windows
claim explicitly with a text widget that accepts the override exactly as the
Windows platform plugin does. The helper under test is the fix:
``platform_ui.install_shortcut_guard`` blocks that override for the actions we
own (and also copes with Windows reporting AltGr as ``GroupSwitchModifier``).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from folith.qt import i18n, platform_ui, views

CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
GROUP = Qt.KeyboardModifier.GroupSwitchModifier


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def language():
    old = i18n.language()
    i18n.set_language("zh-CN")
    yield
    i18n.set_language(old)


class _AltGrEdit(QPlainTextEdit):
    """A text editor that claims Ctrl+Alt+P like Windows' AltGr input does."""

    def event(self, event):  # noqa: N802 - Qt naming
        if (event.type() == QEvent.Type.ShortcutOverride
                and event.key() == Qt.Key.Key_P
                and (event.modifiers() & GROUP
                     or (event.modifiers() & CTRL and event.modifiers() & ALT))):
            event.accept()  # "I'll handle this as text", so the shortcut is skipped
            return True
        return super().event(event)


def _bind_action(window, sequence="Ctrl+Alt+P"):
    calls = []
    action = QAction("原页", window)
    action.setShortcut(QKeySequence(sequence))
    action.triggered.connect(lambda: calls.append(True))
    window.addAction(action)
    return action, calls


def _window_with_edit(app, edit):
    window = QMainWindow()
    central = QWidget()
    layout = QVBoxLayout(central)
    window.setCentralWidget(central)
    layout.addWidget(edit)
    window.show()
    edit.setFocus()
    app.processEvents()
    return window


def test_an_altgr_text_widget_would_otherwise_swallow_the_shortcut(app):
    edit = _AltGrEdit()
    window = _window_with_edit(app, edit)
    _action, calls = _bind_action(window)

    QTest.keyClick(edit, Qt.Key.Key_P, CTRL | ALT)
    app.processEvents()
    assert calls == [], "sanity: without the guard AltGr text input wins, as on Windows"
    assert edit.toPlainText()  # a character was inserted instead of the shortcut firing
    window.close()


def test_shortcut_guard_lets_the_action_fire_and_suppresses_the_character(app):
    edit = _AltGrEdit()
    window = _window_with_edit(app, edit)
    action, calls = _bind_action(window)
    platform_ui.install_shortcut_guard(edit, (action,))

    QTest.keyClick(edit, Qt.Key.Key_P, CTRL | ALT)
    app.processEvents()
    assert calls == [True], "the window-level action must fire instead of typing a character"
    assert edit.toPlainText() == "", "the AltGr character must not be inserted"
    window.close()


def test_shortcut_guard_handles_windows_altgr_group_switch(app):
    """Right Alt (AltGr) arrives as GroupSwitchModifier; the action must still fire."""
    edit = _AltGrEdit()
    window = _window_with_edit(app, edit)
    action, calls = _bind_action(window)
    platform_ui.install_shortcut_guard(edit, (action,))

    QTest.keyClick(edit, Qt.Key.Key_P, CTRL | GROUP)
    app.processEvents()
    assert calls == [True]
    assert edit.toPlainText() == ""
    window.close()


def test_shortcut_guard_only_claims_its_own_actions(app):
    widget = QLineEdit()
    source, source_calls = _bind_action(widget, "Ctrl+Alt+P")
    outline, outline_calls = _bind_action(widget, "Ctrl+Alt+O")
    guard = platform_ui.install_shortcut_guard(widget, (source, outline))
    assert guard is not None

    def override(key, modifiers):
        event = QKeyEvent(QEvent.Type.ShortcutOverride, key, modifiers)
        return guard.eventFilter(widget, event), event

    swallowed, event = override(Qt.Key.Key_P, CTRL | ALT)
    assert swallowed is True and not event.isAccepted()
    swallowed, event = override(Qt.Key.Key_O, CTRL | ALT)
    assert swallowed is True and not event.isAccepted()
    # Another Ctrl+Alt letter, and a shifted variant, stay with the widget.
    assert override(Qt.Key.Key_Q, CTRL | ALT)[0] is False
    assert override(Qt.Key.Key_P, CTRL | ALT | Qt.KeyboardModifier.ShiftModifier)[0] is False
    assert source_calls == [] and outline_calls == []
    widget.deleteLater()


def test_shortcut_guard_is_a_noop_without_actions(app):
    widget = QLineEdit()
    assert platform_ui.install_shortcut_guard(widget, ()) is None
    assert platform_ui.install_shortcut_guard(widget, None) is None
    widget.deleteLater()


def test_workbench_guards_every_text_input_it_owns(app):
    bench = views.WorkbenchView()
    guards = {
        "target_edit": bench.target_edit,
        "source_view": bench.inline_editor.source_view,
        "segment_list": bench.segment_list,
        "search_edit": bench.search_edit,
        "assist_input": bench.assist_input,
        "page_box": bench.pdf_panel.page_box,
    }
    for name, widget in guards.items():
        assert hasattr(widget, "_shortcut_guard"), f"{name} has no shortcut guard"
    actions = bench._source_shortcut_actions
    assert bench.source_shortcut in actions
    assert bench.outline_shortcut in actions
    assert bench.source_shortcut.shortcut() == platform_ui.shortcut("source")
    bench.deleteLater()


def test_workbench_ctrl_alt_p_toggles_the_source_panel(app):
    bench = views.WorkbenchView()
    # A source is "available" so the toggle really opens the left column; the
    # point here is the key press reaching the QAction, not the PDF pipeline.
    bench._source_available = True
    bench.resize(1200, 800)
    bench.show()
    bench.segment_list.setFocus()
    app.processEvents()
    assert bench.source_open() is False

    QTest.keyClick(bench.segment_list, Qt.Key.Key_P, CTRL | ALT)
    app.processEvents()
    assert bench.source_open() is True

    QTest.keyClick(bench.segment_list, Qt.Key.Key_P, CTRL | ALT)
    app.processEvents()
    assert bench.source_open() is False
    bench.close()
    bench.deleteLater()
