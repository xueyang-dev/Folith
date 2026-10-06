#!/usr/bin/env python3
"""D1 timeout-branch probe: a close attempt that must NOT claim success.

Roadmap §3.3: once the bounded wait is exceeded the product must offer
"keep waiting" or "force quit", and a forced quit only promises that already
persisted results are recoverable.  This probe holds the job's worker lock (the
same OS lock a stuck worker would hold), triggers a real close, and records what
the flow reports — then releases the lock and triggers a second close, which must
succeed.

It runs inside the frozen app (automation hook), so the lock it holds is a real
`folith.job_lock` handle.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _job_dirs(data_dir: Path) -> list[str]:
    if not data_dir.is_dir():
        return []
    return [entry.name for entry in sorted(data_dir.iterdir())
            if entry.is_dir() and not entry.name.startswith(".")
            and (entry / "state.json").is_file()]


def run(window, context):  # noqa: D103
    os.environ.setdefault("FOLITH_EXIT_FORCE_PROMPT", "0")
    data_dir = Path(context["data_dir"])
    log_dir = Path(context["log_dir"])
    flow = log_dir / "exit-flow.json"
    evidence: dict = {"data_dir": str(data_dir)}

    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from folith import job_lock
    from folith.desktop import exit_flow

    job = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and job is None:
        jobs = _job_dirs(data_dir)
        job = jobs[0] if jobs else None
        if job is None:
            time.sleep(2)
    evidence["job_id"] = job
    if job is None:
        evidence["error"] = "no job found"
        return evidence

    lock_path = data_dir / job / exit_flow.WORKER_LOCK_FILE
    holder = job_lock.acquire(lock_path, blocking=False)
    evidence["lock_acquired"] = holder is not None
    evidence["lock_seen_by_flow"] = exit_flow.worker_lock_held(data_dir, job)

    # --- close attempt 1: must be reported as unsafe ------------------------
    try:
        if flow.exists():
            flow.unlink()
    except OSError:
        pass
    window.events.closing.set()
    deadline = time.monotonic() + 180
    first = None
    while time.monotonic() < deadline:
        first = _read_json(flow)
        if first:
            break
        time.sleep(1)
    evidence["report_unsafe"] = first
    try:
        if first:
            (log_dir / "exit-flow-attempt1.json").write_text(
                json.dumps(first, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    evidence["window_still_open_after_unsafe"] = True  # asserted by the runner

    # --- release the lock and close again: must succeed ---------------------
    if holder is not None:
        try:
            holder.close()
        except Exception:  # pragma: no cover - defensive
            pass
    evidence["lock_released"] = not exit_flow.worker_lock_held(data_dir, job)
    # The fixture marked the job "running"; a real worker would have stopped by
    # now, so record that transition too (otherwise the flow can never be safe).
    runtime_path = data_dir / job / "runtime_state.json"
    runtime = _read_json(runtime_path) or {}
    runtime["status"] = "cancelled"
    runtime_path.write_text(json.dumps(runtime, ensure_ascii=False), encoding="utf-8")
    try:
        if flow.exists():
            flow.unlink()
    except OSError:
        pass
    window.events.closing.set()
    deadline = time.monotonic() + 180
    second = None
    while time.monotonic() < deadline:
        second = _read_json(flow)
        if second and second.get("safe_to_exit"):
            break
        time.sleep(1)
    evidence["report_safe"] = second
    try:
        if second:
            (log_dir / "exit-flow-attempt2.json").write_text(
                json.dumps(second, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return evidence
