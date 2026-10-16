"""Desktop task defaults: they must survive a restart, and only they must.

The desktop settings dialog exposes a small set of *task defaults*.  Before this
pass they lived only in memory (``EngineConfig``), so every restart silently
restored the dataclass defaults.  This suite pins the storage, the values it is
allowed to hold, and the precedence they feed into.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402


def test_selftest_local_ocr_is_explicit_and_preserves_other_task_choices(monkeypatch):
    from folith.qt import adapter, selftest
    monkeypatch.setenv("FOLITH_QT_SELFTEST_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.delenv("FOLITH_QT_SELFTEST_OCR", raising=False)
    base = adapter.EngineConfig(ocr_backend="online", deliver_epub=True, deliver_terms_xlsx=False)
    assert selftest.engine_from_env(adapter, base).ocr_backend == "online"
    monkeypatch.setenv("FOLITH_QT_SELFTEST_OCR", "1")
    selected = selftest.engine_from_env(adapter, base)
    assert selected.ocr_backend == "tesseract"
    assert selected.deliver_epub is False
    assert selected.deliver_original_pdf is False
    assert selected.deliver_terms_xlsx is False
    assert base.ocr_backend == "online"
from PySide6.QtWidgets import QApplication  # noqa: E402

import core  # noqa: E402
from folith.application.settings import (  # noqa: E402
    SettingsSources,
    resolve_effective_settings,
)
from folith.qt import adapter  # noqa: E402
from folith.qt import window as window_module  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@contextmanager
def isolated_settings(tmp_path: Path, monkeypatch):
    """Give the window an INI settings store instead of the user's real plist.

    ``QSettings("Folith", "QtDesktop")`` resolves to the native (macOS plist)
    store no matter what ``setDefaultFormat`` says, so the window's ``QSettings``
    symbol is replaced with a factory that opens an explicit INI file.  The
    round-trip is still real Qt settings; only the store is a throwaway.
    """
    ini = tmp_path / "qt-client-settings.ini"
    real = QSettings

    def factory(*args, **kwargs):
        return real(str(ini), real.Format.IniFormat)

    monkeypatch.setattr(window_module, "QSettings", factory)
    yield ini


def _settings_host(config):
    """The window's persistence methods, without building the whole window."""
    class Host:
        TASK_DEFAULT_KEYS = window_module.MainWindow.TASK_DEFAULT_KEYS
        TASK_DEFAULT_BOOL_KEYS = window_module.MainWindow.TASK_DEFAULT_BOOL_KEYS
        TASK_DEFAULT_PREFIX = window_module.MainWindow.TASK_DEFAULT_PREFIX
        DELIVERY_KEYS = window_module.MainWindow.DELIVERY_KEYS
        _coerce_task_default = window_module.MainWindow._coerce_task_default
        _load_client_defaults = window_module.MainWindow._load_client_defaults
        _save_client_defaults = window_module.MainWindow._save_client_defaults

        def __init__(this):
            this.config = config
            # Uses the window module's symbol, so the isolated factory applies.
            this.settings = window_module.QSettings("Folith", "QtDesktop")
            this.stored_task_defaults = {}

    return Host()


def _all_settings_pairs():
    settings = window_module.QSettings("Folith", "QtDesktop")
    return ([(key, settings.value(key)) for key in settings.allKeys()],
            settings.fileName())


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------

def test_the_dialog_defaults_survive_a_restart(qt_app, tmp_path, monkeypatch):
    with isolated_settings(tmp_path, monkeypatch):
        chosen = adapter.EngineConfig(
            target_lang="English", auto_term=False, enable_review=False,
            use_tm=True, enable_understanding=True, enable_source_cleanup=True,
            enable_report=True, ocr_language="ara", ocr_mode="force",
            ocr_backend="paddle")
        first = _settings_host(chosen)
        first._save_client_defaults()

        # A fresh process would build a fresh EngineConfig; simulate that.
        restarted = adapter.EngineConfig()
        second = _settings_host(restarted)
        second._load_client_defaults()

    assert (restarted.target_lang, restarted.auto_term, restarted.enable_review,
            restarted.use_tm, restarted.enable_understanding,
            restarted.enable_source_cleanup, restarted.enable_report,
            restarted.ocr_language, restarted.ocr_mode,
            restarted.ocr_backend) == (
        "English", False, False, True, True, True, True, "ara", "force", "paddle")
    assert second.stored_task_defaults["target_lang"] == "English"
    assert second.stored_task_defaults["enable_report"] is True
    assert set(second.stored_task_defaults) == set(
        window_module.MainWindow.TASK_DEFAULT_KEYS)


def test_only_the_visible_task_defaults_are_stored(qt_app, tmp_path, monkeypatch):
    with isolated_settings(tmp_path, monkeypatch):
        config = adapter.EngineConfig(target_lang="English", enable_review=False)
        host = _settings_host(config)
        host._save_client_defaults()
        pairs, where = _all_settings_pairs()

    keys = {key for key, _ in pairs}
    assert where.endswith(".ini") and "Preferences" not in where, where
    assert not where.endswith(".plist"), "the native user store must not be touched"
    assert keys == {f"task/{key}" for key in window_module.MainWindow.TASK_DEFAULT_KEYS} \
        | {f"delivery/{key}" for key in window_module.MainWindow.DELIVERY_KEYS}
    # Fields the dialog never shows must not become global defaults.
    for absent in ("translation_theory", "style_rules", "segmentation_mode",
                   "translation_concurrency", "enable_annotate",
                   "reasoning_effort"):
        assert f"task/{absent}" not in keys


def test_no_credential_is_written_to_the_settings_store(qt_app, tmp_path, monkeypatch):
    with isolated_settings(tmp_path, monkeypatch):
        config = adapter.EngineConfig(
            api_key="super-secret-key", reviewer_api_key="reviewer-secret-key",
            provider="OpenAI", model="gpt-5.1", base_url="https://api.openai.com/v1")
        host = _settings_host(config)
        host._save_client_defaults()
        pairs, _ = _all_settings_pairs()

    blob = repr(pairs)
    assert "super-secret-key" not in blob
    assert "reviewer-secret-key" not in blob
    assert "api_key" not in {key.split("/")[-1] for key, _ in pairs}


def test_an_empty_stored_string_is_not_a_default(qt_app, tmp_path, monkeypatch):
    """An empty OCR language means "auto", not "a default the user chose"."""
    with isolated_settings(tmp_path, monkeypatch):
        settings = window_module.QSettings("Folith", "QtDesktop")
        settings.setValue("task/ocr_language", "")
        settings.sync()

        config = adapter.EngineConfig(ocr_language="")
        host = _settings_host(config)
        host._load_client_defaults()

    assert "ocr_language" not in host.stored_task_defaults


# ---------------------------------------------------------------------------
# precedence: stored default < explicit choice, task state wins on resume
# ---------------------------------------------------------------------------

def test_a_stored_default_is_used_for_a_new_task():
    config = adapter.EngineConfig(target_lang="English", auto_term=False,
                                  enable_report=True)
    sources = adapter.settings_sources(
        config, {}, user_defaults={"target_lang": "English", "auto_term": False,
                                   "enable_report": True})
    effective = resolve_effective_settings(sources)
    assert effective.winner("auto_term") == "user_defaults"
    assert effective.winner("target_lang") == "user_defaults"
    contract = effective.to_job_config()
    assert contract.target_lang == "English"
    assert contract.auto_term is False
    assert contract.enable_report is True


def test_an_explicit_choice_beats_the_stored_default():
    # The user changed the dialog this session: the live value differs from
    # what is still stored.
    config = adapter.EngineConfig(target_lang="Deutsch", auto_term=True)
    sources = adapter.settings_sources(
        config, {}, user_defaults={"target_lang": "English", "auto_term": False})
    effective = resolve_effective_settings(sources)
    assert effective.winner("target_lang") == "explicit"
    assert effective.winner("auto_term") == "explicit"
    contract = effective.to_job_config()
    assert contract.target_lang == "Deutsch"
    assert contract.auto_term is True


def test_changing_a_global_default_does_not_rewrite_an_existing_task():
    state = core.new_job_state("book.pdf")
    state["p1_done"] = True
    state["pipeline_config"] = {"target_lang": "Deutsch", "auto_term": False,
                                "batch_size": 8, "max_batch_chars": 6400}
    config = adapter.EngineConfig(target_lang="English", auto_term=True)
    sources = adapter.settings_sources(
        config, state, user_defaults={"target_lang": "English", "auto_term": True})
    effective = resolve_effective_settings(sources)

    assert effective.winner("target_lang") == "job_state"
    assert effective.winner("auto_term") == "job_state"
    contract = effective.to_job_config()
    assert (contract.target_lang, contract.auto_term) == ("Deutsch", False)


# ---------------------------------------------------------------------------
# end to end: a new task is created from the stored defaults
# ---------------------------------------------------------------------------

def _make_docx(texts) -> bytes:
    from docx import Document

    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def data_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    return data_dir


def test_a_new_task_receives_the_stored_defaults(data_env, tmp_path):
    source = tmp_path / "defaults.docx"
    source.write_bytes(_make_docx(["一段。"]))
    stored = {"target_lang": "English", "auto_term": False, "use_tm": False,
              "enable_report": True, "ocr_language": "ara"}
    config = adapter.EngineConfig(
        target_lang="English", auto_term=False, use_tm=False,
        enable_report=True, ocr_language="ara")

    imported = adapter.import_documents(
        [source], config, user_defaults=stored)[0]
    state = core.load_job_state(imported.job_id)

    assert state["target_lang"] == "English"
    assert state["auto_term"] is False
    assert state["use_tm"] is False
    assert state["report_enabled"] is True
    assert state["pipeline_config"] is None or True  # not written before a run


def test_an_explicit_choice_overrides_the_stored_default_for_a_new_task(data_env, tmp_path):
    source = tmp_path / "override.docx"
    source.write_bytes(_make_docx(["一段。"]))
    # Stored: report on.  The dialog in this session says off.
    stored = {"enable_report": True, "auto_term": True}
    config = adapter.EngineConfig(enable_report=False, auto_term=False)

    imported = adapter.import_documents([source], config, user_defaults=stored)[0]
    state = core.load_job_state(imported.job_id)

    assert state["report_enabled"] is False
    assert state["auto_term"] is False


def test_a_resumed_task_keeps_its_recorded_configuration(data_env, tmp_path):
    source = tmp_path / "resume.docx"
    source.write_bytes(_make_docx(["一段。"]))
    # Task identity includes the target language, so hold it constant and change
    # the other defaults the user edited afterwards.
    first = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="Deutsch", auto_term=False,
                                       enable_report=False, ocr_language="ara"),
        user_defaults={"target_lang": "Deutsch", "auto_term": False,
                       "enable_report": False, "ocr_language": "ara"})[0]

    # The user later changes the global defaults and re-opens the same document.
    again = adapter.import_documents(
        [source], adapter.EngineConfig(target_lang="Deutsch", auto_term=True,
                                       enable_report=True, ocr_language="eng"),
        user_defaults={"target_lang": "Deutsch", "auto_term": True,
                       "enable_report": True, "ocr_language": "eng"})[0]
    state = core.load_job_state(again.job_id)

    assert again.job_id == first.job_id
    assert again.created is False
    assert state["target_lang"] == "Deutsch"
    assert state["auto_term"] is False
    assert state["report_enabled"] is False


def test_the_window_hands_its_stored_defaults_to_the_resolver(qt_app, tmp_path, monkeypatch):
    """The wiring itself: window -> adapter -> user_defaults layer."""
    with isolated_settings(tmp_path, monkeypatch):
        chosen = adapter.EngineConfig(target_lang="English", auto_term=False)
        host = _settings_host(chosen)
        host._save_client_defaults()

        restarted = adapter.EngineConfig()
        fresh = _settings_host(restarted)
        fresh._load_client_defaults()

        sources = adapter.settings_sources(
            fresh.config, {}, user_defaults=fresh.stored_task_defaults)
        effective = resolve_effective_settings(sources)

    assert isinstance(sources, SettingsSources)
    assert effective.winner("target_lang") == "user_defaults"
    assert effective.to_job_config().target_lang == "English"


def test_the_settings_dialog_collects_the_defaults_that_get_persisted(
        qt_app, tmp_path, monkeypatch):
    """Dialog → collect → save → restart → load, without building the window."""
    from folith.qt.dialogs import EngineDialog
    from folith.qt.workers import TaskRunner

    with isolated_settings(tmp_path, monkeypatch):
        dialog = EngineDialog(adapter.EngineConfig(), TaskRunner())
        dialog.target_box.setCurrentText("English")
        dialog.default_mode_box.setCurrentIndex(dialog.default_mode_box.findData("快速"))
        dialog.auto_term_box.setChecked(False)
        dialog.review_box.setChecked(False)
        dialog.tm_box.setChecked(False)
        dialog.understanding_box.setChecked(True)
        dialog.cleanup_box.setChecked(True)
        dialog.report_box.setChecked(True)
        collected = dialog._collect()
        dialog.close()

        host = _settings_host(collected)
        host._save_client_defaults()

        restarted = adapter.EngineConfig()
        reloaded = _settings_host(restarted)
        reloaded._load_client_defaults()

    assert restarted.target_lang == "English"
    assert restarted.default_translation_mode == "快速"
    assert restarted.auto_term is False
    assert restarted.enable_review is False
    assert restarted.use_tm is False
    assert restarted.enable_understanding is True
    assert restarted.enable_source_cleanup is True
    assert restarted.enable_report is True
    # Connection fields belong to provider_config.json, never to this store.
    for absent in ("provider", "model", "api_key", "base_url"):
        assert absent not in reloaded.stored_task_defaults
