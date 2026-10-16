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
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
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
    saved = json.loads(core.provider_config_path().read_text())
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


def test_connection_card_collapses_inherited_roles_and_labels_estimates(isolated, app):
    cfg = adapter.EngineConfig(provider="OpenAI", model="current-model", api_key="fake")
    panel = ConnectionPanel(Runner(), lambda: cfg)
    panel.usage_state = {"llm_usage": {"calls": 2, "total_tokens": 1200, "estimated_total_tokens": 300}}
    panel.refresh()
    assert not panel._rows["translation"]["container"].isHidden()
    assert panel._rows["reviewer"]["container"].isHidden()
    assert panel._rows["vision"]["container"].isHidden()
    assert panel._rows["translation"]["title"].text() == "current-model"
    assert "1,200" in panel.usage.text() and "估算 300" in panel.usage.text()


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
        json.loads(core.ocr_online_config_path().read_text()) if core.ocr_online_config_path().exists() else {})
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
                        (True, ["first", "second"], "OK"))
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

    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]
                                                 / "scripts" / "qt_i18n_coverage.py"), "--strict"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-800:]


@pytest.mark.parametrize("language, expected", [("ru", "Фолит"), ("es", "Folith"), ("ar", "فوليث")])
def test_foreign_wordmark_is_a_short_name_and_a_short_tagline(language, expected, app, monkeypatch):
    from folith.qt import i18n
    monkeypatch.setattr(i18n, "language", lambda: language)
    home = views.HomeView()
    home._logo_theme = None
    home._refresh_logo()
    name, _, tagline = home._logo_label.text().partition("<br>")
    assert name == f"<b>{expected}</b>", "只写名字的音译，不直译「译页」"
    assert len(tagline) - len("<span style='font-size:10px'></span>") <= 30
    home.close()
