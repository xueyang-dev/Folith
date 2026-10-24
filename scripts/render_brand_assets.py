"""Derive the page icon from the one standard Folith mark.

The single source of truth is ``docs/logo-exploration/folith-mark-final.svg``
(the 2026-10-16 corrected geometry), shipped as
``folith/resources/brand/folith-mark.svg`` and rendered once to
``folith-mark-hires.png`` (1254x1254).  This script derives the remaining mark
bitmap from that master; it never draws a second logo:

    folith-mark.png   256x256 page icon (browser tab, install entry)

The lockups and the app icon have their own derivation scripts
(``extract_brand_png.py``, ``build_app_icon.py``).

Requires Pillow.

    python scripts/render_brand_assets.py          # regenerate
    python scripts/render_brand_assets.py --check  # fail when out of date
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "folith" / "resources" / "brand"
MASTER = BRAND / "folith-mark-hires.png"
PAGE_ICON = BRAND / "folith-mark.png"
PAGE_ICON_SIZE = 256


def render_page_icon() -> Image.Image:
    master = Image.open(MASTER).convert("RGBA")
    return master.resize((PAGE_ICON_SIZE, PAGE_ICON_SIZE), Image.Resampling.LANCZOS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="only verify the page icon matches the master")
    args = parser.parse_args(argv)
    rendered = render_page_icon()
    if args.check:
        current = Image.open(PAGE_ICON).convert("RGBA")
        same = current.size == rendered.size and not any(
            ImageChops.difference(current, rendered).getextrema()[i][1]
            for i in range(4))
        if not same:
            print(f"[fail] {PAGE_ICON.name} is out of date; run "
                  "python scripts/render_brand_assets.py", file=sys.stderr)
            return 1
        print("page icon matches the standard mark")
        return 0
    rendered.save(PAGE_ICON, optimize=True)
    print(f"  {PAGE_ICON.relative_to(ROOT)}  {PAGE_ICON.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
