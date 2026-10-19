"""Desktop data/log directory rules and build identity (roadmap §3.2, §3.5).

The desktop entry point must set an absolute data directory *before* importing
`core`, so `core.OUTPUT_DIR` (which reads ``FOLITH_OUTPUT_DIR``) resolves to a
stable per-user location instead of a relative ``outputs`` folder inside the
install directory.  Source runs keep their existing convention.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DATA_DIR_ENV = "FOLITH_DESKTOP_DATA_DIR"
LOG_DIR_ENV = "FOLITH_DESKTOP_LOG_DIR"
BUILD_ID_ENV = "FOLITH_BUILD_ID"
APP_VERSION_ENV = "FOLITH_APP_VERSION"
BUILD_INFO_ENV = "FOLITH_BUILD_INFO"
BUILD_INFO_FILENAME = "desktop_build_info.json"

APP_DIR_NAME = "Folith"


@dataclass(frozen=True)
class DesktopPaths:
    data_dir: Path
    log_dir: Path

    def ensure(self) -> "DesktopPaths":
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def launcher_log(self) -> Path:
        return self.log_dir / "launcher.log"

    @property
    def service_log(self) -> Path:
        return self.log_dir / "service.log"

    @property
    def state_dir(self) -> Path:
        """App-level state directory (parent of the user data directory).

        Deliberately outside ``data_dir``: instance locks and instance records
        are machine-local process state, not user project data that a user might
        copy or back up.
        """
        return self.data_dir.parent

    @property
    def instance_lock(self) -> Path:
        return self.state_dir / ".instance.lock"

    @property
    def instance_record(self) -> Path:
        return self.state_dir / "instance.json"


def default_data_dir(platform: str | None = None, env: dict[str, str] | None = None) -> Path:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_DIR_NAME / "data"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / "data"
    base = env.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_DIR_NAME / "data"


def default_log_dir(platform: str | None = None, env: dict[str, str] | None = None) -> Path:
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform == "win32":
        base = env.get("LOCALAPPDATA") or env.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_DIR_NAME / "logs"
    if platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_DIR_NAME
    base = env.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_DIR_NAME / "logs"


def resolve_paths(env: dict[str, str] | None = None, platform: str | None = None) -> DesktopPaths:
    """Resolve data/log directories; an explicit user setting always wins."""
    env = os.environ if env is None else env
    data = env.get(DATA_DIR_ENV)
    logs = env.get(LOG_DIR_ENV)
    data_dir = Path(data).expanduser() if data else default_data_dir(platform, env)
    log_dir = Path(logs).expanduser() if logs else default_log_dir(platform, env)
    return DesktopPaths(data_dir=data_dir.resolve(), log_dir=log_dir.resolve())


def build_info() -> dict:
    """Build record written by the desktop build script (empty when absent).

    Diagnostics must report the versions of the artifact that is actually
    running, so a frozen build carries its own build record instead of relying
    on whatever package metadata happens to be importable.
    """
    explicit = os.environ.get(BUILD_INFO_ENV)
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(resource_path(BUILD_INFO_FILENAME))
    for candidate in candidates:
        try:
            if candidate.exists():
                import json

                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            continue
    return {}


def app_version() -> str:
    """Version of the actually-running artifact."""
    explicit = os.environ.get(APP_VERSION_ENV)
    if explicit:
        return explicit.strip()
    recorded = build_info().get("app_version")
    if isinstance(recorded, str) and recorded:
        return recorded
    root = repo_root()
    if root is not None:
        from_source = _pyproject_version(root / "pyproject.toml")
        if from_source:
            return from_source
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("foliothread")
        except PackageNotFoundError:
            pass
    except Exception:  # pragma: no cover - defensive
        pass
    return "0.0.0+unknown"


def _pyproject_version(path: Path) -> str | None:
    """Read the source-of-truth version from pyproject.toml (source runs only)."""
    try:
        import tomllib

        with path.open("rb") as handle:
            data = tomllib.load(handle)
        value = data.get("project", {}).get("version")
        if isinstance(value, str) and value:
            return value
    except Exception:  # pragma: no cover - defensive
        return None
    return None


def build_id() -> str:
    """Build identifier: explicit build record first, git commit second."""
    explicit = os.environ.get(BUILD_ID_ENV)
    if explicit:
        return explicit.strip()
    recorded = build_info().get("build_id")
    if isinstance(recorded, str) and recorded:
        return recorded
    root = repo_root()
    if root is not None:
        try:
            import subprocess

            from .process import hidden_console_kwargs
            result = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5, check=False,
                **hidden_console_kwargs(),
            )
            if result.returncode == 0 and result.stdout.strip():
                return f"git:{result.stdout.strip()}"
        except Exception:  # pragma: no cover - defensive
            pass
    return "unknown"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def repo_root() -> Path | None:
    """Source checkout root, or None when running from a frozen bundle."""
    if is_frozen():
        return None
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "core.py").exists() and (candidate / "qt_client.py").exists():
            return candidate
    return None


def bundle_root() -> Path:
    """Directory holding bundled resources (PyInstaller-aware)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    root = repo_root()
    if root is not None:
        return root
    return Path(sys.executable).resolve().parent


def resource_path(*parts: str) -> Path:
    """Locate a bundled resource, preferring the frozen bundle layout."""
    root = bundle_root()
    candidate = root.joinpath(*parts)
    if candidate.exists():
        return candidate
    fallback = repo_root()
    if fallback is not None:
        other = fallback.joinpath(*parts)
        if other.exists():
            return other
    return candidate


def tesseract_binary_name() -> str:
    return "tesseract.exe" if os.name == "nt" else "tesseract"


def tesseract_payload_root() -> Path | None:
    """Bundled OCR payload root, when the build shipped one (roadmap §3.4)."""
    root = resource_path("tesseract")
    if (root / "bin" / tesseract_binary_name()).is_file():
        return root
    return None


def apply_bundled_tool_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Point the OCR engine and its data at the bundled payload.

    Applied to the launcher process as well as the service child: diagnostics
    and any tool probe must report the engine the app actually ships, not
    whatever happens to be installed on the machine.
    """
    target = os.environ if env is None else env
    payload = tesseract_payload_root()
    if payload is not None:
        target["FOLIOTHREAD_TESSERACT_CMD"] = str(payload / "bin" / tesseract_binary_name())
        target["TESSDATA_PREFIX"] = str(payload / "share" / "tessdata")
    return target


def app_script_path() -> Path:
    """Path to the Streamlit main script (`app.py`) for the running build."""
    explicit = os.environ.get("FOLITH_APP_SCRIPT")
    if explicit:
        return Path(explicit).expanduser().resolve()
    bundled = resource_path("app.py")
    if bundled.exists():
        return bundled.resolve()
    root = repo_root()
    if root is not None:
        return (root / "app.py").resolve()
    return bundled.resolve()
