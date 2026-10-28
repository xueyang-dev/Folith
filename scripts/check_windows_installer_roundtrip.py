#!/usr/bin/env python3
"""Install, upgrade and uninstall the NSIS installer; prove user files survive.

The installer lets the user pick any folder, and a user may point Folith's data
folder inside it.  Uninstalling must remove only what the installer wrote.
This drives the real installer silently against two folders:

* a folder that already holds unrelated user files (and a data folder): after
  an upgrade-install and uninstall, those files are intact, the application
  files are gone, and a stale file from the earlier install did not survive
  the upgrade;
* a fresh folder: uninstall leaves nothing behind;
* while Folith.exe is held as a running process holds it, install and
  uninstall both stop with exit code 2 and change nothing.

It writes the per-user uninstall registry key and Start Menu shortcuts, so it
refuses to run on a machine where Folith is already installed.

Usage:
    python scripts/check_windows_installer_roundtrip.py `
        --installer dist/installer/Folith-Setup-0.8.0-windows-x64.exe `
        --out build/qt-windows/windows-installer-roundtrip.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

UNINST_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Folith"
APP_FILES = ("Folith.exe", "_internal", "Uninstall.exe")


def _registered() -> bool:
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINST_KEY))
        return True
    except OSError:
        return False


def _shortcut_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Folith"


def _install(installer: Path, target: Path) -> None:
    # /D must be the last argument and must not be quoted, even with spaces,
    # so build the command line by hand instead of letting list2cmdline quote it.
    command = f'"{installer}" /S /D={target}'
    result = subprocess.run(command, timeout=600, check=False)
    if result.returncode != 0:
        raise SystemExit(f"installer exited with {result.returncode}")
    if not (target / "Folith.exe").is_file():
        raise SystemExit(f"installer did not install into {target}")


def _uninstall(target: Path, timeout: float = 120.0) -> None:
    # The uninstaller copies itself to %TEMP% and returns at once; wait for
    # the copy to finish by watching what it removes.
    subprocess.run([str(target / "Uninstall.exe"), "/S"], timeout=60, check=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _registered() and not any((target / name).exists() for name in APP_FILES):
            time.sleep(0.5)  # let RMDir of the folder itself run
            return
        time.sleep(0.25)
    raise SystemExit(f"uninstall did not finish within {timeout}s")


class _HeldLikeRunning:
    """Hold Folith.exe the way a running process does: no one else may write it.

    Starting the real application here would touch the user's data folder and
    single-instance lock, so the check reproduces the file lock instead.
    """

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        import _winapi

        generic_read, share_read, open_existing = 0x80000000, 0x1, 3
        self.handle = _winapi.CreateFile(
            str(self.path), generic_read, share_read, 0, open_existing, 0, 0)
        return self

    def __exit__(self, *exc):
        import _winapi

        _winapi.CloseHandle(self.handle)


def _check_refuses_while_running(installer: Path, target: Path, problems: list[str]) -> dict:
    """Install and uninstall must stop, not half-replace files, while Folith runs."""
    facts = {}
    marker = target / "_internal" / "still-here.txt"
    marker.write_text("x", encoding="utf-8")
    with _HeldLikeRunning(target / "Folith.exe"):
        install = subprocess.run(f'"{installer}" /S /D={target}', timeout=600, check=False)
        # _?= runs the uninstaller in place and synchronously, so its exit code
        # is the real one (normally it copies itself to %TEMP% and returns).
        uninstall = subprocess.run(
            f'"{target / "Uninstall.exe"}" /S _?={target}', timeout=120, check=False)
    facts["install_while_running_exit"] = install.returncode
    facts["uninstall_while_running_exit"] = uninstall.returncode
    if install.returncode != 2:
        problems.append(f"install while running exited {install.returncode}, expected 2")
    if uninstall.returncode != 2:
        problems.append(f"uninstall while running exited {uninstall.returncode}, expected 2")
    if not marker.is_file():
        problems.append("install/uninstall changed files while Folith was running")
    if not _registered():
        problems.append("uninstall while running removed the registry key")
    marker.unlink(missing_ok=True)
    return facts


def run(installer: Path) -> tuple[dict, list[str]]:
    problems: list[str] = []
    facts: dict = {"installer": str(installer)}
    with tempfile.TemporaryDirectory(prefix="folith-nsis-") as tmp:
        root = Path(tmp)

        # 1. A folder that already holds the user's own files.
        shared = root / "Shared Apps 共享"
        user_files = {
            shared / "notes.txt": "the user's own file",
            shared / "Folith data" / "jobs" / "task.json": '{"status": "completed"}',
        }
        for path, text in user_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        _install(installer, shared)
        for name in APP_FILES:
            if not (shared / name).exists():
                problems.append(f"install did not create {name}")
        if not _registered():
            problems.append("install did not register the uninstall key")
        if not _shortcut_dir().is_dir():
            problems.append("install did not create the Start Menu folder")

        facts.update(_check_refuses_while_running(installer, shared, problems))

        stale = shared / "_internal" / "stale-from-previous-version.dll"
        stale.write_bytes(b"old")
        _install(installer, shared)  # upgrade in place
        facts["stale_file_removed_by_upgrade"] = not stale.exists()
        if stale.exists():
            problems.append("upgrade left a file the new build does not ship")

        _uninstall(shared)
        for path, text in user_files.items():
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                problems.append(f"uninstall removed or changed a user file: {path.relative_to(root)}")
        for name in APP_FILES:
            if (shared / name).exists():
                problems.append(f"uninstall left {name}")
        if _registered():
            problems.append("uninstall left the registry key")
        if _shortcut_dir().exists():
            problems.append("uninstall left the Start Menu folder")
        facts["user_files_kept"] = all(path.is_file() for path in user_files)

        # 2. A fresh folder is removed completely.
        fresh = root / "Folith"
        _install(installer, fresh)
        _uninstall(fresh)
        facts["fresh_folder_removed"] = not fresh.exists()
        if fresh.exists():
            problems.append(f"uninstall left the fresh install folder: {sorted(p.name for p in fresh.iterdir())}")
    return facts, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("the installer round trip runs on Windows only", file=sys.stderr)
        return 2
    if _registered():
        print("Folith is already installed for this user; refusing to touch it", file=sys.stderr)
        return 2
    facts, problems = run(args.installer.resolve())
    report = {"ok": not problems, "problems": problems, **facts}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
