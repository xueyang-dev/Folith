"""Small, thread-safe LLM usage ledger for translation runs.

Providers in the legacy core return plain text, so exact token usage is not
always available at this boundary.  The ledger records provider usage when a
future adapter supplies it and otherwise stores a clearly labelled character
based estimate (roughly four UTF-8 characters per token).  Estimates are for
comparing pipeline choices, never for billing.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, Optional


SCHEMA_VERSION = 1
_LOCK = threading.RLock()


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _estimate_tokens(chars: int) -> int:
    return int(math.ceil(max(0, chars) / 4.0)) if chars else 0


def empty_usage() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "calls": 0,
        "failures": 0,
        "latency_seconds": 0.0,
        "prompt_chars": 0,
        "completion_chars": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated_prompt_tokens": 0,
        "estimated_completion_tokens": 0,
        "estimated_total_tokens": 0,
        "exact_token_calls": 0,
        "by_role": {},
        "by_provider_model": {},
    }


def _usage_value(usage: Any, name: str) -> Optional[int]:
    if usage is None:
        return None
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _slot(ledger: Dict[str, Any], group: str, key: str) -> Dict[str, Any]:
    slots = ledger.setdefault(group, {})
    return slots.setdefault(key, {
        "calls": 0, "failures": 0, "latency_seconds": 0.0,
        "prompt_chars": 0, "completion_chars": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        "estimated_total_tokens": 0,
    })


def record_call(
    ledger: Dict[str, Any],
    *,
    role: str,
    provider: str,
    model: str,
    system_prompt: Any,
    user_prompt: Any,
    completion: Any = "",
    usage: Any = None,
    latency_seconds: float = 0.0,
    success: bool = True,
) -> None:
    """Merge one call into a shared ledger without exposing prompt contents."""
    if not isinstance(ledger, dict):
        return
    prompt_chars = len(str(system_prompt or "")) + len(str(user_prompt or ""))
    completion_chars = len(str(completion or ""))
    prompt_tokens = _usage_value(usage, "prompt_tokens")
    completion_tokens = _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens")
    exact = (prompt_tokens is not None and completion_tokens is not None
             and total_tokens is not None)
    prompt_tokens = (_nonnegative_int(prompt_tokens)
                     if prompt_tokens is not None else _estimate_tokens(prompt_chars))
    completion_tokens = (_nonnegative_int(completion_tokens)
                         if completion_tokens is not None
                         else _estimate_tokens(completion_chars))
    total_tokens = (_nonnegative_int(total_tokens)
                    if total_tokens is not None
                    else prompt_tokens + completion_tokens)
    role_key = str(role or "unknown")
    provider_model_key = f"{provider or 'unknown'}::{model or 'unknown'}"
    with _LOCK:
        ledger.setdefault("schema_version", SCHEMA_VERSION)
        ledger["calls"] = _nonnegative_int(ledger.get("calls")) + 1
        if not success:
            ledger["failures"] = _nonnegative_int(ledger.get("failures")) + 1
        latency = max(0.0, float(latency_seconds or 0.0))
        ledger["latency_seconds"] = round(float(ledger.get("latency_seconds") or 0.0) + latency, 6)
        for name, value in (("prompt_chars", prompt_chars), ("completion_chars", completion_chars),
                            ("prompt_tokens", prompt_tokens), ("completion_tokens", completion_tokens),
                            ("total_tokens", total_tokens)):
            ledger[name] = _nonnegative_int(ledger.get(name)) + _nonnegative_int(value)
        if exact:
            ledger["exact_token_calls"] = _nonnegative_int(ledger.get("exact_token_calls")) + 1
        else:
            ledger["estimated_prompt_tokens"] = _nonnegative_int(
                ledger.get("estimated_prompt_tokens")) + prompt_tokens
            ledger["estimated_completion_tokens"] = _nonnegative_int(
                ledger.get("estimated_completion_tokens")) + completion_tokens
            ledger["estimated_total_tokens"] = _nonnegative_int(
                ledger.get("estimated_total_tokens")) + total_tokens
        for group, key in (("by_role", role_key), ("by_provider_model", provider_model_key)):
            slot = _slot(ledger, group, key)
            slot["calls"] += 1
            slot["failures"] += 0 if success else 1
            slot["latency_seconds"] = round(slot["latency_seconds"] + latency, 6)
            slot["prompt_chars"] += prompt_chars
            slot["completion_chars"] += completion_chars
            slot["prompt_tokens"] += prompt_tokens
            slot["completion_tokens"] += completion_tokens
            slot["total_tokens"] += total_tokens
            slot["estimated_total_tokens"] += total_tokens if not exact else 0


def tracked_call(call_fn, ledger: Dict[str, Any], *, role: str):
    """Wrap a provider function while preserving its legacy call signature."""
    def invoke(provider, api_key, model, system_prompt, user_prompt, **kwargs):
        started = time.perf_counter()
        result = ""
        try:
            result = call_fn(provider, api_key, model, system_prompt, user_prompt, **kwargs)
            record_call(ledger, role=role, provider=provider, model=model,
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        completion=result, latency_seconds=time.perf_counter() - started,
                        success=True)
            return result
        except Exception:
            record_call(ledger, role=role, provider=provider, model=model,
                        system_prompt=system_prompt, user_prompt=user_prompt,
                        completion=result, latency_seconds=time.perf_counter() - started,
                        success=False)
            raise
    return invoke
