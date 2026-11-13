"""E-4e: ``get_job_runtime_status`` is a read; it must not publish "lost".

The engine child owns a task from the moment the GUI spawns it, but it writes
``runtime_state.worker.owner_pid`` only after it has imported ``core`` (~1 s).
A workbench refresh inside that window used to fall through
``_runtime_pid_alive(None) == False`` into ``_runtime_mark_lost(...)`` and
persist ``interrupted`` — so the user's pause request turned into "the last run
exited" and the UI lost the pending request (E-6p §5②, E-6a §5①).

The three cases pinned here:

1. cold start (ownership token, no ``owner_pid``) -> read writes nothing;
2. a held ``.worker.lock`` is ownership too -> read writes nothing;
3. a genuinely lost run is still *reported* as lost, and the explicit write
   path (``publish_lost_runtime_state`` / the launcher that holds the lock)
   publishes it.
"""
import os
import threading

import pytest

import core


OLD = "2000-01-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def clean_registries():
    yield
    core._RUNTIME_ENGINE_OWNERS.clear()
    core._RUNTIME_WORKERS.clear()


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job_id = "read-path-probe"
    core.save_job_state(job_id, core.new_job_state("probe.docx"))
    return job_id


def disk_snapshot(job_id):
    """Bytes of every durable runtime file; a read must leave them identical."""
    runtime_state = core.runtime_state_path(job_id)
    events = core.runtime_events_path(job_id)
    return {
        "runtime_state": runtime_state.read_bytes() if runtime_state.exists() else None,
        "events": events.read_bytes() if events.exists() else None,
        "files": sorted(p.name for p in core.job_dir(job_id).iterdir()),
    }


def event_names(job_id):
    return [item.get("event") for item in core.read_runtime_events(job_id)]


def assert_lost_is_a_view(job_id, runtime):
    assert runtime["status"] == "interrupted"
    assert runtime.get("measured") is False, (
        "a derived lost verdict must say it was not read from disk")
    assert "进程已退出" in str(runtime.get("phase_label") or "")


def test_cold_start_read_does_not_publish_interrupted(job):
    """The engine child was spawned; its ``owner_pid`` is not written yet.

    Start -> Pause -> (workbench refresh) is exactly the E-6p sequence: the
    pause request writes ``pausing``, the child is still importing core, and
    the refresh must report the request, not rewrite the task as interrupted.
    """
    token = core.register_engine_owner(job, pid=os.getpid())
    try:
        core.update_runtime_state(
            job, status="pausing", pause_requested=True,
            worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None},
            event="已请求暂停", event_name="pause_requested",
            event_visibility="user", event_category="lifecycle")
        before = disk_snapshot(job)

        runtime = core.get_job_runtime_status(job)

        assert runtime.get("status") == "pausing", runtime
        assert runtime.get("measured") is not False, (
            "the durable request must be reported as read, not derived")
        assert disk_snapshot(job) == before, (
            "a read inside the cold-start window must not touch runtime_state "
            "or the event stream")
        assert "interrupted" not in event_names(job)
    finally:
        core.release_engine_owner(job, token)


def test_cold_start_read_does_not_publish_interrupted_when_only_the_lock_is_held(job):
    """Execution proven by ``.worker.lock`` alone (no token, no recorded pid).

    A direct segment operation and a just-spawned child both hold the lock
    before they publish an owner pid.
    """
    lock = core._job_lock.acquire(core.job_dir(job) / ".worker.lock",
                                  blocking=False)
    assert lock is not None
    try:
        core.update_runtime_state(
            job, status="waiting_external", phase_label="等待模型响应",
            last_heartbeat_at=OLD, last_progress_at=OLD,
            worker={"owner_pid": None, "worker_id": None, "lease_expires_at": OLD},
            event="已向模型发送请求", event_name="llm_request_started")
        before = disk_snapshot(job)

        runtime = core.get_job_runtime_status(job)

        assert runtime.get("status") == "waiting_external", runtime
        assert disk_snapshot(job) == before, "a read must not write the lock owner's state"
        assert "interrupted" not in event_names(job)
    finally:
        lock.close()


def test_real_lost_run_is_a_read_only_view_then_published_by_the_writer(
        job, monkeypatch):
    """Nothing owns the task: the read still says lost, without writing it."""
    monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: False)
    core.update_runtime_state(
        job, status="running", phase_label="正在翻译",
        last_heartbeat_at=OLD, last_progress_at=OLD,
        worker={"owner_pid": 999999, "worker_id": "dead-worker",
                "lease_expires_at": OLD},
        event="正在翻译", event_name="translating")
    before = disk_snapshot(job)

    runtime = core.get_job_runtime_status(job)

    assert_lost_is_a_view(job, runtime)
    assert disk_snapshot(job) == before, (
        "the read path must not persist the lost verdict")
    assert core.load_runtime_state(job)["status"] == "running"
    assert "interrupted" not in event_names(job)

    # The write path owns the durable transition, and name says so.
    assert core.publish_lost_runtime_state(job) is True
    assert core.load_runtime_state(job)["status"] == "interrupted"
    assert "interrupted" in event_names(job)
    # ... and it is idempotent: a second publish has nothing to do.
    assert core.publish_lost_runtime_state(job) is False


def test_publish_lost_runtime_state_never_overwrites_a_live_run(job):
    """A stale lost verdict must not clobber a run this process spawned."""
    token = core.register_engine_owner(job, pid=os.getpid())
    try:
        core.update_runtime_state(
            job, status="starting",
            worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None},
            event="已启动", event_name="run_launched")
        before = disk_snapshot(job)

        assert core.publish_lost_runtime_state(job) is False
        assert disk_snapshot(job) == before
    finally:
        core.release_engine_owner(job, token)


def test_start_after_restart_publishes_the_lost_run_instead_of_refusing(
        job, monkeypatch):
    """A stale active status from a previous session must not block a start.

    Before E-4e the launch gate worked by accident: the UI refresh had already
    rewritten the status to ``interrupted``.  The launcher now does the write
    itself, explicitly, after taking ``.worker.lock``.
    """
    monkeypatch.setattr(core, "_runtime_pid_alive", lambda pid: False)
    core.update_runtime_state(
        job, status="running", phase_label="正在翻译",
        last_heartbeat_at=OLD, last_progress_at=OLD,
        worker={"owner_pid": 999999, "worker_id": "dead-worker",
                "lease_expires_at": OLD},
        event="正在翻译", event_name="translating")

    entered, release = threading.Event(), threading.Event()
    original = core.run_job_pipeline

    def slow_pipeline(*args, **kwargs):
        entered.set()
        release.wait(5)
        return core.load_job_state(args[0])

    monkeypatch.setattr(core, "run_job_pipeline", slow_pipeline)
    try:
        assert core.start_job_worker(
            job, "probe.docx", None, {"enable_report": False}) is True
        assert entered.wait(5), "the resumed run never started"
        assert "interrupted" in event_names(job), (
            "the launcher must publish the loss it found (the read no longer does)")
        assert core.load_runtime_state(job)["status"] == "running"
    finally:
        release.set()
        monkeypatch.setattr(core, "run_job_pipeline", original)
        core._RUNTIME_WORKERS.clear()
