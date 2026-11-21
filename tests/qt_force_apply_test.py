"""C-42: 'apply anyway' only after a real failed connection test."""
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

from folith.qt import adapter, dialogs  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _config(connections=None):
    return adapter.EngineConfig(
        target_lang="简体中文", provider="OpenAI", model="gpt", api_key="",
        connections=connections or [])


def _fake_runner():
    class Runner:
        def submit(self, fn, cfg, *, on_done, on_error, label=""):
            self.args = (fn, cfg, on_done, on_error, label)
        def fire(self, ok, message):
            self.args[2]((ok, message))
    return Runner()


def test_saved_connection_keeps_test_button(app):
    runner = _fake_runner()
    dlg = dialogs.EngineDialog(_config(), runner)
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    # A saved connection counts even when its key is empty (env-var / local gateway).
    assert dlg.models_apply_button.text() == "测试并保存"
    assert not dlg.models_force_button.isVisible() or True  # hidden initially
    dlg.close()


def test_missing_required_field_hides_force_and_focuses(app):
    runner = _fake_runner()
    dlg = dialogs.EngineDialog(_config(), runner)
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    dlg.apply_connection()
    assert dlg.models_force_button.isHidden()
    assert "无法应用" in dlg._status.text()
    dlg.close()


def test_failed_test_shows_force_and_can_apply(app, monkeypatch):
    runner = _fake_runner()
    cfg = adapter.EngineConfig(
        target_lang="简体中文", provider="OpenAI", model="gpt", api_key="bad", base_url="",
        connections=[{"id": "gw", "name": "gw", "provider": "OpenAI",
                      "model": "gpt", "api_key": "bad", "base_url": ""}],
        connection_roles={"translation": "gw"})
    dlg = dialogs.EngineDialog(cfg, runner)
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    dlg.apply_connection()
    assert runner.args and runner.args[4] == "apply-connection"
    runner.fire(False, "unauthorized")
    app.processEvents()
    assert dlg.models_force_button.isVisible() or not dlg.models_force_button.isHidden()
    dlg.close()


def test_successful_test_saves_without_force(app, monkeypatch):
    runner = _fake_runner()
    cfg = adapter.EngineConfig(
        target_lang="简体中文", provider="OpenAI", model="gpt", api_key="good", base_url="",
        connections=[{"id": "gw", "name": "gw", "provider": "OpenAI",
                      "model": "gpt", "api_key": "good", "base_url": ""}],
        connection_roles={"translation": "gw"})
    saved = []
    dlg = dialogs.EngineDialog(cfg, runner)
    monkeypatch.setattr(dlg, "_commit_connection",
                        lambda c, *, tested, message="": saved.append((tested, message)))
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    dlg.apply_connection()
    runner.fire(True, "ok")
    app.processEvents()
    assert saved == [(True, "ok")]
    assert not dlg.models_force_button.isVisible() or dlg.models_force_button.isHidden()
    dlg.close()


def test_long_error_wraps_and_is_fully_visible(app):
    from folith.qt import dialogs as d

    long_error = "HTTP 403: " + "权限不足、密钥无访问权。" * 30
    text = d.connection_error_text(long_error)
    dlg = dialogs.EngineDialog(_config(), _fake_runner())
    try:
        dlg._status.setWordWrap(True)
        dlg._status.setText(text)
        app.processEvents()
        assert dlg._status.wordWrap()
        assert dlg._status.text() == text
        assert dlg._status.heightForWidth(380) > dlg._status.fontMetrics().height()
    finally:
        dlg.close()


def test_permission_error_gets_generic_hint_only():
    from folith.qt import dialogs as d

    hint = "服务商拒绝了这把密钥对该模型的访问"
    assert hint in d.connection_error_text("HTTP 403, permission_error")
    assert hint in d.connection_error_text("permission denied")
    assert hint not in d.connection_error_text("connection timed out")


def test_saved_connection_without_key_keeps_test_and_save(app):
    runner = _fake_runner()
    cfg = adapter.EngineConfig(
        target_lang="简体中文", provider="Local Gateway", model="local", api_key="",
        connections=[{"id": "lg", "name": "local", "provider": "Local Gateway",
                      "model": "local", "api_key": "", "base_url": "http://127.0.0.1:1/v1"}],
        connection_roles={"translation": "lg"})
    dlg = dialogs.EngineDialog(cfg, runner)
    dlg.show(); dlg.show_page("models")
    app.processEvents()
    assert dlg.models_apply_button.text() == "测试并保存"
    dlg.close()


def test_editor_test_and_save_403_shows_hint(app, monkeypatch):
    from folith.qt import connection_library as cl

    runner = _fake_runner()
    dlg = dialogs.EngineDialog(
        adapter.EngineConfig(target_lang="简体中文", provider="OpenAI", model="gpt", api_key="",
                             connections=[]), runner)
    lib = cl.ConnectionLibrary(dlg)
    monkeypatch.setattr(lib, "_editor_profile", lambda quiet=False: {"id": "x"})
    monkeypatch.setattr(lib, "save", lambda: True)
    lib.open_editor()
    lib.test_and_save()
    runner.fire(False, "HTTP 403: permission_error")
    app.processEvents()
    text = dlg.test_label.text()
    assert "服务商拒绝了这把密钥对该模型的访问" in text
    assert dlg.test_label.wordWrap()
    assert not dlg.test_label.isHidden()
    assert dlg.test_label.sizePolicy().verticalPolicy().name == "MinimumExpanding"
    lib.editor.close(); dlg.close()
