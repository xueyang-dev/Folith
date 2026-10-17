"""Tests for job state signature cache, caller mutation isolation, and summary read model."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
import pytest

import core
from folith.storage import job_repository as repo


@pytest.fixture
def temp_job_env(tmp_path: Path):
    old_out = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp_path
    repo.invalidate_cache()
    repo.reset_cache_stats()
    try:
        yield tmp_path
    finally:
        core.OUTPUT_DIR = old_out
        repo.invalidate_cache()


def test_signature_cache_hits_on_unchanged_file(temp_job_env: Path):
    job_id = "test-job-sig"
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": "Hello", "target": "你好"}]
    core.save_job_state(job_id, state)

    repo.reset_cache_stats()

    # First load: cache miss, disk read
    s1 = core.load_job_state(job_id)
    assert s1 is not None
    assert s1["pairs"][0]["target"] == "你好"
    stats1 = repo.get_cache_stats()
    assert stats1["cache_misses"] == 1
    assert stats1["cache_hits"] == 0
    assert stats1["disk_reads"] == 1

    # Second load: same signature -> cache hit! ZERO disk reads
    s2 = core.load_job_state(job_id)
    assert s2 is not None
    assert s2["pairs"][0]["target"] == "你好"
    stats2 = repo.get_cache_stats()
    assert stats2["cache_hits"] == 1
    assert stats2["disk_reads"] == 1


def test_signature_cache_invalidates_on_atomic_replace(temp_job_env: Path):
    job_id = "test-job-atomic"
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": "Hello", "target": "你好"}]
    core.save_job_state(job_id, state)

    s1 = core.load_job_state(job_id)
    assert s1["pairs"][0]["target"] == "你好"

    # Simulate worker updating state.json via atomic replace
    time.sleep(0.01)  # Ensure mtime resolution
    p = core.job_state_path(job_id)
    tmp = p.with_suffix(".tmp")
    state["pairs"][0]["target"] = "您好（更新）"
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)

    # Next load must detect new signature and re-read updated state
    s2 = core.load_job_state(job_id)
    assert s2 is not None
    assert s2["pairs"][0]["target"] == "您好（更新）"


def test_cached_state_is_immune_to_caller_mutations(temp_job_env: Path):
    job_id = "test-job-mutation"
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": "Original", "target": "原始译文"}]
    core.save_job_state(job_id, state)

    # First load
    s1 = core.load_job_state(job_id)
    assert s1["pairs"][0]["target"] == "原始译文"

    # Caller aggressively mutates the returned dictionary
    s1["pairs"][0]["target"] = "被污染的译文"
    s1["new_injected_key"] = "hacked"

    # Second load must return pristine copy, not contaminated by s1
    s2 = core.load_job_state(job_id)
    assert s2["pairs"][0]["target"] == "原始译文"
    assert "new_injected_key" not in s2


def test_summary_read_model_saved_on_mutation(temp_job_env: Path):
    job_id = "test-job-summary"
    state = core.new_job_state("book.docx")
    state["pairs"] = [
        {"source": "S1", "target": "T1"},
        {"source": "S2", "target": "T2"},
    ]
    core.save_job_state(job_id, state)

    summary_file = core.OUTPUT_DIR / job_id / "summary.json"
    assert summary_file.is_file(), "save_job_state must derive and write summary.json"

    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert data["job_id"] == job_id
    assert data["filename"] == "book.docx"
    assert data["segment_count"] == 2


def test_read_path_has_strictly_no_write_side_effects(temp_job_env: Path):
    job_id = "test-job-no-side-effect"
    state = core.new_job_state("legacy.docx")
    state["pairs"] = [{"source": "S1", "target": "T1"}]

    # Write state.json manually WITHOUT summary.json (legacy job simulation)
    d = core.job_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    summary_file = d / "summary.json"
    assert not summary_file.exists()

    # Calling list_job_summaries must return the fallback summary
    summaries = core.list_job_summaries()
    assert len(summaries) == 1
    assert summaries[0]["job_id"] == job_id
    assert summaries[0]["filename"] == "legacy.docx"

    # STRICT INVARIANT: summary.json must NOT be created on disk during read path!
    assert not summary_file.exists(), "Read path must NOT have write side-effects!"


def test_delete_job_invalidates_cache(temp_job_env: Path):
    job_id = "test-job-del"
    state = core.new_job_state("to_delete.docx")
    core.save_job_state(job_id, state)

    s1 = core.load_job_state(job_id)
    assert s1 is not None

    core.delete_job(job_id)
    assert core.load_job_state(job_id) is None


def test_read_runtime_view_is_lightweight_and_reads_no_full_state(temp_job_env: Path):
    job_id = "test-job-runtime-view"
    state = core.new_job_state("runtime_test.docx")
    state["pairs"] = [{"source": f"S{i}", "target": f"T{i}"} for i in range(50)]
    core.save_job_state(job_id, state)

    # Save a runtime state
    core.update_runtime_state(
        job_id, status="running", phase_label="段落翻译中",
        completed_units=15, total_units=50, operation_label="翻译",
        heartbeat=True, worker={"owner_pid": os.getpid(), "worker_id": "test-worker"})

    repo.reset_cache_stats()

    # read_runtime_view should NOT load full state if summary exists
    view = core.read_runtime_view(job_id)
    assert view["status"] == "running"
    assert view["progress_done"] == 15
    assert view["progress_total"] == 50
    assert view["stage_label"] in {"translation", "段落翻译中"}
    assert view["state_revision"] > 0
    # Verified: zero disk reads of state.json!
    stats = repo.get_cache_stats()
    assert stats["disk_reads"] == 0, "read_runtime_view must not load state.json when summary exists"


def test_list_job_summaries_efficiency_with_many_jobs(temp_job_env: Path):
    # Seed 20 jobs with summaries
    for i in range(20):
        jid = f"job-{i:03d}"
        st = core.new_job_state(f"doc_{i}.docx")
        st["pairs"] = [{"source": "A", "target": "B"}]
        core.save_job_state(jid, st)

    repo.reset_cache_stats()

    # list_job_summaries should read summaries directly with 0 state.json disk reads
    summaries = core.list_job_summaries()
    assert len(summaries) == 20
    stats = repo.get_cache_stats()
    assert stats["disk_reads"] == 0, "list_job_summaries must read summary.json without reading state.json"


def test_saved_jobs_snapshot_semantics(temp_job_env: Path, monkeypatch):
    """Invariant: saved_jobs_snapshot scans once per run without mutation, and refreshes on mutation without rerun."""
    scan_count = 0
    orig_list_jobs = core.list_jobs

    def counted_list_jobs(*args, **kwargs):
        nonlocal scan_count
        scan_count += 1
        return orig_list_jobs(*args, **kwargs)

    monkeypatch.setattr(core, "list_jobs", counted_list_jobs)

    # Initial scan creates single snapshot
    snapshot = core.list_jobs()
    assert scan_count == 1
    saved_jobs = snapshot

    # Without mutation, multiple consumers reuse snapshot without secondary scans
    consumer_a = saved_jobs
    consumer_b = saved_jobs
    assert consumer_a is consumer_b
    assert scan_count == 1, "Snapshot must be reused across consumers without duplicate disk scanning"

    # Simulate collection mutation without immediate rerun
    core.save_job_state("job-new", core.new_job_state("doc.docx"))
    # Snapshot must be explicitly refreshed to reflect collection mutation
    snapshot = core.list_jobs()
    assert scan_count == 2
    assert any(j["job_id"] == "job-new" for j in snapshot)


def test_summary_and_runtime_overlay_live_status(temp_job_env: Path):
    """Invariant: Static summary.json is overlaid with live runtime.json truth."""
    job_id = "job-overlay-test"
    state = core.new_job_state("paper.pdf")
    state["stage"] = "TRANSLATING"
    core.save_job_state(job_id, state)

    import os
    # Worker starts running in background and updates runtime.json
    core.update_runtime_state(
        job_id,
        status="running",
        phase_label="正在翻译段落",
        operation_label="正文翻译",
        completed_units=10,
        total_units=50,
        heartbeat=True,
        worker={"owner_pid": os.getpid(), "worker_id": "test-worker"},
    )

    # build_job_runtime_view overlays live runtime status onto static state
    view = core.build_job_runtime_view(job_id, state)
    assert view["status"] == "running", "Live runtime status ('running') must overlay static status"
    assert view["progress_completed"] == 10
    assert view["progress_total"] == 50

    # read_runtime_view also reflects live running status
    rt_view = core.read_runtime_view(job_id)
    assert rt_view["status"] == "running"
    assert rt_view["progress_done"] == 10


def test_state_cache_keeps_one_entry_per_job(temp_job_env: Path):
    """A changed file signature replaces the job's cache entry, not adds one."""
    job_id = "test-job-one-entry"
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": "Hello", "target": "你好"}]
    core.save_job_state(job_id, state)
    other = "test-job-other"
    core.save_job_state(other, core.new_job_state("other.docx"))
    core.load_job_state(other)

    path = repo.get_job_state_path(job_id)
    for padding in range(1, 4):
        # Change the signature behind the cache's back (no save, no invalidate).
        path.write_text(path.read_text(encoding="utf-8") + " " * padding, encoding="utf-8")
        assert core.load_job_state(job_id)["pairs"][0]["target"] == "你好"

    keys = [k for k in repo._STATE_CACHE if k[0] == job_id]
    assert len(keys) == 1
    assert keys[0][2] == path.stat().st_size
    assert [k for k in repo._STATE_CACHE if k[0] == other]
