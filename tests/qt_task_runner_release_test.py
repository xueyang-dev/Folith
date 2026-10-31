"""A finished background task must not keep what its callable captured alive.

A poll read captures the whole task state; before the runner released finished
tasks, every poll leaked one copy and a long book grew by gigabytes.
"""
from __future__ import annotations

import gc
import os
import sys
import time
import weakref
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt.workers import TaskRunner  # noqa: E402


class _Big(dict):
    """A dict subclass so the test can hold a weak reference to it."""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _drain(app, runner, timeout=10.0):
    deadline = time.monotonic() + timeout
    while runner.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.005)


def _submit_capturing(runner, big, results, *, fail=False):
    def read():
        if fail:
            raise RuntimeError("boom")
        return len(big)

    def done(value):
        results.append((value, len(big)))

    def failed(message, detail):
        results.append(("failed", len(big)))

    runner.submit(read, on_done=done, on_error=failed, label="probe")


@pytest.mark.parametrize("fail", [False, True])
def test_finished_task_releases_what_its_callbacks_captured(app, fail):
    runner = TaskRunner()
    big = _Big(rows=list(range(1000)))
    ref = weakref.ref(big)
    results = []
    _submit_capturing(runner, big, results, fail=fail)
    del big
    _drain(app, runner)
    gc.collect()
    assert results, "the callback never ran"
    assert ref() is None, "a finished task still holds its captured state"


def test_callbacks_still_receive_the_result_and_progress(app):
    runner = TaskRunner()
    seen = []
    progress = []

    def work(on_progress):
        on_progress(1, 2)
        on_progress(2, 2)
        return "ok"

    runner.submit(work, on_done=seen.append,
                  on_progress=lambda done, total: progress.append((done, total)),
                  label="progress")
    _drain(app, runner)
    assert seen == ["ok"]
    assert progress == [(1, 2), (2, 2)]
