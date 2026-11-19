"""Notice, once per run, that most of the model's output is reasoning.

Whether a model thinks is only partly knowable before a run: a connection may
not offer the switch (a Local Gateway only takes a reasoning level), a custom
relay may ignore it, an unrecognised model may think by default.  The usage the
provider returns settles it.  On 2026-11-19 a whole novel ran with 89% of its
output tokens spent on reasoning while the user believed thinking was off; the
only place that showed was a token count in the performance report afterwards.

:func:`observe` is the pure counting half: fed every successful call's usage,
it answers exactly once per ``(job, run)`` when the share crosses the threshold.
:func:`notice_from_record` is the hook the run trace calls for every recorded
model call; it turns that answer into one user-visible runtime event.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

#: Calls that must have reported usage before the share means anything.
MIN_CALLS = 20
#: Share of output tokens spent on reasoning that triggers the notice.
SHARE = 0.5
#: Fixed so the activity view can translate it; the numbers go in the detail.
MESSAGE = "模型输出大多是思考过程，思考未能关闭，翻译会明显变慢"

_LOCK = threading.Lock()
# (job_id, run_id) -> [calls, completion_tokens, reasoning_tokens, notified]
_RUNS: Dict[tuple, list] = {}


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def observe(job_id: Any, run_id: Any, completion_tokens: Any,
            reasoning_tokens: Any) -> Optional[Dict[str, Any]]:
    """Count one call; return ``{"calls", "share"}`` the first time the run is reasoning-heavy.

    Only calls that report both totals count: an endpoint that returns no
    reasoning figure says nothing either way, so it can neither raise nor dilute
    the share.  ``completion_tokens`` is the OpenAI-compatible total, which
    already includes the reasoning tokens.
    """
    completion = _as_int(completion_tokens)
    reasoning = _as_int(reasoning_tokens)
    if not job_id or completion is None or reasoning is None or completion <= 0:
        return None
    key = (str(job_id), str(run_id or ""))
    with _LOCK:
        entry = _RUNS.setdefault(key, [0, 0, 0, False])
        if entry[3]:
            return None
        entry[0] += 1
        entry[1] += completion
        entry[2] += max(0, min(reasoning, completion))
        if entry[0] < MIN_CALLS or entry[2] < SHARE * entry[1]:
            return None
        entry[3] = True
        return {"calls": entry[0], "share": entry[2] / entry[1]}


def detail(result: Dict[str, Any]) -> str:
    """The numbers behind the notice, for the activity row's detail line."""
    return (f"已完成的 {int(result['calls'])} 次模型调用中，"
            f"{float(result['share']) * 100:.0f}% 的输出 token 是思考过程。"
            "可在连接设置里换用能「关闭思考」的连接，或调低推理强度。")


def notice_from_record(job_id: Any, run_id: Any, record: Dict[str, Any]) -> None:
    """Count one recorded model call and post the notice the first time it is due.

    Only successful calls that carry the unified ``reasoning_level`` trace
    field count: those report reasoning *inside* ``completion_tokens``.  Gemini
    reports its thoughts beside the output instead, so the share would not mean
    the same thing.  Never raises: a lost notice must not fail a request whose
    translation already arrived (not even on a full disk).
    """
    try:
        if record.get("status") != "success":
            return
        from folith.engine import reasoning_catalog as _catalog
        if not _catalog.is_reasoning_level_record(record):
            return
        result = observe(job_id, run_id, record.get("completion_tokens"),
                         record.get("reasoning_tokens"))
        if result is None:
            return
        from folith.engine._link import core as _c
        _c.update_runtime_state(
            job_id, on_timeout="skip", event=MESSAGE,
            event_name="reasoning_heavy", event_visibility="user",
            event_category="progress",
            event_metadata={"detail": detail(result),
                            "reasoning_share": round(result["share"], 3),
                            "calls": result["calls"]})
    except Exception:  # noqa: BLE001 - a notice must never break a request
        pass


def reset() -> None:
    """Forget every run (tests)."""
    with _LOCK:
        _RUNS.clear()
