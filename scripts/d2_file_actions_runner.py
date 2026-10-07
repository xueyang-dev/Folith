#!/usr/bin/env python3
"""D2 file-integration verification: reveal a delivered file from the real UI.

1. run the D0 slice with review enabled so a **final delivery snapshot** exists;
2. drive the app's delivery page and click "显示所在文件夹" on translation.docx;
3. verify the UI reported the native action and that the file really is on disk.

Usage:
    venv/bin/python scripts/d2_file_actions_runner.py --out /tmp/d2-files \
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

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2 file integration verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir)
    job = None
    setup: dict = {}
    setup_attempts: list[dict] = []
    try:
        # The browser occasionally dies mid-run in this environment; retry the
        # setup once before treating it as a real failure.
        for attempt in (1, 2):
            service = desktop_launcher.DesktopService(
                paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
                ready_timeout=240.0,
            )
            try:
                launch = service.start()
                env = os.environ.copy()
                env["FOLIO_PLAYWRIGHT_CORE"] = playwright
                env["FOLIO_CHROME"] = chrome
                run = subprocess.run(
                    [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                     "--url", launch.url, "--source", str(source),
                     "--out-dir", str(out_dir / f"setup{attempt}"), "--stub-url", stub_url,
                     "--result", str(out_dir / f"setup{attempt}.json"), "--review"],
                    capture_output=True, text=True, env=env, timeout=3600, check=False,
                )
                setup = _read_json(out_dir / f"setup{attempt}.json") or {}
                job = setup.get("job_id")
                setup_attempts.append({"attempt": attempt, "exit_code": run.returncode,
                                       "job_id": job,
                                       "error": str(setup.get("error") or "")[:160]})
                if job:
                    break
            finally:
                service.stop()
        evidence["setup"] = {"job_id": job, "ok": setup.get("ok"),
                             "attempts": setup_attempts}
        if not job:
            evidence["error"] = ("setup slice produced no job: "
                                 + str(setup.get("error") or "unknown"))
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:2000])
            return 1
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    snapshot_dir = data_dir / job / "delivery_snapshots"
    versions = sorted(snapshot_dir.glob("v*")) if snapshot_dir.exists() else []
    delivered = versions[-1] / "translation.docx" if versions else None
    evidence["delivery"] = {
        "snapshot_versions": [item.name for item in versions],
        "translation_docx": str(delivered) if delivered else None,
        "exists": bool(delivered and delivered.is_file()),
    }

    # Drive the delivery page and use the native reveal action.
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        env2 = os.environ.copy()
        env2["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env2["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d2_file_actions.js"),
             "--url", launch.url, "--job", job,
             "--out-dir", str(out_dir / "actions"), "--result", str(out_dir / "actions.json")],
            capture_output=True, text=True, env=env2, timeout=1800,
        )
        evidence["actions_run"] = {"exit_code": run.returncode}
        evidence["actions"] = _read_json(out_dir / "actions.json") or {}
    finally:
        service.stop()

    actions = evidence.get("actions") or {}
    evidence["ok"] = bool(
        evidence["delivery"]["exists"]
        and actions.get("translation_docx_button") is True
        and (actions.get("notice") or "").startswith("已在文件管理器中显示")
    )
    (out_dir / "d2-file-actions-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "snapshot": evidence["delivery"]["snapshot_versions"],
        "reveal_buttons": actions.get("reveal_buttons"),
        "open_buttons": actions.get("open_buttons"),
        "notice": actions.get("notice"),
        # Surface the phase error so a harness failure is diagnosable (and the
        # sweep can classify a dead browser as infrastructure, not product).
        "actions_error": actions.get("error"),
        "evidence": str(out_dir / "d2-file-actions-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
