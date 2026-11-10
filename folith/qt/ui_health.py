"""Qt pulse producer; evidence collection belongs to an independent process."""
from __future__ import annotations

import atexit
import contextlib
import faulthandler
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

from PySide6.QtCore import QTimer, qVersion

from folith.desktop.ui_watchdog import TIMEOUT_SECONDS, _write_report, _now
from folith.qt.jank_monitor import JankMonitor, jank_monitor_enabled


class UiHealthMonitor:
    def __init__(self, app, window, paths, *, timeout=TIMEOUT_SECONDS, identity=None):
        self.app, self.window, self.paths = app, window, paths
        self.timeout = timeout
        self.identity = {key: (identity or {}).get(key, "")
                         for key in ("app_version", "build_id")}
        self.process = None
        self.jank = None
        self._trace_api = None
        self.timer = QTimer(app)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.pulse)
        self.directory: Path | None = None
        self._stacks = self._crashes = None

    def start(self):
        # The stall sampler is independent of the out-of-process watchdog, so it
        # starts first: a watchdog that cannot launch must not also remove the
        # only 100 ms-level responsiveness measurement we have.
        self._start_jank()
        root = self.paths.log_dir / "ui-health"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix="session-", dir=root))
        # Five runs are enough to retain the failed run across a restart.
        previous = sorted((p for p in root.glob("session-*") if p != self.directory),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        for old in previous[4:]:
            shutil.rmtree(old, ignore_errors=True)
        self._stacks = (self.directory / "python-stacks.log").open("ab")
        self._crashes = (self.directory / "crash-stacks.log").open("ab")
        faulthandler.enable(self._crashes, all_threads=True)
        if sys.platform != "win32":
            faulthandler.register(signal.SIGUSR2, self._stacks, all_threads=True)
        arguments = ["--ui-watchdog", str(os.getpid()), str(self.directory),
                     str(self.paths.data_dir), str(self.timeout)]
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [sys.executable, "-m", "folith.qt"]) + arguments
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self._crashes,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
        )
        atexit.register(self.stop)
        self.app.aboutToQuit.connect(self.stop)
        self.pulse()
        self.timer.start()

    def _start_jank(self):
        """Start the GUI stall sampler and publish it to the trace engine.

        It owns its own ``QTimer`` (25 ms) instead of riding the 1 s heartbeat:
        a 1 s pulse quantises every measurement to 1 s and cannot resolve the
        100 ms threshold at all.  The heartbeat still carries the counters, so
        an incident report says what the last-known stall state was.
        """
        monitor = JankMonitor(enabled=jank_monitor_enabled())
        try:
            monitor.start()
        except Exception:  # a measurement must never prevent the app from starting
            monitor.stop()
            return None
        self.jank = monitor
        try:
            from folith import trace as trace_api

            trace_api.register_gui_stall_source(monitor)
            self._trace_api = trace_api
        except Exception:
            self._trace_api = None
        return monitor

    def _stop_jank(self):
        monitor, self.jank = self.jank, None
        api, self._trace_api = self._trace_api, None
        if monitor is None:
            return
        if api is not None:
            with contextlib.suppress(Exception):
                api.unregister_gui_stall_source(monitor)
        with contextlib.suppress(Exception):
            monitor.stop()

    def pulse(self):
        if self.process is None:
            return
        if self.process.poll() is not None:
            self.timer.stop()
            self._observer_failed()
            return
        state = self.window._cached_state
        snapshot = {
            "gui_pulse_at": _now(), "heartbeat_timeout_seconds": self.timeout,
            "job_id": self.window._current_job_id,
            "committed_pairs": len(state.get("pairs") or []),
            "background_tasks": len(self.window.runner._running),
            "qt_version": qVersion(), "python_version": sys.version.split()[0],
            "platform": self.app.platformName(),
            "serial_raster": os.environ.get("QT_NO_GUI_THREADPOOL") == "1",
            "gui_jank": self.jank.snapshot() if self.jank is not None else None,
            **self.identity,
        }
        if sys.platform == "win32":
            snapshot["window_handle"] = int(self.window.winId())
        try:
            self.process.stdin.write((json.dumps(snapshot) + "\n").encode())
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.timer.stop()
            self._observer_failed()
        if sys.platform == "win32":
            # Windows has no faulthandler.register. Its native timer still
            # produces Python stacks when a C extension holds the GIL. A dump
            # on wake is raw evidence, not a confirmed gate failure; the child
            # applies the suspend grace before writing incident.json.
            if self._stacks.tell() > 1024 * 1024:
                self._stacks.seek(0)
                self._stacks.truncate()
            faulthandler.dump_traceback_later(self.timeout, file=self._stacks)

    def _observer_failed(self):
        with contextlib.suppress(OSError):
            _write_report(self.directory, {
                "status": "observer_failed", "detected_at": _now(),
                "evidence": "watchdog_channel_closed",
            })

    def stop(self):
        self.timer.stop()
        self._stop_jank()
        process, self.process = self.process, None
        if process is not None:
            if process.stdin is not None:
                with contextlib.suppress(OSError):
                    process.stdin.write(b'{"event":"stop"}\n')
                    process.stdin.flush()
                with contextlib.suppress(OSError):
                    process.stdin.close()
            try:
                process.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=1)
        if sys.platform == "win32":
            faulthandler.cancel_dump_traceback_later()
        elif self._stacks is not None:
            faulthandler.unregister(signal.SIGUSR2)
        if self._crashes is not None:
            faulthandler.disable()
        for stream in (self._stacks, self._crashes):
            if stream is not None:
                stream.close()
        self._stacks = self._crashes = None
