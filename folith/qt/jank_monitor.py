"""Main-thread stall ("jank") sampler for the Qt desktop client.

Why this exists: :mod:`folith.desktop.ui_watchdog` answers only "does the GUI
answer at all?" (30 s heartbeat).  It cannot count 100 ms stalls and it cannot
say *which function* held the GUI thread.  This module keeps a bounded
pulse-interval record on the GUI thread and, while the GUI thread is *inside* a
stall, captures its Python stack from a helper thread, so a report can name the
blocking function.

Measurement
    A ``QTimer`` pulses every ``pulse_interval_ms`` (25 ms by default).  The
    metric is the gap between two pulses, ``interval_ms``: one jank event when
    ``interval_ms >= slow_ms`` (100 ms), a severe one at ``>= severe_ms``
    (500 ms).  One timer period is part of the measured interval, so the value
    overstates the blocked time by at most one period plus scheduler jitter;
    ``blocked_ms`` (``interval_ms - pulse_interval_ms``) is recorded next to it
    so a reader can pick the number they mean.

Stack capture
    The GUI thread never renders a traceback.  A helper thread watches the last
    pulse timestamp and, only after the GUI has been silent for two periods,
    asks ``sys._current_frames()`` for the GUI thread's frame.  A stall caused by
    a C extension that holds the GIL cannot be captured this way (the helper
    cannot run at all); such events are still counted and are stored with
    ``stack_captured: false`` — they show up in the watchdog's faulthandler dump
    instead, and the report says so.

Cost
    GUI thread per pulse: one ``time.monotonic()`` read, one lock acquisition,
    one counter increment and one bounded-deque append (which allocates one
    tuple).  No stack work ever happens on the GUI thread.
    Helper thread: an ``Event.wait`` in a loop with two lock acquisitions and
    one monotonic read per poll, plus one ``sys._current_frames`` call per poll
    *only while a stall is suspected*.  Everything is bounded (see
    ``retention`` in :meth:`JankMonitor.summary`).

Both the clock and the stack getter are constructor arguments, so a test can
inject a blocking call and assert the captured stack names that call.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from folith.trace import compute_percentiles

VERSION = 1
SLOW_MS = 100.0
SEVERE_MS = 500.0
DEFAULT_PULSE_MS = 25.0
DEFAULT_POLL_MS = 20.0
DEFAULT_TOP_N = 200
DEFAULT_RECENT_LIMIT = 50
DEFAULT_SAMPLE_LIMIT = 20000
STACK_DEPTH = 16
ENV_SWITCH = "FOLITH_JANK_MONITOR"

_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
_SELF_FILE = Path(__file__).name
#: Aggregation key for stalls whose stack could not be captured (held GIL, etc.).
UNCAPPED_STACK: Tuple[str, ...] = ("<stack not captured>",)


def jank_monitor_enabled(environ: Optional[Dict[str, str]] = None) -> bool:
    """Whether the sampler should run.  Unset/empty means enabled (default on)."""
    source = os.environ if environ is None else environ
    value = source.get(ENV_SWITCH)
    if value is None or not str(value).strip():
        return True
    return str(value).strip().lower() not in _DISABLED_VALUES


def _iso(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat(timespec="milliseconds")


def _rate(count: int, observed_s: float) -> float:
    """Events per minute over the observed window (0 when nothing was observed)."""
    if observed_s <= 0.0:
        return 0.0
    return round(count * 60.0 / observed_s, 2)


def render_frames(frame: Any, *, limit: int = STACK_DEPTH) -> Tuple[str, ...]:
    """Render the outermost-first Python frames reachable from *frame*."""
    frames: List[str] = []
    while frame is not None and len(frames) < limit:
        code = frame.f_code
        frames.append(f"{Path(code.co_filename).name}:{frame.f_lineno} in {code.co_name}")
        frame = frame.f_back
    frames.reverse()
    return tuple(frames)


def capture_thread_stack(ident: Optional[int], *, limit: int = STACK_DEPTH) -> Tuple[str, ...]:
    """Python stack of another thread through ``sys._current_frames()``.

    Returns an empty tuple when the thread is gone or holds the GIL inside a C
    extension: in both cases there is no Python frame to attribute, and inventing
    one would be worse than recording that it is missing.
    """
    if ident is None:
        return ()
    frame = sys._current_frames().get(ident)
    if frame is None:
        return ()
    return render_frames(frame, limit=limit)


class JankMonitor:
    """Bounded, injectable GUI-thread stall sampler.

    ``clock`` must be monotonic and ``wall_clock`` wall time; both are injected
    so tests can drive the thresholds deterministically.  ``stack_getter`` takes
    a thread ident and returns rendered frames.
    """

    def __init__(
        self,
        *,
        slow_ms: float = SLOW_MS,
        severe_ms: float = SEVERE_MS,
        pulse_interval_ms: float = DEFAULT_PULSE_MS,
        poll_interval_ms: float = DEFAULT_POLL_MS,
        top_n: int = DEFAULT_TOP_N,
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        stack_getter: Callable[[Optional[int]], Tuple[str, ...]] = capture_thread_stack,
        gui_thread_ident: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        if severe_ms <= slow_ms:
            raise ValueError("severe_ms must be greater than slow_ms")
        if slow_ms <= 0.0 or pulse_interval_ms <= 0.0:
            raise ValueError("thresholds and pulse interval must be positive")
        self.slow_ms = float(slow_ms)
        self.severe_ms = float(severe_ms)
        self.pulse_interval_ms = float(pulse_interval_ms)
        self.poll_interval_ms = float(poll_interval_ms)
        self.top_n = max(1, int(top_n))
        self._clock = clock
        self._wall_clock = wall_clock
        self._stack_getter = stack_getter
        self._gui_ident = gui_thread_ident
        self._enabled = jank_monitor_enabled() if enabled is None else bool(enabled)

        self._lock = threading.RLock()
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=max(1, int(sample_limit)))
        self._stall_samples: Deque[Tuple[float, float]] = deque(maxlen=max(1, int(sample_limit)))
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(recent_limit)))
        self._aggregates: "OrderedDict[Tuple[str, ...], Dict[str, Any]]" = OrderedDict()
        self._last_pulse: Optional[float] = None
        self._observed_from: Optional[float] = None
        self._pulse_count = 0
        self._slow_count = 0
        self._severe_count = 0
        self._capture_attempts = 0
        self._capture_successes = 0
        self._pending_stack: Tuple[str, ...] = ()
        self._longest: Optional[Dict[str, Any]] = None
        self._phase = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._timer: Any = None
        self._started = False

    # -- lifecycle ---------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def timer(self) -> Any:
        return self._timer

    def start(self, timer: Any = None) -> None:
        """Start the pulse timer (GUI thread) and the stack watcher.

        Call from the GUI thread.  Without a ``QCoreApplication`` (pure-logic
        use in tests) no timer is created and pulses are driven by calling
        :meth:`pulse`/:meth:`mark_pulse` directly.
        """
        if not self._enabled:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            now = self._clock()
            self._last_pulse = now
            self._observed_from = now
            # A capture from a previous session must not be attributed to the
            # first stall of this one.
            self._pending_stack = ()
            if self._gui_ident is None:
                self._gui_ident = threading.get_ident()
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, name="FolithJankWatcher",
                                        daemon=True)
        self._thread.start()
        if timer is None:
            timer = self._qt_timer()
        self._timer = timer
        if timer is not None:
            try:
                timer.start(max(1, int(self.pulse_interval_ms)))
            except RuntimeError:  # no event loop on this thread: nothing to sample
                self._timer = None

    def stop(self) -> Dict[str, Any]:
        """Stop sampling and return the final summary.  Idempotent."""
        timer, self._timer = self._timer, None
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass
            try:
                timer.deleteLater()
            except RuntimeError:
                pass
        self._stop.set()
        thread, self._thread = self._thread, None
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=2.0)
        with self._lock:
            self._started = False
            self._pending_stack = ()
        return self.summary()

    def _qt_timer(self) -> Any:
        """A precise repeating timer parented to nothing, or None without Qt."""
        try:
            from PySide6.QtCore import QCoreApplication, QTimer
            from PySide6.QtCore import Qt as QtCore
        except ImportError:  # pragma: no cover - Qt is a hard desktop dependency
            return None
        if QCoreApplication.instance() is None:
            return None
        timer = QTimer()
        timer.setTimerType(QtCore.TimerType.PreciseTimer)
        timer.setInterval(max(1, int(self.pulse_interval_ms)))
        timer.timeout.connect(self.pulse)
        return timer

    # -- GUI thread side ---------------------------------------------------
    def pulse(self) -> None:
        """``QTimer`` slot."""
        self.mark_pulse()

    def set_phase(self, phase: str) -> None:
        """Label subsequent stall events with the scenario step that was running."""
        self._phase = str(phase or "")

    def mark_pulse(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Record one event-loop pulse; return the jank event when one was taken.

        The fast path costs one monotonic read, one lock acquisition, one
        counter increment and one bounded-deque append (one tuple); rendering a
        stack never happens on the GUI thread, and only while a stall is
        suspected.
        """
        if not self._enabled:
            return None
        moment = self._clock() if now is None else float(now)
        with self._lock:
            last, self._last_pulse = self._last_pulse, moment
            if last is None:
                self._observed_from = moment
                return None
            interval_ms = (moment - last) * 1000.0
            self._pulse_count += 1
            self._samples.append((moment, interval_ms))
            stack = self._pending_stack
            self._pending_stack = ()
            if interval_ms < self.slow_ms:
                return None
            if stack and _SELF_FILE in stack[-1]:
                # Captured while the timer callback itself was on top: that is
                # the sampler, not the thing that blocked the GUI.
                stack = ()
            return self._record_locked(moment, interval_ms, stack)

    def _record_locked(self, mono: float, interval_ms: float,
                       stack: Tuple[str, ...]) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "at": _iso(self._wall_clock()),
            "monotonic": round(mono, 3),
            "interval_ms": round(interval_ms, 1),
            "blocked_ms": round(max(0.0, interval_ms - self.pulse_interval_ms), 1),
            "severity": "severe" if interval_ms >= self.severe_ms else "slow",
            # The thread the stack belongs to: on the desktop it is the Python
            # main thread, because that is where Qt runs the GUI.
            "thread_ident": self._gui_ident,
            "stack_captured": bool(stack),
            "stack": list(stack),
        }
        if self._phase:
            event["phase"] = self._phase
        self._slow_count += 1
        if interval_ms >= self.severe_ms:
            self._severe_count += 1
        self._stall_samples.append((mono, interval_ms))
        key = stack or UNCAPPED_STACK
        entry = self._aggregates.get(key)
        if entry is None:
            entry = {"count": 0, "total_ms": 0.0, "max_ms": 0.0,
                     "first_at": event["at"], "last_at": event["at"]}
            self._aggregates[key] = entry
        # Accumulate *before* trimming.  A key enters the map with total_ms 0,
        # so trimming first would let every new hotspot evict itself and never
        # reach top_stacks — the opposite of what the report promises.
        entry["count"] += 1
        entry["total_ms"] = round(entry["total_ms"] + interval_ms, 1)
        entry["max_ms"] = round(max(entry["max_ms"], interval_ms), 1)
        entry["last_at"] = event["at"]
        self._trim_aggregates_locked(keep=key)
        self._recent.append(event)
        if self._longest is None or interval_ms > self._longest["interval_ms"]:
            self._longest = dict(event)
        return event

    def _trim_aggregates_locked(self, *, keep: Tuple[str, ...]) -> None:
        """Drop the least-accumulated stack, never the one just observed.

        Counters (``slow_count``, ``severe_count``, ``longest``) stay exact; a
        stack that leaves the Top-N map simply stops contributing to
        ``total_ms``.  ``keep`` protects the newest observation: it exists so a
        brand-new hotspot cannot evict itself before it is visible.
        """
        while len(self._aggregates) > self.top_n:
            candidates = [item for item in self._aggregates if item != keep]
            if not candidates:
                return
            victim = min(candidates, key=lambda item: self._aggregates[item]["total_ms"])
            del self._aggregates[victim]

    # -- helper thread -----------------------------------------------------
    def _watch(self) -> None:
        """Capture the GUI thread's stack only while it is suspected of blocking."""
        poll_s = max(0.005, self.poll_interval_ms / 1000.0)
        # One period is the expected wait; two means the timer itself is late.
        capture_after_s = max(self.pulse_interval_ms * 2.0, self.poll_interval_ms * 1.5) / 1000.0
        while not self._stop.wait(poll_s):
            with self._lock:
                if not self._enabled or self._last_pulse is None:
                    continue
                stamp = self._last_pulse
                ident = self._gui_ident
            if self._clock() - stamp < capture_after_s:
                # GUI is healthy (or simply between pulses): nothing to attribute.
                with self._lock:
                    if self._last_pulse == stamp:
                        self._pending_stack = ()
                continue
            try:
                stack = self._stack_getter(ident)
            except Exception:  # a measurement must never break the app
                stack = ()
            with self._lock:
                if self._last_pulse != stamp:
                    continue  # the GUI resumed while we were reading its frames
                self._capture_attempts += 1
                if stack:
                    self._capture_successes += 1
                    # Keep the deepest stack seen during the stall.  The last
                    # capture can land just *after* the block ended, when the
                    # thread is back in the event loop and only the module frame
                    # remains; the deeper capture names what actually blocked.
                    if len(stack) > len(self._pending_stack):
                        self._pending_stack = tuple(stack)

    # -- reporting ---------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """Compact counters for the once-a-second UI health heartbeat."""
        with self._lock:
            top_function = ""
            if self._aggregates:
                worst = max(self._aggregates,
                            key=lambda item: self._aggregates[item]["total_ms"])
                if worst != UNCAPPED_STACK and worst:
                    top_function = worst[-1]
            return {
                "enabled": self._enabled,
                "slow": self._slow_count,
                "severe": self._severe_count,
                "longest_ms": round(self._longest["interval_ms"], 1) if self._longest else 0.0,
                "top_function": top_function,
            }

    def summary(self) -> Dict[str, Any]:
        """Whole-session view (bounded) of the stalls observed so far."""
        with self._lock:
            now = self._clock()
            observed = max(0.0, now - self._observed_from) if self._observed_from else 0.0
            intervals = [value for _, value in self._samples]
            stalls = [value for _, value in self._stall_samples]
            ranked = sorted(self._aggregates.items(),
                            key=lambda item: item[1]["total_ms"], reverse=True)
            return {
                "version": VERSION,
                "scope": "process_since_monitor_start",
                "enabled": self._enabled,
                "observed_s": round(observed, 3),
                "pulse_interval_ms": self.pulse_interval_ms,
                "thresholds_ms": {"slow": self.slow_ms, "severe": self.severe_ms},
                "pulse_count": self._pulse_count,
                "slow_count": self._slow_count,
                "severe_count": self._severe_count,
                "slow_per_minute": _rate(self._slow_count, observed),
                "severe_per_minute": _rate(self._severe_count, observed),
                "interval_ms": compute_percentiles(intervals),
                "stall_ms": compute_percentiles(stalls),
                "longest": dict(self._longest) if self._longest else None,
                "top_stacks": [
                    {
                        "stack": list(key) if key != UNCAPPED_STACK else [],
                        "count": entry["count"],
                        "total_ms": entry["total_ms"],
                        "max_ms": entry["max_ms"],
                        "first_at": entry["first_at"],
                        "last_at": entry["last_at"],
                    }
                    for key, entry in ranked
                ],
                "recent": [dict(event) for event in self._recent],
                "stack_capture": {
                    "gui_thread_ident": self._gui_ident,
                    "gui_thread_is_main": self._gui_ident == threading.main_thread().ident,
                    "attempts": self._capture_attempts,
                    "succeeded": self._capture_successes,
                },
                "retention": {
                    "top_stacks": self.top_n,
                    "recent_events": self._recent.maxlen,
                    "interval_samples": self._samples.maxlen,
                    "stall_samples": self._stall_samples.maxlen,
                },
            }

    def checkpoint(self) -> Dict[str, Any]:
        """Opaque marker for :meth:`summary_since` (taken when a run starts)."""
        with self._lock:
            return {
                "at": self._clock(),
                "pulse_count": self._pulse_count,
                "slow_count": self._slow_count,
                "severe_count": self._severe_count,
                "aggregates": {
                    key: (entry["count"], entry["total_ms"])
                    for key, entry in self._aggregates.items()
                },
            }

    def summary_since(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Run-scoped view: everything recorded after *checkpoint* was taken.

        Counters are exact differences.  ``top_stacks[].count/total_ms`` are
        exact for the window; ``max_ms`` is ``None`` when that stack also stalled
        before the window, because a delta of maxima is not derivable.
        """
        with self._lock:
            start = float(checkpoint.get("at") or 0.0)
            now = self._clock()
            observed = max(0.0, now - start)
            before = checkpoint.get("aggregates") or {}
            pulses = self._pulse_count - int(checkpoint.get("pulse_count") or 0)
            slow = self._slow_count - int(checkpoint.get("slow_count") or 0)
            severe = self._severe_count - int(checkpoint.get("severe_count") or 0)
            stalls = [value for mono, value in self._stall_samples if mono >= start]
            events = [dict(event) for event in self._recent
                      if float(event.get("monotonic") or 0.0) >= start]
            rows = []
            for key, entry in self._aggregates.items():
                prev = before.get(key)
                count = entry["count"] - (prev[0] if prev else 0)
                if count <= 0:
                    continue
                total = round(entry["total_ms"] - (prev[1] if prev else 0.0), 1)
                peak = entry["max_ms"] if (prev is None or count == entry["count"]) else None
                rows.append({
                    "stack": list(key) if key != UNCAPPED_STACK else [],
                    "count": count,
                    "total_ms": total,
                    "max_ms": peak,
                })
            rows.sort(key=lambda row: row["total_ms"], reverse=True)
            longest = max(events, key=lambda event: event["interval_ms"]) if events else None
            return {
                "version": VERSION,
                "scope": "run_window",
                "enabled": self._enabled,
                "window": {
                    "started_at": _iso(self._wall_clock() - observed),
                    "observed_s": round(observed, 3),
                },
                "pulse_interval_ms": self.pulse_interval_ms,
                "thresholds_ms": {"slow": self.slow_ms, "severe": self.severe_ms},
                "pulse_count": pulses,
                "slow_count": slow,
                "severe_count": severe,
                "slow_per_minute": _rate(slow, observed),
                "severe_per_minute": _rate(severe, observed),
                "stall_ms": compute_percentiles(stalls),
                "longest": longest,
                "top_stacks": rows[: self.top_n],
                "notes": [
                    "counters and per-stack count/total_ms are exact for this window",
                    "top_stacks[].max_ms is null when that stack also stalled before the window",
                    "longest names the largest stall still present in the retained event list",
                    "stall_ms percentiles cover retained stall samples inside the window",
                ],
            }
