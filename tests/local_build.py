"""Whether the Qt build on disk is a build of the commit under test.

Several release tests compare committed documents against ``build/qt-desktop``
records.  CI builds first and then tests, so the records always describe HEAD.
On a developer machine ``build/`` usually holds an older (or dirty) build, and
those comparisons fail on every commit for reasons unrelated to the change.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_INFO = ROOT / "build" / "qt-desktop" / "desktop_build_info.json"


def stale_reason() -> str:
    """Empty when the local Qt build is a clean build of HEAD; otherwise why not."""
    try:
        info = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "no Qt build info on disk"
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "cannot read git HEAD"
    built = str(info.get("build_id") or "")
    if built != f"git:{head}":
        return f"local Qt build is {built or 'unknown'}, HEAD is git:{head}"
    if info.get("worktree_dirty"):
        return f"local Qt build of git:{head} was made from a dirty worktree"
    return ""


def skip_unless_fresh(pytest) -> None:
    reason = stale_reason()
    if reason:
        pytest.skip(f"needs a clean Qt build of HEAD ({reason}); CI builds before testing")
