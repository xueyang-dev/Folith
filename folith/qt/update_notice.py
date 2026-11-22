"""Sidebar update notice: one bounded check per launch, cached with backoff (#518)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import support

CACHE_SECONDS = 6 * 60 * 60
RETRY_SECONDS = 15 * 60


def _version(value: str) -> tuple[int, ...]:
    parts = []
    for piece in str(value or "").lstrip("v").split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class UpdateNotice:
    """Read-only result of the launch-time GitHub Releases check."""

    def __init__(self, cache_dir: Path, *, now: Callable[[], float] = time.time,
                 checker: Callable[..., dict] | None = None) -> None:
        self._path = Path(cache_dir) / "update-notice.json"
        self._now = now
        self._checker = checker or support.check_for_update
        self.latest = ""
        self.url = ""
        self.notes = ""

    def _load(self) -> dict:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _cache_age(self, payload: dict) -> float:
        try:
            checked = float(payload.get("checked_at", 0))
        except (TypeError, ValueError, OverflowError):
            return float("inf")
        age = self._now() - checked
        return age if age >= 0 else float("inf")

    def cached(self, current: str = "") -> bool:
        """True when a cached newer release is still relevant to *current*."""
        payload = self._load()
        state = payload.get("state")
        if state not in ("newer", "current", "failed"):
            return False
        delay = CACHE_SECONDS if state == "newer" else RETRY_SECONDS
        if self._cache_age(payload) > delay:
            return False
        latest = str(payload.get("latest", ""))
        # A cached 9.0.0 is irrelevant when 9.1.0 is already installed.
        if state == "newer" and _version(latest) <= _version(current):
            return False
        if state == "newer":
            self.latest = latest
            self.url = str(payload.get("url", ""))
            self.notes = str(payload.get("notes", ""))
            return bool(latest)
        return False

    def check(self, current: str) -> bool:
        """Return True when a newer release was found; failures stay silent."""
        if self.cached(current):
            return True
        try:
            result = self._checker(current)
        except Exception:  # noqa: BLE001 - offline / rate-limited: log-only
            result = {"state": "failed", "latest": "", "url": "", "notes": ""}
        state = result.get("state")
        if state not in ("newer", "current", "failed"):
            state = "failed"
        payload = {"checked_at": self._now(), "state": state,
                   "latest": str(result.get("latest", "")),
                   "url": str(result.get("url", "")),
                   "notes": str(result.get("notes", ""))}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        if state != "newer":
            return False
        self.latest = str(result.get("latest", ""))
        self.url = str(payload["url"])
        self.notes = str(payload["notes"])
        return bool(self.latest)
