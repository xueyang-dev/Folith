#!/usr/bin/env python3
"""Build the macOS app icon (`.icns`) from the tracked brand artwork.

Roadmap §5 (D2 macOS row): the beta must carry its own app identity — Dock,
Finder and the About panel otherwise show PyInstaller's generic placeholder
(`icon-windowed.icns`), which is not an identifiable application.

The source PNG is tracked in the repository; the `.icns` is a build artifact, so
no binary icon is committed.  Uses only macOS built-ins (`sips`, `iconutil`).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Product-owner decision (2026-10-03): the desktop/Dock icon uses the brand's
# transparent mark, rendered from the user-provided high-resolution master
# (`folith-mark-hires.png`, 1254×1254).  The lower-resolution `folith-app-icon.png`
# (273×257, navy tile) and the legacy `foliothread-app-icon.png` (old set) are NOT
# used for the bundle.
DEFAULT_SOURCE = ROOT / "folith" / "resources" / "brand" / "folith-mark-hires.png"

# (file name, pixel size).  The 1024 frame is omitted: the tracked artwork is
# 512px, and upscaling it would only add a soft frame.
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
)


def available() -> bool:
    return bool(shutil.which("sips") and shutil.which("iconutil"))


def square_master(source: Path, destination: Path, *, margin: float = 0.10) -> dict:
    """Normalise the user-provided artwork onto a square canvas.

    The brand file is a tight, transparent mark on a non-square canvas.  macOS
    `.icns` frames are square, so the artwork is centred on a square canvas with a
    margin (matching the macOS icon grid) — never stretched, cropped or recoloured.
    """
    from PIL import Image

    image = Image.open(source).convert("RGBA")
    bbox = image.getchannel("A").getbbox()
    if bbox is None:
        raise SystemExit(f"icon source has no visible pixels: {source}")
    artwork = image.crop(bbox)
    side = max(artwork.size)
    canvas_side = int(round(side / (1 - 2 * margin)))
    canvas = Image.new("RGBA", (canvas_side, canvas_side), (0, 0, 0, 0))
    canvas.paste(artwork, ((canvas_side - artwork.width) // 2,
                           (canvas_side - artwork.height) // 2))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination)
    return {"source_canvas": image.size, "artwork": artwork.size,
            "square_canvas": canvas.size, "margin": margin}


def build(source: Path, destination: Path, *, margin: float = 0.10) -> dict:
    if not available():
        raise SystemExit("sips/iconutil are required (macOS only)")
    if not source.is_file():
        raise SystemExit(f"icon source not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Folith.iconset"
        master = Path(tmp) / "master.png"
        normalised = square_master(source, master, margin=margin)
        source = master
        iconset.mkdir()
        for name, size in ICONSET_FRAMES:
            result = subprocess.run(
                ["sips", "-z", str(size), str(size), str(source), "--out", str(iconset / name)],
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
    parser.add_argument("--margin", type=float, default=0.10,
                        help="transparent margin around the artwork (macOS icon grid)")
    args = parser.parse_args(argv)
    record = build(Path(args.source), Path(args.out), margin=args.margin)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
