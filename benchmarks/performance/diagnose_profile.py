"""Diagnostic: rank the functions the GUI thread and the worker spend time in.

The scenario is the real one (a document pipeline running in the background
while the window is used).  Selected functions are wrapped with a timing
probe; the report groups the samples by thread so a GUI-thread cost is never
mixed up with a worker-thread cost.

Run: ``python -m benchmarks.performance.diagnose_profile --duration 40``
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

WORK = Path(tempfile.mkdtemp(prefix="folith-profile-"))
(WORK / "data").mkdir(parents=True, exist_ok=True)
(WORK / "logs").mkdir(parents=True, exist_ok=True)
os.environ["FOLITH_OUTPUT_DIR"] = str(WORK / "data")
os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(WORK / "data")
os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(WORK / "logs")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import core  # noqa: E402
from benchmarks.performance import fixtures  # noqa: E402

STATS: "collections.Counter[str]" = collections.Counter()
TOTALS: "collections.defaultdict[str, float]" = collections.defaultdict(float)
MAXIMA: "collections.defaultdict[str, float]" = collections.defaultdict(float)
BY_THREAD: "collections.defaultdict[str, collections.Counter]" = \
    collections.defaultdict(collections.Counter)
LOCK = threading.Lock()


def probe(label: str):
    def wrap(fn):
        def inner(*args, **kwargs):
            thread = threading.current_thread().name
            started = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - started) * 1000.0
                with LOCK:
                    STATS[label] += 1
                    TOTALS[label] += elapsed
                    if elapsed > MAXIMA[label]:
                        MAXIMA[label] = elapsed
                    BY_THREAD[thread][label] += elapsed
        inner.__name__ = getattr(fn, "__name__", label)
        inner.__qualname__ = inner.__name__
        return inner
    return wrap


def instrument() -> None:
    from folith.qt import adapter
    from folith.qt import views
    from folith.storage import job_repository
    from folith import snapshots

    CALLERS: "collections.Counter[str]" = collections.Counter()

    def caller_probe(label):
        def wrap(fn):
            def inner(*args, **kwargs):
                thread = threading.current_thread().name
                started = time.perf_counter()
                try:
                    return fn(*args, **kwargs)
                finally:
                    elapsed = (time.perf_counter() - started) * 1000.0
                    with LOCK:
                        STATS[label] += 1
                        TOTALS[label] += elapsed
                        if elapsed > MAXIMA[label]:
                            MAXIMA[label] = elapsed
                        BY_THREAD[thread][label] += elapsed
                        if label in ("core.update_runtime_state",
                                     "core.load_runtime_state"):
                            import traceback
                            stack = traceback.extract_stack()
                            caller = "?"
                            for frame in reversed(stack[:-2]):
                                if "diagnose_profile" in frame.filename:
                                    continue
                                caller = f"{frame.name}@{Path(frame.filename).name}:{frame.lineno}"
                                break
                            CALLERS[f"{label} <- {caller}"] += 1
            inner.__name__ = getattr(fn, "__name__", label)
            inner.__qualname__ = inner.__name__
            return inner
        return wrap

    targets = [
        (adapter, "list_jobs", "adapter.list_jobs"),
        (adapter, "load_state", "adapter.load_state"),
        (adapter, "runtime_view", "adapter.runtime_view"),
        (adapter, "state_signature", "adapter.state_signature"),
        (adapter, "segment_rows", "adapter.segment_rows"),
        (adapter, "delivery_snapshots", "adapter.delivery_snapshots"),
        (adapter, "delivery_preview", "adapter.delivery_preview"),
        (adapter, "drafts_signature", "adapter.drafts_signature"),
        (job_repository, "_isolated_copy", "repo._isolated_copy"),
        (job_repository, "load_job_state_cached", "repo.load_job_state_cached"),
        (job_repository, "encode_state", "repo.encode_state"),
        (snapshots, "state_identity", "snapshots.state_identity"),
        (core, "update_runtime_state", "core.update_runtime_state"),
        (core, "load_runtime_state", "core.load_runtime_state"),
        (core, "_runtime_cancel_requested", "core._runtime_cancel_requested"),
        (core, "_append_runtime_event", "core._append_runtime_event"),
        (views.WorkbenchView, "apply", "views.apply"),
        (views.WorkbenchView, "_render_segments", "views._render_segments"),
        (views.WorkbenchView, "_render_runtime", "views._render_runtime"),
        (views.WorkbenchView, "_render_delivery", "views._render_delivery"),
        (views.WorkbenchView, "_render_review", "views._render_review"),
        (views.WorkbenchView, "_render_terms", "views._render_terms"),
        (views.WorkbenchView, "_render_followup", "views._render_followup"),
        (views.WorkbenchView, "_render_activity", "views._render_activity"),
        (views.WorkbenchView, "_render_segment_detail", "views._render_segment_detail"),
        (views.WorkbenchView, "_schedule_row_heights", "views._schedule_row_heights"),
        (views.WorkbenchView, "_apply_row_height_chunk", "views._apply_row_height_chunk"),
        (views.WorkbenchView, "_paint_row", "views._paint_row"),
        (views.WorkbenchView, "_visible_rows", "views._visible_rows"),
        (views.WorkbenchView, "_sync_drafts_banner", "views._sync_drafts_banner"),
        (views.WorkbenchView, "_sync_word_count", "views._sync_word_count"),
        (views.WorkbenchView, "_render_excluded", "views._render_excluded"),
        (core, "validate_delivery_translation_state", "core.validate_delivery_translation_state"),
        (core, "translation_review_readiness", "core.translation_review_readiness"),
        (core, "recovery_summary", "core.recovery_summary"),
        (core, "task_status_label", "core.task_status_label"),
        (core, "workflow_outcomes", "core.workflow_outcomes"),
        (adapter, "glossary_view", "adapter.glossary_view"),
        (adapter, "review_queue", "adapter.review_queue"),
        (adapter, "followup_view", "adapter.followup_view"),
    ]
    from folith.application import task_attention
    from folith import translation_evidence
    from folith import delivery as _delivery
    targets.extend([
        (task_attention, "state_facts", "attention.state_facts"),
        (translation_evidence, "translation_review_readiness", "evidence.review_readiness"),
        (_delivery, "unresolved_blocking", "delivery.unresolved_blocking"),
    ])
    for module, attribute, label in targets:
        original = getattr(module, attribute, None)
        if original is None:
            continue
        setattr(module, attribute, caller_probe(label)(original))
    # ``core`` re-exports the engine helpers; patch the module the pipeline uses.
    from folith.engine import runtime_telemetry
    original_flush = runtime_telemetry._RuntimeTelemetryCoalescer._flush_locked
    runtime_telemetry._RuntimeTelemetryCoalescer._flush_locked = probe(
        "telemetry._flush_locked")(original_flush)
    instrument.CALLERS = CALLERS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--segments", type=int, default=400)
    args = parser.parse_args()

    from PySide6.QtCore import QTimer

    from folith.qt import bootstrap
    from folith.qt import window as window_module
    from offline_provider import OfflineProvider

    instrument()

    source = fixtures.build_docx(WORK / "doc.docx", args.segments)
    state = core.new_job_state(source.name)
    core.save_job_state("prof", state)
    core.save_source("prof", source.read_bytes())

    paths = bootstrap.current_paths()
    app = window_module.build_application([])
    config = window_module.adapter.EngineConfig.load()
    window = window_module.MainWindow(config, paths, None)
    window.show()
    window.open_job("prof")
    for _ in range(150):
        app.processEvents()
        time.sleep(0.01)

    core.call_llm = OfflineProvider()
    core.start_job_worker("prof", source.name, None, {
        "provider": "OfflineFixture", "api_key": "offline", "model": "offline-fixture",
        "target_lang": "简体中文", "auto_term": False, "enable_report": False,
        "translation_theory": "", "user_glossary": [], "style_rules": "保持学术书面语。",
        "enable_review": True, "enable_annotate": False, "use_tm": True,
        "strict_terminology_governance": False,
        "delivery_config": {"deliver_report": False},
    })

    with LOCK:
        STATS.clear()
        TOTALS.clear()
        MAXIMA.clear()
        BY_THREAD.clear()

    QTimer.singleShot(int(args.duration * 1000), app.quit)
    started = time.time()
    app.exec()
    elapsed = time.time() - started

    print(f"=== profiled {elapsed:.1f}s ===")
    print(f"{'function':42s} {'calls':>7s} {'total ms':>10s} {'max ms':>9s}  threads")
    for label, count in STATS.most_common():
        owners = sorted({name.split("-")[0][:14] for name, counter in BY_THREAD.items()
                         if label in counter})
        print(f"{label:42s} {count:7d} {TOTALS[label]:10.1f} {MAXIMA[label]:9.1f}  "
              f"{','.join(owners)}")
    print("\ncalls by caller:")
    for label, count in instrument.CALLERS.most_common(20):
        print(f"  {count:7d}  {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
