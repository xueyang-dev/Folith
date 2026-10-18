"""Three OCR sources flowing into cleanup, segmentation, translation and export.

The online source is the interesting one: it is the only source that returns text
without line geometry, so this file proves that a coordinate-less page result
still reaches every downstream stage and the delivered document.  Translation
uses the deterministic offline provider double from ``tests/offline_provider.py``
— no external model is called here (that is stated per test).
"""
from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

import core
from folith import ocr_online

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
ONLINE_MODEL = "vision-fixture"


def pdf(pages=1):
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page(width=144, height=144)
        return doc.tobytes()


class _Usage:
    def model_dump(self):
        return {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60,
                "completion_tokens_details": {"reasoning_tokens": 0}}


class FixedTranscriptionClient:
    """Fixed Arabic transcription; counts requests so reuse is observable."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **body):
        self.requests.append(body)
        rows = [{"index": index, "text": text, "break_after": index == len(self.lines) - 1}
                for index, text in enumerate(self.lines)]
        if '"b"' in body['messages'][0]['content']:
            payload = json.dumps({"b":[{"t":"paragraph", "s":" ".join(self.lines), "d":"rtl"}]}, ensure_ascii=False)
        else:
            payload = json.dumps({"segments": rows}, ensure_ascii=False)
        return type("Resp", (), {
            "id": f"req-{len(self.requests)}", "model": ONLINE_MODEL,
            "usage": _Usage(),
            "choices": [type("Choice", (), {
                "finish_reason": "stop",
                "message": type("Message", (), {"content": payload})()})()],
        })()


def save_online_profile(model=ONLINE_MODEL, max_tokens=8192):
    """Save the credential-bearing OCR profile, as the settings page does."""
    core.save_ocr_online_config(provider="OpenCode Go", model=model,
                                api_key="fixture-key",
                                base_url="https://relay.example/v1",
                                max_tokens=max_tokens)


def online_descriptor(model=ONLINE_MODEL):
    """Secret-free snapshot, exactly what a task persists with itself."""
    config, error = core.resolve_ocr_online_config(
        {"credential_source": "ocr_profile", "model": model})
    assert error is None and config is not None
    return config.describe()


@pytest.fixture
def online_client(monkeypatch):
    client = FixedTranscriptionClient(["مرحبا بالعالم.", "هذا اختبار للترجمة."])
    monkeypatch.setattr(ocr_online.OnlineOCRBackend, "_get_client", lambda self: client)
    return client


def test_online_result_reaches_cleanup_segmentation_translation_and_docx(
        tmp_path, monkeypatch, online_client):
    from offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    save_online_profile()
    args = dict(provider="DeepSeek", api_key="offline", model="fixture",
                target_lang="简体中文", auto_term=False, enable_report=False,
                translation_theory="", user_glossary=[], enable_review=False,
                enable_annotate=False, use_tm=False, enable_understanding=False,
                enable_source_cleanup=True,
                delivery_config={"deliver_report": False},
                ocr_language="ara", ocr_dpi=144, ocr_mode="force")
    job_id = "online-pipeline"
    source = pdf(1)
    state = core.run_job_pipeline(
        job_id, "scan.pdf", source, ocr_backend="online",
        ocr_online=online_descriptor(), **args)

    assert state["p1_done"] and state["p2_done"]
    assert state["source_paras"], "OCR text must reach the raw source paragraphs"
    assert "مرحبا بالعالم" in "\n".join(state["source_paras"])
    assert state["paras"], "the coordinate-less transcript must be segmented"
    assert state["pairs"], "the segments must reach translation"
    assert state["extraction_report"]["semantic_source"] == "vlm"
    diag = state["extraction_report"]["page_diagnostics"][0]
    assert diag["model"] == ONLINE_MODEL
    assert diag["usage"]["total_tokens"] == 60
    assert state["source_cleanup"]["status"] == "skipped"
    assert state["pairs"][0]["source_spans"][0]["page_id"] == "p1"
    # The requested source and its secret-free connection description are saved.
    assert state["pipeline_config"]["ocr_backend"] == "online"
    assert state["pipeline_config"]["ocr_online"]["model"] == ONLINE_MODEL
    assert state["ocr_config"]["ocr_online"]["model"] == ONLINE_MODEL
    # No credential reaches the task snapshot on disk.
    raw_state = (core.job_state_path(job_id)).read_text(encoding="utf-8")
    assert "fixture-key" not in raw_state
    assert online_client.requests[0]["messages"][1]["content"][0]["type"] == "image_url"

    delivery = core.build_delivery_assets(job_id, state)
    assert delivery["translation.docx"].startswith(b"PK")

    # Resume: the same online connection reuses the cached page, no new request.
    calls = len(online_client.requests)
    resumed = core.run_job_pipeline(
        job_id, "scan.pdf", source, ocr_backend="tesseract",
        ocr_online=online_descriptor(), **args)
    assert resumed["pipeline_config"]["ocr_backend"] == "online"
    assert len(online_client.requests) == calls


def test_rescan_with_a_different_ocr_source_resets_source_derived_state(
        tmp_path, monkeypatch, online_client):
    from offline_provider import OfflineProvider

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "call_llm", OfflineProvider())
    save_online_profile()
    args = dict(provider="DeepSeek", api_key="offline", model="fixture",
                target_lang="简体中文", auto_term=False, enable_report=False,
                translation_theory="", user_glossary=[], enable_review=False,
                enable_annotate=False, use_tm=False, enable_understanding=False,
                enable_source_cleanup=True,
                delivery_config={"deliver_report": False},
                ocr_language="ara", ocr_dpi=144, ocr_mode="force")
    job_id = "online-rescan"
    source = pdf(1)
    state = core.run_job_pipeline(
        job_id, "scan.pdf", source, ocr_backend="online",
        ocr_online=online_descriptor(), **args)
    assert state["pairs"]
    previous_pairs = len(state["pairs"])

    # An explicit rescan that changes the online model is a different OCR
    # configuration: old translations must not survive next to the new source.
    rescanned = core.rescan_pdf_source(
        job_id, filename="scan.pdf", provider="DeepSeek", api_key="offline",
        model="fixture", ocr_backend="online", ocr_language="ara", ocr_dpi=144,
        ocr_mode="force", ocr_online=online_descriptor(model="other-model"))
    cleanup = rescanned["source_cleanup"]
    assert cleanup["ocr_source"] == "online"
    assert cleanup["ocr_source_changed"] is True
    assert cleanup["previous_pair_count"] == previous_pairs
    assert not (rescanned.get("pairs") or []), "old translations must be invalidated"
    assert rescanned["pipeline_config"]["ocr_online"]["model"] == "other-model"


