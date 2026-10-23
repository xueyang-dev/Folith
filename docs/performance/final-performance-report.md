# Final performance report - Folith desktop client

**Port integration (2026-10-23).** The measurements below remain the recorded
pre-port source-tree pair; they were not rerun after integration. The port now
invalidates background projections when runtime, draft, activity or term-panel
inputs change, and rechecks delivery on its cadence even without a translation
commit. Two regressions reproduced the stale-cache cases before this fix; the
focused suites pass 60 cases afterward. The baseline builder was regenerated
and verified: 38/38 edits restore the product files to pre-port main `b8f82049`.

**Revision 2 (2026-10-23).** This revision replaces the earlier before/after
pair. The first reconstruction reversed C1-C4 but not C5/C6, so its "before"
tree already contained two optimisations. The numbers below come from a tree
that reverses all six changes (27/27 edits), verified against the source. The
old 45.0 s / 226.4 s pair is withdrawn and must not be mixed with these runs.

**Scope.** The confirmed root causes, the changes that were implemented, the
measured before/after numbers, the tests that were run, and what is still
open.

Companion documents:

| Document | Content |
|---|---|
| `docs/performance/memoq-design-study.md` | What memoQ's documentation says about background work |
| `docs/performance/architecture-audit.md` | Folith's real execution map and the blocking inventory |
| `docs/performance/baseline-report.md` | Measurement method and the baseline numbers |
| `docs/performance/optimization-plan.md` | Evidence -> change -> risk for every item |
| `benchmarks/performance/{baseline,optimized,comparison}.json` | The raw measurements used by this revision |

---

## 1. Result

The same 400-paragraph document is translated in **54.42 s instead of
75.82 s** on the fixed pair, with **25.9 % less CPU**, **39.3 % more
throughput**, and **84.9 % less GUI-thread time per refresh**. The
1,500-paragraph document falls from **447.46 s to 326.80 s** (-27.0 %), with
**93.6 % less GUI-thread time per refresh** and peak RSS **1.9 % lower**.

The passive event-loop p95 is **not** better in the running scenarios: it
moves from 29.1 to 41.1 ms in S2 and from 42.9 to 48.3 ms in S10. It remains
below the 100 ms acceptance target, but it is a real regression and is
reported as such in §5.4, not explained away. The foreground measures that
isolate application work (GUI time per refresh, GUI time per segment, stalls
per 100 segments) improve substantially.

---

## 2. Root causes

### 2.1 Batch progress was persisted through the durable, fsync'd path - 26.8 s of a 40 s diagnostic run

`core.update_runtime_state` (a full read-modify-write of
`runtime_state.json`, an atomic replace, and an fsync'd append to
`runtime_events.jsonl`) was called once per batch phase - seven times per
translation batch. A 400-paragraph diagnostic run made **1,997 calls and
spent 26,764 ms of CPU inside a 40-second run**, against a pipeline that
produced 401 translated segments.

The status lines are presentation, not business truth: the translated pairs
are committed by `_commit_batch` into `state.json` plus the journal on an
already cooperative path.

*Location:* `core.py` (`_translation_runtime`, `_runtime_caption_callback`).
*Evidence:* `benchmarks/performance/diagnose_profile.py`, diagnostic profile.
This is a separate 40-second diagnostic, not the paired benchmark below.

### 2.2 The whole document was re-projected on the GUI thread on every poll

`MainWindow.refresh_workbench` -> `WorkbenchView.apply` recomputed the
delivery gate, follow-up facts, review queue, term list, word count, and
activity log. On the fixed baseline this cost **2,456.4 ms of GUI-thread time
in S2 (136.5 ms per refresh)** and **42,675.4 ms in S10 (444.5 ms per
refresh)**.

*Location:* `folith/qt/window.py`, `folith/qt/views.py`.

### 2.3 The task state was copied with a pickle round-trip - 115-130 ms of uninterrupted GIL hold

`job_repository._isolated_copy` is `pickle.loads(pickle.dumps(state))` of the
whole state. `pickle` is a C extension and holds the GIL for its whole
duration. In the diagnostic profile, 49 calls cost 678 ms with a 114.8 ms
worst case.

*Location:* `folith/storage/job_repository.py`.
**This root cause was not fixed.**

### 2.4 The frozen-delivery checks deep-copied the state and re-hashed every asset

`state_identity` deep-copied the whole state to hash it, and
`delivery_snapshot_status` read and SHA-256'd every frozen asset on every
task-list refresh, on the GUI thread.

*Location:* `folith/snapshots.py`, `folith/engine/delivery_state.py`.

### 2.5 The task list was refreshed on the GUI thread every five seconds

`adapter.list_jobs()` on the GUI thread cost **3,000.1 ms over the S10
baseline**, with a worst recorded call of 240.7 ms in the instrumented
long-operation set. The periodic refresh is now off the GUI thread. Explicit
user refreshes are still synchronous; their measured regression is disclosed
in §5.5.

*Location:* `folith/qt/window.py`.

### 2.6 Appending a segment rebuilt the whole table

`views._render_segments` cleared the grid and recreated every
`QTableWidgetItem` whenever the visible row count changed. Translation only
appends, so the common case paid for all previously displayed rows again:
**931.2 ms in S2** and **18,160.7 ms in S10** on the fixed baseline.

*Location:* `folith/qt/views.py` (`_render_segments`).

### 2.7 Contributing factors (not independently dominant)

* The runtime state was read about 4x per batch (3,781 reads / 1,509 ms); the
  control-plane probes were deliberately not cached.
* `views._render_segment_detail` does two O(segments) scans per poll.
* A `QTimer` with `setInterval(0)` is re-armed by every poll.

---

## 3. memoQ lessons - what applied and what did not

### Applied

| memoQ principle | How it was applied |
|---|---|
| "the operation runs in the background, and you can continue working" (progress-windows.html) | The derived, whole-document projections moved off the GUI thread; the GUI only draws what a worker computed. |
| A durable task surface with terminal states and retained failures (task-tracker.html) | The merged status writer keeps a truthful terminal state, retains failures, and never lets a late flush revive a finished task. |
| "you can also stop the indexing if you need your computer to run faster" (corpus-indexing-overview.html) | The status channel now costs about one third of what it did; the foreground can no longer be starved by it. |
| "memoQ waits half a second before showing any lookup results" (options-miscellaneous.html) | The delivery gate got its own explicit, slower cadence instead of being recomputed on every poll. |
| "Maximum number of translation memory and corpus hits shown" (translation-results-settings.html) | Bounded work per refresh: one merged status write per window, one delivery gate computation per cadence. |
| "the default is 2, and it is not recommended to change them" (server-administrator-omniscien.html) | Concurrency was not increased. The batch pool is unchanged. |

### Not applied, deliberately

| memoQ feature | Why not |
|---|---|
| Task tracker as a separate window | Folith already has a persistent activity panel and a run-status strip. |
| Server-side concurrency configuration | Folith has no server tier. |
| Views (named, persisted segment slices) | The measured grid cost was rebuilding all rows on append (C6), not the absence of saved slices. |
| Session restore of open tabs/segment | A usability feature unrelated to the reported symptom. |
| MT parallelism configuration | No MT path in the measured scenarios. |

The adopted discipline is measurable: "the foreground must never wait for a
background job" is evaluated as GUI-thread time per refresh and per segment,
not as a slogan.

---

## 4. Changes (C1-C6)

### C1 - The batch-progress status channel is merged (`core.py`)

A per-job `_RuntimeStatusWriter` merges status updates for a 0.4 s window and
writes them once. The activity log still receives every event; only the state
file is coalesced. Cancellation, pause, the worker lease, heartbeat, stage
boundaries, and terminal states stay synchronous. Durable phases
(`state_commit`, `state_commit_done`) bypass the window. The writer is closed
before a terminal state is written, and a late flush cannot revive a finished
task. The writer helper itself predates this work; the changes are at the
call sites and in finalisation.

### C2 - Whole-document projections run on the worker thread (`folith/qt/adapter.py`, `window.py`, `views.py`)

The poll already read the task state off the GUI thread; it now also builds
the derived projections (`adapter.workbench_models`) there and hands them to
the view with the state. `WorkbenchView.apply` accepts optional models; with
`models=None` it behaves as before, so existing call sites keep their
semantics. The delivery gate keeps its own slower cadence.

Two follow-ups from review are included:

* The worker's delivery-preview cache is now a self-contained
  `_DeliveryPreviewCache` guarded by a lock. The worker no longer writes
  `_delivery_model*` fields on the `MainWindow` object.
* `_render_delivery` clears the panel only when the **job** changes. A
  same-job key change (snapshot count or delivery status) keeps the previous
  preview until the next background result arrives, instead of showing a
  blank panel for up to the five-second cadence. Switching tasks still
  clears immediately, pinned by
  `test_switching_between_two_delivered_tasks_shows_the_right_assets`; the
  no-blank behaviour is pinned by
  `test_same_job_delivery_key_change_keeps_panel_until_fresh`.

### C3 - Frozen-delivery checks are cheap (`folith/snapshots.py`, `folith/engine/delivery_state.py`)

`state_identity` builds a shallow copy instead of a deep copy; the hash value
is unchanged. Asset integrity is remembered per `(path, mtime_ns, size)`, so
an unchanged frozen asset is verified with a `stat` instead of being read and
hashed again.

The integrity trade-off is explicit: the status probe can be fooled by a
same-size rewrite with a restored timestamp. Export/read-back no longer uses
that cache: `load_asset` re-hashes the bytes it just read
(`folith/snapshots.py:249`), with a regression test. That export fix landed
after the measured pair; the benchmark hot path uses `asset_intact`, not
`load_asset`. The status cache was the cost C3 removed and remains a disclosed
residual risk, not an absolute tamper guarantee.

### C4 - The runtime event log is not fsync'd per ordinary record (`core.py`)

The record is written and flushed to the OS (readers see it immediately);
only the forced platter flush is dropped for ordinary records. Control-plane,
cancel, terminal, and state-commit records keep their fsync. The event log is
an activity trail; the business truth is `state.json` plus the journal. The
change is deliberately narrower than "no fsync anywhere".

### C5 - The periodic task-list refresh moves off the GUI thread (`folith/qt/window.py`)

The five-second poll uses the existing `_refresh_home_async` path while the
home view is active; a generation counter discards stale results. The
workbench poll is unchanged. Explicit user-initiated refreshes remain
synchronous because the user expects the list to reflect the action
immediately. That explicit path is still a measured regression (§5.5).

### C6 - Appended segments reuse existing grid cells (`folith/qt/views.py`)

When the visible list grows and every existing segment keeps its UID and
position, `_render_segments` increases `rowCount`, creates cells only for new
rows, and keeps the painted cache. Removal, reordering, task switches, and
changed prefix UIDs take the full-rebuild path; same-count content changes are
repainted in place. The tests pin both the append reuse and the changed-prefix
rebuild.

### Not changed (and why)

* **The pickle round-trip in `_isolated_copy`.** It remains a known GIL hold;
  it was out of scope for this pass and is listed as unfinished.
* **No new threads, executors, or process pool.** The existing QThreadPool and
  pipeline worker were reused.
* **No concurrency increase.** The batch pool is unchanged.
* **The cancel/pause path was not cached.** A cancel must be observed
  promptly, including from another process.
* **No new timers.** The heartbeat cadence is unchanged.

---

## 5. Before / after

### 5.1 Provenance and commands

The baseline tree is `<tmp>/folith-revert-check3`,
built with:

```powershell
python -m benchmarks.performance.revert_to_baseline <target-dir>
```

The script now refuses to write a marker unless all 27 edits apply. The
runner refuses `--baseline` on a tree without that marker, because
`baseline_mode.py` alone only reverses C1-C4. The existing marker was later
rewritten without a UTF-8 BOM; its content stayed `reverted 27/27 edits`, and
no measured code or benchmark data changed.

Runs used for this revision:

```powershell
# from the reverted tree
python -m benchmarks.performance.run_benchmark --scenario idle --duration 300 --baseline --out <...>
python -m benchmarks.performance.run_benchmark --scenario translate --scenario review --scenario export --scenario large --duration 600 --baseline --out <...>

# from D:\03_Development\Folith\source
python -m benchmarks.performance.run_benchmark --scenario idle --duration 300 --out <...>
python -m benchmarks.performance.run_benchmark --scenario translate --scenario review --scenario export --scenario large --duration 600 --out <...>
```

The per-duration outputs were merged into `baseline.json` and
`optimized.json`; `comparison.json` was generated from those files. The JSON
files carry the tree path, the 27/27 count, and whether `--baseline` was used.

### 5.2 Headline (S2, 400 paragraphs, 401 segments)

| Metric | Before | After | Change |
|---|---|---|---|
| Time to finish | 75.82 s | **54.42 s** | **-28.2 %** (1.39x faster) |
| CPU time | 85.73 s | **63.55 s** | **-25.9 %** |
| Throughput | 5.289 seg/s | **7.369 seg/s** | **+39.3 %** |
| GUI-thread time per refresh | 136.5 ms | **20.6 ms** | **-84.9 %** |
| GUI-thread time per segment | 6.126 ms | **0.565 ms** | **-90.8 %** |
| Event-loop delay p95 | 29.143 ms | 41.072 ms | **+40.9 %** |
| Event-loop delay p99 | 44.340 ms | 65.119 ms | +46.9 % |
| Event-loop delay max | 360.365 ms | 397.637 ms | +10.3 % |
| Stalls > 100 ms | 18 | 17 | -5.6 % |
| Interaction handler p95 / max | 11.272 / 114.993 ms | 14.228 / 98.896 ms | +26.2 % / -14.0 % |
| Peak RSS | 365.38 MB | 366.80 MB | +0.4 % |

The 1,500-paragraph scenario:

| Metric | Before | After | Change |
|---|---|---|---|
| Time to finish | 447.46 s | **326.80 s** | **-27.0 %** (1.37x faster) |
| CPU time | 553.47 s | **406.64 s** | **-26.5 %** |
| Throughput | 3.354 seg/s | **4.593 seg/s** | **+36.9 %** |
| GUI-thread time per refresh | 444.5 ms | **28.4 ms** | **-93.6 %** |
| GUI-thread time per segment | 28.431 ms | **1.211 ms** | **-95.7 %** |
| Event-loop delay p95 | 42.917 ms | 48.346 ms | **+12.6 %** |
| Event-loop delay p99 | 150.943 ms | 158.323 ms | +4.9 % |
| Event-loop delay max | 1,361.842 ms | 1,092.320 ms | -19.8 % |
| Stalls > 100 ms | 204 | 140 | -31.4 % |
| Interaction handler p95 / max | 16.624 / 528.714 ms | 19.679 / 574.425 ms | +18.4 % / +8.6 % |
| Peak RSS | 774.23 MB | 759.77 MB | -1.9 % |

### 5.3 All scenarios

| Scenario | Metric | Before | After | Change |
|---|---|---|---|---|
| **S1 idle** (300 s) | delay p95 | 21.799 ms | 20.256 ms | -7.1 % |
| | delay p99 | 42.414 ms | 24.815 ms | -41.5 % |
| | delay max | 261.608 ms | 176.511 ms | -32.5 % |
| | stalls > 100 ms | 43 | 7 | -83.7 % |
| | CPU | 189.48 s | 175.12 s | -7.6 % |
| | GUI ms / refresh | 5.0 ms | 3.6 ms | -28.0 % |
| | interaction p95 / max | 7.023 / 32.192 ms | 5.920 / 27.266 ms | -15.7 % / -15.3 % |
| **S2 translate** | wall | 75.82 s | 54.42 s | -28.2 % |
| | CPU | 85.73 s | 63.55 s | -25.9 % |
| | delay p95 | 29.143 ms | 41.072 ms | +40.9 % |
| | delay p99 | 44.340 ms | 65.119 ms | +46.9 % |
| | delay max | 360.365 ms | 397.637 ms | +10.3 % |
| | stalls > 100 ms | 18 | 17 | -5.6 % |
| | GUI ms / refresh | 136.5 ms | 20.6 ms | -84.9 % |
| | interaction p95 / max | 11.272 / 114.993 ms | 14.228 / 98.896 ms | +26.2 % / -14.0 % |
| | throughput | 5.289 seg/s | 7.369 seg/s | +39.3 % |
| | peak RSS | 365.38 MB | 366.80 MB | +0.4 % |
| **S7 review / QA** | wall | 71.21 s | 50.61 s | -28.9 % |
| | CPU | 84.52 s | 67.86 s | -19.7 % |
| | delay p95 | 29.981 ms | 39.358 ms | +31.3 % |
| | delay p99 | 41.530 ms | 74.059 ms | +78.3 % |
| | delay max | 275.199 ms | 249.231 ms | -9.4 % |
| | stalls > 100 ms | 14 | 18 | +28.6 % |
| | GUI ms / refresh | 148.3 ms | 15.7 ms | -89.4 % |
| | throughput | 5.631 seg/s | 7.923 seg/s | +40.7 % |
| **S8 export running** | wall | 68.80 s | 51.92 s | -24.5 % |
| | CPU | 89.34 s | 74.66 s | -16.4 % |
| | delay p95 | 30.413 ms | 39.321 ms | +29.3 % |
| | delay p99 | 45.996 ms | 73.061 ms | +58.8 % |
| | delay max | 272.420 ms | 321.458 ms | +18.0 % |
| | stalls > 100 ms | 13 | 17 | +30.8 % |
| | GUI ms / refresh | 127.2 ms | 21.8 ms | -82.9 % |
| | interaction p95 / max | 13.229 / 76.900 ms | 16.288 / 275.969 ms | +23.1 % / +258.9 % |
| | throughput | 5.829 seg/s | 7.723 seg/s | +32.5 % |
| **S10 large** (1,500 -> 1,501 seg.) | wall | 447.46 s | 326.80 s | -27.0 % |
| | CPU | 553.47 s | 406.64 s | -26.5 % |
| | delay p95 | 42.917 ms | 48.346 ms | +12.6 % |
| | delay p99 | 150.943 ms | 158.323 ms | +4.9 % |
| | delay max | 1,361.842 ms | 1,092.320 ms | -19.8 % |
| | stalls > 100 ms | 204 | 140 | -31.4 % |
| | GUI ms / refresh | 444.5 ms | 28.4 ms | -93.6 % |
| | interaction p95 / max | 16.624 / 528.714 ms | 19.679 / 574.425 ms | +18.4 % / +8.6 % |
| | throughput | 3.354 seg/s | 4.593 seg/s | +36.9 % |
| | peak RSS | 774.23 MB | 759.77 MB | -1.9 % |

### 5.4 How to read the event-loop numbers

The absolute p95/p99 in the running scenarios are worse. This is not hidden:
the optimised build finishes the same work faster, so more background
operations overlap in any given second; the new home-list reads run on a
worker and compete for the GIL; and the synchronous explicit list refresh is
slower. The data does not yet separate those causes, so no single cause is
claimed.

What is isolated from throughput does improve:

* GUI-thread time per refresh: **-82.9 % to -93.6 %** in S2/S7/S8/S10.
* GUI-thread time per segment: **-90.8 %** (S2) and **-95.7 %** (S10).
* Stalls per 100 translated segments: 4.49 -> 4.24 (S2), 13.59 -> 9.33 (S10).
* Worst single stall: -19.8 % (S10), -9.4 % (S7), +10.3 % (S2), +18.0 % (S8).

The p95 remains **48.3 ms or lower in every running scenario**, below the
100 ms target. The idle scenario improves on every event-loop, CPU, stall, and
GUI-refresh metric in the table.

**Host drift.** A second consecutive optimised run on the same host produced
S2 60.77 s and S10 346.02 s, versus 54.42 s and 326.80 s in the pair above.
The fixed baseline's S10 is 447.46 s, versus 401.5 s in the withdrawn pair,
while S2 is 75.82 s versus 79.6 s. Absolute times therefore vary materially
between passes; only ratios from the same fixed pair are used here. The
earlier idle repeat came from the withdrawn pair and is not evidence for this
revision.

### 5.5 Function-level confirmation

S2 (400 paragraphs):

| Operation | Before | After |
|---|---|---|
| `views.apply` (GUI thread) | 2,456.4 ms | **226.5 ms** |
| `views._render_segments` | 931.2 ms | **88.4 ms** |
| `views._render_delivery` | not separately labelled (inside `views.apply`) | **7.9 ms** |
| `adapter.list_jobs` | 193.8 ms | 399.0 ms |

S10 (1,500 paragraphs):

| Operation | Before | After |
|---|---|---|
| `views.apply` (GUI thread) | 42,675.4 ms | **1,818.0 ms** |
| `views._render_segments` | 18,160.7 ms | **509.9 ms** |
| `views._render_delivery` | 18,194.1 ms | **35.2 ms** |
| `adapter.list_jobs` | 3,000.1 ms | **6,757.4 ms (worse)** |

S10 `_render_segments` max fell from 216.8 ms to 116.8 ms. The earlier
"67 -> 219 ms" reading came from the invalid pair and is withdrawn.

The `adapter.list_jobs` total is worse, but it is not a like-for-like row:
the optimised S10 trace contains 30 calls above 25 ms (max 618.4 ms) while
the baseline trace contains one (max 240.7 ms), on 64 explicit refreshes
versus 88 in the baseline. Each synchronous explicit refresh is slower; it
was not issued more often. The periodic five-second path is off the GUI
thread. The explicit path is the remaining regression:

| S10 explicit home refresh | Before | After |
|---|---|---|
| p95 | 118.264 ms | 282.888 ms |
| max | 528.714 ms | 434.028 ms |

This is consistent with GIL/thread contention from C2/C5, but it has not been
isolated with a profiler. It is listed as an open item, not as a solved one.

---

## 6. Functional tests

### 6.1 New regression tests

| File | What it pins |
|---|---|
| `tests/runtime_status_coalescing_test.py` | 50 status updates collapse into at most 3 durable writes; every event still reaches the activity log; cancel and terminal states are never overwritten; **durable phases (`state_commit`, `state_commit_done`) are written immediately while ordinary progress waits for the flush**; **ordinary appends skip fsync and terminal appends still fsync once**. |
| `tests/qt_workbench_projection_test.py` | With precomputed models the view does not call the projection adapters; without models it still computes them; the delivery gate does not run on the GUI thread during a poll; **a same-job key change keeps the old panel until fresh data arrives**. |
| `tests/qt_client_window_test.py` | Opening a task computes the delivery preview once; **the periodic poll calls the async home refresh, not the sync one**; **a stale async home result is discarded by generation**; **an enabled delivery button still revalidates on click instead of trusting the cached preview**. |
| `tests/qt_inline_editor_test.py` | Appending visible segments keeps existing `QTableWidgetItem` objects; **a changed prefix UID rebuilds instead of reusing stale cells**. |
| `tests/delivery_snapshot_test.py` | **Export re-hashes the bytes it reads even when a same-size rewrite restores the mtime**, so the status cache cannot mask tampering. |

Focused run after adding these tests:

```powershell
pytest tests/runtime_status_coalescing_test.py tests/qt_workbench_projection_test.py tests/qt_inline_editor_test.py tests/qt_client_window_test.py::test_periodic_home_poll_uses_async_refresh tests/qt_client_window_test.py::test_async_home_refresh_discards_a_stale_generation tests/qt_client_window_test.py::test_opening_a_task_computes_the_delivery_preview_once tests/qt_client_window_test.py::test_switching_between_two_delivered_tasks_shows_the_right_assets tests/qt_client_window_test.py::test_a_stale_delivery_button_still_revalidates_before_freezing tests/delivery_snapshot_test.py::test_load_asset_rehashes_after_same_size_rewrite_with_restored_mtime -q
```

Result: **30 passed in 300.01 s**.

The C3 `load_asset` rehash and the C2 worker-cache/panel follow-ups landed
after the measured pair. Neither adds whole-document work to the GUI thread:
C2 keeps the preview worker-owned, and C3 only re-hashes bytes on export
reads.

### 6.2 Executed runs

| Run | Command | Result |
|---|---|---|
| Non-Qt suite | `pytest tests -q --ignore-glob="tests/qt_*.py"` | **23 failed, 3,048 passed, 35 skipped** in 1,701.85 s |
| Affected Qt suite | `pytest tests/qt_client_window_test.py tests/qt_inline_editor_test.py tests/qt_workbench_projection_test.py tests/qt_delivery_ui_test.py tests/qt_delivery_content_test.py tests/qt_delivery_supplement_test.py -q` | **2 failed, 125 passed** in 637.75 s |
| Focused delivery/projection | `pytest tests/qt_client_window_test.py::test_opening_a_task_computes_the_delivery_preview_once tests/qt_workbench_projection_test.py tests/qt_delivery_ui_test.py tests/qt_delivery_content_test.py tests/qt_delivery_supplement_test.py -q` | **30 passed** in 28.39 s |
| Inline editor | `pytest tests/qt_inline_editor_test.py -q` | **11 passed** in 8.27 s |
| New focused set | see §6.1 | **30 passed** in 300.01 s |

The earlier focused-delivery and inline-editor rows predate the tests added
for this revision; the 30-test focused run is the current set.

Triage of the failures:

* **21 of the 23 non-Qt failures reproduce on the baseline tree.** The two
  exceptions are `smoke_test.py::test_opencode_go_provider_route` and
  `smoke_test.py::test_misc_helpers`; both pass alone and fail only in the
  full run with the literal value `'[]'`, consistent with a list-returning
  test stub left in `core.call_llm`. This is test-order pollution.
* **Both Qt failures reproduce on the baseline tree**:
  `test_home_can_move_a_task_to_another_project` and
  `test_menu_shortcuts_actually_fire_and_are_not_ambiguous`. The baseline run
  of exactly those two tests is `2 failed in 6.94 s` with identical
  assertions.
* The combined `pytest tests -q` run did not finish within a 40-minute limit.
  The suites above were run separately; no claim is made that every Qt module
  passed.

### 6.3 Interactive stress

The benchmark driver delivers foreground gestures from the Qt event loop while
the pipeline runs, so each measurement is what a click would have felt like.
The 1,500-paragraph run performed 8,374 actions (navigation, selection,
scrolling, edits, search, settings, and explicit home refresh). Overall
interaction-handler p95 was 19.679 ms, p99 26.1 ms. Excluding explicit home
refresh, per-action p95 values were: workbench 25.5 ms, projects 2.9 ms,
home 1.9 ms, scroll 0.9 ms, select segment 21.0 ms, edit 0.03 ms, search
0.04 ms, settings 0.005 ms. Explicit home refresh was the outlier at p95
282.9 ms / max 434.0 ms, discussed in §5.5.

These are synthetic offscreen interactions, not a human acceptance run; they
exercise the same Qt handlers but not the native window manager or a real IME.

---

## 7. Remaining limitations

1. **Passive event-loop p95 regressed in running scenarios** (S2 +40.9 %,
   S7 +31.3 %, S8 +29.3 %, S10 +12.6 %). It stays below 100 ms, but it is a
   regression. The remaining largest gaps are paint bursts (up to 1,092 ms
   in S10), not instrumented application logic. The offscreen plugin renders
   this more expensively than a GPU-composited native window.
2. **C3's status cache is not adversarial tamper-proof.** `asset_intact`
   remembers digests per `(path, mtime_ns, size)`; a same-size rewrite with a
   restored timestamp can still pass the status probe. The export path
   (`load_asset`) now re-hashes the bytes it reads and has a regression test.
   Full content hashing on every status poll was the cost C3 removed; the
   residual risk is disclosed rather than hidden.
3. **C4 is narrower than "no fsync".** Only ordinary runtime-event records use
   `sync=False`; control-plane, cancel, terminal, and state-commit records
   keep their durable flush.
4. **C5 only affects the periodic home poll.** The workbench poll is unchanged;
   the periodic home refresh is async only while the home view is active.
   Explicit user refreshes remain synchronous, and S10 explicit refresh
   regressed (§5.5). The cause is not yet profiled.
5. **C1 reuses the existing writer.** The helper and its 0.4 s window predate
   this work; the changes are at the call sites and in finalisation. The new
   immediate-durable-phase test covers the intended contract.
6. **C6 reuses rows only under a strict condition** (visible list grew and all
   existing segment UIDs kept their positions). Reordering, removal, task
   switches, and changed prefix UIDs take the rebuild path. Same-count content
   changes are repainted in place. The S10 maximum is 116.8 ms, not zero.
7. **`views._render_delivery` still costs 35.2 ms in S10** after the optimisation
   (down from 18,194.1 ms); it is no longer dominant but not eliminated.
8. **The pickle root cause is unfixed.** `job_repository._isolated_copy` still
   does a full `pickle` round-trip and can hold the GIL for ~115 ms.
9. **`adapter.list_jobs` and explicit home refresh are still synchronous.**
   S10 total rose to 6,757.4 ms and explicit refresh p95 to 282.9 ms. This is
   the clearest remaining foreground regression.
10. **No energy measurement was possible** on this host. CPU time is the
    proxy; no watt-hour or battery figures are claimed.
11. **The measurements are from one Windows machine** and vary by roughly
    +/-15 % in wall time and +/-10 % in CPU between repeats. A second
    optimised pass measured 60.77 s (S2) and 346.02 s (S10). Absolute numbers
    are not portable; the fixed-pair ratios are the result.
12. **macOS was not measured.** The changes are in shared Python/Qt code with
    no platform-specific branch, but the measurements are Windows-only.
13. **The combined test suite did not complete within 40 minutes**, so the
    pass/fail table is per-suite, not an all-tests claim.

The delivery panel can keep an older preview for up to five seconds after a
same-job state change. A click re-runs `adapter.delivery_preview` and the
backend gate (`core.approve_delivery`) revalidates before freezing; the stale
button path is pinned by
`test_a_stale_delivery_button_still_revalidates_before_freezing`. The
`_DeliveryPreviewCache` lock is deliberately coarse and currently has one
caller, the single in-flight poll worker; future callers must not invoke it
from the GUI thread.

---

## 8. Next priorities

1. **Profile and fix explicit home refresh / `adapter.list_jobs`.** It is the
   one user gesture that got worse (S10 p95 118 -> 283 ms). Determine whether
   C2/C5 GIL contention or `list_jobs` growth dominates; if contention,
   serialise or defer the explicit refresh and show the stale list briefly.
2. **Move segment-table painting to a model/view with a lazy delegate.** The
   remaining gaps are paint bursts up to 1,092 ms (S10) and 398 ms (S2) while
   instrumented application time is 1,818 ms / 227 ms. This is the largest
   remaining GUI cost.
3. **Make the remaining whole-document projections incremental** (delivery
   gate, `recovery_summary`), so they scale with the change, not the document.
4. **Remove the pickle round-trip from `_isolated_copy`** or replace it with a
   copy-on-write/shallow representation that preserves correctness.
5. **Triage the full-suite hang and the two order-dependent smoke tests.**
   Until then, do not claim an all-tests pass.

## Port to main (issue #88)

The numbers in this report were measured on the pre-port working tree
(`source`), not on the merged branch. C1-C6 were integrated hunk by hunk into
`main`, whose workbench already offloaded the review queue and the delivery
preview on the *open-task* path; the poll path now uses `adapter.workbench_models`
as well. `revert_to_baseline.py` was regenerated against the ported code: it
reverts 37/37 hunks and the reverted product files are identical to `main`.
The `27/27` counts quoted above refer to the pre-port tree and are kept as
measured.
