"""Critical-path attribution profiler for the translation batch loop.

Why this module exists
----------------------
Phase 5 attributed ``_setup`` down to individual functions and found two real
O(n^2) scans.  The next two buckets on the critical path -- ``semantic_review``
and ``commit_tail`` -- were still single opaque numbers, so any optimisation
attempt inside them would have been a guess.  This module exists so that
``semantic_review wall = provider wait + local CPU + queue wait`` can be
*measured* rather than asserted.

What it records
---------------
One :class:`CriticalPathProfiler` accumulates, per named phase:

``calls``
    How many times the phase body ran.
``wall_s``
    Sum of ``time.perf_counter`` deltas around the body.
``cpu_s``
    Sum of ``time.thread_time`` deltas around the body.  This is *thread* CPU
    time, so a phase that blocks on a socket (or on another thread's work)
    reports ``wall_s >> cpu_s`` -- which is exactly the discriminator needed to
    separate provider wait from local CPU.  ``wait_s = wall_s - cpu_s`` is
    derived in :meth:`snapshot` rather than stored, so it can never drift out of
    sync with its inputs.
``p50`` / ``p95`` / ``max``
    Percentiles of the per-call wall durations.
``items`` / ``chars`` / ``bytes`` / ``objects``
    Optional counts a phase body annotates through :class:`PhaseSink`.
    ``items`` is what a phase *looked at*, ``objects`` is what it *allocated*;
    that pair is what separates "scanned the prefix" from "rebuilt the prefix".

Named counters (:meth:`count`) cover things that are not durations: snapshot
count, fsync count, bytes written, artifact writes.

Design constraints
------------------
* **Zero overhead when off.** ``phase()`` checks a module-level flag and returns
  ``contextlib.nullcontext`` with a shared null sink.  The disabled path costs
  one attribute read, so production pays nothing.
* **No behaviour change.** Recording only observes durations and counts.  Every
  phase body is wrapped so the timer closes in ``finally`` -- a phase that raises
  still appears in the table.
* **Bounded memory.** One ``float`` per call per phase.  A 20,000-segment run is
  ~5,000 batches; at ~30 phases that is ~150k floats, a few megabytes, and it is
  released when the run's profiler is dropped.
* **Thread-safe.** The translation loop is single-threaded at concurrency 1, but
  the review scheduler can use several workers, so every mutation is guarded.
"""
from __future__ import annotations

import contextlib
import functools
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

#: Set to 1/true/yes/on to record.  Read once at import and re-read by
#: :func:`enable`/:func:`disable` so a benchmark can toggle it in-process.
ENV_FLAG = "FOLITH_PROFILE_CRITICAL_PATH"


def _env_enabled() -> bool:
    return str(os.environ.get(ENV_FLAG) or "").strip().lower() in {
        "1", "true", "yes", "on"}


# --------------------------------------------------------------------------- #
# phase vocabulary
# --------------------------------------------------------------------------- #
#: Work between ``deterministic_qa_done`` and the end of the review block.
REVIEW_INPUT_PHASES = (
    "review_input_previous_context",
    "review_input_state_copy",
    # Same full-state copy, but taken inside the *repair* window for the shadow
    # (blind) review.  Kept separate so it is never charged to semantic_review.
    "repair_shadow_state_copy",
    "review_input_evidence_index",
    "review_input_packet_build",
    "reviewer_prompt_assemble",
)

#: Fine-grained breakdown of ``review_input_packet_build`` -- the Phase 6 top
#: local-CPU bucket.  Phase 6 could only report it as one number (1.24s CPU at
#: n=3,000), which is not enough to decide *what* to change.  Each entry here
#: isolates one concrete piece of work so the answer comes from measurement.
#:
#: ``review_input_pair_prefix`` is the documented anti-pattern: the packet
#: materialises a full ``dict(pair)`` copy of every committed pair while the
#: consumer only ever indexes the current batch and its bounded dependency set.
#: ``*_memory_*`` split :func:`_scoped_project_memory`; ``*_glossary*`` and
#: ``review_input_relevant_glossary`` sit inside
#: ``translation_core.build_review_packet`` and are the *second* independent
#: ``O(G x S)`` term scan in the same call.
REVIEW_PACKET_PHASES = (
    "review_input_pair_prefix",
    "review_input_project_memory",
    "review_input_memory_normalize",
    "review_input_memory_hash",
    "review_input_memory_compile",
    "review_input_memory_scan",
    "review_input_memory_project",
    "review_input_review_packet_core",
    "review_input_bounded",
    "review_input_truth",
    "review_input_glossary_resolve",
    "review_input_relevant_glossary",
    "review_input_memory_deepcopy",
    "review_input_fingerprint",
)

#: The reviewer round itself: transport, parse, normalize, evidence fetch.
#:
#: ``reviewer_suggestion_overlay`` is instrumented: it is the expensive nested
#: block (overlay construction, deterministic recheck, blind validation,
#: overlay evaluation).  ``reviewer_postprocess`` is deliberately *not*
#: instrumented inline -- it is the thin remainder of the same per-finding
#: loop, and wrapping it would have meant re-indenting the whole ladder.  The
#: audit reports it as a **derived residual** (semantic_review total minus the
#: instrumented phases), labelled as derived everywhere it appears.
REVIEWER_PHASES = (
    "reviewer_transport",
    "reviewer_parse",
    "reviewer_findings_normalize",
    "reviewer_evidence_request",
    "reviewer_suggestion_overlay",
    "reviewer_blind_validation",
    "reviewer_postprocess",
)

#: Work between ``state_commit_done`` and the next ``generation_started``.
COMMIT_PHASES = (
    "commit_timing_record",
    "commit_log_append",
    "commit_recheck_deterministic",
    "commit_conflict_detection",
    "commit_findings_merge",
    "commit_knowledge_feedback",
    "commit_knowledge_llm",
    "commit_tm_eligibility",
    "commit_state_commit",
    "commit_tm_promotion_flush",
    "commit_telemetry",
)

#: Byte-level durability work, recorded separately from the buckets above so a
#: journal write is never mistaken for review or commit CPU.
IO_PHASES = (
    "journal_serialize",
    "journal_append",
    "journal_fsync",
    "audit_event_append",
    "audit_event_fsync",
)

#: Every phase this module knows how to aggregate.
ALL_PHASES = (REVIEW_INPUT_PHASES + REVIEW_PACKET_PHASES + REVIEWER_PHASES
              + COMMIT_PHASES + IO_PHASES)


def _percentile(ordered: List[float], fraction: float) -> float:
    """Nearest-rank percentile; same convention as the other perf artifacts."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    index = int(round(fraction * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, index))]


class PhaseSink:
    """Mutable per-invocation record a phase body may annotate.

    ``items`` / ``chars`` / ``bytes_`` / ``objects`` are only meaningful for
    phases that traverse, receive, write or build something; every other phase
    leaves them at zero so the aggregate is honest instead of double-counting
    the same payload.
    """

    __slots__ = ("items", "chars", "bytes_", "objects")

    def __init__(self) -> None:
        self.items = 0
        self.chars = 0
        self.bytes_ = 0
        self.objects = 0

    def traversed(self, count: int) -> None:
        """Record how many items this phase visited (O(n) evidence)."""
        self.items += max(0, int(count))

    def received(self, size: int) -> None:
        """Record response size in characters (the reviewer's payload cost)."""
        self.chars += max(0, int(size))

    def written(self, size: int) -> None:
        self.bytes_ += max(0, int(size))

    def materialized(self, count: int) -> None:
        """Record how many objects this phase allocated.

        ``items`` counts what a phase *looked at*; ``objects`` counts what it
        *built*.  Keeping them apart is what lets the report distinguish
        "scanned the prefix" from "rebuilt the prefix" -- they have very
        different fixes.
        """
        self.objects += max(0, int(count))


_NULL_SINK = PhaseSink()


class CriticalPathProfiler:
    """Accumulates phase durations, thread-CPU and named counters."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._lock = threading.Lock()
        self.enabled = bool(enabled)
        self._durations: Dict[str, List[float]] = {name: [] for name in ALL_PHASES}
        self._cpu: Dict[str, float] = {name: 0.0 for name in ALL_PHASES}
        self._wall: Dict[str, float] = {name: 0.0 for name in ALL_PHASES}
        self._items: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._chars: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._bytes: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._objects: Dict[str, int] = {name: 0 for name in ALL_PHASES}
        self._counters: Dict[str, float] = {}

    # -- recording ---------------------------------------------------------
    def record(
        self,
        phase: str,
        wall_s: float,
        *,
        cpu_s: float = 0.0,
        items: int = 0,
        chars: int = 0,
        bytes_written: int = 0,
        objects: int = 0,
    ) -> None:
        if not self.enabled:
            return
        wall_s = max(0.0, float(wall_s))
        cpu_s = max(0.0, float(cpu_s))
        with self._lock:
            if phase not in self._durations:
                # Unknown phase: keep the timeline honest rather than dropping.
                self._durations[phase] = []
                self._cpu[phase] = 0.0
                self._wall[phase] = 0.0
                self._items[phase] = 0
                self._chars[phase] = 0
                self._bytes[phase] = 0
                self._objects[phase] = 0
            self._durations[phase].append(wall_s)
            self._wall[phase] += wall_s
            self._cpu[phase] += cpu_s
            self._items[phase] += max(0, int(items))
            self._chars[phase] += max(0, int(chars))
            self._bytes[phase] += max(0, int(bytes_written))
            self._objects[phase] += max(0, int(objects))

    @contextlib.contextmanager
    def phase(self, name: str):
        """Time one phase; the yielded :class:`PhaseSink` carries counts."""
        if not self.enabled:
            yield _NULL_SINK
            return
        sink = PhaseSink()
        wall_started = time.perf_counter()
        cpu_started = time.thread_time()
        try:
            yield sink
        finally:
            self.record(
                name,
                time.perf_counter() - wall_started,
                cpu_s=time.thread_time() - cpu_started,
                items=sink.items,
                chars=sink.chars,
                bytes_written=sink.bytes_,
                objects=sink.objects,
            )

    def count(self, name: str, value: float = 1.0) -> None:
        """Accumulate a named counter that is not a duration."""
        if not self.enabled:
            return
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + float(value)

    # -- reporting ---------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            for name in list(self._durations):
                self._durations[name] = []
                self._cpu[name] = 0.0
                self._wall[name] = 0.0
                self._items[name] = 0
                self._chars[name] = 0
                self._bytes[name] = 0
                self._objects[name] = 0
            self._counters = {}

    def totals(self) -> Dict[str, Any]:
        """Cheap cumulative readings, safe to sample *inside* a long run.

        ``snapshot()`` sorts and copies every recorded duration, so it is
        O(calls so far) -- correct once at the end, but sampling it per batch
        would itself turn a per-batch sampler into an O(n^2) path and
        contaminate the very scaling it is meant to observe.  ``totals()`` reads
        only the running scalars, so a sampler can diff consecutive readings at
        a cost proportional to the number of *phases*, not the number of calls.
        """
        with self._lock:
            return {
                "phases": {
                    name: {
                        "calls": len(self._durations.get(name) or ()),
                        "wall_s": self._wall.get(name, 0.0),
                        "cpu_s": self._cpu.get(name, 0.0),
                        "items": self._items.get(name, 0),
                        "chars": self._chars.get(name, 0),
                        "bytes_written": self._bytes.get(name, 0),
                        "objects": self._objects.get(name, 0),
                    }
                    for name in self._wall
                },
                "counters": dict(self._counters),
            }

    def snapshot(self) -> Dict[str, Any]:
        """Aggregate per-phase calls / wall / CPU / wait / percentiles."""
        with self._lock:
            durations = {n: list(v) for n, v in self._durations.items()}
            wall = dict(self._wall)
            cpu = dict(self._cpu)
            items = dict(self._items)
            chars = dict(self._chars)
            written = dict(self._bytes)
            objects = dict(self._objects)
            counters = dict(self._counters)

        phases: Dict[str, Dict[str, Any]] = {}
        for name, values in durations.items():
            ordered = sorted(values)
            total_wall = wall.get(name, 0.0)
            total_cpu = cpu.get(name, 0.0)
            count = len(values)
            phases[name] = {
                "calls": count,
                "total_wall_s": round(total_wall, 6),
                "total_cpu_s": round(total_cpu, 6),
                # Derived, never stored: a blocked phase shows up as wait.
                "wait_s": round(max(0.0, total_wall - total_cpu), 6),
                "avg_wall_s": round(total_wall / count, 6) if count else 0.0,
                "p50_s": round(_percentile(ordered, 0.50), 6),
                "p95_s": round(_percentile(ordered, 0.95), 6),
                "max_s": round(ordered[-1], 6) if ordered else 0.0,
                "items": items.get(name, 0),
                "chars": chars.get(name, 0),
                "bytes_written": written.get(name, 0),
                "objects": objects.get(name, 0),
            }
        return {
            "enabled": self.enabled,
            "phases": phases,
            "counters": {k: round(v, 6) for k, v in sorted(counters.items())},
        }


_PROFILER = CriticalPathProfiler(enabled=_env_enabled())

#: One shared null context, reused for every disabled ``phase()`` call.  A fresh
#: ``nullcontext`` per call would allocate on the disabled path, which is the
#: one place overhead must stay at zero.
_NULL_CONTEXT = contextlib.nullcontext(_NULL_SINK)


def profiler() -> CriticalPathProfiler:
    return _PROFILER


def enabled() -> bool:
    return _PROFILER.enabled


def enable() -> CriticalPathProfiler:
    _PROFILER.enabled = True
    return _PROFILER


def disable() -> None:
    _PROFILER.enabled = False


def reset() -> None:
    _PROFILER.reset()


def snapshot() -> Dict[str, Any]:
    return _PROFILER.snapshot()


def totals() -> Dict[str, Any]:
    """Cumulative scalars only; cheap enough to sample every batch."""
    return _PROFILER.totals()


def phase(name: str):
    """Time one critical-path phase. No-op (nullcontext) when disabled."""
    if not _PROFILER.enabled:
        return _NULL_CONTEXT
    return _PROFILER.phase(name)


def count(name: str, value: float = 1.0) -> None:
    _PROFILER.count(name, value)


def timed(phase_name: str) -> Callable[[Callable], Callable]:
    """Decorator: time every call of a helper under ``phase_name``.

    Used for leaf helpers that are called from several places (so an inline
    ``with`` block would have to be repeated).  When the profiler is disabled
    the wrapper is a single extra frame and nothing else -- no context manager,
    no clock reads -- which keeps the off path honest.
    """
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not _PROFILER.enabled:
                return fn(*args, **kwargs)
            with _PROFILER.phase(phase_name):
                return fn(*args, **kwargs)
        return wrapper
    return decorate
