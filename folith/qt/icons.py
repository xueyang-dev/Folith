"""线性图标：用 QPainter 画，不引入字体/SVG 依赖，颜色跟随调用方给的 token。

为什么自己画：Web 界面里图标是"看起来像软件"的主要来源之一（文件夹、时钟、加号、
刷新、盾牌…），而 Qt 的 `QStyle.StandardPixmap` 是各平台混搭的原生图标，风格不统一，
跟品牌对不上。这里用同一套 24×24 网格、同一线宽、圆头圆角的描边图标，
颜色由调用方从主题 token 里取，因此三套主题天然一致。

用法：

    from . import icons
    button.setIcon(icons.icon("plus", 16, tokens["text-on-accent"]))
    label.setPixmap(icons.pixmap("folder", 18, tokens["text-secondary"]))
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

#: 24×24 网格上的描边指令：
#:   ("line", x1, y1, x2, y2)                 直线
#:   ("poly", [(x, y), ...], closed)          折线（closed=True 时闭合）
#:   ("circle", cx, cy, r)                    圆
#:   ("rect", x, y, w, h, r)                  圆角矩形
#:   ("arc", cx, cy, r, start_deg, span_deg)  圆弧（Qt 角度：0=3 点方向，逆时针为正）
ICONS: dict[str, list[tuple]] = {
    "plus": [("line", 12, 5, 12, 19), ("line", 5, 12, 19, 12)],
    "search": [("circle", 11, 11, 6.5), ("line", 15.6, 15.6, 20, 20)],
    "folder": [("poly", [(3, 7.5), (9.5, 7.5), (11.5, 10.5), (21, 10.5),
                         (21, 19.5), (3, 19.5)], True)],
    "file-text": [("poly", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)], True),
                  ("poly", [(14, 3), (14, 8), (19, 8)], False),
                  ("line", 9, 13, 16, 13), ("line", 9, 17, 16, 17)],
    # 交付页按文件类型取图标：空页 / 表格 / 数据 / 图片，其余回退 file-text。
    "file": [("poly", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)], True),
             ("poly", [(14, 3), (14, 8), (19, 8)], False)],
    "file-sheet": [("poly", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)], True),
                   ("poly", [(14, 3), (14, 8), (19, 8)], False),
                   ("line", 9, 12.5, 16, 12.5), ("line", 9, 16, 16, 16),
                   ("line", 12.5, 12.5, 12.5, 16), ("line", 9, 19.4, 16, 19.4)],
    "file-code": [("poly", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)], True),
                  ("poly", [(14, 3), (14, 8), (19, 8)], False),
                  ("poly", [(10.6, 12.2), (8.2, 14.8), (10.6, 17.4)], False),
                  ("poly", [(13.4, 12.2), (15.8, 14.8), (13.4, 17.4)], False)],
    "image": [("rect", 3.5, 5.5, 17, 13, 2), ("circle", 8.6, 10.2, 1.6),
              ("poly", [(4.6, 17.6), (10, 12.6), (12.8, 15.2), (15.6, 12.4),
                        (19.4, 16.4)], False)],
    "upload": [("line", 12, 16, 12, 4), ("poly", [(7.5, 8.5), (12, 4), (16.5, 8.5)], False),
               ("poly", [(4, 15), (4, 20), (20, 20), (20, 15)], False)],
    "download": [("line", 12, 4, 12, 16), ("poly", [(7.5, 11.5), (12, 16), (16.5, 11.5)], False),
                 ("poly", [(4, 15), (4, 20), (20, 20), (20, 15)], False)],
    "refresh": [("arc", 12, 12, 8, 40, 280), ("poly", [(17.5, 3.5), (19.5, 9), (14, 10.5)], False)],
    "settings": [("circle", 12, 12, 3.2),
                 ("poly", [(12, 3), (13.6, 5.6), (16.6, 6.0), (18.6, 3.9), (20.1, 5.4),
                           (18.0, 7.4), (18.4, 10.4), (21, 12), (18.4, 13.6),
                           (18.0, 16.6), (20.1, 18.6), (18.6, 20.1), (16.6, 18.0),
                           (13.6, 18.4), (12, 21), (10.4, 18.4), (7.4, 18.0),
                           (5.4, 20.1), (3.9, 18.6), (6.0, 16.6), (5.6, 13.6),
                           (3, 12), (5.6, 10.4), (6.0, 7.4), (3.9, 5.4), (5.4, 3.9),
                           (7.4, 6.0), (10.4, 5.6)], True)],
    "list": [("line", 8, 6, 21, 6), ("line", 8, 12, 21, 12), ("line", 8, 18, 21, 18),
             ("circle", 4.2, 6, 1.1), ("circle", 4.2, 12, 1.1), ("circle", 4.2, 18, 1.1)],
    "chevron-left": [("poly", [(15, 5), (8, 12), (15, 19)], False)],
    "chevron-right": [("poly", [(9, 5), (16, 12), (9, 19)], False)],
    "chevron-down": [("poly", [(5, 9), (12, 16), (19, 9)], False)],
    "chevron-up": [("poly", [(5, 15), (12, 8), (19, 15)], False)],
    "arrow-left": [("line", 20, 12, 4, 12), ("poly", [(10, 6), (4, 12), (10, 18)], False)],
    "arrow-right": [("line", 4, 12, 20, 12), ("poly", [(14, 6), (20, 12), (14, 18)], False)],
    "check": [("poly", [(4, 12.5), (9.5, 18), (20, 6.5)], False)],
    "check-circle": [("circle", 12, 12, 8.5), ("poly", [(8, 12.4), (11, 15.4), (16.4, 9)], False)],
    "alert": [("poly", [(12, 3.5), (21.5, 20), (2.5, 20)], True),
              ("line", 12, 9, 12, 14.5), ("circle", 12, 17.4, 0.9)],
    "info": [("circle", 12, 12, 8.5), ("line", 12, 11, 12, 16.5), ("circle", 12, 7.8, 0.95)],
    "clock": [("circle", 12, 12, 8.5), ("poly", [(12, 7), (12, 12), (16, 14)], False)],
    "save": [("poly", [(4, 4), (16, 4), (20, 8), (20, 20), (4, 20)], True),
             ("poly", [(8, 4), (8, 10), (16, 10), (16, 4)], False),
             ("rect", 8, 14, 8, 6, 1)],
    "copy": [("rect", 8, 8, 12, 12, 2), ("poly", [(16, 8), (16, 4), (4, 4), (4, 16), (8, 16)], False)],
    "shield-check": [("poly", [(12, 3), (20, 6), (20, 12), (12, 21), (4, 12), (4, 6)], True),
                     ("poly", [(8.5, 12), (11, 14.5), (15.5, 9.5)], False)],
    "sparkles": [("poly", [(9, 4), (10.6, 8.4), (15, 10), (10.6, 11.6), (9, 16),
                           (7.4, 11.6), (3, 10), (7.4, 8.4)], True),
                 ("poly", [(17.5, 13), (18.4, 15.6), (21, 16.5), (18.4, 17.4),
                           (17.5, 20), (16.6, 17.4), (14, 16.5), (16.6, 15.6)], True)],
    "eye-off": [("poly", [(3, 3), (21, 21)], False),
                ("poly", [(10.6, 5.4), (12, 5.3), (16.8, 7.0), (20, 10), (21, 12)], False),
                ("poly", [(3, 12), (6.5, 7.4), (10, 5.6)], False),
                ("poly", [(7.5, 14.5), (4, 18)], False), ("poly", [(16.5, 14.5), (20, 18)], False),
                ("circle", 12, 12, 3.2)],
    "package": [("poly", [(12, 3), (21, 8), (21, 16), (12, 21), (3, 16), (3, 8)], True),
                ("poly", [(3, 8), (12, 13), (21, 8)], False), ("line", 12, 13, 12, 21)],
    "external-link": [("poly", [(14, 4), (20, 4), (20, 10)], False),
                      ("line", 20, 4, 11, 13),
                      ("poly", [(18, 14), (18, 20), (4, 20), (4, 6), (10, 6)], False)],
    "x": [("line", 6, 6, 18, 18), ("line", 18, 6, 6, 18)],
    "play": [("poly", [(7, 4.5), (19, 12), (7, 19.5)], True)],
    "pause": [("rect", 7, 5, 3.4, 14, 1), ("rect", 13.6, 5, 3.4, 14, 1)],
    "stop": [("rect", 6, 6, 12, 12, 2)],
    "trash": [("poly", [(4, 7), (20, 7)], False), ("poly", [(9, 7), (9, 4), (15, 4), (15, 7)], False),
              ("poly", [(6, 7), (7, 20), (17, 20), (18, 7)], False),
              ("line", 10.5, 11, 10.5, 16.5), ("line", 13.5, 11, 13.5, 16.5)],
    "translate": [("poly", [(3, 6), (12, 6)], False), ("poly", [(7.5, 4), (7.5, 6)], False),
                  ("poly", [(4.5, 10), (10.5, 10), (7.5, 6)], False),
                  ("poly", [(12, 20), (17, 9), (22, 20)], False),
                  ("line", 14, 17, 20, 17)],
    "eye": [("poly", [(2.5, 12), (6, 7.5), (12, 5.5), (18, 7.5), (21.5, 12)], False),
            ("poly", [(21.5, 12), (18, 16.5), (12, 18.5), (6, 16.5), (2.5, 12)], False),
            ("circle", 12, 12, 3)],
}

#: 线宽按目标尺寸等比缩放（24 网格 → size 像素）
_STROKE = 1.75
_GRID = 24.0


def _painter_path(ops: list[tuple]) -> tuple[QPainterPath, list[QPainterPath]]:
    """把指令拆成"描边路径"与"需要单独填充的圆点"。"""
    path = QPainterPath()
    dots: list[QPainterPath] = []
    for op in ops:
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2 = op
            path.moveTo(x1, y1)
            path.lineTo(x2, y2)
        elif kind == "poly":
            _, points, closed = op
            path.moveTo(*points[0])
            for point in points[1:]:
                path.lineTo(*point)
            if closed:
                path.closeSubpath()
        elif kind == "circle":
            _, cx, cy, radius = op
            if radius <= 1.2:                      # 小圆点：填充更像"实心点"
                dot = QPainterPath()
                dot.addEllipse(QPointF(cx, cy), radius, radius)
                dots.append(dot)
            else:
                path.addEllipse(QPointF(cx, cy), radius, radius)
        elif kind == "rect":
            _, x, y, width, height, radius = op
            path.addRoundedRect(QRectF(x, y, width, height), radius, radius)
        elif kind == "arc":
            _, cx, cy, radius, start, span = op
            box = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            path.arcMoveTo(box, start)
            path.arcTo(box, start, span)
    return path, dots


def pixmap(name: str, size: int = 16, color: str | QColor = "#000000",
           *, stroke: float | None = None) -> QPixmap:
    """画出一个图标。未知名字返回空 pixmap（调用方 `isNull()` 可判断）。"""
    ops = ICONS.get(name)
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    if not ops:
        return pixmap
    path, dots = _painter_path(ops)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    scale = size / _GRID
    painter.scale(scale, scale)
    pen = QPen(QColor(color) if not isinstance(color, QColor) else color)
    width = (stroke if stroke is not None else _STROKE)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    brush = QColor(color) if not isinstance(color, QColor) else color
    painter.setBrush(brush)
    painter.setPen(Qt.PenStyle.NoPen)
    for dot in dots:
        painter.drawPath(dot)
    painter.end()
    return pixmap


def icon(name: str, size: int = 16, color: str | QColor = "#000000") -> QIcon:
    """QIcon 版本（按钮/菜单用）。"""
    return QIcon(pixmap(name, size, color))


def names() -> list[str]:
    return sorted(ICONS)
