"""#517：设置窗口适配屏幕、固定操作、模型页紧凑折叠。

这些用例钉住的是几何与结构，不是像素：任何把操作按钮放回滚动区、让窗口往下
长出屏幕、或者把十条连接全摊开的改动都会在这里变红。
"""
from __future__ import annotations

import json
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
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton,  # noqa: E402
                               QScrollArea, QToolButton)

from folith.qt import adapter, dialogs  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


class _FakeScreen:
    """1280×800、Dock 可见时 730px 可用高度的屏幕（#517 的验收尺寸）。"""

    def __init__(self, rect: QRect) -> None:
        self._rect = rect

    def availableGeometry(self) -> QRect:  # noqa: N802 - Qt 命名
        return self._rect


AREA = QRect(0, 0, 1280, 730)


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _dialog(connections: int = 0) -> dialogs.EngineDialog:
    dialog = dialogs.EngineDialog(adapter.EngineConfig(target_lang="简体中文"), TaskRunner())
    library = dialog.connection_library
    library.profiles = [
        {"id": f"c{i}", "name": f"连接 {i}", "provider": "OpenAI", "model": f"model-{i}",
         "api_key": f"key-{i}", "base_url": "", "models": [f"model-{i}"], "custom": False}
        for i in range(connections)
    ]
    library.refresh()
    return dialog


# ------------------------------------------------------------- 几何 ----------

def test_height_change_clamps_the_whole_window_into_available_area(app, monkeypatch):
    dialog = _dialog(connections=10)
    monkeypatch.setattr(dialog, "screen", lambda: _FakeScreen(AREA))
    try:
        # 窗口原本开在屏幕下方：resize 往下长会把底部按钮推到 Dock 后面。
        dialog.move(900, 1200)
        dialog.show_page("models")
        app.processEvents()
        frame = dialog.frameGeometry()
        assert AREA.contains(frame), (frame, AREA)
        assert abs(frame.center().x() - AREA.center().x()) <= 2
        assert abs(frame.center().y() - AREA.center().y()) <= 2
    finally:
        dialog.close()


def test_first_show_recenters_a_window_opened_below_the_screen(app, monkeypatch):
    dialog = _dialog(connections=10)
    monkeypatch.setattr(dialog, "screen", lambda: _FakeScreen(AREA))
    try:
        dialog.move(400, 2000)
        dialog.show()
        app.processEvents()
        assert AREA.contains(dialog.frameGeometry()), dialog.frameGeometry()
    finally:
        dialog.close()


# ----------------------------------------------------------- 固定操作 --------

def test_apply_button_is_pinned_outside_every_scroll_area(app):
    dialog = _dialog(connections=10)
    try:
        dialog.show()
        app.processEvents()
        apply_button = dialog.models_apply_button
        assert any(area.isAncestorOf(apply_button)
                   for area in [dialog._pinned_actions]), "操作按钮应在固定区"
        for area in dialog.findChildren(QScrollArea):
            assert not area.isAncestorOf(apply_button), "操作按钮不能跟着内容滚动"
        dialog.show_page("general")
        assert not dialog._pinned_actions.isVisible()
        dialog.show_page("models")
        assert dialog._pinned_actions.isVisible()
    finally:
        dialog.close()


# ----------------------------------------------------------- 模型页 ----------

def _connection_rows(library):
    return [label for label in library.list_group.findChildren(QLabel)
            if label.property("textRole") == "name"]


def test_long_connection_list_collapses_and_expands(app):
    dialog = _dialog(connections=10)
    try:
        library = dialog.connection_library
        assert len(_connection_rows(library)) == library.COLLAPSE_AFTER
        assert library.show_all_button.text() == "显示全部 (10)"
        library.show_all_button.click()
        assert len(_connection_rows(library)) == 10
        assert not [button for button in library.list_group.findChildren(QPushButton)
                    if button.text().startswith("显示全部")]
    finally:
        dialog.close()


def test_connection_rows_use_one_line_and_a_row_menu(app):
    dialog = _dialog(connections=2)
    try:
        library = dialog.connection_library
        for label in _connection_rows(library):
            holder = label.parentWidget()
            assert isinstance(holder.layout(), QHBoxLayout), "一行连接应当只用一个横排"
            full_buttons = [button for button in holder.findChildren(QPushButton)
                            if button.text() in ("编辑", "删除")]
            assert not full_buttons, full_buttons
            actions = holder.findChild(QToolButton)
            assert actions is not None and actions.menu() is not None
            labels = [action.text() for action in actions.menu().actions()]
            assert "编辑" in labels and "删除" in labels
    finally:
        dialog.close()


def test_new_strings_are_translated_in_every_locale():
    locales = Path(ROOT) / "folith" / "resources" / "locales"
    for code in ("zh-CN", "en", "es", "ru", "ar", "fr", "de"):
        data = json.loads((locales / f"qt-{code}.json").read_text(encoding="utf-8"))
        assert "显示全部 ({v0})" in data, code
        assert "连接操作" in data, code
