"""Runtime-state read/write for a translation run (moved out of ``core.py``, #356).

**纯搬移**：逻辑与原版逐行一致，唯一的形式变化是对 `core` 上名字的引用改成
``core.<name>``（调用时解析）。共享的模块级状态（`_RUNTIME_STATUS_WRITERS` 等）仍是
同一个对象，测试对 ``core.<name>`` 的 monkeypatch 仍然生效；`core.py` 用
``X as X`` 把这些名字再导出，调用点与测试一行未改。
"""
from __future__ import annotations

import json
import os
import time


def _core():
    """The ``core`` module, resolved per call (never a snapshot)."""
    import core
    return core


def runtime_state_path(job_id):
    core = _core()
    return core.job_dir(job_id) / core.RUNTIME_STATE_FILE

def runtime_events_path(job_id):
    core = _core()
    return core.job_dir(job_id) / core.RUNTIME_EVENTS_FILE

def runtime_technical_log_path(job_id):
    core = _core()
    return core.job_dir(job_id) / core.RUNTIME_TECHNICAL_LOG

def _runtime_defaults():
    core = _core()
    return {
        "schema_version": core.RUNTIME_SCHEMA_VERSION,
        "status": "idle",
        "pipeline": "document_pipeline",
        "stage": "",
        "stage_id": "",
        "stage_index": None,
        "stage_total": None,
        "operation": "",
        "operation_id": "",
        "operation_label": "",
        "stage_progress": {},
        "section_id": None,
        "phase": "",
        "phase_label": "",
        "started_at": None,
        "operation_started_at": None,
        "last_heartbeat_at": None,
        "last_progress_at": None,
        "completed_units": 0,
        "total_units": 0,
        "overall_progress": None,
        "last_event": "",
        "events": [],
        "last_technical_event": "",
        "cancel_requested": False,
        "pause_requested": False,
        "exit_requested": False,
        "attempt": 0,
        "resume_request_id": None,
        "error": None,
        "worker": {
            "owner_pid": None,
            "worker_id": None,
            "lease_expires_at": None,
        },
    }

def load_runtime_state(job_id):
    """Read the durable worker status without changing the workflow state."""
    core = _core()
    if not job_id:
        return core._runtime_defaults()
    try:
        raw = json.loads(core._atomic_file.read_shared_text(core.runtime_state_path(job_id)))
    except (OSError, TypeError, ValueError):
        return core._runtime_defaults()
    if not isinstance(raw, dict):
        return core._runtime_defaults()
    result = core._runtime_defaults()
    result.update(raw)
    result["schema_version"] = core.RUNTIME_SCHEMA_VERSION
    if result.get("status") == "cancel_requested":
        result["status"] = "cancelling"
    worker = result.get("worker")
    result["worker"] = {**core._runtime_defaults()["worker"], **worker} \
        if isinstance(worker, dict) else dict(core._runtime_defaults()["worker"])
    result["events"] = [item for item in result.get("events") or []
                         if isinstance(item, dict)
                         and item.get("visibility") == "user"][-24:]
    return result

def _runtime_event_code(message):
    core = _core()
    value = core.re.sub(r"[^a-z0-9]+", "_", str(message or "").lower()).strip("_")
    return value[:80] or "runtime_event"

def _runtime_event_meta(event_name, visibility=None, category=None):
    core = _core()
    default_visibility, default_category = core._RUNTIME_EVENT_META.get(
        str(event_name or ""), ("technical", "debug"))
    return visibility or default_visibility, category or default_category

def _append_runtime_event(job_id, event, *, stage=None, operation=None, metadata=None,
                          visibility=None, category=None, sync=True, event_name=None):
    """Append one record to the job's runtime event log.

    ``sync=False`` skips the ``fsync``: the record is still handed to the OS
    immediately (any reader sees it), it is only not forced all the way to the
    platter.  The event log is an activity/diagnostic trail -- the business
    truth lives in ``state.json`` and the journal -- so paying a disk flush per
    record bought nothing but latency.
    """
    core = _core()
    event_name = event_name or (event.get("event") if isinstance(event, dict) else None)
    if not event_name:
        event_name = core._runtime_event_code(event.get("message") if isinstance(event, dict)
                                         else event)
    visibility, category = core._runtime_event_meta(event_name, visibility, category)
    record = {
        "timestamp": core._utc_now_iso(),
        "job_id": job_id,
        "pipeline": event.get("pipeline") if isinstance(event, dict) else None,
        "stage": stage or "",
        "operation": operation or "",
        "event": event_name,
        "message": event.get("message") if isinstance(event, dict) else str(event),
        "visibility": visibility,
        "category": category,
    }
    if record["pipeline"] is None:
        record["pipeline"] = "document_pipeline"
    if metadata:
        record["metadata"] = metadata
    path = core.runtime_events_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            if sync:
                os.fsync(stream.fileno())
    except OSError as exc:
        core._workflow_errors.raise_if_fatal(exc, job_id)
        raise
    with core._RUNTIME_PERSIST_LOCK:
        core._RUNTIME_PERSIST_STATS["event_appends"] += 1
        if sync:
            core._RUNTIME_PERSIST_STATS["fsyncs"] += 1
        core._RUNTIME_PERSIST_STATS["event_append_seconds"] += (
            time.perf_counter() - started)
    return record

def _runtime_event_lines_reverse(path):
    """Read complete JSONL lines from the tail without loading the whole log."""
    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        pending = b""
        while position:
            size = min(position, 8192)
            position -= size
            handle.seek(position)
            lines = (handle.read(size) + pending).split(b"\n")
            pending = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8")
        if pending:
            yield pending.decode("utf-8")

def read_runtime_events(job_id, limit=None, *, visibility=None, category=None):
    core = _core()
    path = core.runtime_events_path(job_id)
    if not path.is_file():
        return []
    events = []
    try:
        newest_first = bool(limit and limit > 0)
        lines = (core._runtime_event_lines_reverse(path) if newest_first else
                 path.read_text(encoding="utf-8").splitlines())
        for line in lines:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                event_visibility, event_category = core._runtime_event_meta(
                    value.get("event"), value.get("visibility"), value.get("category"))
                normalized = {**value, "visibility": event_visibility,
                              "category": event_category}
                if visibility and event_visibility != visibility:
                    continue
                if category and event_category != category:
                    continue
                events.append(normalized)
                if newest_first and len(events) >= limit:
                    break
    except OSError:
        return []
    if newest_first:
        return list(reversed(events))
    return events[-limit:] if limit else events

def _runtime_status_writer(job_id):
    """The per-job status writer; ``None`` when there is no job id."""
    core = _core()
    if not job_id:
        return None
    with core._RUNTIME_STATUS_LOCK:
        writer = core._RUNTIME_STATUS_WRITERS.get(job_id)
        if writer is None:
            writer = core._RuntimeStatusWriter(job_id)
            core._RUNTIME_STATUS_WRITERS[job_id] = writer
        return writer

def flush_runtime_status(job_id=None):
    """Write every pending status update now (terminal boundaries, shutdown)."""
    core = _core()
    with core._RUNTIME_STATUS_LOCK:
        if job_id:
            writers = [core._RUNTIME_STATUS_WRITERS[job_id]] if job_id in core._RUNTIME_STATUS_WRITERS else []
        else:
            writers = list(core._RUNTIME_STATUS_WRITERS.values())
    for writer in writers:
        writer.flush()

def reset_runtime_status(job_id=None):
    """Drop the pending status writers (a task reached a terminal boundary)."""
    core = _core()
    with core._RUNTIME_STATUS_LOCK:
        if job_id is None:
            writers = list(core._RUNTIME_STATUS_WRITERS.values())
            core._RUNTIME_STATUS_WRITERS.clear()
        else:
            writer = core._RUNTIME_STATUS_WRITERS.pop(job_id, None)
            writers = [writer] if writer is not None else []
    for writer in writers:
        writer.close()

def runtime_status_stats():
    core = _core()
    with core._RUNTIME_STATUS_LOCK:
        return {
            "writers": len(core._RUNTIME_STATUS_WRITERS),
            "submits": core._RUNTIME_STATUS_STATS["submits"],
            "flushes": core._RUNTIME_STATUS_STATS["flushes"],
            "coalesced": core._RUNTIME_STATUS_STATS["coalesced"],
            "dropped_terminal": core._RUNTIME_STATUS_STATS["dropped_terminal"],
        }

def _submit_runtime_status(job_id, **changes):
    """Hand a batch-progress status update to the merged status writer.

    Falls back to the synchronous path when there is no writer (no job id,
    or the writer was closed), so the behaviour never depends on whether the
    writer exists.
    """
    core = _core()
    writer = core._runtime_status_writer(job_id)
    if writer is None:
        return {}
    return writer.submit(**changes)

def _runtime_telemetry_request_started(job_id, *, purpose=None, operation_id=None):
    core = _core()
    coalescer = core._runtime_telemetry_coalescer(job_id)
    if coalescer is not None:
        coalescer.request_started(purpose=purpose, operation_id=operation_id)

def _runtime_telemetry_request_finished(job_id, *, purpose=None, operation_id=None,
                                        reasoning_level=None):
    core = _core()
    coalescer = core._runtime_telemetry_coalescer(job_id)
    if coalescer is not None:
        coalescer.request_finished(purpose=purpose, operation_id=operation_id,
                                   reasoning_level=reasoning_level)
