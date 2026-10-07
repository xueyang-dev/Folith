#!/usr/bin/env python3
"""Release checks for the macOS desktop artifact (roadmap §5 D3 / line 57 / line 170).

Roadmap line 57 asks for "一份发行依赖版本清单、资源清单、验收用例与发布说明"; line 170
puts "可复现构建记录" and "支持系统声明" in D3.  This script inspects the artifact the
user actually gets and writes a release manifest:

* artifact identity: sha256, size, DMG verification;
* bundle identity and architecture;
* bundled runtime + the distributions actually inside the bundle;
* bundled OCR payload, cross-checked against the pinned tessdata provenance;
* development-environment leakage (Homebrew / venv / repo paths) in binaries and text;
* credential leakage (provider config / key-looking files);
* signature and notarisation state, reported honestly (BLOCKED in this environment).

Exit code 0 means every *checkable* item passed.  Unsigned / un-notarised is
recorded as BLOCKED, never as a pass.

Usage:
    venv/bin/python scripts/release_checks.py --out build/desktop/release-manifest.json \
        [--dmg dist/desktop/Folith-0.5.0-macos-arm64.dmg] [--app dist/desktop/Folith.app]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PREFIXES = ("/usr/lib/", "/System/", "/Library/Apple/")
FORBIDDEN_PATH_TOKENS = ("/opt/homebrew", "/usr/local/opt", "/FolioThread/venv", "venv/lib/python")
SECRET_PATTERNS = (re.compile(r"sk-[A-Za-z0-9]{20,}"), re.compile(r"AIza[0-9A-Za-z_\-]{30,}"))
CREDENTIAL_FILES = ("provider_config.json", ".env", "credentials.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv: list[str], timeout: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _check(name: str, ok: bool | None, detail: str, *, blocking: bool = True) -> dict:
    return {"check": name, "ok": ok, "detail": detail, "blocking": blocking}


def resource_report(app: Path) -> dict:
    """Compare the repository's resources with the copies inside the bundle.

    A resource that silently fails to ship (locale catalog, template, brand
    asset) leaves every other check green while the UI degrades at runtime.
    """
    repo = ROOT / "folith" / "resources"
    bundled = next((path for path in app.rglob("folith/resources") if path.is_dir()), None)
    if bundled is None:
        return {"bundled_root": None, "repo_files": 0, "bundle_files": 0,
                "missing": [], "mismatched": []}
    repo_files = {str(path.relative_to(repo)) for path in repo.rglob("*") if path.is_file()}
    bundle_files = {str(path.relative_to(bundled)) for path in bundled.rglob("*") if path.is_file()}
    missing = sorted(repo_files - bundle_files)
    mismatched = []
    for name in sorted(repo_files & bundle_files):
        if _sha256(repo / name) != _sha256(bundled / name):
            mismatched.append(name)
    return {"bundled_root": str(bundled.relative_to(app)), "repo_files": len(repo_files),
            "bundle_files": len(bundle_files), "missing": missing, "mismatched": mismatched}


def _bundled_distributions(app: Path) -> list[dict]:
    """Distributions PyInstaller actually placed in the bundle (accurate inventory)."""
    found: dict[str, str] = {}
    for info in app.rglob("*.dist-info"):
        metadata = info / "METADATA"
        if not metadata.is_file():
            continue
        name = version = ""
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines()[:40]:
            if line.startswith("Name: ") and not name:
                name = line[6:].strip()
            elif line.startswith("Version: ") and not version:
                version = line[11:].strip()
            if name and version:
                break
        if name:
            found[name] = version or "unknown"
    return [{"name": name, "version": version} for name, version in sorted(found.items())]


def _macho_dependencies(binary: Path) -> list[str]:
    result = _run(["otool", "-L", str(binary)], timeout=120)
    deps = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        deps.append(line.split(" (")[0].strip())
    return deps


def _scan_binaries(app: Path) -> tuple[list[str], list[str], int]:
    """Return (forbidden hits, external absolute deps, scanned binary count)."""
    hits: list[str] = []
    external: list[str] = []
    count = 0
    for path in app.rglob("*"):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                magic = handle.read(4)
        except OSError:
            continue
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
                         b"\xbe\xba\xfe\xca", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
            continue
        count += 1
        for dep in _macho_dependencies(path):
            if dep.startswith(SYSTEM_PREFIXES):
                continue
            external.append(dep)
            if any(token in dep for token in FORBIDDEN_PATH_TOKENS):
                hits.append(f"{path.relative_to(app)} -> {dep}")
    return hits, sorted(set(external)), count


def _scan_text(app: Path) -> tuple[list[str], list[str]]:
    """Return (forbidden path hits, secret-looking hits) for text resources."""
    path_hits: list[str] = []
    secret_hits: list[str] = []
    for path in app.rglob("*"):
        if not path.is_file() or path.stat().st_size > 3_000_000:
            continue
        if path.suffix.lower() in {".so", ".dylib", ".pyc", ".png", ".jpg", ".icns", ".ttf",
                                   ".traineddata", ".whl", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for token in FORBIDDEN_PATH_TOKENS:
            if token in text:
                path_hits.append(f"{path.relative_to(app)} contains {token}")
                break
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                secret_hits.append(f"{path.relative_to(app)}: {match.group(0)[:12]}…")
    return path_hits, secret_hits


def _tessdata_report(app: Path) -> dict:
    tessdata = app / "Contents" / "Frameworks" / "tesseract" / "share" / "tessdata"
    if not tessdata.is_dir():
        tessdata = app / "Contents" / "Resources" / "tesseract" / "share" / "tessdata"
    provenance = None
    for candidate in (tessdata.parent / "tessdata_provenance.json",
                      app / "Contents" / "Frameworks" / "tesseract" / "tessdata_provenance.json"):
        if candidate.is_file():
            provenance = json.loads(candidate.read_text(encoding="utf-8"))
            break
    languages = sorted(path.stem for path in tessdata.glob("*.traineddata")) if tessdata.is_dir() else []
    mismatches: list[str] = []
    if provenance:
        for record in provenance.get("languages") or []:
            shipped = tessdata / record["file"]
            if not shipped.is_file():
                mismatches.append(f"{record['file']}: missing")
                continue
            digest = _sha256(shipped)
            if digest != record.get("sha256"):
                mismatches.append(f"{record['file']}: sha256 {digest[:12]}… != pinned {str(record.get('sha256'))[:12]}…")
    return {
        "languages": languages,
        "configs_present": (tessdata / "configs" / "tsv").is_file(),
        "provenance": bool(provenance),
        "licence": (provenance or {}).get("licence"),
        "sha256_mismatches": mismatches,
    }


def _build_record(app: Path) -> dict:
    for candidate in (app / "Contents" / "Frameworks" / "desktop_build_info.json",
                      app / "Contents" / "Resources" / "desktop_build_info.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return {}


def dmg_app_matches_built_app(shipped_app: Path, built_app: Path) -> tuple[bool, str]:
    """Does the image carry the app that was just built?

    Rebuilding the app without re-cutting the DMG ships the wrong binary while
    every other check still passes, so this is a blocking release check.
    """
    shipped = _build_record(shipped_app)
    built = _build_record(built_app)
    same = bool(shipped) and bool(built) and shipped.get("built_at") == built.get("built_at") \
        and shipped.get("build_id") == built.get("build_id")
    detail = (f"DMG app built_at={shipped.get('built_at')} vs built app "
              f"built_at={built.get('built_at')}")
    if not same:
        detail += " -> rebuild the DMG (scripts/build_dmg.sh)"
    return same, detail


def run_checks(*, dmg: Path | None, app: Path, built_app: Path | None = None) -> dict:
    checks: list[dict] = []
    manifest: dict = {}

    plist_path = app / "Contents" / "Info.plist"
    plist = plistlib.loads(plist_path.read_bytes()) if plist_path.is_file() else {}
    icon_name = str(plist.get("CFBundleIconFile") or "")
    icon = app / "Contents" / "Resources" / icon_name if icon_name else None
    checks.append(_check("bundle_present", app.is_dir(), str(app)))
    checks.append(_check("bundle_identity",
                         plist.get("CFBundleName") == "Folith"
                         and plist.get("CFBundleIdentifier") == "dev.folith.desktop",
                         f"{plist.get('CFBundleName')} / {plist.get('CFBundleIdentifier')}"))
    checks.append(_check("own_icon", bool(icon and icon.is_file()) and icon_name != "icon-windowed.icns",
                         f"CFBundleIconFile={icon_name or 'missing'}"))

    binary = app / "Contents" / "MacOS" / "Folith"
    archs = _run(["lipo", "-archs", str(binary)]).stdout.strip() if binary.is_file() else ""
    checks.append(_check("architecture", archs == "arm64",
                         f"lipo -archs -> {archs or 'unavailable'} (first release targets arm64)"))

    record_path = app / "Contents" / "Frameworks" / "desktop_build_info.json"
    if not record_path.is_file():
        record_path = app / "Contents" / "Resources" / "desktop_build_info.json"
    record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
    manifest["app"] = {
        "version": record.get("app_version"),
        "build_id": record.get("build_id"),
        "commit": record.get("commit"),
        "worktree_dirty": record.get("worktree_dirty"),
        "packaging_backend": record.get("packaging_backend"),
        "target_platform": record.get("target_platform"),
        "target_arch": record.get("target_arch"),
        "minimum_system_version": plist.get("LSMinimumSystemVersion"),
        "bundled_at": record.get("built_at"),
    }
    checks.append(_check("build_record", bool(record.get("app_version") and record.get("build_id")),
                         f"{record.get('app_version')} / {record.get('build_id')}"))

    manifest["runtime"] = {
        key: record.get(key) for key in
        ("python_version", "streamlit_version", "pywebview_version", "pyinstaller_version")
    }
    checks.append(_check("bundled_runtime", all(manifest["runtime"].values()),
                         json.dumps(manifest["runtime"], ensure_ascii=False)))

    distributions = _bundled_distributions(app)
    manifest["bundled_distributions"] = distributions
    checks.append(_check("distribution_inventory", len(distributions) > 0,
                         f"{len(distributions)} distributions inside the bundle "
                         "(PyInstaller ships metadata only where collected)"))

    requirements: dict[str, list[str]] = {}
    for name in ("requirements.txt", "requirements-desktop.txt", "requirements-desktop-build.txt"):
        path = ROOT / name
        if path.is_file():
            requirements[name] = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                                  if line.strip() and not line.strip().startswith("#")]
    manifest["pinned_requirements"] = requirements
    checks.append(_check("pinned_requirements_recorded", bool(requirements),
                         f"{sorted(requirements)} recorded in the manifest"))

    resources = resource_report(app)
    manifest["resources"] = resources
    checks.append(_check("bundled_resources_complete",
                         bool(resources["repo_files"]) and not resources["missing"]
                         and not resources["mismatched"],
                         f"{resources['repo_files']} repo files / "
                         f"{resources['bundle_files']} bundled; "
                         f"missing={resources['missing'] or 'none'}, "
                         f"mismatched={resources['mismatched'] or 'none'}"))

    ocr = _tessdata_report(app)
    manifest["ocr"] = ocr
    checks.append(_check("ocr_languages", set(ocr["languages"]) == {"ara", "chi_sim", "eng"},
                         f"{ocr['languages']} (beta baseline)"))
    checks.append(_check("ocr_configs", ocr["configs_present"], "tessdata/configs/tsv present"))
    checks.append(_check("ocr_provenance", ocr["provenance"] and not ocr["sha256_mismatches"],
                         f"licence={ocr['licence']}, mismatches={ocr['sha256_mismatches'] or 'none'}"))

    forbidden_bin, external, scanned = _scan_binaries(app)
    manifest["binary_scan"] = {"scanned": scanned, "external_dependencies": external[:40],
                              "forbidden": forbidden_bin}
    checks.append(_check("no_development_paths_in_binaries", not forbidden_bin,
                         f"{scanned} binaries scanned; hits={forbidden_bin[:3] or 'none'}"))

    forbidden_text, secrets = _scan_text(app)
    manifest["text_scan"] = {"forbidden_paths": forbidden_text[:10], "secret_like": secrets[:5]}
    checks.append(_check("no_development_paths_in_text", not forbidden_text,
                         f"hits={forbidden_text[:3] or 'none'}"))
    checks.append(_check("no_secret_like_strings", not secrets,
                         f"hits={secrets[:3] or 'none'}"))

    bundled_credentials = [str(path.relative_to(app)) for path in app.rglob("*")
                           if path.name in CREDENTIAL_FILES]
    checks.append(_check("no_credentials_bundled", not bundled_credentials,
                         f"hits={bundled_credentials[:3] or 'none'}"))

    if dmg is not None:
        verify = _run(["hdiutil", "verify", str(dmg)])
        manifest["artifact"] = {
            "path": str(dmg), "bytes": dmg.stat().st_size, "sha256": _sha256(dmg),
            "format": "UDZO", "verify_exit_code": verify.returncode,
        }
        checks.append(_check("dmg_checksum", verify.returncode == 0,
                             f"hdiutil verify exit={verify.returncode}"))

    # The shipped image must contain the app that was just built: rebuilding the
    # app without re-cutting the DMG is a silent way to ship the wrong binary.
    if built_app is not None and built_app.is_dir() and built_app.resolve() != app.resolve():
        same, detail = dmg_app_matches_built_app(app, built_app)
        checks.append(_check("dmg_app_matches_built_app", same, detail))

    codesign = _run(["codesign", "-dv", "--verbose=2", str(app)])
    codesign_text = (codesign.stderr or codesign.stdout).strip()
    adhoc = "adhoc" in codesign_text.lower()
    authority = re.search(r"Authority=(.+)", codesign_text)
    manifest["signature"] = {
        "identifier": (re.search(r"Identifier=(.+)", codesign_text) or [None, None])[1],
        "adhoc": adhoc,
        "authority": authority.group(1).strip() if authority else None,
        "developer_id": bool(authority and "Developer ID" in authority.group(1)),
    }
    checks.append(_check("signed_with_developer_id", None if not manifest["signature"]["developer_id"] else True,
                         "BLOCKED: no Developer ID in this environment" if adhoc
                         else codesign_text.splitlines()[0] if codesign_text else "no signature",
                         blocking=False))

    spctl = _run(["spctl", "-a", "-vv", str(app)])
    manifest["gatekeeper"] = {
        "exit_code": spctl.returncode,
        "detail": (spctl.stderr or spctl.stdout).strip()[:200],
        "notarized": spctl.returncode == 0,
    }
    checks.append(_check("notarized_and_accepted", True if spctl.returncode == 0 else None,
                         f"spctl exit={spctl.returncode}: {manifest['gatekeeper']['detail'][:120]}"
                         if spctl.returncode != 0 else "accepted",
                         blocking=False))

    manifest["supported_systems"] = {
        "macos_minimum": plist.get("LSMinimumSystemVersion"),
        "architecture": archs or "unknown",
        "windows": "BLOCKED - no Windows host in this environment (D2/D3 Windows work deferred)",
    }
    # Roadmap line 11: the release page may only list systems that were actually
    # verified, so record the machine these checks ran on.
    manifest["verified_environment"] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }
    manifest["checks"] = checks
    blocking = [item for item in checks if item["blocking"]]
    manifest["blocking_failures"] = [item["check"] for item in blocking if item["ok"] is not True]
    manifest["ok"] = not manifest["blocking_failures"]
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="macOS desktop release checks")
    parser.add_argument("--out", default=str(ROOT / "build" / "desktop" / "release-manifest.json"))
    parser.add_argument("--dmg", default=None)
    parser.add_argument("--app", default=None)
    parser.add_argument("--mount", action="store_true",
                        help="check the app inside the mounted DMG instead of the build dir")
    args = parser.parse_args(argv)

    dmg = Path(args.dmg).expanduser().resolve() if args.dmg else None
    if dmg is None:
        candidates = sorted((ROOT / "dist" / "desktop").glob("Folith-*-macos-*.dmg"))
        dmg = candidates[-1] if candidates else None
    app = Path(args.app).expanduser().resolve() if args.app else (ROOT / "dist" / "desktop" / "Folith.app")
    if not app.is_dir():
        print(json.dumps({"error": f"no app bundle at {app}"}, indent=2))
        return 2

    built_app = app
    mount_dir = None
    try:
        if args.mount and dmg is not None:
            mount_dir = Path(tempfile.mkdtemp(prefix="folith-release-"))
            attach = _run(["hdiutil", "attach", "-nobrowse", "-readonly",
                           "-mountpoint", str(mount_dir), str(dmg)])
            if attach.returncode != 0:
                print(json.dumps({"error": "attach failed", "detail": attach.stderr[:200]}, indent=2))
                return 1
            app = mount_dir / "Folith.app"
        manifest = run_checks(dmg=dmg, app=app, built_app=built_app)
    finally:
        if mount_dir is not None:
            _run(["hdiutil", "detach", str(mount_dir)])
            shutil.rmtree(mount_dir, ignore_errors=True)

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({
        "ok": manifest["ok"],
        "blocking_failures": manifest["blocking_failures"],
        "checks": {item["check"]: item["ok"] for item in manifest["checks"]},
        "app": manifest["app"],
        "ocr": manifest["ocr"]["languages"],
        "distributions": len(manifest["bundled_distributions"]),
        "signature": manifest["signature"],
        "manifest": str(out),
    }, ensure_ascii=False, indent=2))
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
