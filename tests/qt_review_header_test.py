"""#523: review stat labels are visible and the triage mode is a labelled switch."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["FOLITH_QT_NONINTERACTIVE"] = "1"

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from folith.qt import components, style  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme_name", ("light", "dark"))
def test_stat_labels_have_a_real_width(qapp, theme_name):
    from folith.qt import i18n

    style.apply_theme(qapp, theme_name)
    strip = style.StatStrip([("must", i18n.tr("必须处理")),
                             ("suggested", i18n.tr("建议")),
                             ("auto", i18n.tr("自动处理")),
                             ("reference", i18n.tr("仅供参考"))])
    # Regression guard for the 0-width bug: each name has a positive minimum
    # and each cell has stretch 1 (the old trailing addStretch(1) starved them).
    for label in strip.findChildren(style.ElideLabel):
        assert label.minimumWidth() > 0
    for cell in strip._cells.values():
        assert strip.layout().stretch(strip.layout().indexOf(cell)) == 1
    strip.show()
    for width in (1280, 980):
        strip.resize(width, 28)
        qapp.processEvents()
        strip.layout().activate()
        for label in strip.findChildren(style.ElideLabel):
            assert label.width() > 0, (theme_name, width, label.text())
            assert label.text() and not label.text().startswith("…")
    assert "addStretch" not in strip.layout().__class__.__name__.lower()


def test_triage_mode_is_a_switched_row_with_visible_state(qapp):
    from folith.qt.triage_mode import TriageModeRow

    row = TriageModeRow()
    assert isinstance(row.switch, components.Switch)
    assert "关" in row.state.text()
    row.setChecked(True)
    assert "开" in row.state.text()
    assert "只对本任务生效" in row.note.text()
    seen = []
    row.toggled.connect(seen.append)
    row.setChecked(False)
    assert seen == [False]


def test_triage_feedback_says_what_happened(qapp):
    from folith.qt.triage_mode import TriageModeRow

    row = TriageModeRow()
    row.show_result(3)
    assert "3" in row.feedback.text() and not row.feedback.isHidden()
    row.show_result(0)
    assert "没有" in row.feedback.text()
    row.show_disabled_notice()
    assert "审计" in row.feedback.text()
