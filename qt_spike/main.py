#!/usr/bin/env python3
"""Qt Spike S1: minimal PySide6 window that reuses the existing business core.

Purpose (roadmap handover §6.2 S1):
  * prove PySide6 can host a real window on macOS;
  * prove `core` is usable **without Streamlit** (it is: core.py imports no
    Streamlit) and that the existing data format is read as-is;
  * measure startup cost so later "界面响应" work has a baseline.

`--smoke` runs the same window construction, prints a JSON summary and exits, so
it can be used as automated evidence (with QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _prepare_env(data_dir: str | None, log_dir: str | None) -> None:
    """The core reads FOLITH_OUTPUT_DIR at import time, so set it first."""
    if data_dir:
        os.environ["FOLITH_OUTPUT_DIR"] = str(Path(data_dir).expanduser())
    if log_dir:
        os.environ["FOLITH_DESKTOP_LOG_DIR"] = str(Path(log_dir).expanduser())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Qt spike: minimal window over core")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--smoke", action="store_true",
                        help="build the window, report, and exit (no event loop)")
    parser.add_argument("--result", default=None, help="write the smoke summary here")
    args = parser.parse_args(argv)

    _prepare_env(args.data_dir, args.log_dir)
    started = time.monotonic()

    import core  # noqa: PLC0415  (import after the env is set)
    core_ready = time.monotonic()

    from PySide6.QtCore import Qt  # noqa: PLC0415
    from PySide6.QtWidgets import (QApplication, QLabel, QListWidget,  # noqa: PLC0415
                                   QListWidgetItem, QMainWindow, QVBoxLayout,
                                   QWidget)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    qt_ready = time.monotonic()

    window = QMainWindow()
    window.setWindowTitle("Folith (Qt Spike S1)")
    central = QWidget()
    layout = QVBoxLayout(central)
    layout.addWidget(QLabel(f"数据目录：{core.OUTPUT_DIR}"))

    jobs = core.list_jobs()
    listing = QListWidget()
    for job in jobs:
        item = QListWidgetItem(f"{job.get('title') or job.get('job_id')} — "
                               f"{job.get('status') or '—'}")
        item.setData(Qt.UserRole, job.get("job_id"))
        listing.addItem(item)
    layout.addWidget(QLabel(f"任务数：{len(jobs)}"))
    layout.addWidget(listing)
    window.setCentralWidget(central)
    window.resize(720, 480)
    built = time.monotonic()

    summary = {
        "ok": True,
        "data_dir": str(core.OUTPUT_DIR),
        "jobs": len(jobs),
        "job_ids": [job.get("job_id") for job in jobs[:5]],
        "timings_ms": {
            "core_import": round((core_ready - started) * 1000),
            "qt_import": round((qt_ready - core_ready) * 1000),
            "window_build": round((built - qt_ready) * 1000),
            "total": round((built - started) * 1000),
        },
        "qt_version": getattr(__import__("PySide6.QtCore", fromlist=["qVersion"]), "qVersion")(),
        "pyside_version": __import__("PySide6", fromlist=["__version__"]).__version__,
        "platform": app.platformName(),
    }

    def _emit(mode: str) -> None:
        summary["mode"] = mode
        text = json.dumps(summary, ensure_ascii=False, indent=2)
        if args.result:
            Path(args.result).write_text(text + "\n", encoding="utf-8")
        # flush: a window-mode run is usually terminated by signal, which would
        # otherwise discard block-buffered stdout.
        print(text, flush=True)

    if args.smoke:
        _emit("smoke")
        window.close()
        return 0

    window.show()
    _emit("window")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
