"""任务库的「拖入文档」：拖进来先说能不能放，放下后在虚线格里给结果。"""
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

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDragEnterEvent, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import style, views  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


@pytest.fixture()
def home(app):
    view = views.HomeView()
    view.resize(1200, 800)
    yield view
    view.deleteLater()


def _mime(keep: list, *names: str) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(f"/tmp/{name}") for name in names])
    keep.append(mime)          # 事件不持有 mime：测试里要自己留着
    return mime


def _enter(home, keep, *names):
    event = QDragEnterEvent(home.rect().center(), Qt.DropAction.CopyAction,
                            _mime(keep, *names), Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
    home.dragEnterEvent(event)
    return event


def test_drag_over_says_ready_for_supported_and_reject_for_unsupported(home):
    keep: list = []
    assert home.drop_tile.state() == "idle"
    assert _enter(home, keep, "a.docx").isAccepted()
    assert home.drop_tile.state() == "ready"
    home.dragLeaveEvent(None)
    assert home.drop_tile.state() == "idle"
    _enter(home, keep, "virus.exe")
    assert home.drop_tile.state() == "reject"


def test_drop_emits_only_importable_paths_and_reports_the_rest(home):
    keep: list = []
    seen: list = []
    home.filesDropped.connect(seen.append)
    event = QDropEvent(QPointF(home.rect().center()), Qt.DropAction.CopyAction,
                       _mime(keep, "a.docx", "x.exe"), Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    home.dropEvent(event)
    assert seen == [["/tmp/a.docx"]]
    assert home.drop_tile.state() == "busy"
    home.show_drop_result(["a.docx"], [])
    assert home.drop_tile.state() == "warn"          # 导入成功，但有一个格式不支持
    assert "a.docx" in home.drop_tile.detail.text()
    assert "1" in home.drop_tile.detail.text()


def test_dropping_only_unsupported_files_imports_nothing(home):
    keep: list = []
    seen: list = []
    home.filesDropped.connect(seen.append)
    home.dropEvent(QDropEvent(QPointF(home.rect().center()), Qt.DropAction.CopyAction,
                              _mime(keep, "x.exe"), Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
    assert seen == []
    assert home.drop_tile.state() == "error"
