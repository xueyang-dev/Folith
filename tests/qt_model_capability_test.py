"""#258：把 OCR 专用 / 专用翻译模型指派给「视觉」「翻译」角色时给出的警告。

钉住的事实：

* 名称匹配保守：已知的 OCR / 机器翻译家族被认出来，未知与通用模型不被误报；
* 视觉角色选中 OCR 专用模型时给出「结构化格式」警告；通用 VLM 不警告；
* 翻译角色选中专用翻译模型时给出「专用翻译模型」警告；换回通用模型即消失；
* 运行时的失败说明指向「不按结构化格式回答、请换通用视觉语言模型」，
  而不是只让用户提高输出上限。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

from folith import ocr_online  # noqa: E402
from folith.model_capability import classify_model  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.qt.dialogs import EngineDialog  # noqa: E402


class _SyncRunner:
    """立即执行，测试里不需要线程。"""

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - adapter swallows its own
            if on_error:
                on_error(str(exc), "")
            return None
        if on_done:
            on_done(result)
        return None


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


PROFILE = {
    "id": "c1", "name": "Relay", "provider": "DeepSeek", "model": "gpt-5",
    "models": ["gpt-5", "PaddlePaddle/PaddleOCR-VL-1.5", "tencent/Hunyuan-MT-7B"],
    "api_key": "k", "base_url": "",
}


def _dialog(app, roles, models):
    config = adapter.EngineConfig(
        provider="DeepSeek", model="gpt-5", api_key="k",
        connections=[dict(PROFILE)], connection_roles=roles, connection_models=models)
    return EngineDialog(config, _SyncRunner())


# ---- 名称匹配 -----------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "PaddlePaddle/PaddleOCR-VL-1.5", "PaddleOCR-VL", "GOT-OCR", "stepfun-ai/GOT-OCR2_0",
    "olmOCR-7B", "my-OCR-model", "OCR-model",
])
def test_ocr_only_family_is_recognised(name):
    assert classify_model(name) == "ocr_only"


@pytest.mark.parametrize("name", [
    "tencent/Hunyuan-MT-7B", "Hunyuan-MT", "Helsinki-NLP/opus-mt-en-zh",
    "facebook/m2m100_1.2B", "facebook/seamless-m4t", "foo/bar-MT-7B",
])
def test_translation_only_family_is_recognised(name):
    assert classify_model(name) == "translation_only"


@pytest.mark.parametrize("name", [
    "qwen2.5-vl", "Qwen/Qwen2.5-VL-72B-Instruct", "Qwen3-VL", "dots3-note-prev",
    "deepseek-v4.1-flash", "deepseek-flash", "gpt-5", "glm-4.5v", "",
])
def test_unknown_and_general_models_are_not_flagged(name):
    assert classify_model(name) == "general"


# ---- 视觉角色 -----------------------------------------------------------------

def test_vision_role_warns_for_ocr_only_model(app):
    dialog = _dialog(app, {"translation": "c1", "vision": "c1"},
                     {"vision": "PaddlePaddle/PaddleOCR-VL-1.5"})
    hint = dialog.connection_library.vision_modality_hint
    assert not hint.isHidden()
    assert "结构化格式" in hint.text()
    assert hint.tone() == "attention"


def test_vision_role_general_vlm_shows_no_capability_warning(app):
    dialog = _dialog(app, {"translation": "c1", "vision": "c1"},
                     {"vision": "gpt-5"})
    hint = dialog.connection_library.vision_modality_hint
    # 没有能力警告：显示的是中性的输入类型提示，而不是「结构化格式」警告。
    assert "结构化格式" not in hint.text()
    assert hint.tone() != "attention" or "仅接受文本" in hint.text()


# ---- 翻译角色 -----------------------------------------------------------------

def test_translation_role_warns_for_translation_only_model(app):
    dialog = _dialog(app, {"translation": "c1"},
                     {"translation": "tencent/Hunyuan-MT-7B"})
    warning = dialog.connection_library.capability_warnings["translation"]
    assert not warning.isHidden()
    assert "专用翻译模型" in warning.text()
    assert warning.tone() == "attention"


def test_translation_role_switching_to_general_clears_warning(app):
    dialog = _dialog(app, {"translation": "c1"},
                     {"translation": "tencent/Hunyuan-MT-7B"})
    library = dialog.connection_library
    warning = library.capability_warnings["translation"]
    assert not warning.isHidden()
    library.model_selectors["translation"].setCurrentText("gpt-5")
    assert warning.isHidden()


def test_general_models_produce_no_role_warnings(app):
    dialog = _dialog(app, {"translation": "c1", "reviewer": "c1", "vision": "c1"},
                     {"translation": "gpt-5", "reviewer": "gpt-5", "vision": "gpt-5"})
    library = dialog.connection_library
    assert library.capability_warnings["translation"].isHidden()
    assert library.capability_warnings["reviewer"].isHidden()
    assert "结构化格式" not in library.vision_modality_hint.text()


# ---- 运行时说明 ---------------------------------------------------------------

def test_plain_text_truncation_points_at_structured_output_not_output_limit():
    message = ocr_online._truncation_message("11\n序章 中国史の時代\nはじめに\n一、日本におけ")
    assert "结构化格式" in message or "纯文本" in message
    assert "通用视觉语言模型" in message
    assert "提高输出上限或改用其他来源" not in message


def test_json_looking_truncation_keeps_the_output_limit_advice():
    message = ocr_online._truncation_message('{"b":[{"t":"paragraph","s":"x"')
    assert "提高输出上限" in message


def test_invalid_semantic_json_explains_the_structured_format():
    message = ocr_online._parse_failure_message("invalid_semantic_json")
    assert "结构化格式" in message
    assert "通用视觉语言模型" in message


def test_failed_page_reason_names_the_structured_format_problem():
    from folith.document_understanding import PageFailed

    wrong_format = PageFailed(
        0, "p1", "invalid_semantic_json", None,
        "Visual understanding failed for p1: invalid_semantic_json: ...")
    assert "结构化格式" in wrong_format.describe()
    plain = PageFailed(
        0, "p1", "truncated", None,
        "truncated: 视觉理解返回的是纯文本转写，不是约定的结构化格式...")
    assert "纯文本" in plain.describe()
