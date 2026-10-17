"""Exact translation-result cache: identical computation, paid once.

This is deliberately **not** translation memory.  Translation memory answers
"do we already have an approved translation *for this source sentence*" and is
a linguistic, human-reviewable artefact scoped by target language.  This cache
answers a narrower, purely mechanical question:

    has this exact model computation already been performed and paid for?

The identity is therefore the *effective request*, not the source text alone.
Rather than maintaining a hand-written list of fields that "materially affect"
the output (a list that silently rots as prompts grow), the key is a hash of the
fully rendered request: provider, model, system prompt, user prompt, temperature
and the structured-output schema.  If any of those differ, the key differs.  If
all of them are identical, the model would have been shown exactly the same
bytes, so reusing the recorded output cannot change the result.

That choice buys correctness and, for free, exactly the partial invalidation the
design asks for: changing one confirmed term only rewrites the glossary section
of the batches that actually inject that term, so only those batches miss.

The cache never influences translation memory, ordering, review or persistence
truth.  It is a derived side-cache: losing it costs API calls, never correctness.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import job_lock as _job_lock

#: 2: the effective route (endpoint + reasoning effort) joined the identity, so
#: entries written under version 1 cannot prove which endpoint produced them.
CACHE_VERSION = 2
DEFAULT_MAX_ENTRIES = 4096
#: Refuse to persist a single absurd entry rather than let one huge batch
#: dominate the cache file.
MAX_ENTRY_TARGET_CHARS = 200_000
#: Total retained translation characters.  Entry count alone is not a bound: a
#: document configured with very large batches would otherwise let 4096 entries
#: grow without limit.
DEFAULT_MAX_TOTAL_CHARS = 8_000_000
COMPACT_JSON = {"ensure_ascii": False, "separators": (",", ":")}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def prompt_fingerprint(*, provider: str, model: str, system_prompt: str,
                       user_prompt: str, temperature: Any,
                       response_format: Optional[Dict[str, Any]] = None,
                       route: Optional[Dict[str, Any]] = None) -> str:
    """Stable identity of one effective translation request.

    Volatile values (timestamps, request ids, UI state) are absent by
    construction: only the bytes the model actually sees, plus generation
    settings, participate.
    """
    material = {
        "v": CACHE_VERSION,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "temperature": None if temperature is None else round(float(temperature), 6),
        "response_format": response_format or None,
        "system": system_prompt or "",
        "user": user_prompt or "",
        "route": dict(route or {}),
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


class TranslationResultCache:
    """Bounded, thread-safe mapping of request fingerprint -> translated batch."""

    def __init__(self, entries: Optional[Dict[str, List[str]]] = None, *,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS):
        self.entries: Dict[str, List[str]] = {
            str(key): [str(item) for item in value]
            for key, value in (entries or {}).items()
            if isinstance(value, (list, tuple))
        }
        self.baseline = set(self.entries)
        self.max_entries = max(1, int(max_entries or DEFAULT_MAX_ENTRIES))
        self.max_total_chars = max(1, int(max_total_chars or DEFAULT_MAX_TOTAL_CHARS))
        self._lock = threading.RLock()
        self._chars = sum(self._entry_chars(value) for value in self.entries.values())
        self.hits = 0
        self.misses = 0
        self.stores = 0
        self.evictions = 0
        self.rejected = 0

    @staticmethod
    def _entry_chars(values: Sequence[str]) -> int:
        return sum(len(str(item)) for item in values)

    # -- lookup ---------------------------------------------------------
    def get(self, key: str) -> Optional[List[str]]:
        with self._lock:
            value = self.entries.get(key)
            if value is None:
                self.misses += 1
                return None
            self.hits += 1
            return list(value)

    def put(self, key: str, targets: Sequence[str]) -> bool:
        values = [str(item if item is not None else "") for item in targets or []]
        if not key or not values:
            return False
        size = self._entry_chars(values)
        if size > MAX_ENTRY_TARGET_CHARS:
            with self._lock:
                self.rejected += 1
            return False
        with self._lock:
            previous = self.entries.get(key)
            if previous is not None:
                self._chars -= self._entry_chars(previous)
            self.entries[key] = values
            self._chars += size
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
    def dirty(self) -> Dict[str, List[str]]:
        with self._lock:
            return {key: list(value) for key, value in self.entries.items()
                    if key not in self.baseline}

    def merge_from(self, other: "TranslationResultCache") -> None:
        """Fold entries written by another process/task into this one."""
        with self._lock:
            for key, value in other.entries.items():
                if key not in self.entries:
                    self._set_locked(key, list(value))
            self.baseline.update(other.baseline)
            self._evict_locked()

    def _set_locked(self, key: str, values: Sequence[str]) -> None:
        """Replace one entry while keeping the retained-character total exact."""
        previous = self.entries.get(key)
        if previous is not None:
            self._chars -= self._entry_chars(previous)
        self.entries[key] = list(values)
        self._chars += self._entry_chars(values)

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entries": len(self.entries),
                "retained_chars": self._chars,
                "hits": self.hits,
                "misses": self.misses,
                "stores": self.stores,
                "evictions": self.evictions,
                "rejected": self.rejected,
                "max_entries": self.max_entries,
                "max_total_chars": self.max_total_chars,
            }


def from_payload(payload: Any, *,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
                 ) -> TranslationResultCache:
    if not isinstance(payload, dict):
        return TranslationResultCache(max_entries=max_entries,
                                      max_total_chars=max_total_chars)
    if int(payload.get("version") or 0) != CACHE_VERSION:
        # A version bump invalidates the whole file: cheaper and safer than
        # migrating an identity scheme that has just been declared stale.
        return TranslationResultCache(max_entries=max_entries,
                                      max_total_chars=max_total_chars)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return TranslationResultCache(max_entries=max_entries,
                                      max_total_chars=max_total_chars)
    return TranslationResultCache(entries, max_entries=max_entries,
                                  max_total_chars=max_total_chars)


def to_payload(cache: TranslationResultCache) -> Dict[str, Any]:
    with cache._lock:  # noqa: SLF001 - serialisation owns the same lock
        return {"version": CACHE_VERSION,
                "entries": {key: list(value)
                            for key, value in cache.entries.items()}}


def load(path: Optional[Path], *,
         max_entries: int = DEFAULT_MAX_ENTRIES,
         max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS
         ) -> TranslationResultCache:
    """Read the cache file; a corrupt or unreadable file degrades to empty."""
    if path is None:
        return TranslationResultCache(max_entries=max_entries,
                                      max_total_chars=max_total_chars)
    try:
        if not Path(path).is_file():
            return TranslationResultCache(max_entries=max_entries,
                                          max_total_chars=max_total_chars)
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TranslationResultCache(max_entries=max_entries,
                                      max_total_chars=max_total_chars)
    return from_payload(payload, max_entries=max_entries,
                        max_total_chars=max_total_chars)


def save(cache: TranslationResultCache, path: Optional[Path]) -> bool:
    """Atomically merge this cache's delta into the on-disk file.

    Same shape as ``save_tm``: process lock, re-read under the lock, merge,
    encode once, write a temp file, replace.  A concurrent writer's entries are
    preserved instead of being clobbered by a stale read.
    """
    if path is None:
        return False
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    destination = path.with_name("translation_cache.write.lock")
    try:
        with _job_lock.exclusive(destination):
            merged = load(path, max_entries=cache.max_entries,
                          max_total_chars=cache.max_total_chars)
            with cache._lock:  # noqa: SLF001
                for key, value in cache.entries.items():
                    merged._set_locked(key, list(value))  # noqa: SLF001
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
    "TranslationResultCache", "from_payload", "load", "prompt_fingerprint",
    "save", "to_payload",
]
