#!/usr/bin/env python3
"""Build the macOS app icon (`.icns`) from the tracked brand artwork.

Roadmap §5 (D2 macOS row): the beta must carry its own app identity — Dock,
Finder and the About panel otherwise show PyInstaller's generic placeholder
(`icon-windowed.icns`), which is not an identifiable application.

The source and composed PNGs are tracked; `.icns` remains a build artifact.
Composites the white tile with Pillow, then uses macOS `sips` / `iconutil`.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# P9: retain the provided mark's colours and add the approved white app tile.
DEFAULT_SOURCE = ROOT / "folith" / "resources" / "brand" / "folith-mark-hires.png"
DEFAULT_MARGIN = 100 / 1024

# Complete standard iconset, including the 1024px Retina frame.
ICONSET_FRAMES = (
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
)


def available() -> bool:
    return bool(shutil.which("sips") and shutil.which("iconutil"))


def square_master(source: Path, destination: Path, *, margin: float = DEFAULT_MARGIN) -> dict:
    """Compose the P9 white tile without recolouring the brand artwork.

    The continuous curve is a superellipse (exponent 4), an approximation of
    the macOS squircle, not Apple's proprietary system mask. Only the mask is
    supersampled; the high-resolution mark is downsampled once to keep it crisp.
    """
    from PIL import Image, ImageDraw, ImageFilter

    image = Image.open(source).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit(f"icon source has no visible pixels: {source}")
    artwork = image.crop(bbox)
    side = 1024
    inset = round(side * margin)
    tile = side - 2 * inset
    scale = 4
    mask = Image.new("L", (side * scale, side * scale))
    radius = tile / 2
    points = []
    for index in range(2048):
        angle = math.tau * index / 2048
        c, s = math.cos(angle), math.sin(angle)
        x = side / 2 + radius * math.copysign(abs(c) ** 0.5, c)
        y = side / 2 + radius * math.copysign(abs(s) ** 0.5, s)
        points.append((round(x * scale), round(y * scale)))
    ImageDraw.Draw(mask).polygon(points, fill=255)
    mask = mask.resize((side, side), Image.Resampling.LANCZOS)
    shadow_mask = Image.new("L", (side, side))
    shadow_mask.paste(mask, (0, 10))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(12)).point(lambda a: round(a * 0.09))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_mask)
    canvas.alpha_composite(shadow)
    white = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    white.putalpha(mask)
    canvas.alpha_composite(white)
    mark = artwork.copy()
    mark.thumbnail((round(tile * 0.70), round(tile * 0.70)), Image.Resampling.LANCZOS)
    position = ((side - mark.width) // 2, (side - mark.height) // 2)
    canvas.alpha_composite(mark, position)
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
    return {"source_canvas": image.size, "artwork": artwork.size,
            "square_canvas": canvas.size, "margin": margin,
            "tile_bounds": [inset, inset, side - inset, side - inset],
            "mark_bounds": [*position, position[0] + mark.width, position[1] + mark.height]}


def build(source: Path, destination: Path, *, margin: float = DEFAULT_MARGIN,
          png_out: Path | None = None) -> dict:
    if not available():
        raise SystemExit("sips/iconutil are required (macOS only)")
    if not source.is_file():
        raise SystemExit(f"icon source not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Folith.iconset"
        master = Path(tmp) / "master.png"
        normalised = square_master(source, master, margin=margin)
        if png_out is not None:
            png_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(master, png_out)
        iconset.mkdir()
        for name, size in ICONSET_FRAMES:
            result = subprocess.run(
                ["sips", "-z", str(size), str(size), str(master), "--out", str(iconset / name)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode != 0:
                raise SystemExit(f"sips failed for {name}: {result.stderr.strip()[:200]}")
        convert = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(destination)],
            capture_output=True, text=True, check=False,
        )
        if convert.returncode != 0:
            raise SystemExit(f"iconutil failed: {convert.stderr.strip()[:200]}")
    return {
        "source": str(source),
        "icns": str(destination),
        "bytes": destination.stat().st_size,
        "frames": [{"name": name, "size": size} for name, size in ICONSET_FRAMES],
        "normalised": normalised,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="build the macOS app icon")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--out", default=str(ROOT / "build" / "desktop" / "Folith.icns"))
    parser.add_argument("--png-out", help="also save the composed 1024px PNG for Qt / design review")
    parser.add_argument("--margin", type=float, default=DEFAULT_MARGIN,
                        help="transparent margin around the white tile (default: 100/1024)")
    args = parser.parse_args(argv)
    record = build(Path(args.source), Path(args.out), margin=args.margin,
                   png_out=Path(args.png_out) if args.png_out else None)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
