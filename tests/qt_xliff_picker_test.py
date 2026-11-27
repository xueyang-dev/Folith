"""#526: XLIFF import filter advertises every supported bilingual suffix."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from folith.qt import adapter, xliff_io  # noqa: E402


def test_picker_has_separate_filters_and_all_bilingual_suffixes():
    doc, bilingual = xliff_io.picker_filters(adapter.SOURCE_SUFFIXES)
    assert doc != bilingual
    assert "双语文件" in bilingual or "Bilingual" in bilingual
    for suffix in xliff_io.BILINGUAL_SUFFIXES:
        assert f"*{suffix}" in bilingual
    for suffix in adapter.SOURCE_SUFFIXES:
        assert f"*{suffix}" in doc


def test_document_filter_has_no_bilingual_suffixes():
    doc, _bilingual = xliff_io.picker_filters(adapter.SOURCE_SUFFIXES)
    for suffix in xliff_io.BILINGUAL_SUFFIXES:
        assert f"*{suffix}" not in doc


def test_writeback_hidden_reason_is_shown_for_non_xliff12():
    from PySide6.QtWidgets import QApplication
    from folith.qt.delivery_panel import DeliveryWorkspace

    QApplication.instance() or QApplication([])
    panel = DeliveryWorkspace()
    panel.set_bilingual_dialect("xliff12")
    assert panel.bilingual_targets.isVisible() or not panel.bilingual_targets.isHidden()
    assert panel.bilingual_reason.text() == ""
    panel.set_bilingual_dialect("sdlxliff")
    assert panel.bilingual_targets.isHidden()
    assert "只支持原样导出" in panel.bilingual_reason.text() or \
        panel.bilingual_reason.text() != ""
