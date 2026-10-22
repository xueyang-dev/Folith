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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "default_review_mode: run with the real DEFAULT_REVIEW_MODE (risk_selective)")


@pytest.fixture(autouse=True)
def _legacy_always_review_mode(request, monkeypatch):
    """Pin the review default to ``always`` for pipeline tests.

    New tasks default to ``risk_selective``, which skips independent review of
    clean batches.  Most pipeline tests assert the reviewed/TM-eligible path and
    were written against ``always``; they keep that behaviour here.  Tests that
    exercise the real default opt out with ``@pytest.mark.default_review_mode``.
    """
    if request.node.get_closest_marker("default_review_mode"):
        return
    from folith import review_gate
    monkeypatch.setattr(review_gate, "DEFAULT_REVIEW_MODE",
                        review_gate.REVIEW_MODE_ALWAYS)


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
    # macOS keeps the cleanup: the isolated Qt regression relies on leaked
    # windows being gone before the next animation test.
    if sys.platform.startswith("linux"):
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


_STRAY_WINDOWS: list[str] = []
_STRAY_FILTER = None
_NON_STRAY_TYPES = ("Popup", "ToolTip", "Dialog", "Tool", "Sheet", "SplashScreen")


def _product_frames() -> list[str]:
    import traceback

    tests_dir = str(ROOT / "tests")
    product_dir = str(ROOT / "folith")
    frames = []
    for frame in traceback.extract_stack():
        name = os.path.abspath(frame.filename)
        if name.startswith(product_dir) and not name.startswith(tests_dir):
            frames.append(f"{os.path.relpath(name, ROOT)}:{frame.lineno} {frame.name}")
    return frames


def _install_stray_window_filter():
    """Fail a Qt test when product code shows a parentless plain widget.

    A widget shown before it has a parent becomes a top-level window for a
    moment (a flash on Windows) and is hidden again once it joins a layout.
    """
    global _STRAY_FILTER
    if _STRAY_FILTER is not None:
        return
    classes = _qt_classes()
    if not classes:
        return
    QEvent, QApplication = classes
    from PySide6.QtCore import QObject, Qt
    from PySide6.QtWidgets import QDialog, QMainWindow, QMenu, QWidget

    allowed = tuple(getattr(Qt.WindowType, name) for name in _NON_STRAY_TYPES)

    class _StrayWindowFilter(QObject):
        def eventFilter(self, watched, event):  # noqa: N802 - Qt naming
            if event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
                try:
                    stray = (watched.parentWidget() is None and watched.isWindow()
                             and not isinstance(watched, (QDialog, QMainWindow, QMenu))
                             and (watched.windowType() & Qt.WindowType.WindowType_Mask)
                             not in allowed)
                except RuntimeError:
                    stray = False
                if stray:
                    frames = _product_frames()
                    if frames:
                        _STRAY_WINDOWS.append(
                            f"{type(watched).__name__} shown without a parent "
                            f"(objectName={watched.objectName()!r}) at {frames[-1]}")
            return False

    app = QApplication.instance() or QApplication([])
    _STRAY_FILTER = _StrayWindowFilter(app)
    app.installEventFilter(_STRAY_FILTER)


def _qt_test(item) -> bool:
    return item.path.name.startswith("qt_") and _qt_classes() != ()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    if not _qt_test(item):
        return (yield)
    _STRAY_WINDOWS.clear()
    _install_stray_window_filter()
    result = yield
    if _STRAY_WINDOWS:
        found = "\n  ".join(dict.fromkeys(_STRAY_WINDOWS))
        _STRAY_WINDOWS.clear()
        pytest.fail("product code showed parentless widgets as top-level windows:\n  " + found,
                    pytrace=False)
    return result


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
