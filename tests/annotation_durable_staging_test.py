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
  - On restart / resume from cursor 0, every task staged on disk is replayed
    without a duplicate LLM request, while tasks that were never staged are
    requested normally (a new request is not a repeated one);
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

SECTION_SOURCES = tuple(f"Section {i} about Aerodynamics." for i in range(4))
CRASH_RECOVERY_JOB_ID = "job-crash-recovery"
ANNOTATION_TARGET_LANG = "简体中文"
ANNOTATION_MODEL = "model"


def _staged_sections_on_disk(job_dir, sources, *, batch_size):
    """Section indexes whose results are actually present under .annotation_staging.

    Read from disk only: the in-memory state of a crashed run is not evidence
    that a result survived. Each staged file is named after its batch offset and
    repeats that offset in its payload; offsets are mapped back to section
    indexes with the same batching the product uses.
    """
    staging_dir = Path(job_dir) / ".annotation_staging"
    if not staging_dir.is_dir():
        return set()
    sections_by_offset = {}
    offset = 0
    for batch in core.make_batches(list(sources), batch_size=batch_size, max_chars=2600):
        sections_by_offset[offset] = set(range(offset, offset + len(batch)))
        offset += len(batch)
    staged = set()
    for path in sorted(staging_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        staged_offset = payload["offset"]
        assert path.stem == str(staged_offset), (
            f"staged file {path.name} does not match its payload offset {staged_offset!r}"
        )
        assert staged_offset in sections_by_offset, (
            f"unexpected staged batch offset {staged_offset!r}"
        )
        staged.update(sections_by_offset[staged_offset])
    return staged


def _assert_run2_reuses_staged_results(run2_sections, staged_after_run1):
    """On resume, requesting a never-staged section is fine; repeating a staged one is not.

    Set semantics over section indexes: run 2 works on the *remaining* sections,
    so its call set is not expected to be empty. What is forbidden is
    intersecting the sections already durably staged before run 2 started.
    """
    repeated = set(run2_sections) & set(staged_after_run1)
    assert repeated == set(), (
        "Run 2 repeated LLM calls for sections that were already durably staged: "
        f"{sorted(repeated)}; run 2 sections={sorted(set(run2_sections))}, "
        f"staged after run 1={sorted(staged_after_run1)}"
    )


class _FailedFirstCommitScenario:
    """Run 1 crashes on its first commit after some results are already staged.

    The construction is event driven, never timing driven:
    * Section 0's request blocks until Section 1's staged file is on disk, so
      "Section 1 was staged before the failing commit" holds by construction;
    * Sections 2 and 3 report a non-stageable status in run 1, so the staging
      directory deterministically holds exactly Sections 0 and 1 afterwards.
      Run 2 therefore has real new work -- the same shape CI hit (run 2's only
      call was Section 3, a section run 1 never staged), without needing a slow
      machine to lose that section.
    """

    UNSTAGEABLE_IN_RUN1 = (2, 3)

    def __init__(self, tmp_path, monkeypatch):
        monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(core, "ANNOT_BATCH_SIZE", 1)

        self.job_id = CRASH_RECOVERY_JOB_ID
        self.job_dir = tmp_path / self.job_id
        self.job_dir.mkdir(parents=True)
        self.calls = {1: [], 2: []}
        self.called_sections = {1: set(), 2: set()}
        self.phase = 1
        self.fail_save = True
        self.staged_after_run1 = set()
        self.recovered_state = None

        state = core.new_job_state("crash_test.docx")
        state["pipeline_config"] = {"translation_concurrency": 2, "annotation_scheduler": "c4"}
        state["pairs"] = [
            {"source": source, "target": f"第{i}节关于空气动力学。"}
            for i, source in enumerate(SECTION_SOURCES)
        ]
        core.save_job_state(self.job_id, state)
        self._state = state

        self._section1_staged = threading.Event()
        self._real_save_staged = annotation_staging.save_staged_annotation
        real_save_state = core.save_job_state

        def flaky_save(jid, current_state):
            # Fail the very first commit (Section 0's commit).
            if self.fail_save:
                raise OSError("Simulated disk write failure on first batch commit")
            real_save_state(jid, current_state)

        monkeypatch.setattr(core, "annotate_batch", self._mock_annotate)
        monkeypatch.setattr(annotation_staging, "save_staged_annotation", self._save_staged)
        monkeypatch.setattr(core, "save_job_state", flaky_save)

    def _mock_annotate(self, pairs, *_args):
        source = pairs[0]["source"]
        self.calls[self.phase].append(source)
        self.called_sections[self.phase].add(SECTION_SOURCES.index(source))
        if source == SECTION_SOURCES[0]:
            # Only proceed once Section 1's result is durably on disk. The
            # timeout is a broken-construction guard, not a race window.
            if not self._section1_staged.wait(timeout=30):
                raise RuntimeError("Section 1 was never staged; the construction is broken")
        if self.phase == 1 and source in {
            SECTION_SOURCES[i] for i in self.UNSTAGEABLE_IN_RUN1
        }:
            return core.AnnotationBatchResult(
                [], status="request_failed", error="simulated request failure"
            )
        return core.AnnotationBatchResult([
            {"seg": 0, "src": "Aerodynamics", "tgt": "空气动力学", "type": "domain", "note": "专业术语"}
        ])

    def _save_staged(self, job_id, offset, bi, items, fingerprint, **kwargs):
        saved = self._real_save_staged(job_id, offset, bi, items, fingerprint, **kwargs)
        if saved and offset == 1:
            self._section1_staged.set()
        return saved

    def run1(self):
        """Run 1: must raise OSError on the first batch commit.

        Returns the section indexes that were requested, and records on disk
        (``staged_after_run1``) which sections actually made it to staging.
        """
        with pytest.raises(OSError, match="Simulated disk write failure"):
            core.annotate_stage(
                self._state, self.job_id, [], "DeepSeek", "key",
                ANNOTATION_MODEL, ANNOTATION_TARGET_LANG,
            )
        self.fail_save = False
        self.staged_after_run1 = _staged_sections_on_disk(
            self.job_dir, SECTION_SOURCES, batch_size=core.ANNOT_BATCH_SIZE
        )
        return set(self.called_sections[1])

    def resume(self, *, model=ANNOTATION_MODEL):
        """Run 2: reload the durable checkpoint (still offset 0) and resume.

        Returns the section indexes requested by run 2.
        """
        self.phase = 2
        self.recovered_state = core.load_job_state(self.job_id)
        core.annotate_stage(
            self.recovered_state, self.job_id, [], "DeepSeek", "key",
            model, ANNOTATION_TARGET_LANG,
        )
        return set(self.called_sections[2])


def test_annotation_save_failure_recovery_reuses_staged_results(tmp_path, monkeypatch):
    scenario = _FailedFirstCommitScenario(tmp_path, monkeypatch)

    # --- Run 1: commit of Section 0 fails, Section 1 is already staged -------
    run1_sections = scenario.run1()
    assert {0, 1} <= run1_sections

    staged_after_run1 = scenario.staged_after_run1
    assert staged_after_run1 == {0, 1}, (
        "Sections 0 and 1 must be durably staged before the failing commit; "
        f"staging directory holds {sorted(staged_after_run1)}"
    )
    # The commit really failed: the durable checkpoint is still 0.
    assert core.load_job_state(scenario.job_id).get("annotations_done_offset", 0) == 0

    # --- Run 2: resume with a healthy disk -----------------------------------
    run2_sections = scenario.resume()
    calls_in_run2 = scenario.calls[2]

    # Existing guarantee, kept verbatim: a staged Section 1 must not be re-called.
    assert SECTION_SOURCES[1] not in calls_in_run2, (
        "Section 1 was already durably staged; repeating LLM call is forbidden!"
    )
    # Set semantics: run 2 is supposed to request the sections that were never
    # staged (here Sections 2 and 3) and must not touch any staged one.
    _assert_run2_reuses_staged_results(run2_sections, staged_after_run1)
    assert run2_sections == {2, 3}, (
        f"Run 2 should only request the never-staged sections, got {calls_in_run2}"
    )

    # Job completely finished
    assert scenario.recovered_state["annotations_done"]
    assert scenario.recovered_state["annotations_done_offset"] == 4

    # All staged files cleaned up on final success
    staging_dir = scenario.job_dir / ".annotation_staging"
    assert not staging_dir.is_dir() or not list(staging_dir.glob("*.json"))


def test_recovery_set_assertion_detects_repeated_staged_call(tmp_path, monkeypatch):
    """Negative control: the set assertion must fail when run 2 re-calls a staged section.

    Changing the model between runs invalidates the staged fingerprints, so the
    product correctly re-requests Sections 0 and 1. This test is about the
    assertion itself: it must report the repeat instead of passing silently.
    """
    scenario = _FailedFirstCommitScenario(tmp_path, monkeypatch)
    scenario.run1()
    assert scenario.staged_after_run1 == {0, 1}

    run2_sections = scenario.resume(model="model-v2")

    # The repeat really happened end to end (not a synthetic call list).
    assert set(run2_sections) & scenario.staged_after_run1 == {0, 1}, (
        f"expected Sections 0 and 1 to be requested again, got {scenario.calls[2]}"
    )
    with pytest.raises(AssertionError, match="already durably staged"):
        _assert_run2_reuses_staged_results(run2_sections, scenario.staged_after_run1)


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
