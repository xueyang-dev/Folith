"""Rebuilt panels must never leave stray top-level windows behind.

A widget added to a visible layout is shown by a queued call.  When the same
panel is rebuilt twice in one turn ("save and next" did this to the inspector),
widgets detached with ``setParent(None)`` before that queued show ran popped up
as separate windows on screen and took keyboard focus from the editor.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from folith.qt import segment_assist  # noqa: E402

#: Detaches that hand the widget to a new owner (re-parenting, not discarding).
REPARENT_SITES = {("setup_panel.py", "self.footer_bar.setParent(None)")}


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _strays(app, *keep):
    return [widget for widget in app.topLevelWidgets()
            if widget.isVisible() and widget not in keep]


def test_rebuilding_context_twice_in_one_turn_leaves_no_window(app):
    host = QWidget()
    layout = QVBoxLayout(host)
    view = segment_assist.ContextView()
    layout.addWidget(view)
    host.show()
    app.processEvents()
    context = {"previous": {"index": 0, "source": "a", "target": "甲"},
               "next": {"index": 2, "source": "c", "target": "丙"}}
    view.set_context(context)
    view.set_context(context)  # same turn: the first rows' queued show is pending
    app.processEvents()
    try:
        assert _strays(app, host) == []
    finally:
        host.close()
        host.deleteLater()
        app.processEvents()


def test_every_discarding_detach_hides_first():
    problems = []
    for path in sorted((ROOT / "folith" / "qt").glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            match = re.match(r"^\s*(.+)\.setParent\(None\)\s*$", line)
            if not match or (path.name, line.strip()) in REPARENT_SITES:
                continue
            previous = lines[number - 1].strip() if number else ""
            if not previous.startswith(f"{match.group(1)}.hide()"):
                problems.append(f"{path.name}:{number + 1}: {line.strip()}")
    assert not problems, "hide() before setParent(None):\n" + "\n".join(problems)
