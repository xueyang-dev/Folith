"""#520：小通知的语气、图标、底部定位、中间省略与错误常驻。"""
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
from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from folith.qt import notifications  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _toast(app):
    parent = QWidget()
    parent.resize(1000, 700)
    parent.show()
    app.processEvents()
    return notifications.Toast(parent), parent


@pytest.mark.parametrize("tone,status", [("success", "done"), ("info", "active"),
                                         ("warning", "attention"), ("error", "error"),
                                         ("progress", "active")])
def test_every_tone_has_an_icon_and_a_status_colour(app, tone, status):
    toast, parent = _toast(app)
    try:
        toast.present("已保存", tone=tone)
        assert toast._tone == tone
        assert toast.label.property("tone") == status
        assert not toast.icon_label.pixmap().isNull()
        assert toast.isVisible()
    finally:
        parent.close()


def test_toast_sits_bottom_centre_and_dismisses_after_five_seconds(app):
    toast, parent = _toast(app)
    try:
        toast.present("已创建 1 个新任务")
        assert toast.timer.interval() == 5000
        assert abs(toast.x() + toast.width() // 2 - parent.width() // 2) <= 1
        assert toast.y() + toast.height() <= parent.height() - 16
    finally:
        parent.close()


def test_error_toast_is_sticky_even_after_hover(app):
    toast, parent = _toast(app)
    try:
        toast.present("保存失败", error=True)
        assert not toast.timer.isActive()
        app.sendEvent(toast, QEvent(QEvent.Type.Enter))
        app.sendEvent(toast, QEvent(QEvent.Type.Leave))
        assert not toast.timer.isActive(), "errors must wait for the user"
        toast._fade()
        assert toast.isVisible()
    finally:
        parent.close()


def test_long_filename_is_elided_in_the_middle_with_full_text_available(app):
    toast, parent = _toast(app)
    name = "The Correspondent (Virginia Evans) (z-library.sk, 1lib.sk, z-lib.sk).epub"
    try:
        toast.present(f"已创建 1 个新任务（{name}）")
        assert toast.label.text() != toast._message
        assert "…" in toast.label.text()
        assert toast.label.text().startswith("已创建")
        assert toast.label.text().endswith("）.epub") or ".epub" in toast.label.text()
        assert toast.label.toolTip() == toast._message
        assert toast.accessibleName() == f"已创建 1 个新任务（{name}）"
    finally:
        parent.close()


def test_detail_button_opens_with_long_text(app, monkeypatch):
    toast, parent = _toast(app)
    try:
        toast.present("导入失败", error=True, detail="完整原因：HTTP 401")
        assert toast.details.isVisible()
        assert toast._detail == "完整原因：HTTP 401"
    finally:
        parent.close()


@pytest.mark.parametrize("tone,status", [("success", "done"), ("info", "active"),
                                         ("warning", "attention"), ("error", "error")])
def test_banner_shows_one_line_with_tone_icon(app, tone, status):
    holder = QWidget()
    banner = notifications.NoticeBanner(holder)
    banner.set_notice("已保存 12 段译文", tone=tone)
    assert not banner.isHidden()
    assert banner.label.property("tone") == status
    assert not banner.icon_label.pixmap().isNull()
    assert not banner.label.wordWrap()
    assert not banner.action_button.isVisible()
    banner.close()


def test_banner_elides_the_middle_and_exposes_an_action(app):
    holder = QWidget()
    banner = notifications.NoticeBanner(holder)
    banner.resize(520, 40)
    name = "The Correspondent (Virginia Evans) (z-library.sk, 1lib.sk).epub"
    clicked = []
    banner.set_notice(f"已创建 1 个新任务（{name}）", action="打开任务",
                      on_action=lambda: clicked.append(True))
    app.processEvents()
    assert "…" in banner.label.text()
    assert banner.label.toolTip().endswith(name + "）")
    assert not banner.action_button.isHidden()
    banner.action_button.click()
    assert clicked == [True]
    banner.close_button.click()
    assert banner.isHidden()
    banner.close()
