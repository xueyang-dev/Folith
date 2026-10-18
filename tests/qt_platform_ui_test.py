"""Platform facts, real Qt shortcuts and the shared UI boundary."""
from __future__ import annotations

import ast
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from folith.qt import components, i18n, macwindow, platform_ui, support
from folith.qt.segment_editor import _TargetEdit


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def language():
    old = i18n.language()
    i18n.set_language("zh-CN")
    yield
    i18n.set_language(old)


@pytest.mark.parametrize("platform,system,manager,device,settings", [
    ("darwin", "macOS", "Finder", "Mac", "偏好设置…"),
    ("win32", "Windows", "资源管理器", "电脑", "设置…"),
    ("linux", "Linux", "文件管理器", "电脑", "设置…"),
])
def test_platform_labels(monkeypatch, platform, system, manager, device, settings):
    monkeypatch.setattr(platform_ui.sys, "platform", platform)
    monkeypatch.setattr(platform_ui.platform, "system", lambda: "Linux")
    assert platform_ui.system_name() == system
    assert platform_ui.file_manager_name() == manager
    assert platform_ui.device_name() == device
    assert platform_ui.settings_label() == settings


@pytest.mark.parametrize("code", [code for code, _ in i18n.LANGUAGES])
@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_translated_templates_preserve_user_text(monkeypatch, code, platform):
    monkeypatch.setattr(platform_ui.sys, "platform", platform)
    i18n.set_language(code)
    text = i18n.tr("在{v0}中显示", v0=platform_ui.file_manager_name())
    assert "{" not in text
    assert ("Finder" in text) == (platform == "darwin")
    target = "Mac / Finder / macOS / ⌘S"
    hint = i18n.tr('插入「{v0}」到译文光标处（{v1}点击术语也可以）',
                   v0=target, v1=platform_ui.modifier_text(Qt.KeyboardModifier.AltModifier))
    assert target in hint


@pytest.mark.parametrize("action,key", [
    ("open", QKeySequence.StandardKey.Open),
    ("save", QKeySequence.StandardKey.Save),
    ("close", QKeySequence.StandardKey.Close),
    ("quit", QKeySequence.StandardKey.Quit),
])
def test_shortcuts_follow_actual_qt_platform(app, action, key):
    assert platform_ui.shortcut(action) == QKeySequence(key)
    assert platform_ui.shortcut_text(action) == QKeySequence(key).toString(
        QKeySequence.SequenceFormat.NativeText)


def test_search_hint_matches_installed_shortcut(app):
    bar = components.SearchBar()
    assert bar.hint.text() == bar._shortcut.key().toString(QKeySequence.SequenceFormat.NativeText)
    bar.deleteLater()


def test_save_next_accepts_command_modifier_only(app):
    editor = _TargetEdit()
    calls = []
    editor.commitAndNext.connect(lambda: calls.append(True))
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(calls) == 1
    # Windows/Meta and Ctrl+Alt must not unexpectedly submit a segment.
    for modifiers in (Qt.KeyboardModifier.MetaModifier,
                      Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        QTest.keyClick(editor, Qt.Key.Key_Return, modifiers)
    assert len(calls) == 1
    editor.deleteLater()


def test_windows_title_bar_never_calls_cocoa(app, monkeypatch):
    monkeypatch.setattr(platform_ui.sys, "platform", "win32")
    monkeypatch.setattr(macwindow, "configure_title_bar",
                        lambda *_: pytest.fail("Cocoa path invoked on Windows"))
    widget = QWidget()
    flags = widget.windowFlags()
    platform_ui.configure_title_bar(widget, lambda: pytest.fail("safe area changed"))
    assert widget.windowFlags() == flags
    widget.deleteLater()


def test_macos_title_bar_dispatch_and_offscreen_guard(app, monkeypatch):
    widget = QWidget()
    flags = widget.windowFlags()
    monkeypatch.setattr(platform_ui.sys, "platform", "darwin")
    # Actual NSWindow behavior still needs a real macOS machine.
    if QApplication.platformName() != "cocoa":
        platform_ui.configure_title_bar(widget, lambda: pytest.fail("not Cocoa"))
        assert widget.windowFlags() == flags
    calls = []
    monkeypatch.setattr(macwindow, "configure_title_bar", lambda *args: calls.append(args))
    platform_ui.configure_title_bar(widget, lambda: None)
    assert calls[0][0] is widget
    widget.deleteLater()


def test_report_system_facts_and_backup_path(app, monkeypatch, tmp_path):
    monkeypatch.setattr(platform_ui.sys, "platform", "win32")
    monkeypatch.setattr(platform_ui.platform, "win32_ver", lambda: ("11", "10.0.26100", "", ""))
    monkeypatch.setattr(support.adapter, "app_identity", lambda: {"app_version": "0.6.0", "build_id": "abc"})
    view = support.about_view()
    assert view["system_name"] == "Windows"
    assert view["system_version"] == "10.0.26100"
    body = parse_qs(urlparse(support.issue_url(view)).query)["body"][0]
    assert "Windows 10.0.26100" in body and "macOS" not in body
    root = tmp_path / "Mac Finder 文档"
    root.mkdir()
    monkeypatch.setattr(support.adapter.core, "OUTPUT_DIR", root)
    monkeypatch.setattr(support, "running_jobs", lambda: [])
    target = tmp_path / "backup.zip"
    support.export_all_data(target)
    import zipfile
    with zipfile.ZipFile(target) as archive:
        assert str(root) in archive.read("Folith-data/README.txt").decode()


def test_windows_bundled_ocr_and_process_metrics():
    if os.name != "nt":
        pytest.skip("Requires native Windows APIs")
    from folith.desktop import paths
    from folith.trace import _windows_rss_mb
    import core

    assert paths.tesseract_binary_name() == "tesseract.exe"
    assert _windows_rss_mb() > 0
    assert core._runtime_pid_alive(os.getpid())


def test_new_shared_ui_uses_platform_parameters():
    root = Path(platform_ui.__file__).parent
    offenders = []
    for path in root.glob("*.py"):
        if path.stem in {"platform_ui", "macwindow"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "tr" or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            text = str(node.args[0].value)
            if any(token in text for token in ("⌘", "⌥", "Finder", "macOS", "这台 Mac")):
                offenders.append(f"{path.name}:{node.lineno}: {text}")
    assert not offenders, "\n".join(offenders)
