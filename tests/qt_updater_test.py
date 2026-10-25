"""Qt update state and the existing save/stop boundary (no real installation)."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from folith.qt import support
from folith.qt.updater import UpdateController
from folith.desktop import updates


class Runner:
    def submit(self, fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs):
        kwargs.pop("label", None)
        if on_progress:
            kwargs["on_progress"] = on_progress
        try:
            value = fn(*args, **kwargs)
        except Exception as exc:
            if on_error:
                on_error(str(exc), "")
        else:
            if on_done:
                on_done(value)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_settings_shows_verified_download_then_restart(app, tmp_path, monkeypatch):
    from folith.qt.settings_pages import AboutPage
    controller = UpdateController(Runner(), cache_dir=tmp_path)
    monkeypatch.setattr(updates, "windows_available", lambda: True)
    monkeypatch.setattr(support, "check_for_update", lambda _: {
        "state": "newer", "latest": "0.8.0", "notes": "Release notes", "url": ""})
    monkeypatch.setattr(updates, "fetch_manifest", lambda _: {"size": 4})
    monkeypatch.setattr(updates, "download", lambda *a, **k: {"path": "verified.exe"})
    page = AboutPage(controller.runner, updater=controller)
    page.check_update()
    assert not page.install_button.isHidden()
    assert page.download_link.isHidden()
    assert page.release_notes.toPlainText() == "Release notes"
    page.install_button.click()
    assert controller.state == "ready"
    assert page.install_button.text() == "重启并更新"
    called = []
    controller.restartRequested.connect(lambda: called.append(True))
    page.install_button.click()
    assert called == [True]
    # Settings can be recreated against the application-owned state.
    page.deleteLater()
    other = AboutPage(controller.runner, updater=controller)
    assert other.install_button.text() == "重启并更新"


def test_cancellation_remains_retryable(app, tmp_path, monkeypatch):
    controller = UpdateController(Runner(), cache_dir=tmp_path)
    controller.state = "newer"
    controller.manifest = {"size": 4}
    def cancelled(*args, **kwargs):
        controller.cancel()
        raise updates.DownloadCancelled()
    monkeypatch.setattr(updates, "download", cancelled)
    controller.download()
    assert controller.state == "cancelled"
    monkeypatch.setattr(updates, "download", lambda *a, **k: {"path": "verified.exe"})
    controller.download()
    assert controller.state == "ready"


def test_bad_signature_never_offers_download(app, tmp_path, monkeypatch):
    controller = UpdateController(Runner(), cache_dir=tmp_path)
    monkeypatch.setattr(updates, "windows_available", lambda: True)
    monkeypatch.setattr(support, "check_for_update", lambda _: {"state": "newer", "latest": "0.8.0"})
    def bad(*a):
        raise updates.UpdateError("Bad signature")
    monkeypatch.setattr(updates, "fetch_manifest", bad)
    controller.check()
    assert controller.state == "failed" and controller.package is None


def test_update_refuses_force_exit_and_never_launches(app):
    from folith.qt.window import MainWindow
    calls = []
    window = SimpleNamespace(_pending_update=True, _exit_in_progress=True,
                             _critical=lambda *a: calls.append("notice"))
    MainWindow._acquire_close(window, forced=True)
    assert calls == ["notice"]
    assert not window._exit_in_progress and not window._pending_update


def test_failed_edit_during_update_keeps_window_open(app, monkeypatch):
    from folith.qt import adapter
    from folith.qt.window import MainWindow
    calls = []
    def fail(*a):
        raise OSError("disk full")
    monkeypatch.setattr(adapter, "submit_edit", fail)
    window = SimpleNamespace(_pending_update=True, _exit_in_progress=True,
        runner=SimpleNamespace(wait_for_done=lambda _: True), _edit_queue=[("job", 0, "draft")],
        _failure=lambda *a, **k: calls.append("failed"),
        updates=SimpleNamespace(install=lambda _: calls.append("installed")), paths=None)
    MainWindow._acquire_close(window)
    assert calls == ["failed"] and window._edit_queue == [("job", 0, "draft")]
    assert not window._pending_update
