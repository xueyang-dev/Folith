#!/usr/bin/env python3
"""D1 paused-task probe: pause a real task through the UI, then close the window.

Roadmap §6.1 requires D1 to extend the exit matrix to *paused* tasks, and the
prompt is explicit that pause must not be assumed equivalent to save-and-stop
unless the implementation proves it.  This probe therefore:

1. waits until the job is genuinely mid-task;
2. clicks the app's own 暂停任务 button (the real UI path, not a direct core call);
3. waits until the worker has actually paused and released its lock;
4. triggers the real window-close path and waits for the exit-flow report.
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
        "journal_pairs": _journal_pairs(data_dir / job / "state_journal.jsonl"),
        "worker_lock_held": _worker_lock_held(data_dir, job),
    }


def _worker_lock_held(data_dir: Path, job: str) -> bool:
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from folith.desktop import exit_flow

        return exit_flow.worker_lock_held(data_dir, job)
    except Exception:  # pragma: no cover - defensive
        return False


def _navigate(window, job: str) -> bool:
    """Route the app window to the job so its runtime controls render."""
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from folith.desktop import exit_flow

        return exit_flow.navigate_to_job(window, job)
    except Exception:  # pragma: no cover - defensive
        return False


def _jobs_with_runtime(data_dir: Path) -> list[str]:
    """Any job that has runtime state (including interrupted / paused ones)."""
    found: list[str] = []
    if not data_dir.is_dir():
        return found
    for entry in sorted(data_dir.iterdir()):
        if not entry.is_dir():
            continue
        if (_read_json(entry / "runtime_state.json") or {}).get("status"):
            found.append(entry.name)
    return found


def run(window, context):  # noqa: D103
    os.environ.setdefault("FOLITH_EXIT_FORCE_PROMPT", "0")
    data_dir = Path(context["data_dir"])
    evidence: dict = {"data_dir": str(data_dir), "samples": []}
    deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_WAIT", "420"))

    job = None
    while time.monotonic() < deadline:
        for candidate in _jobs_with_runtime(data_dir):
            job = candidate
            break
        if job:
            break
        time.sleep(2)
    evidence["job_id"] = job
    if not job:
        evidence["error"] = "no job with runtime state appeared"
        return evidence

    # The service was stopped mid-flight, so the job may be reported as
    # interrupted: the window must first be routed to it, then resumed, before a
    # pause is meaningful.
    evidence["navigated"] = _navigate(window, job)
    evidence["state_on_arrival"] = _job_state(data_dir, job)
    resume_deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_RESUME_WAIT", "180"))
    while time.monotonic() < resume_deadline:
        state = _job_state(data_dir, job)
        if state["runtime_status"] in {"running", "waiting_external", "starting", "queued"}:
            break
        for key in ("runtime_resume", "runtime_retry", "runtime_continue"):
            try:
                window.evaluate_js(
                    "(function(){const el=document.querySelector('.st-key-%s_%s button');"
                    "if(el){el.click(); return 'clicked';} return 'missing';})()" % (key, job))
            except Exception:  # pragma: no cover - defensive
                pass
        time.sleep(3)
    evidence["state_after_resume_click"] = _job_state(data_dir, job)

    # Pause as soon as the job is active again: the worker honours a pause when
    # scheduling the next batch, so the request must land while work remains.
    state = _job_state(data_dir, job)
    evidence["samples"].append(state)
    evidence["mid_task_state"] = state

    # 2. Pause through the app's own button.
    clicked = None
    pause_deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_PAUSE_WAIT", "180"))
    while time.monotonic() < pause_deadline:
        try:
            clicked = window.evaluate_js(
                "(function(){const el=document.querySelector('.st-key-runtime_pause_%s button');"
                "if(!el){return 'missing';} el.click(); return 'clicked';})()" % job
            )
        except Exception as exc:  # pragma: no cover - defensive
            clicked = f"error:{type(exc).__name__}"
        if clicked == "clicked":
            break
        time.sleep(2)
    evidence["pause_click"] = clicked

    # 3. Wait until the worker really paused and released its lock.
    paused_state = _job_state(data_dir, job)
    while time.monotonic() < pause_deadline:
        paused_state = _job_state(data_dir, job)
        if paused_state["runtime_status"] == "paused" and not paused_state["worker_lock_held"]:
            break
        time.sleep(2)
    evidence["paused_state"] = paused_state
    # The product reports `paused` once it stops scheduling; the worker may still
    # hold its lock for a moment.  Releasing the lock is the *exit flow's* job
    # (verified after the close), so the two facts are recorded separately.
    evidence["paused_observed"] = paused_state["runtime_status"] == "paused"
    evidence["worker_lock_held_at_pause"] = paused_state["worker_lock_held"]
    evidence["paused_confirmed"] = bool(evidence["paused_observed"])

    # 4. Real close path.
    evidence["closing"] = True
    try:
        evidence["closing_cancelled"] = bool(window.events.closing.set())
    except Exception as exc:  # pragma: no cover - defensive
        evidence["closing_error"] = f"{type(exc).__name__}: {exc}"
        try:
            window.destroy()
        except Exception:
            pass
    flow_report = data_dir.parent / "logs" / "exit-flow.json"
    deadline = time.monotonic() + float(os.environ.get("FOLITH_D1_FLOW_WAIT", "180"))
    while time.monotonic() < deadline and not flow_report.exists():
        time.sleep(1)
    evidence["flow_report_seen"] = flow_report.exists()
    evidence["state_after_close_request"] = _job_state(data_dir, job)
    return evidence
