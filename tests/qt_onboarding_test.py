"""P8：新手指南与情境提示（acceptance-feedback 第 9 条，9.4 的验收项）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter, connection_status, dialogs, onboarding, preferences  # noqa: E402


class _SyncRunner:
    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(str(exc), "")
            return None
        if on_done:
            on_done(result)
        return None


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(preferences, "settings", lambda: store)
    monkeypatch.setattr(adapter.core, "load_ocr_online_config", lambda *a, **k: {})
    return store


def test_first_run_kinds(settings):
    assert onboarding.first_run_kind(settings, has_jobs=False, configured=False,
                                     version="0.6.0") == "welcome"
    assert onboarding.first_run_kind(settings, has_jobs=True, configured=False,
                                     version="0.6.0") == "whatsnew"
    assert onboarding.first_run_kind(settings, has_jobs=False, configured=True,
                                     version="0.6.0") == "whatsnew"
    onboarding.mark_completed(settings, "0.6.0")
    assert onboarding.first_run_kind(settings, has_jobs=False, configured=False,
                                     version="0.6.0") == "", "never shown twice"


def test_offscreen_and_scripted_runs_never_pop_up(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert onboarding._automatic() is True


def test_sample_document_is_bundled_and_public_domain():
    text = onboarding.SAMPLE_PATH.read_text(encoding="utf-8")
    assert text.startswith("Pride and Prejudice")
    assert "It is a truth universally acknowledged" in text
    assert "Gutenberg" not in text and "Illustration" not in text
    assert 700 < len(text.split()) < 1000
    assert "公有领域" in onboarding.SAMPLE_CREDIT


def test_welcome_walks_four_steps_and_reuses_the_settings_controls(app, settings, monkeypatch):
    monkeypatch.setattr(adapter, "test_connection", lambda config: (True, "OK"))
    monkeypatch.setattr(adapter.EngineConfig, "save", lambda self: Path("x"))
    form = dialogs.EngineDialog(adapter.EngineConfig(target_lang="简体中文"), _SyncRunner())
    wizard = onboarding.WelcomeDialog(form)
    assert wizard.stack.count() == 4
    # 「连接模型」就是设置窗口的「模型」页本身，不是第二套表单。
    assert wizard.stack.widget(1).isAncestorOf(form.key_edit)
    assert wizard.stack.widget(2).isAncestorOf(form.target_box)
    wizard.go(1)
    assert wizard.next_button.text() == "稍后再连接", "the connection step can be skipped"
    form.provider_box.setCurrentText("自定义中转站")
    form.model_box.setCurrentText("m")
    form.key_edit.setText("k")
    form.base_edit.setText("http://127.0.0.1:9/v1")
    form.apply_connection()
    assert wizard._connected and wizard.next_button.text() == "继续"
    wizard.go(3)
    assert not wizard.next_button.isVisibleTo(wizard)
    seen = []
    wizard.sampleRequested.connect(lambda: seen.append("sample"))
    wizard.sample_choice.click()
    assert seen == ["sample"] and wizard.result() == wizard.DialogCode.Accepted


def test_skip_is_always_available(app, settings):
    form = dialogs.EngineDialog(adapter.EngineConfig(), _SyncRunner())
    wizard = onboarding.WelcomeDialog(form)
    for index in range(4):
        wizard.go(index)
        assert wizard.skip_button.isVisibleTo(wizard) or not wizard.isVisible()
        assert not wizard.skip_button.isHidden()
    wizard.skip_button.click()
    assert wizard.result() == wizard.DialogCode.Rejected


def test_coach_marks_show_once_one_at_a_time_and_can_be_turned_off(app, settings, monkeypatch):
    monkeypatch.setattr(onboarding, "_automatic", lambda: False)
    host = QWidget()
    host.resize(600, 400)
    first, second = QPushButton("a", host), QPushButton("b", host)
    host.show()
    app.processEvents()
    marks = onboarding.CoachMarks(settings, host)
    assert marks.offer("prepare", first) is True
    assert marks.showing() == "prepare"
    assert marks.offer("pdf", second) is False, "only one tip at a time"
    marks.close()
    app.processEvents()
    assert marks.offer("prepare", first) is False, "each tip appears once"
    assert marks.offer("pdf", second) is True
    marks.disable_all()
    app.processEvents()
    assert marks.offer("deliver", first) is False
    assert marks.offer("translate", None) is False
    marks.reset()
    assert marks.offer("prepare", first) is True, "「重新显示提示」 brings them back"
    marks.close()
    host.close()


def test_window_help_menu_and_onboarding_entry(app, tmp_path, monkeypatch):
    """帮助菜单里有「新手指南…」「重新显示提示」；脚本化运行下指南不弹出、但记为完成。"""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data))
    monkeypatch.setenv("FOLITH_QT_NONINTERACTIVE", "1")
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    from folith.desktop import paths as desktop_paths
    from folith.qt import window as window_module

    store_file = str(tmp_path / "w.ini")
    monkeypatch.setattr(preferences, "settings",
                        lambda: QSettings(store_file, QSettings.Format.IniFormat))
    window = window_module.MainWindow(adapter.EngineConfig(), desktop_paths.resolve_paths(), None)
    try:
        assert {"guide", "tips", "shortcuts", "github", "issue"} <= set(window.menu_actions)
        assert window.maybe_start_onboarding() == "", "never pops up in scripted runs"
        window.show_onboarding()
        assert str(window.settings.value(onboarding.COMPLETED_KEY) or "")
        imported = []
        monkeypatch.setattr(window, "import_paths", lambda paths, **kw: imported.append(paths))
        window.import_sample()
        assert imported == [[str(onboarding.SAMPLE_PATH)]]
    finally:
        window.timer.stop()
        window.close()
