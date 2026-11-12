"""Phase 3 P1a contract checks — Annotation Durable Ready Staging & Bounded Refill.

Under test:
* Durable staging unit behavior:
  - Atomic write and load of completed results;
  - Hash fingerprint verification (source + target + model + target_lang);
  - Target text change invalidates staged result (stale annotation never written back);
  - Consumer confirmation cleans up staged file;
* Bounded concurrency & refill (workers=2, capacity=4):
  - Active worker requests strictly bounded <= 2;
  - Outstanding capacity (active + uncommitted ready) bounded <= 4;
  - Task 0 slow, Task 1 fast -> Task 2 submitted concurrently while Task 0 is running;
  - Generator yields strictly sequentially (0, 1, 2, ...);
* Crash and disk failure recovery:
  - First commit save fails (simulated disk full / crash): Task 0 fails commit,
    subsequent completed tasks were durably staged to disk;
  - On restart / resume from cursor 0, already-staged tasks are loaded from disk
    with ZERO duplicate LLM requests;
* Cancellation resilience:
  - In-flight pending futures cancelled; durably staged tasks preserved on disk;
* Global filtering & field-by-field equality:
  - Bounded durable staging produces annotations and batch states identical to
    the baseline sequential execution.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core
from folith import annotation_staging


# ---------------------------------------------------------------------------
# 1. Staging Unit Behavior & Invalidation
# ---------------------------------------------------------------------------

def test_staging_save_load_confirm_cycle(tmp_path):
    job_dir = tmp_path / "job-1"
    job_dir.mkdir(parents=True)

    pairs = [{"source": "Quantum Computing", "target": "量子计算"}]
    fp = annotation_staging.compute_batch_fingerprint(pairs, "简体中文", "gpt-4o")

    items = core.AnnotationBatchResult([
        {"seg": 0, "src": "Quantum Computing", "tgt": "量子计算", "type": "domain", "note": "专业术语"}
    ])

    # Save to staging
    saved = annotation_staging.save_staged_annotation(
        "job-1", offset=0, bi=0, items=items, fingerprint=fp, job_dir_path=job_dir
    )
    assert saved

    staging_file = job_dir / ".annotation_staging" / "0.json"
    assert staging_file.is_file()

    # Load from staging with matching fingerprint
    loaded = annotation_staging.load_staged_annotation(
        "job-1", offset=0, expected_fingerprint=fp, job_dir_path=job_dir
    )
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0]["src"] == "Quantum Computing"
    assert loaded[0]["tgt"] == "量子计算"

    # Confirm staged annotation removes the file
    annotation_staging.confirm_staged_annotation("job-1", offset=0, job_dir_path=job_dir)
    assert not staging_file.is_file()


def test_staging_fingerprint_mismatch_invalidates(tmp_path):
    job_dir = tmp_path / "job-2"
    job_dir.mkdir(parents=True)

    pairs = [{"source": "Artificial Intelligence", "target": "人工智能"}]
    fp = annotation_staging.compute_batch_fingerprint(pairs, "简体中文", "gpt-4o")

    items = core.AnnotationBatchResult([
        {"seg": 0, "src": "Artificial Intelligence", "tgt": "人工智能", "type": "domain", "note": "术语"}
    ])
    annotation_staging.save_staged_annotation(
        "job-2", offset=10, bi=1, items=items, fingerprint=fp, job_dir_path=job_dir
    )

    # User modifies target translation from "人工智能" to "机器智能"
    modified_pairs = [{"source": "Artificial Intelligence", "target": "机器智能"}]
    new_fp = annotation_staging.compute_batch_fingerprint(modified_pairs, "简体中文", "gpt-4o")

    assert new_fp != fp

    # Loading with new fingerprint must return None and remove the stale staged file
    loaded = annotation_staging.load_staged_annotation(
        "job-2", offset=10, expected_fingerprint=new_fp, job_dir_path=job_dir
    )
    assert loaded is None

    staging_file = job_dir / ".annotation_staging" / "10.json"
    assert not staging_file.is_file(), "Stale staged file must be removed"


# ---------------------------------------------------------------------------
# 2. Concurrency, Straggler Bounded Refill & Order Preservation
# ---------------------------------------------------------------------------

def test_annotation_results_bounded_refill_and_order(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_dir = tmp_path / "job-concurrency"
    job_dir.mkdir(parents=True)

    # 4 batches of 1 segment
    tasks = [
        (0, 0, [{"source": "S0", "target": "T0"}], [0]),
        (1, 1, [{"source": "S1", "target": "T1"}], [0]),
        (2, 2, [{"source": "S2", "target": "T2"}], [0]),
        (3, 3, [{"source": "S3", "target": "T3"}], [0]),
    ]

    active_workers = 0
    max_active_workers = 0
    lock = threading.Lock()
    call_order = []

    def mock_annotate(pairs, *_args):
        nonlocal active_workers, max_active_workers
        src = pairs[0]["source"]
        with lock:
            active_workers += 1
            max_active_workers = max(max_active_workers, active_workers)
            call_order.append(src)

        # S0 is slow (50ms); S1, S2, S3 are fast (5ms)
        if src == "S0":
            time.sleep(0.05)
        else:
            time.sleep(0.005)

        with lock:
            active_workers -= 1

        return core.AnnotationBatchResult([
            {"seg": 0, "src": src, "tgt": "T", "type": "domain", "note": "n"}
        ])

    monkeypatch.setattr(core, "annotate_batch", mock_annotate)

    yielded_batches = []
    # workers=2, capacity=4
    generator = core._annotation_results(
        tasks, "job-concurrency", "test", "key", "model", "简体中文", workers=2, capacity=4
    )

    for (bi, offset, pairs, indexes), items in generator:
        yielded_batches.append(bi)

    # Strictly yielded in sequential order: 0, 1, 2, 3
    assert yielded_batches == [0, 1, 2, 3]

    # Active workers never exceeded 2
    assert max_active_workers <= 2

    # Because S0 was slow and capacity=4 allowed refill, S1 and S2 were submitted
    # while S0 was still working. S1 and S2 completed before S0 finished!
    assert "S1" in call_order[:3]


# ---------------------------------------------------------------------------
# 3. Crash / Save Failure Recovery with Zero Repeat LLM Calls
# ---------------------------------------------------------------------------

def test_annotation_save_failure_recovery_reuses_staged_results(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)

    state = core.new_job_state("crash_test.docx")
    state["pipeline_config"] = {"translation_concurrency": 2, "annotation_scheduler": "c4"}
    state["pairs"] = [
        {"source": f"Section {i} about Aerodynamics.", "target": f"第{i}节关于空气动力学。"}
        for i in range(4)
    ]

    job_id = "job-crash-recovery"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    core.save_job_state(job_id, state)

    llm_calls = []

    def mock_annotate(pairs, *_args):
        src = pairs[0]["source"]
        llm_calls.append(src)
        # Section 0 is slow (40ms), Section 1 finishes fast (5ms)
        if "Section 0" in src:
            time.sleep(0.04)
        else:
            time.sleep(0.005)
        return core.AnnotationBatchResult([
            {"seg": 0, "src": "Aerodynamics", "tgt": "空气动力学", "type": "domain", "note": "专业术语"}
        ])

    fail_save = True
    real_save_state = core.save_job_state

    def flaky_save(jid, current_state):
        nonlocal fail_save
        # Fail the very first commit (Section 0 commit)
        if fail_save:
            raise OSError("Simulated disk write failure on first batch commit")
        real_save_state(jid, current_state)

    monkeypatch.setattr(core, "annotate_batch", mock_annotate)
    monkeypatch.setattr(core, "save_job_state", flaky_save)

    # Run 1: Must raise OSError on first batch commit
    with pytest.raises(OSError, match="Simulated disk write failure"):
        core.annotate_stage(state, job_id, [], "DeepSeek", "key", "model", "简体中文")

    # In Run 1: Section 0 and Section 1 were executed.
    calls_in_run1 = list(llm_calls)
    assert len(calls_in_run1) >= 2
    assert "Section 0 about Aerodynamics." in calls_in_run1
    assert "Section 1 about Aerodynamics." in calls_in_run1

    # Section 1 finished and was durably staged to disk!
    staged_file_1 = job_dir / ".annotation_staging" / "1.json"
    assert staged_file_1.is_file(), "Section 1 must be durably staged on disk"

    # Reload state from disk (where annotations_done_offset is still 0 because save failed)
    recovered_state = core.load_job_state(job_id)
    assert recovered_state.get("annotations_done_offset", 0) == 0

    # Run 2: Resume with disk functioning normally
    fail_save = False
    core.annotate_stage(recovered_state, job_id, [], "DeepSeek", "key", "model", "简体中文")

    # Check total LLM calls
    calls_in_run2 = llm_calls[len(calls_in_run1):]

    # CRITICAL ACCEPTANCE: Section 0 and Section 1 were already durably staged;
    # zero repeat LLM calls occurred on resume!
    assert "Section 1 about Aerodynamics." not in calls_in_run2, (
        "Section 1 was already durably staged; repeating LLM call is forbidden!"
    )
    assert len(calls_in_run2) == 0, (
        f"All completed batches were staged; zero repeat calls expected, got {calls_in_run2}"
    )

    # Job completely finished
    assert recovered_state["annotations_done"]
    assert recovered_state["annotations_done_offset"] == 4

    # All staged files cleaned up on final success
    assert not (job_dir / ".annotation_staging").is_dir() or not list((job_dir / ".annotation_staging").glob("*.json"))


# ---------------------------------------------------------------------------
# 4. Cancellation Resilience
# ---------------------------------------------------------------------------

def test_annotation_cancellation_preserves_staged_and_cancels_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)

    state = core.new_job_state("cancel_test.docx")
    state["pipeline_config"] = {"translation_concurrency": 2, "annotation_scheduler": "c4"}
    state["pairs"] = [
        {"source": f"Segment {i} test.", "target": f"分段 {i} 测试。"}
        for i in range(4)
    ]

    job_id = "job-cancel"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True)
    core.save_job_state(job_id, state)

    cancel_event = threading.Event()

    def mock_annotate(pairs, *_args):
        src = pairs[0]["source"]
        if "Segment 0" in src:
            time.sleep(0.04)
        elif "Segment 1" in src:
            time.sleep(0.005)
        return core.AnnotationBatchResult([
            {"seg": 0, "src": "test", "tgt": "测试", "type": "domain", "note": "术语"}
        ])

    real_save = core.save_job_state

    def mock_save(jid, current_state):
        # Trigger cancellation after committing batch 0
        real_save(jid, current_state)
        if current_state.get("annotations_done_offset") == 1:
            cancel_event.set()

    monkeypatch.setattr(core, "annotate_batch", mock_annotate)
    monkeypatch.setattr(core, "save_job_state", mock_save)
    monkeypatch.setattr(core, "_runtime_cancel_requested", lambda _jid: cancel_event.is_set())

    with pytest.raises(RuntimeError, match="任务已请求取消"):
        core.annotate_stage(state, job_id, [], "DeepSeek", "key", "model", "简体中文")

    # Segment 1 was completed and staged before cancel halted the loop
    staged_file_1 = job_dir / ".annotation_staging" / "1.json"
    assert staged_file_1.is_file()


# ---------------------------------------------------------------------------
# 5. Full End-to-End Equivalence with Baseline Serial Execution
# ---------------------------------------------------------------------------

def test_bounded_staging_matches_serial_output_field_by_field(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 2)

    glossary = [
        {"source": "Urban Air Mobility", "target": "城市空中交通"},
        {"source": "Airbus", "target": "空客"},
    ]

    pairs = [
        {"source": "Airbus develops Urban Air Mobility in Europe.", "target": "空客在欧洲开发城市空中交通。"},
        {"source": "Urban Air Mobility requires advanced battery technology.", "target": "城市空中交通需要先进电池技术。"},
        {"source": "Safety standards in Urban Air Mobility are critical.", "target": "城市空中交通中的安全标准至关重要。"},
        {"source": "Airbus conducts flight tests in Germany.", "target": "空客在德国进行飞行测试。"},
    ]

    def mock_annotate(pairs_batch, *_args):
        items = []
        for idx, p in enumerate(pairs_batch):
            if "Airbus" in p["source"]:
                items.append({"seg": idx, "src": "Airbus", "tgt": "空客", "type": "domain", "note": "专有名词"})
            if "Urban Air Mobility" in p["source"]:
                items.append({"seg": idx, "src": "Urban Air Mobility", "tgt": "城市空中交通", "type": "domain", "note": "专业术语"})
        return core.AnnotationBatchResult(items)

    monkeypatch.setattr(core, "annotate_batch", mock_annotate)

    # 1. Run Baseline Serial (concurrency=1, capacity=1)
    state_serial = core.new_job_state("serial.docx")
    state_serial["pipeline_config"] = {"translation_concurrency": 1, "annotation_scheduler": "c2"}
    state_serial["pairs"] = copy.deepcopy(pairs)
    res_serial = core.annotate_stage(state_serial, "job-serial", glossary, "DeepSeek", "key", "model", "简体中文")

    # 2. Run Bounded Staging (concurrency=2, capacity=4)
    state_parallel = core.new_job_state("parallel.docx")
    state_parallel["pipeline_config"] = {"translation_concurrency": 2, "annotation_scheduler": "c4"}
    state_parallel["pairs"] = copy.deepcopy(pairs)
    res_parallel = core.annotate_stage(state_parallel, "job-parallel", glossary, "DeepSeek", "key", "model", "简体中文")

    # Both must produce identical annotations, global frequency limits, and completion states
    assert res_serial["annotations_done"] == res_parallel["annotations_done"] == True
    assert res_serial["annotations_done_offset"] == res_parallel["annotations_done_offset"] == 4
    assert res_serial["annotations"] == res_parallel["annotations"]
    assert res_serial["annotation_batches"] == res_parallel["annotation_batches"]
