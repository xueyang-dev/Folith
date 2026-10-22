"""A probe run must not read or write the developer's real preferences.

The window creates a task from the *stored* task defaults (report stage, review,
OCR mode), so a developer's leftover ``task/enable_report`` changes what the
probe flow runs: the fixture then enters the report stage, ``p2_done`` never
becomes true and the run reports a failure that has nothing to do with the build
under test.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from folith.qt import preferences  # noqa: E402


def test_normal_launch_uses_the_platform_store(tmp_path, monkeypatch):
    monkeypatch.delenv(preferences.SETTINGS_DIR_ENV, raising=False)
    assert preferences.redirected_dir() is None
    # Reading the name does not write: the user's own preferences stay untouched.
    assert str(tmp_path) not in preferences.settings().fileName()


def test_probe_settings_go_to_the_redirected_file(tmp_path, monkeypatch):
    target = tmp_path / "probe-settings"
    monkeypatch.setenv(preferences.SETTINGS_DIR_ENV, str(target))

    assert preferences.redirected_dir() == target
    settings = preferences.settings()
    assert Path(settings.fileName()).resolve().is_relative_to(target.resolve())

    settings.setValue("task/enable_report", True)
    settings.sync()
    assert (target / preferences.ORGANIZATION
            / f"{preferences.APPLICATION}.ini").is_file()
    # A second reader sees the same isolated store, not the platform's.
    assert preferences.settings().value("task/enable_report") is True
