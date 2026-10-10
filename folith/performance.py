"""Low-overhead, file-backed pipeline timing instrumentation.

The profiler is intentionally independent from the workflow state machine.  A
stage can record timing data without changing whether the stage succeeds,
fails, or is resumed.  It is safe to call from OCR/LLM worker threads and
keeps the persisted payload small enough to be useful as an audit artifact.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_float(value: Any) -> float:
    try:
        return round(max(0.0, float(value or 0.0)), 6)
    except (TypeError, ValueError):
        return 0.0


class StageHandle:
    """Mutable handle for one stage, including concurrent item timings."""

    def __init__(self, profiler: "PipelineProfiler", name: str, record: Dict[str, Any]):
        self.profiler = profiler
        self.name = name
        self.record = record
        self.started_monotonic = time.perf_counter()
        self.record["started_monotonic"] = self.started_monotonic
        self._finished = False

    def item(
        self,
        item_id: Any,
        started_monotonic: float,
        *,
        finished_monotonic: Optional[float] = None,
        queue_wait_s: float = 0.0,
        api_latency_s: float = 0.0,
        retry_count: int = 0,
        status: str = "completed",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        finished = finished_monotonic or time.perf_counter()
        item = {
            "item_id": str(item_id),
            "started_monotonic": started_monotonic,
            "finished_monotonic": finished,
            "wall_time_s": _safe_float(finished - started_monotonic),
            "queue_wait_s": _safe_float(queue_wait_s),
            "api_latency_s": _safe_float(api_latency_s),
            "retry_count": max(0, int(retry_count or 0)),
            "status": str(status or "completed"),
        }
        if metadata:
            item["metadata"] = dict(metadata)
        with self.profiler._lock:
            self.record["items"].append(item)
            self.record["completed_item_count"] = len(self.record["items"])
            self.record["queue_wait_s"] += item["queue_wait_s"]
            self.record["api_latency_s"] += item["api_latency_s"]
            self.record["retry_count"] += item["retry_count"]

    def metadata(self, **values: Any) -> None:
        with self.profiler._lock:
            self.record.setdefault("metadata", {}).update(values)

    def finish(self, *, status: str = "completed", persist: bool = True,
               **metadata: Any) -> Dict[str, Any]:
        """Close the stage. ``persist=False`` lets a caller that closes several
        stages together write the artifact once instead of once per stage."""
        if self._finished:
            return self.record
        with self.profiler._lock:
            if self._finished:
                return self.record
            finished_monotonic = time.perf_counter()
            self.record["finished_at"] = _now_iso()
            self.record["finished_monotonic"] = finished_monotonic
            self.record["wall_time_s"] = _safe_float(
                finished_monotonic - self.started_monotonic
            )
            self.record["status"] = str(status or "completed")
            if metadata:
                self.record.setdefault("metadata", {}).update(metadata)
            items = list(self.record.get("items") or [])
            if items:
                for key in ("wall_time_s", "queue_wait_s", "api_latency_s"):
                    values = [_safe_float(item.get(key)) for item in items]
                    self.record.setdefault("per_item", {})[key] = {
                        "count": len(values),
                        "sum": _safe_float(sum(values)),
                        "avg": _safe_float(sum(values) / len(values)),
                        "min": _safe_float(min(values)),
                        "max": _safe_float(max(values)),
                    }
                # If per_item wall time sum is recorded and smaller than container wall time,
                # record active_wall_time_s to distinguish batch loop span from stage active duration
                item_wall_sum = self.record.get("per_item", {}).get("wall_time_s", {}).get("sum", 0.0)
                self.record["item_wall_sum_s"] = item_wall_sum
                intervals = sorted((item["started_monotonic"], item["finished_monotonic"])
                                   for item in items)
                union = 0.0
                left, right = intervals[0]
                for start, end in intervals[1:]:
                    if start > right:
                        union += max(0.0, right - left)
                        left, right = start, end
                    else:
                        right = max(right, end)
                self.record["interval_union_s"] = _safe_float(union + max(0.0, right - left))
                if item_wall_sum > 0:
                    self.record["active_wall_time_s"] = item_wall_sum
                else:
                    self.record["active_wall_time_s"] = self.record["wall_time_s"]
                completed_pages = sum(
                    1 for item in items
                    if str(item.get("metadata", {}).get("kind") or "") == "page"
                )
                completed_batches = sum(
                    1 for item in items
                    if str(item.get("metadata", {}).get("kind") or "") == "batch"
                )
                self.record["completed_item_count"] = len(items)
                self.record["completed_page_count"] = completed_pages
                self.record["completed_batch_count"] = completed_batches
                if not self.record.get("item_count"):
                    self.record["item_count"] = len(items)
                if not self.record.get("page_count"):
                    self.record["page_count"] = completed_pages
                if not self.record.get("batch_count"):
                    self.record["batch_count"] = completed_batches
            else:
                self.record["item_wall_sum_s"] = None
                self.record["interval_union_s"] = None
                self.record["active_wall_time_s"] = self.record["wall_time_s"]
            self._finished = True
        if persist:
            self.profiler.persist()
        return self.record

    def __enter__(self) -> "StageHandle":
        return self

    def __exit__(self, exc_type, _exc_value, _traceback) -> bool:
        self.finish(status="failed" if exc_type else "completed")
        return False


class PipelineProfiler:
    """Collect stage metrics with atomic persistence at stage boundaries."""

    def __init__(self, path: Optional[Path] = None, *, run_id: str = "") -> None:
        self.path = Path(path) if path else None
        self.run_id = str(run_id or "")
        self.started_at = _now_iso()
        self.started_monotonic = time.perf_counter()
        self._lock = threading.RLock()
        self._stages: Dict[str, Dict[str, Any]] = {}
        self._trace_stage_names = set()

    def start_stage(
        self,
        name: str,
        *,
        concurrency: int = 1,
        item_count: Optional[int] = None,
        page_count: Optional[int] = None,
        batch_count: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StageHandle:
        stage_name = str(name or "unknown")
        record: Dict[str, Any] = {
            "stage_name": stage_name,
            "started_at": _now_iso(),
            "finished_at": None,
            "wall_time_s": 0.0,
            "item_count": int(item_count or 0),
            "page_count": int(page_count or 0),
            "batch_count": int(batch_count or 0),
            "queue_wait_s": 0.0,
            "api_latency_s": 0.0,
            "retry_count": 0,
            "concurrency": max(1, int(concurrency or 1)),
            "status": "running",
            "metadata": dict(metadata or {}),
            "items": [],
        }
        with self._lock:
            self._stages[stage_name] = record
        return StageHandle(self, stage_name, record)

    def skipped(
        self, name: str, *, metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        stage = self.start_stage(name, concurrency=0, metadata=metadata)
        return stage.finish(status="skipped")

    def stage(self, name: str, **kwargs: Any) -> StageHandle:
        return self.start_stage(name, **kwargs)

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            record = self._stages.get(str(name))
            return dict(record) if record else None

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            stages = {name: dict(record) for name, record in self._stages.items()}
        finished = _now_iso()
        return {
            "version": VERSION,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": finished,
            "wall_time_s": _safe_float(time.perf_counter() - self.started_monotonic),
            "stages": stages,
        }

    def persist(self, path: Optional[Path] = None) -> Dict[str, Any]:
        target = Path(path) if path else self.path
        snapshot = self.snapshot()
        try:
            from folith.trace import trace
            run = trace.get_active_run()
            if run:
                for s_name, s_rec in snapshot.get("stages", {}).items():
                    if s_name not in run.stages or s_name in self._trace_stage_names:
                        run.stages[s_name] = {
                            "stage_name": s_name,
                            "first_started_at": s_rec.get("started_at"),
                            "last_finished_at": s_rec.get("finished_at"),
                            "wall_time_s": s_rec.get("active_wall_time_s", s_rec.get("wall_time_s", 0.0)),
                            "cpu_time_s": 0.0,
                            "spans_count": len(s_rec.get("items", [])),
                            "items_in": s_rec.get("item_count", 0),
                            "items_out": s_rec.get("completed_item_count", 0),
                            "status": s_rec.get("status", "success"),
                            "operations": {},
                            "metadata": dict(s_rec.get("metadata") or {}),
                        }
                        self._trace_stage_names.add(s_name)
        except Exception:
            pass
        if target is None:
            return snapshot
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(target)
        except OSError:
            # Timing must never turn a successful import into a failed import.
            pass
        return snapshot
