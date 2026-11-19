"""Qt 连接编辑器：思考开关（reasoning_mode）控件。

钉住的事实：

* 自定义中转站 + dots3-note-prev 模型 → thinking_row 可见，选项「服务默认」和「关闭思考」；
* DeepSeek deepseek-flash → 选项包含「关闭思考（DeepSeek 系）」；
* 完全未知的模型 → thinking_row 隐藏；
* 选「关闭思考」后保存，读回连接的 reasoning_mode 正确；
  重新打开编辑区，combo 自动选回已保存的档位；
  connection_thinking_mode() 按已保存档位返回；
* 完全未知的模型保存后，profile 里不含 reasoning_mode 键；
  重新编辑一条模型已失去支持的连接时，控件回到「服务默认」，
  但 _thinking_mode 记住了原来的值以便原样写回。
"""
from __future__ import annotations

import json
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
from folith.engine import llm_provider  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SyncRunner:
    """立即执行，测试里不需要线程。"""

    def submit(self, fn, *args, on_done=None, on_error=None, label="", **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(str(exc), "")
            return None
        if on_done:
            on_done(result)
        return None


def _make_profile(pid="c1", provider="自定义中转站", model="dots3-note-prev",
                  base_url="http://localhost:8080/v1", **extra):
    """一条最小的连接 profile，默认用自定义中转站 + dots3 模型。"""
    return {"id": pid, "name": "Test Connection", "provider": provider,
            "provider_id": pid if provider == "自定义中转站" else "",
            "custom": provider == "自定义中转站",
            "model": model, "models": [model], "api_key": "sk-test",
            "base_url": base_url, "protocol": "openai_compat",
            "model_modalities": {}, **extra}


def _dialog(app, profiles=None, roles=None):
    if profiles is None:
        profiles = []
    if roles is None:
        roles = {"translation": profiles[0]["id"]} if profiles else {}
    config = adapter.EngineConfig(
        target_lang="简体中文",
        connections=profiles,
        connection_roles=roles,
    )
    dialog = EngineDialog(config, _SyncRunner())
    app.processEvents()
    return dialog


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. 控件可见性与选项内容
# ---------------------------------------------------------------------------

def test_custom_relay_dots3_model_offers_a_thinking_level(app):
    """#540 PR-2：dots3 的「用途 › 思考强度」列含服务默认与 Off（vLLM 开关）。"""
    from folith.engine import effort_settings
    assert effort_settings.choices("自定义中转站", "dots3-note-prev") == [
        ("service_default", "服务默认"), ("off", "Off"), ("on", "On")]
    assert effort_settings.to_job_fields({"translation": "off"}, "translation",
                                         "自定义中转站", "dots3-note-prev") == (
        "", "thinking_disabled_vllm")


def test_deepseek_flash_maps_off_to_the_deepseek_switch(app):
    """#540 PR-2：DeepSeek 的 Off 档走 DeepSeek 自己的关闭开关。"""
    from folith.engine import effort_settings
    assert ("off", "Off") in effort_settings.choices("DeepSeek", "deepseek-flash")
    assert effort_settings.to_job_fields({"translation": "off"}, "translation",
                                         "DeepSeek", "deepseek-flash") == (
        "", "thinking_disabled")


def test_unknown_model_hides_thinking_row(app):
    """完全未知的模型 → thinking_row 隐藏。"""
    profile = _make_profile(pid="c3", provider="OpenAI", model="gpt-4-turbo",
                            custom=False, provider_id="", base_url="")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c3")
    app.processEvents()

    assert dialog.thinking_row.isHidden(), "未知模型不应显示思考行"
    assert dialog.thinking_box.count() <= 1, "未知模型思考 combo 不应有实质选项"


# ---------------------------------------------------------------------------
# 2. 保存、重新编辑、connection_thinking_mode
# ---------------------------------------------------------------------------

def test_save_thinking_disabled_writes_reasoning_mode(app, isolated):
    """选「关闭思考」后保存，连接里 reasoning_mode 正确写入。"""
    profile = _make_profile(provider="自定义中转站", model="dots3-note-prev",
                            base_url="http://relay.local/v1")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c1")
    app.processEvents()

    box = dialog.thinking_box
    idx = box.findData("thinking_disabled_vllm")
    assert idx >= 0
    box.setCurrentIndex(idx)

    # 不经网络测试：直接调 save()
    assert lib.save(), "保存应成功"
    app.processEvents()

    saved_profiles = lib.profiles
    p = next(p for p in saved_profiles if p["id"] == "c1")
    assert p.get("reasoning_mode") == "thinking_disabled_vllm"


def test_re_edit_restores_saved_thinking_mode(app, isolated):
    """保存后重新打开编辑区，combo 应自动选回已保存的档位。"""
    profile = _make_profile(reasoning_mode="thinking_disabled_vllm",
                            provider="自定义中转站", model="dots3-note-prev",
                            base_url="http://relay.local/v1")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c1")
    app.processEvents()

    box = dialog.thinking_box
    assert box.currentData() == "thinking_disabled_vllm", (
        f"应选回已保存档位，实际：{box.currentData()!r}")


def test_connection_thinking_mode_reflects_saved_profile(app, isolated):
    """connection_thinking_mode() 按翻译连接中已保存的 reasoning_mode 返回。"""
    profile = _make_profile(provider="自定义中转站", model="dots3-note-prev",
                            base_url="http://relay.local/v1",
                            reasoning_mode="thinking_disabled_vllm")
    # 保存到真正的 llm-config.json（指向 tmp_path）
    core.save_provider_config(
        provider="", model="",
        api_key="",
        connections=[profile],
        connection_roles={"translation": "c1"},
    )
    mode = llm_provider.connection_thinking_mode()
    assert mode == "thinking_disabled_vllm"


# ---------------------------------------------------------------------------
# 3. 未知模型：不写 reasoning_mode；已失去支持时保留原值
# ---------------------------------------------------------------------------

def test_unknown_model_saves_no_reasoning_mode_key(app, isolated):
    """未知模型保存后，profile 里不含 reasoning_mode 键。"""
    profile = _make_profile(pid="c3", provider="OpenAI", model="gpt-4-turbo",
                            custom=False, provider_id="", base_url="")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c3")
    app.processEvents()

    assert lib.save(), "保存应成功"
    app.processEvents()

    p = next(p for p in lib.profiles if p["id"] == "c3")
    assert "reasoning_mode" not in p, (
        "未知模型不应将 reasoning_mode 写入连接（会覆盖 FOLITH_REASONING_MODE 出口）")


def test_lost_model_support_keeps_stored_value(app, isolated):
    """编辑一条模型已失去支持的连接：thinking_row 隐藏但 _thinking_mode 保留原值。"""
    # 用一个不再出现在 _THINKING_CONTROL_MODELS 里的伪模型
    profile = _make_profile(pid="c4", provider="OpenAI", model="gpt-3.5-turbo",
                            custom=False, provider_id="", base_url="",
                            reasoning_mode="thinking_disabled_vllm")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c4")
    app.processEvents()

    assert dialog.thinking_row.isHidden(), "失去支持的模型不应显示思考行"
    assert lib._thinking_mode == "thinking_disabled_vllm", (
        "_thinking_mode 应记住已保存的值以便原样写回")
    # _editor_profile 应把 _thinking_mode 原样写回
    p = lib._editor_profile(quiet=True)
    # 由于 thinking_box.count() <= 1，取 _thinking_mode；但值非空，
    # 所以不会被 del，而是原样留在 profile 里
    # 注意：因为模型不支持，thinking_box 只有 0 或 1 个条目，
    # 所以 _editor_profile 用 self._thinking_mode；
    # 但 _thinking_mode 非空且模型不支持，最终 del 逻辑：
    # del p["reasoning_mode"] when not p["reasoning_mode"]
    # 这里 _thinking_mode = "thinking_disabled_vllm" 非空，所以 NOT 删除
    # 实际语义：模型已不支持，但用户没有主动清空，所以保留原值
    assert p is not None
    # 根据逻辑：thinking_box.count() <= 1 → 用 _thinking_mode（非空）→ 不删除
    assert p.get("reasoning_mode") == "thinking_disabled_vllm"


# ---------------------------------------------------------------------------
# 4. 作用范围提示（#220 A3）：开关影响哪些角色要写在界面上
# ---------------------------------------------------------------------------

def test_thinking_levels_are_per_role_not_a_connection_switch(app, isolated):
    """#540 PR-2：三行各有档位下拉；专用视觉连接不改变「作用范围」提示（已移除）。"""
    profile = _make_profile(provider="自定义中转站", model="dots3-note-prev",
                            base_url="http://relay.local/v1")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    app.processEvents()
    assert set(lib.level_selectors) == {"translation", "reviewer", "vision"}
    assert dialog.thinking_hint.isHidden(), "连接编辑器不再展示作用范围提示"
    # 视觉沿用翻译但可单独改
    from folith.engine import effort_settings
    levels = {"translation": "low"}
    assert effort_settings.effective_level(levels, "vision") == "low"
    levels["vision"] = "off"
    assert effort_settings.effective_level(levels, "vision") == "off"
    dialog.close()


def test_thinking_hint_hides_with_unknown_model(app):
    """未知模型：思考行隐藏，作用范围提示也一起隐藏。"""
    profile = _make_profile(pid="c3", provider="OpenAI", model="gpt-4-turbo",
                            custom=False, provider_id="", base_url="")
    dialog = _dialog(app, profiles=[profile])
    lib = dialog.connection_library
    lib.edit("c3")
    app.processEvents()

    assert dialog.thinking_row.isHidden()
    assert dialog.thinking_hint.isHidden(), "思考行隐藏时提示不应单独残留"
