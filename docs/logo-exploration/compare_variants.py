"""Side-by-side sheet of the original replica and versions A, B, C, with metrics.

    QT_QPA_PLATFORM=offscreen venv/bin/python docs/logo-exploration/compare_variants.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
VERSIONS = [
    ("原图（复刻）", "folith-mark-replica.svg"),
    ("A 旋转 + 重新居中", "folith-mark-rotated.svg"),
    ("B 两页对称（保留斜切）", "folith-mark-symmetric.svg"),
    ("C 旋转 + 对称", "folith-mark-rotated-symmetric.svg"),
]


def render_alpha(path: Path, size: int = 628) -> np.ndarray:
    img = QImage(size, size, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(path)).render(p, QRectF(0, 0, size, size))
    p.end()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(size, size, 4)
    return arr[..., 3].astype(float) / 255


def metrics(a: np.ndarray) -> dict[str, str]:
    n = a.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    m = a > 0.5
    cx, cy = (a * xx).sum() / a.sum(), (a * yy).sum() / a.sum()
    ys, xs = np.nonzero(m)
    bx, by = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    quads = [m[(yy < by) & (xx < bx)].mean(), m[(yy < by) & (xx >= bx)].mean(),
             m[(yy >= by) & (xx < bx)].mean(), m[(yy >= by) & (xx >= bx)].mean()]
    # Point symmetry: overlap of the silhouette with itself turned 180° about its centroid.
    shift = np.roll(np.roll(m[::-1, ::-1], int(round(2 * cy - (n - 1))), 0), int(round(2 * cx - (n - 1))), 1)
    sym = (m & shift).sum() / (m | shift).sum()
    pct = lambda v: f"{v / n * 100:+.1f}%"
    return {
        "重心偏移（右 / 下）": f"{pct(cx - n / 2)} / {pct(cy - n / 2)}",
        "四象限填充（左上 右上 左下 右下）": " ".join(f"{q * 100:.0f}" for q in quads),
        "四象限最大差": f"{(max(quads) - min(quads)) * 100:.0f} 个百分点",
        "中心对称度": f"{sym * 100:.0f}%",
    }


def superellipse(cx, cy, half, n=4.0, steps=240) -> QPainterPath:
    path = QPainterPath()
    for i in range(steps):
        t = 2 * np.pi * i / steps
        c, s = np.cos(t), np.sin(t)
        pt = QPointF(cx + half * np.sign(c) * abs(c) ** (2 / n), cy + half * np.sign(s) * abs(s) ** (2 / n))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def main() -> None:
    QGuiApplication(sys.argv)
    col_w, pad = 340, 30
    W, H = pad + col_w * len(VERSIONS), 1180
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor("#F2F2F4"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    title, body, small = QFont("PingFang SC", 17, QFont.Weight.DemiBold), QFont("PingFang SC", 13), QFont("PingFang SC", 12)
    for i, (label, name) in enumerate(VERSIONS):
        svg = HERE / name
        x = pad + i * col_w
        p.setFont(title)
        p.setPen(QColor("#1D1D1F"))
        p.drawText(QPointF(x, 46), label)
        # 1) white square with centre guides
        sq = QRectF(x, 70, 300, 300)
        p.fillRect(sq, QColor("#FFFFFF"))
        QSvgRenderer(str(svg)).render(p, sq.adjusted(30, 30, -30, -30))
        p.setPen(QPen(QColor(255, 59, 48, 150), 1))
        p.drawLine(QPointF(sq.center().x(), sq.top()), QPointF(sq.center().x(), sq.bottom()))
        p.drawLine(QPointF(sq.left(), sq.center().y()), QPointF(sq.right(), sq.center().y()))
        # 2) same square, no guides (how it actually reads)
        sq2 = QRectF(x, 390, 300, 300)
        p.fillRect(sq2, QColor("#FFFFFF"))
        QSvgRenderer(str(svg)).render(p, sq2.adjusted(30, 30, -30, -30))
        # 3) app icon tile at 128 / 64 / 32
        ix = x
        for s in (128, 64, 32):
            tile = superellipse(ix + s / 2, 770 + s / 2, s * 0.40)
            p.fillPath(tile, QColor("#FFFFFF"))
            QSvgRenderer(str(svg)).render(p, QRectF(ix + s * 0.17, 770 + s * 0.17, s * 0.66, s * 0.66))
            ix += s + 24
        # 4) metrics
        p.setFont(small)
        y = 940
        for k, v in metrics(render_alpha(svg)).items():
            p.setPen(QColor("#6E6E73"))
            p.drawText(QPointF(x, y), k)
            p.setPen(QColor("#1D1D1F"))
            p.setFont(body)
            p.drawText(QPointF(x, y + 20), v)
            p.setFont(small)
            y += 52
    p.end()
    out = HERE / "variants-compare.png"
    img.save(str(out))
    print(out)


if __name__ == "__main__":
    main()
