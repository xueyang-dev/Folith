"""#540 PR-2: per-purpose thinking levels, migration parity and the settings UI."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from folith.engine import effort_settings  # noqa: E402
from folith.qt import adapter, dialogs  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _config(**extra):
    base = dict(provider="Local Gateway", model="auto/deepseek-v4.1-flash",
                api_key="k", target_lang="简体中文", reasoning_effort="low")
    base.update(extra)
    return adapter.EngineConfig(**base)


# ---- pure settings logic ------------------------------------------------------

def test_choices_only_include_verified_levels():
    assert effort_settings.choices("Local Gateway", "any") == [
        ("service_default", "服务默认"), ("low", "Low"), ("medium", "Medium"),
        ("high", "High")]
    assert effort_settings.choices("DeepSeek", "deepseek-v4") == [
        ("service_default", "服务默认"), ("off", "Off"), ("on", "On")]
    assert effort_settings.choices("Custom", "unknown") == [("service_default", "服务默认")]


def test_gateway_cannot_switch_off_but_deepseek_can():
    assert effort_settings.cannot_switch("Local Gateway", "any")
    assert not effort_settings.cannot_switch("DeepSeek", "deepseek-v4")


def test_reviewer_and_vision_inherit_translation_unset():
    levels = {"translation": "low"}
    assert effort_settings.effective_level(levels, "reviewer") == "low"
    assert effort_settings.effective_level(levels, "vision") == "low"
    levels["vision"] = "off"
    assert effort_settings.effective_level(levels, "vision") == "off"


def test_migration_is_idempotent_and_additive():
    cfg = _config(connection_levels={"translation": "high"})
    assert effort_settings.migrate(cfg) == {"translation": "high", "reviewer": "", "vision": ""}
    fresh = _config(connection_levels={})
    levels = effort_settings.migrate(fresh)
    assert levels["translation"] == "low"
    again = effort_settings.migrate(type("C", (), {"connection_levels": levels})())
    assert again == levels


def test_legacy_value_is_kept_and_marked():
    cfg = _config(reasoning_effort="extreme")
    levels = effort_settings.migrate(cfg)
    assert levels["translation"] == "extreme"
    assert effort_settings.is_legacy("extreme", "Local Gateway", "any")
    effort, mode = effort_settings.to_job_fields(levels, "translation", "Local Gateway", "any")
    assert (effort, mode) == ("extreme", "provider_default"), "migration must not change the wire value"


@pytest.mark.parametrize("levels,provider,model,expected", [
    ({"translation": "low"}, "Local Gateway", "any", ("low", "provider_default")),
    ({"translation": "off"}, "DeepSeek", "deepseek-v4", ("", "thinking_disabled")),
    ({"translation": "on"}, "DeepSeek", "deepseek-v4", ("", "provider_default")),
    ({"translation": "service_default"}, "Custom", "unknown", ("", "provider_default")),
])
def test_to_job_fields(levels, provider, model, expected):
    assert effort_settings.to_job_fields(levels, "translation", provider, model) == expected


def test_migration_request_body_parity(monkeypatch, tmp_path):
    """The same call before and after migration produces identical request fields."""
    seen = []
    from types import SimpleNamespace
    from folith import llm_transport
    import core

    class _Completions:
        def create(self, **request):
            seen.append(request)
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=40, total_tokens=50)
            return SimpleNamespace(id="r1", choices=[SimpleNamespace(
                message=SimpleNamespace(content="x"))], usage=usage)

    class _Fake:
        def __init__(self, **_kw):
            self.chat = SimpleNamespace(completions=_Completions())

        def close(self):
            pass

    llm_transport.close_all_clients()
    monkeypatch.setattr(core, "OpenAI", _Fake)
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "1")
    # old format: scalar reasoning_effort on the connection
    core.call_llm("Local Gateway", "k", "any", "s", "u", base_url="http://127.0.0.1:1/v1",
                  reasoning_effort="low")
    old = dict(seen[-1])
    # migrated format: per-purpose level mapped to the same scalar
    cfg = _config(connection_levels={"translation": "low"})
    effort, mode = effort_settings.to_job_fields(cfg.connection_levels, "translation",
                                                 cfg.provider, cfg.model)
    core.call_llm("Local Gateway", "k", "any", "s", "u", base_url="http://127.0.0.1:1/v1",
                  reasoning_effort=effort)
    new = dict(seen[-1])
    assert old == new


# ---- settings UI --------------------------------------------------------------

def _dialog(monkeypatch):
    monkeypatch.setattr(adapter, "job_is_alive", lambda *_a, **_k: False)
    return dialogs.EngineDialog(_config(), TaskRunner())


def test_every_role_row_has_a_level_combo(app, monkeypatch):
    dlg = _dialog(monkeypatch)
    try:
        lib = dlg.connection_library
        assert set(lib.level_selectors) == {"translation", "reviewer", "vision"}
        for role, box in lib.level_selectors.items():
            assert isinstance(box, QComboBox)
            assert box.isEnabled() is False or box.count() >= 1
        dlg.show_page("models")
        app.processEvents()
        g = lib.level_selectors["translation"]
        assert [g.itemData(i) for i in range(g.count())][:4] == [
            "service_default", "low", "medium", "high"]
    finally:
        dlg.close()


def test_editor_no_longer_shows_effort_rows(app, monkeypatch):
    dlg = _dialog(monkeypatch)
    try:
        lib = dlg.connection_library
        editor = lib.editor
        assert dlg.reasoning_row.parentWidget() is not editor or dlg.reasoning_row.isHidden()
        assert not dlg.reasoning_row.isVisible()
        assert not dlg.thinking_row.isVisible()
        assert not dlg.thinking_hint.isVisible()
    finally:
        dlg.close()


def test_level_selection_is_saved_in_config(app, monkeypatch):
    dlg = _dialog(monkeypatch)
    try:
        lib = dlg.connection_library
        box = lib.level_selectors["translation"]
        idx = box.findData("high")
        assert idx >= 0
        box.setCurrentIndex(idx)
        assert dlg.config.connection_levels["translation"] == "high"
        collected = dlg._collect()
        assert collected.connection_levels["translation"] == "high"
    finally:
        dlg.close()


def test_unknown_model_level_is_disabled_with_a_dedicated_hint(app, monkeypatch):
    dlg = dialogs.EngineDialog(_config(
        connections=[
            {"id": "gw", "name": "gateway", "provider": "Local Gateway",
             "model": "auto/x", "api_key": "k", "base_url": "http://127.0.0.1:1/v1"},
            {"id": "u", "name": "mystery", "provider": "OpenRouter",
             "model": "mystery-model", "api_key": "k", "base_url": "http://127.0.0.1:9/v1"},
        ],
        connection_roles={"translation": "gw", "vision": "u"},
        connection_models={"translation": "auto/x", "vision": "mystery-model"}), TaskRunner())
    try:
        lib = dlg.connection_library
        box = lib.level_selectors["vision"]     # bound to mystery-model
        assert not box.isEnabled(), "目录外的模型不能选档位"
        assert box.count() == 1 and box.currentData() == "service_default"
        assert "没有已验证" in box.toolTip()
        assert "{v0}" not in box.toolTip()
    finally:
        dlg.close()


def test_connection_row_summary_names_the_default_levels(app, monkeypatch):
    dlg = _dialog(monkeypatch)
    try:
        lib = dlg.connection_library
        labels = [w.toolTip() for w in lib.list_group.findChildren(object)
                  if hasattr(w, "fullText")]
        all_text = " ".join(labels)
        # 网关摘要含 Low/Medium/High，DeepSeek 摘要含 Off/On；未知模型不写档位。
        assert "Low / Medium / High" in all_text or "思考：" in all_text
    finally:
        dlg.close()


def test_levels_survive_save_and_load(tmp_path, monkeypatch):
    import core
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(core, "OUTPUT_DIR", data)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data))
    cfg = _config(connection_levels={"translation": "medium", "vision": "off"})
    cfg.save()
    reloaded = adapter.EngineConfig.load()
    assert reloaded.connection_levels["translation"] == "medium"
    assert reloaded.connection_levels["vision"] == "off"
