"""Architecture Phase 4 — one effective-settings resolution path.

The contract under test:

* every runtime setting has an *explicit* winner, recorded as provenance;
* a persisted task keeps its own decisions when a global preference changes;
* equivalent Qt and Streamlit inputs resolve to identical effective settings;
* the resolution works without Streamlit or PySide6 (CLI / headless);
* secrets never leak into the describing surface.
"""
from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith.application import job_config as job_config_module  # noqa: E402
from folith.application.job_config import JobConfig  # noqa: E402
from folith.application.settings import (  # noqa: E402
    ENVIRONMENT_FALLBACKS,
    LAYERS,
    SECRET_FIELDS,
    SettingsSources,
    resolve_effective_settings,
)

PROFILE = {
    "provider": "OpenAI",
    "model": "gpt-5.1",
    "api_key": "profile-secret",
    "base_url": "https://api.openai.com/v1",
    "reasoning_effort": "medium",
    "reviewer": {"provider": "OpenAI", "model": "gpt-5.1",
                 "api_key": "profile-reviewer", "base_url": ""},
}


@contextmanager
def settings_env(profile: Dict[str, Any] | None = None):
    """Isolated output dir, optionally with a written provider profile."""
    tmp = Path(tempfile.mkdtemp(prefix="effective-settings-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        if profile is not None:
            (tmp).mkdir(parents=True, exist_ok=True)
            core.save_provider_config(
                profile["provider"], profile["model"], profile["api_key"],
                base_url=profile["base_url"],
                reasoning_effort=profile.get("reasoning_effort"),
                reviewer=profile.get("reviewer"))
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _persisted_job(**pipeline_config) -> Dict[str, Any]:
    state = core.new_job_state("book.docx")
    state["p1_done"] = True
    state["pipeline_config"] = dict(pipeline_config)
    return state


# ---------------------------------------------------------------------------
# precedence
# ---------------------------------------------------------------------------

def test_layers_are_declared_in_precedence_order():
    assert LAYERS[0] == "job_state"
    assert LAYERS.index("explicit") < LAYERS.index("user_defaults")
    assert LAYERS.index("user_defaults") < LAYERS.index("application_defaults")
    assert LAYERS[-1] == "default"


def test_new_job_resolution_order_is_explicit_then_user_defaults_then_app_defaults():
    # only an application default
    bare = resolve_effective_settings(SettingsSources())
    assert bare.get("batch_size") is None          # nothing supplied
    assert bare.winner("batch_size") == "default"
    assert bare.to_job_config().batch_size == 4    # the contract's own default

    # a stored user default
    stored = resolve_effective_settings(SettingsSources(user_defaults={"batch_size": 6}))
    assert stored.get("batch_size") == 6
    assert stored.winner("batch_size") == "user_defaults"
    assert stored.to_job_config().batch_size == 6

    # an explicit choice wins over the stored default
    chosen = resolve_effective_settings(SettingsSources(
        explicit={"batch_size": 8}, user_defaults={"batch_size": 6}))
    assert chosen.get("batch_size") == 8
    assert chosen.winner("batch_size") == "explicit"

    # the application-default layer is consulted before the contract default
    app_default = resolve_effective_settings(SettingsSources(
        application_defaults={"batch_size": 12}))
    assert app_default.get("batch_size") == 12
    assert app_default.winner("batch_size") == "application_defaults"


def test_an_explicit_invalid_value_is_not_silently_replaced():
    """Presence beats truthiness for the user's live choice, so it can be refused."""
    from folith.application import JobConfigError

    with pytest.raises(JobConfigError):
        resolve_effective_settings(SettingsSources(
            explicit={"batch_size": 0})).to_job_config()
    with pytest.raises(JobConfigError):
        resolve_effective_settings(SettingsSources(
            explicit={"batch_size": -3})).to_job_config()
    # An empty live language is not an error: it falls back to the documented
    # default, exactly as it did before the settings layer existed.
    effective = resolve_effective_settings(SettingsSources(
        explicit={"target_lang": ""}))
    assert effective.winner("target_lang") == "explicit"
    assert effective.to_job_config().target_lang == core.DEFAULT_TARGET_LANG


def test_connection_precedence_explicit_then_profile_then_saved_then_defaults():
    state = _persisted_job()
    state["pipeline_config"]["provider"] = "DeepSeek"

    explicit = resolve_effective_settings(SettingsSources(
        explicit={"provider": "OpenAI"}, provider_profile={"provider": "Anthropic"},
        job_state=state))
    assert explicit.get("provider") == "OpenAI"
    assert explicit.winner("provider") == "explicit"

    profile = resolve_effective_settings(SettingsSources(
        provider_profile={"provider": "Anthropic"}, job_state=state))
    assert profile.get("provider") == "Anthropic"
    assert profile.winner("provider") == "provider_profile"

    # A legacy state that stored the connection flat still supplies it.
    legacy = resolve_effective_settings(SettingsSources(job_state=state))
    assert legacy.get("provider") == "DeepSeek"
    assert legacy.winner("provider") == "job_state"

    # An empty credential falls through to the profile instead of winning.
    empty = resolve_effective_settings(SettingsSources(
        explicit={"api_key": ""}, provider_profile={"api_key": "from-profile"}))
    assert empty.get("api_key") == "from-profile"


# ---------------------------------------------------------------------------
# resume determinism
# ---------------------------------------------------------------------------

def test_a_persisted_task_ignores_later_global_preference_changes():
    state = _persisted_job(
        target_lang="Deutsch", batch_size=8, max_batch_chars=6400,
        enable_review=False, strict_terminology_governance=True,
        ocr_quality="historical", ocr_dpi=350,
        segmentation_mode="paragraph", translation_concurrency=6,
        enable_understanding=True)

    # A user who later changes every global preference must not rewrite the task.
    effective = resolve_effective_settings(SettingsSources(
        explicit={"target_lang": "English", "batch_size": 4, "max_batch_chars": 2400,
                  "enable_review": True, "strict_terminology_governance": False,
                  "ocr_quality": "standard", "ocr_dpi": 220,
                  "segmentation_mode": "sentence", "translation_concurrency": 2,
                  "enable_understanding": False},
        user_defaults={"ocr_quality": "standard", "batch_size": 4},
        job_state=state))

    for field in ("target_lang", "batch_size", "max_batch_chars", "enable_review",
                  "strict_terminology_governance", "ocr_quality", "ocr_dpi",
                  "segmentation_mode", "translation_concurrency",
                  "enable_understanding"):
        assert effective.winner(field) == "job_state", field

    config = effective.to_job_config()
    assert config.target_lang == "Deutsch"
    assert (config.batch_size, config.max_batch_chars) == (8, 6400)
    assert config.enable_review is False
    assert config.strict_terminology_governance is True
    assert config.ocr_quality == "historical"
    assert config.ocr_dpi == 350
    assert config.segmentation_mode == "paragraph"
    assert config.translation_concurrency == 6
    assert config.enable_understanding is True


def test_resume_is_deterministic_across_repeated_resolution():
    state = _persisted_job(target_lang="Deutsch", batch_size=8,
                           ocr_language="ara", ocr_backend="paddle")
    sources = SettingsSources(explicit={"target_lang": "English"}, job_state=state)
    first = resolve_effective_settings(sources).to_job_config()
    second = resolve_effective_settings(sources).to_job_config()
    assert first.to_json() == second.to_json()
    assert first.fingerprint() == second.fingerprint()


def test_a_locked_segmentation_contract_outranks_the_live_choice():
    state = _persisted_job()
    state["segmentation"] = {"mode": "paragraph", "locked": True}
    effective = resolve_effective_settings(SettingsSources(
        explicit={"segmentation_mode": "sentence"}, job_state=state))
    assert effective.get("segmentation_mode") == "paragraph"
    assert effective.winner("segmentation_mode") == "job_state_derived"


def test_persisted_only_defaults_apply_to_a_persisted_pdf_task():
    state = core.new_job_state("book.pdf")
    state["p1_done"] = True
    effective = resolve_effective_settings(SettingsSources(job_state=state))
    assert effective.get("enable_source_cleanup") is True
    assert effective.winner("enable_source_cleanup") == "persisted_default"

    # ...but a brand-new task leaves the rule to the core.
    fresh = resolve_effective_settings(SettingsSources())
    assert fresh.get("enable_source_cleanup") is None
    assert fresh.to_job_config().enable_source_cleanup is None


# ---------------------------------------------------------------------------
# provider / language / OCR changes
# ---------------------------------------------------------------------------

def test_switching_the_connection_after_job_creation_keeps_the_task_settings():
    state = _persisted_job(target_lang="Deutsch", batch_size=8, use_tm=False,
                           translation_theory="功能对等")
    effective = resolve_effective_settings(SettingsSources(
        explicit={"provider": "Anthropic", "model": "claude-x", "api_key": "new-key"},
        provider_profile={"provider": "OpenAI", "model": "gpt-5.1",
                          "api_key": "old-key"},
        job_state=state))

    assert effective.winner("provider") == "explicit"
    config = effective.to_job_config()
    assert (config.provider, config.model, config.api_key) == (
        "Anthropic", "claude-x", "new-key")
    # Task identity and task-scoped settings stay with the task.
    assert config.target_lang == "Deutsch"
    assert config.batch_size == 8
    assert config.use_tm is False
    assert config.translation_theory == "功能对等"


def test_target_language_is_task_scoped_not_connection_scoped():
    state = _persisted_job(target_lang="Deutsch")
    effective = resolve_effective_settings(SettingsSources(
        explicit={"target_lang": "English"}, job_state=state))
    assert effective.winner("target_lang") == "job_state"
    assert effective.to_job_config().target_lang == "Deutsch"

    # A brand-new task takes the live language.
    fresh = resolve_effective_settings(SettingsSources(
        explicit={"target_lang": "English"}))
    assert fresh.to_job_config().target_lang == "English"


def test_ocr_and_vlm_settings_resolve_per_field():
    state = _persisted_job(ocr_quality="historical", ocr_dpi=350,
                           ocr_backend="paddle", ocr_paddle_model="official")
    effective = resolve_effective_settings(SettingsSources(
        explicit={"ocr_quality": "standard", "ocr_dpi": 220, "ocr_psm": 6,
                  "ocr_language": "ara", "ocr_mode": "force",
                  "ocr_backend": "online", "ocr_online": {"model": "vision"}},
        job_state=state))

    assert effective.winner("ocr_quality") == "job_state"
    assert effective.winner("ocr_dpi") == "job_state"
    assert effective.winner("ocr_backend") == "job_state"
    # Fields the task never pinned follow the live choice.
    assert effective.winner("ocr_psm") == "explicit"
    assert effective.winner("ocr_language") == "explicit"
    assert effective.winner("ocr_mode") == "explicit"
    assert effective.winner("ocr_online") == "explicit"

    config = effective.to_job_config()
    assert config.ocr_quality == "historical"
    assert config.ocr_dpi == 350
    assert config.ocr_backend == "paddle"
    assert config.ocr_psm == 6
    assert config.ocr_language == "ara"
    assert config.ocr_mode == "force"
    assert config.ocr_online == {"model": "vision"}


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------

def test_secrets_never_appear_in_the_describing_surface():
    effective = resolve_effective_settings(SettingsSources(
        explicit={"api_key": "super-secret", "reviewer_api_key": "reviewer-secret",
                  "provider": "OpenAI"}))
    described = effective.describe()
    text = json.dumps(described, ensure_ascii=False)
    assert "super-secret" not in text
    assert "reviewer-secret" not in text
    assert described["api_key"]["value"] == "***"
    assert described["reviewer_api_key"]["value"] == "***"
    assert described["provider"]["value"] == "OpenAI"
    assert described["api_key"]["source"] == "explicit"
    assert SECRET_FIELDS == {"api_key", "reviewer_api_key"}


def test_secrets_are_redacted_even_when_they_come_from_the_profile():
    with settings_env(PROFILE):
        sources = SettingsSources(provider_profile={
            "api_key": "profile-secret", "reviewer_api_key": "profile-reviewer"})
        described = resolve_effective_settings(sources).describe()
        text = json.dumps(described, ensure_ascii=False)
        assert "profile-secret" not in text
        assert "profile-reviewer" not in text


# ---------------------------------------------------------------------------
# environment layer
# ---------------------------------------------------------------------------

def test_environment_fallbacks_are_real_core_reads():
    """The documented environment layer must not drift from core's own reads."""
    # core.py plus the clusters split out of it (folith/engine)
    source = "\n".join(
        path.read_text("utf-8")
        for path in [ROOT / "core.py", *sorted((ROOT / "folith" / "engine").glob("*.py"))]
    )
    for name in ENVIRONMENT_FALLBACKS:
        assert name in source, f"{name} is no longer read by core"


# ---------------------------------------------------------------------------
# JobConfig stays the execution contract
# ---------------------------------------------------------------------------

def test_job_config_from_frontend_still_delegates_to_the_resolver(monkeypatch):
    captured = {}
    original = job_config_module.JobConfig.from_settings

    def spy(cls, effective):
        captured["effective"] = effective
        return original(effective)

    monkeypatch.setattr(job_config_module.JobConfig, "from_settings",
                        classmethod(spy))
    state = _persisted_job(batch_size=8, target_lang="Deutsch")
    config = JobConfig.from_frontend({"target_lang": "English"}, state=state)
    assert "effective" in captured, "from_frontend bypassed the settings resolver"
    assert captured["effective"].winner("batch_size") == "job_state"
    assert captured["effective"].winner("target_lang") == "job_state"
    assert config.batch_size == 8
    assert config.target_lang == "Deutsch"


def test_effective_settings_and_from_frontend_agree():
    state = _persisted_job(target_lang="Deutsch", batch_size=8)
    values = {"target_lang": "English", "use_tm": False, "ocr_language": "ara"}
    resolved = resolve_effective_settings(SettingsSources(
        explicit=values, job_state=state)).to_job_config()
    direct = JobConfig.from_frontend(values, state=state)
    assert resolved.to_dict() == direct.to_dict()
    assert resolved.fingerprint() == direct.fingerprint()


# ---------------------------------------------------------------------------
# cross-frontend equivalence
# ---------------------------------------------------------------------------

_STREAMLIT_HELPERS = ("_settings_sources",)


def _streamlit_settings_sources(globals_map: Dict[str, Any], session: Dict[str, Any]):
    """Exec app.py's real layer builder with equivalent Streamlit inputs."""
    import types
    from contextlib import nullcontext  # noqa: F401

    source = (ROOT / "app.py").read_text("utf-8")
    tree = ast.parse(source)
    body = [node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in _STREAMLIT_HELPERS]
    assert body, "app.py no longer defines _settings_sources"
    body[0].decorator_list = []

    class FakeSt:
        session_state = dict(session)

    def batch_params(config):
        profile = {"保守": (4, 2400), "均衡": (6, 4800), "快速": (8, 6400)}
        return profile.get(str((config or {}).get("batch_profile") or "保守"), (4, 2400))

    namespace = {
        "core": core,
        "st": FakeSt(),
        "_batch_params": batch_params,
        "_session_ocr_online_descriptor": lambda **kwargs: None,
        "SettingsSources": SettingsSources,
        "_SettingsSources": SettingsSources,
    }
    namespace.update(globals_map)
    exec(compile(ast.Module(body=body, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["_settings_sources"](globals_map.get("_state"))


def _streamlit_globals() -> Dict[str, Any]:
    return {
        "ai_provider": "OpenAI", "ai_model": "gpt-5.1", "api_key": "sk",
        "api_base": "https://api.openai.com/v1", "reasoning_effort": "medium",
        "reviewer_mode": "separate", "reviewer_provider": "OpenAI",
        "reviewer_model": "gpt-5.1", "reviewer_api_key": "rk",
        "reviewer_base_url": "",
        "target_lang": "English", "auto_term": True,
        "translation_theory": "目的论 (Skopos Theory)", "user_glossary": [],
        "style_rules": "保持学术书面语。", "enable_review": True,
        "enable_annotate": True, "enable_report": True, "use_tm": True,
        "strategy_config": {"auto_term": True, "use_tm": True,
                            "enable_understanding": True, "enable_review": True,
                            "strict_terminology_governance": False,
                            "segmentation_mode": "sentence",
                            "translation_concurrency": 4,
                            "batch_profile": "保守"},
        "output_config": {"deliver_plain_docx": True,
                          "deliver_bilingual_docx": True,
                          "deliver_terms_xlsx": True, "deliver_evidence": True,
                          "deliver_pdf": False, "deliver_review_report": False},
        "research_settings": {"target_words": 4200},
        "literature_sources": ["refs.bib"],
    }


def test_qt_and_streamlit_equivalent_inputs_resolve_identically():
    from folith.qt import adapter

    state = {"glossary": [{"source": "术语", "target": "term"}]}
    with settings_env(PROFILE):
        config = adapter.EngineConfig(
            provider="OpenAI", model="gpt-5.1", api_key="sk",
            base_url="https://api.openai.com/v1", target_lang="English",
            reasoning_effort="medium", auto_term=True, enable_review=True,
            use_tm=True, enable_understanding=True, enable_source_cleanup=None,
            enable_report=True, enable_annotate=True,
            translation_theory="目的论 (Skopos Theory)",
            style_rules="保持学术书面语。", segmentation_mode="sentence",
            translation_concurrency=4, reviewer_mode="separate",
            reviewer_provider="OpenAI", reviewer_model="gpt-5.1",
            reviewer_api_key="rk", reviewer_base_url="")
        qt_sources = adapter.settings_sources(config, state)

        streamlit_globals = _streamlit_globals()
        streamlit_globals["_state"] = state
        st_sources = _streamlit_streamlit_sources(streamlit_globals)

        qt = resolve_effective_settings(qt_sources)
        web = resolve_effective_settings(st_sources)

        # The desktop surface does not expose research/literature settings yet
        # (a documented Phase 1 divergence); every field both surfaces *can*
        # express must resolve identically.
        qt_values = qt.to_job_config().to_dict()
        web_values = web.to_job_config().to_dict()
        assert {k for k in qt_values if qt_values[k] != web_values[k]} == {
            "research_settings", "literature_sources"}

        # Feed the same values through the desktop layer and the whole contract
        # matches byte for byte — one resolution path, not two.
        qt_sources_complete = SettingsSources(
            explicit={**dict(qt_sources.explicit),
                      "research_settings": {"target_words": 4200},
                      "literature_sources": ["refs.bib"]},
            user_defaults=qt_sources.user_defaults,
            provider_profile=qt_sources.provider_profile,
            job_state=qt_sources.job_state)
        qt_complete = resolve_effective_settings(qt_sources_complete)
        assert qt_complete.to_job_config().to_dict() == web_values
        assert qt_complete.to_job_config().fingerprint() == \
            web.to_job_config().fingerprint()
        # ...and both layers were actually used, not just an empty path.
        for source in (qt, web):
            assert source.winner("provider") in {"explicit", "provider_profile"}
            assert source.winner("batch_size") in {"user_defaults", "default"}
            assert source.winner("user_glossary") == "job_state"


def _streamlit_streamlit_sources(globals_map: Dict[str, Any]):
    return _streamlit_settings_sources(globals_map, {})


def test_streamlit_builder_emits_named_layers():
    globals_map = _streamlit_globals()
    globals_map["_state"] = {}
    sources = _streamlit_settings_sources(globals_map, {})
    assert isinstance(sources, SettingsSources)
    assert sources.explicit.get("provider") == "OpenAI"
    assert sources.user_defaults.get("batch_size") == 4
    assert sources.user_defaults.get("enable_understanding") is True
    assert "provider" in sources.provider_profile or sources.provider_profile == {}
    assert sources.job_state == {}


def test_qt_builder_emits_named_layers():
    from folith.qt import adapter

    with settings_env(PROFILE):
        config = adapter.EngineConfig(provider="OpenAI", model="gpt-5.1",
                                      api_key="sk", target_lang="English")
        sources = adapter.settings_sources(config, {"filename": "book.pdf"})
    assert isinstance(sources, SettingsSources)
    assert sources.explicit["provider"] == "OpenAI"
    assert sources.provider_profile["provider"] == "OpenAI"
    assert sources.job_state == {"filename": "book.pdf"}


# ---------------------------------------------------------------------------
# headless
# ---------------------------------------------------------------------------

_HEADLESS = '''
import json, sys, tempfile
from pathlib import Path

BLOCKED = {"streamlit", "PySide6", "PySide2", "shiboken6"}


class _Block:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("blocked: " + fullname)
        return None


sys.meta_path.insert(0, _Block())
sys.path.insert(0, __ROOT__)

from folith.application.settings import SettingsSources, resolve_effective_settings
import core

core.OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="headless-settings-"))
core.save_provider_config("OpenAI", "gpt-5.1", "profile-key",
                          base_url="https://api.openai.com/v1",
                          reasoning_effort="medium")

effective = resolve_effective_settings(SettingsSources(
    explicit={"target_lang": "English"},
    user_defaults={"batch_size": 6},
    provider_profile=core.load_provider_config() or {},
    job_state={},
))
config = effective.to_job_config()

from folith.ui import i18n
i18n.set_locale("en")

print(json.dumps({
    "api_key_source": effective.winner("api_key"),
    "api_key_redacted": effective.describe()["api_key"]["value"],
    "batch_size": config.batch_size,
    "target_lang": config.target_lang,
    "catalog_headless": bool(i18n.catalog("en")),
    "locale": i18n.current_locale(),
    "frontends_loaded": sorted(n for n in sys.modules
                               if n.split(".")[0] in BLOCKED),
}))
'''


def test_settings_resolution_and_locale_work_headlessly():
    script = _HEADLESS.replace("__ROOT__", repr(str(ROOT)))
    completed = subprocess.run([sys.executable, "-c", script], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["api_key_source"] == "provider_profile"
    assert payload["api_key_redacted"] == "***"
    assert payload["batch_size"] == 6
    assert payload["target_lang"] == "English"
    assert payload["catalog_headless"] is True
    assert payload["locale"] == "en"
    assert payload["frontends_loaded"] == []
