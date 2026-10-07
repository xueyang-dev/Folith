#!/usr/bin/env python3
"""D1 crash recovery verification (roadmap §3.3 / §8).

Simulates a crash (SIGKILL of the service while a task is mid-flight, i.e. no
chance to save), then reopens the same data directory and checks what the user
sees: the job must be reported as interrupted (never as complete), a resume
control must be offered, and using it must actually continue the work.

Usage:
    venv/bin/python scripts/d1_crash_recovery_runner.py --out /tmp/d1-crash \
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

STUB_DELAY = 6.0


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _job_state(data_dir: Path, job: str) -> dict:
    state = _read_json(data_dir / job / "state.json") or {}
    runtime = _read_json(data_dir / job / "runtime_state.json") or {}
    pairs = state.get("pairs") or []
    translated = [i for i, pair in enumerate(pairs)
                  if isinstance(pair, dict) and str(pair.get("target") or "").strip()]
    journal = 0
    journal_path = data_dir / job / "state_journal.jsonl"
    if journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            for pair in record.get("pairs") or []:
                if isinstance(pair, dict) and str(pair.get("target") or "").strip():
                    journal += 1
    return {
        "pair_count": len(pairs),
        "translated_count": len(translated),
        "delivery_status": state.get("delivery_status"),
        "runtime_status": runtime.get("status"),
        "runtime_stage": runtime.get("stage"),
        "journal_pairs": journal,
        "worker_lock_held": exit_flow.worker_lock_held(data_dir, job),
    }


def _services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 crash recovery verification")
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

    # --- 1. start a real task, then crash the service mid-flight -------------
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
        evidence["setup"] = {"exit_code": run.returncode, "job_id": job}
        if not job:
            evidence["error"] = "task never started"
            evidence["slice_stdout_tail"] = (run.stdout or "")[-600:]
            evidence["slice_stderr_tail"] = (run.stderr or "")[-400:]
            print(json.dumps(evidence, ensure_ascii=False, indent=2)[:2000])
            return 1

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = _job_state(data_dir, job)
            if state["journal_pairs"] > 0 and state["runtime_stage"] == "translation":
                break
            time.sleep(1.5)
        evidence["before_crash"] = _job_state(data_dir, job)
        service_pid = launch.evidence.service_pid
        desktop_process.terminate_process(service_pid, timeout=0)  # SIGTERM first
        os.kill(service_pid, 9) if desktop_process.pid_alive(service_pid) else None
        evidence["crashed_pid"] = service_pid
    finally:
        service.stop(graceful=False)
        # NOTE: the controlled endpoint stays alive: the resumed worker needs it.

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and desktop_process.pid_alive(evidence.get("crashed_pid", 0)):
        time.sleep(0.5)
    evidence["after_crash"] = {
        "service_alive": desktop_process.pid_alive(evidence.get("crashed_pid", 0)),
        "state": _job_state(data_dir, job),
    }

    # --- 2. reopen: the user must see "interrupted", never "complete" --------
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
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    resume_payload = evidence.get("resume") or {}
    after_crash = evidence["after_crash"]["state"]
    after_resume = evidence.get("after_resume") or {}
    evidence["ok"] = bool(
        after_crash["runtime_status"] in {"interrupted", "stalled", "idle_incomplete"}
        or resume_payload.get("interrupted_text_present")
    ) and bool(
        (resume_payload.get("resume_present") or resume_payload.get("retry_present"))
        and resume_payload.get("resumed") is True
        and after_crash.get("delivery_status") not in {"final", "delivered"}
        and (after_resume.get("translated_count") or 0) > (after_crash.get("translated_count") or 0)
        and _services() == 0
    )
    (out_dir / "d1-crash-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "before_crash": evidence.get("before_crash"),
        "after_crash": after_crash,
        "interrupted_text_present": resume_payload.get("interrupted_text_present"),
        "resume_present": resume_payload.get("resume_present"),
        "resumed": resume_payload.get("resumed"),
        "progress_before": resume_payload.get("progress_before"),
        "progress_after": resume_payload.get("progress_after"),
        "after_resume": after_resume,
        "services_after": _services(),
        "evidence": str(out_dir / "d1-crash-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
