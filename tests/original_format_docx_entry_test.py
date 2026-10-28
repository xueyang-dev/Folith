"""Desktop entry for the original-format DOCX (DOCX Contract v1, #26).

The engine and its contract tests live in ``docx_contract_v1_test.py``.  These
tests pin the *entry*: one delivery key, off by default, offered for DOCX
sources only, and a delivery that either writes back into the source package or
says why it did not.
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from docx import Document  # noqa: E402
from folith.application import commands as cmd  # noqa: E402
from folith.application import task_setup  # noqa: E402
from folith.application.settings import SettingsSources  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

KEY = "deliver_original_docx"


def _docx_bytes(*texts: str) -> bytes:
    buffer = io.BytesIO()
    document = Document()
    for text in texts:
        document.add_paragraph(text)
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def data_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    return data_dir


def _finished_job(job_id: str, filename: str, source: bytes, *, enabled=True,
                  target="源文本", plain_docx=True) -> dict:
    state = core.new_job_state(filename)
    state.update({
        "p1_done": True, "p2_done": True, "target_lang": "简体中文",
        "paras": ["Source text"], "source_paragraphs": ["Source text"],
        "segmentation": {"mode": "sentence", "paragraph_ranges": [
            {"paragraph_index": 0, "start": 0, "end": 1,
             "gate": {"status": "passed", "reason": "lossless_sentence_split"}}]},
        "pairs": [{"source": "Source text", "target": target, "source_spans": []}],
        "delivery_config": dict(core.default_delivery_config(), **{KEY: enabled},
                                deliver_plain_docx=plain_docx),
    })
    core.save_job_state(job_id, state)
    core.save_source(job_id, source)
    return state


# --- configuration ---------------------------------------------------------

def test_default_is_off_and_normalize_knows_the_key():
    assert core.DELIVERY_CONFIG_DEFAULTS[KEY] is False
    assert core.default_delivery_config()[KEY] is False
    assert core.normalize_delivery_config({KEY: True})[KEY] is True
    # An old task with no such key must not gain an artifact.
    assert core.normalize_delivery_config({"deliver_plain_docx": True})[KEY] is False
    assert KEY in task_setup.DELIVERY_OUTPUT_KEYS
    assert KEY in task_setup.DELIVERY_WIDGET_KEYS
    assert task_setup.delivery_config_for_outputs(KEY)[KEY] is True


def test_applicability_follows_the_source_type():
    assert core.original_format_docx_applicable("a.docx")
    assert core.original_format_docx_applicable("A.DOCX", b"PK")
    assert not core.original_format_docx_applicable("a.pdf")
    assert not core.original_format_docx_applicable("a.docx", b"")
    assert not core.original_format_docx_applicable("")


def test_catalog_row_is_docx_only_and_filtered_for_other_sources():
    items = task_setup.DELIVERY_TRANSLATION_ITEMS
    row = next(item for item in items if item["key"] == KEY)
    assert row["docx_only"] is True
    keys = lambda docx: {i["key"] for i in task_setup.delivery_items_for_source(
        items, False, docx)}
    assert KEY in keys(True)
    assert KEY not in keys(False)
    # Callers that do not say keep every row (existing behaviour).
    assert KEY in {i["key"] for i in task_setup.delivery_items_for_source(items, True)}


# --- delivery bundle -------------------------------------------------------

def test_docx_source_produces_original_format_docx(data_env):
    source = _docx_bytes("Source text")
    state = _finished_job("oformat-docx", "doc.docx", source)
    bundle = core.build_delivery_assets("oformat-docx", state)

    assert "original_format.docx" in bundle
    report = json.loads(bundle["original_format_report.json"])
    assert report["roundtrip"] is True
    assert report["untranslated_units"] == []
    # translation.docx is the usual rebuilt file, not the write-back.
    assert "translation.docx" in bundle
    assert bundle["translation.docx"] != bundle["original_format.docx"]
    # The package was edited in place: the source's own parts are still there.
    with zipfile.ZipFile(io.BytesIO(source)) as src, \
            zipfile.ZipFile(io.BytesIO(bundle["original_format.docx"])) as out:
        assert src.namelist() == out.namelist()
        assert "源文本" in out.read("word/document.xml").decode("utf-8")
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert "original_format.docx" not in (manifest.get("skipped_assets") or {})
    assert "original_format.docx" in json.dumps(manifest)


def test_default_off_produces_nothing(data_env):
    state = _finished_job("oformat-off", "doc.docx", _docx_bytes("Source text"),
                          enabled=False)
    bundle = core.build_delivery_assets("oformat-off", state)
    assert "original_format.docx" not in bundle
    assert "original_format_report.json" not in bundle


def test_non_docx_source_is_skipped_with_a_reason(data_env):
    state = _finished_job("oformat-pdf", "book.pdf", b"%PDF-1.4 not a real pdf",
                         plain_docx=False)
    bundle = core.build_delivery_assets("oformat-pdf", state)
    assert "original_format.docx" not in bundle
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert "DOCX source" in manifest["skipped_assets"]["original_format.docx"]


def test_untranslated_unit_is_visible_in_report_and_notes(data_env):
    state = _finished_job("oformat-gap", "doc.docx", _docx_bytes("Source text"),
                          target="")
    bundle = core.build_delivery_assets("oformat-gap", state)
    report = json.loads(bundle["original_format_report.json"])
    assert [u["reason"] for u in report["untranslated_units"]] == ["no_target"]
    manifest = json.loads(bundle["delivery_manifest.json"])
    assert any("没有译文" in note for note in manifest.get("renderer_notes") or [])


def test_failed_write_back_is_an_explicit_error_not_a_rebuild(data_env):
    state = _finished_job("oformat-bad", "doc.docx", b"this is not a zip package",
                         plain_docx=False)
    with pytest.raises(RuntimeError, match="原格式 DOCX 回写失败"):
        core.build_delivery_assets("oformat-bad", state)


def test_supplement_is_refused_for_a_non_docx_source(data_env):
    _finished_job("oformat-supp", "book.pdf", b"%PDF-1.4")
    with pytest.raises(cmd.CommandRejected):
        cmd.enable_delivery_outputs("oformat-supp", [KEY])


# --- Qt --------------------------------------------------------------------

@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def test_qt_job_config_keeps_the_choice_for_docx_and_drops_it_otherwise():
    from folith.qt import adapter

    config = adapter.EngineConfig(**{KEY: True})
    assert adapter.EngineConfig().deliver_original_docx is False
    kept = adapter.job_config_values(config, {"filename": "a.docx"})
    dropped = adapter.job_config_values(config, {"filename": "a.pdf"})
    assert kept["delivery_config"][KEY] is True
    assert dropped["delivery_config"][KEY] is False


def _panel(data_env, filename, payload):
    from folith.qt import adapter
    from folith.qt.setup_panel import TaskSetupPanel

    imported = cmd.import_document(filename, payload, SettingsSources(explicit={}))
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), imported.job_id))
    return panel, imported.job_id


def test_qt_switch_is_enabled_for_docx_and_round_trips(data_env, qt_app):
    from folith.qt import adapter

    panel, job_id = _panel(data_env, "doc.docx", _docx_bytes("Chapter One. Opening."))
    box = panel._delivery_boxes[KEY]
    assert box.isEnabled() and not box.isChecked()
    box.setChecked(True)
    payload = panel.payload()
    assert payload["delivery"][KEY] is True
    adapter.apply_task_setup(adapter.EngineConfig(), job_id, payload)
    assert core.load_job_state(job_id)["delivery_config"][KEY] is True


def test_qt_switch_is_disabled_and_explained_for_other_sources(data_env, qt_app):
    panel, _job = _panel(data_env, "notes.txt", b"Chapter One. Opening paragraph.\n")
    box = panel._delivery_boxes[KEY]
    assert not box.isEnabled() and not box.isChecked()
    assert "DOCX" in box.toolTip()
    box.setChecked(True)          # even if forced, the payload never carries it
    assert panel.payload()["delivery"][KEY] is False
