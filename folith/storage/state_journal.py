"""Append-only mutation journal for the translation hot path.

Why this file exists
--------------------
``state.json`` is the single authoritative complete task snapshot.  Before this
module, durability during translation came from a *bounded snapshot cadence*: a
full snapshot every N batches (capped at a fraction of the document).  That
bounds the crash window, but it also means the number of full snapshots grows
with the document while each one rewrites the whole state -- so cumulative
write volume grows with the square of the document length.  Measured at
production caliber, amplification was ``~0.0166 * segments`` (84x at 5000,
332x at 20000).

The journal decouples those two concerns.  A committed batch is appended to
``state_journal.jsonl`` as a *strong-typed mutation*; the snapshot no longer has
to be taken often, because a crash is repaired by *replaying* the journal
instead of *recomputing* batches.  Replay is pure I/O and never calls a model,
so the crash window costs zero model work.  That is what allows the snapshot
count to stop scaling with the segment count.

Authority model (deliberately unchanged)
----------------------------------------
* ``state.json`` -- authoritative complete snapshot.  The only authority.
* ``state_journal.jsonl`` -- this module.  A *durability record*, not an
  authority.  It can only ever reproduce content the snapshot would have
  carried.  Deleting it can never change the content of a state; it can only
  make a crash more expensive.
* ``events.jsonl`` -- workflow / audit / recovery evidence.  Never reused as
  the authoritative journal.
* ``runtime_events.jsonl`` -- runtime telemetry.

Mutation typing
---------------
Only a fixed set of named mutations is allowed.  Arbitrary path patches and a
generic JSON-diff engine are forbidden by design: a diff engine cannot tell
"the pipeline advanced" from "a human corrected the text", and the difference
is exactly what recovery has to preserve.

The one mutation implemented here is ``translation_batch_committed``.  Its
record is a fixed, named shape -- not a diff:

``pairs``
    The batch's committed translation pairs, addressed by *absolute* segment
    offset, so applying the same record twice writes the same values.
``set``
    Named top-level keys whose whole value changed (replaced or mutated in
    place inside the batch).  Recorded only when the value actually changed.
``extend``
    Named append-only lists: the items this batch appended, together with the
    list length they were appended to.  The length is checked on replay, so
    applying a record twice fails loudly instead of duplicating items.
``patch``
    In-place updates to items that earlier records already carried, keyed by
    the item's own identity (``review_event_id`` / ``finding_id`` /
    ``human_actions`` index / ``case_id``).  Produced by the mutator itself,
    never by comparing two states.

Idempotency
-----------
Recovery is a pure function of ``(snapshot, journal)``: it never writes the
snapshot, and it only ever applies records whose ``seq`` is greater than the
snapshot's recorded ``journal_cursor``.  Applying the same record twice
therefore cannot happen within a recovery, and repeating a recovery from the
same snapshot reproduces the same state exactly.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from folith import critical_path as _critical_path

JOURNAL_SCHEMA_VERSION = 1

JOURNAL_FILE = "state_journal.jsonl"

#: The only mutation type this module knows.  Adding one means adding an
#: ``_apply_<name>`` function and listing it in ``_APPLIERS``.
MUTATION_TRANSLATION_BATCH = "translation_batch_committed"

#: ``state["journal_cursor"]`` is the highest ``seq`` already contained in the
#: snapshot that was loaded.  It is the *only* thing that decides what a replay
#: applies, which is what makes replay repeatable.
CURSOR_KEY = "journal_cursor"

#: Compact, unsorted: the record's own key order is deterministic, and sorting
#: a multi-kilobyte record on every batch is pure encoder cost.
_COMPACT = {"ensure_ascii": False, "separators": (",", ":")}

# Tail scanning constants.  ``max_seq`` must stay cheap because it runs on every
# state load, including UI reads that will find nothing to replay.
_TAIL_SCAN_BYTES = 256 * 1024
_TAIL_SCAN_LINES = 64

_DIR_READY: set = set()
_DIR_LOCK = threading.Lock()


class JournalGap(RuntimeError):
    """The journal no longer lines up with the snapshot it would be applied to.

    Raised when a record addresses a segment offset past the end of the
    snapshot's ``pairs``, or when a record's schema / mutation type is unknown.
    The caller's only safe response is to stop replaying and let the pipeline
    recompute from wherever the state actually got to -- never to guess.
    """


def _output_dir(output_dir: Optional[Path] = None) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    import core
    return Path(core.OUTPUT_DIR)


def journal_path(job_id: str, output_dir: Optional[Path] = None) -> Path:
    return _output_dir(output_dir) / str(job_id) / JOURNAL_FILE


def reset_dir_cache() -> None:
    """Forget the per-process "directory exists" hints (tests, tmpdir moves)."""
    with _DIR_LOCK:
        _DIR_READY.clear()


def _ensure_dir(path: Path) -> None:
    parent = str(path.parent)
    with _DIR_LOCK:
        if parent in _DIR_READY:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _DIR_LOCK:
        _DIR_READY.add(parent)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------
def append_mutation(job_id: str, record: Dict[str, Any],
                    output_dir: Optional[Path] = None) -> Tuple[int, int]:
    """Append one mutation record and make it durable before returning.

    Returns ``(seq, bytes_written)``.  The byte count is returned rather than
    re-``stat``-ed by the caller so the byte-budget snapshot trigger costs no
    extra syscall on the hot path.

    ``record["seq"]`` must already be set by the caller: sequence numbers are
    assigned from the snapshot's cursor so they stay monotonic across the
    rotations that happen after every snapshot.

    The write is a single ``write`` of one complete line followed by
    ``fsync``.  A crash can therefore leave a *truncated last line* but never a
    torn earlier one -- which is the only failure mode the reader has to
    tolerate.
    """
    seq = record.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise ValueError("journal record needs an integer seq")
    path = journal_path(job_id, output_dir=output_dir)
    with _critical_path.phase("journal_serialize") as _ser_sink:
        payload = (json.dumps(record, **_COMPACT) + "\n").encode("utf-8")
        _ser_sink.written(len(payload))
    _ensure_dir(path)

    def _write_then_sync() -> None:
        # Append and fsync are timed as *siblings*, not nested: the whole point
        # of splitting them is to see how much of the journal cost is the disk
        # barrier rather than the bytes.  Open/close stays inside the append
        # phase, and the ordering (write -> flush -> fsync -> close) is exactly
        # what the single ``with`` block below used to do.
        stream = None
        with _critical_path.phase("journal_append") as _app_sink:
            stream = path.open("ab")
            stream.write(payload)
            stream.flush()
            _app_sink.written(len(payload))
        try:
            with _critical_path.phase("journal_fsync"):
                os.fsync(stream.fileno())
        finally:
            stream.close()

    try:
        _write_then_sync()
    except OSError:
        with _DIR_LOCK:
            _DIR_READY.discard(str(path.parent))
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_then_sync()
    return seq, len(payload)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
def size(job_id: str, output_dir: Optional[Path] = None) -> int:
    """Journal size in bytes, or 0 when there is no journal."""
    try:
        return journal_path(job_id, output_dir=output_dir).stat().st_size
    except OSError:
        return 0


def _iter_lines(path: Path):
    """Yield complete lines; a trailing partial line is silently dropped."""
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if not raw:
        return
    chunks = raw.split(b"\n")
    # ``split`` leaves the text after the final newline as the last element.  A
    # non-empty final element means the writer was interrupted mid-line.
    for chunk in chunks[:-1]:
        if not chunk.strip():
            continue
        try:
            yield json.loads(chunk.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # A malformed line makes everything after it untrustworthy.  Stop
            # rather than skip: the caller recomputes from where the state got
            # to, which is always safe, whereas skipping could apply a mutation
            # out of order.
            return


def read_mutations(job_id: str, after_seq: int = 0,
                   output_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return every readable record with ``seq`` greater than ``after_seq``."""
    out: List[Dict[str, Any]] = []
    for record in _iter_lines(journal_path(job_id, output_dir=output_dir)):
        if not isinstance(record, dict):
            break
        seq = record.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            break
        if seq > after_seq:
            out.append(record)
    return out


def max_seq(job_id: str, output_dir: Optional[Path] = None) -> int:
    """Highest readable ``seq``, scanning backwards so a load stays cheap.

    Scans at most ``_TAIL_SCAN_LINES`` lines from the end.  A journal longer
    than that tail belongs to a job whose snapshot is behind by more than the
    scan window, in which case the returned value is a *lower bound* -- and
    that is safe, because the only consumer is the rotation guard, which must
    err on the side of keeping the journal.
    """
    path = journal_path(job_id, output_dir=output_dir)
    try:
        file_size = path.stat().st_size
    except OSError:
        return 0
    if file_size == 0:
        return 0
    start = max(0, file_size - _TAIL_SCAN_BYTES)
    try:
        with path.open("rb") as stream:
            stream.seek(start)
            tail = stream.read()
    except OSError:
        return 0
    chunks = tail.split(b"\n")
    if start > 0:
        chunks = chunks[1:]           # the first chunk may be a partial line
    if tail.endswith(b"\n"):
        chunks = chunks[:-1]          # the element after the final newline
    seen = 0
    for chunk in reversed(chunks):
        if not chunk.strip():
            continue
        seen += 1
        if seen > _TAIL_SCAN_LINES:
            break
        try:
            record = json.loads(chunk.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        seq = record.get("seq") if isinstance(record, dict) else None
        if isinstance(seq, int) and not isinstance(seq, bool):
            return seq
    return 0


# ---------------------------------------------------------------------------
# cursor
# ---------------------------------------------------------------------------
def cursor(state: Dict[str, Any]) -> int:
    value = state.get(CURSOR_KEY) if isinstance(state, dict) else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def set_cursor(state: Dict[str, Any], seq: int) -> None:
    state[CURSOR_KEY] = int(seq)


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------
def _apply_translation_batch(state: Dict[str, Any], record: Dict[str, Any]) -> None:
    """Apply one committed batch, writing absolute values only.

    Every write below is "put this value at this place", never "add one more".
    That is what makes a repeated application equivalent to a single one.
    """
    offset = record.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise JournalGap("record has no usable offset")
    pairs = state.get("pairs")
    if not isinstance(pairs, list):
        pairs = state["pairs"] = []
    if offset > len(pairs):
        raise JournalGap(
            f"journal addresses offset {offset} but the snapshot holds "
            f"{len(pairs)} pairs")
    for index, pair in enumerate(record.get("pairs") or []):
        position = offset + index
        if position < len(pairs):
            pairs[position] = pair
        else:
            pairs.append(pair)

    for key, value in (record.get("set") or {}).items():
        state[key] = value

    for key, entry in (record.get("extend") or {}).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise JournalGap(f"extend.{key} is not an append entry")
        target = state.get(key)
        if not isinstance(target, list):
            target = state[key] = []
        # The recorded base length is what makes a double application *detectable*
        # instead of silently duplicating items.  A mismatch means this record is
        # not being applied to the state it was written against, so the honest
        # answer is to stop and let the pipeline recompute.
        base = entry.get("base")
        if not isinstance(base, int) or isinstance(base, bool) or len(target) != base:
            raise JournalGap(
                f"extend.{key} expects base {base!r}, found {len(target)}")
        target.extend(entry["items"])

    _apply_patches(state, record.get("patch") or {})


def _apply_patches(state: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for entry in patch.get("review_events") or []:
        event = _find_by_key(state.get("review_evidence"), "review_event_id",
                             entry.get("review_event_id"))
        if event is not None:
            event.update(entry.get("fields") or {})
    for entry in patch.get("findings") or []:
        finding = _find_by_key(state.get("findings"), "finding_id",
                               entry.get("finding_id"))
        if finding is not None:
            finding.update(entry.get("fields") or {})
    decisions = state.get("human_actions")
    if isinstance(decisions, list):
        for entry in patch.get("decisions") or []:
            index = entry.get("index")
            if isinstance(index, int) and not isinstance(index, bool) \
                    and 0 <= index < len(decisions) \
                    and isinstance(decisions[index], dict):
                decisions[index].update(entry.get("fields") or {})
    reviews = state.get("case_reviews")
    if isinstance(reviews, dict):
        for entry in patch.get("case_reviews") or []:
            case_id = str(entry.get("case_id") or "")
            record = reviews.get(case_id)
            if isinstance(record, dict):
                record.update(entry.get("fields") or {})


def _find_by_key(items: Any, key: str, value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(items, list) or not value:
        return None
    wanted = str(value)
    for item in reversed(items):
        if isinstance(item, dict) and str(item.get(key) or "") == wanted:
            return item
    return None


_APPLIERS = {
    MUTATION_TRANSLATION_BATCH: _apply_translation_batch,
}


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------
def replay(job_id: str, state: Dict[str, Any],
           output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Apply every journal record the snapshot does not already contain.

    Returns a small report so callers and tests can see what happened without
    parsing the journal themselves::

        {"applied": int, "cursor": int, "gap": bool, "dropped": int}

    ``gap`` means a record could not be applied (schema drift, unknown mutation,
    or an offset past the end of the snapshot's pairs).  Replay stops there and
    leaves the state exactly as far as it legitimately got; the pipeline then
    recomputes from that point.  A gap is always safe -- it costs work, never
    correctness -- so it is reported rather than raised.

    This function must never call a model and must never write the snapshot.
    """
    start = cursor(state)
    if not isinstance(state, dict):
        return {"applied": 0, "cursor": start, "gap": False, "dropped": 0}
    if max_seq(job_id, output_dir=output_dir) <= start:
        return {"applied": 0, "cursor": start, "gap": False, "dropped": 0}

    records = read_mutations(job_id, after_seq=start, output_dir=output_dir)
    applied = 0
    current = start
    gap = False
    for record in records:
        if record.get("schema") != JOURNAL_SCHEMA_VERSION:
            gap = True
            break
        applier = _APPLIERS.get(record.get("mutation"))
        if applier is None:
            gap = True
            break
        try:
            applier(state, record)
        except JournalGap:
            gap = True
            break
        current = int(record["seq"])
        applied += 1
    set_cursor(state, current)
    return {
        "applied": applied,
        "cursor": current,
        "gap": gap,
        "dropped": len(records) - applied,
    }


# ---------------------------------------------------------------------------
# rotation (compaction)
# ---------------------------------------------------------------------------
def rotate(job_id: str, output_dir: Optional[Path] = None) -> bool:
    """Retire the journal after a snapshot that already contains all of it.

    Order matters and is fixed: the caller must have *already* replaced
    ``state.json`` atomically before calling this.  The journal is renamed out
    of the way first and only then unlinked, so a crash in between leaves a
    stray ``.applied`` file the reader ignores -- never a missing journal for a
    snapshot that does not yet contain its records.
    """
    path = journal_path(job_id, output_dir=output_dir)
    try:
        retired = path.with_suffix(".jsonl.applied")
        path.replace(retired)
    except OSError:
        return False
    try:
        retired.unlink()
    except OSError:
        pass
    return True


def rotate_if_applied(job_id: str, state: Dict[str, Any],
                      output_dir: Optional[Path] = None) -> bool:
    """Rotate only when the snapshot provably contains every journal record.

    Saving a state whose cursor is behind the journal is legitimate -- several
    save paths write the in-memory state without having replayed anything -- so
    the guard, not the caller, decides.  Erring towards keeping the journal is
    always safe; dropping a record a snapshot does not contain is not.
    """
    if cursor(state) < max_seq(job_id, output_dir=output_dir):
        return False
    return rotate(job_id, output_dir=output_dir)


def state_report(job_id: str, state: Optional[Dict[str, Any]] = None,
                 output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Small read-only description of the journal, for diagnostics and tests."""
    return {
        "bytes": size(job_id, output_dir=output_dir),
        "max_seq": max_seq(job_id, output_dir=output_dir),
        "cursor": cursor(state) if isinstance(state, dict) else 0,
    }
