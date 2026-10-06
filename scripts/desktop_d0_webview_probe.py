#!/usr/bin/env python3
"""D0 verification: drive the *real native WebView* through the frozen app.

Run through the launcher:

    Folith.app/Contents/MacOS/Folith \
        --automation-script scripts/desktop_d0_webview_probe.py \
        --automation-result /tmp/d0-webview.json

Environment (all optional):
    FOLITH_D0_WEBVIEW_JOB      job id to open (adds CAT-editor evidence)
    FOLITH_D0_WEBVIEW_EXPECT   text expected to already be in segment 1
    FOLITH_D0_WEBVIEW_RTL      RTL sample to type into segment 2

This is verification infrastructure, not product surface (roadmap §6.1 requires
real-window evidence for WebSocket/rerun/fragment, long-text editing, IME input
handling and file behaviour).  It drives the page with `window.evaluate_js` and
records observed DOM state; it never injects Folith state directly.

Anything it cannot establish (real OS-level IME composition, the native save
panel for downloads) is reported as `not_verified` rather than silently passed.
"""
from __future__ import annotations

import json
import os
import time

LONG_TEXT = (
    "D0 长文本编辑验证：" + "译页桌面客户端长文本输入测试。" * 60
    + "\nRTL sample: مرحبا بالعالم — mixed 中英阿 text 12345."
)
COMPOSITION_TEXT = "组合输入测试：中文输入法 candidate 候选词"
RTL_TEXT = "نص عربي للتحرير المباشر D0-RTL-2026"


def _js(window, script, retries: int = 1, delay: float = 0.5):
    last = None
    for _ in range(retries):
        try:
            return window.evaluate_js(script)
        except Exception as exc:  # pragma: no cover - WebView timing
            last = exc
            time.sleep(delay)
    raise last


def _wait_for(window, predicate_js: str, timeout: float = 60.0, interval: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if window.evaluate_js(predicate_js):
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _install_error_collector(window) -> None:
    _js(
        window,
        """
        (() => {
          if (!window.__d0_errors) {
            window.__d0_errors = [];
            window.addEventListener('error', e => window.__d0_errors.push(String(e.message)));
            window.addEventListener('unhandledrejection',
              e => window.__d0_errors.push('rejection: ' + String(e.reason)));
          }
          return true;
        })()
        """,
    )


def _set_textarea(window, selector: str, text: str, *, blur: bool = True) -> dict:
    """Set a Streamlit textarea through its native setter and fire real events."""
    return _js(
        window,
        """
        (() => {
          const el = document.querySelector(%s);
          if (!el) return {ok: false, reason: 'textarea-not-found'};
          const proto = Object.getPrototypeOf(el);
          const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
          el.focus();
          setter.call(el, %s);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          %s          return {ok: true, length: el.value.length, value_tail: el.value.slice(-40)};
        })()
        """
        % (json.dumps(selector), json.dumps(text),
           "el.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));"
           "el.dispatchEvent(new Event('blur', { bubbles: true }));" if blur else ""),
    )


def run(window, context):  # noqa: D103
    evidence: dict = {
        "url": context.get("url"),
        "window_title": None,
        "app_root_present": False,
        "app_content_rendered": False,
        "rerun_observed": False,
        "fragment_observed": False,
        "websocket_connected": None,
        "page_errors": [],
        "job_id": os.environ.get("FOLITH_D0_WEBVIEW_JOB") or None,
        "not_verified": [],
    }

    evidence["window_title"] = _js(window, "document.title", retries=10)
    _install_error_collector(window)

    # 1. The Streamlit UI must actually render *content* inside WKWebView.
    evidence["app_root_present"] = _wait_for(
        window,
        "!!document.querySelector('[data-testid=\"stAppViewContainer\"]')",
        timeout=90,
    )
    evidence["app_content_rendered"] = _wait_for(
        window,
        "document.body.innerText.trim().length > 200",
        timeout=180,
        interval=1.0,
    )
    if not evidence["app_content_rendered"]:
        evidence["not_verified"].append("streamlit-content-not-rendered")
        evidence["page_errors"] = _js(window, "window.__d0_errors || []") or []
        evidence["dom_text_sample"] = _js(window, "document.body.innerText.slice(0, 600)")
        return evidence

    evidence["websocket_connected"] = not _js(
        window,
        "document.body.innerText.includes('Connection error')"
        " || document.body.innerText.includes('连接错误')",
    )

    # 2. Rerun: click a real widget and require the rendered DOM to change.
    clicked = _wait_for(
        window,
        """
        (() => {
          const buttons = [...document.querySelectorAll('button')]
            .filter(b => b.innerText && b.innerText.trim().length > 0);
          if (!buttons.length) return false;
          window.__d0_before = document.body.innerText;
          buttons[0].click();
          return true;
        })()
        """,
        timeout=60,
        interval=1.0,
    )
    if clicked:
        evidence["rerun_observed"] = _wait_for(
            window,
            "window.__d0_before !== undefined"
            " && document.body.innerText !== window.__d0_before",
            timeout=45,
            interval=1.0,
        )
    else:
        evidence["not_verified"].append("no-clickable-widget-for-rerun")

    job = evidence["job_id"]
    if not job:
        evidence["not_verified"].extend([
            "no-job-provided-so-cat-editor-not-exercised",
            "os-level-ime-composition-requires-human",
            "native-save-panel-requires-human",
        ])
        evidence["dom_text_sample"] = _js(window, "document.body.innerText.slice(0, 400)")
        return evidence

    # 3. Reopen an existing job inside the native window and read the editor.
    url = f"{context['url']}/?task={job}&section=translation"
    _js(window, f"window.location.href = {json.dumps(url)}")
    editor_ready = _wait_for(
        window,
        f"!!document.querySelector('.st-key-cat_editor_{job}_0 textarea')",
        timeout=180,
        interval=1.0,
    )
    if not editor_ready:
        evidence["not_verified"].append("cat-editor-not-rendered-in-webview")
        evidence["page_errors"] = _js(window, "window.__d0_errors || []") or []
        return evidence

    # Wait for the widget to hydrate from the server before reading its value:
    # Streamlit renders the textarea first and fills it on the next websocket
    # delta, so an immediate read can legitimately be empty.
    _wait_for(
        window,
        f"((document.querySelector('.st-key-cat_editor_{job}_0 textarea') || {{}}).value"
        f" || '').length > 0",
        timeout=120,
        interval=1.0,
    )
    expect = os.environ.get("FOLITH_D0_WEBVIEW_EXPECT")
    current = _js(
        window,
        f"(document.querySelector('.st-key-cat_editor_{job}_0 textarea') || {{}}).value || ''",
    )
    evidence["editor_value_excerpt"] = (current or "")[:120]
    evidence["editor_contains_expected"] = bool(expect and current and expect in current)

    # 4. Long-text editing + commit through the real UI inside WKWebView.
    set_result = _set_textarea(window, f".st-key-cat_editor_{job}_0 textarea", LONG_TEXT)
    evidence["long_text_set"] = set_result
    evidence["long_text_rerun"] = _wait_for(
        window,
        f"document.body.innerText.includes('已保存')"
        f" || !!document.querySelector('.st-key-cat_save_btn_{job}_0 button:not([disabled])')",
        timeout=120,
        interval=1.0,
    )
    save_clicked = _js(
        window,
        f"""
        (() => {{
          const btn = document.querySelector('.st-key-cat_save_btn_{job}_0 button');
          if (!btn) return false;
          if (btn.disabled) return 'disabled';
          btn.click();
          return true;
        }})()
        """,
    )
    evidence["long_text_save_click"] = save_clicked
    evidence["long_text_committed"] = _wait_for(
        window,
        "document.body.innerText.includes('已保存')",
        timeout=120,
        interval=1.0,
    )
    persisted = _js(
        window,
        f"(document.querySelector('.st-key-cat_editor_{job}_0 textarea') || {{}}).value || ''",
    )
    evidence["long_text_persisted_length"] = len(persisted or "")
    evidence["long_text_persisted_matches"] = bool(persisted and persisted.strip() == LONG_TEXT.strip())

    # 5. Composition events (IME shape) must not destroy the value; real OS-level
    #    composition still needs a human at the keyboard.
    evidence["composition_events_roundtrip"] = _js(
        window,
        f"""
        (() => {{
          const el = document.querySelector('.st-key-cat_editor_{job}_1 textarea');
          if (!el) return null;
          el.focus();
          el.dispatchEvent(new CompositionEvent('compositionstart', {{ bubbles: true }}));
          el.dispatchEvent(new CompositionEvent('compositionupdate',
              {{ bubbles: true, data: {json.dumps(COMPOSITION_TEXT)} }}));
          el.dispatchEvent(new CompositionEvent('compositionend',
              {{ bubbles: true, data: {json.dumps(COMPOSITION_TEXT)} }}));
          return el.value.length;
        }})()
        """,
    )

    # 6. RTL editing in the native window (segment 2).
    rtl_set = _set_textarea(window, f".st-key-cat_editor_{job}_2 textarea", RTL_TEXT)
    evidence["rtl_text_set"] = rtl_set
    evidence["rtl_direction"] = _js(
        window,
        f"""
        (() => {{
          const el = document.querySelector('.st-key-cat_editor_{job}_2 textarea');
          if (!el) return null;
          const style = window.getComputedStyle(el);
          return {{direction: style.direction, unicode_bidi: style.unicodeBidi,
                   text_align: style.textAlign}};
        }})()
        """,
    )
    evidence["rtl_save_click"] = _js(
        window,
        f"""
        (() => {{
          const btn = document.querySelector('.st-key-cat_save_btn_{job}_2 button');
          if (!btn) return false;
          if (btn.disabled) return 'disabled';
          btn.click();
          return true;
        }})()
        """,
    )
    evidence["rtl_committed"] = _wait_for(
        window,
        "document.body.innerText.includes('已保存')",
        timeout=120,
        interval=1.0,
    )

    evidence["fragment_observed"] = bool(
        _js(window, "!!document.querySelector('[data-testid*=\"stFragment\"], [class*=\"stFragment\"]')")
    )
    evidence["page_errors"] = _js(window, "window.__d0_errors || []") or []
    evidence["not_verified"].extend([
        "os-level-ime-composition-requires-human",
        "native-save-panel-requires-human",
    ])
    evidence["dom_text_sample"] = _js(window, "document.body.innerText.slice(0, 400)")
    return evidence
