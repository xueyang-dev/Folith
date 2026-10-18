"""D1 save & stop: exit-flow inspection and commit planning (roadmap §3.3).

The WebView-driven end-to-end evidence lives in `scripts/desktop_d1_exit_probe.py`
and the D1 evidence log; these tests cover the pure/read-only logic and the
contract with the existing product artifacts.
"""
from __future__ import annotations

import json

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from folith.desktop import exit_flow  # noqa: E402


def _write_drafts(data_dir: Path, job_id: str, drafts: list[dict]) -> Path:
    path = data_dir / job_id / exit_flow.DRAFTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "job_id": job_id, "drafts": drafts},
                               ensure_ascii=False), encoding="utf-8")
    return path


def test_active_statuses_matches_the_business_core():
    """The local copy must not drift from core.RUNTIME_ACTIVE_STATUSES."""
    import core

    assert set(exit_flow.ACTIVE_STATUSES) == set(core.RUNTIME_ACTIVE_STATUSES)


def test_active_job_id_from_url():
    assert exit_flow.active_job_id_from_url("http://127.0.0.1:1234/?task=abc&section=translation") == "abc"
    assert exit_flow.active_job_id_from_url("http://127.0.0.1:1234/?job=xyz") == "xyz"
    assert exit_flow.active_job_id_from_url("http://127.0.0.1:1234/") is None
    assert exit_flow.active_job_id_from_url(None) is None


def test_read_pending_drafts_is_tolerant(tmp_path):
    job = "job1"
    assert exit_flow.read_pending_drafts(tmp_path, job) == ()
    path = tmp_path / job / exit_flow.DRAFTS_FILE
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert exit_flow.read_pending_drafts(tmp_path, job) == ()
    path.write_text(json.dumps({"drafts": [{"segment_id": "7", "text": "新译文"}]}), encoding="utf-8")
    drafts = exit_flow.read_pending_drafts(tmp_path, job)
    assert len(drafts) == 1 and drafts[0].segment_id == "7" and drafts[0].text == "新译文"


def test_snapshot_reports_running_task_and_worker_lock(tmp_path):
    job = "job2"
    (tmp_path / job).mkdir(parents=True)
    (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
        json.dumps({"status": "running", "stage": "translation"}), encoding="utf-8")
    state = exit_flow.snapshot(tmp_path, job)
    assert state.active_status == "running" and state.task_running is True

    (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
        json.dumps({"status": "completed"}), encoding="utf-8")
    assert exit_flow.snapshot(tmp_path, job).task_running is False

    # No worker running in this process: the lock must read as free.
    assert exit_flow.worker_lock_held(tmp_path, job) is False


def test_worker_lock_is_detected_while_held(tmp_path):
    """A lock held by another process must be reported as held."""
    job = "job3"
    (tmp_path / job).mkdir(parents=True)
    lock_path = tmp_path / job / exit_flow.WORKER_LOCK_FILE
    holder = subprocess.Popen([
        sys.executable, "-c",
        "import sys,time,pathlib; sys.path.insert(0, %r);"
        "from folith import job_lock;"
        "stream = job_lock.acquire(pathlib.Path(%r));"
        "print('locked', flush=True); time.sleep(30)"
        % (str(ROOT), str(lock_path)),
    ], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "locked"
        assert exit_flow.worker_lock_held(tmp_path, job) is True
    finally:
        holder.terminate()
        holder.wait(timeout=10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and exit_flow.worker_lock_held(tmp_path, job):
        time.sleep(0.2)
    assert exit_flow.worker_lock_held(tmp_path, job) is False


class _FakeWindow:
    """Minimal stand-in for a pywebview window recording JS calls."""

    def __init__(self, script_results):
        self.script_results = list(script_results)
        self.calls: list[str] = []

    def evaluate_js(self, script):
        self.calls.append(script)
        if not self.script_results:
            raise RuntimeError("no scripted result left")
        return self.script_results.pop(0)


def test_flush_focused_editor_reports_what_it_did():
    window = _FakeWindow([{"tag": "textarea", "value_length": 12, "value_tail": "abc"}])
    result = exit_flow.flush_focused_editor(window)
    assert result["flushed"] is True and result["tag"] == "textarea"

    failing = _FakeWindow([])
    failing.evaluate_js = lambda script: (_ for _ in ()).throw(RuntimeError("boom"))
    assert exit_flow.flush_focused_editor(failing)["flushed"] is False


def test_commit_pending_drafts_confirms_each_write(tmp_path):
    job = "job4"
    _write_drafts(tmp_path, job, [{"segment_id": "1", "text": "第一段新译文"}])
    window = _FakeWindow([{"clicked": True, "value_length": 6}])
    # The UI commit is simulated by clearing the draft file when the click lands.
    original = exit_flow.evaluate

    def fake_evaluate(win, script):
        _write_drafts(tmp_path, job, [])
        return original(win, script)

    exit_flow.evaluate = fake_evaluate
    try:
        result = exit_flow.commit_pending_drafts(window, tmp_path, job, timeout=2)
    finally:
        exit_flow.evaluate = original
    assert result["confirmed"] is True
    assert result["committed_segments"] == ["1"]
    assert result["remaining"] == 0


def test_commit_pending_drafts_refuses_to_claim_success(tmp_path):
    job = "job5"
    _write_drafts(tmp_path, job, [{"segment_id": "2", "text": "第二段新译文"}])
    window = _FakeWindow([{"clicked": False, "reason": "save-button-disabled"}])
    window.evaluate_js = lambda script: {"clicked": False, "reason": "save-button-disabled"}
    result = exit_flow.commit_pending_drafts(window, tmp_path, job, timeout=0.5)
    assert result["confirmed"] is False
    assert result["remaining"] == 1
    assert "save-button-disabled" in result["failures"]


@pytest.mark.parametrize("pending_reason", ["save-button-disabled", "no-matching-row"])
def test_commit_waits_for_the_editor_after_the_draft_is_recorded(tmp_path, pending_reason):
    job = "draft-before-rerender"
    path = _write_drafts(tmp_path, job, [{"segment_id": "1", "text": "尚未提交的编辑"}])

    class RerenderingWindow:
        attempts = 0

        def evaluate_js(self, script):
            self.attempts += 1
            if self.attempts == 1:
                return {"clicked": False, "reason": pending_reason}
            path.write_text(json.dumps({"drafts": []}), encoding="utf-8")
            return {"clicked": True}

    result = exit_flow.commit_pending_drafts(RerenderingWindow(), tmp_path, job, timeout=1)
    assert result["confirmed"] is True
    assert result["committed_segments"] == ["1"]
    assert result["failures"] == []


def test_commit_does_not_accept_a_persistently_missing_editor_row(tmp_path):
    job = "missing-editor"
    _write_drafts(tmp_path, job, [{"segment_id": "1", "text": "未提交的编辑"}])
    window = _FakeWindow([])
    window.evaluate_js = lambda script: {"clicked": False, "reason": "no-matching-row"}
    result = exit_flow.commit_pending_drafts(window, tmp_path, job, timeout=0.02)
    assert result["confirmed"] is False
    assert result["remaining"] == 1
    assert result["committed_segments"] == []
    assert result["failures"] == ["no-matching-row"]


def test_run_exit_flow_without_a_job_is_a_no_op():
    window = _FakeWindow([""])
    report = exit_flow.run_exit_flow(window, data_dir=Path("/nonexistent"))
    assert report["action"] == "nothing-to-save"
    assert report["job_id"] is None


def test_run_exit_flow_reports_unsafe_exit_when_drafts_remain(tmp_path):
    job = "job6"
    _write_drafts(tmp_path, job, [{"segment_id": "9", "text": "未提交"}])
    # 1) job id, 2) blur result, 3) commit attempt (button missing)
    window = _FakeWindow([job, {"value_length": 0}, {"clicked": False, "reason": "no-matching-row"}])
    report = exit_flow.run_exit_flow(window, data_dir=tmp_path, flush_timeout=0.3,
                                     commit_timeout=0.3)
    assert report["safe_to_exit"] is False
    assert report["action"] == "incomplete"
    assert report["after"]["draft_count"] == 1
    assert "剩余草稿 1 处" in exit_flow.describe(report)


def test_run_exit_flow_cancels_a_running_task_before_committing(tmp_path):
    job = "job7"
    (tmp_path / job).mkdir(parents=True)
    (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
        json.dumps({"status": "running"}), encoding="utf-8")
    _write_drafts(tmp_path, job, [{"segment_id": "3", "text": "运行中的编辑"}])
    window = _FakeWindow([
        job,                                                    # current job
        {"value_length": 0},                                    # blur
        {"clicked": True, "via": "scoped"},                     # cancel click
        {"clicked": True, "value_length": 7},                   # commit click
    ])
    original = exit_flow.evaluate

    def fake_evaluate(win, script):
        if "runtime_cancel" in script:
            (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
                json.dumps({"status": "cancelled"}), encoding="utf-8")
        if "cat_row_" in script:
            _write_drafts(tmp_path, job, [])
        return original(win, script)

    exit_flow.evaluate = fake_evaluate
    try:
        report = exit_flow.run_exit_flow(window, data_dir=tmp_path, flush_timeout=0.3,
                                         cancel_timeout=2, commit_timeout=2)
    finally:
        exit_flow.evaluate = original
    assert report["cancel"]["cancel_clicked"] is True
    assert report["cancel"]["left_active_status"] is True
    assert report["commit"]["confirmed"] is True
    assert report["safe_to_exit"] is True
    assert report["action"] == "saved-and-stopped"


# --- D1-2b: jobs the window is not showing, and the timeout choice ---------


def test_active_job_ids_finds_jobs_not_shown_in_the_window(tmp_path):
    for job, status in (("a", "running"), ("b", "completed"), ("c", "waiting_external")):
        (tmp_path / job).mkdir(parents=True)
        (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
            json.dumps({"status": status}), encoding="utf-8")
    assert exit_flow.active_job_ids(tmp_path) == ["a", "c"]


def test_run_exit_flow_cancels_a_job_the_window_is_not_showing(tmp_path):
    """A task running in another session must still be stopped on exit."""
    job = "bg"
    (tmp_path / job).mkdir(parents=True)
    (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
        json.dumps({"status": "running"}), encoding="utf-8")
    window = _FakeWindow([
        "",                                                     # no ?task= in the URL
        True,                                                   # navigation JS
        True,                                                   # control visible?
        {"value_length": 0},                                    # blur
        {"clicked": True, "via": "scoped"},                     # cancel click
    ])
    original = exit_flow.evaluate

    def fake_evaluate(win, script):
        if "runtime_cancel" in script:
            (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
                json.dumps({"status": "cancelled"}), encoding="utf-8")
        return original(win, script)

    exit_flow.evaluate = fake_evaluate
    try:
        report = exit_flow.run_exit_flow(window, data_dir=tmp_path, flush_timeout=0.3,
                                         cancel_timeout=3, commit_timeout=1)
    finally:
        exit_flow.evaluate = original
    assert report["job_id"] == job
    assert report["job_source"] == "data-dir"
    assert report["navigated_to_job"] is True
    assert report["cancel"]["cancel_clicked"] is True
    assert report["safe_to_exit"] is True


def test_force_exit_is_offered_when_the_bounded_wait_is_exceeded(tmp_path):
    """Keep-waiting vs force-quit must be an explicit user choice."""
    job = "stuck"
    (tmp_path / job).mkdir(parents=True)
    (tmp_path / job / exit_flow.RUNTIME_STATE_FILE).write_text(
        json.dumps({"status": "running"}), encoding="utf-8")
    _write_drafts(tmp_path, job, [{"segment_id": "5", "text": "未提交的编辑"}])
    window = _FakeWindow([
        job,                                   # job id in the URL
        {"value_length": 0},                   # blur
        {"clicked": False, "reason": "cancel-control-not-found"},
        {"clicked": False, "reason": "no-matching-row"},
    ])
    asked: list[str] = []

    def refuse(_window, message):
        asked.append(message)
        return False

    report = exit_flow.run_exit_flow(window, data_dir=tmp_path, flush_timeout=0.3,
                                     cancel_timeout=0.6, commit_timeout=0.6,
                                     confirm=refuse)
    assert asked and "强制退出" in asked[0]
    assert report["forced"] is False
    assert report["safe_to_exit"] is False
    assert report["action"] == "incomplete"

    # Same situation, user chooses force-quit.
    window2 = _FakeWindow([
        job, {"value_length": 0},
        {"clicked": False, "reason": "cancel-control-not-found"},
        {"clicked": False, "reason": "no-matching-row"},
    ])
    report2 = exit_flow.run_exit_flow(window2, data_dir=tmp_path, flush_timeout=0.3,
                                      cancel_timeout=0.6, commit_timeout=0.6,
                                      confirm=lambda _w, _m: True)
    assert report2["forced"] is True
    assert report2["safe_to_exit"] is True
    assert report2["action"] == "forced-exit"
    assert "强制退出" in exit_flow.describe(report2)


# --- wiring: the close/quit path must register the exit flow ---------------


class _FakeEvent:
    def __init__(self, sink):
        self._sink = sink

    def __iadd__(self, item):
        self._sink.append(item)
        return self


def test_exit_flow_cancels_every_active_job(tmp_path, monkeypatch):
    """§3.3: stopping only the job in the window must not be reported as safe."""
    from folith.desktop import exit_flow

    data = tmp_path / "data"
    for job, status in (("job-a", "running"), ("job-b", "running")):
        (data / job).mkdir(parents=True)
        (data / job / "state.json").write_text('{"pairs": [], "paras": []}', encoding="utf-8")
        (data / job / "runtime_state.json").write_text(
            json.dumps({"status": status}), encoding="utf-8")

    cancelled = []

    monkeypatch.setattr(exit_flow, "current_job_id", lambda window: "job-a")
    monkeypatch.setattr(exit_flow, "flush_focused_editor",
                        lambda window: {"tag": "body", "value_tail": "", "value_length": 0})
    monkeypatch.setattr(exit_flow, "commit_pending_drafts",
                        lambda *a, **k: {"committed_segments": [], "remaining": 0})
    monkeypatch.setattr(exit_flow, "navigate_to_job", lambda window, job, **k: True)

    def fake_cancel(window, data_dir, job_id, *, timeout=30.0):
        cancelled.append(job_id)
        (data_dir / job_id / "runtime_state.json").write_text(
            json.dumps({"status": "cancelled"}), encoding="utf-8")
        return {"cancel_clicked": True, "worker_lock_released": True, "status": "cancelled"}

    monkeypatch.setattr(exit_flow, "cancel_running_task", fake_cancel)

    report = exit_flow.run_exit_flow(None, data_dir=data, cancel_timeout=0.1,
                                     commit_timeout=0.1, confirm=lambda *a: False)
    assert "job-a" in cancelled and "job-b" in cancelled, cancelled
    assert set(report["other_cancels"]) == {"job-b"}
    assert report["safe_to_exit"] is True
    assert report["action"] == "saved-and-stopped"


def test_exit_flow_reports_unsafe_when_another_job_keeps_running(tmp_path, monkeypatch):
    """A second task that will not stop must block the 'safe' verdict."""
    from folith.desktop import exit_flow

    data = tmp_path / "data"
    for job in ("job-a", "job-b"):
        (data / job).mkdir(parents=True)
        (data / job / "state.json").write_text('{"pairs": [], "paras": []}', encoding="utf-8")
        (data / job / "runtime_state.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8")

    monkeypatch.setattr(exit_flow, "current_job_id", lambda window: "job-a")
    monkeypatch.setattr(exit_flow, "flush_focused_editor",
                        lambda window: {"tag": "body", "value_tail": "", "value_length": 0})
    monkeypatch.setattr(exit_flow, "commit_pending_drafts",
                        lambda *a, **k: {"committed_segments": [], "remaining": 0})
    monkeypatch.setattr(exit_flow, "navigate_to_job", lambda window, job, **k: True)

    def fake_cancel_primary(window, data_dir, job_id, *, timeout=30.0):
        (data_dir / "job-a" / "runtime_state.json").write_text(
            json.dumps({"status": "cancelled"}), encoding="utf-8")
        return {"cancel_clicked": True, "worker_lock_released": True, "status": "cancelled"}

    # job-b never stops.
    monkeypatch.setattr(exit_flow, "cancel_running_task",
                        lambda window, data_dir, job_id, *, timeout=30.0: (
                            fake_cancel_primary(window, data_dir, job_id, timeout=timeout)
                            if job_id == "job-a" else
                            {"cancel_clicked": False, "worker_lock_released": False,
                             "status": "running"}))
    report = exit_flow.run_exit_flow(None, data_dir=data, cancel_timeout=0.1,
                                     commit_timeout=0.1, confirm=lambda *a: False)
    assert report["safe_to_exit"] is False, report
    assert report["action"] == "incomplete"
    assert "另有" in (report.get("force_prompt") or ""), report.get("force_prompt")
