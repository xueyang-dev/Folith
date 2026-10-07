"""Crash-safe batch checkpoints and translation-memory reconciliation."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import critical_path as _critical_path
from . import translation_target
from .textual import has_textual_content


EVENTS_FILE = "events.jsonl"

#: Per-process "the parent directory exists" hints, per parent path.
_DIR_READY: set = set()
_DIR_LOCK = threading.Lock()


def events_path(job_root: Path) -> Path:
    return Path(job_root) / EVENTS_FILE


def reset_dir_cache() -> None:
    """Forget the per-process directory hints (tests, tmpdir moves)."""
    with _DIR_LOCK:
        _DIR_READY.clear()


def _ensure_dir(path: Path) -> None:
    """Create the parent directory once per process, then trust the cache.

    ``mkdir(parents=True, exist_ok=True)`` is idempotent but not free: it walks
    the whole path again on every call, and this ran on *every* workflow event
    (several per batch, so tens of thousands of times per long document).  The
    cache is the same guard ``state_journal._ensure_dir`` already uses, and it
    is dropped on failure so a directory removed underneath us is recreated
    rather than silently swallowing the append.
    """
    parent = str(path.parent)
    with _DIR_LOCK:
        if parent in _DIR_READY:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _DIR_LOCK:
        _DIR_READY.add(parent)


def _append_once(path: Path, event: Dict[str, Any]) -> None:
    """One append attempt: encode/open/write/flush, then the disk barrier."""
    stream = None
    with _critical_path.phase("audit_event_append") as _sink:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        stream = path.open("a", encoding="utf-8")
        stream.write(payload)
        stream.flush()
        _sink.written(len(payload))
    try:
        with _critical_path.phase("audit_event_fsync"):
            os.fsync(stream.fileno())
    finally:
        stream.close()


def append_event(job_root: Path, event: Dict[str, Any]) -> None:
    """Append one durable, JSON-serializable workflow event.

    This runs several times per batch and ends in an ``fsync``, so the append
    and the disk barrier are timed separately: without that split a durable
    *sequence* of small writes is indistinguishable from a slow disk.
    """
    path = events_path(job_root)
    _ensure_dir(path)
    try:
        _append_once(path, event)
    except OSError:
        # The cached directory vanished (or was never creatable).  Recreate it
        # and retry exactly once -- the same recovery the uncached
        # `mkdir(exist_ok=True)`-per-call version got for free.
        with _DIR_LOCK:
            _DIR_READY.discard(str(path.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
        with _DIR_LOCK:
            _DIR_READY.add(str(path.parent))
        _append_once(path, event)
    _critical_path.count("audit_event_fsync_count")


def read_events(job_root: Path) -> List[Dict[str, Any]]:
    path = events_path(job_root)
    if not path.is_file():
        return []
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                events.append(value)
    except OSError:
        return []
    return events


def recovery_summary(job_root: Path, state: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize durable progress for the UI without changing resume semantics."""
    root = Path(job_root)
    events = read_events(root)
    started: Dict[int, Dict[str, Any]] = {}
    committed = set()
    last_completed_stage = "尚未开始"
    phase_labels = {
        "generation_done": "批次翻译完成",
        "deterministic_qa_done": "自动质量检查完成",
        "semantic_review_done": "独立审校完成",
        "semantic_review_skipped": "独立审校已跳过",
        "knowledge_feedback_done": "知识反馈完成",
        "knowledge_feedback_skipped": "知识反馈已跳过（本地触发条件未满足）",
        "knowledge_feedback_cache_hit": "知识反馈复用（相同请求已付费）",
        "state_commit_done": "进度已保存",
        "tm_promotion_done": "翻译记忆已同步",
    }
    for event in events:
        batch = event.get("batch")
        if isinstance(batch, int) and batch >= 0:
            if event.get("phase") == "generation_started":
                started[batch] = event
            elif event.get("phase") == "state_commit_done":
                committed.add(batch)
        label = phase_labels.get(event.get("phase"))
        if label:
            last_completed_stage = label

    all_batches = set(started) | committed
    total_batches = max(all_batches) + 1 if all_batches else 0
    completed_batches = sorted(committed)
    reviewed_batches = int((state.get("review_stats") or {}).get("batches_reviewed") or 0)
    if reviewed_batches:
        total_batches = max(total_batches, reviewed_batches)
    pairs = state.get("pairs") or []
    current = None
    for batch in sorted(started):
        event = started[batch]
        offset = event.get("offset")
        segment_count = event.get("segment_count")
        if not isinstance(offset, int) or not isinstance(segment_count, int):
            continue
        saved_in_batch = max(0, min(segment_count, len(pairs) - offset))
        if batch not in committed or saved_in_batch < segment_count:
            current = {
                "number": batch + 1,
                "start_segment": offset,
                "end_segment": offset + segment_count - 1,
                "completed_segments": saved_in_batch,
                "segment_count": segment_count,
                "regenerate_segments": segment_count - saved_in_batch,
            }
            break

    durable_times = []
    for name in ("state.json", EVENTS_FILE):
        try:
            durable_times.append((root / name).stat().st_mtime)
        except OSError:
            pass
    last_saved_at = None
    if durable_times:
        last_saved_at = datetime.fromtimestamp(max(durable_times)).astimezone().isoformat(
            timespec="seconds")

    stage = str(state.get("stage") or "")
    if last_completed_stage == "尚未开始":
        last_completed_stage = {
            "TERMS_PREPARED": "术语准备完成",
            "GLOSSARY_FROZEN": "术语已确认",
            "TRANSLATING": "术语已确认",
            "TRANSLATED": "批次翻译完成",
            "REVIEW_REQUIRED": "独立审校完成",
            "FINAL": "交付已确认",
        }.get(stage, last_completed_stage)
    can_resume = bool(current) or (
        bool(state.get("p1_done")) and not state.get("p2_done")
        and stage in {"GLOSSARY_FROZEN", "TRANSLATING"}
    )
    return {
        "auto_save_enabled": True,
        "last_saved_at": last_saved_at,
        "completed_batches": completed_batches,
        "completed_batch_count": max(len(completed_batches), reviewed_batches),
        "total_batches": total_batches,
        "current_batch": current,
        "can_resume": can_resume,
        "last_completed_stage": last_completed_stage,
        "recovered_tm_entries": int(state.get("tm_recovered_count") or 0),
        "event_count": len(events),
    }


def _eligible(source: str, target: str) -> bool:
    return has_textual_content(source) \
        and bool(str(target or "").strip()) \
        and not translation_target.is_translation_transport_wrapper(target)


def _state_entries(
    state: Dict[str, Any], scope: Optional[Callable[..., Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    entries = {}
    for pair in state.get("pairs") or []:
        if not pair.get("reviewed") or pair.get("stale_due_to_glossary"):
            continue
        source, target = pair.get("source"), pair.get("target")
        if not _eligible(source, target):
            continue
        scoped = scope(str(source), str(target)) if scope else None
        if scope is not None:
            if not scoped:
                continue
            key, entry = scoped
        else:
            key, entry = str(source), {"target": str(target), "reviewed": True}
        entries[key] = entry
    return entries


def reconcile_translation_memory(
    tm: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    job_root: Path,
    scope: Optional[Callable[..., Any]] = None,
) -> Tuple[bool, int]:
    """Recover accepted state entries and pending TM promotions after restart.

    `scope(source, target)` 返回 `(记忆键, 记录)`，由调用方把目标语言等**作用域
    信息**编进键里；返回假值表示这条记忆的作用域无法确定（例如目标语言未知），
    此时**不写入**。默认（`scope=None`）保持旧的"以原文为键"行为，只给不关心
    作用域的调用方用。
    """
    desired = _state_entries(state, scope)
    initial_tm = dict(tm)
    recovered_sources = set()
    events = read_events(job_root)
    promoted_batches = {event.get("batch") for event in events
                        if event.get("phase") in {"tm_promoted", "tm_promotion_done"}}
    for event in events:
        if event.get("phase") != "tm_promotion_pending":
            continue
        if event.get("batch") in promoted_batches:
            continue
        for item in event.get("entries") or []:
            source, target = item.get("source"), item.get("target")
            if not _eligible(source, target):
                continue
            scoped = scope(str(source), str(target)) if scope else None
            if scope is not None:
                if not scoped:
                    continue
                key, entry = scoped
            else:
                key, entry = str(source), {"target": str(target), "reviewed": True}
            desired[key] = entry
            if initial_tm.get(key) != entry:
                recovered_sources.add(key)
    changed = False
    for key, entry in desired.items():
        if tm.get(key) != entry:
            tm[key] = entry
            changed = True
    return changed, len(recovered_sources)


def batch_entries(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {"source": str(pair.get("source") or ""),
         "target": str(pair.get("target") or "")}
        for pair in pairs
        if pair.get("reviewed") and not pair.get("stale_due_to_glossary")
        and _eligible(pair.get("source"), pair.get("target"))
    ]
