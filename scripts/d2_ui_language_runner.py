#!/usr/bin/env python3
"""D2 check: the bundled locale catalogs are reachable at runtime.

Switches the UI language in the frozen app (Chinese → English → Chinese) and
verifies the visible text actually changes.  Roadmap §5 D2: the beta must work for
an ordinary user; a catalog that silently fails to load would leave the UI in
Chinese while every static check still passed.

Usage:
    venv/bin/python scripts/d2_ui_language_runner.py --out /tmp/d2-lang \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bundled UI language check")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict = {}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=out_dir / "data", log_dir=out_dir / "logs"),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_ui_language.js"),
             "--url", launch.url, "--out-dir", str(out_dir / "probe"),
             "--result", str(out_dir / "language.json")],
            capture_output=True, text=True, env=env, timeout=1800, check=False)
        evidence["probe_run"] = {"exit_code": run.returncode,
                                 "stderr_tail": (run.stderr or "")[-300:]}
        try:
            evidence["probe"] = json.loads((out_dir / "language.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            evidence["probe"] = {}
    finally:
        service.stop()

    probe = evidence.get("probe") or {}
    evidence["ok"] = bool((evidence.get("probe_run") or {}).get("exit_code") == 0
                          and probe.get("ok") is True)
    (out_dir / "d2-ui-language-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "before": probe.get("before"),
        "after_english": {k: v for k, v in (probe.get("after_english") or {}).items()
                          if k != "sample"},
        "restored": probe.get("restored"),
        "error": probe.get("error"),
        "evidence": str(out_dir / "d2-ui-language-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
