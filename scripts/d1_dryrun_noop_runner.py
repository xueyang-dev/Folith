#!/usr/bin/env python3
"""D1 `--import-dry-run` must be a strict no-op (roadmap §3.2).

The earlier evidence only showed "the target directory was not created".  A user
who already has data is the common case, so the dry run must also change nothing
when the target exists: neither the legacy source nor the existing data
directory may gain, lose or modify a single file.

Usage:
    venv/bin/python scripts/d1_dryrun_noop_runner.py --out /tmp/d1-dryrun \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
"""
from __future__ import annotations

import argparse
import hashlib
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

# A plausible translation: the product's completeness gate blocks short stand-ins.
EDIT_TEXT = ("近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由试运行验证标记二〇二六写入。")


def _fingerprint(root: Path) -> dict[str, str]:
    """Every entry under `root`: files by content hash, directories by marker."""
    entries: dict[str, str] = {}
    if not root.exists():
        return entries
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_dir():
            entries[f"dir:{relative}"] = ""
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries[f"file:{relative}"] = digest
    return entries


def _diff(before: dict[str, str], after: dict[str, str]) -> dict:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "changed": sorted(name for name in set(before) & set(after)
                          if before[name] != after[name]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="dry-run no-op verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir = out_dir / "legacy-outputs"
    data_dir = out_dir / "desktop-data"
    log_dir = out_dir / "desktop-logs"
    evidence: dict = {"legacy_dir": str(legacy_dir), "data_dir": str(data_dir)}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # --- 1. a real legacy directory -----------------------------------------
    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir)
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=legacy_dir, log_dir=out_dir / "legacy-logs"),
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
             "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"), "--edit-text", EDIT_TEXT],
            capture_output=True, text=True, env=env, timeout=3600, check=False)
        setup = json.loads((out_dir / "setup.json").read_text(encoding="utf-8"))
        job = setup.get("job_id")
        evidence["legacy_setup"] = {"exit_code": run.returncode, "job_id": job}
        if not job:
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1500])
            return 1
    finally:
        service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    (legacy_dir / "provider_config.json").write_text(
        json.dumps({"provider": "自定义中转站", "api_key": "sk-" + "LEGACY-MUST-NOT-TRAVEL"}),
        encoding="utf-8")

    # --- 2. a target that already has the user's own data -------------------
    data_dir.mkdir(parents=True, exist_ok=True)
    existing_job = data_dir / "existing-job"
    existing_job.mkdir()
    (existing_job / "state.json").write_text(
        json.dumps({"pairs": [], "delivery_status": "draft"}), encoding="utf-8")
    (data_dir / "provider_config.json").write_text(
        json.dumps({"provider": "自定义中转站", "api_key": "sk-USER-OWN-KEY"}),
        encoding="utf-8")
    (data_dir / ".onboarded").write_text("1", encoding="utf-8")

    before_legacy = _fingerprint(legacy_dir)
    before_data = _fingerprint(data_dir)
    evidence["before"] = {"legacy_entries": len(before_legacy),
                          "data_entries": len(before_data)}

    # --- 3. dry run ---------------------------------------------------------
    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    dry = subprocess.run([str(Path(args.frozen_app).expanduser()),
                          "--import-legacy-data", str(legacy_dir), "--import-dry-run"],
                         capture_output=True, text=True, env=env, timeout=300, check=False)
    evidence["dry_run"] = {"exit_code": dry.returncode,
                           "stdout": dry.stdout.strip()[:400],
                           "mentions_dry_run": "试运行" in dry.stdout}

    after_legacy = _fingerprint(legacy_dir)
    after_data = _fingerprint(data_dir)
    evidence["legacy_diff"] = _diff(before_legacy, after_legacy)
    evidence["data_diff"] = _diff(before_data, after_data)
    evidence["user_key_untouched"] = (
        json.loads((data_dir / "provider_config.json").read_text(encoding="utf-8"))
        .get("api_key") == "sk-USER-OWN-KEY")

    evidence["ok"] = bool(
        evidence["dry_run"]["exit_code"] == 0
        and evidence["dry_run"]["mentions_dry_run"]
        and not any(evidence["legacy_diff"].values())
        and not any(evidence["data_diff"].values())
        and evidence["user_key_untouched"]
    )
    (out_dir / "d1-dryrun-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "dry_run": evidence["dry_run"],
        "legacy_diff": evidence["legacy_diff"],
        "data_diff": evidence["data_diff"],
        "user_key_untouched": evidence["user_key_untouched"],
        "evidence": str(out_dir / "d1-dryrun-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
