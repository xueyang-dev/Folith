"""Remember a user-chosen data/output folder; applied on the next launch.

The location is a client preference, not part of the data directory itself: it
must remain readable when the configured folder is on a disconnected drive, so
the app can fall back to the default and still start.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings

ORGANIZATION = "Folith"
APPLICATION = "QtDesktop"
LOCATION_KEY = "storage/data_dir"


def application_dir(executable: str | None = None, frozen: bool | None = None) -> Path | None:
    """Folder that installing, upgrading or replacing the app rewrites.

    Windows: the folder holding ``Folith.exe`` (the installer replaces its
    contents on upgrade; a portable copy is replaced as a whole).  macOS: the
    ``.app`` bundle.  None for a source run, which has no install folder.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return None
    exe = Path(executable or sys.executable).resolve()
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return exe.parent


def inside_application_dir(path: str | Path, app_dir: Path | None = None) -> bool:
    """True when ``path`` is the application folder or inside it."""
    app_dir = application_dir() if app_dir is None else app_dir
    if app_dir is None:
        return False
    try:
        candidate = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return candidate == app_dir or app_dir in candidate.parents


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
