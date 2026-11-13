"""Main-thread stall sampler: thresholds, bounded aggregation, run-record wiring.

The Qt test at the bottom runs in a subprocess so ``QApplication`` and its pulse
timer are real; everything above it is pure logic with an injected clock.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from folith.qt.jank_monitor import (
    ENV_SWITCH,
    JankMonitor,
    ThreadFrame,
    attribute_frame,
    capture_other_thread_frames,
    frame_is_framework,
    jank_monitor_enabled,
    render_frames,
)


class _TimerStub:
    """Timer seam for tests that must not create a real Qt timer."""

    def __init__(self) -> None:
        self.started = None
        self.stopped = False

    def start(self, interval_ms: int) -> None:
        self.started = interval_ms

    def stop(self) -> None:
        self.stopped = True

    def deleteLater(self) -> None:  # noqa: N802 - mirrors the Qt API
        pass


# ---------------------------------------------------------------- thresholds
def test_threshold_boundary_99_100_101_ms():
    clock = [1000.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    assert monitor.mark_pulse() is None  # first pulse only starts the clock
    clock[0] += 0.099
    assert monitor.mark_pulse() is None
    clock[0] += 0.100
    event = monitor.mark_pulse()
    assert event is not None
    assert event["interval_ms"] == pytest.approx(100.0, abs=0.01)
    assert event["severity"] == "slow"
    clock[0] += 0.101
    assert monitor.mark_pulse()["severity"] == "slow"
    summary = monitor.summary()
    assert summary["slow_count"] == 2
    assert summary["severe_count"] == 0
    assert summary["pulse_count"] == 3


def test_severe_threshold_counts_once_in_both_buckets():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    clock[0] += 0.6
    event = monitor.mark_pulse()
    assert event["severity"] == "severe"
    summary = monitor.summary()
    # A severe stall is still a stall: >500 counts inside >100.
    assert summary["slow_count"] == 1
    assert summary["severe_count"] == 1
    assert summary["longest"]["interval_ms"] == pytest.approx(600.0, abs=0.1)
    assert summary["pulse_interval_ms"] == 25.0
    assert summary["recent"][0]["blocked_ms"] == pytest.approx(575.0, abs=0.1)


def test_disabled_switch_from_environment_and_flag():
    assert jank_monitor_enabled({}) is True
    assert jank_monitor_enabled({ENV_SWITCH: "1"}) is True
    assert jank_monitor_enabled({ENV_SWITCH: ""}) is True
    for value in ("0", "false", "no", "off", "OFF", " disabled "):
        assert jank_monitor_enabled({ENV_SWITCH: value}) is False

    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0, enabled=False)
    monitor.start(_TimerStub())
    assert monitor.timer is None
    clock[0] += 5.0
    assert monitor.mark_pulse() is None
    summary = monitor.summary()
    assert summary["enabled"] is False
    assert summary["slow_count"] == 0
    assert monitor.stop()["enabled"] is False


def test_healthy_pulses_never_touch_the_stack_getter():
    calls = []
    monitor = JankMonitor(clock=lambda: time.monotonic(), wall_clock=lambda: 0.0,
                          stack_getter=lambda ident: calls.append(ident) or ())
    monitor.start(_TimerStub())
    try:
        for _ in range(20):
            monitor.mark_pulse()
            time.sleep(0.002)
    finally:
        monitor.stop()
    assert calls == []
    assert monitor.summary()["stack_capture"]["attempts"] == 0


# ------------------------------------------------------- aggregation/top-N
def _stall(monitor: JankMonitor, clock, interval_ms: float, stack=()):
    monitor._pending_stack = tuple(stack)
    clock[0] += interval_ms / 1000.0
    return monitor.mark_pulse()


def test_percentiles_and_top_n_aggregate_by_stack():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    hotspot = ("views.py:9 in render_rows", "window.py:3 in refresh")
    other = ("core.py:7 in dedupe",)
    for value in (150.0, 150.0, 200.0):
        _stall(monitor, clock, value, hotspot)
    _stall(monitor, clock, 600.0, other)
    summary = monitor.summary()
    assert summary["slow_count"] == 4
    assert summary["severe_count"] == 1
    # Ranked by accumulated stall time, not by event count: one 600 ms stall
    # outranks three smaller ones on the same stack.
    ranked = [tuple(row["stack"]) for row in summary["top_stacks"]]
    assert ranked == [other, hotspot]
    rows = {tuple(row["stack"]): row for row in summary["top_stacks"]}
    assert rows[hotspot]["count"] == 3
    assert rows[hotspot]["total_ms"] == pytest.approx(500.0, abs=0.1)
    assert rows[hotspot]["max_ms"] == pytest.approx(200.0, abs=0.1)
    assert summary["stall_ms"]["count"] == 4
    assert summary["stall_ms"]["max"] == pytest.approx(600.0, abs=0.1)
    assert summary["stall_ms"]["p50"] == pytest.approx(200.0, abs=0.1)
    assert summary["longest"]["stack"] == list(other)


def test_event_and_aggregate_lists_are_bounded():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0,
                          top_n=3, recent_limit=2)
    monitor.mark_pulse()
    for index in range(10):
        _stall(monitor, clock, 120.0 + index, (f"module.py:{index} in f{index}",))
    summary = monitor.summary()
    assert summary["slow_count"] == 10            # counters stay exact
    assert len(summary["top_stacks"]) == 3        # aggregate map is capped
    assert len(summary["recent"]) == 2            # event ring is capped
    assert summary["retention"]["top_stacks"] == 3
    # The kept entries are the ones with the largest accumulated stall time.
    assert [row["total_ms"] for row in summary["top_stacks"]] == sorted(
        (row["total_ms"] for row in summary["top_stacks"]), reverse=True)


def test_full_aggregate_map_still_records_a_new_hotspot():
    """Review F1: a new stack must not evict itself from a full Top-N map."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0, top_n=2)
    monitor.mark_pulse()
    _stall(monitor, clock, 100.0, ("a.py:1 in A",))
    _stall(monitor, clock, 200.0, ("b.py:1 in B",))
    _stall(monitor, clock, 5000.0, ("huge.py:9 in giant",))
    summary = monitor.summary()
    assert summary["slow_count"] == 3
    assert summary["severe_count"] == 1
    assert len(summary["top_stacks"]) == 2
    ranked = [tuple(row["stack"]) for row in summary["top_stacks"]]
    assert ranked[0] == ("huge.py:9 in giant",), ranked
    assert ("huge.py:9 in giant",) in ranked
    # The trimmed stack is the least accumulated one, not the newest.
    assert ("a.py:1 in A",) not in ranked
    assert ("b.py:1 in B",) in ranked
    assert summary["longest"]["stack"] == ["huge.py:9 in giant"]
    # Trimming a stack from the map never changes the exact counters.
    assert summary["stall_ms"]["count"] == 3


def test_uncaptured_stalls_are_counted_and_labelled():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    _stall(monitor, clock, 300.0)
    summary = monitor.summary()
    assert summary["slow_count"] == 1
    assert summary["top_stacks"][0]["stack"] == []
    assert summary["recent"][0]["stack_captured"] is False


def test_restart_drops_a_stale_capture_and_start_is_idempotent():
    """Review F5: a stack captured for the previous session must not be reused."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    stub = _TimerStub()
    monitor.start(stub)
    first_timer, first_thread = monitor.timer, monitor._thread
    monitor.start(_TimerStub())  # must be a no-op, not a second sampler
    assert monitor.timer is first_timer
    assert monitor._thread is first_thread
    monitor._pending_stack = ("stale.py:1 in stale",)
    monitor.stop()
    assert monitor._pending_stack == ()
    clock[0] += 0.2
    monitor.start(_TimerStub())
    assert monitor._pending_stack == (), "restart kept the previous capture"
    clock[0] += 0.15
    event = monitor.mark_pulse()
    assert event is not None
    assert event["stack_captured"] is False
    assert event["stack"] == []
    monitor.stop()


# ------------------------------------------------- running-window (run record)
def test_summary_since_reports_only_the_window():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 1000.0)
    monitor.mark_pulse()
    hotspot = ("before.py:1 in early",)
    _stall(monitor, clock, 400.0, hotspot)
    marker = monitor.checkpoint()
    clock[0] += 0.0
    _stall(monitor, clock, 150.0, ("during.py:2 in late",))
    _stall(monitor, clock, 550.0, hotspot)
    window = monitor.summary_since(marker)
    assert window["pulse_count"] == 2
    assert window["slow_count"] == 2
    assert window["severe_count"] == 1
    by_stack = {tuple(row["stack"]): row for row in window["top_stacks"]}
    assert by_stack[("during.py:2 in late",)]["count"] == 1
    assert by_stack[hotspot]["count"] == 1
    # hotspot also stalled before the window, so a window maximum is unknowable.
    assert by_stack[hotspot]["max_ms"] is None
    assert by_stack[("during.py:2 in late",)]["max_ms"] == pytest.approx(150.0, abs=0.1)
    assert window["longest"]["stack"] == list(hotspot)
    assert json.loads(json.dumps(window))["slow_count"] == 2


def test_summary_and_window_are_json_serializable():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 5.0)
    monitor.mark_pulse()
    _stall(monitor, clock, 150.0, ("a.py:1 in b",))
    assert json.loads(json.dumps(monitor.summary()))["slow_count"] == 1
    assert json.loads(json.dumps(monitor.snapshot()))["slow"] == 1


def test_injected_blocking_call_is_recorded_with_its_stack():
    """A real 150 ms and a real 600 ms block, captured from the 'GUI' thread."""
    monitor = JankMonitor(slow_ms=100.0, severe_ms=500.0, pulse_interval_ms=10.0,
                          poll_interval_ms=5.0)
    finished = threading.Event()
    errors = []

    def gui_like_thread() -> None:
        # start() from this thread makes it the monitored thread (the Qt case is
        # the same: the GUI thread is whichever thread started the monitor).
        monitor.start(_TimerStub())
        try:
            def run(seconds: float) -> None:
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    monitor.mark_pulse()
                    time.sleep(0.01)

            run(0.2)
            _blocking_call(0.15)   # one >100 ms stall
            run(0.3)
            _deeper_call(0.6)      # one >500 ms stall
            run(0.2)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test body
            errors.append(exc)
        finally:
            monitor.stop()
            finished.set()

    thread = threading.Thread(target=gui_like_thread, name="fake-gui", daemon=True)
    thread.start()
    assert finished.wait(6.0)
    thread.join(2.0)
    assert errors == []
    summary = monitor.summary()
    assert summary["slow_count"] == 2, summary["recent"]
    assert summary["severe_count"] == 1, summary["recent"]
    captured = [event for event in summary["recent"] if event["stack_captured"]]
    assert captured, summary["stack_capture"]
    innermost = [event["stack"][-1] for event in captured]
    assert any("_blocking_call" in frame for frame in innermost), innermost
    assert any("_deeper_call" in frame for frame in innermost), innermost
    assert summary["stack_capture"]["gui_thread_ident"] == thread.ident
    assert summary["stack_capture"]["gui_thread_is_main"] is False
    assert summary["stack_capture"]["succeeded"] > 0
    assert summary["top_stacks"][0]["stack"], summary["top_stacks"][0]


def _blocking_call(seconds: float) -> None:
    time.sleep(seconds)


def _deeper_call(seconds: float) -> None:
    time.sleep(seconds)


# ------------------------------------------------------------- run record glue
def test_run_context_writes_window_into_the_same_run_record(tmp_path):
    from folith.trace import RunContext, TraceLevel, register_gui_stall_source, unregister_gui_stall_source

    class _Source:
        def checkpoint(self):
            return {"at": 0.0}

        def summary_since(self, marker):
            assert marker == {"at": 0.0}
            return {"scope": "run_window", "slow_count": 3, "severe_count": 1}

    source = _Source()
    register_gui_stall_source(source)
    try:
        context = RunContext("jank-run", output_dir=tmp_path, level=TraceLevel.STANDARD)
        summary = context.finish()
    finally:
        unregister_gui_stall_source(source)
    assert summary["gui_jank"]["slow_count"] == 3
    assert summary["gui_jank"]["measured"] is True
    assert summary["gui_jank"]["source"].endswith("._Source")
    written = json.loads(context.jank_path.read_text(encoding="utf-8"))
    assert written["severe_count"] == 1
    assert context.jank_path.parent == context.run_dir
    assert (context.run_dir / "summary.json").is_file()


def test_two_gui_stall_sources_keep_their_own_windows(tmp_path):
    """Review F2: one source must not overwrite another in the run record."""
    from folith.trace import RunContext, TraceLevel, register_gui_stall_source, unregister_gui_stall_source

    class _SourceA:
        def checkpoint(self):
            return {}

        def summary_since(self, marker):
            return {"enabled": True, "slow_count": 5, "top_stacks": [{"stack": ["a.py"], "count": 5}]}

    class _SourceB:
        def checkpoint(self):
            return {}

        def summary_since(self, marker):
            return {"enabled": True, "slow_count": 2, "top_stacks": [{"stack": ["b.py"], "count": 2}]}

    first, second = _SourceA(), _SourceB()
    register_gui_stall_source(first)
    register_gui_stall_source(second)
    try:
        context = RunContext("jank-two", output_dir=tmp_path, level=TraceLevel.STANDARD)
        summary = context.finish()
    finally:
        unregister_gui_stall_source(first)
        unregister_gui_stall_source(second)
    block = summary["gui_jank"]
    assert block["measured"] is True
    assert [window["slow_count"] for window in block["sources"]] == [5, 2]
    tops = [entry["stack"] for window in block["sources"] for entry in window["top_stacks"]]
    assert tops == [["a.py"], ["b.py"]]


def test_run_context_without_a_source_says_not_measured(tmp_path):
    """Review F3: "not measured" must not look like "zero stalls"."""
    from folith.trace import RunContext, TraceLevel

    context = RunContext("jank-none", output_dir=tmp_path, level=TraceLevel.STANDARD)
    summary = context.finish()
    assert summary["gui_jank"]["measured"] is False
    assert "reason" in summary["gui_jank"]
    # The run record still gets a file, so a reader of the directory sees why.
    written = json.loads(context.jank_path.read_text(encoding="utf-8"))
    assert written["measured"] is False


# ------------------------------------------------------------------ Qt, real app
def test_real_qt_pulse_timer_records_a_blocking_slot(tmp_path):
    pytest.importorskip("PySide6")
    script = r'''
import json
import sys
import time
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from folith.qt.jank_monitor import JankMonitor

app = QGuiApplication([])
monitor = JankMonitor(pulse_interval_ms=20.0, poll_interval_ms=10.0)
monitor.start()
assert monitor.timer is not None, "no Qt timer was created"

def block_here():
    time.sleep(0.15)

def finish():
    try:
        summary = monitor.stop()
        assert summary["stack_capture"]["gui_thread_is_main"] is True
        assert summary["pulse_count"] > 20, summary["pulse_count"]
        assert summary["slow_count"] >= 1, summary["slow_count"]
        stacks = [event["stack"] for event in summary["recent"] if event["stack_captured"]]
        assert stacks, summary["stack_capture"]
        assert any("block_here" in frames[-1] for frames in stacks), stacks
        assert monitor.timer is None
        json.dumps(summary)
        print("jank ok", summary["slow_count"], summary["severe_count"], flush=True)
        app.exit(0)
    except BaseException:
        import traceback
        traceback.print_exc()
        monitor.stop()
        app.exit(1)

QTimer.singleShot(200, block_here)
QTimer.singleShot(1200, finish)
raise SystemExit(app.exec())
'''
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1],
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "jank ok" in result.stdout


def test_render_frames_names_the_python_function():
    frames = render_frames(sys._getframe())
    # Innermost frame last, like a traceback; the filename basename is kept so a
    # report does not leak the machine's absolute paths.
    assert frames[-1].endswith("in test_render_frames_names_the_python_function")
    assert any(frame.startswith("jank_monitor_test.py:") for frame in frames)
    assert ":" in frames[-1] and " in " in frames[-1]


def test_ui_health_starts_registers_and_stops_the_sampler(tmp_path):
    """The production entry point must own the sampler, not just the tests."""
    pytest.importorskip("PySide6")
    script = r'''
from pathlib import Path
from types import SimpleNamespace
import sys
from PySide6.QtGui import QGuiApplication
from folith import trace
from folith.desktop.paths import DesktopPaths
from folith.qt.ui_health import UiHealthMonitor

root = Path(sys.argv[1])
app = QGuiApplication([])
paths = DesktopPaths(root / "data", root / "logs").ensure()
window = SimpleNamespace(_cached_state={}, _current_job_id="",
                         runner=SimpleNamespace(_running=[]), winId=lambda: 0)
monitor = UiHealthMonitor(app, window, paths, timeout=5.0)
monitor.start()
first = monitor.jank
assert first is not None, "sampler was not started"
assert first in trace._GUI_STALL_SOURCES, "sampler not registered with the run record"
monitor.start()  # review F2: must be idempotent, not a second sampler
assert monitor.jank is first, "a repeated start() replaced the sampler"
assert [s for s in trace._GUI_STALL_SOURCES if isinstance(s, type(first))] == [first]
monitor.pulse()  # the heartbeat must carry the stall counters
snapshot = monitor.jank.snapshot()
assert set(snapshot) == {"enabled", "slow", "severe", "instrumentation_slow",
                             "longest_ms", "top_function"}, snapshot
assert snapshot["enabled"] is True
monitor.stop()
assert monitor.jank is None
assert monitor.jank not in trace._GUI_STALL_SOURCES
print("ui health sampler wired", flush=True)
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1],
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ui health sampler wired" in result.stdout


def test_selftest_entrypoint_measures_the_gui(tmp_path):
    """Review F3: the scripted (frozen-capable) entry point must measure stalls."""
    pytest.importorskip("PySide6")
    script = r'''
import json
import sys
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from folith.trace import trace as trace_api
from folith.qt import app as app_module
from folith.qt import selftest

root = Path(sys.argv[1])


def fake_run(self, adapter, core, fixtures):
    """Stand-in for the real flow: it must still be measured like one."""
    run = trace_api.start_run("selftest-stub", output_dir=self.out_dir)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    time.sleep(0.15)  # one >100 ms stall, measured by the sampler
    QApplication.processEvents()
    run.finish()
    return {"ok": True}


selftest.Flow.run = fake_run
code = app_module.main([
    "--data-dir", str(root / "data"), "--log-dir", str(root / "logs"),
    "--selftest", "--selftest-fixture", "txt",
    "--result", str(root / "selftest.json"), "--allow-second-instance",
])
assert code == 0, code
report = json.loads((root / "selftest.json").read_text(encoding="utf-8"))
assert report["gui_jank"]["measured"] is True, report.get("gui_jank")
runs = sorted(root.glob(".performance/runs/run_*/summary.json"))
assert runs, "the selftest run wrote no performance record"
block = json.loads(runs[0].read_text(encoding="utf-8"))["gui_jank"]
assert block["measured"] is True, block
assert block["slow_count"] >= 1, block
assert (runs[0].parent / "gui_jank.json").is_file()
print("selftest jank ok", block["slow_count"], flush=True)
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               FOLITH_QT_SELFTEST_BASE_URL="http://127.0.0.1:9/v1")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest jank ok" in result.stdout


# ------------------------------------------------- instrumentation (#327)
def test_instrumentation_stalls_are_counted_apart_from_product_stalls():
    """Harness-caused stalls must not enter the product numbers."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    monitor._pending_stack = ("views.py:1 in _fill_cards",)
    with monitor.instrumentation():
        clock[0] += 0.3          # the harness work blocks the GUI thread
    event = monitor.mark_pulse()  # the next pulse observes that interval
    assert event is not None and event["instrumentation"] is True
    summary = monitor.summary()
    assert summary["slow_count"] == 0          # not part of the product metric
    assert summary["severe_count"] == 0
    assert summary["instrumentation_slow_count"] == 1
    assert summary["instrumentation_total_ms"] == pytest.approx(300.0, abs=0.1)
    assert summary["stall_ms"]["count"] == 0   # product percentiles exclude it
    assert summary["instrumentation_stall_ms"]["count"] == 1
    assert summary["longest"] is None          # product longest excludes it
    assert summary["top_stacks"] == []         # product hotspots exclude it
    # The event is still recorded (recent/report readers can see the harness
    # cost); it is only ineligible for the product statistics above.
    assert summary["recent"][0]["instrumentation"] is True
    assert monitor.snapshot()["instrumentation_slow"] == 1

    # A product stall afterwards is counted normally again.
    monitor._pending_stack = ("views.py:2 in set_rows",)
    clock[0] += 0.2
    event = monitor.mark_pulse()
    assert event is not None and event["instrumentation"] is False
    summary = monitor.summary()
    assert summary["slow_count"] == 1
    assert summary["longest"]["stack"] == ["views.py:2 in set_rows"]
    assert summary["top_stacks"][0]["stack"] == ["views.py:2 in set_rows"]


def test_instrumentation_window_does_not_mark_a_later_stall():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    clock[0] += 0.05
    with monitor.instrumentation():
        pass                       # a short predicate; no stall during it
    clock[0] += 0.01
    monitor.mark_pulse()           # interval 60 ms: below threshold, nothing recorded
    clock[0] += 0.10
    event = monitor.mark_pulse()   # interval 100 ms, starts after the window closed
    assert event is not None
    assert event["instrumentation"] is False
    assert monitor.summary()["slow_count"] == 1


def test_run_window_splits_product_and_instrumentation():
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    marker = monitor.checkpoint()
    clock[0] += 0.2
    monitor.mark_pulse()                     # product stall
    with monitor.instrumentation():
        clock[0] += 0.4                      # harness work blocks the GUI thread
    monitor.mark_pulse()                     # the next pulse observes it
    window = monitor.summary_since(marker)
    assert window["slow_count"] == 1
    assert window["instrumentation_slow_count"] == 1
    assert window["instrumentation_total_ms"] == pytest.approx(400.0, abs=0.1)
    assert window["stall_ms"]["count"] == 1
    assert window["instrumentation_stall_ms"]["count"] == 1
    # #347 B1: the 400 ms harness event is the larger event in this window, so a
    # naive max() over the event ring would hand it to the product as "longest".
    assert window["longest"]["interval_ms"] == pytest.approx(200.0, abs=0.1)
    assert window["longest"]["instrumentation"] is False
    assert window["longest"]["interval_ms"] <= window["stall_ms"]["max"] + 0.1
    assert "instrumentation stalls" in " ".join(window["notes"])


def test_run_window_longest_ignores_a_2s_instrumentation_stall():
    """#347 B1 regression: a settle-phase predicate poll must not be "longest".

    Deterministic reproduction from the review: a 2 s harness stall inside an
    instrumentation window, with no product stall anywhere near it.
    """
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 1e6 + clock[0])
    monitor.mark_pulse(0.0)
    clock[0] = 0.020
    monitor.mark_pulse(0.020)
    marker = monitor.checkpoint()
    monitor.set_instrumentation(True)
    clock[0] = 0.030
    monitor.set_instrumentation(False)        # instrumentation window closes
    clock[0] = 2.030
    event = monitor.mark_pulse(2.030)         # this 2 s interval belongs to the harness
    assert event is not None and event["instrumentation"] is True
    window = monitor.summary_since(marker)
    assert window["slow_count"] == 0
    assert window["severe_count"] == 0
    assert window["stall_ms"]["count"] == 0
    assert window["instrumentation_slow_count"] == 1
    assert window["instrumentation_stall_ms"]["max"] == pytest.approx(2010.0, abs=0.1)
    # The event is still recorded — this is what used to leak out as "longest".
    assert window["instrumentation_total_ms"] == pytest.approx(2010.0, abs=0.1)
    assert window["longest"] is None
    assert window["top_stacks"] == []


def test_summary_and_run_window_agree_on_the_longest_product_stall():
    """Both entry points must apply one rule for longest/slow_count/stall_ms."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    marker = monitor.checkpoint()             # window starts here = whole session
    for interval_ms, stack in ((150.0, ("a.py:1 in a",)),
                               (900.0, ("b.py:2 in b",)),
                               (300.0, ("c.py:3 in c",))):
        _stall(monitor, clock, interval_ms, stack)
    with monitor.instrumentation():
        clock[0] += 2.0                       # harness stall, larger than any product one
    monitor.mark_pulse()
    whole = monitor.summary()
    window = monitor.summary_since(marker)
    assert whole["longest"]["interval_ms"] == pytest.approx(900.0, abs=0.1)
    assert whole["longest"]["stack"] == ["b.py:2 in b"]
    assert window["longest"] == whole["longest"]
    assert window["slow_count"] == whole["slow_count"] == 3
    assert window["stall_ms"] == whole["stall_ms"]
    assert window["severe_count"] == whole["severe_count"] == 1


# ------------------------------------------------- other threads (P1-1)
def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


def _gil_hog_loop(stop: threading.Event, ready: threading.Event) -> None:
    """A pure-Python busy loop: the GIL holder a stall report must name.

    The inner loop keeps the thread's innermost frame inside this function most
    of the time (``Event.is_set`` is a Python call, so a sample can land there),
    which is also the case the normalisation has to survive.
    """
    ready.set()
    counter = 0
    while not stop.is_set():
        for _ in range(200):
            counter += 1


def _wait_in_thread(stop: threading.Event) -> None:
    stop.wait(5.0)


def _pulse_loop(monitor: JankMonitor, seconds: float, sleep_s: float = 0.01) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        monitor.mark_pulse()
        time.sleep(sleep_s)


def test_attribute_frame_normalises_to_the_innermost_application_frame():
    """One thread, one aggregation key: library frames never own the row."""
    stop = threading.Event()
    ready = threading.Event()

    def waiter() -> None:
        ready.set()
        _wait_in_thread(stop)

    thread = threading.Thread(target=waiter, name="waiting-worker", daemon=True)
    thread.start()
    seen = []
    try:
        assert ready.wait(2.0)
        assert _wait_for(
            lambda: bool(seen.append(capture_other_thread_frames()[0].get(thread.ident))
                         or seen[-1] is not None), 2.0), "the thread was never snapshotted"
    finally:
        stop.set()
        thread.join(2.0)
    observation = seen[-1]
    assert observation.name == "waiting-worker"
    assert observation.running is False       # parked inside threading.wait
    # The innermost frame was threading.wait; the key is the application code
    # that owns the wait, not the library frame.
    assert observation.frame.endswith("in _wait_in_thread"), observation.frame
    assert not observation.frame.startswith("<library>"), observation.frame
    # A stdlib frame is framework, an application frame is not.
    assert frame_is_framework(sys._getframe()) is False
    raw = threading.current_thread()
    assert raw is not None


def test_attribute_frame_names_a_busy_loop_even_inside_a_library_call():
    """``while not event.is_set()`` must not be grouped under threading.py."""
    stop = threading.Event()
    ready = threading.Event()
    hog = threading.Thread(target=_gil_hog_loop, args=(stop, ready), name="busy",
                           daemon=True)
    hog.start()
    try:
        assert ready.wait(2.0)
        seen = []

        def saw_it() -> bool:
            candidate = capture_other_thread_frames()[0].get(hog.ident)
            if candidate is not None and "_gil_hog_loop" in candidate.frame:
                seen.append(candidate)
                return True
            return False

        assert _wait_for(saw_it, 2.0), "the busy thread was never attributed"
    finally:
        stop.set()
        hog.join(2.0)
    assert seen[-1].name == "busy"
    assert not seen[-1].frame.startswith("<library>"), seen[-1].frame
    assert seen[-1].frame.endswith("in _gil_hog_loop"), seen[-1].frame
    # Sampled inside its own loop, it was executing application code.
    assert any(item.running for item in seen), "never sampled in application code"


def test_other_thread_snapshot_is_capped_and_budgeted(monkeypatch):
    """One snapshot is bounded: thread count, and wall time (P1-1 cost rule)."""
    stop = threading.Event()
    workers = [threading.Thread(target=_wait_in_thread, args=(stop,),
                                name=f"idle-{index}", daemon=True) for index in range(2)]
    for worker in workers:
        worker.start()
    try:
        idents = {worker.ident for worker in workers}
        assert _wait_for(
            lambda: idents.issubset(capture_other_thread_frames()[0]), 2.0
        ), "the idle threads never appeared in a snapshot"
        frames, truncated = capture_other_thread_frames(max_threads=1)
        assert truncated is True
        assert len(frames) == 1

        # The time budget is checked before each thread is walked; with an
        # injected clock past the deadline nothing is inspected.
        import folith.qt.jank_monitor as jank_monitor

        ticks = iter([100.0, 200.0] + [200.0] * 32)
        monkeypatch.setattr(jank_monitor, "time",
                            type("_Clock", (), {"monotonic": staticmethod(lambda: next(ticks))}))
        frames, truncated = capture_other_thread_frames(budget_ms=4.0)
        monkeypatch.undo()
        assert truncated is True
        assert frames == {}
    finally:
        stop.set()
        for worker in workers:
            worker.join(2.0)


def test_thread_snapshot_is_taken_once_per_stall_not_once_per_poll():
    """Cost: one all-thread snapshot per stall, not one per watcher poll."""
    clock = [0.0]
    calls = []

    def threads_getter(ident):
        calls.append(ident)
        return {4242: ThreadFrame(name="hog", frame="worker.py:1 in spin", running=True)}, False

    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0,
                          poll_interval_ms=5.0, threads_getter=threads_getter)
    monitor.start(_TimerStub())
    try:
        clock[0] = 0.2
        assert _wait_for(lambda: bool(monitor._pending_threads), 2.0)
        # The stall stays open for ~10 more polls of the watcher; none of them
        # may take a second snapshot.
        time.sleep(0.05)
        assert len(calls) == 1, calls
        event = monitor.mark_pulse()
        assert event is not None
        assert event["other_threads"] == [
            {"thread": "hog", "frame": "worker.py:1 in spin", "running": True}]
        assert len(calls) == 1, calls
        clock[0] = 0.6
        assert _wait_for(lambda: bool(monitor._pending_threads), 2.0)
        assert monitor.mark_pulse() is not None
        assert len(calls) == 2, calls
    finally:
        monitor.stop()
    capture = monitor.summary()["stack_capture"]
    assert capture["other_threads_attempts"] == 2
    assert capture["other_threads_succeeded"] == 2


def test_other_thread_ranking_names_the_thread_that_held_the_gil():
    """A real background busy loop must be named, not inferred."""
    monitor = JankMonitor(slow_ms=100.0, severe_ms=800.0, pulse_interval_ms=10.0,
                          poll_interval_ms=5.0)
    finished = threading.Event()
    stop_hog = threading.Event()
    errors = []

    def gui_like_thread() -> None:
        monitor.start(_TimerStub())
        try:
            _pulse_loop(monitor, 0.2)
            monitor.mark_pulse()
            time.sleep(0.35)         # the GUI is silent; the hog keeps the GIL busy
            _pulse_loop(monitor, 0.2)  # this pulse records the stall
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test body
            errors.append(exc)
        finally:
            monitor.stop()
            finished.set()

    hog = threading.Thread(target=_gil_hog_loop, args=(stop_hog, ready := threading.Event()),
                           name="gil-hog", daemon=True)
    hog.start()
    assert ready.wait(2.0)
    gui = threading.Thread(target=gui_like_thread, name="fake-gui", daemon=True)
    gui.start()
    try:
        assert finished.wait(8.0)
        gui.join(2.0)
    finally:
        stop_hog.set()
        hog.join(2.0)
    assert errors == []
    summary = monitor.summary()
    assert summary["slow_count"] >= 1, summary["recent"]
    rows = summary["other_threads"]
    assert rows, summary["stack_capture"]
    frames = [row["frame"] for row in rows]
    assert any("_gil_hog_loop" in frame for frame in frames), frames
    hog_row = next(row for row in rows if "_gil_hog_loop" in row["frame"])
    assert "gil-hog" in hog_row["threads"]
    assert hog_row["count"] >= 1
    assert hog_row["total_ms"] > 0.0
    # The event itself carries the same evidence, so a run record names it too.
    blamed = [event for event in summary["recent"] if event.get("other_threads")]
    assert blamed, summary["recent"]
    assert any(row["frame"] == hog_row["frame"]
               for event in blamed for row in event["other_threads"])
    assert summary["stack_capture"]["other_threads_attempts"] >= 1


# ------------------------------------------------------- F1 / F2 (#327 review)
def test_summary_since_longest_is_exact_beyond_the_recent_ring():
    """F1: ``longest`` must not be read from the bounded ``recent`` ring."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0, recent_limit=5)
    monitor.mark_pulse()
    marker = monitor.checkpoint()
    _stall(monitor, clock, 900.0, ("huge.py:9 in giant",))
    for index in range(9):
        _stall(monitor, clock, 150.0 + index, (f"small.py:{index} in step{index}",))
    whole = monitor.summary()
    # The 900 ms event has been pushed out of the ring: this is the old defect.
    assert all(event["interval_ms"] != 900.0 for event in whole["recent"])
    assert len(whole["recent"]) == 5
    window = monitor.summary_since(marker)
    assert window["slow_count"] == 10
    assert window["longest"]["interval_ms"] == pytest.approx(900.0, abs=0.1)
    assert window["longest"]["stack"] == ["huge.py:9 in giant"]
    # Consistent with the exact percentile maximum, not with the ring.
    assert window["stall_ms"]["max"] == pytest.approx(900.0, abs=0.1)
    assert whole["longest"]["interval_ms"] == pytest.approx(900.0, abs=0.1)
    assert whole["longest"] == window["longest"]


def test_all_pulse_p99_excludes_instrumentation():
    """F2: 97 x 25 ms product pulses plus 3 x 2 s harness intervals."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    for _ in range(97):
        clock[0] += 0.025
        monitor.mark_pulse()
    for _ in range(3):
        with monitor.instrumentation():
            clock[0] += 2.0
        monitor.mark_pulse()
    summary = monitor.summary()
    # Before the fix these 100 intervals gave p99 = 2000 ms; the product's own
    # all-pulse p99 is 25 ms.
    assert summary["interval_ms"]["count"] == 97
    assert summary["interval_ms"]["p99"] == pytest.approx(25.0, abs=0.01)
    assert summary["interval_ms"]["max"] == pytest.approx(25.0, abs=0.01)
    assert summary["instrumentation_interval_ms"]["count"] == 3
    assert summary["instrumentation_interval_ms"]["max"] == pytest.approx(2000.0, abs=0.1)
    # And none of it leaked into the stall statistics.
    assert summary["slow_count"] == 0
    assert summary["stall_ms"]["count"] == 0
    assert summary["instrumentation_slow_count"] == 3


def test_instrumentation_never_reaches_any_product_statistic():
    """Invariant: the harness's stall is invisible to every product field."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0,
                          threads_getter=lambda ident: ({}, False))
    monitor.mark_pulse()
    marker = monitor.checkpoint()
    monitor._pending_stack = ("views.py:1 in _fill_cards",)
    monitor._pending_threads = {7: ThreadFrame(name="pool-1", frame="core.py:4 in _scan")}
    clock[0] += 0.2
    product = monitor.mark_pulse()
    assert product["instrumentation"] is False
    monitor._pending_stack = ("bench.py:2 in _drive",)
    monitor._pending_threads = {8: ThreadFrame(name="harness", frame="bench.py:3 in _drive",
                                               running=True)}
    with monitor.instrumentation():
        clock[0] += 2.0
    harness = monitor.mark_pulse()
    assert harness["instrumentation"] is True
    whole = monitor.summary()
    window = monitor.summary_since(marker)
    for block in (whole, window):
        assert block["slow_count"] == 1
        assert block["severe_count"] == 0
        assert block["stall_ms"]["count"] == 1
        assert block["stall_ms"]["max"] == pytest.approx(200.0, abs=0.1)
        assert block["interval_ms"]["max"] == pytest.approx(200.0, abs=0.1)
        assert block["longest"]["interval_ms"] == pytest.approx(200.0, abs=0.1)
        assert block["longest"]["instrumentation"] is False
        assert [row["stack"] for row in block["top_stacks"]] == [["views.py:1 in _fill_cards"]]
        assert [row["frame"] for row in block["other_threads"]] == ["core.py:4 in _scan"]
    assert whole["instrumentation_slow_count"] == 1
    assert whole["instrumentation_total_ms"] == pytest.approx(2000.0, abs=0.1)
    assert whole["instrumentation_interval_ms"]["count"] == 1


def test_other_thread_ranking_accumulates_count_and_blocked_time():
    """The ranking carries thread names, the frame, count and stall time."""
    clock = [0.0]
    spinner = ThreadFrame("spinner", "scan.py:9 in _rescan", running=True)
    idle = ThreadFrame("idle-1", "<library> queue.py:8 in get")
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    monitor._pending_threads = {10: spinner, 11: idle}   # one stall before the window
    monitor._pending_stack = ("views.py:1 in paint",)
    clock[0] += 0.2
    monitor.mark_pulse()
    marker = monitor.checkpoint()
    for interval_ms in (200.0, 300.0):
        monitor._pending_threads = {10: spinner, 11: idle}
        monitor._pending_stack = ("views.py:1 in paint",)
        clock[0] += interval_ms / 1000.0
        monitor.mark_pulse()
    summary = monitor.summary()
    rows = {row["frame"]: row for row in summary["other_threads"]}
    assert rows["scan.py:9 in _rescan"]["count"] == 3
    assert rows["scan.py:9 in _rescan"]["total_ms"] == pytest.approx(700.0, abs=0.1)
    assert rows["scan.py:9 in _rescan"]["threads"] == ["spinner"]
    assert rows["scan.py:9 in _rescan"]["max_ms"] == pytest.approx(300.0, abs=0.1)
    assert rows["scan.py:9 in _rescan"]["running"] == 3
    assert rows["<library> queue.py:8 in get"]["running"] == 0
    # Same accumulation for the library wait, but application code leads the rank.
    assert rows["<library> queue.py:8 in get"]["count"] == 3
    assert [row["frame"] for row in summary["other_threads"]][0] == "scan.py:9 in _rescan"
    # The per-event evidence keeps both threads, application frame first.
    assert summary["recent"][0]["other_threads"] == [
        {"thread": "spinner", "frame": "scan.py:9 in _rescan", "running": True},
        {"thread": "idle-1", "frame": "<library> queue.py:8 in get", "running": False},
    ]
    window = monitor.summary_since(marker)
    by_frame = {row["frame"]: row for row in window["other_threads"]}
    assert by_frame["scan.py:9 in _rescan"]["count"] == 2      # exact window delta
    assert by_frame["scan.py:9 in _rescan"]["total_ms"] == pytest.approx(500.0, abs=0.1)
    assert by_frame["scan.py:9 in _rescan"]["max_ms"] is None   # it also ran before
    assert by_frame["scan.py:9 in _rescan"]["running"] == 2     # exact window delta
    assert window["stack_capture"]["other_threads_attempts"] == 0


def test_equally_long_stalls_keep_a_maximum_in_every_window():
    """A window starting between two equal maxima must not report ``None``."""
    clock = [0.0]
    monitor = JankMonitor(clock=lambda: clock[0], wall_clock=lambda: 0.0)
    monitor.mark_pulse()
    _stall(monitor, clock, 300.0, ("a.py:1 in a",))
    clock[0] += 0.001
    monitor.mark_pulse()                      # a healthy pulse, not a stall
    marker = monitor.checkpoint()             # starts between the two equals
    _stall(monitor, clock, 300.0, ("b.py:2 in b",))
    window = monitor.summary_since(marker)
    assert window["longest"]["interval_ms"] == pytest.approx(300.0, abs=0.1)
    assert window["longest"]["stack"] == ["b.py:2 in b"]
    # The all-time view keeps the earliest of the tie, as the helper documents.
    assert monitor.summary()["longest"]["stack"] == ["a.py:1 in a"]
