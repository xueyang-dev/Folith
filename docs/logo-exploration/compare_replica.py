"""Render the replica next to the original and measure the difference.

    QT_QPA_PLATFORM=offscreen venv/bin/python docs/logo-exploration/compare_replica.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "folith/resources/brand/folith-mark-hires.png"
SVG = HERE / "folith-mark-replica.svg"


def render_svg(size: int) -> Image.Image:
    img = QImage(size, size, QImage.Format.Format_RGBA8888)
    img.fill(QColor(255, 255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(SVG)).render(p, QRectF(0, 0, size, size))
    p.end()
    buf = img.constBits().tobytes()
    return Image.frombytes("RGBA", (size, size), buf).convert("RGB")


def on_white(path: Path) -> Image.Image:
    src = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
    bg.alpha_composite(src)
    return bg.convert("RGB")


def main() -> None:
    QGuiApplication(sys.argv)
    orig = on_white(SRC)
    rep = render_svg(orig.width)
    a, b = np.asarray(orig, float), np.asarray(rep, float)
    diff = np.abs(a - b).mean(axis=2)
    print(f"mean abs diff {diff.mean():.2f} / 255; pixels > 40: {(diff > 40).mean() * 100:.2f}%")
    heat = Image.fromarray(np.clip(diff * 4, 0, 255).astype(np.uint8)).convert("RGB")
    sheet = Image.new("RGB", (orig.width * 3, orig.height), "white")
    for i, im in enumerate((orig, rep, heat)):
        sheet.paste(im, (i * orig.width, 0))
    sheet = sheet.resize((sheet.width // 3, sheet.height // 3), Image.LANCZOS)
    out = HERE / "replica-compare.png"
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
