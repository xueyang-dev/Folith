"""Data-safety constraints for the desktop work (roadmap §3.2).

Roadmap §3.2: "临时文件 / 可删缓存与任务资产分开，清缓存不能删除源文件、检查点或交付快照"。

P5 added a cache-clearing action. It may only touch the dedicated derived
cache entries, never a job's source, state, checkpoints or delivery snapshots.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core  # noqa: E402

TASK_ASSETS = ("delivery_snapshots", "state.json", "source.bin",
               "source_cleanup_checkpoint", "state_journal")
DESTRUCTIVE = re.compile(r"rmtree\(|\.unlink\(|os\.remove\(")
# Journal rotation retires its own compacted segments by design.
ALLOWED = {"folith/storage/state_journal.py"}


def _product_sources():
    for path in sorted((ROOT / "folith").rglob("*.py")):
        yield path
    for name in ("app.py", "core.py"):
        yield ROOT / name


def test_shared_ocr_cache_lives_outside_job_assets(tmp_path, monkeypatch):
    """The reusable cache must be a dedicated subtree, not part of a job's data."""
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    cache = core.ocr_profile_cache_dir(b"some-source-bytes")
    assert cache is not None
    assert cache.parent.parent == tmp_path, "cache must sit in its own top-level subtree"
    assert cache.parent.name == core.OCR_PROFILE_CACHE_DIRNAME
    # A job directory is `<output>/<job_id>`: the cache must never look like one.
    assert cache.parent.name != cache.parent.parent.name


def test_no_destructive_call_targets_task_assets():
    offenders = []
    for path in _product_sources():
        relative = str(path.relative_to(ROOT))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not DESTRUCTIVE.search(line):
                continue
            if any(asset in line for asset in TASK_ASSETS) and relative not in ALLOWED:
                offenders.append(f"{relative}:{number}: {line.strip()[:120]}")
    assert not offenders, "destructive call sites touching task assets: " + "; ".join(offenders)


def test_cache_clearing_preserves_all_task_assets(tmp_path, monkeypatch):
    from folith.qt import support

    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    job = tmp_path / "0123456789abcdef"
    retained = {}
    for name in TASK_ASSETS:
        path = job / name
        if name.endswith(".json") or name.endswith(".bin"):
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            path.mkdir(parents=True, exist_ok=True)
            path = path / "evidence.dat"
        payload = ("preserve " + name).encode()
        path.write_bytes(payload)
        retained[path] = payload
    (tmp_path / "ocr_profile_cache").mkdir()
    (tmp_path / "ocr_profile_cache" / "page.json").write_bytes(b"cache")
    (tmp_path / "translation_cache.json").write_bytes(b"cache")
    (tmp_path / "knowledge_cache.json").write_bytes(b"cache")
    monkeypatch.setattr(support, "running_jobs", lambda: [])
    assert support.clear_caches() == 15
    assert all(path.read_bytes() == payload for path, payload in retained.items())
    assert all(not (tmp_path / name).exists() for name in support.CACHE_ENTRIES)
