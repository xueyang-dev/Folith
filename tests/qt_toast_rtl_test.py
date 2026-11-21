"""A-61: toast stack centred on the content area in LTR and RTL."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from qt_shell_previews_test import app, window  # noqa: F401,F811


@pytest.mark.parametrize('rtl', [False, True])
@pytest.mark.parametrize('sidebar_open', [False, True])
@pytest.mark.parametrize('count', [1, 2, 3])
def test_toast_centred_on_content_area(window, app, rtl, sidebar_open, count):  # noqa: F811
    main = window['window']
    direction = Qt.LayoutDirection.RightToLeft if rtl else Qt.LayoutDirection.LeftToRight
    QApplication.instance().setLayoutDirection(direction)
    main.setLayoutDirection(direction)
    main.sidebar_clip.setVisible(sidebar_open)
    app.processEvents()
    main.toast.close_all()
    for i in range(count):
        main._notice(f'notice-{i}', task=False)
    app.processEvents()
    sheet = main.sheet
    sheet_rect = sheet.mapTo(main, sheet.rect().topLeft())
    content_center = sheet_rect.x() + sheet.width() / 2
    for toast in main.toast.visible():
        assert abs((toast.x() + toast.width() / 2) - content_center) <= 2
        toast_rect = toast.geometry()
        sidebar_rect = main.sidebar_clip.geometry().translated(
            main.sidebar_clip.mapTo(main, main.sidebar_clip.rect().topLeft()))
        if main.sidebar_clip.isVisibleTo(main):
            assert not toast_rect.intersects(sidebar_rect)
    QApplication.instance().setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    main.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
