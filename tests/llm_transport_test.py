"""LLM transport reuse and runtime telemetry coalescing contracts.

Two independent guarantees are pinned here:

1. Transport clients are reused per thread and per behaviour identity, the API
   key never appears in plaintext in a cache key or a stats payload, and the
   pool can be closed explicitly.
2. High-frequency "in flight" runtime telemetry is coalesced, so a burst of
   concurrent requests does not produce a linear number of full
   ``runtime_state.json`` rewrites and fsyncs — while cancellation and failure
   still persist immediately and the UI still sees "waiting for model".
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import core
from folith import llm_transport


class _FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **_kwargs):
        owner = self._owner
        owner.calls += 1
        if owner.delay:
            time.sleep(owner.delay)
        if owner.error is not None:
            raise owner.error
        return SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content="OK"))])


class _FakeOpenAI:
    """Stands in for ``openai.OpenAI``; counts real constructions."""

    instances = 0
    lock = threading.Lock()
    default_delay = 0.0
    default_error = None

    def __init__(self, **kwargs):
        with _FakeOpenAI.lock:
            _FakeOpenAI.instances += 1
        self.kwargs = kwargs
        self.calls = 0
        self.delay = type(self).default_delay
        self.error = type(self).default_error
        self.chat = SimpleNamespace(completions=_FakeCompletions(self))
        self.closed = False

    def close(self):
        self.closed = True

    def with_options(self, **kwargs):
        self.request_options = kwargs
        return self


@pytest.fixture(autouse=True)
def _clean_transport(tmp_path, monkeypatch):
    """Isolate the registry, the output dir and the fake SDK per test."""
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    llm_transport.close_all_clients()
    llm_transport.reset_client_registry_stats()
    core.reset_runtime_persistence_stats()
    core.reset_runtime_telemetry()
    _FakeOpenAI.instances = 0
    _FakeOpenAI.default_delay = 0.0
    _FakeOpenAI.default_error = None
    monkeypatch.setattr(core, "OpenAI", _FakeOpenAI)
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "1")
    yield
    core.reset_runtime_telemetry()
    llm_transport.close_all_clients()
    core.OUTPUT_DIR = old_output


def _call(provider="DeepSeek", api_key="sk-secret-abc", **kwargs):
    return core.call_llm(provider, api_key, "deepseek-v4-flash",
                         "system", "user", **kwargs)


def _set_job(job_id):
    core._RUNTIME_CTX.job_id = job_id
    core.job_dir(job_id).mkdir(parents=True, exist_ok=True)


def _call_in_job(job_id, **_kwargs):
    """Worker threads do not inherit the thread-local job id; set it first."""
    core._RUNTIME_CTX.job_id = job_id
    return _call()


def _clear_job():
    if hasattr(core._RUNTIME_CTX, "job_id"):
        del core._RUNTIME_CTX.job_id


# --------------------------------------------------------------------------
# Part 1a — transport client registry
# --------------------------------------------------------------------------

def test_client_is_reused_within_thread():
    assert _call() == "OK"
    assert _call() == "OK"
    assert _call() == "OK"
    assert _FakeOpenAI.instances == 1
    stats = llm_transport.client_registry_stats()
    assert stats["created"] == 1
    assert stats["reused"] == 2


def test_explicit_single_attempt_disables_sdk_retries(monkeypatch):
    seen = []
    original = _FakeOpenAI.with_options
    def capture(self, **kwargs):
        seen.append(kwargs)
        return original(self, **kwargs)
    monkeypatch.setattr(_FakeOpenAI, "with_options", capture)
    assert _call(max_attempts=1) == "OK"
    assert seen == [{"max_retries": 0}]


def test_gemini_single_attempt_is_passed_to_request(monkeypatch):
    seen = []
    def generate_content(**kwargs):
        seen.append(kwargs["config"].http_options.retry_options.attempts)
        return SimpleNamespace(text="OK", usage_metadata=None)
    fake = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    monkeypatch.setattr(llm_transport, "get_registry", lambda: SimpleNamespace(acquire=lambda *args: fake))
    result = core._call_llm_transport({"kind": "gemini"}, "Gemini", "key", "model",
                                      "system", "prompt", max_attempts=1)
    assert result == "OK" and seen == [1]


def test_distinct_identity_dimensions_do_not_share_a_client():
    _call(api_key="key-one")
    _call(api_key="key-two")
    _call(api_key="key-one", base_url="https://relay.example.com/v1")
    assert _FakeOpenAI.instances == 3


def test_api_key_never_appears_in_identity_or_stats():
    secret = "sk-super-secret-value"
    _call(api_key=secret)
    stats = llm_transport.client_registry_stats()
    identity_keys = list(stats["identities"])
    assert identity_keys
    for key in identity_keys:
        assert secret not in key
    identity = llm_transport.build_openai_identity(
        provider="DeepSeek", base_url="https://api.deepseek.com",
        proxy_bypass=False, api_key=secret)
    described = identity.describe()
    assert secret not in repr(described)
    assert described["key_fingerprint"] == llm_transport.api_key_fingerprint(secret)
    assert len(described["key_fingerprint"]) == 16


def test_registry_is_thread_local_and_closable():
    barrier = threading.Barrier(3)
    seen = []
    lock = threading.Lock()

    def worker(name):
        barrier.wait()
        _call(api_key=f"key-{name}")
        with lock:
            seen.append(threading.get_ident())

    threads = [threading.Thread(target=worker, args=(name,))
               for name in ("a", "b", "c")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(seen)) == 3
    assert _FakeOpenAI.instances == 3
    assert llm_transport.close_all_clients() == 3
    assert llm_transport.client_registry_stats()["threads"] == 0


def test_pool_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_LLM_CLIENT_POOL", "0")
    _call()
    _call()
    assert _FakeOpenAI.instances == 2
    assert llm_transport.client_registry_stats()["reused"] == 0


# --------------------------------------------------------------------------
# Part 1b — runtime telemetry coalescing
# --------------------------------------------------------------------------

def test_concurrent_requests_do_not_write_state_linearly():
    job_id = "telemetry-burst"
    _set_job(job_id)
    _FakeOpenAI.default_delay = 0.35
    concurrency = 12
    core.reset_runtime_persistence_stats()
    core.reset_runtime_telemetry()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(
                lambda _i: _call_in_job(job_id), range(concurrency)))
    finally:
        _clear_job()

    assert results == ["OK"] * concurrency
    persistence = core.runtime_persistence_stats()
    telemetry = core.runtime_telemetry_stats()["totals"]
    # 2 submits per request, but far fewer than 2 writes per request.
    assert telemetry["submits"] >= concurrency * 2
    assert persistence["state_writes"] <= concurrency // 2
    assert persistence["fsyncs"] <= concurrency // 2
    assert telemetry["coalesced"] > 0


def test_ui_still_sees_waiting_for_model_while_in_flight():
    job_id = "telemetry-waiting"
    _set_job(job_id)
    _FakeOpenAI.default_delay = 0.5
    core.reset_runtime_telemetry()
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_call_in_job, job_id) for _ in range(3)]
            time.sleep(0.2)
            runtime = core.load_runtime_state(job_id)
            assert runtime["status"] == "waiting_external"
            assert runtime["phase"] == "waiting_llm"
            assert "请求进行中" in runtime["phase_label"]
            for future in futures:
                assert future.result() == "OK"
        core.flush_runtime_telemetry(job_id)
        runtime = core.load_runtime_state(job_id)
        assert runtime["status"] == "running"
        assert runtime["phase_label"] == "处理模型结果"
    finally:
        _clear_job()


def test_failure_persists_immediately_and_clears_active_count():
    job_id = "telemetry-failure"
    _set_job(job_id)
    _FakeOpenAI.default_error = RuntimeError("provider exploded")
    core.reset_runtime_telemetry()
    try:
        with pytest.raises(RuntimeError):
            _call_in_job(job_id)
        # failure must not sit in a pending coalescing window
        runtime = core.load_runtime_state(job_id)
        assert runtime["status"] == "running"
        stats = core.runtime_telemetry_stats()["totals"]
        assert stats["immediate"] >= 2  # start + failure
    finally:
        _clear_job()


def test_cancel_state_is_not_overwritten_by_pending_telemetry():
    job_id = "telemetry-cancel"
    _set_job(job_id)
    _FakeOpenAI.default_delay = 0.3
    core.reset_runtime_telemetry()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_call_in_job, job_id) for _ in range(2)]
            time.sleep(0.05)
            core.update_runtime_state(job_id, cancel_requested=True,
                                      status="cancelling", phase="cancelling",
                                      phase_label="正在取消")
            for future in futures:
                # call_llm surfaces the cancellation once the response lands;
                # that is the intended contract, not a transport failure.
                with pytest.raises(RuntimeError, match="取消"):
                    future.result()
            core.flush_runtime_telemetry(job_id)
        runtime = core.load_runtime_state(job_id)
        assert runtime["cancel_requested"] is True
        assert runtime["status"] == "cancelling"
        assert runtime["phase_label"] == "正在取消"
    finally:
        _clear_job()
