"""F14 的契约：卡片的"纸"就是布局给它的矩形，左边线不再比同列控件窄 4px。

2026-11-04 UI 审查：卡片曾经在自己的矩形里内缩 ``ELEVATION_PAD`` 画投影，
可见的纸面因此比布局矩形窄（左 4 / 右 4），而语言下拉、分组标题这类不带卡片的
控件画在矩形边缘——准备页同一列出现三条左边线（x=759/764/768）。
现在的约定：

* 纸面 = 控件矩形，只有下方留 `ELEVATION_PAD` 给投影；
* 投影只往**下**画，左右不外扩（外扩会立刻被控件矩形裁掉，留下硬边）；
* 因此同列任何控件的可见左边缘都等于它的 ``x()``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget  # noqa: E402

from folith.qt import style, theme  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    style.apply_theme(instance, "light")
    yield instance


def test_the_paper_is_the_layout_rect(app):
    card = style.Card()
    card.resize(240, 120)
    assert theme.ELEVATION_PAD[:3] == (0, 0, 0), theme.ELEVATION_PAD
    paper = card.paper_rect()
    assert paper.left() == 0 and paper.top() == 0
    assert paper.width() == card.width()
    # 只把下方那一条留白还给投影
    assert paper.height() == card.height() - theme.ELEVATION_PAD[3]


def test_a_card_and_a_plain_control_share_one_left_edge(app):
    """同一列里，带卡片与不带卡片（这里用 QLabel 顶替下拉/说明行）的可见左边缘相同。"""
    column = QWidget()
    column.resize(320, 260)
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    card = style.Card()
    layout.addWidget(card)
    plain = QLabel("同列的不带卡片控件")
    layout.addWidget(plain)
    column.show()
    app.processEvents()
    assert card.x() == plain.x() == 0
    assert card.paper_rect().left() == plain.x()
    column.deleteLater()


def test_the_shadow_only_falls_below_the_card(app):
    """卡片左边一列像素仍是页面底色（没有横向投影），下方才有投影。"""
    page = QColor(theme.tokens("light")["bg-page"])
    host = QWidget()
    host.resize(260, 220)
    host.setStyleSheet(f"background: {page.name()};")
    card = style.Card()
    card.setParent(host)
    card.setGeometry(30, 20, 160, 100)
    host.show()
    app.processEvents()
    image = host.grab().toImage()
    ratio = image.devicePixelRatio()
    left_of_card = image.pixelColor(round((30 - 3) * ratio), round((20 + 50) * ratio))
    assert (left_of_card.red(), left_of_card.green(), left_of_card.blue()) == (
        page.red(), page.green(), page.blue())
    # 纸的下边在 y=20+100-9=111，投影落在它下面、控件矩形之内（裁切边界之前）。
    below_card = image.pixelColor(round((30 + 80) * ratio),
                                  round((20 + 100 - 4) * ratio))
    assert below_card.lightness() < page.lightness(), (
        below_card.name(), page.name())
    host.deleteLater()
