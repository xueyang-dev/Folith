"""Engine-owner registry for a translation run (moved out of ``core.py``, #356).

**纯搬移**：逻辑与原版逐行一致，唯一形式变化是对 `core` 上名字的引用改成
``core.<name>``（调用时解析）。共享的注册表对象（`_RUNTIME_ENGINE_OWNERS`）仍留在
core 里，所以还是同一个对象、测试对 ``core.<name>`` 的 monkeypatch 仍生效；
`core.py` 用 ``X as X`` 再导出这五个名字，调用点与测试一行未改。
"""
from __future__ import annotations

import os
import uuid


def _core():
    """The ``core`` module, resolved per call (never a snapshot)."""
    import core
    return core


def register_engine_owner(job_id, *, pid, worker_id="", token=None):
    """Record that *this* process owns an engine child for ``job_id``.

    The GUI calls this right after it spawns ``--engine-host`` and before the
    child has written anything to disk, so every action gate
    (:func:`request_job_pause`, :func:`continue_job`, :func:`resume_job`, ...)
    sees the task as executing during the child's ~1 s cold start.  Returns the
    token to hand back to :func:`release_engine_owner`.

    ``worker_id`` is the child's own run id once it is known; binding it keeps
    the registration tied to *that* run rather than to a pid (see
    :func:`_engine_owner_alive`).

    The **first** registration (``token is None``) is also the launch boundary
    of a run, and it clears the pending pause/cancel/exit/restart requests
    exactly once (E-6a).  Passing ``token`` means "not that boundary": the
    in-memory entry is updated and nothing is written, so a rebind cannot
    erase a request.  :func:`_bind_worker_id` passes the token for that
    reason, and so does ``start_engine`` — its flag clear is the strict
    ``publish_starting_status`` write, which lands *before* this registry
    entry becomes visible.  A caller that still clears here gets the token
    back only after the write; if the write raises, the entry just stored is
    removed with that same token before the error propagates.
    """
    core = _core()
    if not job_id or not pid:
        return ""
    launch = token is None
    token = str(token or uuid.uuid4().hex)
    with core._RUNTIME_ENGINE_OWNERS_LOCK:
        core._RUNTIME_ENGINE_OWNERS[str(job_id)] = {
            "token": token,
            "pid": int(pid),
            "worker_id": str(worker_id or ""),
            "registered_at": core._utc_now_iso(),
        }
    if launch:
        # "A new run starts here."  Written once, by the process that owns the
        # launch, before the child can publish anything.  The registry entry
        # must not outlive a write that never returned the token.
        try:
            core.update_runtime_state(
                job_id, on_timeout="raise", pause_requested=False,
                cancel_requested=False, exit_requested=False,
                restart_requested=False,
                event="已启动新的运行", event_name="run_launched",
                event_visibility="technical", event_category="lifecycle")
        except Exception:
            release_engine_owner(job_id, token)
            raise
    return token


def release_engine_owner(job_id, token):
    """Forget an engine child, but only the one registered with ``token``.

    The token check is the point: a supervisor whose child already died must
    not clear the registration of a newer child for the same task (start, crash,
    restart) and make a live run look stopped.
    """
    core = _core()
    if not job_id or not token:
        return False
    with core._RUNTIME_ENGINE_OWNERS_LOCK:
        entry = core._RUNTIME_ENGINE_OWNERS.get(str(job_id))
        if not entry or entry.get("token") != str(token):
            return False
        core._RUNTIME_ENGINE_OWNERS.pop(str(job_id), None)
    return True


def engine_owner(job_id):
    """The engine child this process registered for ``job_id`` (or ``{}``)."""
    core = _core()
    with core._RUNTIME_ENGINE_OWNERS_LOCK:
        entry = core._RUNTIME_ENGINE_OWNERS.get(str(job_id or ""))
        return dict(entry) if entry else {}


def _engine_owner_alive(job_id):
    """True while the engine child registered by this process is still running.

    This is the answer the pid cannot give.  The registry is written by the
    supervisor that owns the ``Popen`` handle, so it knows the difference
    between "my child is running" and "that pid was recycled": a reaped child is
    removed from the registry (only with its own token, see
    :func:`release_engine_owner`).  A recorded ``worker_id`` additionally ties
    the registration to the run the core wrote to disk, so a registration left
    behind by an older run cannot answer for a newer one.
    """
    core = _core()
    entry = core.engine_owner(job_id)
    if not entry:
        return False
    if not core._runtime_pid_alive(entry.get("pid")):
        return False
    worker_id = entry.get("worker_id")
    if not worker_id:
        return True
    recorded = ((core.load_runtime_state(job_id).get("worker")) or {}).get("worker_id")
    return not recorded or recorded == worker_id


def _job_execution_alive(job_id):
    """A stale lease does not prove that its owner has stopped executing.

    Three tiers, strongest first: a worker thread in *this* process, the engine
    child this process spawned (ownership token — see
    :func:`_engine_owner_alive`), and a process recorded on disk.

    The last tier keeps the historical pid semantics on purpose: an expired
    lease alone is *not* proof of death (cpu-heavy extraction and long provider
    calls both outlive a heartbeat), and `tests/workflow_recovery_test.py` /
    `tests/runtime_heartbeat_recovery_test.py` pin "a live owner with an old
    lease still owns the run".  Pid reuse is instead answered where it actually
    happens: the child this GUI spawned is proven by its token, never by
    ``os.kill(pid, 0)``.
    """
    core = _core()
    if not job_id:
        return False
    if core.is_job_worker_alive(job_id):
        return True
    if core._engine_owner_alive(job_id):
        return True
    owner = (core.load_runtime_state(job_id).get("worker") or {}).get("owner_pid")
    return owner != os.getpid() and core._runtime_pid_alive(owner)
