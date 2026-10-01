"""Lifecycle-managed LLM transport: client registry with connection reuse.

Why this module exists
----------------------
``core.call_llm`` used to construct a fresh ``OpenAI`` (or ``genai.Client``)
for every single request.  Each construction builds a new ``httpx`` transport
and therefore a new connection pool, so no TCP/TLS connection was ever reused
between the ~4-40 requests of one translation batch loop.  Under the bounded
concurrency paths (translation batches, source cleanup, knowledge feedback)
this multiplied the per-request setup cost and the number of sockets the
provider saw, which in turn made 429 storms more likely.

Design contract
---------------
* A client is reused only when **every** dimension that can change the wire
  behaviour is identical: provider, transport kind, resolved base URL, proxy
  mode, connect/read timeouts and max retries.  Different API keys never share
  a client.
* The API key is never stored in a cache key, log line, or performance
  artifact.  Identity uses a truncated SHA-256 fingerprint, which is enough to
  separate keys and useless for recovering one.
* The pool is thread-local.  ``ThreadPoolExecutor`` reuses its worker threads,
  so each worker keeps its own keep-alive pool for the lifetime of the run and
  there is no cross-thread sharing of an SDK object.
* Closing is explicit: :func:`close_thread_clients` for a worker that is done,
  :func:`close_all_clients` for the end of a run.  ``atexit`` covers the
  process-exit case.
* ``FOLIOTHREAD_LLM_CLIENT_POOL=0`` disables reuse entirely and restores the
  previous construct-per-call behaviour, which is the kill switch if a provider
  or relay ever misbehaves with a persistent connection.
"""
from __future__ import annotations

import atexit
import hashlib
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

# Timeouts used by ``core.call_llm`` for the OpenAI-compatible route.  They live
# here so the identity key and the client factory can never drift apart.
OPENAI_TIMEOUT: Tuple[float, float] = (15.0, 180.0)
OPENAI_MAX_RETRIES = 1
GEMINI_TIMEOUT_MS = 150_000


def client_pool_enabled() -> bool:
    """Client reuse is on by default; the env var is the documented off switch."""
    raw = os.environ.get("FOLIOTHREAD_LLM_CLIENT_POOL")
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def api_key_fingerprint(api_key: Any) -> str:
    """Non-reversible identity for an API key.

    Only the first 16 hex characters are kept.  This is a *cache discriminator*,
    not a secret: it cannot be used to call the provider, and it is safe to
    include in diagnostics because inverting it is infeasible.
    """
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ClientIdentity:
    """Everything that can change the behaviour of a reused HTTP client."""

    provider: str
    kind: str
    base_url: str
    proxy_mode: str
    timeout_connect: float
    timeout_read: float
    max_retries: int
    key_fingerprint: str

    def cache_key(self) -> str:
        return "|".join((
            self.provider,
            self.kind,
            self.base_url,
            self.proxy_mode,
            f"{self.timeout_connect:.3f}",
            f"{self.timeout_read:.3f}",
            str(self.max_retries),
            self.key_fingerprint,
        ))

    def describe(self) -> Dict[str, Any]:
        """Secret-free description for logs and performance artifacts."""
        return {
            "provider": self.provider,
            "kind": self.kind,
            "base_url": self.base_url,
            "proxy_mode": self.proxy_mode,
            "timeout_connect": self.timeout_connect,
            "timeout_read": self.timeout_read,
            "max_retries": self.max_retries,
            "key_fingerprint": self.key_fingerprint,
        }


@dataclass
class RegistryStats:
    created: int = 0
    reused: int = 0
    closed: int = 0
    failed_factory: int = 0
    threads: int = 0
    identities: Dict[str, int] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "created": self.created,
            "reused": self.reused,
            "closed": self.closed,
            "failed_factory": self.failed_factory,
            "threads": self.threads,
            "identities": dict(self.identities),
        }


class LLMClientRegistry:
    """Thread-local, lifecycle-managed client registry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # thread id -> {cache_key: client}
        self._by_thread: Dict[int, Dict[str, Any]] = {}
        # cache_key -> [(thread_id, client)] so close() can reach every instance
        self._all: Dict[str, list] = {}
        self._closed = False
        self._stats = RegistryStats()

    # -- acquisition ------------------------------------------------------
    def acquire(self, identity: ClientIdentity, factory: Callable[[], Any]) -> Any:
        """Return a reusable client, creating it once per thread + identity."""
        if not client_pool_enabled():
            self._stats.created += 1
            return factory()
        key = identity.cache_key()
        thread_id = threading.get_ident()
        with self._lock:
            if self._closed:
                raise RuntimeError("LLM client registry 已关闭")
            bucket = self._by_thread.get(thread_id)
            if bucket is None:
                bucket = {}
                self._by_thread[thread_id] = bucket
                self._stats.threads = len(self._by_thread)
            client = bucket.get(key)
            if client is not None:
                self._stats.reused += 1
                return client
            try:
                client = factory()
            except BaseException:
                self._stats.failed_factory += 1
                raise
            bucket[key] = client
            self._all.setdefault(key, []).append((thread_id, client))
            self._stats.created += 1
            self._stats.identities[key] = self._stats.identities.get(key, 0) + 1
            return client

    # -- release ----------------------------------------------------------
    def close_thread_clients(self) -> int:
        """Close and drop the current thread's clients (call from worker cleanup)."""
        thread_id = threading.get_ident()
        with self._lock:
            bucket = self._by_thread.pop(thread_id, {})
            for key, client in bucket.items():
                self._all.get(key, [])[:] = [
                    entry for entry in self._all.get(key, []) if entry[0] != thread_id
                ]
            self._stats.threads = len(self._by_thread)
        closed = 0
        for client in bucket.values():
            if _close_client(client):
                closed += 1
        with self._lock:
            self._stats.closed += closed
        return closed

    def prune_dead_threads(self) -> int:
        """Drop clients whose owning thread has exited.

        Long runs create and retire executor threads; without this the pool
        would hold sockets for threads that no longer exist.
        """
        alive = {thread.ident for thread in threading.enumerate() if thread.ident}
        with self._lock:
            dead = [tid for tid in self._by_thread if tid not in alive]
            doomed = []
            for tid in dead:
                doomed.extend(self._by_thread.pop(tid, {}).values())
            if dead:
                for key, entries in list(self._all.items()):
                    kept = [entry for entry in entries if entry[0] in self._by_thread]
                    if kept:
                        self._all[key] = kept
                    else:
                        self._all.pop(key, None)
            self._stats.threads = len(self._by_thread)
        closed = sum(1 for client in doomed if _close_client(client))
        with self._lock:
            self._stats.closed += closed
        return closed

    def close_all(self) -> int:
        """Close every pooled client. Safe to call more than once."""
        with self._lock:
            self._closed = True
            clients = [client for entries in self._all.values()
                       for _, client in entries]
            self._all.clear()
            self._by_thread.clear()
            self._stats.threads = 0
        closed = sum(1 for client in clients if _close_client(client))
        with self._lock:
            self._stats.closed += closed
        return closed

    def reopen(self) -> None:
        """Allow acquisition again after :meth:`close_all` (used by tests)."""
        with self._lock:
            self._closed = False

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return self._stats.snapshot()

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = RegistryStats(threads=len(self._by_thread))


def _close_client(client: Any) -> bool:
    """Best-effort close; a client that cannot close is not an error."""
    for name in ("close", "aclose"):
        closer = getattr(client, name, None)
        if callable(closer):
            try:
                closer()
                return True
            except Exception:  # noqa: BLE001 - teardown must never raise
                return False
    # ``genai.Client`` may not expose close(); it holds an httpx client that does.
    for attribute in ("_api_client", "_client"):
        inner = getattr(client, attribute, None)
        closer = getattr(inner, "close", None)
        if callable(closer):
            try:
                closer()
                return True
            except Exception:  # noqa: BLE001
                return False
    return False


_REGISTRY = LLMClientRegistry()
atexit.register(_REGISTRY.close_all)


def get_registry() -> LLMClientRegistry:
    return _REGISTRY


def close_thread_clients() -> int:
    return _REGISTRY.close_thread_clients()


def close_all_clients() -> int:
    """Close every pooled LLM client (end of a run / application shutdown)."""
    closed = _REGISTRY.close_all()
    _REGISTRY.reopen()
    return closed


def close_client(client: Any) -> bool:
    """Best-effort release of a single client. Never raises."""
    return _close_client(client)


def client_registry_stats() -> Dict[str, Any]:
    return _REGISTRY.stats()


def reset_client_registry_stats() -> None:
    _REGISTRY.reset_stats()


def build_openai_identity(
    *,
    provider: str,
    base_url: Optional[str],
    proxy_bypass: bool,
    api_key: str,
    timeout: Tuple[float, float] = OPENAI_TIMEOUT,
    max_retries: int = OPENAI_MAX_RETRIES,
) -> ClientIdentity:
    return ClientIdentity(
        provider=str(provider or ""),
        kind="openai",
        base_url=str(base_url or ""),
        proxy_mode="bypass" if proxy_bypass else "env",
        timeout_connect=float(timeout[0]),
        timeout_read=float(timeout[1]),
        max_retries=int(max_retries),
        key_fingerprint=api_key_fingerprint(api_key),
    )


def build_gemini_identity(*, provider: str, api_key: str,
                          timeout_ms: int = GEMINI_TIMEOUT_MS) -> ClientIdentity:
    return ClientIdentity(
        provider=str(provider or ""),
        kind="gemini",
        base_url="",
        proxy_mode="env",
        timeout_connect=float(timeout_ms) / 1000.0,
        timeout_read=float(timeout_ms) / 1000.0,
        max_retries=0,
        key_fingerprint=api_key_fingerprint(api_key),
    )
