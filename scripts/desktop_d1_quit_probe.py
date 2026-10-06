#!/usr/bin/env python3
"""D1 quit probe: leave an editor unblurred, then wait for an external quit.

Used for the Cmd+Q / Dock-quit path: the app is launched through LaunchServices
(`open --env ... --args --automation-script ...`), this probe types into a CAT
editor without blurring it, writes a sentinel file so the harness knows the edit
is in flight, and then **waits** (it must not return: returning from the
automation callback terminates the app) until the window goes away.

Environment:
    FOLITH_D1_JOB       job to open
    FOLITH_D1_EDIT      text to type without blurring
    FOLITH_D1_SENTINEL  file to write once the unblurred edit is in place
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _js(window, script, retries: int = 3, delay: float = 0.5):
    last = None
    for _ in range(retries):
        try:
            return window.evaluate_js(script)
        except Exception as exc:  # pragma: no cover - WebView timing
            last = exc
            time.sleep(delay)
    raise last


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


def run(window, context):  # noqa: D103
    job = os.environ.get("FOLITH_D1_JOB") or ""
    edit_text = os.environ.get("FOLITH_D1_EDIT") or "D1 退出路径编辑 CMD-Q"
    sentinel = os.environ.get("FOLITH_D1_SENTINEL") or ""
    evidence: dict = {"job_id": job, "edit_text": edit_text, "mode": "quit-wait"}
    if not job:
        evidence["error"] = "FOLITH_D1_JOB not set"
        return evidence

    # Wait for the first page load to finish before navigating: setting
    # location.href while the initial load is still pending gets overridden.
    _wait_for(window, "document.body.innerText.trim().length > 200", timeout=150,
              interval=0.5)
    url = f"{context['url']}/?task={job}&section=translation"
    _js(window, f"window.location.href = {json.dumps(url)}")
    selector = f".st-key-cat_editor_{job}_0 textarea"
    if not _wait_for(window, f"!!document.querySelector({json.dumps(selector)})", timeout=180):
        evidence["error"] = "cat-editor-not-rendered"
        return evidence
    _wait_for(window,
              f"((document.querySelector({json.dumps(selector)}) || {{}}).value || '').length > 0",
              timeout=120)

    evidence["typed"] = _js(window, """
        (() => {
          const el = document.querySelector(%s);
          if (!el) return { ok: false };
          const setter = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(el), 'value').set;
          el.focus();
          setter.call(el, %s);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          return { ok: true, focused: document.activeElement === el,
                   value_length: el.value.length };
        })()
        """ % (json.dumps(selector), json.dumps(edit_text)))

    if sentinel:
        try:
            Path(sentinel).write_text(json.dumps(evidence, ensure_ascii=False),
                                      encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            evidence["sentinel_error"] = str(exc)

    # Stay alive until the harness quits the app (returning would end the app).
    deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_WAIT", "600"))
    while time.monotonic() < deadline:
        try:
            if window.events.closed.is_set():
                evidence["window_closed"] = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    evidence["exiting"] = True
    return evidence
