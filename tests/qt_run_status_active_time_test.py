"""Finished-run wording uses work time, not the wall clock (#320).

Before #320 the top bar derived the duration from ``started_at`` → ``finished_at``,
so a laptop suspended for the weekend reported the sleep as work.  #320 records a
heartbeat-based active time; the UI must prefer it and keep the old wording when the
record is absent (pre-#320 history, or a build without the accessor).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

import core  # noqa: E402
from folith.qt import views  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
#: The task the UI is showing; 200 minutes of wall clock, 135 minutes of work.
WALL_CLOCK_MINUTES = 200
ACTIVE_MINUTES = 135


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


def _runtime(**raw) -> dict:
    return {
        "status": "completed",
        "runtime": {
            "started_at": _iso(WALL_CLOCK_MINUTES),
            "finished_at": _iso(0),
            **raw,
        },
    }


def _line(runtime: dict, job_id: str = "job-1") -> str:
    _tone, line = views.run_status_view(
        runtime, note="", running=False, completed=1, total=1, unit="段",
        now=NOW.timestamp(), job_id=job_id)
    return line


def _write_history(job_dir: Path, entries: list[dict]) -> None:
    perf = job_dir / ".performance"
    perf.mkdir(parents=True, exist_ok=True)
    (perf / "history.json").write_text(json.dumps(entries, ensure_ascii=False),
                                       encoding="utf-8")


def _use_job_dir(monkeypatch, job_dir: Path) -> None:
    monkeypatch.setattr(core, "job_dir", lambda job_id: job_dir)


def _silence_live_accessor(monkeypatch) -> None:
    """Make sure a developer's running app cannot leak a live value into the test."""
    monkeypatch.setattr(views, "_trace_active_seconds", lambda job_id: None)


def test_active_time_wins_over_the_wall_clock(app, tmp_path, monkeypatch):
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)
    _write_history(tmp_path, [{
        "run_id": "run-1",
        "task_id": "job-1",
        "started_at": _iso(WALL_CLOCK_MINUTES),
        "finished_at": _iso(0),
        "active_seconds": ACTIVE_MINUTES * 60,
        "interrupted_seconds": (WALL_CLOCK_MINUTES - ACTIVE_MINUTES) * 60,
        "elapsed_wall_clock_s": WALL_CLOCK_MINUTES * 60,
    }])

    assert _line(_runtime()) == "已完成 · 135 分钟"
    assert _line(_runtime()) != "已完成 · 200 分钟"


def test_live_trace_accessor_is_preferred(app, tmp_path, monkeypatch):
    """#320's public accessor wins while the run is still tracked in-process."""
    from folith import trace as trace_module

    _use_job_dir(monkeypatch, tmp_path)  # no history on disk at all
    manager = trace_module.trace
    monkeypatch.setattr(manager, "active_seconds",
                        lambda job_id=None: ACTIVE_MINUTES * 60, raising=False)

    assert _line(_runtime()) == "已完成 · 135 分钟"


def test_no_record_keeps_the_old_wall_clock_wording(app, tmp_path, monkeypatch):
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)

    assert _line(_runtime()) == "已完成 · 200 分钟"


def test_pre_320_history_without_active_seconds_falls_back(app, tmp_path, monkeypatch):
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)
    _write_history(tmp_path, [{
        "run_id": "run-1",
        "task_id": "job-1",
        "finished_at": _iso(0),
        "wall_time_s": WALL_CLOCK_MINUTES * 60,
    }])

    assert _line(_runtime()) == "已完成 · 200 分钟"


def test_older_segment_is_not_used_for_a_newer_run(app, tmp_path, monkeypatch):
    """A resumed task keeps one history file; the older run must not stand in."""
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)
    _write_history(tmp_path, [{
        "task_id": "job-1",
        "finished_at": _iso(90),
        "active_seconds": 20 * 60,
    }])

    assert _line(_runtime()) == "已完成 · 200 分钟"


def test_active_time_is_shown_even_without_started_at(app, tmp_path, monkeypatch):
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)
    _write_history(tmp_path, [{
        "task_id": "job-1",
        "finished_at": _iso(0),
        "active_seconds": ACTIVE_MINUTES * 60,
    }])
    runtime = {"status": "completed",
               "runtime": {"finished_at": _iso(0)}}

    assert _line(runtime) == "已完成 · 135 分钟"


def test_library_run_view_passes_the_job_id_through(app, tmp_path, monkeypatch):
    """The lightweight home/library row path must use the same active-time source."""
    _use_job_dir(monkeypatch, tmp_path)
    _silence_live_accessor(monkeypatch)
    _write_history(tmp_path, [{
        "task_id": "job-1",
        "finished_at": _iso(0),
        "active_seconds": ACTIVE_MINUTES * 60,
    }])

    class _Row:
        job_id = "job-1"
        runtime_status = "completed"
        status_label = "已完成"
        error = ""
        active = False
        runtime = {"started_at": _iso(WALL_CLOCK_MINUTES), "finished_at": _iso(0)}

    _tone, line = views.library_run_view(_Row())
    assert line == "已完成 · 135 分钟"
