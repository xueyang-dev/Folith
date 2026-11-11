"""Destroyed dialogs must never be touched by their queued callbacks.

The concept and batch-unify flows connect signal lambdas that used to close over the
dialog (a reference cycle) and hand callbacks to the async runner.  Both kept the
dialog's Python wrapper alive past ``deleteLater()``; touching the deleted C++ object
is the stale-wrapper SIGSEGV class (see ``docs/segfault-audit-0.8.2.md``).  These tests
delete the dialog and then fire the captured runner callback: it must be a no-op.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QWidget  # noqa: E402
from shiboken6 import Shiboken  # noqa: E402

from concept_workflow_test import concept_job, save  # noqa: E402,F401
from folith.engine import concept_workflow as engine  # noqa: E402
from folith.qt import adapter, batch_unify_dialog, concept_dialog  # noqa: E402


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _flush_deferred_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class _Runner:
    """Captures the callbacks the dialogs hand to the real async runner."""

    def __init__(self) -> None:
        self.done = None
        self.error = None

    def submit(self, fn, *args, on_done=None, on_error=None, **kwargs):
        self.done, self.error = on_done, on_error


class _Workbench:
    def reload_current_segment(self) -> None:
        pass


class _Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._current_job_id = "job-1"
        self.runner = _Runner()
        self.workbench = _Workbench()
        self.view = None

    def refresh_workbench(self, force: bool = False) -> None:
        pass

    def _confirm(self, *args, **kwargs) -> bool:
        return False

    def _run_with_busy(self, *args, on_done=None, **kwargs):
        on_done(self.view)


def test_concept_callback_after_dialog_deleted_is_a_noop(app, concept_job, monkeypatch):
    window = _Window()
    window.view = engine.view(concept_job)
    monkeypatch.setattr(adapter, "concept_workflow_view", lambda job_id: window.view)

    seen: dict = {}

    def fake_exec(self) -> None:
        seen["dialog"] = self
        self.saveRequested.emit({}, {})

    monkeypatch.setattr(concept_dialog.ConceptDecisionDialog, "exec", fake_exec)
    monkeypatch.setattr(concept_dialog.ConceptDecisionDialog, "deleteLater",
                        lambda self: Shiboken.delete(self))
    concept_dialog.open_concept_workflow(window)
    dialog = seen["dialog"]
    assert not Shiboken.isValid(dialog)
    assert window.runner.done is not None
    window.runner.done({"view": window.view, "changed": 0})


def test_batch_unify_callback_after_dialog_deleted_is_a_noop(app, monkeypatch):
    window = _Window()
    window.view = ({"entities": []}, {"pairs": [], "entity_registry": []}, {})
    monkeypatch.setattr(batch_unify_dialog, "_load_review_data", lambda job_id: window.view)

    seen: dict = {}

    def fake_exec(self) -> None:
        seen["dialog"] = self
        self.applyRequested.emit([{"source": "A", "target": "甲"}])

    monkeypatch.setattr(batch_unify_dialog.BatchUnifyDialog, "exec", fake_exec)
    monkeypatch.setattr(batch_unify_dialog.BatchUnifyDialog, "deleteLater",
                        lambda self: Shiboken.delete(self))
    batch_unify_dialog.open_batch_unify_dialog(window)
    dialog = seen["dialog"]
    assert not Shiboken.isValid(dialog)
    assert window.runner.done is not None
    window.runner.done(None)


def test_notification_details_dialog_is_deleted_on_close(app):
    from folith.qt.notifications import Toast

    host = QMainWindow()
    toast = Toast(host)
    toast.present("通知正文", detail="完整说明")
    toast._show_details()
    dialog = next(w for w in QApplication.topLevelWidgets()
                  if isinstance(w, QDialog) and w.isVisible())
    assert dialog is not None
    dialog.close()
    _flush_deferred_deletes()
    _flush_deferred_deletes()
    app.processEvents()
    assert not Shiboken.isValid(dialog)
