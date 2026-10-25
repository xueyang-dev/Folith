"""原文语言：目录匹配、交给视觉模型的提示、智能画像识别、准备页与任务的往返。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"


def test_catalogue_matching_never_guesses():
    from folith import source_languages as s

    assert s.match("乌尔都语") == s.match("Urdu") == s.match("urd") == "اردو"
    assert s.match("维吾尔语") == "ئۇيغۇرچە" and s.match("Persian") == "فارسی"
    assert s.match("土耳其语") == s.match("Turkish") == "Türkçe"
    assert s.match("荷兰语") == s.match("Dutch") == s.match("nld") == "Nederlands"
    assert s.match("波兰语") == s.match("Polish") == s.match("pol") == "Polski"
    assert s.match("乌克兰语") == s.match("Ukrainian") == s.match("ukr") == "Українська"
    assert s.match("越南语") == s.match("Vietnamese") == s.match("vie") == "Tiếng Việt"
    assert s.match("希伯来语") == s.match("Hebrew") == "עברית"
    assert s.match("克林贡语") == "" and s.match("") == ""
    assert s.english_name("اردو") == "Urdu"
    assert s.english_name("Nederlands") == "Dutch"
    assert s.english_name("Polski") == "Polish"
    assert s.english_name("Українська") == "Ukrainian"
    assert s.english_name("Tiếng Việt") == "Vietnamese"
    # 不在目录里的标签（例如本机 OCR 的组合标签）不变成模型提示。
    assert s.english_name("阿拉伯语 + 英语") == ""
    assert s.ocr_code("ئۇيغۇرچە") == "uig" and s.ocr_code("x") is None
    assert s.ocr_code("Nederlands") == "nld"
    assert s.ocr_code("Polski") == "pol"
    assert s.ocr_code("Українська") == "ukr"
    assert s.ocr_code("Tiếng Việt") == "vie"
    assert s.display_name("اردو") == "اردو · Urdu" and s.display_name("English") == "English"
    assert s.display_name("Nederlands") == "Nederlands · Dutch"
    assert s.display_name("Tiếng Việt") == "Tiếng Việt · Vietnamese"


def test_vlm_prompt_names_the_language_and_it_is_part_of_the_cache_identity():
    from folith.document_understanding import SYSTEM_PROMPT, VLMDocumentProvider, language_hint
    from folith.ocr_online import OnlineOCRConfig

    config = OnlineOCRConfig(provider="openai", base_url="https://example.invalid/v1",
                             model="vlm", api_key="k")
    auto = VLMDocumentProvider(config)
    urdu = VLMDocumentProvider(config, language="Urdu")
    arabic = VLMDocumentProvider(config, language="Arabic")
    assert auto.system_prompt == SYSTEM_PROMPT and "language" not in auto.cache_identity
    assert "written in Urdu" in urdu.system_prompt
    assert "do not normalise it into another language that shares the script" in urdu.system_prompt
    assert urdu.cache_identity != arabic.cache_identity != auto.cache_identity
    assert language_hint("") == ""


def test_smart_profile_reports_the_source_language_in_the_same_call():
    from folith import style_profile

    calls = []

    def fake(*args, **kwargs):
        calls.append(args)
        return json.dumps({"document_profile": {}, "source_language": "维吾尔语",
                           "style_recommendation": {"recommended_style": "literary",
                                                    "confidence": 0.8, "reasons": ["r"]}})

    _profile, rec, warnings = style_profile.quick_profile(
        ["段落 " * 40, "段落 " * 40], "p", "k", "m", call_llm=fake)
    assert rec["source_language"] == "ئۇيغۇرچە" and not warnings and len(calls) == 1
    assert "source_language" in style_profile._style_prompt()

    def odd(*args, **kwargs):
        return json.dumps({"source_language": "外星语",
                           "style_recommendation": {"recommended_style": "general"}})

    _profile, rec, _warnings = style_profile.quick_profile(["段落 " * 40], "p", "k", "m",
                                                           call_llm=odd)
    assert rec["source_language"] == ""


@pytest.fixture()
def data_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("FOLITH_DESKTOP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FOLITH_OUTPUT_DIR", str(data_dir))
    import core

    monkeypatch.setattr(core, "OUTPUT_DIR", data_dir, raising=False)
    yield tmp_path


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _new_task(tmp_path):
    from folith.qt import adapter

    source = tmp_path / "book.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    return adapter.import_documents([source], adapter.EngineConfig(target_lang="简体中文"))[0].job_id


def test_detected_language_fills_in_until_the_user_picks_one(data_env, qt_app):
    from folith.qt import adapter
    from folith.qt.setup_panel import TaskSetupPanel

    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(adapter.EngineConfig(), job_id))
    panel.show()
    qt_app.processEvents()
    try:
        assert panel.source_box.currentData() == "" and panel.payload()["source_lang"] == ""
        panel.set_style_recommendation({"recommendation": {"recommended_style": "general"},
                                        "source_language": "اردو"})
        assert panel.source_box.currentData() == "اردو"
        assert panel.source_hint.isVisible(), "要说明这是识别出来的、可以修改"
        # 用户亲手改成阿拉伯语：之后的识别结果不能把它改回去。
        panel.source_box.setCurrentIndex(panel.source_box.findData("العربية"))
        panel._source_picked_by_user()
        assert not panel.apply_detected_source_language("اردو")
        assert panel.source_box.currentData() == "العربية"
        assert not panel.source_hint.isVisible()
        assert panel.payload()["source_lang"] == "العربية"
    finally:
        panel.close()


def test_the_source_language_is_saved_with_the_task_and_read_back(data_env, qt_app):
    from folith.qt import adapter
    from folith.qt.setup_panel import TaskSetupPanel

    config = adapter.EngineConfig(target_lang="简体中文")
    job_id = _new_task(data_env)
    panel = TaskSetupPanel()
    panel.set_choices(adapter.setup_choices())
    panel.load(adapter.task_setup_view(config, job_id))
    panel.source_box.setCurrentIndex(panel.source_box.findData("ئۇيغۇرچە"))
    adapter.apply_task_setup(config, job_id, panel.payload())
    assert adapter.load_state(job_id)["source_lang"] == "ئۇيغۇرچە"
    reopened = TaskSetupPanel()
    reopened.set_choices(adapter.setup_choices())
    reopened.load(adapter.task_setup_view(config, job_id))
    assert reopened.source_box.currentData() == "ئۇيغۇرچە"
    # 保存过的选择是确认过的：识别结果不覆盖它。
    assert not reopened.apply_detected_source_language("اردو")
    panel.close()
    reopened.close()


def test_entering_preparation_profiles_once_and_only_when_it_can(monkeypatch):
    from folith.qt import window as window_module

    calls = []

    class Fake:
        _auto_profiled: set = set()

        class config:
            @staticmethod
            def connection_problem():
                return ""

        def run_style_profile(self, automatic=False):
            calls.append(automatic)

    fake = Fake()
    fake._auto_profiled = set()
    monkeypatch.setattr(window_module, "_automation", lambda: False)
    window_module.MainWindow._auto_style_profile(fake, "job1")
    window_module.MainWindow._auto_style_profile(fake, "job1")
    assert calls == [True], "同一个任务只自动做一次"
    monkeypatch.setattr(fake.config, "connection_problem", staticmethod(lambda: "未配置"))
    window_module.MainWindow._auto_style_profile(fake, "job2")
    assert calls == [True], "模型没配置好时不自动调用"
    monkeypatch.setattr(window_module, "_automation", lambda: True)
    monkeypatch.setattr(fake.config, "connection_problem", staticmethod(lambda: ""))
    window_module.MainWindow._auto_style_profile(fake, "job3")
    assert calls == [True], "脚本化运行不自动调用模型"
