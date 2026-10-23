"""Rebuild a pre-optimisation copy of the source tree.

The working copy has no version control, so "before" has to be reconstructed.
This script copies the tree and reverses the optimisation edits, which makes
an honest before/after comparison (and answers "did this test already fail?")
possible.

Run: ``python -m benchmarks.performance.revert_to_baseline <target-dir>``

The script fails if any of its edits does not apply, and only then writes
the ``.folith-baseline-reverted`` marker that the benchmark runner requires
for ``--baseline``.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_MARKER = ".folith-baseline-reverted"

SKIP = {".pytest_cache", "__pycache__", "build", "dist", "outputs", ".git",
        "offline-wheels", ".venv-windows"}

# (relative path, current text, original text)
PATCHES: list[tuple[str, str, str]] = []


def patch(path: str, current: str, original: str) -> None:
    PATCHES.append((path, current, original))


# Generated from the final C1-C6 text: each hunk below replaces the optimised
# text with the code that was on ``main`` before the C1-C6 port, so a reverted
# tree is byte-for-byte (line endings aside) the pre-port product code.
patch("core.py", r'''        with commit_lock:
            if saved:
                runtime_batches_saved[0] += 1
            # 批次进度是展示状态，不是业务真值：合并写（见
            # ``_RuntimeStatusWriter``）。取消/暂停的覆盖规则由真正的
            # ``update_runtime_state`` 在落盘时执行，所以语义不变。
            _submit_runtime_status(
                job_id, status="running", stage="translation", stage_id="translation",
                operation=operation_id, operation_id=operation_id, operation_label=label,
                phase=operation_id, phase_label=label, progress=True,
''', r'''        with commit_lock:
            if saved:
                runtime_batches_saved[0] += 1
            update_runtime_state(
                job_id, status="running", stage="translation", stage_id="translation",
                operation=operation_id, operation_id=operation_id, operation_label=label,
                phase=operation_id, phase_label=label, progress=True,
''')

patch("core.py", r'''

def _append_runtime_event(job_id, event, *, stage=None, operation=None, metadata=None,
                          visibility=None, category=None, sync=True, event_name=None):
    """Append one record to the job's runtime event log.

    ``sync=False`` skips the ``fsync``: the record is still handed to the OS
    immediately (any reader sees it), it is only not forced all the way to the
    platter.  The event log is an activity/diagnostic trail -- the business
    truth lives in ``state.json`` and the journal -- so paying a disk flush per
    record bought nothing but latency.
    """
    event_name = event_name or (event.get("event") if isinstance(event, dict) else None)
    if not event_name:
        event_name = _runtime_event_code(event.get("message") if isinstance(event, dict)
                                         else event)
    visibility, category = _runtime_event_meta(event_name, visibility, category)
    record = {
        "timestamp": _utc_now_iso(),
''', r'''

def _append_runtime_event(job_id, event, *, stage=None, operation=None, metadata=None,
                          visibility=None, category=None):
    event_name = event.get("event") if isinstance(event, dict) else _runtime_event_code(event)
    visibility, category = _runtime_event_meta(event_name, visibility, category)
    record = {
        "timestamp": _utc_now_iso(),
''')

patch("core.py", r'''        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            if sync:
                os.fsync(stream.fileno())
    except OSError as exc:
        _workflow_errors.raise_if_fatal(exc, job_id)
        raise
    with _RUNTIME_PERSIST_LOCK:
        _RUNTIME_PERSIST_STATS["event_appends"] += 1
        if sync:
            _RUNTIME_PERSIST_STATS["fsyncs"] += 1
        _RUNTIME_PERSIST_STATS["event_append_seconds"] += (
            time.perf_counter() - started)
    return record
''', r'''        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        _workflow_errors.raise_if_fatal(exc, job_id)
        raise
    with _RUNTIME_PERSIST_LOCK:
        _RUNTIME_PERSIST_STATS["event_appends"] += 1
        _RUNTIME_PERSIST_STATS["fsyncs"] += 1
        _RUNTIME_PERSIST_STATS["event_append_seconds"] += (
            time.perf_counter() - started)
    return record
''')

patch("core.py", r'''


# ================= 运行时状态合并（批次进度的写放大治理）=================
# 背景：批次进度（"正在翻译第 40–52 段"、"正在保存第 7 批译文"）是**展示状态**，
# 不是业务真值——译文在 ``state.json`` 和 journal 里。但它过去走的是与业务真值
# 相同的持久化路径：每一次都「读全量 runtime_state.json → 合并 → 整文件原子
# 替换」，并且每次都追加一条 fsync 过的事件记录。
#
# 实测（400 段文档、40 秒运行）：批次阶段一共触发 2276 次全量重写、2267 次
# fsync，累计 29.3 秒 CPU——是同一段时间里真正翻译工作量的数倍。这些写入持有
# GIL（json.dumps 是 C 扩展，全程不释放），桌面端感受到的"后台任务把整个应用
# 拖慢"主要就是它们。
#
# 这里把**批次进度**改成合并写：窗口内的多次状态更新合并成一次落盘。取消、
# 暂停、worker 租约、阶段边界和终态仍然走原来的同步路径——它们的正确性不能
# 依赖一个还没到期的窗口。
RUNTIME_STATUS_FLUSH_SECONDS = 0.4

#: States a task cannot come back from.  A status update that arrives after
#: one of them must not revive the task.
_RUNTIME_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: Status phases that are published to disk immediately: they announce that a
#: translation batch was written (or is being written).  The progress counter
#: and the run log both show them, and the counter must advance one batch at
#: a time and only after the corresponding batch was saved.  The other phases
#: ("building the context", "translating", "checking") are transient chatter
#: and are merged inside the window.
_RUNTIME_DURABLE_PHASES = frozenset({"state_commit", "state_commit_done"})

_RUNTIME_STATUS_LOCK = threading.RLock()
_RUNTIME_STATUS_WRITERS = {}
_RUNTIME_STATUS_STATS = {"submits": 0, "flushes": 0, "coalesced": 0,
                          "dropped_terminal": 0}


class _RuntimeStatusWriter:
    """Merge high-frequency runtime *status* updates into one durable write.

    The status channel answers "what is the task doing right now".  It is not
    business truth: the translated pairs live in ``state.json`` and the
    journal, and everything a crash recovery or a cancellation decision
    depends on is written synchronously by its own caller.

    What this writer guarantees:

    * the merged state on disk is never older than ``flush_interval``;
    * every queued event still reaches ``runtime_events.jsonl`` (the activity
      log keeps its history -- only the *state file* is coalesced);
    * :meth:`flush` is synchronous, so a terminal state is never left behind a
      pending window.
    """

    def __init__(self, job_id, *, flush_interval=None):
        self.job_id = job_id
        self.flush_interval = max(
            0.0, float(flush_interval if flush_interval is not None
                       else RUNTIME_STATUS_FLUSH_SECONDS))
        self._lock = threading.RLock()
        self._pending = {}
        self._pending_events = []
        self._last_flush = 0.0
        self._timer = None
        self._closed = False

    # -- public API -------------------------------------------------------
    def submit(self, *, event=None, event_name=None, event_metadata=None,
               event_visibility=None, event_category=None, progress=False,
               **changes):
        """Merge one status update; write it out at most once per window.

        Two kinds of update are published immediately, because the user reads
        them and they must not be skipped:

        * a **durable phase** -- "writing batch N" / "batch N saved".  The
          progress counter advances one batch at a time and only after the
          corresponding batch was written;
        * a **stage progress counter that moved** -- the number of finished
          units is the progress bar.

        Everything else ("building the context", "translating", "checking",
        the in-flight caption) is merged inside the window.
        """
        with self._lock:
            if self._closed:
                return {}
            _RUNTIME_STATUS_STATS["submits"] += 1
            now = time.monotonic()
            counter = _stage_progress_counter(changes.get("stage_progress"))
            pending_counter = _stage_progress_counter(
                self._pending.get("stage_progress"))
            durable = str(changes.get("operation_id") or "") in _RUNTIME_DURABLE_PHASES
            if durable or (counter is not None and pending_counter is not None
                           and counter != pending_counter):
                for key, value in changes.items():
                    if value is not None:
                        self._pending[key] = value
                if progress:
                    self._pending["_progress"] = True
                if event:
                    message = str(event).strip()
                    if message:
                        self._pending_events.append({
                            "message": message,
                            "event_name": event_name,
                            "event_metadata": event_metadata,
                            "event_visibility": event_visibility,
                            "event_category": event_category,
                        })
                self._flush_locked(now)
                return self._pending
            for key, value in changes.items():
                if value is not None:
                    self._pending[key] = value
            if progress:
                self._pending["_progress"] = True
            if event:
                message = str(event).strip()
                if message:
                    self._pending_events.append({
                        "message": message,
                        "event_name": event_name,
                        "event_metadata": event_metadata,
                        "event_visibility": event_visibility,
                        "event_category": event_category,
                    })
            if self._last_flush <= 0.0 or (now - self._last_flush) >= self.flush_interval:
                # The very first update of a task is published at once (the
                # user must see that something started); later updates wait
                # for the trailing edge of the window.
                self._flush_locked(now)
                return self._pending
            _RUNTIME_STATUS_STATS["coalesced"] += 1
            self._schedule_locked(now)
            return self._pending

    def flush(self):
        with self._lock:
            self._flush_locked(time.monotonic())

    def close(self):
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._flush_locked(time.monotonic())

    def pending(self) -> bool:
        with self._lock:
            return bool(self._pending or self._pending_events)

    def _terminal_state_reached(self) -> bool:
        """Has this task already reached a state it cannot come back from?"""
        try:
            current = load_runtime_state(self.job_id) or {}
        except Exception:  # noqa: BLE001 - an unreadable state is not terminal
            return False
        return str(current.get("status") or "") in _RUNTIME_TERMINAL_STATUSES

    # -- internals --------------------------------------------------------
    def _schedule_locked(self, now):
        if self._timer is not None:
            return
        delay = max(0.01, self.flush_interval - (now - self._last_flush))
        timer = threading.Timer(delay, self._flush_from_timer)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _flush_from_timer(self):
        with self._lock:
            self._timer = None
            if self._closed:
                return
            self._flush_locked(time.monotonic())

    def _flush_locked(self, now):
        if not self._pending and not self._pending_events:
            self._last_flush = now
            return
        changes = dict(self._pending)
        self._pending.clear()
        progress = bool(changes.pop("_progress", False))
        events = list(self._pending_events)
        self._pending_events.clear()
        self._last_flush = now
        if self._terminal_state_reached():
            # A terminal state is authoritative.  A status that arrives after it
            # (a straggler from a batch that was still in flight) must not
            # revive the task as "running".
            _RUNTIME_STATUS_STATS["dropped_terminal"] += 1
            return
        _RUNTIME_STATUS_STATS["flushes"] += 1
        # The activity log keeps every event; only the state file is merged.
        for item in events:
            _append_runtime_event(
                self.job_id,
                {"event": item.get("event_name"), "message": item.get("message")},
                stage=changes.get("stage_id") or changes.get("stage"),
                operation=changes.get("operation_id") or changes.get("operation"),
                metadata=item.get("event_metadata"),
                visibility=item.get("event_visibility"),
                category=item.get("event_category"),
                sync=False,
                event_name=item.get("event_name"))
        last = events[-1] if events else {}
        try:
            update_runtime_state(
                self.job_id,
                event=last.get("message"),
                event_name=last.get("event_name"),
                event_metadata=last.get("event_metadata"),
                event_visibility=last.get("event_visibility"),
                event_category=last.get("event_category"),
                progress=progress,
                event_logged=bool(events),
                **changes)
        except Exception:  # noqa: BLE001 - a status write must never break a batch
            pass


def _runtime_status_writer(job_id):
    """The per-job status writer; ``None`` when there is no job id."""
    if not job_id:
        return None
    with _RUNTIME_STATUS_LOCK:
        writer = _RUNTIME_STATUS_WRITERS.get(job_id)
        if writer is None:
            writer = _RuntimeStatusWriter(job_id)
            _RUNTIME_STATUS_WRITERS[job_id] = writer
        return writer


def flush_runtime_status(job_id=None):
    """Write every pending status update now (terminal boundaries, shutdown)."""
    with _RUNTIME_STATUS_LOCK:
        if job_id:
            writers = [_RUNTIME_STATUS_WRITERS[job_id]] if job_id in _RUNTIME_STATUS_WRITERS else []
        else:
            writers = list(_RUNTIME_STATUS_WRITERS.values())
    for writer in writers:
        writer.flush()


def reset_runtime_status(job_id=None):
    """Drop the pending status writers (a task reached a terminal boundary)."""
    with _RUNTIME_STATUS_LOCK:
        if job_id is None:
            writers = list(_RUNTIME_STATUS_WRITERS.values())
            _RUNTIME_STATUS_WRITERS.clear()
        else:
            writer = _RUNTIME_STATUS_WRITERS.pop(job_id, None)
            writers = [writer] if writer is not None else []
    for writer in writers:
        writer.close()


def runtime_status_stats():
    with _RUNTIME_STATUS_LOCK:
        return {
            "writers": len(_RUNTIME_STATUS_WRITERS),
            "submits": _RUNTIME_STATUS_STATS["submits"],
            "flushes": _RUNTIME_STATUS_STATS["flushes"],
            "coalesced": _RUNTIME_STATUS_STATS["coalesced"],
            "dropped_terminal": _RUNTIME_STATUS_STATS["dropped_terminal"],
        }


def _stage_progress_counter(stage_progress):
    """The "how many units are done" number of a stage progress payload."""
    if not isinstance(stage_progress, dict):
        return None
    counter = stage_progress.get("completed")
    try:
        return int(counter)
    except (TypeError, ValueError):
        return None


def _submit_runtime_status(job_id, **changes):
    """Hand a batch-progress status update to the merged status writer.

    Falls back to the synchronous path when there is no writer (no job id,
    or the writer was closed), so the behaviour never depends on whether the
    writer exists.
    """
    writer = _runtime_status_writer(job_id)
    if writer is None:
        return {}
    return writer.submit(**changes)


def _runtime_telemetry_request_started(job_id):
    coalescer = _runtime_telemetry_coalescer(job_id)
    if coalescer is not None:
''', r'''


def _runtime_telemetry_request_started(job_id):
    coalescer = _runtime_telemetry_coalescer(job_id)
    if coalescer is not None:
''')

patch("core.py", r'''
def update_runtime_state(job_id, *, event=None, progress=False, heartbeat=False,
                         event_name=None, event_metadata=None,
                         event_visibility=None, event_category=None,
                         event_logged=False, **changes):
    """Atomically merge a worker status update into runtime_state.json.

    ``event_logged=True`` says the event has already been appended to
    ``runtime_events.jsonl`` by the caller (the merged status writer does
    that, so the activity log keeps every event while the state file is
    written once per window).
    """
    if not job_id:
        return _runtime_defaults()
    now = _utc_now_iso()
''', r'''
def update_runtime_state(job_id, *, event=None, progress=False, heartbeat=False,
                         event_name=None, event_metadata=None,
                         event_visibility=None, event_category=None, **changes):
    """Atomically merge a worker status update into runtime_state.json."""
    if not job_id:
        return _runtime_defaults()
    now = _utc_now_iso()
''')

patch("core.py", r'''                        current["events"] = current["events"][-24:]
                else:
                    current["last_technical_event"] = message
                if not duplicate and not event_logged:
                    _append_runtime_event(
                        job_id,
                        {"event": event_name, "message": message,
''', r'''                        current["events"] = current["events"][-24:]
                else:
                    current["last_technical_event"] = message
                if not duplicate:
                    _append_runtime_event(
                        job_id,
                        {"event": event_name, "message": message,
''')

patch("core.py", r'''    if stage_info["stage"] != "pipeline":
        changes.update({key: stage_info[key] for key in (
            "stage", "stage_id", "operation", "operation_id", "operation_label")})
    # 字幕是展示状态：合并写，落盘时仍然由 ``update_runtime_state`` 执行
    # 取消/暂停的覆盖规则。
    _submit_runtime_status(job_id, progress=True, event=message, status="running",
                           event_visibility="user", event_category="progress", **changes)
    if _runtime_cancel_requested(job_id):
        raise RuntimeError("任务已请求取消")

''', r'''    if stage_info["stage"] != "pipeline":
        changes.update({key: stage_info[key] for key in (
            "stage", "stage_id", "operation", "operation_id", "operation_label")})
    update_runtime_state(job_id, progress=True, event=message, status="running",
                         event_visibility="user", event_category="progress", **changes)
    if _runtime_cancel_requested(job_id):
        raise RuntimeError("任务已请求取消")

''')

patch("core.py", r'''                on_caption=lambda text: _runtime_caption_callback(job_id, text),
            )
        result_state = load_job_state(job_id) or state
        # 终态之前先把合并中的批次进度写掉并关闭这条通道：否则一次迟到的状态写
        # 会把 "completed" 覆盖回 "running"。
        flush_runtime_status(job_id)
        reset_runtime_status(job_id)
        if _runtime_cancel_requested(job_id):
            # An exit stop reuses the cancel boundary but must publish a
            # resumable ``interrupted`` state, never "the user cancelled".
''', r'''                on_caption=lambda text: _runtime_caption_callback(job_id, text),
            )
        result_state = load_job_state(job_id) or state
        if _runtime_cancel_requested(job_id):
            # An exit stop reuses the cancel boundary but must publish a
            # resumable ``interrupted`` state, never "the user cancelled".
''')

patch("core.py", r'''    # lease 永不过期 → 应用还活着时这个任务会**真的硬卡死**（状态一直停在活跃态，
    # start_job_worker / resume_job 都会拒绝）。这里必须失败闭合。
    except BaseException as exc:  # noqa: BLE001 - worker must publish failure to UI
        # 失败也是终态：先落盘并关闭合并中的状态通道，再读取/写终态。
        flush_runtime_status(job_id)
        reset_runtime_status(job_id)
        runtime_after = load_runtime_state(job_id)
        exit_requested = bool(runtime_after.get("exit_requested"))
        cancelled = (not exit_requested) and (
''', r'''    # lease 永不过期 → 应用还活着时这个任务会**真的硬卡死**（状态一直停在活跃态，
    # start_job_worker / resume_job 都会拒绝）。这里必须失败闭合。
    except BaseException as exc:  # noqa: BLE001 - worker must publish failure to UI
        runtime_after = load_runtime_state(job_id)
        exit_requested = bool(runtime_after.get("exit_requested"))
        cancelled = (not exit_requested) and (
''')

patch("core.py", r'''        try:
            flush_runtime_telemetry(job_id)
            reset_runtime_telemetry(job_id)
            flush_runtime_status(job_id)
            reset_runtime_status(job_id)
            _llm_transport.close_thread_clients()
            try:
                update_runtime_state(
''', r'''        try:
            flush_runtime_telemetry(job_id)
            reset_runtime_telemetry(job_id)
            _llm_transport.close_thread_clients()
            try:
                update_runtime_state(
''')

patch("folith/snapshots.py", r'''import tempfile
from .job_lock import exclusive
import zipfile
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple


SNAPSHOT_DIR = "delivery_snapshots"
''', r'''import tempfile
from .job_lock import exclusive
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


SNAPSHOT_DIR = "delivery_snapshots"
''')

patch("folith/snapshots.py", r'''
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: Frozen delivery assets are immutable, but their integrity is re-checked on
#: every task-list refresh (the frozen-delivery status is part of every task's
#: label).  Hashing the same megabytes again and again cost more than reading
#: them, so the digest is remembered per (path, mtime, size).
_ASSET_HASH_CACHE: "OrderedDict[Tuple[str, int, int], str]" = OrderedDict()
_ASSET_HASH_CACHE_MAX = 64


def _sha256_cached(path: Path, data: bytes) -> str:
    """The SHA-256 of a file, remembered while the file stays unchanged."""
    try:
        stat = path.stat()
    except OSError:
        return _sha256(data)
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _ASSET_HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = _sha256(data)
    if len(_ASSET_HASH_CACHE) >= _ASSET_HASH_CACHE_MAX:
        _ASSET_HASH_CACHE.popitem(last=False)
    _ASSET_HASH_CACHE[key] = digest
    return digest


def translation_truth_hash(state: Mapping[str, Any]) -> str:
''', r'''
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def translation_truth_hash(state: Mapping[str, Any]) -> str:
''')

patch("folith/snapshots.py", r'''
def state_identity(state: Dict[str, Any]) -> str:
    """Hash the approved translation state without snapshot bookkeeping."""
    # The keys below are the only ones that are dropped from the hash, so the
    # copy only has to be shallow.  A full ``deepcopy`` of a book-length state
    # cost ~100 ms and was paid on every task-list refresh (the frozen
    # delivery status is part of every task's label).
    # QA confirmations, impact explanations and artifact bookkeeping are
    # review metadata, not the frozen document bytes.  They must not make an
    # otherwise identical frozen snapshot appear divergent after a reviewer
    # records a visual/Word check.
    value = {key: item for key, item in (state or {}).items()
             if key not in ("_source_bin", "delivery_snapshots",
                            "latest_delivery_snapshot_version", "final_qa",
                            "dependency_impact", "snapshot_revision")}
    academic = value.get("academic_state")
    if isinstance(academic, dict):
        value["academic_state"] = {key: item for key, item in academic.items()
                                   if key != "artifact_status"}
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return _sha256(payload.encode("utf-8"))
''', r'''
def state_identity(state: Dict[str, Any]) -> str:
    """Hash the approved translation state without snapshot bookkeeping."""
    value = deepcopy(state)
    # QA confirmations, impact explanations and artifact bookkeeping are
    # review metadata, not the frozen document bytes.  They must not make an
    # otherwise identical frozen snapshot appear divergent after a reviewer
    # records a visual/Word check.
    for key in ("_source_bin", "delivery_snapshots",
                "latest_delivery_snapshot_version", "final_qa",
                "dependency_impact", "snapshot_revision"):
        value.pop(key, None)
    academic = value.get("academic_state")
    if isinstance(academic, dict):
        academic.pop("artifact_status", None)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return _sha256(payload.encode("utf-8"))
''')

patch("folith/snapshots.py", r'''    except OSError:
        return None
    expected = manifest.get("artifact_hashes", {}).get(name)
    # Export/read-back must re-hash the bytes it just read.  The status cache
    # is only for the task-list integrity probe; using it here would accept a
    # same-size rewrite with a restored mtime.
    return data if expected == _sha256(data) else None


def asset_intact(job_root: Path, version: int, name: str,
                 manifest: Optional[Mapping[str, Any]] = None) -> bool:
    """Is the frozen asset still byte-identical to what the manifest records?

    The digest is remembered per (path, mtime, size), so an unchanged asset is
    neither read nor hashed again on every task-list refresh.
    """
    if manifest is None:
        manifest = next((item for item in list_snapshots(job_root)
                         if item.get("snapshot_version") == int(version)), None)
    if manifest is None:
        return False
    expected = (manifest.get("artifact_hashes") or {}).get(name)
    if not expected:
        return False
    path = snapshot_root(job_root) / f"v{int(version)}" / name
    try:
        stat = path.stat()
    except OSError:
        return False
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    cached = _ASSET_HASH_CACHE.get(key)
    if cached is not None:
        return cached == expected
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return _sha256_cached(path, data) == expected


def archive(job_root: Path, version: int) -> Optional[bytes]:
''', r'''    except OSError:
        return None
    expected = manifest.get("artifact_hashes", {}).get(name)
    return data if expected == _sha256(data) else None


def archive(job_root: Path, version: int) -> Optional[bytes]:
''')

patch("folith/engine/delivery_state.py", r'''    state = state if state is not None else _c.load_job_state(job_id)
    current_identity = _snapshots.state_identity(state or {})
    matches = current_identity == latest.get("translation_state_identity")
    integrity = delivery_snapshot_assets_intact(job_id, latest["snapshot_version"])
    return {
        "latest": latest,
        "current": bool(matches and integrity and state
''', r'''    state = state if state is not None else _c.load_job_state(job_id)
    current_identity = _snapshots.state_identity(state or {})
    matches = current_identity == latest.get("translation_state_identity")
    assets = delivery_snapshot_assets(job_id, latest["snapshot_version"])
    integrity = bool(assets) and all(data is not None for data in assets.values())
    return {
        "latest": latest,
        "current": bool(matches and integrity and state
''')

patch("folith/engine/delivery_state.py", r'''    }
    _LIGHT_STATUS_CACHE[str(job_root)] = (key, status)
    return dict(status)


def delivery_snapshot_assets_intact(job_id, version) -> bool:
    """Are the frozen assets still exactly the ones the manifest records?

    The assets themselves are not read here: the digests are remembered per
    (path, mtime, size), so an unchanged snapshot is verified without reading
    and hashing megabytes on every task-list refresh.
    """
    manifest = next((item for item in list_delivery_snapshots(job_id)
                     if item.get("snapshot_version") == int(version)), None)
    if manifest is None:
        return False
    names = [item.get("name") for item in manifest.get("assets") or []
             if item.get("name")]
    if not names:
        return False
    root = _c.job_dir(job_id)
    return all(_snapshots.asset_intact(root, int(version), name, manifest)
               for name in names)


def delivery_snapshot_assets(job_id, version):
''', r'''    }
    _LIGHT_STATUS_CACHE[str(job_root)] = (key, status)
    return dict(status)


def delivery_snapshot_assets(job_id, version):
''')

patch("folith/qt/adapter.py", r'''
    return _events.activity_rows(
        core.read_runtime_events(job_id, limit, visibility="user"))


def workbench_models(job_id: str, state: dict, *,
                     count_occurrences: bool = False) -> dict:
    """The derived, read-only projections the workbench renders.

    Every one of these walks the whole document: the word count, the follow-up
    facts, the review queue, the delivery gate.  The workbench re-reads them
    whenever the task state changes -- which, during a translation run, is
    once per committed batch -- so computing them on the GUI thread turned
    every background batch commit into a visible freeze (measured: up to
    270 ms of uninterrupted GUI-thread work per refresh).

    They are pure functions of the task state, so the poll reads them on a
    worker thread and hands the results to the view.  The view still owns
    every widget; only the *data* moves off the GUI thread.  A projection that
    fails is left out, so the view computes it (and handles the failure)
    itself.

    The delivery gate is deliberately *not* part of this bundle: it is the
    most expensive projection and is refreshed on its own, slower cadence
    (see ``MainWindow._poll_workbench``).
    """
    from folith import word_count as _word_count

    rows = segment_rows(job_id, state)
    models: dict = {"rows": rows}
    for name, build in (
            ("word_count", lambda: _word_count.document_count(rows)),
            ("runtime", lambda: runtime_view(job_id, state)),
            ("snapshots", lambda: delivery_snapshots(job_id)),
            ("followup", lambda: followup_view(job_id, state)),
            ("review", lambda: review_queue(job_id, state)),
            ("terms", lambda: glossary_view(
                job_id, state, count_occurrences=count_occurrences)),
            ("activity", lambda: activity_events(job_id))):
        try:
            models[name] = build()
        except Exception:  # noqa: BLE001 - one failed projection must not hide the rest
            continue
    return models


def source_path(job_id: str) -> Path | None:
''', r'''
    return _events.activity_rows(
        core.read_runtime_events(job_id, limit, visibility="user"))


def source_path(job_id: str) -> Path | None:
''')

patch("folith/qt/views.py", r'''        self._dirty = False
        self._last_runtime: dict = {}
        self._snapshots: list[dict] = []
        #: The derived, read-only projections that arrive with the state (they
        #: are computed on a worker thread because each walks the document).
        self._models: dict = {}
        #: True while the worker thread owns the expensive projections.
        self._delivery_owned_by_background = False
        self._delivery_rendered = False
        self._delivery_preview: dict = {}
        self._delivery_preview_at = 0.0
''', r'''        self._dirty = False
        self._last_runtime: dict = {}
        self._snapshots: list[dict] = []
        self._delivery_rendered = False
        self._delivery_preview: dict = {}
        self._delivery_preview_at = 0.0
''')

patch("folith/qt/views.py", r'''
    def apply(self, job_id: str, state: dict, rows: list[adapter.SegmentRow],
              runtime: dict, snapshots: list[dict],
              *, refresh_delivery: bool = False, prepared: dict | None = None,
              models: dict | None = None) -> None:
        """Refresh everything that is *not* the text the user is typing.

        ``models`` carries the derived, read-only projections (word count,
        follow-up facts, review queue, activity log, delivery gate).  They are
        computed on a worker thread because each of them walks the whole
        document; without them the GUI thread froze for up to 270 ms on every
        background batch commit.  When they are absent the view computes them
        itself, exactly as before.
        """
        self.job_id = job_id
        self._state = state
        self._last_runtime = runtime or {}
        self._snapshots = snapshots or []
        self._models = dict(models or {})
        # True when the derived projections come from the worker thread: the
        # view then never computes an expensive projection itself.
        self._delivery_owned_by_background = models is not None
        self._prepared_review = (state, prepared["review"]) if prepared and "review" in prepared else None
        self._prepared_delivery = (state, prepared["delivery"]) if prepared and "delivery" in prepared else None
        # The window hands back the very same state and row objects while neither
''', r'''
    def apply(self, job_id: str, state: dict, rows: list[adapter.SegmentRow],
              runtime: dict, snapshots: list[dict],
              *, refresh_delivery: bool = False, prepared: dict | None = None) -> None:
        """Refresh everything that is *not* the text the user is typing."""
        self.job_id = job_id
        self._state = state
        self._last_runtime = runtime or {}
        self._snapshots = snapshots or []
        self._prepared_review = (state, prepared["review"]) if prepared and "review" in prepared else None
        self._prepared_delivery = (state, prepared["delivery"]) if prepared and "delivery" in prepared else None
        # The window hands back the very same state and row objects while neither
''')

patch("folith/qt/views.py", r'''        if key == self._followup_key:
            return
        self._followup_key = key
        view = self._models.get("followup")
        if view is None:
            try:
                view = adapter.followup_view(self.job_id, self._state)
            except Exception:  # noqa: BLE001 - 收尾页坏了不能拖垮翻译页
                view = {}
        self.followup_panel.apply(view)
        # 顶栏图标与抽屉说同一件事：抽屉里有几张 ✕ / ⚠ 卡，角标就是几。
        count = self.followup_panel.issue_count()
''', r'''        if key == self._followup_key:
            return
        self._followup_key = key
        try:
            view = adapter.followup_view(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 收尾页坏了不能拖垮翻译页
            view = {}
        self.followup_panel.apply(view)
        # 顶栏图标与抽屉说同一件事：抽屉里有几张 ✕ / ⚠ 卡，角标就是几。
        count = self.followup_panel.issue_count()
''')

patch("folith/qt/views.py", r'''        signature = f"{self.job_id}|{adapter.activity_signature(self.job_id)}"
        if signature == self.activity_panel.signature():
            return
        rows = self._models.get("activity")
        if rows is None:
            try:
                rows = adapter.activity_events(self.job_id)
            except Exception:  # noqa: BLE001 - 活动页坏了不能拖垮翻译页
                rows = []
        self.activity_panel.set_rows(rows[-50:], signature=signature)

    def _render_terms(self, *, force: bool = False) -> None:
''', r'''        signature = f"{self.job_id}|{adapter.activity_signature(self.job_id)}"
        if signature == self.activity_panel.signature():
            return
        try:
            rows = adapter.activity_events(self.job_id)
        except Exception:  # noqa: BLE001 - 活动页坏了不能拖垮翻译页
            rows = []
        self.activity_panel.set_rows(rows[-50:], signature=signature)

    def _render_terms(self, *, force: bool = False) -> None:
''')

patch("folith/qt/views.py", r'''        on_page = force or self.terms_open()
        if on_page:
            self._terms_key = key
        view = self._models.get("terms")
        if view is None:
            try:
                view = adapter.glossary_view(self.job_id, self._state,
                                             count_occurrences=on_page)
            except Exception:  # noqa: BLE001 - 术语页坏了不能拖垮翻译页
                view = {}
        self.terms_panel.apply(self.job_id, view, force=force)
        self._sync_terms_gate(view)

''', r'''        on_page = force or self.terms_open()
        if on_page:
            self._terms_key = key
        try:
            view = adapter.glossary_view(self.job_id, self._state,
                                         count_occurrences=on_page)
        except Exception:  # noqa: BLE001 - 术语页坏了不能拖垮翻译页
            view = {}
        self.terms_panel.apply(self.job_id, view, force=force)
        self._sync_terms_gate(view)

''')

patch("folith/qt/views.py", r'''            prepared = getattr(self, "_prepared_review", None)
            self._prepared_review = None
            view = (prepared[1] if prepared is not None and prepared[0] is self._state
                    else self._models.get("review"))
            if view is None:
                view = adapter.review_queue(self.job_id, self._state)
        except Exception:  # noqa: BLE001 - 审校页坏了不能拖垮翻译页
            view = {}
        self._review_items = list(view.get("queue_items") or [])
''', r'''            prepared = getattr(self, "_prepared_review", None)
            self._prepared_review = None
            view = (prepared[1] if prepared is not None and prepared[0] is self._state
                    else adapter.review_queue(self.job_id, self._state))
        except Exception:  # noqa: BLE001 - 审校页坏了不能拖垮翻译页
            view = {}
        self._review_items = list(view.get("queue_items") or [])
''')

patch("folith/qt/views.py", r'''    def _render_segments(self) -> None:
        """按可见集合更新网格；重建时按稳定段身份恢复选中，不触碰编辑器。"""
        self._sync_segment_placeholder()
        previous_rows = self._rows
        self._rows = self._visible_rows()
        old_count = self.segment_list.rowCount()
        new_count = len(self._rows)
        # Rows only appended at the end (a translation run committing a batch):
        # the existing cells stay, so the grid is not rebuilt and re-selected.
        append_only = (0 < old_count < new_count
                       and len(previous_rows) >= old_count
                       and all(previous_rows[position].segment_uid
                               == self._rows[position].segment_uid
                               for position in range(old_count)))
        if old_count != new_count and append_only:
            self.segment_list.blockSignals(True)
            try:
                self.segment_list.setRowCount(new_count)
                for position in range(old_count, new_count):
                    self.segment_list.setItem(position, 0, QTableWidgetItem(""))
                    self.segment_list.setItem(position, 1, QTableWidgetItem(""))
                    self.segment_list.setItem(position, 2, QTableWidgetItem(""))
            finally:
                self.segment_list.blockSignals(False)
        elif old_count != new_count:
            self.segment_list.blockSignals(True)
            self._painted.clear()  # 下面会换成全新的空单元格，旧缓存不再对应屏幕内容
            # 行全部重建：展开行要在选回当前段之后重新展开（位置可能变了）。
            self._active_position = -1
            self.row_delegate.active_row = -1
            self.segment_list.setRowCount(new_count)
            # 重建之后必须立刻把内容画上去：否则要等到下一次后台刷新才显示文字，
            # 中间会看到一片空白行（打开任务或段落数变化时最明显）。
            self._queue_segment_paint(schedule_heights=False)
''', r'''    def _render_segments(self) -> None:
        """按可见集合更新网格；重建时按稳定段身份恢复选中，不触碰编辑器。"""
        self._sync_segment_placeholder()
        self._rows = self._visible_rows()
        if self.segment_list.rowCount() != len(self._rows):
            self.segment_list.blockSignals(True)
            self._painted.clear()  # 下面会换成全新的空单元格，旧缓存不再对应屏幕内容
            # 行全部重建：展开行要在选回当前段之后重新展开（位置可能变了）。
            self._active_position = -1
            self.row_delegate.active_row = -1
            self.segment_list.setRowCount(len(self._rows))
            # 重建之后必须立刻把内容画上去：否则要等到下一次后台刷新才显示文字，
            # 中间会看到一片空白行（打开任务或段落数变化时最明显）。
            self._queue_segment_paint(schedule_heights=False)
''')

patch("folith/qt/views.py", r'''        key = (self.job_id, id(self._state), len(self._all_rows))
        if key != self._word_count_key:
            self._word_count_key = key
            count = self._models.get("word_count")
            if count is None:
                count = word_count.document_count(self._all_rows)
            self._word_count = count
        count = self._word_count
        if count is None or not count.total.segments:
            self.word_total_label.setText("")
''', r'''        key = (self.job_id, id(self._state), len(self._all_rows))
        if key != self._word_count_key:
            self._word_count_key = key
            self._word_count = word_count.document_count(self._all_rows)
        count = self._word_count
        if count is None or not count.total.segments:
            self.word_total_label.setText("")
''')

patch("folith/qt/views.py", r'''        key = (self.job_id, len(self._snapshots),
               str(self._state.get("delivery_status") or ""))
        unchanged = self._delivery_rendered and key == self._delivery_key
        same_job = bool(self._delivery_rendered and self._delivery_key
                        and self._delivery_key[0] == self.job_id)
        # A fresh preview from the worker thread is painted as it arrives: the
        # worker's own cadence (see ``_DeliveryPreviewCache``) is the throttle.
        fresh = self._models.get("delivery")
        if fresh is not None:
            prepared = (self._state, fresh)
        else:
            # Validating every pair is the expensive part (~0.2 s on a book): while
            # the state object is the one already validated only a slow safety
            # re-check (files changed outside the state) is worth the GUI thread.
            same_state = self._delivery_state is self._state
            interval = DELIVERY_IDLE_REFRESH_SECONDS if same_state else DELIVERY_REFRESH_SECONDS
            if unchanged and not force and (now - self._delivery_preview_at) < interval:
                return
            if unchanged and (now - self._delivery_preview_at) < DELIVERY_FORCE_FLOOR_SECONDS:
                return
        if (fresh is None and prepared is None
                and self._delivery_owned_by_background):
            # The worker owns the delivery preview and had nothing new for this
            # poll: keep showing the last one (never another task's).
            if not same_job:
                preview = {}
            else:
                if not force and (now - self._delivery_preview_at) < DELIVERY_REFRESH_SECONDS:
                    return
                if (now - self._delivery_preview_at) < DELIVERY_FORCE_FLOOR_SECONDS:
                    return
                preview = self._delivery_preview or {}
        else:
            preview = (prepared[1] if prepared is not None and prepared[0] is self._state
                       else adapter.delivery_preview(self.job_id, self.config, state=self._state)
                       if self.job_id else {})
        self._delivery_preview = preview
        self._delivery_rendered = True
        self._delivery_preview_at = now
''', r'''        key = (self.job_id, len(self._snapshots),
               str(self._state.get("delivery_status") or ""))
        unchanged = self._delivery_rendered and key == self._delivery_key
        # Validating every pair is the expensive part (~0.2 s on a book): while the
        # state object is the one already validated only a slow safety re-check
        # (files changed outside the state) is worth the GUI thread's time.
        same_state = self._delivery_state is self._state
        interval = DELIVERY_IDLE_REFRESH_SECONDS if same_state else DELIVERY_REFRESH_SECONDS
        if unchanged and not force and (now - self._delivery_preview_at) < interval:
            return
        if unchanged and (now - self._delivery_preview_at) < DELIVERY_FORCE_FLOOR_SECONDS:
            return
        preview = (prepared[1] if prepared is not None and prepared[0] is self._state
                   else adapter.delivery_preview(self.job_id, self.config, state=self._state)
                   if self.job_id else {})
        self._delivery_preview = preview
        self._delivery_rendered = True
        self._delivery_preview_at = now
''')

patch("folith/qt/window.py", r'''import dataclasses
import os
import sys
import threading
import time
import weakref
from pathlib import Path
''', r'''import dataclasses
import os
import sys
import time
import weakref
from pathlib import Path
''')

patch("folith/qt/window.py", r'''            except Exception:  # noqa: BLE001 - a log failure must never break the UI path
                pass
        return total


class _DeliveryPreviewCache:
    """Worker-owned delivery-preview cache.

    The preview walks the whole document, so it is refreshed on its own
    slower cadence.  The window must not expose mutable cache fields to the
    worker thread; this object owns them and serialises access.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job_id = ""
        self._model: dict = {}
        self._at = 0.0

    def refresh(self, job_id: str, config, state: dict) -> dict | None:
        with self._lock:
            if job_id != self._job_id:
                self._job_id = job_id
                self._model = {}
                self._at = 0.0
            now = time.monotonic()
            if now - self._at < views_module.DELIVERY_REFRESH_SECONDS:
                return None
            model = adapter.delivery_preview(job_id, config, state=state)
            self._model = model
            self._at = now
            return model


class _NullProgress:
''', r'''            except Exception:  # noqa: BLE001 - a log failure must never break the UI path
                pass
        return total


class _NullProgress:
''')

patch("folith/qt/window.py", r'''        self._rows_cache: tuple | None = None  # (job_id+drafts 签名, state 对象, rows)
        self._cached_state: dict = {}
        self._cached_signature = ""
        #: The delivery gate is the most expensive projection (it walks the
        #: whole document), so it is refreshed on its own, slower cadence --
        #: on the worker thread, never on the GUI thread.
        self._delivery_cache = _DeliveryPreviewCache()
        #: ``(state, models)`` the last poll computed on the worker thread; only
        #: valid while ``state`` is still the cached state object.
        self._cached_models: tuple | None = None
        #: 「译前准备」的目录（`adapter.setup_choices`）在一个进程里只需读一次。
        self._setup_choices_loaded = False
        #: 本次运行里已经自动做过智能画像的任务（进入「准备」时自动做一次，不重复收费）。
''', r'''        self._rows_cache: tuple | None = None  # (job_id+drafts 签名, state 对象, rows)
        self._cached_state: dict = {}
        self._cached_signature = ""
        #: 「译前准备」的目录（`adapter.setup_choices`）在一个进程里只需读一次。
        self._setup_choices_loaded = False
        #: 本次运行里已经自动做过智能画像的任务（进入「准备」时自动做一次，不重复收费）。
''')

patch("folith/qt/window.py", r'''            self._home_counter += 1
            if self._home_counter >= HOME_REFRESH_SECONDS:
                self._home_counter = 0
                self._refresh_home_async()
                self.refresh_pending_badge()

    @staticmethod
''', r'''            self._home_counter += 1
            if self._home_counter >= HOME_REFRESH_SECONDS:
                self._home_counter = 0
                self.refresh_home()
                self.refresh_pending_badge()

    @staticmethod
''')

patch("folith/qt/window.py", r'''        cached_at_start = self._cached_signature
        self._state_load_in_flight = True

        count_occurrences = self.workbench.terms_open()

        def read():
            state = adapter.load_state(job_id)
            if not state:
                return state, None
            models = adapter.workbench_models(
                job_id, state, count_occurrences=count_occurrences)
            # The delivery preview walks the whole document, the most expensive
            # projection: it refreshes on its own cadence and the other polls
            # keep the last result (the panel only repaints on change).
            models["delivery"] = self._delivery_cache.refresh(
                job_id, self.config, state)
            return state, models

        def done(payload) -> None:
            self._state_load_in_flight = False
''', r'''        cached_at_start = self._cached_signature
        self._state_load_in_flight = True

        def read():
            state = adapter.load_state(job_id)
            try:
                review = adapter.review_queue(job_id, state) if state else {}
            except Exception:  # noqa: BLE001 - match the panel's projection boundary
                review = {}  # a failed review projection must not hide committed pairs
            return state, {"review": review}

        def done(payload) -> None:
            self._state_load_in_flight = False
''')

patch("folith/qt/window.py", r'''            # still preparing its review queue. Keep that newer GUI snapshot.
            if self._cached_signature not in (cached_at_start, signature):
                return
            state, models = payload
            if not state or self._cached_signature == signature:
                # Unreadable (refresh reports it) or already refreshed meanwhile.
                self.refresh_workbench()
                return
            self._cached_state = state
            self._cached_signature = signature
            self._cached_models = (state, models)
            self.refresh_workbench(preloaded=True, models=models)

        def failed(_message: str, _detail: str) -> None:
            self._state_load_in_flight = False
''', r'''            # still preparing its review queue. Keep that newer GUI snapshot.
            if self._cached_signature not in (cached_at_start, signature):
                return
            state, prepared = payload
            if not state or self._cached_signature == signature:
                # Unreadable (refresh reports it) or already refreshed meanwhile.
                self.refresh_workbench()
                return
            self._cached_state = state
            self._cached_signature = signature
            self.refresh_workbench(preloaded=True, prepared=prepared)

        def failed(_message: str, _detail: str) -> None:
            self._state_load_in_flight = False
''')

patch("folith/qt/window.py", r'''                           label="poll-state")

    def refresh_workbench(self, *, force: bool = False, preloaded: bool = False,
                          prepared: dict | None = None,
                          models: dict | None = None) -> None:
        if self._opening_job_id:
            return
        job_id = self._current_job_id
''', r'''                           label="poll-state")

    def refresh_workbench(self, *, force: bool = False, preloaded: bool = False,
                          prepared: dict | None = None) -> None:
        if self._opening_job_id:
            return
        job_id = self._current_job_id
''')

patch("folith/qt/window.py", r'''        timing = _PhaseLog(self._log, "refresh_workbench", job_id, min_ms=self.SLOW_REFRESH_MS)
        try:
            self._refresh_workbench(job_id, timing, force=force, preloaded=preloaded,
                                    prepared=prepared, models=models)
        finally:
            timing.flush()
            self._hide_loading()
''', r'''        timing = _PhaseLog(self._log, "refresh_workbench", job_id, min_ms=self.SLOW_REFRESH_MS)
        try:
            self._refresh_workbench(job_id, timing, force=force, preloaded=preloaded,
                                    prepared=prepared)
        finally:
            timing.flush()
            self._hide_loading()
''')

patch("folith/qt/window.py", r'''    SLOW_REFRESH_MS = 150

    def _refresh_workbench(self, job_id: str, timing: _PhaseLog, *, force: bool,
                           preloaded: bool, prepared: dict | None = None,
                           models: dict | None = None) -> None:
        signature = adapter.state_signature(job_id)
        changed = preloaded or signature != self._cached_signature
        if (changed or force) and not preloaded:
''', r'''    SLOW_REFRESH_MS = 150

    def _refresh_workbench(self, job_id: str, timing: _PhaseLog, *, force: bool,
                           preloaded: bool, prepared: dict | None = None) -> None:
        signature = adapter.state_signature(job_id)
        changed = preloaded or signature != self._cached_signature
        if (changed or force) and not preloaded:
''')

patch("folith/qt/window.py", r'''        if changed or force:
            with timing.phase("setup_panel"):
                self._load_setup(job_id, state)
        if models is None:
            cached = self._cached_models
            if (not changed and not force and cached is not None
                    and cached[0] is state):
                # The state did not change: the models the last poll computed
                # are still valid, so the worker thread keeps owning those
                # whole-document projections.
                models = cached[1]
        else:
            models = dict(models)
        with timing.phase("segment_rows"):
            rows = models.get("rows") if models is not None else None
            if rows is None:
                rows = self._segment_rows(job_id, state)
        # Leaving "prepare" for a large task builds the whole grid: say so first.
        if ((changed or force) and len(rows) >= self.LOADING_SEGMENTS
                and getattr(self.workbench, "_showing_setup", False)):
            self._show_loading(i18n.tr('正在载入译文…'))
        with timing.phase("runtime_view"):
            runtime = models.get("runtime") if models is not None else None
            if runtime is None:
                runtime = adapter.runtime_view(job_id, state)
        with timing.phase("delivery_snapshots"):
            snapshots = models.get("snapshots") if models is not None else None
            if snapshots is None:
                snapshots = adapter.delivery_snapshots(job_id) if (changed or force) \
                    else self.workbench._snapshots
        # The delivery panel is re-validated on an explicit refresh, or when the
        # run reaches a terminal state — not on every background state write.
        status = str(runtime.get("status") or "")
''', r'''        if changed or force:
            with timing.phase("setup_panel"):
                self._load_setup(job_id, state)
        with timing.phase("segment_rows"):
            rows = self._segment_rows(job_id, state)
        # Leaving "prepare" for a large task builds the whole grid: say so first.
        if ((changed or force) and len(rows) >= self.LOADING_SEGMENTS
                and getattr(self.workbench, "_showing_setup", False)):
            self._show_loading(i18n.tr('正在载入译文…'))
        with timing.phase("runtime_view"):
            runtime = adapter.runtime_view(job_id, state)
        with timing.phase("delivery_snapshots"):
            snapshots = adapter.delivery_snapshots(job_id) if (changed or force)                 else self.workbench._snapshots
        # The delivery panel is re-validated on an explicit refresh, or when the
        # run reaches a terminal state — not on every background state write.
        status = str(runtime.get("status") or "")
''')

patch("folith/qt/window.py", r'''        self._last_runtime_status = status
        with timing.phase("workbench.apply"):
            self.workbench.apply(job_id, state, rows, runtime, snapshots,
                                 refresh_delivery=force or terminal_change, prepared=prepared,
                                 models=models)
        self._sync_menu_state()
        # 情境提示等布局与过渡落定之后再挑（锚点要在屏幕上的最终位置）。
        self._later(600, lambda: self._offer_tips(runtime))
''', r'''        self._last_runtime_status = status
        with timing.phase("workbench.apply"):
            self.workbench.apply(job_id, state, rows, runtime, snapshots,
                                 refresh_delivery=force or terminal_change, prepared=prepared)
        self._sync_menu_state()
        # 情境提示等布局与过渡落定之后再挑（锚点要在屏幕上的最终位置）。
        self._later(600, lambda: self._offer_tips(runtime))
''')


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: revert_to_baseline <target-dir>", file=sys.stderr)
        return 2
    target = Path(argv[0]).resolve()
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for entry in ROOT.iterdir():
        if entry.name in SKIP:
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache"))
        else:
            shutil.copy2(entry, destination)
    applied = 0
    for relative, current, original in PATCHES:
        path = target / relative
        text = path.read_text(encoding="utf-8")
        if current not in text:
            print(f"  MISS {relative}: {current[:60]!r}", file=sys.stderr)
            continue
        path.write_text(text.replace(current, original, 1), encoding="utf-8")
        applied += 1
    if applied != len(PATCHES):
        print(f"reverted only {applied}/{len(PATCHES)} edits into {target}; "
              "refusing to mark an incomplete baseline", file=sys.stderr)
        return 1
    (target / BASELINE_MARKER).write_text(
        f"reverted {applied}/{len(PATCHES)} edits\n", encoding="utf-8")
    print(f"reverted {applied}/{len(PATCHES)} edits into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
