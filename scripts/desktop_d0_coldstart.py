#!/usr/bin/env python3
"""D0 cold-start measurement: launch -> first rendered UI -> clean shutdown.

Used by the packager decision record (roadmap §5 asks for cold start alongside
repeated builds, resources and native dependencies).

    /usr/bin/time -p dist/desktop/Folith.app/Contents/MacOS/Folith \
        --automation-script scripts/desktop_d0_coldstart.py \
        --automation-result /tmp/coldstart.json
"""
from __future__ import annotations

import os
import time


def run(window, context):  # noqa: D103
    started = time.monotonic()
    deadline = started + 120
    rendered = False
    while time.monotonic() < deadline:
        try:
            if window.evaluate_js("document.body.innerText.trim().length > 200"):
                rendered = True
                break
        except Exception:
            pass
        time.sleep(0.2)
    first_paint = time.monotonic() - started
    return {
        "first_paint_seconds_after_window": round(first_paint, 2),
        "rendered": rendered,
        "pid": os.getpid(),
        "url": context.get("url"),
        "build_id": context.get("build_id"),
    }
