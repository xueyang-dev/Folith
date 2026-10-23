# Folith architecture audit — where the desktop client spends its time

**Method.** Every statement below was read out of the repository at the paths
given, and every cost figure was *measured*, not estimated (see
`baseline-report.md` for the method and the raw numbers). The audit was done
before any change was made.

**Stack.** Python 3.11.16, PySide6 6.11.2 (Qt Widgets), Windows/macOS
desktop client. Entry points:

| Entry | File | Role |
|---|---|---|
| `python qt_client.py` / `python -m folith.qt` | `folith/qt/app.py` | desktop client; pins the data/log dirs *before* importing `core` |
| `folith/qt/window.py:3431` `build_application` | | `QApplication` factory |
| `folith/qt/window.py:112` `MainWindow` | | the only window; owns presentation + orchestration |
| `core.py:8013` `start_job_worker` | | the background pipeline worker |

---

## 1. The execution map

```
User presses "start translation"
   │
   ├─ window.py:1725  MainWindow.start_or_resume()
   │     └─ runner.submit(start)                  ← Qt pool thread (workers.py)
   │           └─ adapter.start_job → application/commands.py → core.start_job_worker
   │                 └─ core.py:8089  threading.Thread("folith-<job_id>", daemon=True)
   │                       ├─ core.py:6659  ThreadPoolExecutor("ft-trans", max_workers=N)
   │                       │     └─ core.py:5344  _process_batch_payload()
   │                       │           ├─ translate_batch → core.call_llm → HTTP
   │                       │           ├─ core._translation_runtime(...)   ─┐
   │                       │           └─ core._runtime_caption_callback()  ─┤ status channel
   │                       ├─ core.py:6410  _commit_batch → state.json + journal
   │                       └─ core.py:7844  heartbeat thread (5 s)
   │
   └─ window.py:249  QTimer(POLL_MS = 1000) → MainWindow._poll()
         └─ window.py:1619  _poll_workbench()
               ├─ adapter.state_signature(job_id)          (2 stat calls, GUI thread)
               ├─ runner.submit(adapter.load_state)        ← Qt pool thread
               │     └─ core.load_job_state → job_repository.load_job_state_cached
               │           ├─ json.loads(state.json)       (cache miss)
               │           ├─ state_journal.replay(...)
               │           └─ _isolated_copy(state)        ← pickle round-trip
               └─ window.py:1654  refresh_workbench(preloaded=True)
                     └─ window.py:1684  workbench.apply(...)
                           ├─ _render_segments   → _paint_row, _schedule_row_heights
                           ├─ _render_delivery   → adapter.delivery_preview  (whole document)
                           ├─ _render_followup   → adapter.followup_view
                           ├─ _render_review     → adapter.review_queue
                           ├─ _render_terms      → adapter.glossary_view
                           ├─ _render_activity   → adapter.activity_events
                           └─ _render_segment_detail / _render_runtime / …
```

Two independent channels meet on the GUI thread: **the status channel** (the
worker writing `runtime_state.json` and `runtime_events.jsonl`) and **the view
channel** (the GUI thread re-projecting the whole document on every poll).

---

## 2. Thread, process and executor ownership

| Owner | Kind | Where |
|---|---|---|
| `folith-<job_id>` | `threading.Thread` (daemon) | `core.py:8089` — the pipeline worker |
| `folith` (heartbeat) | `threading.Thread` (daemon, 5 s) | `core.py:7844` |
| `ft-trans-N` | `ThreadPoolExecutor` (translation batches) | `core.py:6659` |
| `ft-annot-N` | `ThreadPoolExecutor` (annotation batches) | `core.py:4172` |
| `manual-review-N` | `ThreadPoolExecutor` | `core.py:10423` |
| `vlm-page-N` | `ThreadPoolExecutor` | `folith/document_understanding.py:463` |
| OCR page workers | `queue.Queue` + N threads | `core.py:1206`, `core.py:1267` |
| reviewer pool | `ThreadPoolExecutor` + `Condition` back-pressure | `folith/review_scheduler.py:217` |
| Qt pool | `QThreadPool`, **max 4 threads** | `folith/qt/workers.py:57` |
| telemetry flush | `threading.Timer` (one per pending window) | `folith/engine/runtime_telemetry.py:107` |
| resource sampler | `threading.Thread` (1.5 s) | `folith/trace.py:227` |

Observed peak thread count in the benchmark: **40–49 threads**.

`sys.setswitchinterval(0.001)` is set in `folith/qt/app.py:92`, so the GUI
thread gets the GIL at least every millisecond. That is the *only* reason the
window stays alive at all under load — and it also means that any GIL-held
work on a worker thread is felt directly by the GUI thread.

---

## 3. The status channel (worker → disk → GUI)

### 3.1 What it writes

| Artefact | Written by | Format |
|---|---|---|
| `runtime_state.json` | `core.update_runtime_state` | whole-file rewrite, atomic replace |
| `runtime_events.jsonl` | `core._append_runtime_event` | append + `flush` + `os.fsync` |
| `state.json` | `core.save_job_state` | whole-file rewrite, atomic replace (cooperative encoder) |
| `state_journal.jsonl` | `folith/storage/state_journal.py` | append + fsync |

### 3.2 How often

`update_runtime_state` is a *read-modify-write of the whole file*, guarded by
a global `threading.RLock` (`core.py:226`) plus a per-job `.runtime-write.lock`
file lock, and it is followed by an fsync'd JSONL append whenever an event is
attached.

The call sites that matter, per translation batch:

| Call site | What it says |
|---|---|
| `core.py:5377` `_translation_runtime("context_build", …)` | building the batch context |
| `core.py:5535` `_translation_runtime("translation_generation", …)` | translating |
| `core.py:5613` `_translation_runtime("translation_qa", …)` | deterministic QA |
| `core.py:6410` `_translation_runtime("state_commit", …)` | writing the batch |
| `core.py:6419` `_translation_runtime("state_commit_done", …)` | batch saved |
| `core.py:7835` `_runtime_caption_callback(…)` | the user-facing caption |
| `core.py:7904` (lambda) | the batch summary caption |

**Measured (400-paragraph document, 40 s):** 1 997 calls to
`update_runtime_state`, **26 764 ms** of wall time — 26.8 seconds of a
40-second run spent writing *status*, against a pipeline that produced 401
translated segments. 1 988 fsync'd event appends.

Every one of those is a status line, not business truth: the translated pairs
are committed by `_commit_batch` into `state.json` + the journal.

### 3.3 The read side

`load_runtime_state` was called **3 781 times** in the same 40 s
(1 509 ms) — roughly four file reads plus JSON parses per batch:

- `_runtime_pause_checkpoint` → 1 read (`core.py:276`)
- `_runtime_cancel_requested` → 1 read (`core.py:267`)
- `_runtime_caption_callback` → 3 reads (`core.py:7829`, `:7835`, `:7837`)
- `translate_stage` → 2 reads (`core.py:6654`, `:6655`)
- `_process_batch_payload` → 1 read (`core.py:5353`)

### 3.4 The GUI's read of the same channel

`adapter.runtime_view` → `core.build_job_runtime_view` →
`read_runtime_events(job_id, 5, visibility="user")` + `load_runtime_state`:
**349 ms over 41 polls** (8.5 ms per poll), on the GUI thread.

---

## 4. The view channel (GUI thread)

`MainWindow.refresh_workbench` → `WorkbenchView.apply` re-projects the whole
document on every poll. Measured over 40 s (baseline):

| Renderer | Calls | Total | Max one call | Thread |
|---|---|---|---|---|
| `views.apply` (total) | 41 | 2 803 ms | **189 ms** | GUI |
| `_render_segments` | 41 | 1 107 ms | 128.9 ms | GUI |
| `_render_delivery` → `adapter.delivery_preview` | 41 | 544 ms | 104.3 ms | GUI |
| `_sync_word_count` | 41 | 597 ms | 30.4 ms | GUI |
| `_render_followup` → `adapter.followup_view` | — | 579 ms | 107 ms | GUI |
| `_render_activity` → `adapter.activity_events` | — | 459 ms | 15 ms | GUI |
| `_render_segment_detail` | 82 | 145 ms | 5.9 ms | GUI |
| `adapter.runtime_view` | 41 | 349 ms | 10.3 ms | GUI |
| `adapter.list_jobs` (home refresh) | 6 | 81 ms each | 231 ms | GUI |

Inside those:

- `adapter.delivery_preview` → `core.validate_delivery_translation_state` →
  `check_translation_batch` over **every** pair (a deterministic QA sweep),
  plus `EntityRegistry.consistency_findings(pairs)`: **170 ms per call**.
- `adapter.followup_view` → `core.recovery_summary` → `checkpoint.read_events`
  reads and parses the **whole** checkpoint event log on every call
  (59–159 ms).
- `adapter.load_state` (already backgrounded) → `job_repository._isolated_copy`
  = `pickle.loads(pickle.dumps(state))` of the whole state:
  **115–130 ms of uninterrupted GIL hold** per call, 49 calls.
- `_render_segment_detail` → two linear scans over all rows
  (`views.py:5437`, `views.py:5441`) — O(segments) per poll.

---

## 5. Main-thread blocking inventory

| # | What | Where | Why it hurts |
|---|---|---|---|
| B1 | Per-batch runtime status written through the durable path | `core.py:5249`, `:7825` | 26.8 s of CPU in a 40 s run; holds the GIL and the disk |
| B2 | `load_state` on the GUI thread | `window.py:1136`, `:1201`, `:1244`, `:1661`, `:2025`, `:2357`, `:2433`, `:2665` | multi-MB `json.loads` + pickle copy |
| B3 | `delivery_preview` on the GUI thread | `window.py:2578`, `:2621`, `:2885` (`export_plan` builds the whole DOCX/PDF) | 170 ms per call |
| B4 | `list_jobs` on the GUI thread | `window.py:809` (`refresh_home`, every 5 s) | 81 ms per call, 231 ms worst |
| B5 | `state_identity` deep-copies the whole state | `folith/snapshots.py:54` | ~100 ms per task-list refresh |
| B6 | `delivery_snapshot_assets` reads and hashes every frozen asset | `folith/engine/delivery_state.py:503` | megabytes of I/O + sha256 per refresh |
| B7 | Every batch-mutating adapter call writes the whole state synchronously | `window.py:1994`, `:2011`, `:2112`, `:2124`, `:2722`, `:2744` | `encode_state` takes the **non-cooperative** branch on the main thread (`job_repository.py:188`) |
| B8 | `start_job_worker` performs 2–3 durable runtime writes on the calling (GUI) thread | `core.py:8064`, `:8075` | ~40 ms at start |

## 6. Signal / progress flooding

- The Qt side has **no** progress-signal flooding: `TaskRunner`
  (`folith/qt/workers.py`) exposes `done` / `failed` / `progress`, and
  `progress` is only used by the delivery build (`window.py:1931`).
- The **file-based** channel floods instead: `runtime_events.jsonl` grew by
  ~2 800 records in 40 s, and the activity panel re-reads its tail on every
  signature change.
- The status channel itself is the flood: 1 997 whole-file rewrites in 40 s.

## 7. Repaint / model-update inventory

- `WorkbenchView.apply` calls **15** renderers per poll; 6 of them recompute
  a whole-document projection whenever the state object identity changes
  (which is every committed batch).
- `_paint_row` compares the cached row dataclass per position — cheap per row,
  but it runs for every visible row on every poll.
- `_schedule_row_heights` rebuilds a pending list of **all** row positions
  and drives it from a `QTimer` with `setInterval(0)` — a busy-ish timer on
  the GUI thread (`views.py:2767`). The chunk loop itself is budgeted
  (4 ms per tick), so it is a fairness problem rather than a correctness one.
- `style_module.repolish` (`folith/qt/style.py:1311`) is memoised, but its
  cache key is rebuilt on every call (dynamic properties + `repr`).

## 8. Polling and timers

| Timer | Interval | Where | Note |
|---|---|---|---|
| main poll | 1 000 ms | `window.py:249` | the poll that triggers the whole view channel |
| home refresh | 5 s | `window.py:1464` | `adapter.list_jobs()` on the GUI thread |
| row-height chunk | **0 ms** | `views.py:2767` | re-armed by every `_render_segments` |
| row-height resize | 120 ms | `views.py:2769` | clears the height cache → full re-measure |
| draft autosave | 1 500 ms | `views.py:2492` | |
| PDF follow | 150 ms | `views.py:3541` | |
| edit-save idle | 400 ms | `window.py:142` | |
| marquee | 30 ms | `views.py:2097` | runs while a long caption is displayed |
| heartbeat | 5 s | `core.py:6918` | full runtime-state read-modify-write |

## 9. Task lifecycle

- Cancellation is **cooperative and file-backed**: `request_job_cancel`
  writes `cancel_requested=True` into `runtime_state.json`
  (`core.py:8174`); the worker polls it at batch boundaries and around every
  model call. An in-flight HTTP request is **not** aborted.
- Pause is cooperative too (`_runtime_pause_checkpoint`, `core.py:270`).
- The worker releases its lease in a `finally` block that catches
  `BaseException` (`core.py:7941`, `:7981`) — deliberately, so a `SystemExit`
  in the worker cannot strand the lease.
- The Qt pool's tasks are not cancellable; `stop_job_and_wait`
  (`adapter.py:4034`) polls in a 0.25 s loop for up to 45 s **on a pool
  thread** (correctly off the GUI thread, but it occupies one of four pool
  threads).
- Deadlock risk: `_RUNTIME_LOCK` is a process-global lock held across a
  read-modify-write **and** an fsync; every translation batch thread
  serialises on it.

## 10. Memory

- `state.json` for the 400-paragraph fixture is several MB; the *isolated
  copy* per load doubles it transiently (pickle buffer + parsed copy).
- Peak RSS measured: 315 MB (idle) → 564 MB (1 500-paragraph document).
- The `_STATE_CACHE` in `job_repository` holds up to 256 parsed states
  (`job_repository.py:38`); each is a multi-MB dict.

---

## 11. Ranked findings (by measured impact)

| Rank | Finding | Measured impact | Evidence |
|---|---|---|---|
| 1 | Batch progress is persisted through the durable, fsync'd path | 26.8 s of CPU in a 40 s run; 1 997 whole-file rewrites | `core.py:5249`, `:7825` |
| 2 | The whole document is re-projected on the GUI thread on every poll | 2 803 ms of GUI-thread time in 40 s; up to 189 ms in one call | `window.py:1684`, `views.py:3672` |
| 3 | `delivery_preview` (whole-document QA sweep) on the GUI thread | 170 ms per call | `views.py:5164` |
| 4 | `_isolated_copy` = pickle round-trip of the whole state | 115–130 ms of GIL hold per load | `job_repository.py:201` |
| 5 | `recovery_summary` re-reads the whole checkpoint log per poll | 59–159 ms per call | `folith/checkpoint.py:127` |
| 6 | `state_identity` deep-copies the whole state; snapshot assets are re-hashed | ~100 ms + megabytes of I/O per task-list refresh | `folith/snapshots.py:54`, `delivery_state.py:503` |
| 7 | `list_jobs` on the GUI thread every 5 s | 81 ms typical, 231 ms worst | `window.py:809` |
| 8 | `_render_segment_detail` does two O(segments) scans per poll | 145 ms over 40 s | `views.py:5437`, `:5441` |
| 9 | The runtime state is read ~4× per batch | 3 781 reads / 1 509 ms | `core.py:267`, `:276`, `:7829` |
| 10 | A zero-interval timer is re-armed by every poll | GUI fairness | `views.py:2767` |
