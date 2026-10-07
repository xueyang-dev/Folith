"""Delivery defaults, engine status and Finder document types (regression guards).

Four defects found by the local release-candidate acceptance on the frozen
`.app`; each test below fails on the build that was rejected and passes after
the repair:

1. A ticked 「原排版译文 PDF（Beta）」 was dropped for **every** new task: the
   settings layer was resolved with an empty state, and the original-layout gate
   reads the source filename, so the gate answered "not applicable" for a file
   it had not been told about.
2. EPUB was not exposed anywhere in the Qt client, so `translation.epub` could
   never be requested from the shipped app even though the core builds it.
3. The sidebar engine chip's *text* was never updated — a configured client
   still read 「未配置」 next to a green chip.
4. `CFBundleDocumentTypes` advertised `.txt` / `.md` / `.doc`, which the import
   layer refuses, so Finder offered Folith for files it cannot open.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))


def _make_docx(texts: list[str]) -> bytes:
    import io

    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    buf.seek(0)
    doc.save(buf)
    return buf.getvalue()


def _make_pdf(text: str = "A real paragraph for the delivery defaults.") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 300), text, fontsize=9, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def adapter_env(tmp_path, monkeypatch):
    """Isolate one adapter against a temp data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_DESKTOP_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))

    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)

    from folith.qt import adapter

    yield {"adapter": adapter, "data_dir": data_dir, "tmp": tmp_path, "core": core}


# ---------------------------------------------------------------- 1. gate


def test_original_layout_default_reaches_a_new_pdf_task(adapter_env):
    """A ticked Beta option must survive import for a PDF source."""
    adapter = adapter_env["adapter"]
    data_dir = adapter_env["data_dir"]
    tmp = adapter_env["tmp"]

    source = tmp / "paper.pdf"
    source.write_bytes(_make_pdf())
    config = adapter.EngineConfig(target_lang="简体中文", deliver_original_pdf=True)

    imported = adapter.import_documents([source], config)
    assert len(imported) == 1

    state = json.loads(
        (data_dir / imported[0].job_id / "state.json").read_text(encoding="utf-8"))
    assert state["delivery_config"]["deliver_original_pdf"] is True


def test_original_layout_default_is_dropped_for_a_non_pdf_task(adapter_env):
    """The same ticked option must never promise an artifact a DOCX cannot have."""
    adapter = adapter_env["adapter"]
    data_dir = adapter_env["data_dir"]
    tmp = adapter_env["tmp"]

    source = tmp / "paper.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。"]))
    config = adapter.EngineConfig(target_lang="简体中文", deliver_original_pdf=True)

    imported = adapter.import_documents([source], config)
    state = json.loads(
        (data_dir / imported[0].job_id / "state.json").read_text(encoding="utf-8"))
    assert state["delivery_config"]["deliver_original_pdf"] is False


# ---------------------------------------------------------------- 2. EPUB


def test_epub_is_exposed_and_reaches_the_task(adapter_env):
    adapter = adapter_env["adapter"]
    data_dir = adapter_env["data_dir"]
    tmp = adapter_env["tmp"]

    config = adapter.EngineConfig(target_lang="简体中文", deliver_epub=True)
    source = tmp / "book.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。"]))
    imported = adapter.import_documents([source], config)

    state = json.loads(
        (data_dir / imported[0].job_id / "state.json").read_text(encoding="utf-8"))
    assert state["delivery_config"]["deliver_epub"] is True

    # The delivery preview must name the artifact the core will build.
    preview = adapter.delivery_preview(imported[0].job_id)
    assert "translation.epub" in preview["asset_names"]


def test_settings_dialog_offers_the_epub_option():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt import adapter, dialogs

    dialog = dialogs.EngineDialog(adapter.EngineConfig(deliver_epub=True), None, None)
    try:
        assert dialog.deliver_epub_box.isChecked() is True
        assert "translation.epub" in dialog.deliver_epub_box.text()
    finally:
        dialog.deleteLater()


def test_delivery_keys_persist_epub():
    from folith.qt.window import MainWindow

    assert "deliver_epub" in MainWindow.DELIVERY_KEYS


# ---------------------------------------------------------------- 3. chip


def test_engine_chip_reports_the_configured_state():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt import i18n
    from folith.qt import views

    home = views.HomeView()
    try:
        home.set_engine_problem("", "OpenCode Go / deepseek-v4.1-flash")
        assert home.setup_chip.text() == i18n.tr("已配置")
        assert home.setup_chip.text() != i18n.tr("未配置")

        home.set_engine_problem("缺少 API Key", "OpenCode Go / deepseek-v4.1-flash")
        assert home.setup_chip.text() == i18n.tr("未配置")
    finally:
        home.deleteLater()


def test_configured_chip_text_is_translated_in_every_locale():
    locales = ROOT / "folith" / "resources" / "locales"
    for name in ("qt-zh-CN", "qt-en", "qt-es", "qt-ru", "qt-ar"):
        catalog = json.loads((locales / f"{name}.json").read_text(encoding="utf-8"))
        assert catalog.get("已配置"), name
        assert catalog.get("未配置"), name
    english = json.loads((locales / "qt-en.json").read_text(encoding="utf-8"))
    assert english["已配置"] == "Configured"
    assert english["已配置"] != english["未配置"]


# ------------------------------------------------------------ 4. Finder


def _spec_document_types() -> list[dict]:
    spec = (ROOT / "packaging" / "folith-qt.spec").read_text(encoding="utf-8")
    tree = ast.parse(spec)
    constants: dict[str, object] = {}
    node = None
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    try:
                        constants[target.id] = ast.literal_eval(statement.value)
                    except ValueError:
                        pass
                    if target.id == "DOCUMENT_TYPES":
                        node = constants[target.id]
    assert node, "the spec must declare DOCUMENT_TYPES"
    return node


def test_bundle_declares_only_ingestible_document_types():
    """Finder must not be told Folith opens a type the import layer refuses."""
    from folith.application.job_config import SOURCE_SUFFIXES
    from folith.qt import adapter

    assert SOURCE_SUFFIXES == (".pdf", ".docx")
    declared = {
        uti
        for entry in _spec_document_types()
        for uti in entry.get("LSItemContentTypes") or []
    }
    # One declared UTI per ingestible suffix — no extra text/markdown/legacy
    # Word types that would only produce "不支持的文件类型" after a double-click.
    assert declared == {
        "com.adobe.pdf",
        "org.openxmlformats.wordprocessingml.document",
    }
    assert "public.plain-text" not in declared
    assert "net.daringfireball.markdown" not in declared
    assert "com.microsoft.word.doc" not in declared
    assert adapter.SUPPORTED_SUFFIXES == SOURCE_SUFFIXES
