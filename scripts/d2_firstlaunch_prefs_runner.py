#!/usr/bin/env python3
"""D2 first-launch and preference-persistence verification.

Two things an ordinary user hits that earlier checks did not cover:

1. **First launch with no data directory**: the app must create its data/log
   directories itself (roadmap §3.2) and still serve the UI;
2. **Preferences survive a restart**: the UI language is stored in
   `<data dir>/ui_preferences.json`, so a restart must come back in the chosen
   language without any user action.

Usage:
    venv/bin/python scripts/d2_firstlaunch_prefs_runner.py --out /tmp/d2-first \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright,
)


def _probe(node: str, url: str, out_dir: Path, result: Path, env: dict,
           *, read_only: bool = False, keep: bool = False) -> dict:
    command = [node, str(ROOT / "scripts" / "desktop_ui_language.js"),
               "--url", url, "--out-dir", str(out_dir), "--result", str(result)]
    if read_only:
        command.append("--read-only")
    if keep:
        command.append("--keep")
    run = subprocess.run(command, capture_output=True, text=True, env=env,
                         timeout=1800, check=False)
    try:
        payload = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    payload["_exit_code"] = run.returncode
    payload["_stderr_tail"] = (run.stderr or "")[-200:]
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="first-launch and preferences check")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately do NOT create these: this is the first launch on a clean machine.
    data_dir = out_dir / "fresh" / "nested" / "data"
    log_dir = out_dir / "fresh" / "nested" / "logs"
    evidence: dict = {"data_dir": str(data_dir), "log_dir": str(log_dir),
                      "pre_existing": {"data": data_dir.exists(), "logs": log_dir.exists()}}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2
    env = os.environ.copy()
    env["FOLIO_PLAYWRIGHT_CORE"] = playwright
    env["FOLIO_CHROME"] = chrome

    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        evidence["launch"] = {"port": launch.evidence.port,
                              "service_pid": launch.evidence.service_pid}
        evidence["dirs_created"] = {"data": data_dir.is_dir(), "logs": log_dir.is_dir()}
        try:
            with urllib.request.urlopen(launch.url, timeout=30) as response:
                body = response.read().decode("utf-8", "replace")
            # The served HTML is the Streamlit shell (the product title is set
            # client-side); the rendered UI is checked by the browser probe below.
            evidence["page"] = {"http_status": response.status,
                                "bytes": len(body),
                                "shell_ok": "streamlit" in body.lower() and len(body) > 2000}
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            evidence["page"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Switch the language so the preference file is written.
        evidence["switch"] = _probe(node, launch.url, out_dir / "switch",
                                    out_dir / "switch.json", env, keep=True)
    finally:
        service.stop()

    prefs_path = data_dir / "ui_preferences.json"
    try:
        preferences = json.loads(prefs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        preferences = {}
    evidence["preferences_after_switch"] = preferences

    # Restart: the UI must come back in the chosen language by itself.
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        evidence["after_restart"] = _probe(node, launch.url, out_dir / "restart",
                                           out_dir / "restart.json", env, read_only=True)
    finally:
        service.stop()

    switch = evidence.get("switch") or {}
    after_restart = evidence.get("after_restart") or {}
    evidence["ok"] = bool(
        not evidence["pre_existing"]["data"] and not evidence["pre_existing"]["logs"]
        and evidence["dirs_created"]["data"] and evidence["dirs_created"]["logs"]
        and (evidence.get("page") or {}).get("http_status") == 200
        and (evidence.get("page") or {}).get("shell_ok") is True
        and (switch.get("before") or {}).get("chinese_present") is True
        and switch.get("ok") is True
        and preferences.get("locale") == "en"
        and (after_restart.get("before") or {}).get("english_present") is True
        and (after_restart.get("before") or {}).get("chinese_present") is False
    )
    (out_dir / "d2-firstlaunch-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "pre_existing": evidence["pre_existing"],
        "dirs_created": evidence["dirs_created"],
        "page": evidence.get("page"),
        "preferences_after_switch": preferences,
        "restored_locale_after_restart": after_restart.get("before"),
        "evidence": str(out_dir / "d2-firstlaunch-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
