"""Architecture Phase 2 — state/project write serialisation.

The property under test is *no lost update*: two surfaces that each perform
``load → mutate → save`` on the same authoritative store must both survive, or
the conflicting one must fail loudly.  Every concurrency test here has a paired
"what the bug looked like" control that demonstrates the loss when the
transaction lock is not used, so the tests cannot silently stop proving anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core  # noqa: E402
from folith import project as project_module  # noqa: E402
from folith import store_lock  # noqa: E402

LOCKED_TERM = {"source": "canopy closure", "target": "林冠郁闭",
               "preferred": "林冠郁闭", "status": "locked",
               "behavior": "translate"}


@contextmanager
def state_env():
    """Isolated output directory; the same `core.OUTPUT_DIR` every surface sees."""
    tmp = Path(tempfile.mkdtemp(prefix="state-lock-"))
    old_output = core.OUTPUT_DIR
    core.OUTPUT_DIR = tmp
    try:
        yield tmp
    finally:
        core.OUTPUT_DIR = old_output
        shutil.rmtree(tmp, ignore_errors=True)


def _job_with_pairs(job_id="job-a", pairs=2):
    state = core.new_job_state("book.docx")
    state["paras"] = [f"source {i}" for i in range(pairs)]
    state["pairs"] = [
        {"source": f"source {i}", "target": f"old {i}", "segment_uid": f"s{i}"}
        for i in range(pairs)
    ]
    state["p1_done"] = True
    core.save_job_state(job_id, state)
    return state


# ---------------------------------------------------------------------------
# Test A — two concurrent edits must both survive
# ---------------------------------------------------------------------------

def test_two_concurrent_edits_both_survive():
    with state_env():
        _job_with_pairs()
        barrier = threading.Barrier(2)
        errors: list = []

        def edit(index, target):
            try:
                barrier.wait(timeout=5)

                def mutate(state):
                    # Widen the window: with a correct transaction lock the other
                    # thread cannot even load state while we are inside it.
                    time.sleep(0.05)
                    state["pairs"][index]["target"] = target

                core.mutate_job_state("job-a", mutate)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=edit, args=(0, "NEW-0")),
                   threading.Thread(target=edit, args=(1, "NEW-1"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        assert not errors, errors
        pairs = core.load_job_state("job-a")["pairs"]
        assert pairs[0]["target"] == "NEW-0"
        assert pairs[1]["target"] == "NEW-1", "a concurrent edit was lost"


def test_without_the_transaction_lock_the_same_race_loses_an_update():
    """The control: raw load → mutate → save really does lose an edit.

    The write goes through the same atomic-replacement primitive the product
    uses, so this isolates the point of Phase 2: atomic replacement is not a
    transaction.
    """
    from folith.storage import job_repository

    with state_env():
        _job_with_pairs()
        barrier = threading.Barrier(2)
        # Serialise the *write* only.  Both threads have already loaded the same
        # version, so the second write still overwrites the first; this avoids
        # a concurrent ReplaceFileW sharing violation (WinError 1176) while
        # keeping the lost-update control deterministic on every runner.
        save_lock = threading.Lock()

        def raw_edit(index, target):
            state = core.load_job_state("job-a")
            barrier.wait(timeout=5)          # both threads hold the same version
            with save_lock:
                state["pairs"][index]["target"] = target
                job_repository.save_job_state_atomic("job-a", state)

        threads = [threading.Thread(target=raw_edit, args=(0, "NEW-0")),
                   threading.Thread(target=raw_edit, args=(1, "NEW-1"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        pairs = core.load_job_state("job-a")["pairs"]
        survived = {pairs[0]["target"], pairs[1]["target"]} & {"NEW-0", "NEW-1"}
        assert len(survived) == 1, (
            "the control no longer demonstrates a lost update — the test suite "
            "would stop proving that the lock is what fixes it")


def test_real_edit_entry_point_serializes_its_transaction():
    with state_env():
        _job_with_pairs()
        barrier = threading.Barrier(2)
        errors: list = []

        def edit(index, target):
            try:
                barrier.wait(timeout=5)
                core.save_translation_edit("job-a", index, target)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=edit, args=(0, "EDIT-0")),
                   threading.Thread(target=edit, args=(1, "EDIT-1"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        assert not errors, errors
        pairs = core.load_job_state("job-a")["pairs"]
        assert (pairs[0]["target"], pairs[1]["target"]) == ("EDIT-0", "EDIT-1")


# ---------------------------------------------------------------------------
# Test B — same-segment conflict: serialized, never corrupted
# ---------------------------------------------------------------------------

def test_same_segment_conflict_is_serialized_without_corruption():
    with state_env():
        _job_with_pairs()
        barrier = threading.Barrier(2)
        errors: list = []

        def edit(target):
            try:
                barrier.wait(timeout=5)

                def mutate(state):
                    time.sleep(0.03)
                    state["pairs"][0]["target"] = target

                core.mutate_job_state("job-a", mutate)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=edit, args=("A-wins",)),
                   threading.Thread(target=edit, args=("B-wins",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        assert not errors, errors
        state = core.load_job_state("job-a")
        assert state["pairs"][0]["target"] in {"A-wins", "B-wins"}
        assert len(state["pairs"]) == 2
        assert state["pairs"][1]["target"] == "old 1", "unrelated segment was damaged"


# ---------------------------------------------------------------------------
# Test C — concurrent project mutations
# ---------------------------------------------------------------------------

def test_concurrent_project_promotions_keep_both_terms():
    with state_env():
        project = core.create_project("shared")
        project_id = project["project_id"]
        jobs = []
        for index, term in enumerate(({"source": "alpha", "target": "阿尔法"},
                                      {"source": "beta", "target": "贝塔"})):
            job_id = f"promote-{index}"
            core.save_job_state(job_id, core.new_job_state(f"{index}.docx"))
            core.assign_job_to_project(job_id, project_id)
            state = core.load_job_state(job_id)
            state["glossary"] = [{**LOCKED_TERM, **term}]
            core.save_job_state(job_id, state)
            jobs.append(job_id)

        barrier = threading.Barrier(2)
        errors: list = []

        def promote(job_id):
            try:
                barrier.wait(timeout=5)
                core.promote_job_to_project(job_id, actor="reviewer")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=promote, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        assert not errors, errors
        merged = core.load_project(project_id)
        sources = {entry.get("source") for entry in merged.get("glossary") or []}
        assert sources == {"alpha", "beta"}, (
            "a concurrent promotion was silently overwritten")


def test_project_update_under_the_lock_is_not_overwritten():
    with state_env():
        project = core.create_project("target")
        project_id = project["project_id"]
        barrier = threading.Barrier(2)
        errors: list = []

        def add_term(term):
            try:
                barrier.wait(timeout=5)
                with core.project_mutation_lock(project_id):
                    current = core.load_project(project_id)
                    entries = list(current.get("glossary") or [])
                    entries.append(dict(term))
                    current["glossary"] = entries
                    core.save_project(current)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=add_term, args=({"source": "one"},)),
                   threading.Thread(target=add_term, args=({"source": "two"},))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)

        assert not errors, errors
        sources = {entry.get("source")
                   for entry in core.load_project(project_id).get("glossary") or []}
        assert sources == {"one", "two"}


# ---------------------------------------------------------------------------
# Test D — project delete vs save/update
# ---------------------------------------------------------------------------

def test_delete_project_is_not_resurrected_by_a_stale_save():
    with state_env():
        project = core.create_project("doomed")
        project_id = project["project_id"]
        stale = core.load_project(project_id)

        core.delete_project(project_id, confirm_name="doomed", cascade=True)
        assert core.load_project(project_id) is None

        with pytest.raises(ValueError) as excinfo:
            core.save_project(stale)
        assert "删除" in str(excinfo.value)
        assert core.load_project(project_id) is None, "the project was resurrected"
        assert not project_module.project_dir(core.OUTPUT_DIR, project_id).exists()


def test_concurrent_delete_and_update_leave_the_project_deleted():
    with state_env():
        project = core.create_project("racing")
        project_id = project["project_id"]
        loaded = threading.Event()
        errors: list = []

        def slow_update():
            try:
                with core.project_mutation_lock(project_id):
                    current = core.load_project(project_id)
                    loaded.set()
                    time.sleep(0.2)
                    current["description"] = "updated"
                    core.save_project(current)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        updater = threading.Thread(target=slow_update)
        updater.start()
        assert loaded.wait(5)
        # The delete must wait for the in-flight update instead of racing it.
        core.delete_project(project_id, confirm_name="racing", cascade=True)
        updater.join(15)

        assert core.load_project(project_id) is None, (
            "delete must win deterministically, whatever the interleaving")
        assert not project_module.project_dir(core.OUTPUT_DIR, project_id).exists()
        assert not errors or all("删除" in str(item) for item in errors), errors


def test_delete_job_is_not_resurrected_by_a_concurrent_save():
    with state_env():
        _job_with_pairs("job-gone")
        assert core.delete_job("job-gone") is True
        assert core.load_job_state("job-gone") is None
        # A save through the transaction lock can no longer recreate the job.
        with core.job_state_mutation_lock("job-gone", timeout=1.0):
            pass
        assert not core.job_dir("job-gone").exists()


# ---------------------------------------------------------------------------
# Test E — locks are always released
# ---------------------------------------------------------------------------

def test_lock_is_released_after_an_exception():
    with state_env():
        _job_with_pairs()
        with pytest.raises(RuntimeError):
            with core.job_state_mutation_lock("job-a"):
                raise RuntimeError("boom")

        # Re-acquirable immediately, and a failing mutation leaves no lock behind.
        with core.job_state_mutation_lock("job-a", timeout=1.0):
            pass

        def explode(state):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            core.mutate_job_state("job-a", explode)

        core.mutate_job_state("job-a", lambda state: state.__setitem__("ok", True))
        assert core.load_job_state("job-a")["ok"] is True


def test_a_failing_mutation_does_not_write_state():
    with state_env():
        _job_with_pairs()
        before = core.load_job_state("job-a")

        def explode(state):
            state["pairs"][0]["target"] = "should-not-persist"
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            core.mutate_job_state("job-a", explode)
        assert core.load_job_state("job-a") == before


# ---------------------------------------------------------------------------
# Test F — independent stores are not globally serialized
# ---------------------------------------------------------------------------

def test_independent_jobs_are_not_serialized():
    with state_env():
        _job_with_pairs("job-a")
        _job_with_pairs("job-b")
        holding = threading.Event()
        release = threading.Event()

        def hold_job_a():
            with core.job_state_mutation_lock("job-a"):
                holding.set()
                release.wait(10)

        holder = threading.Thread(target=hold_job_a)
        holder.start()
        assert holding.wait(5)
        try:
            begin = time.monotonic()
            core.mutate_job_state("job-b", lambda state: state.__setitem__("x", 1))
            elapsed = time.monotonic() - begin
        finally:
            release.set()
            holder.join(10)
        assert elapsed < 2.0, "an unrelated job was serialized behind another job's lock"
        assert core.load_job_state("job-b")["x"] == 1


def test_a_held_lock_times_out_with_a_clear_retriable_error():
    with state_env():
        _job_with_pairs("job-a")
        holding = threading.Event()
        release = threading.Event()

        def hold():
            with core.job_state_mutation_lock("job-a"):
                holding.set()
                release.wait(10)

        holder = threading.Thread(target=hold)
        holder.start()
        assert holding.wait(5)
        try:
            with pytest.raises(store_lock.StoreLockTimeout) as excinfo:
                core.mutate_job_state(
                    "job-a", lambda state: None, timeout=0.2)
            assert "稍后重试" in str(excinfo.value)
        finally:
            release.set()
            holder.join(10)

        # The pending edit was refused, never silently dropped.
        assert core.load_job_state("job-a")["pairs"][0]["target"] == "old 0"


def test_reentrant_acquisition_in_one_thread():
    with state_env():
        _job_with_pairs("job-a")
        with core.job_state_mutation_lock("job-a"):
            with core.job_state_mutation_lock("job-a"):
                core.mutate_job_state("job-a", lambda state: state.__setitem__("n", 1))
        assert core.load_job_state("job-a")["n"] == 1


# ---------------------------------------------------------------------------
# Worker interaction (pause/resume must stay live)
# ---------------------------------------------------------------------------

def test_an_active_worker_blocks_an_external_mutation_without_deadlock():
    """A live worker keeps its existing exclusivity; the transaction lock must not
    turn that into a hang, and it must still be released on the failure path."""
    from folith import job_lock

    with state_env():
        _job_with_pairs("job-live")
        holding = threading.Event()
        release = threading.Event()

        def hold_worker_lock():
            stream = job_lock.acquire(core.job_dir("job-live") / ".worker.lock",
                                      blocking=False)
            holding.set()
            release.wait(10)
            stream.close()

        holder = threading.Thread(target=hold_worker_lock)
        holder.start()
        assert holding.wait(5)
        try:
            with pytest.raises(RuntimeError) as excinfo:
                core.mutate_job_state(
                    "job-live", lambda state: state.__setitem__("x", 1), timeout=2.0)
            assert "后台处理" in str(excinfo.value)
        finally:
            release.set()
            holder.join(10)

        # The state lock was released even though the transaction failed.
        core.mutate_job_state("job-live", lambda state: state.__setitem__("x", 1))
        assert core.load_job_state("job-live")["x"] == 1


def test_pause_and_resume_paths_are_not_blocked_by_a_state_transaction():
    """Runtime control uses its own store; a held state lock must not stall it."""
    with state_env():
        _job_with_pairs("job-control")
        holding = threading.Event()
        release = threading.Event()

        def hold_state_lock():
            with core.job_state_mutation_lock("job-control"):
                holding.set()
                release.wait(10)

        holder = threading.Thread(target=hold_state_lock)
        holder.start()
        assert holding.wait(5)
        try:
            begin = time.monotonic()
            core.request_job_pause("job-control")
            core.continue_job("job-control")
            elapsed = time.monotonic() - begin
        finally:
            release.set()
            holder.join(10)
        assert elapsed < 2.0, "pause/resume was serialized behind a state transaction"


# ---------------------------------------------------------------------------
# Multi-process evidence
# ---------------------------------------------------------------------------

_WORKER_SOURCE = '''
import sys, time
sys.path.insert(0, {root!r})
import core

job_id = sys.argv[1]
iterations = int(sys.argv[2])


def bump(state):
    # A deliberate gap between load and save: without a cross-process
    # transaction lock this is exactly where the update is lost.
    time.sleep(0.02)
    state["counter"] = int(state.get("counter") or 0) + 1


for _ in range(iterations):
    core.mutate_job_state(job_id, bump)
'''


def test_multi_process_mutations_do_not_lose_updates(tmp_path):
    """Qt and Streamlit are separate processes; prove the lock spans them."""
    with state_env():
        _job_with_pairs("job-mp")
        worker = tmp_path / "mutation_worker.py"
        worker.write_text(_WORKER_SOURCE.format(root=str(ROOT)), encoding="utf-8")
        env = dict(os.environ, FOLITH_OUTPUT_DIR=str(core.OUTPUT_DIR),
                   PYTHONPATH=str(ROOT))
        processes = [
            subprocess.Popen([sys.executable, str(worker), "job-mp", "5"],
                             cwd=str(ROOT), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for _ in range(2)
        ]
        outputs = [process.communicate(timeout=120) for process in processes]
        for process, (stdout, stderr) in zip(processes, outputs):
            assert process.returncode == 0, stderr.decode("utf-8", "replace")

        state = core.load_job_state("job-mp")
        assert state["counter"] == 10, (
            "two processes x five increments must all survive")


def test_store_lock_primitive_is_cross_process(tmp_path):
    """The filesystem lock itself, independent of the core helpers."""
    lock_path = tmp_path / "store.lock"
    holding = threading.Event()
    release = threading.Event()

    def holder():
        with store_lock.mutation_lock(lock_path, timeout=2.0):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=holder)
    thread.start()
    assert holding.wait(5)
    try:
        # A different thread (same process) is still a different open file
        # description, which is what makes the lock work across processes too.
        with pytest.raises(store_lock.StoreLockTimeout):
            with store_lock.mutation_lock(lock_path, timeout=0.15):
                pass
    finally:
        release.set()
        thread.join(10)

    # Released: the same path can be taken again.
    with store_lock.mutation_lock(lock_path, timeout=1.0):
        pass


def test_state_lock_never_recreates_a_deleted_task_directory():
    with state_env():
        _job_with_pairs("job-gone")
        assert core.delete_job("job-gone") is True
        with pytest.raises(ValueError):
            core.mutate_job_state("job-gone", lambda state: None)
        assert not core.job_dir("job-gone").exists()
        # The lock file itself lives outside the task directory.
        assert core.job_state_lock_path("job-gone").parent.name == ".locks"
        assert core.job_state_lock_path("job-gone").parent != core.job_dir("job-gone")


def _hold_worker_lock(job_id, hold_seconds):
    """Simulate a worker that has written its final status but not yet closed
    ``.worker.lock``: take the lock now, release it from another thread."""
    path = core.job_dir(job_id) / ".worker.lock"
    lock = core._job_lock.acquire(path, blocking=False)
    assert lock is not None, "the test could not take the worker lock"
    timer = threading.Timer(hold_seconds, lock.close)
    timer.daemon = True
    timer.start()
    return timer


def test_save_waits_out_a_finishing_worker_instead_of_refusing():
    """#72: a completed run that is still closing its lock must not block a save.

    The worker writes the terminal runtime status before it closes
    ``.worker.lock``.  A save in that gap used to fail with
    "任务正在后台处理" even though nothing was running, which is what made
    ``qt_client_adapter_test`` fail intermittently on slow CI runners.
    """
    with state_env():
        job_id = "job-settling"
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        core.update_runtime_state(job_id, status="completed")

        timer = _hold_worker_lock(job_id, 0.4)
        started = time.monotonic()
        try:
            core.save_job_state(job_id, core.load_job_state(job_id))
        finally:
            timer.join(5)
        elapsed = time.monotonic() - started

        # It waited for the teardown (rather than failing) but stayed bounded.
        assert elapsed >= 0.2, f"the save did not wait for the lock: {elapsed:.3f}s"
        assert elapsed < core.WORKER_LOCK_SETTLE_SECONDS + 1.0


def test_save_is_still_refused_immediately_while_the_run_is_active():
    """The bounded wait must not soften the guard for a run that is really busy."""
    with state_env():
        job_id = "job-active"
        core.save_job_state(job_id, core.new_job_state("fixture.docx"))
        core.update_runtime_state(job_id, status="running")

        timer = _hold_worker_lock(job_id, 1.0)
        started = time.monotonic()
        try:
            with pytest.raises(RuntimeError) as refused:
                core.save_job_state(job_id, core.new_job_state("fixture.docx"))
            elapsed = time.monotonic() - started
        finally:
            timer.join(5)

        assert "任务正在后台处理" in str(refused.value)
        # No settle loop for an active run: it fails on the ownership check.
        assert elapsed < 0.5, f"an active run made the save wait {elapsed:.3f}s"


def test_nested_store_lock_survives_locked_file_resolution_failure(tmp_path, monkeypatch):
    # Windows can refuse resolving an already-open lock file. The outer lock's
    # resolved key must still match a nested save's key; falling back to the raw
    # spelling (e.g. a short temp path or one containing '..') deadlocks itself.
    (tmp_path / "nested").mkdir()
    target = tmp_path / "nested" / ".." / "task.state.lock"
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == target and target.exists():
            raise PermissionError("cannot resolve the open lock file")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with store_lock.mutation_lock(target, timeout=0.1):
        assert store_lock.held_by_this_thread(target)
        with store_lock.mutation_lock(target, timeout=0.1):
            assert store_lock.held_by_this_thread(target)
    assert not store_lock.held_by_this_thread(target)
