"""Unit and integration tests for ReviewScheduler and Reviewer Overlap Pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core
from folith import context as _context
from folith.performance import PipelineProfiler
from folith.review_scheduler import (
    BatchLifecycleState,
    BatchReviewSnapshot,
    ReviewResult,
    ReviewScheduler,
    compute_overlap_metrics,
)


def _digest(values) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _faithful_target(line: str, suffix: str = "译文") -> str:
    """Keep the source numbers so the deterministic number check stays clean.

    The scheduler tests used to return translations that dropped every number.
    That was fine before the deterministic number check existed; now it would
    (correctly) flag the fixture as incomplete and the ``reviewed_clean``
    assertions would fail.  The fixture is about scheduling, not about dropping
    numbers, so it translates faithfully instead.
    """
    import re

    ordinal = line.split(".", 1)[0].strip()
    numbers = re.findall(r"\d+", line)
    kept = "、".join(numbers) if numbers else "无"
    return f"这是第 {ordinal} 段，保留数字 {kept} 的中文{suffix}。"


def _make_dummy_snapshot(batch_index: int, offset: int = 0, count: int = 2) -> BatchReviewSnapshot:
    batch_pairs = [
        {
            "source": f"Source sentence {offset + i}",
            "target": f"Target translation {offset + i}",
            "initial_target": f"Target translation {offset + i}",
            "from_tm": False,
        }
        for i in range(count)
    ]
    return BatchReviewSnapshot.create(
        batch_index=batch_index,
        offset=offset,
        batch_pairs=batch_pairs,
        glossary=[],
        glossary_text="",
        glossary_ids=[],
        glossary_hash="",
        style_rules="",
        target_lang="简体中文",
        section_profile={},
        deterministic_findings=[],
        blocked_local_indexes=set(),
        review_context={},
        formal_segment_ids=list(range(offset, offset + count)),
        evidence_index=None,
        formal_packet={},
        translator_model="test-model",
    )


def test_scheduler_lifecycle_and_in_flight():
    scheduler = ReviewScheduler(max_in_flight=2, reviewer_workers=1)
    snapshot = _make_dummy_snapshot(0)
    assert scheduler.in_flight_count() == 0

    def worker(snap):
        time.sleep(0.05)
        return ReviewResult(
            batch_index=snap.batch_index,
            offset=snap.offset,
            review_succeeded=True,
            review_failed=False,
            rfindings=[],
            review_trace=None,
            review_event_id=None,
            formal_records=[],
            promoted_review_events=[],
            repair_overlays=[],
            review_evidence=[],
            accepted_suggestions={},
        )

    scheduler.submit(snapshot, worker)
    assert scheduler.in_flight_count() <= 1
    res = scheduler.wait_for_batch(0, timeout=2.0)
    assert res.batch_index == 0
    assert res.review_succeeded is True
    assert scheduler.in_flight_count() == 0
    scheduler.shutdown()


def test_scheduler_backpressure_bounding():
    """Verify that submit blocks when in_flight reaches max_in_flight."""
    scheduler = ReviewScheduler(max_in_flight=2, reviewer_workers=2)
    release_event = threading.Event()

    def slow_worker(snap):
        release_event.wait(timeout=5.0)
        return ReviewResult(
            batch_index=snap.batch_index,
            offset=snap.offset,
            review_succeeded=True,
            review_failed=False,
            rfindings=[],
            review_trace=None,
            review_event_id=None,
            formal_records=[],
            promoted_review_events=[],
            repair_overlays=[],
            review_evidence=[],
            accepted_suggestions={},
        )

    scheduler.submit(_make_dummy_snapshot(0), slow_worker)
    scheduler.submit(_make_dummy_snapshot(1), slow_worker)
    assert scheduler.in_flight_count() == 2

    # Attempting to submit 3rd batch should block
    submitted_third = threading.Event()

    def submit_third():
        scheduler.submit(_make_dummy_snapshot(2), slow_worker)
        submitted_third.set()

    t = threading.Thread(target=submit_third)
    t.start()
    time.sleep(0.05)
    assert not submitted_third.is_set(), "Third submit should be blocked by backpressure"

    # Release workers
    release_event.set()
    res0 = scheduler.wait_for_batch(0, timeout=2.0)
    res1 = scheduler.wait_for_batch(1, timeout=2.0)
    t.join(timeout=2.0)
    assert submitted_third.is_set(), "Third submit should unblock once slot is freed"
    res2 = scheduler.wait_for_batch(2, timeout=2.0)
    assert res2.batch_index == 2
    scheduler.shutdown()


def test_scheduler_cancellation():
    """Verify that cancellation unblocks waiting threads and rejects further submits."""
    scheduler = ReviewScheduler(max_in_flight=1, reviewer_workers=1)
    hang_event = threading.Event()

    def hanging_worker(snap):
        hang_event.wait(timeout=5.0)
        return ReviewResult(
            batch_index=snap.batch_index,
            offset=snap.offset,
            review_succeeded=True,
            review_failed=False,
            rfindings=[],
            review_trace=None,
            review_event_id=None,
            formal_records=[],
            promoted_review_events=[],
            repair_overlays=[],
            review_evidence=[],
            accepted_suggestions={},
        )

    scheduler.submit(_make_dummy_snapshot(0), hanging_worker)

    failed_with_cancel = threading.Event()

    def wait_task():
        try:
            scheduler.wait_for_batch(0)
        except RuntimeError as exc:
            if "取消" in str(exc):
                failed_with_cancel.set()

    t = threading.Thread(target=wait_task)
    t.start()
    time.sleep(0.05)
    scheduler.cancel()
    t.join(timeout=2.0)
    assert failed_with_cancel.is_set()

    with pytest.raises(RuntimeError, match="取消"):
        scheduler.submit(_make_dummy_snapshot(1), hanging_worker)

    hang_event.set()
    scheduler.shutdown()


def test_scheduler_error_propagation():
    """Verify that exceptions raised in reviewer worker are re-raised in wait_for_batch."""
    scheduler = ReviewScheduler(max_in_flight=2, reviewer_workers=1)

    def faulty_worker(snap):
        raise ValueError("Simulated network outage in reviewer")

    scheduler.submit(_make_dummy_snapshot(0), faulty_worker)
    with pytest.raises(ValueError, match="Simulated network outage"):
        scheduler.wait_for_batch(0, timeout=2.0)
    scheduler.shutdown()


def test_compute_overlap_metrics():
    stats = {
        "total_submitted": 5,
        "total_completed": 5,
        "total_queue_wait_s": 0.05,
        "total_review_wall_s": 2.5,
        "total_api_latency_s": 2.4,
        "hidden_review_seconds": 1.8,
        "exposed_review_seconds": 0.7,
        "translator_idle_s": 0.3,
        "reviewer_idle_s": 0.1,
        "max_backlog": 2,
    }
    metrics = compute_overlap_metrics(stats)
    assert metrics["total_review_seconds"] == 2.5
    assert metrics["hidden_review_seconds"] == 1.8
    assert metrics["exposed_review_seconds"] == 0.7
    assert metrics["hidden_review_ratio"] == 0.72
    assert metrics["translator_idle_seconds"] == 0.3
    assert metrics["max_review_backlog"] == 2


def test_e2e_translation_equivalence_sequential_vs_overlap(monkeypatch):
    """Verify that Reviewer Overlap produces 100% equivalent translation invariants.

    Invariants checked:
    - total pairs count
    - translation_digest (exact match)
    - findings_digest (exact match)
    - tm_digest (exact match)
    - review status on all pairs
    """
    tmp_seq = Path(tempfile.mkdtemp(prefix="ft-seq-"))
    tmp_ovl = Path(tempfile.mkdtemp(prefix="ft-ovl-"))

    def fake_llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        if "独立的翻译审校专家" in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []}, ensure_ascii=False)
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = []
        for line in lines:
            ordinal = line.split(".", 1)[0].strip()
            results.append(_faithful_target(line, "标准译文"))
        return json.dumps(results, ensure_ascii=False)

    old_output_dir = core.OUTPUT_DIR
    try:
        monkeypatch.setattr(core, "call_llm", fake_llm)
        sources = [
            f"Paragraph {i}: The quick brown fox jumps over the lazy dog in session {i // 2}."
            for i in range(8)
        ]

        # 1. Run Sequential baseline (review_overlap=False)
        core.OUTPUT_DIR = tmp_seq
        state_seq = {
            "paras": list(sources),
            "pairs": [],
            "findings": [],
            "pipeline_config": {
                "batch_size": 2,
                "review_overlap": False,
            },
        }
        core.save_job_state("seq-job", state_seq)
        core.translate_stage(
            state_seq, "seq-job", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=True, batch_size=2, translation_concurrency=1,
            review_overlap=False,
        )

        # 2. Run Overlap pipeline (review_overlap=True)
        core.OUTPUT_DIR = tmp_ovl
        state_ovl = {
            "paras": list(sources),
            "pairs": [],
            "findings": [],
            "pipeline_config": {
                "batch_size": 2,
                "review_overlap": True,
            },
        }
        core.save_job_state("ovl-job", state_ovl)
        core.translate_stage(
            state_ovl, "ovl-job", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=True, batch_size=2, translation_concurrency=1,
            review_overlap=True, max_review_in_flight=2,
        )

        # 3. Invariant assertions
        assert len(state_seq["pairs"]) == len(sources)
        assert len(state_ovl["pairs"]) == len(sources)

        seq_targets = [p["target"] for p in state_seq["pairs"]]
        ovl_targets = [p["target"] for p in state_ovl["pairs"]]
        assert seq_targets == ovl_targets, "Target translations must be 100% identical"
        assert _digest(seq_targets) == _digest(ovl_targets)

        seq_statuses = [p["review_status"] for p in state_seq["pairs"]]
        ovl_statuses = [p["review_status"] for p in state_ovl["pairs"]]
        assert seq_statuses == ovl_statuses, "Review statuses must be 100% identical"
        assert all(s == "reviewed_clean" for s in ovl_statuses)

        seq_findings = [f.get("summary") for f in state_seq["findings"]]
        ovl_findings = [f.get("summary") for f in state_ovl["findings"]]
        assert seq_findings == ovl_findings

        # TM equivalence
        tm_seq = core.load_tm()
        core.OUTPUT_DIR = tmp_ovl
        tm_ovl = core.load_tm()
        assert len(tm_seq) == len(tm_ovl)
        assert _digest(sorted(tm_seq.keys())) == _digest(sorted(tm_ovl.keys()))

        # Overlap metrics recorded
        assert "review_overlap" in state_ovl
        overlap_info = state_ovl["review_overlap"]
        assert overlap_info["total_submitted"] == 4
        assert overlap_info["total_completed"] == 4
        assert overlap_info["max_review_backlog"] >= 1

    finally:
        core.OUTPUT_DIR = old_output_dir


def test_overlap_with_simulated_latency(monkeypatch):
    """Verify that review latency is hidden behind translation generation."""
    tmp = Path(tempfile.mkdtemp(prefix="ft-lat-"))
    old_output_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp

    GEN_LATENCY = 0.08
    REV_LATENCY = 0.08

    def simulated_llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        if "独立的翻译审校专家" in (system_prompt or ""):
            time.sleep(REV_LATENCY)
            return json.dumps({"findings": [], "evidence_requests": []}, ensure_ascii=False)
        time.sleep(GEN_LATENCY)
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = [f"译文 {line}" for line in lines]
        return json.dumps(results, ensure_ascii=False)

    try:
        monkeypatch.setattr(core, "call_llm", simulated_llm)
        sources = [f"Paragraph {i}: Testing overlap latency." for i in range(8)]  # 4 batches of size 2

        # Sequential run
        state_seq = {"paras": list(sources), "pairs": [], "findings": []}
        core.save_job_state("seq-lat", state_seq)
        t0 = time.perf_counter()
        core.translate_stage(
            state_seq, "seq-lat", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=False, batch_size=2, translation_concurrency=1,
            review_overlap=False,
        )
        seq_wall = time.perf_counter() - t0

        # Overlap run
        state_ovl = {"paras": list(sources), "pairs": [], "findings": []}
        core.save_job_state("ovl-lat", state_ovl)
        t1 = time.perf_counter()
        core.translate_stage(
            state_ovl, "ovl-lat", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=False, batch_size=2, translation_concurrency=1,
            review_overlap=True, max_review_in_flight=2,
        )
        ovl_wall = time.perf_counter() - t1

        # Assertions
        assert ovl_wall < seq_wall, f"Overlap wall time ({ovl_wall:.3f}s) should be lower than sequential ({seq_wall:.3f}s)"
        savings = (seq_wall - ovl_wall) / seq_wall
        metrics = state_ovl["review_overlap"]
        assert metrics["hidden_review_seconds"] > 0, "Should have measured hidden review time"
        assert metrics["hidden_review_ratio"] > 0.40, f"Expected > 40% hidden ratio, got {metrics['hidden_review_ratio']}"

    finally:
        core.OUTPUT_DIR = old_output_dir


def test_ordered_commit_barrier_with_out_of_order_completion():
    """Verify that commits occur in strict document batch order even if reviews finish out of order."""
    scheduler = ReviewScheduler(max_in_flight=4, reviewer_workers=2)
    commit_order = []

    def out_of_order_worker(snap):
        # Batch 0 takes 0.1s; Batch 1 takes 0.01s
        if snap.batch_index == 0:
            time.sleep(0.08)
        else:
            time.sleep(0.01)
        return ReviewResult(
            batch_index=snap.batch_index,
            offset=snap.offset,
            review_succeeded=True,
            review_failed=False,
            rfindings=[],
            review_trace=None,
            review_event_id=None,
            formal_records=[],
            promoted_review_events=[],
            repair_overlays=[],
            review_evidence=[],
            accepted_suggestions={},
        )

    scheduler.submit(_make_dummy_snapshot(0), out_of_order_worker)
    scheduler.submit(_make_dummy_snapshot(1), out_of_order_worker)

    # In consumer: drain strictly by index
    for expected_bi in (0, 1):
        res = scheduler.wait_for_batch(expected_bi)
        commit_order.append(res.batch_index)

    assert commit_order == [0, 1], f"Expected commits in order [0, 1], got {commit_order}"
    scheduler.shutdown()


def test_strategy_b_stable_barrier(monkeypatch):
    """Verify that Strategy B (stable) runs successfully with ordered barriers."""
    tmp = Path(tempfile.mkdtemp(prefix="ft-stratb-"))
    old_output_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp

    def fake_llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        if "独立的翻译审校专家" in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []}, ensure_ascii=False)
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        results = [_faithful_target(line, "稳定译文") for line in lines]
        return json.dumps(results, ensure_ascii=False)

    try:
        monkeypatch.setattr(core, "call_llm", fake_llm)
        sources = [f"Section {i} content." for i in range(6)]
        state = {"paras": list(sources), "pairs": [], "findings": []}
        core.save_job_state("stratb-job", state)
        core.translate_stage(
            state, "stratb-job", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=False, batch_size=2, translation_concurrency=1,
            review_overlap=True, review_overlap_strategy="stable",
        )
        assert len(state["pairs"]) == 6
        assert all(p["review_status"] == "reviewed_clean" for p in state["pairs"])
        assert state["batch_plan"]["review_overlap_strategy"] == "stable"

    finally:
        core.OUTPUT_DIR = old_output_dir


def test_crash_recovery_resumption_with_overlap(monkeypatch):
    """Verify that a job interrupted mid-stage resumes correctly with Reviewer Overlap."""
    tmp = Path(tempfile.mkdtemp(prefix="ft-res-"))
    old_output_dir = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp

    translated_batches = []

    def recording_llm(provider, api_key, model, system_prompt, user_prompt, temperature=0.1):
        if "独立的翻译审校专家" in (system_prompt or ""):
            return json.dumps({"findings": [], "evidence_requests": []}, ensure_ascii=False)
        lines = [line.strip() for line in user_prompt.splitlines() if line.strip() and line[0].isdigit()]
        first_line = lines[0] if lines else ""
        translated_batches.append(first_line)
        results = [_faithful_target(line, "恢复译文") for line in lines]
        return json.dumps(results, ensure_ascii=False)

    try:
        monkeypatch.setattr(core, "call_llm", recording_llm)
        sources = [f"Paragraph {i}: Content." for i in range(6)]  # 3 batches of size 2

        # 1. Pre-populate first batch (2 segments) as already committed
        state = {
            "paras": list(sources),
            "pairs": [
                {
                    "source": sources[0], "target": "这是第 1 段，保留数字 0 的恢复译文。",
                    "initial_target": "这是第 1 段，保留数字 0 的恢复译文。",
                    "accepted_target": "这是第 1 段，保留数字 0 的恢复译文。",
                    "reviewed": True, "review_status": "reviewed_clean",
                    "target_provenance": "reviewed", "from_tm": False,
                },
                {
                    "source": sources[1], "target": "这是第 2 段，保留数字 1 的恢复译文。",
                    "initial_target": "这是第 2 段，保留数字 1 的恢复译文。",
                    "accepted_target": "这是第 2 段，保留数字 1 的恢复译文。",
                    "reviewed": True, "review_status": "reviewed_clean",
                    "target_provenance": "reviewed", "from_tm": False,
                },
            ],
            "findings": [],
        }
        core.save_job_state("resume-job", state)

        # 2. Run translation stage with review_overlap=True
        core.translate_stage(
            state, "resume-job", glossary=[], provider="test", api_key="k",
            model="m", target_lang="简体中文", style_rules="", enable_review=True,
            use_tm=False, batch_size=2, translation_concurrency=1,
            review_overlap=True,
        )

        assert len(state["pairs"]) == 6
        assert all(p["reviewed"] for p in state["pairs"])
        # Notice: only batches 1 and 2 (segments 2-3 and 4-5) should have been generated by Translator LLM
        generation_calls = [b for b in translated_batches if b]
        assert len(generation_calls) == 2

    finally:
        core.OUTPUT_DIR = old_output_dir
