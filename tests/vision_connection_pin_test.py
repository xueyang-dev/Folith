"""任务钉住的视觉连接 vs 当前视觉连接：密钥与地址永远来自同一个连接。

起因（真实任务）：任务钉着「自定义中转站」的地址，用户后来把翻译连接换成 OpenCode Go；
续跑时地址仍是中转站、密钥却取自当前的 OpenCode Go——每页 401。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402
from folith import ocr_online  # noqa: E402

RELAY = {"id": "relay", "provider": "自定义中转站", "model": "deepseek-v4.1-flash",
         "api_key": "relay-key", "base_url": "https://relay.example/v1", "models": ["m"]}
GO = {"id": "go", "provider": "OpenCode Go", "model": "deepseek-v4.1-flash",
      "api_key": "go-key", "base_url": "", "models": ["m"]}
PINNED = {"provider": "自定义中转站", "model": "deepseek-v4.1-flash",
          "base_url": "https://relay.example/v1", "credential_source": "translation_profile"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)

    def save(connections):
        core.save_provider_config(
            "OpenCode Go", "deepseek-v4.1-flash", "go-key", None,
            connections=connections, connection_roles={"translation": "go", "vision": ""})
    save([RELAY, GO])
    return SimpleNamespace(save=save, path=tmp_path)


def _job(job_id="pin-job", **pipeline):
    state = core.new_job_state("scan.pdf")
    state["pipeline_config"] = {"ocr_backend": "online", "ocr_online": dict(PINNED), **pipeline}
    core.save_job_state(job_id, state)
    return job_id


def test_pinned_endpoint_gets_its_own_connections_key_never_the_current_one(home):
    config, error = core.resolve_ocr_online_config(PINNED)
    assert error is None
    assert config.base_url == "https://relay.example/v1"
    assert config.api_key == "relay-key" and config.api_key != "go-key"
    assert config.credential_owner == config.endpoint_id()
    assert config.unavailable_reason() == ""


def test_no_saved_connection_owns_the_pinned_endpoint_means_no_request(home):
    home.save([GO])  # 中转站连接已删除
    config, error = core.resolve_ocr_online_config(PINNED)
    assert config is None and error["error"] == "connection_missing"
    # 任务级入口：不会带着当前连接的密钥去敲中转站的地址，而是明确失败。
    with pytest.raises(ValueError) as caught:
        core._vlm_runtime(ocr_online_config=dict(PINNED))
    assert "已不在已保存的连接里" in str(caught.value)


def test_guard_refuses_a_key_that_belongs_to_another_endpoint():
    config = ocr_online.OnlineOCRConfig(
        provider="自定义中转站", base_url="https://relay.example/v1", model="m",
        api_key="go-key", credential_owner="OpenCode Go|https://opencode.ai/zen/go/v1")
    assert "另一个连接" in config.unavailable_reason()
    calls = []
    backend = ocr_online.OnlineOCRBackend(config)
    backend._get_client = lambda: calls.append(1)  # type: ignore[assignment]
    blocks, error = backend.ocr_page(b"\x89PNG", page_index=0, page_number=1, language="eng", dpi=150)
    assert blocks == [] and "unconfigured" in error and not calls


def test_conflict_is_reported_with_the_old_connection_present_and_choice_is_persisted(home):
    from folith.qt import adapter
    job = _job()
    conflict = adapter.vision_connection_conflict(job)
    assert conflict["pinned_label"] == "自定义中转站 · deepseek-v4.1-flash"
    assert conflict["current_label"] == "OpenCode Go · deepseek-v4.1-flash"
    adapter.set_vision_choice(job, conflict, "pinned")
    saved = core.load_job_state(job)["pipeline_config"]
    assert saved["ocr_online_choice"]["choice"] == "pinned"
    assert adapter.vision_connection_conflict(job) is None  # 已选过，不再询问
    # 选「原连接」：保持钉住的连接，密钥仍来自拥有它的连接。
    assert core._reconcile_vision_connection(job, core.load_job_state(job)) is None
    assert core.load_job_state(job)["pipeline_config"]["ocr_online"]["base_url"] == PINNED["base_url"]


def test_choosing_the_current_connection_replaces_the_pinned_one(home):
    from folith.qt import adapter
    job = _job()
    adapter.set_vision_choice(job, adapter.vision_connection_conflict(job), "current")
    state = core.load_job_state(job)
    message = core._reconcile_vision_connection(job, state)
    assert "OpenCode Go · deepseek-v4.1-flash" in message
    saved = core.load_job_state(job)["pipeline_config"]
    assert saved["ocr_online"]["provider"] == "OpenCode Go"
    assert saved["ocr_online_choice"]["choice"] == "current"
    assert core.load_runtime_state(job)["last_event"] == message


def test_deleted_pinned_connection_falls_back_to_current_and_says_so(home):
    from folith.qt import adapter
    home.save([GO])
    job = _job()
    assert adapter.vision_connection_conflict(job) is None  # 不需要问
    message = core._reconcile_vision_connection(job, core.load_job_state(job))
    assert message == "原识别连接已删除，改用当前连接『OpenCode Go · deepseek-v4.1-flash』继续"
    assert core.load_job_state(job)["pipeline_config"]["ocr_online"]["provider"] == "OpenCode Go"
    runtime = core.load_runtime_state(job)
    assert runtime["last_event"] == message
    assert any(e["message"] == message for e in runtime["events"])


def test_no_conflict_when_the_pinned_endpoint_is_the_current_one(home):
    state = core.new_job_state("scan.pdf")
    current, _ = core.resolve_ocr_online_config(None)
    state["pipeline_config"] = {"ocr_online": current.describe()}
    assert core.vision_connection_conflict(state["pipeline_config"]) is None


def _fake_window(monkeypatch, *, automation, picked=0):
    from folith.qt import adapter, prompts
    from folith.qt import window as window_module
    asked = []
    monkeypatch.setattr(window_module, "_automation", lambda: automation)
    monkeypatch.setattr(prompts.ChoiceDialog, "ask", classmethod(
        lambda cls, parent, title, message, *, options, cancel="取消":
        asked.append((message, options)) or picked))
    recorded = []
    monkeypatch.setattr(adapter, "vision_connection_conflict", lambda job: {
        "pinned_label": "A · m", "current_label": "B · m", "pinned_id": "a", "current_id": "b"})
    monkeypatch.setattr(adapter, "set_vision_choice",
                        lambda job, conflict, choice: recorded.append(choice))
    fake = SimpleNamespace(_failure=lambda *a, **k: None)
    return window_module.MainWindow._choose_vision_connection, fake, asked, recorded


def test_resume_prompt_defaults_to_current_and_remembers_the_choice(monkeypatch):
    choose, fake, asked, recorded = _fake_window(monkeypatch, automation=False, picked=0)
    assert choose(fake, "job") is True
    assert "原来用『A · m』" in asked[0][0] and "『B · m』" in asked[0][0]
    assert asked[0][1] == ["用当前连接", "继续用原连接"]  # 第一个（默认）是当前连接
    assert recorded == ["current"]
    choose, fake, asked, recorded = _fake_window(monkeypatch, automation=False, picked=1)
    assert choose(fake, "job") and recorded == ["pinned"]
    choose, fake, asked, recorded = _fake_window(monkeypatch, automation=False, picked=None)
    assert choose(fake, "job") is False and recorded == []  # 取消：不启动


def test_automation_never_opens_the_dialog_and_picks_current(monkeypatch):
    choose, fake, asked, recorded = _fake_window(monkeypatch, automation=True)
    assert choose(fake, "job") is True and asked == [] and recorded == ["current"]


def test_credentials_are_looked_up_by_the_endpoint_owner(home, monkeypatch):
    """密钥按「拥有这个地址的连接」查找；找不到就是 None（调用方据此放弃，而不是借用别的密钥）。"""
    found = core.credential_for_endpoint("自定义中转站", "https://relay.example/v1")
    assert found[0] == "relay-key"
    home.save([GO])
    assert core.credential_for_endpoint("自定义中转站", "https://relay.example/v1") is None


def test_a_replayed_pinned_connection_is_not_an_explicit_choice(home):
    """Resume rebuilds settings from the task, so the pinned descriptor comes back as an argument."""
    saved = {"ocr_online": dict(PINNED)}
    assert core._explicit_ocr_online(dict(PINNED), saved) is None
    assert core._explicit_ocr_online(None, saved) is None
    other = {**PINNED, "provider": "OpenCode Go", "base_url": ""}
    assert core._explicit_ocr_online(other, saved) == other   # a real override still wins


def test_resume_settings_replay_the_pinned_connection_and_the_pipeline_lets_the_choice_win(home):
    from folith.application.commands import as_settings_sources
    from folith.application.job_config import JobConfig
    from folith.application.settings import resolve_effective_settings
    from folith.qt import adapter
    job = _job()
    adapter.set_vision_choice(job, adapter.vision_connection_conflict(job), "current")
    state = core.load_job_state(job)
    replayed = JobConfig.from_settings(
        resolve_effective_settings(as_settings_sources({}).with_state(state))).ocr_online
    assert replayed["provider"] == "自定义中转站"            # the bug's precondition
    explicit = core._explicit_ocr_online(replayed, state["pipeline_config"])
    assert explicit is None
    assert core._reconcile_vision_connection(job, state)    # so the switch happens
    assert core.load_job_state(job)["pipeline_config"]["ocr_online"]["provider"] == "OpenCode Go"


def test_deepseek_vision_reads_default_to_thinking_off_without_overriding_choices():
    from folith.engine.ocr_config import default_vision_reasoning, _vision_reasoning_mode
    assert default_vision_reasoning("DeepSeek", "deepseek-flash") == "thinking_disabled"
    assert default_vision_reasoning("OpenCode Go", "deepseek-v4.1-flash") == "thinking_disabled"
    assert default_vision_reasoning("OpenAI", "gpt-4o") == "provider_default"
    assert default_vision_reasoning("自定义中转站", "dots3-note-prev") == "provider_default"
    # Following the translation connection: an earlier recorded default is not a choice.
    assert _vision_reasoning_mode("translation_profile", "DeepSeek", "deepseek-flash",
                                  "provider_default") == "thinking_disabled"
    # A dedicated vision connection's explicit choice is kept.
    assert _vision_reasoning_mode("ocr_profile", "DeepSeek", "deepseek-flash",
                                  "provider_default") == "provider_default"
    assert _vision_reasoning_mode("translation_profile", "OpenAI", "gpt-4o",
                                  "") == "provider_default"
