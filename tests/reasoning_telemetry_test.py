"""Reasoning usage is unknown-not-zero; empty stages report no work; thinking-off is opt-in."""
import json
import time
from types import SimpleNamespace

import pytest

import core
from folith import llm_transport
from folith.performance import PipelineProfiler
from folith.trace import RunContext, TraceLevel, aggregate_optional, trace


# ---- fake SDK ------------------------------------------------------------

class _Completions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **request):
        self.owner.requests.append(request)
        return self.owner.response


class _FakeOpenAI:
    requests = []
    response = None

    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(completions=_Completions(type(self)))

    def close(self):
        pass


def _response(usage=None, content="译文", reasoning=None):
    message = SimpleNamespace(content=content)
    if reasoning is not None:
        message.reasoning_content = reasoning
    return SimpleNamespace(id="r1", choices=[SimpleNamespace(message=message)], usage=usage)


@pytest.fixture(autouse=True)
def _fake_sdk(tmp_path, monkeypatch):
    llm_transport.close_all_clients()
    _FakeOpenAI.requests = []
    _FakeOpenAI.response = _response()
    monkeypatch.setattr(core, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "1")
    core.set_llm_reasoning_modes(None, None)
    yield
    core.set_llm_reasoning_modes(None, None)
    llm_transport.close_all_clients()


def _run_and_calls(tmp_path, response, **call_kwargs):
    _FakeOpenAI.response = response
    run = trace.start_run("t-usage", output_dir=tmp_path)
    try:
        core._RUNTIME_CTX.job_id = "t-usage"
        core.call_llm("OpenCode Go", "sk-x", "deepseek-v4.1-flash", "sys", "usr", **call_kwargs)
        return run, run.model_calls[-1]
    finally:
        del core._RUNTIME_CTX.job_id
        trace.finish_run("t-usage")


# ---- 1. None vs 0 -----------------------------------------------------------

def test_missing_usage_is_none_not_zero(tmp_path):
    _, call = _run_and_calls(tmp_path, _response(usage=None))
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens",
                "total_tokens", "reasoning_tokens", "reasoning_chars"):
        assert call[key] is None, key
    assert call["metadata"]["usage_available"] is False


def test_reasoning_tokens_and_chars_recorded_separately(tmp_path):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=500, total_tokens=510,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=480))
    _, call = _run_and_calls(tmp_path, _response(usage=usage, reasoning="x" * 77))
    assert call["reasoning_tokens"] == 480
    assert call["reasoning_chars"] == 77          # chars, not tokens
    assert call["completion_tokens"] == 500
    assert call["cached_tokens"] is None          # not returned -> unknown


def test_partial_usage_keeps_unreturned_fields_none(tmp_path):
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    _, call = _run_and_calls(tmp_path, _response(usage=usage))
    assert call["reasoning_tokens"] is None and call["reasoning_chars"] is None


def test_genuine_zero_is_preserved():
    ctx = RunContext("zero", output_dir=None, level=TraceLevel.OFF)
    ctx.record_model_call("p", "m", 1.0, prompt_tokens=0, completion_tokens=0, reasoning_tokens=0)
    assert ctx.model_calls[0]["reasoning_tokens"] == 0
    assert ctx.model_calls[0]["total_tokens"] == 0


def test_aggregation_marks_partial_and_unknown(tmp_path):
    ctx = RunContext("agg", output_dir=tmp_path, level=TraceLevel.STANDARD)
    ctx.record_model_call("p", "m", 2.0, prompt_tokens=10, completion_tokens=100, reasoning_tokens=90)
    ctx.record_model_call("p", "m", 4.0)                       # usage missing entirely
    ctx.record_model_call("p", "m", 1.0, prompt_tokens=5, completion_tokens=50)  # no reasoning field
    summary = ctx.generate_summary()
    mm = summary["model_metrics"]
    assert mm["total_completion_tokens"] == 150
    assert mm["token_usage"]["completion_tokens"] == {
        "total": 150, "calls_reported": 2, "calls_total": 3, "complete": False}
    assert mm["total_reasoning_tokens"] == 90
    assert mm["token_usage"]["reasoning_tokens"]["calls_reported"] == 1
    assert mm["total_cached_tokens"] is None                   # nobody reported -> unknown
    # throughput ignores the call that reported no usage (4 s must not dilute it)
    assert mm["tokens_per_second"] == pytest.approx(150 / 3.0, abs=0.01)
    md = ctx.generate_performance_report_md(summary, ctx.generate_stages_doc())
    assert "partial: 2/3" in md and "Cached: unknown" in md
    json.dumps(summary)                                        # None survives serialization


def test_aggregate_optional_empty():
    assert aggregate_optional([], "x")["complete"] is False
    assert aggregate_optional([], "x")["total"] is None


# ---- 2. zero-span stage timing ---------------------------------------------

def test_stage_with_no_items_reports_no_work(tmp_path):
    run = trace.start_run("t-stage", output_dir=tmp_path)
    try:
        profiler = PipelineProfiler(tmp_path / "performance.json", run_id=run.run_id)
        idle = profiler.start_stage("translation_semantic_review")
        busy = profiler.start_stage("translation_generation")
        t0 = time.perf_counter()
        busy.item("a", t0 - 3.0, finished_monotonic=t0 - 1.0)   # 2 s
        busy.item("b", t0 - 2.5, finished_monotonic=t0 - 0.5)   # overlaps: union 2.5 s, sum 4 s
        time.sleep(0.05)
        busy.finish()
        idle.finish()
        trace.finish_run("t-stage")
        stages = run.stages
        assert stages["translation_semantic_review"]["wall_time_s"] == 0.0
        assert stages["translation_semantic_review"]["no_work"] is True
        assert stages["translation_semantic_review"]["lifetime_s"] > 0.0
        assert stages["translation_generation"]["wall_time_s"] == pytest.approx(2.5, abs=0.01)
        assert stages["translation_generation"]["busy_time_s"] == pytest.approx(4.0, abs=0.01)
        summary = json.loads((run.run_dir / "summary.json").read_text(encoding="utf-8"))
        row = summary["stages"]["translation_semantic_review"]
        assert row["wall_time_s"] == 0.0 and row["no_work"] is True and row["pct_of_total"] == 0.0
        md = (run.run_dir / "performance.md").read_text(encoding="utf-8")
        line = next(l for l in md.splitlines() if l.startswith("| `translation_semantic_review`"))
        assert "no work" in line and "s |" not in line.split("|")[2]
    finally:
        trace.finish_run("t-stage")


def test_span_stage_wall_is_union_not_sum(tmp_path):
    ctx = RunContext("spans", output_dir=tmp_path, level=TraceLevel.STANDARD)
    import threading
    gate = threading.Barrier(2)

    def work():
        with ctx.span("parallel", "op"):
            gate.wait()
            time.sleep(0.1)

    threads = [threading.Thread(target=work) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ctx.finish()
    rec = ctx.stages["parallel"]
    assert rec["busy_time_s"] >= 0.19                  # two overlapping 0.1 s spans
    assert rec["wall_time_s"] < rec["busy_time_s"] * 0.75
    assert rec["no_work"] is False


# ---- 3. thinking-disabled opt-in ----------------------------------------------

def _body_for(**call_kwargs):
    _FakeOpenAI.requests.clear()
    core.call_llm("OpenCode Go", "sk-x", "deepseek-v4.1-flash", "s", "u", **call_kwargs)
    return _FakeOpenAI.requests[-1]


def test_default_sends_no_thinking_control():
    assert "extra_body" not in _body_for()


def test_explicit_mode_sends_disabled_body():
    assert _body_for(reasoning_mode="thinking_disabled")["extra_body"] == {
        "thinking": {"type": "disabled"}}
    assert "extra_body" not in _body_for(reasoning_mode="provider_default")


def test_thread_mode_is_per_role():
    from folith import usage as _usage
    core.set_llm_reasoning_modes("thinking_disabled", None)
    token = _usage.CURRENT_ROLE.set("translation")
    try:
        assert "extra_body" in _body_for()
    finally:
        _usage.CURRENT_ROLE.reset(token)
    token = _usage.CURRENT_ROLE.set("auxiliary")
    try:
        assert "extra_body" not in _body_for()          # auxiliary not opted in
    finally:
        _usage.CURRENT_ROLE.reset(token)
    token = _usage.CURRENT_ROLE.set("review")
    try:
        assert "extra_body" not in _body_for()          # reviewer never inherits
    finally:
        _usage.CURRENT_ROLE.reset(token)


def test_route_and_cache_key_separate_thinking_modes():
    base = core.effective_request_route("OpenCode Go", "deepseek-v4.1-flash", role="translation")
    assert "reasoning_mode" not in base                 # default keys unchanged
    core.set_llm_reasoning_modes("thinking_disabled", "thinking_disabled")
    off = core.effective_request_route("OpenCode Go", "deepseek-v4.1-flash", role="translation")
    assert off["reasoning_mode"] == "thinking_disabled" and off != base
    from folith import translation_cache
    kw = dict(provider="OpenCode Go", model="m", system_prompt="s", user_prompt="u",
              temperature=0.3, response_format=None)
    assert translation_cache.prompt_fingerprint(**kw, route=base) != \
        translation_cache.prompt_fingerprint(**kw, route=off)


def test_run_configuration_records_mode(tmp_path):
    run = trace.start_run("t-cfg", output_dir=tmp_path)
    try:
        run.record_run_configuration({"reasoning_mode": "thinking_disabled",
                                      "auxiliary_reasoning_mode": "provider_default"})
        trace.finish_run("t-cfg")
        events = [json.loads(l) for l in (run.run_dir / "requests.jsonl").read_text(
            encoding="utf-8").splitlines()]
        cfg = next(e for e in events if e["event"] == "run_configuration")["configuration"]
        assert cfg["reasoning_mode"] == "thinking_disabled"
        assert cfg["auxiliary_reasoning_mode"] == "provider_default"
    finally:
        trace.finish_run("t-cfg")
