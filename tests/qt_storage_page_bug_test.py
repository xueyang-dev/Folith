"""C-35 U-3: storage page path elision, calculating state, platform_ui label."""
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

from folith.qt import platform_ui, settings_pages, style  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_long_paths_are_elided_with_full_text_in_tooltip(app):
    page = settings_pages.StoragePage(None)
    long_path = "/very/long/" + "segment/" * 20 + "data"
    page.path_label.setText(long_path)
    assert page.path_label.toolTip() == long_path
    assert isinstance(page.path_label, style.ElideLabel)
    assert len(page.cleanup_path_labels) == 3
    for label in page.cleanup_path_labels:
        assert isinstance(label, style.ElideLabel)
        assert label.toolTip() == label.fullText()
        assert label.toolTip() in page.cleanup_path_labels[0].toolTip() or label.text()


def test_usage_values_show_a_calculating_state(app):
    page = settings_pages.StoragePage(None)
    assert all("计算中" in label.text() for label in page.usage_values.values())
    page.apply({"data_dir": "/tmp/data", "log_dir": "/tmp/log",
                "jobs": 0, "projects": 0, "caches": 0, "logs": 0, "total": 0,
                "job_count": 0})
    assert all("0 B" in label.text() for label in page.usage_values.values())


def test_reveal_label_comes_from_platform_ui(app):
    page = settings_pages.StoragePage(None)
    assert platform_ui.file_manager_name() in page.reveal_button.text()
    assert "在 " in page.reveal_button.text() and " 中显示" in page.reveal_button.text()


def test_all_three_cleanup_paths_survive_apply(app):
    from folith.qt.settings_pages import cleanup_paths

    page = settings_pages.StoragePage(None)
    data = "/long/" + "path/" * 30
    log = "/logs/" + "deep/" * 30
    view = {"data_dir": data, "log_dir": log, "jobs": 0, "projects": 0,
            "caches": 0, "logs": 0, "total": 0, "job_count": 0}
    page.apply(view)
    expected = cleanup_paths(data, log)
    assert len(page.cleanup_path_labels) == len(expected) == 3
    for label, full in zip(page.cleanup_path_labels, expected):
        assert label.isVisible() or not label.isHidden()
        assert label.toolTip() == full


def test_long_paths_use_middle_elision(app):
    from PySide6.QtCore import Qt

    page = settings_pages.StoragePage(None)
    long_path = "/Volumes/" + "very-long-folder-" * 20 + "/Folith/data"
    assert page.path_label._mode == Qt.TextElideMode.ElideMiddle
    assert all(label._mode == Qt.TextElideMode.ElideMiddle
               for label in page.cleanup_path_labels)
    page.path_label.setText(long_path)
    page.path_label.resize(400, 20)
    app.processEvents()
    shown = page.path_label.text()
    assert "…" in shown, shown
    assert shown.endswith("Folith/data"), shown
    assert shown.startswith("/Volumes/"), shown
