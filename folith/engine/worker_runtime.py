"""Worker lifecycle for a translation run (moved out of ``core.py``, #356).

这一块是「一次运行的外壳」：启动 / 续跑 / 取消 / 退出，以及 worker 线程的主体
（批次循环、终态捕获、心跳与收尾）。**本文件是纯搬移**：逻辑与原版逐行一致，唯一
的形式变化是对 `core` 上名字的引用改成 `core.<name>` —— 这样共享的模块级状态还是
同一个对象，测试里对 `core.<name>` 的 monkeypatch 仍然生效。

`core.py` 在原来的位置用 ``from folith.engine.worker_runtime import ...`` 把这些名字
重新绑定回去，所以既有调用点与测试一行都不用改。改行为的 PR 不要混进这里。
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timedelta, timezone


def _core():
    """The ``core`` module, resolved per call (never a snapshot)."""
    import core
    return core


def _run_job_worker(job_id, filename, file_bytes, pipeline_kwargs, base_url=None,
                    segment_operation=None):
    core = _core()
    stop_event = threading.Event()
    heartbeat = threading.Thread(target=core._runtime_heartbeat_loop,
                                 args=(job_id, stop_event,
                                       getattr(threading.current_thread(), "worker_id", None)),
                                 daemon=True)
    core._RUNTIME_CTX.job_id = job_id
    core.set_llm_base_url(base_url)
    core.set_llm_reasoning_effort((pipeline_kwargs or {}).get("reasoning_effort"))
    heartbeat.start()
    # 主动留痕：进程被直接杀掉时没有机会写日志，所以"有启动、没有释放"本身就是
    # 最有价值的证据（本次排查正是靠它区分"被关闭"与"抛异常"）。
    core._append_runtime_technical_log(
        job_id,
        f"worker started pid={os.getpid()} thread={threading.current_thread().name}"
        f" attempt={core.load_runtime_state(job_id).get('attempt')}"
        f" resume={bool(core.load_runtime_state(job_id).get('resume_request_id'))}")
    try:
        kwargs = dict(pipeline_kwargs or {})
        kwargs.pop("on_status", None)
        kwargs.pop("on_caption", None)
        state = core.load_job_state(job_id) or core.new_job_state(filename)
        queued = core.load_runtime_state(job_id)
        core._take_pending_restart(job_id)  # a new worker never inherits an old request
        core.update_runtime_state(
            job_id, on_timeout="skip", status="starting", phase="starting",
            phase_label="正在读取最近检查点", heartbeat=True, progress=True,
            event="后台 worker 已接管任务", event_name="worker_started",
            event_visibility="technical", event_category="orchestration",
            pipeline=("segment_operation" if segment_operation else
                      "academic_writing" if kwargs.get("enable_report")
                      else "document_pipeline"),
            restart_requested=False,
            worker_connection=core._connection_signature_from_kwargs(kwargs))
        core.update_runtime_state(
            job_id, on_timeout="skip", status="running", phase="running",
            phase_label="已恢复 · 正在执行" if queued.get("resume_request_id")
            else "正在执行", heartbeat=True)
        while True:
            if segment_operation:
                kind = segment_operation["kind"]
                indexes = segment_operation["indexes"]
                kwargs.pop("reasoning_effort", None)
                if kind == "review":
                    result = core.review_translation_segments(job_id, indexes, **kwargs)
                    failed = result[1].get("failed_segment_ids") or []
                    if failed:
                        raise RuntimeError(f"{len(failed)} 个段落审校未完成，请在审校队列中重试。")
                else:
                    result = core.retranslate_segments(
                        job_id, indexes, **kwargs,
                        on_caption=lambda text: core._runtime_caption_callback(job_id, text))
                    if len(result[1]) != len(indexes):
                        raise RuntimeError("部分段落未完成重译，请查看段落状态后重试。")
                    readiness = core._translation_evidence.translation_review_readiness(result[0])
                    if set(indexes) & set(readiness.get("failed_segment_ids") or []):
                        raise RuntimeError("重译已保存，但部分段落审校未完成，请在审校队列中重试。")
                if not core._runtime_cancel_requested(job_id):
                    core.update_runtime_state(
                        job_id, on_timeout="skip", segment_operation_result="completed",
                        completed_units=len(indexes), progress=True,
                        event="所选段落审校已完成" if kind == "review" else "所选段落重译已完成",
                        event_name="segment_operation_completed", event_visibility="user",
                        event_category="progress")
                break
            try:
                result = core.run_job_pipeline(
                    job_id, filename, file_bytes, **kwargs,
                    on_status=lambda label: core._runtime_status_callback(job_id, state, label),
                    on_caption=lambda text: core._runtime_caption_callback(job_id, text),
                )
            except core._WorkerRestartRequested:
                # The connection changed while the task was paused.  Keep the
                # same worker thread and task lock, but re-enter the pipeline
                # from the last checkpoint with the newly resolved settings.
                pending = core._take_pending_restart(job_id)
                if not pending:
                    raise
                core.trace.finish_run(job_id=job_id, status="interrupted")
                filename = pending.get("filename") or filename
                kwargs = dict(pending.get("kwargs") or {})
                kwargs.pop("on_status", None)
                kwargs.pop("on_caption", None)
                base_url = pending.get("base_url")
                core.set_llm_base_url(base_url)
                core.set_llm_reasoning_effort(kwargs.get("reasoning_effort"))
                state = core.load_job_state(job_id) or state
                core.flush_runtime_status(job_id)
                core.reset_runtime_status(job_id)
                core.update_runtime_state(
                    job_id, on_timeout="skip", pause_requested=False, restart_requested=False,
                    cancel_requested=False, exit_requested=False,
                    worker_connection=core._connection_signature_from_kwargs(kwargs),
                    event="已用新连接重新启动任务；已识别的页面与译文会保留。",
                    event_name="connection_restart_applied",
                    event_visibility="technical", event_category="lifecycle")
                result = None
                continue
            break
        result_state = core.load_job_state(job_id) or state
        # 终态之前先把合并中的批次进度写掉并关闭这条通道：否则一次迟到的状态写
        # 会把 "completed" 覆盖回 "running"。
        core.flush_runtime_status(job_id)
        core.reset_runtime_status(job_id)
        if core._runtime_cancel_requested(job_id):
            # An exit stop reuses the cancel boundary but must publish a
            # resumable ``interrupted`` state, never "the user cancelled".
            if core._runtime_exit_requested(job_id) and not core._runtime_business_complete(result_state):
                if not segment_operation:
                    core.trace.finish_run(job_id=job_id, status="interrupted")
                core._write_terminal_runtime_state(
                    job_id, what="job_stopped_for_exit",
                    status="interrupted", phase="interrupted",
                    phase_label="已停止以便退出",
                    event="任务已停止以便退出；可继续处理。",
                    event_name="job_stopped_for_exit", progress=True,
                    event_visibility="user", event_category="lifecycle",
                    worker={"owner_pid": None, "worker_id": None,
                            "lease_expires_at": None})
            else:
                if not segment_operation:
                    core.trace.finish_run(job_id=job_id, status="cancelled")
                core._write_terminal_runtime_state(
                    job_id, what="job_cancelled",
                    status="cancelled", phase="cancelled", phase_label="已取消",
                    event="任务已取消",
                    event_name="job_cancelled", progress=True,
                    event_visibility="user", event_category="lifecycle",
                    worker={"owner_pid": None, "worker_id": None,
                            "lease_expires_at": None})
        elif core._runtime_business_complete(result_state):
            if not segment_operation:
                core.trace.finish_run(job_id=job_id, status="completed")
            core._write_terminal_runtime_state(
                job_id, what="job_completed",
                status="completed", phase="completed", phase_label="已完成",
                event="任务已完成",
                event_name="job_completed", progress=True,
                event_visibility="user", event_category="lifecycle",
                heartbeat=True, overall_progress=1.0,
                worker={"owner_pid": None, "worker_id": None,
                        "lease_expires_at": None})
        else:
            if not segment_operation:
                core.trace.finish_run(job_id=job_id, status="checkpointed")
            core._write_terminal_runtime_state(
                job_id, what="job_checkpointed",
                status="idle_incomplete", phase="idle_incomplete",
                phase_label="等待继续", event="阶段已保存，等待继续",
                event_name="job_checkpointed", progress=True,
                event_visibility="technical", event_category="checkpoint",
                heartbeat=True,
                worker={"owner_pid": None, "worker_id": None,
                        "lease_expires_at": None})
        return result
    # 捕获 BaseException 而不是 Exception：worker 只捕 Exception 时，若线程抛出
    # SystemExit 一类的 BaseException，`finally` 不会执行 → 心跳线程继续续租 →
    # lease 永不过期 → 应用还活着时这个任务会**真的硬卡死**（状态一直停在活跃态，
    # start_job_worker / resume_job 都会拒绝）。这里必须失败闭合。
    except BaseException as exc:  # noqa: BLE001 - worker must publish failure to UI
        # 失败也是终态：先落盘并关闭合并中的状态通道，再读取/写终态。
        core.flush_runtime_status(job_id)
        core.reset_runtime_status(job_id)
        runtime_after = core.load_runtime_state(job_id)
        exit_requested = bool(runtime_after.get("exit_requested"))
        storage_failed = isinstance(exc, core._workflow_errors.AnnotationStagingError)
        cancelled = (not exit_requested) and not storage_failed and (
            bool(runtime_after.get("cancel_requested")) or "请求取消" in str(exc))
        if storage_failed:
            exit_requested = False
        final_status = "interrupted" if exit_requested else (
            "cancelled" if cancelled else "failed")
        if not segment_operation:
            core.trace.finish_run(job_id=job_id, status=final_status, failure_reason=str(exc))
        if not cancelled and not exit_requested:
            try:
                core._write_runtime_technical_log(job_id, exc)
            except OSError:
                pass
        current = runtime_after
        provider_status = core.provider_error_status(exc)
        user_error_message = (
            core.provider_error_message(exc, "任务执行失败")
            if provider_status["status"] != "unknown"
            else str(exc)[:500] or "任务执行失败")
        error = None if (cancelled or exit_requested) else {
            "type": type(exc).__name__,
            "message": user_error_message,
            "stage": current.get("stage_id") or current.get("stage"),
            "operation": current.get("operation_id") or current.get("operation"),
            "connection": core._connection_label_from_kwargs(kwargs),
            "timestamp": core._utc_now_iso(),
            "technical_log": core.RUNTIME_TECHNICAL_LOG,
        }
        if storage_failed:
            error["errno"] = exc.errno
            error["storage_error_type"] = type(exc.storage_error).__name__
        try:
            core._write_terminal_runtime_state(
                job_id, what="job_failed", status=final_status,
                phase=final_status,
                phase_label="已停止以便退出" if exit_requested
                else ("已取消" if cancelled else "步骤失败"),
                error=error,
                event="任务已停止以便退出；可继续处理。" if exit_requested
                else ("任务已取消" if cancelled else f"步骤失败：{user_error_message[:180]}"),
                event_name="job_stopped_for_exit" if exit_requested
                else ("job_cancelled" if cancelled else "job_failed"),
                event_visibility="user",
                event_category="lifecycle" if (cancelled or exit_requested) else "error",
                progress=True, heartbeat=True,
                **({"pause_requested": False} if storage_failed else {}),
                worker={"owner_pid": None, "worker_id": None,
                        "lease_expires_at": None})
        except core._workflow_errors.WorkflowStopped as stopped:
            if stopped.reason != "disk_full":
                raise
        return None
    finally:
        core._take_pending_restart(job_id)
        core._append_runtime_technical_log(
            job_id, f"worker released pid={os.getpid()}"
                    f" thread={threading.current_thread().name}")
        stop_event.set()
        heartbeat.join(timeout=core.RUNTIME_HEARTBEAT_SECONDS + 1)
        core.set_llm_base_url(None)
        # Terminal boundary: flush anything still inside the telemetry window,
        # then drop the coalescer so a resumed run starts from a clean slate.
        # The pooled transport client for this worker thread is released here
        # too, so a long-lived process does not accumulate idle sockets.
        try:
            core.flush_runtime_telemetry(job_id)
            core.reset_runtime_telemetry(job_id)
            core.flush_runtime_status(job_id)
            core.reset_runtime_status(job_id)
            core._llm_transport.close_thread_clients()
            try:
                core.update_runtime_state(
                    job_id, on_timeout="skip", event="后台运行已释放", event_name="worker_released",
                    event_visibility="technical", event_category="orchestration")
            except core._workflow_errors.WorkflowStopped as stopped:
                if stopped.reason != "disk_full":
                    raise
        finally:
            core._RUNTIME_CTX.__dict__.clear()
            with core._RUNTIME_WORKERS_LOCK:
                if core._RUNTIME_WORKERS.get(job_id) is threading.current_thread():
                    core._RUNTIME_WORKERS.pop(job_id, None)
                execution_lock = getattr(threading.current_thread(), "execution_lock", None)
                if execution_lock is not None:
                    execution_lock.close()


def _queued_belongs_to_this_launch(runtime, status, *, launch_prepared,
                                   launch_token):
    """True when ``queued`` is this launch's starting row, not a dead run.

    ``publish_starting_status`` writes ``status=queued`` and this launch's
    token before the child imports ``core``.  Holding ``.worker.lock`` does
    not make that row a leftover: publishing it as lost leaves a user-visible
    「上次运行的进程已退出」 and can make the monitor treat the run as already
    over.  A different token, a missing token, or an in-process start is still
    a leftover and must be published.
    """
    if not launch_prepared or status != "queued":
        return False
    expected = str(launch_token or "")
    launch = runtime.get("launch") if isinstance(runtime, dict) else None
    recorded = str(launch.get("token") or "") if isinstance(launch, dict) else ""
    return bool(expected) and recorded == expected


def start_job_worker(job_id, filename, file_bytes, pipeline_kwargs, base_url=None,
                     resume_request_id=None, *, segment_operation=None,
                     launch_prepared=False, launch_token=""):
    """Start one resumable pipeline worker; repeated UI reruns are idempotent.

    ``launch_prepared`` is set only by the engine child
    (:mod:`folith.desktop.engine_host`).  A run that starts in another process
    was *launched* there: ``start_engine`` registered the owner and cleared the
    pending requests before it returned, so this process is the executor, not
    the launch.  It must not clear again — the clear here happens ~1 s later,
    which is exactly the window in which the user's Pause/Cancel click lands
    (E-6a).  In-process starts (the default) *are* their own launch and clear
    the previous run's requests here, once.
    """
    core = _core()
    with core._RUNTIME_WORKERS_LOCK:
        if core._job_execution_alive(job_id):
            return False
        execution_lock = core._job_lock.acquire(core.job_dir(job_id) / ".worker.lock", blocking=False)
        if execution_lock is None:
            return False
        try:
            core._workflow_errors.reset(job_id)
            if file_bytes is not None:
                core.save_source(job_id, file_bytes)
            runtime = core.load_runtime_state(job_id)
            stored_status = runtime.get("status") or "idle"
            # A pause/cancel request written while this engine child was still
            # importing core leaves ``pausing``/``cancelling`` behind with no
            # run to own it (``.worker.lock`` was just taken).  The label is the
            # request, not another run: treat it as the resumable state it
            # wrapped so this run starts and honours it (E-6a).
            request_pending = stored_status in core.REQUEST_ONLY_STATUSES
            status = "interrupted" if request_pending else stored_status
            matching_resume = bool(resume_request_id) and (
                status not in core.RUNTIME_ACTIVE_STATUSES or (
                    status == "resume_requested" and runtime.get("resume_request_id") == resume_request_id))
            own_launch = _queued_belongs_to_this_launch(
                runtime, status, launch_prepared=launch_prepared,
                launch_token=launch_token)
            if (status in core.RUNTIME_ACTIVE_STATUSES and not matching_resume
                    and not own_launch):
                # We hold ``.worker.lock``, so no other execution can exist and
                # this active status is a leftover from a run that ended without
                # publishing (the app was closed, a child was killed).  Publish
                # that loss here, explicitly — the read path no longer heals it
                # (E-4e) — and start as a resumed run.  ``resume_requested`` is
                # kept: it is another launch's pending request, not a dead run.
                # A ``queued`` row stamped with this launch's token is the
                # parent's starting write, not that leftover (B-82).
                if status != "resume_requested" and core.publish_lost_runtime_state(
                        job_id, owns_execution_lock=True):
                    runtime = core.load_runtime_state(job_id)
                    status = str(runtime.get("status") or "interrupted")
                if status in core.RUNTIME_ACTIVE_STATUSES:
                    return False
            if not matching_resume and not request_pending and not own_launch:
                runtime = core.get_job_runtime_status(job_id)
                if runtime.get("status") in core.RUNTIME_ACTIVE_STATUSES:
                    return False
            state = core.load_job_state(job_id) or core.new_job_state(filename)
            worker_id = uuid.uuid4().hex
            attempt = int(runtime.get("attempt") or 0) + 1
            started_at = core._utc_now_iso()
            lease_expires_at = (datetime.now(timezone.utc) +
                                timedelta(seconds=core.RUNTIME_LEASE_SECONDS)).isoformat(
                                    timespec="seconds")
            completed_units, total_units = (0, 0)
            if state.get("p2_done") and pipeline_kwargs.get("enable_report"):
                completed_units, total_units = core._academic_runtime_progress(job_id, state)
            context = core._academic_resume_context(state, runtime) \
                if matching_resume and pipeline_kwargs.get("enable_report") else {
                    "pipeline": "document_pipeline", "stage": "pipeline",
                    "stage_id": "pipeline", "operation": "pipeline",
                    "operation_id": "pipeline", "operation_label": "准备任务",
                    "stage_progress": {},
                    "section_id": "", "stage_index": None, "stage_total": None,
                }
            if segment_operation:
                kind = segment_operation["kind"]
                context.update(
                    pipeline="segment_operation", stage="translation",
                    stage_id="segment_review" if kind == "review" else "segment_retranslation",
                    operation=kind, operation_id=kind,
                    operation_label="所选段落审校" if kind == "review" else "所选段落重译")
                completed_units, total_units = 0, len(segment_operation["indexes"])
            if matching_resume and status != "resume_requested":
                runtime = core.update_runtime_state(
                    job_id, on_timeout="raise", status="resume_requested", phase="resume_requested",
                    phase_label="正在恢复任务", resume_request_id=resume_request_id,
                    started_at=started_at, operation_started_at=started_at,
                    last_progress_at=started_at, completed_units=completed_units,
                    total_units=total_units, overall_progress=None,
                    worker={"owner_pid": os.getpid(), "worker_id": worker_id,
                            "lease_expires_at": lease_expires_at},
                    event="已从断点恢复任务", event_name="resume_requested",
                    event_visibility="user", event_category="lifecycle",
                    event_metadata={"resume_request_id": resume_request_id}, **context)
            if not launch_prepared:
                # One run starts here, so the requests of the previous run do
                # not carry over.  This is the *only* clear on the in-process
                # path; publishing ``queued`` below reads the flags and never
                # writes them (an engine launch already cleared them).
                core.update_runtime_state(
                    job_id, on_timeout="raise", pause_requested=False, cancel_requested=False,
                    exit_requested=False, restart_requested=False,
                    event="已启动新的运行", event_name="run_started",
                    event_visibility="technical", event_category="lifecycle")
            core.update_runtime_state(
                job_id, on_timeout="raise", status="queued", phase="starting", phase_label="准备中",
                **context, started_at=runtime.get("started_at") if matching_resume
                else started_at,
                operation_started_at=started_at, last_progress_at=started_at,
                last_heartbeat_at=started_at, error=None,
                attempt=attempt, resume_request_id=resume_request_id or "",
                segment_operation=segment_operation or {}, segment_operation_result="pending",
                completed_units=completed_units, total_units=total_units,
                overall_progress=None,
                worker={"owner_pid": os.getpid(), "worker_id": worker_id,
                        "lease_expires_at": lease_expires_at},
                event="已排入后台 worker", event_name="job_queued",
                event_visibility="technical", event_category="orchestration")
            worker = threading.Thread(
                target=core._run_job_worker,
                args=(job_id, filename, file_bytes, dict(pipeline_kwargs or {}), base_url,
                      segment_operation),
                name=f"folith-{job_id}", daemon=True)
            worker.worker_id = worker_id
            worker.execution_lock = execution_lock
            core._RUNTIME_WORKERS[job_id] = worker
            worker.start()
            execution_lock = None  # owned by the worker until its final cleanup
            return True
        finally:
            if execution_lock is not None:
                execution_lock.close()


def start_segment_worker(job_id, kind, indexes, *, base_url=None, **kwargs):
    """Run a selected review or retranslation on the existing job worker."""
    core = _core()
    if kind not in {"review", "retranslate"}:
        raise ValueError(f"未知段落操作：{kind}")
    state = core.load_job_state(job_id)
    if state is None:
        raise ValueError(f"找不到任务 {job_id}")
    indexes = sorted(set(int(index) for index in indexes))
    if not indexes or any(index < 0 or index >= len(state.get("pairs") or [])
                          for index in indexes):
        raise ValueError("请选择仍然存在的翻译段落。")
    if kind == "review":
        kwargs["base_url"] = base_url or ""
    return core.start_job_worker(
        job_id, state.get("filename") or "", None, kwargs, base_url=base_url,
        segment_operation={"kind": kind, "indexes": indexes})


def resume_job(job_id, filename, pipeline_kwargs, base_url=None,
               resume_request_id=None, *, launch_prepared=False,
               launch_token=""):
    """Idempotently request resume and publish the transition before worker start.

    ``launch_prepared`` has the same meaning as in :func:`start_job_worker`: an
    engine child executes a launch that already cleared the pending requests,
    and clearing again here would drop a pause/cancel the user made while the
    child was starting (E-6a).
    """
    core = _core()
    with core._RUNTIME_WORKERS_LOCK:
        runtime = core.get_job_runtime_status(job_id)
        status = str(runtime.get("status") or "")
        if status in {"paused", "pausing"} and (
                core.is_job_worker_alive(job_id) or core._job_execution_alive(job_id)):
            # A paused worker is still alive: continue it.  Passing the freshly
            # resolved kwargs lets the core restart the worker transparently if
            # the connection changed while it was paused (issue #222).
            return core.continue_job(job_id, restart_kwargs=dict(pipeline_kwargs or {}),
                                base_url=base_url, filename=filename)
        if core._job_execution_alive(job_id):
            return False
        # ``pausing``/``cancelling`` with nothing executing is a request written
        # during the engine child's cold start, addressed to the run being
        # resumed — not a run that owns the task (E-6a).
        if status in core.RUNTIME_ACTIVE_STATUSES and status not in core.REQUEST_ONLY_STATUSES:
            return False
        state = core.load_job_state(job_id) or core.new_job_state(filename)
        request_id = resume_request_id or uuid.uuid4().hex
        if runtime.get("resume_request_id") == request_id:
            return False
        pipeline_kwargs = dict(pipeline_kwargs or {})
        academic = state.get("academic_state") or {}
        if state.get("p2_done") and (academic.get("artifacts") or academic.get(
                "current_stage") not in {None, "", "not_started"}):
            pipeline_kwargs["enable_report"] = True
        return core.start_job_worker(
            job_id, filename, None, pipeline_kwargs, base_url=base_url,
            resume_request_id=request_id, launch_prepared=launch_prepared,
            launch_token=launch_token)


def is_job_worker_alive(job_id):
    """True when this process has a *thread* running the pipeline for ``job_id``.

    Deliberately narrow (E-0): it answers "is the worker mine?".  A GUI action
    gate must not use it alone once the pipeline can run in an engine child;
    use ``core.is_job_worker_alive(...) or _job_execution_alive(...)`` instead, which
    is what :func:`request_job_cancel` / :func:`request_job_exit` already did.
    """
    core = _core()
    if not job_id:
        return False
    with core._RUNTIME_WORKERS_LOCK:
        worker = core._RUNTIME_WORKERS.get(job_id)
        if worker and worker.is_alive():
            return True
        # If in-memory registry lost the reference (e.g. Streamlit reloaded the module),
        # look for any active worker thread belonging to this job in the current process.
        target_name = f"folith-{job_id}"
        for thread in threading.enumerate():
            if thread.name == target_name and thread.is_alive():
                core._RUNTIME_WORKERS[job_id] = thread
                return True
        return False


def request_job_cancel(job_id, *, force=False):
    """Request cancellation between provider calls; an active HTTP call finishes first.

    If no active worker is running, or if force=True, marks the runtime state as cancelled immediately.
    """
    core = _core()
    if not job_id:
        return False
    alive = core.is_job_worker_alive(job_id)
    if alive or core._job_execution_alive(job_id):
        core.update_runtime_state(job_id, on_timeout="raise", status="cancelling", cancel_requested=True,
                             phase="cancelling", phase_label="正在取消",
                             event="已请求取消任务", event_name="cancel_requested",
                             progress=True)
        return True

    # If no worker is alive or force cancel was explicitly requested:
    # Immediately transition the job state out of active running status.
    # GUI/action side (E-6u): the user is waiting on Cancel, so a refused write
    # is reported ("please retry") instead of parking the slot for a retry
    # budget; the terminal state is either written or explicitly refused.
    # CX-19 (#621): this terminal write happens only when no executor is alive.
    # Re-check the in-process owner inside the state lock (only_if_unowned) and
    # keep an already-terminal status (leave_terminal): a launch thread that
    # registered just after the liveness check above must not have its
    # cancel_requested cleared here, and a stop that already landed must not be
    # reopened. If the lock-time check finds an owner the write is skipped; the
    # launch path re-reads the stop intent before sending the start line.
    core.update_runtime_state(
        job_id, on_timeout="raise", status="cancelled", phase="cancelled", phase_label="已取消",
        cancel_requested=False, error=None,
        event="任务已取消", event_name="job_cancelled",
        event_visibility="user", event_category="lifecycle",
        progress=True, heartbeat=True, only_if_unowned=True, leave_terminal=True,
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})
    return True


def request_job_exit(job_id):
    """Stop a task because the app is quitting; keep it resumable.

    The exit flow must never lose committed batches.  This reuses the same
    cooperative cancel boundary (the worker stops scheduling and releases its
    write lock) but records ``exit_requested`` so the worker publishes
    ``interrupted`` — the status that offers "resume" on reopen — instead of
    ``cancelled``.  ``cancel_requested`` still breaks a paused worker out of its
    wait loop; the two flags together mean "stop because of exit", not "the user
    cancelled".
    """
    core = _core()
    if not job_id:
        return False
    # Asking twice must confirm, never restate: the engine host asks again on
    # stdin EOF, possibly while the worker is finishing.  That second write used
    # to flip an already-``interrupted`` run back to ``cancelling``, and the next
    # publisher cleared ``exit_requested`` -- the flag that says "resume me"
    # (Windows, E-6a follow-up; #453).  Once set, the request exists.
    runtime = core.load_runtime_state(job_id) or {}
    if runtime.get("exit_requested") is True:
        return True
    alive = core.is_job_worker_alive(job_id) or core._job_execution_alive(job_id)
    if alive:
        # ``retry``, not ``raise``: a single 2 s acquire against a child that
        # holds the lock became "停止任务失败" and kept the app open (#453); the
        # exit flow still caps the whole stop at its own budget.
        core.update_runtime_state(
            job_id, on_timeout="retry", exit_requested=True, cancel_requested=True,
            status="cancelling", phase="cancelling", phase_label="正在停止以便退出",
            event="正在停止任务以便退出；已完成的进度会保留，可稍后继续。",
            event_name="exit_requested", event_visibility="user",
            event_category="lifecycle", progress=True)
        return True
    # No live worker owns this task: publish the resumable terminal state
    # directly instead of leaving a stale active status behind.
    # GUI/action side (E-6u): quitting waits on this write; a refusal is
    # reported to the exit flow ("停止任务失败"), and the app stays open.
    core.update_runtime_state(
        job_id, on_timeout="retry", status="interrupted", phase="interrupted",
        phase_label="已停止以便退出", cancel_requested=False,
        exit_requested=True, error=None,
        event="任务已停止以便退出；可继续处理。",
        event_name="job_stopped_for_exit", event_visibility="user",
        event_category="lifecycle", progress=True, heartbeat=True,
        only_if_unowned=True, leave_terminal=True,
        worker={"owner_pid": None, "worker_id": None, "lease_expires_at": None})
    return True
