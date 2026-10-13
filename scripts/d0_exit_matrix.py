#!/usr/bin/env python3
"""D0 exit feasibility: stop the service *while a task is running*.

Roadmap §3.3/§6.1: D0 must show that closing the app during a small task really
reclaims the service and worker, and that the interrupted job is recoverable
rather than silently marked complete.  This script:

1. starts the controlled model endpoint with an artificial delay so the task is
   still running when we stop;
2. starts the (optionally frozen) Folith service through the normal launcher;
3. drives the UI just far enough to start a real task;
4. stops the service mid-task and records process/port evidence;
5. restarts the service on the same data directory and records what the job
   looks like afterwards (interrupted / resumable, not "completed").
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)


def _job_state(data_dir: Path, job_id: str) -> dict:
    path = data_dir / job_id / "state.json"
    if not path.exists():
        return {"exists": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    pairs = state.get("pairs") or []
    translated = [
        index for index, pair in enumerate(pairs)
        if isinstance(pair, dict) and str(pair.get("target") or "").strip()
    ]
    return {
        "exists": True,
        "pair_count": len(pairs),
        "translated_count": len(translated),
        "delivery_status": state.get("delivery_status"),
        "job_status": state.get("status"),
        "interrupted": state.get("interrupted"),
        "runtime_state": _runtime_state(data_dir, job_id),
        "journal": _journal_summary(data_dir, job_id),
    }


def _runtime_state(data_dir: Path, job_id: str) -> dict | None:
    path = data_dir / job_id / "runtime_state.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"unreadable": True}


def _journal_summary(data_dir: Path, job_id: str) -> dict:
    path = data_dir / job_id / "state_journal.jsonl"
    if not path.exists():
        return {"exists": False}
    entries = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    translated = 0
    for line in entries:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        for pair in record.get("pairs") or []:
            if isinstance(pair, dict) and str(pair.get("target") or "").strip():
                translated += 1
    return {"exists": True, "entries": len(entries), "translated_pairs": translated}


def _is_mid_translation(state: dict | None) -> bool:
    journal = (state or {}).get("journal") or {}
    runtime = (state or {}).get("runtime_state") or {}
    return bool(
        (journal.get("translated_pairs") or 0) > 0
        and str(runtime.get("stage") or "") == "translation"
        and str(runtime.get("status") or "") in {"running", "waiting_external"}
        and str(runtime.get("operation") or "") in {
            "translation", "translation_generation", "context_build",
            "state_commit_done", "state_commit", "translation_qa",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D0 running-task exit check")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", default=None)
    parser.add_argument("--delay", type=float, default=6.0)
    parser.add_argument("--ready-timeout", type=float, default=240.0)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {"delay_s": args.delay, "frozen": bool(args.frozen_app)}

    node = resolve_node()
    playwright = resolve_playwright()
    chrome = resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["docx"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["docx"]["path"])

    if args.frozen_app:
        os.environ["FOLITH_DESKTOP_EXECUTABLE"] = str(Path(args.frozen_app).expanduser())
    os.environ[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    os.environ[desktop_paths.LOG_DIR_ENV] = str(log_dir)

    stub, stub_url, stub_log = start_stub_llm(out_dir, delay=args.delay)
    evidence["stub_llm_url"] = stub_url
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=args.ready_timeout,
    )
    try:
        launch = service.start()
        evidence["service"] = launch.evidence.as_dict()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url,
             "--source", str(source),
             "--out-dir", str(out_dir),
             "--stub-url", stub_url,
             "--result", str(out_dir / "start.json"),
             "--stop-after-start"],
            capture_output=True, text=True, env=env, timeout=1800,
        )
        start_payload = json.loads((out_dir / "start.json").read_text(encoding="utf-8"))
        job_id = start_payload.get("job_id")
        evidence["job_id"] = job_id
        evidence["start_exit_code"] = run.returncode
        if not job_id:
            evidence["error"] = "job never started"
            evidence["slice_stdout_tail"] = (run.stdout or "")[-600:]
            evidence["slice_stderr_tail"] = (run.stderr or "")[-400:]
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:2000])
            return 1

        # Let the worker get into the middle of the task.
        # Wait until the journal shows partial translated batches while the
        # runtime still reports an in-progress translation operation.
        deadline = time.monotonic() + 90
        running_state = None
        while time.monotonic() < deadline:
            running_state = _job_state(data_dir, job_id)
            if _is_mid_translation(running_state):
                break
            time.sleep(1.0)
        evidence["state_while_running"] = running_state
        evidence["stopped_mid_task"] = _is_mid_translation(running_state)

        stop = service.stop()
        evidence["shutdown_mid_task"] = {
            "acknowledged": stop.acknowledged,
            "child_gone": stop.launcher_child_gone,
            "service_gone": stop.service_pid_gone,
            "port_released": stop.port_released,
            "residual_pids": stop.residual_pids,
            "forced": stop.forced,
        }
        evidence["service_pid_alive_after_stop"] = desktop_process.pid_alive(
            launch.evidence.service_pid
        )
        evidence["state_after_stop"] = _job_state(data_dir, job_id)
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            stub.kill()

    # Reopen on the same data directory: the job must still be there and must not
    # pretend to be finished.
    reopen = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=args.ready_timeout,
    )
    try:
        launch2 = reopen.start()
        job_id = evidence.get("job_id")
        evidence["reopen"] = {
            "service_pid": launch2.evidence.service_pid,
            "port": launch2.evidence.port,
            "job": _job_state(data_dir, job_id) if job_id else None,
        }
    finally:
        stop2 = reopen.stop()
        evidence["reopen_shutdown"] = {
            "service_gone": stop2.service_pid_gone,
            "port_released": stop2.port_released,
            "residual_pids": stop2.residual_pids,
        }

    evidence["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    evidence["ok"] = bool(
        evidence.get("stopped_mid_task")
        and evidence.get("shutdown_mid_task", {}).get("service_gone")
        and not evidence.get("shutdown_mid_task", {}).get("residual_pids")
        and not evidence.get("service_pid_alive_after_stop")
        and evidence.get("reopen", {}).get("job", {}).get("exists")
        # The interrupted job must not be presented as delivered/complete.
        and (evidence.get("reopen", {}).get("job", {}).get("delivery_status") or "draft")
        not in {"final", "delivered"}
        and not evidence.get("reopen", {}).get("job", {}).get("runtime_state", {}).get("completed")
    )
    (out_dir / "exit-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2)[:4000])
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
