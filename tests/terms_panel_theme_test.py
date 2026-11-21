"""U-8: the compact terms sidebar's root must paint a token background, and
the status dot colour must depend only on status (not on row selection)."""
from __future__ import annotations

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
    """A-53/A-54: simulate a light Folith theme on a machine whose system
    palette is near-black (the user's case). The panel root must still paint
    the token background; sampling the rendered sidebar catches a black root."""
    from PySide6.QtGui import QColor, QPalette

    main = window['window']
    main.workbench.set_terms_open(True)
    panel = main.workbench.terms_panel
    panel.apply('test-job', {'rows': [
        {'source': 'alpha', 'preferred': '甲', 'status': 'provisional'},
    ]}, force=True)
    main.theme_preference = 'light'
    main.apply_theme_preference()
    palette = QApplication.instance().palette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#050505'))
    palette.setColor(QPalette.ColorRole.Base, QColor('#050505'))
    QApplication.instance().setPalette(palette)
    panel.setPalette(palette)
    for _ in range(20):
        QApplication.processEvents()
    image = panel.grab().toImage()
    expected = theme.qcolor(theme.tokens('light')['bg-surface']).name()
    for x, y in ((20, 40), (panel.width() // 2, panel.height() - 8)):
        assert image.pixelColor(x, y).name() == expected


def test_selected_status_dot_and_text_match_unselected(window):  # noqa: F811
    """A-54: compare rendered pixels of selected/unselected rows' status
    cells -- selection may change the background, never the dot/text colour."""
    panel = window['window'].workbench.terms_panel
    panel.apply('t', {'rows': [
        {'source': 'a', 'preferred': '甲', 'status': 'provisional'},
        {'source': 'b', 'preferred': '乙', 'status': 'provisional'},
    ]}, force=True)
    panel.table.setCurrentCell(0, 2)
    for _ in range(20):
        QApplication.processEvents()
    image = panel.table.viewport().grab().toImage()
    inks = []
    for row in (0, 1):
        rect = panel.table.visualRect(panel.table.model().index(row, 2))
        counts = {}
        for x in range(rect.left() + 2, rect.left() + 18):
            for y in range(rect.top() + 2, rect.top() + rect.height() - 2):
                color = image.pixelColor(x, y).name()
                counts[color] = counts.get(color, 0) + 1
        # The most frequent non-near-white colour is the status ink.
        inks.append(max(((c, n) for c, n in counts.items()
                         if c not in ('#ffffff', '#f9fafb', '#f4f6f8')),
                        key=lambda item: item[1])[0])
    assert inks[0] == inks[1]
