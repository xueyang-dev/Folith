"""Provider/model scoped bounded request controller.

The problem this replaces
-------------------------
``translate_batch`` reacted to a 429 with a worker-local ``time.sleep(15)`` and
then immediately re-sent.  With four concurrent workers that is a retry herd:
all four hit the limit, all four sleep the same fixed amount, and all four wake
up and fire at the same instant, which tends to earn another 429.

What this does
--------------
One controller per ``(provider, model)`` — shared by every worker in the process
— with three cooperating mechanisms:

* **shared request gate** — a minimum interval between request *starts*, so
  workers leave the gate spaced out instead of simultaneously;
* **shared cooldown with exponential backoff + jitter** — the first 429 pushes a
  deadline that every worker observes, so nobody re-sends while the provider is
  still refusing; jitter de-synchronises the wake-up;
* **cancellation awareness** — waiting is done in short slices with a cancel
  probe, so a cancel request is honoured within ~0.2 s instead of after a full
  backoff.

``source_cleanup._RequestGate`` is a thin subclass of this class rather than a
second implementation, so cleanup retry pacing and body-translation pacing can
never drift apart.
"""
from __future__ import annotations

import contextvars
import os
import random
import threading
import time
from typing import Any, Callable, Dict, Optional

DEFAULT_BASE_BACKOFF_S = 1.0
DEFAULT_MAX_BACKOFF_S = 60.0
DEFAULT_JITTER_RATIO = 0.25
DEFAULT_MAX_SLEEP_SLICE_S = 0.2


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return float(default)


def _raise_if_cancelled(cancel_check: Optional[Callable[[], bool]]) -> None:
    if cancel_check is not None and cancel_check():
        raise RuntimeError("任务已请求取消")


CANCEL_POLL_S = 0.25
# The thread a request is made for; set on the helper thread of call_cancellable.
_CALLER_THREAD: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "folith_request_caller_thread", default=None)


def request_owner_thread() -> int:
    """The thread a request belongs to, also when it runs on a cancellable helper."""
    return _CALLER_THREAD.get() or threading.get_ident()


def call_cancellable(fn: Callable[[], Any],
                     cancel_check: Optional[Callable[[], bool]],
                     *, poll: float = CANCEL_POLL_S) -> Any:
    """Run one blocking provider request, giving it up as soon as cancel is asked.

    A blocking HTTP call cannot be interrupted from outside, so it runs on a
    daemon helper thread while this thread waits in short slices.  On cancel
    the request is abandoned: its late reply (or error) is dropped, and the
    socket is released when the transport's own timeout ends it.  The service
    may still bill for that request.
    """
    if cancel_check is None:
        return fn()
    _raise_if_cancelled(cancel_check)
    done = threading.Event()
    box: Dict[str, Any] = {}
    # Request tracing and usage roles live in context variables; the helper
    # thread must see the caller's, or its HTTP attempts join no logical call.
    context = contextvars.copy_context()
    context.run(_CALLER_THREAD.set, request_owner_thread())

    def run() -> None:
        try:
            box["value"] = context.run(fn)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=run, name="folith-request", daemon=True).start()
    while not done.wait(poll):
        _raise_if_cancelled(cancel_check)
    if "error" in box:
        raise box["error"]
    return box["value"]


class ProviderRequestController:
    """Shared pacing + backoff gate for one provider/model pair."""

    def __init__(
        self,
        key: str,
        *,
        min_interval: Optional[float] = None,
        base_backoff: Optional[float] = None,
        max_backoff: Optional[float] = None,
        jitter_ratio: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Optional[Callable[[], float]] = None,
        max_sleep_slice: float = DEFAULT_MAX_SLEEP_SLICE_S,
    ) -> None:
        self.key = str(key)
        self._min_interval = max(0.0, float(
            min_interval if min_interval is not None
            else _env_float("FOLIOTHREAD_LLM_REQUEST_INTERVAL", 0.0)))
        self._base_backoff = max(0.0, float(
            base_backoff if base_backoff is not None
            else _env_float("FOLIOTHREAD_LLM_RATE_LIMIT_BASE_BACKOFF",
                            DEFAULT_BASE_BACKOFF_S)))
        self._max_backoff = max(self._base_backoff, float(
            max_backoff if max_backoff is not None
            else _env_float("FOLIOTHREAD_LLM_RATE_LIMIT_MAX_BACKOFF",
                            DEFAULT_MAX_BACKOFF_S)))
        self._jitter_ratio = max(0.0, float(
            jitter_ratio if jitter_ratio is not None
            else _env_float("FOLIOTHREAD_LLM_RATE_LIMIT_JITTER", DEFAULT_JITTER_RATIO)))
        self._clock = clock
        self._sleep = sleeper
        self._random = random_source or random.random
        self._max_sleep_slice = max(0.01, float(max_sleep_slice))
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._cooldown_until = 0.0
        self._consecutive = 0
        self._stats = {
            "acquired": 0,
            "gate_wait_s": 0.0,
            "rate_limited": 0,
            "backoff_s": 0.0,
            "cancelled_waits": 0,
        }

    # -- configuration ----------------------------------------------------
    @property
    def min_interval(self) -> float:
        return self._min_interval

    def set_min_interval(self, value: float) -> None:
        with self._lock:
            self._min_interval = max(0.0, float(value or 0.0))

    # -- gate -------------------------------------------------------------
    def acquire(self, cancel_check: Optional[Callable[[], bool]] = None) -> float:
        """Block until this worker may start a request. Returns seconds waited."""
        waited = 0.0
        while True:
            _raise_if_cancelled(cancel_check)
            with self._lock:
                now = self._clock()
                delay = max(self._next_allowed - now, self._cooldown_until - now)
                if delay <= 0.0:
                    self._next_allowed = now + self._min_interval
                    self._stats["acquired"] += 1
                    self._stats["gate_wait_s"] += waited
                    return waited
            waited += self._sleep_slice(delay, cancel_check)

    def _sleep_slice(self, delay: float,
                     cancel_check: Optional[Callable[[], bool]]) -> float:
        chunk = min(delay, self._max_sleep_slice)
        try:
            self._sleep(chunk)
        except BaseException:
            with self._lock:
                self._stats["cancelled_waits"] += 1
            raise
        if cancel_check is not None and cancel_check():
            with self._lock:
                self._stats["cancelled_waits"] += 1
            raise RuntimeError("任务已请求取消")
        return chunk

    # -- feedback ---------------------------------------------------------
    def note_rate_limited(self, retry_after: Optional[float] = None) -> float:
        """Record a 429 and push the shared cooldown. Returns the backoff used."""
        with self._lock:
            self._consecutive += 1
            backoff = min(self._max_backoff,
                          self._base_backoff * (2 ** (self._consecutive - 1)))
            if self._jitter_ratio:
                backoff *= 1.0 + (self._jitter_ratio * float(self._random()))
            if retry_after is not None:
                try:
                    backoff = max(backoff, float(retry_after))
                except (TypeError, ValueError):
                    pass
            backoff = max(0.0, backoff)
            now = self._clock()
            self._cooldown_until = max(self._cooldown_until, now + backoff)
            # Re-open the pacing gate only after the cooldown, so the workers
            # that were already waiting do not stampede the provider.
            self._next_allowed = max(self._next_allowed, now + backoff)
            self._stats["rate_limited"] += 1
            self._stats["backoff_s"] += backoff
            return backoff

    def note_success(self) -> None:
        with self._lock:
            self._consecutive = 0

    # -- introspection ----------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "key": self.key,
                "min_interval_s": round(self._min_interval, 6),
                "consecutive_rate_limits": self._consecutive,
                "cooldown_remaining_s": round(
                    max(0.0, self._cooldown_until - self._clock()), 6),
                "gate_wait_s": round(self._stats["gate_wait_s"], 6),
                "rate_limited": self._stats["rate_limited"],
                "backoff_s": round(self._stats["backoff_s"], 6),
                "acquired": self._stats["acquired"],
                "cancelled_waits": self._stats["cancelled_waits"],
            }


_CONTROLLERS: Dict[str, ProviderRequestController] = {}
_CONTROLLER_LOCK = threading.RLock()


def controller_key(provider: Any, model: Any = "") -> str:
    return f"{str(provider or '')}|{str(model or '')}"


def get_request_controller(provider: Any, model: Any = "",
                           **kwargs: Any) -> ProviderRequestController:
    """Process-wide controller for a provider/model pair."""
    key = controller_key(provider, model)
    with _CONTROLLER_LOCK:
        controller = _CONTROLLERS.get(key)
        if controller is None:
            controller = ProviderRequestController(key, **kwargs)
            _CONTROLLERS[key] = controller
        return controller


def request_controller_stats() -> Dict[str, Any]:
    with _CONTROLLER_LOCK:
        return {key: controller.snapshot()
                for key, controller in sorted(_CONTROLLERS.items())}


def reset_request_controllers() -> None:
    with _CONTROLLER_LOCK:
        _CONTROLLERS.clear()
