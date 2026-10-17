"""应用自己的对话框（`folith.qt.prompts`）的界面用例。

这层替换的是系统 `QMessageBox` / `QInputDialog` / `QProgressDialog`，所以用例钉住的是
**行为契约**而不是像素：

* 确认框返回用户真实选择，危险动作的主按钮是实心危险色（不是白底红字）；
* 通知框的详情默认收起、可展开；
* 文本输入框返回值与是否确认；
* 等待框是模态、只有一条出路「在后台运行」，点它只是隐藏、不取消任务。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from folith.qt import prompts, style  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    yield instance


@pytest.fixture()
def window(app):
    """这些对话框的父窗口占位（用例只关心对话框自身的行为）。"""
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    yield host
    host.deleteLater()


def test_confirm_reports_the_real_choice(app, window):
    dialog = prompts.ConfirmDialog(window, "删除任务", "确定删除吗？",
                                   yes="永久删除", no="取消")
    dialog.show()
    app.processEvents()
    assert dialog.accepted_by_user is False
    dialog.no_button.click()
    app.processEvents()
    assert dialog.accepted_by_user is False
    assert dialog.result() == 0                      # Rejected

    second = prompts.ConfirmDialog(window, "冻结交付", "确定继续吗？")
    second.show()
    app.processEvents()
    second.yes_button.click()
    app.processEvents()
    assert second.accepted_by_user is True
    assert second.result() == 1                      # Accepted


def test_danger_confirm_uses_a_danger_button(app, window):
    danger = prompts.ConfirmDialog(window, "删除任务", "不可撤销", tone="danger")
    safe = prompts.ConfirmDialog(window, "冻结交付", "确定继续吗？")
    try:
        assert danger.yes_button.property("kind") == "danger"
        assert safe.yes_button.property("kind") == "primary"
    finally:
        danger.close()
        safe.close()


def test_enter_on_a_danger_confirm_cancels_instead_of_deleting(app, window):
    """危险确认框弹出时直接按回车，不能执行不可撤销的操作：回车与焦点都在取消上。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    danger = prompts.ConfirmDialog(window, "删除任务", "不可撤销",
                                   yes="永久删除", no="取消", tone="danger")
    danger.show()
    app.processEvents()
    try:
        assert danger.no_button.isDefault() and not danger.yes_button.isDefault()
        assert danger.focusWidget() is danger.no_button
        QTest.keyClick(danger, Qt.Key.Key_Return)
        app.processEvents()
        assert danger.accepted_by_user is False
        assert danger.result() == 0
    finally:
        danger.close()


def test_enter_on_a_safe_confirm_still_confirms(app, window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    safe = prompts.ConfirmDialog(window, "冻结交付", "确定继续吗？")
    safe.show()
    app.processEvents()
    try:
        assert safe.yes_button.isDefault() and not safe.no_button.isDefault()
        QTest.keyClick(safe, Qt.Key.Key_Return)
        app.processEvents()
        assert safe.accepted_by_user is True
    finally:
        safe.close()


def test_notice_keeps_detail_collapsed_until_toggled(app, window):
    dialog = prompts.NoticeDialog(window, "导入失败", "有 2 个文档没有导入",
                                 tone="danger", detail="traceback…")
    dialog.show()
    app.processEvents()
    try:
        assert dialog._detail_view.isHidden() is True
        assert dialog._detail_button.isHidden() is False
        dialog._detail_button.setChecked(True)
        app.processEvents()
        assert dialog._detail_view.isHidden() is False
        assert "traceback" in dialog._detail_view.toPlainText()
        dialog._detail_button.setChecked(False)
        app.processEvents()
        assert dialog._detail_view.isHidden() is True
    finally:
        dialog.close()

    plain = prompts.NoticeDialog(window, "已保存", "诊断包已写入")
    plain.show()
    app.processEvents()
    try:
        assert plain._detail_button.isHidden() is True, "没有详情就不该有展开按钮"
    finally:
        plain.close()


def test_text_input_returns_the_typed_value(app, window):
    dialog = prompts.TextInputDialog(window, "新建项目", "项目名称：",
                                     text="生态恢复")
    dialog.show()
    app.processEvents()
    try:
        assert dialog.value() == "生态恢复"
        dialog.edit.setText("产品手册")
        dialog.ok_button.click()
        app.processEvents()
        assert dialog.value() == "产品手册"
        assert dialog.result() == 1
    finally:
        dialog.close()


def test_busy_dialog_is_modal_with_one_way_out(app, window):
    dialog = prompts.BusyDialog(window, "正在导入 3 个文档…")
    dialog.show()
    app.processEvents()
    try:
        assert dialog.isModal() is True
        buttons = dialog.findChildren(QPushButton)
        assert [button.text() for button in buttons] == ["在后台运行"]
        assert dialog.isVisible() is True
        buttons[0].click()
        app.processEvents()
        assert dialog.isVisible() is False, "点「在后台运行」只隐藏，不取消任务"
    finally:
        dialog.close()


def test_every_prompt_paints_with_theme_tokens(app, window):
    """对话框内容直接落在窗口面上（主题的最亮表面，由 QSS 提供），不再套一张带边框的卡片。"""
    dialogs = [
        prompts.ConfirmDialog(window, "标题", "正文"),
        prompts.NoticeDialog(window, "标题", "正文"),
        prompts.TextInputDialog(window, "标题", "标签"),
        prompts.BusyDialog(window, "处理中…"),
    ]
    try:
        for dialog in dialogs:
            assert dialog.objectName() == "promptDialog", type(dialog).__name__
            assert not dialog.findChildren(style.Card), type(dialog).__name__
    finally:
        for dialog in dialogs:
            dialog.close()
