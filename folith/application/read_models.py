"""Canonical, frontend-neutral job read models.

One job identity, one status representation, one progress representation and one
set of user-available actions — resolved here, once, and consumed by every
surface (Qt workbench, Streamlit, CLI).

Design notes
------------
* This module **composes existing core reads** (``core.read_runtime_view``,
  ``core.build_job_runtime_view``, ``core.recovery_summary``,
  ``core.task_status_label``, ``folith.storage.job_repository``).  It is a read
  model, not a second storage layer: it never writes, never migrates and never
  re-implements runtime semantics.
* Job identity is the **directory name**, exactly like the core's own storage.
  A cached ``summary.json`` is a cache; when its embedded ``job_id`` disagrees
  with the directory it is discarded rather than trusted.
* A task whose state cannot be parsed is still listed, with an explicit
  ``unreadable`` marker — it is never silently hidden.
* The list composes the *cheap* reads (the same ones the desktop client used
  before Phase 3), so it adds no extra scans; the full runtime view is only
  built for a single job when a surface actually needs it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: Runtime status given to a task directory whose state cannot be parsed.
UNREADABLE_STATUS = "unreadable"
UNREADABLE_LABEL = "无法读取（数据可能损坏）"
UNREADABLE_ERROR = "状态文件无法解析；任务目录仍在磁盘上，可以删除或手动检查。"

_LABEL_CACHE: Dict[Tuple[str, str, str], str] = {}
_LABEL_CACHE_LIMIT = 400


@dataclass(frozen=True)
class JobSummary:
    """One row of the canonical job list."""

    job_id: str
    filename: str
    project_id: Optional[str]
    target_lang: str
    status: str
    status_label: str
    progress_label: str
    stage: str
    segment_count: int
    updated_at: str
    active: bool
    available_actions: Tuple[str, ...] = ()
    primary_action: Optional[str] = None
    snapshot_version: Optional[int] = None
    diverged: bool = False
    unreadable: bool = False
    error: str = ""
    progress_percent: int | None = None
    attention_count: int = 0
    attention_tone: str = ""
    runtime: Mapping[str, Any] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        """True only when the runtime itself reports completion."""
        return self.status == "completed"

    @property
    def title(self) -> str:
        return self.filename or self.job_id


@dataclass(frozen=True)
class JobRuntimeView:
    """The canonical frontend-facing runtime view of one job.

    ``payload`` is the underlying ``core.build_job_runtime_view`` mapping, kept
    for presentation helpers that need fields this contract does not (yet)
    model — e.g. ``stage_progress`` or the user event list.  It is a
    presentation passthrough; the fields above it are the stable contract.
    """

    job_id: str
    status: str
    status_label: str
    stage: str
    progress_done: Optional[int]
    progress_total: Optional[int]
    message: str
    detail: str
    available_actions: Tuple[str, ...]
    primary_action: Optional[str]
    error: str
    retryable: bool
    updated_at: Optional[str]
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def active(self) -> bool:
        return self.status in _active_statuses()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _core():
    import core

    return core


def _active_statuses() -> frozenset:
    return frozenset(getattr(_core(), "RUNTIME_ACTIVE_STATUSES", ()))


def _output_dir(output_dir: Optional[Path] = None) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    return Path(_core().OUTPUT_DIR)


def _label_cache_key(job_id: str, summary: Mapping[str, Any], signature: str) -> Tuple[str, str, str]:
    return (job_id, signature, str(summary.get("progress_label") or ""))


def _status_label(job_id: str, summary: Mapping[str, Any], signature: str) -> str:
    """Authored once, for every surface: the task's user-facing status label.

    Delegates to ``core.task_status_label`` (which already folds in the delivery
    snapshot) behind a small bounded memo, because the label is recomputed for
    every row on every list refresh and the snapshot check touches disk.
    """
    if "status_label" in summary:
        return str(summary["status_label"])
    core = _core()
    key = _label_cache_key(job_id, summary, signature)
    cached = _LABEL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        state = core.load_job_state(job_id)
        label = core.task_status_label(state or {}, job_id) if state \
            else str(summary.get("progress_label") or "待处理")
    except Exception as exc:  # noqa: BLE001 - a label must never break the list
        label = f"{summary.get('progress_label') or '待处理'}（状态读取失败：{exc}）"
    if len(_LABEL_CACHE) > _LABEL_CACHE_LIMIT:
        _LABEL_CACHE.clear()
    _LABEL_CACHE[key] = label
    return label


def _expensive_marker_cache_clear() -> None:
    """Drop the label memo (used by tests and after bulk mutations)."""
    _LABEL_CACHE.clear()


def _cached_summary(job_id: str, entry: Path) -> Optional[Dict[str, Any]]:
    """The cached summary for one job directory, or ``None`` when unusable.

    The directory name is the authority: a cache whose embedded id disagrees is
    discarded (its document name, segment count and language would describe a
    different task).
    """
    import json

    summary_path = entry / "summary.json"
    if not summary_path.is_file():
        return None
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a broken cache falls back to state
        return None
    if not isinstance(data, dict) or not data:
        return None
    if str(data.get("job_id") or "") != job_id:
        return None
    # Pre-P6 summaries lack these facts. Use the existing read-only state
    # fallback until the next normal save writes the current summary shape.
    if "attention_count" not in data:
        return _rebuilt_summary(job_id) or data
    return data


def _rebuilt_summary(job_id: str) -> Optional[Dict[str, Any]]:
    core = _core()
    state = core.load_job_state(job_id)
    if not state:
        return None
    from folith.storage import job_repository

    summary = job_repository.build_job_summary(job_id, state)
    summary["job_id"] = job_id
    return summary


def _unreadable_summary(job_id: str, entry: Path) -> JobSummary:
    stamp = ""
    try:
        state_path = entry / "state.json"
        summary_path = entry / "summary.json"
        newest = max(
            state_path.stat().st_mtime if state_path.is_file() else 0.0,
            summary_path.stat().st_mtime if summary_path.is_file() else 0.0,
        )
        stamp = datetime.fromtimestamp(newest).astimezone().isoformat(timespec="seconds")
    except OSError:
        stamp = ""
    return JobSummary(
        job_id=job_id,
        filename=f"（无法读取的任务 {job_id[:8]}）",
        project_id=None,
        target_lang="",
        status=UNREADABLE_STATUS,
        status_label=UNREADABLE_LABEL,
        progress_label="",
        stage="",
        segment_count=0,
        updated_at=stamp,
        active=False,
        available_actions=(),
        primary_action=None,
        unreadable=True,
        error=UNREADABLE_ERROR,
    )


#: job_id -> (signature of the task directory's files, last_saved_at).
_LAST_SAVED: Dict[str, Tuple[Any, str]] = {}


def _job_files_signature(job_id: str) -> Any:
    """Names, sizes and mtimes of the files directly in the task directory.

    ``last_saved_at`` is derived from the state and the durable checkpoint
    artifacts, all of which live there; a write to any of them changes this.
    """
    try:
        folder = _core().job_dir(job_id)
        return tuple(sorted(
            (entry.name, entry.stat().st_mtime_ns, entry.stat().st_size)
            for entry in os.scandir(folder) if entry.is_file()))
    except OSError:
        return None


def _last_saved_at(job_id: str) -> str:
    """The core's "last saved" value, recomputed only when the task's files change.

    Computing it loads the whole state (≈0.2 s for a large book); the task list
    is refreshed on every return to it, mostly with nothing changed.
    """
    signature = _job_files_signature(job_id)
    cached = _LAST_SAVED.get(job_id)
    if signature is not None and cached is not None and cached[0] == signature:
        return cached[1]
    core = _core()
    try:
        state = core.load_job_state(job_id) or {}
        updated = str(core.recovery_summary(job_id, state).get("last_saved_at") or "")
    except Exception:  # noqa: BLE001 - the list must still render
        return ""
    if signature is not None:
        _LAST_SAVED[job_id] = (signature, updated)
    return updated


def _summary_row(job_id: str, summary: Mapping[str, Any]) -> JobSummary:
    core = _core()
    updated = str(summary.get("updated_at") or "")
    if not updated and "attention_count" in summary:
        from folith.checkpoint import durable_saved_at
        updated = durable_saved_at(core.job_dir(job_id)) or ""
    if not updated and "attention_count" not in summary:
        # A persisted state does not carry its own timestamp; the core's
        # authoritative "last saved" value comes from the durable artifacts.
        updated = _last_saved_at(job_id)
    try:
        runtime = core.read_runtime_view(job_id)
    except Exception:  # noqa: BLE001
        runtime = {}
    status = str(runtime.get("runtime_status")
                 or summary.get("runtime_status") or "idle")
    snapshot_version: Optional[int] = None
    diverged = False
    try:
        snapshot = core.delivery_snapshot_status(job_id)
        latest = snapshot.get("latest") or {}
        snapshot_version = latest.get("snapshot_version")
        diverged = bool(snapshot.get("diverged"))
    except Exception:  # noqa: BLE001 - snapshot state is decoration for the list
        pass
    signature = f"{updated}|{summary.get('segment_count')}|{status}"
    error = runtime.get("error")
    if isinstance(error, Mapping):
        error = error.get("message") or ""
    label = _status_label(job_id, summary, signature)
    if snapshot_version is not None:
        label = (f"工作版本已偏离冻结交付 v{snapshot_version}" if diverged else
                 f"已冻结交付 v{snapshot_version}") if snapshot.get("current") or diverged else label
    count = int(summary.get("attention_count") or 0)
    tone = str(summary.get("attention_tone") or "")
    if summary.get("recovery_attention") and status not in _active_statuses():
        count += 1
        tone = tone or "warning"
    return JobSummary(
        job_id=job_id,
        filename=str(summary.get("filename") or ""),
        project_id=summary.get("project_id"),
        target_lang=str(summary.get("target_lang") or ""),
        status=status,
        status_label=label,
        progress_label=str(summary.get("progress_label") or ""),
        stage=str(summary.get("stage") or ""),
        segment_count=int(summary.get("segment_count") or 0),
        updated_at=updated,
        active=status in _active_statuses(),
        available_actions=tuple(core.runtime_actions_for_status(status)),
        primary_action=core.primary_runtime_action_for_status(status),
        snapshot_version=snapshot_version,
        diverged=diverged,
        error=str(error or ""),
        progress_percent=summary.get("progress_percent"),
        attention_count=count,
        attention_tone=tone,
        runtime=runtime.get("runtime") or {},
    )


def _sort_key(row: JobSummary):
    return row.updated_at or ""


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

def list_jobs(*, output_dir: Optional[Path] = None) -> List[JobSummary]:
    """The canonical job list: every task directory, including unreadable ones."""
    out = _output_dir(output_dir)
    try:
        entries = sorted(out.iterdir()) if out.is_dir() else []
    except OSError:
        return []

    rows: List[JobSummary] = []
    known: set = set()
    candidates: List[Path] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        candidates.append(entry)
        job_id = entry.name
        summary = _cached_summary(job_id, entry) or _rebuilt_summary(job_id)
        if summary is None:
            continue
        known.add(job_id)
        rows.append(_summary_row(job_id, summary))

    for entry in candidates:
        if entry.name in known:
            continue
        state_path = entry / "state.json"
        summary_path = entry / "summary.json"
        if not (state_path.is_file() or summary_path.is_file()):
            continue  # not a task directory
        rows.append(_unreadable_summary(entry.name, entry))

    # Active tasks first, then most recently updated; the second sort is stable
    # so the active grouping survives as the tie-break.
    rows.sort(key=lambda row: (not row.active, _sort_key(row)))
    rows.sort(key=_sort_key, reverse=True)
    return rows


def get_job(job_id: str, *, output_dir: Optional[Path] = None) -> Optional[JobSummary]:
    """One canonical summary row, or ``None`` when the task does not exist."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return None
    entry = _output_dir(output_dir) / job_id
    summary = _cached_summary(job_id, entry) or _rebuilt_summary(job_id)
    if summary is not None:
        return _summary_row(job_id, summary)
    state_path = entry / "state.json"
    summary_path = entry / "summary.json"
    if state_path.is_file() or summary_path.is_file():
        return _unreadable_summary(job_id, entry)
    return None


def get_job_runtime(job_id: str, state: Optional[Mapping[str, Any]] = None) -> JobRuntimeView:
    """The canonical runtime view of one job (progress, actions, message)."""
    core = _core()
    state = state if isinstance(state, Mapping) else core.load_job_state(job_id)
    payload = core.build_job_runtime_view(job_id, dict(state) if state else state)
    status = str(payload.get("status") or "idle")
    error = payload.get("runtime", {}).get("error") if isinstance(
        payload.get("runtime"), Mapping) else None
    if isinstance(error, Mapping):
        error = error.get("message") or ""
    actions = tuple(payload.get("available_actions") or ())
    return JobRuntimeView(
        job_id=str(job_id),
        status=status,
        status_label=str(payload.get("status_label") or status),
        stage=str(payload.get("current_operation") or payload.get("surface_label") or ""),
        progress_done=(int(payload["progress_completed"])
                       if payload.get("progress") is not None else None),
        progress_total=(int(payload["progress_total"])
                        if payload.get("progress") is not None else None),
        message=str(payload.get("headline") or ""),
        detail=str(payload.get("detail") or ""),
        available_actions=actions,
        primary_action=payload.get("primary_action"),
        error=str(error or ""),
        retryable="retry" in actions,
        updated_at=payload.get("last_activity_at"),
        payload=payload,
    )


def get_available_actions(job_id: str, state: Optional[Mapping[str, Any]] = None) -> Tuple[str, ...]:
    """The actions a user may take on this job, from the one action policy."""
    core = _core()
    state = state if isinstance(state, Mapping) else core.load_job_state(job_id)
    payload = core.build_job_runtime_view(job_id, dict(state) if state else state)
    return tuple(payload.get("available_actions") or ())


def actions_for_status(status: str) -> Tuple[str, ...]:
    """The canonical action policy for a raw runtime status."""
    return tuple(_core().runtime_actions_for_status(status))


def job_has_live_worker(job_id: str) -> bool:
    """True when a worker holds this task's lifetime lock right now."""
    core = _core()
    try:
        return bool(core.is_job_worker_alive(job_id))
    except Exception:  # noqa: BLE001 - liveness probe must never raise to a UI
        return False


def job_is_active(job_id: str) -> bool:
    """True when the task is in one of the core's active runtime statuses."""
    core = _core()
    try:
        return bool(core.job_is_active(job_id))
    except Exception:  # noqa: BLE001
        return False
