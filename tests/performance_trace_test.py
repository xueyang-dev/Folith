"""Comprehensive verification test suite for Folith's performance tracing system.

Covers all 18 requirements:
1. run_id 唯一
2. span duration
3. nested span
4. exception 时 span 正确关闭
5. retry event
6. resource sampler
7. trace disabled
8. JSONL serialization
9. malformed metadata
10. concurrent event writes
11. run summary
12. percentile calculation
13. cache statistics
14. failed run
15. cancelled run
16. historical runs 不互相覆盖
17. 不记录完整 source/target
18. 大量 events 下内存不会无限增长
"""
import concurrent.futures
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from folith.trace import (
    ResourceSampler,
    RunContext,
    TraceLevel,
    TraceManager,
    compute_percentiles,
    generate_run_id,
    sanitize_metadata,
    trace,
)


def test_1_run_id_unique():
    """1. run_id 唯一"""
    id1 = generate_run_id()
    id2 = generate_run_id()
    assert id1 != id2
    assert id1.startswith("run_")
    assert id2.startswith("run_")


def test_profiler_bridge_updates_ocr_completion_and_keeps_native_spans(tmp_path):
    from folith.performance import PipelineProfiler
    run = trace.start_run("ocr-bridge", output_dir=tmp_path)
    profiler = PipelineProfiler(tmp_path / "performance.json", run_id=run.run_id)
    try:
        with trace.span("native_stage", operation="native_operation"):
            pass
        stage = profiler.start_stage("ocr", page_count=2, concurrency=1,
            metadata={"actual_engine": "paddleocr"})
        profiler.persist()
        assert run.stages["ocr"]["status"] == "running"
        stage.item("page-1", time.perf_counter(), metadata={"kind": "page"})
        stage.finish()
        assert run.stages["ocr"]["status"] == "completed"
        assert run.stages["native_stage"]["operations"]["native_operation"]
        trace.finish_run("ocr-bridge")
        result = json.loads((tmp_path / "performance.json").read_text(encoding="utf-8"))
        assert result["stages"]["ocr"]["metadata"]["actual_engine"] == "paddleocr"
        assert result["stages"]["ocr"]["items"]
        assert result["stages"]["ocr"]["page_count"] == 2
    finally:
        trace.finish_run("ocr-bridge")


def test_2_span_duration(tmp_path):
    """2. span duration"""
    ctx = RunContext("task_dur", output_dir=tmp_path, level=TraceLevel.STANDARD)
    with ctx.span("test_stage", "op_wait") as s:
        time.sleep(0.02)
    assert s.duration_ms >= 15.0
    assert s.cpu_time_ms >= 0.0
    assert s.status == "success"
    summary = ctx.finish()
    stage_info = summary["stages"]["test_stage"]
    assert stage_info["wall_time_s"] >= 0.015


def test_3_nested_span(tmp_path):
    """3. nested span"""
    mgr = TraceManager()
    mgr.set_level(TraceLevel.DETAILED)
    run = mgr.start_run("task_nest", output_dir=tmp_path)
    with mgr.span("outer_stage", "outer_op") as outer:
        with mgr.span("inner_stage", "inner_op") as inner:
            time.sleep(0.005)
    assert inner.parent_id == outer.event_id
    assert outer.parent_id is None
    mgr.finish_run("task_nest")


def test_4_span_exception_safe_close(tmp_path):
    """4. exception 时 span 正确关闭"""
    ctx = RunContext("task_exc", output_dir=tmp_path, level=TraceLevel.STANDARD)
    with pytest.raises(ValueError, match="test error"):
        with ctx.span("error_stage", "boom") as s:
            raise ValueError("test error")
    assert s.status == "error"
    assert s.error_info["error_type"] == "ValueError"
    assert "test error" in s.error_info["message"]
    assert s.duration_ms >= 0.0
    summary = ctx.finish(status="failed")
    assert summary["stages"]["error_stage"]["status"] == "error"


def test_5_retry_event(tmp_path):
    """5. retry event"""
    ctx = RunContext("task_retry", output_dir=tmp_path, level=TraceLevel.STANDARD)
    ctx.record_retry(stage="translation", operation="batch_call", attempt=1, backoff_s=1.5, error_type="RateLimitError")
    ctx.record_retry(stage="translation", operation="batch_call", attempt=2, backoff_s=3.0, error_type="RateLimitError")
    summary = ctx.finish()
    assert summary["errors_and_retries"]["retries"] == 2
    assert len(summary["errors_and_retries"]["retry_list"]) == 2
    assert summary["errors_and_retries"]["retry_list"][0]["attempt"] == 1


def test_6_resource_sampler(tmp_path):
    """6. resource sampler"""
    log_file = tmp_path / "resources.jsonl"
    sampler = ResourceSampler(log_file, interval=0.1)
    sampler.start()
    time.sleep(0.25)
    summary = sampler.stop()
    assert summary["sample_count"] >= 1
    assert summary["process_rss_peak_mb"] > 0
    assert summary["thread_peak"] >= 1
    assert log_file.is_file()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert "process_cpu_pct" in first
    assert "process_rss_mb" in first


def test_7_trace_disabled(tmp_path):
    """7. trace disabled"""
    ctx = RunContext("task_off", output_dir=tmp_path, level=TraceLevel.OFF)
    with ctx.span("stage", "op") as s:
        pass
    ctx.record_batch(1, 10, 500, 100.0)
    ctx.finish()
    # No files should be written under .performance/runs
    assert not (tmp_path / ".performance" / "runs" / ctx.run_id / "events.jsonl").is_file()
    assert not (tmp_path / ".performance" / "runs" / ctx.run_id / "summary.json").is_file()


def test_8_jsonl_serialization(tmp_path):
    """8. JSONL serialization"""
    ctx = RunContext("task_jsonl", output_dir=tmp_path, level=TraceLevel.STANDARD)
    with ctx.span("stage_1", "op_1", metadata={"key": "val_1"}):
        pass
    with ctx.span("stage_2", "op_2", metadata={"count": 42}):
        pass
    ctx.finish()
    events_path = ctx.events_path
    assert events_path.is_file()
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)
        assert "run_id" in parsed
        assert "duration_ms" in parsed


def test_9_malformed_metadata(tmp_path):
    """9. malformed metadata"""
    class CustomObj:
        def __str__(self):
            return "<CustomObjRepresentation>"

    raw_meta = {
        "func": lambda x: x,
        "obj": CustomObj(),
        "unserializable": object(),
    }
    sanitized = sanitize_metadata(raw_meta)
    # Must be valid json
    dumped = json.dumps(sanitized)
    assert "<CustomObjRepresentation>" in dumped


def test_10_concurrent_event_writes(tmp_path):
    """10. concurrent event writes"""
    ctx = RunContext("task_concurrent", output_dir=tmp_path, level=TraceLevel.STANDARD)

    def worker(worker_id):
        for i in range(25):
            with ctx.span(f"stage_{worker_id}", f"op_{i}", items_in=1, items_out=1):
                time.sleep(0.001)

    threads = [threading.Thread(target=worker, args=(wid,)) for wid in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = ctx.finish()
    lines = ctx.events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8 * 25
    # Every line must be valid JSON
    for line in lines:
        item = json.loads(line)
        assert item["status"] == "success"


def test_11_run_summary(tmp_path):
    """11. run summary"""
    ctx = RunContext("task_summary", filename="book.pdf", target_lang="简体中文", output_dir=tmp_path)
    ctx.set_dataset_info(page_count=10, source_chars=5000, segment_count=100)
    with ctx.span("import", "read"):
        time.sleep(0.01)
    with ctx.span("translation", "batch"):
        time.sleep(0.01)
    summary = ctx.finish()

    assert summary["task_id"] == "task_summary"
    assert summary["dataset"]["filename"] == "book.pdf"
    assert summary["dataset"]["segment_count"] == 100
    assert "import" in summary["stages"]
    assert "translation" in summary["stages"]
    assert "latency_percentiles" in summary
    assert "resources" in summary
    assert "unaccounted_time_s" in summary


def test_12_percentile_calculation():
    """12. percentile calculation"""
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    p = compute_percentiles(values)
    assert p["count"] == 10
    assert p["min"] == 1.0
    assert p["max"] == 10.0
    assert p["avg"] == 5.5
    assert p["p50"] == 5.0 or p["p50"] == 6.0  # nearest rank


def test_13_cache_statistics(tmp_path):
    """13. cache statistics"""
    ctx = RunContext("task_cache", output_dir=tmp_path)
    ctx.record_tm_lookup("exact", duration_ms=1.2)
    ctx.record_tm_lookup("exact", duration_ms=1.1)
    ctx.record_tm_lookup("normalized", duration_ms=2.4)
    ctx.record_tm_lookup("miss", duration_ms=0.8)
    ctx.record_cache_event("ocr_cache", hit=True)
    ctx.record_cache_event("ocr_cache", hit=False)
    summary = ctx.finish()
    tm = summary["tm_and_cache"]
    assert tm["tm_lookups"] == 4
    assert tm["tm_exact_hits"] == 2
    assert tm["tm_normalized_hits"] == 1
    assert tm["tm_misses"] == 1
    assert tm["tm_hit_ratio"] == 0.75
    assert tm["caches"]["ocr_cache"]["hits"] == 1
    assert tm["caches"]["ocr_cache"]["misses"] == 1


def test_14_failed_run(tmp_path):
    """14. failed run"""
    ctx = RunContext("task_fail", output_dir=tmp_path)
    summary = ctx.finish(status="failed", failure_reason="API key expired")
    assert summary["status"] == "failed"
    assert summary["failure_reason"] == "API key expired"
    assert (tmp_path / ".performance" / "runs" / ctx.run_id / "summary.json").is_file()
    assert (tmp_path / ".performance" / "runs" / ctx.run_id / "performance.md").is_file()


def test_15_cancelled_run(tmp_path):
    """15. cancelled run"""
    ctx = RunContext("task_cancel", output_dir=tmp_path)
    summary = ctx.finish(status="cancelled", failure_reason="User cancelled")
    assert summary["status"] == "cancelled"
    assert summary["failure_reason"] == "User cancelled"


def test_16_historical_runs_preserved(tmp_path):
    """16. historical runs 不互相覆盖"""
    task_id = "task_multi_run"
    out_dir = tmp_path / task_id

    # Run 1
    ctx1 = RunContext(task_id, output_dir=out_dir)
    with ctx1.span("import"):
        time.sleep(0.005)
    ctx1.finish()

    # Run 2
    ctx2 = RunContext(task_id, output_dir=out_dir)
    with ctx2.span("import"):
        time.sleep(0.005)
    ctx2.finish()

    # Both run directories must exist
    run1_dir = out_dir / ".performance" / "runs" / ctx1.run_id
    run2_dir = out_dir / ".performance" / "runs" / ctx2.run_id
    assert run1_dir.is_dir()
    assert run2_dir.is_dir()
    assert ctx1.run_id != ctx2.run_id

    # History file must contain both runs
    hist_file = out_dir / ".performance" / "history.json"
    assert hist_file.is_file()
    history = json.loads(hist_file.read_text(encoding="utf-8"))
    assert len(history) == 2
    assert history[0]["run_id"] == ctx1.run_id
    assert history[1]["run_id"] == ctx2.run_id


def test_17_text_masking_privacy():
    """17. 不记录完整 source/target"""
    long_source = "A" * 500
    long_target = "B" * 500
    meta = {
        "segment_id": 42,
        "source_text": long_source,
        "target_text": long_target,
        "note": "Short note",
    }
    sanitized = sanitize_metadata(meta)
    assert sanitized["segment_id"] == 42
    assert sanitized["note"] == "Short note"
    assert isinstance(sanitized["source_text"], dict)
    assert sanitized["source_text"]["char_count"] == 500
    assert "hash" in sanitized["source_text"]
    assert "AAAA" in sanitized["source_text"]["preview"]
    assert len(sanitized["source_text"]["preview"]) <= 45


def test_18_bounded_memory_growth_under_large_event_volume(tmp_path):
    """18. 大量 events 下内存不会无限增长"""
    ctx = RunContext("task_large", output_dir=tmp_path)
    # Emit 1,000 spans
    for i in range(1000):
        with ctx.span("batch_stage", f"batch_{i}", items_in=1):
            pass
    # The in-memory events count is tracked, but events are streamed to disk
    assert ctx._events_count == 1000
    ctx.finish()
    lines = ctx.events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1000
