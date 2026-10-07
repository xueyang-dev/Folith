"""Delivery contract for the original-layout PDF option.

The artifact rebuilds the source document's own pages, so it exists for PDF
tasks only.  These tests pin the contract on both shells and on a saved job,
including the promise that an old task's Word/EPUB/bilingual behaviour does not
change.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402


def _pdf_bytes() -> bytes:
    import pymupdf
    document = pymupdf.open()
    page = document.new_page(width=300, height=180)
    page.insert_text((40, 60), "A source sentence.", fontsize=12, fontname="helv")
    data = document.tobytes()
    document.close()
    return data


def test_the_applicability_predicate_follows_the_source_type():
    assert core.original_layout_pdf_applicable("book.pdf")
    assert core.original_layout_pdf_applicable("BOOK.PDF")
    assert core.original_layout_pdf_applicable("", _pdf_bytes())
    assert not core.original_layout_pdf_applicable("report.docx")
    assert not core.original_layout_pdf_applicable("")
    assert not core.original_layout_pdf_applicable("report.docx", b"PK\x03\x04")


def test_qt_import_keeps_the_option_for_a_pdf_task():
    from folith.qt import adapter

    config = adapter.EngineConfig(deliver_original_pdf=True)
    values = adapter.job_config_values(config, {"filename": "book.pdf"})
    assert values["delivery_config"]["deliver_original_pdf"] is True


def test_qt_import_drops_the_option_for_a_non_pdf_task():
    from folith.qt import adapter

    config = adapter.EngineConfig(deliver_original_pdf=True)
    values = adapter.job_config_values(config, {"filename": "report.docx"})
    assert values["delivery_config"]["deliver_original_pdf"] is False
    # The other delivery choices are untouched.
    assert values["delivery_config"]["deliver_plain_docx"] == \
        config.deliver_plain_docx


def test_the_qt_default_is_off():
    from folith.qt import adapter

    assert adapter.EngineConfig().deliver_original_pdf is False
    assert core.DELIVERY_CONFIG_DEFAULTS["deliver_original_pdf"] is False


def test_a_saved_job_without_the_key_behaves_as_before():
    """Old tasks carry no such key; they must not gain an artifact."""
    config = core.normalize_delivery_config({"deliver_plain_docx": True})
    assert config["deliver_original_pdf"] is False


def test_a_docx_job_that_asks_for_it_records_the_skip(tmp_path, monkeypatch):
    """A stale saved choice is reported, never silently honoured."""
    import io
    from docx import Document

    job_id = "delivery-contract-docx"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    document = Document()
    document.add_paragraph("Source text")
    buffer = io.BytesIO()
    document.save(buffer)

    state = core.new_job_state("doc.docx")
    state.update({
        "p1_done": True, "p2_done": True, "target_lang": "简体中文",
        "paras": ["Source text"], "source_paragraphs": ["Source text"],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "Source text", "target": "源文本",
                   "source_spans": []}],
        "delivery_config": dict(core.default_delivery_config(),
                                deliver_original_pdf=True),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, buffer.getvalue())

    bundle = core.build_delivery_assets(job_id, state)
    assert "original_layout.pdf" not in bundle
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert manifest["skipped_assets"]["original_layout.pdf"]
    # Word delivery itself is unchanged.
    assert bundle["translation.docx"].startswith(b"PK")


def test_a_pdf_job_with_the_option_off_produces_no_artifact(tmp_path, monkeypatch):
    job_id = "delivery-contract-pdf-off"
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    state = core.new_job_state("book.pdf")
    state.update({
        "p1_done": True, "p2_done": True, "target_lang": "简体中文",
        "paras": ["A source sentence."],
        "source_paragraphs": ["A source sentence."],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "A source sentence.", "target": "一句原文。",
                   "source_spans": []}],
        "delivery_config": dict(core.default_delivery_config()),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, _pdf_bytes())
    bundle = core.build_delivery_assets(job_id, state)
    assert "original_layout.pdf" not in bundle
    assert bundle["translation.docx"].startswith(b"PK")
