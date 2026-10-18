"""Queued UI callbacks must not outlive their owning Qt runner."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from folith.qt.workers import TaskRunner


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("outcome", ["done", "failed", "progress"])
@pytest.mark.parametrize("destroyed", [False, True])
def test_callbacks_follow_runner_lifetime(app, outcome, destroyed):
    runner = TaskRunner()
    seen = []

    def work(*, on_progress):
        if outcome == "progress":
            on_progress(1, 2)
        if outcome == "failed":
            raise RuntimeError("offline failure")
        return "done"

    runner.submit(work, on_done=lambda value: seen.append("done"),
                  on_error=lambda *args: seen.append("failed"),
                  on_progress=lambda *args: seen.append("progress"))
    assert runner.wait_for_done(3000)
    if destroyed:
        delete(runner)
    app.processEvents()
    assert seen == ([] if destroyed else
                    ["progress", "done"] if outcome == "progress" else [outcome])
    if not destroyed:
        delete(runner)
