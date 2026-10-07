#!/usr/bin/env python3
"""D1 legacy-data adoption verification (roadmap §3.2).

1. build a real "legacy" data directory (a job produced by the browser slice
   against a service pointed at that directory) and hash the whole tree;
2. adopt it with the frozen app's `--import-legacy-data` into a fresh desktop
   data directory;
3. verify: the legacy tree is byte-identical afterwards, the full asset set was
   copied, credentials were not copied, and the report says so;
4. reopen the adopted job in the real app (frozen, `--no-browser` + browser
   reopen check) to prove it is usable, not merely present on disk.

Usage:
    venv/bin/python scripts/d1_import_runner.py --out /tmp/d1-import \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import legacy_import  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

EDIT_TEXT = "导入校验编辑 IMPORT-CHECK"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tree_digest(root: Path) -> dict:
    """Path -> sha256 for every file, so "unchanged" is provable."""
    digest: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 legacy data adoption verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir = out_dir / "legacy-outputs"
    desktop_dir = out_dir / "desktop-data"
    log_dir = out_dir / "desktop-logs"
    evidence: dict = {}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # --- 1. produce a real legacy data directory ----------------------------
    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _stub_log = start_stub_llm(out_dir)
    setup_service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=legacy_dir, log_dir=out_dir / "legacy-logs"),
        ready_timeout=240.0,
    )
    try:
        launch = setup_service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url, "--source", str(source),
             "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"),
             "--edit-text", EDIT_TEXT],
            capture_output=True, text=True, env=env, timeout=3600,
        )
        setup = _read_json(out_dir / "setup.json") or {}
        job = setup.get("job_id")
        evidence["legacy_setup"] = {"exit_code": run.returncode, "job_id": job}
        if not job:
            evidence["error"] = "legacy job was not created"
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:2000])
            return 1
    finally:
        setup_service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    # A credential in the legacy directory must not travel.
    (legacy_dir / "provider_config.json").write_text(
        json.dumps({"provider": "自定义中转站", "api_key": "sk-LEGACY-MUST-NOT-TRAVEL"}),
        encoding="utf-8")
    before = _tree_digest(legacy_dir)
    evidence["legacy_tree"] = {"files": len(before), "job": job}

    # --- 2. adopt it with the frozen app ------------------------------------
    app = Path(args.frozen_app).expanduser()
    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(desktop_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    dry = subprocess.run([str(app), "--import-legacy-data", str(legacy_dir),
                          "--import-dry-run"],
                         capture_output=True, text=True, env=env, timeout=300, check=False)
    evidence["dry_run"] = {"exit_code": dry.returncode,
                           "stdout": dry.stdout.strip()[:400],
                           "destination_exists": desktop_dir.exists()}
    adopt = subprocess.run([str(app), "--import-legacy-data", str(legacy_dir)],
                           capture_output=True, text=True, env=env, timeout=900, check=False)
    evidence["import"] = {"exit_code": adopt.returncode,
                          "stdout": adopt.stdout.strip()[:600]}

    # --- 3. verify ----------------------------------------------------------
    after = _tree_digest(legacy_dir)
    legacy_job_files = sorted(str(p.relative_to(legacy_dir / job))
                              for p in (legacy_dir / job).rglob("*") if p.is_file())
    copied_files = sorted(str(p.relative_to(desktop_dir))
                          for p in (desktop_dir / job).rglob("*") if p.is_file())
    adopted_job_files = sorted(str(p.relative_to(desktop_dir / job))
                               for p in (desktop_dir / job).rglob("*") if p.is_file())
    report = legacy_import.verify_import(desktop_dir, [job])
    evidence["verification"] = {
        "legacy_tree_unchanged": before == after,
        "legacy_files_before": len(before),
        "legacy_files_after": len(after),
        "adopted_job_files": copied_files,
        # The whole asset set must travel: the file sets must be identical.
        "legacy_job_file_count": len(legacy_job_files),
        "asset_set_identical": legacy_job_files == adopted_job_files,
        "adopted_job_readable": report.get("ok"),
        "credential_copied": (desktop_dir / "provider_config.json").exists(),
        "asset_kinds": {
            name: (desktop_dir / job / name).exists()
            for name in ("state.json", "state_journal.jsonl", "source.bin",
                         "translation_drafts.json", "delivery_snapshots")
        },
    }

    # --- 4. reopen the adopted job in the real app --------------------------
    reopen_service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=desktop_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = reopen_service.start()
        env2 = os.environ.copy()
        env2["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env2["FOLIO_CHROME"] = chrome
        reopen = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_reopen.js"),
             "--url", launch.url, "--job", job, "--expect", EDIT_TEXT,
             "--out-dir", str(out_dir / "reopen"), "--result", str(out_dir / "reopen.json")],
            capture_output=True, text=True, env=env2, timeout=900,
        )
        payload = _read_json(out_dir / "reopen.json") or {}
        evidence["reopen"] = {
            "exit_code": reopen.returncode,
            "editor_contains_edit": payload.get("editor_contains_edit"),
            "editor_excerpt": payload.get("editor_excerpt"),
        }
    finally:
        reopen_service.stop()

    verification = evidence["verification"]
    evidence["ok"] = bool(
        evidence.get("import", {}).get("exit_code") == 0
        and verification["legacy_tree_unchanged"]
        and verification["adopted_job_readable"]
        and not verification["credential_copied"]
        and verification["asset_set_identical"]
        and evidence.get("reopen", {}).get("editor_contains_edit") is True
    )
    (out_dir / "d1-import-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "import_exit": evidence.get("import", {}).get("exit_code"),
        "legacy_tree_unchanged": verification["legacy_tree_unchanged"],
        "adopted_files": len(copied_files),
        "credential_copied": verification["credential_copied"],
        "reopen_editor_has_edit": evidence.get("reopen", {}).get("editor_contains_edit"),
        "evidence": str(out_dir / "d1-import-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
