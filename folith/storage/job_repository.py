"""Authoritative state storage repository and derived summary read-model.

Architectural guarantees:
1. `state.json` remains the single authoritative ground truth.
2. `load_job_state` employs a file signature cache (mtime_ns, size) to eliminate
   expensive repeated disk parsing and migration while auto-invalidating on atomic replace.
3. Cache returns deep-copied snapshots to prevent caller mutations from polluting the cache.
4. `summary.json` is a lightweight derived read model written ONLY during mutation paths.
5. The read path has strictly NO write side-effects: missing summaries fall back to
   loading state without creating or touching files on disk.
"""
from __future__ import annotations

import collections
import copy
import json
import os
import pickle
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from folith import profiler
from folith.storage import state_journal

# Cache mapping (job_id, mtime_ns, file_size, journal_mtime_ns, journal_size)
# -> normalized state dict.  The journal is part of the signature because a
# load replays it: without it, a journal append between two snapshots would be
# invisible to every reader until the next snapshot.
_STATE_CACHE: collections.OrderedDict[Tuple[str, int, int, int, int], Dict[str, Any]] = collections.OrderedDict()
_STATE_CACHE_MAX_ENTRIES = 256

# Directories already created in this process, keyed by (output_dir, job_id).
# `mkdir(parents=True, exist_ok=True)` is still a syscall on every save; on a
# per-batch save path that is pure overhead for a directory that exists.
_DIR_READY: set = set()
_DIR_READY_LOCK = threading.Lock()

# Serializer for the authoritative snapshot and the derived summary.
# ``indent=2`` is presentation, not data: the compact encoder emits the same
# keys, values, ordering and Unicode escaping with fewer bytes, which is what
# the write path actually pays for.  Measured in
# ``scripts/persistence_serializer_benchmark.py``.
#
# Public because the project translation memory (written by ``core.save_tm``)
# is the same kind of artifact read back with ``json.loads``; sharing one
# definition keeps the two writers from drifting apart.
COMPACT_JSON = {"ensure_ascii": False, "separators": (",", ":")}

_COMPACT_JSON = COMPACT_JSON

_STATS = {
    "cache_hits": 0,
    "cache_misses": 0,
    "disk_reads": 0,
}


def get_cache_stats() -> Dict[str, int]:
    return dict(_STATS)


def reset_cache_stats() -> None:
    _STATS["cache_hits"] = 0
    _STATS["cache_misses"] = 0
    _STATS["disk_reads"] = 0


def invalidate_cache(job_id: Optional[str] = None) -> None:
    """Clear cached state snapshots for a specific job or all jobs."""
    if job_id is None:
        _STATE_CACHE.clear()
        with _DIR_READY_LOCK:
            _DIR_READY.clear()
        return
    job_str = str(job_id)
    keys_to_remove = [k for k in _STATE_CACHE if k[0] == job_str]
    for k in keys_to_remove:
        _STATE_CACHE.pop(k, None)


def _ensure_job_dir(output_dir: Path, job_id: str) -> Path:
    """Return the job directory, creating it at most once per process.

    The directory is only ever removed out from under us by an external actor
    (a test cleaning its tmpdir), and ``_write_text`` re-creates it and retries
    once when that happens, so the cached hint can never turn a recoverable
    situation into a failed save.
    """
    key = (str(output_dir), str(job_id))
    with _DIR_READY_LOCK:
        if key in _DIR_READY:
            return output_dir / str(job_id)
    d = output_dir / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    with _DIR_READY_LOCK:
        _DIR_READY.add(key)
    return d


def _write_bytes(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path``, recovering if the directory vanished."""
    try:
        path.write_bytes(payload)
    except OSError as exc:
        from folith.workflow_errors import raise_if_fatal
        raise_if_fatal(exc, path.parent.name)
        with _DIR_READY_LOCK:
            _DIR_READY.discard((str(path.parent.parent), str(path.parent.name)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def _get_output_dir(output_dir: Optional[Path] = None) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    import core
    return Path(core.OUTPUT_DIR)


def get_job_state_path(job_id: str, output_dir: Optional[Path] = None) -> Path:
    return _get_output_dir(output_dir) / str(job_id) / "state.json"


def get_job_summary_path(job_id: str, output_dir: Optional[Path] = None) -> Path:
    return _get_output_dir(output_dir) / str(job_id) / "summary.json"


def _isolated_copy(state: Dict[str, Any]) -> Dict[str, Any]:
    """调用方拿到的独立副本：语义同 ``copy.deepcopy``，但快约 3 倍。

    状态是从 JSON 读出来的纯数据，pickle 往返与 deepcopy 等价（同样保留共享引用）。
    大文档的状态有几 MB（整本书的版面 IR），界面每次刷新都要拿一份；deepcopy 在
    这里实测要 0.2–0.6 秒，是工作台卡顿的一部分。遇到不能 pickle 的值时退回 deepcopy。
    """
    try:
        return pickle.loads(pickle.dumps(state, pickle.HIGHEST_PROTOCOL))
    except Exception:  # noqa: BLE001 - 退回到原来的做法
        return copy.deepcopy(state)


def load_job_state_cached(
    job_id: str,
    output_dir: Optional[Path] = None,
    force_reload: bool = False,
) -> Optional[Dict[str, Any]]:
    """Load job state with safe file signature caching and caller isolation.

    Returns a deepcopy of the normalized state to guarantee caller mutations
    cannot corrupt the cached internal representation.
    """
    p = get_job_state_path(job_id, output_dir=output_dir)
    try:
        stat = p.stat()
    except OSError:
        return None

    journal_path = state_journal.journal_path(job_id, output_dir=output_dir)
    try:
        jstat = journal_path.stat()
        journal_signature = (jstat.st_mtime_ns, jstat.st_size)
    except OSError:
        journal_signature = (0, 0)

    signature = (str(job_id), stat.st_mtime_ns, stat.st_size,
                 journal_signature[0], journal_signature[1])

    if not force_reload and signature in _STATE_CACHE:
        _STATS["cache_hits"] += 1
        profiler.count("load_job_state_cache_hit")
        # Move to end for LRU
        _STATE_CACHE.move_to_end(signature)
        return _isolated_copy(_STATE_CACHE[signature])

    _STATS["cache_misses"] += 1
    _STATS["disk_reads"] += 1
    profiler.count("load_job_state_cache_miss")
    profiler.count("load_job_state_disk_read")

    with profiler.span("load_job_state_disk", job_id=job_id):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

        import core
        from folith import delivery as _delivery
        from folith import state_migration as _state_migration
        from folith import translation_evidence as _translation_evidence

        state = _delivery.normalize_state_findings(_state_migration.migrate_state(raw))
        # The journal holds every translation batch committed after this
        # snapshot.  Replaying it here -- before the reconcile steps, which
        # compare against the current pairs -- is what makes a crash cost
        # replay instead of re-translation.  It is pure I/O and never calls a
        # model; with nothing to replay it is a single tail read.
        state_journal.replay(job_id, state, output_dir=output_dir)
        _translation_evidence.reconcile_runtime_review_truth(state)
        state = core._reconcile_final_delivery_snapshot(job_id, state)

        # Store in LRU cache
        if len(_STATE_CACHE) >= _STATE_CACHE_MAX_ENTRIES:
            _STATE_CACHE.popitem(last=False)
        _STATE_CACHE[signature] = state

        return _isolated_copy(state)


def save_job_state_atomic(
    job_id: str,
    state: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> None:
    """Atomically save state.json and update the derived summary.json.

    ``state.json`` stays the single authoritative complete task snapshot; this
    function only decides *how* that snapshot reaches the disk.  The work is
    split into named sub-phases (see ``folith.persistence_profiler``) so
    the cost of a save can be attributed instead of guessed.
    """
    import core
    from folith import delivery as _delivery
    from folith import persistence_profiler as _pprof
    from folith import translation_evidence as _translation_evidence

    _pprof.count_save()

    # Resolving and creating the write target is idempotent and independent of
    # the reconcile results, so it runs first: that keeps every phase below a
    # pure function of already-prepared inputs.
    with _pprof.phase("state_pre_reconcile"):
        d = _ensure_job_dir(_get_output_dir(output_dir), job_id)

    with _pprof.phase("review_truth_reconcile") as sink:
        _translation_evidence.reconcile_runtime_review_truth(state)
        sink.traversed(len(state.get("review_evidence") or []))

    with _pprof.phase("finding_normalization") as sink:
        _delivery.normalize_state_findings(state)
        sink.traversed(len(state.get("findings") or []))

    with _pprof.phase("delivery_snapshot_reconcile"):
        core._reconcile_final_delivery_snapshot(job_id, state)

    tmp = d / f"state.json.{uuid.uuid4().hex}.tmp"
    dest = d / "state.json"
    with _pprof.phase("json_serialization") as sink:
        # Encode once here and hand the bytes to the writer: the UTF-8 pass is
        # part of serialization, and doing it twice would be pure waste on a
        # multi-megabyte payload.
        encoded = json.dumps(state, **_COMPACT_JSON).encode("utf-8")
        sink.serialized(len(encoded))
    try:
        with _pprof.phase("state_file_write") as sink:
            _write_bytes(tmp, encoded)
            sink.written(len(encoded))
        with _pprof.phase("atomic_replace"):
            tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)

    # Invalidate cache for this job_id so next read picks up the new file stat
    with _pprof.phase("cache_invalidation"):
        invalidate_cache(job_id)

    # Derived read-model: save summary.json during this write path
    try:
        save_job_summary(job_id, state, output_dir=output_dir)
    except Exception:
        pass


def build_job_summary(job_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a lightweight read-model dictionary from a full job state."""
    import core
    state = state if isinstance(state, dict) else {}
    pairs = state.get("pairs") or []
    paras = state.get("paras") or []
    seg_count = len(pairs) if pairs else len(paras)
    delivery = state.get("delivery") or {}
    findings = delivery.get("findings") or []
    runtime = state.get("runtime") or {}
    progress_info = state.get("progress") or ""

    runtime_status = runtime.get("status")
    if not runtime_status or runtime_status == "idle":
        runtime_status = "idle" if core._runtime_business_complete(state) else "idle_incomplete"

    return {
        "job_id": str(job_id),
        "filename": str(state.get("filename") or ""),
        "project_id": state.get("project_id"),
        "target_lang": state.get("target_lang") or "zh",
        "stage": state.get("academic_stage") or state.get("stage") or "translation",
        "updated_at": state.get("updated_at") or state.get("created_at") or "",
        "created_at": state.get("created_at") or "",
        "runtime_status": runtime_status,
        "progress": progress_info,
        "progress_label": core.progress_label(state),
        "segment_count": seg_count,
        "review_count": len(findings),
    }


def save_job_summary(
    job_id: str,
    state: Dict[str, Any],
    output_dir: Optional[Path] = None,
) -> None:
    """Atomically write summary.json for a job during a mutation path."""
    from folith import persistence_profiler as _pprof

    d = _ensure_job_dir(_get_output_dir(output_dir), job_id)
    with _pprof.phase("summary_build"):
        summary = build_job_summary(job_id, state)
    with _pprof.phase("summary_serialization") as sink:
        encoded = json.dumps(summary, **_COMPACT_JSON).encode("utf-8")
        sink.serialized(len(encoded))
    with _pprof.phase("summary_write") as sink:
        tmp = d / f"summary.json.{uuid.uuid4().hex}.tmp"
        try:
            _write_bytes(tmp, encoded)
            sink.written(len(encoded))
            tmp.replace(d / "summary.json")
        finally:
            tmp.unlink(missing_ok=True)


def load_job_summary(
    job_id: str,
    output_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Read summary.json if available; returns None if missing."""
    p = get_job_summary_path(job_id, output_dir=output_dir)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_job_summaries(output_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List lightweight summaries for all jobs.

    STRICT INVARIANT: This is a read path. If summary.json is missing for a job,
    it falls back to reading state.json in-memory without creating any files on disk.
    """
    out = _get_output_dir(output_dir)
    summaries: List[Dict[str, Any]] = []
    if not out.is_dir():
        return summaries

    for d in sorted(out.iterdir()):
        if not d.is_dir():
            continue
        job_id = d.name
        summary_path = d / "summary.json"
        if summary_path.is_file():
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                summaries.append(data)
                profiler.count("summary_cache_hit")
                continue
            except Exception:
                pass

        # Fallback to state.json if summary.json missing (DO NOT WRITE TO DISK)
        state_path = d / "state.json"
        if state_path.is_file():
            state = load_job_state_cached(job_id, output_dir=out)
            if state is not None:
                summary = build_job_summary(job_id, state)
                summaries.append(summary)
                profiler.count("summary_fallback_miss")

    return summaries


def read_runtime_view(
    job_id: str,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Lightweight read-only view of a job's runtime status.

    Reads only lightweight artifacts (`runtime.json` and optionally `summary.json`),
    avoiding the overhead of loading, migrating, and reconciling full `state.json`.
    Includes `state_revision` (mtime_ns) to allow caller fragments to detect
    business state updates.
    """
    import core
    runtime = core.load_runtime_state(job_id) or {}
    if runtime.get("status") in core.RUNTIME_ACTIVE_STATUSES:
        # Keep lease/worker reconciliation without loading the full document.
        runtime = core.get_job_runtime_status(job_id, state={})
    summary = load_job_summary(job_id, output_dir=output_dir)

    state_p = get_job_state_path(job_id, output_dir=output_dir)
    try:
        state_mtime_ns = state_p.stat().st_mtime_ns
    except OSError:
        state_mtime_ns = 0

    status = runtime.get("status")
    if not status or status == "idle":
        if summary and summary.get("runtime_status"):
            status = summary.get("runtime_status")
        else:
            state = load_job_state_cached(job_id, output_dir=output_dir)
            if state:
                status = "idle" if core._runtime_business_complete(state) else "idle_incomplete"
            else:
                status = "idle"

    labels = {
        "resume_requested": "正在恢复任务",
        "queued": "正在恢复任务",
        "starting": "正在恢复任务",
        "running": "正在运行",
        "pausing": "正在暂停",
        "paused": "已暂停",
        "waiting_external": "正在运行",
        "stalled": "暂无运行信号",
        "interrupted": "上次运行已中断",
        "failed": "当前步骤失败",
        "cancelling": "正在取消",
        "cancelled": "任务已取消",
        "completed": "已完成",
        "idle_incomplete": "未完成",
        "waiting_manual": "待术语确认",
        "idle": "空闲",
    }
    status_label = labels.get(status, status)
    stage_label = runtime.get("stage") or runtime.get("phase_label") or (
        summary.get("stage") if summary else "任务处理"
    )
    operation_label = runtime.get("operation_label") or runtime.get("operation") or "正在执行"

    completed = int(runtime.get("completed_units") or 0)
    total = int(runtime.get("total_units") or 0)
    if total == 0 and summary:
        total = int(summary.get("segment_count") or 0)

    events = runtime.get("events") or []

    return {
        "job_id": str(job_id),
        "status": status,
        "runtime_status": status,
        "status_label": status_label,
        "headline_status": status_label,
        "stage_label": stage_label,
        "pipeline_stage": stage_label,
        "operation_label": operation_label,
        "operation_detail": runtime.get("phase_label") or "",
        "detail": runtime.get("last_event") or runtime.get("error") or "",
        "progress_done": completed,
        "progress_total": total,
        "runtime": runtime,
        "state_revision": state_mtime_ns,
        "events": events,
    }
