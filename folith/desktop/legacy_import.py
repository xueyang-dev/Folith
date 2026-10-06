"""Adopt an existing data directory ("接入旧数据目录", D1, roadmap §3.2).

Rules taken directly from the roadmap:

* the user picks an existing ``outputs/`` directory; the **original is kept**
  (copy, never move/delete);
* the whole job asset set travels — source files, state, journal, edit drafts and
  task assets — not just ``state.json``;
* the old instance must be stopped first (a held worker lock means it is still
  writing);
* nothing is switched until the copy has been verified as readable, and a failed
  import never overwrites existing data;
* credentials are not copied (roadmap §3.4 excludes them from transfers);
* no disk scanning and no general migration framework: only this explicit,
  user-initiated directory adoption.
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from folith import job_lock

WORKER_LOCK = ".worker.lock"
CREDENTIAL_FILES = ("provider_config.json",)
APP_STATE_FILES = (".instance.lock", "instance.json")
STAGING_PREFIX = ".import-staging-"
DRAFTS_FILE = "translation_drafts.json"
JOURNAL_FILE = "state_journal.jsonl"


class LegacyImportError(RuntimeError):
    """The import cannot proceed (or failed) — the destination is untouched."""


@dataclass
class ImportPlan:
    source: Path
    destination: Path
    jobs: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    busy_jobs: list[str] = field(default_factory=list)
    loose_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "source": str(self.source),
            "destination": str(self.destination),
            "jobs": list(self.jobs),
            "conflicts": list(self.conflicts),
            "excluded": list(self.excluded),
            "busy_jobs": list(self.busy_jobs),
            "loose_files": list(self.loose_files),
        }


@dataclass
class ImportReport:
    ok: bool
    source: Path
    destination: Path
    copied_jobs: list[str] = field(default_factory=list)
    skipped_conflicts: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    verified: dict = field(default_factory=dict)
    error: str | None = None
    dry_run: bool = False
    source_untouched: bool = True
    started_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "source": str(self.source),
            "destination": str(self.destination),
            "copied_jobs": list(self.copied_jobs),
            "skipped_conflicts": list(self.skipped_conflicts),
            "excluded": list(self.excluded),
            "verified": dict(self.verified),
            "error": self.error,
            "source_untouched": self.source_untouched,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def describe(self) -> str:
        if self.error:
            return f"接入失败（原目录未改动）：{self.error}"
        if self.dry_run:
            return (f"试运行：可接入 {len(self.copied_jobs)} 个任务，"
                    f"跳过 {len(self.skipped_conflicts)} 个已存在任务")
        return (f"已接入 {len(self.copied_jobs)} 个任务；"
                f"跳过 {len(self.skipped_conflicts)} 个已存在任务；"
                f"未复制凭据 {len(self.excluded)} 项")


# --- planning --------------------------------------------------------------


def _job_dirs(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.iterdir()
                  if entry.is_dir() and not entry.name.startswith("."))


def _lock_is_held(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        stream = job_lock.acquire(path, blocking=False)
    except OSError:  # pragma: no cover - defensive
        return False
    if stream is None:
        return True
    try:
        stream.close()
    except OSError:  # pragma: no cover - defensive
        pass
    return False


def busy_jobs(source: Path) -> list[str]:
    """Jobs whose worker lock is still held: the old instance is running."""
    return [job for job in _job_dirs(source)
            if _lock_is_held(source / job / WORKER_LOCK)]


def plan_import(source: str | Path, destination: str | Path) -> ImportPlan:
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not source.is_dir():
        raise LegacyImportError(f"旧数据目录不存在或不是目录：{source}")
    if source == destination:
        raise LegacyImportError("旧数据目录与当前数据目录相同，无需接入")
    if destination in source.parents or source in destination.parents:
        raise LegacyImportError("旧数据目录与当前数据目录不能互相包含")

    plan = ImportPlan(source=source, destination=destination)
    for name in sorted(entry.name for entry in source.iterdir() if entry.is_file()):
        if name in CREDENTIAL_FILES:
            plan.excluded.append(name)
        elif name in APP_STATE_FILES:
            plan.excluded.append(name)
        else:
            plan.loose_files.append(name)
    for job in _job_dirs(source):
        if (destination / job).exists():
            plan.conflicts.append(job)
        else:
            plan.jobs.append(job)
    plan.busy_jobs = busy_jobs(source)
    return plan


# --- verification ----------------------------------------------------------


def _job_manifest(root: Path) -> tuple[int, int]:
    """(file count, total bytes) for a directory tree."""
    count = 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            count += 1
            total += path.stat().st_size
    return count, total


def _verify_job(staged: Path, original: Path) -> list[str]:
    """Readability checks; returns a list of problems (empty = fine)."""
    problems: list[str] = []
    state = staged / "state.json"
    if state.exists():
        try:
            payload = json.loads(state.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                problems.append("state.json is not an object")
            elif "pairs" not in payload and "filename" not in payload:
                problems.append("state.json has no job fields")
        except (OSError, ValueError) as exc:
            problems.append(f"state.json unreadable: {exc}")
    journal = staged / JOURNAL_FILE
    if journal.exists():
        try:
            for line in journal.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)
        except (OSError, ValueError) as exc:
            problems.append(f"journal unreadable: {exc}")
    drafts = staged / DRAFTS_FILE
    if drafts.exists():
        try:
            payload = json.loads(drafts.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "drafts" not in payload:
                problems.append("drafts file has no drafts list")
        except (OSError, ValueError) as exc:
            problems.append(f"drafts unreadable: {exc}")

    expected_count, expected_bytes = _job_manifest(original)
    actual_count, actual_bytes = _job_manifest(staged)
    if (expected_count, expected_bytes) != (actual_count, actual_bytes):
        problems.append(
            f"copy mismatch: expected {expected_count} files/{expected_bytes} bytes, "
            f"got {actual_count}/{actual_bytes}"
        )
    return problems


# --- import ----------------------------------------------------------------


def import_legacy_data(source: str | Path, destination: str | Path, *,
                       dry_run: bool = False,
                       on_progress: Callable[[str], None] | None = None) -> ImportReport:
    """Copy a legacy data directory into the desktop data directory.

    Never overwrites: jobs already present in the destination are skipped.  The
    copy is staged inside the destination's parent, verified, and only then moved
    into place — a failure removes the staging directory and leaves the
    destination exactly as it was.
    """
    report = ImportReport(ok=False, source=Path(source), destination=Path(destination),
                          dry_run=dry_run, started_at=time.time())

    def note(message: str) -> None:
        if on_progress is not None:
            try:
                on_progress(message)
            except Exception:  # pragma: no cover - logging must not break import
                pass

    try:
        plan = plan_import(source, destination)
    except LegacyImportError as exc:
        report.error = str(exc)
        report.finished_at = time.time()
        return report

    report.source = plan.source
    report.destination = plan.destination
    report.copied_jobs = list(plan.jobs)
    report.skipped_conflicts = list(plan.conflicts)
    report.excluded = list(plan.excluded)

    if plan.busy_jobs:
        report.error = ("旧实例仍在运行（任务 " + "、".join(plan.busy_jobs)
                        + " 的 worker 锁未释放）；请先退出旧应用再接入")
        report.finished_at = time.time()
        return report
    if dry_run:
        report.ok = True
        report.finished_at = time.time()
        return report

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f"{STAGING_PREFIX}{int(time.time())}"
    created: list[str] = []
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        verified: dict = {}
        for job in plan.jobs:
            note(f"copying job {job}")
            shutil.copytree(plan.source / job, staging / job, symlinks=False)
            problems = _verify_job(staging / job, plan.source / job)
            verified[job] = {"ok": not problems, "problems": problems}
            if problems:
                raise LegacyImportError(
                    f"任务 {job} 复制校验失败：" + "；".join(problems))
        for name in plan.loose_files:
            shutil.copy2(plan.source / name, staging / name)
            verified[name] = {"ok": True, "problems": []}
        report.verified = verified

        # All verified: move into place.  Existing entries are never replaced.
        destination.mkdir(parents=True, exist_ok=True)
        for job in plan.jobs:
            target = destination / job
            if target.exists():  # pragma: no cover - guarded by the plan
                report.skipped_conflicts.append(job)
                continue
            (staging / job).replace(target)
            created.append(job)
        for name in plan.loose_files:
            target = destination / name
            if not target.exists():
                (staging / name).replace(target)
        report.copied_jobs = [job for job in plan.jobs if job in created]
        report.ok = True
    except (LegacyImportError, OSError, shutil.Error) as exc:
        report.error = str(exc)
        report.ok = False
    finally:
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:  # pragma: no cover - defensive
            pass
    report.finished_at = time.time()
    return report


def verify_import(destination: str | Path, jobs: Iterable[str]) -> dict:
    """Re-read the adopted jobs from their final location."""
    destination = Path(destination).expanduser().resolve()
    result: dict = {"ok": True, "jobs": {}}
    for job in jobs:
        problems = _verify_job(destination / job, destination / job)
        result["jobs"][job] = problems
        if problems:
            result["ok"] = False
    return result
