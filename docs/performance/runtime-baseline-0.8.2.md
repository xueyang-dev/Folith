# Runtime baseline 0.8.2 — A0 sampler and fixed scenarios (issue #323)

Status: **sampler merged, S1 harness skeleton implemented, real-machine baselines
still to be run.**  This page defines the numbers the A0 baseline must report,
says where each number comes from, and marks the rows nobody has measured yet.
No baseline figure is written here before it was measured; every table row that
is not measured says *pending*.

## What already existed

- `folith/desktop/ui_watchdog.py`: out-of-process heartbeat gate, 30 s timeout.
  It can only answer "does the GUI answer at all?" and it reports one incident,
  not a 100 ms-level distribution.
- `folith/qt/ui_health.py`: the GUI-side pulse producer for that watchdog.
- `folith/trace.py::ResourceSampler`: RSS / CPU% / thread count / GPU probe at
  1.5 s, written to `resources.jsonl` inside the task's performance run and
  summarised in `summary.json -> resources`.  Nothing was added here: the
  sampler was already wired into the run record.
- `folith/qt/selftest.py`: the scripted acceptance flow already measures a
  max pulse gap, but keeps no stacks, percentiles or per-scenario history.

## What this change adds

### 1. Main-thread stall sampler (`folith/qt/jank_monitor.py`)

- A `QTimer` pulses on the GUI thread every **25 ms**
  (`JankMonitor.pulse_interval_ms`).  The measured quantity is the gap between
  two pulses, `interval_ms`.
- Thresholds: `interval_ms >= 100` is one slow stall, `interval_ms >= 500` is a
  severe stall.  A severe stall also counts as a slow one.
- Because one timer period is part of the measured interval, `interval_ms`
  overstates the blocked time by at most one period plus scheduler jitter.
  Every event also carries `blocked_ms = max(0, interval_ms - 25)`, so a report
  can use either number; the issue's "每 100 ms" wording is the `interval_ms`
  one and the report says so.
- When a stall is recorded, a helper thread has already captured the GUI
  thread's Python stack with `sys._current_frames()`.  The helper only reads
  frames while the GUI has been silent for two pulse periods, so healthy idling
  costs nothing.  The GUI thread itself never renders a traceback.
- A stall inside a C extension that holds the GIL cannot be captured (the
  helper cannot run): such events are still counted, get
  `stack_captured: false` and an empty `stack`.  They remain visible as a
  native `python-stacks.log` dump from the watchdog's faulthandler.
- A captured stack whose innermost frame is only the module frame means the GUI
  thread was inside the Qt event loop with no Python frame below it — i.e. the
  blocking work was native (paint, layout, a C++ call).  This is recorded as a
  captured stack on purpose.
- Off switch: `FOLITH_JANK_MONITOR=0` (also `false|no|off|disabled`).  Default
  is on.  There is no settings UI yet; the environment variable is the contract
  for scripted runs.
- Retention is bounded: Top-N stacks by accumulated stall time (200), a recent
  event ring (50), and fixed-size sample windows (20 000 intervals / 20 000
  stall durations) for percentiles.  Counters are exact and never trimmed.

### 2. Where the sampler writes

The sampler is process-wide (one per GUI), but every task performance record
gets a **run-scoped window**:

| Location | Scope | Content |
| --- | --- | --- |
| `<output_dir>/.performance/runs/<run_id>/gui_jank.json` | one task run | the full run-window block below |
| `.../summary.json -> gui_jank` | one task run | same block, embedded |
| `.../resources.jsonl` | one task run | 1.5 s RSS/CPU%/thread/GPU samples (pre-existing) |
| `.../summary.json -> resources` | one task run | RSS peak, CPU avg/peak, thread peak (pre-existing) |
| `logs/ui-health/session-*/incident.json` | app session | watchdog "unresponsive" evidence (pre-existing) |
| `--out/runtime-baseline.json` | one scenario run | harness report: window + run records + limits |

The window is a diff against a checkpoint taken when `RunContext` is created,
so a stall from an earlier task in the same app session is not attributed to a
later one.  Wiring: `folith.qt.ui_health` registers the sampler with
`folith.trace.register_gui_stall_source`; the engine stays Qt-free and headless
runs simply get `"gui_jank": {}`.

### 3. Run-window fields (`gui_jank`)

```json
{
  "version": 1,
  "scope": "run_window",
  "enabled": true,
  "window": {"started_at": "…", "observed_s": 120.4},
  "pulse_interval_ms": 25.0,
  "thresholds_ms": {"slow": 100.0, "severe": 500.0},
  "pulse_count": 4812,
  "slow_count": 37,
  "severe_count": 2,
  "slow_per_minute": 18.44,
  "severe_per_minute": 1.0,
  "stall_ms": {"count": 37, "p50": 132.0, "p90": 210.0, "p95": 300.0,
                "p99": 604.0, "max": 604.0, "avg": 168.0, "min": 104.0},
  "longest": {"at": "…", "interval_ms": 604.0, "blocked_ms": 579.0,
               "severity": "severe", "stack_captured": true,
               "stack": ["…/views.py:1645 in _apply_filter", "…"]},
  "top_stacks": [{"stack": ["…"], "count": 12, "total_ms": 2100.0,
                   "max_ms": 340.0}],
  "notes": ["…"]
}
```

Reading rules:

- `slow_count` / `severe_count` answer "每分钟 >100 ms / >500 ms 次数" once
  divided by `window.observed_s`; `*_per_minute` already does that (values from
  windows shorter than a minute are noisy extrapolations and are labelled by
  `observed_s`).
- `stall_ms` percentiles are over recorded stalls only (p50 of a healthy run is
  the median stall, not the median ping).  `JankMonitor.summary()` additionally
  reports `interval_ms` percentiles over **all** pulses for the whole session.
- `top_stacks` is ranked by `total_ms` (accumulated stall time), identical
  stacks aggregated.  `count`/`total_ms` are exact for the window;
  `max_ms` is `null` when that stack also stalled before the window (a delta of
  maxima is not derivable).
- `longest.stack` is the largest stall still present in the retained event
  ring; the numeric `longest.interval_ms` is exact.

The process-lifetime view (`JankMonitor.summary()`, also embedded by the
harness as `metrics.gui_jank_process`) adds `scope`, all-pulse percentiles,
`recent` events and `stack_capture` counters
(`attempts`, `succeeded`, `gui_thread_ident`, `gui_thread_is_main`).

### 4. Fixed-scenario harness (`scripts/runtime_baseline_runner.py`)

```bash
# S1 skeleton, offscreen, small: proves the pipeline, not a baseline
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/runtime_baseline_runner.py \
    --out /tmp/rb-s1 --paras 200 --seconds 90 --offscreen

# S1 for a real baseline: frozen app, large EPUB, 30 minutes of interaction
venv/bin/python scripts/runtime_baseline_runner.py --out /tmp/rb-mac-s1 \
    --paras 10000 --seconds 1800 --mode frozen \
    --frozen-app dist/qt-desktop/Folith.app/Contents/MacOS/Folith
```

S1 imports the deterministic EPUB, starts translation against a local mock
gateway, then scrolls the paragraph table, edits translations and switches stage
pages while translation runs, waits for the task to settle, and writes one JSON
report.  The report sets `real_machine_baseline: false` and lists its
`limits`; offscreen `QPA` has no compositor, no GPU paint path and no native
input, so it can never stand in for the frozen-app run.

Scenario status:

| Scenario | Meaning | State |
| --- | --- | --- |
| S1 | ~10k-segment EPUB translating while the user scrolls / edits / switches pages | harness implemented (source + frozen `--selftest` path); real-machine run pending |
| S2 | ≥300-page scanned PDF, paging + thumbnails during OCR | declared, not implemented |
| S3 | open a finished large task, switch tasks | declared, not implemented |
| S4 | 8-hour run against a local mock gateway | declared, not implemented |

The frozen `--selftest` path does not start the UI-health monitor (that branch
of `folith/qt/app.py` returns before it), so it currently yields the run record's
`resources` but no stall window.  That is a known gap for the real-machine
round, not a measurement.

## Baseline report

Fill this in only from real runs.  Two machines, frozen build, same fixture and
same `--paras/--seconds`.

### macOS — pending

| Metric | S1 | S2 | S3 | S4 |
| --- | --- | --- | --- | --- |
| p50 / p99 stall (`interval_ms`) | pending | pending | pending | pending |
| longest single stall | pending | pending | pending | pending |
| >100 ms per minute | pending | pending | pending | pending |
| >500 ms per minute | pending | pending | pending | pending |
| "unresponsive" incidents (watchdog) | pending | pending | pending | pending |
| RSS peak / growth slope | pending | pending | pending | pending |
| idle / during-translation CPU% | pending | pending | pending | pending |
| Top-3 stacks | pending | pending | pending | pending |

### Windows — pending

Same table; a Windows run must be executed on a Windows machine by the user.
Mocked or offscreen numbers are not a substitute.

### Local offscreen smoke (not a baseline)

The S1 harness has been executed once offscreen on the development machine with
`--paras 12 --seconds 6` to prove the pipeline (translation settled, run record
and stall window present).  The values are recorded in
`/tmp/rb1/runtime-baseline.json` for that run only and are **not** entered in the
table above: offscreen + 12 segments + mock gateway measures the harness, not
the product.

## Verifying the sampler itself

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/jank_monitor_test.py -q
QT_QPA_PLATFORM=offscreen venv/bin/python -m pytest tests/performance_trace_test.py \
    tests/ui_watchdog_test.py -q
```

`tests/jank_monitor_test.py` covers the threshold boundaries (99/100/101 ms),
severity counting, the off switch, injected real 150 ms / 600 ms blocking calls
recorded with their own stack, percentile and Top-N aggregation, bounded
retention, the run-window delta, the run-record wiring, and a real Qt offscreen
pulse-timer run.
