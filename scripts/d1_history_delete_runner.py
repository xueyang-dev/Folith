#!/usr/bin/env python3
"""D1: removing a task must be confirmed and must not touch anything else.

Roadmap §3.2 (data safety) and the prompt's "never overwrite/delete user work
without an explicit, confirmed action": the history view's 移除 control is the
product's only deletion entry.  This verifies, in the frozen app, that a wrong
name deletes nothing, closing deletes nothing, the exact name does delete, and a
neighbouring task (with its delivery snapshot) is untouched.

Usage:
    venv/bin/python scripts/d1_history_delete_runner.py --out /tmp/d1-del \
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

from folith.desktop import launcher as desktop_launcher  # noqa: E402
from folith.desktop import paths as desktop_paths  # noqa: E402

import d0_fixtures  # noqa: E402
from d0_slice_runner import (  # noqa: E402
    resolve_chrome, resolve_node, resolve_playwright, start_stub_llm,
)

EDIT_TEXT = ("近期关于生态恢复与演替的研究，已从描述静态状态转向对状态之间转换的建模；"
             "本段由移除验证标记写入。")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _services() -> int:
    result = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, check=False)
    return sum(1 for line in result.stdout.splitlines()
               if "/MacOS/Folith --serve" in line and "grep" not in line)


def _snapshot_files(data_dir: Path, job: str) -> int:
    return sum(1 for _ in (data_dir / job / "delivery_snapshots").rglob("*") if _.is_file()) \
        if (data_dir / job / "delivery_snapshots").is_dir() else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="history removal must be confirmed")
    parser.add_argument("--out", required=True)
    parser.add_argument("--frozen-app", required=True)
    parser.add_argument("--scenario", default="confirmed",
                        choices=("confirmed", "running", "paused"),
                        help="'confirmed' checks the typed confirmation; 'running'/"
                             "'paused' check that an active task cannot be removed")
    args = parser.parse_args(argv)

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    data_dir = out_dir / "data"
    log_dir = out_dir / "logs"
    app = str(Path(args.frozen_app).expanduser())
    evidence: dict = {}

    node, playwright, chrome = resolve_node(), resolve_playwright(), resolve_chrome()
    if not (node and playwright and chrome):
        print(json.dumps({"error": "browser automation unavailable"}, indent=2))
        return 2

    active_scenario = args.scenario in ("running", "paused")
    running_scenario = active_scenario
    manifest = d0_fixtures.build(["pdf_text", "docx"], out_dir / "fixtures")
    sources = [Path(manifest["inputs"]["pdf_text"]["path"]),
               Path(manifest["inputs"]["docx"]["path"])]
    if running_scenario:
        sources = sources[:1]
        jobs = []  # the task is started later, against the live app
    # A slow stub keeps the task genuinely running during the attempt.
    stub, stub_url, _log = start_stub_llm(out_dir, delay=20.0 if running_scenario else 0.2)

    env = os.environ.copy()
    env["FOLIO_PLAYWRIGHT_CORE"] = playwright
    env["FOLIO_CHROME"] = chrome

    # --- 1. two real tasks, each with a delivery snapshot --------------------
    # (the 'running' scenario starts its single task later, against the live app)
    jobs: list[str] = []
    service = desktop_launcher.DesktopService(
        paths=desktop_paths.DesktopPaths(data_dir=data_dir, log_dir=log_dir),
        ready_timeout=240.0,
    )
    launch = None
    try:
        launch = service.start() if not running_scenario else None
        for index, source in enumerate([] if running_scenario else sources, start=1):
            extra = ["--stop-after-start"] if running_scenario else ["--review"]
            run = subprocess.run(
                [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                 "--url", launch.url, "--source", str(source),
                 "--out-dir", str(out_dir / f"setup{index}"), "--stub-url", stub_url,
                 "--result", str(out_dir / f"setup{index}.json"), "--edit-text", EDIT_TEXT]
                + extra,
                capture_output=True, text=True, env=env, timeout=3600, check=False)
            setup = _read_json(out_dir / f"setup{index}.json") or {}
            job = setup.get("job_id")
            evidence[f"task{index}"] = {"exit_code": run.returncode, "job_id": job}
            if job:
                jobs.append(job)
    finally:
        if launch is not None:
            service.stop()
        if not running_scenario:
            stub.terminate()
            try:
                stub.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                stub.kill()

    if running_scenario:
        # The task must be running in the *same* service the probe talks to, so
        # the app is launched first and the task is started against it.  (A task
        # whose worker died is reported as interrupted, which is legitimately
        # removable, so stopping the setup service first would test nothing.)
        app_env = os.environ.copy()
        app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
        app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
        proc = subprocess.Popen([app, "--no-browser"], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=app_env)
        try:
            record = data_dir.parent / "instance.json"
            deadline = time.monotonic() + 240
            url = None
            while time.monotonic() < deadline and url is None:
                url = (_read_json(record) or {}).get("url")
                if url is None:
                    time.sleep(2)
            evidence["url"] = url
            if not url:
                evidence["error"] = "no instance record"
                return 1
            run = subprocess.run(
                [node, str(ROOT / "scripts" / "desktop_d0_slice.js"),
                 "--url", url, "--source", str(sources[0]),
                 "--out-dir", str(out_dir / "running-setup"), "--stub-url", stub_url,
                 "--result", str(out_dir / "running-setup.json"),
                 "--stop-after-start", "--edit-text", EDIT_TEXT],
                capture_output=True, text=True, env=env, timeout=3600, check=False)
            setup = _read_json(out_dir / "running-setup.json") or {}
            job = setup.get("job_id")
            evidence["running_setup"] = {"exit_code": run.returncode, "job_id": job}
            if not job:
                evidence["error"] = "task did not start"
                return 1
            evidence["target"] = job
            probe_result = out_dir / "delete-running.json"
            # For the paused scenario the probe pauses first and waits, so the
            # on-disk status can be recorded *while* the task is still paused.
            if args.scenario == "paused":
                probe_proc = subprocess.Popen(
                    [node, str(ROOT / "scripts" / "desktop_d1_delete_paused.js"),
                     "--url", url, "--job", job, "--data-dir", str(data_dir),
                     "--out-dir", str(out_dir), "--result", str(probe_result)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
                ready = out_dir / "paused-ready"
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline and not ready.exists():
                    time.sleep(2)
                evidence["status_at_attempt"] = _read_json(
                    data_dir / job / "runtime_state.json") or {}
                evidence["paused_ready"] = ready.exists()
                (out_dir / "go-delete").write_text("go", encoding="utf-8")
                try:
                    probe_proc.wait(timeout=900)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    probe_proc.kill()
                evidence["probe_exit_code"] = probe_proc.returncode
            else:
                evidence["status_at_attempt"] = _read_json(
                    data_dir / job / "runtime_state.json") or {}
                probe = subprocess.run(
                    [node, str(ROOT / "scripts" / "desktop_d1_delete_running.js"),
                     "--url", url, "--job", job, "--data-dir", str(data_dir),
                     "--out-dir", str(out_dir), "--result", str(probe_result)],
                    capture_output=True, text=True, env=env, timeout=1800, check=False)
                evidence["probe_exit_code"] = probe.returncode
            evidence["probe"] = _read_json(probe_result) or {}
            evidence["target_dir_while_running"] = (data_dir / job).is_dir()
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _services() > 0:
            time.sleep(1)
        stub.terminate()
        try:
            stub.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            stub.kill()
        evidence["services_after_exit"] = _services()
        evidence["target_dir_after"] = (data_dir / job).is_dir()
        status = str((evidence["status_at_attempt"] or {}).get("status") or "")
        expected_status = ({"paused"} if args.scenario == "paused"
                           else {"running", "waiting_external", "queued", "starting",
                                 "resume_requested", "cancelling"})
        evidence["ok"] = bool(
            (evidence["probe"] or {}).get("ok") is True
            and status in expected_status
            and evidence["target_dir_while_running"] is True
            and evidence["target_dir_after"] is True
            and evidence["services_after_exit"] == 0)
        (out_dir / "d1-history-delete-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": evidence["ok"], "scenario": args.scenario, "job": job,
            "status_at_attempt": status,
            "refusal_text": (evidence["probe"] or {}).get("refusal_text"),
            "steps": (evidence["probe"] or {}).get("steps"),
            "target_dir_after": evidence["target_dir_after"],
            "services_after_exit": evidence["services_after_exit"],
            "evidence": str(out_dir / "d1-history-delete-evidence.json"),
        }, ensure_ascii=False, indent=2))
        return 0 if evidence["ok"] else 1

    if len(jobs) != 2:
        (out_dir / "d1-history-delete-evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, ensure_ascii=False, indent=2)[:1200])
        return 1
    target, neighbour = jobs[0], jobs[1]
    evidence["target"] = target
    evidence["neighbour"] = neighbour
    evidence["neighbour_snapshot_files_before"] = _snapshot_files(data_dir, neighbour)

    # --- 2. drive the removal modal in the frozen app -----------------------
    app_env = os.environ.copy()
    app_env[desktop_paths.DATA_DIR_ENV] = str(data_dir)
    app_env[desktop_paths.LOG_DIR_ENV] = str(log_dir)
    probe_result = out_dir / "history-delete.json"
    proc = subprocess.Popen([app, "--no-browser"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=app_env)
    try:
        record = data_dir.parent / "instance.json"
        deadline = time.monotonic() + 240
        url = None
        while time.monotonic() < deadline and url is None:
            url = (_read_json(record) or {}).get("url")
            if url is None:
                time.sleep(2)
        evidence["url"] = url
        if not url:
            evidence["error"] = "no instance record"
            return 1
        probe = subprocess.run(
            [node, str(ROOT / "scripts" / "desktop_d1_history_delete.js"),
             "--url", url, "--job", target, "--data-dir", str(data_dir),
             "--out-dir", str(out_dir), "--result", str(probe_result)],
            capture_output=True, text=True, env=env, timeout=1800, check=False)
        evidence["probe_exit_code"] = probe.returncode
        evidence["probe"] = _read_json(probe_result) or {}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and _services() > 0:
        time.sleep(1)
    evidence["services_after_exit"] = _services()
    evidence["target_dir_after"] = (data_dir / target).is_dir()
    evidence["neighbour_dir_after"] = (data_dir / neighbour).is_dir()
    evidence["neighbour_snapshot_files_after"] = _snapshot_files(data_dir, neighbour)

    probe_data = evidence["probe"]
    steps = probe_data.get("steps") or {}
    evidence["ok"] = bool(
        probe_data.get("ok") is True
        and evidence["target_dir_after"] is False
        and evidence["neighbour_dir_after"] is True
        and evidence["neighbour_snapshot_files_after"]
        == evidence["neighbour_snapshot_files_before"]
        and evidence["neighbour_snapshot_files_before"] > 0
        and evidence["services_after_exit"] == 0
    )
    (out_dir / "d1-history-delete-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": evidence["ok"],
        "target": target, "neighbour": neighbour,
        "steps": steps,
        "expected_name": probe_data.get("expected_name"),
        "target_dir_after": evidence["target_dir_after"],
        "neighbour_dir_after": evidence["neighbour_dir_after"],
        "neighbour_snapshot_files": [evidence["neighbour_snapshot_files_before"],
                                     evidence["neighbour_snapshot_files_after"]],
        "services_after_exit": evidence["services_after_exit"],
        "evidence": str(out_dir / "d1-history-delete-evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
