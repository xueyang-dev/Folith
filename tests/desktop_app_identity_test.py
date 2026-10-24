"""D2 app identity: bundle metadata, icon and window title.

Roadmap §5 (D2 macOS row): the beta must carry its own identity — Dock, Finder,
app switcher and the menu bar otherwise show PyInstaller's generic placeholder
and a bare "Python"/"Streamlit" name.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

BUNDLE = ROOT / "dist" / "desktop" / "Folith.app"
ICNS = ROOT / "build" / "desktop" / "Folith.icns"
# P9: the provided high-resolution mark remains the unmodified brand source.
BRAND_ICON = ROOT / "folith" / "resources" / "brand" / "folith-mark-hires.png"
TILE_ICON = ROOT / "folith" / "resources" / "brand" / "folith-app-icon-white.png"


def test_brand_artwork_is_tracked_and_usable():
    """The desktop icon comes from the high-resolution transparent mark."""
    assert BRAND_ICON.is_file(), "the high-resolution brand mark must live in the repository"
    assert TILE_ICON.is_file(), "the composed white-tile app icon must ship"
    builder = (ROOT / "scripts" / "build_app_icon.py").read_text(encoding="utf-8")
    default = builder.split("DEFAULT_SOURCE")[1].split("\n")[0]
    assert '"folith-mark-hires.png"' in default, "the decided source must be the hires mark"
    assert "app-icon" not in default, "the source must be the mark, not a tile"


def test_icon_source_is_normalised_to_a_square_canvas(tmp_path):
    """P9: white squircle, transparent outer corners, undistorted brand mark."""
    from build_app_icon import square_master
    from PIL import Image, ImageChops

    target = tmp_path / "master.png"
    record = square_master(BRAND_ICON, target)
    assert record["square_canvas"] == (1024, 1024)
    assert record["tile_bounds"] == [100, 100, 924, 924]
    left, top, right, bottom = record["mark_bounds"]
    assert abs((right-left) / (bottom-top) - record["artwork"][0] / record["artwork"][1]) < 0.01
    master = Image.open(target).convert("RGBA")
    assert all(master.getpixel(point)[3] == 0 for point in
               [(0,0), (1023,0), (0,1023), (1023,1023)])
    assert master.getpixel((512,140)) == (255,255,255,255)
    assert master.getpixel((140,512)) == (255,255,255,255)
    # Qt and the bundle must use the same composition; compare decoded pixels
    # rather than PNG encoder metadata or timestamps.
    tracked = Image.open(BRAND_ICON.with_name("folith-app-icon-white.png")).convert("RGBA")
    assert tracked.size == master.size
    assert all(maximum == 0 for minimum, maximum in ImageChops.difference(master, tracked).getextrema())


def test_icon_builder_produces_a_valid_icns(tmp_path):
    from build_app_icon import available, build

    if not available():
        pytest.skip("sips/iconutil unavailable (macOS only)")
    target = tmp_path / "Folith.icns"
    record = build(BRAND_ICON, target)
    assert target.is_file() and target.stat().st_size > 10_000
    assert record["bytes"] == target.stat().st_size
    # Round-trip through iconutil: the file must be a real iconset.
    extracted = tmp_path / "check.iconset"
    result = subprocess.run(["iconutil", "-c", "iconset", str(target), "-o", str(extracted)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    names = {path.name for path in extracted.iterdir()}
    assert {"icon_16x16.png", "icon_128x128.png", "icon_512x512.png", "icon_512x512@2x.png"} <= names
    from PIL import Image
    retina = Image.open(extracted / "icon_512x512@2x.png").convert("RGBA")
    assert retina.size == (1024,1024)
    assert retina.getpixel((0,0))[3] == 0 and retina.getpixel((1023,1023))[3] == 0
    assert retina.getpixel((512,140)) == (255,255,255,255)


def test_built_bundle_carries_its_own_identity():
    """Static check of the packaged artifact when a build is present."""
    if not BUNDLE.is_dir():
        pytest.skip("no built bundle to inspect")
    plist = plistlib.loads((BUNDLE / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleName"] == "Folith"
    assert plist["CFBundleIdentifier"] == "dev.folith.desktop"
    assert plist["CFBundleIconFile"] == "Folith.icns", "must not ship the placeholder icon"
    assert "icon-windowed.icns" not in plist["CFBundleIconFile"]
    icon = BUNDLE / "Contents" / "Resources" / plist["CFBundleIconFile"]
    assert icon.is_file() and icon.stat().st_size > 10_000
    assert not (BUNDLE / "Contents" / "Resources" / "icon-windowed.icns").exists(), \
        "the PyInstaller placeholder icon should be gone"


