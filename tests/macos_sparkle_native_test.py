"""Compile and load the real bridge on macOS CI; no signing/upgrade claim."""
import base64
import ctypes
import os
import plistlib
import sys
from pathlib import Path

import pytest

from scripts.embed_macos_updater import embed


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("FOLITH_TEST_SPARKLE_FRAMEWORK"),
                    reason="Requires macOS and the pinned Sparkle framework")
def test_native_sparkle_bridge_compiles_and_loads(tmp_path):
    app = tmp_path / "Folith.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleShortVersionString": "0.8.0", "LSMinimumSystemVersion": "15.0"}, stream)
    embed(app, Path(os.environ["FOLITH_TEST_SPARKLE_FRAMEWORK"]),
          base64.b64encode(b"x" * 32).decode(), "https://example.org/appcast.xml")
    library = ctypes.CDLL(str(contents / "Frameworks/FolithUpdater.dylib"))
    assert library.folith_updater_start
    assert library.folith_updater_check
    assert library.folith_updater_resume
