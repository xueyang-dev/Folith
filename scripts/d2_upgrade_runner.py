#!/usr/bin/env python3
"""D2 upgrade verification: replacing the app must keep the user's data.

Roadmap §3.2: "升级只更新应用、保留数据；实际改变格式时再设计对应备份 / 兼容处理，
不预建多版本迁移体系。"

The check is deliberately concrete:

1. install build A (v0.5.0) into /Applications and create real work with it
   (import → translate → edit → review → freeze delivery);
2. fingerprint the durable job assets;
3. build B with a *different* version and build id into throw-away directories,
   and install B over A;
4. run the upgraded app on the same data directory: diagnostics must report B,
   the job must reopen with its committed edit and delivery intact;
5. the durable assets must be byte-identical and no migration/backup files may
   appear; removing the app must leave the data intact.

Usage:
    venv/bin/python scripts/d2_upgrade_runner.py --out /tmp/d2-upgrade \
        [--dmg dist/desktop/Folith-0.5.0-macos-arm64.dmg]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
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

INSTALLED = Path("/Applications/Folith.app")
DURABLE = ("state.json", "translation_drafts.json", "runtime_state.json")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fingerprint(job_dir: Path) -> dict[str, str]:
    """sha256 of every durable file in the job directory (snapshots included)."""
    result: dict[str, str] = {}
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") or "logs" in path.parts:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(job_dir))] = digest
    return result


def _install(app: Path) -> None:
    if INSTALLED.exists():
        shutil.rmtree(INSTALLED)
    subprocess.run(["ditto", str(app), str(INSTALLED)], check=True)


def _diagnostics(binary: Path, data_dir: Path, log_dir: Path, target: Path,
                 extra_env: dict | None = None) -> dict:
    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    env.update(extra_env or {})
    subprocess.run([str(binary), "--export-diagnostics", str(target)],
                   env=env, capture_output=True, text=True, timeout=600, check=False)
    payload = _read_json(target) or {}
    return payload.get("diagnostics") or {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D2 upgrade verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dmg", default=None)
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse an existing build B under the output directory")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {}
    if INSTALLED.exists():
        print(json.dumps({"error": f"{INSTALLED} already exists; refusing to overwrite"},
                         ensure_ascii=False, indent=2))
        return 2

    # The harness runs in source mode, so point the launcher at the installed
    # bundle (the frozen app is the service child) and at its bundled OCR payload.
    os.environ["FOLITH_DESKTOP_EXECUTABLE"] = str(INSTALLED / "Contents" / "MacOS" / "Folith")
    dmg = Path(args.dmg).expanduser().resolve() if args.dmg else None
    if dmg is None:
        candidates = sorted((ROOT / "dist" / "desktop").glob("Folith-*-macos-*.dmg"))
        if not candidates:
            print(json.dumps({"error": "no DMG found; run scripts/build_dmg.sh"}, indent=2))
            return 2
        dmg = candidates[-1]

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    # ---- 1. install A from the DMG and create real work ---------------------
    mount = out_dir / "mnt"
    mount.mkdir(exist_ok=True)
    attach = subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly",
                             "-mountpoint", str(mount), str(dmg)],
                            capture_output=True, text=True, check=False)
    if attach.returncode != 0:
        print(json.dumps({"error": "attach failed", "detail": attach.stderr[:200]}, indent=2))
        return 1
    try:
        _install(mount / "Folith.app")
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], capture_output=True, text=True, check=False)

    version_a = _diagnostics(INSTALLED / "Contents" / "MacOS" / "Folith",
                             data_dir, log_dir, out_dir / "diag-a.json")
    evidence["installed_a"] = {
        "version": (version_a.get("app") or {}).get("version"),
        "build_id": (version_a.get("app") or {}).get("build_id"),
    }

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])

    slice_out = out_dir / "slice"
    stub, stub_url, _log = start_stub_llm(out_dir)
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    job = None
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url, "--source", str(source),
             "--out-dir", str(slice_out / "setup"), "--stub-url", stub_url,
             "--result", str(slice_out / "setup.json"), "--review"],
            capture_output=True, text=True, env=env, timeout=3600, check=False)
        setup = _read_json(slice_out / "setup.json") or {}
        job = setup.get("job_id")
        evidence["work"] = {"job_id": job, "ok": setup.get("ok"),
                            "edit_text": setup.get("edit_text"),
                            # Surface the slice's own failure so a harness browser
                            # hiccup is distinguishable from a product problem.
                            "slice_error": str(setup.get("error") or ""),
                            "failed_step": next((step.get("step") for step in setup.get("steps") or []
                                                 if not step.get("ok")), None)}
    finally:
        service.stop()
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()
    if not job:
        evidence["ok"] = False
        evidence["error"] = "could not create work with build A"
        (out_dir / "d2-upgrade-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:600])
        return 1

    job_dir = data_dir / job
    before = _fingerprint(job_dir)
    evidence["before_upgrade"] = {"files": len(before), "job_dir": str(job_dir)}

    # ---- 2. build B (different version / build id) into throw-away dirs -----
    build_out = out_dir / "build-b"
    dist_b = build_out / "dist"
    evidence["build_b"] = {"version": "0.5.1", "build_id": f"upgrade-{int(time.time())}"}
    if not args.skip_build:
        build_env = os.environ.copy()
        build_env.update({
            "FOLITH_APP_VERSION": evidence["build_b"]["version"],
            "FOLITH_BUILD_ID": evidence["build_b"]["build_id"],
            "FOLITH_OUT_DIR": str(build_out / "work"),
            "FOLITH_DIST_DIR": str(dist_b),
        })
        built = subprocess.run(["bash", str(ROOT / "scripts" / "build_desktop.sh")],
                               capture_output=True, text=True, env=build_env,
                               timeout=3600, check=False)
        evidence["build_b"]["exit_code"] = built.returncode
        if built.returncode != 0:
            evidence["ok"] = False
            evidence["build_b"]["tail"] = (built.stdout or built.stderr)[-500:]
            (out_dir / "d2-upgrade-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:800])
            return 1

    app_b = dist_b / "Folith.app"
    if not app_b.is_dir():
        evidence["ok"] = False
        evidence["error"] = f"build B produced no bundle at {app_b}"
        (out_dir / "d2-upgrade-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:600])
        return 1

    # ---- 3. upgrade in place and reopen the existing work ------------------
    _install(app_b)
    version_b = _diagnostics(INSTALLED / "Contents" / "MacOS" / "Folith",
                             data_dir, log_dir, out_dir / "diag-b.json")
    evidence["installed_b"] = {
        "version": (version_b.get("app") or {}).get("version"),
        "build_id": (version_b.get("app") or {}).get("build_id"),
    }

    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        reopen = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_reopen.js"),
             "--url", launch.url, "--job", job,
             "--expect", str((_read_json(slice_out / "setup.json") or {}).get("edit_text") or ""),
             "--out-dir", str(slice_out / "reopen"),
             "--result", str(slice_out / "reopen.json")],
            capture_output=True, text=True, env=env, timeout=1800, check=False)
        evidence["reopen"] = {"exit_code": reopen.returncode,
                              **(_read_json(slice_out / "reopen.json") or {})}
    finally:
        service.stop()

    after = _fingerprint(job_dir)
    changed = sorted(name for name, digest in before.items() if after.get(name) != digest)
    added = sorted(name for name in after if name not in before)
    removed = sorted(name for name in before if name not in after)
    migration = sorted(str(path.relative_to(job_dir)) for path in job_dir.rglob("*")
                       if any(token in path.name.lower()
                              for token in ("backup", ".bak", "migrat", "legacy-copy")))
    evidence["after_upgrade"] = {
        "files": len(after), "changed": changed, "added": added, "removed": removed,
        "migration_artifacts": migration,
    }

    # ---- 4. remove the app; the data must survive --------------------------
    shutil.rmtree(INSTALLED)
    survived = _fingerprint(job_dir) == after
    evidence["after_uninstall"] = {"app_removed": not INSTALLED.exists(), "data_intact": survived}

    reopen_payload = evidence.get("reopen") or {}
    evidence["ok"] = bool(
        evidence["installed_a"]["version"] == "0.5.0"
        and evidence["installed_b"]["version"] == "0.5.1"
        and evidence["installed_b"]["build_id"] == evidence["build_b"]["build_id"]
        and evidence["installed_a"]["build_id"] != evidence["installed_b"]["build_id"]
        and evidence["work"]["ok"]
        and reopen_payload.get("ok") is True
        and not changed and not removed and not migration
        and evidence["after_uninstall"]["data_intact"]
    )
    (out_dir / "d2-upgrade-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "installed_a": evidence["installed_a"],
        "installed_b": evidence["installed_b"],
        "job_id": job,
        "reopen_ok": reopen_payload.get("ok"),
        "reopen_steps": [s.get("step") for s in (reopen_payload.get("steps") or [])],
        "durable_files": len(before),
        "changed": changed, "added": added, "removed": removed,
        "migration_artifacts": migration,
        "after_uninstall": evidence["after_uninstall"],
        "evidence": str(out_dir / "d2-upgrade-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
