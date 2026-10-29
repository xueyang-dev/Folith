"""Shared background loader for the source-outline sidecar (#0.8.0).

A single :class:`OutlineLoader` per process ensures that:

* A cold or invalidated sidecar is parsed **at most once** per source file,
  regardless of how many UI consumers (prep panel, workbench outline) are
  watching.
* Switching tasks discards stale results: the ``ready(job_id)`` signal only
  fires for the generation that is still current.
* The running parse count is visible through :meth:`is_pending`, so callers
  can show a loading indicator without polling.

The loader is Qt-free in its background thread (it calls
:func:`folith.outline_anchor.load_outline`, which is also Qt-free), so there is
no dependency on Qt inside the worker; only the ``ready`` *Signal* emission
happens on the Qt main thread.

Usage::

    ldr = outline_loader()           # process-level singleton
    payload = ldr.request(job_id, state)
    if payload is None:
        # cold — wait for ldr.ready(job_id)
        ldr.ready.connect(my_slot)
    else:
        _apply(payload)
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from folith import outline_anchor
from folith.qt import adapter

_log = logging.getLogger(__name__)

#: Generation token used to detect stale results.
#: Incremented each time a job is loaded; a background result for an older
#: generation is silently dropped.
_Generation = int


class _ParseJob(QRunnable):
    """One background parse run; dedup is handled by ``outline_anchor``."""

    def __init__(self, loader: "OutlineLoader", job_id: str,
                 generation: _Generation, state: dict) -> None:
        super().__init__()
        self._loader = loader
        self._job_id = job_id
        self._generation = generation
        self._state = dict(state or {})

    def run(self) -> None:
        try:
            # If state is empty (no filename), load it now off the GUI thread.
            state = self._state
            if not state.get("filename"):
                try:
                    from folith.qt import adapter as _adapter
                    state = _adapter.load_state(self._job_id) or state
                except Exception:  # noqa: BLE001
                    pass
            # ``no_parse=False``: will parse if needed (and dedup via
            # outline_anchor's per-job event so only one parse runs).
            raw = outline_anchor.load_outline(
                self._job_id, state, no_parse=False)
        except Exception:  # noqa: BLE001
            _log.exception("outline background parse failed for %s", self._job_id)
            raw = None
        self._loader._finished.emit(self._job_id, self._generation, raw)


class OutlineLoader(QObject):
    """Background loader for the source-outline sidecar.

    ``ready(job_id)`` is emitted on the main thread when the outline for
    *job_id* becomes available.  A warm sidecar is returned synchronously from
    :meth:`request`; a cold sidecar triggers a background parse and ``ready``
    fires when it finishes.
    """

    #: Emitted on the main thread when a parse (or warm load) completes.
    ready = Signal(str)

    # Internal signal bridging the worker thread back to the main thread.
    _finished = Signal(str, int, object)

    #: Maximum in-memory cache entries.
    LIMIT = 64

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cache: dict[str, tuple[_Generation, dict]] = {}
        self._pending: dict[str, _Generation] = {}  # job_id -> generation
        self._generations: dict[str, _Generation] = {}  # job_id -> current gen
        self._lock = threading.Lock()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._finished.connect(self._on_finished)

    # ------------------------------------------------------------------
    def request(self, job_id: str, state: dict | None = None) -> dict | None:
        """Return the ready payload for *job_id*, or ``None`` if still loading.

        If ``None`` is returned a parse has been queued; :attr:`ready` fires
        when it completes.  Call ``request`` again from the ``ready`` slot to
        retrieve the result.

        Calling ``request`` while a parse is already pending for the same
        generation is a no-op (the existing job is returned or the queue entry
        is already there).
        """
        job_id = str(job_id or "")
        if not job_id:
            return None

        with self._lock:
            gen = self._generations.get(job_id, 0)
            cached = self._cache.get(job_id)
            if cached is not None and cached[0] == gen:
                return cached[1]
            if job_id in self._pending and self._pending[job_id] == gen:
                return None  # already queued

        # Try the fast path: warm sidecar (no parse needed).
        try:
            payload = adapter.task_outline(job_id, state, no_parse=True)
        except Exception:  # noqa: BLE001
            payload = None

        if payload and not payload.get("pending"):
            with self._lock:
                self._cache[job_id] = (gen, payload)
                while len(self._cache) > self.LIMIT:
                    self._cache.pop(next(iter(self._cache)))
            return payload

        # Cold: schedule a background parse.
        with self._lock:
            self._pending[job_id] = gen

        self._pool.start(_ParseJob(self, job_id, gen, state or {}), priority=10)
        return None

    def bump_generation(self, job_id: str) -> None:
        """Increment the generation for *job_id*, invalidating any pending result.

        Not called on every job switch: :meth:`_on_finished` guards stale
        results via the generation token, and :meth:`_on_outline_ready` in the
        window additionally checks ``_current_job_id``.  Call this when you
        want to force a fresh parse for a job while it is still the active
        one — for example, after replacing the source file in-place without
        going through the normal re-import path.  For the standard re-import
        and delete flows, :meth:`invalidate` is the right call (it also drops
        the cache entry).
        """
        job_id = str(job_id or "")
        if not job_id:
            return
        with self._lock:
            self._generations[job_id] = self._generations.get(job_id, 0) + 1
            self._cache.pop(job_id, None)

    def invalidate(self, job_id: str) -> None:
        """Drop the cached result for *job_id* so the next request re-reads."""
        job_id = str(job_id or "")
        with self._lock:
            self._cache.pop(job_id, None)
            self._generations[job_id] = self._generations.get(job_id, 0) + 1

    def is_pending(self, job_id: str) -> bool:
        """True while a background parse is queued or running for *job_id*."""
        job_id = str(job_id or "")
        with self._lock:
            gen = self._generations.get(job_id, 0)
            return self._pending.get(job_id) == gen

    def _on_finished(self, job_id: str, generation: int, raw: dict | None) -> None:
        """Main-thread callback: store the result and emit ``ready``.

        When the parse failed (``raw`` is ``None`` or still pending after the
        worker ran), we cache a ``{"failed": True}`` marker so that subsequent
        :meth:`request` calls return it immediately instead of re-queuing a new
        parse that would fail again, creating an infinite loop.
        """
        with self._lock:
            current_gen = self._generations.get(job_id, 0)
            self._pending.pop(job_id, None)
            if generation != current_gen:
                return  # stale result for a job the user already left
            if raw is not None and not raw.get("pending"):
                entry = raw
            else:
                # Parse failed or returned a still-pending payload; store a
                # failure sentinel so we never re-queue for this generation.
                entry = {"failed": True}
            self._cache[job_id] = (generation, entry)
            while len(self._cache) > self.LIMIT:
                self._cache.pop(next(iter(self._cache)))
        self.ready.emit(job_id)

    def wait(self, msecs: int = 5000) -> bool:
        """Tests / self-checks: wait until the background pool is idle."""
        return self._pool.waitForDone(msecs)


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_OUTLINE_LOADER: OutlineLoader | None = None


def outline_loader() -> OutlineLoader:
    """Return the process-level :class:`OutlineLoader` singleton."""
    global _OUTLINE_LOADER
    if _OUTLINE_LOADER is None:
        _OUTLINE_LOADER = OutlineLoader()
    return _OUTLINE_LOADER
