"""On-demand, read-only AI explanation for one review item (issue #525).

This module deliberately owns no Qt state and no job state.  The caller passes
a bounded packet plus an injected LLM callable, so a test can supply a fake
provider and assert the exact number of requests.  Nothing here writes to a
job: no HumanDecision, no ``resolved``, no triage record.  Results are cached
in memory by the packet fingerprint and dropped when the text changes.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

SYSTEM_PROMPT = (
    "You are a read-only terminology reviewer. Judge whether the reported "
    "problem is real, explain why, suggest a change, and cite the evidence you "
    "used. Never decide anything: do not resolve, accept or edit any finding. "
    "In particular, a longer compound such as 'Crown colophon' may be a "
    "different term from the publisher 'Crown' even when it shares a prefix."
)


def fingerprint(packet: dict[str, Any]) -> str:
    """Stable hash of the *input* only — no model output, no clock."""
    payload = json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_packet(item: dict[str, Any], *, state: dict[str, Any] | None = None,
                 surrounding: list[dict[str, Any]] | None = None,
                 glossary: list[dict[str, Any]] | None = None,
                 tm: list[dict[str, Any]] | None = None,
                 entities: list[dict[str, Any]] | None = None,
                 prior_decisions: list[dict[str, Any]] | None = None,
                 char_limit: int = 12_000) -> dict[str, Any]:
    """Build a bounded explanation packet from already-available context.

    Caps every list so one pathological item cannot blow up the prompt; callers
    pass the same data the review packet already derives, so no new unbounded
    traversal happens here.
    """

    def cap(rows: Any, n: int = 12) -> list[Any]:
        return list(rows or [])[:n]

    packet = {
        "item": {k: item.get(k) for k in
                 ("source", "target", "reason", "type", "segment_index")
                 if k in item},
        "surrounding": cap(surrounding),
        "glossary": cap(glossary),
        "translation_memory": cap(tm, 6),
        "entity_registry": cap(entities, 12),
        "prior_decisions": cap(prior_decisions, 6),
        "job_language": (state or {}).get("target_lang", ""),
    }
    # Deterministic truncation by serialized size: keep the head of each list.
    text = json.dumps(packet, ensure_ascii=False, default=str)
    if len(text) > char_limit:
        packet["surrounding"] = cap(packet["surrounding"], 4)
        packet["glossary"] = cap(packet["glossary"], 4)
        packet["translation_memory"] = cap(packet["translation_memory"], 2)
    return packet


class ExplanationCache:
    """In-memory cache keyed by the input fingerprint (never persisted)."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def get(self, packet: dict[str, Any]) -> dict[str, Any] | None:
        return self._entries.get(fingerprint(packet))

    def put(self, packet: dict[str, Any], result: dict[str, Any]) -> None:
        self._entries[fingerprint(packet)] = dict(result)

    def clear(self) -> None:
        self._entries.clear()


def explain(packet: dict[str, Any], *, call: Callable[..., str],
            cache: ExplanationCache | None = None,
            provider: str = "", api_key: str = "", model: str = "") -> dict[str, Any]:
    """Run exactly one model call for a new fingerprint; return a result dict.

    ``call`` has the same signature as ``core.call_llm``.  Any exception is
    returned as an error result — the UI must show failure, never a decision.
    """
    cache = cache or ExplanationCache()
    cached = cache.get(packet)
    if cached is not None:
        return {**cached, "cached": True}
    user_prompt = json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str)
    try:
        text = call(provider, api_key, model, SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001 - surfaced as an error state, never swallowed
        return {"ok": False, "error": str(exc), "cached": False}
    result = {"ok": True, "text": str(text or ""), "cached": False}
    cache.put(packet, result)
    return result
