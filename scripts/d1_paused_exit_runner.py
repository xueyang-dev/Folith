#!/usr/bin/env python3
"""D1 paused-task exit verification (roadmap §6.1 "D1 再扩展暂停…矩阵").

Flow:
1. drive a real task through the app's own service until it is mid-flight;
2. start the app with the paused probe, which clicks the app's 暂停任务 button,
   waits until the worker is really paused and its lock is released, then triggers
   the real window-close path;
3. verify the exit flow, that nothing was lost, that no process survived;
4. reopen the same data directory and confirm the paused work is resumable and
   actually resumes to completion.

Usage:
    venv/bin/python scripts/d1_paused_exit_runner.py --out /tmp/d1-paused \
        --frozen-app dist/desktop/Folith.app/Contents/MacOS/Folith
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

from folith.desktop import exit_flow  # noqa: E402
from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402
from folith.desktop import process as desktop_process  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

STUB_DELAY = 8.0


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
        "worker_lock_held": exit_flow.worker_lock_held(data_dir, job),
    }


def _services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 paused-task exit verification")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    evidence: dict = {"stub_delay_s": STUB_DELAY}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    manifest = d0_fixtures.build(["pdf_text"], out_dir / "fixtures")
    source = Path(manifest["inputs"]["pdf_text"]["path"])
    stub, stub_url, _log = start_stub_llm(out_dir, delay=STUB_DELAY)
    try:
        return _run_phases(args, out_dir, data_dir, log_dir, source, stub, stub_url,
                           node, playwright, chrome, evidence)
    finally:
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()


def _run_phases(args, out_dir, data_dir, log_dir, source, stub, stub_url,
                node, playwright, chrome, evidence) -> int:

    # ---- 1. create a mid-flight task ---------------------------------------
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    job = None
    try:
        launch = service.start()
        env = os.environ.copy()
        env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", launch.url, "--source", str(source),
             "--out-dir", str(out_dir / "setup"), "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"), "--stop-after-start"],
            capture_output=True, text=True, env=env, timeout=2400,
        )
        setup = _read_json(out_dir / "setup.json") or {}
        job = setup.get("job_id")
        evidence["setup"] = {"exit_code": run.returncode, "job_id": job,
                             "stderr_tail": (run.stderr or "")[-400:]}
        if not job:
            evidence["error"] = "task never started"
            evidence["slice_stdout_tail"] = (run.stdout or "")[-600:]
            return 1
        # Stopping the service during document processing leaves the job failed;
        # wait until translation is genuinely under way so the reopen has a
        # resumable, mid-flight job (which is what a paused task needs).
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            state = _job_state(data_dir, job)
            if state["runtime_stage"] == "translation" and state["journal_pairs"] > 0:
                break
            time.sleep(1.5)
        evidence["before_service_stop"] = _job_state(data_dir, job)
    finally:
        service.stop()

    # ---- 2. pause through the UI, then close the window --------------------
    app_env = os.environ.copy()
    app_env["FOLITH_DESKTOP_DATA_DIR"] = str(data_dir)
    app_env["FOLITH_DESKTOP_LOG_DIR"] = str(log_dir)
    app_env["FOLITH_D1_PAUSE_WAIT"] = "180"
    app_env["FOLITH_D1_FLOW_WAIT"] = "180"
    app_env["FOLITH_EXIT_FORCE_PROMPT"] = "0"
    probe_result = out_dir / "paused-probe.json"
    app = subprocess.run(
        [str(Path(args.frozen_app).expanduser()), "--automation-script",
         str(ROOT / "scripts" / "desktop_d1_paused_probe.py"),
         "--automation-result", str(probe_result)],
        capture_output=True, text=True, env=app_env, timeout=2400, check=False,
    )
    evidence["app"] = {"exit_code": app.returncode, "tail": (app.stdout or "")[-300:]}
    evidence["probe"] = _read_json(probe_result) or {}
    evidence["flow_report"] = _read_json(log_dir / "exit-flow.json") or {}
    evidence["after_exit"] = _job_state(data_dir, job)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _services() > 0:
        time.sleep(1)
    evidence["services_after_exit"] = _services()

    # ---- 3. reopen and resume the paused work ------------------------------
    # Keep the SAME endpoint alive: the app's provider config stores this URL, so
    # starting a second server on another port would break the resume phase.
    reopen = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    try:
        launch = reopen.start()
        env2 = os.environ.copy()
        env2["FOLIO_PLAYWRIGHT_CORE"] = playwright
        env2["FOLIO_CHROME"] = chrome
        resume = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d1_resume.js"),
             "--url", launch.url, "--job", job,
             "--out-dir", str(out_dir / "resume"), "--result", str(out_dir / "resume.json")],
            capture_output=True, text=True, env=env2, timeout=1800,
        )
        evidence["resume_run"] = {"exit_code": resume.returncode}
        evidence["resume"] = _read_json(out_dir / "resume.json") or {}
        evidence["after_resume"] = _job_state(data_dir, job)
    finally:
        reopen.stop()

    probe = evidence.get("probe") or {}
    resume = evidence.get("resume") or {}
    after_exit = evidence["after_exit"]
    after_resume = evidence.get("after_resume") or {}
    flow_steps = [step.get("step") if isinstance(step, dict) else str(step)
                  for step in (evidence["flow_report"].get("steps") or [])]
    evidence["flow_steps"] = flow_steps
    paused_state = probe.get("paused_state") or {}
    evidence["ok"] = bool(
        # the app's own pause control was used and the product reported paused
        probe.get("pause_click") == "clicked"
        and probe.get("paused_observed") is True
        # the real close path ran its flow, including stopping the task
        and probe.get("flow_report_seen") is True
        and "cancel-task" in flow_steps
        # the exit flow released the worker lock and left no processes behind
        and after_exit["worker_lock_held"] is False
        and evidence["services_after_exit"] == 0
        # nothing was lost or falsely completed
        and after_exit["delivery_status"] != "final"
        and after_exit["translated_count"] >= (paused_state.get("translated_count") or 0)
        # reopening offers a resume control and the work continues to completion
        and resume.get("resumed") is True
        and (after_resume.get("translated_count") or 0) > (after_exit.get("translated_count") or 0)
        and _services() == 0
    )
    (out_dir / "d1-paused-exit-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "pause_click": probe.get("pause_click"),
        "paused_observed": probe.get("paused_observed"),
        "worker_lock_held_at_pause": probe.get("worker_lock_held_at_pause"),
        "paused_state": probe.get("paused_state"),
        "flow_steps": flow_steps,
        "after_exit": after_exit,
        "services_after_exit": evidence["services_after_exit"],
        "resume_control": resume.get("control_used"),
        "resumed": resume.get("resumed"),
        "progress": [resume.get("progress_before"), resume.get("progress_after")],
        "after_resume": after_resume,
        "evidence": str(out_dir / "d1-paused-exit-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
