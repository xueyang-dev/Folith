#!/usr/bin/env python3
"""D1 running-task probe: close the window while a real task is mid-flight.

The job is created *after* the app starts (the harness drives the app's own
service with a browser), so this probe discovers the active job itself, waits
until it is genuinely mid-task (journal has partial results, runtime stage is
translation), and then triggers the real close path so the launcher's exit flow
must cancel the task, wait for the worker to release its lock, and only then
exit.
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


def _journal_pairs(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    total = 0
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        for pair in record.get("pairs") or []:
            if isinstance(pair, dict) and str(pair.get("target") or "").strip():
                total += 1
    return total


def _job_state(data_dir: Path, job: str) -> dict:
    state = _read_json(data_dir / job / "state.json") or {}
    runtime = _read_json(data_dir / job / "runtime_state.json") or {}
    pairs = state.get("pairs") or []
    translated = [i for i, pair in enumerate(pairs)
                  if isinstance(pair, dict) and str(pair.get("target") or "").strip()]
    return {
        "pair_count": len(pairs),
        "translated_count": len(translated),
        "delivery_status": state.get("delivery_status"),
        "runtime_status": runtime.get("status"),
        "runtime_stage": runtime.get("stage"),
        "runtime_operation": runtime.get("operation"),
        "journal_pairs": _journal_pairs(data_dir / job / "state_journal.jsonl"),
    }


def _active_jobs(data_dir: Path) -> list[str]:
    active: list[str] = []
    if not data_dir.is_dir():
        return active
    for entry in sorted(data_dir.iterdir()):
        if not entry.is_dir():
            continue
        runtime = _read_json(entry / "runtime_state.json") or {}
        if runtime.get("status"):
            active.append(entry.name)
    return active


def run(window, context):  # noqa: D103
    # Scripted runs must never block on the native force-quit dialog.
    os.environ.setdefault("FOLITH_EXIT_FORCE_PROMPT", "0")
    data_dir = Path(context["data_dir"])
    evidence: dict = {"data_dir": str(data_dir), "samples": []}
    deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_WAIT", "420"))

    job = None
    while time.monotonic() < deadline:
        for candidate in _active_jobs(data_dir):
            state = _job_state(data_dir, candidate)
            if state["runtime_status"] in {"running", "waiting_external", "starting", "queued"}:
                job = candidate
                break
        if job:
            break
        time.sleep(2)
    evidence["job_id"] = job
    if not job:
        evidence["error"] = "no active job appeared"
        return evidence

    # Wait until it is genuinely mid-task: partial journal results exist.
    while time.monotonic() < deadline:
        state = _job_state(data_dir, job)
        evidence["samples"].append(state)
        if state["journal_pairs"] > 0 and state["runtime_stage"] == "translation":
            break
        time.sleep(1.5)

    evidence["mid_task_state"] = _job_state(data_dir, job)
    evidence["closing"] = True
    try:
        evidence["closing_cancelled"] = bool(window.events.closing.set())
    except Exception as exc:  # pragma: no cover - defensive
        evidence["closing_error"] = f"{type(exc).__name__}: {exc}"
        try:
            window.destroy()
        except Exception:
            pass
    # The app terminates when this function returns, so the probe must outlive
    # the exit flow: wait for its persisted report (written when it finishes)
    # before handing control back.
    flow_report = data_dir.parent / "logs" / "exit-flow.json"
    deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_FLOW_WAIT", "180"))
    while time.monotonic() < deadline and not flow_report.exists():
        time.sleep(1)
    evidence["flow_report_seen"] = flow_report.exists()
    evidence["state_after_close_request"] = _job_state(data_dir, job)
    return evidence
