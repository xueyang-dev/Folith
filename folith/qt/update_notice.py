"""Sidebar update notice: one check per launch, cached for six hours (#518)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import support

CACHE_SECONDS = 6 * 60 * 60


def _version_tuple(value: str) -> tuple[int, ...]:
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

    def cached(self) -> bool:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if self._now() - float(payload.get("checked_at", 0)) > CACHE_SECONDS:
            return False
        if payload.get("state") != "newer":
            return False
        self.latest = str(payload.get("latest", ""))
        self.url = str(payload.get("url", ""))
        self.notes = str(payload.get("notes", ""))
        return bool(self.latest)

    def check(self, current: str) -> bool:
        """Return True when a newer release was found; failures stay silent."""
        if self.cached():
            return True
        try:
            result = self._checker(current)
        except Exception:  # noqa: BLE001 - offline / rate-limited: log-only
            return False
        payload = {"checked_at": self._now(), "state": result.get("state"),
                   "latest": result.get("latest", ""), "url": result.get("url", ""),
                   "notes": result.get("notes", "")}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        if result.get("state") != "newer":
            return False
        self.latest = str(result.get("latest", ""))
        self.url = str(result.get("url", ""))
        self.notes = str(result.get("notes", ""))
        return bool(self.latest)
