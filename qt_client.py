#!/usr/bin/env python3
"""Folith desktop client (Qt Widgets) — source-tree entry point.

The frozen Qt ``.app`` is built from this file (``packaging/folith-qt.spec``) and
its release notes (``docs/qt-beta/release-notes.md``) describe it; testers only
ever double-click the installed application.  The ``folith`` console script and
the ``start.*`` launchers still run the legacy Streamlit launcher (``gui.py``),
kept for one release.  ``dist/qt-desktop/`` holds the packaged build,
``build/qt-desktop/`` its intermediate files; the retired Streamlit desktop shell
keeps its own ``dist/desktop/`` baseline.

Usage:
    venv/bin/python qt_client.py
    venv/bin/python qt_client.py --data-dir /tmp/folith-data   # isolated run
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from folith.qt.app import main  # noqa: E402  (path set up above)

if __name__ == "__main__":
    sys.exit(main())
