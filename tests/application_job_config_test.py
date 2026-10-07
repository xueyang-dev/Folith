"""Architecture Phase 1 — the shared application job contract.

These tests are the migration oracle for ``folith.application.JobConfig``:

* ``test_core_signature_*``  — complete field coverage against the real
  ``core.run_job_pipeline`` signature (nothing may be forgotten or invented);
* ``test_frontend_equivalence_*`` — the Qt adapter and a Streamlit-shaped value
  mapping produce the same effective configuration (Test A);
* ``test_resume_*`` — the documented resume precedence, including the divergences
  Phase 1 deliberately fixed (Test D);
* ``test_source_admission_*`` — one authority for which files can be imported
  (Test C);
* ``test_golden_contract_*`` — a frozen snapshot of one realistic job so future
  architecture work cannot silently change the contract (Test E + fixture).
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import core
from folith.application import (
    INTERNAL_CORE_OPTIONS,
    JOB_CONFIG_FIELDS,
    JOB_CONFIG_KWARG_KEYS,
    OPTIONAL_REVIEWER_KWARG_KEYS,
    SOURCE_SUFFIXES,
    JobConfig,
    JobConfigError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "application_job_contract_golden.json"

POSITIONAL_CORE_PARAMS = {"job_id", "filename", "file_bytes"}


def core_keyword_params() -> set:
    signature = inspect.signature(core.run_job_pipeline)
    return {name for name in signature.parameters
            if name not in POSITIONAL_CORE_PARAMS}


def contract_kwarg_keys() -> set:
    return set(JOB_CONFIG_KWARG_KEYS) | set(OPTIONAL_REVIEWER_KWARG_KEYS)


# ---------------------------------------------------------------------------
# Test B — complete field coverage
# ---------------------------------------------------------------------------

def test_core_signature_is_fully_accounted_for():
    """Every core keyword is either in the shared contract or explicitly internal."""
    unknown = core_keyword_params() - contract_kwarg_keys() - set(INTERNAL_CORE_OPTIONS)
    assert not unknown, (
        "core.run_job_pipeline gained keyword(s) with no documented origin: "
        f"{sorted(unknown)}")
    stale = (contract_kwarg_keys() | set(INTERNAL_CORE_OPTIONS)) - core_keyword_params()
    assert not stale, (
        "the contract claims keyword(s) the core does not accept: "
        f"{sorted(stale)}")


def test_contract_matches_jobconfig_fields():
    declared = {spec.name for spec in JobConfig.__dataclass_fields__.values()}
    assert declared == set(JOB_CONFIG_FIELDS)
    assert declared == contract_kwarg_keys() | {"source_suffix", "reviewer_mode"}


def test_emitted_kwargs_are_always_accepted_by_core():
    config = JobConfig.from_frontend(_minimal_values())
    kwargs = config.to_pipeline_kwargs()
    unknown = set(kwargs) - core_keyword_params()
    assert not unknown, f"unknown core kwargs emitted: {sorted(unknown)}"
    required = {"provider", "api_key", "model", "target_lang", "auto_term",
                "enable_report", "translation_theory"}
    assert required <= set(kwargs)


def test_reviewer_keywords_are_conditional():
    same = JobConfig.from_frontend({**_minimal_values(), "reviewer_mode": "same"})
    assert not (set(same.to_pipeline_kwargs()) & OPTIONAL_REVIEWER_KWARG_KEYS)

    separate = JobConfig.from_frontend({
        **_minimal_values(), "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI", "reviewer_model": "gpt-5.1",
        "reviewer_api_key": "rk"})
    assert set(separate.to_pipeline_kwargs()) >= OPTIONAL_REVIEWER_KWARG_KEYS

    # A half-filled selection falls back to the main model, which is what both
    # frontends did before Phase 1 (the core's role normalizer does the same).
    half = JobConfig.from_frontend({
        **_minimal_values(), "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI", "reviewer_model": ""})
    assert not (set(half.to_pipeline_kwargs()) & OPTIONAL_REVIEWER_KWARG_KEYS)


def test_mirrored_constants_match_core():
    """The duplicated constants are a drift guard, not a second source of truth."""
    from folith import ocr_language
    from folith.application import job_config as module

    assert module.DEFAULT_TARGET_LANG == core.DEFAULT_TARGET_LANG
    assert module.DEFAULT_OCR_MODE == core.DEFAULT_OCR_MODE
    assert module.DEFAULT_OCR_QUALITY == ocr_language.DEFAULT_OCR_QUALITY
    assert module.DEFAULT_BATCH_SIZE == core.BATCH_SIZE
    assert module.DEFAULT_MAX_BATCH_CHARS == core.TRANSLATION_MAX_BATCH_CHARS
    assert module.DEFAULT_PADDLE_MODEL == "official"


# ---------------------------------------------------------------------------
# Test A — same logical configuration through both frontends
# ---------------------------------------------------------------------------

def _minimal_values() -> dict:
    return {
        "provider": "OpenAI",
        "model": "gpt-5.1",
        "api_key": "sk-test",
        "translator_base_url": "https://api.openai.com/v1",
        "target_lang": "English",
    }


def _logical_streamlit_values() -> dict:
    """The same logical configuration Qt can express, in Streamlit's vocabulary."""
    return {
        **_minimal_values(),
        "reasoning_effort": "medium",
        "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI",
        "reviewer_model": "gpt-5.1",
        "reviewer_api_key": "rk",
        "reviewer_base_url": "",
        "auto_term": True,
        "translation_theory": "目的论 (Skos Theory)",
        "user_glossary": [{"source": "FolioThread", "target": "FolioThread"}],
        "style_rules": "保持学术书面语。",
        "enable_review": True,
        "enable_annotate": True,
        "enable_report": True,
        "use_tm": True,
        "enable_understanding": True,
        "enable_source_cleanup": False,
        "segmentation_mode": "sentence",
        "translation_concurrency": 4,
        # The fields Qt cannot express stay at the shared defaults, which is
        # exactly what the desktop client effectively used before Phase 1.
        "batch_size": 4,
        "max_batch_chars": 2400,
        "ocr_language": "",
        "ocr_quality": "standard",
        "ocr_mode": "auto",
        "ocr_backend": "online",
        "ocr_paddle_model": "official",
        "delivery_config": {
            "deliver_plain_docx": True,
            "deliver_bilingual_docx": True,
            "deliver_terms_xlsx": True,
            "deliver_evidence": True,
            "deliver_pdf": False,
            "enable_report": True,
            "enable_annotate": True,
        },
        "source_suffix": ".pdf",
    }


def _logical_qt_engine_config():
    from folith.qt import adapter
    return adapter.EngineConfig(
        provider="OpenAI",
        model="gpt-5.1",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        target_lang="English",
        reasoning_effort="medium",
        auto_term=True,
        enable_review=True,
        use_tm=True,
        enable_understanding=True,
        enable_source_cleanup=False,
        enable_report=True,
        enable_annotate=True,
        translation_theory="目的论 (Skos Theory)",
        style_rules="保持学术书面语。",
        segmentation_mode="sentence",
        translation_concurrency=4,
        reviewer_mode="separate",
        reviewer_provider="OpenAI",
        reviewer_model="gpt-5.1",
        reviewer_api_key="rk",
        reviewer_base_url="",
    )


def test_frontend_equivalence_same_logical_configuration():
    """Test A: Streamlit and Qt adapters agree on the same logical input."""
    from folith.qt import adapter

    state = {"filename": "book.pdf",
             "glossary": [{"source": "FolioThread", "target": "FolioThread"}]}
    streamlit = JobConfig.from_frontend(_logical_streamlit_values(), state=state)
    qt = JobConfig.from_frontend(
        adapter.job_config_values(_logical_qt_engine_config(), state), state=state)

    assert qt.to_dict() == streamlit.to_dict()
    assert qt.fingerprint() == streamlit.fingerprint()
    assert qt.to_pipeline_kwargs() == streamlit.to_pipeline_kwargs()


def test_frontend_equivalence_through_the_public_adapters():
    """Both public entry points resolve to the same core kwargs."""
    from folith.qt import adapter

    state = {"filename": "book.pdf",
             "glossary": [{"source": "FolioThread", "target": "FolioThread"}]}
    qt_kwargs = adapter.pipeline_kwargs(_logical_qt_engine_config(), state)
    streamlit_kwargs = JobConfig.from_frontend(
        _logical_streamlit_values(), state=state).to_pipeline_kwargs()
    assert qt_kwargs == streamlit_kwargs


def test_streamlit_adapter_passes_every_contract_field():
    """The Streamlit mapping must not silently stop supplying a contract field.

    ``app.py`` is a Streamlit script and cannot be imported here, so the mapping
    is inspected statically: every contract keyword must be named in the layer
    builder ``_settings_sources`` (Phase 4) or its predecessor.
    """
    import ast

    source = (Path(__file__).resolve().parent.parent / "app.py").read_text("utf-8")
    tree = ast.parse(source)
    supplied = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and node.name in {"_settings_sources", "_job_config_values",
                                  "_pipeline_kwargs"}):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Dict):
                    supplied |= {
                        key.value for key in inner.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    missing = contract_kwarg_keys() - supplied
    assert not missing, f"app.py._settings_sources omits contract fields: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Test D — resume vs new job
# ---------------------------------------------------------------------------

def test_new_job_uses_live_values():
    config = JobConfig.from_frontend(_minimal_values(), state={})
    assert config.target_lang == "English"
    assert config.enable_understanding is None
    assert config.enable_source_cleanup is None


def test_saved_pipeline_configuration_wins_on_resume():
    state = {
        "pipeline_config": {
            "target_lang": "Deutsch", "auto_term": False, "enable_review": False,
            "batch_size": 8, "max_batch_chars": 6400,
            "ocr_language": "", "ocr_quality": "high", "ocr_dpi": 300,
            "segmentation_mode": "paragraph", "translation_concurrency": 6,
            "strict_terminology_governance": True,
        },
        "p1_done": True,
    }
    config = JobConfig.from_frontend(_minimal_values(), state=state)
    assert config.target_lang == "Deutsch"
    assert config.auto_term is False
    assert config.enable_review is False
    assert config.segmentation_mode == "paragraph"
    assert config.translation_concurrency == 6
    assert config.strict_terminology_governance is True
    assert config.ocr_quality == "high"
    assert config.ocr_dpi == 300
    assert config.ocr_language == "", (
        "an explicit 'auto' OCR language saved with the task must survive resume")


def test_batch_parameters_survive_resume():
    """Divergence fix: a resumed job keeps the batch split it was created with."""
    state = {"pipeline_config": {"batch_size": 8, "max_batch_chars": 6400},
             "p1_done": True}
    config = JobConfig.from_frontend(_minimal_values(), state=state)
    assert (config.batch_size, config.max_batch_chars) == (8, 6400)

    kwargs = JobConfig.from_frontend(_minimal_values(), state=state).to_pipeline_kwargs()
    assert (kwargs["batch_size"], kwargs["max_batch_chars"]) == (8, 6400)

    # A fresh job takes the frontend profile (the "保守" preset by default).
    fresh = JobConfig.from_frontend({**_minimal_values(), "batch_size": 6,
                                     "max_batch_chars": 4800})
    assert (fresh.batch_size, fresh.max_batch_chars) == (6, 4800)


def test_connection_is_live_first_on_resume():
    """Divergence fix: the current connection wins; a stale key is not replayed."""
    state = {
        "pipeline_config": {"provider": "DeepSeek", "model": "deepseek-v4-pro",
                            "api_key": "old-key"},
        "p1_done": True,
    }
    config = JobConfig.from_frontend(_minimal_values(), state=state)
    assert (config.provider, config.model, config.api_key) == (
        "OpenAI", "gpt-5.1", "sk-test")

    # With no live connection the saved one is used rather than nothing.
    fallback = JobConfig.from_frontend(
        {"target_lang": "English"}, state=state)
    assert (fallback.provider, fallback.model) == ("DeepSeek", "deepseek-v4-pro")


def test_state_fallbacks_supply_job_scoped_values():
    state = {
        "p1_done": True,
        "target_lang": "Français",
        "theory": "功能对等",
        "glossary": [{"source": "a", "target": "b"}],
        "quality_mode": True,
        "segmentation": {"mode": "paragraph", "locked": True},
        "research_settings": {"target_words": 3000},
        "literature_sources": ["refs.bib"],
        "report_enabled": True,
        "enable_annotate": True,
    }
    config = JobConfig.from_frontend(_minimal_values(), state=state)
    assert config.target_lang == "Français"
    assert config.translation_theory == "功能对等"
    assert list(config.user_glossary) == [{"source": "a", "target": "b"}]
    assert config.strict_terminology_governance is True
    assert config.segmentation_mode == "paragraph"
    assert config.enable_report is True
    assert config.enable_annotate is True
    assert config.research_settings == {"target_words": 3000}
    assert list(config.literature_sources) == ["refs.bib"]


def test_academic_artifacts_force_the_report_stage_on_resume():
    state = {
        "p1_done": True, "p2_done": True,
        "pipeline_config": {"enable_report": False},
        "academic_state": {"current_stage": "draft_report"},
    }
    config = JobConfig.from_frontend(_minimal_values(), state=state)
    assert config.enable_report is True


def test_reasoning_effort_is_validated_against_the_resolved_model():
    state = {"pipeline_config": {"reasoning_effort": "medium"}, "p1_done": True}
    supported = JobConfig.from_frontend(_minimal_values(), state=state)
    assert supported.reasoning_effort == "medium"

    # gpt-4o is not a reasoning model: the value must not be sent.
    unsupported = JobConfig.from_frontend(
        {**_minimal_values(), "model": "gpt-4o"}, state=state)
    assert unsupported.reasoning_effort == ""


def test_source_cleanup_tristate_on_resume():
    new_pdf = JobConfig.from_frontend({**_minimal_values(), "source_suffix": ".pdf"})
    assert new_pdf.enable_source_cleanup is None, (
        "a new task must let the core apply its documented rule")

    resumed_pdf = JobConfig.from_frontend(
        _minimal_values(), state={"filename": "book.pdf", "p1_done": True})
    assert resumed_pdf.enable_source_cleanup is True

    resumed_docx = JobConfig.from_frontend(
        _minimal_values(), state={"filename": "notes.docx", "p1_done": True})
    assert resumed_docx.enable_source_cleanup is False

    explicit = JobConfig.from_frontend(
        _minimal_values(), state={"filename": "notes.docx", "p1_done": True,
                                  "enable_source_cleanup": False})
    assert explicit.enable_source_cleanup is False


# ---------------------------------------------------------------------------
# Test C — file admission has one authority
# ---------------------------------------------------------------------------

def test_only_ingestible_suffixes_are_advertised():
    from folith.qt import adapter
    assert SOURCE_SUFFIXES == (".pdf", ".docx")
    assert adapter.SUPPORTED_SUFFIXES == SOURCE_SUFFIXES
    assert adapter.MAX_SOURCE_BYTES > 0


def test_qt_import_rejects_formats_the_core_cannot_ingest(tmp_path):
    from folith.qt import adapter
    for suffix in (".doc", ".txt", ".md", ".markdown", ".bib", ".ris"):
        target = tmp_path / f"source{suffix}"
        target.write_bytes(b"content")
        with pytest.raises(adapter.AdapterError) as excinfo:
            adapter.read_source_file(target)
        assert ".pdf" in str(excinfo.value)
        assert ".docx" in str(excinfo.value)


def test_streamlit_admission_matches_the_shared_authority():
    """The Streamlit uploader must advertise exactly the shared suffix set."""
    import re

    source = (Path(__file__).resolve().parent.parent / "app.py").read_text("utf-8")
    match = re.search(
        r"uploaded_files = st\.file_uploader\(.*?type=\[([^\]]*)\]",
        source, re.S)
    assert match, "could not find the Streamlit source uploader"
    advertised = re.findall(r'"([^"]+)"', match.group(1))
    assert tuple(f".{item}" for item in advertised) == SOURCE_SUFFIXES


def test_core_ingests_exactly_the_advertised_formats():
    """The advertised set is exactly what the core extractor can turn into text."""
    docx_bytes = _minimal_docx()
    paragraphs, warnings = core.extract_document_paragraphs("sample.docx", docx_bytes)
    assert paragraphs, warnings

    paragraphs, warnings = core.extract_document_paragraphs(
        "sample.txt", b"hello world\n\nsecond paragraph\n")
    assert not paragraphs
    assert any("不支持" in item for item in warnings)


def test_unsupported_suffix_is_refused_by_the_contract():
    with pytest.raises(JobConfigError):
        JobConfig.from_frontend({**_minimal_values(), "source_suffix": ".txt"})


def _minimal_docx() -> bytes:
    import io
    from docx import Document
    document = Document()
    document.add_paragraph("第一段。")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Validation rules the product already has
# ---------------------------------------------------------------------------

def test_invalid_batch_limits_are_refused():
    with pytest.raises(JobConfigError):
        JobConfig.from_frontend({**_minimal_values(), "batch_size": 0})


def test_translation_concurrency_is_clamped_like_the_core():
    low = JobConfig.from_frontend({**_minimal_values(), "translation_concurrency": 0})
    assert low.translation_concurrency == 1
    high = JobConfig.from_frontend({**_minimal_values(), "translation_concurrency": 99})
    assert high.translation_concurrency == 16


def test_unknown_ocr_backend_is_refused():
    with pytest.raises(JobConfigError):
        JobConfig.from_frontend({**_minimal_values(), "ocr_backend": "not-a-backend"})


def test_unknown_reviewer_mode_is_refused():
    with pytest.raises(JobConfigError):
        JobConfig.from_frontend({**_minimal_values(), "reviewer_mode": "double"})


def test_separate_reviewer_without_a_model_falls_back_instead_of_failing():
    """Pre-Phase-1 behaviour, preserved: an incomplete pairing uses the main model."""
    config = JobConfig.from_frontend({
        **_minimal_values(), "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI", "reviewer_model": ""})
    assert config.separate_reviewer_usable is False
    assert not (set(config.to_pipeline_kwargs()) & OPTIONAL_REVIEWER_KWARG_KEYS)


def test_separate_reviewer_inherits_the_main_address_for_the_same_provider():
    config = JobConfig.from_frontend({
        **_minimal_values(), "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI", "reviewer_model": "gpt-5.1",
        "reviewer_base_url": ""})
    assert config.reviewer_base_url == "https://api.openai.com/v1"


# ---------------------------------------------------------------------------
# Test E — deterministic serialization
# ---------------------------------------------------------------------------

def test_serialization_is_deterministic():
    values = _logical_streamlit_values()
    first = JobConfig.from_frontend(values)
    reordered = JobConfig.from_frontend(dict(reversed(list(values.items()))))
    assert first.to_json() == reordered.to_json()
    assert first.fingerprint() == reordered.fingerprint()

    jittered = dict(values, style_rules=values["style_rules"])
    assert JobConfig.from_frontend(jittered).fingerprint() == first.fingerprint()


def test_different_configurations_have_different_fingerprints():
    base = JobConfig.from_frontend(_minimal_values())
    other = JobConfig.from_frontend({**_minimal_values(), "use_tm": False})
    assert base.fingerprint() != other.fingerprint()


# ---------------------------------------------------------------------------
# Golden contract fixture
# ---------------------------------------------------------------------------

def golden_inputs() -> dict:
    """The inputs behind the frozen contract snapshot."""
    return {
        "streamlit_new_pdf_job": {
            "values": _full_streamlit_values(),
            "state": {},
        },
        "qt_new_pdf_job": {
            "engine": _full_engine_config_kwargs(),
            "state": {"filename": "book.pdf"},
        },
        "resumed_pdf_job": {
            "values": _minimal_values(),
            "state": {
                "filename": "book.pdf",
                "p1_done": True,
                "pipeline_config": {
                    "provider": "DeepSeek",
                    "model": "deepseek-v4-pro",
                    "target_lang": "Deutsch",
                    "auto_term": False,
                    "enable_report": True,
                    "translation_theory": "功能对等",
                    "style_rules": "学术书面语。",
                    "enable_review": False,
                    "enable_annotate": False,
                    "use_tm": True,
                    "strict_terminology_governance": True,
                    "batch_size": 8,
                    "max_batch_chars": 6400,
                    "enable_understanding": True,
                    "reasoning_effort": "high",
                    "ocr_language": "ara",
                    "ocr_quality": "historical",
                    "ocr_dpi": 350,
                    "ocr_psm": 3,
                    "ocr_mode": "force",
                    "ocr_backend": "paddle",
                    "ocr_paddle_model": "official",
                    "reviewer": {"provider": "OpenAI", "model": "gpt-5.1",
                                 "base_url": "https://api.openai.com/v1",
                                 "configured": True},
                    "translator": {"provider": "DeepSeek",
                                   "model": "deepseek-v4-pro",
                                   "base_url": "https://api.deepseek.com/v1",
                                   "configured": True},
                },
                "glossary": [{"source": "术语", "target": "term", "locked": True}],
                "research_settings": {"target_words": 4200, "body_language": "de"},
                "literature_sources": ["refs.bib"],
                "delivery_config": {"deliver_plain_docx": True, "deliver_pdf": True},
            },
        },
    }


def _full_streamlit_values() -> dict:
    return {
        **_minimal_values(),
        "model": "gpt-5.1",
        "reasoning_effort": "high",
        "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI",
        "reviewer_model": "gpt-5.1",
        "reviewer_api_key": "rk",
        "reviewer_base_url": "",
        "target_lang": "English",
        "auto_term": True,
        "translation_theory": "目的论 (Skopos Theory)",
        "user_glossary": [{"source": "术语", "target": "term", "locked": True}],
        "style_rules": "保持学术书面语。",
        "enable_review": True,
        "enable_annotate": True,
        "enable_report": True,
        "use_tm": True,
        "enable_understanding": True,
        "enable_source_cleanup": None,
        "strict_terminology_governance": False,
        "segmentation_mode": "sentence",
        "translation_concurrency": 4,
        "batch_size": 4,
        "max_batch_chars": 2400,
        "ocr_language": "ara",
        "ocr_quality": "high",
        "ocr_dpi": 300,
        "ocr_psm": 3,
        "ocr_mode": "auto",
        "ocr_backend": "paddle",
        "ocr_paddle_model": "official",
        "ocr_online": None,
        "research_settings": {"target_words": 4200, "body_language": "en",
                              "report_stage": "final_report"},
        "literature_sources": ["refs.bib"],
        "delivery_config": {
            "deliver_plain_docx": True, "deliver_bilingual_docx": True,
            "deliver_terms_xlsx": True, "deliver_evidence": True,
            "deliver_pdf": True, "enable_report": True, "enable_annotate": True,
        },
        "source_suffix": ".pdf",
    }


def _full_engine_config_kwargs() -> dict:
    return {
        "provider": "OpenAI", "model": "gpt-5.1", "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1", "target_lang": "English",
        "reasoning_effort": "high", "reviewer_mode": "separate",
        "reviewer_provider": "OpenAI", "reviewer_model": "gpt-5.1",
        "reviewer_api_key": "rk",
    }


def _resolve_golden(name: str) -> JobConfig:
    inputs = golden_inputs()[name]
    state = inputs.get("state") or {}
    if "engine" in inputs:
        from folith.qt import adapter
        config = adapter.EngineConfig(**inputs["engine"])
        values = adapter.job_config_values(config, state)
        return JobConfig.from_frontend(values, state=state)
    return JobConfig.from_frontend(inputs["values"], state=state)


def test_golden_contract_matches_the_frozen_snapshot():
    assert FIXTURE.is_file(), f"missing golden fixture: {FIXTURE}"
    golden = json.loads(FIXTURE.read_text("utf-8"))
    assert set(golden["scenarios"]) == set(golden_inputs())
    for name, expected in golden["scenarios"].items():
        config = _resolve_golden(name)
        assert config.to_dict() == expected["config"], name
        kwargs = config.to_pipeline_kwargs()
        assert sorted(kwargs) == expected["pipeline_keys"], name
        assert config.fingerprint() == expected["fingerprint"], name
