"""翻译风格：基础风格 + 微调 + 智能画像（原向导独有，Qt 原来没有）。

* 规则文本由共享的 ``profile_to_rules`` 生成（含四个微调与自定义规则），界面不另写；
* 智能画像读真实原文、走一次模型调用；失败降级为通用风格并**不给"接受推荐"**；
* 不选风格 = 沿用设置里的规则（原来的行为不变）；选了才覆盖，并随准备面板的载荷走；
* 画像需要完整的模型连接，缺什么先说什么。
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402


def _docx(paragraphs):
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture()
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "styleprofile000001"
    state = core.new_job_state("paper.docx")
    state["target_lang"] = "简体中文"
    core.save_job_state(job_id, state)
    core.save_source(job_id, _docx(["Abstract. This paper examines translation norms."] * 6))
    return job_id


def _config(**overrides):
    values = dict(provider=next(iter(core.PROVIDERS)), model="m", api_key="k",
                  target_lang="简体中文")
    values.update(overrides)
    return adapter.EngineConfig(**values)


def test_choices_come_from_the_shared_profiles():
    ids = [c["id"] for c in adapter.style_profile_choices()]
    assert "academic" in ids and "general" in ids
    assert all(c["name"] for c in adapter.style_profile_choices())


def test_rules_include_adjustments_and_custom_rules():
    rules = adapter.style_rules_from_selection({
        "selected": "academic", "source": "user_override",
        "adjustments": {"formality": 80, "terminology": 55,
                        "restructuring": 20, "form_preservation": 90},
        "custom_rules": "人名保留原文"})
    assert "表达正式度：80/100" in rules and "原文形式保留：90/100" in rules
    assert rules.endswith("人名保留原文。")
    assert "保持正式" in rules      # 基础风格自带的规则


def test_unknown_style_falls_back_to_general_rules():
    assert adapter.style_rules_from_selection({"selected": "nope"})


def test_quick_profile_reads_the_source_and_returns_a_recommendation(job, monkeypatch):
    seen = {}

    def fake_llm(provider, api_key, model, system, prompt, **kwargs):
        seen["prompt"] = prompt
        return json.dumps({
            "document_profile": {"domain": "翻译学", "genre": "学术论文"},
            "style_recommendation": {
                "recommended_style": "academic", "confidence": 0.82,
                "reasons": ["术语密集", "学术引用"], "domain": ["翻译学"]}})

    monkeypatch.setattr(core, "call_llm", fake_llm)
    result = adapter.quick_style_profile(job, _config())
    assert "translation norms" in seen["prompt"], "画像必须基于真实原文样本"
    assert result["failed"] is False
    assert result["recommendation"]["recommended_style"] == "academic"
    assert result["document_profile"]["domain"] == "翻译学"


def test_model_failure_degrades_to_general_and_is_flagged(job, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(core, "call_llm", broken)
    result = adapter.quick_style_profile(job, _config())
    assert result["failed"] is True
    assert result["recommendation"]["recommended_style"] == "general"
    assert result["warnings"]


def test_incomplete_connection_is_reported_first(job):
    with pytest.raises(adapter.AdapterError):
        adapter.quick_style_profile(job, _config(api_key=""))


def test_missing_source_is_a_readable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    core.save_job_state("nosource0000000001", core.new_job_state("x.docx"))
    with pytest.raises(adapter.AdapterError, match="原文"):
        adapter.quick_style_profile("nosource0000000001", _config())


# ---------------------------------------------------------------- 面板 --------

@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def panel(qapp):
    from folith.qt.setup_panel import TaskSetupPanel

    return TaskSetupPanel()


def test_default_keeps_the_settings_rules(panel):
    assert panel.style_selection() is None
    assert panel.payload()["style_selection"] is None
    assert not any(slider.isEnabled() for slider in panel.style_sliders.values())


def test_choosing_a_style_overrides_the_rules_in_the_payload(panel):
    panel.style_box.setCurrentIndex(panel.style_box.findData("academic"))
    assert all(slider.isEnabled() for slider in panel.style_sliders.values())
    panel.style_sliders["formality"].setValue(85)
    panel.style_custom.setText("保留引文格式")
    payload = panel.payload()
    assert payload["style_selection"]["selected"] == "academic"
    assert "表达正式度：85/100" in payload["style_rules"]
    assert payload["style_rules"].endswith("保留引文格式。")


def test_recommendation_can_be_accepted(panel):
    panel.set_style_recommendation({"recommendation": {
        "recommended_style": "technical", "confidence": 0.7, "reasons": ["术语一致"]},
        "failed": False})
    assert not panel.style_accept_button.isHidden()
    assert "术语一致" in panel.style_status.text()
    panel.style_accept_button.click()
    assert panel.style_box.currentData() == "technical"


def test_a_failed_profile_offers_nothing_to_accept(panel):
    panel.set_style_recommendation({"recommendation": {
        "recommended_style": "general", "confidence": 0.0, "reasons": ["无法完成自动画像"]},
        "failed": True})
    assert panel.style_accept_button.isHidden()
    assert panel.style_box.currentData() == "", "失败降级不能悄悄选中一个风格"


def test_profile_button_asks_the_window_and_shows_busy_state(panel):
    seen = []
    panel.styleProfileRequested.connect(lambda: seen.append(1))
    panel.style_profile_button.click()
    assert seen == [1]
    panel.set_style_busy(True)
    assert not panel.style_profile_button.isEnabled()
    panel.set_style_busy(False)
    assert panel.style_profile_button.isEnabled()


def test_style_is_a_collapsed_row_without_manual_sliders(qapp):
    """风格不是核心设置：默认收起、沿用设置；展开后没有人工调节的滑块，示例只有一句。"""
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    panel.resize(1100, 1200)
    panel.show()
    qapp.processEvents()
    try:
        section = panel.style_section
        assert section.isVisible() and not section.is_expanded()
        # 没手动选风格时，风格来自智能画像的分析建议。
        assert section.summary_label.text() == "智能画像分析建议"
        # 它和「文档识别」同在「输入与上下文」卡片里，排在核心设置之后。
        assert section.parent() is panel.doc_section.parent()
        assert all(slider.isHidden() for slider in panel.style_sliders.values())
        section.button.setChecked(True)
        # 示例跟着目标语言走（简体中文有示例）。
        panel.target_box.setEditText("简体中文")
        panel._pick_style_card("literary")
        qapp.processEvents()
        assert section.summary_label.text() == panel.style_box.currentText()
        assert all(slider.isHidden() for slider in panel.style_sliders.values())
        # 选中风格仍按它的默认强度写进任务配置。
        selection = panel.style_selection()
        assert selection["selected"] == "literary"
        assert set(selection["adjustments"]) == set(panel.style_sliders)
        assert panel.style_sample_row.isVisible() and panel.style_sample.text()
        samples = [label for label in panel.findChildren(type(panel.style_sample))
                   if label.objectName() == "styleSample" and label.isVisible()]
        assert len(samples) == 1, "页面上同一时刻只有一句示例"
    finally:
        panel.close()
