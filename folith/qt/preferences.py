"""Where the client's own preferences live, and how a probe redirects them.

The preferences are a *client* store: the interface language, the task defaults
a new task is created from, the delivery defaults, the last connection check.
They are deliberately not part of a task's data directory.

A probe has to be able to run without them.  ``QSettings(organization,
application)`` always uses the platform's native store -- ``setDefaultFormat``
does not apply to that constructor, and ``setPath`` is only honoured for
``IniFormat`` -- so redirecting means naming an explicit file.  With
``SETTINGS_DIR_ENV`` set, every reader and writer goes to that file instead,
which is what keeps a packaged-flow check evidence about the build rather than
about the machine it ran on.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings

ORGANIZATION = "Folith"
APPLICATION = "QtDesktop"

#: Directory a probe uses for its own preferences; unset in normal use.
SETTINGS_DIR_ENV = "FOLITH_QT_SETTINGS_DIR"


def redirected_dir() -> Path | None:
    """The probe's settings directory, or ``None`` for the platform's store."""
    value = os.environ.get(SETTINGS_DIR_ENV, "").strip()
    return Path(value).expanduser() if value else None


def settings() -> QSettings:
    """Open the client preferences (redirected while a probe is running)."""
    base = redirected_dir()
    if base is None:
        return QSettings(ORGANIZATION, APPLICATION)
    path = base / ORGANIZATION / f"{APPLICATION}.ini"
    path.parent.mkdir(parents=True, exist_ok=True)
    return QSettings(str(path), QSettings.Format.IniFormat)
