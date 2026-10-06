#!/usr/bin/env python3
"""Waits for the runner's go-ahead, then closes the window and records the report.

Used to check the exit flow while **two** tasks are active at once.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run(window, context):  # noqa: D103
    os.environ.setdefault("FOLITH_EXIT_FORCE_PROMPT", "0")
    log_dir = Path(context["log_dir"])
    out_dir = log_dir.parent
    marker = out_dir / "go-exit"
    flow = log_dir / "exit-flow.json"
    evidence: dict = {"waited_for": str(marker)}

    # Optional cancel phase: the runner asks for a task's cancel to be requested
    # (but not waited for) so the exit flow meets a task that is mid-cancellation
    # with a model request still in flight.
    if os.environ.get("FOLITH_TWO_TASKS_CANCEL") == "1":
        go_cancel = out_dir / "go-cancel"
        cancel_job_file = out_dir / "cancel-job"
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline and not (go_cancel.exists() and cancel_job_file.exists()):
            time.sleep(2)
        job = (cancel_job_file.read_text(encoding="utf-8").strip()
               if cancel_job_file.exists() else "")
        evidence["cancel_job"] = job
        if job:
            import sys as _sys

            _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from folith.desktop import exit_flow as _exit_flow

            _exit_flow.navigate_to_job(window, job)
            click_script = (
                "(() => { const el = document.querySelector('.st-key-runtime_cancel_%s button');"
                " if (el && !el.disabled) { el.click(); return true; } return false; })()" % job)
            deadline = time.monotonic() + 180
            clicked = False
            while time.monotonic() < deadline:
                try:
                    if window.evaluate_js(click_script):
                        clicked = True
                        break
                except Exception:
                    pass
                time.sleep(1.5)
            evidence["cancel_clicked"] = clicked
            # Wait until the product has actually recorded the cancellation request.
            deadline = time.monotonic() + 180
            status = None
            while time.monotonic() < deadline:
                status = _exit_flow.read_runtime_status(Path(context["data_dir"]), job)
                if status == "cancelling":
                    break
                time.sleep(1)
            evidence["status_after_cancel_request"] = status
            (out_dir / "cancelling-ok").write_text(
                "1" if status == "cancelling" else "0", encoding="utf-8")

    # Optional pause phase: the runner asks for one task to be paused so the exit
    # flow meets a mixed "running + paused" state.
    if os.environ.get("FOLITH_TWO_TASKS_PAUSE") == "1":
        go_pause = out_dir / "go-pause"
        pause_job_file = out_dir / "pause-job"
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline and not (go_pause.exists() and pause_job_file.exists()):
            time.sleep(2)
        job = pause_job_file.read_text(encoding="utf-8").strip() if pause_job_file.exists() else ""
        evidence["pause_job"] = job
        if job:
            import sys as _sys

            _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from folith.desktop import exit_flow as _exit_flow

            _exit_flow.navigate_to_job(window, job)
            click_script = (
                "(() => { const el = document.querySelector('.st-key-runtime_pause_%s button');"
                " if (el && !el.disabled) { el.click(); return true; } return false; })()" % job)
            deadline = time.monotonic() + 180
            clicked = False
            while time.monotonic() < deadline:
                try:
                    if window.evaluate_js(click_script):
                        clicked = True
                        break
                except Exception:
                    pass
                time.sleep(1.5)
            evidence["pause_clicked"] = clicked
            # Wait until the worker really paused (status left the running set).
            deadline = time.monotonic() + 180
            paused = False
            while time.monotonic() < deadline:
                status = _exit_flow.read_runtime_status(Path(context["data_dir"]), job)
                if status == "paused" or (status not in _exit_flow.ACTIVE_STATUSES):
                    paused = True
                    evidence["status_after_pause"] = status
                    break
                time.sleep(2)
            evidence["paused"] = paused
            (out_dir / "paused-ok").write_text("1" if paused else "0", encoding="utf-8")

    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(2)
    evidence["marker_seen"] = marker.exists()
    if not marker.exists():
        evidence["error"] = "runner never signalled"
        return evidence

    try:
        if flow.exists():
            flow.unlink()
    except OSError:
        pass
    window.events.closing.set()
    deadline = time.monotonic() + 300
    report = None
    while time.monotonic() < deadline:
        report = _read_json(flow)
        if report and report.get("safe_to_exit") is not None:
            break
        time.sleep(1)
    evidence["report"] = report
    try:
        if report:
            (out_dir / "two-tasks-exit-flow.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return evidence
