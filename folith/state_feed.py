"""Batch-level delta feed projected from the durable state journal.

Why this module exists
----------------------
``state.json`` is the authoritative snapshot, and ``state_journal.jsonl`` is the
durability record the pipeline appends one committed translation batch at a
time (see :mod:`folith.storage.state_journal`).  The workbench used to react to
every one of those appends by re-reading and rebuilding the whole state; this
module supplies the missing read-side primitive: *turn the journal suffix into
typed deltas*.

Authority model (the point of the design)
-----------------------------------------
The delta is a **read-side projection of the write record**, not a second source
of truth.  There is no new file, no new write path and no new event bus: the
event is the journal record the pipeline already writes, and this module only
reads it.  Consequently

* deleting the feed changes nothing about the document, only about how much
  work the UI does;
* an A2 subprocess needs no IPC format -- it writes the same ``state_journal``
  and the GUI reads the same file (the payload is plain JSON);
* every doubt (sequence gap, corrupt record, unsupported schema, oversized
  record, backlog) is answered with :class:`ResyncRequired`, i.e. "reload the
  authoritative state", which is always correct.

Contract fields
---------------
``DeltaBatch`` carries ``seq``/``from_seq`` (monotonic journal sequence),
``batch``/``batches`` (pipeline batch ids), ``offset`` (absolute pair offset),
``segments`` (only the changed segments, each with the absolute pair value and
the set of changed baseline fields), ``counters`` / ``delivery`` (small whole
values from the record's ``set``), ``findings_delta`` (counts only -- content
stays in the slow projection), ``progress`` (read in the same tick from the
existing ``runtime_state.json``, never duplicated into the journal) and
``domains`` (which independently-cached UI projection went dirty).

Qt-free and core-free: it needs a journal path and a state path, nothing else.
Every I/O failure is reported, never swallowed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from folith.translation_candidates import BASELINE_FIELDS, pair_signature

#: Rollback switch: ``FOLIOTHREAD_WORKBENCH_DELTA=0`` puts the client back on
#: the whole-state poll without touching any data.
DELTA_ENV = "FOLIOTHREAD_WORKBENCH_DELTA"

#: Schema of the delta payload itself (the journal record keeps its own
#: ``schema`` field; both are checked).
DELTA_SCHEMA_VERSION = 1

#: Journal schemas this reader understands.  Mirrors
#: ``state_journal.READABLE_SCHEMA_VERSIONS``; a newer record is a reported gap,
#: never silently ignored.
READABLE_JOURNAL_SCHEMAS = (1, 2)

#: The only mutation this feed knows.  The journal may grow other typed
#: mutations later; an unknown one is a permanent fallback for this job.
MUTATION_TRANSLATION_BATCH = "translation_batch_committed"

#: Bounds that keep one poll bounded on both the byte and the record axis.
DEFAULT_MAX_RECORD_BYTES = 512 * 1024
DEFAULT_MAX_RECORDS_PER_POLL = 32
DEFAULT_MAX_BACKLOG = 256

ORIGIN_PIPELINE = "pipeline"

#: Reasons a caller must (or may) fall back to a whole-state reload.
REASON_SEQ_GAP = "seq_gap"
REASON_CORRUPT_RECORD = "corrupt_record"
REASON_UNKNOWN_SCHEMA = "unknown_schema"
REASON_UNKNOWN_MUTATION = "unknown_mutation"
REASON_RECORD_TOO_LARGE = "record_too_large"
REASON_BACKLOG = "backlog"

#: Small whole values copied out of the record's ``set`` object.  Everything
#: else in ``set`` (an entity registry, academic state...) only marks a domain
#: dirty; its content stays in the slow projection, which is what keeps the
#: delta bounded.
COUNTER_KEYS = ("review_stats", "has_blocking", "p1_done", "p3_done",
                "stage", "translation_truth")
DELIVERY_KEYS = ("delivery_status", "delivery_approved_by_human",
                 "delivery_approval", "final_qa")

#: Which UI projection domains each journal key dirties.  This is the single
#: mapping the view uses instead of comparing state-object identity.
_SET_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "review_stats": ("counts", "review", "stage"),
    "has_blocking": ("stage", "review", "followup"),
    "p1_done": ("stage", "counts"),
    "p3_done": ("stage", "academic", "details"),
    "stage": ("stage",),
    "translation_truth": ("stage", "counts", "details"),
    "delivery_status": ("delivery", "stage"),
    "delivery_approved_by_human": ("delivery",),
    "delivery_approval": ("delivery",),
    "final_qa": ("delivery", "academic"),
    "academic_state": ("academic", "details"),
    "entity_registry": ("details", "followup", "terms"),
    "knowledge_feedback_policy": ("details",),
    "knowledge_feedback_failures": ("details",),
    "terminology_variant_decisions": ("terms", "details"),
    "concept_decisions": ("terms", "details"),
}
_EXTEND_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "findings": ("review", "followup"),
    "review_evidence": ("review",),
    "repair_overlays": ("followup", "review"),
    "human_actions": ("activity", "details"),
    "knowledge_events": ("activity", "academic", "details"),
    "context_packet_log": ("academic", "details"),
    "glossary_injection_log": ("terms", "details"),
}
_PATCH_DOMAINS = ("review", "followup", "terms")
_CANDIDATE_DOMAINS = ("terms", "followup", "stage")

#: A delta always changes the grid and, through it, the counters.
_ALWAYS_DOMAINS = ("rows", "counts")

_PROGRESS_KEYS = ("status", "stage", "stage_id", "stage_progress",
                  "overall_progress", "completed_units", "total_units",
                  "pause_requested", "cancel_requested", "phase", "operation")


def delta_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the incremental feed is enabled for this process."""
    source = os.environ if environ is None else environ
    value = source.get(DELTA_ENV)
    if value is None or not str(value).strip():
        return True
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Idle:
    """The journal has nothing new (or a torn tail is still being written)."""

    reason: str = "unchanged"


@dataclass(frozen=True)
class SnapshotChanged:
    """``state.json`` advanced; the caller decides whether a reload is needed.

    ``worker_active`` is the caller's observation: while the pipeline worker
    owns the task, a snapshot write is normally its own compaction (the state
    already contains the deltas applied so far), so the caller may keep the
    incremental path.  With no live worker it is an external write and the
    caller must reload.
    """

    worker_active: bool = False


@dataclass(frozen=True)
class ResyncRequired:
    """The delta chain can no longer be trusted; reload the whole state."""

    reason: str
    detail: str = ""
    #: ``True`` means "do not try the feed again for this job" (unknown schema
    #: or mutation): the caller should stay on whole-state polling.
    permanent: bool = False


@dataclass(frozen=True)
class LegacyMode:
    """The feed is switched off (env rollback or a permanent fallback)."""

    reason: str = "disabled_by_env"


@dataclass(frozen=True)
class FeedCursor:
    seq: int = 0
    offset: int = 0
    snapshot: Optional[Tuple[int, int]] = None


@dataclass(frozen=True)
class SegmentChange:
    """One segment a delta says changed, with the absolute pair value."""

    index: int
    segment_uid: str
    pair: Mapping[str, Any] = field(default_factory=dict)
    fields: Tuple[str, ...] = ()


def _as_payload_segment(segment: SegmentChange) -> Dict[str, Any]:
    return {
        "index": int(segment.index),
        "segment_uid": str(segment.segment_uid or ""),
        "fields": list(segment.fields),
        "pair": dict(segment.pair),
    }


@dataclass(frozen=True)
class DeltaBatch:
    """One poll's worth of committed batches, already applied to nothing."""

    schema: int = DELTA_SCHEMA_VERSION
    job_id: str = ""
    from_seq: int = 0
    seq: int = 0
    batch: int = 0
    batches: Tuple[int, ...] = ()
    offset: int = 0
    segments: Tuple[SegmentChange, ...] = ()
    counters: Mapping[str, Any] = field(default_factory=dict)
    delivery: Mapping[str, Any] = field(default_factory=dict)
    findings_delta: Mapping[str, int] = field(default_factory=dict)
    progress: Optional[Mapping[str, Any]] = None
    domains: Tuple[str, ...] = ()
    origin: str = ORIGIN_PIPELINE
    bytes: int = 0

    def as_payload(self) -> Dict[str, Any]:
        """Plain JSON payload (A2 can transport this unchanged)."""
        return {
            "schema": int(self.schema),
            "job_id": str(self.job_id),
            "from_seq": int(self.from_seq),
            "seq": int(self.seq),
            "batch": int(self.batch),
            "batches": [int(value) for value in self.batches],
            "offset": int(self.offset),
            "segments": [_as_payload_segment(segment) for segment in self.segments],
            "counters": dict(self.counters),
            "delivery": dict(self.delivery),
            "findings_delta": dict(self.findings_delta),
            "progress": dict(self.progress) if self.progress is not None else None,
            "domains": list(self.domains),
            "origin": str(self.origin),
            "bytes": int(self.bytes),
        }


FeedResult = Union[Idle, SnapshotChanged, ResyncRequired, LegacyMode, DeltaBatch]


# ---------------------------------------------------------------------------
# file helpers
# ---------------------------------------------------------------------------
def _signature(path: Optional[Path]) -> Optional[Tuple[int, int]]:
    if path is None:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _journal_signature(path: Path) -> Optional[Tuple[int, int, int, int]]:
    """``(dev, inode, mtime_ns, size)``.

    The identity is part of the signature because compaction *replaces* the
    journal file (``state_journal.rotate`` renames + unlinks).  A new file can
    have the same size and even a close timestamp, but never the same inode --
    and a rotated journal must be re-read from offset 0, otherwise the byte
    cursor would point past the new file's end and the appends would sit
    unread.
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


def _read_suffix(path: Path, offset: int, after_seq: int,
                 end_offset: int) -> Tuple[List[Tuple[Dict[str, Any], int]], int]:
    """Read complete records after ``offset``, returning ``(records, next_offset)``.

    Mirrors ``state_journal.read_mutations_from``: a partial tail or a malformed
    line leaves the offset before that line, so a later read cannot skip it.
    Each record is paired with its raw byte length (the delta's byte budget).
    """
    records: List[Tuple[Dict[str, Any], int]] = []
    position = offset
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            end = os.fstat(stream.fileno()).st_size if end_offset is None else end_offset
            while position < end:
                line = stream.readline(end - position)
                if not line.endswith(b"\n"):
                    break
                if not line.strip():
                    position = stream.tell()
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    break
                if not isinstance(record, dict):
                    break
                seq = record.get("seq")
                if not isinstance(seq, int) or isinstance(seq, bool):
                    break
                if seq > after_seq:
                    records.append((record, len(line)))
                position = stream.tell()
    except OSError:
        pass
    return records, position


def _tail_kind(path: Path, offset: int, end: int) -> str:
    """``eof`` / ``partial`` (writer mid-line) / ``complete`` (unusable line)."""
    if offset >= end:
        return "eof"
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            remaining = stream.read(end - offset)
    except OSError:
        return "eof"
    if not remaining:
        return "eof"
    return "complete" if b"\n" in remaining else "partial"


def _read_progress(path: Optional[Path]) -> Optional[Mapping[str, Any]]:
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    progress = {key: raw[key] for key in _PROGRESS_KEYS if key in raw}
    return progress or None


def _changed_fields(previous: Optional[Mapping[str, Any]],
                    current: Mapping[str, Any]) -> Tuple[str, ...]:
    if previous is None:
        return tuple(BASELINE_FIELDS)
    before = pair_signature(previous)
    after = pair_signature(current)
    return tuple(name for name in BASELINE_FIELDS
                 if before.get(name, "") != after.get(name, ""))


def _domains_for(record: Mapping[str, Any]) -> Tuple[str, ...]:
    domains: set[str] = set(_ALWAYS_DOMAINS)
    set_values = record.get("set")
    if isinstance(set_values, dict):
        for key in set_values:
            domains.update(_SET_DOMAINS.get(key, ()))
    extend = record.get("extend")
    if isinstance(extend, dict):
        for key in extend:
            domains.update(_EXTEND_DOMAINS.get(key, ()))
    if record.get("patch"):
        domains.update(_PATCH_DOMAINS)
    if record.get("candidates"):
        domains.update(_CANDIDATE_DOMAINS)
    return tuple(sorted(domains))


# ---------------------------------------------------------------------------
# the feed
# ---------------------------------------------------------------------------
class StateFeed:
    """Incremental reader of one task's ``state_journal.jsonl`` suffix."""

    def __init__(self, journal_path: Union[str, Path],
                 state_path: Optional[Union[str, Path]] = None, *,
                 job_id: str = "",
                 runtime_path: Optional[Union[str, Path]] = None,
                 max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                 max_records_per_poll: int = DEFAULT_MAX_RECORDS_PER_POLL,
                 max_backlog: int = DEFAULT_MAX_BACKLOG,
                 environ: Optional[Mapping[str, str]] = None) -> None:
        self.journal_path = Path(journal_path)
        self.state_path = Path(state_path) if state_path is not None else None
        self.runtime_path = Path(runtime_path) if runtime_path is not None else None
        self.job_id = job_id
        self.max_record_bytes = int(max_record_bytes)
        self.max_records_per_poll = int(max_records_per_poll)
        self.max_backlog = int(max_backlog)
        self.enabled = delta_enabled(environ)
        self._seq = 0
        self._offset = 0
        self._journal_sig: Optional[Tuple[int, int]] = None
        self._snapshot_sig: Optional[Tuple[int, int]] = None
        self._legacy_reason = ""

    # -- lifecycle --------------------------------------------------------
    @property
    def mode(self) -> str:
        return "legacy" if (not self.enabled or self._legacy_reason) else "delta"

    @property
    def cursor(self) -> FeedCursor:
        return FeedCursor(seq=self._seq, offset=self._offset,
                          snapshot=self._snapshot_sig)

    def start(self, cursor_seq: int = 0) -> None:
        """Begin from the snapshot the caller just loaded.

        ``cursor_seq`` is ``state["journal_cursor"]``: every record up to it is
        already inside the loaded state.  The byte offset starts at 0 on
        purpose -- records appended between ``load_state`` and ``start`` must be
        delivered, so the first poll re-scans the file once (a few MB at most).
        """
        self._seq = max(0, int(cursor_seq))
        self._offset = 0
        self._journal_sig = None
        self._snapshot_sig = _signature(self.state_path)
        self._legacy_reason = ""

    def reset(self, cursor_seq: int) -> None:
        """Resume after a whole-state reload (re-baselines the snapshot)."""
        self.start(cursor_seq)

    def disable(self, reason: str = "permanent_fallback") -> None:
        """Stop using the feed for this job (sticky until a new ``start``)."""
        self._legacy_reason = str(reason or "permanent_fallback")

    # -- polling ----------------------------------------------------------
    def poll(self, *, previous_pair: Optional[Callable[[int], Optional[Mapping[str, Any]]]] = None,
             worker_active: bool = False) -> FeedResult:
        if not self.enabled:
            return LegacyMode()
        if self._legacy_reason:
            return LegacyMode(self._legacy_reason)

        snapshot_sig = _signature(self.state_path)
        if snapshot_sig != self._snapshot_sig:
            self._snapshot_sig = snapshot_sig
            return SnapshotChanged(worker_active=bool(worker_active))

        journal_sig = _journal_signature(self.journal_path)
        if journal_sig is None:
            return Idle()
        size = journal_sig[3]
        if self._journal_sig is not None \
                and (journal_sig[0], journal_sig[1]) != (self._journal_sig[0], self._journal_sig[1]):
            # Compaction replaced the journal file.  Sequence numbers continue
            # from the snapshot cursor, so the byte cursor is the only thing to
            # reset; the seq check below decides whether anything was lost.
            self._offset = 0
        if size < self._offset:
            self._offset = 0
        if size == self._offset and journal_sig != self._journal_sig:
            # Same length, different content/timestamp: an in-place rewrite (a
            # truncation that refilled the file to the old byte count).  The
            # byte cursor cannot prove what is behind it any more, so re-read
            # from the start once and let the sequence check decide.
            self._offset = 0
        if self._offset == size and journal_sig == self._journal_sig:
            return Idle()

        records, next_offset = _read_suffix(self.journal_path, self._offset,
                                            self._seq, size)
        if not records:
            if next_offset != self._offset:
                # Lines that are already in the loaded state; remember how far
                # the clean prefix reaches so the next poll starts after them.
                self._offset = next_offset
                self._journal_sig = journal_sig
                return Idle()
            if self._offset < size:
                kind = _tail_kind(self.journal_path, self._offset, size)
                self._journal_sig = journal_sig
                if kind == "partial":
                    return Idle("partial_tail")
                return ResyncRequired(REASON_CORRUPT_RECORD,
                                      f"unusable line at offset {self._offset}")
            self._journal_sig = journal_sig
            return Idle()

        expected = self._seq + 1
        for record, raw_size in records:
            if record.get("seq") != expected:
                return ResyncRequired(
                    REASON_SEQ_GAP,
                    f"expected seq {expected}, found {record.get('seq')!r}")
            if not all(key in record for key in ("schema", "mutation", "offset", "pairs")):
                return ResyncRequired(REASON_CORRUPT_RECORD,
                                      f"record {expected} is missing required fields")
            if record.get("schema") not in READABLE_JOURNAL_SCHEMAS:
                return ResyncRequired(REASON_UNKNOWN_SCHEMA,
                                      f"record {expected} schema {record.get('schema')!r}",
                                      permanent=True)
            if record.get("mutation") != MUTATION_TRANSLATION_BATCH:
                return ResyncRequired(REASON_UNKNOWN_MUTATION,
                                      f"record {expected} mutation {record.get('mutation')!r}",
                                      permanent=True)
            if raw_size > self.max_record_bytes:
                return ResyncRequired(
                    REASON_RECORD_TOO_LARGE,
                    f"record {expected} is {raw_size} bytes "
                    f"(limit {self.max_record_bytes})")
            expected += 1

        last_seq = int(records[-1][0]["seq"])
        if len(records) > self.max_records_per_poll:
            return ResyncRequired(REASON_BACKLOG,
                                  f"{len(records)} records pending "
                                  f"(limit {self.max_records_per_poll})")
        if last_seq - self._seq > self.max_backlog:
            return ResyncRequired(REASON_BACKLOG,
                                  f"{last_seq - self._seq} records pending "
                                  f"(limit {self.max_backlog})")

        delta = self._build_delta(records, previous_pair=previous_pair)
        self._seq = last_seq
        self._offset = next_offset
        self._journal_sig = journal_sig
        return delta

    # -- building ---------------------------------------------------------
    def _build_delta(
        self,
        records: Sequence[Tuple[Dict[str, Any], int]],
        *,
        previous_pair: Optional[Callable[[int], Optional[Mapping[str, Any]]]] = None,
    ) -> DeltaBatch:
        segments: List[SegmentChange] = []
        counters: Dict[str, Any] = {}
        delivery: Dict[str, Any] = {}
        findings: Dict[str, int] = {}
        domains: set[str] = set(_ALWAYS_DOMAINS)
        batches: List[int] = []
        total_bytes = 0
        offset = 0

        for record, raw_size in records:
            total_bytes += raw_size
            try:
                batches.append(int(record.get("batch") or 0))
            except (TypeError, ValueError):
                batches.append(0)
            record_offset = record.get("offset")
            if not isinstance(record_offset, int) or isinstance(record_offset, bool) \
                    or record_offset < 0:
                record_offset = 0
            offset = record_offset
            pairs = record.get("pairs")
            for position, pair in enumerate(pairs if isinstance(pairs, list) else []):
                if not isinstance(pair, dict):
                    continue
                index = record_offset + position
                previous = previous_pair(index) if previous_pair is not None else None
                segments.append(SegmentChange(
                    index=index,
                    segment_uid=str(pair.get("segment_uid") or ""),
                    pair=pair,
                    fields=_changed_fields(previous, pair)))

            set_values = record.get("set")
            if isinstance(set_values, dict):
                for key in COUNTER_KEYS:
                    if key in set_values:
                        counters[key] = set_values[key]
                for key in DELIVERY_KEYS:
                    if key in set_values:
                        delivery[key] = set_values[key]
            extend = record.get("extend")
            if isinstance(extend, dict):
                for key in ("findings", "review_evidence", "repair_overlays"):
                    entry = extend.get(key)
                    if isinstance(entry, dict):
                        items = entry.get("items")
                        findings[key] = len(items) if isinstance(items, list) else 0
            patch = record.get("patch")
            if isinstance(patch, dict) and patch:
                findings["patches"] = len(patch)
            domains.update(_domains_for(record))

        progress = _read_progress(self.runtime_path)
        return DeltaBatch(
            schema=DELTA_SCHEMA_VERSION,
            job_id=self.job_id,
            from_seq=self._seq + 1,
            seq=int(records[-1][0]["seq"]),
            batch=batches[-1] if batches else 0,
            batches=tuple(batches),
            offset=offset,
            segments=tuple(segments),
            counters=counters,
            delivery=delivery,
            findings_delta=findings,
            progress=progress,
            domains=tuple(sorted(domains)),
            origin=ORIGIN_PIPELINE,
            bytes=total_bytes,
        )


__all__ = [
    "COUNTER_KEYS", "DEFAULT_MAX_BACKLOG", "DEFAULT_MAX_RECORD_BYTES",
    "DEFAULT_MAX_RECORDS_PER_POLL", "DELIVERY_KEYS", "DELTA_ENV",
    "DELTA_SCHEMA_VERSION", "DeltaBatch", "FeedCursor", "FeedResult", "Idle",
    "LegacyMode", "MUTATION_TRANSLATION_BATCH", "ORIGIN_PIPELINE",
    "READABLE_JOURNAL_SCHEMAS", "REASON_BACKLOG", "REASON_CORRUPT_RECORD",
    "REASON_RECORD_TOO_LARGE", "REASON_SEQ_GAP", "REASON_UNKNOWN_MUTATION",
    "REASON_UNKNOWN_SCHEMA", "ResyncRequired", "SegmentChange",
    "SnapshotChanged", "StateFeed", "delta_enabled",
]
