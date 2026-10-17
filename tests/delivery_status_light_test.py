"""The task list's delivery status skips asset hashing and repeated state loads."""
from __future__ import annotations

from pathlib import Path

import pytest

import core
from folith import snapshots
from folith.application import read_models
from folith.engine import delivery_state
from folith.storage import job_repository as repo


@pytest.fixture
def job_env(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(core, "OUTPUT_DIR", tmp_path)
    repo.invalidate_cache()
    delivery_state._LIGHT_STATUS_CACHE.clear()
    yield tmp_path
    repo.invalidate_cache()
    delivery_state._LIGHT_STATUS_CACHE.clear()


def _frozen_job(job_id: str):
    state = core.new_job_state("sample.docx")
    state["pairs"] = [{"source": "Hello", "target": "你好", "initial_target": "你好"}]
    state["delivery_status"] = "final"
    core.save_job_state(job_id, state)
    loaded = core.load_job_state(job_id)
    snapshots.create_snapshot(core.job_dir(job_id), job_id, loaded,
                              {"translation.txt": "你好".encode("utf-8")}, {}, {})
    return loaded


def test_light_status_matches_strict_without_hashing_or_reloading(job_env, monkeypatch):
    job_id = "light-job"
    _frozen_job(job_id)
    strict = core.delivery_snapshot_status(job_id)
    assert strict["current"] and not strict["diverged"]

    def no_hashing(*_args, **_kwargs):
        raise AssertionError("the light status must not read or hash assets")

    monkeypatch.setattr(snapshots, "load_asset", no_hashing)
    loads = []
    real_load = core.load_job_state
    monkeypatch.setattr(core, "load_job_state",
                        lambda *a, **k: loads.append(a) or real_load(*a, **k))

    first = core.delivery_snapshot_status_light(job_id)
    second = core.delivery_snapshot_status_light(job_id)
    for status in (first, second):
        assert status["current"] is True
        assert status["diverged"] is False
        assert status["latest"]["snapshot_version"] == strict["latest"]["snapshot_version"]
    assert len(loads) == 1  # the unchanged state is not reloaded

    # Editing the working version is still noticed.
    state = real_load(job_id)
    state["pairs"][0]["target"] = "您好"
    core.save_job_state(job_id, state)
    edited = core.delivery_snapshot_status_light(job_id)
    assert edited["diverged"] is True and edited["current"] is False
    assert len(loads) == 2


def test_light_status_reports_missing_asset(job_env):
    job_id = "light-missing"
    _frozen_job(job_id)
    snapshots.asset_path(core.job_dir(job_id), 1, "translation.txt").unlink()
    status = core.delivery_snapshot_status_light(job_id)
    assert status["integrity"] is False and status["current"] is False


def test_task_list_row_uses_light_status(job_env, monkeypatch):
    job_id = "light-row"
    _frozen_job(job_id)
    strict_calls = []
    monkeypatch.setattr(core, "delivery_snapshot_status",
                        lambda *a, **k: strict_calls.append(a) or {})
    row = read_models._summary_row(job_id, {"filename": "sample.docx"})
    assert strict_calls == []
    assert row.status_label == "已冻结交付 v1"
