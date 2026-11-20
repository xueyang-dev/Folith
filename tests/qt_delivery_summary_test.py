"""#521: setup and delivery stages use the same compact summary component."""
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
from PySide6.QtWidgets import QApplication  # noqa: E402, F401


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_both_stages_use_the_shared_component():
    """Code check: the one widget is imported by setup and delivery panels."""
    setup = Path(ROOT, "folith/qt/setup_panel.py").read_text(encoding="utf-8")
    delivery = Path(ROOT, "folith/qt/delivery_panel.py").read_text(encoding="utf-8")
    assert "DeliverySummaryRow()" in setup
    assert "DeliverySummaryRow(" in delivery
    assert "self.delivery_summary_row" in setup
    assert "self.summary_row" in delivery


def test_summary_shows_preset_and_file_facts(qapp):
    from folith.qt.delivery_summary import DeliverySummaryRow

    row = DeliverySummaryRow()
    row.set_summary("标准交付", "3 个文件 · DOCX ×2 · XLSX ×1")
    assert row.preset.text() == "标准交付"
    assert "3 个文件" in row.detail.text()
    assert "标准交付" in row.toolTip() and "3 个文件" in row.toolTip()
    assert row.accessibleName() == row.toolTip()
    row.expand(True)
    assert not row.chevron.isHidden()


def test_setup_panel_owns_a_summary_row(qapp):
    from folith.qt.setup_panel import TaskSetupPanel

    panel = TaskSetupPanel()
    assert panel.delivery_summary_row is not None
    assert panel.delivery_summary.isHidden(), "facts are stated once, by the shared row"
