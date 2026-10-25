"""设置里「获取可用模型」：拉到的列表要替换预设，但不能弄丢用户已有的连接。

接替 Streamlit 的 ``provider_models_ui_test``。钉住：

* 用对话框里**当前**填的提供商 / 密钥 / 地址去拉（改了密钥就用新密钥）；
* 成功：列表替换为拉到的模型，已选模型保留；
* 失败：列表和已选模型都不动，并给出错误；
* 拉到的模型和自定义地址会进入保存的配置；
* 单独配置的审校员凭据不受翻译模型这边拉取的影响。
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

import core  # noqa: E402
from folith.qt import adapter  # noqa: E402
from folith.qt.dialogs import EngineDialog  # noqa: E402


class _SyncRunner:
    """立即执行，测试里不需要线程。"""

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - adapter 自己吞异常
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


@pytest.fixture()
def fetch(monkeypatch):
    calls: list[tuple] = []
    state = {"fail": False, "models": ["deepseek-v4.1-flash", "glm-5.3"]}

    def fake(provider, key, base_url=None):
        calls.append((provider, key, base_url))
        if state["fail"]:
            return False, [], "模型目录暂时不可用", {}
        return True, list(state["models"]), f"已获取 {len(state['models'])} 个可用模型", {}

    monkeypatch.setattr(core, "fetch_provider_models", fake)
    return calls, state


def _dialog(app, **config):
    """打开设置。已保存的翻译连接要先点「编辑」才进编辑区——和用户的操作一样。"""
    dialog = EngineDialog(adapter.EngineConfig(target_lang="简体中文", **config), _SyncRunner())
    active = dialog.connection_library.roles.get("translation")
    if active:
        dialog.connection_library.edit(active)
    app.processEvents()
    return dialog


def _models(dialog):
    return [dialog.model_box.itemText(i) for i in range(dialog.model_box.count())]


def test_fetch_uses_the_current_fields_and_replaces_the_list(app, fetch):
    calls, _ = fetch
    dialog = _dialog(app, provider="DeepSeek", model="deepseek-v4.1-flash",
                     api_key="old-key", base_url="https://regional.example.com/v1")
    dialog.key_edit.setText("another-account")
    dialog.fetch_button.click()
    assert calls[-1] == ("DeepSeek", "another-account", "https://regional.example.com/v1")
    assert _models(dialog) == ["deepseek-v4.1-flash", "glm-5.3"]
    assert dialog.model_box.currentText() == "deepseek-v4.1-flash", "已选模型不能被列表刷新冲掉"
    assert dialog.fetch_button.isEnabled()


def test_failed_fetch_keeps_list_and_selection(app, fetch):
    _, state = fetch
    dialog = _dialog(app, provider="DeepSeek", api_key="k")
    dialog.fetch_button.click()
    dialog.model_box.setCurrentText("glm-5.3")
    state["fail"] = True
    dialog.fetch_button.click()
    assert _models(dialog) == ["deepseek-v4.1-flash", "glm-5.3"]
    assert dialog.model_box.currentText() == "glm-5.3"
    assert "不可用" in dialog.test_label.text()
    assert dialog.fetch_button.isEnabled(), "失败后按钮必须可再点"


def test_discovered_model_and_endpoint_reach_the_saved_config(app, fetch):
    _, state = fetch
    state["models"] = ["new-text-model", "another-model"]
    dialog = _dialog(app, provider="DeepSeek", api_key="k",
                     base_url="https://regional.example.com/v1")
    dialog.fetch_button.click()
    dialog.model_box.setCurrentText("new-text-model")
    saved = dialog._collect()
    assert saved.model == "new-text-model"
    assert saved.base_url == "https://regional.example.com/v1"
    # 重开对话框（相当于重启后读回配置）：发现的模型仍是当前选择。
    again = _dialog(app, provider="DeepSeek", model=saved.model, api_key="k",
                    base_url=saved.base_url)
    assert again.model_box.currentText() == "new-text-model"
    assert again.base_edit.text() == "https://regional.example.com/v1"


def test_separate_reviewer_credentials_survive_a_fetch(app, fetch):
    dialog = _dialog(app, provider="DeepSeek", api_key="main-key",
                     reviewer_mode="separate", reviewer_provider="Gemini",
                     reviewer_model="gemini-review-model", reviewer_api_key="review-key")
    dialog.fetch_button.click()
    saved = dialog._collect()
    assert saved.api_key == "main-key"
    assert (saved.reviewer_mode, saved.reviewer_provider, saved.reviewer_model,
            saved.reviewer_api_key) == ("separate", "Gemini", "gemini-review-model", "review-key")


def test_switching_provider_resets_the_model_list(app, fetch):
    dialog = _dialog(app, provider="DeepSeek", api_key="k")
    dialog.fetch_button.click()
    assert "glm-5.3" in _models(dialog)
    other = next(p for p in dialog.config.providers if p != "DeepSeek")
    dialog.provider_box.setCurrentText(other)
    assert _models(dialog) == list(dialog.config.models_for(other)), \
        "换提供商后不能沿用上一家拉到的列表"
