"""C-46: three followups from Codex CX-07 (#580, #588, #590)."""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"
from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from folith.qt import dialog_fit, settings_pages  # noqa: E402


def test_feedback_cleared_when_task_changes():
    QApplication.instance() or QApplication([])
    from folith.qt.review_panel import ReviewPanel
    panel = ReviewPanel()
    panel.bind_job("job-a")
    panel.triage_toggle.show_result(3)
    assert not panel.triage_toggle.feedback.isHidden()
    panel.bind_job("job-b")
    assert panel.triage_toggle.feedback.isHidden()
    assert panel.triage_toggle.feedback.text() == ""
    # same task must not clear a fresh feedback line
    panel.triage_toggle.show_result(2)
    panel.bind_job("job-b")
    assert not panel.triage_toggle.feedback.isHidden()


def test_fit_discards_margin_when_just_barely_fits(monkeypatch):
    app = QApplication.instance() or QApplication([])
    widget = QDialog()
    widget.resize(400, 300)
    widget.setMinimumWidth(400)
    class Screen:
        def availableGeometry(self): return QRect(0, 0, 400, 300)  # zero slack
    monkeypatch.setattr(widget, "screen", lambda: Screen())
    widget.show(); app.processEvents()
    dialog_fit.fit_to_screen(widget, recenter=True)
    frame = widget.frameGeometry()
    # no cosmetic 12 px margin when the area is this tight
    assert frame.left() == 0, frame
    assert frame.right() <= 400 + 4, frame  # offscreen decoration allowance
    widget.close()


def test_cleanup_paths_keeps_plist_line_with_newline_in_dir(tmp_path):
    data = str(tmp_path / "line1\nline2")
    logs = str(tmp_path / "logs")
    values = settings_pages.cleanup_paths(data, logs)
    assert len(values) == 3
    assert values[0] == data and values[1] == logs
    assert "plist" in values[2] or "HKEY" in values[2]
