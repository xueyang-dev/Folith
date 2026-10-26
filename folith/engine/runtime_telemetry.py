"""Split out of core.py.

Moved verbatim out of ``core.py``; ``core`` re-exports every public and private
name defined here.  Anything still owned by ``core`` is read late-bound through
``_c`` so that tests (and callers) that patch ``core.X`` keep working.
"""

import threading
import time
from folith.engine._link import core as _c


def runtime_persistence_stats():
    """Counters proving how much runtime persistence actually happened."""
    with _c._RUNTIME_PERSIST_LOCK:
        return dict(_c._RUNTIME_PERSIST_STATS)


def reset_runtime_persistence_stats():
    with _c._RUNTIME_PERSIST_LOCK:
        for key in _c._RUNTIME_PERSIST_STATS:
            _c._RUNTIME_PERSIST_STATS[key] = 0


class _RuntimeTelemetryCoalescer:
    """Merge high-frequency, non-authoritative runtime telemetry updates.

    Only LLM in-flight status flows through here.  Everything that a crash
    recovery or a cancellation decision depends on is written synchronously by
    the caller instead of being queued behind this window.
    """

    def __init__(self, job_id, *, flush_interval=None, writer=None):
        self.job_id = job_id
        self.flush_interval = max(
            0.0, float(flush_interval if flush_interval is not None
                       else _c.RUNTIME_TELEMETRY_FLUSH_SECONDS))
        self._writer = writer
        self._lock = threading.RLock()
        self._pending = {}
        self._active = 0
        # 在途请求的用途标签（#167）：request_started 压入、request_finished
        # 按用途摘除。并发请求用途可能不同，展示取"最近一次"的用途。
        self._active_purposes = []
        self._last_flush = None
        self._timer = None
        self._closed = False
        self._stats = {"submits": 0, "flushes": 0, "coalesced": 0,
                       "immediate": 0, "events": 0}

    # -- public API -------------------------------------------------------
    def request_started(self, purpose=None):
        self.submit(active_delta=1, status="waiting_external",
                    phase="waiting_llm", event_name="llm_request_started",
                    purpose=purpose)

    def request_finished(self, purpose=None):
        self.submit(active_delta=-1, status="running", phase="running",
                    event_name="llm_response_received", purpose=purpose)

    def request_failed(self, error=None, *, purpose=None):
        self.submit(force=True, active_delta=-1, status="running", phase="running",
                    event=str(error)[:240] if error else "模型请求失败",
                    event_name="llm_request_failed", purpose=purpose)

    def submit(self, *, active_delta=0, force=False, purpose=None, **changes):
        """Record one telemetry update; flush on the leading or trailing edge."""
        with self._lock:
            if self._closed:
                return
            self._stats["submits"] += 1
            if active_delta:
                if active_delta > 0 and self._active == 0:
                    changes["model_wait_started_at"] = _c._utc_now_iso()
                self._active = max(0, self._active + int(active_delta))
                if active_delta > 0:
                    self._active_purposes.append(purpose)
                elif purpose in self._active_purposes:
                    self._active_purposes.remove(purpose)
                elif self._active_purposes:
                    self._active_purposes.pop(0)
                # 事件属于本次请求；等待状态另从仍在途的请求推导。
                # None 也要覆盖旧值，避免合并窗口沿用上次事件的用途。
                self._pending["purpose"] = purpose
            for key, value in changes.items():
                if value is not None:
                    self._pending[key] = value
            now = time.monotonic()
            leading_edge = (self._last_flush is None
                            or (now - self._last_flush) >= self.flush_interval)
            if force or leading_edge or self._active == 0:
                self._stats["immediate"] += 1
                self._flush_locked(now)
                return
            self._stats["coalesced"] += 1
            self._schedule_locked(now)

    def flush(self):
        with self._lock:
            if self._pending or self._active:
                self._flush_locked(time.monotonic())

    def close(self):
        with self._lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._pending:
                self._flush_locked(time.monotonic())

    def stats(self):
        with self._lock:
            return {**self._stats, "active": self._active}

    # -- internals --------------------------------------------------------
    def _schedule_locked(self, now):
        if self._timer is not None:
            return
        delay = max(0.01, self.flush_interval - (now - self._last_flush))
        timer = threading.Timer(delay, self._flush_from_timer)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _flush_from_timer(self):
        with self._lock:
            self._timer = None
            # The last response may already have flushed this window.  An
            # empty timer must not write "running" over the worker's completion.
            if self._closed or not (self._pending or self._active):
                return
            self._flush_locked(time.monotonic())

    def _flush_locked(self, now):
        changes = dict(self._pending)
        self._pending.clear()
        self._last_flush = now
        active = self._active
        event_name = changes.pop("event_name", None)
        event = changes.pop("event", None)
        active_purpose = self._active_purposes[-1] if self._active_purposes else None
        purpose = changes.pop("purpose", None)
        if event_name is None:
            purpose = active_purpose
        if purpose:
            changes["event_metadata"] = {"purpose": str(purpose)}
        if active > 0:
            changes["status"] = "waiting_external"
            changes["phase"] = "waiting_llm"
            changes["phase_label"] = (
                f"等待模型响应：{active_purpose}（{active} 个请求进行中）" if active_purpose
                else f"等待模型响应（{active} 个请求进行中）")
            if event is None:
                if event_name == "llm_response_received":
                    event = f"已收到模型响应，仍有 {active} 个请求进行中"
                    if purpose:
                        event = f"已收到模型响应（{purpose}），仍有 {active} 个请求进行中"
                else:
                    event = "已向模型发送请求" + (f"（{purpose}）" if purpose else "")
                    event_name = "llm_request_started"
        else:
            changes.setdefault("status", "running")
            changes.setdefault("phase", "running")
            changes["phase_label"] = "处理模型结果"
            if event is None:
                event = "已收到模型响应" + (f"（{purpose}）" if purpose else "")
                event_name = "llm_response_received"
        # 取消是业务真值：一旦用户请求取消，合并窗口里的"等待/处理中"就不该再
        # 覆盖 cancelling。宁可丢掉这几条遥测，也不能让 UI 显示成还在正常跑。
        try:
            if (_c.load_runtime_state(self.job_id) or {}).get("cancel_requested"):
                return
        except Exception:  # noqa: BLE001 - 读不到就按未取消处理
            pass
        writer = self._writer or _c.update_runtime_state
        try:
            writer(self.job_id, progress=True, event=event, event_name=event_name, **changes)
            self._stats["flushes"] += 1
            self._stats["events"] += 1
        except Exception:  # noqa: BLE001 - telemetry must never break a request
            pass


def _runtime_telemetry_coalescer(job_id):
    if not job_id:
        return None
    with _c._TELEMETRY_LOCK:
        coalescer = _c._TELEMETRY_COALESCERS.get(job_id)
        if coalescer is None:
            coalescer = _RuntimeTelemetryCoalescer(job_id)
            _c._TELEMETRY_COALESCERS[job_id] = coalescer
            _c._TELEMETRY_COALESCER_STATS["created"] += 1
        return coalescer


def _runtime_telemetry_request_failed(job_id, error=None, *, purpose=None):
    coalescer = _runtime_telemetry_coalescer(job_id)
    if coalescer is not None:
        coalescer.request_failed(error=error, purpose=purpose)


def flush_runtime_telemetry(job_id=None):
    """Force pending telemetry to disk (call at stage boundaries / shutdown)."""
    with _c._TELEMETRY_LOCK:
        if job_id:
            coalescer = _c._TELEMETRY_COALESCERS.get(job_id)
            targets = [coalescer] if coalescer else []
        else:
            targets = list(_c._TELEMETRY_COALESCERS.values())
    for coalescer in targets:
        coalescer.flush()


def reset_runtime_telemetry(job_id=None):
    """Drop coalescer state; used when a job reaches a terminal boundary."""
    with _c._TELEMETRY_LOCK:
        if job_id is None:
            targets = list(_c._TELEMETRY_COALESCERS.values())
            _c._TELEMETRY_COALESCERS.clear()
        else:
            coalescer = _c._TELEMETRY_COALESCERS.pop(job_id, None)
            targets = [coalescer] if coalescer else []
    for coalescer in targets:
        coalescer.close()


def runtime_telemetry_stats():
    with _c._TELEMETRY_LOCK:
        coalescers = {jid: coalescer.stats()
                      for jid, coalescer in _c._TELEMETRY_COALESCERS.items()}
        created = _c._TELEMETRY_COALESCER_STATS["created"]
    totals = {"submits": 0, "flushes": 0, "coalesced": 0, "immediate": 0, "events": 0}
    for stats in coalescers.values():
        for key in totals:
            totals[key] += int(stats.get(key) or 0)
    return {"coalescers": len(coalescers), "created": created,
            "totals": totals, "per_job": coalescers}
