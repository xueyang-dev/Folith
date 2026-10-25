"""macOS-only Sparkle adapter. Importing the shared UI never loads Cocoa."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QTimer


class SparkleUpdater:
    def __init__(self, restart):
        if sys.platform != "darwin" or not getattr(sys, "frozen", False):
            raise RuntimeError("Sparkle requires a packaged macOS application")
        contents = Path(sys.executable).resolve().parent.parent
        self.library = ctypes.CDLL(str(contents / "Frameworks" / "FolithUpdater.dylib"))
        # Retain callback and CDLL for the complete application lifetime.
        # Leave the native delegate stack before entering Qt's close flow.
        self.callback = ctypes.CFUNCTYPE(None)(lambda: QTimer.singleShot(0, restart))
        self.library.folith_updater_start.argtypes = [ctypes.CFUNCTYPE(None)]
        self.library.folith_updater_start.restype = ctypes.c_int
        self.library.folith_updater_check.argtypes = []
        self.library.folith_updater_check.restype = None
        self.library.folith_updater_resume.argtypes = []
        self.library.folith_updater_resume.restype = None
        if not self.library.folith_updater_start(self.callback):
            raise RuntimeError("Sparkle could not start; check the signed appcast configuration")

    def check(self):
        self.library.folith_updater_check()

    def resume(self):
        self.library.folith_updater_resume()
