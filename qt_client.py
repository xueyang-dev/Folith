#!/usr/bin/env python3
"""Folith desktop client (Qt Widgets) — source-tree entry point.

This is the default entry for developers and for the frozen ``.app``; testers
only ever double-click the installed application.  ``dist/qt-desktop/`` holds
the packaged build, ``build/qt-desktop/`` its intermediate files; the retired
Streamlit shell keeps its own ``dist/desktop/`` baseline.

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
