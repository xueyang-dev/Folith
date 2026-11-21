"""U-8: the compact terms sidebar's root must paint a token background, and
the status dot colour must depend only on status (not on row selection)."""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from folith.qt import theme
from qt_shell_previews_test import app, window  # noqa: F401,F811  # shared isolated real-window fixture


def _rendered(window):  # noqa: F811
    main = window['window']
    panel = main.workbench.terms_panel
    main.workbench.set_terms_open(True)
    panel.apply('test-job', {'rows': [
        {'source': 'alpha', 'preferred': '甲', 'status': 'provisional'},
        {'source': 'beta', 'preferred': '乙', 'status': 'provisional'},
    ]}, force=True)
    for _ in range(20):
        QApplication.processEvents()
    return panel.grab().toImage()


def _bg(name):  # noqa: F811
    return theme.qcolor(theme.tokens('light')[name]).name()


def test_terms_panel_root_paints_token_background(window):  # noqa: F811
    """A-53: sample the rendered sidebar, not the QSS string. The three blank
    spots around banner/table/action rows must not be black."""
    image = _rendered(window)
    panel = window['window'].workbench.terms_panel
    width = panel.width()
    samples = []
    for x, y in ((20, 40), (width - 12, panel.height() // 2),
                 (width // 2, panel.height() - 8)):
        if 0 <= x < image.width() and 0 <= y < image.height():
            samples.append(image.pixelColor(x, y).name())
    allowed = {_bg('bg-surface'), _bg('bg-page')}
    assert samples and all(color in allowed for color in samples)
    assert '#000000' not in samples


def test_status_dot_colour_is_independent_of_selection(window):  # noqa: F811
    """U-8: the dot icon must declare the same pixmap for every QIcon mode,
    otherwise Qt derives a brown variant for selected rows."""
    panel = window['window'].workbench.terms_panel
    panel.apply('test-job', {'rows': [
        {'source': 'alpha', 'preferred': '甲', 'status': 'provisional'},
        {'source': 'beta', 'preferred': '乙', 'status': 'provisional'},
    ]}, force=True)
    assert panel.table.rowCount() >= 2
    icons = [panel.table.item(row, 2).icon() for row in range(2)]
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
        images = [i.pixmap(QSize(8, 8), mode).toImage() for i in icons]
        assert bytes(images[0].constBits()) == bytes(images[1].constBits())
