"""Executor pools release their workers' pooled LLM clients once shut down."""
from __future__ import annotations

import threading

import pytest

import core
from folith import llm_transport
from folith.review_scheduler import BatchReviewSnapshot, ReviewResult, ReviewScheduler

_IDENTITY = llm_transport.ClientIdentity(
    provider="fake", kind="openai", base_url="http://127.0.0.1:1", proxy_mode="direct",
    timeout_connect=1.0, timeout_read=1.0, max_retries=0, key_fingerprint="0" * 16)


class _FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def pooled(monkeypatch):
    """Acquire a pooled client from the calling thread; record thread and client."""
    monkeypatch.delenv("FOLIOTHREAD_LLM_CLIENT_POOL", raising=False)
    registry = llm_transport.get_registry()
    registry.reopen()
    seen = []
    lock = threading.Lock()

    def acquire():
        client = registry.acquire(_IDENTITY, _FakeClient)
        with lock:
            seen.append((threading.get_ident(), client))

    return registry, seen, acquire


def _assert_released(registry, seen):
    assert seen, "the pool workers never acquired a client"
    owners = {tid for tid, _client in seen}
    assert owners.isdisjoint(registry._by_thread)
    assert all(tid not in owners for entries in registry._all.values() for tid, _c in entries)
    assert all(client.closed for _tid, client in seen)


def test_annotation_pool_releases_worker_clients(pooled, monkeypatch):
    registry, seen, acquire = pooled

    def fake_annotate_batch(pairs, *_args, **_kwargs):
        acquire()
        return [{} for _ in pairs]

    monkeypatch.setattr(core, "annotate_batch", fake_annotate_batch)
    pairs = [{"source": f"s{i}", "target": f"t{i}"} for i in range(6)]
    tasks = [(bi, bi * 2, pairs, [bi * 2, bi * 2 + 1]) for bi in range(3)]
    results = list(core._annotation_results(tasks, "", "fake", "key", "m", "简体中文", 3))
    assert len(results) == 3
    _assert_released(registry, seen)


def test_annotation_pool_releases_clients_when_consumer_stops_early(pooled, monkeypatch):
    registry, seen, acquire = pooled

    def fake_annotate_batch(pairs, *_args, **_kwargs):
        acquire()
        return [{} for _ in pairs]

    monkeypatch.setattr(core, "annotate_batch", fake_annotate_batch)
    pairs = [{"source": f"s{i}", "target": f"t{i}"} for i in range(4)]
    tasks = [(bi, bi, pairs, [bi]) for bi in range(4)]
    results = core._annotation_results(tasks, "", "fake", "key", "m", "简体中文", 2)
    next(results)
    results.close()  # e.g. the consumer raised mid-run
    _assert_released(registry, seen)


def test_review_scheduler_shutdown_releases_worker_clients(pooled):
    registry, seen, acquire = pooled
    scheduler = ReviewScheduler(max_in_flight=2, reviewer_workers=2)
    snapshot = BatchReviewSnapshot.create(
        batch_index=0, offset=0,
        batch_pairs=[{"source": "s", "target": "t", "initial_target": "t", "from_tm": False}],
        glossary=[], glossary_text="", glossary_ids=[], glossary_hash="", style_rules="",
        target_lang="简体中文", section_profile={}, deterministic_findings=[],
        blocked_local_indexes=set(), review_context={}, formal_segment_ids=[0],
        evidence_index=None, formal_packet={}, translator_model="test-model")

    def worker(snap):
        acquire()
        return ReviewResult(
            batch_index=snap.batch_index, offset=snap.offset, review_succeeded=True,
            review_failed=False, rfindings=[], review_trace=None, review_event_id=None,
            formal_records=[], promoted_review_events=[], repair_overlays=[],
            review_evidence=[], accepted_suggestions={})

    scheduler.submit(snapshot, worker)
    scheduler.wait_for_batch(0, timeout=5.0)
    scheduler.shutdown()
    _assert_released(registry, seen)
