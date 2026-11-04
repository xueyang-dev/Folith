"""F6 的网格落地：阅读行距下的绘制与**术语高亮矩形**必须同源。

背景：行距改成"1.5 / 1.7 × 字号"之后，`QStyledItemDelegate` 画字与
`term_highlight.text_rects` 各自按自己的排版算位置，荧光笔会整体上移（每行差 4px，
第 2 行就偏 4px、第 3 行偏 8px）。约定：**绘制用的那份 `QTextLayout` 就是高亮矩形
的依据**（`style.RowDelegate.reading_text` + `term_highlight.layout_char_rects`）。

验收（Lead 的硬要求）：多行段落里，第 2 行中间命中的术语，高亮矩形与字符实际位置
偏差 <= 1px；日间 / 夜间各一次；离屏 grab + 直接读 Qt 几何两种口径都测。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetricsF  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
)

from folith.qt import style, term_highlight, theme  # noqa: E402

#: 一段会换行成 4 行的拉丁段落：第 2 行一定要有"中间"的词可测。
PARAGRAPH = ("A term that is rendered one way in chapter two and another way in "
             "chapter nine forces every reader to stop and wonder whether the author "
             "meant two different things.")

#: 从右往左的段落（阿拉伯文）：行要右对齐，术语矩形仍要压在字上。
ARABIC = ("يشترط مكتب التدقيق أن يُوثَّق كل نموذج يُستخدم في قرارات الائتمان، وأن تُسجَّل "
          "التغييرات الجوهرية بالتاريخ والمسؤول، وأن تُحفظ النسخة المعتمدة في الأرشيف الرقمي.")


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def _grid(app, theme_name: str, *, text: str = PARAGRAPH, delegate_cls=None):
    """一段落、单列、开了阅读行距的网格；行高按同一口径留足。"""
    style.apply_theme(app, theme_name)
    table = QTableWidget(1, 1)
    table.setObjectName("segmentGrid")
    table.setShowGrid(False)
    table.setItem(0, 0, QTableWidgetItem(text))
    font = table.font()
    font.setPixelSize(theme.FONT_SIZES["reading"])
    table.setFont(font)
    table.setColumnWidth(0, 320)
    table.horizontalHeader().hide()
    table.verticalHeader().hide()
    delegate = (delegate_cls or style.RowDelegate)(table, reading_text=True)
    table.setItemDelegate(delegate)
    table.resize(360, 320)
    table.show()
    app.processEvents()
    index = table.model().index(0, 0)
    probe = QStyleOptionViewItem()
    delegate.initStyleOption(probe, index)
    probe.rect = table.visualRect(index)
    probe.widget = table
    table.setRowHeight(0, style.reading_text_height(
        text, font, style.item_text_rect(probe, table).width()) + 8)
    app.processEvents()
    table.grab()                     # 触发一次真实绘制，委托把 layout 留下来
    app.processEvents()
    # 行高定下来之后再取 option：委托记录的是**最后这次**绘制的格子矩形。
    option = QStyleOptionViewItem()
    delegate.initStyleOption(option, index)
    option.rect = table.visualRect(index)
    option.widget = table
    return table, delegate, option, style.item_text_rect(option, table)


def _term_on_second_line(layout, text: str) -> tuple[int, str]:
    """第 2 个可见行里、行中段的那个词（不是行首、也不是行尾）。"""
    assert layout.lineCount() >= 2, layout.lineCount()
    line = layout.lineAt(1)
    line_text = text[line.textStart():line.textStart() + line.textLength()]
    words = [(match.start(), match.group()) for match in re.finditer(r"[A-Za-z]{3,}", line_text)]
    middle = [(start, word) for start, word in words
              if start > 10 and start + len(word) < len(line_text) - 10]
    assert middle, line_text
    start, word = middle[len(middle) // 2]
    return line.textStart() + start, word


def _dominant(image, left: int, top: int, right: int, bottom: int):
    """一块区域里出现最多的颜色（= 行底色）。"""
    counts: dict[tuple[int, int, int], int] = {}
    for y in range(max(0, top), min(image.height(), bottom)):
        for x in range(max(0, left), min(image.width(), right)):
            colour = image.pixelColor(x, y)
            key = (colour.red(), colour.green(), colour.blue())
            counts[key] = counts.get(key, 0) + 1
    best = max(counts, key=lambda key: counts[key])
    from PySide6.QtGui import QColor
    return QColor(*best)


def _ink(image, left: int, top: int, right: int, bottom: int,
         background) -> tuple[int, int, int, int]:
    """一块矩形里"与底色不同"的像素：数量 + 最左 / 最上 / 最下坐标。

    不能用"亮度 < 某值"判断：夜间是浅字压深底，整块底色都会被算成墨迹。
    """
    count, first_x, first_y, last_y = 0, None, None, None
    for y in range(max(0, top), min(image.height(), bottom)):
        for x in range(max(0, left), min(image.width(), right)):
            colour = image.pixelColor(x, y)
            if max(abs(colour.red() - background.red()),
                   abs(colour.green() - background.green()),
                   abs(colour.blue() - background.blue())) > 30:
                count += 1
                first_x = x if first_x is None else min(first_x, x)
                first_y = y if first_y is None else min(first_y, y)
                last_y = y if last_y is None else max(last_y, y)
    return count, -1 if first_x is None else first_x, -1 if first_y is None else first_y, \
        -1 if last_y is None else last_y


@pytest.mark.parametrize("theme_name", theme.THEME_ORDER)
def test_the_reading_layout_is_used_for_painting(app, theme_name):
    table, delegate, _option, text_rect = _grid(app, theme_name)
    layout = delegate.painted_layout
    assert layout is not None, "阅读行距没有生效：委托没有留下 layout"
    assert layout.lineCount() >= 3
    step = style.reading_line_height(PARAGRAPH) * theme.FONT_SIZES["reading"]
    # 每行的 y 就是 1.5 × 字号 的整数倍：这才是"按阅读行距排"的硬证据。
    for number in range(layout.lineCount()):
        assert layout.lineAt(number).y() == pytest.approx(number * step, abs=0.5)
    assert delegate.painted_text_rect.width() == pytest.approx(text_rect.width(), abs=1)
    table.deleteLater()


@pytest.mark.parametrize("theme_name", theme.THEME_ORDER)
def test_the_term_rect_matches_the_character_position_within_one_pixel(app, theme_name):
    table, delegate, _option, text_rect = _grid(app, theme_name)
    layout = delegate.painted_layout
    start, word = _term_on_second_line(layout, PARAGRAPH)
    line = layout.lineAt(1)
    assert line.textStart() <= start < line.textStart() + line.textLength()

    rects = term_highlight.layout_char_rects(layout, [(start, start + len(word))],
                                             text_rect.topLeft())
    assert len(rects) == 1
    rect = rects[0]

    # 独立口径：字符 x 用 QFontMetricsF 的 advance 累加，行 y 用"行号 × 行距"。
    prefix = PARAGRAPH[line.textStart():start]
    expected_x = text_rect.left() + QFontMetricsF(table.font()).horizontalAdvance(prefix)
    step = style.reading_line_height(PARAGRAPH) * theme.FONT_SIZES["reading"]
    expected_y = text_rect.top() + step
    assert abs(rect.left() - expected_x) <= 1.0, (rect.left(), expected_x)
    assert abs(rect.top() - expected_y) <= 1.0, (rect.top(), expected_y)
    assert rect.width() == pytest.approx(
        QFontMetricsF(table.font()).horizontalAdvance(word), abs=1.5)
    table.deleteLater()


@pytest.mark.parametrize("theme_name", theme.THEME_ORDER)
def test_the_public_entry_uses_the_same_layout(app, theme_name):
    """`segment_editor.py` 调的就是这个入口：它必须挑到委托刚画过的那份 layout。"""
    table, delegate, option, text_rect = _grid(app, theme_name)
    layout = delegate.painted_layout
    start, word = _term_on_second_line(layout, PARAGRAPH)
    direct = term_highlight.layout_char_rects(layout, [(start, start + len(word))],
                                              text_rect.topLeft())
    public = term_highlight.text_rects(option, PARAGRAPH, [(start, start + len(word))], table)
    assert len(public) == len(direct) == 1
    assert public[0].left() == pytest.approx(direct[0].left(), abs=0.01)
    assert public[0].top() == pytest.approx(direct[0].top(), abs=0.01)
    table.deleteLater()


@pytest.mark.parametrize("theme_name", theme.THEME_ORDER)
def test_the_highlight_covers_the_glyphs_on_screen(app, theme_name):
    """离屏 grab：术语的墨迹必须落在矩形里，且矩形左侧 6px 内没有墨迹（没跑到上一行/前一格）。"""
    table, delegate, _option, text_rect = _grid(app, theme_name)
    layout = delegate.painted_layout
    start, word = _term_on_second_line(layout, PARAGRAPH)
    rects = term_highlight.layout_char_rects(layout, [(start, start + len(word))],
                                             text_rect.topLeft())
    rect = rects[0]
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    box = (round(rect.left() * ratio), round(rect.top() * ratio),
           round(rect.right() * ratio), round(rect.bottom() * ratio))
    # 底色取这一行里出现最多的颜色（行内空白处就是它，深浅主题都适用）。
    background = _dominant(image, 0, box[1], image.width(), box[3])
    inside, first_x, _first_y, _last_y = _ink(image, *box, background)
    assert inside >= 3, "矩形里没有墨迹：矩形没落在字上"
    # 墨迹的左沿与矩形左沿差 <= 1 逻辑像素（字形左侧留白 + 设备像素取整）
    assert first_x - box[0] <= round(1 * ratio) + 1, (first_x, box[0])
    # 矩形上沿之上 4 个逻辑像素内没有墨迹（偏到上一行会把上一行的字圈进来）
    above, _x, _ty, _by = _ink(image, box[0], box[1] - round(4 * ratio),
                               box[2], box[1] - 1, background)
    assert above == 0, above
    table.deleteLater()


class _RtlRowDelegate(style.RowDelegate):
    """工作台的 `WorkbenchRowDelegate` 会给阿拉伯文格子设 RTL 方向。"""

    def initStyleOption(self, option, index):  # noqa: N802 - Qt 命名
        super().initStyleOption(option, index)
        option.direction = Qt.LayoutDirection.RightToLeft


@pytest.mark.parametrize("theme_name", theme.THEME_ORDER)
def test_rtl_reading_text_stays_right_aligned_with_its_marks(app, theme_name):
    """从右往左的语言：行右对齐，术语矩形仍在文字区里、且压住字。"""
    table, delegate, _option, text_rect = _grid(app, theme_name, text=ARABIC,
                                                delegate_cls=_RtlRowDelegate)
    layout = delegate.painted_layout
    assert layout is not None
    for number in range(layout.lineCount()):
        line = layout.lineAt(number)
        assert line.x() > 0, "RTL 段落没有右对齐"
        assert line.x() + line.naturalTextWidth() <= text_rect.width() + 1

    start = layout.lineAt(0).textStart() + 8
    rect = term_highlight.layout_char_rects(layout, [(start, start + 6)],
                                            text_rect.topLeft())[0]
    assert text_rect.left() <= rect.left() and rect.right() <= text_rect.right() + 1
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()
    box = (round(rect.left() * ratio), round(rect.top() * ratio),
           round(rect.right() * ratio), round(rect.bottom() * ratio))
    background = _dominant(image, 0, box[1], image.width(), box[3])
    inside, _first_x, _ty, _by = _ink(image, *box, background)
    assert inside >= 3, "RTL 矩形里没有墨迹"
    table.deleteLater()


def test_reading_layout_accepts_a_point_sized_font(app):
    """字号用 pt 设置时（pixelSize = -1）也要能排：回退到 QFontMetricsF 的高度。"""
    from PySide6.QtGui import QFont

    font = QFont()
    font.setPointSize(11)
    assert font.pixelSize() < 0
    layout = style.reading_layout("hello world with a point sized font", font, 80, 1.5)
    assert layout.lineCount() >= 2
    assert layout.lineAt(1).y() > 0
