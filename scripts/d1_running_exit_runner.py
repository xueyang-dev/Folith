#!/usr/bin/env python3
"""D1 running-task exit verification: cancel + bounded wait + reclaim.

Sequence:
1. start the frozen app in desktop mode (real window) with the running-task
   probe; the app publishes its service URL in `instance.json`;
2. drive that same service with a browser to create and start a real task whose
   model calls are slowed down by the controlled endpoint;
3. the probe waits until the task is mid-flight and then triggers the real close
   path; the launcher's exit flow must cancel the task, wait for the worker to
   release its lock, and only then exit;
4. verify: cancel took effect, worker lock released, no residual processes, the
   job is not marked complete, and partial results are preserved.

Usage:
    venv/bin/python scripts/d1_running_exit_runner.py --out /tmp/d1-running \
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
from folith.desktop import paths as desktop_paths  # noqa: E402

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


def _job_summary(data_dir: Path, job: str) -> dict:
    state = _read_json(data_dir / job / "state.json") or {}
    runtime = _read_json(data_dir / job / "runtime_state.json") or {}
    pairs = state.get("pairs") or []
    translated = [i for i, pair in enumerate(pairs)
                  if isinstance(pair, dict) and str(pair.get("target") or "").strip()]
    journal_path = data_dir / job / "state_journal.jsonl"
    journal_pairs = 0
    if journal_path.exists():
        for line in journal_path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            for pair in record.get("pairs") or []:
                if isinstance(pair, dict) and str(pair.get("target") or "").strip():
                    journal_pairs += 1
    return {
        "pair_count": len(pairs),
        "translated_count": len(translated),
        "delivery_status": state.get("delivery_status"),
        "runtime_status": runtime.get("status"),
        "runtime_stage": runtime.get("stage"),
        "journal_pairs": journal_pairs,
        "worker_lock_held": exit_flow.worker_lock_held(data_dir, job),
    }


def _service_count() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 running-task exit verification")
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

    env = os.environ.copy()
    env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    env["FOLITH_D1_WAIT"] = "420"
    probe_result = out_dir / "running-probe.json"

    app = subprocess.Popen(
        [str(Path(args.frozen_app).expanduser()),
         "--automation-script", str(ROOT / "scripts" / "desktop_d1_running_probe.py"),
         "--automation-result", str(probe_result)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        # Wait for the app's published instance record -> its service URL.
        record = None
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline and record is None:
            record = _read_json(data_dir.parent / "instance.json")
            time.sleep(1)
        if not record:
            evidence["error"] = "app never published an instance record"
            return 1
        evidence["instance"] = {"port": record.get("port"),
                                "service_pid": record.get("service_pid")}

        # Create and start a real task inside the app's own service.
        browser_env = os.environ.copy()
        browser_env["FOLIO_PLAYWRIGHT_CORE"] = playwright
        browser_env["FOLIO_CHROME"] = chrome
        run = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
             "--url", f"http://127.0.0.1:{record['port']}",
             "--source", str(source),
             "--out-dir", str(out_dir / "setup"),
             "--stub-url", stub_url,
             "--result", str(out_dir / "setup.json"),
             "--stop-after-start"],
            capture_output=True, text=True, env=browser_env, timeout=2400,
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

        # The probe closes the window while the task runs; wait for the app to exit.
        try:
            stdout, _ = app.communicate(timeout=900)
        except subprocess.TimeoutExpired:
            app.kill()
            stdout, _ = app.communicate()
            evidence["app_timed_out"] = True
        evidence["app_exit_code"] = app.returncode
        evidence["app_stdout_tail"] = (stdout or "")[-2000:]
    finally:
        if app.poll() is None:
            app.kill()
            app.wait(timeout=10)
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()

    evidence["probe"] = _read_json(probe_result) or {}
    evidence["after"] = _job_summary(data_dir, job) if job else None
    evidence["services_after_exit"] = _service_count()
    log_lines = []
    launcher_log = log_dir / "launcher.log"
    if launcher_log.exists():
        log_lines = [line for line in launcher_log.read_text(encoding="utf-8").splitlines()
                     if "exit flow" in line]
    if log_lines:
        evidence["exit_flow_log"] = log_lines[-1]
        import re

        match = re.search(r"(\{.*\})", log_lines[-1])
        if match:
            try:
                evidence["exit_flow_report"] = json.loads(match.group(1))
            except ValueError:
                pass

    after = evidence.get("after") or {}
    report = evidence.get("exit_flow_report") or {}
    cancel = report.get("cancel") or {}
    evidence["ok"] = bool(
        evidence.get("app_exit_code") == 0
        and evidence["services_after_exit"] == 0
        and cancel.get("cancel_clicked") is True
        and cancel.get("left_active_status") is True
        and cancel.get("worker_lock_released") is True
        and after.get("worker_lock_held") is False
        and (after.get("runtime_status") or "") not in exit_flow.ACTIVE_STATUSES
        and (after.get("delivery_status") or "draft") not in {"final", "delivered"}
        # Partial work must survive the cancel: the product either applied the
        # journal into state.json or left journal entries to replay.
        and ((after.get("translated_count") or 0) > 0
             or (after.get("journal_pairs") or 0) > 0)
    )
    (out_dir / "d1-running-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "job_id": job,
        "mid_task": (evidence.get("probe") or {}).get("mid_task_state"),
        "cancel": cancel,
        "after": after,
        "services_after_exit": evidence["services_after_exit"],
        "evidence": str(out_dir / "d1-running-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
