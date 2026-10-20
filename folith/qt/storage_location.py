"""Remember a user-chosen data/output folder; applied on the next launch.

The location is a client preference, not part of the data directory itself: it
must remain readable when the configured folder is on a disconnected drive, so
the app can fall back to the default and still start.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

ORGANIZATION = "Folith"
APPLICATION = "QtDesktop"
LOCATION_KEY = "storage/data_dir"


def _settings() -> QSettings:
    return QSettings(ORGANIZATION, APPLICATION)


def saved_data_dir() -> str:
    value = _settings().value(LOCATION_KEY)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def set_saved_data_dir(path: str | Path) -> None:
    store = _settings()
    store.setValue(LOCATION_KEY, str(Path(path).expanduser()))
    store.sync()


def clear_saved_data_dir() -> None:
    store = _settings()
    store.remove(LOCATION_KEY)
    store.sync()
