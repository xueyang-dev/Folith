#!/usr/bin/env python3
"""D2 DMG verification: mount the image, inspect it, and launch from the mount.

Roadmap §5 (D2 macOS): the beta is delivered as a DMG containing the app and an
Applications drag target.  This checks the real artifact rather than the build
directory:

1. `hdiutil attach` the DMG read-only and verify its checksum;
2. the mounted volume must contain `Folith.app` plus an `Applications` symlink
   pointing at `/Applications`, and the app must carry its own identity;
3. launch the app **from the mounted volume** (read-only) and confirm the service
   handshake succeeds and everything is reclaimed on exit;
4. detach and record the evidence.

Usage:
    venv/bin/python scripts/d2_dmg_runner.py --out /tmp/d2-dmg \
        [--dmg dist/desktop/Folith-0.5.0-macos-arm64.dmg]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _run(argv: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _services() -> int:
    result = _run(["ps", "-eo", "command"], timeout=30)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2 DMG verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dmg", default=None)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    dmg = Path(args.dmg).expanduser().resolve() if args.dmg else None
    if dmg is None:
        candidates = sorted((ROOT / "dist" / "desktop").glob("Folith-*-macos-*.dmg"))
        if not candidates:
            print(json.dumps({"error": "no DMG found; run scripts/build_dmg.sh"}, indent=2))
            return 2
        dmg = candidates[-1]
    evidence: dict = {"dmg": str(dmg)}

    evidence["verify"] = {
        "exit_code": _run(["hdiutil", "verify", str(dmg)]).returncode,
        "bytes": dmg.stat().st_size,
        "sha256": _sha256(dmg),
    }

    mount = out_dir / "mnt"
    if mount.exists():
        shutil.rmtree(mount)
    mount.mkdir()
    attach = _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)])
    evidence["attach"] = {"exit_code": attach.returncode, "stdout": attach.stdout.strip()[:300]}
    if attach.returncode != 0:
        evidence["ok"] = False
        evidence["error"] = "attach failed"
        (out_dir / "d2-dmg-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:800])
        return 1

    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    try:
        app = mount / "Folith.app"
        applications = mount / "Applications"
        plist = {}
        plist_path = app / "Contents" / "Info.plist"
        if plist_path.is_file():
            plist = plistlib.loads(plist_path.read_bytes())
        evidence["contents"] = {
            "app_present": app.is_dir(),
            "applications_symlink": applications.is_symlink(),
            "applications_target": os.readlink(applications) if applications.is_symlink() else None,
            "bundle_name": plist.get("CFBundleName"),
            "bundle_identifier": plist.get("CFBundleIdentifier"),
            "bundle_icon": plist.get("CFBundleIconFile"),
            "bundle_version": plist.get("CFBundleShortVersionString"),
            "icon_present": (app / "Contents" / "Resources" / str(plist.get("CFBundleIconFile"))).is_file(),
        }

        # Launch the app from the read-only volume.
        env = os.environ.copy()
        env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
        env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
        env["FOLITH_BUILD_ID"] = "dmg-verification"
        binary = app / "Contents" / "MacOS" / "Folith"
        proc = subprocess.Popen([str(binary), "--no-browser"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        record = None
        deadline = time.monotonic() + args.ready_timeout
        while time.monotonic() < deadline and record is None and proc.poll() is None:
            record = _read_json(data_dir.parent / "instance.json")
            time.sleep(1)
        evidence["launch"] = {
            "handshake_record": bool(record),
            "service_pid": (record or {}).get("service_pid"),
            "port": (record or {}).get("port"),
            "data_dir": str(data_dir),
        }
        if record:
            evidence["launch"]["service_alive"] = desktop_process.pid_alive(
                int(record.get("service_pid") or 0))
        proc.send_signal(signal.SIGTERM)
        try:
            stdout, _ = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
            stdout, _ = proc.communicate()
        evidence["launch"]["exit_code"] = proc.returncode
        evidence["launch"]["stdout_tail"] = (stdout or "")[-400:]
        time.sleep(3)
        evidence["launch"]["services_after_exit"] = _services()
        evidence["launch"]["record_removed"] = not (data_dir.parent / "instance.json").exists()
    finally:
        detach = _run(["hdiutil", "detach", str(mount)])
        evidence["detach"] = {"exit_code": detach.returncode, "stdout": detach.stdout.strip()[:200]}

    contents = evidence.get("contents") or {}
    launch = evidence.get("launch") or {}
    evidence["ok"] = bool(
        evidence["verify"]["exit_code"] == 0
        and contents.get("app_present")
        and contents.get("applications_symlink") and contents.get("applications_target") == "/Applications"
        and contents.get("bundle_name") == "Folith"
        and contents.get("bundle_identifier") == "dev.folith.desktop"
        and contents.get("icon_present")
        and launch.get("handshake_record")
        and launch.get("service_alive")
        and launch.get("services_after_exit") == 0
        and evidence.get("detach", {}).get("exit_code") == 0
    )
    (out_dir / "d2-dmg-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "dmg": evidence["dmg"],
        "verify_exit": evidence["verify"]["exit_code"],
        "size_mb": round(evidence["verify"]["bytes"] / 1e6, 1),
        "sha256": evidence["verify"]["sha256"][:16] + "…",
        "contents": contents,
        "launch": launch,
        "evidence": str(out_dir / "d2-dmg-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
