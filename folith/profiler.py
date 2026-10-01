"""Lightweight zero-overhead profiling utilities for Folith UI and IO.

Activated only when `FOLITH_PROFILE_UI=1` is set in the environment.
Provides span timers, execution counters, and benchmark reports.
"""
from __future__ import annotations

import collections
import contextlib
import os
import time
from typing import Any, Dict, Iterator, List, Optional

_ENABLED = os.environ.get("FOLITH_PROFILE_UI") == "1"

_COUNTERS: Dict[str, int] = collections.defaultdict(int)
_DURATIONS: Dict[str, float] = collections.defaultdict(float)
_SPANS: List[Dict[str, Any]] = []


def is_profiling_enabled() -> bool:
    return _ENABLED or os.environ.get("FOLITH_PROFILE_UI") == "1"


def count(name: str, delta: int = 1) -> None:
    if is_profiling_enabled():
        _COUNTERS[name] += delta


@contextlib.contextmanager
def span(name: str, **metadata: Any) -> Iterator[None]:
    if not is_profiling_enabled():
        yield
        return

    _COUNTERS[name] += 1
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _DURATIONS[name] += elapsed_ms
        if len(_SPANS) < 1000:
            _SPANS.append({
                "name": name,
                "duration_ms": elapsed_ms,
                "metadata": metadata,
            })


def get_profile_stats() -> Dict[str, Any]:
    stats = {}
    for name, c in _COUNTERS.items():
        total_ms = _DURATIONS.get(name, 0.0)
        avg_ms = (total_ms / c) if c > 0 else 0.0
        stats[name] = {
            "count": c,
            "total_ms": round(total_ms, 3),
            "avg_ms": round(avg_ms, 3),
        }
    return stats


def reset_profile_stats() -> None:
    _COUNTERS.clear()
    _DURATIONS.clear()
    _SPANS.clear()
