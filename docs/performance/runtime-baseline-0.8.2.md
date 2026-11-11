# Runtime baseline 0.8.2 — A0 sampler and fixed scenarios (issue #323)

Status: **sampler merged; S1 harness measurement v2 implemented (the harness no
longer pumps the GUI thread) and macOS diagnostic runs recorded with it.  No
gate baseline exists yet: S2–S4, the frozen `8109b9c2` baseline build (with the
v2 probe back-ported) and Windows acceptance are still pending.  Every number in
this file is diagnostic data, not a gate denominator.**

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
  "instrumentation_slow_count": 3,
  "instrumentation_severe_count": 0,
  "instrumentation_total_ms": 940.0,
  "instrumentation_stall_ms": {"count": 3, "p50": 250.0, "max": 420.0},
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
- `longest` is the largest **non-instrumentation** stall visible to the view.
  The process view (`summary()`) keeps an exact running maximum; the run window
  takes the maximum over the retained event ring
  (`retention.recent_events`, 50 events), so a window whose product stalls
  overflow that ring reports the largest *retained* product stall.  In both
  scopes `longest.instrumentation` is always `false`: a harness stall is never
  eligible, even when it is the largest event in the ring.
- `instrumentation_*` count stalls caused by the measuring harness itself
  (predicate polling such as job listing / runtime-state reads).  They are
  **never** part of `slow_count`, `severe_count`, `stall_ms`, `longest` or
  `top_stacks`; a stall is attributed to the instrumentation when an
  instrumentation window overlapped the interval it covers.
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

# S1 acceptance run on the candidate build: frozen app, large EPUB, 30 minutes
# of interaction
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

**Measurement v2 (#327):** every step is a `QTimer` callback inside the
application's own `app.exec()`; the harness never calls `processEvents()`, and
the only remaining harness work (predicate polling) is marked
`instrumentation` and excluded from the product numbers.  The harness no longer
appears as a stall bucket.  See the macOS diagnostic section for the numbers
and for why the superseded v1 numbers were lower (its pump chopped the loop).

Scenario status:

| Scenario | Meaning | State |
| --- | --- | --- |
| S1 | ~10k-segment EPUB translating while the user scrolls / edits / switches pages | harness v2 (event-loop driven, instrumentation split); macOS v2 diagnostic runs recorded (60 s, mock, source build); frozen run and Windows pending |
| S2 | ≥300-page scanned PDF, paging + thumbnails during OCR | declared, not implemented |
| S3 | open a finished large task, switch tasks | declared, not implemented |
| S4 | 8-hour run against a local mock gateway | declared, not implemented |

`folith/qt/app.py` now starts the UI health monitor **before** the scripted
`--selftest` flow, so a selftest run (source or frozen) writes the same
`gui_jank` block and `gui_jank.json` as an interactive run, and its report
carries `gui_jank.measured`.  If a build ever runs without the monitor, the run
record says `{"measured": false, "reason": …}` instead of implying zero stalls.
The frozen `.app` run itself is still pending: no 0.8.2 artifact was available
in this round, and the real-endpoint reference used the user's local gateway,
not a remote provider over the WAN.

## Release gate (#327 ruling)

#327 keeps a **relative** gate and keeps the absolute responsiveness numbers
**as targets**.  Both are reported here; neither one replaces the other.

1. **Relative gate (pass/fail).**  The `>100 ms` rate and the `>500 ms` rate
   must each drop by **≥ 70 %** against the effective baseline.  Both tiers
   count, both platforms count.  Decision D1 of the 0.8.2 plan splits each rate
   into R ("during translation", the task's own window) and I ("after
   completion", a fixed finished large task) because one whole-window number
   hides movements in opposite directions.
2. **No gate denominator has been measured yet.**  Per D1 and the #327 text, the
   baseline is the frozen `8109b9c2` build (code from before A1 and A3) with the
   v2 probe back-ported, at least 3 rounds of at least 5 minutes each on the
   replay gateway (a mock gateway replaying a recorded real latency
   distribution), median of the rounds.  Measuring on A1/A3-era code, as the
   numbers in this file were, would make the candidate beat A1 *on top of* A1,
   which is not what the ruling asks for.  This baseline has not been built or
   measured in this round; nothing in this file is it.
3. **Absolute targets are retained, with actual values stated next to them.**
   `p99 < 100 ms` (over all pulse intervals) and longest single stall
   `< 500 ms` remain the absolute targets.  They are **not** superseded and
   must not be suppressed; the honest reading is that today's numbers are far
   above them:
   - all-pulse `p99`: **771 / 824 / 802 ms** in the three diagnostic v2 runs
     (7.7–8.2× the 100 ms target);
   - longest single stall: **2648 / 2905 / 2774 ms** (5.3–5.8× the 500 ms
     target);
   - the superseded v1 control was over as well (all-pulse `p99` 440.5 ms,
     longest 1572.7 ms).

   The relative gate measures progress against a baseline; it does not make the
   absolute target met, moot, or unquotable.
4. **Measurement first.**  These numbers are only comparable once the measuring
   harness stopped pumping the GUI thread (measurement v2 below).  Gate results
   measured with the v1 harness are not accepted.
5. **Hard gates (unchanged).**  Zero "unresponsive" incidents; an 8-hour run
   with zero crashes and no sustained RSS growth; 30 freeze-pressure rounds on
   the frozen build with zero crashes.
6. **Dual-platform real-machine acceptance is still outstanding** (macOS source
   build measured here; Windows belongs to the user's real machine).

## Measurement reports (diagnostic data)

Numbers are entered here only after they were measured, and every number below
is **diagnostic data**: short (60 s) runs on the **source** build at
`bc765adc` — which already contains A1 P0–P2 and A3, so it is not "before the
change" — against a mock gateway that replies instantly.  None of these sections
is a gate baseline, none is a denominator for the ≥ 70 % target, and the
real-endpoint runs are a separate reference that never feeds a gate.  The gate
denominator is defined in the release-gate section above and has not been
measured.

Reading rule used by every table in this file: `p50` / `p99` / `max` are
percentiles of the **recorded stall events' `interval_ms`** (events ≥ 100 ms),
not of all pulse intervals.  A p50 of 238 ms therefore means "the median stall
lasted 238 ms"; the median *pulse* of a healthy 25 ms timer is tens of
milliseconds.  All-pulse percentiles are a different distribution and live in
`metrics.gui_jank_process.interval_ms`; they are quoted only where the text says
so — the absolute-target `p99 < 100 ms` is an all-pulse number.

### macOS — measurement v2: diagnostic runs (可控负载, source build)

**Diagnostic data — not a baseline and not a gate denominator.**  The gate
denominator is the frozen `8109b9c2` build with the v2 probe back-ported and
≥ 3 × 5 min replay-gateway rounds (release gate above); nothing in this section
qualifies, and calling these numbers a baseline is the exact misreading this
section exists to prevent.

Conditions, stated so that they cannot be mistaken for a gate run: **source
build at `bc765adc`**, deterministic 1000-paragraph / 2000-segment EPUB fixture,
**60 s window per run, 3 runs tabulated** (seven completed runs of the same
series exist — see the sampling note under the table), mock model gateway with
instant replies, macOS cocoa QPA.  This is a **short diagnostic measurement**:
across the **seven** completed runs with identical parameters the stall p99
spans 1198.4–1967.3 ms — **1.64×** — and the longest stall spans
2451.3–3254.3 ms.  That run-to-run noise is wider than the 1.55× max-stall
spread of the two v1 real-endpoint runs quoted further down (p99 there:
1.44×), and it is far wider than any effect three 60 s rounds could attribute:
with a 1.64× spread between identical runs, this setup cannot resolve an effect
of that size, let alone a smaller one.  (Inside the three tabulated runs the
tail happens to differ by only 1.1–1.4×; that narrower figure holds **only for
those three** and is not the series' noise bound.)  So only very large effects
would be resolvable, and the mock load measures GIL saturation rather than user
experience (0.8.2 plan §6.3).  It is kept because it is reproducible and shows
where the tail comes from — **not** because it can serve as a gate comparison.

Measurement: `measurement: v2` — the scenario runs inside the application's own
event loop (`app.exec()`, every step a `QTimer` callback) instead of the v1
harness pumping `QApplication.processEvents()` from script code.  The only
remaining harness work (predicate polling such as job listing / runtime state
reads) is marked `instrumentation` and counted in `instrumentation_slow_count` /
`instrumentation_total_ms`, never in `slow_count`, `stall_ms`, `longest` or
`top_stacks`.  Same machine and same parameters as the v1 control below; no
commit was recorded for that v1 control, so an identical build cannot be
confirmed (see the comparison below).

| Run | segments | window (s) | pulses | >100 ms | >500 ms | stall p50 (ms) | stall p99 (ms) | max stall (ms) | instrumentation stalls / ms | RSS peak (MB) | CPU avg / peak % | threads | settled |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| v2 #1 | 2000 | 61.758 | 966 | 94 (91.32/min) | 19 (18.46/min) | 237.5035 | 1273.0593 | 2648.4925 | 1 / 304.1 | 825.83 | 113.7 / 185.5 | 16 | yes |
| v2 #2 | 2000 | 62.321 | 941 | 86 (82.8/min) | 24 (23.11/min) | 232.9181 | 1752.7818 | 2904.5755 | 2 / 494.2 | 789.14 | 119.5 / 282.4 | 16 | yes |
| v2 #3 | 2000 | 61.916 | 895 | 91 (88.18/min) | 26 (25.2/min) | 259.1497 | 1696.9372 | 2773.7492 | 0 / 0.0 | 789.7 | 118.1 / 213.6 | 15 | yes |
| v2 offscreen control | 2000 | 61.149 | 930 | 90 (88.31/min) | 25 (24.53/min) | 247.2909 | 1783.1414 | 1834.6595 | 1 / 457.4 | 721.75 | 110.3 / 138.1 | 16 | yes |

`p50` / `p99` / `max` in this table are stall-event percentiles (definition
above), not all-pulse percentiles.  The three tabulated runs are `after-4`,
`after-5` and `after-6` — three of the seven completed mock-mode runs in the
development series under `/private/tmp/a0-m2` (`after-1` … `after-7`; the
offscreen control is `off-v2`; `after-8/` left no report, so it is not one of
the seven and is not counted).  **The original reports record no selection
rule, and none is reconstructed here:** these three are documented as *the runs
that were tabulated*, not as a representative, best-case or worst-case sample.
They are not the group's worst case — untabulated `after-1` has the group's
worst single stall (**3254.3 ms**, vs 2648.5 / 2904.6 / 2773.7 ms here) and
untabulated `after-2` the worst stall p99 (**1967.3 ms**, vs 1273.1 / 1752.8 /
1696.9 ms here).  Both numbers exist in completed reports, so **this table does
not represent the worst case of the seven runs**; the group range is p99
1198.4–1967.3 ms (1.64×) and longest stall 2451.3–3254.3 ms.  Those reports
predate the `measurement` marker — see How to re-measure.

**Diagnostic summary (v2 driver, mock gateway) — these are NOT the numbers the
≥ 70 % target uses:**

- `>100 ms` rate: **91.3, 82.8, 88.2 per minute** (3 runs)
- `>500 ms` rate: **18.5, 23.1, 25.2 per minute**
- median stall / stall p99 / longest stall: 238–259 / 1273, 1753, 1697 /
  2648, 2905, 2774 ms
- all-pulse `p99` (the absolute target's metric): **771, 824, 802 ms**
- zero "unresponsive" incidents in every run (watchdog gate never fired)

#### v1 (harness pumped the GUI) vs v2 — same machine, 1000 paragraphs, 60 s, mock

| Metric | v1 (`before-1`) | v2 (3 runs) | Reading |
| --- | --- | --- | --- |
| `>100 ms` per minute | 80.61 | 91.3, 82.8, 88.2 | roughly level |
| `>500 ms` per minute | 10.68 | 18.5, 23.1, 25.2 | **v2 higher** |
| stall p99 (ms) | 1246.2451 | 1273, 1753, 1697 | **v2 higher** |
| max stall (ms) | 1572.6792 | 2648, 2905, 2774 | **v2 higher** |
| #1 aggregated bucket | 16 events / 2944 ms / max 437 ms — `_interact → QApplication.processEvents()` (the harness itself) | `_run_scenario_event_loop` — native Qt dispatch, **no pump**; product frames own the rest | harness bucket removed |

**Why v2 is worse — and why that is not a regression claim.**  v1 called
`processEvents()` roughly every 10 ms, so the event loop returned to the harness
between batches of product work; long bursts were split across several pulse
intervals and the measured tail came out **shorter than what the shipping app
actually experiences** (`folith/qt/app.py` also runs `app.exec()`).  v2 measures
the real loop, so the tail grows and the harness bucket disappears.  The two
tables are **not** a controlled before/after pair: no commit was recorded for
the v1 control's measurement, so "no product code changed between the two
tables" **cannot be verified** and is withdrawn.  What is verifiable is the
driver change above and its effect on the numbers.  Anyone comparing a v1 number
with a v2 number is comparing two different measurements, not two product
states — this is why the v1 table below is marked superseded.

#### v2 Top stacks (mock workload, by accumulated stall time)

| # | Run | Count | Total (ms) | Max (ms) | Frames (innermost last, truncated) |
| --- | --- | --- | --- | --- | --- |
| 1 | v2 #1 | 4 | 5494.0 | 3254.3 | contextlib.py:137 in __enter__ ← atomic_file.py:96 in shared_text_reader |
| 2 | v2 #1 | 20 | 5741.0 | 749.3 | runtime_baseline_runner.py:629 in main ← runtime_baseline_runner.py:458 in run_source ← runtime_baseline_runner.py:398 in _run_scenario_event_loop |
| 3 | v2 #1 | 2 | 1807.0 | 1047.4 | script_leak.py:139 in script_counts ← script_leak.py:127 in script_of |
| 1 | v2 #2 | 2 | 3661.5 | 2904.6 | atomic_file.py:169 in read ← contextlib.py:137 in __enter__ ← atomic_file.py:96 in shared_text_reader |
| 2 | v2 #2 | 13 | 2620.5 | 514.6 | runtime_baseline_runner.py:629 in main ← runtime_baseline_runner.py:458 in run_source ← runtime_baseline_runner.py:398 in _run_scenario_event_loop |
| 3 | v2 #2 | 3 | 2191.0 | 1175.8 | views.py:6668 in _render_segment_detail ← segment_assist.py:96 in set_items ← segment_assist.py:165 in _add_finding |
| 1 | v2 #3 | 21 | 5590.4 | 953.8 | runtime_baseline_runner.py:629 in main ← runtime_baseline_runner.py:458 in run_source ← runtime_baseline_runner.py:398 in _run_scenario_event_loop |
| 2 | v2 #3 | 1 | 2773.7 | 2773.7 | atomic_file.py:169 in read ← contextlib.py:137 in __enter__ ← atomic_file.py:96 in shared_text_reader |
| 3 | v2 #3 | 1 | 1696.9 | 1696.9 | atomic_file.py:169 in read ← contextlib.py:137 in __enter__ ← atomic_file.py:96 in shared_text_reader |

`_run_scenario_event_loop` is the application's own event loop: a stall captured
while Qt dispatches natively (paint/layout/plugin work, no Python frame below)
shows that frame.  It is **not** the old pump; `test_measurement_path_never_calls_process_events`
enforces that the measurement path contains no `processEvents` call at all.
The remaining product hotspots are the real A1 candidates:
`atomic_file.shared_text_reader` (state read on the GUI thread, up to
2.9 s in a single event), `segment_assist`, `window._poll_workbench_full`, and
`flow_layout` sizing.

### 真实模型端点对照（真实 API；仅作参考，不参与门槛判定）

Same code, same machine, same 1000-paragraph document and UI scale, but
translation runs against the user's configured real endpoint
(`Local Gateway` / `auto/deepseek-v4.1-flash`, host
`127.0.0.1:3425`, review on).  The API key was read in
memory from the provider config and never written into any output
(`api_key_loaded: true`); the 2-minute window is **live translation, not a
finished task**.

| Run | segments | window (s) | pulses | >100 ms | >500 ms | stall p50 (ms) | stall p99 (ms) | max stall (ms) | instrumentation stalls / ms | RSS peak (MB) | CPU avg / peak % | threads | settled |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| real API #1 | 24 | 141.533 | 5456 | 15 (6.36/min) | 1 (0.42/min) | 133.9134 | 743.4853 | 743.4853 | 0 / 0.0 | 532.48 | 19.5 / 110.5 | 18 | no — stopped mid-task |
| real API #2 | 20 | 141.472 | 5394 | 18 (7.63/min) | 2 (0.85/min) | 120.1927 | 721.315 | 721.315 | 0 / 0.0 | 491.56 | 17.6 / 128.8 | 17 | no — stopped mid-task |

- `>100 ms`: **6.4, 7.6 per minute**; `>500 ms`: **0.4, 0.8 per minute**;
  stall p99 743, 721 ms, longest 743, 721 ms (n=2).  All-pulse `p99` here is
  56.7 / 66.2 ms — under the 100 ms absolute target only because the GUI is
  idle waiting for the network, so it is not comparable with the mock-load rows
  above and is not evidence that the target is met.
- **Why it is not a gate input:** with real model latency the GUI spends
  most of its time waiting for the network, so the stall rate is an order of
  magnitude lower (6.4, 7.6 vs 91.3, 82.8, 88.2 per minute for the mock
  workload).  That is a different scenario, not a better product.
- **Why it is not a finished S1 run:** in 140 s the real endpoint translated
  24 / 20 of the 2000 segments.  A full 1000-paragraph real-endpoint run would
  take hours, so it cannot be a repeatable gate input.  It is kept here as the
  honest "real environment" reference and as the reason the gate uses a
  controlled workload.
- Real-endpoint top stacks show the same product code (event loop/native
  dispatch, `flow_layout` sizing, paint events) and no harness pump; one run
  also shows a lazy import on the GUI thread
  (`<frozen importlib._bootstrap_external> → get_code`, 743 ms in one event).
- Because the GUI is mostly idle here, the resource numbers are lower too
  (RSS peak 532 / 492 MB, CPU avg 19.5 / 17.6 %).

### How to re-measure (do not run as part of the gate PR)

```bash
# Short diagnostic run (1000 paragraphs, 60 s, mock gateway) — NOT a gate
# measurement and NOT the denominator the ≥ 70 % target uses.
QT_QPA_PLATFORM=offscreen venv/bin/python scripts/runtime_baseline_runner.py \
    --out /tmp/rb-v2 --paras 1000 --seconds 60 --settle-timeout 240 --offscreen

# Foreground slice on a real machine (user's own run, ~5 minutes)
venv/bin/python scripts/runtime_baseline_runner.py \
    --out /tmp/rb-real-slice --paras 100 --seconds 300 --settle-timeout 20 \
    --engine-config "$HOME/Library/Application Support/Folith/data/provider_config.json"

# Windows: same command from the repo checkout on the Windows machine
# (the provider config lives under %APPDATA%/Folith/data/provider_config.json).
```

`--engine-config` reads the real connection **in memory**: the key is never
copied into the report, the isolated data dir or a log.  The runner always
isolates data/log/QSettings under `--out`, so a measurement never touches the
user's real Folith data.  Reports written by the current runner carry
`measurement: {"version": 2, "driver": "qt_event_loop_callbacks",
"instrumentation_split": true}`.  The three runs in the v2 diagnostic table
above were measured before that marker was added to the report (the harness was
still being finished), so their reports carry no `measurement` block even though
they were produced by the v2 driver; do not treat "no marker" on its own as
proof of a v1 run — the v1 pump bucket (`_interact` / `processEvents`) is the
distinguishing evidence.  On macOS the window is raised/activated for the run
(S1 is a foreground session); offscreen runs ignore that.

No command is given for the **gate baseline**, because the current code cannot
produce it: it needs the frozen `8109b9c2` build with the v2 probe back-ported,
≥ 3 rounds of ≥ 5 minutes, and the replay gateway (a recorded real latency
distribution), median of rounds, split into R and I.  Until that build and the
replay gateway exist, no number in this file can be compared against it, and a
60 s mock run must not be presented as if it could.

### macOS 真机 — measurement v1 (superseded: the harness pumped the GUI)

> The numbers in this section were measured with the **v1** harness, whose own
> `processEvents()` pump was the top stall bucket and split product work into
> 10 ms slices (under-reporting the tail).  They are **superseded** as a
> measurement and are not used for any gate comparison; the v2 diagnostic runs
> above are the only v2 numbers in this file.



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

| Run | paras → segments | window (s) | effective pulse rate (Hz) | >100 ms | >500 ms | /min >100 | /min >500 | stall p50 (ms) | stall p99 (ms) | max stall (ms) | RSS start → peak (MB) | CPU avg / peak % | threads | settled |
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

#### S1 rows for the real-machine table (v1)

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
a gate baseline needs ≥ 3 rounds of ≥ 5 minutes each, on the replay gateway,
built from the frozen `8109b9c2` code (D1) — not a real-endpoint run and not
these 60 s windows, which are a starting range only.

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
| stall p99 (ms) | 1073 | 747 | 638 | 1.17–1.68× |
| max stall (ms) | 1357 | 874 | 795 | 1.10–1.71× |
| RSS peak (MB) | 710 | 788 | 735 | ≈1.0× |
| CPU avg % | 117.3 | 112.8 | 110.5 | ≈1.05× |

Conclusion: offscreen undercounts >500 ms stalls by 2.1–2.7× and p99 by up to
1.7×, while its resource numbers are close to the real GUI.  Offscreen is
acceptable for pipeline and resource checks and is **not** a substitute for a
real-machine run — exactly what the issue requires.

### Windows — pending

Windows acceptance still belongs to the user's real machine.  Use the
re-measure command above and run it from the Windows checkout; mocked,
source-on-macOS or offscreen numbers are not a substitute.

Same table; a Windows run must be executed on a Windows machine by the user.
Mocked, source-mode-on-macOS or offscreen numbers are not a substitute.

### Offscreen control (diagnostic only)

Two offscreen runs exist and are kept only as controls: the 12-paragraph/6 s
pipeline smoke recorded in `/tmp/rb1/runtime-baseline.json`, and the
1000-paragraph/60 s control in the comparison table above.  Neither is a
baseline, and neither is entered in the macOS real-machine table.

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
