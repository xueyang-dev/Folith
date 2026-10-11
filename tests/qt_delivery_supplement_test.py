"""「交付」页的补生成：翻译完成后补出当初没选的文件（例如电子书）。

用户翻译完一本书时没选电子书，后来又需要：不应该重新翻译。补生成只打开这个
文件的交付开关，再走同一道交付门禁冻结一个新版本；旧版本不变。
"""
from __future__ import annotations

import io
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


def _make_docx(texts: list[str]) -> bytes:
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for text in texts:
        doc.add_paragraph(text)
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    from folith.qt import adapter

    return {"core": core, "adapter": adapter, "tmp": tmp_path}


def _finished(env, *, translated: bool = True) -> tuple[str, object]:
    adapter, core = env["adapter"], env["core"]
    source = env["tmp"] / "book.docx"
    source.write_bytes(_make_docx(["第一段。", "第二段。"]))
    config = adapter.EngineConfig(target_lang="English", deliver_epub=False)
    job_id = adapter.import_documents([source], config)[0].job_id
    state = core.load_job_state(job_id) or {}
    state["paras"] = ["第一段。", "第二段。"]
    state["pairs"] = [
        {"source": "第一段。", "target": "First paragraph.", "reviewed": True,
         "segment_uid": "s0"},
        {"source": "第二段。", "target": "Second paragraph.", "reviewed": True,
         "segment_uid": "s1"},
    ]
    state["p1_done"] = True
    state["p2_done"] = translated
    core.save_job_state(job_id, state)
    return job_id, config


def _asset_names(core, job_id: str, version: int) -> set[str]:
    manifest = next(item for item in core.list_delivery_snapshots(job_id)
                    if item.get("snapshot_version") == version)
    return {item["name"] for item in manifest.get("assets") or []}


def test_offers_only_files_that_can_be_added_without_retranslating(env):
    adapter = env["adapter"]
    job_id, config = _finished(env)
    adapter.deliver(job_id, config)
    keys = {item["key"] for item in adapter.supplementable_outputs(job_id)}
    assert "deliver_epub" in keys
    # 原排版 PDF 只适用于 PDF 原文；标注版跟随任务开关；研究产物需要整次运行。
    assert "deliver_original_pdf" not in keys
    assert "enable_annotate" not in keys
    assert "enable_report" not in keys
    # 已经交付的文件不再列出。
    assert "deliver_bilingual_docx" not in keys


def test_adding_the_ebook_freezes_a_new_version_and_keeps_the_old_one(env):
    adapter, core = env["adapter"], env["core"]
    job_id, config = _finished(env)
    first = adapter.deliver(job_id, config)
    assert "translation.epub" not in _asset_names(core, job_id, first["version"])

    result = adapter.add_delivery_outputs(job_id, ["deliver_epub"], config)

    assert result["version"] == first["version"] + 1
    names = _asset_names(core, job_id, result["version"])
    assert "translation.epub" in names
    assert _asset_names(core, job_id, first["version"]) <= names
    assert "translation.epub" not in _asset_names(core, job_id, first["version"])
    assert adapter.delivery_preview(job_id, config)["snapshot"]["current"]
    assert "deliver_epub" not in {
        item["key"] for item in adapter.supplementable_outputs(job_id)}


def test_refused_before_the_translation_is_finished(env):
    adapter = env["adapter"]
    from folith.application import commands

    job_id, config = _finished(env, translated=False)
    assert adapter.supplementable_outputs(job_id) == []
    with pytest.raises(commands.CommandRejected):
        commands.enable_delivery_outputs(job_id, ["deliver_epub"])
    with pytest.raises(adapter.AdapterError):
        adapter.add_delivery_outputs(job_id, ["deliver_epub"], config)


def test_research_items_cannot_be_added_afterwards(env):
    from folith.application import commands

    job_id, _config = _finished(env)
    with pytest.raises(commands.CommandRejected):
        commands.enable_delivery_outputs(job_id, ["enable_report"])


def test_a_failed_delivery_puts_the_choice_back(env, monkeypatch):
    adapter, core = env["adapter"], env["core"]
    job_id, config = _finished(env)
    adapter.deliver(job_id, config)
    before = dict(core.load_job_state(job_id)["delivery_config"])

    def refuse(*_args, **_kwargs):
        raise adapter.AdapterError("门禁拒绝")

    monkeypatch.setattr(adapter, "deliver", refuse)
    with pytest.raises(adapter.AdapterError):
        adapter.add_delivery_outputs(job_id, ["deliver_epub"], config)
    assert core.load_job_state(job_id)["delivery_config"] == before
    assert adapter.delivery_preview(job_id, config)["snapshot"]["current"]


def test_delivery_page_shows_a_button_per_missing_file():
    app = QApplication.instance() or QApplication([])
    from folith.qt import views

    bench = views.WorkbenchView()
    emitted: list[str] = []
    bench.supplementRequested.connect(emitted.append)
    bench._fill_supplements([
        {"key": "deliver_epub", "title": "译文电子书", "format": "EPUB", "detail": ""},
        {"key": "deliver_tmx", "title": "翻译记忆", "format": "TMX", "detail": ""},
    ])
    assert not bench.supplement_card.isHidden()
    button = bench.supplement_buttons["deliver_epub"]
    assert button.text() == "生成译文电子书（EPUB）"
    button.click()
    assert emitted == ["deliver_epub"]
    bench._fill_supplements([])
    assert bench.supplement_card.isHidden()
    assert bench.supplement_buttons == {}
    bench.deleteLater()
    app.processEvents()


def test_pause_button_stays_while_the_task_waits_on_the_model():
    """暂停按钮不能随着 running ⇄ waiting_external 来回切换而一闪一闪。"""
    app = QApplication.instance() or QApplication([])
    from folith.qt import views

    bench = views.WorkbenchView()
    for status in ("running", "waiting_external", "running"):
        bench._last_runtime = {"status": status, "available_actions": ["cancel"]}
        bench._render_runtime()
        assert not bench.pause_button.isHidden(), status
        assert bench.pause_button.isEnabled()
    bench._last_runtime = {"status": "pausing", "available_actions": ["cancel"]}
    bench._render_runtime()
    assert not bench.pause_button.isHidden()
    assert not bench.pause_button.isEnabled()
    assert bench.pause_button.text() == "正在暂停…"
    bench._last_runtime = {"status": "paused", "available_actions": ["resume", "cancel"]}
    bench._render_runtime()
    assert bench.pause_button.isHidden()
    bench.deleteLater()
    app.processEvents()
