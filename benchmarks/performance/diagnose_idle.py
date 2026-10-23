"""Diagnostic: who paints, and which threads exist, while the window is idle.

Run: ``python -m benchmarks.performance.diagnose_idle``
"""
from __future__ import annotations

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

WORK = Path(tempfile.mkdtemp(prefix="folith-diag-"))
(WORK / "data").mkdir(parents=True, exist_ok=True)
(WORK / "logs").mkdir(parents=True, exist_ok=True)
os.environ["FOLITH_OUTPUT_DIR"] = str(WORK / "data")
os.environ["FOLITH_DESKTOP_DATA_DIR"] = str(WORK / "data")
os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(WORK / "logs")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
os.environ["FOLITH_EXIT_FORCE_PROMPT"] = "0"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QTimer  # noqa: E402

import core  # noqa: E402
from benchmarks.performance import fixtures  # noqa: E402


class PainterSpy(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.by_class: collections.Counter = collections.Counter()
        self.by_object: collections.Counter = collections.Counter()
        self.total = 0

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.total += 1
            name = type(obj).__name__
            self.by_class[name] += 1
            self.by_object[f"{name}:{obj.objectName()}"] += 1
        return False


def main() -> int:
    from folith.qt import bootstrap
    from folith.qt import window as window_module

    source = fixtures.build_docx(WORK / "doc.docx", 400)
    state = core.new_job_state(source.name)
    core.save_job_state("diag", state)
    core.save_source("diag", source.read_bytes())

    paths = bootstrap.current_paths()
    app = window_module.build_application([])
    spy = PainterSpy()
    app.installEventFilter(spy)

    config = window_module.adapter.EngineConfig.load()
    window = window_module.MainWindow(config, paths, None)
    window.show()
    window.open_job("diag")
    for _ in range(120):
        app.processEvents()
        time.sleep(0.01)

    spy.by_class.clear()
    spy.by_object.clear()
    spy.total = 0
    threads_before = sorted(t.name for t in threading.enumerate())

    start = time.time()
    end = start + 6.0

    def stop():
        app.quit()

    QTimer.singleShot(int((end - start) * 1000), stop)
    app.exec()

    elapsed = time.time() - start
    print(f"=== idle {elapsed:.1f}s: {spy.total} paints "
          f"({spy.total / elapsed:.0f}/s) ===")
    print("top classes:")
    for name, count in spy.by_class.most_common(12):
        print(f"  {count:7d}  {name}")
    print("top objects:")
    for name, count in spy.by_object.most_common(12):
        print(f"  {count:7d}  {name}")

    threads_after = sorted(t.name for t in threading.enumerate())
    print(f"threads before: {len(threads_before)}  after: {len(threads_after)}")
    new = [t for t in threads_after if t not in threads_before]
    print("new threads:", new)
    counts = collections.Counter(t.split("-")[0] for t in threads_after)
    print("thread name prefixes:", dict(counts))

    # Timers that are currently active in the window.
    active = []
    for widget in window.findChildren(QObject):
        for name in ("_timer", "_height_timer", "_height_resize", "_autosave",
                     "_pdf_follow", "_edit_idle", "_defaults_timer", "timer"):
            candidate = getattr(widget, name, None)
            if candidate is None:
                continue
            if isinstance(candidate, QTimer) and candidate.isActive():
                active.append((type(widget).__name__, name,
                               candidate.interval(), candidate.timerType()))
    print("active timers:")
    for entry in active:
        print("  ", entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
