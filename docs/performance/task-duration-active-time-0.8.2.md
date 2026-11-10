# Task duration = time actually worked (issue #320, 0.8.2)

Status: **heartbeat-based active time implemented in the run record and the
performance report; network-outage time deliberately NOT implemented (see §4);
the UI still shows the start→finish difference and needs a separate small PR
(§6).**  Real suspend/resume acceptance still has to be run on macOS and
Windows (§7).

## 1. The problem this replaces

A 0.8.1 trial task reported four different durations for the same work:

| Source | Value | Definition |
| --- | --- | --- |
| UI status line (`views.run_status_view`) | 266.8 min | `finished_at - started_at` from the task's runtime record |
| Actual suspend | 132.1 min | no request in flight, machine asleep |
| Actual work | 134.8 min | 266.8 − 132.1 |
| `performance.md` | 133 min | `time.perf_counter()` delta |

The report was only accidentally close: `perf_counter` pauses on macOS while the
machine sleeps, and Windows does not promise the same, so the same code would
report different numbers on the two platforms the product ships to.

## 2. Definition

**Active work time** = the wall-clock time during which the run was actually
able to work.  It excludes local interruptions that have nothing to do with the
task:

- machine suspend / hibernation;
- a frozen process (e.g. debugger stop, OS freeze);
- app exit or machine shutdown: each resumed run is a new segment, and the gap
  between segments belongs to no segment.

It does **not** exclude slow provider responses, load, or long GIL-bound work:
those are the product's own performance and must stay visible.

## 3. Algorithm (implemented)

`folith.performance.ActiveTimeTracker`, used by every `RunContext`:

- While the run is active a daemon thread samples `time.time()` every
  `heartbeat_interval_s` (default **5 s**, `FOLITH_ACTIVE_HEARTBEAT_S`
  overrides it, clamped to 0.5–60 s).
- A gap between two samples **larger than `factor × interval`** (factor 3, i.e.
  > 15 s by default) is an interruption.  Suspend, hibernation and a frozen
  process all produce exactly such a gap regardless of platform.
- An interrupted gap contributes **zero** active seconds and is recorded as a
  span (`started_at`, `ended_at`, `duration_s`, `reason: inactive_gap`).
  The rule therefore can only *understate* work (by at most one heartbeat
  period per interruption), never inflate it.
- A backwards wall-clock jump (NTP) contributes nothing and is counted in
  `clock_anomalies`; it is not called an interruption.  A forward jump is
  indistinguishable from a suspend and is treated as one.
- `perf_counter` is kept only as `monotonic_elapsed_s`, labelled diagnostic.
  Nothing user-facing is derived from it any more.

Resumed tasks: each run keeps its own active seconds.  `sum_active_time(entries)`
sums them for a task total; `wall_span_s` is the naive first-start→last-finish
difference kept only to show what the old definition would have claimed, and
`excluded_between_segments_s` is that difference minus the active sum.

## 4. Network outages — agreed condition, not implemented

The agreed condition (from the issue) for time to exclude:

> 没有任何请求成功、且所有在飞请求都因传输错误或退避等待而空转
> (no request succeeded, and every in-flight request was idle on a transport
> error or a retry backoff).

**Not implemented in this change.**  Reason: the condition cannot be decided
reliably from what `RunContext` records today, and a wrong decision would hide
real provider latency — the exact failure the issue warns about.

- A request that has been in flight for ten minutes without a response and
  without an error is indistinguishable from a healthy-but-slow provider.  The
  condition can only be confirmed *retroactively*, after transport failures
  exist, so a live window cannot be classified while it is happening.
- `RunContext` records when a model request starts and when it completes, and
  retries carry `error_kind` (`protocol`/`transport`), but there is no
  per-attempt "last byte received" timestamp and no explicit
  "waiting on backoff" interval.  Reconstructing "all in-flight requests were
  idle on transport failures" from `requests.jsonl` + `retries` would need a
  new per-attempt event (attempt start/end + failure kind + backoff window).
- A future implementation can add that event without touching this algorithm:
  the tracker would accept externally marked spans with a `reason` other than
  `inactive_gap` (e.g. `network_outage`) and exclude them the same way.  That
  needs its own issue and its own test corpus (real transport failures, not a
  mocked socket).

Until then, network outages are **part of active time** and show up in the
report's unaccounted time / retry observation, labelled as such.

## 5. What the report and the run record now say

`summary.json` / `performance.md` (same run directory as before):

```json
{
  "wall_time_s": 8090.4,
  "elapsed_wall_clock_s": 16016.5,
  "interrupted_time_s": 7926.1,
  "interrupted_pct_of_elapsed": 49.49,
  "monotonic_elapsed_s": 8091.2,
  "active_time": {
    "method": "wall_clock_heartbeat",
    "heartbeat_interval_s": 5.0,
    "interruption_factor": 3.0,
    "samples": 1604,
    "active_seconds": 8090.4,
    "interrupted_seconds": 7926.1,
    "interruption_count": 1,
    "elapsed_wall_clock_s": 16016.5,
    "clock_anomalies": 0,
    "spans": [{"reason": "inactive_gap", "started_at": "...", "ended_at": "...", "duration_s": 7926.1}]
  },
  "unaccounted_time_s": 0.0,
  "unaccounted_pct": 0.0,
  "unaccounted_note": "no top-level stage times were recorded for this run, so a gap percentage is not meaningful"
}
```

- `wall_time_s` **is** active work time.  This is the number the report and any
  UI surface must show.
- The report gained an **Interrupted Time (excluded)** section: count, total,
  percentage of elapsed wall clock, and the last five spans; plus a
  Run Overview line for elapsed vs active vs monotonic.
- The misleading "Unaccounted Gap … 100% of runtime" line is gone: when no
  top-level stage times were recorded the report now says the percentage is not
  meaningful instead of claiming the whole run was unaccounted.  The
  unaccounted percentage is computed against *active* time and is labelled as
  such.
- `history.json` entries carry `active_seconds`, `interrupted_seconds` and
  `elapsed_wall_clock_s`, so a task total can be summed across resumed
  segments.
- `performance.json` (legacy viewer) now gets `wall_time_s` = active time, plus
  `active_seconds` / `interrupted_seconds` / `monotonic_elapsed_s`.

## 6. Accessors for the UI (and the remaining UI change)

`folith/trace.py` exposes one definition, so no consumer recomputes it:

```python
run.active_seconds()              # float, live while the run is running
run.interrupted_spans()           # list of spans
run.active_time_summary()         # the whole block above
trace.trace.active_seconds(job_id)   # active run's value, or None
trace.trace.active_time(job_id)
sum_active_time(history_entries)  # task total over resumed segments
```

**The UI does not use it yet.**  Current location (read-only investigation):
`folith/qt/views.py: run_status_view()` computes

```python
started = _parse_iso(raw.get("started_at"))
finished = _parse_iso(raw.get("finished_at")) or now
minutes = int(round((finished - started) / 60))
```

from the task's runtime record.  Those timestamps are written by `core.py`: the
first task start sets `started_at` and a resume deliberately keeps the original
one, while a terminal status sets `finished_at` — which is exactly why the UI
shows the suspend-inclusive duration.  Both files (`views.py`, `core.py`) are
outside this change's boundary, so the UI still shows the old number.

Follow-up PR (small, needs views.py + one runtime field): persist the active
seconds into the runtime record when a task reaches a terminal status (or read
the newest `history.json` entry) and show that in `run_status_view`; then add
an assertion that the UI string and `summary["wall_time_s"]` agree.

## 7. Verification

Automated (`tests/active_time_test.py`, wall clock injected — no test waits for
a real suspend):

- 132.1-minute suspend injected between two heartbeats → excluded, one span
  listed with its duration;
- boundary: a gap of exactly `3 × interval` is work, `3 × interval + 1 ms` is an
  interruption;
- backwards clock jump → no active seconds, counted as a clock anomaly, not an
  interruption;
- two resumed run segments with a gap → task total = sum of segments, the gap is
  reported as excluded;
- a slow request (heartbeat running, request in flight) is **not** an
  interruption;
- report/accessor consistency: `summary["wall_time_s"] == run.active_seconds()`,
  `history.json` carries the same number, and the report lists the span once.

Real-device acceptance (mock tests cannot replace this; both are still pending):

- macOS: start a task, note the active time, run `pmset sleepnow`, wake after
  ≥ 5 minutes, let the task finish.  Expect one interruption in `performance.md`
  with approximately the sleep duration, and
  `wall_time_s ≈ elapsed − sleep` (within one heartbeat period).
- Windows: same with `rundll32.exe powrprof.dll,SetSuspendState 0,1,0` (or
  Start → Sleep), then compare `performance.md` with the UI.  This run also
  confirms the Windows clock behaviour the old implementation silently depended
  on.
- App-exit/resume: start, quit mid-task, resume after ≥ 5 minutes; the task's
  summed segments must exclude the shutdown gap.

## 8. Known limits

- One heartbeat period per interruption can be lost from active time (the gap
  is excluded wholesale).  With the 5 s default this is negligible and always
  errs on the conservative side.
- A forward wall-clock jump (manual clock change, large NTP step) is treated as
  an interruption.  It errs in the same direction (never inflates).
- The tracker covers the process that runs the task.  Time before the first
  heartbeat (run creation → first sample) is counted normally, so the first
  interruption of a run is detected from the first sample on.
- Network-outage exclusion is not implemented (§4).
