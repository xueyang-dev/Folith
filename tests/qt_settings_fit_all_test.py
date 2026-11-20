"""C-35: every settings page and the connection dialog stay inside the screen."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, dialog_fit, dialogs  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _Screen:
    def __init__(self, rect=QRect(0, 0, 1280, 720)):
        self.rect = rect

    def availableGeometry(self):  # noqa: N802 - Qt naming
        return self.rect


def _config():
    return adapter.EngineConfig(target_lang="简体中文", provider="OpenAI", model="m", api_key="k")


@pytest.mark.parametrize("page", ("general", "models", "defaults", "reading",
                                  "storage", "about", "vision"))
def test_all_settings_pages_fit_a_short_screen(app, monkeypatch, page):
    dlg = dialogs.EngineDialog(_config(), TaskRunner())
    monkeypatch.setattr(dlg, "screen", lambda: _Screen())
    try:
        dlg.show_page(page)
        app.processEvents()
        frame = dlg.frameGeometry()
        area = _Screen().rect
        assert area.contains(frame), (page, frame, area)
    finally:
        dlg.close()


def test_connection_editor_dialog_fits_and_is_centred(app, monkeypatch):
    from folith.qt.connection_library import ConnectionLibrary

    dlg = dialogs.EngineDialog(_config(), TaskRunner())
    lib = ConnectionLibrary(dlg)
    editor = lib.editor
    monkeypatch.setattr(editor, "screen", lambda: _Screen())
    editor.show()
    app.processEvents()
    area = _Screen().rect
    assert area.contains(editor.frameGeometry()), editor.frameGeometry()
    # centred over the settings dialog when it fits
    assert abs(editor.frameGeometry().center().x() - area.center().x()) <= 2
    editor.close()
    dlg.close()


def test_footer_is_a_single_row(app):
    dlg = dialogs.EngineDialog(_config(), TaskRunner())
    try:
        footer = dlg.findChild(object, "settingsFooter")
        assert footer is not None
        assert dlg._status.parent() is footer
        assert dlg._pinned_actions.parent() is footer
        assert dlg._status.geometry().bottom() == dlg._pinned_actions.geometry().bottom() or \
            abs(dlg._status.geometry().center().y()
                - dlg._pinned_actions.geometry().center().y()) <= 20
    finally:
        dlg.close()


def test_fit_helper_caps_frame_and_cleans_up(app, monkeypatch):
    from PySide6.QtWidgets import QDialog

    widget = QDialog()
    monkeypatch.setattr(widget, "screen", lambda: _Screen())
    widget.resize(900, 1400)
    widget.show()
    app.processEvents()
    dialog_fit.fit_to_screen(widget, recenter=True)
    frame = widget.frameGeometry()
    assert frame.height() < 720
    assert _Screen().rect.contains(frame)
    widget.close()
