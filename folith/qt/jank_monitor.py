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

Other threads
    Naming the GUI function says *where* the stall happened; it does not say who
    else was runnable at that moment.  So the same ``sys._current_frames()``
    snapshot also yields every other thread's stack, normalised to its innermost
    non-framework (application) frame.  Repeated observations are ranked into
    ``other_threads``: who was doing what while the GUI was blocked, how often,
    how many of those samples caught it executing application code, and for how
    much accumulated stall time.  A thread inside pure-Python work is named by
    its own function, so a GIL hog shows up directly instead of being inferred.

    Limitation: ``sys._current_frames()`` sees Python frames only.  A thread
    blocked in a C call (socket, C lock) reports the Python caller that made the
    call, so the ranking says where each thread is in *Python* code, not which
    thread holds the GIL at that exact instant.

Cost
    GUI thread per pulse: one ``time.monotonic()`` read, one lock acquisition,
    one counter increment and one bounded-deque append (which allocates one
    tuple).  No stack work ever happens on the GUI thread.
    Helper thread: an ``Event.wait`` in a loop with two lock acquisitions and
    one monotonic read per poll, plus one ``sys._current_frames`` call per poll
    *only while a stall is suspected*.  The all-thread snapshot is taken at most
    once per stall (not once per poll), walks at most ``STACK_DEPTH`` frames for
    at most ``MAX_OTHER_THREADS`` threads, and stops when
    ``OTHER_THREADS_BUDGET_MS`` of wall time is used up.  Everything is bounded
    (see ``retention`` in :meth:`JankMonitor.summary`).

Both the clock and the stack getter are constructor arguments, so a test can
inject a blocking call and assert the captured stack names that call.
"""
from __future__ import annotations

import contextlib
import os
import sys
import sysconfig
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, NamedTuple, Optional, Tuple

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
#: Threads other than the GUI one described per stall event.  The aggregate
#: ranking keeps all of them (bounded by ``top_n``); the per-event list is the
#: part a run record carries, so it stays small.
OTHER_THREADS_PER_EVENT = 5
#: Cap on threads inspected in one snapshot and on the wall time the snapshot
#: may take.  The watcher only runs while the GUI is blocked, so an unbounded
#: walk would add load exactly when it hurts most.
MAX_OTHER_THREADS = 64
OTHER_THREADS_BUDGET_MS = 4.0
#: A frame rendered from a place with no usable source location.
_LIBRARY_FRAME_PREFIX = "<library> "
ENV_SWITCH = "FOLITH_JANK_MONITOR"

_DISABLED_VALUES = frozenset({"0", "false", "no", "off", "disabled"})
_SELF_FILE = Path(__file__).name
_SELF_PATH = os.path.realpath(__file__)
#: Aggregation key for stalls whose stack could not be captured (held GIL, etc.).
UNCAPPED_STACK: Tuple[str, ...] = ("<stack not captured>",)
#: Thread name of the sampler's own watcher: never a suspect.
WATCHER_THREAD_NAME = "FolithJankWatcher"


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


def _longest_product_stall(events: Iterable[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Largest *product* stall among *events*, or ``None``.

    This is the single definition of "longest" the module reports: every entry
    point (``summary()`` and ``summary_since()``) goes through it, so the two
    scopes cannot drift apart.  Instrumentation stalls are filtered here and
    nowhere else — they are the measuring harness's own cost, so they never
    enter the user-visible jank statistics even when one is the largest event
    retained.  Ties keep the earliest event, and ``None`` entries (an unset
    running maximum) are skipped.
    """
    best: Optional[Dict[str, Any]] = None
    for event in events:
        if event is None or event.get("instrumentation"):
            continue
        if best is None or event["interval_ms"] > best["interval_ms"]:
            best = event
    return best


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


def _library_roots() -> Tuple[str, ...]:
    """Directories whose frames are "the framework", not the application."""
    roots = set()
    try:
        paths = sysconfig.get_paths()
    except Exception:  # pragma: no cover - a broken sysconfig must not break sampling
        paths = {}
    # Deliberately not ``sys.prefix``: in a frozen bundle it is the
    # application's own root, and every application frame would stop counting.
    for name in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = paths.get(name)
        if value:
            roots.add(os.path.realpath(value))
    return tuple(sorted(roots))


_LIBRARY_ROOTS = _library_roots()


#: ``co_filename`` -> framework?  ``os.path.realpath`` walks the path with
#: syscalls, and this predicate runs once per frame of every thread in a
#: snapshot; caching turns a walk into a dict lookup.
_FRAMEWORK_PATH_CACHE: Dict[str, bool] = {}


def frame_is_framework(frame: Any) -> bool:
    """Whether *frame* belongs to the stdlib/venv or to this module.

    Used to normalise a thread's stack to the innermost frame that is actually
    the application's: "``threading.py`` is waiting two frames below my worker"
    must not be reported as the answer to "what was that thread doing?".
    """
    filename = frame.f_code.co_filename
    cached = _FRAMEWORK_PATH_CACHE.get(filename)
    if cached is not None:
        return cached
    if not filename or filename.startswith("<"):
        # "<frozen threading>", "<string>": no source location to blame.
        return True
    resolved = os.path.realpath(filename)
    if resolved == _SELF_PATH:
        result = True
    else:
        result = any(resolved == root or resolved.startswith(root + os.sep)
                     for root in _LIBRARY_ROOTS)
    if len(_FRAMEWORK_PATH_CACHE) < 4096:
        _FRAMEWORK_PATH_CACHE[filename] = result
    return result


#: ``co_filename`` -> basename.  Rendering a label is on the hot path of a
#: bounded snapshot; ``Path(...).name`` is far more expensive than this lookup.
_BASENAME_CACHE: Dict[str, str] = {}


def _basename(filename: str) -> str:
    value = _BASENAME_CACHE.get(filename)
    if value is None:
        value = Path(filename).name
        if len(_BASENAME_CACHE) < 4096:
            _BASENAME_CACHE[filename] = value
    return value


def attribute_frame(frame: Any, *, limit: int = STACK_DEPTH) -> Tuple[str, bool]:
    """Normalise one thread's stack to ``(frame, executing_application_code)``.

    Frames are walked innermost-outwards (``f_back``), so the first
    non-framework frame is the deepest application code the thread was in.  That
    is the aggregation key: a busy loop is named by its own function even when
    the sample happened to land inside a library call it made
    (``threading.Event.is_set``), so one thread cannot be split across two rows.

    When every frame is framework (an idle pool worker parked in ``queue.get``),
    the innermost frame is returned with a ``<library>`` prefix: the reader still
    learns *where* it waited, and it cannot be mistaken for application code.

    The second value says the *innermost* frame was application code, i.e. this
    thread was executing Python app code at that instant rather than parked
    inside a Python-level library wait.  It is a per-sample signal: a busy loop
    that happens to be inside ``Event.is_set`` at one sample is still reported as
    running at the next.  Limitation: a thread blocked inside a C call
    (``socket.recv``, a C lock) still reports the application frame that made the
    call, so this cannot see inside C.
    """
    innermost: Optional[str] = None
    walked = 0
    current = frame
    while current is not None and walked < limit:
        code = current.f_code
        label = f"{_basename(code.co_filename)}:{current.f_lineno} in {code.co_name}"
        if innermost is None:
            innermost = label
        if not frame_is_framework(current):
            return label, walked == 0
        current = current.f_back
        walked += 1
    return _LIBRARY_FRAME_PREFIX + (innermost or "<no frame>"), False


class ThreadFrame(NamedTuple):
    """One non-GUI thread as seen in a snapshot."""

    name: str
    #: Innermost non-framework frame, or ``<library> ...`` for a pure wait.
    frame: str
    #: The innermost frame itself was application code: the thread was executing
    #: Python app code, not parked inside a Python-level library wait.
    running: bool = False


#: ident -> one observation of that thread
OtherThreadFrames = Dict[int, ThreadFrame]


def capture_other_thread_frames(
    exclude: Optional[int] = None,
    *,
    limit: int = STACK_DEPTH,
    max_threads: int = MAX_OTHER_THREADS,
    budget_ms: float = OTHER_THREADS_BUDGET_MS,
) -> Tuple[OtherThreadFrames, bool]:
    """Every thread's innermost application frame, except *exclude*.

    One ``sys._current_frames()`` snapshot, then a bounded walk per thread.
    Returns ``({ident: ThreadFrame}, truncated)``; *truncated* says the thread
    cap or the time budget cut the snapshot short, so a reader knows the ranking
    was built from a partial view rather than from "nothing else was running".
    """
    try:
        frames = sys._current_frames()
    except Exception:  # pragma: no cover - defensive: never break the app
        return {}, True
    current = threading.current_thread()
    names: Dict[int, str] = {}
    try:
        for thread in threading.enumerate():
            names[thread.ident] = thread.name
    except Exception:  # pragma: no cover - enumerate needs an internal lock
        names = {current.ident: current.name}
    targets: List[Tuple[int, str, Any]] = []
    for ident, frame in frames.items():
        if ident == exclude or ident == current.ident:
            continue
        name = names.get(ident) or f"thread-{ident}"
        if name == WATCHER_THREAD_NAME:
            continue
        targets.append((ident, name, frame))
    # Application threads are walked first.  The budget can expire under GIL
    # contention (the watcher is competing with the very thread it measures), and
    # a truncated snapshot must keep the suspects, not whichever thread the
    # frame map happened to list first.
    targets.sort(key=lambda item: (frame_is_framework(item[2]), item[0]))
    deadline = time.monotonic() + max(0.0, float(budget_ms)) / 1000.0
    result: OtherThreadFrames = {}
    truncated = False
    inspected = 0
    for ident, name, frame in targets:
        if inspected >= max(1, int(max_threads)):
            truncated = True
            break
        if time.monotonic() > deadline:
            truncated = True
            break
        inspected += 1
        label, running = attribute_frame(frame, limit=limit)
        result[ident] = ThreadFrame(name=name, frame=label, running=running)
    return result, truncated


def _ranked_thread_rows(threads: OtherThreadFrames) -> List[ThreadFrame]:
    """Observations of one snapshot: application frames before pure library waits.

    A thread parked in a library wait is not a suspect, so it must not lead the
    per-event evidence on an equal contribution.  The frame name breaks the
    remaining ties so the output is deterministic.
    """
    rows = list(threads.values())
    rows.sort(key=lambda row: (row.frame.startswith(_LIBRARY_FRAME_PREFIX), row.frame))
    return rows


def _blame_sort_key(item: Tuple[str, Dict[str, Any]]) -> Tuple[float, int, int, int]:
    """Rank by accumulated stall time, application frames ahead of library waits."""
    frame, entry = item
    return (entry["total_ms"],
            0 if frame.startswith(_LIBRARY_FRAME_PREFIX) else 1,
            entry["running"],
            entry["count"])


def _blame_row(entry: Dict[str, Any]) -> Dict[str, Any]:
    """One "what were the other threads doing" row, ready for JSON."""
    return {
        "frame": entry["frame"],
        "threads": list(entry["threads"]),
        "count": entry["count"],
        #: Observations in which the thread was executing application code.
        "running": entry["running"],
        "total_ms": entry["total_ms"],
        "max_ms": entry["max_ms"],
        "first_at": entry["first_at"],
        "last_at": entry["last_at"],
    }


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
        threads_getter: Callable[
            [Optional[int]], Tuple[OtherThreadFrames, bool]
        ] = capture_other_thread_frames,
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
        self._threads_getter = threads_getter
        self._gui_ident = gui_thread_ident
        self._enabled = jank_monitor_enabled() if enabled is None else bool(enabled)

        self._lock = threading.RLock()
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=max(1, int(sample_limit)))
        self._stall_samples: Deque[Tuple[float, float]] = deque(maxlen=max(1, int(sample_limit)))
        # #327: stalls caused by the measurement harness itself (predicate
        # polling, scripted-fallback pumping) are counted apart and never mixed
        # into the product numbers.
        self._instrumentation_slow_count = 0
        self._instrumentation_severe_count = 0
        self._instrumentation_total_ms = 0.0
        self._instrumentation_stall_samples: Deque[Tuple[float, float]] = deque(
            maxlen=max(1, int(sample_limit)))
        # F2: pulse intervals recorded while an instrumentation window covered
        # them.  They are the harness's latency, so they get their own
        # distribution and never reach ``interval_ms``.
        self._instrumentation_pulse_samples: Deque[Tuple[float, float]] = deque(
            maxlen=max(1, int(sample_limit)))
        self._instrumentation_depth = 0
        self._instrumentation_until: Optional[float] = None
        self._recent: Deque[Dict[str, Any]] = deque(maxlen=max(1, int(recent_limit)))
        self._aggregates: "OrderedDict[Tuple[str, ...], Dict[str, Any]]" = OrderedDict()
        # F1: suffix maxima of product stalls.  ``_recent`` is a ring, so the
        # largest stall could leave it and ``summary_since`` would report a
        # smaller value as "longest"; this chain keeps exactly the events that
        # are still the maximum at or after their own position, so a window
        # query is exact and its size is bounded by the same ``sample_limit``.
        self._maxima: Deque[Tuple[float, Dict[str, Any]]] = deque(
            maxlen=max(1, int(sample_limit)))
        # "who else was runnable while the GUI was blocked", grouped by frame.
        self._thread_blame: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._last_pulse: Optional[float] = None
        self._observed_from: Optional[float] = None
        self._pulse_count = 0
        self._slow_count = 0
        self._severe_count = 0
        self._capture_attempts = 0
        self._capture_successes = 0
        self._thread_capture_attempts = 0
        self._thread_capture_successes = 0
        self._thread_capture_truncated = 0
        self._pending_stack: Tuple[str, ...] = ()
        self._pending_threads: OtherThreadFrames = {}
        self._stall_capture_open = False
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
            self._pending_threads = {}
            self._stall_capture_open = False
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
            self._pending_threads = {}
            self._stall_capture_open = False
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

    def set_instrumentation(self, active: bool) -> None:
        """Mark harness work that is not part of what the product is measured on.

        A stall is attributed to the instrumentation when an instrumentation
        window overlapped the interval it covers: the window's end is recorded
        when it closes, and any stall whose interval started before that end is
        mine, not the product's.  Instrumentation stalls are counted in
        ``instrumentation_slow_count`` and excluded from ``slow_count``,
        percentiles, ``longest`` and ``top_stacks``; every interval (stall or
        not) that the window covered goes to ``instrumentation_interval_ms``
        instead of ``interval_ms``.
        """
        with self._lock:
            if active:
                self._instrumentation_depth += 1
                return
            self._instrumentation_depth = max(0, self._instrumentation_depth - 1)
            self._instrumentation_until = self._clock()

    @contextlib.contextmanager
    def instrumentation(self):
        """``with jank.instrumentation():`` around harness-only work."""
        self.set_instrumentation(True)
        try:
            yield
        finally:
            self.set_instrumentation(False)

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
            stack, threads = self._pending_stack, self._pending_threads
            self._pending_stack = ()
            self._pending_threads = {}
            # The capture window is closed: a later stall needs a fresh snapshot.
            self._stall_capture_open = False
            # Strictly after the interval start: a window that closed exactly at
            # the previous pulse does not cover this interval.
            instrumented = (self._instrumentation_until is not None
                            and self._instrumentation_until > last)
            # F2: attribution applies to *every* interval, not only to stalls.
            # A 40 ms interval inside a predicate poll is the harness's latency
            # too; letting it into ``_samples`` inflates the all-pulse
            # percentiles that the absolute target is read from.
            if instrumented:
                self._instrumentation_pulse_samples.append((moment, interval_ms))
            else:
                self._samples.append((moment, interval_ms))
            if interval_ms < self.slow_ms:
                return None
            if stack and _SELF_FILE in stack[-1]:
                # Captured while the timer callback itself was on top: that is
                # the sampler, not the thing that blocked the GUI.
                stack = ()
            return self._record_locked(moment, interval_ms, stack,
                                       instrumentation=instrumented,
                                       threads=threads)

    def _record_locked(self, mono: float, interval_ms: float,
                       stack: Tuple[str, ...], *,
                       instrumentation: bool = False,
                       threads: Optional[OtherThreadFrames] = None) -> Dict[str, Any]:
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
        event["instrumentation"] = bool(instrumentation)
        if instrumentation:
            # Part of the measurement tool, not the product's responsiveness.
            self._instrumentation_slow_count += 1
            if interval_ms >= self.severe_ms:
                self._instrumentation_severe_count += 1
            self._instrumentation_total_ms = round(
                self._instrumentation_total_ms + interval_ms, 1)
            self._instrumentation_stall_samples.append((mono, interval_ms))
            self._recent.append(event)
            return event
        if threads:
            # Evidence trail: the few other threads that were runnable at the
            # same instant as the GUI stack above.  Kept on the event itself so
            # a run record can be read without the aggregate.
            event["other_threads"] = [
                {"thread": row.name, "frame": row.frame, "running": row.running}
                for row in _ranked_thread_rows(threads)[:OTHER_THREADS_PER_EVENT]
            ]
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
        self._record_thread_blame_locked(threads or {}, interval_ms, event["at"])
        self._recent.append(event)
        # Same rule as the run window: a product stall only wins if it beats the
        # previous product maximum.  Instrumentation can never win, by
        # construction of the branch above.
        self._longest = _longest_product_stall((self._longest, dict(event)))
        self._record_maximum_locked(mono, event)
        return event

    def _record_maximum_locked(self, mono: float, event: Dict[str, Any]) -> None:
        """Maintain the suffix-maxima chain used by :meth:`summary_since`.

        An event that is smaller than some event after it can never be the
        maximum of any window that starts before it, so it is dropped.  What
        remains is a chain whose ``interval_ms`` never increases front to back;
        the first entry at or after a window start is that window's maximum.

        Equal sizes are *kept*, not collapsed: a window may start between two
        equally long stalls, and dropping the earlier one would leave that window
        with no maximum at all.  Keeping both also matches
        :func:`_longest_product_stall` ("ties keep the earliest").  The chain is
        bounded by ``sample_limit`` like every other ring.
        """
        while self._maxima and self._maxima[-1][1]["interval_ms"] < event["interval_ms"]:
            self._maxima.pop()
        self._maxima.append((mono, dict(event)))

    def _record_thread_blame_locked(self, threads: OtherThreadFrames,
                                    interval_ms: float, at: str) -> None:
        """Attribute one stall to the other threads' innermost app frames."""
        keys = []
        for observation in threads.values():
            frame = observation.frame
            entry = self._thread_blame.get(frame)
            if entry is None:
                entry = {"frame": frame, "count": 0, "running": 0, "total_ms": 0.0,
                         "max_ms": 0.0, "threads": [], "first_at": at, "last_at": at}
                self._thread_blame[frame] = entry
            keys.append(frame)
            entry["count"] += 1
            if observation.running:
                entry["running"] += 1
            entry["total_ms"] = round(entry["total_ms"] + interval_ms, 1)
            entry["max_ms"] = round(max(entry["max_ms"], interval_ms), 1)
            entry["last_at"] = at
            if observation.name and observation.name not in entry["threads"]:
                entry["threads"].append(observation.name)
        if keys:
            self._trim_thread_blame_locked(keep=set(keys))

    def _trim_thread_blame_locked(self, *, keep: "set[str]") -> None:
        """Drop the least-accumulated blamed frame, never one just observed."""
        while len(self._thread_blame) > self.top_n:
            candidates = [item for item in self._thread_blame if item not in keep]
            if not candidates:
                return
            victim = min(candidates, key=lambda item: self._thread_blame[item]["total_ms"])
            del self._thread_blame[victim]

    def _longest_product_stall_since_locked(self, start: float) -> Optional[Dict[str, Any]]:
        """Largest product stall at or after *start* (exact, from the chain).

        ``_recent`` is a ring and can drop the largest stall of a long run; the
        maxima chain cannot, so this is the value ``stall_ms.max`` would agree
        with instead of an under-reported one.  The same
        :func:`_longest_product_stall` rule decides eligibility, so both scopes
        keep one definition of "longest".
        """
        return _longest_product_stall(
            dict(event) for mono, event in self._maxima if mono >= start)

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
                need_threads = not self._stall_capture_open
            if self._clock() - stamp < capture_after_s:
                # GUI is healthy (or simply between pulses): nothing to attribute.
                with self._lock:
                    if self._last_pulse == stamp:
                        self._pending_stack = ()
                        self._pending_threads = {}
                        self._stall_capture_open = False
                continue
            try:
                stack = self._stack_getter(ident)
            except Exception:  # a measurement must never break the app
                stack = ()
            # Once per stall, not once per poll: the snapshot is the expensive
            # part, and re-reading it every 20 ms would slow the very stall it
            # is measuring.
            threads: OtherThreadFrames = {}
            truncated = False
            if need_threads:
                try:
                    threads, truncated = self._threads_getter(ident)
                except Exception:  # a measurement must never break the app
                    threads, truncated = {}, False
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
                if need_threads:
                    self._stall_capture_open = True
                    self._thread_capture_attempts += 1
                    if threads:
                        self._thread_capture_successes += 1
                    if truncated:
                        self._thread_capture_truncated += 1
                    self._pending_threads = dict(threads)

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
                "instrumentation_slow": self._instrumentation_slow_count,
                "longest_ms": round(self._longest["interval_ms"], 1) if self._longest else 0.0,
                "top_function": top_function,
            }

    def summary(self) -> Dict[str, Any]:
        """Whole-session view (bounded) of the stalls observed so far.

        ``slow_count``/``severe_count``/``stall_ms``/``interval_ms``/
        ``longest``/``top_stacks``/``other_threads`` are product statistics:
        instrumentation is counted apart in the ``instrumentation_*`` fields and
        is never eligible for any of them.
        """
        with self._lock:
            now = self._clock()
            observed = max(0.0, now - self._observed_from) if self._observed_from else 0.0
            # F2: ``_samples`` holds product intervals only (all of them, healthy
            # ones included), so the absolute-target percentiles cannot be moved
            # by a 2 s harness interval.
            intervals = [value for _, value in self._samples]
            stalls = [value for _, value in self._stall_samples]
            ranked = sorted(self._aggregates.items(),
                            key=lambda item: item[1]["total_ms"], reverse=True)
            blamed = sorted(self._thread_blame.items(), key=_blame_sort_key, reverse=True)
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
                # #327: the harness's own stalls are reported apart and are
                # never part of slow_count/stall_ms/longest/top_stacks.
                "instrumentation_slow_count": self._instrumentation_slow_count,
                "instrumentation_severe_count": self._instrumentation_severe_count,
                "instrumentation_total_ms": self._instrumentation_total_ms,
                "instrumentation_stall_ms": compute_percentiles(
                    [value for _, value in self._instrumentation_stall_samples]),
                "instrumentation_interval_ms": compute_percentiles(
                    [value for _, value in self._instrumentation_pulse_samples]),
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
                "other_threads": [_blame_row(entry) for _, entry in blamed],
                "recent": [dict(event) for event in self._recent],
                "stack_capture": {
                    "gui_thread_ident": self._gui_ident,
                    "gui_thread_is_main": self._gui_ident == threading.main_thread().ident,
                    "attempts": self._capture_attempts,
                    "succeeded": self._capture_successes,
                    "other_threads_attempts": self._thread_capture_attempts,
                    "other_threads_succeeded": self._thread_capture_successes,
                    "other_threads_truncated": self._thread_capture_truncated,
                },
                "retention": {
                    "top_stacks": self.top_n,
                    "recent_events": self._recent.maxlen,
                    "interval_samples": self._samples.maxlen,
                    "stall_samples": self._stall_samples.maxlen,
                    "instrumentation_samples": self._instrumentation_stall_samples.maxlen,
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
                "instrumentation_slow_count": self._instrumentation_slow_count,
                "instrumentation_severe_count": self._instrumentation_severe_count,
                "instrumentation_total_ms": self._instrumentation_total_ms,
                "aggregates": {
                    key: (entry["count"], entry["total_ms"])
                    for key, entry in self._aggregates.items()
                },
                "other_threads": {
                    frame: (entry["count"], entry["total_ms"], entry["running"])
                    for frame, entry in self._thread_blame.items()
                },
                "gui_capture": (self._capture_attempts, self._capture_successes),
                "thread_capture": (self._thread_capture_attempts,
                                   self._thread_capture_successes,
                                   self._thread_capture_truncated),
            }

    def summary_since(self, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Run-scoped view: everything recorded after *checkpoint* was taken.

        Counters are exact differences.  ``top_stacks[].count/total_ms`` are
        exact for the window; ``max_ms`` is ``None`` when that stack also stalled
        before the window, because a delta of maxima is not derivable.
        ``longest`` uses the same rule as :meth:`summary` and the same helper, so
        instrumentation stalls are not eligible in either scope; it is taken from
        the suffix-maxima chain rather than from the ``recent`` event ring, which
        would under-report once a long run pushes the largest stall out (#327 F1).
        """
        with self._lock:
            start = float(checkpoint.get("at") or 0.0)
            now = self._clock()
            observed = max(0.0, now - start)
            before = checkpoint.get("aggregates") or {}
            before_blame = checkpoint.get("other_threads") or {}
            pulses = self._pulse_count - int(checkpoint.get("pulse_count") or 0)
            slow = self._slow_count - int(checkpoint.get("slow_count") or 0)
            severe = self._severe_count - int(checkpoint.get("severe_count") or 0)
            instrumented_slow = (self._instrumentation_slow_count
                                 - int(checkpoint.get("instrumentation_slow_count") or 0))
            instrumented_severe = (self._instrumentation_severe_count
                                   - int(checkpoint.get("instrumentation_severe_count") or 0))
            instrumented_total = round(self._instrumentation_total_ms
                                       - float(checkpoint.get("instrumentation_total_ms") or 0.0), 1)
            before_gui_capture = checkpoint.get("gui_capture") or (0, 0)
            before_thread_capture = checkpoint.get("thread_capture") or (0, 0, 0)
            instrumented_stalls = [value for mono, value in self._instrumentation_stall_samples
                                   if mono >= start]
            stalls = [value for mono, value in self._stall_samples if mono >= start]
            intervals = [value for mono, value in self._samples if mono >= start]
            instrumented_intervals = [
                value for mono, value in self._instrumentation_pulse_samples if mono >= start]
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
            blamed = []
            for frame, entry in self._thread_blame.items():
                prev = before_blame.get(frame)
                count = entry["count"] - (prev[0] if prev else 0)
                if count <= 0:
                    continue
                row = _blame_row(entry)
                row["count"] = count
                row["running"] = entry["running"] - (int(prev[2]) if prev and len(prev) > 2 else 0)
                row["total_ms"] = round(entry["total_ms"] - (prev[1] if prev else 0.0), 1)
                row["max_ms"] = (entry["max_ms"]
                                 if (prev is None or count == entry["count"]) else None)
                blamed.append(row)
            blamed.sort(key=lambda row: (row["total_ms"],
                                         0 if row["frame"].startswith(_LIBRARY_FRAME_PREFIX)
                                         else 1,
                                         row["running"],
                                         row["count"]), reverse=True)
            # Same helper as summary(): instrumentation stalls are not eligible,
            # so a 2 s predicate poll can never become the window's "longest".
            longest = self._longest_product_stall_since_locked(start)
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
                "interval_ms": compute_percentiles(intervals),
                "stall_ms": compute_percentiles(stalls),
                # #327: measurement-harness stalls, separated from product ones.
                "instrumentation_slow_count": instrumented_slow,
                "instrumentation_severe_count": instrumented_severe,
                "instrumentation_total_ms": instrumented_total,
                "instrumentation_stall_ms": compute_percentiles(instrumented_stalls),
                "instrumentation_interval_ms": compute_percentiles(instrumented_intervals),
                "longest": longest,
                "top_stacks": rows[: self.top_n],
                "other_threads": blamed[: self.top_n],
                "stack_capture": {
                    "gui_thread_ident": self._gui_ident,
                    "attempts": self._capture_attempts - int(before_gui_capture[0] or 0),
                    "succeeded": self._capture_successes - int(before_gui_capture[1] or 0),
                    "other_threads_attempts": (self._thread_capture_attempts
                                               - int(before_thread_capture[0] or 0)),
                    "other_threads_succeeded": (self._thread_capture_successes
                                                - int(before_thread_capture[1] or 0)),
                    "other_threads_truncated": (self._thread_capture_truncated
                                                - int(before_thread_capture[2] or 0)),
                },
                "notes": [
                    "counters and per-stack count/total_ms are exact for this window",
                    "top_stacks[].max_ms is null when that stack also stalled before the window",
                    "longest is the largest non-instrumentation stall in the window, "
                    "taken from the suffix-maxima chain (not from the bounded recent ring); "
                    "ties keep the earliest occurrence",
                    "interval_ms percentiles cover product pulse intervals inside the window",
                    "stall_ms percentiles cover retained stall samples inside the window",
                    "other_threads rows are the innermost non-framework frame of each non-GUI "
                    "thread seen during a stall; running counts the samples that caught it "
                    "executing application code; total_ms sums the stalls it was seen in, so "
                    "rows overlap and are not additive",
                    "slow_count/severe_count/stall_ms/interval_ms/longest/top_stacks/"
                    "other_threads exclude instrumentation stalls; instrumentation_* fields "
                    "count the harness's own stalls",
                ],
            }
