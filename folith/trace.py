"""Unified, lightweight performance tracing and observability engine for Folith.

Three-tier observability:
1. Human-readable Activity Logs: clean user-facing milestones.
2. Structured Run Events: machine-readable event stream with run_id, parent spans, timings.
3. Performance Trace: hierarchical spans, resource sampling, token tracking, percentiles,
   and automated agent-ready diagnostic reports.
"""
from __future__ import annotations

import atexit
import contextlib
import contextvars
import dataclasses
import hashlib
import json
import os
import platform
import sys
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union


try:
    import resource  # POSIX only; absent on Windows.
except ImportError:  # pragma: no cover - platform dependent
    resource = None  # type: ignore[assignment]


def _windows_rss_mb() -> float:
    """Working-set size on Windows, since ``resource`` does not exist there."""
    try:
        import ctypes
        from ctypes import wintypes

        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        ok = psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        if not ok:
            return 0.0
        return round(counters.WorkingSetSize / (1024 * 1024), 2)
    except Exception:
        return 0.0


class TraceLevel(str, Enum):
    OFF = "off"
    STANDARD = "standard"
    DETAILED = "detailed"

    @classmethod
    def from_env(cls, default: TraceLevel = STANDARD) -> TraceLevel:
        raw = os.environ.get("FOLITH_PERFORMANCE_TRACE") or os.environ.get("FOLIOTHREAD_PERFORMANCE_TRACE")
        if not raw:
            return default
        raw = str(raw).strip().lower()
        if raw in {"off", "0", "false", "none", "disable", "disabled"}:
            return cls.OFF
        if raw in {"detailed", "2", "full", "debug", "verbose"}:
            return cls.DETAILED
        return cls.STANDARD


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_float(val: Any, decimals: int = 4) -> float:
    try:
        return round(float(val or 0.0), decimals)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(val: Any) -> Optional[int]:
    """Provider-reported count: ``None`` stays unknown, it is never coerced to 0."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def aggregate_optional(records: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """Sum a nullable per-call field without treating unknown as zero.

    ``total`` is the sum over calls that reported the field (``None`` when no
    call did); ``complete`` is true only if every call reported it.
    """
    known = [r[key] for r in records if r.get(key) is not None]
    return {
        "total": sum(known) if known else None,
        "calls_reported": len(known),
        "calls_total": len(records),
        "complete": bool(records) and len(known) == len(records),
    }


def _fmt_optional_total(agg: Dict[str, Any]) -> str:
    if agg["total"] is None:
        return "unknown"
    if agg["complete"]:
        return f"{agg['total']:,}"
    return f"≥{agg['total']:,} (partial: {agg['calls_reported']}/{agg['calls_total']} calls reported)"


def generate_run_id() -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand_hex = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
    return f"run_{now_str}_{rand_hex}"


def sanitize_metadata(data: Any, max_str_len: int = 120) -> Any:
    """Sanitize metadata to avoid leaking large source/target texts and prevent bloated traces."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            key_str = str(k).lower()
            # If key explicitly relates to full text/prompt/content and value is large:
            if any(term in key_str for term in ("text", "source", "target", "prompt", "content", "body")) and isinstance(v, str):
                if len(v) > max_str_len:
                    h = hashlib.sha256(v.encode("utf-8", errors="replace")).hexdigest()[:12]
                    sanitized[k] = {
                        "char_count": len(v),
                        "hash": h,
                        "preview": v[:40] + "…",
                    }
                    continue
            sanitized[k] = sanitize_metadata(v, max_str_len=max_str_len)
        return sanitized
    elif isinstance(data, (list, tuple)):
        if len(data) > 50:
            return [sanitize_metadata(x, max_str_len=max_str_len) for x in data[:50]] + [f"… and {len(data) - 50} more items"]
        return [sanitize_metadata(x, max_str_len=max_str_len) for x in data]
    elif isinstance(data, str):
        if len(data) > max_str_len:
            h = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()[:12]
            return {
                "char_count": len(data),
                "hash": h,
                "preview": data[:40] + "…",
            }
        return data
    elif isinstance(data, (int, float, bool)) or data is None:
        return data
    else:
        return str(data)


def compute_percentiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "min": 0.0, "avg": 0.0, "count": 0}
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def _pct(p: float) -> float:
        idx = max(0, min(n - 1, int(round((p / 100.0) * (n - 1)))))
        return round(float(sorted_vals[idx]), 4)

    return {
        "count": n,
        "avg": round(sum(sorted_vals) / n, 4),
        "min": round(sorted_vals[0], 4),
        "max": round(sorted_vals[-1], 4),
        "p50": _pct(50),
        "p90": _pct(90),
        "p95": _pct(95),
        "p99": _pct(99),
    }


def _probe_gpu() -> Dict[str, Any]:
    """Gracefully probe GPU metrics without introducing heavy required dependencies."""
    try:
        import torch
        if torch.cuda.is_available():
            dev = torch.cuda.current_device()
            allocated = torch.cuda.memory_allocated(dev)
            reserved = torch.cuda.memory_reserved(dev)
            total = torch.cuda.get_device_properties(dev).total_memory
            return {
                "gpu_available": True,
                "backend": "cuda",
                "device_name": torch.cuda.get_device_name(dev),
                "vram_used_mb": round(allocated / (1024 * 1024), 1),
                "vram_reserved_mb": round(reserved / (1024 * 1024), 1),
                "vram_total_mb": round(total / (1024 * 1024), 1),
                "vram_utilization_pct": round((allocated / total) * 100.0, 1) if total else 0.0,
            }
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return {
                "gpu_available": True,
                "backend": "apple_mps",
                "device_name": "Apple Silicon MPS",
            }
    except Exception:
        pass
    return {"gpu_available": False}


class ResourceSampler:
    """Low-overhead background resource sampler (1~2 seconds interval)."""

    def __init__(self, log_path: Optional[Path] = None, interval: float = 1.5):
        self.log_path = Path(log_path) if log_path else None
        self.interval = max(0.5, float(interval))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_wall = time.perf_counter()
        self._last_proc = time.process_time()
        self._samples: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._file = None

        # Check for optional psutil
        try:
            import psutil
            self._psutil_proc = psutil.Process()
            self._has_psutil = True
        except ImportError:
            self._psutil_proc = None
            self._has_psutil = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self.log_path.open("a", encoding="utf-8")
        self._stop_event.clear()
        self._last_wall = time.perf_counter()
        self._last_proc = time.process_time()
        self._thread = threading.Thread(target=self._run_sampling, name="FolithResourceSampler", daemon=True)
        self._thread.start()

    def _sample_once(self, started_mono: float) -> Dict[str, Any]:
        now_wall = time.perf_counter()
        now_proc = time.process_time()
        wall_delta = max(1e-6, now_wall - self._last_wall)
        proc_delta = max(0.0, now_proc - self._last_proc)
        self._last_wall = now_wall
        self._last_proc = now_proc

        process_cpu_pct = round((proc_delta / wall_delta) * 100.0, 1)

        # Process RSS memory
        if self._has_psutil and self._psutil_proc:
            try:
                mem_info = self._psutil_proc.memory_info()
                rss_mb = round(mem_info.rss / (1024 * 1024), 2)
                sys_cpu_pct = round(self._psutil_proc.cpu_percent(interval=None), 1)
                import psutil
                sys_mem_pct = round(psutil.virtual_memory().percent, 1)
            except Exception:
                rss_mb = self._fallback_rss()
                sys_cpu_pct = None
                sys_mem_pct = None
        else:
            rss_mb = self._fallback_rss()
            sys_cpu_pct = None
            sys_mem_pct = None

        sample = {
            "timestamp": _now_iso(),
            "elapsed_s": round(now_wall - started_mono, 2),
            "process_cpu_pct": process_cpu_pct,
            "system_cpu_pct": sys_cpu_pct,
            "process_rss_mb": rss_mb,
            "system_memory_pct": sys_mem_pct,
            "thread_count": threading.active_count(),
            "gpu": _probe_gpu(),
        }
        return sample

    def _fallback_rss(self) -> float:
        if resource is None:
            return _windows_rss_mb()
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss = usage.ru_maxrss
            if sys.platform != "darwin":
                rss *= 1024
            return round(rss / (1024 * 1024), 2)
        except Exception:
            return 0.0

    def _run_sampling(self) -> None:
        started_mono = self._last_wall
        while not self._stop_event.is_set():
            try:
                sample = self._sample_once(started_mono)
                with self._lock:
                    self._samples.append(sample)
                    if len(self._samples) > 2000:
                        self._samples = self._samples[-1000:]
                if self._file:
                    self._file.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    self._file.flush()
            except Exception:
                pass
            self._stop_event.wait(self.interval)

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

        with self._lock:
            samples = list(self._samples)

        if not samples:
            # Take one final snapshot
            samples = [self._sample_once(self._last_wall)]

        cpu_vals = [s["process_cpu_pct"] for s in samples if s.get("process_cpu_pct") is not None]
        rss_vals = [s["process_rss_mb"] for s in samples if s.get("process_rss_mb") is not None]
        threads = [s["thread_count"] for s in samples if s.get("thread_count") is not None]

        summary = {
            "sample_count": len(samples),
            "process_cpu_avg_pct": round(sum(cpu_vals) / len(cpu_vals), 1) if cpu_vals else 0.0,
            "process_cpu_peak_pct": round(max(cpu_vals), 1) if cpu_vals else 0.0,
            "process_rss_avg_mb": round(sum(rss_vals) / len(rss_vals), 2) if rss_vals else 0.0,
            "process_rss_peak_mb": round(max(rss_vals), 2) if rss_vals else 0.0,
            "thread_peak": max(threads) if threads else 1,
            "gpu_detected": any(s.get("gpu", {}).get("gpu_available") for s in samples),
            "latest_sample": samples[-1] if samples else {},
        }
        return summary


class SpanHandle:
    """Thread-safe and exception-safe span context handle."""

    def __init__(
        self,
        run_context: "RunContext",
        stage: str,
        operation: str,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        items_in: int = 0,
        items_out: int = 0,
    ):
        self.run_context = run_context
        self.stage = stage
        self.operation = operation
        self.event_id = hashlib.sha256(os.urandom(8)).hexdigest()[:12]
        self.parent_id = parent_id
        self.metadata = dict(metadata or {})
        self.items_in = int(items_in or 0)
        self.items_out = int(items_out or 0)
        self.started_monotonic = time.perf_counter()
        self.started_cpu_time = time.process_time()
        self.started_at = _now_iso()
        self.finished_at: Optional[str] = None
        self.duration_ms: float = 0.0
        self.cpu_time_ms: float = 0.0
        self.status = "running"
        self.error_info: Optional[Dict[str, Any]] = None
        self._finished = False

    def add_metadata(self, **kwargs: Any) -> None:
        self.metadata.update(kwargs)

    def set_items(self, items_in: Optional[int] = None, items_out: Optional[int] = None) -> None:
        if items_in is not None:
            self.items_in = int(items_in)
        if items_out is not None:
            self.items_out = int(items_out)

    def finish(self, status: str = "success", error: Optional[BaseException] = None, **extra_metadata: Any) -> Dict[str, Any]:
        if self._finished:
            return self.to_dict()
        self._finished = True
        finished_mono = time.perf_counter()
        finished_cpu = time.process_time()
        self.finished_at = _now_iso()
        self.duration_ms = round((finished_mono - self.started_monotonic) * 1000.0, 3)
        self.cpu_time_ms = round((finished_cpu - self.started_cpu_time) * 1000.0, 3)
        self.status = status
        if extra_metadata:
            self.metadata.update(extra_metadata)
        if error is not None:
            self.status = "error"
            self.error_info = {
                "error_type": type(error).__name__,
                "message": str(error)[:300],
            }
            self.metadata["error"] = self.error_info
        if self.run_context.level == TraceLevel.OFF:
            return {}
        event_dict = self.to_dict()
        self.run_context._record_span_finish(self, event_dict)
        return event_dict

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_context.run_id,
            "task_id": self.run_context.job_id,
            "event_id": self.event_id,
            "parent_event_id": self.parent_id,
            "stage": self.stage,
            "operation": self.operation,
            "status": self.status,
            "timestamp": self.started_at,
            "monotonic_time": round(self.started_monotonic, 4),
            "duration_ms": self.duration_ms,
            "cpu_time_ms": self.cpu_time_ms,
            "items_in": self.items_in,
            "items_out": self.items_out,
            "metadata": sanitize_metadata(self.metadata),
        }

    def __enter__(self) -> "SpanHandle":
        stack = list(_ACTIVE_SPAN_STACK.get())
        stack.append(self.event_id)
        _ACTIVE_SPAN_STACK.set(stack)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        stack = list(_ACTIVE_SPAN_STACK.get())
        if stack and stack[-1] == self.event_id:
            stack.pop()
            _ACTIVE_SPAN_STACK.set(stack)
        if exc_type is not None:
            self.finish(status="error", error=exc_val)
        else:
            self.finish(status="success")
        return False  # Never suppress exception


_RUN_CONTEXT_VAR: contextvars.ContextVar[Optional["RunContext"]] = contextvars.ContextVar("folith_run_context", default=None)
_ACTIVE_SPAN_STACK: contextvars.ContextVar[List[str]] = contextvars.ContextVar("folith_span_stack", default=[])
_JOB_RUNS_LOCK = threading.RLock()
_ACTIVE_JOB_RUNS: Dict[str, "RunContext"] = {}


class RunContext:
    """Centralized runtime tracker for a single run of a task."""

    def __init__(
        self,
        job_id: str,
        run_id: Optional[str] = None,
        filename: str = "",
        file_type: str = "",
        file_size_bytes: int = 0,
        target_lang: str = "",
        output_dir: Optional[Path] = None,
        level: Optional[TraceLevel] = None,
    ):
        self.job_id = str(job_id)
        self.run_id = str(run_id or generate_run_id())
        self.filename = str(filename or "")
        self.file_type = str(file_type or Path(filename).suffix.lstrip(".").lower() if filename else "")
        self.file_size_bytes = int(file_size_bytes or 0)
        self.target_lang = str(target_lang or "")
        if isinstance(level, str):
            try:
                self.level = TraceLevel(level.lower())
            except ValueError:
                self.level = TraceLevel.STANDARD
        else:
            self.level = level or TraceLevel.from_env()
        self.output_dir = Path(output_dir) if output_dir else Path("outputs") / self.job_id

        # Timestamps
        self.started_at = _now_iso()
        self.started_monotonic = time.perf_counter()
        self.started_cpu_time = time.process_time()
        self.finished_at: Optional[str] = None
        self.total_wall_time_s: float = 0.0
        self.total_cpu_time_s: float = 0.0
        self.status = "running"
        self.failure_reason: Optional[str] = None

        # File paths
        self.perf_root = self.output_dir / ".performance"
        self.run_dir = self.perf_root / "runs" / self.run_id
        self.events_path = self.run_dir / "events.jsonl"
        self.requests_path = self.run_dir / "requests.jsonl"
        self.resources_path = self.run_dir / "resources.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.stages_path = self.run_dir / "stages.json"
        self.report_path = self.run_dir / "performance.md"
        self.legacy_perf_path = self.output_dir / "performance.json"
        self.history_path = self.perf_root / "history.json"

        # Locks and streaming handles
        self._lock = threading.RLock()
        self._events_file = None
        self._requests_file = None
        self._request_events_count = 0
        self._request_sequence = 0
        self._active_requests: Dict[int, Dict[str, Any]] = {}
        self._events_count = 0
        self._span_stage_map: Dict[str, str] = {}
        self._stage_intervals: Dict[str, List[Tuple[float, float]]] = {}
        self._sampler = ResourceSampler(self.resources_path, interval=1.5)

        # In-memory metrics accumulators
        self.dataset_meta: Dict[str, Any] = {
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size_bytes": self.file_size_bytes,
            "page_count": 0,
            "scanned_pages": 0,
            "ocr_pages": 0,
            "source_chars": 0,
            "source_words": 0,
            "paragraph_count": 0,
            "segment_count": 0,
            "target_lang": self.target_lang,
        }

        # Stage timeline: stage_name -> aggregated stats
        self.stages: Dict[str, Dict[str, Any]] = {}
        # Root stages in order of execution
        self.top_level_stages: List[str] = []

        # Model requests
        self.model_calls: List[Dict[str, Any]] = []

        # Segment & batch timings
        self.segment_durations: List[float] = []
        self.batch_records: List[Dict[str, Any]] = []

        # Cache & TM stats
        self.tm_stats = {
            "lookups": 0,
            "exact_hits": 0,
            "normalized_hits": 0,
            "misses": 0,
            "durations_ms": [],
        }
        self.terminology_stats = {
            "lookups": 0,
            "matches": 0,
            "durations_ms": [],
        }
        self.caches: Dict[str, Dict[str, int]] = {}

        # OCR pages stats
        self.ocr_page_records: List[Dict[str, Any]] = []

        # Errors & Retries
        self.errors: List[Dict[str, Any]] = []
        self.retries: List[Dict[str, Any]] = []
        self.warnings_count: int = 0

        # Concurrency observation
        self.concurrency_stats: Dict[str, Dict[str, int]] = {}

        # Start sampler if tracing is enabled
        if self.level != TraceLevel.OFF:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._events_file = self.events_path.open("a", encoding="utf-8")
            self._requests_file = self.requests_path.open("a", encoding="utf-8")
            self._sampler.start()

    def set_dataset_info(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if v is not None:
                    self.dataset_meta[k] = v

    def span(
        self,
        stage: str,
        operation: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        items_in: int = 0,
        items_out: int = 0,
    ) -> SpanHandle:
        stack = _ACTIVE_SPAN_STACK.get()
        parent_id = stack[-1] if stack else None
        handle = SpanHandle(
            run_context=self,
            stage=stage,
            operation=operation or stage,
            parent_id=parent_id,
            metadata=metadata,
            items_in=items_in,
            items_out=items_out,
        )
        with self._lock:
            self._span_stage_map[handle.event_id] = stage
            if parent_id is None and stage not in self.top_level_stages:
                self.top_level_stages.append(stage)
        return handle

    def _record_span_finish(self, span: SpanHandle, event_dict: Dict[str, Any]) -> None:
        with self._lock:
            if self._events_file:
                is_detailed = self.level == TraceLevel.DETAILED
                is_top = span.parent_id is None
                is_error = span.status == "error"
                is_batch = "batch" in span.operation.lower()
                if is_detailed or is_top or is_error or is_batch or span.duration_ms >= 5.0:
                    self._events_count += 1
                    try:
                        self._events_file.write(json.dumps(event_dict, ensure_ascii=False) + "\n")
                        # Periodic flush every 25 events or on error
                        if self._events_count % 25 == 0 or is_error:
                            self._events_file.flush()
                    except Exception:
                        pass

            # Record stage mapping for hierarchy awareness
            self._span_stage_map[span.event_id] = span.stage
            parent_stage = self._span_stage_map.get(span.parent_id) if span.parent_id else None
            is_stage_root = (parent_stage != span.stage)

            # Aggregate into stages
            stage_name = span.stage
            if stage_name not in self.stages:
                self.stages[stage_name] = {
                    "stage_name": stage_name,
                    "first_started_at": span.started_at,
                    "last_finished_at": span.finished_at,
                    "wall_time_s": 0.0,
                    "cpu_time_s": 0.0,
                    "spans_count": 0,
                    "items_in": 0,
                    "items_out": 0,
                    "status": "success",
                    "operations": {},
                }
                if span.parent_id is None and stage_name not in self.top_level_stages:
                    self.top_level_stages.append(stage_name)

            record = self.stages[stage_name]
            if is_stage_root:
                # wall_time_s is the union of (possibly concurrent) root spans,
                # recomputed in _finalize_throughputs; busy_time_s is their sum.
                record["wall_time_s"] = round(record["wall_time_s"] + (span.duration_ms / 1000.0), 4)
                record["busy_time_s"] = record["wall_time_s"]
                self._stage_intervals.setdefault(stage_name, []).append(
                    (span.started_monotonic, span.started_monotonic + span.duration_ms / 1000.0))
                record["cpu_time_s"] = round(record["cpu_time_s"] + (span.cpu_time_ms / 1000.0), 4)
            record["spans_count"] += 1
            record["items_in"] += span.items_in
            record["items_out"] += span.items_out
            record["last_finished_at"] = span.finished_at
            if span.status == "error":
                record["status"] = "error"

            op = span.operation or "default"
            op_rec = record["operations"].setdefault(op, {"count": 0, "wall_time_ms": 0.0, "cpu_time_ms": 0.0})
            op_rec["count"] += 1
            op_rec["wall_time_ms"] = round(op_rec["wall_time_ms"] + span.duration_ms, 3)
            op_rec["cpu_time_ms"] = round(op_rec["cpu_time_ms"] + span.cpu_time_ms, 3)

    def _write_request_event(self, event: Dict[str, Any]) -> None:
        if self._requests_file is None:
            return
        try:
            self._requests_file.write(json.dumps({"run_id": self.run_id, "task_id": self.job_id, **event}, ensure_ascii=False) + "\n")
            self._request_events_count += 1
            if self._request_events_count % 25 == 0 or event.get("status") == "error":
                self._requests_file.flush()
        except Exception:
            pass  # Diagnostics must not replace the provider/storage failure.

    def record_point(self, event: str, **fields: Any) -> None:
        """Persist a timing boundary; callers supply counts/identifiers only."""
        with self._lock:
            self._write_request_event({"event": event, "timestamp": _now_iso(),
                                       "monotonic": time.perf_counter(), **fields})

    def record_run_configuration(self, config: Dict[str, Any], *, resume: bool = False) -> None:
        if self._requests_file is None:
            return
        from folith.desktop.paths import app_version, build_id
        scalar_keys = (
            "target_lang", "auto_term", "enable_report", "enable_review", "enable_annotate",
            "use_tm", "strict_terminology_governance", "batch_size", "max_batch_chars",
            "enable_understanding", "enable_source_cleanup", "translation_concurrency",
            "knowledge_feedback_interval", "segmentation_mode", "reasoning_effort",
            "reasoning_mode", "auxiliary_reasoning_mode",
            "source_cleanup_concurrency", "source_cleanup_max_retries", "ocr_workers",
            "ocr_queue_size", "ocr_backend", "ocr_mode", "review_overlap", "max_review_in_flight",
        )
        snapshot = {key: config[key] for key in scalar_keys if key in config
                    and isinstance(config[key], (str, int, float, bool, type(None)))}
        for role in ("translator", "reviewer", "auxiliary"):
            role_config = config.get(role)
            if isinstance(role_config, dict):
                snapshot[role] = {key: role_config[key] for key in ("provider", "model", "reasoning_effort", "reasoning_mode")
                                  if isinstance(role_config.get(key), str)}
        self.record_point("run_configuration", configuration=snapshot, resume=resume,
                          app_version=app_version(), build_id=build_id(),
                          build_identity_scope="artifact_or_git_head_excludes_uncommitted_changes")

    def begin_http_attempt(self, request: Any) -> None:
        with self._lock:
            active = self._active_requests.get(threading.get_ident())
            if active is None:
                return
            attempt = active.get("http_attempts", 0) + 1
            active["http_attempts"] = attempt
            request.extensions["folith_trace_attempt"] = (active["logical_request_id"], attempt)
            self.record_point("http_attempt_started", logical_request_id=active["logical_request_id"],
                              attempt=attempt, role=active["role"], batch_id=active["batch_id"])

    def record_http_response(self, response: Any) -> None:
        identity = response.request.extensions.get("folith_trace_attempt")
        if identity is not None:
            self.record_point("http_response_headers", logical_request_id=identity[0],
                              attempt=identity[1], status_code=response.status_code)

    def begin_model_request(self, provider: str, model: str, *, role: str = "unknown", batch_id: Any = None, prompt_cost: Optional[Dict[str, Any]] = None, system_chars: Optional[int] = None,
                            user_chars: Optional[int] = None, segment_count: Optional[int] = None) -> None:
        if self._requests_file is None:
            return
        with self._lock:
            self._request_sequence += 1
            rec = {"logical_request_id": f"{self.run_id}/request/{self._request_sequence}", "started_at": _now_iso(),
                   "started_monotonic": time.perf_counter(), "provider": provider,
                   "model": model, "role": role, "batch_id": batch_id,
                   "system_chars": system_chars, "user_chars": user_chars,
                   "segment_count": segment_count}
            if prompt_cost is not None:
                rec["prompt_cost"] = dict(prompt_cost)
            self._active_requests[threading.get_ident()] = rec
            self._write_request_event({"event": "request_started", **rec,
                                       "job_in_flight": len(self._active_requests)})

    def end_model_request(self) -> None:
        with self._lock:
            rec = self._active_requests.pop(threading.get_ident(), None)
            if rec is not None:
                self._write_request_event({"event": "request_finished", **rec,
                    "finished_at": _now_iso(), "finished_monotonic": time.perf_counter(),
                    "job_in_flight": len(self._active_requests)})

    def record_model_call(
        self,
        provider: str,
        model: str,
        duration_s: float,
        request_id: Optional[str] = None,
        input_segments: int = 1,
        input_chars: int = 0,
        output_chars: int = 0,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        reasoning_tokens: Optional[int] = None,
        reasoning_chars: Optional[int] = None,
        ttft_s: Optional[float] = None,
        status: str = "success",
        retry_count: int = 0,
        error_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Token fields are nullable: None = the provider did not report it.
        prompt_tokens = _optional_int(prompt_tokens)
        completion_tokens = _optional_int(completion_tokens)
        total_tokens = _optional_int(total_tokens)
        if not total_tokens and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        rec = {
            "timestamp": _now_iso(),
            "provider": str(provider or "unknown"),
            "model": str(model or "unknown"),
            "request_id": str(request_id) if request_id else None,
            "duration_s": _safe_float(duration_s),
            "input_segments": _safe_int(input_segments),
            "input_chars": _safe_int(input_chars),
            "output_chars": _safe_int(output_chars),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": _optional_int(cached_tokens),
            "total_tokens": total_tokens,
            "reasoning_tokens": _optional_int(reasoning_tokens),
            # Auxiliary observation (length of returned reasoning text), NOT a token count.
            "reasoning_chars": _optional_int(reasoning_chars),
            "ttft_s": _safe_float(ttft_s) if ttft_s is not None else None,
            "ttft_available": ttft_s is not None,
            "status": status,
            "retry_count": _safe_int(retry_count),
            "error_type": str(error_type) if error_type else None,
            "metadata": sanitize_metadata(metadata or {}),
        }
        with self._lock:
            rec.update(self._active_requests.get(threading.get_ident(), {}))
            self.model_calls.append(rec)
            self._write_request_event({"event": "model_call", **rec})

    def record_batch(
        self,
        batch_id: Union[int, str],
        segment_count: int,
        source_chars: int,
        duration_ms: float,
        tm_hits: int = 0,
        model_called: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        rec = {
            "batch_id": str(batch_id),
            "segment_count": _safe_int(segment_count),
            "source_chars": _safe_int(source_chars),
            "duration_ms": _safe_float(duration_ms, 2),
            "tm_hits": _safe_int(tm_hits),
            "model_called": bool(model_called),
            "metadata": sanitize_metadata(metadata or {}),
        }
        with self._lock:
            self.batch_records.append(rec)
            # Store per-segment duration estimates
            if segment_count > 0:
                per_seg_ms = duration_ms / segment_count
                for _ in range(segment_count):
                    self.segment_durations.append(per_seg_ms)

    def record_tm_lookup(self, hit_type: str, duration_ms: float = 0.0) -> None:
        with self._lock:
            self.tm_stats["lookups"] += 1
            if hit_type == "exact":
                self.tm_stats["exact_hits"] += 1
            elif hit_type in {"normalized", "fuzzy"}:
                self.tm_stats["normalized_hits"] += 1
            else:
                self.tm_stats["misses"] += 1
            if duration_ms > 0:
                self.tm_stats["durations_ms"].append(_safe_float(duration_ms, 3))

    def record_terminology_lookup(self, matches: int = 0, duration_ms: float = 0.0) -> None:
        with self._lock:
            self.terminology_stats["lookups"] += 1
            self.terminology_stats["matches"] += int(matches)
            if duration_ms > 0:
                self.terminology_stats["durations_ms"].append(_safe_float(duration_ms, 3))

    def record_cache_event(self, cache_name: str, hit: bool) -> None:
        with self._lock:
            c = self.caches.setdefault(str(cache_name), {"hits": 0, "misses": 0})
            if hit:
                c["hits"] += 1
            else:
                c["misses"] += 1

    def record_ocr_page(
        self,
        page_no: int,
        page_type: str = "scanned",
        resolution: int = 150,
        pixel_count: int = 0,
        render_ms: float = 0.0,
        ocr_ms: float = 0.0,
        total_ms: float = 0.0,
        status: str = "completed",
    ) -> None:
        rec = {
            "page_no": int(page_no),
            "page_type": str(page_type),
            "resolution": int(resolution),
            "pixel_count": int(pixel_count),
            "render_ms": _safe_float(render_ms, 2),
            "ocr_ms": _safe_float(ocr_ms, 2),
            "total_ms": _safe_float(total_ms or (render_ms + ocr_ms), 2),
            "status": str(status),
        }
        with self._lock:
            self.ocr_page_records.append(rec)

    def record_error(self, stage: str, operation: str, error_type: str, message: str, retry_count: int = 0, recovered: bool = False) -> None:
        rec = {
            "timestamp": _now_iso(),
            "stage": str(stage),
            "operation": str(operation),
            "error_type": str(error_type),
            "message": str(message)[:250],
            "retry_count": int(retry_count),
            "recovered": bool(recovered),
        }
        with self._lock:
            self.errors.append(rec)

    def record_retry(self, stage: str, operation: str, attempt: int, backoff_s: float, error_type: str) -> None:
        rec = {
            "timestamp": _now_iso(),
            "stage": str(stage),
            "operation": str(operation),
            "attempt": int(attempt),
            "backoff_s": _safe_float(backoff_s, 2),
            "error_type": str(error_type),
        }
        with self._lock:
            self.retries.append(rec)
            self._write_request_event({"event": "retry", **rec})

    def finish(self, status: str = "success", failure_reason: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if self.finished_at is not None:
                return self.generate_summary()
            self.status = status
            self.failure_reason = failure_reason
            self.finished_at = _now_iso()
            self.total_wall_time_s = round(time.perf_counter() - self.started_monotonic, 4)
            self.total_cpu_time_s = round(time.process_time() - self.started_cpu_time, 4)

            # Stop sampler
            resource_summary = self._sampler.stop()

            self.record_point("run_finished", status=status,
                              open_logical_request_ids=[r["logical_request_id"] for r in self._active_requests.values()])
            # Close event stream
            if self._requests_file:
                try:
                    self._requests_file.flush()
                    self._requests_file.close()
                except Exception:
                    pass
                self._requests_file = None
            if self._events_file:
                try:
                    self._events_file.flush()
                    self._events_file.close()
                except Exception:
                    pass
                self._events_file = None

            # Calculate throughputs
            self._finalize_throughputs()

            # Generate summaries & reports
            summary = self.generate_summary(resource_summary)
            stages_doc = self.generate_stages_doc()
            perf_md = self.generate_performance_report_md(summary, stages_doc, resource_summary)

            # Persist files if tracing enabled
            if self.level != TraceLevel.OFF:
                try:
                    self.run_dir.mkdir(parents=True, exist_ok=True)
                    self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.stages_path.write_text(json.dumps(stages_doc, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.report_path.write_text(perf_md, encoding="utf-8")

                    # Also update history.json
                    self._update_history(summary)

                    # Also write legacy performance.json for backwards compatibility
                    self._write_legacy_performance(summary, stages_doc)
                except Exception:
                    pass

            return summary

    def _finalize_stage_times(self) -> None:
        """Make ``wall_time_s`` mean work time, never the stage's lifetime.

        A stage that recorded no spans/items did no measured work: it reports
        0 plus ``no_work`` and keeps its first-start→last-finish span as
        ``lifetime_s``.  Stages built from spans report the union of their
        root-span intervals (concurrent spans are not double counted) and keep
        the plain sum as ``busy_time_s``.
        """
        for stage_name, rec in self.stages.items():
            if "lifetime_s" not in rec:
                rec["lifetime_s"] = rec.get("wall_time_s", 0.0)
            if not rec.get("spans_count"):
                rec["no_work"] = True
                rec["wall_time_s"] = 0.0
                rec["busy_time_s"] = 0.0
                continue
            rec["no_work"] = False
            intervals = sorted(self._stage_intervals.get(stage_name, ()))
            if intervals:
                union = 0.0
                left, right = intervals[0]
                for start, end in intervals[1:]:
                    if start > right:
                        union += right - left
                        left, right = start, end
                    else:
                        right = max(right, end)
                union += right - left
                rec["wall_time_s"] = round(union, 4)

    def _finalize_throughputs(self) -> None:
        self._finalize_stage_times()
        for stage_name, rec in self.stages.items():
            if rec.get("no_work"):
                rec["throughput"] = {}
                continue
            wall_s = max(0.001, rec["wall_time_s"])
            tp = {}
            if rec["items_out"] > 0:
                tp["items_per_s"] = round(rec["items_out"] / wall_s, 2)
            elif rec["items_in"] > 0:
                tp["items_per_s"] = round(rec["items_in"] / wall_s, 2)

            if "ocr" in stage_name.lower() or "pdf" in stage_name.lower():
                pages = self.dataset_meta.get("page_count", 0)
                if pages > 0:
                    tp["pages_per_s"] = round(pages / wall_s, 2)

            if "translat" in stage_name.lower():
                chars = self.dataset_meta.get("source_chars", 0)
                if chars > 0:
                    tp["chars_per_s"] = round(chars / wall_s, 1)

            rec["throughput"] = tp

    def generate_summary(self, resource_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        res_summary = resource_summary or {}
        # Effective wall time (finished or currently running)
        current_wall = self.total_wall_time_s
        if current_wall <= 0.0:
            current_wall = max(0.001, time.perf_counter() - self.started_monotonic)

        # Unaccounted time: wall time minus top-level stage sum
        top_level_sum = sum(
            self.stages[s]["wall_time_s"]
            for s in self.top_level_stages
            if s in self.stages
        )
        unaccounted_s = max(0.0, current_wall - top_level_sum)
        if current_wall > 0.05:
            unaccounted_pct = min(100.0, max(0.0, round((unaccounted_s / current_wall) * 100.0, 1)))
        else:
            unaccounted_pct = 0.0

        # Segment & batch latency distributions
        batch_durations = [b["duration_ms"] / 1000.0 for b in self.batch_records]
        batch_pcts = compute_percentiles(batch_durations)
        seg_durations_s = [d / 1000.0 for d in self.segment_durations]
        seg_pcts = compute_percentiles(seg_durations_s)

        # Top 10 slowest batches
        sorted_batches = sorted(self.batch_records, key=lambda b: b.get("duration_ms", 0.0), reverse=True)
        top_slow_batches = sorted_batches[:10]

        # Model requests summary
        model_latencies = [m["duration_s"] for m in self.model_calls]
        model_pcts = compute_percentiles(model_latencies)
        token_usage = {key: aggregate_optional(self.model_calls, key) for key in (
            "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens",
            "reasoning_tokens", "reasoning_chars")}
        total_prompt_tok = token_usage["prompt_tokens"]["total"]
        total_out_tok = token_usage["completion_tokens"]["total"]
        total_cached_tok = token_usage["cached_tokens"]["total"]
        total_tok = token_usage["total_tokens"]["total"]
        # Throughput only over calls that actually reported completion tokens.
        reported_calls = [m for m in self.model_calls if m.get("completion_tokens") is not None]
        reported_latency = sum(m["duration_s"] for m in reported_calls)
        model_calls_success = sum(1 for m in self.model_calls if m.get("status") == "success")
        model_calls_failed = sum(1 for m in self.model_calls if m.get("status") != "success")

        # TM summary
        tm_lookups = self.tm_stats["lookups"]
        tm_hits = self.tm_stats["exact_hits"] + self.tm_stats["normalized_hits"]
        tm_hit_ratio = round(tm_hits / max(1, tm_lookups), 4) if tm_lookups else 0.0
        tm_durations = self.tm_stats["durations_ms"]
        tm_pcts = compute_percentiles(tm_durations)

        # Stage duration breakdown & percent of total
        stage_breakdown = {}
        for s_name, s_data in self.stages.items():
            w = s_data["wall_time_s"]
            if current_wall > 0.05:
                pct = min(100.0, max(0.0, round((w / current_wall) * 100.0, 2)))
            else:
                pct = 0.0
            cpu = s_data["cpu_time_s"]
            stage_breakdown[s_name] = {
                "wall_time_s": w,
                "no_work": bool(s_data.get("no_work", False)),
                "spans_count": s_data.get("spans_count", 0),
                "busy_time_s": s_data.get("busy_time_s"),
                "lifetime_s": s_data.get("lifetime_s"),
                "pct_of_total": pct,
                "cpu_time_s": cpu,
                "cpu_wall_ratio": round(cpu / max(0.001, w), 2),
                "throughput": s_data.get("throughput", {}),
                "status": s_data.get("status", "success"),
            }

        # OCR pages summary
        ocr_durations = [p["total_ms"] / 1000.0 for p in self.ocr_page_records]
        ocr_pcts = compute_percentiles(ocr_durations)
        slowest_ocr_pages = sorted(self.ocr_page_records, key=lambda p: p.get("total_ms", 0.0), reverse=True)[:5]

        summary = {
            "version": 2,
            "run_id": self.run_id,
            "task_id": self.job_id,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "trace_level": self.level.value if hasattr(self.level, "value") else str(self.level),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "wall_time_s": round(current_wall, 4),
            "process_cpu_time_s": self.total_cpu_time_s if self.total_cpu_time_s > 0 else round(time.process_time() - self.started_cpu_time, 4),
            "cpu_wall_ratio": round(self.total_cpu_time_s / max(0.001, current_wall), 2),
            "unaccounted_time_s": round(unaccounted_s, 2),
            "unaccounted_pct": unaccounted_pct,
            "dataset": dict(self.dataset_meta),
            "stages": stage_breakdown,
            "latency_percentiles": {
                "batch_seconds": batch_pcts,
                "segment_seconds": seg_pcts,
                "model_call_seconds": model_pcts,
                "tm_lookup_ms": tm_pcts,
                "ocr_page_seconds": ocr_pcts,
            },
            "slowest_batches": top_slow_batches,
            "slowest_ocr_pages": slowest_ocr_pages,
            "model_metrics": {
                "total_calls": len(self.model_calls),
                "successful_calls": model_calls_success,
                "failed_calls": model_calls_failed,
                # None = no call reported the field; otherwise the sum over
                # reporting calls only.  See ``token_usage`` for completeness.
                "total_prompt_tokens": total_prompt_tok,
                "total_completion_tokens": total_out_tok,
                "total_cached_tokens": total_cached_tok,
                "total_tokens": total_tok,
                "total_reasoning_tokens": token_usage["reasoning_tokens"]["total"],
                "total_reasoning_chars": token_usage["reasoning_chars"]["total"],
                "token_usage": token_usage,
                "avg_call_latency_s": model_pcts.get("avg", 0.0),
                "tokens_per_second": (round(total_out_tok / max(0.001, reported_latency), 2)
                                      if reported_calls and total_out_tok is not None else None),
            },
            "tm_and_cache": {
                "tm_lookups": tm_lookups,
                "tm_exact_hits": self.tm_stats["exact_hits"],
                "tm_normalized_hits": self.tm_stats["normalized_hits"],
                "tm_misses": self.tm_stats["misses"],
                "tm_hit_ratio": tm_hit_ratio,
                "terminology_lookups": self.terminology_stats["lookups"],
                "terminology_matches": self.terminology_stats["matches"],
                "caches": self.caches,
            },
            "resources": {
                "process_cpu_avg_pct": res_summary.get("process_cpu_avg_pct", 0.0),
                "process_cpu_peak_pct": res_summary.get("process_cpu_peak_pct", 0.0),
                "process_rss_peak_mb": res_summary.get("process_rss_peak_mb", 0.0),
                "thread_peak": res_summary.get("thread_peak", 1),
                "gpu_detected": res_summary.get("gpu_detected", False),
            },
            "errors_and_retries": {
                "warnings": self.warnings_count,
                "errors": len(self.errors),
                "retries": len(self.retries),
                "recovered_errors": sum(1 for e in self.errors if e.get("recovered")),
                "error_list": self.errors[-10:],
                "retry_list": self.retries[-10:],
            },
        }
        return summary

    def generate_stages_doc(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.job_id,
            "stages": self.stages,
            "top_level_stages": self.top_level_stages,
        }

    def generate_performance_report_md(
        self,
        summary: Dict[str, Any],
        stages_doc: Dict[str, Any],
        resource_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        d = summary.get("dataset", {})
        stages = summary.get("stages", {})
        m = summary.get("model_metrics", {})
        tm = summary.get("tm_and_cache", {})
        r = summary.get("resources", {})
        lp = summary.get("latency_percentiles", {})
        err = summary.get("errors_and_retries", {})
        _empty = {"total": None, "calls_reported": 0, "calls_total": 0, "complete": False}
        tu = {k: (m.get("token_usage") or {}).get(k, _empty) for k in (
            "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens",
            "reasoning_tokens", "reasoning_chars")}
        tps = m.get("tokens_per_second")
        tps_text = f"{tps} tokens/s" if tps is not None else "unknown (no completion usage reported)"

        # Generate rule-based bottlenecks
        bottlenecks = []
        wall = summary.get("wall_time_s", 0.0)

        # Stage dominance
        for s_name, s_info in stages.items():
            pct = s_info.get("pct_of_total", 0.0)
            if pct >= 50.0:
                bottlenecks.append(f"- **Dominant Stage**: Stage `{s_name}` consumed **{s_info.get('wall_time_s')}s** ({pct}% of total runtime).")

        # CPU bound vs IO bound
        cpu_wall = summary.get("cpu_wall_ratio", 0.0)
        if cpu_wall < 0.2:
            bottlenecks.append(f"- **IO / Network Bound**: Process CPU/Wall ratio is low ({cpu_wall:.2f}), indicating significant time spent waiting on LLM network or disk IO.")
        elif cpu_wall > 0.8:
            bottlenecks.append(f"- **CPU Bound**: Process CPU/Wall ratio is high ({cpu_wall:.2f}), indicating compute-intensive operations.")

        # Batch variance
        b_pct = lp.get("batch_seconds", {})
        if b_pct.get("p50", 0.0) > 0:
            ratio = b_pct.get("p95", 0.0) / b_pct.get("p50", 1.0)
            if ratio >= 2.5:
                bottlenecks.append(f"- **Batch Latency Long-Tail**: P95 batch latency ({b_pct.get('p95')}s) is **{ratio:.1f}×** higher than P50 ({b_pct.get('p50')}s).")

        # Unaccounted time
        unacc = summary.get("unaccounted_time_s", 0.0)
        unacc_pct = summary.get("unaccounted_pct", 0.0)
        if unacc_pct >= 15.0 and unacc >= 5.0:
            bottlenecks.append(f"- **Unaccounted Idle Gap**: **{unacc}s** ({unacc_pct}%) of runtime is outside recorded pipeline stages (potential queueing, UI reruns, or lock waits).")

        # Model retries
        if err.get("retries", 0) > 0:
            bottlenecks.append(f"- **API Retries**: Encountered **{err.get('retries')} retries**; model backoff/transient errors added hidden delay.")

        # TM effectiveness
        if d.get("segment_count", 0) > 100 and tm.get("tm_lookups", 0) > 0:
            if tm.get("tm_hit_ratio", 0.0) < 0.05:
                bottlenecks.append(f"- **Low TM Hit Rate**: Translation Memory hit rate is low ({tm.get('tm_hit_ratio') * 100:.1f}%), meaning almost all segments required fresh LLM generation.")

        if not bottlenecks:
            bottlenecks.append("- No obvious performance anomalies detected based on standard heuristics.")

        bottlenecks_text = "\n".join(bottlenecks)

        # Top slowest batches table
        slow_batches_rows = []
        for b in summary.get("slowest_batches", [])[:5]:
            slow_batches_rows.append(
                f"| #{b.get('batch_id')} | {b.get('segment_count')} segs | {b.get('source_chars')} chars | {b.get('duration_ms') / 1000.0:.2f} s | {'Yes' if b.get('model_called') else 'No'} | {b.get('tm_hits', 0)} hits |"
            )
        slow_batches_table = (
            "| Batch ID | Segments | Chars | Duration | Model Called | TM Hits |\n"
            "|---|---|---|---|---|---|\n" + "\n".join(slow_batches_rows)
            if slow_batches_rows
            else "_No batch data recorded._"
        )

        # Stages table
        stage_rows = []
        for s_name, s_info in stages.items():
            tp_str = ", ".join(f"{v} {k}" for k, v in s_info.get("throughput", {}).items()) or "—"
            if s_info.get("no_work"):
                stage_rows.append(
                    f"| `{s_name}` | — (not run / no work recorded) | — | — | — | — | {s_info.get('status')} |")
                continue
            stage_rows.append(
                f"| `{s_name}` | {s_info.get('wall_time_s')} s | {s_info.get('pct_of_total')}% | {s_info.get('cpu_time_s')} s | {s_info.get('cpu_wall_ratio')} | {tp_str} | {s_info.get('status')} |"
            )
        stages_table = (
            "| Stage | Work Time (union of spans) | % Total | CPU Time | CPU/Wall Ratio | Throughput | Status |\n"
            "|---|---|---|---|---|---|---|\n" + "\n".join(stage_rows)
            if stage_rows
            else "_No stages recorded._"
        )

        md = f"""# Run Performance Report

## Run Overview
- **Run ID**: `{summary.get('run_id')}`
- **Task ID**: `{summary.get('task_id')}`
- **Status**: `{summary.get('status')}`
- **Trace Mode**: `{summary.get('trace_level')}`
- **Started At**: {summary.get('started_at')}
- **Finished At**: {summary.get('finished_at')}
- **Wall Clock Duration**: **{summary.get('wall_time_s')} s**
- **Process CPU Time**: **{summary.get('process_cpu_time_s')} s** (Ratio: {summary.get('cpu_wall_ratio')})

## Dataset Characteristics
- **File**: `{d.get('filename')}` ({d.get('file_type', 'unknown')}, {d.get('file_size_bytes', 0):,} bytes)
- **Target Language**: {d.get('target_lang')}
- **Pages**: {d.get('page_count', 0)} (Scanned: {d.get('scanned_pages', 0)}, OCR: {d.get('ocr_pages', 0)})
- **Volume**: {d.get('source_chars', 0):,} source chars, {d.get('source_words', 0):,} words, {d.get('paragraph_count', 0):,} paragraphs, {d.get('segment_count', 0):,} segments

## Pipeline Timing & Stage Breakdown
{stages_table}

## Resource Utilization
- **Process CPU**: Avg {r.get('process_cpu_avg_pct')}% · Peak {r.get('process_cpu_peak_pct')}%
- **Process RSS Memory**: Peak **{r.get('process_rss_peak_mb')} MB**
- **Active Threads Peak**: {r.get('thread_peak')}
- **GPU Available**: {'Yes' if r.get('gpu_detected') else 'No'}

## Model Performance
- **Total Requests**: {m.get('total_calls', 0)} (Successful: {m.get('successful_calls', 0)}, Failed: {m.get('failed_calls', 0)})
- **Tokens Breakdown** (unreported = unknown, not zero): Prompt: {_fmt_optional_total(tu['prompt_tokens'])} | Cached: {_fmt_optional_total(tu['cached_tokens'])} | Output: {_fmt_optional_total(tu['completion_tokens'])} | Reasoning: {_fmt_optional_total(tu['reasoning_tokens'])} | Total: {_fmt_optional_total(tu['total_tokens'])}
- **Reasoning Text Returned** (chars, not tokens): {_fmt_optional_total(tu['reasoning_chars'])}
- **Throughput**: {tps_text}
- **Latency**: Avg {m.get('avg_call_latency_s', 0.0)} s | P50 {lp.get('model_call_seconds', {}).get('p50', 0.0)} s | P95 {lp.get('model_call_seconds', {}).get('p95', 0.0)} s | Max {lp.get('model_call_seconds', {}).get('max', 0.0)} s

## TM / Terminology / Cache
- **TM Lookups**: {tm.get('tm_lookups', 0)} (Exact: {tm.get('tm_exact_hits', 0)}, Normalized: {tm.get('tm_normalized_hits', 0)}, Miss: {tm.get('tm_misses', 0)})
- **TM Hit Rate**: **{tm.get('tm_hit_ratio', 0.0) * 100:.1f}%**
- **Terminology**: {tm.get('terminology_lookups', 0)} lookups, {tm.get('terminology_matches', 0)} matches

## OCR / PDF Performance
- **Pages Processed**: {len(self.ocr_page_records)}
- **Latency Distribution**: P50 {lp.get('ocr_page_seconds', {}).get('p50', 0.0)} s | P95 {lp.get('ocr_page_seconds', {}).get('p95', 0.0)} s | Max {lp.get('ocr_page_seconds', {}).get('max', 0.0)} s

## Latency Distribution
- **Batches**: P50 {lp.get('batch_seconds', {}).get('p50', 0.0)} s | P90 {lp.get('batch_seconds', {}).get('p90', 0.0)} s | P95 {lp.get('batch_seconds', {}).get('p95', 0.0)} s | Max {lp.get('batch_seconds', {}).get('max', 0.0)} s
- **Segments**: P50 {lp.get('segment_seconds', {}).get('p50', 0.0)} s | P95 {lp.get('segment_seconds', {}).get('p95', 0.0)} s | Max {lp.get('segment_seconds', {}).get('max', 0.0)} s

## Slowest Operations (Top Batches)
{slow_batches_table}

## Errors / Retries
- **Warnings**: {err.get('warnings', 0)}
- **Errors**: {err.get('errors', 0)} (Recovered: {err.get('recovered_errors', 0)})
- **Retries**: {err.get('retries', 0)}

## Unaccounted Time
- **Unaccounted Gap**: **{summary.get('unaccounted_time_s', 0.0)} s** ({summary.get('unaccounted_pct', 0.0)}% of total runtime)
- Represents gap between top-level stage instrumentation and total wall clock.

## Potential Bottlenecks
{bottlenecks_text}
"""
        return md

    def _update_history(self, summary: Dict[str, Any]) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            history = []
            if self.history_path.is_file():
                try:
                    history = json.loads(self.history_path.read_text(encoding="utf-8"))
                    if not isinstance(history, list):
                        history = []
                except Exception:
                    history = []
            entry = {
                "run_id": summary.get("run_id"),
                "task_id": summary.get("task_id"),
                "started_at": summary.get("started_at"),
                "finished_at": summary.get("finished_at"),
                "wall_time_s": summary.get("wall_time_s"),
                "status": summary.get("status"),
                "segment_count": summary.get("dataset", {}).get("segment_count", 0),
                "peak_ram_mb": summary.get("resources", {}).get("process_rss_peak_mb", 0.0),
                "tm_hit_ratio": summary.get("tm_and_cache", {}).get("tm_hit_ratio", 0.0),
                "model_calls": summary.get("model_metrics", {}).get("total_calls", 0),
                "total_tokens": summary.get("model_metrics", {}).get("total_tokens"),
                "stages": {k: v.get("wall_time_s", 0.0) for k, v in summary.get("stages", {}).items()},
            }
            # Append and keep up to 50 runs
            history.append(entry)
            history = history[-50:]
            self.history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _write_legacy_performance(self, summary: Dict[str, Any], stages_doc: Dict[str, Any]) -> None:
        """Write legacy performance.json structure so older viewers/tests continue to function."""
        try:
            existing_stages = {}
            if self.legacy_perf_path.exists():
                try:
                    existing_data = json.loads(self.legacy_perf_path.read_text(encoding="utf-8"))
                    if isinstance(existing_data, dict):
                        existing_stages = existing_data.get("stages") or {}
                except Exception:
                    pass
            # A standalone delivery run must not erase the ingestion and
            # translation timings already stored for this task.
            legacy_stages = dict(existing_stages)
            for s_name, s_info in self.stages.items():
                old_stage = existing_stages.get(s_name) or {}
                b_count = len([b for b in self.batch_records if b.get("stage") == s_name])
                if not b_count and isinstance(old_stage.get("batch_count"), int):
                    b_count = old_stage["batch_count"]
                legacy_stages[s_name] = {
                    "stage_name": s_name,
                    "started_at": s_info.get("first_started_at"),
                    "finished_at": s_info.get("last_finished_at"),
                    "wall_time_s": s_info.get("wall_time_s", 0.0),
                    "item_count": s_info.get("items_in", 0),
                    "page_count": old_stage.get("page_count", self.dataset_meta.get("page_count", 0)),
                    "batch_count": b_count,
                    "status": s_info.get("status", "completed"),
                    "concurrency": old_stage.get("concurrency", 1),
                    "metadata": {**(s_info.get("metadata") or {}), **s_info.get("operations", {})},
                    "items": old_stage.get("items") or [],
                }
            legacy = {
                "version": 1,
                "run_id": self.run_id,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "wall_time_s": self.total_wall_time_s,
                "stages": legacy_stages,
            }
            self.legacy_perf_path.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


class TraceManager:
    """Singleton coordinator for Folith tracing."""

    def __init__(self):
        self._default_level = TraceLevel.from_env()

    def get_level(self) -> TraceLevel:
        return TraceLevel.from_env(self._default_level)

    def set_level(self, level: Union[str, TraceLevel]) -> None:
        if isinstance(level, str):
            self._default_level = TraceLevel(level)
        else:
            self._default_level = level

    def is_enabled(self) -> bool:
        return self.get_level() != TraceLevel.OFF

    def is_detailed(self) -> bool:
        return self.get_level() == TraceLevel.DETAILED

    def start_run(
        self,
        job_id: str,
        run_id: Optional[str] = None,
        filename: str = "",
        file_type: str = "",
        file_size_bytes: int = 0,
        target_lang: str = "",
        output_dir: Optional[Path] = None,
        level: Optional[TraceLevel] = None,
    ) -> RunContext:
        ctx = RunContext(
            job_id=job_id,
            run_id=run_id,
            filename=filename,
            file_type=file_type,
            file_size_bytes=file_size_bytes,
            target_lang=target_lang,
            output_dir=output_dir,
            level=level or self.get_level(),
        )
        _RUN_CONTEXT_VAR.set(ctx)
        with _JOB_RUNS_LOCK:
            _ACTIVE_JOB_RUNS[str(job_id)] = ctx
        return ctx

    def get_active_run(self, job_id: Optional[str] = None) -> Optional[RunContext]:
        if job_id:
            with _JOB_RUNS_LOCK:
                run = _ACTIVE_JOB_RUNS.get(str(job_id))
                if run:
                    return run
        return _RUN_CONTEXT_VAR.get()

    def finish_run(self, job_id: Optional[str] = None, status: str = "success", failure_reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
        run = self.get_active_run(job_id)
        if not run:
            return None
        res = run.finish(status=status, failure_reason=failure_reason)
        if job_id:
            with _JOB_RUNS_LOCK:
                _ACTIVE_JOB_RUNS.pop(str(job_id), None)
        return res

    @contextlib.contextmanager
    def span(
        self,
        stage: str,
        operation: str = "",
        job_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        items_in: int = 0,
        items_out: int = 0,
    ) -> Iterator[SpanHandle]:
        run = self.get_active_run(job_id)
        if not run or run.level == TraceLevel.OFF:
            # Yield dummy handle with no-op
            dummy = SpanHandle(
                run_context=run or RunContext(job_id or "dummy", level=TraceLevel.OFF),
                stage=stage,
                operation=operation or stage,
                metadata=metadata,
                items_in=items_in,
                items_out=items_out,
            )
            yield dummy
            return

        handle = run.span(stage=stage, operation=operation, metadata=metadata, items_in=items_in, items_out=items_out)
        stack = list(_ACTIVE_SPAN_STACK.get())
        stack.append(handle.event_id)
        token = _ACTIVE_SPAN_STACK.set(stack)
        try:
            yield handle
            handle.finish(status="success")
        except BaseException as exc:
            handle.finish(status="error", error=exc)
            raise
        finally:
            cur_stack = list(_ACTIVE_SPAN_STACK.get())
            if cur_stack and cur_stack[-1] == handle.event_id:
                cur_stack.pop()
                _ACTIVE_SPAN_STACK.set(cur_stack)
            else:
                _ACTIVE_SPAN_STACK.reset(token)

    def record_model_call(self, *args, job_id: Optional[str] = None, **kwargs) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_model_call(*args, **kwargs)

    def record_batch(self, *args, job_id: Optional[str] = None, **kwargs) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_batch(*args, **kwargs)

    def record_tm_lookup(self, hit_type: str, duration_ms: float = 0.0, job_id: Optional[str] = None) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_tm_lookup(hit_type, duration_ms)

    def record_terminology_lookup(self, matches: int = 0, duration_ms: float = 0.0, job_id: Optional[str] = None) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_terminology_lookup(matches, duration_ms)

    def record_cache_event(self, cache_name: str, hit: bool, job_id: Optional[str] = None) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_cache_event(cache_name, hit)

    def record_ocr_page(self, *args, job_id: Optional[str] = None, **kwargs) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_ocr_page(*args, **kwargs)

    def record_error(self, *args, job_id: Optional[str] = None, **kwargs) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_error(*args, **kwargs)

    def record_retry(self, *args, job_id: Optional[str] = None, **kwargs) -> None:
        run = self.get_active_run(job_id)
        if run:
            run.record_retry(*args, **kwargs)


# Global trace instance
trace = TraceManager()
