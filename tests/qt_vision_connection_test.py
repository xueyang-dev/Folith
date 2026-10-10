"""视觉理解连接：扫描件默认走 VLM，它用的模型必须能在 Qt 里单独配置。

产品已转向 VLM 结构化视觉理解（``ocr_backend == "online"``），OCR 只是可选的本机
定位插件。视觉连接优先用"专用连接"，没有才沿用翻译连接；翻译模型不支持图像时，
扫描件抽取会明确失败。这里钉住：

* 默认（没保存过）= 沿用翻译连接，界面不替用户写任何文件；
* 单独配置 → 文件落盘（仅当前用户可读）、密钥不会出现在视图里、留空密钥沿用旧值；
* 切回"沿用翻译模型"会真的删掉专用连接（连同密钥）；
* 配置不完整时不保存，并说清楚缺什么；
* 设置对话框：只有改过才写盘，选了"单独配置"却没配完整时不关闭对话框。
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import adapter, style  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_output(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


def _provider():
    return adapter.vision_providers()[0]


def test_default_is_the_translation_connection_and_writes_nothing(isolated_output):
    view = adapter.vision_connection_view()
    assert view["dedicated"] is False
    assert view["has_key"] is False
    assert not core.ocr_online_config_path().exists()
    assert _provider() in view["providers"]


def test_saving_a_dedicated_connection_persists_privately(isolated_output):
    view = adapter.save_vision_connection(
        provider=_provider(), model="vision-model-x", api_key="sk-secret-value",
        max_tokens=16384, reasoning_mode="provider_default")
    path = core.ocr_online_config_path()
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert view["dedicated"] and view["model"] == "vision-model-x"
    assert view["has_key"] is True
    assert "sk-secret-value" not in json.dumps(view), "密钥不能出现在视图里"
    assert json.loads(path.read_text(encoding="utf-8"))["model"] == "vision-model-x"


def test_blank_key_keeps_the_saved_one_and_other_fields_update(isolated_output):
    adapter.save_vision_connection(provider=_provider(), model="m1", api_key="sk-keep")
    adapter.save_vision_connection(provider=_provider(), model="m2", api_key="")
    saved = json.loads(core.ocr_online_config_path().read_text(encoding="utf-8"))
    assert saved["model"] == "m2"
    assert saved["api_key"] == "sk-keep"


def test_incomplete_connection_is_refused_with_a_reason(isolated_output):
    with pytest.raises(adapter.AdapterError, match="服务商"):
        adapter.save_vision_connection(provider="", model="m", api_key="k")
    with pytest.raises(adapter.AdapterError, match="视觉模型"):
        adapter.save_vision_connection(provider=_provider(), model="", api_key="k")
    with pytest.raises(adapter.AdapterError, match="API Key"):
        adapter.save_vision_connection(provider=_provider(), model="m", api_key="")
    assert not core.ocr_online_config_path().exists(), "拒绝时不能留下半截文件"


def test_token_limit_is_clamped_to_what_the_core_accepts(isolated_output):
    view = adapter.save_vision_connection(
        provider=_provider(), model="m", api_key="k", max_tokens=10 ** 9)
    assert view["max_tokens"] == view["max_tokens_limit"]


def test_going_back_to_the_translation_connection_removes_the_secret(isolated_output):
    adapter.save_vision_connection(provider=_provider(), model="m", api_key="sk-gone")
    view = adapter.clear_vision_connection()
    assert view["dedicated"] is False and view["has_key"] is False
    assert not core.ocr_online_config_path().exists()
    adapter.clear_vision_connection()      # 重复清除不报错


def test_status_message_speaks_of_vision_not_ocr(isolated_output):
    status = adapter.vision_connection_view()["status"]
    assert "线上 OCR" not in status["message"]


# ---------------------------------------------------------------- 对话框 ------

from folith.qt.dialogs import EngineDialog  # noqa: E402
from folith.qt.workers import TaskRunner  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication([])
    style.set_current_theme("light")
    style.apply_theme(instance, "light")
    return instance


@pytest.fixture()
def dialog(app):
    dlg = EngineDialog(adapter.EngineConfig(target_lang="简体中文"), TaskRunner())
    dlg.show()
    for _ in range(3):
        app.processEvents()
    yield dlg
    dlg.close()


def test_dialog_hides_the_dedicated_fields_by_default(dialog):
    assert dialog.vision_same_radio.isChecked()
    assert dialog.vision_fields.isHidden()
    assert not dialog._vision_changed()


def test_dialog_shows_fields_when_choosing_a_dedicated_model(dialog):
    dialog.vision_separate_radio.setChecked(True)
    assert not dialog.vision_fields.isHidden()
    assert dialog._vision_changed()
    assert dialog._vision_problem(), "没配完整就不能保存"


def test_dialog_save_writes_the_vision_connection_only_when_it_changed(dialog, isolated_output):
    dialog._apply_vision()
    assert not core.ocr_online_config_path().exists(), "没改过不应写文件"
    dialog.vision_separate_radio.setChecked(True)
    dialog.vision_provider_box.setCurrentText(_provider())
    dialog.vision_model_edit.setText("vision-x")
    dialog.vision_key_edit.setText("sk-dialog")
    assert dialog._vision_problem() == ""
    dialog._apply_vision()
    assert json.loads(core.ocr_online_config_path().read_text(encoding="utf-8"))["model"] == "vision-x"


def test_dialog_switching_back_clears_the_saved_connection(dialog, isolated_output):
    adapter.save_vision_connection(provider=_provider(), model="m", api_key="k")
    dialog._vision_view = adapter.vision_connection_view()
    dialog.vision_separate_radio.setChecked(False)
    dialog.vision_same_radio.setChecked(True)
    assert dialog._vision_changed()
    dialog._apply_vision()
    assert not core.ocr_online_config_path().exists()


def test_dialog_refuses_to_save_an_incomplete_dedicated_connection(dialog, isolated_output):
    dialog.vision_separate_radio.setChecked(True)
    dialog._save()
    assert dialog.result() == 0, "对话框不能在配置不完整时被接受"
    assert not dialog._status.isHidden() and dialog._status.text()
    assert not core.ocr_online_config_path().exists()
