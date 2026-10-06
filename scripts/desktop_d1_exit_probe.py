#!/usr/bin/env python3
"""D1 save & stop probe: leave an editor unblurred, then close the window.

Run through the desktop launcher:

    FOLITH_D1_JOB=<job> FOLITH_D1_EDIT="<text>" \
    dist/desktop/Folith.app/Contents/MacOS/Folith \
        --automation-script scripts/desktop_d1_exit_probe.py \
        --automation-result /tmp/d1-exit.json

The probe types into a CAT editor **without blurring it** (so the value lives
only in the WebView, exactly the roadmap §3.3 "尚未失焦的 textarea" case),
records that no draft exists on disk yet, and then closes the window.  The
launcher's exit flow must flush, commit and verify the write before the app
exits; the caller checks the on-disk result afterwards.
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


def _drafts_file(data_dir: Path, job: str) -> Path:
    return Path(data_dir) / job / "translation_drafts.json"


def _drafts(data_dir: Path, job: str) -> list[dict]:
    path = _drafts_file(data_dir, job)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("drafts") if isinstance(payload, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def _segment_target(data_dir: Path, job: str, index: int) -> str:
    try:
        state = json.loads((Path(data_dir) / job / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    pairs = state.get("pairs") or []
    if 0 <= index < len(pairs) and isinstance(pairs[index], dict):
        return str(pairs[index].get("target") or "")
    return ""


def run(window, context):  # noqa: D103
    job = os.environ.get("FOLITH_D1_JOB") or ""
    edit_text = os.environ.get("FOLITH_D1_EDIT") or (
    "近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
    "本段由未失焦编辑标记二〇二六写入，用于验证保存并停止。")
    data_dir = Path(context["data_dir"])
    evidence: dict = {"job_id": job, "edit_text": edit_text, "steps": {}}
    if not job:
        evidence["error"] = "FOLITH_D1_JOB not set"
        return evidence

    def step(name, value):
        evidence["steps"][name] = value
        return value

    step("target_before", _segment_target(data_dir, job, 0))
    step("drafts_before", len(_drafts(data_dir, job)))

    # Open the job's translation workspace inside the real window.
    url = f"{context['url']}/?task={job}&section=translation"
    _js(window, f"window.location.href = {json.dumps(url)}")
    selector = f".st-key-cat_editor_{job}_0 textarea"
    if not _wait_for(window, f"!!document.querySelector({json.dumps(selector)})", timeout=180):
        evidence["error"] = "cat-editor-not-rendered"
        return evidence
    # Wait for the widget to hydrate from the server before typing into it.
    _wait_for(window,
              f"((document.querySelector({json.dumps(selector)}) || {{}}).value || '').length > 0",
              timeout=120)

    # Type WITHOUT blurring: value stays in the WebView, no draft on the server.
    typed = _js(window, """
        (() => {
          const el = document.querySelector(%s);
          if (!el) return { ok: false };
          const setter = Object.getOwnPropertyDescriptor(
            Object.getPrototypeOf(el), 'value').set;
          el.focus();
          setter.call(el, %s);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          return {
            ok: true,
            focused: document.activeElement === el,
            value_length: el.value.length,
            value_tail: el.value.slice(-32),
          };
        })()
        """ % (json.dumps(selector), json.dumps(edit_text)))
    step("typed_without_blur", typed)

    # Give Streamlit a moment; a draft must NOT exist yet (that is the point).
    time.sleep(3)
    step("drafts_after_typing", len(_drafts(data_dir, job)))

    # Trigger the *same* path the OS close button uses.  `window.destroy()`
    # calls NSWindow.close directly and bypasses the delegate, so it would not
    # exercise the exit flow (verified in pywebview/platforms/cocoa.py:758).
    evidence["closing"] = True
    try:
        cancelled = bool(window.events.closing.set())
        evidence["closing_cancelled"] = cancelled
        if not cancelled:
            # No handler registered (or it allowed the close): close directly.
            window.destroy()
    except Exception as exc:  # pragma: no cover - defensive
        evidence["closing_error"] = f"{type(exc).__name__}: {exc}"
        try:
            window.destroy()
        except Exception:  # pragma: no cover - defensive
            pass
    # Give the exit flow a moment to flush/commit before this script returns.
    time.sleep(2)
    step("drafts_after_close_request", len(_drafts(data_dir, job)))
    return evidence
