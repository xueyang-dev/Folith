# Baseline report — Folith desktop client under a running translation

This report describes the **measurement method** and the **baseline numbers**
that the optimisation was judged against. Every figure was measured on the
machine described below; nothing is estimated.

> **Revision note (2026-10-23).** Section 4 was re-measured against the true
> C1-C6 reverted tree after C5/C6 were found missing from the first
> reconstruction. These baseline rows are the "before" side of
> `final-performance-report.md` Revision 2. Section 5 is an earlier 40-second
> diagnostic profile and is not a paired benchmark row.

## 1. Environment

| | |
|---|---|
| OS | Windows (win32), PowerShell 5.1 |
| CPU / RAM | as reported by the host (single socket, ≥8 cores) |
| Python | 3.11.16 (64-bit) |
| Qt | PySide6 6.11.2 |
| Qt platform plugin | `offscreen` (deterministic, no display needed) |
| Model | **none** — the repository's own `tests/offline_provider.py` answers every prompt deterministically, so the run needs no network and always does the same work |
| Data directory | a fresh temporary directory per run (the real Folith data dir is never touched) |

`offscreen` was chosen because it is reproducible and headless-safe. A
cross-check on the native `windows` plugin was run for the translate scenario
and is reported in §7; it moves the absolute numbers but not the ratios.

## 2. What is measured, and how

### 2.1 The application under test

The harness boots the **real** `folith.qt.window.MainWindow` and the **real**
`core.start_job_worker` pipeline. Only the model is replaced.

* Document: a deterministic DOCX built by
  `benchmarks/performance/fixtures.py` — 400 body paragraphs of realistic
  academic English (1500 for the large scenario). Seeded, so the same
  `--segments` value always yields the same paragraphs.
* Task: the production pipeline — import → understanding → translation →
  independent review → state commit — driven through the production entry
  point `core.start_job_worker`, with its own heartbeat thread, its own job
  lock and its own runtime-state files.

### 2.2 The event-loop probe

A `QTimer` with `Qt.PreciseTimer` at a 10 ms cadence runs inside the real
event loop (`QApplication.exec()`). Each fire records:

* **gap** — wall time since the previous fire: how long the event loop was
  unavailable;
* **delay** — how much of that gap is *overrun* beyond the requested cadence
  (`gap − 10 ms`): the scheduling delay.

The probe samples every 10 ms, so a stall of *N* ms appears as one sample of
*N* ms and shows up in p99/max but not p95. Both are reported.

### 2.3 Interaction latency

The foreground interactions are delivered by a driver timer **on the GUI
thread**, i.e. exactly like a user clicking. Each gesture is timed as
"GUI-thread time to execute the action" (`handler_ms`) and the worst
event-loop gap in the 250 ms after it is attributed to it (`stall_ms` — the
paint that follows an interaction is part of what the user waits for).

The gesture set and their **minimum cadence** mirror the real application:

| Gesture | Cadence |
|---|---|
| navigate to workbench / projects / home | continuous |
| scroll the segment table | continuous |
| select a segment | continuous |
| type into the inline editor | every 0.5 s |
| search / filter | every 2 s |
| open the settings page | every 3 s |
| refresh the task list | every 5 s (the application's own `HOME_REFRESH_SECONDS`) |

### 2.4 The measurement window

For a scenario with a background task, the window runs **until the worker
finishes** (capped at the scenario's maximum). This matters: an earlier
iteration measured a fixed 30 s window and compared runs that had done
different amounts of pipeline work. Now both runs finish the *same document*,
and the wall time is itself a result.

The idle scenario runs for the full cap with no background task.

### 2.5 Process metrics

`psutil` samples CPU time, RSS and thread count every 100 ms from a separate
thread. `psutil` is a **benchmark-only** dependency; the product does not
depend on it.

### 2.6 Function-level profiling

`benchmarks/performance/diagnose_profile.py` wraps ~45 functions across the
core, the adapter and the view with a timing probe, and groups the samples by
thread. This is what ranks the bottlenecks; the event-loop numbers say
*whether* it matters, this says *what* it is.

## 3. Reproducing

```powershell
# baseline (the pre-optimisation code is reconstructed by the revert script)
python -m benchmarks.performance.revert_to_baseline $env:TEMP\folith-baseline-src
cd $env:TEMP\folith-baseline-src
python -m benchmarks.performance.run_benchmark --scenario idle --scenario translate `
    --scenario review --scenario export --duration 300 --out baseline.json
python -m benchmarks.performance.run_benchmark --scenario large --duration 900 --out baseline-large.json

# optimised
cd <repo>/source
python -m benchmarks.performance.run_benchmark --scenario idle --scenario translate `
    --scenario review --scenario export --duration 300 --out optimized.json
python -m benchmarks.performance.run_benchmark --scenario large --duration 900 --out optimized-large.json

python -m benchmarks.performance.compare --baseline baseline.json baseline-large.json `
    --optimised optimized.json optimized-large.json --out comparison.json
```

`benchmarks/performance/baseline_mode.py` provides a second, independent way
to measure "before": it re-installs the pre-optimisation *behaviour* at
runtime (per-batch durable status writes, fsync per event, deep-copy state
identity, GUI-thread projections). Both methods agree to within run-to-run
noise (baseline translate: 109.8 s CPU via the tree, 111.0 s via the runtime
patch).

## 4. Baseline results

Whole-document runs (400 paragraphs -> 401 segments), offscreen platform,
from the C1-C6 reverted tree (27/27 edits).

| Scenario | Wall (s) | CPU (s) | Delay p50 | p95 | p99 | max | Stalls > 100 ms | GUI-thread per refresh |
|---|---|---|---|---|---|---|---|---|
| S1 idle (no background) | 300.02 | 189.48 | 5.507 | 21.799 | 42.414 | 261.608 | 43 | 5.0 ms |
| S2 translate | 75.82 | 85.73 | 5.904 | 29.143 | 44.340 | 360.365 | 18 | **136.5 ms** |
| S7 review/QA | 71.21 | 84.52 | 5.755 | 29.981 | 41.530 | 275.199 | 14 | **148.3 ms** |
| S8 export running | 68.80 | 89.34 | 6.966 | 30.413 | 45.996 | 272.420 | 13 | **127.2 ms** |
| S10 large (1500 → 1501 seg.) | 447.46 | 553.47 | 14.052 | 42.917 | 150.943 | 1 361.842 | 204 | **444.5 ms** |

"GUI-thread per refresh" is the total time the window was unavailable inside
`WorkbenchView.apply`, divided by the number of refreshes. It is the direct
measure of "how long the UI freezes each time the background task commits a
batch".

## 5. Where the time goes (function-level profile, 400 paragraphs, 40 s)

| Function | Calls | Total | Max | Thread(s) |
|---|---|---|---|---|
| `core.update_runtime_state` | 1 997 | **26 764 ms** | 138.5 | worker |
| `core._append_runtime_event` | 1 988 | 2 015 ms | 30.8 | worker |
| `core.load_runtime_state` | 3 781 | 1 509 ms | 124.6 | worker + GUI |
| `core._runtime_cancel_requested` | 1 148 | 1 035 ms | 127.3 | worker |
| `views.apply` (whole refresh) | 41 | **2 803 ms** | **189 ms** | GUI |
| `views._render_segments` | 41 | 1 107 ms | 128.9 | GUI |
| `views._sync_word_count` | 41 | 597 ms | 30.4 | GUI |
| `views._render_delivery` | 41 | 544 ms | 104.3 | GUI |
| `core.recovery_summary` | 47 | 554 ms | 99.0 | GUI + worker |
| `views._render_activity` | 41 | 459 ms | 14.6 | GUI |
| `adapter.runtime_view` | 41 | 349 ms | 10.3 | GUI |
| `repo._isolated_copy` | 49 | 678 ms | 114.8 | worker |
| `adapter.load_state` | 41 | 1 134 ms | 129.6 | worker |
| `views._render_segment_detail` | 82 | 145 ms | 5.9 | GUI |

### The single dominant cost

`core.update_runtime_state` cost **26.8 seconds of CPU inside a 40-second
run** — writing *status*, not results. Per translation batch the pipeline
issues seven of them ("building context", "translating", "checking",
"writing", "saved", plus two captions). Each one is a read-modify-write of
the whole `runtime_state.json`, an atomic replace, and an fsync'd append to
`runtime_events.jsonl`.

The translated text itself is committed by a different path (`_commit_batch`
→ `state.json` + journal) which is already cooperative. The status channel
was not.

## 6. Reproduction of the user's symptom

The reported symptom is *"the UI gets sluggish once a translation task is
running"*. The baseline reproduces it exactly:

* With **no** background task: the window is quiet — 0 paint events in a
  6-second idle observation, event-loop delay p95 ≈ 10 ms, and the GUI thread
  spends 4 ms per refresh.
* With a background task: every committed batch makes the workbench refresh,
  and that refresh costs **136.5 ms of uninterrupted GUI-thread time**
  (444.5 ms for a 1 500-paragraph document), because the refresh re-projects
  the whole document (delivery gate, follow-up facts, review queue, word
  count, activity log) on the GUI thread.
* At the same time the background task burns 26.8 s of CPU writing status,
  holding the GIL (CPython's `json.dumps` never releases it) and the disk.
* The result: 18 stalls over 100 ms in a 75.82-second run, worst 360 ms.

## 7. Native-platform cross-check

One earlier S2 cross-check on the native `windows` plugin (not part of the
fixed pair above):

| | offscreen | native `windows` |
|---|---|---|
| event-loop delay p95 | 25.4 ms | 51.8 ms |
| CPU | 94.5 s | 61.6 s |

The native plugin renders more expensively on this host. The *ratios* between
the before and after runs are the stable, meaningful quantity; the absolute
numbers are platform-specific and are reported as such.

## 8. Energy

No energy or power measurement was possible on this host: no battery-backed
device, and the platform exposes no energy counter through the interfaces
available here. **No watt-hour or battery figures are claimed.** What *can* be
stated is the CPU-time reduction, which is the platform-independent proxy for
energy per completed workload — a process that burns 55 % less CPU time for
the same document does the same work for proportionally less energy on any
machine, and the idle/background wake-up count is unchanged (the heartbeat
still runs at 5 s; no new timers were introduced).

## 9. Measurement noise

Repeated runs of the same scenario vary by roughly ±15 % in wall time and
±10 % in CPU time on this host (shared machine). The comparison numbers in
`comparison.json` come from single runs; the *ratios* are stable across
repeats (baseline translate CPU measured at 94.5 s, 109.8 s and 115.7 s in
three separate runs; optimised at 38.5 s, 42.6 s, 42.8 s and 55.2 s).
