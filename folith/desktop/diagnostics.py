"""Desktop build/runtime diagnostics and a redacted export (D1, roadmap §3.5).

Facts are taken from the artifact that is actually running: the build record
shipped inside the bundle, the real interpreter/library versions, the resolved
user data and log directories, and the schema versions **observed in the
persisted data** (plus the cheap journal constant) — the roadmap explicitly
allows `job_state=unversioned` and requires not inventing a new state format or
migration system for diagnostics.

The export is deliberately narrow: it contains build facts, resolved paths with
the user name removed, and a filtered error summary.  It never reads the
provider configuration, environment dumps, tokens, or document contents.
"""
from __future__ import annotations

import json
import platform
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import paths as desktop_paths

DIAGNOSTICS_FILE = "diagnostics.json"
EXPORT_VERSION = 1
ERROR_LINE_LIMIT = 60
ERROR_SCAN_LIMIT = 4000
_ERROR_MARKERS = ("error", "warning", "traceback", "错误", "失败", "异常")

# Paths that must never be read while building diagnostics.
_FORBIDDEN_SOURCES = ("provider_config.json", ".onboarded")
_SECRET_KEYS = ("api_key", "apikey", "token", "authorization", "secret", "password")

# High-confidence credential shapes.  Log lines can carry a key *without* the
# `api_key=` prefix (an upstream error echoing the header value, a provider SDK
# message), and §3.5 requires API keys to be excluded from the export.  These
# patterns deliberately do not match plain hashes, so diagnostics stay useful.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),           # OpenAI-style
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),          # Google
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),             # GitHub
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),     # Slack
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),  # Authorization: Bearer …
)


@dataclass
class DiagnosticsReport:
    facts: dict[str, Any]
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "export_version": EXPORT_VERSION,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "diagnostics": self.facts,
            "error_summary": list(self.errors),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


# --- redaction -------------------------------------------------------------


def redact_path(value: str | Path | None, home: Path | None = None) -> str:
    """Remove the user name from a path (roadmap §3.5).

    Paths under the home directory become `~/...`; any other occurrence of the
    user name (for example a temp directory named `pytest-of-<user>`) is
    replaced with `<user>` so a user-chosen location cannot leak a project or
    account name into a support bundle.
    """
    if value is None:
        return ""
    text = str(value)
    home = home or Path.home()
    try:
        home_text = str(home)
        user_name = home.name
    except Exception:  # pragma: no cover - defensive
        return text
    if home_text and text.startswith(home_text):
        text = "~" + text[len(home_text):]
    if user_name:
        text = text.replace(user_name, "<user>")
    return text


def _redact_text(text: str, home: Path | None = None) -> str:
    """Redact the home directory anywhere in the text, plus secret-looking values.

    Log lines carry the home path in the middle (after a timestamp), so a prefix
    replacement is not enough.
    """
    home = home or Path.home()
    home_text = str(home)
    redacted = text.replace(home_text, "~")
    redacted = redact_path(redacted, home)
    # Specific credential shapes first: the generic `key: value` rule would
    # otherwise consume only the word "Bearer" and leave the token behind.
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    for key in _SECRET_KEYS:
        redacted = re.sub(rf"(?i)({key}\s*[:=]\s*)\S+", r"\1<redacted>", redacted)
    return redacted


# --- collection ------------------------------------------------------------


def _distribution_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "not-installed"


def _journal_schema_declared() -> Any:
    try:
        from folith.storage.state_journal import JOURNAL_SCHEMA_VERSION

        return JOURNAL_SCHEMA_VERSION
    except Exception:
        return None


def _core_schema_declared(attribute: str) -> Any:
    """Read a schema version from the business core's own constant.

    Roadmap §3.5: the declared versions must come from the corresponding
    constants (never a diagnostic-only copy).  Importing `core` is heavy, so it
    happens lazily and any failure is reported as unknown rather than invented.
    """
    try:
        import core

        return getattr(core, attribute)
    except Exception:
        return None


def _observed_schema(data_dir: Path) -> dict[str, Any]:
    """Schema versions actually present in the persisted data (newest first)."""
    observed: dict[str, Any] = {"runtime": None, "journal": None,
                                "translation_drafts": None, "jobs_scanned": 0}
    if not data_dir.is_dir():
        return observed
    job_dirs = sorted(
        (entry for entry in data_dir.iterdir()
         if entry.is_dir() and not entry.name.startswith(".")),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for job in job_dirs[:20]:
        observed["jobs_scanned"] += 1
        if observed["runtime"] is None:
            runtime = _read_json(job / "runtime_state.json")
            if isinstance(runtime, dict):
                value = runtime.get("schema_version")
                if value is not None:
                    observed["runtime"] = value
        if observed["translation_drafts"] is None:
            drafts = _read_json(job / "translation_drafts.json")
            if isinstance(drafts, dict):
                value = drafts.get("version")
                if value is not None:
                    observed["translation_drafts"] = value
        if observed["journal"] is None:
            journal = job / "state_journal.jsonl"
            try:
                with journal.open(encoding="utf-8") as handle:
                    for line in handle:
                        record = json.loads(line)
                        if isinstance(record, dict) and record.get("schema") is not None:
                            observed["journal"] = record["schema"]
                            break
            except (OSError, ValueError):
                pass
        if all(observed[key] is not None
               for key in ("runtime", "journal", "translation_drafts")):
            break
    return observed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _job_count(data_dir: Path) -> int:
    if not data_dir.is_dir():
        return 0
    return sum(1 for entry in data_dir.iterdir()
               if entry.is_dir() and not entry.name.startswith("."))


def collect(*, paths: desktop_paths.DesktopPaths | None = None,
            build: dict[str, Any] | None = None,
            pywebview_version: str | None = None) -> dict[str, Any]:
    """Gather the roadmap §3.5 facts from the running artifact."""
    paths = paths or desktop_paths.resolve_paths()
    record = build if build is not None else desktop_paths.build_info()
    observed = _observed_schema(paths.data_dir)
    return {
        "app": {
            "version": desktop_paths.app_version(),
            "build_id": desktop_paths.build_id(),
            "commit": record.get("commit") or "unknown",
            "built_at": record.get("built_at") or "unknown",
            "packaging_backend": record.get("packaging_backend") or "source",
            "target_platform": record.get("target_platform") or sys.platform,
            "target_arch": record.get("target_arch") or platform.machine(),
            "frozen": desktop_paths.is_frozen(),
        },
        "runtime": {
            "python_version": sys.version.split()[0],
            "streamlit_version": record.get("streamlit_version")
            or _distribution_version("streamlit"),
            "pywebview_version": pywebview_version
            or record.get("pywebview_version")
            or _distribution_version("pywebview"),
            "pyinstaller_version": record.get("pyinstaller_version")
            or _distribution_version("pyinstaller"),
        },
        "system": {
            "os": platform.system(),
            "os_version": platform.platform(),
            "architecture": platform.machine(),
            "machine": platform.machine(),
        },
        "locations": {
            "data_dir": redact_path(paths.data_dir),
            "log_dir": redact_path(paths.log_dir),
            "launcher_log": redact_path(paths.launcher_log),
            "service_log": redact_path(paths.service_log),
            "job_count": _job_count(paths.data_dir),
        },
        "data_schema": {
            # There is no unified job-state schema version; record that honestly.
            "job_state": "unversioned",
            "runtime": {"declared": _core_schema_declared("RUNTIME_SCHEMA_VERSION"),
                        "observed": observed["runtime"]},
            "journal": {"declared": _journal_schema_declared(),
                        "observed": observed["journal"]},
            "translation_drafts": {
                "declared": _core_schema_declared("TRANSLATION_DRAFTS_VERSION"),
                "observed": observed["translation_drafts"]},
            "source": f"observed from {observed['jobs_scanned']} job(s) in the data directory",
        },
    }


# --- error summary ---------------------------------------------------------


def collect_error_summary(paths: desktop_paths.DesktopPaths,
                          home: Path | None = None,
                          limit: int = ERROR_LINE_LIMIT) -> list[str]:
    """Filtered tail of the app's own logs; never includes document content."""
    lines: list[str] = []
    for log_path in (paths.launcher_log, paths.service_log):
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines()[-ERROR_SCAN_LIMIT:]:
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if any(marker in lowered for marker in _ERROR_MARKERS):
                lines.append(f"{log_path.name}: {_redact_text(stripped, home)}")
    return lines[-limit:]


# --- reporting -------------------------------------------------------------


def write_diagnostics(paths: desktop_paths.DesktopPaths, *,
                      pywebview_version: str | None = None) -> Path:
    """Write `<log_dir>/diagnostics.json`; returns the path (best effort)."""
    report = DiagnosticsReport(
        facts=collect(paths=paths, pywebview_version=pywebview_version),
        errors=collect_error_summary(paths),
    )
    target = paths.log_dir / DIAGNOSTICS_FILE
    try:
        paths.log_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(report.to_json() + "\n", encoding="utf-8")
    except OSError:  # pragma: no cover - diagnostics must never break startup
        return target
    return target


def collect_tools() -> dict[str, Any]:
    """External-tool capability state, from the *actual* launch environment.

    Roadmap §14: a missing optional tool must produce an accurate capability
    state (never NOT_RUN turned into PASS).  This imports the business core, so
    it is only used by the explicit export path, not at app startup.
    """
    tools: dict[str, Any] = {}
    try:
        import core

        tesseract = core._find_tesseract()
        tools["tesseract"] = {
            "found": bool(tesseract),
            "path": redact_path(tesseract) if tesseract else "",
            "languages": core.installed_ocr_languages() if tesseract else [],
        }
        soffice = core._find_soffice()
        tools["libreoffice"] = {
            "found": bool(soffice),
            "path": redact_path(soffice) if soffice else "",
            # LibreOffice is optional; absent means the render QA stays NOT_RUN.
            "render_qa": "available" if soffice else "NOT_RUN",
        }
    except Exception as exc:  # pragma: no cover - diagnostics must never fail
        tools["error"] = f"{type(exc).__name__}: {exc}"
    return tools


def export_bundle(destination: str | Path, *,
                  paths: desktop_paths.DesktopPaths | None = None,
                  pywebview_version: str | None = None) -> Path:
    """Write a redacted diagnostics bundle the user can attach to a report."""
    paths = paths or desktop_paths.resolve_paths()
    target = Path(destination).expanduser()
    facts = collect(paths=paths, pywebview_version=pywebview_version)
    facts["tools"] = collect_tools()
    report = DiagnosticsReport(facts=facts, errors=collect_error_summary(paths))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report.to_json() + "\n", encoding="utf-8")
    return target


def forbidden_sources() -> Iterable[str]:
    """Sources the diagnostics path must never read (used by tests)."""
    return _FORBIDDEN_SOURCES
