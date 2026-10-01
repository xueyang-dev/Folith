"""Fine-grained profiler for the ``save_job_state`` persistence path.

Round 2 established *that* ``state_commit`` is expensive (49% of a 5000-segment
run) but treated it as one opaque bucket, so every optimisation was a guess.
This module splits a single ``save_job_state`` call into the sub-phases the
persistence brief names, so the next change is driven by measurement.

Two independent timelines are recorded:

``SAVE_PHASES``
    The eleven sub-phases of one :func:`save_job_state` call — from entry
    overhead through JSON serialization, the atomic replace, and the derived
    ``summary.json`` write.

``COMMIT_PHASES``
    The work that sits *next to* the save call inside the same
    ``state_commit`` profiler stage: the translation-truth mutation record
    (``truth_marking``) and the enclosing batch commit.  Without these, a
    sub-phase table that only covers ``save_job_state`` would under-report the
    stage it is supposed to explain.

``TM_PHASES``
    The four sub-phases of one :func:`save_tm` call.  The project translation
    memory is a *derived* materialised cache, so it gets its own timeline rather
    than being folded into ``SAVE_PHASES``.

Design constraints
------------------
* **Zero overhead when off.** Every entry point is an attribute check on a
  module singleton; the disabled path yields ``contextlib.nullcontext``.  The
  module never touches the filesystem and never mutates state.
* **No behaviour change.** Recording only observes durations and byte counts.
  A phase body that raises still records its duration (``finally``), so a
  failed save cannot silently disappear from the table.
* **Cheap on the hot path.** Per-call cost is a handful of ``perf_counter``
  calls plus one list append per phase — microseconds against a save measured
  in tens of milliseconds.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import Any, Dict, List, Optional

#: Sub-phases of a single ``save_job_state`` call, in execution order.
SAVE_PHASES = (
    "state_pre_reconcile",
    "review_truth_reconcile",
    "finding_normalization",
    "delivery_snapshot_reconcile",
    "json_serialization",
    "state_file_write",
    "atomic_replace",
    "cache_invalidation",
    "summary_build",
    "summary_serialization",
    "summary_write",
)

#: Work adjacent to the save call inside the ``state_commit`` stage.
COMMIT_PHASES = (
    "truth_marking",
)

#: Sub-phases of a single ``save_tm`` call, in execution order.
#:
#: ``tm.json`` is a *derived* materialised cache -- ``checkpoint.
#: reconcile_translation_memory`` rebuilds it from ``state["pairs"]`` plus any
#: unacknowledged ``tm_promotion_pending`` events -- so its write path can be
#: profiled and tuned without touching the authority model.  These phases are
#: deliberately separate from ``SAVE_PHASES`` so the two artifacts are never
#: summed into one misleading "persistence" bucket.
TM_PHASES = (
    "tm_prepare",
    "tm_json_serialization",
    "tm_file_write",
    "tm_atomic_replace",
)

#: Every phase this module knows how to aggregate.
ALL_PHASES = SAVE_PHASES + COMMIT_PHASES + TM_PHASES

ENV_FLAG = "FOLITH_PROFILE_PERSISTENCE"


class PhaseSink:
    """Mutable per-invocation record a phase body can annotate.

    Only ``serialized_bytes`` and ``written_bytes`` are meaningful for the
    phases that serialize or write; every other phase leaves them at zero so
    the aggregate stays honest instead of double-counting the same payload.
    """

    __slots__ = ("serialized_bytes", "written_bytes", "items")

    def __init__(self) -> None:
        self.serialized_bytes = 0
        self.written_bytes = 0
        self.items = 0

    def serialized(self, size: int) -> None:
        self.serialized_bytes += max(0, int(size))

    def written(self, size: int) -> None:
        self.written_bytes += max(0, int(size))

    def traversed(self, count: int) -> None:
        """Record how many state items this phase visited (O(n) evidence)."""
        self.items += max(0, int(count))


_NULL_SINK = PhaseSink()


def _env_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG) or "").strip().lower() in {
        "1", "true", "yes", "on"}


def _percentile(sorted_values: List[float], fraction: float) -> float:
    """Nearest-rank percentile; matches how the other benchmark scripts report."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = int(round(fraction * (len(sorted_values) - 1)))
    index = max(0, min(len(sorted_values) - 1, index))
    return sorted_values[index]


class PersistenceProfiler:
    """Accumulates per-phase durations and byte counts across many saves."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._lock = threading.Lock()
        self.enabled = bool(enabled)
        self._durations: Dict[str, List[float]] = {name: [] for name in ALL_PHASES}
        self._serialized: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._written: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._items: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._save_calls = 0

    # -- recording ---------------------------------------------------------
    def record(
        self,
        phase: str,
        duration_s: float,
        *,
        serialized_bytes: int = 0,
        written_bytes: int = 0,
        items: int = 0,
    ) -> None:
        if not self.enabled:
            return
        if phase not in self._durations:
            # Unknown phase: keep the timeline honest rather than dropping data.
            with self._lock:
                self._durations[phase] = []
                self._serialized[phase] = 0
                self._written[phase] = 0
                self._items[phase] = 0
        with self._lock:
            self._durations[phase].append(float(duration_s))
            self._serialized[phase] += max(0, int(serialized_bytes))
            self._written[phase] += max(0, int(written_bytes))
            self._items[phase] += max(0, int(items))

    def count_save(self) -> None:
        """Count one top-level ``save_job_state`` invocation."""
        if not self.enabled:
            return
        with self._lock:
            self._save_calls += 1

    @contextlib.contextmanager
    def phase(self, name: str):
        """Time one phase; the yielded :class:`PhaseSink` carries byte counts."""
        if not self.enabled:
            yield _NULL_SINK
            return
        sink = PhaseSink()
        started = time.perf_counter()
        try:
            yield sink
        finally:
            self.record(
                name, time.perf_counter() - started,
                serialized_bytes=sink.serialized_bytes,
                written_bytes=sink.written_bytes,
                items=sink.items,
            )

    # -- reporting ---------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            for name in list(self._durations):
                self._durations[name] = []
                self._serialized[name] = 0
                self._written[name] = 0
                self._items[name] = 0
            self._save_calls = 0

    def snapshot(self) -> Dict[str, Any]:
        """Aggregate per-phase count / total / avg / p50 / p95 / bytes."""
        with self._lock:
            durations = {name: list(values) for name, values in self._durations.items()}
            serialized = dict(self._serialized)
            written = dict(self._written)
            items = dict(self._items)
            save_calls = self._save_calls

        phases: Dict[str, Dict[str, Any]] = {}
        for name, values in durations.items():
            ordered = sorted(values)
            total = sum(values)
            count = len(values)
            phases[name] = {
                "call_count": count,
                "total_wall_s": round(total, 6),
                "avg_s": round(total / count, 6) if count else 0.0,
                "p50_s": round(_percentile(ordered, 0.50), 6),
                "p95_s": round(_percentile(ordered, 0.95), 6),
                "max_s": round(ordered[-1], 6) if ordered else 0.0,
                "bytes_serialized": serialized.get(name, 0),
                "bytes_written": written.get(name, 0),
                "items_traversed": items.get(name, 0),
            }
        measured_total = sum(
            record["total_wall_s"] for name, record in phases.items()
            if name in SAVE_PHASES)
        return {
            "save_calls": save_calls,
            "save_phase_total_s": round(measured_total, 6),
            "phases": phases,
        }


_PROFILER = PersistenceProfiler(enabled=_env_enabled())


def profiler() -> PersistenceProfiler:
    return _PROFILER


def enable() -> PersistenceProfiler:
    _PROFILER.enabled = True
    return _PROFILER


def disable() -> None:
    _PROFILER.enabled = False


def reset() -> None:
    _PROFILER.reset()


def snapshot() -> Dict[str, Any]:
    return _PROFILER.snapshot()


def phase(name: str):
    """Time one persistence phase. No-op (nullcontext) when disabled."""
    if not _PROFILER.enabled:
        return contextlib.nullcontext(_NULL_SINK)
    return _PROFILER.phase(name)


def count_save() -> None:
    _PROFILER.count_save()
