"""Deterministic "nothing else is running" for workbench tests.

A test that asserts "this refresh changed nothing" is only as strong as its
premise: that no background read, idle tick or slow projection landed between
capturing the baseline and asserting.  That premise used to be implicit.  It is
not true under load: ``MainWindow`` stops its 1 s poll timer when a test opens a
task, but a read the *open path itself* started can still be in flight, and its
``done`` callback then lands inside the test's ``processEvents()`` calls.

The 2026-11-12 Linux full regression (run 43224655060, job 131204875307) failed
exactly there: ``runtime`` and ``stage`` moved 2 -> 3 in the middle of a loop
that only called ``refresh_workbench()``.  Those two revisions are bumped by
``WorkbenchView.apply_runtime()``, i.e. a ``DeltaIdle`` tick from
``_apply_feed_result()``.  ``_state`` was never replaced and ``apply()`` ran
exactly five times, so every other assertion in the test still held and the
failure looked like "two unrelated domains moved".

Two tools live here:

* :class:`WorkbenchEvents` counts the view's write entry points as an event
  sequence, so "no background write happened" is asserted from what actually
  ran instead of inferred from "I looked twice and the numbers matched".
* :func:`wait_for_quiet` pumps the event loop until every read/projection has
  finished and no poll is pending or scheduled, then requires a few consecutive
  turns without a new event.  It returns a report the caller must assert on; a
  timeout is a failure with the pending flags named, never a silent "carry on".

Neither helper sleeps to hide a race: the timing loop only waits for observable
work to finish (and a timeout fails the test).
"""
from __future__ import annotations

import time

#: Every entry point that can change the workbench from a callback.
#: ``apply``/``_replace_state`` are counted too (a background whole-state reload
#: shows up as a new object), but they are also produced by the test's own
#: synchronous refresh, so they are not classified as background on their own.
_POLL = "_poll_workbench"
_WRITE_ENTRY_POINTS = (_POLL, "apply", "apply_runtime", "apply_delta", "_replace_state")

#: The subset that can only come from a background callback: a poll, an idle
#: runtime tick, or a journal batch.  These are what a "nothing else happened"
#: loop must not observe.
_BACKGROUND = (_POLL, "apply_runtime", "apply_delta")


class WorkbenchEvents:
    """Event sequence over the window's background write entry points."""

    def __init__(self, window, monkeypatch):
        self._counts = {name: 0 for name in _WRITE_ENTRY_POINTS}
        self._order: list[str] = []
        self._log: list[str] = []      # never reset: a timeout report shows the history
        self._churn = 0
        view = window.workbench
        for name in _WRITE_ENTRY_POINTS:
            owner = window if name == _POLL else view
            self._wrap(owner, name, monkeypatch)

    def _wrap(self, owner, name, monkeypatch) -> None:
        original = getattr(owner, name)
        counts, order, log = self._counts, self._order, self._log

        def counted(*args, **kwargs):
            counts[name] += 1
            order.append(name)
            log.append(name)
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, counted)

    def reset(self) -> None:
        for name in self._counts:
            self._counts[name] = 0
        self._order.clear()

    def count(self, name: str) -> int:
        return self._counts[name]

    def background(self) -> int:
        """How many background writes were seen since the last reset."""
        return sum(self._counts[name] for name in _BACKGROUND)

    def note_churn(self) -> None:
        self._churn += 1

    def report(self) -> dict:
        return {"events": list(self._order), "log": list(self._log),
                "counts": dict(self._counts), "churn": self._churn}


def wait_for_quiet(window, app, events: WorkbenchEvents, *, label: str = "",
                   timeout: float = 120.0, turns: int = 3) -> dict:
    """Pump until no background work can still land; return a report to assert.

    Quiet means, all at once: no state read or slow projection in flight, no
    poll pending or scheduled for re-issue, and ``turns`` consecutive event-loop
    turns in which the view's write entry points were not called at all.  The
    last condition is what makes the wait independent of how the pending work
    was started; the flags alone only describe the reads this window knows
    about.
    """
    deadline = time.monotonic() + timeout
    stable = 0
    pending: dict[str, bool] = {}
    events.reset()
    while time.monotonic() < deadline:
        app.processEvents()
        pending = {
            "state_load_in_flight": bool(getattr(window, "_state_load_in_flight", False)),
            "delta_projection_in_flight": bool(
                getattr(window, "_delta_projection_in_flight", False)),
            "pending_poll": bool(getattr(window, "_pending_poll", False)),
            "repoll_pending": bool(getattr(window, "_repoll_pending", False)),
        }
        if any(pending.values()) or events.background():
            # Work was still running (or its callback just landed): observe it,
            # then start counting quiet turns again.
            if events.background():
                events.note_churn()
            stable = 0
            events.reset()
        else:
            stable += 1
            if stable >= turns:
                report = events.report()
                report.update({"quiet": True, "label": label, "pending": pending,
                               "turns": stable})
                return report
        time.sleep(0.01)                # let the worker thread make progress
    report = events.report()
    report.update({"quiet": False, "label": label, "pending": pending,
                   "turns": stable, "timeout": timeout})
    return report
