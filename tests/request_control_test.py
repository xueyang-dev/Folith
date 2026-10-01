"""Provider-scoped rate-limit control contracts.

The behaviour being pinned: a 429 produces one *shared* cooldown that every
concurrent worker observes, with exponential backoff and jitter, and waiting is
interruptible by cancellation.  The old behaviour — a worker-local fixed
``sleep(15)`` — is explicitly ruled out.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import core
from folith import request_control
from folith import source_cleanup


@pytest.fixture(autouse=True)
def _clean_controllers():
    request_control.reset_request_controllers()
    yield
    request_control.reset_request_controllers()


def test_gate_spaces_request_starts():
    controller = request_control.ProviderRequestController(
        "test|gate", min_interval=0.05, jitter_ratio=0.0)
    started = []
    lock = threading.Lock()

    def worker():
        controller.acquire()
        with lock:
            started.append(time.monotonic())

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _i: worker(), range(4)))

    started.sort()
    gaps = [right - left for left, right in zip(started, started[1:])]
    assert len(gaps) == 3
    assert min(gaps) >= 0.03, gaps


def test_backoff_grows_exponentially_and_is_capped():
    controller = request_control.ProviderRequestController(
        "test|backoff", base_backoff=0.05, max_backoff=0.2, jitter_ratio=0.0)
    first = controller.note_rate_limited()
    second = controller.note_rate_limited()
    third = controller.note_rate_limited()
    fourth = controller.note_rate_limited()
    assert first == pytest.approx(0.05)
    assert second == pytest.approx(0.10)
    assert third == pytest.approx(0.20)
    assert fourth == pytest.approx(0.20)  # capped


def test_jitter_stays_within_the_configured_ratio():
    controller = request_control.ProviderRequestController(
        "test|jitter", base_backoff=1.0, max_backoff=10.0, jitter_ratio=0.25,
        random_source=lambda: 1.0)
    assert controller.note_rate_limited() == pytest.approx(1.25)
    controller2 = request_control.ProviderRequestController(
        "test|jitter2", base_backoff=1.0, max_backoff=10.0, jitter_ratio=0.25,
        random_source=lambda: 0.0)
    assert controller2.note_rate_limited() == pytest.approx(1.0)


def test_shared_cooldown_blocks_every_worker():
    controller = request_control.ProviderRequestController(
        "test|shared", base_backoff=0.25, max_backoff=0.25, jitter_ratio=0.0)
    controller.note_rate_limited()
    started = []
    lock = threading.Lock()

    def worker():
        controller.acquire()
        with lock:
            started.append(time.monotonic())

    begin = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _i: worker(), range(4)))
    # Nobody may start before the shared cooldown expires.
    assert min(started) - begin >= 0.2


def test_note_success_resets_the_backoff_ladder():
    controller = request_control.ProviderRequestController(
        "test|reset", base_backoff=0.1, max_backoff=10.0, jitter_ratio=0.0)
    assert controller.note_rate_limited() == pytest.approx(0.1)
    assert controller.note_rate_limited() == pytest.approx(0.2)
    controller.note_success()
    assert controller.note_rate_limited() == pytest.approx(0.1)


def test_acquire_honours_cancellation_without_waiting_the_full_backoff():
    controller = request_control.ProviderRequestController(
        "test|cancel", base_backoff=5.0, max_backoff=5.0, jitter_ratio=0.0)
    controller.note_rate_limited()
    cancelled = threading.Event()

    def cancel_check():
        return cancelled.is_set()

    def worker():
        time.sleep(0.15)
        cancelled.set()
        with pytest.raises(RuntimeError, match="取消"):
            controller.acquire(cancel_check)

    started = time.monotonic()
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    # returned as soon as cancellation was observed, not after the 5 s cooldown
    assert time.monotonic() - started < 2.0


def test_controllers_are_scoped_by_provider_and_model():
    a = request_control.get_request_controller("DeepSeek", "m1")
    b = request_control.get_request_controller("DeepSeek", "m2")
    c = request_control.get_request_controller("OpenAI", "m1")
    assert a is request_control.get_request_controller("DeepSeek", "m1")
    assert a is not b and b is not c and a is not c


def test_cleanup_gate_is_the_shared_implementation():
    assert issubclass(source_cleanup._RequestGate,
                      request_control.ProviderRequestController)
    gate = source_cleanup._RequestGate(0.02)
    assert gate.min_interval == pytest.approx(0.02)
    assert gate.wait() >= 0.0


def test_translate_batch_uses_shared_backoff_not_a_fixed_sleep(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_BASE_BACKOFF", "0.02")
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_MAX_BACKOFF", "0.05")
    request_control.reset_request_controllers()
    attempts = []

    def always_rate_limited(*_args, **_kwargs):
        attempts.append(time.monotonic())
        raise RuntimeError("HTTP 429 rate limit")

    retry_events = []
    monkeypatch.setattr(core, "_runtime_job_id", lambda: "retry_display_test")
    monkeypatch.setattr(core, "update_runtime_state", lambda job_id, **kwargs: retry_events.append(kwargs))
    monkeypatch.setattr(core, "_llm_cancel_check", lambda: None)
    monkeypatch.setattr(core, "call_llm", always_rate_limited)
    begin = time.monotonic()
    with pytest.raises(RuntimeError, match="批次翻译失败"):
        core.translate_batch(["one"], [], [], "", "", "zh",
                             "DeepSeek", "key", "deepseek-v4-flash")
    elapsed = time.monotonic() - begin
    assert len(attempts) == 3
    assert [e["event"] for e in retry_events] == [
        "翻译请求未完成，正在重试（第 2/3 次尝试）",
        "翻译请求未完成，正在重试（第 3/3 次尝试）",
    ]
    # The old fixed sleep(15) path needed ~30 s for two retries.
    assert elapsed < 2.0, elapsed


def test_concurrent_translate_batches_share_one_cooldown(monkeypatch):
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_BASE_BACKOFF", "0.05")
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_MAX_BACKOFF", "0.1")
    monkeypatch.setenv("FOLIOTHREAD_LLM_RATE_LIMIT_JITTER", "0")
    request_control.reset_request_controllers()
    starts = []
    lock = threading.Lock()

    def rate_limited(*_args, **_kwargs):
        with lock:
            starts.append(time.monotonic())
        raise RuntimeError("HTTP 429")

    monkeypatch.setattr(core, "call_llm", rate_limited)
    begin = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(core.translate_batch, ["one"], [], [], "", "", "zh",
                        "DeepSeek", "key", "deepseek-v4-flash")
            for _ in range(4)
        ]
        for future in futures:
            with pytest.raises(RuntimeError):
                future.result()
    elapsed = time.monotonic() - begin
    # 4 workers x 3 attempts = 12 requests; with the shared cooldown they must
    # not all fire at once, and the whole thing stays far below 4 x 30 s.
    assert len(starts) == 12
    assert elapsed < 5.0, elapsed
    starts.sort()
    # the cooldown creates real gaps rather than one simultaneous burst
    assert any(right - left >= 0.03 for left, right in zip(starts, starts[1:]))
