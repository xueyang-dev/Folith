"""P5：原生设置窗口、连接状态、存储与关于（acceptance-feedback 第 8 条、D6、12-B、12-H）。"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.qt import adapter, connection_status, dialogs, support  # noqa: E402


class _SyncRunner:
    def __init__(self):
        self.labels = []

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        self.labels.append(label)
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


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """数据目录与连接状态都隔离：不读写开发者本机的设置或数据。"""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(connection_status, "QSettings", lambda *args: store)
    monkeypatch.setattr(adapter.core, "load_ocr_online_config", lambda *a, **k: {})
    return data, store


def _config(**extra):
    base = dict(provider="自定义中转站", model="m1", api_key="k1",
                base_url="http://127.0.0.1:9/v1", target_lang="简体中文")
    base.update(extra)
    return adapter.EngineConfig(**base)


# ---------------------------------------------------------------- 窗口 -------

def test_settings_is_a_non_modal_window_with_six_titled_pages(app):
    dialog = dialogs.EngineDialog(_config(), _SyncRunner())
    from PySide6.QtCore import Qt
    from folith.qt import platform_ui
    assert bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == platform_ui.is_macos()
    assert not dialog.isModal()
    assert [key for key, _icon, _label in dialog.PAGES] == [
        "general", "models", "vision", "defaults", "storage", "about"]
    for key, _icon, label in dialog.PAGES:
        dialog.show_page(key)
        assert dialog.current_page() == key
        assert dialog.windowTitle() == label
        assert dialog.tab_buttons[key].isChecked()
    # 工作模式属于工作流：在「任务默认值」页里，而不是「通用」。
    defaults = dialog.pages["defaults"].widget()
    assert defaults.isAncestorOf(dialog.academic_box)
    assert not dialog.pages["general"].widget().isAncestorOf(dialog.academic_box)
    # 视觉模型和翻译、审校一起在「模型 › 用途」里选；扫描件怎么读是单独一页。
    assert dialog.pages["models"].widget().isAncestorOf(dialog.vision_connection_box)
    assert dialog.pages["vision"].widget().isAncestorOf(dialog.ocr_backend_box)
    dialog.close()


def test_ordinary_options_apply_at_once_without_unapplied_connection_edits(app):
    dialog = dialogs.EngineDialog(_config(use_tm=True), _SyncRunner())
    seen = []
    dialog.preferencesChanged.connect(lambda: seen.append(dialog.config))
    dialog.key_edit.setText("typed-but-not-applied")
    dialog.tm_box.setChecked(False)
    assert dialog._defaults_timer.isActive(), "changes are coalesced, then applied"
    dialog.flush_preferences()
    assert len(seen) == 1
    assert seen[0].use_tm is False
    assert seen[0].api_key == "k1", "an unapplied connection edit must not leak into defaults"
    dialog.close()


def test_apply_tests_the_connection_first(app, monkeypatch):
    saved = []
    monkeypatch.setattr(adapter.EngineConfig, "save", lambda self: saved.append(self) or Path("x"))
    monkeypatch.setattr(adapter, "test_connection", lambda config: (False, "HTTP 401"))
    dialog = dialogs.EngineDialog(_config(), _SyncRunner())
    applied = []
    dialog.connectionApplied.connect(lambda: applied.append(True))
    dialog.connection_library.profiles[0]["model"] = "m2"
    dialog.connection_library.role_models["translation"] = "m2"
    dialog.apply_connection()
    assert saved == [] and applied == [], "a failing connection is not saved"
    assert "HTTP 401" in dialog._status.text()
    assert not dialog.models_force_button.isHidden()
    assert connection_status.status("translation", dialog._collect())["state"] == "failed"

    monkeypatch.setattr(adapter, "test_connection", lambda config: (True, "OK · 0.3s"))
    dialog.apply_connection()
    assert [config.model for config in saved] == ["m2"] and applied == [True]
    assert dialog.config.model == "m2"
    assert dialog.models_force_button.isHidden()
    assert connection_status.status("translation", dialog.config)["state"] == "ok"
    dialog.close()


# ------------------------------------------------------------ 连接状态 --------

def test_connection_status_follows_the_configuration(isolated):
    _data, store = isolated
    config = _config()
    assert connection_status.status("translation", config)["state"] == "untested"
    assert connection_status.status("reviewer", config)["state"] == "inherited"
    connection_status.record("translation", config, True, "OK",
                             now=datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc))
    row = connection_status.status("translation", config)
    assert (row["state"], row["at"][:16]) == ("ok", "2026-10-15T09:00")
    assert connection_status.overview(config)["text"] == "连接正常"
    # 改了模型或密钥：旧结果不再算数。
    for change in ({"model": "m2"}, {"api_key": "k2"}):
        assert connection_status.status("translation", _config(**change))["state"] == "untested"
    assert "k1" not in json.dumps({key: store.value(key) for key in store.allKeys()}), \
        "the key itself is never stored"
    broken = _config(api_key="")
    assert connection_status.overview(broken)["text"] == "翻译模型未配置"
    separate = _config(reviewer_mode="separate", reviewer_provider="自定义中转站",
                       reviewer_model="r1")
    assert connection_status.status("reviewer", separate)["state"] == "untested"
    assert connection_status.overview(separate)["text"] == "审校模型未测试"


def test_connection_panel_tests_one_role(app, monkeypatch):
    from folith.qt.settings_pages import ConnectionPanel

    monkeypatch.setattr(adapter.core, "test_provider", lambda *a: (True, "响应「OK」"))
    config = _config()
    panel = ConnectionPanel(_SyncRunner(), lambda: config)
    changed = []
    panel.changed.connect(lambda: changed.append(True))
    assert panel._rows["reviewer"]["container"].isHidden(), "inherited: merged into translation"
    panel.test_button.click()
    assert changed == [True]
    assert connection_status.status("translation", config)["state"] == "ok"
    assert panel._rows["translation"]["state"].text() == "已连接"
    assert "上次测试" in panel._rows["translation"]["container"].toolTip()


# ----------------------------------------------------------------- 存储 -------

def _seed(data: Path) -> None:
    job = data / "0123456789abcdef"
    job.mkdir()
    (job / "state.json").write_text("{}" * 500)
    (data / "projects").mkdir()
    (data / "projects" / "p.json").write_text("x" * 300)
    (data / "ocr_profile_cache" / "aa").mkdir(parents=True)
    (data / "ocr_profile_cache" / "aa" / "page.json").write_text("y" * 700)
    (data / "translation_cache.json").write_text("z" * 200)
    (data / "provider_config.json").write_text('{"api_key": "secret-key"}')
    (data / "translation_cache.write.lock").write_text("")


def test_storage_breakdown_and_cache_cleanup(isolated, monkeypatch):
    data, _store = isolated
    _seed(data)
    view = support.storage_view()
    assert view["job_count"] == 1 and view["jobs"] == 1000
    assert view["projects"] == 300 and view["caches"] == 900
    monkeypatch.setattr(adapter, "job_is_alive", lambda job_id: True)
    with pytest.raises(adapter.AdapterError):
        support.clear_caches()
    monkeypatch.setattr(adapter, "job_is_alive", lambda job_id: False)
    assert support.clear_caches() == 900
    assert not (data / "ocr_profile_cache").exists()
    assert not (data / "translation_cache.json").exists()
    assert (data / "0123456789abcdef" / "state.json").exists(), "tasks are never touched"
    assert (data / "projects" / "p.json").exists()


def test_export_leaves_out_keys_and_locks(isolated, tmp_path):
    data, _store = isolated
    _seed(data)
    result = support.export_all_data(tmp_path / "backup")
    assert result["path"].endswith("backup.zip")
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
        blob = b"".join(archive.read(name) for name in names)
    assert "Folith-data/0123456789abcdef/state.json" in names
    assert not any("provider_config" in name or name.endswith(".lock") for name in names)
    assert b"secret-key" not in blob
    with pytest.raises(adapter.AdapterError):
        support.export_all_data(data / "inside.zip")


# ----------------------------------------------------------------- 关于 -------

def test_issue_link_carries_only_version_and_system():
    url = support.issue_url({"version": "0.6.0", "build": "git:abc1234",
                             "system_name": "macOS", "system_version": "15.7.5",
                             "frozen": True})
    assert url.startswith(support.ISSUES_URL + "?")
    assert "0.6.0" in url and "15.7.5" in url
    assert "Library" not in url and "Users" not in url


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("tag, state", [("v0.7.0", "newer"), ("v0.6.0", "current"),
                                        ("0.5.9", "current")])
def test_update_check_compares_with_the_latest_release(tag, state):
    body = json.dumps({"tag_name": tag, "html_url": "https://example.test/r"}).encode()
    result = support.check_for_update("0.6.0", opener=lambda *a, **k: _Response(body))
    assert result["state"] == state


def test_update_check_reports_a_failure_instead_of_raising():
    def offline(*_a, **_k):
        raise OSError("offline")

    assert support.check_for_update("0.6.0", opener=offline)["state"] == "failed"


def test_shortcut_list_comes_from_the_real_menus(app):
    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWidgets import QMenuBar

    from folith.qt.settings_pages import ShortcutsDialog, menu_shortcuts

    bar = QMenuBar()
    menu = bar.addMenu("文件")
    action = QAction("设置…", bar)
    action.setShortcut(QKeySequence("Ctrl+,"))
    menu.addAction(action)
    menu.addAction(QAction("没有快捷键", bar))
    rows = menu_shortcuts(bar)
    assert [(m, a) for m, a, _k in rows] == [("文件", "设置…")]
    dialog = ShortcutsDialog(bar)
    assert dialog.table.rowCount() == len(rows) + len(dialog.rows) - len(rows)
    assert any(text == "展开 / 收起 PDF 原页" for _m, text, _k in dialog.rows)
