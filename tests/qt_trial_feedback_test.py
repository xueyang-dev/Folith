"""Trial feedback 41: persistence and runtime behavior behind the new controls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
import core
from folith.qt import adapter, dialogs, connection_status, theme, views
from folith.qt.settings_pages import ConnectionPanel
from folith.model_connections import effective_config


class Runner:
    def submit(self, fn, *args, on_done=None, on_error=None, **kwargs):
        try:
            result = fn(*args)
        except Exception as exc:
            if on_error:
                on_error(str(exc), "")
            else:
                raise
        else:
            if on_done:
                on_done(result)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "load_ocr_online_config", lambda: {})
    store = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(connection_status, "_settings", lambda: store)
    return tmp_path


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def profile(key="translation", **extra):
    return {"id": key, "name": key, "provider": "OpenAI", "model": key,
            "models": [key], "api_key": "fake-secret", "base_url": "", **extra}


def test_old_translation_review_and_vision_migrate_without_losing_keys(isolated, monkeypatch):
    core.save_provider_config("OpenAI", "translate", "translation-secret", reviewer={
        "provider": "DeepSeek", "model": "review", "api_key": "review-secret"})
    vision = {"provider": "OpenAI", "model": "vision", "api_key": "vision-secret", "base_url": ""}
    monkeypatch.setattr(core, "load_ocr_online_config", lambda: vision)
    config = adapter.EngineConfig.load()
    assert len(config.connections) == 3
    assert set(config.connection_roles) == {"translation", "reviewer", "vision"}
    config.save()
    if os.name != "nt":  # Windows permissions are ACLs, not POSIX mode bits.
        assert core.provider_config_path().stat().st_mode & 0o777 == 0o600
    again = adapter.EngineConfig.load()
    assert again.connections == config.connections
    assert again.connection_roles == config.connection_roles
    assert again.reviewer_api_key == "review-secret"


def test_environment_auth_remains_out_of_saved_library_and_does_not_duplicate(isolated, monkeypatch, app):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    p = profile(api_key="")
    cfg = effective_config(adapter.EngineConfig(), [p], {"translation": p["id"]})
    assert cfg.api_key == "environment-secret"
    cfg.save()
    saved = json.loads(core.provider_config_path().read_text(encoding="utf-8"))
    assert "environment-secret" not in json.dumps(saved)
    loaded = adapter.EngineConfig.load()
    assert loaded.api_key == "environment-secret"
    dialog = dialogs.EngineDialog(loaded, Runner())
    assert len(dialog.connection_library.profiles) == 1
    dialog.close()


def test_multiple_connections_survive_save_edit_delete_and_role_selection(isolated, app):
    cfg = effective_config(adapter.EngineConfig(), [profile()], {"translation": "translation"})
    dialog = dialogs.EngineDialog(cfg, Runner())
    library = dialog.connection_library
    library.name.setText("Review API")
    dialog.provider_box.setCurrentText("DeepSeek")
    dialog.model_box.setCurrentText("review-model")
    dialog.key_edit.setText("review-secret")
    library.save()
    assert len(library.profiles) == 2
    reviewer = library.profiles[1]["id"]
    selector = library.selectors["reviewer"]
    selector.setCurrentIndex(selector.findData(reviewer))
    assert library.persist()
    reloaded = adapter.EngineConfig.load()
    assert reloaded.model == "translation"
    assert reloaded.reviewer_model == "review-model"
    assert reloaded.reviewer_api_key == "review-secret"
    library.delete(reviewer)
    remaining = adapter.EngineConfig.load()
    assert len(remaining.connections) == 1
    assert remaining.reviewer_mode == "same"
    library.delete("translation")
    assert not adapter.EngineConfig.load().api_key
    assert not library.profiles
    dialog.close()


def test_custom_connection_validates_id_and_restores_saved_models(isolated, app):
    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    library = dialog.connection_library
    library.kind.set_value("custom")
    library.change_kind("custom")
    dialog.base_edit.setText("https://example.test/v1/chat/completions")
    dialog.model_box.setCurrentText("model-a")
    library.save()
    assert not library.profiles
    library.provider_id.setText("my-api")
    library.models.setText("model-a, model-b")
    library.save()
    saved = adapter.EngineConfig.load().connections[0]
    assert saved["base_url"] == "https://example.test/v1"
    assert saved["provider_id"] == "my-api"
    assert saved["protocol"] == "openai_compat"
    library.edit(saved["id"])
    assert dialog.model_box.findText("model-b") >= 0
    dialog.close()


def test_vision_selects_saved_credentials_without_a_second_form(isolated, app):
    cfg = effective_config(adapter.EngineConfig(), [profile(), profile("vision")],
                           {"translation": "translation"})
    dialog = dialogs.EngineDialog(cfg, Runner())
    combo = dialog.vision_connection_box
    combo.setCurrentIndex(combo.findData("vision"))
    form = dialog._vision_form()
    assert form["model"] == "vision" and form["api_key"] == "fake-secret"
    assert dialog._collect().connection_roles["vision"] == "vision"
    assert not hasattr(dialog, "vision_key_edit")
    dialog.close()


def test_uninstalled_local_ocr_is_disabled_in_settings(isolated, app, monkeypatch):
    monkeypatch.setattr(core, "_find_tesseract", lambda: "")
    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    index = dialog.ocr_backend_box.findData("tesseract")
    assert not dialog.ocr_backend_box.model().item(index).isEnabled()
    assert dialog.ocr_backend_box.currentData() == "online"
    dialog.close()


@pytest.mark.parametrize("saved_pipeline", [False, True])
def test_missing_local_ocr_fallback_reaches_runtime_and_is_recorded(isolated, monkeypatch, saved_pipeline):
    state = {"filename": "scanned.pdf", "ocr_backend": "tesseract"}
    if saved_pipeline:
        state["pipeline_config"] = {"ocr_backend": "tesseract"}
    monkeypatch.setattr(adapter, "load_state", lambda _job: state)
    monkeypatch.setattr(core, "_find_tesseract", lambda: "")
    monkeypatch.setattr(core, "mutate_job_state", lambda _job, fn: fn(state))
    events = []
    monkeypatch.setattr(core, "_append_runtime_event", lambda job, event, **kw: events.append(event))
    captured = []
    monkeypatch.setattr(adapter._commands, "start_job", lambda job, sources, **kw:
                        captured.append(adapter._resolve_settings(sources.with_state(state))) or True)
    cfg = adapter.EngineConfig(provider="OpenAI", model="model", api_key="fake", ocr_backend="tesseract")
    assert adapter.start_job("job", "scanned.pdf", b"source", cfg)
    assert captured[0].values["ocr_backend"] == "online"
    assert state["ocr_fallback_notice"] in events[0]["message"]
    assert cfg.ocr_backend == "tesseract"  # Global preference remains intact.
    monkeypatch.setattr(core, "_find_tesseract", lambda: "/test/tesseract")
    adapter.start_job("job", "scanned.pdf", b"source", cfg)
    assert captured[1].values["ocr_backend"] == "tesseract"
    assert "ocr_fallback_notice" not in state


@pytest.mark.parametrize("mode", ["快速", "标准", "学术增强"])
def test_default_mode_preselects_preparation_without_starting(mode, isolated):
    cfg = adapter.EngineConfig(default_translation_mode=mode)
    view = adapter.task_setup_view(cfg, "job", {"filename": "draft.docx"})
    assert view["strategy_preset"] == mode
    previous = {"filename": "draft.docx", "strategy_preset": "标准"}
    assert adapter.task_setup_view(cfg, "job", previous)["strategy_preset"] == "标准"


def test_connection_card_collapses_inherited_roles_and_labels_estimates(isolated, app, monkeypatch):
    from folith import usage
    monkeypatch.setattr(usage, "_DAILY_LOG", {"path": str(isolated / "usage-daily.json")})
    usage.record_call({}, role="translate", provider="OpenAI", model="m",
                      system_prompt="x" * 800, user_prompt="", completion="y" * 400)
    usage.record_call({}, role="translate", provider="OpenAI", model="m", system_prompt="",
                      user_prompt="", usage={"prompt_tokens": 600, "completion_tokens": 300,
                                             "total_tokens": 900})
    cfg = adapter.EngineConfig(provider="OpenAI", model="current-model", api_key="fake")
    panel = ConnectionPanel(Runner(), lambda: cfg)
    assert not panel._rows["translation"]["container"].isHidden()
    assert panel._rows["reviewer"]["container"].isHidden()
    assert panel._rows["vision"]["container"].isHidden()
    assert panel._rows["translation"]["title"].text() == "OpenAI"
    assert panel._rows["translation"]["message"].text().startswith("current-model")
    assert "1,200" in panel.usage.text() and "300" in panel.usage_note.text()


def test_home_counts_once_and_attention_link_filters_actual_tasks(isolated, app):
    home = views.HomeView()
    rows = [adapter.JobRow(str(i), f"doc{i}.pdf", None, "English", "待处理", "", "idle", 2, "", False,
                           attention_count=i) for i in range(2)]
    home.set_rows(rows)
    assert home.stats.isHidden()
    assert home.status_filter.button("all").text() == "全部"
    assert 'href="attention"' in home.stats_line.text()
    home.stats_line.linkActivated.emit("attention")
    assert [row.job_id for row in home._visible_rows()] == ["1"]


def test_saved_reviewer_never_borrows_the_translation_api_key(isolated, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    review = profile("review", provider="DeepSeek", api_key="")
    cfg = effective_config(adapter.EngineConfig(), [profile(), review],
                           {"translation": "translation", "reviewer": "review"})
    assert cfg.connection_problem() == "审校模型缺少 API 密钥"
    assert connection_status.endpoint("reviewer", cfg)["api_key"] == ""
    cfg.save()
    loaded = adapter.EngineConfig.load()
    assert len(loaded.connections) == 2
    assert loaded.reviewer_api_key == ""
    assert loaded.connection_problem() == "审校模型缺少 API 密钥"


@pytest.mark.parametrize("size", [(1180, 780), (980, 640)])
def test_first_cover_fits_even_with_the_selection_toolbar(isolated, app, size):
    home = views.HomeView()
    home.resize(*size)
    rows = [adapter.JobRow(str(i), f"doc{i}.pdf", None, "English", "待处理", "", "idle", 2, "", False)
            for i in range(4)]
    home.set_rows(rows)
    home.show()
    home._select_job_from_card("0")
    for _ in range(5):
        app.processEvents()
    first = home._cards["0"]
    from PySide6.QtCore import QPoint
    bottom = first.mapTo(home.card_scroll.viewport(), QPoint(0, first.height())).y()
    assert bottom <= home.card_scroll.viewport().height()
    home.close()


def test_deleting_active_translation_retains_other_saved_apis(isolated, app):
    cfg = effective_config(adapter.EngineConfig(), [profile(), profile("spare")],
                           {"translation": "translation"})
    dialog = dialogs.EngineDialog(cfg, Runner())
    dialog.connection_library.delete("translation")
    reloaded = adapter.EngineConfig.load()
    assert reloaded.provider == "" and reloaded.api_key == ""
    assert [c["id"] for c in reloaded.connections] == ["spare"]
    dialog.close()


def test_saved_reviewer_uses_its_own_official_address_after_restart(isolated, monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    main = profile(base_url="https://relay.example.test/v1")
    review = profile("review")
    cfg = effective_config(adapter.EngineConfig(), [main, review],
                           {"translation": "translation", "reviewer": "review"})
    assert adapter.pipeline_kwargs(cfg)["reviewer_base_url"] == "https://api.openai.com/v1"
    cfg.save()
    again = adapter.EngineConfig.load()
    assert len(again.connections) == 2
    assert adapter.pipeline_kwargs(again)["reviewer_base_url"] == "https://api.openai.com/v1"


def test_explicit_preparation_choice_clears_previous_ocr_fallback(isolated):
    state = core.new_job_state("scan.pdf")
    state.update(ocr_backend="online", ocr_backend_requested="tesseract",
                 ocr_fallback_notice="local OCR unavailable")
    core.save_job_state("trial", state)
    adapter._commands.apply_task_setup("trial", {"ocr_backend": "online"})
    saved = core.load_job_state("trial")
    assert saved["ocr_backend"] == "online"
    assert "ocr_backend_requested" not in saved
    assert "ocr_fallback_notice" not in saved


def test_roles_can_use_different_models_from_one_saved_api(isolated, app, monkeypatch):
    monkeypatch.setattr(core, "load_ocr_online_config", lambda:
        json.loads(core.ocr_online_config_path().read_text(encoding="utf-8")) if core.ocr_online_config_path().exists() else {})
    p = profile(models=["translation", "review", "vision"])
    cfg = effective_config(adapter.EngineConfig(), [p], {"translation": p["id"]})
    dialog = dialogs.EngineDialog(cfg, Runner())
    library = dialog.connection_library
    library.selectors["reviewer"].setCurrentIndex(
        library.selectors["reviewer"].findData(p["id"]))
    library.model_selectors["reviewer"].setCurrentText("review")
    dialog.vision_connection_box.setCurrentIndex(dialog.vision_connection_box.findData(p["id"]))
    library.model_selectors["vision"].setCurrentText("vision")
    assert dialog._vision_form()["model"] == "vision"
    assert library.persist()
    reloaded = adapter.EngineConfig.load()
    assert len(reloaded.connections) == 1
    assert reloaded.model == "translation"
    assert reloaded.reviewer_model == "review"
    assert reloaded.reviewer_api_key == reloaded.api_key
    assert reloaded.connection_models["reviewer"] == "review"
    assert reloaded.connection_models["vision"] == "vision"
    assert core.load_ocr_online_config()["model"] == "vision"
    dialog.close()


def test_discovered_model_catalog_is_saved_with_the_api(isolated, app, monkeypatch):
    monkeypatch.setattr(adapter, "provider_models", lambda *args:
                        (True, ["first", "second"], "OK", {}))
    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    dialog._fetch_models()
    assert dialog.model_box.findText("second") >= 0
    dialog.model_box.setCurrentText("first")
    dialog.connection_library.save()
    assert adapter.EngineConfig.load().connections[0]["models"] == ["first", "second"]
    dialog.close()


def test_same_api_and_model_collapse_even_with_resolved_official_review_url(isolated, app):
    p = profile()
    cfg = effective_config(adapter.EngineConfig(), [p],
                           {"translation": p["id"], "reviewer": p["id"]})
    panel = ConnectionPanel(Runner(), lambda: cfg)
    assert panel._rows["reviewer"]["container"].isHidden()


# ---- 整改阶段 0（remediation-plan.md）------------------------------------------
def test_short_window_slims_the_continue_card_instead_of_hiding_it(isolated, app):
    home = views.HomeView()
    rows = [adapter.JobRow(str(i), f"doc{i}.pdf", None, "English", "翻译中", "", "running", 2,
                           "", True) for i in range(2)]
    home.set_rows(rows)
    home.resize(980, 640)
    home.show()
    for _ in range(5):
        app.processEvents()
    if home._continue_candidate() is not None:
        assert home.continue_card.isVisible(), "最常用的入口不能在小窗口里消失"
        assert not home.continue_paper.isVisible() and not home.continue_meta.isVisible()
    home.resize(1200, 900)
    for _ in range(5):
        app.processEvents()
    if home._continue_candidate() is not None:
        assert home.continue_paper.isVisible()
    home.close()


def test_long_labels_elide_and_keep_their_full_text(app):
    from folith.qt import style

    button = style.ElideButton("Terms and translation memory")
    button.show()          # 未显示的控件收不到 resizeEvent
    button.resize(120, 30)
    app.processEvents()
    assert button.text() == "Terms and translation memory"
    from PySide6.QtWidgets import QPushButton
    assert QPushButton.text(button).endswith("…")
    assert button.toolTip() == "Terms and translation memory"
    button.resize(600, 30)
    app.processEvents()
    assert QPushButton.text(button) == "Terms and translation memory" and button.toolTip() == ""


def test_every_interface_string_is_translated():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1]
                            / "scripts" / "qt_i18n_coverage.py"), "--strict"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    assert result.returncode == 0, result.stdout[-800:]


@pytest.mark.parametrize("language, expected", [("ru", "Фолит"), ("es", "Folith"), ("ar", "فوليث")])
def test_foreign_wordmark_is_a_short_name_and_a_short_tagline(language, expected, app, monkeypatch):
    from folith.qt import i18n
    monkeypatch.setattr(i18n, "language", lambda: language)
    home = views.HomeView()
    home._logo_theme = None
    home._refresh_logo()
    from PySide6.QtGui import QTextDocument
    document = QTextDocument()
    document.setHtml(home._logo_label.text())
    name, tagline = document.toPlainText().splitlines()
    assert name == expected, "只写名字的音译，不直译「译页」"
    assert len(tagline) <= 30
    home.close()


def test_daily_usage_tally_counts_estimates_separately(tmp_path, monkeypatch):
    from folith import usage
    monkeypatch.setattr(usage, "_DAILY_LOG", {"path": str(tmp_path / "daily.json")})
    usage.record_call({}, role="t", provider="p", model="m", system_prompt="x" * 40,
                      user_prompt="", completion="")
    usage.record_call({}, role="t", provider="p", model="m", system_prompt="", user_prompt="",
                      usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
    assert usage.today_usage() == {"total_tokens": 20, "estimated_tokens": 10}
    monkeypatch.setattr(usage, "_DAILY_LOG", {"path": None})
    assert usage.today_usage() == {"total_tokens": 0, "estimated_tokens": 0}


def test_settings_pickers_drive_the_saved_values(isolated, app):
    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    dialog.accent_picker.button("green").click()
    assert dialog.accent_box.currentData() == "green"
    dialog.mode_cards["快速"].click()
    assert dialog.default_mode_box.currentData() == "快速"
    dialog.default_mode_box.setCurrentIndex(dialog.default_mode_box.findData("学术增强"))
    assert dialog.mode_cards["学术增强"].isChecked() and not dialog.mode_cards["快速"].isChecked()
    dialog.ocr_mode_picker.button("off").click()
    assert dialog.ocr_mode_box.currentData() == "off"
    dialog.vision_option.click()
    assert dialog.ocr_backend_box.currentData() == "online"
    assert dialog.ocr_language_row.isHidden()
    dialog.close()


def test_connection_editor_is_a_sheet_and_the_page_has_one_save(isolated, app):
    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    library = dialog.connection_library
    page = dialog.pages["models"].widget()
    assert not page.isAncestorOf(dialog.key_edit), "添加连接的表单在单独的面板里"
    assert library.editor.isAncestorOf(dialog.key_edit)
    saves = [b for b in page.findChildren(QPushButton) if b.text() in ("测试并保存", "保存用途", "保存")]
    assert len(saves) == 1
    dialog.provider_box.setCurrentText("DeepSeek")
    library.models.setText("m-1, m-2")
    library.add_model()
    assert dialog.model_box.currentText() == "m-2"
    # #48：列表显示带输入类型标注；手动加的模型没有目录声明，如实「未知」。
    assert [library.model_list.item(i).text()
            for i in range(library.model_list.count())][-2:] == ["m-1 · 未知", "m-2 · 未知"]
    dialog.close()


def test_labels_looked_up_from_tables_are_translated():
    """The coverage script only sees literal tr() strings; labels kept in tables slip past it."""
    from folith.qt import connection_status, theme as theme_module
    from folith.qt.connection_library import ConnectionLibrary
    from folith.qt.settings_pages import ConnectionPanel
    keys = {theme_module.label(n) for n in (theme_module.FOLLOW_SYSTEM, "light", "dark")}
    keys |= {label for _key, label in theme_module.ACCENT_LABELS}
    keys |= {label for _key, _icon, label in dialogs.EngineDialog.PAGES}
    keys |= {label for label, _key, _icon in dialogs.EngineDialog.DEFAULT_MODES}
    keys |= {label for label, _key in dialogs.EngineDialog.MODE_STEPS}
    keys |= set(ConnectionPanel.STATE_TEXT.values()) | set(ConnectionPanel.ROLE_SHORT.values())
    keys |= set(ConnectionLibrary.STATE_TEXT.values()) | {l for _r, l in ConnectionLibrary.ROLES}
    keys |= set(ConnectionLibrary.INPUT_LABELS.values())
    keys |= set(connection_status.ROLE_LABELS.values())
    keys |= {label.split("（")[0] for label, _mode in adapter.OCR_MODE_CHOICES}
    root = Path(__file__).resolve().parents[1] / "folith" / "resources" / "locales"
    for language in ("en", "ru", "es", "ar"):
        catalog = json.loads((root / f"qt-{language}.json").read_text(encoding="utf-8"))
        missing = sorted(k for k in keys if catalog.get(k, k) == k)
        assert not missing, (language, missing)


def test_counts_are_singular_for_one_and_markdown_is_listed_once(app, monkeypatch):
    from folith.qt import i18n
    monkeypatch.setattr(i18n, "_state", {**i18n._state, "code": "en"})
    assert i18n.tr_n('{v0} 个任务', 1, v0=1) == "1 task"
    assert i18n.tr_n('{v0} 个任务', 3, v0=3) == "3 tasks"
    home = views.HomeView()
    formats = home.drop_tile._formats
    assert formats.count("MD") == 1 and "MARKDOWN" not in formats
    home.close()


def test_first_cover_fits_even_with_the_setup_banner(isolated, app):
    home = views.HomeView()
    home.set_engine_problem("no provider")
    rows = [adapter.JobRow(str(i), f"doc{i}.docx", None, "English", "翻译中", "", "running", 2,
                           "", True) for i in range(3)]
    home.set_rows(rows)
    home._set_view_mode("cards")
    home.resize(980, 640)
    home.show()
    for _ in range(10):
        app.processEvents()
    card = next(iter(home._cards.values()))
    viewport = home.card_scroll.viewport()
    bottom = card.mapTo(viewport, card.rect().bottomLeft()).y()
    assert bottom <= viewport.height(), (bottom, viewport.height())
    home.close()


def test_ampersand_in_a_sidebar_label_is_shown_not_eaten(app):
    from folith.qt import style
    button = style.ElideButton("Terms & memory")
    button.resize(600, 36)
    button.show()
    app.processEvents()
    assert QPushButton.text(button) == "Terms && memory"
    assert button.text() == "Terms & memory"
    button.close()


def test_stats_line_redraws_its_colours_after_a_theme_change(isolated, app):
    from folith.qt import style
    home = views.HomeView()
    home.show()
    rows = [adapter.JobRow(str(i), f"doc{i}.docx", None, "English", "翻译中", "", "running", 2,
                           "", True) for i in range(2)]
    style.apply_theme(app, "light"); views.set_current_theme("light")
    home.set_rows(rows)
    light = home.stats_line.text()
    assert style.html_color("text-secondary") in light
    style.apply_theme(app, "dark"); views.set_current_theme("dark")
    for _ in range(5):
        app.processEvents()
    assert style.html_color("text-secondary") in home.stats_line.text()
    assert "rgba" not in home.stats_line.text(), "Qt 富文本读不懂 rgba()"
    style.apply_theme(app, "light"); views.set_current_theme("light")
    home.close()


# ---- 试用反馈第三轮（2026-10-16 晚）：拖动窗口、页面间隙、设置窗口 -----------------
def test_window_drag_area_moves_the_window_but_not_through_buttons(app, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWindow
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

    from folith.qt import components

    moves = []
    monkeypatch.setattr(QWindow, "startSystemMove", lambda self: moves.append(self) or True)
    bar = QFrame()
    layout = QHBoxLayout(bar)
    button = QPushButton("x")
    layout.addWidget(button)
    layout.addStretch(1)
    bar.resize(400, 60)
    components.WindowDragArea(bar)
    bar.show()
    app.processEvents()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert moves == [], "a click on a button is still a click"
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(300, 30))
    assert len(moves) == 1, "an empty spot drags the window"
    bar.close()


def test_run_status_click_is_not_taken_for_a_window_drag(app, monkeypatch):
    from PySide6.QtGui import QWindow
    from PySide6.QtTest import QTest

    moves = []
    monkeypatch.setattr(QWindow, "startSystemMove", lambda self: moves.append(self) or True)
    bench = views.WorkbenchView()
    bench.resize(1400, 800)
    bench.show()
    bench.run_status.setVisible(True)
    app.processEvents()
    QTest.mouseClick(bench.run_status, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert moves == []
    assert bench.activity_popover.isVisible()
    bench.activity_popover.close()
    bench.close()


def test_sheet_keeps_equal_gaps_when_the_sidebar_is_collapsed(app):
    from folith.qt import components

    sheet = components.Sheet(QWidget())
    sheet.resize(600, 400)
    assert sheet.leading_gap() == 0
    sheet.set_leading_gap(components.Sheet.GAP)
    margins = sheet.layout().contentsMargins()
    assert margins.left() == margins.right() == components.Sheet.GAP
    assert sheet._sheet_rect().left() == components.Sheet.GAP


def test_settings_window_uses_platform_window_flags(app):
    from folith.qt import adapter, dialogs, platform_ui

    class Runner:
        def submit(self, *_a, **_k):
            return None

    dialog = dialogs.EngineDialog(adapter.EngineConfig(), Runner())
    assert bool(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == platform_ui.is_macos()
    assert not dialog.isModal()
    dialog.close()


# ---- 第四轮：交付文件名、操作台宽度 ---------------------------------------------
@pytest.mark.parametrize("source, asset, expected", [
    ("The Midnight Library.pdf", "bilingual.docx", "The Midnight Library_bilingual.docx"),
    ("论文_刘英.pdf", "terms.xlsx", "论文_刘英_terms.xlsx"),
    ("a:b.docx", "translation.docx", "a_b_translation.docx"),
    (r"C:\books\sample.docx", "translation.docx", "sample_translation.docx"),
    ("/books/sample.docx", "translation.docx", "sample_translation.docx"),
    ("", "bilingual.docx", "bilingual.docx"),
])
def test_delivered_files_are_named_after_the_source_document(source, asset, expected):
    assert adapter.delivery_file_name(source, asset) == expected


@pytest.mark.parametrize("language", ["zh-CN", "en", "ru"])
def test_segment_panel_is_never_clipped_sideways(app, language):
    """操作台左右永远完整：每个按钮都在可见宽度以内，只上下滚动。"""
    from folith.qt import i18n

    previous = i18n.language()
    i18n.set_language(language)
    try:
        bench = views.WorkbenchView()
        bench.show()
        for width in (1400, 1000):
            bench.resize(width, 800)
            app.processEvents()
            bench._layout_translation_columns()
            app.processEvents()
            inspector = bench.inspector_scroll.widget()
            assert bench.inspector_scroll.viewport().width() >= inspector.minimumSizeHint().width()
            clipped = [button.text() for button in inspector.findChildren(QPushButton)
                       if button.isVisible()
                       and button.mapTo(inspector, button.rect().topRight()).x() >= inspector.width()]
            assert clipped == [], (language, width, clipped)
        bench.close()
    finally:
        i18n.set_language(previous)


# ---- 后台闪烁：空闲轮询不重建、不重排 ---------------------------------------------
def test_repolish_skips_when_nothing_changed(app):
    from PySide6.QtWidgets import QLabel

    from folith.qt import style

    label = QLabel("x")
    label.setProperty("tone", "done")
    style.repolish(label)
    first = getattr(label, "_folith_polish_key", None)
    assert first is not None
    polished = []
    original = label.style().polish
    label.style().polish = lambda widget: polished.append(widget) or original(widget)
    style.repolish(label)
    assert polished == [], "same properties: no re-style, no repaint"
    label.setProperty("tone", "failed")
    style.repolish(label)
    assert label._folith_polish_key != first


def test_term_and_context_lists_are_not_rebuilt_with_the_same_content(app):
    from folith.qt import segment_assist

    terms = segment_assist.TermList()
    terms.set_terms([{"source": "a", "target": "b"}], {"a": 2})
    chip = terms.buttons[0]
    terms.set_terms([{"source": "a", "target": "b"}], {"a": 2})
    assert terms.buttons[0] is chip
    terms.set_terms([{"source": "a", "target": "c"}], {"a": 2})
    assert terms.buttons[0] is not chip

    context = segment_assist.ContextView()
    value = {"previous": {"index": 1, "source": "s", "target": "t"}}
    context.set_context(value)
    row = context.rows["previous"]
    context.set_context(dict(value))
    assert context.rows["previous"] is row


# ---- 2026-10-17 第六轮：暂停后继续、凸形卡片、术语左栏、RTL ---------------------------
def test_continue_on_a_paused_live_task_resumes_instead_of_starting(app, monkeypatch):
    """暂停的任务 worker 还活着、握着任务锁：「继续翻译」走 continue，不再走启动
    （启动要先写任务状态，被锁拒绝成「无法启动任务 · 任务正在后台处理」）。"""
    from folith.qt import window as window_module

    calls = []
    host = type("Host", (), {})()
    host._current_job_id = "job-paused"
    host._starting_jobs = set()
    host.continue_job = lambda: calls.append("continue")
    host._notice = lambda *a, **k: calls.append(("notice", a))
    monkeypatch.setattr(adapter, "job_is_alive", lambda job_id: True)
    monkeypatch.setattr(adapter, "start_job", lambda *a, **k: calls.append("start"))
    window_module.MainWindow.start_or_resume(host)
    assert calls == ["continue"]


def test_popover_draws_its_own_shadow_only(app):
    """系统窗口阴影按展开动画的第一帧算，刚展开时卡片外面套一圈灰框。"""
    from folith.qt import components

    popover = components.Popover("x", QWidget())
    assert popover.windowFlags() & Qt.WindowType.NoDropShadowWindowHint


def test_tabs_leave_room_around_the_progress_bar_and_the_shield(app):
    run = views.RunStatusGroup()
    left, top, right, _bottom = run.PADDING
    assert left - run.TAB_INSET >= 10 and right - run.TAB_INSET >= 10
    assert top - run.TAB_INSET >= 6
    tab = views.RoundTab(views.CheckStatusButton())
    left, top, right, _bottom = tab.PADDING
    assert min(left, top, right) - tab.TAB_INSET >= 5


def test_rtl_cells_are_laid_out_right_to_left(app):
    from folith.qt import segment_editor

    assert segment_editor.cell_direction("كلمة التحرير: مع اندماج (Terry Smith).") \
        == Qt.LayoutDirection.RightToLeft
    assert segment_editor.cell_direction("编者按：随着社会学") == Qt.LayoutDirection.LeftToRight
    assert segment_editor.cell_direction("") == Qt.LayoutDirection.LeftToRight
    from PySide6.QtWidgets import QPlainTextEdit

    edit = QPlainTextEdit()
    segment_editor.set_text_direction(edit, Qt.LayoutDirection.RightToLeft)
    option = edit.document().defaultTextOption()
    assert option.textDirection() == Qt.LayoutDirection.RightToLeft
    assert option.alignment() & Qt.AlignmentFlag.AlignRight


def _started_bench(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "terms0000000000001"
    state = core.new_job_state("a.docx")
    state.update(p1_done=True, paras=["Alpha.", "Beta."],
                 pairs=[{"source": "Alpha.", "target": "甲。"}, {"source": "Beta.", "target": ""}])
    core.save_job_state(job_id, state)
    bench = views.WorkbenchView()
    state = adapter.load_state(job_id)
    bench.load(job_id, adapter.EngineConfig(target_lang="简体中文"), state,
               adapter.segment_rows(job_id, state), [])
    return bench


def test_header_has_no_more_menu_and_a_details_button(app, tmp_path, monkeypatch):
    bench = _started_bench(tmp_path, monkeypatch)
    bench.resize(1400, 800)
    bench.show()
    app.processEvents()
    assert not bench.stage_bar.more_button.isVisible(), "nothing left in 更多 without academic mode"
    assert bench.details_button.isVisible()
    assert bench.check_tab.isVisible(), "检查与修复 is its only entry now"
    assert bench.run_status.isVisible(), "the run log opens from the status"
    bench.close()


def test_terms_open_in_the_left_column_not_a_whole_page(app, tmp_path, monkeypatch):
    bench = _started_bench(tmp_path, monkeypatch)
    bench.resize(1400, 800)
    bench.show()
    app.processEvents()
    assert bench.terms_button.isVisible()
    assert not hasattr(bench, "source_button") or not bench.source_button.isVisible()
    assert bench.tabs.indexOf(bench.terms_panel) == -1, "术语 is no longer a page"
    bench.show_page("terms")
    bench._layout_translation_columns()
    app.processEvents()
    assert bench.terms_open() and not bench.source_open()
    assert bench.terms_button.isChecked()
    assert bench.left_stack.currentWidget() is bench.terms_side
    split = bench.translation_split
    sizes = split.sizes()
    assert 0 < sizes[split.indexOf(bench.source_clip)] < bench.width() // 2
    assert bench.current_page_id() == "translate"
    bench.terms_close_button.click()
    bench._layout_translation_columns()
    assert not bench.terms_open()
    assert split.sizes()[split.indexOf(bench.source_clip)] == 0
    bench.close()


# ---- 第七轮：下拉卡片精简、圆顶「凸」字、任务详情同一种卡片 -------------------------
def test_round_tab_top_is_concentric_with_its_button(app):
    tab = views.RoundTab(views.CheckStatusButton())
    left, top, right, _bottom = tab.PADDING
    assert left == top == right, "equal padding keeps the semicircle concentric with the button"


def test_check_card_has_no_overview_and_details_unfold_from_the_info_button(app, tmp_path, monkeypatch):
    bench = _started_bench(tmp_path, monkeypatch)
    bench.resize(1400, 900)
    bench.show()
    app.processEvents()
    bench.followup_panel.apply({"failures": [{"start": 0, "end": 1, "count": 2, "reason": "x"}]})
    assert bench.followup_panel.overview.isHidden(), "the shield already carries the count"
    assert bench.details_tab.isVisible()
    assert bench.popover_height() >= bench.window().height() // 2
    bench.open_details()
    app.processEvents()
    assert bench.details_popover.isVisible()
    assert bench.details_tab.property("tethered") is True
    assert bench.details_popover.height() >= bench.popover_height() - 1
    bench.details_popover.close()
    bench.close()


def test_tether_at_the_screen_edge_runs_straight_into_the_card(app):
    """ⓘ 在窗口最右、卡片被屏幕挡住不能居中：颈部不被夹窄，卡片的边与上半截对齐。"""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QToolButton

    from folith.qt import components

    host = QWidget()
    area = app.primaryScreen().availableGeometry()
    host.setGeometry(area.right() - 300, area.top() + 40, 300, 200)
    tab = views.RoundTab(QToolButton())
    tab.setParent(host)
    tab.adjustSize()
    tab.move(host.width() - tab.width() - 2, 4)
    host.show()
    app.processEvents()
    popover = components.Popover("x", QWidget(), host, size=QSize(420, 300), header=False)
    popover.open_at(tab, align="center", tether=True)
    app.processEvents()
    pad = 9
    assert popover._neck[1] == pytest.approx(popover._popup_size.width() - pad, abs=1)
    path, panel = popover._tether_path()
    assert path.contains(QPointF(panel.right() - 1, panel.top() + 1)), "square corner on the flush side"
    popover.close()
    host.close()


# ---- 第八轮：脚注编号不是 transport wrapper；硬门禁不给"已解决"按钮 ------------------------
@pytest.mark.parametrize("source, target, ok", [
    ("[1]", "[1]", True), ("[12, 13]", "[12, 13]", True), ("注释", "[1]", True),
    ("x", '["译文"]', False), ("x", '{"translation": "y"}', False), ("x", "{}", False),
])
def test_footnote_markers_are_not_transport_wrappers(source, target, ok):
    from folith import translation_target

    assert translation_target.validate_translation_target(source, target)["ok"] is ok


def test_invariant_findings_offer_only_edit_or_retranslate():
    from folith import workbench_view

    assert workbench_view._is_invariant({"type": "delivery_invariant"})
    assert workbench_view._is_invariant({"detector": "Translation Target Invariant"})
    assert not workbench_view._is_invariant({"type": "terminology"})


def test_details_card_is_always_flush_right(app):
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QToolButton

    from folith.qt import components

    host = QWidget()
    host.resize(900, 300)
    tab = views.RoundTab(QToolButton())
    tab.setParent(host)
    tab.adjustSize()
    tab.move(300, 4)  # 右边空间充足
    host.show()
    app.processEvents()
    popover = components.Popover("x", QWidget(), host, size=QSize(420, 300), header=False)
    popover.open_at(tab, align="center", tether=True, flush="right")
    app.processEvents()
    assert popover._neck[1] == pytest.approx(popover._popup_size.width() - 9, abs=1)
    popover.close()
    host.close()
