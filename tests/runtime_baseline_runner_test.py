"""The runtime baseline harness must declare what it did not measure.

Only the pure parts run here: the Qt window is exercised by
``tests/jank_monitor_test.py`` (sampler) and by running the script itself.  What
this file pins down:

* a report always carries ``measurement.version`` (v3) and the phase blocks the
  plan judges (P0-6: v2 reports had no ``measurement`` field and no test),
* an empty, disabled or never-pulsed window is ``measured: false``, never a
  zero-filled block that reads like "zero stalls" (F5),
* R and I are computed from the run and scenario windows, both tiers, with the
  tool's own stalls included and excluded and the tool's share named,
* a round whose I phase is shorter than 15 s is voided and counted,
* the five core operations get per-phase percentiles, and a phase without a
  sample is unmeasured rather than a 0 ms latency,
* S3 opens finished tasks and switches between them (the 10,000-segment case),
* the measurement path still never pumps the GUI thread.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import json
import sys
import textwrap
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import runtime_baseline_runner as runner  # noqa: E402


def test_unimplemented_scenario_writes_a_declared_report(tmp_path):
    code = runner.main(["--out", str(tmp_path), "--scenario", "S2"])
    assert code == 2
    payload = json.loads((tmp_path / "runtime-baseline.json").read_text(encoding="utf-8"))
    assert payload["scenario"] == "S2"
    assert payload["implemented"] is False
    assert "not implemented in this round" in payload["reason"]
    assert payload["limits"]


def test_s1_fixture_is_a_deterministic_epub(tmp_path):
    path = runner.build_epub(tmp_path / "fixtures" / "s1.epub", 17)
    with zipfile.ZipFile(path) as book:
        assert book.read("mimetype") == b"application/epub+zip"
        chapter = book.read("chapter.xhtml").decode("utf-8")
    assert chapter.count("<p>") == 17
    # Same input, same content: the baseline must not drift through randomness.
    again = runner.build_epub(tmp_path / "fixtures" / "s1-again.epub", 17)
    with zipfile.ZipFile(again) as book:
        assert book.read("chapter.xhtml") == chapter.encode("utf-8")


def test_runner_reads_the_product_run_record_not_a_parallel_one(tmp_path):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "job123" / ".performance" / "runs" / "run_20260101_000000_abc"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({
        "run_id": "run_20260101_000000_abc",
        "wall_time_s": 12.5,
        "resources": {"process_rss_peak_mb": 321.0},
        "gui_jank": {"slow_count": 4},
    }), encoding="utf-8")
    (run_dir / "gui_jank.json").write_text(json.dumps({"slow_count": 4}), encoding="utf-8")
    (run_dir / "resources.jsonl").write_text('{"process_rss_mb": 300.0}\n', encoding="utf-8")
    rows = runner.read_run_records(data_dir, "job123")
    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_20260101_000000_abc"
    assert rows[0]["resources"]["process_rss_peak_mb"] == 321.0
    assert rows[0]["gui_jank"] == {"slow_count": 4}
    assert rows[0]["gui_jank_file"] is True
    assert rows[0]["resources_jsonl_lines"] == 1


def test_stall_window_is_explicit_about_not_being_measured():
    """Review F3: a missing window must produce `measured: false`, never `{}`."""
    empty = runner._stall_window_from_records([])
    assert empty["measured"] is False
    assert empty["reason"]

    unmeasured = runner._stall_window_from_records(
        [{"gui_jank": {"measured": False, "reason": "no source"}}])
    assert unmeasured["measured"] is False
    assert unmeasured["reason"]

    measured = {"enabled": True, "pulse_count": 50, "slow_count": 2, "top_stacks": [],
                "window": {"observed_s": 5.0}}
    chosen = runner._stall_window_from_records(
        [{"gui_jank": {"measured": False}}, {"gui_jank": measured}])
    assert chosen["slow_count"] == 2
    assert chosen["measured"] is True


def test_disabled_sampler_window_says_not_measured():
    """`enabled: false` is not the same statement as `measured: false`."""
    block = runner._normalize_window({"enabled": False, "slow_count": 0})
    assert block["measured"] is False
    assert "FOLITH_JANK_MONITOR" in block["reason"]
    measured = runner._normalize_window(
        {"enabled": True, "slow_count": 0, "pulse_count": 40, "observed_s": 1.0})
    assert measured["measured"] is True


def test_f5_empty_and_never_pulsed_windows_are_not_a_zero_measurement():
    """F5: an empty window must not return all zeros that read as "zero jank"."""
    for empty in ({}, None, {"slow_count": 0, "severe_count": 0}, []):
        block = runner._normalize_window(empty)
        assert block["measured"] is False, empty
        assert block["reason"], empty
    # A block with counters but no evidence the sampler ever ran is not measured
    # either: pulse_count == 0 is the sampler saying "I observed nothing".
    never = runner._normalize_window({"enabled": True, "pulse_count": 0,
                                      "slow_count": 0, "severe_count": 0})
    assert never["measured"] is False
    assert "pulse" in never["reason"]
    # A run-scope window keeps observed_s under `window`; zero there is a miss.
    empty_scope = runner._normalize_window(
        {"enabled": True, "pulse_count": 3, "window": {"observed_s": 0.0}})
    assert empty_scope["measured"] is False
    assert "observed" in empty_scope["reason"]


def test_f5_stall_window_from_records_does_not_accept_an_empty_block():
    """A run record whose window exists but measured nothing is a miss (F5)."""
    chosen = runner._stall_window_from_records(
        [{"gui_jank": {"slow_count": 0, "severe_count": 0}}])
    assert chosen["measured"] is False
    assert chosen["reason"]
    measured = {"enabled": True, "pulse_count": 10, "slow_count": 1,
                "window": {"observed_s": 2.0}}
    assert runner._stall_window_from_records([{"gui_jank": measured}])["measured"] is True


# ---------------------------------------------------------------------------
# #327: the scenario must be driven from event-loop callbacks, never by pumping
# the GUI thread from script code (that made the harness the biggest stall).
# ---------------------------------------------------------------------------

class _StubApp:
    """Stands in for QApplication: quitting is allowed, pumping is a failure."""

    def __init__(self):
        self.quit_calls = 0
        self.pumped = 0

    def quit(self):
        self.quit_calls += 1

    def processEvents(self):  # noqa: N802 - mirrors the Qt API
        self.pumped += 1
        raise AssertionError("the scenario path must not pump the GUI thread")


class _StubScrollBar:
    def __init__(self):
        self._value = 0
        self._maximum = 1200

    def maximum(self):
        return self._maximum

    def value(self):
        return self._value

    def setValue(self, value):  # noqa: N802 - mirrors the Qt API
        self._value = value


class _StubWorkbench:
    def __init__(self, rows: int = 10):
        # ``rowCount`` is the QTableWidget's own count, which can lag ``_rows``
        # right after open; the driver must not select before it catches up.
        self.segment_list = SimpleNamespace(
            verticalScrollBar=lambda: _StubScrollBar(), rowCount=lambda: rows)
        self.calls = []
        self.activity_popover = SimpleNamespace(
            close=lambda: self.calls.append(("popup-close",)))

    def open_activity(self):
        self.calls.append(("popup-open",))

    def set_followup_drawer_open(self, open_):
        self.calls.append(("drawer", bool(open_)))

    def visible_segment_count(self):
        return 10

    def select_segment(self, index):
        self.calls.append(("select", index))
        return True

    def set_editor_text_for_test(self, text):
        self.calls.append(("text", text))

    def flush_draft(self):
        self.calls.append(("flush",))

    def show_page(self, page):
        self.calls.append(("page", page))


class _StubWindow:
    def __init__(self, adapter=None):
        self.runner = SimpleNamespace(busy=True)
        self.workbench = _StubWorkbench()
        self._opening_job_id = ""
        self._current_job_id = ""
        self.calls = []
        self.adapter = adapter

    def import_paths(self, paths, autostart=False):
        self.calls.append(("import", tuple(paths), autostart))
        if self.adapter is not None:
            self.adapter.import_fixture()

    def open_job(self, job_id):
        self.calls.append(("open", job_id))
        self._current_job_id = job_id

    def start_or_resume(self):
        self.calls.append(("start",))

    def submit_edit(self, index):
        self.calls.append(("submit", index))


class _StubAdapter:
    core = SimpleNamespace(RUNTIME_ACTIVE_STATUSES={"running", "queued"},
                           load_runtime_state=staticmethod(lambda job_id: {"status": "completed"}))

    def __init__(self, job_id="job-1"):
        self._jobs = [SimpleNamespace(job_id=job_id)]

    def add_job(self, job_id):
        self._jobs.append(SimpleNamespace(job_id=job_id))

    def list_jobs(self):
        return list(self._jobs)

    def load_state(self, job_id):
        return {"p2_done": True}


class _ProgressingAdapter(_StubAdapter):
    """S1 stand-in: the task keeps running until the test finishes it."""

    def __init__(self, job_id="job-1"):
        super().__init__(job_id)
        self.done = False
        self.core = SimpleNamespace(
            RUNTIME_ACTIVE_STATUSES={"running", "queued"},
            load_runtime_state=lambda job_id: {
                "status": "completed" if self.done else "running"})


class _ImportingAdapter(_StubAdapter):
    """S3 stand-in: an import creates the task, so the driver can wait for it."""

    def __init__(self):
        self._jobs = []
        self._next = 0

    def import_fixture(self):
        self._next += 1
        self.add_job(f"job-{self._next}")


class _StubJank:
    """Sampler stand-in: marks instrumentation and hands out window checkpoints."""

    def __init__(self, window=None):
        self.phases = []
        self.instrumentation_entries = 0
        self.instrumentation_exits = 0
        self.checkpoints = 0
        self.window = window if window is not None else {
            "enabled": True, "pulse_count": 80, "slow_count": 2, "severe_count": 0,
            "instrumentation_slow_count": 1, "instrumentation_severe_count": 0,
            "window": {"observed_s": 2.0}, "top_stacks": [],
        }

    def set_phase(self, name):
        self.phases.append(name)

    def checkpoint(self):
        self.checkpoints += 1
        return {"at": float(self.checkpoints)}

    def summary_since(self, checkpoint):
        return dict(self.window)

    @contextlib.contextmanager
    def instrumentation(self):
        self.instrumentation_entries += 1
        try:
            yield
        finally:
            self.instrumentation_exits += 1


def _make_driver(clock, *, window=None, adapter=None, app=None, jank=None, seconds=2.0, settle_timeout=10.0):
    result = {"steps": [], "failures": []}
    driver = runner._ScenarioDriver(
        app=app or _StubApp(), window=window or _StubWindow(),
        adapter=adapter or _StubAdapter(), jank=jank or _StubJank(),
        fixture=Path("fixture.epub"), seconds=seconds, settle_timeout=settle_timeout,
        result=result, clock=lambda: clock[0])
    return driver, result


def test_scenario_driver_steps_run_from_event_loop_callbacks():
    clock = [0.0]
    window, app, jank = _StubWindow(), _StubApp(), _StubJank()
    adapter = _ProgressingAdapter()
    driver, result = _make_driver(clock, window=window, adapter=adapter, app=app,
                                  jank=jank, seconds=10.0)

    driver.begin()
    assert window.calls[0] == ("import", ("fixture.epub",), False)
    assert driver.state == "import"
    assert app.pumped == 0  # nothing pumped, and the stub would have raised

    # Not imported yet.
    clock[0] += 0.1
    assert driver.tick() is False
    window.runner.busy = False
    clock[0] += 0.2
    assert driver.tick() is False
    assert ("open", "job-1") in window.calls
    assert driver.state == "open"

    clock[0] += 0.2
    driver.tick()
    assert ("start",) in window.calls
    assert driver.state == "interact"

    # Scrolling is one callback per 100 ms, not a processEvents loop.
    clock[0] += 0.11
    driver.tick()
    assert driver.scrolls == 1 and app.pumped == 0

    # Editing relies on the same queued callbacks.
    clock[0] += 2.1
    driver.tick()
    assert driver.edited == 1
    assert ("submit", 0) in window.calls

    # A page switch, again from the timer callback.
    clock[0] += 4.1
    driver.tick()
    assert driver.pages == 1
    assert any(call[0] == "page" for call in window.workbench.calls)

    # v3: once the task is finished the run window closes, and the interaction
    # that continues is measured as the I phase ("complete").
    adapter.done = True
    clock[0] += 0.6
    driver.tick()
    assert driver.run_window is not None and driver.run_window["measured"] is True
    assert driver.run_observed_s is not None and driver.run_observed_s > 0
    assert driver.measurement_phase == "complete"
    clock[0] += 2.1
    driver.tick()
    assert driver.operations.count("type_echo", "complete") == 1
    assert driver.operations.count("type_echo", "run") == 2
    assert driver.edited == 3

    # Past the interaction window -> settle -> terminal predicate -> quit.
    clock[0] += 10.0  # beyond seconds=10 of interaction
    driver.tick()
    assert driver.state == "settle"
    clock[0] += 0.3
    assert driver.tick() is True
    assert driver.finished is True and driver.settled is True
    assert app.quit_calls == 1
    assert result["failures"] == []
    # Predicate polling is marked as instrumentation (harness work), paired.
    assert jank.instrumentation_entries >= 1
    assert jank.instrumentation_entries == jank.instrumentation_exits
    # v3: the run window is frozen when the task is seen settled, and it came
    # from the sampler's checkpoints rather than from the interaction window.
    assert jank.checkpoints >= 1
    assert driver.run_window is not None and driver.run_window["measured"] is True
    assert driver.run_observed_s is not None and driver.run_observed_s > 0
    # Every operation sample carries the phase it happened in.
    assert driver.operations.count("scroll", "run") == 3
    assert driver.operations.count("select_segment", "run") == 2
    assert driver.operations.count("page_switch", "run") == 1
    assert driver.operations.count("open_panel", "run") >= 1
    assert driver.operations.count("type_echo", "complete") == 1
    assert driver.operations.count("open_task", "setup") == 1
    assert driver.operations.count("import", "setup") == 1


def test_scenario_driver_reports_a_stuck_import_instead_of_pumping():
    clock = [0.0]
    driver, result = _make_driver(clock, settle_timeout=1.0)
    driver.begin()
    clock[0] += 0.5
    driver.tick()
    clock[0] += 0.6
    assert driver.tick() is True
    assert driver.settled is False
    assert any("import did not produce a task" in item for item in result["failures"])


def _processevents_calls(function) -> list:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "processEvents"):
            hits.append(node.lineno)
    return hits


def test_measurement_path_never_calls_process_events():
    """The regression guard for #327: the tool must not pump the GUI thread."""
    for function in (runner.run_source, runner.run_source_s3, runner._drive_event_loop):
        assert _processevents_calls(function) == [], function.__name__
    for cls in (runner._ScenarioDriver, runner._TaskSwitchDriver):
        for name, member in vars(cls).items():
            if inspect.isfunction(member):
                assert _processevents_calls(member) == [], f"{cls.__name__}.{name}"
    # Steps are queued on the event loop, and the app's own loop drives them.
    drive = textwrap.dedent(inspect.getsource(runner._drive_event_loop))
    assert "QTimer.singleShot(0, driver.begin)" in drive
    assert "_run_scenario_event_loop(app)" in drive
    assert _processevents_calls(runner._run_scenario_event_loop) == []
    assert "app.exec()" in textwrap.dedent(inspect.getsource(runner._run_scenario_event_loop))


# ---------------------------------------------------------------------------
# Measurement v3 (#347): the report contract, R/I phases, per-operation p95 and
# the tool's own share.  P0-6: v2 reports had no `measurement` field at all and
# nothing failed when it disappeared.
# ---------------------------------------------------------------------------

def _window(**overrides):
    """A window shaped like a measured sampler window (run scope)."""
    block = {
        "enabled": True, "pulse_count": 400, "slow_count": 20, "severe_count": 2,
        "instrumentation_slow_count": 5, "instrumentation_severe_count": 1,
        "observed_s": 60.0, "window": {"observed_s": 60.0}, "top_stacks": [],
        "stall_ms": {"p95": 120.0},
    }
    block.update(overrides)
    return block


def _report(**overrides):
    kwargs = {
        "scenario": "S1",
        "paras": 17,
        "fixtures": [Path("fixture.epub")],
        "result": {},
        "environment": {"mode": "source_offscreen"},
        "started_at": "2026-01-01T00:00:00.000Z",
    }
    kwargs.update(overrides)
    return runner.assemble_report(**kwargs)


def test_report_declares_measurement_v3_and_the_phase_contract():
    payload = _report(result={
        "gui_jank": _window(),
        "run_window": _window(observed_s=12.0, slow_count=6, severe_count=0,
                              instrumentation_slow_count=2,
                              instrumentation_severe_count=0),
        "translation": {"settled": True},
        "operations": runner.OperationRecorder().report(),
    })
    measurement = payload["measurement"]
    assert measurement["version"] == 3
    assert measurement["phases"]["discarded_rounds"] == 0
    assert measurement["operations"]["core"] == sorted(runner.CORE_OPERATIONS)
    assert measurement["tool_share"]["reported_as"]
    phases = payload["metrics"]["phases"]
    for label in ("R", "I"):
        assert phases[label]["measured"] is True, label
        assert set(phases[label]["tiers"]) == {"slow_100ms", "severe_500ms"}
        for tier in phases[label]["tiers"].values():
            assert set(tier) >= {"excluding_tool", "including_tool", "tool_events",
                                 "tool_share"}
    assert phases["valid_round"] is True
    # The five core operations are all present with a per-phase verdict.
    operations = payload["metrics"]["operations"]["operations"]
    for name in runner.CORE_OPERATIONS:
        assert name in operations
        assert operations[name]["core"] is True
        for phase in ("setup", "run", "complete"):
            assert phase in operations[name]["phases"]


def test_a_real_window_run_does_not_carry_the_offscreen_limits(monkeypatch):
    """A real-window round must not describe itself as offscreen (P1-5).

    The report used to print the offscreen limit list unconditionally, so a
    round measured in a real window with animations on carried a limit that did
    not apply to it -- and, worse, hid the one that does (no frozen product).
    """
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    payload = _report(environment={"mode": "source", "offscreen": False})
    joined = " ".join(payload["limits"]).lower()
    assert "offscreen" not in joined, payload["limits"]
    assert "frozen product" in joined, payload["limits"]
    assert "offscreen" not in payload["note"].lower(), payload["note"]
    assert payload["real_machine_baseline"] is False, \
        "a source run is still not a frozen product"
    # The offscreen list is still what an offscreen round reports.
    offscreen = _report(environment={"mode": "source_offscreen", "offscreen": True})
    assert "offscreen" in " ".join(offscreen["limits"]).lower()


def test_report_records_whether_animations_were_on(monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "0")
    on = _report(environment={"mode": "source", "offscreen": False})
    assert on["motion_enabled"] is True, on["motion"]
    assert on["motion"]["source"] == "FOLITH_REDUCE_MOTION"

    monkeypatch.setenv("FOLITH_REDUCE_MOTION", "1")
    off = _report(environment={"mode": "source", "offscreen": False})
    assert off["motion_enabled"] is False, off["motion"]

    # Unset + a real window: the system decides, and guessing would be a lie.
    monkeypatch.delenv("FOLITH_REDUCE_MOTION", raising=False)
    monkeypatch.delenv("FOLITH_QT_NONINTERACTIVE", raising=False)
    unknown = _report(environment={"mode": "source", "offscreen": False})
    assert unknown["motion_enabled"] is None, unknown["motion"]
    assert "system" in unknown["motion"]["source"]


def test_report_without_a_measured_window_is_not_a_zero_measurement():
    """F5 at the report level: no window must not become an all-zero R and I."""
    payload = _report(result={"gui_jank": {}, "translation": {"settled": False}})
    assert payload["metrics"]["gui_jank"]["measured"] is False
    assert payload["metrics"]["gui_jank"]["reason"]
    phases = payload["metrics"]["phases"]
    assert phases["R"]["measured"] is False
    assert phases["I"]["measured"] is False
    assert phases["valid_round"] is False
    assert phases["discarded_rounds"] == 1


def test_phases_split_run_and_completion_with_and_without_the_tool():
    scenario = _window(observed_s=60.0, slow_count=30, severe_count=6,
                       instrumentation_slow_count=6, instrumentation_severe_count=1)
    run = _window(observed_s=12.0, slow_count=12, severe_count=1,
                  instrumentation_slow_count=4, instrumentation_severe_count=0)
    phases = runner._phase_metrics(scenario, run)

    assert phases["R"]["observed_s"] == 12.0
    slow = phases["R"]["tiers"]["slow_100ms"]
    assert slow["excluding_tool"] == {"count": 12, "per_minute": 60.0}
    assert slow["including_tool"] == {"count": 16, "per_minute": 80.0}
    assert slow["tool_events"] == 4
    assert slow["tool_share"] == 0.25

    assert phases["i_phase_s"] == 48.0
    after = phases["I"]["tiers"]["slow_100ms"]
    assert after["excluding_tool"] == {"count": 18, "per_minute": 22.5}
    assert after["including_tool"] == {"count": 20, "per_minute": 25.0}
    severe = phases["I"]["tiers"]["severe_500ms"]
    assert severe["excluding_tool"] == {"count": 5, "per_minute": 6.25}
    assert severe["including_tool"] == {"count": 6, "per_minute": 7.5}
    assert phases["valid_round"] is True
    assert phases["discarded_rounds"] == 0


def test_a_round_with_a_short_i_phase_is_void_and_counted():
    scenario = _window(observed_s=60.0, slow_count=30, severe_count=6)
    run = _window(observed_s=50.0, slow_count=12, severe_count=1)
    phases = runner._phase_metrics(scenario, run)
    assert phases["i_phase_s"] == 10.0
    assert phases["I"]["valid"] is False
    assert "shorter than the 15 s minimum" in phases["I"]["invalid_reason"]
    assert phases["valid_round"] is False
    assert phases["discarded_rounds"] == 1
    assert phases["discard_reasons"]


def test_scenario_without_a_translation_window_reports_r_as_not_applicable():
    """S3 has no translating phase: R must say so, I covers the whole window."""
    phases = runner._phase_metrics(
        _window(observed_s=45.0, slow_count=9, severe_count=1),
        {"measured": False, "present": False, "reason": "S3 has no translating phase"})
    assert phases["R"]["measured"] is False
    assert "S3 has no translating phase" in phases["R"]["reason"]
    assert phases["I"]["measured"] is True
    assert phases["I"]["window"] == "scenario_window"
    assert phases["I"]["tiers"]["slow_100ms"]["excluding_tool"]["per_minute"] == 12.0
    assert phases["valid_round"] is True


def test_runner_frame_attribution_does_not_inflate_the_tool_share():
    """A stall captured inside the harness is named, but counted once."""
    window = _window(top_stacks=[
        {"stack": ["a.py:1 in f", "runtime_baseline_runner.py:310 in _interact"],
         "count": 7, "total_ms": 900.0, "max_ms": 200.0},
        {"stack": ["a.py:1 in f", "runtime_baseline_runner.py:433 in _run_scenario_event_loop"],
         "count": 19, "total_ms": 3474.6, "max_ms": 321.3},
    ])
    phase = runner._phase_block("翻译中", window, 10.0, window_name="run_window",
                                source="test")
    # Only the harness's own frame counts; the app's event loop is the product.
    assert phase["runner_frame_events"] == 7
    assert phase["runner_frame_share_of_product"] == round(7 / 20, 4)
    tier = phase["tiers"]["slow_100ms"]
    # including_tool is product + marked instrumentation only — still reconcilable.
    assert tier["including_tool"]["count"] == window["slow_count"] + window["instrumentation_slow_count"]


def test_operation_percentiles_are_per_phase_and_never_a_zero_latency():
    recorder = runner.OperationRecorder()
    for value in (30.0, 40.0, 45.0, 48.0, 49.0):
        recorder.add("scroll", "run", value)
    recorder.add("scroll", "complete", 12.0)
    reporter = recorder.report()
    scroll = reporter["operations"]["scroll"]

    running = scroll["phases"]["run"]
    assert running["measured"] is True
    assert running["count"] == 5
    assert running["p95"] == 49.0
    assert running["budget_ms"] == 50.0
    assert running["p95_within_budget"] is True

    # A phase with no sample is unmeasured, not "0 ms".
    setup = scroll["phases"]["setup"]
    assert setup["measured"] is False
    assert setup["reason"]
    assert "p95" not in setup

    assert reporter["operations"]["scroll"]["phases"]["complete"]["count"] == 1
    assert "select_segment" in reporter["missing"]
    assert set(reporter["operations"]) >= set(runner.CORE_OPERATIONS)


def test_operation_budget_failure_is_reported_as_a_failure():
    recorder = runner.OperationRecorder()
    for value in (120.0, 140.0, 160.0):
        recorder.add("page_switch", "complete", value)
    page = recorder.report()["operations"]["page_switch"]["phases"]["complete"]
    assert page["p95"] == 160.0
    assert page["p95_within_budget"] is False
    assert page["first_ms"] == 120.0
    assert page["first_within_budget"] is True


# ---------------------------------------------------------------------------
# S3: open a finished large task and switch tasks
# ---------------------------------------------------------------------------

def test_s3_builds_finished_tasks_then_measures_open_and_switch():
    clock = [0.0]
    adapter = _ImportingAdapter()
    window = _StubWindow(adapter=adapter)
    jank = _StubJank(window=_window(observed_s=30.0, slow_count=4, severe_count=0,
                                    instrumentation_slow_count=1))
    result = {"steps": [], "failures": []}
    driver = runner._TaskSwitchDriver(
        app=_StubApp(), window=window, adapter=adapter, jank=jank,
        fixtures=[Path("s3-big.epub"), Path("s3-small.epub")],
        seconds=20.0, settle_timeout=60.0, result=result, clock=lambda: clock[0])

    driver.begin()
    assert ("import", ("s3-big.epub",), False) == window.calls[0]
    ticks = 0
    while not driver.finished and ticks < 500:
        ticks += 1
        clock[0] += 0.3
        driver.tick()

    assert driver.finished is True, result["failures"]
    assert result["failures"] == []
    assert driver.big_job_id == "job-1" and driver.small_job_id == "job-2"
    # Both tasks were translated before the measurement window opened.
    assert any(step["step"] == "built" for step in result["steps"])
    assert any(step["step"] == "measure_started" for step in result["steps"])
    assert driver.scenario_window is not None
    assert driver.scenario_window["measured"] is True
    # Opening and then switching between the finished tasks were both measured.
    assert driver.cycles >= 2
    assert driver.operations.count("open_task", "complete") >= 1
    assert driver.operations.count("switch_task", "complete") >= 1
    # Every core operation was sampled on a finished task.
    for name in runner.CORE_OPERATIONS:
        assert driver.operations.count(name, "complete") >= 1, name
    # Nothing was charged to a translating phase in S3.
    assert driver.operations.count("scroll", "run") == 0
    assert driver.measure_observed_s >= runner.I_PHASE_MIN_SECONDS


def test_s3_report_declares_r_not_applicable():
    payload = _report(scenario="S3", fixtures=[Path("s3-big.epub")], result={
        "gui_jank": _window(observed_s=45.0, slow_count=9, severe_count=1),
        "run_window": {"measured": False, "present": False,
                       "reason": "S3 has no translating phase"},
        "translation": {"settled": True, "completed": True,
                        "mode": "s3_finished_tasks"},
        "operations": runner.OperationRecorder().report(),
    })
    assert payload["measurement"]["version"] == 3
    assert payload["metrics"]["phases"]["R"]["measured"] is False
    assert payload["metrics"]["phases"]["I"]["measured"] is True
    assert payload["ok"] is True


def test_a_failed_translation_is_not_a_measured_round():
    """A task that settled by *failing* must not report ok (P1-5 wiring trap).

    ``settled`` is terminal-state, not success: with the replay gateway down the
    task reached ``failed`` immediately, and a report that keyed ``ok`` on
    ``settled`` called that run healthy.
    """
    payload = _report(result={
        "gui_jank": _window(observed_s=60.0),
        "run_window": _window(observed_s=3.0),
        "translation": {"settled": True, "completed": False,
                        "runtime_status": "failed", "p2_done": False},
        "run_records": [],
    })
    assert payload["ok"] is False
    assert payload["translation"]["runtime_status"] == "failed"


def test_longest_consumes_the_repaired_window_scoped_sampler_field():
    """F1 (P1-1): once the sampler reports a suffix-maxima chain, trust it."""
    repaired = _window(
        longest={"interval_ms": 596.4, "blocked_ms": 571.4, "severity": "severe",
                 "at": "2026-01-01T00:00:00.000Z",
                 "stack": ["a.py:1 in f"]},
        notes=["longest is the largest non-instrumentation stall in the window, "
               "taken from the suffix-maxima chain (not from the bounded recent ring)"])
    summary = runner._longest_summary(repaired["longest"], repaired)
    assert summary["exact"] is True
    assert "suffix-maxima" in summary["exact_reason"]

    # The pre-P1-1 build derived it from the bounded ring: say so, do not pretend.
    legacy = _window(longest={"interval_ms": 320.0, "stack": []})
    old_summary = runner._longest_summary(legacy["longest"], legacy)
    assert old_summary["exact"] is None
    assert "bounded event ring" in old_summary["exact_reason"]


def test_aggregate_medians_skip_void_and_foreign_reports(tmp_path):
    def write(name, *, scenario="S1", run_s, slow_run, slow_scn, version=3):
        directory = tmp_path / name
        directory.mkdir()
        result = {
            "gui_jank": _window(observed_s=60.0, slow_count=slow_scn, severe_count=0),
            "run_window": _window(observed_s=run_s, slow_count=slow_run, severe_count=0),
            "translation": {"settled": True, "completed": True},
        }
        payload = _report(scenario=scenario, result=result)
        payload["measurement"]["version"] = version
        (directory / "runtime-baseline.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    write("r1", run_s=10.0, slow_run=10, slow_scn=30)
    write("r2", run_s=10.0, slow_run=14, slow_scn=34)
    write("void", run_s=55.0, slow_run=4, slow_scn=30)      # I phase 5 s < 15 s
    write("v2", run_s=10.0, slow_run=99, slow_scn=99, version=2)

    summary = runner.aggregate_reports(tmp_path)
    assert summary["rounds"] == 2
    assert summary["mixed_scenarios"] is False
    # 10/10 s = 60/min and 14/10 s = 84/min -> median 72
    assert summary["medians"]["R_slow_100ms_excluding_tool_per_minute"] == 72.0
    reasons = " ".join(item["reason"] for item in summary["skipped"])
    assert "not v3" in reasons
    assert "15 s minimum" in reasons
    assert summary["voided_rounds"] == 1

    write("s3", scenario="S3", run_s=10.0, slow_run=10, slow_scn=30)
    mixed = runner.aggregate_reports(tmp_path)
    assert mixed["mixed_scenarios"] is True
    assert "mix scenarios" in mixed["warning"]


# ---------------------------------------------------------------------------
# P1-5 prep: the two wiring faults the smoke run hit
# ---------------------------------------------------------------------------

def test_frozen_arm_reports_tool_share_as_null_not_zero():
    """A frozen round cannot separate the tool's stalls, so 0.0 would be a lie.

    The app's own scripted flow takes no instrumentation mark, so its counters are
    all zero even when the harness *would* have caused stalls (P1-2 measured 25 %
    and 100 % tool shares in the source arm).  Reading that zero as "pure product"
    inverts the baseline/candidate comparison, so the field is null with a reason.
    """
    window = _window(observed_s=60.0, slow_count=7, severe_count=2,
                     instrumentation_slow_count=0, instrumentation_severe_count=0)
    frozen = runner._phase_metrics(window, window, tool_split_measured=False)
    assert frozen["tool_split_measured"] is False
    assert frozen["tool_split_reason"]
    for label in ("R", "I"):
        for tier in frozen[label]["tiers"].values():
            assert tier["tool_split_measured"] is False
            assert tier["tool_share"] is None
            assert tier["tool_events"] is None
            assert tier["including_tool"]["count"] is None
            assert tier["including_tool"]["reason"]
    # The product-side reading survives: the scenario really had 7 slow stalls.
    r_slow = frozen["R"]["tiers"]["slow_100ms"]["excluding_tool"]
    assert r_slow == {"count": 7, "per_minute": 7.0}

    # The same window measured with the harness present keeps the full split.
    source = runner._phase_metrics(window, window)
    assert source["tool_split_measured"] is True
    assert source["R"]["tiers"]["slow_100ms"]["tool_share"] == 0.0


def test_a_report_says_whether_seconds_controlled_the_window():
    """``--seconds`` is harness-only; the frozen arm must not imply it applied."""
    frozen = _report(result={
        "gui_jank": _window(),
        "run_window": _window(observed_s=12.0),
        "translation": {"settled": True, "completed": True},
        "tool_split_measured": False,
        "window_control": {"controlled_by": "the frozen app's own --selftest flow",
                           "seconds_applied": False},
    })
    assert frozen["window_control"]["seconds_applied"] is False
    assert frozen["measurement"]["tool_share"]["measured"] is False
    line = runner._summary_line(frozen)
    assert line["seconds_applied"] is False
    assert line["tool_split_measured"] is False
    for name, value in line.items():
        if name.endswith("_tool_share"):
            assert value is None, name

    source = _report(result={
        "gui_jank": _window(),
        "run_window": _window(observed_s=12.0),
        "translation": {"settled": True, "completed": True},
        "tool_split_measured": True,
        "window_control": {"controlled_by": "the harness interaction window",
                           "seconds_applied": True},
    })
    assert source["window_control"]["seconds_applied"] is True
    assert runner._summary_line(source)["seconds_applied"] is True


def test_frozen_mode_refuses_seconds_instead_of_ignoring_it():
    """P1-4 §5: ``--seconds 15`` never moved the frozen window.  Refuse now."""
    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--out", "/tmp/p15-unused", "--mode", "frozen",
                     "--frozen-app", "/bin/true", "--seconds", "30"])
    assert excinfo.value.code == 2


def test_the_driver_does_not_edit_before_the_table_has_rows():
    """Right after open the row list is filled before QTableWidget is resized.

    Selecting then paints a row whose items do not exist yet and the product
    raises ``'NoneType' object has no attribute 'text'`` (views.py:6097).  That is
    not the editing scenario, so no edit is attempted and no failure is recorded.
    """
    clock = [0.0]
    window = _StubWindow()
    window.workbench.segment_list = SimpleNamespace(
        verticalScrollBar=lambda: _StubScrollBar(), rowCount=lambda: 0)
    driver, result = _make_driver(clock, window=window,
                                  adapter=_ProgressingAdapter(), seconds=10.0)
    driver.begin()
    window.runner.busy = False
    clock[0] += 0.3
    driver.tick()          # import -> open
    clock[0] += 0.2
    driver.tick()          # open -> interact
    assert driver.state == "interact"

    clock[0] += 2.1
    driver.tick()
    assert driver.edited == 0
    assert not any(call[0] == "select" for call in window.workbench.calls)
    assert result["failures"] == []

    # Once the table catches up, the very next slot performs the edit.
    window.workbench.segment_list = SimpleNamespace(
        verticalScrollBar=lambda: _StubScrollBar(), rowCount=lambda: 10)
    clock[0] += 2.1
    driver.tick()
    assert driver.edited == 1
    assert ("submit", 0) in window.calls


# ---------------------------------------------------------------------------
# --verify: the one-command post-round contract check (P1-5 runbook step)
# ---------------------------------------------------------------------------

def _write_round(directory: Path, *, result: dict, mode: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = _report(result=result, environment={"mode": mode})
    (directory / "runtime-baseline.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return directory


def _source_result():
    return {
        "gui_jank": _window(observed_s=60.0),
        "run_window": _window(observed_s=12.0),
        "translation": {"settled": True, "completed": True},
        "tool_split_measured": True,
        "window_control": {"controlled_by": "the harness interaction window",
                           "seconds_applied": True},
        "run_records": [],
    }


def _frozen_result():
    return {
        "gui_jank": _window(observed_s=60.0),
        "run_window": _window(observed_s=60.0),
        "translation": {"settled": True, "completed": True},
        "tool_split_measured": False,
        "window_control": {"controlled_by": "the frozen app's own --selftest flow",
                           "seconds_applied": False},
        "run_records": [],
    }


def test_verify_accepts_a_source_round_and_calls_it_aggregate_ready(tmp_path):
    _write_round(tmp_path / "r1", result=_source_result(), mode="source_offscreen")
    verdict = runner.verify_round(tmp_path / "r1")
    failed = [item["check"] for item in verdict["checks"] if not item["ok"]]
    # The product artifacts are missing in this synthetic round; everything the
    # harness itself owns must pass, and I is required because --seconds applied.
    assert "measurement_v3" not in failed
    assert "R_valid" not in failed
    assert "I_valid" not in failed
    assert verdict["seconds_applied"] is True
    assert verdict["aggregate_ready"] is True


def test_verify_does_not_require_an_i_phase_from_the_frozen_arm(tmp_path):
    _write_round(tmp_path / "frozen", result=_frozen_result(), mode="frozen_selftest")
    verdict = runner.verify_round(tmp_path / "frozen")
    by_name = {item["check"]: item for item in verdict["checks"]}
    assert by_name["I_valid"]["required"] is False
    assert by_name["tool_share_honest"]["ok"] is True
    assert by_name["window_control_recorded"]["ok"] is True
    # R-only, so the aggregation view must not pretend it is a full round.
    assert verdict["aggregate_ready"] is False


def test_verify_rejects_a_frozen_round_that_reports_a_tool_share_of_zero(tmp_path):
    result = _frozen_result()
    payload = _report(result=result, environment={"mode": "frozen_selftest"})
    # Simulate the pre-fix report: the frozen arm printed 0.0 for the share.
    for label in ("R", "I"):
        for tier in payload["metrics"]["phases"][label]["tiers"].values():
            tier["tool_share"] = 0.0
            tier["tool_events"] = 0
    directory = tmp_path / "bad"
    directory.mkdir()
    (directory / "runtime-baseline.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    verdict = runner.verify_round(directory)
    by_name = {item["check"]: item for item in verdict["checks"]}
    assert by_name["tool_share_honest"]["ok"] is False
    assert verdict["ok"] is False


def test_verify_fails_when_the_round_has_no_report(tmp_path):
    verdict = runner.verify_round(tmp_path)
    assert verdict["ok"] is False
    assert verdict["checks"][0]["check"] == "report_present"
    assert runner.main(["--verify", str(tmp_path)]) == 1
