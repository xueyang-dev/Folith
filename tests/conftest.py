"""Keep every test run out of the developer's real Folith data.

``core.OUTPUT_DIR`` is read from ``FOLITH_OUTPUT_DIR`` at import time, and many
test modules import ``core`` at module top, before any fixture can repoint it.
A test that only sets the env var inside the test body then writes its jobs into
whatever directory ``core`` pinned: the repo's ``outputs/`` or, when the shell
already carried the desktop path, ``~/Library/Application Support/Folith/data``.
That is how 100+ ``broken.docx`` tasks ended up in a real install's task list.

pytest imports this file before collecting any test module, so the defaults are
pointed at a per-session scratch directory here, and the session fails if a real
data directory still gained entries.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith.desktop import paths as _desktop_paths  # noqa: E402  (no core import)

_DATA_ENVS = ("FOLITH_OUTPUT_DIR", "FOLIOTHREAD_OUTPUT_DIR", _desktop_paths.DATA_DIR_ENV)
_LOG_ENV = _desktop_paths.LOG_DIR_ENV


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve()


_REAL_DIRS = tuple(dict.fromkeys(_resolved(p) for p in (
    _desktop_paths.default_data_dir(),
    ROOT / "outputs",
)))


def _is_real(value: str | None) -> bool:
    if not value:
        return True
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return _resolved(path) in _REAL_DIRS


# Tests talk to local stub servers on 127.0.0.1.  A macOS system proxy (Clash and
# similar, common on developer machines) would otherwise intercept those requests
# and answer 502, so test results would depend on whether the proxy is on.
for _name in ("NO_PROXY", "no_proxy"):
    _hosts = [h for h in os.environ.get(_name, "").split(",") if h]
    for _host in ("127.0.0.1", "localhost", "::1"):
        if _host not in _hosts:
            _hosts.append(_host)
    os.environ[_name] = ",".join(_hosts)

_SESSION_DIR = Path(tempfile.mkdtemp(prefix="folith-test-data-"))
if any(_is_real(os.environ.get(name)) for name in _DATA_ENVS):
    for name in _DATA_ENVS:
        os.environ[name] = str(_SESSION_DIR / "data")
if not os.environ.get(_LOG_ENV) or _resolved(Path(os.environ[_LOG_ENV])) == _resolved(
        _desktop_paths.default_log_dir()):
    os.environ[_LOG_ENV] = str(_SESSION_DIR / "logs")


def _entries(directory: Path) -> set[str]:
    try:
        return set(os.listdir(directory))
    except OSError:
        return set()


_BEFORE = {directory: _entries(directory) for directory in _REAL_DIRS}

_QT = None


def _qt_classes():
    global _QT
    if _QT is None:
        try:
            from PySide6.QtCore import QEvent
            from PySide6.QtWidgets import QApplication
        except ImportError:
            _QT = ()
        else:
            _QT = (QEvent, QApplication)
    return _QT


@pytest.fixture(autouse=True)
def _discard_test_top_level_widgets():
    """Drop widgets created by a test before the next test applies a theme.

    Qt repolishes every live top-level widget on `setStyleSheet`. Without this,
    a full run accumulates thousands of hidden windows and later theme changes
    take tens of minutes on Windows.
    """
    yield
    # The leak this guards against is specific to Windows theme repolishing.
    # On Linux, deleting top-level widgets from every test teardown can crash
    # the Qt event loop (SIGBUS in sendPostedEvents) while a test still holds a
    # wrapper for a preview widget; Linux runs are short enough without it.
    if os.name != "nt":
        return
    classes = _qt_classes()
    if not classes:
        return
    QEvent, QApplication = classes
    app = QApplication.instance()
    if app is None:
        return
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    for widget in list(QApplication.topLevelWidgets()):
        try:
            widget.deleteLater()
        except RuntimeError:
            pass
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def pytest_sessionfinish(session, exitstatus):
    leaked = {
        directory: sorted(_entries(directory) - before)
        for directory, before in _BEFORE.items()
    }
    leaked = {directory: names for directory, names in leaked.items() if names}
    if not leaked:
        return
    lines = ["", "tests wrote into a real Folith data directory:"]
    for directory, names in leaked.items():
        shown = ", ".join(names[:10]) + (" ..." if len(names) > 10 else "")
        lines.append(f"  {directory}: {shown}")
    lines.append("repoint core.OUTPUT_DIR (monkeypatch.setattr) before creating jobs.")
    session.config.get_terminal_writer().line("\n".join(lines), red=True)
    session.exitstatus = 1
