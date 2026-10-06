"""D2 app identity: bundle metadata, icon and window title.

Roadmap §5 (D2 macOS row): the beta must carry its own identity — Dock, Finder,
app switcher and the menu bar otherwise show PyInstaller's generic placeholder
and a bare "Python"/"Streamlit" name.
"""
from __future__ import annotations

import json
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
# `docs/brand.md`: the official, user-provided App icon is the brand asset the
# bundle must use.  The legacy `foliothread-app-icon.png` (old set) must not be.
BRAND_ICON = ROOT / "folith" / "resources" / "brand" / "folith-mark-hires.png"
TILE_ICON = ROOT / "folith" / "resources" / "brand" / "folith-app-icon.png"
LEGACY_ICON = ROOT / "folith" / "resources" / "brand" / "foliothread-app-icon.png"


def test_spec_wires_the_app_icon_and_identity_fields():
    spec = (ROOT / "packaging" / "folith-desktop.spec").read_text(encoding="utf-8")
    assert "Folith.icns" in spec, "the bundle must use the built app icon"
    assert '"CFBundleIdentifier": "dev.folith.desktop"' in spec
    assert '"CFBundleShortVersionString": APP_VERSION' in spec
    assert '"LSMinimumSystemVersion"' in spec
    assert '"NSAllowsLocalNetworking"' in spec


def test_build_script_builds_the_icon():
    script = (ROOT / "scripts" / "build_desktop.sh").read_text(encoding="utf-8")
    assert "build_app_icon.py" in script
    assert "Folith.icns" in script


def test_brand_artwork_is_tracked_and_usable():
    """The desktop icon comes from the high-resolution transparent mark."""
    assert BRAND_ICON.is_file(), "the high-resolution brand mark must live in the repository"
    assert TILE_ICON.is_file() and LEGACY_ICON.is_file(), "other brand assets still ship"
    builder = (ROOT / "scripts" / "build_app_icon.py").read_text(encoding="utf-8")
    default = builder.split("DEFAULT_SOURCE")[1].split("\n")[0]
    assert '"folith-mark-hires.png"' in default, "the decided source must be the hires mark"
    assert "folith-app-icon.png" not in default and "foliothread-app-icon.png" not in default


def test_icon_source_is_normalised_to_a_square_canvas(tmp_path):
    """The brand file is a tight non-square mark; .icns frames are square."""
    from build_app_icon import square_master

    target = tmp_path / "master.png"
    record = square_master(BRAND_ICON, target, margin=0.10)
    width, height = record["square_canvas"]
    assert width == height and width > max(record["artwork"])
    assert target.is_file()
    # No stretching: the artwork keeps its own aspect ratio inside the canvas.
    from PIL import Image

    master = Image.open(target).convert("RGBA")
    bbox = master.getchannel("A").getbbox()
    art_w, art_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    source_ratio = record["artwork"][0] / record["artwork"][1]
    assert abs((art_w / art_h) - source_ratio) < 0.02


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
    assert {"icon_16x16.png", "icon_128x128.png", "icon_512x512.png"} <= names


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


def test_window_title_is_the_product_name():
    """`app.py` must keep setting the product title (the native window follows it)."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "page_title=_brand.APP_TITLE_ZH" in source
