"""D1 legacy data adoption (roadmap §3.2).

Safety properties under test: the original directory is never modified, the whole
job asset set travels (not just state.json), existing destination jobs are never
overwritten, a held worker lock blocks the import, and a failed verification
leaves the destination exactly as it was.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import legacy_import  # noqa: E402


def _legacy_job(root: Path, job: str, *, text: str = "旧译文",
                corrupt_state: bool = False) -> Path:
    path = root / job
    (path / "delivery_snapshots" / "v1").mkdir(parents=True, exist_ok=True)
    state = {"filename": f"{job}.docx", "pairs": [{"source": "src", "target": text}],
             "delivery_status": "final"}
    (path / "state.json").write_text(
        "not json" if corrupt_state else json.dumps(state, ensure_ascii=False),
        encoding="utf-8")
    (path / "state_journal.jsonl").write_text(
        json.dumps({"schema": 1, "pairs": []}) + "\n", encoding="utf-8")
    (path / "translation_drafts.json").write_text(
        json.dumps({"version": 1, "drafts": [{"segment_id": "s1", "text": text}]}),
        encoding="utf-8")
    (path / "source.bin").write_bytes(b"SOURCE-BYTES-" + job.encode())
    (path / "delivery_snapshots" / "v1" / "translation.docx").write_bytes(b"DELIVERED")
    return path


def _tree(root: Path) -> dict[str, int]:
    return {str(p.relative_to(root)): p.stat().st_size
            for p in root.rglob("*") if p.is_file()}


def test_plan_lists_jobs_conflicts_and_credentials(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a")
    _legacy_job(source, "job-b")
    (source / "provider_config.json").write_text('{"api_key": "sk-x"}', encoding="utf-8")
    (source / ".instance.lock").write_text("", encoding="utf-8")
    destination.mkdir()
    (destination / "job-b").mkdir()

    plan = legacy_import.plan_import(source, destination)
    assert plan.jobs == ["job-a"]
    assert plan.conflicts == ["job-b"]
    assert set(plan.excluded) == {"provider_config.json", ".instance.lock"}


def test_import_copies_the_whole_asset_set_and_keeps_the_original(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a")
    (source / "provider_config.json").write_text('{"api_key": "sk-secret"}', encoding="utf-8")
    before = _tree(source)

    report = legacy_import.import_legacy_data(source, destination)

    assert report.ok is True and report.error is None
    assert report.copied_jobs == ["job-a"]
    assert "provider_config.json" in report.excluded
    # The whole asset set travelled, not just state.json.
    for name in ("state.json", "state_journal.jsonl", "translation_drafts.json",
                 "source.bin", "delivery_snapshots/v1/translation.docx"):
        assert (destination / "job-a" / name).exists(), name
    # Credentials did not travel.
    assert not (destination / "provider_config.json").exists()
    # The original is byte-identical and still complete.
    assert _tree(source) == before
    # No staging leftovers.
    assert not list(destination.parent.glob(f"{legacy_import.STAGING_PREFIX}*"))


def test_import_never_overwrites_existing_jobs(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a", text="旧数据里的译文")
    _legacy_job(destination, "job-a", text="当前数据目录里的译文")

    report = legacy_import.import_legacy_data(source, destination)

    assert report.ok is True
    assert report.copied_jobs == []
    assert report.skipped_conflicts == ["job-a"]
    state = json.loads((destination / "job-a" / "state.json").read_text(encoding="utf-8"))
    assert state["pairs"][0]["target"] == "当前数据目录里的译文"


def test_import_refuses_while_the_old_instance_is_running(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a")
    lock_path = source / "job-a" / legacy_import.WORKER_LOCK
    holder = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time,pathlib; sys.path.insert(0, %r);"
        "from folith import job_lock;"
        # The stream must stay referenced: dropping it closes the file and
        # releases the flock immediately (CPython refcounting).
        "stream = job_lock.acquire(pathlib.Path(%r));"
        "print('locked' if stream else 'none', flush=True); time.sleep(30)"
        % (str(ROOT), str(lock_path)),
    ], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert legacy_import.plan_import(source, destination).busy_jobs == ["job-a"]
        report = legacy_import.import_legacy_data(source, destination)
        assert report.ok is False
        assert "旧实例仍在运行" in (report.error or "")
        assert not (destination / "job-a").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=10)


def test_failed_verification_leaves_the_destination_untouched(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-good")
    _legacy_job(source, "job-bad", corrupt_state=True)
    destination.mkdir()
    (destination / "existing.txt").write_text("keep me", encoding="utf-8")
    before = _tree(destination)

    report = legacy_import.import_legacy_data(source, destination)

    assert report.ok is False
    assert "job-bad" in (report.error or "")
    assert _tree(destination) == before, "a failed import must not change the destination"
    assert not (destination / "job-good").exists()
    assert not list(destination.parent.glob(f"{legacy_import.STAGING_PREFIX}*"))
    # The source survives the failure.
    assert (source / "job-good" / "state.json").exists()


def test_dry_run_copies_nothing(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a")
    report = legacy_import.import_legacy_data(source, destination, dry_run=True)
    assert report.ok is True and report.dry_run is True
    assert report.copied_jobs == ["job-a"]
    assert not destination.exists()
    assert "试运行" in report.describe()


def test_import_rejects_unsafe_paths(tmp_path):
    source = tmp_path / "legacy"
    _legacy_job(source, "job-a")
    with pytest.raises(legacy_import.LegacyImportError):
        legacy_import.plan_import(source, source)
    with pytest.raises(legacy_import.LegacyImportError):
        legacy_import.plan_import(source, source / "inner")
    with pytest.raises(legacy_import.LegacyImportError):
        legacy_import.plan_import(tmp_path / "missing", tmp_path / "desktop")


def test_verify_import_reads_the_adopted_jobs(tmp_path):
    source = tmp_path / "legacy"
    destination = tmp_path / "desktop"
    _legacy_job(source, "job-a")
    report = legacy_import.import_legacy_data(source, destination)
    assert report.ok is True
    verified = legacy_import.verify_import(destination, report.copied_jobs)
    assert verified["ok"] is True
    assert verified["jobs"]["job-a"] == []
