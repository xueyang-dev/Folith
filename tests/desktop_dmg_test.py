"""D2 DMG artifact: build script wiring and, when present, the real artifact.

Roadmap §5 (D2 macOS row): the beta is delivered as a DMG with an Applications
drag target.  The heavy end-to-end check (mount + launch from the mount) lives in
`scripts/d2_dmg_runner.py`; these tests keep the wiring honest and cross-check the
recorded artifact.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "build_dmg.sh"
RECORD = ROOT / "build" / "desktop" / "dmg_record.json"
DMG_DIR = ROOT / "dist" / "desktop"


def test_build_script_declares_the_dmg_layout():
    source = SCRIPT.read_text(encoding="utf-8")
    assert SCRIPT.exists() and os.access(SCRIPT, os.X_OK)
    assert "ln -s /Applications" in source, "the drag target is the point of the DMG"
    assert "hdiutil create" in source and "-format UDZO" in source
    assert "hdiutil verify" in source, "the image checksum must be verified"
    assert "Folith-${VERSION}-macos-${ARCH}.dmg" in source


def test_build_script_is_not_silently_unsigned_aware():
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"signed": False' in source, "record the signing state honestly"
    assert "D3" in source, "note where signing / notarisation belongs"


@pytest.mark.skipif(not RECORD.exists(), reason="no DMG has been built here")
def test_dmg_record_matches_the_artifact():
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    artifact = Path(record["artifact"])
    if not artifact.is_file():
        pytest.skip("recorded DMG is not on disk")
    assert artifact.parent == DMG_DIR
    assert artifact.stat().st_size == record["bytes"]
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert digest == record["sha256"]
    assert record["format"] == "UDZO"
    assert record["signed"] is False
    assert record["target_platform"] == "macos"


@pytest.mark.skipif(not (DMG_DIR / "Folith.app").is_dir(), reason="no app bundle here")
def test_dmg_stages_the_same_app_that_was_built():
    """The DMG must carry the built bundle, not a stale copy."""
    app = DMG_DIR / "Folith.app"
    plist = app / "Contents" / "Info.plist"
    assert plist.is_file()
    text = plist.read_text(encoding="utf-8", errors="replace")
    assert "dev.folith.desktop" in text and "Folith.icns" in text
    assert (app / "Contents" / "MacOS" / "Folith").is_file()
