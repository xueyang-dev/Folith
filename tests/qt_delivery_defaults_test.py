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
        # 格式名是复选框，文件名是同一行后面的灰色小字（8.3-6）。
        assert dialog.deliver_epub_box.text() == "EPUB 电子书"
        from PySide6.QtWidgets import QLabel

        names = [label.text() for label in dialog._deliver_epub_holder.findChildren(QLabel)]
        assert "translation.epub" in names
    finally:
        dialog.deleteLater()


def test_delivery_keys_persist_epub():
    from folith.qt.window import MainWindow

    assert "deliver_epub" in MainWindow.DELIVERY_KEYS


# ------------------------------------------------- 3. 引擎状态（侧栏底部）

# 断言对象从 `setup_chip`（一枚「未配置」芯片）换成了 `engine_label` + `engine_dot`：
# 2026-10-09 的构图精修把侧栏那张 100px 高的引擎卡改成"状态点 + 状态词 + 模型串 +
# 入口"，芯片被去掉。要守的性质没变，而且更强了：**文字与颜色必须来自同一份状态**
# （旧用例只看了文字，芯片色调可以单独跑偏）。
# 为什么状态词本身就够用：芯片当年存在的理由是"文字曾停在构造值不动，绿芯片旁边
# 写着未配置"；现在 `set_engine_problem` 是唯一写这两处的地方，所以直接断言它。


def test_engine_state_reports_the_configured_state():
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from folith.qt import i18n
    from folith.qt import views

    home = views.HomeView()
    try:
        home.set_engine_problem("", "OpenCode Go / deepseek-v4.1-flash")
        assert home.engine_label.text() == i18n.tr("已配置")
        assert home.engine_label.text() != i18n.tr("未配置")
        assert home.engine_dot.property("state") == "ok"
        # 模型串是常驻属性：配好了就必须看得见（否则侧栏只剩一个"已配置"）
        assert home.engine_model.isVisible() or "deepseek" in home.engine_model.fullText()
        # 侧栏底部只有「状态 + 设置…」；配置模型的入口只在阻塞时的顶部提示条里出现一次。
        assert home.settings_button.text() == i18n.tr("设置…")
        assert home.setup_banner.isHidden()

        home.set_engine_problem("缺少 API Key", "OpenCode Go / deepseek-v4.1-flash")
        assert home.engine_label.text() == i18n.tr("未配置")
        assert home.engine_dot.property("state") == "warn"
        # 阻塞时顶部提示条必须出现，并且带着配置入口（不能只报错不给路）
        assert home.setup_banner.isVisible() or home.setup_banner.text()
        assert home.setup_banner.action_button().text() == i18n.tr("配置模型连接…")
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

    assert SOURCE_SUFFIXES == (".pdf", ".docx", ".epub", ".txt", ".md", ".markdown")
    declared = {
        uti
        for entry in _spec_document_types()
        for uti in entry.get("LSItemContentTypes") or []
    }
    # Markdown aliases share one UTI; unsupported legacy Word stays excluded.
    assert declared == {
        "com.adobe.pdf",
        "org.openxmlformats.wordprocessingml.document",
        "org.idpf.epub-container",
        "public.plain-text",
        "net.daringfireball.markdown",
    }
    assert "com.microsoft.word.doc" not in declared
    assert adapter.SUPPORTED_SUFFIXES == SOURCE_SUFFIXES
