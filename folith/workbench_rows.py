"""The workbench read model: segment rows and their incremental updates.

Why this module exists
----------------------
During a translation run the pipeline publishes one committed batch at a time
(see :mod:`folith.state_feed`, which projects the durable
``state_journal.jsonl`` records).  The workbench used to answer every batch by
re-reading the whole task state and rebuilding every row, every derived
projection and the whole grid -- so the per-batch cost grew with the book
(issue #324).  This module is the read-side half of the fix: a row list that
can be updated one segment at a time, plus an incremental whole-document word
count (:class:`folith.word_count.DocumentCountIndex`).

Authority model
---------------
Rows and counters here are a *projection* of the authoritative ``pairs`` -- they
are derived data, never a second source of truth.  Nothing in this module
writes a task state, a draft or a journal record; deleting the model only costs
a rebuild from :func:`rows_from_state`.  Any doubt about identity, ordering or
freshness raises :class:`NeedResync`; the caller then reloads the authoritative
state and calls :meth:`WorkbenchReadModel.reset_from`.

Qt-free on purpose: the desktop client and the tests both use it, and the
status vocabulary (:data:`STATUS_*`) is shared with the UI instead of being
duplicated.  ``folith/qt/adapter.py`` still owns the localized presentation of
these codes (``_pair_status``); the two are intentionally the same vocabulary.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Mapping

from folith import word_count as _word_count

#: Status codes for one segment.  The UI maps these to localized labels; the
#: ordering matches the existing ``adapter._pair_status`` precedence.
STATUS_EXCLUDED = "excluded"
STATUS_HUMAN_EDITED = "human_edited"
STATUS_REVIEWED = "reviewed"
STATUS_PENDING_REVIEW = "pending_review"
STATUS_UNTRANSLATED = "untranslated"

STATUS_CODES = (STATUS_EXCLUDED, STATUS_HUMAN_EDITED, STATUS_REVIEWED,
                STATUS_PENDING_REVIEW, STATUS_UNTRANSLATED)

#: Every projection domain the view caches independently (issue #324 §3.5).
#: A reset bumps all of them; a delta bumps only the ones it touched.
DOMAINS = ("rows", "counts", "stage", "runtime", "review", "delivery",
           "followup", "terms", "academic", "details", "activity", "excluded")


class NeedResync(RuntimeError):
    """The delta no longer matches the rows this model holds.

    Raised for a uid mismatch (structural edit / reorder), an offset past the
    end of the book, or a stale model.  The caller's only safe response is to
    reload the authoritative state and rebuild; never to guess.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def pair_status_code(pair: Mapping[str, Any] | None) -> str:
    """The status code of one pair, with the existing UI precedence."""
    pair = pair or {}
    if pair.get("excluded"):
        return STATUS_EXCLUDED
    if pair.get("human_edited"):
        return STATUS_HUMAN_EDITED
    if pair.get("reviewed"):
        return STATUS_REVIEWED
    if str(pair.get("target") or "").strip():
        return STATUS_PENDING_REVIEW
    return STATUS_UNTRANSLATED


@dataclass(frozen=True)
class WorkbenchRow:
    """One segment as the workbench renders it.

    Field-compatible with ``folith.qt.adapter.SegmentRow`` on purpose: the P2
    step switches the view over to this class (``adapter`` will re-export it)
    without changing any widget code.
    """

    index: int
    segment_uid: str
    source: str
    target: str
    status: str
    reviewed: bool
    human_edited: bool
    excluded: bool
    has_draft: bool = False
    draft_text: str = ""
    #: The draft was written against an older target (``baseline`` differs):
    #: it is still the user's unsaved work, but submitting it would replace
    #: fresher text, so the UI must say so.
    stale_draft: bool = False

    @property
    def source_preview(self) -> str:
        text = " ".join((self.source or "").split())
        return text[:80] + ("…" if len(text) > 80 else "")

    @property
    def target_preview(self) -> str:
        text = " ".join((self.target or "").split())
        return text[:80] + ("…" if len(text) > 80 else "")

    def with_pending_save(self, text: str | None) -> "WorkbenchRow":
        """The row as the grid must show it while a just-saved edit is pending."""
        if text is None:
            return self
        return dataclasses.replace(
            self, target=str(text), human_edited=True, has_draft=False,
            draft_text="", stale_draft=False,
            status=self.status if self.excluded else STATUS_HUMAN_EDITED)


def _para_text(item: Any) -> str:
    """Source text of one ``paras`` entry (plain string or ``{"text": …}``)."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("text") or item.get("source") or "")
    return ""


def _draft_for(index: int, uid: str, drafts: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not drafts:
        return None
    draft = drafts.get(uid) if uid else None
    if draft is None:
        draft = drafts.get(str(index))
    return draft if isinstance(draft, dict) else None


def row_from_pair(index: int, pair: Mapping[str, Any],
                  draft: Mapping[str, Any] | None = None) -> WorkbenchRow:
    uid = str(pair.get("segment_uid") or "") or str(index)
    target = str(pair.get("target") or "")
    baseline = str((draft or {}).get("baseline") or "")
    return WorkbenchRow(
        index=index,
        segment_uid=uid,
        source=str(pair.get("source") or ""),
        target=target,
        status=pair_status_code(pair),
        reviewed=bool(pair.get("reviewed")),
        human_edited=bool(pair.get("human_edited")),
        excluded=bool(pair.get("excluded")),
        has_draft=bool(draft),
        draft_text=str((draft or {}).get("text") or ""),
        stale_draft=bool(draft) and bool(baseline) and baseline != target,
    )


def row_from_paragraph(index: int, item: Any,
                       draft: Mapping[str, Any] | None = None) -> WorkbenchRow:
    uid = str(index)
    return WorkbenchRow(
        index=index,
        segment_uid=uid,
        source=_para_text(item),
        target="",
        status=STATUS_UNTRANSLATED,
        reviewed=False,
        human_edited=False,
        excluded=False,
        has_draft=bool(draft),
        draft_text=str((draft or {}).get("text") or ""),
    )


def rows_from_state(state: Mapping[str, Any],
                    drafts: Mapping[str, Any] | None = None) -> list[WorkbenchRow]:
    """Rebuild every row from the authoritative state (open / resync path)."""
    pairs = state.get("pairs") or []
    rows: list[WorkbenchRow] = []
    if pairs:
        for index, pair in enumerate(pairs):
            if not isinstance(pair, dict):
                continue
            uid = str(pair.get("segment_uid") or "") or str(index)
            rows.append(row_from_pair(index, pair, _draft_for(index, uid, drafts)))
        return rows
    # Before translation the workbench shows the source segments, so the user
    # can read (and prepare) the document while the worker runs.
    for index, item in enumerate(state.get("paras") or []):
        rows.append(row_from_paragraph(index, item, _draft_for(index, str(index), drafts)))
    return rows


@dataclass(frozen=True)
class AppliedDelta:
    """What one delta actually changed on the read side."""

    positions: tuple[int, ...] = ()
    rows: tuple[WorkbenchRow, ...] = ()
    appended: int = 0
    counts_changed: bool = False
    domains: tuple[str, ...] = ()
    counters: Mapping[str, Any] = field(default_factory=dict)


class WorkbenchReadModel:
    """Rows + counters kept current from :class:`folith.state_feed.DeltaBatch`.

    The model is owned by one thread (the GUI thread in the desktop client) and
    is pure data; the slow whole-document projections still come from a full
    ``load_state`` on a worker at their own cadence.
    """

    def __init__(self) -> None:
        self.rows: list[WorkbenchRow] = []
        self.counts = _word_count.DocumentCountIndex()
        self.counters: dict[str, Any] = {}
        self.domains: dict[str, int] = {name: 0 for name in DOMAINS}
        self._pairs: list[Mapping[str, Any]] = []
        self._drafts: Mapping[str, Any] | None = None

    # -- rebuilding -------------------------------------------------------
    def reset_from(self, state: Mapping[str, Any],
                   drafts: Mapping[str, Any] | None = None) -> None:
        """Rebuild from the authoritative state.  Bumps every domain."""
        self._pairs = [pair if isinstance(pair, dict) else {}
                       for pair in (state.get("pairs") or [])]
        self._drafts = drafts
        self.rows = rows_from_state(state, drafts)
        self.counts = _word_count.DocumentCountIndex(self.rows)
        self.counters = {}
        for name in DOMAINS:
            self.domains[name] += 1

    def previous_pair(self, index: int) -> Mapping[str, Any] | None:
        """The pair this model held at ``index`` (used to diff delta fields)."""
        if 0 <= index < len(self._pairs):
            return self._pairs[index]
        return None

    def row_at(self, position: int) -> WorkbenchRow | None:
        return self.rows[position] if 0 <= position < len(self.rows) else None

    # -- applying ---------------------------------------------------------
    def apply(self, delta: Any,
              drafts: Mapping[str, Any] | None = None) -> AppliedDelta:
        """Apply one typed delta; returns only the rows that actually changed."""
        if drafts is not None and drafts is not self._drafts:
            self._drafts = drafts
        positions: list[int] = []
        changed_rows: list[WorkbenchRow] = []
        appended = 0
        counts_changed = False

        for segment in delta.segments:
            index = int(segment.index)
            pair = dict(segment.pair)
            draft = _draft_for(index, str(pair.get("segment_uid") or ""), self._drafts)
            if index < len(self.rows):
                existing = self.rows[index]
                uid = str(getattr(segment, "segment_uid", "") or "")
                if uid and existing.segment_uid and uid != existing.segment_uid:
                    raise NeedResync("uid_mismatch",
                                     f"index {index}: model has {existing.segment_uid}, delta has {uid}")
                row = row_from_pair(index, pair, draft)
                # The rows may have been built from ``paras`` (a job whose first
                # translation batch is arriving now) while ``_pairs`` is still
                # empty; the journal record is the first absolute pair value, so
                # the parallel list must grow with it.
                if index >= len(self._pairs):
                    self._pairs.extend([{}] * (index + 1 - len(self._pairs)))
                if row == existing:
                    self._pairs[index] = pair
                    continue
                self.rows[index] = row
                self._pairs[index] = pair
                self.counts.update(index, row)
                positions.append(index)
                changed_rows.append(row)
                counts_changed = True
            elif index == len(self.rows):
                row = row_from_pair(index, pair, draft)
                self.rows.append(row)
                self._pairs.append(pair)
                self.counts.append(row)
                positions.append(index)
                changed_rows.append(row)
                counts_changed = True
                appended += 1
            else:
                raise NeedResync("offset_gap",
                                 f"delta starts at {index}, model holds {len(self.rows)} rows")

        domains = tuple(delta.domains or ())
        for name in domains:
            if name in self.domains:
                self.domains[name] += 1
        if hasattr(delta, "counters") and delta.counters:
            self.counters.update(dict(delta.counters))

        return AppliedDelta(positions=tuple(positions), rows=tuple(changed_rows),
                            appended=appended, counts_changed=counts_changed,
                            domains=domains, counters=dict(delta.counters or {}))


__all__ = [
    "AppliedDelta", "DOMAINS", "NeedResync", "STATUS_CODES",
    "STATUS_EXCLUDED", "STATUS_HUMAN_EDITED", "STATUS_PENDING_REVIEW",
    "STATUS_REVIEWED", "STATUS_UNTRANSLATED", "WorkbenchReadModel",
    "WorkbenchRow", "pair_status_code", "row_from_pair", "row_from_paragraph",
    "rows_from_state",
]
