"""#524: review shortcuts have one definition (binding + keycap hint)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import components, platform_ui  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


REVIEW_ACTIONS = ("review_next", "review_apply", "review_open",
                  "review_confirm", "review_keep", "review_edit")


def test_binding_and_hint_come_from_one_definition(qapp):
    for action in REVIEW_ACTIONS:
        seq = platform_ui.shortcut(action)
        assert not seq.isEmpty(), action
        text = platform_ui.shortcut_text(action)
        assert text == seq.toString(QKeySequence.SequenceFormat.NativeText)
        cap = components.KeyHint(action)
        assert cap.text() == text
        assert cap.objectName() == "keyHint"


def test_review_panel_uses_platform_ui_for_every_shortcut(qapp):
    source = (ROOT / "folith/qt/review_panel.py").read_text(encoding="utf-8")
    assert 'QKeySequence("N")' not in source
    assert 'QShortcut(platform_ui.shortcut(action), self)' in source
    for action in REVIEW_ACTIONS:
        assert f'"{action}"' in source
    # no hand-typed shortcut sentence in the hint
    assert "快捷键：N 下一项" not in source


def test_shortcuts_dialog_lists_review_actions():
    source = (ROOT / "folith/qt/settings_pages.py").read_text(encoding="utf-8")
    for action in REVIEW_ACTIONS:
        assert f'("{action}"' in source


def test_generated_hint_from_the_same_binding(qapp):
    """The visible keycap text is never a literal from the translated sentence."""
    from folith.qt.review_panel import ReviewPanel

    panel = ReviewPanel()
    caps = panel.shortcut_hint.findChildren(components.KeyHint)
    assert len(caps) == len(REVIEW_ACTIONS)
    for cap, action in zip(caps, REVIEW_ACTIONS):
        assert cap.text() == platform_ui.shortcut_text(action)
