"""Knowledge-feedback observation reuse: identical extraction, paid once.

Knowledge feedback is *stateful* in a way translation is not: the model call
produces a list of observations, and a separate deterministic merge folds those
observations into the candidate queue, the entity registry and the findings.  So
reuse here cannot mean "skip the work" — it means:

    reuse the remote computation, replay the deterministic state effect.

This module owns only the first half.  It stores the **normalised observation
list** produced by one extraction request; on a hit the caller feeds that list
through exactly the same merge path a fresh response would have taken
(``folith.knowledge.observe_batch``).  Nothing about ordering, deduplication,
precedence, provenance or findings changes.

Identity
--------
The key is a hash of the *fully rendered request* — the bytes the model would
have been shown — plus the generation settings that can change its output:
provider, model, temperature and response format.  Rather than maintain a
hand-written list of "inputs that matter" (a list that rots as prompts grow),
hashing the rendered request makes every meaningful change a miss by
construction.

That is also why no project state participates in the key: the feedback prompt
contains no glossary, no entity registry, no candidate queue, no document
context and no review findings.  It is built from nothing but
``(segment_id, source, target)`` triples plus a constant system prompt.  Project
state affects only the local merge, which always re-runs.

This mirrors ``folith/translation_cache.py`` deliberately — same canonical
JSON, same bounded store, same process-lock/merge/temp-file/atomic-replace
write — but it is a separate cache with its own version, so the two can be
invalidated independently.

Losing this file costs API calls, never correctness.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import job_lock as _job_lock

CACHE_VERSION = 1
#: One entry is one batch's observation list.  4096 entries covers a very long
#: document (or several) without letting the file grow without bound.
DEFAULT_MAX_ENTRIES = 4096
#: Refuse to persist a single absurd observation list rather than let one entry
#: dominate the file.  A real batch produced ~2 KB in the reference workload.
MAX_ENTRY_CHARS = 40_000
#: Total retained characters.  Entry count alone is not a bound: a document
#: configured with very large batches would otherwise grow past any limit.
DEFAULT_MAX_TOTAL_CHARS = 2_000_000
COMPACT_JSON = {"ensure_ascii": False, "separators": (",", ":")}

#: A cached observation must look like this or the entry is treated as a miss.
#: Corruption must never reach the merge path half-formed.
_OBSERVATION_FIELDS = ("source_expression", "observed_target", "kind", "segment_id")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def feedback_fingerprint(*, provider: str, model: str, system_prompt: str,
                         user_prompt: str, temperature: Any,
                         response_format: Optional[Dict[str, Any]] = None) -> str:
    """Stable identity of one effective knowledge-extraction request.

    Volatile values (timestamps, request ids, batch indices that are already
    part of the prompt, UI state) are absent by construction: only the bytes the
    model actually sees, plus generation settings, participate.
    """
    material = {
        "v": CACHE_VERSION,
        "kind": "knowledge_feedback",
        "provider": str(provider or ""),
        "model": str(model or ""),
        "temperature": None if temperature is None else round(float(temperature), 6),
        "response_format": response_format or None,
        "system": system_prompt or "",
        "user": user_prompt or "",
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _normalise_observations(value: Any) -> Optional[List[Dict[str, Any]]]:
    """Accept a provider-shaped observation list, or reject it as unusable."""
    if not isinstance(value, list):
        return None
    cleaned: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return None
        if not all(field in item for field in _OBSERVATION_FIELDS):
            return None
        segment_id = item.get("segment_id")
        if isinstance(segment_id, bool) or not isinstance(segment_id, int):
            return None
        cleaned.append({
            "source_expression": str(item.get("source_expression") or ""),
            "observed_target": str(item.get("observed_target") or ""),
            "kind": str(item.get("kind") or "term"),
            "segment_id": int(segment_id),
        })
    return cleaned


class KnowledgeObservationCache:
    """Bounded, thread-safe map of request fingerprint -> normalised observations."""

    def __init__(self, entries: Optional[Dict[str, Any]] = None, *,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS):
        self.entries: Dict[str, Dict[str, Any]] = {}
        for key, value in (entries or {}).items():
            entry = self._coerce(value)
            if entry is not None:
                self.entries[str(key)] = entry
        self.baseline = set(self.entries)
        self.max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
        self.max_total_chars = max(1, int(max_total_chars or DEFAULT_MAX_TOTAL_CHARS))
        self._lock = threading.RLock()
        self._chars = sum(self._entry_chars(e) for e in self.entries.values())
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0
        self.rejected = 0
        self.invalid_entries = 0

    # -- entry shape ----------------------------------------------------
    @staticmethod
    def _coerce(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        observations = _normalise_observations(value.get("observations"))
        if observations is None:
            return None
        return {
            "observations": observations,
            "provider": str(value.get("provider") or ""),
            "model": str(value.get("model") or ""),
        }

    @staticmethod
    def _entry_chars(entry: Dict[str, Any]) -> int:
        return len(json.dumps(entry, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _observation_chars(observations: Sequence[Dict[str, Any]]) -> int:
        return sum(len(str(obs.get("source_expression")) or "")
                   + len(str(obs.get("observed_target")) or "")
                   for obs in observations)

    # -- identity -------------------------------------------------------
    @staticmethod
    def key_for(*, provider: str, model: str, system_prompt: str,
                user_prompt: str, temperature: Any = 0.1,
                response_format: Optional[Dict[str, Any]] = None) -> str:
        return feedback_fingerprint(
            provider=provider, model=model, system_prompt=system_prompt,
            user_prompt=user_prompt, temperature=temperature,
            response_format=response_format)

    # -- lookup ---------------------------------------------------------
    def get(self, key: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not key:
            return None
        with self._lock:
            entry = self.entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            observations = _normalise_observations(entry.get("observations"))
            if observations is None:
                # A corrupt entry degrades to a miss; the provider call happens
                # normally and never sees half-valid knowledge.
                self.entries.pop(key, None)
                self.invalid_entries += 1
                self.misses += 1
                return None
            self.hits += 1
            return [dict(obs) for obs in observations]

    def put(self, key: Optional[str], observations: Sequence[Dict[str, Any]],
            *, provider: str = "", model: str = "") -> bool:
        cleaned = _normalise_observations(list(observations or []))
        if not key or cleaned is None:
            return False
        entry = {"observations": cleaned, "provider": str(provider or ""),
                 "model": str(model or "")}
        if self._entry_chars(entry) > MAX_ENTRY_CHARS:
            with self._lock:
                self.rejected += 1
            return False
        with self._lock:
            previous = self.entries.get(key)
            if previous is not None:
                self._chars -= self._entry_chars(previous)
            self.entries[key] = entry
            self._chars += self._entry_chars(entry)
            self.stores += 1
            self._evict_locked()
        return True

    def _evict_locked(self) -> None:
        # dict preserves insertion order, so the head is the oldest write.
        while self.entries and (len(self.entries) > self.max_entries
                                or self._chars > self.max_total_chars):
            key = next(iter(self.entries))
            self._chars -= self._entry_chars(self.entries.pop(key))
            self.evictions += 1

    # -- persistence ----------------------------------------------------
    def dirty(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self.entries.items()
                    if key not in self.baseline}

    def merge_from(self, other: "KnowledgeObservationCache") -> None:
        """Fold entries written by another process/task into this one."""
        with self._lock:
            for key, value in other.entries.items():
                if key not in self.entries:
                    self._set_locked(key, dict(value))
            self.baseline.update(other.baseline)
            self._evict_locked()

    def _set_locked(self, key: str, entry: Dict[str, Any]) -> None:
        previous = self.entries.get(key)
        if previous is not None:
            self._chars -= self._entry_chars(previous)
        self.entries[key] = entry
        self._chars += self._entry_chars(entry)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            avoided_in = 0
            avoided_out = 0
            for entry in self.entries.values():
                observations = entry.get("observations") or []
                # The rendered request is not stored (it would be far larger
                # than the answer); report the answer-side saving, which is what
                # the provider bills for completion tokens, plus the observation
                # payload size as a conservative estimate of the prompt portion
                # this entry itself is responsible for.
                avoided_out += _estimate_tokens(len(json.dumps(
                    observations, ensure_ascii=False)))
                avoided_in += _estimate_tokens(
                    self._observation_chars(observations))
            return {
                "entries": len(self.entries),
                "retained_chars": self._chars,
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "evictions": self.evictions,
                "rejected": self.rejected,
                "invalid_entries": self.invalid_entries,
                "max_entries": self.max_entries,
                "max_total_chars": self.max_total_chars,
                "retained_observation_tokens": avoided_out,
            }


def _estimate_tokens(chars: int) -> int:
    """Same estimator as ``folith.usage`` so numbers line up with the ledger."""
    return max(0, (int(chars) + 3) // 4)


def from_payload(payload: Any, *,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
                 ) -> KnowledgeObservationCache:
    if not isinstance(payload, dict):
        return KnowledgeObservationCache(max_entries=max_entries,
                                         max_total_chars=max_total_chars)
    if int(payload.get("version") or 0) != CACHE_VERSION:
        # A version bump invalidates the whole file: cheaper and safer than
        # migrating an identity scheme that has just been declared stale.
        return KnowledgeObservationCache(max_entries=max_entries,
                                         max_total_chars=max_total_chars)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return KnowledgeObservationCache(max_entries=max_entries,
                                         max_total_chars=max_total_chars)
    return KnowledgeObservationCache(entries, max_entries=max_entries,
                                     max_total_chars=max_total_chars)


def to_payload(cache: KnowledgeObservationCache) -> Dict[str, Any]:
    with cache._lock:  # noqa: SLF001 - serialisation owns the same lock
        return {"version": CACHE_VERSION,
                "entries": {key: dict(value)
                            for key, value in cache.entries.items()}}


def load(path: Optional[Path], *,
         max_entries: int = DEFAULT_MAX_ENTRIES,
         max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
         ) -> KnowledgeObservationCache:
    """Read the cache file; a corrupt or unreadable file degrades to empty."""
    if path is None:
        return KnowledgeObservationCache(max_entries=max_entries,
                                         max_total_chars=max_total_chars)
    try:
        if not Path(path).is_file():
            return KnowledgeObservationCache(max_entries=max_entries,
                                             max_total_chars=max_total_chars)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return KnowledgeObservationCache(max_entries=max_entries,
                                         max_total_chars=max_total_chars)
    return from_payload(payload, max_entries=max_entries,
                        max_total_chars=max_total_chars)


def save(cache: KnowledgeObservationCache, path: Optional[Path]) -> bool:
    """Atomically merge this cache's delta into the on-disk file.

    Same shape as ``save_tm``/``translation_cache.save``: process lock, re-read
    under the lock, merge, encode once, write a temp file, replace.  A
    concurrent writer's entries are preserved instead of being clobbered by a
    stale read.
    """
    if path is None:
        return False
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    destination = path.with_name("knowledge_cache.write.lock")
    try:
        with _job_lock.exclusive(destination):
            merged = load(path, max_entries=cache.max_entries,
                          max_total_chars=cache.max_total_chars)
            with cache._lock:  # noqa: SLF001
                for key, value in cache.entries.items():
                    merged._set_locked(key, dict(value))  # noqa: SLF001
            merged._evict_locked()  # noqa: SLF001
            encoded = json.dumps(to_payload(merged), **COMPACT_JSON).encode("utf-8")
            tmp = path.with_suffix(".json.tmp")
            try:
                tmp.write_bytes(encoded)
                tmp.replace(path)
            finally:
                tmp.unlink(missing_ok=True)
    except OSError:
        return False
    with cache._lock:  # noqa: SLF001
        cache.baseline = set(cache.entries)
    return True


__all__ = [
    "CACHE_VERSION", "DEFAULT_MAX_ENTRIES", "DEFAULT_MAX_TOTAL_CHARS",
    "KnowledgeObservationCache", "MAX_ENTRY_CHARS", "feedback_fingerprint",
    "from_payload", "load", "save", "to_payload",
]
