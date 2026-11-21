"""C-45: the pinned primary button follows the connection list immediately."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
from PySide6.QtWidgets import QApplication  # noqa: E402
from folith.qt import adapter, dialogs  # noqa: E402


class Runner:
    def __init__(self): self.calls = 0
    def submit(self, *a, **kw): self.calls += 1


def _dlg(runner):
    dlg = dialogs.EngineDialog(adapter.EngineConfig(target_lang="简体中文", provider="OpenAI", model="gpt", api_key="k"), runner)
    dlg.show(); dlg.show_page("models")
    QApplication.instance().processEvents()
    return dlg


def test_add_then_click_without_page_switch_triggers_one_apply():
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    lib.profiles.clear()
    lib.refresh()
    assert dlg.models_apply_button.text() == "添加连接"
    lib.profiles.append({"id": "new", "name": "new", "provider": "OpenAI",
                         "model": "gpt", "api_key": "k", "base_url": ""})
    lib.refresh()
    assert dlg.models_apply_button.text() == "测试并保存"
    hits = []
    dlg.apply_connection = lambda: hits.append(1)
    dlg.sync_models_primary_button()
    dlg.models_apply_button.click()
    assert len(hits) == 1
    dlg.close()


def test_delete_last_connection_restores_add_button():
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    lib.profiles.append({"id": "new", "name": "new", "provider": "OpenAI",
                         "model": "gpt", "api_key": "k", "base_url": ""})
    lib.refresh()
    lib.profiles.clear()
    lib.refresh()
    assert dlg.models_apply_button.text() == "添加连接"
    dlg.close()


def test_repeated_page_switches_leave_one_slot():
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    lib.profiles.append({"id": "new", "name": "new", "provider": "OpenAI",
                         "model": "gpt", "api_key": "k", "base_url": ""})
    for _ in range(3):
        lib.refresh()
        dlg.show_page("models")
        dlg.show_page("general")
        dlg.show_page("models")
    hits = []
    dlg.apply_connection = lambda: hits.append(1)
    dlg.sync_models_primary_button()
    dlg.models_apply_button.click()
    assert len(hits) == 1
    dlg.close()


def test_focus_goes_to_the_actually_empty_field(monkeypatch):
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    focused = []
    for widget in (dlg.provider_box, dlg.model_box, dlg.key_edit, dlg.base_edit, lib.add_button):
        monkeypatch.setattr(widget, "setFocus", lambda w=widget: focused.append(w))
    lib.open_editor()
    # offscreen: make the fields report visible (they are behind the editor)
    for w in (dlg.provider_box, dlg.model_box, dlg.key_edit, dlg.base_edit):
        monkeypatch.setattr(w, "isVisible", lambda: True)
    dlg.key_edit.setText("sk-123")
    dlg.base_edit.clear()
    dlg._focus_missing_field("anything")
    assert focused == [dlg.base_edit]
    focused.clear()
    dlg.key_edit.clear(); dlg.base_edit.setText("http://x")
    dlg._focus_missing_field("anything")
    assert focused == [dlg.key_edit]
    lib.editor.close(); dlg.close()


def test_focus_falls_back_to_add_button_when_editor_closed(monkeypatch):
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    focused = []
    for widget in (dlg.provider_box, dlg.model_box, dlg.key_edit, dlg.base_edit, lib.add_button):
        monkeypatch.setattr(widget, "setFocus", lambda w=widget: focused.append(w))
    dlg.provider_box.setCurrentIndex(0); dlg.model_box.setCurrentIndex(0)
    dlg.key_edit.setText("x"); dlg.base_edit.setText("y")
    dlg._focus_missing_field("anything")
    assert focused == [lib.add_button]
    dlg.close()


def test_focus_falls_back_when_editor_closed_and_fields_empty(monkeypatch):
    runner = Runner(); dlg = _dlg(runner)
    lib = dlg.connection_library
    focused = []
    for widget in (dlg.provider_box, dlg.model_box, dlg.key_edit, dlg.base_edit, lib.add_button):
        monkeypatch.setattr(widget, "setFocus", lambda w=widget: focused.append(w))
    dlg.provider_box.setCurrentIndex(0); dlg.model_box.setCurrentIndex(0)
    dlg.key_edit.clear(); dlg.base_edit.clear()
    dlg._focus_missing_field("anything")
    assert focused == [lib.add_button]
    dlg.close()
