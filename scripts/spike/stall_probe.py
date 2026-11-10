"""Main-thread stall probe for the A2 process-isolation spike.

Metric parity with A0's ``folith/qt/jank_monitor.py`` (PR #330, not yet on main):
a 25 ms ``QTimer`` pulse on the GUI thread; ``interval_ms`` is the gap between
two pulses; ``>= 100 ms`` is one slow stall, ``>= 500 ms`` severe;
``blocked_ms = interval_ms - 25``.  Percentiles are over recorded stalls.

This is a spike-local reimplementation, not a second product sampler: it exists
only because the A0 sampler is not on ``main`` yet.  When #330 merges, the
prototype should call it instead.  The numbers it prints are therefore
"same metric, same thresholds", not "A0-verified evidence".
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

PULSE_MS = 25.0
SLOW_MS = 100.0
SEVERE_MS = 500.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


class PulseProbe:
    """Records GUI-thread pulse gaps while the Qt event loop runs."""

    def __init__(self, interval_ms: float = PULSE_MS) -> None:
        from PySide6.QtCore import QTimer

        self.interval_ms = float(interval_ms)
        self._started = None
        self._last = None
        self.intervals: list[float] = []
        self.stalls: list[float] = []
        self._timer = QTimer()
        self._timer.setInterval(int(self.interval_ms))
        self._timer.timeout.connect(self._pulse)

    def start(self) -> None:
        self._started = time.monotonic()
        self._last = time.monotonic()
        self._timer.start()

    def _pulse(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            interval = (now - self._last) * 1000.0
            self.intervals.append(interval)
            if interval >= SLOW_MS:
                self.stalls.append(interval)
        self._last = now

    def summary(self) -> dict[str, Any]:
        observed = (time.monotonic() - self._started) if self._started else 0.0
        stalls = self.stalls
        seen = [value - self.interval_ms for value in stalls]
        return {
            "version": 1,
            "scope": "spike_pulse_probe",
            "pulse_interval_ms": self.interval_ms,
            "thresholds_ms": {"slow": SLOW_MS, "severe": SEVERE_MS},
            "observed_s": round(observed, 3),
            "pulse_count": len(self.intervals),
            "slow_count": len(stalls),
            "severe_count": sum(value >= SEVERE_MS for value in stalls),
            "slow_per_minute": round(len(stalls) * 60.0 / observed, 2) if observed else 0.0,
            "blocked_ms": {
                "count": len(seen),
                "p50": round(_percentile(seen, 0.5), 1) if seen else 0.0,
                "p95": round(_percentile(seen, 0.95), 1) if seen else 0.0,
                "p99": round(_percentile(seen, 0.99), 1) if seen else 0.0,
                "max": round(max(seen), 1) if seen else 0.0,
            },
            "interval_ms": {
                "p50": round(_percentile(self.intervals, 0.5), 1) if self.intervals else 0.0,
                "p95": round(_percentile(self.intervals, 0.95), 1) if self.intervals else 0.0,
                "p99": round(_percentile(self.intervals, 0.99), 1) if self.intervals else 0.0,
                "max": round(max(self.intervals), 1) if self.intervals else 0.0,
            },
        }


def write_summary(path: str | Path, probe: PulseProbe) -> None:
    Path(path).write_text(json.dumps(probe.summary(), ensure_ascii=False, indent=2),
                          encoding="utf-8")
