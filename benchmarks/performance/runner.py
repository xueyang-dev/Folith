"""Scenario driver for the Folith performance benchmark.

The benchmark runs the **real** Qt window and the **real** core worker.  The
only substitution is the model: ``tests/offline_provider.py`` answers every
prompt deterministically, so a run needs no network and always does the same
amount of work.

What is measured while the background task runs:

* Qt event-loop scheduling delay (how late the event loop is),
* GUI-thread cost of every foreground interaction,
* process CPU / RSS / thread count,
* paint events produced by the window,
* how often the GUI re-reads the task state and re-rendered the workbench.

The window runs under a **real** Qt event loop (``QApplication.exec()``); the
interactions are delivered by a driver timer on the GUI thread, exactly like
a user clicking.  Nothing busy-polls ``processEvents``, because that would
invent repaints that the shipping application never produces.

Usage::

    python -m benchmarks.performance.run_benchmark --scenario translate \
        --segments 400 --out benchmarks/performance/baseline.json
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import fixtures
from .metrics import (EventLoopProbe, PaintCounter, ProcessSampler,
                      percentiles, write_json)

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))


PROVIDER = "OfflineFixture"
MODEL = "offline-fixture"
TARGET_LANG = "简体中文"

#: How long after an interaction the event-loop stall is attributed to it.
STALL_ATTRIBUTION_WINDOW_S = 0.25

#: The wall-clock window the "before" and "after" numbers are compared over.
#: A run that finishes the same work faster must still be judged over the
#: same seconds, otherwise the faster run is measured over a period that
#: contains a different amount of work.
COMPARISON_WINDOW_SECONDS = 30.0


# --------------------------------------------------------------------- setup


def _prepare_environment(workdir: Path, platform: str = "offscreen") -> None:
    """Pin the data/log directories *before* ``core`` is imported.

    ``core.OUTPUT_DIR`` is read from the environment at import time, so this
    has to happen first or the benchmark would write into the developer's real
    Folith data directory.
    """
    data_dir = workdir / "data"
    log_dir = workdir / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["FOLITH_OUTPUT_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(log_dir)
    os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
    os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    os.environ["QT_QPA_PLATFORM"] = platform


# ----------------------------------------------------------------- scenario


@dataclass
class Scenario:
    """One measured situation."""

    name: str
    background: str          # idle | translate | review | export
    segments: int = 400
    duration_s: float = 25.0
    description: str = ""
    actions: List[str] = field(default_factory=list)


@dataclass
class ActionResult:
    name: str
    started_at: float
    handler_ms: float


class InteractionDriver:
    """Delivers the foreground interactions from the Qt event loop.

    The driver is a timer on the GUI thread: each tick performs one atomic
    user gesture.  The gap between ticks is short so the interactions feel
    like a user working continuously, and the event-loop probe runs in the
    same loop and therefore sees the cost of every gesture.

    Each gesture has a minimum cadence, so the benchmark does not hammer
    actions the real application only performs occasionally (the task list
    is refreshed every five seconds, not every frame).
    """

    def __init__(self, session: "BenchSession") -> None:
        from PySide6.QtCore import QObject, QTimer

        self.session = session
        self._timer = QTimer(session.window)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        self.catalogue: List[tuple] = []
        self.index = 0
        self.deadline = 0.0
        self.finished = False
        self._last_run: Dict[str, float] = {}
        self._ticks = 0

    def start(self, duration_s: float) -> None:
        self.deadline = time.perf_counter() + duration_s
        self.catalogue = self.session.catalogue()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        if now >= self.deadline:
            self._finish()
            return
        # A background scenario is measured for as long as the background task
        # runs (up to the cap): comparing runs that did different amounts of
        # pipeline work would not be a comparison.  The liveness probe is a
        # lock check, so it is not asked for on every tick.
        self._ticks += 1
        if self.session.scenario.background != "idle" and self.session.worker_started \
                and self._ticks % 25 == 0 \
                and not self.session.worker_running():
            self._finish()
            return
        for offset in range(len(self.catalogue)):
            index = (self.index + offset) % len(self.catalogue)
            name, action, interval = self.catalogue[index]
            last = self._last_run.get(name)
            if last is not None and (now - last) < interval:
                continue
            self.index = index + 1
            self._last_run[name] = now
            self.session.measure(name, action)
            return

    def _finish(self) -> None:
        """Stop measuring, then close the window.

        The exit flow (flushing drafts, releasing the worker) is measured on
        its own: it is a deliberate user action, not part of the interactive
        responsiveness of a running task.
        """
        self.stop()
        self.finished = True
        self.session.probe.stop()
        started = time.perf_counter()
        self.session.window.close()
        self.session.shutdown_seconds = time.perf_counter() - started


class BenchSession:
    """Owns the application, the window, the worker and the instruments."""

    def __init__(self, workdir: Path, scenario: Scenario) -> None:
        self.workdir = workdir
        self.scenario = scenario
        self.job_id = f"bench-{scenario.name}-{scenario.segments}"
        self.actions: List[ActionResult] = []
        self.counters: Dict[str, int] = {}
        self._counter_lock = threading.Lock()
        #: GUI-thread operations long enough to be felt (>50 ms).
        self.long_operations: List[Dict[str, Any]] = []
        #: Total GUI-thread time per instrumented operation.
        self.gui_thread_ms: Dict[str, float] = {}
        self._window_start = time.perf_counter()
        #: How long the exit flow took (measured on its own).
        self.shutdown_seconds = 0.0
        #: True once the background worker has been started (the driver stops
        #: measuring when it has finished, so every run does the same work).
        self.worker_started = False
        self.worker_finished_at: float = 0.0

    # -- instrumentation --------------------------------------------------
    def bump(self, name: str, amount: int = 1) -> None:
        with self._counter_lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def _install_counters(self) -> None:
        """Count the calls that the poll path makes on every refresh."""
        from folith.qt import adapter
        from folith.qt import views

        original_load_state = adapter.load_state
        original_apply = views.WorkbenchView.apply
        original_list_jobs = adapter.list_jobs
        original_render_segments = views.WorkbenchView._render_segments
        original_render_delivery = views.WorkbenchView._render_delivery
        session = self

        def load_state(*args, **kwargs):
            session.bump("adapter.load_state")
            return original_load_state(*args, **kwargs)

        def apply(workbench, *args, **kwargs):
            session.bump("workbench.apply")
            return session._timed("views.apply", original_apply, workbench, *args, **kwargs)

        def list_jobs(*args, **kwargs):
            return session._timed("adapter.list_jobs", original_list_jobs, *args, **kwargs)

        def render_segments(workbench, *args, **kwargs):
            return session._timed("views._render_segments", original_render_segments,
                                   workbench, *args, **kwargs)

        def render_delivery(workbench, *args, **kwargs):
            return session._timed("views._render_delivery", original_render_delivery,
                                   workbench, *args, **kwargs)
        adapter.load_state = load_state
        adapter.list_jobs = list_jobs
        views.WorkbenchView.apply = apply
        views.WorkbenchView._render_segments = render_segments
        views.WorkbenchView._render_delivery = render_delivery

    def _timed(self, label: str, fn, *args, **kwargs):
        """Record the GUI-thread operations and how long they took.

        The total is the answer to "how much of the GUI thread did the
        background task cost the user?": it is the time the window was
        unavailable, summed over every refresh.
        """
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            with self._counter_lock:
                self.gui_thread_ms[label] = self.gui_thread_ms.get(label, 0.0) + elapsed
                if elapsed > 50.0:
                    self.long_operations.append({
                        "label": label, "ms": round(elapsed, 1),
                        "at": round(time.perf_counter() - self._window_start, 3),
                        "thread": threading.current_thread().name,
                    })

    # -- lifecycle --------------------------------------------------------
    def prepare(self) -> None:
        """Build the document, create the job and boot the window."""
        import core

        self._install_counters()
        source_dir = self.workdir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        self.source_path = fixtures.build_docx(
            source_dir / f"bench-{self.scenario.segments}.docx",
            self.scenario.segments)
        self.file_bytes = self.source_path.read_bytes()

        state = core.new_job_state(self.source_path.name)
        core.save_job_state(self.job_id, state)
        core.save_source(self.job_id, self.file_bytes)

        from folith.qt import bootstrap
        from folith.qt import window as window_module

        self.paths = bootstrap.current_paths()
        self.app = window_module.build_application([])
        self.paint_counter = PaintCounter(self.app)
        self.app.installEventFilter(self.paint_counter)
        self.probe = EventLoopProbe(self.app, interval_ms=10)

        config = _engine_config()
        self.window = window_module.MainWindow(config, self.paths, None)
        self.window.resize(1400, 900)
        self.window.show()
        self._settle(1.5)

    def open_job(self) -> None:
        self.window.open_job(self.job_id)
        self._settle(1.5)

    # -- background work --------------------------------------------------
    def start_worker(self) -> None:
        """Start the real pipeline worker with the deterministic provider."""
        import core
        from offline_provider import OfflineProvider

        provider = OfflineProvider()
        self.provider = provider
        core.call_llm = provider
        pipeline_kwargs = {
            "provider": PROVIDER,
            "api_key": "offline",
            "model": MODEL,
            "target_lang": TARGET_LANG,
            "auto_term": False,
            "enable_report": False,
            "translation_theory": "",
            "user_glossary": [],
            "style_rules": "保持学术书面语。",
            "enable_review": True,
            "enable_annotate": False,
            "use_tm": True,
            "strict_terminology_governance": False,
            "delivery_config": {"deliver_report": False},
        }
        started = core.start_job_worker(
            self.job_id, self.source_path.name, None, pipeline_kwargs)
        if not started:
            raise RuntimeError("the background worker refused to start")
        self.worker_started = True

    def worker_running(self) -> bool:
        import core
        return bool(core.is_job_worker_alive(self.job_id))
    # -- foreground interactions -----------------------------------------
    def _settle(self, seconds: float) -> None:
        """Let the window finish its pending layout before the measurement.

        This runs *before* the real event loop starts, so it cannot influence
        the measured window.
        """
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            self.app.processEvents()
            time.sleep(0.005)

    def measure(self, name: str, action: Callable[[], None]) -> None:
        """Run one foreground interaction and record what the user would feel.

        ``handler_ms`` is the GUI-thread time the interaction itself took.
        The paint it produces is delivered by the event loop right after and
        shows up in the event-loop probe; :meth:`collect` attributes the worst
        stall in the following window back to this interaction.
        """
        started = time.perf_counter()
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - a broken action is a result
            self.bump(f"action_error:{name}")
            self.bump(f"action_error_detail:{type(exc).__name__}")
            return
        elapsed = (time.perf_counter() - started) * 1000.0
        self.actions.append(ActionResult(name, started, elapsed))

    # -- interaction catalogue -------------------------------------------
    # Each entry is one atomic user gesture.
    def action_navigate_projects(self) -> None:
        self.window.stack.setCurrentWidget(self.window.projects)

    def action_navigate_home(self) -> None:
        self.window.stack.setCurrentWidget(self.window.home)

    def action_navigate_workbench(self) -> None:
        self.window.stack.setCurrentWidget(self.window.workbench)

    def action_scroll_table(self) -> None:
        table = self.window.workbench.segment_list
        bar = table.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        bar.setValue(bar.value() + max(1, bar.maximum() // 4))

    def action_select_segment(self) -> None:
        table = self.window.workbench.segment_list
        count = table.rowCount()
        if count <= 0:
            return
        table.selectRow(min(count - 1, table.currentRow() + 1))

    def action_edit_segment(self) -> None:
        """Type into the inline editor; the autosave path picks it up."""
        workbench = self.window.workbench
        editor = getattr(workbench, "inline_editor", None)
        if editor is None:
            return
        text_edit = getattr(editor, "editor", None) or getattr(editor, "target", None)
        if text_edit is None:
            return
        text_edit.setPlainText("基准测试译文 / benchmark translation")

    def action_search(self) -> None:
        workbench = self.window.workbench
        setter = getattr(workbench, "set_search_text", None)
        if setter is None:
            return
        setter("restoration")

    def action_home_refresh(self) -> None:
        """The 5-second home-page refresh: re-reads every job on disk."""
        self.window.refresh_home()

    def action_open_settings(self) -> None:
        """Switch to the settings tab of the workbench."""
        workbench = self.window.workbench
        pages = getattr(workbench, "_pages", {}) or {}
        page = pages.get("settings")
        if page is not None:
            workbench.tabs.setCurrentWidget(page)

    def action_export_in_background(self) -> None:
        """Start a delivery build in the background (the DOCX/PDF path)."""
        from folith.qt import adapter

        def build():
            try:
                adapter.delivery_preview(self.job_id, self.window.config)
            except Exception:  # noqa: BLE001
                pass

        thread = threading.Thread(target=build, daemon=True)
        thread.start()
        self.export_thread = thread

    # -- the interaction script ------------------------------------------
    def catalogue(self) -> List[tuple]:
        """(name, action, minimum cadence in seconds) for each gesture.

        The cadences mirror the application: the task list is refreshed every
        five seconds, a search is a deliberate action, and navigation,
        scrolling and selection happen continuously.
        """
        catalogue: List[tuple] = [
            ("navigate_workbench", self.action_navigate_workbench, 0.0),
            ("navigate_projects", self.action_navigate_projects, 0.0),
            ("navigate_home", self.action_navigate_home, 0.0),
            ("scroll_table", self.action_scroll_table, 0.0),
            ("select_segment", self.action_select_segment, 0.0),
            ("home_refresh", self.action_home_refresh, 5.0),
        ]
        if self.scenario.background in {"review", "translate"}:
            catalogue.append(("edit_segment", self.action_edit_segment, 0.5))
        if self.scenario.background == "export":
            catalogue.append(("navigate_projects", self.action_navigate_projects, 0.0))
        if self.scenario.segments >= 200:
            catalogue.append(("search", self.action_search, 2.0))
        catalogue.append(("open_settings", self.action_open_settings, 3.0))
        return catalogue

    # -- results ----------------------------------------------------------
    def collect(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        window_start = self.probe.samples[0][0] if self.probe.samples else 0.0
        window_end = window_start + COMPARISON_WINDOW_SECONDS
        by_name: Dict[str, List[float]] = {}
        stalls: Dict[str, List[float]] = {}
        windowed: Dict[str, List[float]] = {}
        for item in self.actions:
            by_name.setdefault(item.name, []).append(item.handler_ms)
            window_end_item = item.started_at + STALL_ATTRIBUTION_WINDOW_S
            worst = 0.0
            for stamp, gap, _delay in self.probe.samples:
                if item.started_at <= stamp <= window_end_item:
                    worst = max(worst, gap)
            stalls.setdefault(item.name, []).append(worst)
            if item.started_at <= window_end:
                windowed.setdefault(item.name, []).append(item.handler_ms)
        interactions = {
            name: {"handler_ms": percentiles(values),
                   "stall_ms": percentiles(stalls.get(name, []))}
            for name, values in by_name.items()
        }
        all_handler = [item.handler_ms for item in self.actions]
        all_stalls = [max(values) for values in stalls.values()] if stalls else []
        window_handler = [value for values in windowed.values() for value in values]
        return {
            "scenario": self.scenario.name,
            "background": self.scenario.background,
            "segments": self.scenario.segments,
            "description": self.scenario.description,
            "interactions": interactions,
            "interaction_handler_ms": percentiles(all_handler),
            "interaction_stall_ms": percentiles(all_stalls),
            "event_loop": self.probe.report(),
            "comparison_window_seconds": COMPARISON_WINDOW_SECONDS,
            "event_loop_in_window": self.probe.report(until=window_end),
            "interaction_handler_ms_in_window": percentiles(window_handler),
            "paints": self.paint_counter.count,
            "counters": dict(self.counters),
            "gui_thread_ms": {key: round(value, 1)
                              for key, value in self.gui_thread_ms.items()},
            "long_operations": self.long_operations[:40],
            "big_stalls": [
                {"at": round(stamp - window_start, 3), "gap_ms": round(gap, 1)}
                for stamp, gap, _delay in self.probe.samples if gap > 150.0
            ][:40],
            "process": self._process_report(),
            **extra,
        }

    def _process_report(self) -> Dict[str, Any]:
        import core
        report: Dict[str, Any] = {}
        try:
            state = core.load_job_state(self.job_id) or {}
            pairs = state.get("pairs") or []
            report["pairs"] = len(pairs)
            report["translated"] = sum(
                1 for pair in pairs if str(pair.get("target") or "").strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            report["state_cache"] = dict(core._job_repo.get_cache_stats())
        except Exception:  # noqa: BLE001
            pass
        return report


def _engine_config():
    """A configured-enough engine config that needs no API key."""
    from folith.qt import adapter

    config = adapter.EngineConfig.load()
    for field, value in (("provider", PROVIDER), ("model", MODEL),
                          ("api_key", "offline")):
        try:
            setattr(config, field, value)
        except Exception:  # noqa: BLE001 - dataclass fields may be frozen
            pass
    return config


# ----------------------------------------------------------------- runner


def run_scenario(scenario: Scenario, *, keep_workdir: bool = False,
                 workdir: Optional[Path] = None,
                 baseline: bool = False,
                 platform: str = "offscreen") -> Dict[str, Any]:
    owned = workdir is None
    if workdir is None:
        workdir = Path(tempfile.mkdtemp(prefix=f"folith-bench-{scenario.name}-"))
    _prepare_environment(workdir, platform)

    session = BenchSession(workdir, scenario)
    sampler = ProcessSampler(interval=0.1)
    started_at = time.time()
    setup_started = time.perf_counter()
    try:
        session.prepare()
        if baseline:
            # baseline_mode only reverses C1-C4.  C5/C6 must be reversed in the
            # tree itself, otherwise a "baseline" still contains optimisations.
            marker = ROOT / ".folith-baseline-reverted"
            if not marker.is_file():
                raise SystemExit(
                    "--baseline requires a tree produced by "
                    "`python -m benchmarks.performance.revert_to_baseline <dir>`; "
                    "baseline_mode alone does not revert C5/C6")
            # Re-install the pre-optimisation behaviour *after* the window was
            # built, so both runs execute the same product code except for the
            # optimised paths.  This is idempotent on the reverted tree.
            from . import baseline_mode
            baseline_mode.install()
        session.open_job()
        setup_seconds = time.perf_counter() - setup_started

        # The measurement window starts here: setup (document build, window
        # construction, opening the task) is reported separately so it cannot
        # be mistaken for background cost.
        sampler.start()
        session.paint_counter.count = 0
        with session._counter_lock:
            session.counters.clear()
        session._window_start = time.perf_counter()
        session.probe.start()

        background_started = False
        if scenario.background != "idle":
            session.start_worker()
            background_started = True

        if scenario.background == "export":
            session.action_export_in_background()

        driver = InteractionDriver(session)
        driver.start(scenario.duration_s)
        session.app.exec()
        driver.stop()
        session.probe.stop()
        sampler.stop()

        worker_alive = session.worker_running() if background_started else False
        payload = session.collect({
            "started_at": started_at,
            "setup_seconds": round(setup_seconds, 3),
            "wall_seconds": round(time.time() - started_at, 3),
            "measured_seconds": round(time.time() - started_at - setup_seconds, 3),
            "worker_alive_at_end": worker_alive,
            "background": scenario.background,
            "interactions_performed": len(session.actions),
            "shutdown_seconds": round(session.shutdown_seconds, 3),
            "mode": "baseline" if baseline else "optimised",
        })
        payload["process"] = {**sampler.report(), **payload.get("process", {})}
        return payload
    finally:
        sampler.stop()
        if owned and not keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


SCENARIOS: Dict[str, Callable[[], Scenario]] = {
    "idle": lambda: Scenario(
        name="S1-idle-navigation", background="idle", segments=400, duration_s=20.0,
        description="No background task: navigate, scroll, select and search."),
    "translate": lambda: Scenario(
        name="S2-translate-workbench", background="translate", segments=400,
        duration_s=30.0,
        description="Document pipeline running in the background while the "
                    "workbench is used."),
    "review": lambda: Scenario(
        name="S7-review-running", background="review", segments=400, duration_s=30.0,
        description="Review/QA phase running while the CAT editor is used."),
    "export": lambda: Scenario(
        name="S8-export-running", background="export", segments=400, duration_s=25.0,
        description="Delivery pre-flight (DOCX/PDF build) running while the "
                    "project view is used."),
    "large": lambda: Scenario(
        name="S10-large-document", background="translate", segments=1500,
        duration_s=40.0,
        description="Large document loaded: filter, search and scroll while the "
                    "pipeline runs."),
}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="folith-benchmark",
        description="Reproducible performance benchmark for the Folith Qt client")
    parser.add_argument("--scenario", action="append", default=[],
                        choices=sorted(SCENARIOS), help="scenario to run (repeatable)")
    parser.add_argument("--segments", type=int, default=None,
                        help="override the document size for the chosen scenario")
    parser.add_argument("--duration", type=float, default=None,
                        help="override the measurement window in seconds")
    parser.add_argument("--out", default="benchmarks/performance/baseline.json")
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--keep-workdir", default=None)
    parser.add_argument("--baseline", action="store_true",
                        help="measure a tree produced by revert_to_baseline "
                             "(the runner refuses an unmarked tree)")
    parser.add_argument("--platform", default="offscreen",
                        help="Qt platform plugin (offscreen, windows, cocoa)")
    args = parser.parse_args(argv)

    names = args.scenario or ["idle", "translate", "review", "export", "large"]
    results = []
    for name in names:
        scenario = SCENARIOS[name]()
        if args.segments is not None:
            scenario.segments = args.segments
        if args.duration is not None:
            scenario.duration_s = args.duration
        workdir = Path(args.keep_workdir) if args.keep_workdir else None
        print(f"[bench] running {scenario.name} "
              f"({scenario.segments} segments, {scenario.duration_s}s)...",
              flush=True)
        payload = run_scenario(scenario, workdir=workdir, baseline=args.baseline,
                               platform=args.platform)
        results.append(payload)
        print(f"[bench] {scenario.name}: "
              f"event-loop p95={payload['event_loop']['delay_ms']['p95']}ms "
              f"handler p95={payload['interaction_handler_ms']['p95']}ms "
              f"cpu={payload['process']['cpu_seconds']}s "
              f"paints={payload['paints']}", flush=True)

    document = {
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "scenarios": results,
    }
    write_json(args.out, document)
    print(f"[bench] wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
