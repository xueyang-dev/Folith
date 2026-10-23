"""Measurement primitives for the Folith performance benchmark.

Three independent things are measured, because a desktop application's
responsiveness is the *interaction* of all three:

``EventLoopProbe``
    How late the Qt event loop is relative to its own schedule.  This is the
    metric that maps onto "the window stopped responding".

``ProcessSampler``
    Process CPU time, resident memory and thread count, sampled from a
    separate thread so the measurement does not itself stall the GUI.

``PaintCounter``
    How many paint events the window actually produced.  A repaint storm
    shows up here long before it shows up in a latency number.

Everything degrades gracefully: ``psutil`` is optional (it is a benchmark tool,
not a product dependency), and the sampler falls back to ``resource`` /
``time.process_time``.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QTimer, Qt

try:  # pragma: no cover - optional benchmark dependency
    import psutil
except Exception:  # noqa: BLE001
    psutil = None


# ---------------------------------------------------------------- statistics


def percentiles(samples: List[float]) -> Dict[str, Any]:
    """p50/p95/p99 plus the shape of the tail, in milliseconds."""
    if not samples:
        return {"count": 0, "p50": None, "p95": None, "p99": None,
                "max": None, "mean": None}
    ordered = sorted(samples)
    size = len(ordered)

    def _pick(fraction: float) -> float:
        index = min(size - 1, max(0, int(round(fraction * (size - 1)))))
        return ordered[index]

    return {
        "count": size,
        "p50": round(_pick(0.50), 3),
        "p95": round(_pick(0.95), 3),
        "p99": round(_pick(0.99), 3),
        "max": round(ordered[-1], 3),
        "mean": round(statistics.fmean(ordered), 3),
    }


def summarise(values: List[float]) -> Dict[str, Any]:
    """Alias kept for readability at call sites that measure durations."""
    return percentiles(values)


# ------------------------------------------------------------ event loop


class EventLoopProbe(QObject):
    """Sample how late the Qt event loop runs relative to a fixed cadence.

    A ``QTimer`` with :attr:`Qt.TimerType.PreciseTimer` is asked to fire every
    ``interval_ms``.  Two numbers come out of it:

    ``gap_ms``
        wall time between two consecutive fires -- i.e. how long the event
        loop was unavailable.
    ``delay_ms``
        how much of that gap is *overrun* beyond the requested cadence
        (``gap - interval``).  A healthy loop keeps this near zero; a stalled
        loop shows the whole stall here.

    The timer is deliberately coarse (10 ms by default): a fine timer would
    add its own wakeups and distort exactly what it is trying to measure.
    """

    def __init__(self, parent: Optional[QObject] = None,
                 interval_ms: int = 10) -> None:
        super().__init__(parent)
        self.interval_ms = int(interval_ms)
        self.gaps_ms: List[float] = []
        self.delays_ms: List[float] = []
        #: ``(timestamp, gap_ms, delay_ms)`` so a stall can be attributed to
        #: the interaction that caused it.
        self.samples: List[tuple] = []
        self._last: Optional[float] = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        if self._last is not None:
            gap = (now - self._last) * 1000.0
            delay = max(0.0, gap - self.interval_ms)
            self.gaps_ms.append(gap)
            self.delays_ms.append(delay)
            self.samples.append((now, gap, delay))
        self._last = now

    def reset(self) -> None:
        self.gaps_ms.clear()
        self.delays_ms.clear()
        self.samples.clear()
        self._last = time.perf_counter()

    def report(self, *, until: Optional[float] = None) -> Dict[str, Any]:
        """Percentiles over the samples taken up to ``until`` (a timestamp).

        The comparison window matters: a run that finishes the same work three
        times faster must still be compared over the same wall-clock window.
        """
        gaps = [value for value in self.gaps_ms]
        delays = [value for value in self.delays_ms]
        stamps = [stamp for stamp, _gap, _delay in self.samples]
        if until is not None:
            keep = [index for index, stamp in enumerate(stamps) if stamp <= until]
            gaps = [self.gaps_ms[index] for index in keep]
            delays = [self.delays_ms[index] for index in keep]
        return {
            "gap_ms": percentiles(gaps),
            "delay_ms": percentiles(delays),
            "stalls_over_100ms": sum(1 for value in gaps if value > 100.0),
            "stalls_over_250ms": sum(1 for value in gaps if value > 250.0),
        }


# --------------------------------------------------------------- painting


class PaintCounter(QObject):
    """Count the paint events the whole application produces."""

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.count = 0

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


# ---------------------------------------------------------- process sample


class ProcessSampler(threading.Thread):
    """Sample CPU / RSS / thread count of this process from its own thread."""

    def __init__(self, interval: float = 0.1) -> None:
        super().__init__(name="bench-sampler", daemon=True)
        self.interval = interval
        # Not ``_stop``: ``threading.Thread`` already owns that name.
        self._stop_event = threading.Event()
        self.samples: List[Dict[str, float]] = []
        self._process = psutil.Process(os.getpid()) if psutil is not None else None
        self._last_cpu = self._cpu_times()

    # -- helpers ----------------------------------------------------------
    def _cpu_times(self) -> Optional[tuple]:
        if self._process is None:
            return None
        try:
            times = self._process.cpu_times()
        except Exception:  # noqa: BLE001
            return None
        return (times.user, times.system)

    def _rss(self) -> int:
        if self._process is None:
            try:
                import resource

                usage = resource.getrusage(resource.RUSAGE_SELF)
                return int(usage.ru_maxrss) * 1024
            except Exception:  # noqa: BLE001
                return 0
        try:
            return int(self._process.memory_info().rss)
        except Exception:  # noqa: BLE001
            return 0

    def _threads(self) -> int:
        if self._process is None:
            return threading.active_count()
        try:
            return self._process.num_threads()
        except Exception:  # noqa: BLE001
            return threading.active_count()

    # -- thread body ------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            now = self._cpu_times()
            if now is not None and self._last_cpu is not None:
                user = now[0] - self._last_cpu[0]
                system = now[1] - self._last_cpu[1]
                self.samples.append({
                    "t": time.time(),
                    "user": user,
                    "system": system,
                    "cpu": user + system,
                    "rss": self._rss(),
                    "threads": self._threads(),
                })
                self._last_cpu = now
            self._stop_event.wait(self.interval)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=3.0)

    # -- reporting --------------------------------------------------------
    def report(self) -> Dict[str, Any]:
        if not self.samples:
            return {"cpu_seconds": 0.0, "user_seconds": 0.0, "system_seconds": 0.0,
                    "peak_rss_mb": 0.0, "first_rss_mb": 0.0, "last_rss_mb": 0.0,
                    "peak_threads": 0, "samples": 0}
        user = sum(item["user"] for item in self.samples)
        system = sum(item["system"] for item in self.samples)
        rss = [item["rss"] for item in self.samples]
        threads = [int(item["threads"]) for item in self.samples]
        return {
            "cpu_seconds": round(user + system, 4),
            "user_seconds": round(user, 4),
            "system_seconds": round(system, 4),
            "peak_rss_mb": round(max(rss) / (1024 * 1024), 2),
            "first_rss_mb": round(rss[0] / (1024 * 1024), 2),
            "last_rss_mb": round(rss[-1] / (1024 * 1024), 2),
            "peak_threads": max(threads),
            "median_threads": int(statistics.median(threads)),
            "samples": len(self.samples),
        }


def process_cpu_seconds() -> float:
    """Total CPU seconds burned by this process (all threads)."""
    if psutil is not None:
        try:
            times = psutil.Process(os.getpid()).cpu_times()
            return round(times.user + times.system, 4)
        except Exception:  # noqa: BLE001
            pass
    return round(time.process_time(), 4)


# ----------------------------------------------------------------- helpers


def write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")
