"""Generate the desktop build record consumed by diagnostics and the handshake.

Roadmap §3.5 requires diagnostics to report the app version, build id/commit,
Python / Streamlit / pywebview versions, OS, architecture and packaging backend
*of the artifact that is actually running*.  A frozen bundle therefore carries
its own record instead of trusting ambient package metadata (the repository venv
still has a stale editable `foliothread 0.4.0` dist-info, which would otherwise
be reported for a 0.5.0 build).

Usage:
    python scripts/write_desktop_build_info.py --out build/desktop/desktop_build_info.json \
        --backend pyinstaller --platform macos --arch arm64
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "unknown"))
    except Exception:
        return "not-installed"


def _distribution_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "unknown"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _project_version() -> str:
    try:
        import tomllib

        with (ROOT / "pyproject.toml").open("rb") as handle:
            return str(tomllib.load(handle).get("project", {}).get("version", "unknown"))
    except Exception:
        return "unknown"


def _qt_version() -> str:
    try:
        from PySide6.QtCore import qVersion

        return str(qVersion())
    except Exception:
        return "unknown"


def build_record(*, backend: str, platform_name: str, arch: str, dirty: bool | None = None,
                 app_version: str | None = None, build_id: str | None = None,
                 shell: str = "streamlit") -> dict:
    commit = _git("rev-parse", "HEAD")
    short = _git("rev-parse", "--short", "HEAD")
    if dirty is None:
        dirty = bool(_git("status", "--porcelain"))
    # Release engineering may pin the version (a release tag) and the build id;
    # the upgrade verification uses the same overrides to build a distinct app.
    record = {
        "app_version": app_version or _project_version(),
        "build_id": build_id or (f"git:{short}" if short else "unknown"),
        "commit": commit or "unknown",
        "worktree_dirty": bool(dirty),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "packaging_backend": backend,
        "target_platform": platform_name,
        "target_arch": arch,
        "python_version": sys.version.split()[0],
        "shell": shell,
        "pyinstaller_version": _distribution_version("pyinstaller"),
        "os_version": platform.platform(),
    }
    # Record the runtime the artifact actually ships.  The Qt client and the
    # retired Streamlit shell have different runtimes, and reporting the other
    # shell's versions is exactly the MAC-04 defect.
    if shell == "qt":
        record["pyside6_version"] = _distribution_version("PySide6")
        record["qt_version"] = _qt_version()
    else:
        record["streamlit_version"] = _distribution_version("streamlit")
        record["pywebview_version"] = _distribution_version("pywebview")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="write the desktop build record")
    parser.add_argument("--out", required=True)
    parser.add_argument("--backend", default="pyinstaller")
    parser.add_argument("--platform", dest="platform_name", default=sys.platform)
    parser.add_argument("--arch", default=platform.machine())
    parser.add_argument("--dirty", choices=["true", "false"], default=None)
    parser.add_argument("--shell", choices=["streamlit", "qt"], default="streamlit",
                        help="which desktop shell this record describes")
    parser.add_argument("--app-version", default=None,
                        help="override the app version (release tag / upgrade test)")
    parser.add_argument("--build-id", default=None,
                        help="override the build id (release tag / upgrade test)")
    args = parser.parse_args(argv)

    dirty = None if args.dirty is None else args.dirty == "true"
    record = build_record(
        backend=args.backend, platform_name=args.platform_name, arch=args.arch, dirty=dirty,
        app_version=args.app_version, build_id=args.build_id, shell=args.shell,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
