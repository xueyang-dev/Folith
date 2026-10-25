"""Signed Windows updates. Network and disk work is called through TaskRunner.

The trusted public key is embedded at build time, never taken from a release.
GitHub hosts the manifest, its detached signature and the existing NSIS package.
"""
from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import Version

REPO = "https://github.com/xueyang-dev/Folith"
PUBLIC_KEY_FILE = Path(__file__).resolve().parents[1] / "resources" / "update-public-key.txt"
MANIFEST_NAME = "folith-update-windows-x64.json"


class UpdateError(RuntimeError):
    pass


class DownloadCancelled(UpdateError):
    pass


def public_key() -> str:
    return PUBLIC_KEY_FILE.read_text(encoding="ascii").strip() if PUBLIC_KEY_FILE.exists() else ""


def windows_available() -> bool:
    return (sys.platform == "win32" and bool(getattr(sys, "frozen", False))
            and platform.machine().lower() in {"amd64", "x86_64"} and bool(public_key()))


def _release_url(url: str, version: str) -> str:
    prefix = f"{REPO}/releases/download/v{version}/"
    if not url.startswith(prefix) or "/" in url[len(prefix):] or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", url[len(prefix):]):
        raise UpdateError("Invalid release asset URL")
    return url


def _read(url: str, *, opener=None, limit: int = 1024 * 1024) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Folith-Updater"})
    with (opener or urllib.request.urlopen)(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("Update metadata is too large")
    return data


def verify_manifest(raw: bytes, signature: bytes, key: str, *, expected_version: str) -> dict:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
            base64.b64decode(signature, validate=True), raw)
        result = json.loads(raw)
        version = result["version"]
        if (not re.fullmatch(r"\d+\.\d+\.\d+", version)
                or version != expected_version.lstrip("v") or result["schema"] != 1
                or result["platform"] != "windows" or result["arch"] != "x64"
                or not re.fullmatch(r"[a-f0-9]{64}", result["sha256"])
                or type(result["size"]) is not int or not 0 < result["size"] <= 2**32):
            raise ValueError("Wrong update version, platform, architecture or size")
        _release_url(result["url"], version)
        if not result["url"].endswith(".exe"):
            raise ValueError("Expected an NSIS installer")
        return result
    except Exception as exc:
        raise UpdateError("Update signature or metadata validation failed") from exc


def fetch_manifest(version: str, *, key: str | None = None, opener=None) -> dict:
    version = version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise UpdateError("Only stable releases support in-app updates")
    url = f"{REPO}/releases/download/v{version}/{MANIFEST_NAME}"
    return verify_manifest(_read(url, opener=opener), _read(url + ".sig", opener=opener),
                           key if key is not None else public_key(), expected_version=version)


def download(manifest: dict, directory: Path, *, cancel: threading.Event,
             on_progress=None, opener=None) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"Folith-Setup-{manifest['version']}-windows-x64.exe"
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    received = 0
    try:
        request = urllib.request.Request(_release_url(manifest["url"], manifest["version"]),
                                         headers={"User-Agent": "Folith-Updater"})
        with (opener or urllib.request.urlopen)(request, timeout=20) as response, partial.open("wb") as stream:
            while True:
                if cancel.is_set():
                    raise DownloadCancelled("Download cancelled")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > manifest["size"]:
                    raise UpdateError("Unexpected update size")
                stream.write(chunk)
                digest.update(chunk)
                if on_progress:
                    on_progress(received, manifest["size"])
        if cancel.is_set():
            raise DownloadCancelled("Download cancelled")
        if received != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
            raise UpdateError("The downloaded update is incomplete or damaged")
        partial.replace(target)
        return {"path": str(target), "manifest": manifest}
    finally:
        partial.unlink(missing_ok=True)


def launch_windows_update(package: dict, *, data_dir: Path, log_dir: Path) -> None:
    """Called only after normal shutdown was accepted. The helper waits for our PID.

    Copy the helper out of the installation so replacing the bundle cannot remove it.
    No shell interpolation: paths are separate argv entries.
    """
    if not windows_available():
        raise UpdateError("In-app updates require a signed, packaged Windows build")
    installer = Path(package["path"]).resolve()
    manifest = package["manifest"]
    if Version(manifest["version"]) <= Version(_installed_version()):
        raise UpdateError("The update is not newer than this installation")
    install = Path(sys.executable).resolve().parent
    if any(path.resolve().is_relative_to(install) for path in (data_dir, log_dir)):
        raise UpdateError("Move data and log folders outside the application before updating")
    helper = installer.parent / "install-update.ps1"
    shutil.copyfile(Path(__file__).with_name("install-update.ps1"), helper)
    log_dir.mkdir(parents=True, exist_ok=True)
    import os
    command = [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
               "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
               "-File", str(helper), "-ParentId", str(os.getpid()), "-Installer", str(installer),
               "-Sha256", manifest["sha256"], "-InstallDir", str(install),
               "-TargetVersion", manifest["version"],
               "-DataDir", str(data_dir.resolve()), "-LogDir", str(log_dir.resolve())]
    subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW,
                     close_fds=True, cwd=str(installer.parent))


def acknowledge_windows_update(log_dir: Path) -> str:
    """Only a successfully constructed new window retires the recovery backup."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return ""
    result_path = log_dir / "update-result.json"
    if not result_path.exists():
        return ""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        install = Path(sys.executable).resolve().parent
        if result["state"] == "failed":
            failed = install.with_name(install.name + ".update-failed")
            if failed.is_dir() and not failed.is_symlink():
                shutil.rmtree(failed)
            result_path.unlink()
            return "failed"
        if result["state"] != "installed" or result["version"] != _installed_version():
            return "recovery"
        backup = Path(result["backup"]).resolve()
        if backup != install.with_name(install.name + ".update-backup"):
            raise UpdateError("Unexpected recovery backup path")
        # Validate the exact sibling before recursively removing our backup.
        if backup.is_dir() and not backup.is_symlink():
            shutil.rmtree(backup)
        result_path.unlink()
        return "installed"
    except (OSError, KeyError, ValueError, UpdateError):
        return "recovery"


def _installed_version() -> str:
    from .paths import app_version
    return app_version()
