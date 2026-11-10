# Runtime baseline 0.8.2 — A0 sampler and fixed scenarios (issue #323)

Status: **sampler merged; macOS source-mode S1 measured 2026-11-11 on a real
GUI (synthetic workload, preliminary, n=2); frozen-app macOS baseline, Windows
baseline and S2–S4 still pending.**  This page defines the numbers the A0
baseline must report, says where each number comes from, and marks the rows
nobody has measured yet.  No baseline figure is written here before it was
measured; every table row that is not measured says *pending*.

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
`folith.trace.register_gui_stall_source`; the engine stays Qt-free, and a run
with no sampler says so instead of looking like a zero-stall run:

```json
{"measured": false, "reason": "no GUI stall source was registered for this run …"}
```

### 3. Run-window fields (`gui_jank`)

```json
{
  "version": 1,
  "scope": "run_window",
  "measured": true,
  "source": "folith.qt.jank_monitor.JankMonitor",
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
  reports `interval_ms` percentiles over all pulses, but only over the retained
  sample window (`retention.interval_samples`, 20 000 pulses ≈ 8 minutes at
  40 Hz); the counters next to them are exact for the whole session.
- `top_stacks` is ranked by `total_ms` (accumulated stall time), identical
  stacks aggregated.  `count`/`total_ms` are exact for the window;
  `max_ms` is `null` when that stack also stalled before the window (a delta of
  maxima is not derivable).
- `longest.stack` is the largest stall still present in the retained event
  ring; the numeric `longest.interval_ms` is exact.
- `measured` is always present.  `false` plus a `reason` means *nothing was
  sampled* (headless run, monitor never started, or
  `FOLITH_JANK_MONITOR=0`); it must never be read as "zero stalls".  When more
  than one sampler is registered, the block carries a `sources` list with one
  sub-record per source instead of letting one overwrite another.

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
| S1 | ~10k-segment EPUB translating while the user scrolls / edits / switches pages | harness implemented (source + frozen `--selftest` path, both measured); source-mode macOS run done, frozen run pending |
| S2 | ≥300-page scanned PDF, paging + thumbnails during OCR | declared, not implemented |
| S3 | open a finished large task, switch tasks | declared, not implemented |
| S4 | 8-hour run against a local mock gateway | declared, not implemented |

`folith/qt/app.py` now starts the UI health monitor **before** the scripted
`--selftest` flow, so a selftest run (source or frozen) writes the same
`gui_jank` block and `gui_jank.json` as an interactive run, and its report
carries `gui_jank.measured`.  If a build ever runs without the monitor, the run
record says `{"measured": false, "reason": …}` instead of implying zero stalls.
The frozen `.app` run itself is still pending for the reasons above (no 0.8.2
artifact in this round, and a real provider is not available).

## Baseline report

Fill this in only from real runs.  The frozen `.app` remains the target: the
macOS numbers below come from the **source** build (same code, same Qt, real
cocoa GUI) because no frozen 0.8.2 artifact was available in this round.

### macOS 真机 — 2026-11-11（本机，source 模式 + mock 网关，合成负载）

**口径 / scope — read this before using any number below.**  These are
real-machine numbers from this Mac's cocoa QPA (`offscreen: false`), measured
with a mock model gateway (`scripts/d0_fake_llm_server.py`) and a synthetic
EPUB.  **mock 网关 + 合成负载 ≠ 真实模型服务负载**；`real_machine_baseline`
只对 **GUI 侧**（主线程事件循环、布局、绘制、状态刷新）成立，不代表真实
provider 延迟或真实文档分布。  Windows 真机待用户在 Windows 上执行；offscreen
对照组单列在下面，不能替代本表。

Machine / build: macOS 27.0.1 (26A434), Apple M4, 16 GB RAM, 10 cores,
Python 3.11.3, Qt/PySide6 6.11.2.  Sampler: 25 ms pulse, thresholds 100/500 ms,
`FOLITH_JANK_MONITOR` default on.  Data, logs and QSettings were isolated under
`/tmp/a0-macos/<label>`; no run artifact is committed to the repository.

Driver (same harness as S1 above): scroll every ~0.1 s, edit a translation every
~2 s, switch stage page every ~4 s, while translation runs against the mock
gateway.  The product rejects an edit while the task is busy
(`[qt:无法提交] 任务正在后台处理，请等待完成后保存`), so that interaction mostly
exercises the reject path; this is product behaviour, not a harness failure.

| Run | paras → segments | window (s) | effective pulse rate (Hz) | >100 ms | >500 ms | /min >100 | /min >500 | p50 (ms) | p99 (ms) | max (ms) | RSS start → peak (MB) | CPU avg / peak % | threads | settled |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| real #1 | 1000 → 2000 | 62.18 | 15.8 | 107 | 14 | 103.2 | 13.5 | 183 | 1073 | 1357 | 405 → 710 | 117.3 / 241.2 | 15 | yes (task 47.3 s) |
| real #2 | 1000 → 2000 | 61.56 | 20.8 | 87 | 11 | 84.8 | 10.7 | 215 | 747 | 874 | 422 → 788 | 112.8 / 128.7 | 15 | yes |
| real, large doc (unfinished) | 5000 → 10000 | 422.73 | 9.4 | 764 | 176 | 108.4 | 25.0 | 280 | 1345 | 2388 | 445 → 1249 | 105.4 / 130.6 | 15 | no (8168/10000 segments) |
| offscreen control | 1000 → 2000 | 60.56 | 24.0 | 72 | 5 | 71.3 | 5.0 | 232 | 638 | 795 | 345 → 735 | 110.5 / 127.2 | 14 | yes |

`window` is `gui_jank.window.observed_s` (the run-window diff, not the process
lifetime).  Effective pulse rate = `pulse_count / window`; the nominal rate is
40 Hz, so 9.4–24 Hz is itself a signal that the GUI thread rarely returned to
the event loop.  "settled" means the task finished within the settle budget;
the unfinished large-document run was cancelled by teardown and its run record
says `status: interrupted`.  That run's window also contains the 300 s settle
wait and its polling (`_pump` bucket below), so treat it as a stress case, not
as a fourth comparable S1 sample.

#### S1 rows for the baseline table

| Metric | S1, macOS real machine (2 runs, 1000 paragraphs, 60 s) | State |
| --- | --- | --- |
| p50 / p99 stall (`interval_ms`) | 183 / 1073 ms and 215 / 747 ms | measured, n=2 |
| longest single stall | 1357 ms and 874 ms (both `stack_captured: true`) | measured |
| >100 ms per minute | 103.2 and 84.8 | measured |
| >500 ms per minute | 13.5 and 10.7 | measured |
| "unresponsive" incidents (watchdog) | 0 in all four runs (no `incident.json`; max stall 1.4 s vs the 30 s gate) | measured |
| RSS peak | 710 MB and 788 MB for 2000 segments; 1249 MB for the unfinished 10 000-segment run | measured |
| RSS growth slope | task-load growth only: real #1 rose 405→710 MB while building its 2000 segments, then stayed at 710 MB for its last 20 s. The regression slope over the task window (362 / 977 / 1060 MB/min for real #1 / real #2 / offscreen) is warm-up, not a leak signal. A leak verdict needs a completed task plus a long idle window (S4) | measured, weak |
| CPU during translation | 117.3 % avg / 241.2 % peak, 112.8 % / 128.7 % | measured |
| thread peak | 15 | measured |
| Top-3 stacks | next table | measured |

Noise: two runs with identical parameters differ by 1.22× (>100/min), 1.26×
(>500/min), 1.17× (p50), 1.44× (p99), 1.55× (max).  All are inside 2×, so the
pair is reported as one range rather than a single number.  n=2 is still thin:
a committed baseline needs at least 3 runs of 5+ minutes each against a real
endpoint; the 60 s windows here are a starting range, not a final baseline.

#### Top stacks by accumulated stall time (macOS, 1000 paragraphs)

Frames are rendered outermost → innermost and truncated to the last three.

| # | Run | Frames (innermost last) | Count | Total | Max |
| --- | --- | --- | --- | --- | --- |
| 1 | real #1 | `_interact` → `QCoreApplication::processEvents()` (harness loop) | 32 | 6491 ms | 557 ms |
| 2 | real #1 | `flow_layout._preferred_width ← _width_bounds ← style.py:2001 minimumSizeHint` | 5 | 1626 ms | 570 ms |
| 3 | real #1 | `flow_layout._arrange ← _preferred_width ← _width_bounds` | 4 | 1499 ms | 438 ms |
| 1 | real #2 | `_interact` → `QCoreApplication::processEvents()` (harness loop) | 22 | 4201 ms | 443 ms |
| 2 | real #2 | `flow_layout._arrange ← _preferred_width ← _width_bounds` | 5 | 2188 ms | 561 ms |
| 3 | real #2 | `atomic_file.read ← contextlib.__enter__ ← atomic_file.shared_text_reader` | 1 | 874 ms | 874 ms |
| 1 | real, large doc | `atomic_file.read ← shared_text_reader` (state read on the GUI thread) | 104 | 45 370 ms | 1703 ms |
| 2 | real, large doc | `atomic_file.read_shared_text ← _retry_windows_read ← read` | 51 | 18 267 ms | 1264 ms |
| 3 | real, large doc | `_pump` → `QCoreApplication::processEvents()` (includes the 300 s settle wait) | 56 | 19 780 ms | 932 ms |

Reading rule for the first bucket: an innermost frame of the harness
(`_interact` / `_pump`) plus `processEvents` means the GUI thread was blocked
**inside Qt's native dispatch** — layout, paint, plugin or GC work — with no
deeper Python frame.  It is product work triggered by the harness's own loop,
not harness overhead, but it cannot name a product function; a real user's
equivalent action would enter through a widget slot.  The `flow_layout` /
`minimumSizeHint` and `atomic_file.shared_text_reader` stacks are product code
and are the actionable findings for A1: laying out flow widgets and reading job
state both happen on the GUI thread and reach 0.44–1.70 s per event.

The task's own record agrees with the scenario window: real #1's
`.performance/runs/run_*/summary.json` (task window 47.3 s, translation still
running) has 85 slow / 8 severe = 107.8/min versus the scenario window's
103.2/min, and `gui_jank.json` is present in the same run directory.

#### 真机 vs offscreen（同参数，1000 段 / 60 s）

| Metric | real #1 | real #2 | offscreen | real / offscreen |
| --- | --- | --- | --- | --- |
| >100 ms per minute | 103.2 | 84.8 | 71.3 | 1.19–1.45× |
| >500 ms per minute | 13.5 | 10.7 | 5.0 | 2.1–2.7× |
| p99 (ms) | 1073 | 747 | 638 | 1.17–1.68× |
| max (ms) | 1357 | 874 | 795 | 1.10–1.71× |
| RSS peak (MB) | 710 | 788 | 735 | ≈1.0× |
| CPU avg % | 117.3 | 112.8 | 110.5 | ≈1.05× |

Conclusion: offscreen undercounts >500 ms stalls by 2.1–2.7× and p99 by up to
1.7×, while its resource numbers are close to the real GUI.  Offscreen is
acceptable for pipeline and resource checks and is **not** a substitute for the
responsiveness baseline — exactly what the issue requires.

### Windows — pending

Same table; a Windows run must be executed on a Windows machine by the user.
Mocked, source-mode-on-macOS or offscreen numbers are not a substitute.

### Offscreen control (not a baseline)

Two offscreen runs exist and are kept only as controls: the 12-paragraph/6 s
pipeline smoke recorded in `/tmp/rb1/runtime-baseline.json`, and the
1000-paragraph/60 s control in the comparison table above.  Neither is entered
in the macOS real-machine table.

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
