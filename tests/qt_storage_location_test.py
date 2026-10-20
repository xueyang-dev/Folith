"""The remembered data/output folder is a preference, not part of the data."""
import os

import pytest
from PySide6.QtCore import QSettings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import storage_location  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "settings.ini"
    monkeypatch.setattr(
        storage_location, "_settings",
        lambda: QSettings(str(path), QSettings.Format.IniFormat))
    return tmp_path


def test_missing_preference_means_default(store):
    assert storage_location.saved_data_dir() == ""


def test_set_and_clear_roundtrip(store):
    target = store / "FolithData"
    storage_location.set_saved_data_dir(target)
    assert storage_location.saved_data_dir() == str(target)
    storage_location.clear_saved_data_dir()
    assert storage_location.saved_data_dir() == ""


def test_expanduser_is_applied(store, monkeypatch):
    monkeypatch.setenv("HOME", str(store))
    monkeypatch.setenv("USERPROFILE", str(store))
    storage_location.set_saved_data_dir("~/FolithData")
    assert storage_location.saved_data_dir() == str(store / "FolithData")
