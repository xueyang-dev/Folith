"""U-8: the compact terms sidebar must use theme token backgrounds, and the
status dot colour must depend only on status (not on row selection)."""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from qt_shell_previews_test import app, window  # noqa: F401,F811  # shared isolated real-window fixture


def test_terms_containers_are_named_and_styled(window):  # noqa: F811
    panel = window['window'].workbench.terms_panel
    assert panel.filter_bar.objectName() == 'termsFilterBar'
    assert panel.table.objectName() == 'termsTable'
    assert panel.editor_card.objectName() == 'termsEditorCard'
    app = QApplication.instance()  # noqa: F811
    sheet = app.styleSheet()
    assert 'QWidget#termsFilterBar' in sheet
    assert 'QFrame#termsEditorCard' in sheet
    assert 'QTableView#termsTable' in sheet
    assert 'QTableView#termsTable::item:selected' in sheet


def test_status_dot_colour_is_independent_of_selection(window):  # noqa: F811
    """U-8: the dot icon must declare the same pixmap for every QIcon mode,
    otherwise Qt derives a brown variant for selected rows."""
    panel = window['window'].workbench.terms_panel
    panel.apply('test-job', {'rows': [
        {'source': 'alpha', 'preferred': '甲', 'status': 'provisional'},
        {'source': 'beta', 'preferred': '乙', 'status': 'provisional'},
    ]})
    assert panel.table.rowCount() >= 2
    icons = [panel.table.item(row, 2).icon() for row in range(2)]
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
        images = [i.pixmap(QSize(8, 8), mode).toImage() for i in icons]
        assert bytes(images[0].constBits()) == bytes(images[1].constBits())
