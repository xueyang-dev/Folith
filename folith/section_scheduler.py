"""Section-aware batch scheduling for translation concurrency.

Why order matters
-----------------
A translation batch reads continuity state that is only meaningful *in document
order*: ``previous_target`` (the accepted targets of earlier segments), the
provisional knowledge candidates, and the entity registry.  Running batches out
of order can therefore change the translation context even when the batch
contents are identical — which is why the product default stays at
``translation_concurrency=1``.

What this module provides
-------------------------
A planner that keeps batch order inside one semantic unit / section while
allowing *independent* sections to run in parallel:

* no wave ever contains two batches from the same section, so a batch never
  starts before its in-section predecessor has been produced;
* waves are capped at the requested concurrency;
* the plan is a pure function of (batch sizes, section boundaries, concurrency)
  and is returned as data, so the caller can keep committing strictly in global
  batch order.

This is a scheduling primitive, not a policy change.  It is off by default
(:func:`default_enabled`) until a quality-equivalence benchmark justifies
raising the default concurrency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

SECTION_SCHEDULER_ENV = "FOLIOTHREAD_TRANSLATION_SECTION_SCHEDULER"


def default_enabled() -> bool:
    raw = os.environ.get(SECTION_SCHEDULER_ENV)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BatchTask:
    """One translation batch and the section it belongs to."""

    index: int
    section_id: str
    section_index: int
    offset: int
    size: int


@dataclass(frozen=True)
class Schedule:
    """A wave plan plus the per-section ordering it preserves."""

    tasks: Tuple[BatchTask, ...]
    waves: Tuple[Tuple[int, ...], ...]
    concurrency: int

    def section_order(self) -> Dict[str, List[int]]:
        groups: Dict[str, List[int]] = {}
        for task in sorted(self.tasks, key=lambda item: item.index):
            groups.setdefault(task.section_id, []).append(task.index)
        return groups

    def to_dict(self) -> Dict[str, object]:
        return {
            "concurrency": self.concurrency,
            "waves": [list(wave) for wave in self.waves],
            "sections": {key: value for key, value in self.section_order().items()},
        }


def build_tasks(batch_sizes: Sequence[int],
                section_index_by_offset: Sequence[int],
                section_ids: Optional[Sequence[str]] = None) -> Tuple[BatchTask, ...]:
    """Pair each batch with the section its first segment belongs to."""
    tasks: List[BatchTask] = []
    offset = 0
    for index, size in enumerate(batch_sizes):
        if offset >= len(section_index_by_offset):
            break
        section_index = int(section_index_by_offset[offset])
        if section_ids is not None and section_index < len(section_ids):
            section_id = str(section_ids[section_index])
        else:
            section_id = f"section-{section_index}"
        tasks.append(BatchTask(index=index, section_id=section_id,
                               section_index=section_index, offset=offset,
                               size=int(size)))
        offset += int(size)
    return tuple(tasks)


def plan_waves(tasks: Sequence[BatchTask], concurrency: int) -> Schedule:
    """Pack batches into waves that never repeat a section.

    Batches are considered in document order, so within a section the earlier
    batch is always placed in an earlier (or the same, which is forbidden) wave
    than its successor.  A batch is deferred to the next wave when its section is
    already represented in the current one.
    """
    limit = max(1, int(concurrency or 1))
    ordered = sorted(tasks, key=lambda item: item.index)
    waves: List[List[int]] = []
    pending = list(ordered)
    while pending:
        wave: List[int] = []
        used_sections: set = set()
        deferred: List[BatchTask] = []
        for task in pending:
            if len(wave) >= limit:
                deferred.append(task)
                continue
            if task.section_id in used_sections:
                deferred.append(task)
                continue
            wave.append(task.index)
            used_sections.add(task.section_id)
        waves.append(wave)
        pending = deferred
    return Schedule(tasks=tuple(ordered),
                    waves=tuple(tuple(wave) for wave in waves),
                    concurrency=limit)


def build_schedule(batch_sizes: Sequence[int],
                   section_index_by_offset: Sequence[int],
                   *, concurrency: int,
                   section_ids: Optional[Sequence[str]] = None) -> Schedule:
    return plan_waves(
        build_tasks(batch_sizes, section_index_by_offset, section_ids),
        concurrency)


def serial_schedule(batch_sizes: Sequence[int],
                    section_index_by_offset: Sequence[int],
                    section_ids: Optional[Sequence[str]] = None) -> Schedule:
    """The reference plan: exactly the document order, one batch per wave."""
    tasks = build_tasks(batch_sizes, section_index_by_offset, section_ids)
    return Schedule(tasks=tasks,
                    waves=tuple((task.index,) for task in tasks),
                    concurrency=1)
