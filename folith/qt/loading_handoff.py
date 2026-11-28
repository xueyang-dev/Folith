"""Run the work a loading frame covers, after that frame is actually painted.

Why this exists
---------------
``LoadingOverlay.present`` used to force a synchronous ``repaint()`` so the
overlay was on screen before the caller blocked.  That forced paint from a
worker-result callback is what crashed the Windows frozen package (0.8.4 G1a),
so the overlay now only schedules its frame (``update()``).  But the caller that
needs the frame — the >=300-segment "leaving Prepare" refresh — did the whole
apply in the same GUI call and hid the overlay in its ``finally``, so the
scheduled frame was never painted while it was visible.

The split, exactly as CX-31 §2 asks for it:

1. the refresh shows the overlay and returns, so the event loop can run;
2. the overlay's own ``paintEvent`` queues one continuation on the window's
   single-shot timer (``_later``, owned by the window, so closing it cancels the
   work) — the continuation is *not* run inside the paint stack;
3. the continuation re-checks that the task and the request are still valid,
   then applies.

Nothing here calls ``processEvents()``.  While the frame is pending the normal
poll keeps running; a second request replaces the pending one (``defer`` bumps
the serial), so one request applies once and a superseded request neither applies
nor hides the overlay the newer request just put up.  If no frame arrives — the
window is hidden, minimised, or painting is suppressed — the continuation runs
after ``FRAME_FALLBACK_MS`` instead of waiting for a frame that may never come.
"""
from __future__ import annotations

from typing import Callable, Optional

#: Bound on waiting for the overlay's next frame before applying anyway.
FRAME_FALLBACK_MS = 250


class PaintingHandoff:
    """One pending continuation, owned by the window that shows the overlay."""

    def __init__(self, window) -> None:
        self._window = window
        self._pending: Optional[tuple[int, str, Callable[[], None]]] = None
        self._serial = 0

    def defer(self, overlay, key: str, run: Callable[[], None]) -> None:
        """Queue *run* for after the overlay's next frame (replacing any pending)."""
        self._serial += 1
        serial = self._serial
        self._pending = (serial, str(key), run)
        # From inside paintEvent: only schedule, never work.
        overlay.run_after_next_paint(
            lambda: self._window._later(0, lambda: self._fire(serial)))
        self._window._later(FRAME_FALLBACK_MS, lambda: self._fire(serial))

    def cancel(self) -> None:
        self._pending = None

    def _fire(self, serial: int) -> None:
        pending = self._pending
        if pending is None or pending[0] != serial:
            # A newer request owns the overlay now; it applies and hides.
            return
        self._pending = None
        _serial, key, run = pending
        window = self._window
        if window._closing_accepted or str(key) != str(window._current_job_id):
            # The task moved on: drop this request and leave the overlay alone.
            # Hiding it here would take down the overlay the newer request just
            # put up; that request hides it once its own frame is on screen.
            return
        run()


def apply_workbench_refresh(window, *, job_id: str, state, rows, models,
                            prepared, force: bool, changed: bool,
                            timing) -> None:
    """The tail of ``MainWindow._refresh_workbench``: projections and apply.

    Lives here rather than in ``window.py`` because that file is at its release
    line budget, and because the deferred path and the immediate path must run
    exactly the same code.  Hiding the overlay and flushing the phase log stay
    with ``refresh_workbench``, so both paths finish the same way.
    """
    from folith.qt import adapter

    with timing.phase("runtime_view"):
        runtime = models.get("runtime") if models is not None else None
        if runtime is None:
            runtime = adapter.runtime_view(job_id, state)
    with timing.phase("delivery_snapshots"):
        snapshots = models.get("snapshots") if models is not None else None
        if snapshots is None:
            snapshots = adapter.delivery_snapshots(job_id) if (changed or force) \
                else window.workbench._snapshots
    # The delivery panel is re-validated on an explicit refresh, or when the run
    # reaches a terminal state — not on every background state write.
    status = str(runtime.get("status") or "")
    terminal = status in ("completed", "failed", "cancelled", "interrupted",
                          "stalled", "idle_incomplete")
    terminal_change = terminal and status != window._last_runtime_status
    window._last_runtime_status = status
    with timing.phase("workbench.apply"):
        window.workbench.apply(job_id, state, rows, runtime, snapshots,
                               refresh_delivery=force or terminal_change,
                               prepared=prepared, models=models)
    window._sync_menu_state()
    # 内容变了，布局的最小尺寸也可能跟着变（标题、阶段条、检查器动作条）。
    # 每次刷新同步一次，窗口的 minimumSize() 才不会比真实下限小 1–8px——
    # 否则「缩到最小尺寸」会被 Qt 悄悄放大（resize(870,463) → 871×463）。
    window._sync_minimum_size()
    # 情境提示等布局与过渡落定之后再挑（锚点要在屏幕上的最终位置）。
    window._later(600, lambda: window._offer_tips(runtime))
