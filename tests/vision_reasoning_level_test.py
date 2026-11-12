"""读页的推理强度：本地网关给档位，视觉读页默认调到 low。

起因（真实任务）：网关前面是推理模型，思考也算在输出预算里。8192 的预算下，
第 25 页 8189 个 token 全花在推理上，正文为空，任务在 4% 卡住反复重试。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402
from folith import ocr_online  # noqa: E402

GATEWAY = "http://127.0.0.1:3425/v1"
MODEL = "auto/deepseek-v4.1-flash"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    return tmp_path


def test_the_gateway_offers_the_portable_levels():
    assert core.reasoning_effort_options("Local Gateway", MODEL) == ("low", "medium", "high")
    assert core.reasoning_effort_options("Local Gateway", "") == ()


def test_the_thinking_switch_stays_off_the_gateway():
    """网关收下 thinking 字段但不照做（实测反而更糟），所以不给这个开关。"""
    assert core.thinking_control_options("Local Gateway", MODEL) == ()


def test_vision_reads_default_to_a_low_level_only_on_the_gateway():
    assert ocr_online.default_vision_effort("Local Gateway", MODEL) == "low"
    assert ocr_online.default_vision_effort("DeepSeek", "deepseek-v4.1-flash") == ""


def test_an_unknown_level_is_dropped_rather_than_sent():
    config = ocr_online.OnlineOCRConfig(
        provider="Local Gateway", base_url=GATEWAY, model=MODEL, reasoning_effort="turbo"
    ).normalized()
    assert config.reasoning_effort == ""


def test_the_level_travels_only_to_an_endpoint_measured_to_honour_it():
    gateway = ocr_online.OnlineOCRConfig(
        provider="Local Gateway", base_url=GATEWAY, model=MODEL, reasoning_effort="low"
    ).normalized()
    body = ocr_online.OnlineOCRBackend(gateway)._request_body("system", [])
    assert body["reasoning_effort"] == "low"

    elsewhere = ocr_online.OnlineOCRConfig(
        provider="OpenAI", base_url="https://api.openai.com/v1", model="gpt-5",
        api_key="k", reasoning_effort="low",
    ).normalized()
    assert "reasoning_effort" not in ocr_online.OnlineOCRBackend(elsewhere)._request_body("s", [])


def test_the_level_is_part_of_the_cache_identity():
    config = ocr_online.OnlineOCRConfig(
        provider="Local Gateway", base_url=GATEWAY, model=MODEL, reasoning_effort="low"
    ).normalized()
    assert "effort=low" in config.cache_options()
    assert config.describe()["reasoning_effort"] == "low"


def test_a_task_that_recorded_no_level_gets_the_gateway_default(home):
    core.save_provider_config("Local Gateway", MODEL, "local-key", GATEWAY)
    snapshot = {
        "provider": "Local Gateway", "model": MODEL, "base_url": GATEWAY,
        "credential_source": "translation_profile",
    }
    config, error = core.resolve_ocr_online_config(snapshot)
    assert error is None
    assert config.reasoning_effort == "low"


def test_an_old_task_budget_is_raised_so_the_thinking_fits(home):
    """老任务记着 8192；推理端点需要至少当前的默认预算，否则密排页照旧失败。"""
    core.save_provider_config("Local Gateway", MODEL, "local-key", GATEWAY)
    snapshot = {
        "provider": "Local Gateway", "model": MODEL, "base_url": GATEWAY,
        "credential_source": "translation_profile", "max_tokens": 8192,
    }
    config, error = core.resolve_ocr_online_config(snapshot)
    assert error is None
    assert config.max_tokens == core.OCR_ONLINE_DEFAULT_MAX_TOKENS == 32768


def test_another_endpoint_keeps_the_budget_the_task_recorded(home):
    core.save_provider_config("自定义中转站", "deepseek-v4.1-flash", "relay-key",
                              "https://relay.example/v1")
    snapshot = {
        "provider": "自定义中转站", "model": "deepseek-v4.1-flash",
        "base_url": "https://relay.example/v1",
        "credential_source": "translation_profile", "max_tokens": 8192,
    }
    config, error = core.resolve_ocr_online_config(snapshot)
    assert error is None
    assert config.max_tokens == 8192
    assert config.reasoning_effort == ""
