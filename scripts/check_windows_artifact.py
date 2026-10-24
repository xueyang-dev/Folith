#!/usr/bin/env python3
"""Windows equivalent of the macOS Qt release artifact checks.

The macOS checks prove facts about ``Folith.app`` and the DMG (Info.plist,
bundle identity, Mach-O closure).  Windows has different evidence: the frozen
EXE version resource, the portable ZIP layout, the NSIS installer, the bundled
Tesseract DLL closure and the build record's commit.  This script checks those
facts and fails on absence rather than reporting "missing = pass".

Usage:
    python scripts/check_windows_artifact.py `
        --zip dist/installer/Folith-Qt-0.7.2-windows-x64.zip `
        --installer dist/installer/Folith-Setup-0.7.2-windows-x64.exe `
        --out build/qt-windows/windows-artifact-check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "build" / "qt-windows"
DIST_DIR = ROOT / "dist" / "qt-windows"
BUNDLE = DIST_DIR / "Folith"
EXE = BUNDLE / "Folith.exe"
FORBIDDEN_NAMES = (
    "provider_config.json", "credentials.json", "secrets.toml", ".env",
    "id_rsa", "id_ed25519", ".netrc", ".pypirc",
)
FORBIDDEN_ENTRIES = ("streamlit", "pywebview", "webview")
BASELINE_LANGUAGES = ("eng", "chi_sim", "ara")
SHIPPED_SOURCES = ("folith", "core.py", "qt_client.py")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, check=False)


def _project_version() -> str:
    try:
        import tomllib

        with (ROOT / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        return ""


def _ps_json(script: str) -> dict:
    """Run a small PowerShell expression and decode its JSON object."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             script + " | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60, check=False)
        text = result.stdout.strip()
        return json.loads(text) if text else {}
    except Exception:
        return {}


def _version_info(path: Path) -> dict:
    quoted = "'" + str(path).replace("'", "''") + "'"
    return _ps_json(
        f"$v=(Get-Item -LiteralPath {quoted}).VersionInfo;"
        "[pscustomobject]@{FileVersion=$v.FileVersion;"
        "ProductVersion=$v.ProductVersion;"
        "FileDescription=$v.FileDescription;"
        "ProductName=$v.ProductName;CompanyName=$v.CompanyName}")


def _has_icon(path: Path) -> bool:
    quoted = "'" + str(path).replace("'", "''") + "'"
    data = _ps_json(
        "Add-Type -AssemblyName System.Drawing;"
        f"$i=[System.Drawing.Icon]::ExtractAssociatedIcon({quoted});"
        "[pscustomobject]@{has_icon=($null -ne $i)}")
    return data.get("has_icon") is True


def _record() -> tuple[dict, list[str]]:
    problems: list[str] = []
    path = OUT_DIR / "desktop_build_info.json"
    if not path.is_file():
        return {}, [f"missing build record: {path}"]
    record = json.loads(path.read_text(encoding="utf-8"))
    expected = _project_version()
    if record.get("app_version") != expected:
        problems.append(
            f"build record app_version={record.get('app_version')!r} "
            f"!= pyproject {expected!r}")
    if record.get("target_platform") != "windows":
        problems.append(f"build record platform is {record.get('target_platform')!r}")
    if record.get("target_arch") != "AMD64":
        problems.append(f"build record arch is {record.get('target_arch')!r}")
    if record.get("worktree_dirty") is not False:
        problems.append("build record says the worktree was dirty")
    head = _git("rev-parse", "HEAD").stdout.strip()
    if record.get("commit") != head:
        problems.append(
            f"build commit {str(record.get('commit'))[:12]} != HEAD {head[:12]}")
    if record.get("shell") != "qt":
        problems.append(f"build record shell is {record.get('shell')!r}")
    return record, problems


def _source_drift(record: dict) -> list[str]:
    commit = str(record.get("commit") or "")
    if not commit:
        return ["build record has no commit"]
    if _git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        return [f"build commit {commit[:12]} is not in this repository"]
    changed = sorted({
        name for name in _git(
            "diff", "--name-only", commit, "--", *SHIPPED_SOURCES
        ).stdout.splitlines() if name
    })
    untracked = sorted({
        name for name in _git(
            "ls-files", "--others", "--exclude-standard", "--", *SHIPPED_SOURCES
        ).stdout.splitlines() if name
    })
    drift = changed + [name for name in untracked if name not in changed]
    return [] if not drift else [
        f"{len(drift)} shipped source file(s) differ from build commit "
        f"{commit[:12]}: {', '.join(drift[:6])}"]


def _check_bundle(record: dict) -> tuple[dict, list[str]]:
    problems: list[str] = []
    facts: dict = {}
    exe = EXE
    if not BUNDLE.is_dir():
        return {"bundle": str(BUNDLE), "exists": False}, [
            f"missing frozen bundle: {BUNDLE}"]
    if not exe.is_file():
        problems.append(f"missing frozen executable: {exe}")
        return {"bundle": str(BUNDLE), "exists": True, "exe": False}, problems

    facts["bundle"] = str(BUNDLE)
    facts["exe"] = str(exe)
    facts["exe_bytes"] = exe.stat().st_size
    facts["files"] = sum(1 for path in BUNDLE.rglob("*") if path.is_file())
    facts["version_info"] = _version_info(exe)
    facts["has_icon"] = _has_icon(exe)

    version = str(record.get("app_version") or "")
    info = facts["version_info"]
    if version and version not in str(info.get("ProductVersion") or ""):
        problems.append(
            f"EXE ProductVersion {info.get('ProductVersion')!r} does not contain {version}")
    if version and version not in str(info.get("FileVersion") or ""):
        problems.append(
            f"EXE FileVersion {info.get('FileVersion')!r} does not contain {version}")
    if info.get("ProductName") != "Folith":
        problems.append(f"EXE ProductName is {info.get('ProductName')!r}")
    if not facts["has_icon"]:
        problems.append("the EXE has no associated icon")

    names = [path.name.lower() for path in BUNDLE.rglob("*")]
    for forbidden in FORBIDDEN_NAMES:
        if any(name == forbidden or name.startswith(forbidden + ".") for name in names):
            problems.append(f"forbidden private file in bundle: {forbidden}")
    for entry in FORBIDDEN_ENTRIES:
        if any(entry in path.name.lower() for path in BUNDLE.rglob("*")):
            problems.append(f"retired runtime present in bundle: {entry}")
    if any(path.name.lower() in {"provider_config.json", "outputs", "exports"}
           for path in BUNDLE.rglob("*")):
        problems.append("user data / provider config leaked into the bundle")
    # Third-party wheels may ship their own test modules (e.g. pyarrow/tests);
    # only the application's own test suite must never be in the artifact.
    for relative in ("tests", "_internal/tests"):
        if (BUNDLE / relative).is_dir():
            problems.append(f"application test directory shipped: {relative}")

    payload_path = OUT_DIR / "tesseract" / "tesseract_payload.json"
    engine = BUNDLE / "_internal" / "tesseract" / "bin" / "tesseract.exe"
    tessdata = BUNDLE / "_internal" / "tesseract" / "share" / "tessdata"
    ocr: dict = {"engine": str(engine), "engine_present": engine.is_file()}
    if payload_path.is_file():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        ocr["payload_version"] = payload.get("engine_version")
        ocr["payload_languages"] = payload.get("languages")
    ocr["languages"] = sorted(path.stem for path in tessdata.glob("*.traineddata"))
    ocr["configs"] = (tessdata / "configs").is_dir()
    if not engine.is_file():
        problems.append("bundled tesseract.exe is missing")
    if not set(BASELINE_LANGUAGES) <= set(ocr["languages"]):
        problems.append(
            f"bundled OCR languages {ocr['languages']} do not cover {list(BASELINE_LANGUAGES)}")
    if not ocr["configs"]:
        problems.append("bundled tessdata/configs is missing (tsv output would fail)")
    if engine.is_file():
        run = subprocess.run(
            [str(engine), "--tessdata-dir", str(tessdata), "--list-langs"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False)
        ocr["list_langs_exit_code"] = run.returncode
        ocr["list_langs"] = [line.strip() for line in run.stdout.splitlines()
                             if line.strip() and not line.lower().startswith("list")]
        if run.returncode != 0:
            problems.append(
                f"bundled tesseract could not list languages: {run.stderr[-300:]}")
        elif not set(BASELINE_LANGUAGES) <= set(ocr["list_langs"]):
            problems.append(
                f"tesseract did not load {list(BASELINE_LANGUAGES)}: {ocr['list_langs']}")
    facts["ocr"] = ocr
    return facts, problems


def _check_zip(path: Path, version: str) -> tuple[dict, list[str]]:
    problems: list[str] = []
    facts: dict = {"path": str(path)}
    if not path.is_file():
        return facts, [f"missing portable ZIP: {path}"]
    facts["bytes"] = path.stat().st_size
    facts["sha256"] = _sha256(path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            bad = archive.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        return facts, [f"portable ZIP is not readable: {exc}"]
    facts["entries"] = len(names)
    if bad:
        problems.append(f"portable ZIP has a corrupt entry: {bad}")
    if not names or any(not name.startswith("Folith/") for name in names):
        problems.append("portable ZIP must contain a single top-level Folith/ folder")
    if "Folith/Folith.exe" not in names:
        problems.append("portable ZIP does not contain Folith/Folith.exe")
    if not any(name.startswith("Folith/_internal/PySide6/") for name in names):
        problems.append("portable ZIP is missing the bundled PySide6 runtime")
    for name in names:
        lower = name.lower()
        if Path(lower).name in FORBIDDEN_NAMES:
            problems.append(f"portable ZIP contains private file: {name}")
        if "/outputs/" in lower or "/provider_config" in lower:
            problems.append(f"portable ZIP contains user data: {name}")
    if version and f"-{version}-" not in path.name:
        problems.append(f"portable ZIP name does not contain version {version}")
    facts["top_level"] = sorted({name.split("/", 1)[0] for name in names})
    return facts, problems


def _check_installer(path: Path | None, version: str) -> tuple[dict, list[str]]:
    problems: list[str] = []
    facts: dict = {}
    if path is None:
        return {"skipped": "portable-only build"}, []
    facts["path"] = str(path)
    if not path.is_file():
        return facts, [f"missing installer: {path}"]
    facts["bytes"] = path.stat().st_size
    facts["sha256"] = _sha256(path)
    facts["version_info"] = _version_info(path)
    facts["has_icon"] = _has_icon(path)
    info = facts["version_info"]
    if version and version not in str(info.get("ProductVersion") or ""):
        problems.append(
            f"installer ProductVersion {info.get('ProductVersion')!r} does not contain {version}")
    if version and f"-{version}-" not in path.name:
        problems.append(f"installer name does not contain version {version}")
    if not facts["has_icon"]:
        problems.append("the installer has no associated icon")
    return facts, problems


def check(zip_path: Path | None, installer: Path | None) -> dict:
    record, problems = _record()
    problems += _source_drift(record) if record else []
    bundle, bundle_problems = _check_bundle(record)
    problems += bundle_problems
    version = str(record.get("app_version") or _project_version())
    if zip_path is None:
        zip_path = ROOT / "dist" / "installer" / (
            f"Folith-Qt-{version}-windows-x64.zip")
    zip_facts, zip_problems = _check_zip(zip_path, version)
    problems += zip_problems
    installer_facts, installer_problems = _check_installer(installer, version)
    problems += installer_problems
    return {
        "ok": not problems,
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        "app_version": version,
        "build_record": record,
        "bundle": bundle,
        "portable_zip": zip_facts,
        "installer": installer_facts,
        "problems": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="check the Windows Qt artifact")
    parser.add_argument("--zip", type=Path, default=None)
    parser.add_argument("--installer", type=Path, default=None)
    parser.add_argument("--out", type=Path,
                        default=OUT_DIR / "windows-artifact-check.json")
    args = parser.parse_args(argv)
    report = check(args.zip, args.installer)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "app_version": report["app_version"],
        "zip_sha256": report["portable_zip"].get("sha256"),
        "installer_sha256": report["installer"].get("sha256"),
        "problems": report["problems"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
