"""Build the "two pages + seam seal" logo exploration as mask-free SVGs.

The gaps between the pages and around the seal are real cut-outs computed
with QPainterPath boolean operations, so the files render the same in Qt's
SVG Tiny renderer, browsers and design tools.

    QT_QPA_PLATFORM=offscreen venv/bin/python docs/logo-exploration/build_logo.py
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPainter,
                           QPainterPath, QTransform)
from PySide6.QtSvg import QSvgRenderer

OUT = Path(__file__).resolve().parent

NAVY = "#0B1F3B"
BLUE = "#2B5FD1"
SEAL = "#C8372D"
LINE = "#2A3F66"
WHITE = "#FFFFFF"

# Dark-background variant: the navy page would vanish on a dark window.
DARK_FRONT = "#E9EEF8"
DARK_BACK = "#6E97F2"
DARK_LINE = "#BCC8DE"

PAGE_TILT = 6.0      # degrees, the whole page stack leans like the old mark
SEAL_TILT = -9.0     # relative to the pages: a hand-stamped seal is never square
CENTER = QPointF(128, 128)


def rounded(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def centered(cx: float, cy: float, size: float, r: float) -> QPainterPath:
    return rounded(cx - size / 2, cy - size / 2, size, size, r)


def rotate_about(point: QPointF, degrees: float) -> QTransform:
    return (QTransform().translate(point.x(), point.y())
            .rotate(degrees).translate(-point.x(), -point.y()))


def to_d(path: QPainterPath) -> str:
    """Serialise a QPainterPath (lines and cubic curves) as SVG path data."""
    parts: list[str] = []
    i, n = 0, path.elementCount()
    while i < n:
        e = path.elementAt(i)
        if e.type == QPainterPath.ElementType.MoveToElement:
            parts.append(f"M{e.x:.2f} {e.y:.2f}")
            i += 1
        elif e.type == QPainterPath.ElementType.LineToElement:
            parts.append(f"L{e.x:.2f} {e.y:.2f}")
            i += 1
        else:  # CurveToElement + two CurveToDataElement
            c2, end = path.elementAt(i + 1), path.elementAt(i + 2)
            parts.append(f"C{e.x:.2f} {e.y:.2f} {c2.x:.2f} {c2.y:.2f} {end.x:.2f} {end.y:.2f}")
            i += 3
    return " ".join(parts) + "Z"


def mark_shapes(small: bool) -> dict[str, QPainterPath]:
    """All shapes in a 256-unit design space, already tilted."""
    pages = rotate_about(CENTER, PAGE_TILT)
    seal_c = QPointF(108, 166)
    # Qt composes left to right: tilt the seal on its own centre, then with the pages.
    seal_t = rotate_about(seal_c, SEAL_TILT) * pages

    gap = 9.0 if small else 6.0
    seal_size = 56.0 if small else 44.0
    seal_gap = 7.0 if small else 5.0

    back = pages.map(rounded(34, 70, 116, 152, 24))
    front = pages.map(rounded(104, 32, 116, 152, 24))
    front_halo = pages.map(rounded(104 - gap, 32 - gap, 116 + 2 * gap, 152 + 2 * gap, 24 + gap))
    seal = seal_t.map(centered(seal_c.x(), seal_c.y(), seal_size, 6))
    seal_halo = seal_t.map(centered(seal_c.x(), seal_c.y(), seal_size + 2 * seal_gap, 6 + seal_gap))

    shapes = {
        "back": back.subtracted(front_halo).subtracted(seal_halo),
        "front": front.subtracted(seal_halo),
        "seal": seal,
    }
    if not small:
        frame = (centered(seal_c.x(), seal_c.y(), seal_size - 9, 3.5)
                 .subtracted(centered(seal_c.x(), seal_c.y(), seal_size - 12.6, 2)))
        shapes["seal_frame"] = seal_t.map(frame)
        lines = QPainterPath()
        for y, w in ((58, 72), (76, 72), (94, 50)):
            lines.addPath(rounded(126, y, w, 9, 4.5))
        shapes["lines"] = pages.map(lines)
    return shapes


def mark_svg(small: bool = False, dark: bool = False, *, transform: str = "",
             standalone: bool = True) -> str:
    s = mark_shapes(small)
    fills = {
        "back": DARK_BACK if dark else BLUE,
        "front": DARK_FRONT if dark else NAVY,
        "lines": DARK_LINE if dark else LINE,
        "seal": SEAL,
        "seal_frame": WHITE,
    }
    body = "".join(
        f'<path fill="{fills[k]}" fill-rule="evenodd" d="{to_d(s[k])}"/>'
        for k in ("back", "front", "lines", "seal", "seal_frame") if k in s)
    g = f'<g transform="{transform}">{body}</g>' if transform else body
    if not standalone:
        return g
    title = "Folith mark" + (" (small sizes)" if small else "") + (" on dark" if dark else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" '
            f'role="img" aria-label="{title}"><title>{title}</title>{g}</svg>\n')


def mark_bounds(small: bool = False) -> QRectF:
    rect = QRectF()
    for path in mark_shapes(small).values():
        rect = rect.united(path.boundingRect())
    return rect


def superellipse(cx: float, cy: float, half: float, n: float = 4.0, steps: int = 360) -> str:
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + half * math.copysign(abs(c) ** (2 / n), c)
        y = cy + half * math.copysign(abs(s) ** (2 / n), s)
        pts.append(f"{x:.2f} {y:.2f}")
    return "M" + " L".join(pts) + "Z"


def app_icon_svg(small: bool = False) -> str:
    """1024 canvas, 824 white tile (the P9 spec), mark about 70% of the tile."""
    bounds = mark_bounds(small)
    target = 824 * (0.76 if small else 0.66)
    scale = target / max(bounds.width(), bounds.height())
    tx = 512 - bounds.center().x() * scale
    ty = 512 - bounds.center().y() * scale
    mark = mark_svg(small, transform=f"translate({tx:.2f} {ty:.2f}) scale({scale:.4f})",
                    standalone=False)
    tile = superellipse(512, 512, 412)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" '
            'aria-label="Folith app icon"><title>Folith app icon</title>'
            f'<path fill="#E4E6EB" d="{superellipse(512, 518, 412)}"/>'
            f'<path fill="{WHITE}" d="{tile}"/>{mark}</svg>\n')


def write_files() -> dict[str, Path]:
    files = {
        "mark": ("folith-mark-seal.svg", mark_svg()),
        "mark_small": ("folith-mark-seal-small.svg", mark_svg(small=True)),
        "mark_dark": ("folith-mark-seal-dark.svg", mark_svg(dark=True)),
        "app": ("folith-app-icon-seal.svg", app_icon_svg()),
        "app_small": ("folith-app-icon-seal-small.svg", app_icon_svg(small=True)),
    }
    out = {}
    for key, (name, text) in files.items():
        path = OUT / name
        path.write_text(text, encoding="utf-8")
        out[key] = path
    return out


def render(painter: QPainter, svg: Path, x: float, y: float, size: float) -> None:
    QSvgRenderer(str(svg)).render(painter, QRectF(x, y, size, size))


def preview(files: dict[str, Path]) -> Path:
    current = Path(__file__).resolve().parents[2] / "folith/resources/brand/folith-app-icon-white.png"
    sizes = (256, 128, 64, 32, 16)
    img = QImage(1440, 1060, QImage.Format.Format_ARGB32)
    img.fill(QColor("#F5F5F7"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    label = QFont("PingFang SC", 15)
    p.setFont(label)

    def row(y: float, title: str, draw, dark: bool = False) -> None:
        if dark:
            p.fillRect(0, y - 40, img.width(), 330, QColor("#1C1C1E"))
        p.setPen(QColor("#EBEBF5" if dark else "#3E495D"))
        p.drawText(QPointF(40, y - 12), title)
        x = 40.0
        for size in sizes:
            draw(x, y + (256 - size) / 2, size)
            p.drawText(QPointF(x, y + 280), f"{size}px")
            x += size + 64

    def new_icon(x, y, s):
        render(p, files["app_small"] if s <= 32 else files["app"], x, y, s)

    def old_icon(x, y, s):
        p.drawImage(QRectF(x, y, s, s), QImage(str(current)))

    row(60, "新方案 · App 图标（32 px 及以下用简化版）", new_icon)
    row(400, "现在的 App 图标（对照）", old_icon)
    p.fillRect(0, 700, img.width(), 360, QColor("#1C1C1E"))
    p.setPen(QColor("#EBEBF5"))
    p.drawText(QPointF(40, 748), "深色背景 · App 图标与单独图标（深色版）")
    x = 40.0
    for size in (256, 64, 32):
        render(p, files["app_small"] if size <= 32 else files["app"], x, 772 + (256 - size) / 2, size)
        x += size + 64
    render(p, files["mark_dark"], x + 40, 772, 256)
    p.end()
    out = OUT / "preview.png"
    img.save(str(out))
    return out


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    written = write_files()
    print(preview(written))
    for path in written.values():
        print(path)
