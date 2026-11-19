"""Thinking that was never switched off must be visible before, during and after a run.

The 2026-11-19 case this pins: translation ran on a Local Gateway connection
("magpie"), which offers no thinking switch, while "thinking off" was set on a
different connection (OpenCode Go).  89% of the output tokens were reasoning and
nothing in the UI said so.  Three surfaces now do:

* the setup panel resolves what a new run will really do (engine rules, not a guess);
* a running task gets one activity notice once most output is reasoning;
* the performance report states the reasoning share and flags it as a bottleneck.
"""
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
from folith.engine import llm_provider, reasoning_watch  # noqa: E402
from folith.trace import trace  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # A developer's shell may force a mode; the resolution under test must not see it.
    monkeypatch.delenv("FOLITH_REASONING_MODE", raising=False)
    monkeypatch.delenv("FOLITH_AUXILIARY_REASONING_MODE", raising=False)
    reasoning_watch.reset()
    yield
    reasoning_watch.reset()


MAGPIE = {"id": "gw", "name": "magpie", "provider": "Local Gateway",
          "model": "auto/deepseek-v4.1-flash", "base_url": "http://127.0.0.1:3425/v1",
          "reasoning_effort": "low"}
OPENCODE = {"id": "oc", "name": "OpenCode Go", "provider": "OpenCode Go",
            "model": "deepseek-v4.1-flash", "reasoning_effort": "low",
            "reasoning_mode": "thinking_disabled"}


def _saved(active, *connections):
    return {"provider": active["provider"], "model": active["model"],
            "reasoning_effort": active.get("reasoning_effort", ""),
            "connections": list(connections),
            "connection_roles": {"translation": active["id"]}}


# ---- 1. what a new run will do ------------------------------------------------

def test_gateway_connection_cannot_turn_thinking_off_and_names_the_other_switch():
    status = llm_provider.translation_thinking_status(_saved(MAGPIE, MAGPIE, OPENCODE))
    assert status["state"] == "not_switchable"
    assert status["connection"] == "magpie"
    assert status["effort"] == "low"
    assert status["elsewhere"] == ["OpenCode Go"]


def test_verified_switch_on_the_translation_connection_reads_off():
    status = llm_provider.translation_thinking_status(_saved(OPENCODE, MAGPIE, OPENCODE))
    assert status["state"] == "off"
    assert status["elsewhere"] == []


def test_explicit_service_default_on_a_thinking_model_reads_on():
    deepseek = {"id": "ds", "name": "DeepSeek", "provider": "DeepSeek",
                "model": "deepseek-flash", "reasoning_mode": ""}
    status = llm_provider.translation_thinking_status(_saved(deepseek, deepseek))
    assert status["state"] == "on"


def test_deepseek_without_a_choice_defaults_to_off():
    """Same rule a run uses: the DeepSeek family reads off unless someone chose otherwise."""
    deepseek = {"id": "ds", "name": "DeepSeek", "provider": "DeepSeek", "model": "deepseek-flash"}
    assert llm_provider.translation_thinking_status(_saved(deepseek, deepseek))["state"] == "off"


def test_unknown_model_is_not_guessed():
    mt = {"id": "sf", "name": "SiliconFlow", "provider": "SiliconFlow",
          "model": "tencent/Hunyuan-MT-7B"}
    assert llm_provider.translation_thinking_status(_saved(mt, mt))["state"] == "unknown"


def test_no_translation_model_reports_nothing():
    assert llm_provider.translation_thinking_status({"provider": "", "model": ""}) is None


def test_thinking_view_warns_with_both_connection_names():
    from folith.qt import adapter, thinking_status

    config = adapter.EngineConfig(provider=MAGPIE["provider"], model=MAGPIE["model"],
                                  reasoning_effort="low", connections=[MAGPIE, OPENCODE],
                                  connection_roles={"translation": "gw"})
    view = thinking_status.view(config)
    assert view["tone"] == "warning"
    assert "magpie" in view["text"] and "OpenCode Go" in view["text"]
    assert "低" in view["text"]


def test_thinking_view_only_explains_a_connection_that_simply_cannot_switch():
    from folith.qt import adapter, thinking_status

    config = adapter.EngineConfig(provider=MAGPIE["provider"], model=MAGPIE["model"],
                                  reasoning_effort="low", connections=[MAGPIE],
                                  connection_roles={"translation": "gw"})
    view = thinking_status.view(config)
    assert view["tone"] == "hint"
    assert "magpie" in view["text"]


def test_thinking_view_warns_when_a_switchable_model_is_left_thinking():
    from folith.qt import adapter, thinking_status

    deepseek = {"id": "ds", "name": "DeepSeek", "provider": "DeepSeek",
                "model": "deepseek-flash", "reasoning_mode": ""}
    config = adapter.EngineConfig(provider="DeepSeek", model="deepseek-flash",
                                  connections=[deepseek], connection_roles={"translation": "ds"})
    assert thinking_status.view(config)["tone"] == "warning"


def test_thinking_view_is_silent_for_an_unknown_model():
    from folith.qt import adapter, thinking_status

    config = adapter.EngineConfig(provider="SiliconFlow", model="tencent/Hunyuan-MT-7B")
    assert thinking_status.view(config) == {"text": "", "tone": ""}


def test_setup_panel_shows_and_hides_the_thinking_row():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from folith.qt.setup_panel import TaskSetupPanel

    app = QApplication.instance() or QApplication([])
    panel = TaskSetupPanel()
    try:
        assert panel.thinking_row.isHidden()
        panel.set_thinking_status({"text": "思考：这条连接无法关闭。", "tone": "warning"})
        assert not panel.thinking_row.isHidden()
        assert panel.thinking_note.objectName() == "warningText"
        assert not panel.thinking_settings_button.isHidden()
        panel.set_thinking_status({"text": "思考：已关闭（OpenCode Go）", "tone": "hint"})
        assert panel.thinking_note.objectName() == "hint"
        assert panel.thinking_settings_button.isHidden()
        panel.set_thinking_status({"text": "", "tone": ""})
        assert panel.thinking_row.isHidden()
    finally:
        panel.deleteLater()
        app.processEvents()


# ---- 2. during the run --------------------------------------------------------

def test_watch_fires_once_after_enough_reasoning_heavy_calls():
    results = [reasoning_watch.observe("job", "run", 417, 367)
               for _ in range(reasoning_watch.MIN_CALLS + 5)]
    fired = [r for r in results if r]
    assert len(fired) == 1
    assert results.index(fired[0]) == reasoning_watch.MIN_CALLS - 1
    assert fired[0]["share"] == pytest.approx(367 / 417)


def test_watch_stays_quiet_when_thinking_is_off_or_unreported():
    for _ in range(50):
        assert reasoning_watch.observe("job", "run", 60, 0) is None
        assert reasoning_watch.observe("job2", "run", 60, None) is None


def test_watch_counts_each_run_separately():
    for _ in range(reasoning_watch.MIN_CALLS):
        reasoning_watch.observe("job", "run-1", 400, 350)
    assert any(reasoning_watch.observe("job", "run-2", 400, 350)
               for _ in range(reasoning_watch.MIN_CALLS))


def _record(completion, reasoning, *, status="success", metadata=None):
    return {"status": status, "completion_tokens": completion, "reasoning_tokens": reasoning,
            "metadata": {"reasoning_mode": "provider_default"} if metadata is None else metadata}


def test_gemini_style_and_failed_calls_are_not_counted(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "update_runtime_state",
                        lambda job_id, **kwargs: writes.append(job_id))
    for _ in range(reasoning_watch.MIN_CALLS * 3):
        reasoning_watch.notice_from_record("job", "run", _record(100, 900, metadata={}))
        reasoning_watch.notice_from_record("job", "run", _record(417, 367, status="error"))
    assert writes == []


def test_the_first_crossing_becomes_one_user_activity_event(monkeypatch):
    writes = []
    monkeypatch.setattr(core, "update_runtime_state",
                        lambda job_id, **kwargs: writes.append((job_id, kwargs)))
    for _ in range(reasoning_watch.MIN_CALLS + 3):
        reasoning_watch.notice_from_record("job", "run", _record(417, 367))
    assert len(writes) == 1
    job_id, kwargs = writes[0]
    assert job_id == "job"
    assert kwargs["event"] == reasoning_watch.MESSAGE
    assert kwargs["event_visibility"] == "user"
    assert kwargs["on_timeout"] == "skip"
    assert "88%" in kwargs["event_metadata"]["detail"]


def test_the_notice_reads_as_a_warning_in_the_activity_view():
    from folith import runtime_events_view

    rows = runtime_events_view.activity_rows([{
        "event": "reasoning_heavy", "message": reasoning_watch.MESSAGE,
        "metadata": {"detail": "x"}, "timestamp": "2026-11-19T07:40:00+00:00"}])
    assert rows[-1]["status"] == "warning"
    assert rows[-1]["label"] == reasoning_watch.MESSAGE


def test_a_failing_runtime_write_never_breaks_the_request(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(core, "update_runtime_state", boom)
    for _ in range(reasoning_watch.MIN_CALLS + 1):
        reasoning_watch.notice_from_record("job", "run", _record(417, 367))


# ---- 3. after the run ---------------------------------------------------------

def _report(tmp_path, calls, configuration):
    run = trace.start_run("t-thinking", output_dir=tmp_path)
    try:
        run.record_run_configuration(configuration)
        for completion, reasoning in calls:
            run.record_model_call(provider="Local Gateway", model="auto/deepseek-v4.1-flash",
                                  duration_s=9.0, prompt_tokens=2000,
                                  completion_tokens=completion, reasoning_tokens=reasoning)
    finally:
        trace.finish_run("t-thinking")
    return (run.run_dir / "performance.md").read_text(encoding="utf-8")


def test_report_states_the_share_and_flags_reasoning_heavy_runs(tmp_path):
    md = _report(tmp_path, [(417, 367)] * 30,
                 {"reasoning_mode": "provider_default", "reasoning_effort": "low"})
    assert "**Reasoning Share of Output**: 88.0% of reported output tokens" in md
    assert "thinking mode `provider_default`, reasoning level `low`" in md
    assert "**Reasoning-Heavy Output**" in md


def test_report_does_not_flag_a_run_with_thinking_off(tmp_path):
    md = _report(tmp_path, [(60, 0)] * 40, {"reasoning_mode": "thinking_disabled"})
    assert "**Reasoning Share of Output**: 0.0%" in md
    assert "Reasoning-Heavy Output" not in md


def test_report_says_unknown_when_reasoning_is_not_reported(tmp_path):
    md = _report(tmp_path, [(60, None)] * 5, {})
    assert "**Reasoning Share of Output**: unknown" in md
    assert "Reasoning-Heavy Output" not in md


def test_watch_reads_usage_from_the_openai_compatible_path(tmp_path, monkeypatch):
    """End to end through call_llm: the provider's usage feeds the watch."""
    from folith import llm_transport

    seen = []
    monkeypatch.setattr(reasoning_watch, "notice_from_record",
                        lambda job_id, run_id, rec: seen.append(
                            (job_id, rec["completion_tokens"], rec["reasoning_tokens"])))

    class _Completions:
        def create(self, **_request):
            usage = SimpleNamespace(prompt_tokens=10, completion_tokens=417, total_tokens=427,
                                    completion_tokens_details=SimpleNamespace(reasoning_tokens=367))
            message = SimpleNamespace(content="译文")
            return SimpleNamespace(id="r1", choices=[SimpleNamespace(message=message)], usage=usage)

    class _FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=_Completions())

        def close(self):
            pass

    llm_transport.close_all_clients()
    monkeypatch.setattr(core, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "1")
    trace.start_run("t-watch", output_dir=tmp_path)
    core._RUNTIME_CTX.job_id = "t-watch"
    try:
        core.call_llm("OpenCode Go", "sk-x", "deepseek-v4.1-flash", "sys", "usr")
    finally:
        del core._RUNTIME_CTX.job_id
        trace.finish_run("t-watch")
        llm_transport.close_all_clients()
    assert seen == [("t-watch", 417, 367)]
