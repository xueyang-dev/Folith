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
from folith.qt import adapter, connection_status, dialogs, preferences, support  # noqa: E402


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
    # Every preference reader goes through folith.qt.preferences; redirect that
    # one entry point so the connection status never touches the real store.
    monkeypatch.setattr(preferences, "settings", lambda: store)
    monkeypatch.setattr(adapter.core, "load_ocr_online_config", lambda *a, **k: {})
    return data, store


def _config(**extra):
    base = dict(provider="自定义中转站", model="m1", api_key="k1",
                base_url="http://127.0.0.1:9/v1", target_lang="简体中文")
    base.update(extra)
    return adapter.EngineConfig(**base)


# ---------------------------------------------------------------- 窗口 -------

def test_settings_is_a_non_modal_window_with_titled_pages(app):
    dialog = dialogs.EngineDialog(_config(), _SyncRunner())
    from PySide6.QtCore import Qt
    from folith.qt import platform_ui
    assert bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == platform_ui.is_macos()
    assert not dialog.isModal()
    assert [key for key, _icon, _label in dialog.PAGES] == [
        "general", "models", "vision", "defaults", "reading", "storage", "about"]
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


def test_export_excludes_vision_credentials_and_raw_forensics(isolated, tmp_path):
    """CX-06 §1/§3：视觉密钥与可能回显密钥的原始取证都不进备份。

    对应证据 ``backup_members_with_model_keys_by_role`` 逐项：
    ``ocr_online_config.json``、``document_understanding/*-failure.json``、
    ``failures.jsonl``、``.performance/*/summary.json``、
    ``runtime_technical.log``（外加一直排除的 ``provider_config.json``）。
    任务数据本身照带；README 提示恢复后重填密钥，且不再写本机绝对路径。
    """
    data, _store = isolated
    _seed(data)
    job = data / "0123456789abcdef"
    forensics = job / "document_understanding"
    forensics.mkdir()
    (forensics / "0-failure.json").write_text('{"error": "api_key secret-key echoed"}')
    (forensics / "failures.jsonl").write_text('{"at": 1, "error": "secret-key"}\n')
    (job / "runtime_technical.log").write_text("RuntimeError: relay said secret-key\n")
    perf = job / ".performance" / "runs" / "run_1"
    perf.mkdir(parents=True)
    (perf / "summary.json").write_text('{"errors": [{"message": "secret-key"}]}')
    (data / "ocr_online_config.json").write_text('{"api_key": "secret-key"}')
    (job / "source.txt").write_text("document bytes")

    result = support.export_all_data(tmp_path / "backup2")
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
        blob = b"".join(archive.read(name) for name in names
                        if not name.endswith("README.txt"))
        readme = archive.read("Folith-data/README.txt").decode("utf-8")

    # 任务数据照带：备份的用途不变。
    assert "Folith-data/0123456789abcdef/state.json" in names
    assert "Folith-data/0123456789abcdef/source.txt" in names
    # 凭据文件与原始取证全部排除。
    assert not any("ocr_online_config" in name for name in names)
    assert not any("provider_config" in name for name in names)
    assert not any(name.endswith("-failure.json") for name in names)
    assert not any(name.endswith("failures.jsonl") for name in names)
    assert not any("runtime_technical.log" in name for name in names)
    assert not any("/.performance/" in name for name in names)
    # 任何成员里都搜不到密钥。
    assert b"secret-key" not in blob
    # README：恢复后要重填密钥，且没有本机绝对路径。
    assert "重新填写" in readme
    assert str(data) not in readme and str(tmp_path) not in readme


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
    assert any(text == "展开 / 收起原页或原位置" for _m, text, _k in dialog.rows)


def test_application_dir_is_the_folder_upgrades_rewrite(tmp_path):
    from folith.qt import storage_location

    assert storage_location.application_dir(frozen=False) is None
    windows_exe = tmp_path / "Programs" / "Folith" / "Folith.exe"
    assert storage_location.application_dir(str(windows_exe), frozen=True) == \
        windows_exe.parent.resolve()
    mac_exe = tmp_path / "Applications" / "Folith.app" / "Contents" / "MacOS" / "Folith"
    assert storage_location.application_dir(str(mac_exe), frozen=True) == \
        (tmp_path / "Applications" / "Folith.app").resolve()


def test_data_folder_inside_the_application_folder_is_detected(tmp_path):
    from folith.qt import storage_location

    app_dir = (tmp_path / "Folith").resolve()
    assert storage_location.inside_application_dir(app_dir, app_dir)
    assert storage_location.inside_application_dir(app_dir / "data", app_dir)
    assert not storage_location.inside_application_dir(tmp_path / "Folith data", app_dir)
    assert not storage_location.inside_application_dir(tmp_path, app_dir)


def test_changing_location_refuses_the_application_folder(app, isolated, tmp_path, monkeypatch):
    from folith.qt import bootstrap, settings_pages, storage_location

    app_dir = (tmp_path / "Programs" / "Folith").resolve()
    chosen = app_dir / "data"
    chosen.mkdir(parents=True)
    saved = []
    monkeypatch.setattr(bootstrap, "automation", lambda: False)
    monkeypatch.setattr(storage_location, "application_dir", lambda *a, **k: app_dir)
    monkeypatch.setattr(storage_location, "set_saved_data_dir", saved.append)
    monkeypatch.setattr(settings_pages.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(chosen)))
    page = settings_pages.StoragePage(_SyncRunner())
    page._change_location()
    assert saved == []
    assert "应用文件夹" in page.status.text()

    outside = tmp_path / "Folith data"
    outside.mkdir()
    monkeypatch.setattr(settings_pages.QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(outside)))
    page._change_location()
    assert saved == [str(outside)]


def test_cleanup_list_names_the_data_folder_in_use(isolated, tmp_path):
    from folith.desktop import paths as desktop_paths
    from folith.qt import settings_pages

    moved = tmp_path / "elsewhere" / "Folith data"
    logs = tmp_path / "logs"
    lines = settings_pages.cleanup_paths(str(moved), str(logs))
    assert lines[:2] == [str(moved), str(logs)]
    assert len(lines) == 3
    default = settings_pages.cleanup_paths(
        str(desktop_paths.default_data_dir()), str(logs))[0]
    expected = desktop_paths.default_data_dir()
    assert default == str(expected if sys.platform == "win32" else expected.parent)


def test_export_replaces_legacy_error_text_with_a_marker(isolated, tmp_path):
    """CX-11 §2：升级前任务里的指定错误字段在导出副本里替换为占位符。

    旧构建把提供商原始异常写进任务错误字段（可能回显密钥、正文和路径），
    事后无法识别，所以这些字段打包时不带原文；批号、偏移、事件标识等恢复
    任务所需的结构原样保留。
    """
    data, _store = isolated
    job = data / "0123456789abcdef"
    job.mkdir()
    (job / "state.json").write_text(json.dumps({
        "p1_done": False,
        "translation_failures": [
            {"batch": 0, "offset": 0, "segment_count": 1, "recovered": False,
             "reason": "批次翻译失败（1 段）：Error code: 400 - relay said secret-key"}],
    }, ensure_ascii=False), encoding="utf-8")
    (job / "events.jsonl").write_text(json.dumps({
        "batch": 0, "offset": 0, "phase": "translation_protocol_failed",
        "reason": "raw relay body with secret-key and /srv/relay/fixture/scan.png",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (job / "runtime_state.json").write_text(json.dumps({
        "status": "failed",
        "error": {"type": "RuntimeError",
                  "message": "步骤失败：批次翻译失败（1 段）：… secret-key …"},
        "last_event": "步骤失败：批次翻译失败（1 段）：… secret-key …",
        "events": [{"event": "job_failed", "category": "error",
                    "message": "步骤失败：… secret-key …"},
                   {"event": "translation", "category": "progress",
                    "message": "正在翻译第 1-4 段（无敏感内容）"}],
    }, ensure_ascii=False), encoding="utf-8")
    (job / "runtime_events.jsonl").write_text(json.dumps({
        "event": "job_failed", "category": "error",
        "message": "步骤失败：… secret-key …"}, ensure_ascii=False) + "\n",
        encoding="utf-8")

    result = support.export_all_data(tmp_path / "backup3")
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
        blob = b"".join(archive.read(name) for name in names)
        state = json.loads(archive.read("Folith-data/0123456789abcdef/state.json"))
        segment_events = [json.loads(line) for line in
                          archive.read("Folith-data/0123456789abcdef/events.jsonl")
                          .decode("utf-8").splitlines() if line.strip()]
        runtime = json.loads(archive.read("Folith-data/0123456789abcdef/runtime_state.json"))
        runtime_events = [json.loads(line) for line in
                          archive.read("Folith-data/0123456789abcdef/runtime_events.jsonl")
                          .decode("utf-8").splitlines() if line.strip()]
    # 任何成员里都搜不到旧错误文本。
    assert b"secret-key" not in blob
    # 指定字段替换为占位符，恢复所需结构保留。
    failure = state["translation_failures"][0]
    assert failure["reason"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert failure["batch"] == 0 and failure["offset"] == 0
    assert segment_events[0]["reason"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert segment_events[0]["phase"] == "translation_protocol_failed"
    assert runtime["error"]["message"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert runtime["last_event"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert runtime["events"][0]["message"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert runtime["events"][1]["message"] == "正在翻译第 1-4 段（无敏感内容）", \
        "进度类事件不受影响"
    assert runtime_events[0]["message"] == support.EXPORT_CLEANED_ERROR_TEXT
    # 原文件不动（导出的只是副本）。
    assert "secret-key" in (job / "state.json").read_text(encoding="utf-8")


def test_export_excludes_retained_copies_of_credential_files(isolated, tmp_path):
    """CX-11 §3：凭据文件的 .bak/.old 副本不进备份；其它文件的副本照带。"""
    data, _store = isolated
    _seed(data)
    for name in ("provider_config.json.bak", "provider_config.json.old",
                 "ocr_online_config.json.bak", "ocr_online_config.json.old"):
        (data / name).write_text('{"api_key": "secret-key"}', encoding="utf-8")
    (data / "notes.txt.bak").write_text("an ordinary operator backup", encoding="utf-8")
    result = support.export_all_data(tmp_path / "backup4")
    with zipfile.ZipFile(result["path"]) as archive:
        names = archive.namelist()
        blob = b"".join(archive.read(name) for name in names)
    assert not any("provider_config.json" in name for name in names)
    assert not any("ocr_online_config.json" in name for name in names)
    assert "Folith-data/notes.txt.bak" in names, "凭据文件名之外的 .bak 不受影响"
    assert b"secret-key" not in blob


def test_export_replaces_torn_legacy_lines_with_an_omission_record(
        isolated, tmp_path):
    """CX-12 §2：追加式事件日志的末行可能写了一半——解析失败的行不照抄。

    截断的旧失败行可能带着升级前写进的回显，事后无法脱敏；导出副本换成
    可读的省略记录，原文件留在本机。
    """
    data, _store = isolated
    job = data / "0123456789abcdef"
    job.mkdir()
    good = json.dumps({"phase": "translation_protocol_failed",
                       "reason": "升级前的原始错误文本带 secret-key"})
    torn = ('{"phase": "translation_protocol_failed", "reason": "raw relay '
            'secret-key')  # 写到一半的末行
    (job / "events.jsonl").write_text(good + "\n" + torn, encoding="utf-8")
    (job / "state.json").write_text("{}", encoding="utf-8")

    result = support.export_all_data(tmp_path / "backup5")
    with zipfile.ZipFile(result["path"]) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist())
        rows = [json.loads(line) for line in
                archive.read("Folith-data/0123456789abcdef/events.jsonl")
                .decode("utf-8").splitlines() if line.strip()]
    assert b"secret-key" not in blob
    assert rows[0]["reason"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert rows[1]["event"] == support.EXPORT_OMITTED_LINE_EVENT
    assert rows[1]["message"] == support.EXPORT_OMITTED_LINE_TEXT
    # 原文件不动：截断行仍在本机，供操作者自己决定。
    assert torn in (job / "events.jsonl").read_text(encoding="utf-8")


def test_export_keeps_normal_event_reasons(isolated, tmp_path):
    """CX-12 应修：只有失败类事件可能带模型回包，正常诊断文案照带。"""
    data, _store = isolated
    job = data / "0123456789abcdef"
    job.mkdir()
    lines = [
        {"phase": "journal_baseline", "reason": "快照基线 40 段"},
        {"phase": "clean_deterministic", "reason": "确定性清理完成"},
        {"phase": "first_batch", "reason": "首批 4 段已提交"},
        {"phase": "loop_exit", "reason": "批次循环退出"},
        {"phase": "translation_protocol_failed", "reason": "原始回包 secret-key"},
    ]
    (job / "events.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8")
    (job / "state.json").write_text("{}", encoding="utf-8")
    result = support.export_all_data(tmp_path / "backup6")
    with zipfile.ZipFile(result["path"]) as archive:
        rows = [json.loads(line) for line in
                archive.read("Folith-data/0123456789abcdef/events.jsonl")
                .decode("utf-8").splitlines() if line.strip()]
    reasons = {row["phase"]: row["reason"] for row in rows}
    assert reasons["journal_baseline"] == "快照基线 40 段"
    assert reasons["clean_deterministic"] == "确定性清理完成"
    assert reasons["first_batch"] == "首批 4 段已提交"
    assert reasons["loop_exit"] == "批次循环退出"
    assert reasons["translation_protocol_failed"] == support.EXPORT_CLEANED_ERROR_TEXT


def test_export_cleans_legacy_profile_and_knowledge_failures(isolated, tmp_path):
    """旧任务里画像失败的两份副本和知识提取失败要脱敏，正常提示与计数保留。"""
    data, _store = isolated
    job = data / "0123456789abcdef"
    job.mkdir()
    secret = "secret-key"
    raw_profile = (
        "文档画像失败（Error code: 400 - {\"api_key\": \"" + secret
        + "\"}），已跳过；可在 UI 中人工填写。")
    quota = (
        "文档画像失败（该连接的本月用量额度已用尽，等一会儿不会恢复；"
        "请更换连接或提升额度。），已跳过；可在 UI 中人工填写。")
    hint = "文档画像提示：样本较短，分节可能不完整"
    structured = (
        "文档画像失败（class=unknown; http=400; error=Remote; "
        "stage=document_profile），已跳过；可在 UI 中人工填写。")
    raw_knowledge = "知识反馈调用失败：Error code: 400 - body " + secret
    local_knowledge = "知识反馈返回不是 JSON 数组"
    echoed = (
        "文档画像失败（上游返回 API 请求失败，api_key=" + secret
        + "），已跳过；可在 UI 中人工填写。")
    echoed_knowledge = "知识反馈调用失败：API 请求失败 " + secret
    state = {
        "profile_warnings": [raw_profile, quota, hint, structured, echoed],
        "warnings": [raw_profile, quota, hint, "术语表已更新", echoed],
        "knowledge_feedback_failures": 2,
        "knowledge_events": [
            {"type": "extract_failed", "batch": 3, "offset": 12,
             "reason": raw_knowledge},
            {"type": "extract_failed", "batch": 4, "offset": 16,
             "reason": local_knowledge},
            {"type": "extract_failed", "batch": 5, "offset": 20,
             "reason": echoed_knowledge},
            {"type": "entity_observation", "batch": 3, "offset": 12,
             "source": "术语", "observed_target": "term",
             "reason": "观察到的译法"},
        ],
    }
    (job / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    original = (job / "state.json").read_text(encoding="utf-8")

    result = support.export_all_data(tmp_path / "backup7")
    with zipfile.ZipFile(result["path"]) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist())
        exported = json.loads(archive.read(
            "Folith-data/0123456789abcdef/state.json"))
    assert secret.encode() not in blob
    assert exported["profile_warnings"] == [
        support.EXPORT_CLEANED_ERROR_TEXT, quota, hint, structured,
        support.EXPORT_CLEANED_ERROR_TEXT]
    assert exported["warnings"] == [
        support.EXPORT_CLEANED_ERROR_TEXT, quota, hint, "术语表已更新",
        support.EXPORT_CLEANED_ERROR_TEXT]
    failed = exported["knowledge_events"]
    assert failed[0]["reason"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert failed[0]["batch"] == 3 and failed[0]["offset"] == 12
    assert failed[1]["reason"] == local_knowledge
    assert failed[1]["batch"] == 4 and failed[1]["offset"] == 16
    assert failed[2]["reason"] == support.EXPORT_CLEANED_ERROR_TEXT
    assert failed[2]["batch"] == 5 and failed[2]["offset"] == 20
    assert failed[3]["type"] == "entity_observation"
    assert failed[3]["reason"] == "观察到的译法"
    assert failed[3]["source"] == "术语"
    assert exported["knowledge_feedback_failures"] == 2
    assert len(exported["profile_warnings"]) == 5
    assert len(exported["warnings"]) == 5
    assert original == (job / "state.json").read_text(encoding="utf-8")
    assert secret in original


def test_profile_warnings_do_not_keep_the_model_section_id(isolated, tmp_path):
    """章节警告只用本地序号；历史警告里的模型 ID 在两份列表和 ZIP 中都换成占位。"""
    from folith import models

    data, _store = isolated
    job = data / "0123456789abcdef"
    job.mkdir()
    # 发布扫描认源码里连续的 sk- 长串。运行时再拼，扫描看不到字面量。
    secret = "sk" + "-" + "x" * 24
    profile = models.normalize_document_profile({
        "domain": "law",
        "confidence": 0.9,
        "sections": [
            {"section_id": secret, "start_segment": 0, "end_segment": 2},
            {"section_id": "also-" + secret, "start_segment": 3, "end_segment": 4,
             "topic": "kept"},
        ],
    })
    problems = models.validate_document_profile(profile)
    assert problems == ["章节 1 缺少 topic/domain"]
    assert secret not in "\n".join(problems)
    fresh = f"文档画像提示：{problems[0]}"
    historical_hint = f"文档画像提示：章节 {secret} 缺少 topic/domain"
    historical_failure = (
        "文档画像失败（画像缺少关键字段：章节 " + secret
        + " 缺少 topic/domain），已跳过；可在 UI 中人工填写。")
    profile_warnings = [fresh, historical_hint, historical_failure]
    warnings = [fresh, historical_hint, historical_failure, "术语表已更新"]
    (job / "state.json").write_text(json.dumps({
        "profile_warnings": profile_warnings,
        "warnings": warnings,
    }, ensure_ascii=False), encoding="utf-8")

    result = support.export_all_data(tmp_path / "backup-chapter-id")
    with zipfile.ZipFile(result["path"]) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist())
        exported = json.loads(archive.read(
            "Folith-data/0123456789abcdef/state.json"))
    placeholder = "章节（序号已省略） 缺少 topic/domain"
    assert secret.encode() not in blob
    assert len(exported["profile_warnings"]) == len(profile_warnings)
    assert len(exported["warnings"]) == len(warnings)
    assert placeholder in exported["profile_warnings"][1]
    assert exported["profile_warnings"][1].startswith("文档画像提示：")
    assert exported["profile_warnings"][2].startswith("文档画像失败（")
    assert placeholder in exported["profile_warnings"][2]
    assert exported["profile_warnings"][2] != support.EXPORT_CLEANED_ERROR_TEXT
    assert placeholder in exported["warnings"][1]
    assert placeholder in exported["warnings"][2]
    assert exported["warnings"][3] == "术语表已更新"
    joined = "\n".join(exported["profile_warnings"] + exported["warnings"])
    assert secret not in joined


def test_export_scrubs_every_credential_echo_across_all_fields(isolated, tmp_path, monkeypatch):
    """CX-20：画像/术语 scope/语义单元/标注错误/概要里的凭据都从整包里擦掉。"""
    data, _store = isolated
    job = data / "c0ffee1234567890"
    job.mkdir()
    secret = "sk" + "-" + "z" * 24  # 运行时拼，源码无字面量
    # 当前连接里确有这个凭据值（供导出层按值擦除）。
    monkeypatch.setattr(core, "load_provider_config", lambda: {
        "provider": "relay", "model": "m", "api_key": secret, "connections": [],
    })
    monkeypatch.setattr(core, "load_ocr_online_config", lambda *a, **k: {})
    state = {
        "document_profile": {
            "domain": "law", "confidence": 0.9,
            "sections": [
                {"section_id": "intro", "start_segment": 0, "end_segment": 2,
                 "topic": "引言"},
                {"section_id": secret, "start_segment": 3, "end_segment": 4},
            ],
        },
        "glossary": [{"term": "x", "scope": "section:" + secret}],
        "auto_term_entries": [{"term": "y", "scope": "section:" + secret}],
        "semantic_units": [{"source_range_id": secret, "unit_id": "u1"}],
        "annotation_batches": [{"error": "boom " + secret + " /Users/a/secret.docx"}],
        "understanding_warnings": ["摘要失败 " + secret],
        "document_synopsis": {"summary": "s", "warning": "概要 " + secret},
        "profile_warnings": [], "warnings": [],
    }
    (job / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    (job / "document_synopsis.json").write_text(
        json.dumps({"summary": "s", "warning": "概要 " + secret}, ensure_ascii=False),
        encoding="utf-8")
    (job / "semantic_units.json").write_text(
        json.dumps([{"source_range_id": secret}], ensure_ascii=False), encoding="utf-8")

    # 一个不符合任何内置密钥形状的合成凭据（只能靠"按值擦除"兜住）。
    blobby = "relay" + "-" + "q" * 18
    monkeypatch.setattr(core, "load_provider_config", lambda: {
        "provider": "relay", "model": "m", "api_key": blobby,
        "connections": [{"api_key": secret}],
    })
    state["document_synopsis"] = {"summary": "s", "warning": "概要 " + blobby}
    (job / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    result = support.export_all_data(tmp_path / "backup-cx20")
    with zipfile.ZipFile(result["path"]) as archive:
        blob = b"".join(archive.read(name) for name in archive.namelist())
    assert secret.encode() not in blob, "合成凭据仍在备份包里"
    assert blobby.encode() not in blob, "非形状合成凭据仍在备份包里"
    # 画像的普通字段保留（只擦凭据，不改结构）。
    with zipfile.ZipFile(result["path"]) as archive:
        exported = json.loads(archive.read("Folith-data/c0ffee1234567890/state.json"))
    assert exported["document_profile"]["sections"][0]["section_id"] == "intro"
    assert len(exported["document_profile"]["sections"]) == 2


def test_recovered_old_task_keeps_section_terms_by_original_id(isolated):
    """CX-20 回归：不重写 section_id，旧任务的章节术语按原 ID 恢复后仍生效。"""
    from folith import models

    # 归一化保留模型给的 section_id（revert 后行为）。
    profile = models.normalize_document_profile({
        "domain": "law", "confidence": 0.9,
        "sections": [{"section_id": "intro", "start_segment": 0, "end_segment": 2,
                      "topic": "引言"}],
    })
    assert profile["sections"][0]["section_id"] == "intro"
    # 术语 scope 用 section:intro，与画像一致 -> 匹配成功。
    term = {"term": "合同", "scope": "section:intro"}
    scope = (term.get("scope") or "").strip()
    section = profile["sections"][0]
    assert scope.startswith("section:") and section.get("section_id") == scope[8:]
