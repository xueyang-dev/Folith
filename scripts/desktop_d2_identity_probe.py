#!/usr/bin/env python3
"""D2 app-identity probe: report the rendered window title, then stay alive so
the harness can query LaunchServices (Dock / menu / app switcher identity)."""
from __future__ import annotations

import json
import os
import time


def _wait_for(window, predicate_js: str, timeout: float = 120.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if window.evaluate_js(predicate_js):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _menu_facts() -> dict:
    """Menu bar structure and key equivalents (Cmd+C/V/A/Q), from AppKit."""
    try:
        from AppKit import NSApp

        menu = NSApp.mainMenu()
        if menu is None:
            return {"available": False, "reason": "no main menu"}
        top = []
        for index in range(menu.numberOfItems()):
            item = menu.itemAtIndex_(index)
            title = str(item.title())
            entry = {"title": title, "items": []}
            submenu = item.submenu()
            if submenu is not None:
                for sub_index in range(submenu.numberOfItems()):
                    sub = submenu.itemAtIndex_(sub_index)
                    entry["items"].append({
                        "title": str(sub.title()),
                        "key": str(sub.keyEquivalent() or ""),
                    })
            top.append(entry)
        return {"available": True, "top_level": [item["title"] for item in top],
                "menus": top}
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def run(window, context):  # noqa: D103
    rendered = _wait_for(window, "document.body.innerText.trim().length > 200", timeout=150)
    evidence = {
        "rendered": rendered,
        "document_title": None,
        "url": context.get("url"),
        "pid": os.getpid(),
        "hold_seconds": float(os.environ.get("FOLITH_D2_HOLD", "18")),
    }
    if rendered:
        try:
            evidence["document_title"] = window.evaluate_js("document.title")
            evidence["body_sample"] = window.evaluate_js(
                "document.body.innerText.slice(0, 80)")
        except Exception as exc:  # pragma: no cover - defensive
            evidence["title_error"] = str(exc)
    evidence["menu"] = _menu_facts()
    if os.environ.get("FOLITH_D2_RESULT"):
        try:
            with open(os.environ["FOLITH_D2_RESULT"], "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, ensure_ascii=False, indent=2)
        except OSError:
            pass
    time.sleep(evidence["hold_seconds"])
    return evidence
