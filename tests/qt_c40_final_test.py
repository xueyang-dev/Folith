"""C-40: directory reveal and inherited service_default marker."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import adapter, dialogs, reveal_path  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_directory_uses_open_directory_file_still_reveals(monkeypatch, tmp_path):
    calls = []

    class Result:
        ok = True
        detail = ""

    monkeypatch.setattr(adapter.file_actions, "open_directory",
                        lambda p: calls.append(("dir", str(p))) or Result())
    monkeypatch.setattr(adapter, "reveal_in_finder",
                        lambda p: calls.append(("file", str(p))) or {"ok": True, "detail": ""})
    folder = tmp_path / "data"; folder.mkdir()
    file = tmp_path / "a.docx"; file.write_bytes(b"x")
    assert reveal_path.reveal_path_or_directory(folder)["ok"]
    assert reveal_path.reveal_path_or_directory(file)["ok"]
    assert calls == [("dir", str(folder)), ("file", str(file))]


def _panel(app, levels):
    cfg = adapter.EngineConfig(
        target_lang="简体中文", provider="OpenCode Go", model="deepseek-v4", api_key="k",
        connections=[{"id": "gw", "name": "opencode", "provider": "OpenCode Go",
                      "model": "deepseek-v4", "api_key": "k", "base_url": ""}],
        connection_roles={"translation": "gw", "reviewer": "", "vision": ""},
        connection_models={"translation": "deepseek-v4"},
        connection_levels=levels)
    dlg = dialogs.EngineDialog(cfg, TaskRunner())
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    return dlg


def test_inherited_service_default_is_marked(app):
    dlg = _panel(app, {"translation": ""})
    try:
        for role in ("reviewer", "vision"):
            box = dlg.connection_library.level_selectors[role]
            assert box.currentData() == "service_default"
            assert "沿用翻译" in box.currentText()
    finally:
        dlg.close()


def test_inherited_off_is_marked_and_override_is_not(app):
    dlg = _panel(app, {"translation": "off"})
    try:
        box = dlg.connection_library.level_selectors["reviewer"]
        assert box.currentData() == "off" and "沿用翻译" in box.currentText()
        index = box.findData("on")
        box.setCurrentIndex(index)
        assert "沿用翻译" not in box.currentText()
    finally:
        dlg.close()
