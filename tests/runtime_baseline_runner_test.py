"""The runtime baseline harness must declare what it did not measure.

Only the pure parts run here: S1's Qt window is exercised by
``tests/jank_monitor_test.py`` (sampler) and by running the script itself.  What
this file pins down is that an unimplemented scenario reports itself as such
instead of writing a report that looks measured, and that the fixture is the
deterministic EPUB the scenario promises.
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

    measured = {"measured": True, "slow_count": 2, "top_stacks": []}
    chosen = runner._stall_window_from_records(
        [{"gui_jank": {"measured": False}}, {"gui_jank": measured}])
    assert chosen == measured


def test_disabled_sampler_window_says_not_measured():
    """`enabled: false` is not the same statement as `measured: false`."""
    block = runner._normalize_window({"enabled": False, "slow_count": 0})
    assert block["measured"] is False
    assert "FOLITH_JANK_MONITOR" in block["reason"]
    measured = runner._normalize_window({"enabled": True, "slow_count": 0})
    assert measured["measured"] is True


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
    def __init__(self):
        self.segment_list = SimpleNamespace(verticalScrollBar=lambda: _StubScrollBar())
        self.calls = []

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
    def __init__(self):
        self.runner = SimpleNamespace(busy=True)
        self.workbench = _StubWorkbench()
        self._opening_job_id = ""
        self._current_job_id = ""
        self.calls = []

    def import_paths(self, paths, autostart=False):
        self.calls.append(("import", tuple(paths), autostart))

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

    def list_jobs(self):
        return list(self._jobs)

    def load_state(self, job_id):
        return {"p2_done": True}


class _StubJank:
    def __init__(self):
        self.phases = []
        self.instrumentation_entries = 0
        self.instrumentation_exits = 0

    def set_phase(self, name):
        self.phases.append(name)

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
    adapter = _StubAdapter()
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
    assert _processevents_calls(runner.run_source) == []
    for name, member in vars(runner._ScenarioDriver).items():
        if inspect.isfunction(member):
            assert _processevents_calls(member) == [], name
    source = textwrap.dedent(inspect.getsource(runner.run_source))
    # Steps are queued on the event loop, and the app's own loop drives them.
    assert "QTimer.singleShot(0, driver.begin)" in source
    assert "_run_scenario_event_loop(app)" in source
    assert _processevents_calls(runner._run_scenario_event_loop) == []
    assert "app.exec()" in textwrap.dedent(inspect.getsource(runner._run_scenario_event_loop))
