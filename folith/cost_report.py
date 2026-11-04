"""Per-task cost report that reads only the performance trace.

The trace (``.performance/runs/*/requests.jsonl``) is the single place every
model request is written: text-path calls, and now vision page reads as
``model_call`` events with ``role="vision"``.  Each attempt is its own event, so
failed and retried-away attempts stay counted instead of being overwritten.

This report never invents money.  The repository ships no provider price table,
so it reports the numbers the trace actually holds — calls and provider-reported
tokens — and says explicitly that the amount was not computed.  A caller that
does have a price table can pass it in; only then are peak/off-peak windows
filled in, and only for models the table prices.

It is a diagnostic, not a bill: a provider that reports no usage leaves those
token fields unknown (``None``), which is not the same as zero.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

#: Token fields the trace records per model call.  ``None`` means unreported.
TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens",
                "total_tokens", "reasoning_tokens")

#: Display / aggregation order for roles; unknown roles go last.
ROLE_ORDER = ("translation", "vision", "review", "auxiliary")


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a set of model_call events; unknown tokens never become zero."""
    tokens: Dict[str, Dict[str, Any]] = {}
    for field in TOKEN_FIELDS:
        known = [row[field] for row in rows if _as_int(row.get(field)) is not None]
        tokens[field] = {
            "total": sum(int(value) for value in known) if known else None,
            "calls_reported": len(known),
            "calls_total": len(rows),
            "complete": bool(rows) and len(known) == len(rows),
        }
    return {
        "calls": len(rows),
        "successes": sum(1 for row in rows if row.get("status") == "success"),
        "failures": sum(1 for row in rows if row.get("status") != "success"),
        "duration_s": round(sum(float(row.get("duration_s") or 0.0) for row in rows), 3),
        "tokens": tokens,
    }


def _role_order(role: str) -> tuple:
    try:
        return (0, ROLE_ORDER.index(role))
    except ValueError:
        return (1, role)


def read_model_calls(requests_paths: Iterable[Path]) -> List[Dict[str, Any]]:
    """Read ``model_call`` events from one or more ``requests.jsonl`` files.

    A line that is being appended to while we read is skipped, never fatal: the
    report is a diagnostic and must not raise into the task page.
    """
    calls: List[Dict[str, Any]] = []
    for path in requests_paths:
        try:
            with open(path, encoding="utf-8") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("event") == "model_call":
                        calls.append(event)
        except OSError:
            continue
    return calls


def build_cost_report(calls: List[Dict[str, Any]], *, run_count: int = 0,
                      price_table: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Turn the trace's model_call events into a per-task cost report."""
    calls = [call for call in calls if isinstance(call, dict)]
    totals = _aggregate(calls)

    by_role: Dict[str, List[Dict[str, Any]]] = {}
    by_model: Dict[str, List[Dict[str, Any]]] = {}
    for call in calls:
        by_role.setdefault(str(call.get("role") or "unknown"), []).append(call)
        provider = str(call.get("provider") or "unknown")
        model = str(call.get("model") or "unknown")
        by_model.setdefault(f"{provider}::{model}", []).append(call)

    roles = []
    for role, rows in by_role.items():
        entry = {"role": role, **_aggregate(rows)}
        roles.append(entry)
    roles.sort(key=lambda item: _role_order(str(item["role"])))

    models = []
    for key, rows in by_model.items():
        provider, _, model = key.partition("::")
        entry = {"label": " · ".join(part for part in (provider, model)
                                     if part and part != "unknown") or key,
                 "provider": provider, "model": model, **_aggregate(rows)}
        models.append(entry)
    models.sort(key=lambda item: -(item["tokens"]["total_tokens"]["total"] or 0))

    report: Dict[str, Any] = {
        "available": bool(calls),
        "runs": int(run_count or 0),
        "calls": totals["calls"],
        "successes": totals["successes"],
        "failures": totals["failures"],
        "duration_s": totals["duration_s"],
        "tokens": totals["tokens"],
        "by_role": roles,
        "by_model": models,
        # This repository has no price table, so no amount is ever fabricated.
        "pricing": {"available": False, "reason": "no_price_table", "currency": None},
    }
    if price_table:
        report["pricing"] = _price_report(calls, price_table)
    return report


def _price_report(calls: List[Dict[str, Any]], price_table: Dict[str, Any]) -> Dict[str, Any]:
    """Optional: price calls with an explicit table (per 1M tokens), peak vs off-peak.

    Only used when a caller supplies a table; the built-in report stays token-only.
    Unknown models are counted, not priced at zero.
    """
    windows = {"peak": 0.0, "off_peak": 0.0}
    priced_calls = 0
    unknown_calls = 0
    unknown_models: List[str] = []
    for call in calls:
        entry = ((price_table.get(str(call.get("provider") or "")) or {})
                 .get(str(call.get("model") or "")))
        if not isinstance(entry, dict):
            unknown_calls += 1
            label = f"{call.get('provider') or 'unknown'}::{call.get('model') or 'unknown'}"
            if label not in unknown_models:
                unknown_models.append(label)
            continue
        prompt = _as_int(call.get("prompt_tokens"))
        completion = _as_int(call.get("completion_tokens"))
        cached = _as_int(call.get("cached_tokens")) or 0
        if prompt is None or completion is None:
            unknown_calls += 1
            continue
        billable_prompt = max(0, prompt - cached)
        for window, prices in entry.items():
            if window not in windows or not isinstance(prices, dict):
                continue
            flat = (billable_prompt * float(prices.get("input") or 0.0)
                    + cached * float(prices.get("cached_input") or 0.0)
                    + completion * float(prices.get("output") or 0.0))
            windows[window] += flat / 1_000_000.0
        priced_calls += 1
    return {
        "available": priced_calls > 0,
        "reason": "priced_from_supplied_table",
        "currency": "USD",
        "windows": {key: round(value, 6) for key, value in windows.items()},
        "priced_calls": priced_calls,
        "unknown_calls": unknown_calls,
        "unknown_models": unknown_models,
    }


#: Small cache keyed by the exact files (path, mtime, size) seen last time, so the
#: task page does not re-parse a multi-megabyte trace on every refresh.
_CACHE: Dict[str, Any] = {"key": None, "report": None}


def _request_files(run_root: Path) -> List[Path]:
    try:
        return sorted(Path(run_root).glob("*/requests.jsonl"))
    except OSError:
        return []


def _signature(files: List[Path]) -> tuple:
    parts = []
    for path in files:
        try:
            stat = path.stat()
            parts.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(parts)


def task_cost_report(run_root, *, price_table: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build (and briefly cache) the cost report for one task's trace directory.

    ``run_root`` is typically ``job_dir / ".performance" / "runs"``.  Every run
    directory is summed: each model_call in the trace is a request that was
    actually sent, so the total is what the task really spent, retries included.
    """
    files = _request_files(Path(run_root))
    signature = _signature(files)
    if _CACHE["key"] == signature and _CACHE["report"] is not None:
        cached = _CACHE["report"]
        if price_table is None:
            return cached
    calls = read_model_calls(files)
    report = build_cost_report(calls, run_count=len(files), price_table=price_table)
    if price_table is None:
        _CACHE["key"], _CACHE["report"] = signature, report
    return report
