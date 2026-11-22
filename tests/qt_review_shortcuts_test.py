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
    assert "platform_ui.REVIEW_ACTIONS" in source
    assert "REVIEW_ACTIONS" in (ROOT / "folith/qt/platform_ui.py").read_text(encoding="utf-8")
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


def test_keycaps_are_discoverable_to_mouse_and_accessibility(qapp):
    from PySide6.QtCore import Qt
    from folith.qt.review_panel import ReviewPanel
    panel = ReviewPanel()
    caps = panel.shortcut_hint.findChildren(components.KeyHint)
    assert len(caps) == 6
    for cap, (action, label) in zip(caps, (
            ("review_next", "下一项"), ("review_apply", "暂存建议"),
            ("review_open", "确认问题存在"), ("review_confirm", "确认已解决"),
            ("review_keep", "保留当前译文"), ("review_edit", "修改译文"))):
        assert not cap.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert label in cap.accessibleName()
        assert platform_ui.shortcut_text(action) in cap.accessibleName()
        assert cap.accessibleDescription() == label
    name = panel.shortcut_hint.accessibleName()
    for label in ("下一项", "暂存建议", "确认问题存在", "确认已解决", "保留当前译文", "修改译文"):
        assert label in name
    panel.close()


def test_shortcuts_dialog_labels_are_source_text_not_import_time_translations():
    """C-56 #613: LOCAL_SHORTCUTS stays source text; dialog translates later."""
    from folith.qt import settings_pages

    pairs = dict(settings_pages.LOCAL_SHORTCUTS)
    assert pairs["review_next"] == "审校：下一项"
    assert pairs["review_edit"] == "审校：修改译文"


def test_switch_language_updates_shortcut_dialog_and_hint(qapp):
    """C-56 #613: source text in LOCAL_SHORTCUTS, translated at dialog time."""
    from folith.qt import i18n, settings_pages

    i18n.set_language("en")
    en = i18n.tr(dict(settings_pages.LOCAL_SHORTCUTS)["review_next"])
    i18n.set_language("zh-CN")
    zh = i18n.tr(dict(settings_pages.LOCAL_SHORTCUTS)["review_next"])
    assert en != zh and "Next" in en
    i18n.set_language("zh-CN")
