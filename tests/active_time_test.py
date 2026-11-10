"""Task duration = time actually worked, not the start→finish wall difference (#320).

The wall clock is injected everywhere: no test sleeps to simulate a 132-minute
suspend, and no test depends on how ``perf_counter`` behaves while a machine is
asleep.
"""
from __future__ import annotations

import json
import time

import pytest

from folith.performance import ActiveTimeTracker
from folith.trace import ActiveTimeTracker as TraceActiveTimeTracker
from folith.trace import RunContext, TraceLevel, sum_active_time


class _FixedTracker:
    """Tracker stub for the report/accessor tests: no thread, no timing."""

    def __init__(self, snapshot):
        self._snapshot = dict(snapshot)
        self.started = self.stopped = False

    def start_sampling(self):
        self.started = True

    def stop_sampling(self):
        self.stopped = True
        return dict(self._snapshot)

    def current(self):
        return float(self._snapshot.get("active_seconds") or 0.0)

    def snapshot(self):
        return dict(self._snapshot)


def _snapshot(active=8100.0, interrupted=7926.0, count=1):
    return {
        "method": "wall_clock_heartbeat",
        "heartbeat_interval_s": 5.0,
        "interruption_factor": 3.0,
        "samples": 1600,
        "active_seconds": active,
        "interrupted_seconds": interrupted,
        "interruption_count": count,
        "elapsed_wall_clock_s": active + interrupted,
        "clock_anomalies": 0,
        "spans": [{"reason": "inactive_gap",
                   "started_at": "2026-11-11T01:00:00.000+00:00",
                   "ended_at": "2026-11-11T03:12:06.000+00:00",
                   "duration_s": interrupted}] if count else [],
    }


# ------------------------------------------------------------ pure tracker
def test_tracker_sums_normal_heartbeats():
    clock = [1000.0]
    tracker = ActiveTimeTracker(interval_s=5.0, wall_clock=lambda: clock[0])
    tracker.start()
    for _ in range(10):
        clock[0] += 5.0
        assert tracker.tick() is None
    snapshot = tracker.snapshot()
    assert snapshot["active_seconds"] == pytest.approx(50.0)
    assert snapshot["interrupted_seconds"] == 0.0
    assert snapshot["interruption_count"] == 0


def test_tracker_excludes_a_suspend_gap_and_lists_it():
    clock = [1000.0]
    tracker = ActiveTimeTracker(interval_s=5.0, wall_clock=lambda: clock[0])
    tracker.start()
    clock[0] += 5.0
    tracker.tick()
    # Machine sleeps for 132.1 minutes: one heartbeat gap of 7926 s.
    clock[0] += 7926.0
    span = tracker.tick()
    assert span is not None
    assert span["duration_s"] == pytest.approx(7926.0, abs=0.01)
    clock[0] += 5.0
    tracker.tick()
    snapshot = tracker.snapshot()
    # Only the two real 5 s heartbeats count; the suspend does not.
    assert snapshot["active_seconds"] == pytest.approx(10.0)
    assert snapshot["interrupted_seconds"] == pytest.approx(7926.0, abs=0.01)
    assert snapshot["interruption_count"] == 1
    assert snapshot["spans"][0]["reason"] == "inactive_gap"
    assert snapshot["elapsed_wall_clock_s"] == pytest.approx(7936.0, abs=0.01)


def test_tracker_interval_boundary_uses_the_factor():
    clock = [0.0]
    tracker = ActiveTimeTracker(interval_s=5.0, factor=3.0, wall_clock=lambda: clock[0])
    tracker.start()
    clock[0] += 15.0  # exactly 3 x interval: still work
    assert tracker.tick() is None
    clock[0] += 15.001  # just past it: interruption
    assert tracker.tick() is not None
    snapshot = tracker.snapshot()
    assert snapshot["active_seconds"] == pytest.approx(15.0)
    assert snapshot["interruption_count"] == 1


def test_tracker_ignores_a_backwards_clock_jump():
    clock = [1000.0]
    tracker = ActiveTimeTracker(interval_s=5.0, wall_clock=lambda: clock[0])
    tracker.start()
    clock[0] += 5.0
    tracker.tick()
    clock[0] -= 60.0  # NTP correction, not an interruption
    assert tracker.tick() is None
    clock[0] += 5.0
    tracker.tick()
    snapshot = tracker.snapshot()
    assert snapshot["clock_anomalies"] == 1
    assert snapshot["interruption_count"] == 0
    assert snapshot["active_seconds"] == pytest.approx(10.0)


def test_tracker_stop_counts_the_final_stretch():
    clock = [0.0]
    tracker = ActiveTimeTracker(interval_s=5.0, wall_clock=lambda: clock[0])
    tracker.start()
    clock[0] += 4.0  # no heartbeat happened before the run ended
    snapshot = tracker.stop()
    assert snapshot["active_seconds"] == pytest.approx(4.0)
    assert snapshot["samples"] == 2


def test_tracker_thread_heartbeat_runs_and_stops():
    tracker = ActiveTimeTracker(interval_s=0.05)
    tracker.start_sampling()
    time.sleep(0.2)
    snapshot = tracker.stop_sampling()
    assert snapshot["active_seconds"] >= 0.1
    assert snapshot["interruption_count"] == 0
    assert tracker.stop()["active_seconds"] >= snapshot["active_seconds"]


# --------------------------------------------------- RunContext / report glue
def test_run_context_reports_active_seconds_and_interruptions(tmp_path):
    tracker = _FixedTracker(_snapshot())
    context = RunContext("active-run", output_dir=tmp_path, level=TraceLevel.STANDARD,
                         active_tracker=tracker)
    assert tracker.started
    summary = context.finish()
    assert tracker.stopped
    # Reported duration is active time, not start→finish (which includes 132 min).
    assert summary["wall_time_s"] == pytest.approx(8100.0)
    # 135 min active + 132.1 min suspended = the 266.8 min the UI used to show.
    assert summary["elapsed_wall_clock_s"] == pytest.approx(16026.0)
    assert summary["interrupted_time_s"] == pytest.approx(7926.0)
    assert summary["interrupted_pct_of_elapsed"] == pytest.approx(49.46, abs=0.1)
    assert summary["active_time"]["interruption_count"] == 1
    # The accessor and the summary are the same number, by construction.
    assert context.active_seconds() == pytest.approx(summary["wall_time_s"])
    assert [span["duration_s"] for span in context.interrupted_spans()] == [pytest.approx(7926.0)]
    # The monotonic clock stays available as a diagnostic, never as the duration.
    assert summary["monotonic_elapsed_s"] >= 0.0


def test_run_context_survives_a_tracker_that_never_started(tmp_path):
    context = RunContext("no-tracker", output_dir=tmp_path, level=TraceLevel.OFF)
    summary = context.finish()
    # Trace level OFF keeps the old monotonic reference; nothing is written.
    assert summary["wall_time_s"] >= 0.0
    assert summary["active_time"]["active_seconds"] == 0.0


def test_report_states_interruptions_and_never_claims_100_percent_gap(tmp_path):
    tracker = _FixedTracker(_snapshot())
    context = RunContext("active-report", output_dir=tmp_path, level=TraceLevel.STANDARD,
                         active_tracker=tracker)
    summary = context.finish()
    md = (context.run_dir / "performance.md").read_text(encoding="utf-8")
    assert "**Active Work Time**: **8100.0 s**" in md
    assert "**Interrupted (excluded)**: **7926.0 s** in 1 span(s)" in md
    assert "## Interrupted Time (excluded)" in md
    assert "| 2026-11-11T01:00:00.000+00:00 | 7926.0 s | inactive_gap |" in md
    assert "100.0%" not in md
    assert summary["unaccounted_pct"] == 0.0
    assert "no top-level stage times were recorded" in summary["unaccounted_note"]


def test_resumed_runs_sum_segments_and_exclude_the_gap(tmp_path):
    first = RunContext("segments", output_dir=tmp_path, level=TraceLevel.STANDARD,
                       active_tracker=_FixedTracker(_snapshot(active=0.06, interrupted=0.0, count=0)))
    summary_one = first.finish()
    time.sleep(0.15)  # app closed / machine shut down: no segment covers this
    second = RunContext("segments", output_dir=tmp_path, level=TraceLevel.STANDARD,
                        active_tracker=_FixedTracker(_snapshot(active=0.04, interrupted=0.0, count=0)))
    summary_two = second.finish()
    total = sum_active_time([summary_one, summary_two])
    assert total["segments"] == 2
    assert total["active_seconds"] == pytest.approx(0.10)
    assert total["wall_span_s"] is not None
    # The gap between the two runs exists in wall time but is not active time.
    assert total["wall_span_s"] > total["active_seconds"]
    assert total["excluded_between_segments_s"] == pytest.approx(
        total["wall_span_s"] - 0.10, abs=0.02)
    # History keeps the same definition for a future UI reader.
    history = json.loads((tmp_path / ".performance" / "history.json").read_text(encoding="utf-8"))
    assert history[-1]["active_seconds"] == pytest.approx(0.04)
    assert history[-1]["interrupted_seconds"] == 0.0


def test_slow_request_is_not_an_interruption(tmp_path):
    """A slow server response keeps the heartbeat going and stays active time."""
    # A short heartbeat keeps the test fast; a request "in flight" is just the
    # main thread waiting, exactly like a slow provider.
    context = RunContext("slow-request", output_dir=tmp_path, level=TraceLevel.STANDARD,
                         active_tracker=ActiveTimeTracker(interval_s=0.05))
    context.begin_model_request("测试", "stub-model", role="translation")
    time.sleep(0.25)
    context.end_model_request()
    summary = context.finish()
    assert summary["active_time"]["interruption_count"] == 0
    assert summary["interrupted_time_s"] == 0.0
    assert summary["wall_time_s"] >= 0.2


class _ManualTracker(ActiveTimeTracker):
    """Real tracker with the heartbeat thread replaced by explicit ticks."""

    def start_sampling(self):
        self.start()

    def stop_sampling(self):
        return self.stop()


def test_active_seconds_is_committed_only_and_never_reinterpolates(tmp_path):
    """Two reads at the same instant are identical; the wall clock is not re-read."""
    clock = [100.0]
    tracker = _ManualTracker(interval_s=0.5, wall_clock=lambda: clock[0])
    run = RunContext("committed", output_dir=tmp_path, level=TraceLevel.STANDARD,
                     active_tracker=tracker)
    clock[0] += 0.2
    tracker.tick()
    first = run.active_seconds()
    clock[0] += 0.001  # the wall clock moves, the committed value must not
    second = run.active_seconds()
    assert first == second == pytest.approx(0.2)
    assert run.active_time_summary()["active_seconds"] == first
    # The tracked value behind the accessor is the committed one, not "now".
    assert tracker.snapshot()["active_seconds"] == first

    summary = run.finish()
    assert summary["wall_time_s"] == run.active_seconds()
    assert run.active_seconds() == run.active_seconds()  # frozen after finish


def test_trace_manager_exposes_the_same_accessor(tmp_path):
    from folith.trace import TraceManager

    manager = TraceManager()
    manager.set_level(TraceLevel.STANDARD)
    run = manager.start_run("manager-active", output_dir=tmp_path)
    try:
        time.sleep(0.05)
        # Same instance, so this cannot be a second calculation; the tolerance
        # (10 ms) is two orders of magnitude above the 0.1 ms rounding unit and
        # one below the 5 s heartbeat, so a green result cannot depend on a
        # clock delta between the two calls.
        assert manager.get_active_run("manager-active") is run
        reported = manager.active_seconds("manager-active")
        expected = run.active_seconds()
        assert reported == pytest.approx(expected, abs=0.01)
        block = manager.active_time("manager-active")
        assert block["method"] == "wall_clock_heartbeat"
        assert block["running"] is True
        assert block["active_seconds"] == pytest.approx(expected, abs=0.01)
    finally:
        manager.finish_run("manager-active")


def test_trace_reexports_the_same_tracker_class():
    assert TraceActiveTimeTracker is ActiveTimeTracker
