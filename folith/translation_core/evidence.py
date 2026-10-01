"""Deterministic review dependencies and stale-preserving invalidation."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping


def _stable_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def fingerprint(dependencies: Mapping[str, Any]) -> str:
    """Return one deterministic fingerprint for a non-empty dependency map."""
    if not isinstance(dependencies, Mapping) or not dependencies:
        raise ValueError("fingerprint dependencies must be a non-empty mapping")
    digest = hashlib.sha256(_stable_json(dict(dependencies)).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def review_input_fingerprint(
    *,
    source: Any,
    target: Any,
    glossary_hash: str,
    confirmed_knowledge: Any,
    context: Any,
    deterministic_checks: Any,
    evidence: Any,
) -> str:
    """Bind review state to every mutable input exposed to the reviewer."""
    return fingerprint({
        "source": source,
        "target": target,
        "glossary_hash": str(glossary_hash or ""),
        "confirmed_knowledge": confirmed_knowledge,
        "context": context,
        "deterministic_checks": deterministic_checks,
        "evidence": evidence,
    })


def freshness(record: Mapping[str, Any], current_fingerprint: str) -> str:
    """Classify a derived record without mutating or deleting it."""
    if not record.get("input_fingerprint"):
        return "missing"
    return "current" if record.get("input_fingerprint") == current_fingerprint else "stale"


def mark_stale(
    records: Iterable[Mapping[str, Any]],
    current_fingerprint: str,
    reason: str,
    *,
    stale_at: str | None = None,
) -> List[Dict[str, Any]]:
    """Keep records and mark only mismatched dependencies stale."""
    result = []
    for record in records:
        item = dict(record)
        if freshness(item, current_fingerprint) == "stale":
            if item.get("status") != "stale":
                item["status_before_stale"] = item.get("status")
            item["status"] = "stale"
            item["stale_reason"] = str(reason or "review inputs changed")
            if stale_at is not None:
                item["stale_at"] = stale_at
        result.append(item)
    return result
