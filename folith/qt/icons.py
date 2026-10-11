"""线性图标：用 QPainter 画，不引入字体/SVG 依赖，**颜色在绘制时才解析**。

为什么自己画：Qt 的 `QStyle.StandardPixmap` 是各平台混搭的原生图标，风格不统一，
跟品牌对不上。这里用同一套 24×24 网格、同一线宽、圆头圆角的描边图标。

**图标不携带颜色，只携带"用哪个语义 token"**：

    button.setIcon(icons.icon("plus", 16, "text-on-accent"))
    nav.setIcon(icons.icon("projects", 18, "accent", duotone=True))

返回的 ``QIcon`` 背后是 :class:`ThemedIconEngine`，每次绘制都向当前主题要这个 token 的
值，所以日间 / 夜间 / 纸面切换、以后的"自定义主题色"都不需要重建任何图标；
禁用态自动换成 ``disabled-ink``。想要固定颜色（极少数场景）传 ``QColor`` 或 ``#hex``。

``duotone=True`` 给闭合形状加一层 16% 透明度的同色底（参考设计稿里"描边 + 浅色填充"
的双色图标）；填充色由描边色派生，因此不需要为每套主题单独配一个"浅色"。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (QColor, QIcon, QIconEngine, QImage, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import QSizePolicy, QWidget


def _as_color(value) -> QColor:
    """token 值 → QColor。走 :func:`folith.qt.theme.qcolor`，因为 token 里存在
    ``rgba(...)`` 形式（``QColor("rgba(...)")`` 会静默变成黑色）。"""
    if isinstance(value, QColor):
        return value
    from .theme import qcolor

    return qcolor(value)


#: 24×24 网格上的描边指令：
#:   ("line", x1, y1, x2, y2)                 直线
#:   ("poly", [(x, y), ...], closed)          折线（closed=True 时闭合）
#:   ("circle", cx, cy, r)                    圆
#:   ("rect", x, y, w, h, r)                  圆角矩形
#:   ("arc", cx, cy, r, start_deg, span_deg)  圆弧（Qt 角度：0=3 点方向，逆时针为正）
#:   ("disc", cx, cy, r)                      实心圆（用主色填充）
#:   ("cut", <以上任一种描边指令>)               在实心形状上"镂空"（用于实心图标里的对勾/三角）
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
    # ---- 导航 / 工作流（设计稿图标表）-------------------------------------------
    "projects": [("rect", 3.5, 5, 11, 15, 2.5), ("rect", 11.5, 9.5, 9, 10.5, 2.5)],
    "workbench": [("rect", 3.5, 4.5, 15, 15, 3.5),
                  ("poly", [(9, 15.2), (9.5, 12), (18, 3.6), (20.4, 6), (12, 14.5)], True)],
    "segments": [("rect", 4, 4, 6.5, 6.5, 2), ("rect", 13.5, 4, 6.5, 6.5, 2),
                 ("rect", 4, 13.5, 6.5, 6.5, 2), ("rect", 13.5, 13.5, 6.5, 6.5, 2)],
    "termbase": [("rect", 5, 3.5, 14, 17, 3.2), ("line", 9, 9, 15, 9),
                 ("line", 9, 13, 15, 13), ("line", 9, 17, 12.5, 17)],
    "memory": [("poly", [(9.5, 16), (6.5, 16), (4, 13.5), (4, 6.5), (6.5, 4),
                         (13.5, 4), (16, 6.5), (16, 9.5)], False),
               ("rect", 9.5, 9.5, 11, 11, 2.5),
               ("line", 12.5, 14, 17.5, 14), ("line", 12.5, 17, 16, 17)],
    "review": [("arc", 12, 12, 8.5, 75, 315), ("poly", [(8, 12.4), (11, 15.4), (16.2, 9)], False),
               ("disc", 18.2, 5.4, 1.1), ("disc", 20.2, 8.2, 0.8)],
    "export": [("line", 12, 15, 12, 4), ("poly", [(7.5, 8.5), (12, 4), (16.5, 8.5)], False),
               ("poly", [(4, 14), (4, 18), (6, 20), (18, 20), (20, 18), (20, 14)], False)],
    "grid": [("rect", 4, 4, 6.5, 6.5, 3.25), ("rect", 13.5, 4, 6.5, 6.5, 3.25),
             ("rect", 4, 13.5, 6.5, 6.5, 3.25), ("rect", 13.5, 13.5, 6.5, 6.5, 3.25)],
    "book": [("poly", [(12, 7), (12, 20)], False),
             ("poly", [(12, 7), (9, 5), (3.5, 5), (3.5, 18), (9, 18), (12, 20)], True),
             ("poly", [(12, 7), (15, 5), (20.5, 5), (20.5, 18), (15, 18), (12, 20)], True)],
    # 译前准备「基础风格」卡片的风格图标（与语言无关的图形，不用汉字块）。
    "graduation": [("poly", [(2, 9.5), (12, 4.5), (22, 9.5), (12, 14.5)], True),
                   ("poly", [(6.5, 12), (6.5, 16.2), (12, 19), (17.5, 16.2), (17.5, 12)], False),
                   ("line", 22, 9.5, 22, 15)],
    "code": [("poly", [(8, 7), (3, 12), (8, 17)], False),
             ("poly", [(16, 7), (21, 12), (16, 17)], False),
             ("line", 13.5, 5, 10.5, 19)],
    "briefcase": [("rect", 3, 7.5, 18, 12.5, 2.5),
                  ("poly", [(9, 7.5), (9, 5), (15, 5), (15, 7.5)], False),
                  ("line", 3, 12.5, 21, 12.5)],
    "feather": [("poly", [(20.5, 3.5), (13, 5), (8, 10), (6.5, 17.5), (14, 16), (19, 11)], True),
                ("line", 6.5, 17.5, 3.5, 20.5), ("line", 9.5, 14.5, 16, 8)],
    "scale": [("line", 12, 4, 12, 20), ("line", 7, 20, 17, 20), ("line", 5, 7, 19, 7),
              ("poly", [(5, 7), (2.5, 13), (7.5, 13)], True),
              ("poly", [(19, 7), (16.5, 13), (21.5, 13)], True)],
    "megaphone": [("poly", [(4, 10), (4, 14), (8, 14), (16, 18.5), (16, 5.5), (8, 10)], True),
                  ("line", 8, 14, 9.5, 19.5), ("arc", 16, 12, 4, -45, 90)],
    "book-open": [("rect", 3.5, 4.5, 17, 15, 2.5), ("line", 12, 4.5, 12, 19.5),
                  ("line", 6.2, 9, 9.4, 9), ("line", 6.2, 12.5, 9.4, 12.5),
                  ("line", 14.6, 9, 17.8, 9), ("line", 14.6, 12.5, 17.8, 12.5)],
    "help": [("circle", 12, 12, 8.5),
             ("poly", [(9.6, 9.6), (10.2, 8.1), (12, 7.6), (13.8, 8.1), (14.4, 9.6),
                       (13.8, 11), (12, 12), (12, 13.3)], False),
             ("disc", 12, 16.4, 0.95)],
    "shield": [("poly", [(12, 3), (20, 6), (20, 12), (12, 21), (4, 12), (4, 6)], True)],
    "pulse": [("line", 4, 11, 4, 13), ("line", 8, 7.5, 8, 16.5), ("line", 12, 4, 12, 20),
              ("line", 16, 8, 16, 16), ("line", 20, 10.5, 20, 13.5)],
    "more": [("disc", 5.5, 12, 1.4), ("disc", 12, 12, 1.4), ("disc", 18.5, 12, 1.4)],
    "dot": [("disc", 12, 12, 5)],
    "plus-circle": [("disc", 12, 12, 9.5), ("cut", "line", 12, 7.5, 12, 16.5),
                    ("cut", "line", 7.5, 12, 16.5, 12)],
    "check-filled": [("disc", 12, 12, 9.5),
                     ("cut", "poly", [(8, 12.4), (11, 15.4), (16.2, 9)], False)],
    "play-filled": [("disc", 12, 12, 9.5), ("cut", "poly", [(10, 8), (16, 12), (10, 16)], True)],
    "arrow-up": [("line", 12, 19, 12, 5), ("poly", [(6.5, 10.5), (12, 5), (17.5, 10.5)], False)],
    "arrow-down": [("line", 12, 5, 12, 19), ("poly", [(6.5, 13.5), (12, 19), (17.5, 13.5)], False)],
    "bell": [("poly", [(6, 16.5), (6, 10.5), (8, 6.5), (12, 5), (16, 6.5), (18, 10.5),
                       (18, 16.5), (19.5, 18), (4.5, 18)], True),
             ("poly", [(10, 20.5), (14, 20.5)], False)],
    "sun": [("circle", 12, 12, 4), ("line", 12, 2.8, 12, 4.8), ("line", 12, 19.2, 12, 21.2),
            ("line", 2.8, 12, 4.8, 12), ("line", 19.2, 12, 21.2, 12),
            ("line", 5.5, 5.5, 6.9, 6.9), ("line", 17.1, 17.1, 18.5, 18.5),
            ("line", 5.5, 18.5, 6.9, 17.1), ("line", 17.1, 6.9, 18.5, 5.5)],
    "moon": [("poly", [(20, 14.5), (18, 15.2), (15.5, 15), (12.8, 13), (11.5, 10), (11.7, 7),
                       (13, 4.2), (10.5, 4.8), (7, 7.5), (5.2, 11.5), (5.8, 15.5),
                       (8.5, 19), (12.5, 20.5), (16.5, 19.7), (19.2, 17.2)], True)],
    "eye": [("poly", [(2.5, 12), (6, 7.5), (12, 5.5), (18, 7.5), (21.5, 12)], False),
            ("poly", [(21.5, 12), (18, 16.5), (12, 18.5), (6, 16.5), (2.5, 12)], False),
            ("circle", 12, 12, 3)],
}

#: 线宽按目标尺寸等比缩放（24 网格 → size 像素）
_STROKE = 1.9
_GRID = 24.0
#: 双色图标的底色透明度（描边色的 alpha）。
DUOTONE_ALPHA = 0.16


def _painter_path(ops: list[tuple]) -> tuple[QPainterPath, QPainterPath, QPainterPath,
                                             QPainterPath, list[QPainterPath]]:
    """把指令拆成：描边路径 / 闭合形状（双色底）/ 实心形状 / 镂空路径。

    返回 ``(stroke, closed, solid, cut, dots)``。
    """
    stroke = QPainterPath()
    closed = QPainterPath()
    solid = QPainterPath()
    cut = QPainterPath()
    dots: list[QPainterPath] = []

    def add(op: tuple, target: QPainterPath, *, fillable: QPainterPath | None) -> None:
        kind = op[0]
        if kind == "line":
            _, x1, y1, x2, y2 = op
            target.moveTo(x1, y1)
            target.lineTo(x2, y2)
        elif kind == "poly":
            _, points, is_closed = op
            shape = QPainterPath()
            shape.moveTo(*points[0])
            for point in points[1:]:
                shape.lineTo(*point)
            if is_closed:
                shape.closeSubpath()
                if fillable is not None:
                    fillable.addPath(shape)
            target.addPath(shape)
        elif kind == "circle":
            _, cx, cy, radius = op
            if radius <= 1.2:                      # 小圆点：填充更像"实心点"
                dot = QPainterPath()
                dot.addEllipse(QPointF(cx, cy), radius, radius)
                dots.append(dot)
            else:
                shape = QPainterPath()
                shape.addEllipse(QPointF(cx, cy), radius, radius)
                target.addPath(shape)
                if fillable is not None:
                    fillable.addPath(shape)
        elif kind == "rect":
            _, x, y, width, height, radius = op
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(x, y, width, height), radius, radius)
            target.addPath(shape)
            if fillable is not None:
                fillable.addPath(shape)
        elif kind == "arc":
            _, cx, cy, radius, start, span = op
            box = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            target.arcMoveTo(box, start)
            target.arcTo(box, start, span)

    for op in ops:
        kind = op[0]
        if kind == "disc":
            _, cx, cy, radius = op
            solid.addEllipse(QPointF(cx, cy), radius, radius)
        elif kind == "cut":
            add(op[1:], cut, fillable=None)
        else:
            add(op, stroke, fillable=closed)
    return stroke, closed, solid, cut, dots


_PATH_CACHE: dict[str, tuple] = {}
_PIXMAP_CACHE: dict[tuple, QPixmap] = {}


def _paths_for(name: str):
    cached = _PATH_CACHE.get(name)
    if cached is None:
        ops = ICONS.get(name)
        cached = _painter_path(ops) if ops else ()
        _PATH_CACHE[name] = cached
    return cached


def render(name: str, width: int, height: int, color: str | QColor, *,
           duotone: bool = False, stroke: float | None = None,
           dpr: float = 1.0) -> QPixmap:
    """把图标画成 ``width×height`` 设备像素的 pixmap（带缓存）。未知名字返回空 pixmap。"""
    qcolor = _as_color(color)
    key = (name, width, height, qcolor.rgba(), duotone, stroke, round(dpr, 2))
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    result = QPixmap(max(1, width), max(1, height))
    result.fill(Qt.GlobalColor.transparent)
    paths = _paths_for(name)
    if paths:
        stroke_path, closed, solid, cut, dots = paths
        image = QImage(result.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(width / _GRID, height / _GRID)
        if duotone and not closed.isEmpty():
            wash = QColor(qcolor)
            wash.setAlphaF(min(1.0, wash.alphaF() * DUOTONE_ALPHA))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(wash)
            painter.drawPath(closed)
        pen = QPen(qcolor)
        pen.setWidthF(stroke if stroke is not None else _STROKE)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(stroke_path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor)
        for dot in dots:
            painter.drawPath(dot)
        painter.drawPath(solid)
        if not cut.isEmpty():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            clear = QPen(Qt.GlobalColor.black)
            clear.setWidthF(stroke if stroke is not None else _STROKE)
            clear.setCapStyle(Qt.PenCapStyle.RoundCap)
            clear.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(clear)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(cut)
        painter.end()
        result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(dpr)
    if len(_PIXMAP_CACHE) > 600:
        _PIXMAP_CACHE.clear()
    _PIXMAP_CACHE[key] = result
    return result


def _token_value(token: str) -> str | None:
    """``token`` 是语义 token 名时，返回它在**当前主题**里的值；否则返回 None。"""
    from . import theme as theme_module
    from .style import current_theme

    return theme_module.tokens(current_theme()).get(token)


def is_token(value) -> bool:
    return isinstance(value, str) and _token_value(value) is not None


def resolve_color(color: str | QColor) -> QColor:
    """token 名 → 当前主题的颜色；``#hex`` / ``QColor`` 原样。"""
    if isinstance(color, str):
        value = _token_value(color)
        if value is not None:
            return _as_color(value)
    return _as_color(color)


class ThemedIconEngine(QIconEngine):
    """每次绘制才解析颜色的图标引擎：主题切换后不需要重建 QIcon。

    ``color_on`` / ``duotone_on`` 对应 ``QIcon.State.On``（可勾选按钮被选中时），
    侧栏导航项因此"选中 = 图标变强调色 + 浅色底"，不需要自己监听 toggled。
    """

    def __init__(self, name: str, color: str | QColor = "text-secondary", *,
                 duotone: bool = False, stroke: float | None = None,
                 color_on: str | QColor | None = None,
                 duotone_on: bool | None = None) -> None:
        super().__init__()
        self._name = name
        self._color = color
        self._duotone = duotone
        self._stroke = stroke
        self._color_on = color_on
        self._duotone_on = duotone_on

    def _look(self, mode: QIcon.Mode, state: QIcon.State) -> tuple[QColor, bool]:
        on = state == QIcon.State.On and self._color_on is not None
        base = self._color_on if on else self._color
        duotone = (self._duotone_on if on and self._duotone_on is not None
                   else self._duotone)
        if mode == QIcon.Mode.Disabled and isinstance(base, str) and is_token(base):
            return resolve_color("disabled-ink"), duotone
        return resolve_color(base), duotone

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        color, duotone = self._look(mode, state)
        return render(self._name, size.width(), size.height(), color,
                      duotone=duotone, stroke=self._stroke)

    def paint(self, painter: QPainter, rect, mode: QIcon.Mode, state: QIcon.State) -> None:
        dpr = 1.0
        device = painter.device()
        if device is not None:
            try:
                dpr = float(device.devicePixelRatio()) or 1.0
            except (AttributeError, TypeError):
                dpr = 1.0
        width = max(1, round(rect.width() * dpr))
        height = max(1, round(rect.height() * dpr))
        color, duotone = self._look(mode, state)
        pm = render(self._name, width, height, color, duotone=duotone,
                    stroke=self._stroke, dpr=dpr)
        painter.drawPixmap(rect, pm)

    def clone(self) -> "ThemedIconEngine":
        return ThemedIconEngine(self._name, self._color, duotone=self._duotone,
                                stroke=self._stroke, color_on=self._color_on,
                                duotone_on=self._duotone_on)

    def key(self) -> str:
        return "folith-themed"


def icon(name: str, size: int = 16, color: str | QColor = "text-secondary", *,
         duotone: bool = False, stroke: float | None = None,
         color_on: str | QColor | None = None,
         duotone_on: bool | None = None) -> QIcon:
    """QIcon（按钮 / 菜单 / 标签页用）。``color`` 优先传 **token 名**，见模块说明。

    ``size`` 只是兼容旧调用；引擎按实际绘制尺寸出图，不受它限制。
    """
    return QIcon(ThemedIconEngine(name, color, duotone=duotone, stroke=stroke,
                                  color_on=color_on, duotone_on=duotone_on))


def pixmap(name: str, size: int = 16, color: str | QColor = "text-secondary",
           *, stroke: float | None = None, duotone: bool = False) -> QPixmap:
    """画出一个**当下**颜色的图标（静态，不随主题更新）。

    只用于"当场就画进别的东西里"的场合（委托 ``paint``、自绘控件的 ``paintEvent``）。
    要放在界面上长期显示的图标，用 :func:`icon` 或 :class:`IconLabel`。
    """
    return render(name, size, size, resolve_color(color), duotone=duotone, stroke=stroke)


class IconLabel(QWidget):
    """显示一个主题图标的小控件（代替 ``QLabel.setPixmap``，随主题自动换色）。"""

    def __init__(self, name: str = "", size: int = 16,
                 color: str | QColor = "text-secondary", *, duotone: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._icon = QIcon()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if name:
            self.set_icon(name, color, duotone=duotone)

    def set_icon(self, name: str, color: str | QColor = "text-secondary", *,
                 duotone: bool = False) -> None:
        self._icon = icon(name, self._size, color, duotone=duotone)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        self._icon.paint(painter, self.rect(), Qt.AlignmentFlag.AlignCenter)
        painter.end()


def names() -> list[str]:
    return sorted(ICONS)
