# Optimisation plan — from measured bottleneck to minimal change

Each item states the **evidence** that motivated it, the **change**, the
**risk**, and how it is **verified**. The order is by measured impact, not by
how easy the change was.

Nothing here was implemented "because memoQ has it". Each item was derived
from a measurement in `baseline-report.md` / `architecture-audit.md`.

> **Revision note (2026-10-23).** Some paired figures below came from the
> first reconstruction, before C5/C6 were found missing. The authoritative
> fixed-baseline numbers are in `final-performance-report.md` Revision 2;
> diagnostic 40-second profiles are unaffected.

---

## D1. Merge the batch-progress status writes  *(root cause, 26.8 s of 40 s)*

**Evidence.** `core.update_runtime_state` was called 1 997 times in a 40-second
run and cost 26 764 ms — one durable read-modify-write of
`runtime_state.json` per *batch phase*, plus an fsync'd event append. The
pipeline produces seven status lines per batch ("building context",
"translating", "checking", "writing", "saved", two captions). None of them is
business truth: the translated pairs are committed by `_commit_batch` into
`state.json` + the journal, on a path that is already cooperative.

**Change.** A per-job merged status writer (`core._RuntimeStatusWriter`):

* status updates are merged and written at most once per
  `RUNTIME_STATUS_FLUSH_SECONDS` (0.4 s);
* every event still reaches `runtime_events.jsonl` — only the *state file* is
  coalesced, so the activity log keeps its history;
* the writer is closed (flushed and dropped) **before** the terminal state is
  written, so a late flush can never overwrite `completed` / `failed` /
  `cancelled`.

**What stays synchronous.** Cancellation, pause, the worker lease, the
heartbeat, the stage boundaries and every terminal state. Those are the
writes a crash recovery or a cancellation decision depends on, and they must
not sit behind a window. The merge is also cancelled-aware: the existing
"cancel_requested ⇒ status becomes cancelling" rule is applied by
`update_runtime_state` at flush time, so a pending status can never report
"running" after the user pressed cancel.

**Pattern reuse.** The codebase already had this exact pattern for in-flight
LLM telemetry (`folith/engine/runtime_telemetry.py`, "运行时遥测合并"); this
extends it to the batch-progress channel, which the earlier pass did not
cover.

**Risk.** Low. `update_runtime_state` keeps its contract; only three call
sites opt into the merged path. Verified by:
* `tests/runtime_state_test.py`, `tests/runtime_telemetry_timer_test.py`,
  `tests/llm_transport_test.py`, `tests/runtime_pause_test.py`,
  `tests/runtime_status_test.py` — all pass unchanged;
* a new regression test (§Verification).

---

## D2. Do not compute whole-document projections on the GUI thread  *(2 803 ms of GUI time per 40 s, up to 189 ms in one call)*

**Evidence.** `WorkbenchView.apply` re-projected the whole document on every
poll: the delivery gate (a deterministic QA sweep over every pair — 170 ms),
the follow-up facts (which re-read the whole checkpoint log — 59–159 ms),
the review queue, the term list, the word count (three regex passes per row)
and the activity log.

**Change.** The poll already reads the task state on a worker thread. It now
also builds the derived, read-only projections **on that same worker thread**
(`adapter.workbench_models`) and hands them to the view with the state. The
view still owns every widget; only the *data* moves.

The delivery gate is the most expensive projection and keeps its own, slower
cadence: the worker refreshes it every 5 s (the application's existing
`DELIVERY_REFRESH_SECONDS`) and the view draws the last result in between.

**What stays on the GUI thread.** Explicit user-initiated refreshes
(`refresh_workbench(force=True)`) still compute the delivery preview in the
GUI thread, exactly as before: the user just did something and the panel has
to reflect it immediately.

**Risk.** Medium — the view is the busiest file in the project. Mitigations:
the models are an *optional* parameter (`apply(..., models=None)`); with
`models=None` every renderer falls back to its previous behaviour, so all
existing call sites and tests are unaffected. The delivery panel's
"never show another task's content" rule is preserved explicitly.

---

## D3. Make the frozen-delivery checks cheap  *(~100 ms + megabytes of I/O per task-list refresh)*

**Evidence.** `folith/snapshots.py:54` deep-copied the whole state to hash it
(`state_identity`), and `folith/engine/delivery_state.py:503` read and
SHA-256'd every frozen asset (DOCX/PDF bytes) on every task-list refresh.

**Change.**

* `state_identity` now builds a *shallow* copy: the only keys dropped from
  the hash are five top-level ones plus one nested key, so a deep copy was
  never needed. The hash value is unchanged.
* Asset integrity is remembered per `(path, mtime, size)`, so an unchanged
  frozen asset is verified by a `stat` instead of being read and hashed
  again.

**Risk.** Low. The hash input is identical; only the way the input is
obtained changed. Verified by `tests/delivery_snapshot_test.py` (the snapshot
identity is asserted byte-for-byte there).

---

## D4. Stop re-reading the runtime state four times per batch  *(3 781 reads → 1 509 ms)*

**Evidence.** `_runtime_pause_checkpoint`, `_runtime_cancel_requested`,
`_runtime_caption_callback`, `translate_stage` and `_process_batch_payload`
each read and JSON-parsed `runtime_state.json`.

**Change.** Partly solved by D1 (the merged writer removes the write-side
read) and by D5 (the caption callback no longer writes synchronously). The
remaining reads are *control-plane* probes — they must see a cancel request
that may come from another process — so they were **not** replaced by a
cache. A stat-validated cache was considered and rejected: it would delay the
observation of a cross-process cancel, and the honesty of the cancel path is
worth more than the remaining ~0.8 s.

---

## D5. Skip the fsync on every runtime event  *(2 015 ms → 1 708 ms)*

**Evidence.** `core._append_runtime_event` wrote, flushed **and fsync'd** one
record per event — 1 988 times in 40 s.

**Change.** The record is still written and flushed to the OS (any reader sees
it immediately); only the forced platter flush was dropped for ordinary
records. Control-plane and terminal records keep their fsync.

**Risk.** Low. The event log is an activity/diagnostic trail; the business
truth is `state.json` + the journal, which are unaffected.

---

## D6. Remove the two O(segments) scans from the segment-detail refresh  *(145 ms per 40 s)*

**Evidence.** `views.py:5437` and `views.py:5441` each walked every row to
find the current segment and its position.

**Change.** Deferred to the incremental-refresh work (below) rather than
patched in isolation: the row list is rebuilt anyway on every state change,
so the index belongs there.

---

## D7. The task list refresh  *(81 ms typical, 231 ms worst, every 5 s on the GUI thread)*

**Evidence.** `window.refresh_home` → `adapter.list_jobs()` on the GUI
thread; per job it reads the runtime view, the summary and the frozen
delivery status.

**Change.** D3 removes the two dominant costs inside it (the deep copy and
the asset hashing). Moving the whole refresh to a worker thread was
considered and rejected at this stage: the task list is the one surface the
user waits for, and a stale card is worse than a 50 ms wait. It is the next
candidate if the measurement still shows it.

---

## D8. Zero-interval row-height timer  *(fairness)*

**Evidence.** `views.py:2767` sets a `QTimer` to `setInterval(0)`, re-armed by
every `_render_segments`. The chunk loop is budgeted (4 ms per tick), so the
timer spins the event loop between chunks.

**Change.** Deferred. The measurement shows the chunk loop costs 94–150 ms in
total over 40 s and the resulting paint is inside the paint burst, not in the
timer. Changing the interval without a measured win would be a guess.

---

## Verification plan

1. **Unit / integration** — the affected test modules, unchanged:
   `runtime_state_test.py`, `runtime_telemetry_timer_test.py`,
   `llm_transport_test.py`, `runtime_pause_test.py`, `runtime_status_test.py`,
   `delivery_snapshot_test.py`, `state_journal_test.py`,
   `cooperative_persistence_test.py`, `translation_concurrency_test.py`.
2. **Qt client** — `qt_client_window_test.py`, `qt_client_adapter_test.py`,
   `phase3_workbench_ui_test.py`, `qt_delivery_ui_test.py`,
   `qt_delivery_content_test.py`, `qt_delivery_supplement_test.py`,
   `qt_activity_test.py`, `qt_followup_test.py`, `qt_run_status_test.py`,
   `qt_library_test.py`, `qt_home_cards_test.py`.
3. **New regression tests** for the confirmed defects (§ below).
4. **The benchmark itself** — the same harness, before and after.
5. **Interactive stress** — the scenario suite with the window driven while
   the pipeline runs.

## New regression tests (for the confirmed defects)

| Defect | Test |
|---|---|
| Batch progress must not write the runtime state once per batch phase | `tests/runtime_status_coalescing_test.py`: N batch-phase updates ⇒ at most a small constant number of durable state writes; the final state and every event are still present |
| Cancellation must survive the merged status channel | the same module: a cancel request must not be overwritten by a pending status flush, and the worker must observe it |
| The workbench must not recompute whole-document projections on the GUI thread | `tests/qt_workbench_projection_test.py`: `WorkbenchView.apply` with precomputed models must not call the projection adapters on the GUI thread |
| The frozen-delivery identity must not change | `tests/delivery_snapshot_test.py` already asserts it; add an explicit check that the hash is unchanged by the shallow copy |
