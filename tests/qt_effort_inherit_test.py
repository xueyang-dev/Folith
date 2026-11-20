"""C-35 PR-2: inherited thinking level is shown truthfully in the third column."""
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
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.engine import effort_settings  # noqa: E402
from folith.qt import adapter, dialogs  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _config():
    return adapter.EngineConfig(
        target_lang="简体中文", provider="OpenCode Go", model="deepseek-v4", api_key="k",
        connections=[
            {"id": "gw", "name": "opencode", "provider": "OpenCode Go",
             "model": "deepseek-v4", "api_key": "k", "base_url": ""},
            {"id": "oc", "name": "opencode", "provider": "OpenCode Go",
             "model": "deepseek-v4", "api_key": "k", "base_url": ""},
        ],
        connection_roles={"translation": "gw", "reviewer": "", "vision": ""},
        connection_models={"translation": "deepseek-v4"},
        connection_levels={"translation": "off"})


def _panel(app):
    dlg = dialogs.EngineDialog(_config(), TaskRunner())
    dlg.show()
    dlg.show_page("models")
    app.processEvents()
    return dlg


def test_inherited_row_shows_actual_level_with_marker(app):
    dlg = _panel(app)
    try:
        lib = dlg.connection_library
        box = lib.level_selectors["reviewer"]
        assert box.currentData() == "off"
        assert "沿用翻译" in box.currentText()
        assert effort_settings.to_job_fields(
            dlg.config.connection_levels, "reviewer", "OpenCode Go", "deepseek-v4")[0] == ""
        assert dlg.config.connection_levels.get("reviewer", "") == ""
    finally:
        dlg.close()


def test_individual_override_wins(app):
    dlg = _panel(app)
    try:
        box = dlg.connection_library.level_selectors["reviewer"]
        index = box.findData("on")
        assert index >= 0
        box.setCurrentIndex(index)
        assert dlg.config.connection_levels["reviewer"] == "on"
        assert effort_settings.to_job_fields(
            dlg.config.connection_levels, "reviewer", "OpenCode Go", "deepseek-v4") == (
                "", "provider_default")
    finally:
        dlg.close()


def test_third_column_is_aligned_for_all_three_rows(app):
    dlg = _panel(app)
    try:
        lib = dlg.connection_library
        xs = [lib.level_selectors[role].mapToGlobal(
            lib.level_selectors[role].rect().topLeft()).x()
              for role in ("translation", "reviewer", "vision")]
        assert len(set(xs)) == 1, xs
        # the inherited model selector keeps its column-2 place
        assert not lib.model_selectors["reviewer"].isHidden()
        assert lib.model_selectors["reviewer"].currentText() == "沿用翻译模型"
    finally:
        dlg.close()
