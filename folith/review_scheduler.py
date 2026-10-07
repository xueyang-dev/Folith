"""Reviewer Overlap Scheduler for the translation pipeline.

Decouples the network-bound semantic review of batch i from the sequential
translation generation of batch i+1, while keeping:
1. Translator concurrency strictly 1 (sequential generation B1 -> B2 -> B3);
2. Commit strictly ordered (C1 -> C2 -> C3);
3. Reviewer operating on an immutable BatchReviewSnapshot (no mutable state reads);
4. Bounded backpressure (MAX_REVIEW_IN_FLIGHT);
5. Reusable provider rate controllers;
6. Deterministic crash recovery & clean cancellation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class BatchLifecycleState(str, Enum):
    """Runtime lifecycle states for a batch in the translation pipeline."""
    GENERATED = "GENERATED"
    DETERMINISTIC_VALIDATED = "DETERMINISTIC_VALIDATED"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEW_RUNNING = "REVIEW_RUNNING"
    REVIEW_READY = "REVIEW_READY"
    COMMIT_READY = "COMMIT_READY"
    COMMITTED = "COMMITTED"


@dataclass
class BatchReviewSnapshot:
    """Immutable snapshot of a batch after generation, deterministic QA and repair.

    The Reviewer worker reads ONLY from this snapshot. It is completely isolated
    from concurrent changes to global pairs or state.
    """
    batch_index: int
    offset: int
    segment_ids: List[int]
    source_texts: List[str]
    generated_targets: List[str]
    initial_targets: List[str]
    batch_pairs: List[Dict[str, Any]]
    glossary: List[Dict[str, Any]]
    glossary_text: str
    glossary_ids: List[str]
    glossary_hash: str
    style_rules: str
    target_lang: str
    section_profile: Dict[str, Any]
    deterministic_findings: List[Dict[str, Any]]
    blocked_local_indexes: Set[int]
    review_context: Dict[str, Any]
    formal_segment_ids: List[int]
    evidence_index: Any
    formal_packet: Dict[str, Any]
    input_hash: str
    translator_model: str = ""
    paras: List[str] = field(default_factory=list)
    committed_pairs: List[Dict[str, Any]] = field(default_factory=list)
    findings_all: List[Dict[str, Any]] = field(default_factory=list)
    document_profile: Dict[str, Any] = field(default_factory=dict)
    document_synopsis: Dict[str, Any] = field(default_factory=dict)
    section_digests: List[Dict[str, Any]] = field(default_factory=list)
    state_ref: Dict[str, Any] = field(default_factory=dict)
    base_evidence_count: int = 0
    #: Deterministic pre-review gate verdict (``risk_selective`` mode only).
    #: A skipped batch is never sent to the reviewer, never marked reviewed and
    #: never promoted to translation memory.
    review_gate_skip: bool = False
    review_gate_reason: str = ""
    review_gate_signals: Optional[Dict[str, Any]] = None
    created_at_s: float = field(default_factory=time.perf_counter)

    @classmethod
    def create(
        cls,
        *,
        batch_index: int,
        offset: int,
        batch_pairs: Sequence[Dict[str, Any]],
        glossary: Sequence[Dict[str, Any]],
        glossary_text: str,
        glossary_ids: Sequence[str],
        glossary_hash: str,
        style_rules: str,
        target_lang: str,
        section_profile: Dict[str, Any],
        deterministic_findings: Sequence[Dict[str, Any]],
        blocked_local_indexes: Set[int],
        review_context: Dict[str, Any],
        formal_segment_ids: Sequence[int],
        evidence_index: Any,
        formal_packet: Dict[str, Any],
        translator_model: str = "",
        paras: Optional[Sequence[str]] = None,
        committed_pairs: Optional[Sequence[Dict[str, Any]]] = None,
        findings_all: Optional[Sequence[Dict[str, Any]]] = None,
        document_profile: Optional[Dict[str, Any]] = None,
        document_synopsis: Optional[Dict[str, Any]] = None,
        section_digests: Optional[Sequence[Dict[str, Any]]] = None,
        state_ref: Optional[Dict[str, Any]] = None,
        base_evidence_count: int = 0,
        review_gate_skip: bool = False,
        review_gate_reason: str = "",
        review_gate_signals: Optional[Dict[str, Any]] = None,
    ) -> "BatchReviewSnapshot":
        segment_ids = list(formal_segment_ids)
        source_texts = [str(p.get("source") or "") for p in batch_pairs]
        generated_targets = [str(p.get("target") or "") for p in batch_pairs]
        initial_targets = [str(p.get("initial_target") or "") for p in batch_pairs]
        pairs_copy = [dict(p) for p in batch_pairs]

        hash_payload = {
            "bi": int(batch_index),
            "offset": int(offset),
            "sources": source_texts,
            "targets": generated_targets,
            "glossary_hash": str(glossary_hash or ""),
            "segment_ids": segment_ids,
        }
        digest = hashlib.sha256(
            json.dumps(hash_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        return cls(
            batch_index=int(batch_index),
            offset=int(offset),
            segment_ids=segment_ids,
            source_texts=source_texts,
            generated_targets=generated_targets,
            initial_targets=initial_targets,
            batch_pairs=pairs_copy,
            glossary=list(glossary),
            glossary_text=str(glossary_text or ""),
            glossary_ids=list(glossary_ids),
            glossary_hash=str(glossary_hash or ""),
            style_rules=str(style_rules or ""),
            target_lang=str(target_lang or ""),
            section_profile=dict(section_profile or {}),
            deterministic_findings=[dict(f) for f in deterministic_findings],
            blocked_local_indexes=set(blocked_local_indexes),
            review_context=dict(review_context or {}),
            formal_segment_ids=segment_ids,
            evidence_index=evidence_index,
            formal_packet=dict(formal_packet or {}),
            input_hash=digest,
            translator_model=str(translator_model or ""),
            paras=list(paras or []),
            committed_pairs=[dict(p) for p in (committed_pairs or [])],
            findings_all=[dict(f) for f in (findings_all or [])],
            document_profile=dict(document_profile or {}),
            document_synopsis=dict(document_synopsis or {}),
            section_digests=[dict(d) for d in (section_digests or [])],
            state_ref=dict(state_ref or {}),
            base_evidence_count=int(base_evidence_count or 0),
            review_gate_skip=bool(review_gate_skip),
            review_gate_reason=str(review_gate_reason or ""),
            review_gate_signals=dict(review_gate_signals or {}) or None,
            created_at_s=time.perf_counter(),
        )


@dataclass
class ReviewResult:
    """Outcome of a batch review task, prepared for ordered commit."""
    batch_index: int
    offset: int
    review_succeeded: bool
    review_failed: bool
    rfindings: List[Dict[str, Any]]
    review_trace: Optional[Dict[str, Any]]
    review_event_id: Optional[str]
    formal_records: List[Dict[str, Any]]
    promoted_review_events: List[Any]
    repair_overlays: List[Dict[str, Any]]
    review_evidence: List[Dict[str, Any]]
    accepted_suggestions: Dict[int, str]  # local_index -> accepted suggested target
    #: True when the deterministic pre-review gate skipped this batch.  The
    #: result carries explicit provenance and never claims a review happened.
    review_skipped: bool = False
    review_skip_reason: str = ""
    review_skip_trace: Optional[Dict[str, Any]] = None
    # Timings
    queue_wait_s: float = 0.0
    api_latency_s: float = 0.0
    review_parse_s: float = 0.0
    total_wall_s: float = 0.0
    llm_requests: int = 0
    llm_failures: int = 0
    started_at_s: float = 0.0
    finished_at_s: float = 0.0


class ReviewScheduler:
    """Coordinates asynchronous review execution and ordered commit barrier.

    Features:
    - Bounded in-flight queue (backpressure).
    - Reviewer runs asynchronously; commits remain strictly ordered by batch index.
    - Safe cancellation and shutdown.
    - Timing accounting (queue wait, api latency, hidden review latency).
    """

    def __init__(self, max_in_flight: int = 2, reviewer_workers: int = 1):
        self.max_in_flight = max(1, int(max_in_flight))
        self.reviewer_workers = max(1, int(reviewer_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.reviewer_workers, thread_name_prefix="ft-rev"
        )
        self._condition = threading.Condition()
        self._results: Dict[int, ReviewResult] = {}
        self._errors: Dict[int, BaseException] = {}
        self._in_flight: Set[int] = set()
        self._cancelled = False
        self._max_backlog = 0
        self._lifecycle: Dict[int, BatchLifecycleState] = {}
        self._overlap_stats = {
            "total_submitted": 0,
            "total_completed": 0,
            "total_queue_wait_s": 0.0,
            "total_review_wall_s": 0.0,
            "total_api_latency_s": 0.0,
            "hidden_review_seconds": 0.0,
            "exposed_review_seconds": 0.0,
            "translator_idle_s": 0.0,
            "reviewer_idle_s": 0.0,
            "max_backlog": 0,
        }

    def set_lifecycle(self, batch_index: int, state: BatchLifecycleState) -> None:
        with self._condition:
            self._lifecycle[batch_index] = state

    def get_lifecycle(self, batch_index: int) -> Optional[BatchLifecycleState]:
        with self._condition:
            return self._lifecycle.get(batch_index)

    def is_cancelled(self) -> bool:
        with self._condition:
            return self._cancelled

    def in_flight_count(self) -> int:
        with self._condition:
            return len(self._in_flight)

    def wait_for_slot(self) -> None:
        """Block if current in-flight reviews exceed or meet max_in_flight."""
        with self._condition:
            while len(self._in_flight) >= self.max_in_flight and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RuntimeError("任务已请求取消")

    def submit(
        self,
        snapshot: BatchReviewSnapshot,
        worker_fn: Callable[[BatchReviewSnapshot], ReviewResult],
    ) -> None:
        """Submit a snapshot to the reviewer thread pool with backpressure."""
        bi = snapshot.batch_index
        with self._condition:
            if self._cancelled:
                raise RuntimeError("任务已请求取消")
            while len(self._in_flight) >= self.max_in_flight and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RuntimeError("任务已请求取消")

            self._in_flight.add(bi)
            self._lifecycle[bi] = BatchLifecycleState.REVIEW_PENDING
            self._overlap_stats["total_submitted"] += 1
            cur_backlog = len(self._in_flight)
            if cur_backlog > self._max_backlog:
                self._max_backlog = cur_backlog
                self._overlap_stats["max_backlog"] = cur_backlog

        enqueued_at = time.perf_counter()

        def _task_wrapper():
            started_at = time.perf_counter()
            queue_wait = started_at - enqueued_at
            with self._condition:
                if self._cancelled:
                    self._in_flight.discard(bi)
                    self._condition.notify_all()
                    return
                self._lifecycle[bi] = BatchLifecycleState.REVIEW_RUNNING

            try:
                result = worker_fn(snapshot)
                result.queue_wait_s = queue_wait
                result.started_at_s = started_at
                result.finished_at_s = time.perf_counter()
                result.total_wall_s = result.finished_at_s - started_at
                with self._condition:
                    self._results[bi] = result
                    self._lifecycle[bi] = BatchLifecycleState.REVIEW_READY
                    self._overlap_stats["total_completed"] += 1
                    self._overlap_stats["total_queue_wait_s"] += queue_wait
                    self._overlap_stats["total_review_wall_s"] += result.total_wall_s
                    self._overlap_stats["total_api_latency_s"] += result.api_latency_s
            except BaseException as exc:
                with self._condition:
                    self._errors[bi] = exc
            finally:
                with self._condition:
                    self._in_flight.discard(bi)
                    self._condition.notify_all()

        self._executor.submit(_task_wrapper)

    def has_result(self, batch_index: int) -> bool:
        with self._condition:
            return batch_index in self._results or batch_index in self._errors

    def pop_result(self, batch_index: int) -> Optional[ReviewResult]:
        """Return the ReviewResult for batch_index if ready, without blocking."""
        with self._condition:
            if batch_index in self._errors:
                err = self._errors.pop(batch_index)
                raise err
            return self._results.pop(batch_index, None)

    def wait_for_batch(
        self, batch_index: int, timeout: Optional[float] = None
    ) -> ReviewResult:
        """Block until batch_index review result is ready, then return it."""
        deadline = time.perf_counter() + timeout if timeout is not None else None
        with self._condition:
            while (
                batch_index not in self._results
                and batch_index not in self._errors
                and not self._cancelled
            ):
                if deadline is not None:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        raise TimeoutError(f"Review for batch {batch_index} timed out")
                    self._condition.wait(timeout=remaining)
                else:
                    self._condition.wait()

            if self._cancelled and batch_index not in self._results:
                raise RuntimeError("任务已请求取消")

            if batch_index in self._errors:
                err = self._errors.pop(batch_index)
                raise err
            return self._results.pop(batch_index)

    def cancel(self) -> None:
        """Signal cancellation to all in-flight and queued review tasks."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the internal executor pool."""
        self.cancel()
        self._executor.shutdown(wait=wait)

    def record_hidden_latency(self, hidden_s: float) -> None:
        with self._condition:
            self._overlap_stats["hidden_review_seconds"] += max(0.0, float(hidden_s))

    def record_exposed_latency(self, exposed_s: float) -> None:
        with self._condition:
            self._overlap_stats["exposed_review_seconds"] += max(0.0, float(exposed_s))

    def record_translator_idle(self, idle_s: float) -> None:
        with self._condition:
            self._overlap_stats["translator_idle_s"] += max(0.0, float(idle_s))

    def stats_snapshot(self) -> Dict[str, Any]:
        with self._condition:
            return dict(self._overlap_stats)


def compute_overlap_metrics(
    stats: Dict[str, Any],
    total_run_wall_s: float = 0.0,
    generation_wall_s: float = 0.0,
    review_wall_s: float = 0.0,
) -> Dict[str, Any]:
    total_review = float(review_wall_s or stats.get("total_review_wall_s") or 0.0)
    hidden = max(0.0, float(stats.get("hidden_review_seconds") or 0.0))
    exposed = max(0.0, float(stats.get("exposed_review_seconds") or 0.0))
    if total_review > 0 and hidden == 0.0 and exposed == 0.0:
        # If interval tracking was not used, compute from serial sum vs run wall:
        exposed = max(0.0, total_run_wall_s - generation_wall_s) if total_run_wall_s > generation_wall_s else 0.0
        hidden = max(0.0, total_review - exposed)
    hidden_ratio = round(hidden / total_review, 4) if total_review > 0 else 0.0
    return {
        "total_review_seconds": round(total_review, 4),
        "hidden_review_seconds": round(hidden, 4),
        "exposed_review_seconds": round(exposed, 4),
        "hidden_review_ratio": hidden_ratio,
        "translator_idle_seconds": round(float(stats.get("translator_idle_s") or 0.0), 4),
        "reviewer_idle_seconds": round(float(stats.get("reviewer_idle_s") or 0.0), 4),
        "max_review_backlog": int(stats.get("max_backlog") or 0),
        "total_submitted": int(stats.get("total_submitted") or 0),
        "total_completed": int(stats.get("total_completed") or 0),
    }
