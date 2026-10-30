"""A translation-only or OCR-only model cannot start a task (measured: garbage output)."""
from __future__ import annotations

import pytest

import core
from folith.model_capability import task_model_problem
from folith.qt import adapter


def test_translation_only_and_ocr_only_models_cannot_translate():
    assert "专用翻译模型" in task_model_problem("translation", "tencent/Hunyuan-MT-7B")
    assert "OCR 专用模型" in task_model_problem("translation", "PaddlePaddle/PaddleOCR-VL-1.5")
    assert "专用翻译模型" in task_model_problem("reviewer", "tencent/Hunyuan-MT-7B")
    assert task_model_problem("translation", "deepseek-flash") == ""
    assert task_model_problem("translation", "Qwen/Qwen2.5-VL-72B-Instruct") == ""


def test_desktop_refuses_to_start_but_the_connection_itself_is_complete():
    cfg = adapter.EngineConfig(provider="DeepSeek", model="tencent/Hunyuan-MT-7B", api_key="k")
    assert cfg.connection_problem() == ""          # settings, onboarding: still configured
    assert "Hunyuan-MT-7B" in cfg.start_problem()  # starting a task: refused with the reason
    general = adapter.EngineConfig(provider="DeepSeek", model="deepseek-flash", api_key="k")
    assert general.start_problem() == ""


def test_pipeline_refuses_before_doing_any_work(monkeypatch):
    monkeypatch.setattr(core, "_ensure_output_dir",
                        lambda: pytest.fail("pipeline started work with an unfit model"))
    with pytest.raises(ValueError, match="专用翻译模型"):
        core._run_job_pipeline_impl(
            "job", "book.pdf", b"", provider="SiliconFlow", api_key="k",
            model="tencent/Hunyuan-MT-7B", target_lang="简体中文", auto_term=False,
            enable_report=False, translation_theory="")
