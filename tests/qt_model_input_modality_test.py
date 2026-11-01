"""#48（Qt 层）：模型输入类型在连接设置里的呈现。

钉住的事实：

* 「获取可用模型」拉到的输入模态标注在目录每个模型后面；没声明的显式「未知」，
  悬停说明来源与实测入口；
* 列表显示带标注，但选中回写 ``model_box`` 的永远是纯模型名；
* 「测试视觉」只在点按钮时发一次请求，结果按 连接+模型 缓存（改配置即作废），
  并即时刷新列表与「视觉」用途的提示；
* 视觉用途选中「已知仅文本」模型时出警告；未知保持中性提示，不装懂。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")

import core  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from folith.qt import adapter, connection_status, preferences  # noqa: E402
from folith.qt.dialogs import EngineDialog  # noqa: E402

FECTHED_MODELS = ["vision-x", "plain-y", "mystery-z"]
FETCHED_MODALITIES = {"vision-x": ["text", "image"], "plain-y": ["text"]}


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


class _Store:
    """QSettings 的最小替身：探测缓存的读写不需要真的偏好存储。"""

    def __init__(self):
        self.rows = {}

    def value(self, key, default=None):
        return self.rows.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - QSettings 命名
        self.rows[key] = value


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def fetch(monkeypatch):
    def fake(provider, key, base_url=None):
        return True, list(FECTHED_MODELS), f"已获取 {len(FECTHED_MODELS)} 个可用模型", \
            {m: list(v) for m, v in FETCHED_MODALITIES.items()}

    monkeypatch.setattr(core, "fetch_provider_models", fake)


def _dialog(app, **config):
    """打开设置并进入编辑区：没有连接时 config 会被提升成一条，先点「编辑」。"""
    dialog = EngineDialog(adapter.EngineConfig(target_lang="简体中文", **config), _SyncRunner())
    active = dialog.connection_library.roles.get("translation")
    if active:
        dialog.connection_library.edit(active)
    app.processEvents()
    return dialog


def _items(library):
    return [library.model_list.item(i) for i in range(library.model_list.count())]


# ---- 探测缓存：按 连接+模型 存，配置一改变即作废 --------------------------------


def test_vision_probe_cache_roundtrip_and_invalidation():
    store = _Store()
    connection_status.record_vision_probe(
        "DeepSeek", "m1", "https://x.example/v1", "k", True, "支持图像输入",
        settings=store, now=datetime(2026, 10, 25, 8, 0, tzinfo=timezone.utc))
    hit = connection_status.vision_probe_result(
        "DeepSeek", "m1", "https://x.example/v1", "k", settings=store)
    assert hit and hit["ok"] is True and hit["message"] == "支持图像输入"
    assert hit["at"].startswith("2026-10-25")
    # 任一配置维度变化都意味着旧观测不再属于这条连接：全部回到未知。
    assert connection_status.vision_probe_result(
        "DeepSeek", "m2", "https://x.example/v1", "k", settings=store) is None
    assert connection_status.vision_probe_result(
        "DeepSeek", "m1", "https://y.example/v1", "k", settings=store) is None
    assert connection_status.vision_probe_result(
        "DeepSeek", "m1", "https://x.example/v1", "k2", settings=store) is None
    assert connection_status.vision_probe_result(
        "OpenAI", "m1", "https://x.example/v1", "k", settings=store) is None


# ---- 编辑区目录：标注 + 显式未知 + 选中回写纯模型名 --------------------------------


def test_catalog_labels_declared_and_unknown_states(app, fetch):
    dialog = _dialog(app, provider="DeepSeek", model="vision-x", api_key="k")
    dialog.fetch_button.click()
    library = dialog.connection_library
    texts = [item.text() for item in _items(library)]
    assert texts == ["vision-x · 文本 + 视觉", "plain-y · 文本", "mystery-z · 未知"]
    # model_box 的条目永远是纯名字：显示标注不污染数据模型。
    assert [dialog.model_box.itemText(i) for i in range(dialog.model_box.count())] \
        == FECTHED_MODELS
    tooltips = [item.toolTip() for item in _items(library)]
    assert "服务商模型目录声明" in tooltips[0]
    assert "未知" in tooltips[2] and "测试视觉" in tooltips[2]
    # 选中回写用 UserRole 里的纯模型名。
    library.model_list.setCurrentRow(1)
    assert dialog.model_box.currentText() == "plain-y"
    assert _items(library)[1].data(Qt.ItemDataRole.UserRole) == "plain-y"


def test_provider_switch_drops_stale_modality_labels(app, fetch, monkeypatch):
    catalog = {}

    def fake(provider, key, base_url=None):
        return True, ["other-m"], "已获取 1 个可用模型", {}

    monkeypatch.setattr(core, "fetch_provider_models", fake)
    dialog = _dialog(app, provider="DeepSeek", model="vision-x", api_key="k")
    dialog.fetch_button.click()
    other = next(p for p in dialog.config.providers if p != "DeepSeek" and p != "Gemini")
    dialog.provider_box.setCurrentText(other)
    texts = [item.text() for item in _items(dialog.connection_library)]
    assert texts and all(text.endswith("· 未知") for text in texts), \
        "换提供商后不能沿用上一家目录的输入模态标注"


def test_fetched_modalities_travel_into_the_saved_profile(app, fetch):
    dialog = _dialog(app, provider="DeepSeek", model="vision-x", api_key="k")
    dialog.fetch_button.click()
    profile = dialog.connection_library._editor_profile(quiet=True)
    assert profile["model_modalities"] == {
        "vision-x": ["text", "image"], "plain-y": ["text"]}, "声明随连接保存；未知不编造"
    assert set(profile["model_modalities"]) <= set(profile["models"])


# ---- 「测试视觉」：用户触发、结果缓存、界面即时刷新 -------------------------------


def test_probe_button_updates_list_and_caches_result(app, fetch, monkeypatch, tmp_path):
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path))
    calls = []

    def fake_probe(provider, key, model, base_url=""):
        calls.append((provider, key, model, base_url))
        return True, "支持图像输入：模型认出了图里的颜色（回复「红色」）· 耗时 0.1s"

    monkeypatch.setattr(adapter, "test_vision_model", fake_probe)
    dialog = _dialog(app, provider="DeepSeek", model="mystery-z", api_key="k")
    dialog.fetch_button.click()
    library = dialog.connection_library
    library.model_list.setCurrentRow(2)          # mystery-z · 未知
    library.vision_probe_button.click()
    assert calls == [("DeepSeek", "k", "mystery-z", "")], "探测必须用编辑区当前的连接配置"
    item = library.model_list.currentItem()
    assert item.text() == "mystery-z · 文本 + 视觉"
    assert "实测" in item.toolTip() and "默认模型" in item.toolTip()
    assert "支持图像输入" in dialog.test_label.text()
    # 结果按 连接+模型 落在客户端偏好里，重开编辑区仍然读得到。
    cached = connection_status.vision_probe_result("DeepSeek", "mystery-z", "", "k")
    assert cached and cached["ok"] is True
    # 换个模型选中，探测按钮仍可点（每个模型各测各的）。
    assert library.vision_probe_button.isEnabled()


def test_probe_failure_keeps_unknown_out_of_the_way(app, fetch, monkeypatch, tmp_path):
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path))

    def fake_probe(provider, key, model, base_url=""):
        return False, "模型回复了但没认出图里的颜色（「我看不到图片」），图像输入未验证通过。"

    monkeypatch.setattr(adapter, "test_vision_model", fake_probe)
    dialog = _dialog(app, provider="DeepSeek", model="mystery-z", api_key="k")
    dialog.fetch_button.click()
    library = dialog.connection_library
    library.model_list.setCurrentRow(2)
    library.vision_probe_button.click()
    # 未通过 ≠ 已知仅文本：显示成「文本」会冒充服务商结论，失败信息留在状态行和缓存里。
    assert "未验证通过" in dialog.test_label.text()
    cached = connection_status.vision_probe_result("DeepSeek", "mystery-z", "", "k")
    assert cached and cached["ok"] is False


# ---- 「视觉」用途：已知仅文本要警告，未知保持中性 ----------------------------------

PROFILE = {"id": "c1", "name": "Relay", "provider": "DeepSeek", "model": "plain-y",
           "models": ["plain-y", "vision-x", "mystery-z"], "api_key": "k", "base_url": "",
           "model_modalities": {"plain-y": ["text"], "vision-x": ["text", "image"]}}


def _vision_dialog(app, **overrides):
    config = adapter.EngineConfig(
        provider="DeepSeek", model="plain-y", api_key="k",
        connections=[dict(PROFILE, **overrides.get("profile", {}))],
        connection_roles={"translation": "c1", "vision": "c1"},
        connection_models={"vision": overrides.get("vision_model", "plain-y")})
    return EngineDialog(config, _SyncRunner())


def test_vision_role_warns_for_declared_text_only(app):
    dialog = _vision_dialog(app)
    hint = dialog.connection_library.vision_modality_hint
    assert not hint.isHidden()
    # fullText() 返回完整语义文本（ElideLabel.text() 可能因已知宽度而省略显示）
    assert "仅接受文本输入" in hint.label.fullText()
    assert hint.tone() == "attention"


def test_vision_role_model_switch_updates_hint(app):
    dialog = _vision_dialog(app)
    hint = dialog.connection_library.vision_modality_hint
    dialog.connection_library.model_selectors["vision"].setCurrentText("vision-x")
    assert "支持图像输入" in hint.label.fullText() and hint.tone() == "done"
    dialog.connection_library.model_selectors["vision"].setCurrentText("mystery-z")
    assert "未知" in hint.label.fullText() and hint.tone() == "idle"


def test_vision_role_hint_reads_probe_cache(app, monkeypatch, tmp_path):
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(tmp_path))
    connection_status.record_vision_probe("DeepSeek", "mystery-z", "", "k",
                                           False, "实测失败信息")
    dialog = _vision_dialog(app, vision_model="mystery-z",
                            profile={"model_modalities": {}})
    hint = dialog.connection_library.vision_modality_hint
    assert "实测未通过" in hint.label.fullText() and hint.tone() == "attention"
    assert hint.toolTip() == "实测失败信息", "失败原因放悬停，不挤版面"


def test_vision_role_hint_hidden_without_selection(app):
    config = adapter.EngineConfig(provider="DeepSeek", model="plain-y", api_key="k",
                                  connections=[dict(PROFILE)],
                                  connection_roles={"translation": "c1"})
    dialog = EngineDialog(config, _SyncRunner())
    assert dialog.connection_library.vision_modality_hint.isHidden()
