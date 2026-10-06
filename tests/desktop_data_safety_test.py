"""Data-safety constraints for the desktop work (roadmap §3.2).

Roadmap §3.2: "临时文件 / 可删缓存与任务资产分开，清缓存不能删除源文件、检查点或交付快照"。

The product currently has **no** cache-clearing action, so the requirement is
`NOT APPLICABLE` today — but the *separation* it depends on is real and must not
regress: a future clear-cache feature may only touch the dedicated cache subtree,
never a job's assets.  These tests pin that down.
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


def test_cache_clearing_still_does_not_exist():
    """If a clear-cache surface is added, this test must be updated with its safety case."""
    pattern = re.compile(r"清理缓存|清除缓存|清空缓存|删除缓存|clear_cache")
    hits = []
    for path in _product_sources():
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, ("a cache-clearing surface appeared; verify it cannot delete source "
                      f"files, checkpoints or delivery snapshots, then update this test: {hits}")
