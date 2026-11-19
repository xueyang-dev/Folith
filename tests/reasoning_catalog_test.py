"""#540 PR-1: the verified reasoning catalogue, byte-for-byte dispatch and the one trace field."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

import core  # noqa: E402
from folith.engine import llm_provider, reasoning_catalog as cat  # noqa: E402
from folith import llm_transport  # noqa: E402


# ---- catalogue data -----------------------------------------------------------

def test_every_entry_has_levels_default_and_verified_evidence():
    assert len(cat.ENTRIES) == 4
    ids = [e.id for e in cat.ENTRIES]
    assert ids == ["local-gateway-effort", "deepseek-thinking-switch",
                   "dots-vllm-switch", "openai-reasoning-effort"]
    for entry in cat.ENTRIES:
        assert entry.levels
        assert len({level.key for level in entry.levels}) == len(entry.levels)
        assert entry.default == "service_default" or entry.default in {
            level.key for level in entry.levels}
        assert len(entry.verified) > 30
        assert all(level.dispatch in {"reasoning_effort", "thinking_disabled",
                                      "thinking_disabled_vllm", "service_default"}
                   for level in entry.levels)


@pytest.mark.parametrize("provider,model,expected", [
    ("Local Gateway", "auto/deepseek-v4.1-flash", "local-gateway-effort"),
    ("Local Gateway", "anything", "local-gateway-effort"),
    ("Local Gateway", "", None),
    ("DeepSeek", "deepseek-v4.1-flash", "deepseek-thinking-switch"),
    ("OpenCode Go", "deepseek-flash", "deepseek-thinking-switch"),
    ("DeepSeek", "deepseek-chat", None),
    ("OpenAI", "dots3-note-prev", "dots-vllm-switch"),
    ("OpenAI", "dots-foo", "dots-vllm-switch"),
    ("Custom", "dots3-note-prev", None),
    ("OpenAI", "o1", "openai-reasoning-effort"),
    ("OpenAI", "o4-mini", "openai-reasoning-effort"),
    ("OpenAI", "gpt-5-chat-latest", None),
    ("Custom", "unknown", None),
])
def test_matching(provider, model, expected):
    entry = cat.match(provider, model)
    assert (entry.id if entry else None) == expected


def test_gateway_has_no_off_level_and_deepseek_has_one():
    assert cat.levels_for("Local Gateway", "m") == cat.EFFORT_LEVELS
    assert [l.key for l in cat.levels_for("DeepSeek", "deepseek-v4")] == ["off", "on"]
    assert "off" not in [l.key for l in cat.levels_for("Local Gateway", "m")]


# ---- old public contract is preserved exactly ---------------------------------

@pytest.mark.parametrize("provider,model,effort,switches", [
    ("Local Gateway", "m", ("low", "medium", "high"), ()),
    ("Local Gateway", "", (), ()),
    ("OpenAI", "o1", ("low", "medium", "high"), ()),
    ("OpenAI", "o4-mini", ("low", "medium", "high"), ()),
    ("OpenAI", "gpt-5-chat", (), ()),
    ("OpenAI", "gpt-4o", (), ()),
    ("DeepSeek", "deepseek-v4", (), ("thinking_disabled",)),
    ("OpenCode Go", "deepseek-flash", (), ("thinking_disabled",)),
    ("OpenAI", "dots3", (), ("thinking_disabled_vllm",)),
    ("Custom", "dots3", (), ()),          # only OpenAI-compatible providers
    ("Gemini", "dots3", (), ()),
    ("Custom", "unknown", (), ()),
])
def test_public_helpers_keep_the_old_contract(provider, model, effort, switches):
    assert llm_provider.reasoning_effort_options(provider, model) == effort
    assert core.reasoning_effort_options(provider, model) == effort
    assert llm_provider.thinking_control_options(provider, model) == switches
    assert core.thinking_control_options(provider, model) == switches
    assert cat.effort_options(provider, model) == effort
    assert cat.thinking_options(provider, model) == switches


# ---- byte-for-byte request bodies ---------------------------------------------

def _request(provider, model, effort=None, mode="", temperature=0.1):
    request = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    cat.apply_to_request(request, provider, model,
                         effort=effort, mode=mode, temperature=temperature)
    return request


def test_low_effort_with_no_temperature_for_openai_reasoning_family():
    assert _request("OpenAI", "o1", effort="low", temperature=0.7) == {
        "model": "o1", "messages": [{"role": "user", "content": "hi"}],
        "reasoning_effort": "low",
    }


def test_low_effort_keeps_temperature_on_a_gateway():
    assert _request("Local Gateway", "m", effort="low", temperature=0.7)[
        "temperature"] == 0.7


def test_deepseek_off_and_dots_vllm_switch_shapes():
    assert _request("DeepSeek", "deepseek-v4", mode="thinking_disabled")["extra_body"] == {
        "thinking": {"type": "disabled"}}
    assert _request("Custom", "dots3", mode="thinking_disabled_vllm")["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}}


def test_openai_naming_quirk_keeps_its_original_prefix_set():
    # o4 (not o4-mini) is in the temperature quirk but not the catalogue.
    assert "temperature" not in _request("OpenAI", "o4", effort=None)
    assert cat.match("OpenAI", "o4") is None


# ---- the single unified trace field -------------------------------------------

def test_level_record_is_the_only_per_call_field():
    assert cat.level_record("Local Gateway", "m", effort="low") == {
        "level": "low", "dispatch": "reasoning_effort"}
    assert cat.level_record("DeepSeek", "deepseek-v4", mode="thinking_disabled") == {
        "level": "off", "dispatch": "thinking_disabled"}
    assert cat.level_record("Custom", "unknown") == {
        "level": "service_default", "dispatch": "service_default"}


def test_call_llm_records_the_unified_field(tmp_path, monkeypatch):
    """The provider call's trace record carries level+method, not the old key."""
    seen = []
    from folith.trace import trace

    class _Completions:
        def create(self, **_request):
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=40, total_tokens=50)
            message = SimpleNamespace(content="译文")
            return SimpleNamespace(id="r1", choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

        def close(self):
            pass

    run = trace.start_run("t-level", output_dir=tmp_path)
    monkeypatch.setattr("builtins.open", open)  # keep io helpers intact
    original = run.record_model_call
    run.record_model_call = lambda **kwargs: (seen.append(kwargs), original(**kwargs))[1]
    llm_transport.close_all_clients()
    monkeypatch.setattr(core, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "1")
    core._RUNTIME_CTX.job_id = "t-level"
    try:
        core.call_llm("Local Gateway", "sk-x", "auto/deepseek-v4.1-flash", "sys", "usr",
                      base_url="http://127.0.0.1:3425/v1", reasoning_effort="low")
    finally:
        del core._RUNTIME_CTX.job_id
        trace.finish_run("t-level")
        llm_transport.close_all_clients()
    assert seen, "the model call must be recorded"
    metadata = seen[-1]["metadata"]
    assert cat.LEVEL_FIELD in metadata
    assert metadata[cat.LEVEL_FIELD] == {"level": "low", "dispatch": "reasoning_effort"}
    assert "reasoning_mode" not in metadata


def test_runtime_telemetry_accepts_the_same_field():
    from folith.engine import runtime_telemetry

    coalescer = runtime_telemetry._RuntimeTelemetryCoalescer("job", writer=lambda **kw: kw)
    level = {"level": "off", "dispatch": "thinking_disabled"}
    seen = []
    coalescer.submit = lambda **kw: seen.append(kw)
    coalescer.request_finished(purpose="翻译", reasoning_level=level)
    assert seen[-1]["reasoning_level"] == level
